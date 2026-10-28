# hwde-led-test - learnings

## 2026-10-28 [P3][parts] part-sourcer must write refdes on every parts.json line
bom_cpl maps ref -> LCSC through each line's `refdes`; this board's lines had
none, so BOM.csv shipped an empty LCSC column until P9 (bom_unsourced, 4 parts).
The schematic reviewer saw it at P4 as a warning.

## 2026-10-28 [P3][parts] keep parts_search attributes in parts.json
check_ratings reads the voltage rating from the line's `attributes`; dropping
them left C1 and J1 unrated (they carry "Voltage Rating" 16 V and 250 V).
R1's "Voltage-Supply(Max)" 75 V does not match V_ATTR_RE, so it stays unrated.

## 2026-10-28 [P3][library] EasyEDA 0603 courtyards are the body outline
C0603, R0603 and LED0603-RD came with +-0.80 x +-0.40 mm courtyards while the
pads reach +-1.15 x +-0.45; widened by hand to +-1.40 x +-0.70.

## 2026-10-28 [P8][pdn] a board with no IC still needs its bulk cap in decoupling.json
check_pdn errors on a power rail with no cap association; C1 recorded against
J1.1 (the rail's source) cleared it.

## 2026-10-28 [P6][outline] fit margin must leave room for refdes silk
board_edit --outline fit measures copper and footprints, not reference text: at
1.0 and 1.5 mm margin the J1/R1/D1 refdes were clipped by the edge; 2.0 mm was clean.
