"""Datasheet fetches follow LCSC's HTML redirect stub, offline (brief 261028-141933).

wmsc.lcsc.com answers a datasheet URL with 200 text/html and a 140-byte
`<script>window.top.location.href='https://datasheet.lcsc.com/...'</script>`;
the PDF is at that target. Part of `hwde test`.
"""
from __future__ import annotations

import json
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

import httpx

SCRIPTS = Path(__file__).resolve().parents[1] / ".claude/skills/hwde/scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import datasheet_extract  # noqa: E402
import researchlib  # noqa: E402

WMSC = ("https://wmsc.lcsc.com/wmsc/upload/file/pdf/v2/lcsc/"
        "1806151818_Hubei-KENTO-Elec-KT-0603G_C12624.pdf")
TARGET = ("https://datasheet.lcsc.com/datasheet/pdf/"
          "69e84a3abd84e5ab5856c47a2d0334ce.pdf?productCode=C12624")
STUB = f"<script>window.top.location.href='{TARGET}'</script>".encode()
PDF = b"%PDF-1.7\n" + b"x" * 2000


def site(stub_to: str = TARGET):
    def handle(req: httpx.Request) -> httpx.Response:
        if str(req.url) == WMSC:
            body = f"<script>window.top.location.href='{stub_to}'</script>"
            return httpx.Response(200, content=body.encode(),
                                  headers={"content-type": "text/html"})
        if str(req.url) == TARGET:
            return httpx.Response(200, content=PDF,
                                  headers={"content-type": "application/pdf"})
        return httpx.Response(404)
    return httpx.MockTransport(handle)


def test_stub_redirect():
    assert len(STUB) == 140, len(STUB)
    assert researchlib.stub_redirect(STUB) == TARGET
    assert researchlib.stub_redirect(
        b'<meta http-equiv="refresh" content="0; url=https://a.example/x.pdf">'
    ) == "https://a.example/x.pdf"
    assert researchlib.stub_redirect(
        b"<script>location.replace('/d/x.pdf')</script>") == "/d/x.pdf"
    assert researchlib.stub_redirect(PDF) is None
    assert researchlib.stub_redirect(b"<html>" + b" " * 5000 + STUB) is None
    assert researchlib.stub_redirect(
        b"<script>location.href='javascript:void(0)'</script>") is None


def test_http_transport_follows_stub():
    r = researchlib.http_transport(WMSC, _mock=site())
    assert r["body"] == PDF, r["body"][:60]
    assert r["hops"] == [WMSC, TARGET], r["hops"]
    assert r["final_url"] == TARGET


def fake(stub_to: str = TARGET):
    return lambda url: researchlib.http_transport(url, _mock=site(stub_to))


def test_fetch_workspace():
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "parts").mkdir()
        (ws / "parts" / "parts.json").write_text(json.dumps({"parts": [
            {"lcsc": "C12624", "mpn": "KT-0603G", "datasheet": WMSC},
            {"lcsc": "C1", "mpn": "X"}]}))
        args = Namespace(fetch=str(ws), lcsc=None)
        out, code = datasheet_extract.do_fetch(args, transport=fake())
        assert code == 1, out          # C1 has no datasheet URL
        got = {r["lcsc"]: r for r in out["datasheets"]}
        assert got["C12624"]["status"] == "fetched", got["C12624"]
        assert got["C1"]["status"] == "missing"
        assert (ws / "parts/datasheets/C12624.pdf").read_bytes() == PDF
        idx = json.loads((ws / "parts/datasheets/index.json").read_text())
        assert idx["C12624"]["final_url"] == TARGET

        args = Namespace(fetch=str(ws), lcsc="C12624")
        out, code = datasheet_extract.do_fetch(args, transport=fake())
        assert code == 0 and out["fetched"] == 1, out


def test_stub_off_the_allowlist_is_refused():
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "x.pdf"
        r = datasheet_extract.fetch_datasheet(
            WMSC, dest, transport=fake("https://evil.example/x.pdf"))
        assert r["status"] == "refused" and "evil.example" in r["error"], r
        assert not dest.exists()


def test_html_is_never_written():
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "x.pdf"
        r = datasheet_extract.fetch_datasheet(
            WMSC, dest, transport=lambda u: {
                "status": 200, "final_url": u, "hops": [u],
                "body": b"<html>no pdf here</html>"})
        assert r["status"] == "refused" and "not a PDF" in r["error"], r
        assert not dest.exists()

