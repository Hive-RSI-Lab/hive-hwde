"""empalace_mesh.py - the gmsh half of lib/empalace.py (#616 cross-check).

Run by the IIC tree's python (solvers.run("gmsh-python", ...)), not by hwde's
venv, which has no gmsh: `empalace_mesh.py SPEC.json OUT.msh ATTRS.json`. It
imports nothing of hwde's. SPEC is empalace.mesh_spec's plain data; ATTRS says
which mesh attribute is which and how large the mesh came out.

Every copper layer is a zero-thickness surface inside the volume, fragmented
into the dielectric so the mesh conforms to it. A barrel is a hole cut through
the volume, its walls are PEC. Attributes come from fragment()'s own map, never
from a bounding box: a box test also catches internal interfaces (a 50 ohm line
read 10 ohm that way, kb/em-solvers.md).
"""
import ctypes.util
import glob
import json
import os
import sys

# gmsh.py looks in its own directory and asks find_library, which runs ldconfig
# and gcc: neither is any use through the image's loader.
_real_find = ctypes.util.find_library


def _find(name):
    if name.startswith("gmsh") or name.startswith("libgmsh"):
        for d in os.environ.get("HWDE_GMSH_LIBDIR", "").split(":"):
            hit = sorted(glob.glob(os.path.join(d, "libgmsh.so*")))
            if hit:
                return hit[0]
    return _real_find(name)


ctypes.util.find_library = _find
import gmsh  # noqa: E402

TOL = 1e-4      # gmsh's bounding boxes carry about 1e-7 of slack


def surface(occ, outer, holes=()):
    def loop(pts):
        ps = [occ.addPoint(x, y, z) for x, y, z in pts]
        ls = [occ.addLine(ps[i], ps[(i + 1) % len(ps)]) for i in range(len(ps))]
        return occ.addCurveLoop(ls)
    return occ.addPlaneSurface([loop(outer)] + [loop(h) for h in holes])


def build(spec):
    occ = gmsh.model.occ
    x0, y0, x1, y1 = spec["dom"]
    vols = []
    for k, (za, zb) in enumerate(spec["layers"]):
        vols.append((3, occ.addBox(x0, y0, za, x1 - x0, y1 - y0, zb - za)))
    if spec["posts"]:
        cut = [(3, occ.addBox(a, b, c, d - a, e - b, f - c)) for a, b, c, d, e, f in spec["posts"]]
        vols, _ = occ.cut(vols, cut, removeObject=True, removeTool=True)
    tags = {}       # input surface -> what it stands for
    tools = []
    for s in spec["sheets"]:
        t = surface(occ, [(x, y, s["z"]) for x, y in s["outer"]],
                    [[(x, y, s["z"]) for x, y in h] for h in s.get("holes", [])])
        tools.append((2, t))
        tags[t] = ("sheet", s)
    for lp in spec["ports"]:
        t = surface(occ, lp["pts"])
        tools.append((2, t))
        tags[t] = ("port", lp)
    occ.synchronize()
    out, omap = occ.fragment(vols, tools)
    occ.synchronize()
    inputs = vols + tools
    sheet_parts, sig_parts, port_parts = [], [], {}
    for src, kids in zip(inputs, omap):
        if src[0] != 2:
            continue
        kind, what = tags[src[1]]
        kt = [t for d, t in kids if d == 2]
        if kind == "sheet":
            sheet_parts += kt
            if what.get("signal"):
                sig_parts += kt
        else:
            port_parts.setdefault(what["index"], []).extend(kt)
    return sheet_parts, sig_parts, port_parts


