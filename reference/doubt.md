# Doubt: the board asks how it could fail, and writes the check that answers

The person, 10-30: "the point of closing the loop is having it be critical
of its own design, form its own questions, and then build tools to test
those to ensure the board will work."

The gates ask the same list of every board. ERC, DRC, verify and DFM can't
ask what they don't know to ask: that a 3.3 V expander drives a 5 V amp's
RESET, that "VBUS" means 20 V on this board and 5 V on the last, that the
TVS's standoff sits under the top of the PD contract. A person reviewing the
board asks those. hwde asks them itself, and answers each with a check it
writes, so the answer re-runs on every change and the next board inherits
the question.

## The loop

For a board in `boards/<b>` (`hwde doubt <verb> --workspace boards/<b>`):

1. **ask**: every question in the module's library (`scripts/doubts/`) that
   applies to this board joins its ledger, and the board's parts and rails
   are printed as prompts, with the rails whose voltage no net name says.
2. **Doubt it yourself.** Go part by part, rail by rail, connector by
   connector, and ask what makes the board fail there: at power-on, at the
   worst input, hot, on a reset, plugged in wrong, after firmware does the
   wrong thing. A question is worth a check when its answer could stop the
   board, and it isn't one a gate already answers. For each:
   `add --id <id> --question "...?" --why "how the board fails" --domain D`
   writes `doubts/checks/<id>.py`; write `answer(board)` in it.
3. **fact**: a voltage the net names don't carry (`PVDD`, a PD `VBUS`)
   goes in once with its source (`--rail PVDD=21 --source "..."`); every
   check reads it.
4. **run**: every check answers on the board as it is now: pass with its
   margin, fail with where, or unknown with the fact that would settle it.
5. **Close each one.** An unknown is a fact to fetch (a datasheet row, a
   load current): fetch it, cite it, put it where the check reads it, run
   again. A fail is fixed on the board, or disposed of (`dispose --kind
   fixed|contingency|firmware|justified|accepted --how "..."`, with the
   number). `status` lists what's open; a disposition holds only for the
   answer it was given against, and a board or check change makes every
   answer stale until re-run.
6. **promote**: a check that would ask the same of any board (it has an
   `applies(board)`, reads nothing board-specific) moves into the library
   with a test that fails on a board with the fault. Every later board is
   asked it on `ask`.

The ledger is `doubts/ledger.json` (written by doubt.py only) and its table
`doubts/doubts.md`. It is a record the agent weighs, not a gate, as with
`first_spin.py`: say in a decision (`state.py decision`) how far the doubt
went and what's still open.

## Writing a check

A check is a Python file; `scripts/lib/doubtlib.py` documents the contract
and the `Board` it gets (nets joined through 0 R, inductors, ferrites, net
ties and jumpers; each part's pins by name; `rail(net)`, `supply(ref)`,
`rated_volts(ref)`, `mpn(ref)`, parts.json rows; the .kicad_pcb path for
geometry). Rules:

- A pass names what it checked and by how much it passes. A pass with no
  evidence is refused as an error.
- An assumption is an unknown, not a pass: a part read as plain CMOS
  because its datasheet row isn't in yet stays open until the row is.
- Facts come with a source: a datasheet's document, revision and page
  (`reference/doubts/logic_levels.yaml` is one such table), a README line,
  a measurement. Never from memory.
- A check that raises answers `error`, which can't be disposed of: fix the
  check.
- Be critical of the check too: run it, read every line it says, and when
  it reads a board wrong (a cap between VBUS and a gate taken as VBUS to
  ground), fix the check before trusting its answer.

## What the library asks now

| Check | Question | First found |
|---|---|---|
| `cross_rail_logic` | logic crossing supply domains reaches VIH and stays inside the receiver's absolute maximum | hifi-receiver-2 core: TS5A23157 select at 5 V from 3.3 V logic (review-2); on the pinned core it asked for the TPA3221's VIH and FAULT/OTW_CLIP drive, closed from TI SLASEE9C |
| `polarized_caps` | every polarised cap's + pad on the higher rail | - |
| `ground_ties` | every ground net tied to the main ground | - |
| `floating_controls` | no IC enable, reset, shutdown, select or address pin left open | lite-main: TPS54302 EN, CH340B RST, TPA3118 SYNC open (each a datasheet question) |

## First use: lite-main at its pin

On a scratch copy of `pinned/lite-main`, `ask` added two library questions
and printed `PVDD`, `/AMP_GVDD` and others as rails with no voltage, and
`VBUS` read 5 V by name on a board whose CH224A asks for 20 V. The board's
own question, "does every cap and TVS on VBUS and PVDD rate for the 20 V
contract plus PD's 5 %?" (after `fact --rail VBUS=21 --rail PVDD=21`),
failed on D1, an SMBJ20A with a 20 V standoff under 21 V. hifi-receiver-2
had fixed that after the pin (task 261030-093037), found by a person; the
gates never flagged it. Its first version also read C19, a gate hold-off
cap between VBUS and the gate, as VBUS to ground: the check was fixed to
take the voltage across a cap, not its net's.
