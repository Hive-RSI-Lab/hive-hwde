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
def _kicad_version() -> tuple[tuple[int, ...], str]:
    """(version, what it is) of the pipeline's kicad-cli, () when unknown.

    Asked once, at collection: a pin whose version probe fails (a slow
    kicad-cli on a strained machine times out) skips the recorded tests with
    the reason, never aborts the whole run (issue #252)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "lib"))
    import env
    try:
        cli = env.find_kicad_cli()
    except env.EnvError as e:
        return (), f"no usable kicad-cli ({e})"
    if not cli:
        return (), "no kicad-cli"
    ver = env.kicad_cli_version(cli)
    if not ver:
        return (), f"kicad-cli {cli} of unknown version"
    return ver, ".".join(map(str, ver))


# Upstream tests that fail in hive-hwde for a cause a brief already owns:
# each is an expected failure until that brief lands. An XPASS in the summary
# means its cause is gone: take its line out. (nodeid -> why) The _CITES lines
# that only lacked the source PDFs XPASS where `hwde sources` has fetched them
# (issue #97); they stay listed for a HOME that hasn't.
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
}


# Tests too heavy for the per-PR test (bin/hwde test deselects `slow`; bin/hwde
# test-slow runs them): each took 4 s or more on the Hive's loaded machine,
# together about 500 of its 790 s (261029-175316). A parametrized test is
# named without its ids, so every case of it is slow. (nodeid -> why)
_TOOLS = "runs the live toolchain (kicad-cli, KiCad's python, java)"
_BOARD = "runs a whole fixture board's checks or bench stage"
_INTAKE = "materializes a whole KiCad project through intake"
SLOW = {
    "tests/test_solvers.py::test_live_palace_validates": "runs Palace (FEM eigenmode) on the IIC-OSIC-TOOLS tree",
    "tests/test_solvers.py::test_live_openems_validates": "runs an openEMS full-wave solve (minutes)",
    "tests/test_check_env.py::test_passes_on_dev_machine": _TOOLS,
    "tests/test_check_env.py::test_out_file_and_quiet": _TOOLS,
    "tests/test_check_env.py::test_unpinned_discovery_still_resolves_10x": _TOOLS,
    "tests/test_check_env.py::test_missing_kicad_fails_with_remediation": _TOOLS,
    "tests/test_check_env.py::test_missing_java_fails_with_remediation": _TOOLS,
    "tests/test_board_rename.py::test_kicad_cli_opens_the_renamed_project": _TOOLS,
    "tests/test_board_setup.py::test_board_init_fixed_outline_too_tight_gets_no_default_holes": _TOOLS,
    "tests/test_build_modes.py::test_board_init_cli_does_not_refuse_a_fixed_outline": _TOOLS,
    "tests/test_bench.py::test_offline_fixture_matches_baseline": _BOARD,
    "tests/test_dfm_zone_fill.py::test_live_refill_confirms_dfm1": _TOOLS,
    "tests/test_bench.py::test_known_answers_fire_where_expected": _BOARD,
    "tests/test_mcp_server.py::test_dfm_check_is_read_only": _BOARD,
    "tests/test_mcp_server.py::test_review_runs_every_gate_read_only": _BOARD,
    "tests/test_checks_s5.py::test_full_manifest_coverage": _BOARD,
    "tests/test_research.py::test_bench_p1_freeze_score_baseline_and_compare": _BOARD,
    "tests/test_research.py::test_validate_refuses_contract_breaches": _BOARD,
    "tests/test_learn.py::test_freeze_baseline_then_seeded_regression_exits_1": _BOARD,
    "tests/test_sim_board.py::test_engine_resistor_overpower": _BOARD,
    "tests/test_layout_sim.py::test_mutant_current_density_max_at_neck": _BOARD,
    "tests/test_intake.py::test_intake_refuses_an_existing_workspace_unless_forced": _INTAKE,
    "tests/test_intake.py::test_schematic_only_project_lands_at_p4": _INTAKE,
    "tests/test_intake.py::test_hermetic_intake_never_writes_to_the_source": _INTAKE,
    "tests/test_intake.py::test_no_upgrade_mixed_versions_is_refused_before_materializing": _INTAKE,
    "tests/test_intake.py::test_hermetic_intake_builds_a_valid_workspace": _INTAKE,
    "tests/test_intake.py::test_board_only_project_imports_with_a_no_schematic_warning": _INTAKE,
}


@pytest.hookimpl(tryfirst=True)  # before -m deselects by mark
def pytest_collection_modifyitems(config, items):
    for item in items:
        why = KNOWN_FAILING.get(item.nodeid)
        if why:
            item.add_marker(pytest.mark.xfail(reason=why, strict=False))
        if item.nodeid.split("[")[0] in SLOW:
            item.add_marker(pytest.mark.slow)
    marked = [i for i in items if i.get_closest_marker("kicad_recorded")]
    if not marked:
        return
    ver, have = _kicad_version()
    if ver[:3] == RECORDED_KICAD:
        return
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
    # and writes no board's ledger.json unless it says so (test_ledger)
    monkeypatch.setenv("HWDE_LEDGER_WRITE", "0")
    # and fsyncs nothing (safelib): every file here is scratch, and on a
    # loaded disk each fsync costs seconds (test_attest spent 8 of 12 s in four)
    monkeypatch.setenv("HWDE_NO_FSYNC", "1")
    monkeypatch.setenv("HWDE_EASYEDA_CACHE",
                       str(tmp_path_factory.mktemp("easyeda_empty")))
    # The rate-limit latch is process state: one test's mocked 403 must not
    # skip every later test's fetches.
    mod = sys.modules.get("easyeda")
    if mod is not None and hasattr(mod, "reset_rate_limit"):
        mod.reset_rate_limit()
