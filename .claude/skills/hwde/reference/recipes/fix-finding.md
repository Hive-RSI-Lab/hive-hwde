# fix-finding - close a violation through the standard loop

The loop is the same for every gate: budget -> snapshot -> dispatch -> fixers ->
declare the edit -> re-gate -> commit. It is in SKILL.md because every other
recipe falls into it; this file is the part that changes per finding.

## The findings file

`--findings` takes a gate result (`reports/gate-<g>.json`, has `failing`), a
check report (`reports/checks/<check>.json`, has `violations`), or a cluster
payload. `fix_dispatch.py` accepts all three shapes and attaches each cluster's
`reference/remediations/<kind>.md` to the work order - the fixer gets the
knowledge without anyone remembering to paste it. `counts.with_remediation` in
the dispatch summary tells you how much of the batch is covered.

## Declare the class the fix ACTUALLY belongs to

`state.py edit --class <c>` is what stamps derived artifacts stale, so guessing
here is how a stale gerber ships. The fixer's domain maps to the class:

| fixer domain | edit class | why |
|---|---|---|
| router (tracks/vias) | reroute_net | copper moved |
| placement (footprint move/rotate) | move_fp | placement + copper both suspect |
| silk (labels, refdes) | silk_edit | hold 0 - cosmetics |
| plane (zone outline/priority) | plane_edit | return paths move; hold 2 |
| outline (board size/edge) | outline_change | hold 2 - and `board_edit.py` records it ITSELF; do not also run `state.py edit` |
| schematic / parts (value, MPN) | swap_part_same_fp | BOM/CPL + netlist stale |
| library (new footprint on a part) | swap_part_new_fp | geometry changed |
| fab (rules, floors) | rule_change | .kicad_pro / .kicad_dru |

If the fix was purely a waiver or a report edit, there is no class - record a
`decision` instead.

## Order of re-gating

Re-run the gate that failed. Then, if copper or placement moved, re-run
`drc_routed` BEFORE `verify`: a fix that quiets P8 while breaking P7 is the
classic regression, and the freshness marks will not save you from running them
in the wrong order.

## Budgets and escalation

`fix_loops.<gate>` defaults to 3, and it is a counter: `state.py budget
--consume` past it reports `over: true` and goes on counting. Whether a fourth
round is worth it is your call - it is when each round removes violations or
finds a new cause, it is not when the same ones come back. Then change
approach: a render, the remaining violations WITH coordinates, what was tried,
and the next move you pick (another fixer tier, an upstream change, a
cross-stage spawn). Record it (`state.py decision`). Only waiving an error on a
board that will be built goes to the person.

Two special cases:
- `cleanup_regression` (route_cleanup exit 1): restore the snapshot and continue
  WITHOUT cleanup. It is optional by design.
- `requires_pipeline_rewind` (the fix needs a schematic or library change after
  P5): weigh the tradeoff and pick; a cost/feature trade goes to the person. A rewind re-enters at P4/P5 for the affected
  scope - which, since T8, is usually `add-part` / `swap-part` instead of a
  full re-place.

## Run close

A fix loop is where the checks get caught being wrong - a finding that was a
modelling artefact, a remediation that armed the next phase's trap. Append
those to `~/dev/boards/<b>/LEARNINGS.md` (tag them with the phase they fired in) and
compile the queue:

    learnings.py compile --workspace ~/dev/boards/<b>

The `promote` verb rules on them later; a finding you argued with and waived is
exactly the kind of entry that turns into a check threshold.

## Do not

- Do not hand two fixers the same board region concurrently. `parallel_groups`
  in the dispatch summary is the safe set (regions disjoint by bbox + 1 mm);
  when in doubt, serialize - correctness beats wall clock.
- Do not skip `record-gate` on a FAILED attempt. The history is the audit trail
  and the freshness hashes come from it.
