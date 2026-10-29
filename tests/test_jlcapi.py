"""JLCPCB Open API integration tests (lib/jlcapi.py, order_track.py,
order_submit.py --api/--api-create).

Criteria -> tests:
  - signing recipe pinned by the OFFICIAL doc-sample vector (exact base64)
                                     -> test_sign_official_vector
  - nonce format (32 chars A-Za-z0-9) -> test_nonce_format
  - JOP Authorization header assembly -> test_auth_header_assembly
  - envelope normalization variants (success/successful, message/msg,
    TDP file-id-in-message flagged via data_from_message, trace id,
    non-JSON bodies)                 -> test_normalize_*
  - classifier table 401/403/1000/1002/429, code 1000 outranking ANY HTTP
    status, + remediation strings    -> test_classify_table,
                                        test_remediation_strings
  - VERIFIED-LIVE (2026-07-28) scope-pending 403 body pinned verbatim
                                     -> test_classify_live_scope_pending_body
  - multipart construction (boundary, file + fileName parts) and header-
    token escaping (quotes, CR/LF)   -> test_multipart_body,
                                        test_multipart_escapes_header_tokens
  - upload signs EMPTY body first; the 401 retry is FULL jar style (meta
    text part added AND that JSON signed, fresh nonce)
                                     -> test_upload_empty_then_meta_fallback,
                                        test_upload_single_attempt_when_ok
  - post_json signs the exact wire body -> test_post_json_signs_wire_body
  - extract_file_key accepts only bare-string data / fileKey|key|fileId,
    never message-promoted data      -> test_extract_file_key_tightened
  - JLC Balance endpoint is a documented stub -> test_get_balance_stub
  - probe verdict lines pinned EXACTLY for all classifications + exit map
                                     -> test_probe_verdicts
  - order_track: fresh fetch writes tracking.json atomically (no tmp
    residue), change detection incl. same-count wip content changes and
    str-vs-int status codes, unknown status "unknown(7)", cancelled ->
    exit 1, missing batch -> exit 2, corrupt non-dict tracking.json,
    --out write failure keeps the exit contract, wip steps, --batch
    override                        -> test_track_*
  - order_submit --api mocked end-to-end (upload->audit->calculate ->
    api_quote.json; create NEVER called), pcbParam per the hendley
    PcbOrderCraftData table (layer/width/length/qty/thickness + every
    Required=yes field, stencil* keys banned), stackup-derived copper
    (JLC2313_1.6_2oz -> "2"), copper-vs-notes mismatch refusal,
    qty-without-estimate-row honesty, scope_pending -> exit 0 (incl.
    post-upload 403 keeping the fileKey), bad_signature -> exit 2,
    missing creds -> exit 2          -> test_api_quote_*, test_api_*,
                                        test_build_pcb_param_*,
                                        test_derive_copper_oz
  - order_submit --api-create: created-latch (second create refuses, zero
    transport calls) that --out CANNOT bypass (canonical fab/order.json is
    the record of truth, written on every run; --out only adds a copy),
    latch also armed by a recorded WEB order_number, create_attempt record
    pre-armed ON DISK before the transport call (in_flight / ambiguous
    failed:unknown_error blocks every retry until a human clears it;
    clean refusals write no record; unambiguous rejects retry freely),
    PCB-only estimate scope for assembly-inclusive quote rows,
    gerber sha binding, fresh/future/unparseable fetched_at guards,
    --ship-json whitelist (injection refused), freight attestation (grand
    total = pcb + quoted freight; hand-edited method or drifted cost
    refuses), attested-qty vs pcbParam.qty cross-check, missing board
    dimensions refuse locally, matching grand-total confirm creates
    exactly once, re-run never clobbers the placed-order record (verdict
    "created" is sticky, fresh outcomes land in last_quote_verdict/
    last_create_verdict, failed re-quotes flag quote_stale), board-specific
    human_steps survive every rewrite so the copper guard stays armed
                                     -> test_api_create_*,
                                        test_rerun_preserves_created_order,
                                        test_board_note_survives_*,
                                        test_created_verdict_not_downgraded_*

All hermetic tests run zero live HTTP (injectable transport / mocked
session). The single @pytest.mark.net probe runs only when all three
HWDE_JLCPCB_* env vars are present.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import string
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".claude" / "skills" / "hwde" / "scripts"
PYTHON = sys.executable
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import fabhash  # noqa: E402
import jlcapi  # noqa: E402
import order_submit  # noqa: E402
import order_track  # noqa: E402

API_ENV = ("HWDE_JLCPCB_APPID", "HWDE_JLCPCB_KEY", "HWDE_JLCPCB_SECRET")

# The OFFICIAL doc-sample signing vector (contract section 1, reproduced
# locally by the research and again here - the auth recipe's regression pin).
VECTOR = {
    "secret": "z0BWlikshimuyiwBsH1i2qwnzMb3j3kA",
    "method": "POST",
    "path": "/order/v1/createOrder",
    "timestamp": "1625208260",
    "nonce": "IZHEJYNIHYZIE8S0LLC0VWTPJVRRTO50",
    "body": '{"goodsId":100,"quantity":52,"createdTime":"2024-03-21 10:03:20"}',
    "signature": "sygwKhKBkLwHVv0c7D+a/A7JTEJjGH/kLugFKh16918=",
}

# Required=yes PcbOrderCraftData fields the CALCULATE payload carries.
# Live-corrected 2026-07-29 against the real endpoint: goldThickness only
# when surfaceFinish is 2; insideCuprumThickness only when layer >= 4
# (2L rejects it, code 2129); isAddCustomerCode/markOnPcb/
# autoConfirmProductionFile omitted entirely (doc's calculate example omits
# them; live rejects the "Yes"+2 pairing, code 2708) - create-side options,
# decided at the first gated live create.
REQUIRED_PCB_PARAM_FIELDS = {
    "layer", "width", "length", "qty", "thickness", "pcbColor",
    "surfaceFinish", "copperWeight", "goldFinger",
    "materialDetails", "panelFlag", "panelByJLCPCB_X", "panelByJLCPCB_Y",
    "differentDesign", "flyingProbeTest", "castellatedHoles",
    "orderDetailsRemark", "cascadeStructure", "impedanceFlag",
    "plateType", "viaCovering", "needTechnics", "edgeRounding",
    "serviceConfigVos",
}
CREATE_SIDE_OMITTED = {"isAddCustomerCode", "markOnPcb",
                       "autoConfirmProductionFile"}


# ------------------------------------------------------------------ helpers

def ok_resp(data, message="SUCCESS"):
    return {"ok": True, "code": 200, "message": message, "data": data,
            "http_status": 200, "trace_id": "T-1"}


def err_resp(http_status, code=None, message="err"):
    return {"ok": False, "code": code, "message": message, "data": None,
            "http_status": http_status, "trace_id": "T-E"}


def auth_fields(headers: dict) -> dict:
    auth = headers["Authorization"]
    assert auth.startswith("JOP ")
    return dict(re.findall(r'(\w+)="([^"]*)"', auth))


class FakeTransport:
    """JlcSession transport hook: records calls, replays queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url,
                           "headers": dict(headers), "body": body})
        return self.responses.pop(0)


class FakeSession:
    """Endpoint-level mock for order_track / order_submit / probe tests."""

    def __init__(self, **responses):
        self.responses = responses
        self.calls = []

    def _hit(self, name, arg):
        self.calls.append((name, arg))
        return self.responses[name]

    def upload_gerber(self, p):
        return self._hit("upload_gerber", str(p))

    def audit(self, key, language=0):
        return self._hit("audit", key)

    def calculate(self, payload):
        return self._hit("calculate", payload)

    def create_order(self, payload):
        return self._hit("create_order", payload)

    def order_detail(self, batch):
        return self._hit("order_detail", batch)

    def wip(self, uuid):
        return self._hit("wip", uuid)

    def component_detail(self, codes):
        return self._hit("component_detail", list(codes))

    def names(self):
        return [n for n, _ in self.calls]


# ================================================================== signing

def test_sign_official_vector():
    got = jlcapi.sign(VECTOR["method"], VECTOR["path"], VECTOR["timestamp"],
                      VECTOR["nonce"], VECTOR["body"], VECTOR["secret"])
    assert got == VECTOR["signature"]


def test_sign_trailing_newline_is_load_bearing():
    """Dropping the final \\n must change the signature (regression guard for
    the exact 5-line recipe)."""
    import base64
    import hashlib
    import hmac
    sts_no_tail = "\n".join([VECTOR["method"], VECTOR["path"],
                             VECTOR["timestamp"], VECTOR["nonce"],
                             VECTOR["body"]])
    wrong = base64.b64encode(hmac.new(VECTOR["secret"].encode(),
                                      sts_no_tail.encode(),
                                      hashlib.sha256).digest()).decode()
    assert wrong != VECTOR["signature"]


def test_nonce_format():
    seen = {jlcapi.make_nonce() for _ in range(20)}
    assert len(seen) == 20                      # no dupes in 20 draws
    alphabet = set(string.ascii_letters + string.digits)
    for n in seen:
        assert len(n) == 32 and set(n) <= alphabet


