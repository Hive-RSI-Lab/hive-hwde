#!/usr/bin/env python
"""layer_views.py - one PDF per copper layer, drawn the way KiCad shows it, plus 3D views.

Each copper layer is plotted by kicad-cli (pcb export pdf, the default colour
theme on KiCad's dark background) with its side's context: F.Cu with
F.SilkS, F.Fab and F.CrtYd, B.Cu with the B.* set, an inner layer with
F.Fab, and all of them with Edge.Cuts. The bottom layers are seen from the
top, as KiCad's editor shows them. A plot has no net names, so each page
gets an overlay drawn from the board's own geometry (lib/layer_swig.py under
KiCad's python): the net's name on every pad of that layer, along its long
axis and sized to fit; along each track segment long enough to hold it,
at most one label per net every LABEL_SPACING mm; and on each zone island.
kicad-cli plots a page at scale 1 with board millimetres as page
millimetres, which is what lets the overlay land on the copper; the merged
page is then cropped to the board outline plus MARGIN. Everything stays
vector, so the names stay sharp however far a reader zooms in.

The 3D views (top, bottom, iso) come from render.py; a PNG newer than the
board is kept, so a rebuild does not render the board again.

Outputs in --out-dir, named after the board file: <board>_<layer>.pdf
(F_Cu, In1_Cu, ..., B_Cu) and <board>_<view>.png. JSON to stdout (or --out FILE): {script, status, input,
layers:[{layer, path, labels}], views:[{view, path, status}], warnings}.
Exit 0 = every layer and every asked-for view produced (--no-3d asks for
none), 1 = the layers are there but a 3D view failed, 2 = error.

CLI:
  layer_views.py board.kicad_pcb --out-dir DIR [--no-3d] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import kc  # noqa: E402
import render  # noqa: E402  (render_views, read-only use)
import routelib  # noqa: E402
from lib import env  # noqa: E402

WORKER = Path(__file__).resolve().parent / "lib" / "layer_swig.py"
MM = 72 / 25.4          # points per mm
MARGIN = 2.0            # mm of background kept around the board outline
LABEL_SPACING = 6.0     # mm between two labels of one net on one layer
BACKGROUND = "#001023"  # KiCad's default PCB editor background
ADVANCE = 0.602         # DejaVu Sans Mono advance width, in em
MIN_EM = 0.06           # mm; a name smaller than this is not drawn
VIEWS = ["top", "bottom", "iso"]


def context_layers(layer: str) -> list[str]:
    """The layers plotted under a copper layer: its side's silk, fab and
    courtyard (an inner layer gets F.Fab for the footprints), and the outline."""
    if layer == "F.Cu":
        side = ["F.SilkS", "F.Fab", "F.CrtYd"]
    elif layer == "B.Cu":
        side = ["B.SilkS", "B.Fab", "B.CrtYd"]
    else:
        side = ["F.Fab"]
    return [layer] + side + ["Edge.Cuts"]


def short_net(net: str) -> str:
    """The name KiCad prints on copper: the last part of a hierarchical path."""
    return net.rstrip("/").rsplit("/", 1)[-1] or net


def upright(deg: float) -> float:
    """An angle folded into (-90, 90] so the text never reads upside down."""
    deg = (deg + 180) % 360 - 180
    if deg > 90:
        deg -= 180
    elif deg <= -90:
        deg += 180
    return deg


def pad_labels(pads: list[dict], layer: str) -> list[tuple]:
    """(x, y, text, em, angle) for every pad on the layer: along the pad's
    long side, no taller than half its short side, no longer than 90% of it."""
    out = []
    for p in pads:
        if layer not in p["layers"]:
            continue
        text = short_net(p["net"])
        long_, short = max(p["w"], p["h"]), min(p["w"], p["h"])
        angle = p["angle"] + (0 if p["w"] >= p["h"] else 90)
        em = min(0.5 * short, 0.9 * long_ / (ADVANCE * len(text)))
        if em >= MIN_EM:
            out.append((p["x"], p["y"], text, em, upright(angle)))
    return out


def track_labels(tracks: list[dict], layer: str) -> list[tuple]:
    """(x, y, text, em, angle) at the middle of each segment that holds its
    net's name with a character's room each end, longest segments first, and
    none within LABEL_SPACING of another label of the same net."""
    out, placed = [], {}
    segs = [t for t in tracks if t["layer"] == layer]
    segs.sort(key=lambda t: -math.hypot(t["x1"] - t["x0"], t["y1"] - t["y0"]))
    for t in segs:
        dx, dy = t["x1"] - t["x0"], t["y1"] - t["y0"]
        length = math.hypot(dx, dy)
        text = short_net(t["net"])
        em = 0.8 * t["width"]
        if em < MIN_EM or length < ADVANCE * em * (len(text) + 2):
            continue
        x, y = (t["x0"] + t["x1"]) / 2, (t["y0"] + t["y1"]) / 2
        near = placed.setdefault(t["net"], [])
        if any(math.hypot(x - a, y - b) < LABEL_SPACING for a, b in near):
            continue
        near.append((x, y))
        # board y points down, so the angle a reader sees is atan2(-dy, dx)
        out.append((x, y, text, em, upright(math.degrees(math.atan2(-dy, dx)))))
    return out


def zone_labels(zones: list[dict], layer: str) -> list[tuple]:
    return [(z["x"], z["y"], short_net(z["net"]), 1.0, 0.0)
            for z in zones if z["layer"] == layer]


def overlay_pdf(path: Path, page_mm: tuple[float, float], labels: list[tuple]) -> None:
    """A transparent page the size of the plot with each label drawn in
    board millimetres (y down), as embedded TrueType text."""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["pdf.fonttype"] = 42
    import matplotlib.pyplot as plt

    w, h = page_mm
    fig = plt.figure(figsize=(w / 25.4, h / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    for x, y, text, em, angle in labels:
        ax.text(x, y, text, fontsize=em * MM, family="DejaVu Sans Mono",
                color="white", ha="center", va="center", rotation=angle,
                rotation_mode="anchor")
    fig.savefig(path, transparent=True)
    plt.close(fig)


def plot_layer(cli: Path, pcb: Path, layer: str, geo: dict, out: Path,
               stage: Path) -> int:
    """Plot one copper layer with its context, overlay its net names, crop
    it to the board and write it to `out`. Returns the number of labels."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import RectangleObject

    raw = stage / f"{out.stem}_plot.pdf"
    cp = kc.run_cli(cli, ["pcb", "export", "pdf", "--mode-single", "--exclude-value", "--bg-color",
                          BACKGROUND, "-l", ",".join(context_layers(layer)),
                          "-o", str(raw), str(pcb)])
    if cp.returncode != 0 or not raw.is_file():
        raise RuntimeError(f"kicad-cli pdf plot of {layer} failed (rc={cp.returncode}): "
                           + (cp.stderr or cp.stdout or "")[-300:])
    page = PdfReader(raw).pages[0]
    w_pt, h_pt = float(page.mediabox.width), float(page.mediabox.height)
    labels = (zone_labels(geo["zones"], layer) + track_labels(geo["tracks"], layer)
              + pad_labels(geo["pads"], layer))
    ov = stage / f"{out.stem}_names.pdf"
    overlay_pdf(ov, (w_pt / MM, h_pt / MM), labels)
    page.merge_page(PdfReader(ov).pages[0])
    x0, y0, x1, y1 = geo["edge"]
    box = RectangleObject([(x0 - MARGIN) * MM, h_pt - (y1 + MARGIN) * MM,
                           (x1 + MARGIN) * MM, h_pt - (y0 - MARGIN) * MM])
    page.mediabox = box
    page.cropbox = box
    writer = PdfWriter()
    writer.add_page(page)
    writer.compress_identical_objects()
    with out.open("wb") as fh:
        writer.write(fh)
    return len(labels)


