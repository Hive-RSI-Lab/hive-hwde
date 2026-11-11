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

## A part added by hand: check what the library cannot tell you

An EasyEDA footprint or symbol cannot be trusted for pad count or supply
voltage. One oscillator's pulled footprint had 6 pads and tied its N.C. pin to
GND; the real part has 4 terminals. Read the maker's sheet for the exact
ordering code.

- A distributor's voltage range is for the series, and can disagree with the
  sheet for one ordering code (a 1.7-3.3 V listing for a part whose code is
  3.0 V +-5%, abs max 4.0 V). Run it at the sheet's voltage.
- A footprint added with your own pcbnew script (board_init and board_update
  hide these fields; a script of yours does not) comes in with `LCSC` and
  `MPN` visible on F.Silkscreen, 1.27 mm text up to 28 mm wide. The sign:
  hundreds of `silk_overlap` warnings whose items span far beyond the part,
  and failing dfm silk. Hide those fields. `fp.SetField(name, value)` on a
  loaded footprint makes that field visible too, so hide it in the same
  script. A copied footprint also carries the old `LCSC Part` value: set it.
- The board's `LCSC Part` field and the schematic's `LCSC` field can drift
  after a swap, so the BOM and the stock check read different parts. Sync them
  and diff the BOM after any swap.
- Changing a land means re-checking the courtyard: pads widened past their
  courtyard raise a new `check_courtyard` error that DRC does not show. Change
  the library footprint and the placed copy together.
- A dfm `wrong_offset` naming a part you moved, at a distance equal to the
  move, means the position CSV is stale: re-export fab before rerunning dfm.
- `check_model_fit` can read marking shapes on a 3D model as leads. Check by
  eye before moving a land because of it.
- A new power trunk can ring off a piece of the F.Cu GND pour. DRC reports an
  unconnected zone against itself at no useful position: look for fill
  polygons with no GND via, then add one.

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
