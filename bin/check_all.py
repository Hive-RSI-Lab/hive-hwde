"""hwde check-all: the hive-hwde test.

Re-runs every board's gate steps from a scratch copy of its workspace, so a
fresh clone stays as it was, and holds docs/workarounds.md to its probes.

    hwde check-all [--board NAME ...] [--no-probes] [--keep DIR]

Steps per board, each through bin/hwde (STEPS below): erc, drc_routed, verify
and dfm as `gate --gate <g>`, and fab as fab_export + bom_cpl (offline). A
board runs each gate its state.json records as passed, and fab once it has a
fab/ package: a board re-passes everything it once passed. Each probe in
tests/workarounds/ exits 0 while its cause stands, 10 once it's fixed. The
knowledge library passes `knowledge --validate --strict` against these
boards; it needs the source PDFs (HWDE_KNOWLEDGE_SOURCES, which bin/hwde fetches
into HOME first), and is skipped, said so, without them.

Exit 0 when every step passes and the table matches the probes, else 1.
Run by bin/hwde (venv python, HWDE_* pins, HWDE_BIN set).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402  (one reader of boards/ and docs/workarounds.md)

HWDE = os.environ.get("HWDE_BIN", str(HERE / "hwde"))
SKILL = ROOT / ".claude" / "skills" / "hwde" / "scripts"


def run(cmd: list[str], cwd: Path, log: Path, env: dict | None = None) -> int:
    with log.open("w") as f:
        return subprocess.run(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT,
                              stdin=subprocess.DEVNULL, env=env).returncode


def tail(log: Path, n: int = 8) -> str:
    lines = log.read_text(errors="replace").splitlines()
    return "\n".join("      " + l for l in lines[-n:])


def step(name: str, board: score.Board, ws: Path, logs: Path) -> tuple[bool, str]:
    pcb = f"kicad/{board.name}.kicad_pcb"
    log = logs / f"{board.name}-{name}.log"
    if name == "fab":
        out = ws / "fab-check"
        if run([HWDE, "fab_export", "--pcb", pcb, "--out-dir", str(out),
                "--out", str(logs / f"{board.name}-fab_export.json")], ws, log):
            return False, tail(log)
        if not list(out.glob("*_gerbers.zip")) and not list(out.glob("*.zip")):
            return False, "      fab_export wrote no gerber zip"
        rep = logs / f"{board.name}-bom_cpl.json"
        argv = [HWDE, "bom_cpl", "--pcb", pcb, "--out-dir", str(out),
                "--offline", "--no-price-lookup", "--out", str(rep)]
        if (ws / "parts" / "parts.json").is_file():
            argv += ["--parts", "parts/parts.json"]
        rc = run(argv, ws, log)
        if rc:
            try:
                r = json.loads(rep.read_text())
                why = "; ".join(v.get("message", v.get("kind", "?"))
                                for v in r.get("violations", []))
            except (OSError, ValueError):
                why = ""
            return False, (f"      bom_cpl: {why}" if why else tail(log))
        return True, ""
    inp = f"kicad/{board.name}.kicad_sch" if name == "erc" else pcb
    out = logs / f"{board.name}-{name}.json"
    rc = run([HWDE, "gate", "--gate", name, inp, "--workspace", ".",
              "--out", str(out)], ws, log)
    if rc == 0:
        return True, ""
    try:
        r = json.loads(out.read_text())
        why = "; ".join(f"{v.get('check') or v.get('kind', '?')}: "
                        f"{v.get('msg') or v.get('message', '')} "
                        f"{','.join(v.get('refs') or [])}"[:160]
                        for v in r.get("failing", [])[:6])
    except (OSError, ValueError):
        why = ""
    return False, (f"      {why}" if why else tail(log))


def probes(table: dict[str, score.Workaround]) -> list[str]:
    """Problems with docs/workarounds.md against tests/workarounds/*.py."""
    bad = []
    pdir = ROOT / "tests" / "workarounds"
    found = {p.stem: p for p in sorted(pdir.glob("*.py")) if not p.name.startswith("_")}
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        [str(SKILL), str(SKILL / "lib"), str(pdir)]))

    def run(p: Path):
        t = time.monotonic()
        r = subprocess.run([sys.executable, str(p)], cwd=ROOT, env=env,
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        return r, time.monotonic() - t

    # The probes are independent (each reads its own fixture or a scratch
    # copy): four at a time, reported in order. One after another they
    # were the per-PR test's slowest item (73 s on a loaded machine).
    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = list(pool.map(run, found.values()))
    for (wid, p), (r, took) in zip(found.items(), runs):
        said = (r.stdout.strip().splitlines() or [""])[-1]
        took = f"{took:.0f}s"
        if r.returncode == 0:
            state = "stands"
            if wid not in table:
                bad.append(f"{wid}: its cause stands ({said}) but "
                           f"docs/workarounds.md doesn't list it")
        elif r.returncode == 10:
            state = "fixed"
            if wid in table:
                bad.append(f"{wid}: fixed ({said}); take its workaround out "
                           f"and its row off docs/workarounds.md")
        else:
            state = f"error {r.returncode}"
            err = (r.stderr.strip().splitlines() or [""])[-1]
            bad.append(f"{wid}: probe failed to run: {err or said}")
        print(f"  probe {wid}: {state} ({took}) {said}", flush=True)
    for wid in table:
        if wid not in found:
            bad.append(f"{wid}: listed with no probe tests/workarounds/{wid}.py")
    return bad


def knowledge(logs: Path) -> list[str] | None:
    """Problems `knowledge --validate --strict` reports, or None when the
    source PDFs it checks citations against aren't here."""
    src = os.environ.get("HWDE_KNOWLEDGE_SOURCES")
    if not (src and Path(src).is_dir()) and not any(
            (SKILL.parent / "reference" / "knowledge" / "sources").glob("*.pdf")):
        return None
    out = logs / "knowledge.json"
    rc = run([HWDE, "knowledge", "--validate", "--strict", "--out", str(out)],
             ROOT, logs / "knowledge.log",
             env=dict(os.environ, HWDE_BOARDS_ROOT=str(ROOT / "boards")))
    try:
        problems = json.loads(out.read_text()).get("problems", [])
    except (OSError, ValueError):
        problems = []
    if rc and not problems:
        problems = [tail(logs / "knowledge.log").strip() or f"exit {rc}"]
    return [f"knowledge: {p}" for p in problems]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--board", action="append", default=[],
                    help="only this board (repeatable)")
    ap.add_argument("--no-probes", action="store_true",
                    help="skip the workaround probes")
    ap.add_argument("--keep", help="leave the scratch workspaces and logs here")
    args = ap.parse_args()

    table = score.read_workarounds(ROOT / "docs" / "workarounds.md")
    boards = score.read_boards(ROOT / "boards")
    if args.board:
        boards = [b for b in boards if b.name in args.board]
    if not boards:
        print("check-all: no boards to check", file=sys.stderr)
        return 1
    scratch = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="hwde-check-"))
    scratch.mkdir(parents=True, exist_ok=True)
    logs = scratch / "logs"
    logs.mkdir(exist_ok=True)
    failed = []
    try:
        for b in boards:
            ws = scratch / b.name
            shutil.rmtree(ws, ignore_errors=True)
            shutil.copytree(b.path, ws, ignore=shutil.ignore_patterns(
                "fab-check", "__pycache__"))
            print(f"{b.name}: {', '.join(b.steps)}", flush=True)
            for s in b.steps:
                t = time.monotonic()
                ok, why = step(s, b, ws, logs)
                print(f"  {s}: {'pass' if ok else 'FAIL'} ({time.monotonic() - t:.0f}s)",
                      flush=True)
                if not ok:
                    failed.append(f"{b.name} {s}")
                    print(why, flush=True)
        bad = knowledge(logs)
        if bad is None:
            print("knowledge: skipped (no source PDFs; set HWDE_KNOWLEDGE_SOURCES)",
                  flush=True)
        else:
            print(f"knowledge: {'FAIL' if bad else 'pass'}", flush=True)
            for m in bad[:20]:
                print(f"  {m}", flush=True)
            failed += bad
        if not args.no_probes:
            print("workarounds:", flush=True)
            bad = probes(table)
            for m in bad:
                print(f"  WRONG {m}", flush=True)
            failed += bad
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
    if failed:
        print(f"check-all: FAIL ({len(failed)}): " + "; ".join(failed[:8]))
        return 1
    print(f"check-all: pass ({len(boards)} board(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
