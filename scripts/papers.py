#!/usr/bin/env python
"""papers.py - the research-to-checks pipeline: current EE research found,
read from legitimate copies, and turned into hwde tests, scripts, sims and
design guidelines only once hwde's own check agrees (docs/research/README.md).

    papers.py due                         topics whose scan interval has passed
    papers.py scan (--topic ID | --due) [--since YYYY-MM-DD] [--per N] [--add]
                                          search OpenAlex for each topic's
                                          queries; --add puts works the ledger
                                          hasn't seen in it as candidates and
                                          stamps the topic's last_scanned
    papers.py access ID [--write]         look again for an open copy:
                                          OpenAlex, then Unpaywall (with
                                          HWDE_RESEARCH_MAILTO), then Semantic
                                          Scholar; never a shadow library
    papers.py fetch ID                    download the first open copy that is
                                          a PDF into the cache (HWDE_PAPERS_CACHE,
                                          default ~/.cache/hwde-papers) with its
                                          text; a host's bot wall is recorded
                                          as blocked, not worked around
    papers.py triage ID (--keep | --drop) --why ".."
    papers.py queue                       what to do next: triage, read, test,
                                          and the person's paywalled list
    papers.py lint                        the ledger's promises (exit 1 on any)
    papers.py docs [--check]              write docs/research/{claims,
                                          guidelines,paywalled}.md (--check:
                                          exit 1 when they're stale)

Every command prints one JSON object. The ledger is reference/research/
papers.yaml; the topics are reference/research/watch.yaml. Claims are added
to the ledger by the reader (agents/paper-reader.md), by hand: a claim's
verdict is hwde's, reached by its own evidence, never the paper's word.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import paperslib as pl  # noqa: E402


def out(obj: dict, rc: int = 0) -> int:
    print(json.dumps(obj, indent=1))
    return rc


def load():
    return pl.load_yaml(pl.LEDGER), pl.load_yaml(pl.WATCH)


def save_ledger(ledger: dict) -> None:
    pl.dump_yaml(pl.LEDGER, ledger, pl.header_of(pl.LEDGER))


def save_watch(watch: dict) -> None:
    pl.dump_yaml(pl.WATCH, watch, pl.header_of(pl.WATCH))


def find(ledger: dict, pid: str) -> dict:
    for p in ledger.get("papers") or []:
        if p["id"] == pid:
            return p
    raise SystemExit(f"papers: no paper {pid}")


def cmd_scan(a) -> int:
    ledger, watch = load()
    topics = pl.due_topics(watch) if a.due else \
        [t for t in watch.get("topics") or [] if t["id"] == a.topic]
    if not topics:
        return out({"script": "papers", "cmd": "scan", "status": "pass",
                    "note": "no topic due" if a.due else f"no topic {a.topic}"},
                   0 if a.due else 2)
    report, errors = [], []
    for t in topics:
        since = a.since or t.get("since")
        found = []
        for q in t["queries"]:
            try:
                d = pl.fetch_json(pl.openalex_search_url(q, since, a.per))
            except (urllib.error.URLError, OSError, ValueError) as exc:
                errors.append(f"{t['id']}: {q}: {exc}")
                continue
            found += [pl.from_openalex(w) for w in d.get("results") or []]
        added = pl.merge_candidates(ledger, found, t["id"]) if a.add else []
        if a.add and not any(e.startswith(t["id"] + ":") for e in errors):
            t["last_scanned"] = pl.today()
        report.append({"topic": t["id"], "found": len(found),
                       "added": [p["id"] for p in added],
                       "seen": [] if a.add else
                       [{"id": p["id"], "title": p["title"], "year": p.get("year"),
                         "access": p["access"]["state"]} for p in found]})
    if a.add:
        save_ledger(ledger)
        save_watch(watch)
    return out({"script": "papers", "cmd": "scan",
                "status": "error" if errors and not any(r["found"] for r in report) else "pass",
                "topics": report, "errors": errors}, 1 if errors else 0)


def cmd_access(a) -> int:
    ledger, _ = load()
    p = find(ledger, a.id)
    copies, tried = [], []
    if p.get("openalex"):
        tried.append("openalex")
        try:
            copies += pl.open_copies(pl.fetch_json(f"{pl.OPENALEX}/works/{p['openalex']}"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            tried[-1] += f" ({exc})"
    email = os.environ.get("HWDE_RESEARCH_MAILTO")
    if not copies and p.get("doi") and email:
        tried.append("unpaywall")
        try:
            copies += pl.unpaywall_copies(p["doi"], email)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            tried[-1] += f" ({exc})"
    if not copies and p.get("doi"):
        tried.append("semanticscholar")
        try:
            copies += pl.s2_copies(p["doi"])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            tried[-1] += f" ({exc})"
    state = "open" if copies else "paywalled"
    if a.write and (p.get("access") or {}).get("state") != "person_supplied":
        p["access"] = {"state": state, "copies": copies[:3]}
        save_ledger(ledger)
    return out({"script": "papers", "cmd": "access", "status": "pass",
                "id": p["id"], "state": state, "copies": copies, "tried": tried})


def cmd_fetch(a) -> int:
    """Each open copy in turn until one is a PDF; the text goes beside it
    (pdftotext) for the reader. Copies stay in the cache, not the repo."""
    ledger, _ = load()
    p = find(ledger, a.id)
    acc = p.get("access") or {}
    if acc.get("state") not in ("open",):
        return out({"script": "papers", "cmd": "fetch", "status": "fail",
                    "id": p["id"], "note": f"access is {acc.get('state')}: "
                    "nothing hwde may fetch (a person_supplied copy is read where the person put it)"}, 1)
    dest = pl.cache_dir() / f"{p['id']}.pdf"
    tries = []
    for c in acc.get("copies") or []:
        r = pl.fetch_copy(c["url"], dest)
        tries.append(r)
        if r["ok"]:
            break
    got = next((r for r in tries if r["ok"]), None)
    if got:
        acc.pop("blocked", None)
        acc["read"] = {"url": got["url"], "sha256": got["sha256"]}
        txt = dest.with_suffix(".txt")
        try:
            subprocess.run(["pdftotext", "-layout", str(dest), str(txt)],
                           check=True, capture_output=True, timeout=120)
            got["text"] = str(txt)
        except (OSError, subprocess.SubprocessError) as exc:
            got["text_error"] = str(exc)
    else:
        acc["blocked"] = sorted({r["blocked"] for r in tries}) or ["no copies"]
    p["access"] = acc
    save_ledger(ledger)
    return out({"script": "papers", "cmd": "fetch",
                "status": "pass" if got else "fail", "id": p["id"],
                "copy": got, "tries": tries}, 0 if got else 1)


def cmd_triage(a) -> int:
    ledger, _ = load()
    p = find(ledger, a.id)
    p["status"] = "kept" if a.keep else "dropped"
    p["why"] = a.why
    if a.drop:
        p.pop("abstract", None)  # a dropped paper keeps only what dedups it
    save_ledger(ledger)
    return out({"script": "papers", "cmd": "triage", "status": "pass",
                "id": p["id"], "now": p["status"]})


def cmd_queue(a) -> int:
    ledger, _ = load()
    return out({"script": "papers", "cmd": "queue", "status": "pass",
                **pl.queue(ledger)})


def cmd_lint(a) -> int:
    ledger, watch = load()
    probs = pl.lint(ledger, watch)
    return out({"script": "papers", "cmd": "lint",
                "status": "fail" if probs else "pass", "problems": probs},
               1 if probs else 0)


DOC_PAGES = {"claims.md": pl.claims_md, "guidelines.md": pl.guidelines_md,
             "paywalled.md": pl.paywalled_md}


def cmd_docs(a) -> int:
    ledger, _ = load()
    stale = []
    for name, fn in DOC_PAGES.items():
        f = pl.DOCS / name
        text = fn(ledger)
        if (f.read_text() if f.exists() else None) != text:
            stale.append(name)
            if not a.check:
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(text)
    rc = 1 if a.check and stale else 0
    return out({"script": "papers", "cmd": "docs",
                "status": "fail" if rc else "pass",
                ("stale" if a.check else "written"): stale}, rc)


def cmd_due(a) -> int:
    _, watch = load()
    return out({"script": "papers", "cmd": "due", "status": "pass",
                "due": [t["id"] for t in pl.due_topics(watch)]})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--topic")
    g.add_argument("--due", action="store_true")
    s.add_argument("--since")
    s.add_argument("--per", type=int, default=8)
    s.add_argument("--add", action="store_true")
    s = sub.add_parser("access")
    s.add_argument("id")
    s.add_argument("--write", action="store_true")
    s = sub.add_parser("fetch")
    s.add_argument("id")
    s = sub.add_parser("triage")
    s.add_argument("id")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--keep", action="store_true")
    g.add_argument("--drop", action="store_true")
    s.add_argument("--why", required=True)
    sub.add_parser("queue")
    sub.add_parser("lint")
    sub.add_parser("due")
    s = sub.add_parser("docs")
    s.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    return {"scan": cmd_scan, "access": cmd_access, "triage": cmd_triage, "fetch": cmd_fetch,
            "queue": cmd_queue, "lint": cmd_lint, "docs": cmd_docs,
            "due": cmd_due}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
