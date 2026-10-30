# lite-main schematic review (adversarial)

Reviewer: hwde schematic-reviewer, fresh context. Inputs: kicad/lite-main.kicad_sch,
re-exported to reports/top.net (net-identical to reports/lite-main.net), rendered to
reports/schematic.pdf. Checked against datasheet text for CH224Q (V2.0), TPA3118D2
(SLOS708G), ES8388 (rev 5.0), TPS54302 (SLVSDG6C), LP5907 (SNVS798, fetched from ti.com),
and the BOM attributes in parts/parts.json. Checklists applied: power, mcu,
interface-usb, connector. No build mode is declared (no requirements.md), so the board
is judged as the product it is.

Result: **1 error, 14 warnings.**

## Error

### E1 - PVDD switch soft-start does not limit inrush (Q1, C2, R5, R6, Q4)
C2 (220 nF) sits gate-to-SOURCE on Q1 (AO4485). With R5 100k / R6 47k the gate has a
Thevenin of -13.6 V through ~32 kohm, so C2 only adds a delay (tau ~7 ms) before Vgs
reaches threshold. A gate-source capacitor does nothing to the drain slew: once the
gate passes about -2 V, Vgs keeps moving at ~1.6 V/ms and with gfs ~25-30 S the FET can
pass tens of amps within ~0.3 ms. Crss (~180 pF) gives only microamps of Miller
feedback against ~0.36 mA of gate drive, so it does not limit the slew either. The 440 uF
of bulk plus 3 x 1 uF therefore charges to 20 V in well under 1 ms. That is an estimated
20-40 A peak drawn from a 3.25-5 A PD source. The likely result is charger OCP and a VBUS
dropout. The ESP32, which sits on the same VBUS, then resets every time firmware enables
PVDD. It also stresses Q1's SOA. This breaks the product's own inrush rule (product.md:
"soft-started P-MOSFET").
Fix: make it a Miller soft-start. Put C (10-22 nF, with ~1 kohm in series) from gate to
DRAIN (PVDD) and keep R5 for turn-off. Size it for dV/dt ~2 V/ms, which is about 0.9 A
into 440 uF.

## Warnings (worst first)

1. **TPA3118 SDZ driven directly by GPIO21.** The abs-max table caps SDZ at
   10 V/ms slew (note 2 says a 100 kohm series resistor is needed above that) and at
   PVCC + 0.3 V. A 3.3 V high while PVDD is off violates that. Add series R and rescale
   R52, because 100k/100k gives 1.65 V, which is below VIH = 2 V.
2. **SMBJ24A does not protect anything.** VBR min is 26.7 V and the clamp is 38.9 V. That
   is above the 30 V abs-max of TPS54302 VIN and TPA3118 PVCC, and above the 25 V rating
   of C3/C4.
3. **C3/C4 10 uF/25 V X5R 0805 on 21 V.** After DC-bias derating they are about 2 uF
   each, while TI asks for >10 uF of ceramic (its example uses 35 V parts). Use 50 V
   1206/1210.
4. **Q1 body diode back-feeds PVDD bulk into VBUS.** This happens on every VBUS drop
   (hard reset, renegotiation to 5 V), and PVDD has no bleeder. The source then has to
   discharge 440 uF to reach vSafe0V.
5. **PVDD is enabled by GPIO18, not by CH224Q PG** as blocks.md B2 says (that pin map
   lists GPIO18 as unused). The guarantee that a 5 V-only port never powers the amp
   therefore rests on firmware alone. Document it, or AND it with PG in hardware.
6. **LP5907 COUT out of range.** It carries 11.2 uF nominal (C16 + C32 + C33 + C34)
   against a datasheet range of 0.7-10 uF.
7. **No ESD on USB-C D+/D-/CC.** No array or waiver on a user-facing connector of a
   product board (interface-usb checklist).
8. **VBUS ceramic at attach is ~15-21 uF**, above the 10 uF cSnkBulk limit.
9. **CH224Q I2C internal pull-ups (7-15 k) go to an undocumented rail.** If that rail is
   5 V, the bus idles at ~3.6 V, which is the ESP32's limit. Measure at bring-up; R2/R3
   are 0R links and can be removed.
