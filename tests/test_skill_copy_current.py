"""check_env's skill-copy-current check: a board repo's vendored copy of the
skill that is behind ai-ee main fails check_env before a run spends on it
(PCB-0018 routed 57 min from a copy without main's HV net classes).

Each case builds its own source repo and vendoring repo in tmp_path.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_env  # noqa: E402

SKILL = check_env.SKILL_REL


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_skill(repo, text, other=False):
    """Write one file under the skill dir (or outside it) and commit."""
    f = repo / (("docs/x.md") if other else f"{SKILL}/SKILL.md")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", text)


def make_source(tmp_path, n):
    """ai-ee stand-in: n skill commits v0..v{n-1} on main, one unrelated commit."""
    src = tmp_path / "ai-ee"
    src.mkdir()
    git(src, "init", "-qb", "main")
    for i in range(n):
        commit_skill(src, f"v{i}")
    commit_skill(src, "unrelated", other=True)  # not a skill commit: not counted
    return src


def make_vendor(tmp_path, text):
    """boards stand-in: a separate repo whose skill dir holds `text`."""
    b = tmp_path / "boards"
    b.mkdir()
    git(b, "init", "-qb", "main")
    commit_skill(b, text)
    return b / SKILL


def test_behind_copy_fails_with_count_and_sync_remediation(tmp_path):
    src = make_source(tmp_path, 3)
    c = check_env.check_skill_copy(make_vendor(tmp_path, "v0"), src)
    assert c["status"] == "fail"
    assert "2 commits behind" in c["detail"]
    assert "bin/sync-skill" in c["remediation"]


def test_level_copy_passes(tmp_path):
    src = make_source(tmp_path, 3)
    c = check_env.check_skill_copy(make_vendor(tmp_path, "v2"), src)
    assert c["status"] == "pass"
    assert "level" in c["detail"]


def test_copy_matching_no_commit_warns(tmp_path):
    src = make_source(tmp_path, 2)
    c = check_env.check_skill_copy(make_vendor(tmp_path, "edited in place"), src)
    assert c["status"] == "warn"
    assert "sync-skill --check" in c["remediation"]


def test_origin_main_is_preferred_over_stale_local_main(tmp_path):
    """The planning checkout's local main may lag; origin/main is what landed."""
    src = make_source(tmp_path, 3)
    git(src, "update-ref", "refs/remotes/origin/main", "main")
    git(src, "reset", "-q", "--hard", "main~3")  # local main back at v0
    c = check_env.check_skill_copy(make_vendor(tmp_path, "v1"), src)
    assert c["status"] == "fail"
    assert "1 commit behind ai-ee origin/main" in c["detail"]


def test_no_source_repo_means_no_check(tmp_path):
    vend = make_vendor(tmp_path, "v0")
    assert check_env.check_skill_copy(vend, tmp_path / "missing") is None
    plain = tmp_path / "not-git"
    plain.mkdir()
    assert check_env.check_skill_copy(vend, plain) is None


def test_running_from_the_source_repo_means_no_check(tmp_path):
    """ai-ee itself (or a worktree of it, which shares its git dir) is not a copy."""
    src = make_source(tmp_path, 2)
    assert check_env.check_skill_copy(src / SKILL, src) is None
    wt = tmp_path / "wt"
    git(src, "worktree", "add", "-q", str(wt), "main~1")
    assert check_env.check_skill_copy(wt / SKILL, src) is None


def test_env_var_names_the_source(tmp_path, monkeypatch):
    src = make_source(tmp_path, 2)
    monkeypatch.setenv("HWDE_SOURCE_REPO", str(src))
    c = check_env.check_skill_copy(make_vendor(tmp_path, "v0"))
    assert c["status"] == "fail" and "1 commit behind" in c["detail"]
