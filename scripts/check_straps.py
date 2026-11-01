"""check_straps.py - an MCU's strapping pins read the level they need at
power-on and on an MCU-only reset, the pins it pulls at reset leave no load
on, and an IO expander's pins come up at a defined level (P8 verify suite).

The hit: hifi-receiver-2's core passed every gate while its ESP32 GPIO0
strap could latch a 50 MHz clock: the RMII oscillator's OE came from a
TCA9555 pin whose internal 100 k pull-up fought a 100 k pull-down (1.65 V,
mid-band, and the oscillator reads a floating OE as enabled), and once
firmware set that pin, it stayed set through every ESP32 reset (review-2
DIGITAL-1/2). ERC can't see an internal pull-up or what holds through a
reset; this check resolves each net from the netlist and
reference/reset_states.yaml.

How a net resolves (for one event, `por` or `mcu_reset`): every resistor
to a rail is a pull; a resistor to another net brings that net's own pulls
and drivers in series; 0 R, inductors, ferrites and fuses join nets; a part
in reset_states.yaml adds its pins' internal pulls; an oscillator output
drives a clock while its enable resolves high or floating. An expander's
pins hold whatever firmware set through an MCU-only reset unless its RESET
pin is on the MCU's enable net. The level is the pulls' Thevenin voltage
against the MCU's VIL/VIH (with its own internal pull). Capacitors, diodes,
transistors and switches don't drive at reset (a transistor is taken as
off). Any other IC pin, and any connector pin, is a part whose reset state
the file doesn't know.

Findings:
  strap_level         error (the strap's severity): a strapping pin reads
                      the wrong level, an undefined one, a clock or a level
                      firmware left behind, at power-on or on an MCU reset
  strap_unknown       warning: a strap with a wanted level also reaches pins
                      with no known reset state (confirm each is an input or
                      hi-Z at reset, or add the part to reset_states.yaml)
  load_on_at_reset    warning: an MCU pin the chip pulls at reset (a
                      strap's pull, or reset_pulls: the ESP32-C3's JTAG
                      pins) holds a FET gate past its VGS(th) min or a
                      part's enable asserted, so the load is on before
                      firmware runs (minuet v3 GPIO6, the LED strip)
  reset_pull_offboard warning: a reset_pulls pin's net leaves the board on
                      a connector with no pull but the chip's own (a debug
                      header excepted): whatever is plugged in sees it
  io_reset_undefined  error: an expander pin sits between VIL and VIH of its
                      supply at power-on (its pull fights an external one)
  fet_on_at_reset     error: a MOSFET gate on an expander pin (directly or
                      through one resistor) sits at or above the FET's
                      minimum VGS(th) at power-on (reset_states.yaml fets)
  io_reset_floating   warning: an expander pin with no pull at all, and
                      nothing else on its net that could hold it
  io_reset_holds      warning: an expander whose outputs survive an MCU
                      reset (no RESET pin on the MCU's enable)
  mcu_unknown         warning: a part reset_states.yaml's mcu_like calls an
                      MCU has no row, so nothing about it was judged
  no_netlist          warning: the board has an MCU from the file but no
                      netlist beside it, so nothing was resolved
  dead_battery_rd     warning: an STM32G4 UCPD dead-battery control pin (PA9,
                      PA10) reads high (or may) at reset, so the chip puts
                      5.1 k to ground on the CC pin it controls (PB6, PB4)
                      until firmware sets UCPD1_DBDIS
  dead_battery_open   warning: a CC pin reaches a USB-C connector with its
                      DBCC pin neither on that CC line nor tied low (RM0440
                      46.4.6: one or the other)
  programming_pin_load warning: a pin the programmer clocks while the MCU is
                      held in reset (the ATmega's MOSI, SCK; reset_states.yaml
                      `programming`) drives a FET gate or a part's enable, so
                      that load switches during every program cycle

The netlist is the board's own `<board>.net` beside the .kicad_pcb (the one
the pipeline exports), or --netlist.

CLI: --pcb board.kicad_pcb [--netlist board.net] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import engcalc  # noqa: E402
import geom  # noqa: E402
import simlib  # noqa: E402
from check_ratings import volts_from_name  # noqa: E402

SCRIPT = "check_straps"
SOURCE = SCRIPT  # verify_all, gate and the scorecard match a finding to its check by source
RESET_YAML = HERE.parent / "reference" / "reset_states.yaml"
EVENTS = ("por", "mcu_reset")
EVENT_TEXT = {"por": "at power-on", "mcu_reset": "on an MCU-only reset"}
# an expander pin's load is taken as CMOS: VIL 0.3, VIH 0.7 of its supply
IO_VIL, IO_VIH = 0.3, 0.7
DEFAULT_VDD = 3.3
MAX_DEPTH = 4       # series resistors followed from a pin
SHORT_PREFIXES = ("L", "FB", "F", "NT")
PASSIVE_PREFIXES = ("C", "D", "LED", "Q", "SW", "S", "TP", "H", "MH", "BT",
                    "K", "JP")
CONNECTOR_PREFIXES = ("J", "P", "CN", "CON", "X")


def load_kb(path: Path = RESET_YAML) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    for row in doc.get("mcus") or []:
        row["_match"] = re.compile(row["match"], re.I)
    for row in doc.get("parts") or []:
        row["_match"] = re.compile(row["match"], re.I)
        row["_pins"] = re.compile(row.get("pins") or "$^", re.I)
    osc = doc.get("oscillators") or {}
    for k in ("refs", "output", "enable"):
        osc["_" + k] = re.compile(osc.get(k) or "$^", re.I)
    doc["oscillators"] = osc
    return doc


def pin_name(pinfunction: str, pin: str) -> str:
    """'IO0_25' -> 'IO0', '~{INT}_1' -> 'INT', 'GPIO12' -> 'IO12'."""
    name = (pinfunction or "").strip()
    if pin and name.endswith(f"_{pin}"):
        name = name[: -len(pin) - 1]
    name = re.sub(r"~\{([^}]*)\}", r"\1", name).upper()
    return re.sub(r"^GPIO(\d+)$", r"IO\1", name)


def prefix(ref: str) -> str:
    m = re.match(r"^[A-Za-z]+", ref)
    return m.group(0).upper() if m else ""


class Board:
    """The netlist as nets and pins, with 0 R / inductor joins merged."""

    def __init__(self, parsed: dict):
        self.comps = parsed["components"]
        self.pins: dict[str, dict[str, dict]] = {}
        for net, nodes in parsed["nets"].items():
            for nd in nodes:
                self.pins.setdefault(nd["ref"], {})[nd["pin"]] = {
                    "net": net, "name": pin_name(nd.get("pinfunction"),
                                                 nd["pin"]),
                    "bar": "~{" in (nd.get("pinfunction") or "")}
        parent = {n: n for n in parsed["nets"]}

        def find(n):
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n
        self.shorts = set()
        for ref, pins in self.pins.items():
            if len(pins) == 2 and self.is_short(ref):
                a, b = (p["net"] for p in pins.values())
                parent[find(a)] = find(b)
                self.shorts.add(ref)
        self.group = {n: find(n) for n in parent}
        self.nodes: dict[str, list] = {}
        for net, nodes in parsed["nets"].items():
            for nd in nodes:
                if nd["ref"] in self.shorts:
                    continue
                self.nodes.setdefault(self.group[net], []).append(
                    (nd["ref"], nd["pin"]))
        self.names: dict[str, list] = {}
        for net in parsed["nets"]:
            self.names.setdefault(self.group[net], []).append(net)

    def value(self, ref: str) -> str:
        return (self.comps.get(ref) or {}).get("value") or ""

    def is_short(self, ref: str) -> bool:
        p = prefix(ref)
        if p in SHORT_PREFIXES:
            return True
        return p == "R" and engcalc.parse_value(self.value(ref)) == 0

    def net(self, ref: str, pin: str) -> str:
        return self.group[self.pins[ref][pin]["net"]]

    def rail(self, g: str) -> float | None:
        for name in self.names.get(g, []):
            v = volts_from_name(name)
            if v is not None:
                return v
        return None

    def label(self, g: str) -> str:
        return sorted(self.names.get(g, [g]), key=len)[0]

    def unconnected(self, g: str) -> bool:
        return all(n.startswith("unconnected-") for n in self.names.get(g, []))


def part_supply(board: Board, ref: str) -> float:
    """The rail on a part's supply pin (DEFAULT_VDD if none can be named)."""
    for meta in board.pins.get(ref, {}).values():
        if re.match(r"^(VCC|VDD|VDDIO|VIO|3V3|VDD3P3)", meta["name"]):
            v = board.rail(board.group[meta["net"]])
            if v:
                return v
    return DEFAULT_VDD


