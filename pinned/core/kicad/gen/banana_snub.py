"""core: hook zone A R- and zone B L+'s snubbers back onto their B.Cu pours, stage 3 of the banana ECO (KiCad python).

The 1 uF filter cap and 4.7R Zobel of every speaker leg sit on F.Cu in the
row at y 131-134. They used to reach their net through the old screw
terminal's pin; the F.Cu nets (L-, R+) now reach their pours through the
inductor pads and the staircases, but A_SPK_R- and B_SPK_L+ live on B.Cu
under keepouts (power_zones.py: no tracks or vias) right below the caps.
Each gets a 0.45/0.2 mm via between its cap and resistor (0.22 mm clear of
both pads) and a B.Cu stub into its pour; the pour's keepout now starts at
y 134, below the stub.

    python3.11 kicad/gen/banana_snub.py kicad/core.kicad_pcb
"""
import sys

import pcbnew

MM = 1e6
# net, via (x, y), F.Cu stub to the resistor's pin 1, B.Cu stub end in the pour, keepout column x0
HOOKS = [("/amp_a/A_SPK_R-", (57.135, 132.4), (57.7, 132.95), (55.4, 132.4), 54.5),
         ("/amp_b/B_SPK_L+", (83.235, 132.4), (83.8, 132.95), (76.3, 132.4), 75.5)]
KO_TOP = 134.0


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def main(path):
    b = pcbnew.LoadBoard(path)
    nets = b.GetNetsByName()
    keep = []
    for z in b.Zones():
        if not z.GetIsRuleArea() or not z.IsOnLayer(pcbnew.B_Cu):
            continue
        bb = z.GetBoundingBox()
        x0, y0 = bb.GetX() / MM, bb.GetY() / MM
        if any(abs(x0 - k) < 0.05 for *_, k in HOOKS) and y0 < KO_TOP:
            o = z.Outline()
            for i in range(o.TotalVertices()):
                v = o.CVertex(i)
                if v.y / MM < KO_TOP:
                    o.SetVertex(i, V(v.x / MM, KO_TOP))
    for net, via, f_end, b_end, _ in HOOKS:
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(V(*via))
        v.SetWidth(int(0.45 * MM))
        v.SetDrill(int(0.2 * MM))
        v.SetNet(nets[net])
        b.Add(v)
        keep.append(v)
        for layer, end in ((pcbnew.F_Cu, f_end), (pcbnew.B_Cu, b_end)):
            t = pcbnew.PCB_TRACK(b)
            t.SetStart(V(*via))
            t.SetEnd(V(*end))
            t.SetWidth(int(0.3 * MM))
            t.SetLayer(layer)
            t.SetNet(nets[net])
            b.Add(t)
            keep.append(t)
    b.Save(path)
    print(len(keep), "items")


if __name__ == "__main__":
    main(sys.argv[1])
