#!/usr/bin/env python
"""guide_facts.py - one JSON of the facts a board's owner guide needs.

The board-guide agent (agents/board-guide.md) writes a short owner-facing PDF
with the pdf-material-builder skill: what the board does, its schematic, how
to use it, the BOM with LCSC numbers and Basic/Extended status, the cost
estimate and how to order it at JLCPCB. This script gathers those facts from
the workspace so the agent works from ONE file and never opens a design file.

It reads, all read-only:
  state.json                    board name, phase, gate verdicts, decisions
  fab/<board>_gerbers.zip       the upload zip          (fab_export.py)
  fab/BOM.csv, fab/CPL.csv      the JLC upload pair     (bom_cpl.py)
  fab/BOM-full.csv              the BOM of record       (bom_cpl.py)
  fab/prebuy.csv                Extended parts to pre-buy for the build
                                (bom_cpl.py); absent -> a `todo`
  kicad/parts.json              LCSC / MPN / basic flag / unit price
  reports/bom_cpl.json          rotation corrections to eyeball in JLC preview
  fab/quote.json                estimated cost matrix   (order_quote.py)
  reports/schematic.pdf         the schematic export    (kc.py sch-pdf)
  reports/cost.json             model cost of generating the board, total
                                and by hwde step        (gen_cost.py)
  requirements.md, architecture/*.md, reports/*_top.png  prose + renders
  ../register.yaml              the board's part number PCB-NNNN-R, if the
                                boards register lists it (lib/boardreg.py);
                                `part_number` is null otherwise and
                                `part_number_reason` says why
With --render it also writes, through render.py (the path that shows the
parts), fresh reports/<board>_top.png and _bottom.png before gathering: the
guide's board picture must never be a render older than the parts on it.
Without --render a `todo` asks for it.
Paths resolve through reference/invalidation.yaml artifact_kinds (plus the
workspace's registry overrides), the same way the gates find them.

Exit 0 "pass"       every required fact is present.
Exit 1 "violations" the fab package is incomplete (no zip / BOM / CPL / board
                    name) - the guide cannot be written yet; `missing` says what.
                    With --render, a failed render is one too.
                    A missing quote, schematic PDF, pre-buy list, generation
                    cost or fresh render is a `todo` with the command that
                    makes it, not a violation.
Exit 2 "error"      no workspace / unreadable state.json or parts.json.

CLI:
  guide_facts.py --workspace ~/dev/boards/<name> [--render] [--out facts.json]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib import boardreg, statelib  # noqa: E402

GATES_SHOWN = ("erc", "place", "drc_routed", "verify", "sim", "dfm")


class FactsError(RuntimeError):
    """Unusable workspace (exit 2)."""


def _rel(ws: Path, p: Path) -> str:
    try:
        return p.relative_to(ws).as_posix()
    except ValueError:
        return p.as_posix()


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FactsError(f"{what} unreadable: {path}: {exc}") from exc


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _part_status(ent: dict) -> str:
    if "basic" in ent:
        return "basic" if ent["basic"] else "extended"
    t = str(ent.get("type", "")).strip().lower()
    if t in ("basic", "preferred", "extended"):
        return "extended" if t == "extended" else "basic"
    return "unknown"


def load_parts_index(path: Path) -> dict[str, dict]:
    """LCSC -> {mpn, status, unit_price, description}. Tolerates the S6
    per-distinct shape ({"parts": [...]}) and a bare list."""
    if not path.is_file():
        return {}
    data = _read_json(path, "parts.json")
    items = data.get("parts", []) if isinstance(data, dict) else data
    out: dict[str, dict] = {}
    for ent in items if isinstance(items, list) else []:
        if not isinstance(ent, dict):
            continue
        lcsc = str(ent.get("lcsc") or ent.get("LCSC") or "").strip()
        if not lcsc or lcsc in out:
            continue
        price = ent.get("price")
        out[lcsc] = {
            "mpn": ent.get("mpn") or ent.get("mfr_part"),
            "status": _part_status(ent),
            "unit_price": float(price) if isinstance(price, (int, float)) else None,
            "description": ent.get("description") or ent.get("block"),
        }
    return out


def _qty(row: dict) -> int:
    q = str(row.get("Qty Per Board") or "").strip()
    if q.isdigit():
        return int(q)
    return len([d for d in str(row.get("Designator", "")).split(",") if d.strip()])


def bom_lines(bom_full: Path, bom: Path, parts: dict[str, dict]) -> list[dict]:
    """One line per BOM row, BOM-full.csv preferred (it carries every class
    and a quantity), else the JLC upload BOM.csv."""
    src = bom_full if bom_full.is_file() else bom
    lines = []
    for row in _read_csv(src):
        lcsc = str(row.get("LCSC") or row.get("LCSC Part #") or "").strip()
        info = parts.get(lcsc, {})
        lines.append({
            "designators": row.get("Designator", ""),
            "value": row.get("Comment", ""),
            "footprint": row.get("Footprint", ""),
            "qty_per_board": _qty(row),
            "lcsc": lcsc or None,
            "mpn": row.get("MPN") or info.get("mpn"),
            "assembly_class": row.get("Assembly Class") or "smt_placed",
            "jlc_status": info.get("status", "unknown") if lcsc else "not_lcsc",
            "unit_price": info.get("unit_price"),
        })
    return lines


def quote_summary(quote: dict) -> dict:
    rows = []
    for r in quote.get("matrix") or []:
        rows.append({"qty": r.get("qty"),
                     "surface_finish": r.get("surface_finish"),
                     "total": r.get("total"),
                     "unit_cost": r.get("unit_cost"),
                     "assembly_total": (r.get("assembly") or {}).get("total")})
    return {"estimated": bool(quote.get("estimated", True)),
            "currency": quote.get("currency", "USD"),
            "disclaimer": quote.get("disclaimer"),
            "authoritative_quote_url": quote.get("authoritative_quote_url"),
            "spec": quote.get("spec"),
            "lead_time": quote.get("lead_time"),
            "cheapest": quote.get("cheapest"),
            "matrix": rows}


def cost_summary(cost: dict) -> dict:
    """What the guide prints of reports/cost.json: the total, the per-step
    lines (with the reason when a round could not be split), the rounds shared
    with other work, and how the total reconciles with the recorded ones."""
    return {k: cost.get(k) for k in (
        "currency", "total_usd", "recorded_usd", "loop_logged_usd",
        "breakdown", "breakdown_reason", "by_step", "shared",
        "incomplete_sessions", "unpriced_tokens", "notes", "prices")} | {
        "timed_out_iterations": sum(
            (r.get("loop") or {}).get("unlogged_iterations", 0)
            for r in cost.get("rounds") or [] if not r.get("shared_with"))}


RENDER_VIEWS = ("top", "bottom")


def fresh_render(pcb: Path, reports: Path) -> dict:
    """Render the board's top and bottom through render.py into reports/."""
    import render  # sibling; imports kc -> kicad-cli only when rendering
    return render.render_views(pcb, list(RENDER_VIEWS), reports, width=1600,
                               height=900, quality="high")


