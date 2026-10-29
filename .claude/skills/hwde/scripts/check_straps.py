"""check_straps.py - an MCU's strapping pins read the level they need at
power-on and on an MCU-only reset, and an IO expander's pins come up at a
defined level (P8 verify suite).

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
  io_reset_undefined  error: an expander pin sits between VIL and VIH of its
                      supply at power-on (its pull fights an external one)
  io_reset_floating   warning: an expander pin with no pull at all, and
                      nothing else on its net that could hold it
  io_reset_holds      warning: an expander whose outputs survive an MCU
                      reset (no RESET pin on the MCU's enable)
  no_netlist          warning: the board has an MCU from the file but no
                      netlist beside it, so nothing was resolved

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
SOURCE = "check.straps"
RESET_YAML = HERE.parent / "reference" / "reset_states.yaml"
EVENTS = ("por", "mcu_reset")
EVENT_TEXT = {"por": "at power-on", "mcu_reset": "on an MCU-only reset"}
# an expander pin's load is taken as CMOS: VIL 0.3, VIH 0.7 of its supply
IO_VIL, IO_VIH = 0.3, 0.7
DEFAULT_VDD = 3.3
MAX_DEPTH = 4       # series resistors followed from a pin
SHORT_PREFIXES = ("L", "FB", "F", "NT")
PASSIVE_PREFIXES = ("C", "D", "LED", "Q", "SW", "S", "TP", "H", "MH", "BT",
                    "K")
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
                                                 nd["pin"])}
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


class Resolver:
    def __init__(self, board: Board, kb: dict, mcu: dict | None):
        self.b, self.kb, self.mcu = board, kb, mcu
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
            for pin, meta in board.pins[mcu["ref"]].items():
                if meta["name"] in [e.upper() for e in mcu["row"]["enable"]]:
                    self.enable_net = board.net(mcu["ref"], pin)

    def part_vdd(self, ref: str) -> float:
        for meta in self.b.pins.get(ref, {}).values():
            if re.match(r"^(VCC|VDD|VDDIO|VIO|3V3|VDD3P3)", meta["name"]):
                v = self.b.rail(self.b.group[meta["net"]])
                if v:
                    return v
        return DEFAULT_VDD

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
    """{canonical strap name: pin number} for one MCU."""
    aliases = {k.upper(): v.upper() for k, v in
               (mcu["row"].get("aliases") or {}).items()}
    out = {}
    for pin, meta in board.pins[mcu["ref"]].items():
        for tok in re.split(r"[/,]", meta["name"]):
            tok = re.sub(r"^GPIO(\d+)$", r"IO\1", tok.strip())
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
    vdd = row.get("vdd", DEFAULT_VDD)
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
                        row.get("pull_ohm", 45000),
                        f"{mcu['ref']} internal {spec['pull']}")
        want = spec.get("want", "any")
        fact = {"mcu": mcu["ref"], "strap": strap, "pin": pin,
                "net": board.label(g), "want": want}
        bad = {}
        unknown = []
        for event in EVENTS:
            st = res.state(g, event, skip={(mcu["ref"], pin)}, vdd=vdd,
                           vil=row.get("vil", 0.25), vih=row.get("vih", 0.75),
                           internal=internal)
            fact[event] = st["text"]
            unknown = st["unknown"]
            ok = st["state"] == want or (
                want == "any" and st["state"] in ("high", "low", "undefined",
                                                  "floating"))
            if not ok:
                bad[event] = st["text"]
        facts.append(fact)
        pos = pad_pos(bg, mcu["ref"], pin)
        if bad:
            events = " and ".join(EVENT_TEXT[e] for e in bad)
            texts = sorted(set(bad.values()))
            viols.append(checklib.violation(
                SCRIPT, spec.get("severity", "error"), pos, None,
                board.label(g), [mcu["ref"]],
                f"{mcu['ref']} {strap} (pin {pin}, {board.label(g)}) must "
                f"read {want} at reset, but {events} it reads "
                f"{' / '.join(texts)}: {spec.get('why', '')}{unverified}",
                SOURCE, kind="strap_level", strap=strap, events=sorted(bad),
                states=bad))
        elif unknown and want != "any":
            viols.append(checklib.violation(
                SCRIPT, "warning", pos, None, board.label(g), [mcu["ref"]],
                f"{mcu['ref']} {strap} (pin {pin}, {board.label(g)}) also "
                f"reaches {', '.join(sorted(set(unknown)))}, whose reset "
                "state hwde doesn't know: confirm each is an input or hi-Z "
                f"until the strap latches (or add the part to "
                "reset_states.yaml)", SOURCE, kind="strap_unknown",
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
            elif st["state"] == "floating" and not st["unknown"]:
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
            return [], {"netlist": None, "mcus": []}
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
    for mcu in mcus:
        v, f = check_mcu(board, kb, mcu, bg)
        viols += v
        straps += f
    v, f = check_expanders(board, kb, mcus[0] if mcus else None, bg)
    viols += v
    ios += f
    return viols, {"netlist": str(netlist),
                   "mcus": [m["ref"] for m in mcus],
                   "straps": straps, "expander_pins": ios}


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
    return checklib.report(SCRIPT, pcb, viols, checked=[seen]), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
