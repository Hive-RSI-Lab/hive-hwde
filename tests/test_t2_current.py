"""T2 regression tests for check_current gate blind spots.

Derived from LEARNINGS.md:
 - 2026-07-28 [routing][check_current]: via-count rule is net-wide; overrides
   fed only track widths (a 1 A fuse tap on a 5 A net needed 10 vias).
 - 2026-07-29 [check_current][gates]: plane-fed rail = every via is a 1-via
   leaf cluster (27 unsatisfiable clusters on lumina-carrier +3V3).
 - 2026-07-29 [check_current][gates]: no bridge awareness - undersized_track
   could not say whether a parallel same-net path exists.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
PYTHON = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_current  # noqa: E402
import geom  # noqa: E402


# ---- synthetic boards (helper copied from tests/test_checks.py) ------------

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


def _kinds(vs, kind):
    return [v for v in vs if v["kind"] == kind]


# ---- fixture A: plane-fed rail (LEARNINGS 2026-07-29 shape) ----------------
# Full B.Cu plane on +3V3; three 1-via leaf taps >2 mm apart, each with a
# short 0.3 mm F.Cu escape. At 1.0 A / 0.5 A-per-via every cluster needs 2.

PLANE_FED_BODY = """  (zone (net "+3V3") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
  (via (at 3 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "+3V3"))
  (via (at 10 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "+3V3"))
  (via (at 17 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "+3V3"))
  (segment (start 3 5) (end 3.8 5) (width 0.3) (layer "F.Cu") (net "+3V3"))
  (segment (start 10 5) (end 10.8 5) (width 0.3) (layer "F.Cu") (net "+3V3"))
  (segment (start 17 5) (end 17.8 5) (width 0.3) (layer "F.Cu") (net "+3V3"))
"""

ENTRY_A = {"net": "+3V3", "current_a": 1.0, "via_amps": 0.5}


@pytest.fixture(scope="module")
def plane_fed_bg(tmp_path_factory):
    return _board(tmp_path_factory, "planefed", PLANE_FED_BODY)


def test_plane_fed_absent_keeps_old_errors(plane_fed_bg):
    """Control: without plane_fed the three 1-via taps are hard errors."""
    vs, facts = check_current.check_net(plane_fed_bg, dict(ENTRY_A))
    weak = _kinds(vs, "insufficient_transition_vias")
    assert len(weak) == 3
    assert all(v["severity"] == "error" for v in weak)
    assert all(v["required"] == 2 and v["vias"] == 1 for v in weak)
    assert {tuple(v["pos"]) for v in weak} == {(3.0, 5.0), (10.0, 5.0),
                                               (17.0, 5.0)}
    assert "advisory" not in weak[0]
    assert "plane_fed" not in facts


def test_plane_fed_downgrades_to_advisory(plane_fed_bg):
    vs, facts = check_current.check_net(
        plane_fed_bg, dict(ENTRY_A, plane_fed=True))
    weak = _kinds(vs, "insufficient_transition_vias")
    assert len(weak) == 3
    assert all(v["severity"] == "warning" for v in weak)
    assert all(v["advisory"] is True for v in weak)
    assert {tuple(v["pos"]) for v in weak} == {(3.0, 5.0), (10.0, 5.0),
                                               (17.0, 5.0)}
    # 0.3 mm escapes at the 1.0 A full-budget screen are advisory too
    thin = _kinds(vs, "undersized_track")
    assert thin and all(v["severity"] == "warning" and v["advisory"] is True
                        for v in thin)
    # violations reported, but nothing at error severity
    assert vs and not any(v["severity"] == "error" for v in vs)
    assert facts["plane_fed"] is True
    assert facts["advisory_violations"] == len(vs) == 6


def test_plane_fed_override_region_stays_error(plane_fed_bg):
    """The regulator-feed tap declared via an override stays enforceable."""
    entry = dict(ENTRY_A, plane_fed=True,
                 overrides=[{"near": [3, 5], "radius_mm": 1.0,
                             "current_a": 1.0}])
    vs, _ = check_current.check_net(plane_fed_bg, entry)
    weak = _kinds(vs, "insufficient_transition_vias")
    assert len(weak) == 3
    by_pos = {tuple(v["pos"]): v for v in weak}
    tap1 = by_pos[(3.0, 5.0)]
    assert tap1["severity"] == "error" and tap1["required"] == 2
    assert "advisory" not in tap1
    for pos in [(10.0, 5.0), (17.0, 5.0)]:
        assert by_pos[pos]["severity"] == "warning"
        assert by_pos[pos]["advisory"] is True


def test_plane_fed_clean_rail_passes(tmp_path_factory):
    """A plane-fed rail with adequate copper stays status pass."""
    body = """  (zone (net "+3V3") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
  (via (at 10 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "+3V3"))
  (segment (start 10 5) (end 12 5) (width 0.5) (layer "F.Cu") (net "+3V3"))
"""
    bg = _board(tmp_path_factory, "planeok", body)
    entry = {"net": "+3V3", "current_a": 0.4, "via_amps": 0.5,
             "plane_fed": True}
    vs, facts = check_current.check_net(bg, entry)
    assert vs == []
    assert facts["plane_fed"] is True
    assert facts["advisory_violations"] == 0
    cons = bg.path.parent / "c.json"
    cons.write_text(json.dumps({"power": [entry]}), encoding="utf-8")
    payload, out = check_current.run(
        ["--pcb", str(bg.path), "--constraints", str(cons)])
    assert payload["status"] == "pass" and out is None


# ---- fixture B: declared plane-fed without a plane -------------------------

def test_plane_missing_is_error(tmp_path_factory):
    body = ('  (segment (start 1 5) (end 5 5) (width 1) (layer "F.Cu") '
            '(net "+3V3"))\n')
    bg = _board(tmp_path_factory, "noplane", body)
    vs, facts = check_current.check_net(
        bg, {"net": "+3V3", "current_a": 0.5, "plane_fed": True})
    assert [v["kind"] for v in vs] == ["plane_missing"]
    v = vs[0]
    assert v["severity"] == "error" and v["net"] == "+3V3"
    assert v["pos"] is not None and len(v["pos"]) == 2  # net-copper point
    assert facts["plane_fed"] is True
    assert facts["advisory_violations"] == 0


# ---- fixture C: overrides reach via clusters (LEARNINGS 2026-07-28) --------

def test_override_reaches_via_cluster(tmp_path_factory):
    """A 0.4 A branch tap on a 5 A net no longer needs 10 vias."""
    body = (
        '  (via (at 4 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
        '(net "VBUS"))\n'
        '  (via (at 16 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
        '(net "VBUS"))\n')
    bg = _board(tmp_path_factory, "viaover", body)
    entry = {"net": "VBUS", "current_a": 5.0, "via_amps": 0.5,
             "overrides": [{"near": [4, 5], "radius_mm": 1.0,
                            "current_a": 0.4}]}
    vs, _ = check_current.check_net(bg, entry)
    weak = _kinds(vs, "insufficient_transition_vias")
    assert len(weak) == 1          # tap cluster passes at 0.4 A (need 1 via)
    assert weak[0]["pos"] == [16.0, 5.0]
    assert weak[0]["severity"] == "error" and weak[0]["required"] == 10
    # control: without the override both clusters demand 10 vias (old rule)
    vs2, _ = check_current.check_net(
        bg, {"net": "VBUS", "current_a": 5.0, "via_amps": 0.5})
    assert len(_kinds(vs2, "insufficient_transition_vias")) == 2


# dumbbell pour (copied from tests/test_checks.py): two 5x5 lobes joined by
# a 0.2 mm strip; the neck fails a 0.5 A budget (req 0.25 mm)
DUMBBELL = """  (zone (net "PWR") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 15 0) (xy 15 5) (xy 0 5)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 5 0) (xy 5 2.4) (xy 10 2.4) (xy 10 0) (xy 15 0)
           (xy 15 5) (xy 10 5) (xy 10 2.6) (xy 5 2.6) (xy 5 5) (xy 0 5))))
  (via (at 2 2) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))
  (via (at 13 2) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))