def mcu_vdd(board: Board, mcu: dict) -> float:
    """An MCU's supply: the row's `vdd`, or the rail on its own supply pin
    for a family that runs at more than one voltage (`vdd: supply`)."""
    vdd = mcu["row"].get("vdd", DEFAULT_VDD)
    return part_supply(board, mcu["ref"]) if vdd == "supply" else vdd


class Resolver:
    def __init__(self, board: Board, kb: dict, mcu: dict | None,
                 mcu_pulls: dict | None = None):
        self.b, self.kb, self.mcu = board, kb, mcu
        # {(mcu ref, pin): (volts, ohm, label)}: MCU pins whose own reset
        # pull counts (check_reset_pulls); the strap check leaves it empty
        self.mcu_pulls = mcu_pulls or {}
        self.osc = kb["oscillators"]
        self.parts = {}
        for ref in board.pins:
            val = board.value(ref)
            for row in kb.get("parts") or []:
                if row["_match"].search(val):
                    self.parts[ref] = row
                    break
        self.enable_net = None
        if mcu:
            pins = mcu_pins(board, mcu)
            for name in mcu["row"]["enable"]:
                if name.upper() in pins:
                    self.enable_net = board.net(mcu["ref"], pins[name.upper()])

    def part_vdd(self, ref: str) -> float:
        return part_supply(self.b, ref)

    def is_osc(self, ref: str) -> bool:
        if not self.osc["_refs"].match(ref):
            return False
        return any(self.osc["_output"].match(m["name"])
                   for m in self.b.pins[ref].values())

    def reset_follows_mcu(self, ref: str, row: dict) -> bool:
        names = [n.upper() for n in (row.get("reset") or [])]
        for pin, meta in self.b.pins[ref].items():
            if meta["name"] in names and self.enable_net \
                    and self.b.net(ref, pin) == self.enable_net:
                return True
        return False

    def contributions(self, g: str, event: str, skip: set, series: float = 0,
                      depth: int = 0, seen: frozenset = frozenset()) -> dict:
        """Pulls, drivers and unknown pins on net group g, seen through
        `series` ohms. skip: (ref, pin) pairs not to count (the observer)."""
        out = {"pulls": [], "drivers": [], "unknown": []}
        seen = seen | {g}
        v = self.b.rail(g)
        if v is not None:
            out["pulls"].append((v, series, self.b.label(g)))
            return out
        for ref, pin in self.b.nodes.get(g, []):
            if (ref, pin) in skip:
                continue
            meta = self.b.pins[ref][pin]
            p = prefix(ref)
            where = f"{ref}.{pin} ({meta['name'] or self.b.value(ref)})"
            if (ref, pin) in self.mcu_pulls:
                pv, ohm, label = self.mcu_pulls[(ref, pin)]
                out["pulls"].append((pv, series + ohm, label))
                continue
            if self.mcu and ref == self.mcu["ref"]:
                continue  # its other pins are inputs at reset
            if p == "R" and len(self.b.pins[ref]) == 2:
                r = engcalc.parse_value(self.b.value(ref))
                other = [q for q in self.b.pins[ref] if q != pin][0]
                og = self.b.net(ref, other)
                if r is None:
                    out["unknown"].append(f"{ref} (value "
                                          f"{self.b.value(ref)!r})")
                    continue
                if og in seen or depth >= MAX_DEPTH:
                    continue
                sub = self.contributions(og, event, skip, series + r,
                                         depth + 1, seen)
                for k in out:
                    out[k] += sub[k]
                continue
            row = self.parts.get(ref)
            if row is not None:
                if row["kind"] == "expander" and row["_pins"].match(
                        meta["name"]):
                    if event == "mcu_reset" and not \
                            self.reset_follows_mcu(ref, row):
                        out["drivers"].append({
                            "kind": "firmware", "series": series,
                            "where": where})
                    elif row.get("pull"):
                        pv = (self.part_vdd(ref) if row["pull"]["dir"] == "up"
                              else 0.0)
                        out["pulls"].append(
                            (pv, series + row["pull"]["ohm"],
                             f"{where} internal {row['pull']['dir']}"))
                    continue
                if row["kind"] == "input" and row["_pins"].match(
                        meta["name"]):
                    continue
                if row["kind"] == "expander":
                    continue  # INT, SCL/SDA, address pins: no drive at reset
            if self.is_osc(ref):
                if self.osc["_output"].match(meta["name"]):
                    out["drivers"].append(self.osc_drive(ref, event, series,
                                                         where, seen))
                continue
            if p in PASSIVE_PREFIXES or ref in self.b.shorts:
                continue
            if p in CONNECTOR_PREFIXES:
                out["unknown"].append(f"{where}, a connector")
                continue
            out["unknown"].append(f"{where} {self.b.value(ref)}".strip())
        return out

    def osc_drive(self, ref, event, series, where, seen) -> dict:
        ens = [(pin, m) for pin, m in self.b.pins[ref].items()
               if self.osc["_enable"].match(m["name"])]
        d = {"kind": "clock", "series": series, "where": where}
        if not ens:
            d["why"] = "no enable pin"
            return d
        pin, _ = ens[0]
        eg = self.b.net(ref, pin)
        if self.b.unconnected(eg):
            d["why"] = "enable pin open"
            return d
        st = self.state(eg, event, skip={(ref, pin)},
                        vdd=self.part_vdd(ref), vil=0.3, vih=0.7,
                        seen=seen)
        en = st["state"]
        if en == "low":
            d["kind"] = "off"
        elif en in ("high", "floating"):
            d["why"] = f"enable {self.b.label(eg)} {en}"
        else:
            d["kind"] = "maybe_clock"
            d["why"] = f"enable {self.b.label(eg)} {st['text']}"
        return d

    def state(self, g, event, skip=frozenset(), vdd=DEFAULT_VDD, vil=0.25,
              vih=0.75, internal=None, seen=frozenset()) -> dict:
        """The level of net group g: high | low | undefined | floating |
        toggling | may_toggle | firmware, with the pulls behind it."""
        c = self.contributions(g, event, set(skip), seen=frozenset(seen))
        pulls = list(c["pulls"])
        if internal:
            pulls.append(internal)
        drivers = [d for d in c["drivers"] if d["kind"] != "off"]
        res = {"unknown": c["unknown"], "pulls": [p[2] for p in pulls],
               "drivers": drivers}
        for kind, st in (("clock", "toggling"), ("maybe_clock", "may_toggle"),
                         ("firmware", "firmware")):
            hits = [d for d in drivers if d["kind"] == kind]
            if hits:
                d = hits[0]
                text = {"toggling": f"a clock from {d['where']}",
                        "may_toggle": f"maybe a clock from {d['where']}",
                        "firmware": f"whatever firmware left on "
                                    f"{d['where']}"}[st]
                if d.get("why"):
                    text += f" ({d['why']})"
                return {**res, "state": st, "volts": None, "text": text}
        hard = [p for p in pulls if p[1] == 0]
        if hard:
            volts = hard[0][0]
        elif pulls:
            gsum = sum(1 / p[1] for p in pulls)
            volts = sum(p[0] / p[1] for p in pulls) / gsum
        else:
            return {**res, "state": "floating", "volts": None,
                    "text": "floating (no pull)"}
        if volts >= vih * vdd:
            st = "high"
        elif volts <= vil * vdd:
            st = "low"
        else:
            st = "undefined"
        pulls_txt = ", ".join(
            f"{p[2]}{'' if p[1] == 0 else f' {p[1] / 1000:.3g} k'}"
            for p in pulls)
        return {**res, "state": st, "volts": volts,
                "text": f"{volts:.2f} V, {st} (VIL {vil * vdd:.2f} / VIH "
                        f"{vih * vdd:.2f} V; {pulls_txt})"}


