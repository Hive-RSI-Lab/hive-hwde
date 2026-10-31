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
   A 2-terminal diode (D*/LED*) with no extraction is rated by a parts.json
   reverse-voltage attribute ("Reverse Voltage (Vr)"), its cathode found by
   pad name (the board's pinfunction, else the netlist <board>.net beside
   it) or else pad 1, KiCad's convention.
   Reversed input (warning, field `scenario`): each 2-net power input - a
   J/P/CN/X/BT part on one ground and one positive rail, a header, jack or
   battery that plugs in either way - is plugged in reversed: its rail goes
   to minus its volts, carried through lone series R/L/FB parts (a blocking
   diode draws no current), and every diode it then reverse-biases past its
   reverse rating is named. "The LED blocks reverse current" is checked
   against the LED's VR, not taken on trust.
 - rating_outside_recommended (warning): above a recommended max, or a
   supply pin below its recommended min.
 - rating_over_current (warning): a power_out pin whose net's
   constraints `power[].current_a` exceeds the pin's current rating.
   A fuse or PPTC (F*/FU*, or a parts.json hold-current attribute) carrying
   a constraints power[].current_a: over its rating at the ambient is an
   error, a fuse past 75 % of its rerated current (k_T, Littelfuse) or a
   PPTC whose Ihold at the ambient (typical Bourns/Littelfuse table) clears
   the current by under 10 % a warning (R-RAT-3, R-RAT-4).
 - rating_unrated (warning): a part with no rating to check a connected
   pin against - no extraction, no row that names or classes the pin, or a
   relative bound whose reference supply has no known voltage. One per
   part, listing the pins. Never a silent pass.
 - rating_over_voltage, also: a chip resistor's volts across it above its
   limiting element voltage (parts.json Voltage-Supply(Max), else the chip
   size's D/CRCW e3 Umax; R-RAT-1); a fuse or PPTC whose voltage rating is
   under the rail it sits on (it must hold the rail off once open).
 - rating_over_temperature (error): the board's ambient above a part's
   operating range (parts.json "Operating Temperature", or the
   extraction's operating_temp_c), or constraints ambient_min_c below it
   (R-REL-2). The ambient is constraints ambient_c, else the enclosure air
   check_thermal works out, else 25 C, said so in `ambient`.
Sources and verified/disputed tags: docs/ingest/safety-components.md.
A pin on a net of unknown voltage is not a finding (counted in facts).

The fact ledger (lib/ledgerlib.py): each refdes gets a `rating` fact
(verified when a rating was compared with known volts and held, failed on an
error finding, unknown when unrated or nothing could be compared) and a
`pinout` fact (pinout_fact: the board's pad numbers and names against the
extraction's pinout; no extraction is unknown). Written into <ws>/ledger.json
when the board sits in a workspace; `ledger_counts` in the report.

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
import engcalc  # noqa: E402
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
# A 2-pad part's working-voltage attribute as LCSC lists it: "Voltage Rated"
# and "Voltage Rating" (caps, connectors), "Voltage - DC", and the chip
# resistors' "Voltage-Supply(Max)" / "Working Voltage". Not "Voltage -
# Forward(Vf)" or "Overload Voltage": neither is a rating across the part.
V_ATTR_RE = re.compile(r"voltage.*rated|rated.*voltage|voltage rating|"
                       r"voltage - dc|voltage\s*-\s*supply\s*\(max\)|"
                       r"working voltage", re.I)


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
        pads, fns, nums = {}, {}, set()
        for pad in _kids(fp, "pad"):
            num = str(pad[1]) if len(pad) > 1 else ""
            if num:
                nums.add(num)
            net = None
            for nn in _kids(pad, "net"):
                strs = [x for x in nn[1:] if isinstance(x, str)]
                net = strs[-1] if strs else table.get(nn[1])
            for pf in _kids(pad, "pinfunction"):
                if num and len(pf) > 1 and isinstance(pf[1], str):
                    fns.setdefault(num, pf[1])
            if num and net and not net.startswith("unconnected-"):
                pads.setdefault(num, net)
        attrs = {str(_t(x)) for a in _kids(fp, "attr") for x in a[1:]}
        out.append({"ref": props.get("Reference", "?"),
                    "in_bom": not (attrs & {"exclude_from_bom", "board_only"}),
                    "lcsc": (props.get("LCSC") or props.get("LCSC Part")
                             or "").strip(),
                    "value": props.get("Value", ""),
                    "footprint": str(fp[1]) if len(fp) > 1 else "",
                    "pads": pads, "fns": fns, "nums": sorted(nums)})
    return out


# --- net voltages -------------------------------------------------------------

def base_net(net: str) -> str:
    return net.rsplit("/", 1)[-1].upper()


def is_ground(net: str) -> bool:
    return bool(GROUND_RE.match(base_net(net)))


# A name word that makes the net a signal about a rail, not the rail:
# PG_1V35 is 1V35's power-good, ADC_1V35 its divided-down sense.
SIGNAL_WORDS = {"PG", "PGOOD", "PGD", "PWRGD", "PWROK", "POK", "OK", "ADC",
                "AIN", "SENSE", "SNS", "VSENSE", "ISENSE", "FB", "EN", "ENA",
                "ENABLE", "MON", "DIV", "DET", "FLT", "FAULT", "ALERT",
                "MEAS", "CTRL", "INT", "IRQ", "SEL", "TRIP", "OVP", "UVP",
                "SS", "TRK", "TRACK", "COMP", "ILIM"}


def volts_from_name(net: str) -> float | None:
    """A rail's voltage read from its name: +3V3, 1V35, VCC_5V, VBUS and
    VBUS_J1 (any VBUS word) as 5 V, grounds 0. A name with a signal word
    (PG_1V35, ADC_1V35, VBUS_SENSE, 3V3_EN) is a signal, not a rail."""
    n = base_net(net)
    if GROUND_RE.match(n):
        return 0.0
    words = n.split("_")
    if SIGNAL_WORDS & set(words):
        return None
    if n.endswith("VBUS") or "VBUS" in words:
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


def _board_features(bom: list[dict]) -> set[str]:
    """refdes parts.json classes as board_feature (printed copper, logos)."""
    out = set()
    for ln in bom:
        refs = ln.get("refdes") or []
        refs = [refs] if isinstance(refs, str) else refs
        rc = ln.get("refdes_class") or {}
        for r in refs:
            if rc.get(r, ln.get("assembly_class")) == "board_feature":
                out.add(str(r))
    return out


def run_check(pcb: Path, parts_dir: Path, constraints: dict | None,
              power_tree: Path | None,
              cons_dir: Path | None = None,
              trusted: set[str] | None = None) -> tuple[list[dict], dict]:
    """trusted: refdes whose rating a person checked and recorded verified in
    the ledger (#429) - their 'unrated' warnings are dropped (a real
    over-rating error still fires)."""
    pj = parts_dir / "parts.json"
    bom = checklib.load_json(pj, "parts.json").get("parts", []) \
        if pj.is_file() else []
    # footprints that are not parts - excluded from the BOM (printed RF
    # structures, logos, test coupons) or classed board_feature - have no
    # rating to check (#429)
    feats = _board_features(bom)
    board = [b for b in load_parts_on_board(pcb)
             if b["in_bom"] and b["ref"] not in feats]
    trusted = set(trusted or ())
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
    checked_by_ref: dict[str, int] = {}
    netfns = net_pinfunctions(pcb)
    diodes = {b["ref"]: d for b in board
              if (d := diode_reverse(b, exts.get(b["lcsc"]),
                                     bom_by_lcsc.get(b["lcsc"]), netfns))}
    for b in sorted(board, key=lambda x: x["ref"]):
        ref, pads = b["ref"], b["pads"]
        if not pads:
            continue
        ext = exts.get(b["lcsc"])
        if ext is None and is_fuse(b, bom_by_lcsc.get(b["lcsc"])):
            continue  # rated rail-to-ground, not across: fuse_findings
        dio = diodes.get(ref)
        if ext is None and dio:
            vk, va = volts.get(pads[dio["k"]]), volts.get(pads[dio["a"]])
            if vk is not None and va is not None:
                checked_by_ref[ref] = checked_by_ref.get(ref, 0) + 1
                if vk - va > dio["vr"] + EPS:
                    emit("rating_reverse_polarity", "error", ref,
                         pads[dio["k"]],
                         f"{ref} is reversed by {_fmt(vk - va)} V (cathode "
                         f"{pads[dio['k']]}, anode {pads[dio['a']]}), over "
                         f"its {_fmt(dio['vr'])} V reverse rating "
                         f"[{dio['src']}; {dio['how']}]",
                         pin=f"{ref}.{dio['k']}", net_v=vk - va,
                         limit_v=dio["vr"], rating=dio["src"])
            continue
        if ext is None:
            if _check_unextracted(b, bom_by_lcsc.get(b["lcsc"]), volts, emit,
                                  unrated_passives):
                checked_by_ref[ref] = checked_by_ref.get(ref, 0) + 1
            continue
        ratings = part_ratings(ext)
        name_of = {p["pin"]: p for p in ext.get("pinout") or []}
        pair_refs = {r["ref"] for rows in ratings.values()
                     for lst in rows.values() for r in lst if r["ref"]}
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
            if not vabs and not vrec and p["name"] in pair_refs:
                continue  # the other side of a pair rating (a diode's A)
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
            checked_by_ref[ref] = checked_by_ref.get(ref, 0) + 1
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
    # a 2-net power input plugged in the wrong way round: what each diode
    # it reaches is asked to block (a design that says "the LED blocks
    # reverse current" is claiming exactly this). A fault case, so a warning.
    reversed_inputs = []
    for conn, rail in input_connectors(board, volts):
        rev = reversed_input_volts(board, volts, vsrc, rail)
        reversed_inputs.append({"connector": conn["ref"], "rail": rail,
                                "rail_v": -volts[rail]})
        for ref, dio in sorted(diodes.items()):
            pads = next(b["pads"] for b in board if b["ref"] == ref)
            vk, va = rev.get(pads[dio["k"]]), rev.get(pads[dio["a"]])
            if vk is None or va is None or vk - va <= dio["vr"] + EPS:
                continue
            emit("rating_reverse_polarity", "warning", ref, rail,
                 f"{conn['ref']} plugged in reversed puts {_fmt(vk - va)} V "
                 f"across {ref} the wrong way (cathode {pads[dio['k']]}, "
                 f"anode {pads[dio['a']]}; {rail} = {_fmt(volts[rail])} V, "
                 "no drop in the series parts while it blocks), over its "
                 f"{_fmt(dio['vr'])} V reverse rating [{dio['src']}; "
                 f"{dio['how']}]", pin=f"{ref}.{dio['k']}", net_v=vk - va,
                 limit_v=dio["vr"], rating=dio["src"],
                 scenario=f"{conn['ref']} reversed")
    amb = ambient(constraints, cons_dir)
    fuse_facts = fuse_findings(board, bom_by_lcsc, volts, vsrc, currents,
                               amb, emit, checked_by_ref, unrated_passives)
    temp_facts = temperature_findings(board, bom_by_lcsc, exts, amb, emit)
    unrated_passives = [r for r in unrated_passives if r not in trusted]
    out = [v for v in out if not (v.get("kind") == "rating_unrated"
                                  and set(v.get("refs") or []) <= trusted
                                  and v.get("refs"))]
    lcsc_of = {b["ref"]: b["lcsc"] for b in board}
    bare = [r for r in unrated_passives
            if not (bom_by_lcsc.get(lcsc_of.get(r)) or {}).get("attributes")]
    if unrated_passives:
        emit("rating_unrated", "warning", None, None,
             f"{len(unrated_passives)} two-terminal part(s) have no extraction "
             "and no voltage-rated attribute in parts.json: "
             + ", ".join(unrated_passives)
             + (f" ({', '.join(bare)}: the parts.json line carries no "
                "attributes - keep parts_search's row whole)" if bare else ""),
             pins=unrated_passives)
        out[-1]["refs"] = sorted(unrated_passives)
    facts = {"parts_on_board": len(board), "extractions": sorted(exts),
             "net_volts": {n: {"v": volts[n], "src": vsrc[n]}
                           for n in sorted(volts)},
             "pins_checked": checked_pins,
             "reversed_inputs": reversed_inputs,
             "pins_on_unknown_nets": unknown_net_pins,
             "ambient": amb, "fuses": fuse_facts,
             "operating_range": temp_facts,
             "ledger_facts": ledger_facts(board, exts, out, checked_by_ref,
                                          bom_by_lcsc)}
    return out, facts


def _check_unextracted(b: dict, bom: dict | None, volts: dict, emit,
                       unrated_passives: list[str]) -> bool:
    """A part with no extraction: a 2-net part's `Voltage Rated` attribute
    is checked across it; anything else is an explicit unrated finding
    (two-terminal BOM parts are collected into one finding by the caller).
    True when a rating was compared with known volts."""
    ref, pads = b["ref"], b["pads"]
    nets = sorted(set(pads.values()))
    rated = bom_volts(bom)
    src = "parts.json"
    if rated is None and len(nets) == 2 and re.match(r"^R\d", ref):
        rated = engcalc.resistor_umax_v(b.get("footprint"))
        src = (f"{engcalc.package_code(b.get('footprint'))} limiting element "
               "voltage, D/CRCW e3 table")
    if rated is not None and len(nets) == 2:
        va, vb = volts.get(nets[0]), volts.get(nets[1])
        if va is None or vb is None:
            return False
        if abs(va - vb) > rated + EPS:
            emit("rating_over_voltage", "error", ref, nets[0],
                 f"{ref} is rated {_fmt(rated)} V [{src}] but sits across "
                 f"{nets[0]} = {_fmt(va)} V and {nets[1]} = {_fmt(vb)} V",
                 limit_v=rated, net_v=abs(va - vb), rating=src)
        return True
    if len(nets) <= 1:
        return False  # a single-net part stresses nothing
    why = ("no datasheet extraction" if bom or b["lcsc"] else
           "no LCSC part on the footprint and no extraction")
    if bom is not None and len(nets) == 2:
        unrated_passives.append(ref)
        return False
    emit("rating_unrated", "warning", ref, None,
         f"{ref} ({b['lcsc'] or 'no LCSC'}): {why}", pins=[why])
    return False


def bom_volts(bom: dict | None) -> float | None:
    """A part's voltage-rated attribute in parts.json (V_ATTR_RE), or None."""
    rated = None
    for a in (bom or {}).get("attributes") or []:
        if V_ATTR_RE.search(str(a.get("name") or "")):
            m = re.search(r"(\d+(?:\.\d+)?)\s*(k?)V", str(a.get("value") or ""))
            if m:
                rated = float(m.group(1)) * (1000 if m.group(2) else 1)
    return rated


# --- ambient: fuses, PPTCs and every part's operating range -------------------

DEFAULT_AMBIENT_C = 25.0
HOLD_ATTR_RE = re.compile(r"hold(?:ing)?\s*current|current.*hold|\(ih\)", re.I)
FUSE_I_ATTR_RE = re.compile(r"current.*rat|rated.*current|^current$", re.I)
FUSE_V_ATTR_RE = re.compile(r"volt.*(?:max|rat)|rated.*volt|^voltage", re.I)
PPTC_TEXT_RE = re.compile(r"PPTC|resettable|polyfuse|polyswitch|multifuse|"
                          r"\bPTC\b", re.I)
TEMP_ATTR_RE = re.compile(r"operating temp", re.I)


def ambient(cons: dict | None, cons_dir: Path | None) -> dict:
    """The air the board's parts sit in: {c, min_c, src}. constraints
    `ambient_c`, else the enclosure's inside air as check_thermal works it
    out (room_c plus the thermal entries' heat through the walls), else
    25 C, said so in `src`. `ambient_min_c` is the coldest it starts in
    (None: the cold end is not checked)."""
    cons = cons or {}
    lo = cons.get("ambient_min_c")
    lo = float(lo) if isinstance(lo, (int, float)) else None
    if isinstance(cons.get("ambient_c"), (int, float)):
        return {"c": float(cons["ambient_c"]), "min_c": lo,
                "src": "constraints ambient_c"}
    if isinstance(cons.get("enclosure"), dict):
        try:
            import check_thermal
            heat = sum(float(e.get("power_w", 0) or 0)
                       for e in cons.get("thermal") or [])
            air, _, _ = check_thermal.enclosure_air(
                cons["enclosure"], heat, cons_dir or Path("."))
            return {"c": round(float(air), 1), "min_c": lo,
                    "src": "enclosure air (check_thermal)"}
        except Exception as exc:  # noqa: BLE001
            room = cons["enclosure"].get("room_c", 30)
            if isinstance(room, (int, float)):
                return {"c": float(room), "min_c": lo,
                        "src": f"enclosure room_c (box air not worked out: "
                               f"{str(exc)[:80]})"}
    return {"c": DEFAULT_AMBIENT_C, "min_c": lo,
            "src": "default 25 C: constraints carry no ambient_c"}


def _attr(bom: dict | None, rx: re.Pattern) -> str | None:
    for a in (bom or {}).get("attributes") or []:
        if rx.search(str(a.get("name") or "")):
            return str(a.get("value") or "")
    return None


def is_fuse(b: dict, bom: dict | None) -> bool:
    if len(set(b["pads"].values())) != 2 and not re.match(r"^F\d", b["ref"]):
        return False
    return bool(re.match(r"^(?:F|FU)\d", b["ref"])) or \
        _attr(bom, HOLD_ATTR_RE) is not None


def is_pptc(bom: dict | None) -> bool:
    if _attr(bom, HOLD_ATTR_RE) is not None:
        return True
    text = " ".join(str((bom or {}).get(k) or "") for k in
                    ("category", "description", "mpn", "name", "value"))
    return bool(PPTC_TEXT_RE.search(text))


def fuse_findings(board: list[dict], bom_by_lcsc: dict, volts: dict,
                  vsrc: dict, currents: dict, amb: dict, emit,
                  checked: dict, unrated_passives: list[str]) -> list[dict]:
    """Fuses and PPTCs (F*, or a parts.json hold-current attribute) at the
    board's ambient (R-RAT-3, R-RAT-4). The current is the larger
    constraints power[].current_a of its two nets; its voltage is the
    larger rail it sits on, against ground: a fuse that opens must hold off
    the circuit's voltage, not the millivolts across it while it conducts.
      fuse: I_op over 0.75 x k_T x I_rated warns (it ages open), over
            k_T x I_rated is an error;
      PPTC: Ihold(T) = Ihold x the typical derating table, under I_op is an
            error (it trips in normal running), under 1.1 x I_op warns;
      either: a voltage rating (Vmax) under the rail is an error."""
    t = amb["c"]
    out = []
    for b in sorted(board, key=lambda x: x["ref"]):
        bom = bom_by_lcsc.get(b["lcsc"])
        if not b["pads"] or not is_fuse(b, bom):
            continue
        ref, nets = b["ref"], sorted(set(b["pads"].values()))
        pptc = is_pptc(bom)
        kind = "PPTC" if pptc else "fuse"
        irated = engcalc.parse_amps(_attr(bom, HOLD_ATTR_RE) if pptc else
                                    _attr(bom, FUSE_I_ATTR_RE))
        vrated = engcalc.parse_volts(_attr(bom, FUSE_V_ATTR_RE))
        iop = max((currents[n] for n in nets if n in currents), default=None)
        vs = [abs(volts[n]) for n in nets if n in volts]
        vop = max(vs) if vs else None
        fact = {"ref": ref, "kind": kind, "rated_a": irated,
                "rated_v": vrated, "op_a": iop, "op_v": vop, "ambient_c": t}
        out.append(fact)
        if irated is None and vrated is None:
            unrated_passives.append(ref)
            continue
        net = nets[0]
        did = False
        if vrated is not None and vop is not None:
            did = True
            if vop > vrated + EPS:
                emit("rating_over_voltage", "error", ref, net,
                     f"{ref} ({kind}) is rated {_fmt(vrated)} V but sits on "
                     f"{_fmt(vop)} V ({'/'.join(vsrc[n] for n in nets if n in vsrc)}): "
                     "once it opens it must hold off the rail [parts.json]",
                     limit_v=vrated, net_v=vop, rating="parts.json")
        if irated is not None and iop is not None:
            did = True
            if pptc:
                k = engcalc.pptc_hold_factor(t)
                hold = irated * k
                fact.update(hold_at_ambient_a=round(hold, 4), k=round(k, 3))
                why = (f"{ref} (PPTC) holds {_fmt(irated)} A at 23 C, "
                       f"{_fmt(hold)} A at {_fmt(t)} C ({amb['src']}; x"
                       f"{k:.2f}, typical Bourns/Littelfuse derating - the "
                       f"part's own table wins), against {_fmt(iop)} A on "
                       f"{'/'.join(n for n in nets if n in currents)} "
                       "(constraints power[])")
                if iop > hold + EPS:
                    emit("rating_over_current", "error", ref, net,
                         why + ": it trips in normal running", limit_a=hold,
                         current_a=iop, ambient_c=t)
                elif iop * engcalc.PPTC_MARGIN > hold + EPS:
                    emit("rating_over_current", "warning", ref, net,
                         why + ": under the 10 % hold margin", limit_a=hold,
                         current_a=iop, ambient_c=t)
            else:
                k = engcalc.fuse_rerate(t)
                cap = irated * k
                use = cap * engcalc.FUSE_LOAD_FRACTION
                fact.update(rerated_a=round(cap, 4), k=round(k, 3))
                why = (f"{ref} (fuse) is rated {_fmt(irated)} A, "
                       f"{_fmt(cap)} A at {_fmt(t)} C ({amb['src']}; k_T "
                       f"{k:.2f}), against {_fmt(iop)} A on "
                       f"{'/'.join(n for n in nets if n in currents)} "
                       "(constraints power[])")
                if iop > cap + EPS:
                    emit("rating_over_current", "error", ref, net,
                         why + ": over its rerated current", limit_a=cap,
                         current_a=iop, ambient_c=t)
                elif iop > use + EPS:
                    emit("rating_over_current", "warning", ref, net,
                         why + f": over 75 % of it ({_fmt(use)} A), where a "
                         "fuse ages and opens on its own", limit_a=use,
                         current_a=iop, ambient_c=t)
        if did:
            checked[ref] = checked.get(ref, 0) + 1
    return out


def temperature_findings(board: list[dict], bom_by_lcsc: dict, exts: dict,
                         amb: dict, emit) -> dict:
    """Every part's operating temperature range (parts.json "Operating
    Temperature", else the extraction's `operating_temp_c` [lo, hi])
    against the board's ambient (R-REL-2): an ambient above the part's
    maximum, or an `ambient_min_c` below its minimum, is an error; a range
    LCSC gives for the junction only (@(Tj)) is held against the air too, a
    bound the air must not pass. The part's own rise comes on top
    (check_thermal), so passing here is necessary, not sufficient."""
    t, lo_amb = amb["c"], amb["min_c"]
    checked, ranges = 0, {}
    for b in sorted(board, key=lambda x: x["ref"]):
        if not b["pads"]:
            continue
        rng = engcalc.parse_temp_range(_attr(bom_by_lcsc.get(b["lcsc"]),
                                             TEMP_ATTR_RE))
        src = "parts.json Operating Temperature"
        ot = (exts.get(b["lcsc"]) or {}).get("operating_temp_c")
        if rng is None and isinstance(ot, list) and len(ot) == 2 and \
                all(isinstance(x, (int, float)) for x in ot):
            rng, src = (float(ot[0]), float(ot[1]), False), "extraction"
        if rng is None:
            continue
        lo, hi, tj = rng
        checked += 1
        ranges[f"{_fmt(lo)}..{_fmt(hi)}"] = ranges.get(
            f"{_fmt(lo)}..{_fmt(hi)}", 0) + 1
        what = "junction " if tj else ""
        if t > hi + EPS:
            emit("rating_over_temperature", "error", b["ref"], None,
                 f"{b['ref']} operates to {_fmt(hi)} C ({what}max, {src}) "
                 f"but the board's air is {_fmt(t)} C ({amb['src']})",
                 limit_c=hi, ambient_c=t, rating=src)
        elif lo_amb is not None and lo_amb < lo - EPS:
            emit("rating_over_temperature", "error", b["ref"], None,
                 f"{b['ref']} operates down to {_fmt(lo)} C ({src}) but the "
                 f"board starts at {_fmt(lo_amb)} C (constraints "
                 "ambient_min_c)", limit_c=lo, ambient_c=lo_amb, rating=src)
    return {"parts_checked": checked, "ranges": dict(sorted(ranges.items()))}


VR_ATTR_RE = re.compile(r"reverse.*volt|\(vr\)|\bvrrm\b", re.I)
INPUT_CONN_RE = re.compile(r"^(?:J|P|CN|CON|X|BT)\d")
SERIES_RE = re.compile(r"^(?:R|L|FB)\d")


def net_pinfunctions(pcb: Path) -> dict[str, dict[str, str]]:
    """{ref: {pad: pinfunction}} from the schematic netlist beside the board
    (<board>.net, else the only *.net there): a board written without
    pinfunctions still says which diode pad is the cathode."""
    cands = [pcb.with_suffix(".net")]
    if not cands[0].is_file():
        cands = sorted(pcb.parent.glob("*.net"))
        if len(cands) != 1:
            return {}
    try:
        root = sexpdata.loads(cands[0].read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict[str, str]] = {}
    for nets in _kids(root, "nets"):
        for n in _kids(nets, "net"):
            for node in _kids(n, "node"):
                f = {_t(k[0]): k[1] for k in node[1:]
                     if isinstance(k, list) and len(k) > 1}
                if f.get("ref") and f.get("pin") and f.get("pinfunction"):
                    out.setdefault(str(f["ref"]), {})[str(f["pin"])] = \
                        str(f["pinfunction"])
    return out


def diode_reverse(b: dict, ext: dict | None, bom: dict | None,
                  netfns: dict) -> dict | None:
    """A 2-terminal diode's reverse-voltage rating and its pads:
    {k, a, vr, src, how}. The rating is the extraction's reverse row (rated
    K against A) or a parts.json reverse-voltage attribute; the cathode is
    the extraction's K pin, else the pad named K/cathode, else pad 1 (the
    KiCad diode and LED convention, said in `how`). None for anything else."""
    if not re.match(r"^(?:D|LED)\d", b["ref"]) or len(b["pads"]) != 2:
        return None
    vr = src = None
    k = a = None
    how = "pinout"
    if ext:
        idx = pin_index(ext.get("pinout") or [])
        ks, as_ = idx.get("K") or [], idx.get("A") or []
        if ks and as_:
            k, a = ks[0]["pin"], as_[0]["pin"]
            for r in part_ratings(ext).get(k, {}).get(("voltage", "abs"), []):
                if r["ref"] == as_[0]["name"] and isinstance(r["hi"], float):
                    if vr is None or r["hi"] < vr:
                        vr, src = r["hi"], r["src"]
    if vr is None:
        for att in (bom or {}).get("attributes") or []:
            if VR_ATTR_RE.search(str(att.get("name") or "")):
                m = re.search(r"(\d+(?:\.\d+)?)\s*(k?)V",
                              str(att.get("value") or ""))
                if m:
                    vr = float(m.group(1)) * (1000 if m.group(2) else 1)
                    src = f"parts.json {att.get('name')} = {att.get('value')}"
    if vr is None:
        return None
    if k is None:
        fns = dict(netfns.get(b["ref"]) or {}, **b.get("fns") or {})
        for pad, fn in fns.items():
            name = re.sub(r"_\d+$", "", fn.upper())
            if name in ("K", "CATHODE"):
                k = pad
            elif name in ("A", "ANODE"):
                a = pad
        how = "pad names"
        if k is None or a is None:
            k, a, how = "1", "2", "pad 1 taken as the cathode (KiCad's " \
                "diode convention)"
    if k not in b["pads"] or a not in b["pads"]:
        return None
    return {"k": k, "a": a, "vr": vr, "src": src, "how": how}


def reversed_input_volts(board: list[dict], volts: dict, vsrc: dict,
                         rail: str) -> dict[str, float]:
    """Net volts with a power input plugged in reversed: its `rail` pin sits
    at minus the rail's volts against the board's ground. A net reached from
    it through one series part (R, L, FB) and touched by no other series
    part takes its volts (a reverse-biased diode passes no current, so the
    resistor drops nothing); every other net is unknown."""
    rev = {n: 0.0 for n in volts if is_ground(n)}
    rev[rail] = -volts[rail]
    series: dict[str, list[str]] = {}
    for b in board:
        nets = sorted(set(b["pads"].values()))
        if SERIES_RE.match(b["ref"]) and len(nets) == 2:
            for x, y in ((nets[0], nets[1]), (nets[1], nets[0])):
                series.setdefault(x, []).append(y)
    grew = True
    while grew:
        grew = False
        for x, ys in series.items():
            if x in rev or len(ys) != 1 or ys[0] not in rev or \
                    vsrc.get(x, "pull-up") != "pull-up":
                continue
            rev[x] = rev[ys[0]]
            grew = True
    return rev


def input_connectors(board: list[dict], volts: dict) -> list[tuple]:
    """(connector, rail net) for each 2-net power input: a J/P/CN/X/BT part
    whose pads sit on one ground net and one positive rail and nothing else -
    a header, jack or battery that can be plugged in the wrong way round."""
    out = []
    for b in board:
        nets = set(b["pads"].values())
        if not INPUT_CONN_RE.match(b["ref"]) or len(nets) != 2:
            continue
        gnd = [n for n in nets if is_ground(n)]
        rails = [n for n in nets if not is_ground(n)
                 and (volts.get(n) or 0) > 0]
        if len(gnd) == 1 and len(rails) == 1:
            out.append((b, rails[0]))
    return out


def _pin_name(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(s or "").upper())


def pinout_fact(b: dict, ext: dict | None) -> tuple[str, str]:
    """(status, value) for a part's pin map: the board's pad numbers must all
    be pins of the datasheet extraction, and where the pad carries a pin
    name, it must be that pin's name (or an alias of it). A name that
    disagrees is unknown, not failed: the two may spell one pin differently,
    and a reader has to look."""
    if ext is None or not ext.get("pinout"):
        return "unknown", "no datasheet extraction with a pinout"
    pins = {str(p.get("pin")): p for p in ext["pinout"]}
    nums = [n for n in b.get("nums") or [] if n]
    missing = [n for n in nums if n not in pins and n.upper() != "EP"
               and not (n == str(len(pins) + 1)
                        and (ext.get("exposed_pad") or {}).get("present"))]
    if missing:
        return "failed", (f"board pad(s) {', '.join(missing[:8])} are not "
                          f"pins of the {ext.get('mpn')} extraction")
    differ = []
    for n, fn in sorted((b.get("fns") or {}).items()):
        p = pins.get(n)
        if p is None or not fn:
            continue
        a, c = _pin_name(fn), _pin_name(p.get("name"))
        if a and c and not (a in c or c in a or aliases(fn) & aliases(
                str(p.get("name")))):
            differ.append(f"{n}: board {fn} / datasheet {p.get('name')}")
    if differ:
        return "unknown", ("pin names differ, check by eye: "
                           + "; ".join(differ[:6]))
    named = sum(1 for n in nums if (b.get("fns") or {}).get(n))
    return "verified", (f"{len(nums)} pads are pins of the extraction"
                        + (f", {named} named alike" if named else ""))


def ledger_facts(board: list[dict], exts: dict, vios: list[dict],
                 checked: dict[str, int], bom_by_lcsc: dict) -> list[dict]:
    """rating and pinout facts per refdes (lib/ledgerlib.py)."""
    failed: dict[str, str] = {}
    unrated: dict[str, str] = {}
    for v in vios:
        for ref in v.get("refs") or []:
            if v.get("severity") == "error":
                failed.setdefault(ref, v.get("msg") or v.get("message", ""))
            elif v.get("kind") == "rating_unrated":
                unrated.setdefault(ref, (
                    v.get("msg") or "") if len(v.get("refs") or []) == 1
                    else "no datasheet extraction and no voltage-rated "
                         "attribute in parts.json")
    out = []
    for b in board:
        ref = b["ref"]
        ext = exts.get(b["lcsc"])
        src = ((ext or {}).get("source_pdf")
               or (f"parts/{b['lcsc']}.json (datasheet extraction)"
                   if ext else f"parts.json attributes of {b['lcsc']}"))
        if ref in failed:
            st, val = "failed", failed[ref]
        elif ref in unrated:
            st, val = "unknown", unrated[ref]
        elif checked.get(ref):
            st, val = "verified", (f"{checked[ref]} connected pin(s) within "
                                   "their datasheet rating at the board's "
                                   "rail voltages")
        else:
            st, val = "unknown", ("no pin of it sits on a net of known "
                                  "voltage" if b["pads"] else "no connected pad")
        out.append({"subject": ref, "kind": "rating", "status": st,
                    "value": val, "source": src if st != "unknown" else "",
                    "lcsc": b["lcsc"]})
        pst, pval = pinout_fact(b, ext)
        out.append({"subject": ref, "kind": "pinout", "status": pst,
                    "value": pval, "lcsc": b["lcsc"],
                    "source": src if pst != "unknown" else ""})
    return out


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
    import ledgerlib
    # a fact someone else (a person, `ledger.py set`) recorded verified is
    # evidence this check lacks: it is not re-warned and not overwritten
    # unless the check finds it actually failed (#429)
    held = {k: f for k, f in ledgerlib.load(ws)["facts"].items()
            if f.get("kind") in ("rating", "pinout")
            and f.get("status") == "verified" and f.get("by") != SCRIPT} \
        if ledgerlib.workspace_of(pcb) else {}
    trusted = {f["subject"] for f in held.values() if f["kind"] == "rating"}
    viol, facts = run_check(pcb, parts, cons, tree,
                            Path(args.constraints).resolve().parent
                            if args.constraints else None, trusted=trusted)
    lf = facts.pop("ledger_facts")
    for f in lf:
        h = held.get(ledgerlib.key(f["subject"], f["kind"]))
        if h is not None and f["status"] != "failed":
            f.update(status="verified", value=h.get("value", ""),
                     source=h.get("source", ""), held_by=h.get("by"))
    facts["held_verified"] = sorted(held)
    if ledgerlib.writable(ws):
        exp = ledgerlib.expected(ws)
        led = ledgerlib.put(ws, [f for f in lf if ledgerlib.key(
            f["subject"], f["kind"]) in exp and "held_by" not in f], SCRIPT)
        facts["ledger"] = str(led) if led else None
    facts["ledger_counts"] = {
        k: {st: sum(1 for f in lf if f["kind"] == k and f["status"] == st)
            for st in ledgerlib.STATUSES} for k in ("rating", "pinout")}
    return checklib.report(SCRIPT, pcb, viol, **facts), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
