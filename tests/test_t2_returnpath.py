"""T2 regression tests: stackup-aware reference resolution + cross-net waiver
class in check_return_path.

LEARNINGS 2026-07-30 [check_return_path][stackup]: on an F / GND / +3V3 / B
stack the only plane adjacent to B.Cu belongs to +3V3, so the old code judged
every GND-referenced B.Cu corridor against GND via-disk slivers on In2 and
reported corridor_void errors no matter how good the layout was. These tests
pin the fix: per-(signal layer, ref layer) resolution, cross-net waiver mode
(warnings, never errors), and unchanged behavior when the declared net's fill
is actually present.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon, box

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_return_path  # noqa: E402
import geom  # noqa: E402


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


# lumina-class stack: In1 = GND plane, In2 = +3V3 plane, signal on B.Cu.
IN1_GND = """  (zone (net "GND") (layer "In1.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In1.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
"""

IN2_PWR = """  (zone (net "+3V3") (layer "In2.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
"""

# +3V3 plane with a 0.8 mm gap at x 4..4.8 crossing the trace corridor
IN2_PWR_SPLIT = """  (zone (net "+3V3") (layer "In2.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 0 0) (xy 4 0) (xy 4 10) (xy 0 10)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 4.8 0) (xy 20 0) (xy 20 10) (xy 4.8 10))))
"""

# usbbuck4-class: declared-net GND strip under the corridor, +3V3 elsewhere
IN2_GND_STRIP = """  (zone (net "GND") (layer "In2.Cu")
    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 2) (xy 0 2)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 0 0) (xy 10 0) (xy 10 2) (xy 0 2))))
"""

IN2_GND_STRIP_SPLIT = """  (zone (net "GND") (layer "In2.Cu")
    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 2) (xy 0 2)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 0 0) (xy 4 0) (xy 4 2) (xy 0 2)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 4.8 0) (xy 10 0) (xy 10 2) (xy 4.8 2))))
