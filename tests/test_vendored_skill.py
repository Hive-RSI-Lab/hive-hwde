"""A copy of the skill vendored outside the hwde repo (the boards repo carries
only .claude/skills/hwde) still finds its tools and degrades cleanly where it
needs files only the hwde checkout has (root LEARNINGS, triage, bench
fixtures). Each case runs the COPIED modules in a subprocess so the import is
of the copy, never of this checkout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / ".claude" / "skills" / "hwde"

PROBE = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from lib import env, learnlib, benchlib
out = {"repo_root": str(env.repo_root()), "tools_dir": str(env.tools_dir()),
       "jar": str(env.find_freerouting_jar()), "krt": str(env.find_krt()),
       "triage": learnlib.triage_summary()}
try:
    learnlib._need_root_files(learnlib.ROOT_LEARNINGS, learnlib.TRIAGE)
except ValueError as exc:
    out["promote"] = str(exc)
try:
    benchlib.load_manifest()
except benchlib.BenchError as exc:
    out["bench"] = str(exc)
print(json.dumps(out))
"""


def _vendored(tmp_path: Path) -> Path:
    """tmp/boards = a repo holding only the skill, like ihsan-sa/boards."""
    boards = tmp_path / "boards"
    (boards / ".git").mkdir(parents=True)
    dst = boards / ".claude" / "skills" / "hwde"
    shutil.copytree(SKILL, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst / "scripts"


def _tools(root: Path) -> Path:
    (root / "freerouting").mkdir(parents=True)
    (root / "freerouting" / "freerouting-2.2.4.jar").write_bytes(b"jar")
    plugins = root / "krt" / "KiCadRoutingTools-0.19.0" / "plugins"
    plugins.mkdir(parents=True)
    return root


def _probe(scripts: Path, home: Path, **extra: str) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HWDE_", "AIEE_"))}
    env.update(HOME=str(home), **extra)
    cp = subprocess.run([sys.executable, "-c", PROBE, str(scripts),
                         str(home)], capture_output=True, text=True, env=env)
    assert cp.returncode == 0, cp.stderr
    return json.loads(cp.stdout)


def test_vendored_copy_uses_hwde_checkout_tools(tmp_path):
    scripts = _vendored(tmp_path)
    home = tmp_path / "home"
    tools = _tools(home / "dev" / "ai-ee" / "tools")
    out = _probe(scripts, home)
    assert out["repo_root"] == str(tmp_path / "boards")
    assert out["tools_dir"] == str(tools)
    assert out["jar"].startswith(str(tools / "freerouting"))
    assert out["krt"].startswith(str(tools / "krt"))


def test_tools_dir_pin_wins_and_own_tools_beat_default(tmp_path):
    scripts = _vendored(tmp_path)
    home = tmp_path / "home"
    _tools(home / "dev" / "ai-ee" / "tools")
    pinned = _tools(tmp_path / "pinned")
    assert _probe(scripts, home, HWDE_TOOLS_DIR=str(pinned))["tools_dir"] \
        == str(pinned)
    own = _tools(tmp_path / "boards" / "tools")
    assert _probe(scripts, home)["tools_dir"] == str(own)


def test_tools_dir_pin_that_is_missing_fails_loudly(tmp_path, monkeypatch):
    sys.path.insert(0, str(SKILL / "scripts"))
    from lib import env
    monkeypatch.setenv("HWDE_TOOLS_DIR", str(tmp_path / "nope"))
    with pytest.raises(env.EnvError, match="HWDE_TOOLS_DIR"):
        env.tools_dir()


def test_vendored_copy_degrades_without_repo_files(tmp_path):
    scripts = _vendored(tmp_path)
    home = tmp_path / "home"
    _tools(home / "dev" / "ai-ee" / "tools")
    out = _probe(scripts, home)
    assert out["triage"]["rows"] == 0 and "absent" in out["triage"]
    assert "LEARNINGS.md not found" in out["promote"]
    assert "vendored copy" in out["bench"]
