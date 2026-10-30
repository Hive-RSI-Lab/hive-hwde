"""place_swig - BUNDLED-python SWIG worker for place_edit.py (S9).

Runs inside KiCad's bundled python (the only interpreter with pcbnew); invoked
as `python place_swig.py job.json` by the venv driver. stdlib + lib/safelib.
Every job runs under safelib.board_locks(job): an OS writer lock on the
"board" and "out" .kicad_pcb (<pcb>.lock), held from LoadBoard to Save;
a second worker on the same board waits, then refuses (LockBusy).

job JSON: {"board": in_path, "out": out_path, "ops": [op, ...]}
ops are ABSOLUTE (idempotent by construction - re-applying is a no-op):
  {"op": "place",  "ref": R, "x": mm, "y": mm, ["deg": d], ["side": front|back]}
  {"op": "move",   "ref": R, "x": mm, "y": mm}
  {"op": "rotate", "ref": R, "deg": d}
  {"op": "flip",   "ref": R, "side": "front"|"back"}   # absolute side, not toggle
  {"op": "lock",   "ref": R, "locked": true|false}
Text ops (S14, closes V17 - silk labels/refdes moves were previously
unscriptable):
  {"op": "add_text",  "text": T, "x": mm, "y": mm, "layer": "F.SilkS",
   ["deg": d], ["size": mm], ["thickness": mm]}
     - board-frame graphic text. Idempotent: an existing text with the same
       string on the same layer within 0.01 mm is UPDATED, never duplicated.
  {"op": "remove_text", "text": T, "x": mm, "y": mm, "layer": "F.SilkS"}
     - deletes the board-frame graphic text matching (string, layer, position
       within 0.01 mm).  Idempotent: absent -> no-op, {"removed": 0}.  This is
       the only scripted way to RELOCATE a gr_text (remove + add_text), which
       a mismarked polarity/cathode legend needs.
  {"op": "move_text", "ref": R, "field": "reference"|"value", "x": mm,
   "y": mm, ["deg": d]}
     - repositions a footprint's Reference/Value field text (board frame;
       stored angle is ABSOLUTE per LEARNINGS [geometry]).
  {"op": "set_text", "ref": R, "field": "reference"|"value", ["hide": bool],
   ["size": mm], ["thickness": mm], ["layer": "F.SilkS"|"B.SilkS"|...]}
     - hides/shows, resizes (both axes) or re-layers a footprint's
       Reference/Value field in place; a B.* layer mirrors the text, an F.*
       layer un-mirrors it (same rule as add_text). The driver enforces the
       silk size/stroke floor. Absolute, so idempotent.
  {"op": "silk_clear", "ref": R, ["layer": "F.SilkS"], ["only_offboard": true]}
     - deletes a footprint's own GRAPHIC silk (lines/arcs/circles/polys) on
       that layer, never its Reference/Value text. `only_offboard` keeps the
       items fully inside the board outline. The only scripted way to fix
       footprint-INTERNAL silk on an already-placed board (a library edit
       cannot reach one without re-running board_init and losing placement).

The board is saved ONLY if every op applied; any failure exits 3 with a JSON
error and writes nothing (the driver treats the staged copy as garbage).
Save() may drop a default .kicad_pro next to `out` - the driver stages in a
scratch dir and moves only the .kicad_pcb back, so real project files are
never clobbered. mm->IU uses pcbIUScale.mmToIU (FromMM truncates: prior-
attempt fact, e.g. 32.3 mm -> 32299999 nm).
"""
import json
import sys

import safelib  # lib/ is sys.path[0]: the worker runs as lib/place_swig.py

import pcbnew


def iu(mm: float) -> int:
    return int(pcbnew.pcbIUScale.mmToIU(float(mm)))


def side_of(fp) -> str:
    return "back" if fp.GetLayer() == pcbnew.B_Cu else "front"


# TOP_BOTTOM, deliberately (U19). The two directions are NOT interchangeable
# for an absolute-op writer (measured on 10.0.3, usbbuck4 J1 at -90 deg):
#   LEFT_RIGHT mirrors in the BOARD frame and KEEPS the orientation, so the
#     resulting LOCAL frame is R(a).M_x.R(-a).L - it depends on the angle the
#     part happened to have when the op ran, and the same {x,y,deg,side} op
#     would then produce different geometry on different boards.
#   TOP_BOTTOM mirrors local y and negates the orientation, independent of the
#     starting angle; SetOrientationDegrees below then pins the angle, so the
#     op fully determines the result. placelib.Footprint.mirror models THIS.
# The name is FLIP_DIRECTION_TOP_BOTTOM (underscored) - an earlier
# un-underscored guess never resolved and always fell through to the bool
# fallback, which happens to mean the same thing here but silently.
_FLIP = getattr(pcbnew, "FLIP_DIRECTION_TOP_BOTTOM", True)


def set_side(fp, want: str) -> None:
    if side_of(fp) != want:
        fp.Flip(fp.GetPosition(), _FLIP)


