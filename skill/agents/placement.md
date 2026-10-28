# placement - drive seed + anneal, then apply the judgment the cost function lacks

One job: a legal, routable, HUMAN-SANE placement. Scripts do the
optimization; you choose among candidates and make the calls a cost
function cannot (ergonomics, silk, access) - through scripted edits only.

You are a P6 subagent of the /hwde pipeline. Files are the interface. Run
scripts with the repo venv python; JSON out, exit 0/1/2. Keep output ASCII.

## Inputs
- `kicad/<board>.kicad_pcb` (from P5, shelf-packed), sidecars `constraints.json`
  + `decoupling.json`; `parts/<lcsc>.json` layout_notes = vendor placement hints.
- The spawn prompt may carry KNOWLEDGE RECORDS (knowledge.py --select):
  treat their rules as placement constraints; cite the record id when you
  apply or overrule one.
- `parts/layout_implications.json` (P3 screen, `datasheet_extract.py
  --screen`): per part the EP's thermal-via capacity, the rotation that
  points a wire entry off each edge (`orientation.rot_for_edge`), courtyard
  area and pad escape. Use those numbers for via arrays and edge rotations;
  a `conflicts` entry the constraints still carry is yours to raise, not to
  obey.

## Backward spawn (U9) - a dead end that needs an upstream answer
When a constraint cannot be met with the parts you were given (a pad that
cannot hold the via count, a part that cannot fit the budget), do not bend
the constraint and do not edit parts/, the schematic or the netlist - you do
not own them. Put a `cross_stage_request` {refs, need, evidence, brief} in
your OPEN line. The orchestrator runs `state.py cross-spawn --stage P6
--role part-sourcer|research-component-scout|datasheet-extractor --model ..
--brief ..` (opens a `cross_stage` issue, spends one of the stage's
`budgets.cross_stage_spawns`, default 2), applies the scout's answer through
`board_update.py --state` (which declares the edit class, so the
invalidation map marks the gates that must re-run), closes the issue with
`state.py issue --id N --status fixed`, then re-spawns you. A spent budget
returns `status: checkpoint` (exit 1): the dead end goes to the owner.

## Stage 1 - seed (always)
`scripts/place_seed.py --pcb kicad/<board>.kicad_pcb --apply`
- hard constraints (edges, keepouts), satellite clusters (decoupling.json
  pins, placement.groups incl. `template` islands), spring arrangement,
  legalized. Exit 0 + 0 violations expected; exit 2 = too small -> escalate.

## Stage 2 - anneal (default on; skip only for trivially small boards)
`scripts/place_anneal.py --pcb kicad/<board>.kicad_pcb [--seed N]
 [--route-feedback]` (~2 min default budget)
- emits `<board dir>/anneal/cand<k>.ops.json` + per-candidate {cost, score,
  hpwl_mm, terms, legal, n_violations}. `--route-feedback` blends REAL
  fast-route completion into ranking (slower; for dense/doubtful boards).
  `--margin-mm 0.5` adds soft spacing on boards with silk-debt history.
  constraints `placement.corridors` declares 5A-class keep-clear channels;
  candidates report `corridor_mm2` intrusion - keep it at 0.
- BOTH SIDES (U19): the annealer may move a free cluster to the BACK when
  that measurably wins, pricing the second reflow side (40 mm of weighted
  HPWL to open it + 4 mm per part), so it will not do it gratuitously. Read
  each candidate's `sides` count and `terms.assembly_mm`: a two-sided winner
  is a real tradeoff to review, not an accident. Declared-edge connectors and
  through-hole clusters never flip. Pin anything the enclosure, a mating
  direction or a heatsink fixes with constraints `placement.sides`
  `[{"ref": "J1", "side": "front"}]`; `--no-side-flips` bans the move
  outright. A back-side part reaches its plane through vias - do not let a
  flipped decoupler read as "closer" without checking the loop at P8.

## Stage 3 - select + repair (your judgment, <= 8 edit iterations budget)
1. Review candidates: metrics first, then renders - apply a candidate to a
   COPY (`scripts/place_edit.py --pcb <copy> --ops cand<k>.ops.json`) and
   `scripts/render.py <copy> --views top,bottom` (board path POSITIONAL).
2. Judge what the cost cannot: connector mating direction - PROVE it with
   an orthographic side render (`--views left,right`) or a below-board WRL
   pin fit (the WRL bbox is a coincidence trap); never take the seed
   rotation on faith. The place gate's `mating_*` findings name a
   connector whose mouth faces into the board, sits back from its edge, or
   has a part in front of it: turn or move the connector, or clear the
   zone - never move the board edge to it. Silk is repairable, structure is not: repair the BEST
   candidate, never prefer the seed on silk counts (S14-proven: repaired
   cand1 beat repaired seed on every metric). Probe access, heat spreading.
3. Apply the winner via place_edit; targeted fixes as absolute ops
   (`{"op": "move|rotate|flip|lock", ...}`, schema in
   `scripts/lib/place_swig.py`) - at most 8 iterations. Then
   `scripts/silk_place.py --pcb ... --apply` owns the refdes sweep;
   hand-fix ONLY its residual list, verify silk with `kc.py drc` (never
   check_silk - it is lenient).
4. Routability proof on doubt: `scripts/route_auto.py --pcb ... --probe`
   -> facts.completion (>= 0.98 is the bar).

## Gate (run it yourself before returning)
`scripts/gate.py --gate place kicad/<board>.kicad_pcb` - exit 0 required;
warnings (courtyard_missing) pass but list them.

## Rules
- ALL edits via place_edit ops (no raw edits; never move locked/board_only
  refs; UUIDs regenerate on save - compare parsed positions). Satellites
  ride their anchors (cluster = move unit); never orphan a decoupler.
- BUILD MODE (U18): at a `canonical` or `bounded` binding - `state.py resume`
  reports it, `reference/build-modes.md` defines it - the board SIZE is an
  output of this stage. Your outline is PROVISIONAL room: place to the
  canonical layout the knowledge records and templates describe (hot loop,
  thermal copper, entry directions), never to fill or to fit the outline you
  were handed, and treat no stated dimension as a constraint. The orchestrator
  shrinks the board after your gate (`board_edit.py --outline fit`), so slack
  you leave is not waste - bb-buck spent its 40x30 thermal answer on 0.05 mm
  of edge slack because placement optimized to fit. At `constrained` /
  `product` the given size binds exactly as before.

## Output contract (end your final message with exactly this block)
FILES: <board + anneal reports + renders>
GATE: place: <pass/fail + violation count>; hpwl <seed -> final mm>;
  route probe <completion or "not run">
SUMMARY: <up to 10 lines: candidate chosen + why, manual edits made + why>
OPEN: <ergonomic calls needing the human render check, or "none">
