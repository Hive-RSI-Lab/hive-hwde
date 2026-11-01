"""parts_enrich.py: parts.json attributes from the LCSC listing (#467).
The JLC search is stubbed; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import parts_enrich  # noqa: E402
import partslib  # noqa: E402

LISTING = {
    "C185817": {"lcsc": "C185817", "model": "RLM25FEER005",
                "description": "2W 5mΩ Current Sense Resistor 2512",
                "datasheet": "https://x/ds.pdf",
                "attributes": [{"name": "Power(Watts)", "value": "2W"},
                               {"name": "Resistance", "value": "5mΩ"}]},
}


@pytest.fixture
def stub(monkeypatch, tmp_path):
    calls = []

    def live_search(code, page=1, page_size=25, part_type=None):
        calls.append(code)
        hit = LISTING.get(code)
        # JLC's fuzzy search returns neighbours too: only the exact code counts
        rows = [{"lcsc": code + "9", "attributes": []}]
        return (rows + [hit] if hit else rows), 2
    monkeypatch.setattr(partslib, "live_search", live_search)
    monkeypatch.setattr(partslib, "endpoint_reachable", lambda timeout=8: True)
    monkeypatch.setenv("HWDE_LCSC_CACHE", str(tmp_path / "cache"))
    return calls


def _ws(tmp_path, parts):
    (tmp_path / "parts").mkdir()
    pj = tmp_path / "parts" / "parts.json"
    pj.write_text(json.dumps({"parts": parts}, indent=1), encoding="utf-8")
    return pj


def test_fills_and_keeps_hand_values(tmp_path, stub):
    pj = _ws(tmp_path, [
        {"refdes": ["RS1"], "lcsc": "C185817", "mpn": "RLM25FEER005",
         "attributes": [{"name": "Power(Watts)", "value": "3W"}]},
        {"refdes": ["U9"], "lcsc": "C000001", "mpn": "GONE"},
        {"refdes": ["J1"], "mpn": "no code"}])
    out = tmp_path / "r.json"
    rc = parts_enrich.main(["--workspace", str(tmp_path), "--out", str(out)])
    assert rc == 1                      # C000001 unknown to JLC
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["filled"] == 1 and rep["written"] is True
    (v,) = rep["violations"]
    assert v["kind"] == "lcsc_unknown" and v["refs"] == ["U9"]
    rs1 = json.loads(pj.read_text(encoding="utf-8"))["parts"][0]
    assert rs1["attributes"] == [{"name": "Power(Watts)", "value": "3W"},
                                 {"name": "Resistance", "value": "5mΩ"}]
    assert rs1["description"].startswith("2W") and rs1["datasheet"]
    assert pj.read_text(encoding="utf-8").startswith('{\n "parts"')
    # second run: from the cache, nothing new
    stub.clear()
    rc = parts_enrich.main(["--workspace", str(tmp_path), "--out", str(out)])
    assert stub == [] and json.loads(out.read_text())["filled"] == 0


def test_dry_run_writes_nothing(tmp_path, stub):
    pj = _ws(tmp_path, [{"refdes": ["RS1"], "lcsc": "C185817"}])
    before = pj.read_text(encoding="utf-8")
    out = tmp_path / "r.json"
    assert parts_enrich.main(["--parts", str(tmp_path / "parts"),
                              "--dry-run", "--out", str(out)]) == 0
    assert pj.read_text(encoding="utf-8") == before
    assert json.loads(out.read_text())["rows"][0]["added"][:2] == \
        ["Power(Watts)", "Resistance"]


def test_unreachable_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(partslib, "live_search", lambda *a, **k: ([], 0))
    monkeypatch.setattr(partslib, "endpoint_reachable", lambda timeout=8: False)
    monkeypatch.setenv("HWDE_LCSC_CACHE", str(tmp_path / "cache"))
    _ws(tmp_path, [{"refdes": ["RS1"], "lcsc": "C185817"}])
    assert parts_enrich.main(["--workspace", str(tmp_path),
                              "--out", str(tmp_path / "r.json")]) == 2
    assert not (tmp_path / "cache" / "C185817.json").exists()
