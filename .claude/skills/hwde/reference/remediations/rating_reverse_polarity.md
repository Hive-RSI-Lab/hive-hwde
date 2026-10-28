# rating_reverse_polarity

A pin sits below its absolute-minimum rating (a rail negative where the part allows
-0.3 V), or a ground pin sits on a positive rail. The usual cause is a swapped connector,
a reversed polarised part, or a negative rail routed to a single-supply part.

- Emitted by: scripts/check_ratings.py (kind="rating_reverse_polarity")   Gate: verify (P8,
  via verify_all.py); runs from P4 on.
- Fixer domain: schematic (cluster_violations.py).
- Fields: net, refs [the part], pin, net_v, limit_v (for the absolute-min case), rating.
  Error, except a volt inferred through a pull-up, which is a warning.

## Is it real?
- A ground pin "on a positive rail": check the net really is a rail and not a ground with a
  name the check could not read (V48_RTN is ground; a net named after a voltage is not).
  Rename the net or declare it in constraints.json `voltages` with 0.
- A negative-rail part (op-amp V-, gate driver VEE): the extraction's pin type decides. A
  negative supply pin typed power_in with an abs_max row "-0.3 to ..." is an extraction
  error; fix the row or add a `pin_ratings` entry rated against the right reference.
- Pair ratings (VGS, BST-SW) are checked between the two pins' nets; a P-channel figure is
  printed negative and read as a floor.

## Fix ladder (cheapest first)
1. Swapped pins or connector: fix the symbol-to-footprint mapping or the connector pinout.
2. Reversed input from outside the board: add reverse-polarity protection (a series
   Schottky, or a P-FET ideal diode for low drop) at the input.
3. Negative rail on a single-supply part: re-source a part that allows it.

## Do not
- Do not flip the rail's sign in constraints.json to silence it.
- Do not waive a reversed ground pin; that is a short across the rail.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_ratings.py --pcb <ws>\kicad\<board>.kicad_pcb --constraints <ws>\kicad\constraints.json
```

## Sources
- scripts/check_ratings.py docstring; docs/competitive-research.md backlog item 5.
