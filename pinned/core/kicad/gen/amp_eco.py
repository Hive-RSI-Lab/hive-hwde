"""core: TPA3220 amp-area ECO (review-3 R3-AMP-1/2), on the routed board.

- PVDD HF pair per pin group (1 uF/100 V 1206 + 100 nF 0603) moves to B.Cu
  under the pin group: PVDD pad on the strip's plane vias, GND pad on the
  EP via field. TI SLASEN3B fig 53 / 11.1.3.
- Bootstrap caps move to the row ends on F.Cu, BST pad <= 2.6 mm from its
  pin: BSTxP/OUT1P and BST2M/OUT2M touch their own output pour; BST1M/OUT1M
  and BST2P/OUT2P reach their mid-row output column over B.Cu (miniroute).

Old copper of the moved caps (the BST nets, the OUT link routes, PVDD/GND
stubs of the old PVDD caps) is ripped first. Run under KiCad's python:
  python3.11 kicad/gen/amp_eco.py
"""
import math
import os
import sys
from pathlib import Path

import pcbnew

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("MR_LAYERS", "F.Cu,B.Cu")
import miniroute as mr  # noqa: E402

MM = 1e6
LIB = str(Path(mr.B) / "lib" / "aiee.pretty")
# (amp, net prefix, PVDD cap base, first BST cap: BST1M, BST1P, BST2M, BST2P)
AMPS = (("U15", "/amp_a/A_", 200, 222), ("U16", "/amp_b/B_", 300, 320))


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def mm(v):
    return (v.x / MM, v.y / MM)


b = pcbnew.LoadBoard(mr.PCB)


def pad(ref, num):
    return [p for p in b.FindFootprintByReference(ref).Pads() if p.GetNumber() == num][0]


def component(start_pads, net, stop_vias=False):
    """Tracks/vias reachable from start_pads on net, not walking through other pads."""
    items = [t for t in b.GetTracks() if t.GetNetname() == net]
    other = [p for f in b.GetFootprints() for p in f.Pads()
             if p.GetNetname() == net and p not in start_pads]
    seen, todo = set(), []
    for t in items:
        ends = [t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]
        if any(p.HitTest(e) for p in start_pads for e in ends):
            todo.append(t)
    while todo:
        t = todo.pop()
        if t.m_Uuid.AsString() in seen:
            continue
        seen.add(t.m_Uuid.AsString())
        if stop_vias and t.GetClass() == "PCB_VIA":
            continue
        ends = [t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]
        for e in ends:
            if any(p.HitTest(e) for p in other):
                continue          # stop at the amp pin
            for u in items:
                if u.m_Uuid.AsString() in seen:
                    continue
                if u.GetClass() == "PCB_VIA":
                    hit = (u.GetPosition() - e).EuclideanNorm() < u.GetWidth(pcbnew.F_Cu) / 2
                else:
                    hit = u.HitTest(e, int(0.02 * MM)) and u.GetLayer() in \
                        ([t.GetLayer()] if t.GetClass() != "PCB_VIA" else [u.GetLayer()])
                if hit:
                    todo.append(u)
    return [t for t in items if t.m_Uuid.AsString() in seen]


def swap_fp(ref, libname, layer_b):
    old = b.FindFootprintByReference(ref)
    new = pcbnew.FootprintLoad(LIB, libname)
    new.SetParent(b)
    new.SetReference(ref)
    new.SetValue(old.GetValue())
    new.SetFPID(pcbnew.LIB_ID("aiee", libname))
    for f in old.GetFields():
        if f.GetName() not in ("Reference", "Value", "Footprint"):
            nf = new.GetField(f.GetName()) if new.HasField(f.GetName()) else None
            if nf is None:
                nf = pcbnew.PCB_FIELD(new, new.GetNextFieldOrdinal(), f.GetName())
                new.Add(nf)
            nf.SetText(f.GetText())
            nf.SetVisible(False)
    new.SetPath(old.GetPath())
    new.SetSheetname(old.GetSheetname())
    new.SetSheetfile(old.GetSheetfile())
    new.SetAttributes(old.GetAttributes())
    nets = {p.GetNumber(): p.GetNet() for p in old.Pads()}
    for p in new.Pads():
        p.SetNet(nets[p.GetNumber()])
    new.SetPosition(old.GetPosition())
    b.Remove(old)
    b.Add(new)
    if layer_b and not new.IsFlipped():
        new.Flip(new.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    return new


def put(ref, x, y, pad1_dir, bottom=False, lib=None):
    """Place ref with centre (x, y); pad1_dir is where pad 1 points: 'N','S','E','W'."""
    fp = swap_fp(ref, lib, bottom) if lib else b.FindFootprintByReference(ref)
    if bottom and not fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    if not bottom and fp.IsFlipped():
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    fp.SetPosition(V(x, y))
    want = {"E": (1, 0), "W": (-1, 0), "N": (0, -1), "S": (0, 1)}[pad1_dir]
    for deg in (0, 90, 180, 270):
        fp.SetOrientationDegrees(deg)
        p1 = mm(pad(ref, "1").GetPosition())
        d = (p1[0] - x, p1[1] - y)
        if d[0] * want[0] + d[1] * want[1] > 0.3:
            break
    return fp


def track(net, pts, layer, w):
    ni = b.FindNet(net)
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x1, y1)); t.SetEnd(V(x2, y2))
        t.SetWidth(int(w * MM)); t.SetLayer(layer); t.SetNet(ni)
        b.Add(t)


