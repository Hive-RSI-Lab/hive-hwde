#!/usr/bin/env python
"""jlcapi.py - JLCPCB Open API client (open.jlcpcb.com, `JOP` HMAC auth).

Implements the researched contract in ai-library/jlcpcb-openapi-2026/contract.md:

  - Auth: every request carries `Authorization: JOP appid="..",accesskey="..",
    timestamp="..",nonce="..",signature=".."`. The signature is
    Base64(HMAC-SHA256(secret, string-to-sign)) over FIVE lines, each terminated
    by \\n INCLUDING the last: METHOD / path(+query) / unix-seconds / 32-char
    nonce / raw body. The official doc-sample vector is reproduced by sign()
    exactly (pinned in tests/test_jlcapi.py), and the recipe is VERIFIED LIVE
    (2026-07-28): a signed POST to component/getComponentDetailByCode was
    accepted at the auth layer of open.jlcpcb.com with real credentials.
  - All business calls are POST, compact JSON (camelCase keys as given by the
    caller), HTTPS only. Response envelope is normalized across the documented
    variants: success vs successful, message vs msg, and the TDP-style
    file-id-in-`message` (surfaced into `data` when data is empty).
  - Uploads: multipart/form-data with parts `file` + `fileName`; the signature
    body line is EMPTY first (hendley live-verified style). On HTTP 401 the
    request is retried once in full jar-derived style: a `meta` text part
    carrying {"fileName": ...} is added to the multipart and that same JSON
    is signed. See contract "Uploads signing conflict".
  - Business errors NEVER raise - they come back normalized for classify().
    Only transport-level failures (DNS, TLS, timeout) raise JlcApiError.
  - No sandbox exists: create_order() places a REAL order. The safety gate
    (human confirm token) lives in order_submit.py, not here.

CLI (the day-one auth validation tool - one cheap signed call):
  jlcapi.py --probe [--out probe.json]
    Reads HWDE_JLCPCB_APPID / HWDE_JLCPCB_KEY / HWDE_JLCPCB_SECRET (or the
    pre-rename AIEE_* spelling of each), POSTs
    component/getComponentDetailByCode ["C2040"], reports the classification:
      ok            -> "live"                                     exit 0
      scope_pending -> "SIGNING VERIFIED - scope approval pending" exit 0
      bad_signature -> "signing broken"                            exit 1
      ip_blocked / rate_limited / error                            exit 1
      transport failure / missing env vars                         exit 2
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import sys
import time
import urllib.error
import urllib.request

OPEN_BASE = "https://open.jlcpcb.com"
_PREFIX = "/overseas/openapi"
PATHS = {
    "upload_gerber": _PREFIX + "/pcb/uploadGerber",
    "audit": _PREFIX + "/pcb/audit/get",
    "calculate": _PREFIX + "/pcb/calculate",
    "create": _PREFIX + "/pcb/create",
    "order_detail": _PREFIX + "/pcb/order/detail",
    "wip": _PREFIX + "/pcb/wip/get",
    "component_detail": _PREFIX + "/component/getComponentDetailByCode",
}
_NONCE_ALPHABET = string.ascii_uppercase + string.ascii_lowercase + string.digits
# Success `message` literals that must NOT be mistaken for a TDP-style
# file-id-in-message payload when data is empty.
_SUCCESS_WORDS = {"success", "successful", "ok"}

REMEDIATION = {
    "bad_signature": ("HTTP 401 = signature verification failed: check "
                      "appid/accesskey/secret, the 5-line string-to-sign "
                      "(trailing newline, body byte-identical to the wire), "
                      "and this host's clock (timestamp expiry is enforced)"),
    "scope_pending": ("service permission still under review at "
                      "api.jlcpcb.com -> Permission Setting"),
    "ip_blocked": ("add this host's public IP in the portal IP Whitelisting "
                   "or clear the whitelist"),
    "rate_limited": ("back off and retry later - JLCPCB publishes no rate "
                     "numbers (business code 1002 / HTTP 429)"),
    "unknown_error": (
        "JLC returned business code 2 'unknown_error' on HTTP 200 - the API "
        "cannot say why and there is NO order list/search endpoint to ask "
        "whether the call landed (order/detail and wip/get both need a "
        "batchNum you must already hold). DO NOT RETRY: an ambiguous create "
        "is how a board gets bought twice. Check the JLCPCB web portal, quote "
        "the trace id to support, and use the web cart. Known trigger: "
        "pcb/create with 4+ layers (2-layer works on the same account)"),
}


class JlcApiError(RuntimeError):
    """Transport-level failure (DNS/TLS/timeout) or unusable client state.
    Business-level errors do NOT raise - they normalize + classify()."""


# --------------------------------------------------------------- pure pieces

def sign(method: str, path_with_query: str, timestamp, nonce: str,
         body: str, secret: str) -> str:
    """Base64 HMAC-SHA256 over the 5-line string-to-sign (each line \\n-
    terminated, INCLUDING the last). Pure - pinned by the official vector."""
    string_to_sign = (f"{method.upper()}\n{path_with_query}\n{timestamp}\n"
                      f"{nonce}\n{body}\n")
    mac = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                   hashlib.sha256).digest()
    return base64.b64encode(mac).decode("ascii")


def make_nonce() -> str:
    """32 chars A-Za-z0-9 (the format the JOP header documents)."""
    return "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(32))


def compact_json(payload) -> str:
    """The byte-identical-to-wire JSON encoding (Java SDK toJSON style)."""
    return json.dumps(payload, separators=(",", ":"))


def normalize_response(http_status: int, headers: dict, body: bytes) -> dict:
    """-> {ok, code, message, data, http_status, trace_id, data_from_message}.
    Tolerates every envelope variant in the contract: success/successful,
    message/msg, and the TDP upload that returns the file id in `message` with
    empty data (flagged via data_from_message so PCB-endpoint consumers such
    as extract_file_key can refuse to treat a prose message as an id)."""
    trace_id = None
    for k, v in (headers or {}).items():
        if str(k).lower() == "j-trace-id":
            trace_id = v
            break
    text = body.decode("utf-8", errors="replace") if body else ""
    try:
        doc = json.loads(text) if text else None
    except json.JSONDecodeError:
        doc = None
    if isinstance(doc, dict):
        code = doc.get("code")
        message = doc.get("message", doc.get("msg"))
        data = doc.get("data")
        flag = doc.get("success", doc.get("successful"))
    else:
        code, message, data, flag = None, (text[:200] or None), doc, None
    ok = (http_status == 200 and flag is not False
          and (code in (200, "200") or (code is None and flag is True)))
    promoted = False
    if ok and (data is None or data == "") and isinstance(message, str) \
            and message and message.lower() not in _SUCCESS_WORDS:
        data = message                     # TDP-style: the id rides in message
        promoted = True
    return {"ok": ok, "code": code, "message": message, "data": data,
            "http_status": http_status, "trace_id": trace_id,
            "data_from_message": promoted}


def classify(resp: dict) -> str:
    """-> ok | bad_signature | scope_pending | ip_blocked | rate_limited |
    unknown_error | error. Business code 1000 (Forbidden IP) outranks EVERY
    HTTP status -
    a whitelist block can ride any transport wrapper and must never be
    misread as a scope problem. After that, HTTP statuses win. The live-
    observed scope-pending shape (2026-07-28, all services "Reviewing") is
    HTTP 403 + {"code":403,"success":false,"message":"API insufficient
    permissions, access denied"} - classified on the 403 status alone, so a
    hypothetical bodyless 403 lands in the same bucket."""
    status = resp.get("http_status")
    code = resp.get("code")
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = None
    if code == 1000:
        return "ip_blocked"
    if status == 401:
        return "bad_signature"
    if status == 403:
        return "scope_pending"
    if code == 1002 or status == 429:
        return "rate_limited"
    if resp.get("ok"):
        return "ok"
    if code == 2:
        # HTTP 200 + business code 2 "unknown_error": JLC's catch-all. It
        # left the tool with no verdict and no remediation on a live create
        # (2026-07-30), which is the worst possible state for a
        # money-spending call - it now classifies, and REMEDIATION says
        # "do not retry, check the portal".
        return "unknown_error"
    return "error"


def extract_file_key(resp: dict):
    """fileKey from an uploadGerber response: a bare string `data`, or a data
    dict's fileKey/key/fileId field - nothing else. Arbitrary dict values are
    NOT accepted, and neither is a TDP-style message-promoted `data` (the PCB
    upload puts the fileKey in data proper; a prose message is not an id)."""
    if resp.get("data_from_message"):
        return None
    data = resp.get("data")
    if isinstance(data, str) and data:
        return data
    if isinstance(data, dict):
        for key in ("fileKey", "key", "fileId"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def _mp_escape(value: str) -> str:
    """Header-safe multipart token: strip CR/LF (header injection) and escape
    double quotes so filename/name attributes cannot break the disposition."""
    return str(value).replace("\r", "").replace("\n", "").replace('"', '\\"')


def multipart_body(file_name: str, file_bytes: bytes,
                   fields: dict | None = None,
                   boundary: str | None = None) -> tuple[bytes, str]:
    """Manual multipart/form-data: file part `file` (octet-stream) + one text
    part per extra field. -> (body_bytes, content_type)."""
    boundary = boundary or ("aiee" + secrets.token_hex(16))
    out = bytearray()
    out += (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; '
            f'filename="{_mp_escape(file_name)}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    out += file_bytes
    out += b"\r\n"
    for name, value in (fields or {}).items():
        out += (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{_mp_escape(name)}"'
                f"\r\n\r\n{value}\r\n").encode("utf-8")
    out += f"--{boundary}--\r\n".encode("utf-8")
    return bytes(out), f"multipart/form-data; boundary={boundary}"


# ------------------------------------------------------------------- session

def _urllib_transport(method: str, url: str, headers: dict, body: bytes,
                      timeout: float) -> tuple[int, dict, bytes]:
    """Default transport: stdlib urllib.request, TLS defaults. Returns HTTP
    errors as (status, headers, body); raises JlcApiError on transport
    failure."""
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read()
        except OSError:
            payload = b""
        return exc.code, dict(exc.headers or {}), payload
    except urllib.error.URLError as exc:
        raise JlcApiError(f"transport failure for {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise JlcApiError(f"timeout after {timeout}s for {url}") from exc


class JlcSession:
    """One credential set against one base URL. Builds the JOP Authorization
    header per request; `transport` is injectable for hermetic tests
    (signature: (method, url, headers, body_bytes, timeout) ->
    (http_status, headers, body_bytes))."""

    def __init__(self, appid: str, accesskey: str, secret: str,
                 base_url: str = OPEN_BASE, timeout: float = 60,
                 transport=None):
        if not (appid and accesskey and secret):
            raise JlcApiError("appid, accesskey and secret are all required")
        self.appid = str(appid)
        self.accesskey = accesskey
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport or _urllib_transport

    def auth_header(self, method: str, path: str, sign_body: str,
                    timestamp=None, nonce: str | None = None) -> str:
        ts = str(int(time.time()) if timestamp is None else timestamp)
        nc = nonce or make_nonce()
        sig = sign(method, path, ts, nc, sign_body, self.secret)
        return (f'JOP appid="{self.appid}",accesskey="{self.accesskey}",'
                f'timestamp="{ts}",nonce="{nc}",signature="{sig}"')

    def _call(self, path: str, send_body: bytes, content_type: str,
              sign_body: str) -> dict:
        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
            "Authorization": self.auth_header("POST", path, sign_body),
        }
        status, rheaders, rbody = self.transport(
            "POST", self.base_url + path, headers, send_body, self.timeout)
        return normalize_response(status, rheaders, rbody)

    def post_json(self, path: str, payload) -> dict:
        """POST compact JSON; the signed body is byte-identical to the wire."""
        body = compact_json(payload)
        return self._call(path, body.encode("utf-8"),
                          "application/json", body)

    # ------------------------------------------------------------ endpoints

    def upload_gerber(self, zip_path) -> dict:
        """Multipart gerber upload. Attempt 1 is hendley live-verified style:
        parts `file` + `fileName`, signature body line EMPTY. On HTTP 401 the
        retry switches to the full jar-derived style: a text part `meta`
        carrying the JSON of the non-file fields is ADDED to the multipart,
        and that same JSON string is what gets signed."""
        from pathlib import Path
        p = Path(zip_path)
        try:
            file_bytes = p.read_bytes()
        except OSError as exc:
            raise JlcApiError(f"cannot read gerber zip {p}: {exc}") from exc
        path = PATHS["upload_gerber"]
        body, ctype = multipart_body(p.name, file_bytes,
                                     {"fileName": p.name})
        resp = self._call(path, body, ctype, "")
        if resp["http_status"] == 401:
            meta = compact_json({"fileName": p.name})
            body2, ctype2 = multipart_body(p.name, file_bytes,
                                           {"fileName": p.name, "meta": meta})
            resp = self._call(path, body2, ctype2, meta)
        return resp

    def audit(self, file_key: str, language: int = 0) -> dict:
        return self.post_json(PATHS["audit"],
                              {"key": file_key, "language": language})

    def calculate(self, payload: dict) -> dict:
        return self.post_json(PATHS["calculate"], payload)

    def create_order(self, payload: dict) -> dict:
        """Places a REAL order (no sandbox exists). The confirm-token safety
        gate lives in order_submit.py --api-create - keep it that way."""
        return self.post_json(PATHS["create"], payload)

    def order_detail(self, batch_num: str) -> dict:
        return self.post_json(PATHS["order_detail"], {"batchNum": batch_num})

    def wip(self, order_uuid: str) -> dict:
        return self.post_json(PATHS["wip"], {"orderUUID": order_uuid})

    def component_detail(self, codes: list) -> dict:
        return self.post_json(PATHS["component_detail"],
                              {"componentCodes": list(codes)})

    def get_balance(self):
        raise JlcApiError(
            "JLC Balance endpoint path not yet public - download the JLC "
            "Balance SDK jar from the portal console (SDKs page) and read "
            "the request URI constant, or wait for a live probe")


# --------------------------------------------------------------------- probe

# The skill was ai-ee before it was hwde. Every HWDE_* variable below is also
# read under its pre-rename AIEE_* spelling, so an existing credential setup
# keeps working; HWDE_ wins when both are set.
def cred_env(name: str) -> str | None:
    """Value of `name` (an HWDE_* var), falling back to its AIEE_* twin."""
    val = os.environ.get(name)
    if val or not name.startswith("HWDE_"):
        return val
    return os.environ.get("AIEE_" + name[len("HWDE_"):])


PROBE_ENV = ("HWDE_JLCPCB_APPID", "HWDE_JLCPCB_KEY", "HWDE_JLCPCB_SECRET")
_VERDICTS = {
    "ok": "live",
    "scope_pending": "SIGNING VERIFIED - scope approval pending",
    "bad_signature": "signing broken",
    "ip_blocked": "auth reached the API but this host's IP is not whitelisted",
    "rate_limited": "rate limited - inconclusive, retry later",
    "unknown_error": "JLC business code 2 (unknown_error) - DO NOT RETRY, "
                     "check the portal (see remediation)",
    "error": "unexpected API response - see message",
}
_EXIT = {"ok": 0, "scope_pending": 0}


def session_from_env(transport=None) -> "JlcSession":
    missing = [v for v in PROBE_ENV if not cred_env(v)]
    if missing:
        raise JlcApiError("missing env vars: " + ", ".join(missing))
    return JlcSession(cred_env("HWDE_JLCPCB_APPID"),
                      cred_env("HWDE_JLCPCB_KEY"),
                      cred_env("HWDE_JLCPCB_SECRET"),
                      transport=transport)


def probe(session: "JlcSession | None" = None) -> tuple[dict, int]:
    """One cheap signed call (component detail C2040) -> (payload, exit)."""
    session = session or session_from_env()
    resp = session.component_detail(["C2040"])
    cls = classify(resp)
    data = resp.get("data")
    payload = {
        "script": "jlcapi",
        "status": "pass" if cls in _EXIT else "violations",
        "probe_endpoint": PATHS["component_detail"],
        "classification": cls,
        "verdict": _VERDICTS[cls],
        "remediation": REMEDIATION.get(cls),
        "http_status": resp.get("http_status"),
        "code": resp.get("code"),
        "message": resp.get("message"),
        "trace_id": resp.get("trace_id"),
        "data_summary": (f"{len(data)} rows" if isinstance(data, list)
                         else type(data).__name__ if data is not None
                         else None),
    }
    return payload, _EXIT.get(cls, 1)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true",
                    help="one cheap signed call to validate auth end-to-end")
    ap.add_argument("--out", help="write the probe JSON here instead of stdout")
    args = ap.parse_args(argv)
    if not args.probe:
        ap.error("--probe is the only action")

    try:
        payload, exit_code = probe()
    except JlcApiError as exc:
        payload = {"script": "jlcapi", "status": "error",
                   "error": f"JlcApiError: {exc}"}
        exit_code = 2
    except Exception as exc:  # noqa: BLE001 (SPEC: any error -> exit 2)
        payload = {"script": "jlcapi", "status": "error",
                   "error": f"{type(exc).__name__}: {exc}"}
        exit_code = 2

    text = json.dumps(payload, indent=1)
    if args.out:
        from pathlib import Path
        try:
            Path(args.out).write_text(text, encoding="utf-8")
        except OSError as exc:
            # --out failure must not break the exit contract: surface the
            # payload on stdout and exit 2 naming the unwritable path.
            payload["out_write_error"] = f"cannot write {args.out}: {exc}"
            print(json.dumps(payload, indent=1))
            return 2
    else:
        print(text)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
