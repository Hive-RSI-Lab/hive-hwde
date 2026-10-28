# E2E bench: how a finished board is scored against a brief

`bench.py --stage E2E` scores one finished board workspace against one brief.
It copies EEBench's method (hidden tasks, SPICE at tolerance corners, BOM cost
in the score) and extends it to layout, which EEBench does not grade. The
code is `scripts/lib/e2elib.py` and `score_e2e` in `scripts/bench.py`; the
briefs are `tests/fixtures/stages/e2e/<brief>/`.

## Running it

```
bench.py --stage E2E --fixture e2e_<brief> --artifact <workspace>   # a run of yours
bench.py --stage E2E --fixture e2e_<brief>                          # the fixture's own board
bench.py --list                                                     # every brief
```

The e2e stage is opt-in. The default check runs one cheap case, the frozen
bb-ldo workspace offline (`tests/test_bench_e2e.py`); the live case of the same
workspace is `smoke`-marked. On the no-container Linux host, first
`. ~/.local/kicad10/hwde-env.sh` and start `Xvfb :99`; without kicad-cli the
live checks drop out and `composite_inputs` says `offline`.

## Briefs and hidden bounds

Each brief is two files. `brief.md` is the only thing the design agent is
given, so pass it as the task and nothing else from its directory.
`bounds.yaml` holds the numbers the board is judged on, and the agent must
never see it. A bound that leaks into a run makes that run's score
meaningless, so a driver that shows the agent `tests/` has spent the brief.

Calibration briefs restate a shipped board's brief (bb-ldo, bb-buck, bb-adc,
bb-amp) and, with no `--artifact`, score that board from the boards repo
(`args.calibration_board`). hwde has seen them, so they check the scorer, not
hwde. The five held-out briefs have never been run; with no `--artifact` they
score 0.

`bounds.yaml` keys (`load_bounds` refuses anything else, and refuses a file
missing `nets`, `layout.area_mm2_max` or `cost.target_usd`, because without
them a category would score its weight for free):

| key | what it bounds |
|---|---|
| `nets: [{id, pattern}]` | a net whose name the regex finds must exist; the id names it for SPICE |
| `parts: [{id, value_pattern, min_count}]` | parts whose value matches, at least `min_count` |
| `spice: [...]` | the checks below |
| `layout: {area_mm2_max, crossings_signal_max, decap_worst_mm_max}` | board outline area, signal ratsnest crossings, worst decap distance |
| `cost: {qty, target_usd}` | BOM USD per board at an order of `qty` boards |

## SPICE at corners

The bench writes its own decks. It never runs the workspace's `kicad/sims/`,
because those are the agent grading itself. It reads the network between the
bound's nets from the workspace netlist, copies it once per tolerance corner
(every +/- combination, up to 10 parts, which is exact for a resistive divider)
and runs all copies in one ngspice deck through sim_run's worker. A part's
tolerance is the `N%` in its value, or the bound's default (R 1 %, C 10 %).
Resistors into another rail the bounds name are left out, and off-board parts
are open circuit.

- `divider`: `top`, `mid`, `bottom` net ids, and 1 V on top gives the ratio
  V(mid)/V(top). With `vref` the value is `vref / ratio` (a regulator's
  output); `vref: {by_value: {regex: volts}}` takes the reference from the
  first part whose value matches. Anchor each pattern to the whole part
  number (`TPS5430(?![0-9])`), because a bare prefix also matches its
  siblings with a different reference. A design with no listed part is
  unscored rather than failed. With `scale` the value is `scale * ratio` (a
  sense divider at full input).
- `rc_lowpass`: `in`, `out`, `gnd`, and the -3 dB frequency of V(out)/V(in)
  relative to its own low-frequency gain. It takes two decks, because
  ngspice's `.meas` cannot subtract one measure from another.

The check scores the fraction of corners that land inside `[min, max]`.
Briefs only carry SPICE where the answer does not hang on which IC the agent
picks, or where `by_value` covers the plausible ICs.

## The score

Every check scores 0 to 1, or `None` when this host or design cannot score
it. A missing artefact scores 0, not `None`, and so does a board with no
closed Edge.Cuts outline. A category is the mean of its
scored checks.

| category | checks |
|---|---|
| electrical | nets, parts, ERC errors (live), SPICE (live) |
| layout | area, placement legality, signal crossings, decap distance (with a decoupling sidecar), DRC errors (live), routing completion (live) |
| cost | BOM at `qty`, scaled by the fraction of BOM lines that carry a price |

A "less is better" value scores 1 at or under its bound and falls linearly to
0 at twice the bound. A bound of 0 scores `1/(1+n)`.

```
composite = 100 - 45*(1-electrical) - 20*(1-layout) - 35*(1-cost)
```

That is the bench's composite. A brief with a known-answer in the repo's
`evals/known_answers/` (usbc_ldo, stereo_amp) is also scored by
`evals/e2e_electrical.py` (datasheet pin-level connectivity, values, power
and thermal, decap distance, ERC/DRC gates), and the eval run record's
composite is then `100*(0.70*E + 0.15*layout + 0.15*cost)`, with the bench's
kept as `composite_bench`.

Cost keeps EEBench's 0.35, and electrical plus layout split its 0.65
technical share. `e2e.eebench_equiv = 100*(0.65*electrical + 0.35*cost)` is
what EEBench's own split would say, with no layout term. Baselines live in
`tests/fixtures/stages/baselines/e2e_*.score.json`. Calibration baselines
read the boards repo, which is not pinned, so re-record them when a
calibration board changes.

## Known limits

- Net and part checks match names and values by regex. A correct design with
  unusual net names loses points, so write patterns broad.
- The SPICE kinds are the two above. An op-amp gain stage or a regulator's
  loop is not simulated.
- Cost is parts only (parts.json price breaks), with no PCB or assembly fee.
- Nothing is normalised across briefs. Compare a brief's score only with the
  same brief.
