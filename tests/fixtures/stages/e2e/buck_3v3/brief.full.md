# buck 3v3 (e2e bench brief, full)

A step-down supply board. 12 V DC in (9-15 V) on a 2.1 mm barrel jack;
3.3 V at up to 1.5 A out on a screw terminal; a power-good LED. The output
must stay within 3% across component tolerances.

Specification:

- Input 12 V nominal, 9 to 15 V, centre-positive 2.1 mm barrel jack.
- Output 3.3 V, 0 to 1.5 A continuous, within 3% over the tolerance of
  every part that sets it (use 1% feedback resistors and count the
  reference's own tolerance).
- A synchronous buck with an integrated switch is the expected answer; set
  the inductor and output capacitors from the datasheet's design procedure.
- A power-good LED driven from the converter's PG pin, or from the output
  if the part has none.
- Two layers, assembled by JLCPCB; prefer JLCPCB basic parts.
- Keep the switching loop (input cap, switch node, inductor, output cap)
  tight, with an unbroken ground return under it.
- A test point on the output, the switch node and ground.
- Keep the board small and the BOM cheap: both are judged.
