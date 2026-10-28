"""place_edit - apply an atomic footprint move/rotate/flip/lock op list (S9, SPEC P6.3).

The pipeline's ONLY writer for placement changes (SPEC section 4: op lists are
applied atomically; agents never raw-edit the board). Edit path decision (V4/V8,
recorded in PROGRESS S9): SWIG bundled python, headless - the spec's "IPC
headless" does not exist at the KiCad-10 pin (no api-server; kipy needs a GUI
pcbnew and the sandboxed-GUI probe now fails "KiCad is not ready to reply").
kipy/IPC is the KiCad-11 migration target.

Contract:
  place_edit.py --pcb B.kicad_pcb --ops ops.json [--out-report r.json]
  ops.json: {"version": 1, "ops": [...]}  (op shapes: see lib/place_swig.py)

Atomic + rollback: ops are validated first (schema + refs against the board);
the board is copied to a scratch dir INSIDE the board's directory; the SWIG
worker edits and saves the copy; the driver re-parses the copy and verifies
every op's target landed (position 1e-3 mm, angle 0.05 deg, side, locked);
only then does os.replace() swap it in (same volume -> atomic). Any failure
leaves the original board byte-identical. Re-applying the same op list is
idempotent (ops are absolute). exit 0 applied+verified / 2 error (rolled back).

set_text (hide / resize / re-layer a Reference or Value field) is the fix
for a refdes with no legal spot near its own pads (dense 0603 rows): on a
silk layer it refuses a size below check_silk's legibility floor or a stroke
below the fab's silk width floor (silk_text_floor()).

Note: KiCad regenerates footprint/graphic UUIDs on every save, so two saves of
the same placement are NOT byte-identical - compare parsed positions, never
file hashes.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import placelib  # noqa: E402
from checklib import CheckError  # noqa: E402

WORKER = SCRIPTS / "lib" / "place_swig.py"

OP_FIELDS = {  # op -> (required, optional)
    "place": ({"ref", "x", "y"}, {"deg", "side"}),
    "move": ({"ref", "x", "y"}, set()),
    "rotate": ({"ref", "deg"}, set()),
    "flip": ({"ref", "side"}, set()),
    "lock": ({"ref", "locked"}, set()),
    # S14 text ops (V17): board-frame silk/fab text + refdes/value moves
    "add_text": ({"text", "x", "y", "layer"}, {"deg", "size", "thickness"}),
    # remove_text + add_text is the only scripted way to RELOCATE a gr_text
    # (add_text matches on the TARGET position, so it can never move one).
    "remove_text": ({"text", "x", "y", "layer"}, set()),
    "move_text": ({"ref", "field", "x", "y"}, {"deg"}),
    # hide / resize / re-layer a Reference|Value field in place: the fix when
    # no legal spot for a refdes exists near its own pads (dense 0603 rows),
    # where move_text has nowhere to go. Needs at least one of the options.
    "set_text": ({"ref", "field"}, {"hide", "size", "thickness", "layer"}),
    # footprint-INTERNAL silk graphics on an already-placed board: a library
    # edit cannot reach one without re-running board_init (and losing the
    # placement), so this is the only scripted route. Never touches text.
    "silk_clear": ({"ref"}, {"layer", "only_offboard"}),
}
TEXT_LAYERS = {"F.SilkS", "B.SilkS", "F.Fab", "B.Fab"}
POS_TOL = 1e-3   # mm
ANG_TOL = 0.05   # deg
FOOTPRINT_OPS = {"place", "move", "rotate", "flip"}  # copper-relevant ops
TEXT_OPS = ("add_text", "remove_text", "move_text", "set_text")
SILK_LAYERS = {"F.SilkS", "B.SilkS"}
SIZE_TOL = 1e-3  # mm


def silk_text_floor() -> tuple[float, float]:
    """(min size, min stroke) for refdes/value text that stays on silk.

    Size is check_silk's legibility floor (MIN_TEXT_H, measured on size_y).
    Stroke is the larger of check_silk's MIN_TEXT_TH and the fab's silk width
    floor (jlc_capabilities.yaml min_silk_width_mm, the same number
    rules_gen writes as the .kicad_dru aiee_silk_width_floor text_thickness
    rule), taken across every profile because the op does not know the
    board's. A shrink below either would trade one finding for another."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import check_silk
    import yaml
    caps = yaml.safe_load((SCRIPTS.parent / "reference"
                           / "jlc_capabilities.yaml").read_text(
                               encoding="utf-8"))
    widths = [row["min_silk_width_mm"]
              for row in (caps.get("design_rules") or {}).values()
              if isinstance(row, dict) and "min_silk_width_mm" in row]
    return (check_silk.MIN_TEXT_H,
            max([check_silk.MIN_TEXT_TH] + widths))


