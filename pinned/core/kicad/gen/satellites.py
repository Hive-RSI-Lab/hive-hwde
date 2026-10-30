"""core: place every non-anchor part next to what it connects to.

Run with KiCad's python (pcbnew). Reads the board (with the anchors from
anchors.py applied in memory), decoupling.json and the anchor table, and
writes anneal/satellites.ops.json: one "place" op per remaining part, at the
nearest free spot (courtyard + margin, inside the outline) to the pads it
connects to; decouplers go to their IC pin first. place_edit applies it.

  ~/.cache/hwde/bin/python3 kicad/gen/satellites.py
"""
import json
import math
import sys
from pathlib import Path

import pcbnew

HERE = Path(__file__).resolve()
BOARD = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
import anchors  # noqa: E402  (writes manual.ops.json as a side effect)

MARGIN = 0.4
RAILS = {"GND", "VBUS_IN", "VBUS", "PVDD", "+5V", "+5VA", "+3V3", "+3V3A",
         "+3V3E"}
MM = 1e6


def rect(fp, margin=MARGIN):
    """Courtyard bbox united with the pads' bbox + 0.25 mm (EasyEDA
    courtyards are often the body outline only; the place gate unites)."""
    fp.BuildCourtyardCaches()
    cy = fp.GetCourtyard(pcbnew.F_CrtYd)
    xs, ys = [], []
    if cy.OutlineCount():
        bb = cy.BBox()
        xs += [bb.GetLeft(), bb.GetRight()]
        ys += [bb.GetTop(), bb.GetBottom()]
    for pd in fp.Pads():
        bb = pd.GetBoundingBox()
        xs += [bb.GetLeft() - 250000, bb.GetRight() + 250000]
        ys += [bb.GetTop() - 250000, bb.GetBottom() + 250000]
    return (min(xs) / MM - margin, min(ys) / MM - margin,
            max(xs) / MM + margin, max(ys) / MM + margin)


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def main():
    b = pcbnew.LoadBoard(str(BOARD / "kicad" / "core.kicad_pcb"))
    eb = b.GetBoardEdgesBoundingBox()
    out = (eb.GetLeft() / MM + 0.6, eb.GetTop() / MM + 0.6,
           eb.GetRight() / MM - 0.6, eb.GetBottom() / MM - 0.6)
    fps = {fp.GetReference(): fp for fp in b.GetFootprints()}
    placed = {}
    for ref, v in anchors.A.items():
        fp = fps[ref]
        fp.SetPosition(pcbnew.VECTOR2I(int((anchors.X0 + v[0]) * MM),
                                       int((anchors.Y0 + v[1]) * MM)))
        fp.SetOrientationDegrees(v[2] if len(v) > 2 else 0)
        placed[ref] = rect(fp)
    for ref, fp in fps.items():
        if ref.startswith("H"):
            placed[ref] = rect(fp)
    # copper reserved for the amp output fan-in pours (power_zones.py)
    import power_zones as pz
    k = 0
    for du in (0.0, pz.DU):
        rects = [r for _, (_, rr) in pz.ZONE.items() for r in rr] + pz.ZONE_PVDD
        rects += [(9.8, 13.5, 13.3, 37.3), (3.0, 34.3, 20.5, 37.3)]
        for (u1, v1, u2, v2) in rects:
            k += 1
            placed[f"_keep{k}"] = (anchors.X0 + u1 + du, anchors.Y0 + v1,
                                   anchors.X0 + u2 + du, anchors.Y0 + v2)
    dec = json.loads((BOARD / "kicad" / "decoupling.json").read_text())
    dec_target = {d["cap"]: (d["ic"], d["pin"]) for d in dec["associations"]}

    def pad_xy(ref, num):
        for pd in fps[ref].Pads():
            if pd.GetNumber() == num:
                p = pd.GetPosition()
                return p.x / MM, p.y / MM
        return None

    netpads = {}
    for o, ofp in fps.items():
        for op in ofp.Pads():
            netpads.setdefault(op.GetNetname(), []).append((o, op))

    def target(ref):
        if ref in dec_target:
            ic, pin = dec_target[ref]
            if ic in placed:
                return pad_xy(ic, pin)
        pts, rail_pts, pts_src = [], [], []
        for pd in fps[ref].Pads():
            net = pd.GetNetname()
            if not net or net.startswith("unconnected"):
                continue
            for o, op in netpads.get(net, []):
                if o == ref or o not in placed:
                    continue
                p = op.GetPosition()
                (rail_pts if net in RAILS else pts).append((p.x / MM, p.y / MM))
                if net not in RAILS:
                    pts_src.append(((p.x / MM, p.y / MM), o))
        # filter/snubber parts sit at their inductor, not between it and
        # the far terminal
        lpts = [q for q, o in lpads] if (lpads := [(p_, o) for p_, o in pts_src if o.startswith("L")]) else []
        use = lpts or pts or rail_pts
        if not use:
            return None
        return (sum(p[0] for p in use) / len(use),
                sum(p[1] for p in use) / len(use))

    todo = [r for r in fps if r not in placed]
    ops = []
    # decouplers first, then the rest; a part whose neighbours are not
    # placed yet waits (up to three passes)
    order = sorted(todo, key=lambda r: (0 if r in dec_target else 1, r))
    queue = list(order)
    waits = {}
    while queue:
        ref = queue.pop(0)
        t = target(ref)
        if t is None and waits.get(ref, 0) < 3:
            waits[ref] = waits.get(ref, 0) + 1
            queue.append(ref)
            continue
        fp = fps[ref]
        if t is None:
            t = ((out[0] + out[2]) / 2, (out[1] + out[3]) / 2)
        spot = None
        for ring in range(0, 160):
            r = ring * 0.5
            n = max(1, int(2 * math.pi * r / 0.5))
            cands = []
            for k in range(n):
                a = 2 * math.pi * k / n
                cands.append((t[0] + r * math.cos(a), t[1] + r * math.sin(a)))
            for (x, y) in cands:
                for deg in (0, 90):
                    fp.SetOrientationDegrees(deg)
                    fp.SetPosition(pcbnew.VECTOR2I(int(x * MM), int(y * MM)))
                    rc = rect(fp)
                    if rc[0] < out[0] or rc[1] < out[1] or rc[2] > out[2] \
                            or rc[3] > out[3]:
                        continue
                    if any(overlap(rc, o) for o in placed.values()):
                        continue
                    spot = (x, y, deg, rc)
                    break
                if spot:
                    break
            if spot:
                break
        if not spot:
            print(f"no spot for {ref}", file=sys.stderr)
            continue
        x, y, deg, rc = spot
        placed[ref] = rc
        ops.append({"op": "place", "ref": ref, "x": round(x, 3),
                    "y": round(y, 3), "deg": deg})
    p = BOARD / "anneal" / "satellites.ops.json"
    p.write_text(json.dumps({"version": 1, "ops": ops}, indent=1) + "\n")
    print(json.dumps({"placed": len(ops), "todo": len(todo)}))


if __name__ == "__main__":
    main()
