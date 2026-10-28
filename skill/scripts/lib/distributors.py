"""distributors.py - DigiKey / Mouser parametric-data clients (U15).

Why: a research task on a PART slot needs the authoritative datasheet link
and the vendor's own parametric values (the LCSC catalogue field is sometimes
the wrong rating - LEARNINGS 2026-08-08 [research][tools]: the IEC 24 A in the
"current" field vs the UL 10 A the requirement is written against). The
distributor APIs answer that deterministically; the client is built now and
EXITS 2 WITH THE EXACT MISSING CREDENTIAL until the owner registers keys
(owner-supplied prerequisite - the order_submit / jlcapi pattern).

Credentials (env vars):
    HWDE_DIGIKEY_CLIENT_ID + HWDE_DIGIKEY_CLIENT_SECRET
        developer.digikey.com -> My Apps -> create an app with the Product
        Information V4 product; the Client ID / Client Secret pair is what
        the OAuth2 client-credentials token call takes. HWDE_DIGIKEY_SANDBOX=1
        points at sandbox-api.digikey.com (sandbox keys, canned data).
    HWDE_MOUSER_API_KEY
        mouser.com/api-hub -> Search API key (a plain query-string key).
Each is also read under its pre-rename AIEE_* spelling (the skill was ai-ee
before it was hwde); HWDE_ wins when both are set.

Request shapes (public docs; UNVERIFIED LIVE until keys exist - verify-later
V21; every network leg goes through an injectable transport so tests pin the
shapes without touching the network):
    DigiKey  POST {base}/v1/oauth2/token   form: client_id, client_secret,
                                            grant_type=client_credentials
             POST {base}/products/v4/search/keyword
                  headers Authorization: Bearer <token>, X-DIGIKEY-Client-Id,
                  X-DIGIKEY-Locale-Site/Language/Currency (US/en/USD)
                  json {"Keywords": mpn, "Limit": n, "Offset": 0}
             GET  {base}/products/v4/search/{pn}/productdetails
    Mouser   POST https://api.mouser.com/api/v1/search/partnumber?apiKey=K
                  json {"SearchByPartRequest": {"mouserPartNumber": mpn,
                                                "partSearchOptions": "None"}}
Responses are normalized to ONE shape (normalize_*): provider, mpn,
manufacturer, description, datasheet_url, product_url, distributor_pn,
stock, price_breaks[{qty, unit_price, currency}], parameters{name: value},
status. Business errors come back as status "error" payloads, never raise;
only transport failures raise DistributorError (-> exit 2 in research.py).
"""
from __future__ import annotations

import os
from urllib.parse import quote

# The skill was ai-ee before it was hwde. Every HWDE_* variable below is also
# read under its pre-rename AIEE_* spelling, so an existing credential setup
# keeps working; HWDE_ wins when both are set.
def cred_env(name: str) -> str | None:
    """Value of `name` (an HWDE_* var), falling back to its AIEE_* twin."""
    val = os.environ.get(name)
    if val or not name.startswith("HWDE_"):
        return val
    return os.environ.get("AIEE_" + name[len("HWDE_"):])


DIGIKEY_ENV = ("HWDE_DIGIKEY_CLIENT_ID", "HWDE_DIGIKEY_CLIENT_SECRET")
MOUSER_ENV = ("HWDE_MOUSER_API_KEY",)
PROVIDERS = ("digikey", "mouser")
ENV = {"digikey": DIGIKEY_ENV, "mouser": MOUSER_ENV}
REGISTER = {
    "digikey": ("register at developer.digikey.com (My Apps -> create app "
                "with the Product Information V4 product) and export "
                "HWDE_DIGIKEY_CLIENT_ID + HWDE_DIGIKEY_CLIENT_SECRET; set "
                "HWDE_DIGIKEY_SANDBOX=1 to use sandbox-api.digikey.com keys"),
    "mouser": ("register at mouser.com/api-hub (Search API) and export "
               "HWDE_MOUSER_API_KEY"),
}
DIGIKEY_BASE = "https://api.digikey.com"
DIGIKEY_SANDBOX = "https://sandbox-api.digikey.com"
MOUSER_BASE = "https://api.mouser.com"
TIMEOUT = 30.0
USER_AGENT = "hwde research (distributor client)"


class DistributorError(Exception):
    """Transport-level failure or a client misuse - the exit-2 class."""


def missing_credentials(provider: str) -> list[str]:
    if provider not in ENV:
        raise DistributorError(f"unknown provider {provider!r} "
                               f"(one of {', '.join(PROVIDERS)})")
    return [v for v in ENV[provider] if not cred_env(v)]


