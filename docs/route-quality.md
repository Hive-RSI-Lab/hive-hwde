# Route quality: a layout judged as good, not only legal

DRC, the verify suite and check_diffpair say whether a routed board is
legal. None of them says whether it is a good layout. A DDR byte lane can be
matched to the picosecond and still be one long zig-zag. In the talk that
started this, a person steered a router through many rounds until that route
was compact. hwde now does those rounds itself.

## `route_quality.py`: the score

Each routed net is compared with the shortest route it could have had: the
octilinear (0/45/90 degree) minimum spanning tree over its pads.

- **cost** = track length + 1.5 mm per excess via + 0.25 mm per excess bend.
  One via is free when no copper layer holds all the net's pads, and one
  bend is free per connection.
- **quality** = ideal / cost, at most 1.0.
- **Matched nets** (a `length_match` group or a diff pair) are measured
  against the longest member's shortest route. Meander up to that length is
  free; meander beyond it is charged. That is the zig-zag case.
- **Classes**: plane, diff_pair, matched, rf, high_speed, power, signal.
  Each class's score is sum(ideal) / sum(cost), so long nets weigh more.
  Each class also lists its worst nets.
- **Not judged**: pour nets, nets with fewer than two pads, and nets whose
  tracks cover less than half the ideal (not routed by tracks). A partial
  route never counts as a perfect one.

`--min-quality Q` turns each net below Q into a warning. The score is not in
the verify suite or in `bin/hwde score`, because it measures taste, not
failure. The router acts on it (agents/router.md step 4a).

Scores on the pinned boards as they stand:

| board | board score | diff_pair | power | signal |
|---|---|---|---|---|
| core | 0.686 | 0.660 | 0.627 | 0.694 |
| lite-main | 0.780 | 0.857 | 0.730 | 0.777 |
| hwde-led-test | 0.803 | - | 0.779 | 0.873 |

core's worst net is /linein/TAP_R: 58 mm of track against a 12 mm ideal,
with 12 vias and 87 bends.

## `route_improve.py`: the rounds, and when they stop

Each round:

1. Take the nets that waste the most (cost - ideal), four per round, from
   the signal, power and high_speed classes.
2. Rip them and route them again with route_finish's maze router. Other
   nets' copper is never ripped.
3. A net whose new route is no better gets its old copper back. If that old
   copper now runs into the new routes, the round is judged as routed.
4. Keep the round only if nothing comes unconnected, no DRC error or
   warning type grows, and the board score rises by at least 0.002.
   Otherwise restore the board as it was before the round.

It stops on its own after two rounds in a row without a gain, when no
candidate nets are left, or after 12 rounds. diff_pair, matched and rf nets
are left alone unless `--classes` names them, because their geometry is
deliberate and the maze router does not reproduce coupling or a length
target.

### On lite-main (pinned copy, scratch dir)

RESULTS
