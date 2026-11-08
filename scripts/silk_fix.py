"""silk_fix - clear the silk findings DRC reports: hide the field or delete the footprint silk each one names.

  hwde silk_fix --pcb B.kicad_pcb [--drc report.json] [--dry-run]
                [--out-report r.json]

For every silk_overlap / silk_over_copper / silk_edge_clearance finding it
takes the silk side of the finding:
  - "Reference field of R7" / "Value field of R7"   -> place_edit set_text
    hide (the assembly drawing keeps the refdes);
  - "Segment of R7 on F.Silkscreen" (any graphic)    -> place_edit silk_clear
    on that part and layer, matched by the item's uuid from the report, or
    by the finding's position (`at`) when the report has no uuid.
Silk that belongs to no footprint (board text, board graphics) and footprint
text other than the two fields are left and listed: move those by hand
(place_edit move_text, or the board's own gen step).

Without --drc it runs DRC itself (kicad-cli). After applying it runs DRC
again and reports the silk count before and after and every other count
before and after: status pass when the silk findings are gone and nothing
else moved, violations otherwise. --dry-run prints the ops and stops.

Replaces the board-local scripts silk_clean.py (pinned core). The two
fixed-rule silk scripts are place_edit silk_clear ops: silk_clear.py is
`{"over_pads": true}`, silk_edge.py `{"edge_mm": 0.25}`.

Run silk_place first - moving a refdes to a legal spot keeps it on the
board; silk_fix is for what silk_place could not place.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import checklib  # noqa: E402
import env  # noqa: E402
import place_edit  # noqa: E402
from checklib import CheckError  # noqa: E402

SILK_CHECKS = ("silk_overlap", "silk_over_copper", "silk_edge_clearance")
_FIELD = re.compile(r"^(Reference|Value) field of (\S+)")
_GRAPHIC = re.compile(r"^(\w[\w ]*?) of (\S+) on ([FB])\.Silkscreen")
_TEXTISH = ("Text", "Text Box", "Field")


def _violations(doc: dict) -> list[dict]:
    """A kc.run_drc / hwde drc report ({violations}) or a gate report
    ({failing}) -> its violation list."""
    for key in ("violations", "failing"):
        if isinstance(doc.get(key), list):
            return doc[key]
    raise CheckError("DRC report has no 'violations' or 'failing' list")


def silk_findings(violations: list[dict]) -> list[dict]:
    return [v for v in violations if v.get("check") in SILK_CHECKS]


def plan(violations: list[dict]) -> tuple[list[dict], list[str]]:
    """Silk findings -> (place_edit ops, items left for a hand fix)."""
    hide: dict[tuple, dict] = {}
    clear: dict[tuple, set] = {}       # (ref, layer) -> uuids
    at_ops: list[dict] = []
    left: list[str] = []
    seen_at: set = set()
    for v in silk_findings(violations):
        for it in v.get("items", []):
            msg = it.get("msg", "")
            m = _FIELD.match(msg)
            if m:
                field = m.group(1).lower()
                hide[(m.group(2), field)] = {
                    "op": "set_text", "ref": m.group(2), "field": field,
                    "hide": True}
                continue
            m = _GRAPHIC.match(msg)
            if m and m.group(1) not in _TEXTISH:
                ref, layer = m.group(2), f"{m.group(3)}.SilkS"
                if it.get("uuid"):
                    clear.setdefault((ref, layer), set()).add(it["uuid"])
                elif it.get("pos"):
                    key = (ref, layer, tuple(it["pos"]))
                    if key not in seen_at:
                        seen_at.add(key)
                        at_ops.append({"op": "silk_clear", "ref": ref,
                                       "layer": layer,
                                       "at": list(it["pos"])})
                else:
                    left.append(f"{v['check']}: {msg} (no uuid or position)")
                continue
            if "Silkscreen" in msg:     # board silk, or a footprint's text
                left.append(f"{v['check']}: {msg}")
    ops = list(hide.values())
    ops += [{"op": "silk_clear", "ref": ref, "layer": layer,
             "uuid": sorted(u)} for (ref, layer), u in sorted(clear.items())]
    ops += at_ops
    return ops, sorted(set(left))


def _by_check(violations: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in violations:
        out[v["check"]] = out.get(v["check"], 0) + 1
    return dict(sorted(out.items()))


def run(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pcb", required=True)
    ap.add_argument("--drc", default=None,
                    help="DRC report JSON (else DRC runs here)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the ops, change nothing")
    ap.add_argument("--out-report", default=None)
    args = ap.parse_args(argv)
    pcb = Path(args.pcb)
    if not pcb.is_file():
        raise CheckError(f"board not found: {pcb}")
    import kc
    cli = None
    if args.drc:
        before = _violations(checklib.load_json(args.drc, "DRC report"))
    else:
        cli = kc.resolve_cli()
        before = kc.run_drc(cli, pcb)["violations"]
    ops, left = plan(before)
    payload = {"script": "silk_fix", "board": str(pcb),
               "silk_before": len(silk_findings(before)), "ops": ops,
               "left": left}
    if args.dry_run or not ops:
        payload["status"] = "pass" if not payload["silk_before"] else \
            "violations"
        payload["applied"] = 0
        return payload, args.out_report
    ops = place_edit.validate_ops({"version": 1, "ops": ops})
    payload["results"] = place_edit.apply_ops(pcb, ops)
    payload["applied"] = len(ops)
    cli = cli or kc.resolve_cli()
    after = kc.run_drc(cli, pcb)["violations"]
    others = {k: n for k, n in _by_check(before).items()
              if k not in SILK_CHECKS}
    others_after = {k: n for k, n in _by_check(after).items()
                    if k not in SILK_CHECKS}
    payload.update({
        "silk_after": len(silk_findings(after)),
        "other_before": others, "other_after": others_after,
        "status": "pass" if not silk_findings(after)
        and others == others_after else "violations"})
    return payload, args.out_report


def main(argv: list[str] | None = None) -> int:
    return checklib.cli_wrap("silk_fix", lambda: run(argv))


if __name__ == "__main__":
    sys.exit(main())
