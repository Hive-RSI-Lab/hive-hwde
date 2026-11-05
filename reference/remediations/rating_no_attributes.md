# rating_no_attributes

A rated part's parts.json row has empty `attributes` and nothing else rates it, so its limit
was never compared with the board. Rated here means a diode (D*, LED*), a fuse (F*, FU*), a
cap (C*) or a regulator (U*, VR*, IC* with a fixed output in the MPN, or a row that says so).
On precision-daq, filling these rows showed D101 reversed by 5.25 V over its 5 V stand-off,
and F101's hold current against the 0.6 A rail (#675).

- Emitted by: scripts/check_ratings.py (kind="rating_no_attributes")   Gate: verify (error).
- Fixer domain: parts (cluster_violations.py).
- Fields: refs (every ref on the BOM code), lcsc, mpn. One finding per BOM code.

## Is it real?
Yes, as a gap: nothing says the part is wrong, only that nobody looked. The ledger fact stays
`unknown`, not `failed`. Not raised for a part with an extraction (a fuse still is:
fuse_findings reads only attributes), one with no parts.json row at all, a single-net part,
or a rating someone recorded verified in the ledger.

## Fix ladder (cheapest first)
1. `scripts/parts_enrich.py --workspace <ws>` fills every row from its LCSC listing, keeping
   any value the row already has. It exits 1 on an unknown code and 2 when the endpoint can't
   be reached.
2. Writing rows by hand from parts_search: keep the row whole, `attributes` included.
3. No listing, or the listing has no rating: add a datasheet extraction (P3), or record what
   you checked with `ledger.py set --kind rating --status verified --source <datasheet page>`.
4. Then read what verify says next. A filled row may now show a real rating_over_voltage,
   rating_reverse_polarity or rating_over_current: that is the point, so fix those too.

## Do not
- Do not add a row with a made-up attribute to clear this; cite the listing or the datasheet.
- Do not waive it to pass the gate unless the part's rating has been checked some other way.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --parts <ws>/parts
```

## Sources
- scripts/check_ratings.py `no_attribute_findings`; hive-hwde #675.
