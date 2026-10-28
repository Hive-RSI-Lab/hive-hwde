#!/usr/bin/env python
"""datasheet_extract.py - datasheet PDF -> schema-validated part JSON (SPEC.md P3, 6.1).

The actual field extraction is LLM-assisted (a datasheet-extractor sub-agent
reads the PDF pages); this script owns the deterministic half:

  --pdf FILE        pull machine-readable text per page (pypdf) and emit a
                    grounding payload {text_by_page, schema, template} for the
                    agent to fill. Refuses non-PDF bytes (exit 2): the
                    www.lcsc.com datasheet URLs serve an HTML viewer shell and
                    an HTML "pinout" must never reach the extractor. Pages with
                    no schema-relevant keywords are emitted as stubs
                    {page, first_line, chars} to keep the grounding payload
                    proportional to schema needs, not datasheet length
                    (--full-text restores them). Image-only datasheets yield
                    little/no text - the agent then reads the rendered pages
                    directly.
  --validate FILE   validate a candidate extraction against the schema.
                    exit 0 = valid, 1 = schema violations, 2 = unreadable.
  --schema          print the JSON Schema (single source of truth).
  --app-note FILE   U4 variant: same PDF handling, but the grounding payload
                    carries the KNOWLEDGE RECORD schema + template
                    (lib/knowledgelib.py) and layout-rule keywords - the
                    extractor fills reference/knowledge/records/*.yaml (one
                    record per class-level rule, sources citing this PDF by
                    page), stores the PDF under reference/knowledge/sources/,
                    then lints with `knowledge.py --validate`.
  --screen WS       U9 P3 layout screen (lib/layoutimpl.py): per part in
                    WS/parts/parts.json, the layout implications computed from
                    its extraction - thermal-via capacity of the exposed pad,
                    wire-entry direction + the rotation per board edge,
                    courtyard area vs the board budget (--board-mm, else the
                    state's stated size), pad pitch vs the routing floor - and
                    the CONFLICTS with architecture/constraints.json
                    (thermal min_vias over capacity, an edge rot that points
                    a wire entry inward, courtyards over budget). Writes
                    WS/parts/layout_implications.json; exit 1 on a conflict.
  --implications FILE [FILE ...]
                    the same fields for candidate extractions (part-sourcer),
                    ranked by layoutimpl.score against --needs JSON
                    {min_thermal_vias, edge, max_courtyard_mm2,
                    min_stub_width_mm}; exit 0.

The extracted JSON is the ground truth the schematic agents wire against
(SPEC P4: "never wire from model memory of a pinout") and the land pattern
fp_verify.py checks footprints against.

Contract (SPEC section 6): argparse, JSON to stdout or --out, exit 0/1/2.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# JSON Schema (Draft 2020-12) for a datasheet-extract JSON document.
PIN_TYPES = ["power_in", "power_out", "ground", "input", "output",
             "bidirectional", "passive", "analog", "clock", "nc",
             "open_collector", "tristate", "unspecified"]

DATASHEET_SCHEMA: dict = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "hwde datasheet extraction",
    "type": "object",
    "required": ["mpn", "pinout"],
    "additionalProperties": False,
    "properties": {
        "mpn": {"type": "string", "minLength": 1},
        "lcsc": {"type": "string"},
        "package": {"type": "string"},
        "description": {"type": "string"},
        "pinout": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["pin", "name", "type"],
                "additionalProperties": False,
                "properties": {
                    "pin": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "type": {"type": "string", "enum": PIN_TYPES},
                    "notes": {"type": "string"},
                },
            },
        },
        "decoupling": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["pins", "value"],
                "additionalProperties": False,
                "properties": {
                    "pins": {"type": "array", "items": {"type": "string"},
                             "minItems": 1},
                    "value": {"type": "string", "minLength": 1},
                    "placement": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
        },
        "land_pattern": {
            "type": "object",
            "additionalProperties": False,
            "required": ["pad_count"],
            "properties": {
                "package": {"type": "string"},
                "pad_count": {"type": "integer", "minimum": 1},
                "pitch_mm": {"type": "number", "exclusiveMinimum": 0},
                "pad_size_mm": {
                    "type": "array", "items": {"type": "number", "minimum": 0},
                    "minItems": 2, "maxItems": 2,
                },
                "row_spacing_mm": {"type": "number", "minimum": 0},
                "drill_mm": {"type": "number", "exclusiveMinimum": 0},
                "annulus_mm": {"type": "number", "exclusiveMinimum": 0},
                "courtyard_excess_mm": {"type": "number", "minimum": 0},
                # U9 layout-implication inputs
                "courtyard_mm": {
                    "type": "array", "items": {"type": "number",
                                               "exclusiveMinimum": 0},
                    "minItems": 2, "maxItems": 2,
                },
                "wire_entry_local": {"type": "string",
                                     "enum": ["+X", "-X", "+Y", "-Y"],
                                     "description": "connector wire/cable "
                                     "entry direction in footprint-local "
                                     "coords (KiCad y down)"},
                "pin1": {"type": "string",
                         "description": "pad number that is pin 1 (default '1')"},
                "notes": {"type": "string"},
            },
        },
        "exposed_pad": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "present": {"type": "boolean"},
                "connect_to": {"type": "string"},
                "thermal_vias": {"type": "integer", "minimum": 0},
                # U9: the recommended EP LAND (w x h) - via capacity needs it
                "size_mm": {
                    "type": "array", "items": {"type": "number",
                                               "exclusiveMinimum": 0},
                    "minItems": 2, "maxItems": 2,
                },
            },
            "required": ["present"],
        },
        "layout_notes": {"type": "array", "items": {"type": "string"}},
        "abs_max": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["param", "value"],
                "additionalProperties": False,
                "properties": {
                    "param": {"type": "string"},
                    "value": {"type": ["number", "string"]},
                    "unit": {"type": "string"},
                },
            },
        },
        # check_ratings: per-pin ratings, read before the free-text abs_max
        # rows (which it parses heuristically). min/max: volts or amps, or
        # "<PIN>+0.3" relative to that pin's net; ref: rated against that
        # pin's net instead of ground (VGS: pins [G], ref S).
        "pin_ratings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["pins"],
                "additionalProperties": False,
                "properties": {
                    "pins": {"type": "array", "items": {"type": "string"},
                             "minItems": 1},
                    "kind": {"type": "string",
                             "enum": ["voltage", "current"]},
                    "level": {"type": "string",
                              "enum": ["abs_max", "recommended"]},
                    "min": {"type": ["number", "string"]},
                    "max": {"type": ["number", "string"]},
                    "ref": {"type": "string"},
                    "source": {"type": "string"},
                },
            },
        },
        "source_pdf": {"type": "string"},
        # U9: computed by --screen/--implications, never hand-filled
        "layout_implications": {"type": "object"},
    },
}


def blank_template() -> dict:
    """A minimal, schema-shaped skeleton for the extractor agent to fill."""
    return {
        "mpn": "",
        "lcsc": "",
        "package": "",
        "pinout": [{"pin": "1", "name": "", "type": "unspecified"}],
        "decoupling": [],
        "land_pattern": {"pad_count": 0},
        "layout_notes": [],
        "abs_max": [],
    }


class MissingModule(RuntimeError):
    """A locked dependency is absent from this venv (exit 2, named remedy)."""


def extract_pdf_text(pdf_path: Path) -> list[dict]:
    """Per-page text via pypdf. Never raises on a page - records the error instead."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise MissingModule(
            f"python module '{exc.name or 'pypdf'}' is not installed in "
            f"{sys.executable}; run check_env.py (it compares the venv with "
            f"requirements.lock) and install with: {sys.executable} -m pip "
            "install -r requirements.lock") from exc
    reader = PdfReader(str(pdf_path))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - malformed pages shouldn't abort
            txt = ""
            pages.append({"page": i, "text": "", "error": f"{type(exc).__name__}: {exc}"})
            continue
        pages.append({"page": i, "text": txt})
    return pages


