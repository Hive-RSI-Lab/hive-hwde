# Every first-spin finding and what was done about it

Written by `first_spin.py` from the domain reviews and dispositions.json.

| Finding | Board | Severity | What it was | Disposition | How, with the margin |
|---|---|---|---|---|---|
| DFM-1 | hwde-led-test | minor | The saved B.Cu GND zone fill is stale: it clears J1 pad 1 (+5V, THT, solder side) by only 0.1275 mm, while the zone's own clearance is 0.5 mm. A refill gives 0.5005 mm. The fab Gerbers carry the stale fill (fresh export from the saved board diffs 0 lines against fab/gerbers). DRC and dfm_check pa... | - |  |
| PARTS-1 | hwde-led-test | minor | Two extended JLC parts (J1 C492401, D1 C12624): an extended-part loading fee each on a 5-board order. No basic 0603 green LED was found: live parts_search for C72043 (Everlight 19-217/GHC-YR1S2/3T) returns basic=False, stock 0; basic-only 0603 green search returns 0 rows. | - |  |
| POWER-1 | hwde-led-test | minor | LED current spans 3.46-5.77 mA over the input range (4.75-5.25 V), R1 +-1% and the KT-0603G VF spread; 6.28 mA at a 5.5 V USB source. The brief asks for about 3-5 mA; the high corner is 15% over. R1=470R was chosen on VF=3.1 V read as typical, but the datasheet gives 3.1 V as the MAX (min 2.6 V, ... | - |  |
| POWER-2 | hwde-led-test | minor | Reverse polarity on J1 (the brief's stated protection is 'the LED blocks reverse current') puts about 5.25 V reverse across D1; KT-0603G VR abs max is 5 V. J1 is an unkeyed 2-pin header, so a reversed Dupont lead is a plausible connection. check_ratings ran and did not flag it (D1/R1 have no extr... | - |  |
| DFM-2 | hwde-led-test | note | Board is 13.40 x 10.50 mm with no fiducials; the short side is 0.5 mm over the commonly quoted JLC PCBA 10 mm minimum. JLC may add edge rails/panelize for assembly (cost, not function). | - |  |
| DFM-3 | hwde-led-test | note | Both GND vias are JLC-standard 0.6/0.3 (annulus 0.15 = floor) and sit 0.13-0.14 mm from SMD pad edges (D1 pad 1, C1 pad 2). They are tented (no F/B mask openings), same net, so wicking risk is low. | - |  |
| DFM-4 | hwde-led-test | note | No board name, revision or date on silk; only refdes and the J1 '+'/'-' marks. | - |  |
| PARTS-2 | hwde-led-test | note | KENTO numbers the LED pins 1 = anode (+), 2 = cathode (green corner mark at the pin-2 end); the EasyEDA C12624 symbol/footprint and this board number pad 1 = K. The board is right by role (pad 1 K on GND, pad 2 A on /LED_A, silk triangle and chamfer at pad 1), but any pin-number compare against t... | - |  |
| PARTS-3 | hwde-led-test | note | The three 0603 lands are the generic LCSC/EasyEDA patterns, slightly larger than the makers' recommendations: C1 pad width 0.90 mm vs Samsung 2/3W < b < W (0.53-0.80 mm); R1 0.806x0.864 at 1.5 pitch (outer 2.31 mm) vs UNI-ROYAL 0603 land D = 2.1 mm, C = 0.8 mm; D1 0.8x0.8 at 1.5 pitch (outer 2.3 ... | - |  |
| PARTS-4 | hwde-led-test | note | D1's library symbol is named/valued '0603 Green 509-620mcd' while the placed part is KT-0603G (IV 210-430 mcd typ range, bins P24-P28 up to 520 mcd). Instance fields (MPN, LCSC C12624, value KT-0603G) are right; the lib metadata is stale. | - |  |
| POWER-3 | hwde-led-test | note | No overvoltage protection (brief accepts). R1 is the first part to exceed its rating: 100 mW at Vin = 2.6 + sqrt(0.1*465.3) = 9.4 V continuous. A 12 V adapter gives R1 (12-2.6)^2/465.3 = 190 mW (1.9x) and D1 20 mA (<30 mA). C1 16 V. | - |  |
| POWER-4 | hwde-led-test | note | Hot-plug onto a live 5.25 V supply rings the 4.7 uF MLCC with the lead inductance: 8.9 V peak (4.7 uF), 9.3 V (2.6 uF after DC bias). Within C1 16 V and D1 IFP 60 mA; no action. | - |  |
| POWER-5 | hwde-led-test | note | check_observability reports +5V blind (no test point). J1 pins 1/2 are bare 0.64 mm header posts on +5V/GND and serve as the probe point; D1 is the board's only status output. Bring-up reading: 3.4-5.8 mA into J1 at 5.0 V (nominal 4.6 mA) and D1 lit; >6 mA or 0 mA is a fault. | - |  |

## Still open

- parts:3 (unknown_open, major): D1 as JLC actually loads it: reel orientation and JLC's model registration put the cathode mark on pad 1 (DFM placement preview)

## Tools wanted

- **check_observability connector posts** (power): count header/connector pins on a rail as probe points
- **check_ratings (two-terminal reverse)** (power): VR of LEDs/diodes against the reversed input when the input connector is unkeyed, from parts.json attributes or the datasheet when no extraction exists
- **dfm_check margin-to-floor report** (dfm): list features within 5% of a fab floor (clearance, annulus, silk)
- **led-indicator corner calc (eng_calc or sim_board block)** (power): LED current at Vin min/max x R tolerance x VF bin min/max, and flags a VF taken from a max column as typical
- **pin-numbering cross-check** (parts): datasheet pin numbering vs EasyEDA/footprint numbering for polarized 2-terminal parts, by role (K/A), reported when they disagree
- **zone fill freshness (dfm_check or gate)** (dfm): refill a copy of the board and compare filled polygons with the saved fill; flag any change, and any fill closer than the zone's own clearance

## Fault classes

| Class | Mode | Worst | Verdict | Check | Findings |
|---|---|---|---|---|---|
| DFA-09 | Part out of stock, extended-part cost, no alternate | minor | check_blind | order_quote | PARTS-1 |
| RATE-03 | Reverse polarity or input above supply | minor | check_blind | check_ratings | POWER-2 |
| new | Zone fill saved stale (not refilled after an edit), so the copper and Gerbers carry clearances the zone's own rule forbids while DRC and DFM pass at the fab floor | minor | new_class | - | DFM-1 |
| RATE-23 | Invented or misread datasheet values drive the design | minor | no_check | - | POWER-1 |
| TEST-01 | No test points on rails | note | caught | - | POWER-5 |
| DFA-10 | Schematic and board fields out of sync | note | check_blind | kc.py | PARTS-4 |
| DFA-13 | Land pattern wrong | note | check_blind | fp_verify | PARTS-3 |
| FAB-07 | Via in pad not filled or not tented | note | check_blind | dfm_check | DFM-3 |
| CONC-12 | Datasheet pinout read wrong | note | no_check | - | PARTS-2 |
| DFA-15 | Panel rails and tooling | note | no_check | - | DFM-2 |
| PROT-04 | No overvoltage or reverse protection on input | note | no_check | - | POWER-3 |
| PROT-07 | Hot-plug transient exceeds ratings | note | no_check | - | POWER-4 |
| TEST-08 | No board name, revision and date on silk | note | no_check | - | DFM-4 |
