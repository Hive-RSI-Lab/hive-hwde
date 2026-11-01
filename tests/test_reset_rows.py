"""STM32 and ATmega reset rows (#602): known answers from the manuals, and a
planted fault for each behaviour, on synthetic netlists."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "scripts" / "lib"))
sys.path.insert(0, str(REPO / "tests"))
import check_reset_drive  # noqa: E402
import check_straps  # noqa: E402
from test_antenna_straps import _by_kind, _netlist  # noqa: E402


def _row(family):
    return next(r for r in check_straps.load_kb()["mcus"]
                if r["family"] == family)


def _run(mod, pcb, tmp_path):
    return mod.run(["--pcb", str(pcb), "--netlist",
                    str(tmp_path / "s.net")])[0]


def _straps(pcb, tmp_path):
    return _run(check_straps, pcb, tmp_path)


def _drive(pcb, tmp_path):
    return _run(check_reset_drive, pcb, tmp_path)


G4 = {"NRST": ("7", "PG10-NRST"), "BOOT0": ("45", "PB8-BOOT0"),
      "PA0": ("8", "PA0"), "PA9": ("31", "PA9"), "PA10": ("32", "PA10"),
      "PA13": ("37", "PA13"), "PA14": ("38", "PA14"), "PA15": ("39", "PA15"),
      "PB3": ("40", "PB3"), "PB4": ("41", "PB4"), "PB6": ("43", "PB6")}


def _g4(tmp_path, comps=None, nets=None, value="STM32G431CBT6", boot="/BOOT0",
        pinmap=G4):
    """A G4 with BOOT0 on 10 k to ground, NRST and a 100 nF, on +3V3."""
    c = {"U1": value, "R1": "10k", "C1": "100n", **(comps or {})}
    n = {"+3V3": [("U1", "24", "VDD")],
         "GND": [("U1", "23", "VSS"), ("R1", "2", ""), ("C1", "2", "")],
         "/NRST": [("U1", pinmap["NRST"][0], pinmap["NRST"][1]),
                   ("C1", "1", "")],
         boot: [("U1", pinmap["BOOT0"][0], pinmap["BOOT0"][1])]}
    if boot == "/BOOT0":
        n[boot].append(("R1", "1", ""))
    for name, nodes in (nets or {}).items():
        n.setdefault(name, []).extend(nodes)
    return _netlist(tmp_path, c, n)


def _pin(name):
    return ("U1", G4[name][0], G4[name][1])


# ----------------------------------------------------------- known answers

def test_stm32_debug_pulls_match_the_gpio_reset_registers():
    """GPIOx_PUPDR reset values: 2 bits a pin, 01 up, 10 down. RM0440 9.4.4
    and RM0090 8.4.4: port A 0x6400 0000, port B 0x0000 0100. PB3 has none."""
    want = {}
    for port, reg in (("A", 0x64000000), ("B", 0x00000100)):
        for i in range(16):
            bits = (reg >> (2 * i)) & 3
            if bits:
                want[f"P{port}{i}"] = {1: "up", 2: "down"}[bits]
    assert want == {"PA13": "up", "PA14": "down", "PA15": "up", "PB4": "up"}
    for fam in ("STM32G4", "STM32F4", "STM32F1"):
        got = {k: v["pull"] for k, v in _row(fam)["reset_pulls"].items()}
        assert got == want, fam


def test_stm32_boot0_wants_low_and_has_no_pull_nrst_pulls_up():
    for fam, ohm in (("STM32G4", 40000), ("STM32F4", 40000),
                     ("STM32F1", 40000)):
        row = _row(fam)
        assert row["verified"] is True
        assert row["straps"]["BOOT0"]["want"] == "low"
        assert row["straps"]["BOOT0"]["pull"] == "none"
        assert row["straps"]["NRST"]["pull"] == "up"
        assert row["straps"]["NRST"]["ohm"] == ohm


def test_f407_boot0_thresholds_are_its_own():
    """DS8626 Table 48: VIL 0.1 VDD + 0.1, VIH 0.17 VDD + 0.7, at 3.3 V."""
    boot = _row("STM32F4")["straps"]["BOOT0"]
    assert abs(boot["vil"] * 3.3 - (0.1 * 3.3 + 0.1)) < 0.01
    assert abs(boot["vih"] * 3.3 - (0.17 * 3.3 + 0.7)) < 0.02


def test_g4_dead_battery_pairs_are_the_datasheets():
    """DS12589 pin table note 6: PA9 controls PB6, PA10 controls PB4, 5.1 k."""
    assert _row("STM32G4")["dead_battery"] == [
        {"cc": "PB6", "db": "PA9", "ohm": 5100},
        {"cc": "PB4", "db": "PA10", "ohm": 5100}]


def test_atmega_row_is_the_datasheets():
    """DS40002061B: RRST 30-60 k, VIL2 0.1, VIH2 0.9 VCC; MOSI PB3, SCK PB5."""
    row = _row("ATmega48/88/168/328")
    assert row["pull_ohm"] == 45000
    assert (row["straps"]["RESET"]["vil"], row["straps"]["RESET"]["vih"]) \
        == (0.1, 0.9)
    assert row["programming"]["pins"] == ["PB3", "PB5"]
    assert not row.get("reset_pulls")  # tri-stated by reset, pull-ups off


def test_rows_match_the_parts_and_no_others():
    kb = check_straps.load_kb()

    def fam(value):
        return next((r["family"] for r in kb["mcus"]
                     if r["_match"].search(value)), None)
    assert fam("STM32G431CBT6") == fam("STM32G474RBT3") == "STM32G4"
    assert fam("STM32F407VGT6") == "STM32F4"
    assert fam("STM32F103C8T6") == "STM32F1"
    for v in ("ATmega88A-AU", "ATMEGA88A-AU-ND", "ATmega48A", "ATmega328P-AU",
              "ATmega168PA"):
        assert fam(v) == "ATmega48/88/168/328", v
    for v in ("STM32G071", "STM32F030F4P6", "STM32L476", "ATmega328PB",
              "ATmega2560", "ATtiny85"):
        assert fam(v) is None, v
        assert check_straps.unknown_mcus({"U1": v}, kb) == ["U1"]


# ---------------------------------------------------------- pin name parsing

def test_pin_names_split_by_the_row(tmp_path):
    pcb = _netlist(tmp_path, {"U1": "STM32G431CBT6", "U2": "ATmega88A-AU"}, {
        "/A": [("U1", "45", "PB8-BOOT0"), ("U1", "7", "PG10-NRST"),
               ("U1", "37", "PA13(JTMS-SWDIO)"), ("U1", "3", "PC14-OSC32_IN"),
               ("U2", "29", "PC6_(RESET/PCINT14)"),
               ("U2", "15", "PB3_(PCINT3/OC2A/MOSI)"),
               ("U2", "1", "PD3_(PCINT19/OC2B/INT1)")]})
    from simlib import parse_netlist
    b = check_straps.Board(parse_netlist(tmp_path / "s.net"))
    kb = check_straps.load_kb()
    (u1, u2) = check_straps.find_mcus(b, kb)
    p1, p2 = check_straps.mcu_pins(b, u1), check_straps.mcu_pins(b, u2)
    assert p1["BOOT0"] == "45" and "PB8" not in p1
    assert p1["NRST"] == "7" and p1["PA13"] == "37" and p1["PC14"] == "3"
    assert p2["RESET"] == "29" and p2["PB3"] == "15" and p2["PD3"] == "1"
    assert check_reset_drive.gpio_pins(b, u1) == {"37": "PA13", "3": "PC14"}
    assert check_reset_drive.gpio_pins(b, u2) == {"15": "PB3", "1": "PD3"}


# ------------------------------------------------------------- planted faults

def test_good_g4_board_is_judged_and_clean(tmp_path):
    p = _straps(_g4(tmp_path), tmp_path)
    assert p["checked"][0]["mcus"] == ["U1"]
    assert "mcu_unknown" not in _by_kind(p)
    assert not p["violations"]
    got = {s["strap"]: s for s in p["checked"][0]["straps"]}
    assert "low" in got["BOOT0"]["por"] and "high" in got["NRST"]["por"]


def test_boot0_pulled_high_fails(tmp_path):
    """The issue's planted fault, on each family's own pin name."""
    for value, fn in (("STM32G431CBT6", "PB8-BOOT0"),
                      ("STM32F407VGT6", "BOOT0"),
                      ("STM32F103C8T6", "BOOT0")):
        pm = {**G4, "BOOT0": ("45", fn)}
        pcb = _g4(tmp_path, value=value, boot="/B", pinmap=pm, nets={
            "+3V3": [("R9", "1", "")], "/B": [("R9", "2", "")]},
            comps={"R9": "10k"})
        (v,) = _by_kind(_straps(pcb, tmp_path))["strap_level"]
        assert v["strap"] == "BOOT0" and v["severity"] == "error", value
        assert "bootloader" in v["msg"] or "system memory" in v["msg"]


def test_boot0_left_floating_fails(tmp_path):
    (v,) = _by_kind(_straps(_g4(tmp_path, boot="/B"), tmp_path))["strap_level"]
    assert v["strap"] == "BOOT0" and "floating" in v["msg"]


def test_f407_boot0_divider_past_its_low_threshold_fails(tmp_path):
    """0.6 V is low on the row's 0.3 VDD, high-side of BOOT0's own 0.43 V."""
    pm = {**G4, "BOOT0": ("94", "BOOT0")}
    pcb = _g4(tmp_path, value="STM32F407VGT6", boot="/B", pinmap=pm,
              comps={"R8": "100k", "R9": "22k"}, nets={
                  "+3V3": [("R8", "1", "")],
                  "/B": [("R8", "2", ""), ("R9", "1", "")],
                  "GND": [("R9", "2", "")]})
    (v,) = _by_kind(_straps(pcb, tmp_path))["strap_level"]
    assert "undefined" in v["msg"]


def test_boot0_on_a_part_of_unknown_reset_state_only_warns(tmp_path):
    """bldc-drive: CAN RXD on PB8, with option bytes programmed after the
    first DFU. hwde can't see option bytes, so it asks rather than fails."""
    pcb = _g4(tmp_path, boot="/CAN_RXD", comps={"U2": "TCAN1044AV"},
              nets={"/CAN_RXD": [("U2", "4", "RXD")]})
    k = _by_kind(_straps(pcb, tmp_path))
    assert "strap_level" not in k
    (v,) = k["strap_unknown"]
    assert v["strap"] == "BOOT0" and "nSWBOOT0" in v["msg"]


