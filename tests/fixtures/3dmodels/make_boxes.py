"""Regenerate the box models in this directory from a real KiCad 3dmodels tree.

    python3 -I tests/fixtures/3dmodels/make_boxes.py /path/to/share/kicad/3dmodels

For every `${KICAD<n>_3DMODEL_DIR}/<lib>.3dshapes/<name>.step` that a board or
footprint under tests/ names, whose file is in the real tree, this writes
`<lib>.3dshapes/<name>.wrl`: one VRML box with the bounding box of the model's faces (KiCad's
0.1 inch unit). check_body_clash prefers a .wrl twin to the .step, so the tests
resolve against these on any host (conftest.py, `hermetic_3d`).

A model the real tree doesn't have gets no box, on purpose: the golden boards
that name one (usbbuck4 J1, rf4 U1) are judged without it, as on a host with the
stock library.
"""
import gzip
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import modelgeom  # noqa: E402

REF = re.compile(r'\$\{KICAD\d*_3DMODEL_DIR\}/([^"\s]+?)\.(?:step|stp|wrl)\b')


def wanted() -> set[str]:
    out = set()
    for p in (ROOT / "tests").rglob("*"):
        if p.is_file() and p.suffix in (".kicad_pcb", ".kicad_mod", ".py", ".json"):
            out.update(REF.findall(p.read_text(encoding="utf-8", errors="replace")))
    return out


GZ = re.compile(r"PinHeader_")  # check_model_fit judges the pins and opens the .step itself,
#                                  so these are the real STEP files, gzipped (modelgeom reads gzip)


def box_wrl(b) -> str:
    u = modelgeom.VRML_MM
    x0, y0, z0, x1, y1, z1 = (v / u for v in b)
    p = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    pts = ", ".join(f"{x:.5f} {y:.5f} {z:.5f}" for x, y, z in p)
    return ("#VRML V2.0 utf8\n# bounding box of the stock model (make_boxes.py)\n"
            "Shape { geometry IndexedFaceSet { coord Coordinate { point [ "
            f"{pts} ] }}\n coordIndex [ 0,1,3,2,-1, 4,6,7,5,-1, 0,4,5,1,-1,"
            " 2,3,7,6,-1, 0,2,6,4,-1, 1,5,7,3,-1 ] } }\n")


SEAT_MM = 0.3  # check_body_clash ignores z below this (pads, leads)


def body_box(mesh):
    """xy from faces standing above SEAT_MM (the body, not the leads), z from all."""
    allv = [q for f in mesh.faces for q in f]
    hi = [q for f in mesh.faces if min(v[2] for v in f) >= SEAT_MM for q in f] or allv
    a, h = modelgeom._box(allv), modelgeom._box(hi)
    return (h[0], h[1], a[2], h[3], h[4], a[5])


def main(tree: str) -> None:
    tree = Path(tree)
    for name in sorted(wanted()):
        src = next((tree / f"{name}{s}" for s in (".step", ".stp", ".wrl")
                    if (tree / f"{name}{s}").is_file()), None)
        if src is None:
            print("no real model, no box:", name)
            continue
        mesh = modelgeom.load(src)
        if mesh is None:
            print("empty:", name)
            continue
        out = HERE / f"{name}.wrl"
        out.parent.mkdir(parents=True, exist_ok=True)
        if GZ.search(name):
            out.unlink(missing_ok=True)
            out = HERE / f"{name}{src.suffix}"
            out.write_bytes(gzip.compress(src.read_bytes(), 9, mtime=0))
            print("wrote", out.relative_to(ROOT))
            continue
        out.write_text(box_wrl(body_box(mesh)), encoding="utf-8")
        print("wrote", out.relative_to(ROOT))


if __name__ == "__main__":
    main(sys.argv[1])
