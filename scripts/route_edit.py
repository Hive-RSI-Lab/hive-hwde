"""route_edit - apply an atomic track/via op list (S11, SPEC P7/section 4).

The pipeline's ONLY writer for routing changes outside route_auto's SES import
(SPEC section 4: op lists applied atomically; agents never raw-edit the
board). Generators (stitch_vias.py, plane_repair.py, route_cleanup.py, fixer
agents) emit ops; this driver applies them via the SWIG bundled python worker
(lib/route_swig.py) - the same V4/V8 edit-path decision as place_edit (S9).

Contract:
  route_edit.py --pcb B.kicad_pcb --ops ops.json [--out-report r.json]
  ops.json: {"version": 1, "ops": [...]}  (op shapes: see lib/route_swig.py)
    add_track {start:[x,y], end:[x,y], width, layer, net}
    add_via   {at:[x,y], size, drill, net}          # through via
    remove    {uuid}                                 # track or via; absent=noop
    set_net   {uuid, net}                            # worker-level (renet)

Selector ops - the ECOs boards used to hand-script with pcbnew (U-ECO; the
hi-fi boards carried ~45 rip/renet/add scripts because route_edit only took
uuids). The driver resolves each against the parsed board (geom) into the
worker ops above, so they share the atomic apply + verify:
    rip      {SELECTOR}                    remove every track/via it selects
    renet    {to, SELECTOR}                move the selected tracks/vias to net `to`
                                           (copper that touches no pad: KiCad
                                           re-derives pad-tied copper's net
                                           from the pad on save, and verify
                                           refuses that)
    add_path {points:[[x,y],...], width, layer, net}   a polyline of add_tracks
    rename_net {from, to}                  board-wide: pads, tracks, vias and
                                           zones of `from` go to `to` (created
                                           when absent) - the routed rename
                                           board_update refuses
  SELECTOR (all given keys must match; at least one of net, pads, at,
  region, uuid_prefix):
    net: NAME | [NAME...]       only these nets
    layer: L                    tracks on L only (vias are never selected)
    kind: track|via|any         default any
    region: [x0,y0,x1,y1]       an end (via: its centre) inside the box
    at: [x,y], radius (0.02)    an end (via: its centre) within radius;
      touch: "any"              ... or any point of the track's centreline
    pads: ["REF" | "REF.PAD"], radius (0)   an end within radius of one of
                                those pads' copper, on the pad's own net
    uuid_prefix: S | [S...]     the item's uuid starts with one of these
  A selector that matches nothing is "absent" (a no-op, like remove), so a
  re-run is idempotent. Items without a uuid (synthetic boards) can't be
  selected.

  --op '{"op": "rip", "net": "VBUS"}' (repeatable) takes ops inline instead
  of --ops FILE; --refill refills zones on the staged copy (with the board's
  .kicad_pro/.kicad_dru beside it) before the swap.

Atomic + rollback (place_edit pattern): ops are validated (schema + nets/
layers against the board); the board is copied to a scratch dir INSIDE the
board's directory; the worker edits and saves the copy; the driver re-parses
the copy (geom) and verifies every add landed (endpoints/center 1e-3 mm,
width 1e-3) and every removed uuid is gone from the file text; only then does
os.replace() swap the .kicad_pcb in (same volume -> atomic; sibling project
files never touched). Any failure leaves the original board byte-identical.
Re-applying the same op list is idempotent: identical existing items are
skipped ("exists"), absent removal uuids are no-ops ("absent").

Zone fills are NOT refreshed here: adding/removing copper stales any pour it
crosses. Callers that touch plane layers must refill afterwards
(kicad-cli pcb drc --refill-zones --save-board; kc.run_drc(refill=True)).
exit 0 applied+verified / 2 error (rolled back).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import geom  # noqa: E402
import routelib  # noqa: E402
from checklib import CheckError  # noqa: E402

OP_FIELDS = {  # op -> (required, optional)
    "add_track": ({"start", "end", "width", "layer", "net"}, set()),
    "add_via": ({"at", "size", "drill", "net"}, set()),
    "remove": ({"uuid"}, set()),
    "set_net": ({"uuid", "net"}, set()),
}
SELECT_KEYS = {"net", "layer", "kind", "region", "at", "radius", "touch",
               "pads", "uuid_prefix"}
SCOPE_KEYS = {"net", "pads", "at", "region", "uuid_prefix"}
ECO_FIELDS = {  # selector ops, resolved by the driver into OP_FIELDS ops
    "rip": (set(), SELECT_KEYS),
    "renet": ({"to"}, SELECT_KEYS),
    "add_path": ({"points", "width", "layer", "net"}, set()),
    "rename_net": ({"from", "to"}, set()),
}
AT_TOL = 0.02    # mm: default `at` radius (the hand scripts' 20000 nm)
POS_TOL = 1e-3   # mm


def _num(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)


def _pt(v) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) == 2
            and _num(v[0]) and _num(v[1]))


def validate_ops(doc: dict) -> list[dict]:
    if not isinstance(doc, dict) or doc.get("version") != 1:
        raise CheckError("ops file must be {'version': 1, 'ops': [...]}")
    ops = doc.get("ops")
    if not isinstance(ops, list) or not ops:
        raise CheckError("ops list is empty")
    for i, op in enumerate(ops):
        kind = op.get("op") if isinstance(op, dict) else None
        if kind not in OP_FIELDS and kind not in ECO_FIELDS:
            raise CheckError(f"ops[{i}]: unknown op '{kind}'")
        req, opt = OP_FIELDS.get(kind) or ECO_FIELDS[kind]
        missing = req - op.keys()
        if missing:
            raise CheckError(f"ops[{i}] ({kind}): missing {sorted(missing)}")
        unknown = op.keys() - req - opt - {"op"}
        if unknown:
            raise CheckError(f"ops[{i}] ({kind}): unknown keys {sorted(unknown)}")
        for k in ("start", "end", "at"):
            if k in op and not _pt(op[k]):
                raise CheckError(f"ops[{i}]: {k} must be [x, y] mm")
        for k in ("width", "size", "drill"):
            if k in op and not (_num(op[k]) and op[k] > 0):
                raise CheckError(f"ops[{i}]: {k} must be a positive number")
        if kind == "add_via" and op["drill"] >= op["size"]:
            raise CheckError(f"ops[{i}]: drill must be < size")
        if "uuid" in op and not (isinstance(op["uuid"], str) and op["uuid"]):
            raise CheckError(f"ops[{i}]: uuid must be a non-empty string")
        for k in ("layer", "to", "from") + (("net",) if kind not in (
                "rip", "renet") else ()):
            if k in op and not (isinstance(op[k], str) and op[k]):
                raise CheckError(f"ops[{i}]: {k} must be a non-empty string")
        if kind in ("rip", "renet"):
            _validate_selector(i, op)
        if kind == "add_path":
            pts = op["points"]
            if not (isinstance(pts, list) and len(pts) >= 2
                    and all(_pt(p) for p in pts)):
                raise CheckError(f"ops[{i}]: points must be 2+ [x, y] mm")
        if kind == "rename_net" and op["from"] == op["to"]:
            raise CheckError(f"ops[{i}]: from and to are the same net")
    return ops


def _strs(v) -> list[str] | None:
    """A name or a list of names -> list, None when malformed."""
    v = [v] if isinstance(v, str) else v
    if isinstance(v, list) and v and all(isinstance(x, str) and x for x in v):
        return v
    return None


def _validate_selector(i: int, op: dict) -> None:
    if not op.keys() & SCOPE_KEYS:
        raise CheckError(f"ops[{i}] ({op['op']}): needs at least one of "
                         f"{sorted(SCOPE_KEYS)} (a whole-board rip is "
                         "never what an ECO means)")
    for k in ("net", "pads", "uuid_prefix"):
        if k in op and _strs(op[k]) is None:
            raise CheckError(f"ops[{i}]: {k} must be a name or a list of "
                             "names")
    if op.get("kind", "any") not in ("track", "via", "any"):
        raise CheckError(f"ops[{i}]: kind must be track|via|any")
    if op.get("touch", "end") not in ("end", "any"):
        raise CheckError(f"ops[{i}]: touch must be end|any")
    if "touch" in op and "at" not in op:
        raise CheckError(f"ops[{i}]: touch goes with at")
    if "at" in op and not _pt(op["at"]):
        raise CheckError(f"ops[{i}]: at must be [x, y] mm")
    r = op.get("region")
    if r is not None and not (isinstance(r, list) and len(r) == 4
                              and all(_num(x) for x in r)
                              and r[0] <= r[2] and r[1] <= r[3]):
        raise CheckError(f"ops[{i}]: region must be [x0, y0, x1, y1] with "
                         "x0 <= x1, y0 <= y1")
    if "radius" in op and not (_num(op["radius"]) and op["radius"] >= 0):
        raise CheckError(f"ops[{i}]: radius must be >= 0")
    if "radius" in op and not op.keys() & {"at", "pads"}:
        raise CheckError(f"ops[{i}]: radius goes with at or pads")


def select_items(bg, op: dict) -> list:
    """Tracks/vias (geom items with a uuid) an ECO selector picks, in board
    order. Pure: the venv-side twin of the hi-fi boards' rip_* scripts."""
    from shapely.geometry import Point

    nets = set(_strs(op["net"])) if "net" in op else None
    layer = op.get("layer")
    kind = "track" if layer else op.get("kind", "any")
    prefixes = tuple(_strs(op["uuid_prefix"])) if "uuid_prefix" in op \
        else None
    region = op.get("region")
    at = Point(op["at"]) if "at" in op else None
    rad = op.get("radius", AT_TOL if at is not None else 0.0)
    pads = None
    if "pads" in op:
        want = [p.split(".", 1) for p in _strs(op["pads"])]
        pads = [p for p in bg.pads_of()
                if p.net and any(p.ref == w[0]
                                 and (len(w) == 1 or p.number == w[1])
                                 for w in want)]

    def ends(it):
        if isinstance(it, geom.Via):
            return [it.at]
        c = list(it.shape.coords)
        return [c[0], c[-1]]

    items = []
    if kind in ("track", "any"):
        items += bg.tracks_of(layer=layer)
    if kind in ("via", "any"):
        items += bg.vias_of()
    out = []
    for it in items:
        if it.uuid is None or (nets is not None and it.net not in nets):
            continue
        if prefixes is not None and not it.uuid.startswith(prefixes):
            continue
        pts = ends(it)
        if region is not None and not any(
                region[0] <= x <= region[2] and region[1] <= y <= region[3]
                for x, y in pts):
            continue
        if at is not None:
            near = any(at.distance(Point(q)) <= rad + 1e-9 for q in pts)
            if not near and op.get("touch") == "any" \
                    and isinstance(it, geom.Track):
                near = it.shape.distance(at) <= rad + 1e-9
            if not near:
                continue
        if pads is not None and not any(
                p.net == it.net and p.poly.distance(Point(q)) <= rad + 1e-9
                for p in pads for q in pts):
            continue
        out.append(it)
    return out


