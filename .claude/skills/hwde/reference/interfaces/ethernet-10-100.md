# Canonical interface fragment: 10/100BASE-TX Ethernet MDI (magjack)

Machine-readable half: `ethernet-10-100.json` (exact constraints_schema
shapes). Seeded T6 (2026-08-06) from
`boards/lumina-carrier/research/interface-ethernet.{json,md}` - a P1 fragment
opus-verified against primary sources. The full worked derivation (W5500 +
ESP32-S3 + PoE-PD specifics) stays in that file; this copy keeps the
CLASS-level canon for any 10/100 PHY behind an integrated-magnetics RJ45.

HOW TO USE (research-interface-spec agents): START here. Validate-and-adapt:
(1) adapt net names to the sheet plan, (2) resolve stackup-dependent keys
(gap_mm) after P2 picks the stackup, (3) re-resolve PHY-specific rows (host
bus limits, support components, auto-MDIX, magnetics table) against YOUR
PHY's datasheet, (4) if the board is a PoE PD, apply the PoE rows and
coordinate with the power-side agent, (5) mark every delta in your md.

## Sources

| Tag | Document |
|-----|----------|
| 802.3 | IEEE 802.3 clause 24/25 numbers as tested by UNH-IOL clause 25 suite (ANSI X3.263-1995) |
| WIZnet | WIZnet hardware design guide (W5500 class; MDI length/spacing/via rules) |
| SNLA079D | TI "10/100 PHY layout" app note (impedance, magnetics envelope, Bob-Smith, chassis moat) |
| Pulse | Pulse magnetics layout note (edge distance, ICM plane rules, 15 mil dielectric) |
| AN956 | Skyworks AN956 (PoE PD reference wiring, ESD/EMI network) |

## The numbers that bind layout

| Quantity | Value | Source |
|---|---|---|
| Differential impedance | 100 ohm (SE <= 50 ohm to GND); return-loss floor 10 dB = ~100 +/-45 | WIZnet, SNLA079D 2.1, ANSI X3.263 9.1.5 |
| Edge rate | 3.0-5.0 ns (10-90%), symmetry <= 0.5 ns -> `t_rise_ns: 3.0` conservative | ANSI X3.263 9.1.6 |
| Transmit clock | 125 MHz +/-50 ppm TOTAL (crystal budget must SUM inside it) | 802.3 24.2.3.4 |
| Differential output | 950-1050 mV, symmetry 98-102% | ANSI X3.263 9.1.4 |
| PHY-to-magnetics run | <= 25 mm recommended / 75 mm abs max; >= 25 mm from board edges | WIZnet; Pulse |
| Pair-to-pair / to-other-signal | >= 0.508 mm TX-RX (0.762 preferred) / >= 7.5 mm non-MDI | WIZnet W/K; Pulse |
| Vias on MDI | 0 target (WIZnet prohibits; Pulse allows max 2) | WIZnet; Pulse |
| Magnetics | 1:1 +/-2%, >= 350 uH OCL, IL -1 dB, RL -16 dB, isolation 1500 Vrms | SNLA079D Table 4 |
| MDI ESD array | PHY side, at the magjack end, <= 1 pF/line | Semtech guidance (carrier fragment) |

## Judgment calls carried by the JSON (re-verify, then reuse)

- **`gap_mm` dropped (stackup-dependent)**: pitch = width + gap of the chosen
  diff_100 profile (0.47 on JLC04161H-3313); rules_gen recomputes geometry
  from `impedance_ohm` anyway.
- **`impedance_ohm` omitted from high_speed on purpose**: explicit diff_pairs
  entries win in rules_gen; a single-ended 50 there would conflict.
- **`max_skew_mm: 2.5` ASSUMED**: no clause number exists; physics allows
  ~45 mm - the bar is EMI hygiene, not signal validity.
- **`max_uncoupled_mm: 5.0`**: THT magjack pin grid (~2 mm) makes the
  connector fan-out inherently uncoupled for 1-2 mm/leg; tighter values
  false-positive on correct layout.
- **4 layers effectively required** for a real 100 ohm target (no reference
  plane on 2-layer; SNLA079D s8, AN956 s8).

## Structural rules (the expensive-to-relearn part)

1. **Plane void under the magjack**, all layers, from the PHY-side pad row
   outward to the board edge; separate chassis island for shield tabs only.
   FR-4 between adjacent layers is not a 1500 Vrms barrier (~8 mil prepreg
   vs Pulse's >= 15 mil) - void BOTH inner layers.
2. **Pipeline trap**: check_return_path errors (no waiver) on >= 0.01 mm of
   pair crossing missing reference copper - the void must start AT the pad
   row, and planes_gen can only express voids by sizing positive region
   rects. Decide the geometry at P5.
3. **PoE PD boards**: magjack MUST pin out both centre taps + both spare
   pairs (hard P3 filter - common magjacks physically cannot feed a PD), and
   Bob-Smith's DC half must NOT be fitted on powered taps (TI: "does not
   apply for PoE"); keep only the AC/EMI half per AN956.
4. **Chassis vs signal ground** is an enclosure question, not a layout
   default: floating PD + plastic box may have nothing to bridge to. Default
   strap: 1 M || 1 nF (>= 2 kV) with a fitted 0 ohm alternative.
5. **Crystal budget**: spec initial AND temperature tolerance so the sum
   (+ aging + load-cap error) stays inside +/-50 ppm; compute load caps from
   the crystal's stated CL, never copy the habitual 22 pF.

## What stays per-run (do not generalize from here)

PHY support components (bias resistors, core caps, strap pins), host-bus
(SPI/RMII) speed limits and pin legality, LED wiring polarity, auto-MDIX
presence, PoE power-side networks. See the carrier fragment for the worked
W5500 + PoE example of each.
