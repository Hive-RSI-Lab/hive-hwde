"""Run by KiCad's bundled python (pcbnew): write every footprint embedded in a
board to a .pretty library, unplaced (front side, 0 deg, at the origin), one
per footprint name, and relink the board's footprints to libname:name (the
name KiCad's schematic importers put in Footprint fields) and save it.
argv: board.kicad_pcb out.pretty libname. Prints JSON:
{"written": [...], "variants": {name: [refs that differ from the first]}}.
eda_io import calls it so an imported board's parts have a library to link to.
"""
import json
import os
import sys

import pcbnew

board_path, pretty, libname = sys.argv[1], sys.argv[2], sys.argv[3]
board = pcbnew.LoadBoard(board_path)
os.makedirs(pretty, exist_ok=True)
io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
seen, variants, written = {}, {}, []


def shape(fp):
    """Pads as (number, size, shape, local position): enough to tell two
    footprints with one name but different copper apart."""
    out = []
    for p in fp.Pads():
        pos = p.GetFPRelativePosition()
        out.append((p.GetNumber(), p.GetSize().x, p.GetSize().y, p.GetShape(),
                    round(pos.x / 1000), round(pos.y / 1000)))
    return sorted(out)


for fp in board.GetFootprints():
    name = fp.GetFPID().GetLibItemName().wx_str()
    if not name:
        continue
    c = pcbnew.FOOTPRINT(fp)
    if c.IsFlipped():
        c.Flip(c.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    c.SetOrientationDegrees(0)
    c.SetPosition(pcbnew.VECTOR2I(0, 0))
    sig = shape(c)
    fp.SetFPID(pcbnew.LIB_ID(libname, name))
    if name in seen:
        if seen[name] != sig:
            variants.setdefault(name, []).append(fp.GetReference())
        continue
    seen[name] = sig
    c.SetFPID(pcbnew.LIB_ID(libname, name))
    c.SetReference("REF**")
    io.FootprintSave(pretty, c)
    written.append(name)
board.Save(board_path)
print(json.dumps({"written": sorted(written), "variants": variants}))
