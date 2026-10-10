#!/usr/bin/env python3
# See LICENSE for licensing information.
"""Import the ICsprout55 standard cells OpenRAM uses into gds_lib/sp_lib.

OpenRAM only needs the positive-edge D flip-flop from the foundry libraries.
This script copies that cell, unmodified except for the removal of the 7T
library's M1 *pin purpose* (81;2), which both Calibre decks map to M1 dummy
(MET1dmi) and therefore flag.  The pin geometry is also drawn on 81;1, so no
connectivity is lost.  The CDL is flattened to a 5-terminal transistor netlist
(D Q CK VDD VSS); the 9T library's separate well terminals are tied VNW=VDD and
VPW=VSS, which is how the surrounding OpenRAM well taps bias them.

Usage:
  import_stdcells.py --h7c-gds DIR --h7c-cdl DIR --sc9t DIR

  DIR for --h7c-gds holds ics55_LLSC_H7C{R,L,H}.gds (openecos release v1.10.102)
  DIR for --h7c-cdl holds ics55_LLSC_H7C{R,L,H}/cdl/*.cdl (icsprout55-pdk repo)
  DIR for --sc9t is ICsprout_55LLULP1225_STD_0818 (official 9T release)
"""
import argparse
import os
import re

import klayout.db as kdb

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# library key -> (cell name, gds, cdl)
def variants(a):
    v = {}
    for key, suf in (("H7CR", "H7R"), ("H7CL", "H7L"), ("H7CH", "H7H")):
        v[key] = ("DFFQX1" + suf,
                  os.path.join(a.h7c_gds, "ics55_LLSC_{}.gds".format(key)),
                  os.path.join(a.h7c_cdl, "ics55_LLSC_{0}/cdl/ics55_LLSC_{0}.cdl".format(key)))
    for key, vt in (("SC9T", "SVT"), ("SC9TL", "LVT"), ("SC9TH", "HVT")):
        base = os.path.join(a.sc9t, "ICsprout55_SC9T_BASIC_{}/0.1".format(vt))
        v[key] = ("DFFQX1_9T" + vt,
                  os.path.join(base, "gds/ICsprout55_9T{}_basic.gds".format(vt)),
                  os.path.join(base, "cdl/ICsprout55_9T{}_basic.cdl".format(vt)))
    return v


def import_gds(src, cell, dst):
    s = kdb.Layout()
    s.read(src)
    sc = s.cell(cell)
    assert sc is not None, (src, cell)
    d = kdb.Layout()
    d.dbu = s.dbu
    dc = d.create_cell(cell)
    dc.copy_tree(sc)
    pin = d.find_layer(81, 2)
    if pin is not None:
        for c in d.each_cell():
            c.shapes(pin).clear()
    d.write(dst)
    return dc.bbox()


def parse_cdl(path):
    subckts = {}
    lines = []
    for raw in open(path, errors="replace"):
        raw = raw.rstrip("\n")
        if raw.startswith("+") and lines:
            lines[-1] += " " + raw[1:]
        else:
            lines.append(raw)
    cur = None
    for l in lines:
        t = l.split()
        if not t or t[0].startswith("*"):
            continue
        if t[0].upper() == ".SUBCKT":
            params = {}
            ports = []
            for tok in t[2:]:
                if "=" in tok:
                    k, val = tok.split("=", 1)
                    params[k.lower()] = val
                else:
                    ports.append(tok)
            cur = dict(name=t[1], ports=ports, params=params, body=[])
            subckts[t[1]] = cur
        elif t[0].upper() == ".ENDS":
            cur = None
        elif cur is not None:
            cur["body"].append(t)
    return subckts


def num(v, env):
    v = env.get(v.lower(), v)
    m = re.match(r"^([-+0-9.eE]+)([a-zA-Z]*)$", str(v))
    if not m:
        raise ValueError(v)
    scale = {"": 1, "u": 1e-6, "n": 1e-9, "p": 1e-12, "m": 1e-3}[m.group(2).lower()]
    return float(m.group(1)) * scale


def flatten(subckts, name, nets, env, prefix, out, bulk_map):
    sc = subckts[name]
    local = dict(zip(sc["ports"], nets))
    menv = dict(sc["params"])
    menv.update(env)
    for t in sc["body"]:
        if t[0][0] in "Mm":
            d, g, s, b, model = t[1:6]
            kv = dict(x.split("=", 1) for x in t[6:] if "=" in x)
            kv = {k.lower(): v for k, v in kv.items()}
            w = num(kv["w"], menv)
            ln = num(kv["l"], menv)
            m = kv.get("m", kv.get("mr", "1"))
            def n(x):
                x = local.get(x, prefix + x)
                return bulk_map.get(x, x)
            out.append("M{}{} {} {} {} {} {} W={:.4g}u L={:.4g}u M={}".format(
                prefix.replace("/", "_"), t[0][1:], n(d), n(g), n(s), n(b), model,
                w * 1e6, ln * 1e6, m))
        elif t[0][0] in "Xx":
            toks = [x for x in t[1:] if x != "/"]
            args = [x for x in toks if "=" in x]
            pos = [x for x in toks if "=" not in x]
            sub = pos[-1]
            conns = [local.get(x, prefix + x) for x in pos[:-1]]
            penv = {}
            for a in args:
                k, v = a.split("=", 1)
                penv[k.lower()] = v
            flatten(subckts, sub, conns, penv, prefix + t[0] + "/", out, bulk_map)
        else:
            raise ValueError("unsupported CDL card: {}".format(t))


def import_sp(cdl, cell, dst, key):
    subckts = parse_cdl(cdl)
    sc = subckts[cell]
    ports = ["D", "Q", "CK", "VDD", "VSS"]
    nets = {p: p for p in sc["ports"]}
    bulk_map = {"VNW": "VDD", "VPW": "VSS"}
    out = []
    flatten(subckts, cell, [bulk_map.get(p, p) for p in sc["ports"]], {}, "", out, bulk_map)
    with open(dst, "w") as f:
        f.write("* {} imported from {} by import_stdcells.py\n".format(cell, os.path.basename(cdl)))
        f.write("* Flattened; port order D Q CK VDD VSS")
        if "VNW" in nets:
            f.write("; well terminals tied VNW=VDD, VPW=VSS")
        f.write("\n.SUBCKT {} {}\n".format(cell, " ".join(ports)))
        f.write("\n".join(out) + "\n.ENDS {}\n".format(cell))
    return len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h7c-gds", required=True)
    ap.add_argument("--h7c-cdl", required=True)
    ap.add_argument("--sc9t", required=True)
    a = ap.parse_args()
    for key, (cell, gds, cdl) in variants(a).items():
        bbox = import_gds(gds, cell, os.path.join(HERE, "gds_lib", cell + ".gds"))
        n = import_sp(cdl, cell, os.path.join(HERE, "sp_lib", cell + ".sp"), key)
        print("{:6s} {:14s} bbox={} devices={}".format(key, cell, bbox, n))


if __name__ == "__main__":
    main()
