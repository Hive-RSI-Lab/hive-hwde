# Power tree

| Rail | Source | Nominal | Range | Load | Notes |
|---|---|---|---|---|---|
| +3V3 | core, J1 pins 2 + 19 | 3.3 V | 3.2-3.4 V | ~30 mA (LCD logic ~10 mA, U1 < 1 mA, IR 1.5 mA, pull-ups) | C2 10 uF at J1; C1, C4 100 nF at U1, J2 |
| +5V | core, J1 pins 3 + 18 | 5.0 V | 4.75-5.25 V | ~70 mA (backlight 39-61 mA, LED 9 mA) | C3 10 uF at J1 |
| GND | J1 pins 1, 4, 6, 11, 20 + shell pads | 0 V | - | return | B.Cu pour |

Each FFC contact is rated 500 mA; the worst rail carries 70 mA over two
contacts. Dissipation: R13+R17+R18 0.15 W worst (50 mW each), Q1 < 1 mW, total board < 0.5 W.
No regulators.
