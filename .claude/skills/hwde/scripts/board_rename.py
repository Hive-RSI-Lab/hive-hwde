#!/usr/bin/env python
"""board_rename.py - rename a numbered board's workspace to <PN>_<name>.

For the boards repo's one-time move to part-numbered directories (and any
board numbered later). For each board named on the command line (its old
name, its PN or its directory; --all = every register rev whose directory is
not yet <PN>_<name>) it:

  1. renames the workspace directory <root>/<name> -> <root>/<PN>_<name>;
  2. renames the top-level KiCad project files in kicad/ (.kicad_pro,
     .kicad_sch, .kicad_pcb, .kicad_prl, .kicad_dru, .net) to the new stem;
     sub-sheets and the kicad/route/ scratch copies keep their names;
  3. patches what names the project: .kicad_pro meta.filename and its
     top_level_sheets entry for the root sheet, and every `(project "<old>"`
     symbol-instance block in kicad/*.kicad_sch (KiCad keys reference
     designators by project name, so a stale one loses every annotation);
  4. rewrites the paths state.json stores: artifacts.*.path under kicad/ or
     fab/<old>_gerbers.zip (the zip is renamed with it) and `workspace`;
     `board` (the human name) and history are left as they are;
  5. rewrites that rev's `dir:` and `bom:` in register.yaml in place, the
     rest of the file (comments, order) byte-for-byte;
  6. verifies: kicad-cli exports the schematic netlist and the board's
     footprint positions from the renamed files, and the netlist's reference
     designators match the ones exported before the rename.

A board with no PN in the register is refused; so is one whose target
directory exists, and one whose `dir:` line (quoted or not, a trailing
`# comment` allowed) is not found exactly once - checked before anything
moves, so a refused board is left untouched. --dry-run prints the plan and writes nothing. Nothing is
committed: the boards repo's own session reviews and commits the result.

    board_rename.py (BOARD... | --all) [--root DIR] [--dry-run] [--no-verify]
                    [--out FILE]

Exit 0 = every board renamed (or planned) and verified; 1 = a board was
refused or failed verification (the others still ran); 2 = error.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from checklib import CheckError, cli_wrap  # noqa: E402
from lib import boardreg, env  # noqa: E402

SCRIPT = "board_rename.py"
STEM_SUFFIXES = (".kicad_pro", ".kicad_sch", ".kicad_pcb", ".kicad_prl",
                 ".kicad_dru", ".net")
_REF_RE = re.compile(r'\(ref "([^"]+)"\)')


def plan(root: Path, key: str) -> dict:
    """What renaming board `key` would do, or {"refused": why}."""
    ws = boardreg.resolve(key, root)
    if ws is None:
        return {"board": key, "refused": f"no workspace for {key} under {root}"}
    pn, why = boardreg.part_number(ws)
    if pn is None:
        return {"board": key, "refused": why}
    human = boardreg.split_dir(ws.name)[1]
    new = f"{pn['pn']}_{human}"
    if ws.name == new:
        return {"board": key, "pn": pn["pn"], "dir": new, "done": True}
    if (root / new).exists():
        return {"board": key, "refused": f"{root / new} already exists"}
    try:   # checked before anything moves, so a refusal leaves no half-rename
        register_text(root, ws.name, new)
    except (CheckError, OSError) as exc:
        return {"board": key, "refused": str(exc)}
    kicad = ws / "kicad"
    pros = sorted(kicad.glob("*.kicad_pro")) if kicad.is_dir() else []
    if len(pros) > 1:
        return {"board": key, "refused": f"{len(pros)} .kicad_pro in {kicad}"}
    old = pros[0].stem if pros else ws.name
    files = [f"kicad/{old}{s}" for s in STEM_SUFFIXES
             if (kicad / f"{old}{s}").is_file()]
    if (ws / "fab" / f"{old}_gerbers.zip").is_file():
        files.append(f"fab/{old}_gerbers.zip")
    return {"board": key, "pn": pn["pn"], "rev": pn["rev"],
            "from": ws.name, "to": new, "old_stem": old, "files": files}


def _new_rel(rel: str, old: str, new: str) -> str:
    return rel.replace(f"/{old}.", f"/{new}.", 1) if rel.startswith("kicad/") \
        else rel.replace(f"/{old}_gerbers", f"/{new}_gerbers", 1)


def patch_pro(pro: Path, old: str, new: str) -> None:
    doc = json.loads(pro.read_text(encoding="utf-8"))
    doc.setdefault("meta", {})["filename"] = pro.name
    for sheet in (doc.get("schematic") or {}).get("top_level_sheets") or []:
        if sheet.get("filename") == f"{old}.kicad_sch":
            sheet["filename"] = f"{new}.kicad_sch"
            if sheet.get("name") == old:
                sheet["name"] = new
    pro.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def patch_sheets(kicad: Path, old: str, new: str) -> int:
    """Rewrite `(project "<old>"` in every schematic; count the hits."""
    pat = re.compile(r'(\(project\s+)"' + re.escape(old) + '"')
    hits = 0
    for sch in sorted(kicad.glob("*.kicad_sch")):
        text = sch.read_text(encoding="utf-8")
        text, n = pat.subn(r'\1"' + new.replace("\\", r"\\") + '"', text)
        if n:
            sch.write_text(text, encoding="utf-8")
            hits += n
    return hits


def patch_state(ws: Path, p: dict) -> None:
    path = ws / "state.json"
    if not path.is_file():
        return
    st = json.loads(path.read_text(encoding="utf-8"))
    for ent in (st.get("artifacts") or {}).values():
        if isinstance(ent, dict) and ent.get("path") in p["files"]:
            ent["path"] = _new_rel(ent["path"], p["old_stem"], p["to"])
    if isinstance(st.get("workspace"), str):
        head, _, tail = st["workspace"].rpartition("/")
        if tail == p["from"]:
            st["workspace"] = f"{head}/{p['to']}" if head else p["to"]
    path.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")


def register_text(root: Path, old_dir: str, new_dir: str) -> str:
    """register.yaml with `dir: <old>` and `bom: <old>/...` rewritten, nothing
    else. The value may be quoted and followed by a `# comment`; anything but
    exactly one `dir:` match raises CheckError."""
    reg = root / boardreg.REGISTER
    text = reg.read_text(encoding="utf-8")
    esc = re.escape(old_dir)
    text, n = re.subn(r"""(\bdir:\s*(["']?))""" + esc
                      + r"(?=\2\s*(?:#[^\n]*)?(?:[,}\n]|\Z))",
                      lambda m: m.group(1) + new_dir, text)
    if n != 1:
        raise CheckError(f"{reg}: expected one `dir: {old_dir}`, found {n}")
    return re.sub(r"""(\bbom:\s*["']?)""" + esc + "/",
                  lambda m: m.group(1) + new_dir + "/", text)


def patch_register(root: Path, old_dir: str, new_dir: str) -> None:
    (root / boardreg.REGISTER).write_text(
        register_text(root, old_dir, new_dir), encoding="utf-8")


def kicad_refs(cli: Path, sch: Path) -> tuple[set[str] | None, str]:
    """Reference designators kicad-cli's netlist export reports, or None."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "n.net"
        cp = subprocess.run([str(cli), "sch", "export", "netlist", "--output",
                             str(out), str(sch)], capture_output=True,
                            text=True, timeout=300)
        if cp.returncode != 0 or not out.is_file():
            return None, (cp.stderr or cp.stdout).strip()[-300:]
        return set(_REF_RE.findall(out.read_text(encoding="utf-8"))), ""


def kicad_opens_pcb(cli: Path, pcb: Path) -> str:
    with tempfile.TemporaryDirectory() as td:
        cp = subprocess.run([str(cli), "pcb", "export", "pos", "--output",
                             str(Path(td) / "p.pos"), str(pcb)],
                            capture_output=True, text=True, timeout=300)
        return "" if cp.returncode == 0 else \
            (cp.stderr or cp.stdout).strip()[-300:] or f"rc {cp.returncode}"


def rename(root: Path, p: dict, cli: Path | None) -> dict:
    ws, new_ws = root / p["from"], root / p["to"]
    old, new = p["old_stem"], p["to"]
    sch_old = ws / "kicad" / f"{old}.kicad_sch"
    # kicad-cli leaves a .kicad_prl beside what it opens; the check's own
    # are removed again, a board's own is kept (renamed with the rest).
    prl_kept = {f"{new}.kicad_prl" if n == f"{old}.kicad_prl" else n
                for n in (x.name for x in (ws / "kicad").glob("*.kicad_prl"))}
    before = kicad_refs(cli, sch_old)[0] if cli and sch_old.is_file() else None
    ws.rename(new_ws)
    for rel in p["files"]:
        src = new_ws / rel
        src.rename(new_ws / _new_rel(rel, old, new))
    kicad = new_ws / "kicad"
    if (kicad / f"{new}.kicad_pro").is_file():
        patch_pro(kicad / f"{new}.kicad_pro", old, new)
    p["project_refs_rewritten"] = patch_sheets(kicad, old, new) \
        if kicad.is_dir() else 0
    patch_state(new_ws, p)
    patch_register(root, p["from"], p["to"])
    if cli is None:
        return {"verified": False, "why": "no kicad-cli (--no-verify)"}
    problems = []
    sch, pcb = kicad / f"{new}.kicad_sch", kicad / f"{new}.kicad_pcb"
    if sch.is_file():
        after, err = kicad_refs(cli, sch)
        if after is None:
            problems.append(f"kicad-cli cannot export {sch.name}: {err}")
        elif before is not None and after != before:
            problems.append(f"reference designators changed: lost "
                            f"{sorted(before - after)[:8]}, gained "
                            f"{sorted(after - before)[:8]}")
    if pcb.is_file() and (err := kicad_opens_pcb(cli, pcb)):
        problems.append(f"kicad-cli cannot open {pcb.name}: {err}")
    for prl in kicad.glob("*.kicad_prl"):
        if prl.name not in prl_kept:
            prl.unlink()
    return {"verified": not problems, "problems": problems}


def run(args) -> tuple[dict, str | None]:
    root = Path(args.root).expanduser() if args.root else env.boards_root()
    if not (root / boardreg.REGISTER).is_file():
        raise CheckError(f"no {boardreg.REGISTER} in {root}")
    keys = list(args.board)
    if args.all:
        keys += [e["dir"] for e in boardreg.load(root).values()
                 if not boardreg.split_dir(e["dir"])[0]]
    if not keys:
        raise CheckError("name a board, or --all")
    cli = None if (args.dry_run or args.no_verify) else env.find_kicad_cli()
    if cli is None and not (args.dry_run or args.no_verify):
        raise CheckError("kicad-cli not found (needed to verify; --no-verify "
                         "skips that)")
    boards, bad = [], 0
    for key in keys:
        p = plan(root, key)
        if "refused" not in p and not p.get("done") and not args.dry_run:
            try:
                p["result"] = rename(root, p, cli)
                bad += not p["result"]["verified"] and cli is not None
            except Exception as exc:   # one board's failure must not lose
                p["error"] = f"{type(exc).__name__}: {exc}"  # the rest's report
                bad += 1
        bad += "refused" in p
        boards.append(p)
    return {"script": SCRIPT, "status": "violations" if bad else "pass",
            "root": str(root), "dry_run": bool(args.dry_run),
            "boards": boards}, args.out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board", nargs="*", help="old name, PN or directory")
    ap.add_argument("--all", action="store_true",
                    help="every register rev whose dir is not <PN>_<name>")
    ap.add_argument("--root", help="boards repo (default: HWDE_BOARDS_ROOT, "
                                   "~/dev/boards)")
    ap.add_argument("--dry-run", action="store_true", help="plan only")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip the kicad-cli open check")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    return cli_wrap(SCRIPT, lambda: run(args))


if __name__ == "__main__":
    sys.exit(main())
