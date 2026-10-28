# move - relocate a footprint (or its label)

Two variants, and the difference is ceremony: moving a FOOTPRINT is `move_fp`
(hold 1, five gates); moving a refdes/value LABEL is `silk_edit` (hold 0, three
gates). The router picks the silk variant when the task names refdes / silk /
label / text.

## Footprint moves

`place_edit.py` is the only writer. Ops are ABSOLUTE and idempotent:

    {"version":1,"ops":[{"op":"move","ref":"C12","x":141.2,"y":98.5}]}
    {"op":"rotate","ref":"U1","deg":90} | {"op":"flip","ref":"J2","side":"back"}
    {"op":"lock","ref":"J1","locked":true}

It validates, edits a scratch copy, re-parses to verify every op landed
(1e-3 mm / 0.05 deg), and only then swaps the file in. Any failure leaves the
board byte-identical. KiCad regenerates UUIDs on save, so compare parsed
positions, never file hashes.

## The two things nothing does for you

1. **Old copper stays put.** `--allow-routed` is required on a routed board and
   it means exactly what it says: the tracks that used to reach those pads are
   still where they were. Re-route the affected nets (`reroute-net`) before
   believing `drc_routed`. Unconnected items in DRC are the honest signal.
2. **Neighbour silk does not move.** The footprint's own silk travels with it;
   the label you slid out of its way three edits ago does not. `check_silk`
   (verify, P8) catches the overlap, DRC does not.

## Zones

Any copper-adjacent edit stales the pours. The only headless filler at this pin
is `kicad-cli pcb drc --refill-zones --save-board` (kc.py `drc --refill --save-board`);
`drc_routed` refuses stale fills outright rather than grading phantom clearance
errors.

## Where to move it TO

The router does not choose coordinates - that is placement judgment. Cheapest
sources, in order: the violation's own coordinates (a decoupler that is 21 mm
from its pin wants to be at the pin), `place_metrics.py` for legality after the
move, and `placement` (the agent) when the move cascades into its neighbours.

## Do not

- Do not move a locked footprint without saying so - a lock is usually a
  mechanical constraint someone paid for.
- Do not batch a move with a re-route in one snapshot. Snapshot before the move;
  the re-route is its own step with its own gate.