# Pages whose text carries none of these are stubbed out of the grounding
# payload: extractor context must scale with SCHEMA needs, not datasheet
# length (measured: 85-97 KB groundings dominated by electrical-characteristics
# and application pages the schema never reads).
GROUNDING_KEYWORDS = ("pin", "function", "land pattern", "recommended",
                      "absolute maximum", "layout", "decoupl", "bypass",
                      "thermal pad", "exposed pad", "package outline", "solder")

# --app-note: pages carrying none of these are stubbed - app notes are
# layout-rule sources, so the payload scales with RULE content, not page count.
APP_NOTE_KEYWORDS = ("layout", "loop", "ground", "plane", "via", "place",
                     "rout", "emi", "noise", "thermal", "copper", "trace",
                     "inductor", "capacitor", "switch", "snubber", "ring",
                     "feedback", "shield", "current path")


def _trim_pages(pages: list[dict],
                keywords: tuple = GROUNDING_KEYWORDS) -> tuple[list[dict], int]:
    """Full text only for schema-relevant pages; stubs for the rest."""
    out, trimmed = [], 0
    for p in pages:
        text = p.get("text", "")
        low = text.lower()
        if p.get("error") or any(k in low for k in keywords):
            out.append(p)
            continue
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        out.append({"page": p["page"], "first_line": first[:120],
                    "chars": len(text)})
        trimmed += 1
    return out, trimmed


