"""The per-test limit in conftest: a hung test fails by name and leaves no solver running (#801)."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from conftest import EM, TEST_LIMIT, time_limit


def test_a_test_past_its_limit_fails_by_name():
    with pytest.raises(pytest.fail.Exception, match="the_test ran past its limit of 0.05 s"):
        with time_limit(0.05, "the_test"):
            time.sleep(10)


def test_the_limit_kills_the_solver_it_interrupts(tmp_path):
    pidfile = tmp_path / "pid"
    t0 = time.time()
    with pytest.raises(pytest.fail.Exception):
        with time_limit(0.5, "the_test"):
            subprocess.run(["sh", "-c", f"echo $$ > {pidfile}; exec sleep 30"], capture_output=True, timeout=20)
    assert time.time() - t0 < 10
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_the_limit_is_cleared_when_the_block_ends():
    with time_limit(0.05):
        pass
    time.sleep(0.1)


def test_a_test_in_both_tiers_gets_the_longer_limit():
    assert list(TEST_LIMIT) == ["em", "slow"] and TEST_LIMIT["em"] > TEST_LIMIT["slow"]


def test_every_em_entry_names_a_test_that_exists():
    root = Path(__file__).resolve().parents[1]
    for nodeid in EM:
        path, name = nodeid.split("::")
        assert f"def {name}(" in (root / path).read_text(), nodeid
