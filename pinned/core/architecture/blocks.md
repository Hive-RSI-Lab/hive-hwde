# core: architecture

One 4-layer board for **Plus** and **Ultra** (docs/product.md). Plus fits
everything except the parts marked **[Ultra]**. Ultra fits those as well and
leaves out the parts marked **[Plus]**, because its front board carries
those controls instead.

## Blocks

| Block | Parts | Notes |
|---|---|---|
| B1 USB-C in | J1 USB4105-GF-A-120 (48 V/5 A), D1 SMBJ30A, U1 CH224Q, U2 USBLC6-2SC6 | CH224Q asks for 28 V EPR with a single 210k on CFG1, and the ESP32 can read or change the contract over I2C (0x22/0x23). Its DP/DM are left unconnected, so D+/D- go only to the CH340C. |
| B2 Input current | R1 10 mΩ 2512, U3 INA186A1 (40 V common mode, 25 V/V) | 1.25 V at 5 A into GPIO34 (ADC1), for the power governor. INA180 was swapped for this part: the INA180 stops at 26 V common mode, under 28 V. |
| B3 PVDD switch | Q1 AO4485 P-FET, Q2 2N7002, Q3 S8050 + D2 BZT52C33, D4 BZT52C15, C bulk 2x 470 µF 50 V | PVDD stays off until firmware sets PVDD_EN, which keeps the PD inrush rule. A 1 µF gate-source cap (C8) holds Q1 off while VBUS steps at attach and at 5 V to 28 V, and D4 clamps Vgs at 15 V. The 100 nF gate-drain cap sets the turn-on ramp to about 2 V/ms, roughly 2 A into 940 µF. The 100k/100k gate divider gives Vgs = 14.7 V at 29.4 V. D2 and Q3 cut the enable for gross overvoltage, about 32-36 V. This is slow (R4·C8) and only a backstop: the TPA3221's absolute maximum is 37 V, and the CH224Q has its own OVP. |
| B4 Rails | U4 SY8303 (VBUS to 5.1 V, 1 MHz), U5 SY8089 (5 V to 3.3 V), U6 LP5907-3.3 (5 V to 3V3A), FB on +5VA | Rails: VBUS_IN, VBUS, PVDD, +5V, +5VA (analog: op-amp, switch), +3V3 (digital), +3V3A (DAC/ADC analog), +3V3E (PHY analog, via ferrite) |
| B5 ESP32 | U7 ESP32-WROVER-E-N8R8, U8 CH340C, Q4/Q5 S8050 auto-reset | Serial and flashing go over the same USB-C. |
| B6 Ethernet | U9 LAN8720A, J2 HR911105A, Y2 50 MHz oscillator | Y2 drives the 50 MHz RMII clock into the PHY's CLKIN (REF_CLK-in mode, nINTSEL=1) and into ESP32 GPIO0, which takes it as an input. This is the WT32-ETH01 scheme, and it avoids the experimental GPIO0 clock-out mode. Y2's OE is PHY_RST, held low at boot by a pull-down, so GPIO0 boots clean. nRST follows PHY_RST through 10k/100nF, so the PHY leaves reset about 1 ms after its clock starts. |
| B7 Sabre DAC | U10 ES9018K2M, Y1 50 MHz oscillator, U11 OPA1678 | A 50 MHz MCLK, not 100 MHz: with the internal DVDD the datasheet allows 50 MHz at most, and 100 MHz would need an external 1.3 V DVDD. 50 MHz still covers 24/96 PCM and 96 kHz S/PDIF, which is the product's hi-res target. The OPA1678 runs differential to single-ended at gain 0.54 on +5VA, around a stiff 470R/470R VREF of about 2.4 V (the 2 x 4.3k DAC legs load it): about 1.15 Vrms at full scale. |
| B8 Subs DAC | U12 PCM5102A | Its outputs are ground-centred at 2.1 Vrms, through 470 Ω to J4 (dual RCA: SUB1, SUB2). |
| B9 Line in | J5 dual RCA, U13 ES7243E ADC, U14 TS5A23157 | The input is halved (4.7k/4.7k, low enough to drive the amp input), so the FLX4's 2 Vrms becomes 1 Vrms and matches the DAC path. The ADC takes it AC-coupled and gets its MCLK from BCLK (reg 0x02 bit 7), so no MCLK pin is needed. The switch picks between the DAC path (NO, selected when DJ_SEL is high, the default) and DJ direct (NC). Q6 inverts the 3.3 V expander output up to the switch's 5 V logic, which needs VIH 3.5 V. Both of its inputs sit at 2.5 V, so it switches without a pop. |
| B10 Amp zone A | U15 TPA3221, L1-L4 10 µH (Isat 10 A), J6/J7 KF301 2-way screw terminals | AD mode, controller, 24 dB, single-ended inputs (IN_M AC-grounded), 600 kHz. VDD comes from +5V, with AVDD and GVDD each through 4.7 Ω + 1 µF, which bypasses the internal LDO (datasheet fig. 8-3). Each output has 10 µH + 1 µF, plus a 4.7 Ω + 1 nF snubber on the speaker side, as in TI fig. 9-1. On the switching node it would dissipate 0.47 W. The TPA3221 inverts, so OUTx_M goes to the + terminal and the speaker keeps absolute polarity. Speakers connect through screw terminals, not on-board 4 mm sockets. The GC4010-F socket the product page named has no EasyEDA model and was never confirmed to take a real banana plug. The enclosure's banana sockets or binding posts wire to these terminals (product.md open item 4). |
| B11 Amp zone B **[Ultra]** | U16 TPA3221, L5-L8, J10/J11, C bulk 470 µF | It runs as a peripheral (FREQ_ADJ to AVDD, OSCM/OSCP from U15), switched in sync with zone A. Its input is the same switched signal as zone A, so zone B plays zone A's program and has its own enable (AMP_B_RST). |
| B12 Control | U17 TCA9555 (0x20), SW1 EC11 knob **[Plus]**, SW2/SW3 buttons **[Plus]**, U18 IRM-H638T **[Plus]**, D3 red status LED | The ESP32 has no pins left (see below), so the slow controls go through the expander. |
| B13 Optical **[Ultra]** | U19 PLR237 TOSLINK | Goes to the ES9018K2M GPIO1, which is S/PDIF input 4 (spdif_sel). The board has a 10k pull-down, so the pin is defined when the part isn't fitted. |
| B14 Front-board FFC **[Ultra]** | J3 FPC-05F-20PH20 (20 pin 0.5 mm, bottom contact) | The pinout is below. |