"""


def test_override_drops_pour_neck(tmp_path_factory):
    """A failing neck whose reported pos sits in an override region is
    re-tested at the override requirement and dropped when it passes."""
    bg = _board(tmp_path_factory, "neckover", DUMBBELL)
    base = {"net": "PWR", "current_a": 0.5}
    vs, _ = check_current.check_net(bg, dict(base))
    assert len(_kinds(vs, "pour_neckdown")) == 1     # control
    # region covers the whole pour (neck pos is a split-component sample)
    entry = dict(base, overrides=[{"near": [7.5, 2.5], "radius_mm": 10.0,
                                   "current_a": 0.05}])
    vs2, _ = check_current.check_net(bg, entry)
    assert _kinds(vs2, "pour_neckdown") == []


# ---- fixture D: bridge labeling (LEARNINGS 2026-07-29) ---------------------

_PADS_PWR = """  (footprint "t:U" (at 2 5)
    (layer "F.Cu")
    (property "Reference" "U1" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "PWR")))
  (footprint "t:U" (at 14 5)
    (layer "F.Cu")
    (property "Reference" "U2" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "PWR")))
"""

_DIRECT = '  (segment (start 2 5) (end 14 5) (width 0.2) (layer "F.Cu") (net "PWR"))\n'
_DETOUR = (
    '  (segment (start 2 5) (end 2 8) (width 0.2) (layer "F.Cu") (net "PWR"))\n'
    '  (segment (start 2 8) (end 14 8) (width 0.2) (layer "F.Cu") (net "PWR"))\n'
    '  (segment (start 14 8) (end 14 5) (width 0.2) (layer "F.Cu") (net "PWR"))\n')


def test_parallel_paths_labeled_not_bridge(tmp_path_factory):
    bg = _board(tmp_path_factory, "loop", _PADS_PWR + _DIRECT + _DETOUR)
    vs, facts = check_current.check_net(bg, {"net": "PWR", "current_a": 2.0})
    thin = _kinds(vs, "undersized_track")
    assert len(thin) == 4
    assert all(v["bridge"] is False for v in thin)
    assert all(v["severity"] == "error" for v in thin)  # label, not a waiver
    assert facts["bridge_labeled"] is True


def test_sole_path_labeled_bridge(tmp_path_factory):
    bg = _board(tmp_path_factory, "solepath", _PADS_PWR + _DIRECT)
    vs, facts = check_current.check_net(bg, {"net": "PWR", "current_a": 2.0})
    thin = _kinds(vs, "undersized_track")
    assert len(thin) == 1
    assert thin[0]["bridge"] is True
    assert facts["bridge_labeled"] is True


def test_zone_parallel_path_not_bridge(tmp_path_factory):
    """A segment paralleled by the net's own pour (via through-vias at both
    ends) is not a bridge when the graph includes zone fills, and since both
    ends sit on the pour it is dropped as a pour tap, not reported."""
    body = _DIRECT + (
        '  (via (at 2 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
        '(net "PWR"))\n'
        '  (via (at 14 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
        '(net "PWR"))\n'
        """  (zone (net "PWR") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
""")
    bg = _board(tmp_path_factory, "zonepar", body)
    vs, facts = check_current.check_net(bg, {"net": "PWR", "current_a": 2.0})
    assert _kinds(vs, "undersized_track") == []
    assert facts["pour_taps"] == [[8.0, 5.0]]
    assert facts["bridge_labeled"] is True


# ---- pour taps (PCB-0018-A PHASE/LS_SRC class) -----------------------------
# A 10 A pour on F.Cu holds the FET pad Q1.1; a 0.25 mm signal tap leaves it.

_TAP_POUR = """  (zone (net "PH") (layer "F.Cu")
    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10))))
  (footprint "t:Q" (at 5 5)
    (layer "F.Cu")
    (property "Reference" "Q1" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 2 2) (layers "F.Cu") (net "PH")))
  (footprint "t:R" (at 15 5)
    (layer "F.Cu")
    (property "Reference" "R1" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 0.8 0.8) (layers "F.Cu") (net "PH")))
