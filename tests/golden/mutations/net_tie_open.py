"""Mutant: net-tie-open (blinky2).

ROUTER fault: a 3-pad solder jumper (SolderJumper-3 Bridged12, a net tie:
net_tie_pad_groups "1, 2") straps /BOOT0 to GND through the copper bridge
the footprint draws between pads 1 and 2, and that bridge has been moved to
F.SilkS, as KRT's writer did to every net-less copper graphic (#1008). The
pads are open on the board that gets built and BOOT0 floats, yet KiCad's DRC
passes it: each pad keeps its own net and footprint graphics aren't
connectivity, so the board reads the same with the bridge or without it.
Must be caught by check_net_tie (kind "net_tie_open", ref JP1, on F.Cu).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

U = "4e71e000-0000-4000-8000-0000000000"   # + two digits

JP = ('\t(footprint "Jumper:SolderJumper-3_P1.3mm_Bridged12_Pad1.0x1.5mm"\n'
      '\t\t(layer "F.Cu")\n'
      f'\t\t(uuid "{U}01")\n'
      '\t\t(at 144 134)\n'
      '\t\t(property "Reference" "JP1"\n'
      '\t\t\t(at 0 -1.8 0)\n'
      '\t\t\t(layer "F.SilkS")\n'
      f'\t\t\t(uuid "{U}02")\n'
      '\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n'
      '\t\t)\n'
      '\t\t(property "Value" "BOOT0"\n'
      '\t\t\t(at 0 2 0)\n'
      '\t\t\t(layer "F.Fab")\n'
      f'\t\t\t(uuid "{U}03")\n'
      '\t\t\t(effects (font (size 1 1) (thickness 0.15)))\n'
      '\t\t)\n'
      + mutlib.footprint_fields(f"{U}1") +
      '\t\t(attr exclude_from_pos_files exclude_from_bom allow_soldermask_bridges)\n'
      '\t\t(net_tie_pad_groups "1, 2")\n'
      # the bridge: the library draws it on F.Cu
      '\t\t(fp_poly\n'
      '\t\t\t(pts (xy -0.9 -0.3) (xy -0.4 -0.3) (xy -0.4 0.3) (xy -0.9 0.3))\n'
      '\t\t\t(stroke (width 0) (type solid))\n'
      '\t\t\t(fill yes)\n'
      '\t\t\t(layer "F.SilkS")\n'
      f'\t\t\t(uuid "{U}04")\n'
      '\t\t)\n'
      '\t\t(fp_rect\n'
      '\t\t\t(start -0.8 -0.75)\n'
      '\t\t\t(end 0.8 0.75)\n'
      '\t\t\t(stroke (width 0) (type default))\n'
      '\t\t\t(fill yes)\n'
      '\t\t\t(layer "F.Mask")\n'
      f'\t\t\t(uuid "{U}05")\n'
      '\t\t)\n'
      '\t\t(fp_rect\n'
      '\t\t\t(start -2.3 -1.25)\n'
      '\t\t\t(end 2.3 1.25)\n'
      '\t\t\t(stroke (width 0.05) (type solid))\n'
      '\t\t\t(fill no)\n'
      '\t\t\t(layer "F.CrtYd")\n'
      f'\t\t\t(uuid "{U}06")\n'
      '\t\t)\n'
      + "".join(
          f'\t\t(pad "{n}" smd rect\n'
          f'\t\t\t(at {x} 0)\n'
          '\t\t\t(size 1 1.5)\n'
          '\t\t\t(layers "F.Cu" "F.Mask")\n'
          f'\t\t\t(net "{net}")\n'
          f'\t\t\t(uuid "{U}0{6 + int(n)}")\n'
          '\t\t)\n'
          for n, x, net in (("1", -1.3, "GND"), ("2", 0, "/BOOT0"),
                            ("3", 1.3, "+3V3")))
      + '\t)\n')


def surgery(text):
    text = mutlib.append_items(text, JP, "net-tie jumper, bridge on silk")
    return text, {"ref": "JP1", "pads": ["1", "2"], "layer": "F.Cu"}


if __name__ == "__main__":
    sys.exit(mutlib.run("net-tie-open", "blinky2", surgery))
