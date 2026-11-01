"""Mutant: rf-match-decade (rf4).

VALUE fault: rf4's pi match between the RFM95W's RF pin (/RF1) and the SMA
(/RF_FEED) is C14 5.6 pF / L2 6.8 nH / C15 8.2 pF. Its constraints sidecar
names the antenna the network was designed for (J1 sees 25+j12 ohm at 868
MHz; the golden network gives ~19 dB return loss across 863-870 MHz), and
C15 is populated as 82 pF, one decade off (a value typed as 82p for 8p2).
ERC, DRC and every footprint check pass a cap of the wrong value. Must be
caught by check_rf_match (kind "rf_match_rl", refs include C15).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mutlib

RF_MATCH = [{"load": "J1", "zin": "25+j12@868M",
             "band_hz": [863e6, 870e6], "rl_db": 10,
             "note": "TEST antenna: the impedance rf4's golden pi matches"}]


def surgery(text):
    text = mutlib.edit_footprint(text, "C15", '"Value" "8.2pF"',
                                 '"Value" "82pF"', "C15 value")
    cons = mutlib.golden_json("rf4", "constraints.json")
    cons["rf_match"] = RF_MATCH
    return text, {"ref": "C15", "value": "8.2pF -> 82pF",
                  "sidecars": {"constraints.json": mutlib.dump_json(cons)}}


if __name__ == "__main__":
    sys.exit(mutlib.run("rf-match-decade", "rf4", surgery))
