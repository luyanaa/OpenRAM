#!/usr/bin/env python3
"""Package an OpenRAM ICsprout55 macro for a Cadence/Calibre ASIC flow.

  package_macro.py OUTDIR MACRO DEST --stdcell-cdl CDL --meas MEAS.json
                   --words N --bits B [--ports 0,1] [--stdcell-lef LEF]

OUTDIR is the OpenRAM output directory of MACRO.  Writes into DEST:
  gds/MACRO.gds        supply labels renamed vdd->VDD, gnd->VSS (top cell)
  lef/MACRO.lef        DATABASE MICRONS 1000, FOREIGN/ORIGIN, VDD/VSS,
                       antenna gate/diffusion areas on the signal pins
  cdl/MACRO.cdl        LVS netlist; top supply ports VDD/VSS; the foundry
                       DFFQX1_9TSVT is *referenced* in the foundry port order
                       (include the 9T SVT library CDL, as for any std cell)
  spice/MACRO.sp       self-contained simulation netlist (VDD/VSS ports)
  lib/MACRO_<corner>.lib   from Spectre measurements (sim/mklib.py)
  verilog/MACRO.v      behavioural model (OpenRAM), VDD/VSS under USE_POWER_PINS
  verilog/MACRO_stub.v black box for synthesis / P&R netlists
"""
import argparse, os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DFF = "DFFQX1_9TSVT"
RENAME = {"vdd": "VDD", "gnd": "VSS"}


def gds(src, dst, top):
    import klayout.db as kdb
    ly = kdb.Layout(); ly.read(src)
    cell = ly.cell(top)
    n = 0
    for li in ly.layer_indexes():
        for s in cell.shapes(li).each():
            if s.is_text() and s.text_string in RENAME:
                s.text_string = RENAME[s.text_string]; n += 1
    opt = kdb.SaveLayoutOptions(); opt.gds2_max_vertex_count = 4000
    ly.write(dst, opt)
    return n


def foundry_ports(cdl):
    t = open(cdl).read().replace("\n+", " ")
    m = re.search(r"^\.SUBCKT\s+%s\s+([^\n]*)" % DFF, t, re.M | re.I)
    return m.group(1).split()


def netlist(src, dst, top, dff_ports=None):
    """Rename the top-level supplies; with dff_ports, drop the embedded DFF
    definition and reorder its instances to dff_ports."""
    t = open(src).read().replace("\n+", " ")
    out, ours = [], None
    blocks = re.split(r"(?=^\.SUBCKT )", t, flags=re.M | re.I)
    for b in blocks:
        m = re.match(r"\.SUBCKT\s+(\S+)\s*([^\n]*)", b, re.I)
        if m and m.group(1) == DFF:
            ours = m.group(2).split()
            if dff_ports:
                continue
        if m and m.group(1) == top:
            b = re.sub(r"(?<![\w\[\]/.])(vdd|gnd)(?![\w\[\]/.])", lambda x: RENAME[x.group(1)], b)
        out.append(b)
    text = "".join(out)
    if dff_ports:
        def fix(mm):
            nets = mm.group(2).split()
            byname = dict(zip(ours, nets))
            return mm.group(1) + " " + " ".join(byname[p] for p in dff_ports) + " " + DFF
        text = re.sub(r"^(X\S+)\s+([^\n]*?)\s+%s\s*$" % DFF, fix, text, flags=re.M)
        text = ("* %s requires the foundry %s subcircuit (ICsprout55 9T SVT CDL)\n"
                "* .INCLUDE <ICsprout55_9TSVT_basic.cdl>\n" % (top, DFF)) + text
    # wrap long lines again
    lines = []
    for l in text.split("\n"):
        while len(l) > 78 and not l.startswith("*"):
            cut = l.rfind(" ", 0, 78)
            if cut <= 0:
                break
            lines.append(l[:cut]); l = "+ " + l[cut + 1:]
        lines.append(l)
    open(dst, "w").write("\n".join(lines))


