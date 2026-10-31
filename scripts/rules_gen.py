#!/usr/bin/env python
"""rules_gen.py - constraints.json -> KiCad custom design rules + net classes (SPEC P5).

KiCad custom DRC rules (`.kicad_dru`) are the enforcement backbone: anything
expressible as a rule becomes a named DRC violation, so the standard DRC gate
(gate.py drc) carries it for free. This script emits:

  1. A `<board>.kicad_dru` = fab-floor BASELINE (from reference/jlc_capabilities.yaml
     for the board's layer count / copper weight - the weight is DERIVED from the
     stackup's own outer copper unless --copper-oz overrides, and an explicit
     mismatch against a named stackup is refused, T6 P5-1) + DESIGN rules from
     constraints:
       - power nets  -> minimum track width (IPC-2152 via check_current) per net
       - diff pairs  -> diff_pair_gap rule + differential width (impedance.py +
         stackup); on an inner STRIPLINE layer (a pour on the layers above and
         below, from constraints planes) a layer-scoped width/gap solved with
         impedance.field_stripline (#323); any other inner layer keeps the
         `disallow track` guard (T6 P5-4)
       - high_speed impedance_ohm (single-ended) -> outer microstrip width as
         a fact, and a layer-scoped track_width rule per stripline layer
       - voltages / voltage_pairs -> named HV clearance rules from the IPC-2221
         table in check_creepage (T6 P5-3; carrier hand-authored these before)
     Baseline rules are emitted FIRST and per-net rules LAST: when two rules of the
     same constraint type match one item, the LATER rule wins (LEARNINGS [drc]), so
     the specific per-net rule overrides the generic floor.
     `--check-dru PATH` instead ASSERTS an existing DRU still carries the aiee_*
     fab-floor baseline (T6 P5-5, ladder row 160 - DRUs do get hand-edited).
  2. (optional, --pro) net classes written into `<board>.kicad_pro` net_settings:
     ONE POWER CLASS PER REQUIRED WIDTH (never one class at the widest - that put
     pd-trigger's 20 mA /VDD in the same 1.75 mm class as its 5 A VBUS and would
     have had Freerouting drive 1.75 mm traces into 0.6 mm pads; LEARNINGS
     2026-07-28 [routing][rules_gen][freerouting]; a plane_fed rail's class is
     sized for its taps, power_rules) and one class per differential
     impedance, assigned by netclass_patterns; plus board.design_settings.rules
     minimums and the fab-floor severities from lib/fabfloors.py - the same single
     source board_init writes, so the two files cannot disagree. These drive the
     router (S11) and placement; DRU is the DRC enforcer.
     Every net with an aiee_hv_* clearance rule is ALSO moved into a class carrying
     that clearance (a `_HV_<c>mm` variant of its power/diff class, or `HV_<c>mm`),
     and every class clearance is floored at the board's min_hole_clearance minus
     the class's via ring: the DSN export hands Freerouting netclasses only, so a
     DRU-only clearance cost PCB-0018 a 1 h route with 406 clearance errors.

With the board (--pcb, default the .kicad_pcb beside --pro), its pads add
neck-down rules where a power net meets pads narrower than its width
(neck_rules).

Conditions use `A.NetName == 'NET'` (NOT `A.Net`, which silently matches nothing -
LEARNINGS [drc]). kicad-cli auto-loads the .kicad_dru sitting next to the board.

I/O: SPEC section 6 - argparse, JSON to stdout or --out, exit 0/1/2, no interactivity.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import traceback
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))
import check_current  # noqa: E402  (required_width_mm)
import fabfloors  # noqa: E402  (the single source of fab minimums)
import fabmaterial  # noqa: E402  (which laminate: floors + order)
import impedance as imp  # noqa: E402

REFERENCE = SCRIPTS.parent / "reference"
CAP_FILE = REFERENCE / "jlc_capabilities.yaml"
STACKUP_FILE = REFERENCE / "stackups.yaml"

# Copper finished thickness (mm) by weight, for IPC-2152 width scaling.
CU_OZ_MM = {1.0: 0.035, 0.5: 0.0175, 2.0: 0.070}


# --------------------------------------------------------------- reference data

def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# capability lookup lives in lib/fabfloors.py (board_init uses the same one)
capability_class = fabfloors.capability_class


# --------------------------------------------------------------- DRU rules

class Rule:
    """One .kicad_dru rule."""
    def __init__(self, name: str, constraint: str, minv: float | None = None,
                 condition: str | None = None, layer: str | None = None,
                 severity: str | None = None, extra: str | None = None):
        self.name = name
        self.constraint = constraint
        self.minv = minv
        self.condition = condition
        self.layer = layer
        self.severity = severity
        self.extra = extra

    def render(self) -> str:
        lines = [f'(rule "{self.name}"']
        if self.severity:
            lines.append(f'\t(severity {self.severity})')
        if self.layer:
            lines.append(f'\t(layer {self.layer})')
        if self.minv is not None:
            lines.append(f'\t(constraint {self.constraint} (min {self.minv:.4f}mm))')
        elif self.extra is not None:
            lines.append(f'\t(constraint {self.constraint} {self.extra})')
        else:
            lines.append(f'\t(constraint {self.constraint})')
        if self.condition:
            lines.append(f'\t(condition "{self.condition}")')
        lines.append(')')
        return "\n".join(lines)


def baseline_rules(cap: dict) -> list[Rule]:
    """Fab-floor rules from a jlc_capabilities.yaml design-rule row.

    Minimums come from fabfloors.DRU_FLOOR_KEYS - the same map
    fabfloors.check_dru asserts against - so the writer and the checker
    cannot drift (T6 P5-5)."""
    def floor(name: str) -> float:
        return cap[fabfloors.DRU_FLOOR_KEYS[name]]

    r: list[Rule] = []
    r.append(Rule("aiee_track_width_floor", "track_width", floor("aiee_track_width_floor"),
                  condition="A.Type == 'track'"))
    r.append(Rule("aiee_clearance_floor", "clearance", floor("aiee_clearance_floor")))
    r.append(Rule("aiee_via_drill_floor", "hole_size", floor("aiee_via_drill_floor"),
                  condition="A.Type == 'via'"))
    r.append(Rule("aiee_via_diameter_floor", "via_diameter", floor("aiee_via_diameter_floor"),
                  condition="A.Type == 'via'"))
    r.append(Rule("aiee_annular_floor", "annular_width", floor("aiee_annular_floor"),
                  condition="A.Type == 'via'"))
    r.append(Rule("aiee_edge_clearance_floor", "edge_clearance", floor("aiee_edge_clearance_floor")))
    r.append(Rule("aiee_hole_to_hole_floor", "hole_to_hole", floor("aiee_hole_to_hole_floor")))
    r.append(Rule("aiee_silk_width_floor", "text_thickness", floor("aiee_silk_width_floor"),
                  condition="A.Type == 'text' && (A.Layer == 'F.Silkscreen' || A.Layer == 'B.Silkscreen')"))
    assert {x.name for x in r} == set(fabfloors.DRU_FLOOR_KEYS)
    return r


def tap_current_a(entry: dict) -> float:
    """What one track of a plane-fed rail carries: its declared
    tap_current_a, else the entry's per-via ampacity (check_current's
    default 0.5 A) - a tap feeds its pad through one via or the pour edge -
    and never more than the rail."""
    tap = entry.get("tap_current_a", entry.get("via_amps",
                                               check_current.VIA_AMPS_DEFAULT))
    return min(float(tap), float(entry["current_a"]))


def power_rules(constraints: dict, cu_mm: float) -> tuple[list[Rule], list[dict]]:
    """Per-power-net minimum track width from IPC-2152 (check_current).

    A plane_fed rail's trunk is its pour (constraints "planes"), so its
    tracks are taps: they are sized for tap_current_a, not the rail's
    current. One width per net at the full 5 A (2.38 mm on 1 oz) would sit
    on every stub into a 0.34 mm HTSSOP pin and is unmeetable; lite-main
    (hifi-receiver-2) declared its 5 A rails at 0.4-0.8 A to get the taps
    through, which hid the real current from every other check."""
    rules: list[Rule] = []
    facts: list[dict] = []
    plane_nets = {pl.get("net") for pl in constraints.get("planes", []) or []}
    for entry in constraints.get("power", []):
        net = entry["net"]
        cur = float(entry["current_a"])
        dt = float(entry.get("dt_c", 10.0))
        fact = {"net": net, "current_a": cur, "dt_c": dt}
        sized = cur
        if entry.get("plane_fed"):
            sized = tap_current_a(entry)
            fact.update({"plane_fed": True, "tap_current_a": sized,
                         "trunk_width_mm": round(check_current.required_width_mm(
                             cur, dt, cu_mm), 4),
                         "plane_declared": net in plane_nets})
        w = check_current.required_width_mm(sized, dt, cu_mm)
        safe = re.sub(r"[^A-Za-z0-9]", "_", net).strip("_")
        rules.append(Rule(f"aiee_pwr_width_{safe}", "track_width", round(w, 4),
                          condition=f"A.NetName == '{net}' && A.Type == 'track'"))
        fact["min_width_mm"] = round(w, 4)
        facts.append(fact)
    return rules, facts


# --------------------------------------------------------------- board pads

def board_pads(pcb: Path | None):
    """geom.BoardGeom of the board rules_gen sizes for, or None."""
    if pcb is None or not Path(pcb).is_file():
        return None
    sys.path.insert(0, str(SCRIPTS / "lib"))
    import geom
    return geom.BoardGeom.from_file(pcb)


def neck_rules(bg, power_facts: list[dict], cap: dict
               ) -> tuple[list[Rule], list[dict]]:
    """A neck-down allowance where a power net meets pads narrower than its
    width rule: inside the courtyard of each footprint with such a pad, the
    net's tracks may narrow to the narrowest such pad's short side (floored
    at the fab's track width) - check_current's own pad-exit bound, a track
    no narrower than the copper it leaves. Emitted after the per-net width
    rules (the later rule wins). check_current still judges every neck: one
    longer than its 1 mm pad exit must carry the full width. Returns
    (rules, facts)."""
    rules: list[Rule] = []
    facts: list[dict] = []
    floor = float(cap["min_trace_width_mm"])
    for f in power_facts:
        net, w = f["net"], float(f["min_width_mm"])
        narrow: dict[str, float] = {}
        for p in bg.pads_of(net=net):
            short = min(p.size)
            if short < w - 1e-6:
                narrow[p.ref] = min(narrow.get(p.ref, short), short)
        for ref in sorted(narrow):
            neck = round(max(floor, min(narrow[ref], w)), 4)
            name = f"aiee_pwr_neck_{_safe(net)}_{_safe(ref)}"
            rules.append(Rule(name, "track_width", neck, condition=(
                f"A.NetName == '{net}' && A.Type == 'track' "
                f"&& A.intersectsCourtyard('{ref}')")))
            facts.append({"net": net, "ref": ref, "pad_short_mm":
                          round(narrow[ref], 4), "neck_mm": neck,
                          "width_mm": w, "rule": name})
    return rules, facts


def _bga_refs(bg) -> list[str]:
    """Refs of the board's ball grids (none without a board)."""
    if bg is None:
        return []
    sys.path.insert(0, str(SCRIPTS / "lib"))
    import bgalib
    return [b.ref.replace("'", "") for b in bgalib.find_bgas(bg)]


def bga_rules(bg, cap: dict) -> tuple[list[Rule], list[dict]]:
    """The escape clearance inside each ball grid's courtyard (#325): the
    netclass clearance (hole clearance less the class via's ring) is wider
    than a 0.8 mm field's streets allow. Same geometry and rule text as
    route_escape (lib/bgalib) - one writer's rule is the other's."""
    sys.path.insert(0, str(SCRIPTS / "lib"))
    import bgalib
    floors = {"track_width": cap["min_trace_width_mm"],
              "clearance": cap["min_clearance_mm"],
              "via_diameter": cap["min_via_diameter_mm"],
              "via_drill": cap["min_via_drill_mm"],
              "annular": cap.get("min_annular_ring_mm", 0.0),
              "hole_clearance": KICAD_HOLE_CLEARANCE_MM}
    rules: list[Rule] = []
    facts: list[dict] = []
    for b in bgalib.find_bgas(bg):
        ep = bgalib.escape_params(b, floors)
        name = bgalib.rule_name(b.ref)
        rules.append(Rule(name, "clearance", ep["clearance"],
                          condition=bgalib.rule_condition(b.ref)))
        facts.append({"ref": b.ref, "pitch_mm": b.pitch,
                      "clearance_mm": ep["clearance"],
                      "via_mm": [ep["via_size"], ep["via_drill"]],
                      "method": ep["method"], "rule": name})
    return rules, facts


DIFF_SUFFIXES = [("_P", "_N"), ("_DP", "_DM"), ("+", "-"), ("_H", "_L")]


def detect_diff_pairs(constraints: dict) -> list[dict]:
    """Pair high_speed nets by name suffix. Returns [{p, n, base, impedance_ohm}]."""
    nets = [e["net"] for e in constraints.get("high_speed", [])]
    imp_of = {e["net"]: e.get("impedance_ohm") for e in constraints.get("high_speed", [])}
    pairs: list[dict] = []
    used: set[str] = set()
    # explicit diff_pairs section wins
    for dp in constraints.get("diff_pairs", []):
        pairs.append({"p": dp["p"], "n": dp["n"], "base": dp.get("base", dp["p"]),
                      "impedance_ohm": dp.get("impedance_ohm", 90)})
        used.update([dp["p"], dp["n"]])
    for net in nets:
        if net in used:
            continue
        for pa, nb in DIFF_SUFFIXES:
            if net.endswith(pa):
                base = net[:-len(pa)]
                mate = base + nb
                if mate in nets and mate not in used:
                    z = imp_of.get(net) or imp_of.get(mate)
                    if z is None:
                        z = 90 if "USB" in base.upper() else 100
                    pairs.append({"p": net, "n": mate, "base": base,
                                  "impedance_ohm": int(z)})
                    used.update([net, mate])
                break
    return pairs


def _series_dielectric(stack: list[dict], a: int, b: int) -> tuple[float, float]:
    """(height, epsilon_r) of the dielectrics between stack rows a and b: the
    heights summed, epsilon_r as plies in series (h / sum(h_i / er_i))."""
    plies = [ly for ly in stack[a + 1:b] if ly["type"] != "copper"]
    h = sum(float(ly["thickness_mm"]) for ly in plies)
    if h <= 0:
        return 0.0, 4.5
    inv = sum(float(ly["thickness_mm"]) / float(ly.get("epsilon_r", 4.5))
              for ly in plies)
    return h, h / inv


def plane_layers(stackup: dict, constraints: dict) -> set[str]:
    """Copper layers that carry a pour: constraints planes' layers, else
    planes_gen's defaults (the 2nd and 3rd copper on 4+ layers)."""
    names = [ly["name"] for ly in stackup.get("stack", []) if ly["type"] == "copper"]
    planes = constraints.get("planes")
    if planes is None:
        return set(names[1:3]) if len(names) >= 4 else set()
    return {e.get("layer") for e in planes if e.get("layer")}


def stripline_layers(stackup: dict, constraints: dict) -> dict[str, dict]:
    """Inner signal layers that are stripline: a pour on the copper layer
    above AND below, none on the layer itself. Each maps to the geometry
    impedance.field_stripline takes: the trace sits on the THICKER dielectric
    (h_base, the core on JLC builds) and is embedded in the thinner (h_fill,
    prepreg) - the same convention check_diffpair solves with."""
    stack = stackup.get("stack") or []
    rows = [i for i, ly in enumerate(stack) if ly["type"] == "copper"]
    names = [stack[i]["name"] for i in rows]
    pours = plane_layers(stackup, constraints)
    out: dict[str, dict] = {}
    for k in range(1, len(rows) - 1):
        name = names[k]
        if name in pours or names[k - 1] not in pours or names[k + 1] not in pours:
            continue
        up = _series_dielectric(stack, rows[k - 1], rows[k])
        dn = _series_dielectric(stack, rows[k], rows[k + 1])
        (hb, eb), (hf, ef) = sorted([up, dn], reverse=True)
        t = float(stack[rows[k]].get("thickness_mm")
                  or CU_OZ_MM.get(stack[rows[k]].get("copper_oz", 0.5), 0.0175))
        out[name] = {"h_base": round(hb, 4), "er_base": round(eb, 3),
                     "h_fill": round(hf, 4), "er_fill": round(ef, 3),
                     "t": t, "planes": [names[k - 1], names[k + 1]]}
    return out


def stripline_geometry(z_target: float, geo: dict, diff: bool,
                       floor: float = 0.0) -> dict:
    """Width (and, for a pair, the gap) whose stripline impedance on `geo` is
    z_target. A pair's gap is pinned at clamp(h_fill, 0.13, 0.30), the outer
    pairs' convention (tight coupling, a gap the fab draws), and the width
    solved; when that width is under the fab floor the width is pinned at
    the floor and the gap solved instead (a thin prepreg makes a 100 ohm
    pair narrow: JLC's 6-layer L3 wants 0.078 mm at 0.13 mm)."""
    hb, hf, t = geo["h_base"], geo["h_fill"], geo["t"]
    eb, ef = geo["er_base"], geo["er_fill"]
    s = min(max(hf, 0.13), 0.30) if diff else None
    key = "zdiff" if diff else "z0"

    def z_of(w: float, gap: float | None = s) -> float:
        return imp.field_stripline(w, hb, hf, t, eb, ef, gap)[key]

    # Z falls with width; a guess from the thinner side's height
    w = imp._secant_log(z_of, float(z_target), max(0.05, 0.8 * hf), 0.02, 2.0)
    out = {"model": "stripline"}
    if w < floor:
        w = floor
        if diff:   # Zdiff grows with the gap: solve 1/Z, which falls
            s = imp._secant_log(lambda g: 1.0 / z_of(w, g), 1.0 / float(z_target),
                                s, 0.05, 1.0)
            out["gap_solved"] = True
        else:
            out["below_fab_floor"] = True
    out.update({"width_mm": round(w, 4),
                "zdiff_ohm" if diff else "z0_ohm": round(z_of(w, s), 2)})
    if diff:
        out["gap_mm"] = round(s, 4)
    return out


def _layer_tag(layer: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", layer.replace(".Cu", ""))


def _neckdown(cond: str, bga_refs: list[str] | None) -> str:
    """An impedance width rule's condition, minus the inside of every ball
    grid's courtyard: escape stubs neck down to the fab floor there (#325's
    dog-bones), as every BGA fan-out does, and the stripline width applies
    from the field's edge on."""
    if not bga_refs:
        return cond
    inside = " || ".join(f"A.intersectsCourtyard('{r}')" for r in bga_refs)
    return f"({cond}) && !({inside})"


def _width_window(w: float, floor: float) -> str:
    """track_width (min/opt/max) for an impedance-sized width: +-10 % (about
    +-6 % in Z), the minimum never under the fab floor."""
    lo = max(floor, w * 0.9)
    return f"(min {lo:.4f}mm) (opt {w:.4f}mm) (max {max(lo, w * 1.1):.4f}mm)"


def diff_pair_rules(pairs: list[dict], stackup: dict, constraints: dict | None = None,
                    floor: float = 0.0, bga_refs: list[str] | None = None
                    ) -> tuple[list[Rule], list[dict]]:
    """diff_pair_gap rule + computed width/gap per pair (impedance.py).

    Inner layers: a stripline layer (stripline_layers: a pour above and
    below) gets its own solved width/gap as layer-scoped track_width and
    diff_pair_gap rules; any other inner layer keeps the guard (a named
    disallow-track), since nothing there is a reference to size against."""
    rules: list[Rule] = []
    facts: list[dict] = []
    # outer microstrip: dielectric gap to the nearest inner plane + its er
    h, er, cu_oz = outer_microstrip_params(stackup)
    strip = stripline_layers(stackup, constraints or {})
    inner = [ly["name"] for ly in stackup.get("stack", [])
             if ly["type"] == "copper"][1:-1]
    for dp in pairs:
        w, gap = imp.diff_pair(float(dp["impedance_ohm"]), h,
                               imp.CU_OZ_MM.get(cu_oz, 0.035), er)
        safe = re.sub(r"[^A-Za-z0-9]", "_", dp["base"]).strip("_")
        cond = f"A.NetName == '{dp['p']}' || A.NetName == '{dp['n']}'"
        # gap floor: a differential gap smaller than this is a skew/coupling defect
        rules.append(Rule(f"aiee_diff_gap_{safe}", "diff_pair_gap", round(gap, 4),
                          condition=cond))
        layers: dict[str, dict] = {}
        for lname, geo in strip.items():
            try:
                g = stripline_geometry(float(dp["impedance_ohm"]), geo, diff=True,
                                       floor=floor)
            except (ValueError, RuntimeError, ZeroDivisionError) as e:
                layers[lname] = {"note": f"stripline not solved: {e}"}
                continue
            layers[lname] = g
            tag = _layer_tag(lname)
            rules.append(Rule(f"aiee_diff_w_{safe}_{tag}", "track_width",
                              layer=f'"{lname}"', condition=_neckdown(cond, bga_refs),
                              extra=_width_window(g["width_mm"], floor)))
            rules.append(Rule(f"aiee_diff_gap_{safe}_{tag}", "diff_pair_gap",
                              layer=f'"{lname}"', condition=_neckdown(cond, bga_refs),
                              extra=f"(min {g['gap_mm'] * 0.9:.4f}mm) "
                                    f"(opt {g['gap_mm']:.4f}mm)"))
        # Inner-layer guard (T6 P5-4): an inner layer with no pour on both
        # sides has no geometry to solve, so a pair segment there is a NAMED
        # DRC violation instead of a silent impedance error. Layer
        # transitions via vias remain legal (vias are not tracks).
        unsized = [n for n in inner if n not in layers or "width_mm" not in layers[n]]
        if unsized and len(unsized) == len(inner):
            rules.append(Rule(f"aiee_diff_outer_only_{safe}", "disallow track",
                              layer="inner", condition=cond))
        else:
            for n in unsized:
                rules.append(Rule(f"aiee_diff_outer_only_{safe}_{_layer_tag(n)}",
                                  "disallow track", layer=f'"{n}"', condition=cond))
        f = {"pair": [dp["p"], dp["n"]], "impedance_ohm": dp["impedance_ohm"],
             "width_mm": w, "gap_mm": gap}
        if layers:
            f["layers"] = layers
        facts.append(f)
    return rules, facts


def impedance_rules(constraints: dict, stackup: dict, pairs: list[dict],
                    floor: float = 0.0, bga_refs: list[str] | None = None
                    ) -> tuple[list[Rule], list[dict]]:
    """Single-ended high_speed nets with impedance_ohm: the outer microstrip
    width (a fact: the router's class is left to the board, RF boards draw
    their own) and, per stripline layer, a layer-scoped track_width rule."""
    in_pair = {n for dp in pairs for n in (dp["p"], dp["n"])}
    nets = [e for e in constraints.get("high_speed", [])
            if e.get("impedance_ohm") and e.get("net") not in in_pair]
    if not nets:
        return [], []
    h, er, cu_oz = outer_microstrip_params(stackup)
    t = imp.CU_OZ_MM.get(cu_oz, 0.035)
    strip = stripline_layers(stackup, constraints)
    rules: list[Rule] = []
    facts: list[dict] = []
    by_z: dict[float, dict] = {}
    for e in nets:
        z = float(e["impedance_ohm"])
        if z not in by_z:
            sol = {"outer_width_mm": round(imp.solve_width(z, h, t, er), 4), "layers": {}}
            for lname, geo in strip.items():
                try:
                    g = stripline_geometry(z, geo, diff=False, floor=floor)
                except (ValueError, RuntimeError, ZeroDivisionError) as ex:
                    sol["layers"][lname] = {"note": f"stripline not solved: {ex}"}
                    continue
                sol["layers"][lname] = g
            by_z[z] = sol
        facts.append({"net": e["net"], "impedance_ohm": z, **by_z[z]})
    # one rule per (impedance, layer): its nets ORed in the condition
    for z, sol in by_z.items():
        members = [f["net"] for f in facts if f["impedance_ohm"] == z]
        cond = " || ".join(f"A.NetName == '{n}'" for n in members)
        for lname, g in sol["layers"].items():
            if "width_mm" not in g:
                continue
            rules.append(Rule(f"aiee_z{_mm_tag(z)}_w_{_layer_tag(lname)}", "track_width",
                              layer=f'"{lname}"', condition=_neckdown(cond, bga_refs),
                              extra=_width_window(g["width_mm"], floor)))
    return rules, facts


# --------------------------------------------------------------- HV clearance

def _vtag(volts: float) -> str:
    """48.0 -> '48', 12.5 -> '12p5' (for rule names)."""
    return ("%g" % abs(volts)).replace(".", "p")


def _safe(net: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", net).strip("_")


def hv_clearance_mm(dv: float, coating: str) -> float:
    """Worst-case OUTER-layer IPC-2221 spacing for a dv-volt pair under
    `coating`: max over the track/via/zone row and the exposed-land row -
    the largest requirement check_creepage can adjudicate for any item
    pair, so one net-wide DRU value covers every combination. Table lives
    in check_creepage (single source - no numbers duplicated here)."""
    import check_creepage as cc
    rows = {cc.item_row("track", True, coating), cc.item_row("pad", True, coating)}
    return max(cc.row_clearance_mm(dv, r) for r in rows)


def voltage_rules(constraints: dict, cap: dict) -> tuple[list[Rule], list[dict]]:
    """HV clearance DRU rules from constraints voltages/voltage_pairs (T6
    P5-3 == P2-4, ladder rows 117/118 context).

    rules_gen previously never read `voltages`, so 48 V spacing on
    lumina-carrier was hand-authored into the DRU at P5 (L0 prose in P2
    recipes). Emits, AFTER the baseline (later-wins ordering):
      - one `aiee_hv_<V>v_<net>` clearance rule per net with |V| above the
        IPC-2221 threshold, conditioned on A.NetName only (clearance is
        pairwise, so a single-net condition enforces the net against
        everything - the shape carrier hand-wrote as HV_48V_clearance);
        sorted ascending by clearance so a pair matching two HV rules
        resolves to the LARGER value (later rule wins).
      - one pair-scoped rule per explicit voltage_pairs entry (emitted
        last: an explicit pair OVERRIDES the derived per-net value, same
        semantics as check_creepage). A pair declared at or under the
        threshold between nets that carry a derived HV rule restores the
        baseline clearance for that pair - the check_creepage waiver,
        mirrored in the DRU (`aiee_hv_waived_*`).
    Severity stays error; the voltage is encoded in the rule name so P8
    triage can waive consciously. The pad-escape pre-check remains ladder
    row 117's separate item - not folded in here."""
    import check_creepage as cc
    coating = constraints.get("coating", "none")
    if coating not in cc.COATINGS:
        raise ValueError(f"unknown coating {coating!r} (use one of {cc.COATINGS})")
    vmap = cc.voltage_map(constraints)
    threshold = cc.HV_THRESHOLD_V
    rules: list[Rule] = []
    facts: list[dict] = []

    net_rules: list[tuple[float, Rule, dict]] = []
    hv_nets: set[str] = set()
    for net in sorted(n for n, v in vmap.items() if abs(v) > threshold):
        dv = abs(vmap[net])
        clr = hv_clearance_mm(dv, coating)
        name = f"aiee_hv_{_vtag(dv)}v_{_safe(net)}"
        rule = Rule(name, "clearance", round(clr, 4),
                    condition=f"A.NetName == '{net}'")
        net_rules.append((clr, rule, {"net": net, "voltage": vmap[net],
                                      "clearance_mm": round(clr, 4),
                                      "coating": coating, "rule": name}))
        hv_nets.add(net)
    for clr, rule, fact in sorted(net_rules, key=lambda t: (t[0], t[1].name)):
        rules.append(rule)
        facts.append(fact)

    for a, b, pv in cc.voltage_pair_list(constraints):
        if pv > threshold:
            clr = hv_clearance_mm(pv, coating)
            name = f"aiee_hv_{_vtag(pv)}v_{_safe(a)}__{_safe(b)}"
            waiver = False
        elif a in hv_nets or b in hv_nets:
            # explicit sub-threshold pair between derived-HV nets = waiver:
            # restore the fab-floor clearance for exactly this pair
            clr = float(cap["min_clearance_mm"])
            name = f"aiee_hv_waived_{_safe(a)}__{_safe(b)}"
            waiver = True
        else:
            continue  # sub-threshold pair with no derived rule to override
        rules.append(Rule(name, "clearance", round(clr, 4),
                          condition=f"A.NetName == '{a}' && B.NetName == '{b}'"))
        facts.append({"pair": [a, b], "voltage": pv,
                      "clearance_mm": round(clr, 4), "coating": coating,
                      "rule": name, "waiver": waiver})
    return rules, facts


def package_rules(constraints: dict) -> tuple[list[Rule], list[dict]]:
    """Footprint-scoped clearance from constraints["package_ratings"]:
    [{"ref": "U2", "clearance_mm": 0.1, "why": "..."}].

    The owner's statement that a part is rated for its nets at its own pin
    pitch (an LM5069's 48 V pin 0.2 mm from its neighbour, a 100 V 0402's
    0.4 mm pad gap). Emitted after the HV rules, so inside that one
    courtyard it wins over the net-wide IPC value; the condition has
    exactly the shape check_creepage honours as waiver_class
    "dru_footprint_scope" (pairs stay visible as warnings at P8)."""
    rules: list[Rule] = []
    facts: list[dict] = []
    for e in constraints.get("package_ratings", []) or []:
        ref = str(e["ref"])
        if not re.fullmatch(r"[A-Za-z_]+[0-9]+", ref):
            raise ValueError(f"package_ratings: bad ref {ref!r}")
        clr = float(e["clearance_mm"])
        name = f"aiee_pkg_rating_{_safe(ref)}"
        rules.append(Rule(name, "clearance", round(clr, 4),
                          condition=f"A.intersectsCourtyard('{ref}') && "
                                    f"B.intersectsCourtyard('{ref}')"))
        facts.append({"ref": ref, "clearance_mm": round(clr, 4),
                      "why": e.get("why", ""), "rule": name})
    return rules, facts


def outer_microstrip_params(stackup: dict) -> tuple[float, float, float]:
    """(h, epsilon_r, outer_copper_oz) for outer-layer microstrip from a stackup.

    h = thickness of the dielectric between the outer copper and the next
    (reference) copper; er = its epsilon_r; oz = outer copper weight.
    """
    stack = stackup["stack"]
    outer_oz = stack[0].get("copper_oz", 1.0)
    # first dielectric after the top copper
    for layer in stack[1:]:
        if layer["type"] != "copper":
            return float(layer["thickness_mm"]), float(layer.get("epsilon_r", 4.5)), outer_oz
    return 0.2104, 4.05, outer_oz  # fallback (should not happen for >=2 copper)


# --------------------------------------------------------------- net classes

def _mm_tag(mm: float) -> str:
    """0.4 -> '0p4' (for class names)."""
    return ("%g" % round(mm, 4)).replace(".", "p")


def power_class_name(width_mm: float) -> str:
    """Class name for a power width: 1.75 -> 'Pwr_1p75mm'."""
    return f"Pwr_{_mm_tag(width_mm)}mm"


def net_classes(constraints: dict, power_facts: list[dict],
                diff_facts: list[dict], cap: dict,
                hv_facts: list[dict] | None = None
                ) -> tuple[list[dict], list[dict]]:
    """Build net_settings classes + netclass_patterns for the .kicad_pro.

    Power nets are bucketed BY THEIR OWN required width (one class per
    distinct width); a net whose IPC-2152 width is at or under the Default
    class width simply joins Default. Flattening every power net into one
    class at the widest width is a routing defect, not a conservatism: the
    netclass width is what the DSN export hands Freerouting, so a 20 mA rail
    inherited the 5 A trunk's 1.75 mm and could not enter its own pads.

    HV nets (voltage_rules facts) get the same treatment for clearance: each
    moves into a variant of the class it would otherwise have, at
    max(class clearance, its HV clearance). A pair-scoped HV rule raises both
    nets against everything - stricter than the DRU, never looser - and a
    waiver lowers nothing (a netclass cannot hold a per-pair value). The two
    nets of a diff pair take the same value so they stay in one class.
    """
    classes: list[dict] = []
    patterns: list[dict] = []

    def base_class(name: str, track: float, clearance: float,
                   dpw: float = 0.2, dpg: float = 0.25) -> dict:
        return {
            "name": name, "clearance": round(clearance, 4), "track_width": round(track, 4),
            "via_diameter": cap["min_via_diameter_mm"], "via_drill": cap["min_via_drill_mm"],
            "diff_pair_width": round(dpw, 4), "diff_pair_gap": round(dpg, 4),
            "diff_pair_via_gap": round(dpg, 4),
            "microvia_diameter": 0.3, "microvia_drill": 0.1, "line_style": 0,
            "bus_width": 12, "wire_width": 6,
            "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)",
        }

    default_track = default_class(cap)["track_width"]
    buckets: dict[float, list[dict]] = {}
    for f in power_facts:
        w = round(max(float(f["min_width_mm"]), default_track), 4)
        buckets.setdefault(w, []).append(f)
    for w in sorted(buckets):
        if w <= default_track + 1e-9:
            cname = "Default"          # thin rails need no wider class
        else:
            cname = power_class_name(w)
            # the fab floor, not a blanket 0.2: a power net that lands on a
            # fine-pitch part (CH224Q VBUS, 0.176 mm between pads) cannot
            # meet 0.2 at its own pads. Spacing a voltage needs comes from
            # voltages/voltage_pairs (the _HV_ variants below).
            classes.append(base_class(cname, w, cap["min_clearance_mm"]))
        for f in buckets[w]:
            f["netclass"] = cname       # traceability in the JSON report
            f["class_width_mm"] = w
            patterns.append({"netclass": cname, "pattern": f["net"]})
    for f in diff_facts:
        cname = f"Diff{f['impedance_ohm']}"
        if not any(c["name"] == cname for c in classes):
            classes.append(base_class(cname, f["width_mm"], cap["min_clearance_mm"],
                                      dpw=f["width_mm"], dpg=f["gap_mm"]))
        for n in f["pair"]:
            patterns.append({"netclass": cname, "pattern": n})

    hv: dict[str, float] = {}
    for f in hv_facts or []:
        if f.get("waiver"):
            continue
        for n in ([f["net"]] if "net" in f else f["pair"]):
            hv[n] = max(hv.get(n, 0.0), float(f["clearance_mm"]))
    for f in diff_facts:
        clr = max(hv.get(n, 0.0) for n in f["pair"])
        if clr:
            hv.update({n: clr for n in f["pair"]})
    by_name = {c["name"]: c for c in classes}
    by_name["Default"] = default_class(cap)
    assigned = {p["pattern"]: p for p in patterns}
    for net in sorted(hv):
        p = assigned.get(net)
        if p is None:
            p = assigned[net] = {"netclass": "Default", "pattern": net}
            patterns.append(p)
        base = by_name[p["netclass"]]
        clr = round(hv[net], 4)
        if clr <= base["clearance"] + 1e-9:
            continue
        prefix = "HV" if base["name"] == "Default" else base["name"] + "_HV"
        cname = f"{prefix}_{_mm_tag(clr)}mm"
        if cname not in by_name:
            by_name[cname] = {**base, "name": cname, "clearance": clr}
            classes.append(by_name[cname])
        p["netclass"] = cname
    for f in power_facts:
        f["netclass"] = assigned[f["net"]]["netclass"]
    used = {p["netclass"] for p in patterns}
    return [c for c in classes if c["name"] in used], patterns


# KiCad's own board-setup min_hole_clearance, which a project file carries
# unless someone sets it; hwde does not set it.
KICAD_HOLE_CLEARANCE_MM = 0.25


def hole_clearance_floor(cls: dict, hole_clearance: float) -> float:
    """Least class clearance that keeps a track laid at it outside the
    board's hole clearance of the class's own via: hole clearance minus the
    via ring. On 4layer_1oz (0.1016 clearance, 0.45/0.2 via) that is 0.125,
    not 0.1016, and Freerouting knows nothing of hole clearance."""
    ring = (cls["via_diameter"] - cls["via_drill"]) / 2
    return round(hole_clearance - ring, 4)


def default_class(cap: dict) -> dict:
    return {
        "name": "Default", "clearance": cap["min_clearance_mm"],
        "track_width": max(cap["min_trace_width_mm"], 0.2),
        "via_diameter": cap["min_via_diameter_mm"], "via_drill": cap["min_via_drill_mm"],
        "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
        "microvia_diameter": 0.3, "microvia_drill": 0.1, "line_style": 0,
        "bus_width": 12, "wire_width": 6,
        "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
    }


def update_pro(pro_path: Path, classes: list[dict], patterns: list[dict],
               cap: dict) -> None:
    """Read-modify-write the .kicad_pro: net_settings + design-rule minimums.

    Every class clearance is raised to hole_clearance_floor (the pro's own
    min_hole_clearance, else KiCad's 0.25 mm); a Diff class no higher than
    its pair gap. Keeps the rest of the (minimal, hand-rolled) pro intact -
    only touches net_settings, board.design_settings.rules and the fab-floor
    severities (LEARNINGS [kicad]: a minimal pro is the DRC authority; do not paste a
    full default blob). Floors + severities come from lib/fabfloors.py, the
    same source board_init writes, and are asserted after the merge."""
    pro = json.loads(pro_path.read_text(encoding="utf-8")) if pro_path.exists() else {}
    board = pro.setdefault("board", {})
    ds = board.setdefault("design_settings", {})
    hole_clr = float((ds.get("rules") or {}).get("min_hole_clearance",
                                                 KICAD_HOLE_CLEARANCE_MM))
    all_classes = []
    for c in [default_class(cap)] + classes:
        floor = hole_clearance_floor(c, hole_clr)
        if c["name"].startswith("Diff"):
            # the pair's own gap is a clearance between its two nets
            floor = min(floor, c["diff_pair_gap"])
        all_classes.append({**c, "clearance": max(c["clearance"], floor)})
    pro["net_settings"] = {
        "classes": all_classes,
        "meta": {"version": 3},
        "net_colors": None,
        "netclass_assignments": None,
        "netclass_patterns": patterns,
    }
    ds["rules"] = {**(ds.get("rules") or {}), **fabfloors.pro_rules(cap)}
    ds["rule_severities"] = {**(ds.get("rule_severities") or {}),
                             **fabfloors.pro_rule_severities()}
    bad = fabfloors.check_pro(pro, cap)
    if bad:
        raise RuntimeError("project file would ship sub-fab floors: "
                           + "; ".join(bad))
    pro_path.write_text(json.dumps(pro, indent=2), encoding="utf-8")


# --------------------------------------------------------------- render / main

DRU_HEADER = ("(version 1)\n"
              "# Generated by hwde rules_gen.py from constraints + jlc_capabilities.\n"
              "# Baseline (fab floor) first, per-net design rules last (later rule wins).\n")


def unique_rule_names(rules: list[Rule]) -> list[dict]:
    """Give every rule its own name, in place. _safe folds every
    non-alphanumeric to '_', so A_SPK_L+ and A_SPK_L- both become
    aiee_pwr_width_A_SPK_L; the second and later of a name get _2, _3.
    Returns [{from, to}] for the report."""
    seen: set[str] = {r.name for r in rules}
    count: dict[str, int] = {}
    renamed: list[dict] = []
    for r in rules:
        n = count[r.name] = count.get(r.name, 0) + 1
        if n == 1:
            continue
        k = n
        while f"{r.name}_{k}" in seen:
            k += 1
        new = f"{r.name}_{k}"
        seen.add(new)
        renamed.append({"from": r.name, "to": new})
        r.name = new
    return renamed


def render_dru(rules: list[Rule]) -> str:
    return DRU_HEADER + "\n".join(r.render() for r in rules) + "\n"


def build(constraints: dict, cap: dict, stackup: dict, baseline_only: bool,
          bg=None) -> tuple[list[Rule], dict]:
    """bg: the board (geom.BoardGeom), when there is one: its pads give the
    neck-down rules and the power classes' clearance."""
    outer_oz = cap.get("outer_copper_oz", 1.0)
    cu_mm = CU_OZ_MM.get(outer_oz, 0.035)
    rules = baseline_rules(cap)
    report: dict = {"power": [], "diff_pairs": [], "classes": [], "patterns": [],
                    "hv_rules": [], "necks": []}
    if not baseline_only:
        pr, pf = power_rules(constraints, cu_mm)
        nr, nf = neck_rules(bg, pf, cap) if bg is not None else ([], [])
        pairs = detect_diff_pairs(constraints)
        floor = float(cap.get("min_trace_width_mm", 0.0) or 0.0)
        bga_refs = _bga_refs(bg)
        dr, df = diff_pair_rules(pairs, stackup, constraints, floor, bga_refs)
        zr, zf = impedance_rules(constraints, stackup, pairs, floor, bga_refs)
        hr, hf = voltage_rules(constraints, cap)
        kr, kf = package_rules(constraints)
        br, bf = bga_rules(bg, cap) if bg is not None else ([], [])
        # HV and package-rating clearances stay last: KiCad's later rule
        # wins, and a courtyard's escape clearance must never undercut them
        rules += pr + nr + dr + zr + br + hr + kr
        report["package_rules"] = kf
        report["necks"] = nf
        report["bga_escape"] = bf
        report["renamed_rules"] = unique_rule_names(rules)
        classes, patterns = net_classes(constraints, pf, df, cap, hf)
        report["power"] = pf
        report["diff_pairs"] = df
        report["impedance"] = zf
        report["stripline_layers"] = stripline_layers(stackup, constraints)
        report["classes"] = classes
        report["patterns"] = patterns
        report["hv_rules"] = hf
    return rules, report


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--constraints", help="constraints.json (high_speed/power/diff_pairs)")
    ap.add_argument("--layers", type=int, default=4)
    ap.add_argument("--copper-oz", type=float, default=None,
                    help="outer copper weight (default: derived from the "
                         "stackup's own outer copper, like board_init; an "
                         "explicit value contradicting an explicitly named "
                         "stackup is refused)")
    ap.add_argument("--stackup", help="stackup name from stackups.yaml "
                    "(default: defaults[<layers>])")
    ap.add_argument("--out-dru", help="write the .kicad_dru here")
    ap.add_argument("--pro", help="also update this .kicad_pro (net classes + minimums)")
    ap.add_argument("--pcb", help="the board, for its pads: neck-down rules "
                    "where a power net meets pads narrower than its width "
                    "(default: the .kicad_pcb beside --pro, when there is one)")
    ap.add_argument("--baseline-only", action="store_true",
                    help="emit only the fab-floor baseline (for template generation)")
    ap.add_argument("--check-dru", metavar="PATH",
                    help="check an EXISTING .kicad_dru for the aiee_* fab-floor "
                         "baseline instead of generating (exit 1 on failures); "
                         "resolves the capability row from --layers/--stackup/"
                         "--copper-oz like generation does")
    ap.add_argument("--out", help="write the JSON report here instead of stdout")
    args = ap.parse_args(argv)

    try:
        # Stackup resolution FIRST: the copper weight (-> capability row and
        # every fab floor) is derived from it (T6 P5-1). rules_gen previously
        # defaulted --copper-oz to 1.0 REGARDLESS of --stackup, silently
        # selecting sub-fab 1-oz floors and double-width IPC-2152 power
        # classes on every 2-oz board - the exact defect family T1 killed in
        # board_init (board-setup.md's own step-3 command omits the flag).
        stacks = load_yaml(STACKUP_FILE)
        stk_name = args.stackup or stacks["defaults"].get(args.layers)
        stackup = stacks["stackups"].get(stk_name) if stk_name else None
        if stackup is not None and stackup.get("available") is False \
                and not args.baseline_only:
            ret = stackup.get("retired") or {}
            raise SystemExit(
                f"stackup {stk_name} is marked available: false in "
                f"stackups.yaml - {ret.get('reason', 'not offered by JLC')}; "
                f"use one of: {', '.join(ret.get('replacements') or []) or 'see stackups.yaml'}")

        derived_oz = None
        if stackup is not None:
            coppers = [ly for ly in stackup["stack"] if ly["type"] == "copper"]
            if coppers:
                derived_oz = float(coppers[0].get("copper_oz", 1.0))
        if args.copper_oz is not None and args.stackup and derived_oz is not None \
                and abs(args.copper_oz - derived_oz) > 1e-9:
            raise SystemExit(
                f"--copper-oz {args.copper_oz:g} contradicts stackup "
                f"{stk_name} (outer copper {derived_oz:g} oz). Drop the flag "
                f"- it is derived from the stackup - or pick the matching "
                f"stackup; a board must never quietly get another copper "
                f"weight's fab floors (T1)")
        outer_oz = args.copper_oz if args.copper_oz is not None else \
            (derived_oz if derived_oz is not None else 1.0)

        # Laminate: the named stackup's, checked against the board's own
        # stackup block when there is a board - a Rogers board must not get
        # FR-4 floors or FR-4 diff-pair geometry (SI-09 at design time).
        if stk_name and stackup is None and not args.baseline_only:
            why = fabmaterial.not_offered(fabmaterial.family_of_name(stk_name))
            raise SystemExit(f"stackup {stk_name!r} not in stackups.yaml"
                             + (f" - {why}" if why else ""))
        try:
            material = (fabmaterial.stackup_family(stackup)
                        if stackup is not None else fabmaterial.FR4)
        except fabmaterial.MaterialError as exc:
            raise SystemExit(f"stackup {stk_name}: {exc}") from exc
        board_pcb = Path(args.pcb) if args.pcb else (
            Path(args.pro).with_suffix(".kicad_pcb") if args.pro else None)
        if board_pcb is not None and board_pcb.is_file() \
                and fabmaterial.board_materials(board_pcb):
            board_mat = fabmaterial.board_family(board_pcb)
            if board_mat != material:
                hint = ""
                if not args.stackup:
                    same = [n for n, s in stacks["stackups"].items()
                            if s.get("available") is not False
                            and s.get("layers") == args.layers
                            and s.get("material_family") == board_mat]
                    hint = (f"; pass --stackup ({', '.join(same)})" if same
                            else "")
                raise SystemExit(
                    f"{board_pcb.name} is built on {board_mat} (its stackup "
                    f"block) but rules_gen resolved stackup {stk_name} "
                    f"({material}){hint}. Fab floors and impedance geometry "
                    "would be the wrong laminate's")
        try:
            cls, cap = fabfloors.profile(args.layers, outer_oz,
                                         material=material)
        except fabfloors.FabFloorError as exc:
            raise SystemExit(str(exc)) from exc

        if args.check_dru:
            # T6 P5-5 (ladder row 160): assert a (possibly hand-edited) DRU
            # still carries the aiee_* fab floors. Read-only; exit 1 = the
            # named failures below, for the fix-loop / a later gate wiring.
            failures = fabfloors.check_dru(
                Path(args.check_dru).read_text(encoding="utf-8"), cap)
            result = {
                "script": "rules_gen", "mode": "check_dru",
                "status": "pass" if not failures else "fail",
                "dru": args.check_dru, "capability_class": cls,
                "copper_oz": outer_oz, "failures": failures,
            }
            out = json.dumps(result, indent=2)
            if args.out:
                Path(args.out).write_text(out, encoding="utf-8")
            else:
                print(out)
            return 0 if not failures else 1

        if stackup is None and not args.baseline_only:
            # only needed for diff pairs; fall back to the first AVAILABLE
            # stackup with a matching layer count, else the first available
            stackup = next(
                (s for s in stacks["stackups"].values()
                 if s.get("available") is not False
                 and s.get("layers") == args.layers),
                next(s for s in stacks["stackups"].values()
                     if s.get("available") is not False))

        constraints = {}
        if args.constraints:
            constraints = json.loads(Path(args.constraints).read_text(encoding="utf-8"))

        pcb = Path(args.pcb) if args.pcb else (
            Path(args.pro).with_suffix(".kicad_pcb") if args.pro else None)
        if args.pcb and not pcb.is_file():
            raise SystemExit(f"--pcb not found: {pcb}")
        bg = None if args.baseline_only else board_pads(pcb)
        rules, report = build(constraints, cap, stackup or {}, args.baseline_only,
                              bg)
        dru_text = render_dru(rules)

        if args.out_dru:
            Path(args.out_dru).write_text(dru_text, encoding="utf-8")
        if args.pro and not args.baseline_only:
            update_pro(Path(args.pro), report["classes"], report["patterns"], cap)

        result = {
            "script": "rules_gen", "status": "pass",
            "capability_class": cls, "stackup": stk_name,
            "copper_oz": outer_oz, "material": material,
            "rule_count": len(rules),
            "rule_names": [r.name for r in rules],
            "renamed_rules": report.get("renamed_rules", []),
            "power": report["power"], "diff_pairs": report["diff_pairs"],
            "impedance": report.get("impedance", []),
            "stripline_layers": report.get("stripline_layers", {}),
            "hv_rules": report["hv_rules"],
            "package_rules": report.get("package_rules", []),
            "necks": report["necks"],
            "warnings": [
                f"{f['net']} is plane_fed but constraints planes has no pour "
                "for it: its taps are sized for the plane that isn't there"
                for f in report["power"]
                if f.get("plane_fed") and not f.get("plane_declared")],
            "pcb": str(pcb) if bg is not None else None,
            "classes": [c["name"] for c in report["classes"]],
            "class_clearances": {c["name"]: c["clearance"]
                                 for c in report["classes"]},
            "out_dru": args.out_dru, "pro": args.pro if not args.baseline_only else None,
        }
        if not args.out_dru:
            result["dru"] = dru_text
        out = json.dumps(result, indent=2)
        if args.out:
            Path(args.out).write_text(out, encoding="utf-8")
        else:
            print(out)
        return 0
    except SystemExit as e:
        print(json.dumps({"script": "rules_gen", "status": "error", "error": str(e)}))
        return 2
    except Exception:
        print(json.dumps({"script": "rules_gen", "status": "error",
                          "error": traceback.format_exc()}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
