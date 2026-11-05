# rating_datasheet_unassessed

A part's datasheet can't be judged here: its index.json entry has no PDF on disk (they aren't
committed), or there is a PDF but no MPN in index.json or parts.json to check it against. So nobody
knows it's that part's datasheet. A warning, not a pass. The facts stay as they were: refusing them
would block every fetched board for lack of a file nobody kept. PDFs that are on disk don't land
here: check_ratings and the ledger judge them when they read the index (#768).

- Emitted by: scripts/check_ratings.py (kind="rating_datasheet_unassessed", warning, one per board)
  Gate: verify (P8, via verify_all.py).
- Fields: refs [every part affected]; `unassessed_datasheets` in the report lists the LCSC codes.

## Fix ladder (cheapest first)
1. No PDF: `bin/hwde datasheet_extract --fetch <ws>` downloads and assesses.
2. No MPN: give the parts.json line its MPN; the next read judges the PDF.
3. Suspect after that: rating_datasheet_suspect says why.

## Do not
- Do not write `"status": "ok"` into index.json by hand.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```
