"""check_reset_drive.py - every FET gate and part enable an MCU pin drives
sits at its off level from power-on until firmware drives the pin, and no
pin the chip glitches during power-up drives one on (P8 verify suite).

The mode: an MCU's GPIOs are inputs (or the chip's own pulls) from reset
until firmware configures them, tens to hundreds of ms on an ESP32 with its
ROM boot and bootloader. A FET gate or a load switch, regulator or driver
enable on such a pin with no pull of its own floats through that window,
and a floating gate turns on with a touch or the drain's dV/dt
(docs/failure-modes.yaml FW-08 shoot-through and motor spin, FW-09 outputs
live at reset; RATE-13 enables undefined at power-up). A pull doesn't help
on a pin the chip itself drives during power-up: Espressif's glitch tables
(the ESP32-S3's GPIO1-17 low for ~60 us, its USB_D+ high for up to
~170 ms) and the ROM boot log on U0TXD switch the load before firmware
runs (docs/ingest/test-fw-mech.md F5, safety-components.md R-FS-1).

How a pin is read: check_straps' resolver, at power-on, with the MCU's
pins undriven (an input, or the chip's reset pull where reset_states.yaml
gives one). For each MCU pin from reset_states.yaml `mcus`, every FET gate
(a Q pin G/GATE), BJT base (B/BASE) and every enable, shutdown or disable
pin of another part on its net or one resistor away is a load. A FET's off
level is its source's rail: source on ground, a gate below VGS(th) min is
off; source on a positive rail, a gate within VGS(th) min of that rail (a
P-FET) is off. A BJT is the same against its emitter and VBE 0.5 V. A
switch whose source or emitter rail can't be named from the net is
skipped. Pins the
chip pulls at reset (straps, reset_pulls) are check_straps'
load_on_at_reset; this check leaves their pull to it.

Findings:
  gate_floating       error: a FET gate an MCU pin drives has no pull, so
                      it floats from power-on until firmware drives the pin
                      (warning for a BJT base, which mostly leaks)
  gate_on_at_reset    error: the gate's pulls hold it past VGS(th) min, so
                      the FET conducts before firmware runs (warning when
                      the pull is hard to the on level, which reads as a
                      load meant to start on)
  enable_floating     warning: an enable an MCU pin drives has no pull
                      (fine only if the part's datasheet gives the pin an
                      internal pull to off)
  enable_undefined    error: the enable's pulls sit between VIL and VIH
  enable_on_at_reset  warning: the enable's pulls hold its part on before
                      firmware runs
  drive_glitch        error (warning for a glitch under 1 us): the MCU pin
                      is one the chip glitches or drives during power-up
                      toward the load's on level, which no pull holds off

The netlist is the board's own `<board>.net` beside the .kicad_pcb, or
--netlist; with none, or with no MCU reset_states.yaml knows, this check
reports nothing_to_judge (check_straps raises no_netlist / mcu_unknown).

CLI: --pcb board.kicad_pcb [--netlist board.net] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import check_straps as cs  # noqa: E402
import geom  # noqa: E402
import simlib  # noqa: E402

SCRIPT = "check_reset_drive"
SOURCE = SCRIPT
# a FET whose VGS(th) min reset_states.yaml doesn't give: the low end of
# the logic-level parts boards use (AO3400 0.65 V, Si2302 0.65 V, DMG2302
# 0.5 V)
DEFAULT_VTH = 0.5
# a glitch shorter than this is unlikely to get through the gate's own RC
GLITCH_ERROR_US = 1.0
# a BJT base this far from its emitter conducts (VBE(on) at low current)
VBE_ON = 0.5
# what a GPIO is called, unless the MCU's row says (`gpio`: STM32 PA0, AVR PD3)
GPIO_DEFAULT = r"^IO\d+$"


def load_glitches(row: dict) -> list[dict]:
    out = []
    for g in row.get("glitch") or []:
        out.append({**g, "_pins": re.compile(g["pins"], re.I)})
    return out


def gpio_pins(board, mcu) -> dict[str, str]:
    """{pin number: canonical IO name} for the MCU's GPIOs."""
    out = {}
    gpio = re.compile(mcu["row"].get("gpio", GPIO_DEFAULT))
    for name, pin in cs.mcu_pins(board, mcu).items():
        if gpio.match(name):
            out.setdefault(pin, name)
    return out


def reset_pulled(row: dict) -> dict:
    """{IO name: spec} of the pins the chip pulls from reset: check_straps'
    reset-pull set (straps with a pull, then reset_pulls)."""
    out = {}
    for key in ("straps", "reset_pulls"):
        for name, spec in (row.get(key) or {}).items():
            if spec.get("pull") in ("up", "down"):
                out.setdefault(name, spec)
    return out