def physical(spec, sheet_parts, port_parts):
    occ = gmsh.model.occ
    x0, y0, x1, y1 = spec["dom"]
    ztop, zbot = spec["layers"][-1][1], spec["layers"][0][0]
    vols = [t for _, t in gmsh.model.getEntities(3)]
    attrs = {"materials": [], "pec": [], "absorbing": [], "ports": {}}
    attr = 1
    by_eps = {}
    for v in vols:
        z = occ.getCenterOfMass(3, v)[2]
        for (za, zb), er in zip(spec["layers"], spec["er"]):
            if za - TOL <= z <= zb + TOL:
                by_eps.setdefault(er, []).append(v)
                break
        else:
            raise SystemExit(f"volume {v} at z={z} is in no layer")
    for er, vs in sorted(by_eps.items()):
        gmsh.model.addPhysicalGroup(3, vs, attr)
        attrs["materials"].append({"attr": attr, "er": er})
        attr += 1
    outer = [t for _, t in gmsh.model.getBoundary([(3, v) for v in vols], combined=True, oriented=False)]
    pec, absorbing = set(sheet_parts), set()
    in_port = {t for ts in port_parts.values() for t in ts}
    for t in outer:
        if t in in_port or t in pec:
            continue
        b = gmsh.model.getBoundingBox(2, t)
        flat = lambda i, v: abs(b[i] - v) < TOL and abs(b[i + 3] - v) < TOL
        if flat(2, zbot):
            (pec if spec["floor"] == "pec" else absorbing).add(t)
        elif flat(2, ztop) or flat(0, x0) or flat(0, x1) or flat(1, y0) or flat(1, y1):
            absorbing.add(t)
        else:
            pec.add(t)      # a barrel's wall: a hole's sides reach no outer face
    gmsh.model.addPhysicalGroup(2, sorted(pec), attr)
    attrs["pec"] = [attr]
    attr += 1
    gmsh.model.addPhysicalGroup(2, sorted(absorbing), attr)
    attrs["absorbing"] = [attr]
    attr += 1
    for idx, ts in sorted(port_parts.items()):
        gmsh.model.addPhysicalGroup(2, sorted(ts), attr)
        attrs["ports"][str(idx)] = attr
        attr += 1
    return attrs


def size_field(spec, sheet_parts, sig_parts):
    """Fine at the signal copper's edges (where the charge crowds), medium at
    other copper's, growing out to the far size."""
    f = gmsh.model.mesh.field
    sz = spec["size"]
    ids = []

    def ramp(fid, surfaces, near, dist):
        curves = sorted({c for s in surfaces
                         for _, c in gmsh.model.getBoundary([(2, s)], combined=False, oriented=False)})
        if not curves:
            return fid
        f.add("Distance", fid)
        f.setNumbers(fid, "CurvesList", curves)
        f.setNumber(fid, "Sampling", 200)
        f.add("Threshold", fid + 1)
        f.setNumber(fid + 1, "InField", fid)
        f.setNumber(fid + 1, "SizeMin", near)
        f.setNumber(fid + 1, "SizeMax", sz["far"])
        f.setNumber(fid + 1, "DistMin", near)
        f.setNumber(fid + 1, "DistMax", dist)
        ids.append(fid + 1)
        return fid + 2
    n = ramp(1, sig_parts, sz["near"], sz["reach"])
    others = [s for s in sheet_parts if s not in sig_parts]
    n = ramp(n, others, sz["medium"], sz["reach"])
    # the signal's dielectric: the wave's own wavelength resolved there
    f.add("Box", n)
    for k, v in sz["box"].items():
        f.setNumber(n, k, v)
    ids.append(n)
    f.add("Min", n + 1)
    f.setNumbers(n + 1, "FieldsList", ids)
    f.setAsBackgroundMesh(n + 1)
    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)


def main(spec_path, msh, attrs_path):
    spec = json.load(open(spec_path))
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.option.setNumber("General.NumThreads", 1)      # same mesh on any machine
    gmsh.option.setNumber("Mesh.RandomSeed", 1)
    gmsh.model.add("board")
    sheets, sigs, ports = build(spec)
    attrs = physical(spec, sheets, ports)
    size_field(spec, sheets, sigs)
    gmsh.option.setNumber("Mesh.Algorithm", 6)
    gmsh.option.setNumber("Mesh.Algorithm3D", 1)
    gmsh.model.mesh.generate(3)
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
    gmsh.option.setNumber("Mesh.Binary", 1)
    gmsh.write(msh)
    types, tags, _ = gmsh.model.mesh.getElements(3)
    attrs["tets"] = int(sum(len(t) for t in tags))
    gmsh.finalize()
    json.dump(attrs, open(attrs_path, "w"), indent=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
