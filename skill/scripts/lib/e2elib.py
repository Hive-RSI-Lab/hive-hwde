"""e2elib - the E2E bench stage: one finished board workspace scored against
a held-out brief's hidden bounds (EEBench-style, extended to layout).

The contract is reference/e2e-scoring.md; this module implements it.  A
brief fixture is `brief.md` (the only thing the design agent sees) plus
`bounds.yaml` (hidden: never shown to the agent that builds the board).

Every check yields a score in [0, 1], or None when it cannot be scored on
this host/design (reported, excluded from its category mean).  Categories:
  electrical  required nets present, required parts present, ERC errors
              (live), SPICE at tolerance corners (live: needs ngspice)
  layout      board area, placement legality + signal crossings (+ decap
              distance when the workspace has a decoupling sidecar), DRC
              errors + routing completion (live)
  cost        BOM USD per board at the bounds' order quantity
composite (benchlib.WEIGHTS["E2E"]) = 100 - 45*(1-E) - 20*(1-L) - 35*(1-C):
cost keeps EEBench's 0.35 and electrical+layout keep its 0.65 technical.

SPICE decks are the BENCH's, never the workspace's own sims (those are the
agent grading itself): the resistor/capacitor network between the bound's
nets is read from the workspace netlist and replicated once per tolerance
corner (every +/- combination, exact for a resistive divider) in one deck.
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import yaml

import checklib
import simlib

CATEGORIES = ("electrical", "layout", "cost")
SPICE_KINDS = ("divider", "rc_lowpass")
MAX_CORNER_PARTS = 10          # 2**10 copies of a small network is still fast
_TOL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class E2EError(checklib.CheckError):
    pass


# ------------------------------------------------------------- bounds file

def load_bounds(path: Path) -> dict:
    """bounds.yaml -> dict, with the shape checked (a typo in a hidden bound
    must fail loudly, not score a board against nothing)."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise E2EError(f"bounds {path}: {exc}") from exc
    known = {"brief", "nets", "parts", "spice", "layout", "cost"}
    extra = sorted(set(data) - known)
    if extra:
        raise E2EError(f"bounds {path}: unknown keys {extra}")
    for n in data.get("nets") or []:
        if not {"id", "pattern"} <= set(n):
            raise E2EError(f"bounds {path}: a net needs id + pattern: {n}")
    ids = {n["id"] for n in data.get("nets") or []}
    for s in data.get("spice") or []:
        if s.get("kind") not in SPICE_KINDS:
            raise E2EError(f"bounds {path}: spice kind must be one of "
                           f"{SPICE_KINDS}: {s}")
        if s.get("min") is None and s.get("max") is None:
            raise E2EError(f"bounds {path}: spice check needs min/max: {s}")
        for role in ("top", "mid", "bottom", "in", "out", "gnd"):
            ref = s.get(role)
            if isinstance(ref, str) and ref not in ids:
                raise E2EError(f"bounds {path}: spice {role}={ref!r} is not "
                               f"a net id ({sorted(ids)})")
    # the three terms every brief must bound: without them a category with
    # no scored check would hand out its weight for free
    if not data.get("nets"):
        raise E2EError(f"bounds {path}: needs at least one net")
    if (data.get("layout") or {}).get("area_mm2_max") is None:
        raise E2EError(f"bounds {path}: needs layout.area_mm2_max")
    if not ((data.get("cost") or {}).get("target_usd") or 0) > 0:
        raise E2EError(f"bounds {path}: needs cost.target_usd > 0")
    return data


# ------------------------------------------------------------- workspace

def resolve_workspace(ws: Path) -> dict:
    """The artefacts a finished workspace holds (None where absent).  The
    board's own <dir>.* files win over any other KiCad file in kicad/."""
    ws = Path(ws)
    kicad = ws / "kicad"

    def pick(suffix):
        own = kicad / f"{ws.name}{suffix}"
        if own.is_file():
            return own
        found = sorted(kicad.glob(f"*{suffix}")) if kicad.is_dir() else []
        return found[0] if len(found) == 1 else None

    dec = kicad / "decoupling.json"
    parts = ws / "parts" / "parts.json"
    return {"netlist": pick(".net"), "pcb": pick(".kicad_pcb"),
            "sch": pick(".kicad_sch"),
            "decoupling": dec if dec.is_file() else None,
            "parts": parts if parts.is_file() else None}


