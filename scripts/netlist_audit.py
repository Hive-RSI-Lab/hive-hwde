"""netlist_audit.py - netlist vs constraints.json cross-check (SPEC P4).

Audits an exported KiCad netlist (kicadsexpr format) against the design's
constraints.json (and optionally the S7-emitted decoupling.json), catching
schematic-level structural problems BEFORE board work starts:

 - missing_net (error): a net referenced anywhere in constraints.json
   (high_speed[].net + reference nets, power[].net, voltages[].net,
   diff_pairs[].p/.n, thermal[].net) does not exist in the netlist.
 - missing_ref (error): a refdes referenced by constraints.json
   (placement edges/groups/fixed/separation, thermal[].ref) that is not a
   netlist component - a P4 refdes rename/split would otherwise silently
   mute the constraint (place_anneal drops unknown refs; ladder row 86).
 - diffpair_naming (warning): an explicit diff_pairs entry whose p/n names
   do not follow a recognized differential suffix convention (_P/_N, DP/DM,
   D+/D-; check_diffpair.py's conservative families).
 - diffpair_unpaired (warning): a netlist net carrying a strong differential
   suffix whose partner net is absent.
 - power_no_consumers (warning): a declared power net with no power_in pin
   AND no series escape - a 2-pin member whose far pin leaves the rail to a
   non-ground net (passive loads - LEDs, droppers, fuses - never carry
   pintype power_in; such rails are recorded as passive_fed, not warned).
 - pin_no_net (warning): a symbol pin (per the export's own libparts/units
   inventory) that appears in NO net, not even an unconnected-* singleton.
   Hierarchical exports DROP no-connect singleton nets, so these pads reach
   board_init with no net at all and nothing else reports them (row 68).
 - power_undeclared (warning): a net feeding power_in pins that is neither
   declared (power[] / voltages[]) nor ground-named (GND*/;*GND/VSS*/0V).
 - dangling_net (warning): a single-pin net not named unconnected-* - the
   classic label-typo signature (label misspelled on one stub).
 - ground_island (warning): a ground-named net (not chassis/earth/shield)
   with no power pin on it and no R/L/bead/jumper/net tie to another
   ground net - a local label where the ground symbol belonged.
 - pin_net_swap (error): a net plainly named SDA/SCL, D+/D-, CANH/CANL on a
   pin whose name is its partner - the pair crossed at one part.
 - sot23_pinout (warning): a stock Package_TO_SOT_SMD SOT-23 transistor
   whose symbol pins 1-2-3 are not G-S-D (FET) or B-E-C (BJT), the order
   nearly every SOT-23 part has; the datasheet is the last word.
 - input_common_mode (warning): an op-amp or comparator in
   reference/opamp_cm.yaml with an input whose DC level - a rail, or
   solved from the resistors around it to rails of known volts (net name
   or constraints `voltages`; caps open, connectors unplugged, any driven
   pin on the way makes it unknown and skipped) - sits outside the part's
   input common-mode range on its own supply pins. The output is undefined
   there (errata corpus: minuet v3 LMV331, inputs at 3.1 V, range to 2.6 V).
 - below_min_stable_gain (warning): a decompensated op-amp in
   reference/opamp_min_gain.yaml (OPA637, OPA228, an uncompensated NE5534)
   whose noise gain - 1/beta, the share of the output its resistors and
   capacitors feed back to the inverting input, read at GBW / min gain
   with every source zeroed - is under the part's minimum stable gain: a
   follower (1) or an inverting -1 stage (2) around a gain-5 part rings.
 - pin_part_missing: a part reference/pin_requirements.yaml says the IC's
   datasheet requires between two of its pins (CBK from FB_BK to GND_BK)
   that no part of that kind and value spans (error), or, in a
   hierarchical design, that only parts drawn on other sheets span - the
   rail's caps belong to other parts and none was drawn for this pin
   (warning: confirm one sits at the pin on the board).
 - metadata_mismatch (error): a decoupling association that contradicts the
   netlist (cap/ic missing, IC pin not on the stated rail, cap not spanning
   rail+gnd); value drift cap-vs-netlist is a warning.

Warnings alone do NOT fail the audit (fp_verify.py precedent): exit 1 only
on error-severity findings. Violations use the S2 normalized schema
(source "audit.netlist") so cluster_violations.py / gates merge them.

Compare mode (--compare OTHER.net): strict electrical-identity diff of two
netlists - same net names, same (ref, pin) memberships. Any difference
(missing/extra net, membership diff, rename) is an error violation
(kind netlist_diff). Used by the S7 acceptance (regenerated schematic must
be electrically identical to the golden) and by regen guards after
generator refactors.

CLI:
  netlist_audit.py --netlist X.net --constraints c.json
                   [--decoupling d.json] [--out report.json]
  netlist_audit.py --sch Y.kicad_sch --constraints c.json   (exports first)
  netlist_audit.py --netlist A.net --compare B.net [--out report.json]
Exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

import sexpdata

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))

import checklib  # noqa: E402
import engcalc  # noqa: E402

SCRIPT = "netlist_audit"
SOURCE = "audit.netlist"
PIN_REQUIREMENTS = HERE.parent / "reference" / "pin_requirements.yaml"

# Strong differential suffix families (subset of check_diffpair.SUFFIX_PAIRS:
# the bare +/- and P/N tokens are too ambiguous for unpaired-net warnings).
STRONG_SUFFIXES = [("_P", "_N"), ("DP", "DM"), ("D+", "D-")]

_GROUND_RE = re.compile(r"(^GND|GND$|^VSS|^0V$)")


# ------------------------------------------------------------ netlist parse

def _kids(node, key):
    return [x for x in node if isinstance(x, list) and x
            and str(x[0]) == key]


def _atom(node, key, default=None):
    for x in node:
        if isinstance(x, list) and x and str(x[0]) == key:
            return str(x[1]) if len(x) > 1 else default
    return default


def _pin_name(pin: str | None, func: str) -> str:
    """The pin's own name: kicad-cli writes a hidden or doubled name as
    '<name>_<number>' (S_3 for pin 3 named S)."""
    suffix = f"_{pin}"
    return func[:-len(suffix)] if pin and func.endswith(suffix) else func


def parse_netlist(path: Path | str) -> dict:
    """kicadsexpr netlist -> {"components": {ref: {value, footprint,
    libsource?, unit_pins?}}, "nets": {name: [{ref, pin, pintype}]},
    "libparts": {(lib, part): set(pin numbers)}}. Multiline or single-line
    formatting both parse (sexpdata, not regex - kicad-cli 10 pretty-prints
    netlists). unit_pins (kicad 10 per-comp units block) is the placed
    instance's own pin inventory; libparts is the shared per-symbol union
    (all units) used as the fallback."""
    p = Path(path)
    try:
        with open(p, encoding="utf-8") as fh:
            data = sexpdata.load(fh)
    except OSError as exc:
        raise checklib.CheckError(f"cannot read netlist {p}: {exc}") from exc
    except Exception as exc:
        raise checklib.CheckError(f"netlist {p} does not parse: {exc}") from exc
    if not isinstance(data, list) or not data or str(data[0]) != "export":
        raise checklib.CheckError(f"netlist {p}: not a kicadsexpr export")

    def _pin_nums(node) -> set[str]:
        out = set()
        for pblk in _kids(node, "pins"):
            for pin in _kids(pblk, "pin"):
                num = _atom(pin, "num")
                if num:
                    out.add(num)
        return out

    comps: dict[str, dict] = {}
    for blk in _kids(data, "components"):
        for c in _kids(blk, "comp"):
            ref = _atom(c, "ref")
            if not ref:
                continue
            comps[ref] = {"value": _atom(c, "value"),
                          "footprint": _atom(c, "footprint")}
            sp = _kids(c, "sheetpath")
            if sp and _atom(sp[0], "names"):
                comps[ref]["sheet"] = _atom(sp[0], "names")
            src = _kids(c, "libsource")
            if src:
                comps[ref]["libsource"] = (_atom(src[0], "lib") or "",
                                           _atom(src[0], "part") or "")
            unit_pins: set[str] = set()
            for ub in _kids(c, "units"):
                for u in _kids(ub, "unit"):
                    unit_pins |= _pin_nums(u)
            if unit_pins:
                comps[ref]["unit_pins"] = sorted(unit_pins)
    libparts: dict[tuple, set] = {}
    for blk in _kids(data, "libparts"):
        for lp in _kids(blk, "libpart"):
            key = (_atom(lp, "lib") or "", _atom(lp, "part") or "")
            libparts.setdefault(key, set()).update(_pin_nums(lp))
    nets: dict[str, list] = {}
    for blk in _kids(data, "nets"):
        for n in _kids(blk, "net"):
            name = _atom(n, "name")
            if name is None:
                continue
            nets[name] = [{"ref": _atom(nd, "ref"), "pin": _atom(nd, "pin"),
                           "pintype": _atom(nd, "pintype", ""),
                           "pinfunction": _pin_name(_atom(nd, "pin"),
                                                    _atom(nd, "pinfunction", ""))}
                          for nd in _kids(n, "node")]
    return {"components": comps, "nets": nets, "libparts": libparts}


def _memberships(parsed: dict) -> dict[str, tuple]:
    return {name: tuple(sorted((m["ref"], m["pin"]) for m in members))
            for name, members in parsed["nets"].items()}


def _base_type(pintype: str) -> str:
    return (pintype or "").split("+", 1)[0]


def _is_ground_name(net: str) -> bool:
    return bool(_GROUND_RE.search(net.lstrip("/").upper()))


# ground-named nets that are meant to sit apart (chassis, earth, shields)
_GROUND_EXEMPT_RE = re.compile(r"CHASSIS|EARTH|SHIELD|SHLD|PE$|FG$")
# two-terminal parts that join two grounds (net ties, 0R, beads, jumpers)
_BRIDGE_REF_RE = re.compile(r"^(R|L|FB|NT|JP|SB|J?P)\d")


def _ground_bridges(net: str, members: list[dict],
                    comp_pins: dict[str, dict[str, str]]) -> list[str]:
    """Members that tie this ground net to another ground net: a 2-pin
    R/L/FB/NT/JP whose far pin is ground-named, or any part with a pin on
    another ground net and a net-tie footprint."""
    out = []
    for m in members:
        pins = comp_pins.get(m["ref"], {})
        far = [n for p, n in pins.items() if p != m["pin"] and n != net]
        if any(_is_ground_name(n) for n in far) and (
                _BRIDGE_REF_RE.match(m["ref"] or "") or len(pins) == 2
                and not (m["ref"] or "").startswith("C")):
            out.append(m["ref"])
    return out


# bus signals that never cross: the net's name and the pin's name agree
_BUS_PARTNER = {"SDA": "SCL", "SCL": "SDA", "DP": "DM", "DM": "DP",
                "CANH": "CANL", "CANL": "CANH"}
_BUS_ALIASES = {"D+": "DP", "D-": "DM", "USB_DP": "DP", "USB_DM": "DM",
                "USB_D+": "DP", "USB_D-": "DM", "CAN_H": "CANH",
                "CAN_L": "CANL", "I2C_SDA": "SDA", "I2C_SCL": "SCL"}


def _bus_token(name: str) -> str | None:
    """SDA/SCL/DP/DM/CANH/CANL when a net or pin name is plainly that
    signal: the whole name or its last /._- separated part (CTL.SDA,
    /Audio/I2C_SCL, USB_D+), else None."""
    if not name:
        return None
    tail = re.split(r"[/.]", name.upper())[-1]
    for cand in (tail, tail.rsplit("_", 1)[-1]):
        cand = _BUS_ALIASES.get(cand, cand)
        if cand in _BUS_PARTNER:
            return cand
    return None


_STOCK_SOT23_RE = re.compile(r"^Package_TO_SOT_SMD:SOT-23(-3)?(_|$)")
_SOT23_ORDERS = (("G", "S", "D"), ("B", "E", "C"))


def _transistor_role(func: str | None) -> str | None:
    f = (func or "").upper()
    return {"G": "G", "GATE": "G", "S": "S", "SOURCE": "S", "D": "D",
            "DRAIN": "D", "B": "B", "BASE": "B", "E": "E", "EMITTER": "E",
            "C": "C", "COLLECTOR": "C"}.get(f)


def load_pin_requirements(path: Path = PIN_REQUIREMENTS) -> list[dict]:
    """reference/pin_requirements.yaml's parts (its header says the form)."""
    import yaml  # noqa: PLC0415
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise checklib.CheckError(f"pin requirements {path}: {exc}") from exc
    return doc.get("parts") or []


