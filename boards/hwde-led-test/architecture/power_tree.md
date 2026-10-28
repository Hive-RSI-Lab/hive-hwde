# Power tree

| Rail | Source | Nominal | Range | Load | Notes |
|---|---|---|---|---|---|
| +5V | J1 pin 1 (bench/USB lead) | 5.0 V | 4.75-5.25 V | ~5 mA max (LED branch ~4.3 mA) | no regulation, no reverse protection (LED blocks reverse; stated) |
| GND | J1 pin 2 | 0 V | - | return | |

Dissipation: R1 = I^2 R ~ 12.5 mW (0603 rating 100 mW, ok). Total ~22 mW. Decoupling: C1 4.7 uF at J1. No thermal concerns; no regulator.
