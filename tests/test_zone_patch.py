"""zone_patch: the same-net copper patch + via array pinned core scripted by
hand (kicad/gen/via_array.py, patch_outline.py, drop_patch.py).

The live tests run on a copy of pinned core, the board that needed the
scripts. They undo what the scripts made (drop PATCH_PVDD, or rip its vias)
and redo it with zone_patch, then check the board comes back the same: the
patch fill areas and the DRC counts of the pinned board.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import env  # noqa: E402
import geom  # noqa: E402
import zone_patch  # noqa: E402
from checklib import CheckError  # noqa: E402

CORE = REPO / "pinned" / "core" / "kicad"
PCB = CORE / "core.kicad_pcb"
PVDD_BOX = "53.6,165.8,59.6,171.2"            # PATCH_PVDD, a rectangle
VBUS_POLY = "50,174 58,174 58,181.2 52.5,181.2 52.5,183.6 50,183.6"
PINNED_FILL = {"PATCH_PVDD": 23.796, "PATCH_VBUS": 42.881,
               "PATCH_VBUS_IN": 14.036}


def _dry(*args):
    payload, _ = zone_patch.run(["--pcb", str(PCB), "--dry-run", *args])
    return payload


# ------------------------------------------------------------ no KiCad

def test_geom_reads_the_patch_names_and_outlines():
    bg = geom.BoardGeom.from_file(PCB)
    z = {z.name: z for z in bg.zones_of(layer="F.Cu")
         if z.name.startswith("PATCH_")}
    assert set(z) == set(PINNED_FILL)
    assert z["PATCH_PVDD"].outline == ((53.6, 165.8), (59.6, 165.8),
                                       (59.6, 171.2), (53.6, 171.2))
    assert len(z["PATCH_VBUS"].outline) == 6
    assert zone_patch.patches(bg, "PVDD", "F.Cu", [53, 165, 60, 172]) \
        == [z["PATCH_PVDD"]]
    assert zone_patch.patches(bg, "PVDD", "F.Cu", [53, 165, 58, 172]) == []
    assert zone_patch.patches(bg, "PVDD", "B.Cu", [53, 165, 60, 172]) == []


def test_dry_run_counts_the_vias_already_there():
    p = _dry("--net", "PVDD", "--region", PVDD_BOX, "--vias", "11")
    assert p["status"] == "pass"
    assert (p["replaced"], p["vias_had"], p["vias_added"]) == (1, 11, [])


def test_dry_run_too_many_vias_is_a_violation():
    p = _dry("--net", "PVDD", "--region", PVDD_BOX, "--vias", "40")
    assert p["status"] == "violations"
    assert p["violations"][0]["check"] == "zone_patch_vias"
    # the new ones keep the 0.5 mm hole-to-hole floor to every old via
    bg = geom.BoardGeom.from_file(PCB)
    holes = [(v.at, v.drill) for v in bg.vias_of()]
    for x, y in p["vias_added"]:
        for (hx, hy), d in holes:
            gap = ((x - hx) ** 2 + (y - hy) ** 2) ** .5 - d / 2 - 0.15
            assert gap >= 0.5 - 1e-6


def test_poly_sets_the_region_and_must_be_simple():
    p = _dry("--net", "VBUS", "--poly", VBUS_POLY)
    assert p["replaced"] == 1 and len(p["outline"]) == 6
    with pytest.raises(CheckError, match="not a simple polygon"):
        _dry("--net", "VBUS", "--poly", "50,174 58,181 58,174 50,181")


@pytest.mark.parametrize("args, msg", [
    (["--net", "NOPE", "--region", PVDD_BOX], "not on board"),
    (["--net", "PVDD", "--region", PVDD_BOX, "--layer", "F.SilkS"],
     "not a copper layer"),
    (["--net", "PVDD"], "give --region"),
    (["--net", "PVDD", "--region", "1,2,3"], "want 4 numbers"),
    (["--net", "PVDD", "--region", "0,0,1,1", "--remove"], "no PATCH_PVDD"),
    (["--net", "PVDD", "--region", PVDD_BOX, "--via-drill", "0.7"],
     "--via-drill"),
])
def test_bad_input(args, msg):
    with pytest.raises(CheckError, match=msg):
        _dry(*args)


def test_an_older_overlapping_patch_is_refused_not_kept():
    """#983: an older PATCH_PVDD that overlaps the new outline but reaches
    outside the box would stay and fail DRC on zones_intersect."""
    with pytest.raises(CheckError, match="overlaps the new outline"):
        _dry("--net", "PVDD", "--region", "58,165,62,172")
    with pytest.raises(CheckError, match="overlaps the new outline"):
        _dry("--net", "PVDD", "--poly", "58,166 62,166 62,171 58,171",
             "--region", "58,165,62,172")
    # a box that covers it replaces it; an outline that only abuts it is fine
    p = _dry("--net", "PVDD", "--region", "53,165,62,172")
    assert p["status"] == "pass" and p["replaced"] == 1
    p = _dry("--net", "PVDD", "--region", "59.6,165.8,62,171.2")
    assert p["replaced"] == 0


def test_a_violation_leaves_the_board_untouched(tmp_path, monkeypatch):
    """#983: a violations result used to swap the staged copy in. The KiCad
    steps are stubbed: the zone worker adds nothing, the vias go in as text
    and the refill is skipped, so the staged copy is the board plus the new
    vias, and the patch keeps its pinned fill."""
    import kc
    import routelib
    import route_edit

    def add_vias(board, ops):
        text = board.read_text().rstrip()
        assert text.endswith(")")
        vias = "".join(
            f'\t(via (at {o["at"][0]} {o["at"][1]}) (size {o["size"]}) '
            f'(drill {o["drill"]}) (layers "F.Cu" "B.Cu") '
            f'(net "{o["net"]}") (uuid "00000000-0000-0000-0000-{i:012d}"))\n'
            for i, o in enumerate(ops))
        board.write_text(text[:-1] + vias + ")\n")
    monkeypatch.setattr(route_edit, "apply_ops", add_vias)
    work = tmp_path / "kicad"
    shutil.copytree(CORE, work)
    pcb = work / "core.kicad_pcb"
    before = pcb.read_bytes()
    monkeypatch.setattr(env, "find_kicad_cli", lambda: Path("kicad-cli"))
    monkeypatch.setattr(env, "find_kicad_python", lambda cli: Path("python"))
    monkeypatch.setattr(routelib, "run_worker",
                        lambda *a, **k: {"removed": []})
    monkeypatch.setattr(kc, "run_drc", lambda *a, **k: {})
    p = _patch(pcb, "--net", "PVDD", "--region", PVDD_BOX, "--vias", "40")
    assert p["status"] == "violations" and p["vias_added"]
    assert p["written"] is False
    assert pcb.read_bytes() == before
    assert not list(work.glob(".aiee_patch_*"))


# ------------------------------------------------------------ live

@pytest.fixture
def kicad():
    cli = env.find_kicad_cli()
    if cli is None or env.find_kicad_python(cli) is None:
        pytest.skip("KiCad bundled python not installed")
    return cli


def _fills(pcb: Path) -> dict:
    bg = geom.BoardGeom.from_file(pcb)
    return {z.name: round(z.fill_area("F.Cu"), 3)
            for z in bg.zones_of(layer="F.Cu") if z.name.startswith("PATCH_")}


def _pvdd_vias(pcb: Path) -> list:
    from shapely.geometry import Point, box
    r = box(*map(float, PVDD_BOX.split(",")))
    return [v for v in geom.BoardGeom.from_file(pcb).vias_of(net="PVDD")
            if r.contains(Point(v.at))]


def _patch(pcb: Path, *args) -> dict:
    payload, _ = zone_patch.run(["--pcb", str(pcb), *args])
    return payload


def test_live_drop_and_rebuild_the_patches_on_a_copy_of_core(kicad, tmp_path):
    """drop_patch.py, then via_array.py's zone and patch_outline.py's
    polygon, redone with zone_patch."""
    import kc
    work = tmp_path / "kicad"
    shutil.copytree(CORE, work)
    pcb = work / "core.kicad_pcb"
    base = kc.run_drc(kicad, pcb)["counts"]
    assert _fills(pcb) == PINNED_FILL

    p = _patch(pcb, "--net", "PVDD", "--region", PVDD_BOX, "--remove")
    assert p["status"] == "pass" and p["zones_removed"] == 1
    assert "PATCH_PVDD" not in _fills(pcb)
    assert len(_pvdd_vias(pcb)) == 11            # the vias stay

    p = _patch(pcb, "--net", "PVDD", "--region", PVDD_BOX, "--vias", "11")
    assert p["status"] == "pass" and p["vias_added"] == []
    assert p["fill_mm2"] == pytest.approx(PINNED_FILL["PATCH_PVDD"], abs=0.05)

    # reshaping in place: the old PATCH_VBUS goes, the polygon comes back
    p = _patch(pcb, "--net", "VBUS", "--poly", VBUS_POLY)
    assert p["status"] == "pass" and p["zones_removed"] == 1
    fills = _fills(pcb)
    assert fills.keys() == PINNED_FILL.keys()
    for k, a in PINNED_FILL.items():
        assert fills[k] == pytest.approx(a, abs=0.05), k
    assert kc.run_drc(kicad, pcb)["counts"] == base


def test_live_via_array_on_a_copy_of_core(kicad, tmp_path):
    """via_array.py's vias: rip the PATCH_PVDD vias no track ends on, then
    let zone_patch put the count back. DRC must not move."""
    import kc
    import route_edit
    work = tmp_path / "kicad"
    shutil.copytree(CORE, work)
    pcb = work / "core.kicad_pcb"
    base = kc.run_drc(kicad, pcb)["counts"]
    bg = geom.BoardGeom.from_file(pcb)
    ends = {(round(x, 3), round(y, 3)) for t in bg.tracks_of(net="PVDD")
            for x, y in (t.shape.coords[0], t.shape.coords[-1])}
    loose = [v for v in _pvdd_vias(pcb)
             if (round(v.at[0], 3), round(v.at[1], 3)) not in ends]
    assert len(loose) >= 6
    route_edit.apply_ops(pcb, [{"op": "remove", "uuid": v.uuid}
                               for v in loose])
    assert len(_pvdd_vias(pcb)) == 11 - len(loose)

    p = _patch(pcb, "--net", "PVDD", "--region", PVDD_BOX, "--vias", "11")
    assert p["status"] == "pass", p
    assert len(p["vias_added"]) == len(loose)
    assert len(_pvdd_vias(pcb)) == 11
    assert p["fill_mm2"] == pytest.approx(PINNED_FILL["PATCH_PVDD"], abs=0.05)
    assert kc.run_drc(kicad, pcb)["counts"] == base