def test_auth_header_assembly():
    s = jlcapi.JlcSession("APP1", "AK1", VECTOR["secret"])
    hdr = s.auth_header("POST", VECTOR["path"], VECTOR["body"],
                        timestamp=VECTOR["timestamp"], nonce=VECTOR["nonce"])
    fields = dict(re.findall(r'(\w+)="([^"]*)"', hdr))
    assert hdr.startswith("JOP appid=")
    assert fields == {"appid": "APP1", "accesskey": "AK1",
                      "timestamp": VECTOR["timestamp"],
                      "nonce": VECTOR["nonce"],
                      "signature": VECTOR["signature"]}


# ============================================================= normalization

def test_normalize_success_variant():
    resp = jlcapi.normalize_response(
        200, {"J-Trace-ID": "abc"},
        b'{"code":200,"success":true,"message":"SUCCESS","data":{"x":1}}')
    assert resp == {"ok": True, "code": 200, "message": "SUCCESS",
                    "data": {"x": 1}, "http_status": 200, "trace_id": "abc",
                    "data_from_message": False}


def test_normalize_successful_msg_variant():
    resp = jlcapi.normalize_response(
        200, {"j-trace-id": "lc"},
        b'{"code":200,"successful":true,"msg":"hello","data":[1,2]}')
    assert resp["ok"] and resp["message"] == "hello" and resp["data"] == [1, 2]
    assert resp["trace_id"] == "lc"             # header case-insensitive


def test_normalize_tdp_file_id_in_message():
    resp = jlcapi.normalize_response(
        200, {}, b'{"code":200,"successful":true,"message":"16556_file.zip"}')
    assert resp["ok"] and resp["data"] == "16556_file.zip"
    assert resp["data_from_message"] is True    # promotion is flagged
    # a plain success word must NOT be promoted into data
    resp2 = jlcapi.normalize_response(
        200, {}, b'{"code":200,"success":true,"message":"SUCCESS"}')
    assert resp2["ok"] and resp2["data"] is None
    assert resp2["data_from_message"] is False


def test_normalize_business_error_not_ok():
    resp = jlcapi.normalize_response(
        200, {}, b'{"code":1000,"message":"Forbidden IP"}')
    assert not resp["ok"] and resp["code"] == 1000


def test_normalize_non_json_body():
    resp = jlcapi.normalize_response(500, {}, b"<html>oops</html>")
    assert not resp["ok"] and "<html>" in resp["message"]
    assert resp["http_status"] == 500


# ================================================================ classifier

@pytest.mark.parametrize("resp,want", [
    (err_resp(401), "bad_signature"),
    (err_resp(403), "scope_pending"),
    (err_resp(200, code=1000), "ip_blocked"),
    (err_resp(403, code=1000), "ip_blocked"),   # 1000 wins at ANY HTTP status
    (err_resp(429, code=1000), "ip_blocked"),
    (err_resp(200, code=1002), "rate_limited"),
    (err_resp(429), "rate_limited"),
    (ok_resp({"x": 1}), "ok"),
    (err_resp(200, code=1003), "error"),
    (err_resp(500, code=500), "error"),
    # JLC's catch-all on a live create: HTTP 200 + business code 2
    (err_resp(200, code=2, message="unknown_error"), "unknown_error"),
    (err_resp(200, code="2", message="unknown_error"), "unknown_error"),
])
def test_classify_table(resp, want):
    assert jlcapi.classify(resp) == want


def test_unknown_error_has_remediation():
    """The live 4-layer create returned {code 2, unknown_error} with NO
    classification and NO remediation - the worst state for a money call
    (LEARNINGS 2026-07-30 [jlcapi][order_submit][SPEND])."""
    remedy = jlcapi.REMEDIATION["unknown_error"]
    assert "DO NOT RETRY" in remedy
    assert "portal" in remedy and "4+ layers" in remedy


def test_classify_live_scope_pending_body():
    """The VERBATIM 403 body observed live (2026-07-28) with every service
    permission in "Reviewing": code mirrors the HTTP status and the flag key
    is `success`. A bodyless 403 must land in the same bucket (the classifier
    keys on the HTTP status alone)."""
    live = (b'{"code":403,"success":false,'
            b'"message":"API insufficient permissions, access denied"}')
    resp = jlcapi.normalize_response(403, {}, live)
    assert resp["ok"] is False and resp["code"] == 403
    assert resp["message"] == "API insufficient permissions, access denied"
    assert jlcapi.classify(resp) == "scope_pending"
    bodyless = jlcapi.normalize_response(403, {}, b"")
    assert jlcapi.classify(bodyless) == "scope_pending"


def test_remediation_strings():
    assert ("Permission Setting"
            in jlcapi.REMEDIATION["scope_pending"])
    assert "IP Whitelisting" in jlcapi.REMEDIATION["ip_blocked"]
    assert "bad_signature" in jlcapi.REMEDIATION
    assert "rate_limited" in jlcapi.REMEDIATION


# ================================================================= multipart

def test_multipart_body():
    body, ctype = jlcapi.multipart_body("b.zip", b"PK\x03\x04DATA",
                                        {"fileName": "b.zip"},
                                        boundary="XBOUND")
    assert ctype == "multipart/form-data; boundary=XBOUND"
    assert b'Content-Disposition: form-data; name="file"; filename="b.zip"' \
        in body
    assert b"Content-Type: application/octet-stream" in body
    assert b'Content-Disposition: form-data; name="fileName"' in body
    assert b"PK\x03\x04DATA" in body
    assert body.endswith(b"--XBOUND--\r\n")


def test_multipart_escapes_header_tokens():
    """Double quotes escaped, CR/LF stripped - a hostile filename must not
    break out of the Content-Disposition line."""
    body, _ = jlcapi.multipart_body('a"b\r\n.zip', b"X",
                                    {'evil"name': "v"}, boundary="B1")
    assert b'filename="a\\"b.zip"' in body       # quote escaped, CRLF gone
    assert b'name="evil\\"name"' in body
    assert b'a"b\r\n.zip' not in body            # raw injection absent


def test_upload_empty_then_meta_fallback(tmp_path):
    """Attempt 1 = hendley style: parts file+fileName, EMPTY signed body, no
    meta part. The 401 retry = FULL jar style: a `meta` text part is ADDED to
    the multipart and that same JSON string is signed, under a fresh nonce.
    Signatures are recomputed from the captured JOP headers via sign()."""
    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"PK\x03\x04data")
    transport = FakeTransport([
        (401, {}, b'{"code":401,"message":"signature verification failed"}'),
        (200, {"J-Trace-ID": "t2"},
         b'{"code":200,"success":true,"message":"SUCCESS","data":"FKEY9"}'),
    ])
    s = jlcapi.JlcSession("app", "AK", "SECRET7", transport=transport)
    resp = s.upload_gerber(zip_path)
    assert resp["ok"] and resp["data"] == "FKEY9"
    assert len(transport.calls) == 2
    first, second = transport.calls
    meta = jlcapi.compact_json({"fileName": "b.zip"})

    # attempt 1: no meta part, signature over the EMPTY body line
    assert b'name="meta"' not in first["body"]
    f1 = auth_fields(first["headers"])
    assert f1["signature"] == jlcapi.sign(
        "POST", jlcapi.PATHS["upload_gerber"],
        f1["timestamp"], f1["nonce"], "", "SECRET7")

    # attempt 2: meta part present AND the meta JSON is what gets signed
    assert b'name="meta"' in second["body"]
    assert meta.encode("utf-8") in second["body"]
    f2 = auth_fields(second["headers"])
    assert f2["signature"] == jlcapi.sign(
        "POST", jlcapi.PATHS["upload_gerber"],
        f2["timestamp"], f2["nonce"], meta, "SECRET7")
    assert f2["nonce"] != f1["nonce"]            # fresh nonce on the retry
    assert int(f2["timestamp"]) >= int(f1["timestamp"])

    for call in (first, second):
        assert call["url"].endswith("/overseas/openapi/pcb/uploadGerber")
        assert call["headers"]["Content-Type"].startswith(
            "multipart/form-data; boundary=")
        assert b'name="fileName"' in call["body"]


def test_upload_single_attempt_when_ok(tmp_path):
    zip_path = tmp_path / "b.zip"
    zip_path.write_bytes(b"PK\x03\x04")
    transport = FakeTransport([
        (200, {}, b'{"code":200,"success":true,"data":"FK1"}')])
    s = jlcapi.JlcSession("app", "AK", "S", transport=transport)
    resp = s.upload_gerber(zip_path)
    assert resp["ok"] and len(transport.calls) == 1
    assert b'name="meta"' not in transport.calls[0]["body"]
    assert jlcapi.extract_file_key(resp) == "FK1"


def test_post_json_signs_wire_body():
    transport = FakeTransport([
        (200, {}, b'{"code":200,"success":true,"data":{}}')])
    s = jlcapi.JlcSession("app", "AK", "SEC", transport=transport)
    s.audit("KEY123", language=0)
    call = transport.calls[0]
    wire = call["body"].decode("utf-8")
    assert wire == '{"key":"KEY123","language":0}'   # compact camelCase
    f = auth_fields(call["headers"])
    assert f["signature"] == jlcapi.sign(
        "POST", jlcapi.PATHS["audit"], f["timestamp"], f["nonce"], wire, "SEC")
    assert call["headers"]["Content-Type"] == "application/json"


