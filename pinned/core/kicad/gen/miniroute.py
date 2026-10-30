"""core: a small grid maze router for the last few unconnected pairs (KiCad python).

route_auto's ladder left about 40 connections open, and its KRT finish pass
either laid power nets below their width rule or broke clearance. This
script routes one connection at a time on F.Cu/B.Cu, on a 0.1 mm grid with
8-way moves and vias. The obstacles are other-net copper (tracks, vias,
pads, non-GND pours) inflated by width/2 + clearance. GND pours aren't
obstacles: they refill around new copper. A via cell also needs 0.5 mm
hole-to-hole spacing. The width is the net's rule width (rules_gen), at
least 0.2 mm.

  ~/.cache/hwde/bin/python3 kicad/gen/miniroute.py NET X1 Y1 X2 Y2 [--layers F,B]
  ~/.cache/hwde/bin/python3 kicad/gen/miniroute.py --from-drc reports/drc_routed.json

Coordinates are absolute mm. Every route found is added and the board is
refilled and saved.
"""
import heapq
import json
import math
import sys
from pathlib import Path

import pcbnew

B = Path(__file__).resolve().parents[2]
PCB = str(B / "kicad" / "core.kicad_pcb")
MM = 1e6
G = 0.1          # grid mm
CLR = 0.14       # clearance mm (rule 0.125 + margin)
import os
VIA_D, VIA_H = float(os.environ.get("MR_VIA_D", 0.6)), float(os.environ.get("MR_VIA_H", 0.3))
RULES = {p["net"]: p["min_width_mm"] for p in
         json.loads((B / "reports" / "rules_gen.json").read_text())["power"]}


