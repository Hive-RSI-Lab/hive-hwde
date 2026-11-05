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
SCRIPTS = ROOT / "scripts"
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
    ("/I2C_SDA", None), ("/SW", None), ("/power/1V35", 1.35),
    ("/power/PG_1V35", None), ("/ADC_1V35", None), ("/VBUS_J1", 5.0),
    ("/usb/VBUS_J2", 5.0), ("/VBUS_SENSE", None), ("/3V3_EN", None)])
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
    """fps: (ref, lcsc, {pad: net}[, footprint]); parts: {lcsc: extraction}."""
    ws = tmp_path / "ws"
    (ws / "kicad").mkdir(parents=True)
    (ws / "parts").mkdir()
    lines = ["(kicad_pcb (version 20240108)"]
    for ref, lcsc, pads, *fp in fps:
        lines.append(f'  (footprint "{fp[0] if fp else "x"}" '
                     f'(property "Reference" "{ref}")'
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


# ------------------------------------------------------------ reversed input

LED = {"mpn": "TESTLED", "pinout": [
    {"pin": "1", "name": "K", "type": "passive"},
    {"pin": "2", "name": "A", "type": "passive"}],
    "abs_max": [{"param": "Reverse voltage VR", "value": 5, "unit": "V"}]}


def _led_board(rail="+5V", conn_pads=None):
    return [("J1", "", conn_pads or {"1": rail, "2": "GND"}),
            ("R1", "", {"1": rail, "2": "/LED_A"}),
            ("D1", "C7", {"1": "GND", "2": "/LED_A"})]


def test_reversed_input_over_an_leds_reverse_rating_warns(tmp_path):
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"voltages": [{"net": "+5V", "voltage": 5.25}]}))
    p = _run(_ws(tmp_path, _led_board(), {"C7": LED}), c)
    rp = [v for v in p["violations"] if v["kind"] == "rating_reverse_polarity"]
    assert [(v["pin"], v["severity"], v["scenario"]) for v in rp] == \
        [("D1.1", "warning", "J1 reversed")]
    assert rp[0]["net_v"] == pytest.approx(5.25)
    assert rp[0]["limit_v"] == pytest.approx(5.0)
    # forward biased in normal use: no error, and the anode is rated as
    # the other side of the K-A row, not unrated
    assert _errors(p) == []
    assert not any(v["kind"] == "rating_unrated" and "D1" in v["msg"]
                   for v in p["violations"])
    assert p["reversed_inputs"] == [{"connector": "J1", "rail": "+5V",
                                     "rail_v": -5.25}]


def test_reversed_input_within_rating_or_not_a_two_net_input(tmp_path):
    p = _run(_ws(tmp_path, _led_board("+3V3"), {"C7": LED}))
    assert not any(v["kind"] == "rating_reverse_polarity"
                   for v in p["violations"])
    # a connector carrying a signal too (USB, a bus) is not a bare power
    # input that plugs in either way round
    fps = _led_board(conn_pads={"1": "+5V", "2": "GND", "3": "/SDA"})
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"voltages": [{"net": "+5V", "voltage": 5.25}]}))
    p = _run(_ws(tmp_path / "b", fps, {"C7": LED}), c)
    assert p["reversed_inputs"] == []
    assert not any(v["kind"] == "rating_reverse_polarity"
                   for v in p["violations"])


def test_diode_reverse_attribute_and_netlist_pin_names(tmp_path):
    # no extraction: parts.json's reverse-voltage attribute rates it, and
    # the netlist beside the board says pad 2 is the cathode
    bom = [{"lcsc": "C8", "mpn": "D", "attributes": [
        {"name": "Reverse Voltage (Vr)", "value": "4V"}]}]
    ws = _ws(tmp_path, [("D1", "C8", {"1": "GND", "2": "+5V"})], {}, bom)
    (ws / "kicad" / "board.net").write_text(
        '(export (nets (net (code "1") (name "+5V") (node (ref "D1") '
        '(pin "2") (pinfunction "K"))) (net (code "2") (name "GND") '
        '(node (ref "D1") (pin "1") (pinfunction "A")))))')
    errs = _errors(_run(ws))
    assert [(e["kind"], e["pin"]) for e in errs] == \
        [("rating_reverse_polarity", "D1.2")]
    assert "pad names" in errs[0]["msg"]
    # with no netlist, pad 1 is the cathode (KiCad's convention): the same
    # pads are then forward biased, checked clean and rated
    ws2 = _ws(tmp_path / "b", [("D1", "C8", {"1": "GND", "2": "+5V"})],
              {}, bom)
    p = _run(ws2)
    assert _errors(p) == []
    assert not any(v["kind"] == "rating_unrated" for v in p["violations"])
    ws3 = _ws(tmp_path / "c", [("D1", "C8", {"1": "+5V", "2": "GND"})],
              {}, bom)
    errs = _errors(_run(ws3))
    assert [e["pin"] for e in errs] == ["D1.1"]
    assert "pad 1 taken as the cathode" in errs[0]["msg"]


def test_hwde_led_test_d1_reversed_input(tmp_path):
    """Issue #242: the cold review's POWER-2 - J1 reversed puts 5.25 V
    across D1 (KT-0603G, VR 5 V)."""
    import shutil
    ws = tmp_path / "hwde-led-test"
    src = ROOT / "boards" / "hwde-led-test"
    for d in ("kicad", "parts", "architecture"):
        shutil.copytree(src / d, ws / d)
    payload, _ = cr.run(["--pcb", str(ws / "kicad" / "hwde-led-test.kicad_pcb"),
                         "--constraints",
                         str(ws / "kicad" / "constraints.json")])
    rp = [v for v in payload["violations"]
          if v["kind"] == "rating_reverse_polarity"]
    assert [(v["pin"], v["scenario"]) for v in rp] == [("D1.1", "J1 reversed")]
    assert rp[0]["net_v"] == pytest.approx(5.25)


# ------------------------------------------- resistor Umax, fuses, ambient

def _cons(tmp_path: Path, **kw) -> Path:
    c = tmp_path / "constraints.json"
    c.write_text(json.dumps(kw))
    return c


def test_resistor_limiting_element_voltage_by_chip_size(tmp_path):
    # a 0402 bleeder across +60V: 50 V Umax (D/CRCW e3), at any power
    bom = [{"lcsc": "C25", "mpn": "0402WGF1005TCE", "attributes": []}]
    ws = _ws(tmp_path, [("R1", "C25", {"1": "+60V", "2": "GND"},
                         "Resistor_SMD:R_0402_1005Metric")], {}, bom)
    (e,) = _errors(_run(ws))
    assert e["kind"] == "rating_over_voltage" and e["refs"] == ["R1"]
    assert e["limit_v"] == 50 and "limiting element" in e["rating"]
    # the same part as an 0805 (150 V) holds, and is no longer unrated
    ws2 = _ws(tmp_path / "b", [("R1", "C25", {"1": "+60V", "2": "GND"},
                                "Resistor_SMD:R_0805_2012Metric")], {}, bom)
    assert _run(ws2)["violations"] == []


PPTC_BOM = [{"lcsc": "C70069", "mpn": "SMD1206P110TF", "category": "PTC "
             "Resettable Fuses", "attributes": [
                 {"name": "Current - Hold (Ih)", "value": "1.1A"},
                 {"name": "Current - Trip (It)", "value": "2.2A"},
                 {"name": "Voltage - Max", "value": "16V"},
                 {"name": "Operating Temperature", "value": "-40℃~+85℃"}]}]


def test_pptc_hold_current_at_ambient_and_vmax(tmp_path):
    # R-RAT-4's planted fault: a 1206 PPTC, 1.1 A hold at 23 C (~0.75 A at
    # 70 C), on a 1 A 20 V rail, Vmax 16 V: two errors
    fps = [("F1", "C70069", {"1": "/VIN_RAW", "2": "/VIN"})]
    ws = _ws(tmp_path, fps, {}, PPTC_BOM)
    c = _cons(tmp_path, ambient_c=70,
              voltages=[{"net": "/VIN_RAW", "voltage": 20},
                        {"net": "/VIN", "voltage": 20}],
              power=[{"net": "/VIN", "current_a": 1.0}])
    p = _run(ws, c)
    errs = sorted((e["kind"], e["refs"][0]) for e in _errors(p))
    assert errs == [("rating_over_current", "F1"),
                    ("rating_over_voltage", "F1")]
    oc = next(e for e in _errors(p) if e["kind"] == "rating_over_current")
    assert oc["limit_a"] == pytest.approx(0.748)
    assert p["fuses"][0]["kind"] == "PPTC"
    assert p["ambient"]["src"] == "constraints ambient_c"
    # at 25 C on a 12 V rail the same part holds 1 A with margin: quiet
    (tmp_path / "b").mkdir()
    c2 = _cons(tmp_path / "b", voltages=[{"net": "/VIN_RAW", "voltage": 12}],
               power=[{"net": "/VIN", "current_a": 0.9}])
    p2 = _run(ws, c2)
    assert _errors(p2) == []
    assert not any(v["kind"] == "rating_over_current" for v in p2["violations"])
    # under the 10 % margin warns: 1.05 A against 1.1 x 0.99
    (tmp_path / "c").mkdir()
    c3 = _cons(tmp_path / "c", ambient_c=25,
               power=[{"net": "/VIN", "current_a": 1.05}])
    w = [v for v in _run(ws, c3)["violations"]
         if v["kind"] == "rating_over_current"]
    assert [v["severity"] for v in w] == ["warning"]


