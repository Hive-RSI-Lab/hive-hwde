"""core: pre-order copper fixes from review 6 (KiCad python). Run once, from boards/core.

R6-m3  LIN_L moves off I2S_DOUT_MAIN: rips its B.Cu run between (145.762, 125.65)
       and (115.062, 147.95) and re-routes it with miniroute's maze. I2S tracks
       are inflated by GAP on their own layer; F.Cu is shielded from B.Cu by In1 GND.
R6-m4  C229 (A L+ filter cap) gets a 0.45/0.2 via into the B.Cu A L+ band, so its
       ripple no longer runs through J20's pin and 13.9 mm of 0.205 mm track.
POFV   setup (filling yes) (capping yes), last, as text.

    MR_NOFILL=1 MR_LAYERS=F.Cu,B.Cu python3.11 kicad/gen/preorder_eco.py
Then refill with kicad-cli pcb drc --refill-zones --save-board.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("MR_LAYERS", "F.Cu,B.Cu")
sys.path.insert(0, str(Path(__file__).parent))
import miniroute as mr  # noqa: E402
import pcbnew  # noqa: E402

MM = 1e6
GAP = float(os.environ.get("LIN_GAP", 1.0))
NET = "/linein/LIN_L"
A, B = (145.762, 125.65), (115.062, 147.95)


class Avoid(mr.Router):
    """I2S tracks are inflated on their own layer only: In1 GND shields F.Cu from B.Cu."""

    def grids(self, net, x0, y0, nx, ny, width):
        blk, vblk = super().grids(net, x0, y0, nx, ny, width)
        for t in self.tracks:
            if not t.GetNetname().startswith("/I2S_") or t.GetClass() == "PCB_VIA":
                continue
            r = GAP + width / 2 + t.GetWidth() / 2 / MM
            bb = t.GetBoundingBox()
            k = self.layers.index(t.GetLayer())
            for j in range(max(0, int((bb.GetTop() / MM - r - y0) / mr.G)), min(ny, int((bb.GetBottom() / MM + r - y0) / mr.G) + 2)):
                for i in range(max(0, int((bb.GetLeft() / MM - r - x0) / mr.G)), min(nx, int((bb.GetRight() / MM + r - x0) / mr.G) + 2)):
                    if t.HitTest(mr.V(x0 + i * mr.G, y0 + j * mr.G), int(r * MM)):
                        blk[k][j * nx + i] = 1
        return blk, vblk


def on_run(t):
    """LIN_L's B.Cu segments between A and B; the rest stays."""
    if t.GetNetname() != NET or t.GetClass() == "PCB_VIA" or not t.IsOnLayer(pcbnew.B_Cu):
        return False
    return all(B[0] - 0.01 <= p.x / MM <= A[0] + 0.01 and p.y / MM <= B[1] + 0.01
               for p in (t.GetStart(), t.GetEnd()))


def c229(b):
    """A via on C229's existing F.Cu track and a B.Cu stub into the A L+ column (x 42.6-44.6)."""
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(mr.V(48.61, 129.95))
    v.SetWidth(int(0.45 * MM))
    v.SetDrill(int(0.2 * MM))
    v.SetNet(b.GetNetsByName()["/A_SPK_L+"])
    b.Add(v)
    keep = [v]
    pts = [(48.61, 129.95), (48.16, 129.5), (44.3, 129.5)]  # on the 45-degree grid; y < 129.7: the column keepout starts at y 130
    for p, q in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(mr.V(*p))
        t.SetEnd(mr.V(*q))
        t.SetWidth(int(0.6 * MM))
        t.SetLayer(pcbnew.B_Cu)
        t.SetNet(b.GetNetsByName()["/A_SPK_L+"])
        b.Add(t)
        keep.append(t)
    return keep


def via_fill():
    """All vias filled and capped (JLC POFV, ORDERING.md): check_wicking reads this."""
    s = Path(mr.PCB).read_text()
    i = s.index("(pcbplotparams")
    head = s[:i]
    if "(capping no)" in head:
        head = head.replace("(capping no)", "(capping yes)").replace("(filling no)", "(filling yes)")
    Path(mr.PCB).write_text(head + s[i:])


def main():
    b = pcbnew.LoadBoard(mr.PCB)
    rip = [t for t in b.GetTracks() if on_run(t)]
    print("ripping", len(rip), "segments", round(sum(t.GetLength() for t in rip) / MM, 1), "mm")
    r = Avoid(b)  # own-net copper is no obstacle, so route first: b.Remove() breaks later Zones()/Pads() calls
    got = r.route(NET, A, B, la=(1,), lb=(1,), margin=8.0, width=0.205)
    print("route", got)
    if not got:
        sys.exit("no route at this gap")
    keep = c229(b)
    for t in rip:
        b.Remove(t)
    b.Save(mr.PCB)
    via_fill()
    print("saved", len(keep))


main()
