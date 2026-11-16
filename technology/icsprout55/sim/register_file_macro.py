#!/usr/bin/env python3
"""Generate and phase-check an ICSprout55 32x8 replicated 2R1W macro wrapper.

The storage leaf is always the real OpenRAM-generated 1W1R macro configured by
``configs/ics55_1w1r_32x8.py``.  Two instances are used: each reader owns one
copy and the writer is broadcast.  The generated Icarus regression models the
macro's positive-edge input sampling and delayed negative-edge read access; its
DELAY/T_HOLD values are arbitrary behavioral-model values, not characterized
silicon timing.
"""
import argparse
import hashlib
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[3]
TECH = ROOT / "technology" / "icsprout55"
CONFIG_PATH = TECH / "configs" / "ics55_1w1r_32x8.py"
COMPILER_PATH = ROOT / "sram_compiler.py"
RUNS_PATH = ROOT / "ics55_work" / "runs"
MACRO_NAME = "ics55_register_file_macro_1w1r_32x8"
WRAPPER_MODULE = "rf_ics55_2r1w_macro_32x8"
TESTBENCH_MODULE = "tb_ics55_register_file_macro"
REPORT_NAME = "register_file_macro_32x8.json"
PDK_ROOT = ROOT.parent / "icsprout55-pdk"
DEFAULT_TECH_LEF = PDK_ROOT / "libs.tech" / "librelane" / "N551P6M_ecos.lef"
DEFAULT_H7CR_ROOT = PDK_ROOT / "libs.ref" / "ics55_LLSC_H7CR"
DEFAULT_H7CR_LEF = DEFAULT_H7CR_ROOT / "lef" / "ics55_LLSC_H7CR_ecos.lef"
DEFAULT_H7CR_LIBERTY = RUNS_PATH / "ics55_next_acceptance" / "release" / "liberty" / "ics55_LLSC_H7CR_typ_tt_1p2_25_nldm.lib"
DEFAULT_H7CR_GDS = RUNS_PATH / "ics55_next_acceptance" / "release" / "gds" / "ics55_LLSC_H7CR.gds"
DEFAULT_KLAYOUT_ROOT = PDK_ROOT / "libs.tech" / "klayout" / "tech"
DEFAULT_KLAYOUT_TECH = DEFAULT_KLAYOUT_ROOT / "ics55.lyt"
DEFAULT_KLAYOUT_PROPERTIES = DEFAULT_KLAYOUT_ROOT / "ics55.lyp"
DEFAULT_KLAYOUT_LAYER_MAP = DEFAULT_KLAYOUT_ROOT / "ics55.map"
DEFAULT_H7CR_LATCH_MAP = PDK_ROOT / "libs.tech" / "librelane" / "ics55_LLSC_H7CR" / "latch_map.v"
DEFAULT_SC9T_DFF_GDS = TECH / "gds_lib" / "DFFQX1_9TSVT.gds"
DEFAULT_H7CR_RCX_RULES = PDK_ROOT / "libs.tech" / "librelane" / "ics55_LLSC_H7CR" / "rcx_typ.rules"


class MacroBackendError(ValueError):
    """A required real compiler, model, or timing-regression contract failed."""


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _sha256(path)}


def _resolve_executable(command):
    candidate = Path(command).expanduser()
    if candidate.is_file():
        resolved = candidate.resolve()
    else:
        located = shutil.which(str(command))
        if not located:
            raise MacroBackendError("required executable is unavailable: " + str(command))
        resolved = Path(located).resolve()
    if not os.access(resolved, os.X_OK):
        raise MacroBackendError("required executable is not executable: " + str(resolved))
    return resolved


def _tool_identity(command):
    executable = _resolve_executable(command)
    result = subprocess.run([str(executable), "-V"], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, check=False)
    version = result.stdout.strip()
    if not version:
        raise MacroBackendError("could not establish tool version: " + str(executable))
    return {"path": str(executable), "sha256": _sha256(executable),
            "version": version, "version_returncode": result.returncode}


def validate_compiler_config(config_path=CONFIG_PATH):
    """Require the checked-in real 32x8 [W0,R1] compiler configuration."""
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        raise MacroBackendError("1W1R macro compiler config is missing: " + str(config_path))
    values = runpy.run_path(str(config_path))
    expected = {
        "tech_name": "icsprout55",
        "bitcell": "pbitcell",
        "stdcell_library": "SC9T",
        "word_size": 8,
        "num_words": 32,
        "num_rw_ports": 0,
        "num_w_ports": 1,
        "num_r_ports": 1,
    }
    for name, value in expected.items():
        actual = values.get(name, 0 if name in ("num_rw_ports", "num_w_ports", "num_r_ports") else None)
        if actual != value:
            raise MacroBackendError("macro config must set {}={!r}; got {!r}".format(
                name, value, actual))
    if values.get("netlist_only", False) is True:
        raise MacroBackendError("macro config is netlist_only; physical compiler views are required")
    return {"path": config_path, "values": values}


def _validate_generated_macro(model_path):
    """Check the compiler's emitted module and its actual [W0,R1] pins."""
    model_path = Path(model_path)
    source = model_path.read_text(encoding="utf-8")
    module_match = re.search(r"(?m)^\s*module\s+([A-Za-z_]\w*)\s*\((.*?)\);",
                             source, re.DOTALL)
    if not module_match:
        raise MacroBackendError("generated SRAM Verilog has no parseable top-module header: " + str(model_path))
    module_name, header = module_match.groups()
    if module_name != MACRO_NAME:
        raise MacroBackendError("compiler emitted macro module {!r}, expected {!r}".format(
            module_name, MACRO_NAME))
    declarations = {}
    for match in re.finditer(
            r"(?m)^\s*(input|output|inout)\s+(?:\[[^\]]+\]\s*)?([A-Za-z_]\w*)\s*;",
            source):
        declarations[match.group(2)] = match.group(1)
    expected = {
        "clk0": "input", "csb0": "input", "addr0": "input", "din0": "input",
        "clk1": "input", "csb1": "input", "addr1": "input", "dout1": "output",
    }
    for pin, direction in expected.items():
        if declarations.get(pin) != direction:
            raise MacroBackendError("generated [W0,R1] macro pin {} must be {}; got {!r}".format(
                pin, direction, declarations.get(pin)))
    header_tokens = re.findall(r"[A-Za-z_]\w*", re.sub(r"//[^\n]*", "", header))
    header_positions = [header_tokens.index(pin) if pin in header_tokens else -1
                        for pin in expected]
    if any(position < 0 for position in header_positions) or header_positions != sorted(header_positions):
        raise MacroBackendError("generated macro header does not expose the ordered [W0,R1] interface")
    for parameter in ("DELAY", "T_HOLD"):
        if not re.search(r"\bparameter\s+(?:integer\s+)?{}\b".format(parameter), source):
            raise MacroBackendError("generated macro model does not expose its {} simulation parameter".format(parameter))
    return {"module": module_name, "pins": expected,
            "compiler_source_sha256": _sha256(model_path)}


def _wrapper_source():
    return "\n".join([
        "`timescale 1ns/1ps",
        "// Two real 32x8 [W0,R1] macros: broadcast write, one physical reader per copy.",
        "module {} #(".format(WRAPPER_MODULE),
        "    parameter integer MACRO_DELAY = 3,",
        "    parameter integer MACRO_T_HOLD = 1",
        ") (",
        "    input wire clk,",
        "    input wire r0_en,",
        "    input wire [4:0] r0_addr,",
        "    output wire [7:0] r0_data,",
        "    input wire r1_en,",
        "    input wire [4:0] r1_addr,",
        "    output wire [7:0] r1_data,",
        "    input wire w_en,",
        "    input wire [4:0] w_addr,",
        "    input wire [7:0] w_data",
        "`ifdef USE_POWER_PINS",
        "    , inout wire VDD",
        "    , inout wire VSS",
        "`endif",
        ");",
        "  wire [7:0] copy0_rdata;",
        "  wire [7:0] copy1_rdata;",
        "  wire [7:0] r0_hold_data;",
        "  wire [7:0] r1_hold_data;",
        "  wire r0_latch_gate_n;",
        "  wire r1_latch_gate_n;",
        "  reg r0_sampled_en;",
        "  reg r1_sampled_en;",
        "  reg r0_bypass_valid;",
        "  reg r1_bypass_valid;",
        "  reg [7:0] r0_bypass_data;",
        "  reg [7:0] r1_bypass_data;",
        "  reg [7:0] r0_hold;",
        "  reg [7:0] r1_hold;",
        "",
        "  // The generated macro drives raw dout1 to X after posedge even while",
        "  // disabled. Hold each public output in a low-phase transparent latch.",
        "  // The latch closes at the next posedge; it adds no edge/cycle stage.",
        "  assign r0_hold_data = r0_bypass_valid ? r0_bypass_data : copy0_rdata;",
        "  assign r1_hold_data = r1_bypass_valid ? r1_bypass_data : copy1_rdata;",
        "  assign r0_latch_gate_n = clk | ~r0_sampled_en;",
        "  assign r1_latch_gate_n = clk | ~r1_sampled_en;",
        "`ifdef RF_MACRO_PHYSICAL",
        "  genvar r0_bit;",
        "  generate for (r0_bit = 0; r0_bit < 8; r0_bit = r0_bit + 1) begin: R0_HOLD",
        "    LATLX1H7R latch_cell (.D(r0_hold_data[r0_bit]), .GN(r0_latch_gate_n),",
        "                         .Q(r0_hold[r0_bit]), .QN());",
        "  end endgenerate",
        "  genvar r1_bit;",
        "  generate for (r1_bit = 0; r1_bit < 8; r1_bit = r1_bit + 1) begin: R1_HOLD",
        "    LATLX1H7R latch_cell (.D(r1_hold_data[r1_bit]), .GN(r1_latch_gate_n),",
        "                         .Q(r1_hold[r1_bit]), .QN());",
        "  end endgenerate",
        "`else",
        "  always_latch begin",
        "    if (!clk && r0_sampled_en) r0_hold <= r0_hold_data;",
        "  end",
        "  always_latch begin",
        "    if (!clk && r1_sampled_en) r1_hold <= r1_hold_data;",
        "  end",
        "`endif",
        "  assign r0_data = r0_bypass_valid ? r0_bypass_data : r0_hold;",
        "  assign r1_data = r1_bypass_valid ? r1_bypass_data : r1_hold;",
        "",
        "  // Sample bypass/control state at the same edge as each real macro.",
        "  // Address and write data are captured by each macro's own input flops.",
        "  always @(posedge clk) begin",
        "    r0_sampled_en <= r0_en;",
        "    r1_sampled_en <= r1_en;",
        "    r0_bypass_valid <= r0_en && w_en && (r0_addr == w_addr);",
        "    r1_bypass_valid <= r1_en && w_en && (r1_addr == w_addr);",
        "    if (r0_en && w_en && (r0_addr == w_addr)) r0_bypass_data <= w_data;",
        "    if (r1_en && w_en && (r1_addr == w_addr)) r1_bypass_data <= w_data;",
        "  end",
        "",
        "`ifdef RF_MACRO_SIM",
        "  {} #(.DELAY(MACRO_DELAY), .T_HOLD(MACRO_T_HOLD), .VERBOSE(0)) copy0 (".format(MACRO_NAME),
        "`else",
        "  {} copy0 (".format(MACRO_NAME),
        "`endif",
        "`ifdef USE_POWER_PINS",
        "    .vdd(VDD), .gnd(VSS),",
        "`endif",
        "    .clk0(clk), .csb0(~w_en), .addr0(w_addr), .din0(w_data),",
        "    .clk1(clk), .csb1(~r0_en), .addr1(r0_addr), .dout1(copy0_rdata)",
        "  );",
        "`ifdef RF_MACRO_SIM",
        "  {} #(.DELAY(MACRO_DELAY), .T_HOLD(MACRO_T_HOLD), .VERBOSE(0)) copy1 (".format(MACRO_NAME),
        "`else",
        "  {} copy1 (".format(MACRO_NAME),
        "`endif",
        "`ifdef USE_POWER_PINS",
        "    .vdd(VDD), .gnd(VSS),",
        "`endif",
        "    .clk0(clk), .csb0(~w_en), .addr0(w_addr), .din0(w_data),",
        "    .clk1(clk), .csb1(~r1_en), .addr1(r1_addr), .dout1(copy1_rdata)",
        "  );",
        "endmodule",
        "",
    ])