def test_fuse_runs_at_75_percent_of_its_rerated_current(tmp_path):
    # Littelfuse 881 at 70 C: k 0.90. 1.5 A on a 2 A fuse is over
    # 0.75 x 0.9 x 2 = 1.35 A (warning); 2.5 A holds; 1.9 A on 2 A at 70 C
    # is over 1.8 A outright (error)
    def bom(amps):
        return [{"lcsc": "C9", "mpn": "0881002.MXEP", "attributes": [
            {"name": "Current Rating", "value": f"{amps}A"},
            {"name": "Voltage Rating (DC)", "value": "63V"}]}]
    fps = [("F1", "C9", {"1": "+12V", "2": "/V12F"})]
    def kinds(amps, iop, sub):
        d = tmp_path / sub
        d.mkdir()
        c = _cons(d, ambient_c=70, power=[{"net": "/V12F",
                                           "current_a": iop}])
        return [(v["kind"], v["severity"]) for v in
                _run(_ws(d, fps, {}, bom(amps)), c)["violations"]]
    assert kinds(2, 1.5, "a") == [("rating_over_current", "warning")]
    assert kinds(2.5, 1.5, "b") == []
    assert kinds(2, 1.9, "c") == [("rating_over_current", "error")]


def test_fuse_voltage_is_the_rail_not_the_drop_across_it(tmp_path):
    bom = [{"lcsc": "C9", "mpn": "F", "attributes": [
        {"name": "Current Rating", "value": "2A"},
        {"name": "Voltage Rating", "value": "32V"}]}]
    ws = _ws(tmp_path, [("F1", "C9", {"1": "+48V", "2": "/V48F"})], {}, bom)
    (e,) = _errors(_run(ws))
    assert e["kind"] == "rating_over_voltage" and e["limit_v"] == 32
    assert e["net_v"] == 48


def test_operating_range_against_the_ambient(tmp_path):
    # a 0..70 C commercial part on an 85 C board; the -40..85 one holds
    bom = [{"lcsc": "C1", "mpn": "COMM", "attributes": [
               {"name": "Operating Temperature", "value": "0℃~+70℃"}]},
           {"lcsc": "C2", "mpn": "IND", "attributes": [
               {"name": "Operating Temperature", "value": "-40℃~+85℃"}]}]
    fps = [("U1", "C1", {"1": "+3V3", "2": "GND"}),
           ("U2", "C2", {"1": "+3V3", "2": "GND"})]
    ws = _ws(tmp_path, fps, {}, bom)
    p = _run(ws, _cons(tmp_path, ambient_c=85))
    t = [v for v in p["violations"] if v["kind"] == "rating_over_temperature"]
    assert [(v["refs"], v["severity"], v["limit_c"]) for v in t] == \
        [(["U1"], "error", 70)]
    assert p["operating_range"]["parts_checked"] == 2
    # a cold start below the industrial part's -40 C
    p = _run(ws, _cons(tmp_path, ambient_c=25, ambient_min_c=-45))
    t = [v for v in p["violations"] if v["kind"] == "rating_over_temperature"]
    assert sorted(v["refs"][0] for v in t) == ["U1", "U2"]
    # no ambient_c: 25 C, said so, and nothing fires
    p = _run(ws)
    assert p["ambient"]["c"] == 25 and "default" in p["ambient"]["src"]
    assert not any(v["kind"] == "rating_over_temperature"
                   for v in p["violations"])


def test_lamp_holder_led_is_not_a_pptc(tmp_path):
    # LCSC's LED attribute "Lamp Holder Type" once read as a hold current
    bom = [{"lcsc": "C2286", "mpn": "KT-0603R", "attributes": [
        {"name": "Lamp Holder Type", "value": "1.6mm x 0.8mm square LED"}]}]
    ws = _ws(tmp_path, [("D2", "C2286", {"1": "/LED", "2": "GND"})], {}, bom)
    assert _run(ws)["fuses"] == []


# ------------------------------------------------- not parts / held facts

def test_board_features_and_bom_excluded_footprints_are_not_rated(tmp_path):
    """#429: printed RF structures (exclude_from_bom custom copper) and
    parts.json board_feature lines are copper, not parts: no 'no LCSC'."""
    fps = [("RFS1", "", {"1": "/RF", "2": "GND", "3": "/X"}),
           ("RFS2", "", {"1": "/RF", "2": "GND", "3": "/X"}),
           ("U9", "", {"1": "/RF", "2": "GND", "3": "/X"})]
    bom = [{"refdes": ["RFS2"], "assembly_class": "board_feature"}]
    ws = _ws(tmp_path, fps, {}, bom)
    pcb = ws / "kicad" / "board.kicad_pcb"
    pcb.write_text(pcb.read_text().replace(
        '(property "Reference" "RFS1")',
        '(property "Reference" "RFS1") (attr smd exclude_from_pos_files '
        'exclude_from_bom)'))
    refs = {r for v in _run(ws)["violations"] for r in v["refs"]}
    assert refs == {"U9"}       # a real part with no LCSC still warns


def test_hand_verified_rating_is_kept_and_not_rewarned(tmp_path, monkeypatch):
    """#429: a rating someone recorded verified (ledger.py set) clears the
    unrated warning and is not rewritten to unknown by the next verify."""
    bom = [{"lcsc": "C77", "refdes": ["J1", "J2"], "mpn": "SMA"}]
    fps = [("J1", "C77", {"1": "/RF", "2": "GND"}),
           ("J2", "C77", {"1": "/RF", "2": "GND"})]
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "1")
    ws = _ws(tmp_path, fps, {}, bom)
    (ws / "state.json").write_text("{}")
    import ledgerlib
    hand = {"subject": "J1", "kind": "rating", "status": "verified",
            "value": "50 V rated, checked by hand", "source": "datasheet p2"}
    ledgerlib.put(ws, [hand], "hand")
    p = _run(ws)
    un = [v for v in p["violations"] if v["kind"] == "rating_unrated"]
    assert [v["refs"] for v in un] == [["J2"]]
    assert "J1/rating" in p["held_verified"]
    led = ledgerlib.load(ws)["facts"]
    assert led["J1/rating"]["status"] == "verified"
    assert led["J1/rating"]["by"] == "hand"
    assert led["J2/rating"]["status"] == "unknown"
    # a real finding still overrides a held fact: J1 over its 'rated' volts
    bom[0]["attributes"] = [{"name": "Voltage Rated", "value": "6V"}]
    (ws / "parts" / "parts.json").write_text(json.dumps({"parts": bom}))
    pcb = ws / "kicad" / "board.kicad_pcb"
    pcb.write_text(pcb.read_text().replace('"/RF"', '"+12V"'))
    p = _run(ws)
    assert {e["refs"][0] for e in _errors(p)} == {"J1", "J2"}
    assert ledgerlib.load(ws)["facts"]["J1/rating"]["status"] == "failed"


# ------------------------------------------------------------ no attributes
# #675: a rated part whose parts.json row has no attributes and which nothing
# else rates is not a soft warning. precision-daq's D101 and F101 only showed
# up once the cache was filled, so verify had passed with them unjudged.

def _no_attrs(p: dict) -> list[dict]:
    return [v for v in p["violations"] if v["kind"] == "rating_no_attributes"]


def test_bare_diode_on_a_5v_reverse_rail_blocks_and_names_itself(tmp_path):
    bom = [{"lcsc": "C8", "mpn": "SM712", "attributes": []}]
    ws = _ws(tmp_path, [("D101", "C8", {"1": "+5V", "2": "GND"})], {}, bom)
    p = _run(ws)
    found = _no_attrs(p)
    assert [(v["severity"], v["refs"]) for v in found] == \
        [("error", ["D101"])]
    msg = found[0]["msg"]
    assert "D101" in msg and "C8" in msg and "SM712" in msg
    assert "parts_enrich" in msg and "parts_search" in msg


def test_diode_with_attributes_judged_in_rating_does_not_block(tmp_path):
    bom = [{"lcsc": "C8", "mpn": "SM712", "attributes": [
        {"name": "Reverse Voltage (Vr)", "value": "7V"}]}]
    ws = _ws(tmp_path, [("D101", "C8", {"1": "+5V", "2": "GND"})], {}, bom)
    p = _run(ws)
    assert _no_attrs(p) == [] and _errors(p) == []


def test_filled_diode_over_its_rating_gets_the_real_finding(tmp_path):
    # what filling precision-daq's cache showed: 5.25 V against a 5 V stand-off
    bom = [{"lcsc": "C8", "mpn": "SM712", "attributes": [
        {"name": "Reverse Voltage (Vr)", "value": "5V"}]}]
    ws = _ws(tmp_path, [("D101", "C8", {"1": "+5V25", "2": "GND"})], {}, bom)
    cons = tmp_path / "cons.json"
    cons.write_text(json.dumps({"voltages": [{"net": "+5V25", "voltage": 5.25},
                                             {"net": "GND", "voltage": 0}]}))
    errs = _errors(_run(ws, cons))
    assert [e["kind"] for e in errs] == ["rating_reverse_polarity"]