"""

ENTRY_PH = {"net": "PH", "current_a": 10.0}


def test_tap_inside_own_pour_dropped(tmp_path_factory):
    """A thin track whose both ends sit in the pour that carries the current
    (pad to pad across it) is shunted by it: no finding."""
    body = _TAP_POUR + (
        '  (segment (start 5 5) (end 5 8) (width 0.25) (layer "F.Cu") '
        '(net "PH"))\n')
    bg = _board(tmp_path_factory, "pourtap", body)
    vs, facts = check_current.check_net(bg, ENTRY_PH)
    assert _kinds(vs, "undersized_track") == []
    assert len(facts["pour_taps"]) == 1


def test_stub_inside_pad_dropped(tmp_path_factory):
    """A non-bridge stub drawn wholly over its pad adds no section."""
    body = _TAP_POUR + (
        '  (segment (start 15 5) (end 18 5) (width 0.25) (layer "F.Cu") '
        '(net "PH"))\n'
        '  (segment (start 15 5) (end 18 5) (width 0.25) (layer "B.Cu") '
        '(net "PH"))\n'
        '  (via (at 18 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
        '(net "PH"))\n'
        '  (segment (start 15 5) (end 15.2 5) (width 0.25) (layer "F.Cu") '
        '(net "PH"))\n')
    bg = _board(tmp_path_factory, "padstub", body)
    vs, facts = check_current.check_net(bg, ENTRY_PH)
    thin = _kinds(vs, "undersized_track")
    # the two 3 mm legs stay: they leave the pad copper and no pour shunts them
    assert sorted(v["layer"] for v in thin) == ["B.Cu", "F.Cu"]
    assert all(v["pos"] == [16.5, 5.0] for v in thin)
    assert facts["pour_taps"] == [[15.1, 5.0]]


def test_sole_path_tap_out_of_pour_still_fails(tmp_path_factory):
    """The same tap leaving the pour to reach R1 is the only path there: a
    genuine bridge keeps its error, whatever it feeds."""
    body = _TAP_POUR + (
        '  (segment (start 5 5) (end 15 5) (width 0.25) (layer "F.Cu") '
        '(net "PH"))\n')
    bg = _board(tmp_path_factory, "bridgetap", body)
    vs, facts = check_current.check_net(bg, ENTRY_PH)
    [v] = _kinds(vs, "undersized_track")
    assert v["bridge"] is True and v["severity"] == "error"
    assert "pour_taps" not in facts


def test_no_undersized_no_bridge_fact(tmp_path_factory):
    """bridge_labeled only appears when labeling actually ran."""
    body = _PADS_PWR + \
        '  (segment (start 2 5) (end 14 5) (width 1.2) (layer "F.Cu") (net "PWR"))\n'
    bg = _board(tmp_path_factory, "wideok", body)
    vs, facts = check_current.check_net(bg, {"net": "PWR", "current_a": 2.0})
    assert _kinds(vs, "undersized_track") == []
    assert "bridge_labeled" not in facts


# ---- T6 (P8B-3): derived return-net coverage -------------------------------
# The pd-trigger 5A GND choke class: no board declares its return net, so
# return-path ampacity was unchecked. A >=3A rail synthesizes ONE plane-fed
# entry for the return net; ALL derived findings are advisory warnings.

_GND_RETURN_BODY = """  (segment (start 1 1) (end 19 1) (width 2.0) (layer "F.Cu") (net "VBUS"))
  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 15 0) (xy 15 5) (xy 0 5)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 5 0) (xy 5 2.4) (xy 10 2.4) (xy 10 0) (xy 15 0)
           (xy 15 5) (xy 10 5) (xy 10 2.6) (xy 5 2.6) (xy 5 5) (xy 0 5))))
  (via (at 2 2) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
  (via (at 13 2) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
  (segment (start 2 2) (end 2.8 2) (width 0.3) (layer "F.Cu") (net "GND"))
"""


def _run_cli(bg, power):
    cons = bg.path.parent / "cons.json"
    cons.write_text(json.dumps({"power": power}), encoding="utf-8")
    return check_current.run(["--pcb", str(bg.path),
                              "--constraints", str(cons)])[0]


def test_derived_return_fires_at_5a(tmp_path_factory):
    bg = _board(tmp_path_factory, "gndret", _GND_RETURN_BODY)
    payload = _run_cli(bg, [{"net": "VBUS", "current_a": 5.0}])
    derived = [e for e in payload["checked"] if e.get("derived")]
    assert len(derived) == 1 and derived[0]["net"] == "GND"
    gnd_vs = [v for v in payload["violations"] if v["net"] == "GND"]
    assert gnd_vs, "derived GND coverage produced no findings"
    # the choke class is visible: pour neck between the via attachments
    necks = [v for v in gnd_vs if v["kind"] == "pour_neckdown"]
    assert len(necks) == 1
    # ...and EVERY derived finding is an advisory warning, never an error
    assert all(v["severity"] == "warning" for v in gnd_vs)
    assert all(v["derived"] is True for v in gnd_vs)
    assert necks[0]["advisory"] is True
    assert "derived return-net coverage" in necks[0]["msg"]


def test_derived_return_below_threshold_skipped(tmp_path_factory):
    bg = _board(tmp_path_factory, "gndlow", _GND_RETURN_BODY)
    payload = _run_cli(bg, [{"net": "VBUS", "current_a": 2.0}])
    assert not any(e.get("derived") for e in payload["checked"])
    assert not any(v["net"] == "GND" for v in payload["violations"])


def test_derived_return_skipped_when_declared(tmp_path_factory):
    """An explicitly declared return net is the owner's judgment - no
    synthesis on top of it."""
    bg = _board(tmp_path_factory, "gnddecl", _GND_RETURN_BODY)
    payload = _run_cli(bg, [{"net": "VBUS", "current_a": 5.0},
                            {"net": "GND", "current_a": 5.0,
                             "plane_fed": True}])
    assert not any(e.get("derived") for e in payload["checked"])
    # the declared entry still runs (pour neck at ERROR: declared plane_fed)
    necks = [v for v in payload["violations"] if v["kind"] == "pour_neckdown"]
    assert necks and all(v["severity"] == "error" for v in necks)


def test_derived_return_needs_a_zone(tmp_path_factory):
    """Routed-only return (no zone fill) is not judgeable at plane-fed
    semantics - no synthesis, no noise."""
    body = ('  (segment (start 1 1) (end 19 1) (width 2.0) (layer "F.Cu") '
            '(net "VBUS"))\n'
            '  (segment (start 1 5) (end 19 5) (width 0.3) (layer "F.Cu") '
            '(net "GND"))\n')
    bg = _board(tmp_path_factory, "gndtrk", body)
    payload = _run_cli(bg, [{"net": "VBUS", "current_a": 5.0}])
    assert not any(e.get("derived") for e in payload["checked"])
    assert not any(v["net"] == "GND" for v in payload["violations"])


def test_derived_return_net_field_overrides_default(tmp_path_factory):
    body = _GND_RETURN_BODY.replace('"GND"', '"AGND"')
    bg = _board(tmp_path_factory, "agnd", body)
    payload = _run_cli(bg, [{"net": "VBUS", "current_a": 5.0,
                             "return_net": "AGND"}])
    derived = [e for e in payload["checked"] if e.get("derived")]
    assert len(derived) == 1 and derived[0]["net"] == "AGND"


# ---- T6 (P8A-4): plane_fed_candidate hint (facts only) ---------------------

def test_plane_fed_candidate_hint(plane_fed_bg):
    """The plane-fed shape without the key gets the facts hint; severities
    are untouched (still the old errors - the hint is machine-visible only)."""
    vs, facts = check_current.check_net(plane_fed_bg, dict(ENTRY_A))
    assert facts["plane_fed_candidate"] is True
    assert any(v["severity"] == "error" for v in vs)  # unchanged behavior


def test_no_hint_when_plane_fed_declared(plane_fed_bg):
    _, facts = check_current.check_net(
        plane_fed_bg, dict(ENTRY_A, plane_fed=True))
    assert "plane_fed_candidate" not in facts


def test_no_hint_without_zone(tmp_path_factory):
    body = _PADS_PWR + _DIRECT + (
        '  (via (at 2 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
        '(net "PWR"))\n')
    bg = _board(tmp_path_factory, "nohint", body)
    _, facts = check_current.check_net(bg, {"net": "PWR", "current_a": 2.0})
    assert "plane_fed_candidate" not in facts


# ---- exit codes / run() CLI contract ---------------------------------------

def test_cli_exit_codes(tmp_path_factory, plane_fed_bg):
    script = SCRIPTS / "check_current.py"
    # violations (advisory-only still exits 1: status is "violations")
    cons = plane_fed_bg.path.parent / "pf.json"
    cons.write_text(json.dumps(
        {"power": [dict(ENTRY_A, plane_fed=True)]}), encoding="utf-8")
    proc = subprocess.run(
        [PYTHON, str(script), "--pcb", str(plane_fed_bg.path),
         "--constraints", str(cons)], capture_output=True, text=True)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["status"] == "violations"
    assert payload["counts"]["by_severity"] == {"warning": 6}
    assert payload["checked"][0]["plane_fed"] is True
    # error: net not on board -> exit 2
    cons2 = plane_fed_bg.path.parent / "ghost.json"
    cons2.write_text(json.dumps(
        {"power": [{"net": "/GHOST", "current_a": 1.0}]}), encoding="utf-8")
    proc2 = subprocess.run(
        [PYTHON, str(script), "--pcb", str(plane_fed_bg.path),
         "--constraints", str(cons2)], capture_output=True, text=True)
    assert proc2.returncode == 2
    assert json.loads(proc2.stdout)["status"] == "error"


# ---- pad-exit necks (PCB-0019-A SW_L1/SW_L2 class) -------------------------
# A 0.5 mm-pitch pin row: 0.85 x 0.24 mm pads at y 4.5 / 5.0 / 5.5, the middle
# one on SW. At 1.0 A the requirement is 0.5 mm, which cannot fit between the
# neighbours; the track leaves the pad narrower and widens after a via.

def _pin_row(mid_size="0.85 0.24"):
    return f"""  (footprint "t:QFN" (at 5 5)
    (layer "F.Cu")
    (property "Reference" "U3" (at 0 0 0))
    (pad "1" smd rect (at 0 -0.5) (size 0.85 0.24) (layers "F.Cu") (net "A"))
    (pad "2" smd rect (at 0 0) (size {mid_size}) (layers "F.Cu") (net "SW"))
    (pad "3" smd rect (at 0 0.5) (size 0.85 0.24) (layers "F.Cu") (net "B")))
"""


def _exit(neck_end_x: float, neck_w: float = 0.3) -> str:
    """Neck from the pad centre to x=neck_end_x, a via there, then a full
    0.5 mm track on B.Cu to x=1."""
    return (
        f'  (segment (start 5 5) (end {neck_end_x} 5) (width {neck_w}) '
        '(layer "F.Cu") (net "SW"))\n'
        f'  (via (at {neck_end_x} 5) (size 0.6) (drill 0.3) '
        '(layers "F.Cu" "B.Cu") (net "SW"))\n'
        f'  (segment (start {neck_end_x} 5) (end 1 5) (width 0.5) '
        '(layer "B.Cu") (net "SW"))\n')


ENTRY_SW = {"net": "SW", "current_a": 1.0}


def test_pad_exit_neck_accepted(tmp_path_factory):
    """0.30 mm off a 0.24 mm pad, ~0.3 mm clear of the pad before the via:
    the neck the pin pitch forces is not a finding (PCB-0019-A SW_L1)."""
    bg = _board(tmp_path_factory, "padexit", _pin_row() + _exit(4.0))
    vs, facts = check_current.check_net(bg, ENTRY_SW)
    assert _kinds(vs, "undersized_track") == []
    [info] = facts["pad_exit_necks"]
    assert info["pad"] == "U3.2" and info["pad_width_mm"] == 0.24
    assert info["length_mm"] <= check_current.PAD_EXIT_MAX_MM


def test_long_pad_exit_neck_still_fails(tmp_path_factory):
    """The same neck run 2.5 mm past the pad is a routed trace, not an
    escape: it must meet the full width."""
    bg = _board(tmp_path_factory, "padexitlong", _pin_row() + _exit(2.0))
    vs, facts = check_current.check_net(bg, ENTRY_SW)
    assert len(_kinds(vs, "undersized_track")) == 1
    assert "pad_exit_necks" not in facts


def test_neck_narrower_than_pad_still_fails(tmp_path_factory):
    """0.20 mm off a 0.24 mm pad adds a tighter section than the pad."""
    bg = _board(tmp_path_factory, "padexitthin",
                _pin_row() + _exit(4.0, neck_w=0.2))
    vs, _ = check_current.check_net(bg, ENTRY_SW)
    assert len(_kinds(vs, "undersized_track")) == 1


def test_neck_off_wide_pad_still_fails(tmp_path_factory):
    """A pad as wide as the requirement forces nothing: a short 0.3 mm stub
    off a 1 x 1 mm pad is still undersized."""
    bg = _board(tmp_path_factory, "padexitwide",
                _pin_row(mid_size="1 1") + _exit(4.0))
    vs, _ = check_current.check_net(bg, ENTRY_SW)
    assert len(_kinds(vs, "undersized_track")) == 1


# ---- via-stitched pours (PCB-0017-B GND class) -----------------------------
# F.Cu GND split into two islands with a via each; whether that is a neck
# depends on the B.Cu GND fill the vias land in. 2 A needs 1.1 mm.

_SPLIT_FCU = """  (zone (net "GND") (layer "F.Cu")
    (polygon (pts (xy 0 0) (xy 15 0) (xy 15 5) (xy 0 5)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 0 0) (xy 6 0) (xy 6 5) (xy 0 5)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 9 0) (xy 15 0) (xy 15 5) (xy 9 5))))
  (via (at 3 2.5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
  (via (at 12 2.5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
"""

_BCU_SOLID = """  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 15 0) (xy 15 5) (xy 0 5)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 15 0) (xy 15 5) (xy 0 5))))
"""

# B.Cu dumbbell: the stitched path itself chokes through a 0.2 mm strip
_BCU_NECKED = """  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 15 0) (xy 15 5) (xy 0 5)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 5 0) (xy 5 2.4) (xy 10 2.4) (xy 10 0) (xy 15 0)
           (xy 15 5) (xy 10 5) (xy 10 2.6) (xy 5 2.6) (xy 5 5) (xy 0 5))))
