# hwde-led-test - first-spin review: DFM / assembly

Cold review, 2026-10-29, against .claude/skills/hwde/reference/jlc_capabilities.yaml (2layer_1oz).
Tools: kicad-cli 10.0.6 DRC (0 violations, 0 unconnected, 0 parity), dfm_check (pass),
cpl_verify (4/4 ok), check_bom_sync (pass), a fresh Gerber/drill export diffed against fab/.

Verdict: buildable as is. 0 blockers, 0 majors, 1 minor, 3 notes.

## Findings

**DFM-1 (minor, new class) Stale B.Cu zone fill at the fab floor.** The saved GND fill clears
J1 pad 1 (+5V, THT, solder side) by 0.1275 mm; the zone's own clearance is 0.5 mm and a refill
gives 0.5005 mm. fab/gerbers carries the stale fill (fresh export = 0 diff lines). Passes
DRC/dfm_check because 0.1275 >= 0.127. Margin +0.4 % on the floor, -75 % on intent; a THT joint
on that side. Fix: refill zones, re-export Gerbers, re-run dfm_check.

**DFM-2 (note, DFA-15)** Board 13.40 x 10.50 mm, no fiducials; short side +5 % over the
commonly quoted 10 mm PCBA minimum (not in the capabilities file). JLC may add rails.

**DFM-3 (note, FAB-07)** GND vias 0.6/0.3 (annulus 0.15 = floor), 0.13 mm from D1 pad 1 and
0.14 mm from C1 pad 2; tented (no mask openings in F/B mask Gerbers), same net.

**DFM-4 (note, TEST-08)** No board name/rev on silk.

## Passed, with margins

| Item | Floor | Board | Margin |
|---|---|---|---|
| Track width | 0.127 | 0.200 | 57 % |
| Copper clearance (F.Cu, DRC) | 0.127 | DRC clean | >= 0 |
| Copper to edge | 0.3 | 0.675 (B.Cu pour), 2.4 (pads) | 125 % |
| Hole to edge | 0.5 | 2.83 (J1) | 466 % |
| Hole to hole | 0.5 | 1.34 (J1) | 168 % |
| PTH annulus J1 | 0.15 | 0.40 | 167 % |
| Via drill / dia | 0.3 / 0.6 | 0.3 / 0.6 | 0 % (JLC standard) |
| Silk stroke | 0.15 | 0.15-0.25 | 0 % |
| Paste | - | 1:1 on 0603 pads, none on THT | ok |
| CPL | JLC models | C1 270, D1 90, J1 270, R1 0; offsets 0.0 mm (cpl_verify) | ok |
| CPL vs board | - | positions = board (Y negated), rotations = board | ok |
| BOM | LCSC codes | 4 lines, all LCSC, match parts.json (check_bom_sync) | ok |
| Sides | top SMD only | C1, R1, D1 F.Cu; J1 THT | ok (brief s.7) |
| Outline | closed | 4 lines + 4 r1.0 arcs, 13.40 x 10.50 | ok |
| Tombstoning | symmetric | 0603 pads each fed by one 0.2 mm track (D1/C1 GND pad adds a via) | low |
