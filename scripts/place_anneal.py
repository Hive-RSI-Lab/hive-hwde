"""place_anneal - simulated-annealing placement refinement (S10, SPEC P6 stage 2).

SA over rigid CLUSTER positions/rotations (clusters = placelib.build_clusters
+ the board's OWN satellite geometry - U20: a valid existing slot is
inherited, never re-derived from place_seed, so a hand-placed decoupler
survives stage 2; re-slotting happens only for an invalid slot and is
reported in `satellites_reslotted`), starting from the board's current
placement (normally place_seed --apply output). The board file is never
touched unless --apply-best; results are emitted as ABSOLUTE op lists for
place_edit.py. A candidate violating a DECLARED decoupling max_dist_mm is
rejected (marked illegal), never merely scored down.

Cost (raw term totals maintained incrementally, recombined with weights at
accept time):
  w_hpwl    * sum_net class_weight(net) * hpwl(net)     [gnd 0.25/pwr 0.6/sig 1]
  w_overlap * mm^2 of courtyard overlap + keepout overlap + outside-outline
              (a free body's outline is pulled in by Footprint.edge_keep, so
              its pad copper clears the board's copper-to-edge rule)
              (ramped by sqrt(T0/T) so late epochs are effectively legal-only)
  w_cong    * congestion overflow (MST flight-line demand above --cong-cap
              per 2 mm cell). Each flight line demands its net's track
              pitch in Default-class pitches (net_pitch_units: width +
              clearance from the .kicad_pro netclass patterns and the
              .kicad_dru per-net floors), so a 1.75 mm VBUS asks for its
              real channel. Ground and power nets are charged like any
              other; only a net that owns an inner-layer zone (a real
              plane) is left out, because it leaves by via, not channel.
              (The benchmark: a demand blind to GND and to width packed
              stm32-blinky/usb-buck/pd-trigger 25-31% below the designer
              and left exactly the widest power net unrouted.)
  w_cross   * weighted MST flight-line crossings (pair weight = w(a)*w(b))
  w_rule    * rule terms: high-current path length (current_a * hpwl of each
              constraints.power net), separation groups
              (placement.separation), thermal spreading (constraints.thermal),
              corridor keep-clear (placement.corridors: bodies other than the
              two endpoints pay CORRIDOR_W per mm^2 inside the swath - T6
              P6A-5 cost-only version; seed/legality integration deferred)
  w_assembly* assembly cost of the BACK side (U19): ASM_SECOND_SIDE_MM once
              any part sits there + ASM_PER_PART_MM each, in weighted-HPWL mm

Both sides (U19): _propose() can move a cluster to the OTHER side, so the
annealer can DISCOVER that the back tightens a loop or unjams a packed board
instead of only respecting a side it was handed. A flip mirrors the cluster in
its own frame (placelib.Footprint.mirror is the in-memory pcbnew Flip), so
satellites follow their anchor by construction. Guards: only free clusters
flip - never a declared-edge connector, never a through-hole cluster (back-side
THT is a hand/wave operation this cost model cannot price), never a ref pinned
by constraints placement.sides [{"ref": R, "side": "front"|"back"}]. The
assembly term is the brake that keeps a board which does not NEED two sides
single-sided; --no-side-flips turns the move off entirely.

--margin-mm buffers every body/obstacle poly by margin/2 for the SA overlap
term and repair targets ONLY (true courtyards stay the legality oracle), so
packed-but-legal is accepted but cost-discouraged - the courtyard-margin
answer to silk-blind packing (ladder row 53). Default 0.0 = exact prior
behavior.

Adaptive schedule: T0 from sampled uphill deltas; per-epoch cooling and move
window scaled by the epoch acceptance ratio (TimberWolf-style). Deterministic
per --seed: random.Random(seed) is the only entropy source; wall-clock is
reported but never steers the search.

Top-N: distinct best states are kept during the search; each candidate is
applied to the in-memory model, greedily repaired if it left minor illegality,
legality-checked (placelib), and emitted as cand<k>.ops.json + metrics.

Routability feedback (SPEC P6.2): --route-feedback needs the S11 DSN/
Freerouting path and exits 2 until S11 lands. The blending logic is BUILT and
testable now: anneal(..., route_probe=fn) probes the current best state every
--feedback-every epochs; completion c in [0,1] boosts congestion/crossing
weights by (1 + fb_gain*(1-c)) and candidate scores rank by
cost * (1 + w_feedback*(1-c)). S11 wires the real probe.

Exit 0 best candidate legal / 1 no legal candidate (best one's violations
reported) / 2 error.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from shapely import affinity
from shapely.prepared import prep

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))

import checklib  # noqa: E402
import placelib  # noqa: E402
import place_seed  # noqa: E402
from checklib import CheckError  # noqa: E402
from placelib import PlaceModel  # noqa: E402

EPS = placelib.EPS_AREA

DEFAULT_WEIGHTS = {
    "hpwl": 1.0,
    "overlap": 30.0,     # per mm^2 (before the T-ramp)
    "cong": 2.0,         # per overflow unit
    "cross": 2.0,        # per weighted crossing
    "rule": 1.0,
    "assembly": 1.0,     # scales the back-side terms below
    "feedback": 0.5,     # candidate-score blend per (1 - completion)
}
WEIGHT_NAMES = ("hpwl", "overlap", "cong", "cross", "rule", "assembly",
                "feedback")

# U19 assembly cost of the board's SECOND side, in weighted-HPWL mm so the
# tradeoff reads directly: "this flip must save >= N mm of wirelength".
# OWNER RULING 2026-08-16: 40 mm to open the back at all + 4 mm per part on
# it. On a blinky2-class board (242 mm total HPWL) that is ~17% of all
# wirelength, so a roomy board never flips for wirelength alone; a packed one
# still flips at once, because the overlap it relieves costs 30-240 per mm^2.
ASM_SECOND_SIDE_MM = 40.0
ASM_PER_PART_MM = 4.0

# share of proposals that try a side flip (only when something is flippable)
FLIP_P = 0.08

# hpwl class weights (S9 spring precedent: planes carry gnd; power matters
# less than signal for wirelength but more than gnd)
W_GND, W_PWR, W_SIG = 0.25, 0.6, 1.0
# crossing weights: gnd excluded entirely (a pour crosses anything);
# congestion demand uses the net's pitch instead (net_pitch_units)
MST_PWR, MST_SIG = 0.5, 1.0


def net_pitch_units(pcb: Path, nets) -> dict[str, float]:
    """{net: (track_width + clearance) / Default-class pitch} for every net
    whose pitch is wider than Default's. Width and clearance are each the
    max of the net's .kicad_pro netclass (by netclass_patterns) and its
    .kicad_dru per-net floor. A missing or unreadable project gives {}, so
    every net demands 1.0 - the behaviour before widths were charged."""
    import fnmatch  # noqa: PLC0415
    import route_critical as rc  # noqa: PLC0415 - lazy, heavy imports
    pro = Path(pcb).with_suffix(".kicad_pro")
    try:
        ns = json.loads(pro.read_text(encoding="utf-8")).get(
            "net_settings") or {}
    except (OSError, ValueError):
        return {}
    classes = {c.get("name"): c for c in ns.get("classes") or []
               if isinstance(c, dict)}
    dflt = classes.get("Default") or {}
    try:
        base_w = float(dflt.get("track_width") or 0.2)
        base_c = float(dflt.get("clearance") or 0.2)
    except (TypeError, ValueError):
        return {}
    width = {n: base_w for n in nets}
    clear = {n: base_c for n in nets}
    for pat in ns.get("netclass_patterns") or []:
        cls = classes.get(pat.get("netclass")) or {}
        pattern = pat.get("pattern")
        if not pattern:
            continue
        for n in nets:
            if fnmatch.fnmatchcase(n, pattern):
                for key, tgt in (("track_width", width),
                                 ("clearance", clear)):
                    v = cls.get(key)
                    if isinstance(v, (int, float)):
                        tgt[n] = max(tgt[n], float(v))
    dru = Path(pcb).with_suffix(".kicad_dru")
    for key, tgt in (("track_width", width), ("clearance", clear)):
        for n, v in rc.dru_net_floors(dru, key).items():
            if n in tgt:
                tgt[n] = max(tgt[n], v)
    base = base_w + base_c
    out = {n: (width[n] + clear[n]) / base for n in nets}
    return {n: round(u, 4) for n, u in sorted(out.items()) if u > 1.0}

FB_GAIN = 2.0            # cong/cross boost per (1 - completion)

# rule-term scale per mm^2 of body intrusion into a declared corridor -
# same magnitude as the overlap weight, so a body parked in a 5 A channel
# costs like a courtyard overlap (pd-trigger: the annealer's corridor
# blindness forced a full hand floorplan at +40% HPWL)
CORRIDOR_W = 30.0

# T6 P6A-1: the net-class partition moved to placelib (one source for the
# annealer objective and the bench/metrics signal terms)
_class_sets = placelib.class_sets


# ---------------------------------------------------------------- bodies

@dataclass
class Variant:
    """One cluster's geometry ON ONE SIDE, in the cluster frame (origin =
    anchor courtyard center, angle 0 = anchor local frame)."""
    rel_poly: object              # shapely Polygon about the cluster origin
    pads: list                    # [(net, qx, qy), ...] cluster-frame coords
    slots: dict                   # ref -> ((sx, sy) anchor-local, rel_deg)


@dataclass
class Body:
    """A rigid cluster: anchor + slotted satellites, with a variant per side.

    The back variant is the front one MIRRORED in the cluster frame (y -> -y,
    rel angles negated) - exactly what flipping every member does, so the
    satellites ride their anchor across a flip the same way they ride a move.
    The CURRENT side is engine state (Engine.sides), not a body field; side0
    is what the input board had."""
    cid: int
    cluster: placelib.Cluster
    variants: dict[str, Variant]
    side0: str
    thru: bool
    kind: str                     # "free" | "edge" | "edge_fixed"
    flippable: bool = False
    pin_side: str | None = None   # constraints placement.sides ruling

    @property
    def refs(self):
        return self.cluster.refs

    # input-side views (the geometry every non-flip caller means)
    @property
    def slots(self):
        return self.variants[self.side0].slots

    @property
    def rel_poly(self):
        return self.variants[self.side0].rel_poly

    @property
    def pads(self):
        return self.variants[self.side0].pads


def _other(side: str) -> str:
    return "back" if side == "front" else "front"


def side_pins(placement: dict | None) -> dict[str, str]:
    """constraints placement.sides -> {ref: "front"|"back"} (U19).

    Declares the side a part MUST be assembled on: a connector that mates from
    the top, a part under a heatsink, anything the enclosure fixes. First
    ruling per ref wins; malformed entries are ignored here and reported by
    constraints_lint."""
    out: dict[str, str] = {}
    for e in (placement or {}).get("sides", []):
        ref, side = (e or {}).get("ref"), (e or {}).get("side")
        if isinstance(ref, str) and side in ("front", "back"):
            out.setdefault(ref, side)
    return out


def _mirror_variant(v: Variant) -> Variant:
    """The same cluster flipped: mirror the cluster frame the same way
    placelib.Footprint.mirror mirrors a part (in y - see the note there).

    Proof it is the whole story: with the anchor mirrored by a reflection M,
    the point at local (sx, sy) sits at M.(sx, sy) and a satellite's relative
    rotation negates (M.R(-rel) == R(rel).M), so anchor.to_abs(slot') lands
    the satellite at the mirrored cluster-frame offset and its own mirrored
    local frame lands its pads there too. Nothing needs re-slotting."""
    return Variant(
        affinity.scale(v.rel_poly, xfact=1.0, yfact=-1.0, origin=(0, 0)),
        [(net, qx, -qy) for net, qx, qy in v.pads],
        {ref: ((slot[0], -slot[1]), (-rel) % 360.0)
         for ref, (slot, rel) in v.slots.items()})


def _inherit_slots(model: PlaceModel, cluster, decoupling, warnings,
                   reslotted: list[str]) -> dict[str, tuple]:
    """The board's EXISTING satellite geometry as slots (U20, ladder row 313).

    The annealer refines the placement it was handed: a satellite whose
    current position is valid stays EXACTLY where it is relative to its
    anchor - the SA moves the cluster, never re-slots the member. A slot is
    re-derived (loudly: warning + `satellites_reslotted` fact) only when the
    existing one is invalid: satellite on the other side from its anchor,
    colliding inside its own cluster (PRECISE shapes - the U5 standard, so
    tight-but-legal hand placements survive), or violating a DECLARED
    decoupling max_dist_mm (class defaults are gate heuristics, not
    re-slotting grounds). Pre-U20 every slot came from place_seed, so a
    hand-placed decoupler could not survive stage 2 (bb-adc: seed slots put
    C3 at 3.97 mm against its declared 2.5 mm and the report opened with a
    58 mm hpwl_input/hpwl_start gap before a single move ran)."""
    if not cluster.satellites:
        return {}
    from geom import _rot
    anchor = model.footprints[cluster.anchor]
    declared = [a for a in (decoupling or {}).get("associations", [])
                if a.get("max_dist_mm") is not None
                and {a.get("cap"), a.get("ic")} <= set(cluster.refs)]
    kept_shapes = [anchor.precise_extents_abs()]
    slots: dict[str, tuple] = {}
    invalid: list[str] = []
    for s in sorted(cluster.satellites, key=lambda s: s.ref):
        fp = model.footprints[s.ref]
        why = None
        shape = fp.precise_extents_abs()
        if fp.side != anchor.side:
            why = f"sits on the {fp.side} side of its {anchor.side} anchor"
        elif any(shape.intersection(k).area > placelib.EPS_AREA
                 for k in kept_shapes if shape.intersects(k)):
            why = "collides inside its own cluster"
        else:
            for a in declared:
                if s.ref not in (a.get("cap"), a.get("ic")):
                    continue
                got = placelib.declared_assoc_dist(model, a)
                if got is not None and got[0] > got[1] + 1e-6:
                    why = (f"is {got[0]:.2f} mm from {a['ic']} pin "
                           f"{a.get('pin')} against a declared "
                           f"{got[1]:g} mm limit")
                    break
        if why:
            invalid.append(s.ref)
            warnings.append(f"satellite {s.ref} re-slotted: existing "
                            f"placement {why}")
            continue
        dx = fp.center_abs()[0] - anchor.pos[0]
        dy = fp.center_abs()[1] - anchor.pos[1]
        slots[s.ref] = (_rot(dx, dy, anchor.angle),
                        (fp.angle - anchor.angle) % 360.0)
        kept_shapes.append(shape)
    if not invalid:
        return slots
    reslotted.extend(invalid)
    if cluster.template:
        # a template lays its satellites as one set (symmetric pairs);
        # partial re-derivation has no meaning there
        warnings.append(f"template cluster {cluster.anchor} fully re-derived")
        reslotted.extend(r for r in slots if r not in invalid)
        return place_seed.layout_satellites(model, cluster, warnings)
    slots.update(place_seed.layout_satellites(
        model, cluster, warnings, only=set(invalid), keep=slots))
    return slots


def _build_bodies(model: PlaceModel, clusters, warnings,
                  margin_mm: float = 0.0, decoupling: dict | None = None,
                  reslotted: list[str] | None = None) -> list[Body]:
    bodies = []
    if reslotted is None:
        reslotted = []
    for cid, c in enumerate(clusters):
        slots = _inherit_slots(model, c, decoupling, warnings, reslotted)
        rel_poly, ac = place_seed.cluster_rel_poly(model, c, slots)
        if margin_mm > 0:
            # soft spacing margin (T6 P6A-6): shapes the SA overlap term and
            # repair targets only - placelib legality keeps TRUE courtyards
            rel_poly = rel_poly.buffer(margin_mm / 2.0, join_style=2)
        anchor = model.footprints[c.anchor]
        pads = []
        for p in anchor.pads:
            if p.net:
                pads.append((p.net, p.local[0] - ac[0], p.local[1] - ac[1]))
        thru = "through_hole" in anchor.attrs or any(p.through
                                                     for p in anchor.pads)
        for s in c.satellites:
            fp = model.footprints[s.ref]
            sc = fp.center_local()
            slot, rel = slots[s.ref]
            thru = thru or "through_hole" in fp.attrs or any(p.through
                                                             for p in fp.pads)
            for p in fp.pads:
                if not p.net:
                    continue
                lx, ly = _sat_pad_q(slot, ac, rel, p.local, sc)
                pads.append((p.net, lx, ly))
        kind = "free"
        if c.edge:
            kind = "edge_fixed" if c.edge.get("pos") is not None else "edge"
        here = Variant(rel_poly, pads, slots)
        side0 = anchor.side
        # structural flippability only - placement.sides pins are applied by
        # the Engine, which is the one place that has read the constraints
        bodies.append(Body(
            cid, c, {side0: here, _other(side0): _mirror_variant(here)},
            side0, thru, kind, flippable=(kind == "free" and not thru)))
    return bodies


def _sat_pad_q(slot, ac, rel, pad_local, sc):
    """Cluster-frame pad coord: (slot - ac) + R(-rel).(pad_local - sc)."""
    from geom import _rot
    dx, dy = _rot(pad_local[0] - sc[0], pad_local[1] - sc[1], -rel)
    return (slot[0] - ac[0] + dx, slot[1] - ac[1] + dy)


def _apply_state(model: PlaceModel, bodies: list[Body], centers, angles,
                 sides=None):
    for b in bodies:
        side = b.side0 if sides is None else sides[b.cid]
        for ref in b.refs:
            fp = model.footprints[ref]
            if fp.side != side:
                fp.mirror()
        place_seed.apply_cluster(model, b.cluster, b.variants[side].slots,
                                 centers[b.cid], angles[b.cid])


# ---------------------------------------------------------------- engine

class Engine:
    """Incremental cost evaluation over the cluster state.

    Raw term totals are maintained on every set_state(); cost() recombines
    them with the current weights. All updates are pure functions of the
    coordinates, so reverting a move restores the same values up to float
    accumulation - full_sync() (called every epoch) re-derives every total
    from scratch and kills the drift.
    """

    def __init__(self, model: PlaceModel, bodies: list[Body],
                 constraints: dict, decoupling: dict, *,
                 cell_mm: float = 2.0, cong_cap: int = 4,
                 weights: dict | None = None, margin_mm: float = 0.0):
        self.model = model
        self.bodies = bodies
        self.weights = dict(DEFAULT_WEIGHTS)
        self.weights.update(weights or {})
        self.cell_mm = cell_mm
        self.cong_cap = cong_cap
        self.ov_ramp = 1.0        # annealer raises this as T falls
        self.fb_boost = 1.0       # route-feedback steering multiplier

        self.outline = model.outline
        # a free body's courtyard stays edge_keep inside the outline, so its
        # pad copper clears the board's copper-to-edge rule: kissing the
        # edge left rf-term's M3 hole pads 0.25 mm off it against 0.3 mm
        cu_edge = placelib.copper_edge_mm(model)
        self._keep_in = []
        for b in bodies:
            keep = 0.0
            if b.kind == "free":
                keep = max(model.footprints[r].edge_keep(cu_edge)
                           for r in b.cluster.refs)
            region = self.outline if keep <= 0 else \
                self.outline.buffer(-keep, join_style=2)
            self._keep_in.append((region, prep(region)))
        placement = (constraints or {}).get("placement") or {}
        # U19 placement.sides: a ruled side is a PIN - the annealer never
        # flips that cluster. A ref already sitting on the wrong side is
        # surfaced, never silently "fixed" (the annealer is not the fixer).
        pins = side_pins(placement)
        self.side_unknown_refs = sorted(r for r in pins
                                        if r not in model.footprints)
        self.side_conflicts = sorted(
            f"{r} on {model.footprints[r].side}, pinned {s}"
            for r, s in pins.items()
            if r in model.footprints and model.footprints[r].side != s)
        for b in bodies:
            b.pin_side = next((pins[r] for r in b.refs if r in pins), None)
            if b.pin_side is not None:
                b.flippable = False
        gnd, power = _class_sets(constraints, decoupling)
        self._wnet = {}
        self._wmst = {}
        self._gnd, self._power = gnd, power

        # --- side state (U19): the body's CURRENT side, and its pad table.
        # A pad entry indexes into self._pads[cid], which a flip re-points at
        # the other variant - so entries stay valid across a side change.
        self.sides = [b.side0 for b in bodies]
        self._pads = [b.variants[b.side0].pads for b in bodies]

        # --- pad registry: net -> [(cid, i) | (-1, k into fixed_pts), ...]
        in_cluster = {r for b in bodies for r in b.refs}
        entries: dict[str, list] = {}
        self.fixed_pts: list[tuple[float, float]] = []
        for b in bodies:
            for i, (net, _qx, _qy) in enumerate(b.pads):
                entries.setdefault(net, []).append((b.cid, i))
        for ref in sorted(model.footprints):
            if ref in in_cluster:
                continue
            for _n, net, x, y in model.footprints[ref].pad_centers_abs():
                if net:
                    entries.setdefault(net, []).append(
                        (-1, len(self.fixed_pts)))
                    self.fixed_pts.append((x, y))
        self.entries = {n: v for n, v in sorted(entries.items())
                        if len(v) >= 2}
        self.nets = sorted(self.entries)
        for n in self.nets:
            self._wnet[n] = W_GND if n in gnd else \
                W_PWR if n in power else W_SIG
            self._wmst[n] = 0.0 if n in gnd else \
                MST_PWR if n in power else MST_SIG
        self.mst_nets = [n for n in self.nets if self._wmst[n] > 0]
        # congestion demand: every net but a plane net, each flight line
        # charged its track pitch (module docstring, w_cong)
        inner = set(model.bg.copper_layers[1:-1])
        self.plane_nets = sorted(
            {z.net for z in model.bg.zones_of()
             if z.net and inner.intersection(z.layers)} & set(self.nets))
        self.pitch = net_pitch_units(model.path, self.nets)
        planes = set(self.plane_nets)
        self.cong_nets = [n for n in self.nets if n not in planes]
        self._unit = {n: self.pitch.get(n, 1.0) for n in self.cong_nets}
        # a power plane net is out of the demand but still in the crossings,
        # so flight lines are kept for either
        self.seg_nets = [n for n in self.nets
                         if n in self._unit or self._wmst[n] > 0]
        self.nets_of_body = {
            b.cid: sorted({net for net, _x, _y in b.pads
                           if net in self.entries}) for b in bodies}

        # --- rule terms
        self.current_of = {p["net"]: float(p.get("current_a", 0.0))
                           for p in (constraints or {}).get("power", [])
                           if p.get("net") in self.entries}
        ref2cid = {r: b.cid for b in bodies for r in b.refs}
        self.sep_pairs = []
        self.sep_unknown_refs: list[str] = []
        for s in placement.get("separation", []):
            # A separation ref absent from the board silently dropped the
            # whole constraint (S14: a refdes rename R2 -> R2A/R2B invisibly
            # lost a thermal-separation rule). Collect and surface them.
            self.sep_unknown_refs += sorted(
                {r for r in list(s.get("a", [])) + list(s.get("b", []))
                 if r not in ref2cid})
            ca = sorted({ref2cid[r] for r in s.get("a", []) if r in ref2cid})
            cb = sorted({ref2cid[r] for r in s.get("b", []) if r in ref2cid})
            for i in ca:
                for j in cb:
                    if i != j:
                        self.sep_pairs.append(
                            (min(i, j), max(i, j),
                             float(s.get("min_mm", 5.0))))
        self.th_pairs = []
        th = [(ref2cid[t["ref"]], float(t.get("power_w", 0.0)))
              for t in (constraints or {}).get("thermal", [])
              if t.get("ref") in ref2cid]
        for a in range(len(th)):
            for b_ in range(a + 1, len(th)):
                (i, pa), (j, pb) = th[a], th[b_]
                if i != j and pa > 0 and pb > 0:
                    self.th_pairs.append((min(i, j), max(i, j), pa * pb))
        self.th_spread_mm = 10.0

        # --- corridors (T6 P6A-5, cost-only): placement.corridors entries
        # [{"a": "J1", "b": "J2", "width_mm": 5.5, "net": "VBUS"?}] declare a
        # keep-clear swath between two endpoints; every OTHER body pays
        # CORRIDOR_W per mm^2 of intrusion through the rule term. Endpoints
        # may be cluster refs (swath follows them) or fixed/locked footprints
        # (static anchor). Unknown refs are surfaced, never silently dropped
        # (S14 separation lesson).
        self.corridors = []      # [(end_a, end_b, width_mm, ref_a, ref_b)]
        #                          end = ("cid", cid) | ("pt", (x, y))
        self.corridor_unknown_refs: list[str] = []
        for cor in placement.get("corridors", []):
            refs = (cor.get("a"), cor.get("b"))
            ends, unknown = [], []
            for r in refs:
                if r in ref2cid:
                    ends.append(("cid", ref2cid[r]))
                elif r in model.footprints:
                    ends.append(("pt", model.footprints[r].center_abs()))
                else:
                    unknown.append(str(r))
            if unknown:
                self.corridor_unknown_refs += sorted(set(unknown))
                continue
            if ends[0] == ends[1]:
                continue
            self.corridors.append((ends[0], ends[1],
                                   float(cor.get("width_mm", 3.0)),
                                   refs[0], refs[1]))
        self._corr_end_cids = [
            {v for kind, v in (ea, eb) if kind == "cid"}
            for ea, eb, _w, _ra, _rb in self.corridors]

        # --- obstacles / keepouts (fixed for the whole run)
        fixed_extra = set(placement.get("fixed", []))
        self.obstacles = []      # [(poly, side, thru)]
        # U19: parts that are assembled but never move still put the back side
        # into use - count them so the second-side step is already paid and a
        # flip onto an already-open back only costs the per-part term.
        self.back_base = 0
        for ref in sorted(model.footprints):
            f = model.footprints[ref]
            if ref in in_cluster:
                continue
            if f.side == "back" and "board_only" not in f.attrs:
                self.back_base += 1
            if not f.is_movable or ref in fixed_extra:
                thru = "through_hole" in f.attrs or any(p.through
                                                        for p in f.pads)
                opoly = f.extents_abs()
                if margin_mm > 0:
                    opoly = opoly.buffer(margin_mm / 2.0, join_style=2)
                self.obstacles.append((opoly, f.side, thru))
        self.forbidden = {
            side: [p for p, _w in placelib._forbidden(model, placement, side)]
            for side in ("front", "back")}

        # --- congestion grid
        minx, miny, maxx, maxy = self.outline.bounds
        self._gx0, self._gy0 = minx, miny
        self._cols = max(1, math.ceil((maxx - minx) / cell_mm))
        self._rows = max(1, math.ceil((maxy - miny) / cell_mm))

        # --- state from the board's current placement
        self.centers, self.angles = [], []
        for b in bodies:
            anchor = model.footprints[b.cluster.anchor]
            self.centers.append(anchor.center_abs())
            self.angles.append(anchor.angle % 360.0)
        self._poly_cache: dict[tuple[int, float], object] = {}
        self.out_base = [0.0] * len(bodies)   # edge bodies' seed overhang
        self.full_sync()
        for b in bodies:
            if b.kind != "free":
                self.out_base[b.cid] = self.out_ov[b.cid]
        self._resync_overlap_totals()

    # ------------------------------------------------------------ helpers
    def poly_at(self, cid: int, center=None, angle=None, side=None):
        c = center if center is not None else self.centers[cid]
        a = angle if angle is not None else self.angles[cid]
        s = side if side is not None else self.sides[cid]
        key = (cid, s, round(a % 360.0, 3))
        base = self._poly_cache.get(key)
        if base is None:
            base = affinity.rotate(self.bodies[cid].variants[s].rel_poly, -a,
                                   origin=(0, 0))
            self._poly_cache[key] = base
        return affinity.translate(base, c[0], c[1])

    def _pair_collides(self, i: int, j: int) -> bool:
        a, b = self.bodies[i], self.bodies[j]
        return self.sides[i] == self.sides[j] or a.thru or b.thru

    def assembly_of(self, back_parts: int) -> float:
        """Back-side assembly cost in weighted-HPWL mm (U19 owner ruling)."""
        return (ASM_SECOND_SIDE_MM if back_parts else 0.0) \
            + ASM_PER_PART_MM * back_parts

    def _outside_area(self, cid: int, poly) -> float:
        region, region_prep = self._keep_in[cid]
        if region_prep.contains_properly(poly):
            return 0.0
        out = poly.difference(region).area
        return out if out > EPS else 0.0

    def _coords(self, net: str) -> list[tuple[float, float]]:
        pts = []
        for cid, i in self.entries[net]:
            if cid < 0:
                pts.append(self.fixed_pts[i])
            else:
                _n, qx, qy = self._pads[cid][i]
                cx, cy = self.centers[cid]
                co, si = self._trig[cid]
                pts.append((cx + co * qx - si * qy, cy + si * qx + co * qy))
        return pts

    def entry_pt(self, cid: int, i: int) -> tuple[float, float]:
        """Absolute position of one pad-registry entry (cold path)."""
        if cid < 0:
            return self.fixed_pts[i]
        _n, qx, qy = self._pads[cid][i]
        cx, cy = self.centers[cid]
        co, si = self._trig[cid]
        return (cx + co * qx - si * qy, cy + si * qx + co * qy)

    def _set_trig(self, cid: int) -> None:
        r = math.radians(-self.angles[cid])
        self._trig[cid] = (math.cos(r), math.sin(r))

    # ------------------------------------------------------------ MST bits
    def _mst_segs(self, net: str):
        return placelib._mst_edges(sorted(self.coords[net]))

    def _cells_of_segs(self, segs) -> dict:
        cell = self.cell_mm
        counts: dict[tuple[int, int], int] = {}
        for (ax, ay), (bx, by) in segs:
            length = math.dist((ax, ay), (bx, by))
            steps = max(1, math.ceil(length / (cell / 2)))
            cells = set()
            for s in range(steps + 1):
                t = s / steps
                x, y = ax + (bx - ax) * t, ay + (by - ay) * t
                i = min(self._cols - 1, max(0, int((x - self._gx0) / cell)))
                j = min(self._rows - 1, max(0, int((y - self._gy0) / cell)))
                cells.add((i, j))
            for c in cells:
                counts[c] = counts.get(c, 0) + 1
        return counts

    def _cross_count(self, sa, sb) -> int:
        n = 0
        for (a1, a2) in sa:
            abx0 = min(a1[0], a2[0]); abx1 = max(a1[0], a2[0])
            aby0 = min(a1[1], a2[1]); aby1 = max(a1[1], a2[1])
            for (b1, b2) in sb:
                if min(b1[0], b2[0]) > abx1 or max(b1[0], b2[0]) < abx0 \
                        or min(b1[1], b2[1]) > aby1 \
                        or max(b1[1], b2[1]) < aby0:
                    continue
                if _seg_cross(a1, a2, b1, b2):
                    n += 1
        return n

    # ------------------------------------------------------------ full sync
    def full_sync(self) -> None:
        """Recompute every raw total from the current state."""
        self._trig = {}
        for b in self.bodies:
            self._set_trig(b.cid)
        self.coords = {n: self._coords(n) for n in self.nets}
        self.hpwl_raw = {n: _bbox_hp(self.coords[n]) for n in self.nets}
        self.hpwl_raw_total = sum(self.hpwl_raw.values())
        self.hpwl_w_total = sum(self._wnet[n] * v
                                for n, v in self.hpwl_raw.items())
        self.segs = {n: self._mst_segs(n) for n in self.seg_nets}
        self.cross: dict[tuple[str, str], int] = {}
        self.cross_total = 0.0
        for i, a in enumerate(self.mst_nets):
            for b in self.mst_nets[i + 1:]:
                c = self._cross_count(self.segs[a], self.segs[b])
                if c:
                    self.cross[(a, b)] = c
                    self.cross_total += c * self._wmst[a] * self._wmst[b]
        self.netcells = {n: self._cells_of_segs(self.segs[n])
                         for n in self.cong_nets}
        self.demand: dict[tuple[int, int], float] = {}
        for n in self.cong_nets:
            for c, k in self.netcells[n].items():
                self.demand[c] = self.demand.get(c, 0.0) + k * self._unit[n]
        self.overflow = sum(max(0, d - self.cong_cap)
                            for d in self.demand.values())
        self.polys = {b.cid: self.poly_at(b.cid) for b in self.bodies}
        self.pair_ov = {}
        for i in range(len(self.bodies)):
            for j in range(i + 1, len(self.bodies)):
                a = self._pair_overlap(i, j)
                if a > 0:
                    self.pair_ov[(i, j)] = a
        self.obst_ov = [self._obst_overlap(b.cid) for b in self.bodies]
        self.keep_ov = [self._keep_overlap(b.cid) for b in self.bodies]
        self.out_ov = [self._outside_area(b.cid, self.polys[b.cid])
                       for b in self.bodies]
        self._resync_overlap_totals()
        self._corridor_sync()
        self.rule_total = self._rule_full()
        self.back_parts = self.back_base + sum(
            len(b.refs) for b in self.bodies if self.sides[b.cid] == "back")
        self.assembly_raw = self.assembly_of(self.back_parts)

    def _resync_overlap_totals(self) -> None:
        self.overlap_total = (
            sum(self.pair_ov.values()) + sum(self.obst_ov)
            + sum(self.keep_ov)
            + sum(max(0.0, o - b) for o, b in zip(self.out_ov, self.out_base)))

    def _pair_overlap(self, i: int, j: int) -> float:
        if not self._pair_collides(i, j):
            return 0.0
        pa, pb = self.polys[i], self.polys[j]
        (ax0, ay0, ax1, ay1) = pa.bounds
        (bx0, by0, bx1, by1) = pb.bounds
        if bx0 > ax1 or bx1 < ax0 or by0 > ay1 or by1 < ay0:
            return 0.0
        a = pa.intersection(pb).area
        return a if a > EPS else 0.0

    def _obst_overlap(self, cid: int) -> float:
        b = self.bodies[cid]
        side = self.sides[cid]
        poly = self.polys[cid]
        x0, y0, x1, y1 = poly.bounds
        tot = 0.0
        for opoly, oside, othru in self.obstacles:
            if not (side == oside or b.thru or othru):
                continue
            ox0, oy0, ox1, oy1 = opoly.bounds
            if ox0 > x1 or ox1 < x0 or oy0 > y1 or oy1 < y0:
                continue
            a = poly.intersection(opoly).area
            if a > EPS:
                tot += a
        return tot

    def _keep_overlap(self, cid: int) -> float:
        poly = self.polys[cid]
        x0, y0, x1, y1 = poly.bounds
        tot = 0.0
        for k in self.forbidden[self.sides[cid]]:
            kx0, ky0, kx1, ky1 = k.bounds
            if kx0 > x1 or kx1 < x0 or ky0 > y1 or ky1 < y0:
                continue
            a = poly.intersection(k).area
            if a > EPS:
                tot += a
        return tot

    def _rule_full(self) -> float:
        cur = sum(c * self.hpwl_raw.get(n, 0.0)
                  for n, c in self.current_of.items())
        sep = sum(max(0.0, m - math.dist(self.centers[i],
                                         self.centers[j])) ** 2
                  for i, j, m in self.sep_pairs)
        th = sum(pp * max(0.0, self.th_spread_mm
                          - math.dist(self.centers[i], self.centers[j]))
                 for i, j, pp in self.th_pairs)
        return cur + sep + th + CORRIDOR_W * self.corridor_area

    # ------------------------------------------------------------ corridors
    def _corr_end_pt(self, end):
        kind, v = end
        return self.centers[v] if kind == "cid" else v

    def _corridor_rect(self, k: int):
        ea, eb, w, _a, _b = self.corridors[k]
        ca, cb = self._corr_end_pt(ea), self._corr_end_pt(eb)
        if math.dist(ca, cb) < 1e-6:
            return None
        from shapely.geometry import LineString
        return LineString([ca, cb]).buffer(w / 2.0, cap_style=2)

    def _corr_body_area(self, rect, cid: int) -> float:
        poly = self.polys[cid]
        rx0, ry0, rx1, ry1 = rect.bounds
        x0, y0, x1, y1 = poly.bounds
        if x0 > rx1 or x1 < rx0 or y0 > ry1 or y1 < ry0:
            return 0.0
        a = poly.intersection(rect).area
        return a if a > EPS else 0.0

    def _corridor_one(self, k: int) -> None:
        """(Re)derive corridor k's rect and every body's intrusion area."""
        for key in [key for key in self._corr_area if key[0] == k]:
            self.corridor_area -= self._corr_area.pop(key)
        rect = self._corridor_rect(k)
        self._corr_rects[k] = rect
        if rect is None:
            return
        for b in self.bodies:
            if b.cid in self._corr_end_cids[k]:
                continue
            a = self._corr_body_area(rect, b.cid)
            if a > 0:
                self._corr_area[(k, b.cid)] = a
                self.corridor_area += a

    def _corridor_sync(self) -> None:
        self._corr_rects = [None] * len(self.corridors)
        self._corr_area: dict[tuple[int, int], float] = {}
        self.corridor_area = 0.0
        for k in range(len(self.corridors)):
            self._corridor_one(k)

    def corridor_report(self) -> list[dict]:
        out = []
        for k, (_i, _j, w, ra, rb) in enumerate(self.corridors):
            area = 0.0
            intruders: set[str] = set()
            for (kk, cid), a in self._corr_area.items():
                if kk == k:
                    area += a
                    intruders.update(self.bodies[cid].refs)
            out.append({"a": ra, "b": rb, "width_mm": w,
                        "intrusion_mm2": checklib.rnd(area),
                        "intruders": sorted(intruders)})
        return out

    # ------------------------------------------------------------ moves
    def set_state(self, cid: int, center, angle, side=None) -> None:
        """Move (and optionally FLIP) one body; update every affected total.

        A side change re-points the body at its mirrored variant before any
        geometry is read, so the pad/poly updates below cover the flip exactly
        the way they cover a move - no separate flip path."""
        if side is not None and side != self.sides[cid]:
            self.sides[cid] = side
            self._pads[cid] = self.bodies[cid].variants[side].pads
            n = len(self.bodies[cid].refs)
            self.back_parts += n if side == "back" else -n
            self.assembly_raw = self.assembly_of(self.back_parts)
        self.centers[cid] = (center[0], center[1])
        self.angles[cid] = angle % 360.0
        self._set_trig(cid)

        for net in self.nets_of_body[cid]:
            pts = self._coords(net)
            self.coords[net] = pts
            new = _bbox_hp(pts)
            old = self.hpwl_raw[net]
            if new != old:
                self.hpwl_raw[net] = new
                self.hpwl_raw_total += new - old
                self.hpwl_w_total += self._wnet[net] * (new - old)
                c = self.current_of.get(net)
                if c:
                    self.rule_total += c * (new - old)

        changed = [n for n in self.nets_of_body[cid]
                   if n in self._unit or self._wmst[n] > 0]
        if changed:
            for net in changed:
                segs = self._mst_segs(net)
                self.segs[net] = segs
                if net not in self._unit:
                    continue
                old_cells = self.netcells[net]
                new_cells = self._cells_of_segs(segs)
                u = self._unit[net]
                for c, k in old_cells.items():
                    d0 = self.demand[c]
                    d1 = d0 - k * u
                    self.demand[c] = d1
                    self.overflow += (max(0, d1 - self.cong_cap)
                                      - max(0, d0 - self.cong_cap))
                for c, k in new_cells.items():
                    d0 = self.demand.get(c, 0.0)
                    d1 = d0 + k * u
                    self.demand[c] = d1
                    self.overflow += (max(0, d1 - self.cong_cap)
                                      - max(0, d0 - self.cong_cap))
                self.netcells[net] = new_cells
            changed = [n for n in changed if self._wmst[n] > 0]
            chset = set(changed)
            for net in changed:
                for other in self.mst_nets:
                    if other == net or (other in chset and other < net):
                        continue
                    key = (net, other) if net < other else (other, net)
                    old = self.cross.pop(key, 0)
                    if old:
                        self.cross_total -= old * self._wmst[net] \
                            * self._wmst[other]
                    c = self._cross_count(self.segs[net], self.segs[other])
                    if c:
                        self.cross[key] = c
                        self.cross_total += c * self._wmst[net] \
                            * self._wmst[other]

        poly = self.poly_at(cid)
        self.polys[cid] = poly
        for j in range(len(self.bodies)):
            if j == cid:
                continue
            key = (min(cid, j), max(cid, j))
            old = self.pair_ov.pop(key, 0.0)
            a = self._pair_overlap(*key)
            if a > 0:
                self.pair_ov[key] = a
            self.overlap_total += a - old
        for arr, fn in ((self.obst_ov, self._obst_overlap),
                        (self.keep_ov, self._keep_overlap)):
            old = arr[cid]
            new = fn(cid)
            arr[cid] = new
            self.overlap_total += new - old
        old_out = max(0.0, self.out_ov[cid] - self.out_base[cid])
        self.out_ov[cid] = self._outside_area(cid, poly)
        self.overlap_total += max(0.0, self.out_ov[cid]
                                  - self.out_base[cid]) - old_out

        for k in range(len(self.corridors)):
            if cid in self._corr_end_cids[k]:
                self._corridor_one(k)      # endpoint moved: rect changed
            else:
                rect = self._corr_rects[k]
                if rect is None:
                    continue
                old = self._corr_area.pop((k, cid), 0.0)
                a = self._corr_body_area(rect, cid)
                if a > 0:
                    self._corr_area[(k, cid)] = a
                self.corridor_area += a - old

        if self.sep_pairs or self.th_pairs or self.corridors:
            self.rule_total = self._rule_partial_resync()

    def _rule_partial_resync(self) -> float:
        cur = sum(c * self.hpwl_raw.get(n, 0.0)
                  for n, c in self.current_of.items())
        sep = sum(max(0.0, m - math.dist(self.centers[i],
                                         self.centers[j])) ** 2
                  for i, j, m in self.sep_pairs)
        th = sum(pp * max(0.0, self.th_spread_mm
                          - math.dist(self.centers[i], self.centers[j]))
                 for i, j, pp in self.th_pairs)
        return cur + sep + th + CORRIDOR_W * self.corridor_area

    # ------------------------------------------------------------ cost
    def cost(self) -> float:
        w = self.weights
        return (w["hpwl"] * self.hpwl_w_total
                + w["overlap"] * self.ov_ramp * self.overlap_total
                + w["cong"] * self.fb_boost * self.overflow
                + w["cross"] * self.fb_boost * self.cross_total
                + w["rule"] * self.rule_total
                + w["assembly"] * self.assembly_raw)

    def terms(self) -> dict:
        return {"hpwl_raw_mm": checklib.rnd(self.hpwl_raw_total),
                "hpwl_weighted": checklib.rnd(self.hpwl_w_total),
                "overlap_mm2": checklib.rnd(self.overlap_total),
                "cong_overflow": checklib.rnd(self.overflow),
                "crossings_weighted": checklib.rnd(self.cross_total),
                "rule": checklib.rnd(self.rule_total),
                "corridor_mm2": checklib.rnd(self.corridor_area),
                "assembly_mm": checklib.rnd(self.assembly_raw),
                "back_parts": self.back_parts}

    def side_counts(self) -> dict:
        """Movable parts per side (fixed back-side parts are back_base)."""
        back = sum(len(b.refs) for b in self.bodies
                   if self.sides[b.cid] == "back")
        return {"front": sum(len(b.refs) for b in self.bodies) - back,
                "back": back}


def _bbox_hp(pts) -> float:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def _seg_cross(a1, a2, b1, b2) -> bool:
    """Proper crossing (interiors intersect); touches/collinear -> False."""
    d1 = _orient(b1, b2, a1)
    d2 = _orient(b1, b2, a2)
    if d1 == 0 or d2 == 0 or (d1 > 0) == (d2 > 0):
        return False
    d3 = _orient(a1, a2, b1)
    d4 = _orient(a1, a2, b2)
    return d3 != 0 and d4 != 0 and (d3 > 0) != (d4 > 0)


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


# ---------------------------------------------------------------- annealer

@dataclass
class Params:
    seed: int = 1
    candidates: int = 3
    moves_per_cluster: int = 80
    max_epochs: int = 140
    stall: int = 15
    grid: float = 0.25
    cell_mm: float = 2.0
    cong_cap: int = 4
    edge_margin: float = 0.8
    feedback_every: int = 10
    weights: dict | None = None
    margin_mm: float = 0.0     # soft body-spacing margin (0.0 = legacy)
    allow_flip: bool = True    # U19 side-flip moves


class Annealer:
    def __init__(self, engine: Engine, params: Params, route_probe=None):
        self.e = engine
        self.p = params
        self.rng = random.Random(params.seed)
        self.route_probe = route_probe
        self.free = [b.cid for b in engine.bodies if b.kind == "free"]
        self.edge = [b.cid for b in engine.bodies if b.kind == "edge"]
        self.flippable = [b.cid for b in engine.bodies if b.flippable] \
            if params.allow_flip else []
        minx, miny, maxx, maxy = engine.outline.bounds
        self.w0 = 0.35 * math.hypot(maxx - minx, maxy - miny)
        self.window = self.w0
        self.completion = None
        self.trace: list[dict] = []

    # --------------------------------------------------------- proposals
    def _snap(self, v: float) -> float:
        g = self.p.grid
        return round(v / g) * g

    def _propose(self):
        """-> list of (cid, new_center, new_angle, new_side) or None."""
        e, rng = self.e, self.rng
        r = rng.random()
        # flips take their band off the TOP of the range (the translate slice,
        # the most redundant move) so an unflippable board's move mix - and
        # its rng stream - is bit-identical to the pre-U19 annealer
        if self.flippable and r >= 1.0 - FLIP_P:
            return self._flip()
        if self.edge and r < 0.10:
            if len(self.edge) >= 2 and rng.random() < 0.35:
                i, j = rng.sample(self.edge, 2)
                bi, bj = e.bodies[i], e.bodies[j]
                if bi.cluster.edge["edge"] != bj.cluster.edge["edge"]:
                    return None
                axis = 1 if bi.cluster.edge["edge"] in ("left", "right") else 0
                ci, cj = e.centers[i], e.centers[j]
                ni = list(ci)
                nj = list(cj)
                ni[axis], nj[axis] = cj[axis], ci[axis]
                return [(i, tuple(ni), e.angles[i], e.sides[i]),
                        (j, tuple(nj), e.angles[j], e.sides[j])]
            cid = rng.choice(self.edge)
            return self._slide(cid)
        if not self.free:
            return None
        if r < 0.30:
            cid = rng.choice(self.free)
            ang = (e.angles[cid] + rng.choice((90.0, 180.0, 270.0))) % 360.0
            return [(cid, e.centers[cid], ang, e.sides[cid])]
        if r < 0.50 and len(self.free) >= 2:
            i, j = rng.sample(self.free, 2)
            return [(i, e.centers[j], e.angles[i], e.sides[i]),
                    (j, e.centers[i], e.angles[j], e.sides[j])]
        cid = rng.choice(self.free)
        cx, cy = e.centers[cid]
        w = max(self.p.grid, self.window)
        nx = self._snap(cx + (rng.random() * 2 - 1) * w)
        ny = self._snap(cy + (rng.random() * 2 - 1) * w)
        minx, miny, maxx, maxy = e.outline.bounds
        nx = min(maxx, max(minx, nx))
        ny = min(maxy, max(miny, ny))
        return [(cid, (nx, ny), e.angles[cid], e.sides[cid])]

    def _flip(self):
        """Move one cluster to the other side (U19).

        Half the flips also RELOCATE to the cluster's connectivity centroid:
        a bare toggle is uphill by the assembly term alone, so SA would have to
        accept a strictly-worse state and then find the payoff before it cooled
        past it. Landing where the wirelength actually is puts the gain in the
        same move as the cost, which is what makes the back side discoverable
        rather than merely reachable."""
        e, rng = self.e, self.rng
        cid = rng.choice(self.flippable)
        centre = e.centers[cid]
        if rng.random() < 0.5:
            centre = self._connect_target(cid) or centre
        return [(cid, centre, e.angles[cid], _other(e.sides[cid]))]

    def _connect_target(self, cid: int):
        """Mean position of everything this cluster is wired to."""
        e = self.e
        sx = sy = 0.0
        n = 0
        for net in e.nets_of_body[cid]:
            for ocid, i in e.entries[net]:
                if ocid == cid:
                    continue
                x, y = e.entry_pt(ocid, i)
                sx += x
                sy += y
                n += 1
        if not n:
            return None
        minx, miny, maxx, maxy = e.outline.bounds
        return (min(maxx, max(minx, self._snap(sx / n))),
                min(maxy, max(miny, self._snap(sy / n))))

    def _slide(self, cid: int):
        e, rng = self.e, self.rng
        b = e.bodies[cid]
        edge = b.cluster.edge["edge"]
        axis = 1 if edge in ("left", "right") else 0
        minx, miny, maxx, maxy = e.outline.bounds
        lo, hi = (miny, maxy) if axis == 1 else (minx, maxx)
        poly = e.polys[cid]
        c = list(e.centers[cid])
        # the cluster's real reach each side of its center (a one-sided
        # satellite makes it asymmetric), as place_seed's edge span uses
        b_lo = poly.bounds[axis] - c[axis]
        b_hi = poly.bounds[axis + 2] - c[axis]
        t = c[axis] + (rng.random() * 2 - 1) * max(self.p.grid, self.window)
        t = min(hi - self.p.edge_margin - b_hi,
                max(lo + self.p.edge_margin - b_lo, self._snap(t)))
        c[axis] = t
        return [(cid, tuple(c), e.angles[cid], e.sides[cid])]

    # --------------------------------------------------------- mechanics
    def _try(self, moves) -> tuple[bool, float]:
        e = self.e
        before = e.cost()
        olds = [(cid, e.centers[cid], e.angles[cid], e.sides[cid])
                for cid, _c, _a, _s in moves]
        for cid, c, a, s in moves:
            e.set_state(cid, c, a, s)
        delta = e.cost() - before
        if delta <= 0 or (self.t > 0 and delta / self.t < 700
                          and self.rng.random() < math.exp(-delta / self.t)):
            return True, delta
        for cid, c, a, s in reversed(olds):
            e.set_state(cid, c, a, s)
        return False, delta

    def _estimate_t0(self) -> float:
        ups = []
        self.t = 0.0  # metropolis off: only downhill accepted during probe
        n = max(30, 4 * (len(self.free) + len(self.edge)))
        for _ in range(n):
            moves = self._propose()
            if not moves:
                continue
            e = self.e
            olds = [(cid, e.centers[cid], e.angles[cid], e.sides[cid])
                    for cid, _c, _a, _s in moves]
            before = e.cost()
            for cid, c, a, s in moves:
                e.set_state(cid, c, a, s)
            d = e.cost() - before
            for cid, c, a, s in reversed(olds):
                e.set_state(cid, c, a, s)
            if d > 0:
                ups.append(d)
        base = sum(ups) / len(ups) if ups else max(1.0, 0.01 * self.e.cost())
        return 20.0 * base

    def snapshot(self):
        return (tuple(self.e.centers), tuple(self.e.angles),
                tuple(self.e.sides))

    def _distinct(self, s1, s2) -> bool:
        for (c1, c2) in zip(s1[0], s2[0]):
            if abs(c1[0] - c2[0]) > 2.0 or abs(c1[1] - c2[1]) > 2.0:
                return True
        for (a1, a2) in zip(s1[1], s2[1]):
            if abs((a1 - a2) % 360.0) > 1.0:
                return True
        return s1[2] != s2[2]

    # --------------------------------------------------------- main loop
    def run(self) -> dict:
        e, p = self.e, self.p
        n_mov = len(self.free) + len(self.edge)
        if n_mov == 0:
            return {"pool": [(e.cost(), self.snapshot(), None)],
                    "moves": 0, "accepted": 0, "epochs": 0,
                    "t0": 0.0, "t_end": 0.0,
                    "note": "no movable clusters - nothing to anneal"}
        t0 = self._estimate_t0()
        e.full_sync()   # kill the apply/revert float drift from sampling
        self.t = t0
        moves_per_epoch = p.moves_per_cluster * n_mov
        best_cost = e.cost()
        best = self.snapshot()
        pool: list[tuple[float, tuple, float | None]] = [
            (best_cost, best, self.completion)]
        total_moves = total_acc = 0
        stall = 0
        epoch = 0
        for epoch in range(1, p.max_epochs + 1):
            e.ov_ramp = min(8.0, math.sqrt(t0 / max(self.t, 1e-9))) \
                if t0 > 0 else 1.0
            acc = 0
            tried = 0
            improved = False
            for _ in range(moves_per_epoch):
                moves = self._propose()
                if not moves:
                    continue
                tried += 1
                ok, _d = self._try(moves)
                if ok:
                    acc += 1
                    c = e.cost()
                    if c < best_cost - 1e-9:
                        best_cost = c
                        best = self.snapshot()
                        improved = True
                        self._pool_add(pool, c, best)
            total_moves += tried
            total_acc += acc
            alpha = acc / tried if tried else 0.0
            e.full_sync()   # kill float drift epoch by epoch
            if self.route_probe and epoch % p.feedback_every == 0:
                self._probe(best)
            self.trace.append({"epoch": epoch, "t": checklib.rnd(self.t, 6),
                               "alpha": checklib.rnd(alpha, 3),
                               "best": checklib.rnd(best_cost, 3),
                               "window": checklib.rnd(self.window, 2)})
            # stall only counts in the cold regime: at high T "best" rarely
            # improves and the counter would kill the run before the cold
            # phase where SA does its real work
            stall = 0 if (improved or alpha >= 0.2) else stall + 1
            if stall >= p.stall:
                break
            if self.t < t0 * 1e-4 and alpha < 0.02:
                break
            if alpha > 0.9:
                self.t *= 0.6
            elif alpha > 0.5:
                self.t *= 0.9
            elif alpha > 0.2:
                self.t *= 0.95
            elif alpha > 0.05:
                self.t *= 0.9
            else:
                self.t *= 0.75
            self.window = min(self.w0, max(2 * p.grid,
                                           self.window * (0.56 + alpha)))
        # quench: greedy descent from the best state
        t_end = self.t
        for cid, c, a, s in zip(range(len(e.bodies)), best[0], best[1],
                                best[2]):
            e.set_state(cid, c, a, s)
        e.full_sync()
        e.ov_ramp = 8.0
        self.t = 0.0
        q_moves = 4 * n_mov * p.moves_per_cluster // 8
        self.window = max(2 * p.grid, 4 * p.grid)
        for _ in range(q_moves):
            moves = self._propose()
            if not moves:
                continue
            total_moves += 1
            ok, _d = self._try(moves)
            if ok:
                total_acc += 1
                c = e.cost()
                if c < best_cost - 1e-9:
                    best_cost = c
                    best = self.snapshot()
                    self._pool_add(pool, c, best)
        e.full_sync()
        self._pool_add(pool, e.cost(), self.snapshot())
        return {"pool": pool, "moves": total_moves, "accepted": total_acc,
                "epochs": epoch, "t0": t0, "t_end": t_end}

    def _pool_add(self, pool, cost, state) -> None:
        pool.append((cost, state, self.completion))
        pool.sort(key=lambda x: x[0])
        del pool[4 * self.p.candidates:]

    def _probe(self, best_state) -> None:
        _apply_state(self.e.model, self.e.bodies, best_state[0],
                     best_state[1], best_state[2])
        c = float(self.route_probe(self.e.model))
        self.completion = min(1.0, max(0.0, c))
        self.e.fb_boost = 1.0 + FB_GAIN * (1.0 - self.completion)


# ---------------------------------------------------------------- repair

def _repair(model: PlaceModel, engine: Engine, placement: dict | None,
            grid: float = 0.25, max_r_mm: float = 6.0) -> bool:
    """Greedy spiral nudge for clusters left in minor illegality. Mutates the
    model AND engine state so caller reads consistent ops. Returns True if
    anything moved."""
    viol = placelib.legality_violations(model, placement)
    ref2cid = {r: b.cid for b in engine.bodies for r in b.refs}
    bad = sorted({ref2cid[r] for v in viol if v["severity"] == "error"
                  for r in v.get("refs", []) if r in ref2cid})
    if not bad:
        return False
    moved = False
    for cid in bad:
        b = engine.bodies[cid]
        if b.kind == "edge_fixed":
            continue
        cur = engine.centers[cid]
        ang = engine.angles[cid]
        axis = None
        if b.kind == "edge":
            axis = 1 if b.cluster.edge["edge"] in ("left", "right") else 0
        found = None
        steps = int(max_r_mm / grid)
        for r in range(1, steps + 1):
            cands = []
            if axis is None:
                for i in range(-r, r + 1):
                    for j in range(-r, r + 1):
                        if max(abs(i), abs(j)) != r:
                            continue
                        cands.append((cur[0] + i * grid, cur[1] + j * grid))
            else:
                for s in (r, -r):
                    c = list(cur)
                    c[axis] += s * grid
                    cands.append(tuple(c))
            for cand in sorted(cands, key=lambda c_: (
                    (c_[0] - cur[0]) ** 2 + (c_[1] - cur[1]) ** 2,
                    c_[1], c_[0])):
                if _spot_legal(engine, cid, cand, ang):
                    found = cand
                    break
            if found:
                break
        if found:
            engine.set_state(cid, found, ang)
            moved = True
    if moved:
        engine.full_sync()
        _apply_state(model, engine.bodies, engine.centers, engine.angles,
                     engine.sides)
    return moved


def _spot_legal(engine: Engine, cid: int, center, angle) -> bool:
    poly = engine.poly_at(cid, center, angle)
    b = engine.bodies[cid]
    side = engine.sides[cid]
    if b.kind == "free" and engine._outside_area(cid, poly) > 0:
        return False
    # an edge cluster may overhang only as far as the seed's designed
    # overhang (out_base) - the same allowance the SA cost gives it
    if b.kind != "free" and (engine._outside_area(cid, poly)
                             > engine.out_base[cid] + EPS):
        return False
    x0, y0, x1, y1 = poly.bounds
    for j, other in enumerate(engine.bodies):
        if j == cid or not engine._pair_collides(cid, j):
            continue
        ox0, oy0, ox1, oy1 = engine.polys[j].bounds
        if ox0 > x1 or ox1 < x0 or oy0 > y1 or oy1 < y0:
            continue
        if poly.intersection(engine.polys[j]).area > EPS:
            return False
    for opoly, oside, othru in engine.obstacles:
        if not (side == oside or b.thru or othru):
            continue
        if poly.intersection(opoly).area > EPS:
            return False
    for k in engine.forbidden[side]:
        if poly.intersection(k).area > EPS:
            return False
    return True


# ---------------------------------------------------------------- driver

def anneal(pcb: Path, constraints: dict, decoupling: dict, params: Params,
           route_probe=None):
    """-> (candidates, facts, model). Candidates: list of dicts sorted by
    blended score; each carries state ops + legality. Board file untouched."""
    t_start = time.perf_counter()
    model = PlaceModel(pcb)
    placement = (constraints or {}).get("placement") or {}
    hpwl_input = placelib.hpwl(model)["total_mm"]

    clusters, warnings = placelib.build_clusters(model, decoupling, placement)
    reslotted: list[str] = []
    bodies = _build_bodies(model, clusters, warnings,
                           margin_mm=params.margin_mm, decoupling=decoupling,
                           reslotted=reslotted)
    engine = Engine(model, bodies, constraints, decoupling,
                    cell_mm=params.cell_mm, cong_cap=params.cong_cap,
                    weights=params.weights, margin_mm=params.margin_mm)
    ann = Annealer(engine, params, route_probe=route_probe)
    start_terms = engine.terms()
    result = ann.run()

    # distinct top-N
    chosen: list[tuple[float, tuple, float | None]] = []
    for cost, state, comp in result["pool"]:
        if any(not ann._distinct(state, s) for _c, s, _f in chosen):
            continue
        chosen.append((cost, state, comp))
        if len(chosen) >= params.candidates:
            break
    if not chosen:
        chosen = result["pool"][:1]

    candidates = []
    for k, (cost, state, comp) in enumerate(chosen):
        # snapshots taken before the first probe carry no completion; the
        # last probe is the best estimate for all near-final states
        if comp is None:
            comp = ann.completion
        for cid in range(len(bodies)):
            engine.set_state(cid, state[0][cid], state[1][cid], state[2][cid])
        engine.full_sync()
        _apply_state(model, bodies, engine.centers, engine.angles,
                     engine.sides)
        repaired = _repair(model, engine, placement, grid=params.grid)
        viol = placelib.legality_violations(model, placement)
        # U20 candidate REJECTION, not scoring: a candidate violating a
        # DECLARED decoupling distance is illegal - it can never rank best
        # over a clean one, --apply-best refuses it, and an all-violating
        # pool exits 1 instead of shipping the violation as a "win"
        viol += placelib.declared_decap_violations(model, decoupling)
        errors = [v for v in viol if v["severity"] == "error"]
        ops = []
        for b in bodies:
            for ref in b.refs:
                fp = model.footprints[ref]
                ops.append({"op": "place", "ref": ref,
                            "x": checklib.rnd(fp.pos[0]),
                            "y": checklib.rnd(fp.pos[1]),
                            "deg": checklib.rnd(fp.angle % 360.0),
                            "side": fp.side})
        terms = engine.terms()
        final_cost = engine.cost()
        score = final_cost * (1.0 + engine.weights["feedback"]
                              * (1.0 - comp)) if comp is not None \
            else final_cost
        cand = {
            "rank": k + 1,
            "cost": checklib.rnd(final_cost, 3),
            "score": checklib.rnd(score, 3),
            "hpwl_mm": terms["hpwl_raw_mm"],
            "terms": terms,
            "legal": not errors,
            "repaired": repaired,
            "completion": comp,
            "sides": engine.side_counts(),
            "violations": viol,
            "ops": ops,
        }
        if engine.corridors:
            cand["corridors"] = engine.corridor_report()
        candidates.append(cand)
    candidates.sort(key=lambda c: (not c["legal"], c["score"]))
    for k, c in enumerate(candidates):
        c["rank"] = k + 1

    best = candidates[0]
    facts = {
        "seed": params.seed,
        "separation_unknown_refs": sorted(set(engine.sep_unknown_refs)),
        "corridor_unknown_refs": sorted(set(engine.corridor_unknown_refs)),
        "side_unknown_refs": engine.side_unknown_refs,
        "side_conflicts": engine.side_conflicts,
        "clusters": len(bodies),
        "movable_clusters": len(ann.free) + len(ann.edge),
        "flippable_clusters": len(ann.flippable),
        "back_parts_fixed": engine.back_base,
        "sides_input": {"front": sum(len(b.refs) for b in bodies
                                     if b.side0 == "front"),
                        "back": sum(len(b.refs) for b in bodies
                                    if b.side0 == "back")},
        "sides_best": best["sides"],
        "satellites_reslotted": sorted(set(reslotted)),
        "hpwl_input_mm": hpwl_input,
        "hpwl_start_mm": start_terms["hpwl_raw_mm"],
        "hpwl_best_mm": best["hpwl_mm"],
        "improvement_pct": checklib.rnd(
            100.0 * (hpwl_input - best["hpwl_mm"]) / hpwl_input, 2)
        if hpwl_input else None,
        "start_terms": start_terms,
        "epochs": result["epochs"],
        "moves": result["moves"],
        "accepted": result["accepted"],
        "t0": checklib.rnd(result["t0"], 4),
        "t_end": checklib.rnd(result["t_end"], 6),
        "feedback_used": route_probe is not None,
        "last_completion": ann.completion,
        "runtime_s": checklib.rnd(time.perf_counter() - t_start, 2),
        "warnings": warnings,
    }
    if "note" in result:
        facts["note"] = result["note"]
    return candidates, facts, model


def make_route_probe(pcb: Path, *, passes: int = 4, timeout_s: int = 180):
    """S11 routability probe for --route-feedback (PROGRESS S10 -> S11).

    Returns fn(model: PlaceModel) -> completion fraction [0, 1]: snapshot the
    model's placement onto a scratch copy of the board (place_edit absolute
    ops), then run route_auto.route_probe (DSN export + capped-effort
    Freerouting, no SES import). A probe failure returns 0.0 - the annealer
    treats an unprobeable placement as unroutable rather than crashing the
    run.
    """
    import shutil

    import place_edit  # noqa: PLC0415 - lazy, only with --route-feedback
    import route_auto  # noqa: PLC0415

    pcb = Path(pcb).resolve()
    work = pcb.parent / "route_probe_anneal"

    def probe(model) -> float:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        staged = work / pcb.name
        for src in pcb.parent.glob(pcb.stem + ".*"):
            if src.is_file() and not src.name.endswith(".lck"):
                shutil.copy2(src, work / src.name)
        ops = [{"op": "place", "ref": ref, "x": fp.pos[0], "y": fp.pos[1],
                "deg": fp.angle, "side": fp.side}
               for ref, fp in sorted(model.footprints.items())]
        try:
            place_edit.apply_ops(staged, ops)
            facts = route_auto.route_probe(
                staged, passes=passes, timeout_s=timeout_s,
                work_dir=work / "probe")
        except CheckError:
            return 0.0
        c = facts.get("completion")
        return float(c) if c is not None else 0.0

    return probe


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: next to the board)")
    ap.add_argument("--decoupling", default=None,
                    help="decoupling.json (default: next to the board)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--candidates", type=int, default=3)
    ap.add_argument("--out-dir", default=None,
                    help="candidate ops dir (default: <board dir>/anneal)")
    ap.add_argument("--apply-best", action="store_true",
                    help="apply the best legal candidate via place_edit")
    ap.add_argument("--route-feedback", action="store_true",
                    help="blend fast-route completion into cost (S11 probe: "
                         "place_edit snapshot -> DSN -> capped Freerouting)")
    ap.add_argument("--feedback-every", type=int, default=10)
    ap.add_argument("--probe-passes", type=int, default=4,
                    help="Freerouting passes per feedback probe")
    ap.add_argument("--probe-timeout-s", type=int, default=180)
    ap.add_argument("--moves-per-cluster", type=int, default=80)
    ap.add_argument("--max-epochs", type=int, default=140)
    ap.add_argument("--stall", type=int, default=15)
    ap.add_argument("--grid-mm", type=float, default=0.25)
    ap.add_argument("--cell-mm", type=float, default=2.0)
    ap.add_argument("--cong-cap", type=int, default=4)
    ap.add_argument("--edge-margin-mm", type=float, default=0.8)
    ap.add_argument("--margin-mm", type=float, default=0.0,
                    help="soft body-spacing margin: buffers bodies/obstacles "
                         "by margin/2 in the SA overlap term + repair targets "
                         "(legality keeps true courtyards). Use ~0.5 on "
                         "boards with silk-debt history")
    ap.add_argument("--no-side-flips", action="store_true",
                    help="never move a cluster to the back side (U19). The "
                         "assembly cost term already keeps a board that does "
                         "not need two sides single-sided; use this when the "
                         "back must stay empty for a reason the constraints "
                         "do not carry")
    for name in WEIGHT_NAMES:
        ap.add_argument(f"--w-{name}", type=float, default=None)
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")

    route_probe = None
    if args.route_feedback:
        route_probe = make_route_probe(pcb, passes=args.probe_passes,
                                       timeout_s=args.probe_timeout_s)

    def sidecar(explicit, name):
        if explicit:
            return checklib.load_json(explicit, name)
        p = pcb.parent / name
        return checklib.load_json(p, name) if p.is_file() else {}

    constraints = sidecar(args.constraints, "constraints.json")
    decoupling = sidecar(args.decoupling, "decoupling.json")

    weights = {}
    for name in WEIGHT_NAMES:
        v = getattr(args, f"w_{name}")
        if v is not None:
            weights[name] = v
    params = Params(seed=args.seed, candidates=args.candidates,
                    moves_per_cluster=args.moves_per_cluster,
                    max_epochs=args.max_epochs, stall=args.stall,
                    grid=args.grid_mm, cell_mm=args.cell_mm,
                    cong_cap=args.cong_cap, edge_margin=args.edge_margin_mm,
                    feedback_every=args.feedback_every,
                    weights=weights or None, margin_mm=args.margin_mm,
                    allow_flip=not args.no_side_flips)

    candidates, facts, _model = anneal(pcb, constraints, decoupling, params,
                                       route_probe=route_probe)

    out_dir = Path(args.out_dir) if args.out_dir else pcb.parent / "anneal"
    out_dir.mkdir(parents=True, exist_ok=True)
    for c in candidates:
        f = out_dir / f"cand{c['rank']}.ops.json"
        f.write_text(json.dumps({"version": 1, "ops": c["ops"]}, indent=1),
                     encoding="utf-8")
        c["ops_file"] = str(f)
    best = candidates[0]

    if args.apply_best:
        if not best["legal"]:
            raise CheckError("no legal candidate to apply "
                             f"({len(best['violations'])} violations on best)")
        import place_edit
        place_edit.apply_ops(pcb, best["ops"])
        facts["applied"] = True
        facts["hpwl_applied_mm"] = placelib.hpwl(
            PlaceModel(pcb))["total_mm"]

    slim = [{k: v for k, v in c.items() if k not in ("ops", "violations")}
            | {"n_violations": len(c["violations"])}
            for c in candidates]
    payload = checklib.report("place_anneal", str(pcb), best["violations"],
                              candidates=slim, **facts)
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("place_anneal", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
