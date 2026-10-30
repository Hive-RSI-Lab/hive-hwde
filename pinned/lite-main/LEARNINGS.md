# lite-main: learnings

## 2026-10-28 [P2][parts] a 0.4 mm-pitch PD sink under a USB-C does not fan out on 2 layers
The CH224Q's DFN-10 puts VBUS (pin 8) between CC1 and CFG1 with 0.176 mm
gaps. Under the receptacle, with CC, D+/D- and VBUS all escaping in the same
5 mm, Freerouting left CC1/CC2/CFG1 unrouted at every clearance tried. The
CH224A (same die, ESSOP-10 at 1.0 mm) routed first time.

## 2026-10-28 [P3][library] check every EasyEDA courtyard against its pads
20 of 21 pulled footprints had courtyards smaller than their pads. The
HTSSOP-32's stopped 0.7 mm inside its pin toes, and the USB-C's 0.9 mm short.
Fixed board-locally in lib/fix_courtyards.py (hive-hwde brief 261028-141932).

## 2026-10-28 [P3][library] EasyEDA connector footprints can miss mechanical features
The XDJK-0271-1650 stacked RCA had no locating-peg holes and no body
courtyard. The USB4105's pegs were plated with no copper ring. Read every
connector's pad list before placement.

## 2026-10-28 [P4][erc] ERC 0/0 with blanket-passive pins checks nothing
lib_pin_types without datasheet extracts makes every pin passive; ERC then
passes, and catches no pin conflicts. The fresh-context reviewer's pin-by-pin
pass found the real error (soft-start cap on the wrong node).

## 2026-10-28 [P7][routing] pours, not per-net widths, for 5 A rails on fine-pitch parts
rules_gen applies one min width per net to every track. At 5 A that is 2.4 mm,
which no 0.6 mm USB-C pad, ESSOP pin or 0.34 mm HTSSOP pin can take. VBUS and
PVDD became pours with plane_fed true, and current_a was sized for the taps.
The pours are drawn from board coordinates after board_init (kicad/gen/power.py).

## 2026-10-28 [P7][routing] board_init re-centres: never keep absolute coordinates across runs
A pre-routed VBUS stub generated from an older outline origin landed 0.3 mm
off and shorted a GND pad.

## 2026-10-28 [P7][routing] Freerouting knows neither NPTH holes nor the hole-to-hole floor
It routed 0.16 mm from RCA locating pegs, and placed vias 0.46-0.49 mm apart
hole to hole. A small rule area over each NPTH hole fixed the first.
Re-running the route and keeping the cleanest result, then nudging the last
via, fixed the second. stitch_vias has the same hole-to-hole blind spot.

## 2026-10-28 [P7][routing] a pour can be cut in two by its own connector's ground
The F.Cu VBUS island between the USB-C's right VBUS pad and Q1's source was
split by the receptacle's GND pad stub to its shell pin. The right VBUS pad
then hung on whatever track Freerouting happened to draw. The GND pad now
goes up to a via under the receptacle instead.

## 2026-10-28 [P8][thermal] a pad-down class-D on 2 layers needs its B.Cu ground unbroken
check_thermal put the TPA3118 at ~62 C/W with a PVDD pour on B.Cu up to its
edge. Pulling the pour back to the inductors, 16 EP vias with solid
connection, and a top-side ground pour all go toward TI's 22 C/W EVM figure.
Bring-up must measure the chip at 1/8 power.
