"""core: VBUS_IN, VBUS and PVDD carry the real 5 A in every layer's pour.

check_current judges each zone on its own at the full budget: every via in
it must sit where the fill is at least the IPC-2152 width wide, and its
vias must all land in one such piece. On this board that took:

- In1/In2 at 1 oz (0.035 mm). At 0.5 oz the width for 5 A at dT 10 C is
  8.06 mm on In2, which no plane here, studded with taps, can give; at 1 oz
  it is 3.5 mm. JLC offers 1 oz inner copper on 4-layer boards.
- The GND stitch grid (about 4.3 mm pitch) cleared from the In2 power
  regions (STITCH_REGIONS, plus 1 mm): only pure stitches, GND vias no track
  or pad touches, and not the ones (KEEP) that are an F.Cu GND island's only
  tie to ground. In1 stays solid GND; F.Cu/B.Cu keep the stitches outside.
- In2 zone outlines (ZONES; refill after):
  - PVDD: the main plane and Q1's drain strip are one zone, and the strip
    runs east to x 71.6 (2.5 mm tall past x 63.6), so the drain current
    reaches the open plane past U15's signal-via field, which walled the
    3.5 mm strip in;
  - VBUS: 0.9 mm lower (+3V3 below gives that up), past Q3's GND tap, so its
    two halves join, plus the corner above C11 from y 171.2;
  - VBUS_IN: 1.1 mm wider beside J1's CC1 via (VBUS steps back to match),
    so D1's and R1's taps share one 3.5 mm-wide piece.
- Edits (EDITS, anneal/power_trunk_edits.ops.json):
  - the PVDD via at (52.36, 163.0) and its In2/B.Cu stubs go: they only
    doubled C207's and (55.56, 165.9)'s own plane vias, and boxed in by
    AMP_FAULT/AMP_OTW it had no room;
  - the VBUS via under Q1's drain strip (59.69, 168.02) moves 0.38 mm up so
    the strip passes it 3.5 mm wide;
  - C10.2's GND via moves left/up off VBUS's way into that corner, tied back
    to its pad by a short F.Cu track;
  - each PVDD finger between an amp's output pins (F.Cu, grown 2.8 mm away
    from the pins, see power_zones.py) gets three more plane vias, five in
    all: a finger feeds one bridge, 2.5 A (constraints.json);
  - review-2's F.Cu patches (POWER-1): R1's input patch grows 1 mm toward J1;
    the patch from R1's output to Q1's source becomes two zones, one per
    pad, and the strip below Q1 that necked to 1.15 mm goes with its three
    vias (overrides for the patches at R1's output and Q1's drain:
    constraints.json);
  - two of +5V's six layer changes get a second via (no room at the others);
  - R18's VBUS_IN tap via moves off the plane (an In2 rule area, RULE_AREA)
    and B.Cu ties it to J1's B4 VBUS pin;
  - C15's GND via goes: it split VBUS's only way into the corner above C11.

Writes anneal/power_trunk.ops.json (the stitch removals) and
anneal/power_trunk_edits.ops.json (the edits), both for route_edit, and
edits the board's zones and stackup in place. From the board workspace:
  python kicad/gen/power_trunk.py            (the hwde venv's python)
  hwde route_edit --pcb kicad/core.kicad_pcb --ops anneal/power_trunk.ops.json
  kicad-cli pcb drc --refill-zones --save-board kicad/core.kicad_pcb
  hwde route_edit --pcb kicad/core.kicad_pcb --ops anneal/power_trunk_edits.ops.json
  kicad-cli pcb drc --refill-zones --save-board kicad/core.kicad_pcb
(refill between the two: route_edit drops the finger vias that touch only
a zone whose fill the outline edit cleared).
"""
import json
import os
import re
import sys
import uuid
from pathlib import Path

