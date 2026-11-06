# rating_listing_disagrees

The distributor's listing in parts.json and this part's datasheet extraction give the same voltage
rating numbers more than 10 % apart. A warning, and it fires whatever the rail: it says one of the two
is wrong, not that the board over-stresses the part. Those findings are `rating_over_voltage`.

- Emitted by: scripts/check_ratings.py (kind="rating_listing_disagrees", warning, one per part)
  Gate: verify (P8, via verify_all.py).
- Fields: refs, listing_v, datasheet_v, rating [drain_source, reverse, rated, supply], listing_attr,
  datasheet_row, side, check_used. `side` is `high` when the listing overstates the part, `low` when
  it understates it. `check_used` is `datasheet`, since an extracted part is never judged on its
  listing, or `none` when no pin met a known rail.

## Is it real?
Read the PDF. Either can be wrong: a listing may carry a family's top figure, as CH224K's 30 V does
for a part whose VBUS is 13.5 V, and an extraction may have misread a row. A supply range is only
compared one way, against the highest voltage row on the part, so a partial extraction with no input
row can make a correct listing look high. A listing below the datasheet's top supply figure isn't
reported, because it may be a nominal.

## Fix ladder (cheapest first)
1. An extraction misread: fix `parts/<lcsc>.json` from the PDF, with the row and page.
2. A listing that's wrong and `side` high: nothing on this board relies on it once the part is
   extracted. Say so in the part's notes, and check other boards using it from the listing alone.
3. A listing that's wrong and `side` low: a rating read from it was only conservative. No action.

## Do not
- Do not edit the attributes in parts.json to make the numbers match: they record what the
  distributor says.
- Do not trust the part's other listed numbers unread: `side` high means this listing overstated once.

## Verify
```
bin/hwde check_ratings --pcb <ws>/kicad/<board>.kicad_pcb --parts <ws>/parts
```
Facts `listing_comparison` holds every comparison on the board, and `listing_not_compared` every part
that couldn't be compared and why.