def test_nrst_tied_low_fails_and_the_debug_header_is_fine(tmp_path):
    p = _g4(tmp_path, comps={"J1": "SWD"}, nets={"/NRST": [("J1", "3", "RESET")]})
    assert not _straps(p, tmp_path)["violations"]
    p = _g4(tmp_path, comps={"R9": "1k"}, nets={
        "/NRST": [("R9", "1", "")], "GND": [("R9", "2", "")]})
    (v,) = _by_kind(_straps(p, tmp_path))["strap_level"]
    assert v["strap"] == "NRST"


def test_nrst_on_a_supervisor_asks(tmp_path):
    p = _g4(tmp_path, comps={"U2": "TPS3839"}, nets={
        "/NRST": [("U2", "1", "RESET")]})
    (v,) = _by_kind(_straps(p, tmp_path))["strap_unknown"]
    assert v["strap"] == "NRST"


def test_jumper_on_boot0_is_not_an_unknown_part(tmp_path):
    """vna: JP201 from BOOT0, open as built, with 10 k to ground."""
    p = _g4(tmp_path, comps={"JP1": "BOOT0"}, nets={
        "/BOOT0": [("JP1", "1", "A")], "+3V3": [("JP1", "2", "B")]})
    assert "strap_unknown" not in _by_kind(_straps(p, tmp_path))