def test_extract_file_key_tightened():
    assert jlcapi.extract_file_key(ok_resp("FK")) == "FK"
    assert jlcapi.extract_file_key(ok_resp({"fileKey": "FK2"})) == "FK2"
    assert jlcapi.extract_file_key(ok_resp({"key": "FK3"})) == "FK3"
    assert jlcapi.extract_file_key(ok_resp({"fileId": "FK4"})) == "FK4"
    # arbitrary dict values are NOT ids
    assert jlcapi.extract_file_key(ok_resp({"note": "uploaded"})) is None
    # a message-promoted data (TDP style) is NOT a pcb fileKey
    promoted = jlcapi.normalize_response(
        200, {}, b'{"code":200,"success":true,"message":"prose result"}')
    assert promoted["data_from_message"] is True
    assert jlcapi.extract_file_key(promoted) is None


def test_get_balance_stub():
    s = jlcapi.JlcSession("a", "k", "s", transport=FakeTransport([]))
    with pytest.raises(jlcapi.JlcApiError,
                       match="JLC Balance endpoint path not yet public"):
        s.get_balance()


# ===================================================================== probe

@pytest.mark.parametrize("resp,verdict,exit_code", [
    (ok_resp([{"componentCode": "C2040"}]), "live", 0),
    (err_resp(403), "SIGNING VERIFIED - scope approval pending", 0),
    (err_resp(401), "signing broken", 1),
    (err_resp(200, code=1000),
     "auth reached the API but this host's IP is not whitelisted", 1),
    (err_resp(200, code=1002), "rate limited - inconclusive, retry later", 1),
])
def test_probe_verdicts(resp, verdict, exit_code):
    fake = FakeSession(component_detail=resp)
    payload, code = jlcapi.probe(session=fake)
    assert code == exit_code
    assert payload["verdict"] == verdict         # exact line, every row
    assert fake.names() == ["component_detail"]
    assert payload["probe_endpoint"].endswith("getComponentDetailByCode")


# =============================================================== order_track

def detail_data(status, uuid=None, express=None):
    pcb = {"orderStatus": status}
    if uuid:
        pcb["orderUUID"] = uuid
    d = {"orderItem": [{"orderType": 1, "pcbItem": pcb}], "totalMoney": 12.5}
    if express:
        d["expressNo"] = express
    return d


def make_ws(tmp_path, batch="B123"):
    ws = tmp_path / "brd"
    fab = ws / "fab"
    fab.mkdir(parents=True, exist_ok=True)
    (fab / "order.json").write_text(json.dumps(
        {"api": {"order": {"batchNum": batch, "orderId": 7}}}),
        encoding="utf-8")
    return ws


def test_track_fresh_fetch_writes_tracking(tmp_path):
    ws = make_ws(tmp_path)
    fake = FakeSession(order_detail=ok_resp(detail_data(1)))
    payload = order_track.run(ws, session=fake)
    assert payload["status"] == "pass"
    assert payload["batch_num"] == "B123"
    assert payload["status_code"] == 1
    assert payload["status_label"] == "Pending Review"
    assert payload["tracking_number"] is None       # null-tolerant
    assert payload["changed"] is True
    assert "initial" in payload["change_summary"]
    saved = json.loads((ws / "fab" / "tracking.json")
                       .read_text(encoding="utf-8"))
    assert saved["status_code"] == 1
    # atomic write leaves no tmp residue beside the real file
    assert not (ws / "fab" / "tracking.json.tmp").exists()
    assert fake.calls == [("order_detail", "B123")]


def test_track_change_detected_and_summary(tmp_path):
    ws = make_ws(tmp_path)
    order_track.run(ws, session=FakeSession(order_detail=ok_resp(
        detail_data(1))))
    p2 = order_track.run(ws, session=FakeSession(order_detail=ok_resp(
        detail_data(5, express="SF001"))))
    assert p2["changed"] is True
    assert "Pending Review" in p2["change_summary"]
    assert "Shipped" in p2["change_summary"]
    assert p2["tracking_number"] == "SF001"
    p3 = order_track.run(ws, session=FakeSession(order_detail=ok_resp(
        detail_data(5, express="SF001"))))
    assert p3["changed"] is False
    assert "no change" in p3["change_summary"]


def test_track_status_code_str_vs_int_no_change(tmp_path):
    """Status codes are normalized to str before compare: "5" == 5."""
    ws = make_ws(tmp_path)
    order_track.run(ws, session=FakeSession(order_detail=ok_resp(
        detail_data("5"))))
    p2 = order_track.run(ws, session=FakeSession(order_detail=ok_resp(
        detail_data(5))))
    assert p2["changed"] is False
    assert "no change" in p2["change_summary"]


def test_track_wip_content_change_same_count(tmp_path):
    """wip diffed by CONTENT, not count: a renamed/re-dated step with the
    same list length must still notify."""
    ws = make_ws(tmp_path)
    order_track.run(ws, session=FakeSession(
        order_detail=ok_resp(detail_data(4, uuid="U1")),
        wip=ok_resp([{"technicsProcessName": "Drilling",
                      "beginTime": "t1"}])))
    p2 = order_track.run(ws, session=FakeSession(
        order_detail=ok_resp(detail_data(4, uuid="U1")),
        wip=ok_resp([{"technicsProcessName": "Plating",
                      "beginTime": "t2"}])))
    assert p2["changed"] is True
    assert "production steps updated" in p2["change_summary"]


def test_track_corrupt_non_dict_prev(tmp_path):
    """A tracking.json that parses but is not a dict = corrupt -> fresh
    diff, same as a JSONDecodeError."""
    ws = make_ws(tmp_path)
    (ws / "fab" / "tracking.json").write_text("[1, 2]", encoding="utf-8")
    payload = order_track.run(ws, session=FakeSession(
        order_detail=ok_resp(detail_data(1))))
    assert payload["changed"] is True
    assert "initial" in payload["change_summary"]


def test_track_unknown_status_code(tmp_path):
    ws = make_ws(tmp_path)
    payload = order_track.run(ws, session=FakeSession(
        order_detail=ok_resp(detail_data(7))))
    assert payload["status_label"] == "unknown(7)"   # enum is OPEN
    assert payload["status"] == "pass"


def test_track_cancelled_exits_1(tmp_path, monkeypatch, capsys):
    ws = make_ws(tmp_path)
    fake = FakeSession(order_detail=ok_resp(detail_data(0)))
    monkeypatch.setattr(order_track, "_make_session", lambda: fake)
    out = tmp_path / "t.json"
    code = order_track.main(["--workspace", str(ws), "--out", str(out)])
    assert code == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "violations"
    assert payload["status_label"] == "Cancelled"


def test_track_missing_batch_exits_2(tmp_path, monkeypatch, capsys):
    ws = tmp_path / "brd"
    (ws / "fab").mkdir(parents=True)
    (ws / "fab" / "order.json").write_text(json.dumps(
        {"api": {"attempted": False}, "order_number": None}),
        encoding="utf-8")
    monkeypatch.setattr(order_track, "_make_session",
                        lambda: pytest.fail("session built without a batch"))
    code = order_track.main(["--workspace", str(ws)])
    assert code == 2
    err = json.loads(capsys.readouterr().out)
    assert err["status"] == "error"
    assert "no API order recorded" in err["error"]


def test_track_out_write_failure_keeps_contract(tmp_path, monkeypatch,
                                                capsys):
    """--out failure must not traceback: payload goes to stdout, exit 2,
    with an error field naming the unwritable path."""
    ws = make_ws(tmp_path)
    fake = FakeSession(order_detail=ok_resp(detail_data(1)))
    monkeypatch.setattr(order_track, "_make_session", lambda: fake)
    out_dir = tmp_path / "outdir"
    out_dir.mkdir()                              # a directory is unwritable
    code = order_track.main(["--workspace", str(ws), "--out", str(out_dir)])
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "pass"           # the fetch itself succeeded
    assert str(out_dir) in payload["out_write_error"]


def test_track_wip_steps_and_uuid(tmp_path):
    ws = make_ws(tmp_path)
    fake = FakeSession(
        order_detail=ok_resp(detail_data(4, uuid="UU-9")),
        wip=ok_resp([{"technicsProcessName": "Drilling",
                      "beginTime": "2026-07-28 10:00:00"}]))
    payload = order_track.run(ws, session=fake)
    assert ("wip", "UU-9") in fake.calls
    assert payload["wip_steps"] == [
        {"step": "Drilling", "begin_time": "2026-07-28 10:00:00"}]
    assert payload["order_uuid"] == "UU-9"


def test_track_batch_flag_overrides(tmp_path):
    ws = make_ws(tmp_path, batch="FROMFILE")
    fake = FakeSession(order_detail=ok_resp(detail_data(2)))
    payload = order_track.run(ws, batch="OVERRIDE", session=fake)
    assert fake.calls == [("order_detail", "OVERRIDE")]
    assert payload["batch_num"] == "OVERRIDE"


# ============================================ order_submit --api (mocked)

@pytest.fixture
def api_env(monkeypatch):
    monkeypatch.setenv("HWDE_JLCPCB_APPID", "APP")
    monkeypatch.setenv("HWDE_JLCPCB_KEY", "KEY")
    monkeypatch.setenv("HWDE_JLCPCB_SECRET", "SECRET")


