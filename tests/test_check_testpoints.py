"""check_testpoints: test-point access, flying-probe / fixture classification,
coverage and the Tag-Connect keep-out (#470). Known answers on a small
fixture board; the slow test cross-checks every pad's and via's side and
position against kicad-cli's IPC-D-356 records on pinned lite-main."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import check_testpoints  # noqa: E402

LAYERS = """(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (9 "F.Adhes" user)
 (11 "B.Adhes" user) (13 "F.Paste" user) (15 "B.Paste" user)
 (5 "F.SilkS" user) (7 "B.SilkS" user) (1 "F.Mask" user) (3 "B.Mask" user)
 (25 "Edge.Cuts" user) (35 "F.CrtYd" user) (37 "F.Fab" user))"""
SETUP = ("(setup (pad_to_mask_clearance 0) (tenting (front yes) (back yes)))")
NETS = ["N1", "N2", "N3", "+3V3", "N5", "N6", "N7", "N8", "N9", "N10"]


def _tp(ref, x, y, d, net, mask=True, layer="F") -> str:
    lay = f'"{layer}.Cu"' + (f' "{layer}.Mask"' if mask else "")
    return (f'(footprint "TestPoint:TestPoint_Pad_D{d}mm" (layer "{layer}.Cu")'
            f' (at {x} {y})\n (property "Reference" "{ref}" (at 0 -1.5 0) '
            f'(layer "F.SilkS"))\n (attr exclude_from_pos_files)\n'
            f' (pad "1" smd circle (at 0 0) (size {d} {d}) (layers {lay}) '
            f'(net "{net}")))\n')


def _r0603(ref, x, y, n1, n2, h=None) -> str:
    """0603 with its F.Fab body 1.6 x 0.8 mm; pads 0.8 x 0.95 at +-0.775."""
    pads = "\n ".join(
        f'(pad "{n}" smd roundrect (at {dx} 0) (size 0.8 0.95) '
        f'(roundrect_rratio 0.25) (layers "F.Cu" "F.Paste" "F.Mask") '
        f'(net "{net}"))'
        for n, dx, net in (("1", -0.775, n1), ("2", 0.775, n2)))
    name = "R_0603_1608Metric" + (f"_H{h}" if h else "")
    return (f'(footprint "Resistor_SMD:{name}" (layer "F.Cu") '
            f'(at {x} {y})\n (property "Reference" "{ref}" (at 0 -1.5 0) '
            f'(layer "F.SilkS"))\n'
            f' (fp_rect (start -0.8 -0.4) (end 0.8 0.4) (layer "F.Fab"))\n'
            f' {pads})\n')


def _via(x, y, net, size=0.6, drill=0.3, tent=None) -> str:
    t = f"(tenting (front {tent}) (back yes))" if tent else ""
    return (f'(via (at {x} {y}) (size {size}) (drill {drill}) '
            f'(layers "F.Cu" "B.Cu") {t} (net "{net}"))\n')


def _tc2030(x, y, paste=False, keepout=True) -> str:
    """Tag-Connect TC2030-IDC-NL as KiCad's stock footprint: six 0.7874 mm
    contacts at 1.27 mm, three 0.99 mm NPTH, a vias-not-allowed rule area
    on the contact-pad centres."""
    lay = '"F.Cu" "F.Mask"' + (' "F.Paste"' if paste else "")
    pads = []
    for i, (px, py) in enumerate([(-1.27, 0.635), (-1.27, -0.635),
                                  (0, 0.635), (0, -0.635), (1.27, 0.635),
                                  (1.27, -0.635)], 1):
        pads.append(f'(pad "{i}" connect circle (at {px} {py}) '
                    f'(size 0.7874 0.7874) (layers {lay}) (net "N{i}"))')
    for hx, hy in ((-2.54, 0), (2.54, -1.016), (2.54, 1.016)):
        pads.append(f'(pad "" np_thru_hole circle (at {hx} {hy}) '
                    f'(size 0.9906 0.9906) (drill 0.9906) '
                    f'(layers "*.Cu" "*.Mask"))')
    ko = ('(zone (layer "F.Cu") (hatch full 0.508) (keepout (tracks allowed)'
          ' (vias not_allowed) (pads allowed) (copperpour not_allowed)'
          ' (footprints not_allowed)) (polygon (pts (xy 1.27 -0.635)'
          ' (xy -1.27 -0.635) (xy -1.27 0.635) (xy 1.27 0.635))))'
          if keepout else "")
    return ('(footprint "Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_'
            f'Vertical" (layer "F.Cu") (at {x} {y})\n (property "Reference" '
            '"J9" (at 0 2.7 0) (layer "F.SilkS"))\n (attr '
            'exclude_from_pos_files exclude_from_bom)\n ' + "\n ".join(pads)
            + f"\n {ko})\n")


def _board(tmp_path: Path, *items: str, name="tp") -> Path:
    pcb = tmp_path / f"{name}.kicad_pcb"
    nets = " ".join(f'(net {i} "{n}")' for i, n in enumerate(NETS, 1))
    pcb.write_text(
        "(kicad_pcb (version 20241229) (generator \"pcbnew\")\n"
        f"(general (thickness 1.6)) (paper \"A4\")\n{LAYERS}\n{SETUP}\n"
        f'(net 0 "") {nets}\n'
        '(gr_rect (start 0 0) (end 40 30) (stroke (width 0.1) (type solid)) '
        '(fill none) (layer "Edge.Cuts"))\n' + "".join(items) + ")\n",
        encoding="utf-8")
    return pcb


def _fixture(tmp_path: Path, *extra: str) -> Path:
    return _board(
        tmp_path,
        _tp("TP1", 10, 10, 0.6, "N1"),          # flying only (small)
        _tp("TP2", 20, 10, 0.9, "N2"),          # fixture ok
        _tp("TP3", 2, 15, 0.9, "N3"),           # 2 mm from the left edge
        _tp("TP4", 30, 5, 1.0, "N6", mask=False),  # tented: unusable
        _tp("TP5", 20, 25, 1.0, "N8"),          # 1.0 mm from TP6: pitch
        _tp("TP6", 21, 25, 1.0, "N9"),
        _tp("TP7", 10, 21.5, 1.0, "N10"),       # 0.6 mm from R1 body
        _tp("TP8", 15, 15, 1.0, "N5", layer="B"),  # bottom side
        _r0603("R1", 10, 20, "N1", "N2"),
        _r0603("R2", 30, 20, "N3", "+3V3"),
        _r0603("R3", 30, 25, "+3V3", "N7"),
        _via(30, 10, "+3V3"),                   # tented by the board
        _via(25, 15, "N5", tent="no"),          # untented 0.6/0.3
        *extra)


def _pts(seen: dict, kind=None) -> dict:
    return {(p["ref"], p["pad"], p["side"]): p for p in seen["points"]
            if kind is None or p["kind"] == kind}


def test_known_answer_classification(tmp_path):
    vios, seen = check_testpoints.check(_fixture(tmp_path), all_points=True)
    tp = _pts(seen, "tp")
    assert tp[("TP1", "1", "top")]["size_mm"] == pytest.approx(0.6, abs=0.01)
    assert tp[("TP1", "1", "top")]["flying_ok"] is True
    assert tp[("TP1", "1", "top")]["fixture_fails"] == ["small"]
    assert tp[("TP2", "1", "top")]["size_mm"] == pytest.approx(0.9, abs=0.01)
    assert tp[("TP2", "1", "top")]["fixture_ok"] is True
    assert tp[("TP3", "1", "top")]["fixture_fails"] == ["edge"]
    assert tp[("TP3", "1", "top")]["edge_mm"] == pytest.approx(2.0, abs=0.01)
    assert tp[("TP3", "1", "top")]["flying_ok"] is True
    assert tp[("TP4", "1", "top")]["flying_ok"] is False
    assert tp[("TP4", "1", "top")]["blocked"] == "tented"
    for r in ("TP5", "TP6"):
        assert tp[(r, "1", "top")]["fixture_fails"] == ["pitch"]
        assert tp[(r, "1", "top")]["pitch_mm"] == pytest.approx(1.0, abs=1e-3)
    t7 = tp[("TP7", "1", "top")]
    assert t7["fixture_fails"] == ["body"] and t7["body_ref"] == "R1"
    # land circle edge at y 21.0, R1's body ends at 20.4
    assert t7["body_mm"] == pytest.approx(0.6, abs=0.01)
    assert tp[("TP8", "1", "bottom")]["fixture_ok"] is True
    vias = [p for p in seen["points"] if p["kind"] == "via"]
    # the tented +3V3 via is no access point; the untented N5 one is,
    # flying only (0.6 mm < 0.8)
    assert [(v["net"], v["side"]) for v in vias] == [("N5", "top")]
    assert vias[0]["flying_ok"] and vias[0]["fixture_fails"] == ["small"]
    assert seen["access_points"]["vias_tented"] == 3   # +3V3 F+B, N5 B
    # an 0603 toe reaches 0.375 mm past the body: no probe target
    r2 = _pts(seen)[("R2", "2", "top")]
    assert r2["size_mm"] == pytest.approx(0.375, abs=0.01)
    assert r2["flying_ok"] is False
    # +3V3: tented via and an 0603 toe only -> the rail is blind
    assert seen["power_nets_no_access"] == ["+3V3"]
    kinds = sorted(v["kind"] for v in vios)
    assert kinds == ["tp_rail_no_access", "tp_unusable"]
    # coverage over nets with 2+ pads: N1 (TP1 + R1.1), N2, N3, +3V3
    assert seen["nets"] == 4
    assert seen["coverage_pct"]["flying_top"] == 75.0
    assert seen["coverage_pct"]["fixture_top"] == 25.0       # N2 only
    assert seen["nets_no_access"] == ["+3V3"]


def test_fixture_rule_findings_only_when_asked(tmp_path):
    pcb = _fixture(tmp_path)
    vios, _ = check_testpoints.check(
        pcb, {"testpoints": {"fixture": True, "min_coverage_pct": 90}})
    fx = sorted(v["refs"][0] for v in vios if v["kind"] == "tp_fixture")
    assert fx == ["TP1", "TP3", "TP5", "TP6", "TP7"]
    assert [v["kind"] for v in vios].count("tp_coverage_low") == 1
    # scoped to the TP nets: every one has a flying-ok point but TP4's
    _, seen = check_testpoints.check(
        pcb, {"testpoints": {"nets": ["N1", "N2", "N6"]}})
    assert seen["nets_no_access"] == ["N6"]


def test_tall_part_widens_the_body_rule(tmp_path):
    # a 5 mm part 1.2 mm away: 0.3 x H = 1.5 mm > 1 mm
    pcb = _board(tmp_path, _tp("TP1", 10, 22.1, 1.0, "N1"),
                 _r0603("R1", 10, 20, "N1", "N2", h=5))
    _, seen = check_testpoints.check(pcb, all_points=True)
    p = _pts(seen, "tp")[("TP1", "1", "top")]
    assert p["fixture_fails"] == ["body"]
    assert p["body_mm"] == pytest.approx(1.2, abs=0.01)
    pcb = _board(tmp_path, _tp("TP1", 10, 22.1, 1.0, "N1"),
                 _r0603("R1", 10, 20, "N1", "N2"), name="low")
    _, seen = check_testpoints.check(pcb, all_points=True)
    assert _pts(seen, "tp")[("TP1", "1", "top")]["fixture_ok"] is True


def test_tag_connect_keepout_and_paste(tmp_path):
    clean = _board(tmp_path, _tc2030(20, 15), name="tc_clean")
    vios, seen = check_testpoints.check(clean)
    assert vios == []
    assert seen["tag_connect"][0]["keepout"] == "footprint"
    bad = _board(
        tmp_path, _tc2030(20, 15, paste=True),
        _via(20.6, 15, "N5"),
        '(segment (start 18 15) (end 22 15) (width 0.2) (layer "F.Cu") '
        '(net "N7"))\n', name="tc_bad")
    vios, seen = check_testpoints.check(bad)
    kinds = sorted(v["kind"] for v in vios)
    assert kinds == ["tc_keepout_track", "tc_keepout_via", "tc_paste"]
    paste = next(v for v in vios if v["kind"] == "tc_paste")
    assert paste["pads"] == ["1", "2", "3", "4", "5", "6"]
    # no rule area in the footprint: the pad-centre rectangle stands in
    nok = _board(tmp_path, _tc2030(20, 15, keepout=False),
                 _via(20.6, 15, "N5"), name="tc_noko")
    vios, seen = check_testpoints.check(nok)
    assert [v["kind"] for v in vios] == ["tc_keepout_via"]
    assert seen["tag_connect"][0]["keepout"] == "pad_centres"
    # the contacts are not access points of their own
    assert all(p["ref"] != "J9" for p in seen["testpoints"])


def test_cli_report(tmp_path):
    pcb = _fixture(tmp_path)
    out = tmp_path / "r.json"
    rc = check_testpoints.main(["--pcb", str(pcb), "--out", str(out)])
    assert rc == 1
    rep = json.loads(out.read_text())
    assert rep["counts"]["by_source"] == {"check_testpoints": 2}
    assert "points" not in rep["checked"][0]


def test_access_side_and_position_match_kicad_ipcd356(tmp_path):
    """Every pad and via the check reads sits where kicad-cli's IPC-D-356
    says, on the side it says (A01 top, A<n> bottom, A00 both): the check's
    board model and the bare-board tester's netlist agree."""
    import ipcd356
    import kc
    src = ROOT / "pinned" / "lite-main" / "kicad"
    work = tmp_path / "kicad"
    shutil.copytree(src, work)
    pcb = work / "lite-main.kicad_pcb"
    ipc = tmp_path / "lite-main.ipc"
    res = kc.export_ipcd356(kc.resolve_cli(), pcb, ipc)
    assert ipc.is_file(), res
    d = ipcd356.parse(ipc.read_text(encoding="utf-8", errors="replace"))
    import geom
    ipcd356.resolve(d, geom.BoardGeom.from_file(pcb))
    import sexpdata
    import check_wicking
    root = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    dflt = check_wicking.board_defaults(root)
    fps = check_testpoints.read_board(root, dflt, pcb)
    ours: dict[str, list] = {}
    for f in fps:
        for p in f["pads"]:
            ours.setdefault(f["ref"], []).append(p)
    vias = check_wicking.read_vias(root, dflt, ["F.Cu", "B.Cu"])
    checked = {"pads": 0, "vias": 0}
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    via_tree = STRtree([Point(v["at"]) for v in vias])
    for tp in d.points:
        if tp.code == "367":
            continue
        side = ({"F", "B"} if tp.access == 0 else {"F"} if tp.access == 1
                else {"B"})
        if tp.ref == "VIA":
            v = vias[int(via_tree.nearest(Point(tp.x, tp.y)))]
            assert abs(v["at"][0] - tp.x) < 0.01 \
                and abs(v["at"][1] - tp.y) < 0.01, (tp.x, tp.y, v["at"])
            assert v["sides"] >= side
            checked["vias"] += 1
            continue
        p = next((p for p in ours.get(tp.ref, ())
                  if p["number"].startswith(tp.pin)
                  and abs(p["center"][0] - tp.x) < 0.01
                  and abs(p["center"][1] - tp.y) < 0.01), None)
        assert p is not None, (tp.ref, tp.pin, tp.x, tp.y)
        assert p["cu"] == side, (tp.ref, tp.pin, p["cu"], tp.access)
        assert (p["net"] or None) == (tp.board_net or None), \
            (tp.ref, tp.pin, p["net"], tp.board_net)
        checked["pads"] += 1
    assert checked["pads"] > 300 and checked["vias"] > 100, checked
    # every access point the check reports is a tester record on its side,
    # with its net
    _, seen = check_testpoints.check(pcb, all_points=True)
    recs = [tp for tp in d.points if tp.code != "367"]
    tree = STRtree([Point(tp.x, tp.y) for tp in recs])
    for pt in seen["points"]:
        s = "F" if pt["side"] == "top" else "B"
        hits = [recs[int(i)] for i in tree.query(
            Point(*pt["at"]).buffer(0.01))]
        hits = [tp for tp in hits if tp.access == 0
                or (tp.access == 1) == (s == "F")]
        assert hits, pt
        assert any(tp.board_net == pt["net"] for tp in hits), (pt, hits)
    assert len(seen["points"]) > 300
    for f in work.glob("*.kicad_prl"):
        f.unlink()