def unknown_mcus(values: dict[str, str], kb: dict) -> list[str]:
    """Refs whose Value is an MCU (kb mcu_like) that no `mcus` row matches:
    nothing about their straps or reset states can be judged."""
    like = kb.get("mcu_like")
    if not like:
        return []
    return sorted(ref for ref, val in values.items()
                  if re.search(like, val or "", re.I)
                  and not any(r["_match"].search(val)
                              for r in kb.get("mcus") or []))


def mcu_verdict(known: list, unknown: dict[str, str]) -> str | None:
    """Why an MCU check judged nothing (None when it had an MCU to judge):
    the coverage line in place of a pass."""
    if known:
        return None
    if unknown:
        return ("reference/reset_states.yaml has no row for "
                + ", ".join(f"{r} ({v})" for r, v in sorted(unknown.items())))
    return "no MCU on the board"


def find_mcus(board: Board, kb: dict) -> list[dict]:
    out = []
    for ref in sorted(board.pins):
        val = board.value(ref)
        for row in kb.get("mcus") or []:
            if row["_match"].search(val):
                out.append({"ref": ref, "row": row, "value": val})
                break
    return out


def mcu_pins(board: Board, mcu: dict) -> dict[str, str]:
    """{canonical strap name: pin number} for one MCU. A pin's name splits
    on the row's `pin_split` (PB8-BOOT0, PD3_(PCINT19/OC2B/INT1))."""
    row = mcu["row"]
    aliases = {k.upper(): v.upper() for k, v in
               (row.get("aliases") or {}).items()}
    split = row.get("pin_split", r"[/,]")
    out = {}
    for pin, meta in board.pins[mcu["ref"]].items():
        for tok in re.split(split, meta["name"]):
            tok = re.sub(r"^GPIO(\d+)$", r"IO\1", tok.strip())
            if not tok:
                continue
            tok = aliases.get(tok, tok)
            out.setdefault(tok, pin)
    return out


