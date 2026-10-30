# core - learnings

## 2026-10-28 [P6][placement] hand anchors + nearest-free-spot satellites beat the annealer here
On a 278-part board on a loaded machine, place_anneal produced nothing in 22
min. About 50 hand-placed structural parts (`kicad/gen/anchors.py`) plus a
3 s nearest-free-spot placer for the rest passed the place gate. The placer
uses courtyard united with pads + 0.4 mm, and reserved rects over the power
pours.

## 2026-10-28 [P7][routing] reserve and keep out the high-current fan-in copper before Freerouting
Pours added after routing were split by the tracks Freerouting had laid
through them. Pours plus rule-area keepouts laid before routing held.

## 2026-10-28 [P7][routing] leave 2x more room than the place gate needs on dense mixed-signal boards
The ladder stalled at 88-98 % completion. Most of the leftovers were pads
boxed in by neighbouring tracks, in areas where the satellite spacing was
0.5 mm.

## 2026-10-28 [P4][review] a fresh schematic review caught two board-killers
- The PVDD P-FET would turn on as VBUS stepped from 5 to 28 V, because it
  had no gate-source cap.
- A 1 nF snubber on the class-D switching node dissipates 0.47 W.

Both are invisible to ERC.

## 2026-10-28 [P9][dfm] run dfm before calling copper done
Three sub-0.1 mm gaps on DRC-clean copper showed up only on the exported
Gerbers.
