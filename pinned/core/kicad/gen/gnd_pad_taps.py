"""core: give each GND pad stranded in an isolated pour fragment a short
track to a new via down to the In1 GND plane. It is gnd_island_vias.py for
fragments too small to hold a via.

  ~/.cache/hwde/bin/python3 kicad/gen/gnd_pad_taps.py
"""
import json, math
from pathlib import Path
import pcbnew

B = Path(__file__).resolve().parents[2]
pcb = str(B / "kicad" / "core.kicad_pcb")
b = pcbnew.LoadBoard(pcb)
MM = 1e6
V = lambda x, y: pcbnew.VECTOR2I(int(x), int(y))
gnd = b.FindNet("GND")
tracks = list(b.GetTracks())
pads = [p for fp in b.GetFootprints() for p in fp.Pads()]
holes = [(t.GetPosition(), t.GetDrill() / 2) for t in tracks if t.GetClass() == "PCB_VIA"]
holes += [(p.GetPosition(), max(p.GetDrillSize().x, p.GetDrillSize().y) / 2) for p in pads if p.GetDrillSize().x > 0]
gt = [t.GetPosition() for t in tracks if t.GetClass() == "PCB_VIA" and t.GetNetname() == "GND"]
gt += [p.GetPosition() for p in pads if p.GetNetname() == "GND" and p.GetDrillSize().x > 0]
eb = b.GetBoardEdgesBoundingBox()


def free(pt, r, layer=None, skip_pad=None):
    """No other-net copper within r of pt (on `layer`, or on any layer)."""
    for t in tracks:
        if t.GetNetname() == "GND":
            continue
        if layer is not None and t.GetClass() != "PCB_VIA" and t.GetLayer() != layer:
            continue
        if t.HitTest(pt, int(r)):
            return False
    for p in pads:
        if p is skip_pad or p.GetNetname() == "GND":
            continue
        if layer is not None and not p.IsOnLayer(layer):
            continue
        if p.HitTest(pt, int(r)):
            return False
    return True


def stranded():
    out = []
    for z in b.Zones():
        if z.GetNetname() != "GND":
            continue
        for lay in z.GetLayerSet().Seq():
            if b.GetLayerName(lay) not in ("F.Cu", "B.Cu"):
                continue
            polys = z.GetFilledPolysList(lay)
            for i in range(polys.OutlineCount()):
                if any(polys.Contains(p, i) for p in gt):
                    continue
                for p in pads:
                    if p.GetNetname() == "GND" and p.IsOnLayer(lay) and polys.Contains(p.GetPosition(), i):
                        out.append((p, lay))
                        break
    return out


def connected_targets(lay):
    """Points of GND copper on `lay` that already reach the plane: GND vias
    and vertices of pour fragments that hold one."""
    pts = list(gt)
    for z in b.Zones():
        if z.GetNetname() != "GND" or not z.IsOnLayer(lay):
            continue
        polys = z.GetFilledPolysList(lay)
        for i in range(polys.OutlineCount()):
            if not any(polys.Contains(p, i) for p in gt):
                continue
            ol = polys.Outline(i)
            for k in range(ol.PointCount()):
                pts.append(ol.CPoint(k))
    return pts


def seg_clear(c, pt, lay, pad):
    n = max(4, int(((pt.x - c.x) ** 2 + (pt.y - c.y) ** 2) ** 0.5 / (0.1 * MM)))
    for s_ in range(1, n):
        q = V(c.x + (pt.x - c.x) * s_ / n, c.y + (pt.y - c.y) * s_ / n)
        if not free(q, 0.125 * MM + 0.13 * MM, layer=lay, skip_pad=pad):
            return False
    return True


added = []
S = stranded()
print("stranded", len(S))
for pad, lay in S:
    c = pad.GetPosition()
    done = False
    # first choice: a straight track to GND copper that already reaches the plane
    tg = sorted(connected_targets(lay), key=lambda p: (p.x - c.x) ** 2 + (p.y - c.y) ** 2)
    for pt in tg[:400]:
        d2 = ((pt.x - c.x) ** 2 + (pt.y - c.y) ** 2) ** 0.5
        if d2 > 4 * MM:
            break
        if d2 < 0.05 * MM:
            continue
        if seg_clear(c, pt, lay, pad):
            t = pcbnew.PCB_TRACK(b); t.SetStart(c); t.SetEnd(pt); t.SetWidth(int(0.25 * MM)); t.SetLayer(lay); t.SetNet(gnd); b.Add(t)
            tracks.append(t)
            added.append(f"{pad.GetParentFootprint().GetReference()}.{pad.GetNumber()}->pour")
            done = True
            break
    if done:
        continue
    for rr in [x * 0.25 for x in range(3, 17)]:
        for k in range(24):
            a = 2 * math.pi * k / 24
            pt = V(c.x + rr * MM * math.cos(a), c.y + rr * MM * math.sin(a))
            if not (eb.GetLeft() + 1 * MM < pt.x < eb.GetRight() - 1 * MM and eb.GetTop() + 1 * MM < pt.y < eb.GetBottom() - 1 * MM):
                continue
            if not free(pt, 0.3 * MM + 0.13 * MM):
                continue
            if any((h.x - pt.x) ** 2 + (h.y - pt.y) ** 2 < (0.15 * MM + r + 0.5 * MM) ** 2 for h, r in holes):
                continue
            # the 0.25 mm tap track from the pad centre to the via
            ok = True
            for s in range(1, 20):
                q = V(c.x + (pt.x - c.x) * s / 20, c.y + (pt.y - c.y) * s / 20)
                if not free(q, 0.125 * MM + 0.13 * MM, layer=lay, skip_pad=pad):
                    ok = False
                    break
            if not ok:
                continue
            v = pcbnew.PCB_VIA(b); v.SetPosition(pt); v.SetWidth(int(0.6 * MM)); v.SetDrill(int(0.3 * MM)); v.SetNet(gnd); b.Add(v)
            t = pcbnew.PCB_TRACK(b); t.SetStart(c); t.SetEnd(pt); t.SetWidth(int(0.25 * MM)); t.SetLayer(lay); t.SetNet(gnd); b.Add(t)
            tracks += [v, t]; holes.append((pt, 0.15 * MM)); gt.append(pt)
            added.append(f"{pad.GetParentFootprint().GetReference()}.{pad.GetNumber()}")
            done = True
            break
        if done:
            break
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(pcb)
print(json.dumps({"gnd_pad_taps": added}))
