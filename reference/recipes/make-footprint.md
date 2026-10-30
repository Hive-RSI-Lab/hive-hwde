# make-footprint - get a part into the project library

The pull is one command; the value is in what happens around it.

## The pull

`lib_pull.py --lcsc <id> --project <ws>/kicad` fetches symbol + footprint +
3D model via easyeda2kicad (`--no-3d` opts out), registers them in the project's
`fp-lib-table` / `sym-lib-table` with portable `${KIPRJMOD}` URIs, and applies
the fixes that raw pulls always need:

- silk pulled back from copper (raw pulls ship silk dots INSIDE pad 1 - a
  guaranteed DRC error on every instance),
- refdes normalization (blanket offset, so P4 does not open with 40 overlapping
  designators),
- `--verify-load` confirms KiCad can parse it, `--verify-drc` measures the silk
  fix with a REAL DRC rather than trusting the fixer.

Pin electrical types from easyeda2kicad are junk (`unspecified` nearly
everywhere), which floods ERC with pin_to_pin warnings and false
`pin_not_driven` errors. Fix that at the SOURCE (retype the symbol pins), never
by loosening the .kicad_pro severities - the erc gate is errors AND warnings = 0
precisely so this class cannot hide.

## The land pattern is a datasheet question

`fp_verify.py` compares the pulled pads against a datasheet extraction:
pad count / pin 1 / pitch are ERRORS, pad size and a missing courtyard are
warnings. Feed it a validated extraction - `datasheet_extract.py --pdf` produces
the grounding payload (per-page text + schema + template) for the extractor
agent, and `--validate` rejects a filled one with precise paths. Never let a
land pattern come from memory.

The SVG overlay (`--svg`) is for a human eyeball, not for the gate.

## Castellated edge pads (the board's own half-holes)

A board that solders down by its edge (a Pico-style module) needs plated
half-holes on its outline, not a pulled part. `scripts/castellated_fp.py
--pins N --pitch P --out {ws}/lib/aiee.pretty/<name>.kicad_mod` writes the
row: every pad carries `(property pad_prop_castellated)`, sizes default to
JLC's recommended values and anything below JLC's minimums refuses (both in
the `castellated:` block of reference/jlc_capabilities.yaml, with sources).
Place it with its origin ON Edge.Cuts (local +Y = board interior) and rotate
for other edges. From then on dfm_check checks the half-holes (on the
outline, spacing, ring, corners), the quote adds the castellated surcharge,
the order sets `castellatedHoles` 1, and the report says "castellated". A
board without that property is never called castellated, whatever the brief
says - a plain PTH row is a deviation the owner accepts, not a claim.

Known limits: JLC publishes no fixed castellated surcharge, so the quote's
adder is one forum-reported point; `castellatedHoles` 1 has not been through
a live calculate yet; and the corner and other-edge checks need a cornered
outline, so a fully rounded outline skips them.

## When the librarian is needed

Scripted fixes cover silk, refdes and the common passives. Spawn `librarian`
for the residue: plated pegs, DIP switches, exposed pads, connectors whose
mechanical drawing disagrees with the pulled pattern. A pad-geometry failure
blocks P4 - it is cheaper here than after a re-route.

## Do not

- Do not pull a part you have not price/stock checked (`parts_search.py`):
  Basic beats Extended, and out-of-stock beats nothing at all.
- Do not register a library directory that does not exist yet - a dangling
  lib-table entry is DRC noise on every subsequent run.