def board_routed(pcb: Path) -> bool:
    """Cheap scan: does the board already carry routed copper?

    Matches top-level (segment ...) / (via ...) nodes; the trailing \\s keeps
    rule-area '(vias not_allowed)' tokens from false-positiving."""
    text = Path(pcb).read_text(encoding="utf-8")
    return re.search(r"\((?:segment|via)\s", text) is not None


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
        for k in ("x", "y", "deg"):
            if k in op and not (isinstance(op[k], (int, float))
                                and math.isfinite(op[k])):
                raise CheckError(f"ops[{i}]: {k} must be a finite number")
        if "side" in op and op["side"] not in ("front", "back"):
            raise CheckError(f"ops[{i}]: side must be front|back")
        if "locked" in op and not isinstance(op["locked"], bool):
            raise CheckError(f"ops[{i}]: locked must be a boolean")
        if kind in ("add_text", "remove_text"):
            if op["layer"] not in TEXT_LAYERS:
                raise CheckError(f"ops[{i}]: layer must be one of "
                                 f"{sorted(TEXT_LAYERS)}")
            if not isinstance(op["text"], str) or not op["text"].strip():
                raise CheckError(f"ops[{i}]: text must be a non-empty string")
            for k in ("size", "thickness"):
                if k in op and not (isinstance(op[k], (int, float))
                                    and 0 < op[k] < 20):
                    raise CheckError(f"ops[{i}]: {k} out of range")
        if kind in ("move_text", "set_text") \
                and op["field"] not in ("reference", "value"):
            raise CheckError(f"ops[{i}]: field must be reference|value")
        if kind == "set_text":
            _validate_set_text(i, op)
        if kind == "silk_clear":
            if op.get("layer", "F.SilkS") not in ("F.SilkS", "B.SilkS"):
                raise CheckError(f"ops[{i}]: layer must be F.SilkS|B.SilkS")
            if "only_offboard" in op and not isinstance(op["only_offboard"],
                                                        bool):
                raise CheckError(f"ops[{i}]: only_offboard must be a boolean")
    return ops


def _validate_set_text(i: int, op: dict) -> None:
    if not op.keys() & {"hide", "size", "thickness", "layer"}:
        raise CheckError(f"ops[{i}] (set_text): needs at least one of "
                         "hide, size, thickness, layer")
    if "hide" in op and not isinstance(op["hide"], bool):
        raise CheckError(f"ops[{i}]: hide must be a boolean")
    if "layer" in op and op["layer"] not in TEXT_LAYERS:
        raise CheckError(f"ops[{i}]: layer must be one of "
                         f"{sorted(TEXT_LAYERS)}")
    for k in ("size", "thickness"):
        if k in op and not (isinstance(op[k], (int, float))
                            and not isinstance(op[k], bool)
                            and 0 < op[k] < 20):
            raise CheckError(f"ops[{i}]: {k} out of range")
    # the floor holds unless the op explicitly sends the field to a fab
    # layer (fab text is never printed); without a layer the field is
    # assumed to stay on silk, which is where refdes fields live
    if op.get("layer", "F.SilkS") in SILK_LAYERS \
            and ("size" in op or "thickness" in op):
        min_size, min_th = silk_text_floor()
        if "size" in op and op["size"] + 1e-9 < min_size:
            raise CheckError(
                f"ops[{i}] (set_text): size {op['size']} mm is below the "
                f"{min_size} mm silk legibility floor (check_silk) - hide "
                "the field instead")
        if "thickness" in op and op["thickness"] + 1e-9 < min_th:
            raise CheckError(
                f"ops[{i}] (set_text): thickness {op['thickness']} mm is "
                f"below the {min_th} mm silk stroke floor (fab min silk "
                "width)")


