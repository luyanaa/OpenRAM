#!/bin/sh
# On the VM: run spectre on DIR/NAME.scs, results in DIR/NAME.raw, log DIR/NAME.log
set -u
dir=$1; name=$2; shift 2
cd "$dir"
export CDS_LIC_FILE=${CDS_LIC_FILE:-27002@127.0.0.1}
spectre "$name.scs" -raw "$name.raw" +log "$name.log" -format psfascii "$@" > /dev/null 2>&1
echo "$name rc=$?"
