# add-part - fit a part onto a board that is already placed and routed

Until T8 this cost a full re-place and re-route, which is why two real boards
shipped with a deferred decoupler and no clamps. It does not any more:
`board_update.py` inserts the footprint and PRESERVES existing copper.

## The chain

Schematic first (the netlist is the contract), then export, then surgery:

1. Library: if the part is new, `make-footprint` - board_update resolves the
   fpid against the project libraries and will refuse an unknown one.
2. `schematic-block` adds the symbol and wires it. A floating pin or a missing
   PWR_FLAG fails `erc`, which is the point of gating here rather than later.
3. `kc.py netlist` exports; `board_update.py --dry-run` proves the diff is
   exactly the add.
4. Apply with `--placements`:
   `{"C99": {"region": [x1,y1,x2,y2]}}` or `{"C99": {"x": .., "y": ..}}`.
   The region scan lands it courtyard-legal and clearance-clear (0.2 mm pad
   clearance), and resolved courtyards feed the next scan's obstacles, so a
   multi-part add does not stack them on one spot. **Front side only.**

## The new pads arrive netted and unrouted

That is by design: DRC's unconnected items ARE the ratsnest. Route them through
`reroute-net` or the fix loop (route_edit for a via + a couple of segments is
usually enough for a decoupler; the standard loop took a replaced cap to DRC
0/0 in the T8 acceptance).

## Choosing the region

A decoupler's region comes from its IC pin, not from free space:
`check_decoupling` classes by value and measures Manhattan pad-to-pin, warning
around 5 / 10 / 20 mm (hf / mid / bulk) with an error threshold above each, plus
a loop-inductance budget. Ask for a region around the pin and let the scan find
the legal spot inside it. If nothing fits, that IS the finding - report it
rather than dropping the part 15 mm away.

## After

BOM and CPL gain a line: re-export both, then `state.py rehash --names bom cpl`.
The edit class is `add_part` (hold 2, the full gate set including `sim`).

## Do not

- Do not add parts one at a time when you know you need three. One netlist, one
  board_update invocation, one gate cycle.
- Do not use this to add a MECHANICAL feature (mounting hole, fiducial) that has
  no symbol - that is a board_init/outline change, and there is no outline-shrink
  step: the P5 outline is final.
