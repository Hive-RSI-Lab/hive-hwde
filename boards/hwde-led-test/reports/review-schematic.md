# Schematic review - hwde-led-test (P4, adversarial)

Inputs: kicad/hwde-led-test.kicad_sch (rendered to reports/schematic.pdf, 1 page), reports/top.net,
requirements.md, architecture/constraints.json, parts/parts.json, lib/aiee.* (symbols and footprints).
There are no parts/<lcsc>.json datasheet extractions. Build mode: none (normal design).
Checklists applied: connector.md (J1) and power.md (the input-cap and abs-max items only; there is no regulator).
mcu.md and interface-usb.md do not apply.

Result: 0 errors, 2 warnings.

## What was verified (correct)
- Netlist: +5V = J1.1, C1.1, R1.1; /LED_A = R1.2, D1.2(A); GND = J1.2, C1.2, D1.1(K). This matches
  architecture/sheets.md and requirements section 2 (J1 pin 1 = +5V, pin 2 = GND).
- LED polarity: the symbol puts K on pin 1 and A on pin 2, and the triangle points at K. The anode goes to the resistor
  and the cathode to GND, so the LED is forward biased from +5V. Footprint LED0603-RD has pad 1 on the left, the
  chamfered or cathode-marked end, and the silk triangle points at pad 1, so it agrees with the symbol.
- R1 470R: 4.0 mA nominal at 5.0 V with Vf 3.1 V, and 3.5-4.6 mA across 4.75-5.25 V. Dissipation is about 9 mW
  against a 100 mW rating, so it is fine.
- C1 4.7 uF 16 V X5R 0603 sits directly across J1 +5V/GND. Under DC bias at 5 V it derates to roughly 2-3 uF effective,
  which is still within the 1-10 uF intent. The 16 V rating is above the required 10 V.
- Reverse plug: the requirements waive protection explicitly. A reversed supply puts at most 5.25 V reverse across D1,
  with the current limited by the 470R. C1 is non-polar. This is not destructive on a bench, so it is not a finding.
- No ESD or protection findings: this is a bench test board, the exclusion is stated in the requirements, and J1 is not
  a user-facing product connector.
- PWR_FLAGs are on +5V and GND. ERC is 0/0.

## Findings
1. WARNING bom-fields-missing (J1, C1, R1, D1). The placed symbol instances have no MPN or "LCSC Part"
   property. Those exist only in the embedded lib_symbols, so the netlist comp fields carry none. The footprints
   carry "LCSC Part", but scripts/bom_cpl.py board_part_fields matches only a property named exactly "LCSC", and
   parts.json has no per-ref mapping. The likely result is a JLC BOM with empty LCSC columns. Fix: add instance
   LCSC/MPN fields, or confirm board_init's field copy produces an "LCSC" footprint field.
2. WARNING led-current-margin (R1, D1). The datasheet would not download, so Vf max is unknown. At Vf 3.4 V
   (common for 0603 InGaN green), Vin 4.75 V and R +1%, the current is 2.8 mA, just under the 3 mA target. A lower R1 such as 390R
   would push the low-Vf, high-line case over 5 mA, so the fix is to get the real Vf min/max first, or to accept the margin. This does not affect
   bring-up.

## Readability note (not a finding)
The power-port labels at J1, C1 and R1 overlap the pin-number text in the PDF. Connectivity is unaffected.