def resolve_ops(bg, ops: list[dict]) -> tuple[list[dict], list[list[int]]]:
    """ECO ops -> worker ops. Returns (worker ops, for each input op the
    indices of the worker ops it became). rename_net passes through (the
    worker does it board-wide; there is nothing to pre-select)."""
    flat: list[dict] = []
    spans: list[list[int]] = []
    for op in ops:
        kind = op["op"]
        start = len(flat)
        if kind == "rip":
            flat += [{"op": "remove", "uuid": it.uuid}
                     for it in select_items(bg, op)]
        elif kind == "renet":
            flat += [{"op": "set_net", "uuid": it.uuid, "net": op["to"]}
                     for it in select_items(bg, op) if it.net != op["to"]]
        elif kind == "add_path":
            pts = op["points"]
            flat += [{"op": "add_track", "start": list(a), "end": list(b),
                      "width": op["width"], "layer": op["layer"],
                      "net": op["net"]}
                     for a, b in zip(pts, pts[1:]) if not _close(a, b)]
        else:
            flat.append(dict(op))
        spans.append(list(range(start, len(flat))))
    return flat, spans


def _close(a: tuple[float, float], b) -> bool:
    return abs(a[0] - b[0]) <= POS_TOL and abs(a[1] - b[1]) <= POS_TOL


