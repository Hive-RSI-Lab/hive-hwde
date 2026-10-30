"""Suite-wide guards: no test may file a document in the real register, and a
test marked `kicad_recorded` runs only on the kicad-cli its numbers were
recorded on (RECORDED_KICAD). On any other version it is skipped with the
reason, because the numbers move for known reasons (LEARNINGS 2026-08-27,
"KiCad 10.0.5 vs 10.0.3 deltas") until someone re-baselines them on purpose.
"""
import functools
import os
import shutil
import sys
from pathlib import Path

import pytest

RECORDED_KICAD = (10, 0, 3)


@functools.cache
def _kicad_version() -> tuple[int, ...]:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".claude"
                           / "skills" / "hwde" / "scripts" / "lib"))
    import env
    cli = env.find_kicad_cli()
    return env.kicad_cli_version(cli) if cli else ()


# Upstream tests that fail in hive-hwde for a cause a brief already owns:
# each is an expected failure until that brief lands. An XPASS in the summary
# means its cause is gone: take its line out. (nodeid -> why)
_CITES = ("the records cite files the module doesn't carry: source PDFs, "
          "upstream's LEARNINGS.md and board workspaces (261028-141932, "
          "261028-154518)")
KNOWN_FAILING = {
    "tests/test_coverage.py::test_committed_library_lint_green_and_checklists_present": _CITES,
    "tests/test_coverage.py::test_committed_records_are_strict_green_after_the_u14_backfill": _CITES,
    "tests/test_coverage.py::test_strict_cli_flag": _CITES,
    "tests/test_coverage.py::test_lint_generalizes_must_point_at_a_more_general_record": _CITES,
    "tests/test_coverage.py::test_instance_level_envelope_optional_and_proven_needs_evidence": _CITES,
    "tests/test_coverage.py::test_prove_upgrades_applied_records_only_and_is_idempotent": _CITES,
    "tests/test_coverage.py::test_prove_reaches_below_approved": _CITES,
    "tests/test_knowledge.py::test_committed_records_lint_green": _CITES,
    "tests/test_knowledge.py::test_validate_cli_contract": _CITES,
    "tests/test_knowledge.py::test_lint_accepts_a_not_redistributed_sidecar": _CITES,
    "tests/test_knowledge.py::test_app_note_grounding_payload": _CITES,
    "tests/test_knowledge.py::test_app_note_records_committed_and_cite_the_pdf": _CITES,
    "tests/test_lib_hygiene.py::test_write_preserves_lib_symbols": "kicad-sch-api "
        "can't read KiCad 10's .kicad_symdir libraries; bin/hwde flattens only "
        "power and Device (workaround symdir, 261028-141924)",
    "tests/test_check_env.py::test_unpinned_discovery_still_resolves_10x": "the "
        "Hive's KiCad is an AppImage in its tools folder, found only through "
        "bin/hwde's HWDE_KICAD_CLI pin, not by env.py's own discovery",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        why = KNOWN_FAILING.get(item.nodeid)
        if why:
            item.add_marker(pytest.mark.xfail(reason=why, strict=False))
    marked = [i for i in items if i.get_closest_marker("kicad_recorded")]
    if not marked:
        return
    ver = _kicad_version()
    if ver[:3] == RECORDED_KICAD:
        return
    have = ".".join(map(str, ver)) or "no kicad-cli"
    skip = pytest.mark.skip(reason=(
        f"numbers recorded on KiCad {'.'.join(map(str, RECORDED_KICAD))}, "
        f"this is {have} (LEARNINGS 2026-08-27)"))
    for item in marked:
        item.add_marker(skip)


@pytest.fixture(autouse=True)
def _never_file_documents(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("DOC_")]:
        monkeypatch.delenv(k)
    # report_gen looks the board's filed documents up in cc-docs' register:
    # never the owner's, so a test reads an empty one unless it writes its own
    # (the env var reaches scripts a test runs as subprocesses too)
    empty = tmp_path_factory.mktemp("no-docs-register")
    monkeypatch.setenv("CC_DOCS_ROOT", str(empty))
    if "report_gen" in sys.modules:
        monkeypatch.setattr(sys.modules["report_gen"], "DOCS_HOME", empty)
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if d and not os.access(os.path.join(d, "cc-docs"), os.X_OK)]
    monkeypatch.setenv("PATH", os.pathsep.join(dirs))
    assert shutil.which("cc-docs") is None


@pytest.fixture(autouse=True)
def _easyeda_offline(monkeypatch, tmp_path_factory):
    """No test fetches an LCSC footprint model or reads the user's cache: a
    test that needs models copies tests/fixtures/easyeda into its own dir."""
    monkeypatch.setenv("HWDE_EASYEDA_OFFLINE", "1")
    # nor asks JLC or LCSC for stock (test_stock_check fakes the sources)
    monkeypatch.setenv("HWDE_STOCK_OFFLINE", "1")
    # a mocked 403 latches at once instead of sleeping through the back-off
    monkeypatch.setenv("HWDE_EASYEDA_BACKOFF", "0")
    # and writes no board's ledger.json unless it says so (test_ledger)
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "0")
    monkeypatch.setenv("HWDE_EASYEDA_CACHE",
                       str(tmp_path_factory.mktemp("easyeda_empty")))
    # The rate-limit latch is process state: one test's mocked 403 must not
    # skip every later test's fetches.
    mod = sys.modules.get("easyeda")
    if mod is not None and hasattr(mod, "reset_rate_limit"):
        mod.reset_rate_limit()
