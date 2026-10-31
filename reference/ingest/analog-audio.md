# Analog and audio: what was read, what held up, what hwde does about it

EE ingestion run, domain analog and audio (task 261031-011721-ad1cf4e572,
issue #302), 2026-10-31. It covers op-amp circuits and stability, class-D
output stages, DAC/ADC front ends, grounding, noise, THD measurement, and
line-level and speaker interfaces.

Every rule below has a status:

- **verified**: reproduced by a script here (`hwde analog_verify`, where
  ngspice and the calculation agree), or stated by two independent sources.
- **conditional**: true only under conditions the folk version drops. The
  tool encodes the formula, not the folk constant.
- **disputed**: the sources disagree.
- **folklore**: widely repeated with no basis that held up.

A source is marked [read] when its text was fetched and read in this run.
It is marked [summary] when only a search-result summary was seen; those
need reading before a check rests on them alone. analog.com timed out on
every fetch in this run, so the ADI items are summary-level.

## What hwde gained

| Tool | What it does | Proof |
|---|---|---|
| `hwde analog_calc noise` | Input-referred noise of a stage: en, in·Rs, in·(Rf‖Rg) and the thermal noise of Rs and Rf‖Rg, summed as powers, with the dominant term named and the noise figure. | ngspice `.noise` on 1 kΩ: 4.0578 nV/√Hz on both sides |
| `analog_calc level`, `bridge`, `damping` | dBu/dBV/Vrms, bridging loss, and the response tilt a source impedance gives a load whose impedance swings Zmin..Zmax (what the "1/8 rule" approximates) | arithmetic; test_analog_calc |
| `analog_calc hpf`, `bias` | A coupling cap's corner with source and load in series, stacked corners, and the drift rate of an input with no DC path (Ib/C) | ngspice AC: 1.505 Hz sim vs 1.5015 Hz calc |
| `analog_calc fpbw` | Full-power bandwidth SR/(2π·Vpk), or the slew a level needs | derivation |
| `analog_calc riso` | Phase margin of an op-amp driving a cap load: GBW, open-loop Ro, Riso, noise gain, optional second pole. The crossover is solved, not assumed. Also gives the Riso for a target PM. | ngspice loop gain, 5 cases within 0.2°; step overshoot exact at Riso 0 |
| `analog_calc lc` | Class-D LC filter, BTL or SE: f0, Q, peak, loss at 20 kHz, attenuation at fsw, and the peak with the load doubled and with it gone. Or L and C for a target f0 and Q. | ngspice AC on a BTL pair: peak, 20 kHz and fsw within 0.05 dB / 0.2 dB |
| `analog_calc classd` | Output power at clipping and at 10 % THD from PVDD, load, Rds(on) and DCR | clip-ratio derivation below |
| `analog_calc sar` | SAR charge-bucket RC: kickback, the residual after t_acq in LSB, and the time and number of τ to ½ LSB | ngspice transient of the charge share, within 1 % |
| `hwde analog_verify` | Re-runs every case above against KiCad's ngspice, plus two engine facts. Exit 1 if any disagree. | itself; tests/test_analog_calc.py runs it |
| netlist_audit `opamp_input_no_dc_path` (error) | An op-amp, comparator or in-amp input whose net reaches only capacitors, diodes, test points and other inputs through R/L/bead/jumper chains: no path for bias current. | tests/test_amp_dc_path.py; the hifi core board's 6 amp inputs pass |
| netlist_audit `opamp_input_dc_via_connector_only` (warning) | The input's only DC path is a connector pin, so it floats with the cable out. | same |
| netlist_audit `polarized_cap_reverse` (warning) | An electrolytic or tantalum whose + side rests more than 0.5 V below its − side, by the same rail/resistor solve `input_common_mode` uses | same; core's 470 µF PVDD caps pass once PVDD's volts are declared |

## Engine facts (KiCad 10.0.6's ngspice, rechecked by `analog_verify`)

1. **`x^3` and `pow(x,3)` in a B-source drop the sign of a negative x.**
   With x = −1 both return +1. `x*x*x` and `pwr(x,3)` return −1. A
   nonlinearity written as `v(a)+k*v(a)^3` therefore produces even
   harmonics and DC instead of a third harmonic. In this run it reported a
   THD of 0.515 % where the analytic answer is 0.248 %. **verified**
   (engine_facts case). Write odd powers as products, or use `pwr`.
2. **A linear circuit shows false THD without a max timestep.** An RLC
   driven by a pure sine, `.tran 10u 10m` with reltol 1e-6, reports a
   THD of 0.0023 %. That is as much as a good audio stage really has.
   With a max step of period/1000 the floor is 2e-11 %. **verified**.
   ngspice's `fourier` uses only the last period on a 200-point grid by
   default (ngspice manual [summary]). Set a max step, let the circuit
   settle first, and confirm the THD doesn't move when the step halves.

## Rules: op-amps

| # | Rule | Status | How |
|---|---|---|---|
| A1 | Every op-amp/in-amp input needs a DC return path for its bias current. Without one it drifts to a rail at Ib/C (10 nA into 1 µF is 10 mV/s). | **verified** | ADI AN-937 and Analog Dialogue "Avoiding common problems when designing amplifier circuits" (Kitchin) [summary]; hwde's own in-amp records (in-bias-current-return-path); topology check built |
| A2 | Cap load: the load pole 1/(2π(Ro+Riso)CL) erodes phase margin, and Riso adds a zero 1/(2π·Riso·CL) that restores it. | **verified** | ngspice loop gain against `cap_load_pm`; ADI AAE-25/AAE-32, TI "Three ways to stabilize op amp capacitive loads" [summary] |
| A3 | "PM 45° → about 23 % overshoot; 60° → about 9 %" | **conditional** | Exact for a two-pole loop (ngspice step: 39.7 % at PM 31° both ways). With Riso the zero adds overshoot (17 % measured where the formula says 12 %), so treat the estimate as a floor. |
| A4 | Raising the noise gain stabilizes a cap load with no Riso | **verified** | NG 4 takes 1 nF/50 Ω from 31° to 57° (ngspice); ADI AAE-25 [summary] |
| A5 | Decompensated op-amps need noise gain ≥ the datasheet's minimum stable gain; it is the noise gain, not the signal gain, that counts | **verified** (two sources) | ADI MT-033, EDN/TI (Trump) [summary]; follow-up 1 |
| A6 | Line outputs need a 47–100 Ω build-out resistor against cable capacitance | **conditional** | Practitioner sources only (ESP, GroupDIY) [summary]. A2's calc decides it for a given op-amp and cable C; follow-up 2. |
| A7 | Full-power bandwidth = SR/(2π·Vpk); NE5532 at 9 V/µs and 10 Vpk reaches 143 kHz | **verified** | derivation |
| A8 | "Use a low-noise op-amp" | **conditional** | `noise` shows a 1 nV/√Hz part behind 10 kΩ gives a noise figure of 0.03 dB: the source's 12.8 nV/√Hz rules. Pick en against Rs. |

## Rules: class-D

| # | Rule | Status | How |
|---|---|---|---|
| D1 | BTL LC filter: per leg L, C to ground. Differentially it is 2L with C/2 (+ C across the load) into RL; Butterworth Q = 0.707. | **verified** | ngspice AC; TI SLOA119B [read] table 1 (8 Ω: 33 µH, 0.47 µF, 0.1 µF gives f0 27.2 kHz, Q 0.71) |
| D2 | SLOA119B calls Q = 0.707 "critically damped" | **folklore** (wording error) | Critically damped is Q = 0.5; Butterworth is maximally flat. Encode the formula, not the prose. |
| D3 | TI's 8 Ω Butterworth filter is flat across audio | **disputed** | It is −1.09 dB at 20 kHz into a resistive 8 Ω (calc and ngspice). Whether that matters is the designer's call; `lc` reports it. |
| D4 | Q depends on the load. A speaker's impedance at f0 is well above nominal (voice-coil L), and with no speaker the filter rings. | **verified** | 10 µH/0.68 µF: 1.5 dB peak at 8 Ω, +3.6 dB more at 16 Ω, 37 dB with the load open and 50 mΩ DCR (ngspice 37.5 dB). Zobel or damping is a design question; follow-up 3. |
| D5 | Output power at clip = Vpk²/2RL with Vpk = PVDD·RL/(RL + 2(Rds + DCR)) for BTL | **verified** | derivation |
| D6 | "10 % THD power is about 25 % above 1 % THD power" | **verified** (close) | A hard-clipped sine gives 1.22 (test_classd_power_and_the_10pct_ratio). A real amp's soft clip differs, so `classd` uses 1.22. |
| D7 | Filterless outputs with a ferrite bead + 220 pF–1 nF per leg, near the IC | **verified** (two sources) | TI SLAA903 [read], MAX98357 EV kit and TPA3137D2 datasheet [summary] |
| D8 | Max speaker wire for a filterless output | **disputed** | 10 cm (SSM2335), 20 cm (TPA2000D2), 30 cm (MAX98357), 1 m with a bead (TI SLOA216) [summary]. It is per part, never a global constant. |
| D9 | SE half-bridge class-D pumps its supply and has about 0 dB open-loop PSRR; BTL cancels the pumping | **verified** (two sources) | Infineon AN-1071, Analog Dialogue 40-06 [summary] |
| D10 | Pop/click: match the INP/INN input caps and source impedances; keep the input RC within the part's power-up window | **conditional** | TPA3110D2 datasheet, TI E2E [summary]: numbers per part; follow-up 4 |

## Rules: converter front ends

| # | Rule | Status | How |
|---|---|---|---|
| C1 | "Charge-bucket Cfilt ≥ 20 × Csample" | **conditional** | TI SBOA443 [read] derives 20 from a 4 V range and a 100 mV droop budget. It bounds the kickback to 1/21 of full scale; it does not make the RC settle. 100 Ω/800 pF/40 pF at 300 ns leaves a 12-bit SAR 5.5 LSB off, and 10 nF (400×) is worse (89 LSB at 18 bits). Both were checked in ngspice. |
| C2 | Settling to ½ LSB takes ln(step·2^(N+1)) τ (11.8 τ for 16 bits full scale) | **verified** | derivation; TI Precision Labs "about 12 τ" [summary] |
| C3 | The charge-bucket cap should be C0G/NP0 | **verified** (two sources) | Baker (TI), TI Precision Labs [summary]; follow-up 5 |
| C4 | The charge-bucket RC is not the anti-alias filter | **verified** | its corner is MHz (`sar` gives filter_fc_hz); SBOA443 [read] |
| C5 | Differential ΔΣ input: common-mode caps about Cdiff/10, matched | **verified** (two sources) | TI Precision Labs, Microchip AN1607 [summary] |
| C6 | DAC sin(x)/x droop is 3.92 dB at Nyquist | **verified** | ADI MT-017 [summary]; 20log(π/2) = 3.92 |

## Rules: grounding, levels, interfaces

| # | Rule | Status | How |
|---|---|---|---|
| G1 | One solid ground plane, partitioned by placement; no trace crosses a split | **verified** (two sources) | Ott, "Split ground planes" [read]; ADI MT-031, TI SLYT499 [summary]. hwde's check_return_path already covers plane crossings. |
| G2 | "Always split AGND and DGND and join them at a star point" | **disputed** | MT-031 [summary]: tie a converter's AGND/DGND at the chip to one plane when its digital current is low. Some datasheets (AD7656) ask for separate planes. Ott [read]: one plane removes the question. Defer to the part's datasheet. |
| L1 | +4 dBu = 1.228 V, −10 dBV = 0.316 V; they are 11.8 dB apart, not 14 | **verified** | arithmetic |
| L2 | Line interfaces need 600 Ω matching | **folklore** outside telecom/broadcast | Modern practice is bridging: Zout 50–600 Ω into Zin ≥ 10 kΩ (Rane, SVC [summary]); 600 Ω into 10 kΩ loses 0.5 dB. |
| L3 | Balanced-input CMRR depends on the input's common-mode impedance. With a typical Zcm of 10–50 kΩ, a few ohms of source imbalance cost tens of dB. | **verified** (two sources) | Whitlock (ProSoundWeb) [read], THAT 1200 datasheet [summary]; follow-up 6 |
| L4 | Headphone Zout ≤ Zload/8 | **conditional** | It is a 1 dB criterion against a load that rises far above its minimum: 20log(9/8) = 1.02 dB. NwAvGuy [read] calls the 8 "somewhat convenient"; IEC 61938 recommends 120 Ω [summary]. Use `damping` with the load's real Zmin/Zmax. |
| L5 | Speaker cable round-trip R ≤ 5 % of nominal Z | **folklore** (a convention) | Roger Russell [summary] calls it conservative. `bridge` gives the actual loss. |
| L6 | Electrolytics: + faces the higher DC node; no sustained reverse bias | **verified** | maker application guides (Nichicon, KEMET) [summary]; check built |
| L7 | Electrolytic coupling caps distort when signal voltage appears across them; the cure is a bigger cap (corner about 10× lower), not a DC bias | **verified** (one author, measured) / **disputed** for tantalum | Self, "Distortion in power amplifiers" and audioXpress [summary] |

## Gap list, ranked (for the lead to file as in-flight work drops)

Filed as briefs: 1 (task 261031-013846-8ebea62ba7), 2 (261031-013847-69dd276ae7), 3 (261031-013848-d3dc117530).

1. **Decompensated op-amp at too low a noise gain** (A5). Add a
   `min_stable_gain` field to opamp_cm.yaml rows (or a sibling table), and a
   netlist_audit rule that computes noise gain from the feedback network
   (Rf/Rg around the − input) and flags a follower or a G = 1 stage built
   with a G ≥ 5 part. Board case it must catch: an OPA847 or AD849 follower.
2. **Op-amp output into a cable or big capacitor with no build-out
   resistor** (A2, A6). From the netlist, flag an op-amp output that
   reaches a connector or a capacitor > 100 pF with no series R. Give
   `analog_calc riso` the op-amp's GBW and Ro from a part table and report
   the PM. Board case: a line-out jack driven straight from an NE5532 into
   a 1 nF (10 m) cable model.
3. **Class-D output-filter review in sim_board** (D1, D3, D4). Find the
   LC on each class-D output (inductor from an OUTx pin, cap to ground or
   across), run `classd_lc` for the declared speaker impedance, and report
   Q, the 20 kHz loss, the open-load peak against the cap's voltage rating,
   and the inductor's Isat against the amp's current limit. Board case: the
   hifi-receiver-2 core's TPA3116 output filter.
4. Class-D INP/INN symmetry: matched input caps and resistors on both legs,
   input RC within the part's start-up window (D10). It catches a 1 µF/
   0.47 µF mismatch that pops.
5. Dielectric of the cap on an ADC input: X7R/X5R where C0G is needed (C3).
   It needs parts.json dielectric data.
6. Balanced input CMRR from Zcm and resistor tolerance: a calc and a
   check on difference-amp receivers (L3).
7. `analog_calc` THD of a board's own stage through ngspice with the step
   rules (Engine facts 2) built in: sim_board's audio stage gets a THD and
   noise figure, not only DC and AC.
8. A sim deck lint for testbenches: `^` on signals, no max step on a
   `.four`/`fourier` run.
9. Polarized cap DC level through op-amp outputs: the solve stops at any
   driven pin. Use sim_board's .op voltages so a coupling cap after an
   op-amp at mid-rail gets checked too.
10. Speaker cable and headphone damping figures in the design doc's audio
    section, from `bridge`/`damping`.

## Open-source tools looked at (ideas, not dependencies)

ngspice (`.noise`, `fourier`, `.meas`) is in use here. PySpice/InSpice is
already hwde's binding. Zero/LISO (op-amp noise with a part library) is the
model for follow-up 7. Lcapy gives closed-form f0/Q. scikit-rf could model
bead + cap resonance (D7). endolith/waveform_analysis is a THD+N reference
with a flattop window. JYKlabs Audio-Measurement-Tools covers AES17's
22 kHz band. SKiDL ERC and kicad-happy are prior art for topology checks
like A1. KiCad custom DRC rules could express G1.