def test_bare_fuse_blocks_even_with_an_extraction(tmp_path):
    # fuse_findings reads hold current only from attributes, so an extraction
    # does not rate it
    bom = [{"lcsc": "C9", "mpn": "0881002", "attributes": []}]
    ws = _ws(tmp_path, [("F101", "C9", {"1": "+5V", "2": "/OUT"})],
             {"C9": {"mpn": "0881002", "pinout": []}}, bom)
    assert [v["refs"] for v in _no_attrs(_run(ws))] == [["F101"]]
    bom[0]["attributes"] = [{"name": "Hold Current", "value": "1A"}]
    ws2 = _ws(tmp_path / "b", [("F101", "C9", {"1": "+5V", "2": "/OUT"})],
              {}, bom)
    assert _no_attrs(_run(ws2)) == []


def test_bare_cap_blocks_and_one_finding_names_every_ref_on_the_code(tmp_path):
    bom = [{"lcsc": "C1", "mpn": "CL05", "attributes": []}]
    ws = _ws(tmp_path, [("C2", "C1", {"1": "+5V", "2": "GND"}),
                        ("C10", "C1", {"1": "+3V3", "2": "GND"})], {}, bom)
    found = _no_attrs(_run(ws))
    assert len(found) == 1 and found[0]["refs"] == ["C10", "C2"]
    bom[0]["attributes"] = [{"name": "Voltage Rated", "value": "16V"}]
    ws2 = _ws(tmp_path / "b", [("C2", "C1", {"1": "+5V", "2": "GND"})], {},
              bom)
    assert _no_attrs(_run(ws2)) == []


@pytest.mark.parametrize("row", [
    {"mpn": "LP5907-3.3"},                                # output in the MPN
    {"mpn": "AP2112", "description": "LDO regulator"},    # the row's words
    {"mpn": "SY8303AIC", "role": "VBUS to 5.1 V"},        # what it converts
    # the family's code, and no word says regulator (#751, linux-computer U603)
    {"mpn": "XC6206P332MR-G", "lcsc": "C5446"},
    {"mpn": "TLV75733PDBVR", "description": "", "category": ""},
    # a switching family, and no word or code says regulator (#763)
    {"mpn": "AP63205WU-7", "role": "5V"},             # rf-plasma U102
    {"mpn": "TPS54560BDDAR", "role": "+12V_BUCK"},    # rf-plasma U201/U202
    {"mpn": "TLV62569PDDCR", "role": "5 V -> +3V3"},  # linux-computer U302
    {"mpn": "TLV62568DBVR"}, {"mpn": "TPS54233DR"}, {"mpn": "TPS54302DDCR"},
    {"mpn": "LMR33630ADDAR"}, {"mpn": "AP63203WU-7"}, {"mpn": "AP63200WU-7"},
    # outside the first table, each named a switcher by its datasheet (#774)
    {"mpn": "LM5017MRX/NOPB"}, {"mpn": "LM5164DDAR", "role": "aux 5 V"},
    {"mpn": "TPS61040DBVR"}, {"mpn": "TPS61041DBVR"},
])
def test_bare_regulator_blocks_unless_extracted(tmp_path, row):
    lcsc = row.get("lcsc", "C3")
    bom = [{"lcsc": "C3", "attributes": [], **row}]
    pads = {"1": "+5V", "2": "GND", "3": "+3V3"}
    ws = _ws(tmp_path, [("U4", lcsc, pads)], {}, bom)
    assert [v["refs"] for v in _no_attrs(_run(ws))] == [["U4"]]
    ws2 = _ws(tmp_path / "b", [("U4", lcsc, pads)], {lcsc: MCU}, bom)
    assert _no_attrs(_run(ws2)) == []


@pytest.mark.parametrize("ref,mpn", [
    ("U4", "TPS2051BDBVR"),    # a load switch
    ("U4", "TPS61042DRBR"),    # a white-LED driver, next to the TPS61040 boost
    ("U4", "LM51641"),         # a number that only starts with the LM5164
    ("U4", "LM50170"),         # the same for the LM5017
    ("U4", "LM66200DRLR"),     # an ideal diode
    ("U4", "TPS26631RGER"),    # an e-fuse
    ("U4", "TPS3808G33DBVR"),  # a supervisor
    ("U4", "TPS60403DBVR"),    # a charge pump
    ("U4", "TPS92515DGQR"),    # an LED driver
    ("U4", "TLV3501AIDBVR"),   # a comparator
    ("U4", "TLV7031DBVR"),     # a comparator
    ("Q4", "TPS54560BDDAR"),   # a family counts on a U/VR/IC part only
    ("U4", "TPS7A0201PDBVR"),  # the family's adjustable code
    ("Q4", "XC6206P332MR-G"),  # a code counts on a U/VR/IC part only
])
def test_bare_part_with_no_usable_code_does_not_block(tmp_path, ref, mpn):
    bom = [{"lcsc": "C3", "mpn": mpn, "attributes": []}]
    pads = {"1": "+5V", "2": "GND", "3": "+3V3"}
    ws = _ws(tmp_path, [(ref, "C3", pads)], {}, bom)
    assert _no_attrs(_run(ws)) == []


def test_bare_coded_regulator_stays_unknown_and_names_no_rail(tmp_path):
    """The code only makes it a regulator for this finding: is_regulator and
    reg_out_volts stay strict, so no rail gets its volts from the code."""
    bom = [{"lcsc": "C3", "mpn": "XC6206P332MR-G", "attributes": []}]
    pads = {"1": "+5V", "2": "GND", "3": "/RAIL"}
    ws = _ws(tmp_path, [("U4", "C3", pads)], {}, bom)
    p = _run(ws)
    assert [v["refs"] for v in _no_attrs(p)] == [["U4"]]
    assert "/RAIL" not in p["net_volts"]
    assert not cr.is_regulator({"ref": "U4"}, bom[0])


@pytest.mark.parametrize("ref,lcsc,pads,row", [
    # a chip resistor is rated from its package
    ("R1", "C4", {"1": "+5V", "2": "GND"}, {"mpn": "0402WGF"}),
    # attributes do not rate these, so filling them would change nothing
    ("U1", "C4", {"1": "+5V", "2": "GND", "3": "/A"}, {"mpn": "STM32"}),
    ("J1", "C4", {"1": "+5V", "2": "GND"}, {"mpn": "USB-C"}),
    # one net stresses nothing
    ("C1", "C4", {"1": "GND", "2": "GND"}, {"mpn": "CL05"}),
])
def test_bare_parts_that_do_not_block(tmp_path, ref, lcsc, pads, row):
    bom = [{"lcsc": lcsc, "attributes": [], **row}]
    assert _no_attrs(_run(_ws(tmp_path, [(ref, lcsc, pads)], {}, bom))) == []


def test_part_with_no_row_at_all_stays_a_warning(tmp_path):
    # the scaffold's empty parts/: nothing to fill yet, intake must still pass
    ws = _ws(tmp_path, [("D1", "C8", {"1": "+5V", "2": "GND"})], {}, [])
    p = _run(ws)
    assert _no_attrs(p) == [] and _errors(p) == []
    assert any(v["kind"] == "rating_unrated" for v in p["violations"])


def test_rating_someone_verified_does_not_block(tmp_path):
    bom = [{"lcsc": "C8", "mpn": "SM712", "attributes": []}]
    ws = _ws(tmp_path, [("D101", "C8", {"1": "+5V", "2": "GND"})], {}, bom)
    out, _ = cr.run_check(ws / "kicad" / "board.kicad_pcb", ws / "parts",
                          None, None, trusted={"D101"})
    assert not [v for v in out if v["kind"] == "rating_no_attributes"]


def test_no_attributes_is_unknown_in_the_ledger_not_failed(tmp_path):
    # nothing was found wrong with the part: its rating was never looked at
    bom = [{"lcsc": "C8", "mpn": "SM712", "attributes": []}]
    ws = _ws(tmp_path, [("D101", "C8", {"1": "+5V", "2": "GND"})], {}, bom)
    _, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb", ws / "parts",
                            None, None)
    rating = next(f for f in facts["ledger_facts"]
                  if f["subject"] == "D101" and f["kind"] == "rating")
    assert rating["status"] == "unknown" and "parts_enrich" in rating["value"]


# ------------------------------------------- regulator input and output (#708)

def _reg_ws(tmp_path, attrs, pads, names=None, ext=None, **row):
    """A regulator U4 (C9) whose row holds attrs ({name: value}); names:
    {pad: pad name} in a netlist beside the board."""
    bom = [{"lcsc": "C9", "mpn": row.pop("mpn", "AP2112"),
            "attributes": [{"name": k, "value": v} for k, v in attrs.items()],
            "description": "LDO regulator", **row}]
    ws = _ws(tmp_path, [("U4", "C9", pads)], {"C9": ext} if ext else {}, bom)
    if names:
        nodes = " ".join(
            f'(net (name "{pads[p]}") (node (ref "U4") (pin "{p}") '
            f'(pinfunction "{n}")))' for p, n in names.items())
        (ws / "kicad" / "board.net").write_text(f"(export (nets {nodes}))")
    return ws


LDO_PADS = {"1": "+12V", "2": "GND", "3": "+3V3"}


