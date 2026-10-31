# Ingestion: the EE tool ecosystem

Task 261031-011721-ff0fd27245 (issue #305), 2026-10-30/31. This is one of the
parallel EE ingestion runs, one per domain (the others are in
`reference/ingest/*.md`). Domain: the open-source EDA and EE tools, scripts and
libraries hwde could use. For each one: what it would add, its licence, and
whether it runs here. A claim counts as **verified** only after I ran it on
this machine or reproduced it by a calculation. **Two-source** means it rests
on two independent published sources that I did not run. **Disputed** means
the sources or runs disagree. **Folklore** means a rule people repeat that a
run contradicts or can't back up. **Unverified** is the label for anything
else. The ranked gap list is at the end. The lead files from it as in-flight
work drops.

Scripts that reproduce the numbers are in
[`tools-ecosystem/`](tools-ecosystem/). Each one runs on its own; they are not
part of `bin/hwde test`.

## What hwde already uses

KiCad 10.0.6 (`kicad-cli`: drc, erc, gerbers, drill, pos, netlist, bom, step,
vrml, render, pdf, svg, upgrade), KiCad's bundled libngspice through InSpice
(the PySpice fork, GPL-3.0), freerouting 2.2.4, KiCadRoutingTools, gerbonara
(Apache-2.0), kiutils (GPL-3.0), kicad-sch-api (MIT), easyeda2kicad
(AGPL-3.0), hierplace and kinet2pcb (MIT), and its own 2D field solver
(`scripts/lib/impedance.py`, numpy/scipy). hwde itself is MIT. Every
GPL/AGPL tool on this list runs as a separate program or an unmodified
import. hwde ships none of them, and nothing serves them over a network, so
none of them changes hwde's licence. (That is the usual reading of the GPL
for separate programs. I have not checked it with a lawyer.)

## Found: the Hive already has the big solvers

`/opt/eda-toolchain/iic-osic-tools-2026.09` is the IIC-OSIC-TOOLS tree that
hive-chip-flow uses. It holds these tools (versions are from its
`tool_metadata.yml`):

| tool | version | licence | runs here? |
|---|---|---|---|
| openEMS (FDTD EM, with CSXCAD and Python bindings for py3.12) | v0.37.0-rc2 | GPL-3.0 | **verified**: the binary starts through the image loader. No simulation run yet. |
| FasterCap (2D/3D capacitance, BEM) | 6.0.7 | LGPL-2.1 | **verified** (the coax and microstrip runs below) |
| FastHenry (3D inductance and resistance) | 3.0.1 | free (MIT-like) | **verified**: it starts. No model run. |
| AWS Palace (3D FEM EM: eigenmode, driven S-params, electrostatics) | 0dc74cd | Apache-2.0 | `palace --help` works. No model run. |
| ngspice (standalone) | 47 | BSD/mixed | **verified** (the RLC run below) |
| Xyce | 7.10 | GPL-3.0 | **not yet**. This is the MPI build, and OpenMPI's singleton init execs `orted` by name, which fails outside the image. It needs bin/eda's shim directory, and that is broken for this tool (see the problem below). |
| Qucs-S | 26.1.1 | GPL-2.0 | GUI plus filter/attenuator/line synthesis tools. There is no qucsator-rf in the tree. Not run. |

hwde uses none of them. hive-chip-flow's `bin/eda` is supposed to run any
tool in the tree, but its generic fallback fails for Xyce, openEMS, FasterCap
and fasthenry with "don't know how to run". The cause is that
`$T/foss/tools/bin/<tool>` is an absolute symlink into `/foss/...`, and the
fallback resolves it with `readlink -f`, which exits 1 when `/foss` doesn't
exist. I posted this to the problems board with the fix (use plain
`readlink`). For now I run them directly through the image's own loader:
`$T/lib64/ld-linux-x86-64.so.2 --library-path "$T/lib/x86_64-linux-gnu:$T/usr/lib/x86_64-linux-gnu:$T/usr/lib" <binary>`.
Xyce also needs `$T/usr/lib/x86_64-linux-gnu/openblas-pthread` first on that
path. The tree's `lib/.../libopenblas.so.0` and `liblapack.so.3` are
symlinks to `/etc/alternatives`, which resolve to the host's glibc-2.43
copies (verified: "GLIBC_2.43 not found").

## Verified results

### V1. hwde's impedance solver agrees with two independent methods; IPC-2141 breaks past w/h ≈ 2

