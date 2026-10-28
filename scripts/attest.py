#!/usr/bin/env python
"""attest.py - release attestation build/verify + derived disposition (U5).

Codex C1: workflow phase is not a release certificate. This CLI issues and
checks the certificate (lib/releaselib.py): an immutable, self-sealed
manifest at <workspace>/fab/attestation.json binding the normalized design
hashes, the strict release-gate reports (verify_release/dfm_release, with
their U2 coverage matrices), the durable waivers, the fab package hashes,
the manufacturing options and the recorded human approvals. Order-shaped
code (order_submit.py, the `order` verb) consumes ONLY this attestation.

Subcommands:
  build        Assemble + write the attestation. Refuses (exit 1, nothing
               written) unless EVERY precondition holds - all applicable
               pipeline gates recorded pass and hash-fresh, no live issues,
               human checkpoint 4 approved, both strict release reports
               present/valid/passing under durable waivers, fab package
               complete, manufacturing options derivable. The refusal lists
               every miss at once. When a valid attestation already exists
               the build is a no-op (immutability); --force reissues.
  verify       Re-verify the recorded attestation against the current tree
               and state. Read-only. Exit 0 valid / 1 invalid.
  disposition  Print the DERIVED release disposition (never hand-set):
               draft, engineering-validated, release-candidate, order-ready,
               ordered, built, bring-up-passed, derated, rework-required,
               blocked. Read-only, always exit 0 unless an error.

CLI (SPEC 6): JSON to stdout or --out; exit 0 ok / 1 refused-or-invalid /
2 operational error. Toolchain-free: build validates recorded reports, it
never runs kicad-cli.

  attest.py build --workspace DIR [--gates FILE] [--max-report-age-h H]
                  [--force] [--out FILE]
  attest.py verify --workspace DIR [--out FILE]
  attest.py disposition --workspace DIR [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import checklib  # noqa: E402
import releaselib  # noqa: E402

SCRIPT = "attest"


def run(argv=None) -> tuple[dict, str | None, int]:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--workspace", required=True,
                       help="board workspace (state.json inside)")
        p.add_argument("--out", help="write result JSON here too")

    p = sub.add_parser("build", help="assemble + write fab/attestation.json")
    common(p)
    # NO criteria override exists: build and verify both read the canonical
    # gates.yaml (U5 adversarial review - a doctored criteria file could
    # attest a failing strict report and leave no trace in the manifest)
    p.add_argument("--max-report-age-h", type=float, default=24.0,
                   help="staleness bound for the strict release reports "
                        "(default %(default)s)")
    p.add_argument("--force", action="store_true",
                   help="reissue even when the existing attestation still "
                        "verifies valid")

    common(sub.add_parser("verify",
                          help="re-verify the attestation (read-only)"))
    common(sub.add_parser("disposition",
                          help="derived release disposition (read-only)"))

    args = ap.parse_args(argv)
    ws = Path(args.workspace)
    result: dict = {"script": SCRIPT, "cmd": args.cmd,
                    "workspace": str(ws).replace("\\", "/")}

    if args.cmd == "build":
        att, problems = releaselib.build(
            ws, max_report_age_h=args.max_report_age_h)
        if att is None:
            result.update(status="violations", problems=problems,
                          attestation=None)
            return result, args.out, 1
        if not args.force:
            existing = releaselib.verify(ws)
            if existing.get("valid"):
                result.update(status="pass", action="unchanged",
                              rev=existing.get("rev"),
                              attestation_sha256=existing
                              .get("attestation_sha256"))
                return result, args.out, 0
        path = releaselib.write_attestation(ws, att)
        result.update(status="pass",
                      action="reissued" if att.get("supersedes") else "issued",
                      attestation=str(path).replace("\\", "/"),
                      rev=att["rev"], board=att["board"],
                      attestation_sha256=att["attestation_sha256"],
                      disposition=releaselib.disposition(ws))
        return result, args.out, 0

    if args.cmd == "verify":
        v = releaselib.verify(ws)
        result.update(status="pass" if v["valid"] else "violations", **v)
        return result, args.out, 0 if v["valid"] else 1

    # disposition
    result.update(status="pass", **releaselib.disposition(ws))
    return result, args.out, 0


def main(argv=None) -> int:
    checklib.utf8_stdout()
    try:
        payload, out, code = run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001  (SPEC 6: any error -> exit 2)
        print(json.dumps({"script": SCRIPT, "status": "error",
                          "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(payload, indent=1)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
    print(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
