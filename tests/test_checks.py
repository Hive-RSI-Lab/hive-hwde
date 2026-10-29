"""S4 acceptance tests: verification suite part 1 (the crown jewels).

Plan S4 accept criteria, arbitrated by tests/golden/manifest.yaml:
  - every relevant S1 mutant caught with correct net + coordinates
        -> test_mutant_* (manifest-driven)
  - zero false positives on the three golden boards
        -> test_golden_clean_* (+ the non-S4 mutants as negative controls)
  - each check < 30 s on the RF board          -> test_performance (smoke)

Pure tests (no marker) exercise the algorithms on synthetic boards and run
without any toolchain. The corpus tests are also toolchain-free (committed
boards + pure-venv geometry) and therefore unmarked; only the timing test
carries the `smoke` marker (wall-clock sensitive).
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from shapely.geometry import box

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
GOLDEN = REPO / "tests" / "golden"
PYTHON = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_current  # noqa: E402
import check_decoupling  # noqa: E402
import check_return_path  # noqa: E402
import checklib  # noqa: E402
import geom  # noqa: E402
from _perf import assert_under  # noqa: E402

MANIFEST = yaml.safe_load((GOLDEN / "manifest.yaml").read_text(encoding="utf-8"))
BOARDS = list(MANIFEST["golden_boards"])
S4_CHECKS = {"check_return_path", "check_current", "check_decoupling"}
S4_MUTANTS = {name: m for name, m in MANIFEST["mutants"].items()
              if m["check"] in S4_CHECKS}
OTHER_MUTANTS = {name: m for name, m in MANIFEST["mutants"].items()
                 if m["check"] not in S4_CHECKS}


def board_path(name: str) -> Path:
    return GOLDEN / name / f"{name}.kicad_pcb"


def mutant_path(mutant: str) -> Path:
    board = MANIFEST["mutants"][mutant]["board"]
    return GOLDEN / "mutants" / mutant / f"{board}.kicad_pcb"


def sidecar(pcb: Path, board: str, name: str) -> Path:
    """A mutant's own constraints.json / decoupling.json when its dir holds
    one (see tests/golden/mutations/mutlib.py), else the golden's."""
    own = pcb.parent / name
    if pcb.parent.parent.name == "mutants" and own.exists():
        return own
    return GOLDEN / board / name


def run_check(script: str, pcb: Path, board: str) -> dict:
    """Run a check module in-process against a board + its fixtures (the
    mutant's sidecar when it has one, else the golden's)."""
    if script == "check_decoupling":
        payload, _ = check_decoupling.run(
            ["--pcb", str(pcb),
             "--metadata", str(sidecar(pcb, board, "decoupling.json"))])
        return payload
    mod = {"check_return_path": check_return_path,
           "check_current": check_current}[script]
    payload, _ = mod.run(
        ["--pcb", str(pcb),
         "--constraints", str(sidecar(pcb, board, "constraints.json"))])
    return payload


# ============================================================ pure: current

def test_ipc2152_interpolation():
    w = check_current.width_1oz_10c
    assert w(0.0) == 0.0
    assert w(0.5) == pytest.approx(0.25)
    assert w(0.4) == pytest.approx(0.20)       # linear below first row
    assert w(1.5) == pytest.approx(0.80)       # between rows
    assert w(10.0) == pytest.approx(9.0)
    assert w(11.0) == pytest.approx(9.0 + (9.0 - 5.5) / 3.0)  # extrapolated
    vals = [w(i / 10.0) for i in range(1, 120)]
    assert all(b >= a for a, b in zip(vals, vals[1:]))  # monotonic


def test_required_width_scales_with_copper_and_dt():
    req = check_current.required_width_mm
    outer = req(0.4, 10.0, 0.035)
    inner = req(0.4, 10.0, 0.0152)
    assert outer == pytest.approx(0.20)
    assert inner == pytest.approx(outer * 0.035 / 0.0152)
    assert req(0.4, 20.0, 0.035) < outer       # more allowed rise -> narrower
    assert req(0.4, 5.0, 0.035) > outer


