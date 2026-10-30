# Response to the board review (reports/review-board.md)

The fresh-context review (opus, 2026-10-28) found 2 errors and 10 warnings.
What was done about each:

| # | Finding | Severity | Done |
|---|---|---|---|
| E1 | The USB D+/D- pair (and +3V3) runs 50 mm on B.Cu from J1 to the CH340C. That slits the ground plane under both bucks, so the SY8089's input loop (C10/C11) and the TPS54302's C4 close around the slit's end. | error | **Partly fixed.** C10/C11 now sit directly across the SY8089, from IN (pin 4) to GND (pin 2) on F.Cu: the 1.5 MHz hot loop closes on top, a few mm long, and no longer depends on B.Cu. The slit itself stays: there is no room to put the CH340C beside J1, and Freerouting can't be held to F.Cu for a net. **Rev B:** put the CH340C next to J1, or run the USB pair on F.Cu along the left of the buck column. Ripple on +5V/+3V3 is a bring-up measurement (bench item). |
| E2 | TVS D1 sat 17 mm from J1 on a 0.2 mm track. | error | **Fixed.** D1 now sits at Q1's source. Its cathode is inside the F.Cu VBUS pour that joins J1's right VBUS pad to Q1, and its anode has a GND via. |
| W1 | Codec and amp grounds meet only at J1 or below v 61. The amp return drop sits in the single-ended input. | warning | **Not changed** (Rev B: run the INN caps' ground back to the codec). The ES8388's LOUT1/ROUT1 is single-ended, so the drop shows as common-mode across C60/C61 and C62/C63. The firmware worker caps the codec output at -4.5 dB. Hum/noise at the speaker is a bring-up measurement. |
| W2 | AMP_IN_L/R pass 1-1.7 mm from L5/C7 and L6. | warning | **Not changed**; same measurement. Rev B: route them along the top of the codec. |
| W3 | VBUS entry: 0.6 mm stub plus 1.0 mm track, and constraints declare only 0.4/0.8 A. | warning | **Documented.** The receptacle's own pads are 0.6 mm. The 1.0 mm track carries the right pad's half (~2.5 A at 100 W, ~1.6 A at the 65 W design point). The governor holds input to 90 % of the PDO. |
| W4 | Amp OUT traces are 0.31 mm over their full length. | warning | **Documented**; README "Amp OUT runs". Sized for music at full power (1.0 A rms, 30 C). Sine or heavily compressed material at full power would run them hot. The governor caps sustained power at the PDO. Rev B: neck only at the pins. |
| W5 | Ground pour runs 2.2 mm from both antenna ends; H4 is ~8 mm away. | warning | **Not changed.** The keep-out covers the antenna and the module's antenna end overhangs nothing. Wi-Fi range is part of bring-up. |
| W6 | U8's EP vias are open, under 100 % paste. | warning | **Fixed.** The vias are tented both sides, and the paste is 4 windows (~55 %). |
| W7 | Top-actuated buttons and a top LED sit under a lid ~31 mm above. | warning | **By design with the enclosure.** Its worker presses SW1-3 through printed plungers and gives D2 a light pipe; it fitted that to this board. |
| W8 | J4 has no L+/L-/R+/R- silk. | warning | **Fixed**; the silk is added. |
| W9 | J4's wire-entry side is unverified. | warning | **Open item** in the README. The enclosure leaves the top/rear open either way. |
| W10 | architecture/blocks.md is stale. | warning | **Fixed.** |
| triage | The diffpair "open trunk" (12) and route-style jogs (8) warnings come from a summary run without constraints. | - | The gate run uses constraints (`diff_pairs: []`); the jogs are cosmetic. |
