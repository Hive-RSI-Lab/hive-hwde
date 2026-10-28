# Checklist: connector (every external connector)

- Pinout vs the MATING side (cable/debugger/host), not just internal nets;
  pin-1 convention stated and marked on silk.
- Power pins: polarity legend on silk visible AFTER assembly; reverse-plug
  consequence stated (protected or destructive - if destructive, flag).
- Mount style matches the exact orderable variant (vertical/RA, SMD/THT);
  shield strategy stated (chassis vs signal GND, one point or direct).
- Mechanical: retention (THT or SMD-with-anchors for force-loaded
  connectors); edge overhang intended; no tall part blocking mating.
- Mating: a horizontal connector's MOUTH (not its back) faces its nearest
  board edge and sits at or over it, and nothing stands in front of it out
  to a plug's length plus a grip; a vertical one has finger room round it
  with no tall part beside it. The place and verify gates check this
  (`mating_*` findings, reference/connector_mating.yaml); a connector they
  cannot read (`mating_direction_unknown`) is checked by eye on a side
  render and given a `mouth` entry in that table.
- ESD protection expectation for user-facing connectors met or waived.
- Hand-solder access if not PCBA-assembled.