def test_via_clustering():
    class V:  # minimal stand-in
        def __init__(self, x, y):
            self.at = (x, y)
    vias = [V(0, 0), V(1.0, 0), V(1.9, 0.5), V(10, 10)]
    groups = sorted(check_current.cluster_vias(vias), key=len)
    assert [len(g) for g in groups] == [1, 3]


# ============================================================ pure: decoupling

def test_farads_parse_and_class():
    p = check_decoupling.parse_farads
    assert p("100nF") == pytest.approx(1e-7)
    assert p("10uF") == pytest.approx(1e-5)
    assert p("22pF") == pytest.approx(2.2e-11)
    assert p("4.7uF") == pytest.approx(4.7e-6)
    assert p(1e-7) == pytest.approx(1e-7)
    assert p("garbage") is None
    cls = check_decoupling.value_class
    assert cls(p("10uF")) == "bulk"
    assert cls(p("100nF")) == "mid"
    assert cls(p("22pF")) == "hf"
    assert cls(None) == "mid"


def test_severity_ladder():
    sev = check_decoupling.sev_for
    assert sev(9.0, 10.0, 15.0) is None
    assert sev(12.0, 10.0, 15.0) == "warning"
    assert sev(16.0, 10.0, 15.0) == "error"


# ============================================================ pure: return path

def test_radius_from_rise_time():
    r = check_return_path.net_entry_radius
    assert r({}) == pytest.approx(2.0)
    assert r({"return_via_radius_mm": 3.5}) == pytest.approx(3.5)
    # t_rise 1 ns -> f_knee 0.5 GHz -> c/(f*20) = 29.98 mm
    assert r({"t_rise_ns": 1.0}) == pytest.approx(29.98, rel=1e-3)


def test_reference_net_mapping():
    f = check_return_path.reference_net_for
    assert f({}, "F.Cu") == "GND"
    assert f({"reference": "AGND"}, "F.Cu") == "AGND"
    assert f({"reference": {"F.Cu": "GND", "B.Cu": "PWR"}}, "B.Cu") == "PWR"
    assert f({"reference": {"default": "AGND"}}, "In1.Cu") == "AGND"


# ---- synthetic boards ----------------------------------------------------

def _board(tmp_path_factory, name: str, body: str) -> geom.BoardGeom:
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path_factory.mktemp(name) / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return geom.load_board(p)


def _board4(tmp_path_factory, name: str, body: str) -> geom.BoardGeom:
    text = f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal)
    (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
"""
    p = tmp_path_factory.mktemp(name) / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return geom.load_board(p)


FULL_FILL = """  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
"""

SPLIT_FILL = """  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 4 0) (xy 4 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 4.8 0) (xy 20 0) (xy 20 10) (xy 4.8 10))))
"""

# fill with a 1.2 x 1.2 antipad void around (3,1), reached by a keyhole slit
ANTIPAD_FILL = """  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10) (xy 0 1.0005)
           (xy 2.4 1.0005) (xy 2.4 1.6) (xy 3.6 1.6) (xy 3.6 0.4)
           (xy 2.4 0.4) (xy 2.4 0.9995) (xy 0 0.9995))))