def _layer_id(board, name: str) -> int:
    lid = board.GetLayerID(name)
    if lid < 0:
        raise KeyError(f"unknown layer '{name}'")
    return lid


def _match_texts(board, op: dict) -> list:
    """Board-frame PCB_TEXTs matching (layer, string, position +-0.01 mm)."""
    lid = _layer_id(board, op["layer"])
    tol = iu(0.01)
    return [d for d in board.GetDrawings()
            if isinstance(d, pcbnew.PCB_TEXT) and d.GetLayer() == lid
            and d.GetText() == op["text"]
            and abs(d.GetPosition().x - iu(op["x"])) <= tol
            and abs(d.GetPosition().y - iu(op["y"])) <= tol]


def apply_text_op(board, op: dict) -> dict:
    kind = op["op"]
    if kind == "remove_text":
        hits = _match_texts(board, op)
        for d in hits:            # idempotent: absent -> removed 0, no error
            # RemoveNative, NOT Remove: Remove() hands ownership to python and
            # once that proxy is collected `board.Drawings()` comes back as a
            # bare SwigPyObject, so every later GetDrawings() raises
            # "'SwigPyObject' object is not iterable" (KiCad 10.0.3, measured).
            board.RemoveNative(d)
        return {"text": op["text"], "layer": op["layer"],
                "x": float(op["x"]), "y": float(op["y"]),
                "removed": len(hits)}
    if kind == "add_text":
        lid = _layer_id(board, op["layer"])
        hits = _match_texts(board, op)
        txt = hits[0] if hits else None  # idempotent re-apply: update, no dupe
        if txt is None:
            txt = pcbnew.PCB_TEXT(board)
            board.Add(txt)
        txt.SetText(op["text"])
        txt.SetLayer(lid)
        txt.SetPosition(pcbnew.VECTOR2I(iu(op["x"]), iu(op["y"])))
        if op.get("deg") is not None:
            txt.SetTextAngleDegrees(float(op["deg"]))
        size = float(op.get("size", 1.0))
        txt.SetTextSize(pcbnew.VECTOR2I(iu(size), iu(size)))
        txt.SetTextThickness(iu(float(op.get("thickness", 0.15))))
        if op["layer"].startswith("B."):
            txt.SetMirrored(True)
        pos = txt.GetPosition()
        return {"text": op["text"], "layer": op["layer"],
                "x": round(pcbnew.ToMM(pos.x), 6),
                "y": round(pcbnew.ToMM(pos.y), 6),
                "deg": round(txt.GetTextAngleDegrees(), 4)}
    if kind == "move_text":
        fp = board.FindFootprintByReference(op["ref"])
        if fp is None:
            raise KeyError(f"footprint '{op['ref']}' not on board")
        item = fp.Reference() if op["field"] == "reference" else fp.Value()
        item.SetPosition(pcbnew.VECTOR2I(iu(op["x"]), iu(op["y"])))
        if op.get("deg") is not None:
            item.SetTextAngleDegrees(float(op["deg"]))
        pos = item.GetPosition()
        return {"ref": op["ref"], "field": op["field"],
                "x": round(pcbnew.ToMM(pos.x), 6),
                "y": round(pcbnew.ToMM(pos.y), 6),
                "deg": round(item.GetTextAngleDegrees(), 4)}
    if kind == "set_text":
        fp = board.FindFootprintByReference(op["ref"])
        if fp is None:
            raise KeyError(f"footprint '{op['ref']}' not on board")
        item = fp.Reference() if op["field"] == "reference" else fp.Value()
        if op.get("layer") is not None:
            item.SetLayer(_layer_id(board, op["layer"]))
            item.SetMirrored(op["layer"].startswith("B."))
        if op.get("size") is not None:
            size = float(op["size"])
            item.SetTextSize(pcbnew.VECTOR2I(iu(size), iu(size)))
        if op.get("thickness") is not None:
            item.SetTextThickness(iu(float(op["thickness"])))
        if op.get("hide") is not None:
            item.SetVisible(not op["hide"])
        sz = item.GetTextSize()
        return {"ref": op["ref"], "field": op["field"],
                "layer": pcbnew.LSET.Name(item.GetLayer()),
                "hidden": not item.IsVisible(),
                "size": round(pcbnew.ToMM(sz.y), 6),
                "thickness": round(pcbnew.ToMM(item.GetTextThickness()), 6)}
    raise KeyError(f"unknown text op '{kind}'")


EDGE_BOX = None   # board-edge bbox as plain ints, cached BEFORE any removal
# Every FOOTPRINT/PCB_SHAPE wrapper touched by a removal stays referenced for
# the life of this (short, single-job) process. Letting SWIG garbage-collect
# them corrupts the board: after one silk_clear returns, the NEXT
# board.FindFootprintByReference() comes back as a bare SwigPyObject with no
# FOOTPRINT methods (KiCad 10.0.5, measured - and it is the wrappers going out
# of scope, not `thisown`, which is False on both sides).
_KEEPALIVE = []


def _box(bb) -> tuple:
    return (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom())