## ESP32-WROVER-E GPIO map

GPIO6-11 go to the flash and GPIO16/17 to the PSRAM, so none of those six are
usable.

| GPIO | Use | Notes |
|---|---|---|
| 0 | ETH_CLK out (50 MHz to LAN8720 CLKIN) | boot strap, 10k pull-up, auto-reset pulls it low |
| 1 / 3 | UART0 TX / RX to CH340C | |
| 2 | I2S_BCK | strap: the DAC/ADC inputs are high-Z at boot |
| 4 | I2S_LRCK | |
| 5 | I2S_DOUT_MAIN (to ES9018K2M DATA2) | strap, high-Z load |
| 12 | I2S_DOUT_SUB (to PCM5102A DIN) | MTDI strap must be low at boot: the internal pull-down and a high-Z load hold it there |
| 13 / 14 | I2C SDA / SCL (2.2k pull-ups) | ES9018K2M 0x48, ES7243E 0x10, CH224Q 0x22, TCA9555 0x20 |
| 15 | LCD_DC (FFC) | MTDO strap |
| 18 / 23 | MDIO / MDC | |
| 19, 21, 22 | RMII TXD0, TX_EN, TXD1 | |
| 25, 26, 27 | RMII RXD0, RXD1, CRS_DV | |
| 32 / 33 | LCD_SCLK / LCD_MOSI (FFC) | |
| 34 | ISENSE (INA186) | ADC1, input only |
| 35 | IR_IN | input only |
| 36 | I2S_DIN (ES7243E SDOUT) | input only |
| 39 | EXP_INT (TCA9555 INT) | input only |

**I2S:** one bit and frame clock for all three converters. I2S1 runs as a
slave, taking its clocks from the I2S0 pads through the GPIO matrix, so the
sub DAC costs only one pin. This is a firmware assumption for the firmware
task to confirm.

## TCA9555 map (address 0x20)

| Pin | Net | Dir | Default |
|---|---|---|---|
| P00 | AMP_A_RST | out | 100k pull-down: amp held in reset |
| P01 | AMP_B_RST | out | 100k pull-down |
| P02 | DJ_DIRECT | out | 100k pull-down: DAC path |
| P03 | PVDD_EN | out | 100k pull-down: PVDD off |
| P04 | DAC_EN (ES9018K2M RESETB + PCM5102A XSMT) | out | 100k pull-down |
| P05 | PHY_RST (Y2 OE; LAN8720 nRST via 10k/100nF) | out | 100k pull-down: Ethernet clock off |
| P06 | LCD_BL (FFC) | out | |
| P07 | LED_STATUS (D3 + FFC) | out | |
| P10 | AMP_FAULT (U15/U16 FAULT, wired-OR) | in | 10k pull-up |
| P11 | AMP_OTW (OTW_CLIP, wired-OR) | in | 10k pull-up |
| P12-P14 | ENC_A, ENC_B, ENC_SW | in | 10k pull-up + 10 nF |
| P15-P16 | BTN_1, BTN_2 | in | 10k pull-up |
| P17 | LCD_CS (Ultra front board) | out | 10k pull-up: deselected |

