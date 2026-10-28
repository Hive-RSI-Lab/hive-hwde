"""check_decoupling.py - decoupling proximity + loop inductance (SPEC 6.3).

Input is the schematic-generation metadata (cap<->pin association; S7 emits it,
tests/golden/<board>/decoupling.json hand-writes it for the corpus):

    {"associations": [
        {"cap": "C1",            # decoupler refdes
         "ic": "U1", "pin": "48",# served IC pin (pad number, string)
         "rail": "+3V3",         # power net (exact board name)
         "gnd": "GND",           # return net (default GND)
         "value": "100nF",       # cap value -> threshold class
         "role": "reg_input",    # optional: switching-regulator input cap
         "max_dist_mm": 10.0,    # optional DECLARED limits: exceeding one is
         "max_loop_nh": 10.0}]}  # an ERROR (class defaults only as fallback)

Per association:
 - Manhattan distance cap rail-pad -> IC pin pad (spec metric; Euclidean also
   reported). Duplicate pin numbers (tabs) resolve to the nearest pad.
 - Vias in the loop: 0 rail vias when cap pad and pin pad sit on connected
   same-layer rail copper, else 2 (down/up through the plane); the ground leg
   is the distance to the nearest ground via (through pour or trace) and
   counts 1 via.
 - Loop inductance estimate: 0.7 nH/mm trace (kicad-happy DC-003 heuristic)
   x (rail Manhattan + ground leg) + 1 nH per via (spec heuristic).
 - Thresholds per cap value class (defaults below, per-association override):
       class      value          dist warn/error   loop warn/error
       bulk       >= 1 uF          20 / 30 mm        30 / 60 nH
       mid        10 nF..1 uF      10 / 15 mm        10 / 20 nH
       hf         < 10 nF           5 / 7.5 mm        6 / 12 nH
 - A stale association (missing ref/pin, net mismatch) is itself an error
   violation (kind=metadata_mismatch): the metadata no longer matches the
   board, which the fix loop must reconcile.
 - role "reg_input" marks a SWITCHING-REGULATOR input cap (buck/boost VIN).
   Grouped per (ic, pin, rail): at least one reg_input association must be
   an HF-capable ceramic (<= 1 uF, or explicit class "hf") whose rail pad
   sits within the hf-class error distance (7.5 mm) of the pin, else ONE
   error (kind=reg_input_no_hf) anchored at the pin. The input loop carries
   the full trapezoidal switch current, so a lone bulk cap is not enough -
   the exact blind spot that let lumina-carrier ship U21 (TPS563201) with
   only a 22 uF at 9.89 mm (retrospective R1, rework on assembled boards).
   Individual associations keep their value-class distance/loop checks:
   declaring the bulk cap reg_input does NOT tighten its own limits.

CLI: --pcb board.kicad_pcb --metadata decoupling.json [--out report.json]
     exit 0/1/2 per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from pathlib import Path

from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
from checklib import CheckError, manhattan, violation  # noqa: E402

SCRIPT = "check_decoupling"
NH_PER_MM = 0.7        # trace inductance heuristic (kicad-happy DC-003)
NH_PER_VIA = 1.0       # spec: via ~1 nH
GND_STUB_WARN_MM = 5.0 # cap ground pad this far from any ground via -> warning

CLASSES = {  # value_min_f, dist warn/error mm, loop warn/error nH
    "bulk": {"min_f": 1e-6, "dist": (20.0, 30.0), "loop": (30.0, 60.0)},
    "mid":  {"min_f": 1e-8, "dist": (10.0, 15.0), "loop": (10.0, 20.0)},
    "hf":   {"min_f": 0.0,  "dist": (5.0, 7.5),   "loop": (6.0, 12.0)},
}

# role=reg_input: a cap this size or smaller still works as the HF input
# ceramic (the retro rework spec is "100 nF, a 1 uF alongside is better");
# anything bigger is a reservoir, not the switch-current loop.
REG_INPUT_HF_MAX_F = 1e-6

# accept the micro sign and Greek mu (datasheets use both) alongside ASCII 'u'.
_VAL_RE = re.compile(r"^\s*([0-9.]+)\s*([pnumµμ]?)F?\s*$", re.IGNORECASE)
# real-world value strings carry suffixes ("10uF 25V X5R", "22uF 16V tantalum");
# fall back to the first <number><prefix>F token. Unit letter + F required so
# "25V"/"X7R" can never match (S14 finding: pdn_no_bulk false-positived on
# every multi-token bulk cap value).
_TOKEN_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([pnumµμ])F(?![a-z0-9])",
                       re.IGNORECASE)
_SCALE = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
          "m": 1e-3, "": 1.0}


def parse_farads(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value or "")
    m = _VAL_RE.match(s)
    if not m:
        m = _TOKEN_RE.search(s)
    if not m:
        return None
    return float(m.group(1)) * _SCALE[m.group(2).lower()]


def value_class(farads: float | None) -> str:
    if farads is None:
        return "mid"  # unknown value: middle class, documented default
    for name in ("bulk", "mid", "hf"):
        if farads >= CLASSES[name]["min_f"]:
            return name
    return "hf"


def same_layer_connected(bg: geom.BoardGeom, net: str, a, b) -> bool:
    """True if pads at centers a/b share connected same-layer copper of net."""
    common = set(a.layers) & set(b.layers) & set(bg.copper_layers)
    for layer in common:
        copper = bg.net_copper(net, layer)
        for part in getattr(copper, "geoms", [copper]):
            if part.intersects(Point(a.center)) and part.intersects(Point(b.center)):
                return True
    return False


def nearest_via_mm(bg: geom.BoardGeom, net: str, center) -> float | None:
    ds = [math.hypot(v.at[0] - center[0], v.at[1] - center[1])
          for v in bg.vias_of(net)]
    return min(ds) if ds else None


def sev_for(value: float, warn: float, error: float) -> str | None:
    if value > error:
        return "error"
    if value > warn:
        return "warning"
    return None


def check_association(bg: geom.BoardGeom, a: dict, reg_groups: dict | None = None):
    cap, ic = a.get("cap"), a.get("ic")
    pin = str(a.get("pin", ""))
    rail, gnd = a.get("rail"), a.get("gnd", "GND")
    if not (cap and ic and pin and rail):
        raise CheckError(f"association needs cap/ic/pin/rail: {a}")

    def mismatch(msg):
        return [violation(SCRIPT, "error", None, None, rail, [cap, ic],
                          msg, SCRIPT, kind="metadata_mismatch")], None

    rail_pads = bg.pads_of(net=rail, ref=cap)
    if not rail_pads:
        return mismatch(f"{cap} has no pad on rail {rail} (moved/rewired? "
                        f"decoupling metadata is stale)")
    pin_pads = [p for p in bg.pads_of(ref=ic) if p.number == pin]
    if not pin_pads:
        return mismatch(f"{ic} has no pad number {pin}")
    if all(p.net != rail for p in pin_pads):
        return mismatch(f"{ic} pin {pin} is not on rail {rail} "
                        f"(board says {sorted({p.net for p in pin_pads})})")
    pin_pads = [p for p in pin_pads if p.net == rail]

    cap_pad, pin_pad = min(
        ((c, p) for c in rail_pads for p in pin_pads),
        key=lambda cp: manhattan(cp[0].center, cp[1].center))
    dist = manhattan(cap_pad.center, pin_pad.center)
    euclid = math.hypot(cap_pad.center[0] - pin_pad.center[0],
                        cap_pad.center[1] - pin_pad.center[1])

    rail_vias = 0 if same_layer_connected(bg, rail, cap_pad, pin_pad) else 2
    gnd_pads = bg.pads_of(net=gnd, ref=cap)
    gnd_leg = None
    if gnd_pads:
        gnd_leg = nearest_via_mm(bg, gnd, gnd_pads[0].center)
    gnd_leg = 0.0 if gnd_leg is None else gnd_leg
    loop_nh = NH_PER_MM * (dist + gnd_leg) + NH_PER_VIA * (rail_vias + 1)

    farads = parse_farads(a.get("value"))
    cls = a.get("class") or value_class(farads)
    if reg_groups is not None and a.get("role") == "reg_input":
        reg_groups.setdefault((ic, pin, rail), []).append({
            "cap": cap, "value": a.get("value", "?"), "dist": dist,
            "hf_capable": (a.get("class") == "hf"
                           or (farads is not None
                               and farads <= REG_INPUT_HF_MAX_F)),
            "pin_pad": pin_pad})
    # U20: a DECLARED per-association limit is the board's own engineering
    # contract - exceeding it is an ERROR outright. The warn/error band only
    # applies to the class-default heuristics (bb-adc escape: a declared
    # 2.5 mm limit exceeded at 3.97 mm read as a warning because the error
    # threshold stayed at the class default, so `gate --gate place` passed).
    dist_warn, dist_err = CLASSES[cls]["dist"]
    if a.get("max_dist_mm") is not None:
        dist_warn = dist_err = float(a["max_dist_mm"])
    loop_warn, loop_err = CLASSES[cls]["loop"]
    if a.get("max_loop_nh") is not None:
        loop_warn = loop_err = float(a["max_loop_nh"])

    violations = []
    common = {"kind": None, "cap_class": cls,
              "manhattan_mm": checklib.rnd(dist),
              "euclid_mm": checklib.rnd(euclid),
              "loop_nh": checklib.rnd(loop_nh),
              "vias_in_loop": rail_vias + 1, "pin": f"{ic}.{pin}"}
    sev = sev_for(dist, dist_warn, dist_err)
    if sev:
        violations.append(violation(
            SCRIPT, sev, cap_pad.center, cap_pad.layers[0], rail, [cap, ic],
            f"{cap} ({a.get('value', '?')}) rail pad is {dist:.1f} mm "
            f"Manhattan ({euclid:.1f} mm direct) from {ic} pin {pin} "
            f"({cls} class limit {dist_warn:.1f} mm)", SCRIPT,
            **{**common, "kind": "decoupler_distance",
               "limit_mm": dist_warn}))
    sev = sev_for(loop_nh, loop_warn, loop_err)
    if sev:
        violations.append(violation(
            SCRIPT, sev, cap_pad.center, cap_pad.layers[0], rail, [cap, ic],
            f"{cap} decoupling loop ~{loop_nh:.1f} nH for {ic} pin {pin} "
            f"({cls} class limit {loop_warn:.1f} nH: {dist:.1f} mm rail + "
            f"{gnd_leg:.1f} mm gnd + {rail_vias + 1} via)", SCRIPT,
            **{**common, "kind": "decoupler_loop", "limit_nh": loop_warn}))
    if gnd_pads and gnd_leg > GND_STUB_WARN_MM:
        violations.append(violation(
            SCRIPT, "warning", gnd_pads[0].center, gnd_pads[0].layers[0],
            gnd, [cap], f"{cap} ground pad is {gnd_leg:.1f} mm from the "
            f"nearest {gnd} via (> {GND_STUB_WARN_MM} mm return stub)",
            SCRIPT, **{**common, "kind": "gnd_stub_long"}))

    facts = {"cap": cap, "pin": f"{ic}.{pin}", "rail": rail, "class": cls,
             "manhattan_mm": checklib.rnd(dist),
             "euclid_mm": checklib.rnd(euclid),
             "gnd_leg_mm": checklib.rnd(gnd_leg),
             "vias_in_loop": rail_vias + 1,
             "loop_nh": checklib.rnd(loop_nh)}
    if a.get("role"):
        facts["role"] = a["role"]
    return violations, facts


def check_reg_inputs(reg_groups: dict) -> list[dict]:
    """Per regulator input pin (associations marked role=reg_input): error
    unless one member is an HF-capable ceramic within the hf error distance.
    Metadata-mismatched associations never reach the group (their own error
    already fired), so an all-stale group is silently absent by design."""
    limit = CLASSES["hf"]["dist"][1]
    out = []
    for (ic, pin, rail), members in sorted(reg_groups.items()):
        if any(m["hf_capable"] and m["dist"] <= limit for m in members):
            continue
        near = min(members, key=lambda m: m["dist"])
        pin_pad = near["pin_pad"]
        out.append(violation(
            SCRIPT, "error", pin_pad.center, pin_pad.layers[0], rail,
            [ic] + sorted(m["cap"] for m in members),
            f"{ic} pin {pin} ({rail}) is a switching-regulator input with "
            f"no HF ceramic (<= {REG_INPUT_HF_MAX_F * 1e6:g} uF) within "
            f"{limit:g} mm: nearest input cap {near['cap']} "
            f"({near['value']}) is {near['dist']:.2f} mm away", SCRIPT,
            kind="reg_input_no_hf", pin=f"{ic}.{pin}",
            nearest_mm=checklib.rnd(near["dist"]), limit_mm=limit))
    return out


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Decoupling proximity / loop inductance check.")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--metadata", required=True,
                    help="decoupling.json (cap<->pin associations)")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    meta = checklib.load_json(args.metadata, "decoupling metadata")
    assocs = meta.get("associations", [])
    bg = geom.load_board(Path(args.pcb))
    bg.assert_fresh()

    violations: list[dict] = []
    checked: list[dict] = []
    reg_groups: dict = {}
    for a in assocs:
        vs, facts = check_association(bg, a, reg_groups)
        violations.extend(vs)
        if facts:
            checked.append(facts)
    violations.extend(check_reg_inputs(reg_groups))

    payload = checklib.report(SCRIPT, args.pcb, violations, checked=checked)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