def pad_pos(bg, ref: str, pin: str):
    if bg is None:
        return None
    for p in bg.pads_of(ref=ref):
        if p.number == pin:
            return p.center
    return None


def check_mcu(board, kb, mcu, bg) -> tuple[list, list]:
    viols, facts = [], []
    row = mcu["row"]
    res = Resolver(board, kb, mcu)
    vdd = mcu_vdd(board, mcu)
    pins = mcu_pins(board, mcu)
    unverified = "" if row.get("verified", False) else \
        " (reset_states.yaml: unverified)"
    for strap, spec in row["straps"].items():
        pin = pins.get(strap)
        if pin is None:
            continue
        g = board.net(mcu["ref"], pin)
        internal = None
        if spec.get("pull") in ("up", "down"):
            internal = (vdd if spec["pull"] == "up" else 0.0,
                        spec.get("ohm", row.get("pull_ohm", 45000)),
                        f"{mcu['ref']} internal {spec['pull']}")
        want = spec.get("want", "any")
        fact = {"mcu": mcu["ref"], "strap": strap, "pin": pin,
                "net": board.label(g), "want": want}
        bad = {}
        unknown = []
        for event in EVENTS:
            st = res.state(g, event, skip={(mcu["ref"], pin)}, vdd=vdd,
                           vil=spec.get("vil", row.get("vil", 0.25)),
                           vih=spec.get("vih", row.get("vih", 0.75)),
                           internal=internal)
            fact[event] = st["text"]
            unknown = st["unknown"]
            if spec.get("connector_ok"):
                # a reset line on the debug header is the point of the header
                unknown = [u for u in unknown if not u.endswith("a connector")]
            ok = st["state"] == want or (
                want == "any" and st["state"] in ("high", "low", "undefined",
                                                  "floating"))
            if st["state"] == "floating" and unknown and not internal:
                # no pull, but a part we don't know is on the net: it may hold
                # the pin either way, so this is strap_unknown, not "floating"
                ok = True
            if not ok:
                bad[event] = st["text"]
        facts.append(fact)
        pos = pad_pos(bg, mcu["ref"], pin)
        if bad:
            events = " and ".join(EVENT_TEXT[e] for e in bad)
            texts = sorted(set(bad.values()))
            also = (f"; it also reaches {', '.join(sorted(set(unknown)))}, "
                    "whose reset state hwde doesn't know" if unknown else "")
            viols.append(checklib.violation(
                SCRIPT, spec.get("severity", "error"), pos, None,
                board.label(g), [mcu["ref"]],
                f"{mcu['ref']} {strap} (pin {pin}, {board.label(g)}) must "
                f"read {want} at reset, but {events} it reads "
                f"{' / '.join(texts)}{also}: {spec.get('why', '')}{unverified}",
                SOURCE, kind="strap_level", strap=strap, events=sorted(bad),
                states=bad))
        elif unknown and want != "any":
            viols.append(checklib.violation(
                SCRIPT, "warning", pos, None, board.label(g), [mcu["ref"]],
                f"{mcu['ref']} {strap} (pin {pin}, {board.label(g)}) also "
                f"reaches {', '.join(sorted(set(unknown)))}, whose reset "
                "state hwde doesn't know: confirm each is an input or hi-Z "
                f"until the strap latches (or add the part to "
                f"reset_states.yaml). {spec.get('why', '')}", SOURCE,
                kind="strap_unknown",
                strap=strap, unknown=sorted(set(unknown))))
    return viols, facts


