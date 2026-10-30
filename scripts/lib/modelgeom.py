"""modelgeom - a footprint's 3D model as geometry, read without a CAD kernel.

KiCad places a footprint's model (VRML .wrl or STEP .step/.stp) by the
footprint's own (model ... (offset) (scale) (rotate)) in the footprint's
frame. Checking that the model sits on its pads needs only where its leads,
pins and body are, which both formats give from plain text:

  VRML  every IndexedFaceSet's `point [...]` list, indexed into faces by the
        `coordIndex [...]` after it, in KiCad's VRML unit (0.1 inch =
        2.54 mm). easyeda2kicad and KiCad's own .wrl files are flat Shapes;
        a Transform's translation/scale before a point list is applied to it
        (a nested Transform's rotation is not).
  STEP  every ADVANCED_FACE as the vertices of its edge loops, a full
        circle's edge adding its extremes in its plane, in the file's
        length unit (an arc's extremes widen the extent but join no face).
        Sub-assembly placements are taken as identity, as in KiCad's stock
        models.

load() caches by (path, mtime, size): a board reuses one 0402 model a
hundred times. place() applies KiCad's model transform as its 3D viewer
does: scale, then rotate about X, Y, Z by MINUS the file's angles, then the
offset (mm), all in the footprint's Y-up frame; it returns the faces in the
footprint's file frame (Y down, as pads are stored), mirrored for a part on
the back, whose stored pad positions are the library's with y negated.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

VRML_MM = 2.54
Box = tuple[float, float, float, float, float, float]  # x0 y0 z0 x1 y1 z1
Pt = tuple[float, float, float]

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_NUM_RE = re.compile(_NUM)
_VRML_RE = re.compile(
    rf"\b(?:(translation)\s+({_NUM})\s+({_NUM})\s+({_NUM})"
    rf"|(scale)\s+({_NUM})\s+({_NUM})\s+({_NUM})"
    r"|(point)\s*\[([^\]]*)\](?:\s*\}\s*)?(?:[^\[\]]*?\bcoordIndex\s*\[([^\]]*)\])?)",
    re.S)
_STEP_ENT_RE = re.compile(r"#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*?)\)\s*;", re.S)
_STEP_CP_RE = re.compile(rf"\(\s*({_NUM})\s*,\s*({_NUM})\s*(?:,\s*({_NUM})\s*)?\)")
_REF_RE = re.compile(r"#(\d+)")


class ModelError(ValueError):
    pass


@dataclass(frozen=True)
class Mesh:
    kind: str                  # "vrml" | "step"
    faces: tuple               # tuple of vertex tuples, model mm, Z up
    box: Box                   # the extent, circle bulges included


_cache: dict[tuple[str, int, int], Mesh | None] = {}


def _box(pts) -> Box | None:
    it = iter(pts)
    first = next(it, None)
    if first is None:
        return None
    x0 = x1 = first[0]
    y0 = y1 = first[1]
    z0 = z1 = first[2]
    for x, y, z in it:
        x0, x1 = min(x0, x), max(x1, x)
        y0, y1 = min(y0, y), max(y1, y)
        z0, z1 = min(z0, z), max(z1, z)
    return (x0, y0, z0, x1, y1, z1)


def _vrml_faces(text: str) -> list[tuple]:
    scale = (1.0, 1.0, 1.0)
    trans = (0.0, 0.0, 0.0)
    faces: list[tuple] = []
    for m in _VRML_RE.finditer(text):
        if m.group(1):
            trans = tuple(float(v) for v in m.group(2, 3, 4))
            continue
        if m.group(5):
            scale = tuple(float(v) for v in m.group(6, 7, 8))
            continue
        nums = [float(v) for v in _NUM_RE.findall(m.group(10))]
        pts = [tuple((nums[i + k] * scale[k] + trans[k]) * VRML_MM
                     for k in range(3))
               for i in range(0, len(nums) - 2, 3)]
        idx = m.group(11)
        if not idx:
            faces += [(p,) for p in pts]
            continue
        face: list = []
        for t in idx.replace(",", " ").split():
            k = int(t)
            if k < 0:
                if face:
                    faces.append(tuple(face))
                face = []
            elif k < len(pts):
                face.append(pts[k])
        if face:
            faces.append(tuple(face))
    return faces


def _step_unit(text: str) -> float:
    if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", text, re.I):
        return 25.4
    m = re.search(r"LENGTH_UNIT\s*\(\s*\)[^;]*?SI_UNIT\s*\(\s*([.$A-Z]*)\s*,"
                  r"\s*\.METRE\.\s*\)", text)
    if m is None:
        m = re.search(r"SI_UNIT\s*\(\s*([.$A-Z]*)\s*,\s*\.METRE\.\s*\)", text)
    prefix = (m.group(1) if m else ".MILLI.").strip(".")
    return {"MILLI": 1.0, "CENTI": 10.0, "DECI": 100.0, "$": 1000.0,
            "": 1000.0, "MICRO": 1e-3}.get(prefix, 1.0)


def _step_geometry(text: str) -> tuple[list[tuple], list[Pt]]:
    """(faces, circle bulge points) of a STEP B-rep. A face is the vertices
    of its loops (ADVANCED_FACE > FACE_BOUND > EDGE_LOOP > ORIENTED_EDGE >
    EDGE_CURVE > VERTEX_POINT), and each circular edge adds the circle's
    extremes in its own plane, so a pin's round end cap is a disc, not one
    point. A file with no ADVANCED_FACE gives its vertices as one-point
    faces. Placement and circle centres are no surface of the part (a
    fillet's axis can sit inside the board), so they're never points."""
    unit = _step_unit(text)
    data = text.split("DATA;", 1)[-1]
    ents: dict[str, tuple[str, str]] = {}
    for m in _STEP_ENT_RE.finditer(data):
        ents[m.group(1)] = (m.group(2), m.group(3))

    def refs(eid: str) -> list[str]:
        e = ents.get(eid)
        return _REF_RE.findall(e[1]) if e else []

    def nums(eid: str) -> list[float]:
        e = ents.get(eid)
        if not e:
            return []
        c = _STEP_CP_RE.search(e[1])
        return [float(v) if v else 0.0 for v in c.groups()] if c else []

    def point(eid: str) -> Pt | None:
        v = nums(eid)
        return tuple(x * unit for x in v) if len(v) == 3 else None

    circle_cache: dict[str, list[Pt]] = {}

    def circle(eid: str) -> list[Pt]:
        """The circle's extreme points along each axis, in its plane."""
        if eid in circle_cache:
            return circle_cache[eid]
        out: list[Pt] = []
        r_nums = _NUM_RE.findall(ents[eid][1].rsplit(",", 1)[-1])
        ax = refs(eid)
        if ax and r_nums:
            r = float(r_nums[-1]) * unit
            loc = refs(ax[0])
            c = point(loc[0]) if loc else None
            n = nums(loc[1]) if len(loc) > 1 else [0.0, 0.0, 1.0]
            ln = math.sqrt(sum(v * v for v in n)) or 1.0
            n = [v / ln for v in n] if len(n) == 3 else [0.0, 0.0, 1.0]
            if c is not None:
                for k in range(3):
                    h = r * math.sqrt(max(0.0, 1.0 - n[k] * n[k]))
                    for sgn in (-1, 1):
                        q = list(c)
                        q[k] += sgn * h
                        out.append(tuple(q))
        circle_cache[eid] = out
        return out

    faces: list[tuple] = []
    bulge: list[Pt] = []
    for eid, (kind, _body) in ents.items():
        if kind != "ADVANCED_FACE":
            continue
        pts: list[Pt] = []
        for bound in refs(eid)[:-1]:
            if ents.get(bound, ("",))[0] not in ("FACE_BOUND",
                                                 "FACE_OUTER_BOUND"):
                continue
            for loop in refs(bound)[:1]:
                for oe in refs(loop):
                    for ec in refs(oe)[:1]:
                        r = refs(ec)
                        for v in r[:2]:
                            cp = refs(v)
                            p = point(cp[0]) if cp else None
                            if p is not None:
                                pts.append(p)
                        if len(r) > 2 and ents.get(r[2], ("",))[0] == "CIRCLE":
                            ext = circle(r[2])
                            bulge += ext
                            # a full circle's extremes are on the edge; an
                            # arc's may not be, so they widen the extent only
                            if len(r) >= 2 and r[0] == r[1]:
                                pts += ext
        if pts:
            faces.append(tuple(dict.fromkeys(pts)))
    if not faces:
        faces = [(p,) for p in (point(refs(e)[0]) if refs(e) else None
                                for e, (k, _) in ents.items()
                                if k == "VERTEX_POINT") if p is not None]
    return faces, bulge


def load(path: Path) -> Mesh | None:
    """The model's geometry in its own frame (mm, Z up), or None when the
    file holds none. Raises ModelError on a format it can't read."""
    path = Path(path)
    st = path.stat()
    key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    if key in _cache:
        return _cache[key]
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        import gzip
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    head = text.lstrip()[:16]
    suffix = path.suffix.lower()
    if head.startswith("#VRML") or (suffix in (".wrl", ".vrml")
                                    and not head.startswith("ISO-10303")):
        kind, faces, extra = "vrml", _vrml_faces(text), []
    elif head.startswith("ISO-10303") or suffix in (".step", ".stp"):
        kind = "step"
        faces, extra = _step_geometry(text)
    else:
        raise ModelError(f"not a VRML or STEP model: {path.name}")
    box = _box([p for f in faces for p in f] + extra)
    mesh = None if box is None else Mesh(kind, tuple(faces), box)
    _cache[key] = mesh
    return mesh


def _rot(axis: int, deg: float, p: Pt) -> Pt:
    if not deg % 360:
        return p
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    x, y, z = p
    if axis == 0:
        return (x, y * c - z * s, y * s + z * c)
    if axis == 1:
        return (x * c + z * s, y, -x * s + z * c)
    return (x * c - y * s, x * s + y * c, z)


def transform(offset=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0),
              rotate=(0.0, 0.0, 0.0), back: bool = False):
    """p (model frame) -> the footprint's file frame (mm, Y down, Z up from
    the part's own side of the board)."""
    ysign = 1.0 if back else -1.0

    def f(p: Pt) -> Pt:
        q = (p[0] * scale[0], p[1] * scale[1], p[2] * scale[2])
        for axis in (0, 1, 2):
            q = _rot(axis, -rotate[axis], q)
        return (q[0] + offset[0], ysign * (q[1] + offset[1]), q[2] + offset[2])
    return f


def place(mesh: Mesh, offset=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0),
          rotate=(0.0, 0.0, 0.0), back: bool = False) -> list[tuple]:
    """The mesh's faces in the footprint's file frame (see transform)."""
    f = transform(offset, scale, rotate, back)
    return [tuple(f(p) for p in face) for face in mesh.faces]


def placed_box(mesh: Mesh, offset=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0),
               rotate=(0.0, 0.0, 0.0), back: bool = False) -> Box:
    """The extent in the footprint's file frame: the eight corners of the
    model's own extent, transformed (exact for right-angle rotations, a
    superset for others)."""
    f = transform(offset, scale, rotate, back)
    b = mesh.box
    return _box([f((x, y, z)) for x in (b[0], b[3]) for y in (b[1], b[4])
                 for z in (b[2], b[5])])
