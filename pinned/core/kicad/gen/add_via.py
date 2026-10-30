"""Add through vias of a net: KiCad's python: kicad/gen/add_via.py PCB NET DIA DRILL x,y [...]"""
import sys
import pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
ni = b.FindNet(sys.argv[2])
d, h = pcbnew.FromMM(float(sys.argv[3])), pcbnew.FromMM(float(sys.argv[4]))
for s in sys.argv[5:]:
    x, y = map(float, s.split(","))
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
    v.SetWidth(d)
    v.SetDrill(h)
    v.SetNet(ni)
    b.Add(v)
b.Save(sys.argv[1])
print(sys.argv[2], len(sys.argv) - 5, "vias")
