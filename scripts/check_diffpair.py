"""check_diffpair.py - differential-pair skew / coupling / symmetry (SPEC 6.3).

Per pair (from constraints.json["diff_pairs"], else auto-discovered by name):
 - intra-pair length SKEW (mm and ps via stackup epsilon_r), measured on each
   net's TRUNK - the branch-free path between the pair's two matched terminals
   (a USB D+ pull-up stub, a series-R tap, etc. must NOT inflate the length);
 - GAP consistency: nearest-partner distance sampled along the coupled run
   (min / median / p90 / max), warned if it wanders beyond tolerance;
 - UNCOUPLED length: run of either trace whose partner has walked away farther
   than coupling_gap_max - the fingerprint of a one-sided meander/detour;
 - VIA symmetry: the two nets should transition layers together;
 - IMPEDANCE as drawn: the pair's dominant width and its median edge gap on
   its main layer, solved by lib/impedance.py's 2D field solver against the
   board's own stackup (coated microstrip outside, stripline inside). Always
   reported as zdiff_ohm; when the spec carries impedance_ohm, a miss beyond
   impedance_tol_pct (default 10, JLC's standard tolerance) warns and beyond
   twice that is an error.

Why not raw length? Adding a meander to the SHORTER trace (the corpus
diffpair-skew mutant does exactly this) barely moves the length skew but is a
real signal-integrity defect - it is caught by uncoupled length, not by skew.
Both are reported; the pair fails on whichever exceeds its threshold.

The trunk is found on netconn's per-net connectivity graph (segments joined
through vias, pads and overlapping same-layer end caps - LEARNINGS 2026-07-29:
an endpoint-snap-only graph turns sub-0.3 mm endpoint mismatches into a
total-copper-length "skew"). The shortest path between two matched terminal
PAD nodes ignores dead-end stubs; if the terminals cannot be connected at all
the length falls back to total copper and a diffpair_open_trunk warning is
emitted so the bogus skew is never silent.

CLI: --pcb board.kicad_pcb [--constraints constraints.json] [--out report.json]
     exit 0/1/2 per SPEC section 6.

constraints.json["diff_pairs"] entries (all keys but the nets optional):
    {"p": "/USB_DP", "n": "/USB_DM",   # exact board net names
     "gap_mm": 0.65,                   # nominal centre-to-centre pitch
     "max_skew_mm": 5.0,               # length-match tolerance
     "max_uncoupled_mm": 5.0,          # allowed one-sided uncoupled run
     "coupling_factor": 3.0,           # coupled if gap <= factor * nominal
     "term_pair_mm": 2.5,              # cross-ref terminal pairing window
     "impedance_ohm": 90,              # differential target
     "impedance_tol_pct": 10,          # allowed miss, percent
     "kind": "sense"}                  # DC Kelvin / low-frequency sense pair
A "kind" of sense (also kelvin, dc_sense, low_frequency) marks a shunt-sense
pair: no skew, uncoupled-length or impedance limits (high-speed rules mean
nothing at DC); open trunk and via asymmetry still apply, and an optional
"max_sense_uncoupled_mm" keeps a "routed together" limit. Auto-discovered
pairs get kind sense only on a narrow name rule (is_sense_name).
"""
from __future__ import annotations

import argparse
import math
import os
import re
import statistics
import sys
from pathlib import Path

from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import geom  # noqa: E402
import impedance  # noqa: E402
import netconn  # noqa: E402
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "check_diffpair"
C_MM_PER_PS = 0.299792458      # speed of light, mm/ps
MAX_SKEW_MM = 5.0              # default intra-pair length-match tolerance
MAX_UNCOUPLED_MM = 5.0         # default one-sided uncoupled run
COUPLING_FACTOR = 3.0          # coupled where gap <= factor * nominal pitch
TERM_PAIR_MM = 2.5             # cross-ref matched-terminal window (same-ref: none)
GAP_TOL_MM = 0.5              # coupled-region gap may wander this much (warn)
IMPEDANCE_TOL_PCT = 10.0       # JLC's standard controlled-impedance tolerance
SAMPLE_MM = 0.1               # centerline sampling step for gap / uncoupled

