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
}
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
        kind = op.get("op")
        if kind not in OP_FIELDS:
            raise CheckError(f"ops[{i}]: unknown op '{kind}'")
        req, opt = OP_FIELDS[kind]
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
        for k in ("layer", "net"):
            if k in op and not (isinstance(op[k], str) and op[k]):
                raise CheckError(f"ops[{i}]: {k} must be a non-empty string")
    return ops


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
    return problems


def apply_ops(pcb: Path, ops: list[dict]) -> dict:
    """Validate, stage, run the SWIG worker, verify, atomically swap in.

    Returns the worker's per-op results. Raises CheckError (original board
    untouched) on any failure. Importable - generators use this directly.
    """
    pcb = Path(pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    bg = geom.load_board(pcb, refresh=True)
    known_nets = set(bg.nets)
    copper = set(bg.copper_layers)
    for i, op in enumerate(ops):
        if "net" in op and op["net"] not in known_nets:
            raise CheckError(f"ops[{i}]: net '{op['net']}' not on board")
        if "layer" in op and op["layer"] not in copper:
            raise CheckError(f"ops[{i}]: layer '{op['layer']}' is not a "
                             f"copper layer of this board")

    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        raise CheckError("KiCad bundled python not found (env.py)")

    stage = Path(tempfile.mkdtemp(prefix=".aiee_route_", dir=pcb.parent))
    try:
        staged = stage / pcb.name
        shutil.copy2(pcb, staged)
        result = routelib.run_worker(
            bp, {"verb": "apply_ops", "board": str(staged),
                 "out": str(staged), "ops": ops}, stage)
        problems = _verify(staged, ops)
        if problems:
            raise CheckError("post-apply verify failed (rolled back): "
                             + "; ".join(problems[:10]))
        os.replace(staged, pcb)
        return result["results"]
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--ops", required=True)
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    ops = validate_ops(checklib.load_json(args.ops, "ops file"))
    results = apply_ops(Path(args.pcb), ops)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    payload = {"script": "route_edit", "status": "pass", "board": args.pcb,
               "applied": len(ops), "verified": True, "by_status": counts,
               "results": results}
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("route_edit", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