"""

SIG_TRACK = """  (segment (start 1 1) (end 9 1) (width 0.2) (layer "F.Cu") (net "SIG"))
"""
HS = [{"net": "SIG", "reference": "GND"}]


def test_corridor_clean_reference(tmp_path_factory):
    bg = _board(tmp_path_factory, "clean", SIG_TRACK + FULL_FILL)
    vs, facts = check_return_path.check_net(bg, HS[0], 3.0)
    assert vs == []
    assert facts["layers"] == ["F.Cu"]


def test_corridor_split_plane_flagged(tmp_path_factory):
    bg = _board(tmp_path_factory, "split", SIG_TRACK + SPLIT_FILL)
    vs, _ = check_return_path.check_net(bg, HS[0], 3.0)
    assert any(v["kind"] == "corridor_void" and v["severity"] == "error"
               for v in vs)
    v = next(v for v in vs if v["kind"] == "corridor_void")
    from shapely.geometry import Polygon
    assert Polygon(v["polygon"]).intersects(box(4, 0.4, 4.8, 1.6))
    assert v["crossing_len_mm"] >= 0.8
    assert v["layer"] == "B.Cu" and v["net"] == "SIG"


def test_corridor_antipad_excised(tmp_path_factory):
    """A lone other-net via antipad nicking the corridor is not a violation."""
    body = SIG_TRACK + ANTIPAD_FILL + \
        '  (via (at 3 1) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))\n'
    bg = _board(tmp_path_factory, "antipad", body)
    vs, _ = check_return_path.check_net(bg, HS[0], 3.0)
    assert vs == []


def test_corridor_antipad_without_via_survives(tmp_path_factory):
    """The same void NOT explained by any via stays a violation (excision
    disks only exist at vias/through-pads)."""
    bg = _board(tmp_path_factory, "voidonly", SIG_TRACK + ANTIPAD_FILL)
    vs, _ = check_return_path.check_net(bg, HS[0], 3.0)
    assert any(v["kind"] == "corridor_void" for v in vs)


def test_corridor_flat_cap_stops_at_endpoint(tmp_path_factory):
    """Copper voids beyond the trace end (past the landing pad) are not the
    trace's problem: fill ends at x=9.5, trace ends at x=9."""
    fill = """  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 9.5 0) (xy 9.5 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 9.5 0) (xy 9.5 10) (xy 0 10))))
"""
    bg = _board(tmp_path_factory, "flatcap", SIG_TRACK + fill)
    vs, _ = check_return_path.check_net(bg, HS[0], 3.0)
    assert vs == []


def test_no_reference_plane_at_all(tmp_path_factory):
    bg = _board(tmp_path_factory, "noref", SIG_TRACK)
    vs, _ = check_return_path.check_net(bg, HS[0], 3.0)
    assert [v["kind"] for v in vs] == ["no_reference_plane"]
    assert vs[0]["severity"] == "error"


TRANS_BODY = """  (segment (start 1 5) (end 5 5) (width 0.2) (layer "F.Cu") (net "SIG"))
  (segment (start 5 5) (end 12 5) (width 0.2) (layer "B.Cu") (net "SIG"))
  (via (at 5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "SIG"))
  (zone (net "GND") (layer "In1.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In1.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
  (zone (net "GND") (layer "In2.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
"""


def test_transition_with_return_via_passes(tmp_path_factory):
    body = TRANS_BODY + \
        '  (via (at 6 5.5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))\n'
    bg = _board4(tmp_path_factory, "transok", body)
    vs, facts = check_return_path.check_net(bg, HS[0], 3.0)
    assert [v for v in vs if v["kind"] == "missing_return_via"] == []
    assert facts["transitions"] == [{"pos": [5.0, 5.0],
                                     "layers": ["F.Cu", "B.Cu"]}]


def test_transition_missing_return_via(tmp_path_factory):
    body = TRANS_BODY + \
        '  (via (at 15 9) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))\n'
    bg = _board4(tmp_path_factory, "transbad", body)
    vs, _ = check_return_path.check_net(bg, HS[0], 3.0)
    miss = [v for v in vs if v["kind"] == "missing_return_via"]
    assert len(miss) == 1
    assert miss[0]["pos"] == [5.0, 5.0]
    assert miss[0]["severity"] == "error"
    assert miss[0]["nearest_ref_via_mm"] == pytest.approx(10.77, abs=0.01)


