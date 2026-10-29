"""check_ratings - every pin's datasheet rating against the net it sits on.

Known answers: tests/fixtures/ratings/blinky is PCB-0001-A_stm32-blinky (P10,
shipped) reduced to refs, LCSC fields and pad nets, with its parts dir and
power tree; overvolt is the same board with U1 pad 10 (PA0, "VIN on any
other pin" = 4.0 V max) moved onto +5V. The small synthetic workspaces
below each build their own board and parts and assert both the case that
fires and the one that must not. Pure Python: no KiCad run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".claude" / "skills" / "hwde" / "scripts"
FIX = ROOT / "tests" / "fixtures" / "ratings"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import check_ratings as cr  # noqa: E402
import datasheet_extract  # noqa: E402
import verify_all  # noqa: E402
from _boards import board_path, need_board  # noqa: E402


def _run(ws: Path, constraints: Path | None = None) -> dict:
    argv = ["--pcb", str(ws / "kicad" / "board.kicad_pcb")]
    if constraints:
        argv += ["--constraints", str(constraints)]
    payload, _ = cr.run(argv)
    return payload


def _errors(p: dict) -> list[dict]:
    return [v for v in p["violations"] if v["severity"] == "error"]


# ------------------------------------------------------------ known answers

def test_overvoltage_fixture_has_exactly_the_planted_error():
    p = _run(FIX / "overvolt")
    errs = _errors(p)
    assert len(errs) == 1, errs
    e = errs[0]
    assert e["kind"] == "rating_over_voltage"
    assert e["pin"] == "U1.10" and e["net"] == "+5V"
    assert e["limit_v"] == pytest.approx(4.0)
    assert "any other pin" in e["rating"]


def test_clean_shipped_board_has_no_error_and_says_what_it_could_not_rate():
    p = _run(FIX / "blinky")
    assert _errors(p) == []
    assert p["pins_checked"] > 0
    unrated = [v for v in p["violations"] if v["kind"] == "rating_unrated"]
    # never a silent pass: the regulator's unrated VOUT and the bare
    # passives are named, as warnings
    assert any("AMS1117" in v["msg"] and "VOUT" in v["msg"] for v in unrated)
    assert all(v["severity"] == "warning" for v in unrated)
    # the rails came from the power tree and the regulator, not guessed
    assert p["net_volts"]["+3V3"] == {"v": 3.3, "src": "power_tree"}


def test_real_shipped_board_passes():
    need_board("PCB-0001-A_stm32-blinky")
    ws = board_path("PCB-0001-A_stm32-blinky")
    payload, _ = cr.run(["--pcb", str(next((ws / "kicad").glob("*.kicad_pcb"))),
                         "--constraints", str(ws / "kicad" / "constraints.json")])
    assert _errors(payload) == []


# ------------------------------------------------------------ parsing

@pytest.mark.parametrize("value,param,rec,want", [
    ("-0.3 to 4.0", "VDD", False, (-0.3, 4.0)),
    ("VSS-0.3 to VDD+4.0", "VIN on FT", False, (-0.3, ("VDD", 4.0))),
    ("+/-20", "VGS", False, (-20.0, 20.0)),
    (-30, "VDS (max)", False, (-30.0, None)),     # P-channel: a floor
    (0.4, "VOL output low", False, (None, None)),  # a characteristic
    (200, "BVDSS_min", False, (None, None)),       # abs 'min' above 0
    (2.2, "operating range min", True, (2.2, None)),
    ("3.0 / 3.3 / 3.6", "VDD33 recommended", True, (3.0, 3.6)),
    ("-0.5 to (VS) + 0.5", "ALERT", False, (-0.5, ("VS", 0.5))),
])
def test_parse_bounds(value, param, rec, want):
    lo, hi = cr.parse_bounds(value, param, 1.0, rec)
    for got, exp in ((lo, want[0]), (hi, want[1])):
        if isinstance(exp, float):
            assert got == pytest.approx(exp)
        elif isinstance(exp, tuple):
            assert got[0] == exp[0] and got[1] == pytest.approx(exp[1])
        else:
            assert got is None


@pytest.mark.parametrize("net,v", [
    ("+3V3", 3.3), ("3V3", 3.3), ("+12V", 12.0), ("-5V", -5.0),
    ("/VCC_5V", 5.0), ("VBUS", 5.0), ("GND", 0.0), ("/pwr/V48_RTN", 0.0),
    ("/I2C_SDA", None), ("/SW", None)])
def test_volts_from_name(net, v):
    assert cr.volts_from_name(net) == v


def test_regulator_mpn_volts():
    assert cr.reg_out_volts("AMS1117-3.3") == 3.3
    assert cr.reg_out_volts("L78L33ACUTR") == 3.3
    assert cr.reg_out_volts("L7805") == 5.0
    assert cr.reg_out_volts("TPS563201DDCR") is None
    # review #32: two digits are whole volts, and 78 only counts as the
    # MPN's own family prefix
    assert cr.reg_out_volts("L7815CV") == 15.0
    assert cr.reg_out_volts("L7818CV") == 18.0
    assert cr.reg_out_volts("MC78M05CDTRKG") == 5.0
    assert cr.reg_out_volts("HT7850") is None
    assert cr.reg_out_volts("TPS78233DDCR") is None
    assert cr.reg_out_volts("L7852CV") == 5.2
    assert cr.reg_out_volts("L7885CV") == 8.5


@pytest.mark.parametrize("cell,v", [
    ("3.3", [3.3]), ("5.0 / 4.7", [5.0, 4.7]), ("-5", [-5.0]),
    ("4.5-5.5", [5.5]), ("4.5 to 5.5 V", [5.5]), ("1.8 \u00b15%", [1.8]),
    ("3.3 +/-0.1", [3.3]), ("12 (5%)", [12.0]),
    ("4.5V-5.5V", [5.5]), ("3.0V\u20133.6V", [3.6]), ("4.5v to 5.5v", [5.5])])
def test_power_tree_cell_volts(cell, v):
    assert cr.cell_volts(cell) == v


def test_power_tree_range_and_tolerance_cells(tmp_path):
    tree = ("| Rail | Nom V |\n|---|---|\n| /VA | 4.5-5.5 |\n"
            "| /VB | 1.8 \u00b15% |\n")
    path = tmp_path / "power_tree.md"
    path.write_text(tree, encoding="utf-8")
    assert cr.power_tree_volts(path, {"/VA", "/VB"}) == {"/VA": 5.5,
                                                         "/VB": 1.8}


# ------------------------------------------------------------ synthetic boards

def _ws(tmp_path: Path, fps: list[tuple], parts: dict, bom=None,
        tree: str | None = None) -> Path:
    """fps: (ref, lcsc, {pad: net}); parts: {lcsc: extraction}."""
    ws = tmp_path / "ws"
    (ws / "kicad").mkdir(parents=True)
    (ws / "parts").mkdir()
    lines = ["(kicad_pcb (version 20240108)"]
    for ref, lcsc, pads in fps:
        lines.append(f'  (footprint "x" (property "Reference" "{ref}")'
                     f' (property "LCSC" "{lcsc}")')
        for num, net in pads.items():
            lines.append(f'    (pad "{num}" smd rect (net "{net}"))')
        lines.append("  )")
    lines.append(")")
    (ws / "kicad" / "board.kicad_pcb").write_text("\n".join(lines))
    (ws / "parts" / "parts.json").write_text(json.dumps({"parts": bom or []}))
    for lcsc, ext in parts.items():
        (ws / "parts" / f"{lcsc}.json").write_text(json.dumps(ext))
    if tree:
        (ws / "architecture").mkdir()
        (ws / "architecture" / "power_tree.md").write_text(tree)
    return ws


MCU = {"mpn": "TESTMCU", "pinout": [
    {"pin": "1", "name": "VDD", "type": "power_in"},
    {"pin": "2", "name": "VSS", "type": "ground"},
    {"pin": "3", "name": "PA0", "type": "bidirectional"},
    {"pin": "4", "name": "ALERT", "type": "output"}],
    "abs_max": [
        {"param": "VDD supply voltage", "value": "-0.3 to 4.0", "unit": "V"},
        {"param": "Voltage on ALERT", "value": "-0.3 to VDD+0.3", "unit": "V"},
        {"param": "VIN on any other pin", "value": "-0.3 to 3.6", "unit": "V"},
        {"param": "VIH input high level", "value": 0.7, "unit": "V"}]}


def test_input_above_supply(tmp_path):
    ws = _ws(tmp_path, [("U1", "C1", {"1": "+3V3", "2": "GND", "3": "+3V3",
                                      "4": "+5V"})], {"C1": MCU})
    p = _run(ws)
    errs = _errors(p)
    assert [e["pin"] for e in errs] == ["U1.4"]
    assert "input above supply" in errs[0]["msg"]
    assert errs[0]["limit_v"] == pytest.approx(3.6)   # VDD 3.3 + 0.3
    # PA0 on 3V3 is under its 3.6 V: kept quiet
    assert not any(v.get("pin") == "U1.3" for v in p["violations"])


def test_ground_pin_on_a_rail_is_reverse_polarity(tmp_path):
    ws = _ws(tmp_path, [("U1", "C1", {"1": "+3V3", "2": "+3V3"})],
             {"C1": MCU})
    errs = _errors(_run(ws))
    assert [(e["kind"], e["pin"]) for e in errs] == \
        [("rating_reverse_polarity", "U1.2")]
    ws2 = _ws(tmp_path / "b", [("U1", "C1", {"1": "+3V3", "2": "GND"})],
              {"C1": MCU})
    assert _errors(_run(ws2)) == []


def test_negative_rail_below_abs_min(tmp_path):
    ws = _ws(tmp_path, [("U1", "C1", {"1": "+3V3", "2": "GND",
                                      "3": "-5V"})], {"C1": MCU})
    errs = _errors(_run(ws))
    assert [(e["kind"], e["pin"]) for e in errs] == \
        [("rating_reverse_polarity", "U1.3")]


def test_pull_up_inference_warns_but_a_divider_stays_unknown(tmp_path):
    fps = [("U1", "C1", {"1": "+3V3", "2": "GND", "3": "/SIG"}),
           ("R1", "", {"1": "/SIG", "2": "+5V"})]
    p = _run(_ws(tmp_path, fps, {"C1": MCU}))
    ov = [v for v in p["violations"] if v["kind"] == "rating_over_voltage"]
    assert [(v["pin"], v["severity"]) for v in ov] == [("U1.3", "warning")]
    # the same pull-up plus a resistor to ground is a divider: no volts
    fps.append(("R2", "", {"1": "/SIG", "2": "GND"}))
    p = _run(_ws(tmp_path / "b", fps, {"C1": MCU}))
    assert not any(v["kind"] == "rating_over_voltage" for v in p["violations"])
    assert "/SIG" not in p["net_volts"]


def test_unrated_relative_bound_and_unnamed_pin(tmp_path):
    ext = {"mpn": "X", "pinout": [
        {"pin": "1", "name": "VCC", "type": "power_in"},
        {"pin": "2", "name": "IN", "type": "input"},
        {"pin": "3", "name": "MYSTERY", "type": "power_out"}],
        "abs_max": [{"param": "IN pin", "value": "-0.3 to VCC+0.3",
                     "unit": "V"}]}
    ws = _ws(tmp_path, [("U1", "C1", {"1": "/VX", "2": "+3V3",
                                      "3": "/Y"})], {"C1": ext})
    un = [v for v in _run(ws)["violations"] if v["kind"] == "rating_unrated"]
    assert len(un) == 1
    pins = " ".join(un[0]["pins"])
    assert "MYSTERY" in pins and "relative to VCC" in pins
    assert "1 VCC" in pins     # VCC itself: no row names it


def test_capacitor_voltage_attribute(tmp_path):
    bom = [{"lcsc": "C9", "mpn": "CAP",
            "attributes": [{"name": "Voltage Rated", "value": "6.3V"}]}]
    ws = _ws(tmp_path, [("C1", "C9", {"1": "+12V", "2": "GND"})], {}, bom)
    errs = _errors(_run(ws))
    assert [(e["kind"], e["refs"]) for e in errs] == \
        [("rating_over_voltage", ["C1"])]
    ws2 = _ws(tmp_path / "b", [("C1", "C9", {"1": "+5V", "2": "GND"})],
              {}, bom)
    assert _run(ws2)["violations"] == []


def test_constraints_and_power_tree_outrank_net_names(tmp_path):
    tree = "| Rail | Nom V |\n|---|---|\n| /VA, /VB | 5.0 / 2.5 |\n"
    ws = _ws(tmp_path, [("U1", "C1", {"1": "/VB", "2": "GND", "3": "/VA",
                                      "4": "+3V3"})], {"C1": MCU}, tree=tree)
    c = tmp_path / "constraints.json"
    c.write_text(json.dumps({"voltages": [{"net": "+3V3", "voltage": 5}]}))
    p = _run(ws, c)
    assert p["net_volts"]["/VA"] == {"v": 5.0, "src": "power_tree"}
    assert p["net_volts"]["/VB"] == {"v": 2.5, "src": "power_tree"}
    assert p["net_volts"]["+3V3"] == {"v": 5.0, "src": "constraints"}
    assert {e["pin"] for e in _errors(p)} == {"U1.3", "U1.4"}


def test_pin_ratings_outrank_the_free_text_rows(tmp_path):
    ext = dict(MCU, pin_ratings=[{"pins": ["PA0"], "kind": "voltage",
                                  "level": "abs_max", "min": -0.3, "max": 5.5,
                                  "source": "p.12 FT pin"}])
    jsonschema.validate(ext, datasheet_extract.DATASHEET_SCHEMA)
    ws = _ws(tmp_path, [("U1", "C1", {"1": "+3V3", "2": "GND",
                                      "3": "+5V"})], {"C1": ext})
    assert _errors(_run(ws)) == []
    ws2 = _ws(tmp_path / "b", [("U1", "C1", {"1": "+3V3", "2": "GND",
                                             "3": "+5V"})], {"C1": MCU})
    assert [e["pin"] for e in _errors(_run(ws2))] == ["U1.3"]


def test_regulator_output_over_current_warns(tmp_path):
    ext = {"mpn": "L78L33ACUTR", "pinout": [
        {"pin": "1", "name": "OUT", "type": "power_out"},
        {"pin": "2", "name": "GND", "type": "ground"}],
        "abs_max": [{"param": "Output current IO", "value": 100,
                     "unit": "mA"}]}
    fps = [("U1", "C1", {"1": "/RAIL", "2": "GND"})]
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"power": [{"net": "/RAIL", "current_a": 0.3}]}))
    p = _run(_ws(tmp_path, fps, {"C1": ext}), c)
    assert p["net_volts"]["/RAIL"]["src"] == "regulator U1"
    oc = [v for v in p["violations"] if v["kind"] == "rating_over_current"]
    assert [(v["pin"], v["severity"]) for v in oc] == [("U1.1", "warning")]
    c.write_text(json.dumps({"power": [{"net": "/RAIL", "current_a": 0.05}]}))
    p = _run(_ws(tmp_path / "b", fps, {"C1": ext}), c)
    assert not any(v["kind"] == "rating_over_current" for v in p["violations"])


# ------------------------------------------------------------ wiring

def test_verify_all_runs_it_only_with_a_parts_dir(tmp_path):
    check = next(c for c in verify_all.CHECKS if c["name"] == "check_ratings")
    assert check["needs"] == ["parts"]
    pcb = str(FIX / "overvolt" / "kicad" / "board.kicad_pcb")
    skipped = verify_all.run_one(check, {"pcb": pcb, "parts": None},
                                 tmp_path, strict=True)
    assert skipped["status"] == "skipped_error"
    ran = verify_all.run_one(check, {"pcb": pcb,
                                     "parts": str(FIX / "overvolt" / "parts")},
                             tmp_path)
    assert ran["status"] == "violations"
    assert [v["pin"] for v in ran["violations"]
            if v["severity"] == "error"] == ["U1.10"]


def test_fixtures_extractions_validate():
    for f in sorted(FIX.glob("*/parts/C*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        jsonschema.validate(data, datasheet_extract.DATASHEET_SCHEMA)
