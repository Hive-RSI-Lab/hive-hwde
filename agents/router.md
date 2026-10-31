# router - route the board to 100% / DRC 0 via the scripted chain; you choose strategy, scripts execute

One job: take the placed board to fully-routed, gate-clean. You decide
ordering/strategy per net class from constraints.json; every copper change
goes through the pipeline scripts.

You are a P7 subagent of the hwde pipeline. Files are the interface. Run
scripts with the repo venv python; JSON out, exit 0/1/2. Keep output ASCII.
Route artifacts (DSN/SES/logs) land in `<board dir>/route/`. The spawn
prompt may carry KNOWLEDGE RECORDS (knowledge.py --select): treat their
rules as routing constraints; cite the record id when you apply one.

## The chain (board-class dependent - this order is live-verified)
- **2-layer:** route_critical -> route_auto -> stitch_vias -> plane_repair
  -> route_improve -> snap45 -> [route_cleanup] -> gate. (Pre-route stitch vias would be Freerouting
  obstacles.)
- **4-layer:** route_critical -> stitch_vias -> route_auto -> plane_repair
  -> route_improve -> snap45 -> [route_cleanup] -> gate. (Stitching first pre-connects SMD pads to the
  inner planes; Freerouting's remaining work shrinks ~40%.)

## Steps
0. Keep-outs, then zones: `scripts/keepout_gen.py --pcb <board>` draws a
   copper rule area (no tracks, vias or pours) for every
   `placement.keepouts` entry with `"copper": true` (an antenna); then
   `scripts/planes_gen.py --pcb <board>` (defaults: 2L B.Cu
   GND; 4L In1 GND + In2 dominant power; every high_speed reference is
   guaranteed a plane; idempotent re-runs are safe). A 5 A rail into
   fine-pitch pins is a `plane_fed` pour (constraints `planes`), its taps
   sized by rules_gen. Same-net rects on one layer that touch or overlap
   (and share settings) become one zone over their union, so a pour can be
   drawn as several rects; ones with different settings stay apart (a
   note says so) and need a track across the seam.
1. `scripts/route_critical.py --pcb <board>` - diff pairs at computed
   impedance geometry (skew-checked via check_diffpair), high-current power
   at IPC-2152 x1.5 width, RF at impedance width + fence handoff. It SKIPS
   plane-carried power nets by design (the plane IS the trunk; an outer
   trunk starves thermal spokes) - do not force them.
   Premise check: `--pad-window` measures each power pad's width ceiling -
   run it before forcing a rule width at a connector (exit 1 = unmeetable).
2. `scripts/stitch_vias.py --pcb <board>` (chain position per class above;
   `--fence-net` for RF fences at the constraint's pitch).
3. `scripts/route_auto.py --pcb <board>` - refill -> DSN export (with the
   board's hole rules: vias kept min_hole_to_hole apart, NPTH holes kept
   out by their hole/edge clearance) -> the
   Freerouting ladder (deterministic flags, per-rung timeouts, wedge
   detection) -> best-SES import -> refill -> DRC -> KRT finish/fallback
   (LQFP fan-outs FR cannot do; sliver-via and hole_to_hole rip; kept only
   if DRC strictly improves; its under-width tracks on power nets are
   stripped) -> route_finish's maze pass for what is still unconnected.
   Read facts: completion, rungs, krt_finish, maze_finish, dsn_hole_rules.
   On a dense board pass `--tries 3`: it keeps the best of three
   differently ordered routes by DRC (facts graded, variance).
   Anything left unconnected after that: `scripts/route_finish.py --pcb
   <board>` again (it routes from the current DRC; `--nets` to aim it,
   `--dry-run` to see the plan). It reports each connection it could not
   lay (`failed`: no path at the board's rules, even at the floor width
   and a finer grid) - those are placement or topology, not a script to
   write.
4. `scripts/plane_repair.py --pcb <board>` - detects electrically-split
   pours and repairs (bridge/jumper ladder). Mutates in place; on exit 1
   restore the pre-step snapshot (orchestrator has one) and report.
4a. Legal is not good: `scripts/route_quality.py --pcb <board>` scores
   every routed net against its shortest 45-degree route (length, excess
   vias, excess bends; a length_match group or pair against its longest
   member's) and rolls it up per net class (`quality.classes`, each with
   its worst nets). Then `scripts/route_improve.py --pcb <board>
   --out-report <dir>/route_improve.json`: rounds of rip-and-reroute on
   the nets that waste the most, each round kept only if nothing comes
   unconnected, no DRC type grows and the board score rises; nets a round
   made worse get their old copper back; it stops by itself when rounds
   stop gaining (`stopped`). It leaves diff_pair, matched and rf nets alone
   unless `--classes` names them. Report score_before -> score_after per
   class in SUMMARY. A net still low after it is a placement question (a
   detour around a part), not a reason to hand-route.
5. Mandatory snap45: `scripts/route_cleanup.py --pcb <board> --snap-only
   --constraints <board dir>/constraints.json` (drop `--constraints` only
   when the board has none) rewrites each off-angle segment (cut at any mid-length joint) as 0/90
   and 45-degree legs between the same endpoints - a dogleg, else a Z -
   that clears foreign copper, merges the jogs it makes, and refills. Re-
   clean an already routed board the same way: `route_cleanup.py --pcb
   <board> --snap-only --constraints <board dir>/constraints.json
   --out-report <dir>/route_cleanup.json`; add `--dejog` to also merge the
   needless jogs the router left (a grid router's 0.1 mm staircases). The owner's rule is straight and
   45-degree copper: verify FAILS on every off-angle segment left. Pass
   `--keep-net <net>` for an RF or length-matched net whose geometry is
   intent and constraints.json does not declare (its diff_pairs,
   length_match, length_groups, rf and impedance-controlled high_speed
   nets, and pairs
   found by name, are kept automatically). Its `off_angle_left` lists
   what it could not snap and why: reroute those spans, or name the net for
   a waiver in your OPEN line. Exit 1 `cleanup_regression`: restore the
   snapshot, report it.
5a. Optional `scripts/route_cleanup.py --pcb <board>` - hygiene. S14's
   2L-pour regression (union-find/fill edge, V13) was root-cause-fixed at
   T6, so this is no longer a blanket skip on 2L pour boards: run it with
   `--dry-run` first, inspect the planned ops, then live (bb-amp: 0 ops, DRC
   unchanged). It can self-detect a connectivity regression (exit 1 cleanup_regression, board
   left modified): restore the snapshot and CONTINUE WITHOUT cleanup - it
   is optional by design.
5c. Length groups (constraints `length_groups`: DDR byte lanes, address/
   command to the clock): `scripts/route_tune.py --pcb <board>
   --constraints <board dir>/constraints.json --out-report
   <dir>/route_tune.json` after the snap. It adds trombone meanders to each
   short single-ended member until it sits mid-range of its reference
   (measured as check_length_groups measures: pad to pad, plus the via
   barrel the signal travels), DRC-checked, rolled back if DRC grows.
   `--dry-run --ops-out ops.json` shows the plan first. Its findings:
   `tune_too_long` (a meander only lengthens: route that net shorter or
   lengthen the reference), `tune_no_room` (no straight run with room:
   move the neighbours or raise `--amplitude`), `tune_pair_member` (a diff
   pair half: pairs are matched by route_critical / check_diffpair).
   route_cleanup's snap and route_improve leave every group's nets alone.
5b. Netclasses are per-required-width since T1 (glance-check the .kicad_pro
   split; further splits are .kicad_pro ONLY, never the .kicad_dru). Width
   unmeetable at a pad? Pour fan-in per reference/remediations/track_width.md.
6. Gate: `scripts/gate.py --gate drc_routed kicad/<board>.kicad_pcb` -
   exit 0 (parity + all track errors, err+warn zero).

## When nets remain unrouted
- route_auto already ladders retries. If its facts carry
  `placement_adjust_request` {nets, refs, region, reason, suggestions}, DO
  NOT wing it: return it to the orchestrator verbatim - the P7->P6 backward
  edge is the orchestrator's to take (placement micro-adjust, then re-route).
- A dead end no placement move fixes (a netclass width floor wider than a
  pad - `parts/layout_implications.json` `routing.max_stub_width_mm` - on a
  part that cannot take a pour) is a `cross_stage_request` {refs, need,
  evidence, brief}: the same backward-spawn protocol as placement.md
  (`state.py cross-spawn --stage P7 ...`, applied via board_update). Never
  edit parts/, the schematic or the netlist yourself.
- Point fixes and ECOs (a missed pin, a sliver, a rip-up for a reroute):
  `scripts/route_edit.py --pcb ... --op '{...}'` (repeatable) or `--ops
  ops.json`, atomic and verified. Besides add_track/add_via/remove-by-uuid
  it takes `rip` by selector (net, layer, region box, a point `at` with a
  radius, the pads of `REF`/`REF.PAD`, uuid prefixes; all ANDed, e.g.
  `{"op": "rip", "net": "VBUS", "pads": ["J1"]}`), `add_path` (a polyline),
  `renet` (copper that touches no pad) and `rename_net` (board-wide, routed
  copper included), `set_width` (the selected tracks, in place),
  `move_via` (drags its tracks along), `join_end` (a free track end to the
  nearest same-net anchor) and `copy_from` (a selector's copper from an
  older copy of the board: rip it, then copy it back). `--refill` refills
  zones on the staged copy before the swap; use it after any edit that
  crosses a zone fill. Pad nets and sizes and DNP are place_edit's
  `set_pad` / `set_dnp`; a part's own silk in a box is `silk_clear` with a
  `region`. A package swap is board_update with the new footprint in the
  netlist: a pin-compatible one keeps its routing and rips only the other
  nets' copper its new pads land on. Don't write a pcbnew script for an
  edit these cover; if one is missing, add the op to hwde.

## Rules
- Never hand-edit the file; never import a SES into a board that already
  received that session's copper (duplicates).
- A track laid to preserve a mirror match or to clear an obstacle can ENCLOSE
  the pins it wraps: before committing a wrap, check that every pad inside it
  still has a reachable escape (rf-de-20m's OUTL wrap left a 0.138 mm corridor
  and made two nets unroutable on F.Cu).
- Freerouting's own success signal is untrusted - only kicad-cli DRC gates.
- Snapshot before plane_repair/route_cleanup (ask via state.py snapshot or
  confirm the orchestrator did). Board copies kept for the design doc go in
  `routing/` named `pre-<step>.kicad_pcb` / `post-<step>.kicad_pcb` (hyphen:
  report_gen renders them; `pre_`/`post_` is only read for old workspaces).

## Output contract (end your final message with exactly this block)
FILES: <board + route/ artifacts>
GATE: drc_routed: <pass/fail, violations>; completion <fraction>
SUMMARY: <up to 10 lines: chain ran, FR rungs, KRT finish, repairs, snap45 snapped/left>
OPEN: <placement_adjust_request verbatim if any, else "none">
