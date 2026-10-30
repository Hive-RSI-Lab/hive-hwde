"""hwde score: what this checkout's gates get wrong on boards held fixed.

    hwde score [--board NAME ...] [--keep DIR] [--draft-known]

docs/score.md is the definition. Each pinned board (pinned/*.json: the hi-fi
receiver's boards as one commit of hifi-receiver-2 had them, and this repo's
hwde-led-test) is copied to a scratch dir, and erc, drc_routed, verify and
dfm run on it through bin/hwde, with bin/hwde's shims off. Each board has a
ledger of its known findings (pinned/known/BOARD.json): a finding the ledger
holds as a real defect of the board costs nothing, since hwde can't move it
on a frozen board; every other finding a gate fails on or warns of (one the
ledger holds false, or one no one has judged yet) charges 1, and so does
each real defect in the ledger no gate reports any more (a missed one). Each
board-local script that stands in for hwde charges 1. A gate that can't run
charges CRASH. Each of hwde's planted faults (bin/strength.py) that a gate or
check lets through charges CRASH too, so loosening a check can't raise the
score. The score is minus the charges: higher is better, 0 is a clean sweep.
Prints one line per board, the planted faults' line, then the number alone.
--draft-known adds each finding the ledger doesn't hold to it, unjudged
(verdict "?"), for someone to judge.

The boards and their ledgers change only through bin/pin.py, which writes
their digests into PINS below, so a refresh or a new verdict is a change to
this file; a pin whose files don't match its digest stops the score.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HWDE = os.environ.get("HWDE_BIN", str(ROOT / "bin" / "hwde"))
SKILL = ROOT / "scripts"
GATES = ("erc", "drc_routed", "verify", "dfm")
CRASH = 100

# The pinned boards and the sha256 of each one's pin and files (digest()).
# bin/pin.py writes this block; don't edit it by hand.
# pins:begin
PINS = {
    "core": "b10364d0c911a12f1fbe4a8d4d60b72c3672436031a152409dac43d204905e86",
    "hwde-led-test": "227282bf4c0694274a7b1c7490efa9de252c61e69037434a39fe2994d09643e0",
    "lite-main": "2ae6e0772c72a1f47829e1206fd38402908ed4b8d103c107dcda1b7014e3bd0c",
    "ultra-front": "bdf9553856f3b242020968be82be3ca450853052f502dd10ddcd291b99409ed5",
}
# pins:end


@dataclass
class Pin:
    name: str
    dir: Path
    project: str
    commit: str
    design: list[str] = field(default_factory=list)
    stand_in: dict[str, str] = field(default_factory=dict)
    unclassified: list[str] = field(default_factory=list)
    path: Path | None = None


def read_pin(path: Path) -> Pin:
    d = json.loads(path.read_text(encoding="utf-8"))
    return Pin(d.get("board") or path.stem, ROOT / d["dir"], d.get("project", ""),
               d.get("commit", ""), list(d.get("design", [])),
               dict(d.get("stand_in", {})), list(d.get("unclassified", [])), path)


def files(d: Path) -> list[str]:
    return sorted(str(p.relative_to(d)) for p in d.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


def scripts(d: Path) -> list[str]:
    """The board-local scripts: every .py and .sh in the board's files."""
    return [f for f in files(d) if f.endswith((".py", ".sh"))]


def digest(pin: Pin) -> str:
    h = hashlib.sha256()
    h.update(pin.path.read_bytes() if pin.path else b"")
    kp = known_path(pin)
    if kp.is_file():
        h.update(b"known\0" + kp.read_bytes())
    for f in files(pin.dir):
        h.update(f.encode() + b"\0" + hashlib.sha256((pin.dir / f).read_bytes()).digest())
    return h.hexdigest()


