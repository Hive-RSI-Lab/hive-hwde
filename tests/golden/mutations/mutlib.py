"""Shared helpers for golden-board mutation scripts (venv python).

Mutations are DETERMINISTIC text surgery on the committed golden
.kicad_pcb files: exact-match edits fail loudly if the golden changed,
and any added items carry hard-coded UUIDs. After surgery the board is
run through `kicad-cli pcb drc --refill-zones --save-board`, which both
validates that KiCad still parses the file and refreshes zone fills.

Every mutation script:
  mutate.py [--out DIR]   -> writes <out>/<board>.kicad_pcb (+ .kicad_pro)
Sidecar convention: a mutant dir may also hold `constraints.json`,
`decoupling.json` and a `parts/` dir that override the golden's for THAT mutant
only (a fault that lives in an input the check reads, not in copper). A script
returns them from `surgery` as detail["sidecars"] = {relpath: text}; they are
written next to the board, and the board is copied even when unchanged so every
mutant dir is self-contained.
  JSON summary to stdout; exit 0 on success, 2 on error.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent
REPO = GOLDEN.parents[1]
sys.path.insert(0, str(REPO / ".claude" / "skills" / "hwde" / "scripts" / "lib"))

import env  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class SurgeryError(RuntimeError):
    pass


def load_golden(board: str) -> str:
    path = GOLDEN / board / f"{board}.kicad_pcb"
    if not path.exists():
        raise SurgeryError(f"golden board missing: {path}")
    return path.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, what: str) -> str:
    n = text.count(old)
    if n != 1:
        raise SurgeryError(f"{what}: expected exactly 1 match, found {n}: "
                           f"{old[:80]!r}")
    return text.replace(old, new, 1)


def remove_block(text: str, anchor: str, opener: str, what: str) -> str:
    """Remove the paren-balanced block that starts at the nearest `opener`
    before `anchor` (e.g. anchor='(at 141.9 123.4)', opener='(via')."""
    ai = text.find(anchor)
    if ai < 0 or text.find(anchor, ai + 1) >= 0:
        raise SurgeryError(f"{what}: anchor not unique: {anchor!r}")
    start = text.rfind(opener, 0, ai)
    if start < 0:
        raise SurgeryError(f"{what}: opener {opener!r} not found before anchor")
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        raise SurgeryError(f"{what}: unbalanced block")
    # swallow leading whitespace back to the previous newline
    ws = start
    while ws > 0 and text[ws - 1] in " \t":
        ws -= 1
    if ws > 0 and text[ws - 1] == "\n":
        ws -= 1
    return text[:ws] + text[i + 1:]


def footprint_block(text: str, ref: str) -> tuple[int, int]:
    """(start, end) of the top-level footprint block for `ref`."""
    anchor = f'(property "Reference" "{ref}"'
    ai = text.find(anchor)
    if ai < 0:
        raise SurgeryError(f"footprint {ref}: reference property not found")
    start = text.rfind("\n\t(footprint", 0, ai)
    if start < 0:
        raise SurgeryError(f"footprint {ref}: block start not found")
    nxt = text.find("\n\t(", ai)
    if nxt < 0:
        raise SurgeryError(f"footprint {ref}: block end not found")
    return start, nxt


def edit_footprint(text: str, ref: str, old: str, new: str, what: str) -> str:
    s, e = footprint_block(text, ref)
    block = text[s:e]
    n = block.count(old)
    if n != 1:
        raise SurgeryError(f"{what}: expected 1 match in {ref} block, got {n}")
    return text[:s] + block.replace(old, new, 1) + text[e:]


def append_items(text: str, items: str, what: str) -> str:
    """Append top-level items before the file's final closing paren."""
    end = text.rstrip()
    if not end.endswith(")"):
        raise SurgeryError(f"{what}: unexpected file tail")
    cut = len(end) - 1
    return end[:cut] + items + ")\n"


def segment_sexpr(start, end, width, layer, net, uuid) -> str:
    return (f"\t(segment\n"
            f"\t\t(start {start[0]} {start[1]})\n"
            f"\t\t(end {end[0]} {end[1]})\n"
            f"\t\t(width {width})\n"
            f"\t\t(layer \"{layer}\")\n"
            f"\t\t(net \"{net}\")\n"
            f"\t\t(uuid \"{uuid}\")\n"
            f"\t)\n")


def via_sexpr(at, net, uuid, size=0.6, drill=0.3) -> str:
    return (f"\t(via\n"
            f"\t\t(at {at[0]} {at[1]})\n"
            f"\t\t(size {size})\n"
            f"\t\t(drill {drill})\n"
            f"\t\t(layers \"F.Cu\" \"B.Cu\")\n"
            f"\t\t(net \"{net}\")\n"
            f"\t\t(uuid \"{uuid}\")\n"
            f"\t)\n")


