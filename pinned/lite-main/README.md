# lite-main: the Lite tier of hifi-receiver-2

The Lite tier's one board, as `docs/product.md` defines it. You plug a USB-C PD
laptop charger of 65 W or more and a pair of speakers into it (Lite asks for
20 V and uses at most what that contract allows, 60–65 W with an ordinary
cable: a bigger charger adds nothing; see issue #136). It streams over Wi-Fi
(Spotify Connect, AirPlay, Lyrion) and Bluetooth, takes a DJ deck on RCA, and
drives two subwoofers from RCA sub outs. A web app on your phone, three buttons
or any IR remote control it.

It was designed with the hive-hwde module (KiCad 10), from schematic to JLC fab
package, on 2026-10-28. **Nothing has been ordered.** Ordering and paying are
the person's. The workspace is named after the board, `boards/lite-main/` (the
brief said `boards/lite/`): that is the path `tools/hwcheck.py` scores, from
product.md's Boards row.

![top](reports/renders/review-top.png)

## What it is

- **Board:** 2 layers, 1.6 mm FR-4, 1 oz, OSP, 118 x 90 mm, four M3 holes
  (H1-H3 plated, H4 bare), 2 mm corners. 154 parts, all on the top side. 147 are SMT; 7 are
  through-hole (J2, J3, J4-J7 and U9). The 4 mounting holes are not BOM parts.
  JLC assembly with LCSC part numbers.
- **Rear edge, left to right:**
  - **J2:** RCA line in (FLX4). Top jack white = L, bottom red = R. ESD: D3/D4.
  - **J3:** RCA sub out L/R. ESD: D5/D6.
  - **J1:** USB-C.
  - **J4-J7:** the speaker outputs, four 4 mm banana sockets (Amass 24.243,
    right-angle PCB mount, 24 A, red C7437321 / black C7437322; JLC fits
    them): J4 L+, J5 L-, J6 R+, J7 R-, at 11 mm pitch. Their round collars
    (9.40 mm across, 2.60 mm long, bottom 3.20 mm over the board) reach 2.65 mm
    past the rear edge into the enclosure's 10.2 mm round holes, bore axis
    8.1 mm over the board. Body 10.10 mm wide, 10.70 mm across the rib; pins
    15.5 +/-0.3 mm (Multicomp's sheet for the same part numbers; Amass's says 15.7). Nothing is wired by hand. Single banana plugs;
    the 11 mm pitch is too close for a dual (19 mm) plug.
- **Front edge:** SW1 vol-, SW2 vol+, SW3 play/source (hold = BT pairing),
  the IR receiver U9 (an IRM-3638T standing at the edge with its lens facing
  forward through the front wall) and a red status LED D2.
- **ESP32 antenna:** at the left edge. A copper keep-out on both layers runs
  from 15 mm above it to the bottom edge, and no copper is left in the check's
  15 mm bands either side. H4, 8 mm below the antenna, is a bare unplated hole
  for that reason, so a metal screw there sits near the antenna with no ground
  bond (`docs/boards/lite-preorder.md`).

| Block | Parts | Notes |
|---|---|---|
| USB-C + PD | J1 HRO TYPE-C-31-M-12 (20 V/5 A), U1 CH224A, D1 SMBJ22A; ESD: D7/D8 PESDNC2FD5VB (D+/D-; CC exempt, docs/boards/lite-esd.md) | asks for 20 V (Rset 120k); I2C 0x22 on the codec's bus, so firmware reads the protocol status (0x09) and the PDO's max current (0x50). The CH224A has no SRCCAP registers (CH224Q only). |
| PVDD switch | Q1 AO4485, Q4 MMBT3904 | on only when GPIO18 is high **and** the PD contract is good (Q4's emitter is CH224A PG); C19 holds the gate shut on VBUS steps; soft start ~28 ms, 0.41 A inrush; R16 bleeder |
| 5 V | U2 TPS54302, L5 10 uH | TI design-example values |
| 3.3 V | U3 SY8089 (digital), U4 LP5907 (codec analog) | |
| MCU | U5 ESP32-WROVER-E-N8R8, U6 CH340B, Q2/Q3 | console + flashing over the same USB-C; auto-program on DTR/RTS |
| Codec | U7 ES8388 | pinned like the ESP32-A1S AudioKit (I2S MCLK 0, BCLK 27, LRCK 25, DOUT 26, DIN 35; I2C 32/33): squeezelite-esp32's board profile applies |
| Amp | U8 TPA3118D2, L1-L4 10 uH 8 A, C68-C71 1 uF | BTL, 26 dB, BD mode, no power limit; 2 x 38.7 W into 4 ohm at 20 V, 1 % THD (review-2) |
| Self-measurement | R66-R69, C81-C84 | SPK_L_P / SPK_R_P through 100k/10k, 470 pF, 1 uF into the codec's LIN2/RIN2: the board records its own speaker outputs (ratio 0.0625 with the ADC's input) |
| Bulk | C58 470 uF polymer, C59 220 uF | |