def test_transition_reference_net_change_needs_stitch_cap(tmp_path_factory):
    pwr_in2 = TRANS_BODY.replace(
        '(zone (net "GND") (layer "In2.Cu")', '(zone (net "PWR") (layer "In2.Cu")')
    entry = {"net": "SIG", "reference": {"F.Cu": "GND", "B.Cu": "PWR"}}
    bg = _board4(tmp_path_factory, "stitchbad", pwr_in2)
    vs, _ = check_return_path.check_net(bg, entry, 3.0)
    assert any(v["kind"] == "missing_stitch_cap" for v in vs)
    # with a two-pad footprint bridging GND/PWR at the transition -> pass
    cap = """  (footprint "t:C" (at 5.8 5.8)
    (layer "F.Cu")
    (property "Reference" "C9" (at 0 0 0))
    (pad "1" smd rect (at -0.5 0) (size 0.6 0.6) (layers "F.Cu") (net "GND"))
    (pad "2" smd rect (at 0.5 0) (size 0.6 0.6) (layers "F.Cu") (net "PWR")))
"""
    bg2 = _board4(tmp_path_factory, "stitchok", pwr_in2 + cap)
    vs2, _ = check_return_path.check_net(bg2, entry, 3.0)
    assert [v for v in vs2 if v["kind"] == "missing_stitch_cap"] == []


def test_missing_net_is_check_error(tmp_path_factory):
    bg = _board(tmp_path_factory, "nonet", SIG_TRACK + FULL_FILL)
    with pytest.raises(checklib.CheckError):
        check_return_path.check_net(bg, {"net": "/GHOST"}, 3.0)


# ---- pour neck (check_current) --------------------------------------------

DUMBBELL = """  (zone (net "PWR") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 15 0) (xy 15 5) (xy 0 5)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 5 0) (xy 5 2.4) (xy 10 2.4) (xy 10 0) (xy 15 0)
           (xy 15 5) (xy 10 5) (xy 10 2.6) (xy 5 2.6) (xy 5 5) (xy 0 5))))
  (via (at 2 2) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))
  (via (at 13 2) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))
"""


def test_pour_neckdown_detected(tmp_path_factory):
    bg = _board(tmp_path_factory, "neck", DUMBBELL)
    vs, _ = check_current.check_net(bg, {"net": "PWR", "current_a": 0.5})
    necks = [v for v in vs if v["kind"] == "pour_neckdown"]
    assert len(necks) == 1
    assert necks[0]["neck_mm"] < 0.25
    assert necks[0]["layer"] == "B.Cu"


def test_pour_wide_neck_passes(tmp_path_factory):
    wide = DUMBBELL.replace("2.4", "1.0").replace("2.6", "4.0")
    bg = _board(tmp_path_factory, "wide", wide)
    vs, _ = check_current.check_net(bg, {"net": "PWR", "current_a": 0.5})
    assert [v for v in vs if v["kind"] == "pour_neckdown"] == []


def test_undersized_track_and_override(tmp_path_factory):
    body = ('  (segment (start 1 8) (end 9 8) (width 0.15) (layer "F.Cu") '
            '(net "PWR"))\n') + DUMBBELL.replace("2.4", "1.0").replace("2.6", "4.0")
    bg = _board(tmp_path_factory, "thin", body)
    entry = {"net": "PWR", "current_a": 0.5}
    vs, _ = check_current.check_net(bg, entry)
    thin = [v for v in vs if v["kind"] == "undersized_track"]
    assert len(thin) == 1 and thin[0]["required_mm"] == pytest.approx(0.25)
    # regional override: the branch only carries 0.1 A -> passes
    entry2 = {"net": "PWR", "current_a": 0.5,
              "overrides": [{"near": [5, 8], "radius_mm": 6.0,
                             "current_a": 0.1}]}
    vs2, _ = check_current.check_net(bg, entry2)
    assert [v for v in vs2 if v["kind"] == "undersized_track"] == []


def test_insufficient_transition_vias(tmp_path_factory):
    bg = _board(tmp_path_factory, "fewvias", DUMBBELL.replace("2.4", "1.0")
                .replace("2.6", "4.0"))
    vs, _ = check_current.check_net(bg, {"net": "PWR", "current_a": 1.6})
    weak = [v for v in vs if v["kind"] == "insufficient_transition_vias"]
    assert len(weak) == 2          # two 1-via clusters, 1.6 A needs 4 each
    assert all(v["required"] == 4 for v in weak)


