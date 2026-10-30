#!/bin/sh
# Rebuild lite-main from its sources, in hwde's order. Run from boards/lite-main:
#   sh build.sh sch      footprint repairs, schematic, ERC gate, netlist, parts.json
#   sh build.sh board    fresh board: board_init, rules_gen, placement, antenna
#                        keep-out, pours, power vias (unrouted)
#   sh build.sh route    Freerouting via route_auto, then stitch_vias
# hwde is the module's runner (hive-hwde/bin/hwde); HWDE overrides it.
set -e
cd "$(dirname "$0")"
HWDE=${HWDE:-$HIVE_MODULES/hive-hwde/bin/hwde}
h() { sh "$HWDE" "$@"; }
eval "$(h env)"
# KiCadRoutingTools is in the Hive's tools folder; bin/hwde doesn't point at it yet
[ -d /hive/tools/kicad-routing-tools ] && export HWDE_KRT_DIR=${HWDE_KRT_DIR:-$(ls -d /hive/tools/kicad-routing-tools/KiCadRoutingTools-*/plugins | tail -n 1)}
kpy() { xvfb-run -a -s "-extension GLX" "$(dirname "$HWDE_KICAD_CLI")/python3" "$@"; }
step=${1:-sch}
case "$step" in
sch)
	python3 lib/fix_footprints.py lib/aiee.pretty >/dev/null
	python3 lib/fix_courtyards.py lib/aiee.pretty >/dev/null
	h lib_pin_types --lib lib/aiee.kicad_sym >/dev/null
	PYTHONPATH=$HWDE_SKILL/scripts:$HWDE_SKILL/scripts/lib xvfb-run -a -s "-extension GLX" "$HWDE_PY" kicad/gen/root.py
	h gate --gate erc kicad/lite-main.kicad_sch --workspace . --out reports/erc-gate.json >/dev/null || { echo "ERC failed: reports/erc-gate.json"; exit 1; }
	h kc netlist kicad/lite-main.kicad_sch --out kicad/lite-main.net >/dev/null
	h netlist_audit --netlist kicad/lite-main.net --constraints kicad/constraints.json --decoupling kicad/decoupling.json --out reports/netlist-audit.json >/dev/null
	python3 kicad/gen/parts_json.py "$HWDE"
	;;
board)
	rm -f kicad/lite-main.kicad_pcb
	h board_init --netlist kicad/lite-main.net --name lite-main --out kicad --layers 2 --outline 100x90 \
		--mounting-holes 4 --schematic kicad/lite-main.kicad_sch --fp-lib lib --workspace . \
		--out-report reports/board-init.json >/dev/null || true   # self-check fails until placed
	h rules_gen --constraints kicad/constraints.json --layers 2 --out-dru kicad/lite-main.kicad_dru \
		--pro kicad/lite-main.kicad_pro >reports/rules-gen.json
	python3 kicad/gen/pro_clearance.py kicad/lite-main.kicad_pro >/dev/null
	python3 kicad/gen/place.py >kicad/place-ops.json
	h place_edit --pcb kicad/lite-main.kicad_pcb --ops kicad/place-ops.json --out-report reports/place-edit.json >/dev/null
	kpy kicad/gen/keepout.py kicad/lite-main.kicad_pcb >/dev/null
	python3 kicad/gen/power.py >/dev/null
	h planes_gen --pcb kicad/lite-main.kicad_pcb --out-report route/planes_gen.json >/dev/null
	h route_edit --pcb kicad/lite-main.kicad_pcb --ops kicad/power-vias.json --out-report route/power-vias.json >/dev/null
	;;
route)
	cp kicad/lite-main.kicad_pcb routing/pre-route_auto.kicad_pcb
	h route_auto --pcb kicad/lite-main.kicad_pcb --out-report route/route_auto.json >route/route_auto.stdout 2>&1 || true
	cp kicad/lite-main.kicad_pcb routing/post-route_auto.kicad_pcb
	h stitch_vias --pcb kicad/lite-main.kicad_pcb --out-report route/stitch_vias.json >/dev/null || true
	h planes_gen --pcb kicad/lite-main.kicad_pcb --constraints route/post-route-planes.json --out-report route/planes_gen_post.json >/dev/null || true
	;;
esac
# after routing: drop stitch vias under the hole-to-hole floor (see kicad/gen/fix_holes.py)
if [ "$step" = route ]; then
	h gate --gate drc_routed kicad/lite-main.kicad_pcb --workspace . --out reports/drc_routed-gate.json >/dev/null 2>&1 || true
	python3 kicad/gen/fix_holes.py >route/fix-holes.json
	h route_edit --pcb kicad/lite-main.kicad_pcb --ops route/fix-holes.json --out-report route/fix-holes-report.json >/dev/null || true
fi
# finish: plane repair, 45-degree snap, silk (sh build.sh finish)
if [ "$step" = finish ]; then
	cp kicad/lite-main.kicad_pcb routing/post-route_auto.kicad_pcb
	h plane_repair --pcb kicad/lite-main.kicad_pcb --out-report route/plane_repair.json >/dev/null
	h route_cleanup --pcb kicad/lite-main.kicad_pcb --snap-only --constraints kicad/constraints.json --out-report route/route_cleanup_snap.json >/dev/null
	cp kicad/lite-main.kicad_pcb routing/pre-silk.kicad_pcb
	h silk_place --pcb kicad/lite-main.kicad_pcb --apply --ops-out kicad/silk_ops.json --out-report reports/silk-place.json >/dev/null || true
	python3 kicad/gen/silk.py >kicad/silk-fix-ops.json
	h place_edit --pcb kicad/lite-main.kicad_pcb --ops kicad/silk-fix-ops.json --allow-routed --out-report reports/silk-fix.json >/dev/null
	kpy kicad/gen/snap_outline.py kicad/lite-main.kicad_pcb
	cp kicad/lite-main.kicad_pcb routing/post-silk.kicad_pcb
fi
# fab package: Gerbers, drill, BOM, CPL (sh build.sh fab); then the dfm gate
if [ "$step" = fab ]; then
	h fab_export --pcb kicad/lite-main.kicad_pcb --out-dir fab --out reports/fab-export.json >/dev/null
	h bom_cpl --pcb kicad/lite-main.kicad_pcb --out-dir fab --parts parts/parts.json --easyeda-cache parts/easyeda --out reports/bom-cpl.json >/dev/null || true
	h gate --gate dfm kicad/lite-main.kicad_pcb --workspace . --out reports/dfm-gate.json
fi
