#!/usr/bin/env python
"""check_requirements.py - deterministic exit lint for the P0 artifact.

The requirements doc is agent prose by design, but three properties are
checkable and one escape shipped for lack of a check (lumina-carrier's
design-doc PDF said 'requirements.md not found' while the file sat under
architecture/): location, the 9-section schema, and the open-question format.

Checks (T6 req-lint):
  (a) <workspace>/requirements.md exists at the workspace ROOT. On a miss,
      strays under architecture/ and brief/ are named (kind req_misplaced,
      error); when no file exists anywhere the kind is req_missing (error).
  (b) '## <n>' headings must cover sections 1..9 (any title wording; extra
      sections 0, 10+, Answers, Traceability are fine). One req_sections
      error per missing number.
  (c) section 9 must contain numbered open questions (plain '1.' or bold
      '**1.' style) or the literal 'none' - req_oq_format, warning.

U18 mode leg - the brief's build-mode token is resolved (modeslib) and the
artifact is checked against it:
  (d) an unresolvable token (unknown target) is req_mode_unknown, error - the
      alternative is a silently mode-less run.
  (e) a declared mode that section 1 never names is req_mode_unnamed, error:
      section 1 is what every later stage and both reviewers read.
  (f) under a binding that makes geometry an OUTPUT, a dimension stated in
      section 5 without a RELAXABLE marker is req_mode_unmarked_size, error -
      unmarked, it reads as a HARD cap that binds at P5 board_init.
  (g) section 1 naming a mode the brief did not declare is req_mode_stray,
      warning (the token is the declaration; prose is not).

Script contract (SPEC.md section 6): argparse, JSON to stdout or --out,
ASCII. Exit 0 = artifact conforms (warnings allowed - they are advisory at
P0, mirroring check_env), 1 = errors (orchestrator loops the analyst once),
2 = cannot run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import modeslib  # noqa: E402
from checklib import CheckError  # noqa: E402

SCRIPT = "check_requirements"
SECTION_NAMES = {
    1: "Function", 2: "Interfaces", 3: "Power", 4: "Environment",
    5: "Size & mounting", 6: "Quantity & budget", 7: "Assembly",
    8: "Compliance/safety flags", 9: "Open questions",
}
STRAY_DIRS = ("architecture", "brief")
_HEADING = re.compile(r"^##\s+(\d+)")
# numbered open question: '1.' / '2)' with optional markdown bold/underline
_NUMBERED = re.compile(r"^\s*(?:\*\*|__)?\s*\d+[.)]")

MISPLACED_REMEDIATION = ("move to workspace root - report_gen.py and all "
                         "later phases resolve it there")
RELAXABLE_RE = re.compile(r"\bRELAXABLE\b")
# 'no HARD cap' / 'not a cap' style prose also satisfies (f): the point is that
# the reader cannot mistake the number for a binding one.
NOT_A_CAP_RE = re.compile(r"\bno\s+hard\s+cap\b|\bnot\s+a\s+cap\b", re.I)


def _vio(severity: str, kind: str, msg: str, **extras) -> dict:
    return checklib.violation("requirements", severity, None, None, None, [],
                              msg, SCRIPT, kind=kind, **extras)


def _section(lines: list[str], found: dict[int, int], n: int) -> list[str]:
    """The body lines of '## <n>', up to the next '## ' heading."""
    if n not in found:
        return []
    start = found[n] + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def _brief_token(ws: Path, facts: dict) -> str | None:
    """The mode token the brief OPENS with (first brief file that declares
    one), recorded in facts with the file that carried it."""
    bdir = ws / "brief"
    if not bdir.is_dir():
        return None
    for p in sorted(bdir.rglob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tok = modeslib.detect(text)
        if tok:
            facts["brief_file"] = p.relative_to(ws).as_posix()
            return tok
    return None


def mode_leg(ws: Path, lines: list[str], found: dict[int, int],
             facts: dict) -> list[dict]:
    """U18: the artifact must agree with the brief's declared build mode, and
    a relaxable dimension must be MARKED relaxable.

    This is the ARTIFACT half of the binding. A stated dimension under a
    geometry-relaxing binding is a preference, and section 1 / section 5 are
    where P2, P6 and both reviewers read that. The other half is
    `board_init.mode_outline_guard`, and it is the one bb-buck needed: its
    section 5 already said "no HARD cap" and passes this lint unchanged - the
    35 x 25 arrived two checkpoints later as a CLI argument no artifact
    records."""
    violations: list[dict] = []
    facts["mode"] = None
    token = _brief_token(ws, facts)
    facts["brief_token"] = token
    sec1 = "\n".join(_section(lines, found, 1))
    if token is None:
        if re.search(r"\bbuild mode\b", sec1, re.I):
            violations.append(_vio(
                "warning", "req_mode_stray",
                "section 1 names a build mode but no brief/ file opens with a "
                "mode token - the token is the declaration (see "
                "reference/build-modes.md); prose is not"))
        return violations
    try:
        mode = modeslib.resolve(token)
    except modeslib.ModeError as exc:
        violations.append(_vio("error", "req_mode_unknown", str(exc),
                               token=token))
        return violations
    facts["mode"] = {k: mode[k] for k in
                     ("token", "target", "scope", "binding", "stage",
                      "geometry_is_output", "excludes", "requires")}
    aliases = [t.rstrip(":") for t, row in modeslib.load()["tokens"].items()
               if row.get("target") == mode["target"]]
    low = sec1.lower()
    missing = []
    if not any(a.lower() in low for a in [mode["target"], *aliases]):
        missing.append(f"the mode ({mode['target']})")
    if mode["binding"] not in low:
        missing.append(f"its binding ({mode['binding']})")
    if missing:
        violations.append(_vio(
            "error", "req_mode_unnamed",
            f"the brief declares {token!r} but section 1 does not name "
            f"{' or '.join(missing)} - section 1 is what P2, P6 and both "
            "reviewers read to know the scope and whether the geometry binds",
            token=token, target=mode["target"], binding=mode["binding"]))
    if mode["geometry_is_output"]:
        body = "\n".join(_section(lines, found, 5))
        size = modeslib.parse_size(body)
        if size and not (RELAXABLE_RE.search(body)
                         or NOT_A_CAP_RE.search(body)):
            violations.append(_vio(
                "error", "req_mode_unmarked_size",
                f"section 5 states {size[0]:g} x {size[1]:g} under binding "
                f"{mode['binding']}, where the board size is an OUTPUT of the "
                "placement - mark it 'RELAXABLE (" + mode["binding"] + ")' or "
                "'no HARD cap', or the number binds at P5 board_init",
                section=5, binding=mode["binding"],
                stated=[size[0], size[1]]))
    return violations


def lint(ws: Path) -> tuple[list[dict], dict]:
    violations: list[dict] = []
    req = ws / "requirements.md"
    facts: dict = {"workspace": str(ws).replace("\\", "/"),
                   "path": None, "sections_found": [],
                   "brief_token": None, "mode": None}
    if not req.is_file():
        strays = [p for d in STRAY_DIRS if (ws / d).is_dir()
                  for p in sorted((ws / d).rglob("requirements.md"))]
        if strays:
            for s in strays:
                rel = s.relative_to(ws).as_posix()
                violations.append(_vio(
                    "error", "req_misplaced",
                    f"requirements.md found at {rel}, not the workspace "
                    f"root: {MISPLACED_REMEDIATION}",
                    stray_path=rel, remediation=MISPLACED_REMEDIATION))
        else:
            violations.append(_vio(
                "error", "req_missing",
                "requirements.md not found at the workspace root (or under "
                "architecture/ or brief/) - P0 has not produced its artifact"))
        return violations, facts

    facts["path"] = "requirements.md"
    lines = req.read_text(encoding="utf-8", errors="replace").splitlines()
    # (b) section coverage by number, wording-tolerant ('&' vs 'and' vs '/')
    found: dict[int, int] = {}
    for i, ln in enumerate(lines):
        m = _HEADING.match(ln)
        if m and int(m.group(1)) not in found:
            found[int(m.group(1))] = i
    facts["sections_found"] = sorted(found)
    for n, name in SECTION_NAMES.items():
        if n not in found:
            violations.append(_vio(
                "error", "req_sections",
                f"section '## {n}. {name}' missing", section=n))
    # (c) open-question format: numbered lines or the literal 'none'
    if 9 in found:
        body = _section(lines, found, 9)
        has_q = any(_NUMBERED.match(ln) for ln in body)
        has_none = any(re.search(r"\bnone\b", ln, re.IGNORECASE)
                       for ln in body)
        if not (has_q or has_none):
            violations.append(_vio(
                "warning", "req_oq_format",
                "section 9 has neither numbered open questions "
                "(e.g. '1. ...') nor the literal 'none'", section=9))
    violations.extend(mode_leg(ws, lines, found, facts))
    return violations, facts


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True,
                    help="board workspace dir (requirements.md at its root)")
    ap.add_argument("--out", help="write result JSON here instead of stdout")
    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    if not ws.is_dir():
        raise CheckError(f"workspace does not exist: {ws}")
    violations, facts = lint(ws)
    payload = checklib.report(SCRIPT, None, violations, **facts)
    if not any(v["severity"] == "error" for v in violations):
        payload["status"] = "pass"  # warnings are advisory at P0
    return payload, args.out


def main(argv=None) -> int:
    return checklib.cli_wrap(SCRIPT, lambda: run(argv))


if __name__ == "__main__":
    raise SystemExit(main())