def problems(pin: Pin) -> list[str]:
    """What makes a pin unfit to score: files changed since bin/pin.py stamped
    it, or a script list that doesn't match the board's scripts."""
    out = []
    if not (pin.dir / "state.json").is_file():
        return [f"{pin.name}: {pin.dir.relative_to(ROOT)} has no state.json"]
    if PINS.get(pin.name) != digest(pin):
        out.append(f"{pin.name}: its files or pin differ from the digest in bin/score.py "
                   f"(re-pin with bin/hwde pin, or put them back)")
    have = set(scripts(pin.dir))
    listed = pin.design + list(pin.stand_in) + pin.unclassified
    for s, n in Counter(listed).items():
        if n > 1:
            out.append(f"{pin.name}: {s} is classified twice")
    for s in sorted(set(listed) - have):
        out.append(f"{pin.name}: {s} is classified but not in the board")
    for s in sorted(have - set(listed)):
        out.append(f"{pin.name}: {s} is in the board but not classified")
    try:
        known = read_known(pin)
    except (OSError, ValueError) as e:
        return out + [f"{pin.name}: {known_path(pin).relative_to(ROOT)} unreadable: {e}"]
    for i, e in enumerate(known):
        if e.get("gate") not in GATES or e.get("verdict", "?") not in VERDICTS:
            out.append(f"{pin.name}: known[{i}] needs a gate of {GATES} and a verdict of {VERDICTS}")
        elif e.get("verdict") in ("real", "false") and not str(e.get("reason") or "").strip():
            out.append(f"{pin.name}: known[{i}] is judged {e['verdict']} with no reason")
    return out


def pins() -> list[Pin]:
    out = [read_pin(p) for p in sorted((ROOT / "pinned").glob("*.json"))]
    for name in sorted(set(PINS) - {p.name for p in out}):
        raise ValueError(f"bin/score.py pins {name}, but pinned/{name}.json is gone")
    return out


def script_charges(pin: Pin) -> list[str]:
    """Every stand-in, and every script not yet classified."""
    return sorted(pin.stand_in) + sorted(pin.unclassified)


def finding_of(gate_name: str, v) -> dict:
    """A gate's finding as the ledger names it: the gate, the check, and
    where it is (net, refs, pos). The message stands in only for a finding
    with nothing else to name it by (some gates report a plain string), so
    a check that words or measures a finding better still matches it."""
    v = v if isinstance(v, dict) else {"message": str(v)}
    pos = v.get("pos")
    pos = [round(float(pos[0]), 3), round(float(pos[1]), 3)] \
        if isinstance(pos, (list, tuple)) and len(pos) >= 2 \
        and all(isinstance(x, (int, float)) for x in pos[:2]) else None
    f = {"gate": gate_name,
         "check": v.get("check") or v.get("kind") or v.get("source") or "?",
         "net": v.get("net"), "refs": sorted(map(str, v.get("refs") or [])), "pos": pos}
    if not (f["net"] or f["refs"] or pos):
        f["message"] = str(v.get("message") or v.get("description") or "")
    return f


def gate_findings(gate_name: str, result: dict, waivers: list[dict] | None = None
                  ) -> tuple[list[dict], int]:
    """The findings one gate result reports: those it fails on that no
    waiver covered (the gate already took those out), and every warning no
    waiver covers; and how many warnings sat past the gate's cap, which
    travel only as a count and so can't be named."""
    import gate  # the skill's: its waiver matching
    out = [finding_of(gate_name, v) for v in result.get("failing") or []]
    warns = result.get("warnings") or []
    for v in warns:
        if not (isinstance(v, dict) and any(gate.waiver_matches(w, v) for w in waivers or [])):
            out.append(finding_of(gate_name, v))
    return out, max(0, int(result.get("warnings_count") or 0) - len(warns))


def gate_charges(result: dict, waivers: list[dict] | None = None) -> Counter:
    """Every finding one gate result reports, by check (no ledger)."""
    fs, past = gate_findings("", result, waivers)
    out = Counter(f["check"] for f in fs)
    out["(warnings past the cap)"] += past
    return +out


# The ledger: pinned/known/<board>.json, {"known": [entry, ...]}. An entry
# is a finding as finding_of names it, with a verdict: "real" (a defect of
# the pinned board: costs nothing while a gate reports it, 1 once none
# does), "false" (hwde's mistake: costs 1 while reported) or "?" (not judged
# yet: costs 1 while reported, as any finding the ledger doesn't hold), and
# a reason.
VERDICTS = ("real", "false", "?")
POS_TOL_MM = 0.05


def known_path(pin: Pin) -> Path:
    return ROOT / "pinned" / "known" / f"{pin.name}.json"


def read_known(pin: Pin) -> list[dict]:
    p = known_path(pin)
    if not p.is_file():
        return []
    return list(json.loads(p.read_text(encoding="utf-8")).get("known") or [])