def _norm_pin(name: str | None) -> str:
    return re.sub(r"~\{([^}]*)\}", r"\1", name or "").strip().upper()


_KIND_PREFIX = {"C": "C", "L": "L", "FB": "L", "R": "R"}


def _part_kind(ref: str) -> str | None:
    m = re.match(r"^([A-Z]+)\d", ref or "")
    return _KIND_PREFIX.get(m.group(1)) if m else None


def _req_window(req: dict) -> tuple[float | None, float | None]:
    """(min, max) a part's value must sit in: min/max as given, else one
    E3 step either side of the nominal value, else no bound."""
    lo, hi = req.get("min"), req.get("max")
    nom = engcalc.parse_value(req.get("value"))
    if nom and lo is None and hi is None:
        lo, hi = nom / 2.2, nom * 2.2
    return (float(lo) if lo is not None else None,
            float(hi) if hi is not None else None)


def _pin_requirements(nets: dict, comps: dict,
                      comp_pins: dict[str, dict[str, str]],
                      specs: list[dict]):
    """Yield (severity, net, ic, req, found, elsewhere, off_value) per
    datasheet-required pin part that is missing or not drawn with its IC."""
    hierarchical = len({c.get("sheet") for c in comps.values()} - {None}) > 1
    for spec in specs:
        rx = re.compile(spec["match"], re.I)
        aliases = {k.upper(): v.upper()
                   for k, v in (spec.get("aliases") or {}).items()}
        for ref in sorted(comps):
            comp = comps[ref]
            if not (rx.search(comp.get("value") or "")
                    or rx.search((comp.get("libsource") or ("", ""))[1])):
                continue
            by_pin: dict[str, set[str]] = {}
            for name, members in nets.items():
                for m in members:
                    if m["ref"] == ref:
                        pn = _norm_pin(m.get("pinfunction"))
                        by_pin.setdefault(aliases.get(pn, pn), set()).add(name)
            for req in spec.get("requires") or []:
                a = by_pin.get(req["pin"].upper())
                b = by_pin.get(req["to"].upper())
                if a is None or b is None:
                    continue  # this symbol has no such pin
                a = {n for n in a if not n.startswith("unconnected-")}
                b = {n for n in b if not n.startswith("unconnected-")}
                if not a and req.get("when") == "connected":
                    continue
                if a & b:
                    continue  # the two pins are one net: nothing to span
                lo, hi = _req_window(req)
                found, off = [], []
                for other, pins in sorted(comp_pins.items()):
                    if other == ref or _part_kind(other) not in req["kinds"]:
                        continue
                    if not (any(n in a for n in pins.values())
                            and any(n in b for n in pins.values())):
                        continue
                    val = engcalc.parse_value(comps.get(other, {}).get("value"))
                    if (lo is not None or hi is not None) and (
                            val is None or (lo is not None and val < lo * 0.999)
                            or (hi is not None and val > hi * 1.001)):
                        off.append(other)
                        continue
                    found.append(other)
                net = sorted(a)[0] if a else None
                if not found:
                    yield "error", net, ref, req, [], [], off
                    continue
                sheet = comp.get("sheet")
                if hierarchical and sheet and not any(
                        comps.get(f, {}).get("sheet") == sheet for f in found):
                    yield "warning", net, ref, req, [], found, off


