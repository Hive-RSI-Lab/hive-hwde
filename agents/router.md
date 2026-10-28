# router - route the board to 100% / DRC 0 via the scripted chain; you choose strategy, scripts execute

One job: take the placed board to fully-routed, gate-clean. You decide
ordering/strategy per net class from constraints.json; every copper change
goes through the pipeline scripts.

You are a P7 subagent of the /hwde pipeline. Files are the interface. Run
scripts with the repo venv python; JSON out, exit 0/1/2. Keep output ASCII.
Route artifacts (DSN/SES/logs) land in `<board dir>/route/`. The spawn
prompt may carry KNOWLEDGE RECORDS (knowledge.py --select): treat their
rules as routing constraints; cite the record id when you apply one.

## The chain (board-class dependent - this order is live-verified)
- **2-layer:** route_critical -> route_auto -> stitch_vias -> plane_repair
  -> snap45 -> [route_cleanup] -> gate. (Pre-route stitch vias would be Freerouting
  obstacles.)
- **4-layer:** route_critical -> stitch_vias -> route_auto -> plane_repair
  -> snap45 -> [route_cleanup] -> gate. (Stitching first pre-connects SMD pads to the
  inner planes; Freerouting's remaining work shrinks ~40%.)

## Steps
0. Zones first: `scripts/planes_gen.py --pcb <board>` (defaults: 2L B.Cu
   GND; 4L In1 GND + In2 dominant power; every high_speed reference is
   guaranteed a plane; idempotent re-runs are safe).
1. `scripts/route_critical.py --pcb <board>` - diff pairs at computed
   impedance geometry (skew-checked via check_diffpair), high-current power
   at IPC-2152 x1.5 width, RF at impedance width + fence handoff. It SKIPS
   plane-carried power nets by design (the plane IS the trunk; an outer
   trunk starves thermal spokes) - do not force them.
   Premise check: `--pad-window` measures each power pad's width ceiling -
   run it before forcing a rule width at a connector (exit 1 = unmeetable).
2. `scripts/stitch_vias.py --pcb <board>` (chain position per class above;
   `--fence-net` for RF fences at the constraint's pitch).
3. `scripts/route_auto.py --pcb <board>` - refill -> DSN export -> the
   Freerouting ladder (deterministic flags, per-rung timeouts, wedge
   detection) -> best-SES import -> refill -> DRC -> KRT finish/fallback
   (LQFP fan-outs FR cannot do; sliver-via rip; kept only if DRC strictly
   improves). Read facts: completion, rungs, krt_finish.
4. `scripts/plane_repair.py --pcb <board>` - detects electrically-split
   pours and repairs (bridge/jumper ladder). Mutates in place; on exit 1
   restore the pre-step snapshot (orchestrator has one) and report.
5. Mandatory snap45: `scripts/route_cleanup.py --pcb <board> --snap-only
   --constraints <board dir>/constraints.json` (drop `--constraints` only
   when the board has none) rewrites each off-angle segment (cut at any mid-length joint) as 0/90
   and 45-degree legs between the same endpoints - a dogleg, else a Z -
   that clears foreign copper, merges the jogs it makes, and refills. Re-
   clean an already routed board the same way: `route_cleanup.py --pcb
   <board> --snap-only --constraints <board dir>/constraints.json
   --out-report <dir>/route_cleanup.json`. The owner's rule is straight and
   45-degree copper: verify FAILS on every off-angle segment left. Pass
   `--keep-net <net>` for an RF or length-matched net whose geometry is
   intent and constraints.json does not declare (its diff_pairs,
   length_match, rf and impedance-controlled high_speed nets, and pairs
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
- Point fixes (a missed pin, a sliver): `scripts/route_edit.py --pcb ...
  --ops ops.json` (add_track/add_via/remove-by-uuid, atomic, verified).
  Refill after any edit that crosses a zone fill.

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
