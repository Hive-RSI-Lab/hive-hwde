"""route_critical - script-driven routing of CRITICAL nets before Freerouting
(S11, SPEC P7.1).

Wraps the vendored KiCadRoutingTools (KRT, tools/krt via env.find_krt) to route
the nets whose geometry is load-bearing BEFORE route_auto hands the remainder
to Freerouting: differential pairs (route_diff.py), RF single-ended nets at
impedance width, and high-current power nets at IPC-2152 width (route.py).
Pre-routed copper survives Freerouting by construction: the DSN exporter emits
it as guide wires which Freerouting protects (see route_auto.py).

Critical-net sources (constraints.json, sidecar default like place_metrics):
  diff pairs  constraints["diff_pairs"] [{p, n, gap_mm?, impedance_ohm?,
              max_skew_mm?, max_uncoupled_mm?}] PLUS auto-discovery over the
              high_speed net names via check_diffpair.discover_pairs - the S5
              checker's own suffix rules (_P/_N, DP/DM, D+/D-), so the pipeline
              and the checker can never pair differently.
  rf          constraints["rf"] [{net, impedance_ohm}] plus high_speed entries
              carrying impedance_ohm that are not half of a diff pair; routed
              single-ended at solve_width() impedance width. Via FENCING is
              NOT done here: each routed RF fact carries a fence_handoff note
              (stitch_vias.py --fence-net <net> owns the fence).
  power       constraints["power"] [{net, current_a, dt_c?}]; width = IPC-2152
              minimum (check_current.required_width_mm, rules_gen's helper)
              * 1.5 margin, clamped >= 0.3 mm.

KRT facts this adapter encodes (all machine-verified against KRT 0.19.0):
  * KRT scripts are invoked as subprocesses of THIS python with
    cwd=<plugins dir> (they sys.path-insert relative dirs) and args as a list
    (never a shell - Git Bash mangles "/USB_DP" into a path). The final
    "JSON_SUMMARY: {...}" stdout line (the LAST one - a reconcile pass may
    print a scoped second summary) is the machine-readable outcome; KRT's own
    exit code / internal DRC are advisory only.
  * --no-fix-drc-settings is ALWAYS passed (without it KRT rewrites the
    .kicad_pro DRC floors - prior-attempt fact), so routed copper must grade
    clean against the board's EXISTING settings. The adapter therefore passes
    explicit floors read from the .kicad_pro (KiCad stock defaults when
    absent: clearance 0.2, via 0.5/0.3, copper-to-edge 0.5) as --clearance /
    --via-size / --via-drill / --board-edge-clearance, plus a --fab-overrides
    file pinning the same values so KRT's fab-tier escalation can never route
    below what kicad-cli will grade (S11: its 0.3/0.15 escalation via drew
    annular/hole/diameter DRC errors on a stock board).
  * Every KRT call routes staged -> FRESH output file; on success the output
    replaces the staged board (which keeps its .kicad_pro sidecar - KRT reads
    netclasses from the INPUT's sibling, and boards must never travel without
    their project file).
  * DP/DM suffixes ARE in KRT's pair detector (net_queries.extract_diff_pair_base
    rule r'^(.*?)D([PMN])$'), verified live on /USB_DP + /USB_DM.
  * KRT 0.19.0 limitation + workaround: a 2-terminal pair where one net has an
    extra stub pad (USB pull-up R, series tap) MISPAIRS its route terminals
    (connectivity.get_net_endpoints Case 3 makes the stub pad a "source" and
    the ref-midpoint target selector then prefers a degenerate cross pairing)
    and the pair dies with "no-escape-path" at both ends. The adapter detaches
    such stub pads - pads outside every matched terminal per
    check_diffpair.matched_terminals - from their net in the KRT INPUT text,
    routes, then restores the net node in the OUTPUT (KRT's writer preserves
    footprint text byte-identical; the restore is verified before the board is
    accepted). The stub itself is ordinary Freerouting work later.
  * Diff-pair width ladder: the impedance-derived width is tried first; if the
    pair defers/fails (fine-pitch escape), one fallback rung at 0.2 mm width
    retries. --grid-step 0.05 (default here) matters: 0.1 quantizes away the
    tap corridors of 0.5 mm-pitch parts (S11-verified: +3V3 15/16 pads at 0.1,
    21/21 at 0.05).
  * KRT 0.19.0 limitation + workaround: a power net routed wider than the
    base width keeps its extra half-width as a FRACTIONAL grid margin, and
    against an off-grid 45-degree obstacle (a diff-pair track) that margin
    comes up to half a diagonal grid phase short (usbbuck4: VBUS ran 0.1854
    mm from USB_DM against 0.2, 3-6 DRC errors depending on the board's
    incidental UUID order). The power pass therefore routes with the
    clearance of every net already carrying tracks raised by grid_step / 2
    in its own --net-clearances file (power_net_clearances); DRC still
    grades against the board's real floors.

Layer discipline: diff pairs and RF prefer the outer layer over the first
inner reference plane (F.Cu on the 4-layer stack, cost 1) with the far outer
layer allowed at cost 3 (power: cost 2); inner layers are excluded and KRT
marks them as forbidden obstacles for via spans. Actual layers used are
re-read from the output (geom) and reported as facts.

Verification before the board is touched: kicad-cli DRC (kc.run_drc,
--all-track-errors) runs on the staged board BEFORE and AFTER; any violation
type whose count increased -> exit 2, original board untouched
(unconnected_items legitimately DROPS). The S5 diff-pair check
(check_diffpair.check_pair) then runs in-process on the routed pairs; its
violations gate status like the router's own.

Determinism: KRT's routing is deterministic for identical inputs, but it
stamps fresh uuid4s on every track/via, so byte-identical reruns are
impossible. Determinism here (and in tests) = same nets routed + same DRC
outcome, never same bytes.

Contract (SPEC section 6):
  route_critical.py --pcb B.kicad_pcb [--constraints c.json]
      [--only diff|power|rf] [--plan plan.json] [--grid-step MM]
      [--timeout-s S] [--work-dir DIR] [--out-report r.json]
      [--pad-window [--nets A,B]]
  exit 0 = every critical item routed + DRC gained nothing + diff-pair check
  passes; 1 = some item failed or a check flags (board still updated with
  what succeeded - only reachable when DRC gained no new errors); 2 = error
  (original board untouched).
  --pad-window (T6, ladder row 78): pure-geometry probe, no KRT and no
  writes - reports the widest connectable track per pad of each power net
  ({ref, pad, net, widest_mm, rule_min_mm, ok}) and exits 1 when a DRU
  per-net width floor is geometrically unmeetable at a pad (the pd-trigger
  USB-C VBUS case: 1.75 mm rule vs ~1.49 mm ceiling).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from shapely.geometry import box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

import check_current  # noqa: E402  (required_width_mm - rules_gen's helper)
import check_diffpair  # noqa: E402  (discover_pairs / matched_terminals / check_pair)
import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import impedance as imp  # noqa: E402
import kc  # noqa: E402
import route_edit  # noqa: E402  (width normalization ops)
import routelib  # noqa: E402  (fresh_work_dir / swap_in)
from checklib import CheckError, violation  # noqa: E402

SCRIPT = "route_critical"
DEFAULT_ORDER = ("diff", "rf", "power")
POWER_MARGIN = 1.5          # IPC-2152 minimum * this
POWER_WIDTH_FLOOR = 0.3     # mm - never route a declared power net thinner
FALLBACK_PAIR_WIDTH = 0.2   # mm - diff ladder rung 2 (fine-pitch escape)
BASE_TRACK_WIDTH = 0.2      # mm - KRT base width (power-tap neckdown target)
# KRT iteration ladder (T6, LEARNINGS 1433/1504): the 200k default A*
# iteration cap - not congestion - is what fails a 60-70 mm haul; 4M routed
# both carrier hauls first try. Retry once, failed nets only, when KRT says
# "No route found after N iterations" and the Coverage diagnostic blames a
# mostly-STATIC frontier (share >= 0.7 - rip sets cannot help by
# construction; unknown share retries too, the no-route line alone marks
# iteration exhaustion).
RETRY_MAX_ITERATIONS = 4_000_000
RETRY_MAX_PROBE_ITERATIONS = 60_000
STATIC_RETRY_SHARE = 0.7
NO_ROUTE_RE = re.compile(r"No route found after \d+ iterations")
COVERAGE_RE = re.compile(
    r"Coverage: (\d+)/(\d+) frontier cells attributed to routed nets; "
    r"(\d+) static/unrippable")
PAD_WINDOW_CAP = 8.0        # mm - widest track the --pad-window probe reports
KRT_REMEDIATION = (
    "KiCadRoutingTools not found (env.find_krt). Re-fetch: download "
    "https://github.com/drandyhaas/KiCadRoutingTools/releases/download/"
    "v0.19.0/KiCadRoutingTools-0.19.0.zip, unzip under tools/krt/, and copy "
    "grid_router-windows-x86_64.pyd to plugins/rust_router/grid_router.pyd "
    "(see tools/krt/PROVENANCE.txt). HWDE_KRT_DIR overrides.")

# KiCad stock board-setup floors, used when the .kicad_pro carries no value.
KICAD_DEFAULTS = {
    "clearance": 0.2,        # Default netclass clearance
    "track_width": 0.2,      # rules.min_track_width
    "via_diameter": 0.5,     # board setup min via diameter
    "via_drill": 0.3,        # board setup min through hole
    "edge_clearance": 0.5,   # board setup copper-to-edge
}


# ------------------------------------------------------------ pure helpers

def parse_summary(stdout: str) -> dict | None:
    """The LAST 'JSON_SUMMARY: {...}' line of a KRT run, parsed."""
    last = None
    for line in stdout.splitlines():
        if line.startswith("JSON_SUMMARY:"):
            last = line[len("JSON_SUMMARY:"):].strip()
    if last is None:
        return None
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return None


def coverage_static_share(stdout: str) -> float | None:
    """Static/unrippable share of the blocking frontier from KRT's LAST
    'Coverage: A/T frontier cells ...; S static/unrippable' diagnostic
    (LEARNINGS 1504: >0.7 means raise iterations, never widen rip sets).
    None when the line never appeared."""
    last = None
    for m in COVERAGE_RE.finditer(stdout or ""):
        last = m
    if last is None:
        return None
    total = int(last.group(2))
    if total <= 0:
        return None
    return int(last.group(3)) / total


def power_width_mm(current_a: float, dt_c: float = 10.0,
                   cu_mm: float = 0.035) -> float:
    """Routing width for a power net: IPC-2152 minimum * margin, floored."""
    need = check_current.required_width_mm(float(current_a), dt_c, cu_mm)
    return round(max(need * POWER_MARGIN, POWER_WIDTH_FLOOR), 4)


def default_impedance(p: str, n: str) -> int:
    """rules_gen's default: USB pairs 90 ohm differential, else 100."""
    return 90 if "USB" in (p + n).upper() else 100