# ---- decoupling on a synthetic board ---------------------------------------

DECAP_BODY = """  (footprint "t:U" (at 5 5)
    (layer "F.Cu")
    (property "Reference" "U9" (at 0 0 0))
    (pad "7" smd rect (at 0 0) (size 0.5 0.5) (layers "F.Cu") (net "VDD")))
  (footprint "t:C" (at {cx} 5)
    (layer "F.Cu")
    (property "Reference" "C9" (at 0 0 0))
    (pad "1" smd rect (at -0.4 0) (size 0.5 0.5) (layers "F.Cu") (net "VDD"))
    (pad "2" smd rect (at 0.4 0) (size 0.5 0.5) (layers "F.Cu") (net "GND")))
  (segment (start 5 5) (end {p1x} 5) (width 0.25) (layer "F.Cu") (net "VDD"))
  (via (at {gvx} 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
""" + FULL_FILL
META = {"cap": "C9", "ic": "U9", "pin": "7", "rail": "VDD", "value": "100nF"}


def _decap_board(tmp_path_factory, name, cap_x):
    body = DECAP_BODY.format(cx=cap_x, p1x=cap_x - 0.4, gvx=cap_x + 1.0)
    return _board(tmp_path_factory, name, body)


def test_decoupling_close_cap_passes(tmp_path_factory):
    bg = _decap_board(tmp_path_factory, "goodcap", 8.0)
    vs, facts = check_decoupling.check_association(bg, dict(META))
    assert vs == []
    assert facts["manhattan_mm"] == pytest.approx(2.6)
    assert facts["vias_in_loop"] == 1          # same-layer rail path


def test_decoupling_far_cap_flagged(tmp_path_factory):
    bg = _decap_board(tmp_path_factory, "farcap", 17.0)
    vs, _ = check_decoupling.check_association(bg, dict(META))
    kinds = {v["kind"]: v["severity"] for v in vs}
    assert kinds.get("decoupler_distance") == "warning"   # 11.6 mm, mid class
    v = next(v for v in vs if v["kind"] == "decoupler_distance")
    assert v["refs"] == ["C9", "U9"] and v["pin"] == "U9.7"


def test_decoupling_declared_limit_is_error(tmp_path_factory):
    """U20: a DECLARED per-association limit is the board's own contract -
    exceeding it is an ERROR at the limit itself, never a warning until the
    class-default error threshold (the bb-adc escape: a declared 2.5 mm
    exceeded at 3.97 mm read as a warning, so `gate --gate place` passed)."""
    bg = _decap_board(tmp_path_factory, "declared", 8.0)   # 2.6 mm measured
    vs, _ = check_decoupling.check_association(
        bg, {**META, "max_dist_mm": 2.0})
    v = [x for x in vs if x["kind"] == "decoupler_distance"]
    assert v and v[0]["severity"] == "error"
    assert v[0]["limit_mm"] == pytest.approx(2.0)
    # within the declared limit: clean - class defaults never re-enter
    vs2, _ = check_decoupling.check_association(
        bg, {**META, "max_dist_mm": 3.0})
    assert [x for x in vs2 if x["kind"] == "decoupler_distance"] == []
    # declared loop limit: same contract
    vs3, _ = check_decoupling.check_association(
        bg, {**META, "max_loop_nh": 1.0})
    v3 = [x for x in vs3 if x["kind"] == "decoupler_loop"]
    assert v3 and v3[0]["severity"] == "error"