def credential_message(provider: str) -> str | None:
    """The exact remediation line, or None when the provider is configured."""
    missing = missing_credentials(provider)
    if not missing:
        return None
    return (f"{provider}: missing env var{'s' if len(missing) > 1 else ''} "
            f"{' and '.join(missing)} - {REGISTER[provider]}")


# ---------------------------------------------------------------- transport
def http_transport(method: str, url: str, headers: dict | None = None,
                   data: dict | None = None, json_body: dict | None = None,
                   timeout: float = TIMEOUT) -> dict:
    """{status, json, text}. Raises DistributorError on DNS/TLS/timeout."""
    import httpx
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.request(method, url, headers=hdrs, data=data,
                               json=json_body)
    except httpx.HTTPError as exc:
        raise DistributorError(f"transport failure: {type(exc).__name__}: "
                               f"{exc}") from exc
    try:
        body = r.json()
    except ValueError:
        body = None
    return {"status": r.status_code, "json": body, "text": r.text}


# ------------------------------------------------------------------ DigiKey
class DigiKeyClient:
    def __init__(self, client_id: str, client_secret: str, transport=None,
                 sandbox: bool = False, locale: tuple = ("US", "en", "USD")):
        if not client_id or not client_secret:
            raise DistributorError("DigiKeyClient needs client_id + secret")
        self.client_id = client_id
        self.client_secret = client_secret
        self.transport = transport or http_transport
        self.base = DIGIKEY_SANDBOX if sandbox else DIGIKEY_BASE
        self.locale = locale
        self._token: str | None = None

    @classmethod
    def from_env(cls, transport=None) -> "DigiKeyClient":
        missing = missing_credentials("digikey")
        if missing:
            raise DistributorError(credential_message("digikey"))
        return cls(cred_env("HWDE_DIGIKEY_CLIENT_ID"),
                   cred_env("HWDE_DIGIKEY_CLIENT_SECRET"),
                   transport=transport,
                   sandbox=cred_env("HWDE_DIGIKEY_SANDBOX") == "1")

    def token(self) -> str:
        if self._token:
            return self._token
        resp = self.transport("POST", f"{self.base}/v1/oauth2/token",
                              headers={"Content-Type":
                                       "application/x-www-form-urlencoded"},
                              data={"client_id": self.client_id,
                                    "client_secret": self.client_secret,
                                    "grant_type": "client_credentials"})
        body = resp.get("json") or {}
        tok = body.get("access_token") if isinstance(body, dict) else None
        if resp.get("status") != 200 or not tok:
            raise DistributorError(
                f"DigiKey token call failed: HTTP {resp.get('status')} "
                f"{str(resp.get('text') or '')[:200]}")
        self._token = tok
        return tok

    def _headers(self) -> dict:
        site, lang, cur = self.locale
        return {"Authorization": f"Bearer {self.token()}",
                "X-DIGIKEY-Client-Id": self.client_id,
                "X-DIGIKEY-Locale-Site": site,
                "X-DIGIKEY-Locale-Language": lang,
                "X-DIGIKEY-Locale-Currency": cur,
                "Content-Type": "application/json",
                "Accept": "application/json"}

    def keyword(self, keywords: str, limit: int = 5) -> dict:
        return self.transport("POST", f"{self.base}/products/v4/search/keyword",
                              headers=self._headers(),
                              json_body={"Keywords": keywords,
                                         "Limit": int(limit), "Offset": 0})

    def details(self, product_number: str) -> dict:
        return self.transport(
            "GET", f"{self.base}/products/v4/search/"
                   f"{quote(product_number, safe='')}/productdetails",
            headers=self._headers())


def normalize_digikey(product: dict) -> dict:
    desc = product.get("Description") or {}
    man = product.get("Manufacturer") or {}
    variations = product.get("ProductVariations") or []
    breaks = []
    dpn = None
    for v in variations:
        dpn = dpn or v.get("DigiKeyProductNumber")
        for b in v.get("StandardPricing") or []:
            breaks.append({"qty": b.get("BreakQuantity"),
                           "unit_price": b.get("UnitPrice"),
                           "currency": "USD"})
    params = {}
    for p in product.get("Parameters") or []:
        name = p.get("ParameterText")
        if name:
            params[name] = p.get("ValueText")
    return {
        "provider": "digikey",
        "mpn": product.get("ManufacturerProductNumber"),
        "manufacturer": man.get("Name"),
        "description": (desc.get("DetailedDescription")
                        or desc.get("ProductDescription")),
        "datasheet_url": product.get("DatasheetUrl"),
        "product_url": product.get("ProductUrl"),
        "distributor_pn": dpn,
        "stock": product.get("QuantityAvailable"),
        "unit_price": product.get("UnitPrice"),
        "price_breaks": breaks,
        "parameters": params,
        "status": ((product.get("ProductStatus") or {}).get("Status")),
    }


