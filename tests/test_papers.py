"""The research-to-checks pipeline (scripts/papers.py, lib/paperslib.py):
offline, on recorded OpenAlex shapes, plus the real ledger's lint and the
generated docs being current."""
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import papers  # noqa: E402
import paperslib as pl  # noqa: E402

WORK = {
    "id": "https://openalex.org/W123",
    "doi": "https://doi.org/10.1000/ABC.1",
    "display_name": "A Study of the Via Stub",
    "publication_year": 2024,
    "type": "article",
    "cited_by_count": 7,
    "is_retracted": False,
    "primary_location": {"source": {"display_name": "IEEE TEMC", "type": "journal"}},
    "best_oa_location": {"is_oa": True, "pdf_url": "https://arxiv.org/pdf/2401.00001",
                         "landing_page_url": "https://arxiv.org/abs/2401.00001",
                         "version": "submittedVersion", "license": "cc-by",
                         "source": {"display_name": "arXiv"}},
    "locations": [
        {"is_oa": True, "pdf_url": "https://sci-hub.se/10.1000/abc.1"},
        {"is_oa": True, "pdf_url": None,
         "landing_page_url": "https://arxiv.org/abs/2401.00001"},
        {"is_oa": False, "landing_page_url": "https://ieeexplore.ieee.org/x"}],
    "abstract_inverted_index": {"Stubs": [0], "resonate": [1], "early.": [2]},
}


def test_shadow_libraries_are_refused():
    for u in ("https://sci-hub.se/x", "https://libgen.rs/a", "http://z-library.sk/b",
              "https://annas-archive.org/md5/x", "https://www.sci-hub.ru/y"):
        assert pl.shadow_host(u), u
    for u in ("https://arxiv.org/pdf/1", "https://www.ti.com/lit/an/x.pdf",
              "https://hal.science/hal-1/document"):
        assert pl.shadow_host(u) is None, u
    with pytest.raises(ValueError):
        pl.fetch_json("https://sci-hub.se/api")


def test_from_openalex_keeps_legit_copies_and_signals():
    p = pl.from_openalex(WORK)
    assert p["doi"] == "10.1000/abc.1"
    assert p["openalex"] == "W123"
    assert p["arxiv"] == "2401.00001"
    assert p["status"] == "candidate"
    assert p["access"]["state"] == "open"
    urls = [c["url"] for c in p["access"]["copies"]]
    assert urls[0] == "https://arxiv.org/pdf/2401.00001"
    assert not any("sci-hub" in u for u in urls)
    assert p["trust"] == {"retracted": False, "peer_reviewed": True,
                          "source_type": "journal", "cited_by": 7}
    assert p["abstract"] == "Stubs resonate early."


def test_no_open_copy_is_paywalled():
    w = {**WORK, "best_oa_location": None,
         "locations": [{"is_oa": False, "landing_page_url": "https://x.org"},
                       {"is_oa": True, "pdf_url": "https://libgen.is/z"}]}
    assert pl.from_openalex(w)["access"]["state"] == "paywalled"


def test_merge_dedups_by_doi_and_renames_clashes():
    ledger = {"papers": [{"id": "study-stub-2024", "doi": "10.1000/abc.1",
                          "status": "dropped", "why": "x"}]}
    again = pl.from_openalex(WORK)
    other = {**pl.from_openalex({**WORK, "doi": "10.1000/other", "id": "W9"})}
    added = pl.merge_candidates(ledger, [again, other, other], "via-si")
    assert [p["id"] for p in added] == ["study-stub-2024-2"]
    assert added[0]["topics"] == ["via-si"]
    assert len(ledger["papers"]) == 2


def test_due_topics():
    watch = {"topics": [{"id": "a", "every_days": 30, "last_scanned": "2026-10-01"},
                        {"id": "b", "every_days": 30, "last_scanned": "2026-10-20"},
                        {"id": "c"}]}
    assert [t["id"] for t in pl.due_topics(watch, on="2026-10-31")] == ["a", "c"]


def _ledger(claim, **paper):
    p = {"id": "p-2024", "title": "T", "doi": "10.1/x", "status": "kept",
         "why": "w", "topics": ["t"], "access": {"state": "open", "copies": []},
         "claims": [claim], **paper}
    return {"papers": [p]}, {"topics": [{"id": "t", "area": "a", "queries": ["q"]}]}


