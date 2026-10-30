#!/bin/sh
# Route up to N times from a fresh board, keep the run with the fewest
# non-silk drc_routed findings (Freerouting doesn't route the same way twice)
cd "$(dirname "$0")/.."
N=${1:-4}; best=99999; bi=0; i=1
while [ $i -le $N ]; do
  if nice -n 19 sh build.sh board >/dev/null 2>&1 && nice -n 19 sh build.sh route >/dev/null 2>&1; then
    nice -n 19 sh $HIVE_MODULES/hive-hwde/bin/hwde gate --gate drc_routed kicad/lite-main.kicad_pcb --workspace . --out /tmp/try$i.json >/dev/null 2>&1
    n=$(python3 -c "import json;d=json.load(open('/tmp/try$i.json'));print(sum(1 for v in d['failing'] if not v['check'].startswith('silk')))")
    cp kicad/lite-main.kicad_pcb /tmp/try$i.kicad_pcb; cp route/route_auto.json /tmp/try${i}_route.json
    echo "attempt $i: $n non-silk"
    if [ "$n" -lt "$best" ]; then best=$n; bi=$i; fi
    [ "$n" -le 0 ] && break
  else echo "attempt $i: build failed"; fi
  i=$((i+1))
done
cp /tmp/try$bi.kicad_pcb kicad/lite-main.kicad_pcb; cp /tmp/try${bi}_route.json route/route_auto.json
echo "best $best (attempt $bi)"
