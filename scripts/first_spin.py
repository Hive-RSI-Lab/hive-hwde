#!/usr/bin/env python
"""first_spin.py - is every first-spin review finding answered?

reference/first-spin.md is the practice: one reviewer per domain writes
`<dir>/<domain>.json`, the run's worker answers each finding in
`<dir>/dispositions.json` and resolves the known unknowns in
`<dir>/resolutions.json`. This script reads them all and says what is
still open, so a board goes to the order with nothing open, or with the
open items in front of the person. A report the agent weighs; not a gate.

<domain>.json (the reviewer's):
  {"domain": "power",
   "findings": [{"id": "POWER-1", "board": "core", "severity":
                 "blocker|major|minor|note", "refs": ["Q1"], "nets": ["PVDD"],
                 "finding": "...", "evidence": "calc / sim / datasheet ref",
                 "fix": "...", "margin": "...",
                 "fault_class": "PI-07 | new", "class_mode": "<when new>",
                 "caught_by": "<hwde check that flagged it, if one did>"}],
   "known_unknowns": [{"fact": "...", "board": "core", "refs": ["U1"],
                       "status": "verified|unverified|wrong", "source": "...",
                       "date": "YYYY-MM-DD", "could_stop_board": true}],
   "tools_wanted": [{"tool": "...", "checks": "...", "why": "..."}]}

dispositions.json (the worker's), by finding id, either form:
  {"POWER-1": {"disposition": "fixed", "how": "..."}}
  {"POWER-1": ["fixed", "..."]}
  disposition: fixed | contingency | firmware | justified | order | enclosure

resolutions.json (optional), by known unknown `<domain>:<index>` (0-based
in that domain's list) or its own `id`:
  {"power:1": {"status": "verified|resolved", "how": "datasheet V2.0 t.8.1.1"}}

Open, worst first:
  undisposed     a blocker or major with no disposition
  no_how         a disposition without its `how` (the margin, the part, the
                 firmware requirement)
  bad_disposition  a disposition word not in the list above
  unknown_open   a could_stop_board fact still unverified (or wrong) and not
                 resolved
  dangling       a disposition for a finding no review has
  unclassed      a blocker or major with no fault_class
Minor and note findings may go without a disposition (listed, not open).

Fault classes: each finding names the class of mistake it is, an id in
docs/failure-modes.yaml (the module's registry: mode, the check that
covers it and how well) or "new" with a one-line class_mode. Each class
the review found gets a verdict against that registry:
  caught       every finding in it was flagged by a check (caught_by)
  check_blind  the registry names a check for it, but the reviewer found it
               anyway: the check missed this board
  no_check     the class is known and nothing checks it
  new_class    not in the registry yet (an id it doesn't have is new too)
Every check_blind, no_check and new_class class with a blocker or major
in it (--brief-severity minor: or a minor) is a draft brief for hive-hwde (`briefs`: title, done, known), so
the next board is checked by a machine for what this one needed a person
for. The schematic and board reviewers' files (reports/review-*.json,
violations with an optional fault_class) join the class count too.

Facts: per-domain counts, the open list, the tools wanted merged across
domains (each is a candidate hive-hwde brief), and with --md a table of
every finding and what was done (the hifi-receiver-2 dispositions.md
shape).

CLI:
  first_spin.py --workspace boards/<b> [--dir reports/first-spin]
                [--md reports/first-spin/dispositions.md] [--out F]
  first_spin.py --dir <review dir> ...
  [--modes docs/failure-modes.yaml] [--briefs F (the draft briefs, JSON)]
Exit 0 nothing open / 1 open items / 2 error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402

SCRIPT = "first_spin"
DEFAULT_DIR = "reports/first-spin"
SEVERITIES = ("blocker", "major", "minor", "note")
MUST_ANSWER = ("blocker", "major")
DISPOSITIONS = ("fixed", "contingency", "firmware", "justified", "order",
                "enclosure")
NOT_REVIEWS = ("dispositions.json", "resolutions.json")
# the module's failure-mode registry, two levels above the skill
MODES = SCRIPTS.parent / "docs" / "failure-modes.yaml"
# the in-pipeline reviewers' files (agents/schematic-reviewer.md,
# verify-reviewer.md): classes only, they are answered at H2/H4
OTHER_REVIEWS = ("reports/review-schematic.json", "reports/review-board.json")
REVIEWER_SEVERITY = {"error": "blocker", "warning": "minor"}


def load_modes(path: Path) -> dict:
    """id -> {mode, script, level} from docs/failure-modes.yaml; {} when it
    isn't there (the skill run outside the module)."""
    if not path.is_file():
        return {}
    import yaml
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = {}
    for m in (doc.get("modes") if isinstance(doc, dict) else doc) or []:
        if isinstance(m, dict) and m.get("id"):
            cov = m.get("coverage") if isinstance(m.get("coverage"), dict) else {}
            out[str(m["id"])] = {"mode": m.get("mode") or "",
                                 "script": str(cov.get("script") or "-"),
                                 "level": str(cov.get("level") or "L0")}
    return out


