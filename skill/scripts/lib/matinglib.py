"""matinglib - can every mating connector on the board actually be mated?

Shared by the P6 place gate (place_metrics.py, family "mating") and the P8
verify gate (check_mating.py), so the fault fails at placement and stays
failed if a later edit brings it back.

Which footprints are mating connectors, and their plug geometry, come from
reference/connector_mating.yaml (see its header). Three violation kinds, all
error severity, each naming the connector in `connector` (refs also lists
the blocking parts):

  mating_faces_inward  a horizontal connector's mouth faces away from its
                       nearest board edge (PCB-0021-A J4: USB-A with its back
                       at the edge and its mouth toward L1).
  mating_mouth_inset   the mouth faces the nearest edge but sits more than
                       mouth_tol_mm inside it.
  mating_zone_blocked  a part on the connector's side sits in the insertion
                       zone in front of the mouth (horizontal), or a part
                       taller than tall_mm sits within finger_mm of a
                       vertical connector.

  mating_model_reversed  a horizontal connector's 3D model points the other
                       way from its copper: the model lies behind the contact
                       pads, so every render shows the mouth facing into the
                       board while the pads face the edge, or the other way
                       round (PCB-0023-A J101/J102/J501: an edge-launch SMA
                       whose model is turned 180 degrees).

and one warning, mating_direction_unknown, for a horizontal connector whose
pad layout is symmetric about its body: it asks for a `mouth` entry in the
table rather than guessing.

Mouth direction is read from the pad layout: the contact pads (the most
common pad size) sit at the back of the housing and the body extends toward
the mouth, so the mouth points from the contact-pad centroid toward the
courtyard centroid. The table's `mouth` map overrides that per footprint.

A connector whose SMD pads sit on both outer copper layers straddles the
board edge (an edge-launch SMA, a card-edge socket), so it is checked as
horizontal even when its name matched a vertical family: a name is a hint,
the copper is the part.

The 3D model's direction is read against the same contact pads: its
bounding box, put through the footprint's model offset/rotate/scale the way
KiCad's 3D viewer does, must reach ahead of them toward the mouth. A box
cannot tell a model turned round from one pushed back, so only a model that
lies wholly behind the contacts fails (model_reversed); one that is merely
offset passes. Only a .wrl is read (beside the board, or the .wrl twin of a .step
model); a connector without one is listed in the facts, never failed.

Part heights (vertical rule) come from the 3D model: a "-H<mm>" in the model
or footprint name, else the highest vertex of a .wrl model found beside the
board (VRML units are 2.54 mm). A part with no readable height is listed in
the facts, never failed.
"""
from __future__ import annotations

import itertools
import math
import re
from functools import lru_cache
from pathlib import Path

import yaml
from shapely.geometry import LineString, box

import checklib
from geom import _is_node, _kid, _kids, _nums, _strs, _tok

SOURCE = "matinglib"
TABLE = Path(__file__).resolve().parents[2] / "reference" / "connector_mating.yaml"
EPS_AREA = 0.01
AXES = {"+x": (1.0, 0.0), "-x": (-1.0, 0.0), "+y": (0.0, 1.0), "-y": (0.0, -1.0)}
KINDS = ("mating_faces_inward", "mating_mouth_inset", "mating_zone_blocked",
         "mating_model_reversed", "mating_direction_unknown")
# a horizontal connector's 3D model counts as reversed when it reaches less
# than MODEL_AHEAD_MM ahead of the contact pads along the copper's mouth
# while it is at least MODEL_MIN_LEN_MM long on that axis (model_reversed)
MODEL_AHEAD_MM = 1.0
MODEL_MIN_LEN_MM = 3.0


@lru_cache(maxsize=1)
def load_table(path: str = str(TABLE)) -> dict:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    d = doc.get("defaults", {})
    fams = []
    for fam in doc.get("families", []):
        fams.append({**d, **fam, "re": re.compile(fam["match"], re.I)})
    mouth = [(re.compile(k, re.I), v) for k, v in (doc.get("mouth") or {}).items()]
    return {"families": fams, "mouth": mouth}


def _name(fpid: str) -> str:
    return fpid.split(":", 1)[-1]


def family_of(fpid: str, table: dict | None = None) -> dict | None:
    table = table or load_table()
    name = _name(fpid)
    for fam in table["families"]:
        if fam["re"].search(name):
            return fam
    return None


