"""netdir.py - which end of a net drives it (#605).

    pins = PinData(bg, si=si, parts_dir=D, netlist=N)
    driver_of(bg, net, pins, spec, power_nets)
        -> {pad "U5.12" | None, ref, pin, source, confidence, why}

First answer wins:

  declared    si.nets[net].driver: "U1", "U1.3"; "both" = declared two-way, no
              inference (a DQ line, or to overrule a guess below)
  oscillator  a supplied oscillator on the net, or behind a series R
  part_data   the extraction's pinout (<parts>/<LCSC>.json), then
              reference/pin_directions.yaml
  pin_name    the symbol's pin names, from the PCB's pads or, where the pads have
              none, the <board>.net beside it: *_TX DOUT SDO MCO CLKOUT drive,
              *_RX DIN SDI receive
  bus_master  a pin the bus master drives (SCLK BCK LRCK MCLK MOSI CS) on a
              peripheral, with one controller on the net: the controller. From
              si.buses [{master, nets}] the confidence is `declared`; inferred it
              is `low`, because firmware can make a codec the master
  series_r    the one real series resistor on the net, when an IC is behind it and
              the layout puts it by that IC (`low`: placement is only intent)
  driver_assumed  none of these

A pin decides only in one of two ways: it is the one output, or every other
candidate is an input and it is the only one left. So two GPIOs stay unknown, and
two pins both called TXD (a MAC and a PHY) are a conflict, not a guess. A muxed
name ("PA9/USART1_TX") says what the pin can do, not what it does: bidirectional.
A resistor in series counts as the pins behind it (one hop). Transistors and
passives are not candidates; a connector is one, with no direction.
"""
from __future__ import annotations

import fnmatch
import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
TABLE = REPO / "reference" / "pin_directions.yaml"
RESET_STATES = REPO / "reference" / "reset_states.yaml"

ACTIVE_REF = re.compile(r"^(U|IC|MOD|M|N|AR|Y|X|OSC)\d", re.I)
CONNECTOR_REF = re.compile(r"^(J|P|CN|CON)\d", re.I)
SERIES_REF = re.compile(r"^R\d")

# not on reset_states' mcu_like, but they master a bus the same way
FPGA_LIKE = r"^(LFE5|ICE40|ECP5|XC[67]|XCZU|EP4C|5C[EG]|GW[125]|LCMXO)"

OUT_NAME = re.compile(r"(^|_)(U?TXD?\d*|DOUT\d*|SD_?O(UT)?\d*|ASDOUT|DSDOUT|DO|MCO\d*|"
                      r"CLK_?OUT\d*|CLKO)(_|$)")
IN_NAME = re.compile(r"(^|_)(U?RXD?\d*|DIN\d*|SD_?I(N)?\d*|ASDIN|DSDIN|DI|CLK_?IN\d*)(_|$)")
MASTER_NAME = re.compile(r"^(.*_)?(SCK|SCLK|BCK|BCLK|LRCK|LRCLK|WS|WCLK|FSYNC|MCLK|MOSI|SIMO|"
                         r"COPI|CS\d*|CSN|NSS)$")
SLAVE_NAME = re.compile(r"^(.*_)?(MISO|SOMI|CIPO)$")
GPIO_NAME = re.compile(r"^(GPIO?\d+|IO\d+|P\d+[._]\d+|P[A-K]\d+|DQ\d*|[LU]?DQS.*|SDA\d*|SCL\d*)$")
PINTYPE_DIR = {"output": "out", "tristate": "out", "input": "in", "bidirectional": "bidir"}


def fn_names(fn: str, pin: str) -> list[str]:
    """'CFG2/SCL_2' on pin 2 -> ['CFG2', 'SCL'] (as sim_board.norm_fn)."""
    s = (fn or "").strip()
    if pin and s.endswith(f"_{pin}"):
        s = s[: -len(pin) - 1]
    s = s.replace("~{", "").replace("}", "")
    return [a.strip().upper() for a in s.split("/") if a.strip()]


