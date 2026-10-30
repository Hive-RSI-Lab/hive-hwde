"""doubtlib.py - the board a doubt check asks its question of, and the shape
of its answer (reference/doubt.md).

A doubt is one question about one board: "what could make this board fail
here?", answered by a check script an agent wrote for it. The check is a
Python file with

    QUESTION = "Does every polarised cap see its + pad at the higher rail?"
    DOMAIN = "power"            # power | analog | digital | thermal | dfm |
                                # parts | mech | emc | fw | safety
    WHY = "a reversed electrolytic vents at power-on"   # how the board fails
    def answer(board) -> dict   # doubtlib.passed / failed / unknown
    def applies(board) -> bool  # library checks only: does the question
                                # arise on this board at all

`answer` gets a `Board`: the netlist (nets joined through 0 R, inductors,
ferrites, net ties and closed jumpers), each part's pins by name, the rail
voltages the net names carry plus the ones the ledger records
(`doubt.py fact --rail NET=V`), parts.json rows by refdes, and the paths of
the .kicad_pcb and .net for checks that read geometry themselves.

An answer is a verdict with what it rests on:
    pass     the board is right here, and `margin` says by how much
    fail     the board fails here; `findings` say where and why
    unknown  the question can't be settled from the files: `unknowns` name
             the fact that would settle it (a datasheet VIH, a load current)
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import checklib  # noqa: E402
import engcalc  # noqa: E402
import simlib  # noqa: E402
from check_ratings import GROUND_RE, base_net, volts_from_name  # noqa: E402

VERDICTS = ("pass", "fail", "unknown")
DOMAINS = ("power", "analog", "digital", "thermal", "dfm", "parts", "mech",
           "emc", "fw", "safety", "test")

# Parts that join their two nets for a DC question: 0 R links, inductors,
# ferrites, net ties, jumpers and fuses.
_JOIN_PREFIXES = {"L", "FB", "NT", "JP", "F"}
_NC_RE = re.compile(r"^(unconnected-|NC_|no_connect)", re.I)
# Supply pin names, most logic-like first: an IC's I/O swings to the first
# of these it has.
SUPPLY_ORDER = ("VIO", "VDDIO", "IOVDD", "OVDD", "VL", "DVDD", "VDD", "VCC",
                "V+", "VDD3P3", "3V3", "AVDD", "VS", "VIN", "PVDD")
_SUPPLY_RE = re.compile(r"^(VIO|VDDIO|IOVDD|OVDD|VL|DVDD|VDD|VCC|V\+|VDD3P3"
                        r"|3V3|AVDD|VS|VIN|PVDD|GVDD|VBAT|VREG)(\d*|_.*)$")


def pin_name(pinfunction: str, pin: str) -> str:
    """'IO0_25' -> 'IO0', '~{INT}_1' -> 'INT', 'GPIO12' -> 'IO12'."""
    name = (pinfunction or "").strip()
    if pin and name.endswith(f"_{pin}"):
        name = name[: -len(pin) - 1]
    name = re.sub(r"~\{([^}]*)\}", r"\1", name).upper()
    return re.sub(r"^GPIO(\d+)$", r"IO\1", name) or str(pin)


def prefix(ref: str) -> str:
    m = re.match(r"^[A-Za-z]+", ref or "")
    return m.group(0).upper() if m else ""


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Board:
    """One board's netlist, joined for DC, with its rails and parts."""

    def __init__(self, netlist: Path, pcb: Path | None = None,
                 parts: dict | None = None, rails: dict | None = None,
                 name: str | None = None):
        self.netlist = Path(netlist)
        self.pcb = Path(pcb) if pcb else None
        self.name = name or self.netlist.stem
        parsed = simlib.parse_netlist(self.netlist)
        self.comps: dict[str, dict] = parsed["components"]
        self.nets: dict[str, list[tuple[str, str]]] = {}
        self.pins: dict[str, dict[str, dict]] = {}
        for net, nodes in parsed["nets"].items():
            for nd in nodes:
                ref, pin = nd["ref"], nd["pin"]
                self.nets.setdefault(net, []).append((ref, pin))
                self.pins.setdefault(ref, {})[pin] = {
                    "net": net, "name": pin_name(nd.get("pinfunction"), pin)}
        self.parts = parts or {}
        self.facts_rails = {k: float(v) for k, v in (rails or {}).items()}
        parent = {n: n for n in self.nets}

        def find(n):
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n
        self.joins: set[str] = set()
        for ref, pins in self.pins.items():
            if len(pins) == 2 and self.is_join(ref):
                a, b = (p["net"] for p in pins.values())
                parent[find(a)] = find(b)
                self.joins.add(ref)
        self._group = {n: find(n) for n in parent}
        self.groups: dict[str, list[str]] = {}
        for n in self.nets:
            self.groups.setdefault(self._group[n], []).append(n)

    @classmethod
    def from_workspace(cls, ws: Path, rails: dict | None = None) -> "Board":
        """The board in a workspace (boards/<b>): kicad/<b>.net, its
        .kicad_pcb and parts/parts.json, whichever exist."""
        ws = Path(ws)
        net = find_netlist(ws)
        pcb = net.with_suffix(".kicad_pcb")
        parts = {}
        pj = ws / "parts" / "parts.json"
        if pj.is_file():
            data = checklib.load_json(pj, "parts.json")
            rows = data.get("parts", data) if isinstance(data, dict) else data
            rows = rows.values() if isinstance(rows, dict) else rows
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                refs = row.get("refdes") or []
                for ref in [refs] if isinstance(refs, str) else refs:
                    parts[ref] = row
        return cls(net, pcb if pcb.is_file() else None, parts, rails,
                   name=ws.name)

    # -- parts
    def value(self, ref: str) -> str:
        return (self.comps.get(ref) or {}).get("value") or ""

    def footprint(self, ref: str) -> str:
        return (self.comps.get(ref) or {}).get("footprint") or ""

    def mpn(self, ref: str) -> str:
        row = self.parts.get(ref) or {}
        return row.get("mpn") or self.value(ref)

    def rated_volts(self, ref: str) -> float | None:
        """A part's voltage rating from its parts.json row (the distributor's
        "Voltage Rating" attribute, then its description) or the value text
        ("470uF 35V"); None when none says."""
        row = self.parts.get(ref) or {}
        for att in row.get("attributes") or []:
            if isinstance(att, dict) and re.match(r"voltage.?rat",
                                                  str(att.get("name")), re.I):
                v = engcalc.parse_volts(str(att.get("value")))
                if v is not None:
                    return v
        for text in (row.get("description"), row.get("value"),
                     self.value(ref)):
            m = re.search(r"(?<![\w.])(\d+(?:\.\d+)?)\s*V(?![\w])", text or "")
            if m:
                return float(m.group(1))
        return None

    def is_join(self, ref: str) -> bool:
        p = prefix(ref)
        if p in _JOIN_PREFIXES:
            return True
        return p == "R" and engcalc.parse_value(self.value(ref)) == 0

    def ics(self) -> list[str]:
        return sorted(r for r in self.pins if prefix(r) in ("U", "IC"))

    def refs(self, *prefixes: str) -> list[str]:
        want = {p.upper() for p in prefixes}
        return sorted(r for r in self.pins if prefix(r) in want)

    # -- nets
    def group(self, net: str) -> str:
        return self._group.get(net, net)

    def net_of(self, ref: str, pin: str) -> str:
        return self.pins[ref][pin]["net"]

    def names(self, net: str) -> list[str]:
        return self.groups.get(self.group(net), [net])

    def label(self, net: str) -> str:
        return sorted(self.names(net), key=lambda n: (len(n), n))[0]

    def nodes(self, net: str) -> list[tuple[str, str]]:
        """Every (ref, pin) on the net's group, the joining parts left out."""
        out = []
        for n in self.names(net):
            out += [(r, p) for r, p in self.nets.get(n, [])
                    if r not in self.joins]
        return out

    def is_unconnected(self, net: str) -> bool:
        return all(_NC_RE.match(n) for n in self.names(net)) \
            or len(self.nodes(net)) <= 1

    def is_ground(self, net: str) -> bool:
        return any(GROUND_RE.match(base_net(n)) for n in self.names(net))

    def rail(self, net: str) -> float | None:
        """The net's DC voltage: a ledger fact first, then its name."""
        for n in self.names(net):
            if n in self.facts_rails or base_net(n) in self.facts_rails:
                return self.facts_rails.get(n, self.facts_rails.get(base_net(n)))
        if self.is_ground(net):
            return 0.0
        for n in self.names(net):
            v = volts_from_name(n)
            if v is not None:
                return v
        return None

    def supply(self, ref: str) -> tuple[str, float] | None:
        """(pin name, volts) of the supply an IC's logic swings to: its
        first supply pin in SUPPLY_ORDER with a known rail."""
        found = {}
        for meta in self.pins.get(ref, {}).values():
            m = _SUPPLY_RE.match(meta["name"])
            if not m:
                continue
            v = self.rail(meta["net"])
            if v is not None and v > 0:
                found.setdefault(m.group(1), (meta["name"], v))
        for key in SUPPLY_ORDER:
            if key in found:
                return found[key]
        return next(iter(found.values()), None)

    def is_supply_pin(self, name: str) -> bool:
        return bool(_SUPPLY_RE.match(name) or GROUND_RE.match(name)
                    or re.match(r"^(EP|EPAD|PAD|THERMAL|TAB|SH|SHIELD)\d*$",
                                name))