def _snap(dx: float, dy: float) -> str:
    if abs(dx) >= abs(dy):
        return "+x" if dx >= 0 else "-x"
    return "+y" if dy >= 0 else "-y"


def contact_centroid(f) -> tuple[float, float] | None:
    """Local centroid of the contact pads (the most common pad size): the
    back of the housing."""
    pads = [p for p in f.pads if p.number]
    if not pads:
        return None
    sizes: dict = {}
    for p in pads:
        k = (round(p.size[0], 2), round(p.size[1], 2), p.through)
        sizes.setdefault(k, []).append(p)
    contacts = max(sizes.values(), key=len)
    return (sum(p.local[0] for p in contacts) / len(contacts),
            sum(p.local[1] for p in contacts) / len(contacts))


def mouth_local(f, table: dict | None = None) -> str | None:
    """The mouth direction in the footprint's local frame, or None when the
    pad layout is symmetric about the body (nothing to read it from)."""
    table = table or load_table()
    for rx, d in table["mouth"]:
        if rx.search(_name(f.fpid)):
            return d
    cc = contact_centroid(f)
    if cc is None:
        return None
    cx, cy = cc
    body = f.extents_local().centroid
    dx, dy = body.x - cx, body.y - cy
    if math.hypot(dx, dy) < 0.3:
        return None
    return _snap(dx, dy)


def _to_abs_dir(f, d: str) -> tuple[float, float]:
    ux, uy = AXES[d]
    o = f.to_abs((0.0, 0.0))
    p = f.to_abs((ux, uy))
    return AXES[_snap(p[0] - o[0], p[1] - o[1])]


def _span(poly, u: tuple[float, float]) -> tuple[float, float]:
    vals = [x * u[0] + y * u[1] for x, y in poly.exterior.coords]
    return min(vals), max(vals)


def _edge_gap(outline, body, u) -> float:
    """Distance from the body's face in direction u to the board edge it
    faces (negative when the face overhangs that edge)."""
    c = body.centroid
    lo, hi = _span(body, u)
    ray = LineString([(c.x, c.y), (c.x + u[0] * 1e4, c.y + u[1] * 1e4)])
    hit = ray.intersection(outline.exterior)
    pts = [g.centroid for g in getattr(hit, "geoms", [hit]) if not g.is_empty]
    ts = [p.x * u[0] + p.y * u[1] for p in pts]
    ts = [t for t in ts if t > c.x * u[0] + c.y * u[1]]
    if not ts:  # centroid already off the board in this direction
        return lo - hi
    return min(ts) - hi


def _zone(body, u, gap: float, plug: float, grip: float):
    """Insertion zone: the mouth's own width from the mouth face to the board
    edge (the plug slides over that ledge), then that width plus grip each
    side out to plug + grip past the edge, where the hand holds it."""
    v = (-u[1], u[0])
    _, front = _span(body, u)
    wlo, whi = _span(body, v)
    edge = front + max(gap, 0.0)

    def rect(a0, a1, b0, b1):
        pts = [(a * u[0] + b * v[0], a * u[1] + b * v[1])
               for a, b in ((a0, b0), (a1, b1))]
        return box(min(pts[0][0], pts[1][0]), min(pts[0][1], pts[1][1]),
                   max(pts[0][0], pts[1][0]), max(pts[0][1], pts[1][1]))

    zone = rect(edge, edge + plug + grip, wlo - grip, whi + grip)
    if edge > front:
        zone = zone.union(rect(front, edge, wlo, whi))
    return zone


# ------------------------------------------------------------ part heights

_H_RE = re.compile(r"[-_]H(\d+(?:\.\d+)?)(?![\d.])", re.I)


def _xyz(node, key: str, default: list[float]) -> list[float]:
    k = _kid(node, key)
    if k is None or _kid(k, "xyz") is None:
        return default
    v = _nums(_kid(k, "xyz"))
    return v if len(v) == 3 else default


