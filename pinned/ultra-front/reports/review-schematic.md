# Schematic review - ultra-front (adversarial, P4)

Inputs reviewed: kicad/ultra-front.net (regenerated, H1-H4 removed; 34 parts, all
nets listed below were checked), reports/ultra-front-schematic.pdf (1 page, A3),
parts/parts.json, kicad/decoupling.json, requirements.md, architecture/*.md,
architecture/constraints.json, and every datasheet in research/datasheets/.
Build mode: none declared (requirements section 1), so no scope tier applies:
normal design.

Result: 0 errors, 6 warnings, 7 info. Nothing found stops bring-up. All pin maps
match their datasheets.

## Errors

None.

## Warnings

### W1 - SPI clock of 40 MHz is 2.6x over the LCD's rated write clock
- Nets: /LCD_SCK, /LCD_MOSI, /LCD_CS, /LCD_DC (J1.5/7/8/9 -> J2.4/5/2/3).
- requirements.md section 2 says "SPI up to 40 MHz over < 150 mm of FFC".
  HS20HS072RX datasheet p.12 (Table 6): T_SCYCW serial clock cycle (write) is
  66 ns min, so the limit is 15.1 MHz. SCL H/L are 15 ns min each.
- ST7789 parts often run past spec on a short tail. This one also has 150 mm of
  FFC, and nothing on this board terminates the line (no series R at J2). A
  40 MHz setting can corrupt pixels or commands.
- Action: the core firmware and requirements should start at <= 15-20 MHz. Only
  raise it after a bench test. Any series termination has to sit at the ESP32
  (source) end, on the core board.

### W2 - FFC cable type is not specified, and a mirrored cable puts GPIOs in conflict (rails survive)
- J1 is FPC-05F-20PH20, a bottom-contact part (parts.json attribute "Contact
  Type: Bottom Contact"). "Pin N on core = pin N here" only holds for the right
  FFC type (same-side or opposite-side contacts) given the core connector's
  contact side.
- With a mirrored cable (pin N -> 21-N), the rails are safe as designed: 1/20,
  2/19 and 3/18 are symmetric. The signals are not:
  - core LCD_BL_PWM (pin 10, ESP32 output) lands on our pin 11 = GND, which
    shorts the GPIO.
  - core LCD_SCK (pin 5, output) lands on our pin 16 = /ENC_A. Each encoder
    A-C closure then shorts the GPIO through the encoder contact, which is
    rated 10 mA / 5 V DC (C361165 p.276, spec table).
  - core LCD_MOSI (pin 7, output) lands on our pin 14 = /FRONT_INT_N, so it
    fights U1's open-drain INT.
- Action: write the exact FFC type (contact sides, length) into the BOM and
  requirements, and mark pin 1 on the silk at both ends.

### W3 - Unused TCA9535 I/Os float at power-up and cause spurious INT
- Netlist: unconnected-(U1-P06-Pad10), (U1-P07-Pad11), (U1-P14..P17-Pad17..20).
- C22396388 p.3: "At power on, all I/Os are configured as inputs". p.5:
  II = +/-1 uA, so the inputs have no keeper. The TCA9535 has no interrupt
  mask register (p.7 Table 1 register map: input/output/polarity/config only), so a
  floating input that toggles asserts /FRONT_INT_N. That can hold the core in an
  interrupt storm until firmware sets these pins as outputs.
  blocks.md B4 relies on firmware ("firmware sets them as outputs low").
- Action: add a pull-down (10k-100k) to GND on each of the six pins, or tie
  them to GND directly if firmware will never drive them high. The design can
  also keep the firmware-only approach as a recorded decision.

### W4 - Turning the RGB LED off by switching the pin to input leaves the G/B cathodes high-Z near VIH, and each change triggers INT
- Nets: /LED_R, /LED_G, /LED_B (U1.14/15/16 -> R14/R15/R16 -> D1.4/3/2;
  D1.1 = +5V).
- With the pin as input, its voltage is about 5 V minus the LED's leakage Vf.
  For green/blue (InGaN, Vf 2.8-3.6 V at 20 mA, C273610 p.3) that is roughly
  2.4-3.0 V. U1's VIH is 0.7 x VCC = 2.31 V (C22396388 p.5), so the margin is
  thin and the node is high-Z, set only by leakage that moves with light and
  temperature. Every on->off change also triggers input-change INT, and that
  cannot be masked.
- The 5 V-tolerance claim itself is correct (C22396388 p.6: "input voltage may
  be raised above VCC to a maximum of 5.5V"; VIH max 5.5 V, p.5).
- Action: for G and B, firmware can use output-high (3.3 V) as "off". That
  leaves 1.7 V across the die, below the 2.8 V Vf min, so no glow and no
  interrupt. Keep input-mode "off" only for red, and have the INT handler
  ignore P11-P13.

### W5 - Backlight resistors are loaded to 80 % of rating, and the docs describe a different circuit
- Nets: +5V -> R13 || R17 (75R 0603 each, 100 mW; parts.json C4275) -> /BL_A
  -> J2.10. /BL_K = J2.11 -> Q1.3 (D).
- Two 75R in parallel = 37.5R. LED Vf is 2.8-3.2 V (HS20HS072RX p.6), so
  I = 41 mA (4.75 V, 3.2 V) / 53 mA typ / 65 mA (5.25 V, 2.8 V). That meets
  the 80 mA abs max (p.5) and the "about 60 mA" target.
- Worst-case dissipation per resistor = 2.45^2 / 75 = 80 mW, i.e. 80 % of a
  100 mW 0603, in a closed 3D-printed enclosure at 40 C.
- blocks.md B3 and power_tree.md still describe "R13 33 R 1206, 0.10-0.15 W
  (1206 rated 0.25 W)", which is a different circuit from the schematic.
- Action: use 0805 or 1206 parts, or accept the margin as a recorded decision.
  Update blocks.md and power_tree.md to match the netlist.

### W6 - The board assumes things about ESP32 strapping pins that only the core can confirm
- This board puts fixed bias on core GPIOs at boot:
  - /LCD_BL_PWM: R12 100R + R11 100k pull-down.
  - /ENC_A, /ENC_B: 10k pull-up plus 10 nF each (R1/C6, R2/C7).
  - /FRONT_INT_N: R7 10k pull-up.
  - /IR_OUT: R9 10k pull-up.
- If the core puts any of these on a strapping pin, boot fails. On a classic
  ESP32: GPIO12/MTDI pulled high selects 1.8 V flash, and GPIO0 pulled low
  enters download mode. On S3: GPIO0/3/45/46.
- Action: check the core's pin assignment against these nets.

## Info

- I1 - LCD supply headroom: J2.8 (IOVCC) and J2.9 (VCC) are on +3V3, which is
  3.2-3.4 V (power_tree.md). The datasheet recommends a 3.3 V max (VCI, IOVCC,
  p.6) with a 4.6 V abs max (p.5). Up to +0.1 V over the recommended max is
  common practice. Noted only.
- I2 - J2 pin-1 orientation is a placement item. The netlist maps J2.N = LCD
  pin N, 1:1, which is correct against p.11 and the p.10 drawing
  (1 GND, 2 CS, 3 RS, 4 SCL, 5 SDA, 6 RESET, 7 NC, 8 IOVCC, 9 VCC, 10 LEDA,
  11 LEDK, 12 GND). The tail reads "1..12" on the display face and "12..1" on
  the back (p.10). The dual-contact FPC-05FB fixes which face makes contact,
  but not the left/right order. The placer must put J2 pin 1 on the tail's
  pin-1 side for the planned tail path (flat vs folded).
- I3 - The encoder part has no detents. EC11E1834403 per ALPS C361165 p.276 is
  vertical, 20 mm flat shaft, 7 mN.m torque, detents "none", 18 pulses, push
  switch with 1.5 mm travel. Confirm that a smooth knob is what the UX wants
  (EC11E18244A5 is the same part with 36 detents). The pins are correct per
  drawing No.5 (p.278): A/C/B with C common, D/E push switch, frame lugs F/G.
  Netlist: SW4.C, SW4.E, SW4.F, SW4.G = GND; A = /ENC_A, B = /ENC_B,
  D = /ENC_SW. The footprint is named SW-TH_EC11E1820402 (the no-push
  variant), but it has D/E pads at 5.08 mm, which matches drawing 5. Name only.
- I4 - The button and encoder lines have no ESD parts. That is acceptable: the
  enclosure is printed plastic and the encoder shaft and frame are grounded
  (F/G to GND). The tact-switch steel cover is not grounded; if the caps are
  thin, a TVS array on /BTN1-3 is cheap insurance.
- I5 - architecture/constraints.json placement.fixed still lists H1-H4. The
  symbols were removed (now board-only), so make sure the holes are added at
  board setup or the list is updated.
- I6 - The FPC connector datasheets C2856803.pdf and C2856829.pdf have a broken
  xref and poppler cannot open them. A copy repaired by stripping startxref and
  running `mutool clean` reads fine (5 pages: generic spec plus drawing). The
  drawing gives 0.5 mm pitch, 0.3 mm FFC, pins numbered from one end, and the
  pin count matches. Re-download them for the archive.
- I7 - The LCD can only be reset through U1 P10 (/LCD_RST, R8 10k pull-up, so
  the LCD comes out of reset at POR). The LCD init depends on I2C working
  first; the ST7789's own POR plus SWRESET is the fallback. This is a firmware
  ordering note.

## Verified correct (evidence)
- J1 pinout vs requirements section 2: all 20 pins match. Netlist: GND on 1, 4,
  6, 11, 20, 21, 22; +3V3 on 2 and 19; +5V on 3 and 18; signals on 5, 7-10,
  12-17 as specified.
- J2 vs the LCD tail (p.11): matches 1:1. Pin 7 NC is left unconnected; shell
  pins 13 and 14 go to GND.
- U1 TCA9535 TSSOP-24 pinout (C22396388 p.3):
  - INT = 1, A1 = 2, A2 = 3, P00-P07 = 4-11, GND = 12, P10-P17 = 13-20,
    A0 = 21, SCL = 22, SDA = 23, VCC = 24. The netlist matches.
  - A0/A1/A2 are all on +3V3, giving 0x4E/0x4F 8-bit = 0x27 7-bit (p.10
    Table 3). No clash with 0x22/0x23/0x48/0x49.
  - INT is open-drain with R7 10k to +3V3. Its 0.33 mA is within the 3 mA
    IOL (p.5).
  - VCC is 3.3 V, within 1.6-5.5 V. C1 100 nF sits on pin 24.
- D1 (C273610 p.2 circuit): pin 1 = common anode, 2 = B, 3 = G, 4 = R.
  Netlist: D1.1 = +5V, D1.2 = /LEDB_K, D1.3 = /LEDG_K, D1.4 = /LEDR_K.
  - Currents: R (5 - 2.0)/1k = 3 mA; G/B (5 - 2.8..3.6)/680 = 2-3.2 mA. Both
    are far under the 30 mA abs max and under U1's IOL (25 mA at Tj <= 65 C).
  - The footprint pad order (1 TR, 2 TL, 3 BL, 4 BR) matches the top view.
- U2 IRM-H638T (C91447 p.1, p.2 application circuit):
  - Pins 1/2 = GND, 3 = OUT, 4 = Vcc. Netlist: U2.1/U2.2 = GND,
    U2.3 = /IR_OUT, U2.4 = /IR_VCC.
  - The filter is R10 100R (datasheet 47-220R) plus C5 4.7 uF (>= 2.2 uF).
    Drop is <= 70 mV at Icc 0.7 mA, so the device sees >= 3.13 V
    (Vs min 2.7 V).
  - The R9 10k pull-up is the datasheet's ">= 10 k optional". VL 0.5 V max at
    a 2 mA sink.
- SW1-SW3 TS-1187A-B-A-B (C318884): A-B form one terminal pair and C-D the
  other, with the switch between the pairs. Netlist: A/B = /BTNn, C/D = GND,
  so a press pulls the 10k-up line low. The footprint pads at (+/-3.0,
  +/-1.85) match the 5.0/7.0 x 3.0/4.5 land pattern.
- Q1 AO3400A SOT-23: G = 1, S = 2, D = 3. Netlist: Q1.1 = /BL_GATE,
  Q1.2 = GND, Q1.3 = /BL_K. It is a low-side switch on the LED cathode, so the
  polarity is correct. The R11 100k pull-down keeps the backlight off while the
  core is in reset. 3.3 V of gate drive is ample for a logic-level part.
- Encoder RC: 10k x 10 nF = 0.1 ms. That is well above the bounce window and
  much shorter than the pulse spacing at hand-spin rates.
- Decoupling (decoupling.json vs ICs):
  - U1 VCC: C1.
  - LCD VCC/IOVCC (adjacent pins 8/9): C4.
  - U2: C5.
  - Bulk at J1: C2 on +3V3, C3 on +5V.
  - No IC power pin is left without a cap.
- Rail budget: +5V carries about 74 mA worst case over 2 FFC contacts rated
  500 mA each (connector attributes). No rail exceeds any IC's abs max:
  - U1: 6 V.
  - LCD: 4.6 V.
  - IRM: 6 V.
  - LED pins: max about 5 V on U1 inputs vs 5.5 V allowed.