def test_gate_on_an_stm32_gpio_floats(tmp_path):
    """The pin regex comes from the row: PIN_RE was ^IO\\d+$ and saw no PAn."""
    nets = {"/PWM": [_pin("PA0"), ("Q1", "1", "G")], "GND": [("Q1", "2", "S")],
            "/D": [("Q1", "3", "D")]}
    k = _by_kind(_drive(_g4(tmp_path, comps={"Q1": "2N7002"}, nets=nets),
                        tmp_path))
    (v,) = k["gate_floating"]
    assert v["pin"] == "PA0" and v["severity"] == "error"
    nets["/PWM"].append(("R9", "1", ""))
    nets["GND"].append(("R9", "2", ""))
    k = _by_kind(_drive(_g4(tmp_path, comps={"Q1": "2N7002", "R9": "10k"},
                            nets=nets), tmp_path))
    assert "gate_floating" not in k


def test_debug_pull_turns_a_load_on(tmp_path):
    """PA15's JTDI pull-up holds a gate on; PB3 has no pull, so it floats."""
    nets = {"/EN": [_pin("PA15"), ("Q1", "1", "G")], "GND": [("Q1", "2", "S")],
            "/D": [("Q1", "3", "D")]}
    (v,) = _by_kind(_straps(_g4(tmp_path, comps={"Q1": "2N7002"}, nets=nets),
                            tmp_path))["load_on_at_reset"]
    assert v["pin"] == "PA15"
    nets["/EN"] = [_pin("PB3"), ("Q1", "1", "G")]
    pcb = _g4(tmp_path, comps={"Q1": "2N7002"}, nets=nets)
    assert "load_on_at_reset" not in _by_kind(_straps(pcb, tmp_path))
    assert _by_kind(_drive(pcb, tmp_path))["gate_floating"][0]["pin"] == "PB3"