def make_fab(tmp_path, stackup="JLC2313_1.6"):
    fab = tmp_path / "fab"
    fab.mkdir()
    (fab / "b1_gerbers.zip").write_bytes(b"PK\x03\x04zip")
    (fab / "BOM.csv").write_text("Comment\n", encoding="utf-8")
    (fab / "CPL.csv").write_text("Designator\n", encoding="utf-8")
    # a real workspace always carries the stackup decision; copper weight is
    # derived from it and REFUSES rather than defaulting (T1)
    if stackup:
        arch = tmp_path / "architecture"
        arch.mkdir(exist_ok=True)
        (arch / "stackup.md").write_text(
            f"## Chosen: `{stackup}`\n", encoding="utf-8")
    quote = {"spec": {"layers": 2, "width_mm": 20.0, "height_mm": 25.0,
                      "thickness_mm": 1.6, "assembly": False},
             "estimated": True,
             "matrix": [{"qty": 5, "surface_finish": "HASL",
                         "solder_mask_color": "green",
                         "total": 4.0, "unit_cost": 0.8}]}
    qj = tmp_path / "quote.json"
    qj.write_text(json.dumps(quote), encoding="utf-8")
    pcb = tmp_path / "b1.kicad_pcb"
    pcb.write_text("(kicad_pcb)", encoding="utf-8")
    return pcb, fab, qj


def submit_argv(pcb, fab, qj, *extra, qty="5"):
    return ["--pcb", str(pcb), "--fab-dir", str(fab), "--quote", str(qj),
            "--qty", qty, *extra]


def quote_session(price=12.5, ship=None):
    calc = {"priceWithoutFreight": price,
            "achieveDateList": ["2026-08-01"],
            "pcbCostInfo": {"totalFee": price}}
    if ship is not None:
        calc["shipList"] = ship
    return FakeSession(
        upload_gerber=ok_resp("FKEY1"),
        audit=ok_resp({"minLineWidth": 0.2, "smallestHole": 0.3}),
        calculate=ok_resp(calc))


def test_api_quote_end_to_end(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = make_fab(tmp_path)
    fake = quote_session(price=12.5,
                         ship=[{"options": "DHL", "cost": 20.0}])
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api"))
    assert code == 0
    # quote-only: upload -> audit -> calculate, create NEVER called
    assert fake.names() == ["upload_gerber", "audit", "calculate"]
    assert "create_order" not in fake.names()
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["real_price"] == 12.5 and aq["file_key"] == "FKEY1"
    assert aq["board"] == "b1" and aq["qty"] == 5
    assert aq["estimate"] == 4.0
    assert aq["estimate_scope"] == "total"     # row carries no assembly
    assert aq["ship_list"][0]["options"] == "DHL"
    assert aq["shipping_method"] == "DHL"        # first shipList option
    assert aq["shipping_cost"] == 20.0
    assert aq["grand_total"] == 32.5             # pcb 12.5 + freight 20.0
    assert aq["gerber_sha256"] == order_submit.sha256(fab / "b1_gerbers.zip")
    assert aq["audit"]["data"]["minLineWidth"] == 0.2
    req = aq["calculate_request"]
    assert req["achieveDate"] == 120             # doc-example lead time
    param = req["pcbParam"]
    # hendley PcbOrderCraftData key names - the stencil* guess is BANNED
    assert param["layer"] == 2 and param["qty"] == 5
    assert param["width"] == 20.0 and param["length"] == 25.0
    assert param["thickness"] == 1.6
    assert param["copperWeight"] == "1" and param["surfaceFinish"] == 1
    assert not any(k.startswith("stencil") for k in param)
    assert REQUIRED_PCB_PARAM_FIELDS <= set(param)
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "ok"
    assert order["api"]["quote_real"] == 12.5
    assert order["api"]["file_key"] == "FKEY1"
    assert any("REAL grand total 32.5" in s for s in order["human_steps"])


def test_api_quote_derives_copper_from_stackup(tmp_path, monkeypatch,
                                               api_env, capsys):
    """pd-trigger-style workspace: architecture/stackup.md `## Chosen:`
    carries the oz marker -> copperWeight "2" goes out."""
    pcb, fab, qj = make_fab(tmp_path)
    arch = tmp_path / "architecture"
    arch.mkdir(exist_ok=True)
    (arch / "stackup.md").write_text(
        "# Stackup\n\n## Chosen: `JLC2313_1.6_2oz` (2-layer, 1.6 mm, "
        "**2 oz** outer copper, HASL)\n", encoding="utf-8")
    fake = quote_session(price=9.9)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api"))
    assert code == 0
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["calculate_request"]["pcbParam"]["copperWeight"] == "2"
    assert aq["copper_weight_oz"] == 2.0
    assert "stackup.md" in aq["copper_weight_source"]


def test_api_quote_copper_mismatch_refuses(tmp_path, monkeypatch, api_env,
                                           capsys):
    """The pd-trigger board-killer: order notes demand 2 oz, derivation says
    1 oz (no stackup.md) -> refuse BEFORE any network traffic."""
    pcb, fab, qj = make_fab(tmp_path)
    (fab / "order.json").write_text(json.dumps({
        "human_steps": ["BOARD-SPECIFIC: 2oz copper MUST be selected at JLC "
                        "order time (NOT default 1oz)"],
        "api": {}}), encoding="utf-8")
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api"))
    assert code == 2
    assert fake.calls == []                      # zero transport calls
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "refused"
    assert "copper-weight mismatch" in order["api"]["note"]


def test_api_quote_qty_without_estimate_row(tmp_path, monkeypatch, api_env,
                                            capsys):
    """--qty with no matching quote-matrix row: the API quote proceeds, the
    local estimate comparison is marked instead of silently using rows[0]."""
    pcb, fab, qj = make_fab(tmp_path)            # matrix has qty 5 only
    fake = quote_session(price=33.0)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api", qty="30"))
    assert code == 0
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["real_price"] == 33.0
    assert aq["estimate"] is None
    assert aq["estimate_note"] == "no matching estimate row (qty 30)"
    assert aq["qty"] == 30
    assert aq["calculate_request"]["pcbParam"]["qty"] == 30
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["quote"]["selected"] is None
    assert order["quote"]["estimate_note"] == ("no matching estimate row "
                                               "(qty 30)")


def test_api_scope_pending_exits_0(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = make_fab(tmp_path)
    fake = FakeSession(upload_gerber=err_resp(403, message="forbidden"))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api"))
    assert code == 0                     # scope review pending is NOT an error
    assert fake.names() == ["upload_gerber"]
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "scope_pending"
    assert "Permission Setting" in order["api"]["note"]
    assert not (fab / "api_quote.json").exists()


def test_api_scope_pending_after_upload_keeps_file_key(tmp_path, monkeypatch,
                                                       api_env, capsys):
    """403 at calculate (post-upload): scope-pending note added AND the
    obtained fileKey recorded so a later run can skip re-upload."""
    pcb, fab, qj = make_fab(tmp_path)
    fake = FakeSession(upload_gerber=ok_resp("FKEY-KEEP"),
                       audit=ok_resp({}),
                       calculate=err_resp(403, message="forbidden"))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api"))
    assert code == 0
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "scope_pending"
    assert order["api"]["file_key"] == "FKEY-KEEP"
    assert any("scope approval pending" in s for s in order["human_steps"])


def test_api_bad_signature_exits_2(tmp_path, monkeypatch, api_env, capsys):
    pcb, fab, qj = make_fab(tmp_path)
    fake = FakeSession(upload_gerber=err_resp(401, message="sig fail"))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api"))
    assert code == 2
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "bad_signature"


def test_api_missing_creds_exits_2(tmp_path, monkeypatch, capsys):
    for v in API_ENV:
        monkeypatch.delenv(v, raising=False)
    pcb, fab, qj = make_fab(tmp_path)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api"))
    assert code == 2
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["available"] is False
    assert "HWDE_JLCPCB_APPID" in order["api"]["note"]


# ===================================== order_submit --api-create (mocked)

def write_api_quote(fab, price=12.5, qty=5, board="b1", age_h=0.0,
                    fetched_at=None, shipping=None, ship_cost=20.0,
                    ship_list=None, grand_total=None, param_qty=None):
    """api_quote.json fixture. No shipping -> grand_total == price (token
    "b1 5pcs 12.5"); with shipping -> grand_total = price + ship_cost."""
    if fetched_at is None:
        fetched_at = (_dt.datetime.now().astimezone()
                      - _dt.timedelta(hours=age_h)
                      ).isoformat(timespec="seconds")
    q = {"board": board, "qty": qty, "real_price": price, "file_key": "FKEY1",
         "gerber_sha256": order_submit.sha256(fab / "b1_gerbers.zip"),
         "design_sha256": fabhash.design_hash(fab / "b1_gerbers.zip"),
         "fetched_at": fetched_at,
         "shipping_cost": ship_cost if shipping else 0.0,
         "grand_total": round(price + (ship_cost if shipping else 0.0), 2),
         "calculate_request": {"orderType": 1, "fileKey": "FKEY1",
                               "achieveDate": 120,
                               "pcbParam": {"layer": 2, "copperWeight": "1",
                                            "qty": (qty if param_qty is None
                                                    else param_qty)}}}
    if shipping:
        q["shipping_method"] = shipping
        q["ship_list"] = (ship_list if ship_list is not None else
                          [{"options": shipping, "cost": ship_cost,
                            "day": "3"}])
    if grand_total is not None:
        q["grand_total"] = grand_total
    p = fab / "api_quote.json"
    p.write_text(json.dumps(q, indent=1), encoding="utf-8")
    return p


CREATE_OK = {"orderId": 55, "batchNum": "W2026072801", "orderType": 1,
             "orderDate": "2026-07-28 12:00:00"}


def test_api_create_mismatched_confirm_refuses(tmp_path, monkeypatch,
                                               api_env, capsys):
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    fake = FakeSession()                       # any call would KeyError
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 99.0"))
    assert code == 2
    assert fake.calls == []                    # create NEVER reached
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "refused"
    assert "confirm token does not match" in order["api"]["note"]


def test_api_create_matching_confirm_creates_once(tmp_path, monkeypatch,
                                                  api_env, capsys):
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab, price=12.5, qty=5, board="b1",
                         shipping="express", ship_cost=20.0)
    sj = tmp_path / "ship.json"
    sj.write_text(json.dumps({
        "shippingAddress": {"firstName": "A", "city": "B"},
        "taxOrVATNumber": "T1", "billingAddressFlag": 0}), encoding="utf-8")
    fake = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 32.5", "--ship-json", str(sj)))
    assert code == 0
    assert fake.names() == ["create_order"]    # exactly once, nothing else
    payload = fake.calls[0][1]
    assert payload["fileKey"] == "FKEY1" and payload["orderType"] == 1
    assert payload["pcbParam"]["layer"] == 2
    assert payload["shippingMethod"] == "express"   # from the quote record
    assert payload["shippingAddress"]["city"] == "B"
    assert payload["taxOrVATNumber"] == "T1"
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "created"
    assert order["api"]["order"]["batchNum"] == "W2026072801"
    assert order["api"]["order"]["orderId"] == 55
    assert order["order_number"] == "W2026072801"


def test_api_create_latch_refuses_second_create(tmp_path, monkeypatch,
                                                api_env, capsys):
    """Created-latch: once order.json records api.order, a second create
    refuses with ZERO transport calls; the record survives."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    argv = submit_argv(pcb, fab, qj, "--api-create",
                       "--api-quote-file", str(aq),
                       "--confirm", "b1 5pcs 12.5")
    fake = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(argv) == 0
    assert fake.names() == ["create_order"]

    fake2 = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(argv) == 2
    assert fake2.calls == []                   # zero transport calls
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["order"]["batchNum"] == "W2026072801"  # intact
    assert "already recorded" in order["api"]["note"]
    assert "W2026072801" in order["api"]["note"]


def test_idless_create_still_arms_latch(tmp_path, monkeypatch, api_env,
                                        capsys):
    """S-2 defense-in-depth: a create that returns business-ok WITHOUT
    orderId/batchNum still records verdict "created", warns loudly, and the
    latch refuses a second create (zero transport calls)."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    argv = submit_argv(pcb, fab, qj, "--api-create",
                       "--api-quote-file", str(aq),
                       "--confirm", "b1 5pcs 12.5")
    fake = FakeSession(create_order=ok_resp({}))       # ok, no ids
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(argv) == 0
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "created"
    assert order["api"]["order"]["batchNum"] is None
    assert any("WITHOUT orderId/batchNum" in s for s in order["human_steps"])

    fake2 = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(argv) == 2
    assert fake2.calls == []                   # latch armed by verdict alone
    order2 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order2["api"]["verdict"] == "created"       # not downgraded
    assert any("WITHOUT orderId/batchNum" in s for s in order2["human_steps"])