def fet_source_volts(board, ref: str, gate_pin: str) -> float | None:
    """The rail on the FET's source pin (G1 pairs with S1 on a dual), or
    a BJT's emitter for its base."""
    gname = board.pins[ref][gate_pin]["name"]
    idx = re.sub(r"^(G|GATE|B|BASE)", "", gname)
    src = r"^(E|EMITTER)(\d*)$" if re.match(r"^(B|BASE)", gname) \
        else r"^(S|SOURCE)(\d*)$"
    for pin, meta in board.pins[ref].items():
        m = re.match(src, meta["name"])
        if m and m.group(2) == idx:
            return board.rail(board.net(ref, pin))
    return None


def load_kind(board, ref, rpin):
    meta = board.pins[ref][rpin]
    if cs.prefix(ref) == "Q" and re.match(r"^(G|GATE)\d?$", meta["name"]):
        return "gate"
    if cs.prefix(ref) == "Q" and re.match(r"^(B|BASE)\d?$", meta["name"]):
        return "base"
    if cs.prefix(ref) in cs.PASSIVE_PREFIXES + cs.CONNECTOR_PREFIXES \
            or ref in board.shorts or cs.prefix(ref) == "R":
        return None
    if cs.part_on(meta["name"], meta.get("bar", False), "high") is None:
        return None
    return "enable"


def on_levels(glitch: dict) -> set[str]:
    return {"low": {"low"}, "high": {"high"},
            "both": {"low", "high"}, "toggle": {"low", "high"}}[
        glitch["level"]]


def check_mcu(board, kb, mcu, bg) -> tuple[list, list]:
    viols, facts = [], []
    row = mcu["row"]
    vdd = cs.mcu_vdd(board, mcu)
    ohm = row.get("pull_ohm", 45000)
    unverified = "" if row.get("verified", False) else \
        " (reset_states.yaml: unverified)"
    pulled = reset_pulled(row)
    names = gpio_pins(board, mcu)
    mpulls = {}
    for pin, name in names.items():
        spec = pulled.get(name)
        if spec:
            mpulls[(mcu["ref"], pin)] = (
                vdd if spec["pull"] == "up" else 0.0, spec.get("ohm", ohm),
                f"{mcu['ref']} {name} internal {spec['pull']}")
    mpulls.update(cs.dead_battery_pulls(board, kb, mcu))
    res = cs.Resolver(board, kb, mcu, mcu_pulls=mpulls)
    glitches = load_glitches(row)
    for pin, name in sorted(names.items(), key=lambda kv: kv[0].zfill(4)):
        g = board.net(mcu["ref"], pin)
        if board.unconnected(g) or board.rail(g) is not None:
            continue
        glitch = next((x for x in glitches if x["_pins"].match(name)), None)
        head = f"{mcu['ref']} {name} (pin {pin}, {board.label(g)})"
        seen = set()
        for ref, rpin, gg in cs.near_nodes(board, g):
            if ref == mcu["ref"] or (ref, rpin) in seen:
                continue
            seen.add((ref, rpin))
            kind = load_kind(board, ref, rpin)
            if kind is None:
                continue
            meta = board.pins[ref][rpin]
            pos = cs.pad_pos(bg, ref, rpin)
            refs = sorted({mcu["ref"], ref})
            bjt = kind == "base"
            switch = "base" if bjt else "gate"
            what = (f"{ref} ({board.value(ref)}) {switch} on "
                    f"{board.label(gg)}" if kind in ("gate", "base") else
                    f"{ref} {meta['name']} ({board.value(ref)}) on "
                    f"{board.label(gg)}")
            if kind in ("gate", "base"):
                vs = fet_source_volts(board, ref, rpin)
                if vs is None:
                    continue
                vth = VBE_ON if bjt else \
                    cs.fet_vth(kb, board.value(ref)) or DEFAULT_VTH
                st = res.state(gg, "por", vdd=vdd, vil=cs.IO_VIL,
                               vih=cs.IO_VIH)
                on_level = "high" if vs <= 0 else "low"
                fact = {"mcu": mcu["ref"], "pin": pin, "name": name,
                        "load": ref, "kind": kind, "net": board.label(gg),
                        "source_v": vs, "por": st["text"]}
            else:
                st = res.state(gg, "por", skip={(ref, rpin)},
                               vdd=res.part_vdd(ref), vil=cs.IO_VIL,
                               vih=cs.IO_VIH)
                on_level = "high" if cs.part_on(
                    meta["name"], meta.get("bar", False), "high") else "low"
                fact = {"mcu": mcu["ref"], "pin": pin, "name": name,
                        "load": ref, "kind": "enable",
                        "part_pin": meta["name"], "net": board.label(gg),
                        "por": st["text"]}
            facts.append(fact)
            if glitch and on_level in on_levels(glitch):
                us = float(glitch.get("us") or 0)
                viols.append(checklib.violation(
                    SCRIPT, "error" if us >= GLITCH_ERROR_US else "warning",
                    pos, None, board.label(gg), refs,
                    f"{head} drives {what}, and the chip drives that pin "
                    f"during power-up ({glitch.get('why', glitch['level'])}),"
                    f" toward the level that turns it on: no pull holds it "
                    "off. Move the load to a pin with no power-up glitch"
                    f"{unverified}", SOURCE, kind="drive_glitch", pin=name,
                    part_pin=meta["name"], glitch=glitch["level"],
                    glitch_us=us))
            if st["state"] in ("toggling", "may_toggle", "firmware"):
                continue  # a clock or an expander on the net: check_straps'
            if name in pulled:
                continue  # a pin the chip pulls: check_straps' load_on_at_reset
            if kind in ("gate", "base"):
                rail = "its emitter rail" if bjt else "its source rail"
                to = rail if vs > 0 else "ground"
                if st["volts"] is None:
                    if st["unknown"]:
                        continue  # something else on the net may hold it
                    why = ("leakage and pickup can bias it on" if bjt else
                           "the FET can turn on from a touch or its drain's "
                           "dV/dt")
                    viols.append(checklib.violation(
                        SCRIPT, "warning" if bjt else "error", pos, None,
                        board.label(gg), refs,
                        f"{head} drives {what} with no pull on it, so the "
                        f"{switch} floats from power-on until firmware "
                        f"drives the pin ({why}): add a pull to {to} at the "
                        f"{switch}", SOURCE, kind="gate_floating", pin=name))
                    continue
                vgs = abs(st["volts"] - vs)
                if vgs < vth:
                    continue
                hard = vgs >= 0.9 * max(abs(vdd - vs), abs(vs))
                limit = (f"VBE {vth:g} V" if bjt else
                         f"its minimum VGS(th) {vth:g} V")
                viols.append(checklib.violation(
                    SCRIPT, "warning" if hard else "error", pos, None,
                    board.label(gg), refs,
                    f"{head} drives {what}, and with the pin undriven the "
                    f"{switch} sits at {st['text']}: {vgs:.2f} V from {rail}, "
                    f"at or above {limit}, so it conducts before firmware "
                    f"runs. Pull the {switch} to {to} unless the load is "
                    "meant to start on", SOURCE,
                    kind="gate_on_at_reset", pin=name,
                    volts=checklib.rnd(st["volts"], 3), vth_min=vth))
                continue
            if st["state"] == "floating":
                if st["unknown"]:
                    continue
                viols.append(checklib.violation(
                    SCRIPT, "warning", pos, None, board.label(gg), refs,
                    f"{head} drives {what} with no pull on it, so the enable "
                    "floats from power-on until firmware drives the pin: add "
                    f"a pull to {'ground' if on_level == 'high' else 'its supply'}"
                    " (the off level) unless the part's datasheet gives the "
                    "pin an internal one", SOURCE, kind="enable_floating",
                    pin=name, part_pin=meta["name"]))
            elif st["state"] == "undefined":
                viols.append(checklib.violation(
                    SCRIPT, "error", pos, None, board.label(gg), refs,
                    f"{head} drives {what}, and with the pin undriven the "
                    f"enable sits at {st['text']}: the part may be on or off "
                    "before firmware runs. Make the pulls agree on the off "
                    "level", SOURCE, kind="enable_undefined", pin=name,
                    part_pin=meta["name"],
                    volts=checklib.rnd(st["volts"], 3)))
            elif st["state"] == on_level:
                viols.append(checklib.violation(
                    SCRIPT, "warning", pos, None, board.label(gg), refs,
                    f"{head} drives {what}, and with the pin undriven the "
                    f"enable reads {st['text']}: the part is on before "
                    "firmware runs. Pull it to the off level unless the "
                    "load is meant to start on", SOURCE,
                    kind="enable_on_at_reset", pin=name,
                    part_pin=meta["name"]))
    return viols, facts


