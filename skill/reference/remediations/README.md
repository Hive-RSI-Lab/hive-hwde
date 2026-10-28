# remediations - knowledge indexed by TRIGGER, not by topic

One file per FINDING TYPE: `<check_id>.md`, where `<check_id>` is the violation
`kind` (custom checks) or the kicad-cli DRC/ERC check name - the same key
`cluster_violations.kind_of()` dispatches on.

Why keyed that way: at runtime an agent never knows it "needs EMI knowledge".
It knows `insufficient_transition_vias` fired 6 times. Retrieval is then a
deterministic file lookup, not a search (design/routing-knowledge-notes.md 6).

## How it reaches the fixer

`fix_dispatch.py` attaches every ref matching a cluster's `kinds` to the work
order (`remediations: [...]`, plus a guidance line telling the fixer to read it
first). Lookup is FILE EXISTENCE - dropping a new `<kind>.md` here wires it in;
no table to update, nothing to redeploy.

## Coverage (T4, 2026-08-06)

Written for every check_id that fired >= 100 times across the six committed
board workspaces (cumulative tally over all report JSONs, fix-loop
intermediates included):

| check_id | fired | boards | domain |
|---|---|---|---|
| unconnected_items | 8574 | 4 | router |
| undersized_track | 1433 | 1 | router |
| clearance | 1145 | 2 | router |
| insufficient_transition_vias | 1072 | 1 | router |
| silk_overlap | 680 | 4 | silk |
| dfm_trace_width | 377 | 1 | router |
| creepage | 365 | 1 | placement |
| track_width | 365 | 1 | router |
| lib_footprint_issues | 333 | 1 | library |
| silk_over_copper | 319 | 4 | silk |
| corridor_void | 284 | 1 | plane |
| silk_edge_clearance | 159 | 4 | silk |
| copper_edge_clearance | 112 | 3 | router |
| track_dangling | 105 | 1 | router |

Next candidates by volume (uncovered): pin_to_pin 60, outside_outline 57,
hole_clearance 51, solder_mask_bridge 49, pour_neckdown 48, via_dangling 44,
decoupler_distance 39, keepout_violation 34, shorting_items 31, hole_to_hole
24, diffpair_skew 23.

## Adding one

Copy the section order of any existing file (title = the check_id, then
`## Is it real?`, `## Fix ladder (cheapest first)`, `## Do not`, `## Verify`,
`## Sources`). Bar for inclusion, enforced by `tests/test_remediations.py`:

- the filename is a kind in `cluster_violations.FIXER_HINTS` (else it can
  never be loaded);
- ASCII, <= 90 lines - a fixer reads this mid-run;
- every script named exists, every `--flag` shown exists in that script;
- every `(line N)` / `(1553)` / `(LEARNINGS 628-636)` citation lands inside a
  real LEARNINGS entry, a hyphenated range stays within ONE entry, and the
  entry's date matches any date given next to it (this is what catches a
  citation that drifted onto the wrong entry).

Content rule: measured facts and their anchors only. If the check itself is
known-blind (several are), say so under "Is it real?" and tell the fixer what
to do instead - do not paper over it. Prose that a script could enforce belongs
in the script (design/ladder-triage.md).
