#!/usr/bin/env python
"""fab_export.py - JLCPCB fabrication package: gerbers + drill + pos, zipped.

The P9 export step (SPEC 6.4). Drives the S2 kc.py wrappers to produce the
fab deliverables JLCPCB expects and bundles the gerbers + drill into one zip
(JLC's upload unit). BOM/CPL are bom_cpl.py's job; DFM is dfm_check.py's.

JLC guidance replicated here:
  - Curated layer set (copper x N + silk + mask + paste + edge cuts). We do NOT
    pass --subtract-soldermask: silk-over-pad must remain VISIBLE in the gerber
    so dfm_check (the independent geometry path) can catch it BEFORE the fab
    silently clips it.
  - Drill: Excellon, millimetres (kicad-cli defaults pos+drill sanely once the
    kc.py wrappers force units; LEARNINGS [kicad-cli]).
  - Pos (CPL source): both sides, csv, mm. bom_cpl.py reformats it into JLC's
    CPL with rotation corrections.
  - Gerbers use KiCad's standard extensions/X2, which current JLC accepts. No
    Protel-extension toggle exists in kicad-cli (it always uses standard ones).

Output: a JSON manifest {files, sha256, gerber_zip, layer_count, ...} to stdout
or --out. Exit 0 success / 2 error (an export has no "violations" state).

CLI:
  fab_export.py --pcb board.kicad_pcb --out-dir fab/ [--name NAME]
                [--layers F.Cu,B.Cu,...] [--no-zip] [--out manifest.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import kc  # noqa: E402
from lib import env  # noqa: E402

# Non-copper fab layers, in JLC upload order. Copper layers are inserted from
# the board's actual stackup (F.Cu, In1.Cu ... B.Cu).
_NONCOPPER = [
    "F.Silkscreen", "B.Silkscreen",
    "F.Mask", "B.Mask",
    "F.Paste", "B.Paste",
    "Edge.Cuts",
]


def copper_layers(pcb: Path) -> list[str]:
    """Ordered copper layer names from the board's (layers ...) block
    (F.Cu, In1.Cu, ..., B.Cu). Pure text scan - no SWIG, no kicad-cli."""
    text = pcb.read_text(encoding="utf-8", errors="replace")
    # (layers (0 "F.Cu" signal) (1 "In1.Cu" ...) (31 "B.Cu" signal) ...)
    import re
    names: list[tuple[int, str]] = []
    m = re.search(r"\(layers\b", text)
    if m:
        # scan balanced region after (layers
        depth = 0
        i = m.start()
        for j in range(m.start(), len(text)):
            c = text[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    i = j
                    break
        block = text[m.start():i + 1]
        for lm in re.finditer(r'\((\d+)\s+"([^"]+\.Cu)"', block):
            names.append((int(lm.group(1)), lm.group(2)))
    # Physical stackup order (top->bottom), NOT the file's raw layer-id order:
    # F.Cu, In1.Cu, In2.Cu, ..., B.Cu. board_init/SWIG can number B.Cu below the
    # inner layers, so sorting by raw id mis-orders inners vs B.Cu (matters for
    # dfm_check's outer-vs-inner copper-weight rules).
    def _phys(name: str) -> tuple[int, int]:
        if name == "F.Cu":
            return (0, 0)
        if name == "B.Cu":
            return (2, 0)
        m = re.match(r"In(\d+)\.Cu", name)
        return (1, int(m.group(1)) if m else 0)
    ordered = [n for _, n in sorted(names, key=lambda t: _phys(t[1]))]
    return ordered or ["F.Cu", "B.Cu"]


def jlc_layer_list(pcb: Path) -> list[str]:
    return copper_layers(pcb) + _NONCOPPER


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run(pcb: Path, out_dir: Path, name: str | None = None,
        layers: list[str] | None = None, make_zip: bool = True) -> dict:
    if not pcb.exists():
        raise FileNotFoundError(f"board not found: {pcb}")
    cli = kc.resolve_cli()
    name = name or pcb.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    gerber_dir = out_dir / "gerbers"
    drill_dir = out_dir / "gerbers"          # JLC wants drill alongside gerbers
    layer_names = layers or jlc_layer_list(pcb)
    coppers = copper_layers(pcb)

    g = kc.export_gerbers(cli, pcb, gerber_dir, layers=",".join(layer_names))
    if g.get("status") != "pass":
        raise RuntimeError(f"gerber export failed: {g.get('stderr') or g}")
    d = kc.export_drill(cli, pcb, drill_dir, fmt="excellon")
    if d.get("status") != "pass":
        raise RuntimeError(f"drill export failed: {d.get('stderr') or d}")
    pos_file = out_dir / f"{name}-pos.csv"
    p = kc.export_pos(cli, pcb, pos_file, side="both", fmt="csv", units="mm")
    if p.get("status") != "pass":
        raise RuntimeError(f"pos export failed: {p.get('stderr') or p}")

    # Files that belong in the fab zip: everything in gerber_dir (gerbers +
    # job + drill) - NOT the pos file (CPL is a separate JLC upload).
    fab_files = sorted(f for f in gerber_dir.iterdir() if f.is_file())
    files_meta = [{"name": f.name, "sha256": sha256(f), "bytes": f.stat().st_size}
                  for f in fab_files]

    zip_path = None
    zip_sha = None
    if make_zip:
        zip_path = out_dir / f"{name}_gerbers.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in fab_files:
                zf.write(f, arcname=f.name)
        zip_sha = sha256(zip_path)

    return {
        "script": "fab_export",
        "status": "pass",
        "board": pcb.name,
        "name": name,
        "layer_count": len(coppers),
        "copper_layers": coppers,
        "layers_exported": layer_names,
        "out_dir": str(out_dir),
        "gerber_dir": str(gerber_dir),
        "pos_file": str(pos_file),
        "files": files_meta,
        "gerber_zip": str(zip_path) if zip_path else None,
        "gerber_zip_sha256": zip_sha,
    }


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True, help="input .kicad_pcb")
    ap.add_argument("--out-dir", required=True, help="fab output directory")
    ap.add_argument("--name", help="basename for pos/zip (default: board stem)")
    ap.add_argument("--layers", help="override the JLC layer set (comma list)")
    ap.add_argument("--no-zip", action="store_true", help="skip the gerber zip")
    ap.add_argument("--out", help="write JSON manifest here instead of stdout")
    args = ap.parse_args(argv)

    try:
        rep = run(Path(args.pcb), Path(args.out_dir), name=args.name,
                  layers=args.layers.split(",") if args.layers else None,
                  make_zip=not args.no_zip)
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        err = {"script": "fab_export", "status": "error",
               "error": f"{type(exc).__name__}: {exc}"}
        text = json.dumps(err, indent=1)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        else:
            print(text)
        return 2

    text = json.dumps(rep, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