def name_dir(names: list[str]) -> str | None:
    """The direction a pin's names say, or None if they say nothing."""
    if any(GPIO_NAME.match(n) for n in names):
        return "bidir"
    got = set()
    for n in names:
        if SLAVE_NAME.match(n):
            got.add("slave")
        elif MASTER_NAME.match(n):
            got.add("master")
        elif "CLK" in n and not re.search(r"CLK_?(OUT|IN|O)\d*(_|$)", n):
            continue                 # TX_CLK, RX_CLK: named for the data, not the way
        elif OUT_NAME.search(n):
            got.add("out")
        elif IN_NAME.search(n):
            got.add("in")
    if not got:
        return None
    return got.pop() if len(got) == 1 else "bidir"


def _table() -> tuple[list, str]:
    rows = (yaml.safe_load(TABLE.read_text(encoding="utf-8")) or {}).get("parts") or []
    for r in rows:
        r["_m"] = re.compile(r["match"], re.I)
        r["_p"] = re.compile(r["pins"])
        r["_n"] = re.compile(r["pin_numbers"]) if r.get("pin_numbers") else None
    like = (yaml.safe_load(RESET_STATES.read_text(encoding="utf-8")) or {}).get("mcu_like") or "$^"
    return rows, like


class PinData:
    """What a board says about its pins, loaded once."""

    def __init__(self, bg, si=None, parts_dir=None, netlist=None):
        self.bg = bg
        self.si = si or {}
        self.rows, like = _table()
        self.controller_re = re.compile(f"(?:{like})|(?:{FPGA_LIKE})", re.I)
        pcb = Path(bg.path)
        self.parts_dir = Path(parts_dir) if parts_dir else pcb.parent.parent / "parts"
        self.part_rows = {}
        pj = self.parts_dir / "parts.json"
        if pj.is_file():
            for row in json.loads(pj.read_text(encoding="utf-8")).get("parts") or []:
                for r in row.get("refdes") or []:
                    self.part_rows[r] = row
        self.extractions: dict = {}
        self.netlist_fn = self._load_netlist(Path(netlist) if netlist else pcb.with_suffix(".net"))

    @staticmethod
    def _load_netlist(path: Path) -> dict:
        """(ref, pin) -> (net, pin function). Empty when there is none."""
        if not path.is_file():
            return {}
        sys.path.insert(0, str(REPO / "scripts"))
        import netlist_audit
        try:
            parsed = netlist_audit.parse_netlist(path)
        except Exception as ex:  # a stale or foreign file must not stop the check
            print(f"netdir: {path}: {ex}", file=sys.stderr)
            return {}
        return {(nd["ref"], nd["pin"]): (net, nd.get("pinfunction") or "")
                for net, nodes in parsed["nets"].items() for nd in nodes}

    def _values(self, ref) -> list[str]:
        row = self.part_rows.get(ref) or {}
        fp = self.bg.footprints.get(ref) or {}
        return [v for v in (row.get("mpn"), row.get("value"), fp.get("value")) if v]

    def names(self, pad) -> list[str]:
        if pad.pinfunction:
            return fn_names(pad.pinfunction, pad.number)
        net, fn = self.netlist_fn.get((pad.ref, pad.number), (None, ""))
        return fn_names(fn, pad.number) if net == pad.net else []   # a stale netlist isn't evidence

    def _extraction(self, ref) -> dict:
        lcsc = (self.part_rows.get(ref) or {}).get("lcsc")
        if not lcsc:
            return {}
        if lcsc not in self.extractions:
            f = self.parts_dir / f"{lcsc}.json"
            pins = {}
            if f.is_file():
                try:
                    pins = {str(p.get("pin")): p.get("type")
                            for p in json.loads(f.read_text(encoding="utf-8")).get("pinout") or []}
                except (ValueError, AttributeError):
                    pins = {}
            self.extractions[lcsc] = pins
        return self.extractions[lcsc]

    def part_dir(self, pad) -> tuple[str | None, str | None]:
        """(direction, where from) by the part's own data."""
        t = self._extraction(pad.ref).get(pad.number)
        if t in PINTYPE_DIR:
            return PINTYPE_DIR[t], f"{(self.part_rows[pad.ref])['lcsc']}.json pin {pad.number}: {t}"
        names, values = self.names(pad), self._values(pad.ref)
        for r in self.rows:
            if not any(r["_m"].search(v) for v in values):
                continue
            hit = any(r["_p"].match(n) for n in names) if names else False
            if r["_n"] is not None and not names:
                hit = bool(r["_n"].match(pad.number))
            if hit:
                how = "" if r.get("verified") else ", unverified"
                return r["dir"], f"reference/pin_directions.yaml: {r['source']}{how}"
        return None, None

    def is_controller(self, ref) -> bool:
        return any(self.controller_re.search(v) for v in self._values(ref))

    def declared_master(self, net) -> str | None:
        for b in self.si.get("buses") or []:
            if b.get("master") and any(fnmatch.fnmatchcase(net, g) for g in b.get("nets") or []):
                return b["master"]
        return None


