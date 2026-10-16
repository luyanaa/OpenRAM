#!/usr/bin/env python3
"""Package an OpenRAM ICsprout55 macro for a Cadence/Calibre ASIC flow.

  package_macro.py OUTDIR MACRO DEST --stdcell-library LIB --stdcell-cdl CDL
                   --meas MEAS.json --words N --bits B [--ports 0,1]

OUTDIR is the OpenRAM output directory of MACRO.  Writes into DEST:
  gds/MACRO.gds        supply labels renamed vdd->VDD, gnd->VSS (top cell)
  lef/MACRO.lef        flow-ready LEF
  cdl/MACRO.cdl        LVS netlist using the selected foundry DFF
  spice/MACRO.sp       self-contained simulation netlist (VDD/VSS ports)
  lib/MACRO_<corner>.lib   role-aware Liberty from Spectre measurements
  verilog/MACRO.v      behavioural model (OpenRAM)
  verilog/MACRO_stub.v black box with behavioral-model port roles
"""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.abspath(os.path.join(HERE, "..", "sim"))
sys.path.insert(0, SIM)
from tbgen import parse_ports

# Kept in sync with technology/icsprout55/tech/tech.py.
STDCELL_DFF = {
    "SC9T": "DFFQX1_9TSVT",
    "SC9TL": "DFFQX1_9TLVT",
    "SC9TH": "DFFQX1_9THVT",
    "H7CR": "DFFQX1H7R",
    "H7CL": "DFFQX1H7L",
    "H7CH": "DFFQX1H7H",
}
STDCELL_ALIASES = {"9T": "SC9T", "SC9TSVT": "SC9T", "7T": "H7CR"}
FOUNDRY_DFF_PORTS = ("CK", "D", "Q", "VDD", "VSS")
RENAME = {"vdd": "VDD", "gnd": "VSS"}


def normalize_stdcell_library(value):
    library = value.upper()
    library = STDCELL_ALIASES.get(library, library)
    if library not in STDCELL_DFF:
        raise ValueError("unsupported ICsprout55 stdcell library '{}'; choose one of {}".format(
            value, ", ".join(STDCELL_DFF)))
    return library


def parse_ports_argument(value):
    try:
        ports = [int(item) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "ports must be a comma-separated list of indices") from error
    if not ports or min(ports) < 0 or len(set(ports)) != len(ports):
        raise argparse.ArgumentTypeError("ports must be distinct non-negative indices")
    return ports


def behavioral_ports(path, macro=None):
    name, address_width, data_width, ports, _ = parse_ports(path)
    if macro and name != macro:
        raise ValueError("behavioral model declares module {}; expected {}".format(name, macro))
    if not ports:
        raise ValueError("behavioral model has no // Port N: RW|R|W metadata")
    indices = [port["idx"] for port in ports]
    if len(indices) != len(set(indices)):
        raise ValueError("behavioral model contains duplicate port-role metadata")
    for port in ports:
        index, role = port["idx"], port["kind"]
        expected = {"clk{}".format(index), "csb{}".format(index), "addr{}".format(index)}
        if role in ("RW", "W"):
            expected.add("din{}".format(index))
        if role == "RW":
            expected.add("web{}".format(index))
        if role in ("RW", "R"):
            expected.add("dout{}".format(index))
        if set(port["sigs"]) != expected:
            raise ValueError("behavioral model port {} ({}) has signals {}, expected {}".format(
                index, role, ",".join(port["sigs"]), ",".join(sorted(expected))))
    return name, address_width, data_width, ports


def port_roles_argument(ports):
    return ",".join("{}:{}".format(port["idx"], port["kind"]) for port in ports)


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


