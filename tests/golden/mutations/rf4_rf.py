"""check_rf's declarations for rf4 (RFM95W at 915 MHz), shared by the rf-*
mutants' constraints.json sidecars and tests/test_check_rf.py, which holds
the unmutated golden passing under them. They stay out of the golden's own
constraints.json, which the stage bench pins by hash.

The L2-J1 feed is ~31 deg (max_deg 40, the pi match's laid length); the host
SPI bus runs 1.6 mm (over 5 h) from the radio's copper."""
import json
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1]

RF = {
    "rf": [{"net": "/RF_FEED", "impedance_ohm": 50, "f_ghz": 0.915,
            "path": ["L2.2", "J1.1"], "max_deg": 40},
           {"net": "/RF1", "f_ghz": 0.915}],
    "rf_blocks": [{"name": "radio", "f_ghz": 0.915,
                   "refs": ["L2", "C14", "C15", "J1"],
                   "nets": ["/RF1", "/RF_FEED"]},
                  {"name": "host", "f_ghz": 0.01, "refs": ["J2"],
                   "nets": ["/SCK", "/MISO", "/MOSI", "/NSS", "/RESET",
                            "/DIO5"]}],
}


def constraints(**more) -> dict:
    """The golden's constraints with RF and `more` added."""
    cons = json.loads((GOLDEN / "rf4" / "constraints.json")
                      .read_text(encoding="utf-8"))
    cons.update(RF)
    cons.update(more)
    return cons


def sidecar(**more) -> dict:
    return {"constraints.json": json.dumps(constraints(**more), indent=2)
            + "\n"}