B = Path(__file__).resolve().parents[2]
# hwde's scripts: $HWDE_SKILL/scripts (hwde env), else the published copy
for _d in (os.environ.get("HWDE_SKILL", ""), "/hive/tools/hwde", "/hive/tools/hwde/.claude/skills/hwde"):
    if _d and (Path(_d) / "scripts" / "lib" / "geom.py").exists():
        sys.path.insert(0, str(Path(_d) / "scripts"))
        break
from lib import geom  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

MARGIN = 1.0
# (layer, net, old outline, new outline), board mm; None drops the zone
R = lambda x1, y1, x2, y2: [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
ZONES = [
    ("In2.Cu", "PVDD", R(34.587, 120.697, 97.587, 168.197),
     [(34.587, 120.697), (97.587, 120.697), (97.587, 168.197), (71.587, 168.197),
      (71.587, 170.697), (63.587, 170.697), (63.587, 171.697), (52.087, 171.697),
      (52.087, 168.197), (34.587, 168.197)]),
    ("In2.Cu", "PVDD", R(52.087, 168.197, 62.087, 171.697), None),
    ("In2.Cu", "VBUS", R(47.887, 172.197, 71.587, 182.697),
     [(48.837, 172.197), (66.587, 172.197), (66.587, 171.197), (71.587, 171.197),
      (71.587, 183.597), (47.887, 183.597), (47.887, 180.497), (48.837, 180.497)]),
    ("In2.Cu", "VBUS_IN", R(34.587, 168.697, 47.387, 190.697),
     [(34.587, 168.697), (47.387, 168.697), (47.387, 172.697), (48.387, 172.697),
      (48.387, 179.997), (47.387, 179.997), (47.387, 190.697), (34.587, 190.697)]),
    ("In2.Cu", "+3V3", R(47.887, 183.197, 72.087, 191.197), R(47.887, 184.097, 72.087, 191.197)),
] + [
    # the PVDD fingers between each amp's output pins grow 2.8 mm away from
    # the pins (free GND pour there), room for five plane vias each
    ("F.Cu", "PVDD", R(x1, 153.697, x2, 156.897), R(x1, 150.897, x2, 156.897))
    for x1, x2 in ((42.587, 44.337), (47.587, 49.187), (75.487, 77.237), (80.487, 82.087))
] + [
    # review-2's F.Cu patch at R1's input pad: 1 mm wider, into the free
    # space beside J1's pins, so its eight vias sit in 3.5 mm of copper
    ("F.Cu", "VBUS_IN", R(42.9, 178.6, 46.2, 185.2), R(41.9, 178.6, 46.2, 185.2)),
    # review-2's F.Cu patch from R1's output to Q1's source, without the strip
    # below Q1 that necked to 1.15 mm (its three vias go, EDITS); the R1 side
    # and Q1's source keep their via arrays into the In2 VBUS plane
    ("F.Cu", "VBUS",
     [(50.0, 174.0), (58.0, 174.0), (58.0, 181.2), (52.5, 181.2), (52.5, 183.6), (50.0, 183.6)],
     # two zones, each judged alone: Q1's source pins and R1's output pad
     [[(53.7, 174.0), (58.0, 174.0), (58.0, 178.2), (53.7, 178.2)],
      [(49.0, 176.6), (53.3, 176.6), (53.3, 181.2), (52.5, 181.2), (52.5, 183.6), (49.0, 183.6)]]),
]
# outlines an earlier version of this script wrote, so a re-run reshapes them
PREV = {"VBUS_IN": [[(34.587, 168.697), (47.387, 168.697), (47.387, 172.697), (48.487, 172.697),
                     (48.487, 179.697), (47.387, 179.697), (47.387, 190.697), (34.587, 190.697)]],
        "PVDD": [[(34.587, 120.697), (97.587, 120.697), (97.587, 168.197), (71.587, 168.197),
                  (71.587, 170.697), (62.087, 170.697), (62.087, 171.697), (52.087, 171.697),
                  (52.087, 168.197), (34.587, 168.197)],
                 [(34.587, 120.697), (97.587, 120.697), (97.587, 168.197), (66.087, 168.197),
                  (66.087, 171.697), (52.087, 171.697), (52.087, 168.197), (34.587, 168.197)]],
        "VBUS": [[(48.987, 172.197), (66.587, 172.197), (66.587, 171.197), (71.587, 171.197),
                  (71.587, 183.597), (47.887, 183.597), (47.887, 180.197), (48.987, 180.197)],
                 [(47.887, 172.197), (66.587, 172.197), (66.587, 171.197), (71.587, 171.197),
                  (71.587, 183.597), (47.887, 183.597)],
                 [(47.887, 172.197), (66.587, 172.197), (66.587, 168.697), (71.587, 168.697),
                  (71.587, 183.597), (47.887, 183.597)]]}
INNER_CU = 0.035
# In2 keeps its VBUS_IN pour off R18's tap via, which B.Cu ties to J1's B4
# VBUS pin instead (EDITS): through the plane the tap sat in a piece that also
# reached J1's pins, and check_current's leaf rule (a tap is excused only when
# its side of the neck feeds bounded parts) could not excuse R18's microamps
RULE_AREA_UUID = "6f1e0a52-9d3c-4c4e-b0d1-6b9a1c2d3e4f"
RULE_AREA = f"""	(zone
		(layer "In2.Cu")
		(uuid "{RULE_AREA_UUID}")
		(hatch edge 0.5)
		(connect_pads
			(clearance 0)
		)
		(min_thickness 0.25)
		(keepout
			(tracks allowed)
			(vias allowed)
			(pads allowed)
			(copperpour not_allowed)
			(footprints allowed)
		)
		(placement
			(enabled no)
			(sheetname "")
		)
		(fill
			(thermal_gap 0.5)
			(thermal_bridge_width 0.5)
			(island_removal_mode 0)
		)
		(polygon
			(pts
				(xy 39.3 186.8) (xy 40.7 186.8) (xy 40.7 188.2) (xy 39.3 188.2)
			)
		)
	)
"""
# where the GND stitch grid was cleared: the In2 power regions as they were
# when this ran (constraints.json's planes, before this change reshaped them)
STITCH_REGIONS = [(34.587, 120.697, 97.587, 168.197), (52.087, 168.197, 62.087, 171.697),
                  (34.587, 168.697, 47.387, 190.697), (47.887, 172.197, 71.587, 182.697)]
# GND vias in those regions that stay: each is the only tie to ground of an
# F.Cu GND island (DRC found them unconnected without), U15/U16's pins 33/34
# tap among them (power_zones.py VIAS_GND)
KEEP = {
    "d52895d4-ce9e-44b1-b334-c85a13456f92", "76544fa2-e547-44ae-b168-8c3679042411",
    "fbb2cc3b-f725-40fc-81d8-89ec8347666b", "4f3ac910-5876-41e5-9cb4-29d7f81656fc",
    "a89dbfa1-5862-4f6d-a3ef-af21be945ccb", "c9dbeb05-07ae-45c7-9bf9-974c95b25094",
    "c9f454d7-c154-4c7c-a898-078b2decf61b", "4e0be45d-2385-462d-ba0c-d17b01e4a4c4",
    "c84c2dee-24f6-415c-b428-f7bde9d72df4", "f633a067-3886-4424-9935-e479ff8cd0ea",
    "544908d5-17f3-4285-86e0-93397c935b07", "cb52ee66-db31-4a99-b611-593990106301",
    "27681f9e-af0e-46aa-8c04-f4e6bca69884"}
EDITS = [{"op": "remove", "uuid": u} for u in (
    # the three VBUS vias in the strip below Q1 the F.Cu patch no longer has
    "1db623d1-9da4-4bfb-9a33-deddd41cb972", "bcebe21f-294f-4a42-aeca-98d8319028d7",
    "290a62ee-e1e7-44e7-b6b9-75f1e0913f1c",
    # R18's VBUS_IN tap via, re-laid 0.83 mm down-left, into the In2 plane's
    # wide piece beside J1
    "befa47c6-6f41-43bb-949f-80a4faf9ed93",
    # C15's GND via and stub: it split the VBUS plane's only way to the corner
    # above C10/C11, and every spot by C15.2 does the same; C15.2 stays on the
    # F.Cu GND pour, U5's GND via 3.5 mm away
    "095b8c8b-7c75-40bc-b0f9-958b9253f107", "ac9eba4d-2182-4f91-a3c2-6e0e9c4fe1fa",
    # PVDD via (52.36, 163.0) and the In2/B.Cu stubs that only led to it
    "97296ff5-30fa-4247-bc18-5c2566dd6a90", "21433b07-0320-4935-8a13-134b6cd90fd7",
    "2f65c1ed-0b3d-4bb6-87b8-69568005d07e", "7cabf0b5-4264-485f-b86d-25d553659b35",
    "cfe66bd7-b776-4661-a23a-cb208151c50d", "567b8335-ce13-4375-abf9-61a6141c3f47",
    "e2e3a31a-7d5c-4210-97ed-f18fd4e6d2cc", "f859b40a-e7b1-40ae-b934-a415c9e2b906",
    "eb4f93e0-b822-4303-b5c7-8ea8a537c4d3", "6b88a7dc-191f-4c51-b52b-b43a65c5c463",
    "54ebe348-24f1-4d91-a2ae-6cc26505edcf", "461d1899-94ac-4873-8946-1a0206908027",
    # VBUS via (59.69, 168.02) and its F.Cu stub, re-laid 0.42 mm up
    "f28a1979-65ab-4478-a1d4-c210436830e7", "e4e89ae0-9a5d-4e31-8eb9-cfbb93f52041",
    # C10.2's GND via, re-laid at (66.95, 174.0)
    "e5dc2aa1-806c-4e9a-8736-f7f5858a7e86")] + [
    {"op": "add_via", "at": [59.6891, 167.6374], "size": 0.7, "drill": 0.3, "net": "VBUS"},
    {"op": "add_track", "start": [59.8792, 167.8275], "end": [59.6891, 167.6374], "width": 0.25,
     "layer": "F.Cu", "net": "VBUS"},
    {"op": "add_track", "start": [59.6891, 168.0176], "end": [59.6891, 167.6374], "width": 0.25,
     "layer": "B.Cu", "net": "VBUS"},
    {"op": "add_via", "at": [66.95, 174.0], "size": 0.6, "drill": 0.3, "net": "GND"},
    {"op": "add_via", "at": [40.0, 187.501], "size": 0.6, "drill": 0.3, "net": "VBUS_IN"},
    {"op": "add_track", "start": [40.588, 186.913], "end": [40.0, 187.501], "width": 0.3,
     "layer": "F.Cu", "net": "VBUS_IN"},
] + [
    {"op": "add_track", "start": a, "end": b, "width": 0.3, "layer": "B.Cu", "net": "VBUS_IN"}
    for a, b in (([39.34, 180.83], [38.6, 180.83]), ([38.6, 180.83], [37.85, 181.58]),
                 ([37.85, 181.58], [37.85, 186.0]), ([37.85, 186.0], [39.351, 187.501]),
                 ([39.351, 187.501], [40.0, 187.501]))
] + [
    {"op": "add_track", "start": [66.95, 174.0], "end": [67.7, 174.0], "width": 0.4,
     "layer": "F.Cu", "net": "GND"},
] + [
    # three more plane vias in each PVDD finger (power_zones.py VIAS_PVDD)
    {"op": "add_via", "at": [x, y], "size": 0.6, "drill": 0.3, "net": "PVDD"}
    for x in (43.437, 48.387, 76.337, 81.287) for y in (151.597, 152.397, 153.197)
] + [
    # +5V layer changes get a second via where the board has room: U4's feed
    # going under the VBUS plane (53.89, 177.65) and the J3 branch (52.14,
    # 213.21); the other four sit in fan-outs with no spot clear of other nets
    {"op": "add_via", "at": [54.494, 178.253], "size": 0.6, "drill": 0.3, "net": "+5V"},
    {"op": "add_via", "at": [52.738, 212.606], "size": 0.6, "drill": 0.3, "net": "+5V"},
] + [
    {"op": "add_track", "start": a, "end": b, "width": 0.355, "layer": layer, "net": "+5V"}
    for layer in ("F.Cu", "B.Cu")
    for a, b in (([54.494, 178.253], [53.893, 177.652]), ([52.738, 212.606], [52.137, 213.207]))
]


def _pts(pts):
    return " ".join(f"(xy {x:g} {y:g})" for x, y in pts)


def edit_board(text: str) -> str:
    """Zone outlines and inner copper (module docstring); idempotent."""
    def zone(m):
        b = m.group(0)
        layer = re.search(r'\n\t\t\(layer "([^"]+)"\)', b)
        if not layer:
            return b
        poly = re.search(r"\(polygon\s*\(pts\s*((?:\(xy [^)]*\)\s*)+?)\s*\)", b)
        have = [(float(x), float(y)) for x, y in
                re.findall(r"\(xy ([-\d.]+) ([-\d.]+)\)", poly.group(1))]
        for lay, net, old, new in ZONES:
            if lay != layer.group(1) or f'(net "{net}")' not in b or \
                    have not in (old, new, *PREV.get(net, [])):
                continue
            if new is None:
                return ""
            polys = new if isinstance(new[0], list) else [new]
            if have in polys:
                return b
            out = []
            for k, pts in enumerate(polys):
                c = b[:poly.start(1)] + _pts(pts) + b[poly.end(1):]
                if k:  # a second zone from the same one: its own uuid
                    c = re.sub(r'\(uuid "([^"]+)"\)', lambda m: '(uuid "%s")' % uuid.uuid5(
                        uuid.NAMESPACE_URL, m.group(1) + str(k)), c, count=1)
                # the old fill no longer matches its outline: drop it for the refill
                out.append(re.sub(r"\n\t\t\(filled_polygon\n.*?\n\t\t\)", "", c, flags=re.S))
            return "".join(out)
        return b
    text = re.sub(r"\n\t\(zone\n.*?\n\t\)", zone, text, flags=re.S)
    if RULE_AREA_UUID not in text:
        text = text[:text.rstrip().rfind(")")] + RULE_AREA + ")\n"
    return re.sub(r'(\(layer "In[12]\.Cu"\n\t+\(type "copper"\)\n\t+\(thickness )[\d.]+',
                  lambda m: m.group(1) + f"{INNER_CU:g}", text)


def main():
    pcb = B / "kicad" / "core.kicad_pcb"
    regions = [box(*r).buffer(MARGIN) for r in STITCH_REGIONS]
    bg = geom.BoardGeom.from_file(pcb)
    tracks = bg.tracks_of("GND")
    pads = bg.pads_of("GND")
    ops = []
    for v in bg.vias_of("GND"):
        if v.uuid in KEEP or not any(r.contains(Point(v.at)) for r in regions):
            continue
        if any(t.poly.intersects(v.poly) for t in tracks):
            continue
        if any(p.poly.intersects(v.poly) for p in pads):
            continue
        ops.append({"op": "remove", "uuid": v.uuid})
    if ops:  # a second run finds them gone: keep the first run's list
        (B / "anneal" / "power_trunk.ops.json").write_text(
            json.dumps({"version": 1, "ops": ops}, indent=1) + "\n")
    print(len(ops), "stitch vias to remove")
    (B / "anneal" / "power_trunk_edits.ops.json").write_text(
        json.dumps({"version": 1, "ops": EDITS}, indent=1) + "\n")
    text = pcb.read_text()
    new = edit_board(text)
    if new != text:
        pcb.write_text(new)
    print("zones and stackup", "edited" if new != text else "already done")


if __name__ == "__main__":
    main()