# ------------------------------------------------------------------- Mouser
class MouserClient:
    def __init__(self, api_key: str, transport=None):
        if not api_key:
            raise DistributorError("MouserClient needs an api_key")
        self.api_key = api_key
        self.transport = transport or http_transport

    @classmethod
    def from_env(cls, transport=None) -> "MouserClient":
        if missing_credentials("mouser"):
            raise DistributorError(credential_message("mouser"))
        return cls(cred_env("HWDE_MOUSER_API_KEY"), transport=transport)

    def part_number(self, mpn: str, option: str = "None") -> dict:
        return self.transport(
            "POST", f"{MOUSER_BASE}/api/v1/search/partnumber?apiKey="
                    f"{quote(self.api_key, safe='')}",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"},
            json_body={"SearchByPartRequest": {"mouserPartNumber": mpn,
                                               "partSearchOptions": option}})

    def keyword(self, keyword: str, records: int = 5) -> dict:
        return self.transport(
            "POST", f"{MOUSER_BASE}/api/v1/search/keyword?apiKey="
                    f"{quote(self.api_key, safe='')}",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"},
            json_body={"SearchByKeywordRequest": {
                "keyword": keyword, "records": int(records),
                "startingRecord": 0}})


def _price(txt) -> float | None:
    if txt is None:
        return None
    s = str(txt)
    keep = "".join(ch for ch in s if ch.isdigit() or ch in ".,")
    keep = keep.replace(",", "")
    try:
        return float(keep) if keep else None
    except ValueError:
        return None


def normalize_mouser(part: dict) -> dict:
    params = {}
    for a in part.get("ProductAttributes") or []:
        name = a.get("AttributeName")
        if name:
            params[name] = a.get("AttributeValue")
    breaks = [{"qty": b.get("Quantity"), "unit_price": _price(b.get("Price")),
               "currency": b.get("Currency")}
              for b in part.get("PriceBreaks") or []]
    stock = part.get("AvailabilityInStock")
    try:
        stock = int(str(stock).replace(",", "")) if stock not in (None, "") \
            else None
    except ValueError:
        stock = None
    return {
        "provider": "mouser",
        "mpn": part.get("ManufacturerPartNumber"),
        "manufacturer": part.get("Manufacturer"),
        "description": part.get("Description"),
        "datasheet_url": part.get("DataSheetUrl"),
        "product_url": part.get("ProductDetailUrl"),
        "distributor_pn": part.get("MouserPartNumber"),
        "stock": stock,
        "unit_price": breaks[0]["unit_price"] if breaks else None,
        "price_breaks": breaks,
        "parameters": params,
        "status": part.get("LifecycleStatus"),
    }


# ------------------------------------------------------------------- lookup
def lookup(mpn: str, providers=PROVIDERS, limit: int = 5,
           transport=None) -> dict:
    """{results: {provider: {status, hits[], error?}}, missing: {provider:
    message}}. A provider without credentials is reported, not raised; a
    transport failure raises DistributorError."""
    results: dict = {}
    missing: dict = {}
    for prov in providers:
        msg = credential_message(prov)
        if msg:
            missing[prov] = msg
            results[prov] = {"status": "no_credentials", "hits": []}
            continue
        if prov == "digikey":
            client = DigiKeyClient.from_env(transport=transport)
            resp = client.keyword(mpn, limit=limit)
            body = resp.get("json") or {}
            if resp.get("status") != 200 or not isinstance(body, dict):
                results[prov] = {"status": "error", "hits": [],
                                 "http_status": resp.get("status"),
                                 "error": str(resp.get("text") or "")[:300]}
                continue
            hits = [normalize_digikey(p) for p in body.get("Products") or []]
            results[prov] = {"status": "pass", "hits": hits,
                             "count": body.get("ProductsCount", len(hits))}
        elif prov == "mouser":
            client = MouserClient.from_env(transport=transport)
            resp = client.part_number(mpn)
            body = resp.get("json") or {}
            errs = body.get("Errors") if isinstance(body, dict) else None
            if resp.get("status") != 200 or not isinstance(body, dict) or errs:
                results[prov] = {"status": "error", "hits": [],
                                 "http_status": resp.get("status"),
                                 "error": (str(errs)[:300] if errs else
                                           str(resp.get("text") or "")[:300])}
                continue
            sr = body.get("SearchResults") or {}
            hits = [normalize_mouser(p) for p in sr.get("Parts") or []][:limit]
            results[prov] = {"status": "pass", "hits": hits,
                             "count": sr.get("NumberOfResult", len(hits))}
    return {"mpn": mpn, "results": results, "missing": missing}