def collect(ws: Path, do_render: bool = False) -> dict:
    state_path = ws / "state.json"
    if not state_path.is_file():
        raise FactsError(f"no state.json in {ws}")
    state = _read_json(state_path, "state.json")
    if not isinstance(state, dict):
        raise FactsError("state.json is not an object")
    board = state.get("board")
    imap = statelib.load_map()
    registry = state.get("artifacts") or {}

    def kind(k: str) -> Path:
        return ws / statelib.kind_path(k, board or "board", imap, registry, ws)

    missing: list[str] = []
    todo: list[dict] = []
    if not board:
        missing.append("state.json board name")

    pcb, sch = kind("pcb"), kind("sch")
    zip_p, bom_p, cpl_p = kind("gerbers"), kind("bom"), kind("cpl")
    bom_full_p = kind("bom_full")
    for label, p in (("gerber zip", zip_p), ("BOM.csv", bom_p),
                     ("CPL.csv", cpl_p)):
        if not p.is_file():
            missing.append(f"{label} ({_rel(ws, p)}) - run the dfm-check recipe")

    parts = load_parts_index(kind("parts"))
    lines = bom_lines(bom_full_p, bom_p, parts) if (
        bom_full_p.is_file() or bom_p.is_file()) else []
    placed = [ln for ln in lines if ln["assembly_class"] == "smt_placed"]
    counts = {s: len({ln["lcsc"] for ln in placed if ln["jlc_status"] == s})
              for s in ("basic", "extended", "unknown")}
    counts["not_lcsc"] = sum(1 for ln in placed if ln["jlc_status"] == "not_lcsc")
    priced = [ln for ln in placed if ln["unit_price"] is not None]
    parts_cost = round(sum(ln["unit_price"] * ln["qty_per_board"]
                           for ln in priced), 2) if priced else None

    rotations = []
    bc = ws / "reports" / "bom_cpl.json"
    if bc.is_file():
        rep = _read_json(bc, "bom_cpl.json")
        for a in rep.get("rotation_audit") or []:
            if isinstance(a, dict) and a.get("correction"):
                rotations.append({k: a.get(k) for k in
                                  ("ref", "package", "layer", "correction",
                                   "final_rot")
                                  if k in a})

    # The pre-buy list sits beside the upload BOM (bom_cpl writes both).
    prebuy_p = bom_p.parent / "prebuy.csv"
    prebuy = None
    if prebuy_p.is_file():
        rows = _read_csv(prebuy_p)
        prebuy = {"path": _rel(ws, prebuy_p),
                  "build_qty": int(rows[0]["Boards"]) if rows else None,
                  "note": rows[0].get("Note") if rows else None,
                  "rows": [{k: r.get(k) for k in
                            ("LCSC", "MPN", "Comment", "Designator",
                             "Qty Per Board", "Qty To Buy")} for r in rows]}
    elif bom_p.is_file():
        todo.append({"fact": "pre-buy list",
                     "cmd": f"scripts/bom_cpl.py --pcb {_rel(ws, pcb)} "
                            f"--parts {_rel(ws, kind('parts'))} "
                            f"--out-dir {_rel(ws, bom_p.parent)}"})

    quote_p = ws / "fab" / "quote.json"
    quote = None
    if quote_p.is_file():
        quote = quote_summary(_read_json(quote_p, "quote.json"))
    else:
        todo.append({"fact": "cost estimate",
                     "cmd": f"scripts/order_quote.py --pcb {_rel(ws, pcb)} "
                            f"--parts {_rel(ws, kind('parts'))} --assembly "
                            f"--out {_rel(ws, quote_p)}"})

    cost_p = ws / "reports" / "cost.json"
    cost = None
    if cost_p.is_file():
        cost = cost_summary(_read_json(cost_p, "cost.json"))
    else:
        todo.append({"fact": "generation cost",
                     "cmd": f"scripts/gen_cost.py --workspace {ws.as_posix()}"
                            f" --out {cost_p.as_posix()}"})

    sch_pdf = ws / "reports" / "schematic.pdf"
    if not sch_pdf.is_file():
        todo.append({"fact": "schematic export",
                     "cmd": f"scripts/kc.py sch-pdf {_rel(ws, sch)} "
                            f"--out {_rel(ws, sch_pdf)}"})

    rendered = None
    if do_render and pcb.is_file():
        try:
            r = fresh_render(pcb, ws / "reports")
        except Exception as exc:  # no kicad-cli, a timeout: still write facts
            missing.append(f"fresh render (render.py failed: {exc})"[:320])
        else:
            rendered = {"status": r["status"],
                        "views": {o["view"]: _rel(ws, Path(o["path"]))
                                  for o in r["outputs"]
                                  if o["status"] == "pass"},
                        "models_missing": r.get("models_missing", []),
                        "models_relinked": r.get("models_relinked", [])}
            if r["status"] != "pass":
                missing.append("fresh render (render.py failed: " + "; ".join(
                    o.get("stderr_tail") or o["view"] for o in r["outputs"]
                    if o["status"] != "pass")[:300] + ")")
    elif do_render:
        missing.append(f"board file ({_rel(ws, pcb)}) - nothing to render")
    else:
        todo.append({"fact": "fresh render",
                     "cmd": f"scripts/guide_facts.py --workspace {ws.as_posix()}"
                            " --render --out "
                            f"{(ws / 'reports' / 'guide_facts.json').as_posix()}"})

    renders = sorted(_rel(ws, p) for p in (ws / "reports").glob("*.png")
                     if p.stem.endswith(("_top", "_bottom", "_iso")))
    prose = [_rel(ws, p) for p in [ws / "requirements.md",
                                   *sorted((ws / "architecture").glob("*.md")),
                                   *sorted((ws / "brief").glob("*.md"))]
             if p.is_file()]
    gates = {g: (state.get("gates", {}).get(g) or {}).get("status")
             for g in GATES_SHOWN if g in (state.get("gates") or {})}
    decisions = [d.get("text") or d.get("decision")
                 for d in state.get("decisions") or [] if isinstance(d, dict)]

    pn, pn_why = boardreg.part_number(ws)

    return {
        "script": "guide_facts",
        "status": "violations" if missing else "pass",
        "board": board,
        "workspace": ws.as_posix(),
        "part_number": pn,
        "part_number_reason": pn_why or None,
        "phase": state.get("phase"),
        "mode": state.get("mode"),
        "gates": gates,
        "guide_pdf": _rel(ws, ws / "fab" / f"{board}-guide.pdf"),
        "paths": {
            "gerber_zip": _rel(ws, zip_p), "bom": _rel(ws, bom_p),
            "bom_full": _rel(ws, bom_full_p), "cpl": _rel(ws, cpl_p),
            "schematic_pdf": _rel(ws, sch_pdf) if sch_pdf.is_file() else None,
            "quote": _rel(ws, quote_p) if quote else None,
            "cost": _rel(ws, cost_p) if cost else None,
            "design_doc": next((_rel(ws, p) for p in
                                (ws / "reports" / "design_doc").glob("*.pdf")),
                               None),
            "renders": renders, "prose": prose,
            "top_render": (rendered or {}).get("views", {}).get("top"),
        },
        "bom": lines,
        "bom_counts": {"lines": len(lines), "placed_lines": len(placed),
                       "unique_parts": counts},
        "parts_cost_per_board": parts_cost,
        "rotation_corrections": rotations,
        "render": rendered,
        "prebuy": prebuy,
        "quote": quote,
        "generation_cost": cost,
        "decisions": [d for d in decisions if d][-12:],
        "missing": missing,
        "todo": todo,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="~/dev/boards/<name>")
    ap.add_argument("--render", action="store_true",
                    help="re-render the top/bottom views first (render.py)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)
    try:
        payload = collect(Path(args.workspace), do_render=args.render)
        code = 1 if payload["missing"] else 0
    except FactsError as exc:
        payload, code = {"script": "guide_facts", "status": "error",
                         "error": str(exc)}, 2
    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="ascii")
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
