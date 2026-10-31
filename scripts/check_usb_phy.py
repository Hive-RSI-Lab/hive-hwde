"""check_usb_phy.py - a USB device's D+/D- carry what its own PHY wants, and
a USB-C sink's CC pins see one 5.1 k Rd each (P8 verify suite).

The hit: the "22-27 ohm on D+/D- always" rule (digital-si-rf ingestion,
task 261031-011721-10951e662f, SIRF-10/11) is folklore. USB 2.0's 28-44 ohm
driver impedance includes any series R. ST's and WCH's PHYs embed it, so a
series R pushes them out of the window. NXP's LPC and the RP2040 don't, so
they need their vendor's 33 or 27 ohm. The D+ pull-up is just as
PHY-specific: an STM32F103 enumerates only with an external 1.5 k (the 10 k
on Blue Pill clones fails), and an extra pull-up on a CH340 halves its own.
On USB-C, Rd is 5.1 k +/-20% total, internal included (Type-C R2.5 Table
4-28), so a CH224K with an external 5.1 k fails as surely as a bare
receptacle with none. ERC sees resistors either way. Each part's needs are
in reference/usb_phys.yaml, row by row from the vendor's own document.

How the lines are read: from each connector pin named D+/DP/DP1/D-/DM/DN...,
the net is walked through 0 R, ferrites and inductors (joined), and through
any resistor of series_max_ohm (100) or less (a series element). A larger
resistor is a pull: to a rail (its volts from the net name), or to a GPIO
or a transistor (switched). The first IC reached is the PHY, unless it is
in not_phy (an ESD array, a charger sink watching DP/DM). CC is read the
same way from pins named CC/CC1/CC2: a pull to GND is an Rd, to a positive
rail an Rp (a source or DRP port, not checked), and a known sink controller
adds its own Rd behind any series resistor.

Findings:
  usb_series_r_unwanted  error: a series resistor between the connector and
                         a PHY whose vendor says to connect directly
  usb_series_r_missing   error: no series resistor on a PHY that needs one
  usb_series_r_value     warning: a needed series resistor more than 10%
                         off the vendor's value
  usb_dp_pullup_missing  error: a PHY with no internal pull-up and no
                         pull-up on D+ (the host never sees the device)
  usb_dp_pullup_value    error: the D+ pull-up isn't 1.5 k +/-5%
  usb_dp_pullup_rail     error: the D+ pull-up goes to a rail outside
                         3.0-3.6 V (VBUS, 5 V)
  usb_dp_pullup_extra    warning: an external D+ pull-up beside the PHY's
                         own (the two in parallel)
  usb_dm_pullup          error: a pull-up on D- (a low-speed device's mark)
  usb_phy_unknown        warning: an IC on D+/D- the file doesn't know
  usb_c_orientation      error: a USB-C receptacle's two D+ (or D-) pins
                         aren't joined, so the device works one way up only
  usb_cc_rd_missing      error: a sink's CC pin with no Rd at all (no VBUS
                         from a C-to-C cable)
  usb_cc_rd_value        error: CC's total Rd outside 5.1 k +/-20% (two Rd
                         in parallel, or a wrong value)
  usb_cc_rd_tolerance    warning: CC's Rd inside +/-20% but outside +/-10%
                         (attaches, but can't read the source's current)
  usb_cc_unknown         warning: an IC on CC the file doesn't know, so its
                         internal Rd can't be counted

The netlist is the board's own `<board>.net` beside the .kicad_pcb, or
--netlist. A board with no netlist passes with nothing checked.

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
from check_straps import (CONNECTOR_PREFIXES, PASSIVE_PREFIXES, Board,  # noqa: E402
                          find_netlist, pad_pos, prefix)

SCRIPT = "check_usb_phy"
SOURCE = SCRIPT
KB_YAML = HERE.parent / "reference" / "usb_phys.yaml"
DP_RE = re.compile(r"^(D\+|DP[12]?|D_P|USB_?D\+|USB_?DP)$")
DM_RE = re.compile(r"^(D-|D[MN][12]?|D_[MN]|USB_?D-|USB_?D[MN])$")
CC_RE = re.compile(r"^CC[12]?$")
SWITCH_PREFIXES = ("Q", "U", "IC")
MAX_DEPTH = 3
DM_PULLUP_MAX_OHM = 10000   # a pull-up this strong on D- marks low speed
USB_SRC = ("USB 2.0 Pull-up/pull-down resistors ECN, section 7.1.5.1: "
           "1.5 kohm +/-5% to 3.0-3.6 V")
TYPEC_SRC = ("USB Type-C R2.5 Table 4-28: Rd 5.1 kohm +/-20% to attach, "
             "+/-10% to read the source's current, the total into the CC "
             "pin including internal resistance")


def load_kb(path: Path = KB_YAML) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    for key in ("phys", "cc_sinks"):
        for row in doc.get(key) or []:
            row["_match"] = re.compile(row["match"], re.I)
            row["_pins"] = re.compile(row["pins"], re.I) \
                if row.get("pins") else None
    doc["_not_phy"] = re.compile(doc.get("not_phy") or "$^", re.I)
    return doc


def row_for(rows, value: str):
    for row in rows or []:
        if row["_match"].search(value or ""):
            return row
    return None


def is_ic(board: Board, ref: str) -> bool:
    p = prefix(ref)
    return not (p in PASSIVE_PREFIXES or p in CONNECTOR_PREFIXES or p == "R"
                or ref in board.shorts)


def walk(board: Board, g0: str, series_max: float) -> dict:
    """What hangs on a signal from net group g0: ICs with the series
    resistors between (ics), pulls (pulls) and unparsed resistors."""
    out = {"ics": [], "pulls": [], "unparsed": []}
    seen = set()

    def visit(g, path, depth):
        seen.add(g)
        for ref, pin in board.nodes.get(g, []):
            if prefix(ref) in CONNECTOR_PREFIXES:
                continue
            if prefix(ref) == "R" and len(board.pins[ref]) == 2:
                if ref in {r for r, _ in path}:
                    continue
                r = engcalc.parse_value(board.value(ref))
                if r is None:
                    out["unparsed"].append(ref)
                    continue
                other = [q for q in board.pins[ref] if q != pin][0]
                og = board.net(ref, other)
                if og in seen or board.unconnected(og):
                    continue
                v = board.rail(og)
                ohm = r + sum(x for _, x in path)
                if r <= series_max and v is None:
                    if depth < MAX_DEPTH:
                        visit(og, path + [(ref, r)], depth + 1)
                    continue
                switched = v is None and any(
                    prefix(x) in SWITCH_PREFIXES
                    for x, _ in board.nodes.get(og, []))
                out["pulls"].append({"ref": ref, "ohm": ohm, "volts": v,
                                     "to": board.label(og),
                                     "switched": switched,
                                     "path": [x for x, _ in path]})
                continue
            if is_ic(board, ref):
                out["ics"].append({"ref": ref, "pin": pin,
                                   "name": board.pins[ref][pin]["name"],
                                   "path": list(path)})
    visit(g0, [], 0)
    return out


def parallel(ohms) -> float | None:
    ohms = [o for o in ohms if o is not None]
    if not ohms:
        return None
    if any(o <= 0 for o in ohms):
        return 0.0
    return 1.0 / sum(1.0 / o for o in ohms)


def kohm(o: float) -> str:
    return f"{o / 1000:.3g} k" if o >= 1000 else f"{o:.3g} ohm"


def connectors(board: Board) -> dict:
    """{ref: {"dp": [pins], "dm": [pins], "cc": [pins]}} for every
    connector with USB-named pins."""
    out = {}
    for ref, pins in board.pins.items():
        if prefix(ref) not in CONNECTOR_PREFIXES:
            continue
        c = {"dp": [], "dm": [], "cc": []}
        for pin, meta in pins.items():
            for key, rx in (("dp", DP_RE), ("dm", DM_RE), ("cc", CC_RE)):
                if rx.match(meta["name"]):
                    c[key].append(pin)
        if c["dp"] or c["dm"] or c["cc"]:
            out[ref] = c
    return out


def check_orientation(board, bg, ref, c) -> list:
    viols = []
    for key, what in (("dp", "D+"), ("dm", "D-")):
        if len(c[key]) < 2:
            continue
        groups = {p: board.net(ref, p) for p in c[key]}
        if len(set(groups.values())) < 2:
            continue
        open_ = [p for p, g in groups.items() if board.unconnected(g)
                 or all(r == ref for r, _ in board.nodes.get(g, []))]
        if not open_:
            continue
        viols.append(checklib.violation(
            SCRIPT, "error", pad_pos(bg, ref, open_[0]), None,
            board.label(groups[open_[0]]), [ref],
            f"{ref}'s {what} pins {', '.join(sorted(c[key]))} aren't joined: "
            f"{', '.join(sorted(open_))} goes nowhere, so a USB-C plug works "
            f"only one way up. Tie both {what} pins to the same net at the "
            "receptacle", SOURCE, kind="usb_c_orientation"))
    return viols


def check_phy(board, bg, kb, phy, row, dp, dm) -> list:
    """dp/dm: the walk entries {ref, pin, name, path} for this PHY's two
    lines (dm may be None)."""
    bus = kb["usb_bus"]
    viols = []
    ref, value = phy, board.value(phy)
    src = row["source"].split(". ")[0].strip()
    lines = [("D+", dp), ("D-", dm)]
    for what, ent in lines:
        if ent is None:
            continue
        path = ent["path"]
        pos = pad_pos(bg, ref, ent["pin"])
        net = board.label(board.net(ref, ent["pin"]))
        if row["series_r"] == "none" and path:
            rs = [r for r, _ in path]
            viols.append(checklib.violation(
                SCRIPT, "error", pad_pos(bg, rs[0], "1") or pos, None, net,
                [ref] + rs,
                f"{ref} ({value}) {what} has {', '.join(rs)} "
                f"({' + '.join(kohm(o) for _, o in path)}) in series, but "
                f"{row['name']} embeds its driver impedance and wants its pin "
                "straight on the bus: the extra R pushes the driver out of "
                "USB 2.0's 28-44 ohm window. Replace it with 0 R or a "
                f"track ({src})", SOURCE, kind="usb_series_r_unwanted",
                line=what, ohm=sum(o for _, o in path)))
        if row["series_r"] == "required":
            want = float(row["ohm"])
            have = sum(o for _, o in path)
            if not path:
                viols.append(checklib.violation(
                    SCRIPT, "error", pos, None, net, [ref],
                    f"{ref} ({value}) {what} goes to the connector with no "
                    f"series resistor, but {row['name']} needs "
                    f"{want:g} ohm on each line, close to the chip, to meet "
                    f"USB 2.0's driver impedance ({src})", SOURCE,
                    kind="usb_series_r_missing", line=what, want_ohm=want))
            elif abs(have - want) > bus["series_tol"] * want:
                rs = [r for r, _ in path]
                viols.append(checklib.violation(
                    SCRIPT, "warning", pad_pos(bg, rs[0], "1") or pos, None,
                    net, [ref] + rs,
                    f"{ref} ({value}) {what} has {have:g} ohm in series "
                    f"({', '.join(rs)}); {row['name']} wants {want:g} ohm "
                    f"({src})", SOURCE, kind="usb_series_r_value", line=what,
                    ohm=have, want_ohm=want))
    return viols


def pullups(w: dict) -> list:
    return [p for p in w["pulls"] if p["volts"] is None or p["volts"] > 0]


def check_pullup(board, bg, kb, phy, row, dp, dpw, dmw) -> list:
    bus = kb["usb_bus"]
    viols = []
    value = board.value(phy)
    pos = pad_pos(bg, phy, dp["pin"])
    net = board.label(board.net(phy, dp["pin"]))
    ups = pullups(dpw)
    lo, hi = bus["dp_pullup_ohm"]
    vlo, vhi = bus["dp_pullup_rail_v"]
    if row["dp_pullup"] == "external":
        if not ups:
            viols.append(checklib.violation(
                SCRIPT, "error", pos, None, net, [phy],
                f"{phy} ({value}) has no pull-up on D+ ({net}), and "
                f"{row['name']} has none inside: the host never sees the "
                f"device. Fit 1.5 k +/-5% from D+ to the 3.3 V rail (or "
                "GPIO-switched, self-powered). Sources: "
                f"{row['source'].split('. ')[0].strip()}; {USB_SRC}",
                SOURCE, kind="usb_dp_pullup_missing"))
            return viols
        refs = [phy] + [p["ref"] for p in ups]
        r = parallel([p["ohm"] for p in ups])
        if r is not None and not lo <= r <= hi:
            viols.append(checklib.violation(
                SCRIPT, "error", pad_pos(bg, ups[0]["ref"], "1") or pos,
                None, net, refs,
                f"{phy} ({value}) D+ pull-up is {kohm(r)} "
                f"({', '.join(p['ref'] for p in ups)}), not 1.5 k +/-5% "
                f"({lo:g}-{hi:g} ohm): too weak, it misses the 2.5 us reset "
                "relaxation to VIH and the host may not enumerate it (the "
                f"10 k on Blue Pill clones). {USB_SRC}", SOURCE,
                kind="usb_dp_pullup_value", ohm=checklib.rnd(r, 1)))
        for p in ups:
            if p["volts"] is not None and not vlo <= p["volts"] <= vhi:
                viols.append(checklib.violation(
                    SCRIPT, "error", pad_pos(bg, p["ref"], "1") or pos,
                    None, net, [phy, p["ref"]],
                    f"{phy} ({value}) D+ pull-up {p['ref']} goes to "
                    f"{p['to']} ({p['volts']:g} V), outside 3.0-3.6 V: the "
                    "idle D+ sits above the transceiver's rail and "
                    f"VIHZ's 3.6 V. Tie it to the 3.3 V rail. {USB_SRC}",
                    SOURCE, kind="usb_dp_pullup_rail", volts=p["volts"]))
    elif ups:
        viols.append(checklib.violation(
            SCRIPT, "warning", pad_pos(bg, ups[0]["ref"], "1") or pos, None,
            net, [phy] + [p["ref"] for p in ups],
            f"{phy} ({value}) has its own D+ pull-up, and "
            f"{', '.join(p['ref'] for p in ups)} adds another "
            f"({kohm(parallel([p['ohm'] for p in ups]))}): the two in "
            "parallel can drop the termination under 900 ohm. Remove the "
            f"external one ({row['source'].split('. ')[0].strip()})",
            SOURCE, kind="usb_dp_pullup_extra"))
    if dmw is not None:
        for p in pullups(dmw):
            if p["ohm"] <= DM_PULLUP_MAX_OHM:
                viols.append(checklib.violation(
                    SCRIPT, "error", pad_pos(bg, p["ref"], "1"), None,
                    p["to"], [phy, p["ref"]],
                    f"{phy} ({value}) D- has a pull-up ({p['ref']}, "
                    f"{kohm(p['ohm'])} to {p['to']}): that marks a "
                    "low-speed device, and this is a full-speed one. "
                    f"Remove it. {USB_SRC}", SOURCE, kind="usb_dm_pullup"))
    return viols


def check_data(board, bg, kb, conns) -> tuple[list, list]:
    bus = kb["usb_bus"]
    viols, facts = [], []
    done, unknown = set(), set()
    for cref, c in sorted(conns.items()):
        dps = {board.net(cref, p) for p in c["dp"]} - done
        dms = {board.net(cref, p) for p in c["dm"]} - done
        dps = {g for g in dps if not board.unconnected(g)}
        dms = {g for g in dms if not board.unconnected(g)}
        done |= dps | dms
        for gdp in sorted(dps):
            dpw = walk(board, gdp, bus["series_max_ohm"])
            dmws = [walk(board, g, bus["series_max_ohm"]) for g in sorted(dms)]
            for ent in dpw["ics"]:
                phy, value = ent["ref"], board.value(ent["ref"])
                if kb["_not_phy"].search(value):
                    continue
                row = row_for(kb.get("phys"), value)
                if row is None:
                    if phy not in unknown:
                        unknown.add(phy)
                        viols.append(checklib.violation(
                            SCRIPT, "warning", pad_pos(bg, phy, ent["pin"]),
                            None, board.label(gdp), [phy],
                            f"{phy} ({value}) is on {cref}'s D+ but "
                            "reference/usb_phys.yaml doesn't know it, so its "
                            "series resistors and D+ pull-up weren't "
                            "checked: add its row from its datasheet (or "
                            "its pattern to not_phy for an ESD part)",
                            SOURCE, kind="usb_phy_unknown"))
                    continue
                if row["_pins"] is not None \
                        and not row["_pins"].match(ent["name"]):
                    facts.append({"connector": cref, "phy": phy,
                                  "skipped": f"pin {ent['name']} isn't "
                                             f"{row['name']}"})
                    continue
                dm, dmw = None, None
                for w in dmws:
                    for e in w["ics"]:
                        if e["ref"] == phy:
                            dm, dmw = e, w
                            break
                    if dm:
                        break
                viols += check_phy(board, bg, kb, phy, row, ent, dm)
                viols += check_pullup(board, bg, kb, phy, row, ent, dpw, dmw)
                facts.append({
                    "connector": cref, "phy": phy, "value": value,
                    "row": row["name"],
                    "series": {"D+": [r for r, _ in ent["path"]],
                               "D-": [r for r, _ in dm["path"]]
                               if dm else None},
                    "dp_pullups": [p["ref"] for p in pullups(dpw)]})
    return viols, facts


def check_cc(board, bg, kb, cref, c) -> tuple[list, list]:
    bus = kb["usb_bus"]
    nominal = bus["cc_rd_ohm"]
    t20, t10 = bus["cc_rd_tol_attach"], bus["cc_rd_tol_current"]
    viols, facts = [], []
    reads = []
    for pin in sorted(c["cc"]):
        g = board.net(cref, pin)
        name = board.pins[cref][pin]["name"]
        w = walk(board, g, bus["series_max_ohm"]) \
            if not board.unconnected(g) else {"ics": [], "pulls": [],
                                              "unparsed": []}
        rp = [p for p in w["pulls"] if p["volts"] is None or p["volts"] > 0]
        rd = [p for p in w["pulls"] if p["volts"] == 0]
        inside, unknown = [], []
        for ent in w["ics"]:
            row = row_for(kb.get("cc_sinks"), board.value(ent["ref"]))
            if row is None:
                unknown.append(ent)
            elif row["rd"] == "internal":
                inside.append((ent, row))
        reads.append((pin, name, g, w, rp, rd, inside, unknown))
    if any(r[4] for r in reads):
        return [], [{"connector": cref, "role": "source or DRP (Rp on CC)"}]
    for pin, name, g, w, rp, rd, inside, unknown in reads:
        net = board.label(g)
        pos = pad_pos(bg, cref, pin)
        paths = [p["ohm"] for p in rd] + [
            float(row["rd_ohm"]) + sum(o for _, o in ent["path"])
            for ent, row in inside]
        total = parallel(paths)
        refs = [cref] + [p["ref"] for p in rd] + [e["ref"] for e, _ in inside]
        facts.append({"connector": cref, "pin": name, "net": net,
                      "rd": [p["ref"] for p in rd],
                      "internal": [e["ref"] for e, _ in inside],
                      "unknown": [e["ref"] for e in unknown],
                      "total_ohm": checklib.rnd(total, 1)
                      if total is not None else None})
        if w["unparsed"]:
            continue
        if unknown:
            u = unknown[0]
            viols.append(checklib.violation(
                SCRIPT, "warning", pad_pos(bg, u["ref"], u["pin"]) or pos,
                None, net, [cref, u["ref"]],
                f"{cref} {name} reaches {u['ref']} "
                f"({board.value(u['ref'])}), which reference/usb_phys.yaml "
                "doesn't know, so whether it holds an Rd inside (and "
                "whether an external 5.1 k doubles it) wasn't checked: add "
                "its cc_sinks row from its datasheet", SOURCE,
                kind="usb_cc_unknown", pin=name))
            continue
        if total is None:
            viols.append(checklib.violation(
                SCRIPT, "error", pos, None, net, [cref],
                f"{cref} {name} ({net}) has no Rd: no resistor to GND and no "
                "sink controller with one inside, so a USB-C source never "
                "sees a sink and keeps VBUS off. Fit 5.1 k +/-10% from "
                f"{name} to GND. {TYPEC_SRC}", SOURCE,
                kind="usb_cc_rd_missing", pin=name))
            continue
        off = abs(total - nominal) / nominal
        many = len(paths) > 1
        why = (f"{len(paths)} Rd in parallel: "
               + ", ".join([f"{p['ref']} {kohm(p['ohm'])}" for p in rd]
                           + [f"{e['ref']} ({r['name']}) inside"
                              for e, r in inside])) if many else \
            f"{(rd[0]['ref'] if rd else inside[0][0]['ref'])}"
        if off > t20:
            viols.append(checklib.violation(
                SCRIPT, "error", pos, None, net, refs,
                f"{cref} {name} ({net}) sees {kohm(total)} to GND ({why}), "
                "outside 5.1 k +/-20%: the source can't read a sink"
                + (", so remove the external Rd beside the controller's own"
                   if many else "") + f". {TYPEC_SRC}"
                + (f"; {inside[0][1]['source'].split('. ')[0].strip()}"
                   if inside else ""),
                SOURCE, kind="usb_cc_rd_value", pin=name,
                ohm=checklib.rnd(total, 1)))
        elif off > t10:
            viols.append(checklib.violation(
                SCRIPT, "warning", pos, None, net, refs,
                f"{cref} {name} ({net}) sees {kohm(total)} to GND ({why}): "
                "inside +/-20%, so it attaches, but outside +/-10%, so the "
                "sink can't tell 1.5 A or 3 A from default power. Use a 1% "
                f"5.1 k. {TYPEC_SRC}", SOURCE, kind="usb_cc_rd_tolerance",
                pin=name, ohm=checklib.rnd(total, 1)))
    return viols, facts


def check(pcb: Path, netlist: Path | None = None, kb: dict | None = None):
    kb = load_kb() if kb is None else kb
    netlist = netlist or find_netlist(pcb)
    if netlist is None:
        return [], {"netlist": None}
    bg = geom.load_board(pcb)
    board = Board(simlib.parse_netlist(netlist))
    conns = connectors(board)
    viols, cc_facts = [], []
    for cref, c in sorted(conns.items()):
        viols += check_orientation(board, bg, cref, c)
        if c["cc"]:
            v, f = check_cc(board, bg, kb, cref, c)
            viols += v
            cc_facts += f
    v, data = check_data(board, bg, kb, conns)
    viols += v
    return viols, {"netlist": str(netlist), "connectors": sorted(conns),
                   "phys": data, "cc": cc_facts}


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
