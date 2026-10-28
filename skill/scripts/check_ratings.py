#!/usr/bin/env python
"""check_ratings.py - every pin's datasheet rating against the net it sits on (P4/P8).

One concern: a pin driven past what its datasheet allows - an over-voltage
input, a reversed rail, an input above its own supply, a regulator output
asked for more than it can give. No DRC sees this class; it is a respin.

Inputs (pure Python, no KiCad run):
 - the board (--pcb): footprint refs, their `LCSC` / `LCSC Part` property,
   and the net on every pad;
 - the parts directory (--parts, default <ws>/parts beside <ws>/kicad):
   parts.json (the P3 BOM, per-part `attributes`) and the per-part
   extraction <LCSC>.json that datasheet_extract.py validates (pinout +
   abs_max, optional pin_ratings);
 - rail voltages, first source wins per net: constraints.json `voltages`
   (--constraints; worst-case volts), the power tree's rail table
   (--power-tree, default <ws>/architecture/power_tree.md: a markdown table
   whose second header cell says V/volt), a fixed-output regulator's output
   pin (AMS1117-3.3, L78L33: the MPN's voltage on its VOUT/OUT power_out
   pin), then the net name (+5V, 3V3, +12V, -5V, VBUS = 5 V; GND*/VSS* = 0).
   A signal net with no voltage of its own takes the highest rail it is
   pulled up to through a 2-pad resistor.

Ratings per pin: the extraction's structured `pin_ratings` entries first
(pins, kind voltage|current, level abs_max|recommended, min/max as a number
or "VDD+0.3", optional ref pin), else its free-text `abs_max` rows parsed:
a row names its pins (VIN, EN, "V(IN)", VEN -> EN, IIN -> IN), a class
("any other pin", "5 V tolerant" -> pins whose notes say FT/5 V tolerant,
"supply" -> power_in, "input"/"output"), or a pair (VGS, "BST-SW",
"with respect to SW" - rated against the ref pin's net, not ground). A row
whose param says operating/recommended is a recommended rating. Pulse,
transient, ESD, clamp, injected and differential-input rows are skipped.
A named pin outranks a class; the tightest bound of the best tier wins.

Findings (kind = violation `kind`, remediations under reference/remediations/):
 - rating_over_voltage (error): net above an absolute maximum - including a
   bound relative to the part's own supply (an input above supply);
   a 2-pad part's `Voltage Rated` attribute below the volts across it.
 - rating_reverse_polarity (error): net below an absolute minimum (a
   negative single figure, as P-channel parts print, is that minimum), or
   a ground pin sitting on a positive rail.
   Either is a warning, not an error, when the net's volts came through a
   pull-up: that resistor may be a deliberate series limit into a clamped
   pin. A supply pin fed through a resistor is not given the rail's volts.
 - rating_outside_recommended (warning): above a recommended max, or a
   supply pin below its recommended min.
 - rating_over_current (warning): a power_out pin whose net's
   constraints `power[].current_a` exceeds the pin's current rating.
 - rating_unrated (warning): a part with no rating to check a connected
   pin against - no extraction, no row that names or classes the pin, or a
   relative bound whose reference supply has no known voltage. One per
   part, listing the pins. Never a silent pass.
A pin on a net of unknown voltage is not a finding (counted in facts).

CLI: --pcb board.kicad_pcb [--parts DIR] [--constraints c.json]
     [--power-tree power_tree.md] [--out report.json]
Exit 0 pass / 1 findings / 2 error (SPEC 6 contract).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import sexpdata

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "check_ratings"
EPS = 1e-6

GROUND_RE = re.compile(r"^(?:[ADPSC]?GND|GND[A-Z0-9_]*|VSS[A-Z0-9_]*|0V|"
                       r"EARTH|RTN|[A-Z0-9_]*_RTN)$")
SKIP_RE = re.compile(r"ESD|HBM|CDM|TRANSIENT|PULSE|SURGE|NON-REPETITIVE|"
                     r"HIPOT|WITHSTAND|ISOLATION|DIELECTRIC|VARIATION|"
                     r"DIFFERENTIAL|NOT THIS PART|INJECT|CLAMP|SHORT|LATCH|"
                     r"FORWARD VOLTAGE|DROPOUT|THRESHOLD|TOTAL|IFSM|IDM|IPP|"
                     r"\b\d+\s*[NMU]S\b|"
                     # electrical characteristics that extractors park in
                     # abs_max: levels, swings, drops - never limits
                     r"ELEC|HYST|SWING|HIGH-LEVEL|LOW-LEVEL|\bVOH\b|"
                     r"\bVOL\b|\bVIH\b|\bVIL\b|VENH|VENL|BREAKDOWN|BVDSS|"
                     r"\bDROP|DIFF|POWER.ON|\bTYP\b|RIPPLE|OFFSET|LEAKAGE|"
                     r"NOISE|ACCURACY|REGULATION|REFERENCE VOLTAGE|"
                     r"FEEDBACK VOLTAGE")
REC_RE = re.compile(r"OPERATING|RECOMMENDED")
DIODE_ALIASES = {"ANODE": "A", "CATHODE": "K", "GATE": "G", "DRAIN": "D",
                 "SOURCE": "S", "COLLECTOR": "C", "EMITTER": "E", "BASE": "B"}
IO_TYPES = {"input", "analog", "clock", "bidirectional", "unspecified",
            "passive", "output", "tristate", "open_collector"}
IN_TYPES = {"input", "analog", "clock", "bidirectional", "unspecified"}
OUT_TYPES = {"output", "tristate", "open_collector", "bidirectional"}
REG_OUT_NAMES = {"VOUT", "OUT", "VO", "OUTPUT"}
V_ATTR_RE = re.compile(r"voltage.*rated|rated.*voltage|voltage rating|"
                       r"voltage - dc", re.I)


# --- board ------------------------------------------------------------------

def _t(x):
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def _kids(n, name):
    return [c for c in n[1:] if isinstance(c, list) and c and _t(c[0]) == name]


def load_parts_on_board(pcb: Path) -> list[dict]:
    """[{ref, lcsc, pads{number: net}}] from the .kicad_pcb text."""
    try:
        root = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CheckError(f"cannot parse {pcb}: {exc}") from exc
    table = {}
    for n in _kids(root, "net"):
        if len(n) >= 3:
            table[n[1]] = str(n[2])
    out = []
    for fp in _kids(root, "footprint"):
        props = {}
        for p in _kids(fp, "property"):
            if len(p) >= 3 and isinstance(p[1], str):
                props[p[1]] = str(p[2])
        pads = {}
        for pad in _kids(fp, "pad"):
            num = str(pad[1]) if len(pad) > 1 else ""
            net = None
            for nn in _kids(pad, "net"):
                strs = [x for x in nn[1:] if isinstance(x, str)]
                net = strs[-1] if strs else table.get(nn[1])
            if num and net and not net.startswith("unconnected-"):
                pads.setdefault(num, net)
        out.append({"ref": props.get("Reference", "?"),
                    "lcsc": (props.get("LCSC") or props.get("LCSC Part")
                             or "").strip(),
                    "pads": pads})
    return out


# --- net voltages -------------------------------------------------------------

def base_net(net: str) -> str:
    return net.rsplit("/", 1)[-1].upper()


def is_ground(net: str) -> bool:
    return bool(GROUND_RE.match(base_net(net)))


def volts_from_name(net: str) -> float | None:
    n = base_net(net)
    if GROUND_RE.match(n):
        return 0.0
    if n.endswith("VBUS"):
        return 5.0
    m = re.search(r"(?<![A-Z0-9.])([+-])?(\d+)V(\d+)(?![0-9])", n)
    if m:
        v = float(f"{m.group(2)}.{m.group(3)}")
        return -v if m.group(1) == "-" else v
    m = re.search(r"(?<![A-Z0-9.])([+-])?(\d+(?:\.\d+)?)V(?![A-Z0-9])", n)
    if m:
        v = float(m.group(2))
        return -v if m.group(1) == "-" else v
    return None


_78XX_DECIMAL = {"33": 3.3, "52": 5.2, "85": 8.5}


def reg_out_volts(mpn: str) -> float | None:
    """Fixed output of a regulator MPN: AMS1117-3.3 -> 3.3, L78L33 -> 3.3,
    L7815 -> 15. A 78xx code counts only as the MPN's own prefix (L, LM,
    MC, UA, KA, NCV, CJ) and reads as whole volts, bar the decimal codes
    33 = 3.3, 52 = 5.2 and 85 = 8.5; any other
    MPN (HT7850, TPS78233) is unknown, never a guess."""
    m = re.search(r"[-_](\d{1,2}\.\d{1,2})(?![0-9])", mpn)
    if m:
        return float(m.group(1))
    m = re.match(r"(?:L|LM|MC|UA|KA|NCV|CJ)?78L?M?(\d{2})", mpn.upper())
    if m:
        return _78XX_DECIMAL.get(m.group(1), float(m.group(1)))
    return None


def cell_volts(cell: str) -> list[float]:
    """Voltages in a power-tree volts cell: '5.0 / 4.7' -> [5.0, 4.7];
    a range '4.5-5.5' (or 'to', '..') is its upper end; tolerance terms
    ('1.8 +-5%', '3.3 +/-0.1', '5%') are dropped, leaving the nominal."""
    s = cell.replace("\u2013", "-").replace("\u00b1", "+/-")
    s = re.sub(r"\+\s*/?\s*-\s*\d+(?:\.\d+)?\s*%?", " ", s)
    s = re.sub(r"\d+(?:\.\d+)?\s*%", " ", s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*V?\s*(?:-|to|\.\.)\s*(\d+(?:\.\d+)?)",
               lambda m: str(max(float(m.group(1)), float(m.group(2)))), s,
               flags=re.I)
    return [float(x) for x in re.findall(r"(?<![\d.])-?\d+(?:\.\d+)?", s)]


def power_tree_volts(path: Path, nets: set[str]) -> dict[str, float]:
    """Rail table rows of power_tree.md: | +3V3 | 3.3 | ... (header cell 2
    must say V / volt); '/VIN, +5V | 5.0 / 4.7' pairs names with numbers."""
    out: dict[str, float] = {}
    if not path or not path.is_file():
        return out
    by_base = {}
    for n in nets:
        by_base.setdefault(n.lstrip("/"), n)
    rows = [ln.strip() for ln in path.read_text(encoding="utf-8",
                                               errors="replace").splitlines()]
    header = None
    for i, ln in enumerate(rows):
        if not ln.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if i + 1 < len(rows) and re.match(r"^\|[\s:|-]+\|?$", rows[i + 1]):
            header = cells
            continue
        if re.match(r"^\|[\s:|-]+\|?$", ln) or not header or len(cells) < 2 \
                or len(header) < 2 \
                or not re.search(r"\bV\b|volt", header[1], re.I):
            continue
        names = [by_base.get(x.strip().lstrip("/"))
                 for x in cells[0].split(",")]
        nums = cell_volts(cells[1])
        if not nums:
            continue
        for k, name in enumerate(names):
            if name:
                out.setdefault(name, nums[k] if len(nums) == len(names)
                               else max(nums))
    return out


# --- ratings --------------------------------------------------------------------

def aliases(name: str) -> set[str]:
    full = name.upper().strip()
    al = {full, re.sub(r"_\d+$", "", full), re.split(r"[-/]", full)[0] or full,
          re.sub(r"[^A-Z0-9]", "", full)}
    if full in DIODE_ALIASES:
        al.add(DIODE_ALIASES[full])
    m = re.match(r"^[AD](VDD|VCC)", full)   # AVDD/DVCC answer to VDD/VCC
    if m:
        al.add(m.group(1))
    return {a for a in al if a and not a.isdigit() and a not in ("V", "I")}


def parse_term(term: str, scale: float):
    """'4.0' -> 4.0; 'VDD+0.3' -> ('VDD', 0.3); '(V+)+0.3' -> ('V+', 0.3);
    'VSS-0.3' -> -0.3. None when unreadable."""
    t = term.strip().upper().replace(" ", "")
    try:
        return float(t) * scale
    except ValueError:
        pass
    if t.startswith("("):
        close = t.find(")")
        if close < 0:
            return None
        name, rest = t[1:close], t[close + 1:]
    else:
        m = re.match(r"([A-Z][A-Z0-9_]*)(.*)$", t)
        if not m:
            return None
        name, rest = m.group(1), m.group(2)
    off = 0.0
    if rest:
        m = re.match(r"^([+-])(\d+(?:\.\d+)?)$", rest)
        if not m:
            return None
        off = float(m.group(2)) * (-1 if m.group(1) == "-" else 1) * scale
    if GROUND_RE.match(name):
        return off
    return (name, off)


def parse_bounds(value, param: str, scale: float, rec: bool = False,
                 kind: str = "voltage"):
    """(lo, hi) from an abs_max value; either side None, a float or an expr.
    A positive 'min' is a lower bound only for a recommended row (an absolute
    maximum's floor is never above 0 V; BVDSS-style minimums are not limits)."""
    p = param.upper()
    if isinstance(value, (int, float)):
        v = float(value) * scale
        if kind == "voltage" and abs(v) < 1.0 and "ABS" not in p:
            return None, None   # a sub-volt single figure is a characteristic
        if v < 0:
            return v, None      # P-channel figures print negative: a floor
        if re.search(r"(?<![A-Z])MIN(?![A-Z])", p):
            return (v, None) if v <= 0 or rec else (None, None)
        if re.search(r"(?<![A-Z])MAX(?![A-Z])", p):
            return None, v
        return (v, None) if v < 0 else (None, v)
    s = str(value).strip().replace("±", "+/-").replace("+-", "+/-")
    m = re.match(r"^\+/-\s*(\d+(?:\.\d+)?)$", s)
    if m:
        v = float(m.group(1)) * scale
        return -v, v
    parts = re.split(r"\s+to\s+", s, flags=re.I)
    if len(parts) == 2:
        return parse_term(parts[0], scale), parse_term(parts[1], scale)
    parts = [x for x in s.split("/")]
    if len(parts) in (2, 3):
        lo, hi = parse_term(parts[0], scale), parse_term(parts[-1], scale)
        return lo, hi
    single = parse_term(s, scale)
    if isinstance(single, float):
        return parse_bounds(single / scale, param, scale, rec, kind)
    return (None, single) if single is not None else (None, None)


def unit_kind(unit: str) -> tuple[str | None, float]:
    u = (unit or "").strip().upper()
    if re.search(r"AC|RMS|KV", u):
        return None, 0
    if u in ("V", "VDC", "V DC") or u.startswith("V "):
        return "voltage", 1.0
    if u.startswith("MV"):
        return "voltage", 1e-3
    for pre, sc in (("MA", 1e-3), ("UA", 1e-6), ("A", 1.0)):
        if u == pre or u.startswith(pre + " ") or u.startswith(pre + "("):
            return "current", sc
    return None, 0


def pin_index(pinout: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for p in pinout:
        for a in aliases(p["name"]):
            idx.setdefault(a, []).append(p)
    return idx


def classify_row(param: str, idx: dict, pinout: list[dict]):
    """-> (explicit pins set, class name or None, ref pin name or None)."""
    p = param.upper()
    toks = re.findall(r"[A-Z][A-Z0-9_]*", p)
    ref = None
    m = re.search(r"(?:RESPECT TO|REFERRED TO|\bTO|\bFROM)\s+\(?([A-Z][A-Z0-9_]*)",
                  p) or re.search(r"\b([A-Z][A-Z0-9_]*)\s*-\s*\(?"
                                  r"([A-Z][A-Z0-9_]*)", p)
    if m:
        cand = m.group(m.lastindex)
        hits = idx.get(cand) or idx.get(cand[1:] if cand[:1] in "VI" else "")
        if hits and not any(h["type"] == "ground" for h in hits):
            ref = hits[0]["name"]
    pins: set[str] = set()
    for tok in toks:
        hits = idx.get(tok)
        if not hits and len(tok) > 1 and tok[0] in "VI":
            hits = idx.get(tok[1:])
        if not hits and len(tok) == 3 and tok[0] == "V" \
                and idx.get(tok[1]) and idx.get(tok[2]):
            hits, ref = idx[tok[1]], idx[tok[2]][0]["name"]
        for h in hits or []:
            if h["type"] != "ground" and h["name"] != ref:
                pins.add(h["pin"])
    if ref is None and "REVERSE" in p and idx.get("K") and idx.get("A"):
        pins, ref = {h["pin"] for h in idx["K"]}, idx["A"][0]["name"]
    if pins:
        return pins, None, ref
    if "TOLERANT" in p or "FT" in toks:
        return set(), "ft", None
    if re.search(r"OTHER PIN|ALL OTHER|ANY PIN|ALL PINS|ANY I/O", p):
        return set(), "other", None
    if "SUPPLY" in p or any(t in ("VCC", "VDD", "VS", "VCCIO", "VDDIO")
                            for t in toks):
        return set(), "supply", None
    has_in = "INPUT" in p or any(t in ("VI", "VIN", "VIO") for t in toks)
    has_out = "OUTPUT" in p or any(t in ("VO", "VOUT", "VIO") for t in toks)
    if has_in and has_out:
        return set(), "io", None
    if has_in:
        return set(), "input", None
    if has_out:
        return set(), "output", None
    return set(), None, None


def part_ratings(ext: dict) -> dict:
    """{pin: {(kind, level): [rows...]}} - rows {lo, hi, ref, tier, src}.
    Tier 0 structured, 1 named, 2 ft, 3 class, 4 'other'."""
    pinout = ext.get("pinout") or []
    idx = pin_index(pinout)
    by_pin: dict[str, dict] = {}

    def add(pins, kind, level, lo, hi, ref, tier, src):
        for pin in pins:
            by_pin.setdefault(pin, {}).setdefault((kind, level), []).append(
                {"lo": lo, "hi": hi, "ref": ref, "tier": tier, "src": src})

    for r in ext.get("pin_ratings") or []:
        pins = set()
        for name in r.get("pins") or []:
            pins |= {p["pin"] for p in pinout if p["pin"] == str(name)}
            pins |= {h["pin"] for h in idx.get(str(name).upper(), [])}
        level = "rec" if r.get("level") == "recommended" else "abs"
        lo = parse_term(str(r["min"]), 1.0) if r.get("min") is not None else None
        hi = parse_term(str(r["max"]), 1.0) if r.get("max") is not None else None
        add(pins, r.get("kind", "voltage"), level, lo, hi, r.get("ref"), 0,
            f"pin_ratings: {r.get('source') or r.get('pins')}")
    classes: list[tuple] = []
    for row in ext.get("abs_max") or []:
        param = str(row.get("param") or "")
        kind, scale = unit_kind(row.get("unit"))
        if kind is None or SKIP_RE.search(param.upper()):
            continue
        level = "rec" if REC_RE.search(param.upper()) else "abs"
        lo, hi = parse_bounds(row.get("value"), param, scale, level == "rec",
                              kind)
        if lo is None and hi is None:
            continue
        pins, cls, ref = classify_row(param, idx, pinout)
        src = f"{param} = {row.get('value')} {row.get('unit') or ''}".strip()
        if pins:
            add(pins, kind, level, lo, hi, ref, 1, src)
        elif cls:
            classes.append((cls, kind, level, lo, hi, src))
    ft = {p["pin"] for p in pinout
          if re.search(r"\bFT[A-Za-z]*\b|5\s?V[- ]tolerant",
                       p.get("notes") or "", re.I)}
    for cls, kind, level, lo, hi, src in classes:
        for p in pinout:
            t, pin = p["type"], p["pin"]
            if t in ("ground", "nc"):
                continue
            hit = {"ft": pin in ft,
                   "other": t in IO_TYPES and pin not in ft,
                   "supply": t == "power_in",
                   "input": t in IN_TYPES or (t == "power_in" and "IN" in
                                              p["name"].upper()),
                   "output": t in OUT_TYPES or (kind == "current"
                                                and t == "power_out"),
                   "io": t in IN_TYPES | OUT_TYPES}[cls]
            if hit:
                tier = {"ft": 2, "other": 4}.get(cls, 3)
                add([pin], kind, level, lo, hi, None, tier, src)
    # keep only the best tier per (pin, kind, level)
    for rows in by_pin.values():
        for key, lst in rows.items():
            best = min(r["tier"] for r in lst)
            rows[key] = [r for r in lst if r["tier"] == best]
    return by_pin


# --- the check ----------------------------------------------------------------

def _fmt(v: float) -> str:
    return f"{v:.3g}"


def run_check(pcb: Path, parts_dir: Path, constraints: dict | None,
              power_tree: Path | None) -> tuple[list[dict], dict]:
    board = load_parts_on_board(pcb)
    pj = parts_dir / "parts.json"
    bom = checklib.load_json(pj, "parts.json").get("parts", []) \
        if pj.is_file() else []
    bom_by_lcsc = {str(p.get("lcsc") or "").strip(): p for p in bom
                   if p.get("lcsc")}
    exts: dict[str, dict] = {}
    for lcsc in {b["lcsc"] for b in board if b["lcsc"]}:
        f = parts_dir / f"{lcsc}.json"
        if f.is_file():
            exts[lcsc] = checklib.load_json(f, f"extraction {f.name}")

    nets = {n for b in board for n in b["pads"].values()}
    volts: dict[str, float] = {}
    vsrc: dict[str, str] = {}

    def setv(net, v, src):
        if net in nets and net not in volts and v is not None:
            volts[net], vsrc[net] = float(v), src

    for e in (constraints or {}).get("voltages") or []:
        setv(e.get("net"), e.get("voltage"), "constraints")
    for net, v in power_tree_volts(power_tree, nets).items():
        setv(net, v, "power_tree")
    for b in board:
        ext = exts.get(b["lcsc"])
        v = reg_out_volts(str(ext.get("mpn") or "")) if ext else None
        if v is None:
            continue
        for p in ext.get("pinout") or []:
            if p["type"] == "power_out" and \
                    aliases(p["name"]) & REG_OUT_NAMES and p["pin"] in b["pads"]:
                setv(b["pads"][p["pin"]], v, f"regulator {b['ref']}")
    for net in nets:
        setv(net, volts_from_name(net), "net name")
    # pull-ups: a 2-pad resistor from an unknown net to a known rail. A net
    # that also has a resistor to ground or to another unknown net is a
    # divider or a series path - its voltage stays unknown.
    pulled: dict[str, float] = {}
    spoiled: set[str] = set()
    for b in board:
        if re.match(r"^R\d", b["ref"]) and len(set(b["pads"].values())) == 2:
            a, c = sorted(set(b["pads"].values()))
            for x, y in ((a, c), (c, a)):
                if x in volts:
                    continue
                if volts.get(y, 0) > 0:
                    pulled[x] = max(pulled.get(x, 0.0), volts[y])
                else:
                    spoiled.add(x)
    for net, v in pulled.items():
        if net not in spoiled:
            setv(net, v, "pull-up")
    currents = {e.get("net"): float(e["current_a"])
                for e in (constraints or {}).get("power") or []
                if e.get("current_a") is not None}

    out: list[dict] = []
    unknown_net_pins = 0
    checked_pins = 0

    def emit(kind, sev, ref, net, msg, **extra):
        out.append(violation(SCRIPT, sev, None, None, net, [ref], msg, SCRIPT,
                             kind=kind, **extra))

    unrated_passives: list[str] = []
    for b in sorted(board, key=lambda x: x["ref"]):
        ref, pads = b["ref"], b["pads"]
        if not pads:
            continue
        ext = exts.get(b["lcsc"])
        if ext is None:
            _check_unextracted(b, bom_by_lcsc.get(b["lcsc"]), volts, emit,
                               unrated_passives)
            continue
        ratings = part_ratings(ext)
        name_of = {p["pin"]: p for p in ext.get("pinout") or []}
        idx = pin_index(ext.get("pinout") or [])

        def ref_volts(name):
            hits = idx.get(str(name).upper()) or []
            if not hits and str(name).upper() in ("VS", "V+"):
                hits = idx.get("V+") or []
            if not hits and str(name).upper().startswith("V"):
                hits = idx.get(str(name).upper()[1:]) or []
            if not hits:  # VDDIOX -> VDDIO*, VDD*: prefix match
                stem = str(name).upper().rstrip("X")
                hits = [p for a, ps in idx.items() if a.startswith(stem)
                        for p in ps]
            vs = [volts[pads[h["pin"]]] for h in hits
                  if h["pin"] in pads and pads[h["pin"]] in volts]
            return max(vs) if vs else None

        def resolve(bound):
            if bound is None or isinstance(bound, float):
                return bound, None
            v = ref_volts(bound[0])
            return (None if v is None else v + bound[1]), bound[0]

        unrated: list[str] = []
        for pin, net in sorted(pads.items()):
            p = name_of.get(pin)
            if p is None or p["type"] == "nc":
                continue
            label = f"{ref}.{pin} ({p['name']})"
            v = volts.get(net)
            if p["type"] == "ground":
                if v is not None and v > 0.5 and not is_ground(net):
                    emit("rating_reverse_polarity", "error", ref, net,
                         f"{label} is a ground pin on {net} = {_fmt(v)} V "
                         f"({vsrc[net]})", pin=f"{ref}.{pin}", net_v=v)
                continue
            rows = ratings.get(pin, {})
            if p["type"] == "power_out" and net in currents:
                for r in rows.get(("current", "abs"), []):
                    hi, _ = resolve(r["hi"])
                    if hi is not None and currents[net] > abs(hi) + EPS:
                        emit("rating_over_current", "warning", ref, net,
                             f"{label} feeds {net} at {currents[net]} A "
                             f"(constraints power[]), over its rated "
                             f"{_fmt(abs(hi))} A [{r['src']}]",
                             pin=f"{ref}.{pin}", limit_a=abs(hi))
            vabs = rows.get(("voltage", "abs"), [])
            vrec = rows.get(("voltage", "rec"), [])
            if not vabs and not vrec:
                unrated.append(f"{pin} {p['name']}: no rating names it")
                continue
            if v is not None and vsrc[net] == "pull-up" \
                    and p["type"] == "power_in":
                v = None  # a series resistor into a supply pin: drop unknown
            if v is None:
                unknown_net_pins += 1
                continue
            checked_pins += 1
            hit_abs = False
            for level, lst in (("abs", vabs), ("rec", vrec)):
                for r in lst:
                    vp, rel = v, ""
                    if r["ref"]:
                        vr = ref_volts(r["ref"])
                        if vr is None:
                            unrated.append(f"{pin} {p['name']}: rated against "
                                           f"{r['ref']}, whose net has no "
                                           "known voltage")
                            continue
                        vp, rel = v - vr, f" vs {r['ref']}"
                    lo, lo_name = resolve(r["lo"])
                    hi, hi_name = resolve(r["hi"])
                    for nm, bnd in ((lo_name, lo), (hi_name, hi)):
                        if nm and bnd is None:
                            unrated.append(f"{pin} {p['name']}: bound is "
                                           f"relative to {nm}, whose net has "
                                           "no known voltage")
                    base = dict(pin=f"{ref}.{pin}", net_v=v, rating=r["src"])
                    # a volt inferred through a pull-up may be a deliberate
                    # series limit into a clamped pin: surfaced, not failed
                    sev = "warning" if vsrc[net] == "pull-up" else "error"
                    if level == "abs" and hi is not None and vp > hi + EPS:
                        hit_abs = True
                        why = (f" - input above supply: {hi_name} is "
                               f"{_fmt(hi - r['hi'][1])} V" if hi_name else "")
                        emit("rating_over_voltage", sev, ref, net,
                             f"{label} on {net} = {_fmt(vp)} V{rel} "
                             f"({vsrc[net]}) exceeds its absolute max "
                             f"{_fmt(hi)} V{why} [{r['src']}]", limit_v=hi,
                             **base)
                    elif level == "abs" and lo is not None and vp < lo - EPS:
                        hit_abs = True
                        emit("rating_reverse_polarity", sev, ref, net,
                             f"{label} on {net} = {_fmt(vp)} V{rel} "
                             f"({vsrc[net]}) is below its absolute min "
                             f"{_fmt(lo)} V [{r['src']}]", limit_v=lo, **base)
                    elif level == "rec" and not hit_abs and (
                            (hi is not None and vp > hi + EPS) or
                            (p["type"] == "power_in" and lo is not None
                             and 0 < vp < lo - EPS)):
                        emit("rating_outside_recommended", "warning", ref, net,
                             f"{label} on {net} = {_fmt(vp)} V{rel} is outside "
                             f"its recommended {_fmt(lo) if lo is not None else '-'}"
                             f"..{_fmt(hi) if hi is not None else '-'} V "
                             f"[{r['src']}]", **base)
        if unrated:
            emit("rating_unrated", "warning", ref, None,
                 f"{ref} ({ext.get('mpn')}): {len(unrated)} connected pin(s) "
                 f"could not be checked - {'; '.join(unrated[:6])}"
                 + (" ..." if len(unrated) > 6 else ""),
                 pins=unrated)
    if unrated_passives:
        emit("rating_unrated", "warning", None, None,
             f"{len(unrated_passives)} two-terminal part(s) have no extraction "
             "and no voltage-rated attribute in parts.json: "
             + ", ".join(unrated_passives), pins=unrated_passives)
        out[-1]["refs"] = sorted(unrated_passives)
    facts = {"parts_on_board": len(board), "extractions": sorted(exts),
             "net_volts": {n: {"v": volts[n], "src": vsrc[n]}
                           for n in sorted(volts)},
             "pins_checked": checked_pins,
             "pins_on_unknown_nets": unknown_net_pins}
    return out, facts


def _check_unextracted(b: dict, bom: dict | None, volts: dict, emit,
                       unrated_passives: list[str]) -> None:
    """A part with no extraction: a 2-net part's `Voltage Rated` attribute
    is checked across it; anything else is an explicit unrated finding
    (two-terminal BOM parts are collected into one finding by the caller)."""
    ref, pads = b["ref"], b["pads"]
    nets = sorted(set(pads.values()))
    rated = None
    for a in (bom or {}).get("attributes") or []:
        if V_ATTR_RE.search(str(a.get("name") or "")):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(k?)V", str(a.get("value") or ""))
            if m:
                rated = float(m.group(1)) * (1000 if m.group(2) else 1)
    if rated is not None and len(nets) == 2:
        va, vb = volts.get(nets[0]), volts.get(nets[1])
        if va is None or vb is None:
            return
        if abs(va - vb) > rated + EPS:
            emit("rating_over_voltage", "error", ref, nets[0],
                 f"{ref} is rated {_fmt(rated)} V but sits across "
                 f"{nets[0]} = {_fmt(va)} V and {nets[1]} = {_fmt(vb)} V",
                 limit_v=rated, net_v=abs(va - vb))
        return
    if len(nets) <= 1:
        return  # a single-net part (test point, mounting hole) stresses nothing
    why = ("no datasheet extraction" if bom or b["lcsc"] else
           "no LCSC part on the footprint and no extraction")
    if bom is not None and len(nets) == 2:
        unrated_passives.append(ref)
        return
    emit("rating_unrated", "warning", ref, None,
         f"{ref} ({b['lcsc'] or 'no LCSC'}): {why}", pins=[why])


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--parts", help="parts dir (default <ws>/parts)")
    ap.add_argument("--constraints", help="constraints.json (voltages, power)")
    ap.add_argument("--power-tree", help="power_tree.md "
                    "(default <ws>/architecture/power_tree.md)")
    ap.add_argument("--out", help="write the report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    ws = pcb.resolve().parent.parent
    parts = Path(args.parts) if args.parts else ws / "parts"
    if not parts.is_dir():
        raise CheckError(f"parts dir not found: {parts}")
    tree = Path(args.power_tree) if args.power_tree else \
        ws / "architecture" / "power_tree.md"
    cons = checklib.load_json(args.constraints, "constraints") \
        if args.constraints else None
    viol, facts = run_check(pcb, parts, cons, tree)
    return checklib.report(SCRIPT, pcb, viol, **facts), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
