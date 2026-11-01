# Solvers

hwde's simulators beyond the KiCad-bundled ngspice that `sim_run` and
`sim_board` drive. They come from the Hive's read-only IIC-OSIC-TOOLS tree
(`/opt/eda-toolchain/iic-osic-tools-*`, the chip-flow module's toolchain);
`scripts/lib/solvers.py` finds it (or `HWDE_EDA_TOOLCHAIN`) and runs each
binary through the image's own loader. Nothing is installed.

| solver | kind | version (2026.09 tree) | deck written by |
|---|---|---|---|
| ngspice | circuit | 47 | `sim_solve.py`, `sim_run`, `sim_board` |
| Xyce | circuit (a second engine to cross-check ngspice) | 7.10 | `sim_solve.py` |
| openEMS | full-wave 3D, FDTD | v0.37.0-rc2 | `lib/emsolve.py` `Csx` (CSXCAD XML) |
| Palace | full-wave 3D, FEM: eigenmode, driven S-params, electro/magnetostatic | v0.18.1 | `lib/emsolve.py` (JSON config + gmsh mesh) |
| gmsh | mesher for Palace | 4.12.1 | `lib/emsolve.py` (`.geo`) |
| OpenVAF | Verilog-A to OSDI compiler, for compact models in ngspice | 20260616 (OpenVAF-reloaded) | `solvers.compile_va` |
| FastHenry2 | quasi-static L / R extraction | 3.0.1 | `lib/emsolve.py` |
| FasterCap | quasi-static C extraction | 6.0.7 | `lib/emsolve.py` |
| thermalfv | board-plane heat spreading + electro-thermal coupling | hwde's own | `lib/thermalfv.py` |

`hwde sim_solve probe` lists them; `hwde sim_solve validate` checks them.

## Trust: every solver earns it on a case with a known answer

A solver's number goes into a board's review only while its validation
case passes. `hwde sim_solve validate` runs each solver on one case whose
answer is closed form and reports the error against a stated tolerance.
Measured on this machine, 2026-10-30:

| case | solver | reference | measured error | tol |
|---|---|---|---|---|
| circuit_ngspice | ngspice | RC low-pass: \|H(fc)\| = 1/√2, step v(τ) = 1 − 1/e | 0.000% | 0.5% |
| circuit_xyce | Xyce | the same deck | +0.017% | 0.5% |
| circuit_xspice | ngspice + XSPICE | d_and truth table + 4-bit ripple counter (exact); adc_bridge -> d_dff -> dac_bridge puts a square wave on the next clock edge + bridge/flop delays; 1st-order sigma-delta ones density = Vin/Vref over 4096 clocks (Vin 0.3137, 0.7071) | logic exact, delay −0.15%, density +0.02% | 0.5% |
| circuit_osdi | ngspice + OpenVAF | Verilog-A 2k over a plain 1k: v = 1/3 | 0.000% | 0.01% |
| circuit_xspice_kicad | KiCad 10.0.6's libngspice (sim_run's engine) | the circuit_xspice decks + the OSDI divider from `.va` source | as circuit_xspice | 0.5% |
| openems_microstrip | openEMS | Hammerstad-Jensen Z0 and ε_eff, 1.5 mm on 0.8 mm of εr 4, 1–2 GHz | −0.1% (Z0), +0.13% (ε_eff) | 2% |
| palace_cavity | Palace + gmsh | closed 40×30×20 mm PEC box, first four modes | +0.025% worst | 0.5% |
| fasthenry_bar | FastHenry | 10×1×0.035 mm Cu bar: R = l/(σwt); L by Ruehli's partial self-inductance | +0.25% (L), 0.00% (R) | 2% |
| fastercap_cube | FasterCap | isolated cube, C = 0.66067813·4πε0·a | −0.10% | 1.5% |
| thermal_fin | thermalfv | 1D fin, θ(tip)/θ0 = cosh(m·dx/2)/cosh(mL) | +0.001% | 0.5% |
| thermal_point | thermalfv | point source on a plate, θ(r) = P/(2πkt)·K0(mr), r = 3, 5, 10 mm | +0.15% worst | 1% |
| electrothermal | thermalfv | strip at fixed volts, R rising with T: hAθ = V²/(R0(1+αθ)) | 0.000% | 1% |