def test_decoupling_no_rail_path_counts_vias(tmp_path_factory):
    """Remove the surface rail trace: loop picks up 2 plane vias."""
    body = DECAP_BODY.format(cx=8.0, p1x=5.0, gvx=9.0).replace(
        '  (segment (start 5 5) (end 5.0 5) (width 0.25) (layer "F.Cu") (net "VDD"))\n',
        "")
    bg = _board(tmp_path_factory, "novia", body)
    vs, facts = check_decoupling.check_association(bg, dict(META))
    assert facts["vias_in_loop"] == 3          # 2 rail + 1 gnd


def test_decoupling_stale_metadata_is_violation(tmp_path_factory):
    bg = _decap_board(tmp_path_factory, "stale", 8.0)
    vs, facts = check_decoupling.check_association(
        bg, {**META, "cap": "C77"})
    assert facts is None
    assert vs[0]["kind"] == "metadata_mismatch"
    assert vs[0]["severity"] == "error"
    vs2, _ = check_decoupling.check_association(bg, {**META, "pin": "3"})
    assert vs2[0]["kind"] == "metadata_mismatch"


# ---- role=reg_input: switching-regulator input group (U1, retro R1) --------

def _reg_group(bg, assoc):
    groups: dict = {}
    vs, facts = check_decoupling.check_association(bg, assoc, groups)
    return vs, facts, check_decoupling.check_reg_inputs(groups)


def test_reg_input_bulk_only_fires_even_when_close(tmp_path_factory):
    """The R1 blind spot: a lone bulk input cap is legal by its own value
    class at ANY distance - the group check must fire on the ABSENCE of an
    HF ceramic, anchored at the regulator pin."""
    bg = _decap_board(tmp_path_factory, "reginbulk", 8.0)   # 2.6 mm away
    vs, facts, gvs = _reg_group(
        bg, {**META, "value": "10uF", "role": "reg_input"})
    assert vs == []                          # the bulk cap itself is legal
    assert facts["role"] == "reg_input"
    assert [v["kind"] for v in gvs] == ["reg_input_no_hf"]
    v = gvs[0]
    assert v["severity"] == "error" and v["pin"] == "U9.7"
    assert v["pos"] == pytest.approx([5.0, 5.0])        # the PIN pad
    assert "U9" in v["refs"] and "C9" in v["refs"]
    assert v["nearest_mm"] == pytest.approx(2.6)


def test_reg_input_hf_within_limit_passes(tmp_path_factory):
    bg = _decap_board(tmp_path_factory, "reginhf", 8.0)
    _, _, gvs = _reg_group(bg, {**META, "role": "reg_input"})   # 100nF
    assert gvs == []


def test_reg_input_hf_too_far_fires(tmp_path_factory):
    """An HF ceramic beyond the hf error distance does not satisfy the
    group even though its own mid-class distance check merely warns."""
    bg = _decap_board(tmp_path_factory, "reginfar", 17.0)   # 11.6 mm
    vs, _, gvs = _reg_group(bg, {**META, "role": "reg_input"})
    assert {v["kind"] for v in vs} == {"decoupler_distance"}
    assert [v["kind"] for v in gvs] == ["reg_input_no_hf"]
    assert gvs[0]["nearest_mm"] == pytest.approx(11.6)


def test_reg_input_explicit_hf_class_qualifies(tmp_path_factory):
    """A >1 uF cap the design genuinely uses as the HF loop qualifies via
    an explicit class override (and takes on hf limits for itself)."""
    bg = _decap_board(tmp_path_factory, "regincls", 8.0)
    _, _, gvs = _reg_group(
        bg, {**META, "value": "2.2uF", "class": "hf", "role": "reg_input"})
    assert gvs == []


