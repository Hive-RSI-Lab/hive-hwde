"""empalace.py - the same board region and ports as lib/emboard.py, solved by
Palace (driven FEM), so a second solver checks the S-matrix (#616)."""
from __future__ import annotations

import cmath
import csv
import json
import math
import os
import time
from pathlib import Path

from . import emboard as eb
from . import solvers

MESH_SCRIPT = Path(__file__).with_name("empalace_mesh.py")
V_POINTS = 8            # Gauss points on a voltage column
GL = {2: (-0.5773502692, 0.5773502692), 4: (-0.8611363116, -0.3399810436, 0.3399810436, 0.8611363116)}
GLW = {2: (1.0, 1.0), 4: (0.3478548451, 0.6521451549, 0.6521451549, 0.3478548451)}


def _gauss(a, b, n=4):
    """(point, weight) along a -> b (3-vectors), n Gauss points."""
    d = [q - p for p, q in zip(a, b)]
    return [([p + u * (t + 1) / 2 for p, u in zip(a, d)], w / 2) for t, w in zip(GL[n], GLW[n])]


def _segments(a, b, step, n=4):
    L = math.dist(a, b)
    k = max(1, math.ceil(L / step))
    out = []
    for i in range(k):
        s = [p + (q - p) * i / k for p, q in zip(a, b)]
        e = [p + (q - p) * (i + 1) / k for p, q in zip(a, b)]
        out += _gauss(s, e, n)
    return out


def _unmerged(csx, name):
    for kind, attrs, inner, prims in csx.props:
        if attrs["Name"] == name:
            return prims
    return []


