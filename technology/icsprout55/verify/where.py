#!/usr/bin/env python3
"""Map DRC markers to the deepest cell instance containing them.
Usage: where.py RUNDIR GDS [rule-regex]"""
import sys, re, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drcwhere
import klayout.db as kdb
rundir, gds = sys.argv[1], sys.argv[2]
pat = re.compile(sys.argv[3]) if len(sys.argv) > 3 else None
ly = kdb.Layout(); ly.read(gds); top = ly.top_cell()
def deepest(pt):
    cell, trans, path = top, kdb.ICplxTrans(), []
    while True:
        found = None
        for inst in cell.each_inst():
            t = trans * inst.cplx_trans
            b = inst.cell.bbox().transformed(t)
            if b.contains(pt) and inst.cell.bbox().area() > 0:
                found = (inst.cell, t); break
        if not found: return "/".join(path) or top.name
        cell, trans = found; path.append(cell.name.replace(top.name + "_", ""))
cnt = collections.Counter()
for name, x0, y0, x1, y1 in drcwhere.parse(os.path.join(rundir, "tape_drc", "drc_CAL.OUT")):
    if pat and not pat.search(name): continue
    pt = kdb.Point(int((x0 + x1) / 2 / ly.dbu), int((y0 + y1) / 2 / ly.dbu))
    cnt[(name, deepest(pt))] += 1
for (r, c), n in sorted(cnt.items(), key=lambda x: (-x[1])):
    print("{:5d} {:16s} {}".format(n, r, c))