def test_api_create_refuses_after_recorded_web_order(tmp_path, monkeypatch,
                                                     api_env, capsys):
    """P10-1a: a WEB order recorded via --order-number arms the latch too -
    the same workspace must never be API-ordered again (the lumina-carrier
    structural bypass: order_number set, api.verdict 'error', latch unarmed).
    The refusal names BOTH clearing paths."""
    pcb, fab, qj = make_fab(tmp_path)
    assert order_submit.main(submit_argv(pcb, fab, qj,
                                         "--order-number", "W555")) == 0
    aq = write_api_quote(fab)
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert fake.calls == []                    # zero transport calls
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["order_number"] == "W555"     # record intact
    assert "already recorded" in order["api"]["note"]
    assert "W555" in order["api"]["note"]
    assert "api.order" in order["api"]["note"]           # clearing path 1
    assert "order_number" in order["api"]["note"]        # clearing path 2


def test_api_create_prearms_attempt_on_disk_before_transport(
        tmp_path, monkeypatch, api_env, capsys):
    """P10-1b: the create_attempt record hits DISK before create_order is
    called, so even a hard process kill mid-create leaves the refusal armed.
    A successful create updates it to state 'created'."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    seen = {}

    class PeekSession(FakeSession):
        def create_order(self, payload):
            self.calls.append(("create_order", payload))
            on_disk = json.loads((fab / "order.json")
                                 .read_text(encoding="utf-8"))
            seen["attempt"] = (on_disk.get("api") or {}).get("create_attempt")
            return ok_resp(CREATE_OK)

    fake = PeekSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 0
    assert seen["attempt"]["state"] == "in_flight"   # armed BEFORE the call
    assert seen["attempt"]["grand_total"] == 12.5
    assert seen["attempt"]["at"]
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["create_attempt"]["state"] == "created"
    assert order["api"]["verdict"] == "created"


def test_api_create_ambiguous_failure_blocks_retry(tmp_path, monkeypatch,
                                                   api_env, capsys):
    """P10-1b: business code 2 (unknown_error) is ambiguous - an order MAY
    have landed and no endpoint can say. The attempt record persists as
    failed:unknown_error and the next --api-create refuses with ZERO
    transport calls, pointing at the portal."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    argv = submit_argv(pcb, fab, qj, "--api-create",
                       "--api-quote-file", str(aq),
                       "--confirm", "b1 5pcs 12.5")
    fake = FakeSession(create_order=err_resp(200, code=2,
                                             message="unknown_error"))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(argv) == 2
    assert fake.names() == ["create_order"]
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["create_attempt"]["state"] == "failed:unknown_error"

    fake2 = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(argv) == 2
    assert fake2.calls == []                   # blocked before transport
    order2 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "ended ambiguously" in order2["api"]["note"]
    assert "portal" in order2["api"]["note"]
    assert order2["api"]["create_attempt"]["state"] == "failed:unknown_error"


def test_api_create_transport_loss_leaves_in_flight_and_blocks(
        tmp_path, monkeypatch, api_env, capsys):
    """P10-1b: transport dies mid-create -> the PRE-ARMED record stays
    in_flight on disk and the next --api-create refuses mechanically."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    argv = submit_argv(pcb, fab, qj, "--api-create",
                       "--api-quote-file", str(aq),
                       "--confirm", "b1 5pcs 12.5")

    class DyingSession(FakeSession):
        def create_order(self, payload):
            self.calls.append(("create_order", payload))
            raise jlcapi.JlcApiError("connection lost mid-create")

    fake = DyingSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(argv) == 2
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["create_attempt"]["state"] == "in_flight"
    assert order["api"]["verdict"] == "transport"

    fake2 = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(argv) == 2
    assert fake2.calls == []                   # an order may exist - refuse
    order2 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "ended ambiguously" in order2["api"]["note"]


def test_api_create_clean_refusal_writes_no_attempt(tmp_path, monkeypatch,
                                                    api_env, capsys):
    """P10-1: pre-transport refusals (e.g. a bad confirm token) never create
    an attempt record - fixing the input and retrying stays mechanical."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 99.0")) == 2
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "create_attempt" not in order["api"]


def test_api_create_unambiguous_reject_does_not_block_retry(
        tmp_path, monkeypatch, api_env, capsys):
    """P10-1: bad_signature is NOT ambiguous (the order definitely did not
    land) - the failed:bad_signature attempt record does not block the next
    create once the credentials are fixed."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    argv = submit_argv(pcb, fab, qj, "--api-create",
                       "--api-quote-file", str(aq),
                       "--confirm", "b1 5pcs 12.5")
    fake = FakeSession(create_order=err_resp(401, message="sig fail"))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(argv) == 2
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["create_attempt"]["state"] == "failed:bad_signature"

    fake2 = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(argv) == 0
    assert fake2.names() == ["create_order"]
    order2 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order2["api"]["verdict"] == "created"
    assert order2["api"]["create_attempt"]["state"] == "created"


def test_api_quote_pcb_only_estimate_for_assembly_rows(tmp_path, monkeypatch,
                                                       api_env, capsys):
    """P10-3: an assembly-inclusive quote row contributes its PCB sub-total
    as the H5 estimate (the Open API prices bare PCBs ONLY), with the
    excluded assembly total surfaced separately - comparing the combined
    total against the API's PCB price told the human the wrong direction of
    error on both credentialed runs."""
    pcb, fab, qj = make_fab(tmp_path)
    quote = {"spec": {"layers": 2, "width_mm": 20.0, "height_mm": 25.0,
                      "thickness_mm": 1.6, "assembly": True},
             "estimated": True,
             "matrix": [{"qty": 5, "surface_finish": "HASL",
                         "solder_mask_color": "green",
                         "pcb": {"total": 4.0},
                         "assembly": {"total": 15.65},
                         "total": 19.65, "unit_cost": 3.93}]}
    qj.write_text(json.dumps(quote), encoding="utf-8")
    fake = quote_session(price=6.2, ship=[{"options": "DHL", "cost": 1.0}])
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["estimate"] == 4.0               # PCB-only, NOT 19.65
    assert aq["estimate_scope"] == "pcb_only"
    assert aq["estimate_assembly_excluded"] == 15.65
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    step = next(s for s in order["human_steps"]
                if s.startswith("API quote in "))
    assert "PCB-only estimate 4.0" in step
    assert "bare PCBs only" in step


def test_rerun_preserves_created_order(tmp_path, monkeypatch, api_env,
                                       capsys):
    """A plain re-run (no api flags) deep-merges the existing api block:
    the placed-order record survives and order_track still resolves it."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    fake = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5")) == 0

    assert order_submit.main(submit_argv(pcb, fab, qj)) == 0   # plain re-run
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["order"]["batchNum"] == "W2026072801"
    assert order["order_number"] == "W2026072801"

    trk = FakeSession(order_detail=ok_resp(detail_data(1)))
    payload = order_track.run(tmp_path, session=trk)
    assert payload["batch_num"] == "W2026072801"