# ------------------------------------------------------------ compare mode

# ------------------------------------------------------------ input common mode

OPAMP_CM_YAML = HERE.parent / "reference" / "opamp_cm.yaml"
# an amplifier input pin's name: + / -, IN+ / -IN, INA- / +IN2, NI / INV
_AMP_IN_RE = re.compile(r"^(?:[+-]|[+-]IN[A-D1-4]?|IN[A-D1-4]?[+-]|"
                        r"[+-]IN_?[A-D1-4]|IN_?[A-D1-4][+-]|NI|INV)$")
# its supply pins, when the symbol types them passive (Amplifier_Operational
# symbols with every pin passive): V+ / V-, VCC / VEE, VDD / VSS, GND
_AMP_SUPPLY_RE = re.compile(r"^(?:V[+-]|VS[+-]|VCC|VDD|VEE|VSS|GND)$")
_CONNECTOR_RE = re.compile(r"^(J|P|CN|X|CON)\d")
_OPEN_DC_RE = re.compile(r"^(C|TP)\d")
_DC_CLUSTER_MAX = 40


def _cm_table(path: Path = OPAMP_CM_YAML) -> list[dict]:
    import yaml  # noqa: PLC0415
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))["parts"]
    for r in rows:
        r["_re"] = re.compile(r["match"], re.I)
    return rows


def _cm_row(comp: dict, table: list[dict]) -> dict | None:
    names = [comp.get("value") or "", (comp.get("libsource") or ("", ""))[1]]
    for r in table:
        if any(r["_re"].search(n.strip()) for n in names if n):
            return r
    return None


def _rail_volts(constraints: dict, nets: dict) -> dict[str, float]:
    """Volts of every net whose voltage hwde can name without a solve:
    constraints `voltages`, then the net's name (GND = 0, +3.3V, 3V3)."""
    from check_ratings import volts_from_name  # noqa: PLC0415
    out = {}
    for name in nets:
        v = volts_from_name(name)
        if v is not None:
            out[name] = v
    for ent in constraints.get("voltages", []) or []:
        if ent.get("net") in nets and isinstance(ent.get("voltage"),
                                                 (int, float)):
            out[ent["net"]] = float(ent["voltage"])
    return out


def _dc_level(net: str, nets: dict, comps: dict,
              comp_pins: dict[str, dict[str, str]],
              rails: dict[str, float]) -> tuple[float | None, list[str], str]:
    """The DC volts on `net` from the resistors around it: every net reached
    through 2-pin resistors from `net` (stopping at nets of known volts) is
    solved by nodal analysis. Capacitors and test points are open, connector
    pins open (an external part unplugged: listed in the second return),
    input pins high-Z. Any other pin on a reached net (an output, a diode,
    a transistor) makes the level unknown: (None, [], why)."""
    from engcalc import parse_value  # noqa: PLC0415
    unknown: list[str] = []
    edges: list[tuple[str, str, float]] = []
    opened: set[str] = set()
    stamped: set[str] = set()
    todo, seen = [net], {net}
    while todo:
        n = todo.pop()
        unknown.append(n)
        if len(unknown) > _DC_CLUSTER_MAX:
            return None, [], "resistor network too large"
        for m in nets[n]:
            ref = m["ref"] or ""
            pins = comp_pins.get(ref, {})
            if ref.startswith("R") and len(pins) == 2:
                if ref in stamped:
                    continue  # met from its other end: stamped once
                stamped.add(ref)
                ohms = parse_value(comps.get(ref, {}).get("value"))
                if ohms is None:
                    return None, [], f"{ref} has no readable value"
                far = next(nt for p, nt in pins.items() if p != m["pin"])
                if far == n:
                    continue
                edges.append((n, far, max(ohms, 1e-3)))
                if far not in rails and far not in seen:
                    seen.add(far)
                    todo.append(far)
            elif _OPEN_DC_RE.match(ref):
                continue
            elif _CONNECTOR_RE.match(ref):
                opened.add(ref)
            elif _base_type(m["pintype"]) == "input" or _AMP_IN_RE.match(
                    (m.get("pinfunction") or "").upper()):
                continue
            else:
                return None, [], (f"{ref}.{m['pin']} "
                                  f"({m['pintype'] or 'no type'}) on {n}")
    idx = {n: i for i, n in enumerate(unknown)}
    k = len(unknown)
    g = [[0.0] * (k + 1) for _ in range(k)]
    for a, b, ohms in edges:
        y = 1.0 / ohms
        for x, o in ((a, b), (b, a)):
            if x not in idx:
                continue
            g[idx[x]][idx[x]] += y
            if o in idx:
                g[idx[x]][idx[o]] -= y
            else:
                g[idx[x]][k] += y * rails[o]
    # Gauss-Jordan; a node with no path to a known rail is singular
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(g[r][c]))
        if abs(g[piv][c]) < 1e-15:
            return None, [], f"{net} has no resistive path to a known rail"
        g[c], g[piv] = g[piv], g[c]
        for r in range(k):
            if r != c and g[r][c]:
                f = g[r][c] / g[c][c]
                g[r] = [x - f * y for x, y in zip(g[r], g[c])]
    return g[0][k] / g[0][0], sorted(opened), ""


