# First spin: a board that will work, with contingencies

The person, 10-28 22:05: "i give the spec, you design a product that will
def work with appropriate contingencies, etc, as needed, then i order it, it
arrives, i plug it in, do the minimal possible on my end, and leave it and
then you ping me when it's ready for use". 22:09: "it needs to like
definitely work ... with as many thorough engineering checks/reviews and
writeups as needed. Like not to the point of diminishing returns cuz
everything has certain margin."

This page is how a board gets there: before the order, a review that works
out every margin with a number; the contingencies built in where a margin
can't be closed on paper; the board able to see itself (self-observation.md);
and a bring-up the Hive runs on its own (remote-bringup.md). It is the
default for any board that will be built, scaled to the board by the agent
running it, not a gate: a two-rail blinky gets one reviewer and a short
list; a product with a power stage, an amplifier and a radio gets a reviewer
per domain. Say in a decision (`state.py decision`) how far you took it.

It generalises hifi-receiver-2's first-spin review (task 261029-020622,
`docs/review-2/` in that project): six domain reviewers on two boards found
6 blockers and 21 majors that hwde's gates had not caught, and listed
248 known unknowns. Every one of them is a
class of mistake worth knowing (the table at the end).

## When

After the verify gate passes and before the order (P8-P9), on the board as
it will be built; again after any fix that changes a block. Reviews of the
schematic at P4 (`schematic-reviewer`) and the routed board at P8
(`verify-reviewer`) are still the in-pipeline checks; the first-spin review
is the domain-by-domain engineering pass that decides whether to spend the
person's money.

## The review: one reviewer per domain, fresh eyes

Spawn `agents/domain-reviewer.md` once per domain the board has (tier
fable/high; opus/high for parts and DFM). The usual domains:

| Domain | What it works out |
|---|---|
| power | every rail's source, protection, inrush (FET SOA), hold-off at every input step, regulator loop, TVS clamp vs downstream abs-max, the current path's copper and vias |
| analog / audio | headroom at every stage (common-mode, output swing on its real supply), gain staging to clip, input coupling corners, switch thresholds at the logic levels driving them |
| digital | straps and reset states at power-on and on an MCU-only reset (internal pulls of every attached IC), clocks against their limits, firmware support for every interface, antenna keep-out |
| thermal | Tj at the stated load for every hot part from the datasheet's own theta (pad-up packages need a heatsink path, not vias), enclosure air |
| DFM / assembly | footprints against the maker's drawing (posts, slots, annulus, edge), paste on exposed pads, finish for the finest pitch, CPL rotation against JLC's model |
| parts | pin-to-pad against the datasheet pin table for every IC and connector, stock and lifecycle, ratings |

Give each the same brief (the role prompt carries the rules): the board
files, the product's requirements, its domain, and where to write. Reviewers do not read
the design's own conclusions first (README, architecture notes, review
files, LEARNINGS, generator comments): they read the netlist, the board and
the vendor datasheets, and form their own.

The rules every reviewer keeps:

- Every finding carries a number: a calculation with its inputs, a
  simulation (`sim_run.py` testbench beside the review, so it re-runs),
  a datasheet table with its document, revision and page, or a measured
  board geometry. "Looks fine" is neither a finding nor a pass.
