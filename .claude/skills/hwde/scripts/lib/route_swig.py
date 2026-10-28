"""route_swig - BUNDLED-python SWIG worker for the S11 routing pipeline.

Runs inside KiCad's bundled python (the only interpreter with pcbnew); invoked
as `python route_swig.py job.json` by the venv drivers (route_edit.py,
route_auto.py, planes_gen.py). stdlib + lib/safelib (itself stdlib).
Every job runs under safelib.board_locks(job): an OS writer lock on the
"board" and "out" .kicad_pcb (<pcb>.lock), held from LoadBoard to Save;
a second worker on the same board waits, then refuses (LockBusy).

The RESULT is written to the file named by job["result"], never stdout: bulk
track/via removal sprays C-level "memory leak of type 'PCB_TRACK *'" lines and
wx spews image-handler noise, both of which tear stdout mid-line (prior-attempt
fact). Exit 0 = result file written with {"ok": true}; exit 3 = failure (result
file has {"ok": false, ...} when possible). Nothing is saved on failure.

job JSON: {"verb": ..., "result": out.json, ...} with verbs:

  export_dsn: {"board": pcb, "dsn": out.dsn, "layer_types": {"In1.Cu": "power"}}
      Specctra DSN export. wx asserts suppressed (export wedges headless on a
      courtyard-polygon wxASSERT otherwise). layer_types (optional) sets
      LT_POWER/LT_SIGNAL per layer BEFORE export (DSN "(type power)" drives
      Freerouting's plane rules; auto-detection does not exist) - transient,
      the board file is not saved by this verb.

  import_ses: {"board": pcb, "ses": in.ses, "out": out_pcb}
      Specctra SES import (adds copper only; footprints/zones untouched) then
      Save(out). Save may drop a default .kicad_pro next to `out` - drivers
      stage in a scratch dir and move only the .kicad_pcb back.

  apply_ops: {"board": pcb, "out": out_pcb, "ops": [...]}
      Track/via op list (route_edit.py's payload). Additions are checked for
      an existing identical item first (skip -> idempotent re-application);
      removals are by uuid and happen LAST, after every read and addition
      (bulk Remove() corrupts later pcbnew calls in the same process - the
      save is the final act). Op shapes:
        {"op": "add_track", "start": [x,y], "end": [x,y], "width": mm,
         "layer": "F.Cu", "net": "NAME"}
        {"op": "add_via", "at": [x,y], "size": mm, "drill": mm, "net": "NAME"}
        {"op": "remove", "uuid": "..."}        # track or via; absent = no-op
      All coordinates mm; mm->IU via pcbIUScale.mmToIU (FromMM truncates).

  add_zones: {"board": pcb, "out": out_pcb, "zones": [...],
              "layer_types": {"In1.Cu": "power"}}
      Zone creation for planes_gen.py. Zones are saved UNFILLED - the caller
      refills via `kicad-cli pcb drc --refill-zones --save-board` (ZONE_FILLER
      segfaults headless on this host). layer_types here ARE saved with the
      board. Zone shape:
        {"net": "GND", "layer": "In1.Cu", "poly": [[x,y], ...] |
         "rect": [x1,y1,x2,y2], "priority": 0, "min_island_mm2": null,
         "clearance": mm, "min_width": mm,
         "connect": "solid"|"thermal"|null}   # solid -> (connect_pads yes)
"""
import json
import sys

import safelib  # lib/ is sys.path[0]: the worker runs as lib/route_swig.py


def _wx_quiet():
    import wx

    app = wx.App()
    wx.DisableAsserts()
    try:
        app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)
    except Exception:
        pass
    return app  # keep a reference so the App outlives the call


def iu(mm):
    import pcbnew

    return int(pcbnew.pcbIUScale.mmToIU(float(mm)))


def mm(iu_val):
    import pcbnew

    return round(pcbnew.ToMM(int(iu_val)), 6)


def _layer_id(board, name):
    lid = board.GetLayerID(str(name))
    if lid < 0:
        raise ValueError("unknown layer: %s" % name)
    return lid


def _net(board, name):
    net = board.FindNet(str(name))
    if net is None:
        raise ValueError("net not on board: %s" % name)
    return net


