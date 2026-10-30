# ultra-front

The front panel of the hifi-receiver-2 **Ultra** tier (see `docs/product.md`):
a 2-layer 110 x 40 mm board carrying the 2.0" colour screen, the volume knob,
three buttons, the IR remote receiver and an RGB status LED. It has no
processor: the `core` board's ESP32 drives everything over one 20-pin FFC
cable. Designed with the hive-hwde module on 2026-10-28, from schematic
through routed layout to a JLC fab package. **Nothing has been ordered;
ordering is the person's.**

![Top](reports/renders/ultra-front_top.png)

## What it is

- 2 layers, 1.6 mm FR-4, 1 oz, HASL, 110 x 40 mm, 2 mm corner radius,
  4 x M2.5 plated mounting holes (GND) 3.05 mm in from each corner.
- Everything is on the top side, which faces the user. All parts are SMD
  except the knob (EC11, through-hole).
- GND is poured on both layers, plus routed GND tracks and a via at each cap/IC GND pad.

| Ref | What | Part | LCSC | JLC |
|---|---|---|---|---|
| J1 | 20-pin 0.5 mm FFC socket to the core board | FPC-05F-20PH20 (bottom contact) | C2856803 | Extended |
| J2 | 12-pin 0.5 mm FPC socket for the LCD tail | FPC-05FB-12PH20 (contacts top and bottom) | C2856829 | Extended |
| U1 | 16-bit I2C GPIO expander, address 0x27 | TCA9535PWR (UMW) | C22396388 | Extended |
| U2 | 38 kHz IR remote receiver | IRM-H638T/TR2 | C91447 | Extended |
| SW4 | Rotary encoder with push switch (smooth, no detents) | EC11E1834403 | C361165 | Extended |
| SW1-SW3 | 5.1 mm tact switches | TS-1187A-B-A-B | C318884 | Basic |
| D1 | RGB status LED, common anode | TJ-S1615SW6TGLCCYRGB-A5 | C273610 | Extended |
| Q1 | Backlight switch | AO3400A | C20917 | Basic |
| R/C | 19 resistors, 7 capacitors, 0603/0805 | see `fab/BOM.csv` | | Basic |

Not on the board, and not assembled by JLC:

- **The LCD:** HS20HS072RX, 2.0" 320x240 IPS, ST7789, LCSC C5329582,
  $2.43 at 100. Its 12-pin tail plugs into J2, and the panel is taped to
  the board at final assembly.
- **The FFC cable to the core board:** 20-pin, 0.5 mm pitch. Its length and
  type are set by the enclosure (see the next section).

**Parts cost:** $2.80 per board at 10 and $2.42 at 100, plus the LCD. It has
6 extended parts.

## The connector to the core board (J1)

Pin N on the core board connects to pin N here.

| Pin | Signal | Pin | Signal |
|---|---|---|---|
| 1 | GND | 11 | GND |
| 2 | +3V3 | 12 | I2C_SCL |
| 3 | +5V | 13 | I2C_SDA |
| 4 | GND | 14 | FRONT_INT_N (U1 INT, open-drain, 10 k pull-up here) |
| 5 | LCD_SCK | 15 | IR_OUT (active low, 10 k pull-up here) |
| 6 | GND | 16 | ENC_A (10 k pull-up + 10 nF here) |
| 7 | LCD_MOSI | 17 | ENC_B (10 k pull-up + 10 nF here) |
| 8 | LCD_CS | 18 | +5V |
| 9 | LCD_DC | 19 | +3V3 |
| 10 | LCD_BL_PWM (100 k pull-down here: backlight off until driven) | 20 | GND |

- **The rails are mirror-symmetric** (1/20, 2/19, 3/18). Whether pin 1 lands
  on pin 1 or pin 20 depends on the FFC type, same-side or opposite-side
  contacts. A wrong cable therefore never shorts a rail, but three signals
  would fight: the core's BL_PWM would meet GND, SCK would meet ENC_A and
  MOSI would meet INT. **The cable must map pin N to pin N.**
- **The cable:** 20-pin, 0.5 mm, 30-50 mm, **type B** (opposite-side
  contacts).
  - **Why type B:** in the Ultra enclosure the cable leaves the core's J3
    contacts-down, runs forward under this board and turns up into J1. Both
    ends are the bottom-contact FPC-05F-20PH20 (C2856803), the same part on
    both boards. A type-B cable then meets pin N to pin N without twisting.
  - **Source:** the enclosure task's `enclosure/README.md`. The core
    board's J3, as merged, carries this same pinout.
