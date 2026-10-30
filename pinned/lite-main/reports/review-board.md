# lite-main: board review (verify-reviewer, fresh context)

Board `kicad/lite-main.kicad_pcb` (digest ec2b7102...), 2 layers, 100 x 90 mm.
Coordinates are given as board u/v (mm from the outline's top-left, as in
`kicad/gen/place.py`). The JSON gives absolute KiCad coordinates (u + 22.865,
v + 60.323).

Renders (2D, kicad-cli PDF + pdftoppm; 3D renders were skipped on purpose
because the machine is overloaded):
- `reports/renders/review-top.png`: F.Cu, silk, fab, edge
- `reports/renders/review-bottom.png`: B.Cu, edge (seen from the top)
- `reports/renders/review-top-amp.png`: zoom on J1/Q1/J4, the amp and its output filter
- `reports/renders/review-top-antenna.png`: zoom on the ESP32 antenna and keep-out
- `reports/renders/review-bottom-mid.png`: zoom on the B.Cu ground between the bucks and the amp

Result: **2 errors, 10 warnings.**

## Errors

### E1. B.Cu ground slit under both bucks; the 3.3 V buck's input loop closes ~40 mm away
(`review-bottom.png`, `review-bottom-mid.png`)

`/USB_DP` and `/USB_DM` run on B.Cu from J1 (v 11.5) straight down to U6 CH340C
(v 61) at u ~52, with `+3V3` on B.Cu right beside them (u 52.7, v 13.5-34, then
diagonally to u 57.37 and down to v 54). Together they slit the only ground
plane for 50 mm, between the codec/MCU half and the power/amp half.

- **U3 SY8089:** its GND pin goes through a via at (54.01, 43.59). That via lands
  right of the slit, in a B.Cu pocket closed by the +3V3 track. On F.Cu it is
  an island of its own, shared only with C12. Its input caps C10 and C11 ground
  through vias at u 47.5, left of the slit, and their F.Cu copper doesn't
  connect to U3's island. So the 1.5 MHz input hot loop goes around the
  slit's end near v 61, not across 5 mm.
- **U2 TPS54302:** C4 (4.7 uF input) has the same fault, through its via at
  (50.77, 22.51). C3 and C5 are on the right side and are fine.
- **Consequence:** a large di/dt loop that radiates next to U4 LP5907 and the
  codec, more ringing on +3V3, and ground bounce in the codec half.
- **Why the checks passed:** check_return_path and check_decoupling are green.
  They don't see slits made of traces, or a decoupling cap whose ground is on
  another island.
- **Fix (router, maybe placement):** put the USB pair on F.Cu, or move U6 next
  to J1 so the pair is a few mm long. Put the +3V3 trunk on F.Cu. Give U3's GND
  and C10/C11's GND one copper area on F.Cu.

### E2. TVS D1 is not at the connector, and 0.2 mm track joins it to VBUS
(`review-top-amp.png`)

D1 (SMBJ20A) sits at (61, 20), about 17 mm from J1. It is on the buck branch,
and its only path to J1 is ~20 mm of 0.2 mm track and three 0.3 mm vias:

J1 B4/A9 -> via (56.1, 8.9) -> B.Cu -> via (59.84, 12.53) -> 0.2 mm F.Cu past U1 -> C1 -> via -> B.Cu -> via -> C4/C3/C5 -> D1.

It never touches the VBUS pour at Q1's source, or J1's pads.

- **Consequence:** the series L/R of that track decouples the TVS from J1, Q1
  and CH224A pin 8 during hot-plug overshoot and surges. A real surge (Ipp
  18.6 A) would fuse the 0.2 mm track. The product tier needs this protection
  to work.
- **Fix (placement + router):** put D1 on the VBUS pour between J1 and Q1
  (u 60-67, v 2-10), with pour-width copper and its own GND via.

## Warnings (worst first)

### W1. Grounding between the class-D stage and the codec

On B.Cu the codec half and the amp half meet only at two places: the J1 neck
(v < 11.5) and around v > 61. Three things cause this:
- the E1 slit;
- the B.Cu PVDD pour (u 63.5-98.7, v 19.8-35), which blocks the direct path
  between U8 and J1;
