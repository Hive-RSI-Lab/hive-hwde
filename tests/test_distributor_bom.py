"""DigiKey / Mouser BOMs beside the JLC fab files (distributor_bom.py, bom_cpl).

Hermetic: synthetic BOM-full rows, the distributors' injectable transport for
the keyed path, no network and no kicad-cli.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bom_cpl  # noqa: E402
import distributor_bom as db  # noqa: E402
import distributors  # noqa: E402

KEY_VARS = ("HWDE_DIGIKEY_CLIENT_ID", "HWDE_DIGIKEY_CLIENT_SECRET",
            "HWDE_DIGIKEY_SANDBOX", "HWDE_MOUSER_API_KEY")


def _row(comment, des, qty, mpn, lcsc, cls, fp="R0603"):
    return {"Comment": comment, "Designator": des, "Qty Per Board": str(qty),
            "Footprint": fp, "MPN": mpn, "LCSC": lcsc,
            "Assembly Class": cls, "Instructions": ""}


BOM_FULL = [
    _row("100R", "R1,R2", 2, "0603WAF1000T5E", "C22775", "smt_placed"),
    _row("LED", "D1", 1, "", "C2286", "smt_placed"),          # MPN from parts/
    _row("MYSTERY", "U9", 1, "", "C999", "smt_placed"),       # stays blank
    _row("100R", "R5", 1, "0603WAF1000T5E", "C22775", "hand_install"),
    _row("trim", "C2", 1, "CL10C220JB8NNNC", "C1653", "dnp"),
    _row("", "L1", 1, "", "", "board_feature"),
    _row("Screw", "", 4, "M3x6", "", "customer_supplied", fp=""),
]


@pytest.fixture
def no_keys(monkeypatch):
    for v in KEY_VARS:
        monkeypatch.delenv(v, raising=False)
        monkeypatch.delenv("AIEE_" + v[len("HWDE_"):], raising=False)


@pytest.fixture
def parts_dir(tmp_path):
    d = tmp_path / "ws" / "parts"
    d.mkdir(parents=True)
    (d / "C2286.json").write_text(json.dumps(
        {"mpn": "KT-0603R", "lcsc": "C2286", "package": "0603 LED"}))
    (d / "parts.json").write_text(json.dumps({"parts": [
        {"lcsc": "C22775", "mpn": "0603WAF1000T5E", "brand": "UNI-ROYAL"}]}))
    return d


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------- build_lines
def test_lines_merge_by_mpn_skip_unbought_and_fill_from_lcsc(parts_dir):
    lines = db.build_lines(BOM_FULL, db.lcsc_index(parts_dir), boards=3)
    by_mpn = {ln["mpn"]: ln for ln in lines}
    assert "CL10C220JB8NNNC" not in by_mpn            # dnp: nothing to buy
    assert all("L1" not in ln["refs"] for ln in lines)  # board_feature
    r = by_mpn["0603WAF1000T5E"]                      # one row, two classes
    assert r["refs"] == ["R1", "R2", "R5"] and r["qty_per_board"] == 3
    assert r["qty"] == 9 and r["classes"] == ["smt_placed", "hand_install"]
    assert r["manufacturer"] == "UNI-ROYAL"
    led = by_mpn["KT-0603R"]
    assert led["mpn_source"] == "lcsc" and led["refs"] == ["D1"]
    blank = [ln for ln in lines if not ln["mpn"]]
    assert [ln["refs"] for ln in blank] == [["U9"]]
    assert by_mpn["M3x6"]["qty"] == 12 and by_mpn["M3x6"]["refs"] == []


def test_price_at_breaks():
    br = [{"qty": 10, "unit_price": 0.1}, {"qty": 100, "unit_price": 0.05},
          {"qty": 1, "unit_price": 0.2}]
    assert db.price_at(br, 1) == (0.2, 1)
    assert db.price_at(br, 50) == (0.1, 50)
    assert db.price_at(br, 100) == (0.05, 100)
    assert db.price_at(br[:2], 4) == (0.1, 10)        # below MOQ -> buy MOQ
    assert db.price_at([], 4) == (None, 4)


# ------------------------------------------------------------- without keys
def test_no_keys_writes_by_mpn_files_and_warns(tmp_path, parts_dir, no_keys):
    def boom(*a, **k):
        raise AssertionError("no transport call without keys")
    out = tmp_path / "ws" / "fab"
    rep = db.write(BOM_FULL, out, "PCB-0099-A_demo", parts_dir=parts_dir,
                   transport=boom)
    dk = _read(out / "PCB-0099-A_demo_BOM_digikey.csv")
    mo = _read(out / "PCB-0099-A_demo_BOM_mouser.csv")
    assert list(dk[0]) == db.DIGIKEY_FIELDS and list(mo[0]) == db.MOUSER_FIELDS
    assert dk[0]["Manufacturer Part Number"] == "0603WAF1000T5E"
    assert dk[0]["Customer Reference"] == "R1,R2,R5"
    assert dk[0]["Quantity 1"] == "3" and dk[0]["Digi-Key Part Number"] == ""
    assert dk[0]["Notes"] == "smt_placed; hand_install"
    assert mo[1]["Notes"] == "smt_placed; MPN from LCSC data"
    assert mo[1]["Customer Part Number"] == "D1"
    assert len(dk) == 4 and len(mo) == 4
    assert not (out / "PCB-0099-A_demo_BOM_cost.json").exists()
    assert rep["cost"] is None and rep["priced"] == {"digikey": False,
                                                     "mouser": False}
    assert rep["missing_mpn"] == ["U9"]
    joined = "\n".join(rep["warnings"])
    assert "no MPN" in joined and "U9" in joined
    assert "digikey: prices not looked up" in joined
    assert "HWDE_MOUSER_API_KEY" in joined


# ------------------------------------------------------------- with keys
def _fake_transport(calls, mouser_errors=False):
    def fake(method, url, headers=None, data=None, json_body=None):
        calls.append((method, url, json_body))
        if url.endswith("/v1/oauth2/token"):
            return {"status": 200, "json": {"access_token": "tok"}, "text": ""}
        if "digikey" in url:
            mpn = json_body["Keywords"]
            return {"status": 200, "text": "", "json": {"Products": [
                {"ManufacturerProductNumber": mpn + "-ALT",
                 "ProductVariations": [{"DigiKeyProductNumber": "WRONG-ND"}]},
                {"ManufacturerProductNumber": mpn,
                 "Manufacturer": {"Name": "DK Maker"},
                 "QuantityAvailable": 5000,
                 "ProductVariations": [{
                     "DigiKeyProductNumber": f"{mpn}-ND",
                     "StandardPricing": [
                         {"BreakQuantity": 1, "UnitPrice": 0.10},
                         {"BreakQuantity": 10, "UnitPrice": 0.05}]}]}]}}
        mpn = json_body["SearchByPartRequest"]["mouserPartNumber"]
        if mouser_errors and mpn == "KT-0603R":
            return {"status": 200, "text": "", "json": {
                "Errors": [{"Message": "bad"}], "SearchResults": None}}
        return {"status": 200, "text": "", "json": {"Errors": [],
                "SearchResults": {"NumberOfResult": 1, "Parts": [{
                    "ManufacturerPartNumber": mpn, "Manufacturer": "MO Maker",
                    "MouserPartNumber": f"71-{mpn}",
                    "AvailabilityInStock": "2",
                    "PriceBreaks": [{"Quantity": 1, "Price": "$0.20",
                                     "Currency": "USD"}]}]}}}
    return fake


def test_keys_fill_pn_stock_price_and_cost(tmp_path, parts_dir, no_keys,
                                          monkeypatch):
    monkeypatch.setenv("HWDE_DIGIKEY_CLIENT_ID", "cid")
    monkeypatch.setenv("HWDE_DIGIKEY_CLIENT_SECRET", "sec")
    monkeypatch.setenv("HWDE_MOUSER_API_KEY", "k")
    calls = []
    out = tmp_path / "out"
    rep = db.write(BOM_FULL, out, "WS", parts_dir=parts_dir, boards=4,
                   transport=_fake_transport(calls, mouser_errors=True))
    dk = {r["Manufacturer Part Number"]: r
          for r in _read(out / "WS_BOM_digikey.csv")}
    mo = {r["Manufacturer Part Number"]: r
          for r in _read(out / "WS_BOM_mouser.csv")}
    r = dk["0603WAF1000T5E"]                  # 3/board x 4 = 12 -> 10 break
    assert r["Digi-Key Part Number"] == "0603WAF1000T5E-ND"   # exact MPN hit
    assert r["Quantity 1"] == "12" and r["Stock"] == "5000"
    assert r["Unit Price (USD)"] == "0.05" and r["Extended Price (USD)"] == "0.6"
    assert r["Manufacturer Name"] == "UNI-ROYAL"   # workspace data wins
    assert dk[""]["Digi-Key Part Number"] == ""    # no MPN: never queried
    assert mo["0603WAF1000T5E"]["Mouser Part Number"] == "71-0603WAF1000T5E"
    assert mo["KT-0603R"]["Mouser Part Number"] == ""   # business error row
    assert mo["KT-0603R"]["Manufacturer"] == ""
    assert not any(c[2] and "U9" in json.dumps(c[2]) for c in calls)
    assert sum(1 for c in calls if c[1].endswith("/oauth2/token")) == 1

    cost = json.loads((out / "WS_BOM_cost.json").read_text())
    assert cost["boards"] == 4 and cost["lines"] == 4
    # digikey: 12x0.05 + 4x0.10 + 16x0.05 = 1.8
    assert cost["digikey"]["total_usd"] == 1.8
    # mouser: 12x0.2 + 16x0.2 (LED errored) = 5.6
    assert cost["mouser"]["total_usd"] == 5.6
    assert cost["mouser"]["lines_unpriced"] == ["D1"]
    assert "0603WAF1000T5E" in cost["mouser"]["short_stock"]
    assert cost["cheaper"] == "digikey"
    assert rep["priced"] == {"digikey": True, "mouser": True}
    assert any("mouser: KT-0603R (D1) not priced" in w for w in rep["warnings"])


def test_one_provider_keyed_and_transport_failure_never_raise(
        tmp_path, parts_dir, no_keys, monkeypatch):
    monkeypatch.setenv("HWDE_MOUSER_API_KEY", "k")

    def down(*a, **k):
        raise distributors.DistributorError("transport failure: ConnectError")
    rep = db.write(BOM_FULL, tmp_path, "WS", parts_dir=parts_dir,
                   transport=down)
    assert rep["priced"] == {"digikey": False, "mouser": False}
    assert rep["cost"] is None
    assert any("mouser: prices not looked up" in w and "ConnectError" in w
               for w in rep["warnings"])

    calls = []
    rep = db.write(BOM_FULL, tmp_path, "WS", parts_dir=parts_dir,
                   transport=_fake_transport(calls))
    cost = json.loads((tmp_path / "WS_BOM_cost.json").read_text())
    assert cost["digikey"] == {"looked_up": False}
    assert cost["mouser"]["looked_up"] and cost["cheaper"] is None
    assert not any("digikey" in c[1] for c in calls)


# ------------------------------------------------------------- via bom_cpl
POS = """Ref,Val,Package,PosX,PosY,Rot,Side
R1,100R,R_0603,1,1,0,top
D1,LED,LED_0603,2,2,0,top
"""


def _workspace(tmp_path):
    ws = tmp_path / "PCB-0099-A_demo"
    (ws / "kicad").mkdir(parents=True)
    (ws / "parts").mkdir()
    (ws / "parts" / "C2286.json").write_text(json.dumps(
        {"mpn": "KT-0603R", "lcsc": "C2286"}))
    pj = ws / "parts" / "parts.json"
    pj.write_text(json.dumps({"parts": [
        {"refdes": ["R1"], "lcsc": "C22775", "mpn": "0603WAF1000T5E"},
        {"refdes": ["D1"], "lcsc": "C2286"}]}))
    pos = tmp_path / "pos.csv"
    pos.write_text(POS)
    return ws, pj, pos


def test_bom_cpl_run_writes_distributor_boms_without_lookup(tmp_path, no_keys,
                                                           monkeypatch):
    monkeypatch.setenv("HWDE_MOUSER_API_KEY", "k")   # keyed, but run() is off

    def boom(*a, **k):
        raise AssertionError("run() must not look prices up by default")
    ws, pj, pos = _workspace(tmp_path)
    rep = bom_cpl.run(ws / "kicad" / "demo.kicad_pcb", ws / "fab", pos=pos,
                      parts_json=pj, transport=boom)
    d = rep["distributor_boms"]
    assert d["digikey"].endswith("fab/PCB-0099-A_demo_BOM_digikey.csv")
    rows = _read(d["mouser"])
    assert [r["Manufacturer Part Number"] for r in rows] == \
        ["KT-0603R", "0603WAF1000T5E"]
    assert d["cost"] is None
    rep = bom_cpl.run(ws / "kicad" / "demo.kicad_pcb", tmp_path / "elsewhere",
                      pos=pos, parts_json=pj, ws_name="custom", boards=2)
    assert Path(rep["distributor_boms"]["mouser"]).name == \
        "custom_BOM_mouser.csv"
    assert _read(rep["distributor_boms"]["mouser"])[0]["Quantity"] == "2"


def test_bom_cpl_cli_says_on_stderr_that_prices_were_not_looked_up(tmp_path):
    ws, pj, pos = _workspace(tmp_path)
    env = {k: v for k, v in os.environ.items()
           if not any(k.endswith(v2[len("HWDE_"):]) for v2 in KEY_VARS)}
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / "bom_cpl.py"),
         "--pcb", str(ws / "kicad" / "demo.kicad_pcb"),
         "--out-dir", str(ws / "fab"), "--pos", str(pos),
         "--parts", str(pj), "--boards", "3"],
        capture_output=True, text=True, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "bom_cpl: digikey: prices not looked up" in res.stderr
    assert "HWDE_MOUSER_API_KEY" in res.stderr
    rep = json.loads(res.stdout)
    assert rep["distributor_boms"]["boards"] == 3
    assert (ws / "fab" / "PCB-0099-A_demo_BOM_digikey.csv").is_file()
    assert not (ws / "fab" / "PCB-0099-A_demo_BOM_cost.json").exists()


def test_standalone_cli_reads_bom_full_and_defaults_ws_name(tmp_path, no_keys):
    ws, pj, pos = _workspace(tmp_path)
    bom_cpl.run(ws / "kicad" / "demo.kicad_pcb", ws / "fab", pos=pos,
                parts_json=pj)
    out = tmp_path / "scratch"
    rc = db.main(["--bom-full", str(ws / "fab" / "BOM-full.csv"),
                  "--out-dir", str(out), "--out", str(tmp_path / "r.json")])
    rep = json.loads((tmp_path / "r.json").read_text())
    assert rc == 0 and rep["status"] == "pass"
    assert (out / "PCB-0099-A_demo_BOM_digikey.csv").is_file()
    assert db.main(["--bom-full", str(tmp_path / "missing.csv"),
                    "--out-dir", str(out), "--out",
                    str(tmp_path / "e.json")]) == 2
