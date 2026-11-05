# rating_regulator_output

A regulator's output pad sits on a rail its parts.json row says it can't make: a fixed 3.3 V
LDO whose OUT pad is on +5V, or a 768 mV to 7 V buck on +12V. Either the wrong part was
sourced or the wrong net is on the pad. Nothing else compares them: the rail's volts come from
its name or constraints.json, and the part is never asked.

- Emitted by: scripts/check_ratings.py (kind="rating_regulator_output")   Gate: verify (P8,
  via verify_all.py); runs from P4 on.
- Fixer domain: parts (cluster_violations.py).
- Fields: net, refs [the part], pin, net_v (the rail's volts), limit_v (the most it makes),
  rating (the attribute or MPN it was read from).
- Judged for a part with no extraction, a filled row and a pad named OUT/VOUT/VO in the
  netlist; and for an extracted regulator's power_out pin named OUT/VOUT/VO/OUTPUT, against a
  rail whose volts come from constraints.json, the power tree or the net name. A fixed output
  is allowed 10 % from the rail, a range or list its whole span. A rail's sign is ignored: a
  -5.0 part on -5V passes.
- An extracted regulator's output is read from the row, then a dotted or 78xx MPN, then the
  extraction's `output_voltage`, a "fixed 3.3V" in its description, or a known family's MPN
  code (RT9080-33, TLV75733, XC6206P332, HT7333, ME6211C33). If none of them says, it is
  rating_regulator_output_unjudged instead.

## Is it real?
- Check the row: `Output Voltage` is what the listing says. An adjustable part listed by one
  example voltage reads as fixed; add its range to the row.
- Read from an MPN code: check the code against the datasheet's ordering table.
- Check the rail's volts: a net NAME is a guess. Put the real figure in constraints.json
  `voltages`.

## Fix ladder (cheapest first)
1. Wrong part in the row: source the variant that makes the rail (AP2112-3.3, not -5.0).
2. Wrong net on the pad: fix the schematic.
3. An adjustable part: set its feedback divider and give the row its real range.

## Do not
- Do not rename the rail to match the part unless the rail really is that voltage: every
  other check reads it too.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```