# Suffix pairs used to auto-discover pairs: two nets sharing a stem whose final
# token is (positive, negative). Deliberately conservative - the ambiguous
# single-letter H/L (hi/lo bus bytes) and P/M pairs are excluded so a bus or an
# ordinary net pair is not mistaken for a differential pair (which would then
# false-positive on uncoupled length). USB D+/D- is matched via the DP/DM token,
# not a bare P/M. Explicit constraints.json["diff_pairs"] always wins.
SUFFIX_PAIRS = [("_P", "_N"), ("DP", "DM"), ("D+", "D-"), ("+", "-"), ("P", "N")]
POSITIVE = {"_P", "DP", "D+", "+", "P"}       # which token is the + net


SENSE_KINDS = {"sense", "kelvin", "dc_sense", "low_frequency"}
# Narrow name default for AUTO-discovered pairs only (an explicit diff_pairs
# entry classifies itself via "kind"): the last path segment of the stem is a
# current-sense name. Deliberately not "SNS"/"SENSE" alone (VSNS, feedback
# sense nets are not shunt pairs).
SENSE_STEM = re.compile(r"(?:^|[/_])(ISNS|ISENSE|ISEN|CSA|SHUNT|KELVIN)\d*_?$")


def is_sense_name(p: str, n: str) -> bool:
    """True when the auto-discovered pair p/n names a shunt-sense pair."""
    stem = os.path.commonprefix([p.upper(), n.upper()])
    return bool(SENSE_STEM.search(stem))


def is_sense(spec: dict) -> bool:
    return str(spec.get("kind", "")).lower() in SENSE_KINDS


# ------------------------------------------------------------ pairing

def discover_pairs(nets) -> list[tuple[str, str]]:
    """Auto-pair nets by name: equal stem, complementary final token.
    Returns (positive, negative) ordered tuples."""
    names = sorted(n for n in nets if n)
    seen: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for name in names:
        if name in seen:
            continue
        up = name.upper()
        for a, b in SUFFIX_PAIRS:
            matched = None
            for lo, hi in ((a, b), (b, a)):
                if up.endswith(lo):
                    stem = up[: len(up) - len(lo)]
                    cand = [m for m in names if m != name
                            and m.upper() == stem + hi and m not in seen]
                    if cand:
                        pos = name if lo in POSITIVE else cand[0]
                        neg = cand[0] if pos == name else name
                        matched = (pos, neg, cand[0])
                        break
            if matched:
                pairs.append((matched[0], matched[1]))
                seen.update((name, matched[2]))
                break
    return pairs


# ------------------------------------------------------------ trunk

def _pad_dist(a, b) -> float:
    return math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1])


def matched_terminals(bg, p, n, term_pair_mm=TERM_PAIR_MM):
    """The (p_pad, n_pad) terminal pairs bounding the coupled trunk.

    SAME-REF pairing first, with NO distance cap: a footprint that carries
    both halves matches its own pads however far apart they sit (LEARNINGS
    2026-07-29: a magjack's RXP/RXN pads are 4.58 mm apart, so any window
    drops the connector terminal and the escape is never measured). Only a
    p-pad with no same-ref n-pad uses the `term_pair_mm` window (spec key
    "term_pair_mm", default 2.5 mm). Unmatched pads (series-R tap, pull-up)
    are branch ends and excluded."""
    p_pads, n_pads = bg.pads_of(net=p), bg.pads_of(net=n)
    matches = []
    for pp in p_pads:
        same = [nn for nn in n_pads if nn.ref == pp.ref]
        if same:
            matches.append((pp, min(same, key=lambda nn: _pad_dist(pp, nn))))
            continue
        best, bd = None, term_pair_mm
        for nn in n_pads:
            d = _pad_dist(pp, nn)
            if d <= bd:
                best, bd = nn, d
        if best is not None:
            matches.append((pp, best))
    return matches