def check_expanders(board, kb, mcu, bg) -> tuple[list, list]:
    viols, facts = [], []
    res = Resolver(board, kb, mcu)
    for ref, row in sorted(res.parts.items()):
        if row["kind"] != "expander":
            continue
        vdd = res.part_vdd(ref)
        used = 0
        for pin, meta in sorted(board.pins[ref].items(),
                                key=lambda kv: kv[0].zfill(4)):
            if not row["_pins"].match(meta["name"]):
                continue
            g = board.net(ref, pin)
            if board.unconnected(g):
                continue
            used += 1
            st = res.state(g, "por", vdd=vdd, vil=IO_VIL, vih=IO_VIH)
            facts.append({"part": ref, "pin": pin, "name": meta["name"],
                          "net": board.label(g), "por": st["text"]})
            pos = pad_pos(bg, ref, pin)
            if st["state"] == "undefined":
                viols.append(checklib.violation(
                    SCRIPT, "error", pos, None, board.label(g), [ref],
                    f"{ref} {meta['name']} ({board.label(g)}) comes up at "
                    f"{st['text']} at power-on, before firmware writes it: "
                    "make the pulls agree (one pull, or a part with no "
                    "internal pull-up)", SOURCE, kind="io_reset_undefined",
                    volts=checklib.rnd(st["volts"], 3)))
            for gate in gates_on(board, g):
                gref, gpin, gg = gate
                vth = fet_vth(kb, board.value(gref))
                if vth is None:
                    continue
                gst = res.state(gg, "por", vdd=vdd, vil=IO_VIL, vih=IO_VIH)
                if gst["volts"] is not None and gst["volts"] >= vth:
                    viols.append(checklib.violation(
                        SCRIPT, "error", pad_pos(bg, gref, gpin), None,
                        board.label(gg), [ref, gref],
                        f"{gref} ({board.value(gref)}) gate on "
                        f"{board.label(gg)} sits at {gst['volts']:.2f} V at "
                        f"power-on, from {ref} {meta['name']}, at or above "
                        f"its minimum VGS(th) {vth:g} V: a low-threshold "
                        "part conducts before firmware runs. Hold the gate "
                        "well below VGS(th) min until firmware drives it",
                        SOURCE, kind="fet_on_at_reset",
                        volts=checklib.rnd(gst["volts"], 3), vth_min=vth))
            if st["state"] == "floating" and not st["unknown"]:
                viols.append(checklib.violation(
                    SCRIPT, "warning", pos, None, board.label(g), [ref],
                    f"{ref} {meta['name']} ({board.label(g)}) floats from "
                    "power-on until firmware drives it: add a pull to the "
                    "level its load must see", SOURCE,
                    kind="io_reset_floating"))
        if used and mcu and not res.reset_follows_mcu(ref, row):
            how = ("has no RESET pin" if not row.get("reset")
                   else "its RESET pin isn't on the MCU's enable")
            viols.append(checklib.violation(
                SCRIPT, "warning", pad_pos(bg, ref, "1"), None, None,
                [ref, mcu["ref"]],
                f"{ref} ({board.value(ref)}) {how}, so its outputs keep "
                f"what firmware set through every {mcu['ref']} reset "
                "(watchdog, panic, EN): anything they enable stays on while "
                "the straps latch", SOURCE, kind="io_reset_holds"))
    return viols, facts


# An IC pin that switches its part (a load switch, regulator, LED driver)
# on: asserted means on for an enable, off for a shutdown. An enable is
# active high unless overbarred or n-named; a shutdown, sleep or standby
# pin is active low (the convention even when the symbol drops the bar).
ENABLE_NAMES = re.compile(r"^(N|N_)?(EN|ENA|ENABLE|EN[0-9]|CE|ON|PWR_?EN|"
                          r"LED_?EN|LOAD_?EN)(_?N|B)?$")
SHUTDOWN_NAMES = re.compile(r"^(N|N_)?(SHDN|SHUTDOWN|SLEEP|STBY|STANDBY|SDZ|"
                            r"SDN|SD_N|SDB)"
                            r"(_?N|B)?$")
DISABLE_NAMES = re.compile(r"^(N|N_)?(DIS|DISABLE|OFF|DRVOFF)(_?N|B)?$")
# a connector pin whose net or pin is a debug port: its pull is the point
DEBUG_NAMES = re.compile(r"JTAG|TMS|TCK|TDI|TDO|SWD|SWCLK|SWO", re.I)


def part_on(name: str, bar: bool, level: str) -> bool | None:
    """Whether a pin at `level` (high | low) leaves its part on; None if the
    pin doesn't switch the part."""
    low_named = bool(re.match(r"^N_?[A-Z]", name)
                     and not name.startswith(("NC", "ON"))) \
        or bool(re.search(r"(_N|[^A]B)$", name))
    if ENABLE_NAMES.match(name):
        active = "low" if bar or low_named else "high"
        return level == active
    if SHUTDOWN_NAMES.match(name):
        return level == "high"
    if DISABLE_NAMES.match(name):
        active = "low" if bar or low_named else "high"
        return level != active
    return None


def near_nodes(board, g) -> list[tuple]:
    """(ref, pin, net group) of every pin on net group g or one resistor
    away from it (the far side of that resistor)."""
    out = [(ref, pin, g) for ref, pin in board.nodes.get(g, [])]
    for ref, pin in board.nodes.get(g, []):
        if prefix(ref) == "R" and len(board.pins[ref]) == 2:
            other = [q for q in board.pins[ref] if q != pin][0]
            og = board.net(ref, other)
            if og != g and board.rail(og) is None:
                out += [(r, p, og) for r, p in board.nodes.get(og, [])
                        if r != ref]
    return out


