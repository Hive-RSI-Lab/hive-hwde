#!/usr/bin/env python
"""human_corpus.py - the human-made board corpus: pin, fetch, label, gate, table.

The evals need boards a person designed, with whatever is known about how each
one turned out, run through the same gates hwde's own boards meet. The corpus
is the manifest reference/human_corpus.yaml; docs/human-corpus.md says how the
boards were picked and how to read the results. Every subcommand works on the
whole manifest, or on the board ids given after it, so a later batch re-runs
without hand work:

    human_corpus.py pin   [ID...]   resolve commit, licence and pcb from GitHub
    human_corpus.py fetch [ID...]   check the boards out by pinned commit
    human_corpus.py label [ID...]   collect outcome evidence from GitHub
    human_corpus.py run   [ID...]   verify_all + dfm_check on each board
    human_corpus.py table           write docs/human-corpus-results.md

Common options: --manifest FILE (default reference/human_corpus.yaml),
--cache DIR (default ~/.cache/hwde-human-corpus, or HWDE_HUMAN_CORPUS_CACHE).

Rules the subcommands keep:

- Nothing is fetched into this repo or the boards repo. Checkouts live in
  CACHE/repos/<owner>__<name>@<sha12>/ (one per repo and commit, shared by
  every board of that repo), sparse to each board's directory; gate outputs
  live in CACHE/runs/<id>/. A cache path inside the repo or the boards root is
  refused.
- pin fills only the fields a board lacks (commit, licence, licence_source,
  pcb), and refuses a board whose licence is not in LICENCES: it is marked
  `excluded` with the reason, and fetch, run and table pass it by. A board
  with no licence is never taken. A licence set by hand (the API reports
  NOASSERTION for a hardware licence it cannot read) stays as set, with its
  licence_source naming the file that grants it.
- fetch refuses a board whose .kicad_pcb predates KiCad 6 (file version below
  KICAD6_VERSION) or was saved by a KiCad nightly the pinned KiCad 10 cannot
  open (above KICAD10_VERSION), and records layers and kicad_version from the file itself.
- run copies a board's folder skipping dangling symlinks, and a board whose copy
  fails is reported as failed while the rest of the batch runs on.
- label writes only the machine field `evidence` (GitHub releases and issues
  whose words point at a fabricated board or an erratum; it is read per
  repo, so boards of one repo share it). The `outcome` a
  person wrote is never rewritten: the evidence sits beside it for the owner
  to confirm, and an outcome with no evidence stays empty.
- run takes at most --jobs boards at once (default 2), on an Xvfb display it
  starts for itself, and copies each board's directory to CACHE/runs/<id>/
  before any gate touches it. Each board's gate results go to
  CACHE/runs/<id>/result.json, which table reads.

Manifest fields per board: id, url, commit, licence, licence_source, domain
(power | mcu-usb | analog | motor | rf | 4-layer), pcb (path in the repo),
layers, kicad_version, outcome {label, source} (label is one of OUTCOMES, or
empty), pair {with, role (before|after), note} for a revision pair, evidence,
excluded, notes.

Exit: 0 done, 1 some board failed (named on stderr), 2 bad invocation.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

SCRIPTS = Path(__file__).resolve().parent
SKILL = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from lib import env  # noqa: E402

MANIFEST = SKILL / "reference" / "human_corpus.yaml"
RESULTS_DOC = env.repo_root() / "docs" / "human-corpus-results.md"
LICENCES = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "0BSD",
    "CERN-OHL-P-2.0", "CERN-OHL-W-2.0", "CERN-OHL-S-2.0", "CERN-OHL-1.2",
    "CC-BY-3.0", "CC-BY-4.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0",
}
DOMAINS = {"power", "mcu-usb", "analog", "motor", "rf", "4-layer"}
OUTCOMES = {"", "product", "fabricated", "errata", "fixed-in-later-rev"}
KICAD6_VERSION = 20211014
KICAD10_VERSION = 20260206  # newest format the pinned KiCad 10 reads; above is a nightly
EVIDENCE_WORDS = ("errata", "erratum", "rework", "bodge", "wrong footprint",
                  "swapped", "short", "fab", "assembled", "rev ")
HEADER = """\
# The human-made board corpus (scripts/human_corpus.py, docs/human-corpus.md).
# pin, fetch and label fill commit/licence/pcb, layers/kicad_version and
# evidence; outcome and pair are a person's word and no pass rewrites them.
"""


def cache_root(arg: str | None) -> Path:
    root = Path(arg or os.environ.get("HWDE_HUMAN_CORPUS_CACHE")
                or Path.home() / ".cache" / "hwde-human-corpus").expanduser().resolve()
    for forbidden in (env.repo_root().resolve(), env.boards_root().expanduser().resolve()):
        if root == forbidden or forbidden in root.parents:
            raise SystemExit(f"error: cache {root} is inside {forbidden}; "
                             "the corpus is never fetched into a repo")
    return root


def load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("boards", [])
    return data


def save(path: Path, data: dict) -> None:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=False, width=100)
    path.write_text(HEADER + body, encoding="utf-8")


def select(data: dict, ids: list[str], include_excluded: bool = False) -> list[dict]:
    known = {b["id"] for b in data["boards"]}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise SystemExit(f"error: unknown board id(s): {', '.join(unknown)}")
    out = [b for b in data["boards"] if not ids or b["id"] in ids]
    return out if include_excluded else [b for b in out if not b.get("excluded")]


def slug(url: str) -> str:
    m = re.match(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise SystemExit(f"error: not a GitHub repo url: {url}")
    return f"{m.group(1)}/{m.group(2)}"


def gh(path: str) -> dict | list | None:
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout else None


# ---------------------------------------------------------------- pin

def pin_board(b: dict) -> str:
    repo = slug(b["url"])
    meta = gh(f"repos/{repo}")
    if not meta:
        return "repo not found"
    if not b.get("licence"):
        spdx = (meta.get("license") or {}).get("spdx_id")
        if not spdx or spdx == "NOASSERTION":
            b["excluded"] = f"no licence GitHub can read ({spdx}); set one by hand with its source"
            return b["excluded"]
        b["licence"] = spdx
        b["licence_source"] = f"GitHub licence detection on {repo}"
    if b["licence"] not in LICENCES:
        b["excluded"] = f"licence {b['licence']} is not in the accepted set"
        return b["excluded"]
    b.pop("excluded", None)
    if not b.get("commit"):
        head = gh(f"repos/{repo}/commits/{meta['default_branch']}")
        if not head:
            return "default branch has no commit"
        b["commit"] = head["sha"]
    if not b.get("pcb"):
        tree = gh(f"repos/{repo}/git/trees/{b['commit']}?recursive=1") or {}
        pros = [e["path"] for e in tree.get("tree", []) if e["path"].endswith(".kicad_pro")]
        if len(pros) != 1:
            return f"{len(pros)} .kicad_pro files; set pcb by hand"
        b["pcb"] = pros[0][: -len(".kicad_pro")] + ".kicad_pcb"
    return ""


# ---------------------------------------------------------------- fetch

def checkout_dir(cache: Path, b: dict) -> Path:
    return cache / "repos" / f"{slug(b['url']).replace('/', '__')}@{b['commit'][:12]}"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True, timeout=1800)


def pcb_facts(pcb: Path) -> tuple[int, int]:
    """(file version, copper layer count) from the board file itself."""
    text = pcb.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\(version\s+(\d+)\)", text[:2000])
    version = int(m.group(1)) if m else 0
    layers = re.search(r"\(layers\b(.*?)\n\s*\)", text, re.S)
    copper = re.findall(r'\(\d+\s+"?[^\s"]*\.Cu"?\s+(?:signal|power|mixed|jumper)',
                        layers.group(1)) if layers else []
    return version, len(copper)


def fetch_board(cache: Path, b: dict) -> str:
    if not b.get("commit") or not b.get("pcb"):
        return "not pinned (run pin)"
    dest = checkout_dir(cache, b)
    board_dir = str(Path(b["pcb"]).parent)
    sparse = "/" if board_dir == "." else board_dir
    if not (dest / ".git").exists():
        dest.mkdir(parents=True, exist_ok=True)
        git(dest, "init", "-q")
        git(dest, "remote", "add", "origin", b["url"])
        git(dest, "sparse-checkout", "set", "--no-cone", "/*.*" if sparse == "/" else sparse)
    else:
        # several boards of one repo widen the same sparse checkout
        r = subprocess.run(["git", "-C", str(dest), "sparse-checkout", "list"],
                           capture_output=True, text=True)
        have = r.stdout.splitlines()  # a path may hold spaces
        want = "/*.*" if sparse == "/" else sparse
        if want not in have:
            git(dest, "sparse-checkout", "set", "--no-cone", *have, want)
    git(dest, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", b["commit"])
    git(dest, "checkout", "-q", "--detach", b["commit"])
    pcb = dest / b["pcb"]
    if not pcb.exists():
        return f"{b['pcb']} is not in commit {b['commit'][:12]}"
    version, layers = pcb_facts(pcb)
    b["kicad_version"] = version
    b["layers"] = layers
    if version < KICAD6_VERSION:
        b["excluded"] = f"board file version {version} predates KiCad 6"
        return b["excluded"]
    if version > KICAD10_VERSION:
        b["excluded"] = f"board file version {version} is a KiCad nightly newer than KiCad 10"
        return b["excluded"]
    return ""


# ---------------------------------------------------------------- label

def label_board(b: dict) -> str:
    repo = slug(b["url"])
    evidence = []
    rel = gh(f"repos/{repo}/releases?per_page=20") or []
    if rel:
        evidence.append(f"{len(rel)} GitHub release(s), latest {rel[0].get('tag_name')}")
    for state in ("closed", "open"):
        issues = gh(f"repos/{repo}/issues?state={state}&per_page=100") or []
        for it in issues:
            if "pull_request" in it:
                continue
            words = (it.get("title") or "").lower()
            if any(w in words for w in EVIDENCE_WORDS):
                evidence.append(f"issue #{it['number']} ({state}): {it['title'][:90]} - {it['html_url']}")
    b["evidence"] = evidence[:12]
    return ""


# ---------------------------------------------------------------- run

def start_xvfb() -> tuple[subprocess.Popen | None, str]:
    for n in range(140, 200):
        if Path(f"/tmp/.X{n}-lock").exists():
            continue
        proc = subprocess.Popen(["Xvfb", f":{n}", "-nolisten", "tcp"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
        if proc.poll() is None:
            return proc, f":{n}"
    raise SystemExit("error: no free X display between :140 and :199")


def gate(cmd: list[str], envv: dict, timeout: int) -> dict:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=envv, timeout=timeout)
        return {"exit": r.returncode, "seconds": round(time.time() - t0, 1),
                "stderr_tail": r.stderr[-800:], "stdout_tail": r.stdout[-800:]}
    except subprocess.TimeoutExpired:
        return {"exit": "timeout", "seconds": timeout, "stderr_tail": ""}


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_board(cache: Path, b: dict, envv: dict, timeout: int) -> str:
    src = checkout_dir(cache, b) / b["pcb"]
    if not src.exists():
        return "not fetched (run fetch)"
    work = cache / "runs" / b["id"]
    if work.exists():
        shutil.rmtree(work)
    kicad = work / "kicad"
    shutil.copytree(src.parent, kicad, ignore=shutil.ignore_patterns(".git", "*-backups"),
                    ignore_dangling_symlinks=True)
    pcb = kicad / src.name
    reports = work / "reports"
    reports.mkdir(parents=True)
    py = sys.executable
    res = {"id": b["id"], "commit": b["commit"], "pcb": b["pcb"]}
    res["verify_all"] = gate([py, str(SCRIPTS / "verify_all.py"), "--pcb", str(pcb),
                              "--reports-dir", str(reports / "checks"), "--jobs", "1",
                              "--out", str(reports / "verify_all.json")], envv, timeout)
    sch = pcb.with_suffix(".kicad_sch")
    dfm = [py, str(SCRIPTS / "dfm_check.py"), "--pcb", str(pcb),
           "--out", str(reports / "dfm.json")]
    dfm += ["--schematic", str(sch)] if sch.exists() else ["--no-polarity"]
    res["dfm_check"] = gate(dfm, envv, timeout)
    res["verify_summary"] = read_json(reports / "verify_all.json")
    res["dfm_report"] = read_json(reports / "dfm.json")
    (work / "result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    return ""


# ---------------------------------------------------------------- table

def summarise(res: dict) -> dict:
    v = res.get("verify_summary") or {}
    d = res.get("dfm_report") or {}
    dviol = d.get("violations") or []
    return {
        "verify": v.get("status") or f"no summary (exit {res['verify_all']['exit']})",
        "verify_sev": (v.get("counts") or {}).get("by_severity") or {},
        "verify_checks": v.get("checks") or {},
        "verify_viol": v.get("violations") or [],
        "dfm": d.get("status") or f"no report (exit {res['dfm_check']['exit']})",
        "dfm_viol": dviol,
    }


def finding_line(v: dict) -> str:
    where = ", ".join(v.get("refs") or []) or v.get("net") or ""
    if v.get("pos"):
        where = f"{where} @ ({v['pos'][0]:.1f}, {v['pos'][1]:.1f})".strip()
    msg = (v.get("msg") or v.get("message") or "").replace("|", "/").replace("\n", " ")
    check = v.get("source") or v.get("check") or ""
    if v.get("kind"):
        check = f"{check} / {v['kind']}"
    return f"{v.get('severity', '?')} | {check} | {msg[:140]} | {where[:60]}"


def gate_error(g: dict) -> str:
    """The error a gate printed when it wrote no report (its JSON on stdout, else stderr)."""
    try:
        return str(json.loads(g.get("stdout_tail") or "").get("error"))[:300]
    except (ValueError, AttributeError):
        return (g.get("stderr_tail") or g.get("stdout_tail") or f"exit {g.get('exit')}")[-300:]


def table(data: dict, cache: Path, out: Path, per_board: int) -> None:
    rows, sections = [], []
    kinds: dict[str, set] = {}
    for b in data["boards"]:
        if b.get("excluded"):
            continue
        res = read_json(cache / "runs" / b["id"] / "result.json")
        label = (b.get("outcome") or {}).get("label") or ""
        if not res:
            rows.append(f"| {b['id']} | {b['domain']} | {b.get('layers', '')} | {label} | not run | | | |")
            continue
        s = summarise(res)
        err = s["verify_sev"].get("error", 0)
        warn = s["verify_sev"].get("warning", 0)
        derr = sum(1 for v in s["dfm_viol"] if v.get("severity") == "error")
        rows.append(f"| [{b['id']}](#{b['id']}) | {b['domain']} | {b.get('layers', '')} | {label} "
                    f"| {s['verify']} | {err} / {warn} | {s['dfm']} | {derr} / {len(s['dfm_viol'])} |")
        lines = [f"### {b['id']}", "",
                 f"{b['url']} at `{b['commit'][:12]}`, `{b['pcb']}`, {b['licence']}, "
                 f"{b['domain']}, {b.get('layers', '?')} layers.", ""]
        if label:
            lines.append(f"Outcome: **{label}** ({b['outcome'].get('source', '')})")
            lines.append("")
        if b.get("pair"):
            p = b["pair"]
            lines += [f"Revision pair: {p.get('role')} of [{p.get('with')}](#{p.get('with')}). "
                      f"{p.get('note', '')}", ""]
        for name in ("verify_all", "dfm_check"):
            report = s["verify"] if name == "verify_all" else s["dfm"]
            if report.startswith("no "):
                lines += [f"{name} wrote no report: {gate_error(res[name])}", ""]
        ran = {k: c.get("status") for k, c in s["verify_checks"].items()}
        lines.append("verify_all checks: " + ", ".join(f"{k} {v}" for k, v in sorted(ran.items())))
        lines.append("")
        found = [v for v in s["verify_viol"] + s["dfm_viol"]
                 if v.get("severity") in ("error", "warning")]
        found.sort(key=lambda v: v.get("severity") != "error")
        for v in found:
            if v.get("severity") == "error":
                key = f"{v.get('source') or v.get('check')} / {v.get('kind')}"
                kinds.setdefault(key, set()).add(b["id"])
        if found:
            lines += ["| severity | check | finding | where |", "|---|---|---|---|"]
            lines += [f"| {finding_line(v)} |" for v in found[:per_board]]
            if len(found) > per_board:
                lines.append(f"\n{len(found) - per_board} more in `runs/{b['id']}/reports/`.")
        else:
            lines.append("No error or warning findings.")
        sections.append("\n".join(lines) + "\n")
    doc = ["# Human-made board corpus: gate baseline", "",
           "Written by `human_corpus.py table`; docs/human-corpus.md says how to read it. "
           "verify and dfm counts are errors / warnings for verify_all and errors / all "
           "findings for dfm_check.", "",
           "| board | domain | layers | outcome | verify_all | errors / warnings | dfm_check "
           "| errors / findings |", "|---|---|---|---|---|---|---|---|", *rows, "",
           "## Error findings by kind", "",
           "How many boards each kind of error finding fires on: a kind that fires on "
           "most boards is a gate to question before it is a board to blame.", "",
           "| check / kind | boards |", "|---|---|",
           *[f"| {k} | {len(v)} |" for k, v in
             sorted(kinds.items(), key=lambda kv: (-len(kv[1]), kv[0]))], "",
           "## Per-board findings", "", *sections]
    out.write_text("\n".join(doc), encoding="utf-8")


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["pin", "fetch", "label", "run", "table"])
    ap.add_argument("ids", nargs="*", help="board ids (default: every board)")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--cache", help="checkout + run dir (default ~/.cache/hwde-human-corpus)")
    ap.add_argument("--jobs", type=int, default=2, help="boards gated at once (run)")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds per gate (run)")
    ap.add_argument("--per-board", type=int, default=25, help="findings listed per board (table)")
    ap.add_argument("--out", default=str(RESULTS_DOC), help="results doc (table)")
    a = ap.parse_args(argv)
    manifest = Path(a.manifest)
    data = load(manifest)
    cache = cache_root(a.cache)
    failed: dict[str, str] = {}

    if a.command == "table":
        table(data, cache, Path(a.out), a.per_board)
        return 0
    if a.command == "pin":
        for b in select(data, a.ids, include_excluded=True):
            if b.get("domain") not in DOMAINS:
                failed[b["id"]] = f"domain {b.get('domain')!r} is not one of {sorted(DOMAINS)}"
            elif (b.get("outcome") or {}).get("label", "") not in OUTCOMES:
                failed[b["id"]] = f"outcome label is not one of {sorted(OUTCOMES)}"
            elif msg := pin_board(b):
                failed[b["id"]] = msg
    elif a.command == "fetch":
        for b in select(data, a.ids):
            try:
                if msg := fetch_board(cache, b):
                    failed[b["id"]] = msg
            except subprocess.SubprocessError as e:
                failed[b["id"]] = f"git: {getattr(e, 'stderr', '') or e}"[:300]
    elif a.command == "label":
        for b in select(data, a.ids):
            label_board(b)
    elif a.command == "run":
        xvfb, display = start_xvfb()
        envv = dict(os.environ, DISPLAY=display)
        try:
            with concurrent.futures.ThreadPoolExecutor(max(1, min(a.jobs, 2))) as ex:
                futs = {ex.submit(run_board, cache, b, envv, a.timeout): b["id"]
                        for b in select(data, a.ids)}
                for f in concurrent.futures.as_completed(futs):
                    try:
                        msg = f.result()
                    except (OSError, shutil.Error) as e:  # one board never kills the batch
                        msg = f"run: {e}"[:300]
                    print(f"{futs[f]}: {msg or 'done'}", file=sys.stderr, flush=True)
                    if msg:
                        failed[futs[f]] = msg
        finally:
            xvfb.terminate()
    if a.command in ("pin", "fetch", "label"):
        save(manifest, data)
    for bid, msg in failed.items():
        print(f"{bid}: {msg}", file=sys.stderr)
    print(json.dumps({"command": a.command, "failed": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