def trunk_length(bg, net, terminals):
    """(length_mm, branch_free, unreachable): path length of `net` between its
    two farthest matched-terminal PAD nodes on the netconn connectivity graph.
    Falls back to total track length (branch_free False) when the graph cannot
    connect any terminal pair; `unreachable` then names the terminal pads
    ("REF.pin") so the caller can report the open trunk."""
    g = netconn.build(bg, net)
    total = sum(t.length for t in bg.tracks_of(net))
    if len(terminals) < 2:
        return total, False, sorted(f"{t.ref}.{t.number}" for t in terminals)
    named = [(f"{t.ref}.{t.number}", netconn.pad_node(g, t)) for t in terminals]
    best = None
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            L = netconn.shortest_path_len(g, named[i][1], named[j][1])
            if L is not None and (best is None or L > best):
                best = L
    if best is None:
        return total, False, sorted(name for name, _ in named)
    return best, True, []


# ------------------------------------------------------------ coupling

def net_geometry(bg, net):
    return unary_union([t.shape for t in bg.tracks_of(net)])


def sample_gaps(line_geom, other_geom, step=SAMPLE_MM):
    """(arc_len, gap) samples of one net's centerline vs the other net."""
    out = []
    if line_geom.is_empty or other_geom.is_empty:
        return out  # distance to an empty geometry is NaN; caller handles
    for line in getattr(line_geom, "geoms", [line_geom]):
        L = line.length
        if L <= 0:
            continue
        nseg = max(1, int(round(L / step)))
        for i in range(nseg):
            seglen = L / nseg
            mid = line.interpolate((i + 0.5) * seglen)
            out.append((seglen, other_geom.distance(mid)))
    return out


def coupling_stats(bg, p, n, coupling_max):
    gp, gn = net_geometry(bg, p), net_geometry(bg, n)
    sp = sample_gaps(gp, gn)
    sn = sample_gaps(gn, gp)
    unc_p = sum(sl for sl, g in sp if g > coupling_max)
    unc_n = sum(sl for sl, g in sn if g > coupling_max)
    coupled = [g for sl, g in sp + sn if g <= coupling_max]
    allgaps = sorted(g for _, g in sp + sn)
    nominal = statistics.median([g for _, g in sp + sn]) if allgaps else 0.0
    stats = {
        "nominal_gap_mm": checklib.rnd(nominal),
        "gap_min_mm": checklib.rnd(allgaps[0]) if allgaps else None,
        "gap_median_mm": checklib.rnd(statistics.median(allgaps)) if allgaps else None,
        "gap_p90_mm": checklib.rnd(allgaps[int(0.9 * (len(allgaps) - 1))]) if allgaps else None,
        "coupled_gap_max_mm": checklib.rnd(max(coupled)) if coupled else None,
    }
    return unc_p, unc_n, stats


# ------------------------------------------------------------ per-pair check