def test_api_create_gerber_drift_refused(tmp_path, monkeypatch, api_env,
                                         capsys):
    """The create is bound to the QUOTED DESIGN: a package whose content
    changed after the quote refuses with a re-quote remediation."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)                  # design hash of the current zip
    (fab / "b1_gerbers.zip").write_bytes(b"PK\x03\x04DIFFERENT")
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "design hash mismatch" in order["api"]["note"]
    assert "re-run --api" in order["api"]["note"]


def _gerber_zip(path, *, when="2026-07-28T11:55:55", version="10.0.3",
                x="X250000"):
    """A minimal but REAL fab package: gerber + drill + job file, with the
    two volatile stamps KiCad writes into every export."""
    import zipfile
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("b1-F_Cu.gtl",
                    f"%TF.GenerationSoftware,KiCad,Pcbnew,{version}*%\n"
                    f"%TF.CreationDate,{when}-07:00*%\n"
                    "%FSLAX46Y46*%\n"
                    f"G04 Created by KiCad (PCBNEW {version}) date {when}*\n"
                    f"{x}Y250000D02*\nM02*\n")
        zf.writestr("b1.drl",
                    f"M48\n; DRILL file KiCad {version} date {when}\n"
                    f"; #@! TF.CreationDate,{when}-07:00\n"
                    "T1C0.300\nM30\n")
        zf.writestr("b1-job.gbrjob", json.dumps(
            {"Header": {"GenerationSoftware": {"Vendor": "KiCad",
                                               "Version": version},
                        "CreationDate": f"{when}-07:00"},
             "GeneralSpecs": {"LayerNumber": 2}}, indent=2))


def test_api_create_survives_a_reexport_of_the_same_design(
        tmp_path, monkeypatch, api_env, capsys):
    """A re-export changes every gerber's timestamp and the zip sha, but not
    the design - and must NOT invalidate an approved quote (LEARNINGS
    2026-07-30 [fab_export][order_submit][jlcapi]). The create still goes out
    against the quoted fileKey, i.e. the bytes JLC audited."""
    pcb, fab, qj = make_fab(tmp_path)
    zip_path = fab / "b1_gerbers.zip"
    _gerber_zip(zip_path)
    aq = write_api_quote(fab)
    quoted_sha = order_submit.sha256(zip_path)

    _gerber_zip(zip_path, when="2026-08-06T09:01:02", version="10.0.4")
    assert order_submit.sha256(zip_path) != quoted_sha      # file changed
    fake = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5")) == 0
    assert fake.names() == ["create_order"]
    assert fake.calls[0][1]["fileKey"] == "FKEY1"           # the audited bytes

    # ... while a real copper change on the same clock still refuses
    # (manual reorder clear = api.order + verdict + order_number, exactly
    # the paths the extended latch refusal names)
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    order["api"].pop("order"), order["api"].pop("verdict")
    order.pop("order_number", None)
    (fab / "order.json").write_text(json.dumps(order), encoding="utf-8")
    _gerber_zip(zip_path, when="2026-08-06T09:01:02", version="10.0.4",
                x="X260000")
    fake2 = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5")) == 2
    assert fake2.calls == []
    order2 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "design hash mismatch" in order2["api"]["note"]


def test_api_create_refuses_four_layer(tmp_path, monkeypatch, api_env,
                                       capsys):
    """4+ layer create is refused LOCALLY: JLC answers every 4L payload with
    HTTP 200 {code 2, unknown_error} and offers no way to ask afterwards
    whether it landed (LEARNINGS 2026-07-30 [ordering])."""
    pcb, fab, qj = make_fab(tmp_path, stackup="JLC04161H-1080B")
    aq = write_api_quote(fab)
    q = json.loads(aq.read_text(encoding="utf-8"))
    q["calculate_request"]["pcbParam"]["layer"] = 4
    aq.write_text(json.dumps(q), encoding="utf-8")
    fake = FakeSession()                       # any call would KeyError
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert fake.calls == []                    # zero transport calls
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    note = order["api"]["note"]
    assert "4-layer" in note and "web cart" in note
    assert "--order-number" in note


def test_api_quote_still_works_for_four_layer(tmp_path, monkeypatch, api_env,
                                              capsys):
    """The guard is create-only: `calculate` prices 4-layer boards correctly
    and that real price is the whole point of the quote leg."""
    pcb, fab, qj = make_fab(tmp_path, stackup="JLC04161H-1080B")
    quote = json.loads(qj.read_text(encoding="utf-8"))
    quote["spec"]["layers"] = 4
    qj.write_text(json.dumps(quote), encoding="utf-8")
    fake = quote_session(price=35.47)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["real_price"] == 35.47
    assert aq["calculate_request"]["pcbParam"]["layer"] == 4
    assert aq["calculate_request"]["pcbParam"]["insideCuprumThickness"] == "0.5"


def test_api_create_ship_json_whitelist(tmp_path, monkeypatch, api_env,
                                        capsys):
    """--ship-json injection of quote-bound keys refuses, listing the
    offenders - never silently dropped."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    sj = tmp_path / "ship.json"
    sj.write_text(json.dumps({
        "shippingAddress": {"city": "X"},
        "fileKey": "EVIL", "pcbParam": {"qty": 9999}, "orderType": 3}),
        encoding="utf-8")
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5", "--ship-json", str(sj)))
    assert code == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    note = order["api"]["note"]
    for offender in ("fileKey", "orderType", "pcbParam"):
        assert offender in note


def test_api_create_stale_quote_refused(tmp_path, monkeypatch, api_env,
                                        capsys):
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab, age_h=48.0)
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert fake.calls == []


def test_api_create_future_quote_refused(tmp_path, monkeypatch, api_env,
                                         capsys):
    """A fetched_at in the future (beyond skew tolerance) = broken clock."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab, age_h=-1.0)      # 1 h in the future
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "FUTURE" in order["api"]["note"]


def test_api_create_bad_fetched_at_refused(tmp_path, monkeypatch, api_env,
                                           capsys):
    """An unparseable fetched_at is a clean exit-2 refusal, no traceback."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab, fetched_at="not-a-timestamp")
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "fetched_at unparseable" in order["api"]["note"]


def test_api_create_requires_quote_and_confirm(tmp_path, monkeypatch,
                                               api_env, capsys):
    pcb, fab, qj = make_fab(tmp_path)
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(pcb, fab, qj, "--api-create"))
    assert code == 2
    assert fake.calls == []


# ============================= N1: latch is canonical, --out cannot bypass

def test_api_create_latch_immune_to_out_redirect(tmp_path, monkeypatch,
                                                 api_env, capsys):
    """--out must not sidestep the created-latch: the canonical
    fab/order.json is the record of truth for prior state."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    create_args = ("--api-create", "--api-quote-file", str(aq),
                   "--confirm", "b1 5pcs 12.5")
    fake = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, *create_args)) == 0

    elsewhere = tmp_path / "elsewhere.json"
    fake2 = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, *create_args, "--out", str(elsewhere)))
    assert code == 2
    assert fake2.calls == []                   # zero transport calls
    canonical = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert canonical["api"]["order"]["batchNum"] == "W2026072801"
    assert "already recorded" in canonical["api"]["note"]
    assert canonical["api"]["verdict"] == "created"      # not downgraded
    assert canonical["api"]["last_create_verdict"] == "refused"
    # --out received a COPY of the refused payload, not a fresh slate
    copy = json.loads(elsewhere.read_text(encoding="utf-8"))
    assert copy["api"]["order"]["batchNum"] == "W2026072801"


def test_api_create_custom_out_still_records_canonically(tmp_path,
                                                         monkeypatch,
                                                         api_env, capsys):
    """Inverse order: the FIRST create used --out custom - the api.order
    record must still land in the canonical fab/order.json, so a later
    plain create is latched."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab)
    create_args = ("--api-create", "--api-quote-file", str(aq),
                   "--confirm", "b1 5pcs 12.5")
    custom = tmp_path / "custom.json"
    fake = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, *create_args, "--out", str(custom))) == 0
    canonical = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert canonical["api"]["order"]["batchNum"] == "W2026072801"
    assert custom.exists()                     # the copy was also written

    fake2 = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(submit_argv(pcb, fab, qj, *create_args)) == 2
    assert fake2.calls == []


