"""lib/netdir: which end of a net drives it (#605)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))
import check_si  # noqa: E402
import geom  # noqa: E402
import impedance  # noqa: E402
import netdir  # noqa: E402
import sim_si  # noqa: E402
import solvers  # noqa: E402
from test_check_si import fp, seg  # noqa: E402
from test_sim_si import write_board  # noqa: E402

need_ngspice = pytest.mark.skipif(solvers.tool_path("ngspice") is None, reason="no ngspice")
need_fields = pytest.mark.skipif(not impedance.SOLVER_OK, reason="no scipy")
LITE = REPO / "pinned" / "lite-main" / "kicad" / "lite-main.kicad_pcb"


def write_net(path, nets):
    """nets: {net: [(ref, pin, function)]}, as kicad-cli writes them."""
    body = ""
    for i, (net, nodes) in enumerate(nets.items(), 1):
        ns = "".join(f'(node (ref "{r}") (pin "{p}") (pinfunction "{f}_{p}") (pintype "passive"))'
                     for r, p, f in nodes)
        body += f'(net (code "{i}") (name "{net}") {ns})\n'
    path.write_text(f'(export (version "E") (nets {body}))\n', encoding="utf-8")


def line(a="U1", b="U2", net="/S", va="A", vb="B", extra=""):
    return (fp(a, 5, 10, va, [("1", 0, net)]) + fp(b, 95, 10, vb, [("1", 0, net)])
            + seg(5, 10, 95, 10, net) + extra)


def who(tmp_path, body, names=None, spec=None, si=None, net="/S", parts=None, power=frozenset()):
    """names: {(ref, pin): function} on /S."""
    p = write_board(tmp_path, body)
    if names:
        nodes = {}
        for (ref, pin), fn in names.items():
            nodes.setdefault(net, []).append((ref, pin, fn))
        write_net(p.with_suffix(".net"), nodes)
    bg = geom.load_board(p)
    pins = netdir.PinData(bg, si=si, parts_dir=parts)
    return netdir.driver_of(bg, net, pins, spec or {}, power)


@pytest.mark.parametrize("names,want", [
    (["SDOUT", "AD2"], "out"), (["DOUT"], "out"), (["I2S_DOUT"], "out"), (["SDO"], "out"),
    (["MCO1"], "out"), (["CLKOUT"], "out"), (["UART4_TX"], "out"), (["TXD0"], "out"),
    (["RXD"], "in"), (["DIN"], "in"), (["DSDIN"], "in"), (["SDI"], "in"), (["USART_RX"], "in"),
    (["IO26"], "bidir"), (["GPIO5"], "bidir"), (["PA9", "USART1_TX"], "bidir"),
    (["DQ3"], "bidir"), (["SDA"], "bidir"), (["SCLK"], "master"), (["LRCK"], "master"),
    (["MOSI"], "master"), (["MISO"], "slave"), (["TX_CLK"], None), (["VDD"], None),
    (["TXP"], None), ([], None), (["DIN", "DOUT"], "bidir"),
])
def test_what_a_pin_name_says(names, want):
    assert netdir.name_dir(names) == want


def test_fn_names_strip_the_pin_suffix():
    assert netdir.fn_names("SDOUT/AD2_3", "3") == ["SDOUT", "AD2"]
    assert netdir.fn_names("~{CS}_1", "1") == ["CS"]


def test_output_named_pin_resolves_to_its_pad(tmp_path):
    got = who(tmp_path, line(), {("U1", "1"): "IO5", ("U2", "1"): "I2S_DOUT"})
    assert got["pad"] == "U2.1" and got["source"] == "pin_name"


def test_rx_on_one_end_makes_the_other_the_driver(tmp_path):
    got = who(tmp_path, line(), {("U1", "1"): "IO5", ("U2", "1"): "UART_RX"})
    assert got["pad"] == "U1.1" and got["confidence"] == "medium"


@pytest.mark.parametrize("a,b", [("IO5", "IO6"), ("DQ0", "DQ0"), ("VDD", "NC")])
def test_two_way_or_silent_pins_stay_unknown(tmp_path, a, b):
    got = who(tmp_path, line(), {("U1", "1"): a, ("U2", "1"): b})
    assert got["pad"] is None and got["source"] == "driver_assumed"


def test_two_outputs_are_a_conflict_not_a_guess(tmp_path):
    got = who(tmp_path, line(), {("U1", "1"): "TXD0", ("U2", "1"): "TXD0"})
    assert got["pad"] is None and "conflict" in got["why"]


def test_declared_beats_inferred(tmp_path):
    names = {("U1", "1"): "IO5", ("U2", "1"): "DOUT"}
    got = who(tmp_path, line(), names, {"driver": "U1"})
    assert got["pad"] == "U1.1" and got["source"] == "declared"
    both = tmp_path / "both"
    both.mkdir()
    assert who(both, line(), names, {"driver": "both"})["pad"] is None


def test_declared_off_the_net_is_said(tmp_path):
    got = who(tmp_path, line(), None, {"driver": "U9"})
    assert got["pad"] is None and got["declared_not_on_net"] == "U9"


def test_part_data_beats_the_pin_name(tmp_path):
    """An RMII PHY's TXD is its input; the name alone reads as an output."""
    got = who(tmp_path, line(va="ESP32-WROVER-E", vb="LAN8720A"),
              {("U1", "1"): "IO19", ("U2", "1"): "TXD0"})
    assert got["pad"] == "U1.1" and got["source"] == "part_data"
    assert "unverified" in got["why"]


