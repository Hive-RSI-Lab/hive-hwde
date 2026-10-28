"""update_swig - BUNDLED-python SWIG worker for board_update.py (T8).

Runs inside KiCad's bundled python (the only interpreter with pcbnew); invoked
via routelib.run_worker(..., worker=UPDATE_WORKER) by the venv driver. stdlib
+ lib/safelib. Result travels by FILE (job["result"]), never stdout: bulk Remove()
sprays C-level noise that tears stdout mid-line (route_swig precedent).
Every job runs under safelib.board_locks(job): an OS writer lock on the
"board" and "out" .kicad_pcb (<pcb>.lock), held from LoadBoard to Save;
a second worker on the same board waits, then refuses (LockBusy).

One verb:

  apply_update: {"board": pcb, "out": out_pcb,
                 "field_updates": [{"ref": R, "value": V?, "fields": {n: v}}],
                 "adds": [{"ref": R, "value": V, "fpid": "Lib:Name",
                           "x": mm, "y": mm, "deg": d, "side": "front"|"back",
                           "fields": {n: v}, "pad_nets": {"1": "NET", ...}}],
                 "fp_paths": [dir, ...],
                 "remove_refs": [R, ...],
                 "remove_uuids": [uuid, ...]}      # tracks/vias

Application order is fixed by the LEARNINGS [swig] bulk-Remove rule: all reads
first (uuid index, silk-residue capture inside doomed footprints' bboxes),
then field updates and footprint adds, then EVERY removal last, then Save as
the final pcbnew act. Nothing is saved on any failure (exit 3, result file
carries the error) - the driver treats the staged copy as garbage.

Silk residue: board-frame PCB_TEXT on F.SilkS/B.SilkS whose anchor falls
inside a removed footprint's copper+courtyard bounding box annotates a part
that no longer exists - removed with the footprint and reported.
"""
import json
import sys

import safelib  # lib/ is sys.path[0]: the worker runs as lib/update_swig.py

import pcbnew

SILK_LAYERS = ("F.SilkS", "B.SilkS")


def iu(mm):
    return int(pcbnew.pcbIUScale.mmToIU(float(mm)))


def to_mm(v):
    return round(pcbnew.ToMM(int(v)), 6)


def side_of(fp):
    return "back" if fp.GetLayer() == pcbnew.B_Cu else "front"


# TOP_BOTTOM, for the reason spelled out in lib/place_swig.py: it is the only
# direction whose result does not depend on the part's starting angle, which
# is what makes an absolute {x,y,deg,side} op reproducible (U19).
_FLIP = getattr(pcbnew, "FLIP_DIRECTION_TOP_BOTTOM", True)


def set_side(fp, want):
    if side_of(fp) != want:
        fp.Flip(fp.GetPosition(), _FLIP)


def load_fp(fpid, fp_paths):
    lib, name = fpid.split(":", 1)
    for root in fp_paths:
        import os
        cand = os.path.join(root, lib + ".pretty")
        if os.path.isdir(cand):
            fp = pcbnew.FootprintLoad(cand, name)
            if fp is not None:
                return fp
    return None


def net_of(board, name, cache):
    if name in cache:
        return cache[name]
    net = board.FindNet(name)
    if net is None:
        net = pcbnew.NETINFO_ITEM(board, name)
        board.Add(net)
    cache[name] = net
    return net


def apply_fields(fp, value, fields):
    """Set Value / custom fields; new custom fields are hidden metadata
    (board_swig convention - they exist for parity, not board art)."""
    if value is not None:
        fp.SetValue(value)
    had = {f.GetName() for f in fp.GetFields()}
    for fname, fval in (fields or {}).items():
        fp.SetField(fname, fval)
    for f in fp.GetFields():
        if f.GetName() in (fields or {}) and f.GetName() not in had:
            f.SetVisible(False)


