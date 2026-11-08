"""check_transformer.py - a transformer's windings are wired as its drawing
and the design's topology say: phasing, bridges, and the turns the wiring
selects (P8 verify suite, issue #644).

The hit: hw-lab mains-psu (hw-lab #17, cold review 1 finding 1) had bulk on
T1's dotted primary pin and the drain on the other, with the rectifier's
anode on the dotted secondary pin. That is a forward converter: the output
and aux rectifiers conduct in the on-time and VDD heads for about 103 V
against the controller's 38 V. erc, verify and attest all passed it. The
same review found the 12 V winding on 8-11 (5 turns) where the drawing's
bracket is 8-13 with 10 and 11 bridged (7 turns). Each part's windings are in
reference/transformers.yaml, row by row from the maker's drawing.

How it reads: every part whose Value matches a row is taken apart into its
physical windings (dot, end, turns). Pins on one net join (a bridge if that
net reaches nothing else); the joined windings form a used winding whose two
outer ends are the pins wired to the rest of the board, its turns counted
from the dots. On the primary, the switch end is the pin whose net holds a
transistor's drain or collector, or an IC pin named DRAIN/D/SW/LX/PH. In the
on-time the switch end is pulled low, so the dotted ends swing negative if
the switch end is dotted and positive if not. On each other winding the
rectifier is the diode reached from its switching end, the end with fewer
parts on it (through 0 R, ferrites and series resistors of 100 ohm or less;
the other end is a rail or return, where an output TVS sits): its
anode's end is the end it sits on, or the
other end if its cathode sits there. A flyback's rectifiers block in the
on-time, so (switch end dotted) == (anode end dotted); a forward converter's
conduct, so they differ.

Findings:
  xfmr_phase           error: a winding's rectifier conducts in the on-time
                       of a flyback (or blocks in a forward's)
  xfmr_turns           error: a winding constraints.json declares
                       (transformers: {T1: {turns: {"8-13": 7}}}) isn't the
                       two ends of one wired winding, or has other turns
  xfmr_bridge_missing  error: pins the drawing bridges sit on two nets
  xfmr_bridge_loaded   error: a bridge node the drawing keeps to itself
                       reaches other parts (the windings aren't in series)
  xfmr_winding_shorted error: a winding's two ends on one net
  xfmr_winding_open    warning: one end of a winding wired, the other open
                       (its turns are out of the circuit)

The netlist is the board's own `<board>.net` beside the .kicad_pcb, or
--netlist. A board with no netlist, or no part with a row, reports
nothing_to_judge. A T-part the file doesn't know, a primary with no switch
found, or a winding with no diode rectifier (a synchronous FET) goes in
not_judged.

CLI: --pcb board.kicad_pcb [--netlist board.net] [--constraints c.json]
     [--out report.json]
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
from check_straps import Board, find_netlist, pad_pos, prefix  # noqa: E402

SCRIPT = "check_transformer"
SOURCE = SCRIPT
KB_YAML = HERE.parent / "reference" / "transformers.yaml"
SERIES_MAX_OHM = 100
MAX_DEPTH = 2
TOPOLOGIES = ("flyback", "forward")
Q_SWITCH_PIN = re.compile(r"^(D|DRAIN|C|COLLECTOR)$")
IC_SWITCH_PIN = re.compile(r"^(D|DRAIN|SW\d*|LX\d*|PH|PHASE)$")
ANODE = re.compile(r"^(A|ANODE)$")
CATHODE = re.compile(r"^(K|C|KA|CATHODE)$")
IC_PREFIXES = ("U", "IC")


def load_kb(path: Path = KB_YAML) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    for row in doc.get("transformers") or []:
        row["_match"] = re.compile(row["match"], re.I)
        names = {w["name"] for w in row["windings"]}
        if row.get("primary") not in names:
            raise checklib.CheckError(
                f"reference/transformers.yaml {row['match']}: primary "
                f"{row.get('primary')!r} is not one of its windings")
    return doc


def row_for(kb: dict, value: str):
    for row in kb.get("transformers") or []:
        if row["_match"].search(value or ""):
            return row
    return None


def _decl(constraints: dict | None) -> dict:
    out = (constraints or {}).get("transformers") or {}
    if not isinstance(out, dict):
        raise checklib.CheckError(
            "constraints transformers: must map a ref to {topology, turns}")
    for ref, d in out.items():
        topo = (d or {}).get("topology")
        if topo is not None and topo not in TOPOLOGIES:
            raise checklib.CheckError(
                f"constraints transformers {ref}: topology {topo!r} is not "
                f"one of {', '.join(TOPOLOGIES)}")
    return out


class Xfmr:
    """One transformer on the board: its pins' states and used windings."""

    def __init__(self, board: Board, ref: str, row: dict):
        self.board, self.ref, self.row = board, ref, row
        self.pins = board.pins.get(ref, {})
        self.state = {p: self._state(p) for p in self._all_pins()}

    def _all_pins(self):
        out = set(self.pins)
        for w in self.row["windings"]:
            out |= {w["dot"], w["end"], *(w.get("taps") or [])}
        return out

    def group(self, pin):
        return self.board.net(self.ref, pin) if pin in self.pins else None

    def _state(self, pin) -> str:
        """ext (reaches other parts), bridge (only this part's pins) or
        open (no net, unconnected, or alone)."""
        g = self.group(pin)
        if g is None or self.board.unconnected(g):
            return "open"
        nodes = self.board.nodes.get(g, [])
        if any(r != self.ref for r, _ in nodes):
            return "ext"
        own = {p for r, p in nodes if r == self.ref}
        return "bridge" if len(own) > 1 else "open"

    def used(self) -> tuple[list[dict], list[str]]:
        """Used windings: windings joined through shared nets, with their
        wired ends' positions in turns (a dot sits `turns` above its end).
        A conflict (windings in opposition on one node) is returned too."""
        wired = [w for w in self.row["windings"]
                 if self.state[w["dot"]] != "open"
                 or self.state[w["end"]] != "open"]
        key = {}
        for w in wired:
            for p in (w["dot"], w["end"]):
                key[p] = self.group(p) if self.state[p] != "open" \
                    else f"open:{p}"
        comps, conflicts, done = [], [], set()
        for w0 in wired:
            if w0["name"] in done:
                continue
            pos = {key[w0["dot"]]: float(w0["turns"]), key[w0["end"]]: 0.0}
            members, grew = [w0], True
            done.add(w0["name"])
            while grew:
                grew = False
                for w in wired:
                    if w["name"] in done:
                        continue
                    kd, ke = key[w["dot"]], key[w["end"]]
                    if kd in pos or ke in pos:
                        if kd in pos:
                            pos.setdefault(ke, pos[kd] - w["turns"])
                        else:
                            pos[kd] = pos[ke] + w["turns"]
                        if abs(pos[kd] - pos[ke] - w["turns"]) > 1e-9:
                            conflicts.append(w["name"])
                        members.append(w)
                        done.add(w["name"])
                        grew = True
            ends = {}
            for w in members:
                for p in (w["dot"], w["end"]):
                    if self.state[p] == "ext":
                        ends.setdefault(key[p], []).append(p)
            for w in members:
                for p in w.get("taps") or []:
                    if self.state.get(p) == "ext":
                        ends.setdefault(self.group(p), []).append(p)
            comps.append({"windings": [w["name"] for w in members],
                          "pos": pos, "ends": ends})
        return comps, conflicts