def _inside(inner: tuple, outer: tuple) -> bool:
    return (inner[0] >= outer[0] and inner[1] >= outer[1]
            and inner[2] <= outer[2] and inner[3] <= outer[3])


def apply_silk_clear(board, op: dict) -> dict:
    """{"op": "silk_clear", "ref": R, ["layer": "F.SilkS"],
        ["only_offboard": true]}

    Delete a footprint's own GRAPHIC silk items (lines, arcs, circles, polys).
    Reference/Value text is never touched - move those with move_text.

    Why this exists: footprint-INTERNAL silk is the librarian's to fix in the
    LIBRARY, but a library edit does not reach a board that is already placed,
    and nothing else in this pipeline can edit footprint graphics on a board.
    That mattered on g0-sense P6: 12 of 13 residual silk warnings were body
    outlines (a 0603 wedged between two ICs, and a flush edge connector whose
    mouth-end silk hangs off the board), and `drc_routed` fails at
    errors+warnings = 0, so they had to go before P7 could pass. Re-running
    board_init to pick up a library edit would have destroyed the placement.

    `only_offboard` keeps every item whose bounding box lies inside the board
    outline and deletes the rest - the flush-connector case, where the silk
    that offends is the part hanging past the edge and the rest is worth
    keeping. Without it the footprint's graphic silk goes entirely.

    Idempotent: nothing left to delete is a no-op with {"removed": 0}."""
    ref = op["ref"]
    fp = board.FindFootprintByReference(ref)
    if fp is None:
        raise KeyError(f"footprint '{ref}' not on board")
    lname = op.get("layer", "F.SilkS")
    lid = _layer_id(board, lname)
    keep_inside = bool(op.get("only_offboard"))
    if keep_inside and EDGE_BOX is None:
        raise RuntimeError("only_offboard needs the board-edge box cached "
                           "before the first removal - see main()")
    victims = []
    for item in list(fp.GraphicalItems()):
        if item.GetLayer() != lid:
            continue
        if isinstance(item, pcbnew.PCB_TEXT):
            continue          # ${REFERENCE}-style fields are move_text's job
        if keep_inside and _inside(_box(item.GetBoundingBox()), EDGE_BOX):
            continue
        victims.append(item)
    _KEEPALIVE.append(fp)
    _KEEPALIVE.extend(victims)
    for item in victims:
        fp.Remove(item)
    return {"ref": ref, "layer": lname, "removed": len(victims),
            "only_offboard": keep_inside}


def apply_op(board, op: dict) -> dict:
    kind = op["op"]
    if kind in ("add_text", "remove_text", "move_text", "set_text"):
        return apply_text_op(board, op)
    if kind == "silk_clear":
        return apply_silk_clear(board, op)
    ref = op["ref"]
    fp = board.FindFootprintByReference(ref)
    if fp is None:
        raise KeyError(f"footprint '{ref}' not on board")
    if kind in ("place", "move"):
        fp.SetPosition(pcbnew.VECTOR2I(iu(op["x"]), iu(op["y"])))
    if kind == "place" and op.get("side") is not None:
        set_side(fp, op["side"])
    if kind in ("place", "rotate") and op.get("deg") is not None:
        fp.SetOrientationDegrees(float(op["deg"]))
    if kind == "flip":
        set_side(fp, op["side"])
    if kind == "lock":
        fp.SetLocked(bool(op["locked"]))
    pos = fp.GetPosition()
    return {"ref": ref,
            "x": round(pcbnew.ToMM(pos.x), 6), "y": round(pcbnew.ToMM(pos.y), 6),
            "deg": round(fp.GetOrientationDegrees(), 4),
            "side": side_of(fp), "locked": fp.IsLocked()}


def main() -> int:
    job = json.loads(open(sys.argv[1], encoding="utf-8").read())
    try:
        with safelib.board_locks(job):  # LoadBoard -> Save under one hold
            return _run(job)
    except safelib.LockBusy as e:  # another writer holds the board
        print(json.dumps({"ok": False, "index": None, "error": str(e)}))
        return 3


def _run(job) -> int:
    global EDGE_BOX
    board = pcbnew.LoadBoard(job["board"])
    # GetBoardEdgesBoundingBox() SEGFAULTS once a footprint has had an item
    # removed (KiCad 10.0.5, measured) - and returning its BOX2I into a later
    # op corrupts the next FindFootprintByReference into a bare SwigPyObject.
    # Take it once, up front, as plain ints.
    if any(o.get("op") == "silk_clear" and o.get("only_offboard")
           for o in job["ops"]):
        EDGE_BOX = _box(board.GetBoardEdgesBoundingBox())
    results = []
    for i, op in enumerate(job["ops"]):
        try:
            results.append(apply_op(board, op))
        except Exception as e:  # no save on ANY failure
            print(json.dumps({"ok": False, "index": i,
                              "error": f"{type(e).__name__}: {e}"}))
            return 3
    if not board.Save(job["out"]):
        print(json.dumps({"ok": False, "index": None,
                          "error": f"board.Save failed: {job['out']}"}))
        return 3
    print(json.dumps({"ok": True, "results": results}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
