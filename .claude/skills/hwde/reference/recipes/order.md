# order - quote, approve, and place the fab order

Real money, no sandbox. Every safety property here exists because the failure is
expensive and silent: a board fabbed from a design that is not the one that
passed the gates.

## Preconditions are the point

The router blocks this recipe unless `dfm` is passed AND hash-fresh AND
unmarked, and no issues are open. "Passed" alone is not enough: a gate result
recorded before the last edit is provenance, not verification. If `state.py
resume` reports `gates_stale` or `gates_freshness_unknown`, re-run those gates
first - that is the whole reason freshness exists.

Phase is workflow position, never a release certificate (codex C1). `state.py
resume` also reports `release_disposition` - the DERIVED disposition (draft,
engineering-validated, release-candidate, order-ready, ordered, built,
bring-up-passed, derated, rework-required, blocked). Only a valid release
attestation confers order-ready, and ordering consumes ONLY that attestation.

## The sequence

1. `report_gen.py` - the design doc is what the human approves against.
2. `order_quote.py` - the quote matrix. `estimated: true` on every figure:
   transcribed headline prices, no panelization, no promotions. The JLC cart is
   the only real quote.
3. `attest.py build --workspace <ws>` - the release attestation (U5). It
   refuses with the FULL problem list unless: every applicable pipeline gate
   (erc, place, drc_routed, verify, dfm, plus sim when kicad/sims exists) is
   recorded pass AND hash-fresh; no open issues; H4 explicitly approved;
   strict `verify_release`/`dfm_release` reports (verify_all --strict /
   dfm_check --strict, saved to reports/verify_release.json and
   reports/dfm_release.json) are stamped, current against the pcb, and pass
   under DURABLE waivers (each waiver binds artifact hash + checker version +
   expiry); the fab package (zip, BOM, CPL, BOM-full) is present; and the
   copper weight derives from architecture/stackup.md. Fix what it lists -
   never route around it. The attestation is immutable and self-sealed; ANY
   bound-input change invalidates it (`attest.py verify` re-checks).
4. `order_submit.py` - assembles the manifest, the fab package paths, and the
   `human_steps` list. A governed workspace (state.json present) whose
   attestation is missing/invalid gets `not_order_ready` and the API legs
   refuse before any network call. With `HWDE_JLCPCB_*` credentials, `--api`
   adds upload + the API's own DFM audit + a real `calculate` (a
   `scope_pending` response is normal) - at the ATTESTED manufacturing
   options. A `--copper-oz` that contradicts the attested value REFUSES
   instead of waiving: that override is exactly how pd-trigger shipped 1 oz
   copper under a 2 oz design basis.
5. **H5, always blocking.** Present: the quote row, the API audit findings beside
   the estimate, `order.json`, and the human steps (upload the zip, JLCDFM second
   opinion, eyeball the CPL polarity preview for polarized parts, pay).

## Creation

Only AFTER approval, and only the ORCHESTRATOR - never an agent:

    order_submit.py --api-create --confirm "<board> <N>pcs <grand total>"

The confirm string must carry the grand total INCLUDING freight exactly as
`api_quote.json` records it. One latched order per workspace, bound to the
normalized design hash AND to the attestation: the on-disk package must be the
attested design, and the quoted copper weight must equal the attested one.
A changed design refuses rather than ordering the wrong board. Payment itself
is never automated.

Four-layer boards are the WEB path: JLC's Open API refuses them (unclassified
code 2 on `pcb/create` while `calculate` accepts them), so `--api-create` guards
on layer count and tells you to order in the browser. Record the resulting
web order number in `fab/order.json` by hand - nothing else will.

## Assembly

There is no PCBA API. BOM/CPL ordering is the JLC web flow, end of story.

## Do not

- Do not re-export the fab package between approval and creation. The latch
  binds to the design hash of what was approved.
- Do not skip the CPL polarity preview because dfm passed. They are different
  checks: ours reads pad geometry, theirs renders what the machine will place.