def test_pa14_pull_down_leaving_the_board_warns(tmp_path):
    p = _g4(tmp_path, comps={"J1": "CONN"}, nets={
        "/LED": [_pin("PA14"), ("J1", "1", "1")]})
    (v,) = _by_kind(_straps(p, tmp_path))["reset_pull_offboard"]
    assert v["pin"] == "PA14"
    p = _g4(tmp_path, comps={"J1": "CONN"}, nets={
        "/SWCLK": [_pin("PA14"), ("J1", "1", "1")]})
    assert "reset_pull_offboard" not in _by_kind(_straps(p, tmp_path))


def _db(tmp_path, db_nodes=None, cc="PB6", db="PA9", cc_net="/SIG",
        cc_nodes=(), comps=None):
    c = {"R7": "10k", "R8": "100k", **(comps or {})}
    nets = {cc_net: [_pin(cc), ("R8", "1", ""), *cc_nodes],
            "+3V3": [("R8", "2", "")]}
    nets["/DB"] = [_pin(db), *(db_nodes or [])]
    if db_nodes is None:
        nets["/DB"] += [("R7", "1", "")]
        nets["+3V3"].append(("R7", "2", ""))
    return _g4(tmp_path, comps=c, nets=nets)


def test_dead_battery_rd_on_a_high_control_pin(tmp_path):
    """PA9 high puts 5.1 k on PB6 against the net's 100 k pull-up: 0.16 V, so
    a line that's meant to idle high reads low until firmware clears it."""
    k = _by_kind(_straps(_db(tmp_path), tmp_path))
    (v,) = k["dead_battery_rd"]
    assert (v["cc"], v["db"], v["mode"]) == ("PB6", "PA9", "on")
    assert "0.16 V, low" in v["msg"] and "UCPD1_DBDIS" in v["msg"]


def test_dead_battery_second_pair(tmp_path):
    (v,) = _by_kind(_straps(_db(tmp_path, cc="PB4", db="PA10"),
                            tmp_path))["dead_battery_rd"]
    assert (v["cc"], v["db"]) == ("PB4", "PA10")


def test_dead_battery_rd_reaches_the_reset_drive_read(tmp_path):
    """PB6 with Rd on holds a gate low, so it doesn't float."""
    nets = {"/G": [_pin("PB6"), ("Q1", "1", "G")], "GND": [("Q1", "2", "S")],
            "/D": [("Q1", "3", "D")], "/DB": [_pin("PA9")]}
    k = _by_kind(_drive(_g4(tmp_path, comps={"Q1": "2N7002"}, nets=nets),
                        tmp_path))
    assert k["gate_floating"][0]["pin"] == "PB6"
    nets["/DB"].append(("R7", "1", ""))
    nets["+3V3"] = [("R7", "2", "")]
    k = _by_kind(_drive(_g4(tmp_path, comps={"Q1": "2N7002", "R7": "10k"},
                            nets=nets), tmp_path))
    assert "gate_floating" not in k


def test_dead_battery_quiet_when_low_or_open_or_held_by_an_ic(tmp_path):
    p = _g4(tmp_path, comps={"R7": "10k"}, nets={
        "/SIG": [_pin("PB6")], "/DB": [_pin("PA9"), ("R7", "1", "")],
        "GND": [("R7", "2", "")]})
    s = _straps(p, tmp_path)
    assert "dead_battery_rd" not in _by_kind(s)
    assert s["checked"][0]["dead_battery"][0]["mode"] == "off"
    p = _g4(tmp_path, comps={"U2": "DRV8353RS"}, nets={
        "/SIG": [_pin("PB6")], "/DB": [_pin("PA9"), ("U2", "36", "INHB")]})
    s = _straps(p, tmp_path)
    assert "dead_battery_rd" not in _by_kind(s)
    assert s["checked"][0]["dead_battery"][0]["mode"] == "unknown"


def test_dead_battery_divider_may_put_rd(tmp_path):
    """vna's PA9: a 100 k / 100 k monitor reads mid-band."""
    p = _g4(tmp_path, comps={"R7": "100k", "R9": "100k"}, nets={
        "/SIG": [_pin("PB6")],
        "/DB": [_pin("PA9"), ("R7", "1", ""), ("R9", "1", "")],
        "+3V3": [("R7", "2", "")], "GND": [("R9", "2", "")]})
    (v,) = _by_kind(_straps(p, tmp_path))["dead_battery_rd"]
    assert v["mode"] == "maybe" and "may put" in v["msg"]


