"""check_via_stubs + via_backdrill + fab_export's back-drill (#478). Known
answers from the closed form on small synthetic boards; the live tests set
KiCad's back-drill and read its drill file back."""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import check_via_stubs  # noqa: E402
import constraints_lint  # noqa: E402
import geom  # noqa: E402
import gerblib  # noqa: E402
import via_backdrill  # noqa: E402
import viastub  # noqa: E402

LAYERS = """(layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal)
 (2 "B.Cu" signal) (5 "F.SilkS" user) (7 "B.SilkS" user) (1 "F.Mask" user)
 (3 "B.Mask" user) (25 "Edge.Cuts" user))"""
# 1.6 mm: 0.035 + 0.2 + 0.0175 + 1.095 + 0.0175 + 0.2 + 0.035, all er 4.3
STACK = """(setup (stackup
 (layer "F.Cu" (type "copper") (thickness 0.035))
 (layer "dielectric 1" (type "prepreg") (thickness 0.2) (epsilon_r 4.3))
 (layer "In1.Cu" (type "copper") (thickness 0.0175))
 (layer "dielectric 2" (type "core") (thickness 1.095) (epsilon_r 4.3))
 (layer "In2.Cu" (type "copper") (thickness 0.0175))
 (layer "dielectric 3" (type "prepreg") (thickness 0.2) (epsilon_r 4.3))
 (layer "B.Cu" (type "copper") (thickness 0.035))))"""
UUID = "78000000-0000-4000-8000-00000000000a"


def _seg(x0, x1, layer):
    return (f'(segment (start {x0} 10) (end {x1} 10) (width 0.2) '
            f'(layer "{layer}") (net "/SIG"))')


def _board(path: Path, second="In1.Cu", drill="") -> Path:
    """/SIG arrives on F.Cu, leaves on `second` through a through via at x=10."""
    path.write_text(
        f'(kicad_pcb (version 20260206) (generator "test") '
        f'(general (thickness 1.6)) {LAYERS} {STACK} (net 1 "/SIG") '
        f'(gr_rect (start 0 0) (end 20 20) (layer "Edge.Cuts") (stroke (width 0.1) (type solid))) '
        f'{_seg(2, 10, "F.Cu")} {_seg(10, 18, second)} '
        f'(via (at 10 10) (size 0.6) (drill 0.3) {drill} (layers "F.Cu" "B.Cu") '
        f'(net "/SIG") (uuid "{UUID}")))', encoding="utf-8")
    return path


def _run(tmp_path, cons, second="In1.Cu", drill=""):
    pcb = _board(tmp_path / "b.kicad_pcb", second, drill)
    return check_via_stubs.check(pcb, cons)


def _kinds(vios):
    return sorted(v["kind"] for v in vios)


def _hs(**kw):
    return {"high_speed": [{"net": "/SIG", "reference": "GND", **kw}]}


# ------------------------------------------------------------ closed form

@pytest.mark.parametrize("mm,ghz", [(1.4, 25.82), (1.2, 30.12), (3.0, 12.05)])
def test_issue_numbers_by_hand(mm, ghz):
    # 299.79 / (4 * mm * sqrt(4.3)): #478's ~25, ~30 and ~12 GHz hold
    assert viastub.f_res_ghz(mm, 4.3) == pytest.approx(ghz, abs=0.01)


def test_max_stub_inverts_f_res():
    assert viastub.f_res_ghz(viastub.max_stub_mm(20.0, 4.3), 4.3) == pytest.approx(20.0)


def test_knee_takes_the_largest_stated():
    assert viastub.knee_ghz(t_rise_ns=0.05) == pytest.approx(10.0)
    assert viastub.knee_ghz(data_rate_mbps=10000) == pytest.approx(10.0)
    assert viastub.knee_ghz(t_rise_ns=1.0, f_max_ghz=6) == pytest.approx(6.0)
    assert viastub.knee_ghz() is None


