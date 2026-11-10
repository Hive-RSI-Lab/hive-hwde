# JLC CPL rotation for bottom-side parts

**Rule pcb-flow uses:** CPL rotation = (180 - KiCad rotation + correction) mod 360, read from below. Position is the board's own coordinates, not mirrored. `correction` is the model fit (`cpl_verify.py`), or `jlc_rotations.csv` when there's no model.

**Where it comes from. It isn't a JLC statement.**
- Bouni `kicad-jlcpcb-tools`, `fabrication.py`: `rotation = (180 - rotation) % 360` for a bottom part ("bottom angles need to be mirrored on Y-axis"), then the correction is added. A position offset has its x negated on the bottom. Read 2026-11-02.
- bennymeg `Fabrication-Toolkit`, `process.py`: `rotation = (180.0 - rotation)` for a bottom part ("JLC expect 'Rotation' to be 'as viewed from above component', so bottom needs inverting, and ends up 180 degrees out as well"), then offsets are added. Read 2026-11-02.
- The geometry fixes everything except which way the board is turned to look at it. Turned about its y axis, the rule follows (derivation in `cpl_verify.py`). Turned about its x axis, the rotation would be (-kicad + correction), which is 180 degrees different.

**What settles it:** JLC's order preview, then a built board. Until one is recorded here, `dfm_check` raises `cpl_bottom_preview` for every board with bottom parts. It's a warning, so it asks for the check without failing the gate.

**Evidence so far:**
- hw-lab twin-side's five polar or multi-pad bottom parts (U301, U303, U304, Y301, D301): the LCSC model read from below lands pin 1 on pad 1 only at the rotation this rule gives (`boards/twin-side/reports/cold-review-1.md`, R1). This shows the model fit and the rule agree. It doesn't show what JLC does.
- JLC preview: none recorded yet.
- Built board: none yet.
