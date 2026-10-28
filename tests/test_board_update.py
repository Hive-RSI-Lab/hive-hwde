"""T8 tests: board_update - incremental netlist-diff surgery on routed boards.

Pure tests (venv only: sexpdata/geom/netconn/placelib) cover diff
classification, orphan connectivity analysis and placement validation against
the committed frozen fixtures (pd_trigger route stage, lumina_carrier routed).
Smoke tests (SWIG bundled python + kicad-cli) cover the three apply modes
end-to-end, the fix-loop-to-DRC-0/0 acceptance, rollback and the carrier
4-layer del+add. Mutant netlists are generated in-test by deterministic
sexpdata surgery on the frozen netlists - nothing extra is committed.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import sexpdata

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import board_update as bu  # noqa: E402
import geom  # noqa: E402
import placelib  # noqa: E402
import statelib  # noqa: E402
from checklib import CheckError  # noqa: E402

PD = REPO / "tests" / "fixtures" / "stages" / "pd_trigger"
PD_PCB = PD / "route" / "pd-trigger.kicad_pcb"
PD_NET = PD / "pd-trigger.net"
PD_LIB = PD / "lib"
CARRIER = REPO / "tests" / "fixtures" / "stages" / "lumina_carrier"
CA_PCB = CARRIER / "routed.kicad_pcb"
CA_NET = CARRIER / "lumina-carrier.net"

S = sexpdata.Symbol


# ---------------------------------------------------------------------------
# netlist surgery helpers (deterministic, tree-level)
# ---------------------------------------------------------------------------

def _head(n):
    return n[0].value() if isinstance(n, list) and n \
        and isinstance(n[0], sexpdata.Symbol) else None


def _kidval(n, key):
    for c in n[1:]:
        if _head(c) == key:
            return c[1] if len(c) > 1 else None
    return None


def _load_net(path: Path):
    return sexpdata.loads(path.read_text(encoding="utf-8"))


def _dump_net(tree, path: Path) -> Path:
    path.write_text(sexpdata.dumps(tree), encoding="utf-8")
    return path


def _comps_sec(tree):
    return next(s for s in tree[1:] if _head(s) == "components")


def _nets_sec(tree):
    return next(s for s in tree[1:] if _head(s) == "nets")


def _del_ref(tree, ref: str) -> None:
    sec = _comps_sec(tree)
    sec[:] = [sec[0]] + [c for c in sec[1:]
                         if not (_head(c) == "comp"
                                 and _kidval(c, "ref") == ref)]
    for net in _nets_sec(tree)[1:]:
        if _head(net) != "net":
            continue
        net[:] = [x for x in net
                  if not (_head(x) == "node" and _kidval(x, "ref") == ref)]


def _set_value(tree, ref: str, value: str) -> None:
    for c in _comps_sec(tree)[1:]:
        if _head(c) == "comp" and _kidval(c, "ref") == ref:
            for k in c[1:]:
                if _head(k) == "value":
                    k[1] = value
                    return
    raise AssertionError(f"{ref} not in netlist")


def _set_field(tree, ref: str, name: str, value: str) -> None:
    for c in _comps_sec(tree)[1:]:
        if _head(c) == "comp" and _kidval(c, "ref") == ref:
            for k in c[1:]:
                if _head(k) == "fields":
                    for f in k[1:]:
                        if _head(f) == "field" and _kidval(f, "name") == name:
                            f[-1] = value
                            return
                    k.append([S("field"), [S("name"), name], value])
                    return
    raise AssertionError(f"{ref} not in netlist")


def _set_footprint(tree, ref: str, fpid: str) -> None:
    for c in _comps_sec(tree)[1:]:
        if _head(c) == "comp" and _kidval(c, "ref") == ref:
            for k in c[1:]:
                if _head(k) == "footprint":
                    k[1] = fpid
                    return
    raise AssertionError(f"{ref} not in netlist")


def _remove_field(tree, ref: str, name: str) -> None:
    for c in _comps_sec(tree)[1:]:
        if _head(c) == "comp" and _kidval(c, "ref") == ref:
            for k in c[1:]:
                if _head(k) == "fields":
                    k[:] = [x for x in k
                            if not (_head(x) == "field"
                                    and _kidval(x, "name") == name)]
                    return
    raise AssertionError(f"{ref} not in netlist")


def _add_comp(tree, ref: str, value: str, fpid: str,
              pin_nets: dict[str, str], lcsc: str = "C14663") -> None:
    _comps_sec(tree).append(
        [S("comp"), [S("ref"), ref], [S("value"), value],
         [S("footprint"), fpid],
         [S("fields"), [S("field"), [S("name"), "LCSC"], lcsc]]])
    for net in _nets_sec(tree)[1:]:
        if _head(net) != "net":
            continue
        name = _kidval(net, "name")
        for pin, want in pin_nets.items():
            if name == want:
                net.append([S("node"), [S("ref"), ref], [S("pin"), pin],
                            [S("pintype"), "passive"]])


def _rewire(tree, ref: str, pin: str, to_net: str) -> None:
    """Move REF.PIN to `to_net` (an unsupported-diff generator)."""
    moved = None
    for net in _nets_sec(tree)[1:]:
        if _head(net) != "net":
            continue
        for x in list(net):
            if _head(x) == "node" and _kidval(x, "ref") == ref \
                    and _kidval(x, "pin") == pin:
                net.remove(x)
                moved = x
    assert moved is not None, f"{ref}.{pin} not in netlist"
    for net in _nets_sec(tree)[1:]:
        if _head(net) == "net" and _kidval(net, "name") == to_net:
            net.append(moved)
            return
    raise AssertionError(f"net {to_net} not in netlist")


# ---------------------------------------------------------------------------
# staging helpers
# ---------------------------------------------------------------------------

def _stage(tmp_path: Path, pcb: Path, extra_exts=(".kicad_pro", ".kicad_dru",
                                                  ".kicad_prl")) -> Path:
    dst = tmp_path / pcb.name
    shutil.copy2(pcb, dst)
    for ext in extra_exts:
        sib = pcb.with_suffix(ext)
        if sib.is_file():
            shutil.copy2(sib, tmp_path / sib.name)
    return dst


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(pcb: Path, netlist: Path, *extra: str):
    payload, _ = bu.run(["--pcb", str(pcb), "--netlist", str(netlist),
                         *extra])
    return payload


def _norm_lines(pcb: Path) -> list[str]:
    out = statelib.NORMALIZERS["sexpr_no_uuid"](Path(pcb))
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    return out.splitlines()


def _normalized_delta(a: Path, b: Path) -> list[str]:
    import difflib
    return [ln for ln in difflib.unified_diff(_norm_lines(a), _norm_lines(b),
                                              lineterm="", n=0)
            if ln[:1] in "+-" and ln[:3] not in ("+++", "---")]


# ---------------------------------------------------------------------------
# pure: diff classification
# ---------------------------------------------------------------------------

def test_baseline_diff_empty_pd():
    payload = _run(PD_PCB, PD_NET, "--dry-run")
    assert payload["status"] == "pass"
    assert payload["changes"] == 0
    for bucket in ("swap_same_fp", "swap_new_fp", "add", "del",
                   "unsupported"):
        assert payload["plan"][bucket] == []


def test_baseline_diff_empty_carrier_and_board_only_excluded():
    payload = _run(CA_PCB, CA_NET, "--dry-run")
    assert payload["changes"] == 0
    assert payload["plan"]["unsupported"] == []
    note = " ".join(payload["plan"]["notes"])
    for hole in ("H1", "H2", "H3", "H4"):
        assert hole in note  # mounting holes never classified as dels


def test_swap_classification(tmp_path):
    tree = _load_net(PD_NET)
    _set_value(tree, "C5", "2.2uF 25V X5R")
    _set_field(tree, "C5", "LCSC", "C23630")
    net = _dump_net(tree, tmp_path / "m.net")
    plan = _run(PD_PCB, net, "--dry-run")["plan"]
    assert [e["ref"] for e in plan["swap_same_fp"]] == ["C5"]
    entry = plan["swap_same_fp"][0]
    assert entry["value"] == ["1uF 50V X5R", "2.2uF 25V X5R"]
    assert entry["fields"]["LCSC"][1] == "C23630"
    assert plan["add"] == plan["del"] == plan["swap_new_fp"] == []


def test_add_classification(tmp_path):
    tree = _load_net(PD_NET)
    _add_comp(tree, "C99", "100nF 50V X7R", "aiee:C0603",
              {"1": "VBUS", "2": "GND"})
    plan = _run(PD_PCB, _dump_net(tree, tmp_path / "m.net"),
                "--dry-run")["plan"]
    assert [e["ref"] for e in plan["add"]] == ["C99"]
    assert plan["add"][0]["pads"] == {"1": "VBUS", "2": "GND"}
    assert plan["del"] == plan["swap_same_fp"] == []


def test_del_classification(tmp_path):
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    plan = _run(PD_PCB, _dump_net(tree, tmp_path / "m.net"),
                "--dry-run")["plan"]
    assert [e["ref"] for e in plan["del"]] == ["C2"]
    assert plan["del"][0]["nets"] == ["GND", "VBUS"]


def test_new_fp_classification(tmp_path):
    tree = _load_net(PD_NET)
    _set_footprint(tree, "C5", "aiee:C1206")
    plan = _run(PD_PCB, _dump_net(tree, tmp_path / "m.net"),
                "--dry-run")["plan"]
    assert [e["ref"] for e in plan["swap_new_fp"]] == ["C5"]
    entry = plan["swap_new_fp"][0]
    assert entry["fp_old"] == "aiee:C0603"
    assert entry["fp_new"] == "aiee:C1206"
    assert entry["pos"]  # defaults the add leg to the old spot
    assert plan["del"] == plan["add"] == []


def test_unsupported_rewire_refuses_apply(tmp_path):
    tree = _load_net(PD_NET)
    _rewire(tree, "C5", "1", "GND")
    net = _dump_net(tree, tmp_path / "m.net")
    payload = _run(PD_PCB, net, "--dry-run")
    assert payload["status"] == "violations"  # dry-run reports, exit 1
    assert payload["plan"]["unsupported"][0]["ref"] == "C5"
    assert payload["plan"]["unsupported"][0]["kind"] == "pad_net_change"
    with pytest.raises(CheckError, match="unsupported"):
        _run(PD_PCB, net)  # apply refuses, nothing mutated


def test_unconnected_star_normalization():
    assert bu._nets_equal("unconnected-(U1-PB0-Pad10)",
                          "unconnected-(U1-PB0-Pad9)")
    assert bu._nets_equal("", None)
    assert not bu._nets_equal("GND", "VBUS")
    assert not bu._nets_equal("unconnected-(X)", "GND")
    # hierarchical exports DROP NC singleton nets: board unconnected-* vs
    # netlist-absent pin (and the netless-board-pad mirror) are the same
    # no-connect class, never a pad_net_change refusal
    assert bu._nets_equal("unconnected-(U1-Pad7)", "")
    assert bu._nets_equal(None, "unconnected-(U1-Pad7)")


def test_board_only_collision_is_unsupported(tmp_path):
    """A netlist ref colliding with a board_only footprint (mounting-hole
    class) must refuse at PLAN time, not die inside the SWIG worker."""
    tree = _load_net(CA_NET)
    _add_comp(tree, "H1", "M3", "aiee:C0603", {})
    payload = _run(CA_PCB, _dump_net(tree, tmp_path / "m.net"), "--dry-run")
    assert payload["status"] == "violations"
    u = payload["plan"]["unsupported"]
    assert [e["ref"] for e in u] == ["H1"]
    assert u[0]["kind"] == "board_only_collision"
    assert payload["plan"]["add"] == []


def test_case_only_fpid_is_note_not_swap(tmp_path):
    """Case-only fpid drift is electrically null - it must not classify as
    a destructive swap_new_fp (del + orphan rip + re-add)."""
    tree = _load_net(PD_NET)
    _set_footprint(tree, "C5", "AIEE:C0603")
    plan = _run(PD_PCB, _dump_net(tree, tmp_path / "m.net"),
                "--dry-run")["plan"]
    assert plan["swap_new_fp"] == []
    assert any("differs only by case" in n for n in plan["notes"])


def test_field_removed_from_netlist_is_noted(tmp_path):
    """A field REMOVED from the netlist (in the netlist's own field
    vocabulary) must not pass silently - it feeds the BOM stale."""
    tree = _load_net(PD_NET)
    _remove_field(tree, "C5", "LCSC")
    plan = _run(PD_PCB, _dump_net(tree, tmp_path / "m.net"),
                "--dry-run")["plan"]
    assert plan["swap_same_fp"] == []
    assert any("C5" in n and "LCSC" in n and "absent from the netlist" in n
               for n in plan["notes"])
    # board-side-only field names ("LCSC Part") never note: the clean
    # baseline diff (test_baseline_diff_empty_pd) pins that direction


# ---------------------------------------------------------------------------
# pure: orphan connectivity analysis
# ---------------------------------------------------------------------------

def test_orphan_analysis_del_c2():
    """Machine-pinned on the frozen fixture: deleting C2 orphans exactly its
    GND stub + GND via; the VBUS feeder (anchored beyond C2) survives; the
    two VBUS fan-in lobes (T-junction endpoint-in-body, DRC-clean) surface
    as pre-existing unanchored copper and are NOT in the orphan set."""
    bg = geom.BoardGeom.from_file(PD_PCB)
    orph = bu.plan_orphans(bg, ["C2"])
    assert orph["affected_nets"] == ["GND", "VBUS"]
    assert [(t.net, t.layer) for t in orph["tracks"]] == [("GND", "F.Cu")]
    assert [(v.net, v.at) for v in orph["vias"]] == [("GND", (24.3, 48.1301))]
    lobes = {p["uuid"] for p in orph["preexisting"]}
    assert len(lobes) == 2  # the two wide VBUS fan-in lobes
    assert {t.uuid for t in orph["tracks"]}.isdisjoint(lobes)
    assert all(t.uuid for t in orph["tracks"])  # removal needs names


def test_orphan_analysis_empty_without_dels():
    bg = geom.BoardGeom.from_file(PD_PCB)
    orph = bu.plan_orphans(bg, [])
    assert orph["affected_nets"] == []
    assert orph["tracks"] == [] and orph["vias"] == []


def test_orphan_analysis_carrier_plane_fed_cap():
    """4L carrier: C62's legs are plane-fed via dedicated vias - deleting it
    orphans those two vias (GND + V48_RAW) and no tracks; the pours and
    every other net stay untouched."""
    bg = geom.BoardGeom.from_file(CA_PCB)
    orph = bu.plan_orphans(bg, ["C62"])
    assert set(orph["affected_nets"]) == {"GND", "V48_RAW"}
    assert orph["tracks"] == []
    assert sorted((v.net, v.at) for v in orph["vias"]) == \
        [("GND", (35.35, 93.5)), ("V48_RAW", (38.0, 93.5))]


def test_geom_tracks_vias_carry_uuids():
    for pcb in (PD_PCB, CA_PCB):
        bg = geom.BoardGeom.from_file(pcb)
        assert all(t.uuid for t in bg.tracks_of())
        assert all(v.uuid for v in bg.vias_of())


# ---------------------------------------------------------------------------
# pure: placements
# ---------------------------------------------------------------------------

def test_placements_validation_shapes():
    adds = [{"ref": "C99"}]
    with pytest.raises(CheckError, match="region"):
        bu.validate_placements({"C99": {"region": [5, 5, 1, 9]}}, adds)
    with pytest.raises(CheckError, match="front-side"):
        bu.validate_placements(
            {"C99": {"region": [1, 1, 9, 9], "side": "back"}}, adds)
    with pytest.raises(CheckError, match="needs x\\+y or region"):
        bu.validate_placements({"C99": {"deg": 90}}, adds)
    out, unused = bu.validate_placements(
        {"C99": {"x": 1, "y": 2}, "C42": {"x": 0, "y": 0}}, adds)
    assert unused == ["C42"]


def test_region_scan_rejects_fully_occupied_region(tmp_path):
    tree = _load_net(PD_NET)
    _add_comp(tree, "C99", "100nF", "aiee:C0603", {"1": "VBUS", "2": "GND"})
    net = _dump_net(tree, tmp_path / "m.net")
    pl = tmp_path / "p.json"
    # dead center of U1 - nothing legal there
    pl.write_text(json.dumps({"C99": {"region": [24.0, 36.0, 26.0, 38.0]}}))
    with pytest.raises(CheckError, match="no courtyard-legal spot"):
        _run(PD_PCB, net, "--placements", str(pl),
             "--lib", str(PD_LIB))


def test_mod_extents_courtyard():
    mod = bu.find_mod("aiee:C0603", [PD_LIB])
    assert mod is not None
    x0, y0, x1, y1 = bu._mod_extents(mod)
    assert 1.0 < (x1 - x0) < 4.0 and 0.5 < (y1 - y0) < 3.0


def test_add_needs_placement(tmp_path):
    tree = _load_net(PD_NET)
    _add_comp(tree, "C99", "100nF", "aiee:C0603", {"1": "VBUS", "2": "GND"})
    net = _dump_net(tree, tmp_path / "m.net")
    with pytest.raises(CheckError, match="needs a placement"):
        _run(PD_PCB, net, "--lib", str(PD_LIB))


def test_add_unknown_footprint_lib(tmp_path):
    tree = _load_net(PD_NET)
    _add_comp(tree, "C99", "100nF", "nosuchlib:C0603",
              {"1": "VBUS", "2": "GND"})
    net = _dump_net(tree, tmp_path / "m.net")
    pl = tmp_path / "p.json"
    pl.write_text(json.dumps({"C99": {"x": 45.0, "y": 46.0}}))
    with pytest.raises(CheckError, match="not found under"):
        _run(PD_PCB, net, "--placements", str(pl), "--lib", str(PD_LIB))


def test_dry_run_mutates_nothing(tmp_path):
    pcb = _stage(tmp_path, PD_PCB)
    before = _sha(pcb)
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    _run(pcb, _dump_net(tree, tmp_path / "m.net"), "--dry-run")
    assert _sha(pcb) == before


def test_two_region_adds_do_not_stack():
    """Two region adds sharing a region must resolve to NON-overlapping
    spots: each resolved courtyard becomes an obstacle for the next."""
    bg = geom.load_board(PD_PCB, refresh=True)
    model = placelib.PlaceModel(PD_PCB)
    mod = bu.find_mod("aiee:C0603", [PD_LIB])
    shared: list = []
    warnings: list = []
    spec = {"region": [40.0, 40.0, 50.0, 50.0]}
    a = bu.resolve_placement("C98", spec, mod, {"1": "VBUS", "2": "GND"},
                             model, bg, warnings, extra_obstacles=shared)
    b = bu.resolve_placement("C99", spec, mod, {"1": "VBUS", "2": "GND"},
                             model, bg, warnings, extra_obstacles=shared)
    assert (a["x"], a["y"]) != (b["x"], b["y"])
    assert len(shared) == 2
    from shapely.geometry import box as _box
    x0, y0, x1, y1 = bu._mod_extents(mod)
    ca = _box(x0 + a["x"], y0 + a["y"], x1 + a["x"], y1 + a["y"])
    cb = _box(x0 + b["x"], y0 + b["y"], x1 + b["x"], y1 + b["y"])
    assert not ca.intersects(cb)


def test_state_prevalidated_before_mutation(tmp_path):
    """A bad --state path must fail BEFORE any mutation (the caller asked
    for edit recording; silently skipping it would drop the invalidation
    marks)."""
    pcb = _stage(tmp_path, PD_PCB)
    before = _sha(pcb)
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    net = _dump_net(tree, tmp_path / "m.net")
    with pytest.raises(CheckError, match="state file"):
        _run(pcb, net, "--state", str(tmp_path / "nope" / "state.json"))
    assert _sha(pcb) == before


# ---------------------------------------------------------------------------
# smoke: the three modes end-to-end (SWIG worker + kicad-cli)
# ---------------------------------------------------------------------------

@pytest.mark.kicad_recorded
@pytest.mark.smoke
def test_swap_apply_fields_only_bom_cpl_and_state(tmp_path):
    """Acceptance (a): the swap diff touches ONLY fields/BOM/CPL. Geometry
    stability is asserted through the T7 normalizer (sexpr_no_uuid): the
    normalized board delta is exactly the swapped property lines. BOM
    regeneration moves only the swapped group; CPL is byte-identical. The
    edit lands in state.json as swap_part_same_fp."""
    ws = tmp_path / "ws"
    (ws / "kicad").mkdir(parents=True)
    pcb = _stage(ws / "kicad", PD_PCB)
    orig = tmp_path / "orig.kicad_pcb"
    shutil.copy2(pcb, orig)
    tree = _load_net(PD_NET)
    _set_value(tree, "C5", "2.2uF 25V X5R")
    _set_field(tree, "C5", "LCSC", "C23630")
    net = _dump_net(tree, tmp_path / "m.net")

    subprocess.run([sys.executable, str(SCRIPTS / "state.py"), "init",
                    "--workspace", str(ws), "--board", "pd-trigger"],
                   check=True, capture_output=True)
    payload = _run(pcb, net, "--state", str(ws / "state.json"))
    assert payload["status"] == "pass"
    assert payload["applied"]["worker"]["fields_updated"] == ["C5"]
    assert payload["applied"]["drc"]["after"]["total"] == 0

    delta = _normalized_delta(orig, pcb)
    assert delta, "value change must appear"
    for line in delta:
        assert "(property" in line or "LCSC" in line, \
            f"non-field change leaked into the board: {line}"

    # copper + placement inventories identical
    a, b = geom.BoardGeom.from_file(orig), geom.BoardGeom.from_file(pcb)
    assert bu._copper_inventory(a) == bu._copper_inventory(b)
    ma, mb = placelib.PlaceModel(orig), placelib.PlaceModel(pcb)
    assert {r: f.pos for r, f in ma.footprints.items()} \
        == {r: f.pos for r, f in mb.footprints.items()}

    # BOM/CPL: only the swapped group moves
    def fab(board, out):
        out.mkdir()
        subprocess.run([sys.executable, str(SCRIPTS / "bom_cpl.py"),
                        "--pcb", str(board), "--out-dir", str(out)],
                       check=True, capture_output=True)
        return ((out / "BOM.csv").read_text().splitlines(),
                (out / "CPL.csv").read_text())

    bom_a, cpl_a = fab(orig, tmp_path / "fab_a")
    bom_b, cpl_b = fab(pcb, tmp_path / "fab_b")
    assert cpl_a == cpl_b
    changed = set(bom_a).symmetric_difference(bom_b)
    assert changed and all("C5" in row or "2.2uF" in row or "1uF" in row
                           for row in changed)

    st = json.loads((ws / "state.json").read_text())
    assert [(e["class"], e["refs"]) for e in st["edits"]] \
        == [("swap_part_same_fp", ["C5"])]
    assert payload["state"]["human_hold"] == 2
    # the re-run set comes from the invalidation map, not prose
    imap = statelib.load_map()
    assert payload["gates_to_rerun"] \
        == sorted(imap["edit_classes"]["swap_part_same_fp"]["gates"])


@pytest.mark.kicad_recorded
@pytest.mark.smoke
def test_del_apply_rips_orphans_and_silk(tmp_path):
    """Acceptance (c): delete leaves no orphan stubs (connectivity-checked
    AND KiCad-checked: DRC total 0 after) and no silk residue (a functional
    silk label inside C2's bbox goes with it). Pre-existing unanchored
    copper (the VBUS fan-in lobes) is preserved."""
    pcb = _stage(tmp_path, PD_PCB)
    import place_edit
    place_edit.apply_ops(pcb, [{"op": "add_text", "text": "IN CAP",
                                "x": 23.6, "y": 47.2, "layer": "F.SilkS"}])
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    payload = _run(pcb, _dump_net(tree, tmp_path / "m.net"))
    assert payload["status"] == "pass"
    assert payload["applied"]["worker"]["removed_refs"] == ["C2"]
    assert payload["applied"]["worker"]["removed_items"] == 2  # stub + via
    texts = payload["applied"]["worker"]["removed_texts"]
    assert [t["text"] for t in texts] == ["IN CAP"]
    # worker must report the FILE-token layer name or the driver's
    # silk-residue verify is vacuous (review finding: GetLayerName says
    # "F.Silkscreen" while sexpr carries "F.SilkS")
    assert texts[0]["layer"] == "F.SilkS"
    assert payload["applied"]["drc"]["after"]["total"] == 0
    assert payload["applied"]["drc"]["dangling_after"] == 0
    assert payload["applied"]["refilled"] is True

    b = geom.BoardGeom.from_file(pcb)
    assert not [p for p in b.pads_of() if p.ref == "C2"]
    # the two pre-existing fan-in lobes survive (by geometry - uuids churn)
    keys = {bu._item_key_track(t) for t in b.tracks_of("VBUS")}
    a = geom.BoardGeom.from_file(PD_PCB)
    for uu in ("01de587b", "e0b8be12"):
        t = next(t for t in a.tracks_of("VBUS") if t.uuid.startswith(uu))
        assert bu._item_key_track(t) in keys
    assert "IN CAP" not in pcb.read_text(encoding="utf-8")


@pytest.mark.kicad_recorded
@pytest.mark.smoke
def test_replace_c2_reaches_drc_zero(tmp_path):
    """Acceptance (b): one invocation deletes C2 and adds C99 at the freed
    spot; existing copper is preserved; the single open ratsnest leg closes
    through the STANDARD fix loop (route_edit via + kicad-cli refill) and
    the board reaches DRC 0 errors / 0 warnings."""
    pcb = _stage(tmp_path, PD_PCB)
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    _add_comp(tree, "C99", "100nF 50V X7R", "aiee:C0603",
              {"1": "VBUS", "2": "GND"})
    net = _dump_net(tree, tmp_path / "m.net")
    pl = tmp_path / "p.json"
    pl.write_text(json.dumps({"C99": {"x": 23.6, "y": 47.2}}))

    before = geom.BoardGeom.from_file(pcb)
    payload = _run(pcb, net, "--placements", str(pl), "--lib", str(PD_LIB))
    assert payload["status"] == "pass"
    assert payload["applied"]["drc"]["new_errors"] == 0
    assert payload["applied"]["drc"]["unconnected_after"] == 1  # GND leg
    assert payload["applied"]["drc"]["dangling_after"] == 0

    # existing copper preserved minus exactly the C2 orphans
    after = geom.BoardGeom.from_file(pcb)
    inv_before = bu._copper_inventory(before)
    for t in payload_orphans_tracks(before):
        inv_before[t] -= 1
    assert +inv_before == +bu._copper_inventory(after)

    # standard fix loop: GND via at C99.2, refill, gate
    import route_edit
    route_edit.apply_ops(pcb, [{"op": "add_via", "at": [24.3, 47.2],
                                "size": 0.6, "drill": 0.3, "net": "GND"}])
    import env as env_mod
    import kc
    rep = kc.run_drc(env_mod.find_kicad_cli(), pcb, refill=True,
                     save_board=True)
    assert rep["counts"]["total"] == 0  # 0 errors AND 0 warnings


def payload_orphans_tracks(before_bg) -> list[tuple]:
    orph = bu.plan_orphans(before_bg, ["C2"])
    return [bu._item_key_track(t) for t in orph["tracks"]] + \
           [bu._item_key_via(v) for v in orph["vias"]]


@pytest.mark.smoke
def test_add_region_scan_preserves_copper(tmp_path):
    """Acceptance (b), region form: the scan finds a courtyard-legal,
    clearance-clear spot inside the declared region; every pre-existing
    track/via survives byte-for-byte; the only DRC delta is the new part's
    ratsnest."""
    pcb = _stage(tmp_path, PD_PCB)
    tree = _load_net(PD_NET)
    _add_comp(tree, "C99", "100nF 50V X7R", "aiee:C0603",
              {"1": "VBUS", "2": "GND"})
    net = _dump_net(tree, tmp_path / "m.net")
    pl = tmp_path / "p.json"
    pl.write_text(json.dumps({"C99": {"region": [40.0, 40.0, 50.0, 50.0]}}))

    before_inv = bu._copper_inventory(geom.BoardGeom.from_file(pcb))
    payload = _run(pcb, net, "--placements", str(pl), "--lib", str(PD_LIB))
    got = payload["placements"]["C99"]
    assert 40.0 <= got["x"] <= 50.0 and 40.0 <= got["y"] <= 50.0
    assert payload["applied"]["drc"]["new_errors"] == 0
    assert payload["applied"]["drc"]["unconnected_after"] == 2
    assert payload["applied"]["drc"]["dangling_after"] == 0

    after = geom.BoardGeom.from_file(pcb)
    assert bu._copper_inventory(after) == before_inv  # nothing touched
    fp = placelib.PlaceModel(pcb).footprints["C99"]
    assert {p.number: p.net for p in fp.pads} == {"1": "VBUS", "2": "GND"}


@pytest.mark.smoke
def test_carrier_del_and_add_4layer(tmp_path):
    """Carrier mutants: deleting the plane-fed C62 rips exactly its two
    feed vias on a 4-layer board with pours; a same-invocation region add
    (C99 on +3V3/GND) lands legally; no new dangling, planes intact."""
    pcb = _stage(tmp_path, CA_PCB)
    tree = _load_net(CA_NET)
    _del_ref(tree, "C62")
    _add_comp(tree, "C99", "100nF 50V X7R", "aiee:C0603",
              {"1": "+3V3", "2": "GND"})
    net = _dump_net(tree, tmp_path / "m.net")
    pl = tmp_path / "p.json"
    pl.write_text(json.dumps({"C99": {"region": [60.0, 60.0, 75.0, 75.0]}}))

    payload = _run(pcb, net, "--placements", str(pl), "--lib", str(PD_LIB))
    assert payload["status"] == "pass"
    assert payload["applied"]["worker"]["removed_refs"] == ["C62"]
    assert payload["applied"]["worker"]["removed_items"] == 2  # the 2 vias
    assert payload["applied"]["drc"]["dangling_after"] \
        <= payload["applied"]["drc"]["dangling_before"]
    assert payload["applied"]["drc"]["new_errors"] == 0
    assert payload["applied"]["refilled"] is True

    b = geom.BoardGeom.from_file(pcb)
    assert not [p for p in b.pads_of() if p.ref == "C62"]
    assert placelib.PlaceModel(pcb).footprints["C99"].side == "front"
    # planes still filled after the refill
    assert any(z.fills for z in b.zones_of("GND"))


# ---------------------------------------------------------------------------
# smoke: atomicity
# ---------------------------------------------------------------------------

@pytest.mark.smoke
def test_new_fp_swap_apply(tmp_path):
    """swap_part_new_fp applies as del+add of the SAME ref in one worker
    run (the duplicate-ref guard must tolerate a ref that is also being
    removed, and removal must rip the ORIGINAL object, not the
    replacement). C5 C0603 -> C1206 at the old spot: replacement present
    with the new fpid, pads netted, old orphan stubs gone, no new
    dangling."""
    pcb = _stage(tmp_path, PD_PCB)
    tree = _load_net(PD_NET)
    _set_footprint(tree, "C5", "aiee:C1206")
    payload = _run(pcb, _dump_net(tree, tmp_path / "m.net"),
                   "--lib", str(PD_LIB))
    assert payload["status"] == "pass"
    assert payload["applied"]["worker"]["removed_refs"] == ["C5"]
    assert [a["ref"] for a in payload["applied"]["worker"]["added"]] \
        == ["C5"]
    assert payload["applied"]["drc"]["dangling_after"] \
        <= payload["applied"]["drc"]["dangling_before"]
    fp = placelib.PlaceModel(pcb).footprints["C5"]
    assert fp.fpid == "aiee:C1206"
    nets = {p.number: p.net for p in fp.pads if p.number}
    assert set(nets) == {"1", "2"} and all(nets.values())


@pytest.mark.smoke
def test_dangling_gate_rolls_back(tmp_path, monkeypatch):
    """Wiring test for the hard DRC-dangling gate: if the staged board
    reports more dangling than the original, the update must roll back
    byte-identically. (The counts are monkeypatched - the organic
    scenario needs a board KiCad itself half-flags; see LEARNINGS
    [connectivity] 2026-08-07.)"""
    pcb = _stage(tmp_path, PD_PCB)
    before = _sha(pcb)
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    net = _dump_net(tree, tmp_path / "m.net")
    counts = iter([0, 1])  # before -> 0, after -> 1
    monkeypatch.setattr(bu, "_dangling_count", lambda rep: next(counts))
    with pytest.raises(CheckError, match="NEW dangling"):
        _run(pcb, net)
    assert _sha(pcb) == before


CRUMB = ('  (segment (start 24.3 47.2) (end 24.3 47.2) (width 0.25) '
         '(layer "F.Cu") (net "GND") '
         '(uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeee01"))\n')


@pytest.mark.kicad_recorded
@pytest.mark.smoke
def test_zero_length_crumb_inside_deleted_pad_is_removed(tmp_path):
    """Zero-length editing residue under a deleted pad is invisible to
    netconn (no edge) but must still be ripped, or the del rolls back
    forever on the crumb's track_dangling (adversarial-review probe,
    machine-verified). Judged by direct copper touch against dead pads."""
    pcb = _stage(tmp_path, PD_PCB)
    text = pcb.read_text(encoding="utf-8")
    assert text.rstrip().endswith(")")
    i = text.rindex(")")
    pcb.write_text(text[:i] + CRUMB + text[i:], encoding="utf-8")
    bg = geom.load_board(pcb, refresh=True)
    assert any(t.length == 0 for t in bg.tracks_of("GND"))

    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    payload = _run(pcb, _dump_net(tree, tmp_path / "m.net"))
    assert payload["status"] == "pass"
    assert payload["applied"]["worker"]["removed_items"] == 3  # stub+via+crumb
    assert payload["applied"]["drc"]["after"]["total"] == 0
    after = geom.load_board(pcb, refresh=True)
    assert not any(t.length == 0 for t in after.tracks_of())


@pytest.mark.smoke
def test_rollback_on_worker_failure(tmp_path, monkeypatch):
    pcb = _stage(tmp_path, PD_PCB)
    before = _sha(pcb)
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    net = _dump_net(tree, tmp_path / "m.net")

    def boom(*a, **k):
        raise CheckError("synthetic worker failure")
    monkeypatch.setattr(bu.routelib, "run_worker", boom)
    with pytest.raises(CheckError, match="synthetic worker failure"):
        _run(pcb, net)
    assert _sha(pcb) == before


@pytest.mark.smoke
def test_rollback_on_verify_failure(tmp_path, monkeypatch):
    pcb = _stage(tmp_path, PD_PCB)
    before = _sha(pcb)
    tree = _load_net(PD_NET)
    _del_ref(tree, "C2")
    net = _dump_net(tree, tmp_path / "m.net")

    monkeypatch.setattr(bu, "verify_apply",
                        lambda *a, **k: ["synthetic verify problem"])
    with pytest.raises(CheckError, match="synthetic verify problem"):
        _run(pcb, net)
    assert _sha(pcb) == before


def test_noop_apply_short_circuits(tmp_path):
    """A no-diff apply must not stage, run workers, or touch the board -
    pure by construction (no toolchain in this test)."""
    pcb = _stage(tmp_path, PD_PCB)
    before = _sha(pcb)
    payload = _run(pcb, PD_NET)
    assert payload["status"] == "pass"
    assert "nothing to do" in payload["note"]
    assert _sha(pcb) == before
