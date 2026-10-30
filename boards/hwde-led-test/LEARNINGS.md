# hwde-led-test - learnings

## 2026-10-28 [P3][parts] part-sourcer must write refdes on every parts.json line
bom_cpl maps ref -> LCSC through each line's `refdes`; this board's lines had
none, so BOM.csv shipped an empty LCSC column until P9 (bom_unsourced, 4 parts).
The schematic reviewer saw it at P4 as a warning.
Fixed in the module (issue #5): the cause was bom_cpl reading only a footprint
field named `LCSC`, while lib_pull names it `LCSC Part`. It now reads both and
binds refdes-less lines to board refs by LCSC, so parts.json needs no refdes.

## 2026-10-28 [P3][parts] keep parts_search attributes in parts.json
check_ratings reads the voltage rating from the line's `attributes`; dropping
them left C1 and J1 unrated (they carry "Voltage Rating" 16 V and 250 V).
R1's "Voltage-Supply(Max)" 75 V does not match V_ATTR_RE, so it stays unrated.
Fixed in the module (issue #5): part-sourcer.md's example line now carries
`attributes`, V_ATTR_RE reads "Voltage-Supply(Max)", and check_ratings names
the lines that have no attributes.

## 2026-10-28 [P3][library] EasyEDA 0603 courtyards are the body outline
C0603, R0603 and LED0603-RD came with +-0.80 x +-0.40 mm courtyards while the
pads reach +-1.15 x +-0.45; widened by hand to +-1.40 x +-0.70. Later the same
day fpfix rule E made the repair at pull time (pads + 0.25 mm on a 0.05 mm
grid), `fpfix.py --pcb` applied it to the placed copies, and check_courtyard
(verify) fails a courtyard that misses its pads.

## 2026-10-28 [P8][pdn] a board with no IC still needs its bulk cap in decoupling.json
check_pdn errors on a power rail with no cap association; C1 recorded against
J1.1 (the rail's source) cleared it.
Fixed in the module (issue #5): check_pdn now counts caps the board puts straight
across a rail and ground when none is recorded; root.py's hand-written entry is gone.

## 2026-10-28 [P6][outline] fit margin must leave room for refdes silk
board_edit --outline fit measures copper and footprints, not reference text: at
1.0 and 1.5 mm margin the J1/R1/D1 refdes were clipped by the edge; 2.0 mm was clean.
Fixed in board_edit: fit now counts every silk item (check_silk's geometry),
and any outline edit refuses one that newly clips silk (`silk_clipped`).

## 2026-10-29 [P10][dfm] the saved zone fill was stale and the Gerbers carried it
The B.Cu GND fill cleared J1.1 (+5V) by 0.1275 mm against the zone's own 0.5 mm:
the board was saved after edits without a refill, DRC (netclass floor) and the
gerber DFM passed, and fab/gerbers were exported from it (first-spin DFM-1).
dfm_check now fails it (FAB-27, dfm_stale_zone_fill); the board was refilled
(kicad-cli pcb drc --refill-zones --save-board, 0.5005 mm) and the Gerbers
re-exported.