def matches(e: dict, f: dict) -> bool:
    """One ledger entry names this finding: same gate, check, net and refs,
    within POS_TOL_MM of its pos, and the same message where that is all
    either has."""
    if any(e.get(k) != f.get(k) for k in ("gate", "check", "net")):
        return False
    if sorted(e.get("refs") or []) != f["refs"]:
        return False
    ep, fp = e.get("pos"), f["pos"]
    if (ep is None) != (fp is None):
        return False
    if ep is not None and (abs(ep[0] - fp[0]) > POS_TOL_MM or abs(ep[1] - fp[1]) > POS_TOL_MM):
        return False
    return ("message" not in e and "message" not in f) or e.get("message") == f.get("message")


@dataclass
class Judged:
    """One board's findings against its ledger."""
    real: list[dict] = field(default_factory=list)        # known defects, reported
    missed: list[dict] = field(default_factory=list)      # known defects, not reported
    false: list[dict] = field(default_factory=list)       # known false, reported
    unjudged: list[dict] = field(default_factory=list)    # reported, verdict "?" or none

    @property
    def charges(self) -> int:
        return len(self.missed) + len(self.false) + len(self.unjudged)


def judge(found: list[dict], known: list[dict], ran: set[str] | None = None) -> Judged:
    """Pair each finding with at most one ledger entry (an entry is one
    finding, so two alike need two entries). A real entry of a gate that
    didn't run (`ran` leaves it out) isn't missed: that gate charged CRASH."""
    j = Judged()
    left = list(known)
    for f in found:
        e = next((e for e in left if matches(e, f)), None)
        if e is not None:
            left.remove(e)
        v = (e or {}).get("verdict", "?")
        (j.real if v == "real" else j.false if v == "false" else j.unjudged).append(f)
    j.missed = [e for e in left if e.get("verdict") == "real"
                and (ran is None or e.get("gate") in ran)]
    return j


def draft_known(pin: Pin, found: list[dict]) -> int:
    """Add each finding the ledger doesn't hold to it, verdict "?"; returns
    how many. Entries already there keep their verdict and reason."""
    known = read_known(pin)
    left = list(known)
    new = []
    for f in found:
        e = next((e for e in left if matches(e, f)), None)
        if e is not None:
            left.remove(e)
        else:
            new.append({**f, "verdict": "?", "reason": ""})
    if new:
        p = known_path(pin)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"board": pin.name, "known": known + new}, indent=1) + "\n",
                     encoding="utf-8")
    return len(new)


def waivers_of(ws: Path) -> list[dict]:
    import gate
    side = ws / "reports" / "verify-waivers.json"
    try:
        return gate.load_waivers(side) if side.is_file() else []
    except Exception:  # an unreadable sidecar waives nothing
        return []


def board_file(ws: Path, name: str, suffix: str) -> str:
    """kicad/<name><suffix>, or the one such file when the board is named
    otherwise."""
    p = ws / "kicad" / f"{name}{suffix}"
    if p.is_file():
        return str(p.relative_to(ws))
    found = sorted((ws / "kicad").glob(f"*{suffix}"))
    return str(found[0].relative_to(ws)) if len(found) == 1 else str(p.relative_to(ws))


class NoToolchain(Exception):
    """KiCad couldn't be found or started: says nothing about hwde."""