def test_extraction_beats_the_table_and_the_name(tmp_path):
    parts = tmp_path / "parts"
    parts.mkdir()
    (parts / "parts.json").write_text(json.dumps(
        {"parts": [{"refdes": ["U1"], "lcsc": "C1", "mpn": "X"}]}))
    (parts / "C1.json").write_text(json.dumps(
        {"mpn": "X", "pinout": [{"pin": "1", "name": "RX", "type": "output"}]}))
    got = who(tmp_path, line(), {("U1", "1"): "RX", ("U2", "1"): "IO1"}, parts=parts)
    assert got["pad"] == "U1.1" and got["source"] == "part_data"
    assert "C1.json" in got["why"]


def test_a_stale_netlist_is_not_evidence(tmp_path):
    p = write_board(tmp_path, line())
    write_net(p.with_suffix(".net"), {"/OTHER": [("U2", "1", "DOUT"), ("U1", "1", "IO5")]})
    bg = geom.load_board(p)
    assert netdir.driver_of(bg, "/S", netdir.PinData(bg))["pad"] is None


def test_codec_clock_goes_to_the_one_controller_with_low_confidence(tmp_path):
    got = who(tmp_path, line(va="ESP32-WROVER-E", vb="ES7243E"),
              {("U1", "1"): "IO27", ("U2", "1"): "SCLK"})
    assert got["pad"] == "U1.1" and got["source"] == "bus_master" and got["confidence"] == "low"


def test_declared_bus_master_overrules_the_guess(tmp_path):
    got = who(tmp_path, line(va="ESP32-WROVER-E", vb="ES7243E"),
              {("U1", "1"): "IO27", ("U2", "1"): "SCLK"},
              si={"buses": [{"master": "U2", "nets": ["/S*"]}]})
    assert got["pad"] == "U2.1" and got["confidence"] == "declared"


def test_miso_comes_from_the_slave_and_no_controller_means_no_guess(tmp_path):
    got = who(tmp_path, line(va="STM32F405", vb="W25Q64"),
              {("U1", "1"): "PA6", ("U2", "1"): "MISO"})
    assert got["pad"] == "U2.1"
    two = tmp_path / "two"
    two.mkdir()
    assert who(two, line(va="FOO", vb="BAR"), {("U1", "1"): "IO1", ("U2", "1"): "SCLK"})["pad"] is None