def test_lint_holds_the_promises(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        'def test_ok():\n    """p-2024/1"""\n\ndef test_other():\n    pass\n')
    base = {"id": "p-2024/1", "claim": "c", "where": "s.2"}
    ok = {**base, "verdict": "reproduced", "finding": "f",
          "evidence": "tests/test_x.py::test_ok", "guideline": "g"}
    assert pl.lint(*_ledger(ok), root=tmp_path) == []
    cases = {
        "name the claim": {**ok, "evidence": "tests/test_x.py::test_other"},
        "no such test": {**ok, "evidence": "tests/test_x.py::test_gone"},
        "doesn't exist": {**ok, "evidence": "tests/nope.py::test_ok"},
        "no finding": {k: v for k, v in ok.items() if k != "finding"},
        "hasn't reproduced": {**base, "verdict": "untested", "plan": "p",
                              "guideline": "g"},
        "no plan": {**base, "verdict": "untested"},
        "with no why": {**base, "verdict": "not_checkable"},
        "no page": {k: v for k, v in ok.items() if k != "where"},
        "<paper id>/<n>": {**ok, "id": "other/1"},
    }
    for want, claim in cases.items():
        probs = pl.lint(*_ledger(claim), root=tmp_path)
        assert any(want in p for p in probs), (want, probs)
    probs = pl.lint(*_ledger(ok, trust={"retracted": True}), root=tmp_path)
    assert any("retracted" in p for p in probs)
    probs = pl.lint(*_ledger(ok, access={"state": "open", "copies": [
        {"url": "https://libgen.rs/x"}]}), root=tmp_path)
    assert any("shadow library" in p for p in probs)
    probs = pl.lint(*_ledger(ok, access={"state": "paywalled"}), root=tmp_path)
    assert any("paywalled" in p for p in probs)
    led, watch = _ledger(ok)
    led["papers"][0].update(status="dropped", why="")
    assert any("dropped with no why" in p for p in pl.lint(led, watch, root=tmp_path))


def test_fetch_copy_takes_only_a_pdf(tmp_path):
    html = tmp_path / "wall.html"
    html.write_text("<html><title>Making sure you're not a bot!</title></html>")
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.5\n...")
    r = pl.fetch_copy(html.as_uri(), tmp_path / "out" / "a.pdf")
    assert not r["ok"] and "not a PDF" in r["blocked"]
    r = pl.fetch_copy(pdf.as_uri(), tmp_path / "out" / "b.pdf")
    assert r["ok"] and (tmp_path / "out" / "b.pdf").read_bytes().startswith(b"%PDF")
    assert not pl.fetch_copy("https://sci-hub.se/x", tmp_path / "c.pdf")["ok"]


def test_scan_adds_candidates_and_stamps_the_topic(tmp_path, monkeypatch, capsys):
    led, watch = tmp_path / "papers.yaml", tmp_path / "watch.yaml"
    led.write_text("# head\npapers: []\n")
    watch.write_text("# head\n" + yaml.safe_dump({"topics": [
        {"id": "via-si", "area": "si", "queries": ["via stub"], "every_days": 30}]}))
    monkeypatch.setattr(pl, "LEDGER", led)
    monkeypatch.setattr(pl, "WATCH", watch)
    monkeypatch.setattr(pl, "fetch_json", lambda url: {"results": [WORK]})
    assert papers.main(["scan", "--due", "--add"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["topics"][0]["added"] == ["study-stub-2024"]
    assert led.read_text().startswith("# head\n")
    assert yaml.safe_load(watch.read_text())["topics"][0]["last_scanned"] == pl.today()
    assert papers.main(["scan", "--due", "--add"]) == 0  # nothing due now
    assert "no topic due" in capsys.readouterr().out


def test_the_ledger_lints_clean():
    ledger, watch = pl.load_yaml(pl.LEDGER), pl.load_yaml(pl.WATCH)
    assert pl.lint(ledger, watch) == []


def test_research_docs_are_current():
    ledger = pl.load_yaml(pl.LEDGER)
    for name, fn in papers.DOC_PAGES.items():
        assert (pl.DOCS / name).read_text() == fn(ledger), \
            f"docs/research/{name} is stale: run `hwde papers docs`"


def test_paywalled_list_names_what_the_person_can_get():
    text = pl.paywalled_md(pl.load_yaml(pl.LEDGER))
    assert "doi.org/" in text and "shadow" in text