def via(net, x, y, d=0.7, h=0.3):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(V(x, y)); v.SetWidth(int(d * MM)); v.SetDrill(int(h * MM))
    v.SetNet(b.FindNet(net))
    b.Add(v)


routes = []
for U, pre, base, kb in AMPS:
    u = b.FindFootprintByReference(U)
    cx, cy = mm(u.GetPosition())
    pin = {p.GetNumber(): mm(p.GetPosition()) for p in u.Pads()}
    c1m, c1p, c2m, c2p = (f"C{kb + k}" for k in range(4))
    bst = {c1m: ("BST1M", "OUT1M"), c1p: ("BST1P", "OUT1P"),
           c2m: ("BST2M", "OUT2M"), c2p: ("BST2P", "OUT2P")}
    pv = [f"C{base + k}" for k in (7, 8, 9, 10)]
    # 1. rip the old copper
    dead = []
    for c, (bn, on) in bst.items():
        dead += [t for t in b.GetTracks() if t.GetNetname() == pre + bn]
        k = component([pad(c, "2")], pre + on)
        print(c, on, len(k), round(sum(t.GetLength() for t in k if t.GetClass() != "PCB_VIA") / MM, 1))
        dead += k
    for c in pv:
        for n in ("1", "2"):
            dead += component([pad(c, n)], pad(c, n).GetNetname(), True)
    # each PVDD strip's plane-via count, kept after the ECO (check_current:
    # >= 1 via per 0.5 A at the layer change)
    strips = []
    for z in b.Zones():
        bb = z.GetBoundingBox()
        if not z.GetIsRuleArea() and z.GetNetname() == "PVDD" and z.IsOnLayer(pcbnew.F_Cu) \
                and z.GetAssignedPriority() == 5 and bb.Contains(V(cx - 2.4, cy - 8.7)) != bb.Contains(V(cx + 2.4, cy - 8.7)):
            box = (bb.GetLeft() / MM, bb.GetTop() / MM, bb.GetRight() / MM, bb.GetBottom() / MM)
            n = sum(1 for t in b.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == "PVDD"
                    and box[0] <= mm(t.GetPosition())[0] <= box[2] and box[1] <= mm(t.GetPosition())[1] <= box[3])
            strips.append((box, n))
    # the PVDD plane via that would sit under the new 1206 PVDD pads
    for gx in (cx - 2.19, cx + 2.21):
        for t in b.GetTracks():
            if t.GetClass() == "PCB_VIA" and t.GetNetname() == "PVDD":
                x, y = mm(t.GetPosition())
                if abs(x - gx) < 1.3 and 155.0 - 161.697 + cy < y < 157.0 - 161.697 + cy:
                    dead.append(t)
    gone = set()
    for t in dead:
        if t.m_Uuid.AsString() not in gone:
            gone.add(t.m_Uuid.AsString())
            b.Delete(t)
    print(U, "ripped", len(gone))
    top = cy - 3.797
    # 2. PVDD pairs on B.Cu (pad 1 = PVDD, north, toward the pins)
    yp = top - 1.9                       # 1206 PVDD pad centre
    # 1206 on the pin group; its 0603 partner beside it, outboard, courtyards
    # apart, the OUT link passes under the 1206 between its pads
    for c1, c2, s in ((f"C{base + 9}", f"C{base + 10}", -1), (f"C{base + 7}", f"C{base + 8}", 1)):
        x1 = cx + s * 2.4
        put(c1, x1, yp + 1.59, "N", bottom=True, lib="C1206")
        put(c2, x1 + s * 2.5, yp - 0.1, "E" if s < 0 else "W", bottom=True)
        p1, p2 = mm(pad(c1, "1").GetPosition()), mm(pad(c2, "1").GetPosition())
        # the strip's plane vias, re-laid as two staggered columns above the
        # 1206 pad: 8 where there were 5-6 (check_current: >= 1 per 0.5 A)
        for box, n0 in strips:
            if not box[0] <= x1 <= box[2]:
                continue
            for t in list(b.GetTracks()):
                if t.GetClass() == "PCB_VIA" and t.GetNetname() == "PVDD" and \
                        box[0] - 0.2 <= mm(t.GetPosition())[0] <= box[2] + 0.2 and \
                        box[1] <= mm(t.GetPosition())[1] <= box[3]:
                    b.Delete(t)
            xm = (box[0] + box[2]) / 2
            for k in range(4):
                for dx in (-0.435, 0.435):
                    via("PVDD", xm + dx, box[1] + 0.45 + 0.9 * k)
        track("PVDD", [p2, (p1[0], p2[1])], pcbnew.B_Cu, 1.5)
        # tie the PVDD pad to the strip's plane vias: one 1.5 mm B.Cu run up
        # through both via columns (a shorter one leaves a 0.016 mm same-net
        # gap to the next via on the Gerbers, which dfm can't tell from a short)
        for box, n0 in strips:
            if box[0] <= x1 <= box[2]:
                track("PVDD", [p1, (p1[0], box[1] + 0.45)], pcbnew.B_Cu, 1.5)
        c1fp = b.FindFootprintByReference(c1)

        c1fp.GetField("LCSC").SetText("C282823") if c1fp.HasField("LCSC") else None
    # 3. bootstrap caps at the row ends (F.Cu; pad 1 = BST)
    L, R = cx - 7.987, cx + 7.987
    put(c1p, L, top - 0.75, "S")             # BST1P / OUT1P
    put(c1m, L - 0.6, top + 1.7, "E")        # BST1M / OUT1M
    put(c2m, R, top - 0.75, "S")             # BST2M / OUT2M
    put(c2p, R + 0.6, top + 1.7, "W")        # BST2P / OUT2P
    for c, pn in ((c1p, "44"), (c2m, "23")):
        a = mm(pad(c, "1").GetPosition())
        track(pad(c, "1").GetNetname(), [a, (pin[pn][0], a[1])], pcbnew.F_Cu, 0.3)
        # the OUT pad sits inside its output pour's outline: the fill takes it
    for c, pn in ((c1m, "43"), (c2p, "24")):
        a = mm(pad(c, "1").GetPosition())
        px, py = pin[pn]
        track(pad(c, "1").GetNetname(), [(px, py + 0.6), (px, a[1]), a], pcbnew.F_Cu, 0.3)
    # OUT1M / OUT2P: B.Cu to the output column (routed after the refill)
    # OUT1M / OUT2P leave mid-row: a 0.45 mm via just under the pin (between
    # the row and the EP), then B.Cu to the cap's OUT pad
    for c, pn in ((c1m, "35"), (c2p, "32")):
        px, py = pin[pn]
        net = pre + ("OUT1M" if pn == "35" else "OUT2P")
        vy = py + 1.18
        for t in list(b.GetTracks()):       # the old link's via under the pin
            if t.GetNetname() == net and t.GetClass() == "PCB_VIA" and \
                    math.hypot(mm(t.GetPosition())[0] - px, mm(t.GetPosition())[1] - vy) < 1.0:
                b.Delete(t)
        track(net, [(px, py + 0.6), (px, vy)], pcbnew.F_Cu, 0.3)
        via(net, px, vy, 0.45, 0.2)
        routes.append((net, (px, vy), mm(pad(c, "2").GetPosition())))

