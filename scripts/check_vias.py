"""check_vias.py - every via can be built in the board's stackup: blind,
buried and micro spans against the lamination, laser and mechanical drill
sizes, aspect ratios, rings and spacing, and the stack's own balance
(P8 verify suite).

KiCad 10 draws a via of any type between any two copper layers, and its DRC
checks the drawn copper, not whether a fab can make the hole: a buried via
from In1 to In3 passes DRC on a standard 6-layer board, which JLC laminates
once and drills through only, so it can't exist. A laser via is a 0.075 to
0.15 mm hole that crosses one thin prepreg; a mechanical one that a core is
too thin for, or a hole 20 times deeper than wide, can't be plated. Those
are fab facts, read from:

  reference/stackups.yaml       the board's lamination (matched by name or by
                                its layer thicknesses) and, for an HDI build,
                                the via spans JLC laminates it for (`vias`)
  reference/jlc_capabilities.yaml  `hdi:` (JLC's HDI FAQ) and
                                `through_max_aspect`

Findings (error unless marked):
  via_type_span      the KiCad via type doesn't fit its span: a micro via
                     crossing more than one dielectric, a blind via that
                     reaches neither outer layer or both, a buried one that
                     reaches an outer layer
  via_no_lamination  a blind/buried/micro via on a board whose stack is no
                     stackups.yaml build (or has no stack block): nothing
                     says the span can be laminated
  via_needs_hdi      a blind/buried/micro via on a standard lamination (one
                     press, drilled through only); names the HDI builds in
                     stackups.yaml that make that span
  via_span_unbuilt   a non-through span the board's HDI build isn't
                     laminated for (its `vias` list)
  via_spans_cross    two mechanical blind/buried spans that overlap without
                     nesting: no order of presses drills both
  via_drill          a laser via's hole outside 0.075-0.15 mm, a buried or
                     mechanical blind one outside 0.15-0.55 mm
  via_aspect         a laser via deeper than its hole (1:1), a mechanical one
                     deeper than 16x its hole
  via_laser_depth    the dielectric a laser via crosses outside 0.05-0.127 mm
  via_core           a buried via across less than 0.30 mm of dielectric
  via_ring           a laser or buried via's pad less than its hole + 0.15 mm
                     (an HDI board's through via: + 0.10 mm)
  via_hole_spacing   a laser via within 0.13 mm (same net) / 0.24 mm (other
                     net) of another via sharing a layer; a stacked via (same
                     centre, same net) is a stack, not a conflict
  via_hdi_layers     blind/buried/micro vias on a board outside JLC's HDI
                     range (4-24 layers)
  stackup_unbalanced warning: the stack isn't mirror-symmetric about its
                     centre (copper weight, or a dielectric more than 10%
                     off its mirror), which bows the board in reflow

CLI: --pcb board.kicad_pcb [--stackup NAME] [--out report.json]
     exit 0 pass / 1 violations / 2 error, per SPEC section 6.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402

SCRIPT = "check_vias"
SOURCE = SCRIPT  # verify_all, gate and the scorecard match a finding to its check by source
REFERENCE = HERE.parent / "reference"
STACKUPS = REFERENCE / "stackups.yaml"
CAPABILITIES = REFERENCE / "jlc_capabilities.yaml"
# a board's stack block matches a stackups.yaml entry when every dielectric
# is within this of the entry's (mm)
MATCH_TOL_MM = 0.002
BALANCE_TOL = 0.10
EPS = 1e-6


# ------------------------------------------------------------ stackup

def _entry_gaps(entry: dict) -> list[float]:
    """Dielectric height per copper-to-copper gap of a stackups.yaml entry."""
    gaps, acc, seen_cu = [], 0.0, False
    for s in entry["stack"]:
        if s["type"] == "copper":
            if seen_cu:
                gaps.append(acc)
            seen_cu, acc = True, 0.0
        else:
            acc += float(s["thickness_mm"])
    return gaps


def match_entry(stk: "geom.Stackup", stackups: dict) -> tuple[str | None, dict | None]:
    """The stackups.yaml entry the board's own (stackup) block is: same
    copper count and every dielectric within MATCH_TOL_MM. None when the
    board has no block or a stack nobody listed."""
    if stk.assumed:
        return None, None
    have = [d["height"] for d in stk.dielectrics]
    hits = []
    for name, e in stackups.items():
        if e.get("layers") != len(stk.copper_layers):
            continue
        want = _entry_gaps(e)
        if len(want) == len(have) and all(abs(a - b) <= MATCH_TOL_MM
                                          for a, b in zip(have, want)):
            hits.append((e.get("available") is False, name))
    if not hits:
        return None, None
    hits.sort()
    name = hits[0][1]
    return name, stackups[name]


def span_of(v: "geom.Via", cu: list[str]) -> tuple[int, int]:
    i, j = cu.index(v.layers[0]), cu.index(v.layers[-1])
    return min(i, j), max(i, j)


def depth(stk: "geom.Stackup", i: int, j: int) -> float:
    """Dielectric crossed between copper i and copper j (indices)."""
    return sum(d["height"] for d in stk.dielectrics[i:j])


def hole_depth(stk: "geom.Stackup", i: int, j: int) -> float:
    """Hole length from copper i to copper j, both coppers included."""
    cu = stk.copper_layers
    return depth(stk, i, j) + sum(stk.copper_thickness.get(cu[k], 0.0)
                                  for k in range(i, j + 1))


def entry_spans(entry: dict | None, cu: list[str]) -> dict[tuple[int, int], str]:
    """{(i, j): kind} an HDI entry is laminated for (through excluded)."""
    out = {}
    for r in (entry or {}).get("vias") or []:
        if r["kind"] == "through":
            continue
        if r["from"] in cu and r["to"] in cu:
            a, b = sorted((cu.index(r["from"]), cu.index(r["to"])))
            out[(a, b)] = r["kind"]
    return out


def hdi_builds(stackups: dict, n: int, span: tuple[str, str]) -> list[str]:
    """stackups.yaml HDI entries of n layers that laminate this span."""
    out = []
    for name, e in stackups.items():
        if e.get("layers") != n or e.get("available") is False:
            continue
        if any({r["from"], r["to"]} == set(span) for r in e.get("vias") or []):
            out.append(name)
    return out


# ------------------------------------------------------------ the check

def check(pcb: Path, stackup_name: str | None = None,
          stackups_file: Path = STACKUPS,
          cap_file: Path = CAPABILITIES) -> tuple[list[dict], dict]:
    bg = geom.load_board(pcb)
    stk = bg.stackup
    cu = list(bg.copper_layers)
    n = len(cu)
    stackups = yaml.safe_load(stackups_file.read_text(encoding="utf-8"))["stackups"]
    caps = yaml.safe_load(cap_file.read_text(encoding="utf-8"))
    hdi = caps.get("hdi") or {}
    through_ar = float(caps.get("through_max_aspect", 16.0))

    if stackup_name:
        if stackup_name not in stackups:
            raise checklib.CheckError(f"stackup {stackup_name!r} not in "
                                      f"{stackups_file.name}")
        name, entry = stackup_name, stackups[stackup_name]
    else:
        name, entry = match_entry(stk, stackups)
    allowed = entry_spans(entry, cu)
    is_hdi_entry = bool(allowed)

    vias = bg.vias_of()
    blind = [v for v in vias if v.kind != "through" or
             span_of(v, cu) != (0, n - 1)]
    facts = {"stackup": name, "stackup_source": (
                 "--stackup" if stackup_name else
                 "matched by layer thicknesses" if name else
                 "no stackups.yaml entry matches the board's stack"),
             "copper_layers": n, "vias": len(vias),
             "non_through": len(blind),
             "hdi_entry": is_hdi_entry,
             "by_kind": {}}
    for v in vias:
        facts["by_kind"][v.kind] = facts["by_kind"].get(v.kind, 0) + 1
    viols: list[dict] = []

    def vio(sev, v, kind, msg, **extra):
        viols.append(checklib.violation(
            SCRIPT, sev, v.at if v is not None else None,
            (f"{v.layers[0]}-{v.layers[-1]}" if v is not None else None),
            (v.net or None) if v is not None else None, [], msg, SOURCE,
            kind=kind, **extra))

    def desc(v):
        return (f"{v.kind} via {v.layers[0]}-{v.layers[-1]} "
                f"({v.drill:g}/{v.diameter:g} mm) at "
                f"({v.at[0]:.3f}, {v.at[1]:.3f}){' on ' + v.net if v.net else ''}")

    lo_l, hi_l = (hdi.get("layers") or [4, 24])
    if blind and not (lo_l <= n <= hi_l):
        vio("error", blind[0], "via_hdi_layers",
            f"{len(blind)} blind/buried/micro via(s) on a {n}-layer board; "
            f"JLC builds HDI at {lo_l}-{hi_l} layers only "
            f"(first: {desc(blind[0])})", count=len(blind))

    laser_rng = hdi.get("laser_drill_mm") or [0.075, 0.15]
    mech_rng = hdi.get("buried_drill_mm") or [0.15, 0.55]
    laser_d = hdi.get("laser_dielectric_mm") or [0.05, 0.127]

    for v in vias:
        i, j = span_of(v, cu)
        through = (i, j) == (0, n - 1)
        outer_hits = (i == 0) + (j == n - 1)
        # -- type vs span
        if v.kind == "micro" and j - i != 1:
            vio("error", v, "via_type_span",
                f"{desc(v)} crosses {j - i} dielectrics: a laser via crosses "
                f"one; draw a stack of micro vias, one per layer pair",
                span=[cu[i], cu[j]])
        elif v.kind == "blind" and outer_hits != 1:
            vio("error", v, "via_type_span",
                f"{desc(v)} reaches {'both' if outer_hits == 2 else 'neither'} "
                f"outer layer: a blind via reaches exactly one "
                f"({'through' if outer_hits == 2 else 'buried'} is its type)",
                span=[cu[i], cu[j]])
        elif v.kind == "buried" and outer_hits:
            vio("error", v, "via_type_span",
                f"{desc(v)} reaches an outer layer: a buried via doesn't "
                f"(blind is its type)", span=[cu[i], cu[j]])
        if through:
            ar = hole_depth(stk, i, j) / v.drill if v.drill > 0 else math.inf
            if ar > through_ar + EPS:
                vio("error", v, "via_aspect",
                    f"{desc(v)}: {hole_depth(stk, i, j):.3f} mm deep through a "
                    f"{v.drill:g} mm hole is {ar:.1f}:1, past the "
                    f"{through_ar:g}:1 JLC plates", aspect=round(ar, 2))
            if is_hdi_entry:
                need = v.drill + float(hdi.get("through_pad_over_drill_mm", 0.10))
                if v.diameter + EPS < need:
                    vio("error", v, "via_ring",
                        f"{desc(v)}: an HDI build's through via needs a pad of "
                        f"hole + {hdi.get('through_pad_over_drill_mm', 0.10)} "
                        f"mm = {need:.3f} mm", need_mm=round(need, 4))
            continue
        # -- non-through: the lamination
        span = (cu[i], cu[j])
        laser = v.kind == "micro" or allowed.get((i, j)) == "micro"
        if entry is None:
            vio("error", v, "via_no_lamination",
                f"{desc(v)}: the board's stack "
                f"{'block' if not stk.assumed else '(none: no stackup block)'} "
                f"is no build in stackups.yaml, so nothing says a press "
                f"order makes this span. Set the board's stackup to an HDI "
                f"build (board_init --stackup JLCH...) or name it with "
                f"--stackup", span=list(span))
        elif not is_hdi_entry:
            alts = hdi_builds(stackups, n, span)
            vio("error", v, "via_needs_hdi",
                f"{desc(v)}: {name} is a standard lamination, pressed once "
                f"and drilled through only, so this span can't be made. "
                + (f"HDI builds in stackups.yaml with {span[0]}-{span[1]}: "
                   f"{', '.join(alts)}" if alts else
                   f"No {n}-layer HDI build in stackups.yaml makes "
                   f"{span[0]}-{span[1]}: `hwde stackup_fetch list --prefix "
                   f"JLCH{n:02d}` lists JLC's"),
                stackup=name, span=list(span), builds=alts)
        elif is_hdi_entry and (i, j) not in allowed:
            have = ", ".join(f"{cu[a]}-{cu[b]} ({k})"
                             for (a, b), k in sorted(allowed.items()))
            alts = [b for b in hdi_builds(stackups, n, span) if b != name]
            vio("error", v, "via_span_unbuilt",
                f"{desc(v)}: {name} is laminated for {have} only"
                + (f"; {', '.join(alts)} make(s) {span[0]}-{span[1]}"
                   if alts else ""),
                stackup=name, span=list(span), builds=alts)
        d_cross = depth(stk, i, j)
        if laser:
            if not (laser_rng[0] - EPS <= v.drill <= laser_rng[1] + EPS):
                vio("error", v, "via_drill",
                    f"{desc(v)}: a laser via's hole is {laser_rng[0]:g}-"
                    f"{laser_rng[1]:g} mm", range_mm=laser_rng)
            if v.drill > 0 and d_cross / v.drill > float(hdi.get("laser_max_aspect", 1.0)) + EPS:
                vio("error", v, "via_aspect",
                    f"{desc(v)}: it crosses {d_cross:.3f} mm of dielectric, "
                    f"deeper than its {v.drill:g} mm hole (laser vias plate "
                    f"to 1:1); open the hole to >= {d_cross:.3f} mm or use a "
                    f"thinner build-up ply", depth_mm=round(d_cross, 4))
            if j - i == 1 and not (laser_d[0] - EPS <= d_cross <= laser_d[1] + EPS):
                vio("error", v, "via_laser_depth",
                    f"{desc(v)}: the {d_cross:.3f} mm dielectric it crosses is "
                    f"outside the {laser_d[0]:g}-{laser_d[1]:g} mm a laser "
                    f"drills", depth_mm=round(d_cross, 4))
            ring = float(hdi.get("laser_pad_over_drill_mm", 0.15))
        else:
            if not (mech_rng[0] - EPS <= v.drill <= mech_rng[1] + EPS):
                vio("error", v, "via_drill",
                    f"{desc(v)}: a mechanical blind/buried via's hole is "
                    f"{mech_rng[0]:g}-{mech_rng[1]:g} mm", range_mm=mech_rng)
            hd = hole_depth(stk, i, j)
            mar = float(hdi.get("mech_max_aspect", 16.0))
            if v.drill > 0 and hd / v.drill > mar + EPS:
                vio("error", v, "via_aspect",
                    f"{desc(v)}: {hd:.3f} mm deep is {hd / v.drill:.1f}:1, "
                    f"past {mar:g}:1", aspect=round(hd / v.drill, 2))
            if v.kind == "buried" or outer_hits == 0:
                core = float(hdi.get("buried_min_core_mm", 0.30))
                if d_cross + EPS < core:
                    vio("error", v, "via_core",
                        f"{desc(v)}: it crosses {d_cross:.3f} mm of "
                        f"dielectric; a buried via is drilled in a core of "
                        f">= {core:g} mm", depth_mm=round(d_cross, 4))
            ring = float(hdi.get("buried_pad_over_drill_mm", 0.15))
        if v.diameter + EPS < v.drill + ring:
            vio("error", v, "via_ring",
                f"{desc(v)}: pad {v.diameter:g} mm, needs hole + {ring:g} = "
                f"{v.drill + ring:.3f} mm", need_mm=round(v.drill + ring, 4))

    # -- spans that no press order makes (mechanical only; laser vias are
    # drilled per build-up layer and stack)
    mech = sorted({span_of(v, cu) for v in blind
                   if v.kind != "micro" and allowed.get(span_of(v, cu)) != "micro"})
    for a in range(len(mech)):
        for b in range(a + 1, len(mech)):
            (i1, j1), (i2, j2) = mech[a], mech[b]
            overlap = min(j1, j2) - max(i1, i2)
            nested = (i1 <= i2 and j2 <= j1) or (i2 <= i1 and j1 <= j2)
            if overlap > 0 and not nested:
                v = next(x for x in blind if span_of(x, cu) == mech[b])
                vio("error", v, "via_spans_cross",
                    f"mechanical spans {cu[i1]}-{cu[j1]} and {cu[i2]}-{cu[j2]} "
                    f"overlap without one holding the other: each is drilled "
                    f"in its own pressed sub-stack, and these two can't both "
                    f"be", spans=[[cu[i1], cu[j1]], [cu[i2], cu[j2]]])

    # -- laser-via hole spacing
    same = float(hdi.get("hole_to_hole_same_net_mm", 0.13))
    diff = float(hdi.get("hole_to_hole_diff_net_mm", 0.24))
    lasers = [v for v in blind if v.kind == "micro"
              or allowed.get(span_of(v, cu)) == "micro"]
    reported = set()
    for v in lasers:
        si = span_of(v, cu)
        for w in vias:
            key = frozenset((id(v), id(w)))
            if w is v or key in reported:
                continue
            sj = span_of(w, cu)
            if min(si[1], sj[1]) < max(si[0], sj[0]):
                continue   # no layer in common
            c = math.dist(v.at, w.at)
            if c < 0.01 and v.net == w.net:
                continue   # a stack
            edge = c - v.drill / 2 - w.drill / 2
            need = same if (v.net and v.net == w.net) else diff
            if edge + EPS < need:
                reported.add(key)
                vio("error", v, "via_hole_spacing",
                    f"{desc(v)} is {edge:.3f} mm hole-to-hole from {desc(w)}; "
                    f"JLC needs {need:g} mm "
                    f"({'same' if need == same else 'different'} net)",
                    gap_mm=round(edge, 4), need_mm=need)

    # -- balance
    if not stk.assumed and n > 2:
        half = len(stk.dielectrics)
        for k in range(half // 2):
            a, b = stk.dielectrics[k]["height"], stk.dielectrics[half - 1 - k]["height"]
            if max(a, b) > 0 and abs(a - b) / max(a, b) > BALANCE_TOL:
                vio("warning", None, "stackup_unbalanced",
                    f"dielectric {k + 1} ({a:.3f} mm) and its mirror "
                    f"{half - k} ({b:.3f} mm) differ by more than "
                    f"{BALANCE_TOL:.0%}: an unbalanced stack bows in reflow",
                    gaps=[k + 1, half - k])
        for k in range(n // 2):
            ta = stk.copper_thickness.get(cu[k], 0.0)
            tb = stk.copper_thickness.get(cu[n - 1 - k], 0.0)
            if max(ta, tb) > 0 and abs(ta - tb) / max(ta, tb) > BALANCE_TOL:
                vio("warning", None, "stackup_unbalanced",
                    f"{cu[k]} ({ta:.4f} mm) and its mirror {cu[n - 1 - k]} "
                    f"({tb:.4f} mm) carry different copper: an unbalanced "
                    f"stack bows in reflow", layers=[cu[k], cu[n - 1 - k]])
    if entry is not None and entry.get("hdi_order") is not None:
        facts["hdi_order"] = entry["hdi_order"]
    return viols, facts


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--stackup", help="stackups.yaml entry (default: the one "
                    "the board's stack block matches)")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise checklib.CheckError(f"board not found: {pcb}")
    viols, facts = check(pcb, args.stackup)
    return checklib.report(SCRIPT, pcb, viols, checked=[facts]), args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