# ==================== N2: board-specific notes survive, guard stays armed

def test_board_note_survives_rewrites_and_guard_stays_armed(tmp_path,
                                                            monkeypatch,
                                                            api_env, capsys):
    """The reviewer's probe: pd-trigger-style note, no stackup.md. Run 1
    refuses AND must not destroy the note; run 2 must STILL refuse."""
    pcb, fab, qj = make_fab(tmp_path)
    note = ("BOARD-SPECIFIC: 2oz copper MUST be selected at JLC order time "
            "(NOT default 1oz)")
    (fab / "order.json").write_text(json.dumps(
        {"human_steps": [note], "api": {}}), encoding="utf-8")

    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 2
    assert fake.calls == []
    order1 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert note in order1["human_steps"]       # evidence survived run 1
    assert "copper-weight mismatch" in order1["api"]["note"]

    fake2 = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 2
    assert fake2.calls == []                   # guard STILL armed on run 2
    order2 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert note in order2["human_steps"]       # verbatim, forever
    assert order2["human_steps"].count(note) == 1   # and not duplicated
    assert "copper-weight mismatch" in order2["api"]["note"]


# ========================== N3: freight is inside the attested grand total

def test_api_create_ship_method_not_in_shiplist_refused(tmp_path,
                                                        monkeypatch,
                                                        api_env, capsys):
    """A hand-edited shipping_method that is not among the QUOTED options
    refuses before any transport."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab, shipping="hand-edited", ship_cost=20.0,
                         ship_list=[{"options": "express", "cost": 20.0,
                                     "day": "3"}])
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 32.5"))
    assert code == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "not among the quoted shipList options" in order["api"]["note"]


def test_api_create_ship_cost_drift_refused(tmp_path, monkeypatch, api_env,
                                            capsys):
    """The recorded freight must match the quoted shipList cost - drift
    means the token attested a different grand total."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab, shipping="express", ship_cost=20.0,
                         ship_list=[{"options": "express", "cost": 25.0,
                                     "day": "3"}])
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 32.5"))
    assert code == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "does not match the attested freight" in order["api"]["note"]


# ================================ N4: attested qty vs calculated qty

def test_api_create_qty_mismatch_refused(tmp_path, monkeypatch, api_env,
                                         capsys):
    """The reviewer's 5-vs-500 shape: top-level qty says 5, the calculate
    request actually priced 500 -> refuse, zero calls."""
    pcb, fab, qj = make_fab(tmp_path)
    aq = write_api_quote(fab, qty=5, param_qty=500)
    fake = FakeSession()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    code = order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create", "--api-quote-file", str(aq),
        "--confirm", "b1 5pcs 12.5"))
    assert code == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "pcbParam.qty" in order["api"]["note"]


# ================== INFO: created verdict is sticky, stale quotes flagged

def test_created_verdict_not_downgraded_and_stale_flagged(tmp_path,
                                                          monkeypatch,
                                                          api_env, capsys):
    """quote ok -> create -> FAILING re-quote: verdict stays "created", the
    fresh outcome lands in last_quote_verdict, and the surviving quote
    pointers are flagged stale."""
    pcb, fab, qj = make_fab(tmp_path)
    fake_q = quote_session(price=12.5, ship=[{"options": "express",
                                              "cost": 20.0, "day": "3"}])
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake_q)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0

    fake_c = FakeSession(create_order=ok_resp(CREATE_OK))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake_c)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, "--api-create",
        "--api-quote-file", str(fab / "api_quote.json"),
        "--confirm", "b1 5pcs 32.5")) == 0     # grand total from the quote

    # unchanged zip -> the recorded fileKey is reused (no re-upload), so a
    # signature failure now manifests at the first live call of the run
    fake_f = FakeSession(audit=ok_resp({"minLineWidth": 0.2}),
                         calculate=err_resp(401, message="sig fail"))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake_f)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 2
    assert "upload_gerber" not in fake_f.names()

    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "created"          # never downgraded
    assert order["api"]["last_quote_verdict"] == "bad_signature"
    assert order["api"]["quote_stale"] is True           # pointers flagged
    assert order["api"]["order"]["batchNum"] == "W2026072801"
    assert order["api"]["quote_real"] == 12.5            # still present


# ============================================================== pcbParam

def test_build_pcb_param_2oz_enig_black():
    param = order_submit.build_pcb_param(
        {"layers": 4, "qty": 10, "copper_weight_oz": 2,
         "surface_finish": "ENIG", "solder_mask_color": "black",
         "width_mm": 30.0, "height_mm": 40.0, "thickness_mm": 1.6})
    assert param["copperWeight"] == "2"        # 2 oz -> "2" (create example)
    assert param["surfaceFinish"] == 2         # ENIG
    assert param["pcbColor"] == 5              # black
    assert param["layer"] == 4 and param["qty"] == 10
    assert param["width"] == 30.0 and param["length"] == 40.0
    assert param["goldThickness"] == 2         # required when ENIG


BASE_SPEC = {"layers": 2, "qty": 5, "width_mm": 20.0, "height_mm": 25.0}


def test_build_pcb_param_defaults():
    param = order_submit.build_pcb_param(dict(BASE_SPEC))
    assert param["copperWeight"] == "1"
    assert param["surfaceFinish"] == 1         # HASL -> lead-free HASL
    assert param["pcbColor"] == 0              # green
    assert param["thickness"] == 1.6
    assert param["materialDetails"] == 0 and param["panelFlag"] == 0
    assert param["differentDesign"] == 1       # table-documented default
    assert param["needTechnics"] == 0          # table-documented default
    assert param["impedanceFlag"] == "no"
    assert "goldThickness" not in param        # HASL: not required


def test_build_pcb_param_covers_required_fields():
    """Every calculate-side required field is present, the old stencil* key
    names (impedance-template vocabulary) are banned, and the live-rejected
    create-side options stay OUT of the calculate payload."""
    param = order_submit.build_pcb_param(dict(BASE_SPEC))
    missing = REQUIRED_PCB_PARAM_FIELDS - set(param)
    assert not missing, f"required pcbParam fields missing: {sorted(missing)}"
    assert not any(k.startswith("stencil") for k in param)
    assert not (CREATE_SIDE_OMITTED & set(param))       # live code 2708
    vos = param["serviceConfigVos"]
    assert {v["serviceConfigCode"] for v in vos} == {"PPBP", "CPF"}


def test_build_pcb_param_inner_copper_by_layer_count():
    """insideCuprumThickness is a 4+ layer selection only (live code 2129
    on a 2-layer board, 2026-07-29)."""
    two = order_submit.build_pcb_param(dict(BASE_SPEC))
    assert "insideCuprumThickness" not in two
    four = order_submit.build_pcb_param(dict(BASE_SPEC, layers=4))
    # JLC's standard 4-layer inner copper is 0.5 oz. The create example's
    # "1" was hardcoded here and both bought a premium (48% of the PCB cost
    # on lumina-carrier) and fabricated a stackup the impedance was NOT
    # solved against (LEARNINGS 2026-07-30 [ordering][impedance]).
    assert four["insideCuprumThickness"] == "0.5"
    heavy = order_submit.build_pcb_param(
        dict(BASE_SPEC, layers=4, inner_copper_weight_oz=1))
    assert heavy["insideCuprumThickness"] == "1"


def test_build_pcb_param_missing_spec_refuses():
    with pytest.raises(order_submit.ApiRefused):
        order_submit.build_pcb_param({"layers": None, "qty": None})


def test_build_pcb_param_missing_dimensions_refuses():
    """Missing width/height refuses LOCALLY with remediation instead of
    sending "width": null and burning the round-trip."""
    with pytest.raises(order_submit.ApiRefused, match="width_mm/height_mm"):
        order_submit.build_pcb_param({"layers": 2, "qty": 5})
    with pytest.raises(order_submit.ApiRefused, match="width_mm/height_mm"):
        order_submit.build_pcb_param(
            {"layers": 2, "qty": 5, "width_mm": 20.0})   # height missing