# ------------------------------------------------------------ min stable gain

OPAMP_MIN_GAIN_YAML = HERE.parent / "reference" / "opamp_min_gain.yaml"
# an op-amp's inverting input and its output, with the unit's tag (A, 2)
_AMP_INV_RE = re.compile(r"^(?:-|-IN_?([A-D1-4])?|IN_?([A-D1-4])?-|INV)$")
_AMP_OUT_RE = re.compile(r"^(?:~|V?OUT_?([A-D1-4])?|VO_?([A-D1-4])?|"
                         r"([A-D1-4])OUT)$")
# -in pin -> output pin of the standard single / dual / quad pinouts
_STD_LOOPS = ({"2": "6"}, {"2": "1", "6": "7"},
              {"2": "1", "6": "7", "9": "8", "13": "14"})
_DRIVEN_TYPES = ("output", "power_out", "power_in", "tri_state")


def _min_gain_table(path: Path = OPAMP_MIN_GAIN_YAML) -> list[dict]:
    import yaml  # noqa: PLC0415
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))["parts"]
    for r in rows:
        r["_re"] = re.compile(r["match"], re.I)
        # YAML 1.1 reads 80.0e6 (no exponent sign) as a string
        r["min_gain"], r["gbw_hz"] = float(r["min_gain"]), float(r["gbw_hz"])
    return rows


def _amp_loops(types: dict[str, dict]) -> list[tuple[str, str]]:
    """(inverting input pin, output pin) of each amplifier in one part:
    the only pair, else paired by unit tag (INA-/OUTA), else by the
    standard single, dual or quad op-amp pinout."""
    def tag(rx, m):
        hit = rx.match((m.get("pinfunction") or "").upper())
        return next((g for g in hit.groups() if g), "") if hit else None
    invs = {p: tag(_AMP_INV_RE, m) for p, m in types.items()
            if tag(_AMP_INV_RE, m) is not None}
    outs = {p: tag(_AMP_OUT_RE, m) for p, m in types.items()
            if tag(_AMP_OUT_RE, m) is not None
            or (_base_type(m["pintype"]) == "output"
                and not _AMP_SUPPLY_RE.match((m.get("pinfunction")
                                              or "").upper()))}
    if len(invs) == 1 and len(outs) == 1:
        return [(next(iter(invs)), next(iter(outs)))]
    pairs = [(i, o) for i, ti in invs.items() for o, to in outs.items()
             if ti and ti == to]
    if len(pairs) == len(invs):
        return sorted(pairs)
    for std in _STD_LOOPS:
        if set(std) == set(invs) and set(std.values()) <= set(outs):
            return sorted(std.items())
    return []


def _noise_gain(inv: str, out: str, own: str, f_hz: float, nets: dict,
                comps: dict, comp_pins: dict[str, dict[str, str]],
                rails: dict[str, float]) -> tuple[float | None, str]:
    """|1/beta| at f_hz: the output net driven with 1 V and every source
    zeroed, the inverse of the share that comes back to the inverting input
    through the resistors and capacitors around it. Rails, nets with a
    driven pin (an output, a supply pin) and connector pins (a source
    plugged in) are AC ground; inputs and test points are open. Any other
    pin on the way (a diode, a transistor, a bead) makes it unknown:
    (None, why). No passive path back from the output is no feedback here
    (open loop, a comparator, or a loop through another stage): (None, why)."""
    import cmath  # noqa: PLC0415
    from engcalc import parse_value  # noqa: PLC0415
    if inv == out:
        return 1.0, ""
    jw = 2j * cmath.pi * f_hz
    fixed: dict[str, complex] = {out: 1.0}
    unknown: list[str] = []
    edges: list[tuple[str, str, complex]] = []
    stamped: set[str] = set()
    todo, seen = [inv], {inv, out}
    while todo:
        n = todo.pop()
        members = nets[n]
        if n != inv and (n in rails or any(
                _CONNECTOR_RE.match(m["ref"] or "")
                or _base_type(m["pintype"]) in _DRIVEN_TYPES
                or (m["ref"] == own and _AMP_OUT_RE.match(
                    (m.get("pinfunction") or "").upper()))
                for m in members)):
            fixed[n] = 0.0
            continue
        unknown.append(n)
        if len(unknown) > _DC_CLUSTER_MAX:
            return None, "feedback network too large"
        for m in members:
            ref = m["ref"] or ""
            pins = comp_pins.get(ref, {})
            kind = _part_kind(ref) if len(pins) == 2 else None
            if kind in ("R", "C"):
                if ref in stamped:
                    continue
                stamped.add(ref)
                val = parse_value(comps.get(ref, {}).get("value"))
                if val is None:
                    return None, f"{ref} has no readable value"
                far = next(nt for p, nt in pins.items() if p != m["pin"])
                if far == n:
                    continue
                y = (1.0 / max(val, 1e-3) if kind == "R" else jw * val)
                edges.append((n, far, y))
                if far not in seen:
                    seen.add(far)
                    todo.append(far)
            elif _OPEN_DC_RE.match(ref) or ref == own:
                continue
            elif _base_type(m["pintype"]) == "input" or _AMP_IN_RE.match(
                    (m.get("pinfunction") or "").upper()):
                continue
            else:
                return None, (f"{ref}.{m['pin']} "
                              f"({m['pintype'] or 'no type'}) on {n}")
    if not any(out in (a, b) for a, b, _ in edges):
        return None, "no passive feedback from the output"
    idx = {n: i for i, n in enumerate(unknown)}
    k = len(unknown)
    g = [[0j] * (k + 1) for _ in range(k)]
    for a, b, y in edges:
        for x, o in ((a, b), (b, a)):
            if x not in idx:
                continue
            g[idx[x]][idx[x]] += y
            if o in idx:
                g[idx[x]][idx[o]] -= y
            else:
                g[idx[x]][k] += y * fixed[o]
    for c in range(k):
        piv = max(range(c, k), key=lambda r: abs(g[r][c]))
        if abs(g[piv][c]) < 1e-15:
            return None, f"{inv} has no path to a fixed node"
        g[c], g[piv] = g[piv], g[c]
        for r in range(k):
            if r != c and g[r][c]:
                f = g[r][c] / g[c][c]
                g[r] = [x - f * y for x, y in zip(g[r], g[c])]
    beta = abs(g[0][k] / g[0][0])
    if beta < 1e-9:
        return None, "no signal fed back"
    return 1.0 / beta, ""


