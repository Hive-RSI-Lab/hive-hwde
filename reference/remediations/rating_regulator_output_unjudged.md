# rating_regulator_output_unjudged

An extracted regulator has an output pin on the board, but nothing says what it makes: not its
parts.json row, its extraction, or its MPN. So rating_regulator_output couldn't compare it with
its rail. A warning, not a pass: the part may be on the wrong rail.

- Emitted by: scripts/check_ratings.py (kind="rating_regulator_output_unjudged", warning)
  Gate: verify (P8, via verify_all.py); runs from P4 on.
- Fixer domain: parts (cluster_violations.py).
- Fields: net (its first output net), refs [the part].
- A part is a regulator here if its U/VR/IC ref comes with the row's or the extraction's words
  (LDO, regulator, buck, boost, DC-DC, PMIC). An eFuse, load switch, power path, charger,
  reference, hot-swap or supervisor is not warned.

## Is it real?
- Check the datasheet: a fixed part has one output, an adjustable one is set by its divider.
- A part that isn't a regulator and whose words say it is: correct its description.

## Fix ladder (cheapest first)
1. A fixed part: set `output_voltage` in parts/<lcsc>.json (volts), from the datasheet.
2. An adjustable part: give the parts.json row its `Output Voltage` range.
3. Filled row, still warned: the attribute's name must hold "Output Voltage" or "Voltage - Output".

## Do not
- Do not guess the voltage from the rail it sits on: that makes the check pass by construction.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```
