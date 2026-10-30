# hwde-led-test - first-spin review: parts

Cold review, 2026-10-29. Datasheets fetched from LCSC 2026-10-29 into /tmp/ds/<ref>/:
XFCN PZ254V-11-XX drawing rev A1 (2024-06-11); Samsung MLCC catalog (Nov 2015);
UNI-ROYAL 0603WAF chip resistor datasheet; KENTO KT-0603G "0603-0.6" spec rev A.0 (2018-12-06).

Verdict: pin-to-pad correct on all four parts. 0 blockers, 0 majors, 1 minor, 3 notes.

## Findings

**PARTS-1 (minor, DFA-09) Two extended parts.** J1 C492401 and D1 C12624 are extended
(parts.json basic=false). No basic 0603 green exists: live parts_search C72043 basic=False,
stock 0; basic-only 0603 green search 0 rows. Cost only.

**PARTS-2 (note, CONC-12) D1 pin numbering differs between maker and footprint.** KENTO p.2
numbers pin 1 = anode (+), pin 2 = cathode (green mark at the pin-2 end). EasyEDA C12624 and
this board number pad 1 = K. By role the board is right: pad 1 (18, 20.55) K on GND, pad 2
(18, 19.05) A on /LED_A, silk triangle and chamfer at pad 1; cpl_verify (EasyEDA model,
basis K/A role) ok at 90 deg. Open: JLC's preview of the cathode mark at order.

**PARTS-3 (note, DFA-13) 0603 lands slightly larger than maker lands.**
C1 width 0.90 vs Samsung b < W = 0.80; R1 outer 2.31 vs UNI-ROYAL D = 2.1; D1 outer 2.3 vs
KENTO 2.1 (pad 0.8 vs 0.7, gap 0.70 = 0.70). All cover the terminations; no risk at 0603.

**PARTS-4 (note, DFA-10) D1 lib symbol named "0603 Green 509-620mcd"**; KT-0603G is 210-430 mcd
(p.3; bins to 520). Instance fields right.

## Pin-to-pad (each against the datasheet)

| Ref | Datasheet | Board | Result |
|---|---|---|---|
| J1 | 1x2, pitch 2.54 +-0.05, pin 0.64 sq (diag 0.905), hole dia 1.02 | pads 2.54 apart, drill 1.20, pad 2.0 (pin 1 rect) | pass: 0.15 mm radial clearance to pin diagonal, annulus 0.40 |
| J1 pinout | brief: 1 = +5V, 2 = GND | pad1 +5V (10.9,18.93) silk '+'; pad2 GND (10.9,21.47) silk '-' | pass |
| C1 | 0603, 1.6x0.8x0.8, 16 V X5R (CL10A475KO8NNNC) | C0603, pad1 +5V, pad2 GND, 1.4 pitch | pass (non-polar) |
| R1 | 0603 1.6x0.8x0.45, 470R 1 % | R0603 pad1 +5V, pad2 /LED_A | pass (non-polar) |
| D1 | 0603 1.6x0.8x0.6, K/A by mark | pad1 K GND, pad2 A /LED_A | pass by role (PARTS-2) |

Ratings are in power.md. Stock (parts.json, undated): 0.42-4.8 M each; D1 has alternates
C84267, C2986059.
