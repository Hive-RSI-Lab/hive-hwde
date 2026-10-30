"""env asks kicad-cli its version once per binary (issue #200): the answer is
kept in the process and in HWDE_HOME, keyed by the binary, so another script
or a rewritten-but-identical wrapper doesn't start KiCad again, and a changed
binary is asked anew."""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS / "lib"))

import env  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="sh stub")


def _stub(path: Path, count: Path, ver: str) -> Path:
    path.write_text(f'#!/bin/sh\necho x >>"{count}"\necho {ver}\n')
    path.chmod(0o755)
    return path


def _runs(count: Path) -> int:
    return len(count.read_text().splitlines()) if count.exists() else 0


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(env, "_VERSION_LINES", {})
    return tmp_path / "state"


def test_asked_once_then_read_from_the_cache(tmp_path, state, monkeypatch):
    count = tmp_path / "count"
    cli = _stub(tmp_path / "kicad-cli", count, "10.0.6")
    assert env.kicad_cli_version(cli) == (10, 0, 6)
    assert env.kicad_cli_version(cli) == (10, 0, 6)
    assert _runs(count) == 1
    # A new process (empty memo) reads the file, as does a wrapper rewritten
    # with the same contents (bin/hwde writes its wrapper on every run).
    monkeypatch.setattr(env, "_VERSION_LINES", {})
    _stub(cli, count, "10.0.6")
    assert env.kicad_cli_version_line(cli) == "10.0.6"
    assert _runs(count) == 1
    assert (state / "kicad-cli-version.json").is_file()


def test_a_changed_binary_is_asked_again(tmp_path, state):
    count = tmp_path / "count"
    cli = _stub(tmp_path / "kicad-cli", count, "10.0.6")
    assert env.kicad_cli_version(cli) == (10, 0, 6)
    _stub(cli, count, "10.0.7")
    assert env.kicad_cli_version(cli) == (10, 0, 7)
    assert _runs(count) == 2


def test_a_failed_answer_is_not_kept(tmp_path, state):
    count = tmp_path / "count"
    cli = _stub(tmp_path / "kicad-cli", count, "garbage")
    assert env.kicad_cli_version(cli) == ()
    assert env.kicad_cli_version(cli) == ()
    assert _runs(count) == 2
    assert env.kicad_cli_version(tmp_path / "missing") == ()


def test_an_unwritable_state_dir_still_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(env, "_VERSION_LINES", {})
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("HWDE_HOME", str(blocker / "state"))
    cli = _stub(tmp_path / "kicad-cli", tmp_path / "count", "10.0.6")
    assert env.kicad_cli_version(cli) == (10, 0, 6)
