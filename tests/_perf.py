"""Wall-clock limits for the few smoke tests that time a real run.

A timing test measures the machine as much as the code. On an idle box the
limit holds; on a box whose 1-minute load average is above its CPU count
(the landing gate runs beside other suites) the elapsed time says nothing
about the change, so the limit SKIPS with the load and the time it measured
instead of failing.
"""
from __future__ import annotations

import os

import pytest


def assert_under(elapsed: float, limit: float, what: str) -> None:
    """Fail when `elapsed` >= `limit` seconds on an idle box; skip when busy."""
    if elapsed < limit:
        return
    load = os.getloadavg()[0]
    cpus = os.cpu_count() or 1
    if load > cpus:
        pytest.skip(f"{what} took {elapsed:.1f}s (limit {limit:.0f}s) but the "
                    f"box was busy: load {load:.1f} on {cpus} CPUs")
    raise AssertionError(f"{what} took {elapsed:.1f}s (limit {limit:.0f}s, "
                         f"load {load:.1f} on {cpus} CPUs)")
