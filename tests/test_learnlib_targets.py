"""learnlib target resolution for workspaces outside the module repo.

U15 research tasks write LEARNINGS entries that name the workspace's own
outputs as boards/<board>/research/...; a workspace kept elsewhere holds
them at research/... under the workspace. validate must accept those, and
a workspace with no LEARNINGS.md and no queue has nothing to rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import learnlib  # noqa: E402

ENTRY = """# Learnings

## 2026-09-27 [P2][research] research task block-x-1: 1 verified record
- rec boards/{name}/research/records/rec.yaml
- gone boards/{name}/research/records/gone.yaml
"""


def _ws(tmp_path: Path, name: str = "demo-board") -> Path:
    ws = tmp_path / name
    (ws / "research" / "records").mkdir(parents=True)
    (ws / "research" / "records" / "rec.yaml").write_text("id: rec\n")
    (ws / "LEARNINGS.md").write_text(ENTRY.format(name=name))
    return ws


def test_workspace_research_target_resolves(tmp_path):
    ws = _ws(tmp_path)
    queue, _ = learnlib.compile_queue(ws, board="demo-board")
    targets = queue["entries"][0]["targets"]
    assert "boards/demo-board/research/records/rec.yaml" in targets
    # A file that is not there is still not a target.
    assert not any("gone.yaml" in t for t in targets)
    learnlib.save_queue(ws, queue)
    problems, _ = learnlib.validate_queue(ws)
    assert not any("does not exist" in p for p in problems), problems


def test_target_exists_by_board_name(tmp_path):
    ws = _ws(tmp_path, "PCB-0099-A_demo")
    rel = "boards/demo/research/records/rec.yaml"
    assert learnlib._target_exists(rel, ws, "demo")
    assert not learnlib._target_exists(rel, ws, "other")
    assert not learnlib._target_exists(rel)


def test_no_learnings_no_queue_is_a_warning(tmp_path):
    ws = tmp_path / "empty-board"
    ws.mkdir()
    problems, warnings = learnlib.validate_queue(ws)
    assert problems == []
    assert warnings and "nothing to rule" in warnings[0]


def test_missing_workspace_is_a_problem(tmp_path):
    problems, _ = learnlib.validate_queue(tmp_path / "no-such-board")
    assert problems and "does not exist" in problems[0]