def test_reg_input_carrier_known_answer():
    """Retro 2026-08-07 R1: U21 (TPS563201) shipped with only a 22 uF at
    9.89 mm and no HF ceramic anywhere - the only soldering-iron rework on
    the batch, invisible to value classing. With role=reg_input declared in
    the carrier sidecar this MUST fire; U20 (C61 100 nF at 5.16 mm) is the
    clean twin proving the group can pass."""
    # the routed carrier PCB is frozen in the stage fixtures; its sidecar
    # (with the reg_input roles) is copied from the board as it shipped
    fix = REPO / "tests" / "fixtures"
    payload, _ = check_decoupling.run(
        ["--pcb", str(fix / "stages" / "lumina_carrier" / "routed.kicad_pcb"),
         "--metadata",
         str(fix / "checks" / "lumina-carrier.decoupling.json")])
    ri = [v for v in payload["violations"] if v["kind"] == "reg_input_no_hf"]
    assert len(ri) == 1
    v = ri[0]
    assert v["pin"] == "U21.3" and v["severity"] == "error"
    assert v["nearest_mm"] == pytest.approx(9.89, abs=0.05)
    assert v["pos"] == pytest.approx([65.35, 112.05], abs=0.05)
    roles = {c["pin"] for c in payload["checked"]
             if c.get("role") == "reg_input"}
    assert roles == {"U20.3", "U21.3"}


def test_unfilled_zone_refused(tmp_path_factory):
    unfilled = SIG_TRACK + """  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
"""
    p = tmp_path_factory.mktemp("stale") / "stale.kicad_pcb"
    p.write_text(f"""(kicad_pcb
  (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{unfilled})
""", encoding="utf-8")
    cons = p.parent / "c.json"
    cons.write_text(json.dumps({"high_speed": HS}), encoding="utf-8")
    proc = subprocess.run(
        [PYTHON, str(SCRIPTS / "check_return_path.py"), "--pcb", str(p),
         "--constraints", str(cons)], capture_output=True, text=True)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["status"] == "error"


# ============================================================ corpus: goldens

@pytest.mark.parametrize("board", BOARDS)
@pytest.mark.parametrize("script", sorted(S4_CHECKS))
def test_golden_clean(script, board):
    payload = run_check(script, board_path(board), board)
    assert payload["status"] == "pass", json.dumps(payload["violations"],
                                                   indent=1)
    assert payload["violations"] == []
    assert payload["counts"]["total"] == 0
    assert payload["checked"]           # it actually checked something


@pytest.mark.parametrize("mutant", sorted(OTHER_MUTANTS))
@pytest.mark.parametrize("script", sorted(S4_CHECKS))
def test_non_s4_mutants_stay_clean(script, mutant):
    """Negative controls: S5/S12-designated mutants must not trip S4 checks."""
    board = MANIFEST["mutants"][mutant]["board"]
    payload = run_check(script, mutant_path(mutant), board)
    assert payload["status"] == "pass", json.dumps(payload["violations"],
                                                   indent=1)


# ============================================================ corpus: mutants

def test_mutant_plane_split_caught():
    m = S4_MUTANTS["plane-split-under-clock"]
    payload = run_check("check_return_path", mutant_path("plane-split-under-clock"),
                        m["board"])
    assert payload["status"] == "violations"
    exp = m["expect"]
    net = exp["net"] if exp["net"].startswith(("/", "+")) or exp["net"] == "GND" \
        else "/" + exp["net"]
    region = box(exp["region"]["x"][0], exp["region"]["y"][0],
                 exp["region"]["x"][1], exp["region"]["y"][1])
    from shapely.geometry import Polygon
    hits = [v for v in payload["violations"]
            if v["kind"] == "corridor_void" and v["net"] == net
            and v["layer"] == exp["layer"]
            and Polygon(v["polygon"]).intersects(region)]
    assert hits, json.dumps(payload["violations"], indent=1)
    assert all(v["severity"] == "error" for v in hits)
    # the S1-recorded slot crossing: full 1.4 mm slot width
    assert hits[0]["crossing_len_mm"] == pytest.approx(1.4, abs=0.1)