def silk_text_sexpr(text_str, at, uuid, size=1.0) -> str:
    return (f"\t(gr_text \"{text_str}\"\n"
            f"\t\t(at {at[0]} {at[1]} 0)\n"
            f"\t\t(layer \"F.SilkS\")\n"
            f"\t\t(uuid \"{uuid}\")\n"
            f"\t\t(effects\n"
            f"\t\t\t(font\n"
            f"\t\t\t\t(size {size} {size})\n"
            f"\t\t\t\t(thickness 0.15)\n"
            f"\t\t\t)\n"
            f"\t\t)\n"
            f"\t)\n")


def keepout_zone_sexpr(layer, rect, name, uuid) -> str:
    x1, y1, x2, y2 = rect
    return (f"\t(zone\n"
            f"\t\t(net 0)\n"
            f"\t\t(net_name \"\")\n"
            f"\t\t(layers \"{layer}\")\n"
            f"\t\t(uuid \"{uuid}\")\n"
            f"\t\t(name \"{name}\")\n"
            f"\t\t(hatch edge 0.5)\n"
            f"\t\t(keepout\n"
            f"\t\t\t(tracks allowed)\n"
            f"\t\t\t(vias allowed)\n"
            f"\t\t\t(pads allowed)\n"
            f"\t\t\t(copperpour not_allowed)\n"
            f"\t\t\t(footprints allowed)\n"
            f"\t\t)\n"
            f"\t\t(connect_pads\n"
            f"\t\t\t(clearance 0)\n"
            f"\t\t)\n"
            f"\t\t(min_thickness 0.25)\n"
            f"\t\t(filled_areas_thickness no)\n"
            f"\t\t(fill\n"
            f"\t\t\t(thermal_gap 0.5)\n"
            f"\t\t\t(thermal_bridge_width 0.5)\n"
            f"\t\t)\n"
            f"\t\t(polygon\n"
            f"\t\t\t(pts\n"
            f"\t\t\t\t(xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2}) (xy {x1} {y2})\n"
            f"\t\t\t)\n"
            f"\t\t)\n"
            f"\t)\n")


def golden_json(board: str, name: str) -> dict:
    """The golden's sidecar (constraints.json / decoupling.json) as a dict."""
    return json.loads((GOLDEN / board / name).read_text(encoding="utf-8"))


def dump_json(doc: dict) -> str:
    return json.dumps(doc, indent=2) + "\n"


def finalize(mutant: str, board: str, text: str, out_dir: Path | None,
             detail: dict) -> dict:
    """Write mutant board + project, refill zones, return summary dict."""
    out = out_dir or (GOLDEN / "mutants" / mutant)
    out.mkdir(parents=True, exist_ok=True)
    sidecars = detail.pop("sidecars", None) or {}
    for rel, body in sidecars.items():
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
    pcb = out / f"{board}.kicad_pcb"
    pcb.write_text(text, encoding="utf-8")
    shutil.copyfile(GOLDEN / board / f"{board}.kicad_pro",
                    out / f"{board}.kicad_pro")

    cli = env.find_kicad_cli()
    if cli is None:
        raise SurgeryError("kicad-cli not found")
    rep = out / "_refill.json"
    cp = subprocess.run(
        [str(cli), "pcb", "drc", "--format", "json", "--refill-zones",
         "--save-board", "-o", str(rep), str(pcb)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    ok = rep.exists()
    rep.unlink(missing_ok=True)
    if not ok:
        raise SurgeryError(
            f"refill/parse failed: {cp.stdout.strip()} {cp.stderr.strip()}")
    return {"script": mutant, "status": "pass", "board": board,
            "out": str(pcb), "sidecars": sorted(sidecars), **detail}


def run(mutant: str, board: str, surgery, argv=None) -> int:
    """Standard main: parse args, apply `surgery(text) -> (text, detail)`."""
    import argparse
    ap = argparse.ArgumentParser(description=f"mutation: {mutant}")
    ap.add_argument("--out", help="output dir (default tests/golden/mutants/"
                    f"{mutant}/)")
    args = ap.parse_args(argv)
    try:
        text, detail = surgery(load_golden(board))
        result = finalize(mutant, board, text,
                          Path(args.out) if args.out else None, detail)
    except Exception as exc:
        print(json.dumps({"script": mutant, "status": "error",
                          "error": str(exc)}))
        return 2
    print(json.dumps(result))
    return 0
