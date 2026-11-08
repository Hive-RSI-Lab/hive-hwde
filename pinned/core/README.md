# core: the Plus/Ultra main board

The main board of hifi-receiver-2's **Plus** and **Ultra** tiers
([docs/product.md](../../docs/product.md)). It is a USB-C-powered
streaming amplifier on one 4-layer board, 154.4 x 113 mm (120 x 100 until the
banana sockets; see below):

- **Input:** 28 V USB PD EPR at up to 140 W.
- **Streaming:** ESP32 with Wi-Fi, Bluetooth and Ethernet.
- **Mains DAC:** Sabre ES9018K2M.
- **Subwoofers:** a separate sub DAC for two sub outputs.
- **Line in:** RCA, with a zero-latency "DJ direct" path.
- **Amp:** two TPA3220 class-D amps (the pad-down TPA3221): 2 x 77 W into 4 ohm peak, no heatsink. Plus plays L on zone A and R on zone B; Ultra drives two pairs.
- **Ultra population options:** a second amp (zone B), an optical input
  and the FFC to the `ultra-front` display board.

It was designed with the hive-hwde module (KiCad 10, JLCPCB) on 2026-10-28,
from schematic through routed layout to a JLC fab package. **Nothing has
been ordered. Ordering is the person's.**

![Top](reports/renders/core_top.png)

## Status (2026-10-29)

All hwde gates pass on the routed board after the first-spin review ECO, with hive-hwde at c23e27e:

