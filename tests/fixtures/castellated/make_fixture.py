"""Rebuild castellated_edge.kicad_pcb (run with KiCad's python, a display set).

A 30 x 20 mm two-layer board with two rows of six castellated pads from
castellated_fp.py: J1 on the top edge, J2 on the bottom edge turned 180 deg.
  python3 tests/fixtures/castellated/make_fixture.py
"""
import subprocess
import tempfile
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GEN = ROOT / ".claude/skills/hwde/scripts/castellated_fp.py"
VENV_PY = ROOT / ".venv/bin/python"   # the generator needs the venv (shapely)
NAME = "Castellated_1x06_P2.54"


def mm(v):
    return pcbnew.FromMM(v)


with tempfile.TemporaryDirectory() as td:
    lib = Path(td) / "Cast.pretty"
    subprocess.run([str(VENV_PY), str(GEN), "--pins", "6", "--pitch", "2.54",
                    "--out", str(lib / f"{NAME}.kicad_mod")], check=True,
                   stdout=subprocess.DEVNULL)
    b = pcbnew.BOARD()
    pts = [(0, 0), (30, 0), (30, 20), (0, 20)]
    for i, a in enumerate(pts):
        c = pts[(i + 1) % 4]
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
        s.SetEnd(pcbnew.VECTOR2I(mm(c[0]), mm(c[1])))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.05))
        b.Add(s)
    for ref, (x, y, rot) in {"J1": (15, 0, 0), "J2": (15, 20, 180)}.items():
        fp = pcbnew.FootprintLoad(str(lib), NAME)
        fp.SetReference(ref)
        b.Add(fp)
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        fp.SetOrientationDegrees(rot)
    b.Save(str(HERE / "castellated_edge.kicad_pcb"))
