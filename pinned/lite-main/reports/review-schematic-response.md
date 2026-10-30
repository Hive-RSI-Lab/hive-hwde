# Response to the schematic review (reports/review-schematic.md)

The fresh-context reviewer (opus, 2026-10-28) found 1 error and 14 warnings.
What was done about each:

| # | Finding | Severity | Done |
|---|---|---|---|
| 1 | Q1 soft-start cap was gate-source, so it only delayed turn-on; 20-40 A inrush into the amp bulk | error | **Fixed.** C2 is now 100 nF gate-drain (Miller) through R15 1k. Gate pull ~0.36 mA gives ~3.6 V/ms on PVDD, about 1.6 A into the 690 uF bulk, a 6 ms ramp. |
| 2 | SDZ driven straight from GPIO21 (TI: 100k series for the 10 V/ms and PVCC+0.3 V limits) | warning | **Fixed.** R54 100k in series; the 100k pull-down R52 moved to the GPIO side so SDZ still sees a full 3.3 V high. |
| 3 | SMBJ24A clamps at 38.9 V, above the 30 V abs-max of TPS54302/TPA3118 | warning | **Changed** to SMBJ20A (breakdown 22.2-24.5 V, clamp 32.4 V at 18.6 A). Still above 30 V at full surge current; at the few-amp events a USB-C hot plug produces it clamps near breakdown. A TVS cannot clamp a 20 V bus under 30 V at rated surge; noted as a residual risk. |
| 4 | C3/C4 10 uF/25 V 0805 derate to ~2 uF at 20 V | warning | **Fixed.** 10 uF 50 V 1206 + 4.7 uF 50 V 1206 (both JLC Basic). |
| 5 | Q1 body diode back-feeds the amp bulk into VBUS; no bleeder | warning | **Partly.** R16 22k PVDD bleeder added. The back-feed through the body diode stays (a second, back-to-back FET is cost the Lite tier does not carry); firmware drops PVDD_EN before any PD renegotiation. |
| 6 | PVDD enabled by GPIO18 only, not by PG as blocks.md said | warning | **Fixed in hardware.** Q4's emitter now goes to CH224Q PG (open drain, low = contract good): PVDD switches on only when firmware asks AND the PD contract is good. A 5 V-only port can never power the amp. |
| 7 | LP5907 output capacitance 11.2 uF, above its 10 uF limit | warning | **Fixed.** C32 is 4.7 uF: 1 + 4.7 + 2 x 0.1 = 5.9 uF. |
| 8 | No ESD on USB-C data/CC | warning | **Not done**, recorded as a decision: the Lite tier is cost-first; CH340C and CH224Q carry their own HBM ESD cells. Plus/Ultra should add a USBLC6-2SC6. |
| 9 | VBUS ceramic above the 10 uF cSnkBulk limit | warning | **Accepted.** About 15 uF at 5 V (the 50 V parts derate little there); PD sources tolerate it in practice. Logged as a known deviation. |
| 10 | PVDD bulk ripple rating 195 mA | warning | **Fixed on one side.** C58 is now a 470 uF 35 V polymer (3.8 A ripple); C59 stays a 220 uF electrolytic. |
| 11 | 26 dB gain clips 3.7 dB below codec full scale | warning | **Accepted, by design.** Firmware caps the codec volume (and the power governor limits it further); 20 dB would leave the amp at 20 W into 4 ohm. |
| 12 | White LED at 0.2-0.6 mA | warning | **Fixed.** Red KT-0603R (Basic, Vf 2.0 V) with 1k: about 1.3 mA. |
| 13 | IR and a button on GPIO39/36 (ESP32 errata 3.11) | warning | **Fixed for IR** (moved to GPIO14). The vol- button stays on GPIO36: a polled, debounced button tolerates the glitch. |
| 14 | CH224Q internal I2C pull-up rail unknown | warning | **Mitigated.** R2/R3 are 0R links: if bring-up measures CFG2/CFG3 idling above 3.6 V, depopulate them. |
| 15 | ERC blind because all pins are passive | warning | **Noted** in the README as a hwde snag (no datasheet extracts were made, so lib_pin_types typed every pin passive). The reviewer's pin-by-pin netlist check stands in for it. |
