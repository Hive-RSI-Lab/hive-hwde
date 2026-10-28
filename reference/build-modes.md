# Build modes - what a brief declares about SCOPE and about GEOMETRY

A brief may open with a MODE token. A mode answers the "how much board is this"
questions up front so the brief does not have to, and it binds every stage that
would otherwise pad the design out - or squeeze it. It never changes what
correct means.

A mode is two dials, and the caller sets NEITHER directly. The brief names a
TARGET LEARNING OUTCOME and the target derives both:

- **scope tier** - how much board goes around the block under study.
- **binding level** - whether the stated geometry is an INPUT to the design or
  an OUTPUT of it.

plus an optional **stage under study** - the stage the run exists to teach,
which names the deliverable to freeze as a graded bench fixture
(`bench.py --freeze --stage <it>`, then `learn`).

Two dials set independently is how bb-buck went wrong. It ran at a block-only
scope with a size that bound (35 x 25, given by the owner at H1) against a P2
decision that had derived 40 x 30 because *the outline is the radiator*
(R_ba 39 -> 31 C/W across the range). Placement then optimized to FIT: the
final board carries 0.05 mm of slack on all four edges. A correct board that
taught the wrong lesson - which is why the target, not the caller, picks the
binding.

## Tokens

| token | target |
|---|---|
| `learning <target>:` | `<target>` |
| `ultra bare bones design:` | block-basics |
| `ultra-bare-bones:` | block-basics |

The legacy `ultra bare bones` tokens resolve to `block-basics`, which is
CANONICAL: the size default those tokens always carried - "the smallest outline
that keeps the layout HONEST" - is a canonical binding written in prose, and
bb-buck's binding size was the accident, not the contract. When a size really is
fixed, say so with the `fit-check` target instead of assuming the old behaviour.

At P0: `state.py mode --token "<the brief's token>"` records the resolved mode,
logs the decision and is what `board_init` reads; then name mode, target, scope,
binding and stage in `requirements.md` section 1. A brief with no token has no
mode - design normally.

## Targets

| target | scope | binding | stage | teaches |
|---|---|---|---|---|
| `stage-placement` | block-only | canonical | P6 | the block's canonical layout: hot loop, thermal copper, part-to-part geometry |
| `stage-routing` | block-only | canonical | P7 | how the block's copper is really routed: widths, returns, plane strategy |
| `stage-schematic` | block-only | canonical | P4 | the block's minimum correct schematic and the values behind it |
| `block-basics` | block-only | canonical | - | the block end to end, with no single stage under study |
| `block-integration` | block+interfaces | bounded | - | the block driving and driven by the real interfaces of its role |
| `production-block` | product | product | - | what a shippable version of this block actually needs |
| `fit-check` | block-only | constrained | - | whether the block fits a size that really is fixed |

## Scope tiers

`excludes` is a closed vocabulary of feature classes. A class the tier excludes
is NEVER a reviewer finding; a class the tier requires IS one when it is absent.
No tier excludes `thermal`: a thermal solution the datasheet requires at the
stated operating point is support, not a feature, and is in scope everywhere -
bb-buck's outline was sized by its junction temperature.

| tier | excludes | requires | the board is |
|---|---|---|---|
| `block-only` | protection, filtering, indicators, test-points, config, second-rail, mechanical, enclosure-fit | - | one block, one input interface, one output interface |
| `block+interfaces` | protection, indicators, config, enclosure-fit | filtering | the block plus every interface its role needs, conditioned as those interfaces require |
| `product` | - | protection, filtering, connectors, thermal, enclosure-fit | a shippable version of the block |

### block-only - one functional block, built to be studied

These boards exercise the pipeline and seed the knowledge library one domain at
a time, so every part that is not that block is noise.

**Include, and nothing else**
- The block's active part(s) plus exactly the support components its datasheet
  requires for correct operation at the stated operating point.
- One input and one output interface, each the simplest part that carries the
  power or signal involved - unless the block under study IS an interface, in
  which case that interface is whatever its standard requires.
- What the fab needs to build the board and the bench needs to hold it.

**Exclude, unless the block under study IS that thing**
- Protection of every kind: TVS/ESD, reverse polarity, fusing, OVP/OCP/UVLO
  beyond what lives inside the IC.
- Filtering, sequencing or conditioning the datasheet does not require.
- Indicators, displays, buttons, jumpers, config straps, DNP/alternate
  footprints, and test points beyond the block's own measurement need.
- Any second rail, second IC or MCU the block does not need in order to work.
- Mechanical and enclosure features beyond mounting the bare board.