def _set_layer_types(board, layer_types):
    import pcbnew

    kinds = {"power": pcbnew.LT_POWER, "signal": pcbnew.LT_SIGNAL,
             "mixed": pcbnew.LT_MIXED, "jumper": pcbnew.LT_JUMPER}
    for lname, kind in (layer_types or {}).items():
        if kind not in kinds:
            raise ValueError("layer_types[%s]: unknown type %r" % (lname, kind))
        board.SetLayerType(_layer_id(board, lname), kinds[kind])


def verb_export_dsn(job):
    import pcbnew

    _wx_quiet()
    board = pcbnew.LoadBoard(job["board"])
    _set_layer_types(board, job.get("layer_types"))
    ok = pcbnew.ExportSpecctraDSN(board, job["dsn"])
    if not ok:
        raise RuntimeError("ExportSpecctraDSN returned False")
    return {"dsn": job["dsn"]}


def verb_import_ses(job):
    import pcbnew

    _wx_quiet()
    board = pcbnew.LoadBoard(job["board"])
    before = len(board.GetTracks())
    ok = pcbnew.ImportSpecctraSES(board, job["ses"])
    if not ok:
        raise RuntimeError("ImportSpecctraSES returned False")
    after = len(board.GetTracks())
    if not board.Save(job["out"]):
        raise RuntimeError("board.Save failed: %s" % job["out"])
    return {"tracks_before": before, "tracks_after": after, "out": job["out"]}


def _track_key(t):
    import pcbnew

    s, e = t.GetStart(), t.GetEnd()
    a, b = (s.x, s.y), (e.x, e.y)
    if b < a:
        a, b = b, a
    return (a, b, t.GetWidth(), t.GetLayer(), t.GetNetname())


def verb_apply_ops(job):
    import pcbnew

    board = pcbnew.LoadBoard(job["board"])
    tol = iu(0.001)

    # Read pass: index existing items (before any mutation).
    tracks = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    track_keys = {_track_key(t) for t in tracks}
    via_index = {}
    for v in vias:
        p = v.GetPosition()
        via_index.setdefault((p.x, p.y, v.GetNetname()), v)
    by_uuid = {}
    for t in board.GetTracks():
        by_uuid[t.m_Uuid.AsString()] = t

    results = []
    to_remove = []
    for i, op in enumerate(job["ops"]):
        kind = op["op"]
        if kind == "add_track":
            start = pcbnew.VECTOR2I(iu(op["start"][0]), iu(op["start"][1]))
            end = pcbnew.VECTOR2I(iu(op["end"][0]), iu(op["end"][1]))
            lid = _layer_id(board, op["layer"])
            net = _net(board, op["net"])
            a, b = (start.x, start.y), (end.x, end.y)
            if b < a:
                a, b = b, a
            key = (a, b, iu(op["width"]), lid, op["net"])
            if key in track_keys:
                results.append({"op": kind, "status": "exists"})
                continue
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(start)
            t.SetEnd(end)
            t.SetWidth(iu(op["width"]))
            t.SetLayer(lid)
            t.SetNet(net)
            board.Add(t)
            track_keys.add(key)
            results.append({"op": kind, "status": "added",
                            "uuid": t.m_Uuid.AsString()})
        elif kind == "add_via":
            at = pcbnew.VECTOR2I(iu(op["at"][0]), iu(op["at"][1]))
            net = _net(board, op["net"])
            hit = None
            for (vx, vy, vn), v in via_index.items():
                if vn == op["net"] and abs(vx - at.x) <= tol \
                        and abs(vy - at.y) <= tol:
                    hit = v
                    break
            if hit is not None:
                results.append({"op": kind, "status": "exists"})
                continue
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(at)
            v.SetWidth(iu(op["size"]))
            v.SetDrill(iu(op["drill"]))
            v.SetViaType(pcbnew.VIATYPE_THROUGH)
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            v.SetNet(net)
            board.Add(v)
            via_index[(at.x, at.y, op["net"])] = v
            results.append({"op": kind, "status": "added",
                            "uuid": v.m_Uuid.AsString()})
        elif kind == "remove":
            item = by_uuid.get(op["uuid"])
            if item is None:
                results.append({"op": kind, "status": "absent"})
            else:
                to_remove.append(item)
                results.append({"op": kind, "status": "removed"})
        else:
            raise ValueError("ops[%d]: unknown op %r" % (i, kind))

    # Removal pass LAST; Save immediately after; no pcbnew reads beyond this.
    for item in to_remove:
        board.Remove(item)
    if not board.Save(job["out"]):
        raise RuntimeError("board.Save failed: %s" % job["out"])
    return {"results": results, "removed": len(to_remove)}


