"""hwde's planted faults: the share its own gates and checks still catch.

    python bin/strength.py        (the venv's python; bin/score.py calls it)

The score (bin/score.py) charges what hwde's gates find on pinned boards.
A check made looser finds less, so on its own that number could rise by
loosening hwde's checks. This is the other half: faults planted where each
gate's verdict comes from, run through the same code the gates run, offline
and in seconds. Each fault that gets through charges CRASH, as a gate that
can't run does: a check that misses a fault it was built to catch isn't
measuring the boards, so loosening one costs more than the findings it
could hide (docs/score.md, "The planted faults").

  gate    reference/gates.yaml through gate.evaluate: each of the gates the
          boards pass fails a report carrying one finding of each severity
          that gate fails on. FAILS_ON pins those severities: a gate that
          lets a planted error (or, for erc and drc_routed, a warning)
          through is a miss.
  verify  tests/golden's mutants, through tests/golden's scorecard
          (score_checks: each mutant run through the one check that owns
          it). Every check in the verify suite (verify_all.load_checks)
          must own at least one mutant, and a mutant whose check left the
          suite is a miss, so taking a check or its fault out costs too.
  dfm     the bench's P9 fixtures with a known answer (tests/fixtures/
          stages), and faults planted at JLC's published floors in a copy
          of pd-trigger's shipped drill file (DRILL_FAULTS): a hole closer
          to its neighbour than 0.5 mm, a drill under 0.3 mm. A floor in
          jlc_capabilities.yaml lowered past them lets the fault through.

ERC and DRC themselves are KiCad's; their planted faults would need
kicad-cli and are not here (the gate leg still holds what hwde decides
about their reports).

faults() gives (gate step, fault, caught) for each; run alone it prints one
line per fault and exits 1 when one got through.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".claude" / "skills" / "hwde" / "scripts"
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / "lib")]

# The severities each gate the score counts must fail on (SPEC P4: ERC
# "must be clean"; drc_routed fails on warnings since S3).
FAILS_ON = {"erc": ("error", "warning"), "drc_routed": ("error", "warning"),
            "verify": ("error",), "dfm": ("error",)}
# (name, kind dfm must report, fault): faults at JLC's published floors
# (hole edge to hole edge 0.5 mm, drill 0.3 mm), planted in pd-trigger's
# shipped drill file. "pair" is a hole beside an existing one, "lone" a
# hole of its own, each away from every other hole.
DRILL_FAULTS = (
    ("hole-to-hole-0.45", "dfm_hole_to_hole", {"pair": 0.45}),
    ("drill-0.25", "dfm_hole_size", {"lone": 0.25}),
)
DRILL_BOARD = "pd_trigger_dfm"
_HIT = re.compile(r"^X(-?[\d.]+)Y(-?[\d.]+)$")
_TOOL = re.compile(r"^T(\d+)C([\d.]+)$")


def gate_faults() -> list[tuple[str, str, bool]]:
    import gate
    gates = gate.load_gates(gate.DEFAULT_GATES)
    out = []
    for name, sevs in FAILS_ON.items():
        g = gates.get(name)
        for sev in sevs:
            caught = False
            if g is not None:
                v = {"check": g.get("tool"), "severity": sev, "pos": [0, 0],
                     "layer": None, "net": None, "refs": [], "msg": "planted",
                     "source": g.get("tool"), "kind": "planted"}
                caught = gate.evaluate(name, g, {"violations": [v]})["status"] == "fail"
            out.append((name, f"gate {name}: one {sev}", caught))
    return out


def verify_faults() -> list[tuple[str, str, bool]]:
    import score_checks
    import verify_all
    checks = verify_all.load_checks()
    names = [c["name"] for c in checks]
    manifest = score_checks.load_manifest()
    out = []
    for m, rec in manifest["mutants"].items():
        if rec["check"] not in names and rec["check"] != "dfm_check":
            out.append(("verify", f"verify {m}: its check {rec['check']} is not in the suite", False))
    jobs = score_checks.mutant_jobs(manifest, names)
    res = score_checks.score(jobs, [], 4, checks)
    missed = {(f["check"], f["item"]) for f in res["findings"]
              if f.get("verdict") in ("miss", "error")}
    for j in jobs:
        out.append(("verify", f"verify {j['item']} ({j['target']})",
                    (j["target"], j["item"]) not in missed))
    owned = {j["target"] for j in jobs}
    for n in names:
        if n not in owned:
            out.append(("verify", f"verify {n}: no planted fault", False))
    return out


def _dfm(fx: dict, gerbers: Path) -> list[dict]:
    import dfm_check
    files = {k: ROOT / r["path"] for k, r in fx["files"].items()}
    args = fx.get("args") or {}
    return dfm_check.run(files["pcb"], fab_dir=gerbers,
                         copper_oz=float(args.get("copper_oz", 1.0)),
                         netlist=files.get("netlist"))["violations"]


def _plant(drl: str, fault: dict) -> str:
    """drl with one planted hole: a new tool, and a hit placed so its gap to
    every hole but the one it is planted beside is over 2 mm."""
    lines = drl.splitlines()
    tools, hits, tool = {}, [], None
    for ln in lines:
        if (t := _TOOL.match(ln)):
            tools[t.group(1)] = float(t.group(2))
        elif ln.startswith("T") and ln[1:].isdigit():
            tool = ln.strip()[1:]
        elif (h := _HIT.match(ln)) and tool in tools:
            hits.append((float(h.group(1)), float(h.group(2)), tools[tool]))
    if not hits:
        raise ValueError("drill file has no hits")

    def free(x, y, d, but=None):
        return all(((x - hx) ** 2 + (y - hy) ** 2) ** .5 - (d + hd) / 2 > 2.0
                   for i, (hx, hy, hd) in enumerate(hits) if i != but)
    xs, ys = [h[0] for h in hits], [h[1] for h in hits]
    spot = None
    if "pair" in fault:
        for i, (hx, hy, hd) in enumerate(hits):
            r = hd + fault["pair"]           # same drill: centre to centre
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                x, y = hx + dx * r, hy + dy * r
                if free(x, y, hd, but=i):
                    spot, dia = (x, y), hd
                    break
            if spot:
                break
    else:
        dia = fault["lone"]
        n = 40
        for a in range(1, n):
            for b in range(1, n):
                x = min(xs) + (max(xs) - min(xs)) * a / n
                y = min(ys) + (max(ys) - min(ys)) * b / n
                if free(x, y, dia):
                    spot = (x, y)
                    break
            if spot:
                break
    if spot is None:
        raise ValueError(f"no room to plant {fault}")
    t = str(max(int(k) for k in tools) + 1)
    head = lines.index("%")
    end = max(i for i, ln in enumerate(lines) if ln.strip() == "M30")
    body = [f"T{t}", f"X{spot[0]:.3f}Y{spot[1]:.3f}"]
    lines = lines[:head] + [f"T{t}C{dia:.3f}"] + lines[head:end] + body + lines[end:]
    return "\n".join(lines) + "\n"


def dfm_faults() -> list[tuple[str, str, bool]]:
    import benchlib
    import yaml
    stages = ROOT / "tests" / "fixtures" / "stages"
    fixtures = yaml.safe_load((stages / "manifest.yaml").read_text(encoding="utf-8"))["fixtures"]
    out = []
    for fid, fx in fixtures.items():
        if fx.get("stage") != "P9" or not fx.get("known_answer"):
            continue
        vs = _dfm(fx, ROOT / fx["dirs"]["gerbers"]["path"])
        ka = benchlib.match_known_answer(fx["known_answer"], vs)
        out.append(("dfm", f"dfm {fid}", ka["status"] == "ok"))
    fx = fixtures[DRILL_BOARD]
    src = ROOT / fx["dirs"]["gerbers"]["path"]

    def count(vs, kind):
        return sum(v.get("kind") == kind and v.get("severity") == "error" for v in vs)
    clean = _dfm(fx, src)
    with tempfile.TemporaryDirectory(prefix="hwde-strength-") as tmp:
        for name, kind, fault in DRILL_FAULTS:
            work = Path(tmp) / name
            shutil.copytree(src, work)
            drl = next(work.glob("*.drl"))
            drl.write_text(_plant(drl.read_text(encoding="utf-8"), fault), encoding="utf-8")
            out.append(("dfm", f"dfm {name} ({kind})", count(_dfm(fx, work), kind) > count(clean, kind)))
    return out


def faults() -> list[tuple[str, str, bool]]:
    """(gate step, fault, caught) for every planted fault. A leg that can't
    run raises: the score then can't be read, rather than read as caught."""
    return gate_faults() + verify_faults() + dfm_faults()


if __name__ == "__main__":
    fs = faults()
    for step, name, ok in fs:
        print(f"{'caught' if ok else 'MISSED'}  {step:10} {name}")
    print(f"planted faults caught: {sum(ok for *_, ok in fs)}/{len(fs)}")
    sys.exit(0 if all(ok for *_, ok in fs) else 1)
