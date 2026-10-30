"""Part-specific repairs to EasyEDA footprints pulled by lib_pull (idempotent).

Each one is a snag logged in the board README for hive-hwde:
- USB4105 (USB-C-SMD_MC-311D): the two locating pegs came as PLATED holes
  with no copper ring (annular 0); they are NPTH in GCT's drawing and in
  KiCad's stock USB4105 footprint. The 0.6 mm power/ground pads end 0.18 mm
  from those holes; trim 0.07 mm off their inner end so hole clearance is
  0.25 mm without a waiver.
- TPA3118D2 HTSSOP-32: the nine thermal vias in the exposed pad came with no
  pad number (so no net) and with paste on them. Replace them with a 4 x 4
  grid numbered 33 (GND), without paste, connected solid to the pours
  (thermal spokes starved them and check_thermal wanted more vias).
- CH224Q DFN-10 0.4 mm pitch: pads are 0.176 mm apart, legal at JLC
  (0.127 mm) but under the board's 0.2 mm default clearance; give the
  footprint its own 0.13 mm clearance (JLC: 0.127).

    python3 lib/fix_footprints.py lib/aiee.pretty
"""
import re
import sys
from pathlib import Path

lib = Path(sys.argv[1])
done = []

f = lib / "USB-C-SMD_MC-311D.kicad_mod"
s = f.read_text()
s2 = re.sub(r'\(pad "" thru_hole circle \(at ([-\d.]+) ([-\d.]+)\) \(size 0\.65 0\.65\) \(drill 0\.65\) \(layers \*\.Cu \*\.Mask\)\)',
            r'(pad "" np_thru_hole circle (at \1 \2) (size 0.65 0.65) (drill 0.65) (layers *.Cu *.Mask))', s)
s2 = re.sub(r'\(pad (A1-B12|B1-A12|A4-B9|B4-A9) smd rect \(at ([-\d.]+) -2\.38 0\.00\) \(size 0\.600 1\.150\)',
            r'(pad \1 smd rect (at \2 -2.415 0.00) (size 0.600 1.080)', s2)
if s2 != s:
    f.write_text(s2)
    done.append(f.stem)

f = lib / "HTSSOP-32_L11.0-W6.1-P0.65-LS8.1-BL-EP.kicad_mod"
s = f.read_text()
# the thermal path is the TPA3118's only heat sink on 2 layers: replace the
# 3 x 3 vias at 1 mm with 4 x 4 at 0.85 mm (0.55 mm hole to hole, all inside
# the 3.81 x 3.71 mm pad)
s2 = re.sub(r'\t\(pad (?:""|33) thru_hole circle \(at [-\d.]+ [-\d.]+\) \(size 0\.61 0\.61\) \(drill 0\.30\) \(layers [^)]*\)(?: \(zone_connect 2\))?\)\n', '', s)
grid = "".join(f"\t(pad 33 thru_hole circle (at {x:.3f} {y:.3f}) (size 0.61 0.61) (drill 0.30) (layers *.Cu) (zone_connect 2))\n"
               for x in (-1.275, -0.425, 0.425, 1.275) for y in (-1.275, -0.425, 0.425, 1.275))
# EP copper + mask without paste; 4 paste windows (~55 %) so solder doesn't
# all wick into the (tented) vias
s2 = s2.replace("(pad 33 smd rect (at 0.00 0.00 0.00) (size 3.810 3.710) (layers F.Cu F.Paste F.Mask))",
                "(pad 33 smd rect (at 0.00 0.00 0.00) (size 3.810 3.710) (layers F.Cu F.Mask))")
s2 = re.sub(r'\t\(pad "" smd rect \(at [-\d.]+ [-\d.]+\) \(size 1\.400 1\.400\) \(layers F\.Paste\)\)\n', '', s2)
grid += "".join(f'\t(pad "" smd rect (at {x:.2f} {y:.2f}) (size 1.400 1.400) (layers F.Paste))\n'
                for x in (-0.95, 0.95) for y in (-0.95, 0.95))
i = s2.index("\t(pad 33 smd rect")
s2 = s2[:s2.index("\n", i) + 1] + grid + s2[s2.index("\n", i) + 1:]
if s2 != s:
    f.write_text(s2)
    done.append(f.stem)

f = lib / "DFN-10_L2.0-W2.0-P0.40-BL-EP.kicad_mod"
s = f.read_text()
if "(clearance 0.127)" not in s:
    s = s.replace("\t(attr smd)", "\t(attr smd)\n\t(clearance 0.127)", 1) if "\t(attr smd)" in s \
        else re.sub(r'(\(module [^\n]*\n)', r'\1\t(clearance 0.127)\n', s, count=1)
    f.write_text(s)
    done.append(f.stem)
print({"fixed": done})
