# reg_input_no_hf

A regulator input pin whose decoupling associations are marked `role: "reg_input"` has no
HF-capable ceramic (<= 1 uF, or explicit `class: "hf"`) with its rail pad within the hf-class
error distance (7.5 mm) of the pin. On a switching regulator the input ceramic carries the full
trapezoidal switch current; a lone bulk cap - however legal its own bulk-class distance - leaves
volts of VIN ringing per edge and a radiating loop (lumina-carrier R1: the only soldering-iron
rework on the shipped batch).

- Emitted by: scripts/check_decoupling.py (kind="reg_input_no_hf")   Gate: verify (P8, via verify_all.py)
- Fixer domain: schematic (cluster_violations.py) - the usual fix is a MISSING part, not a move.
- Fields: pos = the regulator PIN pad, net (rail), refs [ic + the declared input caps],
  pin ("U21.3"), nearest_mm (closest declared input cap), limit_mm. One violation per
  (ic, pin, rail) group; severity is always error.

## Is it real?
- The group only exists because metadata DECLARED the pin a switching-regulator input; the
  check cannot infer it. If the part is not a switching regulator (linear reg, load switch),
  the `role` tag is wrong - fix decoupling.json, not the board.
- A qualifying cap may exist on the board but not in the metadata: check the schematic for a
  <= 1 uF ceramic on the same rail near the pin that simply lacks an association (run
  netlist_audit.py --decoupling). Metadata gap, not a board defect.
- A cap between 1 uF and the bulk sizes (e.g. 2.2 uF MLCC) hard at the pin is electrically
  arguable as the HF cap; declare it `class: "hf"` explicitly if the design intends that -
  the check honors the explicit class over the value cutoff.
- nearest_mm just over 7.5 with an otherwise-qualifying ceramic is a placement nudge, not a
  missing part - see ladder step 2.

## Fix ladder (cheapest first)
1. BEFORE P5 (schematic-only state): add a 100 nF (X7R, rail-rated; a 1 uF alongside is
   better) at the pin in the schematic block, associate it with `role: "reg_input"`, and put
   it in the regulator's placement group so the annealer holds it at the pin. Cheapest by far
   - after P5 any added part costs board surgery (LEARNINGS 2026-07-30 line 2001).
2. Qualifying ceramic exists but sits past 7.5 mm: move it to the pin with place_edit.py and
   re-gate. AFTER routing has started a move strands copper - validate with kc.py drc /
   drc_routed, not this check alone.
3. On a placed/routed board with no such cap: board_update.py applies the netlist diff while
   keeping copper (regen the schematic with the cap, re-export the netlist, then follow
   reference/recipes/add-part.md; region scan for added parts is front-side only). Add the
   new cap to the regulator's placement group AND to decoupling.json with the role.
4. On SHIPPED hardware this is a hand-rework instruction, not a file edit: fit the ceramic
   from the VIN pin to the nearest ground via; mind adjacent SW pins when bridging pin-to-pin
   (carrier U21: pin 3 to the GND via 2.335 mm away was chosen over bridging pin 2/SW).

## Do not
- Do not silence it by deleting the `role` tag or relaxing max_dist_mm: the group check IS
  the recording of a defect class that per-cap value classing cannot see (LEARNINGS
  2026-08-14 line 3186) - a 22 uF reads as a well-placed bulk cap while the HF cap is absent.
- Do not "fix" it by re-declaring the bulk cap `class: "hf"` unless the design genuinely
  uses it as the HF loop - that erases the finding, not the ringing.
- Do not move the regulator; the cap serves the pin.
- Do not treat a pass as loop-quality proof: the group check is presence/distance only; the
  per-association loop_nh checks still judge each cap.

## Verify
```
.venv\Scripts\python.exe .claude\skills\hwde\scripts\check_decoupling.py --pcb <ws>\kicad\<board>.kicad_pcb --metadata <ws>\kicad\decoupling.json
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate verify <ws>\kicad\<board>.kicad_pcb
```

## Sources
- LEARNINGS 2026-08-14 [decoupling][gates][schematic] value-classing cannot see a missing
  cap (line 3186); LEARNINGS 2026-07-30 [pipeline] no incremental board update - parts added
  after P5 cost P6+P7 (line 2001)
- boards/lumina-carrier/reports/retrospective-2026-08-07.md s5 (R1: U21 TPS563201, nearest
  input cap 9.89 mm, no HF ceramic anywhere; rework anchors measured there)
- check_decoupling.py (REG_INPUT_HF_MAX_F, check_reg_inputs); TI TPS563201 layout guidance
  (input ceramic directly across VIN/GND)
