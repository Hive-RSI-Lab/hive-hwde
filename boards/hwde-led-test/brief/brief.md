# hwde-led-test - brief

The Hive's first hwde board: a tiny, quick test run that proves the pipeline
goes from requirements to checked KiCad files inside the Hive. It is not to be
ordered; ordering stays the person's.

A tiny 2-layer board that lights one LED from 5 V.

- Input: 5 V DC (4.75-5.25 V) on a 2-pin 2.54 mm pin header, J1: pin 1 = +5V,
  pin 2 = GND. From a bench supply or a USB 5 V lead. No reverse-polarity
  protection wanted (the LED itself blocks reverse current; a test board).
- One green LED, 0603, lit at about 3-5 mA through one 0603 series resistor.
- One 0603 ceramic capacitor (1-10 uF, >= 10 V) across the input, at J1.
- Parts from the JLCPCB basic library wherever one fits; SMD parts on the top
  side only, for JLCPCB assembly; the header may be through-hole.
- Board: 2 layers, 1.6 mm FR-4, HASL, as small as practical (about 15 x 12 mm
  is fine, no HARD cap), no mounting holes, no enclosure.
- Quantity if ever built: 5. Room temperature, bench use.
