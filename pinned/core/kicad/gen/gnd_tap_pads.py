"""core: a short track and a via from each named pad down to the planes
(GND pads whose pour fragment a new copper patch or route cut off, or a pad
fed from an inner plane, e.g. +3V3 on In2).
  KiCad's python: kicad/gen/gnd_tap_pads.py [--net NET] REF.PAD [REF.PAD ...]"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import miniroute as mr  # noqa: E402
import pcbnew  # noqa: E402
b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)
args = sys.argv[1:]
NET = "GND"
if args and args[0] == "--net":
    NET, args = args[1], args[2:]
for spec in args:
    ref, num = spec.split(".")
    p = b.FindFootprintByReference(ref).FindPadByNumber(num)
    c = p.GetPosition()
    n = r.route_to_via(NET, (c.x / mr.MM, c.y / mr.MM), layer=0, margin=float(__import__("os").environ.get("MR_MARGIN", 6)), width=0.2)
    r.refresh()
    print(spec, "->", n)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