def _checked_pdf(path: str) -> tuple[Path | None, dict | None]:
    """(path, None) for a real PDF; (None, error payload) otherwise."""
    pdf = Path(path)
    if not pdf.exists():
        return None, {"script": "datasheet_extract", "status": "error",
                      "error": f"PDF not found: {pdf}"}
    with pdf.open("rb") as fh:
        head = fh.read(16)
    if not head.startswith(b"%PDF"):
        # An HTML shell would otherwise be text-extracted into a "pinout" -
        # and the extraction is the ONLY pinout source P4 may wire from.
        return None, {"script": "datasheet_extract", "status": "error",
                      "error": (f"{pdf} is not a PDF (starts {head!r}); "
                                "www.lcsc.com/datasheet URLs serve an HTML "
                                "viewer shell - re-download from the "
                                "wmsc.lcsc.com mirror (parts_search emits the "
                                "wmsc form) with a browser User-Agent")}
    return pdf, None


def do_pdf(args) -> tuple[dict, int]:
    pdf, err = _checked_pdf(args.pdf)
    if err:
        return err, 2
    pages = extract_pdf_text(pdf)
    total_chars = sum(len(p.get("text", "")) for p in pages)
    trimmed = 0
    if not args.full_text:
        pages, trimmed = _trim_pages(pages)
    payload = {
        "script": "datasheet_extract",
        "status": "extracted",
        "source_pdf": str(pdf),
        "lcsc": args.lcsc or "",
        "n_pages": len(pages),
        "text_chars": total_chars,
        "pages_trimmed": trimmed,
        "text_by_page": pages,
        "schema": DATASHEET_SCHEMA,
        "template": blank_template(),
        "note": ("Fill the template from text_by_page and the PDF pages, then "
                 "re-run with --validate. Pages without schema-relevant "
                 "keywords are stubs {page, first_line, chars} - read those "
                 "PDF pages directly if needed, or re-run with --full-text. "
                 "If text_chars is ~0 the PDF is image-only; read the "
                 "rendered pages directly."),
    }
    return payload, 0