"""

ENTRY_GND = {"net": "GND", "current_a": 2.0}


def test_split_fcu_stitched_to_solid_bcu_passes(tmp_path_factory):
    """Each F.Cu island reaches the one B.Cu pour through its via: no neck
    (used to report ~0.00 mm on the F.Cu fill)."""
    bg = _board(tmp_path_factory, "stitched", _SPLIT_FCU + _BCU_SOLID)
    vs, _ = check_current.check_net(bg, ENTRY_GND)
    assert _kinds(vs, "pour_neckdown") == []


def test_split_fcu_without_other_layer_fails(tmp_path_factory):
    """No other fill to stitch through: the split is a real break."""
    bg = _board(tmp_path_factory, "splitonly", _SPLIT_FCU)
    vs, _ = check_current.check_net(bg, ENTRY_GND)
    [neck] = _kinds(vs, "pour_neckdown")
    assert neck["layer"] == "F.Cu"


def test_split_fcu_stitched_to_necked_bcu_fails(tmp_path_factory):
    """Stitching only helps when the other layer carries the width: a B.Cu
    path that necks to 0.2 mm leaves both fills flagged."""
    bg = _board(tmp_path_factory, "stitchneck", _SPLIT_FCU + _BCU_NECKED)
    vs, _ = check_current.check_net(bg, ENTRY_GND)
    necks = _kinds(vs, "pour_neckdown")
    assert {v["layer"] for v in necks} == {"F.Cu", "B.Cu"}
    assert all(v["severity"] == "error" for v in necks)


# ---- stitch vias are not layer transitions (PCB-0023-A /SW, 2026-10-06) ----
# /SW was one pour on all four layers of a GaN inverter, stitched by ~120
# vias >2 mm apart. Every stitch via was its own 1-via "layer transition"
# needing 24 vias at 12 A: 112 errors, all model artefacts. A via that only
# joins same-net pours is now judged with every other via joining the same
# two pours, and not at all when one pour is a dead end; a via a track or a
# loose pad feeds still counts on its own.

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


def _pour(net: str, layer: str, x1: float = 12) -> str:
    return (f'  (zone (net "{net}") (layer "{layer}")\n'
            f'    (polygon (pts (xy 0 0) (xy {x1} 0) (xy {x1} 10) (xy 0 10)))\n'
            f'    (filled_polygon (layer "{layer}")\n'
            f'      (pts (xy 0 0) (xy {x1} 0) (xy {x1} 10) (xy 0 10))))\n')


def _via(x: float, y: float, net: str = "/SW") -> str:
    return (f'  (via (at {x} {y}) (size 0.6) (drill 0.3) '
            f'(layers "F.Cu" "B.Cu") (net "{net}"))\n')


def _smd(ref: str, x: float, y: float, layer: str, net: str = "/SW") -> str:
    return (f'  (footprint "t:Q" (at {x} {y}) (layer "{layer}")\n'
            f'    (property "Reference" "{ref}" (at 0 0 0))\n'
            f'    (pad "1" smd rect (at 0 0) (size 1 1) (layers "{layer}") '
            f'(net "{net}")))\n')


_ALL4 = "".join(_pour("/SW", l) for l in ("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"))
# eight 1-via stitch clusters, rows 6 mm apart, 2.5 mm pitch
_ROW = [(x, y) for y in (2, 8) for x in (1, 3.5, 6, 8.5)]
_STITCH8 = "".join(_via(x, y) for x, y in _ROW)
_STITCH4 = "".join(_via(x, y) for x, y in _ROW[:4])
# a /SW track that really hops F.Cu -> B.Cu through one via, off the pours
_HOP = ('  (segment (start 14 5) (end 16 5) (width 0.5) (layer "F.Cu") '
        '(net "/SW"))\n' + _via(16, 5) +
        '  (segment (start 16 5) (end 19 5) (width 0.5) (layer "B.Cu") '
        '(net "/SW"))\n')
SW_12A = {"net": "/SW", "current_a": 12.0, "via_amps": 0.5}


def test_stitch_vias_on_four_layers_are_not_transitions(tmp_path_factory):
    """Both terminals on the F.Cu pour; In1/In2/B.Cu pours only parallel
    it through the stitch array: zero transition findings at 12 A."""
    bg = _board4(tmp_path_factory, "stitch4",
                 _ALL4 + _STITCH8 + _smd("Q1", 11, 5, "F.Cu")
                 + _smd("L1", 5, 5, "F.Cu"))
    vs, facts = check_current.check_net(bg, dict(SW_12A))
    assert _kinds(vs, "insufficient_transition_vias") == []
    assert facts["stitch_vias"] == 8 and facts["via_clusters"] == 8


def test_track_layer_hop_still_flagged_beside_stitch_array(tmp_path_factory):
    """Same /SW net: the stitch array stays silent, the via a track uses to
    change layers is still a 1-via transition needing 24 vias."""
    bg = _board4(tmp_path_factory, "stitchhop",
                 _ALL4 + _STITCH8 + _smd("Q1", 11, 5, "F.Cu")
                 + _smd("L1", 5, 5, "F.Cu") + _HOP)
    vs, _ = check_current.check_net(bg, dict(SW_12A))
    [hop] = _kinds(vs, "insufficient_transition_vias")
    assert hop["pos"] == [16.0, 5.0] and hop["severity"] == "error"
    assert hop["vias"] == 1 and hop["required"] == 24
    assert "stitch" not in hop


def test_pour_to_pour_hop_counts_the_whole_stitch_array(tmp_path_factory):
    """Terminals on F.Cu and B.Cu: the load current crosses the array, so
    every via joining those two pours is counted together (threshold
    unchanged: 1 via per 0.5 A)."""
    body = (_ALL4 + _STITCH8 + _smd("Q1", 11, 5, "F.Cu")
            + _smd("L1", 5, 5, "B.Cu"))
    bg = _board4(tmp_path_factory, "stitchfb", body)
    vs, _ = check_current.check_net(
        bg, {"net": "/SW", "current_a": 3.0, "via_amps": 0.5})
    assert _kinds(vs, "insufficient_transition_vias") == []   # 8 >= 6
    vs, _ = check_current.check_net(bg, dict(SW_12A))
    [hop] = _kinds(vs, "insufficient_transition_vias")
    assert hop["stitch"] is True and hop["severity"] == "error"
    assert hop["vias"] == 8 and hop["required"] == 24


def test_plated_holes_count_toward_a_pour_to_pour_hop(tmp_path_factory):
    """Four stitch vias at 3 A (need 6) fail; two plated through-holes
    joining the same two pours make up the count."""
    base = (_ALL4 + _STITCH4 + _smd("Q1", 11, 5, "F.Cu")
            + _smd("L1", 5, 5, "B.Cu"))
    entry = {"net": "/SW", "current_a": 3.0, "via_amps": 0.5}
    vs, _ = check_current.check_net(_board4(tmp_path_factory, "pth0", base),
                                    entry)
    [hop] = _kinds(vs, "insufficient_transition_vias")
    assert hop["stitch"] is True and hop["vias"] == 4
    pth = ''.join(
        f'  (footprint "t:J" (at {x} 8) (layer "F.Cu")\n'
        f'    (property "Reference" "J{i}" (at 0 0 0))\n'
        f'    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2) (drill 0.6)'
        f' (layers "*.Cu") (net "/SW")))\n' for i, x in ((1, 2), (2, 7)))
    vs, _ = check_current.check_net(
        _board4(tmp_path_factory, "pth2", base + pth), entry)
    assert _kinds(vs, "insufficient_transition_vias") == []


def test_via_in_a_loose_pad_is_a_transition(tmp_path_factory):
    """A pad with no pour around it on its own layer feeds its via: that via
    is a layer transition, not a stitch. The same pad sitting in an F.Cu
    pour turns the via into a stitch to dead-end pours."""
    body = (_pour("/SW", "In1.Cu") + _pour("/SW", "B.Cu")
            + _smd("Q1", 5, 5, "F.Cu") + _via(5, 5))
    entry = {"net": "/SW", "current_a": 1.0, "via_amps": 0.5}
    vs, facts = check_current.check_net(
        _board4(tmp_path_factory, "loosepad", body), entry)
    [hop] = _kinds(vs, "insufficient_transition_vias")
    assert hop["pos"] == [5.0, 5.0] and "stitch" not in hop
    assert "stitch_vias" not in facts
    vs, facts = check_current.check_net(
        _board4(tmp_path_factory, "pourpad", body + _pour("/SW", "F.Cu")),
        entry)
    assert _kinds(vs, "insufficient_transition_vias") == []
    assert facts["stitch_vias"] == 1


# F.Cu /SW pour with a thermal relief round Q1's pad (hole 4..6, one spoke
# on +x that stops 0.1 mm short of the via): the pad reaches the pour, the
# via inside it touches no F.Cu fill.
_RELIEF_FCU = """  (zone (net "/SW") (layer "F.Cu")
    (polygon (pts (xy 0 0) (xy 12 0) (xy 12 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 0 0) (xy 12 0) (xy 12 10) (xy 0 10) (xy 0 5.1) (xy 4 5.1)
           (xy 4 6) (xy 6 6) (xy 6 4) (xy 4 4) (xy 4 4.9) (xy 0 4.9)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 5.4 4.85) (xy 6.05 4.85) (xy 6.05 5.15) (xy 5.4 5.15))))