- **LCD_CS (pin 8):** the core holds it low with a 10 k resistor (its R57)
  rather than driving it. The LCD is the only device on that SPI bus, and
  the ST7789 runs with CS held low; firmware uses cs = -1.
- **Power drawn:** +3V3 about 30 mA, +5V about 70 mA, of which the
  backlight is about 50 mA.
- **What the core gives up:** 4 SPI outputs (SCK, MOSI, CS, DC), one PWM
  output for the backlight, 2 inputs (INT, IR; input-only GPIO34-39 is
  fine) and the shared I2C bus.
  - ENC_A/ENC_B are optional: wire them to 2 GPIOs for hardware quadrature
    (PCNT), or leave them unconnected and read the knob through U1.
  - I2C pull-ups are on the core, not here.
  - Put 22-33 R series resistors on SCK/MOSI at the ESP32.
  - Run SPI at 15 MHz or less: the LCD's minimum write clock period is 66 ns.
  - Keep these nets off ESP32 strapping pins (GPIO0/2/5/12/15), because this
    board biases them at boot.

## Design decisions, and why

- **An I2C expander (U1) reads the knob and buttons and drives the LED and
  LCD reset.** The core's ESP32-WROVER-E already gives its pins to Ethernet,
  two I2S buses and I2C, so the front board must cost it as few GPIOs as
  possible. U1 is at 0x27, clear of the CH224Q (0x22/0x23) and the
  ES9018K2M (0x48/0x49) on the same bus.
  - **Inputs:** P00-P05 are ENC_A, ENC_B, ENC_SW and BTN1-3. The TCA9535 has
    no internal pull-ups, so each input has a 10 k one.
  - **Outputs:** P10 is LCD_RST, with a 10 k pull-up so the LCD comes out of
    reset at power-up. P11-P13 drive the LED's red, green and blue.
  - **Unused:** P06, P07 and P14-P17 are pulled to GND together through R19.
    A floating input would keep firing INT, and the TCA9535 can't mask it.
- **The LCD socket J2 has contacts on both sides.** The panel lies on the
  board's top side, and its tail runs flat to J2 with the contacts facing up,
  so a bottom-contact socket would make no connection.
  - **Placement:** J2 sits 20.7 mm from the panel's left edge, matching the
    tail length on the datasheet drawing (page 10). Its pin 1 is at the top,
    on the tail's pin-1 side.
  - **Keepouts:** the area under the panel and the tail's path stay free of
    parts.
- **The backlight runs from +5V** through three 120 R resistors in parallel
  (40 R) and a low-side AO3400A.
  - **Current:** 39-61 mA over the supply and LED tolerances, 50 mA typical,
    against the datasheet's 80 mA.
  - **Why +5V:** the LEDs' 2.8-3.2 V leaves no headroom on 3.3 V.
  - **Why three resistors:** they keep each 0603 at 50 mW. A single 33 R 1206
    is not a JLC basic part.
- **The RGB LED is common anode on +5V,** because the green and blue dies
  need about 3 V. U1's pins are 5 V tolerant.
  - **Off, in firmware:** green and blue are driven high (3.3 V), and red is
    set as an input. 1.7 V could just light the red die.
  - **INT:** the interrupt handler ignores P11-P13.
- **The knob has no detents.** The EC11E1834403 is the cheapest EC11 with a
  push switch, and a smooth volume knob is normal on hi-fi. EC11E18244A5 is
  the detented drop-in on the same footprint.
- **The board is 110 x 40 mm**, against product.md's "about 100 x 40". The
  51.8 mm panel, its 20.7 mm tail and the 33 mm knob column don't fit in 100.
- **The LCD tail sets the left-end layout.** The panel sits at x 25.45 mm.
  - **Why there:** its 20.7 mm tail then ends at J2's insertion stop, 2.1 mm
    inside the socket mouth. The board reviewer measured the tail end
    within 0.08 mm of the stop, with a tolerance of +-0.5 mm.
  - **Tail shape:** the tail is about 20.5 mm wide for its first 10 mm, where
    it leaves the panel, and 6.5 mm wide where it enters J2. Both parts are
    keepouts, so no part sits under them and it lies flat.
  - **Moved for this:** the backlight parts and U1. The first board review
    caught the tail outline and the socket position, and both were fixed
    before routing (`reports/review-board.md`, then
    `reports/review-board-2.md`).
