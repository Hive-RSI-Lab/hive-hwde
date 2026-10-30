#!/bin/sh
# drc_routed gate on the board, with a short summary of non-silk findings
cd "$(dirname "$0")/.."
sh $HIVE_MODULES/hive-hwde/bin/hwde gate --gate drc_routed kicad/lite-main.kicad_pcb --workspace . --out reports/drc_routed-gate.json >/dev/null 2>&1
python3 - <<'P'
import json,collections
d=json.load(open('reports/drc_routed-gate.json'))
x0,y0=json.load(open('reports/board-init.json'))['outline_origin']
print(d['status'], d.get('failing_count'), dict(collections.Counter(v.get('check') for v in d['failing'])))
for v in d['failing']:
  if not v.get('check','').startswith('silk'):
    p=v.get('pos') or [0,0]
    print(v.get('check'), v.get('net'), v.get('refs'), 'uv=(%.1f,%.1f)'%(p[0]-x0,p[1]-y0), [i['msg'][:45] for i in v['items']])
P
