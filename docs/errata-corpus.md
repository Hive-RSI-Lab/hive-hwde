# The real-errata corpus

Public boards whose next revision fixed a defect the first one shipped with,
quoted from the designer's own errata (`reference/errata_corpus.yaml`), and
what hwde's checks do with the first revision. Written by
`bin/hwde errata_corpus table` after `fetch` and `run`.

## digits: v1 -> v2

[https://github.com/justinlindh/digits](https://github.com/justinlindh/digits) at `9807030c8411`, AGPL-3.0; errata `hardware/pcb/v1/ERRATA.md`.

| defect | class | hwde on rev A | follow-up |
|---|---|---|---|
| digits-1: LM2596 switching node Net-(D1-K) routed at 0.25 mm (default class) for 3 A | PI (track width vs current) | caught on rev A, gone on rev B (`check_current` undersized_track) | With no constraints.json, check_current finds U1's OUT pin (an LM2596, reference/switch_nodes.yaml) and screens its net at the part's rated 3 A at dT=60C: 0.25 mm fails (needs 0.72 mm); v2's 0.75 mm passes. |
| digits-2: J1 2x20 Pi header on B.Cu rotated 180 deg, so odd/even columns swap against the Pi below | MECH (mating orientation) | caught on rev A, gone on rev B (`check_mate_pins` mate_mirrored_pinout) | J1 is the stock PinHeader_2x20 (numbered for F.Cu) on B.Cu, so KiCad's flip mirrors it; a mirror survives any rotation, so the 180 deg turn the errata blames is not what swaps the rows. check_mate_pins knows the Pi header by its supply pins (2 +5V, 6 GND) and compares the pattern's handedness, seen from above, with the Pi's face up below. v2 puts J1 on F.Cu for a Pi face down above, which the rule does not judge. |
| digits-3: MIC_GND is a local label, not the GND symbol; the mic return floats | SCH (net connectivity) | caught on rev A, gone on rev B (`netlist_audit` ground_island) |  |
| digits-4: J7 bell terminal on single-pin nets BELL_1/BELL_2, wired to nothing | SCH (net connectivity) | caught on rev A, gone on rev B (`netlist_audit` dangling_net) |  |

## oasis: v0.2 -> mainboard-v0.3

[https://github.com/justbuchanan/oasis](https://github.com/justbuchanan/oasis) at `3462f162f80e`, Apache-2.0; errata `pcb/fabricated-boards/v0.2/errata.md`.

| defect | class | hwde on rev A | follow-up |
|---|---|---|---|
| oasis-1: Q1/Q3 SI2328DS on Q_NMOS_GDS + stock SOT-23-3, so gate and source are swapped | LIB (symbol pin map vs package) | caught on rev A, gone on rev B (`netlist_audit` sot23_pinout) | v0.3 keeps the GDS symbol and moves to lib:SOT-23-3-pinswapped-mosfet, so the rule is quiet there. |
| oasis-2: Rfbb1 BOM part an 0402 resistor (C844504) on an 0603 footprint | DFA (BOM part vs footprint package) | missed | brief - check_bom_sync compares the LCSC part's package (parts cache / stock_check) with the footprint's size code |
| oasis-3: MISTER1 connector placed 180 deg off at JLC assembly | DFA (CPL rotation) | missed | none new - cpl_verify covers it on an hwde fab package |

## minuet: v3 -> v4

[https://github.com/brown-studios/minuet](https://github.com/brown-studios/minuet) at `1e43aa3df01b`, MIT; errata `hardware/minuet/v3/design-and-errata.md`.

| defect | class | hwde on rev A | follow-up |
|---|---|---|---|
| minuet-1: no 1 uF capacitor FB_BK to GND_BK, which the MCF8316D datasheet asks for | PI (datasheet-required pin capacitor) | caught on rev A, gone on rev B (`netlist_audit` pin_part_missing) | reference/pin_requirements.yaml carries the MCF8316D's Table 10-1 (SLLSFX9A). v3's +3.3V rail has caps, all drawn on other sheets for other parts, so the finding is a warning that none was drawn for FB_BK; v4 draws C20 1u on /fan/ with the IC and the rule is quiet. |
| minuet-2: LMV331IDCK comparator's input common mode (Vcc-0.7 = 2.6 V) below the 3.1 V rain-sensor signal | ANA (input common-mode range) | missed | brief - sim_board or check_ratings checks each op-amp/comparator input's DC level against the part's common-mode range |
| minuet-3: board areas over 50 C at 4 A per motor phase | TH (copper area under a power IC) | caught_with_constraints | check_thermal estimates rise from the power and copper constraints.json gives; on the foreign board it has no input. v4's 4-layer pours are the fix. |
| minuet-4: ESP32-C3 GPIO6 (MTCK) pulled up by the ROM at reset turns the LED strip on before firmware runs | FW-HW (reset-state pull on a load enable) | missed | brief - check_straps reads every MCU pin with a reset-state pull in reset_states.yaml (not only straps) that drives a load enable or a gate, and flags a load that comes up on before firmware |
| minuet-5: an access-point WiFi connect resets the ESP32-C3 at full TX power | FW (radio power) | not_static |  |

## Count

12 defects: 6 caught, 1 caught_with_constraints, 4 missed, 1 not_static.