def board_geometry(cli: Path, pcb: Path, stage: Path) -> dict:
    py = env.find_kicad_python(cli)
    if py is None:
        raise RuntimeError("KiCad's python (with pcbnew) not found beside kicad-cli")
    return routelib.run_worker(py, {"verb": "dump", "board": str(pcb)}, stage,
                               timeout=120, worker=WORKER)


def views_3d(pcb: Path, out_dir: Path, warnings: list[str]) -> list[dict]:
    """top, bottom and iso PNGs, rendering only those older than the board."""
    want = [v for v in VIEWS
            if not ((p := out_dir / f"{pcb.stem}_{v}.png").is_file()
                    and p.stat().st_mtime >= pcb.stat().st_mtime)]
    done = [{"view": v, "path": str(out_dir / f"{pcb.stem}_{v}.png"), "status": "kept"}
            for v in VIEWS if v not in want]
    if want:
        rep = render.render_views(pcb, want, out_dir, width=2400, height=1600,
                                  quality="high")
        for r in rep["outputs"]:
            done.append({"view": r["view"], "path": r["path"], "status": r["status"]})
            if r["status"] != "pass":
                warnings.append(f"3D {r['view']} view failed: {r.get('stderr_tail', '')[-200:]}")
        if rep.get("models_missing"):
            warnings.append(f"{len(rep['models_missing'])} 3D model(s) missing; "
                            "those parts render as bare pads")
    return sorted(done, key=lambda d: VIEWS.index(d["view"]))


def run(pcb: Path, out_dir: Path, with_3d: bool = True) -> tuple[dict, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    cli = kc.resolve_cli()
    warnings: list[str] = []
    layers = []
    with tempfile.TemporaryDirectory(prefix="hwde_layers_") as td:
        stage = Path(td)
        geo = board_geometry(cli, pcb, stage)
        if not geo.get("edge"):
            raise RuntimeError("board has no Edge.Cuts outline to crop the plots to")
        for layer in geo["copper"]:
            out = out_dir / f"{pcb.stem}_{layer.replace('.', '_')}.pdf"
            n = plot_layer(cli, pcb, layer, geo, out, stage)
            layers.append({"layer": layer, "path": str(out), "labels": n})
    views = views_3d(pcb, out_dir, warnings) if with_3d else []
    ok = all(v["status"] in ("pass", "kept") for v in views)
    report = {"script": "layer_views", "status": "pass" if ok else "violations",
              "input": str(pcb), "layers": layers, "views": views,
              "warnings": warnings}
    return report, 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="input .kicad_pcb")
    ap.add_argument("--out-dir", required=True, help="where the PDFs and PNGs go")
    ap.add_argument("--no-3d", action="store_true", help="plot the layers only")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)
    try:
        report, rc = run(Path(args.input), Path(args.out_dir), not args.no_3d)
    except Exception:
        report, rc = {"script": "layer_views", "status": "error",
                      "error": traceback.format_exc()[-2000:]}, 2
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return rc


if __name__ == "__main__":
    sys.exit(main())