def _diode_pins(board: Board, ref: str):
    a = [p for p, m in board.pins[ref].items() if ANODE.match(m["name"])]
    k = [p for p, m in board.pins[ref].items() if CATHODE.match(m["name"])]
    return a, k


def rectifiers(board: Board, g0: str, other_ends: set) -> list[tuple]:
    """Diodes reached from net group g0 through series resistors:
    [(ref, 'A' | 'K')], the diode's pin facing g0. A diode with its other
    pin on one of other_ends (a clamp across the winding) is left out."""
    out, seen = [], {g0}

    def visit(g, depth):
        for ref, pin in board.nodes.get(g, []):
            p = prefix(ref)
            if p == "D":
                a, k = _diode_pins(board, ref)
                if not a or not k:
                    continue
                far = k if pin in a else a if pin in k else []
                if any(board.net(ref, q) in other_ends for q in far):
                    continue
                if pin in a:
                    out.append((ref, "A"))
                elif pin in k:
                    out.append((ref, "K"))
            elif p == "R" and len(board.pins[ref]) == 2 and depth < MAX_DEPTH:
                r = engcalc.parse_value(board.value(ref))
                if r is None or r > SERIES_MAX_OHM:
                    continue
                q = [x for x in board.pins[ref] if x != pin][0]
                og = board.net(ref, q)
                if og not in seen and not board.unconnected(og):
                    seen.add(og)
                    visit(og, depth + 1)
    visit(g0, 0)
    return out