def test_usbc_cc_with_its_dbcc_open_warns_tied_is_fine(tmp_path):
    p = _g4(tmp_path, comps={"J1": "USB-C"}, nets={
        "/CC1": [_pin("PB6"), ("J1", "A5", "CC1")],
        "unconnected-(U1-PA9-Pad31)": [_pin("PA9")]})
    (v,) = _by_kind(_straps(p, tmp_path))["dead_battery_open"]
    assert (v["cc"], v["db"]) == ("PB6", "PA9")
    p = _g4(tmp_path, comps={"J1": "USB-C"}, nets={
        "/CC1": [_pin("PB6"), _pin("PA9"), ("J1", "A5", "CC1")]})
    s = _straps(p, tmp_path)
    assert not {"dead_battery_open", "dead_battery_rd"} & set(_by_kind(s))
    assert s["checked"][0]["dead_battery"][0]["mode"] == "tied"
    # a source: DBCC tied to ground, as RM0440 46.4.6 asks
    p = _g4(tmp_path, comps={"J1": "USB-C"}, nets={
        "/CC1": [_pin("PB6"), ("J1", "A5", "CC1")], "GND": [_pin("PA9")]})
    assert "dead_battery_open" not in _by_kind(_straps(p, tmp_path))


# ---------------------------------------------------------------- ATmega

def _avr(tmp_path, comps=None, nets=None, rail="+5V"):
    c = {"U1": "ATmega88A-AU", **(comps or {})}
    n = {rail: [("U1", "4", "VCC")], "GND": [("U1", "3", "GND")],
         "/RST": [("U1", "29", "PC6_(RESET/PCINT14)")]}
    for name, nodes in (nets or {}).items():
        n.setdefault(name, []).extend(nodes)
    return _netlist(tmp_path, c, n)


def test_atmega_is_judged_and_reset_pulls_itself_up_at_its_supply(tmp_path):
    p = _straps(_avr(tmp_path), tmp_path)
    assert not p["violations"] and p["checked"][0]["mcus"] == ["U1"]
    (s,) = p["checked"][0]["straps"]
    assert s["strap"] == "RESET" and "5.00 V, high" in s["por"]


def test_atmega_reset_tied_low_or_mid_band_fails(tmp_path):
    p = _avr(tmp_path, comps={"R1": "1k"}, nets={
        "/RST": [("R1", "1", "")], "GND": [("R1", "2", "")]})
    (v,) = _by_kind(_straps(p, tmp_path))["strap_level"]
    assert v["strap"] == "RESET" and v["severity"] == "error"
    # 200 k down against the chip's 45 k: 4.08 V, under VIH2 0.9 VCC at 5 V
    p = _avr(tmp_path, comps={"R1": "200k"}, nets={
        "/RST": [("R1", "1", "")], "GND": [("R1", "2", "")]})
    (v,) = _by_kind(_straps(p, tmp_path))["strap_level"]
    assert "undefined" in v["msg"]


def test_atmega_isp_pin_drives_a_load(tmp_path):
    p = _avr(tmp_path, comps={"Q1": "2N7002", "R1": "10k"}, nets={
        "/SCK": [("U1", "17", "PB5_(SCK/PCINT5)"), ("Q1", "1", "G"),
                 ("R1", "1", "")],
        "GND": [("Q1", "2", "S"), ("R1", "2", "")], "/D": [("Q1", "3", "D")]})
    (v,) = _by_kind(_straps(p, tmp_path))["programming_pin_load"]
    assert v["pin"] == "PB5" and "Q1" in v["refs"]


def test_atmega_miso_and_ordinary_pins_are_not_programming_loads(tmp_path):
    p = _avr(tmp_path, comps={"Q1": "2N7002", "R1": "10k"}, nets={
        "/MISO": [("U1", "16", "PB4_(PCINT4/MISO)"), ("Q1", "1", "G"),
                  ("R1", "1", "")],
        "GND": [("Q1", "2", "S"), ("R1", "2", "")], "/D": [("Q1", "3", "D")]})
    assert "programming_pin_load" not in _by_kind(_straps(p, tmp_path))


def test_atmega_gate_on_a_port_pin_floats(tmp_path):
    p = _avr(tmp_path, comps={"Q1": "2N7002"}, nets={
        "/G": [("U1", "1", "PD3_(PCINT19/OC2B/INT1)"), ("Q1", "1", "G")],
        "GND": [("Q1", "2", "S")], "/D": [("Q1", "3", "D")]})
    (v,) = _by_kind(_drive(p, tmp_path))["gate_floating"]
    assert v["pin"] == "PD3"