def _testbench_source():
    return "\n".join([
        "`timescale 1ns/1ps",
        "module {} #(".format(TESTBENCH_MODULE),
        "    parameter integer MACRO_DELAY = 3,",
        "    parameter integer PERIOD_NS = 10,",
        "    parameter integer EXPECT_DEADLINE_REJECT = 0",
        ")();",
        "  localparam real SETUP_GUARD_NS = 0.1;",
        "  reg clk = 1'b0;",
        "  reg r0_en = 1'b0;",
        "  reg [4:0] r0_addr = 5'b0;",
        "  reg r1_en = 1'b0;",
        "  reg [4:0] r1_addr = 5'b0;",
        "  reg w_en = 1'b0;",
        "  reg [4:0] w_addr = 5'b0;",
        "  reg [7:0] w_data = 8'b0;",
        "  wire [7:0] r0_data;",
        "  wire [7:0] r1_data;",
        "  always #(PERIOD_NS/2) clk = ~clk;",
        "  {} #(.MACRO_DELAY(MACRO_DELAY), .MACRO_T_HOLD(1)) dut (".format(WRAPPER_MODULE),
        "    .clk(clk), .r0_en(r0_en), .r0_addr(r0_addr), .r0_data(r0_data),",
        "    .r1_en(r1_en), .r1_addr(r1_addr), .r1_data(r1_data),",
        "    .w_en(w_en), .w_addr(w_addr), .w_data(w_data)",
        "  );",
        "",
        "  // check mode: 0=skip, 1=exact data, 2=unknown data, 3=must miss",
        "  // the requested word by the next-rising-edge deadline.",
        "  task step;",
        "    input read0; input [4:0] address0;",
        "    input read1; input [4:0] address1;",
        "    input write_en; input [4:0] write_address; input [7:0] write_data;",
        "    input integer check0; input [7:0] expected0;",
        "    input integer check1; input [7:0] expected1;",
        "    begin",
        "      r0_en = read0; r0_addr = address0;",
        "      r1_en = read1; r1_addr = address1;",
        "      w_en = write_en; w_addr = write_address; w_data = write_data;",
        "      @(posedge clk);",
        "      // Deliberately change every request input after its sampling edge.",
        "      #0.01;",
        "      r0_en = ~read0; r0_addr = ~address0;",
        "      r1_en = ~read1; r1_addr = ~address1;",
        "      w_en = ~write_en; w_addr = ~write_address; w_data = ~write_data;",
        "      #(PERIOD_NS - SETUP_GUARD_NS - 0.01);",
        "      if (check0 == 1 && r0_data !== expected0)",
        "        $fatal(1, \"read0 mismatch at next-edge deadline: got %b expected %b\", r0_data, expected0);",
        "      if (check0 == 2 && (^r0_data) !== 1'bx)",
        "        $fatal(1, \"read0 uninitialized data became known: %b\", r0_data);",
        "      if (check0 == 3 && r0_data === expected0)",
        "        $fatal(1, \"late macro access unexpectedly met the deadline: %b\", r0_data);",
        "      if (check1 == 1 && r1_data !== expected1)",
        "        $fatal(1, \"read1 mismatch at next-edge deadline: got %b expected %b\", r1_data, expected1);",
        "      if (check1 == 2 && (^r1_data) !== 1'bx)",
        "        $fatal(1, \"read1 uninitialized data became known: %b\", r1_data);",
        "      if (check1 == 3 && r1_data === expected1)",
        "        $fatal(1, \"late macro access unexpectedly met the deadline: %b\", r1_data);",
        "    end",
        "  endtask",
        "",
        "  initial begin",
        "    if ((PERIOD_NS % 2) != 0) $fatal(1, \"PERIOD_NS must be even\");",
        "    if (MACRO_DELAY <= 0) $fatal(1, \"zero-delay macro proof is forbidden\");",
        "    // The 8-bit output is uninitialized until written; first broadcast 0x3a.",
        "    step(0, 0, 0, 0, 1, 3, 8'h3a, 0, 0, 0, 0);",
        "    if (EXPECT_DEADLINE_REJECT != 0) begin",
        "      step(1, 3, 0, 0, 0, 0, 0, 3, 8'h3a, 0, 0);",
        "      $display(\"RF_MACRO_DEADLINE_REJECTED delay_ns=%0d deadline_ns=%0.1f\",",
        "               MACRO_DELAY, PERIOD_NS - SETUP_GUARD_NS);",
        "      $finish;",
        "    end",
        "    // Independent reads of different words, then hold through raw dout X.",
        "    step(1, 3, 1, 3, 0, 0, 0, 1, 8'h3a, 1, 8'h3a);",
        "    step(0, 3, 0, 3, 0, 0, 0, 1, 8'h3a, 1, 8'h3a);",
        "    step(0, 0, 0, 0, 1, 7, 8'h5a, 1, 8'h3a, 1, 8'h3a);",
        "    step(1, 3, 1, 7, 0, 0, 0, 1, 8'h3a, 1, 8'h5a);",
        "    // Reader 0 bypasses a same-address write; reader 1 independently reads old word.",
        "    step(1, 7, 1, 3, 1, 7, 8'hc3, 1, 8'hc3, 1, 8'h3a);",
        "    // Reader 0 disabled hold survives X at the macro output; copy1 sees broadcast data.",
        "    step(0, 3, 1, 7, 0, 0, 0, 1, 8'hc3, 1, 8'hc3);",
        "    // Reader 1's independent same-address bypass and a distinct reader-0 access.",
        "    step(1, 7, 1, 3, 1, 3, 8'he7, 1, 8'hc3, 1, 8'he7);",
        "    // Both readers bypass the same broadcast write on one sampled edge.",
        "    step(1, 3, 1, 3, 1, 3, 8'hd4, 1, 8'hd4, 1, 8'hd4);",
        "    // Both copies contain the write after bypass is removed.",
        "    step(1, 3, 1, 3, 0, 0, 0, 1, 8'hd4, 1, 8'hd4);",
        "    // Uninitialized locations stay X; disabled reads then hold X, not a fabricated zero.",
        "    step(1, 31, 1, 31, 0, 0, 0, 2, 8'hxx, 2, 8'hxx);",
        "    step(0, 31, 0, 31, 0, 0, 0, 2, 8'hxx, 2, 8'hxx);",
        "    $display(\"RF_MACRO_PHASE_SMOKE_PASS delay_ns=%0d period_ns=%0d deadline_ns=%0.1f\",",
        "             MACRO_DELAY, PERIOD_NS, PERIOD_NS - SETUP_GUARD_NS);",
        "    $finish;",
        "  end",
        "endmodule",
        "",
    ])