def test_6v_ldo_on_12v_blocks_naming_part_rating_and_rail(tmp_path):
    """The known answer of #708: its row says 6 V, its rail says 12 V."""
    ws = _reg_ws(tmp_path, {"Input Voltage(Max)": "6V", "Voltage Rated": "6V"},
                 LDO_PADS)
    errs = _errors(_run(ws))
    assert len(errs) == 1
    v = errs[0]
    assert v["kind"] == "rating_over_voltage" and v["refs"] == ["U4"]
    assert v["net"] == "+12V" and v["limit_v"] == 6 and v["net_v"] == 12
    for word in ("U4", "AP2112", "6 V", "+12V", "12 V", "Input Voltage(Max)"):
        assert word in v["msg"]


def test_16v_ldo_on_12v_passes_and_stays_unrated_for_its_other_pins(tmp_path):
    ws = _reg_ws(tmp_path, {"Input Voltage(Max)": "16V", "Voltage Rated": "16V"},
                 LDO_PADS, mpn="AP2112-3.3")
    p = _run(ws)
    assert _errors(p) == []
    # only the input was judged: this does not say the part is verified
    assert [v["kind"] for v in p["violations"]] == ["rating_unrated"]


@pytest.mark.parametrize("name,value,errs", [
    ("Voltage - Supply", "2.5V~5.5V", 1),    # the top of a range rates it
    ("Voltage - Supply", "4.5V~28V", 0),
    ("Voltage - Supply", "500mV~5.5V", 1),   # not 500 V
    ("Voltage - Input(DC)", "5.5V~65V", 0),
    ("Operating Voltage", "5.7V", 1),
    ("Voltage Rating", "4.5V~60V", 0),
    ("Output Voltage", "3.3V", 0),           # not an input rating
    ("Dropout Voltage", "250mV", 0),
])
def test_input_rating_read_from_the_names_real_rows_use(
        tmp_path, name, value, errs):
    ws = _reg_ws(tmp_path, {name: value}, LDO_PADS)
    assert len(_errors(_run(ws))) == errs


def test_input_rating_prefers_the_input_row_over_a_generic_one(tmp_path):
    ws = _reg_ws(tmp_path, {"Voltage Rated": "40V",
                            "Input Voltage(Max)": "6V"}, LDO_PADS)
    assert [v["limit_v"] for v in _errors(_run(ws))] == [6]


@pytest.mark.parametrize("row", [
    {"mpn": "XYZ1", "description": "boost converter"},
    {"mpn": "XYZ2", "description": "step-up DC-DC"},
    {"mpn": "XYZ3", "description": "DC-DC", "extra": {"Output Voltage": "24V"}},
])
def test_unnamed_step_up_is_not_judged_by_its_highest_net(tmp_path, row):
    """Its output rail is above its input rating on purpose, and with no pad
    names nothing says which net feeds it."""
    attrs = {"Voltage - Supply": "5.5V", **row.pop("extra", {})}
    ws = _reg_ws(tmp_path, attrs, {"1": "+3V7", "2": "GND", "3": "+24V"}, **row)
    assert _errors(_run(ws)) == []


def test_boost_family_is_not_judged_by_its_highest_net(tmp_path):
    """The TPS61040 takes at most 6 V and makes up to 28 V, and neither its row
    nor a pad name has to say so: +12V here is its output (#774)."""
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "6V"},
                 {"1": "+5V", "2": "GND", "3": "+12V"},
                 mpn="TPS61040DBVR", description="")
    assert _errors(_run(ws)) == []


@pytest.mark.parametrize("mpn", ["LM5017MRX/NOPB", "LM5164DDAR"])
def test_step_down_family_is_judged_by_its_highest_net(tmp_path, mpn):
    """A filled row with no word saying regulator is judged once the family
    says so (#774). The rating here is planted, not the part's own."""
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "24V"},
                 {"1": "+48V", "2": "GND", "3": "+5V"},
                 mpn=mpn, description="")
    assert [(v["kind"], v["net"]) for v in _errors(_run(ws))] == \
        [("rating_over_voltage", "+48V")]


@pytest.mark.parametrize("mpn", ["LM5017MRX/NOPB", "LM5164DDAR", "TPS61040DBVR"])
def test_a_family_entry_names_no_rail(mpn):
    """It says what the part is, not what it makes."""
    assert cr.reg_out_code(mpn) is None
    assert cr.reg_out_volts(mpn) is None


def test_named_pads_judge_the_input_not_the_highest_net(tmp_path):
    attrs = {"Voltage - Supply": "5.5V", "Output Voltage": "2.2V~24V"}
    pads = {"1": "+3V7", "2": "GND", "3": "+24V"}
    names = {"1": "VIN_1", "2": "GND_2", "3": "VOUT_3"}
    ws = _reg_ws(tmp_path, attrs, pads, names, description="boost converter")
    assert _errors(_run(ws)) == []
    ws2 = _reg_ws(tmp_path / "b", attrs, {**pads, "1": "+12V"}, names,
                  description="boost converter")
    assert [(v["net"], v["pin"]) for v in _errors(_run(ws2))] == \
        [("+12V", "U4.1")]


def test_enable_pin_above_the_input_rating_blocks(tmp_path):
    pads = {"1": "+5V", "2": "GND", "3": "+12V", "5": "+3V3"}
    names = {"1": "IN_1", "2": "GND_2", "3": "EN_3", "5": "OUT_5"}
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "6V"}, pads, names)
    errs = _errors(_run(ws))
    assert [(v["net"], v["pin"]) for v in errs] == [("+12V", "U4.3")]
    assert "EN" in errs[0]["msg"]


def test_enable_tied_to_an_over_rated_input_is_one_finding(tmp_path):
    pads = {"1": "+12V", "2": "GND", "3": "+12V", "5": "+3V3"}
    names = {"1": "IN_1", "2": "GND_2", "3": "EN_3", "5": "OUT_5"}
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "6V"}, pads, names)
    assert len(_errors(_run(ws))) == 1


def test_enable_through_a_resistor_is_not_held_to_the_rail(tmp_path):
    """A pull-up's volts are a guess at the pin: the supply rule in run_check
    says the same."""
    bom = [{"lcsc": "C9", "mpn": "AP2112", "description": "LDO regulator",
            "attributes": [{"name": "Voltage - Supply", "value": "6V"}]}]
    pads = {"1": "+5V", "2": "GND", "3": "/EN", "5": "+3V3"}
    ws = _ws(tmp_path, [("U4", "C9", pads),
                        ("R1", "", {"1": "/EN", "2": "+12V"}, "R_0402")],
             {}, bom)
    (ws / "kicad" / "board.net").write_text(
        '(export (nets (net (name "/EN") (node (ref "U4") (pin "3") '
        '(pinfunction "EN_3")))))')
    assert [v for v in _errors(_run(ws)) if v["refs"] == ["U4"]] == []


@pytest.mark.parametrize("attrs,mpn,out_net,errs", [
    ({"Voltage - Supply": "6V", "Output Voltage": "3.3V"}, "AP2112", "+5V", 1),
    ({"Voltage - Supply": "6V", "Output Voltage": "3.3V"}, "AP2112", "+1V8", 1),
    ({"Voltage - Supply": "6V", "Output Voltage": "3.3V"}, "AP2112", "+3V3", 0),
    # the MPN says it when the row doesn't
    ({"Voltage - Supply": "6V"}, "LP5907-3.3", "+5V", 1),
    ({"Voltage - Supply": "6V"}, "LP5907-3.3", "+3V3", 0),
    # an adjustable part reaches anything in its range
    ({"Voltage - Supply": "28V", "Output Voltage": "768mV~7V"}, "T1", "+5V", 0),
    ({"Voltage - Supply": "28V", "Output Voltage": "768mV~7V"}, "T1", "+12V", 1),
    ({"Voltage - Supply": "28V"}, "T1", "+5V", 0),
])
def test_output_rail_against_what_the_row_says_it_makes(
        tmp_path, attrs, mpn, out_net, errs):
    pads = {"1": "+24V" if "28V" in attrs["Voltage - Supply"] else "+5V",
            "2": "GND", "3": out_net}
    names = {"1": "IN_1", "2": "GND_2", "3": "OUT_3"}
    if out_net == pads["1"]:
        pads["1"] = "+5V0"
    ws = _reg_ws(tmp_path, attrs, pads, names, mpn=mpn)
    found = [v for v in _errors(_run(ws))
             if v["kind"] == "rating_regulator_output"]
    assert len(found) == errs
    if errs:
        assert out_net in found[0]["msg"] and "U4" in found[0]["msg"]


@pytest.mark.parametrize("mpn,errs", [
    ("ADP7182-5.0", 0),   # a negative part: the rail's sign is what it makes
    ("ADP7182-3.3", 1),
    ("LT9999-5.0", 0),    # neither family: unknown sign, magnitude only
    ("AMS1117-5.0", 1),   # a positive part can't make -5 V (#744)
    ("AMS1117-3.3", 1)])
def test_unextracted_output_on_a_negative_rail(tmp_path, mpn, errs):
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "15V"},
                 {"1": "+12V", "2": "GND", "3": "-5V"},
                 {"1": "IN", "3": "OUT"}, mpn=mpn)
    assert len([v for v in _errors(_run(ws))
                if v["kind"] == "rating_regulator_output"]) == errs


def test_output_needs_a_named_pad(tmp_path):
    """No pad names: nothing says which net it drives, so it isn't judged."""
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "6V", "Output Voltage": "3.3V"},
                 {"1": "+5V", "2": "GND", "3": "+1V8"})
    assert _errors(_run(ws)) == []


