# hwde-led-test - P8 board review (verify-reviewer, fresh context)

Inputs: reports/checks/summary.json (14/14 checks ran, none skipped, 2 warnings),
reports/verify-gate.json (PASS), renders reports/renders/hwde-led-test_top.png,
hwde-led-test_bottom.png, hwde-led-test_iso.png (review set, w 2400),
reports/hwde-led-test-schematic.pdf, requirements.md, architecture/, kicad/constraints.json,
parts/parts.json.

Result: 0 errors, 3 warnings. Nothing found that stops this board working on the bench.

## Findings (worst first)

1. WARNING, silk: J1 polarity is not marked (hwde-led-test_top.png, _iso.png).
   The J1 silk is a plain rectangle with no "+", "5V" or "GND". Pin 1's square pad
   shows only on the bottom (hwde-led-test_bottom.png) and is hidden under the
   header's plastic on top. requirements.md section 3 drops reverse protection
   because "the LED blocks reverse current". With the leads swapped, D1 sees about 5.25 V
   reverse (C1 and R1 take it with no problem). Green InGaN 0603 LEDs are usually rated VR = 5 V, and
   parts.json carries no VR for KT-0603G. Marking the pins is the cheap protection.
   Fix: "+" / "-" text at J1 pins 1/2. The bottom side is empty, so B.SilkS fits easily.

2. WARNING, fab: the board is below JLC's minimum board size for PCBA.
   The outline is 13.05 x 9.08 mm. reference/jlc_capabilities.yaml records a 10 x 10 mm
   board minimum, and requirements section 7 says JLCPCB PCBA. The board also has no fiducials.
   Fix it at the fab step: panelize with rails (JLC puts the fiducials on the rails), or grow the
   outline to 10 mm or more in Y (placement or outline fit). The P6 decision to fit at 2 mm margin was
   recorded and section 5 is a soft target, so the size itself is not drift. Only the
   PCBA minimum is the issue. The board is not to be ordered now, so this is a warning.

3. WARNING, silk: the C1 refdes is ambiguous (hwde-led-test_top.png).
   The "C1" text sits at about (15.2, 22.3), under the gap between R1 and D1 and roughly
   equidistant from C1, R1 and D1. Move it beside C1.

## Hunt list, cleared
- Connector mating: J1 is a vertical 2.54 mm header with mating from above (iso render). Nothing tall
  is near it. C1 (0.8 mm tall, pad edge at about 12.7 mm) is clear of a 2.54 mm Dupont housing
  (header body edge at 12.17). J1 is centred on the left edge as constraints.json asks
  (edge left, pos 0.5: J1 y 20.2 = board centre).
- Polarity after assembly: the D1 outline has a chamfer at the cathode end, outside the body, so it
  stays visible after assembly. Footprint pad 1 = K = GND matches symbol pin 1 = K and
  the schematic. The small diode glyph inside the outline is hidden under the body (not a defect).
- Cross-artifact: all requirements interfaces are present (J1 +5V/GND, D1 green 0603, R1 0603,
  C1 0603 4.7 uF 16 V X5R at J1). All three architecture blocks are present. No mounting holes, as
  stated. 2 layers. SMD top side only. The change from R1 680R (architecture) to 470R is a recorded P3
  decision (real Vf 3.1 V, giving 3.5-4.6 mA over 4.75-5.25 V, inside the 3-5 mA promise), so it is not drift.
  Schematic, PCB and parts.json values match. D1 is a JLC extended part. parts.json records that no
  basic 0603 green exists (only an 0805 one), so that is justified.
- Return path and decoupling: +5V runs J1.1 -> C1.1 -> R1 (the input cap is in the path). C1 GND drops
  through a via at (13.2, 21.74) to the B.Cu GND pour, about 2.3 mm from J1.2, which is thermal-tied
  to the pour. D1 cathode has its own via. This is fine at 5 mA.
- EMI, RF, crystal, switcher, thermal: none on this board.
- Routing visual: straight runs and 45-degree runs only. The +5V path steps down into C1's pad and back
  up to R1, which is deliberate (it routes through the cap pad). It is not a needless detour.

## Warning triage (verify_all)
- check_route_style jog, /LED_A F.Cu (17.06, 18.975), 0.15 mm sidestep: a real but cosmetic defect.
  R1 pad 2 is at y 18.9 and D1 anode at y 19.05. The cheapest fix is to move D1 from
  (18, 19.8) to (18, 19.65) so the trace runs straight (placement, then reroute that one net).
  A waiver is also acceptable for a test board. Recommendation: fix it if anything else
  is touched, otherwise waive.
- check_ratings rating_unrated D1, R1: RECOMMEND WAIVER. R1 does carry a rating
  ("Voltage-Supply(Max)" 75 V) that the checker did not recognise. It sees at most 5.25 V. D1 is
  forward-biased in normal use (about 3.1 V). Its only overstress case is reverse polarity, which is
  handled by finding 1 and listed under OPEN.

## Waivers recommended (human decides at checkpoint 4)
- check_ratings D1/R1 (reason above).
- route_style jog on /LED_A, only if the D1 nudge is not taken.