- Every block passed states its margin ("VDS 30 V rated, 24.5 V worst case:
  18 %").
- Severity: **blocker** (won't work, gets damaged, can't be assembled),
  **major** (works on the bench, fails in a real condition: a charger, a
  load, temperature, a firmware path), **minor** (thin but positive margin,
  or a cost/assembly risk), **note**.
- Each blocker and major has a fix specific enough to do (part, value, net,
  LCSC code). Where the fix is uncertain, propose a contingency built into
  the board (below).
- Every fact the domain relies on is a **known unknown** until checked:
  verified (source, date), unverified, or wrong; `could_stop_board` marks
  the ones that matter. Be exhaustive there.
- A check hwde has no tool for goes under **tools wanted**: what it would
  check and why. Those become hive-hwde briefs (`hive agent queue`): this is
  how the module learns from every board.

Each writes `reports/first-spin/<domain>.md` (the review, findings first)
and `reports/first-spin/<domain>.json` in the schema `first_spin.py`
documents.

## Dispositions: every finding answered

One worker (the run's own, not the reviewers) takes all the findings,
makes the fixes once, and writes `reports/first-spin/dispositions.json`:
each finding's id with one of

| Disposition | Means |
|---|---|
| fixed | changed on the board; `how` says what and the margin after |
| contingency | the board carries a fallback (DNP footprint, 0R, jumper) and bring-up decides; `how` names it and the bring-up item that decides |
| firmware | a duty firmware must carry (a governor, a volume cap, a pin held low before reset); `how` names the requirement in the project's firmware doc |
| justified | left as is; `how` states the margin, with its number |
| order | an ordering choice (finish, via fill, assembly side) |
| enclosure | for the enclosure task |

`scripts/first_spin.py --workspace <ws>` reads the reviews and the
dispositions, lists what is still open (a blocker or major with no
disposition, a disposition with no `how`, a `could_stop_board` fact not
verified), merges the tools wanted, and writes the dispositions table
(`--md`). Exit 1 while anything is open. A board goes to the order with
nothing open, or with the open items in the H4 packet as the person's call.

Re-run the gates after the fixes, then re-run the domain reviews whose
blocks changed (a fresh reviewer reads the fix, as `fix-check.md` did on
hifi-receiver-2).

## Contingencies: cheap fallbacks the board carries

Where a margin can't be closed on paper (a part's behaviour the datasheet
does not pin down, an antenna in a box, a loop that may want another pole),
put the way out on the board while it costs a footprint:

- **DNP footprint** beside the risk: a second bulk cap, an RC snubber, a
  ferrite in place of a 0R, a pull-down on an enable.
- **0R links** that split a rail or a block from the rest, so bring-up can
  isolate it, measure its current, or feed it from the bench.
- **Solder jumpers** for straps, addresses and alternate pin uses.
- **Footprint-compatible alternates** chosen up front (ESP32 WROVER-E/-IE:
  the same footprint with an external antenna if the box blocks RSSI).
- **Test points** on every rail and every signal a fault would be read on
  (they are also what the observability report counts).
- **Spare MCU pins** to pads, and a header for the console even when USB
  carries it.
- **Assembled count**: order five boards, assemble two or three; a rework on
  a bare board is cheaper than another spin.

Each contingency is a disposition and a bring-up item: what reading tells
bring-up to fit it.

## What first spins have caught (hifi-receiver-2, 2026-10-29)

Classes, so the next reviewer looks: (a tool that catches the class is
named; "none" is a tool wanted)

| Class | Example | Caught by |
|---|---|---|
| I/O expander internal pull-ups fighting external pull-downs until firmware runs | TCA9555 enables floating at 1.1-1.65 V, a low-Vth FET turning PVDD on | none (strap/reset-state checker wanted) |
| strap pin seeing an oscillator at reset | GPIO0 fed a 50 MHz RMII clock whose OE floated | none |
| inrush past the pass FET's SOA | 940 uF charged in 12 ms | sim_run testbench by hand (soa_check wanted) |
| Miller turn-on of a P-FET at every input step | PVDD following VBUS on a 5->20 V PD step, 20 A inrush | sim_run testbench by hand |
| pad-up (top-slug) package with no heatsink path | TPA3221 DDV with no EP and no heatsink | none (check_thermal top-slug mode wanted) |
| op-amp common-mode / swing on a single supply | OPA1678 at 0 dBFS past V+ - 2 V | none |
| analog switch VIH at 5 V from 3.3 V logic | TS5A23157 select | none |
| enable pin input current through a large resistor | SDZ through 100k below VIH at the datasheet's IIH | sim_board i2c/enable screens partly |
| footprint missing the maker's locating posts | RCA jack, PLR237 T8 vs T10 lead form | none (drawing reader wanted) |
| annulus below the fab minimum on a maker's pattern | USB4085 0.40 drill in 0.65 pad | dfm gate |
| no antenna keep-out | ESP32 antenna over four copper layers | none (check_antenna wanted) |
| high-current path in thin tracks and single vias | 5 A in 0.255 mm and one via per layer change | check_current (series path walk wanted) |
| HASL on 0.4-0.5 mm pitch | five QFNs and an FFC | none |