def match_net(nets, pattern: str) -> tuple[str | None, list[str]]:
    """First (sorted) net whose name the pattern finds, and every match."""
    rx = re.compile(pattern)
    hits = sorted(n for n in nets if rx.search(n))
    return (hits[0] if hits else None), hits


def _check(cid, cat, score, **kw) -> dict:
    return {"id": cid, "category": cat,
            "score": None if score is None else checklib.rnd(score, 4), **kw}


def le_score(value: float, bound: float) -> float:
    """1 at or under the bound, falling linearly to 0 at twice it; a bound
    of 0 scores 1/(1+value) so a count still has a gradient."""
    if bound <= 0:
        return 1.0 / (1.0 + max(value, 0.0))
    return 1.0 if value <= bound else max(0.0, 2.0 - value / bound)


# ------------------------------------------------------------- electrical

def check_nets(bounds: dict, parsed: dict | None) -> tuple[list, dict]:
    out, netmap = [], {}
    for n in bounds.get("nets") or []:
        if parsed is None:
            out.append(_check(f"net:{n['id']}", "electrical", 0.0,
                              note="no netlist in the workspace"))
            continue
        name, hits = match_net(parsed["nets"], n["pattern"])
        netmap[n["id"]] = name
        out.append(_check(f"net:{n['id']}", "electrical",
                          1.0 if name else 0.0, value=name, matches=hits,
                          bound=n["pattern"]))
    return out, netmap


def check_parts(bounds: dict, parsed: dict | None) -> list:
    out = []
    for p in bounds.get("parts") or []:
        want = int(p.get("min_count", 1))
        if parsed is None:
            out.append(_check(f"part:{p['id']}", "electrical", 0.0,
                              note="no netlist in the workspace"))
            continue
        rx = re.compile(p["value_pattern"])
        refs = sorted(r for r, c in parsed["components"].items()
                      if rx.search(c.get("value") or ""))
        out.append(_check(f"part:{p['id']}", "electrical",
                          min(1.0, len(refs) / want) if want else 1.0,
                          value=refs, bound=f">= {want} x {p['value_pattern']}"))
    return out


def _two_pin(parsed: dict, prefix: str) -> list[tuple[str, str, str]]:
    """[(ref, net_a, net_b)] for every 2-pin part whose ref starts with
    prefix and whose pins are on two different nets."""
    out = []
    for ref, pins in sorted(simlib._ref_pins(parsed).items()):
        if not re.match(rf"^{prefix}\d", ref or "") or len(pins) != 2:
            continue
        a, b = (m["net"] for m in pins.values())
        if a != b:
            out.append((ref, a, b))
    return out


def _tol(value: str | None, default_pct: float) -> float:
    m = _TOL_RE.search(value or "")
    return (float(m.group(1)) if m else float(default_pct)) / 100.0


def _net_of(ref_or_id, netmap: dict, parsed: dict):
    if isinstance(ref_or_id, dict):
        return match_net(parsed["nets"], ref_or_id["pattern"])[0]
    return netmap.get(ref_or_id)


def _vref(spec: dict, parsed: dict) -> tuple[float | None, str]:
    """A number, or {by_value: {regex: volts}} matched against the design's
    part values (the reference is the chosen IC's, so a held-out brief
    lists the plausible ones).  None = unscoreable for this design."""
    v = spec.get("vref")
    if v is None or isinstance(v, (int, float)):
        return v, ""
    for rx, volts in (v.get("by_value") or {}).items():
        for ref, c in sorted(parsed["components"].items()):
            if re.search(rx, c.get("value") or ""):
                return float(volts), f"{ref} {c.get('value')}"
    return None, "no part matches vref.by_value"


def _r_reach(parsed, start, bottom, stop) -> tuple[set, list]:
    """Nets reachable from `start` through resistors only - never through
    `bottom`, and never into a stop net (another rail the bounds name; the
    caller leaves the check's own nets out of it) - and the resistors among
    them."""
    rs = [r for r in _two_pin(parsed, "R")
          if not ({r[1], r[2]} & stop)]
    seen, frontier = {start}, [start]
    while frontier:
        net = frontier.pop()
        if net == bottom:
            continue
        for _, a, b in rs:
            if net in (a, b):
                other = b if net == a else a
                if other not in seen:
                    seen.add(other)
                    frontier.append(other)
    return seen, [r for r in rs if r[1] in seen and r[2] in seen]


