# full-run - brief to order-ready package

The whole pipeline is one recipe. Its steps are the phases; its gates are the
gate table in SKILL.md; the fix loop, the checkpoint format and the spawn
template are shared with every other verb and stay there. Every gate step is
ONE form - `gate.py --gate <g> <input> --workspace <ws> --out <report>` - run on
every attempt, pass and fail: `--workspace` RECORDS the result, and `set-phase`
refuses to advance past a gate phase with no recorded result.

Phase digest: after each phase write 5-10 lines to `log/P<n>-digest.md` and
`state.py set-phase`. Agent selection: spawn only what the design's domains need
- an RF board gets an RF-interface researcher and RF review emphasis; a USB-C
dev board gets neither. Role prompts live in `agents/`. Lean amendment: purely
script-driven phases run INLINE (P5 board setup, gates, P9 fab/dfm scripts, P10
quote/submit) - rule 1 still holds (read script JSON, never design files).
Judgment roles stay agents; reviewers stay FRESH-context agents always. Small
boards may use ONE schematic agent (record it).

## The phases

- **P0 Intake**: spawn `requirements-analyst`; OPEN questions go to the user in
  ONE batch; lint with `check_requirements.py` (it fails an artifact that does
  not name its mode, or states a size the binding relaxes without marking it).
  A brief opening with a mode token names a TARGET deriving a scope tier and a
  BINDING (`reference/build-modes.md`): `state.py mode --token "<it>"` records
  both plus any geometry relaxation; carry them into P2, P5, P6 and every
  reviewer spawn. Safety unknowns (mains / battery / >3 A) are never guessed -
  an unattended run records delegate answers as PROVISIONAL, re-confirmed at H1.
- **P1 Research** (parallel): from the requirements pick the roster -
  `research-component-scout` (per major function), `research-reference-design`
  (per novel block), `research-interface-spec` (per standards-bound interface),
  `research-power-architect` (always, unless trivially powered). Summaries only.
- **P2 Architecture**: spawn `architect`; record its decisions (`state.py
  decision`). **P2 exit = coverage (U13)**: `knowledge.py --coverage --workspace
  <ws> --phase P2 --out log/coverage-P2.json` (blocks[] carry `operating_point`;
  an undeclared dim keeps a record `provisional`). On a `mapping_request` spawn
  `coverage-mapper` (schema-forced record->slot->class edges, no verdicts),
  re-run with `--mapping <its file>` (sha logged in the report). Exit 1 = gap
  slots = research launches AUTOMATICALLY (owner ruling; the `research` recipe
  carries the mechanics): `research.py open --workspace <ws> --gaps
  log/coverage-P2.json --all --phase P2` (one task per gap inside
  `budgets.research`; `status: checkpoint` = cap spent - present the unopened
  slots at H1), per task `researcher` -> FRESH `research-second-reader` ->
  `research.py close`, then re-run coverage (verified records fold in as
  `provisional`). A slot still `gap` is a `state.py decision` "designing under
  coverage gap: ...", never silence. **H1** (blocking): blocks, stackup, cost
  ballpark, key parts, riskiest decision, coverage (covered/provisional/gap per
  slot), cap state, and every spec the mode relaxed (stated -> earned -> why).
