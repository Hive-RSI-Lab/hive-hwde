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
                 "fix": "...", "margin": "..."}],
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
Minor and note findings may go without a disposition (listed, not open).

Facts: per-domain counts, the open list, the tools wanted merged across
domains (each is a candidate hive-hwde brief), and with --md a table of
every finding and what was done (the hifi-receiver-2 dispositions.md
shape).

CLI:
  first_spin.py --workspace boards/<b> [--dir reports/first-spin]
                [--md reports/first-spin/dispositions.md] [--out F]
  first_spin.py --dir <review dir> ...
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


def analyze(reviews: list[dict], disp: dict, res: dict) -> dict:
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
    rank = {"undisposed": 0, "bad_disposition": 1, "no_how": 2,
            "unknown_open": 3, "dangling": 4}
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    open_.sort(key=lambda o: (sev_rank.get(o["severity"], 9),
                              rank[o["kind"]], o["id"]))
    rows.sort(key=lambda r: (sev_rank[r["severity"]], r["id"]))
    return {"counts": counts, "open": open_, "findings": rows,
            "tools_wanted": sorted(tools.values(), key=lambda t: t["tool"])}


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
    out = analyze(reviews, disp, res)
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