def _run_command(command, *, cwd, env, timeout, log_path, label):
    try:
        completed = subprocess.run(command, cwd=str(cwd), env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        Path(log_path).write_text(output + "\nTIMEOUT after {} seconds\n".format(timeout), encoding="utf-8")
        raise MacroBackendError("{} timed out after {} seconds; see {}".format(label, timeout, log_path)) from exc
    Path(log_path).write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0:
        raise MacroBackendError("{} failed with status {}; see {}".format(
            label, completed.returncode, log_path))
    return completed.stdout


def _compile_and_run(*, mode, delay_ns, period_ns, expect_reject,
                     model_path, wrapper_path, testbench_path, out_dir,
                     iverilog_path, vvp_path, timeout, env):
    if delay_ns <= 0:
        raise MacroBackendError("zero-delay behavioral proof is forbidden")
    setup_guard_ns = 0.1
    if period_ns % 2:
        raise MacroBackendError("period_ns must be an even integer for phase-accurate smoke")
    available_after_falling_ns = period_ns / 2.0 - setup_guard_ns
    if expect_reject and delay_ns <= available_after_falling_ns:
        raise MacroBackendError("negative timing point is not over budget: access={}ns, available={}ns".format(
            delay_ns, available_after_falling_ns))
    if not expect_reject and delay_ns > available_after_falling_ns:
        raise MacroBackendError("positive timing point exceeds the falling-to-rising deadline")
    case_dir = Path(out_dir) / "simulation" / mode
    case_dir.mkdir(parents=True, exist_ok=True)
    executable = case_dir / "sim.vvp"
    compile_log = case_dir / "compile.log"
    run_log = case_dir / "run.log"
    compile_command = [
        str(iverilog_path), "-g2012", "-DRF_MACRO_SIM", "-s", TESTBENCH_MODULE,
        "-P{}.MACRO_DELAY={}".format(TESTBENCH_MODULE, delay_ns),
        "-P{}.PERIOD_NS={}".format(TESTBENCH_MODULE, period_ns),
        "-P{}.EXPECT_DEADLINE_REJECT={}".format(TESTBENCH_MODULE, int(expect_reject)),
        "-o", str(executable), str(model_path), str(wrapper_path), str(testbench_path),
    ]
    _run_command(compile_command, cwd=ROOT, env=env, timeout=timeout,
                 log_path=compile_log, label="Icarus {} compile".format(mode))
    run_command = [str(vvp_path), str(executable)]
    output = _run_command(run_command, cwd=ROOT, env=env, timeout=timeout,
                          log_path=run_log, label="Icarus {} simulation".format(mode))
    marker = "RF_MACRO_DEADLINE_REJECTED" if expect_reject else "RF_MACRO_PHASE_SMOKE_PASS"
    if marker not in output:
        raise MacroBackendError("Icarus {} run did not emit required marker {}; see {}".format(
            mode, marker, run_log))
    return {
        "delay_ns": delay_ns,
        "period_ns": period_ns,
        "deadline_after_falling_ns": period_ns / 2.0 - setup_guard_ns,
        "deadline_marker": marker,
        "compile_command": compile_command,
        "run_command": run_command,
        "artifacts": {"executable": _ref(executable),
                      "compile_log": _ref(compile_log), "run_log": _ref(run_log)},
    }


def _verilog_macro_blackbox_source():
    """Emit only the compiler-derived physical macro boundary for Yosys."""
    return "\n".join([
        "// Physical abstract only; behavior comes from the real generated macro .v.",
        "(* blackbox *) module {} (".format(MACRO_NAME),
        "`ifdef USE_POWER_PINS",
        "    inout wire vdd,",
        "    inout wire gnd,",
        "`endif",
        "    input wire clk0,",
        "    input wire csb0,",
        "    input wire [4:0] addr0,",
        "    input wire [7:0] din0,",
        "    input wire clk1,",
        "    input wire csb1,",
        "    input wire [4:0] addr1,",
        "    output wire [7:0] dout1",
        ");",
        "endmodule",
        "",
    ])


def _tcl_path(path):
    value = str(Path(path).resolve())
    if "{" in value or "}" in value:
        raise MacroBackendError("Tcl input path contains braces: " + value)
    return "{" + value + "}"


def _yosys_path(path):
    value = str(Path(path).resolve())
    if '"' in value or "\n" in value:
        raise MacroBackendError("Yosys input path cannot be quoted: " + value)
    return '"' + value + '"'


def _macro_lef_size(lef_path):
    source = Path(lef_path).read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"(?ms)^MACRO\s+{}\s*$.*?^\s*SIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)\s*;".format(
            re.escape(MACRO_NAME)), source)
    if not match:
        raise MacroBackendError("generated macro LEF has no SIZE for " + MACRO_NAME)
    width, height = (float(value) for value in match.groups())
    if width <= 0 or height <= 0:
        raise MacroBackendError("generated macro LEF has invalid dimensions")
    pins = ["clk0", "csb0", "clk1", "csb1", "vdd", "gnd"]
    for bus, size in (("addr0", 5), ("addr1", 5), ("din0", 8), ("dout1", 8)):
        pins.extend("{}[{}]".format(bus, index) for index in range(size))
    for pin in pins:
        if not re.search(r"(?m)^\s*PIN\s+{}\s*$".format(re.escape(pin)), source):
            raise MacroBackendError("generated macro LEF is missing physical pin " + pin)
    return width, height

def _gds_layer_spec(layer_map_path, gds_layer_name):
    matches = []
    for line in Path(layer_map_path).read_text(encoding="utf-8").splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) >= 3 and fields[2] == gds_layer_name:
            try:
                matches.append((int(fields[0]), int(fields[1])))
            except ValueError as exc:
                raise MacroBackendError(
                    "invalid GDS layer mapping for " + gds_layer_name) from exc
    if len(matches) != 1:
        raise MacroBackendError(
            "KLayout layer map must define exactly one " + gds_layer_name + " entry")
    return matches[0]


def _macro_met5_extraction_source(*, source_gds, support_gds, output_json,
                                  macro_name, gds_layer):
    gds_paths = json.dumps([
        str(Path(source_gds).resolve()), str(Path(support_gds).resolve())])
    return "\n".join([
        "import json",
        "import pya",
        "layout = pya.Layout()",
        "options = pya.LoadLayoutOptions()",
        "options.cell_conflict_resolution = pya.LoadLayoutOptions.CellConflictResolution.AddToCell",
        "for path in {}:".format(gds_paths),
        "    layout.read(path, options)",
        "macro = layout.cell({})".format(json.dumps(macro_name)),
        "if macro is None:",
        "    raise RuntimeError('source GDS does not contain the expected macro cell')",
        "hierarchy = pya.Layout()",
        "hierarchy.dbu = layout.dbu",
        "hierarchy_top = hierarchy.create_cell({})".format(json.dumps(macro_name)),
        "hierarchy_top.copy_tree(macro)",
        "ghosts = [cell.name for cell in hierarchy.each_cell() if cell.is_ghost_cell()]",
        "if ghosts:",
        "    raise RuntimeError('source macro GDS has unresolved child cells: ' + ', '.join(ghosts))",
        "layer_index = layout.find_layer({}, {})".format(gds_layer[0], gds_layer[1]),
        "if layer_index is None or layer_index < 0:",
        "    raise RuntimeError('source macro GDS has no M5 layer')",
        "region = pya.Region(macro.begin_shapes_rec(layer_index))",
        "region.merge()",
        "rectangles = []",
        "for polygon in region.each():",
        "    box = polygon.bbox()",
        "    if box.width() > 0 and box.height() > 0:",
        "        rectangles.append([int(box.left), int(box.bottom), int(box.right), int(box.top)])",
        "rectangles.sort()",
        "if not rectangles:",
        "    raise RuntimeError('source macro GDS has no M5 geometry to bound')",
        "with open({}, 'w', encoding='utf-8') as stream:".format(
            json.dumps(str(Path(output_json).resolve()))),
        "    json.dump({",
        "        'schema': 'ics55-macro-gds-met5-obstructions/1',",
        "        'macro_cell': {},".format(json.dumps(macro_name)),
        "        'gds_layer': {},".format(json.dumps(list(gds_layer))),
        "        'layout_dbu_um': repr(layout.dbu),",
        "        'bbox_method': 'recursive merged GDS-region polygon bounding boxes',",
        "        'rectangles_dbu': rectangles,",
        "    }, stream, indent=2, sort_keys=True)",
        "    stream.write('\\n')",
        "",
    ])


def _extract_macro_met5_obstructions(*, source_gds, support_gds, layer_map,
                                     klayout_path, attempt_dir, timeout, env):
    gds_layer = _gds_layer_spec(layer_map, "M5")
    extraction_path = Path(attempt_dir) / "macro_met5_extract.py"
    output_path = Path(attempt_dir) / "macro_met5_obstructions.json"
    log_path = Path(attempt_dir) / "macro_met5_extract.log"
    extraction_path.write_text(_macro_met5_extraction_source(
        source_gds=source_gds, support_gds=support_gds, output_json=output_path,
        macro_name=MACRO_NAME, gds_layer=gds_layer), encoding="utf-8")
    command = [str(klayout_path), "-b", "-r", str(extraction_path)]
    _run_command(command, cwd=ROOT, env=env, timeout=timeout, log_path=log_path,
                 label="KLayout source-GDS MET5 obstruction extraction")
    if not output_path.is_file():
        raise MacroBackendError(
            "KLayout completed without producing source-GDS MET5 obstruction data")
    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MacroBackendError("KLayout emitted invalid source-GDS obstruction data") from exc
    if data.get("schema") != "ics55-macro-gds-met5-obstructions/1":
        raise MacroBackendError("KLayout source-GDS obstruction schema is unexpected")
    if data.get("macro_cell") != MACRO_NAME or data.get("gds_layer") != list(gds_layer):
        raise MacroBackendError("KLayout source-GDS obstruction identity does not match its inputs")
    if not isinstance(data.get("rectangles_dbu"), list) or not data["rectangles_dbu"]:
        raise MacroBackendError("KLayout source-GDS obstruction geometry is empty")
    return data, {"script": extraction_path, "json": output_path, "log": log_path,
                  "command": command, "layer": gds_layer}


