# mating_faces_inward

A connector a plug cannot reach (mating_mouth_inset and mating_zone_blocked
are the same family and take the same fixes). Error severity: fails the P6 `place` gate
and the P8 `verify` gate (check_mating).

- Emitted by: scripts/lib/matinglib.py, through place_metrics.py (family
  `mating`) and check_mating.py. Families and plug sizes:
  reference/connector_mating.yaml.
- Fixer domain: placement. Scripts: place_edit.py, render.py.
- Fields: `connector` (the connector's ref), `mouth` and `nearest_edge`
  (+x/-x/+y/-y, board frame, y down), `gap_mm` (board between the mouth and
  the edge it faces), `blocked_by` (parts in the insertion zone).

## Is it real?
- The mouth is read from the pad layout: the contact row is the back, the
  body reaches toward the mouth. A footprint that breaks that rule reads
  wrong. Check the side render (`render.py --views left,right`) before
  turning a part; if the reading is wrong, add a `mouth` entry for the
  footprint to the table rather than waiving.
- `mating_faces_inward` with a long `blocked_by` is one fault, not many:
  turn the connector and the zone empties.

## Fix ladder
1. Faces inward: rotate the connector 180 degrees (or 90 to the edge it
   should use), keeping its back where the routing expects it, and pull the
   mouth to the edge. A board's declared edge in constraints.json says which.
2. Inset: move it toward the edge until the courtyard front is at or over
   it (copper-to-edge still applies to its pads).
3. Zone blocked: move the named parts out of the zone; for a vertical
   header, move tall parts more than finger_mm away.

## Do not
- Waive it because DRC, DFM and the render review passed: PCB-0021-A J4 and
  PCB-0018-A J701/J702 passed all of them facing the wrong way.
- Move a board edge or shrink the table's plug length to make it pass.

## Verify
- `scripts/place_metrics.py --pcb <board>`: coverage.failed has no `mating`.
- `scripts/check_mating.py --pcb <board>`: status pass.
- Side render shows the mouth at the edge with free air in front of it.

## Sources
- Owner, #ai-ee 2026-10-02: PCB-0021-A J4 and PCB-0018-A J701/J702 shipped
  with their mouths into the board; LEARNINGS 2026-10-02 [placement][connector].
- .claude/skills/hwde/scripts/lib/matinglib.py - rules and geometry
