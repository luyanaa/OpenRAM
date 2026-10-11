#!/bin/sh
# On the Calibre host: LVS + xRC extraction of a macro into WORKDIR/pex.sp.
#   vmpex.sh WORKDIR GDS CDL TOP RULESDIR DECKDIR [rcc|c] [vc]
# RULESDIR: the xRC corner directory with rules.S/C/R (e.g. .../TYP).
# DECKDIR: holds tape_pex.svrf and subckt.sp (verify/mkdecks.sh).
# rcc = R+C, c = C only (R adds ~2% delay on these macros, C is much faster).
# vc: join same-named supply labels (macro built with route_supplies="pins").
# Afterwards drop the parasitic well diodes: clean_pex.py pex.sp pex_clean.sp
set -u
W=$1; GDS=$2; CDL=$3; TOP=$4; RULES=$5; DECKS=$6; MODE=${7:-rcc}; VC=${8:-}
export MGLS_LICENSE_FILE=${MGLS_LICENSE_FILE:-28000@127.0.0.1}
C=${CALIBRE:-/home/user/calibre/aoi_cal_2020.2_14.12/bin/calibre}
mkdir -p "$W"; cd "$W"
cp "$GDS" layout.gds; cp "$CDL" source.cdl
sed -e "s|@TOP@|$TOP|g; s|@GDS@|layout.gds|; s|@CDL@|source.cdl|; s|@RULES@|$RULES|g" "$DECKS/tape_pex.svrf" > runset
cp "$DECKS/subckt.sp" .
if [ "$VC" = vc ]; then sed -i 's/^VIRTUAL CONNECT REPORT YES/VIRTUAL CONNECT REPORT YES\nVIRTUAL CONNECT NAME "vdd" "gnd"/' runset; fi
$C -lvs -hier -spice svdb/$TOP.sp -turbo 4 -nowait runset > lvs.log 2>&1
$C -xrc -pdb -$MODE -turbo 4 -nowait runset > pdb.log 2>&1
$C -xrc -fmt -$MODE -nowait runset > fmt.log 2>&1
grep -m1 "CORRECT\|INCORRECT" lvs.rep; ls -la pex.sp* 2>/dev/null; tail -3 fmt.log
