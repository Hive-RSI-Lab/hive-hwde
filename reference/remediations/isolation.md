# isolation

A declared isolation barrier (constraints `isolation`) doesn't hold on this board: copper too close
or touching across it, or a part across it with no rating that covers it.

- Emitted by: scripts/check_isolation.py   Gate: verify (P8, via verify_all.py, checks.d)
- Tables: reference/iso_tables.yaml. A finding with `table_verified: false` rests on a row or rule
  nobody has read; read the standard before spending layout on it, then flip the row.
- Kinds: iso_touching, iso_clearance, iso_creepage, iso_inner, iso_solid, iso_floating_bridge,
  iso_part_unrated, iso_part_rating_short, iso_part_pin_gap, iso_net_undeclared, iso_part_absent.
- Fields: net, other_net, barrier, domains, spacing_mm, required_mm, creepage_mm + path (the surface
  path's points), refs, shortfalls. The report's `checked[0].barriers[]` has `required` with each
  number's basis, each layer's clearance and creepage, and every spanning part's pin_gap_mm.

## Is it real?
- Check the declaration first: every value is the board's. A `withstand_v_rms` sends the clearance to
  Table F.7a at the peak: 5 kV rms is 7.07 kV peak, 9.56 mm in an inhomogeneous field. A 5 kV
  isolator's 8 mm package does not make that 8 mm on the board.
- A slot only helps creepage, only when wider than X (1 mm at PD2), and only when it runs past the
  copper's own edges: the path walks round its end. Clearance through air ignores it.
- Inner findings without inner_min_mm are warnings on a rule from memory. Declare the number your
  fab, test or safety house gave you.
- iso_floating_bridge: stitching vias, netless pours and mounting pads in the gap. Named nets are
  taken as one conductor, netless copper island by island.

## Fix ladder (cheapest first)
1. Input: a net in the wrong domain, a missing part rating, an undeclared net.
2. iso_part_unrated: a bleed resistor or ESD cap across the barrier needs a rated part (a Y cap) or
   to go. A solder jumper across a barrier is a crossing.
3. Copper: move it out of the gap. Run rules_gen: its aiee_iso_* rules hold pours, DRC and route_auto
   out. route_critical, route_finish and route_cleanup only know net-wide clearances, so check after them.
4. iso_creepage with clearance fine: a slot wider than X, longer than the copper either side. Then
   declare slot_earns_creepage on the barrier: DRC can't see the path round a slot, so its rule falls
   to the clearance number and this check judges the creepage.
5. iso_part_pin_gap: a footprint whose land row is closer than the barrier. Use the part's own
   land pattern or a wider package; the part's rating doesn't cover the board's lands.

## Do not
- Do not lower working_v_rms or the grade to clear a finding: those are the product's facts.
- Do not count solder mask. Coating credit needs IEC 60664-3, which isn't implemented here.

## Verify
```
bin/hwde check_isolation --pcb boards/<name>/kicad/<name>.kicad_pcb --constraints boards/<name>/kicad/constraints.json
```