# ------------------------------------------------------------ the stub

def test_l1_l2_stub_on_1p6_fr4(tmp_path):
    _, facts = _run(tmp_path, _hs(t_rise_ns=1.0))
    assert facts["board_thickness_mm"] == pytest.approx(1.6)
    row = facts["vias"][0]
    assert row["used"] == ["F.Cu", "In1.Cu"]
    assert len(row["stubs"]) == 1
    s = row["stubs"][0]
    # In1's far face (0.2525) to the bottom face: 1.3475, the issue's "~1.4"
    assert s["length_mm"] == pytest.approx(1.3475)
    assert s["f_res_ghz"] == pytest.approx(26.82, abs=0.01)


@pytest.mark.parametrize("rise_ns,want", [
    (1.0, []),                                   # knee 0.5, ratio 53.6
    (0.05, ["via_stub_resonance"]),              # knee 10, ratio 2.68
    (0.02, ["via_stub_resonance"]),              # knee 25, ratio 1.07
])
def test_thresholds(tmp_path, rise_ns, want):
    vios, _ = _run(tmp_path, _hs(t_rise_ns=rise_ns))
    assert _kinds(vios) == want
    if want:
        assert vios[0]["severity"] == ("error" if rise_ns == 0.02 else "warning")
        assert "back-drill" in vios[0]["msg"] and "blind via" in vios[0]["msg"]


def test_data_rate_and_rise_ps_feed_the_knee(tmp_path):
    vios, _ = _run(tmp_path, _hs(data_rate_mbps=20000))
    assert vios[0]["ratio"] == pytest.approx(1.341, abs=0.001)
    pairs = {"diff_pairs": [{"p": "/SIG", "n": "/SIG_N", "rise_ps": 50}]}
    vios, _ = _run(tmp_path, pairs)
    assert vios[0]["knee_ghz"] == pytest.approx(10.0)


def test_moving_the_exit_layer_clears_it(tmp_path):
    cons = _hs(t_rise_ns=0.02)
    vios, facts = _run(tmp_path, cons, second="B.Cu")
    assert vios == [] and facts["vias"][0]["stubs"] == []
    vios, facts = _run(tmp_path, cons, second="In2.Cu")
    assert vios == []            # 0.235 mm: f_res 154 GHz, ratio 6.2
    assert facts["vias"][0]["stubs"][0]["length_mm"] == pytest.approx(0.235)


def test_the_fix_names_a_layer_that_clears(tmp_path):
    vios, _ = _run(tmp_path, _hs(t_rise_ns=0.02))
    assert "In2.Cu or B.Cu" in vios[0]["msg"]


