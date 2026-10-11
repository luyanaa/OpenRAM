#!/bin/sh
# Prepare the Calibre decks used by cal.py from the original foundry decks.
#   mkdecks.sh TAPE_DRC TAPE_LVS OS_DRC OS_LVS OS_SUBCKT_SP [WORKDIR]
# Edits: layout/source path and primary -> placeholders; Density_rules NO and
# CHIPEDGE_CHECK NO (chip-level checks); the tapeout LVS lvs_box.list include
# (chip-specific IO boxes) is commented out.  Nothing else is changed.
# tape_pex.svrf (for ../sim/vmpex.sh) is the tapeout LVS deck switched to
# xRC: RC_EXTRACT TRUE, flow XRC, one HSPICE netlist "pex.sp" with source
# names, and the rules.S/C/R of a corner directory (@RULES@) included.
set -eu
W=${6:-ics55_work}
mkdir -p "$W/decks"
fixdrc='s/^#DEFINE CHIPEDGE_CHECK YES/#DEFINE CHIPEDGE_CHECK NO /; s/^#DEFINE Density_rules YES/#DEFINE Density_rules NO /; s/^LAYOUT PRIMARY ".*"/LAYOUT PRIMARY "@TOP@"/; s/^LAYOUT PATH ".*"/LAYOUT PATH "@GDS@"/'
fixlvs='s/^LAYOUT PRIMARY ".*"/LAYOUT PRIMARY "@TOP@"/; s/^LAYOUT PATH ".*"/LAYOUT PATH "@GDS@"/; s/^SOURCE PRIMARY ".*"/SOURCE PRIMARY "@TOP@"/; s/^SOURCE PATH ".*"/SOURCE PATH "@CDL@"/; s|^INCLUDE "lvs_box.list"|//INCLUDE "lvs_box.list"|'
sed "$fixdrc" "$1" > "$W/decks/tape_drc.svrf"
sed "$fixlvs" "$2" > "$W/decks/tape_lvs.svrf"
sed "$fixdrc" "$3" > "$W/decks/os_drc.svrf"
sed "$fixlvs" "$4" > "$W/decks/os_lvs.svrf"
cp "$5" "$W/decks/subckt.sp"
sed -e 's/^#DEFINE RC_EXTRACT FALSE/#DEFINE RC_EXTRACT TRUE/; s/^#DEFINE RC_EXTRACT_FLOW CCI/#DEFINE RC_EXTRACT_FLOW XRC/' \
    -e '/^PEX NETLIST LUMPED /d' \
    -e 's/^PEX NETLIST DISTRIBUTED .*/PEX NETLIST "pex.sp" HSPICE 1 SOURCENAMES GROUND gnd/' \
    -e 's|^//DRC ICSTATION YES|//DRC ICSTATION YES\nINCLUDE "@RULES@/rules.S"\nINCLUDE "@RULES@/rules.C"\nINCLUDE "@RULES@/rules.R"|' \
    "$W/decks/tape_lvs.svrf" > "$W/decks/tape_pex.svrf"
grep -c "@TOP@" "$W"/decks/*.svrf