def lef(src, dst, top, gate_in, gate_clk, diff_out):
    t = open(src).read()
    t = re.sub(r"DATABASE MICRONS \d+", "DATABASE MICRONS 1000", t)
    # antenna statements need LEF 5.5+; NAMESCASESENSITIVE is obsolete in 5.6+
    t = t.replace("VERSION 5.4 ;", "VERSION 5.8 ;").replace("NAMESCASESENSITIVE ON ;\n", "")
    t = t.replace("   CLASS BLOCK ;\n", "   CLASS BLOCK ;\n   FOREIGN %s 0 0 ;\n   ORIGIN 0 0 ;\n" % top, 1)
    for a, b in RENAME.items():
        t = re.sub(r"\b(PIN|END) %s\b" % a, r"\1 %s" % b, t)

    def ant(mm):
        name, body = mm.group(1), mm.group(2)
        if name in RENAME.values():
            return mm.group(0)
        if "DIRECTION INPUT" in body:
            area = gate_clk if name == "clk0" else gate_in
            add = "      ANTENNAGATEAREA %.4f LAYER MET4 ;\n" % area
        elif "DIRECTION OUTPUT" in body:
            add = "      ANTENNADIFFAREA %.4f LAYER MET4 ;\n" % diff_out
        else:
            add = ""
        return "   PIN %s\n%s%s   END %s" % (name, body, add, name)
    t = re.sub(r"   PIN (\S+)\n(.*?)   END \1", ant, t, flags=re.S)
    # Block every routing layer the macro uses over its whole area (OpenRAM
    # writes per-shape obstructions with gaps): no SoC routing over bitlines
    # and sense nodes.  0.3 um inset keeps the edge pins reachable.
    w, h = map(float, re.search(r"SIZE ([0-9.]+) BY ([0-9.]+)", t).groups())
    obs = "   OBS\n" + "".join("   LAYER  %s ;\n      RECT  0.3 0.3 %.3f %.3f ;\n" % (l, w - 0.3, h - 0.3)
                             for l in ("MET1", "MET2", "MET3", "MET4", "MET5")) + "   END\n"
    t = re.sub(r"   OBS\n.*?   END\n", obs, t, flags=re.S)
    open(dst, "w").write(t)


def verilog(src, dst, stub, top, words, bits, ports):
    source = open(src).read()
    source = re.sub(r"\bvdd\b", "VDD", source)
    source = re.sub(r"\bgnd\b", "VSS", source)
    open(dst, "w").write("`timescale 1ns/1ps\n" + source)
    address_width = (words - 1).bit_length()
    declarations = []
    for port in ports:
        declarations.extend((
            "input clk{}".format(port),
            "input csb{}".format(port),
            "input web{}".format(port),
            "input [{}:0] addr{}".format(address_width - 1, port),
            "input [{}:0] din{}".format(bits - 1, port),
            "output [{}:0] dout{}".format(bits - 1, port),
        ))
    stub_lines = [
        "// Black box of {} ({} words x {} bits, {} read/write port(s)).".format(
            top, words, bits, len(ports)),
        "// Inputs register at rising edges; reads launch on falling edges.",
        "// Concurrent writes to the same physical row across ports are undefined.",
        "(* blackbox *)",
        "module {} (".format(top),
        "`ifdef USE_POWER_PINS",
        "    inout VDD,",
        "    inout VSS,",
        "`endif",
    ]
    stub_lines.extend("    {}{}".format(declaration, "," if index + 1 < len(declarations) else "")
                      for index, declaration in enumerate(declarations))
    stub_lines.extend((");", "endmodule", ""))
    open(stub, "w").write("\n".join(stub_lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("macro")
    ap.add_argument("dest")
    ap.add_argument("--stdcell-cdl", required=True)
    ap.add_argument("--meas", required=True)
    ap.add_argument("--words", type=int, required=True)
    ap.add_argument("--bits", type=int, required=True)
    ap.add_argument("--ports", default="0", help="comma-separated read/write port indices")
    ap.add_argument("--gate-in", type=float, default=0.0318, help="um2, DFF D input")
    ap.add_argument("--gate-clk", type=float, default=0.027, help="um2, clock buffer input")
    ap.add_argument("--diff-out", type=float, default=0.2, help="um2, sense-amp output driver")
    a = ap.parse_args()
    try:
        ports = [int(value) for value in a.ports.split(",")]
    except ValueError:
        ap.error("--ports must be a comma-separated list of indices")
    if not ports or min(ports) < 0 or len(set(ports)) != len(ports):
        ap.error("--ports must contain distinct non-negative indices")
    m, src, d = a.macro, a.outdir, a.dest
    for sub in ("gds", "lef", "cdl", "spice", "lib", "verilog"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    n = gds(os.path.join(src, m + ".gds"), os.path.join(d, "gds", m + ".gds"), m)
    print("gds: %d supply labels renamed" % n)
    lef(os.path.join(src, m + ".lef"), os.path.join(d, "lef", m + ".lef"), m, a.gate_in, a.gate_clk, a.diff_out)
    netlist(os.path.join(src, m + ".lvs.sp"), os.path.join(d, "cdl", m + ".cdl"), m, foundry_ports(a.stdcell_cdl))
    netlist(os.path.join(src, m + ".sp"), os.path.join(d, "spice", m + ".sp"), m)
    verilog(os.path.join(src, m + ".v"), os.path.join(d, "verilog", m + ".v"),
            os.path.join(d, "verilog", m + "_stub.v"), m, a.words, a.bits, ports)
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.read(os.path.join(src, m + ".gds"))
    bbox = ly.top_cell().dbbox()
    subprocess.run([
        sys.executable, os.path.join(HERE, "..", "sim", "mklib.py"),
        a.meas, m, os.path.join(d, "lib"),
        "--words", str(a.words), "--bits", str(a.bits),
        "--area", "{:.3f}".format(bbox.width() * bbox.height()),
        "--ports", ",".join(map(str, ports)),
        "--power-pins", "VDD,VSS",
    ], check=True)


if __name__ == "__main__":
    main()
