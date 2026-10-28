# rating_over_voltage

A pin sits on a net whose voltage is above the pin's absolute-maximum rating in the part's
datasheet extraction - a 3.6 V-max MCU input on a 5 V rail, an EN pin wired straight to a
24 V input, a 6.3 V capacitor across 12 V. When the bound is relative (VDD+0.3), the message
says "input above supply": the pin sees more than its own supply allows. No DRC sees this;
it is a respin.

- Emitted by: scripts/check_ratings.py (kind="rating_over_voltage")   Gate: verify (P8, via
  verify_all.py); runs from P4 on, since it needs only the netlist and the parts dir.
- Fixer domain: schematic (cluster_violations.py).
- Fields: net, refs [the part], pin ("U1.10"), net_v (the rail's volts), limit_v, rating (the
  extraction row it was checked against), and in msg where the rail's volts came from
  (constraints / power_tree / regulator / net name / pull-up). Error, except a volt inferred
  through a pull-up resistor, which is a warning.

## Is it real?
- Check the rail's volts first: the msg names the source. A net NAME is a guess (+5V_EN is an
  enable signal, not a 5 V rail); put the real worst case in constraints.json `voltages`.
- Check the row: the rating quoted is the extraction's, parsed from free text. A wrong pin
  match, or an electrical-characteristics figure parked in abs_max, reads as a limit. Open the
  datasheet page; if the extraction is wrong, fix the extraction (or add a `pin_ratings`
  entry, which wins), re-validate it, and re-run.
- A 5 V-tolerant pin rated by the "any other pin" row: mark the pin FT in its pinout notes so
  the tolerant row applies.
- A pull-up warning through a series resistor into a clamped pin can be deliberate (a
  datasheet that allows it with an R limit). Confirm the datasheet says so, then waive it
  with the page cited.

## Fix ladder (cheapest first)
1. Wrong rail: move the pin to the rail it was meant for (3V3, not 5V) in the schematic.
2. The signal must come from the higher rail: a resistor divider for slow inputs, a series
   resistor plus the datasheet's allowed clamp current, or a level shifter for fast or
   bidirectional lines (I2C: pull up to the lower rail instead).
3. A capacitor or other passive under-rated: pick the next voltage rating up (derate
   ceramics by at least 2x at DC bias) through the part-sourcer.
4. The part cannot take the rail at all: re-source a part rated for it.
After P5 any added part is board surgery - see reference/recipes/add-part.md.

## Do not
- Do not lower the rail's volts in constraints.json to make it pass; `voltages` is the worst
  case and other checks (creepage) read it too.
- Do not delete the abs_max row. Fix a wrong row against the datasheet page.
- Do not waive an error that comes from constraints or a regulator output.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_ratings.py --pcb <ws>\kicad\<board>.kicad_pcb --constraints <ws>\kicad\constraints.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate verify <ws>\kicad\<board>.kicad_pcb
```

## Sources
- docs/competitive-research.md backlog item 5 (Cadstrom-style datasheet rating check).
- scripts/check_ratings.py docstring: rail sources, row parsing, tiers.