def test_derive_copper_oz(tmp_path):
    """Copper weight is a board-killer: derive it or say you cannot - never
    default to 1 oz (LEARNINGS 2026-07-30 [order_submit][stackup])."""
    ws = tmp_path
    oz, src = order_submit.derive_copper_oz(ws)
    assert oz is None and "no architecture/stackup.md" in src   # no file
    arch = ws / "architecture"
    arch.mkdir()

    # file present, no Chosen heading -> refuse, and SAY it is present
    (arch / "stackup.md").write_text("# doc\nsome prose\n", encoding="utf-8")
    oz, src = order_submit.derive_copper_oz(ws)
    assert oz is None and "not the same as a missing file" in src

    # explicit oz marker in the id wins
    (arch / "stackup.md").write_text(
        "intro\n## Chosen: `JLC2313_1.6_2oz` (2-layer)\n", encoding="utf-8")
    oz, src = order_submit.derive_copper_oz(ws)
    assert oz == 2.0 and "2oz" in src

    # the real-world shape that defeated the old parser: a NUMBERED heading
    # with the id on a later line, and no oz marker in the id at all
    (arch / "stackup.md").write_text(
        "# board\n\n## 1. Chosen stackup\n\n**`JLC04161H-1080B`** - JLCPCB "
        "impedance-controlled 4 layer, 1.6 mm, 1 oz outer / 0.5 oz inner.\n",
        encoding="utf-8")
    oz, src = order_submit.derive_copper_oz(ws)
    assert oz == 1.0 and "stackups.yaml[JLC04161H-1080B]" in src

    # an unknown id with no marker is a refusal, not a 1 oz guess
    (arch / "stackup.md").write_text(
        "## Chosen: `SOME_OTHER_FAB_STACK`\n", encoding="utf-8")
    oz, src = order_submit.derive_copper_oz(ws)
    assert oz is None and "no stackup known" in src


def test_api_quote_refuses_underivable_copper(tmp_path, monkeypatch, api_env,
                                              capsys):
    """...and the refusal reaches the wire: zero transport calls."""
    pcb, fab, qj = make_fab(tmp_path, stackup=None)   # no stackup.md
    fake = quote_session()
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 2
    assert fake.calls == []
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "refused"
    assert "copper weight cannot be derived" in order["api"]["note"]
    assert "--copper-oz" in order["api"]["note"]


# ================================================================ live (net)

@pytest.mark.net
@pytest.mark.skipif(not all(os.environ.get(v) for v in API_ENV),
                    reason="HWDE_JLCPCB_* env vars absent")
def test_net_probe_live(tmp_path):
    """The day-one auth validation: one cheap signed call. Exit 0 (live or
    scope_pending - both prove signing) or 1 (classified auth problem)."""
    out = tmp_path / "probe.json"
    r = subprocess.run(
        [PYTHON, str(SCRIPTS / "lib" / "jlcapi.py"), "--probe",
         "--out", str(out)],
        capture_output=True, text=True, timeout=120)
    assert r.returncode in (0, 1), r.stdout + r.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload.get("verdict")
    assert payload.get("classification")


# ------------------------------------------- live follow-ups (2026-07-29)

AUDIT_PENDING = {"ok": False, "code": 2501,
                 "message": "no_audit_result_error", "http_status": 200,
                 "data": None, "trace_id": "t"}


class AuditSeqSession(FakeSession):
    """FakeSession whose audit() walks a response sequence (last repeats)."""

    def __init__(self, audits, **responses):
        super().__init__(**responses)
        self._audits = list(audits)

    def audit(self, key, language=0):
        self.calls.append(("audit", key))
        return (self._audits.pop(0) if len(self._audits) > 1
                else self._audits[0])


def test_audit_repolls_until_ready(tmp_path, monkeypatch, api_env, capsys):
    """pcb/audit is async (live code 2501 right after upload): re-poll with
    backoff until a result lands."""
    pcb, fab, qj = make_fab(tmp_path)
    fake = AuditSeqSession(
        [dict(AUDIT_PENDING), dict(AUDIT_PENDING),
         ok_resp({"minLineWidth": 0.2})],
        upload_gerber=ok_resp("FKEY1"),
        calculate=ok_resp({"priceWithoutFreight": 12.5}))
    sleeps = []
    monkeypatch.setattr(order_submit, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    assert fake.names().count("audit") == 3
    assert sleeps == list(order_submit.AUDIT_POLL_DELAYS_S[:2])
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["audit"]["attempts"] == 3
    assert "pending" not in aq["audit"]
    assert aq["audit"]["data"]["minLineWidth"] == 0.2


def test_audit_still_pending_flagged(tmp_path, monkeypatch, api_env, capsys):
    """DFM still running after all polls: quote proceeds, pending flagged."""
    pcb, fab, qj = make_fab(tmp_path)
    fake = AuditSeqSession(
        [dict(AUDIT_PENDING)],
        upload_gerber=ok_resp("FKEY1"),
        calculate=ok_resp({"priceWithoutFreight": 12.5}))
    sleeps = []
    monkeypatch.setattr(order_submit, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    assert sleeps == list(order_submit.AUDIT_POLL_DELAYS_S)
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["audit"]["pending"] is True
    assert aq["audit"]["attempts"] == 1 + len(order_submit.AUDIT_POLL_DELAYS_S)
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert order["api"]["verdict"] == "ok"


def test_country_passed_to_calculate(tmp_path, monkeypatch, api_env, capsys):
    """--country plumbs the doc-table country code into calculate (without
    it the live endpoint returns no shipList)."""
    pcb, fab, qj = make_fab(tmp_path)
    fake = quote_session(price=12.5, ship=[{"options": "DHL", "cost": 20.0}])
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(
        submit_argv(pcb, fab, qj, "--api", "--country", "US")) == 0
    calc_payload = [a for n, a in fake.calls if n == "calculate"][0]
    assert calc_payload["country"] == "US"
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["country"] == "US"

    (tmp_path / "nocountry").mkdir()
    pcb2, fab2, qj2 = make_fab(tmp_path / "nocountry")
    fake2 = quote_session(price=12.5)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(submit_argv(pcb2, fab2, qj2, "--api")) == 0
    calc2 = [a for n, a in fake2.calls if n == "calculate"][0]
    assert "country" not in calc2


def test_file_key_reused_when_sha_unchanged(tmp_path, monkeypatch, api_env,
                                            capsys):
    """Same gerber bytes -> the recorded fileKey is reused (no re-upload,
    JLC's async audit clock keeps running); changed bytes -> fresh upload."""
    pcb, fab, qj = make_fab(tmp_path)
    fake = quote_session(price=12.5)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    assert fake.names()[0] == "upload_gerber"

    fake2 = AuditSeqSession(
        [ok_resp({"minLineWidth": 0.2})],
        calculate=ok_resp({"priceWithoutFreight": 12.5}))
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    assert "upload_gerber" not in fake2.names()      # reused FKEY1
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["file_key"] == "FKEY1"

    zipf = fab / "b1_gerbers.zip"
    zipf.write_bytes(zipf.read_bytes() + b"x")       # sha drift
    fake3 = quote_session(price=12.5)
    fake3.responses["upload_gerber"] = ok_resp("FKEY2")
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake3)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    assert fake3.names()[0] == "upload_gerber"
    aq3 = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq3["file_key"] == "FKEY2"


def test_ship_method_selector(tmp_path, monkeypatch, api_env, capsys):
    """--ship-method picks a quoted option by name (case-insensitive,
    options or showOptions); an unquoted name refuses."""
    pcb, fab, qj = make_fab(tmp_path)
    ships = [{"options": "DHL EXPRESS", "showOptions": "DHL Express",
              "cost": "23.84"},
             {"options": "HKTHZXR-RMB",
              "showOptions": "Global Standard Direct Line",
              "cost": "6.39"}]
    fake = quote_session(price=40.0, ship=ships)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, "--api", "--country", "US",
        "--ship-method", "global standard direct line")) == 0
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["shipping_method"] == "HKTHZXR-RMB"
    assert aq["shipping_cost"] == 6.39
    assert aq["grand_total"] == 46.39

    fake2 = quote_session(price=40.0, ship=ships)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(submit_argv(
        pcb, fab, qj, "--api", "--ship-method", "carrier pigeon")) == 2
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "not in the quoted shipList" in json.dumps(order["api"])


def test_copper_oz_override_waives_with_note(tmp_path, monkeypatch, api_env,
                                             capsys):
    """--copper-oz is the explicit human override: without it a heavier-oz
    order note refuses; with it the quote proceeds at the override weight
    and a permanent COPPER WAIVER note is recorded exactly once."""
    pcb, fab, qj = make_fab(tmp_path)
    fake = quote_session(price=6.2)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 0
    order = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    order["human_steps"].insert(
        0, "BOARD-SPECIFIC: 2oz copper MUST be selected - 5A path")
    (fab / "order.json").write_text(json.dumps(order), encoding="utf-8")

    fake2 = quote_session(price=6.2)
    monkeypatch.setattr(order_submit, "_make_session", lambda: fake2)
    assert order_submit.main(submit_argv(pcb, fab, qj, "--api")) == 2
    order2 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    assert "copper-weight mismatch" in json.dumps(order2["api"])

    for _ in range(2):                       # waiver note stays singular
        fake3 = quote_session(price=6.2)
        monkeypatch.setattr(order_submit, "_make_session", lambda: fake3)
        assert order_submit.main(submit_argv(
            pcb, fab, qj, "--api", "--copper-oz", "1")) == 0
        param = [a for n, a in fake3.calls if n == "calculate"][0]["pcbParam"]
        assert param["copperWeight"] == "1"
    order3 = json.loads((fab / "order.json").read_text(encoding="utf-8"))
    steps = order3["human_steps"]
    assert sum("COPPER WAIVER:" in s for s in steps) == 1
    assert sum("2oz copper MUST" in s for s in steps) == 1   # note preserved
    aq = json.loads((fab / "api_quote.json").read_text(encoding="utf-8"))
    assert aq["copper_weight_oz"] == 1.0
    assert "human override" in aq["copper_weight_source"]