def _stage_macro_lef(source_path, tech_lef, destination, *, met5_obstructions):
    source = Path(source_path).read_text(encoding="utf-8")
    technology = Path(tech_lef).read_text(encoding="utf-8")
    units = re.search(r"\bDATABASE\s+MICRONS\s+(\d+)\s*;", technology)
    if units is None:
        raise MacroBackendError("technology LEF lacks database units")
    grid = Decimal(units.group(1))
    macro_pattern = r"(?ms)^MACRO\s+{}\s*$.*?^END\s+{}\s*$".format(
        re.escape(MACRO_NAME), re.escape(MACRO_NAME))
    macro_matches = list(re.finditer(macro_pattern, source))
    if len(macro_matches) != 1:
        raise MacroBackendError("source macro LEF must contain exactly one " + MACRO_NAME)
    macro_match = macro_matches[0]
    macro_text = macro_match.group(0)
    obs_match = re.search(r"(?ms)^\s*OBS\s*(.*?)^\s*END\s*$", macro_text)
    if obs_match is None:
        raise MacroBackendError("source macro LEF has no OBS block")
    obs_body = obs_match.group(1)
    layer_headers = list(re.finditer(
        r"(?m)^([ \t]*)LAYER\s+([A-Za-z0-9_]+)\s*;[ \t]*$", obs_body))
    met5_headers = [index for index, match in enumerate(layer_headers)
                    if match.group(2) == "MET5"]
    if len(met5_headers) != 1:
        raise MacroBackendError("source macro LEF must contain one MET5 obstruction layer")
    layer_index = met5_headers[0]
    header = layer_headers[layer_index]
    segment_end = (layer_headers[layer_index + 1].start()
                   if layer_index + 1 < len(layer_headers) else len(obs_body))
    segment = obs_body[header.end():segment_end]
    rect_pattern = r"(?m)^[ \t]*RECT\s+[^;]+;[ \t]*(?:\n|$)"
    original_rectangles = re.findall(rect_pattern, segment)
    if not original_rectangles:
        raise MacroBackendError("source macro LEF MET5 obstruction has no rectangles to refine")
    if re.sub(rect_pattern, "", segment).strip():
        raise MacroBackendError(
            "source macro LEF MET5 obstruction contains unsupported non-RECT geometry")

    macro_size = re.search(r"(?m)^\s*SIZE\s+([0-9.]+)\s+BY\s+([0-9.]+)\s*;", macro_text)
    if macro_size is None:
        raise MacroBackendError("source macro LEF has no dimensions for MET5 obstruction checks")
    macro_width, macro_height = (Decimal(value) for value in macro_size.groups())
    gds_dbu = Decimal(met5_obstructions.get("layout_dbu_um", "0"))
    if gds_dbu <= 0:
        raise MacroBackendError("KLayout source-GDS obstruction DBU must be positive")
    generated_rectangles = []
    for rectangle in met5_obstructions["rectangles_dbu"]:
        if (not isinstance(rectangle, list) or len(rectangle) != 4 or
                any(not isinstance(value, int) for value in rectangle)):
            raise MacroBackendError("KLayout source-GDS obstruction rectangle is malformed")
        coordinates = [Decimal(value) * gds_dbu for value in rectangle]
        x0, y0, x1, y1 = coordinates
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise MacroBackendError("KLayout source-GDS obstruction rectangle is invalid")
        if x1 > macro_width or y1 > macro_height:
            raise MacroBackendError("KLayout source-GDS obstruction exceeds macro LEF dimensions")
        for coordinate in coordinates:
            if (coordinate * grid) != (coordinate * grid).to_integral_value():
                raise MacroBackendError(
                    "GDS-derived MET5 obstruction exceeds technology database precision")
        generated_rectangles.append("      RECT {} ;".format(
            " ".join(format(coordinate, "f") for coordinate in coordinates)))
    if not generated_rectangles:
        raise MacroBackendError("no conservative GDS-derived MET5 obstruction rectangles remain")
    new_segment = "\n".join(generated_rectangles) + "\n"
    refined_obs = obs_body[:header.end()] + new_segment + obs_body[segment_end:]
    refined_macro = (macro_text[:obs_match.start(1)] + refined_obs +
                     macro_text[obs_match.end(1):])
    staged = source[:macro_match.start()] + refined_macro + source[macro_match.end():]
    for geometry in re.findall(r"(?m)^\s*(?:RECT|SIZE)\s+([^;]+);", staged):
        for value in re.findall(r"-?\d+(?:\.\d+)?", geometry):
            scaled = Decimal(value) * grid
            if scaled != scaled.to_integral_value():
                raise MacroBackendError(
                    "macro LEF geometry exceeds technology database precision: " + value)
    staged, count = re.subn(r"DATABASE\s+MICRONS\s+\d+",
                            "DATABASE MICRONS " + units.group(1), staged)
    if count != 1:
        raise MacroBackendError("macro LEF must have exactly one database-units declaration")
    Path(destination).write_text(staged, encoding="utf-8")
    return {
        "source_met5_rectangles_replaced": len(original_rectangles),
        "gds_derived_met5_rectangles": len(generated_rectangles),
        "gds_layer": met5_obstructions["gds_layer"],
        "gds_database_unit_um": str(gds_dbu),
        "technology_database_units_per_um": int(grid),
        "geometry_method": met5_obstructions["bbox_method"],
    }


def _require_h7cr_latch(lef_path, liberty_path):
    lef = Path(lef_path).read_text(encoding="utf-8", errors="replace")
    if not re.search(r"(?m)^MACRO\s+LATLX1H7R\s*$", lef):
        raise MacroBackendError("H7CR LEF is missing the low-transparent output-hold cell LATLX1H7R")
    found_lib_cell = False
    with Path(liberty_path).open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            if re.match(r"\s*cell\s*\(\s*LATLX1H7R\s*\)\s*\{", line):
                found_lib_cell = True
                break
    if not found_lib_cell:
        raise MacroBackendError("H7CR Liberty is missing the output-hold cell LATLX1H7R")


def _openroad_script(*, physical_dir, tech_lef, cell_lef, macro_dir,
                     cell_liberty, macro_liberty, rcx_rules, macro_width,
                     macro_height):
    die_width = 2 * macro_width + 80.0
    die_height = macro_height + 40.0
    macro0_x = 10.0
    macro1_x = macro_width + 70.0
    macro_y = 20.0
    physical_dir = Path(physical_dir)
    macro_lef = physical_dir / "macro.lef"
    netlist = physical_dir / "mapped.v"
    def_path = physical_dir / "routed.def"
    odb_path = physical_dir / "routed.odb"
    spef_path = physical_dir / "openrcx_typ.spef"
    drc_path = physical_dir / "detailed_route_drc.rpt"
    return "\n".join([
        "read_lef {}".format(_tcl_path(tech_lef)),
        "read_lef {}".format(_tcl_path(cell_lef)),
        "read_lef {}".format(_tcl_path(macro_lef)),
        "read_liberty {}".format(_tcl_path(cell_liberty)),
        "read_liberty {}".format(_tcl_path(macro_liberty)),
        "read_verilog {}".format(_tcl_path(netlist)),
        "link_design {}".format(WRAPPER_MODULE),
        "initialize_floorplan -die_area {0 0 %.3f %.3f} -core_area {5 5 %.3f %.3f} -site core7" % (
            die_width, die_height, die_width - 5.0, die_height - 5.0),
        "make_tracks",
        "set_routing_layers -signal MET1-MET4 -clock MET1-MET4",
        "set_wire_rc -signal -layer MET3",
        "set_wire_rc -clock -layer MET3",
        "add_global_connection -net VDD -pin_pattern {^VDD$} -power",
        "add_global_connection -net VSS -pin_pattern {^VSS$} -ground",
        "add_global_connection -net VDD -inst_pattern {^copy[01]$} -pin_pattern {^vdd$} -power",
        "add_global_connection -net VSS -inst_pattern {^copy[01]$} -pin_pattern {^gnd$} -ground",
        "global_connect",
        "set_voltage_domain -name CORE -power VDD -ground VSS",
        "place_macro -macro_name copy0 -location {%.3f %.3f} -orientation R0" % (
            macro0_x, macro_y),
        "place_macro -macro_name copy1 -location {%.3f %.3f} -orientation R0" % (
            macro1_x, macro_y),
        "place_pins -hor_layers MET3 -ver_layers MET2",
        "global_placement -density 0.50",
        "detailed_placement",
        "check_placement -verbose",
        "define_pdn_grid -name stdcell_grid -starts_with POWER -voltage_domain CORE -pins {MET4 MET5}",
        "add_pdn_stripe -grid stdcell_grid -layer MET4 -width 1.0 -pitch 16 -offset 0.5 -spacing 1.0 -starts_with POWER -extend_to_boundary",
        "add_pdn_stripe -grid stdcell_grid -layer MET5 -width 1.0 -pitch 16 -offset 0.5 -spacing 1.0 -starts_with POWER -extend_to_boundary",
        "add_pdn_connect -grid stdcell_grid -layers {MET4 MET5}",
        "add_pdn_stripe -grid stdcell_grid -layer MET1 -width 0.16 -followpins",
        "add_pdn_connect -grid stdcell_grid -layers {MET1 MET4}",
        "define_pdn_grid -macro -default -name macro -starts_with POWER -halo {10 10}",
        "add_pdn_connect -grid macro -layers {MET4 MET5}",
        "pdngen",
        "global_route -congestion_iterations 30",
        "detailed_route -output_drc {}".format(_tcl_path(drc_path)),
        "define_process_corner -ext_model_index 0 ics55_typ",
        "extract_parasitics -ext_model_file {} -lef_res".format(_tcl_path(rcx_rules)),
        "write_spef {}".format(_tcl_path(spef_path)),
        "write_def {}".format(_tcl_path(def_path)),
        "write_db {}".format(_tcl_path(odb_path)),
        "",
    ])


