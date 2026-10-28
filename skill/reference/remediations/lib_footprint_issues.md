# lib_footprint_issues

KiCad's library-CONFIGURATION check: a footprint's declaring library cannot be resolved from
the project DRC actually loaded. Says nothing about pad geometry.

- Emitted by: kicad-cli `pcb drc` via scripts/kc.py (kc.py:221).   Gate: `drc` (P6, error-only)
  and `drc_routed` (P7, error AND warning, gates.yaml:60-68). Severity observed here is
  warning -> it can only fail `drc_routed`.
- Fixer domain: `library` (cluster_violations.py:93)   Scripts you may use: lib_pull.py, fp_verify.py,
  datasheet_extract.py (fix_dispatch.py:115-123)
- Fields on the violation: `refs` (["L20"] - the only useful locator), `msg` (the real sub-reason; read it),
  `pos` = footprint ORIGIN, `items[].uuid` = footprint uuid; `layer`/`net` are null here.

## Is it real?
- Almost certainly not. Every occurrence in the committed reports is the same message ("The
  footprint library 'aiee' is not enabled in the current configuration"), all from DRC on a
  STAGED COPY (work/p7/kb/routed.kicad_pcb, work/p7/mdi/mdi_final.kicad_pcb, 111 each); DRC on
  a real boards/*/kicad/<ws>.kicad_pcb is ZERO. LEARNINGS 754: such a copy also relaxes
  copper_edge_clearance to the 0.5 mm default, so any errors beside these are suspect too.
- Trigger is STEM mismatch, not directory: both staged dirs DO hold fp-lib-table and
  lumina-carrier.kicad_pro, but the boards are routed/mdi_final and KiCad loads only
  `<stem>.kicad_pro`. Sibling pro present -> 0 findings; absent -> 111.
- A correct project suppresses the rule outright (`lib_footprint_issues: "ignore"`,
  board_init.py:186, schlib.py:466). If it DOES fire on the real board, suspect a .kicad_pro
  grown into a full default-shaped blob whose overrides KiCad drops (LEARNINGS 86).
- Genuinely real only if the library is missing: a wrong lib-table URI pulled into the repo
  root leaving the board's lib/ empty (1065, 1099), or lib_pull passed writing nothing (1134,
  1245, 1186).

## Fix ladder (cheapest first)
1. Re-gate the PROJECT board: `gate.py --gate drc_routed ~/dev/boards/<ws>/kicad/<ws>.kicad_pcb`.
   Clean -> report false positive, stop. No library edit.
2. To gate a staged board in place, give it a stem-matched project: copy `<ws>.kicad_pro`,
   `<ws>.kicad_dru`, `fp-lib-table` beside it, pro/dru RENAMED to the staged stem, re-run.
3. If it fires on the project board: confirm .kicad_pro has `board.design_settings.
   rule_severities.lib_footprint_issues == "ignore"`. Full default blob -> that is the defect;
   escalate, do not hand-trim a project file in a fix loop.
4. Prove the library exists before blaming the pull: resolve the fp-lib-table URI
   (`${KIPRJMOD}/../lib/aiee.pretty`) and COUNT .kicad_mod on disk; never trust lib_pull.
5. Only if parts are truly absent: `lib_pull.py --lcsc <id> --out-dir <ABSOLUTE> --project
   ~/dev/boards/<ws>/kicad --verify-load --overwrite`, ~20 s per part, 180 s backoff (1186, 587).
6. Escalate: "on the project board this is a project-file / lib-table defect, not a fixable
   cluster"; add `requires_pipeline_rewind` if footprints under placed parts would change.

## Do not
- Do not re-pull to silence it: that APPENDS duplicate symbols for any EasyEDA name with a
  space or '/' (587), and a relative `--out-dir` bakes dead 3D paths into the repo root (701).
- Do not raw-edit .kicad_mod or fp-lib-table; library edits need approval + lib/EDITS.md.
- Do not read this as a geometry verdict, nor a clean fp_verify as one: fp_verify has NO drill
  handling so a wrong THT annulus passes (1256); pulled courtyards enclose the body only (1167).
- Do not absorb neighbouring complaints: pulled USB-C peg-hole annular/clearance ERRORS are
  real defects under their own check ids (712); 3D-model origin offsets are not defects (1359).

## Verify
```
cd C:\dev\ai-ee3
.venv\Scripts\python.exe .claude\skills\hwde\scripts\kc.py drc boards\<ws>\kicad\<ws>.kicad_pcb --parity --all-track-errors --out boards\<ws>\work\drc_libchk.json
.venv\Scripts\python.exe -c "import json;d=json.load(open(r'boards\<ws>\work\drc_libchk.json'));print(sum(1 for v in d['violations'] if v['check']=='lib_footprint_issues'))"
.venv\Scripts\python.exe .claude\skills\hwde\scripts\gate.py --gate drc_routed boards\<ws>\kicad\<ws>.kicad_pcb
```

## Sources
- LEARNINGS 2026-07-28 [drc][kicad-cli] DRC on a board copy OUTSIDE the project dir (line 754)
- LEARNINGS 2026-07-11 [kicad] .kicad_pro is the DRC/ERC authority, keep it minimal (line 86)
- LEARNINGS [librarian][parts][easyeda2kicad][gates] lib-table URI -> repo root (1065),
  relative --out-dir (701, 1099), false "pulled" (1134, 1245), rate limit (1186), symbol pulls
  not idempotent (587), peg holes (712), courtyards (1167), 3D origin (1359), no drills (1256)
- kc.py:110-121,221-244 ; cluster_violations.py:93 ; fix_dispatch.py:115-123 ; board_init.py:186 ;
  schlib.py:466 ; gates.yaml:52-68 ; boards/lumina-carrier/work/p7/{kb,mdi}/ (staged copies)