def check_reset_pulls(board, kb, mcu, bg) -> tuple[list, list]:
    """Every MCU pin the chip pulls at reset (a strap's pull, or a pin in
    the row's reset_pulls: the ESP32-C3's JTAG pins) holds its net from
    power-on until firmware drives it. Where that net switches a load (a
    FET gate past VGS(th), a part's enable) the load comes up on before
    firmware runs; where it leaves the board on a connector with nothing
    but the MCU's own pull, whatever the connector feeds sees it."""
    viols, facts = [], []
    row = mcu["row"]
    vdd = mcu_vdd(board, mcu)
    ohm = row.get("pull_ohm", 45000)
    pins = mcu_pins(board, mcu)
    unverified = "" if row.get("verified", False) else \
        " (reset_states.yaml: unverified)"
    pulled = {}
    for name, spec in (row.get("straps") or {}).items():
        if spec.get("pull") in ("up", "down"):
            pulled[name] = {**spec, "strap": True}
    for name, spec in (row.get("reset_pulls") or {}).items():
        if spec.get("pull") in ("up", "down"):
            pulled.setdefault(name, {**spec, "strap": False})
    mpulls = {}
    for name, spec in pulled.items():
        pin = pins.get(name)
        if pin is not None:
            mpulls[(mcu["ref"], pin)] = (
                vdd if spec["pull"] == "up" else 0.0, spec.get("ohm", ohm),
                f"{mcu['ref']} {name} internal {spec['pull']}")
    mpulls.update(dead_battery_pulls(board, kb, mcu))
    res = Resolver(board, kb, mcu, mcu_pulls=mpulls)
    for name, spec in sorted(pulled.items()):
        pin = pins.get(name)
        if pin is None:
            continue
        g = board.net(mcu["ref"], pin)
        if board.unconnected(g):
            continue
        st = res.state(g, "por", vdd=vdd, vil=row.get("vil", 0.25),
                       vih=row.get("vih", 0.75))
        facts.append({"mcu": mcu["ref"], "pin": pin, "name": name,
                      "net": board.label(g), "pull": spec["pull"],
                      "por": st["text"]})
        if st["state"] in ("toggling", "may_toggle", "firmware"):
            continue
        pos = pad_pos(bg, mcu["ref"], pin)
        head = (f"{mcu['ref']} {name} (pin {pin}, {board.label(g)}) is "
                f"pulled {spec['pull']} by the chip from reset until "
                "firmware drives it")
        why = f" ({spec['why']})" if spec.get("why") else ""
        seen = set()
        for ref, rpin, gg in near_nodes(board, g):
            if ref == mcu["ref"] or (ref, rpin) in seen:
                continue
            seen.add((ref, rpin))
            meta = board.pins[ref][rpin]
            gst = st if gg == g else res.state(gg, "por", vdd=vdd,
                                               vil=IO_VIL, vih=IO_VIH)
            if prefix(ref) == "Q" and meta["name"] in GATE_NAMES:
                vth = fet_vth(kb, board.value(ref))
                if vth is None or gst["volts"] is None \
                        or gst["volts"] < vth:
                    continue
                viols.append(checklib.violation(
                    SCRIPT, "warning", pad_pos(bg, ref, rpin), None,
                    board.label(gg), [mcu["ref"], ref],
                    f"{head}{why}, so {ref} ({board.value(ref)}) gate on "
                    f"{board.label(gg)} sits at {gst['volts']:.2f} V, at or "
                    f"above its minimum VGS(th) {vth:g} V: its load is on "
                    "before firmware runs. Hold the gate off with a pull "
                    "the chip's can't beat, or use a pin with no reset "
                    f"pull{unverified}", SOURCE, kind="load_on_at_reset",
                    pin=name, volts=checklib.rnd(gst["volts"], 3),
                    vth_min=vth))
                continue
            if prefix(ref) in PASSIVE_PREFIXES + CONNECTOR_PREFIXES \
                    or ref in board.shorts or prefix(ref) == "R":
                continue
            if gst["state"] not in ("high", "low"):
                continue
            on = part_on(meta["name"], meta.get("bar", False),
                         gst["state"])
            if not on:
                continue
            viols.append(checklib.violation(
                SCRIPT, "warning", pad_pos(bg, ref, rpin), None,
                board.label(gg), [mcu["ref"], ref],
                f"{head}{why}, so {ref} {meta['name']} "
                f"({board.value(ref)}) reads {gst['state']} and the part "
                "is on before firmware runs. Hold it off with an external "
                f"pull, or use a pin with no reset pull{unverified}",
                SOURCE, kind="load_on_at_reset", pin=name,
                part_pin=meta["name"]))
        if spec["strap"] or spec.get("offboard") is False:
            continue  # a boot pin on a programming header is meant to be
        ext = [p for p in st["pulls"] if not p.startswith(f"{mcu['ref']} ")]
        conns = [f"{ref}.{rpin}" for ref, rpin in board.nodes.get(g, [])
                 if prefix(ref) in CONNECTOR_PREFIXES]
        if not conns or ext or st["unknown"] and any(
                not u.endswith("a connector") for u in st["unknown"]):
            continue
        names = " ".join(board.names.get(g, [])) + " " + " ".join(
            board.pins[c.split(".")[0]][c.split(".")[1]]["name"]
            for c in conns)
        if DEBUG_NAMES.search(names):
            continue
        viols.append(checklib.violation(
            SCRIPT, "warning", pos, None, board.label(g), [mcu["ref"]],
            f"{head}{why}, and the net leaves the board on "
            f"{', '.join(sorted(conns))} with nothing else holding it: "
            f"whatever is plugged in sees it {st['state']} from power-on "
            "and through every reset (an LED strip or a load switch "
            "comes on early). Add an external pull to the idle level, or "
            f"move the signal to a pin with no reset pull{unverified}",
            SOURCE, kind="reset_pull_offboard", pin=name))
    return viols, facts