def test_oscillator_outranks_pin_names(tmp_path):
    body = (fp("Y1", 5, 10, "50MHz", [("1", 0, "/S"), ("2", 2, "+3V3")])
            + fp("U2", 95, 10, "B", [("1", 0, "/S")]) + seg(5, 10, 95, 10, "/S"))
    got = who(tmp_path, body, {("U2", "1"): "CLKOUT", ("Y1", "1"): "OUT"}, power={"+3V3"})
    assert got["pad"] == "Y1.1" and got["source"] == "oscillator"


def series_body(stub_long=False):
    """U1 -(/A)- R1 -(/S)- U2. Which net is the long one moves R1 end to end."""
    x = 80 if stub_long else 8
    return (fp("U1", 5, 10, "A", [("1", 0, "/A")])
            + fp("R1", x, 10, "33", [("1", 0, "/A"), ("2", 2, "/S")])
            + fp("U2", 95, 10, "B", [("1", 0, "/S")])
            + seg(5, 10, x, 10, "/A") + seg(x + 2, 10, 95, 10, "/S"))


def test_a_series_r_by_its_ic_reads_as_the_source(tmp_path):
    got = who(tmp_path, series_body())
    assert got["pad"] == "R1.2" and got["source"] == "series_r" and got["confidence"] == "low"


def test_a_series_r_by_the_receiver_does_not(tmp_path):
    assert who(tmp_path, series_body(stub_long=True))["pad"] is None


def test_the_pin_behind_a_series_r_counts(tmp_path):
    p = write_board(tmp_path, series_body(stub_long=True))
    write_net(p.with_suffix(".net"), {"/A": [("U1", "1", "MCO")], "/S": [("U2", "1", "IO3")]})
    bg = geom.load_board(p)
    got = netdir.driver_of(bg, "/S", netdir.PinData(bg))
    assert got["pad"] == "R1.2" and got["source"] == "pin_name"


def test_a_series_r_feeding_a_connector_says_nothing(tmp_path):
    """hifi core /LCD_MOSI: MCU -> long net -> R403 -> FFC."""
    body = (fp("U1", 5, 10, "A", [("1", 0, "/S")])
            + fp("R1", 80, 10, "33", [("1", 0, "/S"), ("2", 2, "/B")])
            + fp("J1", 95, 10, "FFC", [("1", 0, "/B")])
            + seg(5, 10, 80, 10, "/S") + seg(82, 10, 95, 10, "/B"))
    assert who(tmp_path, body)["pad"] is None


def test_a_mid_net_driver_starts_the_path(tmp_path):
    body = (fp("U1", 5, 10, "A", [("1", 0, "/S")]) + fp("U2", 35, 10, "B", [("1", 0, "/S")])
            + fp("U3", 95, 10, "C", [("1", 0, "/S")])
            + seg(5, 10, 35, 10, "/S") + seg(35, 10, 95, 10, "/S"))
    bg = geom.load_board(write_board(tmp_path, body))
    name = lambda pp: {f"{q.ref}.{q.number}" for q in pp}  # noqa: E731
    assert name(sim_si.terminal_pads(bg, "/S")) == {"U1.1", "U3.1"}
    assert name(sim_si.terminal_pads(bg, "/S", "U2.1")) == {"U2.1", "U3.1"}
    assert name(sim_si.terminal_pads(bg, "/S", "U3.1")) == {"U1.1", "U3.1"}
    assert name(sim_si.terminal_pads(bg, "/S", "U9.1")) == {"U1.1", "U3.1"}


# ------------------------------------------------------------ check_si's termination

def clock_board(r_at):
    """A 90 mm clock on the outer layer, its series R at r_at."""
    return (fp("U1", 5, 10, "A", [("1", 0, "/CLK")])
            + fp("R1", r_at, 13, "33", [("1", 0, "/CLK"), ("2", 2, "/CLK_R")])
            + fp("U2", 95, 10, "B", [("1", 0, "/CLK")])
            + seg(5, 10, r_at, 10, "/CLK") + seg(r_at, 10, 95, 10, "/CLK")
            + seg(r_at, 10, r_at, 13, "/CLK"))


