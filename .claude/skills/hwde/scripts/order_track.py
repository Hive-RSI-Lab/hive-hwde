#!/usr/bin/env python
"""order_track.py - poll one JLCPCB API order and diff against the last poll.

P10 follow-up for orders placed through order_submit.py --api-create. One
invocation = one fetch (scheduling/watching is EXTERNAL - this script never
loops): calls pcb/order/detail for the recorded batchNum, plus pcb/wip/get
when an orderUUID is visible (production steps exist only after the factory
starts), normalizes to a compact tracking record, writes fab/tracking.json,
and reports `changed` + a one-line `change_summary` (the caller's notification
text) computed against the PREVIOUS tracking.json. The first ever fetch counts
as changed ("initial: <status>").

Status enum is OPEN by contract (official examples show undocumented code 7):
known codes {0 Cancelled, 1 Pending Review, 2 Awaiting Confirmation,
3 Confirmed, 4 Submitted to factory, 5 Shipped}; anything else labels as
"unknown(<n>)". tracking_number is null-tolerant - the PCB order/detail
tracking surface is a documented unknown (TDP's expressNo equivalent).

Credentials: HWDE_JLCPCB_APPID / HWDE_JLCPCB_KEY / HWDE_JLCPCB_SECRET.

CLI:
  order_track.py --workspace ~/dev/boards/<name> [--batch NUM] [--out track.json]
Exit 0 ok / 1 order-level problem (cancelled) / 2 error (no batch recorded,
API/business failure, transport).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
from checklib import CheckError  # noqa: E402
import jlcapi  # noqa: E402
import safelib  # noqa: E402

STATUS_LABELS = {0: "Cancelled", 1: "Pending Review",
                 2: "Awaiting Confirmation", 3: "Confirmed",
                 4: "Submitted to factory", 5: "Shipped"}
# Keys probed (in order) for a tracking number - the PCB surface is unknown,
# so accept the TDP name and plausible variants; absent -> null.
_TRACKING_KEYS = ("expressNo", "expressNumber", "trackingNumber", "trackingNo")


def status_label(code) -> str | None:
    if code is None:
        return None
    try:
        code = int(code)
    except (TypeError, ValueError):
        return f"unknown({code})"
    return STATUS_LABELS.get(code, f"unknown({code})")


def _find_key(obj, key: str):
    """First value for `key` anywhere in a nested dict/list, else None."""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for v in obj.values():
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_key(v, key)
            if hit is not None:
                return hit
    return None


def _make_session():
    """Split out so tests can monkeypatch a fake session in."""
    try:
        return jlcapi.session_from_env()
    except jlcapi.JlcApiError as exc:
        raise CheckError(str(exc)) from exc


def _batch_from_order_json(fab_dir: Path) -> str:
    order_json = fab_dir / "order.json"
    if order_json.exists():
        try:
            man = json.loads(order_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CheckError(f"{order_json} is not valid JSON: {exc}") from exc
        api = man.get("api") or {}
        batch = (api.get("order") or {}).get("batchNum") or man.get("order_number")
        if batch:
            return str(batch)
    raise CheckError(
        "no API order recorded - fab/order.json has no api.order.batchNum "
        "(run order_submit --api-create first, or pass --batch)")


def _items(data) -> list[dict]:
    rows = []
    entries = data.get("orderItem") if isinstance(data, dict) else None
    for ent in entries if isinstance(entries, list) else []:
        if not isinstance(ent, dict):
            continue
        pcb = ent.get("pcbItem") if isinstance(ent.get("pcbItem"), dict) else {}
        code = pcb.get("orderStatus", ent.get("orderStatus"))
        rows.append({"order_type": ent.get("orderType"),
                     "status_code": code,
                     "status_label": status_label(code)})
    return rows


def _wip_steps(session, order_uuid) -> tuple[list[dict], str | None]:
    if not order_uuid:
        return [], None
    try:
        resp = session.wip(str(order_uuid))
    except jlcapi.JlcApiError as exc:
        return [], f"wip fetch failed: {exc}"
    if not resp.get("ok"):
        return [], (f"wip unavailable ({jlcapi.classify(resp)}): "
                    f"{resp.get('message')}")
    steps = resp.get("data")
    out = []
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, dict):
            out.append({"step": step.get("technicsProcessName"),
                        "begin_time": step.get("beginTime")})
    return out, None


def _same_code(a, b) -> bool:
    """Status codes normalized to str before compare: int 5 == "5"."""
    if a is None or b is None:
        return a is None and b is None
    return str(a) == str(b)


def _diff(prev: dict | None, cur: dict) -> tuple[bool, str]:
    label = cur["status_label"] or "status unknown"
    if prev is None:
        extra = f"; tracking {cur['tracking_number']}" \
            if cur["tracking_number"] else ""
        return True, f"initial: {label}{extra}"
    changes = []
    if not _same_code(prev.get("status_code"), cur["status_code"]):
        changes.append(f"status: {prev.get('status_label') or 'unknown'}"
                       f" -> {label}")
    if prev.get("tracking_number") != cur["tracking_number"]:
        changes.append(f"tracking: {prev.get('tracking_number') or 'none'}"
                       f" -> {cur['tracking_number'] or 'none'}")
    # wip compared by CONTENT (step name + begin time sequence), not count -
    # a step can be renamed/re-dated without changing the list length
    prev_wip = prev.get("wip_steps") or []
    cur_wip = cur["wip_steps"]
    if prev_wip != cur_wip:
        if len(prev_wip) != len(cur_wip):
            changes.append(f"production steps: {len(prev_wip)}"
                           f" -> {len(cur_wip)}")
        else:
            changes.append(f"production steps updated ({len(cur_wip)})")
    if changes:
        return True, "; ".join(changes)
    return False, f"no change ({label})"


def run(workspace: Path, batch: str | None = None, session=None) -> dict:
    fab_dir = Path(workspace) / "fab"
    if not fab_dir.is_dir():
        raise CheckError(f"no fab/ directory under {workspace}")
    batch = str(batch) if batch else _batch_from_order_json(fab_dir)
    session = session or _make_session()

    detail = session.order_detail(batch)
    cls = jlcapi.classify(detail)
    if cls != "ok":
        raise CheckError(
            f"order/detail failed ({cls}): {detail.get('message')}"
            + (f" - {jlcapi.REMEDIATION[cls]}" if cls in jlcapi.REMEDIATION
               else ""))
    data = detail.get("data") or {}

    items = _items(data)
    status_code = next((i["status_code"] for i in items
                        if i["status_code"] is not None), None)
    if status_code is None:
        status_code = _find_key(data, "orderStatus")
    order_uuid = _find_key(data, "orderUUID")
    wip_steps, wip_note = _wip_steps(session, order_uuid)
    tracking_number = None
    for key in _TRACKING_KEYS:
        tracking_number = _find_key(data, key)
        if tracking_number is not None:
            break

    tracking_json = fab_dir / "tracking.json"
    with safelib.writer_lock(tracking_json, what="tracking.json"):
        return _finish(fab_dir, tracking_json, batch, detail, data, items,
                       status_code, wip_steps, wip_note, order_uuid,
                       tracking_number)


def _finish(fab_dir, tracking_json, batch, detail, data, items, status_code,
            wip_steps, wip_note, order_uuid, tracking_number) -> dict:
    prev = None
    if tracking_json.exists():
        try:
            prev = json.loads(tracking_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = None                      # corrupt previous -> fresh diff
        if not isinstance(prev, dict):
            prev = None                      # parses but wrong shape = corrupt

    try:
        cancelled = int(status_code) == 0
    except (TypeError, ValueError):
        cancelled = False
    payload = {
        "script": "order_track",
        "status": "violations" if cancelled else "pass",
        "batch_num": batch,
        "status_code": status_code,
        "status_label": status_label(status_code),
        "items": items,
        "wip_steps": wip_steps,
        "wip_note": wip_note,
        "order_uuid": order_uuid,
        "tracking_number": tracking_number,
        "total_money": _find_key(data, "totalMoney"),
        "trace_id": detail.get("trace_id"),
        "fetched_at": _dt.datetime.now().astimezone()
        .isoformat(timespec="seconds"),
    }
    payload["changed"], payload["change_summary"] = _diff(prev, payload)
    # atomic write (U12 safelib): unique temp + fsync + os.replace, under
    # the tracking.json writer lock taken by run()
    safelib.atomic_write_json(tracking_json, payload)
    payload["tracking_json"] = str(tracking_json)
    return payload


def main(argv: list[str] | None = None) -> int:
    checklib.utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True,
                    help="board workspace (contains fab/order.json)")
    ap.add_argument("--batch", help="batchNum override (default: from "
                                    "fab/order.json api.order.batchNum)")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args(argv)

    try:
        payload = run(Path(args.workspace), batch=args.batch)
    except jlcapi.JlcApiError as exc:
        print(json.dumps({"script": "order_track", "status": "error",
                          "error": f"transport: {exc}"}))
        return 2
    except Exception as exc:  # noqa: BLE001 (contract: any error -> exit 2)
        print(json.dumps({"script": "order_track", "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2

    text = json.dumps(payload, indent=1)
    if args.out:
        try:
            safelib.atomic_write_text(Path(args.out), text)
        except OSError as exc:
            # --out failure must not break the exit contract (checklib.emit
            # would traceback): surface the payload on stdout, exit 2.
            payload["out_write_error"] = f"cannot write {args.out}: {exc}"
            print(json.dumps(payload, indent=1))
            return 2
    else:
        print(text)
    return {"pass": 0, "violations": 1}.get(payload.get("status"), 2)


if __name__ == "__main__":
    raise SystemExit(main())
