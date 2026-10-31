"""check_esd (reference/ingest/emc-thermal.md ESD-1..5): an ESD clamp on
every external connector line, first on the line, short to ground, on
synthetic boards."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_esd  # noqa: E402


def _fp(ref: str, value: str, at: str, pads: list[tuple]) -> str:
    """pads: (number, x, y, net) in footprint-local mm, 1 x 1 mm SMD."""
    body = " ".join(
        f'(pad "{n}" smd rect (at {x} {y}) (size 1 1) (layers "F.Cu") '
        f'(net "{net}"))' for n, x, y, net in pads)
    return (f'  (footprint "t:{ref}" (at {at}) (layer "F.Cu")\n'
            f'    (property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS"))\n'
            f'    (property "Value" "{value}" (at 0 0 0) (layer "F.Fab"))\n'
            f'    {body})\n')


def _seg(x0, y0, x1, y1, net="SIG", layer="F.Cu") -> str:
    return (f'  (segment (start {x0} {y0}) (end {x1} {y1}) (width 0.2) '
            f'(layer "{layer}") (net "{net}"))\n')


def _via(x, y, net="GND") -> str:
    return (f'  (via (at {x} {y}) (size 0.6) (drill 0.3) '
            f'(layers "F.Cu" "B.Cu") (net "{net}"))\n')


def _pcb(tmp_path, body: str) -> Path:
    p = tmp_path / "esd.kicad_pcb"
    p.write_text(f"""(kicad_pcb (version 20260206) (generator "test")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1)) (fill no)
    (layer "Edge.Cuts"))
{body})
""", encoding="utf-8")
    return p


def _run(pcb, constraints: dict | None = None, tmp_path=None):
    argv = ["--pcb", str(pcb)]
    if constraints is not None:
        c = pcb.parent / "constraints.json"
        c.write_text(json.dumps(constraints), encoding="utf-8")
        argv += ["--constraints", str(c)]
    payload, _ = check_esd.run(argv)
    return payload, {v["kind"] for v in payload["violations"]}


# J1 at (5, 10): pin 1 SIG at (5, 10), pin 2 GND at (5, 12)
J1 = _fp("J1", "USB-C", "5 10", [("1", 0, 0, "SIG"), ("2", 0, 2, "GND")])
U1 = _fp("U1", "MCU", "30 10", [("1", 0, 0, "SIG")])


def _clamp(x: float, value="PESD5V0S1BL") -> str:
    """D1 with its SIG pin at (x, 10) and its GND pin at (x, 12)."""
    return _fp("D1", value, f"{x} 10", [("1", 0, 0, "SIG"), ("2", 0, 2, "GND")])


def test_clamp_at_the_connector_passes(tmp_path):
    pcb = _pcb(tmp_path, J1 + U1 + _clamp(8) + _seg(5, 10, 8, 10)
               + _seg(8, 10, 30, 10) + _via(8, 13))
    payload, kinds = _run(pcb)
    assert payload["status"] == "pass", json.dumps(payload["violations"])
    line = payload["checked"][0]["lines"][0]
    assert line["clamp"] == "D1" and line["routed"]
    assert line["mm"] == pytest.approx(3.0) and line["vias"] == 0
    assert line["stub_mm"] == pytest.approx(0.0, abs=1e-6)


def test_no_clamp_is_an_error(tmp_path):
    pcb = _pcb(tmp_path, J1 + U1 + _seg(5, 10, 30, 10))
    payload, kinds = _run(pcb)
    assert kinds == {"esd_unclamped"}
    assert payload["violations"][0]["severity"] == "error"


def test_power_line_unclamped_is_a_warning(tmp_path):
    j = _fp("J1", "DC jack", "5 10", [("1", 0, 0, "+5V"), ("2", 0, 2, "GND")])
    u = _fp("U1", "LDO", "30 10", [("1", 0, 0, "+5V")])
    payload, kinds = _run(_pcb(tmp_path, j + u))
    assert kinds == {"esd_unclamped"}
    assert payload["violations"][0]["severity"] == "warning"


def test_ic_reached_before_the_clamp(tmp_path):
    """SLVA680 case 2: the line splits at a tee, one branch to the IC,
    the other on to the clamp."""
    u = _fp("U1", "MCU", "10 5", [("1", 0, 0, "SIG")])
    pcb = _pcb(tmp_path, J1 + u + _clamp(20) + _seg(5, 10, 10, 10)
               + _seg(10, 10, 10, 5) + _seg(10, 10, 20, 10) + _via(20, 13))
    payload, kinds = _run(pcb)
    assert "esd_branch_first" in kinds, kinds
    line = payload["checked"][0]["lines"][0]
    assert line["stub_mm"] == pytest.approx(10.0)
    # 10 mm of stub + 0.2 mm to the ground via at ~1 nH/mm, 3.75e10 A/s
    assert line["est_extra_nh"] == pytest.approx(10.2)
    assert line["est_extra_v"] == pytest.approx(382.5)


def test_via_before_the_clamp(tmp_path):
    pcb = _pcb(tmp_path, J1 + U1 + _clamp(8) + _seg(5, 10, 6, 10)
               + _via(6, 10, "SIG") + _seg(6, 10, 7, 10, layer="B.Cu")
               + _via(7, 10, "SIG") + _seg(7, 10, 8, 10)
               + _seg(8, 10, 30, 10) + _via(8, 13))
    payload, kinds = _run(pcb)
    assert kinds == {"esd_via_first"}
    assert payload["checked"][0]["lines"][0]["vias"] == 2


def test_far_clamp(tmp_path):
    pcb = _pcb(tmp_path, J1 + U1 + _clamp(25) + _seg(5, 10, 25, 10)
               + _seg(25, 10, 30, 10) + _via(25, 13))
    payload, kinds = _run(pcb)
    assert kinds == {"esd_clamp_far"}
    assert payload["violations"][0]["mm"] == pytest.approx(20.0)


def test_clamp_ground_far_from_a_via(tmp_path):
    pcb = _pcb(tmp_path, J1 + U1 + _clamp(8) + _seg(5, 10, 8, 10)
               + _seg(8, 10, 30, 10) + _via(8, 18))
    payload, kinds = _run(pcb)
    assert kinds == {"esd_ground_far"}
    v = payload["violations"][0]
    # pad edge (y 12.5) to via edge (y 17.7)
    assert v["ground_via_mm"] == pytest.approx(5.2, abs=0.05)


def test_clamp_behind_a_series_resistor(tmp_path):
    r = _fp("R1", "33", "10 10", [("1", 0, 0, "SIG"), ("2", 1, 0, "SIG2")])
    d = _fp("D1", "ESD9B5.0ST5G", "14 10",
            [("1", 0, 0, "SIG2"), ("2", 0, 2, "GND")])
    u = _fp("U1", "MCU", "30 10", [("1", 0, 0, "SIG2")])
    payload, kinds = _run(_pcb(tmp_path, J1 + r + d + u + _via(14, 13)))
    assert kinds == {"esd_behind_series"}


def test_internal_connectors_are_skipped(tmp_path):
    ffc = _fp("J1", "FFC 20P", "5 10", [("1", 0, 0, "SIG"), ("2", 0, 2, "GND")])
    payload, kinds = _run(_pcb(tmp_path, ffc + U1))
    assert payload["status"] == "pass"
    assert payload["checked"][0]["internal_by_value"] == ["J1"]
    payload, kinds = _run(_pcb(tmp_path, J1 + U1), {"esd": {"internal": ["J1"]}})
    assert payload["status"] == "pass"
    payload, kinds = _run(_pcb(tmp_path, J1 + U1),
                          {"esd": {"exempt_nets": ["SIG"]}})
    assert payload["status"] == "pass"


def test_explicit_clamp_and_level(tmp_path):
    d = _clamp(8, value="XYZ123")
    body = J1 + U1 + d + _seg(5, 10, 8, 10) + _seg(8, 10, 30, 10) + _via(8, 13)
    payload, kinds = _run(_pcb(tmp_path, body))
    assert kinds == {"esd_unclamped"}
    payload, kinds = _run(_pcb(tmp_path, body),
                          {"esd": {"clamps": ["D1"], "level_kv": 4}})
    assert payload["status"] == "pass"
    assert payload["checked"][0]["di_dt_a_per_s"] == pytest.approx(1.875e10)


def test_iec_level4_di_dt():
    # IEC 61000-4-2:2008 Table 3: 30 A first peak at 8 kV, 0.8 ns rise
    assert check_esd.dI_dt(8) == pytest.approx(3.75e10)