def dead_battery_states(board, kb, mcu) -> list[dict]:
    """Each UCPD dead-battery pair in the row (`dead_battery`): what its DBCC
    pin reads at reset, so whether the chip's Rd sits on its CC pin. `tied`
    is the wiring the manual means (DBCC on the CC line); `on` is high;
    `maybe` is mid-band or a clock; `unknown` is no pull but an IC whose
    reset state we don't know, listed in the facts and not judged."""
    row, out = mcu["row"], []
    pins = mcu_pins(board, mcu)
    vdd = mcu_vdd(board, mcu)
    res = Resolver(board, kb, mcu)
    for pair in row.get("dead_battery") or []:
        cc, db = pins.get(pair["cc"].upper()), pins.get(pair["db"].upper())
        if cc is None or db is None:
            continue
        cg, dg = board.net(mcu["ref"], cc), board.net(mcu["ref"], db)
        d = {**pair, "cc_pin": cc, "db_pin": db, "cc_net": cg, "db_net": dg}
        if cg == dg:
            d.update(mode="tied", text="on its CC line")
        elif board.unconnected(dg):
            d.update(mode="open", text="not connected")
        else:
            st = res.state(dg, "por", skip={(mcu["ref"], db)}, vdd=vdd,
                           vil=row.get("vil", 0.3), vih=row.get("vih", 0.7))
            d["text"] = st["text"]
            d["mode"] = {"high": "on", "low": "off", "undefined": "maybe",
                         "toggling": "maybe", "may_toggle": "maybe",
                         "firmware": "maybe"}.get(
                st["state"], "unknown" if st["unknown"] else "open")
        out.append(d)
    return out


def dead_battery_pulls(board, kb, mcu) -> dict:
    """{(mcu, CC pin): (0 V, Rd, label)} for each CC pin whose Rd is on at
    reset. PB4's JTAG pull-up (40 k) is also on there; Rd is eight times
    stronger and sets the level, so it stands in for both."""
    return {(mcu["ref"], d["cc_pin"]):
            (0.0, d["ohm"], f"{mcu['ref']} {d['cc']} dead-battery Rd")
            for d in dead_battery_states(board, kb, mcu)
            if d["mode"] == "on"}


def check_dead_battery(board, kb, mcu, bg) -> tuple[list, list]:
    viols, facts = [], []
    ref = mcu["ref"]
    res = Resolver(board, kb, mcu,
                   mcu_pulls=dead_battery_pulls(board, kb, mcu))
    for d in dead_battery_states(board, kb, mcu):
        g = d["cc_net"]
        facts.append({"mcu": ref, "cc": d["cc"], "db": d["db"],
                      "cc_net": board.label(g), "db_net": board.label(d["db_net"]),
                      "mode": d["mode"], "db_reads": d["text"]})
        if board.unconnected(g):
            continue
        typec = any(re.search(r"\bCC[12]\b", n)
                    for n in board.names.get(g, [])) or any(
            prefix(r) in CONNECTOR_PREFIXES and re.fullmatch(
                r"(A5|B5|CC[12]?)", board.pins[r][p]["name"])
            for r, p in board.nodes.get(g, []))
        pos = pad_pos(bg, ref, d["cc_pin"])
        if typec and d["mode"] in ("open", "on", "maybe", "unknown"):
            viols.append(checklib.violation(
                SCRIPT, "warning", pos, None, board.label(g), [ref],
                f"{ref} {d['cc']} ({board.label(g)}) reaches a USB-C CC pin, "
                f"but its dead-battery pin {d['db']} is {d['text']}: "
                "RM0440 46.4.6 wants it on that CC line (a sink that must "
                "enumerate unpowered) or tied to ground, or the Rd the source "
                "sees is left to whatever that pin does", SOURCE,
                kind="dead_battery_open", cc=d["cc"], db=d["db"]))
            continue
        if d["mode"] not in ("on", "maybe"):
            continue
        st = res.state(g, "por", vdd=mcu_vdd(board, mcu), vil=IO_VIL,
                       vih=IO_VIH)
        may = "may put" if d["mode"] == "maybe" else "puts"
        viols.append(checklib.violation(
            SCRIPT, "warning", pos, None, board.label(g), [ref],
            f"{ref} {d['db']} ({board.label(d['db_net'])}) reads {d['text']} "
            f"at reset, which {may} the chip's {d['ohm'] / 1000:g} k dead-battery "
            f"Rd on {d['cc']} ({board.label(g)}) until firmware sets "
            f"UCPD1_DBDIS in PWR_CR3: that net reads {st['text']} before then. "
            "Hold the control pin low at reset, or check the net's load "
            "tolerates it", SOURCE, kind="dead_battery_rd", cc=d["cc"],
            db=d["db"], mode=d["mode"]))
    return viols, facts


