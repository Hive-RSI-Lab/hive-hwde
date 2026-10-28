"""layer_swig.py - SWIG worker for layer_views.py. BUNDLED python only.

Read-only: loads the board and writes, to the job's "result" file, the
geometry layer_views.py needs to label nets on a copper plot. Nothing is
saved, so no board lock is taken. Lengths are mm in board coordinates
(y down, the same frame kicad-cli plots a page in at scale 1); angles are
degrees, counter-clockwise as KiCad shows them.

  job: {board, result}
  result: {ok, copper:[name, ...] (front to back), edge:[x0, y0, x1, y1] | null,
           pads:[{layers, net, x, y, w, h, angle}],
           tracks:[{layer, net, x0, y0, x1, y1, width}],
           zones:[{layer, net, x, y}]}

A pad's layers are the copper layers it is on; a through-hole pad lists all
of them. An arc track is carried as its chord. A zone gives one point per
filled island big enough to hold its name, the island's bounding-box centre
when that point is inside the fill.
"""
import json
import sys


def mm(iu_val):
    import pcbnew

    return round(pcbnew.ToMM(int(iu_val)), 4)


def dump(job):
    import pcbnew

    board = pcbnew.LoadBoard(job["board"])
    copper = [board.GetLayerName(l) for l in board.GetEnabledLayers().CuStack()]
    cu_ids = {board.GetLayerID(n): n for n in copper}
    edge = None
    box = board.GetBoardEdgesBoundingBox()
    if box.GetWidth() > 0 and box.GetHeight() > 0:
        edge = [mm(box.GetX()), mm(box.GetY()), mm(box.GetRight()), mm(box.GetBottom())]
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            net = pad.GetNetname()
            if not net:
                continue
            layers = [n for i, n in cu_ids.items() if pad.IsOnLayer(i)]
            size = pad.GetSize(pad.GetPrincipalLayer())
            pos = pad.GetPosition()
            pads.append({"layers": layers, "net": net, "x": mm(pos.x), "y": mm(pos.y),
                         "w": mm(size.x), "h": mm(size.y),
                         "angle": pad.GetOrientation().AsDegrees()})
    tracks = []
    for t in board.GetTracks():
        if t.GetClass() not in ("PCB_TRACK", "PCB_ARC") or not t.GetNetname():
            continue
        s, e = t.GetStart(), t.GetEnd()
        tracks.append({"layer": board.GetLayerName(t.GetLayer()), "net": t.GetNetname(),
                       "x0": mm(s.x), "y0": mm(s.y), "x1": mm(e.x), "y1": mm(e.y),
                       "width": mm(t.GetWidth())})
    zones = []
    for z in board.Zones():
        if not z.GetNetname() or z.GetIsRuleArea():
            continue
        for lid, lname in cu_ids.items():
            if not z.IsOnLayer(lid):
                continue
            polys = z.GetFilledPolysList(lid)
            for i in range(polys.OutlineCount()):
                bb = polys.Outline(i).BBox()
                if min(bb.GetWidth(), bb.GetHeight()) < pcbnew.FromMM(3):
                    continue
                c = bb.Centre()
                if polys.Contains(pcbnew.VECTOR2I(c.x, c.y)):
                    zones.append({"layer": lname, "net": z.GetNetname(),
                                  "x": mm(c.x), "y": mm(c.y)})
    return {"copper": copper, "edge": edge, "pads": pads, "tracks": tracks,
            "zones": zones}


def main():
    job = json.loads(open(sys.argv[1], encoding="utf-8").read())
    try:
        payload = {"ok": True}
        payload.update(dump(job))
        rc = 0
    except Exception as e:  # noqa: BLE001
        import traceback

        payload = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                   "traceback": traceback.format_exc()[-2000:]}
        rc = 3
    with open(job["result"], "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return rc


if __name__ == "__main__":
    sys.exit(main())