def build_divider(parsed, top, mid, bottom, stop, tol_pct) -> dict:
    """The resistor network from `top` to `bottom`, driven at 1 V on top:
    V(mid) in every tolerance corner."""
    seen, parts = _r_reach(parsed, top, bottom, stop)
    if mid not in seen or bottom not in seen:
        return {"error": f"no resistor path {top} -> {mid} -> {bottom}"}
    return _corner_deck(parsed, parts, {top: "TOP", bottom: "0"}, mid,
                        tol_pct, "V1 TOP 0 DC 1\n.tran 1u 2u\n",
                        "find v({node}) at=1u", "tran")


def build_rc(parsed, net_in, net_out, gnd, stop, tol_r_pct, tol_c_pct,
             thresholds=None) -> dict:
    """The resistor network from `in` (series R, divider legs) plus every C
    from a node of it to gnd.  Off-board parts are open circuit.  ngspice's
    .meas cannot subtract one measure from another, so the -3 dB point
    relative to the DC gain is two decks: without `thresholds` the deck
    measures each corner's low-frequency gain in dB, with them it finds
    where each corner falls 3.0103 dB below its own."""
    seen, rs = _r_reach(parsed, net_in, gnd, stop)
    cs = [c for c in _two_pin(parsed, "C")
          if gnd in c[1:] and ({c[1], c[2]} - {gnd}) <= seen - {net_in, gnd}]
    if net_out not in seen or not cs:
        return {"error": f"no R path {net_in} -> {net_out} with a shunt C "
                         f"to {gnd}"}
    tols = {r[0]: tol_r_pct for r in rs} | {c[0]: tol_c_pct for c in cs}
    meas = ("find vdb({node}) at=0.1" if thresholds is None
            else "when vdb({node})={thr} fall=1")
    return _corner_deck(parsed, rs + cs, {net_in: "IN", gnd: "0"}, net_out,
                        tols, "V1 IN 0 DC 0 AC 1\n.ac dec 200 0.1 1G\n",
                        meas, "ac", thresholds)


def _corner_deck(parsed, parts, fixed, probe, tol, source, meas, analysis,
                 thresholds=None):
    """One copy of `parts` per tolerance corner; shared nodes in `fixed`,
    every other node suffixed per copy.  Returns {cir, measures, parts}."""
    if len(parts) > MAX_CORNER_PARTS:
        return {"error": f"{len(parts)} parts > {MAX_CORNER_PARTS} "
                         "(corner count too large)"}
    comps = parsed["components"]
    names = simlib.rename_map({n for _, a, b in parts for n in (a, b)})
    table, lines = [], ["* hwde bench e2e corner deck", source.rstrip()]
    for ref, _, _ in parts:
        value = comps.get(ref, {}).get("value")
        tok = simlib._value_token(value)
        if tok is None:
            return {"error": f"{ref} value {value!r} is not a SPICE number"}
        pct = tol[ref] if isinstance(tol, dict) else tol
        table.append({"ref": ref, "value": tok, "tol": _tol(value, pct)})
    corners = list(itertools.product((-1, 1), repeat=len(parts)))
    measures = []
    for i, signs in enumerate(corners):
        def node(net):
            return fixed.get(net) or f"{names[net]}_C{i}"
        for (ref, a, b), t, s in zip(parts, table, signs):
            lines.append(f"{ref}_C{i} {node(a)} {node(b)} "
                         f"{{{t['value']}*{1 + s * t['tol']:.6g}}}")
        m = f"m_c{i}"
        measures.append(m)
        lines.append(f".meas {analysis} {m} "
                     + meas.format(node=node(probe),
                                   thr=f"{thresholds[i] - 3.0103:.6g}"
                                   if thresholds else ""))
    lines.append(".end")
    return {"cir": "\n".join(lines) + "\n", "measures": measures,
            "parts": table}


