"""Write flat <lib>.kicad_sym files from KiCad 10's one-file-per-symbol
<lib>.kicad_symdir folders, for the libraries hwde's scripts read by lib_id
(power:, Device:). kicad-sch-api 0.5.6 only reads the flat form.

    flatten-symdirs.py <kicad symbols dir> <out dir> [lib ...]

The symbols are copied unchanged, so a schematic built against the flat file
embeds the same lib_symbols as one built against KiCad's own.
"""

import sys
from pathlib import Path


def flatten(symdir: Path, out: Path) -> None:
    bodies = []
    for f in sorted(symdir.glob("*.kicad_sym")):
        txt = f.read_text(encoding="utf-8")
        bodies.append(txt[txt.index("(symbol "):txt.rindex(")")].rstrip())
    tmp = out.with_suffix(".tmp")
    tmp.write_text('(kicad_symbol_lib\n\t(version 20251024)\n\t(generator "hwde")\n\t'
                   + "\n\t".join(bodies) + "\n)\n", encoding="utf-8")
    tmp.replace(out)


def main() -> int:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    for lib in sys.argv[3:] or ["power", "Device"]:
        d = src / f"{lib}.kicad_symdir"
        if d.is_dir():
            flatten(d, dst / f"{lib}.kicad_sym")
        elif (src / f"{lib}.kicad_sym").is_file():
            (dst / f"{lib}.kicad_sym").write_bytes((src / f"{lib}.kicad_sym").read_bytes())
    return 0


if __name__ == "__main__":
    sys.exit(main())