def test_extraction_outranks_the_attribute(tmp_path):
    """Extracted parts are judged pin by pin from the datasheet's rows."""
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "6V"},
                 {"1": "+3V3", "2": "GND", "3": "/A"}, ext=MCU)
    assert _errors(_run(ws)) == []


def test_over_rated_regulator_fails_its_ledger_fact(tmp_path):
    ws = _reg_ws(tmp_path, {"Input Voltage(Max)": "6V"}, LDO_PADS)
    _, got = cr.run_check(ws / "kicad" / "board.kicad_pcb", ws / "parts",
                          None, None)
    rating = {f["subject"]: f for f in got["ledger_facts"]
              if f["kind"] == "rating"}
    assert rating["U4"]["status"] == "failed"
    assert "+12V" in rating["U4"]["value"]


# ------------------------------------- an extracted regulator's output (#721)

def _fixed_ldo(mpn):
    return {"mpn": mpn, "pinout": [
        {"pin": "1", "name": "GND", "type": "ground"},
        {"pin": "2", "name": "VOUT", "type": "power_out"},
        {"pin": "3", "name": "VIN", "type": "power_in"}],
        "abs_max": [{"param": "Input voltage VIN", "value": 15, "unit": "V"}]}


def _fixed_buck(mpn="AP63205WU-7", **over):
    """AP6320x's real pinout: it has no output pin, FB senses the rail."""
    return {"mpn": mpn, "description": "fixed 5.0V output", "pinout": [
        {"pin": "1", "name": "FB", "type": "input"},
        {"pin": "2", "name": "EN", "type": "input"},
        {"pin": "3", "name": "VIN", "type": "power_in"},
        {"pin": "4", "name": "GND", "type": "ground"},
        {"pin": "5", "name": "SW", "type": "output"},
        {"pin": "6", "name": "BST", "type": "passive"}],
        "abs_max": [{"param": "Input voltage VIN", "value": 32, "unit": "V"}],
        **over}


def _buck_pads(fb):
    return {"1": fb, "2": "+12V", "3": "+12V", "4": "GND", "5": "SW_NODE",
            "6": "BST_NODE"}


def _out_errs(p):
    return [v for v in _errors(p) if v["kind"] == "rating_regulator_output"]


def test_extracted_fixed_buck_on_the_wrong_rail_blocks(tmp_path):
    """FB is a fixed part's output, on a rail
    the part can't make (#774)."""
    ws = _ws(tmp_path, [("U4", "C9", _buck_pads("+3V3"))],
             {"C9": _fixed_buck()})
    errs = _out_errs(_run(ws))
    assert [(v["refs"], v["net"], v["pin"]) for v in errs] == \
        [(["U4"], "+3V3", "U4.1")]
    assert errs[0]["net_v"] == 3.3


def test_extracted_fixed_buck_on_its_own_rail_passes(tmp_path):
    ws = _ws(tmp_path, [("U4", "C9", _buck_pads("+5V"))],
             {"C9": _fixed_buck()})
    p = _run(ws)
    assert _out_errs(p) == [] and _unjudged(p) == []


def test_extracted_fixed_buck_reads_its_mpn_with_no_description(tmp_path):
    ws = _ws(tmp_path, [("U4", "C9", _buck_pads("+12V"))],
             {"C9": _fixed_buck("AP63203WU-7", description="")})
    assert [v["net"] for v in _out_errs(_run(ws))] == ["+12V"]


def test_extraction_with_no_mpn_uses_the_rows_for_a_fixed_buck(tmp_path):
    ws = _ws(tmp_path, [("U4", "C9", _buck_pads("+3V3"))],
             {"C9": _fixed_buck("", description="")},
             [{"lcsc": "C9", "mpn": "AP63205WU-7", "attributes": []}])
    assert [v["net"] for v in _out_errs(_run(ws))] == ["+3V3"]


@pytest.mark.parametrize("mpn,over", [
    # an adjustable part's FB sits on a divider, whatever else is said
    ("AP63200WU-7", {"description": "buck, adjustable output"}),
    ("AP63201WU-7", {"description": "", "output_voltage": 5.0}),
    ("TPS54560BDDAR", {"description": "", "output_voltage": 5.0}),
    ("LM5164DDAR", {"description": "", "output_voltage": 5.0}),
    ("LMR33630ADDAR", {"description": "fixed 5.0V output"}),
])
@pytest.mark.parametrize("fb", ["+3V3", "FB_DIV"])
def test_adjustable_parts_fb_stays_out(tmp_path, mpn, over, fb):
    ws = _ws(tmp_path, [("U4", "C9", _buck_pads(fb))],
             {"C9": _fixed_buck(mpn, **over)})
    p = _run(ws)
    assert _out_errs(p) == [] and _unjudged(p) == []


def test_fixed_buck_fb_on_a_net_with_no_stated_rail_is_not_judged(tmp_path):
    ws = _ws(tmp_path, [("U4", "C9", _buck_pads("FB_SENSE"))],
             {"C9": _fixed_buck()})
    assert _out_errs(_run(ws)) == []


def test_fixed_buck_number_on_a_non_regulator_ref_is_not_judged(tmp_path):
    ws = _ws(tmp_path, [("Q4", "C9", _buck_pads("+3V3"))],
             {"C9": _fixed_buck(description="")})
    assert _out_errs(_run(ws)) == []


def test_a_fixed_bucks_fb_does_not_name_its_rail(tmp_path):
    """FB counts as an output only for judging: an unnamed net's volts still
    don't come from the part's MPN."""
    ws = _ws(tmp_path, [("U4", "C9", _buck_pads("VSENSE"))],
             {"C9": _fixed_buck()})
    assert "VSENSE" not in _run(ws)["net_volts"]


def _unjudged(p):
    return [v for v in p["violations"]
            if v["kind"] == "rating_regulator_output_unjudged"]


AMS5 = _fixed_ldo("AMS1117-5.0")
LDO_ON_3V3 = {"1": "GND", "2": "+3V3", "3": "+5V"}


def test_extracted_5v_ldo_on_a_3v3_rail_blocks(tmp_path):
    p = _run(_ws(tmp_path, [("U4", "C9", LDO_ON_3V3)], {"C9": AMS5}))
    errs = _out_errs(p)
    assert [(e["pin"], e["net"], e["net_v"]) for e in errs] == \
        [("U4.2", "+3V3", 3.3)]
    assert "AMS1117-5.0" in errs[0]["msg"]
    assert len(_errors(p)) == 1


def test_extracted_3v3_ldo_on_a_3v3_rail_passes(tmp_path):
    ws = _ws(tmp_path, [("U4", "C9", LDO_ON_3V3)],
             {"C9": _fixed_ldo("AMS1117-3.3")})
    assert _errors(_run(ws)) == []


def test_a_cap_on_the_rail_is_judged_at_the_rail_not_the_mpn(tmp_path):
    """The MPN's 5 V must not replace the board's 3.3 V: a 3.3 V cap on +3V3
    holds, whatever wrong part drives it, and only the LDO is flagged."""
    bom = [{"lcsc": "C7", "mpn": "CAP",
            "attributes": [{"name": "Voltage Rated", "value": "3.3V"}]}]
    ws = _ws(tmp_path, [("U4", "C9", LDO_ON_3V3),
                        ("C1", "C7", {"1": "+3V3", "2": "GND"})],
             {"C9": AMS5}, bom)
    p = _run(ws)
    assert p["net_volts"]["+3V3"] == {"v": 3.3, "src": "net name"}
    assert [e["kind"] for e in _errors(p)] == ["rating_regulator_output"]


def test_extracted_output_is_judged_against_constraints_and_power_tree(
        tmp_path):
    tree = "| Rail | Nom V |\n|---|---|\n| /VA | 3.3 |\n"
    pads = {"1": "GND", "2": "/VA", "3": "+5V"}
    p = _run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": AMS5}, tree=tree))
    assert [e["net"] for e in _out_errs(p)] == ["/VA"]
    c = tmp_path / "c.json"
    c.write_text(json.dumps({"voltages": [{"net": "+3V3", "voltage": 5}]}))
    p = _run(_ws(tmp_path / "b", [("U4", "C9", LDO_ON_3V3)], {"C9": AMS5}), c)
    assert _errors(p) == []  # constraints say +3V3 is really 5 V here


@pytest.mark.parametrize("mpn,rail,errs", [
    ("AMS1117-5.0", "+5V", 0),
    ("AMS1117-3.3", "+3V0", 0),   # exactly 10 % low
    ("AMS1117-5.0", "+4V4", 1),   # just past it
    ("AMS1117-5.0", "+5V6", 1),
    ("ADP7182-5.0", "-5V", 0),    # a negative part: the MPN has no sign
    ("ADP7182-5.0", "-3V3", 1),
])
def test_extracted_output_tolerance(tmp_path, mpn, rail, errs):
    pads = {"1": "GND", "2": rail, "3": "+12V"}
    ws = _ws(tmp_path, [("U4", "C9", pads)], {"C9": _fixed_ldo(mpn)})
    assert len(_out_errs(_run(ws))) == errs


def test_unnamed_rail_takes_the_mpn_and_is_not_judged_by_it(tmp_path):
    """Nothing but the MPN says what /RAIL is: it fills the net, and a second
    regulator isn't judged against the first one's MPN."""
    pads = {"1": "GND", "2": "/RAIL", "3": "+12V"}
    ws = _ws(tmp_path, [("U4", "C9", pads), ("U5", "C8", pads)],
             {"C9": AMS5, "C8": _fixed_ldo("AMS1117-3.3")})
    p = _run(ws)
    assert p["net_volts"]["/RAIL"] == {"v": 5.0, "src": "regulator U4"}
    assert _out_errs(p) == []