Uncoated surface microstrip, t = 35 µm. The JLC 4-layer outer prepreg is
h = 0.2104 mm with εr 4.4. A 2-layer 1.6 mm board is h = 1.5 mm with εr 4.5.
Each case was solved three ways: hwde's own solver
(`impedance.field_microstrip(..., mask=None)`), scikit-rf 2.2.0's `MLine`
(Hammerstad-Jensen with the thickness correction, no dispersion; BSD-3), and
the IPC-2141 formula (hwde's `closed_form` fallback). FasterCap 2D BEM was the
fourth method, with a 40·max(w,h) finite ground strip. Run them with
`z0_hwde.py`, `z0_hj.py` and `fc_ms.py` on `z0_cases.txt`. FasterCap
finished only the w = 0.2 mm case. At `-a0.002` the wider cases ran past
15 minutes each and I stopped them, so they're still to do (gap G2).

| w mm | h mm | w/h | H-J Ω | hwde Ω | FasterCap Ω | IPC-2141 Ω | hwde vs H-J | IPC vs H-J | JLC mask shifts hwde by |
|---|---|---|---|---|---|---|---|---|---|
| 0.10 | 0.2104 | 0.48 | 87.96 | 88.42 | | 86.35 | +0.5% | −1.8% | −6.9% |
| 0.20 | 0.2104 | 0.95 | 67.94 | 68.34 | 68.11 | 67.29 | +0.6% | −1.0% | −5.0% |
| 0.36 | 0.2104 | 1.71 | 50.92 | 51.19 | — | 49.08 | +0.5% | −3.6% | −3.6% |
| 0.60 | 0.2104 | 2.85 | 37.50 | 37.63 | — | 32.24 | +0.4% | **−14.0%** | −2.6% |
| 1.00 | 0.2104 | 4.75 | 26.30 | 26.35 | — | 14.80 | +0.2% | **−43.7%** | −1.8% |
| 0.25 | 1.5 | 0.17 | 128.54 | 129.33 | | 130.34 | +0.6% | +1.4% | −2.8% |
| 1.00 | 1.5 | 0.67 | 82.85 | 83.30 | | 84.97 | +0.5% | +2.6% | −1.2% |
| 2.90 | 1.5 | 1.93 | 48.75 | 48.92 | | 47.86 | +0.4% | −1.8% | −0.6% |
| 5.00 | 1.5 | 3.33 | 34.27 | 34.32 | | 28.59 | +0.2% | **−16.6%** | −0.4% |

- **Verified:** hwde's field solver is within 0.6% of Hammerstad-Jensen
  everywhere, and within 0.4% of FasterCap on the one case it finished (FasterCap is
  within 0.25% of H-J there). These are three
  independent methods (finite volume, a published closed-form fit, boundary
  elements), so the solver hwde uses by default can be trusted to about 1%
  for uncoated microstrip. That is well inside the ±10% JLC promises on a
  controlled-impedance order.
- **Folklore:** "IPC-2141 is good enough for a first cut". At w/h = 2.85 it
  is 14% low, and at 4.75 it is 44% low. The formula's own validity range,
  0.1 ≤ w/h ≤ 2.0 (hwde's docstring says "~0.1–3"), is real, and past it the
  formula is badly wrong. hwde uses it only when scipy is missing, but
  anything that quotes it (an agent's hand calc, a review) should state the
  range. **Gap G7.**
- **Verified, one solver, one external source:** JLC's solder mask lowers a
  thin outer trace's Z0 by 4–7%, enough to move a 50 Ω design outside a ±5%
  window. Here I measured it only with hwde's solver. JLC's own calculator
  models the same coating (the parameters are quoted in `impedance.py`), so
  the direction is two-source but the size of the shift is one-source.
  FasterCap could confirm it (gap G2).
- **FasterCap's own docs contain an error.** Its 2D coax example gives the
  dielectric line as `D ... 2.0 1.0 ... -`. Taken as written, that puts
  εr = 1 between the conductors, which contradicts the example's own
  analytic answer. With the permittivities swapped, FasterCap gives
  114.3 pF/m against the analytic 113.4 (+0.8%, a 40-segment polygon). In
  air it gives 80.28 against 80.26 (+0.02%). In 2D FasterCap takes the last
  conductor as the reference, so a two-conductor file prints a 1×1 matrix,
  which is C(trace, ground). If you wrap it, wrap it with this known.

### V2. ngspice-47 matches the analytic answer for a series RLC step

R = 0.5 Ω, L = C = 10 µ, 5 V step with a 1 µs ramp. The analytic peak is
7.2217 V at 32.95 µs. ngspice-47 from the tree gives 7.2208 V at 32.95 µs
(−0.01%). **Verified** (`/tmp` run, circuit in the table above). Xyce would
make a second simulator to diff against hwde's InSpice/libngspice results
(gap G5), but it doesn't start here yet.

### V3. KLC's footprint checker finds a real assembly error on lite-main

KiCad's own library linter (`kicad-library-utils/klc-check/check_footprint.py`,
GPL-3.0, pure Python, runs from a clone with no install) checked lite-main's
27 pulled footprints. Most of what it reports is KiCad-library house style
(names, the fab layer, 3D model paths: F5.x, F9.x, G1.1), and those don't
matter for a board. Two rules do:

- **F6.1/F7.1, the footprint's placement type:** the XR0850 8×8 mm power
  inductor footprint (`IND-SMD_L8.0-W8.0_XR0850`, as EasyEDA gives it) has
  only SMD pads but `(attr through_hole)`. On the board that covers L1–L4.
  `bom_cpl.board_part_fields` maps `through_hole` to class `tht`, so
  lite-main's `reports/bom-cpl.json` tells JLC "Through-hole - JLC THT
  assembly (or hand-solder after reflow); not on the SMT line" for four SMD
  power inductors. KiCad's own `kicad-cli pcb export stats` counts them as
  THT too (8 THT on the front). **Verified** on the pinned board with
  `tools-ecosystem/attr_vs_pads.py`. The reverse case also shows up: U8
  (HTSSOP-32 with exposed-pad thermal vias) and J1 (USB-C with THT shell
  legs) are `attr smd` with thru_hole pads. For those, `smd` is right
  (reflowed on the SMT line), so the check must allow a THT pad that is a
  thermal via or a shell leg on an SMT-placed part. core, ultra-front and
  hwde-led-test are clean. **Gap G1.**
- **F7.4:** U8's EP vias are "THT pads with incorrect layer settings". That
  is fpfix rule F's thermal vias, so this one is expected, not a fault.

### V4. kicad-happy as a second opinion

[aklofas/kicad-happy](https://github.com/aklofas/kicad-happy) (MIT, about 1.5k
stars, KiCad 5–10) is an agent skill plus standalone deterministic Python
analysers for schematics, PCBs, Gerbers, EMC, thermal and SPICE. Its
VALIDATION.md claims a 5,857-project open corpus (only 41 of them KiCad 10)
and 2.7 M regression assertions. **Unverified**: I didn't run its harness.
Its analysers run here in a plain venv with no KiCad (**verified**). On
lite-main, `--only-deterministic` gives 75 schematic and 31 PCB findings. The
ones that matter:

- PCB: J1's courtyard overhangs the edge by 0.03 mm, and J2/J3 sit 0.0 mm
  from the edge (edge connectors, so expected, but worth comparing with
  hwde's `check_courtyard`/dfm). Thermal vias on U1 are 4 against its
  rule's 5, and on U7 2 against 5. No fiducials. Test-point coverage is
  0/114 nets.
- Schematic: "CC1/CC2 missing 5.1k pull-down at J1" and "CFG2/CFG3 missing
  pull-up" on U1 = CH224A. These are **probably false positives**: the
  CH224 family has its own Rd on CC. I didn't check that against the
  datasheet here. It also reports "0/54 parts have MPNs", because it doesn't
  read hwde's `LCSC Part` field, so its sourcing checks are blind on hwde
  boards.
- **Disputed:** its thermal-via minimum of "5" and its fiducial-required
  error. JLC places boards without board fiducials when the panel has them.
  That belongs to the dfm-assembly ingestion.

The value is the disagreements, not the findings. Every finding where it and
hwde's verify disagree is either a missing hwde check or a false positive
worth recording. **Gap G3.**

### V5. Fab and bring-up outputs that run on the Hive's KiCad as it is

- **InteractiveHtmlBom** (MIT): `generate_interactive_bom.py --no-browser`
  runs under KiCad 10.0.6's bundled `python3.11` (which has pcbnew and
  wxPython 4.3.0a1) and Xvfb, with no install. On lite-main it wrote a
  420 kB ibom.html in under a second. **Verified.** Its PyPI package pulls
  wxPython and won't build in a plain venv (verified), so run it from a
  clone. It's what a person needs to hand-fit the THT parts and to probe a
  board at bring-up. **Gap G6.**
- **KiKit 1.8.1** (MIT): `uv pip install --target` for py3.11, then put that
  folder on sys.path inside KiCad's python. (The AppImage python ignores
  PYTHONPATH: verified.) It panelized lite-main 2×2 with mousebites, rails,
  fiducials and tooling holes. kicad-cli DRC on the panel showed 0
  unconnected, 72 hole_to_hole (mousebites, expected) and 16
  npth_inside_courtyard. **Verified.** It **fails** on hwde-led-test with
  "Missing parameter 'net_settings'", because `board_init` writes a
  `.kicad_pro` holding only `board, erc, meta, schematic`. A KiCad-saved
  project always has `net_settings`. Any tool that reads the project (KiKit,
  KiBot, kicad-happy's netclass checks) trips on it. **Gap G8.**
- **KiBot 1.9.1** (GPL-3.0): installs (verified). Not run. It overlaps
  hwde's fab_export, and its value would be the many output formats.
  Low priority.
- **kicad-cli exports hwde doesn't use** (all verified on lite-main):
  `pcb export stats --format json` (counts, minimum clearance 0.128 mm,
  minimum drill, copper area: a cheap cross-check of dfm), `pcb export
  ipcd356` (the IPC-D-356 netlist a bare-board tester uses; 290 through and
  443 SMD test records, an independent netlist to check the Gerbers'
  copper against), `ipc2581`, `odb` and `jobset run`. **Gap G4** (IPC-D-356
  against Gerber connectivity).

### V6. Part data

- **jlcparts** (yaqwsx, MIT code; the data is JLC's catalogue): a
  multi-part zip at `yaqwsx.github.io/jlcparts/data/cache.zip` (38 MB, plus
  `cache.z01`… at 50 MB each), refreshed daily. Last-Modified was 2026-10-30
  23:52 when I fetched it (**verified**). It's an offline, parametric copy
  of the whole JLC catalogue, so parts_search could filter by value,
  package, voltage and basic/extended without scraping, and a check sandbox
  could look up parts with no network. **Gap G9.**
- DigiKey, Mouser, Nexar/Octopart and element14 all need API keys. Getting
  one is the person's call (a warden). kicad-happy has clients for all of
  them that could be borrowed (MIT).

## Not run, worth knowing (two-source or unverified)

- **scikit-rf** (BSD-3): Touchstone I/O, de-embedding, `MLine`/`CPW`
  media, VNA calibration. It's the library for reading an openEMS or
  Palace S-parameter result, or a vendor's connector/ESD .s2p, and
  checking return loss against a mask. It installs and runs (verified).
- **pyems** (MIT): a Python front end that builds openEMS models of PCB
  structures (microstrip, vias, coax). It would cut a via or connector
  S-parameter model down to a short script. Unverified on openEMS v0.37.
- **SKiDL** (MIT) and **atopile** (MIT): circuits as code. hwde's own
  `kicad/gen/*.py` already covers this. Inspiration only.
- **KiCad-Diff / kiri** (MIT): visual schematic and PCB diffs for a
  review. That overlaps hwde's `layer_views`.
- **Elmer FEM** (GPL/LGPL) and **gmsh**: board thermal FEM. Neither is on
  the machine. The emc-thermal ingestion's call.
- **spicelib** (GPL-3.0): runs and parses LTspice/ngspice/Xyce raw files. It
  installs (verified). The bridge to a vendor's LTspice model. LTspice
  itself is freeware, not open, and runs on Windows/wine, so not here.

## Ranked gap list

Each line says the board case it must catch. The top three are filed as
briefs (the lead's pace). The rest wait here for the lead.

1. **G1 (filed 261031-014441-d66e01db4f) Footprint attr must match its pads.** lib_pull/fpfix sets
   `(attr smd)` on a footprint with only SMD pads and `through_hole` on one
   whose parts are fitted through holes. bom_cpl classes an attr/pad
   mismatch by its pads and says so. Verify fails a mismatch the fix didn't
   reach. Case: pinned lite-main L1–L4 (`tht` in bom-cpl.json today), and
   U8/J1 must stay `smd`.
2. **G2 (filed 261031-014449-039fec1de3) Run the Hive's field solvers from hwde.** Add `hwde eda
   <tool>`, or a fixed chip-flow `bin/eda` hwde finds the same way bin/hwde
   finds the KiCad tools, for FasterCap, FastHenry, openEMS, Palace and
   ngspice-47. A test pins `impedance.py` against FasterCap, with JLC's
   mask, on the V1 cases where the tree exists (skipped elsewhere).
   FastHenry gives the loop inductance of a buck's hot loop (the
   power/emc ingestions need it). Case: V1's table. Mask case: 0.1 mm at
   h = 0.2104 shifts by −6.9%, and FasterCap must agree within 2%.
3. **G3 (filed 261031-014457-617b9614e4) Second-opinion review.** `hwde second_opinion` runs
   kicad-happy's deterministic analysers (pinned commit) on a board, maps
   their findings to hwde's verify check ids, and lists every
   disagreement. A disagreement is closed by a new or changed hwde check,
   or by a recorded false positive with its reason. Teach it hwde's
   `LCSC Part` field. Case: lite-main (U1 thermal vias, J1 courtyard edge
   overhang 0.03 mm, CH224A CC "missing Rd", which should be recorded as a
   false positive).
4. **G4 IPC-D-356 against Gerber copper connectivity.** Export
   `kicad-cli pcb export ipcd356`, rebuild the nets from the Gerbers and
   drill files (gerbonara is already in the lock), and fail any net where
   they differ (a plotter or zone-fill fault the PCB file doesn't show).
   Case: lite-main fab/ Gerbers, with one track deleted from a copy of the
   Gerber.
5. **G5 Cross-simulator diffing.** Run sim_board's netlists on Xyce or
   standalone ngspice-47 as well as InSpice/libngspice, and flag results
   that differ by more than 1%. Needs Xyce to start (the bin/eda fix plus
   an orted shim). Case: the V2 RLC, and lite-main's buck snubber.
6. **G6 iBoM in the fab package and the board guide.** fab_export writes
   `fab/ibom.html` with KiCad's python and Xvfb. The board guide links it
   for THT hand-fitting and bring-up probing. Case: lite-main (J2–J4
   THT).
7. **G7 The IPC-2141 range in docs and calcs.** `eng_calc` and any review
   text that quotes IPC-2141 must state 0.1 ≤ w/h ≤ 2 and fall back to the
   field solver outside it. Case: w = 0.6 at h = 0.2104 (−14%).
8. **G8 board_init writes a full `.kicad_pro`.** Run `kicad-cli pcb upgrade`
   or take a KiCad-saved template, so net_settings and the netclasses
   exist. Case: KiKit panelize on hwde-led-test fails today.
9. **G9 Offline JLC catalogue.** parts_search and stock_check can read
   jlcparts' daily dump (cached in HOME, dated), so part choice is
   parametric and a check sandbox needs no network. Stock stays live
   (72 h rule). Case: find a 10 µH ≥ 6 A 8×8 inductor without the keyword
   search.
10. **G10 Panelization.** `hwde panel` (KiKit) for small boards: JLC
    prices by panel, and a 13×10 mm board like hwde-led-test is under
    JLC's assembly minimum on its own. Depends on G8.
11. **G11 S-parameter checks.** openEMS (via pyems) or Palace models of a
    board's USB/RF launch, read with scikit-rf against a return-loss mask.
    The digital-si-rf ingestion owns the masks. Its coupon board
    (G-coupon) is below.
12. **G12 kicad-cli stats as a dfm cross-check.** The minimum clearance,
    drill and track width that KiCad reports must match dfm_check's
    numbers. Case: lite-main 0.128 mm.

### Coupon boards (tools that need hardware to be trusted)

- **Impedance coupon:** 2- and 4-layer JLC stack-ups, with 50 Ω single and
  90/100 Ω differential lines at the w/h values in V1, coated and uncoated,
  each with SMA or U.FL ends and a through-line for de-embedding. Measured
  with a VNA or TDR (the person's call: they may not own one), it settles
  the mask shift and JLC's real εr. Without an instrument, a ring resonator
  read by a NanoVNA (about $60, the person's purchase) still gives εr_eff.
- **Loop-inductance coupon:** three buck hot-loop layouts. The ringing
  frequency at the switch node, with a known C_oss, gives L_loop to
  compare with FastHenry. Shared with the power/emc ingestions.