def has_check(m: dict) -> str | None:
    """The check script a registry entry names, when it exists here and the
    entry rates it above L0."""
    script = m["script"].split()[0].strip(";,") if m["script"].split() else "-"
    if script in ("-", "") or m["level"] == "L0":
        return None
    name = script if script.endswith(".py") else script + ".py"
    return script if (SCRIPTS / name).is_file() else None


def reviewer_findings(ws: Path) -> list[dict]:
    """The schematic/board reviewers' violations, as findings."""
    out = []
    for rel in OTHER_REVIEWS:
        p = ws / rel
        if not p.is_file():
            continue
        data = checklib.load_json(p, "review")
        for i, v in enumerate((data or {}).get("violations") or []):
            out.append({"id": v.get("id") or f"{p.stem}:{i}",
                        "board": ws.name,
                        "severity": REVIEWER_SEVERITY.get(v.get("severity"),
                                                          "note"),
                        "finding": v.get("msg") or "",
                        "fault_class": v.get("fault_class"),
                        "class_mode": v.get("class_mode"),
                        "caught_by": v.get("caught_by"),
                        "_file": rel})
    return out


def classify(reviews: list[dict], extra: list[dict], modes: dict,
             brief_from: str = "major") -> dict:
    """Group findings by fault class and judge each against the registry."""
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    groups: dict[str, dict] = {}
    unclassed = []
    found = [(r["domain"], r.get("_file"), f) for r in reviews
             for f in r["findings"]]
    found += [("review", f["_file"], f) for f in extra]
    for dom, src, f in found:
        sev = f.get("severity") if f.get("severity") in SEVERITIES else "note"
        fid = f.get("id") or f"{dom.upper()}-?"
        cls = str(f.get("fault_class") or "").strip()
        if not cls:
            unclassed.append({"id": fid, "severity": sev,
                              "msg": (f.get("finding") or "")[:200]})
            continue
        if cls.lower() == "new" or cls not in modes:
            mode = str(f.get("class_mode") or f.get("finding") or "").strip()
            key = "new:" + " ".join(mode.lower().split())[:80]
            entry = {"class": cls if cls.lower() != "new" else None,
                     "mode": mode, "check": None}
        else:
            key, entry = cls, {"class": cls, "mode": modes[cls]["mode"],
                               "check": has_check(modes[cls]),
                               "level": modes[cls]["level"]}
        g = groups.setdefault(key, {**entry, "findings": [], "boards": [],
                                    "worst": "note"})
        g["findings"].append({"id": fid, "domain": dom, "file": src,
                              "board": f.get("board"), "severity": sev,
                              "finding": f.get("finding") or "",
                              "evidence": f.get("evidence") or "",
                              "caught_by": f.get("caught_by")})
        if f.get("board") and f["board"] not in g["boards"]:
            g["boards"].append(f["board"])
        if sev_rank[sev] < sev_rank[g["worst"]]:
            g["worst"] = sev
    classes = []
    for key, g in groups.items():
        if key.startswith("new:"):
            g["verdict"] = "new_class"
        elif all(x["caught_by"] for x in g["findings"]):
            g["verdict"] = "caught"
        elif g["check"]:
            g["verdict"] = "check_blind"
        else:
            g["verdict"] = "no_check"
        classes.append(g)
    classes.sort(key=lambda g: (sev_rank[g["worst"]], g["verdict"],
                                g["class"] or g["mode"]))
    return {"classes": classes, "unclassed": unclassed,
            "briefs": [draft_brief(g) for g in classes
                       if g["verdict"] != "caught"
                       and sev_rank[g["worst"]] <= sev_rank[brief_from]]}