def do_app_note(args) -> tuple[dict, int]:
    """--app-note: grounding payload shaped for KNOWLEDGE RECORDS (U4)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import knowledgelib
    pdf, err = _checked_pdf(args.app_note)
    if err:
        return err, 2
    pages = extract_pdf_text(pdf)
    total_chars = sum(len(p.get("text", "")) for p in pages)
    trimmed = 0
    if not args.full_text:
        pages, trimmed = _trim_pages(pages, APP_NOTE_KEYWORDS)
    payload = {
        "script": "datasheet_extract",
        "status": "extracted",
        "mode": "app_note",
        "source_pdf": str(pdf),
        "n_pages": len(pages),
        "text_chars": total_chars,
        "pages_trimmed": trimmed,
        "text_by_page": pages,
        "record_schema": knowledgelib.RECORD_SCHEMA,
        "record_template": knowledgelib.blank_record(),
        "classes": sorted(knowledgelib.CLASSES),
        "note": ("Write ONE reference/knowledge/records/<id>.yaml per "
                 "class-level layout rule found (record_template shape; "
                 "classes from the controlled list; page numbers here are "
                 "0-based - cite sources 1-based). Store the PDF under "
                 "reference/knowledge/sources/ and cite it by page. Then "
                 "lint: knowledge.py --validate. Stubbed pages "
                 "{page, first_line, chars} carried no rule keywords - read "
                 "those PDF pages directly if needed, or --full-text."),
    }
    return payload, 0


def _layoutimpl():
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import layoutimpl
    return layoutimpl


def _board_mm(args, ws: Path) -> tuple[float, float] | None:
    if args.board_mm:
        w, h = args.board_mm.lower().split("x")
        return float(w), float(h)
    st = ws / "state.json"
    if st.is_file():
        size = (json.loads(st.read_text(encoding="utf-8")).get("mode")
                or {}).get("stated_size")
        if size:
            return float(size[0]), float(size[1])
    return None


def do_screen(args) -> tuple[dict, int]:
    li = _layoutimpl()
    ws = Path(args.screen)
    res = li.screen(ws, _board_mm(args, ws), args.cap_class)
    out = (ws / "parts" if (ws / "parts").is_dir() else ws / "kicad") \
        / "layout_implications.json"
    out.write_text(json.dumps(res, indent=1, ensure_ascii=True),
                   encoding="utf-8")
    payload = {"script": "datasheet_extract", "mode": "screen",
               "status": "violations" if res["conflicts"] else "pass",
               "written": out.as_posix(), **res}
    return payload, 1 if res["conflicts"] else 0


def do_implications(args) -> tuple[dict, int]:
    li = _layoutimpl()
    needs = json.loads(args.needs) if args.needs else {}
    cap = li.fab_row(args.cap_class)
    ranked = []
    for f in args.implications:
        ext = json.loads(Path(f).read_text(encoding="utf-8"))
        imp = li.implications(ext, None, cap)
        ranked.append({"file": f, **imp, **li.score(imp, needs)})
    ranked.sort(key=lambda r: -r["score"])
    return {"script": "datasheet_extract", "mode": "implications",
            "status": "pass", "needs": needs, "candidates": ranked}, 0


def do_validate(args) -> tuple[dict, int]:
    import jsonschema
    path = Path(args.validate)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {"script": "datasheet_extract", "status": "error",
                "error": f"cannot read {path}: {exc}"}, 2
    except json.JSONDecodeError as exc:
        return {"script": "datasheet_extract", "status": "error",
                "error": f"{path} is not valid JSON: {exc}"}, 2
    validator = jsonschema.Draft202012Validator(DATASHEET_SCHEMA)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if not errors:
        return {"script": "datasheet_extract", "status": "pass",
                "file": str(path), "mpn": data.get("mpn"),
                "pins": len(data.get("pinout", [])),
                "has_land_pattern": "land_pattern" in data}, 0
    return {
        "script": "datasheet_extract",
        "status": "invalid",
        "file": str(path),
        "error_count": len(errors),
        "errors": [{"path": "/".join(str(p) for p in e.path), "msg": e.message}
                   for e in errors],
    }, 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pdf", help="extract text + emit grounding payload")
    mode.add_argument("--validate", help="validate a candidate extraction JSON")
    mode.add_argument("--schema", action="store_true", help="print the JSON schema")
    mode.add_argument("--app-note", help="extract text + emit a KNOWLEDGE "
                                         "RECORD grounding payload (U4)")
    mode.add_argument("--screen", metavar="WS",
                      help="U9 P3 layout screen over a workspace")
    mode.add_argument("--implications", nargs="+", metavar="FILE",
                      help="U9 layout implications of candidate "
                           "extractions, ranked against --needs")
    ap.add_argument("--board-mm", help="--screen: board WxH in mm, e.g. "
                                       "50x40 (default: state mode size)")
    ap.add_argument("--cap-class", default="2layer_1oz",
                    help="jlc_capabilities design_rules row")
    ap.add_argument("--needs", help="--implications: JSON needs object")
    ap.add_argument("--lcsc", help="tag the output with this LCSC id (--pdf)")
    ap.add_argument("--full-text", action="store_true",
                    help="--pdf: emit full text for EVERY page (no stubbing)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        if args.schema:
            payload, code = {"script": "datasheet_extract", "status": "pass",
                             "schema": DATASHEET_SCHEMA}, 0
        elif args.pdf:
            payload, code = do_pdf(args)
        elif args.app_note:
            payload, code = do_app_note(args)
        elif args.screen:
            payload, code = do_screen(args)
        elif args.implications:
            payload, code = do_implications(args)
        else:
            payload, code = do_validate(args)
    except MissingModule as exc:
        payload, code = {"script": "datasheet_extract", "status": "error",
                         "error": "missing-module: " + str(exc)}, 2
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        payload, code = {"script": "datasheet_extract", "status": "error",
                         "error": f"{type(exc).__name__}: {exc}"}, 2

    text = json.dumps(payload, indent=1, ensure_ascii=True)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