def check_spice(bounds, parsed, netmap, work: Path, dll) -> list:
    out = []
    for i, s in enumerate(bounds.get("spice") or []):
        cid = f"spice:{s.get('id', i)}"
        lo, hi = s.get("min"), s.get("max")
        bound = {"min": lo, "max": hi}
        if parsed is None:
            out.append(_check(cid, "electrical", 0.0, bound=bound,
                              note="no netlist in the workspace"))
            continue
        if dll is None:
            out.append(_check(cid, "electrical", None, bound=bound,
                              note="no ngspice library on this host"))
            continue
        if s["kind"] == "divider":
            top, mid, bot = (_net_of(s.get(k), netmap, parsed)
                             for k in ("top", "mid", "bottom"))
            if None in (top, mid, bot):
                out.append(_check(cid, "electrical", 0.0, bound=bound,
                                  note="divider nets not found"))
                continue
            vref, why = _vref(s, parsed)
            if s.get("vref") is not None and vref is None:
                out.append(_check(cid, "electrical", None, bound=bound,
                                  note=why))
                continue
            stop = {v for v in netmap.values() if v} - {top, mid, bot}
            deck = build_divider(parsed, top, mid, bot, stop,
                                 s.get("tol_pct", 1.0))
        else:
            nin, nout, gnd = (_net_of(s.get(k), netmap, parsed)
                              for k in ("in", "out", "gnd"))
            if None in (nin, nout, gnd):
                out.append(_check(cid, "electrical", 0.0, bound=bound,
                                  note="filter nets not found"))
                continue
            vref, why = None, ""
            stop = {v for v in netmap.values() if v} - {nin, nout, gnd}
            rc = (parsed, nin, nout, gnd, stop, s.get("tol_r_pct", 1.0),
                  s.get("tol_c_pct", 10.0))
            deck = build_rc(*rc)
            if "error" not in deck:
                res = run_deck(deck["cir"], work / f"e2e_{cid[6:]}_dc.cir",
                               dll)
                gains = [res.get("measures", {}).get(m)
                         for m in deck["measures"]]
                deck = build_rc(*rc, thresholds=gains) if None not in gains \
                    else {"error": f"low-frequency gain pass failed: "
                                   f"{res.get('error') or 'no measure'}"}
        if "error" in deck:
            out.append(_check(cid, "electrical", 0.0, bound=bound,
                              note=deck["error"]))
            continue
        res = run_deck(deck["cir"], work / f"e2e_{cid[6:]}.cir", dll)
        if res.get("status") != "ok":
            out.append(_check(cid, "electrical", 0.0, bound=bound,
                              note=f"engine: {res.get('error')}"))
            continue
        vals = []
        for m in deck["measures"]:
            v = res.get("measures", {}).get(m)
            if v is not None and vref is not None:
                v = vref / v if v else None      # regulator: Vout = Vref/ratio
            elif v is not None and s.get("scale") is not None:
                v = v * s["scale"]               # a sense divider at full input
            vals.append(v)
        ok = [v is not None and (lo is None or v >= lo)
              and (hi is None or v <= hi) for v in vals]
        got = [v for v in vals if v is not None]
        out.append(_check(
            cid, "electrical", sum(ok) / len(ok), bound=bound,
            value={"min": checklib.rnd(min(got), 6) if got else None,
                   "max": checklib.rnd(max(got), 6) if got else None},
            corners=len(ok), corners_in_bounds=sum(ok), vref=vref,
            vref_from=why or None, parts=deck["parts"], kind=s["kind"]))
    return out


def run_deck(cir: str, path: Path, dll) -> dict:
    """The bench's deck through sim_run's killable worker (the one engine
    recipe the sim gate uses)."""
    import sim_run
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cir, encoding="utf-8")
    return sim_run._run_worker(path, dll, 60.0)


# ------------------------------------------------------------- layout

def check_area(bounds: dict, bg) -> list:
    lim = (bounds.get("layout") or {}).get("area_mm2_max")
    if lim is None:
        return []
    if bg is None:
        return [_check("layout:area", "layout", 0.0, bound=lim,
                       note="no board in the workspace")]
    # geom returns an empty Polygon for a missing or open Edge.Cuts: that is
    # no board, not a board of 0 mm2 under the bound
    if bg.outline.is_empty or not bg.outline.area:
        return [_check("layout:area", "layout", 0.0, bound=lim,
                       note="no closed board outline")]
    area = checklib.rnd(bg.outline.area, 1)
    return [_check("layout:area", "layout", le_score(area, lim),
                   value=area, bound=lim)]


