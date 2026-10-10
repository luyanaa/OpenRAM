#!/usr/bin/env python3
"""Print DRC result locations from a Calibre ASCII results DB (drc_CAL.OUT).

Usage: drcwhere.py RUNDIR_or_OUT [rule-regex] [--max N]
Coordinates are printed in um (bounding box of each result polygon/edge).
"""
import os
import re
import sys


def parse(path):
    lines = open(path, errors="replace").read().split("\n")
    top, prec = lines[0].split()[:2]
    prec = float(prec)
    i = 1
    res = []
    while i < len(lines):
        name = lines[i].strip()
        if not name:
            i += 1
            continue
        try:
            cur, orig, nlines = [int(x) for x in lines[i + 1].split()[:3]]
        except (ValueError, IndexError):
            i += 1
            continue
        i += 2 + nlines
        for _ in range(cur):
            hdr = lines[i].split()
            kind, n = hdr[0], int(hdr[2])
            i += 1
            pts = []
            while i < len(lines) and len(pts) < n and not re.match(r"^[pe] ", lines[i]):
                t = lines[i].split()
                if len(t) >= 2 and re.match(r"^-?\d+$", t[0]):
                    if kind == "e":
                        pts += [(int(t[0]), int(t[1])), (int(t[2]), int(t[3]))]
                    else:
                        pts.append((int(t[0]), int(t[1])))
                elif t and t[0] == "CN":
                    pass
                else:
                    if len(t) and not re.match(r"^-?\d+$", t[0]):
                        i += 1
                        continue
                i += 1
                if kind == "e" and len(pts) >= 2 * n:
                    break
            xs = [p[0] / prec for p in pts]
            ys = [p[1] / prec for p in pts]
            if xs:
                res.append((name, min(xs), min(ys), max(xs), max(ys)))
    return res


def main():
    a = sys.argv[1:]
    mx = 10
    if "--max" in a:
        k = a.index("--max")
        mx = int(a[k + 1])
        del a[k:k + 2]
    path = a[0]
    if os.path.isdir(path):
        path = os.path.join(path, "tape_drc", "drc_CAL.OUT")
    pat = re.compile(a[1]) if len(a) > 1 else None
    seen = {}
    for name, x0, y0, x1, y1 in parse(path):
        if pat and not pat.search(name):
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] <= mx:
            print("{:16s} ({:.3f},{:.3f})-({:.3f},{:.3f})".format(name, x0, y0, x1, y1))


if __name__ == "__main__":
    main()