def board_meta(pcb: Path) -> dict[str, dict]:
    """ref -> {"model": (first 3D model path, its rotate xyz) or None,
    "offset"/"scale": that model's xyz, "smd_sides": the outer copper layers
    ({"F", "B"}) its SMD pads sit on} from the board file."""
    import sexpdata
    tree = sexpdata.loads(Path(pcb).read_text(encoding="utf-8"))
    out = {}
    for fp in _kids(tree, "footprint"):
        ref = None
        for prop in _kids(fp, "property"):
            s = _strs(prop)
            if len(s) >= 2 and s[0] == "Reference":
                ref = s[1]
        if not ref:
            continue
        sides = set()
        for pad in _kids(fp, "pad"):
            if "smd" not in [_tok(t) for t in pad[1:4] if not _is_node(t)]:
                continue
            lay = _kid(pad, "layers")
            for name in (_strs(lay) if lay is not None else []):
                if name in ("F.Cu", "B.Cu"):
                    sides.add(name[0])
        meta = {"model": None, "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0], "smd_sides": frozenset(sides)}
        m = _kid(fp, "model")
        if m is not None and _strs(m):
            meta["model"] = (_strs(m)[0], _xyz(m, "rotate", [0.0, 0.0, 0.0]))
            meta["scale"] = _xyz(m, "scale", [1.0, 1.0, 1.0])
            if _kid(m, "offset") is not None:
                meta["offset"] = _xyz(m, "offset", [0.0, 0.0, 0.0])
            else:  # KiCad 5: (at (xyz)) in inches
                meta["offset"] = [v * 25.4 for v in
                                  _xyz(m, "at", [0.0, 0.0, 0.0])]
        out[ref] = meta
    return out


def model_paths(pcb: Path) -> dict[str, tuple[str, list[float]]]:
    """ref -> (first 3D model path, its rotate xyz) from the board file."""
    return {r: m["model"] for r, m in board_meta(pcb).items() if m["model"]}


def straddles(meta: dict | None) -> bool:
    """SMD pads on both outer copper layers: the part clamps the board edge
    between its legs, so its plug goes in parallel to the board."""
    return bool(meta) and meta["smd_sides"] >= {"F", "B"}


@lru_cache(maxsize=512)
def _wrl_bbox(path: str) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    """(min xyz, max xyz) of a .wrl's vertices in mm (VRML unit 2.54 mm);
    transforms inside the file are not applied."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    xs, ys, zs = [], [], []
    for blk in re.findall(r"point\s*\[([^\]]*)\]", text):
        v = [float(x) for x in
             re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", blk)]
        n = len(v) // 3 * 3
        xs.extend(v[0:n:3])
        ys.extend(v[1:n:3])
        zs.extend(v[2:n:3])
    if not zs:
        return None
    return (tuple(min(a) * 2.54 for a in (xs, ys, zs)),
            tuple(max(a) * 2.54 for a in (xs, ys, zs)))


def _wrl_height(path: str) -> float | None:
    bb = _wrl_bbox(path)
    return bb[1][2] if bb else None


def find_wrl(model_path: str, pcb: Path) -> Path | None:
    """The .wrl a board's model path points at: the path itself, else the
    same name under the workspace's lib/*.3dshapes/ (a path baked in another
    checkout), trying the .wrl twin of a .step model."""
    raw = model_path.replace("${KIPRJMOD}", str(Path(pcb).parent))
    p = Path(raw)
    if p.suffix.lower() in (".step", ".stp"):
        p = p.with_suffix(".wrl")
    if p.suffix.lower() != ".wrl":
        return None
    ws = Path(pcb).resolve().parent.parent
    for cand in [p, *ws.glob(f"lib/*.3dshapes/{p.name}")]:
        if cand.is_file():
            return cand
    return None


def _rot3(p, axis: int, deg: float):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = p
    if axis == 0:
        return (x, y * c - z * s, y * s + z * c)
    if axis == 1:
        return (x * c + z * s, y, -x * s + z * c)
    return (x * c - y * s, x * s + y * c, z)


def model_corners_local(f, meta: dict | None,
                        pcb: Path) -> list[tuple[float, float]] | None:
    """Where the 8 corners of the 3D model's bounding box land in the
    footprint's local frame (as the file stores it), or None when no .wrl
    is found.

    KiCad's 3D viewer places a model as offset + Rz(-rz).Ry(-ry).Rx(-rx).
    scale . p in a y-up frame, so footprint-local y is the negated 3D y. A
    back-side footprint's file locals are the library's mirrored in y and its
    model keeps its library transform, so there the 3D y is taken as is
    (checked against kicad-cli renders: PCB-0023-A's SMA, ry 270, and
    PCB-0025-A's back-side barrel jack, rz 90)."""
    if not meta or not meta["model"]:
        return None
    wrl = find_wrl(meta["model"][0], pcb)
    bb = _wrl_bbox(str(wrl)) if wrl else None
    if bb is None:
        return None
    rx, ry, rz = meta["model"][1]
    out = []
    for corner in itertools.product(*zip(*bb)):
        p = tuple(c * s for c, s in zip(corner, meta["scale"]))
        p = _rot3(_rot3(_rot3(p, 0, -rx), 1, -ry), 2, -rz)
        x, y = p[0] + meta["offset"][0], p[1] + meta["offset"][1]
        out.append((x, y) if f.side == "back" else (x, -y))
    return out


def model_span_mm(f, d: str, meta: dict | None,
                  pcb: Path) -> tuple[float, float] | None:
    """How far the 3D model reaches behind (first, negative) and ahead of
    (second) the contact pads along the copper's mouth direction `d`."""
    corners = model_corners_local(f, meta, pcb)
    cc = contact_centroid(f)
    if corners is None or cc is None:
        return None
    u = AXES[d]
    along = [(x - cc[0]) * u[0] + (y - cc[1]) * u[1] for x, y in corners]
    return min(along), max(along)


def model_reversed(span: tuple[float, float] | None) -> bool:
    """The model lies behind the contact pads: it reaches less than
    MODEL_AHEAD_MM ahead of them while it is MODEL_MIN_LEN_MM or longer.

    Only the bounding box is read, and that cannot tell a model turned
    round from one pushed back: a box is the same shape either way. So
    this asks the one thing a box does answer. A connector's contacts sit at
    the back of its housing, so a model of any length must reach forward of
    them. A model that is merely offset still does (PCB-0025-A J101 sits
    5.6 mm too far back and reaches 5 mm ahead; PCB-0016-A J1 2.2 mm)."""
    if span is None:
        return False
    lo, hi = span
    return hi < MODEL_AHEAD_MM and hi - lo >= MODEL_MIN_LEN_MM


def part_height(f, model: tuple[str, list[float]] | None,
                pcb: Path) -> float | None:
    for name in ([Path(model[0]).stem] if model else []) + [_name(f.fpid)]:
        m = _H_RE.search(name)
        if m:
            return float(m.group(1))
    if not model or not model[0].lower().endswith(".wrl"):
        return None
    if any(abs(r) > 1e-6 for r in model[1][:2]):
        return None  # tilted model: its z is not the board's z
    raw = model[0].replace("${KIPRJMOD}", str(Path(pcb).parent))
    base = Path(raw).name
    ws = Path(pcb).resolve().parent.parent
    for cand in [Path(raw), *ws.glob(f"lib/*.3dshapes/{base}")]:
        if cand.is_file():
            return _wrl_height(str(cand))
    return None


# ------------------------------------------------------------ the check

def violations(model, pcb: Path | None = None) -> tuple[list[dict], list[dict]]:
    """(violations, facts) for every mating connector on `model`
    (a placelib.PlaceModel)."""
    table = load_table()
    pcb = Path(pcb or model.path)
    outline = model.outline
    fps = model.footprints
    out: list[dict] = []
    facts: list[dict] = []
    fam_of = {r: family_of(f.fpid, table) for r, f in fps.items()}
    metas = board_meta(pcb) if pcb.is_file() and any(fam_of.values()) else {}
    models = {r: m["model"] for r, m in metas.items() if m["model"]}

    # the name says vertical but the copper clamps the board edge: horizontal
    entry_of = {r: ("horizontal" if fam["entry"] == "vertical"
                    and straddles(metas.get(r)) else fam["entry"])
                for r, fam in fam_of.items() if fam is not None}

    for ref in sorted(fps):
        f, fam = fps[ref], fam_of[ref]
        if fam is None:
            continue
        body = f.extents_abs()
        meta = metas.get(ref)
        entry = entry_of[ref]
        fact = {"ref": ref, "family": fam["family"], "entry": entry}
        if entry != fam["entry"]:
            fact["entry_from"] = "pads on both outer layers"
        facts.append(fact)
        others = [o for r, o in fps.items() if r != ref and o.side == f.side]

        if entry == "horizontal":
            d = mouth_local(f, table)
            if d is None:
                fact["mouth"] = None
                out.append(checklib.violation(
                    "place", "warning", f.center_abs(), None, None, [ref],
                    f"{ref} ({fam['family']}): cannot read its mouth "
                    "direction from the pad layout - add a `mouth` entry "
                    "to reference/connector_mating.yaml", SOURCE,
                    kind="mating_direction_unknown", connector=ref))
                continue
            u = _to_abs_dir(f, d)
            gap = _edge_gap(outline, body, u)
            gaps = {k: _edge_gap(outline, body, a) for k, a in AXES.items()}
            nearest = min(gaps, key=gaps.get)
            facing = _snap(*u)
            fact.update(mouth=facing, gap_mm=checklib.rnd(gap, 2),
                        nearest_edge=nearest)
            c = f.center_abs()
            # brief: "A connector whose mating direction is horizontal must
            # face the nearest board edge, with its mouth at or over that
            # edge." Mouth more than the tolerance short of the edge it
            # faces -> fail; inward when that edge is not the nearest one.
            if gap > fam["mouth_tol_mm"]:
                inward = nearest != facing
                why = (f"its nearest edge is {nearest} "
                       f"({max(gaps[nearest], 0):.1f} mm) but its mouth "
                       f"faces {facing}, into {gap:.1f} mm of board"
                       if inward else
                       f"its mouth is {gap:.1f} mm inside the {facing} edge "
                       f"(limit {fam['mouth_tol_mm']} mm)")
                out.append(checklib.violation(
                    "place", "error", c, None, None, [ref],
                    f"{ref} ({fam['family']}) cannot be mated: {why}",
                    SOURCE,
                    kind="mating_faces_inward" if inward
                    else "mating_mouth_inset",
                    connector=ref, mouth=facing, nearest_edge=nearest,
                    gap_mm=checklib.rnd(gap, 2)))
            span = model_span_mm(f, d, meta, pcb)
            fact["model_span_mm"] = None if span is None \
                else [checklib.rnd(v, 2) for v in span]
            if model_reversed(span):
                back = _snap(-u[0], -u[1])
                out.append(checklib.violation(
                    "place", "error", c, None, None, [ref],
                    f"{ref} ({fam['family']}): its 3D model points the "
                    f"other way from its copper - the pads put the mouth "
                    f"at {facing} but the model lies behind them, "
                    f"{-span[0]:.1f} mm back to {-span[1]:.1f} mm, so every "
                    f"render shows it facing {back}. One of the two is "
                    "backwards: check the datasheet, then fix the model's "
                    "rotate in the footprint (or the footprint)", SOURCE,
                    kind="mating_model_reversed", connector=ref,
                    mouth=facing, model_mouth=back,
                    model_span_mm=[checklib.rnd(v, 2) for v in span]))
            zone = _zone(body, u, gap, fam["plug_mm"], fam["grip_mm"])
            blockers = sorted(o.ref for o in others
                              if o.extents_abs().intersects(zone)
                              and o.precise_extents_abs().intersection(
                                  zone).area > EPS_AREA)
            fact["blocked_by"] = blockers
            if blockers:
                z = zone.centroid
                out.append(checklib.violation(
                    "place", "error", (z.x, z.y), None, None,
                    [ref, *blockers],
                    f"{ref} ({fam['family']}): "
                    f"{', '.join(blockers)} sit in the plug's insertion zone "
                    f"in front of its mouth ({fam['plug_mm']} mm plug + "
                    f"{fam['grip_mm']} mm grip)", SOURCE,
                    kind="mating_zone_blocked", connector=ref,
                    mouth=facing, blocked_by=blockers))
        else:
            zone = body.buffer(fam["finger_mm"])
            tall, unknown = [], []
            for o in others:
                if entry_of.get(o.ref) == "vertical":
                    continue
                if o.precise_extents_abs().intersection(zone).area <= EPS_AREA:
                    continue
                h = part_height(o, models.get(o.ref), pcb)
                if h is None:
                    unknown.append(o.ref)
                elif h > fam["tall_mm"]:
                    tall.append((o.ref, h))
            fact.update(tall_nearby=[r for r, _ in tall],
                        height_unknown=sorted(unknown))
            if tall:
                names = ", ".join(f"{r} ({h:.1f} mm)" for r, h in sorted(tall))
                out.append(checklib.violation(
                    "place", "error", f.center_abs(), None, None,
                    [ref, *(r for r, _ in tall)],
                    f"{ref} ({fam['family']}): {names} within "
                    f"{fam['finger_mm']} mm - taller than {fam['tall_mm']} mm "
                    "leaves no room for the plug housing and a finger",
                    SOURCE, kind="mating_zone_blocked", connector=ref,
                    blocked_by=sorted(r for r, _ in tall)))
    return out, facts