def check_placement(bounds: dict, pcb: Path | None, decoupling) -> list:
    lay = bounds.get("layout") or {}
    ids = ("layout:legality", "layout:crossings")
    if pcb is None:
        return [_check(i, "layout", 0.0, note="no board in the workspace")
                for i in ids]
    import place_metrics
    argv = ["--pcb", str(pcb)]
    if decoupling:
        argv += ["--decoupling", str(decoupling)]
    payload, _ = place_metrics.run(argv)
    m = payload["metrics"]
    viol = payload["counts"]["total"]
    cross = m["crossings_signal"]["count"]
    out = [_check(ids[0], "layout", le_score(viol, 0), value=viol, bound=0),
           _check(ids[1], "layout",
                  le_score(cross, lay.get("crossings_signal_max", 0)),
                  value=cross, bound=lay.get("crossings_signal_max", 0))]
    # explicitly-bulk caps have no distance requirement (as bench P6)
    bulk = {a.get("cap") for a in (checklib.load_json(decoupling, "decoupling")
                                   .get("associations") or [])
            if a.get("class") == "bulk"} if decoupling else set()
    dists = [(f.get("manhattan_mm") or 0) for f in m.get("decoupling") or []
             if f.get("cap") not in bulk]
    if decoupling and dists and "decap_worst_mm_max" in lay:
        worst = checklib.rnd(max(dists), 2)
        out.append(_check("layout:decap", "layout",
                          le_score(worst, lay["decap_worst_mm_max"]),
                          value=worst, bound=lay["decap_worst_mm_max"]))
    return out


# ------------------------------------------------------------- cost

def price_at(breaks: list[dict], qty: int, flat=None) -> float | None:
    """Unit price at an order quantity: the largest break at or under qty
    (the first break when qty is below all of them)."""
    rows = sorted((b["qty"], b["price"]) for b in breaks or []
                  if b.get("price") is not None)
    if not rows:
        return flat
    price = rows[0][1]
    for q, p in rows:
        if q <= qty:
            price = p
    return price


def check_cost(bounds: dict, parts_json: Path | None) -> tuple[list, dict]:
    cost = bounds.get("cost") or {}
    if not cost:
        return [], {}
    qty = int(cost.get("qty", 10))
    target = float(cost["target_usd"])
    if parts_json is None:
        return [_check("cost:bom", "cost", 0.0, bound=target,
                       note="no parts/parts.json in the workspace")], {}
    data = checklib.load_json(parts_json, "parts.json")
    total, unpriced, lines, priced = 0.0, [], 0, 0
    # hwde writes {"parts": [...]}; the bare arm's prompt asks for a plain
    # list of parts with a "unit price", so both shapes and names are read
    rows = data if isinstance(data, list) else data.get("parts") or []
    for p in rows:
        # parts.json has carried the refs under both names
        n = len(p.get("refs") or p.get("refdes") or [])
        if not n:
            continue
        lines += 1
        flat = p.get("price", p.get("unit_price"))
        unit = price_at(p.get("price_breaks"), qty * n, flat)
        if unit is None:
            unpriced.append(p.get("lcsc") or p.get("mpn"))
            continue
        total += unit * n
        priced += 1
    total = checklib.rnd(total, 4)
    facts = {"bom_usd": total, "qty": qty, "bom_lines": lines,
             "unpriced": sorted(u for u in unpriced if u)}
    if not priced:
        return [_check("cost:bom", "cost", 0.0, bound=target, qty=qty,
                       note="no priced BOM line in parts.json")], facts
    # the agent writes parts.json, so an unpriced line must not lower the
    # bill for free: the score is scaled by the priced fraction of lines
    return [_check("cost:bom", "cost",
                   le_score(total, target) * priced / lines, value=total,
                   bound=target, qty=qty, priced_lines=priced,
                   unpriced=facts["unpriced"])], facts


# ------------------------------------------------------------- roll-up

def category_scores(checks: list[dict]) -> dict[str, float]:
    """Mean of the scored checks per category; a category with no scored
    check scores 1.0 (nothing to lose) and says so in `unscored`."""
    out = {}
    for cat in CATEGORIES:
        vals = [c["score"] for c in checks
                if c["category"] == cat and c["score"] is not None]
        out[cat] = checklib.rnd(sum(vals) / len(vals), 4) if vals else 1.0
    return out
