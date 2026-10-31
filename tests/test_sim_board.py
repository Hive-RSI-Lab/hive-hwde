"""sim_board.py (ngspice on a board's own netlist) and lib/engcalc.py.

A small synthetic board carries one planted fault per finding the script
makes: a 100 ohm 0603 across +5V (overpower), an FB divider set 10 % off
its rail, a 0402 10 Mohm bleeder on +60V (over its 50 V Umax at 0.36 mW),
a 470 ohm I2C pull-up (too strong for 3 mA), an LED a GPIO drives
at ~26 mA, a 10 V X5R cap on 9 V (DC bias), a P-FET whose gate a GPIO pulls
to ground from 20 V, an enable RC that settles below 0.7 VDD - and one RC
enable that is fine. The engine tests run wherever the KiCad-bundled ngspice
is (bin/hwde test exports it); they skip elsewhere.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import engcalc  # noqa: E402
import sim_board  # noqa: E402
from lib import env  # noqa: E402

# ref, value, footprint, {pin: (net, pinfunction)}
BOARD = [
    ("U1", "TPS54302", "SOT-23-6", {"1": ("GND", "GND"), "3": ("+12V", "VIN"),
                                    "4": ("/FB5", "FB"), "2": ("/SW", "SW")}),
    ("R1", "100k", "R_0402_1005Metric", {"1": ("+5V", ""), "2": ("/FB5", "")}),
    ("R2", "15k", "R_0402_1005Metric", {"1": ("/FB5", ""), "2": ("GND", "")}),
    ("R3", "100R", "R_0603_1608Metric", {"1": ("+5V", ""), "2": ("GND", "")}),
    ("U2", "MCU", "QFN-32", {"1": ("+3V3", "VDD"), "2": ("GND", "GND"),
                             "3": ("/SDA", "SDA"), "4": ("/SCL", "SCL"),
                             "5": ("/LED", "IO5"), "6": ("/EN", "EN"),
                             "7": ("/GATE_PULL", "IO7")}),
    ("R4", "470", "R_0402_1005Metric", {"1": ("+3V3", ""), "2": ("/SDA", "")}),
    ("R5", "4.7k", "R_0402_1005Metric", {"1": ("+3V3", ""), "2": ("/SCL", "")}),
    ("R6", "22", "R_0402_1005Metric", {"1": ("/LED", ""), "2": ("/LED_A", "")}),
    ("D1", "red", "LED_0603_1608Metric", {"1": ("GND", "K"),
                                          "2": ("/LED_A", "A")}),
    ("R7", "10k", "R_0402_1005Metric", {"1": ("+3V3", ""), "2": ("/EN", "")}),
    ("C1", "1uF", "C_0402_1005Metric", {"1": ("/EN", ""), "2": ("GND", "")}),
    ("U3", "PHY", "QFN-24", {"1": ("+3V3", "VDD"), "2": ("GND", "GND"),
                             "3": ("/NRST", "NRST")}),
    ("R8", "10k", "R_0402_1005Metric", {"1": ("+3V3", ""), "2": ("/NRST", "")}),
    ("R9", "10k", "R_0402_1005Metric", {"1": ("/NRST", ""), "2": ("GND", "")}),
    ("C2", "100nF", "C_0402_1005Metric", {"1": ("/NRST", ""), "2": ("GND", "")}),
    ("C3", "10uF 10V", "C_0805_2012Metric", {"1": ("+9V", ""), "2": ("GND", "")}),
    ("Q1", "AO3401", "SOT-23", {"1": ("/GATE", "G"), "2": ("+20V", "S"),
                                "3": ("/LOAD", "D")}),
    ("R10", "100k", "R_0402_1005Metric", {"1": ("+20V", ""), "2": ("/GATE", "")}),
    ("R11", "1k", "R_0402_1005Metric", {"1": ("/GATE", ""),
                                        "2": ("/GATE_PULL", "")}),
    ("R12", "1k", "R_0402_1005Metric", {"1": ("/LOAD", ""), "2": ("GND", "")}),
    ("L1", "10uH", "L_1210", {"1": ("+3V3", ""), "2": ("+3V3A", "")}),
    ("R13", "330", "R_0402_1005Metric", {"1": ("+3V3A", ""), "2": ("GND", "")}),
    ("R14", "10M", "R_0402_1005Metric", {"1": ("+60V", ""), "2": ("GND", "")}),
]


def write_net(path: Path, board=BOARD) -> Path:
    comps, nets = [], {}
    for ref, value, fp, pins in board:
        comps.append(f'(comp (ref "{ref}") (value "{value}") '
                     f'(footprint "{fp}"))')
        for pin, (net, fn) in pins.items():
            nets.setdefault(net, []).append(
                f'(node (ref "{ref}") (pin "{pin}") (pinfunction "{fn}") '
                f'(pintype "passive"))')
    body = "\n".join(f'(net (code "{i}") (name "{n}") {" ".join(nd)})'
                     for i, (n, nd) in enumerate(sorted(nets.items()), 1))
    path.write_text(f'(export (version "E") (components {" ".join(comps)}) '
                    f'(nets {body}))\n', encoding="utf-8")
    return path


# ---------------------------------------------------------------- engcalc

@pytest.mark.parametrize("text,want", [
    ("4k7", 4700), ("2.2k", 2200), ("100R", 100), ("0R", 0), ("2R2", 2.2),
    ("1M", 1e6), ("100nF", 1e-7), ("2.2u", 2.2e-6), ("10uH 8A", 1e-5),
    ("470uF/50V", 4.7e-4), ("22pF", 22e-12), ("49.9R", 49.9), ("1meg", 1e6),
    ("red", None), ("", None), (None, None), ("C_*", None)])
def test_parse_value(text, want):
    got = engcalc.parse_value(text)
    assert got == pytest.approx(want) if want is not None else got is None


def test_parse_ratings():
    assert engcalc.parse_volts("10uF 25V") == 25
    assert engcalc.parse_volts("470uF/50V") == 50
    assert engcalc.parse_watts("100mW") == pytest.approx(0.1)
    assert engcalc.parse_watts("1/4W") == pytest.approx(0.25)
    assert engcalc.parse_tolerance("±1%") == pytest.approx(0.01)


@pytest.mark.parametrize("fp,code", [
    ("Resistor_SMD:R_0603_1608Metric", "0603"), ("aiee:R0402", "0402"),
    ("x:C_1005Metric", "0402"), ("aiee:SOT-23-6", None), ("", None)])
def test_package_code(fp, code):
    assert engcalc.package_code(fp) == code


def test_worked_examples():
    # SY8089 0.6 V, 150k/33k -> 3.327 V (the Lite board's +3V3)
    fb = engcalc.fb_vout(0.6, 150e3, 33e3, 0.01, 0.02)
    assert fb["nom"] == pytest.approx(3.3273, abs=1e-4)
    assert fb["min"] < fb["nom"] < fb["max"]
    # 10k / 1u to 70 %: 1.204 tau
    assert engcalc.rc_cross_s(0.01, 0.7) == pytest.approx(0.01204, abs=1e-5)
    assert engcalc.rc_corner_hz(10e3, 1e-6) == pytest.approx(15.915, abs=1e-3)
    i2c = engcalc.i2c_pullup(3.3, 2200, 50, 400)
    assert i2c["rmin"] == pytest.approx(966.7, abs=0.1)
    assert i2c["tr_ns"] == pytest.approx(93.2, abs=0.1)
    assert i2c["ok"]
    assert not engcalc.i2c_pullup(3.3, 470, 50, 400)["ok"]
    assert engcalc.crystal_load_pf(18, 18) == pytest.approx(13.0)
    assert engcalc.ldo_tj(5, 3.3, 0.3, 180, 40)["tj_c"] == pytest.approx(131.8)
    assert engcalc.holdup_s(470e-6, 20, 12, 0.5) == pytest.approx(7.52e-3)
    # D/CRCW e3: 0603 Umax 75 V, critical R = 75^2 / 0.1 = 56.25 kOhm
    assert engcalc.resistor_umax_v("R_0603_1608Metric") == 75
    assert engcalc.resistor_umax_v("R_2512_6332Metric") == 500
    assert 75 ** 2 / engcalc.resistor_rating_w("R_0603") == pytest.approx(
        56250)
    # thick film: flat to 70 C, 0 at 155 C; 85 C -> 0.82 (R-RAT-2)
    assert engcalc.resistor_derate(25) == engcalc.resistor_derate(70) == 1
    assert engcalc.resistor_derate(85) == pytest.approx(0.8235, abs=1e-4)
    assert engcalc.resistor_derate(160) == 0
    # Littelfuse 881 at 70 C: k 0.90, so 1.5 A needs 1.5/(0.75*0.9) = 2.22 A
    assert engcalc.fuse_rerate(70) == pytest.approx(0.90)
    assert engcalc.fuse_rerate(0) == 1.0
    assert 1.5 / (engcalc.FUSE_LOAD_FRACTION * engcalc.fuse_rerate(70)) \
        == pytest.approx(2.222, abs=1e-3)
    # a 1.1 A (23 C) PPTC holds about 0.75 A at 70 C
    assert 1.1 * engcalc.pptc_hold_factor(70) == pytest.approx(0.748)
    assert engcalc.pptc_hold_factor(23) == 1.0
    assert engcalc.pptc_hold_factor(-40) > 1.4


@pytest.mark.parametrize("text,want", [
    ("-40℃~+85℃", (-40, 85, False)), ("-55℃~+155℃", (-55, 155, False)),
    ("-40℃~+125℃@(Tj)", (-40, 125, True)),
    ("-40℃~+125℃@(TA/TJ)", (-40, 125, False)),
    ("-40°C ~ 125°C", (-40, 125, False)), ("0 to 70 C", (0, 70, False)),
    ("25V", None), ("", None), (None, None)])
def test_parse_temp_range(text, want):
    assert engcalc.parse_temp_range(text) == want


def test_parse_amps():
    assert engcalc.parse_amps("750 mA") == pytest.approx(0.75)
    assert engcalc.parse_amps("1.1A") == pytest.approx(1.1)
    assert engcalc.parse_amps("6A 32V") == 6
    assert engcalc.parse_amps("32V") is None
    assert engcalc.led_color("KT-0603", "Red") == "red"
    assert engcalc.led_color("yellow-green") == "yellow-green"


# ---------------------------------------------------- board model (no engine)

def test_board_model(tmp_path):
    net = write_net(tmp_path / "b.net")
    board = sim_board.load_board(net)
    rails = sim_board.resolve_rails(board, {}, None, None, {})
    assert rails["+5V"][0] == 5.0 and rails["+3V3"] == (3.3, "net name")
    model = sim_board.build_model(board, {}, rails)
    kinds = {e["ref"]: e["kind"] for e in model["elems"]}
    assert kinds["D1"] == "D" and kinds["Q1"] == "Q" and kinds["C1"] == "C"
    d1 = next(e for e in model["elems"] if e["ref"] == "D1")
    assert d1["type"] == "led" and d1["a"] == "/LED_A"
    q1 = next(e for e in model["elems"] if e["ref"] == "Q1")
    assert q1["type"] == "pmos" and q1["roles"]["G"] == "/GATE"
    assert {u["ref"] for u in model["unmodelled"]} == {"U1", "U2", "U3"}
    io, od = sim_board.io_nets(board, rails, model)
    assert io == {"/SDA": 3.3, "/SCL": 3.3, "/LED": 3.3, "/GATE_PULL": 3.3}
    assert "/EN" not in io and not od
    # +3V3 and +3V3A share a DC node through L1: one source only
    rails["+3V3A"] = (3.2, "net name")
    srcs = sim_board.rail_sources(rails, model)
    assert srcs["+3V3A"] == "+3V3"
    deck = sim_board.circuit(model, rails, "op", io)
    assert "VR_P3V3 P3V3 0 DC 3.3" in deck and "VR_P3V3A" not in deck
    assert "RIO_N_LED IO_N_LED N_LED 25" in deck and deck.endswith(".end\n")


# A dual NPN (SOT-363, E1 B1 C2 E2 B2 C1): unit A switches /LOAD_A from
# /DRV_A, unit B /LOAD_B from /DRV_B. Each unit's role spans two nets.
DUAL_PINS = {"1": "GND", "2": "/DRV_A", "6": "/LOAD_A",
             "4": "GND", "5": "/DRV_B", "3": "/LOAD_B"}
DUAL_FN = {"1": "E", "2": "B", "6": "C", "4": "E", "5": "B", "3": "C"}


@pytest.mark.parametrize("value,fn", [
    ("MMDT3904", DUAL_FN),                                  # E/B/C repeated
    ("BC847BS", {"1": "E1", "2": "B1", "6": "C1",           # numbered units
                 "4": "E2", "5": "B2", "3": "C2"}),
    ("MBT3904DW", {k: "" for k in DUAL_PINS}),               # blank: by MPN
])
def test_dual_bjt_is_two_units(tmp_path, value, fn):
    board = [("Q1", value, "SOT-363",
              {pin: (net, fn[pin]) for pin, net in DUAL_PINS.items()}),
             ("R1", "1k", "R_0402", {"1": ("+5V", ""), "2": ("/LOAD_A", "")}),
             ("R2", "1k", "R_0402", {"1": ("+5V", ""), "2": ("/LOAD_B", "")})]
    b = sim_board.load_board(write_net(tmp_path / "d.net", board))
    model = sim_board.build_model(b, {}, {})
    assert model["unmodelled"] == []
    q = {e["ref"]: e for e in model["elems"] if e["kind"] == "Q"}
    assert set(q) == {"Q1A", "Q1B"}
    assert q["Q1A"]["roles"] == {"E": "GND", "B": "/DRV_A", "C": "/LOAD_A"}
    assert q["Q1B"]["roles"] == {"E": "GND", "B": "/DRV_B", "C": "/LOAD_B"}
    assert all(e["type"] == "npn" and e["comp"] == "Q1" for e in q.values())
    assert q["Q1A"]["dc"].startswith("QQ1A N_LOAD_A N_DRV_A 0 Q_Q1A")


def test_dual_unknown_or_complementary_stays_open(tmp_path):
    for value, fn in (("XYZ123", {k: "" for k in DUAL_PINS}),
                      ("BC847BPN", DUAL_FN)):
        board = [("Q1", value, "SOT-363",
                  {pin: (net, fn[pin]) for pin, net in DUAL_PINS.items()})]
        b = sim_board.load_board(write_net(tmp_path / "d.net", board))
        model = sim_board.build_model(b, {}, {})
        assert [u["ref"] for u in model["unmodelled"]] == ["Q1"]


def test_dual_nfet_units(tmp_path):
    pins = {"1": ("GND", "S"), "2": ("/G1", "G"), "6": ("/D1", "D"),
            "4": ("GND", "S"), "5": ("/G2", "G"), "3": ("/D2", "D")}
    b = sim_board.load_board(write_net(tmp_path / "f.net", [
        ("Q2", "2N7002DW", "SOT-363", pins)]))
    q = {e["ref"]: e for e in sim_board.build_model(b, {}, {})["elems"]}
    assert q["Q2A"]["roles"] == {"S": "GND", "G": "/G1", "D": "/D1"}
    assert q["Q2B"]["type"] == "nmos" and q["Q2B"]["roles"]["D"] == "/D2"


def test_norm_fn():
    assert sim_board.norm_fn("EN_3", "3") == ["EN"]
    assert sim_board.norm_fn("~{RESET}", "1") == ["RESET"]
    assert sim_board.norm_fn("CFG2/SCL_2", "2") == ["CFG2", "SCL"]


def test_vref_table_entries_are_well_formed():
    rows = sim_board.load_vrefs()
    assert rows
    for r in rows:
        assert 0.1 < r["vref"] < 2.0 and 0 < r.get("tol", 0.02) < 0.1
        assert r.get("source") and "verified" in r


# ------------------------------------------------------------------ engine

@pytest.fixture(scope="module")
def report(tmp_path_factory):
    if env.find_ngspice_dll() is None:
        pytest.skip("no ngspice shared library (KiCad bundle or "
                    "HWDE_NGSPICE_DLL)")
    net = write_net(tmp_path_factory.mktemp("sb") / "b.net")
    out = net.with_suffix(".json")
    cp = subprocess.run([sys.executable, str(SCRIPTS / "sim_board.py"),
                         "--net", str(net), "--out", str(out)],
                        capture_output=True, text=True, timeout=300)
    assert cp.returncode == 1, cp.stdout + cp.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def _kinds(rep, kind):
    return [v for v in rep["violations"] if v["kind"] == kind]


def test_engine_resistor_overpower(report):
    (v,) = [v for v in _kinds(report, "resistor_overpower")
            if v["refs"] == ["R3"]]
    assert v["severity"] == "error" and v["power_w"] == pytest.approx(0.25,
                                                                       rel=1e-3)


def test_engine_resistor_overvoltage(report):
    # 60 V across an 0402 (Umax 50 V): 0.36 mW passes the power check
    (v,) = _kinds(report, "resistor_overvoltage")
    assert v["refs"] == ["R14"] and v["severity"] == "error"
    assert v["v_across"] == pytest.approx(60) and v["umax_v"] == 50
    assert not any(x["refs"] == ["R14"]
                   for x in _kinds(report, "resistor_overpower"))


def test_engine_resistor_power_derated_at_ambient(tmp_path):
    if env.find_ngspice_dll() is None:
        pytest.skip("no ngspice shared library")
    # an 0603 1.5k across +10V: 67 mW, a warning at 25 C (over half of
    # 0.1 W), over its rating at 100 C, where it holds 0.1 x 55/85 = 65 mW
    net = write_net(tmp_path / "d.net", [
        ("R1", "1.5k", "R_0603_1608Metric", {"1": ("+10V", ""),
                                             "2": ("GND", "")})])
    got = {}
    for amb in (None, 100):
        cons = tmp_path / f"c{amb}.json"
        cons.write_text(json.dumps({"ambient_c": amb} if amb else {}))
        out = tmp_path / f"r{amb}.json"
        subprocess.run([sys.executable, str(SCRIPTS / "sim_board.py"),
                        "--net", str(net), "--constraints", str(cons),
                        "--out", str(out)], capture_output=True, text=True,
                       timeout=300)
        rep = json.loads(out.read_text(encoding="utf-8"))
        (got[amb],) = _kinds(rep, "resistor_overpower")
    assert got[None]["severity"] == "warning"
    assert got[100]["severity"] == "error"
    assert got[100]["rating_w"] == pytest.approx(0.1 * 55 / 85)
    assert "derated" in got[100]["msg"]


def test_engine_fb_divider(report):
    (v,) = _kinds(report, "fb_divider_mismatch")
    # 0.596 * (1 + 100k/15k) = 4.57 V against +5V: -8.6 %
    assert v["severity"] == "error" and v["refs"] == ["R1", "R2", "U1"]
    assert v["vout"]["nom"] == pytest.approx(4.569, abs=1e-3)
    assert any(u["ref"] == "U1" and "Vref" in u["what"]
               for u in report["known_unknowns"])


def test_engine_i2c(report):
    (v,) = _kinds(report, "i2c_pullup_strong")
    assert v["refs"] == ["R4"]
    scl = next(r for r in report["i2c"] if r["nets"] == ["/SCL"])
    assert scl["ok"] and scl["cb_assumed"]


def test_engine_led_and_gpio(report):
    led = report["leds"][0]
    assert led["current_a"]["default"] < 1e-6
    i = led["current_a"]["io_high"]
    assert 0.02 < i < 0.04   # (3.3 - ~1.9) / (22 + 25 + RS)
    assert [v["refs"] for v in _kinds(report, "led_overcurrent")] == [["D1"]]
    assert any(v["net"] == "/LED" for v in _kinds(report, "gpio_overcurrent"))


def test_engine_cap_dc_bias_and_mosfet(report):
    assert _kinds(report, "cap_dc_bias") == []      # no dielectric known
    (q,) = report["mosfets"]
    # GATE_PULL low through 25 + 1k against 100k to +20V: Vgs ~ -19.8 V
    assert q["vgs"]["io_low"] == pytest.approx(-19.8, abs=0.3)
    (v,) = _kinds(report, "mosfet_vgs")
    assert v["severity"] == "warning" and v["scenario"] == "io_low"


def test_engine_enables(report):
    en = {r["net"]: r for r in report["enables"]}
    assert en["/EN"]["t_cross_s"] == pytest.approx(0.012, rel=0.1)
    assert en["/NRST"]["final_v"] == pytest.approx(1.65, abs=0.01)
    (v,) = _kinds(report, "enable_never_high")
    assert v["net"] == "/NRST"


def test_engine_linked_rails_do_not_fight(report):
    assert abs(report["rail_currents_a"]["+3V3"]) < 0.05
    assert "+3V3A" not in report["rail_currents_a"]


# A PD-fed P-FET load switch: 33k VBUS->gate, 100k gate->GPIO. Driven on
# (GPIO low) its Vsg is VBUS x 33/133: 5.2 V at the 21 V nominal, 2.2 V at
# the 9 V lowest contract - under the 2.5 V Vgs(th) max.
PD_BOARD = [
    ("U1", "MCU", "QFN-32", {"1": ("+3V3", "VDD"), "2": ("GND", "GND"),
                             "3": ("/PVDD_EN", "IO3")}),
    ("Q1", "AO4485", "SOIC-8", {"1": ("/GATE", "G"), "2": ("VBUS", "S"),
                                "3": ("PVDD", "D")}),
    ("R1", "33k", "R_0402_1005Metric", {"1": ("VBUS", ""), "2": ("/GATE", "")}),
    ("R2", "100k", "R_0402_1005Metric", {"1": ("/GATE", ""),
                                         "2": ("/PVDD_EN", "")}),
    ("R3", "1k", "R_0603_1608Metric", {"1": ("PVDD", ""), "2": ("GND", "")}),
]


def _pd_report(tmp_path, attrs, min_v=9.0):
    (tmp_path / "kicad").mkdir()
    write_net(tmp_path / "kicad" / "pd.net", PD_BOARD)
    volts = {"net": "VBUS", "voltage": 21}
    if min_v is not None:
        volts["min"] = min_v
    (tmp_path / "kicad" / "constraints.json").write_text(json.dumps(
        {"voltages": [volts]}), encoding="utf-8")
    (tmp_path / "parts").mkdir()
    (tmp_path / "parts" / "parts.json").write_text(json.dumps({"parts": [
        {"mpn": "AO4485", "refdes": ["Q1"], "attributes": [
            {"name": k, "value": v} for k, v in attrs.items()]}]}),
        encoding="utf-8")
    out = tmp_path / "sim.json"
    subprocess.run([sys.executable, str(SCRIPTS / "sim_board.py"),
                    "--workspace", str(tmp_path), "--out", str(out)],
                   capture_output=True, text=True, timeout=300)
    return json.loads(out.read_text(encoding="utf-8"))


RDSON = {"RDS(on)": "12.5mΩ@10V、16mΩ@4.5V"}


def test_fet_drive_attrs():
    part = {"attrs": {"Gate Threshold Voltage (Vgs(th))": "-1~-2.5V",
                      **RDSON}}
    assert sim_board.fet_vth_max(part)[0] == 2.5
    assert sim_board.fet_rdson_vgs(part) == 4.5
    part["attrs"]["Vgs(th) max"] = "3V"
    assert sim_board.fet_vth_max(part) == (3.0, "parts.json max")
    assert sim_board.fet_vth_max(None) == (None, "")


def test_rail_mins():
    rails = {"VBUS": (21.0, "constraints"), "+3V3": (3.3, "net name")}
    cons = {"voltages": [{"net": "VBUS", "voltage": 21, "min": 9},
                         {"net": "+3V3", "voltage": 3.3, "min": 3.3}]}
    assert sim_board.rail_mins(rails, cons, {}) == {"VBUS": 9.0}
    assert sim_board.rail_mins(rails, None, {"+3V3": 3.0}) == {"+3V3": 3.0}
    with pytest.raises(sim_board.checklib.CheckError):
        sim_board.rail_mins(rails, None, {"PVDD": 5.0})


@pytest.fixture
def need_engine():
    if env.find_ngspice_dll() is None:
        pytest.skip("no ngspice shared library (KiCad bundle or "
                    "HWDE_NGSPICE_DLL)")


def test_engine_underdriven_pvdd_switch(tmp_path, need_engine):
    rep = _pd_report(tmp_path, {"Vgs(th) max": "-2.5V", **RDSON})
    (q,) = rep["mosfets"]
    assert q["on_vgs"]["io_low"] == pytest.approx(5.21, abs=0.05)
    assert q["on_vgs"]["io_low@min"] == pytest.approx(2.23, abs=0.05)
    (v,) = _kinds(rep, "mosfet_underdrive")
    assert v["severity"] == "error" and v["scenario"] == "io_low@min"
    assert v["refs"] == ["Q1"] and rep["status"] == "violations"
    assert rep["rail_min_v"] == {"VBUS": 9.0}


def test_engine_underdrive_rdson_and_nominal(tmp_path, need_engine):
    # only LCSC's bare Threshold figure (1.5 V): 2.2 V clears it but not
    # the 4.5 V RDS(on) point - a warning
    rep = _pd_report(tmp_path, {"Gate Threshold Voltage (Vgs(th))": "1.5V",
                                **RDSON})
    (v,) = _kinds(rep, "mosfet_underdrive")
    assert v["severity"] == "warning" and v["scenario"] == "io_low@min"


def test_engine_no_underdrive_without_min(tmp_path, need_engine):
    rep = _pd_report(tmp_path, {"Vgs(th) max": "2.5V", **RDSON}, min_v=None)
    assert _kinds(rep, "mosfet_underdrive") == []
    assert "rail_min_v" not in rep


def test_fb_divider_series_bottom_chain(tmp_path):
    """A bottom of two resistors in series (FB -> R2 -> mid -> R3 -> GND)
    reads as one bottom of R2 + R3, not '1 top / 0 bottom'."""
    board = [
        ("U4", "TPS54302", "SOT-23-6", {"1": ("GND", "GND"),
                                        "3": ("+12V", "VIN"),
                                        "4": ("/B1V375_FB", "FB")}),
        ("R1", "100k", "R_0402_1005Metric", {"1": ("+5V", ""),
                                             "2": ("/B1V375_FB", "")}),
        ("R2", "10k", "R_0402_1005Metric", {"1": ("/B1V375_FB", ""),
                                            "2": ("/MID", "")}),
        ("R3", "5k", "R_0402_1005Metric", {"1": ("/MID", ""),
                                           "2": ("GND", "")}),
    ]
    b = sim_board.load_board(write_net(tmp_path / "b.net", board))
    rails = sim_board.resolve_rails(b, {}, None, None, {})
    model = sim_board.build_model(b, {}, rails)
    vios, unknowns, facts = [], [], {}
    sim_board.regulator_findings(b, {}, rails, model, {"vref": {}}, vios,
                                 unknowns, facts)
    assert not any("plain divider" in u["why"] for u in unknowns)
    (row,) = facts["regulators_fb"]
    assert row["r_top"] == "R1" and row["r_bot"] == "R2+R3"
    assert row["out_net"] == "+5V"
    assert row["ratio"] == pytest.approx(1 + 100 / 15)
    (v,) = vios
    assert v["refs"] == ["R1", "R2", "R3", "U4"]


# ------------------------------------- SoC pads, pin modes, pin ratings

# An i.MX-style SoC whose pads carry their default function: ENET2_TX_EN
# (muxed to a GPIO) drives a status LED; DRAM/USB pads and an ADC-named net
# stay undriven. An MCU's PB11 sits on a peak detector (an ADC input the
# board's pin_modes names). An INA240 on VS = 3.3 V measures a 12 V rail;
# a gate driver's HB rides above its VDD; a 2 R + 2.2 uF snubber's mid node
# on a sheet is named after VBUS but is no rail.
SOC_BOARD = [
    ("U5", "MCIMX6Y2CVM08AB", "BGA-289", {
        "N13": ("+3V3", "VDD_HIGH_IN"), "A1": ("GND", "GND"),
        "B15": ("/LED_STATUS", "ENET2_TX_EN_B15"),
        "F17": ("/U4TX", "UART4_TX_DATA_F17"),
        "G6": ("/DQ0", "DRAM_DATA00_G6"),
        "U14": ("/USB_DP", "USB_OTG1_DP_U14"),
        "L17": ("/ADC_1V35", "GPIO1_IO03_L17")}),
    ("R20", "470", "R_0402_1005Metric", {"1": ("/LED_STATUS", ""),
                                         "2": ("/LED_K", "")}),
    ("D20", "red", "LED_0603_1608Metric", {"1": ("GND", "K"),
                                           "2": ("/LED_K", "A")}),
    ("R21", "10k", "R_0402_1005Metric", {"1": ("/U4TX", ""),
                                         "2": ("GND", "")}),
    ("R22", "240", "R_0402_1005Metric", {"1": ("/DQ0", ""),
                                         "2": ("GND", "")}),
    ("R23", "10k", "R_0402_1005Metric", {"1": ("+1V35", ""),
                                         "2": ("/ADC_1V35", "")}),
    ("R24", "10k", "R_0402_1005Metric", {"1": ("/ADC_1V35", ""),
                                         "2": ("GND", "")}),
    ("U6", "STM32G474RBT3", "LQFP-64", {"1": ("+3V3", "VDD"),
                                        "2": ("GND", "VSS"),
                                        "30": ("/SWPK_A", "PB11")}),
    ("C20", "100pF", "C_0402_1005Metric", {"1": ("/SWPK_A", ""),
                                           "2": ("GND", "")}),
    ("R25", "10", "R_0402_1005Metric", {"1": ("/SWPK_A", ""),
                                        "2": ("GND", "")}),
    ("U7", "INA240A2DR", "SOIC-8", {"5": ("+3V3", "VS"), "2": ("GND", "GND"),
                                    "8": ("/SHUNT_P", "IN+"),
                                    "1": ("/SHUNT_N", "IN–")}),
    ("R26", "0.01", "R_2512_6332Metric", {"1": ("+12V", ""),
                                          "2": ("/SHUNT_P", "")}),
    ("R27", "0.01", "R_2512_6332Metric", {"1": ("/SHUNT_P", ""),
                                          "2": ("/SHUNT_N", "")}),
    ("R28", "10k", "R_0603_1608Metric", {"1": ("/SHUNT_N", ""),
                                         "2": ("GND", "")}),
    ("U8", "UCC27211", "SOIC-8", {"1": ("+3V3", "VDD"), "7": ("GND", "VSS"),
                                  "2": ("/HB", "HB")}),
    ("R29", "1k", "R_0402_1005Metric", {"1": ("+12V", ""), "2": ("/HB", "")}),
    ("R30", "2", "R_1206_3216Metric", {"1": ("+12V", ""),
                                       "2": ("/charger/CHG_VBUS_RC", "")}),
    ("C21", "2.2uF", "C_0805_2012Metric", {"1": ("/charger/CHG_VBUS_RC", ""),
                                           "2": ("GND", "")}),
]


def test_soc_pads_and_pin_modes(tmp_path):
    b = sim_board.load_board(write_net(tmp_path / "s.net", SOC_BOARD))
    rails = sim_board.resolve_rails(b, {}, None, None, {})
    assert "/charger/CHG_VBUS_RC" not in rails and "+12V" in rails
    model = sim_board.build_model(b, {}, rails)
    skipped = []
    io, od = sim_board.io_nets(b, rails, model, None, skipped)
    assert {"/LED_STATUS", "/U4TX", "/SWPK_A"} <= set(io)
    assert not {"/DQ0", "/USB_DP", "/ADC_1V35"} & set(io)
    assert skipped == [{"ref": "U5", "pin": "L17", "net": "/ADC_1V35",
                        "why": "analog net name"}]
    modes = sim_board.pin_modes({"pin_modes": {"U6": {"PB11": "analog"}}},
                                ["U5.ADC_1V35=io", "U5.F17=nc"])
    assert modes == {"U6": {"PB11": "off"}, "U5": {"ADC_1V35": "io",
                                                   "F17": "off"}}
    modes["U5"]["GPIO1_IO03"] = "io"
    io, _ = sim_board.io_nets(b, rails, model, modes)
    assert "/SWPK_A" not in io and "/U4TX" not in io and "/ADC_1V35" in io
    for bad in (["U5.F17"], ["U5=io"], ["U5.F17=sideways"]):
        with pytest.raises(sim_board.checklib.CheckError):
            sim_board.pin_modes(None, bad)
    for fn in ("ENET2_TX_EN", "UART4_TX_DATA", "GPIO1_IO03", "LCD_DATA05",
               "SD1_CMD", "GPIO0_A4_D"):
        assert sim_board.SOC_PAD_FN.match(fn), fn
    for fn in ("DRAM_DATA00", "USB_OTG1_DP", "SDA", "CAN_H", "VDD_SOC_IN"):
        assert not (sim_board.SOC_PAD_FN.match(fn)
                    and not sim_board.SOC_NOT_IO.match(fn)), fn


def test_pin_rating():
    table = sim_board.load_pin_ratings()
    c = {"ref": "U7", "value": "INA240A2DR"}
    lim, src = sim_board.pin_rating(c, {"fn": ["IN–"]}, None, table)
    assert lim == 80 and "SBOS662" in src
    bq = {"ref": "U301", "value": "x"}
    part = {"mpn": "BQ76952PFBR", "attrs": {}}
    assert sim_board.pin_rating(bq, {"fn": ["PDSG"]}, part, table)[0] == 85
    assert sim_board.pin_rating(bq, {"fn": ["REGIN"]}, part, table) == \
        (None, "")
    csa = {"ref": "U9", "value": "XYZ123"}
    part = {"mpn": "XYZ123", "attrs": {"Common Mode Voltage": "-4V~75V"}}
    assert sim_board.pin_rating(csa, {"fn": ["IN+"]}, part, table)[0] == 75
    assert sim_board.pin_rating(csa, {"fn": ["HB"]}, None, table)[0] == \
        "switch_node"


def test_underdrive_at_rdson_vgs_is_at_spec():
    e = {"ref": "Q501", "type": "nmos", "roles": {"G": "/G"}}
    part = {"attrs": {"Vgs(th) max": "1V", "RDS(on)": "20mΩ@2.5V"}}
    vios, unknowns, row = [], [], {}
    sim_board.underdrive(e, part, {"io_high": 2.497}, row, vios, unknowns)
    assert vios == []
    sim_board.underdrive(e, part, {"io_high": 2.3}, row, vios, unknowns)
    assert [v["kind"] for v in vios] == ["mosfet_underdrive"]


def test_engine_soc_board(tmp_path, need_engine):
    (tmp_path / "kicad").mkdir()
    write_net(tmp_path / "kicad" / "s.net", SOC_BOARD)
    (tmp_path / "kicad" / "constraints.json").write_text(json.dumps(
        {"pin_modes": {"U6": {"PB11": "analog"}}}), encoding="utf-8")
    out = tmp_path / "sim.json"
    subprocess.run([sys.executable, str(SCRIPTS / "sim_board.py"),
                    "--workspace", str(tmp_path), "--out", str(out)],
                   capture_output=True, text=True, timeout=300)
    rep = json.loads(out.read_text(encoding="utf-8"))
    (led,) = rep["leds"]
    assert led["current_a"]["io_high"] == pytest.approx(3e-3, rel=0.25)
    assert _kinds(rep, "led_dark") == []
    assert _kinds(rep, "gpio_overcurrent") == []      # PB11 not driven
    assert _kinds(rep, "input_above_supply") == []    # INA240, HB
    assert _kinds(rep, "resistor_overpower") == []    # no 12 V on R30's end
    assert any(u["what"] == "/charger/CHG_VBUS_RC not a rail"
               for u in rep["known_unknowns"])
    assert any(u["ref"] == "U8" and u["what"].startswith("pin 2 (HB)")
               for u in rep["known_unknowns"])