def test_extracted_output_read_from_the_row_when_the_mpn_has_none(tmp_path):
    """TLV75733-style: no decimal in the MPN, the row says 3.3 V."""
    bom = [{"lcsc": "C9", "mpn": "TLV75733PDBVR", "description": "LDO regulator",
            "attributes": [{"name": "Output Voltage", "value": "3.3V"}]}]
    pads = {"1": "GND", "2": "+5V", "3": "+12V"}
    p = _run(_ws(tmp_path, [("U4", "C9", pads)],
                 {"C9": _fixed_ldo("TLV75733PDBVR")}, bom))
    assert [e["net"] for e in _out_errs(p)] == ["+5V"]
    assert "3.3V" in _out_errs(p)[0]["rating"]


@pytest.mark.parametrize("ref,mpn,row,ptype,rail", [
    ("U4", "ACME1234", {}, "power_out", "+5V"),        # no output known
    ("U4", "AMS1117-3.3", {}, "output", "+5V"),        # not a power output
    ("Q4", "AMS1117-3.3", {}, "power_out", "+5V"),     # not a regulator's ref
    ("U4", "EFUSE1",                                   # not called a regulator
     {"description": "eFuse",
      "attributes": [{"name": "Output Voltage", "value": "3.3V"}]},
     "power_out", "+5V"),
])
def test_extracted_parts_whose_output_is_not_judged(
        tmp_path, ref, mpn, row, ptype, rail):
    ext = _fixed_ldo(mpn)
    ext["pinout"][1]["type"] = ptype
    bom = [dict(row, lcsc="C9", mpn=mpn)] if row else None
    ws = _ws(tmp_path, [(ref, "C9", {"1": "GND", "2": rail, "3": "+12V"})],
             {"C9": ext}, bom)
    assert _out_errs(_run(ws)) == []


def test_bare_buck_stays_unknown_and_names_no_rail(tmp_path):
    """As for a code: is_regulator and reg_out_volts stay strict (#763)."""
    bom = [{"lcsc": "C3", "mpn": "AP63205WU-7", "attributes": []}]
    pads = {"1": "+24V", "2": "GND", "3": "/RAIL"}
    ws = _ws(tmp_path, [("U4", "C3", pads)], {}, bom)
    p = _run(ws)
    assert [v["refs"] for v in _no_attrs(p)] == [["U4"]]
    assert "/RAIL" not in p["net_volts"]
    assert not cr.is_regulator({"ref": "U4"}, bom[0])


def test_bare_buck_blocks_once_per_bom_code(tmp_path):
    """linux-computer U302-U304: three parts, one row to fill (#763)."""
    bom = [{"lcsc": "C3", "mpn": "TLV62569PDDCR", "attributes": [],
            "role": "5 V -> +3V3"}]
    board = [(f"U{n}", "C3", {"1": "+5V", "2": "GND", "3": "+3V3"})
             for n in (302, 303, 304)]
    found = _no_attrs(_run(_ws(tmp_path, board, {}, bom)))
    assert [v["refs"] for v in found] == [["U302", "U303", "U304"]]


@pytest.mark.parametrize("mpn,volts", [
    ("RT9080-33GJ5", 3.3), ("RT9013-18GB", 1.8), ("RT9193-50GB", 5.0),
    ("TLV75733PDBVR", 3.3), ("TLV70018DSER", 1.8), ("TPS78233DDCR", 3.3),
    ("TPS7A0233PDBVR", 3.3), ("TPS7B6950QDCYRQ1", 5.0),
    ("TPS7A2045PDBVR", 4.5),   # TPS7A20 datasheet rev H, Table 8-1
    ("XC6206P332MR", 3.3), ("XC6206P302MR", 3.0),
    ("HT7333-A", 3.3), ("HT7850", 5.0), ("ME6211C33M5G", 3.3),
    # a whole part number, not a tenths code (Diodes AP63200 datasheet, p. 1)
    ("AP63203WU-7", 3.3), ("AP63205WU-7", 5.0),
])
def test_output_code_in_a_known_regulator_family(mpn, volts):
    assert cr.reg_out_code(mpn) == volts


@pytest.mark.parametrize("mpn", [
    "TPS54233DR", "TPS563201DDCR", "TPS61023DRLR",   # not an LDO family
    "TLV7031DBVR",                                   # comparator
    "TPS79601DCQR", "TPS7A4701RGWR", "TLV75801PDRVR",  # 01: adjustable
    "TPS793285DBVR",                                 # 2.85 V: not a two-digit code
    "RT9711ACBG", "HT1621B", "ME2108A33M3G",         # no code, or not this family
    "AMS1117", "AP63200WU-7", "AP63201WU-7", "",     # AP6320[01]: adjustable
])
def test_no_output_code_is_a_guess(mpn):
    assert cr.reg_out_code(mpn) is None


@pytest.mark.parametrize("mpn", ["RT9080-33GJ5", "TLV75733PDBVR"])
@pytest.mark.parametrize("rail,errs", [
    ("+5V0", 1), ("+5V", 1), ("+3V3", 0), ("+3V0", 0), ("+2V5", 1)])
def test_extracted_output_read_from_the_mpns_code(tmp_path, mpn, rail, errs):
    """No row at all and no voltage in the MPN's usual forms (#729)."""
    pads = {"1": "GND", "2": rail, "3": "+12V"}
    p = _run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": _fixed_ldo(mpn)}))
    found = _out_errs(p)
    assert [e["net"] for e in found] == ([rail] if errs else [])
    assert all(mpn in e["rating"] for e in found)
    assert _unjudged(p) == []


@pytest.mark.parametrize("extra", [
    {"output_voltage": 3.3},
    {"description": "1 A low-dropout LDO, fixed 3.3V output"},
    {"description": "Low-noise LDO, 3.3 V fixed"},
])
@pytest.mark.parametrize("rail,errs", [("+5V", 1), ("+3V3", 0)])
def test_extracted_output_read_from_the_extraction(tmp_path, extra, rail, errs):
    ext = dict(_fixed_ldo("ACME1234"), **extra)
    pads = {"1": "GND", "2": rail, "3": "+12V"}
    p = _run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": ext}))
    assert [e["net"] for e in _out_errs(p)] == ([rail] if errs else [])
    assert _unjudged(p) == []
    if errs:
        assert "extraction" in _out_errs(p)[0]["rating"]


