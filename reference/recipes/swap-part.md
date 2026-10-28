# swap-part - a different value, MPN, or package on an existing refdes

The board is not edited directly: the SCHEMATIC changes, the netlist is
re-exported, and `board_update.py` diffs that netlist against the board. That
diff is what decides which of the two classes this really is.

## same_fp vs new_fp

- `swap_part_same_fp` - value/MPN only, footprint unchanged. Fields, BOM and
  CPL move; the geometry is byte-stable. Hold 2 (the electrical meaning
  changed - a human should see it), gates erc/verify/sim/dfm.
- `swap_part_new_fp` - the footprint changed. board_update composes it as
  del+add at the old spot: the old package's copper stubs are RIPPED and not
  reused, and board-only fields on that footprint are dropped. Expect to
  re-route. Gates add place/drc/drc_routed.

Always run `--dry-run` first and read `plan.*`: it tells you which class it
found, and `plan.unsupported` (exit 1) means the diff contains something this
tool will not do - pad-net rewires and net renames, which are a schematic/route
job, not a surgery.

## Sourcing the replacement

`parts_search.py` ranks Basic-first, then stock, then price. An Extended part
costs an assembly feeder, so a Basic equivalent is worth a small parametric
compromise; say which you chose and why (`state.py decision`). If the part is
not in the project library yet, run `make-footprint` first - board_update needs
the fpid to resolve.

## After the swap

BOM and CPL are stale by the map the moment a value changes. Re-run `bom_cpl.py`
and then `state.py rehash --names bom cpl` - a BARE rehash keeps the marks (it
only clears where the hash moved), which is what you want everywhere else.

If a `sim` bench references the swapped part's value, its bounds are now wrong.
The sim gate is in the class's gate list for exactly this reason: a 47k where
4.7k belongs is invisible to ERC, DRC, verify and DFM.

## Do not

- Do not edit the footprint field to "fix" a mismatch the librarian should fix.
  A wrong land pattern is `make-footprint` + `fp_verify`, not a swap.
- Do not swap a part after the order latch without re-running the whole gate
  set. The latch binds to the design hash and will refuse - correctly.
