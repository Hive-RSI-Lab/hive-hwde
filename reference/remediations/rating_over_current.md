# rating_over_current

A part carries more current than it is rated for at the board's ambient:
- a regulator or driver output pin (power_out) feeding a net whose constraints
  `power[].current_a` is above the pin's rated current (warning);
- a fuse past its rerated current I_rated x k_T (error) or past 75 % of it (warning: a fuse
  run that hard ages and opens on its own; Littelfuse 881: k_T = 0.90 at 70 C);
- a PPTC whose hold current at the ambient, Ihold(23 C) x the derating table, is under the
  current (error: it trips in normal running) or clears it by under 10 % (warning).

- Emitted by: scripts/check_ratings.py (kind="rating_over_current")   Gate: verify (P8).
- Fixer domain: parts (cluster_violations.py).
- Fields: refs, net, limit_a (the rating at the ambient), current_a, ambient_c; the report's
  `fuses` lists every fuse and PPTC with what it was rated, carried and rerated to.

## Is it real?
- The current is constraints power[].current_a on either of the part's nets: the worst-case
  continuous current, not the inrush. Check it is the current through this part.
- The PPTC table is typical (Bourns MF-MSMF/MF-NSMF, Littelfuse PolySwitch chip devices).
  The part's own derating table wins: a waiver citing that table's figure at the ambient is
  the right answer when it holds.
- The ambient: the report's `ambient.src`. Without ambient_c it is 25 C.

## Fix ladder (cheapest first)
1. The next current rating up in the same package (same footprint) through the part-sourcer:
   I_rated >= I_op / (0.75 x k_T) for a fuse, Ihold >= 1.1 x I_op / k(T) for a PPTC.
2. A bigger package (1206 -> 1812 PPTC) for a hold current the small one can't reach.
3. For an output pin: a part with the current, or split the load.

## Do not
- Do not lower current_a or ambient_c in constraints.json to make it pass; other checks
  (track width, thermal) read them.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```

## Sources
- docs/ingest/safety-components.md R-RAT-3 (Littelfuse Fuseology Design Guide, 881 series)
  and R-RAT-4 (Bourns Multifuse selection worksheet, Littelfuse PolySwitch selection guide).