def check_pair(bg: geom.BoardGeom, spec: dict):
    p, n = spec.get("p"), spec.get("n")
    if p not in bg.nets or n not in bg.nets:
        raise CheckError(f"diff pair {p!r}/{n!r} not both on board")
    max_skew = float(spec.get("max_skew_mm", MAX_SKEW_MM))
    max_unc = float(spec.get("max_uncoupled_mm", MAX_UNCOUPLED_MM))
    factor = float(spec.get("coupling_factor", COUPLING_FACTOR))

    # A pair with an unrouted half cannot be coupling-checked (an empty geometry
    # makes distance() NaN); report it as not-routed and judge nothing yet.
    tracks_p, tracks_n = bg.tracks_of(p), bg.tracks_of(n)
    if not tracks_p or not tracks_n:
        return [], {"pair": [p, n], "routed": False,
                    "length_p_mm": checklib.rnd(sum(t.length for t in tracks_p)),
                    "length_n_mm": checklib.rnd(sum(t.length for t in tracks_n)),
                    "note": "pair not fully routed; coupling not evaluated"}

    term = matched_terminals(bg, p, n, float(spec.get("term_pair_mm",
                                                      TERM_PAIR_MM)))
    lp, okp, unreach_p = trunk_length(bg, p, [pp for pp, nn in term])
    ln, okn, unreach_n = trunk_length(bg, n, [nn for pp, nn in term])
    skew = abs(lp - ln)

    # nominal pitch drives the coupling threshold
    nom = spec.get("gap_mm")
    if nom is None:
        _, _, s0 = coupling_stats(bg, p, n, 1e9)
        nom = s0["nominal_gap_mm"] or 0.2
    coupling_max = max(factor * nom, nom + 0.5)
    unc_p, unc_n, stats = coupling_stats(bg, p, n, coupling_max)
    uncoupled = max(unc_p, unc_n)

    eps = _pair_epsilon(bg, p, n)
    skew_ps = skew * math.sqrt(eps) / C_MM_PER_PS

    vp, vn = len(bg.vias_of(p)), len(bg.vias_of(n))
    rep_pt = _rep_point(bg, p)

    violations: list[dict] = []
    common = dict(kind=None, pair=[p, n], length_p_mm=checklib.rnd(lp),
                  length_n_mm=checklib.rnd(ln), branch_free=bool(okp and okn))

    # An open trunk means the length above is TOTAL copper, not a trunk - the
    # skew number is meaningless and must never pass silently (LEARNINGS
    # 2026-07-29: reported only as branch_free:false, never as a violation).
    for bad_net, ok, length, unreach in ((p, okp, lp, unreach_p),
                                         (n, okn, ln, unreach_n)):
        if ok:
            continue
        who = ", ".join(unreach) if unreach else "fewer than 2 matched pads"
        violations.append(violation(
            SCRIPT, "warning", _rep_point(bg, bad_net), None, bad_net, [],
            f"diff pair {p}/{n}: {bad_net} trunk is open - cannot connect "
            f"terminal(s) {who}; its length {length:.2f} mm is total copper "
            f"length, so the reported skew is not a trunk skew",
            SCRIPT, **{**common, "kind": "diffpair_open_trunk",
                       "fallback_length_mm": checklib.rnd(length)}))

    sense = is_sense(spec)
    if skew > max_skew and not sense:
        violations.append(violation(
            SCRIPT, "error" if skew > 2 * max_skew else "warning", rep_pt, None,
            p, [], f"diff pair {p}/{n} length skew {skew:.2f} mm "
            f"(~{skew_ps:.0f} ps, eps_r {eps:.1f}); limit {max_skew:.1f} mm",
            SCRIPT, **{**common, "kind": "diffpair_skew",
                       "skew_mm": checklib.rnd(skew),
                       "skew_ps": checklib.rnd(skew_ps), "limit_mm": max_skew}))
    if sense:
        max_unc = float(spec.get("max_sense_uncoupled_mm", math.inf))
    if uncoupled > max_unc:
        who, uval = (p, unc_p) if unc_p >= unc_n else (n, unc_n)
        violations.append(violation(
            SCRIPT, "error", rep_pt, None, who, [],
            f"diff pair {p}/{n} has {uval:.2f} mm of {who} running uncoupled "
            f"(> {coupling_max:.2f} mm from its partner); limit {max_unc:.1f} mm",
            SCRIPT, **{**common, "kind": "diffpair_uncoupled",
                       "uncoupled_mm": checklib.rnd(uval),
                       "uncoupled_p_mm": checklib.rnd(unc_p),
                       "uncoupled_n_mm": checklib.rnd(unc_n),
                       "coupling_max_mm": checklib.rnd(coupling_max),
                       "limit_mm": max_unc}))
    # Gap consistency is REPORTED (the spec's "gap deviation histogram") but is
    # not a gate: a legitimate pair fans out to > nominal gap at its pad
    # breakouts, which must not false-positive. The one-sided detour that a bad
    # gap would signal is already caught, with a clean pad-breakout margin, by
    # the uncoupled-length term above.
    if abs(vp - vn) > int(spec.get("max_via_asym", 0)):
        violations.append(violation(
            SCRIPT, "warning", rep_pt, None, p, [],
            f"diff pair {p}/{n} via count asymmetric: {p} has {vp}, {n} has {vn}",
            SCRIPT, **{**common, "kind": "diffpair_via_asymmetry",
                       "vias_p": vp, "vias_n": vn}))

    zinfo = pair_impedance(bg, p, n, stats["gap_median_mm"])
    target = None if sense else spec.get("impedance_ohm")
    if target and zinfo.get("zdiff_ohm"):
        tol = float(spec.get("impedance_tol_pct", IMPEDANCE_TOL_PCT))
        miss = (zinfo["zdiff_ohm"] - float(target)) / float(target) * 100
        if abs(miss) > tol:
            violations.append(violation(
                SCRIPT, "error" if abs(miss) > 2 * tol else "warning", rep_pt, None,
                p, [], f"diff pair {p}/{n} is {zinfo['zdiff_ohm']:.1f} ohm as drawn "
                f"(width {zinfo['width_mm']} mm, gap {zinfo['gap_mm']} mm on "
                f"{zinfo['signal_layer']}); target {float(target):g} ohm +/-{tol:g}%",
                SCRIPT, **{**common, "kind": "diffpair_impedance",
                           "target_ohm": float(target), "miss_pct": checklib.rnd(miss),
                           **zinfo}))

    facts = {"pair": [p, n], "kind": "sense" if sense else "diff", "length_p_mm": checklib.rnd(lp),
             "length_n_mm": checklib.rnd(ln), "skew_mm": checklib.rnd(skew),
             "skew_ps": checklib.rnd(skew_ps), "branch_free": bool(okp and okn),
             "uncoupled_p_mm": checklib.rnd(unc_p),
             "uncoupled_n_mm": checklib.rnd(unc_n),
             "coupling_max_mm": checklib.rnd(coupling_max),
             "vias_p": vp, "vias_n": vn, **stats, "impedance": zinfo}
    return violations, facts


