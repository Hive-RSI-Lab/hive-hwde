"""Mutant: si-teardrop-clock (usbbuck4).

The si-unterminated-clock fault (MCO detoured to ~63 mm with no series
resistor) plus one KiCad teardrop zone on /MCO at the via where its B.Cu run
starts. Before #633 every zone's net counted as a power net, so the teardrop
made check_si skip MCO and the fault passed silently. Must be caught by
check_si (si_unterminated_clock on /MCO).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import si_unterminated_clock

TEARDROPS_ON = ("\t\t(teardrops\n\t\t\t(best_length_ratio 0.5)\n"
                "\t\t\t(max_length 1)\n\t\t\t(best_width_ratio 1)\n"
                "\t\t\t(max_width 2)\n\t\t\t(curved_edges no)\n"
                "\t\t\t(filter_ratio 0.9)\n\t\t\t(enabled yes)\n"
                "\t\t\t(allow_two_segments yes)\n"
                "\t\t\t(prefer_zone_connections yes)\n\t\t)\n")
UUID = "aaaa0651-dead-beef-0633-000000000001"


def teardrop_sexpr(net, layer, pts, uuid):
    xy = " ".join(f"(xy {x} {y})" for x, y in pts)
    return (f"\t(zone\n"
            f"\t\t(net \"{net}\")\n"
            f"\t\t(layer \"{layer}\")\n"
            f"\t\t(uuid \"{uuid}\")\n"
            f"\t\t(hatch none 0.1)\n"
            f"\t\t(priority 30001)\n"
            f"\t\t(attr\n\t\t\t(teardrop\n\t\t\t\t(type padvia)\n\t\t\t)\n\t\t)\n"
            f"\t\t(connect_pads yes\n\t\t\t(clearance 0)\n\t\t)\n"
            f"\t\t(min_thickness 0.0254)\n"
            f"\t\t(fill yes\n\t\t\t(island_removal_mode 1)\n\t\t)\n"
            f"\t\t(polygon\n\t\t\t(pts\n\t\t\t\t{xy}\n\t\t\t)\n\t\t)\n"
            f"\t)\n")


def surgery(text):
    text, info = si_unterminated_clock.surgery(text)
    # a padvia drop from the MCO via at (141, 123) onto its B.Cu stub
    pts = [(140.6, 122.875), (140.6, 123.125), (141.0, 123.3),
           (141.3, 123.0), (141.0, 122.7)]
    # KiCad deletes a teardrop on refill unless its via has drops enabled
    text = mutlib.replace_once(
        text, '(at 141 123)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
        '\t\t(layers "F.Cu" "B.Cu")\n',
        '(at 141 123)\n\t\t(size 0.6)\n\t\t(drill 0.3)\n'
        '\t\t(layers "F.Cu" "B.Cu")\n' + TEARDROPS_ON, "enable MCO via drops")
    text = mutlib.append_items(
        text, teardrop_sexpr("/MCO", "B.Cu", pts, UUID), "MCO teardrop")
    return text, {**info, "teardrop": True}


if __name__ == "__main__":
    sys.exit(mutlib.run("si-teardrop-clock", "usbbuck4", surgery))