def compare_netlists(a: dict, b: dict) -> dict:
    """Strict electrical identity: same net names, same (ref, pin) members."""
    ma, mb = _memberships(a), _memberships(b)
    only_a = sorted(set(ma) - set(mb))
    only_b = sorted(set(mb) - set(ma))
    diffs = []
    for net in sorted(set(ma) & set(mb)):
        if ma[net] != mb[net]:
            diffs.append({
                "net": net,
                "only_in_a": sorted(set(ma[net]) - set(mb[net])),
                "only_in_b": sorted(set(mb[net]) - set(ma[net])),
            })
    by_members = {mb[n]: n for n in only_b}
    renamed = [{"a": n, "b": by_members[ma[n]], "members": len(ma[n])}
               for n in only_a if ma[n] in by_members]
    return {"identical": not (only_a or only_b or diffs),
            "nets_only_in_a": only_a, "nets_only_in_b": only_b,
            "membership_diffs": diffs, "renamed": renamed,
            "net_counts": {"a": len(ma), "b": len(mb)}}


def run_compare(net_a: Path, net_b: Path, out: str | None):
    a = parse_netlist(net_a)
    b = parse_netlist(net_b)
    cmp_result = compare_netlists(a, b)
    violations = []

    def v(kind, net, msg, **extra):
        violations.append(checklib.violation(
            SCRIPT, "error", None, None, net, [], msg, SOURCE,
            kind="netlist_diff", diff=kind, **extra))

    for net in cmp_result["nets_only_in_a"]:
        if any(r["a"] == net for r in cmp_result["renamed"]):
            continue
        v("missing_in_b", net, f"net '{net}' only in {net_a.name}")
    for net in cmp_result["nets_only_in_b"]:
        if any(r["b"] == net for r in cmp_result["renamed"]):
            continue
        v("missing_in_a", net, f"net '{net}' only in {net_b.name}")
    for r in cmp_result["renamed"]:
        v("renamed", r["a"],
          f"net '{r['a']}' ({net_a.name}) matches membership of "
          f"'{r['b']}' ({net_b.name}) - renamed")
    for d in cmp_result["membership_diffs"]:
        v("membership", d["net"],
          f"net '{d['net']}' membership differs: only in {net_a.name}: "
          f"{d['only_in_a']}, only in {net_b.name}: {d['only_in_b']}",
          only_in_a=d["only_in_a"], only_in_b=d["only_in_b"])

    payload = checklib.report(SCRIPT, str(net_a), violations,
                              mode="compare", a=str(net_a), b=str(net_b),
                              **cmp_result)
    return payload, out


# ------------------------------------------------------------ audit mode

def _constraint_nets(constraints: dict) -> dict[str, list[str]]:
    """Every net name constraints.json references, keyed by where."""
    where: dict[str, list[str]] = {}

    def add(net, src):
        if isinstance(net, str) and net:
            where.setdefault(net, []).append(src)

    for i, ent in enumerate(constraints.get("high_speed", []) or []):
        add(ent.get("net"), f"high_speed[{i}].net")
        ref = ent.get("reference")
        if isinstance(ref, str):
            add(ref, f"high_speed[{i}].reference")
        elif isinstance(ref, dict):
            for lay, rnet in ref.items():
                add(rnet, f"high_speed[{i}].reference[{lay}]")
    for i, ent in enumerate(constraints.get("power", []) or []):
        add(ent.get("net"), f"power[{i}].net")
    for i, ent in enumerate(constraints.get("voltages", []) or []):
        add(ent.get("net"), f"voltages[{i}].net")
    for i, ent in enumerate(constraints.get("thermal", []) or []):
        add(ent.get("net"), f"thermal[{i}].net")
    for i, ent in enumerate(constraints.get("diff_pairs", []) or []):
        add(ent.get("p"), f"diff_pairs[{i}].p")
        add(ent.get("n"), f"diff_pairs[{i}].n")
    return where


def _suffix_partner(name: str) -> str | None:
    """Expected partner net name if `name` carries a strong diff suffix."""
    up = name.upper()
    for a, b in STRONG_SUFFIXES:
        for tok, other in ((a, b), (b, a)):
            if up.endswith(tok):
                return name[: len(name) - len(tok)] + other
    return None


def _pair_follows_convention(p: str, n: str) -> bool:
    up_p, up_n = p.upper(), n.upper()
    for a, b in STRONG_SUFFIXES:
        if (up_p.endswith(a) and up_n.endswith(b)
                and up_p[: -len(a)] == up_n[: -len(b)]):
            return True
    return False


def _constraint_refs(constraints: dict) -> dict[str, list[str]]:
    """Every refdes constraints.json references, keyed by where."""
    where: dict[str, list[str]] = {}

    def add(ref, src):
        if isinstance(ref, str) and ref:
            where.setdefault(ref, []).append(src)

    placement = constraints.get("placement") or {}
    for i, e in enumerate(placement.get("edges") or []):
        add(e.get("ref"), f"placement.edges[{i}].ref")
    for i, g in enumerate(placement.get("groups") or []):
        add(g.get("anchor"), f"placement.groups[{i}].anchor")
        for j, m in enumerate(g.get("members") or []):
            add(m, f"placement.groups[{i}].members[{j}]")
    for i, r in enumerate(placement.get("fixed") or []):
        add(r, f"placement.fixed[{i}]")
    for i, s in enumerate(placement.get("separation") or []):
        for side in ("a", "b"):
            for j, r in enumerate(s.get(side) or []):
                add(r, f"placement.separation[{i}].{side}[{j}]")
    for i, t in enumerate(constraints.get("thermal") or []):
        add(t.get("ref"), f"thermal[{i}].ref")
    return where


def _series_escapes(rail: str, members: list[dict],
                    comp_pins: dict[str, dict[str, str]]) -> list[str]:
    """2-pin members whose far pin leaves the rail through a non-ground,
    non-NC net: current has a path out through a series/load element
    (polyfuse, dropper, LED resistor), which never carries pintype
    power_in. A typo'd dead rail (source + caps to GND only) has none."""
    out = set()
    for m in members:
        pins = comp_pins.get(m["ref"], {})
        if len(pins) != 2:
            continue
        other = next((n for p, n in pins.items() if p != m["pin"]), None)
        if (other and other != rail and not _is_ground_name(other)
                and not other.startswith("unconnected-")):
            out.add(m["ref"])
    return sorted(out)


