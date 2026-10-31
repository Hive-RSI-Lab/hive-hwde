"""A footprint's placement type (attr) against its own pads (lib/fpattr.py,
fpfix rule G, bom_cpl's class and check_fp_attr).

The hit: EasyEDA's XR0850 power inductor footprint is (attr through_hole) on
two SMD pads, so pinned lite-main's L1-L4 went to JLC's THT step in its
bom-cpl.json. U8 (HTSSOP-32 whose EP vias are plated holes) and J1 (USB-C
with THT shell legs) are smd and must stay so.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import sexpdata

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "lib"))

import bom_cpl  # noqa: E402
import check_fp_attr  # noqa: E402
import fpattr  # noqa: E402
import fpfix  # noqa: E402
import verify_all  # noqa: E402

LITE = ROOT / "pinned" / "lite-main"
LITE_PCB = LITE / "kicad" / "lite-main.kicad_pcb"
XR0850 = LITE / "lib" / "aiee.pretty" / "IND-SMD_L8.0-W8.0_XR0850.kicad_mod"

SMD = '\t(pad "{n}" smd rect (at {x} 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask"))\n'
THT = ('\t(pad "{n}" thru_hole circle (at {x} 0) (size 1.6 1.6) (drill 0.8) '
       '(layers "*.Cu" "*.Mask"))\n')


def fp(attr: str | None, pads: str) -> str:
    a = f"\t(attr {attr})\n" if attr is not None else ""
    return f'(footprint "X"\n\t(layer "F.Cu")\n{a}{pads})\n'


def judge(text: str) -> dict:
    return fpattr.judge(sexpdata.loads(text))


def test_smd_pads_marked_through_hole_are_smd():
    v = judge(fp("through_hole", SMD.format(n=1, x=-2) + SMD.format(n=2, x=2)))
    assert v["mismatch"] and v["fix"] == "smd" and v["declared"] == "through_hole"


def test_pins_marked_smd_are_through_hole():
    v = judge(fp("smd", THT.format(n=1, x=0) + THT.format(n=2, x=2.54)))
    assert v["mismatch"] and v["fix"] == "through_hole"


def test_mixed_pads_keep_their_own_type():
    pads = SMD.format(n=1, x=0) + THT.format(n="S", x=5)
    assert not judge(fp("smd", pads))["mismatch"]
    assert not judge(fp("through_hole", pads))["mismatch"]
    v = judge(fp(None, pads))   # no type at all: only the drawing settles it
    assert v["mismatch"] and v["fix"] is None


def test_thermal_vias_inside_an_smd_pad_are_not_pins():
    ep = ('\t(pad "9" smd rect (at 0 0) (size 3 3) '
          '(layers "F.Cu" "F.Paste" "F.Mask"))\n')
    via = ('\t(pad "9" thru_hole circle (at {x} 0) (size 0.6 0.6) (drill 0.3) '
           '(layers "*.Cu"))\n')
    v = judge(fp("smd", ep + via.format(x=-1) + via.format(x=1)))
    assert v["thermal_vias"] == 2 and v["tht"] == 0 and not v["mismatch"]
    v = judge(fp("through_hole", ep + via.format(x=1)))
    assert v["fix"] == "smd"


def test_board_features_and_padless_footprints_are_not_judged():
    assert not judge(fp("smd board_only exclude_from_bom",
                        THT.format(n=1, x=0)))["mismatch"]
    assert not judge(fp("through_hole", ""))["mismatch"]
    npth = ('\t(pad "" np_thru_hole circle (at 0 0) (size 3 3) (drill 3) '
            '(layers "*.Cu" "*.Mask"))\n')
    assert not judge(fp("through_hole", npth))["mismatch"]


def test_set_type_keeps_the_other_flags_and_adds_a_missing_attr():
    t = fp("through_hole exclude_from_pos_files", SMD.format(n=1, x=0))
    new = fpattr.set_type(t, "smd")
    assert "(attr smd exclude_from_pos_files)" in new
    assert new.replace("(attr smd exclude_from_pos_files)", "") == \
        t.replace("(attr through_hole exclude_from_pos_files)", "")
    new = fpattr.set_type(fp(None, SMD.format(n=1, x=0)), "smd")
    assert '\t(layer "F.Cu")\n\t(attr smd)\n' in new
    assert judge(new)["declared"] == "smd"


def test_fpfix_rule_g_fixes_the_xr0850_library_footprint(tmp_path):
    f = tmp_path / XR0850.name
    shutil.copy(XR0850, f)
    rep = fpfix.fix_file(f, check_body=False)
    g = [a for a in rep["actions"] if a["rule"] == "G"]
    assert g == [dict(g[0], action="set_attr", **{"from": "through_hole",
                                                  "to": "smd"})]
    text = f.read_text(encoding="utf-8")
    assert "(attr smd)" in text and "through_hole" not in text
    again, acts = fpfix.sanitize(text)
    assert again == text and not [a for a in acts if a["rule"] == "G"]


def test_fpfix_board_fixes_the_placed_inductors_only(tmp_path):
    pcb = tmp_path / LITE_PCB.name
    shutil.copy(LITE_PCB, pcb)
    rep = fpfix.fix_board(pcb)
    fixed = sorted(r["ref"] for r in rep["results"]
                   if any(a["rule"] == "G" for a in r["actions"]))
    assert fixed == ["L1", "L2", "L3", "L4"]
    old = LITE_PCB.read_text(encoding="utf-8")
    new = pcb.read_text(encoding="utf-8")
    assert new.count("(attr smd)") == old.count("(attr smd)") + 4
    assert len(new) == len(old) - 4 * (len("through_hole") - len("smd"))
    viols, _ = check_fp_attr.check(pcb)
    assert viols == []


def test_bom_cpl_classes_the_inductors_by_their_pads():
    f = bom_cpl.board_part_fields(LITE_PCB)
    for ref in ("L1", "L2", "L3", "L4"):
        assert f[ref]["attr_class"] is None          # smt_placed, not tht
        assert f[ref]["attr_mismatch"]["attr"] == "through_hole"
        assert f[ref]["attr_mismatch"]["pads_say"] == "smd"
    for ref in ("U8", "J1"):
        assert "attr_mismatch" not in f.get(ref, {})
        assert (f.get(ref) or {}).get("attr_class") is None
    classes, _, src = bom_cpl.resolve_assembly({"L1", "U8"}, [], f)
    assert classes["L1"] == bom_cpl.PLACED_CLASS


def test_check_fp_attr_fails_lite_main_l1_to_l4():
    viols, n = check_fp_attr.check(LITE_PCB)
    assert n > 100
    assert sorted(r for v in viols for r in v["refs"]) == ["L1", "L2", "L3", "L4"]
    v = next(v for v in viols if v["refs"] == ["L1"])
    assert v["severity"] == "error" and v["kind"] == "fp_attr_mismatch"
    assert v["pos"] == [89.865, 87.323] and v["fixable"]
    assert "--only IND-SMD_L8.0-W8.0_XR0850" in v["fix"]


def test_check_fp_attr_is_clean_on_the_other_pinned_boards():
    for pcb in (ROOT / "pinned" / "core" / "kicad" / "core.kicad_pcb",
                ROOT / "pinned" / "ultra-front" / "kicad" / "ultra-front.kicad_pcb"):
        assert check_fp_attr.check(pcb)[0] == [], pcb


def test_check_fp_attr_is_registered():
    assert "check_fp_attr" in [c["name"] for c in verify_all.load_checks()]
