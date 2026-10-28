"""score_checks.py - per-check scorecard for the deterministic verify checks.

Runs every check verify_all.py runs (verify_all.load_checks: the built-in
suite plus every scripts/checks.d/*.yaml fragment, so a new check joins the
scorecard the day it joins the suite) over three corpora and scores each
check on its own:

  golden   tests/golden/<board>/ - known-good boards. Every finding counts as
           a false positive, unless tests/golden/scorecard/triage.yaml records
           it as a true finding (verdict: real) with the reason.
  mutants  tests/golden/manifest.yaml mutants, plus those of every
           tests/golden/manifest.d/*.yaml fragment (same `mutants:` shape;
           a name taken twice is an error) - one planted fault each, owned
           by one check. The owning check catches it when one of its findings
           matches every key of the mutant's `expect` (kind, net, layer, ref in
           refs, pair as a set, pos within POS_TOL_MM); otherwise it is a miss.
           A mutant dir's own constraints.json / decoupling.json / parts/
           override the golden's (a fault may be planted in a sidecar).
  boards   finished boards in the boards repo (env.boards_root(); state.json
           phase P9 or later, i.e. past the verify gate). A finding matching a
           triage.yaml entry for that board takes its verdict (real / fp);
           a finding matching the board's reports/verify-waivers.json but no
           triage entry is `waived_untriaged`; the rest are `untriaged`
           (reported, not scored - nobody has judged them). Skipped, with the
           reason, when the boards repo is absent.

Per check: fp, tp, misses, caught, precision = tp/(tp+fp), recall =
caught/(caught+misses) - None where the denominator is 0. tp counts golden
and board findings triaged real plus caught mutants.

--record appends the run as one dated line to --history (default
docs/check-scorecard.jsonl) and rewrites the table in docs/check-scorecard.md
between its SCORECARD markers. --compare checks the run against the last
history line: a check whose fp or misses rose on a corpus both runs scored is
a regression (`regressions` in the output, exit 1).

Output: {"script", "status": pass|regressed, "date", "corpora"{name: {status,
reason?, items}}, "checks"{name: {fp, tp, caught, misses, precision, recall,
by_corpus{corpus: {fp, tp, caught, misses, untriaged, waived_untriaged,
errors}}}}, "findings"[...fp / miss / untriaged detail], "regressions"[...]}

Exit (SPEC section 6): 0 scored and no regression, 1 regression against the
last history line, 2 error.

--golden and --checks-d point the run at another corpus and fragment dir
(the registry's own test scores a drop-in check that way).

CLI: [--corpora golden,mutants,boards] [--boards-root DIR] [--jobs N]
     [--golden DIR] [--checks-d DIR] [--out FILE] [--history FILE]
     [--doc FILE] [--record] [--compare]
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import importlib.util
import json
import math
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "lib"))
import checklib  # noqa: E402
import env  # noqa: E402
import verify_all  # noqa: E402

SCRIPT = "score_checks"
REPO = HERE.parents[3]
GOLDEN = REPO / "tests" / "golden"
TRIAGE = GOLDEN / "scorecard" / "triage.yaml"
HISTORY = REPO / "docs" / "check-scorecard.jsonl"
DOC = REPO / "docs" / "check-scorecard.md"
CORPORA = ("golden", "mutants", "boards")
POS_TOL_MM = 1.0
# boards past the P8 verify gate count as finished
FINISHED_PHASE = 9
DOC_BEGIN = "<!-- SCORECARD:BEGIN (score_checks.py --record writes this) -->"
DOC_END = "<!-- SCORECARD:END -->"
CHECK_NAMES = [c["name"] for c in verify_all.CHECKS]


# ------------------------------------------------------------ running checks

_MODULES: dict[str, object] = {}


def _load(script: str):
    """The check module at `script`, imported once per worker."""
    mod = _MODULES.get(script)
    if mod is None:
        stem = Path(script).stem
        mod = sys.modules.get(stem)
        if mod is None or Path(getattr(mod, "__file__", "") or "").resolve() \
                != Path(script).resolve():
            spec = importlib.util.spec_from_file_location(stem, script)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[stem] = mod
            spec.loader.exec_module(mod)
        _MODULES[script] = mod
    return mod


def _run_check(script: str, argv: list[str]) -> dict:
    """One check in-process (a pool worker); an exception is the check's
    error status, as cli_wrap would report it."""
    try:
        payload, _ = _load(script).run(argv)
        return {"status": payload.get("status"),
                "violations": payload.get("violations", [])}
    except BaseException as exc:  # noqa: BLE001 - argparse exits too
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}",
                "violations": []}


def check_argv(check: dict, inputs: dict) -> list[str] | None:
    """verify_all's invocation of `check` on `inputs`, or None when an input
    it needs is absent (verify_all skips it the same way)."""
    if any(not inputs.get(k) for k in check["needs"]):
        return None
    return ["--pcb", inputs["pcb"], *check["args"](inputs)]


def sidecars(base: Path, fallback: Path | None = None) -> dict:
    """The check inputs found in `base`, each falling back to `fallback`."""
    out = {}
    for key, rel in (("constraints", "constraints.json"),
                     ("decoupling", "decoupling.json"), ("parts", "parts")):
        for d in (base, fallback):
            if d is not None and (d / rel).exists():
                out[key] = str(d / rel)
                break
    return out


# ------------------------------------------------------------ matching

def _near(a, b) -> bool:
    try:
        return math.hypot(float(a[0]) - float(b[0]),
                          float(a[1]) - float(b[1])) <= POS_TOL_MM
    except (TypeError, ValueError, IndexError):
        return False


def catches(expect: dict, v: dict) -> bool:
    """A finding catches a planted fault when it matches every identifying
    key the manifest's `expect` names (keys it does not know are ignored)."""
    if "kind" in expect and v.get("kind") != expect["kind"]:
        return False
    if "net" in expect and v.get("net") != expect["net"]:
        return False
    if "layer" in expect and v.get("layer") != expect["layer"]:
        return False
    if "ref" in expect and expect["ref"] not in (v.get("refs") or []):
        return False
    if "pair" in expect and set(v.get("pair") or []) != set(expect["pair"]):
        return False
    if "pos" in expect and not _near(expect["pos"], v.get("pos")):
        return False
    return True


