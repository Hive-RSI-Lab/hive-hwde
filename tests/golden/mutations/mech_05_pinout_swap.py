"""Mutant: mech-05-pinout-swap (usbbuck4).

SIDECAR fault (MECH-05, a mated pair's pinout swapped). The SWD header J2
(1x04, +3V3 / SWDIO / SWCLK / GND) is declared in constraints.json as mated
with J1 of a small daughter board, mate/swd-daughter.kicad_pcb: a 1x04 socket
on the daughter's B.Cu, stacked over J2 the same way up. The daughter's
socket has +3V3 and GND swapped across the pair (its pin 1 is GND and pin 4
is +3V3), so mating the boards shorts +3V3 to GND. usbbuck4's own copper is
untouched. Must be caught by check_mate_pins (kind "mate_pin_mismatch",
ref J2, at J2 pin 1).

`daughter(swap=False)` builds the same daughter wired straight; the unit
test uses it as the clean case.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

MATE = "mate/swd-daughter.kicad_pcb"
# J2 at (156.5 110.5 -90): pad n sits at x 156.5 - 2.54 (n-1), y 110.5
J2_PADS = [("1", "+3V3"), ("2", "/SWDIO"), ("3", "/SWCLK"), ("4", "GND")]
AT = (156.5, 110.5)


def daughter(swap: bool) -> str:
    nets = dict(J2_PADS)
    if swap:
        nets["1"], nets["4"] = nets["4"], nets["1"]
    pads = "".join(
        f'    (pad "{n}" thru_hole {"rect" if n == "1" else "circle"}'
        f' (at {-2.54 * (int(n) - 1):.2f} 0) (size 1.7 1.7) (drill 1)'
        f' (layers "*.Cu" "*.Mask") (net "{nets[n]}")'
        f' (uuid "00000000-0000-4000-8000-00000005050{n}"))\n'
        for n, _ in J2_PADS)
    x0, y0 = AT[0] - 14.0, AT[1] - 6.0
    return (f"""(kicad_pcb
  (version 20260206) (generator "mech_05_pinout_swap")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
  (setup)
  (gr_rect (start {x0} {y0}) (end {x0 + 20.0} {y0 + 12.0})
    (stroke (width 0.1)) (fill no) (layer "Edge.Cuts")
    (uuid "00000000-0000-4000-8000-000000050500"))
  (footprint "Connector_PinSocket_2.54mm:PinSocket_1x04_P2.54mm_Vertical"
    (layer "B.Cu")
    (at {AT[0]} {AT[1]})
    (property "Reference" "J1" (at 0 -2.8 0) (layer "B.SilkS"))
    (attr through_hole)
    (fp_rect (start -9.4 -1.8) (end 1.8 1.8) (stroke (width 0.05))
      (fill no) (layer "B.CrtYd"))
{pads}  )
)
""")


def surgery(text):
    cons = mutlib.golden_json("usbbuck4", "constraints.json")
    if "mating_pairs" in cons:
        raise mutlib.SurgeryError("golden constraints already declare pairs")
    cons["mating_pairs"] = [{"ref": "J2", "mate_ref": "J1", "mate_pcb": MATE,
                             "mount": "stack"}]
    return text, {"ref": "J2", "mate": f"{MATE}:J1",
                  "swapped": {"1": "GND", "4": "+3V3"},
                  "sidecars": {"constraints.json": mutlib.dump_json(cons),
                               MATE: daughter(swap=True)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("mech-05-pinout-swap", "usbbuck4", surgery))