# -- answers

def _answer(verdict: str, evidence, margin, findings, unknowns) -> dict:
    return {"verdict": verdict,
            "evidence": [evidence] if isinstance(evidence, str) else
            list(evidence or []),
            "margin": margin, "findings": list(findings or []),
            "unknowns": list(unknowns or [])}


def passed(evidence, margin: str | None = None) -> dict:
    """The board is right here; `margin` says by how much."""
    return _answer("pass", evidence, margin, [], [])


def failed(findings, evidence=None, margin: str | None = None) -> dict:
    """The board fails here: each finding a string or {refs, nets, msg}."""
    return _answer("fail", evidence, margin, findings, [])


def unknown(unknowns, evidence=None, findings=None) -> dict:
    """The files can't settle it: `unknowns` name the facts that would."""
    return _answer("unknown", evidence, None, findings, unknowns)


def combine(fails: list, unknowns: list, evidence: list,
            margin: str | None = None) -> dict:
    """The usual end of a check that looks at many places: any fail fails,
    else any unknown is unknown, else pass."""
    if fails:
        return failed(fails, evidence, margin)
    if unknowns:
        return unknown(unknowns, evidence)
    return passed(evidence, margin)


# -- running a check

def load_check(path: Path):
    """Import a check file as a module, and say what it lacks."""
    path = Path(path)
    name = "doubt_" + re.sub(r"\W", "_", str(path.resolve()))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in ("QUESTION", "answer"):
        if not hasattr(mod, attr):
            raise checklib.CheckError(f"{path}: no {attr}")
    return mod