ESP32 pin map:

| GPIO | Signal |
|---|---|
| 0 | MCLK |
| 4 | PD power-good |
| 13 | vol+ |
| 14 | IR |
| 18 | PVDD enable |
| 19 | play |
| 21 | amp SDZ (through 100k) |
| 22 | LED |
| 23 | amp fault |
| 34 | VBUS/11 |
| 39 | PVDD/11 (firmware confirms the switch closed) |
| 36 | vol- |
| 1/3 | UART0, also on spare header J8 |
| 5, 15 | unused by the audio side, on spare header J8 (boot straps) |

The full table is in [`architecture/blocks.md`](architecture/blocks.md). Spare header J8:
[`docs/boards/lite-header.md`](../../docs/boards/lite-header.md).

## First-spin review (review-2, 2026-10-29)

Six independent reviewers (power, audio, digital/RF, thermal, DFM, parts
pin-to-pad) read this board from its KiCad files and the vendor datasheets:
[`docs/review-2/`](../../docs/review-2/). No pinout errors. What changed on
this board because of it:

| Finding | Was | Now |
|---|---|---|
| AUDIO-4 / DIGITAL-14 (major): the amp could stay in shutdown, since SDZ's up-to-50 uA input current through 100k left it under 2 V | R54 100k | R54 10k (SDZ >= 2.7 V) + C26 47 nF slew limit |
| POWER-3 (major): Q1 turned itself on at every VBUS step with the firmware off (C2 Miller coupling) | R6 47k | R6 220k + C19 4.7 uF gate-source (Vsg <= 6.6 V, so 16 V 0603) |
| POWER-4 (major): firmware turn-on at the AO4485 SOA edge, 2.5 A inrush on a 3 A contract | 6 ms ramp | 28 ms ramp, 0.41 A, 5.8 W peak (simulated, >3x SOA margin) |
| AUDIO-7: 1.5-2 dB line-in headroom at the FLX4's 2.1 Vrms | R30/R33 10k | 15k: -4.2 dBFS |
| POWER-10: 5 V buck crossover above TI's 40 kHz | C9 100 pF, 2 x 22 uF | C9 47 pF, + C25 22 uF |
| DFM-2: RCA pin rows ambiguous on the maker's drawing | round pin holes, 1.7 x 2.4 leg holes | pins 1/3 are 1.6 x 2.4 plated slots covering both readings; legs 2.0 x 3.0 as drawn |
| Stock: USB4105 0 free at JLC; CH340C not buyable | USB4105, CH340C | HRO TYPE-C-31-M-12 (20 V, Lite asks 20 V), CH340B |

THERMAL-3 (continuous full power trips the latched OTP) and AUDIO-6
(0 dBFS overdrives the amp by 4.1 dB) are firmware's:
[`docs/review-2/firmware-requirements.md`](../../docs/review-2/firmware-requirements.md).
Every fact the board relies on is in [`docs/known-unknowns.md`](../../docs/known-unknowns.md)
(`python3 tools/known_unknowns.py --check` before ordering).

