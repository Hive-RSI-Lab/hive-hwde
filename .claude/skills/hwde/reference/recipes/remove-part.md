# remove-part - delete a part and the copper it leaves behind

Same chain as `add-part` in reverse: schematic, netlist, `board_update.py`. The
value here is the orphan analysis - the part of a delete that is normally done
by eye and done wrong.

## What "orphaned" means (and what it does not)

Orphans are found by CONNECTIVITY, not proximity: a netconn graph plus local
via-in-pad joins. A track endpoint inside a via or pad is connected; inside
another track's BODY is not (T-junction lobes on a real board pass DRC at
0/0 - body overlap is not evidence either way). Dangling chains are pruned to a
fixpoint, and zero-length crumbs under the dead pad are judged by direct copper
touch.

The analysis is BASELINE-SUBTRACTED: copper that was already unanchored before
your edit is reported under `orphans.netconn_unanchored_kept` and deliberately
left alone. Read that list in the dry-run - it is the honest "I am not touching
this" declaration, not a miss.

## The rollback gate

After the surgery the driver refills zones and runs DRC. More `track_dangling` /
`via_dangling` than before -> the whole edit rolls back byte-identically. So a
failed delete costs you nothing but the report; read it and decide whether the
copper it wanted to leave is actually orphaned.

Silk inside the deleted footprint's bbox goes with it (the worker reports the
FILE layer tokens it stripped, e.g. `F.SilkS`, so the verification is real).

## The electrical half

Removing a decoupler, a clamp or a bleeder changes the design in a way DRC
cannot see. `verify` is the arbiter: `check_pdn` (undecoupled rail, no bulk
reservoir) and `check_decoupling` are in the gate set for this class. If the
part was there for a reason nobody wrote down, ask before deleting.

## Do not

- Do not "remove" a part by deleting its footprint on the board only. The
  schematic is the source of truth; a board-only delete fails parity at the
  next drc gate and silently keeps the BOM line.
- Do not use this to DNP a part for one build. That is a BOM decision
  (`bom_cpl.py` inputs), not a board edit.
