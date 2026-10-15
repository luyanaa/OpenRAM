#!/usr/bin/env python3
"""Generate a Verilog access-pattern testbench for an OpenRAM macro.

The testbench drives OpenRAM's behavioural model (<macro>.v) with a
realistic, deterministic access sequence and writes trace.txt:

  C <cycle> <pin>=<value> ...     inputs applied for the rising edge of <cycle>
  E <cycle> <dout pin>=<bits>     expected read data, valid at the end of <cycle>

Access pattern (per macro):
  * write ports fill a set of addresses with patterns (all 0, all 1, 0101..,
    1010.., walking, pseudo random), several back to back;
  * read ports read those addresses, interleaved with writes to *other*
    addresses on the write port in the same cycle (1W+1R / 2RW);
  * read-after-write in the very next cycle, and re-reads (read stability).
Usage: tbgen.py MACRO.v OUTDIR [--cycles N] [--seed S]
"""
import argparse
import os
import random
import re


def parse_ports(vfile):
    txt = open(vfile).read()
    name_match = re.search(r"module\s+(\w+)", txt)
    aw_match = re.search(r"ADDR_WIDTH\s*=\s*(\d+)", txt)
    dw_match = re.search(r"DATA_WIDTH\s*=\s*(\d+)", txt)
    if not name_match or not aw_match or not dw_match:
        raise ValueError("behavioral model lacks module, ADDR_WIDTH, or DATA_WIDTH metadata")
    name, aw, dw = name_match.group(1), int(aw_match.group(1)), int(dw_match.group(1))
    ports = []
    for m in re.finditer(r"// Port (\d+): (RW|R|W)\s*\n\s*([^\n]+)", txt):
        idx, kind, sig = int(m.group(1)), m.group(2), m.group(3)
        ports.append(dict(idx=idx, kind=kind,
                          sigs=[s.strip() for s in sig.strip().rstrip(")").split(",") if s.strip()]))
    if not ports:
        raise ValueError("behavioral model has no // Port N: RW|R|W role metadata")
    indices = [port["idx"] for port in ports]
    if len(indices) != len(set(indices)):
        raise ValueError("behavioral model has duplicate port-role metadata")
    wmask = "wmask" in txt
    return name, aw, dw, ports, wmask


def patterns(dw, rnd):
    full = (1 << dw) - 1
    alt = int("01" * dw, 2) & full
    pats = [0, full, alt, full ^ alt, 1, 1 << (dw - 1)]
    pats += [rnd.getrandbits(dw) for _ in range(16)]
    return pats