def audit(parsed: dict, constraints: dict,
          decoupling: dict | None,
          pin_requirements: list[dict] | None = None
          ) -> tuple[list[dict], dict]:
    nets = parsed["nets"]
    comps = parsed["components"]
    violations: list[dict] = []

    def v(sev, kind, net, refs, msg, **extra):
        violations.append(checklib.violation(
            SCRIPT, sev, None, None, net, refs, msg, SOURCE,
            kind=kind, **extra))

    # 1. every constraints-referenced net exists
    referenced = _constraint_nets(constraints)
    for net, sources in sorted(referenced.items()):
        if net not in nets:
            v("error", "missing_net", net, [],
              f"constraints net '{net}' ({', '.join(sources)}) not in "
              f"netlist", constraint_sources=sources)

    # 1b. every constraints-referenced refdes exists (ladder row 86: a
    # refdes rename/split must not silently mute a placement constraint)
    ref_where = _constraint_refs(constraints)
    for ref, sources in sorted(ref_where.items()):
        if ref not in comps:
            v("error", "missing_ref", None, [ref],
              f"constraints ref '{ref}' ({', '.join(sources)}) not in "
              f"netlist components", constraint_sources=sources)

    # 2. explicit diff pairs follow the _P/_N (or DP/DM, D+/D-) convention
    for i, ent in enumerate(constraints.get("diff_pairs", []) or []):
        p, n = ent.get("p"), ent.get("n")
        if p and n and not _pair_follows_convention(p, n):
            v("warning", "diffpair_naming", p, [],
              f"diff_pairs[{i}] ('{p}', '{n}') does not follow a "
              f"differential suffix convention (_P/_N, DP/DM, D+/D-)")

    # 3. unpaired differential-looking nets in the netlist
    for name in sorted(nets):
        if name.startswith("unconnected-"):
            continue
        partner = _suffix_partner(name)
        if partner and partner not in nets:
            v("warning", "diffpair_unpaired", name, [],
              f"net '{name}' looks differential but partner "
              f"'{partner}' does not exist")

    # per-component pin->net map over ALL nets (incl. unconnected-*):
    # serves the series-escape scan (4) and the pin inventory (4c)
    comp_pins: dict[str, dict[str, str]] = {}
    for name, members in nets.items():
        for m in members:
            if m["ref"]:
                comp_pins.setdefault(m["ref"], {})[m["pin"]] = name

    # 4. power tree: declared rails have consumers; feeders are declared
    declared_power = [ent.get("net")
                     for ent in constraints.get("power", []) or []]
    declared_all = set(declared_power) | {
        ent.get("net") for ent in constraints.get("voltages", []) or []}
    power_facts = []
    for net in declared_power:
        if net not in nets:
            continue  # already an error above
        members = nets[net]
        n_in = sum(1 for m in members if _base_type(m["pintype"]) == "power_in")
        n_out = sum(1 for m in members
                    if _base_type(m["pintype"]) == "power_out")
        fact = {"net": net, "nodes": len(members),
                "power_in": n_in, "power_out": n_out}
        if n_in == 0:
            # passive loads (LEDs, droppers, fuses) never carry power_in:
            # a series escape means the rail feeds SOMETHING - record the
            # fact instead of a false-positive warning (T6 calibration)
            escapes = _series_escapes(net, members, comp_pins)
            if escapes:
                fact["passive_fed_via"] = escapes
            else:
                v("warning", "power_no_consumers", net,
                  sorted({m["ref"] for m in members}),
                  f"declared power net '{net}' feeds no power_in pin")
        power_facts.append(fact)
    for name in sorted(nets):
        if name in declared_all or name.startswith("unconnected-"):
            continue
        if _is_ground_name(name):
            continue
        feeders = [m for m in nets[name]
                   if _base_type(m["pintype"]) == "power_in"]
        if feeders:
            v("warning", "power_undeclared", name,
              sorted({m["ref"] for m in feeders}),
              f"net '{name}' feeds power_in pins "
              f"({', '.join(f'{m['ref']}.{m['pin']}' for m in feeders)}) "
              f"but is not declared in constraints power[]/voltages[]")

    # 4c. pin inventory vs net membership (ladder row 68): hierarchical
    # exports DROP no-connect singleton nets; a flat export's NC pins live
    # in unconnected-* nets (counted as connected here) and must not fire.
    libparts = parsed.get("libparts") or {}
    pins_expected = pins_connected = 0
    for ref in sorted(comps):
        comp = comps[ref]
        exp = set(comp.get("unit_pins") or ())
        if not exp:
            exp = set(libparts.get(comp.get("libsource")) or ())
        if not exp:
            continue  # no inventory in this export (synthetic/old netlist)
        have = set(comp_pins.get(ref, ()))
        pins_expected += len(exp)
        pins_connected += len(exp & have)
        missing = sorted(exp - have,
                         key=lambda p: (0, int(p)) if p.isdigit() else (1, p))
        if missing:
            v("warning", "pin_no_net", None, [ref],
              f"pin(s) {', '.join(ref + '.' + p for p in missing)} exist "
              f"in the symbol but reach NO net (hierarchical exports drop "
              f"no-connect singletons; these pads arrive at board_init "
              f"with no net at all)", pins=missing)

    # 5. dangling single-pin nets (label-typo signature)
    for name in sorted(nets):
        if name.startswith("unconnected-"):
            continue
        if len(nets[name]) == 1:
            m = nets[name][0]
            v("warning", "dangling_net", name, [m["ref"]],
              f"net '{name}' has a single pin ({m['ref']}.{m['pin']}) - "
              f"label typo?")

    # 6. decoupling associations vs the netlist
    n_assoc = 0
    if decoupling is not None:
        assocs = decoupling.get("associations")
        if not isinstance(assocs, list):
            raise checklib.CheckError(
                "decoupling metadata has no associations[] list")
        n_assoc = len(assocs)
        member_index = {(m["ref"], m["pin"]): name
                        for name, members in nets.items() for m in members}
        for a in assocs:
            cap, ic = a.get("cap"), a.get("ic")
            pin, rail = str(a.get("pin")), a.get("rail")
            gnd = a.get("gnd", "GND")
            ctx = f"association {cap}->{ic}.{pin}"
            if cap not in comps:
                v("error", "metadata_mismatch", rail, [c for c in (ic,) if c],
                  f"{ctx}: cap '{cap}' not in netlist")
                continue
            if ic not in comps:
                v("error", "metadata_mismatch", rail, [cap],
                  f"{ctx}: ic '{ic}' not in netlist")
                continue
            if rail not in nets:
                v("error", "metadata_mismatch", rail, [cap, ic],
                  f"{ctx}: rail net '{rail}' not in netlist")
                continue
            if member_index.get((ic, pin)) != rail:
                v("error", "metadata_mismatch", rail, [cap, ic],
                  f"{ctx}: {ic}.{pin} is on net "
                  f"'{member_index.get((ic, pin), '<none>')}', not '{rail}'")
            cap_nets = {name for (r, _), name in member_index.items()
                        if r == cap}
            if rail not in cap_nets:
                v("error", "metadata_mismatch", rail, [cap],
                  f"{ctx}: no pin of '{cap}' is on rail '{rail}' "
                  f"(cap nets: {sorted(cap_nets)})")
            if gnd not in cap_nets:
                v("error", "metadata_mismatch", gnd, [cap],
                  f"{ctx}: no pin of '{cap}' is on return net '{gnd}' "
                  f"(cap nets: {sorted(cap_nets)})")
            want, have = a.get("value"), comps[cap]["value"]
            if want and have and want != have:
                v("warning", "metadata_mismatch", rail, [cap],
                  f"{ctx}: metadata value '{want}' != netlist value "
                  f"'{have}'")

    # 7. a ground-named net that reaches no ground: no power pin on it and
    # no resistor, inductor, bead, jumper or net tie across to another
    # ground net (errata corpus: digits v1 MIC_GND, a local label where the
    # GND symbol belonged, so the mic return floated)
    for name in sorted(nets):
        if not _is_ground_name(name) or _GROUND_EXEMPT_RE.search(name.upper()):
            continue
        members = nets[name]
        if len(members) < 2 or any(_base_type(m["pintype"]) in
                                   ("power_in", "power_out") for m in members):
            continue
        if _ground_bridges(name, members, comp_pins):
            continue
        others = [n for n in nets if n != name and _is_ground_name(n)]
        if not others:
            continue  # the board's only ground: nothing to be cut off from
        v("warning", "ground_island", name, sorted({m["ref"] for m in members}),
          f"ground net '{name}' carries no power pin and no part ties it to "
          f"another ground ({', '.join(sorted(others)[:3])}): it floats. A "
          f"local label where the ground symbol belonged?")

    # 8. a bus net on its partner's pin: SDA landing on a pin named SCL
    # (errata class: pandore rev A0 CS4272, SDA/SCL transposed at the codec)
    for name in sorted(nets):
        token = _bus_token(name)
        if not token:
            continue
        partner = _BUS_PARTNER[token]
        for m in nets[name]:
            if _bus_token(m.get("pinfunction") or "") == partner:
                v("error", "pin_net_swap", name, [m["ref"]],
                  f"net '{name}' lands on {m['ref']}.{m['pin']}, a pin "
                  f"named '{m['pinfunction']}': {token} and {partner} are "
                  f"crossed at {m['ref']}", pin=m["pin"])

    # 9. a stock SOT-23 transistor whose symbol pin order isn't the
    # package's: nearly every SOT-23 MOSFET is 1=G 2=S 3=D and every BJT
    # 1=B 2=E 3=C (errata corpus: oasis v0.2, Q_NMOS_GDS on SOT-23-3 put
    # the SI2328DS's source where the drain goes)
    for ref in sorted(comps):
        fp = comps[ref].get("footprint") or ""
        if not _STOCK_SOT23_RE.match(fp):
            continue
        funcs = {}
        for name, members in nets.items():
            for m in members:
                if m["ref"] == ref:
                    funcs[m["pin"]] = _transistor_role(m.get("pinfunction"))
        roles = (funcs.get("1"), funcs.get("2"), funcs.get("3"))
        for want in _SOT23_ORDERS:
            if set(roles) == set(want) and roles != want:
                v("warning", "sot23_pinout", None, [ref],
                  f"{ref} ({comps[ref].get('value')}) on {fp}: symbol pins "
                  f"1-2-3 are {'-'.join(roles)}, the SOT-23 standard is "
                  f"{'-'.join(want)}; check the part's datasheet pinout "
                  f"(or use the matching symbol variant)",
                  pins=dict(zip("123", roles)))

    # 10. an op-amp or comparator input whose DC level sits outside the
    # part's input common-mode range (errata corpus: minuet v3, an LMV331
    # whose inputs rest at 3.1-3.3 V on a 3.3 V supply, above its Vcc-0.7 V:
    # the rain sensor never worked)
    table = _cm_table()
    rails = _rail_volts(constraints, nets)
    cm_checked = 0
    for ref in sorted(comps):
        row = _cm_row(comps[ref], table)
        if not row:
            continue
        pins = comp_pins.get(ref, {})
        types = {m["pin"]: m for n in set(pins.values()) for m in nets[n]
                 if m["ref"] == ref}
        supply = [rails[pins[p]] for p, m in types.items()
                  if (_base_type(m["pintype"]) == "power_in"
                      or _AMP_SUPPLY_RE.match((m.get("pinfunction")
                                               or "").upper()))
                  and pins[p] in rails]
        if len(supply) < 2:
            continue
        vneg, vpos = min(supply), max(supply)
        lo, hi = vneg + row["vcm"][0], vpos + row["vcm"][1]
        for p, m in sorted(types.items()):
            if _base_type(m["pintype"]) not in ("input", "passive") or \
                    not _AMP_IN_RE.match((m.get("pinfunction") or "").upper()):
                continue
            net = pins[p]
            if net in rails:
                level, opened = rails[net], []
            else:
                level, opened, _why = _dc_level(net, nets, comps,
                                                comp_pins, rails)
                if level is None:
                    continue
            cm_checked += 1
            if lo - 0.005 <= level <= hi + 0.005:
                continue
            side = (f"above the top of its input common-mode range "
                    f"(V+ {hi - vpos:+.2f} V = {hi:.2f} V" if level > hi else
                    f"below the bottom of its input common-mode range "
                    f"(V- {lo - vneg:+.2f} V = {lo:.2f} V")
            v("warning", "input_common_mode", net, [ref],
              f"{ref} ({comps[ref].get('value')}, {row['kind']}) input "
              f"{m['pinfunction']} (pin {p}) rests at {level:.2f} V on "
              f"'{net}'" + (f" with {', '.join(opened)} open" if opened
                            else "") +
              f", {side} on a {vneg:g}..{vpos:g} V supply): the output "
              f"is undefined there. "
              f"Move the bias inside the range or pick a part whose range "
              f"covers it (range from {row['source'].split(';')[0]}"
              + ("" if row.get("verified") else ", not yet re-read") + ")",
              pin=p, level_v=round(level, 3), vcm_v=[round(lo, 3),
                                                     round(hi, 3)],
              opened=opened)

    # 11. a part the IC's datasheet requires between two of its pins
    # (reference/pin_requirements.yaml; errata corpus: minuet v3 shipped the
    # MCF8316D with no CBK drawn from FB_BK to GND_BK - the +3.3V rail's
    # caps were all drawn on other sheets for other parts)
    specs = (load_pin_requirements() if pin_requirements is None
             else pin_requirements)
    for sev, net, ic, req, found, elsewhere, off in _pin_requirements(
            nets, comps, comp_pins, specs):
        want = req.get("value") or "/".join(req["kinds"])
        what = (f"{ic} ({comps[ic].get('value')}) needs {req['id']} "
                f"({want}) from {req['pin']} to {req['to']} "
                f"[{req.get('source', 'datasheet')}]")
        if sev == "error":
            extra = (f"; off-value parts across it: {', '.join(off)}"
                     if off else "")
            v("error", "pin_part_missing", net, [ic],
              f"{what}: no part spans those pins{extra}",
              part=req["id"], pin=req["pin"], to=req["to"],
              off_value=off)
        else:
            v("warning", "pin_part_missing", net, [ic],
              f"{what}: only {', '.join(elsewhere)} span them, drawn on "
              f"other sheets than {ic}'s {comps[ic].get('sheet')} - the "
              f"rail's parts serve other ICs and none was drawn for this "
              f"pin; draw it with {ic} or confirm one sits at the pin on "
              f"the board", part=req["id"], pin=req["pin"], to=req["to"],
              drawn_elsewhere=elsewhere)

    # 12. a decompensated op-amp run below its minimum stable noise gain
    # (reference/opamp_min_gain.yaml): a follower or a G = -1 stage around
    # an OPA637 or an uncompensated NE5534 oscillates. Noise gain, 1/beta,
    # read where the loop crosses over at the minimum gain, so a feedback
    # cap that drops it there counts (ADI MT-033, AN148)
    gain_table = _min_gain_table()
    loops_checked = 0
    for ref in sorted(comps):
        row = _cm_row(comps[ref], gain_table)
        if not row:
            continue
        pins = comp_pins.get(ref, {})
        types = {m["pin"]: m for n in set(pins.values()) for m in nets[n]
                 if m["ref"] == ref}
        if row.get("comp_pins"):
            comp_re = re.compile(row["comp_pins"], re.I)
            comp_nets = {pins[p] for p, m in types.items()
                         if comp_re.search(m.get("pinfunction") or "")}
            if any((m["ref"] or "").startswith("C") for n in comp_nets
                   for m in nets[n]):
                continue  # compensated by hand
        f_hz = row["gbw_hz"] / row["min_gain"]
        for inv_p, out_p in _amp_loops(types):
            ng, _why = _noise_gain(pins[inv_p], pins[out_p], ref, f_hz,
                                   nets, comps, comp_pins, rails)
            if ng is None:
                continue
            loops_checked += 1
            if ng >= row["min_gain"] * 0.98:
                continue
            sib = (f", or the unity-stable {row['unity_stable']}"
                   if row.get("unity_stable") else "")
            v("warning", "below_min_stable_gain", pins[inv_p], [ref],
              f"{ref} ({comps[ref].get('value')}) is decompensated, stable "
              f"only at a noise gain of {row['min_gain']:g} or more, but "
              f"its loop from {pins[out_p]} (pin {out_p}) back to "
              f"{pins[inv_p]} (pin {inv_p}) gives a noise gain of {ng:.2f} "
              f"at {f_hz / 1e6:.3g} MHz (a follower is 1, an inverting -1 "
              f"stage 2): it will ring or oscillate. Raise the noise gain "
              f"(a smaller gain resistor, or an R-C from the inverting "
              f"input to ground){sib} (from {row['source'].split(';')[0]}"
              + ("" if row.get("verified") else ", not yet re-read") + ")",
              pin=inv_p, out_pin=out_p, noise_gain=round(ng, 3),
              min_gain=row["min_gain"], at_hz=f_hz)

    facts = {
        "nets": len(nets),
        "components": len(comps),
        "unconnected_pins": sum(1 for n in nets
                                if n.startswith("unconnected-")),
        "constraint_nets_checked": len(referenced),
        "constraint_refs_checked": len(ref_where),
        "pins_expected": pins_expected,
        "pins_connected": pins_connected,
        "decoupling_associations": n_assoc,
        "power": power_facts,
        "common_mode_inputs_checked": cm_checked,
        "min_gain_loops_checked": loops_checked,
    }
    return violations, facts