def switch_on(board: Board, g: str) -> str | None:
    for ref, pin in board.nodes.get(g, []):
        name = board.pins[ref][pin]["name"]
        if prefix(ref) == "Q" and Q_SWITCH_PIN.match(name):
            return f"{ref}.{name}"
        if prefix(ref) in IC_PREFIXES and IC_SWITCH_PIN.match(name):
            return f"{ref}.{name}"
    return None


def _span(comp: dict) -> tuple | None:
    """(dotted end group, undotted end group, turns) of a used winding."""
    if len(comp["ends"]) < 2:
        return None
    gs = sorted(comp["ends"], key=lambda g: comp["pos"][g])
    lo, hi = gs[0], gs[-1]
    return hi, lo, comp["pos"][hi] - comp["pos"][lo]


def _pins_txt(comp, g) -> str:
    return "/".join(sorted(comp["ends"][g], key=lambda p: (len(p), p)))


def _fmt(n: float) -> str:
    return f"{n:g}"


def check_one(board, bg, x: Xfmr, decl: dict) -> tuple[list, dict]:
    viols, ref, row = [], x.ref, x.row
    val = board.value(ref)
    src = row["source"].split(";")[0].strip()
    topo = decl.get("topology") or row.get("topology")
    fact = {"ref": ref, "value": val, "topology": topo, "windings": []}

    def pos(pin):
        return pad_pos(bg, ref, pin)

    def v(sev, pin, msg, kind, refs=None, **extra):
        net = board.label(x.group(pin)) if x.group(pin) else None
        viols.append(checklib.violation(
            SCRIPT, sev, pos(pin), None, net, refs or [ref], msg, SOURCE,
            kind=kind, pin=pin, **extra))

    for w in row["windings"]:
        d, e = w["dot"], w["end"]
        sd, se = x.state[d], x.state[e]
        if sd != "open" and se != "open" and x.group(d) == x.group(e):
            v("error", d, f"{ref} ({val}) winding {w['name']} ({d}-{e}) has "
              f"both ends on {board.label(x.group(d))}: it is shorted. "
              f"{src}", "xfmr_winding_shorted")
        elif (sd == "open") != (se == "open") and not w.get("shield"):
            on, off = (d, e) if sd != "open" else (e, d)
            v("warning", off, f"{ref} ({val}) winding {w['name']} ({d}-{e}, "
              f"{_fmt(w['turns'])} turns) is wired at {on} "
              f"({board.label(x.group(on))}) and open at {off}, so its turns "
              f"are out of the circuit; the drawing has it in use. {src}",
              "xfmr_winding_open")
    for a, b in row.get("bridges") or []:
        ga, gb = x.group(a), x.group(b)
        sa, sb = x.state[a], x.state[b]
        if sa == "open" and sb == "open":
            continue
        if ga != gb or sa == "open" or sb == "open":
            v("error", a, f"{ref} ({val}) pins {a} and {b} aren't joined: "
              f"the drawing bridges them to put its windings in series. "
              f"{row['source']}", "xfmr_bridge_missing")
        elif sa == "ext":
            others = sorted({r for r, _ in board.nodes.get(ga, [])
                             if r != ref})
            v("error", a, f"{ref} ({val}) bridge {a}-{b} is on "
              f"{board.label(ga)}, which reaches {', '.join(others[:4])}"
              f"{'...' if len(others) > 4 else ''}: the drawing's bridge "
              "joins two windings in series and connects to nothing else, "
              f"so the winding in use isn't the drawing's. {row['source']}",
              "xfmr_bridge_loaded", refs=[ref] + others[:4])

    comps, conflicts = x.used()
    for name in conflicts:
        w = next(w for w in row["windings"] if w["name"] == name)
        v("error", w["dot"], f"{ref} ({val}) winding {name} "
          f"({w['dot']}-{w['end']}) is joined against the dots of the "
          f"winding it shares a node with, so their turns cancel. {src}",
          "xfmr_winding_shorted")
    spans = []
    for c in comps:
        s = _span(c)
        if s is None:
            continue
        hi, lo, n = s
        spans.append((c, hi, lo, n))
        fact["windings"].append({
            "windings": c["windings"], "dot": _pins_txt(c, hi),
            "end": _pins_txt(c, lo), "turns": checklib.rnd(n, 3),
            "dot_net": board.label(hi), "end_net": board.label(lo)})

    for key, want in sorted((decl.get("turns") or {}).items()):
        pa, _, pb = str(key).partition("-")
        hit = None
        for c, hi, lo, n in spans:
            ends = set(c["ends"][hi]) | set(c["ends"][lo])
            if {pa, pb} <= ends and x.group(pa) != x.group(pb):
                hit = (c, n)
        used = "; ".join(f"{_pins_txt(c, hi)}-{_pins_txt(c, lo)} "
                         f"{_fmt(n)} turns" for c, hi, lo, n in spans)
        if hit is None:
            v("error", pa if pa in x.pins else next(iter(x.pins), pa),
              f"{ref} ({val}) constraints declare winding {key} with "
              f"{_fmt(want)} turns, but {pa} and {pb} aren't the two ends of "
              f"one wired winding (in use: {used or 'none'}). "
              f"{row['source']}", "xfmr_turns", winding=key)
        elif abs(hit[1] - float(want)) > 1e-6:
            v("error", pa, f"{ref} ({val}) winding {key} has {_fmt(hit[1])} "
              f"turns as wired, not the {_fmt(want)} constraints declare. "
              f"{row['source']}", "xfmr_turns", winding=key,
              turns=checklib.rnd(hit[1], 3))

    prim = next((s for s in spans if row["primary"] in s[0]["windings"]),
                None)
    if prim is None:
        fact["not_judged"] = (f"{ref}: its primary isn't wired at both ends, "
                              "so its phasing wasn't judged")
        return viols, fact
    pc, phi, plo, _ = prim
    sw = {g: switch_on(board, g) for g in (phi, plo)}
    if bool(sw[phi]) == bool(sw[plo]):
        fact["not_judged"] = (f"{ref}: no single switch found at the "
                              "primary's ends (a transistor drain or an IC "
                              "DRAIN/SW/LX pin), so its phasing wasn't judged")
        return viols, fact
    sw_dot = bool(sw[phi])
    sw_g = phi if sw_dot else plo
    fact["switch"] = {"pin": _pins_txt(pc, sw_g), "at": sw[sw_g],
                      "dotted": sw_dot}
    left = []
    for c, hi, lo, n in spans:
        if c is pc:
            continue
        label = f"{_pins_txt(c, hi)}-{_pins_txt(c, lo)}"
        found = set()
        diodes = []
        # The rectifier sits on the winding's switching end, the end with
        # fewer parts on it; the other end is a rail or return, where an
        # output TVS or an LED's diode would read the wrong way round.
        # Only on a tie (a bare test winding) are both ends read.
        size = {g: len(board.nodes.get(g, [])) for g in (hi, lo)}
        for g, other in ((hi, lo), (lo, hi)):
            if size[g] > size[other]:
                continue
            for d, side in rectifiers(board, g, {other}):
                anode_end = g if side == "A" else other
                found.add(anode_end)
                diodes.append(d)
        if len(found) != 1:
            left.append(f"{ref} {label}: "
                        + ("no diode rectifier at its ends (a synchronous "
                           "FET isn't judged)" if not found else
                           f"diodes {', '.join(sorted(set(diodes)))} face "
                           "both ways"))
            continue
        an_dot = found.pop() == hi
        wired = "flyback" if an_dot == sw_dot else "forward"
        fw = next(f for f in fact["windings"] if f["windings"] == c["windings"])
        fw.update(rectifier=sorted(set(diodes)), anode_dotted=an_dot,
                  wired=wired)
        if topo and wired != topo:
            conducts = "conducts" if topo == "flyback" else "blocks"
            ap = _pins_txt(c, hi if an_dot else lo)
            v("error", ap.split("/")[0],
              f"{ref} ({val}) is wired as a {wired}, not the {topo} "
              f"declared: the switch ({sw[sw_g]}) is on "
              f"{'dotted' if sw_dot else 'undotted'} primary pin "
              f"{_pins_txt(pc, sw_g)} and {', '.join(sorted(set(diodes)))}'s "
              f"anode end is {'dotted' if an_dot else 'undotted'} pin {ap} "
              f"of winding {label}, so it {conducts} in the on-time. Swap "
              "the primary's ends (or the winding's) to put the switch and "
              f"the anode on like-dotted pins. {src}", "xfmr_phase",
              refs=[ref] + sorted(set(diodes)), topology=topo, wired=wired)
    if left:
        fact["not_judged"] = "; ".join(left)
    return viols, fact