def test_extractions_output_field_beats_the_mpns_code(tmp_path):
    ext = dict(_fixed_ldo("TLV75733PDBVR"), output_voltage=5.0)
    pads = {"1": "GND", "2": "+5V", "3": "+12V"}
    assert _out_errs(_run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": ext}))) == []


def test_extracted_description_that_also_says_adjustable_is_no_voltage(tmp_path):
    ext = dict(_fixed_ldo("ACME1234"),
               description="LDO, adjustable or fixed 3.3V output")
    pads = {"1": "GND", "2": "+5V", "3": "+12V"}
    p = _run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": ext}))
    assert _out_errs(p) == [] and len(_unjudged(p)) == 1


@pytest.mark.parametrize("words", [
    {"ext": {"description": "Adjustable LDO regulator"}},
    {"row": {"description": "LDO regulator"}},
    {"row": {"role": "12 V to 5 V regulator"}},
])
def test_extracted_regulator_with_no_known_output_warns(tmp_path, words):
    ext = dict(_fixed_ldo("ACME1234"), **words.get("ext", {}))
    bom = [dict(words["row"], lcsc="C9", mpn="ACME1234")] \
        if "row" in words else None
    pads = {"1": "GND", "2": "+5V", "3": "+12V"}
    p = _run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": ext}, bom))
    assert _out_errs(p) == []
    got = _unjudged(p)
    assert len(got) == 1 and got[0]["severity"] == "warning"
    assert got[0]["refs"] == ["U4"] and got[0]["net"] == "+5V"
    assert "wasn't judged" in got[0]["msg"] and "ACME1234" in got[0]["msg"]


@pytest.mark.parametrize("ref,ext_words,row_words,ptype", [
    # nothing calls these regulators, or something says they aren't one
    ("U4", {}, None, "power_out"),
    ("U4", {"description": "60 V eFuse"},
     {"role": "4.5 V to 60 V eFuse / load switch"}, "power_out"),
    ("U4", {"description": "LDO with load switch"}, None, "power_out"),
    ("U4", {"description": "Li-ion charger with power path, DC-DC"}, None,
     "power_out"),
    ("U4", {"description": "Precision voltage reference"},
     {"role": "regulator"}, "power_out"),
    # a USB bridge's "to 3.3V" is no regulator's words, in an extraction
    ("U4", {"description": "USB to 3.3V UART bridge"}, None, "power_out"),
    ("Q4", {"description": "LDO regulator"}, None, "power_out"),
    ("U4", {"description": "LDO regulator"}, None, "output"),
])
def test_extracted_parts_that_dont_warn(tmp_path, ref, ext_words, row_words,
                                        ptype):
    ext = dict(_fixed_ldo("ACME1234"), **ext_words)
    ext["pinout"][1]["type"] = ptype
    bom = [dict(row_words, lcsc="C9", mpn="ACME1234")] if row_words else None
    pads = {"1": "GND", "2": "+5V", "3": "+12V"}
    p = _run(_ws(tmp_path, [(ref, "C9", pads)], {"C9": ext}, bom))
    assert _unjudged(p) == [] and _out_errs(p) == []


def test_unjudged_warning_needs_the_output_pin_on_the_board(tmp_path):
    ext = dict(_fixed_ldo("ACME1234"), description="LDO regulator")
    pads = {"1": "GND", "3": "+12V"}
    assert _unjudged(_run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": ext}))) == []


def test_extraction_schema_takes_an_output_voltage():
    field = datasheet_extract.DATASHEET_SCHEMA["properties"]["output_voltage"]
    assert field["type"] == "number" and field["exclusiveMinimum"] == 0


def test_a_bad_output_voltage_in_an_extraction_is_no_voltage(tmp_path):
    ext = dict(_fixed_ldo("ACME1234"), output_voltage="3.3 V",
               description="LDO regulator")
    pads = {"1": "GND", "2": "+5V", "3": "+12V"}
    p = _run(_ws(tmp_path, [("U4", "C9", pads)], {"C9": ext}))
    assert _out_errs(p) == [] and len(_unjudged(p)) == 1


def test_wrong_output_fails_an_extracted_regulators_ledger_fact(tmp_path):
    ws = _ws(tmp_path, [("U4", "C9", LDO_ON_3V3)], {"C9": AMS5})
    _, got = cr.run_check(ws / "kicad" / "board.kicad_pcb", ws / "parts",
                          None, None)
    rating = {f["subject"]: f for f in got["ledger_facts"]
              if f["kind"] == "rating"}
    assert rating["U4"]["status"] == "failed"
    assert "+3V3" in rating["U4"]["value"]


# #744: a filled row and no extraction, its voltage only in the MPN's code.
CODED_ATTRS = {"Input Voltage": "6V", "Output Current": "200mA"}
CODED_NAMES = {"1": "IN", "3": "OUT"}


@pytest.mark.parametrize("mpn,rail,errs", [
    ("TLV75733PDBVR", "+1V8", 1),   # the issue's known answer
    ("TLV75733PDBVR", "+3V3", 0),
    ("TLV75733PDBVR", "+3V0", 0),   # exactly 10 % low
    ("TLV75733PDBVR", "+2V9", 1),   # just past it
    ("RT9080-33GJ5", "+1V8", 1),
    ("XC6206P332MR-G", "+3V3", 0),
])
@pytest.mark.parametrize("words", ["LDO regulator", ""])
def test_unextracted_output_from_the_mpn_code(tmp_path, mpn, rail, errs, words):
    """The code judges with or without the row's words saying regulator."""
    ws = _reg_ws(tmp_path, CODED_ATTRS, {"1": "+5V", "2": "GND", "3": rail},
                 CODED_NAMES, mpn=mpn, description=words)
    p = _run(ws)
    found = _out_errs(p)
    assert len(found) == errs
    assert _unjudged(p) == []
    if errs:
        assert "MPN code" in found[0]["rating"] and "U4" in found[0]["msg"]


def test_the_row_outranks_the_mpn_code(tmp_path):
    ws = _reg_ws(tmp_path, dict(CODED_ATTRS, **{"Output Voltage": "1.8V"}),
                 {"1": "+5V", "2": "GND", "3": "+1V8"}, CODED_NAMES,
                 mpn="TLV75733PDBVR")
    assert _out_errs(_run(ws)) == []


def test_a_code_gives_the_row_its_input_check_too(tmp_path):
    ws = _reg_ws(tmp_path, CODED_ATTRS, {"1": "+12V", "2": "GND", "3": "+3V3"},
                 CODED_NAMES, mpn="TLV75733PDBVR", description="")
    assert [v["kind"] for v in _errors(_run(ws))] == ["rating_over_voltage"]


@pytest.mark.parametrize("mpn", [
    "TPS2051B",     # a load switch
    "TLV7031",      # a comparator
    "TPS7A0201",    # the family's adjustable code
])
def test_an_mpn_with_no_code_and_no_words_is_no_regulator(tmp_path, mpn):
    ws = _reg_ws(tmp_path, CODED_ATTRS, {"1": "+5V", "2": "GND", "3": "+1V8"},
                 CODED_NAMES, mpn=mpn, description="")
    p = _run(ws)
    assert _out_errs(p) == [] and _unjudged(p) == []


BUCK_ATTRS = {"Input Voltage": "4.5V~32V", "Output Current": "2A"}
BUCK_NAMES = {"1": "VIN", "3": "FB"}


@pytest.mark.parametrize("mpn,vin,rail,kinds", [
    ("AP63205WU-7", "3.8V~32V", "+5V", []),
    ("AP63205WU-7", "3.8V~32V", "+3V3", ["rating_regulator_output"]),
    ("AP63203WU-7", "3.8V~32V", "+3V3", []),
    ("AP63203WU-7", "3.8V~32V", "+5V", ["rating_regulator_output"]),
    ("AP63205WU-7", "3.8V~18V", "+5V", ["rating_over_voltage"]),
    ("TLV62569PDDCR", "2.5V~5.5V", "+3V3", ["rating_over_voltage"]),
    ("TPS54560BDDAR", "4.5V~60V", "+5V", []),
])
def test_filled_buck_is_compared(tmp_path, mpn, vin, rail, kinds):
    """A fixed part's FB is its output (the AP63205 datasheet's VFB is 4.95-5.05
    V), so its rail is judged by what it makes; the row's limit judges VIN."""
    ws = _reg_ws(tmp_path, {"Input Voltage": vin, "Output Current": "2A"},
                 {"1": "+24V", "2": "GND", "3": rail}, BUCK_NAMES, mpn=mpn,
                 description="")
    p = _run(ws)
    assert _no_attrs(p) == []
    assert [v["kind"] for v in _errors(p)] == kinds


@pytest.mark.parametrize("mpn", ["TPS54560BDDAR", "AP63200WU-7"])
def test_an_adjustable_bucks_fb_is_not_its_output(tmp_path, mpn):
    """An adjustable part's FB sits on a divider, not on the rail: a stated
    Output Voltage says nothing about it."""
    ws = _reg_ws(tmp_path, {"Input Voltage": "4.5V~32V",
                            "Output Voltage": "5V"},
                 {"1": "+12V", "2": "GND", "3": "+3V3"}, BUCK_NAMES, mpn=mpn,
                 description="")
    assert _out_errs(_run(ws)) == []


def test_filled_adjustable_buck_says_its_output_is_unjudged(tmp_path):
    ws = _reg_ws(tmp_path, BUCK_ATTRS, {"1": "+12V", "2": "GND", "3": "/FB"},
                 BUCK_NAMES, mpn="TPS54560BDDAR", description="")
    p = _run(ws)
    assert _errors(p) == [] and len(_unjudged(p)) == 1


def test_code_does_not_name_the_rail(tmp_path):
    """reg_out_volts stays strict: the code judges, it never sets a net."""
    ws = _reg_ws(tmp_path, CODED_ATTRS, {"1": "+5V", "2": "GND", "3": "/RAIL"},
                 CODED_NAMES, mpn="TLV75733PDBVR")
    p = _run(ws)
    assert "/RAIL" not in p["net_volts"] and _errors(p) == []


def test_unextracted_regulator_nothing_gives_an_output_for_warns(tmp_path):
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "6V"},
                 {"1": "+5V", "2": "GND", "3": "+1V8"}, CODED_NAMES,
                 mpn="TPS54302DDCR", description="buck converter")
    p = _run(ws)
    assert [v["net"] for v in _unjudged(p)] == ["+1V8"]
    assert _unjudged(p)[0]["severity"] == "warning"
    assert "U4" in _unjudged(p)[0]["msg"] and _errors(p) == []


def test_unextracted_unjudged_warning_with_no_output_pad_named(tmp_path):
    """No pad names, or none called an output: still no voltage from anywhere,
    so it says so rather than staying silent."""
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "6V"},
                 {"1": "+5V", "2": "GND", "3": "/SW"},
                 mpn="SY8089A1AAC", description="buck converter")
    found = _unjudged(_run(ws))
    assert [v["net"] for v in found] == [None]
    assert "wasn't judged" in found[0]["msg"]


@pytest.mark.parametrize("attrs,row", [
    ({"Voltage - Supply": "6V", "Output Voltage": "3.3V"}, {}),   # the row says
    ({"Voltage - Supply": "6V"}, {"mpn": "AP2112-3.3"}),          # the MPN says
    ({"Voltage - Supply": "6V"}, {"mpn": "TLV75733PDBVR"}),       # the code says
    ({"Voltage - Supply": "6V"}, {"description": "load switch regulator"}),
    ({"Voltage - Supply": "6V"}, {"mpn": "XYZ", "description": "a thing"}),
])
def test_unextracted_parts_that_dont_warn(tmp_path, attrs, row):
    ws = _reg_ws(tmp_path, attrs, {"1": "+5V", "2": "GND", "3": "+3V3"},
                 CODED_NAMES, **row)
    assert _unjudged(_run(ws)) == []