"""


def test_via_in_a_thermal_relief_pad_is_a_transition(tmp_path_factory):
    """Review of the stitch rule: Q1's pad sits in the F.Cu pour through a
    spoke, but its via touches no F.Cu fill, so all 12 A from Q1 to L1 on
    B.Cu cross that one via. It is a transition, not a stitch, and fails."""
    body = (_RELIEF_FCU + "".join(_pour("/SW", l)
                                  for l in ("In1.Cu", "In2.Cu", "B.Cu"))
            + _smd("Q1", 5, 5, "F.Cu") + _via(5, 5)
            + _smd("L1", 10, 5, "B.Cu"))
    vs, facts = check_current.check_net(
        _board4(tmp_path_factory, "reliefvia", body), dict(SW_12A))
    [hop] = _kinds(vs, "insufficient_transition_vias")
    assert hop["pos"] == [5.0, 5.0] and "stitch" not in hop
    assert hop["severity"] == "error" and hop["required"] == 24
    assert "stitch_vias" not in facts


def test_via_straddling_a_relief_pad_edge_is_a_transition(tmp_path_factory):
    """Review of the relief rule (PCB-0023-A D201): Q1's only via sits on
    its pad's edge, so part of its ring reaches the F.Cu pour past the
    relief gap. The pad itself is ~3% covered by fill (one spoke), not in
    the pour, so its 12 A to L1 on B.Cu still cross that one via. Before,
    the via counted as a stitch and the 24-via array beside it made the
    F.Cu-B.Cu hop pass at 12 A."""
    array = "".join(_via(x, y) for y in (1, 2.5, 7.5, 9)
                    for x in (1, 2.5, 4, 5.5, 7, 8.5))
    body = (_RELIEF_FCU + "".join(_pour("/SW", l)
                                  for l in ("In1.Cu", "In2.Cu", "B.Cu"))
            + _smd("Q1", 5, 5, "F.Cu") + _via(5.75, 5) + array
            + _smd("L1", 10, 5, "B.Cu"))
    bg = _board4(tmp_path_factory, "reliefedge", body)
    [via] = [v for v in bg.vias_of("/SW") if v.poly.intersects(Point(5.75, 5))]
    [pad] = bg.pads_of("/SW", "F.Cu")
    fcu = bg.zone_fill("/SW", "F.Cu")
    assert via.poly.intersects(pad.poly) and via.poly.intersects(fcu)
    assert fcu.intersection(pad.poly).area < 0.1 * pad.poly.area
    vs, facts = check_current.check_net(bg, dict(SW_12A))
    [hop] = _kinds(vs, "insufficient_transition_vias")
    assert hop["pos"] == [5.75, 5.0] and "stitch" not in hop
    assert hop["severity"] == "error" and hop["required"] == 24
    assert facts["stitch_vias"] == 24


def test_copper_graph_cuts_a_skipped_plated_pad(tmp_path_factory):
    """A stitch bundle can hold plated pads, and the leaf bound cuts every
    barrel in it: a skipped plated pad no longer joins F.Cu to B.Cu."""
    pth = ('  (footprint "t:J" (at 5 5) (layer "F.Cu")\n'
           '    (property "Reference" "J1" (at 0 0 0))\n'
           '    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2) (drill 0.6)'
           ' (layers "*.Cu") (net "/SW")))\n')
    bg = _board4(tmp_path_factory, "skippad",
                 _pour("/SW", "F.Cu") + _pour("/SW", "B.Cu") + pth)
    [pad] = [p for p in bg.pads_of("/SW") if p.drill is not None]
    for skip, joined in ((frozenset(), True), (frozenset({id(pad)}), False)):
        nodes, find, _ = check_current._copper_graph(bg, "/SW", skip=skip)
        roots = {l: {find(i) for i, (nl, _) in enumerate(nodes) if nl == l}
                 for l in ("F.Cu", "B.Cu")}
        assert (roots["F.Cu"] == roots["B.Cu"]) is joined


# ---- plated pads join fills through their relief (PCB-0021-A GND) ----------
# F.Cu GND F1 | F2 and B.Cu GND B1 | B2, one via in each pair: F1-B1 and
# F2-B2. J1's plated pad sits in F2's and B1's thermal relief (0.6 mm gap,
# one spoke each), so its centre is in neither fill: it alone joins the two.

_RELIEF_FILLS = """  (zone (net "GND") (layer "F.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 0 0) (xy 8 0) (xy 8 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 12.6 0) (xy 20 0) (xy 20 10) (xy 12.6 10) (xy 12.6 5.2)
           (xy 11.9 5.2) (xy 11.9 4.8) (xy 12.6 4.8))))
  (zone (net "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 0 0) (xy 10.4 0) (xy 10.4 4.8) (xy 11.1 4.8) (xy 11.1 5.2)
           (xy 10.4 5.2) (xy 10.4 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 14 0) (xy 20 0) (xy 20 10) (xy 14 10))))
  (via (at 4 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
  (via (at 17 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "GND"))
"""

_RELIEF_PAD = """  (footprint "t:J" (at 11.5 5)
    (layer "F.Cu")
    (property "Reference" "J1" (at 0 0 0))
    (pad "1" thru_hole circle (at 0 0) (size 1 1) (drill 0.6)
      (layers "*.Cu" "*.Mask") (net "GND")))
"""


def test_plated_pad_in_relief_joins_layers(tmp_path_factory):
    """A plated pad whose fill stops at its thermal relief still joins the
    fills it sits in on each layer (used to need the pad centre in fill)."""
    bg = _board(tmp_path_factory, "relief", _RELIEF_FILLS + _RELIEF_PAD)
    vs, _ = check_current.check_net(bg, ENTRY_GND)
    assert _kinds(vs, "pour_neckdown") == []


def test_relief_fills_without_pad_fail(tmp_path_factory):
    """Control: without J1 the two halves meet nowhere."""
    bg = _board(tmp_path_factory, "reliefnopad", _RELIEF_FILLS)
    vs, _ = check_current.check_net(bg, ENTRY_GND)
    assert {v["layer"] for v in _kinds(vs, "pour_neckdown")} == \
        {"F.Cu", "B.Cu"}


# ---- leaf branches (PCB-0021-A +SYS) ---------------------------------------
# PWR F.Cu pour: a main block holding U1 and a 0.8 mm leg holding R1 pad 1,
# cut apart on F.Cu and bridged by a 0.6 mm B.Cu strip and two single vias
# 4 mm apart. At 2 A the leg, the strip and both 1-via transitions fail;
# the leg only feeds R1 (470R 0603: at most sqrt(0.25/470) = 23 mA).

_LEAF = """  (zone (net "PWR") (layer "F.Cu")
    (polygon (pts (xy 0 0) (xy 16 0) (xy 16 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 13 4.6) (xy 16 4.6) (xy 16 5.4) (xy 13 5.4))))
  (zone (net "PWR") (layer "B.Cu")
    (polygon (pts (xy 9 4.7) (xy 14 4.7) (xy 14 5.3) (xy 9 5.3)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 9 4.7) (xy 14 4.7) (xy 14 5.3) (xy 9 5.3))))
  (via (at 9.5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))
  (via (at 13.5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "PWR"))
  (footprint "t:U" (at 2 5)
    (layer "F.Cu")
    (property "Reference" "U1" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "PWR")))
