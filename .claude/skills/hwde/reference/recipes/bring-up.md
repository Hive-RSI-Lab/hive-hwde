# bring-up - a board in hand, measured, and closed with the numbers

The boards have arrived (`state.py log --event boards_received`). Bring-up
answers one question with measurements: does this board do what the brief
asked, inside its limits? The person's ask (10-28) is that they plug it in,
run one script, leave it, and hear back when it is ready - so run as much as
the board allows without them (reference/remote-bringup.md).

## Before power: the plan

Write `bringup/plan.md` (or the project's bench config) with one item per:

- first-spin finding whose disposition is `contingency` (the reading that
  decides whether to fit the fallback) or `justified` on a thin margin;
- `could_stop_board` fact resolved by argument rather than a datasheet;
- rail and status line (`check_observability.py --workspace <ws>`): `self`
  items the board reads itself, `test_point` items a bench reads;
- function the brief promises (each output, interface, input).

Each item: a name, what is read, the limit (min/max with units, or a text),
and who reads it (self, bench, by hand). Order them so nothing that could
damage the board runs before the readings that would warn of it (rails and
self-test before load; quiet levels before loud).

## Running it

- Unattended where the board has an MCU and a network: the project's bench
  provisions it, updates firmware with rollback, runs the self-test, reads
  every rail and status, drives outputs at a quiet level and reads them back
  through the loopback taps. The hive-fw module builds and flashes the
  firmware; the bench lives in the board's project, not here.
- `scripts/bench.py` is NOT a hardware bench: it scores hwde's own pipeline
  stages on frozen fixtures. Do not reach for it here.
- Bench-only items (power into a real load, a thermocouple, a listen) go to
  the person in ONE batch, with exact wiring and what to send back.

## Closing: the results file

Write `bringup/results.json` in the schema `scripts/lib/bringuplib.py`
documents: per item `name`, `measured` (+`unit`), `limit`, `verdict`
(pass / fail / derated / skip), `evidence` (path to the raw data). A
project bench's session.json converts as is when it has `selftests[].items`,
a `verdicts` map and a by-hand `checklist` (hifi-receiver-2's does).

    scripts/state.py bringup --workspace <ws> --results bringup/results.json

- every required item pass -> `bringup_passed`
- any derated (with a note saying to what) -> `bringup_derated`
- any fail -> `bringup_failed`
- a skip not marked `optional: true` -> refused: measure it, or say why the
  board can be called working without it.

The event records the file's path and sha256. `attest.py disposition`
then derives `bring-up-passed` / `derated` / `rework-required`; a later
bring-up (after rework) supersedes an earlier one. `state.py log --event
bringup_passed` without `--data {"results": ...}` is refused: a bare event
is not evidence.

On `bring-up-passed`: `knowledge.py --prove --workspace <ws> --dry-run`,
then without `--dry-run` - the records that applied become `proven`,
citing the results file.

## What to tell the person

Ready for use: one paragraph and the results page. Otherwise: which item
failed, its reading against its limit, what that means, and the fix - the
contingency the board carries, a firmware change, or a rework (a
`fix-finding` / `swap-part` run for the next spin). Append what the board
taught to the project's LEARNINGS (dated, one claim per heading): a failure
the first-spin review missed is a class for reference/first-spin.md and,
where a check could catch it, a tool for hive-hwde.