def check_programming(board, kb, mcu, bg) -> tuple[list, list]:
    """A pin a programmer clocks while the MCU is held in reset (the row's
    `programming`): a FET gate or enable on it switches with every byte."""
    row = mcu["row"]
    prog = row.get("programming")
    if not prog:
        return [], []
    viols, facts = [], []
    pins = mcu_pins(board, mcu)
    for name in prog["pins"]:
        pin = pins.get(name.upper())
        if pin is None:
            continue
        g = board.net(mcu["ref"], pin)
        if board.unconnected(g):
            continue
        loads, seen = [], set()
        for ref, rpin, gg in near_nodes(board, g):
            if ref == mcu["ref"] or (ref, rpin) in seen:
                continue
            seen.add((ref, rpin))
            meta = board.pins[ref][rpin]
            if prefix(ref) == "Q":
                hit = meta["name"] in GATE_NAMES
            elif prefix(ref) in PASSIVE_PREFIXES + CONNECTOR_PREFIXES \
                    or ref in board.shorts or prefix(ref) == "R":
                hit = False
            else:
                hit = part_on(meta["name"], meta.get("bar", False),
                              "high") is not None
            if not hit:
                continue
            loads.append(ref)
            viols.append(checklib.violation(
                SCRIPT, "warning", pad_pos(bg, ref, rpin), None,
                board.label(gg), [mcu["ref"], ref],
                f"{mcu['ref']} {name} (pin {pin}, {board.label(g)}) is clocked "
                f"by the programmer while the MCU is in reset ({prog['why']}), "
                f"and it drives {ref} {meta['name']} ({board.value(ref)}): "
                "that load switches during every program cycle. Move it to "
                "another pin, or gate it off while RESET is low", SOURCE,
                kind="programming_pin_load", pin=name))
        facts.append({"mcu": mcu["ref"], "pin": pin, "name": name,
                      "net": board.label(g), "loads": loads})
    return viols, facts


GATE_NAMES = ("G", "GATE")


def gates_on(board, g) -> list[tuple]:
    """(ref, pin, gate net group) of every FET gate on net group g or one
    resistor away from it."""
    groups = [g]
    for ref, pin in board.nodes.get(g, []):
        if prefix(ref) == "R" and len(board.pins[ref]) == 2:
            other = [q for q in board.pins[ref] if q != pin][0]
            groups.append(board.net(ref, other))
    out = []
    for gg in groups:
        for ref, pin in board.nodes.get(gg, []):
            if prefix(ref) == "Q" and \
                    board.pins[ref][pin]["name"] in GATE_NAMES:
                out.append((ref, pin, gg))
    return out


def fet_vth(kb: dict, value: str) -> float | None:
    for row in kb.get("fets") or []:
        if re.search(row["match"], value or "", re.I):
            return row["vth_min"]
    return None


def find_netlist(pcb: Path) -> Path | None:
    own = pcb.with_suffix(".net")
    if own.is_file():
        return own
    nets = sorted(pcb.parent.glob("*.net"))
    return nets[0] if len(nets) == 1 else None


def check(pcb: Path, netlist: Path | None = None, kb: dict | None = None):
    kb = load_kb() if kb is None else kb
    bg = geom.load_board(pcb)
    netlist = netlist or find_netlist(pcb)
    if netlist is None:
        mcus = [ref for ref, fp in bg.footprints.items()
                if any(r["_match"].search(fp.get("value") or "")
                       for r in kb.get("mcus") or [])]
        if not mcus:
            return [], {"netlist": None, "mcus": [],
                        "nothing_to_judge": "no netlist beside the board, "
                                            "and no known MCU on it"}
        return [checklib.violation(
            SCRIPT, "warning", None, None, None, mcus,
            f"{', '.join(sorted(mcus))}: no netlist beside {pcb.name} "
            f"({pcb.with_suffix('.net').name}), so the straps and reset "
            "states weren't checked: export it (kicad-cli sch export "
            "netlist)", SOURCE, kind="no_netlist")], {"netlist": None,
                                                      "mcus": mcus}
    board = Board(simlib.parse_netlist(netlist))
    viols, straps, ios = [], [], []
    mcus = find_mcus(board, kb)
    pulls, dead, prog = [], [], []
    for mcu in mcus:
        v, f = check_mcu(board, kb, mcu, bg)
        viols += v
        straps += f
        v, f = check_reset_pulls(board, kb, mcu, bg)
        viols += v
        pulls += f
        v, f = check_dead_battery(board, kb, mcu, bg)
        viols += v
        dead += f
        v, f = check_programming(board, kb, mcu, bg)
        viols += v
        prog += f
    v, f = check_expanders(board, kb, mcus[0] if mcus else None, bg)
    viols += v
    ios += f
    values = {r: board.value(r) for r in board.pins}
    unknown = {r: values[r] for r in unknown_mcus(values, kb)}
    for ref, val in sorted(unknown.items()):
        viols.append(checklib.violation(
            SCRIPT, "warning", None, None, None, [ref],
            f"{ref} ({val}) is an MCU reference/reset_states.yaml has no row "
            "for, so its straps and reset pulls weren't judged: add its row "
            "from its datasheet", SOURCE, kind="mcu_unknown"))
    seen = {"netlist": str(netlist),
            "mcus": [m["ref"] for m in mcus],
            "mcus_unknown": sorted(unknown),
            "straps": straps, "reset_pulls": pulls,
            "dead_battery": dead, "programming": prog,
            "expander_pins": ios}
    if not mcus and not ios:
        seen["nothing_to_judge"] = mcu_verdict(mcus, unknown)
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
