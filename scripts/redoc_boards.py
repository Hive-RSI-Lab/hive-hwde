#!/usr/bin/env python
"""redoc_boards.py - re-render every numbered board's design doc and re-file it.

A one-shot for the boards repo: after its workspaces carry their part numbers,
each design doc is rebuilt from the workspace as it stands (report_gen.py -
state.json, reports, renders; no LLM step runs) so the PDF prints the PN
under its title and in every footer, and is filed with
`cc-docs file --describes <PN>` as the next revision of the board's filed
document (report_gen.doc_target: its project and title), else into the
project "Boards" (report_gen.board_project; DOC_PROJECT set in the
environment overrides it). cc-docs files a new revision only when the
content changed, so a second run files nothing (the board reports
`unchanged`). The skip is keyed on the library too, so a rehearsal with
--library leaves the live run free to file. Each filing also puts the
board's fab set on the revision under <ws>-prefixed names
(report_gen.fab_attachments), so a board whose fab files changed under an
unchanged PDF files them again; a board whose fab set did not go up counts
as not filed.

    redoc_boards.py [BOARD...] [--root DIR] [--library DIR] [--dry-run]
                    [--kind design|highlight|full] [--out FILE]

--kind picks which of report_gen's documents to rebuild and file (default
the design doc); each kind is its own document in the library.

BOARD is an old name, a PN or a directory (default: every register rev whose
workspace exists). --library points cc-docs at a scratch library
(CC_DOCS_ROOT) instead of the live one; --dry-run builds nothing and prints,
per board, the workspace, PN, project, the `cc-docs file` arguments it
would use and the names it would attach.
The rebuilt .tex/.pdf land in each workspace's reports/design_doc/ and are
not committed here.

Exit 0 = every board rebuilt and filed, or unchanged; 1 = a board was
missing, degraded or not filed; 2 = error, including cc-docs not on PATH
(without --dry-run).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

from checklib import CheckError, cli_wrap  # noqa: E402
from lib import boardreg, env  # noqa: E402
import report_gen  # noqa: E402

SCRIPT = "redoc_boards.py"


def run(args) -> tuple[dict, str | None]:
    root = Path(args.root).expanduser() if args.root else env.boards_root()
    reg = boardreg.load(root)
    if not reg:
        raise CheckError(f"no readable {boardreg.REGISTER} in {root}")
    keys = args.board or sorted(reg)
    if args.library:
        os.environ["CC_DOCS_ROOT"] = str(Path(args.library).expanduser())
    if not args.dry_run and shutil.which("cc-docs") is None:
        raise CheckError("cc-docs is not on PATH - nothing could be filed")
    boards, bad = [], 0
    for key in keys:
        ws = boardreg.resolve(key, root)
        if ws is None or not (ws / "state.json").is_file():
            if args.board:   # asked for by name: its absence is a finding
                boards.append({"board": key, "error": "no workspace"})
                bad += 1
            continue
        pn, why = boardreg.part_number(ws)
        row = {"board": key, "workspace": str(ws),
               "pn": pn["pn"] if pn else None,
               "project": report_gen.board_project(ws) if pn else None}
        if pn is None:
            row["error"] = why
            bad += 1
        elif args.dry_run:
            st = report_gen.load_state(ws)
            subdir, suffix = report_gen.KINDS[args.kind][:2]
            cmd = report_gen.cc_docs_args(
                ws, st["board"], ws / "reports" / subdir
                / f"{st['board']}-{suffix}.pdf", kind=args.kind)
            row["cc_docs"] = cmd
            row["project"] = cmd[cmd.index("--project") + 1]
            row["attach"] = [n for _, n in report_gen.fab_attachments(ws)]
        else:
            try:
                board = (report_gen.read_json(ws, "state.json") or {}).get("board")
                if board:   # where the filing goes: the filed document's project
                    row["project"] = report_gen.doc_target(ws, board, args.kind)[0]
                payload, code = report_gen.run(str(ws), file_doc=True,
                                               kind=args.kind)
                row.update(status=payload["status"], pdf=payload["pdf"],
                           filed=payload["filed"],
                           unchanged=payload["unchanged"],
                           attached=payload["attached"],
                           warnings=payload["warnings"][-3:])
                # "not filed" is a finding, and so is a fab set that did
                # not go up; a stamp-matched skip is neither
                fab = report_gen.fab_attachments(ws)
                bad += (code != 0 or payload["pdf"] is None
                        or (not payload["unchanged"]
                            and (payload["filed"] is None
                                 or (fab and not payload["attached"]))))
            except Exception as exc:   # one board's failure must not lose
                row["error"] = f"{type(exc).__name__}: {exc}"  # the rest's
                bad += 1
        boards.append(row)
    return {"script": SCRIPT, "status": "violations" if bad else "pass",
            "root": str(root), "dry_run": bool(args.dry_run),
            "library": os.environ.get("CC_DOCS_ROOT"),
            "boards": boards}, args.out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("board", nargs="*", help="old name, PN or directory")
    ap.add_argument("--root", help="boards repo (default: HWDE_BOARDS_ROOT, "
                                   "~/dev/boards)")
    ap.add_argument("--library", help="scratch cc-docs library (CC_DOCS_ROOT)")
    ap.add_argument("--dry-run", action="store_true", help="plan only")
    ap.add_argument("--kind", choices=sorted(report_gen.KINDS), default="design",
                    help="which document to rebuild (default: design)")
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)
    return cli_wrap(SCRIPT, lambda: run(args))


if __name__ == "__main__":
    sys.exit(main())