- **P3 Parts + Library**: spawn `part-sourcer`; `datasheet-extractor` per
  nontrivial IC (parallel) - reuse a prior board's `parts/<lcsc>.json` on LCSC
  match (re-run `--validate`); then `librarian`. Pad-geometry failures block P4.
  Per-part detail: the `make-footprint` recipe. **P3 exit = coverage again**
  (`--phase P3`): part slots join - per IC, extraction `layout_notes` thin/empty
  = gap; same mapper + auto-research protocol (part-level records from the
  vendor's layout section / app note). Then `datasheet_extract.py --screen <ws>`
  (U9); exit 1 = fix first.
- **P4 Schematic**: one `schematic-block` per sheet from
  `architecture/sheets.md` (parallel where independent; the root-sheet agent
  stitches, runs ERC and `netlist_audit`). Gate `erc`. Then `schematic-reviewer`
  (fresh context): errors -> fix loop, warnings -> `reports/erc-waivers.md`.
  **H2** (blocking): schematic PDF + reviewer findings + waivers.
- **P5 Board Setup**: inline (`board_init` then `rules_gen`, per
  `agents/board-setup.md`); spawn `board-setup` only for impedance or
  library-repair boards. Parity 0, setup 0; sidecars beside the board; generous
  `--outline auto` at a geometry-OUTPUT binding (a fixed WxH is REFUSED there).
- **P6 Placement**: spawn `placement` (seed -> anneal -> select/repair, <= 8 edit
  iterations). Gate `place` (approved residuals: `reports/place-waivers.json`).
  At a geometry-OUTPUT binding placement targets the canonical layout, not the
  outline: after the gate, `board_edit --outline fit --margin M` (+ `planes_gen`
  if it grew) earns the size. **H3** (optional, default ON): top/bottom render.
  Silk/refdes collisions are scripted fixes (`silk_place`, `move_text`); agents
  verify with a full kicad-cli DRC - never trust one checker.
- **P7 Routing**: spawn `router` (chain order is board-class dependent - its
  prompt carries the verified 2L/4L orders). Gate `drc_routed`. A
  `placement_adjust_request` takes the SANCTIONED backward edge: snapshot,
  re-spawn `placement` scoped to the request's refs/region, re-run P7
  (`freerouting_retries` guards the loop). Dead end: `state.py cross-spawn`.
- **P8 Verification**: run the `verify` gate (all 8 checks), then
  `verify-reviewer` (fresh context) with the summary + renders. Triage:
  script-check errors -> fix loop; reviewer errors -> fix loop or human;
  warnings -> `reports/verify-waivers.json` (re-gate with it). After ANY
  copper/placement fix re-run `drc_routed` BEFORE `verify`. **H4** (blocking):
  verification summary + annotated renders + waivers.
- **P9 DFM**: the `dfm-check` recipe, inline (`dfm` agent only for narrative).
- **P10 Ordering**: the `order` recipe, including H5 and the creation latch.

## Simulation legs

- P2/P4: for boards with nontrivial analog content spawn `sim-analyst` - it
  authors `kicad/sims/*.cir` + `.bounds.json` (generic model cards from
  datasheet parameters + Tier-B pin stimulus; NO vendored vendor models). Gate
  `sim` at P8 (`gate.py --gate sim kicad/sims --workspace <ws>`): every bench
  needs a bounds sidecar (missing OR empty = error by design). Wrong-value
  defects - the class no other gate sees - fail here.
- P8 layout legs, advisory by default: `check_irdrop.py` (2.5D FDM IR drop +
  current density on the real copper; gates only when `irdrop_mv_max` /
  `jmax_a_per_mm` are declared; honor `grid_unconverged` warnings) and
  `check_pdn_z.py --metadata decoupling.json` (plane-cavity |Z|; judge peaks and
  `first_min` - `z_max` is a band-edge model artifact; `pdn_target_mohm` gates
  antiresonance peaks only). Fold both into the P8 review; never block on them
  without constraints-declared bounds.

## Run close, resuming, and what not to do

Before the run is finished: append what this board taught to
`~/dev/boards/<b>/LEARNINGS.md` (dated, stage-tagged, one claim per heading), then
`learnings.py compile --workspace ~/dev/boards/<b>` - at the END, an entry's value is
often only clear two phases later. Compiling is not promoting: entries stay
`pending` for the `promote` pass. `compile` also exits 1 naming research the
second reader never cleared: drafts never inject, so the board shipped without
it. Bring-up close (T11): `state.py log --event bringup_passed`, then
`knowledge.py --prove --workspace ~/dev/boards/<b>` (`--dry-run` first) - every record
that APPLIED becomes `proven` with its evidence entry.

A killed run resumes through `resume-phase`. A change of mind after P5 is not a
rewind: `add-part` / `swap-part` / `remove-part` preserve placement and routing;
get decoupling and protection in before P5 anyway. Do NOT re-run a phase you
have gate evidence for - `resume-phase` reads freshness. Do NOT let a report
gate the run: `report_gen.py` is non-blocking, so on exit 1/2 log
`report_gen_degraded`, point at the .tex or last good PDF, and continue.