def _write_klayout_def_map(source_map, output_path):
    """Translate this PDK's GDS map to the KLayout LEF/DEF map dialect."""
    source_map = Path(source_map)
    source_text = source_map.read_text(encoding="utf-8")
    aliases = [
        ("MET1", "M1", "M1LBL"),
        ("MET2", "M2", "M2LBL"),
        ("MET3", "M3", "M3LBL"),
        ("MET4", "M4", "M4LBL"),
        ("MET5", "M5", "M5LBL"),
        ("VIA1", "V1", None),
        ("VIA2", "V2", None),
        ("VIA3", "V3", None),
        ("VIA4", "V4", None),
        ("T4M2", "TM2", "TM2LBL"),
        ("T4V2", "TV2", None),
        ("RDL", "RDL", "RDLLBL"),
        ("RV", "RV", None),
    ]
    source_layers = {}
    for line in source_text.splitlines():
        match = re.match(r"^\s*(\d+)\s+(\d+)\s+(\S+)", line)
        if match:
            layer, datatype, name = match.groups()
            source_layers[name] = (int(layer), int(datatype))
    missing = [name for _, gds_name, label_name in aliases
               for name in (gds_name, label_name) if name and name not in source_layers]
    if missing:
        raise MacroBackendError(
            "PDK GDS layer map is missing required streamout layers: " +
            ", ".join(sorted(set(missing))))

    lines = [
        "# Generated for routed DEF streamout; source PDK GDS map: {}".format(source_map),
        "DIEAREA ALL 351 12",
        "OUTLINE ALL 351 12",
    ]
    mapping = {"routing": {}, "vias": {}}
    for lef_name, gds_name, label_name in aliases:
        layer, datatype = source_layers[gds_name]
        mapping["routing" if lef_name.startswith("MET") else
                "vias" if lef_name.startswith("VIA") else "other"][lef_name] = [
                    layer, datatype]
        purposes = ("NET", "SPNET", "PIN", "VIA") if lef_name.startswith("MET") else (
            ("VIA",) if lef_name.startswith("VIA") else ("NET", "SPNET", "PIN", "VIA"))
        for purpose in purposes:
            lines.append("{} {} {} {}".format(lef_name, purpose, layer, datatype))
        lines.append("{} LEFOBS {} 5".format(lef_name, layer))
        if lef_name.startswith("MET"):
            lines.append("{} LEFPIN {} {}".format(lef_name, layer, datatype))
            lines.append("{} LEFLABEL {} {}".format(lef_name, layer, datatype))
        if label_name:
            label_layer, label_datatype = source_layers[label_name]
            for purpose in ("PIN", "LEFPIN", "LABEL", "LEFLABEL"):
                lines.append("NAME {}/{} {} {}".format(
                    lef_name, purpose, label_layer, label_datatype))
    Path(output_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return mapping


def _klayout_streamout_source(*, def_path, output_path, inventory_path, top_name,
                              technology, lefs, layer_map, gds_files,
                              routing_layers, via_layers):
    """Emit and validate a physical DEF/GDS merge using mapped route geometry."""
    quoted_lefs = json.dumps([str(Path(path).resolve()) for path in lefs])
    quoted_gds = json.dumps([str(Path(path).resolve()) for path in gds_files])
    return "\n".join([
        "import json",
        "import re",
        "import pya",
        "technology = pya.Technology()",
        "technology.load({})".format(json.dumps(str(Path(technology).resolve()))),
        "layout_options = technology.load_layout_options",
        "layout_options.lefdef_config.read_lef_with_def = False",
        "layout_options.lefdef_config.lef_files = {}".format(quoted_lefs),
        "layout_options.lefdef_config.map_file = {}".format(
            json.dumps(str(Path(layer_map).resolve()))),
        "layout_options.lefdef_config.net_property_name = None",
        "layout_options.lefdef_config.instance_property_name = None",
        "layout_options.lefdef_config.pin_property_name = None",
        "layout_options.cell_conflict_resolution = pya.LoadLayoutOptions.CellConflictResolution.OverwriteCell",
        "layout = pya.Layout()",
        "def_path = {}".format(json.dumps(str(Path(def_path).resolve()))),
        "layout.read(def_path, layout_options)",
        "top_cell = layout.cell({})".format(json.dumps(top_name)),
        "if top_cell is None:",
        "    raise RuntimeError('routed DEF is missing the expected top cell')",
        "def_text = open(def_path, encoding='utf-8').read()",
        "component_match = re.search(r'(?ms)^COMPONENTS\\s+(\\d+)\\s*;(.*?)^END COMPONENTS', def_text)",
        "if component_match is None:",
        "    raise RuntimeError('routed DEF has no complete COMPONENTS section')",
        "declared_components = int(component_match.group(1))",
        "component_types = re.findall(r'(?m)^\\s*-\\s+\\S+\\s+(\\S+)', component_match.group(2))",
        "if len(component_types) != declared_components:",
        "    raise RuntimeError('routed DEF component inventory disagrees with its count')",
        "macro_count = component_types.count({})".format(json.dumps(MACRO_NAME)),
        "hold_count = component_types.count('LATLX1H7R')",
        "if macro_count != 2:",
        "    raise RuntimeError('routed DEF must contain exactly two real 32x8 macros')",
        "if hold_count == 0:",
        "    raise RuntimeError('routed DEF is missing mapped output-hold latch instances')",
        "signal_match = re.search(r'(?ms)^NETS\\s+\\d+\\s*;(.*?)^END NETS', def_text)",
        "special_match = re.search(r'(?ms)^SPECIALNETS\\s+\\d+\\s*;(.*?)^END SPECIALNETS', def_text)",
        "if signal_match is None or special_match is None:",
        "    raise RuntimeError('routed DEF lacks complete signal or special-net sections')",
        "route_pattern = r'(?m)^\\s*\\+?\\s*(?:ROUTED|NEW|FIXED|COVER)\\s+MET[1-5]\\b'",
        "signal_routes = len(re.findall(route_pattern, signal_match.group(1)))",
        "special_routes = len(re.findall(route_pattern, special_match.group(1)))",
        "if signal_routes == 0 or special_routes == 0:",
        "    raise RuntimeError('routed DEF contains no mapped signal or PDN wire statements')",
        "top_cell_index = top_cell.cell_index()",
        "for cell in layout.each_cell():",
        "    if cell.cell_index() != top_cell_index and not cell.name.startswith('VIA'):",
        "        cell.clear()",
        "for gds_path in {}:".format(quoted_gds),
        "    layout.read(gds_path, layout_options)",
        "top_only_layout = pya.Layout()",
        "top_only_layout.dbu = layout.dbu",
        "top_only = top_only_layout.create_cell({})".format(json.dumps(top_name)),
        "top_only.copy_tree(layout.cell({}))".format(json.dumps(top_name)),
        "missing_gds = [cell.name for cell in top_only_layout.each_cell() if cell.is_ghost_cell()]",
        "if missing_gds:",
        "    raise RuntimeError('LEF cells have no matching GDS: ' + ', '.join(missing_gds))",
        "instances = [top_only_layout.cell(inst.cell_index).name for inst in top_only.each_inst()]",
        "if len(instances) != declared_components:",
        "    raise RuntimeError('streamed GDS top-level instance count disagrees with routed DEF')",
        "if instances.count({}) != 2:".format(json.dumps(MACRO_NAME)),
        "    raise RuntimeError('streamed GDS is missing one or both real macro instances')",
        "if instances.count('LATLX1H7R') != hold_count:",
        "    raise RuntimeError('streamed GDS output-hold latch inventory disagrees with routed DEF')",
        "route_specs = {}".format(json.dumps(routing_layers, sort_keys=True)),
        "via_specs = {}".format(json.dumps(via_layers, sort_keys=True)),
        "route_geometry = {}",
        "for layer_name, (gds_layer, datatype) in route_specs.items():",
        "    layer_index = top_only_layout.find_layer(gds_layer, datatype)",
        "    count = 0 if layer_index is None else top_only.shapes(layer_index).size()",
        "    if count == 0:",
        "        raise RuntimeError('routed GDS has no top-level geometry for ' + layer_name)",
        "    route_geometry[layer_name] = count",
        "via_geometry = {}",
        "for layer_name, (gds_layer, datatype) in via_specs.items():",
        "    layer_index = top_only_layout.find_layer(gds_layer, datatype)",
        "    count = 0 if layer_index is None else pya.Region(top_only.begin_shapes_rec(layer_index)).count()",
        "    if count == 0:",
        "        raise RuntimeError('routed GDS has no recursive via geometry for ' + layer_name)",
        "    via_geometry[layer_name] = count",
        "cell_geometry = {}",
        "for cell_name in [{}, 'LATLX1H7R']:".format(json.dumps(MACRO_NAME)),
        "    cell = top_only_layout.cell(cell_name)",
        "    if cell is None or cell.is_ghost_cell():",
        "        raise RuntimeError('real GDS geometry is missing for cell ' + cell_name)",
        "    geometry_count = sum(pya.Region(cell.begin_shapes_rec(top_only_layout.find_layer(*pair))).count() for pair in route_specs.values())",
        "    if geometry_count == 0:",
        "        raise RuntimeError('real routed-layer geometry is empty for cell ' + cell_name)",
        "    cell_geometry[cell_name] = geometry_count",
        "top_only_layout.write({})".format(json.dumps(str(Path(output_path).resolve()))),
        "inventory = {",
        "    'schema': 'ics55-register-file-macro-streamout-inventory/1',",
        "    'def_component_count': declared_components,",
        "    'macro_instance_count': macro_count,",
        "    'output_hold_latch_count': hold_count,",
        "    'signal_route_statements': signal_routes,",
        "    'special_route_statements': special_routes,",
        "    'top_level_routing_geometry': route_geometry,",
        "    'recursive_via_geometry': via_geometry,",
        "    'recursive_cell_geometry': cell_geometry,",
        "    'ghost_cells': [],",
        "}",
        "open({}, 'w', encoding='utf-8').write(json.dumps(inventory, indent=2, sort_keys=True) + '\\\\n')".format(
            json.dumps(str(Path(inventory_path).resolve()))),
        "",
    ])


def _next_physical_attempt(physical_root):
    attempts_root = Path(physical_root) / "attempts"
    attempts_root.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10000):
        attempt_dir = attempts_root / "route_{:04d}".format(index)
        try:
            attempt_dir.mkdir()
        except FileExistsError:
            continue
        return attempt_dir
    raise MacroBackendError("could not allocate a fresh physical route attempt under " + str(attempts_root))


