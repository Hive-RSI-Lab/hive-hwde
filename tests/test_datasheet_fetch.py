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

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
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



def _pdf(pages: list[str]) -> bytes:
    """Minimal text PDF, one uncompressed content stream per page."""
    objs, kids = [], []
    for i, t in enumerate(pages):
        kids.append(f"{4 + 2 * i} 0 R")
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, t in enumerate(pages):
        c = f"BT /F1 12 Tf 72 700 Td ({t}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {5 + 2 * i} 0 R /Resources << /Font << /F1 3 0 R >> >> >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(c) + c + b"\nendstream")
    out, offs = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % n + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)


NOTICE = _pdf(["LCSC Datasheet Notice - Datasheet temporarily unavailable"])
PDF = _pdf([f"KT-0603G datasheet page {i} " + "x" * 300 for i in range(1, 4)])  # a whole one (#706)
REAL = _pdf([f"Page {i} absolute maximum ratings VCC 5.5 V " + "x" * 300
             for i in range(1, 9)])


def _serve(body: bytes):
    return lambda u: {"status": 200, "final_url": u, "hops": [u], "body": body}


def test_lcsc_notice_pdf_is_missing_not_fetched():
    with tempfile.TemporaryDirectory() as d:
        dest = Path(d) / "x.pdf"
        r = datasheet_extract.fetch_datasheet(WMSC, dest, transport=_serve(NOTICE))
        assert r["status"] == "missing" and r["stub"] is True, r
        assert not dest.exists()
        r = datasheet_extract.fetch_datasheet(WMSC, dest, transport=_serve(REAL))
        assert r["status"] == "fetched" and dest.read_bytes() == REAL, r


def test_notice_reworded_hits_size_backstop():
    reworded = _pdf(["Sorry, this datasheet is currently unavailable."])
    assert datasheet_extract.stub_notice(reworded)
    assert datasheet_extract.stub_notice(REAL) is None


def test_fetch_workspace_reports_stub_as_missing():
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "parts").mkdir()
        (ws / "parts" / "parts.json").write_text(json.dumps({"parts": [
            {"lcsc": "C194022", "mpn": "X", "datasheet": WMSC}]}))
        out, code = datasheet_extract.do_fetch(
            Namespace(fetch=str(ws), lcsc=None), transport=_serve(NOTICE))
        assert code == 1 and out["fetched"] == 0, out
        assert out["datasheets"][0]["status"] == "missing"
        assert not (ws / "parts/datasheets/C194022.pdf").exists()
        assert not (ws / "parts/datasheets/index.json").exists()


def test_pdf_mode_refuses_a_stub_on_disk():
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "C1.pdf"
        f.write_bytes(NOTICE)
        pdf, err = datasheet_extract._checked_pdf(str(f))
        assert pdf is None and "placeholder" in err["error"], err
        f.write_bytes(REAL)
        assert datasheet_extract._checked_pdf(str(f))[1] is None


# ---------------------------------------------- suspect datasheets (#706)

RIGHT = _pdf([f"TESTPART123 datasheet page {i} " + "x" * 300 for i in range(1, 5)])
WRONG = _pdf([f"OTHERCHIP999 datasheet page {i} " + "x" * 300 for i in range(1, 5)])
TRUNC = RIGHT[:len(RIGHT) * 2 // 3]
NOTEXT = _pdf([""] * 4)
MPN = "TESTPART123-QFN"     # the orderable code; the family is TESTPART123


def test_assess_right_part_is_ok_with_page_and_text_counts():
    a = datasheet_extract.assess_pdf(RIGHT, MPN)
    assert a["status"] == "ok" and a["reasons"] == [], a
    assert a["pages"] == 4 and len(a["chars_per_page"]) == 4
    assert all(c > 300 for c in a["chars_per_page"]) and a["text_chars"] > 1200


def test_assess_wrong_part_is_suspect():
    a = datasheet_extract.assess_pdf(WRONG, MPN)
    assert a["status"] == "suspect" and a["pages"] == 4, a
    assert any("TESTPART123" in r and "pages 1-2" in r for r in a["reasons"]), a


def test_assess_truncated_file_is_suspect():
    a = datasheet_extract.assess_pdf(TRUNC, MPN)
    assert a["status"] == "suspect", a
    assert any("truncated" in r for r in a["reasons"]), a


def test_assess_no_text_layer_is_suspect():
    a = datasheet_extract.assess_pdf(NOTEXT, MPN)
    assert a["status"] == "suspect" and a["text_chars"] == 0, a
    assert any("no text layer" in r for r in a["reasons"]), a


def test_assess_without_an_mpn_skips_only_the_name_check():
    assert datasheet_extract.assess_pdf(WRONG, None)["status"] == "ok"
    assert datasheet_extract.assess_pdf(NOTEXT, None)["status"] == "suspect"


def test_fetch_suspect_is_not_fetched_but_is_kept_and_indexed():
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        (ws / "parts").mkdir()
        (ws / "parts" / "parts.json").write_text(json.dumps({"parts": [
            {"lcsc": "C1", "mpn": MPN, "datasheet": WMSC},
            {"lcsc": "C2", "mpn": MPN, "datasheet": WMSC}]}))
        bodies = {"C1": WRONG, "C2": RIGHT}
        for lcsc, body in bodies.items():
            out, code = datasheet_extract.do_fetch(
                Namespace(fetch=str(ws), lcsc=lcsc), transport=_serve(body))
            r = out["datasheets"][0]
            assert r["status"] == ("suspect" if body is WRONG else "fetched"), r
            assert (ws / f"parts/datasheets/{lcsc}.pdf").is_file()
            assert code == (1 if body is WRONG else 0), out
        idx = json.loads((ws / "parts/datasheets/index.json").read_text())
        assert idx["C1"]["status"] == "suspect" and idx["C1"]["reasons"], idx
        assert idx["C1"]["pages"] == 4 and len(idx["C1"]["chars_per_page"]) == 4
        assert idx["C2"]["status"] == "ok" and idx["C2"]["reasons"] == [], idx