10. **PVDD bulk ripple rating is low.** The RVT 220 uF/35 V is rated 195 mA at 120 Hz,
    which is too low for 2 x 43 W bass.
11. **Gain staging.** 1 Vrms full scale x 26 dB = 20 Vrms against ~13 Vrms clip, so the
    amp hard-clips 3.7 dB below 0 dBFS. Firmware must cap the volume, or use the 20 dB
    strap.
12. **D2 white LED gets only 0.2-0.6 mA** through 1k from 3.3 V (the comment says
    1.3 mA).
13. **IR on GPIO39 and a button on GPIO36.** ESP32 errata 3.11 causes false edges on
    these pins, so firmware must filter.
14. **ERC is blind.** All library pins are typed passive, so the 0/0 ERC is a
    connectivity check only.

## Verified OK (netlist against datasheet)
- CH224Q pins match table 4-1 (VHV 1, SCL 2, SDA 3, DP 4, DM 5, CC2 6, CC1 7, VBUS 8,
  CFG1 9, PG 10, EP GND). VBUS is tied to VHV with 1 uF/50 V. Rset 120k = 20 V
  (table 5-1). Single-resistor mode enables I2C at 0x22, so there is no clash with
  ES8388 at 0x10 (CE = GND). PG is open drain with a 10k pull-up to 3V3. Every pin is
  within the 32 V abs-max.
- TPS54302: pins correct. EN floats, which enables it (internal pull-up). BOOT has
  100 nF. FB 100k/13k gives 5.18 V. 10 uH matches TI's 5 V example.
- SY8089 (EN = IN, FB 150k/33k = 3.33 V) and LP5907 (EN = IN, 5.18 V < 5.5 V) are
  fine.
- AO4485: S = VBUS, D = PVDD, so the body diode blocks VBUS to PVDD. Vgs = -13.6 V
  at 20 V, within ±20 V. The Q4 NPN pull-down is correct.
- ESP32-WROVER-E pinout matches. Flash and PSRAM pins are NC. GPIO12, 2, 15 and 5 float
  (correct for a 3.3 V flash module). GPIO0 has a 10k pull-up plus the codec MCLK input
  (high-Z). EN has 10k/1 uF. The CH340C auto-program pair is wired the standard way
  (Q2: B<-DTR, E=RTS, C=EN; Q3: B<-RTS, E=DTR, C=IO0). UART is crossed correctly
  (CH340 TXD pin 2 to RXD0). CH340C V3 is tied to VCC for 3.3 V operation.
- ES8388: pins match the rev 5.0 table, including the single LRCK, HPGND and CE.
  DVDD/PVDD are on +3V3 and AVDD/HPVDD on +3V3A. VREF, ADCVREF and VMID each have
  10 uF + 100 nF. The I2S/I2C GPIOs match the A1S AudioKit (MCLK 0, BCLK 27, LRCK 25,
  DOUT 26, DIN 35, SCL 32, SDA 33).
- TPA3118D2: DAP pinout matches. MODSEL, MUTE and AM0-2 are at GND (BD, 400 kHz).
  PLIMIT = GVDD (no limit). GAIN is 20k to GND and 100k to GVDD (26 dB master,
  30 kohm input). SYNC is NC (master output). Bootstraps are 220 nF to OUTx.
  GVDD has 1 uF. PVCC has 1 uF/100 nF/1 nF per side plus 2 x 220 uF. The LC filter is
  10 uH + 1 uF. The single-ended input has INN AC-grounded with a matching 1 uF, as
  per 7.3.7. The thermal pad is on GND.
- USB-C pad order matches the standard 16-pin receptacle. D+/D- are doubled for both
  orientations and SBU is NC.
- Polarity: D1 pad 1 is the cathode (silk) and goes to VBUS. The D2 symbol has A = 1
  and K = 2. For the electrolytics, + goes to PVDD.
- The TS-1187A pad pairs 1/2 and 3/4 lie across the lead span (the joined pairs), so
  the buttons are not shorted.

## Note
The single A0 sheet is labels only and unreadable at print scale (schematic.pdf). Judge
from the netlist. This is not a bring-up risk, but it hinders debug.
