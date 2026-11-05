# rating_datasheet_suspect

The PDF on disk for this part isn't shown to be its datasheet, so the ledger facts read from it (the
extraction's `source_pdf` says which PDF that was) are unknown, never verified, and `ledger.py set`
refuses to rest on it. A part with no extraction read nothing from it: its rating came from the
distributor or a package table and stays `listed` (#781). check_ratings and the ledger
judge each PDF on disk when they read index.json, so nobody has to ask for the verdict (#768). The
reason in the message says which case below it is.

- Emitted by: scripts/check_ratings.py (kind="rating_datasheet_suspect", warning, one per part)
  Gate: verify (P8, via verify_all.py).
- Fields: refs [the part's refdes]; `suspect_datasheets` in the report maps LCSC code to the reason.

## Is it real?
Usually the file is fine and the check can't tell, or the file is another part's. Open it before you
do anything: the check reads text, so it can't tell these apart.
- "no text layer": a scan or image-only PDF. Nothing was read, so nothing ties it to the MPN. The
  document may be right (4 of the 7 on fmcw-radar).
- "is not in the text", on a series document that never names the series: passives are often sold
  from a document that prints a scheme, not the part number, and this one has no name to tie it to
  the part (KT-0603 on fmcw-radar, 2 of the 7). It may also be another maker's part.
- "is not in the text", with no ordering table: the document prints electrical values only, with
  nothing that decodes the MPN (a 7X crystal on fmcw-radar, 1 of the 7).
- "truncated", "does not parse" or "host placeholder": the download is broken. Never the right
  document.

## Fix ladder (cheapest first)
1. Truncated, unparsable or placeholder: `bin/hwde datasheet_extract --fetch <ws> --lcsc <code>` again.
2. No text layer: find the same document with text (the maker's site, not the distributor's copy)
   and put it at parts/datasheets/<lcsc>.pdf; the next read judges it. If there is none, read the
   pages with `bin/hwde datasheet_extract --pdf <file> --render <dir>`, then step 5.
3. Series document that never names the series: get the maker's document that does. With a real PDF
   in hand that has an ordering table, add a scheme to reference/ordering_codes.yaml with a test, and
   the MPN decodes against it.
4. No ordering table: look for the maker's ordering or part-numbering page and use that PDF, or
   step 3 if it is a scheme many parts share.
5. You read it yourself and it is that part's: record the fact and why, in the ledger:
   `bin/hwde ledger set --workspace <ws> --subject <ref> --kind rating --status verified
   --source <page> --force "why"`.
   The warning stays. It says the check could not tie the file to the part, which is still so.
6. It is another part's document: delete the PDF, and `--fetch` or find the right one.

## Do not
- Do not write `"status": "ok"` into index.json by hand: the next read judges the PDF again only if
  its stamp changed, so a hand edit would hide the doubt.
- Do not add a scheme to reference/ordering_codes.yaml to quiet one part without a real PDF showing
  it: the scheme then passes every document that matches it.
- Do not use `--force` without reading the page the fact comes from.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --constraints <ws>/kicad/constraints.json
```
The part's warning is gone (or, after step 5, stays with the reason on record in the ledger).

## Sources
- GitHub issues #706 (status recorded), #755 (ordering tables), #768 (assessed on read; the 7 cases on
  fmcw-radar).
