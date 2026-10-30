"""parts_search --kind / --category, hermetic: a fake JLC endpoint stands in
for jlcpcb.com (issue #14: the keyword search matches part numbers, so a part
known by kind - 'RCA jack', 'Toslink', 'Type-C 48V' - found nothing)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import httpx
import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import partslib  # noqa: E402
import parts_search  # noqa: E402


def _row(code: str, mpn: str, volts: str, stock: int, basic: bool = False) -> dict:
    """One raw JLC list row, as the endpoint sends it."""
    return {
        "componentCode": code, "componentModelEn": mpn,
        "componentBrandEn": "ACME", "componentSpecificationEn": "SMD",
        "componentTypeEn": "USB Connectors", "stockCount": stock,
        "componentLibraryType": "base" if basic else "expand",
        "componentPrices": [{"startNumber": 1, "productPrice": 0.3}],
        "describe": f"16P {volts} 5A Female Type-C USB Connectors",
        "dataManualUrl": "https://example.com/ds.pdf",
        "attributes": [
            {"attribute_name_en": "Voltage Rating", "attribute_value_name": volts},
            {"attribute_name_en": "Current Rating-Power", "attribute_value_name": "250mA、5A"},
            {"attribute_name_en": "Connector Type", "attribute_value_name": "Type-C"},
            {"attribute_name_en": "Mounting Type", "attribute_value_name": "-"},
        ],
    }


ROWS = [_row("C1", "LOW-20V", "20V", 9000), _row("C2", "EPR-48V", "48V", 300),
        _row("C3", "HV-100V", "100V", 50, basic=True), _row("C4", "NONE", "-", 800),
        _row("C5", "EPR-50V", "50V", 700)]


class _Resp:
    def __init__(self, body: dict):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def fake_jlc(monkeypatch):
    calls: list[dict] = []

    def post(url, json=None, **_):
        calls.append(json)
        size, page = json["pageSize"], json["currentPage"]
        return _Resp({"code": 200, "data": {"componentPageInfo": {
            "total": len(ROWS), "list": ROWS[(page - 1) * size: page * size]}}})

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(parts_search, "_BROWSE_PAGE", 2)  # three pages for five rows
    return calls


def test_kinds_file_is_well_formed():
    kinds = yaml.safe_load(parts_search.KINDS_FILE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for k, spec in kinds.items():
        for n in [k, *(spec.get("aliases") or [])]:
            assert n not in names, f"kind name {n} used twice"
            names.add(n)
        assert spec.get("what") and spec.get("sources"), k
        for s in spec["sources"]:
            assert s.get("category"), k
            for key in ("mpn", "text", "not_text"):
                if s.get(key):
                    re.compile(s[key])
    for k in ("rca-jack", "banana-socket", "toslink-receiver", "usb-c-48v"):
        assert k in kinds


def test_kind_usb_c_48v_browses_and_filters(fake_jlc, tmp_path):
    out = tmp_path / "r.json"
    assert parts_search.main(["--kind", "type-c-48v", "--out", str(out)]) == 0
    r = json.loads(out.read_text())
    assert r["status"] == "pass" and r["kind"] == "usb-c-48v"
    # 48 V and up only; Basic first, then stock.
    assert [p["mpn"] for p in r["results"]] == ["HV-100V", "EPR-50V", "EPR-48V"]
    assert len(fake_jlc) == 3  # every page read
    c = fake_jlc[0]
    assert c["secondSortName"] == "USB Connectors" and c["stockFlag"] is True
    assert {"Connector Type": ["Type-C"]} in c["componentAttributeList"]


def test_unknown_kind_is_an_error(fake_jlc, tmp_path):
    out = tmp_path / "r.json"
    assert parts_search.main(["--kind", "nope", "--out", str(out)]) == 2
    assert "known:" in json.loads(out.read_text())["error"]


def test_attr_max_reads_the_largest_quantity():
    p = partslib.normalize(partslib._jlc_item(ROWS[0]))
    assert partslib.attr_max(p, "Current Rating-Power") == 5.0
    assert partslib.attr_max(p, "Mounting Type") is None
