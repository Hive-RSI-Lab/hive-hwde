"""parts_search --kind, offline: a fake JLC endpoint stands in for jlcpcb.com.

Checks that every kind in reference/part_kinds.yaml is well formed, and that
`--kind usb-c-48v` browses its category with the right server-side filters,
reads every page, keeps only receptacles rated 48 V or more, and ranks them.
Exit 0 on pass, 1 on a failure (check-all runs it).
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import httpx
import yaml

import parts_search
import partslib


def row(code: str, mpn: str, volts: str, stock: int, ctype: str = "Type-C",
        basic: bool = False) -> dict:
    """One raw JLC list row, as the endpoint sends it."""
    return {
        "componentCode": code, "componentModelEn": mpn,
        "componentBrandEn": "ACME", "componentSpecificationEn": "SMD",
        "componentTypeEn": "USB Connectors", "stockCount": stock,
        "componentLibraryType": "base" if basic else "expand",
        "componentPrices": [{"startNumber": 1, "productPrice": 0.3}],
        "describe": f"16P {volts} 5A Female {ctype} USB Connectors",
        "dataManualUrl": "https://example.com/ds.pdf",
        "attributes": [
            {"attribute_name_en": "Voltage Rating", "attribute_value_name": volts},
            {"attribute_name_en": "Current Rating-Power", "attribute_value_name": "250mA、5A"},
            {"attribute_name_en": "Connector Type", "attribute_value_name": ctype},
            {"attribute_name_en": "Mounting Type", "attribute_value_name": "-"},
        ],
    }


ROWS = [row("C1", "LOW-20V", "20V", 9000), row("C2", "EPR-48V", "48V", 300),
        row("C3", "HV-100V", "100V", 50, basic=True), row("C4", "NONE", "-", 800),
        row("C5", "EPR-50V", "50V", 700)]
calls: list[dict] = []


class Resp:
    def __init__(self, body: dict):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def fake_post(url, json=None, **_):
    calls.append(json)
    size, page = json["pageSize"], json["currentPage"]
    chunk = ROWS[(page - 1) * size: page * size]
    return Resp({"code": 200, "data": {"componentPageInfo": {
        "total": len(ROWS), "list": chunk}}})


def main() -> int:
    bad = []
    kinds = yaml.safe_load(parts_search.KINDS_FILE.read_text())
    names = set()
    for k, spec in kinds.items():
        for n in [k] + list(spec.get("aliases") or []):
            if n in names:
                bad.append(f"kind name '{n}' used twice")
            names.add(n)
        if not spec.get("what") or not spec.get("sources"):
            bad.append(f"{k}: needs what and sources")
        for s in spec.get("sources") or []:
            if not s.get("category"):
                bad.append(f"{k}: a source has no category")
            for key in ("mpn", "text", "not_text"):
                if s.get(key):
                    try:
                        re.compile(s[key])
                    except re.error as exc:
                        bad.append(f"{k}: bad {key} regex: {exc}")
    for k in ("rca-jack", "banana-socket", "toslink-receiver", "usb-c-48v"):
        if k not in kinds:
            bad.append(f"kind {k} is gone from part_kinds.yaml")

    httpx.post = fake_post
    parts_search._BROWSE_PAGE = 2  # three pages for five rows
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "r.json"
        rc = parts_search.main(["--kind", "type-c-48v", "--out", str(out)])
        r = json.loads(out.read_text())
    got = [p["mpn"] for p in r.get("results", [])]
    if rc != 0 or r.get("status") != "pass":
        bad.append(f"usb-c-48v: rc {rc}, {r.get('status')} {r.get('error', '')}")
    # 48 V and up only; Basic first, then stock.
    if got != ["HV-100V", "EPR-50V", "EPR-48V"]:
        bad.append(f"usb-c-48v: got {got}")
    if len(calls) != 3:
        bad.append(f"usb-c-48v: read {len(calls)} pages, want 3")
    c = calls[0] if calls else {}
    if c.get("secondSortName") != "USB Connectors" or c.get("stockFlag") is not True:
        bad.append(f"usb-c-48v: wrong browse payload {c}")
    if {"Connector Type": ["Type-C"]} not in (c.get("componentAttributeList") or []):
        bad.append(f"usb-c-48v: Type-C not filtered server-side {c}")
    first = partslib.normalize(partslib._jlc_item(ROWS[0]))
    if partslib.attr_max(first, "Current Rating-Power") != 5.0:
        bad.append("attr_max: '250mA、5A' should read as 5.0")

    for b in bad:
        print("FAIL", b)
    print("parts-kind: " + ("FAIL" if bad else f"pass (usb-c-48v -> {got})"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
