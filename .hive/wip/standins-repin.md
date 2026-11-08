# WIP: drop the replaced stand-ins (paused 2026-11-08 by the person's HWDE pause)

State of this branch:
- core and lite-main re-pinned at hifi-receiver 81443a6 (PR #180 merged: the 9 replaced scripts deleted).
- The 31 scripts new since the old pin are classified (core 13 design / 59 stand_in, lite-main 5 / 34, 0 unclassified).
  Design calls: core kicad/gen/fab.sh (the board's build recipe: drc_routed, fab_export, bom_cpl, dfm in order);
  core kicad/gen/power_trunk.py (the 5 A In2 plane plan, edits go through route_edit, as power_zones.py);
  lite-main kicad/gen/banana_route.py (prints route_edit ops only, edits nothing).
- bin/pin.py keeps bare_parts across a re-pin.
- lite-main architecture/constraints.json and part C364296 dropped: removed on the board (2095875, d18bc76).
- Ledgers (pinned/known/*.json) NOT yet updated: score --draft-known against this pin still to run.

Verdicts drafted for check_current advisory findings on core (diagnostics in the run notes):
- real: VBUS_IN 7 vias at R1:1 (44.41,180.5): J1's whole current leaves the In2 plane there, 10 needed at 0.5 A/via.
- real: VBUS 5 vias at Q1 source (56.06,176.4): Q1 takes the whole PVDD current; same path as path_capacity VBUS R1->Q1.
- false: PVDD B.Cu staircase x93-95 y162-166 + via (93.1,161.7): island feeds only C7, both taps in one In2 piece (parallel to the plane).
- false: PVDD via (54.16,170.25): one of 11 vias from Q1's drain pour to In2 (10 needed); clusters are not summed.
- false: +3V3 run x135.9-141 y188.7-190.1 + 2 vias: feeds R30 (4.5 mA) and U21 (reset supervisor), 3 taps in one In2 piece.
- false: VBUS 0.25 mm tracks (39.86,189.18) (46.03,189.35): Kelvin sense lines R1:2 -> R17/R19; stub refs count R1, the pad it hangs from.
- false: VBUS_IN via (45.59,175.71): D1 TVS only; counted as an unbounded load under the half-the-loads rule.
- false: VBUS vias (59.69,167.64) C8/D2/R10 and (60.5,180.7) Q3:3 (S8050 OV sense, listed as a rail source): mA or less.
- false: all 181 derived-GND findings (137 stitch vias, 41 In2 0.2 mm tracks, 3 necks at x142-146): the derived return screen
  charges the 5 A rail budget to every GND item, with In1/B.Cu planes in parallel.
Issues still to file (none exist; checked #635 #816 #828 #844 #845 #846): derived-GND screen; plane-fed island tapped at
several places in one fill piece held at full budget; stub refs counting the anchor pad's part; TVS/zener as unbounded load;
signal transistor taken as a rail source; via clusters on one pour not summed.

Left: draft-known score, judge the remaining ~267 entries (route_style, ratings, thermal, body, dfm...: draft verdicts in the
hwde-standins thread notes), file the issues, score (9 scripts gone; stand-in totals 59 core / 34 lite-main), test, test-slow, PR, tick rung.