def V(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


class Router:
    def __init__(self, board):
        self.b = board
        names = os.environ.get("MR_LAYERS", "F.Cu,B.Cu,In2.Cu").split(",")
        self.layers = [board.GetLayerID(n) for n in names]
        self.refresh()

    def refresh(self):
        self.tracks = list(self.b.GetTracks())
        self.pads = [p for fp in self.b.GetFootprints() for p in fp.Pads()]
        self.zones = [z for z in self.b.Zones() if not z.GetIsRuleArea()]
        self.holes = [(t.GetPosition(), t.GetDrill() / 2 / MM) for t in self.tracks
                      if t.GetClass() == "PCB_VIA"]
        self.holes += [(p.GetPosition(), max(p.GetDrillSize().x, p.GetDrillSize().y) / 2 / MM)
                       for p in self.pads if p.GetDrillSize().x > 0]

    def grids(self, net, x0, y0, nx, ny, width):
        """blocked[layer][i] for a track centre; viablk[i] for a via centre."""
        blk = [bytearray(nx * ny) for _ in self.layers]
        vblk = bytearray(nx * ny)
        rt = width / 2 + CLR
        rv = VIA_D / 2 + CLR

        def mark(item, layer_idx, r, arr_list):
            bb = item.GetBoundingBox()
            i0 = max(0, int((bb.GetLeft() / MM - r - x0) / G))
            i1 = min(nx - 1, int((bb.GetRight() / MM + r - x0) / G) + 1)
            j0 = max(0, int((bb.GetTop() / MM - r - y0) / G))
            j1 = min(ny - 1, int((bb.GetBottom() / MM + r - y0) / G) + 1)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    p = V(x0 + i * G, y0 + j * G)
                    for arr in arr_list:
                        if arr[j * nx + i]:
                            continue
                    if item.HitTest(p, int(r * MM)):
                        for arr in arr_list:
                            arr[j * nx + i] = 1

        for t in self.tracks:
            if t.GetNetname() == net:
                continue
            if t.GetClass() == "PCB_VIA":
                mark(t, None, rt, blk)
                mark(t, None, rv, [vblk])
            else:
                lay = t.GetLayer()
                if lay in self.layers:
                    mark(t, None, rt, [blk[self.layers.index(lay)]])
                mark(t, None, rv, [vblk])     # vias cross every layer
        for p in self.pads:
            if p.GetNetname() == net:
                continue
            on = [k for k, l in enumerate(self.layers) if p.IsOnLayer(l)]
            if on:
                mark(p, None, rt, [blk[k] for k in on])
            mark(p, None, rv, [vblk])
        for z in self.b.Zones():
            if not z.GetIsRuleArea() or not z.GetDoNotAllowTracks():
                continue
            for k, l in enumerate(self.layers):
                if not z.IsOnLayer(l):
                    continue
                bb = z.GetBoundingBox()
                for j in range(max(0, int((bb.GetTop() / MM - rt - y0) / G)), min(ny, int((bb.GetBottom() / MM + rt - y0) / G) + 1)):
                    for i in range(max(0, int((bb.GetLeft() / MM - rt - x0) / G)), min(nx, int((bb.GetRight() / MM + rt - x0) / G) + 1)):
                        blk[k][j * nx + i] = 1
                        if z.GetDoNotAllowVias():
                            vblk[j * nx + i] = 1
        for z in self.zones:
            zn = z.GetNetname()
            # plane/pour zones refill around new copper; only the solid
            # power fan-in pours (priority >= 5) are fixed obstacles
            if zn == net or zn == "GND" or z.GetAssignedPriority() < 5:
                continue
            for k, l in enumerate(self.layers):
                if not z.IsOnLayer(l):
                    continue
                bb = z.GetBoundingBox()
                i0 = max(0, int((bb.GetLeft() / MM - x0) / G) - 3)
                i1 = min(nx - 1, int((bb.GetRight() / MM - x0) / G) + 3)
                j0 = max(0, int((bb.GetTop() / MM - y0) / G) - 3)
                j1 = min(ny - 1, int((bb.GetBottom() / MM - y0) / G) + 3)
                for j in range(j0, j1 + 1):
                    for i in range(i0, i1 + 1):
                        p = V(x0 + i * G, y0 + j * G)
                        if z.HitTestFilledArea(l, p, int(rt * MM)):
                            blk[k][j * nx + i] = 1
        for (h, r) in self.holes:
            hx, hy = h.x / MM, h.y / MM
            rh = r + 0.27 + width / 2      # track to hole edge (board 0.25)
            for j in range(max(0, int((hy - rh - y0) / G)), min(ny, int((hy + rh - y0) / G) + 2)):
                for i in range(max(0, int((hx - rh - x0) / G)), min(nx, int((hx + rh - x0) / G) + 2)):
                    if (x0 + i * G - hx) ** 2 + (y0 + j * G - hy) ** 2 < rh * rh:
                        for arr in blk:
                            arr[j * nx + i] = 1
            rr = r + VIA_H / 2 + 0.5
            for j in range(max(0, int((hy - rr - y0) / G)), min(ny, int((hy + rr - y0) / G) + 2)):
                for i in range(max(0, int((hx - rr - x0) / G)), min(nx, int((hx + rr - x0) / G) + 2)):
                    if (x0 + i * G - hx) ** 2 + (y0 + j * G - hy) ** 2 < rr * rr:
                        vblk[j * nx + i] = 1
        eb = self.b.GetBoardEdgesBoundingBox()
        for j in range(ny):
            for i in range(nx):
                x, y = x0 + i * G, y0 + j * G
                if not (eb.GetLeft() / MM + 0.5 < x < eb.GetRight() / MM - 0.5
                        and eb.GetTop() / MM + 0.5 < y < eb.GetBottom() / MM - 0.5):
                    for arr in blk:
                        arr[j * nx + i] = 1
                    vblk[j * nx + i] = 1
        return blk, vblk

    def route(self, net, a, b, la=(0, 1), lb=(0, 1), margin=6.0, width=None):
        w = max(RULES.get(net, 0.2) + 0.005, 0.2) if width is None else width
        # grid aligned on the start point, so a fine-pitch start pad's exit
        # column lands exactly on grid nodes
        x0 = a[0] - math.ceil((a[0] - min(a[0], b[0]) + margin) / G) * G
        y0 = a[1] - math.ceil((a[1] - min(a[1], b[1]) + margin) / G) * G
        nx = int((abs(a[0] - b[0]) + 2 * margin) / G) + 1
        ny = int((abs(a[1] - b[1]) + 2 * margin) / G) + 1
        blk, vblk = self.grids(net, x0, y0, nx, ny, w)
        ia = (int(round((a[0] - x0) / G)), int(round((a[1] - y0) / G)))
        ib = (int(round((b[0] - x0) / G)), int(round((b[1] - y0) / G)))
        # the endpoints sit on their own pads: clear a small disc around them
        for (ci, cj), ls in ((ia, la), (ib, lb)):
            for dj in range(-2, 3):
                for di in range(-2, 3):
                    ii, jj = ci + di, cj + dj
                    if 0 <= ii < nx and 0 <= jj < ny:
                        for k in ls:
                            blk[k][jj * nx + ii] = 0
        start = [(0.0, ia[0], ia[1], k) for k in la]
        goal = {(ib[0], ib[1], k) for k in lb}
        h = lambda i, j: math.hypot(i - ib[0], j - ib[1])
        dist, prev = {}, {}
        pq = []
        for _, i, j, k in start:
            dist[(i, j, k)] = 0.0
            heapq.heappush(pq, (h(i, j), 0.0, i, j, k))
        moves = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1),
                 (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
        found = None
        n = 0
        while pq:
            f, g, i, j, k = heapq.heappop(pq)
            if (i, j, k) in goal:
                found = (i, j, k)
                break
            if g > dist.get((i, j, k), 1e18):
                continue
            n += 1
            if n > 3_000_000:
                break
            for di, dj, c in moves:
                ii, jj = i + di, j + dj
                if not (0 <= ii < nx and 0 <= jj < ny) or blk[k][jj * nx + ii]:
                    continue
                ng = g + c * (4 if k == 2 else 1)
                if ng < dist.get((ii, jj, k), 1e18):
                    dist[(ii, jj, k)] = ng
                    prev[(ii, jj, k)] = (i, j, k)
                    heapq.heappush(pq, (ng + h(ii, jj), ng, ii, jj, k))
            if not vblk[j * nx + i] and not any(arr[j * nx + i] for arr in blk):
              for k2 in range(len(self.layers)):
                if k2 == k:
                    continue
                if True:
                    ng = g + 25
                    if ng < dist.get((i, j, k2), 1e18):
                        dist[(i, j, k2)] = ng
                        prev[(i, j, k2)] = (i, j, k)
                        heapq.heappush(pq, (ng + h(i, j), ng, i, j, k2))
        if not found:
            return None
        path = [found]
        while path[-1] in prev:
            path.append(prev[path[-1]])
        path.reverse()
        return self.commit(net, path, x0, y0, w, a, b)

    def route_to_via(self, net, a, layer=0, margin=3.0, width=0.25):
        """Shortest F/B track from `a` to the nearest cell where a via fits,
        then drop a via there (stitches a stranded pad to the planes)."""
        x0 = a[0] - math.ceil(margin / G) * G
        y0 = a[1] - math.ceil(margin / G) * G
        nx = ny = 2 * int(math.ceil(margin / G)) + 1
        blk, vblk = self.grids(net, x0, y0, nx, ny, width)
        ia = (int(round((a[0] - x0) / G)), int(round((a[1] - y0) / G)))
        for dj in range(-2, 3):
            for di in range(-2, 3):
                blk[layer][(ia[1] + dj) * nx + ia[0] + di] = 0
        dist, prev = {ia: 0.0}, {}
        pq = [(0.0, ia)]
        moves = [(1, 0, 1), (-1, 0, 1), (0, 1, 1), (0, -1, 1),
                 (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
        while pq:
            g, (i, j) = heapq.heappop(pq)
            if g > dist[(i, j)]:
                continue
            if g >= 4 and not vblk[j * nx + i] and not any(arr[j * nx + i] for arr in blk):
                path = [(i, j)]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                path.reverse()
                full = [(pi, pj, layer) for pi, pj in path]
                q = full[-1]
                full.append((q[0], q[1], 1 - layer))   # forces a via at the end
                end = (x0 + q[0] * G, y0 + q[1] * G)
                return self.commit(net, full, x0, y0, width, a, end)
            for di, dj, c in moves:
                ii, jj = i + di, j + dj
                if 0 <= ii < nx and 0 <= jj < ny and not blk[layer][jj * nx + ii]:
                    ng = g + c
                    if ng < dist.get((ii, jj), 1e18):
                        dist[(ii, jj)] = ng
                        prev[(ii, jj)] = (i, j)
                        heapq.heappush(pq, (ng, (ii, jj)))
        return None

    def commit(self, net, path, x0, y0, w, a, b):
        netobj = self.b.FindNet(net)
        pts = [[x0 + i * G, y0 + j * G, k] for i, j, k in path]
        pts[0][0], pts[0][1] = a
        pts[-1][0], pts[-1][1] = b
        # split into same-layer runs; a layer change is a via
        runs, cur = [], [pts[0]]
        for p in pts[1:]:
            if p[2] != cur[-1][2]:
                runs.append(cur)
                cur = [p]
            else:
                cur.append(p)
        runs.append(cur)
        added = 0
        for ri, run in enumerate(runs):
            # keep only the corners of the run
            corners = [run[0]]
            for q0, q1, q2 in zip(run, run[1:], run[2:]):
                d1 = (round((q1[0] - q0[0]) / G), round((q1[1] - q0[1]) / G))
                d2 = (round((q2[0] - q1[0]) / G), round((q2[1] - q1[1]) / G))
                if d1 != d2:
                    corners.append(q1)
            if len(run) > 1:
                corners.append(run[-1])
            for c0, c1 in zip(corners, corners[1:]):
                if (c0[0], c0[1]) == (c1[0], c1[1]):
                    continue
                t = pcbnew.PCB_TRACK(self.b)
                t.SetStart(V(c0[0], c0[1])); t.SetEnd(V(c1[0], c1[1]))
                t.SetWidth(int(w * MM)); t.SetLayer(self.layers[run[0][2]])
                t.SetNet(netobj); self.b.Add(t); added += 1
            if ri < len(runs) - 1:
                q = run[-1]
                v = pcbnew.PCB_VIA(self.b)
                v.SetPosition(V(q[0], q[1])); v.SetWidth(int(VIA_D * MM))
                v.SetDrill(int(VIA_H * MM)); v.SetNet(netobj); self.b.Add(v); added += 1
        self.refresh()
        return added or 1


def item_point(board, it):
    """(x, y, layers) for a DRC item: pads and track ends; None for zones."""
    msg = it["msg"]
    x, y = it["pos"]
    if msg.startswith("Zone"):
        # DRC reports a zone by one of its outline corners: aim a little
        # inside the outline from there (F/B per the zone's layer)
        layer = (0,) if " on F.Cu" in msg else (1,) if " on B.Cu" in msg else None
        if layer is None:
            return None
        net = msg.split("[")[1].split("]")[0]
        for z in board.Zones():
            if z.GetNetname() != net or z.GetIsRuleArea():
                continue
            lid = board.GetLayerID("F.Cu" if layer == (0,) else "B.Cu")
            if not z.IsOnLayer(lid):
                continue
            for dx in (0.4, -0.4):
                for dy in (0.4, -0.4):
                    p = V(x + dx, y + dy)
                    if z.HitTestFilledArea(lid, p, 0):
                        return (x + dx, y + dy, layer)
        return None
    lay = (0, 1)
    if " on F.Cu" in msg and "F.Cu - B.Cu" not in msg:
        lay = (0,)
    elif " on B.Cu" in msg and "F.Cu - B.Cu" not in msg:
        lay = (1,)
    if msg.startswith("PTH pad") or msg.startswith("Via"):
        lay = (0, 1)
    return (x, y, lay)


def main(argv):
    board = pcbnew.LoadBoard(PCB)
    r = Router(board)
    done, failed = [], []
    if argv and argv[0] == "--from-drc":
        rep = json.loads(Path(argv[1]).read_text())
        only = set(argv[2].split(",")) if len(argv) > 2 else None
        for v in rep["failing"]:
            if v["check"] != "unconnected_items":
                continue
            net = v.get("net")
            if only and net not in only:
                continue
            pa = item_point(board, v["items"][0])
            pb = item_point(board, v["items"][1])
            if not pa or not pb:
                failed.append((net, "zone item"))
                continue
            import os
            n = r.route(net, pa[:2], pb[:2], pa[2], pb[2],
                        margin=float(os.environ.get("MR_MARGIN", 6)),
                        width=float(os.environ["MR_WIDTH"]) if "MR_WIDTH" in os.environ else None)
            (done if n else failed).append((net, n))
    else:
        net, x1, y1, x2, y2 = argv[0], *map(float, argv[1:5])
        n = r.route(net, (x1, y1), (x2, y2), (0,), (0,))
        (done if n else failed).append((net, n))
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    board.Save(PCB)
    print(json.dumps({"routed": done, "failed": failed}))


if __name__ == "__main__":
    main(sys.argv[1:])
