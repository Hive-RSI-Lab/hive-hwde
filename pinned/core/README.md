# core: the Plus/Ultra main board

The main board of hifi-receiver-2's **Plus** and **Ultra** tiers
([docs/product.md](../../docs/product.md)). It is a USB-C-powered
streaming amplifier on one 4-layer 120 x 100 mm board:

- **Input:** 28 V USB PD EPR at up to 140 W.
- **Streaming:** ESP32 with Wi-Fi, Bluetooth and Ethernet.
- **Mains DAC:** Sabre ES9018K2M.
- **Subwoofers:** a separate sub DAC for two sub outputs.
- **Line in:** RCA, with a zero-latency "DJ direct" path.
- **Amp:** a TPA3221 class-D amp giving 2 x 77 W into 4 ohm.
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
- **Plus build:** `BOM.csv` (72 lines, every one with an LCSC number), `CPL.csv` (230 placed parts), `BOM-full.csv` and `prebuy.csv`, which lists the Extended parts.
- **Ultra build:** `fab/ultra/` holds `BOM.csv` and `CPL.csv` (272 placed parts) for the same Gerbers.

Board facts:

- **Size:** 4 layers, 120 x 100 mm, 1.6 mm.
- **Stackup:** JLC04161H-1080B, 1 oz outer.
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
| Zone A amp | TPA3221 (24 dB, AD mode, 600 kHz), 10 µH / 10 A filters, 2 screw terminals |
| Zone B amp [Ultra] | the same, synchronised to zone A as a peripheral |
| Control | TCA9539 I/O expander (0x74, reset with the ESP32's EN); Plus front-edge knob, 2 buttons, IR receiver, status LED |
| Optical [Ultra] | PLR237 TOSLINK into the ES9018K2M's S/PDIF mux |
| Front board [Ultra] | 20-pin 0.5 mm FFC to `ultra-front` |

## Plus and Ultra from one board

| Parts | Plus | Ultra |
|---|---|---|
| zone B amp, its filters, bulk cap, terminals (U16, L5-L8, C3xx/R3xx, J10/J11) | not fitted | fitted |
| TOSLINK U19, FB3, C126 | not fitted | fitted |
| FFC J3, R402/R403 | not fitted | fitted |
| knob SW1, buttons SW2/SW3, IR U18 (+R400/C400) | fitted | not fitted |

`fab/BOM.csv`/`CPL.csv` are the **Plus** build. `fab/ultra/BOM.csv` and
`CPL.csv` are the **Ultra** build of the same Gerbers.

## First-spin review (review-2, 2026-10-29)

Six independent reviewers (power, audio, digital/RF, thermal, DFM, parts
pin-to-pad) read this board from its KiCad files and the vendor datasheets:
[`docs/review-2/`](../../docs/review-2/). What changed on core because of it:

| Finding | Was | Now |
|---|---|---|
| DIGITAL-1/2, PARTS-4 (blocker): the TCA9555's pull-ups left PHY_RST (the 50 MHz oscillator into GPIO0, a boot strap), PVDD_EN and the amp/DAC resets mid-rail at power-up, and its outputs held through ESP32 resets | TCA9555, R45 100k | TCA9539 (no pull-ups; RESET = ESP_EN), R45 10k |
| POWER-1 (blocker): the 5 A path ran through 0.25 mm tracks and 1-3 vias | SMD USB4105, few vias | through-hole USB4085 (power pins solid into the In2 VBUS_IN plane), copper patches with 8 vias at R1's input, 10 from R1's output to Q1's source, 8 at Q1's drain; 3-4 per TPA3221 PVDD pin group |
| THERMAL-1/DFM-3 (blocker): TPA3221 is pad-up with no heatsink | none | a heatsink pressed onto each TPA3221 from the lid (thermal pad under it), and a firmware power governor (`docs/review-2/firmware-requirements.md` C2/C3); no room for clip holes beside the chip without moving its PVDD decouplers |
| DFM-1 (blocker): RCA J4/J5 had no holes for the jack's snap-in legs | none | Lite's footprint: 2.0 x 3.0 leg holes, signal pins as slots that fit either reading of the drawing |
| PARTS-7/DFM-12 (blocker, Ultra): TOSLINK T8BK on a T10BK footprint | no post holes | the T8BK's two 1.70 mm post holes added |
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
reviewers' fixes in `kicad/gen/blocks*.py`, then `via_array.py`,
`ant_keepout.py`, `miniroute.py`, hwde's `route_auto` (its KRT fallback; Freerouting
stalled), `stitch_vias`, `island_vias.py`/`gnd_via_drops.py` and hand tracks
(`add_track.py`/`add_via.py`). IR_IN keeps a 25 mm hop on In1 (the GND plane)
under the front UI area, where nothing fast crosses it.