def _klayout_streamout_source(*, def_path, output_path, top_name,
                              technology, lefs, layer_map, gds_files):
    """Emit LibreLane-compatible KLayout DEF-to-GDS streamout logic."""
    quoted_lefs = json.dumps([str(Path(path).resolve()) for path in lefs])
    quoted_gds = json.dumps([str(Path(path).resolve()) for path in gds_files])
    return "\n".join([
        "import pya",
        "technology = pya.Technology()",
        "technology.load({})".format(json.dumps(str(Path(technology).resolve()))),
        "layout_options = technology.load_layout_options",
        "layout_options.lefdef_config.read_lef_with_def = False",
        "layout_options.lefdef_config.lef_files = {}".format(quoted_lefs),
        "layout_options.lefdef_config.map_file = {}".format(
            json.dumps(str(Path(layer_map).resolve()))),
        "layout_options.lefdef_config.net_property_name = None",
        "layout_options.lefdef_config.instance_property_name = None",
        "layout_options.lefdef_config.pin_property_name = None",
        "layout_options.cell_conflict_resolution = pya.LoadLayoutOptions.CellConflictResolution.RenameCell",
        "layout = pya.Layout()",
        "layout.read({}, layout_options)".format(json.dumps(str(Path(def_path).resolve()))),
        "top_cell = layout.cell({})".format(json.dumps(top_name)),
        "if top_cell is None:",
        "    raise RuntimeError('routed DEF is missing the expected top cell')",
        "top_cell_index = top_cell.cell_index()",
        "for cell in layout.each_cell():",
        "    if cell.cell_index() != top_cell_index and not cell.name.startswith('VIA'):",
        "        cell.clear()",
        "for gds_path in {}:".format(quoted_gds),
        "    layout.read(gds_path, layout_options)",
        "top_only_layout = pya.Layout()",
        "top_only_layout.dbu = layout.dbu",
        "top_only = top_only_layout.create_cell({})".format(json.dumps(top_name)),
        "top_only.copy_tree(layout.cell({}))".format(json.dumps(top_name)),
        "missing_gds = [cell.name for cell in top_only_layout.each_cell() if cell.is_ghost_cell()]",
        "if missing_gds:",
        "    raise RuntimeError('LEF cells have no matching GDS: ' + ', '.join(missing_gds))",
        "top_only_layout.write({})".format(json.dumps(str(Path(output_path).resolve()))),
        "",
    ])


def _physical_route(*, out_dir, macro_dir, wrapper_path, liberty_views,
                    yosys="yosys", openroad="openroad", klayout="klayout",
                    tech_lef=DEFAULT_TECH_LEF, cell_lef=DEFAULT_H7CR_LEF,
                    cell_liberty=DEFAULT_H7CR_LIBERTY, cell_gds=DEFAULT_H7CR_GDS,
                    latch_map=DEFAULT_H7CR_LATCH_MAP,
                    sc9t_dff_gds=DEFAULT_SC9T_DFF_GDS,
                    klayout_tech=DEFAULT_KLAYOUT_TECH,
                    klayout_properties=DEFAULT_KLAYOUT_PROPERTIES,
                    klayout_layer_map=DEFAULT_KLAYOUT_LAYER_MAP,
                    timeout=1800, env=None):
    """Map, PDN, route, and stream out the real macro composition.

    Generated macro GDS and H7CR glue GDS remain distinct inputs. OpenROAD
    consumes real LEF/Liberty abstracts; LibreLane-style KLayout streamout
    merges those GDS views into the routed DEF.
    """
    if not liberty_views:
        raise MacroBackendError("physical route requires the generated macro's analytical Liberty view")
    macro_dir = Path(macro_dir)
    paths = {
        "technology LEF": tech_lef,
        "H7CR LEF": cell_lef,
        "H7CR typical Liberty": cell_liberty,
        "H7CR GDS": cell_gds,
        "SC9T DFF GDS used inside macro": sc9t_dff_gds,
        "H7CR latch mapping": latch_map,
        "KLayout technology": klayout_tech,
        "KLayout properties": klayout_properties,
        "KLayout LEF/DEF layer map": klayout_layer_map,
        "generated macro Verilog": macro_dir / (MACRO_NAME + ".v"),
        "generated macro LEF": macro_dir / (MACRO_NAME + ".lef"),
        "generated macro GDS": macro_dir / (MACRO_NAME + ".gds"),
        "generated macro Liberty": liberty_views[0],
        "wrapper RTL": wrapper_path,
    }
    missing = [name + " (" + str(path) + ")" for name, path in paths.items()
               if not Path(path).is_file()]
    if missing:
        raise MacroBackendError("physical route prerequisites are missing: " + "; ".join(missing))
    macro_interface = _validate_generated_macro(paths["generated macro Verilog"])
    _require_h7cr_latch(cell_lef, cell_liberty)
    macro_width, macro_height = _macro_lef_size(paths["generated macro LEF"])
    yosys_path = _resolve_executable(yosys)
    openroad_path = _resolve_executable(openroad)
    klayout_path = _resolve_executable(klayout)
    physical_root = Path(out_dir) / "physical"
    attempt_dir = _next_physical_attempt(physical_root)
    env = os.environ.copy() if env is None else env
    met5_data, met5_artifacts = _extract_macro_met5_obstructions(
        source_gds=paths["generated macro GDS"], support_gds=sc9t_dff_gds,
        layer_map=klayout_layer_map, klayout_path=klayout_path,
        attempt_dir=attempt_dir, timeout=timeout, env=env)
    staged_lef = attempt_dir / "macro.lef"
    staged_obstruction_view = _stage_macro_lef(
        paths["generated macro LEF"], tech_lef, staged_lef,
        met5_obstructions=met5_data)
    blackbox_path = attempt_dir / (MACRO_NAME + "_blackbox.v")
    blackbox_path.write_text(_verilog_macro_blackbox_source(), encoding="utf-8")
    mapped_path = attempt_dir / "mapped.v"
    synth_path = attempt_dir / "synthesis.ys"
    synth_log = attempt_dir / "synthesis.log"
    synth_path.write_text("\n".join([
        "read_liberty -lib {}".format(_yosys_path(cell_liberty)),
        "read_verilog -lib -DUSE_POWER_PINS {}".format(_yosys_path(blackbox_path)),
        "read_verilog -sv -DRF_MACRO_PHYSICAL -DUSE_POWER_PINS {}".format(
            _yosys_path(wrapper_path)),
        "hierarchy -check -top {}".format(WRAPPER_MODULE),
        "synth -top {} -flatten -noabc".format(WRAPPER_MODULE),
        "techmap -map {}".format(_yosys_path(latch_map)),
        "dfflibmap -liberty {}".format(_yosys_path(cell_liberty)),
        "abc -liberty {}".format(_yosys_path(cell_liberty)),
        "clean",
        "check -assert",
        "stat -liberty {}".format(_yosys_path(cell_liberty)),
        "write_verilog -noattr -noexpr {}".format(_yosys_path(mapped_path)),
        "",
    ]), encoding="utf-8")
    _run_command([str(yosys_path), "-s", str(synth_path)], cwd=ROOT, env=env,
                 timeout=timeout, log_path=synth_log, label="Yosys H7CR wrapper mapping")
    if not mapped_path.is_file():
        raise MacroBackendError("Yosys completed without producing " + str(mapped_path))

    route_script = attempt_dir / "route.tcl"
    route_log = attempt_dir / "openroad.log"
    route_script.write_text(_openroad_script(
        physical_dir=attempt_dir, tech_lef=tech_lef, cell_lef=cell_lef,
        macro_dir=macro_dir, cell_liberty=cell_liberty,
        macro_liberty=paths["generated macro Liberty"],
        macro_width=macro_width, macro_height=macro_height), encoding="utf-8")
    route_command = [str(openroad_path), "-exit", str(route_script)]
    _run_command(route_command, cwd=ROOT, env=env, timeout=timeout,
                 log_path=route_log, label="OpenROAD macro composition PDN and route")
    def_path = attempt_dir / "routed.def"
    gds_path = attempt_dir / (WRAPPER_MODULE + "_routed.gds")
    streamout_script = attempt_dir / "klayout_streamout.py"
    streamout_log = attempt_dir / "klayout_streamout.log"
    streamout_script.write_text(_klayout_streamout_source(
        def_path=def_path, output_path=gds_path, top_name=WRAPPER_MODULE,
        technology=klayout_tech, lefs=[
            tech_lef, cell_lef, staged_lef],
        layer_map=klayout_layer_map, gds_files=[
            cell_gds, sc9t_dff_gds, Path(macro_dir) / (MACRO_NAME + ".gds")]),
        encoding="utf-8")
    streamout_command = [str(klayout_path), "-b", "-r", str(streamout_script)]
    _run_command(streamout_command, cwd=ROOT, env=env, timeout=timeout,
                 log_path=streamout_log, label="LibreLane-compatible KLayout routed DEF streamout")
    expected_outputs = {
        "macro_lef": staged_lef,
        "macro_met5_extract_script": met5_artifacts["script"],
        "macro_met5_obstruction_data": met5_artifacts["json"],
        "macro_met5_extract_log": met5_artifacts["log"],
        "mapped_netlist": mapped_path,
        "route_script": route_script,
        "routed_def": def_path,
        "routed_odb": attempt_dir / "routed.odb",
        "routed_gds": gds_path,
        "global_route_spef": attempt_dir / "global_route.spef",
        "detailed_route_drc": attempt_dir / "detailed_route_drc.rpt",
        "klayout_streamout_script": streamout_script,
        "synthesis_log": synth_log,
        "openroad_log": route_log,
        "klayout_streamout_log": streamout_log,
    }
    absent = [name for name, path in expected_outputs.items() if not path.is_file()]
    if absent:
        raise MacroBackendError(
            "physical flow returned successfully but omitted outputs: " + ", ".join(absent))
    return {
        "status": "placement_pdn_and_route_completed",
        "attempt_directory": str(attempt_dir.resolve()),
        "glue_library": "ICsprout55 H7CR release cells",
        "macro_internal_library": "ICsprout55 SC9T DFF is inside the generated macro",
        "macro_leaf_acceptance": "not established by this composite route",
        "macro_interface": macro_interface,
        "macro_obstruction_view": {
            "status": "source-GDS-derived conservative MET5 obstruction rectangles",
            "source_macro_gds": _ref(paths["generated macro GDS"]),
            "support_cell_gds": _ref(sc9t_dff_gds),
            "layer_map": _ref(klayout_layer_map),
            "extraction_data": _ref(met5_artifacts["json"]),
            "staged_physical_lef": _ref(staged_lef),
            "refinement": staged_obstruction_view,
        },
        "mapped_hold_cell": "LATLX1H7R",
        "macro_dimensions_um": {"width": macro_width, "height": macro_height},
        "power_pin_mapping": {
            "copy0.vdd": "VDD", "copy0.gnd": "VSS",
            "copy1.vdd": "VDD", "copy1.gnd": "VSS",
        },
        "pdn": {
            "core_stripes": {"vertical": "MET4", "horizontal": "MET5",
                             "width_um": 1.0, "pitch_um": 16.0, "offset_um": 0.5},
            "standard_cell_rails": {"layer": "MET1", "width_um": 0.16,
                                    "connection_layer": "MET4"},
            "macro_connection_layers": ["MET4", "MET5"],
        },
        "tools": {"yosys": _tool_identity(yosys_path),
                  "openroad": _tool_identity(openroad_path),
                  "klayout": _tool_identity(klayout_path)},
        "commands": {"yosys": [str(yosys_path), "-s", str(synth_path)],
                     "openroad": route_command,
                     "klayout_met5_extract": met5_artifacts["command"],
                     "klayout": streamout_command},
        "views": {name: _ref(path) for name, path in paths.items()},
        "artifacts": {name: _ref(path) for name, path in expected_outputs.items()},
        "scope_limits": [
            "The emitted detailed-route DRC report and generated PDN are not foundry DRC/LVS or power-integrity signoff.",
            "The global-route SPEF is an estimate, not accepted extracted timing evidence.",
            "The macro Liberty passed to OpenROAD is analytical and not accepted timing evidence.",
            "The staged macro MET5 obstruction geometry is conservatively bounded from recursively transformed source GDS shapes; original macro LEF/GDS remain unchanged.",
            "Aggregate STA remains blocked by the missing characterized macro timing model and accepted phase-aware SDC.",
        ],
    }


