#!/usr/bin/env python
"""errata_corpus.py - does hwde catch on rev A what the designer fixed in rev B?

The manifest is reference/errata_corpus.yaml: public boards whose next
revision fixed a defect the first one shipped with, each defect quoted from
the designer's errata. A defect with a `catch` names the hwde check and the
finding that is it; this script runs that check on rev A, where the finding
must be, and on rev B, where it must be gone (a check that also fires on the
fixed board isn't seeing the defect). The table goes to docs/errata-corpus.md.

    errata_corpus.py fetch [ID...]   sparse clones at the pinned commit
    errata_corpus.py run   [ID...]   run each catch on rev A and rev B
    errata_corpus.py table           write docs/errata-corpus.md

--cache DIR (default ~/.cache/hwde-errata-corpus, or HWDE_ERRATA_CACHE). Nothing
is fetched into this repo. Checks run here: netlist_audit (on the schematic,
with an empty constraints file: what hwde reads before anyone has written
one). A catch for another script is reported as not run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
MANIFEST = ROOT / "reference" / "errata_corpus.yaml"
DOC = ROOT / "docs" / "errata-corpus.md"
VERDICTS = {"caught", "caught_with_constraints", "missed", "not_static"}


def cache_root(arg: str | None) -> Path:
    c = Path(arg or os.environ.get("HWDE_ERRATA_CACHE")
             or Path.home() / ".cache" / "hwde-errata-corpus").resolve()
    if c == ROOT or ROOT in c.parents:
        raise SystemExit(f"cache {c} is inside the repo")
    return c


def load(path: Path = MANIFEST) -> list[dict]:
    boards = yaml.safe_load(path.read_text(encoding="utf-8"))["boards"]
    for b in boards:
        for d in b["defects"]:
            if d.get("verdict") not in VERDICTS:
                raise SystemExit(f"{d['id']}: verdict not one of {sorted(VERDICTS)}")
            if d["verdict"] == "caught" and not d.get("catch"):
                raise SystemExit(f"{d['id']}: caught with no catch")
            if d["verdict"] == "missed" and not d.get("follow_up"):
                raise SystemExit(f"{d['id']}: a miss needs a follow_up")
    return boards


def repo_dir(cache: Path, b: dict) -> Path:
    return cache / "repos" / f"{b['id']}@{b['commit'][:12]}"


def fetch(cache: Path, b: dict) -> None:
    dest = repo_dir(cache, b)
    dirs = sorted({str(Path(b[r]["sch"]).parent) for r in ("rev_a", "rev_b")})
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout",
                        b["url"], str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "sparse-checkout", "set", *dirs], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", b["commit"]], check=True)


def matches(violation: dict, catch: dict) -> bool:
    """The finding is the defect: same kind, and the net or one of the refs."""
    if violation.get("kind") != catch["kind"]:
        return False
    if catch.get("net") and violation.get("net") != catch["net"]:
        return False
    refs = set(catch.get("refs") or ())
    return not refs or bool(refs & set(violation.get("refs") or ()))


def audit(cache: Path, b: dict, rev: str) -> list[dict]:
    sch = repo_dir(cache, b) / b[rev]["sch"]
    out = cache / "runs" / b["id"] / f"{rev}-netlist_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    empty = out.parent / "constraints.json"
    empty.write_text("{}", encoding="utf-8")
    subprocess.run([sys.executable, str(SCRIPTS / "netlist_audit.py"), "--sch", str(sch),
                    "--constraints", str(empty), "--out", str(out)],
                   capture_output=True, text=True, timeout=900)
    return json.loads(out.read_text(encoding="utf-8")).get("violations", [])


def run(cache: Path, b: dict) -> dict:
    found = {rev: audit(cache, b, rev) for rev in ("rev_a", "rev_b")}
    res = {}
    for d in b["defects"]:
        c = d.get("catch")
        if not c:
            continue
        if c["script"] != "netlist_audit":
            res[d["id"]] = {"ran": False}
            continue
        res[d["id"]] = {"ran": True,
                        "rev_a": any(matches(v, c) for v in found["rev_a"]),
                        "rev_b": any(matches(v, c) for v in found["rev_b"])}
    (cache / "runs" / b["id"] / "result.json").write_text(json.dumps(res, indent=1))
    return res


def outcome(d: dict, r: dict | None) -> str:
    if not d.get("catch"):
        return d["verdict"]
    if not r or not r.get("ran"):
        return f"{d['verdict']} (not run here)"
    if r["rev_a"] and not r["rev_b"]:
        return "caught on rev A, gone on rev B"
    if r["rev_a"]:
        return "REGRESSED: fires on rev B too"
    return "REGRESSED: not found on rev A"


def table(cache: Path, boards: list[dict]) -> str:
    lines = ["# The real-errata corpus", "",
             "Public boards whose next revision fixed a defect the first one shipped with,",
             "quoted from the designer's own errata (`reference/errata_corpus.yaml`), and",
             "what hwde's checks do with the first revision. Written by",
             "`bin/hwde errata_corpus table` after `fetch` and `run`.", ""]
    count: dict[str, int] = {}
    for b in boards:
        rf = cache / "runs" / b["id"] / "result.json"
        res = json.loads(rf.read_text()) if rf.exists() else {}
        lines += [f"## {b['id']}: {b['rev_a']['name']} -> {b['rev_b']['name']}", "",
                  f"[{b['url']}]({b['url']}) at `{b['commit'][:12]}`, {b['licence']}; "
                  f"errata `{b['source']}`.", "",
                  "| defect | class | hwde on rev A | follow-up |", "|---|---|---|---|"]
        for d in b["defects"]:
            count[d["verdict"]] = count.get(d["verdict"], 0) + 1
            catch = d.get("catch")
            how = outcome(d, res.get(d["id"]))
            if catch:
                how += f" (`{catch['script']}` {catch['kind']})"
            lines.append(f"| {d['id']}: {d['summary']} | {d['fault_class']} | {how} | "
                         f"{d.get('follow_up') or d.get('note') or ''} |".replace("\n", " "))
        lines.append("")
    total = sum(count.values())
    lines += ["## Count", "", f"{total} defects: " + ", ".join(
        f"{v} {k}" for k, v in sorted(count.items())) + ".", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["fetch", "run", "table"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--cache")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--out", default=str(DOC))
    a = ap.parse_args(argv)
    cache = cache_root(a.cache)
    boards = [b for b in load(Path(a.manifest)) if not a.ids or b["id"] in a.ids]
    bad = 0
    for b in boards if a.cmd != "table" else []:
        if a.cmd == "fetch":
            fetch(cache, b)
        else:
            for did, r in run(cache, b).items():
                print(did, json.dumps(r))
                bad += r.get("ran", False) and not (r["rev_a"] and not r["rev_b"])
    if a.cmd == "table":
        Path(a.out).write_text(table(cache, boards), encoding="utf-8")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