"""


def _leaf_part(ref="R1", value="470R", lib="t:R0603"):
    return f"""  (footprint "{lib}" (at 15.9 5)
    (layer "F.Cu")
    (property "Reference" "{ref}" (at 0 0 0))
    (property "Value" "{value}" (at 0 0 0))
    (pad "1" smd rect (at -0.4 0) (size 0.5 0.6) (layers "F.Cu") (net "PWR"))
    (pad "2" smd rect (at 0.4 0) (size 0.5 0.6) (layers "F.Cu") (net "LED")))
"""


ENTRY_PWR = {"net": "PWR", "current_a": 2.0}


def test_leaf_branch_judged_at_its_load(tmp_path_factory):
    """The leg's neck and both transitions carry only R1's current: no
    finding, each listed in facts["leaf_branches"] at R1's bound."""
    bg = _board(tmp_path_factory, "leaf", _LEAF + _leaf_part())
    vs, facts = check_current.check_net(bg, ENTRY_PWR)
    assert _kinds(vs, "pour_neckdown") == []
    assert _kinds(vs, "insufficient_transition_vias") == []
    leaves = facts["leaf_branches"]
    assert {b["kind"] for b in leaves} == {"pour_neckdown",
                                           "insufficient_transition_vias"}
    assert all(b["loads"] == ["R1"] for b in leaves)
    assert all(b["current_a"] == pytest.approx(0.0231, abs=1e-3)
               for b in leaves)