def diff_specs(constraints: dict, board_nets) -> list[dict]:
    """Explicit constraints['diff_pairs'] entries PLUS S5-discovered pairs
    over the high_speed net names. Returns [{p, n, impedance_ohm, ...}]."""
    hs = constraints.get("high_speed", []) or []
    imp_of = {e.get("net"): e.get("impedance_ohm") for e in hs}
    specs: list[dict] = []
    used: set[str] = set()
    for entry in constraints.get("diff_pairs", []) or []:
        p, n = entry.get("p"), entry.get("n")
        if not p or not n:
            continue
        spec = dict(entry)
        spec.setdefault("impedance_ohm",
                        imp_of.get(p) or imp_of.get(n) or default_impedance(p, n))
        specs.append(spec)
        used.update((p, n))
    hs_nets = [e.get("net") for e in hs if e.get("net")]
    for p, n in check_diffpair.discover_pairs(hs_nets):
        if p in used or n in used:
            continue
        z = imp_of.get(p) or imp_of.get(n) or default_impedance(p, n)
        specs.append({"p": p, "n": n, "impedance_ohm": z})
        used.update((p, n))
    del board_nets  # membership is judged (with a warning) at route time
    return specs


def rf_specs(constraints: dict, pair_nets: set[str]) -> list[dict]:
    """constraints['rf'] plus high_speed entries with impedance_ohm that are
    not half of a diff pair."""
    specs: list[dict] = []
    seen: set[str] = set()
    for entry in constraints.get("rf", []) or []:
        net = entry.get("net")
        if net and net not in pair_nets and net not in seen:
            specs.append({"net": net,
                          "impedance_ohm": entry.get("impedance_ohm", 50)})
            seen.add(net)
    for entry in constraints.get("high_speed", []) or []:
        net, z = entry.get("net"), entry.get("impedance_ohm")
        if net and z and net not in pair_nets and net not in seen:
            specs.append({"net": net, "impedance_ohm": z})
            seen.add(net)
    return specs


def power_specs(constraints: dict, cu_mm: float = 0.035) -> list[dict]:
    specs = []
    for entry in constraints.get("power", []) or []:
        net = entry.get("net")
        if not net or entry.get("current_a") is None:
            continue
        cur = float(entry["current_a"])
        dt = float(entry.get("dt_c", 10.0))
        ipc = round(check_current.required_width_mm(cur, dt, cu_mm), 4)
        specs.append({"net": net, "current_a": cur, "dt_c": dt,
                      "ipc_min_mm": ipc,
                      "width_mm": power_width_mm(cur, dt, cu_mm)})
    return specs


def plan_order(only: str | None, plan: dict | None) -> list[str]:
    """Item-kind order: --plan {'order': [...]} overrides, --only filters."""
    order = list(DEFAULT_ORDER)
    if plan:
        want = plan.get("order")
        if not (isinstance(want, list) and want
                and all(k in DEFAULT_ORDER for k in want)):
            raise CheckError(f"plan 'order' must be a non-empty subset of "
                             f"{list(DEFAULT_ORDER)}, got {want!r}")
        order = list(dict.fromkeys(want))
    if only:
        order = [k for k in order if k == only]
    return order


