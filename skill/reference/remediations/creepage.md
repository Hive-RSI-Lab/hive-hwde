# creepage

Two nets more than 30 V apart (constraints.json `voltages`, or an explicit `voltage_pairs` entry)
have same-layer copper closer than the IPC-2221 Table 6-1 minimum for the adjudicated row pair.

- Emitted by: scripts/check_creepage.py   Gate: verify (P8, via verify_all.py)
- Written for the T2 check. If your report carries no `item`/`other_item` and the script has no
  `--coating`, you are on the PRE-T2 build: it reports only the WORST pair per net pair, cannot
  express a coated board or an equal-potential PAIR, and its numbers are a starting point (Sources).
- Fixer domain: placement   Scripts you may use: place_edit.py, place_metrics.py, render.py
- Fields on the violation: pos (midpoint of the ACTUAL nearest points - the gap, not a net centroid),
  layer, net (owner = larger |V|), other_net, delta_v, spacing_mm, required_mm, rows [row_a, row_b],
  item / other_item ({"type": track|via|pad|zone} + ends/at/ref+pad), refs (pad refdes only, empty
  for track/via/zone pairs), msg, items[] (msg+pos, NO uuid). check_creepage.py:214-223.

## Is it real?
- Check the COATING first. Row selection defaults to coating "none", so a track adjudicates on B2
  (0.60 mm at 51-100 V) instead of B4 (0.13 mm). verify_all passes only --constraints
  (verify_all.py:52-53), so `--coating` cannot change the gate - the `"coating"` key in
  constraints.json can. lumina-carrier declares no key: 27 findings default, 26 at soldermask.
- A soldermask declaration will NOT clear a pad finding: mask relief exposes lands, so pads stay on
  A6 under any mask (IPC-2221 6.3.4; check_creepage.py:106-109).
- A pad pair inside ONE footprint is not placement's to fix - since T6 the check encodes that:
  same-footprint pad pairs arrive as WARNING with `waiver_class: "land_pattern_pitch"` and
  `same_footprint: true` (16 of lumina-carrier's 26 were that; summary carries
  `same_footprint_under`). Moving the part changes nothing, and a DRU floor above a package's own
  pad gap is unsatisfiable (1280). The class is parts/library (P3) scope - a CUSTOM-edited land
  pattern under this waiver still deserves a look before you trust it.
- Absence is not safety. Netless copper carries no declared voltage and is invisible: J1's board
  locks sat 0.66 mm from 57 V and neither this check nor DRC saw them (1785). An equal-potential
  pair (bridge input, 0.3295 mm at 57 V - 1600) is seen ONLY if declared in `voltage_pairs`.
- Blind to isolation barriers: magjack/opto/isolated-DCDC spacing comes from the part datasheet and
  hipot rating, not `voltages[]` - a 1.05 mm barrier collapse passes at 0.635 mm (1110). A shell
  board-lock pad against an HV tap IS voltage-derived and does get caught (1226).
- The count is not the population: hits are deduped at 0.1 mm and capped at 500 per (net pair,
  layer). Read `checked[].pairs[].pairs_under_requirement` / `truncated` (check_creepage.py:83-84).

## Fix ladder (cheapest first)
1. Re-adjudicate before spending budget: re-run with `--coating soldermask` if the board is masked.
   If the finding vanishes it is an input defect - request the constraints `"coating"` key in OPEN
   and touch no copper. Pre-T2 this gap cost two fixer attempts and one retracted edit (1851).
2. Split what is left by `rows` and `item`/`other_item`: intra-footprint pad pairs -> parts;
   track-to-track (no refs) -> router; pad-to-pad across DIFFERENT parts -> yours.
3. Only then move a part: place_edit.py --pcb <board> --ops <ops.json> (absolute ops), then
   place_metrics.py. A move on a routed board strands copper: flag the re-route in your summary.
4. Fix the INPUT where the geometry is right: an equal-potential pair needs a `voltage_pairs` entry
   {"a","b","voltage"}; netless copper needs a .kicad_dru rule (`B.NetName == ''`, 1785). Neither
   file is yours to edit - put both in OPEN.
5. Escalate: report requires_pipeline_rewind, the needed domain (router / parts / library), and the
   surviving pair list with `rows` and `spacing_mm`.

## Do not
- Do not re-derive the gap by hand: `pos` is the real nearest-point midpoint, and the sweep is
  item-level, so every violating pair is already reported - not just the worst (pre-T2 defect, 1889).
- Do not put a clearance floor on the HV NETCLASS: pad-blind, 30 instant DRC errors between
  adjacent pins of one package (1294); above a package's own pad gap it makes pads unroutable (1280).
- Do not scope a DRU rule by `B.NetName ==`; partial coverage reads as protection and is not (1785).
- Do not trade a pad gap for an annular ring: gap = c_c-(w_a+w_b)/2, ring >= 0.15 mm, check both (1226).
- Do not read the LEARNINGS creepage entries as current behaviour when your report has the T2
  fields (all pairs, seven rows + coating, voltage_pairs, item-level geometry) - then they are
  history, not the live defect list. On a pre-T2 report they still describe what you are holding.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_creepage.py --pcb boards\<name>\kicad\<name>.kicad_pcb --constraints boards\<name>\kicad\constraints.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate verify boards\<name>\kicad\<name>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-29 [check_creepage][gates] worst-pair-only, 216 hidden (1889); net-to-REF (1600)
- LEARNINGS 2026-07-29 [check_creepage][gates][ipc] cannot express a COATED board (1851);
  2026-07-30 [kicad_dru][check_creepage][safety] audit DRU COVERAGE (1785)
- LEARNINGS 2026-07-29 [routing][kicad] netclass PAD-BLIND (1294); [routing][drc] HV rule unroutable
  (1280); [magnetics][check_creepage] board-lock pads (1226); 2026-07-28 magjack (1110)
- check_creepage.py:83-84, 102-111, 165-231; lib/checklib.py:46-62; verify_all.py:52-53;
  gate.py:108-112; cluster_violations.py:47; fix_dispatch.py:63-74; reference/gates.yaml:80-89
- Measured 2026-08-06 on boards/lumina-carrier: 27 default / 26 --coating soldermask, 16 intra-fp