def run_audit(netlist: Path, constraints_path: Path,
              decoupling_path: Path | None, out: str | None):
    parsed = parse_netlist(netlist)
    constraints = checklib.load_json(constraints_path, "constraints")
    decoupling = (checklib.load_json(decoupling_path, "decoupling metadata")
                  if decoupling_path else None)
    violations, facts = audit(parsed, constraints, decoupling)
    payload = checklib.report(SCRIPT, str(netlist), violations,
                              mode="audit", **facts)
    # warnings alone do not fail the audit (fp_verify precedent)
    has_error = any(x["severity"] == "error" for x in violations)
    payload["status"] = "violations" if has_error else "pass"
    return payload, out


# ------------------------------------------------------------ CLI

def _export_netlist(sch: Path) -> Path:
    """Export the schematic's netlist via kc.py into a temp file."""
    import kc  # noqa: PLC0415  (deferred: only the --sch path needs kicad-cli)
    from lib import env  # noqa: PLC0415
    cli = env.find_kicad_cli()
    out = Path(tempfile.mkdtemp(prefix="aiee_audit_")) / (sch.stem + ".net")
    r = kc.export_netlist(cli, sch, out)
    if r.get("status") != "pass":
        raise checklib.CheckError(
            f"netlist export failed for {sch}: {r.get('error') or r}")
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--netlist", help="kicadsexpr netlist to audit")
    ap.add_argument("--sch", help="schematic; netlist is exported first "
                                  "(needs kicad-cli)")
    ap.add_argument("--constraints", help="constraints.json (audit mode)")
    ap.add_argument("--decoupling", help="decoupling.json metadata to "
                                         "cross-check (audit mode)")
    ap.add_argument("--compare", help="second netlist: strict electrical "
                                      "identity diff instead of the audit")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)

    if not args.netlist and not args.sch:
        raise checklib.CheckError("give --netlist or --sch")
    netlist = Path(args.netlist) if args.netlist else None
    if args.sch and not netlist:
        netlist = _export_netlist(Path(args.sch))

    if args.compare:
        return run_compare(netlist, Path(args.compare), args.out)
    if not args.constraints:
        raise checklib.CheckError("audit mode needs --constraints "
                                  "(or use --compare)")
    return run_audit(netlist, Path(args.constraints),
                     Path(args.decoupling) if args.decoupling else None,
                     args.out)


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