def _expected_state(ops: list[dict]) -> dict[str, dict]:
    """Fold the op list into the final expected {ref: {x,y,deg,side,locked}}."""
    want: dict[str, dict] = {}
    for op in ops:
        if op["op"] in TEXT_OPS:
            continue  # verified independently by _verify_texts
        if op["op"] == "silk_clear":
            continue  # moves nothing; the worker reports what it removed
        w = want.setdefault(op["ref"], {})
        if op["op"] in ("place", "move"):
            w["x"], w["y"] = op["x"], op["y"]
        if op["op"] in ("place", "rotate") and op.get("deg") is not None:
            w["deg"] = op["deg"]
        if op["op"] in ("place", "flip") and op.get("side") is not None:
            w["side"] = op["side"]
        if op["op"] == "lock":
            w["locked"] = op["locked"]
    return want


def _verify(pcb: Path, ops: list[dict]) -> list[str]:
    model = placelib.PlaceModel(pcb)
    problems = []
    for ref, w in _expected_state(ops).items():
        fp = model.footprints.get(ref)
        if fp is None:
            problems.append(f"{ref}: vanished from saved board")
            continue
        if "x" in w and (abs(fp.pos[0] - w["x"]) > POS_TOL
                         or abs(fp.pos[1] - w["y"]) > POS_TOL):
            problems.append(f"{ref}: position {fp.pos} != ({w['x']}, {w['y']})")
        if "deg" in w and _angdiff(fp.angle, w["deg"]) > ANG_TOL:
            problems.append(f"{ref}: angle {fp.angle} != {w['deg']}")
        if "side" in w and fp.side != w["side"]:
            problems.append(f"{ref}: side {fp.side} != {w['side']}")
        if "locked" in w and fp.locked != w["locked"]:
            problems.append(f"{ref}: locked {fp.locked} != {w['locked']}")
    return problems


