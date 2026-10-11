#!/bin/sh
# On the VM: build the RTL trace in DIR for MACRO.v
set -eu
dir=$1; vfile=$2
cd "$dir"
export LM_LICENSE_FILE=${LM_LICENSE_FILE:-/opt/synopsys/Synopsys.dat}
vcs -full64 -q tb.v "$vfile" -o simv -l vcs.log >/dev/null 2>&1
./simv -q > sim.log 2>&1