## Design decisions and why

These are the decisions that differ from, or add to, docs/product.md.

- **Current sense:** INA186 (40 V), not INA180. The INA180 stops at 26 V
  common mode, under the 28 V rail. The sense taps are Kelvin, through
  10 Ω + 100 nF.
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
  backstop against gross faults only: the TPA3221's absolute maximum is
  37 V, and a zener cannot trip accurately at 30 V without tripping at the
  legitimate 29.4 V EPR maximum.
- **Snubbers:** on the speaker side of the output filter, as in TI's
  figure 9-1. On the switching node a 1 nF snubber would dissipate 0.47 W
  in each resistor.
- **Speaker terminals:** 2-way 5 mm screw terminals (17 A). The planned
  4 mm sockets (GC4010-F) have no EasyEDA model, and it was never confirmed
  that a real banana plug fits them. The enclosure's banana sockets or
  binding posts wire to the terminals, which product.md already allowed
  for.
- **Amp polarity:** the TPA3221 inverts, so OUTx_M goes to the speaker's
  + terminal.
- **DJ switch logic:** the expander's 3.3 V output can't drive the 5 V
  switch's logic (it needs VIH 3.5 V), so Q6 inverts it up to 5 V.
- **I2S:** one bit and frame clock for all three converters. The sub DAC
  takes its own data pin (I2S1 as slave), because the ESP32 has no pins to
  spare.
- **FFC pinout:** the ultra-front board's map. It needs SPI, I2C and an
  interrupt line, and the front board carries its own expander.

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
- **`silk_clean.py`:** hides reference texts and deletes footprint silk that
  ran over pads or the edge. The assembly drawing keeps every reference.

**Current ratings:** the width rules for the plane-fed rails (VBUS_IN, VBUS,
PVDD, +3V3) and the amp OUT/SPK nets are sized for the tracks that remain,
which are taps and bootstrap links. The main currents ride on the In2 plane
regions and the solid F.Cu/B.Cu pours, which the width rules don't see
(`constraints.json` `power_notes`).

## Testing a built board

1. **Power with no amp:** plug a laptop USB-C port or a 5 V supply into J1.
   - The board boots with PVDD off.
   - The ESP32 appears as a CH340 serial port, and it can be flashed
     through the CH340.
2. **Rails:** check +5V (5.1 V), +3V3, +3V3A and the 2.4 V VREF.
3. **PD contract on a 140 W EPR charger:** use a 240 W (5 A) cable. Read
   the contract from the CH224Q over I2C (0x22/0x23); VBUS should be
   28 V.
4. **Amp bring-up:** set PVDD_EN (expander P03) and watch PVDD ramp in
   about 15 ms. Then release AMP_A_RST (P00) with DAC_EN (P04) set.
5. **Speaker polarity:** play a 1 kHz tone at low level into a 4 Ω dummy
   load and check the output polarity.
6. **Thermals:** check the TPA3221 heatsink temperature at 1/8 power.
   The thermal check is still open in product.md.
7. **Ethernet:** set PHY_RST (P05). This starts the 50 MHz clock, and the
   link should come up.

## Open items

Every fact this board relies on, and whether it's verified, is in
[`docs/known-unknowns.md`](../../docs/known-unknowns.md); run
`python3 tools/known_unknowns.py --check` before ordering. The ones that are
this board's to settle:

- **Heatsink (each TPA3221):** the enclosure presses it on (19 x 12 mm base,
  fins to the lid, at most 20 C/W in still air, 0.2 mm pad of 3 W/mK or
  better). Its exact part is the person's to buy with the enclosure; bring-up
  checks for no OTW at 1/8 power for 10 min (firmware C3).
- **5 V buck bootstrap loop:** C12 sits below L9, so its BS and LX links
  are long routed tracks. Watch U4's switch node for ringing at bring-up.
- **Hidden reference texts:** about 210 hidden to clear silk overlaps on
  the dense passives. The assembly drawing and the CPL carry every designator.
- **HR911105A centre taps:** taken as pins 4/5, the common practice; the
  datasheet text could not be read.
- **Extended parts:** most ICs are Extended, and JLC may hold them as idle
  stock (`fab/prebuy.csv`).
- **Firmware duties:** [`docs/review-2/firmware-requirements.md`](../../docs/review-2/firmware-requirements.md) (core section).
