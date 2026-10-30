"""FAB-27: dfm_check catches a stale zone fill.

The case is hwde-led-test's DFM-1 (task 261029-175013's first-spin review):
the board was saved without a refill, so its B.Cu GND fill cleared J1 pad 1
(+5V) by 0.1275 mm while the zone's own clearance is 0.5 mm. DRC and the
gerber-level DFM passed and the fab Gerbers carried the stale copper. The
fixture is that board as it was saved; a refill gives 0.5005 mm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import dfm_check  # noqa: E402
import geom  # noqa: E402

STALE = REPO / "tests" / "fixtures" / "stale_zone_fill" / "hwde-led-test.kicad_pcb"
FRESH = REPO / "tests" / "golden" / "blinky2" / "blinky2.kicad_pcb"
NPTH = REPO / "tests" / "fixtures" / "stages" / "lumina_carrier" / "routed.kicad_pcb"


def _zone_vios(pcb: Path) -> list[dict]:
    vios: list = []
    dfm_check.check_zone_fill(geom.BoardGeom.from_file(pcb), vios)
    return vios


def test_zone_clearance_is_parsed():
    (z,) = geom.BoardGeom.from_file(STALE).zones_of()
    assert (z.net, z.clearance) == ("GND", 0.5)


def test_hwde_led_test_dfm1_stale_fill_fires():
    (v,) = _zone_vios(STALE)
    assert v["kind"] == "dfm_stale_zone_fill"
    assert v["severity"] == "error"
    assert v["layer"] == "B.Cu" and v["net"] == "GND"
    assert v["refs"] == ["J1"]
    assert v["clearance_mm"] == pytest.approx(0.1275, abs=1e-3)
    assert v["min_mm"] == 0.5
    assert "J1.1 (+5V)" in v["msg"]


def test_a_fresh_fill_is_quiet():
    assert FRESH.exists()
    assert _zone_vios(FRESH) == []


def test_npth_holes_are_left_to_hole_clearance():
    # lumina's mounting holes sit 0.25 mm from the fill (hole clearance),
    # under the zones' 0.5 mm - that is KiCad's own refill, not a stale one.
    bg = geom.BoardGeom.from_file(NPTH)
    assert any(p.kind == "np_thru_hole" for p in bg.pads_of())
    assert _zone_vios(NPTH) == []


def test_the_family_is_reported():
    assert dfm_check.DFM_FAMILIES["zone_fill"] == ("dfm_stale_zone_fill",)
