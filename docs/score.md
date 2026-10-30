# The score

`bin/hwde score` measures what this checkout of hwde finds wrong with real
boards held fixed. The boards stay the same between measures, and hwde is the
one thing that changes, so a better hwde moves the number. The person's test
for the module is "build the hifi board, design new tools, work on them, see
how that improves the board" (hive-hwde chat, 2026-10-28 10:18). This page
is the definition. It is written once: a change to it is a change to what
the Hive measures, and needs to be argued for as one.

## The boards

Each board is a pin, `pinned/<board>.json`:

- **lite-main** and **core**: the hi-fi receiver's boards, copied from
  hifi-receiver-2's `boards/<board>` at the commit the pin names, into
  `pinned/<board>/`. The copy leaves out what no gate reads: the fab
  package (except `fab/CPL.csv` and `fab/cpl_visual.json`, which dfm checks
  against), renders and logs. Further boards (ultra-front) are added the
  same way once they land.
- **hwde-led-test**: this repo's own board, `boards/hwde-led-test`, where it
  is.

`bin/score.py` holds the sha256 of every pin and its files (`PINS`). A board
whose files don't match it stops the score and fails the test. A pinned board
changes only through `bin/hwde pin <board> --from <clone> --commit <sha>`,
which copies the board and writes the digests into `bin/score.py`. The commit
that does that names the hifi-receiver-2 commit. Because it changes the
score's own file, the score starts a new baseline there instead of reading
the refresh as a gain or a loss. `bin/hwde pin --digests` re-stamps the
digests alone, after a pin's scripts are classified or this repo's board
changes.

## What is charged

For every pinned board, a scratch copy goes through `erc`, `drc_routed`,
`verify` and `dfm`, the same `bin/hwde gate` runs `check-all` makes. Every
shim in `bin/hwde` is off (`HWDE_NO_SHIMS`), so a gate gets no help hwde
doesn't ship. One charge is:

1. each finding a gate fails on that no waiver covers (the board's own
   `reports/verify-waivers.json`, applied by the gate as always);
2. each warning a gate reports that no waiver in that file covers;
3. each board-local script listed in the pin under `stand_in`: a script in
   the board's files that does what an hwde script should (a router, a
   via fixer, a zone refill), with the gap it fills. A script under
   `design` is the board's own source (its schematic generator, placement
   plan, plane plan, build recipe) and costs nothing. A `.py` or `.sh` the
   pin doesn't classify is listed under `unclassified` and charged until it
   is.

A gate that can't run (exits other than pass or fail, or writes no result)
charges 100 (`CRASH`), so breaking a gate never hides its findings.

Each planted fault a gate or check lets through charges 100 too (next
section).

The score is minus the charges. Higher is better and 0 is a clean sweep. The
output gives one line per board (the charges per gate, by check, and the
stand-in scripts), then `charges: N`, then the number.

## The planted faults

The charges come from hwde's own checks, so a check made looser finds less
and the score would rise with nothing on the boards better. Before the
boards, `bin/strength.py` runs faults planted where each gate's verdict comes
from, through the same code the gates run, offline, in seconds:

- **gate**: each gate's criteria in `reference/gates.yaml` (`gate.evaluate`)
  must fail each real finding of a kind it fails on (errors, and for erc and
  drc_routed warnings too), put alone among a real report's own. erc's and
  drc_routed's are KiCad's, kept in `tests/golden/gates/` with the runs they
  came from; verify's and dfm's are the findings their checks raise on the
  verify and dfm faults below, as they raise them today. Nothing in a planted
  report says it is planted, so `gate.py` can let one through only by
  skipping that finding on every board too.
- **verify**: each mutant in `tests/golden/` through the check that owns it.
  Every check in the verify suite must own one, and a mutant whose check
  left the suite is a miss, so dropping a check or its fault costs too.
- **dfm**: the bench's known-answer fixture (`tests/fixtures/stages/`) and
  holes planted at JLC's floors in a shipped drill file: a 0.45 mm gap
  between holes (floor 0.5 mm) and a 0.25 mm drill (floor 0.3 mm).

Each fault let through charges 100, as a gate that can't run does: a check
that misses the fault it was built for isn't measuring the boards, and one
loosened check costs more than the findings it could hide. If the faults
can't run, the score stops instead of printing a number. A new check comes
with its own mutant, which also lets the scorecard see it. The faults and the
corpus are in the score's paths (`.hive/project.json`), so a change to them
starts a new baseline rather than reading as a gain. So are `gate.py` and
`reference/gates.yaml`, where the gates' verdicts come from: a change to
either is the person's to merge. The checks themselves (the rest of the
skill's `scripts/`) are not: the Hive scores a merge that changes the
score's paths against its parent with the merge's copy of those paths laid
over it, so a loosened check there would cost both sides the same and read
flat, and a better one could never read as a gain. ERC and DRC themselves
are KiCad's and have no planted faults here: a rule set to "ignore" in a
board's project, or a looser `rules_gen` floor, still gets through.

## What moves it

- A fix in hwde that clears a finding on a pinned board (a check that stops
  misreading a footprint, a gate that stops flagging a correct track) raises
  it at once. Nothing has to be re-run or written by hand.
- A new check that finds a real defect on a pinned board lowers it. That is
  the board's defect, newly seen, and the account of the change should say
  so for the judge.
- A board that drops a stand-in script, because hwde now does that job, is
  re-pinned at the commit that dropped it. The re-pin is a new baseline.
- Removing a row from `docs/workarounds.md` changes nothing: the score
  doesn't read that file. The table and its probes still hold the shims and
  hand edits to the truth in the test.

## What it doesn't see

- Hand edits in a board's files (a decoupling entry written by hand, a
  `refdes` added to `parts.json`) are part of the pinned board and aren't
  charged. `docs/workarounds.md` keeps tracking them, with a probe each.
- The board-building steps (placement, routing) aren't re-run: they need
  minutes to hours per board and give a different board each time. A tool
  that would build a better board shows up when the board is rebuilt with it
  in its own project and re-pinned.
- Classifying a script as `design` or `stand_in` is a judgment the pin
  records. A reviewer reads a change to it as a change to the score.

## Cost

Every board, every measure. On 2026-10-29 the three boards took 1.5 min in
all with the machine at load 120-250. At load 380, lite-main alone took
11 min (drc_routed's zone refill 7.5 min of it). Both are inside the 30
minutes a check gets. If the boards grow past that, measure only the boards
whose pinned files, or the skill scripts their gates call, changed since the
last kept measure. A gate that can't run is tried three times, 30 s apart.
If KiCad itself didn't start (`EnvError`, such as kicad-cli's version probe
timing out under load), the score gives no number rather than charge hwde
for the machine.
