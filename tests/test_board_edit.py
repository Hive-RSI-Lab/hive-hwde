"""U17 tests: board_edit - editing an existing board's Edge.Cuts outline.

Pure tests (venv only: geom/placelib/shapely) cover the shape description,
the three outline modes, the before/after issue diff that decides a refusal,
and the cutout rules - `--report-only` needs no toolchain, so the whole
refusal surface is testable without KiCad.

Smoke tests (SWIG bundled python + kicad-cli) cover apply end-to-end on the
frozen routed pd-trigger fixture and on a copy of the bb-buck workspace:
exact new geometry, copper untouched, DRC no worse, byte-identical rollback,
the recorded outline_change edit, and idempotent re-application.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from shapely.geometry import box

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import board_edit as be  # noqa: E402
import geom  # noqa: E402
import placelib  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402
from _boards import board_path, need_board  # noqa: E402

PD_DIR = REPO / "tests" / "fixtures" / "stages" / "pd_trigger" / "route"
PD_PCB = PD_DIR / "pd-trigger.kicad_pcb"
UB = REPO / "tests" / "golden" / "usbbuck4"
UB_PCB = UB / "usbbuck4.kicad_pcb"
BB = board_path("bb-buck")


# ---------------------------------------------------------------- helpers

def _describe(pcb: Path) -> tuple[dict, "geom.BoardGeom", "placelib.PlaceModel"]:
    bg = geom.BoardGeom.from_file(pcb)
    model = placelib.PlaceModel(pcb)
    return be.describe_outline(bg.outline_faces, bg.outline_arc_radii,
                               bg.outline_items), bg, model


def _stage_pd(tmp_path: Path) -> Path:
    """The frozen routed fixture, with its project sidecars (the staged copy
    needs them or kicad-cli would refill against default rules)."""
    dst = tmp_path / "pdt"
    shutil.copytree(PD_DIR, dst)
    return dst / PD_PCB.name


def _run(pcb: Path, *args, expect: int | None = 0) -> dict:
    """board_edit through its CLI (the SPEC exit-code contract is the API)."""
    cp = subprocess.run(
        [sys.executable, str(SCRIPTS / "board_edit.py"), "--pcb", str(pcb),
         *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    payload = json.loads(cp.stdout)
    if expect is not None:
        assert cp.returncode == expect, f"exit {cp.returncode}: {cp.stdout}"
    return payload


def _issues(pcb: Path, poly, edges: dict | None = None) -> list[dict]:
    _, bg, model = _describe(pcb)
    return be.outline_issues(model, bg, poly, be.fab_floors(pcb), edges or {})


# ============================================================ shape reading

def test_plain_rect_outline_reads_as_ideal():
    cur, _, _ = _describe(PD_PCB)
    assert cur["bbox"] == [9.8, 27.51, 57.8, 57.51]
    assert (cur["w"], cur["h"]) == (48.0, 30.0)
    assert cur["corner_radius"] == 0.0
    assert cur["faces"] == 1 and cur["items"] == {"gr_rect": 1}
    assert cur["ideal"] is True


def test_corner_radius_comes_from_the_arcs_not_the_area():
    """A radius derived from the polygon's AREA carries geom's 16-chord arc
    sampling (r=3 reads back as ~3.009), so every re-edit would inflate the
    board's own corners. The arcs' declared three points are exact."""
    r = 3.0
    coarse = box(0, 0, 40, 30).buffer(-r, quad_segs=4).buffer(r, quad_segs=4)
    got = be.describe_outline([coarse], [r] * 4,
                              {"gr_line": 4, "gr_arc": 4})
    assert got["corner_radius"] == r
    assert got["ideal"] is True


def test_notched_outline_is_not_a_plain_rectangle():
    notched = box(0, 0, 40, 30).difference(box(10, 0, 16, 3))
    got = be.describe_outline([notched], [], {"gr_line": 8})
    assert got["ideal"] is False
    assert got["deviation_mm"] == pytest.approx(3.0, abs=0.01)


def test_interior_window_is_visible_in_the_item_counts(tmp_path):
    """geom's parser returns on the FIRST gr_rect on Edge.Cuts, so a second
    one (an interior window) never reaches .outline. board_edit deletes every
    Edge.Cuts item, so it has to see it - through the item inventory."""
    pcb = tmp_path / "window.kicad_pcb"
    text = PD_PCB.read_text(encoding="utf-8")
    window = ('\t(gr_rect\n\t\t(start 30 40)\n\t\t(end 36 46)\n'
              '\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type default)\n\t\t)\n'
              '\t\t(fill no)\n\t\t(layer "Edge.Cuts")\n\t)\n')
    pcb.write_text(text[:text.rindex(")")] + window + ")", encoding="utf-8")

    bg = geom.BoardGeom.from_file(pcb)
    assert bg.outline_items == {"gr_rect": 2}
    assert bg.outline.bounds[2] - bg.outline.bounds[0] == pytest.approx(48.0)
    cur = be.describe_outline(bg.outline_faces, bg.outline_arc_radii,
                              bg.outline_items)
    assert cur["ideal"] is False

    refused = _run(pcb, "--outline", "keep", "--report-only", expect=2)
    assert refused["status"] == "error"
    assert "not a plain (rounded) rectangle" in refused["error"]
    ok = _run(pcb, "--outline", "keep", "--replace-shape", "--report-only")
    assert ok["status"] == "pass" and ok["applied"] is False


# ================================================================== modes

def test_fixed_outline_anchors_topleft_or_centre():
    cur, bg, model = _describe(PD_PCB)
    tl, _ = be.target_rect("fixed", "40x30", cur, model, bg, 1.0, "topleft")
    assert tl == [9.8, 27.51, 49.8, 57.51]
    ctr, _ = be.target_rect("fixed", "40x30", cur, model, bg, 1.0, "center")
    assert ctr == [13.8, 27.51, 53.8, 57.51]
    keep, _ = be.target_rect("keep", "keep", cur, model, bg, 1.0, "topleft")
    assert keep == cur["bbox"]


def test_fit_is_the_content_bbox_plus_margin():
    cur, bg, model = _describe(PD_PCB)
    rect, extra = be.target_rect("fit", "fit", cur, model, bg, 1.0, "topleft")
    cx1, cy1, cx2, cy2 = extra["content_bbox"]
    assert rect[0] <= cx1 - 1.0 and rect[1] <= cy1 - 1.0
    assert rect[2] >= cx2 + 1.0 and rect[3] >= cy2 + 1.0
    # the margin is a floor: rounding to the 1 um grid may only ever add
    assert rect[0] >= cx1 - 1.0 - be.GRID and rect[2] <= cx2 + 1.0 + be.GRID
    assert extra["content_counts"]["footprints"] == len(model.footprints)


def test_fit_clips_content_to_the_current_outline():
    """usbbuck4's J1 hangs 0.55 mm off its declared left edge. Fit must not
    grow the board to swallow the overhang - it fits what is ON the board."""
    cur, bg, model = _describe(UB_PCB)
    _, extra = be.target_rect("fit", "fit", cur, model, bg, 0.0, "topleft")
    fp_min_x = min(f.extents_abs().bounds[0] for f in model.footprints.values())
    assert fp_min_x < cur["bbox"][0]                 # J1 really does overhang
    assert extra["content_bbox"][0] == pytest.approx(cur["bbox"][0], abs=1e-3)


def test_fit_margin_below_the_fab_floor_is_flagged(tmp_path):
    payload = _run(PD_PCB, "--outline", "fit", "--margin", "0.05",
                   "--report-only", expect=1)
    assert payload["fab_floors"]["min_copper_to_edge_mm"] == 0.3
    assert payload["status"] == "violations"
    assert any(b["kind"] == "copper_to_edge" for b in payload["blocking"])


# ================================================================ refusals

def test_shrink_that_orphans_parts_refuses_and_names_them():
    payload = _run(PD_PCB, "--outline", "30x20", "--report-only", expect=1)
    assert payload["status"] == "violations" and payload["applied"] is False
    kinds = payload["blocking_summary"]["by_kind"]
    assert kinds["footprint_outside"] > 0
    assert {"C2", "D1", "D2"} <= set(payload["blocking_summary"]["refs"])
    for b in payload["blocking"]:
        assert b["msg"] and b["pos"], "a refusal must say WHERE"


def test_a_declared_edge_part_may_overhang_its_own_edge():
    cur, bg, model = _describe(UB_PCB)
    poly = be.rounded_box(*cur["bbox"], cur["corner_radius"])
    edges = {e["ref"]: e for e in json.loads(
        (UB / "constraints.json").read_text())["placement"]["edges"]}
    assert _issues(UB_PCB, poly, edges) == []
    undeclared = [i["key"] for i in _issues(UB_PCB, poly, {})]
    assert "J1" in undeclared


def test_preexisting_overhang_does_not_block_but_worsening_does():
    cur, bg, model = _describe(UB_PCB)
    floors = be.fab_floors(UB_PCB)
    old = be.rounded_box(*cur["bbox"], 0.0)
    before = be.outline_issues(model, bg, old, floors, {})
    assert [i["key"] for i in before] == ["J1"]        # J1 already overhangs
    # the same outline: nothing new
    assert be.new_or_worse(before, before) == []
    # 1 mm off the left edge: J1's overhang grows -> blocked
    x1, y1, x2, y2 = cur["bbox"]
    worse = be.outline_issues(model, bg, be.rounded_box(x1 + 1, y1, x2, y2, 0.0),
                              floors, {})
    blocked = be.new_or_worse(before, worse)
    assert "J1" in [b["key"] for b in blocked]
    assert next(b for b in blocked if b["key"] == "J1")["was"] is not None


def test_cutout_rules(tmp_path):
    rect = [0.0, 0.0, 40.0, 30.0]
    be.validate_cutouts(rect, 0.0, [{"x": 10, "y": 0, "w": 6, "h": 3}])  # ok
    with pytest.raises(CheckError, match="touches no outline edge"):
        be.validate_cutouts(rect, 0.0, [{"x": 10, "y": 10, "w": 5, "h": 5}])
    with pytest.raises(CheckError, match="corner radius"):
        be.validate_cutouts(rect, 3.0, [{"x": 0, "y": 0, "w": 5, "h": 3}])
    with pytest.raises(CheckError, match="outside the new outline"):
        be.validate_cutouts(rect, 0.0, [{"x": 38, "y": 0, "w": 5, "h": 3}])


def test_cutouts_that_would_split_the_board_are_refused():
    """A full-depth notch is a legal notch on each edge it touches and still
    leaves two boards - that is a panel, not an outline."""
    payload = _run(PD_PCB, "--outline", "keep", "--cutout", "20,0,4,30",
                   "--report-only", expect=2)
    assert "separate pieces" in payload["error"]


def test_bad_outline_specs_are_errors():
    for spec in ("40", "40x", "0x30", "fit30"):
        payload = _run(PD_PCB, "--outline", spec, "--report-only", expect=2)
        assert payload["status"] == "error"


# ============================================================ the edit class

def test_outline_change_class_stales_exactly_the_mapped_set():
    cls = statelib.load_map()["edit_classes"]["outline_change"]
    assert cls["mutates"] == ["pcb"]
    assert cls["stale_artifacts"] == ["gerbers"]
    assert set(cls["gates"]) == {"place", "drc", "drc_routed", "verify", "dfm"}
    assert cls["human_hold"] == 2


def test_report_carries_the_gates_and_hold_from_the_map():
    payload = _run(PD_PCB, "--outline", "keep", "--report-only")
    assert payload["edit_class"] == "outline_change"
    assert payload["gates_to_rerun"] == sorted(
        statelib.load_map()["edit_classes"]["outline_change"]["gates"])
    assert payload["human_hold"] == 2


def test_resize_board_verb_is_bound_to_this_script():
    import yaml
    tasks = yaml.safe_load(
        (SCRIPTS.parent / "reference" / "tasks.yaml").read_text(encoding="utf-8"))
    verb = tasks["verbs"]["resize-board"]
    assert verb["edit_class"] == "outline_change"
    assert "gates" not in verb and "human_hold" not in verb
    cmds = [s["do"] for s in verb["steps"] if "do" in s]
    assert any("board_edit.py" in c and "--report-only" in c for c in cmds)
    assert any("board_edit.py" in c and "--workspace" in c for c in cmds)
    # it records the class itself - a second recorder would double-stamp
    assert not any("state.py edit" in c for c in cmds)


def test_find_workspace_is_one_definition(tmp_path):
    """U16 put "which state.json owns this file" in gate.py; U17 needs the
    same answer, so the definition moved to statelib and gate.py aliases it."""
    import gate
    assert "def find_workspace" not in \
        (SCRIPTS / "gate.py").read_text(encoding="utf-8")
    assert gate.find_workspace.__module__.endswith("statelib")
    ws = tmp_path / "wsx"
    (ws / "kicad").mkdir(parents=True)
    (ws / "state.json").write_text("{}", encoding="utf-8")
    (ws / "kicad" / "wsx.kicad_pcb").write_text("", encoding="utf-8")
    assert statelib.find_workspace(ws / "kicad" / "wsx.kicad_pcb") == ws
    assert statelib.find_workspace(PD_PCB) is None      # fixture, no workspace
    with pytest.raises(RuntimeError, match="no state.json"):
        statelib.find_workspace(None, str(REPO / "tests"))


# ================================================================== smoke

@pytest.mark.smoke
def test_apply_resize_keeps_every_piece_of_copper(tmp_path):
    """Acceptance: a resize that keeps everything inside applies, re-parses to
    the exact new outline, leaves copper untouched and DRC no worse."""
    pcb = _stage_pd(tmp_path)
    before = geom.BoardGeom.from_file(pcb)
    before_inv = be.copper_inventory(before, placelib.PlaceModel(pcb))

    payload = _run(pcb, "--outline", "50x32", "--anchor", "center",
                   "--no-record")
    assert payload["status"] == "pass" and payload["applied"] is True
    assert payload["drc"]["errors_after"] <= payload["drc"]["errors_before"]
    assert payload["refilled"] is True

    after = geom.BoardGeom.from_file(pcb)
    got = be.describe_outline(after.outline_faces, after.outline_arc_radii,
                              after.outline_items)
    assert got["w"] == 50.0 and got["h"] == 32.0
    assert got["bbox"] == payload["outline"]["after"]["bbox"]
    assert be.copper_inventory(after, placelib.PlaceModel(pcb)) == before_inv
    assert not after.unfilled_zones()

    # absolute spec -> re-applying the same size changes nothing
    again = _run(pcb, "--outline", "50x32", "--anchor", "center", "--no-record")
    assert again["outline"]["after"]["bbox"] == payload["outline"]["after"]["bbox"]
    assert be.copper_inventory(geom.BoardGeom.from_file(pcb),
                               placelib.PlaceModel(pcb)) == before_inv


@pytest.mark.smoke
def test_apply_refuses_and_leaves_the_board_byte_identical(tmp_path):
    pcb = _stage_pd(tmp_path)
    orig = pcb.read_bytes()
    payload = _run(pcb, "--outline", "30x20", expect=1)
    assert payload["applied"] is False
    assert payload["blocking_summary"]["by_kind"]["footprint_outside"] > 0
    assert pcb.read_bytes() == orig


@pytest.mark.smoke
def test_a_failing_worker_rolls_back_byte_identically(tmp_path, monkeypatch):
    pcb = _stage_pd(tmp_path)
    orig = pcb.read_bytes()
    monkeypatch.setattr(be, "WORKER", SCRIPTS / "lib" / "no_such_worker.py")
    with pytest.raises(CheckError, match="rolled back"):
        be.run(["--pcb", str(pcb), "--outline", "50x32", "--no-record",
                "--no-refill"])
    assert pcb.read_bytes() == orig
    assert not [p for p in pcb.parent.iterdir() if p.name.startswith(".aiee")]


@pytest.mark.smoke
def test_corner_radius_and_notch_round_trip(tmp_path):
    pcb = _stage_pd(tmp_path)
    grown = _run(pcb, "--outline", "58x40", "--anchor", "center",
                 "--corner-radius", "3", "--no-record", "--no-refill")
    assert grown["outline"]["after"]["corner_radius"] == 3.0
    bg = geom.BoardGeom.from_file(pcb)
    assert bg.outline_items == {"gr_line": 4, "gr_arc": 4}
    # exact, from the arcs: a radius re-read from the area would drift
    assert be.describe_outline(bg.outline_faces, bg.outline_arc_radii,
                               bg.outline_items)["corner_radius"] == 3.0

    notched = _run(pcb, "--outline", "keep", "--cutout", "25,0,8,3",
                   "--no-record", "--no-refill")
    assert notched["applied"] is True
    area = geom.BoardGeom.from_file(pcb).outline.area
    assert area == pytest.approx(grown["outline"]["after"]["area_mm2"] - 24,
                                 abs=0.05)
    # the notched board is no longer restatable -> next edit needs consent
    blocked = _run(pcb, "--outline", "keep", "--report-only", expect=2)
    assert "not a plain (rounded) rectangle" in blocked["error"]


@pytest.mark.smoke
def test_shrink_to_fit_bb_buck_and_record_the_edit(tmp_path):
    """Acceptance: shrink-to-fit on the bb-buck placement yields a legal
    outline no larger than its own content bbox + margin, and the edit lands
    in the workspace's state.json as outline_change (nobody has to remember
    a second command)."""
    need_board("bb-buck")
    ws = tmp_path / "bb"
    ws.mkdir()
    shutil.copy2(BB / "state.json", ws / "state.json")
    shutil.copytree(BB / "kicad", ws / "kicad")
    (ws / "reports").mkdir()
    pcb = ws / "kicad" / f"{BB.name}.kicad_pcb"   # <PN>_bb-buck

    payload = _run(pcb, "--outline", "fit", "--margin", "0.5")
    assert payload["status"] == "pass" and payload["applied"] is True
    x1, y1, x2, y2 = payload["outline"]["after"]["bbox"]
    cx1, cy1, cx2, cy2 = payload["outline"]["after"]["content_bbox"]
    assert (x2 - x1) <= (cx2 - cx1) + 2 * 0.5 + 2 * be.GRID
    assert (y2 - y1) <= (cy2 - cy1) + 2 * 0.5 + 2 * be.GRID
    assert payload["blocking"] == []
    assert payload["drc"]["errors_after"] == 0

    # legal by the same yardstick the P9 gate uses
    bg = geom.BoardGeom.from_file(pcb)
    model = placelib.PlaceModel(pcb)
    assert be.outline_issues(model, bg, bg.outline, be.fab_floors(pcb), {}) == []

    state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    edit = state["edits"][-1]
    assert edit["class"] == "outline_change" and edit["human_hold"] == 2
    assert set(edit["gates_marked"]) == {"place", "drc_routed", "verify", "dfm"}
    assert state["artifacts"]["gerbers"]["stale"]
    assert payload["record"]["recorded"] is True