def triage_matches(t: dict, check: str, v: dict) -> bool:
    """A triage entry names check and kind, and optionally net, refs (the
    finding's refs a subset of them) and pos."""
    if t.get("check") != check or t.get("kind") != v.get("kind"):
        return False
    if "net" in t and t["net"] != v.get("net"):
        return False
    if t.get("refs"):
        vrefs = set(v.get("refs") or [])
        if not vrefs or not vrefs <= set(t["refs"]):
            return False
    if "pos" in t and not _near(t["pos"], v.get("pos")):
        return False
    return True


def load_manifest(golden: Path = GOLDEN) -> dict:
    """<golden>/manifest.yaml with every <golden>/manifest.d/*.yaml
    fragment's `mutants:` merged in. A fragment holds only mutants, and a
    mutant name already taken is an error, so two rows never collide."""
    manifest = yaml.safe_load((golden / "manifest.yaml").read_text(
        encoding="utf-8"))
    mutants = manifest.setdefault("mutants", {})
    for path in sorted((golden / "manifest.d").glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(doc, dict) or set(doc) - {"mutants"} \
                or not isinstance(doc.get("mutants"), dict):
            raise checklib.CheckError(
                f"{path}: a fragment is one `mutants:` mapping")
        for name, m in doc["mutants"].items():
            if name in mutants:
                raise checklib.CheckError(
                    f"{path}: mutant {name!r} already in the manifest")
            mutants[name] = m
    return manifest


def load_triage(path: Path) -> list[dict]:
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = doc.get("entries") or []
    for i, t in enumerate(entries):
        if t.get("verdict") not in ("real", "fp") \
                or not str(t.get("reason") or "").strip() \
                or not t.get("board") or not t.get("check") \
                or not t.get("kind"):
            raise checklib.CheckError(
                f"{path}: entry {i} needs board, check, kind, verdict "
                "(real|fp) and a reason")
    return entries


def load_waivers(ws: Path) -> list[dict]:
    p = ws / "reports" / "verify-waivers.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("waivers") or []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


# ------------------------------------------------------------ corpora

def golden_jobs(manifest: dict, names: list[str],
                golden: Path = GOLDEN) -> list[dict]:
    jobs = []
    for board in manifest["golden_boards"]:
        d = golden / board
        jobs.append({"corpus": "golden", "item": board, "board": f"golden:{board}",
                     "inputs": {"pcb": str(d / f"{board}.kicad_pcb"),
                                **sidecars(d)},
                     "checks": names})
    return jobs


def mutant_jobs(manifest: dict, names: list[str],
                golden: Path = GOLDEN) -> list[dict]:
    jobs = []
    for name, m in manifest["mutants"].items():
        if m["check"] not in names:
            continue    # e.g. dfm_check: a fab check, not in the verify suite
        d = golden / "mutants" / name
        jobs.append({"corpus": "mutants", "item": name,
                     "board": f"golden:{m['board']}",
                     "inputs": {"pcb": str(d / f"{m['board']}.kicad_pcb"),
                                **sidecars(d, golden / m["board"])},
                     "checks": [m["check"]], "target": m["check"],
                     "expect": m.get("expect") or {}})
    return jobs


def _phase(ws: Path) -> int:
    try:
        ph = json.loads((ws / "state.json").read_text(encoding="utf-8"))
        return int(str(ph.get("phase", "P0")).lstrip("P") or 0)
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        return 0


def board_jobs(root: Path, names: list[str]) -> tuple[list[dict], str | None]:
    if not root.is_dir():
        return [], f"boards repo absent at {root}"
    jobs = []
    for ws in sorted(p for p in root.iterdir() if p.is_dir()):
        pcbs = sorted((ws / "kicad").glob("*.kicad_pcb"))
        if _phase(ws) < FINISHED_PHASE or len(pcbs) != 1:
            continue
        inputs = {"pcb": str(pcbs[0]), **sidecars(ws / "kicad")}
        if (ws / "parts").is_dir():
            inputs["parts"] = str(ws / "parts")
        jobs.append({"corpus": "boards", "item": ws.name, "board": ws.name,
                     "inputs": inputs, "checks": names,
                     "waivers": load_waivers(ws)})
    if not jobs:
        return [], f"no finished board (phase >= P{FINISHED_PHASE}) in {root}"
    return jobs, None


# ------------------------------------------------------------ scoring

def _blank() -> dict:
    return {"fp": 0, "tp": 0, "caught": 0, "misses": 0, "untriaged": 0,
            "waived_untriaged": 0, "errors": 0}


def _ratio(num: int, den: int):
    return round(num / den, 4) if den else None


def score(jobs: list[dict], triage: list[dict], n_jobs: int,
          checks: list[dict] | None = None) -> dict:
    checks = verify_all.CHECKS if checks is None else checks
    by = {c["name"]: {k: _blank() for k in CORPORA} for c in checks}
    script = {c["name"]: str(verify_all.check_script(c)) for c in checks}
    findings: list[dict] = []
    calls = []
    for job in jobs:
        for check in checks:
            if check["name"] not in job["checks"]:
                continue
            argv = check_argv(check, job["inputs"])
            calls.append((job, check["name"], argv))
    runnable = [(j, c, a) for j, c, a in calls if a is not None]
    with concurrent.futures.ProcessPoolExecutor(max_workers=n_jobs) as ex:
        results = list(ex.map(_run_check, [script[c] for _, c, _ in runnable],
                              [a for _, _, a in runnable]))
    got = {(id(j), c): r for (j, c, _), r in zip(runnable, results)}
    for job, name, argv in calls:
        cell = by.setdefault(name, {k: _blank() for k in CORPORA})[job["corpus"]]
        where = {"check": name, "corpus": job["corpus"], "item": job["item"]}
        if argv is None:
            if job["corpus"] == "mutants":  # an unrunnable target is a miss
                cell["misses"] += 1
                findings.append({**where, "verdict": "miss",
                                 "why": "check skipped: an input is absent"})
            continue
        res = got[(id(job), name)]
        if res["status"] == "error":
            cell["errors"] += 1
            if job["corpus"] == "mutants":
                cell["misses"] += 1
            findings.append({**where, "verdict": "error", "why": res["error"]})
            continue
        vs = [v for v in res["violations"]
              if (v.get("source") or v.get("check")) == name]
        if job["corpus"] == "mutants":
            if any(catches(job["expect"], v) for v in vs):
                cell["caught"] += 1
                cell["tp"] += 1
            else:
                cell["misses"] += 1
                findings.append({**where, "verdict": "miss",
                                 "expect": job["expect"],
                                 "got": [_brief(v) for v in vs][:5]})
            continue
        for v in vs:
            verdict = next((t["verdict"] for t in triage
                            if t["board"] == job["board"]
                            and triage_matches(t, name, v)), None)
            if verdict is None and job["corpus"] == "golden":
                verdict = "fp"          # a known-good board: every finding
            if verdict is None:
                import gate  # lazy: only the boards corpus needs it
                waived = any(gate.waiver_matches(w, v)
                             for w in job.get("waivers") or [])
                verdict = "waived_untriaged" if waived else "untriaged"
            cell[{"real": "tp"}.get(verdict, verdict)] += 1
            if verdict != "real":
                findings.append({**where, "verdict": verdict, **_brief(v)})
    checks = {}
    for name, corp in by.items():
        tot = {k: sum(c[k] for c in corp.values()) for k in _blank()}
        checks[name] = {"fp": tot["fp"], "tp": tot["tp"],
                        "caught": tot["caught"], "misses": tot["misses"],
                        "precision": _ratio(tot["tp"], tot["tp"] + tot["fp"]),
                        "recall": _ratio(tot["caught"],
                                         tot["caught"] + tot["misses"]),
                        "by_corpus": corp}
    return {"checks": checks, "findings": findings}


def _brief(v: dict) -> dict:
    return {k: v.get(k) for k in ("kind", "severity", "net", "refs", "pos",
                                  "layer", "msg")}


# ------------------------------------------------------------ history

def last_record(history: Path) -> dict | None:
    if not history.exists():
        return None
    lines = [ln for ln in history.read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    return json.loads(lines[-1]) if lines else None


def record_line(payload: dict) -> dict:
    """The history line: per check, the headline numbers and the per-corpus
    fp/misses the gate compares (findings detail stays out)."""
    return {"date": payload["date"],
            "corpora": {k: v["status"] for k, v in payload["corpora"].items()},
            "checks": {n: {"fp": c["fp"], "tp": c["tp"], "caught": c["caught"],
                           "misses": c["misses"], "precision": c["precision"],
                           "recall": c["recall"],
                           "by_corpus": {k: {"fp": b["fp"], "misses": b["misses"],
                                             "untriaged": b["untriaged"]}
                                         for k, b in c["by_corpus"].items()}}
                       for n, c in payload["checks"].items()}}


def regressions(now: dict, base: dict | None) -> list[dict]:
    """Checks whose fp or misses rose, per corpus both runs scored. A check
    or corpus the baseline lacks is new, not a regression."""
    if not base:
        return []
    out = []
    for name, c in now["checks"].items():
        b = base["checks"].get(name)
        if not b:
            continue
        for corpus, cell in c["by_corpus"].items():
            if base["corpora"].get(corpus) != "scored" \
                    or now["corpora"].get(corpus) != "scored":
                continue
            bc = b["by_corpus"].get(corpus) or {}
            for metric in ("fp", "misses"):
                if cell[metric] > bc.get(metric, 0):
                    out.append({"check": name, "corpus": corpus,
                                "metric": metric, "was": bc.get(metric, 0),
                                "now": cell[metric]})
    return out


def _fmt(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def doc_table(lines: list[dict]) -> str:
    cur, prev = lines[-1], (lines[-2] if len(lines) > 1 else None)
    rows = [f"Last run {cur['date']}"
            + (f", compared with {prev['date']}." if prev else "."),
            "Corpora: " + ", ".join(f"{k} {v}" for k, v in
                                    cur["corpora"].items()) + ".", "",
            "| check | fp | misses | caught | precision | recall | change |",
            "|---|---|---|---|---|---|---|"]
    for name, c in cur["checks"].items():
        p = (prev or {}).get("checks", {}).get(name)
        delta = "new" if prev and not p else ""
        if p:
            parts = [f"{k} {p[k]}->{c[k]}" for k in ("fp", "misses", "caught")
                     if p[k] != c[k]]
            delta = ", ".join(parts) or "same"
        rows.append(f"| {name} | {c['fp']} | {c['misses']} | {c['caught']} | "
                    f"{_fmt(c['precision'])} | {_fmt(c['recall'])} | {delta} |")
    return "\n".join(rows)


def write_doc(doc: Path, history: Path) -> None:
    lines = [json.loads(ln) for ln in
             history.read_text(encoding="utf-8").splitlines() if ln.strip()]
    text = doc.read_text(encoding="utf-8")
    head, sep, rest = text.partition(DOC_BEGIN)
    _, sep2, tail = rest.partition(DOC_END)
    if not sep or not sep2:
        raise checklib.CheckError(f"{doc} lacks the SCORECARD markers")
    doc.write_text(f"{head}{DOC_BEGIN}\n{doc_table(lines)}\n{DOC_END}{tail}",
                   encoding="utf-8")


# ------------------------------------------------------------ CLI

def run(argv=None):
    ap = argparse.ArgumentParser(
        description="Score each deterministic check: false positives, "
                    "misses, precision and recall over three corpora.")
    ap.add_argument("--corpora", default=",".join(CORPORA),
                    help="comma list of golden,mutants,boards")
    ap.add_argument("--boards-root", help="boards repo (default "
                    "env.boards_root())")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--golden", default=str(GOLDEN),
                    help="golden corpus dir: manifest.yaml, manifest.d/, "
                         "boards, mutants/")
    ap.add_argument("--checks-d", default=str(verify_all.CHECKS_D),
                    help="check fragment dir (verify_all.load_checks)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    ap.add_argument("--history", default=str(HISTORY))
    ap.add_argument("--doc", default=str(DOC))
    ap.add_argument("--triage", default=str(TRIAGE))
    ap.add_argument("--record", action="store_true",
                    help="append this run to --history and rewrite --doc")
    ap.add_argument("--compare", action="store_true",
                    help="exit 1 when a check regressed against the last "
                         "history line")
    args = ap.parse_args(argv)
    want = [c.strip() for c in args.corpora.split(",") if c.strip()]
    bad = [c for c in want if c not in CORPORA]
    if bad:
        raise checklib.CheckError(f"unknown corpus {bad}; known {CORPORA}")

    golden = Path(args.golden)
    checks = verify_all.load_checks(Path(args.checks_d))
    names = [c["name"] for c in checks]
    manifest = load_manifest(golden)
    corpora: dict[str, dict] = {}
    jobs: list[dict] = []
    for corpus in CORPORA:
        if corpus not in want:
            corpora[corpus] = {"status": "skipped", "reason": "not asked for",
                               "items": []}
            continue
        reason = None
        if corpus == "golden":
            js = golden_jobs(manifest, names, golden)
        elif corpus == "mutants":
            js = mutant_jobs(manifest, names, golden)
        else:
            root = Path(args.boards_root) if args.boards_root \
                else env.boards_root()
            js, reason = board_jobs(root, names)
        corpora[corpus] = {"status": "skipped" if reason else "scored",
                           "items": [j["item"] for j in js]}
        if reason:
            corpora[corpus]["reason"] = reason
        jobs += js

    scored = score(jobs, load_triage(Path(args.triage)), max(1, args.jobs),
                   checks)
    payload = {"script": SCRIPT,
               "date": datetime.date.today().isoformat(),
               "corpora": corpora, **scored}
    history = Path(args.history)
    base = last_record(history)
    regs = regressions(record_line(payload), base) if args.compare else []
    payload["baseline"] = base["date"] if base else None
    payload["regressions"] = regs
    payload["status"] = "regressed" if regs else "pass"
    if args.record:
        with history.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record_line(payload), sort_keys=True) + "\n")
        write_doc(Path(args.doc), history)
    return payload, args.out


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out = run(argv)
    except Exception as exc:  # noqa: BLE001 - contract: any error -> exit 2
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if payload["status"] == "regressed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
