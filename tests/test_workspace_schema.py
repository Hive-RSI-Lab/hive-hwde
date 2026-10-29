"""Workspace layout schema (state.json workspace_schema) and the one
check-report location: <ws>/reports/checks. Old workspaces hold
kicad/reports/checks; readers fall back to it and `state.py resume` moves it.
Every case builds its own workspace."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import state as state_mod  # noqa: E402
import statelib  # noqa: E402
import task_router  # noqa: E402
from checklib import CheckError  # noqa: E402


def _ws(tmp_path: Path, schema: int | None) -> Path:
    """A workspace whose state.json carries `schema` (None = absent, i.e. 1)."""
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "b")
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    if schema is None:
        st.pop("workspace_schema")
    else:
        st["workspace_schema"] = schema
    (ws / "state.json").write_text(json.dumps(st), encoding="utf-8")
    return ws


def _put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _resume(ws: Path, *extra: str) -> dict:
    result, _ = state_mod.run(["resume", "--workspace", str(ws), *extra])
    return result


def test_init_stamps_current_schema(tmp_path):
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "b")
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert st["workspace_schema"] == statelib.WORKSPACE_SCHEMA == 2


def test_resume_moves_legacy_reports_and_keeps_a_twin(tmp_path):
    ws = _ws(tmp_path, None)
    old = ws / "kicad" / "reports" / "checks"
    _put(old / "check_silk.json", "old-silk")
    _put(old / "summary.json", "old-summary")
    _put(ws / "reports" / "checks" / "summary.json", "new-summary")

    res = _resume(ws)

    by_from = {a["from"]: a["action"] for a in res["workspace_migration"]}
    assert by_from == {"kicad/reports/checks/check_silk.json": "moved",
                       "kicad/reports/checks/summary.json": "kept"}
    new = ws / "reports" / "checks"
    assert (new / "check_silk.json").read_text() == "old-silk"
    # the report already at the new path wins; its twin is not deleted
    assert (new / "summary.json").read_text() == "new-summary"
    assert (old / "summary.json").read_text() == "old-summary"
    assert not (old / "check_silk.json").exists()
    st = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert st["workspace_schema"] == 2
    assert st["history"][-1]["event"] == "workspace_migrate"


def test_resume_removes_emptied_legacy_dirs(tmp_path):
    ws = _ws(tmp_path, None)
    _put(ws / "kicad" / "reports" / "checks" / "check_pdn.json", "x")
    _resume(ws)
    assert not (ws / "kicad" / "reports").exists()
    assert (ws / "kicad").is_dir()


def test_resume_on_current_workspace_writes_nothing(tmp_path):
    ws = _ws(tmp_path, 2)
    # a stray legacy file on a schema-2 workspace is not touched
    _put(ws / "kicad" / "reports" / "checks" / "check_pdn.json", "x")
    before = (ws / "state.json").read_bytes()
    res = _resume(ws)
    assert res["workspace_migration"] == []
    assert (ws / "state.json").read_bytes() == before
    assert (ws / "kicad" / "reports" / "checks" / "check_pdn.json").exists()


def test_resume_no_migrate_reports_pending_and_writes_nothing(tmp_path):
    ws = _ws(tmp_path, None)
    _put(ws / "kicad" / "reports" / "checks" / "check_pdn.json", "x")
    before = (ws / "state.json").read_bytes()
    res = _resume(ws, "--no-migrate")
    assert res["workspace_migration"] == "pending"
    assert (ws / "state.json").read_bytes() == before
    assert (ws / "kicad" / "reports" / "checks" / "check_pdn.json").exists()


def test_resume_refuses_a_newer_schema(tmp_path):
    ws = _ws(tmp_path, statelib.WORKSPACE_SCHEMA + 1)
    before = (ws / "state.json").read_bytes()
    with pytest.raises(CheckError, match="newer than this build"):
        _resume(ws)
    assert (ws / "state.json").read_bytes() == before


def test_check_reports_dir_falls_back_only_when_new_is_empty(tmp_path):
    ws = tmp_path / "ws"
    assert statelib.check_reports_dir(ws) == ws / "reports" / "checks"
    _put(ws / "kicad" / "reports" / "checks" / "summary.json", "{}")
    assert statelib.check_reports_dir(ws) == ws / "kicad" / "reports" / "checks"
    _put(ws / "reports" / "checks" / "summary.json", "{}")
    assert statelib.check_reports_dir(ws) == ws / "reports" / "checks"


def test_reports_dir_for_inside_and_outside_a_workspace(tmp_path):
    ws = tmp_path / "ws"
    state_mod.State.init(ws, "b")
    assert statelib.reports_dir_for(ws / "kicad" / "b.kicad_pcb") == \
        ws / "reports" / "checks"
    loose = tmp_path / "corpus" / "b.kicad_pcb"
    loose.parent.mkdir()
    assert statelib.reports_dir_for(loose) == \
        loose.parent / "reports" / "checks"


def test_router_checks_slot_follows_the_reader(tmp_path):
    imap = statelib.load_map()
    ws = _ws(tmp_path, None)
    _put(ws / "kicad" / "reports" / "checks" / "summary.json", "{}")
    slots = task_router.workspace_context(ws, None, imap)["slots"]
    assert slots["checks"].endswith("/kicad/reports/checks")
    _resume(ws)
    slots = task_router.workspace_context(ws, None, imap)["slots"]
    assert slots["checks"].endswith("/ws/reports/checks")
