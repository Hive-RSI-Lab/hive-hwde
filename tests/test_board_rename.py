"""board_rename.py and redoc_boards.py: the boards repo's move to <PN>_<name>
directories, run here on a copy of the golden blinky2 project in a scratch
boards root (never the boards repo)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import board_rename  # noqa: E402
import redoc_boards  # noqa: E402
from lib import env  # noqa: E402

REG = """# the register's own comment survives
products:
  PCB-0001:
    title: blinky
    revs:
      A: {dir: blinky2, date: 2026-09-01, bom: blinky2/fab/BOM.csv}
  PCB-0002:
    title: other
    revs:
      A: {dir: other, date: 2026-09-02}
"""


def scratch(tmp_path: Path) -> Path:
    root = tmp_path / "boards"
    ws = root / "blinky2"
    # a .kicad_prl a kicad-cli run left in the golden dir is not the fixture's
    shutil.copytree(REPO / "tests" / "golden" / "blinky2", ws / "kicad",
                    ignore=shutil.ignore_patterns("*.kicad_prl"))
    (ws / "fab").mkdir()
    (ws / "fab" / "blinky2_gerbers.zip").write_bytes(b"zip")
    (ws / "state.json").write_text(json.dumps({
        "board": "blinky2", "phase": "P10", "workspace": "boards/blinky2",
        "artifacts": {
            "pcb": {"path": "kicad/blinky2.kicad_pcb", "kind": "pcb"},
            "gerbers": {"path": "fab/blinky2_gerbers.zip", "kind": "gerbers"},
            "constraints": {"path": "kicad/constraints.json",
                            "kind": "constraints"}}}), encoding="utf-8")
    (root / "other").mkdir()
    (root / "stray").mkdir()
    (root / "register.yaml").write_text(REG, encoding="utf-8")
    return root


def rename(capsys, *argv) -> tuple[int, dict]:
    code = board_rename.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def test_dry_run_writes_nothing(tmp_path, capsys):
    root = scratch(tmp_path)
    code, out = rename(capsys, "--all", "--dry-run", "--root", str(root))
    assert code == 0
    assert [(b["from"], b["to"]) for b in out["boards"]] == [
        ("blinky2", "PCB-0001-A_blinky2"), ("other", "PCB-0002-A_other")]
    assert (root / "blinky2").is_dir()
    assert (root / "register.yaml").read_text(encoding="utf-8") == REG


def test_rename_moves_dir_files_state_and_register(tmp_path, capsys):
    root = scratch(tmp_path)
    code, out = rename(capsys, "blinky2", "--no-verify", "--root", str(root))
    assert code == 0, out
    ws = root / "PCB-0001-A_blinky2"
    assert not (root / "blinky2").exists()
    new = "PCB-0001-A_blinky2"
    names = {p.name for p in (ws / "kicad").iterdir()}
    assert {f"{new}.kicad_pro", f"{new}.kicad_sch", f"{new}.kicad_pcb",
            "constraints.json"} <= names
    assert not any(n.startswith("blinky2.") for n in names)
    pro = json.loads((ws / "kicad" / f"{new}.kicad_pro").read_text())
    assert pro["meta"]["filename"] == f"{new}.kicad_pro"
    sch = (ws / "kicad" / f"{new}.kicad_sch").read_text(encoding="utf-8")
    assert '(project "blinky2"' not in sch
    assert f'(project "{new}"' in sch
    assert out["boards"][0]["project_refs_rewritten"] > 0
    st = json.loads((ws / "state.json").read_text())
    assert st["board"] == "blinky2"                 # the human name stays
    assert st["workspace"] == f"boards/{new}"
    assert st["artifacts"]["pcb"]["path"] == f"kicad/{new}.kicad_pcb"
    assert st["artifacts"]["gerbers"]["path"] == f"fab/{new}_gerbers.zip"
    assert st["artifacts"]["constraints"]["path"] == "kicad/constraints.json"
    assert (ws / "fab" / f"{new}_gerbers.zip").is_file()
    reg = (root / "register.yaml").read_text(encoding="utf-8")
    assert f"A: {{dir: {new}, date: 2026-09-01, bom: {new}/fab/BOM.csv}}" \
        in reg
    assert "{dir: other," in reg and reg.startswith("# the register's own")
    # a second run is a no-op for a board already renamed
    code, out = rename(capsys, "PCB-0001-A", "--no-verify", "--root", str(root))
    assert code == 0 and out["boards"][0]["done"] is True


def test_refusals(tmp_path, capsys):
    root = scratch(tmp_path)
    (root / "PCB-0002-A_other").mkdir()
    code, out = rename(capsys, "stray", "other", "--no-verify",
                       "--root", str(root))
    assert code == 1
    why = [b["refused"] for b in out["boards"]]
    assert "stray is not in" in why[0]
    assert "already exists" in why[1]
    assert (root / "other").is_dir()


def test_kicad_cli_opens_the_renamed_project(tmp_path, capsys):
    if env.find_kicad_cli() is None:
        pytest.skip("no kicad-cli (set HWDE_KICAD_CLI)")
    root = scratch(tmp_path)
    code, out = rename(capsys, "blinky2", "--root", str(root))
    assert code == 0, out
    assert out["boards"][0]["result"] == {"verified": True, "problems": []}
    prl = {p.name for p in (root / "PCB-0001-A_blinky2" / "kicad").glob(
        "*.kicad_prl")}
    assert prl == set()   # the check's own .kicad_prl files are cleaned up


def test_rename_keeps_report_when_one_board_fails(tmp_path, capsys, monkeypatch):
    root = scratch(tmp_path)
    real_rename = board_rename.rename

    def flaky(root_, p, cli):
        if p["from"] == "blinky2":
            raise RuntimeError("kicad-cli timed out")
        return real_rename(root_, p, cli)
    monkeypatch.setattr(board_rename, "rename", flaky)
    code, out = rename(capsys, "--all", "--no-verify", "--root", str(root))
    assert code == 1
    by_from = {b.get("from"): b for b in out["boards"]}
    assert by_from["blinky2"]["error"] == "RuntimeError: kicad-cli timed out"
    assert "result" not in by_from["blinky2"]
    assert (root / "blinky2").is_dir()          # the failed one never moved
    assert by_from["other"]["result"]["verified"] is False
    assert (root / "PCB-0002-A_other").is_dir()  # the other one still ran


def test_redoc_dry_run_names_the_part_number(tmp_path, capsys):
    root = scratch(tmp_path)
    code = redoc_boards.main(["--root", str(root), "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    [row] = out["boards"]          # `other` has no workspace: not listed
    args = row["cc_docs"]
    assert row["pn"] == "PCB-0001-A"
    assert args[args.index("--describes") + 1] == "PCB-0001-A"
    # nothing filed yet: the Boards project (002)
    assert row["project"] == "Boards"
    assert args[args.index("--project") + 1] == "Boards"
    code = redoc_boards.main(["nope", "--root", str(root), "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["boards"][0]["error"] == "no workspace"


def test_register_line_checked_before_anything_moves(tmp_path, capsys):
    root = scratch(tmp_path)
    # a quoted dir with a trailing comment is still rewritten
    (root / "register.yaml").write_text(
        REG.replace("A: {dir: other,", 'A: {dir: "other",  # note\n        ')
        .replace("{dir: blinky2,", "{dir: blinky2,  # note\n        "),
        encoding="utf-8")
    code, out = rename(capsys, "other", "blinky2", "--no-verify",
                       "--root", str(root))
    assert code == 0, out
    reg = (root / "register.yaml").read_text(encoding="utf-8")
    assert 'dir: "PCB-0002-A_other",  # note' in reg
    assert "dir: PCB-0001-A_blinky2,  # note" in reg
    # a `dir:` the rewrite cannot find (YAML reads it, the regex does not):
    # refused before anything moves
    root = scratch(tmp_path / "second")
    (root / "register.yaml").write_text(
        REG.replace("{dir: other,", '{"dir": other,'), encoding="utf-8")
    code, out = rename(capsys, "PCB-0002-A", "--no-verify", "--root", str(root))
    assert code == 1
    assert "expected one `dir: other`, found 0" in out["boards"][0]["refused"]
    assert (root / "other").is_dir() and not (root / "PCB-0002-A_other").exists()


def _redoc(capsys, monkeypatch, root, payload, cc_docs=True):
    if cc_docs:
        monkeypatch.setattr(redoc_boards.shutil, "which", lambda _: "/x/cc-docs")
    else:
        monkeypatch.setattr(redoc_boards.shutil, "which", lambda _: None)
    monkeypatch.setattr(redoc_boards.report_gen, "run",
                        lambda ws, file_doc, kind: (dict(payload), 0))
    code = redoc_boards.main(["--root", str(root)])
    return code, json.loads(capsys.readouterr().out)


def test_redoc_counts_an_unfiled_board(tmp_path, capsys, monkeypatch):
    root = scratch(tmp_path)
    base = {"status": "pass", "pdf": "x.pdf", "warnings": [],
            "attached": ["blinky2_gerbers.zip"]}
    code, out = _redoc(capsys, monkeypatch, root,
                       {**base, "filed": "002-0001 x.pdf", "unchanged": False})
    assert code == 0 and out["status"] == "pass"
    code, out = _redoc(capsys, monkeypatch, root,
                       {**base, "filed": None, "unchanged": True})
    assert code == 0 and out["boards"][0]["unchanged"] is True
    code, out = _redoc(capsys, monkeypatch, root,
                       {**base, "filed": None, "unchanged": False})
    assert code == 1 and out["status"] == "violations"
    code, out = _redoc(capsys, monkeypatch, root, base, cc_docs=False)
    assert code == 2 and "cc-docs is not on PATH" in out["error"]


def test_redoc_counts_a_fab_set_that_did_not_go_up(tmp_path, capsys,
                                                  monkeypatch):
    """A board with a fab set whose files were not attached is not filed;
    attached, or skipped by a matching stamp, it is."""
    root = scratch(tmp_path)   # its fab/ holds blinky2_gerbers.zip
    (root / "blinky2" / "fab" / "BOM.csv").write_text("bom", encoding="utf-8")
    base = {"status": "pass", "pdf": "x.pdf", "warnings": [],
            "filed": "002-0001 x.pdf", "unchanged": False}
    code, out = _redoc(capsys, monkeypatch, root, {**base, "attached": []})
    assert code == 1 and out["status"] == "violations"
    code, out = _redoc(capsys, monkeypatch, root,
                       {**base, "attached": ["blinky2_gerbers.zip",
                                             "blinky2_BOM.csv"]})
    assert code == 0, out
    code, out = _redoc(capsys, monkeypatch, root,
                       {**base, "filed": None, "unchanged": True,
                        "attached": []})
    assert code == 0
    code = redoc_boards.main(["--root", str(root), "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert out["boards"][0]["attach"] == ["blinky2_gerbers.zip",
                                          "blinky2_BOM.csv"]


def test_redoc_keeps_report_when_one_board_fails(tmp_path, capsys, monkeypatch):
    root = scratch(tmp_path)
    (root / "other" / "state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(redoc_boards.shutil, "which", lambda _: "/x/cc-docs")

    def flaky(ws, file_doc, kind):
        if Path(ws).name == "other":
            raise RuntimeError("kicad-cli timed out")
        return ({"status": "pass", "pdf": "x.pdf", "warnings": [],
                 "filed": "002-0001 x.pdf", "unchanged": False,
                 "attached": []}, 0)
    monkeypatch.setattr(redoc_boards.report_gen, "run", flaky)
    # blinky2's design doc is already filed (a scratch register): its row
    # names that document's project, the one the filing will reuse
    lib = tmp_path / "docs-lib"
    lib.mkdir()
    (lib / "register.json").write_text(json.dumps({"documents": {
        "002-0017": {"number": "002-0017", "project": "002",
                     "title": "blinky2 design document",
                     "source": "/old/blinky2/reports/design_doc/b.tex",
                     "revisions": [{"rev": "A"}]}}}), encoding="utf-8")
    monkeypatch.setenv("CC_DOCS_ROOT", str(lib))
    code = redoc_boards.main(["--root", str(root)])
    out = json.loads(capsys.readouterr().out)
    by_board = {b["board"]: b for b in out["boards"]}
    assert by_board["PCB-0002-A"]["error"] == "RuntimeError: kicad-cli timed out"
    assert "status" not in by_board["PCB-0002-A"]
    assert by_board["PCB-0002-A"]["project"] == "Boards"   # no board in its state
    assert by_board["PCB-0001-A"]["status"] == "pass"
    assert by_board["PCB-0001-A"]["project"] == "002"
    assert code == 1


def test_redoc_dry_run_kind_names_that_document(tmp_path, capsys):
    root = scratch(tmp_path)
    code = redoc_boards.main(["--root", str(root), "--dry-run", "--kind", "highlight"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0, out
    args = out["boards"][0]["cc_docs"]
    assert args[args.index("--title") + 1] == "PCB-0001-A blinky2 highlight doc"
    assert args[1].endswith("reports/highlight/blinky2-highlight.pdf")
    code = redoc_boards.main(["--root", str(root), "--dry-run"])
    args = json.loads(capsys.readouterr().out)["boards"][0]["cc_docs"]
    assert args[args.index("--title") + 1] == "PCB-0001-A blinky2 design doc"
