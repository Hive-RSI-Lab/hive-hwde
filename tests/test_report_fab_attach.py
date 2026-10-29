"""report_gen puts a board's fab set on the document it files (owner, #ai-ee:
"make sure the fab files are uploaded to the design documents in the library
for the boards", then "name the files all with the prefix of the project
name so i know which bom it is and cpl etc"). Driven by a fake cc-docs."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report_gen  # noqa: E402

WS = "PCB-0022-A_nfc-card"


class _Builder:
    def __init__(self):
        self.warnings = []
        self.filed = None
        self.unchanged = False
        self.attached = []

    def warn(self, m):
        self.warnings.append(m)


def _fake_cc_docs(tmp_path, monkeypatch, attach_rc=0):
    """A cc-docs that logs each call, answers `file` with a number and
    records what each `attach` was given as name=content lines."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log, att = tmp_path / "calls.log", tmp_path / "attached.log"
    exe = bindir / "cc-docs"
    exe.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'if [ "$1" = file ]; then printf "001-0007-B\\t/lib/x.pdf\\tfiled\\n"; fi\n'
        'if [ "$1" = attach ]; then shift 3; for f in "$@"; do\n'
        f'  echo "$(basename "$f")=$(cat "$f")" >> {att}; done\n'
        f"  exit {attach_rc}; fi\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.delenv("DOC_PROJECT", raising=False)
    monkeypatch.delenv("CC_DOCS_ROOT", raising=False)
    return log, att


def _board(tmp_path, fab=True):
    ws = tmp_path / WS
    ws.mkdir()
    (tmp_path / "register.yaml").write_text(
        f"products:\n  PCB-0022:\n    revs:\n      A: {{dir: {WS}}}\n",
        encoding="utf-8")
    if fab:
        f = ws / "fab"
        f.mkdir()
        (f / f"{WS}_gerbers.zip").write_text("zip", encoding="utf-8")
        (f / "BOM.csv").write_text("bom", encoding="utf-8")
        (f / "CPL.csv").write_text("cpl", encoding="utf-8")
        (f / f"{WS}_BOM_digikey.csv").write_text("dk", encoding="utf-8")
        (f / "order.json").write_text("{}", encoding="utf-8")   # not fab set
    pdf = ws / "reports" / "design_doc" / "x.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4")
    return ws, pdf


def test_fab_set_goes_up_under_prefixed_names(tmp_path, monkeypatch):
    log, att = _fake_cc_docs(tmp_path, monkeypatch)
    ws, pdf = _board(tmp_path)
    b = _Builder()
    report_gen.file_in_register(pdf, "nfc-card", b, True, "d1", ws)
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert calls[0].startswith("file ") and "--describes PCB-0022-A" in calls[0]
    assert "--attach" not in calls[0]
    assert calls[1].startswith("attach 001-0007-B --replace ")
    got = dict(line.split("=", 1) for line in att.read_text().splitlines())
    assert got == {f"{WS}_gerbers.zip": "zip", f"{WS}_BOM.csv": "bom",
                   f"{WS}_CPL.csv": "cpl", f"{WS}_BOM_digikey.csv": "dk"}
    assert all(n.startswith(WS) for n in got)
    assert b.attached == list(got)
    # the JLC upload pair keeps its bare names in the workspace
    assert (ws / "fab" / "BOM.csv").is_file()
    assert not (ws / "fab" / f"{WS}_BOM.csv").exists()
    assert (pdf.parent / report_gen.FILED_STAMP).is_file()


def test_board_without_fab_set_files_as_before(tmp_path, monkeypatch):
    log, att = _fake_cc_docs(tmp_path, monkeypatch)
    ws, pdf = _board(tmp_path, fab=False)
    b = _Builder()
    report_gen.file_in_register(pdf, "nfc-card", b, True, "d1", ws)
    assert len(log.read_text().splitlines()) == 1
    assert not att.exists()
    assert b.warnings == [] and b.attached == []
    assert b.filed.startswith("001-0007-B")


def test_changed_fab_set_goes_up_under_an_unchanged_pdf(tmp_path, monkeypatch):
    log, att = _fake_cc_docs(tmp_path, monkeypatch)
    ws, pdf = _board(tmp_path)
    report_gen.file_in_register(pdf, "nfc-card", _Builder(), True, "d1", ws)
    # same document, same fab set: nothing is called
    b = _Builder()
    report_gen.file_in_register(pdf, "nfc-card", b, True, "d1", ws)
    assert b.unchanged is True
    assert len(log.read_text().splitlines()) == 2
    # same document, new BOM: filed (cc-docs finds it unchanged) and attached
    (ws / "fab" / "BOM.csv").write_text("bom2", encoding="utf-8")
    b = _Builder()
    report_gen.file_in_register(pdf, "nfc-card", b, True, "d1", ws)
    calls = log.read_text().splitlines()
    assert len(calls) == 4 and calls[3].startswith("attach 001-0007-B --replace")
    assert f"{WS}_BOM.csv=bom2" in att.read_text().splitlines()
    assert b.unchanged is False


def test_failed_attach_warns_and_leaves_no_stamp(tmp_path, monkeypatch):
    log, _ = _fake_cc_docs(tmp_path, monkeypatch, attach_rc=3)
    ws, pdf = _board(tmp_path)
    b = _Builder()
    report_gen.file_in_register(pdf, "nfc-card", b, True, "d1", ws)
    assert b.filed.startswith("001-0007-B")
    assert b.attached == []
    assert any("fab files not attached (rc=3)" in w for w in b.warnings)
    assert not (pdf.parent / report_gen.FILED_STAMP).exists()
    # the next run tries again rather than trusting a stamp
    report_gen.file_in_register(pdf, "nfc-card", _Builder(), True, "d1", ws)
    assert len(log.read_text().splitlines()) == 4


def test_fab_attachments_lists_only_present_files(tmp_path):
    ws, _ = _board(tmp_path)
    (ws / "fab" / f"{WS}_BOM_mouser.csv").write_text("m", encoding="utf-8")
    (ws / "fab" / f"{WS}_BOM_cost.json").write_text("{}", encoding="utf-8")
    names = [n for _, n in report_gen.fab_attachments(ws)]
    assert names == [f"{WS}_gerbers.zip", f"{WS}_BOM.csv", f"{WS}_CPL.csv",
                     f"{WS}_BOM_digikey.csv", f"{WS}_BOM_mouser.csv",
                     f"{WS}_BOM_cost.json"]
    assert report_gen.fab_attachments(None) == []
    (tmp_path / "other").mkdir()
    bare, _ = _board(tmp_path / "other", fab=False)
    assert report_gen.fab_attachments(bare) == []
