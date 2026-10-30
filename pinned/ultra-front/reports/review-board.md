# Board review - ultra-front (verify-reviewer, P8, adversarial)

Inputs: kicad/ultra-front.kicad_pcb (routed, 2L, GND pours F+B), .net, kicad/constraints.json,
kicad/decoupling.json, reports/verify.json, reports/drc_routed.json, reports/verify-waivers.json,
requirements.md, architecture/*.md, reports/review-schematic.md, datasheets
HS20HS072RX_C5329582.pdf (p.10 outline, p.11 pinout) and C2856829 (FPC-05FB-12PH20 drawing,
repaired copy). Renders: the fixed review set was re-rendered (top/bottom/iso, 2400 px) from a
scratch copy and agrees with reports/renders/ultra-front_top.png, _bottom.png, _iso.png. I also read the
F.Cu/B.Cu PDFs (ultra-front-F.Cu.pdf, ultra-front-B.Cu.pdf). Build mode: none declared, so this is a normal design.

Coverage note: reports/checks/summary.json (and reports/verify_all.json) on disk come from a run
without --constraints/--decoupling. In that run return_path, current, decoupling, creepage, thermal,
pdn and ratings show as SKIPPED. I re-ran verify_all with kicad/constraints.json and
kicad/decoupling.json into a scratch dir. The result matches reports/verify.json: 16 findings
(2 mating errors, waived but pending; 6 jog warnings; 8 ratings warnings), and every other check
passes. So the gate is right, but the per-check evidence files in reports/checks/ are stale. Re-run
verify_all with the constraints so the files on disk match the gate.

Result: 2 errors, 3 warnings, 9 info.

## Errors

### E1 - LCD tail cannot seat in J2: J2 is ~3.1 mm too close to the panel (placement)
- Where: J2 at (-3.945, 47.0) rot 90, against the LCD keepout x 14.555..67.555 (constraints.json).
- Drawing (HS20HS072RX p.10): the tail end is 20.7 +/-0.5 mm from the panel's outer edge. The panel
  is 51.8 mm, centred in the 53 mm keepout, so its left edge is x = 15.155 (range 14.555..15.755).
  The tail end therefore lands at x = -5.545 (range -6.6..-4.4 with the tail tolerance).
- Connector (C2856829 drawing): the body is 4.9 mm deep. The pads are at the back (lead side). The
  section view gives an FPC insertion depth of 2.10 mm from the mouth face. From the footprint, the
  mouth face is at local y +3.5 (courtyard 3.54), i.e. board x = -0.4. So the insertion stop is at
  x = -0.4 - 2.1 = -2.5.
- The tail end must sit at the stop (-2.5), but the panel position puts it at -5.5. That is
  3.0 mm of tail too much (2.0 mm at best case of all tolerances). The tail would have to sit 3 mm
  past the body's back wall, right on the solder pads (pad centres x = -5.565). This looks like the
  placer lined the tail end up with J2's pads instead of its insertion stop. In practice the tail
  bows up between J2 and the panel, the panel cannot be taped where the window expects it, or the
  ZIF closes on a buckled tail.
- Fix (placement): either move J2 about 3.1 mm in -x, to roughly x = -7.05 (pads at -8.6, pad outer
  edge at -9.25, which is 0.7 mm from the edge at -9.945; C4 at (-8.145, 48) must move, e.g. next to
  J2 pins 8/9), or move the LCD keepout and panel 3.1 mm in +x (x 17.65..70.65). The second option
  collides with the SW1-SW3 courtyards at x 70.31, so the switch column would have to move about
  0.5 mm too. Then rebuild the tail corridor keepout from J2's mouth to the panel.
- Orientation and pin 1 are CORRECT. The mouth faces +x (back-flip body, leads at -x). The drawing
  shows pin 1 on the panel's top side, 15.28 +/-0.5 mm from the panel top edge, so pin 1 is near
  y 28.7 + 15.3 + 0.15 = 44.1. J2 pin 1 is at y = 44.25 and pin 12 at y = 49.75. The pin-1 silk dot
  is at (-5.9, 43.5). J2 is dual-contact, so the face the contacts are on does not matter.

### E2 - The real tail outline covers 7 parts; the corridor keepout models only the narrow end (placement)
- Drawing p.10: the tail is NOT 6.5 mm wide all along. For the first ~10.1 mm out of the panel edge
  it is ~20.5 mm wide. That section is 7.2..27.7 mm from the panel's pin-1-side edge and includes
  the soldered backlight sub-flex with its K/A joints. After that it narrows to 6.5 mm for the last
  ~10.6 mm. On this board, with the panel at the keepout centre, the wide section is the region
  x 5.05..15.16, y 35.9..56.4. The constraints only keep out x -0.145..14.555, y 42.8..51.3.
- Parts under the wide tail section (courtyards):
  - R19 (8.15..10.96, 38.72..40.08), fully covered
  - R11 (12.38..13.73, 52.2..55.0), fully covered
  - R13 (7.65..10.46, 52.92..54.28), fully covered
  - R17 (7.65..10.46, 55.32..56.68), mostly covered
  - U1, pins 5-12 (y 36.8, x 5.1..9.6, body 1.2 mm tall)
  - R7 (11.38..12.73, 33.9..36.7), partly covered
  - Q1 (to x 5.8), at the edge
  - R12 (to y 56.0), at the edge
- Consequences:
  - The tail cannot lie flat as requirements 5 promises. It rides over 0.5-1.2 mm of parts right at
    the panel edge, which lifts and twists the panel corner and stresses the tail.
  - The sub-flex K/A joints (drawing: 23-27.7 mm from the pin-1 edge, 4-6 mm out from the panel)
    land at about x 9-11, y 51.7..55.7. That is directly over the bare +5V and /BL_A terminals of
    R13/R17. If those joints face the PCB, LED-K can touch +5V.
  - Waiver 2 in verify-waivers.json ("parts sit beside the corridor, not in the tail's path") is
    therefore not true for R11. R13, R17 and R19 are also in the path.
- Fix (placement): make the tail keepout the real outline. That is
  [panel_left-10.6, panel_top+6.7, panel_left, panel_top+28.2] for the wide section and
  [J2 mouth, panel_top+13.9, panel_left-10.1, panel_top+22.0] for the narrow section, with 0.5 mm
  margin. Move R19/R11/R13/R17/R12/R7/Q1 out: there is room at x 5..15, y 56.5..60, above J1, and
  at x < 5 next to U1. Do this after E1 is settled, because E1 moves the tail.

## Warnings

### W1 - Front-panel height stack is not consistent: LCD 2.05 mm, tacts 1.5 mm, EC11 body ~6.5 mm (review)
- All user parts face the front panel, and the board screws to that panel (H1-H4). The parts have
  three different heights:
  - The LCD module (2.05 mm thick, p.10) wants to sit just behind the window.
  - SW1-SW3 (TS-1187A, H1.5 per the footprint name) sit 1.5 mm proud of the board.
  - The EC11E (SW4 at 89.055, 47.8) has a frame several mm tall plus a 20 mm shaft.
- One standoff height cannot satisfy all three. Either the panel is stepped/pocketed around SW4, or
  the LCD sits about 4 mm behind its window, or the buttons need printed plungers of about 5 mm.
  No document records this. The enclosure fit is not judgeable from the renders. Decide the stack
  (standoff height per hole and panel pocket depths) before release.

### W2 - H1 and H2 reference designators are printed off the board (silk)
- H1 refdes at (-6.89, 26.2) and H2 at (97.0, 26.2). The board edge is y = 26.8, so both texts sit
  0.6 mm outside the outline (visible above the outline in ultra-front-F.Cu.pdf). The fab clips or
  rejects off-board silk. check_silk did not flag them. Move them inside or hide them (mounting holes
  need no refdes).

### W3 - Long F.Cu GND strips under the LCD are stitched only at their ends; B.Cu sliver on one via (plane)
- The F.Cu pour between the parallel U1-to-buttons/LED traces under the LCD breaks into three long
  strips. Each is stitched only at its two ends:
  - frag x 15.2..80.0, y 46.2..57.5: vias (71.99, 48.44) and (77.06, 47.84) only, all at the right end
  - frag x 11.8..81.9, y 35.8..46.2: vias (71.99, 36.75) and (77.99, 36.86)
  - frag x 8.6..70.6, y 35.2..45.7: vias (12.5, 40.5) and (11.14, 39.32)
- B.Cu frag x 2.5..5.8, y 39.9..59.7 is a ~20 mm sliver tied by one via at (5.055, 54.63).
- These are 55-65 mm resonant stubs under the LCD and the PWM-dimmed backlight. They are harmless
  at DC, but they are not useful ground and they are cheap to fix. Add GND stitching vias every
  ~10 mm along each strip (the LCD keepout allows tented vias), or let the fill remove slivers below
  a minimum width.

## Info

- I1 - SPI return path is adequate. LCD_SCK/MOSI/CS/DC run J1 to J2 mostly on B.Cu, 15-25 mm long,
  under the F.Cu GND fragments at x -5..6. The largest stretch with no copper opposite is 2.6 mm
  (/LCD_MOSI at x 2.75, y 58.7..61.3, under J1/C2 pads). The other gaps are 1.0-1.7 mm where the line
  crosses F.Cu pads and traces. GND stitch vias sit next to the run at (-0.98, 51.09), (0.19, 54.84),
  (0.22, 57.70) and (-0.98, 42.58). This is acceptable at the <= 15 MHz the LCD supports (schematic
  review W1). If E1 moves J2 3 mm left, the runs get 3 mm longer; keep the same stitching.
- I2 - Vias under U1. Five signal vias sit under the TSSOP body: (7.27, 35.13) ENC_B, (4.37, 35.56)
  ENC_A, (8.54, 35.52) EXP_UNUSED, (6.73, 33.67) I2C_SCL, (2.34, 35.48) I2C_SDA. Via tenting is on
  for both sides (board setup), and there is no via-in-pad anywhere except J1 shell pad 22 (GND,
  (-2.385, 63.69), on a 2 x 2.5 mm mechanical tab: fine). Acceptable.
- I3 - Backlight path is OK. +5V from J1 pins 3/18 goes through R13 || R17 || R18 (3 x 120R 0603 =
  40R) to /BL_A, J2.10. LED-K at J2.11 goes to Q1 drain, and Q1 source goes to GND via (5.055, 54.63).
  The LED current is (5.0-3.0)/40 = 50 mA typ and (5.25-2.8)/40 = 61 mA max, under the 80 mA abs max.
  Each resistor dissipates <= 50 mW (50 % of 0.1 W), and 0.2 mm traces are fine at 61 mA. This is
  doc drift only: blocks.md B3 and power_tree.md still say "R13 33R 1206".
- I4 - J1 is correct. It sits on the bottom edge with its mouth facing +y (outward). The pads are at
  y 61.11, and the courtyard and front face reach y 66.56 against the edge at 66.8. Pin-1 dot at
  (-3.15, 60.74), next to pin 1 (x -0.695). The FFC exits straight off the edge.
- I5 - No parts under the LCD panel keepout (x 14.555..67.555, y 28.3..65.3). The nearest are R12 at
  x 13.73 and SW1-SW3 at x 70.31. The R12 refdes text at (14.91, 57.4) sits under the panel; it is
  hidden but harmless.
- I6 - Mounting holes: 4 x M2.5 (2.7 mm, 5.4 mm GND pad) at (-6.895 / 97.005, 29.85 / 63.75), a
  103.9 x 33.9 mm pattern, which meets requirements 5 (4 x M2.5). The pad copper is 0.35 mm from the
  board edge. Panel boss OD limits from nearby parts: H4 has J1's courtyard 3.26 mm from its centre
  (boss OD <= 6.4 mm), and H2 has U2 ~4.0 mm from its centre.
- I7 - Front-panel layout is sensible. LCD on the left; SW1-SW3 in a column at x 74.055 with 12 mm
  pitch; the EC11 knob at (89.055, 47.8) with its shaft and frame on GND (F/G); the IR receiver U2 at
  (89.055, 32.8) above the knob; D1 at (88.055, 60.4) below it. With an 18-20 mm knob, U2's
  downward view angle is partly shadowed. Put the IR window above the knob.
- I8 - Six unused TCA9535 I/Os share one net (/EXP_UNUSED) with a single R19 10k pull-down. This
  fixes schematic-review W3. Firmware must now leave them as inputs. blocks.md B4 says "outputs low",
  and if one were ever driven high it would fight the other five. This is a firmware/doc note.
- I9 - No board name or revision on the silk, and no "LCD pin 1" legend at J2. Add both, and silk the
  FFC type next to J1 (schematic review W2).

## Warnings triage (verify.json)

- 6 x route_style needless jogs (/BL_GATE, /BTN3, /LCD_DC, /LCD_RST, /LCD_SCK, /LEDG_K, all F.Cu).
  These are cosmetic sidesteps with no electrical effect. Waiving them is justified, but clean them
  up when the router re-runs after E1/E2, since the moves will reroute this area anyway. In the renders I
  found no other visual route-style problems: no arcs or off-45 segments, and the long button/LED
  buses under the LCD run parallel.
- 8 x check_ratings "no datasheet extraction" (D1, J1, J2, Q1, SW4, U1, U2, 19 resistors). I checked
  these by hand; nothing on the board exceeds 5.25 V:
  - Q1 AO3400A: 30 V / 5.7 A, carrying 61 mA.
  - U1 TCA9535: VCC 6 V max; I/O 5.5 V tolerant per the schematic review, with D1 anode at 5 V.
  - U2 IRM-H638T: 2.7-5.5 V, running at 3.3 V.
  - SW4 EC11: 10 mA / 5 V, carrying 0.33 mA.
  - FPC contacts: 0.5 A / 50 V class.
  - Resistors: <= 50 mW in 0.1 W parts.
  - Capacitors: 10 uF 25 V on 5 V, 4.7 uF 16 V on 3.3 V.
  Waiver justified. Running datasheet_extract for these parts would turn the warnings into real
  checks.
- 2 x check_mating (waived, approval pending with the person at H4):
  - mating_faces_inward (J2): JUSTIFIED. The mate is the panel's own tail, coming from +x on the
    board, so an inward mouth is correct.
  - mating_zone_blocked (J2): NOT justified as written. R11 (with R13, R17, R19) is genuinely in the
    tail's path (E2). Recommend approving only after E2 is fixed and the waiver text is corrected.
    R5, SW2 and SW4 are beyond the LCD and are fine to waive.

```json
{"violations": [
 {"check": "board-review", "severity": "error", "pos": [-3.945, 47.0], "layer": "F.Cu", "net": null, "refs": ["J2", "C4"], "msg": "LCD tail (20.7 mm from panel edge, p.10) ends at x=-5.5 but J2's insertion stop (mouth x=-0.4, 2.10 mm insertion per C2856829) is at x=-2.5: 3.0 mm of excess tail, J2 must move ~3.1 mm -x (relocate C4) or the LCD keepout +3.1 mm; orientation and pin 1 are correct", "source": "review.board", "kind": "fpc-tail-length-mismatch", "domain": "placement"},
 {"check": "board-review", "severity": "error", "pos": [10.1, 46.2], "layer": "F.Cu", "net": null, "refs": ["R19", "R11", "R13", "R17", "U1", "R7", "Q1", "R12"], "msg": "LCD tail is ~20.5 mm wide for its first 10.1 mm (p.10), region x 5.05..15.16 y 35.9..56.4; keepout covers only the 6.5 mm end; parts under the tail stop it lying flat and the backlight sub-flex K/A joints land over R13/R17 +5V terminals", "source": "review.board", "kind": "fpc-tail-over-parts", "domain": "placement"},
 {"check": "board-review", "severity": "warning", "pos": [89.055, 47.8], "layer": null, "net": null, "refs": ["SW1", "SW2", "SW3", "SW4", "H1", "H2", "H3", "H4"], "msg": "front-panel height stack undefined: LCD 2.05 mm, tacts 1.5 mm, EC11 frame several mm, all facing the panel the board screws to; needs stepped panel, plungers or deep LCD window", "source": "review.board", "kind": "enclosure-height-stack", "domain": "review"},
 {"check": "board-review", "severity": "warning", "pos": [-6.89, 26.2], "layer": "F.SilkS", "net": null, "refs": ["H1", "H2"], "msg": "H1/H2 refdes at y=26.2 sit 0.6 mm outside the board edge (y=26.8); fab clips/rejects off-board silk", "source": "review.board", "kind": "silk-off-board", "domain": "silk"},
 {"check": "board-review", "severity": "warning", "pos": [45.0, 46.0], "layer": "F.Cu", "net": "GND", "refs": [], "msg": "three 55-65 mm F.Cu GND strips under the LCD stitched only at their ends and a 20 mm B.Cu sliver (x 2.5..5.8) on one via: resonant stubs under the PWM backlight; add stitching every ~10 mm or remove slivers", "source": "review.board", "kind": "pour-strip-unstitched", "domain": "plane"}
]}
```