def check(pcb: Path, netlist: Path | None = None,
          constraints: dict | None = None, kb: dict | None = None):
    kb = load_kb() if kb is None else kb
    decl = _decl(constraints)
    netlist = netlist or find_netlist(pcb)
    if netlist is None:
        return [], {"netlist": None,
                    "nothing_to_judge": "no netlist beside the board"}
    bg = geom.load_board(pcb)
    board = Board(simlib.parse_netlist(netlist))
    viols, facts, left, unknown = [], [], [], []
    for ref in sorted(board.pins, key=lambda r: (prefix(r), len(r), r)):
        row = row_for(kb, board.value(ref))
        if row is None:
            if prefix(ref) == "T" and len(board.pins[ref]) >= 4:
                unknown.append(ref)
            continue
        vs, f = check_one(board, bg, Xfmr(board, ref, row),
                          decl.get(ref) or {})
        viols += vs
        facts.append(f)
        if f.get("not_judged"):
            left.append(f["not_judged"])
    for ref in unknown:
        left.append(f"{ref} ({board.value(ref)}): no row in "
                    "reference/transformers.yaml, so its windings weren't "
                    "judged")
    seen = {"netlist": str(netlist), "transformers": facts}
    if left:
        seen["not_judged"] = left
    if not facts:
        seen["nothing_to_judge"] = ("no transformer with a row in "
                                    "reference/transformers.yaml")
    return viols, seen


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--netlist")
    ap.add_argument("--constraints")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    nl = Path(args.netlist) if args.netlist else None
    if nl is not None and not nl.is_file():
        raise checklib.CheckError(f"netlist not found: {nl}")
    cons = (checklib.load_json(args.constraints, "constraints")
            if args.constraints else None)
    viols, seen = check(pcb, nl, cons)
    return checklib.report(SCRIPT, pcb, viols, checked=[seen],
                           nothing_to_judge=seen.get("nothing_to_judge"),
                           not_judged=seen.get("not_judged")), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