def grading_floors(pro_path: Path | None) -> dict:
    """The floors kicad-cli DRC will grade at, from the .kicad_pro (KiCad
    stock defaults when a value is absent). clearance = the LOOSEST netclass
    (a --clearance ceiling at that value caps nothing that matters, but pins
    the no-netclass fallback base to the graded default)."""
    floors = dict(KICAD_DEFAULTS)
    if pro_path is None or not Path(pro_path).is_file():
        return floors
    try:
        proj = json.loads(Path(pro_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return floors
    rules = proj.get("board", {}).get("design_settings", {}).get("rules", {})
    if rules.get("min_track_width"):
        floors["track_width"] = float(rules["min_track_width"])
    # KiCad writes "min_copper_edge_clearance" (fabfloors.PRO_RULE_KEYS);
    # the legacy "min_copper_to_edge" spelling is kept for compatibility.
    edge = rules.get("min_copper_edge_clearance") \
        or rules.get("min_copper_to_edge")
    if edge:
        floors["edge_clearance"] = float(edge)
    if rules.get("min_via_diameter"):
        floors["via_diameter"] = float(rules["min_via_diameter"])
    if rules.get("min_through_hole_diameter"):
        floors["via_drill"] = float(rules["min_through_hole_diameter"])
    classes = proj.get("net_settings", {}).get("classes", []) or []
    clearances = [c.get("clearance") for c in classes
                  if isinstance(c.get("clearance"), (int, float))]
    if clearances:
        floors["clearance"] = float(max(clearances))
    return floors


# ------------------------------------------------------------ DRU parsing

_DRU_RULE_RE = re.compile(r'\(rule\s+"([^"]+)"(.*?)\n\)', re.S)
_DRU_CONSTRAINT_RE = re.compile(
    r'\(constraint\s+(\w+)\s+\(min\s+([\d.]+)\s*mm\)\)')
_DRU_NETNAME_RE = re.compile(r"\.NetName\s*==\s*'([^']+)'")
_DRU_CONDITION_RE = re.compile(r'\(condition\s+"((?:[^"\\]|\\.)*)"\s*\)')
_DRU_LAYER_RE = re.compile(r'\(layer\s+"?([^\s")]+)"?\s*\)')


def parse_dru_rules(text: str) -> list[dict]:
    """Named (rule ...) blocks with a (min Xmm) constraint ->
    [{name, constraint, min_mm, nets, condition, layer}]. nets = NetName
    literals in the condition ([] for unconditioned/baseline rules);
    condition = the raw condition string (None when absent); layer = the
    (layer ...) clause token (None when absent). Only the shapes rules_gen
    emits are recognized; anything else simply yields no row."""
    out: list[dict] = []
    for m in _DRU_RULE_RE.finditer(text or ""):
        name, body = m.group(1), m.group(2)
        c = _DRU_CONSTRAINT_RE.search(body)
        if not c:
            continue
        cond = _DRU_CONDITION_RE.search(body)
        lay = _DRU_LAYER_RE.search(body)
        out.append({"name": name, "constraint": c.group(1),
                    "min_mm": float(c.group(2)),
                    "nets": sorted(set(_DRU_NETNAME_RE.findall(body))),
                    "condition": cond.group(1) if cond else None,
                    "layer": lay.group(1) if lay else None})
    return out


def dru_net_floors(dru_path: Path | None, constraint: str) -> dict[str, float]:
    """Per-net minimums of one constraint type from a .kicad_dru
    (e.g. 'track_width' -> {net: floor_mm}). Missing file -> {}."""
    if dru_path is None or not Path(dru_path).is_file():
        return {}
    try:
        text = Path(dru_path).read_text(encoding="utf-8")
    except OSError:
        return {}
    floors: dict[str, float] = {}
    for rule in parse_dru_rules(text):
        if rule["constraint"] != constraint:
            continue
        for net in rule["nets"]:
            floors[net] = max(floors.get(net, 0.0), rule["min_mm"])
    return floors


def build_net_clearances(pro_path: Path | None, dru_path: Path | None,
                         board_nets) -> dict[str, float] | None:
    """Per-net clearance map for KRT's --net-clearances (LEARNINGS 1522):
    KRT's --clearance is a CAP on its auto-read netclass map (it silently
    pulled 0.635 mm HV nets DOWN to 0.2), and KRT cannot read a .kicad_dru,
    so DRU-only HV clearance never reaches the router without this file.

    Values = max(netclass clearance, DRU per-net clearance rules) - the max
    over the class value means nothing is ever capped DOWN. Fails OPEN
    (returns None -> no file emitted -> today's behavior) on anything
    unparseable rather than emit wrong values."""
    try:
        out: dict[str, float] = {}
        nets = list(board_nets)
        if pro_path is not None and Path(pro_path).is_file():
            proj = json.loads(Path(pro_path).read_text(encoding="utf-8"))
            ns = proj.get("net_settings") or {}
            classes = {c.get("name"): c for c in ns.get("classes") or []
                       if isinstance(c, dict)}
            for pat in ns.get("netclass_patterns") or []:
                cls = classes.get(pat.get("netclass"))
                pattern = pat.get("pattern")
                if not cls or not pattern \
                        or not isinstance(cls.get("clearance"), (int, float)):
                    continue
                for net in nets:
                    if fnmatch.fnmatchcase(net, pattern):
                        out[net] = max(out.get(net, 0.0),
                                       float(cls["clearance"]))
        for net, clr in dru_net_floors(dru_path, "clearance").items():
            if net in board_nets:
                out[net] = max(out.get(net, 0.0), clr)
        out = {n: round(v, 4) for n, v in sorted(out.items()) if v > 0}
        return out or None
    except Exception:  # noqa: BLE001 - fail open, never emit wrong values
        return None


def pair_geometry(bg: geom.BoardGeom, impedance_ohm: float,
                  gap_c2c_mm: float | None = None) -> tuple[float, float]:
    """(track_width, edge_gap) mm for a differential pair on the preferred
    outer layer, from the board stackup (impedance.diff_pair - the same math
    rules_gen uses). constraints gap_mm is CENTER-to-center (check_diffpair's
    convention); KRT's --diff-pair-gap is edge-to-edge."""
    outer = bg.copper_layers[0]
    _, below = bg.stackup.adjacent(outer)
    if below is None or not bg.stackup.dielectrics:
        return FALLBACK_PAIR_WIDTH, 0.2
    h = bg.stackup.height_between(outer, below)
    er = bg.stackup.epsilon_between(outer, below)
    t = bg.stackup.copper_thickness.get(outer, 0.035)
    if gap_c2c_mm is not None:
        # solve width for the pinned center-to-center pitch: w + s = pitch
        w, s = imp.diff_pair(float(impedance_ohm), h, t, er)
        # re-solve with gap pinned so pitch holds approximately
        for _ in range(8):
            s = max(float(gap_c2c_mm) - w, 0.05)
            w, s = imp.diff_pair(float(impedance_ohm), h, t, er, gap=s)
        return w, s
    return imp.diff_pair(float(impedance_ohm), h, t, er)


def rf_width(bg: geom.BoardGeom, impedance_ohm: float) -> float:
    outer = bg.copper_layers[0]
    _, below = bg.stackup.adjacent(outer)
    if below is None or not bg.stackup.dielectrics:
        return BASE_TRACK_WIDTH
    h = bg.stackup.height_between(outer, below)
    er = bg.stackup.epsilon_between(outer, below)
    t = bg.stackup.copper_thickness.get(outer, 0.035)
    return round(imp.solve_width(float(impedance_ohm), h, t, er), 4)


# ------------------------------------------------------------ pad window

def _pad_window_foreign(bg: geom.BoardGeom, net: str, layer: str):
    """WIRED foreign copper on `layer`: tracks/pads/vias of every OTHER net
    INCLUDING unnetted pads (bg.nets omits them - LEARNINGS 1538: 37
    invisible no-net items caused 4 real clearance errors), but never zone
    fills (fills re-flow around new copper at refill)."""
    parts = [t.poly for t in bg.tracks_of(layer=layer) if t.net != net]
    parts += [p.poly for p in bg.pads_of(layer=layer) if p.net != net]
    parts += [v.poly for v in bg.vias_of(layer=layer) if v.net != net]
    return unary_union(parts) if parts else None


def widest_connectable_mm(pad, foreign, outline, clearance: float,
                          edge_clearance: float,
                          cap: float = PAD_WINDOW_CAP) -> float:
    """The widest track that can legally touch this pad (LEARNINGS 795):
    sup over centreline points P of 2*(dist(P, foreign) - CLR) subject to
    dist(P, pad copper) <= W/2, with P additionally kept on the board
    (outline eroded by edge_clearance + W/2). Solved by bisection on W -
    feasible(W) iff pad.buffer(W/2), clipped to the eroded outline, minus
    foreign.buffer(CLR + W/2) still has area. Returns cap when unbounded
    within the cap."""
    fl = None
    if foreign is not None and not foreign.is_empty:
        window = box(*pad.poly.buffer(cap + clearance + 0.1).bounds)
        fl = foreign.intersection(window)
        if fl.is_empty:
            fl = None

    def feasible(w: float) -> bool:
        region = pad.poly.buffer(w / 2.0)
        if outline is not None and not outline.is_empty:
            region = region.intersection(
                outline.buffer(-(edge_clearance + w / 2.0)))
        if fl is not None and not region.is_empty:
            region = region.difference(fl.buffer(clearance + w / 2.0))
        return not region.is_empty and region.area > 1e-9

    if feasible(cap):
        return float(cap)
    lo, hi = 0.0, cap
    for _ in range(14):                      # cap/2^14 < 0.001 mm
        mid = (lo + hi) / 2.0
        if feasible(mid):
            lo = mid
        else:
            hi = mid
    return round(lo, 3)


def _clearance_floor(pro_path: Path | None) -> float:
    """The board's minimum copper clearance (rules.min_clearance, else the
    Default netclass) - the OPTIMISTIC floor for the pad window: if the
    width is unmeetable even at the floor, it is unmeetable, full stop."""
    if pro_path is not None and Path(pro_path).is_file():
        try:
            proj = json.loads(Path(pro_path).read_text(encoding="utf-8"))
            rules = (proj.get("board", {}).get("design_settings", {})
                     .get("rules", {}))
            if isinstance(rules.get("min_clearance"), (int, float)) \
                    and rules["min_clearance"] > 0:
                return float(rules["min_clearance"])
            for c in (proj.get("net_settings", {}).get("classes") or []):
                if c.get("name") == "Default" \
                        and isinstance(c.get("clearance"), (int, float)):
                    return float(c["clearance"])
        except (OSError, json.JSONDecodeError):
            pass
    return KICAD_DEFAULTS["clearance"]


def pad_window_report(pcb: Path, constraints: dict,
                      nets_arg: str | None) -> dict:
    """--pad-window: deterministic no-routing probe - the measured width
    ceiling per pad of each power net vs its DRU per-net track_width floor
    (ladder row 78; converts the pd-trigger premise falsification and the
    carrier's ad-hoc pad_gap.py into one scripted call)."""
    bg = geom.BoardGeom.from_file(pcb)
    if nets_arg:
        nets = [s.strip() for s in nets_arg.split(",") if s.strip()]
    else:
        nets = [e.get("net") for e in constraints.get("power") or []
                if isinstance(e, dict) and e.get("net")]
    if not nets:
        raise CheckError("--pad-window: no target nets (pass --nets or "
                         "declare constraints['power'])")
    pro = pcb.with_suffix(".kicad_pro")
    clearance = _clearance_floor(pro if pro.is_file() else None)
    edge = grading_floors(pro if pro.is_file() else None)["edge_clearance"]
    rule_floors = dru_net_floors(pcb.with_suffix(".kicad_dru"), "track_width")
    outer = set(_outer_layers(bg))
    rows: list[dict] = []
    violations: list[dict] = []
    missing: list[str] = []
    for net in nets:
        if net not in bg.nets:
            missing.append(net)
            continue
        f_cache: dict = {}
        for pad in sorted(bg.pads_of(net=net),
                          key=lambda p: (p.ref, p.number)):
            layers = [l for l in pad.layers if l in outer] \
                or [pad.layers[0]]
            widest = 0.0
            for layer in layers:            # best attach layer wins
                if layer not in f_cache:
                    f_cache[layer] = _pad_window_foreign(bg, net, layer)
                widest = max(widest, widest_connectable_mm(
                    pad, f_cache[layer], bg.outline, clearance, edge))
            rule_min = rule_floors.get(net)
            ok = rule_min is None or widest >= rule_min - 1e-9
            rows.append({"ref": pad.ref, "pad": pad.number, "net": net,
                         "widest_mm": widest, "rule_min_mm": rule_min,
                         "ok": ok})
            if not ok:
                violations.append(violation(
                    SCRIPT, "error", list(pad.center), layers[0], net,
                    [pad.ref],
                    f"power pad {pad.ref}.{pad.number} ({net}): widest "
                    f"connectable track {widest} mm < DRU floor {rule_min} "
                    "mm - no legal rule-width track can reach this pad; "
                    "pour fan-in instead "
                    "(reference/remediations/track_width.md step 4)",
                    SCRIPT, kind="pad_window_unmeetable",
                    widest_mm=widest, rule_min_mm=rule_min))
    return checklib.report(SCRIPT, str(pcb), violations, facts={
        "probe": "pad_window", "pad_window": rows, "nets": nets,
        "missing_nets": missing, "clearance_mm": clearance,
        "edge_clearance_mm": edge, "cap_mm": PAD_WINDOW_CAP})


# ------------------------------------------------------------ stub-pad surgery

_NET_NODE = r'\n[ \t]*\(net(?:\s+\d+)?\s+"{name}"\s*\)'


def _footprint_block(text: str, ref: str) -> tuple[int, int]:
    """(start, end) of the footprint block whose Reference property is `ref`."""
    m = re.search(r'\(property\s+"Reference"\s+"%s"' % re.escape(ref), text)
    if not m:
        raise CheckError(f"footprint {ref!r} not found in board text")
    start = text.rfind("(footprint", 0, m.start())
    if start < 0:
        raise CheckError(f"no (footprint block encloses Reference {ref!r}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise CheckError(f"unbalanced footprint block for {ref!r}")


def detach_stub_pads(text: str, targets: list[tuple[str, str]]
                     ) -> tuple[str, list[tuple[str, str]]]:
    """Remove the (net ...) node of every pad of net inside footprint ref,
    for each (ref, net) target. Returns (new_text, restores) where restores
    is [(detached_block, original_block)] for restore_stub_pads."""
    restores: list[tuple[str, str]] = []
    for ref, net in targets:
        start, end = _footprint_block(text, ref)
        block = text[start:end]
        pat = _NET_NODE.format(name=re.escape(net))
        newblock, nsub = re.subn(pat, "", block)
        if nsub == 0:
            raise CheckError(f"no pad of net {net!r} found in footprint {ref!r}")
        if text.count(block) != 1:
            raise CheckError(f"footprint block for {ref!r} is not unique")
        text = text.replace(block, newblock, 1)
        restores.append((newblock, block))
    return text, restores


def restore_stub_pads(text: str, restores: list[tuple[str, str]]) -> str:
    """Re-insert the original footprint blocks into KRT's output. KRT's
    writer preserves footprint text byte-identical; anything else is a hard
    error (the caller rolls back).

    Applied in REVERSE detach order: when two stub targets share one
    footprint, detach_stub_pads nests its edits (the second detach was made
    on the once-detached block), so only the innermost (last) detached form
    exists in the routed output (S11 review finding)."""
    for detached, original in reversed(restores):
        if text.count(detached) != 1:
            raise CheckError("cannot restore stub pad net: detached footprint "
                             "block not found verbatim in routed output "
                             "(KRT writer changed footprint text?)")
        text = text.replace(detached, original, 1)
    return text


def unmatched_stub_pads(bg: geom.BoardGeom, p: str, n: str
                        ) -> list[tuple[str, str]]:
    """(ref, net) groups of pair pads outside every matched terminal (USB
    pull-up, series tap). Grouped by (ref, net); a group is a stub only when
    NONE of its pads is a matched terminal."""
    matched: set[tuple[str, tuple[float, float]]] = set()
    for pp, nn in check_diffpair.matched_terminals(bg, p, n):
        matched.add((pp.ref, (round(pp.center[0], 3), round(pp.center[1], 3))))
        matched.add((nn.ref, (round(nn.center[0], 3), round(nn.center[1], 3))))
    groups: dict[tuple[str, str], list[bool]] = {}
    for net in (p, n):
        for pad in bg.pads_of(net=net):
            key = (pad.ref, net)
            is_matched = (pad.ref, (round(pad.center[0], 3),
                                    round(pad.center[1], 3))) in matched
            groups.setdefault(key, []).append(is_matched)
    return sorted(k for k, flags in groups.items() if not any(flags))


# ------------------------------------------------------------ KRT invocation

def _tools() -> tuple[Path, Path]:
    cli = env.find_kicad_cli()
    if cli is None:
        raise CheckError("kicad-cli not found (check_env.py --full has "
                         "remediation); pipeline pins KiCad 10.0.3")
    krt = env.find_krt()
    if krt is None:
        raise CheckError(KRT_REMEDIATION)
    return cli, krt


def _stage_board(pcb: Path, work: Path) -> Path:
    """Board + every same-stem sidecar (pro/prl/dru/sch) -> work dir."""
    work.mkdir(parents=True, exist_ok=True)
    staged = work / pcb.name
    for src in pcb.parent.glob(pcb.stem + ".*"):
        if src.is_file() and not src.name.endswith(".lck"):
            shutil.copy2(src, work / src.name)
    if not staged.is_file():
        raise CheckError(f"staging failed: {staged}")
    return staged


def write_fab_overrides(work: Path, floors: dict) -> Path:
    """Pin KRT's fab floor to the board's DRC grading floors (hard floor: the
    overrides file disables fab-tier escalation below what DRC grades)."""
    p = work / "fab_overrides.txt"
    p.write_text(
        f"clearance = {floors['clearance']}\n"
        f"track_width = {floors['track_width']}\n"
        f"via_diameter = {floors['via_diameter']}\n"
        f"via_drill = {floors['via_drill']}\n"
        f"board_edge = {floors['edge_clearance']}\n", encoding="utf-8")
    return p


def run_krt(krt_dir: Path, script: str, staged: Path, out: Path,
            extra: list[str], floors: dict, fab_file: Path,
            grid_step: float, timeout_s: int,
            net_clearances: Path | None = None,
            stdout_sink: list | None = None) -> dict:
    """One KRT run staged -> fresh out. Returns the parsed LAST JSON_SUMMARY.
    Args are a list (no shell) so net names like '/USB_DP' survive.
    net_clearances: optional per-net clearance JSON for KRT's
    --net-clearances (NOT capped by --clearance - LEARNINGS 1522; this is
    the only path a DRU-only HV clearance reaches the router).
    stdout_sink: when given, KRT's full stdout is appended to it (the
    Coverage / no-route diagnostics live there, not in the summary)."""
    args = [sys.executable, script, str(staged), "--output", str(out),
            "--no-fix-drc-settings",
            "--grid-step", str(grid_step),
            "--clearance", str(floors["clearance"]),
            "--via-size", str(floors["via_diameter"]),
            "--via-drill", str(floors["via_drill"]),
            "--board-edge-clearance", str(floors["edge_clearance"]),
            "--fab-overrides", str(fab_file)]
    if net_clearances is not None:
        args += ["--net-clearances", str(net_clearances)]
    args += extra
    try:
        cp = subprocess.run(args, cwd=str(krt_dir), capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise CheckError(f"KRT {script} timed out after {timeout_s}s") from exc
    if stdout_sink is not None:
        stdout_sink.append(cp.stdout or "")
    summary = parse_summary(cp.stdout or "")
    if summary is None:
        # KRT's "nothing to route" path (every target net already fully
        # connected) prints no JSON_SUMMARY but rc=0 + writes an unchanged
        # copy - a legitimate no-op on idempotent re-runs, not an error.
        if cp.returncode == 0 and out.is_file() \
                and "nothing to route" in (cp.stdout or ""):
            return {"already_routed": True, "successful": 0, "failed": 0}
        tail = ((cp.stdout or "") + "\n" + (cp.stderr or "")).strip()[-600:]
        raise CheckError(f"KRT {script} produced no JSON_SUMMARY "
                         f"(rc={cp.returncode}): {tail}")
    return summary


def _pair_outcome(summary: dict, p: str, n: str) -> tuple[bool, str]:
    """(routed_as_coupled_pair, reason) from a route_diff JSON_SUMMARY."""
    for rep in summary.get("pair_reports", []) or []:
        if rep.get("p_net") == p and rep.get("n_net") == n:
            if rep.get("outcome") == "coupled" and \
                    rep.get("pair") in (summary.get("routed_diff_pairs") or []):
                return True, "coupled"
            return False, (rep.get("failure_reason") or rep.get("outcome")
                           or "not-routed")
    # no report row for this pair at all -> it was never even detected
    return False, "pair-not-detected"


def _net_failed(summary: dict, net: str) -> str | None:
    """Failure reason if a route.py summary says `net` did not fully route."""
    if net in (summary.get("failed_single") or []):
        return "single-ended route failed"
    for item in summary.get("failed_multipoint") or []:
        if item.get("net_name") == net:
            k = len(item.get("failed_pads") or [])
            return f"multipoint: {k} pad(s) unconnected"
    return None


def _outer_layers(bg: geom.BoardGeom) -> list[str]:
    cl = bg.copper_layers
    return [cl[0]] if len(cl) < 2 else [cl[0], cl[-1]]


def _slim(summary: dict, keys: tuple[str, ...]) -> dict:
    return {k: summary.get(k) for k in keys if k in summary}


# ------------------------------------------------------------ item runners

def _next_out(ctx: dict, tag: str) -> Path:
    """A FRESH output path per KRT attempt (route.py re-reads DRC floors from
    an output-sibling .kicad_pro on same-path reruns)."""
    ctx["seq"] = ctx.get("seq", 0) + 1
    return ctx["work"] / f"krt_{ctx['seq']:02d}_{tag}.kicad_pcb"


def route_diff_item(ctx: dict, spec: dict) -> tuple[dict | None, dict | None]:
    """Route one diff pair (width ladder + stub-pad detach). Returns
    (fact, violation): exactly one is None."""
    p, n = spec["p"], spec["n"]
    staged = ctx["staged"]
    bg = geom.BoardGeom.from_file(staged)
    if p not in bg.nets or n not in bg.nets:
        missing = [x for x in (p, n) if x not in bg.nets]
        return None, violation(
            SCRIPT, "warning", None, None, p, [],
            f"diff pair {p}/{n} names net(s) not on the board: {missing} "
            f"(stale constraints?)", SCRIPT,
            kind="critical_missing_net", pair=[p, n], missing=missing)

    z = spec.get("impedance_ohm", default_impedance(p, n))
    w, g = pair_geometry(bg, z, spec.get("gap_mm"))
    ladder = [(w, g)]
    if abs(w - FALLBACK_PAIR_WIDTH) > 1e-6:
        ladder.append((FALLBACK_PAIR_WIDTH, max(g, ctx["floors"]["clearance"])))

    stubs = unmatched_stub_pads(bg, p, n)
    original_text = staged.read_text(encoding="utf-8")
    restores: list[tuple[str, str]] = []
    if stubs:
        detached_text, restores = detach_stub_pads(original_text, stubs)
        staged.write_text(detached_text, encoding="utf-8")

    outer = _outer_layers(bg)
    costs = ["1"] if len(outer) == 1 else ["1", "3"]
    try:
        last_reason = "no attempt"
        for rung, (rw, rg) in enumerate(ladder, 1):
            out = _next_out(ctx, f"diff_r{rung}")
            summary = run_krt(
                ctx["krt"], "route_diff.py", staged, out,
                ["--nets", p, n, "--layers", *outer, "--layer-costs", *costs,
                 "--track-width", f"{rw:g}", "--diff-pair-gap", f"{rg:g}"],
                ctx["floors"], ctx["fab_file"], ctx["grid_step"],
                ctx["timeout_s"],
                net_clearances=ctx.get("net_clearances"))
            if summary.get("already_routed"):
                # idempotent re-run: both nets already fully connected; the
                # post-run check_diffpair pass still validates coupling.
                ok, reason = True, "already_routed"
            else:
                ok, reason = _pair_outcome(summary, p, n)
            if ok:
                text = out.read_text(encoding="utf-8")
                if restores:
                    text = restore_stub_pads(text, restores)
                out.write_text(text, encoding="utf-8")
                os.replace(out, staged)
                restores = []          # consumed: staged now holds original pads
                bg2 = geom.BoardGeom.from_file(staged)
                layers = sorted({t.layer for net in (p, n)
                                 for t in bg2.tracks_of(net=net)})
                fact = {"kind": "diff", "nets": [p, n],
                        "impedance_ohm": z,
                        "width_mm": rw, "gap_mm": rg,
                        "impedance_width_mm": w, "ladder_rung": rung,
                        "stub_pads_detached": [list(s) for s in stubs],
                        "layers_used": layers,
                        "krt": _slim(summary, ("routed_diff_pairs",
                                               "total_vias", "total_time",
                                               "min_clearance_used"))}
                return fact, None
            last_reason = reason
        return None, violation(
            SCRIPT, "error", None, None, p, [],
            f"critical diff pair {p}/{n} failed to route as a coupled pair "
            f"({last_reason}); tried widths "
            f"{[rw for rw, _ in ladder]}", SCRIPT,
            kind="critical_route_failed", pair=[p, n], reason=last_reason)
    finally:
        if restores:  # pair never routed - undo the detach on the staged board
            staged.write_text(original_text, encoding="utf-8")


def _route_single_item(ctx: dict, kind: str, nets: list[str],
                       extra: list[str], per_net_meta: dict,
                       net_clearances: Path | None = None
                       ) -> tuple[list, list]:
    """Shared route.py runner for power/rf. Returns (facts, violations).
    net_clearances: this run's --net-clearances file (default: the shared
    ctx map).

    Iteration ladder (T6, LEARNINGS 1433/1504): when KRT reports
    'No route found after N iterations' and the Coverage diagnostic blames a
    mostly-static frontier, the failed nets get ONE retry at 4M iterations -
    the scripted form of the fix that routed both carrier long hauls first
    try, replacing ~10 manual rip-set attempts."""
    staged = ctx["staged"]
    ncl = net_clearances or ctx.get("net_clearances")
    out = _next_out(ctx, kind)
    sink: list[str] = []
    summary = run_krt(ctx["krt"], "route.py", staged, out,
                      ["--nets", *nets] + extra,
                      ctx["floors"], ctx["fab_file"], ctx["grid_step"],
                      ctx["timeout_s"],
                      net_clearances=ncl,
                      stdout_sink=sink)
    os.replace(out, staged)  # keep partial successes; failures become violations
    stdout = sink[0] if sink else ""
    share = coverage_static_share(stdout)
    reasons = {net: _net_failed(summary, net) for net in nets}
    failed = sorted(n for n, r in reasons.items() if r)
    retry = None
    if failed and NO_ROUTE_RE.search(stdout) \
            and (share is None or share >= STATIC_RETRY_SHARE):
        retry = {"nets": failed, "static_share": share,
                 "max_iterations": RETRY_MAX_ITERATIONS, "kept": False}
        out2 = _next_out(ctx, f"{kind}_hi")
        try:
            s2 = run_krt(ctx["krt"], "route.py", staged, out2,
                         ["--nets", *failed] + extra
                         + ["--max-iterations", str(RETRY_MAX_ITERATIONS),
                            "--max-probe-iterations",
                            str(RETRY_MAX_PROBE_ITERATIONS)],
                         ctx["floors"], ctx["fab_file"], ctx["grid_step"],
                         ctx["timeout_s"],
                         net_clearances=ncl)
        except CheckError as exc:
            retry["error"] = str(exc)[:200]
        else:
            os.replace(out2, staged)
            retry["kept"] = True
            for net in failed:
                reasons[net] = _net_failed(s2, net)
    bg2 = geom.BoardGeom.from_file(staged)
    facts, violations = [], []
    for net in nets:
        reason = reasons[net]
        layers = sorted({t.layer for t in bg2.tracks_of(net=net)})
        if reason is None:
            fact = {"kind": kind, "nets": [net], **per_net_meta.get(net, {}),
                    "layers_used": layers,
                    "krt": _slim(summary, ("successful", "failed",
                                           "multipoint_pads_connected",
                                           "multipoint_pads_total",
                                           "total_vias", "total_time"))}
            if retry and net in retry["nets"]:
                fact["iteration_retry"] = True
            facts.append(fact)
        else:
            violations.append(violation(
                SCRIPT, "error", None, None, net, [],
                f"critical {kind} net {net} failed to route: {reason}",
                SCRIPT, kind="critical_route_failed", reason=reason,
                layers_used=layers, static_share=share,
                iteration_retry=bool(retry)))
    if retry and facts:
        facts[0]["krt_retry"] = retry
    return facts, violations


_SEG_BLOCK_RE = re.compile(r"\(segment\b.*?\(uuid \"([0-9a-f-]+)\"\)\s*\)",
                           re.S)
_SEG_FIELD_RES = {
    "start": re.compile(r"\(start ([-\d.]+) ([-\d.]+)\)"),
    "end": re.compile(r"\(end ([-\d.]+) ([-\d.]+)\)"),
    "width": re.compile(r"\(width ([-\d.]+)\)"),
    "layer": re.compile(r"\(layer \"([^\"]+)\"\)"),
    "net": re.compile(r"\(net \"((?:[^\"\\]|\\.)*)\"\)"),
}
CRUMB_MM = 0.05


def normalize_power_widths(staged: Path, specs: list[dict]) -> dict:
    """Post-KRT width discipline (S11 acceptance finding): KRT can emit a few
    base-width segments and sub-grid crumbs on power nets even with
    --no-power-tap-neckdown, which violate rules_gen's aiee_pwr_width_* DRU
    floors. Raise segments below the net's IPC-2152 minimum (the DRU floor -
    the un-margined check_current/rules_gen number) to exactly that floor
    (remove + re-add via route_edit - atomic, verified) and drop crumbs
    (< 0.05 mm). Targeting the floor, not the 1.5x-margined command, keeps
    legitimate fine-pitch neckdowns of low-current nets intact.
    Returns {"widened": n, "crumbs_removed": n}."""
    want = {s["net"]: s["ipc_min_mm"] for s in specs}
    text = staged.read_text(encoding="utf-8")
    ops = []
    widened = crumbs = 0
    for m in _SEG_BLOCK_RE.finditer(text):
        block, uid = m.group(0), m.group(1)
        net_m = _SEG_FIELD_RES["net"].search(block)
        if not net_m or net_m.group(1) not in want:
            continue
        net = net_m.group(1)
        w = float(_SEG_FIELD_RES["width"].search(block).group(1))
        if w >= want[net] - 1e-3:
            continue
        sx, sy = map(float, _SEG_FIELD_RES["start"].search(block).groups())
        ex, ey = map(float, _SEG_FIELD_RES["end"].search(block).groups())
        layer = _SEG_FIELD_RES["layer"].search(block).group(1)
        ops.append({"op": "remove", "uuid": uid})
        if math.hypot(ex - sx, ey - sy) < CRUMB_MM:
            crumbs += 1
            continue
        ops.append({"op": "add_track", "start": [sx, sy], "end": [ex, ey],
                    "width": want[net], "layer": layer, "net": net})
        widened += 1
    if ops:
        route_edit.apply_ops(staged, ops)
    return {"widened": widened, "crumbs_removed": crumbs}


def power_net_clearances(ctx: dict, obstacle_nets: list[str]) -> Path | None:
    """--net-clearances file for the power route.py runs: the shared map with
    `obstacle_nets` (nets already carrying tracks - the diff pairs, off-grid)
    raised by grid_step / 2 over their own clearance (else the floor). KRT
    keeps an obstacle out by that obstacle net's clearance, and its
    fractional-margin shortfall (module docstring) is under half a diagonal
    grid phase, so half a grid step covers it. Only those nets are raised:
    raising the routed power nets instead cost +3V3 a fine-pitch pad tap.
    Returns the shared map unchanged when there is nothing to raise."""
    path = ctx.get("net_clearances")
    if not obstacle_nets:
        return path
    ncl = json.loads(path.read_text(encoding="utf-8")) if path else {}
    for net in obstacle_nets:
        base = max(ncl.get(net, 0.0), ctx["floors"]["clearance"])
        ncl[net] = round(base + ctx["grid_step"] / 2.0, 4)
    out = ctx["work"] / "net_clearances_power.json"
    out.write_text(json.dumps(ncl, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    return out


def route_power_item(ctx: dict, specs: list[dict]) -> tuple[list, list]:
    bg = geom.BoardGeom.from_file(ctx["staged"])
    live = [s for s in specs if s["net"] in bg.nets]
    missing = [s for s in specs if s["net"] not in bg.nets]
    # A power net that a plane carries is NOT trunk-routed on the outer
    # layers: the plane IS the trunk (golden usbbuck4 pattern - +3V3 lives on
    # In2). Outer trunk tracks + their vias fragment the plane locally (S11
    # acceptance: a routed +3V3 trunk starved J2's thermal spokes onto an
    # In2 island - error starved_thermal). PTH pads reach the plane through
    # thermal spokes; SMD pads get vias from stitch_vias (next chain step).
    plane_carried = [s for s in live
                     if any(z.filled for z in bg.zones_of(net=s["net"]))]
    live = [s for s in live if s not in plane_carried]
    violations = [violation(
        SCRIPT, "warning", None, None, s["net"], [],
        f"power net {s['net']} not on the board (stale constraints?)",
        SCRIPT, kind="critical_missing_net") for s in missing]
    plane_facts = [
        {"kind": "power", "nets": [s["net"]], "plane_carried": True,
         "current_a": s["current_a"],
         "plane_layers": sorted(
             ly for z in bg.zones_of(net=s["net"])
             for ly, polys in z.fills.items() if polys),
         "note": "plane is the trunk; SMD pads stitched by stitch_vias, "
                 "PTH pads via thermal spokes"}
        for s in plane_carried]
    if not live:
        return plane_facts, violations
    outer = _outer_layers(bg)
    costs = ["1"] if len(outer) == 1 else ["1", "2"]
    # Per-net neckdown policy (S11 acceptance finding): KRT necks power taps
    # down to the 0.2 mm base width near fine-pitch pads. That is legal only
    # for nets whose IPC-2152/DRU floor is <= 0.2 (e.g. +3V3 @0.4A -> 0.20);
    # nets with a fatter floor (+5V @0.5A -> 0.25) must route with
    # --no-power-tap-neckdown or the neck violates aiee_pwr_width_*. A pad
    # that cannot take the un-necked width then fails loudly.
    relaxed = [s for s in live if s["ipc_min_mm"] <= BASE_TRACK_WIDTH + 1e-3]
    strict = [s for s in live if s["ipc_min_mm"] > BASE_TRACK_WIDTH + 1e-3]
    power = {s["net"] for s in specs}
    ncl = power_net_clearances(ctx, sorted(
        {tk.net for tk in bg.tracks_of() if tk.net} - power))
    facts, vs = list(plane_facts), []
    for group, extra in ((strict, ["--no-power-tap-neckdown"]), (relaxed, [])):
        if not group:
            continue
        nets = [s["net"] for s in group]
        widths = [f"{s['width_mm']:g}" for s in group]
        f, v = _route_single_item(
            ctx, "power", nets,
            ["--power-nets", *nets, "--power-nets-widths", *widths,
             "--track-width", f"{BASE_TRACK_WIDTH:g}", *extra,
             "--layers", *outer, "--layer-costs", *costs],
            {s["net"]: {"current_a": s["current_a"],
                        "width_mm": s["width_mm"],
                        "ipc_min_mm": s["ipc_min_mm"],
                        "neckdown": not extra} for s in group},
            ncl)
        facts.extend(f)
        vs.extend(v)
    norm = normalize_power_widths(ctx["staged"], live)
    if facts:
        facts[0]["width_normalization"] = norm
    return facts, violations + vs


def route_rf_item(ctx: dict, specs: list[dict]) -> tuple[list, list]:
    bg = geom.BoardGeom.from_file(ctx["staged"])
    live = [s for s in specs if s["net"] in bg.nets]
    missing = [s for s in specs if s["net"] not in bg.nets]
    violations = [violation(
        SCRIPT, "warning", None, None, s["net"], [],
        f"rf net {s['net']} not on the board (stale constraints?)",
        SCRIPT, kind="critical_missing_net") for s in missing]
    facts = []
    outer = _outer_layers(bg)
    costs = ["1"] if len(outer) == 1 else ["1", "3"]
    for s in live:
        w = rf_width(bg, s["impedance_ohm"])
        f, vs = _route_single_item(
            ctx, "rf", [s["net"]],
            ["--track-width", f"{w:g}", "--layers", *outer,
             "--layer-costs", *costs],
            {s["net"]: {"impedance_ohm": s["impedance_ohm"], "width_mm": w,
                        "fence_handoff":
                            f"stitch_vias.py --fence-net {s['net']}"}})
        facts.extend(f)
        violations.extend(vs)
    return facts, violations


# ------------------------------------------------------------ verification

def drc_by_check(report: dict, severity: str | None = None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in report["violations"]:
        if severity is not None and v.get("severity") != severity:
            continue
        counts[v["check"]] = counts.get(v["check"], 0) + 1
    return counts


def drc_delta(before: dict, after: dict) -> dict:
    """Per-check-type count changes. 'new' = ERROR-severity types that
    INCREASED (the rollback trigger); 'new_warnings' = increased warning
    types, reported only - post-route hygiene (dangling crumbs etc.) is
    route_cleanup's job later in the P7 chain, not a reason to discard a
    good critical route."""
    b, a = drc_by_check(before), drc_by_check(after)
    be = drc_by_check(before, "error")
    ae = drc_by_check(after, "error")
    new = {k: c - be.get(k, 0) for k, c in ae.items() if c > be.get(k, 0)}
    bw = drc_by_check(before, "warning")
    aw = drc_by_check(after, "warning")
    new_w = {k: c - bw.get(k, 0) for k, c in aw.items() if c > bw.get(k, 0)}
    return {"before": b, "after": a, "new": new, "new_warnings": new_w}


def diffpair_postcheck(staged: Path, routed_pairs: list[dict]) -> tuple[list, list]:
    """S5 check_diffpair on the routed pairs only. Returns (violations, facts)."""
    if not routed_pairs:
        return [], []
    bg = geom.BoardGeom.from_file(staged)
    violations, checked = [], []
    for fact in routed_pairs:
        p, n = fact["nets"]
        spec = {"p": p, "n": n,
                "gap_mm": round(fact["width_mm"] + fact["gap_mm"], 4)}
        for k in ("max_skew_mm", "max_uncoupled_mm", "coupling_factor"):
            if k in fact.get("constraint", {}):
                spec[k] = fact["constraint"][k]
        vs, cf = check_diffpair.check_pair(bg, spec)
        violations.extend(vs)
        checked.append(cf)
    return violations, checked


# ------------------------------------------------------------ CLI

def _sidecar(pcb: Path, explicit: str | None, name: str) -> Path | None:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise CheckError(f"{name} not found: {p}")
        return p
    cand = pcb.parent / name
    return cand if cand.is_file() else None


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--constraints", default=None,
                    help="constraints.json (default: next to the board)")
    ap.add_argument("--only", choices=list(DEFAULT_ORDER), default=None)
    ap.add_argument("--plan", default=None,
                    help='plan.json {"order": ["diff","rf","power"]}')
    ap.add_argument("--pad-window", action="store_true",
                    help="no-routing probe: widest connectable track per "
                         "pad of each power net vs its DRU width floor "
                         "(exit 1 when a floor is geometrically unmeetable)")
    ap.add_argument("--nets", default=None,
                    help="--pad-window net list (comma; default: "
                         "constraints['power'] nets)")
    ap.add_argument("--grid-step", type=float, default=0.05,
                    help="KRT routing grid (mm); 0.05 keeps 0.5 mm-pitch tap "
                         "corridors routable")
    ap.add_argument("--timeout-s", type=int, default=600,
                    help="per-KRT-invocation process timeout")
    ap.add_argument("--work-dir", default=None)
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    pcb = Path(args.pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    cons_path = _sidecar(pcb, args.constraints, "constraints.json")
    constraints = checklib.load_json(cons_path, "constraints") if cons_path else {}
    if args.pad_window:   # pure geometry probe: no KRT, board untouched
        return pad_window_report(pcb, constraints, args.nets), args.out_report
    plan = checklib.load_json(args.plan, "plan") if args.plan else None
    order = plan_order(args.only, plan)

    bg0 = geom.BoardGeom.from_file(pcb)
    cu_outer = bg0.stackup.copper_thickness.get(bg0.copper_layers[0], 0.035)

    d_specs = diff_specs(constraints, bg0.nets)
    pair_nets = {x for s in d_specs for x in (s["p"], s["n"])}
    r_specs = rf_specs(constraints, pair_nets)
    p_specs = [s for s in power_specs(constraints, cu_outer)
               if s["net"] not in pair_nets]
    by_kind = {"diff": d_specs, "rf": r_specs, "power": p_specs}
    todo = [(k, by_kind[k]) for k in order if by_kind[k]]
    skipped = [{"kind": k, "count": len(by_kind[k]), "why": "not in --only/plan"}
               for k in DEFAULT_ORDER if by_kind[k] and k not in order]

    if not todo:
        payload = checklib.report(SCRIPT, str(pcb), [], facts={
            "routed": [], "skipped": skipped, "board_updated": False,
            "note": "no critical nets declared or selected"})
        return payload, args.out_report

    cli, krt = _tools()
    # .resolve(): KRT subprocesses run with cwd=<plugins dir>, so a relative
    # work dir makes KRT die with "fab-overrides file not found" -> "no
    # JSON_SUMMARY" (LEARNINGS 1318, live on lumina-carrier).
    work = (Path(args.work_dir).resolve() if args.work_dir
            else pcb.parent / "route_critical")
    routelib.fresh_work_dir(work)
    staged = _stage_board(pcb, work)
    floors = grading_floors(work / (pcb.stem + ".kicad_pro"))
    ctx = {"staged": staged, "work": work, "krt": krt, "floors": floors,
           "fab_file": write_fab_overrides(work, floors),
           "grid_step": args.grid_step, "timeout_s": args.timeout_s}
    # Per-net clearances for KRT (T6, LEARNINGS 1522): --clearance is a CAP
    # on KRT's auto-read netclass map and KRT cannot read a .kicad_dru, so
    # HV clearance must travel in an explicit --net-clearances file.
    ncl = build_net_clearances(work / (pcb.stem + ".kicad_pro"),
                               work / (pcb.stem + ".kicad_dru"), bg0.nets)
    if ncl:
        ncl_file = work / "net_clearances.json"
        ncl_file.write_text(json.dumps(ncl, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        ctx["net_clearances"] = ncl_file

    # fresh fills before the baseline so stale pours don't skew the DRC delta
    if bg0.zones_of():
        kc.run_drc(cli, staged, refill=True, save_board=True)
    before = kc.run_drc(cli, staged, all_track_errors=True)

    routed, violations = [], []
    for kind, specs in todo:
        if kind == "diff":
            for spec in specs:
                fact, v = route_diff_item(ctx, spec)
                if fact:
                    for k in ("max_skew_mm", "max_uncoupled_mm", "coupling_factor"):
                        if k in spec:
                            fact.setdefault("constraint", {})[k] = spec[k]
                    routed.append(fact)
                if v:
                    violations.append(v)
        elif kind == "power":
            facts, vs = route_power_item(ctx, specs)
            routed.extend(facts)
            violations.extend(vs)
        elif kind == "rf":
            facts, vs = route_rf_item(ctx, specs)
            routed.extend(facts)
            violations.extend(vs)

    # verification on the staged result, before the original is touched
    if bg0.zones_of():
        kc.run_drc(cli, staged, refill=True, save_board=True)
    after = kc.run_drc(cli, staged, all_track_errors=True)
    delta = drc_delta(before, after)
    if delta["new"]:
        raise CheckError(
            "routing introduced new DRC errors (rolled back, original board "
            f"untouched): {delta['new']}; work dir kept at {work}")

    dp_viol, dp_facts = diffpair_postcheck(
        staged, [f for f in routed if f["kind"] == "diff"])
    violations.extend(dp_viol)

    board_updated = bool(routed)
    if board_updated:
        routelib.swap_in(staged, pcb)

    payload = checklib.report(SCRIPT, str(pcb), violations, facts={
        "routed": routed,
        "skipped": skipped,
        "drc_delta": delta,
        "diffpair_check": "pass" if not dp_viol else "violations",
        "diffpair_facts": dp_facts,
        "floors": floors,
        "net_clearances": ncl,
        "board_updated": board_updated,
        "work_dir": str(work),
    })
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