def pair_impedance(bg, p, n, gap_c2c) -> dict:
    """Zdiff of the pair as drawn on its main layer (the layer carrying most of
    its length): the length-dominant track width and the median centre-to-
    centre gap minus that width. An inner layer is solved as stripline with
    the trace sitting on the THICKER neighbouring dielectric (the core, on
    every JLC stackup) and embedded in the thinner (prepreg)."""
    tracks = bg.tracks_of(p) + bg.tracks_of(n)
    by_layer: dict[str, float] = {}
    for t in tracks:
        by_layer[t.layer] = by_layer.get(t.layer, 0.0) + t.length
    sig = max(by_layer, key=by_layer.get)
    by_w: dict[float, float] = {}
    for t in tracks:
        if t.layer == sig:
            by_w[round(t.width, 4)] = by_w.get(round(t.width, 4), 0.0) + t.length
    w = max(by_w, key=by_w.get)
    out = {"signal_layer": sig, "width_mm": w}
    if gap_c2c is None or gap_c2c - w <= 0.01:
        return {**out, "note": "no measurable edge gap; impedance not solved"}
    s = round(gap_c2c - w, 4)
    out["gap_mm"] = s
    if len(bg.copper_layers) < 2 or not bg.stackup.dielectrics:
        return {**out, "note": "no reference plane in the stackup; impedance not solved"}
    above, below = bg.stackup.adjacent(sig)
    t = bg.stackup.copper_thickness.get(sig, 0.035)
    try:
        if above is None or below is None:
            ref = below or above
            z = impedance.field_microstrip(
                w, bg.stackup.height_between(sig, ref), t,
                bg.stackup.epsilon_between(sig, ref), s)["zdiff"]
            model = "coated microstrip"
        else:
            sides = sorted(((bg.stackup.height_between(sig, r),
                             bg.stackup.epsilon_between(sig, r)) for r in (above, below)),
                           reverse=True)
            (hb, eb), (hf, ef) = sides
            z = impedance.field_stripline(w, hb, hf, t, eb, ef, s)["zdiff"]
            model = "stripline"
    except (ValueError, RuntimeError, ZeroDivisionError) as e:
        return {**out, "note": f"impedance not solved: {e}"}
    return {**out, "zdiff_ohm": checklib.rnd(z), "model": model,
            "stackup_assumed": bool(bg.stackup.assumed)}


