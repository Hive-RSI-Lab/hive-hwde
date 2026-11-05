# rating_datasheet_unassessed

A part's datasheet has no verdict: its index.json entry is from before datasheets were assessed
(#706), or `--assess` had no PDF to read. So nobody knows it's that part's datasheet. A warning, not
a pass. The facts stay as they were: refusing them would block every board fetched before #706
for lack of a file nobody kept.

- Emitted by: scripts/check_ratings.py (kind="rating_datasheet_unassessed", warning, one per board)
  Gate: verify (P8, via verify_all.py).
- Fields: refs [every part affected]; `unassessed_datasheets` in the report lists the LCSC codes.

## Fix ladder (cheapest first)
1. PDFs on disk: `bin/hwde datasheet_extract --assess <ws>` writes each verdict into index.json.
2. No PDF (they aren't committed): `bin/hwde datasheet_extract --fetch <ws>` downloads and assesses.
3. Suspect after that: rating_datasheet_suspect says why.

## Do not
- Do not write `"status": "ok"` into index.json by hand.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```