def verb_add_zones(job):
    import pcbnew

    board = pcbnew.LoadBoard(job["board"])
    _set_layer_types(board, job.get("layer_types"))
    made = []
    for i, z in enumerate(job["zones"]):
        zone = pcbnew.ZONE(board)
        zone.SetLayer(_layer_id(board, z["layer"]))
        zone.SetNet(_net(board, z["net"]))
        zone.SetAssignedPriority(int(z.get("priority", 0)))
        if z.get("clearance") is not None:
            zone.SetLocalClearance(iu(z["clearance"]))
        if z.get("min_width") is not None:
            zone.SetMinThickness(iu(z["min_width"]))
        if z.get("min_island_mm2") is not None:
            zone.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_AREA)
            zone.SetMinIslandArea(int(float(z["min_island_mm2"]) * 1e12))
        if z.get("connect") == "solid":
            # (connect_pads yes ...) - solid pad connection for fan-in
            # lobes (T6 P7B-2; ZONE_CONNECTION::FULL == 2 in KiCad 10)
            zone.SetPadConnection(
                getattr(pcbnew, "ZONE_CONNECTION_FULL", 2))
        pts = z.get("poly")
        if pts is None:
            x1, y1, x2, y2 = z["rect"]
            pts = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        outline = zone.Outline()
        outline.NewOutline()
        for x, y in pts:
            outline.Append(iu(x), iu(y))
        zone.SetIsFilled(False)
        board.Add(zone)
        made.append({"index": i, "net": z["net"], "layer": z["layer"],
                     "points": len(pts)})
    if not board.Save(job["out"]):
        raise RuntimeError("board.Save failed: %s" % job["out"])
    return {"zones": made, "out": job["out"]}


def verb_dedup_copper(job):
    """Remove EXACT-duplicate segments/vias (same geometry+net+layer+width).

    S14 finding: Freerouting's SES echoes pre-session guide-wire copper
    (route_critical trunks) back through ImportSpecctraSES, silently
    duplicating it - same-net exact stacks are invisible to DRC and gerber
    checks (run (a) shipped 45 echoed segments). Duplicates are never
    legitimate copper. Collect-first/remove-last/save-once per the
    LEARNINGS [swig] bulk-Remove rule.
    {"board": pcb, "out": out_pcb}
    """
    import pcbnew

    _wx_quiet()
    board = pcbnew.LoadBoard(job["board"])
    seen = set()
    dups = []
    for t in list(board.GetTracks()):
        cls = t.GetClass()
        if cls == "PCB_VIA":
            p = t.GetPosition()
            key = ("via", p.x, p.y, t.GetWidth(), t.GetDrillValue(),
                   t.GetNetname())
        elif cls == "PCB_TRACK":
            key = ("seg",) + _track_key(t)
        else:                      # PCB_ARC etc.: never emitted duplicated
            continue
        if key in seen:
            dups.append(t)
        else:
            seen.add(key)
    for t in dups:
        board.Remove(t)
    if dups and not board.Save(job["out"]):
        raise RuntimeError("board.Save failed: %s" % job["out"])
    return {"removed": len(dups), "changed": bool(dups),
            "out": job["out"] if dups else job["board"]}


VERBS = {
    "export_dsn": verb_export_dsn,
    "import_ses": verb_import_ses,
    "apply_ops": verb_apply_ops,
    "add_zones": verb_add_zones,
    "dedup_copper": verb_dedup_copper,
}


def main():
    job = json.loads(open(sys.argv[1], encoding="utf-8").read())
    result_path = job["result"]
    try:
        verb = job.get("verb")
        if verb not in VERBS:
            raise ValueError("unknown verb: %r" % verb)
        payload = {"ok": True, "verb": verb}
        with safelib.board_locks(job):  # LoadBoard -> Save under one hold
            payload.update(VERBS[verb](job))
        rc = 0
    except Exception as e:  # noqa: BLE001
        import traceback

        payload = {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                   "traceback": traceback.format_exc()[-2000:]}
        rc = 3
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return rc


if __name__ == "__main__":
    sys.exit(main())