def schedule(aw, dw, ports, ncyc, seed):
    """Return list of per-cycle dicts {port_idx: (op, addr, data)}."""
    rnd = random.Random(seed)
    depth = 1 << aw
    pats = patterns(dw, rnd)
    wports = [p for p in ports if p["kind"] in ("W", "RW")]
    rports = [p for p in ports if p["kind"] in ("R", "RW")]
    # address set: corners of the address space + random
    addrs = [0, depth - 1, 1, depth - 2, depth // 2, (depth // 2) - 1]
    while len(addrs) < min(depth, 12):
        a = rnd.randrange(depth)
        if a not in addrs:
            addrs.append(a)
    addrs = addrs[:min(depth, 12)]
    sched = [dict() for _ in range(ncyc)]
    written = {}
    # Characterization needs an isolated read and write from every eligible
    # port; the remaining cycles retain concurrent read/write coverage.
    required = len(wports) + (len(rports) if wports else 0)
    if ncyc < 1 + required + 2:
        raise ValueError("cycles must leave room for per-port isolated accesses and two idle cycles")
    k = 1
    for i, port in enumerate(wports):
        address = addrs[i % len(addrs)]
        data = pats[i % len(pats)]
        sched[k][port["idx"]] = ("W", address, data)
        written[address] = data
        k += 1
    for port in rports:
        if written:
            sched[k][port["idx"]] = ("R", next(iter(written)), None)
            k += 1
    wp = wports[0]
    # dedicated read port if any, else the RW port is time-shared
    rp = [p for p in rports if p is not wp]
    rp = rp[0] if rp else wp
    # The per-port isolated access preamble above advances k.
    if rp is wp:
        # single RW port: shorter fill, then read everything back
        addrs = addrs[:6]
    # phase 1: fill (with concurrent reads of earlier writes when possible)
    for i, a in enumerate(addrs):
        if k >= ncyc - 2:
            break
        d = pats[i % len(pats)]
        sched[k][wp["idx"]] = ("W", a, d)
        if rp is not wp and written:
            keys = [x for x in written if x != a]
            if keys:
                # mostly the previous write (read-after-write), else random
                ra = keys[-1] if i % 2 else keys[rnd.randrange(len(keys))]
                sched[k][rp["idx"]] = ("R", ra, None)
        written[a] = d
        k += 1
    if rp is wp:
        for a in addrs:
            if k >= ncyc - 2:
                break
            sched[k][wp["idx"]] = ("R", a, None)
            k += 1
    # phase 2: read-after-write and overwrite with complements
    while k < ncyc - 2:
        a = addrs[(k * 5) % len(addrs)]
        if rp is wp:
            # RW port: write then read the next cycle
            d = (~written[a]) & ((1 << dw) - 1) if k % 3 else pats[k % len(pats)]
            sched[k][wp["idx"]] = ("W", a, d)
            written[a] = d
            k += 1
            sched[k][wp["idx"]] = ("R", a, None)
            k += 1
        else:
            b = addrs[(k * 3 + 1) % len(addrs)]
            if b == a:
                b = addrs[(addrs.index(a) + 1) % len(addrs)]
            d = (~written[b]) & ((1 << dw) - 1)
            sched[k][wp["idx"]] = ("W", b, d)
            sched[k][rp["idx"]] = ("R", a, None)          # a written >= 1 cycle ago
            k += 1
            sched[k][rp["idx"]] = ("R", b, None)          # read-after-write
            written[b] = d
            k += 1
    return sched


def gen(vfile, outdir, ncyc, seed, clk_delay1=0.0):
    name, aw, dw, ports, wmask = parse_ports(vfile)
    sched = schedule(aw, dw, ports, ncyc, seed)
    os.makedirs(outdir, exist_ok=True)
    L = []
    L.append("`timescale 1ns/1ps")
    L.append("module tb;")
    decl = []
    for p in ports:
        i = p["idx"]
        decl += ["reg clk{0}; reg csb{0};".format(i),
                 "reg [{0}:0] addr{1};".format(aw - 1, i)]
        if p["kind"] == "RW":
            decl += ["reg web{0};".format(i)]
        if p["kind"] in ("W", "RW"):
            decl += ["reg [{0}:0] din{1};".format(dw - 1, i)]
        if p["kind"] in ("R", "RW"):
            decl += ["wire [{0}:0] dout{1};".format(dw - 1, i)]
    L += decl
    conns = []
    for p in ports:
        for s in p["sigs"]:
            conns.append(".{0}({0})".format(s))
    L.append("  {} #(.DELAY(0), .VERBOSE(0)) dut ({});".format(name, ", ".join(conns)))
    L.append("  integer f; integer cyc;")
    L.append("  initial begin")
    L.append('    f = $fopen("trace.txt");')
    for p in ports:
        i = p["idx"]
        L.append("    clk{0}=0; csb{0}=1; addr{0}=0;".format(i))
        if p["kind"] == "RW":
            L.append("    web{0}=1;".format(i))
        if p["kind"] in ("W", "RW"):
            L.append("    din{0}=0;".format(i))
    for k, cyc in enumerate(sched):
        # inputs for the rising edge of cycle k (applied half a cycle before)
        assigns = []
        fields = []
        for p in ports:
            i = p["idx"]
            op = cyc.get(i)
            csb = 0 if op else 1
            addr = op[1] if op else 0
            fields.append("role{}={}".format(i, p["kind"]))
            fields.append("op{}={}".format(i, op[0] if op else "I"))
            fields.append("csb{0}={1}".format(i, csb))
            fields.append("addr{0}={1:0{2}b}".format(i, addr, aw))
            if p["kind"] == "RW":
                web = 0 if (op and op[0] == "W") else 1
                assigns.append("web{0}={1};".format(i, web))
                fields.append("web{0}={1}".format(i, web))
            if p["kind"] in ("W", "RW"):
                d = op[2] if (op and op[0] == "W") else 0
                assigns.append("din{0}={1}'d{2};".format(i, dw, d))
                fields.append("din{0}={1:0{2}b}".format(i, d, dw))
        L.append("    " + " ".join(assigns))
        L.append('    $fdisplay(f, "C {} {}");'.format(k, " ".join(fields)))
        L.append("    #5; " + " ".join("clk{}=1;".format(p["idx"]) for p in ports))
        L.append("    #5; " + " ".join("clk{}=0;".format(p["idx"]) for p in ports))
        # expected data at the end of this cycle (model's dout before next edge)
        for p in ports:
            i = p["idx"]
            op = cyc.get(i)
            if p["kind"] in ("R", "RW") and op and op[0] == "R":
                L.append('    #1 $fdisplay(f, "E {} dout{}=%b", dout{});'.format(k, i, i))
    L.append("    $fclose(f); $finish;")
    L.append("  end")
    L.append("endmodule")
    open(os.path.join(outdir, "tb.v"), "w").write("\n".join(l for l in L if l is not None) + "\n")
    meta = dict(name=name, aw=aw, dw=dw, ports=ports, ncyc=ncyc)
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("vfile")
    ap.add_argument("outdir")
    ap.add_argument("--cycles", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    print(gen(a.vfile, a.outdir, a.cycles, a.seed))