def _angdiff(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


# ---------------------------------------------------------------- text verify
# Independent of the SWIG worker: sexpdata parse of the saved file. gr_text
# positions are board-frame; footprint (property ...) positions are LOCAL with
# an ABSOLUTE angle (LEARNINGS [geometry]) - transform abs = fp + R(-deg).local.

def _sx_head(n):
    import sexpdata
    return n[0].value() if isinstance(n, list) and n \
        and isinstance(n[0], sexpdata.Symbol) else None


def _sx_str(v) -> str:
    import sexpdata
    return v.value() if isinstance(v, sexpdata.Symbol) else str(v)


def _parse_board_texts(pcb: Path):
    """-> (gr_texts [{text, layer, x, y, deg}], fields {(ref, field): {x, y, deg}})."""
    import sexpdata
    data = sexpdata.loads(pcb.read_text(encoding="utf-8"))
    gr_texts, fields = [], {}
    for node in data[1:]:
        head = _sx_head(node)
        if head == "gr_text" and len(node) >= 2:
            entry = {"text": _sx_str(node[1]), "layer": None,
                     "x": None, "y": None, "deg": 0.0}
            for sub in node[2:]:
                sh = _sx_head(sub)
                if sh == "at":
                    entry["x"], entry["y"] = float(sub[1]), float(sub[2])
                    if len(sub) > 3:
                        entry["deg"] = float(sub[3])
                elif sh == "layer":
                    entry["layer"] = _sx_str(sub[1])
            gr_texts.append(entry)
        elif head == "footprint":
            fx = fy = fdeg = None
            props = {}
            for sub in node[1:]:
                sh = _sx_head(sub)
                if sh == "at":
                    fx, fy = float(sub[1]), float(sub[2])
                    fdeg = float(sub[3]) if len(sub) > 3 else 0.0
                elif sh == "property" and len(sub) >= 3:
                    pname = _sx_str(sub[1]).lower()
                    if pname in ("reference", "value"):
                        lx = ly = None
                        adeg = 0.0
                        for p in sub[3:]:
                            if _sx_head(p) == "at":
                                lx, ly = float(p[1]), float(p[2])
                                if len(p) > 3:
                                    adeg = float(p[3])
                        props[pname] = (_sx_str(sub[2]), lx, ly, adeg,
                                        _field_style(sub))
            if fx is None or "reference" not in props:
                continue
            ref = props["reference"][0]
            th = math.radians(fdeg or 0.0)
            for pname, (_, lx, ly, adeg, style) in props.items():
                if lx is None:
                    continue
                ax = fx + lx * math.cos(th) + ly * math.sin(th)
                ay = fy - lx * math.sin(th) + ly * math.cos(th)
                fields[(ref, pname)] = {"x": ax, "y": ay, "deg": adeg,
                                        **style}
    return gr_texts, fields


def _field_style(prop) -> dict:
    """{layer, hidden, size_x, size_y, thickness} of a footprint property.

    Hidden is `(hide yes)` on the property (KiCad 8+; `(hide no)` is shown),
    or the older bare `hide` token inside (effects ...)."""
    import sexpdata
    out = {"layer": None, "hidden": False, "size_x": None, "size_y": None,
           "thickness": None}
    for p in prop[3:]:
        h = _sx_head(p)
        if h == "layer" and len(p) > 1:
            out["layer"] = _sx_str(p[1])
        elif h == "hide":
            out["hidden"] = not (len(p) > 1 and _sx_str(p[1]) == "no")
        elif h == "effects":
            for e in p[1:]:
                if isinstance(e, sexpdata.Symbol) and e.value() == "hide":
                    out["hidden"] = True
                if _sx_head(e) != "font":
                    continue
                for f in e[1:]:
                    fh = _sx_head(f)
                    if fh == "size" and len(f) >= 3:
                        out["size_x"], out["size_y"] = float(f[1]), float(f[2])
                    elif fh == "thickness" and len(f) >= 2:
                        out["thickness"] = float(f[1])
    return out


def _verify_set_text(op: dict, got: dict | None) -> list[str]:
    tag = f"set_text {op['ref']}.{op['field']}"
    if got is None:
        return [f"{tag}: field not found in saved board"]
    problems = []
    if "hide" in op and got["hidden"] != op["hide"]:
        problems.append(f"{tag}: hidden {got['hidden']} != {op['hide']}")
    if "layer" in op and got["layer"] != op["layer"]:
        problems.append(f"{tag}: layer {got['layer']} != {op['layer']}")
    if "size" in op and (got["size_y"] is None
                         or abs(got["size_x"] - op["size"]) > SIZE_TOL
                         or abs(got["size_y"] - op["size"]) > SIZE_TOL):
        problems.append(f"{tag}: size ({got['size_x']}, {got['size_y']}) "
                        f"!= {op['size']}")
    if "thickness" in op and (got["thickness"] is None
                              or abs(got["thickness"] - op["thickness"])
                              > SIZE_TOL):
        problems.append(f"{tag}: thickness {got['thickness']} != "
                        f"{op['thickness']}")
    return problems


def _verify_texts(pcb: Path, ops: list[dict]) -> list[str]:
    text_ops = [op for op in ops if op["op"] in TEXT_OPS]
    if not text_ops:
        return []
    problems = []
    gr_texts, fields = _parse_board_texts(pcb)
    for op in text_ops:
        if op["op"] in ("add_text", "remove_text"):
            hits = [t for t in gr_texts
                    if t["text"] == op["text"] and t["layer"] == op["layer"]
                    and abs(t["x"] - op["x"]) <= POS_TOL
                    and abs(t["y"] - op["y"]) <= POS_TOL]
            if op["op"] == "remove_text":
                if hits:
                    problems.append(
                        f"remove_text '{op['text']}': {len(hits)} still on "
                        f"{op['layer']} at ({op['x']}, {op['y']})")
                continue
            if not hits:
                problems.append(
                    f"add_text '{op['text']}' not found on {op['layer']} at "
                    f"({op['x']}, {op['y']})")
            elif len(hits) > 1:
                problems.append(f"add_text '{op['text']}': {len(hits)} "
                                f"duplicates at the target position")
            elif op.get("deg") is not None \
                    and _angdiff(hits[0]["deg"], op["deg"]) > ANG_TOL:
                problems.append(f"add_text '{op['text']}': angle "
                                f"{hits[0]['deg']} != {op['deg']}")
        elif op["op"] == "set_text":
            problems += _verify_set_text(op, fields.get((op["ref"],
                                                         op["field"])))
        else:
            got = fields.get((op["ref"], op["field"]))
            if got is None:
                problems.append(f"move_text {op['ref']}.{op['field']}: "
                                f"field not found in saved board")
            elif (abs(got["x"] - op["x"]) > POS_TOL
                  or abs(got["y"] - op["y"]) > POS_TOL):
                problems.append(
                    f"move_text {op['ref']}.{op['field']}: position "
                    f"({got['x']:.4f}, {got['y']:.4f}) != ({op['x']}, {op['y']})")
            elif op.get("deg") is not None \
                    and _angdiff(got["deg"], op["deg"]) > ANG_TOL:
                problems.append(f"move_text {op['ref']}.{op['field']}: angle "
                                f"{got['deg']} != {op['deg']}")
    return problems


def apply_ops(pcb: Path, ops: list[dict]) -> dict:
    """Validate refs, stage, run the SWIG worker, verify, atomically swap in.

    Returns the worker's per-op results. Raises CheckError (original board
    untouched) on any failure. Importable - place_seed --apply uses this.
    """
    pcb = Path(pcb).resolve()
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    model = placelib.PlaceModel(pcb)
    missing = sorted({op["ref"] for op in ops if "ref" in op}
                     - model.footprints.keys())
    if missing:
        raise CheckError(f"refs not on board: {', '.join(missing)}")

    cli = env.find_kicad_cli()
    bp = env.find_kicad_python(cli) if cli else None
    if bp is None:
        raise CheckError("KiCad bundled python not found (env.py)")

    stage = Path(tempfile.mkdtemp(prefix=".aiee_edit_", dir=pcb.parent))
    try:
        staged = stage / pcb.name
        shutil.copy2(pcb, staged)
        job = {"board": str(staged), "out": str(staged), "ops": ops}
        jf = stage / "job.json"
        jf.write_text(json.dumps(job), encoding="utf-8")
        cp = subprocess.run([str(bp), str(WORKER), str(jf)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=180)
        # Scan BACKWARDS for the last parseable JSON object rather than
        # trusting the last line: KiCad's SWIG runtime prints
        # "swig/python detected a memory leak of type 'PCB_SHAPE *'" lines at
        # interpreter shutdown - i.e. AFTER the worker's own output - whenever
        # an op detached an item from the board (silk_clear), and taking
        # out[-1] then reported a clean run as "worker exit 0 (rolled back)".
        # Same shape as board_init._last_json.
        result = {}
        for line in reversed((cp.stdout or "").strip().splitlines()):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            break
        if cp.returncode != 0 or not result.get("ok"):
            detail = result.get("error") or (cp.stderr or "").strip()[-300:] \
                or f"worker exit {cp.returncode}"
            idx = result.get("index")
            at = f" at ops[{idx}]" if idx is not None else ""
            raise CheckError(f"worker failed{at}: {detail} (rolled back)")
        problems = _verify(staged, ops) + _verify_texts(staged, ops)
        if problems:
            raise CheckError("post-apply verify failed (rolled back): "
                             + "; ".join(problems))
        os.replace(staged, pcb)
        return result["results"]
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--ops", required=True)
    ap.add_argument("--allow-routed", action="store_true",
                    help="permit footprint ops on a board that already has "
                         "tracks/vias (they invalidate the gate place / "
                         "check_decoupling oracles - rip the affected nets "
                         "first and re-run drc_routed after)")
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)

    ops = validate_ops(checklib.load_json(args.ops, "ops file"))
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    # T6 P6A-4 (ladder row 130): a courtyard-legal footprint move on a ROUTED
    # board shorted a board while gate place + check_decoupling stayed green.
    # Text ops (silk sweeps) stay legitimate on routed boards and are exempt.
    fp_ops = [op for op in ops if op["op"] in FOOTPRINT_OPS]
    routed = board_routed(pcb) if fp_ops else False
    if fp_ops and routed and not args.allow_routed:
        raise CheckError(
            "board has routing - footprint moves invalidate the gate place / "
            "check_decoupling oracles (LEARNINGS 2026-07-30: rip the "
            "affected nets FIRST, then move, then route fresh). Pass "
            "--allow-routed to proceed deliberately and re-run drc_routed "
            "after")
    results = apply_ops(pcb, ops)
    payload = {"script": "place_edit", "status": "pass", "board": args.pcb,
               "applied": len(ops), "verified": True, "results": results}
    if fp_ops and routed:
        payload["routed_board"] = True
        payload["warnings"] = ["footprint ops applied on a routed board "
                               "(--allow-routed): re-run the drc_routed gate"]
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("place_edit", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
