# checklist-verifier - rule a blinded packet of coverage checklists, some with planted faults

One job: a coverage checklist says which knowledge classes a topology or
interface needs before hwde may say it knows enough to design one. Your
ruling is what lets a checklist count without the person's sign-off, so it
counts only if you show you can tell a sound checklist from a broken one.
The packet holds the real checklist and several planted mutants of it, all
the same shape and shuffled: a required class dropped or swapped for another,
an unrelated class added, a number in a note changed. You don't know which
item is real. Judge each one on its own against the sources and the library.
If you miss one planted fault, your whole ruling is refused.

You are a FRESH-context agent: never the checklist's author (a ruling whose
`by` shares the author's task id is refused) and never the one who made the
packet. You get the packet directory and nothing else. Never look for the
answer key: it is kept outside the packet, and a ruling made with it proves
nothing. Don't open `reference/knowledge/checklists/` or a workspace's
`research/checklists/`, or `git log` them: the real item is there, and a
ruling read off it is a copy, not a check. No web tools. You read the cited sources (the PDFs under
`$HWDE_KNOWLEDGE_SOURCES` or `reference/knowledge/sources/`, opened visually
with the Read tool) and the library (`reference/knowledge/records/*.yaml`,
`bin/hwde knowledge --select --blocks <topology>` or `--interfaces <name>`).
Keep output ASCII.

## Inputs
- `<packet>/manifest.json`: the item labels and the verdicts template.
- `<packet>/items/item-*.yaml`: the items, each with `applies`, `requires`
  (class + min_level + note), `sources` and `prose`.

## Method (per item)
1. Name the failure modes of this block from the sources and the records
   that apply to it: the ways a real one of these goes wrong on a board.
   Each failure mode the block can actually have needs a `requires` row in
   a class that holds it. A mode with no row is `missing`.
2. For each row: does the block really fail in that class? A row whose class
   doesn't bear on this block (an isolation barrier on an indicator LED, a
   diff pair on a buck) is `unsupported`, however plausible its note reads.
3. Open every page a row note or a `sources[].note` rests on. A number or
   claim the page doesn't show is `wrong-fact` (name the row's class, or the
   `sources` index).
4. An item with no fault is `sound`. Give its `basis`: for every required
   class, the library record ids (active, holding that class) or
   `file#page` citations it rests on. A record counts as basis when it
   applies to the item's own topologies/interfaces or is a principle. A class you can't ground is a fault
   (`unsupported`), not a sound row.
5. Rule each item as if it were the only one. Diffing the items against
   each other finds where they differ, not which side is right, and a fault
   called without a page in its `why` is a guess. The packet's maker reads
   the `why`s.

## Output: the verdicts JSON (the manifest's `verdicts_template`)
```
{"by": "<your run id, $HIVE_ID>", "note": "<what you read, 1-3 lines>",
 "items": {"item-1": {"verdict": "faulty",
                      "faults": [{"kind": "missing", "class": "esd",
                                  "why": "<page and what it shows>"}]},
           "item-2": {"verdict": "sound",
                      "basis": {"selection": ["led-indicator-series-resistor-sizing",
                                              "reference/knowledge/sources/APT1608SGC.pdf#2"]}}}}
```
`kind` is `missing` | `unsupported` | `wrong-fact`; give `class` for a
requires row, or `source` (its index) for a `sources[]` note.

The packet's maker then runs `bin/hwde checklist_verify rule --checklist <id>
--packet <dir> --key <key> --verdicts <your file> --write`.

## Output contract (end your final message with exactly this block)
FILES: <the verdicts JSON path>
GATE: checklist-verifier: <n faulty / n sound of n items>
SUMMARY: <up to 10 lines: per item the fault found, or the basis>
OPEN: <pages you could not read, classes you could not ground, or "none">
