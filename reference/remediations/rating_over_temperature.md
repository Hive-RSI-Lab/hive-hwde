# rating_over_temperature

A part's operating temperature range doesn't cover the air the board sits in: a 0 to 70 C
commercial MCU on an ambient_c = 85 board, or a -20 C start (ambient_min_c) under a part
rated from -10 C. The part's own rise (check_thermal) comes on top, so a pass here is
necessary, not sufficient.

- Emitted by: scripts/check_ratings.py (kind="rating_over_temperature")   Gate: verify (P8).
- Fixer domain: parts (cluster_violations.py).
- Fields: refs [the part], limit_c (its bound), ambient_c (the air it was held against),
  rating (parts.json "Operating Temperature" or the extraction's operating_temp_c), and the
  report's `ambient` {c, min_c, src}: constraints ambient_c, the enclosure air check_thermal
  works out, or the 25 C default.

## Is it real?
- Check the ambient first: `ambient.src` says where it came from. An enclosure's box air is
  room_c plus the board's heat through the walls; a wrong box size or heat budget moves it.
- Check the range: LCSC's "Operating Temperature" is copied from the datasheet's ordering
  table and can name one grade of several. A "@(Tj)" range is the junction's: the air must
  sit below it by the part's own rise, so a pass is not a margin.
- An ambient_c written as the room, not the inside of the box, understates it.

## Fix ladder (cheapest first)
1. Pick the same part in its industrial (-40 to 85 C) or automotive grade (the suffix in the
   ordering table) through the part-sourcer: same footprint, same pinout.
2. Another part rated for the ambient.
3. Bring the ambient down: vents or a bigger box (check_thermal's enclosure), or move the
   hot parts away. Change constraints.json only when the board's real ambient changed.

## Do not
- Do not lower ambient_c to make it pass: check_thermal, ripple life and every derating read it.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```

## Sources
- docs/ingest/safety-components.md R-REL-2 (Littelfuse PolySwitch selection guide step 5,
  Vishay D/CRCW e3 operating range, every datasheet's ratings table).
