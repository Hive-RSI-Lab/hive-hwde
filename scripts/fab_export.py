#!/usr/bin/env python
"""fab_export.py - JLCPCB fabrication package: gerbers + drill + IPC-D-356 + pos.

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
  - IPC-D-356 netlist `<name>.ipc` (kicad-cli pcb export ipcd356) in the
    gerber dir and zip: the fab's flying-probe E-test runs against it.
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
import re
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


def board_stackup_entry(pcb: Path) -> tuple[str, dict | None]:
    """(material family, the stackups.yaml entry the board was built from).
    The entry is the available one of that family whose dielectric
    thicknesses match the board's stackup block; None when none does."""
    import yaml
    import fabmaterial
    fam = fabmaterial.board_family(pcb)
    diel = [d["thickness_mm"] or 0.0 for d in fabmaterial.board_dielectrics(pcb)]
    stacks = yaml.safe_load((SCRIPTS.parent / "reference" / "stackups.yaml")
                            .read_text(encoding="utf-8"))
    for name, st in (stacks.get("stackups") or {}).items():
        if st.get("available") is False \
                or st.get("material_family", fabmaterial.FR4) != fam:
            continue
        mine = [float(ly["thickness_mm"]) for ly in st["stack"]
                if ly["type"] != "copper"]
        if len(mine) == len(diel) and all(
                abs(a - b) < 1e-4 for a, b in zip(mine, diel)):
            return fam, {"name": name, **st}
    return fam, None


def write_order_notes(pcb: Path, out_dir: Path) -> Path | None:
    """fab/ORDER-NOTES.md for a board off FR-4: the material, thickness,
    finish and solved lines to select on JLC's quote page, so the board
    built is the board designed (SI-09). None on FR-4 (nothing to choose
    beyond the defaults order_submit already lists)."""
    import fabmaterial
    fam, entry = board_stackup_entry(pcb)
    if fam == fabmaterial.FR4 or fabmaterial.not_offered(fam):
        return None
    text = fabmaterial.order_notes(
        fam, entry, pcb.stem,
        impedance=(entry or {}).get("controlled_impedance"))
    if entry is None:
        text += ("\n> The board's stackup block matches no stackups.yaml "
                 "entry: check the thickness by hand.\n")
    path = out_dir / "ORDER-NOTES.md"
    path.write_text(text, encoding="utf-8")
    return path


def aux_origin(pcb: Path) -> tuple[float, float] | None:
    """The board's aux axis (drill/place) origin, when it isn't (0, 0)."""
    m = re.search(r"\(aux_axis_origin ([-\d.]+) ([-\d.]+)\)", pcb.read_text(encoding="utf-8"))
    if m and (float(m.group(1)), float(m.group(2))) != (0.0, 0.0):
        return float(m.group(1)), float(m.group(2))
    return None


def export_ipc_absolute(cli: Path, pcb: Path, ipc_file: Path) -> dict:
    """IPC-D-356 in the gerbers' coordinates. kicad-cli writes it from the
    board's aux (drill/place) origin when the board has one, while gerbers
    and drill are absolute: an Altium import keeps Altium's origin there, and
    every netlist point then lands on no copper (each one an open at the
    fab's tester). Such a board's netlist comes from a copy with the aux
    origin at (0, 0)."""
    if not aux_origin(pcb):
        return kc.export_ipcd356(cli, pcb, ipc_file)
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        for ext in (".kicad_pro", ".kicad_dru"):
            if pcb.with_suffix(ext).exists():
                shutil.copy(pcb.with_suffix(ext), Path(td) / (pcb.stem + ext))
        copy = Path(td) / pcb.name
        copy.write_text(re.sub(r"\(aux_axis_origin [-\d.]+ [-\d.]+\)", "(aux_axis_origin 0 0)",
                               pcb.read_text(encoding="utf-8"), count=1), encoding="utf-8")
        return kc.export_ipcd356(cli, copy, ipc_file)


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
    # The bare-board tester's netlist, in the zip beside the gerbers: JLC,
    # PCBWay and Eurocircuits E-test against it when one is supplied, else
    # against a netlist they extract from the same gerbers (so an export
    # defect would test good). dfm_check compares the gerbers with it.
    ipc_file = gerber_dir / f"{name}.ipc"
    n = export_ipc_absolute(cli, pcb, ipc_file)
    if n.get("status") != "pass":
        raise RuntimeError(f"ipcd356 export failed: {n.get('stderr_tail') or n}")
    pos_file = out_dir / f"{name}-pos.csv"
    p = kc.export_pos(cli, pcb, pos_file, side="both", fmt="csv", units="mm")
    if p.get("status") != "pass":
        raise RuntimeError(f"pos export failed: {p.get('stderr') or p}")

    # Files that belong in the fab zip: everything in gerber_dir (gerbers +
    # job + drill) - NOT the pos file (CPL is a separate JLC upload).
    notes = write_order_notes(pcb, out_dir)

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
        "ipc_netlist": str(ipc_file),
        "files": files_meta,
        "gerber_zip": str(zip_path) if zip_path else None,
        "gerber_zip_sha256": zip_sha,
        **({"order_notes": str(notes)} if notes else {}),
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
