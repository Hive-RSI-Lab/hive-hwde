"""ep-via-pads: does a pull number the thermal vias in an exposed pad?

EasyEDA's TPA3118D2 (HTSSOP-32) footprint carries its exposed pad's thermal
vias as thru-hole pads with no number, so they get no net, and with paste
on them. lite-main (hifi-receiver-2) replaces them after every pull with a
numbered grid, no paste (lib/fix_footprints.py). The probe hands fpfix,
which lib_pull runs on every pull, an exposed pad "33" with three unnumbered
vias in it. Fixed when they come back numbered as the pad they sit in.
"""
import re

import _probe

import fpfix

ep = '  (pad "33" smd rect (at 0 0) (size 3.4 5.0) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
vias = "".join(
    f'  (pad "" thru_hole circle (at {x} {y}) (size 0.6 0.6) (drill 0.3) '
    f'(layers "*.Cu" "F.Paste"))\n' for x, y in ((-1, -1.5), (0, 0), (1, 1.5)))
pins = "".join(
    f'  (pad "{i + 1}" smd rect (at -3.6 {-4.5 + i * 0.65:.2f}) (size 1.4 0.4) '
    f'(layers "F.Cu" "F.Paste" "F.Mask"))\n' for i in range(4))
fp = ('(footprint "HTSSOP-32_probe" (layer "F.Cu")\n' + pins + ep + vias
      + '  (fp_line (start -4.5 -6) (end 4.5 -6) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
      + '  (fp_line (start 4.5 -6) (end 4.5 6) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
      + '  (fp_line (start 4.5 6) (end -4.5 6) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
      + '  (fp_line (start -4.5 6) (end -4.5 -6) (stroke (width 0.05) (type solid)) (layer "F.CrtYd"))\n'
      + ')\n')
new, _ = fpfix.sanitize(fp)
names = re.findall(r'\(pad "([^"]*)" thru_hole', new)
if len(names) != 3:
    raise SystemExit(f"fpfix left {len(names)} of the 3 via pads")
if all(n == "33" for n in names):
    _probe.fixed("fpfix numbers the exposed pad's vias 33")
_probe.stands(f"the exposed pad's vias come back numbered {names}")