def test_leaf_with_unbounded_part_keeps_budget(tmp_path_factory):
    """A capacitor on the leg has no bound: the leg is judged at 2 A."""
    bg = _board(tmp_path_factory, "leafcap",
                _LEAF + _leaf_part("C1", "10uF", "t:C0603"))
    vs, facts = check_current.check_net(bg, ENTRY_PWR)
    assert _kinds(vs, "pour_neckdown")
    assert len(_kinds(vs, "insufficient_transition_vias")) == 2
    assert all(v["current_a"] == 2.0 for v in _kinds(vs, "pour_neckdown"))
    assert "leaf_branches" not in facts


def test_leaf_with_second_path_keeps_budget(tmp_path_factory):
    """A track from the leg back to the main block makes the leg a possible
    bypass of the neck: no bound, the findings stay at 2 A."""
    track = (
        '  (segment (start 9.9 8) (end 14.5 8) (width 0.3) (layer "F.Cu") (net "PWR"))\n'
        '  (segment (start 14.5 8) (end 14.5 5.2) (width 0.3) (layer "F.Cu") (net "PWR"))\n')
    bg = _board(tmp_path_factory, "leafloop", _LEAF + _leaf_part() + track)
    vs, facts = check_current.check_net(bg, ENTRY_PWR)
    assert _kinds(vs, "pour_neckdown")
    assert len(_kinds(vs, "insufficient_transition_vias")) == 2
    assert "leaf_branches" not in facts