def test_both_sides_of_an_inner_transition(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(_board(pcb, "In2.Cu").read_text().replace(
        _seg(2, 10, "F.Cu"), _seg(2, 10, "In1.Cu")))
    _, facts = check_via_stubs.check(pcb, _hs(t_rise_ns=1.0))
    assert [(s["side"], s["length_mm"]) for s in facts["vias"][0]["stubs"]] == [
        ("F", 0.235), ("B", 0.235)]


def test_a_net_with_no_edge_is_measured_not_judged(tmp_path):
    vios, facts = _run(tmp_path, {"high_speed": [{"net": "/SIG", "reference": "GND"}]})
    assert vios == [] and facts["no_edge"] == ["/SIG"]
    assert "/SIG" in facts["not_judged"][0] and facts["nothing_to_judge"] is None
    assert facts["vias"][0]["stubs"][0]["f_res_ghz"] == pytest.approx(26.82, abs=0.01)


def test_undeclared_net_and_no_constraints_stay_silent(tmp_path):
    vios, facts = _run(tmp_path, {})
    assert vios == [] and facts["vias"] == []
    assert "in constraints" in facts["nothing_to_judge"]


def test_one_layer_via_is_not_a_stub(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(_board(pcb).read_text().replace(_seg(10, 18, "In1.Cu"), ""))
    vios, facts = check_via_stubs.check(pcb, _hs(t_rise_ns=0.02))
    assert vios == [] and facts["single_layer_vias"] == 1
    assert facts["nothing_to_judge"] == "no via on a declared fast net"


def test_planted_fault_flags_and_shortening_the_stub_clears_it(tmp_path):
    """The usbbuck4 mutant: /MCO's via leaves In1 -> B.Cu hanging. Cutting that
    barrel back to a blind F.Cu -> In1 via clears it, on the same constraints.
    (Moving the run to In2 would not: it shortens this stub but lengthens the
    one on the via that takes the run back down to B.Cu.)"""
    d = ROOT / "tests" / "golden" / "mutants" / "via-stub-long"
    cons = json.loads((d / "constraints.json").read_text())
    text = (d / "usbbuck4.kicad_pcb").read_text()
    pcb = tmp_path / "usbbuck4.kicad_pcb"
    pcb.write_text(text)
    vios, _ = check_via_stubs.check(pcb, cons)
    assert [(v["kind"], v["severity"], v["pos"]) for v in vios] == [
        ("via_stub_resonance", "error", [141.0, 123.0])]
    old = ('(via\n\t\t(at 141 123)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
           '\t\t(layers "F.Cu" "B.Cu")')
    assert text.count(old) == 1
    pcb.write_text(text.replace(old, old.replace("(via\n", "(via blind\n")
                                .replace('"B.Cu"', '"In1.Cu"')))
    geom.load_board.cache_clear() if hasattr(geom.load_board, "cache_clear") else None
    vios, facts = check_via_stubs.check(pcb, cons)
    assert vios == []
    assert max(s["length_mm"] for x in facts["vias"] for s in x["stubs"]) < 0.3


# ------------------------------------------------------------ back-drill judged

def _drill(end, start="B.Cu", tok="tertiary_drill", size=0.5):
    return f'({tok} (size {size}) (layers "{start}" "{end}"))'


def _bd(**kw):
    return {**_hs(t_rise_ns=0.02), "backdrill": [{"net": "/SIG", **kw}]}


def test_backdrilled_stub_and_barrel(tmp_path):
    vios, facts = _run(tmp_path, _bd(depth_tol_mm=0.05, vendor="pcbway"),
                       drill=_drill("In2.Cu"))
    s = facts["vias"][0]["stubs"][0]
    # depth: bottom face to In2's middle = 0.035 + 0.2 + 0.00875
    assert s["depth_mm"] == pytest.approx(0.24375, abs=1e-4)
    assert s["barrel_left_mm"] == pytest.approx(1.6 - s["depth_mm"], abs=1e-4)
    # the barrel left is measured from the top face; the STUB is what hangs
    # past In1: barrel left - In1's far face (0.2525)
    assert s["length_mm"] == pytest.approx(s["barrel_left_mm"] - 0.2525, abs=1e-4)
    assert s["worst_mm"] == pytest.approx(s["length_mm"] + 0.05, abs=1e-4)
    # 1.15 mm: f_res 31.4 GHz, 1.26 x knee 25: drilling helped, not enough
    assert _kinds(vios) == ["via_stub_resonance"]
    assert vios[0]["severity"] == "error"


def test_backdrill_through_a_used_layer(tmp_path):
    vios, _ = _run(tmp_path, _bd(vendor="pcbway"), second="In2.Cu",
                   drill=_drill("In1.Cu"))
    assert "backdrill_cuts_net" in _kinds(vios)


def test_backdrill_inside_the_tolerance_can_reach_the_signal(tmp_path):
    vios, _ = _run(tmp_path, {**_hs(t_rise_ns=1.0), "backdrill": [
        {"net": "/SIG", "vendor": "pcbway", "depth_tol_mm": 2.0}]},
        drill=_drill("In2.Cu"))
    assert "backdrill_too_deep" in _kinds(vios)     # 1.1 mm left under 2.0


def test_listed_net_with_no_drill_is_missing(tmp_path):
    vios, _ = _run(tmp_path, _bd(vendor="pcbway"))
    miss = [v for v in vios if v["kind"] == "backdrill_missing"]
    assert [(v["side"], v["end"]) for v in miss] == [("B", "In2.Cu")]


def test_jlc_refuses_and_unknown_warns(tmp_path):
    vios, _ = _run(tmp_path, _bd(vendor="jlcpcb"), drill=_drill("In2.Cu"))
    assert [v["severity"] for v in vios if v["kind"] == "backdrill_not_offered"] == ["error"]
    vios, _ = _run(tmp_path, _bd(vendor="eurocircuits"), drill=_drill("In2.Cu"))
    assert [v["severity"] for v in vios if v["kind"] == "backdrill_fab_unknown"] == ["warning"]
    vios, _ = _run(tmp_path, _bd(), drill=_drill("In2.Cu"))   # stack is no listed build
    assert "backdrill_fab_unknown" in _kinds(vios)


def test_a_drill_nobody_listed_is_still_judged(tmp_path):
    vios, facts = _run(tmp_path, {}, second="In2.Cu", drill=_drill("In1.Cu"))
    assert "backdrill_cuts_net" in _kinds(vios)
    assert facts["vias"]


def test_stub_limit(tmp_path):
    vios, _ = _run(tmp_path, {**_hs(t_rise_ns=1.0), "backdrill": [
        {"net": "/SIG", "vendor": "pcbway", "max_stub_mm": 0.4, "depth_tol_mm": 0.1}]},
        drill=_drill("In2.Cu"))
    assert _kinds(vios) == ["backdrill_stub_long"]


def test_top_side_drill_read_by_its_start_layer(tmp_path):
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text(_board(pcb, "B.Cu", _drill("In1.Cu", "F.Cu", "secondary_drill"))
                   .read_text().replace(_seg(2, 10, "F.Cu"), _seg(2, 10, "In2.Cu")))
    _, facts = check_via_stubs.check(pcb, _hs(t_rise_ns=1.0))
    s = facts["vias"][0]["stubs"][0]
    assert s["side"] == "F" and s["exit"] == "In2.Cu"
    # In1's middle to In2's near face
    assert s["length_mm"] == pytest.approx(0.00875 + 1.095, abs=1e-4)


# ------------------------------------------------------------ the plan

def test_plan_stops_past_the_exit_by_the_tolerance(tmp_path):
    pcb = _board(tmp_path / "b.kicad_pcb")
    p = via_backdrill.plan(pcb, {"backdrill": [{"net": "/SIG", "vendor": "pcbway"}]})
    assert [(d["side"], d["end"], d["uuid"]) for d in p["drills"]] == [("B", "In2.Cu", UUID)]
    assert p["drills"][0]["size_mm"] == pytest.approx(0.45)   # 0.3 + PCBWay's 0.15
    assert p["not_drillable"] == []


def test_plan_lists_a_side_no_layer_can_serve(tmp_path):
    pcb = _board(tmp_path / "b.kicad_pcb", "In2.Cu")
    p = via_backdrill.plan(pcb, {"backdrill": [{"net": "/SIG", "vendor": "pcbway"}]})
    assert p["drills"] == []
    assert [s["side"] for s in p["not_drillable"]] == ["B"]


def test_plan_skips_a_side_already_drilled(tmp_path):
    pcb = _board(tmp_path / "b.kicad_pcb", drill=_drill("In2.Cu"))
    p = via_backdrill.plan(pcb, {"backdrill": [{"net": "/SIG", "vendor": "pcbway"}]})
    assert p["drills"] == []


def test_lint_knows_the_new_keys(tmp_path):
    f = tmp_path / "constraints.json"
    f.write_text(json.dumps({
        "backdrill": [{"net": "/SIG", "drill_mm": 0.5, "depth_tol_mm": 0.1,
                       "max_stub_mm": 0.4, "vendor": "pcbway"}],
        "high_speed": [{"net": "/SIG", "reference": "GND", "data_rate_mbps": 10000}]}))
    findings, _ = constraints_lint.lint_file(f)
    assert findings == []


def test_gerblib_keeps_a_backdrill_out_of_the_holes(tmp_path):
    (tmp_path / "b.drl").write_text(
        "M48\n; #@! TF.FileFunction,MixedPlating,1,4\nMETRIC\n"
        "; #@! TA.AperFunction,Plated,PTH,ViaDrill\nT1C0.300\n%\nG90\nT1\nX1.0Y-1.0\nM30\n")
    (tmp_path / "b-in2-back-backdrill.drl").write_text(
        "M48\n; #@! TF.FileFunction,NonPlated,3,4,Blind\nMETRIC\n"
        "; #@! TA.AperFunction,NonPlated,BackDrill\nT1C0.500\n%\nG90\nT1\nX1.0Y-1.0\nM30\n")
    fab = gerblib.open_fab(tmp_path)
    assert [p.name for p in fab.drill_files] == ["b.drl"]
    assert [p.name for p in fab.backdrill_files] == ["b-in2-back-backdrill.drl"]
    assert [h.diameter for h in fab.holes] == [0.3]


# ------------------------------------------------------------ live: KiCad

def _kicad_cli():
    from lib import env
    return env.find_kicad_cli()


@pytest.mark.skipif(_kicad_cli() is None, reason="needs kicad-cli")
def test_live_backdrill_round_trip(tmp_path):
    """via_backdrill sets it in KiCad; KiCad's drill file holds the tool; the
    barrel left = board thickness - depth; the zip ships one file and a note."""
    import fab_export
    pcb = _board(tmp_path / "b.kicad_pcb")
    (tmp_path / "c.json").write_text(json.dumps(
        {"backdrill": [{"net": "/SIG", "vendor": "pcbway", "drill_mm": 0.5}]}))
    rep, _ = via_backdrill.run(["--pcb", str(pcb), "--constraints",
                                str(tmp_path / "c.json"), "--apply"])
    assert rep["set"] == 1
    assert '"B.Cu" "In2.Cu"' in re.sub(r"\s+", " ", pcb.read_text())
    again = via_backdrill.plan(pcb, {"backdrill": [{"net": "/SIG", "vendor": "pcbway"}]})
    assert again["drills"] == []           # idempotent

    man = fab_export.run(pcb, tmp_path / "fab")
    gd = tmp_path / "fab" / "gerbers"
    assert len(man["backdrill"]["files"]) == 1
    drl = gd / man["backdrill"]["files"][0]
    text = drl.read_text()
    assert "T1C0.500" in text and "X10.0Y-10.0" in text
    kicad_depth = float(re.search(r"Backdrill stub ([\d.]+)mm", text).group(1))
    _, facts = check_via_stubs.check(pcb, _hs(t_rise_ns=1.0))
    s = facts["vias"][0]["stubs"][0]
    assert s["depth_mm"] == pytest.approx(kicad_depth, abs=0.001)
    assert s["barrel_left_mm"] == pytest.approx(facts["board_thickness_mm"] - kicad_depth,
                                                abs=0.001)
    names = zipfile.ZipFile(man["gerber_zip"]).namelist()
    assert drl.name in names and man["backdrill"]["note"] in names
    assert not [n for n in names if "_Backdrills_" in n]
    note = (gd / man["backdrill"]["note"]).read_text()
    assert "| B.Cu | In2.Cu | In1.Cu | 0.500 | 1 |" in note
    # gone from the next export once the board has none
    pcb.write_text(re.sub(r"\(tertiary_drill.*?\)\s*\)\s*", "", pcb.read_text(), flags=re.S))
    man = fab_export.run(pcb, tmp_path / "fab")
    assert "backdrill" not in man and not list(gd.glob("*backdrill*"))
