# Sheet plan

Single sheet: `root` (no hierarchy). No hier pins.

| Sheet | Blocks | Nets | Refdes |
|---|---|---|---|
| root | B1 J1, B2 C1, B3 R1+D1 | +5V, GND, /LED_A | J1; C1; R1; D1 (all unique); #PWR range 100+ (pwr_base=100) |

Canonical net names: +5V (J1.1, C1.1, R1.1), GND (J1.2, C1.2, D1 cathode), /LED_A (R1.2 - D1 anode).
