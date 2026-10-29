# stm32-blinky - power tree

Single conversion: external 5 V -> series Schottky -> AMS1117-3.3 -> +3V3.
No sequencing, no power-good, no battery. Requirements' rail-budget GUESS
(sec 3) is confirmed below; no research fragment existed to reconcile
(P1 skipped by recorded decision).

```
J1 (ext 5V, 4.5-5.5V)
  |  /VIN            raw, unprotected stub
  D1 SS34            -0.30V @ <=0.1A (reverse input: blocks)
  |  +5V             protected rail, ~4.7V nom
  U2 AMS1117-3.3     dropout ~0.7-0.8V max @ <=0.1A (spec 1.3V max @ 1A)
  |  +3V3
  +-- U1 STM32F103C8T6   ~40 mA design (72 MHz, peripherals on; DS worst ~50)
  +-- D2+R1 user LED     ~1.3 mA (PC13 sink, R1=1k)
  +-- J2 pin 3 (SWD 3V3) 0 design (debugger reference/sense only)
```

## Budgets

| Rail | Nom V | Design load | Envelope | Source limit |
|---|---|---|---|---|
| /VIN, +5V | 5.0 / 4.7 | ~55 mA (load + U2 Iq <=11 mA) | 110 mA | external supply |
| +3V3 | 3.3 | ~42 mA | 100 mA | AMS1117 ~800 mA |

Copper sizing in constraints.json uses current_a = 0.3 on /VIN, +5V, +3V3:
covers the envelope 3x over plus user abuse of the J2 3V3 pin as a small
supply tap; still floor-width traces at 10 C rise, so it costs nothing.

## Headroom (the one number worth checking)

Need: V_LDO_in >= 3.3 + dropout.
- Nominal: 5.0 - 0.30 = 4.70 in; margin 4.70 - 3.3 = 1.40 V >> ~0.8 V max
  dropout at our load. Robust.
- Worst assumed low line: 4.5 - 0.30 = 4.20 in; margin 0.90 V vs ~0.8 V
  estimated max dropout at 100 mA (extrapolated from the 1.3 V @ 1 A spec) -
  closes, thinly, at the abuse envelope; closes solidly at the real ~40 mA
  blinky load. ACCEPTED RESIDUAL RISK: a supply that genuinely sags to 4.5 V
  under load leaves typ-only margin at 100 mA. Escalation if that matters:
  swap D1 to a P-FET (AO3401A class, SOT-23, Basic) for ~30 mV drop - same
  part count, footprint change only. Not taken now: diode is simpler and
  sufficient at the loads this board can actually present.

## Dissipation (why there is no thermal section in constraints.json)

| Part | Worst design case | Notes |
|---|---|---|
| D1 SS34 | 0.30 V x 0.06 A = 18 mW | SMA package, nothing |
| U2 AMS1117 | (4.7 - 3.3) x 0.055 = 77 mW | ~0.42 W only at the 0.3 A abuse level; SOT-223 on top copper handles both (dT ~7 C design, ~35 C abuse) |

No thermal entries, no thermal vias, no planes overrides: the 2-layer default
(B.Cu GND pour) plus normal top copper is ample. check_thermal no-ops on the
absent key by contract.

## Decoupling plan (emitted as decoupling.json by P4, listed here as the budget)

- C1-C3: 100 nF X7R 0603 at U1 VDD pins 48/24/36; C4: 100 nF at VDDA pin 9.
- C5: 10 uF at U2 VOUT (doubles as +3V3 bulk - board is ~35 mm across).
- C6: 10 uF at U2 VIN (+5V rail).
- C9: 100 nF at NRST (local, not a rail decoupler).
- Deviation from ST AN2586 "full" recommendation (4.7 uF dedicated bulk +
  1 uF/10 nF VDDA pair) is deliberate: no analog use on this board, distances
  are tiny, and the identical set is proven routable and gate-clean in the
  blinky2 golden. Add the VDDA pair only if a future spin uses the ADC.