# Copper in the way of the new OUT links, rerouted after them: U15's AVDD
# and OSCM B.Cu doglegs at the pin-23 end (AVDD's F.Cu leg also ran where
# C224 now sits), and a dead B_OUT1P strap of U16's (the pour feeds pins 39/40).


def rip_box(net, x0, y0, x1, y1):
    k = 0
    for t in list(b.GetTracks()):
        if t.GetNetname() != net:
            continue
        pts = [t.GetPosition()] if t.GetClass() == "PCB_VIA" else [t.GetStart(), t.GetEnd()]
        if all(x0 <= p.x / MM <= x1 and y0 <= p.y / MM <= y1 for p in pts):
            b.Delete(t)
            k += 1
    return k


print("rip", rip_box("/amp_a/A_AVDD", 51.4, 157.4, 57.5, 159.6),
      rip_box("/AMP_OSCM", 50.95, 157.0, 60.5, 160.5),
      rip_box("/amp_b/B_OUT1P", 73.0, 153.3, 75.5, 158.4),
      rip_box("/amp_a/A_OUT1P", 38.0, 158.5, 43.0, 163.5))   # the old C223 link's tail
later = [("/AMP_OSCM", (52.1, 160.7), (60.42, 160.39), (0, 1), (0,)),
         ("/amp_a/A_AVDD", (51.5, 159.5), (57.43, 157.6), (1,), (0,))]