def check(pcb: Path, netlist: Path | None = None, kb: dict | None = None):
    kb = cs.load_kb() if kb is None else kb
    netlist = netlist or cs.find_netlist(pcb)
    if netlist is None:
        return [], {"netlist": None, "mcus": [],
                    "nothing_to_judge": "no netlist beside the board"}
    bg = geom.load_board(pcb)
    board = cs.Board(simlib.parse_netlist(netlist))
    viols, loads = [], []
    mcus = cs.find_mcus(board, kb)
    for mcu in mcus:
        v, f = check_mcu(board, kb, mcu, bg)
        viols += v
        loads += f
    values = {r: board.value(r) for r in board.pins}
    unknown = {r: values[r] for r in cs.unknown_mcus(values, kb)}
    seen = {"netlist": str(netlist), "mcus": [m["ref"] for m in mcus],
            "mcus_unknown": sorted(unknown), "loads": loads}
    why = cs.mcu_verdict(mcus, unknown)
    if why:
        seen["nothing_to_judge"] = why
    return viols, seen


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--netlist")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    nl = Path(args.netlist) if args.netlist else None
    if nl is not None and not nl.is_file():
        raise checklib.CheckError(f"netlist not found: {nl}")
    viols, seen = check(pcb, nl)
    return checklib.report(SCRIPT, pcb, viols, checked=[seen],
                           nothing_to_judge=seen.get("nothing_to_judge")
                           ), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