@pytest.mark.parametrize("mpn,rail,errs", [
    ("AMS1117-5.0", "-5V", 1),     # positive family on a negative rail
    ("L7805CV", "-5V", 1),
    ("TLV75733PDBVR", "-3V3", 1),
    ("ADP7182-5.0", "+5V", 1),     # negative family on a positive rail
    ("TPS72325DBVR", "+2V5", 1),   # coded like a positive TI part, but negative
    ("TPS72325DBVR", "-2V5", 0),
    ("LT1964-5.0", "-5V", 0),
    ("MC7905-5.0", "-5V", 0),
    ("LT9999-5.0", "-5V", 0),      # neither: no false block
    ("LT9999-5.0", "+5V", 0),
])
def test_extracted_output_sign(tmp_path, mpn, rail, errs):
    pads = {"1": "GND", "2": rail, "3": "+12V"}
    ws = _ws(tmp_path, [("U4", "C9", pads)], {"C9": _fixed_ldo(mpn)})
    found = _out_errs(_run(ws))
    assert len(found) == errs
    if errs:
        assert "positive" in found[0]["msg"] or "negative" in found[0]["msg"]


@pytest.mark.parametrize("text,rail,errs", [
    ("Positive LDO regulator", "-5V", 1),
    ("Negative LDO regulator", "-5V", 0),
    ("Negative LDO regulator", "+5V", 1),
    ("Positive and negative dual regulator", "-5V", 0),
    ("Positive and negative dual regulator", "+5V", 0),
])
def test_the_words_tell_the_sign(tmp_path, text, rail, errs):
    ext = dict(_fixed_ldo("LT9999-5.0"), description=text)
    pads = {"1": "GND", "2": rail, "3": "+12V"}
    assert len(_out_errs(_run(_ws(tmp_path / "e", [("U4", "C9", pads)],
                                  {"C9": ext})))) == errs
    ws = _reg_ws(tmp_path / "r", {"Voltage - Supply": "15V"},
                 {"1": "+12V", "2": "GND", "3": rail}, CODED_NAMES,
                 mpn="LT9999-5.0", description=text)
    assert len(_out_errs(_run(ws))) == errs


@pytest.mark.parametrize("attr,rail,errs", [
    ({"Output Configuration": "Positive"}, "-5V", 1),
    ({"Output Configuration": "Negative"}, "-5V", 0),
    ({"Output Voltage": "-5V"}, "-5V", 0),
    ({"Output Voltage": "-5V"}, "+5V", 1),
])
def test_the_row_attributes_tell_the_sign(tmp_path, attr, rail, errs):
    ws = _reg_ws(tmp_path, dict({"Voltage - Supply": "15V"}, **attr),
                 {"1": "+12V", "2": "GND", "3": rail}, CODED_NAMES,
                 mpn="LT9999-5.0", description="LDO regulator")
    assert len(_out_errs(_run(ws))) == errs


def test_a_floating_positive_part_is_not_called_wrong(tmp_path):
    """No pad on ground: its own ground is at another rail, so a positive part
    can sit on a net below board ground on purpose."""
    pads = {"1": "-12V", "2": "-5V", "3": "+12V"}
    ws = _ws(tmp_path, [("U4", "C9", pads)], {"C9": AMS5})
    assert _out_errs(_run(ws)) == []


def test_sign_is_not_judged_on_a_rail_the_mpn_named(tmp_path):
    """/RAIL = +5 here only because the MPN's 5.0 filled it, with no sign: that
    can't say a negative part is on a positive rail."""
    ws = _reg_ws(tmp_path, {"Voltage - Supply": "15V"},
                 {"1": "+12V", "2": "GND", "3": "/RAIL"}, CODED_NAMES,
                 mpn="ADP7182-5.0")
    assert _errors(_run(ws)) == []


# ---------------------------------------------- suspect datasheets (#706)

def _suspect_ws(tmp_path, index):
    ws = _ws(tmp_path, [("U1", "C1", {"1": "+3V3", "2": "GND", "3": "+3V3"})],
             {"C1": MCU})
    d = ws / "parts" / "datasheets"
    d.mkdir()
    if index is not None:
        (d / "index.json").write_text(json.dumps({"C1": index}))
    return ws


def _u1(p, kind):
    return next(f for f in p["ledger_facts"] if f["subject"] == "U1"
                and f["kind"] == kind)


def test_suspect_datasheet_facts_are_not_verified(tmp_path):
    ws = _suspect_ws(tmp_path, {"status": "suspect", "reasons": [
        "MPN TESTMCU not in the text of pages 1-2"]})
    viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                               ws / "parts", None, None)
    for kind in ("rating", "pinout"):
        f = _u1(facts, kind)
        assert f["status"] == "unknown" and "suspect" in f["value"], f
        assert f["source"] == ""
    w = [v for v in viol if v["kind"] == "rating_datasheet_suspect"]
    assert len(w) == 1 and w[0]["severity"] == "warning", viol
    assert "TESTMCU not in the text" in w[0]["msg"] and w[0]["refs"] == ["U1"]
    assert not _errors({"violations": viol})


def test_ok_datasheet_index_still_verifies(tmp_path):
    ws = _suspect_ws(tmp_path, {"status": "ok", "reasons": []})
    viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                               ws / "parts", None, None)
    assert _u1(facts, "rating")["status"] == "verified"
    assert _u1(facts, "pinout")["status"] == "verified"
    assert not [v for v in viol if v["kind"] == "rating_datasheet_suspect"]


def test_notice_pdf_on_disk_is_suspect_even_without_an_index(tmp_path):
    from test_datasheet_fetch import NOTICE
    ws = _suspect_ws(tmp_path, None)
    (ws / "parts" / "datasheets" / "C1.pdf").write_bytes(NOTICE)
    viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                               ws / "parts", None, None)
    assert _u1(facts, "rating")["status"] == "unknown"
    assert [v for v in viol if v["kind"] == "rating_datasheet_suspect"]


# ------------------------------------ unassessed datasheets (#755)

def test_old_index_entry_with_no_status_is_unassessed_not_ok(tmp_path):
    ws = _suspect_ws(tmp_path, {"url": "u", "sha256": "s", "mpn": "TESTMCU"})
    viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                               ws / "parts", None, None)
    w = [v for v in viol if v["kind"] == "rating_datasheet_unassessed"]
    assert len(w) == 1 and w[0]["severity"] == "warning", viol
    assert w[0]["refs"] == ["U1"] and "C1" in w[0]["msg"]
    assert "--fetch" in w[0]["msg"] and "no PDF" in w[0]["msg"]
    assert facts["unassessed_datasheets"] == ["C1"]
    assert not [v for v in viol if v["kind"] == "rating_datasheet_suspect"]
    # a warning, and the facts stand: no file to judge is not a judgement
    assert _u1(facts, "rating")["status"] != "unknown" \
        or "suspect" not in _u1(facts, "rating")["value"]


def test_unassessed_warning_is_one_line_for_the_whole_board(tmp_path):
    ws = _suspect_ws(tmp_path, {"url": "u"})
    d = ws / "parts" / "datasheets"
    (d / "index.json").write_text(json.dumps({"C1": {"status": "unassessed",
                                                     "reasons": ["no PDF"]}}))
    viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                               ws / "parts", None, None)
    assert len([v for v in viol
                if v["kind"] == "rating_datasheet_unassessed"]) == 1
    assert facts["unassessed_datasheets"] == ["C1"]


def test_assessed_or_unrecorded_datasheet_is_not_unassessed(tmp_path):
    for index in ({"status": "ok", "reasons": []}, None):
        ws = _suspect_ws(tmp_path / str(bool(index)), index)
        viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                                   ws / "parts", None, None)
        assert facts["unassessed_datasheets"] == []
        assert not [v for v in viol
                    if v["kind"] == "rating_datasheet_unassessed"]


def test_a_pdf_on_disk_with_no_index_entry_is_judged_by_the_check(tmp_path):
    # #768: nobody has to run --assess; parts.json names the part, so a wrong
    # PDF takes its facts down at the first check
    from test_datasheet_fetch import WRONG
    ws = _suspect_ws(tmp_path, None)
    (ws / "parts" / "parts.json").write_text(json.dumps(
        {"parts": [{"lcsc": "C1", "mpn": "TESTMCU"}]}))
    d = ws / "parts" / "datasheets"
    (d / "C1.pdf").write_bytes(WRONG)
    viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                               ws / "parts", None, None)
    assert facts["unassessed_datasheets"] == []
    assert [v["kind"] for v in viol if v["kind"].startswith("rating_datasheet")
            ] == ["rating_datasheet_suspect"]
    assert _u1(facts, "rating")["status"] == "unknown"
    assert json.loads((d / "index.json").read_text())["C1"]["status"] == "suspect"


def test_old_entry_with_a_wrong_pdf_is_suspect_with_no_assess_run(tmp_path):
    # the known answer in small: fmcw-radar's 44 verified held only because no
    # one had run --assess
    from test_datasheet_fetch import WRONG
    ws = _suspect_ws(tmp_path, {"url": "u", "sha256": "s", "mpn": "TESTMCU"})
    (ws / "parts" / "datasheets" / "C1.pdf").write_bytes(WRONG)
    viol, facts = cr.run_check(ws / "kicad" / "board.kicad_pcb",
                               ws / "parts", None, None)
    assert "C1" in facts["suspect_datasheets"]
    assert _u1(facts, "rating")["status"] == "unknown"
    assert not [v for v in viol if v["kind"] == "rating_datasheet_unassessed"]
