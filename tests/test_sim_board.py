"""sim_board.py (ngspice on a board's own netlist) and lib/engcalc.py.

A small synthetic board carries one planted fault per finding the script
makes: a 100 ohm 0603 across +5V (overpower), an FB divider set 10 % off
its rail, a 470 ohm I2C pull-up (too strong for 3 mA), an LED a GPIO drives
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