| Gate | Result |
|---|---|
| `erc` | 0 errors, 0 warnings |
| `place` | pass |
| `drc_routed` | 0 errors, 0 warnings, parity clean |
| `verify` | pass, 1 waiver (`reports/verify-waivers.json`: the ESP32 module's 3V3 decoupling loop) |
| `dfm` | pass on the exported Gerbers |

The fab package is in `fab/`:

- **Gerbers:** `core_gerbers.zip`.
- **Plus build:** `BOM.csv` (88 lines, every one with an LCSC number), `CPL.csv` (298 placed parts, 14 of them on the bottom: see `fab/ORDERING.md`), `BOM-full.csv` and `prebuy.csv`, which lists the Extended parts.
- **Ultra build:** `fab/ultra/` holds `BOM.csv` and `CPL.csv` (302 placed parts) for the same Gerbers.

Board facts:

- **Size:** 4 layers, 154.4 x 113 mm overall, 1.6 mm. The right-hand 34.4 mm
  is a wing along the rear only (y < 150 in KiCad); the front right stays
  notched so the ESP32's antenna is still at the board edge.
- **Stackup:** 4 layers, 1.6 mm, 1 oz outer and 1 oz inner. JLC04161H-1080B
  comes only with 0.5 oz inner, so leave Specify Stackup at no requirement
  on the quote page: JLC then builds a 0.203 mm 7628 prepreg and a 1.03 mm
  core, and a named 1 oz layup adds $51.20. No net here is impedance
  controlled. Order it with
  epoxy filled and capped vias (`fab/ORDERING.md`), and inner copper 1 oz, not JLC's 0.5 oz default: the In2 VBUS/PVDD planes carry
  the full 5 A on their own (`power_trunk.py`).
- **Vias:** 0.7/0.3 mm, plus a few 0.45/0.2 mm GND taps.
- **Mounting:** 4 x M3 plated holes.
- **Holes:** JLC's 0.5 mm hole-to-hole spacing is held everywhere.

hwde's own estimate for 10 boards with assembly is $278, $27.84 each. That
excludes the parts, and it is a lower bound: the PCB line has the
oversize-area bug that hive-hwde issue #12 fixes. JLC's cart is the real quote.

## What's on it

The full block list, the ESP32 GPIO map, the I/O expander map, the FFC
pinout and the power tree are in
[architecture/blocks.md](architecture/blocks.md). In short:

| Block | Parts |
|---|---|
| Power in | GCT USB4085-GF-A through-hole USB-C (48 V/5 A, 240 W), CH224Q PD sink (asks for 28 V; ESP32 can read it or change it over I2C), SMBJ30A TVS, 10 mΩ shunt + INA186 current sense |
| PVDD switch | AO4485 P-FET, on only when firmware sets PVDD_EN **and** the CH224Q's PG says the contract is good; ~60 ms soft start (0.47 A, 9.8 W peak); gross-overvoltage cut-off |
| Rails | SY8303 VBUS to 5.1 V; SY8089 5 V to 3.3 V; LP5907 3.3 V analog; ferrite-fed +5VA and +3V3E |
| Processor | ESP32-WROVER-E-N8R8; CH340B USB-serial on the same USB-C (auto-reset, flashing from a laptop) |
| Ethernet | LAN8720A + HR911105A magjack, gated 50 MHz RMII clock |
| Mains DAC | ES9018K2M at 49.152 MHz MCLK, OPA1678 differential-to-single-ended stage |
| Sub DAC | PCM5102A to a stacked dual RCA (SUB1, SUB2) |
| Line in | stacked dual RCA, ES7243E ADC, TS5A23157 DAC/DJ-direct selector |
| Self-measurement | zone A's A_SPK_L+/R+ through 150k/10k, 470 pF, 1 uF onto a 1.65 V bias; TS5A23157 U20 picks RCA input (default) or the taps for the ADC; select from expander P17 through Q7 |
| Zone A amp | TPA3220 (24 dB, AD mode, 600 kHz; pad down, 15 vias into the GND planes), 10 µH / 10 A filters, 4 board-mounted 4 mm banana sockets |
| Zone B amp [Ultra] | the same, synchronised to zone A as a peripheral |
| Control | TCA9539 I/O expander (0x74, reset with the ESP32's EN); Plus front-edge knob, 2 buttons, IR receiver, status LED |
| Optical [Ultra] | PLR237 TOSLINK into the ES9018K2M's S/PDIF mux |
| Front board [Ultra] | 20-pin 0.5 mm FFC to `ultra-front` |

## Plus and Ultra from one board

| Parts | Plus | Ultra |
|---|---|---|
| zone B amp, its filters, bulk cap (U16, L5-L8, C3xx/R3xx) | fitted (plays R) | fitted |
| speaker sockets J20/J21 (zone A L), J26/J27 (zone B R) | fitted | fitted |
| speaker sockets J22/J23 (zone A R), J24/J25 (zone B L) | not fitted | fitted |
| TOSLINK U19, FB3, C126 | not fitted | fitted |
| FFC J3, R402/R403 | not fitted | fitted |
| knob SW1, buttons SW2/SW3, IR U18 (+R400/C400) | fitted | not fitted |

`fab/BOM.csv`/`CPL.csv` are the **Plus** build. `fab/ultra/BOM.csv` and
`CPL.csv` are the **Ultra** build of the same Gerbers.

## Banana sockets (2026-11-01, issues #117, #124)

The speaker outputs are eight 4 mm banana sockets in one row along the rear
edge, 11 mm apart. From the left in KiCad (from behind the box it reads
right to left): J20 A L+, J21 A L-, J22 A R+, J23 A R-, J24 B L+, J25 B L-,
J26 B R+, J27 B R-. Red is +. Plus plays L on zone A and R on zone B
(`HIFI_SPLIT`), so it fits J20/J21 and J26/J27; Ultra fits all eight.

A socket reaches 21.4 mm into the board and its nose stands 2.65 mm past the
edge, so the board grew. The rear edge moved 13 mm back, and a 34.4 mm wing
on the right took the RCA jacks J4/J5 and the TOSLINK U19. They moved with
D7/D8, FB3 and C126 and the copper between them as one block. The mounting
holes H1/H2 went to the new rear corners (H2 later moved south along the east edge to (185.45, 127.4), to make room for the jacks' side ears: `kicad/gen/rca_ear_eco.py`). How it was done, from
`boards/core`, KiCad's python as `python3.11`:

1. `kicad/gen/blocks_amp.py`: the sockets replace the terminals. Then
   `hwde lib_pull --lcsc C7437321 C7437322`, `build.py` (only amp_a/amp_b
   kept), `parts/*.json` by hand, and the netlist.
2. `python3.11 kicad/gen/banana_eco.py kicad/core.kicad_pcb`: rips the old
   terminal copper and the stitch vias under the row, and cuts the speaker
   pours back to y 130. It moves the rear block and the holes, and redraws
   the outline and the planes.
3. `hwde board_update --placements kicad/gen/banana_placements.json`.
4. `python3.11 kicad/gen/banana_route.py kicad/core.kicad_pcb`: one
   staircase pour per speaker net from its old pour to its socket's pin 2.
   L-/R+ run on F.Cu and L+/R- on B.Cu, four per layer, with no crossings
   and no vias. The pours are 2 mm wide (3.7 A rms). Where a later band
   crosses below a socket, its pin 1 joins pin 2 by a 1 mm In2 link (J23,
   J24, J25).
5. `python3.11 kicad/gen/banana_snub.py kicad/core.kicad_pcb`: zone A R- and
   zone B L+'s snubbers reached their net through the old terminal pin. Each
   gets a via and a B.Cu stub into its pour.
6. `miniroute.py` relays SUB1/SUB2_OUT, LIN_L/LIN_R and OPT_SPDIF to the
   moved block. Each line still goes into its ESD clamp (D7/D8) before the
   jack. Stale stubs are ripped with `rip_at.py`, and
   `kicad/gen/banana_silk.py` drops the sockets' silk, as on Lite.
   `kicad/gen/banana_45.py` puts the route ends back on the 45-degree grid
   (hwde check_route_style).

Folded in from review 5: R5-PA-1 (C4 220 nF/50 V C344195, C8 4.7 uF/25 V
C1779: Q1's soft start stays in its SOA), the EP paste windows off the vias
for U9/U10/U13 (`kicad/gen/ep_paste.py`, hwde check_ep_paste), and the
footprints' LCSC fields from parts.json (`kicad/gen/lcsc_fields.py`, run by
`fab.sh`, R5-DAL-8).

## First-spin review (review-2, 2026-10-29)

Six independent reviewers (power, audio, digital/RF, thermal, DFM, parts
pin-to-pad) read this board from its KiCad files and the vendor datasheets:
[`docs/review-2/`](../../docs/review-2/). What changed on core because of it:

| Finding | Was | Now |
|---|---|---|
| DIGITAL-1/2, PARTS-4 (blocker): the TCA9555's pull-ups left PHY_RST (the 50 MHz oscillator into GPIO0, a boot strap), PVDD_EN and the amp/DAC resets mid-rail at power-up, and its outputs held through ESP32 resets | TCA9555, R45 100k | TCA9539 (no pull-ups; RESET = ESP_EN), R45 10k |
| POWER-1 (blocker): the 5 A path ran through 0.25 mm tracks and 1-3 vias | SMD USB4105, few vias | through-hole USB4085 (power pins solid into the In2 VBUS_IN plane), copper patches with 8 vias at R1's input, 10 from R1's output to Q1's source, 8 at Q1's drain; 3-4 per TPA3221 PVDD pin group |
| sim_inrush hard_reset (hwde 261029-050227): Q1's ~1.1 s gate RC outlasted tSrcRecover, so a hard-reset source charged PVDD's 940 uF through a still-on Q1 (14 A, Q1 426 C) | Q2 released, R4 alone pulls the gate | R404 + D5 + Q3's second NPN pull Q1's gate to VBUS in ~2 ms when Q2 lets go (PG lost, PVDD_EN low, OV trip), in hardware: 0.7 A, Q1 120 C in the sim |
| THERMAL-1/DFM-3 (blocker): TPA3221 is pad-up with no heatsink | none | U15/U16 swapped to the pin-identical pad-down TPA3220. Its EP is soldered to GND with 15 x 0.3 mm vias into In1/B.Cu, so nothing is fitted by hand. Plus fits both amps, one channel each (L on zone A, R on zone B), and the firmware governor holds the 60 s average to 19 W out, about 2.0 W per chip, in the vented box (9.6 W/ch into 4 ohm). See `docs/review-2/heatsink-options.md` and `firmware-requirements.md` C2/C3 |
| DFM-1 (blocker): RCA J4/J5 had no holes for the jack's snap-in legs | none | Lite's footprint: 2.0 x 3.0 leg holes, signal pins as slots that fit either reading of the drawing |
| PARTS-7/DFM-12 (blocker, Ultra): TOSLINK T8BK on a T10BK footprint | no post holes | the T8BK's two 1.70 mm post holes (NPTH, `kicad/gen/u19_posts.py`; until 2026-10-30 they were only in the library copy, not on the board) |
| DIGITAL-19 (major): copper under the Wi-Fi antenna on all 4 layers | GND/+3V3 pours, two tracks | `ANT_KEEPOUT` rule area x 148.9-154.64, y 189.6-213.5 on every layer; I2S_DIN and RMII_TXD1 rerouted |
| POWER-2 (major): Q1 turn-on outside its SOA | R4/R5 100k, C8 1 uF | 470k/470k, 2.2 uF/50 V |
| POWER-6 (major): no hardware PVDD cut when the PD contract is lost | Q2 source on GND | Q2 source on CH224Q PG (+ R20 10k pull-up), as on Lite |
| AUDIO-1 (major): OPA1678 at 48 mV of input headroom | R73/75/81/83 1.5k | 1.3k |
| AUDIO-2/3 (major): planned tap switch couldn't pass a node below 0 V or be driven from 3.3 V | (new) | biased tap nodes, Q7 level shift, default = RCA |
| DFM-4 (major): IR receiver looking at the lid | IRM-H638T | front-facing IRM-3638T at the front edge |
| PARTS-3/AUDIO-10, DIGITAL-5, AUDIO-5 (minor) | 50 MHz Y1, C47 100 nF, 1 uF ADC caps | 49.152 MHz, 1 uF, 4.7 uF |
| Stock | CH340C | CH340B |

Ultra's zone-B speaker taps (the bring-up ask) were left out: no control line
is left to choose a zone, and the extra switch crowded the ADC; zone B is
watched through FAULT/OTW and ISENSE. The ES9018K2M's GPIO2 was the first
choice for the tap select, but its pin can't be fanned out between GPIO1 and
DATA, so the select comes from expander P17 and the front board's LCD_CS is
held asserted by R57 (DIGITAL-16; firmware cs = -1).

The changes went in as an ECO on the routed board: `board_update`, the
reviewers' fixes in `kicad/gen/blocks*.py`, then the power patches (then
`via_array.py`, now hwde's `zone_patch`),
`ant_keepout.py`, `miniroute.py`, hwde's `route_auto` (its KRT fallback; Freerouting
stalled), `stitch_vias`, `island_vias.py`/`gnd_via_drops.py` and hand tracks
(`add_track.py`/`add_via.py`). IR_IN keeps a 25 mm hop on In1 (the GND plane)
under the front UI area, where nothing fast crosses it.

## Design decisions and why

These are the decisions that differ from, or add to, docs/product.md.

- **Current sense:** INA186 (40 V), not INA180. The INA180 stops at 26 V
  common mode, under the 28 V rail. The sense taps run through
  10 Ω + 100 nF, but they leave the input planes rather than R1's own pads, so U3 reads
  about 5.6% high (`check_kelvin`, waived in `reports/verify-waivers.json`). It errs on the
  safe side; set the gain at bring-up (`docs/firmware.md`).
- **DAC clock:** 49.152 MHz. With its internal 1.2 V DVDD the
  ES9018K2M is limited to 50 MHz; a 50 MHz part met it only at 0 ppm, so
  review-2 moved to 49.152 MHz (synchronous 1024 x 48 kHz). It covers
  24/96 PCM and 96 kHz S/PDIF.
- **Ethernet clock:** a 50 MHz oscillator drives both the PHY and ESP32
  GPIO0. ESP32 clock-out on GPIO0 is experimental in ESP-IDF. The
  oscillator's enable is the PHY reset line, so GPIO0 boots clean.
- **PVDD switch hold-off:** a gate-source capacitor (1 µF) plus a 15 V
  clamp. Without it, the P-FET turned on while VBUS stepped from 5 to 28 V
  and charged the bulk caps at source slew. A schematic review caught it.
- **Overvoltage cut-off:** a 33 V zener (trips about 32-36 V). It is a
  backstop against gross faults only: the TPA3220's absolute maximum is
  37 V, and a zener cannot trip accurately at 30 V without tripping at the
  legitimate 29.4 V EPR maximum.
- **Snubbers:** on the speaker side of the output filter, as in TI's
  figure 9-1. On the switching node a 1 nF snubber would dissipate 0.47 W
  in each resistor.
- **Speaker outputs:** eight board-mounted 4 mm banana sockets, Amass
  24.243 (red C7437321, black C7437322, 24 A), the part Lite uses. JLC fits
  them, so nothing is wired by hand (issues #117, #124). They replaced 2-way
  5 mm screw terminals, which needed posts in the box wired to them. See
  "Banana sockets" above.
- **Amp polarity:** the TPA3220 inverts. The DAC stage undoes it (U11's
  inputs are swapped), so OUTx_P is the speaker's + (blocks_amp.py).
- **DJ switch logic:** the expander's 3.3 V output can't drive the 5 V
  switch's logic (it needs VIH 3.5 V), so Q6 inverts it up to 5 V.
- **I2S:** one bit and frame clock for all three converters. The sub DAC
  takes its own data pin (I2S1 as slave), because the ESP32 has no pins to
  spare.
- **FFC pinout:** the ultra-front board's map. It needs SPI, I2C and an
  interrupt line, and the front board carries its own expander.
  J3 and ultra-front's J1 are the same socket at rotation 0, pin N on the
  same signal at both ends: the cable is 20-pin 0.5 mm **type B**, pin N to
  pin N (`docs/review-2/resolutions.json` parts:64).

## Rebuild and re-check

From this directory (`boards/core`), with `H="sh $HIVE_MODULES/hive-hwde/bin/hwde"`:

1. **Schematic:**
   `eval "$($H env)"; PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib $HWDE_PY kicad/gen/build.py`.
   The design is data in `kicad/gen/blocks*.py`.
2. **ERC:** `$H gate --gate erc kicad/core.kicad_sch --workspace $PWD`
3. **Board setup and placement:** `sh kicad/gen/rebuild_board.sh`. This
   runs board_init, rules_gen, the hand anchors in `kicad/gen/anchors.py`,
   then `kicad/gen/satellites.py` and the place gate.
4. **Routing:** `sh kicad/gen/route.sh` (planes, stitching, Freerouting).
5. **Routed checks and fab:** the `drc_routed`, `verify` and `dfm` gates,
   `fab_export`, and `bom_cpl` run once with `parts/parts.json` (Plus) and
   once with `parts/parts-ultra.json` into `fab/ultra` (Ultra).

The full GND stitching pass (272 vias) went on the routed board afterwards:
[`docs/boards/core-stitch.md`](../../docs/boards/core-stitch.md).

### C43 to the LAN8720A's VDD1A

C43 is the 100 nF on U9 pin 19. It sat 3.8 mm from the pin, but its copper reached the pin only
through 20.3 mm of +3V3E, out through J2.4 and back, so it did little for that pin. ETH_TXN
lies between the two tracks, so `kicad/gen/c43_eco.py` crosses under it on B.Cu with two 0.6/0.3 mm
vias and 1.8 mm of track. The walk is now 6.55 mm, inside hwde's 10 mm limit.

### The two U10 decoupling warnings, left as they are

hwde's `check_decoupling` warns twice on U10 (ES9018K2M). Both are warnings, not errors, and both are left on purpose (2026-11-04):

- **C53 to pin 16 (AVCC_R), 13.7 mm of +3V3A.** Pins 15 (AVCC_L), 16 (AVCC_R) and 17 (VCCA) are joined at the package, 0.5 mm apart.
  So pin 16 is also served by C50 (1 uF, 5.7 mm), C51 (100 nF, 6.1 mm), C54 (100 nF, 7.2 mm) and C52 (1 uF, 7.7 mm), all inside the 10 mm limit.
  C53 is a fifth cap on that group. Its long walk costs nothing the others don't already cover. Moving it would mean new copper beside the DAC outputs for no gain.
- **C55 to pin 18 (DVCC, +3V3), loop about 11.3 nH against a 10 nH limit.** That is 8.8 mm of rail, 3.0 mm of ground and three vias. DVCC is the digital supply that feeds the chip's own 1.2 V regulator, whose output pin 21 (DVDD) has C56 (4.7 uF).
  13 % over the limit moves the cap's self-resonance from about 5.0 to 4.7 MHz. That is not worth a copper change on a board about to be ordered.
  If bring-up shows DAC noise tied to the digital supply, the fix is a 100 nF 0402 placed beside pin 18 on the next spin.

Walks are from hwde's `copperpath` on the board as merged at 5b7f956.

### How the routing was finished

Freerouting (`route_auto`) left about 40 connections open on this dense
board, and its KRT finish pass either laid power nets below their width rule
or broke clearance. So the board was finished with small scripted copper
edits in `kicad/gen/`, each followed by the `drc_routed` gate. They are not
fully reproducible from scratch, because the order of edits mattered. The
routed `kicad/core.kicad_pcb` is the source of truth for copper.

- **Power pours and fixed copper:** `power_zones.py` and `pre_route.py` lay
  the amp output fan-in pours, their keepouts, and the speaker and PVDD via
  arrays before routing.
- **`miniroute.py`:** a 0.1 mm-grid maze router used for the last
  connections, with rip-up helpers `rip.py`, `ripnet.py`, `rip_near.py` and
  `turn.py`.
- **GND repairs:** `gnd_island_vias.py`, `gnd_pad_taps.py`,
  `gnd_via_drops.py` and `unbox_gnd.py` tie isolated GND pour fragments
  down to the In1 plane.
- **`bst_links.py`:** routes the bootstrap caps to their amp pins.
- **`fix_j1.py`:** moves USB-C J1 in from the edge and shortens its pads
  next to the pegs.
- **`power_trunk.py`:** after routing, lets the In2 VBUS_IN, VBUS and PVDD
  planes carry the real 5 A on their own. It clears the GND stitch grid out
  of them, reshapes their zones, moves three vias that walled them in, and
  gives each PVDD finger five plane vias and +5V's trunk layer changes two.
  Its docstring has the run order.
- **Silk:** hwde's `silk_fix` (then `silk_clean.py`) hides reference texts
  and deletes footprint silk that runs over pads or the edge. The assembly
  drawing keeps every reference.

**Current ratings:** `constraints.json` budgets the real currents: VBUS_IN,
VBUS and PVDD at 5 A (the 28 V, 5 A PD contract), +5V at 1.1 A on its trunk and 0.4 A on its branches. hwde's
check_current passes them with each layer's pour judged on its own. The width
rules for the plane-fed rails (VBUS_IN, VBUS, PVDD, +3V3) and the amp OUT/SPK
nets are sized for the tracks that remain, which are taps and bootstrap
links. The main currents ride on the In2 planes and the solid F.Cu/B.Cu
pours, which the width rules don't see (`constraints.json` `power_notes`).

**Hand checks, 2026-11-04** (hwde `check_esd`, `check_body_clash`, the 3D
clash and `check_model_paths`/`check_model_fit`, on main 5b7f956). None of
these is in the CI gates. 0 errors on every check. The warnings:

- **ESD:** 3 warnings, all accepted in
  [`docs/boards/core-esd.md`](../../docs/boards/core-esd.md). They are the
  two far-clamp warnings on USB D+/D-, and ISNS_P beside the D+ stretch.
- **J21/J22, J23/J24, J25/J26 bodies 0.20 mm apart** (`body_tight`, which
  wants 0.3 mm). These are neighbouring banana sockets at 11 mm pitch, whose
  ribbed bodies nearly fill it. Their collars sit in the
  enclosure's 10.2 mm holes, and the holes set each socket's axis. Accepted.
  At assembly, check that no pair leans into contact.
- **U18 overhangs the front edge by 1.15 mm, and its body is 1.65 mm outside
  its courtyard.** U18 is the IRM-3638T IR receiver. It is meant to stand at
  the edge with its lens facing out through the front wall. Accepted.
- **U7 overhangs the board edge by 1.56 mm.** That is the WROVER's antenna
  end, in the front-right notch, which is there to keep the antenna at the
  edge (the `ANT_KEEPOUT` area). Accepted.
- **U3 (INA186) has no 3D model in the check.** Its footprint points to
  `aiee.3dshapes/SC-70-6_L2.0-W1.3-H1.0-P0.65.wrl`, and that file doesn't
  exist. The library has `SC-70-6_L2.1-W1.3-H1.0-LS2.1-P0.65.wrl`. This
  affects 3D only: the Gerbers, BOM and CPL don't change. It is 1 mm tall,
  so nothing can stand on it. Fix the path at the next board edit.
- The core + ultra-front stack fit (`enclosure/ultra.py`) passes again on the
  current core pcb.

## Testing a built board

1. **Power with no amp:** plug a laptop USB-C port or a 5 V supply into J1.
   - The board boots with PVDD off.
   - The ESP32 appears as a CH340 serial port, and it can be flashed
     through the CH340.
2. **Rails:** check +5V (5.1 V), +3V3, +3V3A and the 2.55 V VREF.
3. **PD contract on a 140 W EPR charger:** use a 240 W (5 A) cable. Read
   the contract from the CH224Q over I2C (0x22/0x23); VBUS should be
   28 V.
4. **Amp bring-up:** set PVDD_EN (expander P03) and watch PVDD ramp in
   about 60 ms. Then release AMP_A_RST (P00) with DAC_EN (P04) set.
5. **Speaker polarity:** play a 1 kHz tone at low level into a 4 Ω dummy
   load and check the output polarity.
6. **Thermals:** play 1/8 power into 8 ohm for 10 min and check for no OTW. Then do the same into 4 ohm with the governor on, and log the board temperature next to U15.
7. **Ethernet:** set PHY_RST (P05). This starts the 50 MHz clock, and the
   link should come up.

## Open items

Every fact this board relies on, and whether it's verified, is in
[`docs/known-unknowns.md`](../../docs/known-unknowns.md); run
`python3 tools/known_unknowns.py --check` before ordering. The ones that are
this board's to settle:

- **Amp cooling (TPA3220, no heatsink):** the board's GND planes are the heatsink, at about 26 C/W (23.5 on TI's JEDEC board). That holds 1/8 power into 8 ohm (Tj ≈ 84 C in a 45 C box). Plus splits the pair across both chips (L on zone A, R on zone B), and the firmware governor caps the 60 s average at 19 W out (9.6 W/ch into 4 ohm, 14.8 W/ch into 6 ohm) in the vented box (air ~58 C at 30 C room, hwde check_thermal); Ultra 9 W / 17 W. Bring-up measures the real figure (step 6) and sets the governor from it.
- **TPA3220 stock:** LCSC has none. Buy it into the JLC parts inventory (Mouser/DigiKey through JLC global sourcing) before the order (`fab/prebuy.csv`).
- **5 V buck bootstrap loop:** C12 sits below L9, so its BS and LX links
  are long routed tracks. Watch U4's switch node for ringing at bring-up.
- **Hidden reference texts:** about 210 hidden to clear silk overlaps on
  the dense passives. The assembly drawing and the CPL carry every designator.
- **HR911105A centre taps:** taken as pins 4/5, the common practice; the
  datasheet text could not be read.
- **Extended parts:** most ICs are Extended, and JLC may hold them as idle
  stock (`fab/prebuy.csv`).
- **Firmware duties:** [`docs/review-2/firmware-requirements.md`](../../docs/review-2/firmware-requirements.md) (core section).