def verb_apply_update(job):
    board = pcbnew.LoadBoard(job["board"])

    # ---- read pass: indexes + silk capture (before ANY mutation) --------
    fps = {}
    for fp in board.GetFootprints():
        fps.setdefault(fp.GetReference(), fp)
    by_uuid = {t.m_Uuid.AsString(): t for t in board.GetTracks()}

    remove_refs = list(job.get("remove_refs") or [])
    # Capture the doomed footprint OBJECTS now: a swap_part_new_fp re-adds
    # the SAME ref before removals run, overwriting fps[ref] - removing by
    # ref at removal time would rip the replacement instead of the original.
    doomed_fps = []
    for ref in remove_refs:
        if ref not in fps:
            raise KeyError("remove_refs: footprint '%s' not on board" % ref)
        doomed_fps.append(fps[ref])
    silk_doomed = []
    if remove_refs:
        boxes = []
        for ref, fp in zip(remove_refs, doomed_fps):
            bb = fp.GetBoundingBox(False, False)  # copper+courtyard
            boxes.append((ref, bb))
        # Report the FILE-token layer name ("F.SilkS"), not GetLayerName's
        # canonical "F.Silkscreen" - the driver verifies against sexpr
        # tokens and a mismatched name makes that check vacuous.
        silk_ids = {board.GetLayerID(ln): ln for ln in SILK_LAYERS}
        for d in board.GetDrawings():
            if d.GetClass() != "PCB_TEXT":  # not dimensions/textboxes
                continue
            if d.GetLayer() not in silk_ids:
                continue
            pos = d.GetPosition()
            for ref, bb in boxes:
                if bb.Contains(pos):
                    silk_doomed.append((d, {
                        "text": d.GetText(), "near_ref": ref,
                        "layer": silk_ids[d.GetLayer()],
                        "x": to_mm(pos.x), "y": to_mm(pos.y)}))
                    break

    doomed_items = []
    absent_uuids = []
    for u in job.get("remove_uuids") or []:
        item = by_uuid.get(u)
        if item is None:
            absent_uuids.append(u)  # idempotent re-apply: already gone
        else:
            doomed_items.append(item)

    # ---- mutation pass 1: field updates ---------------------------------
    nets_cache = {}
    fields_updated = []
    for upd in job.get("field_updates") or []:
        fp = fps.get(upd["ref"])
        if fp is None:
            raise KeyError("field_updates: footprint '%s' not on board"
                           % upd["ref"])
        apply_fields(fp, upd.get("value"), upd.get("fields"))
        fields_updated.append(upd["ref"])

    # ---- mutation pass 2: footprint adds --------------------------------
    added = []
    replaced = set(remove_refs)
    for add in job.get("adds") or []:
        ref = add["ref"]
        if ref in fps and ref not in replaced:
            raise KeyError("adds: refdes '%s' already on board" % ref)
        fp = load_fp(add["fpid"], job.get("fp_paths") or [])
        if fp is None:
            raise KeyError("adds: footprint not found in fp_paths: %s"
                           % add["fpid"])
        lib, name = add["fpid"].split(":", 1)
        try:
            fp.SetFPID(pcbnew.LIB_ID(lib, name))
        except Exception:
            pass
        fp.SetReference(ref)
        fp.SetValue(add.get("value", ""))
        apply_fields(fp, None, add.get("fields"))
        board.Add(fp)
        # place_swig order: position, then side (flip about position), then
        # ABSOLUTE orientation (file convention - what the driver verifies).
        fp.SetPosition(pcbnew.VECTOR2I(iu(add["x"]), iu(add["y"])))
        if add.get("side"):
            set_side(fp, add["side"])
        fp.SetOrientationDegrees(float(add.get("deg") or 0.0))
        pad_nets = add.get("pad_nets") or {}
        netted = 0
        for pad in fp.Pads():
            want = pad_nets.get(pad.GetNumber())
            if want:
                pad.SetNet(net_of(board, want, nets_cache))
                netted += 1
        fps[ref] = fp
        pos = fp.GetPosition()
        added.append({"ref": ref, "x": to_mm(pos.x), "y": to_mm(pos.y),
                      "deg": round(fp.GetOrientationDegrees(), 4),
                      "side": side_of(fp), "pads_netted": netted})

    board.BuildListOfNets()

    # ---- removal pass LAST; Save immediately after ----------------------
    removed_texts = []
    for d, info in silk_doomed:
        board.Remove(d)
        removed_texts.append(info)
    for fp in doomed_fps:  # the ORIGINAL objects, never a same-ref add
        board.Remove(fp)
    for item in doomed_items:
        board.Remove(item)
    if not board.Save(job["out"]):
        raise RuntimeError("board.Save failed: %s" % job["out"])
    return {"fields_updated": fields_updated, "added": added,
            "removed_refs": remove_refs,
            "removed_items": len(doomed_items),
            "absent_uuids": absent_uuids,
            "removed_texts": removed_texts}


VERBS = {"apply_update": verb_apply_update}


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