def run_check(path: Path, board: Board) -> dict:
    """Run one check on a board. A check that raises answers `error`, with
    its traceback, never a pass."""
    try:
        mod = load_check(path)
        ans = mod.answer(board)
        if not isinstance(ans, dict) or ans.get("verdict") not in VERDICTS:
            raise checklib.CheckError(
                f"answer() returned {ans!r:.120}, not a doubtlib answer")
        if ans["verdict"] == "pass" and not ans.get("evidence"):
            raise checklib.CheckError("a pass with no evidence is not an "
                                      "answer: say what was checked")
        return ans
    except Exception as e:  # noqa: BLE001 - a broken check is an answer too
        tb = traceback.format_exc(limit=4).strip().splitlines()[-3:]
        return _answer("error", [f"{type(e).__name__}: {e}"] + tb, None, [], [])


def library_dir() -> Path:
    return HERE.parent / "doubts"


def library() -> dict[str, Path]:
    """The module's own doubt checks, by name: every board gets asked these
    where they apply."""
    return {p.stem: p for p in sorted(library_dir().glob("*.py"))
            if not p.name.startswith("_")}


def find_netlist(ws: Path) -> Path:
    ws = Path(ws)
    own = ws / "kicad" / f"{ws.name}.net"
    if own.is_file():
        return own
    nets = sorted((ws / "kicad").glob("*.net")) or sorted(ws.glob("*.net"))
    if not nets:
        raise checklib.CheckError(f"{ws}: no netlist (kicad/<board>.net); "
                                  "export it first")
    return nets[0]