"""

IN2_PWR_TOP = """  (zone (net "+3V3") (layer "In2.Cu")
    (polygon (pts (xy 0 2.5) (xy 20 2.5) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In2.Cu")
      (pts (xy 0 2.5) (xy 20 2.5) (xy 20 10) (xy 0 10))))
"""

B_TRACK = ('  (segment (start 1 1) (end 9 1) (width 0.2) (layer "B.Cu")'
           ' (net "SIG"))\n')
# through GND via -> GND owns via-disk copper on In2 (the real-board trap:
# old code saw non-empty GND copper there and emitted corridor_void, not
# no_reference_plane)
GND_VIA = ('  (via (at 15 8) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")'
           ' (net "GND"))\n')

HS_GND = {"net": "SIG", "reference": "GND"}
WAIVER = "cross_net_reference"


# --------------------------------------------- fixture A: the ~1774 defect

def test_cross_net_reference_resolves_clean(tmp_path_factory):
    """Solid +3V3 plane under a GND-referenced B.Cu trace: resolution picks
    +3V3, no violations at all (old code: corridor_void error per trace)."""
    bg = _board4(tmp_path_factory, "xnetclean",
                 B_TRACK + GND_VIA + IN1_GND + IN2_PWR)
    # trap precondition: GND HAS copper on In2 (via disk) but NO zone fill
    assert not bg.net_copper("GND", "In2.Cu").is_empty
    assert bg.zone_fill("GND", "In2.Cu").is_empty
    vs, facts = check_return_path.check_net(bg, dict(HS_GND), 3.0)
    assert vs == []
    assert facts["layers"] == ["B.Cu"]
    assert facts["reference_resolution"] == [{
        "signal_layer": "B.Cu", "ref_layer": "In2.Cu",
        "declared": "GND", "resolved": "+3V3",
        "waiver_class": WAIVER, "corridor_coverage": 1.0}]


# ---------------------------- fixture B: real defect still caught (waiver)

def test_cross_net_split_plane_warns_with_waiver(tmp_path_factory):
    """A split in the resolved +3V3 plane is still reported - but as warning
    with the waiver-class extras, never error."""
    bg = _board4(tmp_path_factory, "xnetsplit",
                 B_TRACK + GND_VIA + IN1_GND + IN2_PWR_SPLIT)
    vs, facts = check_return_path.check_net(bg, dict(HS_GND), 3.0)
    voids = [v for v in vs if v["kind"] == "corridor_void"]
    assert voids
    assert all(v["severity"] == "warning" for v in vs)
    v = voids[0]
    assert v["waiver_class"] == WAIVER
    assert v["reference_declared"] == "GND"
    assert v["reference_net"] == "+3V3"
    assert v["layer"] == "In2.Cu" and v["net"] == "SIG"
    assert Polygon(v["polygon"]).intersects(box(4, 0.4, 4.8, 1.6))
    # deficit spans trace start (x=1) to the far gap edge (x=4.8)
    assert v["crossing_len_mm"] == pytest.approx(3.8, abs=0.05)
    rec = facts["reference_resolution"][0]
    assert rec["resolved"] == "+3V3" and rec["waiver_class"] == WAIVER
    assert rec["corridor_coverage"] == pytest.approx(0.9, abs=0.01)


# ------------------------- fixture C: declared net's fill wins (no waiver)

def test_declared_net_strip_resolves_declared_clean(tmp_path_factory):
    """GND strip under the corridor (with +3V3 fill elsewhere on In2):
    resolved == declared, no waiver, clean."""
    bg = _board4(tmp_path_factory, "declwins",
                 B_TRACK + GND_VIA + IN1_GND + IN2_GND_STRIP + IN2_PWR_TOP)
    vs, facts = check_return_path.check_net(bg, dict(HS_GND), 3.0)
    assert vs == []
    assert facts["reference_resolution"] == [{
        "signal_layer": "B.Cu", "ref_layer": "In2.Cu",
        "declared": "GND", "resolved": "GND",
        "waiver_class": None, "corridor_coverage": 1.0}]


def test_declared_net_strip_split_stays_error(tmp_path_factory):
    """Split in the DECLARED net's strip: severities unchanged from today
    (error, no waiver keys)."""
    bg = _board4(tmp_path_factory, "declsplit",
                 B_TRACK + GND_VIA + IN1_GND + IN2_GND_STRIP_SPLIT
                 + IN2_PWR_TOP)
    vs, facts = check_return_path.check_net(bg, dict(HS_GND), 3.0)
    voids = [v for v in vs if v["kind"] == "corridor_void"]
    assert voids
    v = voids[0]
    assert v["severity"] == "error"
    assert "waiver_class" not in v
    assert "reference_declared" not in v
    assert v["reference_net"] == "GND"
    assert Polygon(v["polygon"]).intersects(box(4, 0.4, 4.8, 1.6))
    rec = facts["reference_resolution"][0]
    assert rec["resolved"] == "GND" and rec["waiver_class"] is None


# ------------------------------- fixture D: transitions under waiver mode

TRANS_SIG = """  (segment (start 1 5) (end 5 5) (width 0.2) (layer "F.Cu") (net "SIG"))
  (segment (start 5 5) (end 12 5) (width 0.2) (layer "B.Cu") (net "SIG"))
  (via (at 5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "SIG"))
"""


def test_transition_waiver_downgrades_stitch_cap(tmp_path_factory):
    """F side references declared GND (In1), B side cross-net resolves to
    +3V3: the missing stitch cap is a warning carrying the waiver class."""
    bg = _board4(tmp_path_factory, "xnettrans", TRANS_SIG + IN1_GND + IN2_PWR)
    vs, facts = check_return_path.check_net(bg, dict(HS_GND), 3.0)
    assert len(vs) == 1
    v = vs[0]
    assert v["kind"] == "missing_stitch_cap"
    assert v["severity"] == "warning"
    assert v["waiver_class"] == WAIVER
    assert v["reference_declared"] == "GND"
    assert v["reference_net"] == "+3V3"
    assert v["pos"] == [5.0, 5.0]
    assert [(r["signal_layer"], r["ref_layer"], r["resolved"],
             r["waiver_class"]) for r in facts["reference_resolution"]] == [
        ("F.Cu", "In1.Cu", "GND", None),
        ("B.Cu", "In2.Cu", "+3V3", WAIVER)]


def test_transition_explicit_declaration_stays_error(tmp_path_factory):
    """Same geometry, but the user EXPLICITLY declared B.Cu -> +3V3:
    resolution agrees with declaration, so no waiver - error as today."""
    bg = _board4(tmp_path_factory, "decltrans", TRANS_SIG + IN1_GND + IN2_PWR)
    entry = {"net": "SIG", "reference": {"F.Cu": "GND", "B.Cu": "+3V3"}}
    vs, facts = check_return_path.check_net(bg, entry, 3.0)
    stitch = [v for v in vs if v["kind"] == "missing_stitch_cap"]
    assert len(stitch) == 1
    assert stitch[0]["severity"] == "error"
    assert "waiver_class" not in stitch[0]
    assert all(r["waiver_class"] is None
               for r in facts["reference_resolution"])


# ---- verifier follow-up: missing_return_via in waiver mode -----------------

IN1_PWR = """  (zone (net "+3V3") (layer "In1.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "In1.Cu")
      (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10))))
"""

PWR_RETURN_VIA = ('  (via (at 5.6 5) (size 0.6) (drill 0.3)'
                  ' (layers "F.Cu" "B.Cu") (net "+3V3"))\n')


def test_transition_waiver_downgrades_return_via(tmp_path_factory):
    """BOTH sides cross-net resolve to the SAME net (+3V3 planes on In1 AND
    In2): the transition needs a +3V3 return via, and its absence is a
    WARNING carrying the waiver class - not an error (LEARNINGS 2026-07-30:
    an unfixable-by-construction error class teaches teams to ignore the
    check)."""
    bg = _board4(tmp_path_factory, "xnetret", TRANS_SIG + IN1_PWR + IN2_PWR)
    vs, facts = check_return_path.check_net(bg, dict(HS_GND), 3.0)
    assert len(vs) == 1
    v = vs[0]
    assert v["kind"] == "missing_return_via"
    assert v["severity"] == "warning"
    assert v["waiver_class"] == WAIVER
    assert v["reference_declared"] == "GND"
    assert v["reference_net"] == "+3V3"
    assert v["pos"] == [5.0, 5.0]
    assert [(r["signal_layer"], r["resolved"], r["waiver_class"])
            for r in facts["reference_resolution"]] == [
        ("F.Cu", "+3V3", WAIVER), ("B.Cu", "+3V3", WAIVER)]
    # control: a +3V3 through-via 0.6 mm away satisfies the transition
    bg2 = _board4(tmp_path_factory, "xnetret_ok",
                  TRANS_SIG + PWR_RETURN_VIA + IN1_PWR + IN2_PWR)
    vs2, _ = check_return_path.check_net(bg2, dict(HS_GND), 3.0)
    assert vs2 == []
