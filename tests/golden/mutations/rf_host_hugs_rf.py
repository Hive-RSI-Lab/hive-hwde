"""Mutant: rf-host-hugs-rf (rf4).

LAYOUT fault: the host bus line /DIO5 is rerouted 1.1 mm higher, so it runs
along under the RFM95W's RF pin (U1.9, /RF1) 0.54 mm off its copper where
the golden keeps 1.64 mm (5 h is 1.24): SPI edges couple straight into the
receive path. DRC's 0.2 mm clearance passes it. Must be caught by check_rf
(kind "rf_isolation", the radio and host blocks).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib
import rf4_rf


def surgery(text):
    for old, new in (("(start 122.28 124.4)", "(start 122.28 123.3)"),
                     ("(end 103.6 124.4)", "(end 103.6 123.3)"),
                     ("(start 103.6 124.4)", "(start 103.6 123.3)"),
                     ("(end 122.28 124.4)", "(end 122.28 123.3)")):
        text = mutlib.replace_once(text, old, new, f"/DIO5 {old}")
    return text, {"sidecars": rf4_rf.sidecar(), "net": "/DIO5",
                  "y": 123.3}


if __name__ == "__main__":
    sys.exit(mutlib.run("rf-host-hugs-rf", "rf4", surgery))
