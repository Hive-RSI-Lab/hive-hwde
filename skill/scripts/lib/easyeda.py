"""easyeda.py - an LCSC part's own EasyEDA footprint and symbol pins, cached.

JLC places a part the way its LCSC footprint model is drawn in EasyEDA, so
the CPL rotation a part needs depends on that model, not on the package name
(the owner's SOT-23-5/6 parts came out 180 deg off under a '^SOT-23' table
row). This module gets that model from the EasyEDA endpoint easyeda2kicad reads
(with its own plain request - see UA below), keeps
only the shapes a placement check needs, and caches them one JSON per LCSC
number so tests and reruns never touch the network.

Cache file `<cache_dir>/<LCSC>.json`:
  {"lcsc", "title", "package", "origin": [x, y], "pads": [PAD~ strings],
   "silk": [package shapes on the top silk layer], "pins": [symbol P~ strings]}
Coordinates are EasyEDA canvas units (10 mil = 0.254 mm), y pointing down,
the same sense as KiCad's board frame.

`get(lcsc, cache_dir, fetch=...)` returns a parsed `Model` or None when the
part has no data (not cached and fetch disabled, or the API had nothing).

A fetch that fails is never "no data" to a caller that passes `errors`: the
reason (HTTP status, or the network error) lands in `errors[lcsc]`; only a
404 or an empty answer means "EasyEDA has no model for this part". An HTTP
403 or 429 is EasyEDA's per-IP rate limit; the first one latches for the rest
of the process (`rate_limited()`), says so once on stderr, and every later
uncached part is skipped with that reason instead of asking again. There is
no sleeping back-off here - a script stays bounded, and the remedy is a rerun
once the limit clears (minutes), which finds the models already cached.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

UNIT_MM = 0.254
# The top copper layer, and EasyEDA's multi-layer (through-hole) pads.
PAD_LAYERS = {"1", "11"}
TOP_SILK = "3"
DEFAULT_CACHE = Path(os.environ.get("HWDE_EASYEDA_CACHE")
                     or Path.home() / ".cache" / "hwde" / "easyeda")


@dataclass
class Pad:
    number: str
    x: float          # mm, relative to the model origin, y down
    y: float
    w: float
    h: float
    shape: str
    rot: float = 0.0  # the pad's own turn inside the model


@dataclass
class Model:
    lcsc: str
    title: str
    package: str
    pads: list[Pad]
    pin_names: dict[str, str] = field(default_factory=dict)  # number -> name
    silk: list[list[tuple[float, float]]] = field(default_factory=list)


def _f(s: str) -> float:
    return float(s) if s not in ("", None) else 0.0


def trim(raw: dict, lcsc: str) -> dict:
    """The EasyEDA component JSON cut down to what the check reads."""
    pkg = raw.get("packageDetail") or {}
    ds = pkg.get("dataStr") or {}
    head = ds.get("head") or {}
    shapes = ds.get("shape") or []
    sym = raw.get("dataStr") or {}
    pins = [s for s in sym.get("shape") or [] if s.startswith("P~")]
    silk = [s for s in shapes
            if s.split("~", 1)[0] in ("TRACK", "CIRCLE", "ARC")
            and _silk_layer(s) == TOP_SILK]
    return {"lcsc": lcsc, "title": raw.get("title", ""),
            "package": pkg.get("title", ""),
            "origin": [_f(head.get("x")), _f(head.get("y"))],
            "pads": [s for s in shapes if s.startswith("PAD~")],
            "silk": silk, "pins": pins}


def _silk_layer(s: str) -> str:
    f = s.split("~")
    kind = f[0]
    if kind == "TRACK":
        return f[2]
    if kind == "CIRCLE":
        return f[5]
    if kind == "ARC":
        return f[2]
    return ""


def parse(cached: dict) -> Model:
    ox, oy = cached["origin"]
    pads = []
    for s in cached["pads"]:
        f = s.split("~")
        # PAD~shape~x~y~w~h~layer~net~number~...
        if f[6] not in PAD_LAYERS:
            continue
        pads.append(Pad(number=f[8].strip(),
                        x=(_f(f[2]) - ox) * UNIT_MM,
                        y=(_f(f[3]) - oy) * UNIT_MM,
                        w=_f(f[4]) * UNIT_MM, h=_f(f[5]) * UNIT_MM,
                        shape=f[1],
                        rot=_f(f[11]) if len(f) > 11 else 0.0))
    names = {}
    for s in cached.get("pins", []):
        segs = s.split("^^")
        head = segs[0].split("~")
        num = head[3].strip() if len(head) > 3 else ""
        name = ""
        if len(segs) > 3:
            nf = segs[3].split("~")
            name = nf[4].strip() if len(nf) > 4 else ""
        if num:
            names[num] = name
    silk = []
    for s in cached.get("silk", []):
        f = s.split("~")
        if f[0] == "TRACK":
            nums = [_f(v) for v in f[4].split()]
            silk.append([((nums[i] - ox) * UNIT_MM, (nums[i + 1] - oy) * UNIT_MM)
                         for i in range(0, len(nums) - 1, 2)])
        elif f[0] == "CIRCLE":
            cx, cy, r = _f(f[1]), _f(f[2]), _f(f[3])
            silk.append([((cx - ox) * UNIT_MM, (cy - oy) * UNIT_MM, r * UNIT_MM)])
    return Model(lcsc=cached["lcsc"], title=cached.get("title", ""),
                 package=cached.get("package", ""), pads=pads,
                 pin_names=names, silk=silk)


API = "https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5"
# Our own request rather than easyeda2kicad's EasyedaApi, because that one
# swallows an HTTP 403 into an empty result, so a rate-limited part would
# read as "EasyEDA has no such part". The endpoint rate-limits per IP: about
# ten quick requests, then 403 for minutes (2026-10-02) - hence the cache.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like "
      "Gecko) Chrome/120.0.0.0 Safari/537.36")


def _fetch_raw(lcsc: str) -> dict:
    """The component JSON's `result`; {} when EasyEDA has no such part. An
    HTTP error (403 = rate limited) raises, so a caller can tell the two."""
    import urllib.request
    req = urllib.request.Request(API.format(lcsc=lcsc),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("result") or {}) if data.get("success") else {}


RATE_LIMIT_CODES = (403, 429)
# The first rate-limit reason this process met, or None. Module state on
# purpose: bom_cpl and cpl_verify run in one dfm_check process and must
# share it, so the second does not hammer an endpoint the first was refused by.
_RATE_LIMITED: str | None = None


def rate_limited() -> str | None:
    """Why EasyEDA fetches stopped in this process, or None if they did not."""
    return _RATE_LIMITED


def reset_rate_limit() -> None:
    global _RATE_LIMITED
    _RATE_LIMITED = None


def classify(exc: BaseException) -> tuple[int | None, str]:
    """(HTTP status or None, a one-line reason) for a failed fetch."""
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status", None)
    if not isinstance(code, int):
        m = re.search(r"HTTP Error (\d{3})", str(exc))
        code = int(m.group(1)) if m else None
    if code in RATE_LIMIT_CODES:
        return code, f"HTTP {code} from EasyEDA (rate limited)"
    if code is not None:
        return code, f"HTTP {code} from EasyEDA"
    return None, f"network error: {type(exc).__name__}: {exc}"


def cache_path(lcsc: str, cache_dir: Path) -> Path:
    return Path(cache_dir) / f"{lcsc.strip().upper()}.json"


def get(lcsc: str, cache_dir: Path | None = None, fetch: bool = True,
        pace_s: float = 0.0, fetcher=None,
        errors: dict | None = None) -> Model | None:
    """The part's model from cache, else (fetch=True) from EasyEDA, cached.
    A part the API has no footprint for returns None and is not cached, so a
    later run asks again. A fetch that RAISES (network, 403) still returns
    None, but when `errors` is given it records `errors[lcsc] = "<why>"`, so a
    caller can tell "could not ask" from "EasyEDA has nothing". Once a 403/429
    has latched the rate limit, an uncached part is not fetched at all: it
    returns None with the skip recorded the same way."""
    global _RATE_LIMITED
    lcsc = (lcsc or "").strip().upper()
    if not lcsc:
        return None
    path = cache_path(lcsc, cache_dir or DEFAULT_CACHE)
    if path.is_file():
        return parse(json.loads(path.read_text(encoding="utf-8")))
    if not fetch:
        return None
    if _RATE_LIMITED:
        if errors is not None:
            errors[lcsc] = f"not fetched: {_RATE_LIMITED} earlier in this run"
        return None
    if pace_s:
        time.sleep(pace_s)
    try:
        raw = (fetcher or _fetch_raw)(lcsc)
    except Exception as exc:  # noqa: BLE001 - reported, never "no model"
        code, why = classify(exc)
        if code == 404:
            return None  # EasyEDA answered: it has no such part
        if code in RATE_LIMIT_CODES:
            _RATE_LIMITED = why
            print(f"easyeda: {why} at {lcsc}; skipping every further model "
                  f"fetch this run - rerun once the limit clears (minutes)",
                  file=sys.stderr)
        if errors is not None:
            errors[lcsc] = why
        return None
    if not raw.get("packageDetail"):
        return None
    cached = trim(raw, lcsc)
    if not cached["pads"]:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cached, indent=1), encoding="utf-8")
    return parse(cached)
