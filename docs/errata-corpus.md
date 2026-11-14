# The real-errata corpus

Public boards whose next revision fixed a defect the first one shipped with,
quoted from the designer's own errata (`reference/errata_corpus.yaml`), and
what pcb-flow's checks do with the first revision. Written by
`bin/pcb-flow errata_corpus table` after `fetch` and `run`.

## digits: v1 -> v2

[https://github.com/justinlindh/digits](https://github.com/justinlindh/digits) at `9807030c8411`, AGPL-3.0; errata `hardware/pcb/v1/ERRATA.md`.

| defect | class | pcb-flow on rev A | follow-up |
|---|---|---|---|
| digits-1: LM2596 switching node Net-(D1-K) routed at 0.25 mm (default class) for 3 A | PI (track width vs current) | caught on rev A, gone on rev B (`check_current` undersized_track) | With no constraints.json, check_current finds U1's OUT pin (an LM2596, reference/switch_nodes.yaml) and screens its net at the part's rated 3 A at dT=60C: 0.25 mm fails (needs 0.72 mm); v2's 0.75 mm passes. |
| digits-2: J1 2x20 Pi header on B.Cu rotated 180 deg, so odd/even columns swap against the Pi below | MECH (mating orientation) | caught on rev A, gone on rev B (`check_mate_pins` mate_mirrored_pinout) | J1 is the stock PinHeader_2x20 (numbered for F.Cu) on B.Cu, so KiCad's flip mirrors it; a mirror survives any rotation, so the 180 deg turn the errata blames is not what swaps the rows. check_mate_pins knows the Pi header by its supply pins (2 +5V, 6 GND) and compares the pattern's handedness, seen from above, with the Pi's face up below. v2 puts J1 on F.Cu for a Pi face down above, which the rule does not judge. |
| digits-3: MIC_GND is a local label, not the GND symbol; the mic return floats | SCH (net connectivity) | caught on rev A, gone on rev B (`netlist_audit` ground_island) |  |
| digits-4: J7 bell terminal on single-pin nets BELL_1/BELL_2, wired to nothing | SCH (net connectivity) | caught on rev A, gone on rev B (`netlist_audit` dangling_net) |  |

## oasis: v0.2 -> mainboard-v0.3

[https://github.com/justbuchanan/oasis](https://github.com/justbuchanan/oasis) at `3462f162f80e`, Apache-2.0; errata `pcb/fabricated-boards/v0.2/errata.md`.

| defect | class | pcb-flow on rev A | follow-up |
|---|---|---|---|
| oasis-1: Q1/Q3 SI2328DS on Q_NMOS_GDS + stock SOT-23-3, so gate and source are swapped | LIB (symbol pin map vs package) | caught on rev A, gone on rev B (`netlist_audit` sot23_pinout) | v0.3 keeps the GDS symbol and moves to lib:SOT-23-3-pinswapped-mosfet, so the rule is quiet there. |
| oasis-2: Rfbb1 BOM part an 0402 resistor (C844504) on an 0603 footprint | DFA (BOM part vs footprint package) | caught on rev A, gone on rev B (`check_bom_sync` bom_package_mismatch) | check_bom_sync --live reads each LCSC number off the board and JLC's package for it; on a pcb-flow fab package stock_check makes the same comparison against BOM.csv's footprint. v0.3 buys C844759, an 0603. |
| oasis-3: MISTER1 connector placed 180 deg off at JLC assembly | DFA (CPL rotation) | missed | none new - dfm's shipped-CPL check catches it on the pinned board (#880) |
| oasis-4: J1 power-input wire holes too big (SolderWire-2.5sqmm, 3.0 mm drill) | DFA (THT hole vs lead) | caught on rev A, gone on rev B (`check_connector_fit` tht_hole_loose) | J1 is a solder-wire footprint with no part to hold it to, so check_connector_fit sizes the wire from the copper feeding the pads: +18V's only feed is a pour's four 0.5 mm thermal spokes, about 5.4 A at a 20 degC rise (IPC-2221), a 0.75 mm^2 wire with a 1.25 mm conductor, which leaves 1.75 mm in the 3.0 mm hole. v0.3 drops the solder-wire pads for a header, and the finding is gone. |

## minuet: v3 -> v4

[https://github.com/brown-studios/minuet](https://github.com/brown-studios/minuet) at `1e43aa3df01b`, MIT; errata `hardware/minuet/v3/design-and-errata.md`.

| defect | class | pcb-flow on rev A | follow-up |
|---|---|---|---|
| minuet-1: no 1 uF capacitor FB_BK to GND_BK, which the MCF8316D datasheet asks for | PI (datasheet-required pin capacitor) | caught on rev A, gone on rev B (`netlist_audit` pin_part_missing) | reference/pin_requirements.yaml carries the MCF8316D's Table 10-1 (SLLSFX9A). v3's +3.3V rail has caps, all drawn on other sheets for other parts, so the finding is a warning that none was drawn for FB_BK; v4 draws C20 1u on /fan/ with the IC and the rule is quiet. |
| minuet-2: LMV331IDCK comparator's input common mode (Vcc-0.7 = 2.6 V) below the 3.1 V rain-sensor signal | ANA (input common-mode range) | caught on rev A, gone on rev B (`netlist_audit` input_common_mode) | netlist_audit solves each input's DC level from the resistors to known rails and compares it with reference/opamp_cm.yaml: U2's - input rests at 3.09 V on the 68K/1M divider and + at 3.3 V (J3 open, dry), both above 2.6 V. v4 drops the comparator, so the rule is quiet there. |
| minuet-3: board areas over 50 C at 4 A per motor phase | TH (copper area under a power IC) | caught on rev A, gone on rev B (`check_thermal` thermal_area) | With no constraints.json check_thermal screens the power ICs reference/thermal_parts.yaml knows (#884): v3's MCF8316D (U5) sheds heat into 2-layer copper that gives 58 C/W against the datasheet's 25.7, so its 135 C thermal warning comes at ~4.2 A peak per phase from 25 C ambient (6.4 A on the datasheet's board). v4's 4-layer pours give U4 42 C/W (1.65x, under the 2x bar) and the finding is gone. |
| minuet-4: ESP32-C3 GPIO6 (MTCK) pulled up by the ROM at reset turns the LED strip on before firmware runs | FW-HW (reset-state pull on a load enable) | caught on rev A, gone on rev B (`check_straps` reset_pull_offboard) | check_straps reads the C3's reset_pulls (MTMS/MTDI/MTCK pull-ups) from reset_states.yaml: v3's GPIO6 leaves the board on the EXPANSION header J4.5 with only the chip's pull-up, so the light accessory's strip sees it high from reset. It also warns on GPIO5 (J4.6), bare the same way. v4 (ESP32-C6) puts R13 100 k to 3V3 on GPIO6, a pull the accessory is designed for, and the finding is gone. On a board whose load is on the board itself, load_on_at_reset names the gate or enable instead. |
| minuet-5: an access-point WiFi connect resets the ESP32-C3 at full TX power | FW (radio power) | not_static |  |
| minuet-6: fan motor connector J8 has no A/B/C phase labels on silk | DFA (connector pin labels) | caught on rev A, gone on rev B (`check_silk` pin_labels_missing) | J8's three pins carry /fan/A, /fan/B and /fan/C; check_silk reads a connector whose nets name three motor phases (A/B/C or U/V/W, bare or after PH/OUT/MOTOR) and looks for silk within 5 mm of its pads that names each one. On J8 only its refdes and two capacitors' are there; v4 labels the phases and the finding is gone. Two-pin power inputs are not judged: which pin is + is a polarity mark, not a pin label. |
| minuet-7: IR receiver U1's 3D model raised 17 mm where the built standoff is 16 mm | MECH (3D model height) | missed | a stated mounting height (constraints or a mech block) for raised THT parts (#980) |

## Count

15 defects: 11 caught, 1 caught_with_constraints, 2 missed, 1 not_static.
