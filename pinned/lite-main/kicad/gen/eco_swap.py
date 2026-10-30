"""Swap a placed, routed footprint for a pad-compatible one, keeping its copper.

hwde's board_update composes a footprint change as delete + add, which rips
the old part's tracks even when the new pads land on them. Here the new
footprint goes in at the old position and rotation, each pad takes the net of
the old pad it replaces (by the pad map below), and every track and via stays.
drc_routed then judges the result.

Used for review-2 (2026-10-29): J1 USB4105 -> HRO TYPE-C-31-M-12 (pads within
0.09 mm). U6 CH340C -> CH340B keeps its SOP-16 land (same MS-012 package): a
footprint that's already in place is left alone.

The HRO footprint's four VBUS/GND pads come from EasyEDA as 0.6 mm polygons
drawn with a 0.1 mm stroke, 0.7 mm wide at 0.8 mm pitch: a 0.1 mm gap between
VBUS and GND, under the 0.15 mm power clearance. They become plain 0.6 x 1.3 mm
rectangles (HRO's land is 0.6 mm wide), a 0.2 mm gap, in the library file and
on the board.

Its eight signal pads are 1.30 mm long, 0.16 mm longer at the inner end than
the USB4105's 1.15 mm, at the end away from the board edge; the routed DM
link that passes beyond A6 then sits on it. They are trimmed at that end to
1.13 mm (the end under the receptacle body unchanged), which puts it where the
USB4105's pads ended (footprint y -2.955).

    KiCad's python: kicad/gen/eco_swap.py kicad/lite-main.kicad_pcb
"""
import re
import sys
from pathlib import Path

import pcbnew

pcb = sys.argv[1]
lib = str(Path(pcb).resolve().parents[1] / "lib" / "aiee.pretty")

# ref -> (new footprint, value, LCSC, {old pad: new pad} for renamed pads)
SWAPS = {
    "J1": ("USB-C_SMD-TYPE-C-31-M-12_1", "TYPE-C-31-M-12", "C165948",
           {"A4-B9": "A4B9", "B4-A9": "B4A9", "A1-B12": "A1B12", "B1-A12": "B1A12"}),
    "U6": ("SOP-16_L10.0-W3.9-P1.27-LS6.0-BL", "CH340B", "C81010", {}),
}

POWER_PADS = ("A1B12", "A4B9", "B4A9", "B1A12")
modf = Path(lib) / "USB-C_SMD-TYPE-C-31-M-12_1.kicad_mod"
mt = modf.read_text()
for name in POWER_PADS:
    mt = re.sub(r'\(pad %s smd custom (\(at [^)]*\)) \(size 0\.005 0\.005\) (\(layers [^)]*\))\n\t\t\(primitives.*?\n\t\t\)\n\t\)' % name,
                r'(pad %s smd rect \1 (size 0.600 1.300) \2)' % name, mt, flags=re.S)
SIG_PADS = ("A5", "A6", "A7", "A8", "B5", "B6", "B7", "B8")
SIG_LEN, SIG_TRIM = 1.13, 1.30 - 1.13
for name in SIG_PADS:
    mt = re.sub(r'\(pad %s smd rect \(at ([-\d.]+) -2\.47 0\.00\) \(size 0\.300 1\.300\)' % name,
                lambda m: '(pad %s smd rect (at %s %.3f 0.00) (size 0.300 %.3f)' % (name, m.group(1), -2.47 + SIG_TRIM / 2, SIG_LEN), mt)
modf.write_text(mt)

b = pcbnew.LoadBoard(pcb)
for ref, (fpname, value, lcsc, padmap) in SWAPS.items():
    old = b.FindFootprintByReference(ref)
    if old.GetFPID().GetLibItemName() == fpname:
        continue
    new = pcbnew.FootprintLoad(lib, fpname)
    new.SetFPID(pcbnew.LIB_ID("aiee", fpname))
    new.SetReference(ref)
    new.SetValue(value)
    for f in old.GetFields():
        name = f.GetName()
        if name in ("Reference", "Value", "Footprint"):
            continue
        new.SetField(name, lcsc if name.startswith("LCSC") else f.GetText())
    new.SetPath(old.GetPath())
    new.SetLayer(old.GetLayer())
    new.SetOrientation(old.GetOrientation())
    new.SetPosition(old.GetPosition())
    nets = {}
    for p in old.Pads():
        nets.setdefault(padmap.get(p.GetNumber(), p.GetNumber()), p.GetNet())
    for p in new.Pads():
        if p.GetNumber() in nets:
            p.SetNet(nets[p.GetNumber()])
    # keep the reference text where the silk pass put it
    new.Reference().SetPosition(old.Reference().GetPosition())
    new.Reference().SetVisible(old.Reference().IsVisible())
    new.Value().SetVisible(False)
    b.Remove(old)
    b.Add(new)
    print(ref, "->", fpname)
j1 = b.FindFootprintByReference("J1")
for p in j1.Pads():
    if p.GetNumber() in POWER_PADS and p.GetShape() == pcbnew.PAD_SHAPE_CUSTOM:
        p.DeletePrimitivesList()
        p.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.6), pcbnew.FromMM(1.3)))
        print("J1", p.GetNumber(), "-> rect 0.6 x 1.3")
    elif p.GetNumber() in SIG_PADS and abs(pcbnew.ToMM(p.GetSizeY()) - SIG_LEN) > 0.005 or \
            p.GetNumber() in SIG_PADS and abs(pcbnew.ToMM(p.GetFPRelativePosition().y) + 2.47 - SIG_TRIM / 2) > 0.005:
        off = p.GetFPRelativePosition()
        p.SetFPRelativePosition(pcbnew.VECTOR2I(off.x, pcbnew.FromMM(-2.47 + SIG_TRIM / 2)))
        p.SetSize(pcbnew.VECTOR2I(p.GetSizeX(), pcbnew.FromMM(SIG_LEN)))
        print("J1", p.GetNumber(), "-> 0.30 x", SIG_LEN)
b.Save(pcb)