def draft_brief(g: dict) -> dict:
    """A hive-hwde brief for a class the review needed a person to find."""
    ids = ", ".join(x["id"] for x in g["findings"])
    boards = ", ".join(g["boards"]) or "the board"
    mode = " ".join(str(g["mode"]).split())
    short = mode if len(mode) <= 90 else mode[:87] + "..."
    if g["verdict"] == "check_blind":
        title = f"{g['check']} misses {short} ({g['class']}) on {boards}"
        done = (f"{g['check']} flags {boards}'s {ids} (each as the review "
                "found it), with a test made from this case; "
                f"docs/failure-modes.yaml {g['class']} keeps or raises its level.")
    elif g["verdict"] == "no_check":
        title = f"hwde catches {short} ({g['class']})"
        done = (f"A check an agent runs (in verify_all through checks.d, or a "
                f"script) fires on {boards}'s {ids}, with a test made from this "
                f"case; docs/failure-modes.yaml {g['class']} names it and its "
                "level above L0.")
    else:
        title = f"hwde catches {short}"
        done = (f"docs/failure-modes.yaml has this class with an id, and a check "
                f"an agent runs fires on {boards}'s {ids}, with a test made from "
                "this case.")
    known = "; ".join(
        f"{x['id']} ({x['severity']}, {x['board']}, {x['file']}): "
        f"{' '.join(x['finding'].split())[:300]}"
        + (f" Evidence: {' '.join(str(x['evidence']).split())[:200]}"
           if x["evidence"] else "")
        for x in g["findings"])
    return {"title": title, "done": done,
            "known": "A cold design review found it by hand (first_spin.py). "
                     + known,
            "class": g["class"], "verdict": g["verdict"]}


def load_reviews(d: Path) -> list[dict]:
    out = []
    for p in sorted(d.glob("*.json")):
        if p.name in NOT_REVIEWS:
            continue
        data = checklib.load_json(p, "review")
        if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
            continue        # not a domain review (a sim report, a cache)
        data.setdefault("domain", p.stem)
        data["_file"] = p.name
        out.append(data)
    if not out:
        raise checklib.CheckError(
            f"no domain review in {d} (a <domain>.json with a findings list)")
    return out


def norm_disposition(v) -> dict:
    if isinstance(v, (list, tuple)):
        return {"disposition": v[0] if v else None,
                "how": v[1] if len(v) > 1 else ""}
    if isinstance(v, dict):
        return {"disposition": v.get("disposition"), "how": v.get("how") or ""}
    return {"disposition": v, "how": ""}


def analyze(reviews: list[dict], disp: dict, res: dict,
            modes: dict | None = None, extra: list[dict] | None = None,
            brief_from: str = "major") -> dict:
    disp = {k: norm_disposition(v) for k, v in (disp or {}).items()}
    open_: list[dict] = []
    rows: list[dict] = []
    counts: dict[str, dict] = {}
    ids: set[str] = set()
    tools: dict[str, dict] = {}
    for r in reviews:
        dom = r["domain"]
        c = counts.setdefault(dom, {s: 0 for s in SEVERITIES})
        for f in r["findings"]:
            fid = f.get("id") or f"{dom.upper()}-?"
            sev = f.get("severity") if f.get("severity") in SEVERITIES else "note"
            c[sev] += 1
            ids.add(fid)
            d = disp.get(fid)
            row = {"id": fid, "board": f.get("board"), "severity": sev,
                   "finding": f.get("finding") or "",
                   "disposition": (d or {}).get("disposition"),
                   "how": (d or {}).get("how") or ""}
            rows.append(row)
            if d is None:
                if sev in MUST_ANSWER:
                    open_.append({"kind": "undisposed", "id": fid,
                                  "severity": sev, "msg": row["finding"][:200]})
                continue
            if d["disposition"] not in DISPOSITIONS:
                open_.append({"kind": "bad_disposition", "id": fid,
                              "severity": sev,
                              "msg": f"{d['disposition']!r} is not one of "
                                     f"{', '.join(DISPOSITIONS)}"})
            elif not str(d["how"]).strip():
                open_.append({"kind": "no_how", "id": fid, "severity": sev,
                              "msg": f"{d['disposition']} with no how"})
        for i, k in enumerate(r.get("known_unknowns") or []):
            if not k.get("could_stop_board") or k.get("status") == "verified":
                continue
            rv = (res or {}).get(k.get("id") or "") or \
                (res or {}).get(f"{dom}:{i}") or {}
            if rv.get("status") in ("verified", "resolved") and \
                    str(rv.get("how") or "").strip():
                continue
            open_.append({"kind": "unknown_open", "id": k.get("id") or f"{dom}:{i}",
                          "severity": "blocker" if k.get("status") == "wrong"
                          else "major", "msg": (k.get("fact") or "")[:200]})
        for t in r.get("tools_wanted") or []:
            name = str(t.get("tool") or "").strip()
            if not name:
                continue
            e = tools.setdefault(name, {"tool": name, "checks": t.get("checks"),
                                        "why": t.get("why"), "domains": []})
            e["domains"].append(dom)
    for fid in sorted(set(disp) - ids):
        open_.append({"kind": "dangling", "id": fid, "severity": "note",
                      "msg": "a disposition for a finding no review has"})
    cl = classify(reviews, extra or [], modes or {}, brief_from)
    open_ += [{"kind": "unclassed", **u} for u in cl["unclassed"]
              if u["severity"] in MUST_ANSWER]
    rank = {"undisposed": 0, "bad_disposition": 1, "no_how": 2,
            "unknown_open": 3, "unclassed": 4, "dangling": 5}
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    open_.sort(key=lambda o: (sev_rank.get(o["severity"], 9),
                              rank[o["kind"]], o["id"]))
    rows.sort(key=lambda r: (sev_rank[r["severity"]], r["id"]))
    return {"counts": counts, "open": open_, "findings": rows,
            "tools_wanted": sorted(tools.values(), key=lambda t: t["tool"]),
            "classes": cl["classes"], "briefs": cl["briefs"]}