def _name(p) -> str:
    return f"{p.ref}.{p.number}"


def _answer(pad, source, confidence, why) -> dict:
    return {"pad": _name(pad), "ref": pad.ref, "pin": pad.number, "source": source,
            "confidence": confidence, "why": why}


def _unknown(why, **extra) -> dict:
    return {"pad": None, "source": "driver_assumed", "confidence": "none", "why": why, **extra}


def _candidates(bg, net, pins, power_nets) -> list[dict]:
    """[{pad, behind: [the pads that give it its direction]}]."""
    import check_si
    out = []
    for p in bg.pads_of(net):
        if ACTIVE_REF.match(p.ref) or CONNECTOR_REF.match(p.ref):
            if not any(c["pad"].ref == p.ref for c in out):
                out.append({"pad": p, "behind": [p]})
        elif SERIES_REF.match(p.ref):
            for s in check_si.series_resistors(bg, net, power_nets):
                if s["ref"] != p.ref:
                    continue
                behind = [q for q in bg.pads_of(s["other_net"])
                          if ACTIVE_REF.match(q.ref) or CONNECTOR_REF.match(q.ref)]
                if behind:
                    out.append({"pad": p, "behind": behind})
    return out


def _dir_of(behind, one) -> str | None:
    """A candidate's direction from the pins behind it (a series R has several)."""
    got = [one(q) for q in behind]
    if "out" in got:
        return "out"
    if got and all(d == "in" for d in got):
        return "in"
    return got[0] if len(got) == 1 else None


def _resolve(cands, dirs) -> tuple[dict | None, str]:
    """The module doc's two ways. (candidate or None, why not)."""
    outs = [c for c, d in zip(cands, dirs) if d == "out"]
    if len(outs) == 1:
        return outs[0], f"{_name(outs[0]['pad'])} is the only output"
    if len(outs) > 1:
        return None, "conflict: " + ", ".join(_name(c["pad"]) for c in outs) + " all read as outputs"
    rest = [c for c, d in zip(cands, dirs) if d != "in"]
    if len(rest) == 1 and len(cands) > 1:
        return rest[0], "every other pin on the net is an input"
    return None, ""


def _bus(cands, dirs, pins, net) -> dict | None:
    master = pins.declared_master(net)
    slaves = [c for c, d in zip(cands, dirs) if d == "slave" and c["pad"].ref != master
              and not pins.is_controller(c["pad"].ref)]
    if master:
        if len(slaves) == 1:
            return _answer(slaves[0]["pad"], "bus_master", "declared",
                           f"si.buses master {master}; its one slave drives this line")
        own = [c for c in cands if c["pad"].ref == master]
        if own:
            return _answer(own[0]["pad"], "bus_master", "declared", f"si.buses master {master}")
        return None
    ctl = [c for c in cands if pins.is_controller(c["pad"].ref)]
    if len(ctl) != 1:
        return None
    others = [(c, d) for c, d in zip(cands, dirs) if c is not ctl[0]]
    if len(slaves) == 1:
        return _answer(slaves[0]["pad"], "bus_master", "low",
                       f"{_name(slaves[0]['pad'])} is the slave's output on {ctl[0]['pad'].ref}'s bus")
    if (others and all(d in ("master", "in") for _, d in others)
            and any(d == "master" for _, d in others)):
        return _answer(ctl[0]["pad"], "bus_master", "low",
                       f"{ctl[0]['pad'].ref} is the one controller on a bus clock or select; "
                       "firmware may differ: declare si.buses or si.nets[net].driver")
    return None


