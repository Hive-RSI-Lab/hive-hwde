"""Build parts/parts.json from parts/bom-lines.json (written by root.py):
one line per distinct LCSC part, with its refdes list and the live LCSC data
(price breaks, stock, attributes) from hwde's parts_search.

    hwde-venv-python kicad/gen/parts_json.py   (needs the network)
"""
import json
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

WS = Path(__file__).resolve().parents[2]
HWDE = sys.argv[1] if len(sys.argv) > 1 else "hwde"
lines = json.loads((WS / "parts" / "bom-lines.json").read_text())
cache_dir = WS / "parts" / "lcsc"
cache_dir.mkdir(exist_ok=True)

by = OrderedDict()
for ln in lines:
    e = by.setdefault(ln["lcsc"], {"lcsc": ln["lcsc"], "refdes": [], "roles": []})
    e["refdes"].append(ln["ref"])
    if ln.get("role") and ln["role"] not in e["roles"]:
        e["roles"].append(ln["role"])
    e.setdefault("value", ln["value"])

parts = []
for lcsc, e in by.items():
    f = cache_dir / f"{lcsc}.json"
    if not f.exists():
        subprocess.run(["sh", HWDE, "parts_search", "--query", lcsc, "--limit", "3",
                        "--out", str(f)], check=False, capture_output=True)
    d = json.loads(f.read_text()) if f.exists() else {}
    hit = next((r for r in d.get("results", []) if r.get("lcsc") == lcsc), None)
    if hit is None:
        print("no live data for", lcsc, file=sys.stderr)
        hit = {}
    parts.append({
        "lcsc": lcsc, "refdes": e["refdes"], "value": e["value"],
        "mpn": hit.get("mpn"), "brand": hit.get("brand"),
        "package": hit.get("package"), "basic": hit.get("basic"),
        "stock": hit.get("stock"), "price": hit.get("price"),
        "price_breaks": hit.get("price_breaks"), "datasheet": hit.get("datasheet"),
        "description": hit.get("description"), "attributes": hit.get("attributes"),
        "role": "; ".join(e["roles"]), "ref_prefix_hint": e["refdes"][0].rstrip("0123456789"),
    })
(WS / "parts" / "parts.json").write_text(json.dumps({"parts": parts}, indent=1, ensure_ascii=False) + "\n")
print(json.dumps({"distinct": len(parts), "placements": len(lines),
                  "basic": sum(1 for p in parts if p["basic"]),
                  "extended": [p["mpn"] for p in parts if p["basic"] is False]}))