def _verify(staged: Path, ops: list[dict]) -> list[str]:
    bg = geom.BoardGeom.from_file(staged)
    text = staged.read_text(encoding="utf-8")
    problems = []
    for i, op in enumerate(ops):
        kind = op["op"]
        if kind == "add_track":
            want_s, want_e = tuple(op["start"]), tuple(op["end"])
            found = False
            for t in bg.tracks_of(net=op["net"], layer=op["layer"]):
                if abs(t.width - op["width"]) > POS_TOL:
                    continue
                c = list(t.shape.coords)
                s, e = c[0], c[-1]
                if (_close(want_s, s) and _close(want_e, e)) or \
                        (_close(want_s, e) and _close(want_e, s)):
                    found = True
                    break
            if not found:
                problems.append(f"ops[{i}]: track {want_s}->{want_e} "
                                f"({op['net']}, {op['layer']}) not in saved board")
        elif kind == "add_via":
            want = tuple(op["at"])
            found = any(
                _close(want, v.at)
                and abs(v.diameter - op["size"]) <= POS_TOL
                and abs(v.drill - op["drill"]) <= POS_TOL
                for v in bg.vias_of(net=op["net"]))
            if not found:
                problems.append(f"ops[{i}]: via at {want} ({op['net']}) "
                                "not in saved board")
        elif kind == "remove":
            if op["uuid"] in text:
                problems.append(f"ops[{i}]: uuid {op['uuid']} still present")
        elif kind == "set_net":
            hit = [it for it in bg.tracks_of() + bg.vias_of()
                   if it.uuid == op["uuid"]]
            if not hit or hit[0].net != op["net"]:
                problems.append(
                    f"ops[{i}]: {op['uuid']} not on {op['net']} in saved "
                    "board (KiCad re-derives copper's net from the pads it "
                    "touches when it saves: renet the pads too - place_edit "
                    "set_pad - or use rename_net)")
        elif kind == "rename_net":
            left = (len(bg.tracks_of(net=op["from"]))
                    + len(bg.vias_of(net=op["from"]))
                    + len(bg.pads_of(net=op["from"]))
                    + len(bg.zones_of(net=op["from"])))
            if left:
                problems.append(f"ops[{i}]: {left} items still on "
                                f"{op['from']}")
    return problems