def run_gate(pin: Pin, ws: Path, g: str, logs: Path, tries: int = 3
             ) -> tuple[list[dict], int] | None:
    """One gate's findings and how many sat past its cap, or None when it
    can't run. A gate that can't run is tried again (on a loaded machine
    kicad-cli's version probe can time out), unless it is the toolchain,
    which raises NoToolchain."""
    inp = board_file(ws, pin.name, ".kicad_sch" if g == "erc" else ".kicad_pcb")
    out = logs / f"{pin.name}-{g}.json"
    log = logs / f"{pin.name}-{g}.log"
    for n in range(tries):
        if n:
            time.sleep(30)
        out.unlink(missing_ok=True)
        with log.open("w") as f:
            rc = subprocess.run([HWDE, "gate", "--gate", g, inp, "--workspace", ".",
                                 "--out", str(out)], cwd=ws, stdout=f,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
        try:
            result = json.loads(out.read_text())
        except (OSError, ValueError):
            result = None
        if rc in (0, 1) and result:
            return gate_findings(g, result, waivers_of(ws) if g == "verify" else None)
    said = log.read_text(errors="replace")
    if "EnvError" in said:
        raise NoToolchain(f"{pin.name} {g}: {said.strip().splitlines()[-1][-300:]}")
    print(f"{pin.name} {g}: could not run (exit {rc}, CRASH {CRASH}; {log.name})", flush=True)
    return None


def by_check(fs: list[dict]) -> str:
    c = Counter(f"{f['gate']}:{f['check']}" for f in fs)
    return ", ".join(f"{a} {b}" for a, b in c.most_common())


def measure(p: Pin, ws: Path, logs: Path, draft: bool = False) -> int:
    """One board's charges, printed as its line, and the findings it charges
    each on a line of their own (so they can be judged)."""
    shutil.rmtree(ws, ignore_errors=True)
    shutil.copytree(p.dir, ws, ignore=shutil.ignore_patterns("__pycache__"))
    found, ran, n, parts = [], set(), 0, []
    for g in GATES:
        t = time.monotonic()
        r = run_gate(p, ws, g, logs)
        if r is None:
            n += CRASH
            parts.append(f"{g} CRASH")
            continue
        fs, past = r
        found += fs
        ran.add(g)
        # warnings past the cap can't be named, so can't be judged: each charges
        n += past
        parts.append(f"{g} {len(fs)} found" + (f" + {past} past the cap" if past else "")
                     + f" [{time.monotonic() - t:.0f}s]")
    j = judge(found, read_known(p), ran)
    n += j.charges
    sc = script_charges(p)
    n += len(sc)
    parts.append(f"known defects {len(j.real)}/{len(j.real) + len(j.missed)} found")
    for name, fs in (("missed", j.missed), ("false", j.false), ("unjudged", j.unjudged)):
        if fs:
            parts.append(f"{name} {len(fs)} ({by_check(fs)})")
    parts.append(f"scripts {len(sc)}" + (f" ({', '.join(sc)})" if sc else ""))
    print(f"{p.name} ({p.project} {p.commit[:7]}): {n} charged: {'; '.join(parts)}", flush=True)
    for name, fs in (("missed", j.missed), ("false", j.false), ("unjudged", j.unjudged)):
        for f in fs:
            print(f"  {name}: {json.dumps({k: v for k, v in f.items() if k not in ('verdict', 'reason')})}")
    if draft:
        k = draft_known(p, found)
        print(f"  {k} finding(s) added to {known_path(p).relative_to(ROOT)} unjudged", flush=True)
    return n


def planted() -> list[str]:
    """The planted faults let through, each printed; then the line that
    counts them. Each one charges CRASH."""
    import strength
    fs = strength.faults()
    missed = [name for _, name, ok in fs if not ok]
    for name in missed:
        print(f"planted fault let through: {name} (CRASH {CRASH})", flush=True)
    print(f"planted faults caught: {len(fs) - len(missed)}/{len(fs)}", flush=True)
    return missed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", action="append", default=[], help="only this board (repeatable)")
    ap.add_argument("--keep", help="leave the scratch copies and gate logs here")
    ap.add_argument("--draft-known", action="store_true",
                    help="add each finding the ledger doesn't hold to pinned/known/BOARD.json, "
                         "unjudged (then judge them and re-stamp with bin/hwde pin --digests)")
    args = ap.parse_args()
    sys.path.insert(0, str(SKILL))
    try:
        todo = pins()
        bad = [m for p in todo for m in problems(p)]
    except (OSError, ValueError, KeyError) as e:
        bad = [str(e)]
    if bad or not todo:
        for m in bad or ["no pinned boards"]:
            print(f"score: {m}", file=sys.stderr)
        return 1
    try:
        missed = planted()
    except Exception as e:  # noqa: BLE001 - a guard that can't run is not passed
        print(f"score: the planted faults could not run: {type(e).__name__}: {e}",
              file=sys.stderr)
        return 1
    if args.board:
        todo = [p for p in todo if p.name in args.board]
    scratch = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="hwde-score-"))
    logs = scratch / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    try:
        total = sum(measure(p, scratch / p.name, logs, args.draft_known) for p in todo)
    except NoToolchain as e:
        print(f"score: can't measure, the toolchain didn't run: {e}", file=sys.stderr)
        return 1
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
    total += CRASH * len(missed)
    print(f"charges: {total} over {len(todo)} board(s)")
    print(f"{-total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