def test_mutant_missing_return_via_caught():
    m = S4_MUTANTS["missing-return-via"]
    payload = run_check("check_return_path", mutant_path("missing-return-via"),
                        m["board"])
    assert payload["status"] == "violations"
    exp = m["expect"]
    hits = [v for v in payload["violations"]
            if v["kind"] == "missing_return_via" and v["net"] == exp["net"]]
    assert len(hits) == 1, json.dumps(payload["violations"], indent=1)
    v = hits[0]
    assert math.hypot(v["pos"][0] - exp["pos"][0],
                      v["pos"][1] - exp["pos"][1]) < 0.5
    assert v["radius_mm"] == pytest.approx(exp["radius_mm"])
    assert v["nearest_ref_via_mm"] > exp["radius_mm"]


def test_mutant_undersized_trace_caught():
    m = S4_MUTANTS["undersized-power-trace"]
    payload = run_check("check_current", mutant_path("undersized-power-trace"),
                        m["board"])
    assert payload["status"] == "violations"
    exp = m["expect"]
    hits = [v for v in payload["violations"]
            if v["kind"] == "undersized_track" and v["net"] == exp["net"]]
    assert len(hits) == 1, json.dumps(payload["violations"], indent=1)
    v = hits[0]
    assert v["width_mm"] == pytest.approx(exp["width_mm"])
    got = [v["segment"]["start"], v["segment"]["end"]]
    want = [exp["segment"]["start"], exp["segment"]["end"]]
    assert got == want or got == want[::-1]
    assert v["required_mm"] > exp["width_mm"]


def test_mutant_decoupler_moved_caught():
    m = S4_MUTANTS["decoupler-moved"]
    payload = run_check("check_decoupling", mutant_path("decoupler-moved"),
                        m["board"])
    assert payload["status"] == "violations"
    exp = m["expect"]
    hits = [v for v in payload["violations"]
            if v["kind"] == "decoupler_distance" and exp["ref"] in v["refs"]]
    assert len(hits) == 1, json.dumps(payload["violations"], indent=1)
    v = hits[0]
    assert v["pin"] == exp["pin"]
    assert v["severity"] == "error"
    # manifest: flag when pad-to-pin exceeds the class threshold (15 mm here);
    # the S1-recorded 15.7 mm is the direct distance, Manhattan is larger
    assert v["euclid_mm"] > exp["min_distance_mm"]
    assert v["manhattan_mm"] > exp["min_distance_mm"]


def test_manifest_s4_coverage():
    """Exactly the four S4 mutants target S4 checks (guards manifest edits)."""
    assert set(S4_MUTANTS) == {"plane-split-under-clock", "missing-return-via",
                               "undersized-power-trace", "decoupler-moved"}


# ============================================================ CLI contract

def test_cli_out_and_exit_codes(tmp_path):
    out = tmp_path / "report.json"
    proc = subprocess.run(
        [PYTHON, str(SCRIPTS / "check_return_path.py"),
         "--pcb", str(board_path("rf4")),
         "--constraints", str(GOLDEN / "rf4" / "constraints.json"),
         "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["script"] == "check_return_path"
    assert payload["status"] == "pass"
    proc = subprocess.run(
        [PYTHON, str(SCRIPTS / "check_current.py"),
         "--pcb", str(mutant_path("undersized-power-trace")),
         "--constraints", str(GOLDEN / "blinky2" / "constraints.json")],
        capture_output=True, text=True)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    v = payload["violations"][0]
    for key in ("check", "severity", "pos", "layer", "net", "refs", "msg",
                "source", "items"):
        assert key in v            # S2 normalized schema
    proc = subprocess.run(
        [PYTHON, str(SCRIPTS / "check_decoupling.py"),
         "--pcb", str(board_path("blinky2")),
         "--metadata", str(tmp_path / "missing.json")],
        capture_output=True, text=True)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["status"] == "error"


# ============================================================ smoke: timing

@pytest.mark.smoke
def test_performance_rf4_under_30s():
    t0 = time.perf_counter()
    for script in sorted(S4_CHECKS):
        geom._CACHE.clear()        # cold parse each time, like a fresh CLI run
        run_check(script, board_path("rf4"), "rf4")
    elapsed = time.perf_counter() - t0
    assert_under(elapsed, 30.0, "S4 checks on rf4")
