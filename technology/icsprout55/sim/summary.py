#!/usr/bin/env python3
"""Summarize sweep results: per macro and corner, min passing period, longest
failing period, and access time (from the clock rise and from the fall).
Usage: summary.py SWEEPDIR [SWEEPDIR ...]"""
import json, os, sys

rows = {}
for tag in sys.argv[1:]:
    p = os.path.join(tag, "results.jsonl")
    if not os.path.exists(p):
        continue
    for line in open(p):
        r = json.loads(line)
        key = (r["macro"], r["corner"], r.get("skew", 0))
        rows.setdefault(key, {})[r["period"]] = r      # last result wins
print("{:14s} {:15s} {:>9s} {:>9s} {:>10s} {:>10s}".format(
    "macro", "corner", "Tmin_pass", "Tmax_fail", "tacc@Tmin", "tacc-T/2") + "  fails_above_Tmin")
for (m, c, _), d in sorted(rows.items()):
    passes = sorted(T for T, r in d.items() if r.get("passed"))
    fails = sorted(T for T, r in d.items() if not r.get("passed"))
    tmin = passes[0] if passes else None
    tf = max([T for T in fails if tmin is None or T < tmin], default=None)
    tacc = d[tmin]["tacc_ns"] if tmin else None
    print("{:14s} {:15s} {!s:>9} {!s:>9} {!s:>10} {!s:>10}".format(
        m, c, tmin, tf, tacc, None if tacc is None else round(tacc - tmin / 2, 3))
          + "  " + ",".join(str(T) for T in fails if tmin is not None and T > tmin))
