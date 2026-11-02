"""emvia.py - via transitions and launches in 3D (#479, #599 item 3).

A via with its pads, antipads, return vias and stub, or an edge SMA landing on
a line, as an openEMS deck (lib/emboard.deck plus an `extra` hook) -> S at the
port planes, the via's L and C and effective Z, the stub's notch, and a rule.

Two ways in, one deck: `synth` draws the via from a stack and a few numbers (no
board needed), `from_board` cuts it out of a .kicad_pcb and finds the ports
where the signal's tracks cross the region's edge.

What this adds to emboard.deck, all in `mesh_hook`:
  - a via's barrels and small pads are where FDTD's timestep collapses
    (kb/em-solvers.md): deck() pins a mesh line on every vertex of a round
    pad or antipad, down to fine/3. Inside the via's zone those are dropped for
    a uniform `fine` grid, so a round antipad staircases instead of setting dt,
    and only each barrel's two faces stay. `mesh_stats` gives cells, the
    smallest cell and the Courant dt, and refuses a deck under fine/4;
  - z lines through every slab at `zstep`: deck() gives a slab no signal layer
    touches at most 5 cells, too few for a stub's quarter wave;
  - posts tying the planes together along a feed, or a stripline's lumped port
    (signal to one plane) drives the plane pair as well as the line.
The floor is always open here: deck()'s default PEC floor on the bottom layer
would short a stub that reaches it.

The planes stop at the region's edge, with absorbing walls past it: a large
board's cavity modes are not in the answer. A through via with no return via
near it is answered for this region's plane pair, not for the board's.

The launch has no excitation in the coax. The source stays on the line's port
and the run is done twice, the coax's far end absorbing and then shorted: the
waves of two different loads give S = B A^-1 with no assumption about either
load, the way two excited ports do. The coax is read by emboard.port_vi's
three V and two I probes like any feed, so a staircased coax that is not quite
50 ohm reads as what it is and the plane moves to the jack's mating face.

Lengths mm, frequencies Hz, via at the origin in `synth`.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import emboard as eb
from . import emsolve as em

C0 = em.C0
RETURN_FRAC = 0.1      # nearest return via within this many wavelengths (a rule of thumb, as in viastub)
NOTCH_KNEE = 2.0       # #479: a stub notch under 2 x the net's top frequency is a finding
NOTCH_DEPTH_DB = 3.0   # a dip this far under its band edges counts as a notch


# ================================================================ geometry

def ring(n: int, r: float, start_deg: float = 0.0) -> list[tuple[float, float]]:
    """n return vias on a circle of radius r around the via."""
    return [(round(r * math.cos(math.radians(start_deg + 360 * i / n)), 6),
             round(r * math.sin(math.radians(start_deg + 360 * i / n)), 6)) for i in range(n)]


@dataclass
class ViaSpec:
    """A signal via from in_layer to out_layer. Every other layer of the stack
    is a plane with an antipad, and the barrel runs barrel_top..barrel_bot
    (default: the stack's outer layers), so what lies past out_layer is a stub
    and barrel_bot=out_layer is its blind twin."""
    stack: dict
    in_layer: str
    out_layer: str
    w_in: float
    w_out: float
    drill: float = 0.3
    pad: float = 0.6
    antipad: float = 1.0
    returns: list = field(default_factory=list)
    ret_drill: float = 0.3
    ret_pad: float = 0.6
    run: float = 3.0            # via centre to each port plane
    half_y: float = 4.0         # half the region's width
    barrel_top: str | None = None
    barrel_bot: str | None = None
    ret_top: str | None = None  # the return vias' span (default: the whole stack)
    ret_bot: str | None = None
    pad_all: bool = False       # keep the signal's pads on unused layers


def _layers_between(stack: dict, a: str, b: str) -> list[str]:
    ls = stack["layers"]
    i, j = sorted((ls.index(a), ls.index(b)))
    return ls[i:j + 1]


def synth(spec: ViaSpec) -> tuple[dict, list[eb.Port]]:
    """(geo for emboard.deck, its two ports) from a ViaSpec."""
    from shapely.geometry import Point, box
    from shapely.ops import unary_union
    st = spec.stack
    layers = st["layers"]
    top = spec.barrel_top or layers[0]
    bot = spec.barrel_bot or layers[-1]
    for name in (spec.in_layer, spec.out_layer, top, bot):
        if name not in layers:
            raise ValueError(f"{name} is not in the stack {layers}")
    span = _layers_between(st, top, bot)
    if spec.in_layer not in span or spec.out_layer not in span:
        raise ValueError(f"the barrel {top}..{bot} doesn't reach {spec.in_layer} and {spec.out_layer}")
    if spec.ret_pad / 2 + spec.pad / 2 > min((math.hypot(*r) for r in spec.returns), default=9e9):
        raise ValueError("a return via's pad touches the signal via's pad")
    x0, x1, y1 = -spec.run, spec.run, spec.half_y
    region = (x0, -y1, x1, y1)
    rect = box(*region)
    centre = Point(0, 0)
    pad = centre.buffer(spec.pad / 2, quad_segs=8)
    ret_pads = unary_union([Point(*r).buffer(spec.ret_pad / 2, quad_segs=6) for r in spec.returns]) \
        if spec.returns else None
    copper = []

    def add(layer, net, g):
        for pts in eb._polys(g):
            copper.append({"layer": layer, "net": net, "pts": pts})
    sig_in = box(x0, -spec.w_in / 2, 0, spec.w_in / 2).union(pad)
    sig_out = box(0, -spec.w_out / 2, x1, spec.w_out / 2).union(pad)
    if spec.in_layer == spec.out_layer:
        raise ValueError("in_layer and out_layer are the same layer")
    ret_top, ret_bot = spec.ret_top or layers[0], spec.ret_bot or layers[-1]
    ret_span = _layers_between(st, ret_top, ret_bot)
    for layer, sig in ((spec.in_layer, sig_in), (spec.out_layer, sig_out)):
        if ret_pads is not None and layer in ret_span and ret_pads.intersects(sig):
            raise ValueError(f"a return via's pad shorts the signal on {layer}: move the ring or span")
    for layer in layers:
        if layer == spec.in_layer:
            add(layer, "SIG", sig_in)
        elif layer == spec.out_layer:
            add(layer, "SIG", sig_out)
        else:
            add(layer, "GND", rect.difference(centre.buffer(spec.antipad / 2, quad_segs=8)))
            if layer in span and (spec.pad_all or layer in (top, bot)):
                add(layer, "SIG", pad)
        if ret_pads is not None and layer in (spec.in_layer, spec.out_layer) and layer in ret_span:
            add(layer, "GND", ret_pads)
    drills = [{"x": 0.0, "y": 0.0, "d": spec.drill, "net": "SIG", "top": top, "bot": bot}]
    drills += [{"x": r[0], "y": r[1], "d": spec.ret_drill, "net": "GND",
                "top": ret_top, "bot": ret_bot} for r in spec.returns]
    geo = {"source": "synth", "region": list(region), "stack": st, "copper": copper,
           "drills": drills, "lumped": [],
           "via": {"x": 0.0, "y": 0.0, "drill": spec.drill, "antipad": spec.antipad,
                   "returns": [list(r) for r in spec.returns],
                   "in": spec.in_layer, "out": spec.out_layer, "top": top, "bot": bot}}
    ports = [eb.Port(x0, 0.0, "-x", spec.in_layer, spec.w_in),
             eb.Port(x1, 0.0, "+x", spec.out_layer, spec.w_out)]
    return geo, ports


def stack_named(name: str) -> dict:
    """A stackups.yaml build as an emboard Stack dict."""
    import yaml
    path = Path(__file__).resolve().parents[2] / "reference" / "stackups.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entry = (data.get("stackups") or {}).get(name)
    if entry is None:
        raise ValueError(f"stackup {name!r} is not in reference/stackups.yaml")
    cu = [s["name"] for s in entry["stack"] if s["type"] == "copper"]
    gaps = [s for s in entry["stack"] if s["type"] != "copper" and "epsilon_r" in s]
    z, acc = {}, 0.0
    for i in range(len(cu) - 1, -1, -1):
        z[cu[i]] = round(acc, 6)
        if i > 0:
            acc += gaps[i - 1]["thickness_mm"]
    slabs = [[z[cu[i + 1]], z[cu[i]], float(gaps[i]["epsilon_r"])] for i in range(len(cu) - 1)]
    return {"layers": cu, "z": z, "slabs": slabs}


def from_board(pcb, x: float, y: float, radius: float = 3.0, grow_to: float = 8.0) -> tuple[dict, list[eb.Port]]:
    """(geo, ports) for the via at (x, y) on a board: the region is a square
    round it, grown until the via's net crosses the edge exactly twice, on
    straight axis-aligned tracks. Refuses (and says why) when it never does."""
    import sys
    from shapely.geometry import box
    sys.path.insert(0, str(Path(__file__).resolve().parent))     # viastub imports geom by its bare name
    import viastub
    from . import geom as G
    bg = G.load_board(pcb)
    via = min(bg.vias_of(), key=lambda v: math.hypot(v.at[0] - x, v.at[1] - y), default=None)
    if via is None or math.hypot(via.at[0] - x, via.at[1] - y) > 0.5:
        raise ValueError(f"no via within 0.5 mm of ({x}, {y}) on {pcb}")
    used = viastub.used_layers(bg, via)
    if len(used) < 2:
        raise ValueError(f"via at {via.at}: net {via.net!r} uses {used or 'no layer'} there, nothing to transit")
    r, why = radius, ""
    while r <= grow_to + 1e-9:
        vx, vy = via.at
        region = (vx - r, vy - r, vx + r, vy + r)
        ports, why = _edge_crossings(bg, via.net, region)
        if ports:
            break
        r += 1.0
    else:
        raise ValueError(f"via at {via.at} net {via.net!r}: no region up to +-{grow_to} mm has it "
                         f"crossing the edge twice on straight tracks ({why})")
    geo = eb.extract(pcb, region)
    rets = [[round(v.at[0] - vx, 4), round(v.at[1] - vy, 4)] for v in bg.vias_of()
            if v.net != via.net and box(*region).contains(v.poly)]
    geo["via"] = {"x": vx, "y": vy, "drill": via.drill, "returns": rets, "net": via.net,
                  "used": used, "top": via.layers[0], "bot": via.layers[-1],
                  "in": used[0], "out": used[-1]}
    return geo, ports


def _edge_crossings(bg, net: str, region) -> tuple[list[eb.Port], str]:
    x0, y0, x1, y1 = region
    found = []
    for t in bg.tracks_of(net):
        coords = list(t.shape.coords)
        (ax, ay), (bx, by) = coords[0], coords[-1]
        for fixed, lo_, hi_, va, vb, ua, ub, dirs, axis in (
                (x0, y0, y1, ax, bx, ay, by, "-x", 0), (x1, y0, y1, ax, bx, ay, by, "+x", 0),
                (y0, x0, x1, ay, by, ax, bx, "-y", 1), (y1, x0, x1, ay, by, ax, bx, "+y", 1)):
            if (va - fixed) * (vb - fixed) >= 0 or abs(vb - va) < 1e-9:
                continue
            u = ua + (ub - ua) * (fixed - va) / (vb - va)
            if not lo_ < u < hi_:
                continue
            if abs(ub - ua) > 1e-3:
                return [], f"{t.layer} track crosses {dirs} at an angle"
            px, py = (fixed, u) if axis == 0 else (u, fixed)
            found.append(eb.Port(round(px, 4), round(py, 4), dirs, t.layer, t.width))
    if len(found) != 2:
        return [], f"{len(found)} crossing(s)"
    if found[0].dir == found[1].dir:
        return [], "both crossings on one side"
    return found, ""


# ================================================================ mesh

def mesh_hook(geo: dict, zone: float, zstep: float, feed_len: float, then=None, refine: float = 1.0):
    """The `extra` for emboard.deck: see the module's docstring."""
    via = geo["via"]
    drills = geo.get("drills", [])

    def hook(csx, ctx):
        fine = ctx.fine
        for axis, c in (("x", via["x"]), ("y", via["y"])):
            a, b = c - zone, c + zone
            ctx.fixed[axis] = [v for v in ctx.fixed[axis] if not a < v < b]
            ctx.zones[axis].append((a, b))
            for d in drills:
                half = d["d"] * math.sqrt(math.pi) / 4
                ctx.fixed[axis] += ([d[axis] - half, d[axis] + half] if 2 * half > fine else [d[axis]])
        for z0, z1, _ in ctx.stack.slabs:
            n = max(1, math.ceil((z1 - z0) * refine / zstep))
            ctx.fixed["z"] += [z0 + (z1 - z0) * i / n for i in range(n + 1)]
        plane_stitch(csx, ctx, feed_len, refine)
        if then:
            then(csx, ctx)
    return hook


def plane_stitch(csx, ctx, feed_len: float, refine: float = 1.0) -> int:
    """Posts between the planes along each feed where a line has planes on both
    sides. Pinned on the feed's own lines, at the edge of its plane copper."""
    n = 0
    for k, p in enumerate(ctx.ports):
        zs = ctx.stack.z[p.layer]
        others = [l for l in ctx.stack.layers if l != p.layer]
        above = [l for l in others if ctx.stack.z[l] > zs]
        below = [l for l in others if ctx.stack.z[l] < zs]
        if not above or not below:
            continue
        a, b = above[-1], below[0]
        dfeed = min(ctx.coarse, ctx.lam_min / 30 / refine, feed_len / 24 / refine)     # deck()'s
        off = p.hi - p.lo + 4 * max(ctx.stack.gap(p.layer, a), ctx.stack.gap(p.layer, b))
        off = round(off, 6)
        name = f"feed_posts{k}"
        csx.metal(name)
        step = max(1, int(max(ctx.lam_min / 12, 0.5) / dfeed))
        j = 0
        while j * dfeed * step <= feed_len + 1e-9:
            along = p.plane + p.sign * j * dfeed * step
            for side in (-1, 1):
                acr = p.across + side * off
                ctx.pinned["xy"[1 - p.axis]].append(acr)
                xy = (along, acr) if p.axis == 0 else (acr, along)
                csx.box(name, (xy[0], xy[1], ctx.stack.z[b]), (xy[0], xy[1], ctx.stack.z[a]), prio=20)
                n += 1
            j += 1
    return n


def mesh_stats(csx, fine: float | None = None) -> dict:
    """Cells, the smallest cell per axis and the Courant dt of a built deck.
    Refuses a deck whose smallest cell has collapsed under fine/4."""
    lines = {a: sorted(csx.lines[a]) for a in "xyz"}
    least = {a: min(q - p for p, q in zip(lines[a], lines[a][1:])) for a in "xyz"}
    cells = len(lines["x"]) * len(lines["y"]) * len(lines["z"])
    dt = 1e-3 / (C0 * math.sqrt(sum(1 / v ** 2 for v in least.values())))
    out = {"cells": cells, "mesh": [len(lines[a]) for a in "xyz"],
           "min_cell_mm": {a: round(v, 5) for a, v in least.items()}, "dt_courant_s": dt}
    if fine and min(least["x"], least["y"]) < fine / 4:
        raise ValueError(f"timestep collapse: smallest xy cell {min(least['x'], least['y']):.4f} mm "
                         f"at fine {fine:.4f} (dt {dt:.3g} s); a pinned line sits too close to another")
    return out


def _dt_from_log(text: str) -> float | None:
    m = re.search(r"[Tt]imestep[^0-9]*([0-9.]+e[-+]?[0-9]+)", text)
    return float(m.group(1)) if m else None


# ================================================================ run

def _deck_defaults(geo, ports, f_max, fine, zstep) -> dict:
    """Mesh numbers for a via deck from its smallest feature. `fine` None: the
    cell that puts about 3 cells across the drill (with deck()'s line limits)."""
    st = eb.Stack.from_dict(geo["stack"])
    lam = C0 / f_max * 1e3 / math.sqrt(st.er_max())
    via = geo["via"]
    if fine is None:
        fine = min(via["drill"] / 3, lam / 40, min(p.w for p in ports) / 5)
    zstep = zstep or min(lam / 30, max(fine, 0.05))
    far = max([math.hypot(*r) for r in via.get("returns", []) if math.hypot(*r) < 3.0] or [0.0])
    zone = max(far + 2 * fine, via.get("antipad", 2 * via["drill"]) / 2 + 3 * fine)
    h = min(st.gap(p.layer, _nearest(st, p.layer)) for p in ports)
    feed = max(lam / 2, 12 * h)
    return {"fine": fine, "zstep": zstep, "zone": zone, "feed_len": feed, "pad": min(lam / 8, 2.0),
            "zcells": max(4, math.ceil(h / zstep))}


def _nearest(st, layer: str) -> str:
    return min((l for l in st.layers if l != layer), key=lambda l: st.gap(l, layer))


def build(geo, ports, f_max: float, excite: int = 0, *, fine=None, zstep=None, then=None, refine: float = 1.0):
    """(Csx, where, stats) for one excited port. refine divides every cell
    (emboard.deck's knob); `fine` sets the cell it divides."""
    d = _deck_defaults(geo, ports, f_max, fine, zstep)
    hook = mesh_hook(geo, d["zone"], d["zstep"], d["feed_len"], then, refine)
    csx, where = eb.deck(geo, ports, f_max, excite, fine=d["fine"], feed_len=d["feed_len"], pad=d["pad"],
                         floor="open", zcells=d["zcells"], extra=hook, refine=refine)
    stats = mesh_stats(csx, d["fine"] / refine)
    stats.update(fine=round(d["fine"] / refine, 5), refine=refine, zstep=round(d["zstep"] / refine, 5),
                 zone=round(d["zone"], 4))
    return csx, where, stats


def _run_to_tail(csx, where, work: Path, timeout: float, threads: int) -> dict:
    """openEMS until the probes have rung down: emboard.study's loop, plus the
    log's dt. A run that hits STEPS_MAX still ringing is marked, not hidden."""
    from . import solvers
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    while True:
        (work / "deck.xml").write_text(csx.xml())
        r = solvers.run("openems", ["deck.xml", f"--numThreads={threads}"], cwd=work, timeout=timeout)
        if not (work / "p0u0").exists():
            raise solvers.SolverError(f"openEMS wrote no probes: {(r.stdout + r.stderr)[-400:]}")
        tail = eb.record_tail(work, where)
        steps = csx.fdtd["NumberOfTimesteps"]
        if tail <= eb.TAIL_MAX or steps >= eb.STEPS_MAX:
            break
        csx.fdtd["NumberOfTimesteps"] = min(eb.STEPS_MAX, 2 * steps)
    return {"steps": steps, "tail": tail, "rang_down": tail <= eb.TAIL_MAX,
            "seconds": round(time.time() - t0, 1), "dt_log_s": _dt_from_log(r.stdout + r.stderr)}


def freq_grid(f_max: float, f_min: float | None, points: int) -> list[float]:
    f_min = f_min or f_max / 20
    return [f_min + (f_max - f_min) * i / (points - 1) for i in range(points)]


def _waves_from(vi, n_f, n_p):
    return [[((vi[k][m]["v"] + eb.ZREF * vi[k][m]["i"]) / 2, (vi[k][m]["v"] - eb.ZREF * vi[k][m]["i"]) / 2)
             for k in range(n_p)] for m in range(n_f)]


def _solve_feeds(reads: dict, where: dict, freqs, f_max: float, shift=None):
    """emboard's fitted feeds (fit_feed, feed_vi: one line per port over the
    band and all its runs) -> {run: waves}, and with shift=[mm per port] the
    waves at planes that far further in. Also each feed's Z and gamma."""
    n = len(where["ports"])
    keys = list(reads)
    lines = [eb.fit_feed([reads[e][k] for e in keys], where["ports"][k]["dist"], freqs, f_max) for k in range(n)]
    runs, runs_c = {}, {}
    vi0 = None
    for e in keys:
        vi = [eb.feed_vi(reads[e][k], where["ports"][k]["dist"], freqs, lines[k]) for k in range(n)]
        vi0 = vi0 or vi
        runs[e] = _waves_from(vi, len(freqs), n)
        if shift:
            vs = [eb.feed_vi(reads[e][k], [d + shift[k] for d in where["ports"][k]["dist"]], freqs, lines[k])
                  for k in range(n)]
            runs_c[e] = _waves_from(vs, len(freqs), n)
    return runs, runs_c, vi0, lines


def study(geo, ports, f_max: float, f_min=None, points: int = 201, *, fine=None, zstep=None, refine: float = 1.0,
          symmetric=False, shift=None, keep=None, timeout: float = 3600, threads: int = 4) -> dict:
    """S at the port planes (and, with shift=[mm, mm], at planes moved that
    far in), cells, dt, each feed's Z and gamma, each run's ring-down."""
    import tempfile
    freqs = freq_grid(f_max, f_min, points)
    excites = [0] if symmetric else [0, 1]
    reads, info, where, stats = {}, [], None, None
    for e in excites:
        csx, where, stats = build(geo, ports, f_max, e, fine=fine, zstep=zstep, refine=refine)
        ctx = tempfile.TemporaryDirectory(prefix="hwde-emvia-") if not keep else None
        work = Path(keep) / f"port{e}" if keep else Path(ctx.name)
        rec = _run_to_tail(csx, where, work, timeout, threads)
        reads[e] = [eb.read_feed(work, k, freqs) for k in range(2)]
        info.append({"excite": e, **rec})
        if ctx:
            ctx.cleanup()
    runs, runs_c, vi, lines = _solve_feeds(reads, where, freqs, f_max, shift)
    return {"freqs": freqs, "S": eb.sparams(runs, 2, symmetric), "ports": where["ports"], "runs": info,
            "stats": stats, "region": where["region"], "fine": where["fine"],
            "S_shifted": eb.sparams(runs_c, 2, symmetric) if shift else None,
            "feeds": [{"z_rms": round(ln["z_rms"], 3), "eps_rms": round(ln["eps_rms"], 4)} for ln in lines],
            "z_feed": [[complex(r["z"]) for r in f] for f in vi],
            "gamma_feed": [[complex(r["gamma"]) for r in f] for f in vi]}


def refined(run, f_max: float, step: float = eb.REFINE_STEP, refine: float = 1.0) -> dict:
    """run(refine) -> a study's dict; once at `refine`, once a mesh step finer.
    The coarser run with `refine` = {emboard.compare's ds, notch_pct, ok,
    cells, dt}, ok against emboard.CONV_TOL. A run costs about step^4 of the last."""
    a = run(refine)
    b = run(refine * step)
    c = eb.compare(a["freqs"], a["S"], b["S"], f_max / 8)
    a["refine"] = {"ds": c["ds"], "ds_at": c["ds_at"], "notch_pct": c["notch_pct"], "step": step,
                   "converged": c["ds"] <= eb.CONV_TOL["ds"] and c["notch_pct"] <= eb.CONV_TOL["notch_pct"],
                   "cells": b["stats"]["cells"], "dt_courant_s": b["stats"]["dt_courant_s"],
                   "notch_ghz": None if not find_notch(b["freqs"], b["S"]) else round(find_notch(b["freqs"], b["S"])["f_hz"] / 1e9, 3)}
    return a


def stub_info(geo: dict) -> dict:
    """The longer open stub of a via, from its barrel and the layers it uses,
    in emboard's zero-thickness stack, and its closed-form notch."""
    via = geo["via"]
    st = eb.Stack.from_dict(geo["stack"])
    above = st.z[via["top"]] - st.z[via["in"]]
    below = st.z[via["out"]] - st.z[via["bot"]]
    end, exit_, length = (via["top"], via["in"], above) if above >= below else (via["bot"], via["out"], below)
    if length <= 1e-9:
        return {"length_mm": 0.0, "closed_ghz": None}
    er = st.er_between(exit_, end)
    return {"length_mm": round(length, 4), "er": round(er, 4), "from": exit_, "to": end,
            "closed_ghz": round(stub_notch_ghz(length, er), 3)}


def report(res: dict, geo: dict, knee_ghz: float | None = None, frac: float = RETURN_FRAC) -> dict:
    """The readable half of a study: notch vs closed form (their ratio is the
    pads' loading factor, #599), L and C, TDR, the rule's findings."""
    freqs, S = res["freqs"], res["S"]
    stub = stub_info(geo)
    n = find_notch(freqs, S)
    out = {"stub": stub, "notch_ghz": None if n is None else round(n["f_hz"] / 1e9, 3),
           "notch_s21_db": None if n is None else n["s21_db"]}
    if n and stub["closed_ghz"]:
        out["loading"] = round(n["f_hz"] / 1e9 / stub["closed_ghz"], 3)
    if res.get("S_shifted"):
        out["lc"] = {k: None if v is None else round(v, 4)
                     for k, v in lc_from_s(freqs, res["S_shifted"], freqs[-1] / 4).items()}
    t = tdr(freqs, S)
    r_all = max(abs(p["dist"][1]) for p in res["ports"])
    out["tdr_rise_ps"] = round(t["rise_ps"], 1)
    out["tdr_z_ohm"] = z_window(t, 0.0, 2e12 * r_all * 1e-3 * math.sqrt(eb.Stack.from_dict(geo["stack"]).er_max()) / C0)
    via = geo["via"]
    returns = via.get("returns", [])
    er = stack_er(geo["stack"])
    out["return_nearest_mm"] = round(min((math.hypot(*r) for r in returns), default=float("nan")), 3) if returns else None
    out["findings"] = judge(out["notch_ghz"], knee_ghz, returns, None if knee_ghz is None else lambda_mm(knee_ghz, er), frac)
    out["judged"] = knee_ghz is not None
    return out


# ================================================================ readers

def s21_db(S) -> list[float]:
    return [20 * math.log10(max(abs(s[1][0]), 1e-12)) for s in S]


def find_notch(freqs, S, depth_db: float = NOTCH_DEPTH_DB) -> dict | None:
    """The deepest dip of |S21| that stands depth_db under what lies on both
    sides of it, at the parabola through its three points. None: no notch in
    the band (a stub's resonance above it, or no stub)."""
    db = s21_db(S)
    best = None
    for i in range(1, len(db) - 1):
        if db[i] <= db[i - 1] and db[i] <= db[i + 1]:
            left, right = max(db[:i]), max(db[i + 1:])
            if min(left, right) - db[i] >= depth_db and (best is None or db[i] < db[best]):
                best = i
    if best is None:
        return None
    a, b, c = db[best - 1], db[best], db[best + 1]
    den = a - 2 * b + c
    off = 0.5 * (a - c) / den if den else 0.0
    return {"f_hz": freqs[best] + off * (freqs[best + 1] - freqs[best]), "s21_db": round(b, 2)}


def lc_from_s(freqs, S, f_top: float, zref: float = eb.ZREF) -> dict:
    """A short transition's series L and shunt C from S referred to planes at
    its two ends: B = j w L, C = j w C in its ABCD, by least squares through
    the origin up to f_top. Z = sqrt(L/C), t = sqrt(L C)."""
    wL, wC, ww = 0.0, 0.0, 0.0
    for f, s in zip(freqs, S):
        if f > f_top:
            break
        w = 2 * math.pi * f
        _, B, C, _ = eb.abcd_from_s(s, zref)
        wL += w * B.imag
        wC += w * C.imag
        ww += w * w
    if not ww:
        raise ValueError(f"no frequency under {f_top}")
    L, Cc = wL / ww, wC / ww
    return {"L_nH": L * 1e9, "C_pF": Cc * 1e12, "z_ohm": math.sqrt(L / Cc) if L > 0 and Cc > 0 else None,
            "delay_ps": math.sqrt(L * Cc) * 1e12 if L > 0 and Cc > 0 else None}


def tdr(freqs, S, zref: float = eb.ZREF, nfft: int = 4096) -> dict:
    """Impedance seen from port 1 by a band-limited step (Hann over the band,
    DC filled from the lowest point; the band needs a uniform grid whose top
    sets the rise, about 0.5 / f_max). {t_ps, z, rise_ps}."""
    import numpy as np
    f = np.asarray(freqs)
    df = f[1] - f[0]
    if np.ptp(np.diff(f)) > df * 1e-6:
        raise ValueError("tdr needs a uniform frequency grid")
    s11 = np.array([complex(s[0][0]) for s in S])
    n0 = int(round(f[0] / df))
    full = np.zeros(n0 + len(f), dtype=complex)
    full[n0:] = s11
    if n0:
        full[:n0] = np.linspace(s11[0].real, s11[0], n0, endpoint=False)
    full *= np.hanning(2 * len(full))[len(full):]
    n = max(nfft, 2 * len(full))
    # the window's pulse is centred on t = 0, so half of it would wrap to the
    # record's end and cumsum would miss it (a 70 ohm section read 61): delay it
    t0 = 2.0 / f[-1]
    full = full * np.exp(-2j * np.pi * df * np.arange(len(full)) * t0)
    rho = np.cumsum(np.fft.irfft(full, n=n))
    dt = 1 / (n * df)
    # the reflection arrives inside half the record; the rest is the band's wrap
    keep = n // 2
    z = zref * (1 + rho[:keep]) / np.clip(1 - rho[:keep], 1e-6, None)
    return {"t_ps": ((np.arange(keep) * dt - t0) * 1e12).tolist(), "z": z.tolist(),
            "rise_ps": 0.5 / f[-1] * 1e12}


def z_window(t: dict, t0_ps: float, t1_ps: float) -> dict:
    zs = [z for tt, z in zip(t["t_ps"], t["z"]) if t0_ps <= tt <= t1_ps]
    return {"z_min": round(min(zs), 2), "z_max": round(max(zs), 2)}


# ================================================================ closed forms

def cage_z0(er: float, a: float, n: int, r_ring: float, r_rod: float) -> float:
    """Z0 of a rod radius a inside a cage of n rods radius r_rod on a circle
    r_ring (the coaxial via, #479): (60 / sqrt(er)) [ln(R / a) + ln(R / (n r)) / n],
    for a << R and n r << R (thin wires on a circle, by images)."""
    return 60 / math.sqrt(er) * (math.log(r_ring / a) + math.log(r_ring / (n * r_rod)) / n)


def cage_lc(er: float, length: float, a: float, n: int, r_ring: float, r_rod: float) -> dict:
    """L, C of a length of that cage as a lumped section."""
    z = cage_z0(er, a, n, r_ring, r_rod)
    td = length * 1e-3 * math.sqrt(er) / C0
    return {"L_nH": z * td * 1e9, "C_pF": td / z * 1e12, "z_ohm": z, "delay_ps": td * 1e12}


def via_c_johnson_graham(er: float, t_mm: float, pad_mm: float, antipad_mm: float) -> float:
    """Johnson & Graham's through-via C in pF: 1.41 er T D1 / (D2 - D1), inches."""
    return 1.41 * er * (t_mm / 25.4) * pad_mm / (antipad_mm - pad_mm)


def via_l_johnson_graham(h_mm: float, d_mm: float) -> float:
    """Johnson & Graham's via L in nH: 5.08 h (ln(4 h / d) + 1), h in inches.
    No return path in it: an order-of-magnitude figure."""
    return 5.08 * (h_mm / 25.4) * (math.log(4 * h_mm / d_mm) + 1)


def stub_notch_ghz(length_mm: float, er: float) -> float:
    """c / (4 L sqrt(er)), the figure check_via_stubs uses (lib/viastub.f_res_ghz)."""
    return C0 * 1e-6 / (4 * length_mm * math.sqrt(er))


# ================================================================ rule

def judge(notch_ghz: float | None, knee_ghz: float | None, returns, lam_mm: float | None,
          frac: float = RETURN_FRAC, near: float | None = None) -> list[dict]:
    """Findings on a via: a stub notch under NOTCH_KNEE x the net's top
    frequency; the nearest return via further than `frac` of a wavelength at
    that frequency. Nothing is judged without a frequency."""
    out = []
    if knee_ghz is None:
        return out
    if notch_ghz is not None and notch_ghz < NOTCH_KNEE * knee_ghz:
        out.append({"kind": "via_em_stub_notch", "severity": "error",
                    "message": f"EM stub notch {notch_ghz:.2f} GHz is under {NOTCH_KNEE:g} x the net's "
                               f"{knee_ghz:g} GHz"})
    if lam_mm is not None:
        dist = near if near is not None else min((math.hypot(*r) for r in returns), default=None)
        if dist is None:
            out.append({"kind": "via_no_return", "severity": "warning",
                        "message": "no return via in the region: the return current takes the plane pair's cavity"})
        elif dist > frac * lam_mm:
            out.append({"kind": "via_return_far", "severity": "warning",
                        "message": f"nearest return via {dist:.2f} mm is past {frac:g} wavelength "
                                   f"({frac * lam_mm:.2f} mm at {knee_ghz:g} GHz)"})
    return out


def lambda_mm(f_ghz: float, er: float) -> float:
    return C0 / (f_ghz * 1e9) * 1e3 / math.sqrt(er)


def stack_er(stack: dict) -> float:
    st = eb.Stack.from_dict(stack)
    return st.er_between(st.layers[0], st.layers[-1])



# ================================================================ edge launch

# The connector: BWSMA-KE-P001 as hw-lab vna-verify and zcoupon model it. The
# pin and the body are assumed, not from a drawing (vna-verify's KU-VV-1).
SMA_EDGE = {"er": 2.05, "r_o": 2.05, "pin_r": 0.5, "pin_len": 4.0, "body": 9.4, "flange": 3.25,
            "leg_h": 0.4, "leg_len": 5.1}


@dataclass
class LaunchSpec:
    """An edge SMA on a line: board edge at x = 0, board toward +x, the line's
    port at x = run. Planes from the edge in, on every layer but `layer`;
    F.Cu pour around the pad at `gap`, around the line at `line_gap`; the first
    plane cut `antipad` past the pad (and the second with cut_in2)."""
    stack: dict
    layer: str
    w: float
    pad_w: float
    pad_l: float
    pad_in: float               # pad centre from the edge
    gap: float
    antipad: float
    gnd_y: float                # the ground legs' pads, +- from the line
    line_gap: float = 1.5
    cut_in2: bool = False
    edge_cu: float = 0.3        # copper pulled back from the edge
    run: float = 10.0           # edge to the line's port plane
    half_y: float = 6.0
    via_drill: float = 0.3
    vias: list = field(default_factory=list)      # launch vias (x, y)
    sma: dict = field(default_factory=lambda: dict(SMA_EDGE))


def launch_vias(spec: LaunchSpec, back: float = 0.75) -> list[tuple[float, float]]:
    """zcoupon's pair: just past the inner end of each ground pad."""
    x = spec.pad_in + spec.pad_l / 2 + back
    return [(round(x, 4), round(y, 4)) for y in (-spec.gnd_y, spec.gnd_y)]


def launch_synth(spec: LaunchSpec, coax_out: float = 7.0) -> tuple[dict, eb.Port]:
    """(geo, the line's port). The region runs coax_out past the edge (no
    copper there) so the deck's domain holds the coax."""
    from shapely.geometry import box
    st = spec.stack
    layers = st["layers"]
    if spec.layer != layers[0]:
        raise ValueError("launch_synth: the launch sits on the top layer")
    i_ref = 1
    y1 = spec.half_y
    px0, px1 = spec.edge_cu, spec.pad_in + spec.pad_l / 2
    pad = box(px0, -spec.pad_w / 2, px1, spec.pad_w / 2)
    line = box(px1, -spec.w / 2, spec.run, spec.w / 2)
    copper = []

    def add(layer, net, g):
        for pts in eb._polys(g):
            copper.append({"layer": layer, "net": net, "pts": pts})
    add(spec.layer, "SIG", pad.union(line))
    hole = box(px0 - spec.gap - spec.edge_cu, -spec.pad_w / 2 - spec.gap, px1 + spec.gap, spec.pad_w / 2 + spec.gap)
    hole = hole.union(box(px1, -spec.w / 2 - spec.line_gap, spec.run + 1, spec.w / 2 + spec.line_gap))
    add(spec.layer, "GND", box(spec.edge_cu, -y1, spec.run, y1).difference(hole))
    cut = box(px0 - spec.antipad - 1, -spec.pad_w / 2 - spec.antipad, px1 + spec.antipad, spec.pad_w / 2 + spec.antipad)
    for k, layer in enumerate(layers[1:], start=1):
        g = box(0, -y1, spec.run, y1)
        if k == i_ref or (k == 2 and spec.cut_in2 and layer != layers[-1]):
            g = g.difference(cut)
        add(layer, "GND", g)
    vias = spec.vias or launch_vias(spec)
    drills = [{"x": v[0], "y": v[1], "d": spec.via_drill, "net": "GND", "top": layers[0], "bot": layers[-1]}
              for v in vias]
    geo = {"source": "synth", "region": [-coax_out, -y1, spec.run, y1], "stack": st, "copper": copper,
           "drills": drills, "lumped": []}
    return geo, eb.Port(spec.run, 0.0, "+x", spec.layer, spec.w)


def _coax_hook(spec: LaunchSpec, x_end: float, dx: float, fc: float, shorted: bool, x_dom: float, y_dom: float):
    sma = spec.sma
    top = max(spec.stack["z"].values())
    bot = min(spec.stack["z"].values())
    pin_r, r_o, fl = sma["pin_r"], sma["r_o"], sma["flange"]
    z_ax = top + pin_r
    r_i = r_o / math.exp(50.0 * math.sqrt(sma["er"]) / 60.0)

    def hook(csx, ctx):
        air = csx.material("outside", 1.0)
        csx.box(air, (x_dom, -y_dom, bot), (0.0, y_dom, top), prio=1)
        pec = csx.metal("sma")
        ptfe = csx.material("ptfe", sma["er"])
        csx.box(pec, (-0.3, -fl, bot), (0.0, fl, z_ax + fl), prio=60)
        csx.cylinder(ptfe, (-0.3, 0, z_ax), (0.0, 0, z_ax), r_o, prio=61)
        csx.cylinder(pec, (-0.3, 0, z_ax), (0.0, 0, z_ax), r_i, prio=62)
        for sy in (-1, 1):
            csx.box(pec, (0.0, sy * (spec.gnd_y - 0.6), top),
                    (sma["leg_len"], sy * (spec.gnd_y + 0.6), top + sma["leg_h"]), prio=60)
        csx.cylinder(pec, (0.0, 0, z_ax), (sma["pin_len"], 0, z_ax), pin_r, prio=62)
        # the solder under the pin: FDTD would join a tangent cylinder to the pad at one line, or not
        csx.box(pec, (0.0, -pin_r / 2, top), (sma["pin_len"], pin_r / 2, z_ax), prio=62)
        # the coax out of the flange, into the wall
        csx.cylinder(ptfe, (x_end, 0, z_ax), (-0.3, 0, z_ax), r_o, prio=61)
        csx.cylinder(pec, (x_end, 0, z_ax), (-0.3, 0, z_ax), r_i, prio=62)
        wall = r_o + 0.3
        csx.box(pec, (x_end, -wall, z_ax - wall), (-0.3, -r_o, z_ax + wall), prio=60)
        csx.box(pec, (x_end, r_o, z_ax - wall), (-0.3, wall, z_ax + wall), prio=60)
        csx.box(pec, (x_end, -wall, z_ax - wall), (-0.3, wall, z_ax - r_o), prio=60)
        csx.box(pec, (x_end, -wall, z_ax + r_o), (-0.3, wall, z_ax + wall), prio=60)
        # cut the square wall's corners back to round: PTFE over PEC inside r_o is above
        if shorted:
            csx.box(pec, (x_dom, -wall, z_ax - wall), (x_end, wall, z_ax + wall), prio=70)
        # mesh: uniform across the coax, pinned where the probes read
        n = int(round(abs(x_end) / dx))
        ctx.pinned["x"] += [x_end + i * dx for i in range(n + 1)]
        ctx.pinned["x"] += [-0.3, 0.0, sma["pin_len"]]
        half = wall + fc
        m = int(math.ceil(half / fc))
        ctx.pinned["y"] += [0.0]
        ctx.fixed["y"] += [i * fc for i in range(-m, m + 1)] + [r_i, -r_i, r_o, -r_o, pin_r, -pin_r, fl, -fl]
        ctx.fixed["y"] += [sy * (spec.gnd_y + d) for sy in (-1, 1) for d in (-0.6, 0.6)]
        ctx.pinned["z"] += [z_ax]
        ctx.fixed["z"] += [z_ax + i * fc for i in range(-m, m + 1)] + [z_ax + fl, top + sma["leg_h"]]
        for v in (top, bot):
            ctx.pinned["z"].append(v)
    return hook, z_ax, r_i


def _coax_probes(csx, where: dict, spec: LaunchSpec, xs: list[float], z_ax: float, r_i: float, x_face: float) -> None:
    """Probes named as a feed's (p1u*, p1i*), so emboard.port_vi reads them."""
    r_o = spec.sma["r_o"]
    ys = sorted(csx.lines["y"])
    zs = sorted(csx.lines["z"])
    for j, x in enumerate(xs):
        csx.probe(f"p1u{j}", 0, -1.0)
        csx.box(f"p1u{j}", (x, r_i, z_ax), (x, r_o, z_ax))
    q = (r_i + r_o) / 2

    def dual(lines, v):
        i = min(range(len(lines) - 1), key=lambda k: abs((lines[k] + lines[k + 1]) / 2 - v))
        return (lines[i] + lines[i + 1]) / 2
    ya, yb = dual(ys, -q), dual(ys, q)
    za, zb = dual(zs, z_ax - q), dual(zs, z_ax + q)
    for j in range(2):
        x = (xs[j] + xs[j + 1]) / 2
        csx.probe(f"p1i{j}", 1)
        csx.box(f"p1i{j}", (x, ya, za), (x, yb, zb))
    where["ports"].append({"x": x_face, "y": 0.0, "dir": "-x", "layer": "coax", "ref": "coax", "net": "SIG",
                           "w": 2 * r_i, "dist": [abs(x - x_face) for x in xs], "h": r_o - r_i, "er": spec.sma["er"]})


def launch_build(spec: LaunchSpec, f_max: float, *, shorted: bool = False, fine=None, zstep=None,
                 dx: float | None = None, fc: float | None = None, refine: float = 1.0):
    """(Csx, where, stats). shorted: the coax's far end is a PEC plate."""
    geo, port = launch_synth(spec)
    st = eb.Stack.from_dict(spec.stack)
    lam = C0 / f_max * 1e3 / math.sqrt(st.er_max())
    h = st.gap(spec.layer, _nearest(st, spec.layer))
    fine = fine or min(spec.w / 5, h / 2.5, lam / 40)
    zstep = (zstep or max(fine, h / 4)) / refine
    fc = (fc or max(fine, 0.1)) / refine
    dx = (dx or min(0.2, lam / 30)) / refine
    x_end = geo["region"][0]
    pad = 1.0
    feed_len = max(lam / 2, 12 * h)
    z_top = max(spec.stack["z"].values())
    air = spec.sma["pin_r"] + spec.sma["flange"] + 1.5
    hook, z_ax, r_i = _coax_hook(spec, x_end - pad, dx, fc, shorted, x_end - pad, spec.half_y + pad)

    def extra(csx, ctx):
        for z0, z1, _ in ctx.stack.slabs:
            k = max(1, math.ceil((z1 - z0) / zstep))
            ctx.fixed["z"] += [z0 + (z1 - z0) * i / k for i in range(k + 1)]
        hook(csx, ctx)
    csx, where = eb.deck(geo, [port], f_max, 0, fine=fine, feed_len=feed_len, pad=pad, floor="pec",
                         zcells=max(4, math.ceil(h * refine / (zstep * refine))), air=air, extra=extra, refine=refine)
    # three cells apart, an odd count, so the current probes fall on dual lines
    x_mid = -(spec.sma["flange"] + 1.0)
    i_mid = round((x_mid - (x_end - pad)) / dx)
    x_mid = x_end - pad + i_mid * dx
    xs = [x_mid + 3 * dx, x_mid, x_mid - 3 * dx]
    _coax_probes(csx, where, spec, xs, z_ax, r_i, -spec.sma["body"])
    stats = mesh_stats(csx, fine / refine)
    stats.update(fine=round(fine / refine, 5), refine=refine, zstep=round(zstep, 5), z_top=z_top, x_end=x_end - pad)
    return csx, where, stats


def launch_study(spec: LaunchSpec, f_max: float, f_min=None, points: int = 201, *, fine=None, zstep=None,
                 refine: float = 1.0, keep=None, timeout: float = 3600, threads: int = 4) -> dict:
    """S referred to the line's plane (port 1 here is index 0) and the jack's
    mating face (index 1), from the coax absorbing and then shorted."""
    import tempfile
    freqs = freq_grid(f_max, f_min, points)
    reads, info, where, stats = {}, [], None, None
    for e, shorted in enumerate((False, True)):
        csx, where, stats = launch_build(spec, f_max, shorted=shorted, fine=fine, zstep=zstep, refine=refine)
        ctx = tempfile.TemporaryDirectory(prefix="hwde-emlaunch-") if not keep else None
        work = Path(keep) / ("short" if shorted else "match") if keep else Path(ctx.name)
        rec = _run_to_tail(csx, where, work, timeout, threads)
        reads[e] = [eb.read_feed(work, k, freqs) for k in range(2)]
        info.append({"coax_end": "short" if shorted else "absorbing", **rec})
        if ctx:
            ctx.cleanup()
    runs, _, vi, lines = _solve_feeds(reads, where, freqs, f_max)
    return {"freqs": freqs, "S": eb.sparams(runs, 2), "ports": where["ports"], "runs": info,
            "stats": stats, "fine": where["fine"], "z_coax": [complex(r["z"]) for r in vi[1]],
            "feeds": [{"z_rms": round(ln["z_rms"], 3), "eps_rms": round(ln["eps_rms"], 4)} for ln in lines]}


def cascade(s_a, s_b, zref: float = eb.ZREF):
    """S of a followed by b, one frequency, through their ABCD."""
    import numpy as np
    A = np.array(eb.abcd_from_s(s_a, zref)).reshape(2, 2)
    B = np.array(eb.abcd_from_s(s_b, zref)).reshape(2, 2)
    (a, b), (c, d) = (A @ B).tolist()
    den = a + b / zref + c * zref + d
    s11 = (a + b / zref - c * zref - d) / den
    s21 = 2 / den
    s12 = 2 * (a * d - b * c) / den
    s22 = (-a + b / zref - c * zref + d) / den
    return [[s11, s12], [s21, s22]]


def flip(s):
    return [[s[1][1], s[1][0]], [s[0][1], s[0][0]]]


# ================================================================ Palace cross-check

def palace_geo(spec: ViaSpec, fine: float = 0.15, coarse: float = 1.5, air: float = 2.0) -> str:
    """A gmsh .geo for the same via as `synth` draws: every copper layer a
    zero-thickness internal sheet, barrels holes in the dielectric, a lumped
    port on each end face between the line and its reference plane.
    Physical volumes 1.. per slab, 100 air; surfaces 1 PEC, 2 outer, 3 end faces, 11, 12 the ports."""
    st = spec.stack
    layers = st["layers"]
    z = st["z"]
    top = spec.barrel_top or layers[0]
    bot = spec.barrel_bot or layers[-1]
    ret_top, ret_bot = spec.ret_top or layers[0], spec.ret_bot or layers[-1]
    x0, x1, y0, y1 = -spec.run, spec.run, -spec.half_y, spec.half_y
    zlo, zhi = z[layers[-1]] - air, z[layers[0]] + air
    eps = 1e-5
    out = ['SetFactory("OpenCASCADE");', "Mesh.MshFileVersion = 2.2;", "Mesh.ElementOrder = 1;",
           "General.Terminal = 0;", "Geometry.Tolerance = 1e-6;"]
    tag = 0

    def nv():
        nonlocal tag
        tag += 1
        return tag
    # volumes: slabs, air, less the barrels
    vols = []
    for i, (a, b, _) in enumerate(st["slabs"]):
        v = nv()
        out.append(f"Box({v}) = {{{x0},{y0},{a},{x1 - x0},{y1 - y0},{b - a}}};")
        vols.append(v)
    a_lo, a_hi = nv(), nv()
    out.append(f"Box({a_lo}) = {{{x0},{y0},{zlo},{x1 - x0},{y1 - y0},{z[layers[-1]] - zlo}}};")
    out.append(f"Box({a_hi}) = {{{x0},{y0},{z[layers[0]]},{x1 - x0},{y1 - y0},{zhi - z[layers[0]]}}};")
    cyls = []

    def barrel(cx, cy, d, a, b):
        c = nv()
        za, zb = sorted((z[a], z[b]))
        out.append(f"Cylinder({c}) = {{{cx},{cy},{za},0,0,{zb - za},{d / 2}}};")
        cyls.append(c)
    barrel(0, 0, spec.drill, top, bot)
    for r in spec.returns:
        barrel(r[0], r[1], spec.ret_drill, ret_top, ret_bot)
    allv = vols + [a_lo, a_hi]
    out.append(f"BooleanDifference{{ Volume{{{','.join(map(str, allv))}}}; Delete; }}"
               f"{{ Volume{{{','.join(map(str, cyls))}}}; Delete; }}")
    # sheets: only the ones that aren't already faces of a volume matter to the
    # mesh, but every one is fragmented in so its outline is a set of edges
    sheets = []

    def surf(kind: str, args: str) -> int:
        s = nv()
        out.append(f"{kind}({s}) = {{{args}}};")
        sheets.append(s)
        return s

    def disc(cx, cy, zz, r, hole=0.0):
        """A round sheet; `hole` keeps it out of a barrel that runs through
        it (a sheet across the barrel's void overlaps its own mesh)."""
        s = surf("Disk", f"{cx},{cy},{zz},{r}")
        if not hole:
            return s
        h, d = nv(), nv()
        out.append(f"Disk({h}) = {{{cx},{cy},{zz},{hole}}};")
        out.append(f"BooleanDifference({d}) = {{ Surface{{{s}}}; Delete; }}{{ Surface{{{h}}}; Delete; }};")
        sheets[sheets.index(s)] = d
        return d

    def rect(xa, ya, zz, dx, dy):
        return surf("Rectangle", f"{xa},{ya},{zz},{dx},{dy}")
    ret_span = _layers_between(st, ret_top, ret_bot)

    def ret_discs(zz, layer):
        if layer in ret_span:
            for r in spec.returns:
                disc(r[0], r[1], zz, spec.ret_pad / 2)
    sig_span = _layers_between(st, top, bot)

    def through(layer):
        return spec.drill / 2 if layer in sig_span[1:-1] else 0.0
    inset = 0.8 * spec.pad / 2      # a line stops inside the pad, short of the barrel
    for layer in layers:
        zz = z[layer]
        if layer == spec.in_layer:
            rect(x0, -spec.w_in / 2, zz, -inset - x0, spec.w_in)
            disc(0, 0, zz, spec.pad / 2, through(layer))
            ret_discs(zz, layer)
        elif layer == spec.out_layer:
            rect(inset, -spec.w_out / 2, zz, x1 - inset, spec.w_out)
            disc(0, 0, zz, spec.pad / 2, through(layer))
            ret_discs(zz, layer)
        else:
            disc(0, 0, zz, spec.antipad / 2, through(layer))
            if layer in (top, bot) or spec.pad_all:
                disc(0, 0, zz, spec.pad / 2, through(layer))
    # the ports: on the end faces, line to reference
    st_o = eb.Stack.from_dict(st)
    p_faces = []
    for x, layer, w in ((x0, spec.in_layer, spec.w_in), (x1, spec.out_layer, spec.w_out)):
        ref = _nearest(st_o, layer)
        za, zb = sorted((z[layer], z[ref]))
        s = nv()
        out.append(f"Rectangle({s}) = {{{x},{-w / 2},{za},{zb - za},{w}}};")
        out.append(f"Rotate {{{{0,1,0}},{{{x},0,{za}}},-Pi/2}} {{ Surface{{{s}}}; }}")
        sheets.append(s)
        p_faces.append((x, -w / 2, w / 2, za, zb))
    out.append(f"BooleanFragments{{ Volume{{Volume{{:}}}}; Delete; }}{{ Surface{{{','.join(map(str, sheets))}}}; Delete; }}"
               .replace("Volume{Volume{:}}", "Volume{:}"))
    out.append("ReorientAll = 0;")

    def bb(name, xa, ya, za, xb, yb, zb):
        out.append(f"{name}() = Surface In BoundingBox {{{xa - eps},{ya - eps},{za - eps},{xb + eps},{yb + eps},{zb + eps}}};")
    # PEC: lines + pads, plane sheets less their antipad discs, barrels
    out.append("pec() = {};")
    for k, (layer, xa, xb, w) in enumerate(((spec.in_layer, x0, spec.pad / 2, spec.w_in),
                                            (spec.out_layer, -spec.pad / 2, x1, spec.w_out))):
        half = max(w, spec.pad) / 2
        bb(f"sig{k}", xa, -half, z[layer], xb, half, z[layer])
        out.append(f"pec() += sig{k}();")
    for i, layer in enumerate(layers):
        zz = z[layer]
        if layer in (spec.in_layer, spec.out_layer):
            continue
        bb(f"pl{i}", x0, y0, zz, x1, y1, zz)
        r = spec.antipad / 2
        bb(f"ho{i}", -r, -r, zz, r, r, zz)
        out.append(f"pl{i}() -= ho{i}();")
        out.append(f"pec() += pl{i}();")
        if layer in (top, bot) or spec.pad_all:
            bb(f"pd{i}", -spec.pad / 2, -spec.pad / 2, zz, spec.pad / 2, spec.pad / 2, zz)
            out.append(f"pec() += pd{i}();")
    rb = spec.drill / 2
    za, zb = sorted((z[top], z[bot]))
    bb("bar0", -rb, -rb, za, rb, rb, zb)
    out.append("pec() += bar0();")
    for j, r in enumerate(spec.returns):
        rr = spec.ret_drill / 2
        za, zb = sorted((z[ret_top], z[ret_bot]))
        bb(f"bar{j + 1}", r[0] - rr, r[1] - rr, za, r[0] + rr, r[1] + rr, zb)
        out.append(f"pec() += bar{j + 1}();")
    # the return pads on the signal layers
    pp = spec.ret_pad / 2
    for k, layer in enumerate((spec.in_layer, spec.out_layer)):
        if layer in ret_span:
            for j, r in enumerate(spec.returns):
                bb(f"rp{k}_{j}", r[0] - pp, r[1] - pp, z[layer], r[0] + pp, r[1] + pp, z[layer])
                out.append(f"pec() += rp{k}_{j}();")
    # ports, then outer faces
    for k, (x, ya, yb, za, zb) in enumerate(p_faces):
        bb(f"port{k}", x, ya, za, x, yb, zb)
    # An absorbing end face would shunt each lumped port with 377 ohm of the
    # line's own fringe (|S21| read -4 dB); a magnetic wall sends all the line's
    # current through the port.
    out.append("outer() = {};\nends() = {};")
    for name, box_, group in (("xlo", (x0, y0, zlo, x0, y1, zhi), "ends"), ("xhi", (x1, y0, zlo, x1, y1, zhi), "ends"),
                              ("ylo", (x0, y0, zlo, x1, y0, zhi), "outer"), ("yhi", (x0, y1, zlo, x1, y1, zhi), "outer"),
                              ("zlo", (x0, y0, zlo, x1, y1, zlo), "outer"), ("zhi", (x0, y0, zhi, x1, y1, zhi), "outer")):
        bb(name, *box_)
        out.append(f"{group}() += {name}();")
    out.append("ends() -= port0();\nends() -= port1();")
    out.append('Physical Surface("pec", 1) = {pec()};')
    out.append('Physical Surface("outer", 2) = {outer()};')
    out.append('Physical Surface("ends", 3) = {ends()};')
    out.append('Physical Surface("port0", 11) = {port0()};')
    out.append('Physical Surface("port1", 12) = {port1()};')
    for i, (a, b, _) in enumerate(st["slabs"]):
        out.append(f"s{i}() = Volume In BoundingBox {{{x0 - eps},{y0 - eps},{a - eps},{x1 + eps},{y1 + eps},{b + eps}}};")
        out.append(f'Physical Volume("slab{i}", {i + 1}) = {{s{i}()}};')
    out.append(f"al() = Volume In BoundingBox {{{x0 - eps},{y0 - eps},{zlo - eps},{x1 + eps},{y1 + eps},{z[layers[-1]] + eps}}};")
    out.append(f"ah() = Volume In BoundingBox {{{x0 - eps},{y0 - eps},{z[layers[0]] - eps},{x1 + eps},{y1 + eps},{zhi + eps}}};")
    out.append('Physical Volume("air", 100) = {al(), ah()};')
    far = max([math.hypot(*r) for r in spec.returns] or [spec.antipad / 2]) + fine
    out.append(f"Field[1] = Ball; Field[1].Radius = {far}; Field[1].Thickness = {2 * far}; "
               f"Field[1].VIn = {fine}; Field[1].VOut = {coarse};")
    w_min = min(spec.w_in, spec.w_out)
    out.append(f"Field[2] = Box; Field[2].VIn = {max(w_min / 2, fine / 2)}; Field[2].VOut = {coarse}; "
               f"Field[2].XMin = {x0}; Field[2].XMax = {x1}; Field[2].YMin = {-max(w_min, 0.5)}; "
               f"Field[2].YMax = {max(w_min, 0.5)}; Field[2].ZMin = {z[layers[-1]] - 0.3}; "
               f"Field[2].ZMax = {z[layers[0]] + 0.3}; Field[2].Thickness = 1.0;")
    out.append("Field[3] = Min; Field[3].FieldsList = {1, 2}; Background Field = 3;")
    out.append("Mesh.MeshSizeFromPoints = 0; Mesh.MeshSizeExtendFromBoundary = 0; Mesh.MeshSizeFromCurvature = 12;")
    return "\n".join(out) + "\n"


def palace_config(mesh: str, spec: ViaSpec, f_min_ghz: float, f_max_ghz: float, step_ghz: float,
                  order: int = 2, tol: float = 1e-6) -> dict:
    st = spec.stack
    z = st["z"]
    st_o = eb.Stack.from_dict(st)

    def direction(layer):
        return "+Z" if z[layer] > z[_nearest(st_o, layer)] else "-Z"
    mats = [{"Attributes": [i + 1], "Permittivity": float(s[2])} for i, s in enumerate(st["slabs"])]
    mats.append({"Attributes": [100], "Permittivity": 1.0})
    return {
        "Problem": {"Type": "Driven", "Verbose": 1, "Output": "postpro"},
        "Model": {"Mesh": mesh, "L0": 1e-3},
        "Domains": {"Materials": mats},
        "Boundaries": {
            "PEC": {"Attributes": [1]},
            "Absorbing": {"Attributes": [2], "Order": 1},
            "PMC": {"Attributes": [3]},
            "LumpedPort": [
                {"Index": 1, "Attributes": [11], "Direction": direction(spec.in_layer), "R": 50.0, "Excitation": True},
                {"Index": 2, "Attributes": [12], "Direction": direction(spec.out_layer), "R": 50.0}]},
        "Solver": {"Order": order,
                   "Driven": {"MinFreq": f_min_ghz, "MaxFreq": f_max_ghz, "FreqStep": step_ghz,
                              "SaveStep": 0, "AdaptiveTol": 1e-3},
                   "Linear": {"Type": "Default", "KSPType": "GMRES", "Tol": tol, "MaxIts": 200}},
    }


def palace_s(postpro: Path) -> dict:
    """{f_ghz, s11_db, s21_db, ...} from Palace's postpro/port-S.csv."""
    rows = Path(postpro, "port-S.csv").read_text().splitlines()
    head = [c.strip() for c in rows[0].split(",")]
    out: dict = {k: [] for k in ("f_ghz", "s11_db", "s21_db", "s21_deg")}
    idx = {}
    for i, h in enumerate(head):
        m = re.search(r"\|S\[(\d)\]\[(\d)\]\|", h)
        if m and "dB" in h:
            idx[f"s{m.group(1)}{m.group(2)}_db"] = i
        m = re.search(r"arg\(S\[(\d)\]\[(\d)\]\)", h)
        if m:
            idx[f"s{m.group(1)}{m.group(2)}_deg"] = i
    for line in rows[1:]:
        c = [float(v) for v in line.split(",") if v.strip()]
        if not c:
            continue
        out["f_ghz"].append(c[0])
        for k in ("s11_db", "s21_db", "s21_deg"):
            if k in idx:
                out[k].append(c[idx[k]])
    return out


def palace_run(spec: ViaSpec, work, f_min_ghz: float, f_max_ghz: float, step_ghz: float,
               fine: float = 0.15, coarse: float = 1.5, air: float = 2.0, order: int = 2,
               ranks: int = 4, timeout: float = 3600) -> dict:
    """gmsh + Palace driven on spec. {f_ghz, s11_db, s21_db, tets, seconds}."""
    import json
    from . import solvers
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    (work / "via.geo").write_text(palace_geo(spec, fine, coarse, air))
    t0 = time.time()
    r = solvers.run("gmsh", ["via.geo", "-3", "-o", "via.msh"], cwd=work, timeout=min(timeout, 1800))
    if not (work / "via.msh").exists():
        raise solvers.SolverError(f"gmsh: {(r.stdout + r.stderr)[-500:]}")
    txt = (work / "via.msh").read_text(errors="replace")
    (work / "via.json").write_text(json.dumps(palace_config("via.msh", spec, f_min_ghz, f_max_ghz, step_ghz, order), indent=1))
    r = solvers.run("palace", ["via.json"], cwd=work, timeout=timeout)
    if not (work / "postpro" / "port-S.csv").exists():
        raise solvers.SolverError(f"Palace: {(r.stdout + r.stderr)[-800:]}")
    out = palace_s(work / "postpro")
    out["seconds"] = round(time.time() - t0, 1)
    m = re.search(r"\$Elements\s+(\d+)", txt)
    out["elements"] = int(m.group(1)) if m else None
    return out