**Defaults - apply them, do not ask**
- The fewest layers the block honestly needs; JLC PCBA, single-sided assembly.
- Quantity 5, cost minimal, no enclosure, bench environment (indoor, 0-50 C).
- Stop at P9 (fab package + DFM). Ordering is a separate owner decision.
- Size: whatever the binding says. At `canonical` the layout earns it.

### block+interfaces - the block in its real signal environment

Everything block-only includes, plus every interface the block's ROLE needs
(not one in and one out), each conditioned as its own standard requires:
terminations, common-mode chokes, magnetics, bias networks, the connector the
standard specifies. Protection is still out unless an interface standard
mandates it. This is the tier for "does this block actually work when it is
talking to something".

### product - what a shippable version needs

Nothing is excluded, and five classes are REQUIRED: protection sized to the
input source and the environment, filtering (input and output, EMI as well as
ripple), the connectors a product would ship with, a thermal solution proven at
the stated ambient, and enclosure fit. At this tier the ABSENCE of any of them
is a reviewer finding, not a scope decision.

## Binding levels

| binding | geometry | cap | also binds | rule |
|---|---|---|---|---|
| `canonical` | output | - | - | provisional outline -> place -> `board_edit --outline fit` -> route; a stated dimension that fights the canonical layout LOSES, and the loss is a recorded decision |
| `bounded` | output | +30% | - | the canonical flow, and a stated size is kept only when it is at most the cap above what the placement earned; a smaller stated size loses like canonical |
| `constrained` | input | - | - | the stated size binds at `board_init` and placement fits it; nothing is relaxed |
| `product` | input | - | cost, thermal | size, cost and thermal all bind; a stated dimension is a product constraint, not a preference |

Geometry `output` means the board size, aspect and outline are RESULTS of the
placement, so P5 must not be given one:

1. `board_init --outline auto` (generous provisional room - guessing the final
   size here is what binds placement to a number nobody has earned yet).
2. Place (P6) to the canonical layout, gate `place`.
3. `board_edit --outline fit --margin M` - the board becomes what the placement
   needs. Re-run `planes_gen` if it GREW (a zone outline does not follow the
   edge outward).
4. Route (P7).

Mechanically: `board_init` REFUSES a fixed `--outline WxH` when the workspace's
recorded mode makes geometry an output. The `resize-board` recipe carries the
flow; `--allow-fixed-outline` is the explicit, reported consent to override it.

## Relaxation is never silent

A mode relaxes GEOMETRY, cost and packaging - nothing else. Every relaxed spec:

- is recorded with `state.py decision` at the phase that relaxed it, naming the
  stated value, the value the design earned, and the mechanism that earned it
  ("ignoring 35 x 25: the canonical hot-loop layout wants ~45 x 30");
- appears in the H1 checkpoint beside the blocks and the stackup;
- is marked `RELAXABLE (<binding>)` in `requirements.md` section 5 at P0, so a
  stated dimension never reads as a HARD cap that binds at P5.

**What no mode relaxes.** Everything that makes the board true: every gate, the
P2/P3 coverage checks and the research they trigger, DFM, the datasheet's own
requirements, and every safety question in the requirements-analyst's section 8.
A minimal board is minimal in SCOPE, never in rigor. If the stated operating
point implies a hazard (mains, >30 V, >3 A, battery), ask - no mode grants
silence there.

**Research is mandatory at every learning target, and this OVERRIDES the
recipe's flags.** These boards exist to seed the library, so `covered` off the
existing records is not good enough. Run the FIRST coverage call of each phase
with BOTH `--maturity-floor proven --research-provisional`: the floor says only
a record a BUILT board has proven counts, and the escalation puts every unproven
class into the research task instead of letting it pass as `provisional`. Only
bring-up evidence retires the requirement (`knowledge.py --prove`). Re-run
coverage after research WITHOUT those flags - the re-run's job is to fold in
what was just learned, and re-escalating would only re-fire the trigger. The
`budgets.research` caps still bound the spend, and a cap hit is a visible
checkpoint.

**Reviewers.** Read the scope tier: a feature class it EXCLUDES is not a finding
(no absent-ESD/protection/indicator reports at `block-only`), and a class it
REQUIRES is a finding when absent (at `product`, missing protection or filtering
is an error). A spec the binding relaxed is not drift - compare the board
against what the design EARNED and recorded, not against the stated number it
was allowed to lose. Everything else you hunt is unchanged: report anything that
makes the block itself wrong, unsafe at its stated operating point, or
unbuildable.

**Knowledge guardrail.** Scope exclusions are SCOPE decisions, not engineering
judgments. Never write a knowledge record, or promote a learning, that says a
protection or conditioning feature is unnecessary in general - a learning from
one of these boards is valid only where it concerns the block's own topology,
layout or manufacture. Workspace learnings from a mode board carry the mode
token so promotion review can see the provenance.
