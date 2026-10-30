#!/usr/bin/env python
"""fabhash.py - a DESIGN fingerprint for a fab package (T1).

The gerber zip's sha256 is not a design fingerprint. KiCad stamps a creation
timestamp into every gerber/drill header, so re-exporting an UNCHANGED board
produces a different zip: measured on lumina-carrier, two exports 55 minutes
apart differed in exactly one line -
`; DRILL file KiCad 10.0.3 date 2026-07-30T07:07:03` - and every copper layer
was byte-identical, yet the sha went 3afe6590.. -> 4e192c7e..
(LEARNINGS 2026-07-30 [fab_export][order_submit][jlcapi]).

Consequences that made this module necessary:
  * order_submit's create latch bound the order to the zip sha, so a harmless
    re-export invalidated an approved quote and forced a re-upload + re-audit;
  * "the sha changed" was being read as "the design changed", which it is not.

`design_hash(zip)` hashes the package with the volatile header lines removed:
same design re-exported -> same hash; one moved track -> different hash. The
file sha256 stays alongside it and keeps its own (narrower) meaning: "is this
the exact file I quoted".

Volatile content removed (all live-verified forms from KiCad 10.0.3 output):
  gerber   %TF.CreationDate,..*%   %TF.GenerationSoftware,..*%
           G04 Created by KiCad (PCBNEW ..) date ..*
  drill    ; DRILL file KiCad .. date ..
           ; #@! TF.CreationDate,..   ; #@! TF.GenerationSoftware,..
  .gbrjob  Header.CreationDate + Header.GenerationSoftware (JSON-aware; the
           software block spans lines, so a line filter cannot see it)

Everything else - apertures, coordinates, drill sizes, ProjectId, design-rule
attributes - is hashed. A non-zip file (or an unreadable member) falls back to
the raw bytes, which is fail-safe: the hash then only matches an identical
file.

CLI (debugging aid: "did the design actually change?"):
  fabhash.py --zip a.zip [--compare b.zip]   -> JSON, exit 0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

# Line-oriented volatile headers (gerber + Excellon).
VOLATILE_LINE = re.compile(
    r"^\s*(?:"
    r"%TF\.(?:CreationDate|GenerationSoftware)\b"            # gerber X2 attrs
    r"|G04\s+Created\s+by\b"                                 # KiCad banner
    r"|;\s*DRILL\s+file\b"                                   # Excellon banner
    r"|;\s*#@!\s*TF\.(?:CreationDate|GenerationSoftware)\b"   # Excellon X2 attrs
    r"|G04\s+#@!\s*TF\.(?:CreationDate|GenerationSoftware)\b"
    r")", re.IGNORECASE)

# JSON keys dropped from .gbrjob (recursively).
VOLATILE_JSON_KEYS = {"CreationDate", "GenerationSoftware"}


def strip_volatile_json(obj):
    """Recursively drop volatile keys from a parsed .gbrjob."""
    if isinstance(obj, dict):
        return {k: strip_volatile_json(v) for k, v in obj.items()
                if k not in VOLATILE_JSON_KEYS}
    if isinstance(obj, list):
        return [strip_volatile_json(v) for v in obj]
    return obj


def normalize_member(name: str, data: bytes) -> bytes:
    """Design-relevant bytes of one package member."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError:              # not text: hash as-is
            return data
    if name.lower().endswith(".gbrjob"):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError:
            pass                                # fall through to line filter
        else:
            return json.dumps(strip_volatile_json(doc), sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
    kept = [ln.rstrip("\r") for ln in text.splitlines()
            if not VOLATILE_LINE.match(ln)]
    return "\n".join(kept).encode("utf-8")


def design_hash(path: Path | str) -> str:
    """sha256 over the package's design content, volatile headers removed.

    Members are hashed in NAME order with their names, so adding, removing or
    renaming a layer changes the hash. A file that is not a readable zip
    hashes its raw bytes (fail-safe: only an identical file matches)."""
    p = Path(path)
    h = hashlib.sha256()
    try:
        zf = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError):
        h.update(b"raw\0")
        h.update(p.read_bytes())
        return h.hexdigest()
    with zf:
        h.update(b"pkg\0")
        for name in sorted(zf.namelist()):
            info = zf.getinfo(name)
            if info.is_dir():
                continue
            h.update(name.encode("utf-8"))
            h.update(b"\0")
            h.update(normalize_member(name, zf.read(name)))
            h.update(b"\0")
    return h.hexdigest()


def file_sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zip", required=True, help="fab package (gerber zip)")
    ap.add_argument("--compare", help="second package to compare against")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        payload = {"script": "fabhash", "status": "pass",
                   "zip": args.zip,
                   "file_sha256": file_sha256(args.zip),
                   "design_sha256": design_hash(args.zip)}
        if args.compare:
            payload["compare"] = args.compare
            payload["compare_file_sha256"] = file_sha256(args.compare)
            payload["compare_design_sha256"] = design_hash(args.compare)
            payload["same_file"] = (payload["file_sha256"]
                                    == payload["compare_file_sha256"])
            payload["same_design"] = (payload["design_sha256"]
                                      == payload["compare_design_sha256"])
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        payload = {"script": "fabhash", "status": "error",
                   "error": f"{type(exc).__name__}: {exc}"}
        text = json.dumps(payload, indent=1)
        (Path(args.out).write_text(text, encoding="utf-8") if args.out
         else print(text))
        return 2

    text = json.dumps(payload, indent=1)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
