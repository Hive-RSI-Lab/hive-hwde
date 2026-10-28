# unconnected_items

KiCad's connectivity engine says two items on the same net are not joined by copper.
One violation per missing pad-to-pad link, so one net emits several (lumina-carrier /pwr/SW: 3).

- Emitted by: kicad-cli `pcb drc` (`unconnected_items` section), normalized by kc.py:141 (`source: "unconnected"`)   Gate: `drc` (P6), `drc_routed` (P7)
- Fixer domain: router (cluster_violations.py:80)   Scripts you may use: route_edit.py, kc.py, render.py, stitch_vias.py, route_cleanup.py (fix_dispatch.py:47)
- Fields on the violation: `msg` is always "Missing connection between items"; `net`, `layer`, `refs`, `pos` = first item's position;
  `items[]` = the TWO endpoints, each {msg "Pad N [net] of REF on LAYER", pos, uuid}. Those uuids are PAD uuids, not removable copper.

## Is it real?
- The check is sound: KiCad counts tracks, vias, pads and FILLED zones. False positives come from board STATE, never from the number.
- Unrouted board: at P6 every multi-pad net reports. board_init excludes `source: unconnected`
  from its own acceptance for this reason (board_init.py:216; LEARNINGS 2026-07-22, line 236), but
  the `drc` gate counts them as errors anyway (gates.yaml:52-58). A board-wide cluster before P7
  is pipeline state, not a defect: escalate, do not hand-route.
- Stale fill: route_edit never refreshes pours (its docstring), and a plane connects only where filled. Re-DRC with `--refill`
  before believing a plane-net residual (line 1445: unrefilled KRT output = 375 failing, only 4 real).
- Unfixable topology 1: a USB-C's two VBUS pads cannot merge on one layer at a wide VBUS rule - a topology fact, not a placement
  or router defect (line 763). Escalate with that diagnosis.
- Unfixable topology 2: a KRT-routed 3-pad diff net closes only the FIRST leg; the other leg reports unconnected and needs the
  two-branch graft, not a local edit (line 1302). Escalate.

## Fix ladder (cheapest first)
1. Classify before editing (free). Cluster spans most nets -> unrouted state, stop and escalate. One net, few links -> continue.
2. Plane-carried net (GND, +3V3, ...), pad residual: bond the pad to the plane.
   `stitch_vias.py --pcb <abs .kicad_pcb> --nets <net> --dry-run`, then rerun without `--dry-run`.
   It rings a via just past the pad edge and adds a short connecting track when the via disc
   misses the pad (stitch_vias.py:5-12). Plane nets are never outer-trunked (line 445).
3. Two endpoints, clear short gap: `route_edit.py --pcb <abs> --ops ops.json` with
   `add_track {start,end,width,layer,net}` joining each endpoint to the abutting same-net copper.
   Width = that abutting copper's width, never below the check's minimum (fix_dispatch.py:56-57).
4. Dangling stub remnants in the region: `route_cleanup.py --pcb <abs> --dry-run` first; it self-guards and reports DEGRADED if
   unconnected grows (route_cleanup.py:609-613).
5. Escalate: a long haul (tens of mm) with no clear channel is an A* budget problem. Tell the
   orchestrator to re-run KRT with a raised budget - KRT route.py `--max-iterations` defaults to
   200000, and 4000000 routed a 68 mm net first try (lines 1433, 1504). No repo script exposes
   that flag, and route_critical.py / route_auto.py are not in your whitelist.

## Do not
- Do not `remove` an items[].uuid here: they are pads, and pads are not a route_edit op.
- Do not put a `remove` and an `add` at the same position in ONE ops file: adds apply first, the
  add dedups as "exists", the remove deletes the original, whole file rolls back (line 1553).
- Do not move a part to open a corridor: placement domain, and after routing starts the place gate
  is blind to copper - it PASSED a move that shorted the board (line 1470).
- Do not gate an edited or KRT-produced board without a refill first (line 1445).
- Do not type a hierarchical net name ("/pwr/SW") into `--nets` from Git Bash: argv mangling turns
  it into C:/Program Files/... (line 1318). Use PowerShell, or `MSYS2_ARG_CONV_EXCL='*'`.
- Do not rip or re-route neighbouring nets: broad rip sets trade nets 1-for-1 (line 1433), and it is outside your work order.
- Do not request an added footprint: no incremental board-from-netlist update exists, so it costs all of P6+P7 (line 2001).

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<board>\kicad\<board>.kicad_pcb --refill --save-board --parity --all-track-errors --out boards\<board>\work\drc-after.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<board>\kicad\<board>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-22 [swig][kicad] unrouted board -> unconnected expected (line 231)
- LEARNINGS 2026-07-23 [routing][placement] P7 chain order; plane nets never outer-trunked (445)
- LEARNINGS 2026-07-28 [routing][placement] FR cannot merge USB-C's two VBUS pads (763)
- LEARNINGS 2026-07-29 [routing][parts] KRT 3-pad diff net routes as a MESH (1302); relative
  --work-dir + bash net-name mangling [parts][python] (1318); 200k A* cap (1433; [routing][krt] 1504)
- LEARNINGS 2026-07-29 [routing][gates] KRT output must be refilled before DRC (1445);
  [place][gates][routing] place gate invalid after P7 (1470); [route_edit] add before remove (1553)
- LEARNINGS 2026-07-30 [pipeline] no incremental board-from-netlist update (2001)
- scripts/kc.py:141, board_init.py:216, cluster_violations.py:80, fix_dispatch.py:47 and :56
- scripts/stitch_vias.py:5-12, route_cleanup.py:609-613, route_edit.py docstring
- reference/gates.yaml:52-68, boards/lumina-carrier/reports/drc-place.json (field shapes)
