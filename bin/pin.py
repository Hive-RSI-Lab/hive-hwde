"""hwde pin: refresh the boards the score re-runs (docs/score.md).

    hwde pin BOARD --from CLONE --commit SHA   copy CLONE's boards/BOARD at SHA
                                               into pinned/BOARD/
    hwde pin --digests                         re-stamp every pin's digest only
                                               (after classifying its scripts)

A pin is pinned/BOARD.json: where the board came from (project, commit), its
directory (pinned/BOARD, or boards/BOARD for this repo's own board) and each
board-local script in it, as `design` (the board's own source) or `stand_in`
(does what an hwde script should, with why). A script the pin doesn't classify
is listed under `unclassified` and charged like a stand-in until someone
classifies it. Every pin's digest goes into bin/score.py's PINS, so a refresh
is a change to the score's own file: the commit that makes it names the
CLONE commit, and the score starts a new baseline from it.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import score  # noqa: E402

# What the gates never read: the fab package (but the CPL dfm checks against
# and its image verdict), renders and logs.
KEEP_FAB = ("fab/CPL.csv", "fab/cpl_visual.json")
DROP = ("fab/", "renders/", "reports/renders/", "log/")


def wanted(rel: str) -> bool:
    if rel in KEEP_FAB:
        return True
    return not any(rel.startswith(d) for d in DROP) and "__pycache__/" not in rel


def extract(clone: Path, sha: str, path: str, dest: Path) -> None:
    raw = subprocess.run(["git", "-C", str(clone), "archive", "--format=tar", sha, path],
                         check=True, capture_output=True).stdout
    pre = path.rstrip("/") + "/"
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.startswith(pre):
                continue
            rel = m.name[len(pre):]
            if not wanted(rel):
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tf.extractfile(m).read())
            if m.mode & 0o111:
                out.chmod(0o755)


def stamp() -> None:
    """Write every pin's digest into bin/score.py's PINS block."""
    pins = {p.stem: score.digest(score.read_pin(p))
            for p in sorted((ROOT / "pinned").glob("*.json"))}
    body = "".join(f'    "{k}": "{v}",\n' for k, v in pins.items())
    src = (HERE / "score.py").read_text(encoding="utf-8")
    new, n = re.subn(r"(# pins:begin\nPINS = \{\n).*?(\}\n# pins:end)",
                     lambda m: m.group(1) + body + m.group(2), src, flags=re.S)
    if n != 1:
        raise SystemExit("pin: bin/score.py has no PINS block")
    (HERE / "score.py").write_text(new, encoding="utf-8")
    for k, v in pins.items():
        print(f"{k}: {v}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("board", nargs="?")
    ap.add_argument("--from", dest="clone", help="the project's git clone")
    ap.add_argument("--commit", help="the commit to pin (any rev the clone knows)")
    ap.add_argument("--digests", action="store_true", help="only re-stamp digests")
    args = ap.parse_args()
    if args.digests:
        stamp()
        return 0
    if not (args.board and args.clone and args.commit):
        ap.error("BOARD, --from and --commit are needed (or --digests)")
    clone = Path(args.clone).resolve()
    sha = subprocess.run(["git", "-C", str(clone), "rev-parse", f"{args.commit}^{{commit}}"],
                         check=True, capture_output=True, text=True).stdout.strip()
    pf = ROOT / "pinned" / f"{args.board}.json"
    old = json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else {}
    dest = ROOT / "pinned" / args.board
    tmp = Path(tempfile.mkdtemp(prefix="hwde-pin-", dir=ROOT / "pinned"))
    try:
        extract(clone, sha, f"boards/{args.board}", tmp)
        if not (tmp / "state.json").is_file():
            raise SystemExit(f"pin: {clone.name}@{sha[:7]} has no boards/{args.board}/state.json")
        shutil.rmtree(dest, ignore_errors=True)
        tmp.rename(dest)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # what git won't commit here (the board's own .gitignore, this repo's)
    # would be missing from a fresh clone and break the digest there
    rels = "\n".join(str(dest.relative_to(ROOT) / f) for f in score.files(dest))
    ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--no-index", "--stdin"],
                             input=rels, capture_output=True, text=True).stdout.split()
    for f in ignored:
        (ROOT / f).unlink()
    have = set(score.scripts(dest))
    design = [s for s in old.get("design", []) if s in have]
    stand_in = {s: w for s, w in old.get("stand_in", {}).items() if s in have}
    pin = {
        "board": args.board,
        "project": old.get("project") or clone.name,
        "commit": sha,
        "dir": f"pinned/{args.board}",
        "design": sorted(design),
        "stand_in": dict(sorted(stand_in.items())),
        "unclassified": sorted(have - set(design) - set(stand_in)),
    }
    pf.write_text(json.dumps(pin, indent=1) + "\n", encoding="utf-8")
    print(f"pinned {args.board} at {pin['project']}@{sha[:7]}: {len(have)} script(s), "
          f"{len(pin['unclassified'])} unclassified")
    stamp()
    return 0


if __name__ == "__main__":
    sys.exit(main())