- **Routing:**
  - **Freerouting routes everything, GND included.** It runs before any pour
    exists, inside a 0.5 mm edge keepout. Without the keepout it ran tracks
    along the edge. It also doesn't know JLC's 0.5 mm hole-to-hole rule, so
    the default clearance is 0.2 mm, which keeps via holes 0.5 mm apart.
  - **GND vias:** each cap and IC GND pad then gets its own via.
  - **Pours:** GND is poured on both layers. The top pour joins its pads solid
    (`kicad/gen/zone_solid.py`), because the 0.5 mm sockets' GND pins can't
    take two thermal spokes.
  - **Hand-routed:** three gaps the Freerouting import dropped (J1.11 GND,
    two +3V3 links) were routed with a small grid router in
    `route/manual/`.

Every decision is also recorded in `state.json` (`decisions`), with the
stage digests in `log/P*-digest.md`.

## Where things are, for the enclosure

Coordinates are in mm from the board's top-left corner, looking at the top
side (the front). They are centres unless noted.

| What | X | Y | Notes |
|---|---|---|---|
| LCD panel outline | 25.45-77.25 | 1.9-38.1 | 51.8 x 36.2 mm module, taped down at this position so its tail ends at J2's stop (+-0.5 mm). The enclosure window should locate it. Active area 40.8 x 30.6 mm, offset in the module per datasheet p.10 |
| Knob (SW4 shaft) | 99.0 | 21.0 | EC11, 20 mm flat shaft |
| Buttons SW1 / SW2 / SW3 | 84.0 | 8.0 / 20.0 / 32.0 | 5.1 mm switches, 1.5 mm actuator: the panel needs plungers or caps |
| IR receiver U2 | 99.0 | 6.0 | needs a window |
| RGB LED D1 | 98.0 | 33.6 | needs a light pipe |
| J1 (FFC to core) | 14.0 | 35.6 | mouth at the bottom edge |
| Mounting holes | 3.05 / 106.95 | 3.05 / 36.95 | M2.5 |

For a STEP model: `kicad-cli pcb export step kicad/ultra-front.kicad_pcb`.
The 3D models aren't committed; pull them first (below).

## The checks it passed

| Gate | Result |
|---|---|
| ERC (`erc`) | 0 errors, 0 warnings |
| Placement (`place`) | pass, no waivers |
| Routed DRC (`drc_routed`) | 0 errors, 0 warnings, 0 unconnected |
| Verify suite (`verify`, 15 checks incl. check_courtyard) | pass, no waivers; 17 warnings: 9 short "needless jog" route-style findings, and 8 "no datasheet extraction" from check_ratings, because no datasheet JSON was made. The schematic reviewer checked every rail against each part's absolute maximum instead. |
| JLC DFM on the exported Gerbers (`dfm`) | 0 findings |

**J2 faces inward on purpose.** Its mate is the LCD tail lying on the board,
not a plug coming from off the board.
- **How it's declared:** constraints.json declares this in
  `placement.mates` (`onboard_flex`). The corridor is the tail's real outline,
  the same two rectangles as the keepouts.
- **What the checks do:** the place and verify checks hold J2 to that
  corridor instead of the board edge, so the two mating waivers of the first
  version are gone.

**Last passed on hive-hwde aa1ec48** (2026-10-29). `hwde check-all` passed:
erc, drc_routed, verify, fab and dfm. The gates were re-run in the
workspace, and check_courtyard found 0. `tools/bom_stock.py ultra-front`:
18 of 18 lines in stock for 100 boards.

