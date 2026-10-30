# ultra-front - learnings

## 2026-10-28 [P3][library] EasyEDA courtyards are still the body outline, on every pulled footprint
11 of 11 lib_pull footprints had pads outside F.CrtYd (R0603 +-0.80 x 0.40 vs
pads to +-1.15). kicad/gen/fix_courtyards.py grows them to pads + 0.25 mm.
Filed to hive-hwde (261028-153447-fc9e7b8567).

## 2026-10-28 [P4][erc] pulled symbols have every pin `unspecified`
ERC raised pin_to_pin on all 72 nets. kicad/gen/pin_types.py sets passive on
connectors/switches/LED/FET and the TCA9535/IRM-H638T types from their
datasheets. A power_in pin fed only through a resistor (IR_VCC) then needs a
PWR_FLAG.

## 2026-10-28 [P4][erc] a bidirectional pin on a PWR_FLAG'd GND net warns
Tying unused expander I/Os straight to GND gave pin_to_pin (Bidirectional vs
Power output). One 10 k to GND for all of them is both ERC-clean and safer.

## 2026-10-28 [P5][holes] MountingHole_*_Pad_Via fails JLC's hole-to-hole floor
The M2.5 _Pad_Via ring vias sit 0.425 mm apart against the 0.5 mm floor (36
DRC errors). Use the _Pad variant. Filed (261028-153448-a8f0eb515c).

## 2026-10-28 [P6][mating] an FPC socket mated by an on-board display tail fails check_mating
The rule assumes every FFC/FPC plug comes from off the board. J2's mate is
the LCD tail lying on the board, so its mouth faces inward: two waivers,
pending the person. Filed (261028-153447-0047840469).

## 2026-10-28 [P6][board_update] pad-net changes are refused even before routing
Adding R18/R19 and re-pointing U1 pins needed board_init again plus the
placement ops replayed (keep placement as one ops file for exactly this).
Filed (261028-153448-e1eda367ac).

## 2026-10-28 [P7][routing] a 0.5 mm FFC pin can be boxed in by its neighbours' fan-out
Freerouting left /I2C_SDA at J1 pin 13 trapped between SCL and INT (INT's via
closed the channel). Ripping INT and routing both with a small grid A*
(route/manual/) worked; the B.Cu tracks then split the GND pour into 5
islands, which a second GND pour on F.Cu rejoined.

## 2026-10-28 [P6][gate] the place/verify gate takes any non-empty `approved`
A waiver marked "PENDING ... not yet approved" counted as approved. Filed
(261028-153448-894784a6d8).

## 2026-10-28 [P6][mechanical] model a display tail's real outline, and line its END up with the socket's insertion stop
The first layout aligned the tail end with J2's contact pads (3 mm too far in)
and kept out only the 6.5 mm wide end, while the tail is ~20.5 mm wide for
its last 10 mm at the panel. A fresh reviewer reading the LCD drawing caught
both; the fix was two keepouts and J2 moved 3.1 mm.

## 2026-10-28 [P7][routing] Freerouting ignores copper-to-edge and hole-to-hole
It ran tracks 0.28 mm from the edge and put vias 0.43 mm hole-to-hole.
A 0.5 mm rule-area keepout around the edge plus a 0.2 mm Default clearance
(via centres >= 0.8 mm) made its output DRC-clean. Worth doing in route_auto.

## 2026-10-28 [P7][routing] route GND as a net before pouring on a dense 2-layer board
With pours placed first, Freerouting left GND to the pours and the 0.5 mm FFC
GND pins ended up isolated or starved. Routing with no zones gave 100%, then
pours went on top. A top pour needs pads solid for 0.5 mm sockets: a
.kicad_dru zone_connection rule had no effect in kicad-cli 10.0.6, the zone
property did (kicad/gen/zone_solid.py).

## 2026-10-28 [P7][routing] SES import can drop connections Freerouting reported done
fr_completion 1.0 but completion 0.93: J1.11 GND and two +3V3 links were
missing after import; they were routed by hand.

## 2026-10-29 [P6][mating] resolved: placement.mates onboard_flex replaces the J2 waivers
On hive-hwde f437494+ the tail corridor is declared once (same rectangles as
the keepouts) and check_mating/place pass with no waiver.
