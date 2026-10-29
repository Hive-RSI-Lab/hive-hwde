"""T2 regression tests: check_creepage blind-spot fixes.

Locks the three T2 fixes against the LEARNINGS 2026-07-29 lumina-carrier
geometry:
  - voltage_pairs: an explicit net-PAIR differential (bridge/AC input) that
    per-net voltages cannot express (the 0.33 mm 57 V gap that passed silently)
  - all-pairs item-level enumeration (worst-pair-only hid 216 siblings)
  - full IPC-2221 Table 6-1 row model with per-item-type coating adjudication
    (B2/B4/A5 for tracks-vias-zones, A6/A7 for exposed lands, B1 internal)
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_creepage  # noqa: E402
import geom  # noqa: E402

POE_A1 = "/poe/POE_TAP_A1"
POE_A2 = "/poe/POE_TAP_A2"


# ---- synthetic board helpers (mirror test_checks.py; outline overridable) --

def _board(tmp_path_factory, name: str, body: str,
           outline=(0, 0, 20, 10)) -> geom.BoardGeom:
    x0, y0, x1, y1 = outline
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start {x0} {y0}) (end {x1} {y1}) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path_factory.mktemp(name) / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return geom.load_board(p)


def _cons(tmp_path, obj: dict) -> Path:
    p = tmp_path / "constraints.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _run(bg: geom.BoardGeom, cons: Path, *extra):
    payload, _ = check_creepage.run(
        ["--pcb", str(bg.path), "--constraints", str(cons), *extra])
    return payload


# ---- Fixture A: exact LEARNINGS 2026-07-29 PoE tap geometry -----------------
# Two transition vias 0.9295 mm centre-to-centre, 0.6 mm pads -> 0.3295 mm
# copper gap on both layers; both nets at 57 V so dv = 0 for the derived sweep.

_POE_BODY = (
    f'  (via (at 49.650 74.213) (size 0.6) (drill 0.3) '
    f'(layers "F.Cu" "B.Cu") (net "{POE_A1}"))\n'
    f'  (via (at 49.200 73.400) (size 0.6) (drill 0.3) '
    f'(layers "F.Cu" "B.Cu") (net "{POE_A2}"))\n')
_POE_VOLTAGES = [{"net": POE_A1, "voltage": 57},
                 {"net": POE_A2, "voltage": 57}]


def _poe_board(tmp_path_factory, name):
    return _board(tmp_path_factory, name, _POE_BODY, outline=(40, 68, 60, 80))


def test_equal_voltage_pair_not_derived(tmp_path_factory, tmp_path):
    """Negative control (the old blindness, now documented): two 57 V nets
    have dv = 0, so the voltages-derived sweep alone reports nothing."""
    bg = _poe_board(tmp_path_factory, "poe_neg")
    payload = _run(bg, _cons(tmp_path, {"voltages": _POE_VOLTAGES}))
    assert payload["status"] == "pass"
    assert payload["violations"] == []


def test_voltage_pairs_flags_bridge_input(tmp_path_factory, tmp_path):
    """The explicit 114 V pair fires on both layers at the real gap location."""
    bg = _poe_board(tmp_path_factory, "poe_pair")
    payload = _run(bg, _cons(tmp_path, {
        "voltages": _POE_VOLTAGES,
        "voltage_pairs": [{"a": POE_A1, "b": POE_A2, "voltage": 114}]}))
    assert payload["status"] == "violations"
    vs = payload["violations"]
    assert len(vs) == 2                       # one per layer, same via pair
    assert {v["layer"] for v in vs} == {"F.Cu", "B.Cu"}
    for v in vs:
        assert v["kind"] == "creepage"
        assert v["net"] == POE_A1 and v["other_net"] == POE_A2
        assert v["delta_v"] == 114
        # 114 V -> 101-150 band; uncoated outer vias -> B2 -> 0.60 mm
        assert v["required_mm"] == 0.60
        assert v["spacing_mm"] == pytest.approx(0.3295, abs=0.005)
        # pos is the actual gap midpoint, not representative_point()
        assert math.hypot(v["pos"][0] - 49.425, v["pos"][1] - 73.8) < 0.5
    fcu = [v for v in vs if v["layer"] == "F.Cu"][0]
    assert fcu["rows"] == ["B2", "B2"]


def test_voltage_pairs_absent_net_skipped(tmp_path_factory, tmp_path):
    bg = _poe_board(tmp_path_factory, "poe_ghost")
    payload = _run(bg, _cons(tmp_path, {
        "voltages": _POE_VOLTAGES,
        "voltage_pairs": [{"a": "/GHOST", "b": POE_A1, "voltage": 114}]}))
    assert payload["status"] == "pass"        # skipped, not exit-2
    assert "/GHOST" in payload["skipped_absent_nets"]


def test_voltage_pairs_below_30v_recorded(tmp_path_factory, tmp_path):
    bg = _poe_board(tmp_path_factory, "poe_lv")
    payload = _run(bg, _cons(tmp_path, {
        "voltages": _POE_VOLTAGES,
        "voltage_pairs": [{"a": POE_A1, "b": POE_A2, "voltage": 24}]}))
    assert payload["status"] == "pass"
    assert payload["skipped_low_voltage_pairs"] == [
        {"a": POE_A1, "b": POE_A2, "voltage": 24}]


def test_exit_codes_via_main(tmp_path_factory, tmp_path):
    bg = _poe_board(tmp_path_factory, "poe_exit")
    clean = _cons(tmp_path, {"voltages": _POE_VOLTAGES})
    out = tmp_path / "r.json"
    assert check_creepage.main(["--pcb", str(bg.path), "--constraints",
                                str(clean), "--out", str(out)]) == 0
    hot = tmp_path / "hot.json"
    hot.write_text(json.dumps({
        "voltages": _POE_VOLTAGES,
        "voltage_pairs": [{"a": POE_A1, "b": POE_A2, "voltage": 114}]}),
        encoding="utf-8")
    assert check_creepage.main(["--pcb", str(bg.path), "--constraints",
                                str(hot), "--out", str(out)]) == 1


# ---- Fixture B: sibling reporting (worst-pair-only hid 216 siblings) --------

def test_all_violating_pairs_reported(tmp_path_factory, tmp_path):
    """Three separated 0.30 mm gaps on one net pair -> 3 violations (old: 1)."""
    seg = ('  (segment (start {0} {1}) (end {2} {1}) (width 0.5) '
           '(layer "F.Cu") (net "{3}"))\n')
    body = (seg.format(2, 2, 8, "HV") + seg.format(2, 2.8, 8, "GND") +
            seg.format(12, 2, 18, "HV") + seg.format(12, 2.8, 18, "GND") +
            seg.format(2, 8, 8, "HV") + seg.format(2, 8.8, 8, "GND"))
    bg = _board(tmp_path_factory, "siblings", body)
    payload = _run(bg, _cons(tmp_path, {
        "voltages": [{"net": "HV", "voltage": 100}]}))
    assert payload["status"] == "violations"
    vs = payload["violations"]
    assert len(vs) == 3, json.dumps(vs)
    for v in vs:
        assert v["required_mm"] == 0.60
        assert v["spacing_mm"] == pytest.approx(0.30, abs=0.005)
    # three distinct gap locations, not one point sample
    mids = {(round(v["pos"][0], 1), round(v["pos"][1], 1)) for v in vs}
    assert len(mids) == 3
    # the checked facts carry the real defect population
    under = sum(p["pairs_under_requirement"]
                for e in payload["checked"] for p in e.get("pairs", []))
    assert under >= 3
    assert all(p["truncated"] is False
               for e in payload["checked"] for p in e.get("pairs", []))


# ---- Fixture C: coating rows -----------------------------------------------

_C_TRACKS = ('  (segment (start 2 2) (end 18 2) (width 0.5) (layer "F.Cu") '
             '(net "HV"))\n'
             '  (segment (start 2 2.95) (end 18 2.95) (width 0.5) '
             '(layer "F.Cu") (net "GND"))\n')      # 0.45 mm copper gap
_C_VOLT = {"voltages": [{"net": "HV", "voltage": 114}]}


def test_coating_none_track_pair_fires(tmp_path_factory, tmp_path):
    bg = _board(tmp_path_factory, "coat_none", _C_TRACKS)
    payload = _run(bg, _cons(tmp_path, _C_VOLT))
    assert payload["coating"] == "none"
    assert payload["status"] == "violations"   # B2 @ 114 V = 0.60 > 0.45
    v = payload["violations"][0]
    assert v["required_mm"] == 0.60 and v["rows"] == ["B2", "B2"]
    assert v["spacing_mm"] == pytest.approx(0.45, abs=0.005)


def test_coating_soldermask_track_pair_passes(tmp_path_factory, tmp_path):
    bg = _board(tmp_path_factory, "coat_mask", _C_TRACKS)
    payload = _run(bg, _cons(tmp_path, {**_C_VOLT, "coating": "soldermask"}))
    assert payload["coating"] == "soldermask"
    assert payload["status"] == "pass"         # B4 @ 114 V = 0.40 < 0.45


def test_cli_coating_overrides_constraints(tmp_path_factory, tmp_path):
    bg = _board(tmp_path_factory, "coat_cli", _C_TRACKS)
    payload = _run(bg, _cons(tmp_path, {**_C_VOLT, "coating": "none"}),
                   "--coating", "soldermask")
    assert payload["coating"] == "soldermask"
    assert payload["status"] == "pass"


def test_soldermask_exposed_land_stays_a6(tmp_path_factory, tmp_path):
    """Mask relief exposes lands: a pad near HV is A6 (0.80 mm at 101-150 V)
    even under soldermask - it does NOT get the B4 relief (IPC-2221 6.3.4)."""
    body = ('  (segment (start 2 2) (end 18 2) (width 0.5) (layer "F.Cu") '
            '(net "HV"))\n'
            '  (footprint "t:R" (at 10 3.2) (layer "F.Cu")\n'
            '    (property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))\n'
            '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
            '(net "GND")))\n')                 # pad top edge 0.45 mm from HV
    bg = _board(tmp_path_factory, "coat_pad", body)
    payload = _run(bg, _cons(tmp_path, {**_C_VOLT, "coating": "soldermask"}))
    assert payload["status"] == "violations"
    vs = payload["violations"]
    assert len(vs) == 1, json.dumps(vs)
    v = vs[0]
    assert v["required_mm"] == 0.80            # A6 wins over B4
    assert "A6" in v["rows"]
    assert v["spacing_mm"] == pytest.approx(0.45, abs=0.005)
    assert "R1" in v["refs"]


# ---- T6 (P8A-2): same-footprint pad pairs = land_pattern_pitch waiver ------

_HV_PAD_VOLT = {"voltages": [{"net": "HV", "voltage": 114},
                             {"net": "GND", "voltage": 0}]}


def _pad_pair_body(ref_a, ref_b, y_b):
    """Two 1x1 mm pads 0.45 mm apart vertically; refs configurable."""
    fp_a = (f'  (footprint "t:U" (at 10 2) (layer "F.Cu")\n'
            f'    (property "Reference" "{ref_a}" (at 0 0 0) (layer "F.SilkS"))\n'
            f'    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
            f'(net "HV"))')
    if ref_a == ref_b:
        return (fp_a + f'\n    (pad "2" smd rect (at 0 {y_b}) (size 1 1) '
                f'(layers "F.Cu") (net "GND")))\n')
    return (fp_a + ')\n'
            f'  (footprint "t:U" (at 10 {2 + y_b}) (layer "F.Cu")\n'
            f'    (property "Reference" "{ref_b}" (at 0 0 0) (layer "F.SilkS"))\n'
            f'    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
            f'(net "GND")))\n')


def test_same_footprint_pad_pair_is_waived_warning(tmp_path_factory, tmp_path):
    """Both pads of ONE footprint under requirement -> warning with the
    land_pattern_pitch waiver class, still visible in violations."""
    bg = _board(tmp_path_factory, "samefp", _pad_pair_body("U9", "U9", 1.45))
    payload = _run(bg, _cons(tmp_path, _HV_PAD_VOLT))
    assert payload["status"] == "violations"
    vs = payload["violations"]
    assert len(vs) == 1, json.dumps(vs)
    v = vs[0]
    assert v["kind"] == "creepage"
    assert v["severity"] == "warning"
    assert v["same_footprint"] is True
    assert v["waiver_class"] == "land_pattern_pitch"
    assert "part-selection scope" in v["msg"]
    assert v["refs"] == ["U9"]
    # A6/A6 at 114 V -> 0.80 mm; the 0.45 mm gap is under it
    assert v["required_mm"] == 0.80
    assert v["spacing_mm"] == pytest.approx(0.45, abs=0.005)
    # summary carries the class count
    pairs = [p for e in payload["checked"] for p in e.get("pairs", [])]
    assert sum(p["same_footprint_under"] for p in pairs) >= 1


def test_cross_footprint_pad_pair_stays_error(tmp_path_factory, tmp_path):
    bg = _board(tmp_path_factory, "crossfp", _pad_pair_body("U8", "U7", 1.45))
    payload = _run(bg, _cons(tmp_path, _HV_PAD_VOLT))
    assert payload["status"] == "violations"
    vs = payload["violations"]
    assert len(vs) == 1, json.dumps(vs)
    v = vs[0]
    assert v["severity"] == "error"
    assert "same_footprint" not in v
    assert "waiver_class" not in v
    assert sorted(v["refs"]) == ["U7", "U8"]


def test_track_pair_never_waived(tmp_path_factory, tmp_path):
    """The waiver is pad-pair-only: track/via items keep error severity."""
    bg = _board(tmp_path_factory, "trkerr", _C_TRACKS)
    payload = _run(bg, _cons(tmp_path, _C_VOLT))
    assert payload["violations"][0]["severity"] == "error"


# ---- T6 (P8A-4): coating-undeclared hint (facts only) ----------------------

def test_coating_undeclared_hint_fires_on_b2_rows(tmp_path_factory, tmp_path):
    bg = _board(tmp_path_factory, "hint_none", _C_TRACKS)
    payload = _run(bg, _cons(tmp_path, _C_VOLT))     # coating defaults none
    assert payload["coating_undeclared_hint"] is True
    assert payload["coating_undeclared_count"] == 1
    # facts only: same violation count/severities as before the hint existed
    assert [v["severity"] for v in payload["violations"]] == ["error"]


def test_coating_declared_no_hint(tmp_path_factory, tmp_path):
    bg = _board(tmp_path_factory, "hint_mask", _C_TRACKS)
    payload = _run(bg, _cons(tmp_path, {**_C_VOLT, "coating": "soldermask"}))
    assert "coating_undeclared_hint" not in payload


def test_pad_only_findings_no_hint(tmp_path_factory, tmp_path):
    """A6 rows are coating-independent for pads: no hint when nothing was
    adjudicated on B2/B3."""
    bg = _board(tmp_path_factory, "hint_pads", _pad_pair_body("U8", "U7", 1.45))
    payload = _run(bg, _cons(tmp_path, _HV_PAD_VOLT))
    assert payload["status"] == "violations"
    assert "coating_undeclared_hint" not in payload


# ---- table sanity: the pinned clearance_mm API delegates to B2/B1 ----------

def test_clearance_mm_delegates_to_rows():
    c = check_creepage.clearance_mm
    r = check_creepage.row_clearance_mm
    for dv in (10, 30, 31, 100, 150, 170, 300, 500, 1000):
        assert c(dv, True) == r(dv, "B2")
        assert c(dv, False) == r(dv, "B1")
    # LEARNINGS-verified cells: 51-100 and 101-150 bands
    assert r(100, "B4") == 0.13 and r(150, "B4") == 0.40
    assert r(100, "A6") == 0.50 and r(150, "A6") == 0.80
    assert r(100, "A5") == 0.13 and r(150, "A7") == 0.40
    assert r(100, "B3") == 1.50 and r(150, "B3") == 3.20


# ---- verifier follow-up: explicit low-voltage pair WAIVES the derived sweep

def test_low_voltage_pair_waives_derived_sweep(tmp_path_factory, tmp_path):
    """An explicit voltage_pairs entry <= 30 V removes the pair from the
    voltages-derived sweep entirely (deliberate: it declares the true
    differential for an AC-coupled/same-phase pair that node voltages
    overstate). The waiver is visible in skipped_low_voltage_pairs."""
    body = ('  (segment (start 2 2) (end 18 2) (width 0.5) (layer "F.Cu") '
            '(net "HV"))\n'
            '  (segment (start 2 2.8) (end 18 2.8) (width 0.5) (layer "F.Cu") '
            '(net "GND"))\n')                  # 0.30 mm gap
    bg = _board(tmp_path_factory, "lvwaive", body)
    volts = {"voltages": [{"net": "HV", "voltage": 100},
                          {"net": "GND", "voltage": 0}]}
    # control: derived sweep alone flags the 0.30 mm gap at 100 V (B2 0.60)
    payload = _run(bg, _cons(tmp_path, volts))
    assert payload["status"] == "violations"
    # explicit 24 V pair: derived check for this pair is waived, recorded
    (tmp_path / "constraints.json").unlink()
    both = {**volts,
            "voltage_pairs": [{"a": "HV", "b": "GND", "voltage": 24}]}
    payload = _run(bg, _cons(tmp_path, both))
    assert payload["status"] == "pass"
    assert payload["violations"] == []
    assert payload["skipped_low_voltage_pairs"] == [
        {"a": "HV", "b": "GND", "voltage": 24.0}]


# ---- board DRU rule scoped to one footprint = dru_footprint_scope ---------
# PCB-0018-A U301 (0.5 mm-pitch gate driver): the switch-node pour and the
# fanout tracks sit at the package's own pitch from its pins, under the
# owner's `A.intersectsCourtyard('U301') && B...` 0.13 mm clearance rule.

_SCOPE_RULE = """(version 1)
(rule "aiee_clearance_floor"
\t(constraint clearance (min 0.1016mm))
)
(rule "hv_scope_u9_courtyard"
\t(constraint clearance (min {min_mm:.4f}mm))
\t(condition "{cond}")
)
"""
_U9_COND = "A.intersectsCourtyard('U9') && B.intersectsCourtyard('U9')"


def _fp(ref, x, y, net, crtyd=2.0):
    """1x1 mm pad at (x, y) inside a +/-crtyd mm F.CrtYd rect."""
    return (f'  (footprint "t:U" (at {x} {y}) (layer "F.Cu")\n'
            f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS"))\n'
            f'    (fp_rect (start {-crtyd} {-crtyd}) (end {crtyd} {crtyd}) '
            f'(stroke (width 0.05)) (fill no) (layer "F.CrtYd"))\n'
            f'    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
            f'(net "{net}")))\n')


# GND track 0.30 mm below U9's HV pad (pad edge y=2.5, track edge y=2.8)
_GND_TRACK = ('  (segment (start 9 2.9) (end 11 2.9) (width 0.2) '
              '(layer "F.Cu") (net "GND"))\n')


def _scoped(tmp_path_factory, tmp_path, name, body, cond=_U9_COND,
            min_mm=0.13, dru=True):
    bg = _board(tmp_path_factory, name, body)
    if dru:
        bg.path.with_suffix(".kicad_dru").write_text(
            _SCOPE_RULE.format(min_mm=min_mm, cond=cond), encoding="utf-8")
    return _run(bg, _cons(tmp_path, _HV_PAD_VOLT))


def test_dru_footprint_scope_demotes_own_pin_gap(tmp_path_factory, tmp_path):
    payload = _scoped(tmp_path_factory, tmp_path, "scope_ok",
                      _fp("U9", 10, 2, "HV") + _GND_TRACK)
    vs = payload["violations"]
    assert len(vs) == 1, json.dumps(vs)
    v = vs[0]
    assert v["severity"] == "warning"
    assert v["waiver_class"] == "dru_footprint_scope"
    assert v["dru_rule"] == "hv_scope_u9_courtyard"
    assert v["scope_ref"] == "U9" and v["dru_min_mm"] == 0.13
    assert v["spacing_mm"] == pytest.approx(0.30, abs=0.005)
    assert "board rule 'hv_scope_u9_courtyard'" in v["msg"]
    assert payload["dru_footprint_scopes"] == [
        {"rule": "hv_scope_u9_courtyard", "ref": "U9", "min_mm": 0.13,
         "courtyard_found": True}]
    pairs = [p for e in payload["checked"] for p in e.get("pairs", [])]
    assert sum(p["dru_footprint_scope_under"] for p in pairs) == 1


def test_dru_footprint_scope_absent_rule_stays_error(tmp_path_factory,
                                                     tmp_path):
    payload = _scoped(tmp_path_factory, tmp_path, "scope_none",
                      _fp("U9", 10, 2, "HV") + _GND_TRACK, dru=False)
    v, = payload["violations"]
    assert v["severity"] == "error" and "waiver_class" not in v
    assert payload["dru_footprint_scopes"] == []


def test_dru_footprint_scope_other_footprint_stays_error(tmp_path_factory,
                                                         tmp_path):
    """A different footprint's pad inside U9's courtyard is part-to-part
    spacing: the package rating does not cover it."""
    body = (_fp("U9", 10, 2, "HV", crtyd=3.0)
            + _fp("U7", 10, 3.3, "GND", crtyd=0.6))    # 0.30 mm pad gap
    payload = _scoped(tmp_path_factory, tmp_path, "scope_xfp", body)
    v, = payload["violations"]
    assert v["severity"] == "error" and "waiver_class" not in v
    assert sorted(v["refs"]) == ["U7", "U9"]


def test_dru_footprint_scope_gap_outside_courtyard_stays_error(
        tmp_path_factory, tmp_path):
    """Courtyard ends at y=2.7; the track edge at y=2.8 is board copper."""
    payload = _scoped(tmp_path_factory, tmp_path, "scope_out",
                      _fp("U9", 10, 2, "HV", crtyd=0.7) + _GND_TRACK)
    v, = payload["violations"]
    assert v["severity"] == "error"


def test_dru_footprint_scope_below_rule_min_stays_error(tmp_path_factory,
                                                        tmp_path):
    payload = _scoped(tmp_path_factory, tmp_path, "scope_min",
                      _fp("U9", 10, 2, "HV") + _GND_TRACK, min_mm=0.35)
    v, = payload["violations"]
    assert v["severity"] == "error"


def test_dru_footprint_scope_extra_term_not_honoured(tmp_path_factory,
                                                     tmp_path):
    cond = _U9_COND + " && A.NetName == 'HV'"
    payload = _scoped(tmp_path_factory, tmp_path, "scope_net",
                      _fp("U9", 10, 2, "HV") + _GND_TRACK, cond=cond)
    v, = payload["violations"]
    assert v["severity"] == "error"
    assert payload["dru_footprint_scopes"] == []


def test_dru_footprint_scope_member_rule_needs_two_own_pads(
        tmp_path_factory, tmp_path):
    """memberOfFootprint on A and B covers pad pairs only, not a track."""
    cond = "A.memberOfFootprint('U9') && B.memberOfFootprint('U9')"
    payload = _scoped(tmp_path_factory, tmp_path, "scope_member",
                      _fp("U9", 10, 2, "HV") + _GND_TRACK, cond=cond)
    v, = payload["violations"]
    assert v["severity"] == "error"


@pytest.mark.parametrize("cond", [
    "A.intersectsCourtyard('U99')",          # no such footprint
    "A.intersectsBackCourtyard('U9')",       # U9 sits on the front
])
def test_dru_footprint_scope_without_courtyard_stays_error(
        tmp_path_factory, tmp_path, cond):
    payload = _scoped(tmp_path_factory, tmp_path, "scope_nocy",
                      _fp("U9", 10, 2, "HV") + _GND_TRACK, cond=cond)
    v, = payload["violations"]
    assert v["severity"] == "error"
    assert payload["dru_footprint_scopes"][0]["courtyard_found"] is False


def test_dru_footprint_scope_waived_gap_does_not_hide_error(
        tmp_path_factory, tmp_path):
    """A tighter waived gap (own-pin track, 0.25 mm) beside a real error
    (U7's pad, 0.30 mm) at the same spot: a waiver never dedups away an
    error, so it is still reported."""
    body = (_fp("U9", 10, 2, "HV", crtyd=3.0)
            + _fp("U7", 10, 3.3, "GND", crtyd=0.6)
            + '  (segment (start 9 2.85) (end 11 2.85) (width 0.2) '
              '(layer "F.Cu") (net "GND"))\n')
    payload = _scoped(tmp_path_factory, tmp_path, "scope_mask", body)
    sev = sorted((v["severity"], v.get("waiver_class"))
                 for v in payload["violations"])
    assert ("error", None) in sev, json.dumps(payload["violations"])
    assert ("warning", "dru_footprint_scope") in sev


def test_dru_footprint_scope_layer_clause_not_honoured(tmp_path_factory,
                                                       tmp_path):
    bg = _board(tmp_path_factory, "scope_layer",
                _fp("U9", 10, 2, "HV") + _GND_TRACK)
    bg.path.with_suffix(".kicad_dru").write_text(
        _SCOPE_RULE.format(min_mm=0.13, cond=_U9_COND).replace(
            "\t(condition", "\t(layer outer)\n\t(condition"), encoding="utf-8")
    payload = _run(bg, _cons(tmp_path, _HV_PAD_VOLT))
    v, = payload["violations"]
    assert v["severity"] == "error"
    assert payload["dru_footprint_scopes"] == []


@pytest.mark.parametrize("cond, want", [
    ("A.insideCourtyard('U301')",
     {"ref": "U301", "side": None, "courtyard": True, "both_member": False}),
    ("(A.intersectsCourtyard('U3') && B.intersectsCourtyard('U3'))",
     {"ref": "U3", "side": None, "courtyard": True, "both_member": False}),
    ("A.intersectsFrontCourtyard( 'U3' )",
     {"ref": "U3", "side": "front", "courtyard": True, "both_member": False}),
    ("A.memberOfFootprint('U3') && B.memberOfFootprint('U3')",
     {"ref": "U3", "side": None, "courtyard": False, "both_member": True}),
    ("A.insideCourtyard('U*')", None),
    ("A.insideCourtyard('U3') && B.insideCourtyard('U4')", None),
    ("A.insideCourtyard('U3') || B.insideCourtyard('U3')", None),
    ("!A.insideCourtyard('U3')", None),
    ("A.NetName == 'HV'", None),
    (None, None),
])
def test_parse_footprint_scope_forms(cond, want):
    assert check_creepage.parse_footprint_scope(cond) == want
