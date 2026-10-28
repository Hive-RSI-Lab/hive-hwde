"""T6 req-lint tests: check_requirements.py pins the P0 artifact's location
and section schema deterministically.

Criteria -> tests:
  - all five root-level real requirements.md pass (lint starts green)
                                     -> test_real_boards_pass
  - the shipped carrier escape (file under architecture/) fails req_misplaced
    naming the stray path             -> test_carrier_misplaced
  - hermetic misplaced workspace      -> test_misplaced_tmp_workspace
  - no file anywhere -> req_missing   -> test_missing_entirely
  - section 8 deleted -> req_sections -> test_section_deleted
  - section 9 'none' ok; unnumbered prose -> req_oq_format warning, exit 0
                                     -> test_open_question_format
  - SPEC 6 plumbing: --out file, missing workspace exit 2
                                     -> test_out_and_bad_workspace
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import check_requirements  # noqa: E402
from _boards import real_board  # noqa: E402

ROOT_BOARDS = ["stm32-blinky", "usb-buck", "pd-trigger", "lumina-par",
               "lumina-strobe"]

CONFORMING = "\n".join(
    [f"## {n}. {name}\n\nbody {n}"
     for n, name in check_requirements.SECTION_NAMES.items() if n != 9]
    + ["## 9. Open questions", "", "1. A numbered question? (default: yes)"]
) + "\n"


def run(ws, tmp_path=None, capsys=None, *extra):
    code = check_requirements.main(["--workspace", str(ws), *extra])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


def kinds(payload):
    return [v["kind"] for v in payload["violations"]]


@pytest.mark.parametrize("board", ROOT_BOARDS)
def test_real_boards_pass(board, capsys):
    code, payload = run(real_board(board), None, capsys)
    assert code == 0, payload["violations"]
    assert payload["status"] == "pass"
    assert set(range(1, 10)) <= set(payload["sections_found"])


def test_carrier_misplaced(capsys):
    """The one live escape: lumina-carrier wrote to architecture/ and the
    design-doc PDF shipped a 'not found' stub."""
    code, payload = run(real_board("lumina-carrier"), None, capsys)
    assert code == 1
    assert kinds(payload) == ["req_misplaced"]
    v = payload["violations"][0]
    assert v["stray_path"] == "architecture/requirements.md"
    assert "workspace root" in v["remediation"]


def test_misplaced_tmp_workspace(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws / "architecture").mkdir(parents=True)
    (ws / "architecture" / "requirements.md").write_text(CONFORMING,
                                                         encoding="utf-8")
    code, payload = run(ws, tmp_path, capsys)
    assert code == 1
    assert kinds(payload) == ["req_misplaced"]


def test_missing_entirely(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    code, payload = run(ws, tmp_path, capsys)
    assert code == 1
    assert kinds(payload) == ["req_missing"]


def test_section_deleted(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    mutant = CONFORMING.replace("## 8. Compliance/safety flags", "## mutant")
    (ws / "requirements.md").write_text(mutant, encoding="utf-8")
    code, payload = run(ws, tmp_path, capsys)
    assert code == 1
    assert kinds(payload) == ["req_sections"]
    assert payload["violations"][0]["section"] == 8
    # conforming doc passes as-is
    (ws / "requirements.md").write_text(CONFORMING, encoding="utf-8")
    code, payload = run(ws, tmp_path, capsys)
    assert code == 0, payload["violations"]


def test_open_question_format(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    # literal 'none' is fine
    doc = CONFORMING.replace("1. A numbered question? (default: yes)", "None.")
    (ws / "requirements.md").write_text(doc, encoding="utf-8")
    code, payload = run(ws, tmp_path, capsys)
    assert code == 0 and not payload["violations"]
    # bold '**1.' questions (lumina style) are fine
    doc = CONFORMING.replace("1. A numbered question?",
                             "**1. A bold question?**")
    (ws / "requirements.md").write_text(doc, encoding="utf-8")
    code, payload = run(ws, tmp_path, capsys)
    assert code == 0 and not payload["violations"]
    # unnumbered prose warns but stays exit 0 (advisory)
    doc = CONFORMING.replace("1. A numbered question? (default: yes)",
                             "what about the enclosure, and the budget?")
    (ws / "requirements.md").write_text(doc, encoding="utf-8")
    code, payload = run(ws, tmp_path, capsys)
    assert code == 0
    assert kinds(payload) == ["req_oq_format"]
    assert payload["violations"][0]["severity"] == "warning"
    assert payload["status"] == "pass"


def test_out_and_bad_workspace(tmp_path, capsys):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "requirements.md").write_text(CONFORMING, encoding="utf-8")
    out = tmp_path / "req.json"
    code = check_requirements.main(["--workspace", str(ws),
                                    "--out", str(out)])
    capsys.readouterr()
    assert code == 0
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "pass"
    code = check_requirements.main(["--workspace", str(tmp_path / "nope")])
    err = json.loads(capsys.readouterr().out)
    assert code == 2 and err["status"] == "error"
