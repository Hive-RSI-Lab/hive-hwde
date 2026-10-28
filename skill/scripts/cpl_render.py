#!/usr/bin/env python
"""cpl_render.py - draw each placed part the way JLC's assembly preview does.

The image half of the CPL placement check (the script half is cpl_verify.py).
For every part in the CPL it draws, in board coordinates:

  board     the footprint's silkscreen (white) and copper pads (gold), with
            the board's pad 1 outlined in cyan and labelled "1" - and, on a
            diode, LED or polarised cap, the board's K or + pad labelled.
  part      the part's OWN LCSC footprint model (lib/easyeda.py) at the CPL
            position and rotation: translucent grey pads inside a body
            outline, a red dot on the model's pin 1 and, when polarised, its
            K or + pad labelled in red.

A part is right when the red pin-1 dot sits on the cyan pad 1 (and the red K/+
label on the board's). Parts go several to an image (--per-image, default 9),
one titled crop each. A part with no model is drawn with the board only and a
"NO LCSC MODEL" title, so the image pass sees it as unverifiable. A part whose
model fetch failed (an EasyEDA 403/429 or a network error) is a separate case:
it is titled "FETCH FAILED", and the fix is to rerun, not to treat it as no
model.

The images feed the fab step's vision agent (agents/dfm.md), which writes
cpl_visual.json; cpl_verify.merge_visual / dfm_check put that beside the
script verdict. This script never calls a model.

CLI:
  cpl_render.py --pcb board.kicad_pcb --cpl fab/CPL.csv --out-dir fab/cpl_render
                [--parts parts.json] [--cache-dir DIR] [--offline]
                [--per-image N] [--out index.json]
Writes cpl_render_<n>.png and index.json ({"images": [{"file", "refs"}],
"parts": {ref: {"lcsc", "cpl_rot", "polar", "has_model"[, "fetch_error"]}}}
plus "fetch_failed" [refs], "rate_limited" and a rerun "note" when a fetch
failed). "polar" is null for a part whose fetch failed, since it can't be judged
without the model.
Exit 0 / 1 fetch failed (index.json still written) / 2 error.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import cpl_verify as cv  # noqa: E402
import easyeda  # noqa: E402


def _rect(cx, cy, w, h, deg):
    pts = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
    return [(cx + x, cy + y) for x, y in (cv.rot(px, py, deg) for px, py in pts)]


def board_shapes(fp: cv.Footprint) -> dict:
    """The board side in board coordinates: pads, silk, pad 1, role pads."""
    def place(x, y):
        dx, dy = cv.rot(x, y, fp.rot)
        return fp.x + dx, fp.y + dy
    pads = []
    for p in fp.pads:
        cx, cy = place(p.x, p.y)
        pads.append({"number": p.number, "center": (cx, cy),
                     "poly": _rect(cx, cy, p.w, p.h, p.angle)})
    silk = [[place(*pt[:2]) for pt in line] for line in fp.silk]
    return {"pads": pads, "silk": silk}


def model_shapes(model, cpl: dict) -> dict:
    """The LCSC model placed at the CPL position and rotation."""
    def place(x, y):
        dx, dy = cv.rot(x, y, cpl["rot"])
        return cpl["x"] + dx, cpl["y"] + dy
    pads = []
    for p in model.pads:
        cx, cy = place(p.x, p.y)
        pads.append({"number": p.number, "center": (cx, cy),
                     "poly": _rect(cx, cy, p.w, p.h, cpl["rot"] - p.rot)})
    xs = [x for p in model.pads for x in (p.x - p.w / 2, p.x + p.w / 2)]
    ys = [y for p in model.pads for y in (p.y - p.h / 2, p.y + p.h / 2)]
    body = [place(x, y) for x, y in ((min(xs), min(ys)), (max(xs), min(ys)),
                                     (max(xs), max(ys)), (min(xs), max(ys)))]
    pin1 = next((p["center"] for p in pads if p["number"] == "1"), None)
    return {"pads": pads, "body": body, "pin1": pin1}


def _role_pad(roles: dict[str, str], want=("K", "+")):
    for num, r in roles.items():
        if r in want:
            return num, r
    return None, None


def render(pcb: Path, cpl: dict[str, dict], lcsc: dict[str, str],
           cache_dir: Path, out_dir: Path, fetch: bool = True,
           per_image: int = 9) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon

    fps = cv.board_footprints(pcb)
    out_dir.mkdir(parents=True, exist_ok=True)
    refs = sorted((r for r in cpl if r in fps), key=cv._natural)
    index = {"images": [], "parts": {}, "fetch_failed": []}
    for start in range(0, len(refs), per_image):
        chunk = refs[start:start + per_image]
        cols = min(3, len(chunk))
        rows = math.ceil(len(chunk) / cols)
        fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 4 * rows),
                                 squeeze=False)
        for ax in axes.flat:
            ax.set_axis_off()
        for ax, ref in zip(axes.flat, chunk):
            fp, c = fps[ref], cpl[ref]
            errors: dict = {}
            model = easyeda.get(lcsc.get(ref, ""), cache_dir, fetch=fetch,
                                errors=errors)
            polar = cv.is_polar(fp, model)
            index["parts"][ref] = {"lcsc": lcsc.get(ref), "cpl_rot": c["rot"],
                                   "polar": polar,
                                   "has_model": model is not None}
            failed = model is None and bool(errors)
            if failed:
                index["parts"][ref]["polar"] = None
                index["parts"][ref]["fetch_error"] = next(iter(errors.values()))
                index["fetch_failed"].append(ref)
            b = board_shapes(fp)
            ax.set_facecolor("#1d5e2f")
            for p in b["pads"]:
                ax.add_patch(Polygon(p["poly"], closed=True, fc="#c9a227",
                                     ec="none", zorder=1))
            for line in b["silk"]:
                ax.plot([q[0] for q in line], [q[1] for q in line],
                        color="white", lw=1.2, zorder=2)
            for p in b["pads"]:
                if p["number"] == "1":
                    ax.add_patch(Polygon(p["poly"], closed=True, fc="none",
                                         ec="cyan", lw=2.0, zorder=3))
                    ax.annotate("1", p["center"], color="cyan", fontsize=9,
                                ha="center", va="center", zorder=6)
            if polar:
                num, r = _role_pad(cv.board_roles(fp, model) or {"1": "K"})
                for p in b["pads"]:
                    if p["number"] == num and num != "1":
                        ax.annotate(r, p["center"], color="cyan", fontsize=9,
                                    ha="center", va="center", zorder=6)
            title = f"{ref}  {lcsc.get(ref) or 'no LCSC'}  CPL {c['rot']:g}"
            if failed:
                title = f"{ref}  FETCH FAILED"
            elif model is None:
                title = f"{ref}  NO LCSC MODEL"
            else:
                m = model_shapes(model, c)
                ax.add_patch(Polygon(m["body"], closed=True, fc="#80808040",
                                     ec="#b0b0b0", lw=1.0, zorder=4))
                for p in m["pads"]:
                    ax.add_patch(Polygon(p["poly"], closed=True,
                                         fc="#d0d0d070", ec="#e0e0e0",
                                         lw=0.6, zorder=4))
                if m["pin1"]:
                    ax.plot(*m["pin1"], "o", color="red", ms=7, zorder=7)
                if polar:
                    num, r = _role_pad(cv.model_roles(model) or {"1": "+"
                                       if fp.ref.startswith("C") else "K"})
                    for p in m["pads"]:
                        if p["number"] == num:
                            ax.annotate(r, p["center"], color="red",
                                        fontsize=11, ha="left", va="bottom",
                                        zorder=7, fontweight="bold")
            xs = [q[0] for p in b["pads"] for q in p["poly"]] or [fp.x]
            ys = [q[1] for p in b["pads"] for q in p["poly"]] or [fp.y]
            half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 1.0
            cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            ax.set_xlim(cx - half, cx + half)
            ax.set_ylim(cy + half, cy - half)  # board y points down
            ax.set_aspect("equal")
            ax.set_axis_on()
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title(title, fontsize=9)
        name = f"cpl_render_{start // per_image + 1}.png"
        fig.suptitle("red dot = LCSC pin 1 as JLC places it;  cyan = board "
                     "pad 1 / K / +", fontsize=9)
        fig.tight_layout()
        fig.savefig(out_dir / name, dpi=110)
        plt.close(fig)
        index["images"].append({"file": name, "refs": chunk})
    index["rate_limited"] = easyeda.rate_limited()
    if index["fetch_failed"]:
        index["note"] = ("fetch failed for " + ", ".join(index["fetch_failed"])
                         + (f" ({index['rate_limited']})"
                            if index["rate_limited"] else "")
                         + ": not a missing model; rerun in a few minutes")
    (out_dir / "index.json").write_text(json.dumps(index, indent=1),
                                        encoding="utf-8")
    return index


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--cpl", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--parts")
    ap.add_argument("--cache-dir")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--per-image", type=int, default=9)
    ap.add_argument("--out", help="write the index JSON here too")
    args = ap.parse_args(argv)
    try:
        pcb = Path(args.pcb)
        pj = Path(args.parts) if args.parts else None
        idx = render(pcb, cv.read_cpl(Path(args.cpl)), cv.lcsc_map(pcb, pj),
                     Path(args.cache_dir) if args.cache_dir
                     else cv.default_cache(pj), Path(args.out_dir),
                     fetch=not (args.offline or cv.offline()),
                     per_image=max(1, args.per_image))
        rep = {"script": "cpl_render",
               "status": "fetch_failed" if idx["fetch_failed"] else "pass",
               "out_dir": args.out_dir, **idx}
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        rep = {"script": "cpl_render", "status": "error",
               "error": f"{type(exc).__name__}: {exc}"}
    text = json.dumps(rep, indent=1)
    (Path(args.out).write_text(text, encoding="utf-8") if args.out
     else print(text))
    return {"pass": 0, "fetch_failed": 1}.get(rep["status"], 2)


if __name__ == "__main__":
    raise SystemExit(main())
