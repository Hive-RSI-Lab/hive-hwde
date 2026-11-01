# EE ingestion: EMC, ESD and thermal

One of the domain notes from the 2026-10-31 ingestion run (task
261031-011721-e8a4a99307, issue #304). The person's ask: cast a wide net over
free EE sources, keep what is true, and turn it into hwde tests and tools.
"Dont blindly trust everything u find out there."

How to read it: each rule has an id and a status.

- **verified**: reproduced by `emc-thermal/verify.py` (arithmetic, a closed
  form, or ngspice), or stated by two independent sources that agree.
  `tests/test_ingest_emc_thermal.py` keeps the reproduction running.
- **disputed**: sources disagree, or our reproduction disagrees with the
  source. What disagrees is written next to it.
- **folklore**: widely repeated, with no derivation, measurement or second
  source behind it. Often measured to be wrong. hwde does not check it.
- **judgement**: the direction is verified but the number is a choice (a
  threshold no source fixes). A check using one says it is a judgement.

Run `verify.py` with the venv python (`eval "$(bin/hwde env)"` first for
`--spice`, which needs KiCad's ngspice). It prints every number below.

## Sources read

All of them are free to read: vendor app notes, standards summaries,
university and author pages, and open tools. No pirated books.

| Source | What it gave | Kind |
|---|---|---|
| TI SLVA680A, *ESD Protection Layout Guide* (Yater, 2015/2022), ti.com/lit/pdf/slva680 | L1-L4 model of a clamp's line, via rules, ground rules, the corner claim | vendor app note |
| TI SLVAEX9, *ESD Packaging and Layout Guide* | L4 >> L1, clamp as close as rules allow | vendor app note |
| TI SLVA796 (TPS254x USB) | TVS at the connector, no vias, ±8 kV contact reached | vendor app note |
| TI SLVAF82, *ESD and Surge Protection for USB* | USB 2.0 HS clamp < 4 pF; 5 Gbps < 0.5 pF; 10 Gbps < 0.3 pF | vendor app note |
| Nexperia AN10753, *ESD protection for USB 2.0* | the 10 pF HS budget: 5 pF transceiver, 5 pF connector + board + clamp | vendor app note |
| onsemi AND8082 | USB 2.0 clamp < 5 pF line-line | vendor app note |
| Würth ANP002 (USB 2.0 protection) | < 5 pF is fine, their arrays < 2 pF | vendor app note |
| Toshiba e-learning, TVS layout ch. 5-1 | clamp near the entry point, low series L | vendor course |
| IEC 61000-4-2:2008 Table 3 (via the ITU-hosted copy and In Compliance Magazine) | 3.75 A/kV, 0.8 ns, 2 A/kV at 30 ns, 1 A/kV at 60 ns | standard |
| LearnEMC (Hubing): QotW 2023-12-25, 2021-06-28, 2020-05-25, 2024-07-08; *Some of the Worst EMC Design Guidelines* | the debunked list; 5 µA via 1 nW; 0.4 mV CM; AGND/DGND | university / author |
| Shim & Hubing, *20-H Rule Modeling and Measurements* (Clemson CVEL, EMCS01-939) | +3.6 dB from following 20-H | peer-reviewed paper |
| Henry Ott, APEC 2002 slides and tech tips (hott.shielddigitaldesign.com) | DM / CM emission forms; 5 µA class B, 15 µA class A; one solid plane | author |
| Bogatin, EDN *Rule of Thumb #31* | 5 µA on 1 m at 100 MHz, 100 µV/m at 3 m | author |
| ADI AN-1368 / Analog Dialogue 50-02, *Ferrite Beads Demystified* | DC bias collapses bead Z; 20 % of rated current; LC peaking | vendor app note |
| TI SPRA953D, *Semiconductor and IC Package Thermal Metrics* | θJA is a test-board figure; ΨJT 0.5-2 °C/W; Tj = Ttop + ΨJT·P | vendor app note |
| TI SNVA419C (thermal vias) | 12 mil, 0.5 oz, 1.65 mm via: 261 °C/W | vendor app note |
| Würth, *Top-to-bottom heat dissipation* (we-online.com) | 0.6 mm via: 64 / 42 (solder) / 34 (70 µm) / 14 (filled) K/W | vendor app note |
| kicad-happy `emc-precompliance.md` (github.com/aklofas/kicad-happy) | 44 EMC rule ids, the formulas, scoring | open-source tool |
| Wikipedia *Via fence*, Altium docs, JLCPCB blog, AtlasPCB | λ/20 and λ/10 stitching stated, never derived | secondary |
| PCBSync, Aivon, bestpcbs, winwinchip blogs | numbers checked below; several wrong | blogs (corroboration only) |

## ESD

| id | Rule | Status | Evidence |
|---|---|---|---|
| ESD-1 | An IEC 61000-4-2 8 kV contact strike rises to 30 A in 0.8 ns: dI/dt ≈ 3.75e10 A/s (TI rounds to 4e10) | **verified** | IEC Table 3; `esd_di_dt` |
| ESD-2 | A PCB trace is ~1 nH/mm | **verified** as partial self-inductance (Grover: 9.9 nH for 10 mm × 0.2 mm). **Disputed** as loop inductance: over a solid plane a 50 Ω line is ~0.3 nH/mm | `trace_partial_l`, `microstrip_loop_l_per_m` |
| ESD-3 | A 1.6 mm through via is ~1.2-1.3 nH | **verified** (Johnson & Graham's formula) | `via_l` |
| ESD-4 | Every nH between the clamp and ground (L3), or on a branch the strike shares with the IC (L2), adds L·dI/dt at the IC: 0.25 nH → ~10 V at 8 kV | **verified** by ngspice: a zener clamp with 0.25, 1 and 3 nH in its ground over-shoots by exactly L × slope (7.5 / 30 / 90 V at 3e10 A/s) | `esd_spice`; SLVA680 §2.4 |
| ESD-5 | A clamp on every line that leaves the product, placed first on the line, with no via and no branch before it | **verified** (SLVA680 §2.1-2.3, SLVAEX9, SLVA796, Toshiba: four documents, two vendors) | now `scripts/check_esd.py` |
| ESD-6 | One ground via right at the clamp's ground pad | **verified** (SLVA680 §2.4, and ESD-4 sets the cost of not doing it) | `check_esd` `esd_ground_far` |
| ESD-7 | Clamp within 10 mm of the connector pin | **judgement**. Only one blog (PCBSync) gives a number. TI says "as close as design rules allow" | `check_esd.FAR_MM`, labelled a judgement |
| ESD-8 | "A 90° corner on the line arcs: field ≥ 7 kV, arcs for radius < 2.6 mm" (SLVA680 §2.2) | **disputed**. The figure mixes volts and field. 2.6 mm is about the uniform-field air gap that holds 7 kV at ~3 kV/mm (2.3 mm), which is a spacing between conductors, not a corner property. Once the clamp conducts the line sits at tens of volts. LearnEMC and Bogatin measure no emission difference between corner styles | `air_gap_for_kv`; LearnEMC QotW 2023-12-25 |
| ESD-9 | Keep unprotected traces out of the region between the connector and the clamp (SLVA680 §2.2) | plausible, **single source**; follows from ESD-4's dI/dt | `check_esd` `esd_corridor_coupled` (#494) |
| ESD-10 | A USB 2.0 HS clamp is ≤ ~4-5 pF per line (TI < 4, onsemi < 5, Nexperia's 5 pF board budget; parts ~1 pF are usual) | **verified** (three vendors on the same spec budget). One blog's "5-10 pF" is looser than all three: folklore | gap G2 |

### check_esd on the pinned boards (run by hand, not scored)

`bin/hwde check_esd --pcb pinned/<b>/kicad/<b>.kicad_pcb`:

- **lite-main**: USB-C J1 D+/D- (`/USB_DP`, `/USB_DM`) run straight to the
  CH340B (U6) with **no ESD clamp**. CC1/CC2 run to the CH224A with none
  either. Only VBUS has its SMBJ20A. Line-in, sub-out and speaker terminals
  are unclamped too.
- **core**: the USBLC6-2SC6 (U2) is on USB D+/D-, but **22.5 mm and
  18.7 mm of track** from the connector pins, and D- has a 1.4 mm stub
  (~53 V of estimated overshoot at 8 kV). CC1/CC2 are unclamped. The FFC to
  the front board (J3) is skipped as internal.
- **ultra-front**: its two connectors are the FFC/LCD side (internal; give
  them Values naming FFC, or `esd.internal`, and they drop out).

These are told to lead:hifi-receiver-2. Whether audio terminals need clamps
is the board owner's call (`esd.exempt_nets`). USB data lines and CC pins
are not optional by any of the sources above.

## EMC

| id | Rule | Status | Evidence |
|---|---|---|---|
| EMC-1 | Differential-mode (small loop) emission: E = 131.6e-16 f² A I / r (×2 = 263e-16 over a reflecting ground). So E scales with f², area and current, and halving the loop area gains 6 dB | **verified** from the small-loop far field η β² A I / (4π r) | `dm_loop_e` |
| EMC-2 | Common-mode emission: E = 1.257e-6 f L I / r. About 5 µA of CM current on a 1 m cable fails FCC class B | **verified** two ways: the short-wire formula gives 4.8 µA (100 µV/m, no reflection) or 3.6 µA (150 µV/m band, with reflection), and Hubing's resonant-dipole power argument gives 5.0 µA independently | `cm_current_at_limit`, `resonant_dipole_current_for` |
| EMC-2b | FCC 15.109 class B at 3 m ≈ CISPR 32 class B at 10 m, after the 10.5 dB distance shift | **verified** (40.0 vs 40.5 dBµV/m below 88 MHz) | test |
| EMC-3 | A trapezoid's spectrum: flat to 1/(πτ), −20 dB/dec to 1/(π tr), −40 dB/dec after. So slow the edge to cut the high harmonics | **verified** by FFT against Paul's envelope (every harmonic under it, −39 dB per decade past the second corner) | `trapezoid_harmonics` |
| EMC-4 | Plane-pair cavity resonance f = c/(2√εr)·√((m/a)²+(n/b)²): 100 × 80 mm FR4 → 715 MHz | **verified** (matches kicad-happy's EE-001 number) | `cavity_resonance` |
| EMC-5 | Stitch / fence vias at λ/20 | **verified as a convention, folklore as a cutoff**. Ott's aperture estimate SE = 20 log(λ/2L) gives exactly 20 dB at λ/20 and 14 dB at λ/10. Both λ/10 and λ/20 circulate (Altium cites both). Neither is a threshold: SE falls smoothly with gap size | `slot_se_db` |
| EMC-6 | Solid, unsplit ground plane: partition the parts, don't gap the plane; bond chassis to ground at the I/O edge | **verified** (LearnEMC's worst-guidelines page and QotW 2024-07-08, Ott's tech tip, TI SLYT512. The older ADI *Staying Well Grounded* split advice is the outlier) | partly in `check_return_path` (high-speed nets only); gap G5 |
| EMC-7 | A ferrite bead loses most of its impedance under DC bias (TDK MPZ1608S101A: 100 → 10 Ω at 100 MHz at 50 % of rated current), and a bead into a low-ESR cap is a resonant LC that amplifies noise at f0 | **verified**: the DC-bias figure is from vendor curves (ADI AN-1368 quoting TDK and Würth). The peaking is reproduced (1 µH, 10 µF, 16 mΩ: 50 kHz, +26 dB). "Use beads at ≤ 20 % of rated current" is ADI's **judgement** | `lc_filter_peak_db`; gap G3 |
| EMC-8 | 20-H rule (pull the power plane back 20 × spacing) | **folklore**: measured to raise emissions 3.6 dB (Shim & Hubing), and debunked on LearnEMC. Montrose's defence holds only for a power plane between two grounds | not checked, ever |
| EMC-9 | Avoid 90° trace corners for EMC | **folklore** below multi-Gb/s (LearnEMC, Bogatin, Shlepnev). See ESD-8 for TI's ESD variant | not checked |
| EMC-10 | Guard traces around clocks | **folklore** at today's spacings (Bogatin & Simonovich DesignCon 2013, Ritchey) | not checked |
| EMC-11 | Single-point vs multi-point grounding by frequency; ground the clock to chassis; terminate every line longer than λ/4 | **folklore** per LearnEMC's worst-guidelines page | not checked |

## Thermal

| id | Rule | Status | Evidence |
|---|---|---|---|
| TH-1 | A datasheet θJA is a JEDEC test-board number (JESD51-7 2s2p, 114.3 × 76.2 mm), not a design value | **verified** (SPRA953 §1.1: "most often misused", plus the JESD51 board definition) | `check_thermal` already models copper instead of trusting θJA |
| TH-2 | Tj = Ttop + ΨJT·P, with ΨJT 0.5-2 °C/W for plastic packages. Not valid with a heatsink on the part | **verified** (SPRA953 §3, JESD51-2) | `tj_from_top`; gap G7 (bring-up) |
| TH-3 | One plated via conducts L/(k·A_annulus): 0.3 mm / 18 µm / 1.6 mm ≈ 230 K/W; filled ≈ 15 K/W | **verified**: reproduces TI SNVA419C's 261 K/W (271 here) and Würth's 64 K/W (60 here) and 14 K/W filled (14.7 here). `check_thermal.via_r_cw` agrees with SNVA419C within 15 % | `via_theta`, test |
| TH-4 | "An unfilled via is 5-10 K/W" (Aivon blog) | **folklore**: off by 20-50× from the formula and from both vendors | test |
| TH-5 | "Copper area saturates near 1 in² (645 mm²)" (check_thermal's curve: θ floors at 55 C/W on 2 layers) | **disputed**. An exact annular-fin model (1 oz, 2 mm source, h = 10 W/m²K on both faces) still gains 3× from 645 mm² to an infinite sheet (92 → 32 K/W), and reaches within 10 % of the sheet only at ~4900 mm² (2 oz: ~9100 mm²). The empirical flattening comes from vendor test boards that are themselves small. check_thermal errs high (safe), but its "add vias, more copper won't help" advice may be wrong on a big board | `fin_theta`, `fin_saturation_area`; gap G4 |
| TH-6 | Forced air: h grows like √v (laminar), so the first 100-200 LFM buy the most | **verified** direction (check_thermal already uses TI's measured factors and says linear errs high) | covered |

## Gaps: what hwde should do next

Ranked, most worth doing first. G1-G4 are filed as hive-hwde briefs
(tasks 261031-012852-03534ea0dc, -7b1ff6e275, -ab4e83fa07, -44d25c4409).
File the rest in this order as in-flight work drops: G8, G6, G5, G7, G9.

- **G1 (filed)**: put `check_esd` in the verify suite: judge its findings
  on the pinned boards into `pinned/known/*.json` (lite-main's USB and CC,
  core's far USBLC6 and CC), add a `checks.d` fragment and a planted fault.
- **G2 (filed)**: clamp ratings in `check_esd`: the clamp's capacitance
  against the interface (USB 2.0 HS ≤ 4 pF, 5 Gbps ≤ 0.5 pF, from
  `parts.json` or the datasheet record), its standoff VRWM ≥ the line's
  highest voltage, and matched D+/D- capacitance.
- **G3 (filed)**: a ferrite-bead check: a bead in a rail carrying more than
  ~20-50 % of its rated current (from check_current's branch currents) loses
  its impedance. Also an ngspice AC check of each bead + cap filter's peak,
  with the damping fix (a resistor across the bead or a lossy cap).
- **G4 (filed)**: calibrate `check_thermal`'s copper curve against a
  spreading model (the fin solution here, or a 2D finite-difference solve of
  the board's actual net copper on both layers with the board's own size). The
  goal is to stop telling a big board that more copper won't help.
- **G5**: a plane-gap crossing screen for every net with a fast edge (not
  only `constraints.high_speed`), from EMC-6 and EMC-3. Overlaps the
  digital-si-rf ingest; left for it.
- **G6** (done, #494: `check_esd` `esd_corridor_coupled`, and `esd_via_first`
  for the via): ESD-9's keep-out: unprotected traces running beside the
  connector-to-clamp stretch, or the via that stretch takes.
- **G7**: a bring-up helper: from IR or thermocouple top temperatures and
  measured power, Tj = Ttop + ΨJT·P against the part's limit.
- **G8**: an emission pre-screen per switching converter: hot-loop area
  (already found by the buck records) × ripple current × EMC-3's harmonic
  envelope through EMC-1, in dBµV/m against CISPR 32 / FCC class B. A rank of
  which loop to shrink first, not a compliance answer.
- **G9**: cable common-mode budget: any connector whose cable may be ~1 m
  and whose ground pin is far from the clamp or the chassis bond sees a
  few-µA budget (EMC-2). Hard to measure from layout alone; needs a design
  idea first.

## What not to build

- No 20-H, 90° corner, guard-trace or "split the ground" checks (EMC-8 to
  EMC-11). Each is measured or argued false by more than one source.
- No λ/20 hard limit. If stitching is checked, report the SE estimate in dB
  (EMC-5), not pass/fail at λ/20.
