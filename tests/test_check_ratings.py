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
