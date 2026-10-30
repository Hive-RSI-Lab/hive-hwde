# Blocks - ultra-front

```mermaid
flowchart LR
  CORE["core board (ESP32)"] ==|"J1 20-pin 0.5 mm FFC"| J1
  J1 -->|SPI SCK/MOSI/CS/DC| J2["J2 12-pin FPC -> 2.0in ST7789 LCD"]
  J1 -->|LCD_BL_PWM| BL["Q1 + R13: backlight switch, +5V"]
  BL --> J2
  J1 <-->|I2C 0x27, INT_N| U1["U1 TCA9535 16-bit I2C expander"]
  U1 -->|LCD_RST| J2
  ENC["SW4 EC11 encoder + push"] -->|A/B/SW| U1
  ENC -->|ENC_A/ENC_B direct| J1
  BTN["SW1-SW3 buttons"] --> U1
  U1 -->|R/G/B sink| D1["D1 RGB LED, +5V anode"]
  U2["U2 IRM-H638T 38 kHz"] -->|IR_OUT| J1
```

- B1 core link (J1): FPC-05F-20PH20, bottom contact, 20 pins. Pinout in
  requirements.md section 2. Bulk caps C2 (+3V3) and C3 (+5V), 10 uF, at J1.
- B2 LCD (J2): FPC-05FB-12PH20, double-sided contact (tail contacts face the
  display side, so either face works). LCD VCC and IOVCC on +3V3 (datasheet
  1.65-3.3 V / 2.4-3.3 V), C4 100 nF at J2. LCD_RST from U1 P10 with R8 10 k
  pull-up (LCD out of reset at power-up; the ST7789 also has its own POR).
- B3 backlight: +5V -> R13 || R17 || R18 (3 x 120 R 0603, 40 R) -> LED-A ...
  LED-K -> Q1 AO3400A drain, source GND. 4 LEDs in parallel, Vf 2.8-3.2 V:
  I = (V5 - Vf)/40 = 39 mA (4.75 V, 3.2 V) / 50 mA typ / 61 mA (5.25 V,
  2.8 V), under the datasheet's 80 mA. Worst case 0.15 W total, 50 mW per
  0603 (rated 100 mW). Three basic 0603s instead of one 33 R 1206 (not a JLC
  basic part) or two 75 R (80 % of rating, schematic review W5). Gate:
  LCD_BL_PWM through R12 100 R, R11 100 k pull-down so the backlight is off
  until the core drives it (or the core ties it high).
- B4 I/O expander (U1): TCA9535PWR (UMW), TSSOP-24, at 0x27 (A0..A2 to +3V3).
  INT_N open-drain, R7 10 k pull-up here. No internal pull-ups on the TCA9535,
  so every input has an external 10 k (R1-R6). C1 100 nF at VCC.
  P00 ENC_A, P01 ENC_B, P02 ENC_SW, P03 BTN1, P04 BTN2, P05 BTN3,
  P10 LCD_RST, P11 LED_R, P12 LED_G, P13 LED_B; P06, P07, P14-P17 unused,
  pulled to GND together by R19 10 k (a floating input would toggle INT,
  which the TCA9535 cannot mask: schematic review W3). Firmware keeps them
  as inputs.
- B5 encoder (SW4): EC11E1834403, THT, A/C/B quadrature (C = GND) and D/E
  push switch (E = GND). Mounting lugs F/G to GND (shaft ESD to ground).
  ENC_A/ENC_B: R1/R2 10 k pull-up + C6/C7 10 nF (tau 0.1 ms, debounce), to
  U1 and straight out on J1 pins 16/17 for the core's PCNT if it has pins.
- B6 buttons (SW1-SW3): TS-1187A-B-A-B 5.1 mm SMD tact (JLC basic), A/B to
  the button net, C/D to GND, 10 k pull-ups R4-R6.
- B7 IR (U2): IRM-H638T/TR2, +3V3 through R10 100 R with C5 4.7 uF (the
  datasheet's 47-220 R / >= 2.2 uF filter), R9 10 k output pull-up.
- B8 status LED (D1): TJ-S1615SW6TGLCCYRGB-A5 common anode on +5V. Cathodes
  sink into U1 P11-P13 through R14 1 k (red, Vf ~2.0 V, 3 mA), R15/R16 680 R
  (green/blue, Vf ~3.0 V, 3 mA). TCA9535 I/Os are 5 V tolerant. Firmware
  "off": green and blue = output high (3.3 V leaves 1.7 V across a 2.8 V
  die, dark, and no input-change interrupt); red = input (high-Z), since
  1.7 V could just light a red die. The INT handler ignores P11-P13
  (schematic review W4).
