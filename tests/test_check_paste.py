"""check_paste: stencil area ratio and exposed-pad paste (dfm-assembly
ingestion, reference/ingest/dfm-assembly.md). Small fixtures only."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import check_paste  # noqa: E402

SMD = '(layers "F.Cu" "F.Paste" "F.Mask")'


def _qfn(name: str, ep: str, lead=(0.25, 0.8), pitch=0.5, extra="") -> str:
    """A 4 x 4 lead QFN-16-ish footprint at (10, 10) with exposed pad `ep`."""
    w, h = lead
    pads = []
    n = 1
    for side in range(4):
        for i in range(4):
            off = (i - 1.5) * pitch
            x, y, rot = [(off, 2.0, 0), (2.0, -off, 90), (-off, -2.0, 0),
                         (-2.0, off, 90)][side]
            pads.append(f'(pad "{n}" smd rect (at {x} {y} {rot}) '
                        f'(size {w} {h}) {SMD})')
            n += 1
    return (f'(footprint "t:{name}" (layer "F.Cu") (at 10 10)\n'
            f' (property "Reference" "U1" (at 0 -3 0) (layer "F.SilkS"))\n '
            + "\n ".join(pads) + f"\n {ep}\n {extra})\n")


def _board(tmp_path: Path, *fps: str) -> Path:
    pcb = tmp_path / "b.kicad_pcb"
    pcb.write_text("(kicad_pcb (version 20241229)\n" + "".join(fps) + ")\n",
                   encoding="utf-8")
    return pcb


def _kinds(pcb, t=0.12):
    vios, seen = check_paste.check(pcb, t)
    return sorted(v["kind"] for v in vios), vios, seen


def test_full_paste_on_a_qfn_exposed_pad_is_flagged(tmp_path):
    ep = f'(pad "17" smd rect (at 0 0) (size 2.6 2.6) {SMD})'
    kinds, vios, seen = _kinds(_board(tmp_path, _qfn("QFN-16_EP", ep)))
    assert kinds == ["paste_ep_coverage"]
    assert vios[0]["coverage"] == 1.0 and seen["exposed_pads"] == 1


def test_window_pane_exposed_pad_passes(tmp_path):
    ep = '(pad "17" smd rect (at 0 0) (size 2.6 2.6) (layers "F.Cu" "F.Mask"))'
    panes = "\n ".join(
        f'(pad "" smd rect (at {x} {y}) (size 0.93 0.93) (layers "F.Paste"))'
        for x in (-0.59, 0.59) for y in (-0.59, 0.59))
    kinds, _, _ = _kinds(_board(tmp_path, _qfn("QFN-16_EP", ep, extra=panes)))
    assert kinds == []


def test_exposed_pad_with_no_paste_is_an_error(tmp_path):
    ep = '(pad "17" smd rect (at 0 0) (size 2.6 2.6) (layers "F.Cu" "F.Mask"))'
    kinds, vios, _ = _kinds(_board(tmp_path, _qfn("QFN-16_EP", ep)))
    assert kinds == ["paste_ep_missing"] and vios[0]["severity"] == "error"


def test_paste_margin_ratio_brings_coverage_into_band(tmp_path):
    # -0.15 per axis: 2.6 -> 2.6 * 0.7 = 1.82 mm square, 49 % coverage
    ep = (f'(pad "17" smd rect (at 0 0) (size 2.6 2.6) {SMD} '
          f'(solder_paste_margin_ratio -0.15))')
    kinds, _, _ = _kinds(_board(tmp_path, _qfn("QFN-16_EP", ep)))
    assert kinds == []


def test_leaded_powerpad_keeps_its_one_to_one_opening(tmp_path):
    # TI SLOA120: a PowerPAD HTSSOP takes a 1:1 opening; only missing paste counts
    ep = f'(pad "17" smd rect (at 0 0) (size 2.6 2.6) {SMD})'
    kinds, _, _ = _kinds(_board(tmp_path, _qfn("HTSSOP-16_EP", ep)))
    assert kinds == []


def test_fine_pitch_apertures_fail_the_area_ratio_on_012_not_010(tmp_path):
    ep = '(pad "17" smd rect (at 0 0) (size 1.7 1.7) (layers "F.Cu" "F.Mask"))'
    panes = '(pad "" smd rect (at 0 0) (size 1.2 1.2) (layers "F.Paste"))'
    pcb = _board(tmp_path, _qfn("QFN-16_EP", ep, lead=(0.2, 0.6), pitch=0.4,
                                extra=panes))
    kinds, vios, _ = _kinds(pcb, 0.12)
    assert kinds == ["paste_area_ratio"]
    assert len(vios[0]["pads"]) == 16 and vios[0]["area_ratio"] < 0.66
    assert _kinds(pcb, 0.10)[0] == []


def test_two_pad_parts_and_shield_tabs_are_not_exposed_pads(tmp_path):
    fp = ('(footprint "t:USB-C" (layer "F.Cu") (at 5 5)\n'
          ' (property "Reference" "J1" (at 0 -3 0) (layer "F.SilkS"))\n'
          + "".join(f' (pad "{i}" smd rect (at {i * 0.5 - 1} -1) '
                    f'(size 0.3 1) {SMD})\n' for i in range(1, 6))
          + f' (pad "SH" smd rect (at 0 0.2) (size 3 1) {SMD}))\n')
    ind = ('(footprint "t:IND" (layer "F.Cu") (at 20 5)\n'
           ' (property "Reference" "L1" (at 0 -3 0) (layer "F.SilkS"))\n'
           f' (pad "1" smd rect (at -2 0) (size 2 4) {SMD})\n'
           f' (pad "2" smd rect (at 2 0) (size 2 4) {SMD}))\n')
    _, _, seen = _kinds(_board(tmp_path, fp, ind))
    assert seen["exposed_pads"] == 0


def test_aperture_rules_match_kicad():
    # per axis, size + 2 (margin + ratio * size) (paste_margin_vs_kicad.py
    # checks the same against kicad-cli's plot)
    assert check_paste.aperture_size(1, 0.5, -0.02, -0.1) == (0.76, 0.36)
    assert check_paste.aperture_size(0.2, 0.2, -0.2, 0) == (0.0, 0.0)
