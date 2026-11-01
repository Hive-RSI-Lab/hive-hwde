#!/bin/sh
# Rebuild the place_replicate fixtures (#482) from gen_fixture.py:
#   ok/      3 instances of ch.kicad_sch (R1 C1 R2 C2 per channel) + J1
#   mutant/  the same, but channel 3 is ch_x.kicad_sch with an extra C3
# Each board has J1 and channel 1 placed (source_place.ops.json) and
# channel 1 routed (source_route.ops.json, incl. a B.Cu GND hop with two
# vias); channels 2 and 3 sit where board_init packed them.
# Run from the repo root with the hwde env:  sh tests/fixtures/replicate/build.sh
set -e
here=tests/fixtures/replicate
cli=$(bin/hwde env | sed -n 's/^export HWDE_KICAD_CLI=//p' | tr -d "'\"")
for v in ok mutant; do
	d=$here/$v
	flag=; [ $v = mutant ] && flag=--mutant
	python3 $here/gen_fixture.py $d $flag
	"${cli:-kicad-cli}" sch export netlist $d/rep.kicad_sch -o $d/rep.net
	bin/hwde board_init --netlist $d/rep.net --name rep --out $d --layers 2 \
		--outline 40x36 --origin 100,100 --mounting-holes 0 \
		--schematic $d/rep.kicad_sch --allow-fixed-outline \
		--out-report /dev/null || true   # J1's packed spot touches the edge
	bin/hwde place_edit --pcb $d/rep.kicad_pcb --ops $here/source_place.ops.json
	[ $v = mutant ] && bin/hwde place_edit --pcb $d/rep.kicad_pcb \
		--op '{"op":"place","ref":"C303","x":121,"y":127,"deg":0}'
	bin/hwde route_edit --pcb $d/rep.kicad_pcb --ops $here/source_route.ops.json
	rm -f $d/*.lock $d/*.kicad_prl
done
