# items_not_allowed

A copper item sits where a `disallow` DRC rule forbids it. In this pipeline the only generator of
`disallow` rules is `rules_gen.py`'s `aiee_diff_outer_only_<pair>` family (T6 P5-4, the V12 guard):
any TRACK segment of an impedance-solved diff pair on an INNER layer. The rule exists because
`lib/impedance.py` models OUTER-layer microstrip only - inner layers of the real stackups see
asymmetric dielectrics (1080B: 0.2444 / 1.065 mm) and the solved width/gap means nothing there.
Without the rule the pair routes "clean" and ships with silently wrong impedance.

- Emitted by: kicad-cli DRC (`<board>.kicad_dru` auto-loads, no flag), normalized by `kc.py`
  `normalize_violation`. Msg: `Items not allowed (rule 'aiee_diff_outer_only_<pair>')`.
  Gate: `drc` (P6, errors only), `drc_routed` (P7).
- Fixer domain: router   Scripts you may use: route_edit.py, route_critical.py, kc.py, render.py
- Fields on the violation: `pos` [x,y] mm, `layer` (an inner Cu layer), `net` (one of the pair),
  `items[0].uuid` (the offending segment - act on this).

## Is it real?

- Almost always: the router (or a hand edit) dropped an impedance-controlled net onto In1/In2.
  Layer TRANSITIONS are legal - vias are not tracks and never fire this rule - so what is flagged
  is specifically inner-layer track length, exactly the geometry the solver never solved.
- A rule named anything OTHER than `aiee_diff_outer_only_*` means someone hand-authored a disallow
  rule; read the DRU comment beside it before touching anything.
- Not real only if the net was WRONGLY paired (rules_gen JSON `diff_pairs` lists the detected
  pairs; a suffix-matched false pair means fix constraints `diff_pairs`, then regenerate).

## Fix ladder (cheapest first)

1. Reroute the flagged segments onto F.Cu/B.Cu: for a short stub, one route_edit ops file
   (`remove` the inner segment uuids, `add_track` the outer replacement at the pair's solved
   width from the rules_gen JSON `diff_pairs` facts). For a full pair, route_critical.py
   re-routes from constraints `diff_pairs`.
2. If the escape genuinely needs an inner layer (dense fan-out): that is a CONSCIOUS waiver.
   Hand-solve the stripline geometry against the fab's impedance calculator (JLC's, for the
   exact entry in `reference/stackups.yaml` - its epsilon_r values are ASSUMED, see the header),
   record width/gap + source in the workspace log, apply with route_edit.py, and only then
   delete that one `aiee_diff_outer_only_<pair>` rule from the `.kicad_dru`, with a comment
   naming the log entry. Never delete the rule first.

## Do not

- Do not delete or blanket-waive the rule to "make DRC pass" - the violation is the only thing
  standing between an unsolved inner-layer geometry and a shipped board.
- Do not re-solve impedance with `lib/impedance.py` for an inner layer; it has no stripline
  model (V12) - that is the whole point of this rule.
- Do not hand-edit the DRU beyond the single conscious rule deletion in ladder step 2; then
  `rules_gen.py --check-dru` must still pass (the aiee_* floors survive your edit).

## Verify

- Re-run the drc gate (`gate.py drc`): zero `items_not_allowed`, and no NEW clearance /
  diff_pair_gap violations at the rerouted segments.
- `rules_gen.py --check-dru <board>.kicad_dru --layers N --stackup NAME` exits 0.
- check_diffpair (P8) still passes: skew / uncoupled budgets survived the reroute.

## Sources

- PROGRESS.md S8 entry item 4 (V12: outer microstrip only; stripline unsolved).
- `reference/stackups.yaml` header (assumed epsilon_r; confirm against the fab calculator).
- rules_gen.py `diff_pair_rules` (T6 P5-4 comment block: rationale + waiver path).
