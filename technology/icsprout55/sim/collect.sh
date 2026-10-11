#!/bin/sh
# collect.sh SWEEPDIR MACRO : check every run in parallel and rewrite SWEEPDIR/results.jsonl
d=$(cd "$1" && pwd); m=$2
cd "$(dirname "$0")"
ls -d $d/*.raw | sed 's|.*/||; s|\.raw$||' | xargs -P 8 -I{} sh -c '
n={}; T=$(echo $n | sed "s/.*_T\([0-9p]*\).*/\1/; s/p/./"); c=$(echo $n | sed "s/_T[0-9p]*.*//")
v=$(echo $c | sed "s/.*_\([0-9]\)p\([0-9]*\)_.*/\1.\2/")
python3 check.py '"$d"'/$n.raw '"$d"'/trace.txt --period $T --vdd $v 2>/dev/null | tail -1 |
python3 -c "import json,sys; r=json.loads(sys.stdin.read() or \"{}\"); r.update(corner=\"$c\", period=float(\"$T\"), run=\"$n\", macro=\"'"$m"'\"); r.setdefault(\"passed\", False); print(json.dumps(r))"' | sort > $d/results.jsonl.new
mv $d/results.jsonl.new $d/results.jsonl
python3 summary.py $d