- the B.Cu `/AMP_IN` diagonals and the `PVDD_SENSE` track around C58.

U8's supply return (4-5 A peak) reaches J1 through the ~8-11 mm channel at
u 52.7-63.5. The amp's input is single-ended:
- `AMP_IN` is referenced to U7's ground;
- the INN AC-grounds C61/C63 are referenced to U8's ground.

The channel's drop is in the input loop. Estimate: ~2-3 mOhm x 4.6 A = ~10 mV,
x 20 (26 dB) = ~0.2 V at the speaker. This gives distortion that follows the
supply current, and a possible low-frequency feedback path.

**Fix:** make the input pseudo-differential. Run each INN cap's ground end as a
trace from U7's ground, alongside its `AMP_IN`. Fixing E1 also helps. The
star at J1 is otherwise sound: amp current doesn't flow through the codec's
ground.

### W2. Amp inputs routed between the two bucks

`/AMP_IN_L` (v 34.3) and `/AMP_IN_R` (v 37.5) run 22 mm on F.Cu:
- ~1 mm from L5/C7 (5 V buck);
- 1.7 mm from L6 and 3 mm from U3 (3.3 V buck);
- over B.Cu that 7 perpendicular B.Cu tracks cut into strips (there is even a
  B.Cu GND island at u 41-46.7).

They then run 24 mm diagonally on B.Cu under C58. The TPS54302 and the
TPA3118 both switch at 400 kHz, so ripple coupled into the input beats into
the audio band.

**Fix:** route them over solid ground, away from both bucks.

### W3. The 5 A VBUS entry is necked, and the constraints hide it from the checks

- J1 A4/B9 feeds Q1's source pour through a 0.6 x 1.4 mm stub and a
  1.0 x 3.1 mm track. That path carries ~80 % of the current.
- B4/A9 goes through one 0.6/0.3 mm via and 14 mm of 1.2 mm B.Cu.
- `constraints.json` declares VBUS 0.4 A and PVDD 0.8 A. So
  check_current/check_pdn never tested the 4.5 A that the product's limiter
  allows (90 W at 20 V).

Downstream of Q1 the path is adequate:
- the drain pads go to a 21 mm2 F.Cu island, then 5 x 0.8/0.4 vias;
- a 3.5 x 16.8 mm B.Cu column (it abuts the pour, plus a 1.5 mm tie);
- the 535 mm2 B.Cu pour, then 2 x 5 vias to the PVCC F.Cu islands, with C58
  and C59 on them.

**Fix:** extend the VBUS pour to J1's pads, or make the link pour-width. Add
vias on the B4/A9 side. Declare the real current in the constraints.

### W4. Amp OUT runs are 0.31 mm over their whole length

The OUT nets are 0.31 mm for all 12-17 mm, not only at the pins. The README
sizes them for 1.0 A rms. A sine, or compressed/clipped DJ material at full
power, is 2.3-3.3 A rms: an estimated 60-140 C rise in a 0.31 mm 1 oz track.

**Fix:** neck down only at the pins, and widen to >= 1 mm from the bootstrap
caps to the inductors. There is room. The speaker runs (1.21 mm, ~15 C at
3.3 A) and the C68-C71 placement next to the inductors are fine.

### W5. ESP32 antenna clearance (`review-top-antenna.png`)

The keep-out (u 0-7.2, v 55.5-80.5) is clear on both layers and nothing
intrudes. But:
- ground pour continues along the left edge to 2.2 mm from both antenna ends;
- the plated M3 hole H4 (with its metal screw or standoff) is ~8 mm below the
  antenna.

Espressif wants the antenna to overhang the board, or ~15 mm of clearance.

**Fix:** notch the board, or widen the keep-out along the edge. Confirm the
enclosure wall there isn't metal.

### W6. U8 thermal-pad vias and paste

- 16 x 0.3 mm vias in the pad, with mask open on both sides (not tented or
  plugged);
- 100 % paste on the 3.81 x 3.71 mm pad.

Solder wicks down the vias: voids under the PowerPAD, and bumps on B.Cu.

**Fix:** tent the vias on B.Cu, or order plugged vias. Use windowpane paste at
50-70 %.

### W7. Front UI parts face the lid, not the front wall