## FFC to ultra-front (J3, 20 pin 0.5 mm) [Ultra]

This is the ultra-front board's map (worker 261028-145644, 2026-10-28;
boards/ultra-front/README.md). Pin N connects to pin N, and the rails are
mirror-symmetric, so a reversed cable shorts nothing.

| Pin | Net | Pin | Net |
|---|---|---|---|
| 1 | GND | 11 | GND |
| 2 | +3V3 | 12 | I2C_SCL (2.2k pull-ups on core) |
| 3 | +5V | 13 | I2C_SDA |
| 4 | GND | 14 | FRONT_INT_N, wired-OR onto EXP_INT (GPIO39) |
| 5 | LCD_SCK (GPIO32 via 33R) | 15 | IR_OUT, into IR_IN (GPIO35) |
| 6 | GND | 16 | ENC_A (core expander P12) |
| 7 | LCD_MOSI (GPIO33 via 33R) | 17 | ENC_B (core expander P13) |
| 8 | LCD_CS (core expander P17, 10k pull-up) | 18 | +5V |
| 9 | LCD_DC (GPIO15) | 19 | +3V3 |
| 10 | LCD_BL (core expander P06, on/off only) | 20 | GND |

The front board carries its own TCA9535 at 0x27 for its buttons, LED and
LCD reset. On Ultra, P14-P16 (ENC_SW, BTN_1, BTN_2) and D3 belong to the
Plus-only front-edge controls.

## Power tree

USB-C VBUS_IN (5-28 V) feeds three branches:

- **PVDD (switched):** through the TVS, the 10 mΩ shunt and the AO4485
  switch to PVDD, for TPA3221 x2.
- **+5V (5.1 V):** SY8303 to +5V, which feeds the TPA3221 VDD/AVDD/GVDD,
  the CH340C VBUS clamp, the FFC and LP5907 3V3A. It also feeds +5VA through
  a ferrite (OPA1678, TS5A23157, the 2.5 V reference) and the SY8089 3V3
  (ESP32, LAN8720 via ferrite, CH340C, expander, logic).

**+3V3 budget (0.8 A, constraints.json power[]):** the ESP32-WROVER-E's radio
transmit peak is 500 mA (Espressif's datasheet; fw constraints asks for it).
The rest of the rail: LAN8720A with its +3V3E analog side ~50 mA, the two
50 MHz oscillators ~30 mA, ES9018K2M DVCC ~25 mA, PCM5102A and ES7243E
digital ~10 mA, CH340C ~12 mA, TOSLINK and expander ~10 mA, and up to
~100 mA for the Ultra front board over J3, about 0.75 A in all. U5
(SY8089, 2 A) and L10 (SWPA4030S2R2MT, ~2.2 A) give it with margin. The rail
rides the In2 plane: hwde check_irdrop with L10 as the source and the loads
above gives 3.3 mOhm and 2.4 mV at the module. At the module: C30 22 uF +
C31 100 nF (Espressif's reference values), with 2 x 22 uF at the buck. The
+5V budget upstream (0.5 A) has not been raised to match: that is a separate
task (the +5V tracks are 0.35 mm).

## Physical

- **Board:** 4-layer, 1.6 mm, about 120 x 100 mm.
- **Rear edge:** zone A bananas, sub RCA, line-in RCA, zone B bananas.
- **Left edge:** USB-C, RJ45, TOSLINK.
- **Front edge:** EC11 knob, IR, two buttons, status LED, FFC.
- **Heatsinks:** each TPA3221 takes a bolted heatsink on its top slug,
  with two M3 holes per amp.

## Firmware notes from the schematic review

- **Releasing both amps:** zones A and B synchronise only when both RESETs
  are released at the same instant, so write AMP_A_RST and AMP_B_RST in one
  port-0 write.
- **ADC headroom:** the ES7243E has no headroom with a 2 Vrms source
  halved to 1 Vrms, so set the ADC PGA to 0 dB or below.
- **DJ direct:** set P02 (DJ_DIRECT) high for DJ direct. It drives through
  the Q6 inverter, so the switch input sits high when P02 is low.
