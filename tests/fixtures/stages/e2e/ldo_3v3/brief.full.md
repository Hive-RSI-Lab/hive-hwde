# ldo 3v3 (e2e bench brief, full)

A linear regulator bench board. Input 5 V DC on a screw terminal; output
3.3 V at up to 500 mA on a screw terminal. It must hold regulation at full
load with no airflow.

Specification:

- Input 5 V DC from a bench supply. Reverse polarity protection is not
  required.
- Output 3.3 V, 0 to 500 mA continuous, at 25 C ambient in still air.
- The regulator's junction stays at least 15 C below its rated maximum at
  full load, so size the copper under it for the heat it dissipates.
- Input and output capacitors as the regulator's datasheet requires for
  stability, each placed next to its pin.
- Two layers, assembled by JLCPCB; prefer JLCPCB basic parts.
- 2-pin 5.08 mm screw terminals, input and output on opposite board edges.
- A test point on the output and on ground.
- Keep the board small and the BOM cheap: both are judged.