def render_md(res: dict) -> str:
    def cell(s) -> str:
        s = " ".join(str(s or "").split()).replace("|", "/")
        return s if len(s) <= 300 else s[:297] + "..."
    lines = ["# Every first-spin finding and what was done about it", "",
             "Written by `first_spin.py` from the domain reviews and "
             "dispositions.json.", "",
             "| Finding | Board | Severity | What it was | Disposition | How, with the margin |",
             "|---|---|---|---|---|---|"]
    for r in res["findings"]:
        lines.append(f"| {r['id']} | {cell(r['board'])} | {r['severity']} | "
                     f"{cell(r['finding'])} | {r['disposition'] or '-'} | "
                     f"{cell(r['how'])} |")
    if res["open"]:
        lines += ["", "## Still open", ""]
        lines += [f"- {o['id']} ({o['kind']}, {o['severity']}): {cell(o['msg'])}"
                  for o in res["open"]]
    if res["tools_wanted"]:
        lines += ["", "## Tools wanted", ""]
        lines += [f"- **{cell(t['tool'])}** ({', '.join(t['domains'])}): "
                  f"{cell(t['checks'])}" for t in res["tools_wanted"]]
    if res.get("classes"):
        lines += ["", "## Fault classes", "",
                  "| Class | Mode | Worst | Verdict | Check | Findings |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| {g['class'] or 'new'} | {cell(g['mode'])} | {g['worst']} | "
                  f"{g['verdict']} | {g['check'] or '-'} | "
                  f"{', '.join(x['id'] for x in g['findings'])} |"
                  for g in res["classes"]]
    return "\n".join(lines) + "\n"


def run(a) -> dict:
    if a.dir:
        d = Path(a.dir)
        if not d.is_absolute() and a.workspace:
            d = Path(a.workspace) / d
    elif a.workspace:
        d = Path(a.workspace) / DEFAULT_DIR
    else:
        raise checklib.CheckError("give --workspace or --dir")
    if not d.is_dir():
        raise checklib.CheckError(f"no review dir {d}: reference/first-spin.md "
                                  "says what goes there")
    reviews = load_reviews(d)
    dp, rp = d / "dispositions.json", d / "resolutions.json"
    disp = checklib.load_json(dp, "dispositions") if dp.is_file() else {}
    res = checklib.load_json(rp, "resolutions") if rp.is_file() else {}
    extra = reviewer_findings(Path(a.workspace)) if a.workspace else []
    out = analyze(reviews, disp, res, load_modes(Path(a.modes) if a.modes
                                                  else MODES), extra,
                  getattr(a, "brief_severity", "major"))
    if a.briefs:
        Path(a.briefs).write_text(json.dumps(out["briefs"], indent=1) + "\n",
                                  encoding="utf-8")
    if a.md:
        Path(a.md).write_text(render_md(out), encoding="utf-8", newline="\n")
    return {"script": SCRIPT, "status": "open" if out["open"] else "pass",
            "dir": d.as_posix(), "domains": [r["domain"] for r in reviews],
            "md": a.md, **out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace")
    ap.add_argument("--dir", help=f"review dir (default <ws>/{DEFAULT_DIR})")
    ap.add_argument("--md", help="write the dispositions table here")
    ap.add_argument("--modes", help="failure-mode registry (default the "
                    "module's docs/failure-modes.yaml)")
    ap.add_argument("--briefs", help="write the draft hive-hwde briefs here")
    ap.add_argument("--brief-severity", choices=SEVERITIES, default="major",
                    help="draft briefs for classes this bad or worse "
                    "(default major; minor files a check blind on a minor too)")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    checklib.utf8_stdout()
    try:
        payload = run(a)
    except Exception as exc:  # noqa: BLE001  (contract: any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(payload, indent=1)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 1 if payload["open"] else 0


if __name__ == "__main__":
    sys.exit(main())