def _series_is_source(bg, net, s, cands, dirs) -> bool:
    """A series R reads as the source only with an IC behind it, and either no IC
    on this net or a stub behind it short against this net (it sits by the IC
    behind it). A resistor feeding a connector says nothing of who drives."""
    mine = next((d for c, d in zip(cands, dirs) if c["pad"].ref == s["ref"]), None)
    behind = [q.ref for q in bg.pads_of(s["other_net"]) if ACTIVE_REF.match(q.ref)]
    if mine == "in" or not behind or any(
            CONNECTOR_REF.match(q.ref) for q in bg.pads_of(s["other_net"])):
        return False
    if not any(ACTIVE_REF.match(c["pad"].ref) for c in cands):
        return True

    def run(n):
        return sum(t.length for t in bg.tracks_of(n))
    return 2 * run(s["other_net"]) < run(net)


def driver_of(bg, net, pins: PinData, spec=None, power_nets=frozenset()) -> dict:
    """The module doc's rule."""
    import check_si
    spec = spec or {}
    want = spec.get("driver")
    if want == "both":
        return _unknown("declared two-way", declared_both=True)
    pads = bg.pads_of(net)
    for p in pads:
        if want and want in (p.ref, _name(p)):
            return _answer(p, "declared", "declared", "si.nets[net].driver")

    series = check_si.series_resistors(bg, net, power_nets)
    osc, _ = check_si.oscillator_parts(bg, net, power_nets)
    for p in pads:
        if p.ref in osc:
            return _answer(p, "oscillator", "high", f"{p.ref} is a supplied oscillator")
    for s in series:
        if any(q.ref in osc for q in bg.pads_of(s["other_net"])):
            p = next(q for q in pads if q.ref == s["ref"])
            return _answer(p, "oscillator", "high", f"an oscillator behind {s['ref']}")

    cands = _candidates(bg, net, pins, power_nets)

    def part_only(q):
        return pins.part_dir(q)[0]

    def part_or_name(q):
        return pins.part_dir(q)[0] or name_dir(pins.names(q))

    d_part = [_dir_of(c["behind"], part_only) for c in cands]
    hit, why = _resolve(cands, d_part)
    if hit:
        src = next((pins.part_dir(q)[1] for c in cands for q in c["behind"]
                    if pins.part_dir(q)[1]), "")
        return _answer(hit["pad"], "part_data", "high", f"{why} ({src})")
    conflict = why
    d_all = [_dir_of(c["behind"], part_or_name) for c in cands]
    hit, why = _resolve(cands, d_all)
    if hit:
        names = {_name(q): pins.names(q) for c in cands for q in c["behind"]}
        return _answer(hit["pad"], "pin_name", "medium", f"{why} (pins {names})")
    conflict = conflict or why
    if not conflict:
        got = _bus(cands, d_all, pins, net)
        if got:
            return got
    real = [s for s in series if s["ohm"] is None or s["ohm"] > 1.0]
    if not conflict and len(real) == 1 and _series_is_source(bg, net, real[0], cands, d_all):
        p = next(q for q in pads if q.ref == real[0]["ref"])
        return _answer(p, "series_r", "low",
                       f"{p.ref} is the net's one series resistor, with the IC behind it")
    extra = {"declared_not_on_net": want} if want else {}
    return _unknown(conflict or "no pin on the net says which way it points", **extra)