# The F.Cu route keepouts over the OUT1M / OUT2P output columns forbid the
# column's own via: lift them for the route, then put them back split
# around the via (they only keep the router out of the pour).
cols = []
for z in list(b.Zones()):
    if not z.GetIsRuleArea() or not z.IsOnLayer(pcbnew.F_Cu):
        continue
    bb = z.GetBoundingBox()
    for net, a, t in routes:
        if bb.Contains(V(*t)):
            cols.append((net, bb.GetLeft() / MM, bb.GetTop() / MM, bb.GetRight() / MM, bb.GetBottom() / MM))
            b.Remove(z)
            break
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
b = pcbnew.LoadBoard(mr.PCB)
r = mr.Router(b)
START = [None]
_grids = r.grids


def grids(net, x0, y0, nx, ny, width):
    """No layer-change via within 0.9 mm of the start via."""
    blk, vblk = _grids(net, x0, y0, nx, ny, width)
    if START[0]:
        sx, sy = START[0]
        for j in range(ny):
            for i in range(nx):
                if math.hypot(x0 + i * mr.G - sx, y0 + j * mr.G - sy) < 0.9:
                    vblk[j * nx + i] = 1
    return blk, vblk


r.grids = grids
out = []
for net, a, t in routes:
    # the start via's own hole would box the start in
    r.holes = [(h, rr) for h, rr in r.holes if math.hypot(h.x / MM - a[0], h.y / MM - a[1]) > 0.05]
    START[0] = a
    n = r.route(net, a, t, (1,), (0,), margin=4.0, width=0.4)
    r.refresh()
    out.append((net, n))
START[0] = None
for net, a, t, la, lb in later:
    r.holes = [(h, rr) for h, rr in r.holes if math.hypot(h.x / MM - a[0], h.y / MM - a[1]) > 0.05]
    n = r.route(net, a, t, la, lb, margin=3.0, width=0.2)
    r.refresh()
    out.append((net, n))

# C7 (zone B PVDD 10 uF) reached PVDD through the old C307 spot: give its
# route its own In2 plane via, deep in the plane, clear of the U16 fan-out (In2
# is PVDD down to y 168.4 there; check_current erodes the plane 1.75 mm)
for x, y in ((90.99, 172.85), (93.1, 161.7)):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(V(x, y)); v.SetWidth(int(0.7 * MM)); v.SetDrill(int(0.3 * MM))
    v.SetNet(b.FindNet("PVDD")); b.Add(v)
r.refresh()
START[0] = (93.1, 161.7)
r.holes = [(h, rr) for h, rr in r.holes if math.hypot(h.x / MM - 90.99, h.y / MM - 172.85) > 0.05
           and math.hypot(h.x / MM - 93.1, h.y / MM - 161.7) > 0.05]
out.append(("PVDD C7", r.route("PVDD", (90.99, 172.85), (93.1, 161.7), (1,), (1,), margin=4.0, width=0.5)))
r.refresh()
# remnants of the old C310 stub (an island now)
for t in list(b.GetTracks()):
    if t.GetNetname() == "PVDD" and t.GetLayer() == pcbnew.B_Cu and t.GetClass() != "PCB_VIA":
        xs = [t.GetStart().x / MM, t.GetEnd().x / MM]
        ys = [t.GetStart().y / MM, t.GetEnd().y / MM]
        if all(72.2 <= x <= 73.3 for x in xs) and all(161.4 <= y <= 162.2 for y in ys):
            b.Delete(t)

# the moved caps sit tight against the amp's and each other's outlines: no
# silk of their own (the assembly drawing keeps the references)
for U, pre, base, kb in AMPS:
    for c in [f"C{base + k}" for k in (7, 8, 9, 10)] + [f"C{kb + k}" for k in range(4)]:
        fp = b.FindFootprintByReference(c)
        for g in list(fp.GraphicalItems()):
            if g.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                fp.Remove(g)
        fp.Reference().SetVisible(False)


def keepout(x0, y0, x1, y1):
    z = pcbnew.ZONE(b)
    z.SetIsRuleArea(True)
    z.SetLayer(pcbnew.F_Cu)
    z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True)
    z.SetDoNotAllowPads(False); z.SetDoNotAllowZoneFills(False); z.SetDoNotAllowFootprints(False)
    o = z.Outline(); o.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        o.Append(int(x * MM), int(y * MM))
    b.Add(z)


for net, x0, y0, x1, y1 in cols:
    vy = [mm(t.GetPosition())[1] for t in b.GetTracks() if t.GetClass() == "PCB_VIA"
          and t.GetNetname() == net and x0 <= mm(t.GetPosition())[0] <= x1 and y0 <= mm(t.GetPosition())[1] <= y1]
    edges = [y0] + [e for v in sorted(vy) for e in (v - 0.75, v + 0.75)] + [y1]
    for ya, yb in zip(edges[::2], edges[1::2]):
        if yb - ya > 0.2:
            keepout(x0, ya, x1, yb)
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(mr.PCB)
print(out)
