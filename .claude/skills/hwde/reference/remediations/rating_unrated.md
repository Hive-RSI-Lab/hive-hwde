# rating_unrated

A part has connected pins that check_ratings could not check against any rating: no
datasheet extraction, no abs_max row that names or classes the pin, or a bound relative to a
supply whose net has no known voltage. Two-terminal parts with no extraction and no
voltage-rated attribute in parts.json are listed together in one finding. This is the
explicit form of "unknown": the check never passes a pin silently.

- Emitted by: scripts/check_ratings.py (kind="rating_unrated")   Gate: verify (warning; it
  does not fail the gate).
- Fixer domain: parts (cluster_violations.py).
- Fields: refs, pins (one line per pin: why it could not be checked).

## Is it real?
- A resistor, crystal or connector with no rating is usually fine on a low-voltage board.
  On a rail above about 30 V it is not: check each listed part's rating by hand.
- "no rating names it" on an IC pin often means the extraction's row names the pin
  differently (VIN vs IN, AVDD vs VDD). Add a `pin_ratings` entry rather than rewording.
- "relative to X, whose net has no known voltage": give the rail a voltage (net name,
  power_tree.md table, or constraints.json `voltages`).

## Fix ladder (cheapest first)
1. Rail voltage missing: add it to constraints.json `voltages`.
2. Row missing or unmatched: add `pin_ratings` entries to the part's extraction (pins, kind,
   level, min/max, source page) and run datasheet_extract.py --validate on it.
3. No extraction at all for an IC: run the datasheet-extractor on it (P3).

## Do not
- Do not invent a rating from memory; cite the datasheet page in `source`.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\datasheet_extract.py --validate <ws>\parts\<LCSC>.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_ratings.py --pcb <ws>\kicad\<board>.kicad_pcb --parts <ws>\parts
```

## Sources
- scripts/check_ratings.py docstring; docs/competitive-research.md backlog item 5.
