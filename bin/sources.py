"""hwde sources: the knowledge records' source PDFs, fetched on first use.

    sources.py fetch <dir>   download what's missing from ihsan-sa/hwde at the
                             commit docs/knowledge-sources.txt pins, check each
                             against its git blob sha and size; exit 0 once
                             <dir> holds every one, else 1
    sources.py check <dir>   exit 0 when <dir> holds every one (sizes only)

fetch writes <dir>/.complete once <dir> holds them all; bin/hwde takes a
.complete newer than the list as "here" without starting python.

The PDFs are the manufacturers' (NOTICE), so the module carries the list, not
the files: bin/hwde keeps them in HOME, as it keeps the venv. Standard library
only (it runs before the venv exists, with the host's python3).
"""
from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "docs" / "knowledge-sources.txt"
RAW = "https://raw.githubusercontent.com/ihsan-sa/hwde/{commit}/.claude/skills/hwde/reference/knowledge/sources/{name}"


def manifest() -> tuple[str, list[tuple[str, int, str]]]:
    commit, files = "", []
    for ln in MANIFEST.read_text().splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        a, b, *rest = ln.split()
        if a == "commit":
            commit = b
        else:
            files.append((a, int(b), rest[0]))
    return commit, files


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def missing(d: Path, files) -> list[tuple[str, int, str]]:
    return [f for f in files
            if not ((d / f[2]).is_file() and (d / f[2]).stat().st_size == f[1])]


def fetch(d: Path) -> int:
    commit, files = manifest()
    todo = missing(d, files)
    if not todo:
        (d / ".complete").write_text(commit + "\n")  # bin/hwde's quick test
        return 0
    d.mkdir(parents=True, exist_ok=True)
    print(f"hwde: fetching {len(todo)} knowledge source PDF(s) "
          f"({sum(f[1] for f in todo) // 1000000} MB) from ihsan-sa/hwde into {d}",
          file=sys.stderr)
    bad = []
    for sha, size, name in todo:
        try:
            with urllib.request.urlopen(RAW.format(commit=commit, name=name),
                                        timeout=60) as r:
                data = r.read()
        except OSError as e:
            bad.append(f"{name}: {e}")
            continue
        if len(data) != size or blob_sha(data) != sha:
            bad.append(f"{name}: got {len(data)} bytes, not the pinned blob {sha[:10]}")
            continue
        tmp = d / f".{name}.{os.getpid()}"
        tmp.write_bytes(data)
        tmp.replace(d / name)
    for b in bad:
        print(f"hwde: source not fetched: {b}", file=sys.stderr)
    if bad:
        return 1
    (d / ".complete").write_text(commit + "\n")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in ("fetch", "check"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    d = Path(argv[1])
    if argv[0] == "fetch":
        return fetch(d)
    return 1 if missing(d, manifest()[1]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