def _reject_unqualified_sta(config):
    """Fail closed instead of presenting analytical macro delay as aggregate STA."""
    if config["values"].get("analytical_delay", True):
        raise MacroBackendError(
            "aggregate STA is blocked: {} enables analytical_delay=True. "
            "Provide a non-diagnostic characterized Liberty for the actual macro "
            "with clk0/clk1 setup-hold, clk1 falling-edge-to-dout1 access, "
            "clock pulse-width, load/slew, and output-hold constraints, plus "
            "an accepted extracted wrapper SPEF and SDC; the default generated "
            "macro .lib is not signoff timing evidence.".format(config["path"]))
    raise MacroBackendError(
        "aggregate STA is blocked until the characterized macro timing contract, "
        "accepted extracted wrapper SPEF, and phase-aware SDC are supplied.")


def route_existing(out_dir=RUNS_PATH / "ics55_register_file_macro_32x8", *,
                   yosys="yosys", openroad="openroad", klayout="klayout",
                   tech_lef=DEFAULT_TECH_LEF, cell_lef=DEFAULT_H7CR_LEF,
                   cell_liberty=DEFAULT_H7CR_LIBERTY, cell_gds=DEFAULT_H7CR_GDS,
                   latch_map=DEFAULT_H7CR_LATCH_MAP,
                   sc9t_dff_gds=DEFAULT_SC9T_DFF_GDS,
                   klayout_tech=DEFAULT_KLAYOUT_TECH,
                   klayout_properties=DEFAULT_KLAYOUT_PROPERTIES,
                   klayout_layer_map=DEFAULT_KLAYOUT_LAYER_MAP, timeout=1800):
    """Route already generated compiler views without invoking OpenRAM or Icarus."""
    out_dir = Path(out_dir).expanduser().resolve()
    macro_dir = out_dir / "macro"
    wrapper_path = out_dir / (WRAPPER_MODULE + ".v")
    liberty_views = sorted(macro_dir.glob(MACRO_NAME + "*.lib"))
    route_result = _physical_route(
        out_dir=out_dir, macro_dir=macro_dir, wrapper_path=wrapper_path,
        liberty_views=liberty_views, yosys=yosys, openroad=openroad,
        klayout=klayout, tech_lef=tech_lef, cell_lef=cell_lef,
        cell_liberty=cell_liberty, cell_gds=cell_gds, latch_map=latch_map,
        sc9t_dff_gds=sc9t_dff_gds, klayout_tech=klayout_tech,
        klayout_properties=klayout_properties,
        klayout_layer_map=klayout_layer_map, timeout=timeout)
    manifest_path = Path(route_result["attempt_directory"]) / "physical_route.json"
    manifest_path.write_text(json.dumps({
        "schema": "ics55-register-file-macro-physical-route/1",
        "compiler_invoked": False,
        "simulation_invoked": False,
        "route": route_result,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def generate(out_dir=RUNS_PATH / "ics55_register_file_macro_32x8", *,
             config_path=CONFIG_PATH, iverilog="iverilog", vvp="vvp",
             compiler_python=None, timeout=1800, positive_delay_ns=3,
             negative_delay_ns=6, period_ns=10, physical_route=False,
             require_sta=False, yosys="yosys", openroad="openroad", klayout="klayout",
             tech_lef=DEFAULT_TECH_LEF, cell_lef=DEFAULT_H7CR_LEF,
             cell_liberty=DEFAULT_H7CR_LIBERTY, cell_gds=DEFAULT_H7CR_GDS,
             latch_map=DEFAULT_H7CR_LATCH_MAP,
             sc9t_dff_gds=DEFAULT_SC9T_DFF_GDS,
             klayout_tech=DEFAULT_KLAYOUT_TECH,
             klayout_properties=DEFAULT_KLAYOUT_PROPERTIES,
             klayout_layer_map=DEFAULT_KLAYOUT_LAYER_MAP):
    """Run the actual OpenRAM source compiler, emit the wrapper, and run Icarus.

    The macro config and generated views are immutable inputs to the wrapper;
    there is no behavioral/fallback leaf.  Output paths default into
    ``ics55_work/runs/`` and are kept for parent inspection.
    """
    config = validate_compiler_config(config_path)
    if require_sta:
        _reject_unqualified_sta(config)
    out_dir = Path(out_dir).expanduser().resolve()
    macro_dir = out_dir / "macro"
    sim_dir = out_dir / "simulation"
    macro_dir.mkdir(parents=True, exist_ok=True)
    sim_dir.mkdir(parents=True, exist_ok=True)
    if not COMPILER_PATH.is_file():
        raise MacroBackendError("OpenRAM source compiler is missing: " + str(COMPILER_PATH))
    iverilog_path = _resolve_executable(iverilog)
    vvp_path = _resolve_executable(vvp)
    iverilog_identity = _tool_identity(iverilog_path)
    vvp_identity = _tool_identity(vvp_path)
    python_executable = Path(compiler_python or sys.executable).expanduser().absolute()
    if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
        raise MacroBackendError("OpenRAM compiler Python is unavailable: " + str(python_executable))

    env = os.environ.copy()
    env["OPENRAM_HOME"] = str(ROOT / "compiler")
    env["OPENRAM_TECH"] = str(ROOT / "technology")
    compiler_tmp_dir = Path(tempfile.mkdtemp(prefix="openram_tmp_", dir=out_dir))
    env["OPENRAM_TMP"] = str(compiler_tmp_dir)
    macro_log = out_dir / "openram_compile.log"
    macro_command = [
        str(python_executable), str(COMPILER_PATH), "-o", MACRO_NAME,
        "-p", str(macro_dir), str(config["path"]),
    ]
    _run_command(macro_command, cwd=ROOT, env=env, timeout=timeout,
                 log_path=macro_log, label="OpenRAM 32x8 1W1R macro generation")

    macro_model = macro_dir / (MACRO_NAME + ".v")
    interface = _validate_generated_macro(macro_model)
    required_views = {
        "verilog": macro_model,
        "spice": macro_dir / (MACRO_NAME + ".sp"),
        "lvs_spice": macro_dir / (MACRO_NAME + ".lvs.sp"),
        "lef": macro_dir / (MACRO_NAME + ".lef"),
        "gds": macro_dir / (MACRO_NAME + ".gds"),
    }
    missing_views = [name for name, path in required_views.items() if not path.is_file()]
    liberty_views = sorted(macro_dir.glob(MACRO_NAME + "*.lib"))
    if missing_views:
        raise MacroBackendError("OpenRAM did not emit required real macro views: " + ", ".join(missing_views))
    if not liberty_views:
        raise MacroBackendError("OpenRAM did not emit a macro Liberty view")

    simulation_model = sim_dir / (MACRO_NAME + "_timescaled.v")
    model_source = macro_model.read_text(encoding="utf-8")
    simulation_model.write_text("`timescale 1ns/1ps\n" + model_source, encoding="utf-8")
    wrapper_path = out_dir / (WRAPPER_MODULE + ".v")
    testbench_path = out_dir / (TESTBENCH_MODULE + ".sv")
    wrapper_path.write_text(_wrapper_source(), encoding="utf-8")
    testbench_path.write_text(_testbench_source(), encoding="utf-8")

    if positive_delay_ns <= 0 or negative_delay_ns <= 0:
        raise MacroBackendError("both Icarus delay points must be nonzero")
    if negative_delay_ns <= positive_delay_ns:
        raise MacroBackendError("negative delay must exceed the positive delay")
    positive = _compile_and_run(
        mode="within_deadline", delay_ns=positive_delay_ns, period_ns=period_ns,
        expect_reject=False, model_path=simulation_model, wrapper_path=wrapper_path,
        testbench_path=testbench_path, out_dir=out_dir, iverilog_path=iverilog_path,
        vvp_path=vvp_path, timeout=timeout, env=env)
    negative = _compile_and_run(
        mode="over_deadline", delay_ns=negative_delay_ns, period_ns=period_ns,
        expect_reject=True, model_path=simulation_model, wrapper_path=wrapper_path,
        testbench_path=testbench_path, out_dir=out_dir, iverilog_path=iverilog_path,
        vvp_path=vvp_path, timeout=timeout, env=env)
    physical_result = None
    if physical_route:
        physical_result = _physical_route(
            out_dir=out_dir, macro_dir=macro_dir, wrapper_path=wrapper_path,
            liberty_views=liberty_views, yosys=yosys, openroad=openroad,
            klayout=klayout, tech_lef=tech_lef, cell_lef=cell_lef,
            cell_liberty=cell_liberty, cell_gds=cell_gds,
            latch_map=latch_map,
            sc9t_dff_gds=sc9t_dff_gds, klayout_tech=klayout_tech,
            klayout_properties=klayout_properties,
            klayout_layer_map=klayout_layer_map, timeout=timeout, env=env)

    report = {
        "schema": "openram-register-file-macro/1",
        "adapter": "ics55-register-file-macro-2r1w",
        "scope": "actual 32x8 [W0,R1] macro instantiated twice with 2R1W glue",
        "physical_signoff": False,
        "contract": {
            "cpu_port_order": ["R0", "R1", "W"],
            "macro_port_order": ["W0", "R1"],
            "macro_dimensions": {"words": 32, "bits": 8},
            "sample_edge": "posedge clk",
            "macro_read_access_edge": "negedge clk1",
            "read_visibility": "after falling-edge access and before the next rising-edge deadline",
            "pipeline_stages_added": 0,
            "write_broadcast": True,
            "read_during_write": "sampled same-address new-data bypass on each reader",
            "output_hold": "per-reader low-phase transparent latch; disabled reads preserve data across raw macro dout X",
            "storage_initialization": "none",
        },
        "sources": {
            "compiler": _ref(COMPILER_PATH),
            "config": _ref(config["path"]),
            "generated_model": _ref(macro_model),
            "simulation_model_with_timescale": _ref(simulation_model),
            "wrapper": _ref(wrapper_path),
            "testbench": _ref(testbench_path),
            "macro_views": {name: _ref(path) for name, path in required_views.items()},
            "liberty_views": [_ref(path) for path in liberty_views],
            "compiler_log": _ref(macro_log),
        },
        "macro_interface": interface,
        "tools": {
            "compiler_python": str(python_executable),
            "iverilog": iverilog_identity,
            "vvp": vvp_identity,
            "compiler_command": macro_command,
        },
        "behavioral_timing_regressions": {
            "model_delay_is_characterized": False,
            "model_delay_note": "OpenRAM DELAY/T_HOLD are arbitrary behavioral-model values, not physical timing evidence.",
            "period_ns": period_ns,
            "setup_guard_ns": 0.1,
            "positive": positive,
            "negative_over_budget": negative,
            "coverage": [
                "both reads active at once",
                "independent read addresses",
                "writer broadcast to both copies",
                "read0 same-address write bypass",
                "read1 same-address write bypass",
                "simultaneous dual-reader same-address bypass",
                "disabled read holds known data across raw macro output X",
                "disabled read holds uninitialized X",
                "post-sample request/address/data changes",
                "uninitialized storage remains unknown",
                "nonzero access delay meets and misses the explicit phase deadline",
            ],
        },
        "physical_composition": {
            "status": physical_result["status"] if physical_result else "not_generated",
            "macro_views_generated": ["GDS", "LEF", "SPICE", "LVS SPICE", "analytical Liberty", "Verilog"],
            "wrapper_views_generated": ["RTL Verilog"] + (
                ["mapped Verilog", "routed DEF", "routed ODB", "routed GDS",
                 "global-route SPEF", "PDN"] if physical_result else []),
            "aggregate_sta": "blocked-not-run",
            "signoff": False,
            "route": physical_result,
            "blocking_prerequisites": [
                "Non-diagnostic characterized macro Liberty with setup/hold at clk0/clk1 for csb0/addr0/din0 and csb1/addr1, phase-specific clk1 falling-edge-to-dout1 access arcs, output-load/slew coverage, and clock pulse-width limits; the default config sets analytical_delay=True.",
                "An accepted phase-aware SDC and extracted timing model for the routed wrapper, including broadcast write fanout, bypass comparator/data paths, and low-phase output-hold latch setup to the next rising edge.",
                "Separate physical review remains required: the current attempt2 macro strict physical-flat LVS is a mismatch; a routed composition or detailed-route DRC report does not establish leaf or composite LVS/DRC signoff.",
            ],
            "macro_pin_boundary": ["clk0", "csb0", "addr0", "din0", "clk1", "csb1", "addr1", "dout1"],
        },
    }
    report_path = out_dir / REPORT_NAME
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path,
                        default=RUNS_PATH / "ics55_register_file_macro_32x8")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--compiler-python", default=None,
                        help="Python interpreter with the source OpenRAM dependencies")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--period-ns", type=int, default=10)
    parser.add_argument("--positive-delay-ns", type=int, default=3)
    parser.add_argument("--negative-delay-ns", type=int, default=6)
    parser.add_argument("--physical-route", action="store_true",
                        help="map glue with released H7CR views and run OpenROAD PDN plus routing")
    parser.add_argument("--route-only", action="store_true",
                        help="reuse existing macro/ and wrapper outputs; skip compiler and Icarus")
    parser.add_argument("--sta", action="store_true",
                        help="request aggregate STA; fails closed until accepted macro timing constraints exist")
    parser.add_argument("--yosys", default="yosys")
    parser.add_argument("--openroad", default="openroad")
    parser.add_argument("--klayout", default="klayout")
    parser.add_argument("--tech-lef", type=Path, default=DEFAULT_TECH_LEF)
    parser.add_argument("--cell-lef", type=Path, default=DEFAULT_H7CR_LEF)
    parser.add_argument("--cell-liberty", type=Path, default=DEFAULT_H7CR_LIBERTY)
    parser.add_argument("--cell-gds", type=Path, default=DEFAULT_H7CR_GDS)
    parser.add_argument("--latch-map", type=Path, default=DEFAULT_H7CR_LATCH_MAP)
    parser.add_argument("--sc9t-dff-gds", type=Path, default=DEFAULT_SC9T_DFF_GDS)
    parser.add_argument("--klayout-tech", type=Path, default=DEFAULT_KLAYOUT_TECH)
    parser.add_argument("--klayout-properties", type=Path, default=DEFAULT_KLAYOUT_PROPERTIES)
    parser.add_argument("--klayout-layer-map", type=Path, default=DEFAULT_KLAYOUT_LAYER_MAP)
    args = parser.parse_args(argv)
    try:
        if args.route_only:
            if args.sta:
                _reject_unqualified_sta(validate_compiler_config(args.config))
            report_path = route_existing(
                args.out_dir, yosys=args.yosys, openroad=args.openroad,
                klayout=args.klayout, tech_lef=args.tech_lef,
                cell_lef=args.cell_lef, cell_liberty=args.cell_liberty,
                cell_gds=args.cell_gds, sc9t_dff_gds=args.sc9t_dff_gds,
                latch_map=args.latch_map,
                klayout_tech=args.klayout_tech,
                klayout_properties=args.klayout_properties,
                klayout_layer_map=args.klayout_layer_map, timeout=args.timeout)
        else:
            report_path = generate(
                args.out_dir, config_path=args.config, iverilog=args.iverilog, vvp=args.vvp,
                compiler_python=args.compiler_python, timeout=args.timeout,
                positive_delay_ns=args.positive_delay_ns,
                negative_delay_ns=args.negative_delay_ns, period_ns=args.period_ns,
                physical_route=args.physical_route, require_sta=args.sta,
                yosys=args.yosys, openroad=args.openroad, klayout=args.klayout,
                tech_lef=args.tech_lef, cell_lef=args.cell_lef,
                cell_liberty=args.cell_liberty, cell_gds=args.cell_gds,
                sc9t_dff_gds=args.sc9t_dff_gds,
                latch_map=args.latch_map,
                klayout_tech=args.klayout_tech,
                klayout_properties=args.klayout_properties,
                klayout_layer_map=args.klayout_layer_map)
    except (OSError, MacroBackendError, subprocess.SubprocessError) as exc:
        print("ICsprout55 macro register-file backend: " + str(exc), file=sys.stderr)
        return 2
    print(str(report_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
