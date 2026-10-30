"""hwde score: what this checkout's gates find on boards held fixed.

    hwde score [--board NAME ...] [--keep DIR]

docs/score.md is the definition. Each pinned board (pinned/*.json: the hi-fi
receiver's boards as one commit of hifi-receiver-2 had them, and this repo's
hwde-led-test) is copied to a scratch dir, and erc, drc_routed, verify and
dfm run on it through bin/hwde, with bin/hwde's shims off. A charge is each
finding a gate fails on that no waiver covers, each warning, and each
board-local script that stands in for hwde. A gate that can't run charges
CRASH. Each of hwde's planted faults (bin/strength.py) that a gate or check
lets through charges CRASH too, so loosening a check can't raise the score.
The score is minus the charges: higher is better, 0 is a clean sweep.
Prints one line per board, the planted faults' line, then the number alone.

The boards change only through bin/pin.py, which writes their digests into
PINS below, so a refresh is a change to this file; a pin whose files don't
match its digest stops the score.
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
    "core": "3b0d25faaf2150e3627a49175e50f6edfcb18be0b1547a47095549899147b149",
    "hwde-led-test": "19e9d019f0f73784ddbc3d32fec3d2e3dccb70111ccb45250381a12db4c7af2c",
    "lite-main": "7b5f6f4f3a1b3fc8fa9fbd98a7375c74adc2db34997f6efd52f6fbaaaf15a995",
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
    return out


def pins() -> list[Pin]:
    out = [read_pin(p) for p in sorted((ROOT / "pinned").glob("*.json"))]
    for name in sorted(set(PINS) - {p.name for p in out}):
        raise ValueError(f"bin/score.py pins {name}, but pinned/{name}.json is gone")
    return out


def script_charges(pin: Pin) -> list[str]:
    """Every stand-in, and every script not yet classified."""
    return sorted(pin.stand_in) + sorted(pin.unclassified)


def gate_charges(result: dict, waivers: list[dict] | None = None) -> Counter:
    """The findings one gate result charges, by check: those it fails on that
    no waiver covered (the gate already took those out), and every warning no
    waiver covers."""
    import gate  # the skill's: its waiver matching
    out: Counter = Counter()

    def finding(v) -> dict:  # some gates report a finding as a plain string
        return v if isinstance(v, dict) else {"message": str(v)}

    def key(v: dict) -> str:
        return v.get("check") or v.get("kind") or v.get("source") or "?"
    for v in map(finding, result.get("failing") or []):
        out[key(v)] += 1
    warns = result.get("warnings") or []
    for v in map(finding, warns):
        if not any(gate.waiver_matches(w, v) for w in waivers or []):
            out[key(v)] += 1
    # warnings past the gate's cap travel only as a count
    out["(warnings past the cap)"] += max(0, int(result.get("warnings_count") or 0) - len(warns))
    return +out


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


def run_gate(pin: Pin, ws: Path, g: str, logs: Path, tries: int = 3) -> tuple[int, str]:
    """One gate's charges. A gate that can't run is tried again (on a loaded
    machine kicad-cli's version probe can time out); one that still can't
    charges CRASH, unless it is the toolchain, which raises NoToolchain."""
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
            c = gate_charges(result, waivers_of(ws) if g == "verify" else None)
            k = sum(c.values())
            return k, f"{k}" + (f" ({', '.join(f'{a} {b}' for a, b in c.most_common())})" if c else "")
    said = log.read_text(errors="replace")
    if "EnvError" in said:
        raise NoToolchain(f"{pin.name} {g}: {said.strip().splitlines()[-1][-300:]}")
    return CRASH, f"could not run (exit {rc}, CRASH {CRASH}; {log.name})"


def measure(p: Pin, ws: Path, logs: Path) -> int:
    """One board's charges, printed as its line."""
    shutil.rmtree(ws, ignore_errors=True)
    shutil.copytree(p.dir, ws, ignore=shutil.ignore_patterns("__pycache__"))
    parts, n = [], 0
    for g in GATES:
        t = time.monotonic()
        c, said = run_gate(p, ws, g, logs)
        n += c
        parts.append(f"{g} {said} [{time.monotonic() - t:.0f}s]")
    sc = script_charges(p)
    n += len(sc)
    parts.append(f"scripts {len(sc)}" + (f" ({', '.join(sc)})" if sc else ""))
    print(f"{p.name} ({p.project} {p.commit[:7]}): {n} charged: {'; '.join(parts)}", flush=True)
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
        total = sum(measure(p, scratch / p.name, logs) for p in todo)
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
