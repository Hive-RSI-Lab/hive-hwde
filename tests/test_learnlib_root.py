"""Root lessons in the module: learnings.d/ is their home, and triage says so
loudly when a checkout is missing what it counts.

The module carries no root LEARNINGS.md archive or ladder-triage register
(both stayed with the hwde repo), so `triage` used to print a clean pass with
every count zero and `root_learnings` promotion refused every entry. Now the
lesson files are the source; the archive pair is optional but must come
together, and a missing learnings.d/ is a named failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import learnlib  # noqa: E402
import learnings  # noqa: E402

ENTRY = """# Learnings

## 2026-11-13 [P5][drc] keepouts belong in the template
The template must carry the antenna keepout, not the router.
"""
TRIAGE = {"now": "L0", "target": "L2", "owner": "scripts/gate.py",
          "status": "open", "note": "gate on keepout presence"}


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "demo-board"
    ws.mkdir()
    (ws / "LEARNINGS.md").write_text(ENTRY)
    return ws


def _eid(ws: Path) -> str:
    entries, _ = learnlib.parse_entries((ws / "LEARNINGS.md").read_text())
    return entries[0]["entry"]


def test_module_ships_the_lessons_home():
    assert learnlib.LESSONS.is_dir()
    assert (learnlib.LESSONS / learnlib.LESSONS_README).is_file()


def test_module_triage_passes_with_real_sources():
    rc = learnings.main(["triage", "--out", "/dev/null"])
    assert rc == 0
    s = learnlib.triage_summary()
    assert s["problems"] == []
    assert s["sources"]["lessons"].endswith("learnings.d")
    assert s["lessons"] == len(learnlib.lesson_files())


def test_promote_without_archive_then_triage_counts_it(tmp_path):
    ws = _ws(tmp_path)
    lessons = tmp_path / "learnings.d"
    lessons.mkdir()
    gone = tmp_path / "LEARNINGS.md", tmp_path / "ladder-triage.md"
    moved = learnlib.promote_to_root(ws, _eid(ws), TRIAGE, root=gone[0],
                                     triage_file=gone[1], lessons_dir=lessons)
    assert moved["file"].startswith("learnings.d/")
    s = learnlib.triage_summary(triage_file=gone[1], root=gone[0],
                                lessons_dir=lessons)
    assert s["problems"] == []
    assert s["lessons"] == 1 and s["rows"] == 1
    assert s["levels"]["L0"]["now"] == 1 and s["levels"]["L2"]["target"] == 1
    assert s["open_by_owner"] == {"scripts/gate.py": 1}
    assert s["sources"]["archive"] is None
    # The same lesson again is refused: the lesson files carry it.
    try:
        learnlib.promote_to_root(ws, _eid(ws), TRIAGE, root=gone[0],
                                 triage_file=gone[1], lessons_dir=lessons)
    except ValueError as exc:
        assert "already carry" in str(exc)
    else:
        raise AssertionError("duplicate promotion was accepted")


def test_promote_refuses_when_lessons_home_missing(tmp_path):
    ws = _ws(tmp_path)
    lessons = tmp_path / "learnings.d"
    try:
        learnlib.promote_to_root(ws, _eid(ws), TRIAGE,
                                 root=tmp_path / "LEARNINGS.md",
                                 triage_file=tmp_path / "t.md",
                                 lessons_dir=lessons)
    except ValueError as exc:
        assert str(lessons) in str(exc)
    else:
        raise AssertionError("promotion created a home nobody reads")
    assert not lessons.exists()


def test_triage_names_missing_lessons_dir(tmp_path):
    lessons = tmp_path / "learnings.d"
    s = learnlib.triage_summary(triage_file=tmp_path / "t.md",
                                root=tmp_path / "L.md", lessons_dir=lessons)
    assert any(str(lessons) in p for p in s["problems"])


def test_triage_names_half_an_archive(tmp_path):
    lessons = tmp_path / "learnings.d"
    lessons.mkdir()
    root = tmp_path / "LEARNINGS.md"
    root.write_text("# Learnings\n")
    tri = tmp_path / "ladder-triage.md"
    s = learnlib.triage_summary(triage_file=tri, root=root,
                                lessons_dir=lessons)
    assert len(s["problems"]) == 1 and str(tri) in s["problems"][0]
