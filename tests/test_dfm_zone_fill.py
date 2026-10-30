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
# The same board after `kicad-cli pcb drc --refill-zones --save-board`.
REFILLED = STALE.parent / "refilled" / "hwde-led-test.kicad_pcb"
FRESH = REPO / "tests" / "golden" / "blinky2" / "blinky2.kicad_pcb"
NPTH = REPO / "tests" / "fixtures" / "stages" / "lumina_carrier" / "routed.kicad_pcb"


def _geom(pcb: Path):
    return geom.BoardGeom.from_file(pcb)


def _zone_vios(pcb: Path, fresh=None) -> list[dict]:
    vios: list = []
    assert dfm_check.check_zone_fill(_geom(pcb), vios, fresh=fresh) is None
    return vios


def test_zone_clearance_is_parsed():
    (z,) = _geom(STALE).zones_of()
    assert (z.net, z.clearance) == ("GND", 0.5)


def test_hwde_led_test_dfm1_stale_fill_fires():
    (v,) = _zone_vios(STALE, fresh=_geom(REFILLED))
    assert v["kind"] == "dfm_stale_zone_fill"
    assert v["severity"] == "error"
    assert v["layer"] == "B.Cu" and v["net"] == "GND"
    assert v["refs"] == ["J1"]
    assert v["clearance_mm"] == pytest.approx(0.1275, abs=1e-3)
    assert v["min_mm"] == pytest.approx(0.5005, abs=1e-3)
    assert "J1.1 (+5V)" in v["msg"]


def test_a_gap_a_refill_keeps_is_not_stale():
    # A project rule (.kicad_dru, netclass) can set a clearance under the
    # zone's own - the hi-fi boards' 0.10 mm. Then a refill leaves the same
    # gap: a candidate, but the fill is not stale.
    assert dfm_check._zone_fill_candidates(_geom(STALE))
    assert _zone_vios(STALE, fresh=_geom(STALE)) == []


def test_a_refilled_board_has_no_candidates():
    assert dfm_check._zone_fill_candidates(_geom(REFILLED)) == []
    assert dfm_check._zone_fill_candidates(_geom(FRESH)) == []


def test_npth_holes_are_left_to_hole_clearance():
    # lumina's mounting holes sit 0.25 mm from the fill (hole clearance),
    # under the zones' 0.5 mm - that is KiCad's own refill, not a stale one.
    bg = _geom(NPTH)
    assert any(p.kind == "np_thru_hole" for p in bg.pads_of())
    assert dfm_check._zone_fill_candidates(bg) == []


def test_no_refill_is_a_coverage_hole(monkeypatch):
    def boom(pcb):
        raise geom.StaleFillError("no kicad-cli")
    monkeypatch.setattr(dfm_check, "_refilled", boom)
    vios: list = []
    why = dfm_check.check_zone_fill(_geom(STALE), vios)
    assert vios == [] and "no kicad-cli" in why


def test_live_refill_confirms_dfm1(tmp_path):
    # kicad-cli refills a copy (with its .kicad_pro/.kicad_dru beside it).
    (v,) = _zone_vios(STALE)
    assert v["min_mm"] == pytest.approx(0.5005, abs=1e-3)


def test_the_family_is_reported():
    assert dfm_check.DFM_FAMILIES["zone_fill"] == ("dfm_stale_zone_fill",)
