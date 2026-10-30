# Board review 2 - ultra-front (verify-reviewer, fresh context, re-check after E1/E2 fixes)

Inputs: kicad/ultra-front.kicad_pcb (digest b2eadfec...), kicad/constraints.json, README.md,
reports/{verify,dfm,drc_routed,plane_repair,stitch,verify-waivers}.json, reports/checks/*.json,
fab/CPL.csv, datasheets HS20HS072RX_C5329582.pdf p.10 (re-measured at 600 dpi, 27.63 px/mm) and
C2856829.pdf (repaired: stripped startxref + mutool clean; land pattern and section view read).
Footprint and courtyard data came from KiCad python on the board file. Fresh review renders
(top/bottom/iso, 2400 px) were made into a scratch dir; they match reports/renders/*.png, which carry
the same timestamp as the PCB. Coordinates below are absolute KiCad mm, with (u,v) from the board's
top-left corner where useful (abs = (-9.945+u, 26.8+v)).

Gate re-run: `hwde gate --gate drc_routed --no-record`: PASS, 0 failing / 0 total.
verify.json: pass, 19 findings (2 mating errors with waivers pending, 9 route-style jogs, 8 ratings
"no extraction"). dfm.json: pass, 0 findings, but `placement_visual` was SKIPPED (see W4).

Result: 0 errors, 5 warnings, 7 info.

## E1 re-check: the LCD tail now seats, but only at a panel position nothing records

- **J2 geometry** (FPC-05FB-12PH20, rot 90, mouth facing +x):
  - The signal pads are at x -8.265 (outer edge -8.87). The shell pads 13/14 are at x -5.025
    (x -5.92..-4.12).
  - The drawing says the body front face is 0.95 mm past the shell pads' front edge, so the mouth
    face is at x = -4.12 + 0.95 = -3.17. The courtyard edge is at -3.06..-3.13, which agrees.
  - The section view gives an insertion depth of 2.10 mm. So the **insertion stop is at x = -5.27**
    (-5.2 if you take it from the courtyard).
- **Tail end** (p.10: 20.7 +/-0.5 mm from the panel's left edge):
  - At the intended panel position u 25.45, the panel's left edge is at abs x 15.505. The tail end
    then lands at **x = -5.195** (range -5.695..-4.695).
  - Nominal error against the stop is 0.0-0.08 mm, so it **seats**. A tail at the long tolerance
    limit leaves up to 0.5 mm of slack, which needs the panel 0.5 mm in +x. The LCD keepout
    (x 14.555..67.555) allows +0.25. SW1-SW3 are 2.95 mm away, so the keepout could grow.
  - A tail at the short tolerance limit is still inserted 1.6 mm.
- **Lateral position:** LCD pin 1 is 15.28 mm below the panel top (p.10, measured to the contact
  top edge). With the panel top at y 28.7 (v 1.9), pin-1 contact centre is at y ~44.1 and the tail
  spans y 43.54..50.04. J2 pin 1 is at y 44.25 and pin 12 at 49.75, so the offset is 0.1-0.25 mm,
  well inside the +/-0.5 tolerance and the tail's lateral flex. Pin 1 and orientation are correct.
- **The catch** is W1: the u 25.45 position exists nowhere in the repo. The README still says the
  panel is at u 25.0-76.8.

## E2 re-check: no part under the tail or the panel - HOLDS

I used the real outline from p.10 at the intended panel position:
- The wide section is 20.56 mm wide, 7.31..27.87 mm below the panel top and 10.13 mm out from the
  panel edge. That is x 5.375..15.505, y 36.01..56.57.
- The narrow section is 6.5 mm wide: x -3.17..5.375, y 43.54..50.04.
- The panel covers x 15.505..67.305, y 28.7..64.9.

Constraint keepouts: wide x 4.855..15.555, y 35.6..56.7; narrow x -3.045..4.955, y 42.8..51.3;
panel x 14.555..67.555, y 28.3..65.3. All three cover the real outline.

Every F.CrtYd was checked against these three regions. No courtyard intersects any of them, except
J2's own mouth edge (0.04 mm, expected). The closest parts:
- **R13** (12.53..13.98, 56.70..59.59): only **0.13 mm** below the wide section's bottom edge. The
  keepout edge (56.7) touches its courtyard. Its pad edge is about 0.4 mm from the sub-flex. This is
  acceptable, but it is the tightest spot (I1).
- **U1** courtyard bottom at 35.53: 0.48 mm above the wide section.
- **C2/C3/Q1** (top edges 51.40/51.40/51.53): 1.4-1.5 mm below the narrow end.
- **R8** (y 39.48..40.92): 2.6 mm above the narrow end.
- **J1** courtyard to x 11.79 at y >= 60.19: clear of both the panel and the tail.
- **SW1-SW3** at x 70.26: 2.95 mm clear of the panel.

Only tented vias sit under the tail: GND (9.62, 36.22) under the wide section and +3V3 (2.81, 48.11)
under the narrow one. One via sits under the panel: /IR_OUT (19.43, 46.85). Tenting is on for both
sides.

Waiver `mating_zone_blocked` (verify-waivers.json) is now factually correct. I recommend approving
both J2 mating waivers.

## Warnings

### W1 - The panel position that makes E1 work is not recorded; README is stale (review/fab)
- README "Where things are" still gives the LCD panel at u 25.0-76.8. The intended position is
  u 25.45-77.25 (abs x 15.505..67.305).
- If the enclosure window or the assembler places the panel by the README, the tail end lands at
  x -5.645 against the stop at -5.27. That is 0.45 mm too much tail, and up to 0.95 mm at the tail
  tolerance limit, so the tail bows. The panel cannot slide once it is taped and windowed.
- Other README text is wrong for this board:
  - "Both layers are poured with GND ... a top pour joins them": the F.Cu pour is gone.
  - The checks table says 14 warnings and 6 jogs; it is now 17 warnings and 9 jogs.
- The LCD keepout is also not centred on the intended panel (0.95 mm slack at -x, 0.25 mm at +x).
- Fix:
  - Put the panel outline (x 15.505..67.305, y 28.7..64.9) in the README.
  - Draw it on F.Fab or User.Drawings, plus a silk tick at the panel's left edge, so the
    tape-assembly step has a reference.
  - Re-centre the keepout or extend it to x 68.1 to keep the +0.5 mm tolerance room.

### W2 - The B.Cu GND pour is 5 regions, not one; plane_repair.json is stale and says 1 (plane)
The filled polygons on the current board (refilled, same result):
- isl4 main: 3297 mm2, 15 GND vias.
- isl0: **179 mm2**, x 8.37..19.46, y 35.1..59.3, under the wide tail and the LCD's left edge.
  - It is fenced by B.Cu /ENC_B (x 7.8, y 35.6..59.9), /I2C_SCL and /IR_OUT.
  - Its only anchor is the U1 pin-12 GND via at (9.62, 36.22).
- isl1: 37 mm2, x -5.87..2.13, y 46.6..58.5, between /LCD_SCK and /LCD_CS. It has 3 vias.
- isl2: 4.7 mm2, x 2.73..4.91, y 49.0..53.1. It has 1 via, at (3.40, 51.45).
- isl3: 1.5 mm2, a 0.4 x 4 mm sliver at x 0.18..0.57, y 31.3..35.3. It has 1 via, at (0.51, 32.6).

Consequences:
- U1's GND pin reaches the main pour only through about 18 mm of 0.2 mm F.Cu. The path runs
  (9.62, 35.55) -> (0.27, 36.78) -> (-0.13, 34.4) -> C1 GND. There is also a branch to J2 shell pad
  14 via (-3.82, 40.9).
- isl0 is a 179 mm2 single-point-grounded patch under the LCD.

Electrically everything is connected, so DRC shows 0 unconnected. plane_repair.json was made from an
earlier digest (9fa85a3c...), and stitch.json (86 vias) is from the old two-pour board.

Fix (plane/router):
- Add a GND via inside isl0 near (12, 45) with a short F.Cu tie, or a second U1-GND via that lands in
  isl4 (e.g. a short F.Cu stub to x ~11, y 34).
- Re-run plane_repair on the final board.
- Drop isl3: raise the zone's min width, or delete the island.

### W3 - SPI has no GND reference under it, and CS/DC/MOSI share one unshielded bundle (router)
- /LCD_SCK, /LCD_MOSI, /LCD_CS and /LCD_DC run on **B.Cu**, 17-22 mm each. The path goes from J2
  vias (-6.48..-3.69, 44.6..46.2) diagonally to J1 vias (0.91..4.07, 60..63).
- With the F.Cu pour removed, nothing on F.Cu above them is a plane: the narrow-tail corridor has
  only scattered +3V3/GND traces. So there is no continuous return "under" SPI. The only return is
  coplanar B.Cu:
  - SCK has isl4 on its outer side and isl1 on its inner side, so it is fenced. Good.
  - CS, DC and MOSI run as a 3-trace bundle with 0.3-0.5 mm gaps and no GND between them.
  - /LCD_DC has no adjacent GND for about 12 mm.
  - MOSI's other side is isl2/isl0, each tied by a single via.
- At <= 15 MHz SCK (schematic review W1) this should still work. The risks are coupled MOSI edges
  onto CS/DC and radiated edge harmonics, not a functional failure at bring-up.
- `check_return_path` passed with `"checked": []`. It analysed nothing, so this is a hole in the
  checks, not a pass.
- Fix: route the four SPI nets on F.Cu through the tail corridor. Copper is allowed there; the
  keepout is for parts. That puts them over the unbroken B.Cu pour and also removes isl1 and isl2.
  If they stay on B.Cu, add a GND guard trace with 2-3 vias between SCK/CS and MOSI.

### W4 - JLC rotation of the CPL is unverified: dfm placement_visual SKIPPED (fab)
- dfm.json says: `skipped_error: placement_visual - no cpl_visual.json`.
- fab/CPL.csv has non-trivial rotations: J2 90, SW4 180, C1/R4-R7/R13/R17-R19 90, plus U1, U2, D1
  and the J1 FPC at 0. Footprint-origin and rotation conventions for J1, J2, U1 (TSSOP pin 1), U2,
  D1 (RGB polarity) and SW4 are exactly where JLC placements go wrong.
- This must be confirmed in the JLC placement preview, or by running the fab image pass, before
  ordering.

### W5 - R10 refdes ink touches the top board edge (silk)
- The R10 text (82.355, 27.54), 1.0 mm high, has its ink top at y 26.92. With the 0.15 mm stroke
  that is y 26.85, only 0.05 mm inside the edge at y 26.8.
- JLC clips silk at the edge, so the top of "R10" will be cut. check_silk/dfm silk did not flag it.
- Move it to below R10 (around y 28.9), or hide it.
- J1's body silk at y 66.72 (0.08 mm from the edge) gets clipped the same way. That is cosmetic.

## Info

- I1 - The R13 courtyard touches the wide-tail keepout (56.70 vs 56.7), 0.13 mm from the real
  sub-flex outline. If space allows, move R13/R17/R18 0.5 mm in +y or -x. The sub-flex K/A joints
  face this corner.
- I2 - Decoupling is sensible.
  - C1 (0.655, 30.8): +3V3 pad to U1 pin 24 (2.485, 28.38) is 3.6 mm straight line and about 4.4 mm
    of 0.2 mm track. Its GND leg is 1.0 mm to via (-0.1, 29.4) into the main pour. U1's GND return
    is the long one (W2). That is fine for a 400 kHz I2C expander.
  - C4 (-5.945, 53.8): 5.8 mm from J2 pins 8/9, fed by a 0.15 mm track at x -9.16, which is
    0.71 mm from the edge (OK for JLC). Its GND leg is 0.99 mm to via (-4.5, 53.15).
  - The main +3V3 feed to J2 pin 8 comes from the U1 side, so C4 sits on a stub. It would be better
    right at pins 8/9, but it is not a bring-up risk. check_decoupling agrees (loop 6.7 nH).
- I3 - W2 from the first review is FIXED. The H1-H4 refs are hidden (check_silk shows refdes_off_silk
  H1-H4 "hidden"), and I found no other silk past the outline (W5 is on the edge, not past it).
- I4 - W3 from the first review is FIXED in its old form: the F.Cu strips are gone. W2 above is what
  replaced it.
- I5 - A 66 mm F.Cu GND track runs along the bottom edge (y 65.7, x 0.3..66.7), just outside the
  panel (y <= 64.9). It is redundant with the B.Cu pour. It is harmless.
- I6 - Carried over, still open: the first review's W1 (front-panel height stack: LCD 2.05 mm,
  tacts 1.5 mm, EC11 frame) and I9 (no board name/revision on silk, no LCD pin-1 legend at J2).
- I7 - The FPC thickness matches. The connector wants a 0.30 +/-0.02 mm FPC with >= 3.0 mm exposed
  contacts. The tail is 0.3 mm thick with 3.5 mm contacts (p.10). J2 being dual-contact removes the
  face-up/face-down risk.

## Warnings triage (verify.json)
- 9 x route_style jogs: cosmetic. In the renders the buses run parallel and nothing wanders. The
  waiver is justified.
- 8 x check_ratings no-extraction: as in the first review, no rail exceeds 5.25 V. The waiver is
  justified.
- 2 x check_mating (J2 faces inward, zone blocked): both are now JUSTIFIED. The waiver text matches
  the geometry above. Recommend approval.

FILES: reports/review-board-2.md (renders regenerated to a scratch dir only; no other file changed)
GATE: drc_routed PASS 0/0; review 0 errors / 5 warnings / 3 waivers recommended (route_style jogs, ratings, J2 mating x2)
SUMMARY:
- E1 fixed: tail end x -5.195 vs J2 stop -5.27 (mouth -3.17, 2.10 mm insertion); pin 1 within 0.25 mm. Seats.
- E2 fixed: no courtyard under the wide tail (x 5.375..15.505, y 36.0..56.6), narrow tail or panel; R13 0.13 mm, U1 0.48 mm are the closest.
- W1 the panel position u 25.45 is recorded nowhere; README still says u 25.0, which puts 0.45-0.95 mm of excess tail into the J2 seat; README pour/check text is stale.
- W2 B.Cu GND pour is 5 islands (179 mm2 isl0 on one via, the U1 GND via); plane_repair.json is stale and claims 1.
- W3 SPI on B.Cu has no reference plane; CS/DC/MOSI bundled with no GND between; check_return_path checked nothing.
- W4 JLC CPL rotations unverified (dfm placement_visual skipped).
- W5 R10 refdes ink 0.05 mm from the top edge.
OPEN: tail/panel fit in the real enclosure (height stack, window position) and JLC placement preview rotations cannot be judged from files; the coupling level on the CS/DC/MOSI bundle is a judgement, not measured.
