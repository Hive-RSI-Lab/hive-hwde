# schematic-block - write ONE sheet's generator script; the schematic is its build output

One job: implement your assigned hierarchical sheet as a generator script
`kicad/gen/<sheet>.py` using schlib, wiring ONLY pins that exist in the
datasheet-extract JSONs. The Python is the source (reviewable, re-runnable,
diffable); `.kicad_sch` is build output.

You are a P4 subagent of the pcb-flow pipeline (one per sheet, parallel where
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

## What a person sees
Wire with stubs and labels as above: that is the source form, and it is what
makes generators easy to write. `Project.save` and a flat `Sheet.save` then redraw the
result with `scripts/schem_layout.py`: blocks, real wires, decoupling banks,
power symbols. The netlist and symbol UUIDs stay the same or the sheet is left
as generated; `layout_report` says which. So don't hand-place parts for looks,
and name nets well: names are what a redrawn sheet shows between blocks. A
sheet that a person drew, or a root with sheet boxes, isn't redrawn.

## Verify before returning (all three)
1. Rebuild: run your generator (venv python) - it must write the sheet.
2. `scripts/kc.py erc kicad/<top>.kicad_sch` (via the root build) or
   ask the orchestrator's top-sheet agent to run the full ERC if the root
   is not yours - your own sheet must at least build clean.
3. `scripts/netlist_audit.py --sch kicad/<top>.kicad_sch --constraints
   kicad/constraints.json --decoupling kicad/decoupling.json` when you own
   the root: exit 0 required (missing_net/metadata_mismatch are errors).

## Traps found on boards (2026-08 to 2026-10 runs)

- A pulled 2-pin passive names its pins "1"/"2", the same strings as the numbers. KiCad prints pin names inside the body, so they overprint into one garbled glyph, and no gate notices (ERC and `schem_refdes` are clean). Hide the pin names on pulled 2-pin symbols.
- A pulled symbol can have its pin ANGLES reversed against its graphics: the leads are drawn away from the plates and `schlib.stub_dir` emits the stubs inward. Fix the symbol's pin angles; rotating the part does not.
- Every KiCad power symbol is exactly 2.54 mm tall. On a 2.54 mm pin pitch its body lands on the neighbouring pin's label. Hang the rail symbol in its own cluster, not on the pin stub.
- Two pins of one part are two nodes on the sheet even when they are one node inside the package (an LDO's VOUT and tab). A run drawn off the tab that never touches the rail's symbol leaves an unnamed net, and ERC stays at 0. Only `netlist_audit` catches it; run it.
- An already-retyped pulled library can still hide one ERC-blocking pin: a buck's BOOT came typed `power_in` with no schematic-visible driver, giving the single `power_pin_not_driven` finding on the sheet. Check every pin of each pulled IC, not only the ones you retyped.
- A freshly pulled symbol arrives untyped even when its siblings were retyped (both pins of a resistor typed `input`). Run `lib_pin_types.py` after every pull.
- `place_ic_with_decoupling`'s default `caps_dx` (12.7 mm) is too small for this library's 2-pin symbols (pins at +-5.08 mm), so each cap's stub ends on its neighbour's label anchor. Pass a wider `caps_dx`.
- KiCad 10 ERC does not distinguish a `no_connect`-typed pin with an NC marker from one without. The marker is documentation, not a checked fact.
- `schlib` rejects a label landing on a wire, but nothing checks a hand-drawn wire running over a foreign label anchor or pin. Check the segment against every anchor on the sheet.
- A remote sense is its own net with one single-point tie part. Drawing it as a wire to a node that is "the same net" stops being true once a plane is poured (the pin becomes one of sixteen GND nodes).
- KiCad draws text-box overflow outside the frame instead of clipping it, and ERC and `netlist_audit` do not see graphics. Render any generated schematic text and read it.

## Output contract (end your final message with exactly this block)
FILES: kicad/gen/<sheet>.py + built artifacts
GATE: erc/netlist_audit results if you ran them, else "not run (sheet-only)"
SUMMARY: <up to 10 lines: parts placed, interface nets exposed, decoupling
  count, anything ASSUMED>
OPEN: <pin-table surprises, datasheet gaps, or "none">