def apply_ops(pcb: Path, ops: list[dict], *, refill: bool = False) -> list:
    """Validate, resolve ECO selectors, stage, run the SWIG worker, verify,
    atomically swap in.

    Returns one result per input op (a selector op's result counts and names
    what it resolved to). Raises CheckError (original board untouched) on any
    failure. Importable - generators use this directly.
    """
    pcb = Path(pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    bg = geom.load_board(pcb, refresh=True)
    known_nets = set(bg.nets) | {op["to"] for op in ops
                                 if op["op"] == "rename_net"}
    copper = set(bg.copper_layers)
    for i, op in enumerate(ops):
        want = []
        if op["op"] in ("rip", "renet"):
            want = _strs(op["net"]) if "net" in op else []
        elif "net" in op:
            want = [op["net"]]
        if op["op"] == "renet":
            want.append(op["to"])
        if op["op"] == "rename_net":
            want.append(op["from"])
        for n in want:
            if n not in known_nets:
                raise CheckError(f"ops[{i}]: net '{n}' not on board")
        if "layer" in op and op["layer"] not in copper:
            raise CheckError(f"ops[{i}]: layer '{op['layer']}' is not a "
                             f"copper layer of this board")
        if "pads" in op:
            refs = {p.ref for p in bg.pads_of()}
            gone = [x for x in _strs(op["pads"])
                    if x.split(".", 1)[0] not in refs]
            if gone:
                raise CheckError(f"ops[{i}]: pads of parts not on board: "
                                 f"{gone}")
    flat, spans = resolve_ops(bg, ops)

    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        raise CheckError("KiCad bundled python not found (env.py)")

    stage = Path(tempfile.mkdtemp(prefix=".aiee_route_", dir=pcb.parent))
    try:
        staged = stage / pcb.name
        shutil.copy2(pcb, staged)
        worker = routelib.run_worker(
            bp, {"verb": "apply_ops", "board": str(staged),
                 "out": str(staged), "ops": flat}, stage)["results"] \
            if flat else []
        problems = _verify(staged, flat)
        if problems:
            raise CheckError("post-apply verify failed (rolled back): "
                             + "; ".join(problems[:10]))
        if refill and flat:
            _refill(cli, pcb, staged)
        if flat:
            os.replace(staged, pcb)
        return [_fold(op, [worker[j] for j in span], [flat[j] for j in span])
                for op, span in zip(ops, spans)]
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _fold(op: dict, results: list[dict], flat: list[dict]) -> dict:
    """One input op's result from the worker results it expanded into."""
    if op["op"] in OP_FIELDS or op["op"] == "rename_net":
        return results[0]
    out = {"op": op["op"], "count": len(results),
           "status": "absent" if not results else
           {"rip": "removed", "renet": "renetted",
            "add_path": "added"}[op["op"]]}
    if op["op"] in ("rip", "renet"):
        out["uuids"] = [f["uuid"] for f in flat]
    else:
        out["segments"] = [r["status"] for r in results]
    return out


def _refill(cli, pcb: Path, staged: Path) -> None:
    """Refill zones on the staged copy, with the board's own project files
    beside it (kicad-cli would refill against default rules otherwise)."""
    import kc

    for ext in (".kicad_pro", ".kicad_dru"):
        side = pcb.with_suffix(ext)
        if side.is_file():
            shutil.copy2(side, staged.with_suffix(ext))
    try:
        kc.run_drc(cli, staged, refill=True, save_board=True)
    except Exception as e:  # noqa: BLE001 - any refill failure rolls back
        raise CheckError(f"zone refill failed (rolled back): {e}") from e


def load_ops(ops_file: str | None, inline: list[str]) -> list[dict]:
    """--ops FILE and/or --op JSON (repeatable) -> validated op list."""
    ops: list = []
    if ops_file:
        doc = checklib.load_json(ops_file, "ops file")
        if not isinstance(doc, dict) or not isinstance(doc.get("ops"), list):
            raise CheckError("ops file must be {'version': 1, 'ops': [...]}")
        ops += doc["ops"]
        if doc.get("version") != 1:
            raise CheckError("ops file must be {'version': 1, 'ops': [...]}")
    for raw in inline:
        try:
            op = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CheckError(f"--op is not JSON: {e}") from e
        ops += op if isinstance(op, list) else [op]
    if not ops_file and not inline:
        raise CheckError("give --ops FILE or one or more --op JSON")
    return validate_ops({"version": 1, "ops": ops})


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--ops", default=None, help="ops file {version: 1, ops}")
    ap.add_argument("--op", action="append", default=[],
                    help="one op (or a list) as JSON; repeatable")
    ap.add_argument("--refill", action="store_true",
                    help="refill zones on the staged copy before the swap")
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    ops = load_ops(args.ops, args.op)
    results = apply_ops(Path(args.pcb), ops, refill=args.refill)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    payload = {"script": "route_edit", "status": "pass", "board": args.pcb,
               "applied": len(ops), "verified": True, "by_status": counts,
               "refilled": args.refill, "results": results}
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("route_edit", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
