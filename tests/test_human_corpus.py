"""human_corpus.py: the human-made board corpus driver.

Offline: gh is stubbed and no gate runs, so these tests check the manifest
bookkeeping (licence refusal, the human outcome field, the cache guard, the
KiCad 6 floor, the results table), not GitHub or the gates.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import human_corpus as hc  # noqa: E402

PCB6 = """(kicad_pcb
\t(version 20240108)
\t(generator "pcbnew")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(4 "In1.Cu" power)
\t\t(6 "In2.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t)
)
"""


def _board(**kw) -> dict:
    b = {"id": "b1", "url": "https://github.com/o/r", "commit": "", "licence": "",
         "licence_source": "", "domain": "power", "pcb": "",
         "outcome": {"label": "", "source": ""}}
    b.update(kw)
    return b


def _stub_gh(monkeypatch, answers: dict) -> None:
    def gh(path):
        for key, val in answers.items():
            if path.startswith(key):
                return val
        return None
    monkeypatch.setattr(hc, "gh", gh)


def test_pin_takes_a_permissive_licence_and_the_single_project(monkeypatch):
    _stub_gh(monkeypatch, {
        "repos/o/r/commits/": {"sha": "a" * 40},
        "repos/o/r/git/trees/": {"tree": [{"path": "hw/x.kicad_pro"}, {"path": "hw/x.kicad_pcb"}]},
        "repos/o/r": {"license": {"spdx_id": "MIT"}, "default_branch": "main"},
    })
    b = _board()
    assert hc.pin_board(b) == ""
    assert (b["licence"], b["commit"], b["pcb"]) == ("MIT", "a" * 40, "hw/x.kicad_pcb")
    assert "excluded" not in b


@pytest.mark.parametrize("spdx", [None, "NOASSERTION", "GPL-3.0"])
def test_pin_never_takes_an_unlicensed_or_copyleft_software_board(monkeypatch, spdx):
    _stub_gh(monkeypatch, {"repos/o/r": {"license": {"spdx_id": spdx} if spdx else None,
                                         "default_branch": "main"}})
    b = _board()
    assert hc.pin_board(b)
    assert b["excluded"]
    assert not b["commit"]


def test_pin_keeps_a_hand_set_licence_and_pcb(monkeypatch):
    _stub_gh(monkeypatch, {
        "repos/o/r/commits/": {"sha": "b" * 40},
        "repos/o/r": {"license": {"spdx_id": "NOASSERTION"}, "default_branch": "main"},
    })
    b = _board(licence="CERN-OHL-W-2.0", licence_source="README", pcb="a/b.kicad_pcb")
    assert hc.pin_board(b) == ""
    assert (b["licence"], b["licence_source"], b["pcb"]) == ("CERN-OHL-W-2.0", "README", "a/b.kicad_pcb")


def test_pin_asks_for_pcb_when_the_repo_has_several_projects(monkeypatch):
    _stub_gh(monkeypatch, {
        "repos/o/r/commits/": {"sha": "c" * 40},
        "repos/o/r/git/trees/": {"tree": [{"path": "a.kicad_pro"}, {"path": "b.kicad_pro"}]},
        "repos/o/r": {"license": {"spdx_id": "MIT"}, "default_branch": "main"},
    })
    b = _board()
    assert "set pcb by hand" in hc.pin_board(b)
    assert not b["pcb"]


def test_label_writes_evidence_and_never_the_outcome(monkeypatch):
    _stub_gh(monkeypatch, {
        "repos/o/r/releases": [{"tag_name": "v1"}],
        "repos/o/r/issues?state=closed": [
            {"number": 3, "title": "Errata: Q1 footprint swapped", "html_url": "u3"},
            {"number": 4, "title": "Add docs", "html_url": "u4"},
            {"number": 5, "title": "Rework guide", "html_url": "u5", "pull_request": {}}],
    })
    b = _board(outcome={"label": "", "source": ""})
    hc.label_board(b)
    assert b["outcome"] == {"label": "", "source": ""}
    assert b["evidence"][0].startswith("1 GitHub release")
    assert any("#3" in e for e in b["evidence"])
    assert not any("#4" in e or "#5" in e for e in b["evidence"])

    kept = _board(outcome={"label": "product", "source": "shop page"})
    hc.label_board(kept)
    assert kept["outcome"] == {"label": "product", "source": "shop page"}


def test_cache_inside_the_repo_or_boards_root_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("HWDE_BOARDS_ROOT", str(tmp_path / "boards"))
    with pytest.raises(SystemExit):
        hc.cache_root(str(REPO / "scratch"))
    with pytest.raises(SystemExit):
        hc.cache_root(str(tmp_path / "boards" / "c"))
    assert hc.cache_root(str(tmp_path / "cache")) == (tmp_path / "cache").resolve()


def test_pcb_facts_reads_version_and_copper_layers(tmp_path):
    pcb = tmp_path / "x.kicad_pcb"
    pcb.write_text(PCB6)
    assert hc.pcb_facts(pcb) == (20240108, 4)


def test_fetch_excludes_a_board_older_than_kicad6(tmp_path, monkeypatch):
    b = _board(commit="d" * 40, pcb="x.kicad_pcb")
    dest = hc.checkout_dir(tmp_path, b)
    (dest / ".git").mkdir(parents=True)
    (dest / "x.kicad_pcb").write_text(PCB6.replace("20240108", "20171130"))
    monkeypatch.setattr(hc, "git", lambda *a: None)
    monkeypatch.setattr(hc.subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "/*.*"})())
    assert "predates KiCad 6" in hc.fetch_board(tmp_path, b)
    assert b["excluded"]

    nightly = _board(id="b3", commit="9" * 40, pcb="x.kicad_pcb")
    dest = hc.checkout_dir(tmp_path, nightly)
    (dest / ".git").mkdir(parents=True)
    (dest / "x.kicad_pcb").write_text(PCB6.replace("20240108", "20260410"))
    assert "nightly" in hc.fetch_board(tmp_path, nightly)
    assert nightly["excluded"]

    new = _board(id="b2", commit="e" * 40, pcb="x.kicad_pcb")
    dest = hc.checkout_dir(tmp_path, new)
    (dest / ".git").mkdir(parents=True)
    (dest / "x.kicad_pcb").write_text(PCB6)
    assert hc.fetch_board(tmp_path, new) == ""
    assert "excluded" not in new and new["layers"] == 4


def test_table_lists_each_board_and_its_findings(tmp_path):
    ran = _board(commit="f" * 40, pcb="x.kicad_pcb", licence="MIT", layers=2,
                 outcome={"label": "errata", "source": "issue 3"},
                 pair={"with": "b2", "role": "before", "note": "Q1 fixed"})
    idle = _board(id="b2", commit="f" * 40, pcb="y.kicad_pcb", licence="MIT")
    broke = _board(id="b4", commit="f" * 40, pcb="z.kicad_pcb", licence="MIT")
    run4 = tmp_path / "runs" / "b4"
    run4.mkdir(parents=True)
    (run4 / "result.json").write_text(json.dumps({
        "verify_all": {"exit": 0}, "dfm_check": {"exit": 2, "stderr_tail": "",
                                                 "stdout_tail": '{"error": "no capability entry"}'},
        "verify_summary": {"status": "pass", "counts": {}, "checks": {}, "violations": []},
        "dfm_report": None}))
    gone = _board(id="b3", excluded="no licence")
    run = tmp_path / "runs" / "b1"
    run.mkdir(parents=True)
    (run / "result.json").write_text(json.dumps({
        "verify_all": {"exit": 1}, "dfm_check": {"exit": 1},
        "verify_summary": {"status": "violations",
                           "counts": {"by_severity": {"warning": 1}},
                           "checks": {"check_silk": {"status": "violations"}},
                           "violations": [{"severity": "warning", "source": "check_silk",
                                           "msg": "silk over pad", "refs": ["R1"],
                                           "pos": [1.0, 2.0]}]},
        "dfm_report": {"status": "violations",
                       "violations": [{"severity": "error", "check": "dfm_check",
                                       "kind": "annular_ring", "msg": "ring | thin"}]}}))
    out = tmp_path / "doc.md"
    hc.table({"boards": [ran, idle, gone, broke]}, tmp_path, out, per_board=10)
    doc = out.read_text()
    assert "| [b1](#b1) | power | 2 | errata | violations | 0 / 1 | violations | 1 / 1 |" in doc
    assert "| b2 | power |  |  | not run |" in doc
    assert "b3" not in doc
    assert "silk over pad | R1 @ (1.0, 2.0)" in doc
    assert "ring / thin" in doc
    assert "before of [b2](#b2)" in doc
    assert "dfm_check wrote no report: no capability entry" in doc
    assert "verify_all wrote no report" not in doc
    assert "| dfm_check / annular_ring | 1 |" in doc
    # the error sorts above the warning
    assert doc.index("annular_ring") < doc.index("silk over pad")


def test_manifest_rows_are_well_formed():
    data = yaml.safe_load(hc.MANIFEST.read_text())
    ids = [b["id"] for b in data["boards"]]
    assert len(ids) == len(set(ids))
    for b in data["boards"]:
        assert b["domain"] in hc.DOMAINS, b["id"]
        assert (b.get("outcome") or {}).get("label", "") in hc.OUTCOMES, b["id"]
        if b.get("outcome", {}).get("label"):
            assert b["outcome"].get("source"), b["id"]
        if b.get("excluded"):
            continue
        assert b["licence"] in hc.LICENCES and b["licence_source"], b["id"]
        assert len(b["commit"]) == 40 and b["pcb"].endswith(".kicad_pcb"), b["id"]
        if b.get("pair"):
            assert b["pair"]["with"] in ids and b["pair"]["role"] in ("before", "after"), b["id"]


def test_run_copies_past_a_dangling_symlink(tmp_path, monkeypatch):
    b = _board(commit="c" * 40, pcb="hw/x.kicad_pcb")
    src = hc.checkout_dir(tmp_path, b) / "hw"
    src.mkdir(parents=True)
    (src / "x.kicad_pcb").write_text(PCB6)
    (src / "x.kicad_dru").symlink_to(src / "missing.kicad_dru")
    seen = []
    monkeypatch.setattr(hc, "gate", lambda cmd, envv, timeout: seen.append(cmd) or {"exit": 0})
    hc.run_board(tmp_path, b, {}, 10)
    kicad = tmp_path / "runs" / "b1" / "kicad"
    assert (kicad / "x.kicad_pcb").exists()
    assert not (kicad / "x.kicad_dru").exists()
    assert seen


def test_fetch_keeps_a_spaced_path_when_a_second_board_widens_the_checkout(tmp_path, monkeypatch):
    repo = tmp_path / "src"
    for d in ("A B/x", "C D/y"):
        (repo / d).mkdir(parents=True)
        (repo / d / "b.kicad_pcb").write_text(PCB6)
    run = lambda *a: hc.subprocess.run(["git", "-C", str(repo), *a], check=True,
                                       capture_output=True)
    run("init", "-q")
    run("-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    run("config", "uploadpack.allowAnySHA1InWant", "true")
    sha = hc.subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(hc, "slug", lambda url: "o/r")
    cache = tmp_path / "cache"
    first = _board(id="b1", url=repo.as_uri(), commit=sha, pcb="A B/x/b.kicad_pcb")
    second = _board(id="b2", url=repo.as_uri(), commit=sha, pcb="C D/y/b.kicad_pcb")
    assert hc.fetch_board(cache, first) == ""
    assert hc.fetch_board(cache, second) == ""
    assert (hc.checkout_dir(cache, first) / first["pcb"]).exists()
    assert (hc.checkout_dir(cache, second) / second["pcb"]).exists()