- SW1-SW3 are TS-1187A top-actuated tact switches, 1.5 mm tall.
- D2 is a top-emitting 0603 LED.

The README records that the board sits ~31 mm under the lid, which is why U9
was changed to a front-facing part. These parts need ~30 mm plungers or a light
pipe, or right-angle parts that face the front wall. U9 itself is correct: the
lens dome (local +y) faces the front edge with 0.5 mm to spare, and the pinout
(OUT, GND, VCC) matches.

### W8. Connector silk

J4 has no L+ / L- / R+ / R- marking for whoever wires the binding posts. The
J3 reference text sits at J2's right peg.

### W9. J4 wire-entry side not verified

check_mating doesn't cover terminal blocks. The model and footprint don't show
which side the mouths are on. Check the Dorabo DB128V drawing: the mouths must
face the rear edge (v = 0). Otherwise the wires loop over C68-C71 and L1-L4.

### W10. Doc drift: `architecture/blocks.md` is stale

It still lists CH224Q, SMBJ24A, J4-J7 banana sockets, IRM-H638T on GPIO39, an
antenna at the "top edge" and 2 x 220 uF. The board and README differ on all of
these. The board itself delivers every block and interface in the README.

## Checked and fine

- **Rear-edge mating:**
  - J1, J2 and J3 faces are flush with v = 0.
  - Plug bodies clear each other: RCA centres 19.6 and 41.8, USB-C at 58.5,
    terminal pins from 75.2.
  - Q1 and the PD parts are behind J1, not in front of it.
- **Speaker path:** C68-C71 sit between the inductors and J4. The P/N runs of
  each channel are adjacent. PGND pins 22/25/28 tie to the F.Cu pour between
  the OUT pairs.
- **TPS54302 hot loop:** C5 (100 nF) and C3 sit at VIN/GND. It closes through
  two short vias, because the SW track separates C5's GND from U2's GND on
  F.Cu. That is acceptable.
- **Polarity:** C58 and C59 + pads are on PVDD, with the chamfer/+ visible. The
  D1 cathode is on VBUS. U9's pinout is correct. D2 follows its symbol
  (pad 1 = A). Whether pad 1 is the anode on the real KT-0603R isn't
  verified (open item).
- **Capacitor voltage ratings:** checked by hand, because check_ratings rated
  nothing (see below). All are adequate. 50 V on everything at VBUS/PVDD, and
  25 V on the 5 V and bootstrap caps.

## Triage of the verify warnings

- **check_diffpair (12, "open trunk" on BS_* / OUT_*):** these aren't diff
  pairs, and the constraints set `diff_pairs: []`. Justified waiver.
- **check_route_style (8, 19 needless jogs):** cosmetic, no arcs or off-45
  segments. Justified waiver. The visual style problems are the ones above:
  - a 50 mm USB run, caused by U6's placement;
  - 24 mm B.Cu diagonals for the amp inputs.
- **check_ratings (18, "unrated"):** this is a hole, not a pass. No
  datasheet extraction exists, so not one pin was checked. Hand checks are
  above. ICs (CH224A VBUS, TPS54302 VIN, TPA3118 PVCC at 20-21 V vs a 24.5 V
  max clamp) depend on the datasheets.
- **check_thermal waiver (U8):** I agree, on the condition already written in
  it (case temperature measured at bring-up). W6 (EP voids) makes that
  measurement more important.
- **`reports/checks/summary.json`:** it comes from a verify_all run without
  constraints, and 7 checks were skipped in it. The verify gate run had them
  all. Read the gate, not that summary.

## JLC assembly notes

- **Through-hole parts:** J2, J3, J4, U9 and J1's shell pins need JLC's
  through-hole assembly.
- **Edge clearance:** parts sit at or within 1 mm of the edge (J1, J2, J3, U9,
  the switches), so JLC will add edge rails.
- **Fiducials:** none on the board. JLC accepts that.
- **RCA jacks:** J2/J3 NPTH pegs were added by hand (HWDE-SNAGS #11). Check them
  against a real XDJK-0271-1650 before ordering.

## Waivers recommended

- check_diffpair x12 and check_route_style x8: justified as above.
- None of the review findings.