def term_check(tmp_path, body, driver=None, names=None):
    p = write_board(tmp_path, body)
    if names:
        write_net(p.with_suffix(".net"), {"/CLK": names})
    c = tmp_path / "c.json"
    spec = {"class": "osc_clock", "t_rise_ns": 0.5, **({"driver": driver} if driver else {})}
    c.write_text(json.dumps({"si": {"nets": {"/CLK": spec}}}))
    return check_si.run(["--pcb", str(p), "--constraints", str(c), "--xtalk-sim", "0"])[0]


def kinds(rep):
    return {v["kind"] for v in rep["violations"]}


@need_fields
def test_series_r_at_the_far_end_is_flagged(tmp_path):
    """Planted: same board, same R, driver at the end away from it."""
    far = term_check(tmp_path, clock_board(90), names=[("U1", "1", "CLKOUT"), ("U2", "1", "CLKIN")])
    hit = [v for v in far["violations"] if v["kind"] == "si_series_far_end"]
    assert hit and hit[0]["driver"] == "U1.1" and hit[0]["far_from_driver_mm"] > 50


@need_fields
def test_series_r_at_the_driver_is_clean(tmp_path):
    near = term_check(tmp_path, clock_board(90), names=[("U1", "1", "CLKIN"), ("U2", "1", "CLKOUT")])
    assert "si_series_far_end" not in kinds(near)
    assert near["nets"][0]["termination"]["ref"] == "R1"


@need_fields
def test_unknown_driver_keeps_the_old_verdict(tmp_path):
    rep = term_check(tmp_path, clock_board(90))
    assert "si_series_far_end" not in kinds(rep)
    assert rep["drivers_assumed"] == ["/CLK"]


@need_fields
def test_unterminated_clock_names_the_driver(tmp_path):
    body = (fp("U1", 5, 10, "A", [("1", 0, "/CLK")]) + fp("U2", 95, 10, "B", [("1", 0, "/CLK")])
            + seg(5, 10, 95, 10, "/CLK"))
    rep = term_check(tmp_path, body, driver="U2")
    hit = [v for v in rep["violations"] if v["kind"] == "si_unterminated_clock"]
    assert hit and "at the driver U2.1" in hit[0]["msg"]
    assert rep["drivers_assumed"] == []


# ------------------------------------------------------------ lite-main, known answers

@pytest.mark.slow
def test_lite_main_direction_from_its_netlist():
    bg = geom.load_board(LITE)
    pins = netdir.PinData(bg)
    got = {n: netdir.driver_of(bg, n, pins)["pad"]
           for n in ("/I2S_BCLK", "/I2S_LRCK", "/I2S_MCLK", "/I2S_DOUT", "/I2S_DIN", "/USB_DP")}
    assert got == {"/I2S_BCLK": "U5.12", "/I2S_LRCK": "U5.10", "/I2S_MCLK": "U5.25",
                   "/I2S_DOUT": "U5.11", "/I2S_DIN": "U7.8", "/USB_DP": None}


@pytest.mark.slow
@need_ngspice
@need_fields
def test_lite_main_bclk_on_dout_is_the_one_master_figure(tmp_path):
    """331 mV with the pin names (both from U5), 581 mV with no direction."""
    bg = geom.load_board(LITE)
    lm = check_si.LineModel(bg)
    specs = {"/I2S_BCLK": {"t_rise_ns": 2.0}, "/I2S_DOUT": {"t_rise_ns": 2.0}}
    got = sim_si.simulate_pair(bg, lm, "/I2S_BCLK", "/I2S_DOUT", specs, {}, frozenset())
    assert got["noise_mv"] == pytest.approx(331, abs=3)
    assert got["driver_assumed"] == {"/I2S_BCLK": False, "/I2S_DOUT": False}
    assert len(got["orientations"]) == 1

    class NoNames(netdir.PinData):
        def names(self, pad):
            return []

        def part_dir(self, pad):
            return None, None
    blind = sim_si.simulate_pair(bg, lm, "/I2S_BCLK", "/I2S_DOUT", specs, {}, frozenset(),
                                 pins=NoNames(bg))
    assert blind["noise_mv"] == pytest.approx(581, abs=6)
    assert len(blind["orientations"]) == 4
