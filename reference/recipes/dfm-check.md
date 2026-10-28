# dfm-check - "can this actually be made"

`drc_routed` at 0/0 does NOT imply fabricable: DRC grades the board against the
project's own rules, and those rules can sit below the fab's floor. That gap is
what this recipe closes.

## What runs

`fab_export.py` writes the JLC-shaped package (Protel extensions, X2, the JLC
layer set, drill files, zipped). `bom_cpl.py` writes the BOM of record
(`BOM-full.csv`), the assembler upload (`BOM.csv`, one row per LCSC part), the
pre-buy list (`prebuy.csv`: every placed Extended part with its LCSC number and
the qty for `--build-qty` boards, default 5 - JLC's BOM review may hold one as
idle stock that must be bought into inventory first) and `CPL.csv`, applying the
per-package rotation corrections from `reference/jlc_rotations.csv` - the
catcher for a polarized part mounted backwards, which net-level schematic parity
is blind to by construction. For a self-assembled build it also writes
`<ws>_BOM_digikey.csv` and `<ws>_BOM_mouser.csv` from the BOM of record
(`distributor_bom.py`; quantities x `--boards`, default 1), with distributor
part numbers, stock, prices and `<ws>_BOM_cost.json` when the DigiKey/Mouser
API keys are set. Without keys it says so on stderr and carries on.

Who is in which file is decided by `assembly_class` in canonical parts data,
never by what the position export happened to contain: `smt_placed` parts go in
BOM.csv AND CPL.csv, everything else (`hand_install`, `off_board`, `dnp`,
`customer_supplied`, `select_on_test`, `board_feature`) appears only in
BOM-full.csv, marked, with an `Instructions` column. Per-site overrides are
`refdes_class` / `refdes_dnp` + `refdes_notes` on the parts line. **Do not
post-filter or hand-edit the generated files** - a board-local filter script is
the failure mode this replaced (rf-de-20m, codex C9: nine DNP sites, three of
them the ZVS fix).

Then the `dfm` gate. Note what it reads: `gate.py` re-exports gerbers to a
SCRATCH directory from the BOARD, plus the sibling schematic (CPL polarity
oracle) and `parts.json` (BOM leg). The shipped `fab/<board>_gerbers.zip` is an
ORDERING artifact and is NOT a gate input - which is why a board edit stales the
zip through the invalidation MARK, not through any hash comparison.

## Reading the result

- Trace width / clearance / drill / annular ring / hole-to-copper-to-edge are
  errors. A width failure a micron under a floor is still a failure: fix the
  floor at `board_init` (fab floors come from the selected JLC profile at ERROR
  severity, `lib/fabfloors.py`) and re-route, do not waive it.
- KiCad's stock 0.12 mm silk, tight mask dams and a placed part sourced off
  LCSC (`dfm_bom_off_lcsc` - real MPN + distributor, just not a JLC line) are
  advisory warnings by design.
- The assembly-class errors are not waivable paperwork: `dfm_bom_incomplete`
  (a placed part with no source at all), `dfm_assembly_unplaced_smt`,
  `dfm_assembly_qty_mismatch` (parts.json's `qty_per_board_populated` disagrees
  with the classes) and `dfm_unplaced_in_package` (the shipped BOM/CPL lists a
  part the declared variant does not place). The last one is what stops a stale
  or hand-edited fab directory shipping a DNP site.
- `cpl_polarity` with a `rotation_delta_deg` is the mounted-backwards class.
  Believe it: the pad geometry supplies that angle.

## The second opinion

JLCDFM (the fab's own 30+ checks) has no public API and stays a human browser
step: upload the zip at the ordering checkpoint. The local engine covers the big
classes so that step finds nothing new, not so it can be skipped.

## Do not

- Do not treat a passing dfm gate as a quote. Panelization, impedance control
  and part availability are priced at JLC, and `order_quote` numbers are
  transcribed estimates (`estimated: true`), never a quote.
- Do not re-export gerbers just to "refresh" them before ordering without
  re-running the gate - the export is what the latch binds to.