def foundry_ports(cdl, dff):
    text = open(cdl).read()
    text = re.sub(r"\r?\n\+\s*", " ", text)
    matches = re.findall(
        r"^\.SUBCKT\s+{}\s+([^\n]*)".format(re.escape(dff)),
        text, re.M | re.I)
    if not matches:
        raise ValueError("standard-cell CDL lacks expected subcircuit {}".format(dff))
    if len(matches) != 1:
        raise ValueError("standard-cell CDL defines {} more than once".format(dff))
    ports = tuple(token.upper() for token in matches[0].split())
    if ports != FOUNDRY_DFF_PORTS:
        raise ValueError(
            "standard-cell CDL {} terminal order is {}; expected {}".format(
                dff, " ".join(ports), " ".join(FOUNDRY_DFF_PORTS)))
    return FOUNDRY_DFF_PORTS


def netlist(src, dst, top, dff=None, dff_ports=None, stdcell_library=None):
    """Rename supplies and replace the embedded DFF with the selected cell."""
    text = open(src).read().replace("\r\n", "\n").replace("\n+", " ")
    out, source_ports = [], None
    blocks = re.split(r"(?=^\.SUBCKT\s)", text, flags=re.M | re.I)
    for block in blocks:
        match = re.match(r"\.SUBCKT\s+(\S+)\s*([^\n]*)", block, re.I)
        if match and dff and match.group(1).upper() == dff.upper():
            source_ports = match.group(2).split()
            continue
        if match and match.group(1).upper() == top.upper():
            block = re.sub(
                r"(?<![\w\[\]/.])(vdd|gnd)(?![\w\[\]/.])",
                lambda item: RENAME[item.group(1).lower()], block, flags=re.I)
        out.append(block)
    result = "".join(out)

    if dff_ports is not None:
        if not dff or not source_ports:
            raise ValueError("source LVS netlist lacks embedded DFF subcircuit {}".format(dff))
        aliases = {"CLK": "CK", "GND": "VSS"}
        source_names = [aliases.get(pin.upper(), pin.upper()) for pin in source_ports]
        if len(source_names) != len(set(source_names)):
            raise ValueError("embedded DFF {} has ambiguous terminal names".format(dff))
        missing = [pin for pin in dff_ports if pin not in source_names]
        if missing:
            raise ValueError("embedded DFF {} lacks terminals {}".format(
                dff, ", ".join(missing)))
        replacements = 0

        def reorder(instance):
            nonlocal replacements
            nets = instance.group(2).split()
            if len(nets) != len(source_ports):
                raise ValueError("embedded DFF {} instance has {} nets; expected {}".format(
                    dff, len(nets), len(source_ports)))
            byname = dict(zip(source_names, nets))
            replacements += 1
            return "{} {} {}".format(
                instance.group(1),
                " ".join(byname[pin] for pin in dff_ports),
                dff)

        result = re.sub(
            r"^(X\S+)\s+([^\n]*?)\s+{}\s*$".format(re.escape(dff)),
            reorder, result, flags=re.M | re.I)
        if not replacements:
            raise ValueError("source LVS netlist has no instances of {}".format(dff))
        library_note = normalize_stdcell_library(stdcell_library or "")
        result = ("* {} uses {} from ICsprout55 {} CDL\n"
                  "* .INCLUDE <selected ICsprout55 standard-cell CDL>\n".format(
                      top, dff, library_note)) + result

    lines = []
    for line in result.split("\n"):
        while len(line) > 78 and not line.startswith("*"):
            cut = line.rfind(" ", 0, 78)
            if cut <= 0:
                break
            lines.append(line[:cut])
            line = "+ " + line[cut + 1:]
        lines.append(line)
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
            area = gate_clk if re.fullmatch(r"clk\d+", name) else gate_in
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
        index = port["idx"]
        for signal in port["sigs"]:
            if signal in ("addr{}".format(index),):
                declarations.append("input [{}:0] {}".format(address_width - 1, signal))
            elif signal in ("din{}".format(index), "dout{}".format(index)):
                direction = "output" if signal.startswith("dout") else "input"
                declarations.append("{} [{}:0] {}".format(direction, bits - 1, signal))
            else:
                direction = "output" if signal.startswith("dout") else "input"
                declarations.append("{} {}".format(direction, signal))
    roles = ", ".join("{}:{}".format(port["idx"], port["kind"]) for port in ports)
    stub_lines = [
        "// Black box of {} ({} words x {} bits, roles {}).".format(
            top, words, bits, roles),
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
    ap.add_argument("--stdcell-library", required=True,
                    help="ICsprout55 library key from tech.py (for example SC9T or H7CR)")
    ap.add_argument("--stdcell-cdl", required=True)
    ap.add_argument("--meas", required=True)
    ap.add_argument("--words", type=int, required=True)
    ap.add_argument("--bits", type=int, required=True)
    ap.add_argument("--ports", type=parse_ports_argument,
                    help="optional assertion of behavioral-model port indices")
    ap.add_argument("--gate-in", type=float, default=0.0318, help="um2, DFF D input")
    ap.add_argument("--gate-clk", type=float, default=0.027, help="um2, clock buffer input")
    ap.add_argument("--diff-out", type=float, default=0.2, help="um2, sense-amp output driver")
    a = ap.parse_args()
    try:
        library = normalize_stdcell_library(a.stdcell_library)
        _, model_address_width, model_data_width, ports = behavioral_ports(
            os.path.join(a.outdir, a.macro + ".v"), a.macro)
        if a.words <= 0 or a.bits <= 0:
            raise ValueError("--words and --bits must be positive")
        if model_address_width != (a.words - 1).bit_length() or model_data_width != a.bits:
            raise ValueError(
                "package dimensions {}x{} do not match behavioral model widths {}x{}".format(
                    a.words, a.bits, model_address_width, model_data_width))
        dff = STDCELL_DFF[library]
        dff_ports = foundry_ports(a.stdcell_cdl, dff)
    except ValueError as error:
        ap.error(str(error))
    port_indices = [port["idx"] for port in ports]
    if a.ports is not None and set(a.ports) != set(port_indices):
        ap.error("--ports {} does not match behavioral model ports {}".format(
            ",".join(map(str, a.ports)), ",".join(map(str, port_indices))))
    m, src, dest = a.macro, a.outdir, a.dest
    for sub in ("gds", "lef", "cdl", "spice", "lib", "verilog"):
        os.makedirs(os.path.join(dest, sub), exist_ok=True)
    n = gds(os.path.join(src, m + ".gds"), os.path.join(dest, "gds", m + ".gds"), m)
    print("gds: %d supply labels renamed" % n)
    lef(os.path.join(src, m + ".lef"), os.path.join(dest, "lef", m + ".lef"), m,
        a.gate_in, a.gate_clk, a.diff_out)
    netlist(os.path.join(src, m + ".lvs.sp"), os.path.join(dest, "cdl", m + ".cdl"),
            m, dff=dff, dff_ports=dff_ports, stdcell_library=library)
    netlist(os.path.join(src, m + ".sp"), os.path.join(dest, "spice", m + ".sp"), m)
    verilog(os.path.join(src, m + ".v"), os.path.join(dest, "verilog", m + ".v"),
            os.path.join(dest, "verilog", m + "_stub.v"), m, a.words, a.bits, ports)
    import klayout.db as kdb
    ly = kdb.Layout()
    ly.read(os.path.join(src, m + ".gds"))
    bbox = ly.top_cell().dbbox()
    subprocess.run([
        sys.executable, os.path.join(HERE, "..", "sim", "mklib.py"),
        a.meas, m, os.path.join(dest, "lib"),
        "--words", str(a.words), "--bits", str(a.bits),
        "--area", "{:.3f}".format(bbox.width() * bbox.height()),
        "--ports", ",".join(map(str, port_indices)),
        "--port-roles", port_roles_argument(ports),
        "--stdcell-library", library,
        "--power-pins", "VDD,VSS",
    ], check=True)


if __name__ == "__main__":
    main()

