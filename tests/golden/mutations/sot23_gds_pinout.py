"""Mutant: sot23-gds-pinout (usbbuck4).

Erratum class #878 (oasis v0.2: Q1/Q3 drawn with Device:Q_NMOS_GDS on a
SOT-23-3, so the SI2328DS's source lands where its drain goes). A low-side
switch Q9 is added to the schematic with the same symbol and footprint: gate
on MCO, drain on LED_A, source on GND, its symbol pins 1-2-3 G-D-S where the
SOT-23 standard is G-S-D. Must be caught by netlist_audit as sot23_pinout
at Q9.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

X, Y = 330.2, 200.66

# KiCad 9's stock Device:Q_NMOS_GDS, as oasis v0.2 embeds it
LIB_SYMBOL = '\t\t(symbol "Device:Q_NMOS_GDS"\n\t\t\t(pin_names\n\t\t\t\t(offset 0) hide)\n\t\t\t(exclude_from_sim no)\n\t\t\t(in_bom yes)\n\t\t\t(on_board yes)\n\t\t\t(property "Reference" "Q"\n\t\t\t\t(at 5.08 1.27 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(justify left)\n\t\t\t\t)\n\t\t\t)\n\t\t\t(property "Value" "Q_NMOS_GDS"\n\t\t\t\t(at 5.08 -1.27 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(justify left)\n\t\t\t\t)\n\t\t\t)\n\t\t\t(property "Footprint" ""\n\t\t\t\t(at 5.08 2.54 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)\n\t\t\t(property "Datasheet" "~"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)\n\t\t\t(property "Description" "N-MOSFET transistor, gate/drain/source"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)\n\t\t\t(property "ki_keywords" "transistor NMOS N-MOS N-MOSFET"\n\t\t\t\t(at 0 0 0)\n\t\t\t\t(effects\n\t\t\t\t\t(font\n\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(hide yes)\n\t\t\t\t)\n\t\t\t)\n\t\t\t(symbol "Q_NMOS_GDS_0_1"\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 0.254 0) (xy -2.54 0)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 0.254 1.905) (xy 0.254 -1.905)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0.254)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 0.762 -1.27) (xy 0.762 -2.286)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0.254)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 0.762 0.508) (xy 0.762 -0.508)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0.254)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 0.762 2.286) (xy 0.762 1.27)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0.254)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 2.54 2.54) (xy 2.54 1.778)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 2.54 -2.54) (xy 2.54 0) (xy 0.762 0)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 0.762 -1.778) (xy 3.302 -1.778) (xy 3.302 1.778) (xy 0.762 1.778)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 1.016 0) (xy 2.032 0.381) (xy 2.032 -0.381) (xy 1.016 0)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type outline)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 2.794 0.508) (xy 2.921 0.381) (xy 3.683 0.381) (xy 3.81 0.254)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(polyline\n\t\t\t\t\t(pts\n\t\t\t\t\t\t(xy 3.302 0.381) (xy 2.921 -0.254) (xy 3.683 -0.254) (xy 3.302 0.381)\n\t\t\t\t\t)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(circle\n\t\t\t\t\t(center 1.651 0)\n\t\t\t\t\t(radius 2.794)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0.254)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type none)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(circle\n\t\t\t\t\t(center 2.54 -1.778)\n\t\t\t\t\t(radius 0.254)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type outline)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(circle\n\t\t\t\t\t(center 2.54 1.778)\n\t\t\t\t\t(radius 0.254)\n\t\t\t\t\t(stroke\n\t\t\t\t\t\t(width 0)\n\t\t\t\t\t\t(type default)\n\t\t\t\t\t)\n\t\t\t\t\t(fill\n\t\t\t\t\t\t(type outline)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t)\n\t\t\t(symbol "Q_NMOS_GDS_1_1"\n\t\t\t\t(pin input line\n\t\t\t\t\t(at -5.08 0 0)\n\t\t\t\t\t(length 2.54)\n\t\t\t\t\t(name "G"\n\t\t\t\t\t\t(effects\n\t\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t\t)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t\t(number "1"\n\t\t\t\t\t\t(effects\n\t\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t\t)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(pin passive line\n\t\t\t\t\t(at 2.54 5.08 270)\n\t\t\t\t\t(length 2.54)\n\t\t\t\t\t(name "D"\n\t\t\t\t\t\t(effects\n\t\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t\t)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t\t(number "2"\n\t\t\t\t\t\t(effects\n\t\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t\t)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t\t(pin passive line\n\t\t\t\t\t(at 2.54 -5.08 90)\n\t\t\t\t\t(length 2.54)\n\t\t\t\t\t(name "S"\n\t\t\t\t\t\t(effects\n\t\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t\t)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t\t(number "3"\n\t\t\t\t\t\t(effects\n\t\t\t\t\t\t\t(font\n\t\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t\t)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'

INSTANCE = '\t(symbol\n\t\t(lib_id "Device:Q_NMOS_GDS")\n\t\t(at {X} {Y} 0)\n\t\t(unit 1)\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n\t\t(uuid "a8120877-0000-4000-8000-000000000001")\n\t\t(property "Reference" "Q9"\n\t\t\t(at {RX} {Y1} 0)\n\t\t\t(effects (font (size 1.27 1.27)) (justify left))\n\t\t)\n\t\t(property "Value" "SI2328DS"\n\t\t\t(at {RX} {Y2} 0)\n\t\t\t(effects (font (size 1.27 1.27)) (justify left))\n\t\t)\n\t\t(property "Footprint" "Package_TO_SOT_SMD:SOT-23-3"\n\t\t\t(at {X} {Y} 0)\n\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n\t\t)\n\t\t(property "Datasheet" "~"\n\t\t\t(at {X} {Y} 0)\n\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n\t\t)\n\t\t(property "Description" ""\n\t\t\t(at {X} {Y} 0)\n\t\t\t(effects (font (size 1.27 1.27)) (hide yes))\n\t\t)\n\t\t(pin "1"\n\t\t\t(uuid "a8120877-0000-4000-8000-000000000002")\n\t\t)\n\t\t(pin "2"\n\t\t\t(uuid "a8120877-0000-4000-8000-000000000003")\n\t\t)\n\t\t(pin "3"\n\t\t\t(uuid "a8120877-0000-4000-8000-000000000004")\n\t\t)\n\t\t(instances\n\t\t\t(project "usbbuck4"\n\t\t\t\t(path "/d7b09d7b-cdbe-4a27-892b-d932c5bd7013"\n\t\t\t\t\t(reference "Q9")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)\n'


def _label(name, at, uuid):
    return (f'\t(label "{name}"\n\t\t(at {at[0]} {at[1]} 0)\n'
            f'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n'
            f'\t\t\t)\n\t\t\t(justify left bottom)\n\t\t)\n'
            f'\t\t(uuid "{uuid}")\n\t)\n')


def surgery(text):
    sch = mutlib.load_golden_sch("usbbuck4")
    if "Device:Q_NMOS_GDS" in sch:
        raise mutlib.SurgeryError("golden already has a Q_NMOS_GDS")
    sch = mutlib.replace_once(sch, "\t(lib_symbols\n",
                              "\t(lib_symbols\n" + LIB_SYMBOL, "lib symbol")
    # pin ends: G (-5.08, 0), D (2.54, 5.08), S (2.54, -5.08), y up
    pins = (("MCO", (round(X - 5.08, 2), Y)),
            ("LED_A", (round(X + 2.54, 2), round(Y - 5.08, 2))),
            ("GND", (round(X + 2.54, 2), round(Y + 5.08, 2))))
    items = INSTANCE.format(X=X, Y=Y, RX=round(X + 6.35, 2),
                            Y1=round(Y - 1.27, 2), Y2=round(Y + 1.27, 2))
    for i, (net, at) in enumerate(pins):
        items += _label(net, at, f"a8120877-0000-4000-8000-00000000001{i}")
    sch = mutlib.replace_once(sch, "\n\t(sheet_instances",
                              "\n" + items.rstrip("\n") + "\n\t(sheet_instances",
                              "Q9 placement")
    return text, {"ref": "Q9", "sidecars": {"usbbuck4.kicad_sch": sch}}


if __name__ == "__main__":
    sys.exit(mutlib.run("sot23-gds-pinout", "usbbuck4", surgery))
