# schematic-block - write ONE sheet's generator script; the schematic is its build output

One job: implement your assigned hierarchical sheet as a generator script
`kicad/gen/<sheet>.py` using schlib, wiring ONLY pins that exist in the
datasheet-extract JSONs. The Python is the source (reviewable, re-runnable,
diffable); `.kicad_sch` is build output.

You are a P4 subagent of the /hwde pipeline (one per sheet, parallel where
independent). Files are the interface. Run scripts with the repo venv
python; JSON out, exit 0/1/2. Keep output ASCII.

## Inputs
- `architecture/sheets.md` (your sheet: blocks, interface nets, refdes range
  incl. pwr_base), `architecture/constraints.json` (canonical net names),
  `parts/parts.json` + `parts/<lcsc>.json` (pinout ground truth),
  `research/refdesign-*.json` (topology decisions), `lib/` (pulled symbols).

## The generator pattern (S7 reference: `tests/s7_regen/hierdemo/`)
- Your file exposes `build() -> schlib.Sheet`. The root generator stitches
  sheets via `schlib.Project.add_sheet(child, at, size, nets=[...])` and
  `Project.save(out_dir, decoupling=path)`.
- Grounding aid FIRST: `scripts/schlib.py --pins "<LIB:SYMBOL>" --lib lib/aiee.kicad_sym` prints the
  symbol's real pin table - wire against that plus the datasheet JSON, never
  memory. `Sheet.add_component(..., expect={...})` is pin-name insurance:
  use it for every IC.
- Idioms (shared helpers, use them instead of raw wires):
  `place_ic_with_decoupling` (one decoupler per power pin, values from the
  datasheet JSON; emits the decoupling metadata; on a SWITCHING regulator's
  input pin give every input cap `"role": "reg_input"` AND include an HF
  ceramic <= 1 uF - a bulk-only input passes value classing but ships the
  lumina-carrier R1 rework defect), `power_flag` /
  `power_symbol_at_pin` (rails are global power symbols - no sheet pin),
  `hier_pin` (cross-sheet signal nets; pin-stub and free-cluster variants),
  `wire_pin`/`wire_pins` (grid-snapped stubs + local labels).
- Refs unique ACROSS sheets: stay inside your assigned ranges (incl. #PWR
  via your pwr_base).
- Net names: sheet-local labels become "/NAME" (root) or "/<sheet>/NAME";
  when the FINAL netlist name differs from your wiring label (hier-crossed
  or root-local rails), pass `rail_net`/`gnd_net` overrides to
  place_ic_with_decoupling so the decoupling metadata records final names.
- Unconnected INPUT pins are an error - every input is wired, pulled, or
  explicitly no-connect flagged with a one-line justification comment.

## Verify before returning (all three)
1. Rebuild: run your generator (venv python) - it must write the sheet.
2. `scripts/kc.py erc --sch kicad/<top>.kicad_sch` (via the root build) or
   ask the orchestrator's top-sheet agent to run the full ERC if the root
   is not yours - your own sheet must at least build clean.
3. `scripts/netlist_audit.py --sch kicad/<top>.kicad_sch --constraints
   kicad/constraints.json --decoupling kicad/decoupling.json` when you own
   the root: exit 0 required (missing_net/metadata_mismatch are errors).

## Output contract (end your final message with exactly this block)
FILES: kicad/gen/<sheet>.py + built artifacts
GATE: erc/netlist_audit results if you ran them, else "not run (sheet-only)"
SUMMARY: <up to 10 lines: parts placed, interface nets exposed, decoupling
  count, anything ASSUMED>
OPEN: <pin-table surprises, datasheet gaps, or "none">