DEFAULT_ER = 4.5              # FR4 fallback when no reference dielectric exists


def _pair_layers(bg, p, n):
    """Two stackup layers to read epsilon between: the pair's signal layer and
    its nearest reference. Falls back to the outer gap."""
    layers = {t.layer for t in bg.tracks_of(p)} | {t.layer for t in bg.tracks_of(n)}
    sig = next((l for l in bg.copper_layers if l in layers), bg.copper_layers[0])
    above, below = bg.stackup.adjacent(sig)
    ref = below or above or (bg.copper_layers[1] if len(bg.copper_layers) > 1
                             else sig)
    return sig, ref


def _pair_epsilon(bg, p, n) -> float:
    """Effective epsilon_r for the ps conversion; FR4 default on a single-copper
    board (no dielectric gap -> epsilon_between would divide by zero)."""
    if len(bg.copper_layers) < 2 or not bg.stackup.dielectrics:
        return DEFAULT_ER
    try:
        eps = bg.stackup.epsilon_between(*_pair_layers(bg, p, n))
    except (ZeroDivisionError, ValueError, IndexError):
        return DEFAULT_ER
    return eps if eps and eps > 0 else DEFAULT_ER


def _rep_point(bg, net):
    tks = bg.tracks_of(net)
    if not tks:
        return None
    c = tks[0].shape.interpolate(0.5, normalized=True).coords[0]
    return (c[0], c[1])


# ------------------------------------------------------------ CLI

def resolve_pairs(bg, cons) -> list[dict]:
    # an EXPLICIT diff_pairs key wins even when empty ([] means "no pairs");
    # only a missing key falls back to name-based auto-discovery.
    if cons and "diff_pairs" in cons:
        return [s for s in (cons["diff_pairs"] or []) if s.get("p") and s.get("n")]
    return [{"p": p, "n": n, **({"kind": "sense"} if is_sense_name(p, n) else {})}
            for p, n in discover_pairs(bg.nets)]


def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Differential-pair skew / coupling / symmetry check.")
    ap.add_argument("--pcb", required=True, help="path to .kicad_pcb")
    ap.add_argument("--constraints", help="constraints.json (diff_pairs list); "
                    "pairs are auto-discovered by name if omitted")
    ap.add_argument("--out", help="write JSON report here instead of stdout")
    args = ap.parse_args(argv)

    cons = checklib.load_json(args.constraints, "constraints") \
        if args.constraints else {}
    bg = geom.load_board(Path(args.pcb))
    bg.assert_fresh()

    violations: list[dict] = []
    checked: list[dict] = []
    for spec in resolve_pairs(bg, cons):
        p, n = spec.get("p"), spec.get("n")
        # A stale named pair (renamed/absent net) is a warning, not a whole-check
        # abort: other pairs still get judged.
        if p not in bg.nets or n not in bg.nets:
            missing = [x for x in (p, n) if x not in bg.nets]
            violations.append(violation(
                SCRIPT, "warning", None, None, p, [],
                f"diff pair {p}/{n} names net(s) not on the board: {missing} "
                f"(stale constraints?)", SCRIPT, kind="diffpair_missing_net",
                pair=[p, n], missing=missing))
            continue
        vs, facts = check_pair(bg, spec)
        violations.extend(vs)
        checked.append(facts)

    payload = checklib.report(SCRIPT, args.pcb, violations, checked=checked)
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