def test_leaf_reaching_real_load_still_fails(tmp_path_factory):
    """Negative: the leg feeds R1 and also U2, a part with no bound, so it
    is on the real power path and the neck and transitions fail at 2 A."""
    u2 = """  (footprint "t:SOT23" (at 14.5 5)
    (layer "F.Cu")
    (property "Reference" "U2" (at 0 0 0))
    (property "Value" "LDO" (at 0 0 0))
    (pad "1" smd rect (at 0 0) (size 0.5 0.6) (layers "F.Cu") (net "PWR")))
"""
    bg = _board(tmp_path_factory, "leafreal", _LEAF + _leaf_part() + u2)
    vs, facts = check_current.check_net(bg, ENTRY_PWR)
    necks = _kinds(vs, "pour_neckdown")
    assert necks and all(v["current_a"] == 2.0 and v["severity"] == "error"
                         for v in necks)
    assert len(_kinds(vs, "insufficient_transition_vias")) == 2
    assert "leaf_branches" not in facts


def test_leaf_reaching_no_pad_keeps_budget(tmp_path_factory):
    """Copper that reaches no pad is not known to be a leaf (a synthetic
    or unfinished board): no bound, the findings stay at 2 A. The hop into
    the far F.Cu piece is a stitch to a dead-end pour (no pad or track on
    it), so the stitch rule skips it; the hop off the main pour stays."""
    bg = _board(tmp_path_factory, "leafbare", _LEAF)
    vs, facts = check_current.check_net(bg, ENTRY_PWR)
    assert _kinds(vs, "pour_neckdown")
    [hop] = _kinds(vs, "insufficient_transition_vias")
    assert hop["pos"] == [9.5, 5.0] and hop["required"] == 4   # 2 A
    assert "leaf_branches" not in facts


@pytest.mark.parametrize("value,ohms", [
    ("470R", 470.0), ("4k7", 4700.0), ("10k", 1e4), ("2.2K", 2200.0),
    ("1M", 1e6), ("100", 100.0), ("4R7", 4.7), ("10kOhm 1%", 1e4),
    ("0R", 0.0), ("R100", None), ("", None), ("DNP", None)])
def test_parse_ohms(value, ohms):
    assert check_current.parse_ohms(value) == ohms
