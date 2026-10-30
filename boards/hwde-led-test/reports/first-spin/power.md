# hwde-led-test - first-spin review: power

Cold review, 2026-10-29. Inputs: kicad/hwde-led-test.net, .kicad_pcb, constraints.json,
parts/parts.json, requirements.md, brief/brief.md, vendor datasheets (LCSC, fetched 2026-10-29).
Sims: sims/power/*.cir, run with sim_run.py (ngspice 45) - re-run:
`HWDE_NGSPICE_DLL=<libngspice.so> python sim_run.py --dir reports/first-spin/sims/power`.

Verdict: will light on the first spin. 0 blockers, 0 majors, 2 minors, 3 notes.

## Findings

**POWER-1 (minor, RATE-23) LED current 3.46-5.77 mA, brief asks ~3-5 mA.**
R1 = 470R was picked on VF = 3.1 V taken as typical; KENTO KT-0603G spec A.0 p.3 gives
VF 2.6 min / 3.1 max at 5 mA (no typ), bins +-0.05 V (p.4).
- Low corner: 4.75 V, 474.7R, VF 3.15 V -> 3.46 mA (sim; hand 3.37 mA).
- High corner: 5.25 V, 465.3R, VF 2.55 V -> 5.77 mA (sim; hand 5.76 mA). 5.5 V USB: 6.28 mA.
- Margin: IF 30 mA abs max vs 5.77 mA = 81 %. Brief band exceeded by 15 % at the high corner.
- Fix (optional): R1 510R 0603WAF5100T5E, LCSC C23193 (basic) -> 3.2-5.3 mA. Or justify as is.

**POWER-2 (minor, RATE-03) Reversed input exceeds D1 VR.**
J1 is an unkeyed 2-pin header. Reversed at 5.25 V, D1 sees 5.25 - 5 uA x 474.7R = 5.248 V
reverse vs VR 5 V abs max (spec p.3 t.4): -5 %. The brief's "LED blocks reverse current" holds
only up to 5.0 V. check_ratings ran and only said D1/R1 are unrated.
- Contingency: 1N4148WS (C2128, basic, SOD-323) anti-parallel across D1 (A to GND, K to /LED_A),
  fitted or DNP. Reversed: D1 clamped at ~0.75 V, R1 (5.25-0.75)/465.3 = 9.7 mA, 44 mW of 100 mW.

**POWER-3 (note, PROT-04) Overvoltage.** No OVP (brief accepts). First limit is R1 100 mW at
Vin = 2.6 + sqrt(0.1 x 465.3) = 9.4 V. 12 V adapter: R1 190 mW (1.9x), D1 20 mA (< 30 mA).
Headroom from 5.25 V: 79 %.

**POWER-4 (note, PROT-07) Hot-plug ringing.** sims/power/hotplug.cir: live 5.25 V step through
1 uH + 0.1 ohm lead: 8.91 V peak with C1 = 4.7 uF, 9.26 V with C1 = 2.6 uF (DC-bias derated).
C1 16 V: 42 % margin. LED peak 14.1 mA vs IFP 60 mA: 77 %.

**POWER-5 (note, TEST-01, caught_by check_observability).** check_observability: +5V blind.
J1's posts are the probe point; D1 is the indicator. Bring-up reading: 3.4-5.8 mA into J1
at 5.0 V (nominal ~4.6 mA), D1 lit. 0 mA = D1 reversed/open; >> 6 mA = short (C1/J1).

## Blocks passed (with margins)

| Block | Rating | Worst case | Margin |
|---|---|---|---|
| C1 voltage | 16 V (code O, Samsung catalog) | 5.25 V (9.26 V hot-plug) | 67 % (42 %) |
| C1 capacitance | brief >= 1 uF | ~2.6 uF at 5 V (assumed DC bias, unverified) | 2.6x |
| R1 power | 100 mW (UNI-ROYAL 0603 1/10 W) | 15.5 mW (sim, high corner) | 85 % |
| R1 voltage | 75 V | 2.7 V | 96 % |
| D1 IF | 30 mA | 5.77 mA (6.28 mA at 5.5 V) | 81 % (79 %) |
| D1 Pd | 100 mW | 5.77 mA x 2.57 V = 14.8 mW | 85 % |
| J1 contact | 3 A (XFCN A1) | 5.8 mA | > 99 % |
| +5V track | 0.2 mm, required 0.005 mm at 10 mA (check_current) | 5.8 mA | 40x |
| Reverse, C1 | non-polar MLCC | -5.25 V | n/a |

Thermal is trivial (15 mW per part). Tools run: check_current (pass), check_ratings
(1 warning: D1/R1 unrated - blind to POWER-2), check_observability (POWER-5), sim_run.

## Known unknowns / tools wanted
See power.json (9 facts; none could_stop_board left unverified).
