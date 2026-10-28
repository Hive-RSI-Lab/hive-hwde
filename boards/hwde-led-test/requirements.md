# hwde-led-test - requirements

## 1. Function
A tiny 2-layer test board that lights one green 0603 LED from a 5 V DC input
through one 0603 series resistor, with one 0603 ceramic input capacitor at the
input header. Its purpose is to prove the hwde pipeline runs from requirements
to checked KiCad files inside the Hive. It is not to be ordered; ordering stays
with the person. No declared mode (normal design).

## 2. Interfaces
- J1: 2-pin 2.54 mm pin header (through-hole allowed). Pin 1 = +5V, pin 2 = GND.
  Fed from a bench supply or a USB 5 V lead.
- D1: one green LED, 0603, lit at about 3-5 mA.
- No debug connector, no MCU, no buttons, no other I/O.

## 3. Power
- Input: 5 V DC, range 4.75-5.25 V, on J1.
- No reverse-polarity protection (stated: the LED blocks reverse current; test board).
- One rail only (+5V). LED current target 3-5 mA via one 0603 series resistor
  (value to be chosen at design time from the LED Vf; ASSUMED: LED Vf about 2.0-3.0 V
  for green, so roughly 470 ohm-1 k ohm).
- Input capacitor: one 0603 ceramic, 1-10 uF, >= 10 V, across the input at J1.
- Total load about 5 mA max (guess). No battery, no charging.

## 4. Environment
Room temperature, bench use. No enclosure, no ingress, no vibration requirement.

## 5. Size & mounting
- Outline: as small as practical, about 15 x 12 mm is fine. SOFT target, no HARD cap.
- Mounting holes: none. No enclosure.
- Height limit: none stated.
- Stackup: 2 layers, 1.6 mm FR-4, HASL.

## 6. Quantity & budget
Quantity if ever built: 5. Target unit cost: not stated (ASSUMED: not a constraint).

## 7. Assembly
JLCPCB PCBA. SMD parts on the top side only (single-sided SMD assembly); the
header J1 may be through-hole. Use JLCPCB basic-library parts wherever one fits.

## 8. Compliance/safety flags
None apply: no mains, no battery, no motors, no voltage above 30 V, no current
above 3 A, no RF transmit. (5 V DC input, about 5 mA load.)

## 9. Open questions
none
