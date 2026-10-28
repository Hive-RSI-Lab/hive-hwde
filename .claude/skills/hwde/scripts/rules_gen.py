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
         stackup) + inner-layer `disallow track` (V12: only OUTER microstrip is
         solved, T6 P5-4)
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
     2026-07-28 [routing][rules_gen][freerouting]) and one class per differential
     impedance, assigned by netclass_patterns; plus board.design_settings.rules
     minimums and the fab-floor severities from lib/fabfloors.py - the same single
     source board_init writes, so the two files cannot disagree. These drive the
     router (S11) and placement; DRU is the DRC enforcer.
     Every net with an aiee_hv_* clearance rule is ALSO moved into a class carrying
     that clearance (a `_HV_<c>mm` variant of its power/diff class, or `HV_<c>mm`),
     and every class clearance is floored at the board's min_hole_clearance minus
     the class's via ring: the DSN export hands Freerouting netclasses only, so a
     DRU-only clearance cost PCB-0018 a 1 h route with 406 clearance errors.

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


def power_rules(constraints: dict, cu_mm: float) -> tuple[list[Rule], list[dict]]:
    """Per-power-net minimum track width from IPC-2152 (check_current)."""
    rules: list[Rule] = []
    facts: list[dict] = []
    for entry in constraints.get("power", []):
        net = entry["net"]
        cur = float(entry["current_a"])
        dt = float(entry.get("dt_c", 10.0))
        w = check_current.required_width_mm(cur, dt, cu_mm)
        safe = re.sub(r"[^A-Za-z0-9]", "_", net).strip("_")
        rules.append(Rule(f"aiee_pwr_width_{safe}", "track_width", round(w, 4),
                          condition=f"A.NetName == '{net}' && A.Type == 'track'"))
        facts.append({"net": net, "current_a": cur, "dt_c": dt,
                      "min_width_mm": round(w, 4)})
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


def diff_pair_rules(pairs: list[dict], stackup: dict) -> tuple[list[Rule], list[dict]]:
    """diff_pair_gap rule + computed width/gap per pair (impedance.py)."""
    rules: list[Rule] = []
    facts: list[dict] = []
    # outer microstrip: dielectric gap to the nearest inner plane + its er
    h, er, cu_oz = outer_microstrip_params(stackup)
    for dp in pairs:
        w, gap = imp.diff_pair(float(dp["impedance_ohm"]), h,
                               imp.CU_OZ_MM.get(cu_oz, 0.035), er)
        safe = re.sub(r"[^A-Za-z0-9]", "_", dp["base"]).strip("_")
        # gap floor: a differential gap smaller than this is a skew/coupling defect
        rules.append(Rule(
            f"aiee_diff_gap_{safe}", "diff_pair_gap", round(gap, 4),
            condition=(f"A.NetName == '{dp['p']}' || A.NetName == '{dp['n']}'")))
        # Inner-layer guard (T6 P5-4): the width/gap above is solved for the
        # OUTER coated microstrip; the same pair on an inner layer (1080B's
        # inner dielectrics are nothing like it) would be mis-sized, so an
        # inner-layer segment of the pair is a NAMED DRC violation instead
        # of a silent impedance error. Layer transitions via vias remain
        # legal (vias are not tracks). Waiver path: reference/remediations -
        # delete the rule consciously only after solving the inner geometry
        # with impedance.field_stripline (check_diffpair reports the result).
        rules.append(Rule(
            f"aiee_diff_outer_only_{safe}", "disallow track", layer="inner",
            condition=(f"A.NetName == '{dp['p']}' || A.NetName == '{dp['n']}'")))
        facts.append({"pair": [dp["p"], dp["n"]], "impedance_ohm": dp["impedance_ohm"],
                      "width_mm": w, "gap_mm": gap})
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
            classes.append(base_class(cname, w, max(cap["min_clearance_mm"], 0.2)))
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


def render_dru(rules: list[Rule]) -> str:
    return DRU_HEADER + "\n".join(r.render() for r in rules) + "\n"


def build(constraints: dict, cap: dict, stackup: dict, baseline_only: bool
          ) -> tuple[list[Rule], dict]:
    outer_oz = cap.get("outer_copper_oz", 1.0)
    cu_mm = CU_OZ_MM.get(outer_oz, 0.035)
    rules = baseline_rules(cap)
    report: dict = {"power": [], "diff_pairs": [], "classes": [], "patterns": [],
                    "hv_rules": []}
    if not baseline_only:
        pr, pf = power_rules(constraints, cu_mm)
        pairs = detect_diff_pairs(constraints)
        dr, df = diff_pair_rules(pairs, stackup)
        hr, hf = voltage_rules(constraints, cap)
        rules += pr + dr + hr
        classes, patterns = net_classes(constraints, pf, df, cap, hf)
        report["power"] = pf
        report["diff_pairs"] = df
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

        try:
            cls, cap = fabfloors.profile(args.layers, outer_oz)
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

        rules, report = build(constraints, cap, stackup or {}, args.baseline_only)
        dru_text = render_dru(rules)

        if args.out_dru:
            Path(args.out_dru).write_text(dru_text, encoding="utf-8")
        if args.pro and not args.baseline_only:
            update_pro(Path(args.pro), report["classes"], report["patterns"], cap)

        result = {
            "script": "rules_gen", "status": "pass",
            "capability_class": cls, "stackup": stk_name,
            "copper_oz": outer_oz,
            "rule_count": len(rules),
            "rule_names": [r.name for r in rules],
            "power": report["power"], "diff_pairs": report["diff_pairs"],
            "hv_rules": report["hv_rules"],
            "classes": [c["name"] for c in report["classes"]],
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