**Lead decisions** (lead:hifi-receiver-2, 2026-10-28 12:14; the person
doesn't sign off stages):
- The board is approved as built (H1/H2/H4).
- Both J2 mating waivers were approved; they have since been replaced by
  the `placement.mates` declaration.
- The knob stays smooth, with no detents.

**Reviews:** fresh-context reviewers read the schematic
(`reports/review-schematic.md`: 0 errors, 6 warnings, all acted on) and the
board twice (`reports/review-board.md`: 2 errors on the LCD tail, fixed; `reports/review-board-2.md`: 0 errors, warnings below).

**Open items from the reviews**, none of which stops a first build:

- **JLC rotations:** check them in JLC's placement preview before ordering,
  especially J2, SW4, U1, U2 and D1. DFM can't check rotations visually
  here.
- **Front-panel height stack:** the LCD is 2.05 mm thick, the buttons stand
  1.5 mm and the encoder frame stands several mm, all facing the panel. The
  enclosure needs a pocket for the screen and plungers or caps for the
  buttons.
- **SPI ground return:** SPI runs partly on B.Cu with only the top pour
  above it, and CS/DC/MOSI are bundled. That's fine at 15 MHz or less;
  re-route on F.Cu over the bottom pour if a later revision goes faster.
- **The backlight sub-flex on the LCD tail:** it passes over the board near
  R13 at 0.13 mm. The datasheet doesn't show which face carries its K/A
  solder joints. Check on the first panel that nothing exposed touches the
  board; a strip of Kapton under the tail is the fallback.

## How to rebuild and re-check it

From this directory, with the hive-hwde module (`bin/hwde`, see its
`docs/README.md`):

```sh
H="sh $HIVE_MODULES/hive-hwde/bin/hwde"
eval "$($H env)"
python3 kicad/gen/parts_build.py                          # parts/parts.json from parts/search/
$H lib_pull --parts parts/parts.json --project kicad      # footprints/symbols/3D (3D not committed)
python3 kicad/gen/fix_courtyards.py && python3 kicad/gen/pin_types.py   # library repairs
PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib $HWDE_PY kicad/gen/root.py   # schematic (new UUIDs: keep the committed one for the board)
for g in erc place drc_routed verify dfm; do f=kicad/ultra-front.kicad_pcb; [ $g = erc ] && f=kicad/ultra-front.kicad_sch; $H gate --gate $g $f --workspace .; done
$H fab_export --pcb kicad/ultra-front.kicad_pcb --out-dir fab
$H bom_cpl --pcb kicad/ultra-front.kicad_pcb --out-dir fab --parts parts/parts.json --build-qty 10
```

- **Placement:** `anneal/placement.ops.json` (`place_edit`).
- **Routing, in order:**
  1. `kicad/gen/edge_keepout.py <board> 0.5`, with KiCad's python, then
     `route_auto`.
  2. `planes_gen`, then `kicad/gen/zone_solid.py`.
  3. The ops in `route/manual/`, applied with `route_edit`: the gap fixes and
     the GND vias.
  4. `route_cleanup --snap-only`.
- **Placement:** silk hiding is in `anneal/silk.ops.json`.

## The fab package

In `fab/`:

- **Gerbers:** `ultra-front_gerbers.zip`.
- **For JLC:** `BOM.csv` and `CPL.csv`.
- **Full record:** `BOM-full.csv`.
- **To buy first:** `prebuy.csv`, the 6 extended parts. JLC may ask for these
  to be bought into inventory.

At JLC:

- **Assembly:** order top side only, with "PCBA, top side". SW4 is
  through-hole and is soldered by JLC's THT service.
- **Rotations:** check the placement preview, especially J1, J2, D1 and U2.
  The FPC sockets' pull-tab lid faces the mouth.
- **The LCD:** order it separately as LCSC C5329582.

## How to test a built board

1. **Before connecting anything:** with no cable, measure +3V3 to GND and
   +5V to GND across J1's pins 2/1 and 3/4. Neither rail should be shorted:
   expect well over 1 k on each.
2. **Power it:** connect a bench supply through a J1 breakout (or the core
   board) at 3.3 V / 50 mA limit and 5.0 V / 100 mA limit. Idle draw should
   be a few mA on each.
3. **I2C:** an I2C scan from the core finds 0x27. With all buttons released,
   reading U1's input port 0 gives bits 0-5 high.
   - **Buttons:** pressing SW1/SW2/SW3 clears bit 3/4/5.
   - **Knob:** turning it toggles bits 0/1; pushing it clears bit 2.
   - **INT:** each change pulls J1 pin 14 low until the port is read.
4. **LED:** set P11/P12/P13 as outputs, low: D1 lights red, green and blue.
5. **IR:** point any NEC remote at U2. J1 pin 15 pulses low.
6. **LCD:**
   - plug in the panel, contacts either way up.
   - drive LCD_BL_PWM high: the backlight should draw about 50 mA from +5V.
   - init the ST7789 over SPI at 10-15 MHz.