The changes went in as an ECO on the routed board, keeping all other
copper: `kicad/gen/eco_swap.py` (J1/U6 swaps; J1's VBUS/GND pads made plain
0.6 mm rectangles and its signal pads trimmed to the USB4105's inner edge),
`eco_rca.py`, then `board_update` with `find_spots.py` placements, and the new
nets routed with `miniroute.py`/`add_track.py`/`add_via.py`. Freerouting timed
out twice on the routed board under the machine's load.

## Design decisions, and why

Each one is also in `state.json` (`decisions`). The schematic review and what
was done about each finding is in
[`reports/review-schematic-response.md`](reports/review-schematic-response.md).

- **CH224A, not the CH224Q product.md named.** Same die and registers. The
  CH224Q's 2 x 2 mm, 0.4 mm-pitch DFN puts VBUS between CC1 and CFG1, right
  under the USB-C, and it couldn't be fanned out on 2 layers. The ESSOP-10 is
  1.0 mm pitch, same price, 9.5k in stock. The CH224K has 1 in stock.
- **The CC lines are crossed on purpose.** The CH224 treats CC1/CC2 alike, so
  J1's B5 goes to U1 pin 7 and A5 to pin 6. That way neither crosses the
  other or the D+/D- escape.
- **Banana sockets on the board, not binding posts wired to a terminal**
  (task 261030-140102: the person orders, plugs in and does nothing else).
  Amass 24.243 is the only board-mount 4 mm socket JLC stocks (GC4010-F is a
  panel bullet connector). Four at 10.1 mm wide didn't fit the 100 mm edge
  beside USB-C and the RCA jacks, so the board grew to 118 mm east; H2 moved
  under the sockets to (114.45, 27). The L row moved 2.2 mm toward U8, and
  the 1 uF output caps (C68-C71) and R68 sit between the sockets' backs and
  the inductors. `kicad/gen/banana_route.py` is the speaker copper: L+ and R+
  on F.Cu, L- and R- through seven vias each (3.5 A) on B.Cu under the
  sockets. Both pins of each socket are its contact and carry the current.
- **Amp channels swapped at the chip.** The TPA3118's "right" channel drives
  the left speaker, so its output row runs L+ L- R+ R- straight into the
  terminal with no crossing. Nets are named by speaker channel.
- **PVDD needs firmware and the PD contract.** GPIO18 AND CH224A PG (hardware
  AND through Q4's emitter). A 5 V laptop port can never power the amp, and
  the firmware can drop it before a PD renegotiation.
- **5 V buck is a TPS54302**, not the SY8303 in the cost estimate: TI's
  datasheet design example gives every value. 5.18 V (100k/13k, Basic parts).
- **SMBJ22A TVS** (BORN C151920; standoff 22 V, breakdown 24.4–26.9 V,
  clamp 35.5 V at 16.9 A). PD allows 20 V +5 %, so the rail can sit at 21 V:
  the SMBJ20A first fitted had a 20 V standoff, under that, and hwde's USB-C
  PD sink rule asks for working >= 22 V. A 22 V part is the lowest standoff
  that holds 21 V; it still breaks down below the 30 V abs-max of the
  TPS54302 and TPA3118. Same SMB footprint, pin 1 = cathode. No TVS can
  hold a 21 V bus under 30 V at its full rated surge; that residual risk is
  review-2 POWER-9 (PD attaches at 5 V, so real surge energy is small).
- **TPA3118 gain 26 dB.** The ES8388's full scale (~0.9 Vrms) x 20 clips at
  20 V into 4 ohm 3.7 dB before 0 dBFS, so firmware caps the codec volume.
  20 dB would leave only 20 W.
- **Power copper is pours, not tracks.** VBUS (J1 to Q1) and PVDD carry up
  to 5 A, joined by 16 vias. PVDD runs from Q1's drain down a B.Cu column
  under the filter caps, so the speaker lines cross it on F.Cu. It continues
  as a B.Cu pour under the inductors, stopping 5 mm short of the amp so the
  ground under it is unbroken, then F.Cu islands at both PVCC groups. The pours
  are in `kicad/gen/power.py`. Tracks only feed the small taps.
- **Amp OUT runs (pin to inductor) are 0.31 mm minimum** at the HTSSOP pins
  (0.65 mm pitch, 0.34 mm pins), sized for 1.0 A rms / 30 C. That is music at
  full power with a 9 dB crest factor. They are under 15 mm long. The
  inductor-to-socket runs are 1.25 mm (3.5 A, 30 C).
- **USB is not impedance-controlled.** Full speed, 12 Mb/s; a 90 ohm pair on
  1.6 mm 2-layer would be 1.1 mm wide.
- **USB ESD:** D7/D8 (PESDNC2FD5VB) are fitted on D+/D-; the CC pins have no
  clamp (docs/boards/lite-esd.md).
- **Board 118 x 90 mm**, not the ~100 x 80 product.md estimated: 80 left no
  room for the PVDD switch between USB-C and the amp, and the banana sockets
  took 18 mm more.
- **Red LED (Basic part) at 1.3 mm.** The white one the first schematic had ran
  at 0.2-0.6 mA, too dim.
- **IR on GPIO14**, not 39: GPIO36/39 glitch with ADC/Wi-Fi (errata 3.11).
  vol- stays on 36; a polled, debounced button tolerates the glitch. GPIO39
  reads PVDD/11 instead (firmware worker's ask), which is sampled, not edge-triggered.
- **Front-facing IR receiver.** The enclosure worker found the top-looking
  IRM-H638T would sit 31 mm under the lid, outside a remote's 45 degree cone.
  It is now a through-hole IRM-3638T whose lens faces through the front wall,
  with Everlight's 100 ohm / 4.7 uF supply filter.

## The checks it passed

The pre-order round of 2026-11-01 (hwde 0600487: vias off pads, I2S respaced, VBUS stubs, box holes) is in `docs/boards/lite-preorder.md`.

| Gate | Result |
|---|---|
| ERC (`erc`) | 0 errors, 0 warnings (every pulled pin is typed passive, so the schematic review checked pins by hand) |
| Placement (`place`) | pass, 0 errors |
| Routed DRC (`drc_routed`) | 0 errors, 0 warnings, 0 unconnected |
| Verify suite (`verify`) | pass, with one signed waiver (U8 thermal, below) and 44 warnings, now shown by hwde e225475: 25 route-style (off-45 jogs in Freerouting and maze-router copper; DRC-clean, cosmetic), 18 part ratings with no datasheet extraction (each part's voltage and current margin is in `docs/review-2/` instead), 1 decoupler 5.3 mm from U8 pin 31 (C57, 1 nF HF cap, limit 5.0; 0.3 mm over, left) |
| JLC DFM on the exported Gerbers (`dfm`) | pass, 0 errors. 3 warnings: thin silk strokes inside stock/EasyEDA footprints, and J2/J3's LCSC model pad centre 0.79 mm off. The LCSC model itself has pad 3 0.8 mm off pad 1 in Y, where the real jack and our footprint have them level. Since 2026-11-04 the CPL places J2/J3 at the footprint origin (hwde 7f65e45; `cpl_verify` 0.0 mm). They are through-hole, so their holes locate them; check them in JLC's preview. Every CPL rotation was checked against the LCSC model. |

All gates last passed on hive-hwde c23e27e (2026-10-29), after the review-2 ECO.

**The one waiver: TPA3118 thermal**
([`reports/verify-waivers.json`](reports/verify-waivers.json)):

- **The two estimates:** hwde's copper-area model gives the amp ~47 C/W on
  this board, which is ~94 C rise at 2 W (1/8 power into 4 ohm). TI's
  datasheet gives 22 C/W for this package on its 2-layer EVM, ~44 C rise.
- **What the board does about it:** both sides are ground-poured around the
  chip, with 16 solid EP vias and unbroken B.Cu ground under it.
- **Backstops:** the chip shuts itself down on over-temperature and reports it
  on FAULTZ, which the firmware reads.
- **Bring-up must measure it** at 1/8 power into 4 ohm. Above 100 C, stick a
  heatsink on the package top or lower the governor's limit.

**Reviews:**

- **Schematic:** a fresh-context review found 1 error and 14 warnings,
  answered in
  [`reports/review-schematic-response.md`](reports/review-schematic-response.md).
- **Routed board:** [`reports/review-board.md`](reports/review-board.md).

**Hand checks, 2026-11-04** (hwde `check_esd`, `check_body_clash`, the 3D
clash and `check_model_paths`/`check_model_fit`, on main 5b7f956). None of
these is in the CI gates. 0 errors on every check. The warnings:

- **ESD:** 10 warnings, all accepted in
  [`docs/boards/lite-esd.md`](../../docs/boards/lite-esd.md). Two are USB_DM
  warnings: D8's 2 vias and its 13.8 mm of track. The other 8 are corridor
  warnings.
- **J5/J6 bodies 0.20 mm apart** (`body_tight`). These are neighbouring
  banana sockets at 11 mm pitch, whose ribbed bodies nearly fill it. The
  enclosure's holes set each socket's axis. Accepted. At
  assembly, check that they don't lean into contact.
- **U9 overhangs the front edge by 1.13 mm, and its body is 1.63 mm outside
  its courtyard.** U9 is the IR receiver, standing at the edge with its lens
  facing out through the front wall, as designed. Accepted.
- **L1-L4 (XR0850) have no 3D model.** The body-clash and 3D-clash checks
  can't judge them. `enclosure/heights.json` carries their 5.2 mm height, so
  the enclosure fit still counts them.

## Cost

**$14.54 per board at 100**: parts $11.72, JLC assembly $2.16, PCB $0.66.
This is the project score, `python3 tools/hwcheck.py score`, priced from the
real `fab/BOM.csv`, the board's pad counts and its outline.

That is the whole unit's electronics: the speaker sockets are on the board,
so nothing is bought or wired off it. `python3 tools/bom_estimate.py` gives
$14.79 at 100 and $23.41 at 10.

- **Parts:** 48 distinct; 29 JLC Basic, 19 Extended. Extended parts cost $3
  each per order and may need pre-buying: see `fab/prebuy.csv`.
- **Quote:** none yet. JLC's quote is the real price, and ordering and paying
  are the person's.
- **Order remarks** (review-4 R4-BAN-5): the banana sockets J4-J7 have two
  identical pads and their key lies past the board edge, so the remark says
  "J4-J7: plug face outward, past the rear edge (2.65 mm overhang is by
  design)". Before the order, if it causes no delay, measure one Amass 24.243
  sample. Body width over about 11.2 mm stops the board: the sockets no longer
  fit side by side at 11 mm pitch. Worst case on Multicomp's tolerance table
  (+/-0.4 mm, so 11.1 mm wide) overlaps 3 x 0.1 mm across Lite's row, inside
  the 0.7 mm of pin play in the holes (Ultra's eight sockets: 7 x 0.1 mm of the
  same 0.7 mm). The collar (9.40 mm against the box's 10.2 mm holes) only
  affects the box. Also try a 15 mm and a 19 mm banana plug's contact.

## How to rebuild and re-check it

You need a Hive checkout with the hive-hwde module (`$HIVE_MODULES/hive-hwde`),
its KiCad 10 and Freerouting.

```sh
cd boards/lite-main
sh build.sh sch      # footprint repairs, schematic from kicad/gen/root.py, ERC gate, netlist, parts.json
sh build.sh board    # board_init, rules_gen, placement (kicad/gen/place.py), keep-outs, pours, power vias
sh build.sh route    # Freerouting (route_auto), stitch vias, drop vias under the hole-to-hole floor
```

`sh build.sh board` re-creates the board from scratch, so it throws the
routing away. Freerouting doesn't route the same way twice. The committed
`kicad/lite-main.kicad_pcb` is the run that passed, with the finishing steps
below applied. To re-check the committed board without rebuilding it:

```sh
H="sh $HIVE_MODULES/hive-hwde/bin/hwde"
for g in place drc_routed verify dfm; do $H gate --gate $g kicad/lite-main.kicad_pcb --workspace .; done
$H gate --gate erc kicad/lite-main.kicad_sch --workspace .
```

After routing, the committed board also went through:

```sh
sh build.sh finish   # plane_repair, 45-degree snap, silk_place + kicad/gen/silk.py, outline snap
sh build.sh stitch   # full GND stitching at the auto pitch (4.28 mm), 273 vias
sh build.sh fab      # fab_export, bom_cpl (LCSC models cached in parts/easyeda/), dfm gate
```

Two fixes, between `route` and `finish`, only where drc_routed asked for them:

- `kicad/gen/nudge_vias.py` + `kicad/gen/refill.py` for Freerouting's vias
  under the 0.5 mm hole-to-hole floor.
- The route run was repeated until only those were left.

Later, for hive-hwde 9ad982e's stricter checks (review round 4),
`kicad/gen/ep_paste.py` (KiCad's python) gives U1's and U7's exposed pads
window-pane paste apertures off their GND vias (check_ep_paste). It also
takes U8's 16 thermal-via pads to 0.70 mm on their 0.30 mm drill, for JLC's
0.18 mm annular ring (dfm). The same edits are in `lib/aiee.pretty`.

The fab package in `fab/`:

- `lite-main_gerbers.zip`: Gerbers and drill.
- `BOM.csv` and `CPL.csv`: the JLC upload pair.
- `BOM-full.csv`
- `prebuy.csv`

## How to test a built board

1. **Visual:** check D1 (TVS, cathode bar to VBUS), C58/C59 (+ up), D2 and
   the RCA jacks seated on their pegs.
2. **Power, from a 5 V USB port** (a laptop) with nothing connected:
   - +5V (C7) reads 4.7–5 V, +3V3 (C12) 3.3 V, +3V3A (C16) 3.3 V.
   - PVDD (C58+) reads 0 V: the amp switch must stay off on 5 V.
   - The CH340B enumerates as a serial port, and the ESP32 boot log appears
     at 115200.
3. **Flash:** flash the firmware over the same port; auto-program needs no
   buttons.
4. **On a 65–100 W PD charger:**
   - VBUS reads 20 V.
   - The CH224A at I2C 0x22 reports PD active (0x09) and the PDO current
     (0x50).
   - Firmware sets GPIO18. PVDD rises in ~28-36 ms (R6 220k, C19 4.7 uF) and GPIO39 reads ~1.8 V.
   - Watch the charger doesn't drop out (inrush ~0.5 A).
5. **Audio:**
   - Line in, both routes: the analog bypass to the speakers, and the ADC.
   - Sub outs: about 2 Vrms max with no load.
   - The amp, into 4 ohm dummy loads: ~43 W/ch at clipping.
   - **The TPA3118's case temperature at 1/8 power** (the waiver above).
   - FAULTZ stays high.
6. **Controls:** the IR receiver with any NEC remote, the three buttons and
   the LED.

The bench scripts (plan item 9) will automate this.

## Open items

1. **U8 temperature at 1/8 power:** measure at bring-up (the waiver).
2. **RCA jack depth:** check J2/J3 against a real XDJK-0271-1650.
   - The board edge sits at the footprint silk's face line.
   - The EasyEDA 3D model sits 13.5 mm low and 6 mm inboard (found by the
     enclosure worker); only renders are affected.
3. **Banana socket collar:** Amass's sheet gives 24 mm overall, 15.7 mm pin
   pitch and a Ø4 bore, but no widths. The numbers here come from Multicomp's
   sheet for the same part numbers
   (https://electrokit.com/upload/quick/5d/25/3fca_41022868-tds.pdf), assumed to
   match what JLC fits: body 10.10 mm, 10.70 mm across the rib, collar 9.40 x
   2.60 mm long, its bottom 3.20 mm over the board, pins 15.5 +/-0.3 mm. The
   rear wall's hole is 10.2 mm round at axis 8.1 mm; check a real socket against
   it before printing.
4. **CH224A I2C pull-ups:** if bring-up measures CFG2/CFG3 idling above 3.6 V,
   depopulate R2/R3. The I2C readout is then lost, and VBUS sense still works.
5. **USB ESD:** D7/D8 are fitted on D+/D-; CC1/CC2 are left unclamped
   (docs/boards/lite-esd.md).
6. **cSnkBulk:** VBUS has ~15 uF of ceramic at 5 V, above USB-C's 10 uF
   attach limit. PD sources tolerate it in practice; check at bring-up with
   two or three chargers.