def _sheets(csx):
    """The deck's copper back as {z: shapely geometry}, its hole-free pieces
    rejoined: the deck cut them for CSXCAD, the mesh has no use for the cuts."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    per = {}
    for prim in _unmerged(csx, "copper"):
        if prim[0] == "poly":
            per.setdefault(prim[2], []).append(Polygon(prim[3]).buffer(0))
    return {z: unary_union(gs) for z, gs in per.items()}


def mesh_spec(geo, ports, f_max, *, feed_len=None, refine=1.0, **deck_kw) -> tuple[dict, dict, dict]:
    """(spec for empalace_mesh.py, emboard's `where`, what the probes need),
    from emboard.deck itself: the copper, feeds, stitching, barrels, lumped
    ends and domain are the deck's own primitives, so the two solvers can only
    differ in the solver."""
    from shapely.geometry import LineString, Point
    seen = {}
    csx, where = eb.deck(geo, ports, f_max, 0, feed_len=feed_len,
                         extra=lambda c, ctx: seen.update(ctx=ctx), **deck_kw)
    ctx = seen["ctx"]
    st = ctx.stack
    lz = sorted(csx.lines["z"])
    zbot, ztop = lz[0], lz[-1]
    _, a, b = _unmerged(csx, "slab0")[0]
    dom = [a[0], a[1], b[0], b[1]]
    zs = sorted(st.z.values())
    layers, er = [], []
    if zbot < zs[0] - 1e-9:
        layers.append([zbot, zs[0]])
        er.append(1.0)
    for z0, z1, e in sorted(st.slabs):
        layers.append([z0, z1])
        er.append(e)
    layers.append([zs[-1], ztop])
    er.append(1.0)
    sheets = []
    per_z = _sheets(csx)
    floor = where["floor"]
    pins = []
    probes = []
    pdata = []
    plist = []
    for k, (p, w) in enumerate(zip(ctx.ports, where["ports"])):
        zsig, zref = st.z[p.layer], st.z[p.ref]
        _, c, d = _unmerged(csx, f"port{k}")[0]
        rect = ([[c[0], c[1], c[2]], [c[0], d[1], c[2]], [c[0], d[1], d[2]], [c[0], c[1], d[2]]] if p.axis == 0
                else [[c[0], c[1], c[2]], [d[0], c[1], c[2]], [d[0], c[1], d[2]], [c[0], c[1], d[2]]])
        plist.append({"index": k + 1, "pts": rect, "dir": "+Z", "r": eb.ZREF, "excitable": True})
        pins.append((p, zsig))
        cu = per_z[zsig]
        at = lambda along, acr: (along, acr) if p.axis == 0 else (acr, along)
        qs = [p.plane + p.sign * q for q in w["dist"]]
        qi = [(qs[0] + qs[1]) / 2, (qs[1] + qs[2]) / 2]
        # how far the current loop can stand off: half the gap to the next
        # copper across and half the way to the next layer in z
        cut = LineString([at(qi[0], p.lo - 50), at(qi[0], p.hi + 50)]).intersection(cu)
        gaps = []
        for g in (cut.geoms if hasattr(cut, "geoms") else [cut]):
            if g.is_empty:
                continue
            bb = g.bounds
            lo, hi = (bb[1], bb[3]) if p.axis == 0 else (bb[0], bb[2])
            if hi < p.lo - 1e-6:
                gaps.append(p.lo - hi)
            elif lo > p.hi + 1e-6:
                gaps.append(lo - p.hi)
        others = [z for z in st.z.values() if abs(z - zsig) > 1e-9]
        up = min([z - zsig for z in others if z > zsig], default=abs(zsig - zref))
        dn = min([zsig - z for z in others if z < zsig], default=abs(zsig - zref))
        mx = min([abs(zsig - zref) / 2] + [g / 2 for g in gaps])
        v_pts, i_pts = [], []
        step_v = abs(zsig - zref) / 2
        for q in qs:
            xy = at(q, p.across)
            v_pts.append(_segments([*xy, zsig], [*xy, zref], step_v))
        for q in qi:
            corners = [[*at(q, p.lo - mx), zsig - dn / 2], [*at(q, p.hi + mx), zsig - dn / 2],
                       [*at(q, p.hi + mx), zsig + up / 2], [*at(q, p.lo - mx), zsig + up / 2]]
            loop = []
            step = min(p.w, abs(zsig - zref)) / 2
            for s, e in zip(corners, corners[1:] + corners[:1]):
                n = [v / max(math.dist(s, e), 1e-12) for v in (e[i] - s[i] for i in range(3))]
                loop += [(pt, wt * math.dist(s, e) / max(1, math.ceil(math.dist(s, e) / step)), n)
                         for pt, wt in _segments(s, e, step)]
            i_pts.append(loop)
        pdata.append({"v": v_pts, "i": i_pts, "zdir": 1.0 if zref > zsig else -1.0, "L": abs(zsig - zref),
                      "step": step_v})
    for z, g in sorted(per_z.items()):
        if floor == "pec" and abs(z - zbot) < 1e-9:
            continue
        for part in (g.geoms if hasattr(g, "geoms") else [g]):
            if part.geom_type != "Polygon" or part.area < 1e-6:
                continue
            sig = any(abs(zp - z) < 1e-9 and part.buffer(1e-6).contains(Point(
                (p.plane, p.across) if p.axis == 0 else (p.across, p.plane))) for p, zp in pins)
            sheets.append({"z": z, "signal": sig, "outer": [list(c) for c in part.exterior.coords[:-1]],
                           "holes": [[list(c) for c in h.coords[:-1]] for h in part.interiors]})
    posts = []
    for prim in _unmerged(csx, "barrels"):
        _, a_, b_ = prim
        posts.append([a_[0], a_[1], a_[2], b_[0], b_[1], b_[2]])
    for lp in geo.get("lumped", []):
        ax = 0 if lp["axis"] == "x" else 1
        z = st.z[lp["layer"]]
        lo, hi, c0, c1 = lp["lo"], lp["hi"], lp["c"] - lp["w"] / 2, lp["c"] + lp["w"] / 2
        rect = ([[lo, c0, z], [hi, c0, z], [hi, c1, z], [lo, c1, z]] if ax == 0
                else [[c0, lo, z], [c1, lo, z], [c1, hi, z], [c0, hi, z]])
        plist.append({"index": len(plist) + 1, "pts": rect, "dir": "+X" if ax == 0 else "+Y",
                      "r": lp["r"], "excitable": False})
    h = min(pw["h"] for pw in where["ports"])
    wmin = min(pw["w"] for pw in where["ports"])
    near = min(wmin / 4, h / 2) / refine
    size = {"near": near, "medium": 2 * near, "reach": max(6 * h, 2 * wmin),
            "far": ctx.lam_min / 5 / refine,
            "box": {"VIn": ctx.lam_min / 10 / refine, "VOut": ctx.lam_min / 5 / refine,
                    "XMin": dom[0], "XMax": dom[2], "YMin": dom[1], "YMax": dom[3],
                    "ZMin": zs[0], "ZMax": zs[-1], "Thickness": 0.0}}
    spec = {"dom": dom, "layers": layers, "er": er, "floor": floor, "sheets": sheets,
            "posts": posts, "ports": plist, "size": size}
    return spec, where, {"ports": pdata}


def probe_list(pinfo) -> tuple[list[dict], list]:
    """Palace's probe list, and for each port the (index, weight, axis) terms
    of its 3 voltages and 2 currents."""
    plist, terms = [], []

    def add(pt):
        plist.append({"Index": len(plist) + 1, "Center": [round(v, 7) for v in pt]})
        return len(plist)
    for pd in pinfo["ports"]:
        us = [[(add(pt), w * pd["step"] * pd["zdir"], 2) for pt, w in col] for col in pd["v"]]
        ii = []
        for loop in pd["i"]:
            terms_i = []
            for pt, w, n in loop:
                axis = max(range(3), key=lambda i: abs(n[i]))
                terms_i.append((add(pt), w * (1 if n[axis] > 0 else -1), axis))
            ii.append(terms_i)
        terms.append({"u": us, "i": ii})
    return plist, terms


def config(attrs, spec, plist, excite, freqs, order=2, tol=1e-3, solver_tol=1e-6, mesh="board.msh") -> dict:
    lp = []
    for q in spec["ports"]:
        e = {"Index": q["index"], "Attributes": [attrs["ports"][str(q["index"])]],
             "Direction": q["dir"], "R": q["r"]}
        if q["index"] == excite + 1:
            e["Excitation"] = True
        lp.append(e)
    return {"Problem": {"Type": "Driven", "Verbose": 2, "Output": "postpro"},
            "Model": {"Mesh": mesh, "L0": 1e-3},
            "Domains": {"Materials": [{"Attributes": [m["attr"]], "Permittivity": m["er"], "Permeability": 1.0}
                                      for m in attrs["materials"]],
                        "Postprocessing": {"Probe": plist}},
            "Boundaries": {"PEC": {"Attributes": attrs["pec"]},
                           "Absorbing": {"Attributes": attrs["absorbing"], "Order": 1},
                           "LumpedPort": lp},
            "Solver": {"Order": order,
                       "Driven": {"Samples": [{"Type": "Point", "Freq": [round(f / 1e9, 9) for f in freqs]}],
                                  "AdaptiveTol": tol, "SaveStep": 0},
                       "Linear": {"Type": "Default", "KSPType": "GMRES", "Tol": solver_tol, "MaxIts": 500}}}


# ================================================================ compare

# What "the two solvers agree" means. Both numbers are stated in the verdict.
# RMS_DS: the vector difference of S over the band, rms. It is what a designer
# would see as a different curve; -26 dB is about 0.45 dB and 3 degrees on a
# passing line. A notch sits on a steep slope, so a small shift makes a large
# difference at a few points: that is judged by the notch's own frequency.
RMS_DS = 0.05
NOTCH_PCT = 1.0
NOTCH_DEPTH_DB = -10.0      # shallower than this isn't a feature to compare


def _notch(freqs, col) -> dict | None:
    """The deepest interior dip of |S|, parabola through three dB points."""
    db = [20 * math.log10(max(abs(v), 1e-12)) for v in col]
    m = min(range(len(db)), key=db.__getitem__)
    if m == 0 or m == len(db) - 1 or db[m] > NOTCH_DEPTH_DB:
        return None
    y0, y1, y2 = db[m - 1], db[m], db[m + 1]
    den = y0 - 2 * y1 + y2
    d = 0.5 * (y0 - y2) / den if den else 0.0
    return {"f": freqs[m] + d * (freqs[m + 1] - freqs[m]), "db": y1}


def compare(freqs, a, b, *, rms_ds=RMS_DS, notch_pct=NOTCH_PCT, names=("openEMS", "Palace"), columns=None) -> dict:
    """A verdict on two S-matrices on one frequency list: per term, the rms
    and worst vector difference and any notch's shift. columns: the excited
    ports to judge (default all)."""
    n = len(a[0])
    cols = list(columns) if columns is not None else list(range(n))
    terms, why = {}, []
    for j in cols:
        for i in range(n):
            xs = [complex(s[i][j]) for s in a]
            ys = [complex(s[i][j]) for s in b]
            d = [abs(x - y) for x, y in zip(xs, ys)]
            rms = math.sqrt(sum(v * v for v in d) / len(d))
            m = max(range(len(d)), key=d.__getitem__)
            t = {"rms_ds": round(rms, 5), "max_ds": round(d[m], 5), "at_hz": freqs[m]}
            na, nb = _notch(freqs, xs), _notch(freqs, ys)
            name = f"S{i + 1}{j + 1}"
            if na and nb:
                shift = abs(na["f"] - nb["f"]) / na["f"] * 100
                t["notch"] = {names[0]: na, names[1]: nb, "shift_pct": round(shift, 3)}
                if shift > notch_pct:
                    why.append(f"{name} notch {na['f'] / 1e9:.4f} GHz ({names[0]}) against "
                               f"{nb['f'] / 1e9:.4f} GHz ({names[1]}): {shift:.2f}% over {notch_pct}%")
                # the slope near a notch isn't a disagreement; judge the rest
                band = (na["f"] + nb["f"]) / 2
                off = [v for f, v in zip(freqs, d) if abs(f - band) > 0.1 * band]
                rms = math.sqrt(sum(v * v for v in off) / len(off)) if off else rms
                t["rms_ds_off_notch"] = round(rms, 5)
            elif na or nb:
                who, hit = (names[0], na) if na else (names[1], nb)
                why.append(f"{name}: only {who} has a notch ({hit['f'] / 1e9:.4f} GHz, {hit['db']:.1f} dB)")
            if rms > rms_ds:
                why.append(f"{name} rms |dS| {rms:.3f} over {rms_ds}, worst {d[m]:.3f} at {freqs[m] / 1e9:.3f} GHz")
            terms[name] = t
    return {"ok": not why, "solvers": list(names), "terms": terms, "disagree": why,
            "limits": {"rms_ds": rms_ds, "notch_pct": notch_pct}}


# ================================================================ read and run

MU0 = 4e-7 * math.pi


def read_probes(path: Path) -> tuple[list[float], dict]:
    """(f in Hz, {(axis, probe index): complex per frequency}) from Palace's
    probe-E.csv or probe-B.csv. Columns are found by their headers."""
    import re
    rows = list(csv.reader(Path(path).open()))
    head = [h.strip() for h in rows[0]]
    data = [[float(v) for v in r] for r in rows[1:] if r]
    f = [r[0] * 1e9 for r in data]
    re_c, im_c = {}, {}
    for c, h in enumerate(head):
        m = re.match(r"(Re|Im)\{[EB]_([xyz])\[(\d+)\]\}", h)
        if m:
            (re_c if m.group(1) == "Re" else im_c)["xyz".index(m.group(2)), int(m.group(3))] = c
    return f, {k: [complex(r[re_c[k]], r[im_c[k]]) for r in data] for k in re_c}


def read_port_s(path: Path, excite: int, n: int) -> list[list[complex]]:
    """Palace's port-S.csv as [S(k, excite) per port k] per frequency."""
    import re
    rows = list(csv.reader(Path(path).open()))
    head = [h.strip() for h in rows[0]]
    cols = {}
    for c, h in enumerate(head):
        m = re.match(r"(\|S|arg\(S)\[(\d+)\]\[(\d+)\]", h)
        if m and int(m.group(3)) == excite + 1:
            cols.setdefault(int(m.group(2)) - 1, {})[m.group(1)[0]] = c
    out = []
    for r in rows[1:]:
        if not r:
            continue
        v = [float(q) for q in r]
        out.append([10 ** (v[cols[k]["|"]] / 20) * complex(math.cos(math.radians(v[cols[k]["a"]])),
                                                           math.sin(math.radians(v[cols[k]["a"]])))
                     for k in range(n)])
    return out


def feed_reads(pdir: Path, terms, freqs) -> list[tuple[list, list]]:
    """Each port's (three voltages, two currents) per frequency, the same shape
    as emboard.read_feed's: V from E up the column (strip to reference), I from
    H around the strip."""
    import numpy as np
    fe, E = read_probes(pdir / "probe-E.csv")
    fb, B = read_probes(pdir / "probe-B.csv")
    if len(fe) != len(freqs) or any(abs(a - b) > 1e3 for a, b in zip(fe, freqs)):
        raise solvers.SolverError(f"Palace wrote {len(fe)} frequencies, wanted {len(freqs)}")
    out = []
    for t in terms:
        us = [sum(w * 1e-3 * np.asarray(E[ax, i]) for i, w, ax in col) for col in t["u"]]
        ii = [sum(w * 1e-3 / MU0 * np.asarray(B[ax, i]) for i, w, ax in loop) for loop in t["i"]]
        out.append((us, ii))
    return out


def _run_palace(cfg_name, work, np_, timeout):
    import subprocess
    argv, env = solvers.mpi_command("palace", [cfg_name], np_)
    try:
        p = subprocess.run(argv, cwd=work, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise solvers.SolverError(f"Palace ran past {timeout:.0f} s") from e
    (Path(work) / "palace.log").write_text(p.stdout + p.stderr)
    if p.returncode:
        raise solvers.SolverError(f"Palace exit {p.returncode}: {(p.stdout + p.stderr)[-600:]}")
    return p


def mesh(spec, work, timeout=1200) -> dict:
    import subprocess
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    (work / "spec.json").write_text(json.dumps(spec))
    argv, env = solvers.python_command(MESH_SCRIPT, ["spec.json", "board.msh", "attrs.json"])
    try:
        p = subprocess.run(argv, cwd=work, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise solvers.SolverError(f"gmsh ran past {timeout:.0f} s") from e
    if p.returncode or not (work / "attrs.json").exists():
        raise solvers.SolverError(f"gmsh: {(p.stdout + p.stderr)[-600:]}")
    return json.loads((work / "attrs.json").read_text())


def study(geo, ports, f_max, f_min=None, points=201, symmetric=False, excites=None, keep=None,
          refine=1.0, order=2, np_=4, tol=1e-3, timeout=3600, feed_len=None, **deck_kw) -> dict:
    """extract'ed geometry -> gmsh -> Palace run(s) -> S at the deck's port
    planes, 50 ohm, through emboard's own feed fit, so `S` means what emboard's
    does. keep=DIR keeps the runs. `lumped_check` is the same S from Palace's
    own port-S.csv moved through the fitted lines: a check on the estimator
    both solvers share."""
    import tempfile
    n = len(ports)
    excites = excites if excites is not None else ([0] if (symmetric and n == 2) or n == 1 else list(range(n)))
    f_min = f_min or f_max / 20
    freqs = [f_min + (f_max - f_min) * i / (points - 1) for i in range(points)]
    tmp = tempfile.TemporaryDirectory(prefix="hwde-empalace-") if not keep else None
    work = Path(keep) if keep else Path(tmp.name)
    work.mkdir(parents=True, exist_ok=True)
    spec, where, pinfo = mesh_spec(geo, ports, f_max, feed_len=feed_len, refine=refine, **deck_kw)
    t0 = time.time()
    attrs = mesh(spec, work)
    plist, terms = probe_list(pinfo)
    reads, raw, info = {}, {}, []
    for e in excites:
        d = work / f"port{e}"
        d.mkdir(exist_ok=True)
        cfg = config(attrs, spec, plist, e, freqs, order=order, tol=tol, mesh="../board.msh")
        (d / "palace.json").write_text(json.dumps(cfg, indent=1))
        t1 = time.time()
        _run_palace("palace.json", d, np_, timeout)
        reads[e] = feed_reads(d / "postpro", terms, freqs)
        raw[e] = read_port_s(d / "postpro" / "port-S.csv", e, n)
        info.append({"excite": e, "tets": attrs["tets"], "order": order, "refine": refine,
                     "seconds": round(time.time() - t1, 1)})
    post = _post(reads, raw, spec, where, excites, freqs, f_max, n, symmetric)
    lines, S, lumped = post["lines"], post["S"], post["S_lumped"]
    mid = min(range(len(freqs)), key=lambda m: abs(freqs[m] - f_max / 2))
    if tmp:
        tmp.cleanup()
    return {"freqs": freqs, "S": S, "S_lumped": lumped, "ports": where["ports"], "runs": info,
            "region": where["region"], "floor": where["floor"], "seconds": round(time.time() - t0, 1),
            "z_feed": [round(ln["z"][mid], 2) for ln in lines],
            "eps_eff_feed": [round(ln["eps_eff"][mid], 3) for ln in lines],
            "feeds": [{"z_rms": round(ln["z_rms"], 3), "eps_rms": round(ln["eps_rms"], 4)} for ln in lines]}


def _post(reads, raw, spec, where, excites, freqs, f_max, n, symmetric) -> dict:
    """Palace's probes and port-S to S at the planes, both ways. No solve, so a
    kept run can be read again."""
    lines = eb.feed_lines(reads, [p["dist"] for p in where["ports"]], freqs, f_max, symmetric)
    vi = {e: [eb.feed_vi(reads[e][k], where["ports"][k]["dist"], freqs, lines[k]) for k in range(n)]
          for e in excites}
    wv = {e: [[((vi[e][k][m]["v"] + eb.ZREF * vi[e][k][m]["i"]) / 2,
                (vi[e][k][m]["v"] - eb.ZREF * vi[e][k][m]["i"]) / 2) for k in range(n)]
              for m in range(len(freqs))] for e in excites}
    end = [_port_end(spec, where, k) for k in range(n)]
    return {"lines": lines, "S": eb.sparams(wv, n, symmetric),
            "S_lumped": eb.sparams(_shifted_waves(raw, lines, end, freqs, n), n, symmetric)}


def _port_end(spec, where, k) -> float:
    """How far port k's lumped end stands from its plane (mm)."""
    p, q = where["ports"][k], spec["ports"][k]["pts"][0]
    return abs(q[0] - p["x"]) if p["dir"][1] == "x" else abs(q[1] - p["y"])


def _shifted_waves(raw, lines, end, freqs, n) -> dict:
    """The waves at the planes from Palace's lumped S at the feeds' ends: V and
    I there (a 1 W-free unit a on the driven port), carried along each fitted
    line, which is lossless, to the plane."""
    C0 = eb.C0
    out = {}
    for e, col in raw.items():
        rows = []
        for m, f in enumerate(freqs):
            per = []
            for k in range(n):
                a_e, b_e = (1.0 if k == e else 0.0), col[m][k]
                v = a_e + b_e
                i = (a_e - b_e) / eb.ZREF            # into the feed's end, toward the circuit
                z = lines[k]["z"][m]
                bl = 2 * math.pi * f * math.sqrt(lines[k]["eps_eff"][m]) / C0 * end[k] * 1e-3
                vp = v * cmath.cos(bl) - 1j * z * i * cmath.sin(bl)
                ip = i * cmath.cos(bl) - 1j * v / z * cmath.sin(bl)
                per.append(((vp + eb.ZREF * ip) / 2, (vp - eb.ZREF * ip) / 2))
            rows.append(per)
        out[e] = rows
    return out
