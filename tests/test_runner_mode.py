"""bin/hwde is the module's one entry point: it must stay executable, in git
as well as on disk, or every agent's `bin/hwde <script>` is Permission denied
(issue #30: an installed copy was 0644 and had to be run as `sh bin/hwde`)."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bin_hwde_is_executable_on_disk():
    assert os.access(ROOT / "bin" / "hwde", os.X_OK)


def test_bin_hwde_is_executable_in_git():
    out = subprocess.run(["git", "ls-files", "-s", "bin/hwde"], cwd=ROOT,
                         capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout:
        return  # not a git checkout (an installed copy): the disk check stands
    assert out.stdout.split()[0] == "100755", out.stdout
