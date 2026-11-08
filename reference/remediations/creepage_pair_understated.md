# creepage_pair_understated

A constraints.json `voltage_pairs` entry declares a differential BELOW the larger declared
`voltages` figure of one of its own two nets, and carries no `"why"`.

- Emitted by: scripts/check_creepage.py (`understated_pairs`, #651)   Gate: verify (P8)
- Severity: warning. Fixer domain: review - the fix is the constraints entry, not copper.
- Fields on the violation: net (`a`), other_net (`b`), pair_voltage, node_net (the net with the
  larger declared voltage), node_voltage, msg. No pos/layer: it is a finding about an input.

## Why it matters
An explicit pair OVERRIDES the voltages-derived difference for that net pair, and rules_gen.py
writes the board's pair-scoped DRC rule from the same number. A pair written too low therefore
relaxes BOTH the creepage check and DRC at once. hw-lab boards/mains-psu declared HV_BULK-DRAIN
at 219 V on a flyback; with the FET on, DRAIN sits near 0 V and the full 375 V bulk is across
the pair. DRC passed 1.25 mm where IPC-2221 wanted 2.5 mm. A pair declared <= 30 V is worse:
it waives the derived check outright.

## Is it real?
- Work out the largest voltage that ever sits between the two nets across every operating
  state (switch on and off, start-up, fault, surge clamp level). The node voltages bound it when
  the other net can reach 0 V - a switch node, a drain, a rectifier output.
- It is a false alarm only when the two nets provably move together: an AC-coupled pair, both
  ends of a sense resistor, two taps that never separate by more than the declared figure.

## Fix
1. Understated: raise `voltage` to the real worst case, then re-run check_creepage and
   rules_gen (the DRC rule follows the new figure). Expect real `creepage` findings to appear.
2. Correct as written: add `"why": "<one line saying why the nets never see more>"` to the entry.
   The warning goes and the reason stays in the file for the reviewer.
3. Neither file is a fixer's to edit - put the change in OPEN.

## Verify
```
bin/hwde check_creepage --pcb boards/<name>/kicad/<name>.kicad_pcb --constraints boards/<name>/kicad/constraints.json
```

## Sources
- hive-hwde issue #651 (hw-lab mains-psu HV_BULK-DRAIN, 219 V declared, 375 V real)
- check_creepage.py `understated_pairs`; rules_gen.py voltage_pairs rule emission