A case that fails is a defect of hwde's deck or reader, or of the tree,
not of a board: fix it before using the solver. Its tolerance is the
reference's own accuracy plus the discretisation a minutes-long solve
allows, and should only tighten.

The openEMS microstrip took two fixes to get there, both kept in
`lib/emsolve.py`. First, the strip-edge mesh follows the thirds rule, with
no line on the edge itself: a line there with thirds lines beside it read
Z0 4.6% low. Second, the reader uses the exact uniform-line identities, so
the probes can sit λ/6 apart instead of one cell apart. At one cell, γ was
ill-conditioned and ε_eff wandered by 10%. Mesh convergence (Z0 at 1.5
GHz): 6×6 cells across w×h 53.20 Ω, 12×8 52.82, 20×12 52.78, 30×16 52.75,
against HJ 52.86. The default is 12×8: about 35k cells, seconds on an idle
machine.

## How each is run

- **Loader.** The tree's ELFs are built against glibc 2.39; the host's is
  newer. Each runs as `$T/lib64/ld-linux-x86-64.so.2 --library-path ... exe`.
  The tree's `liblapack.so.3`/`libblas.so.3` are absolute symlinks into
  `/etc/alternatives` (the host's, which need GLIBC_2.43), so the library
  path names `usr/lib/x86_64-linux-gnu/openblas-pthread` first.
- **MPI.** Xyce and Palace are Open MPI programs. `OPAL_PREFIX=$T/usr` and
  `OMPI_MCA_ess_singleton_isolated=1` let them start as a single rank
  without spawning `orted`. hwloc warns that `/sys` is missing (the
  sandbox), which is harmless.
- **Heavy.** `bin/hwde` runs `sim_solve` under `hive heavy`, one heavy
  job on the machine at a time. Palace and the thermal cases take seconds;
  openEMS takes minutes.
- **Load.** Xyce loads Trilinos's large shared libraries. With the disk
  saturated (load 244, 10-30) its 4 s RC deck took 290 s, almost all of it
  spent waiting, not computing. Each case allows 20 minutes, and OpenBLAS runs one
  thread (its pool busy-waits). The live-solver tests are in test-slow.
- **XSPICE and OSDI (#499).** The tree's own `spinit` names
  `/foss/tools/...` for its code models, which isn't here, so `solvers.command`
  points ngspice's `SPICE_SCRIPTS` at hwde's spinit
  (`~/.cache/hwde/ngspice-init/<tree>/spinit`) with a `codemodel` line per
  `lib/ngspice/*.cm` the tree really has (analog, digital, spice2poly, table,
  tlines, xtradev, xtraevt). `A` devices, `d_*` gates and flops, `adc_bridge`
  / `dac_bridge` then parse. KiCad's bundled libngspice finds no spinit either:
  `simlib` loads the `*.cm` beside it (`lib/ngspice/`) into every engine it
  starts, so `sim_run` and `sim_board` benches may use `A` devices too.
  OSDI: a deck loads its model itself, `.control` / `pre_osdi FILE` / `.endc`,
  in both engines. In a sim_run bench a relative FILE is beside the bench,
  and a `.va` FILE is compiled first with the tree's OpenVAF
  (`solvers.compile_va`, cached in `~/.cache/hwde/osdi/` by source hash).
  The tree's prebuilt `.osdi` (psp103_nqs, r3_cmc, bsimcmg) are in
  `lib/ngspice/`; vacask's under `foss/tools/vacask/lib/vacask/mod/` are for
  VACASK. `bin/openvaf` is an absolute symlink into `/foss`: hwde runs
  `openvaf-r`. Gotcha: an integrator cap with no DC path needs `uic` and an
  `.ic`, or the operating point is singular and the loop starts saturated.

## Studies for boards

- `hwde sim_solve line --w W --h H --er ER [--t T] [--z-target OHM]`: a
  bare microstrip in openEMS, read by three voltage and two current probes
  (Z0 = √(V·dV/dx ⁄ I·dI/dx), exact on a uniform line whatever its standing
  wave). It is set beside impedance.py's 2D quasi-static solve of the same
  cross-section. A disagreement past 3% exits 1: one of the two is wrong
  for that geometry. A JLC-style 0.34 mm line on 0.2 mm of εr 4.4:
  53.34 Ω full-wave against 53.70 Ω quasi-static (−0.68%). This is the check for an RF board's controlled
  impedance before the fab's calculator is trusted.

- `hwde sim_solve planar-geom --pcb B --region X0,Y0,X1,Y1 --port X,Y,DIR,W ...`
  then `hwde sim_solve planar --geom G.json [--excite 0,1] [--touchstone F.s2p]`:
  S-parameters of a planar microstrip circuit cut from a board. planar-geom
  takes every copper item on one layer in the region (any net; custom pads
  as drawn, via geom.py's new `_custom_pad`; NPTH skipped; holes split since
  CSXCAD polygons have none) and the dielectric under it from the board's
  stackup. planar draws the copper as PEC polygons over an infinite ground,
  adds a straight feed per port (half a wavelength) ending in a 50 Ω lumped
  port, and splits each feed's three voltage probes into the wave going in
  and the wave coming out, moved to the reference plane. One openEMS run per
  excited port. Measured on a 30 mm 50 Ω thru (FR4 1.53 mm, 6 GHz): |S21|
  within 0.04 dB from 1 to 6 GHz, |S11| below −19 dB (the probe split's
  floor), phase within 2% of the quasi-static line; 80 s, 44 MB. hw-lab
  rf-kit's F5 open-stub notch (designed for 2.45 GHz): notch at 2.466 GHz,
  −56 dB, 3 min. Below f_max/8 the pulse carries little energy and the
  answer is noise. Limits: infinite ground (so not antennas, nor a coupon's
  finite ground), ports must leave the circuit straight, the other ports'
  50 Ω ends reflect a little (error ~|S_ii·Γ|).

- `hwde sim_solve board --pcb B --region X0,Y0,X1,Y1 --port X,Y,DIR[,LAYER[,W]] ...
  [--part REF=OHMS] [--symmetric] [--touchstone F.s2p] [--deck-only DIR]`
  (#455, `lib/emboard.py`): S-parameters of a region of a board with
  every copper layer of its stackup, not one layer over an infinite
  ground. `emboard.extract` cuts the region into plain data (each layer's
  z from the board's stackup, copper per net, via and plated-pad barrels,
  2-pad parts as lumped resistors); `emboard.deck` builds the openEMS deck;
  `emboard.waves`/`sparams` read it; `emboard.study` runs it. A port sits
  on the region's edge (the region is cut at its plane). Its net, width and
  reference layer (the nearest layer with copper under it) come from the
  copper. Every layer's copper crossing the edge near it is carried out
  as a feed, half a wavelength long: signal, coplanar ground (stitched to
  the reference layer) and the plane. The feed ends in a 50 Ω lumped port.
  Three voltage and two current probes on the feed give its own Z and γ
  exactly, so V and I are moved to the plane and S is referenced to 50 Ω
  there: a line drawn at the wrong width reads as mismatched, which PR 442's
  voltage-only split cannot see (its S is referenced to the feed's own Z).
  With every port excited S = B·A⁻¹, exact whatever the far ends reflect.
  The three openEMS traps from hw-lab vna-verify are built in:
  1. every copper plane is a pinned z line, and `Csx.xml()` refuses to write
     a deck whose zero-thickness metal is off the mesh
     (`Csx.sheets_off_mesh`; CSXCAD would drop it with "Unused primitive");
  2. MUR walls, never PML_8 (it grew without bound there);
  3. a derivative-of-Gaussian pulse (`Csx.dgauss`: no DC, f0 = f_max as
     the probe sampling rate).
  The dielectric is lossless, because a conductivity is a loss tangent at
  one frequency only; add dielectric and copper loss in the circuit model.
  The mesh uses openEMS's thirds rule on the signal's edges: a line on the
  edge read 48.2 Ω for a 50 Ω line, the thirds rule reads 50.6–51.1 Ω.
  Known answers (tests/test_emboard.py, test-slow; synthetic 2-layer
  coupon, 0.5 mm of εr 4.4, w 0.959 mm = 50 Ω quasi-static, 6 GHz, about
  5–13 s each):
  | case | measured | tolerance |
  |---|---|---|
  | 30 mm thru vs ABCD of the line (impedance.py Z0, ε_eff), f_max/10–f_max (phase, ε_eff from f_max/5) | \|S21\| within 0.05 dB; phase within 0.7% of βl; Z0 back from S 49.7–51.3 Ω; ε_eff +0.4..+1.2%; \|S11\| ≤ −35 dB | 0.1 dB, 1.5%, 3%, 2.5%, −25 dB |
  | open stub 13 mm from the line's centre | notch 3.110 GHz vs c/(4(L+ΔL_open)√ε_eff) = 3.112 GHz (−0.07%) | 2% |
  | mutant: the line twice as wide | Z0 31.2 Ω (its own quasi-static 31.5), so 38% off 50 Ω: the thru check fails | must fail |
  On hw-lab vna-verify's thru row (4 layers JLC04161H-1080B, CPWG 0.43/0.25
  bare over In1, via fences, 30 mm cut between the SMAs; 634k cells, 8 min
  on the loaded tower): ε_eff 2.83–2.87 against field_cpwg's 2.851,
  S21 phase within 1.2° of the line, \|S21\| within 0.05 dB, Z0 53.6–54.7 Ω
  against 53.05 Ω, \|S11\| peaks −22 dB (a 53 Ω line on a 50 Ω reference).
  `line_from_s` cannot read Z0 where βl is a multiple of π, nor phase or
  ε_eff where βl is small (under ~70°).
  The default cell is min(w/5, h/2.5, λ/40) across the signal: at h/1.5 a
  w/h = 3.8 line read ε_eff 4.1 against 3.55, at h/2.5 3.60.
  `impedance.field_cpwg(w, g, h, t, er, ground_w)` is the public CPWG
  solve (vna-verify's 0.43/0.25 on 0.2444 mm of 3.91: 53.05 Ω bare).
  Hooks for 3D studies (#479, #481): `deck(..., extra=fn)` gets the Csx
  and a context (stack, ports, fixed/pinned mesh lines) before the mesh
  is smoothed. Limits: ports must be axis-aligned on the region's edge;
  connector bodies (SMA) are not modelled (#479); no loss.

## What is not here yet

- Antennas in openEMS (radiation boundary, NF2FF: #481); connector
  bodies and launches (the SMA's coax and pin: #479; `sim_solve board`
  already has the vias and plane cut-outs); Palace for the same decks;
  scikit-rf to read the touchstone.
- `thermalfv` on a board's real copper. check_irdrop already rasterises a
  net into a grid; feeding its grid and current into `thermalfv.joule`
  gives the self-heated trace and neck temperatures check_thermal's
  calibrated curve only estimates.
- Thermal FEM (Elmer is not in the tree), and coupled mechanical stress.
- Palace's electrostatic and magnetostatic modes for capacitance and
  inductance matrices of real structures, cross-checked against
  FasterCap/FastHenry on the same geometry.

## HFSS

Ansys HFSS is the commercial full-wave reference. A licence is money and
the person's call. hwde does not drive it, and nothing here assumes it.
Palace (FEM, the same method as HFSS) and openEMS (FDTD) are the open
stand-ins, each held to its validation case. If the person buys a licence,
a deck exporter beside `lib/emsolve.py`'s would let HFSS check the same
cases.
