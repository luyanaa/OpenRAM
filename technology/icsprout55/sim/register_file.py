"""Hash-bound behavioral F1 evidence for the replicated ICsprout55 register file.

This adapter generates a behavioral 1R1W storage leaf and a structural 2R1W
wrapper with two leaf copies. It establishes only the frozen functional access
contract; it makes no electrical, timing, physical, PPA, or signoff claim.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

SIM = Path(__file__).resolve().parent
TECH = SIM.parent
ROOT = SIM.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compiler.architecture.contracts import (
    ContractError,
    REGISTER_FILE_ACCESS_CONTRACT,
    canonical_bytes,
    fields,
    identity,
    number,
    validate_candidate,
    validate_measurement,
)

ADAPTER = "ics55-register-file-f1"
BACKEND = "replicated-1r1w"
REPORT_SCHEMA = "openram-register-file-f1/1"
METRICS = {
    "wen_pulse_ns": ("ns", "write"),
    "sa_setup_ns": ("ns", "read"),
    "bl_diff_v": ("V", "read"),
    "write_recovery_ns": ("ns", "write"),
    "area_um2": ("um^2", "macro"),
    "cycle_time_ns": ("ns", "macro"),
    "read_energy_pj": ("pJ", "read"),
    "write_energy_pj": ("pJ", "write"),
}
ARTIFACT_NAMES = (
    "model", "cycle_model", "wrapper", "testbench", "executable", "compile_log", "run_log",
)
COVERAGE = {
    "simultaneous_r0_r1_w": True,
    "same_address_new_data_bypass_r0": True,
    "same_address_new_data_bypass_r1": True,
    "distinct_read_addresses": True,
    "read_read_same_address": True,
    "read_only_cycles": True,
    "write_visibility": True,
    "uninitialized_read_unknown": True,
    "uninitialized_read_rejection": "Python cycle model rejects; Verilog exposes unknown output",
    "scope": "generated behavioral Verilog only; no electrical or physical claim",
}


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _digest(path)}


def _resolve_executable(command):
    candidate = Path(command).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    resolved = shutil.which(str(command))
    if resolved is None:
        raise ValueError("executable is missing: " + str(command))
    return Path(resolved).resolve()


def _version(executable):
    for flag in ("-V", "--version"):
        try:
            result = subprocess.run(
                [str(executable), flag], text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.stdout.strip():
            return result.stdout.strip()
    raise ValueError("could not establish simulator version: " + str(executable))


def _tool_identity(command):
    executable = _resolve_executable(command)
    return {
        "path": str(executable),
        "sha256": _digest(executable),
        "version": _version(executable),
        "mode": "execution-enabled",
    }


def _validate_tool_identity(value, name):
    fields(value, {"path", "sha256", "version", "mode"}, label=name + " identity")
    if (not isinstance(value["path"], str) or not value["path"]
            or not isinstance(value["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
            or not isinstance(value["version"], str) or not value["version"]
            or value["mode"] != "execution-enabled"):
        raise ValueError("F1 report lacks complete {} identity".format(name))
    path = Path(value["path"]).expanduser().resolve()
    if not path.is_file():
        raise ValueError("F1 tool is missing: " + name)
    if _digest(path) != value["sha256"]:
        raise ValueError("F1 tool hash drift: " + name)
    return path


def _validate_ref(reference, base):
    fields(reference, {"path", "sha256"}, label="register-file F1 artifact")
    if (not isinstance(reference["path"], str) or not reference["path"]
            or not isinstance(reference["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])):
        raise ValueError("F1 artifact requires a path and lowercase SHA-256")
    path = Path(reference["path"]).expanduser()
    path = path.resolve() if path.is_absolute() else (Path(base) / path).resolve()
    if not path.is_file():
        raise ValueError("F1 artifact is missing: " + str(path))
    if _digest(path) != reference["sha256"]:
        raise ValueError("F1 artifact hash drift: " + str(path))
    return path

def _referenced_paths(value, base):
    if isinstance(value, dict):
        if set(value) in ({"path", "sha256"}, {"path", "sha256", "size_bytes"}):
            path = Path(value["path"]).expanduser()
            yield path.resolve() if path.is_absolute() else (Path(base) / path).resolve()
        for child in value.values():
            yield from _referenced_paths(child, base)
    elif isinstance(value, list):
        for child in value:
            yield from _referenced_paths(child, base)


def _candidate_shape(candidate):
    candidate = validate_candidate(deepcopy(candidate))
    if candidate["family"] != "register_file":
        raise ContractError("register-file F1 adapter requires family register_file")
    if candidate["backend"] != BACKEND:
        raise ContractError("register-file F1 adapter requires backend " + BACKEND)
    if candidate["access_contract"] != REGISTER_FILE_ACCESS_CONTRACT:
        raise ContractError("candidate differs from the frozen 2R1W register-file contract")
    if candidate["environment"]["fidelity"] != "F1":
        raise ContractError("register-file F1 adapter requires F1 fidelity")
    if candidate["environment"]["initialization"] != "uninitialized":
        raise ContractError("register-file F1 requires uninitialized storage semantics")
    parameters = candidate["parameters"]
    if "word_size" not in parameters or "num_words" not in parameters:
        raise ContractError("register-file candidate requires word_size and num_words")
    word_size, num_words = parameters["word_size"], parameters["num_words"]
    if type(word_size) is not int or word_size < 1:
        raise ContractError("register-file word_size must be a positive integer")
    if type(num_words) is not int or num_words < 2:
        raise ContractError("register-file num_words must be an integer of at least 2 for distinct reads")
    if num_words & (num_words - 1):
        raise ContractError("register-file num_words must be a power of two for generated Verilog addressing")
    return candidate, word_size, num_words, max(1, (num_words - 1).bit_length())


def _leaf_source(word_size, num_words, address_width):
    return "\n".join([
        "// Generated behavioral 1R1W leaf; contents intentionally have no reset or initialization.",
        "module rf_1r1w_leaf #(parameter integer DATA_WIDTH = {},".format(word_size),
        "                        parameter integer ADDR_WIDTH = {},".format(address_width),
        "                        parameter integer DEPTH = {}) (".format(num_words),
        "    input wire clk,",
        "    input wire r_en,",
        "    input wire [ADDR_WIDTH-1:0] r_addr,",
        "    output reg [DATA_WIDTH-1:0] r_data,",
        "    input wire w_en,",
        "    input wire [ADDR_WIDTH-1:0] w_addr,",
        "    input wire [DATA_WIDTH-1:0] w_data",
        ");",
        "  reg [DATA_WIDTH-1:0] words [0:DEPTH-1];",
        "  always @(posedge clk) begin",
        "    if (r_en) r_data <= words[r_addr];",
        "    if (w_en) words[w_addr] <= w_data;",
        "  end",
        "endmodule",
        "",
    ])


def _wrapper_source(word_size, num_words, address_width):
    return "\n".join([
        "// Generated replicated 2R1W wrapper; each reader owns exactly one copy.",
        "module rf_replicated_2r1w #(parameter integer DATA_WIDTH = {},".format(word_size),
        "                             parameter integer ADDR_WIDTH = {},".format(address_width),
        "                             parameter integer DEPTH = {}) (".format(num_words),
        "    input wire clk,",
        "    input wire r0_en,",
        "    input wire [ADDR_WIDTH-1:0] r0_addr,",
        "    output wire [DATA_WIDTH-1:0] r0_data,",
        "    input wire r1_en,",
        "    input wire [ADDR_WIDTH-1:0] r1_addr,",
        "    output wire [DATA_WIDTH-1:0] r1_data,",
        "    input wire w_en,",
        "    input wire [ADDR_WIDTH-1:0] w_addr,",
        "    input wire [DATA_WIDTH-1:0] w_data",
        ");",
        "  wire [DATA_WIDTH-1:0] copy0_read_data;",
        "  wire [DATA_WIDTH-1:0] copy1_read_data;",
        "  reg r0_bypass_valid; reg r1_bypass_valid;",
        "  reg [DATA_WIDTH-1:0] r0_bypass_data; reg [DATA_WIDTH-1:0] r1_bypass_data;",
        "",
        "  // R0 owns copy0; the one writer is broadcast to both copies.",
        "  rf_1r1w_leaf #(.DATA_WIDTH(DATA_WIDTH), .ADDR_WIDTH(ADDR_WIDTH), .DEPTH(DEPTH)) copy0 (",
        "    .clk(clk), .r_en(r0_en), .r_addr(r0_addr), .r_data(copy0_read_data),",
        "    .w_en(w_en), .w_addr(w_addr), .w_data(w_data)",
        "  );",
        "  // R1 owns copy1; the one writer is broadcast to both copies.",
        "  rf_1r1w_leaf #(.DATA_WIDTH(DATA_WIDTH), .ADDR_WIDTH(ADDR_WIDTH), .DEPTH(DEPTH)) copy1 (",
        "    .clk(clk), .r_en(r1_en), .r_addr(r1_addr), .r_data(copy1_read_data),",
        "    .w_en(w_en), .w_addr(w_addr), .w_data(w_data)",
        "  );",
        "",
        "  // Capture bypass state with the leaf's one-cycle synchronous read output.",
        "  always @(posedge clk) begin",
        "    if (r0_en) begin",
        "      r0_bypass_valid <= w_en && (w_addr == r0_addr);",
        "      if (w_en && (w_addr == r0_addr)) r0_bypass_data <= w_data;",
        "    end",
        "    if (r1_en) begin",
        "      r1_bypass_valid <= w_en && (w_addr == r1_addr);",
        "      if (w_en && (w_addr == r1_addr)) r1_bypass_data <= w_data;",
        "    end",
        "  end",
        "  assign r0_data = r0_bypass_valid ? r0_bypass_data : copy0_read_data;",
        "  assign r1_data = r1_bypass_valid ? r1_bypass_data : copy1_read_data;",
        "endmodule",
        "",
    ])


def _testbench_source(word_size, num_words, address_width):
    return "\n".join([
        "`timescale 1ns/1ps",
        "module tb;",
        "  localparam integer DATA_WIDTH = {};".format(word_size),
        "  localparam integer ADDR_WIDTH = {};".format(address_width),
        "  localparam integer DEPTH = {};".format(num_words),
        "  localparam [DATA_WIDTH-1:0] DATA_ONE = 1;",
        "  localparam [DATA_WIDTH-1:0] DATA_UNKNOWN = {DATA_WIDTH{1'bx}};",
        "  reg clk;",
        "  reg r0_en; reg [ADDR_WIDTH-1:0] r0_addr; wire [DATA_WIDTH-1:0] r0_data;",
        "  reg r1_en; reg [ADDR_WIDTH-1:0] r1_addr; wire [DATA_WIDTH-1:0] r1_data;",
        "  reg w_en; reg [ADDR_WIDTH-1:0] w_addr; reg [DATA_WIDTH-1:0] w_data;",
        "  rf_replicated_2r1w #(.DATA_WIDTH(DATA_WIDTH), .ADDR_WIDTH(ADDR_WIDTH), .DEPTH(DEPTH)) dut (",
        "    .clk(clk), .r0_en(r0_en), .r0_addr(r0_addr), .r0_data(r0_data),",
        "    .r1_en(r1_en), .r1_addr(r1_addr), .r1_data(r1_data),",
        "    .w_en(w_en), .w_addr(w_addr), .w_data(w_data)",
        "  );",
        "  always #5 clk = ~clk;",
        "",
        "  task step;",
        "    input read0_enable;",
        "    input [ADDR_WIDTH-1:0] read0_address;",
        "    input read1_enable;",
        "    input [ADDR_WIDTH-1:0] read1_address;",
        "    input write_enable;",
        "    input [ADDR_WIDTH-1:0] write_address;",
        "    input [DATA_WIDTH-1:0] write_data;",
        "    input [DATA_WIDTH-1:0] expected_read0;",
        "    input [DATA_WIDTH-1:0] expected_read1;",
        "    begin",
        "      @(negedge clk);",
        "      r0_en = read0_enable; r0_addr = read0_address;",
        "      r1_en = read1_enable; r1_addr = read1_address;",
        "      w_en = write_enable; w_addr = write_address; w_data = write_data;",
        "      @(posedge clk); #1;",
        "      if (read0_enable && (r0_data !== expected_read0)) begin",
        '        $display("RF_F1_FAIL read0 expected=%b actual=%b", expected_read0, r0_data);',
        "        $finish;",
        "      end",
        "      if (read1_enable && (r1_data !== expected_read1)) begin",
        '        $display("RF_F1_FAIL read1 expected=%b actual=%b", expected_read1, r1_data);',
        "        $finish;",
        "      end",
        "    end",
        "  endtask",
        "",
        "  initial begin",
        "    clk = 0; r0_en = 0; r1_en = 0; w_en = 0;",
        "    r0_addr = 0; r1_addr = 0; w_addr = 0; w_data = 0;",
        "    // An uninitialized read is invalid/unknown, not silently zero-filled.",
        "    step(1, 0, 0, 0, 0, 0, 0, DATA_UNKNOWN, 0);",
        "    // Initialize valid read locations without observing uninitialized data.",
        "    step(0, 0, 0, 0, 1, 0, DATA_ONE, 0, 0);",
        "    step(0, 0, 0, 0, 1, 1, 0, 0, 0);",
        "    // Concurrent distinct reads and write; R0 exercises new-data bypass.",
        "    step(1, 0, 1, 1, 1, 0, 0, 0, 0);",
        "    // Both readers exercise same-address read/write bypass on address 1.",
        "    step(1, 1, 1, 1, 1, 1, DATA_ONE, DATA_ONE, DATA_ONE);",
        "    // Read-only distinct and equal-address cycles.",
        "    step(1, 0, 1, 1, 0, 0, 0, 0, DATA_ONE);",
        "    step(1, 0, 1, 0, 0, 0, 0, 0, 0);",
        "    // A later read observes a committed write with no concurrent writer.",
        "    step(0, 0, 0, 0, 1, 0, DATA_ONE, 0, 0);",
        "    step(1, 0, 1, 1, 0, 0, 0, DATA_ONE, DATA_ONE);",
        '    $display("RF_F1_PASS");',
        "    $finish;",
        "  end",
        "endmodule",
        "",
    ])


def _source_paths(out_dir):
    return {
        "model": Path(out_dir) / "rf_1r1w_leaf.v",
        "cycle_model": ROOT / "compiler/architecture/register_file.py",
        "wrapper": Path(out_dir) / "rf_replicated_2r1w.v",
        "testbench": Path(out_dir) / "tb.v",
    }


def _compile_command(iverilog_path, executable, source_paths):
    return [
        str(iverilog_path), "-g2012", "-s", "tb", "-o", str(executable),
        str(source_paths["model"]), str(source_paths["wrapper"]),
        str(source_paths["testbench"]),
    ]


def _run_case(source_paths, out_dir, iverilog, vvp, timeout):
    executable = Path(out_dir) / "sim.vvp"
    compile_command = _compile_command(iverilog, executable, source_paths)
    try:
        compiled = subprocess.run(
            compile_command, cwd=str(out_dir), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        (Path(out_dir) / "compile.log").write_text(str(exc), encoding="utf-8")
        raise ValueError("Icarus compile timed out") from exc
    compile_log = Path(out_dir) / "compile.log"
    compile_log.write_text(compiled.stdout, encoding="utf-8")
    if compiled.returncode != 0:
        raise ValueError("Icarus compile failed; see " + str(compile_log))
    run_command = [str(vvp), str(executable)]
    try:
        ran = subprocess.run(
            run_command, cwd=str(out_dir), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        (Path(out_dir) / "run.log").write_text(str(exc), encoding="utf-8")
        raise ValueError("vvp run timed out") from exc
    run_log = Path(out_dir) / "run.log"
    run_log.write_text(ran.stdout, encoding="utf-8")
    if ran.returncode != 0:
        raise ValueError("vvp run failed; see " + str(run_log))
    if "RF_F1_PASS" not in ran.stdout or "RF_F1_FAIL" in ran.stdout:
        raise ValueError("generated register-file testbench did not report RF_F1_PASS")
    return {
        "executable": executable,
        "compile_command": compile_command,
        "compile_returncode": compiled.returncode,
        "run_command": run_command,
        "run_returncode": ran.returncode,
    }


def generate(candidate, out_dir, *, iverilog="iverilog", vvp="vvp", timeout=120):
    """Generate, execute, and record one immutable behavioral F1 evidence bundle.

    The input candidate must have F1 fidelity, uninitialized contents, at least
    three words (to exercise distinct readers and writer addressing), and a
    positive ``word_size``. The function fails closed if simulation is missing,
    any cycle check fails, a source changes during execution, or either tool
    binary changes while being run.
    """
    candidate, word_size, num_words, address_width = _candidate_shape(candidate)
    timeout = number(timeout, "timeout", 0, strict=True)
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = _source_paths(out_dir)
    paths["model"].write_text(_leaf_source(word_size, num_words, address_width), encoding="utf-8")
    paths["wrapper"].write_text(_wrapper_source(word_size, num_words, address_width), encoding="utf-8")
    paths["testbench"].write_text(_testbench_source(word_size, num_words, address_width), encoding="utf-8")

    iverilog_path = _resolve_executable(iverilog)
    vvp_path = _resolve_executable(vvp)
    tools = {"iverilog": _tool_identity(str(iverilog_path)), "vvp": _tool_identity(str(vvp_path))}
    sources_before = {name: _digest(path) for name, path in paths.items()}
    tool_hashes_before = {name: tool["sha256"] for name, tool in tools.items()}
    execution = _run_case(paths, out_dir, iverilog_path, vvp_path, timeout)
    executable = execution["executable"]
    artifact_paths = {**paths, "executable": executable,
                      "compile_log": out_dir / "compile.log", "run_log": out_dir / "run.log"}
    missing = [name for name, path in artifact_paths.items() if not path.is_file()]
    if missing:
        raise ValueError("F1 artifact bundle is incomplete: " + ", ".join(missing))
    sources_after = {name: _digest(path) for name, path in paths.items()}
    tools_after = {"iverilog": _tool_identity(str(iverilog_path)),
                   "vvp": _tool_identity(str(vvp_path))}
    tool_hashes_after = {name: tool["sha256"] for name, tool in tools_after.items()}
    if sources_after != sources_before:
        raise ValueError("register-file model, wrapper, or testbench changed during execution")
    if tool_hashes_after != tool_hashes_before:
        raise ValueError("Icarus tool changed during register-file execution")
    if tools_after != tools:
        raise ValueError("Icarus tool identity changed during register-file execution")

    report = {
        "schema": REPORT_SCHEMA,
        "adapter": ADAPTER,
        "candidate": deepcopy(candidate),
        "candidate_sha256": identity(candidate),
        "artifacts": {name: _ref(path) for name, path in artifact_paths.items()},
        "tools": tools,
        "execution": {
            "source_capture": "before-launch",
            "source_sha256_before": sources_before,
            "source_sha256_after": sources_after,
            "post_execution_unchanged": True,
            "tool_sha256_before": tool_hashes_before,
            "tool_sha256_after": tool_hashes_after,
            "timeout_s": timeout,
            "compile": {"command": execution["compile_command"],
                        "returncode": execution["compile_returncode"]},
            "run": {"command": execution["run_command"],
                    "returncode": execution["run_returncode"]},
        },
        "results": {"functional": True},
        "coverage": deepcopy(COVERAGE),
    }
    report_path = out_dir / "register_file_f1.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def _validate_report(report_path, paths, candidate):
    """Validate a replay report and every source, log, executable, and tool hash."""
    candidate, word_size, num_words, address_width = _candidate_shape(candidate)
    report_path = Path(report_path).resolve()
    if paths.get("report_sha256") is not None and _digest(report_path) != paths["report_sha256"]:
        raise ValueError("F1 report hash drift")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    fields(report, {"schema", "adapter", "candidate", "candidate_sha256", "artifacts",
                    "tools", "execution", "results", "coverage"},
           label="register-file F1 report")
    if report["schema"] != REPORT_SCHEMA or report["adapter"] != ADAPTER:
        raise ValueError("unsupported register-file F1 report schema or adapter")
    if canonical_bytes(report["candidate"]) != canonical_bytes(candidate):
        raise ValueError("F1 report candidate contract or tuple differs from candidate")
    if report["candidate_sha256"] != identity(candidate):
        raise ValueError("F1 report candidate hash differs from candidate")
    if set(report["artifacts"]) != set(ARTIFACT_NAMES):
        raise ValueError("F1 report artifact set is incomplete or contains unknown files")
    artifact_paths = {}
    for name in ARTIFACT_NAMES:
        artifact_paths[name] = _validate_ref(report["artifacts"][name], report_path.parent)
        expected = paths.get(name)
        if expected is not None and Path(expected).resolve() != artifact_paths[name]:
            raise ValueError("F1 report {} path differs from registered artifact".format(name))
    if report["artifacts"]["model"] != _ref(artifact_paths["model"]):
        raise ValueError("F1 model artifact identity is inconsistent")

    expected_sources = {
        "model": _leaf_source(word_size, num_words, address_width),
        "wrapper": _wrapper_source(word_size, num_words, address_width),
        "testbench": _testbench_source(word_size, num_words, address_width),
        "cycle_model": (ROOT / "compiler/architecture/register_file.py").read_text(encoding="utf-8"),
    }
    for name, expected_source in expected_sources.items():
        if artifact_paths[name].read_text(encoding="utf-8") != expected_source:
            raise ValueError("F1 generated {} differs from the canonical adapter source".format(name))

    tools = report["tools"]
    fields(tools, {"iverilog", "vvp"}, label="F1 simulator identities")
    tool_paths = {name: _validate_tool_identity(tools[name], name) for name in ("iverilog", "vvp")}
    execution = report["execution"]
    fields(execution, {"source_capture", "source_sha256_before", "source_sha256_after",
                       "post_execution_unchanged", "tool_sha256_before", "tool_sha256_after",
                       "timeout_s", "compile", "run"}, label="F1 execution record")
    source_hashes = {name: report["artifacts"][name]["sha256"]
                     for name in ("model", "cycle_model", "wrapper", "testbench")}
    if (execution["source_capture"] != "before-launch"
            or execution["post_execution_unchanged"] is not True
            or execution["source_sha256_before"] != source_hashes
            or execution["source_sha256_after"] != source_hashes):
        raise ValueError("F1 report lacks stable before/after source hashes")
    tool_hashes = {name: tools[name]["sha256"] for name in ("iverilog", "vvp")}
    if (execution["tool_sha256_before"] != tool_hashes
            or execution["tool_sha256_after"] != tool_hashes):
        raise ValueError("F1 report lacks stable before/after tool hashes")
    timeout = number(execution["timeout_s"], "timeout_s", 0, strict=True)
    if timeout <= 0:
        raise ValueError("F1 execution timeout must be positive")
    expected_executable = str(artifact_paths["executable"])
    expected_compile = _compile_command(tool_paths["iverilog"], artifact_paths["executable"], artifact_paths)
    expected_run = [str(tool_paths["vvp"]), expected_executable]
    compile_record = execution["compile"]
    run_record = execution["run"]
    fields(compile_record, {"command", "returncode"}, label="F1 compile execution")
    fields(run_record, {"command", "returncode"}, label="F1 run execution")
    if compile_record["command"] != expected_compile or run_record["command"] != expected_run:
        raise ValueError("F1 execution commands are not bound to the exact tools and sources")
    if type(compile_record["returncode"]) is not int or compile_record["returncode"] != 0:
        raise ValueError("F1 simulator compilation did not succeed")
    if type(run_record["returncode"]) is not int or run_record["returncode"] != 0:
        raise ValueError("F1 simulator execution did not succeed")
    log = artifact_paths["run_log"].read_text(encoding="utf-8")
    if "RF_F1_PASS" not in log or "RF_F1_FAIL" in log:
        raise ValueError("F1 run log lacks the deterministic pass marker")
    if report["results"] != {"functional": True}:
        raise ValueError("F1 report does not establish functional contract evidence")
    if report["coverage"] != COVERAGE:
        raise ValueError("F1 coverage does not match the required behavioral scenarios")
    return report, artifact_paths


def _admission_paths(admission):
    paths = {}
    model_artifacts = admission.get("model", {}).get("artifacts", {})
    for name, raw_path in admission.get("artifacts", {}).items():
        path = Path(raw_path).resolve()
        if not path.is_file():
            raise ValueError("registered register-file artifact is missing: " + name)
        reference = model_artifacts.get(name)
        if reference is None:
            raise ValueError("registered register-file artifact is undeclared by the model: " + name)
        if _digest(path) != reference.get("sha256"):
            raise ValueError("registered register-file artifact changed: " + name)
        paths[name] = path
    if "report" in paths:
        paths["report_sha256"] = model_artifacts["report"]["sha256"]
    return paths


def fingerprint(admissions_or_registry, *, iverilog="iverilog", vvp="vvp",
                 replay_only=True, timeout=120):
    """Return cache identity for adapter sources, bound inputs, and tool mode."""
    obj = admissions_or_registry
    pending = set()
    if hasattr(obj, "capabilities"):
        for entry in obj.capabilities.values():
            for implementation in entry["implementations"]:
                for model in implementation["models"].values():
                    if model["adapter"] != ADAPTER:
                        continue
                    for reference in model["artifacts"].values():
                        path = Path(reference["path"]).expanduser()
                        pending.add((obj.path.parent / path).resolve()
                                    if not path.is_absolute() else path.resolve())
    else:
        admissions = [obj] if isinstance(obj, dict) else obj
        for admission in admissions:
            for path in admission.get("artifacts", {}).values():
                pending.add(Path(path).resolve())
    dependencies = {}
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        dependencies[str(path)] = _digest(path)
        if path.suffix.lower() != ".json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for reference in _referenced_paths(value, path.parent):
            pending.add(reference)
        if isinstance(value, dict):
            for tool in value.get("tools", {}).values():
                if isinstance(tool, dict) and isinstance(tool.get("path"), str):
                    pending.add(Path(tool["path"]).expanduser().resolve())
    sources = [Path(__file__).resolve(), ROOT / "compiler/architecture/register_file.py",
               ROOT / "compiler/architecture/contracts.py",
               ROOT / "compiler/architecture/search.py",
               ROOT / "compiler/architecture/__main__.py",
               SIM / "native_register_file.py",
               TECH / "configs" / "ics55_2r1w_native.py"]
    tool = {"iverilog": {"requested": iverilog}, "vvp": {"requested": vvp},
            "mode": "replay-only"}
    execution_timeout = None
    if not replay_only:
        tool = {"iverilog": _tool_identity(iverilog), "vvp": _tool_identity(vvp),
                "mode": "execution-enabled"}
        execution_timeout = number(timeout, "timeout", 0, strict=True)
    return {"adapter": ADAPTER, "sources": [_ref(path) for path in sources],
            "dependencies": dependencies, "tool": tool, "python": sys.version,
            "execution_timeout_s": execution_timeout}


def _null_measurements(candidate, evidence):
    environment = candidate["environment"]
    return [validate_measurement({
        "name": name, "value": None, "unit": unit, "operation": operation,
        "port": None, "domain": deepcopy(environment), "fidelity": environment["fidelity"],
        "evidence": evidence,
    }) for name, (unit, operation) in METRICS.items()]


def evaluate(admission, out_dir, *, execute=False, iverilog="iverilog", vvp="vvp",
             timeout=120, required_margins=None):
    """Replay registered F1 evidence, or execute a fresh local F1 bundle."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = admission["candidate"]
    result = {
        "measurements": [],
        "checks": {"functional": None, "topology": None, "drc": None, "lvs": None,
                   "pex_accepted": None, "characterization": None},
        "artifacts": {},
        "estimated": True,
        "signoff": False,
        "details": {
            "scope": "replicated behavioral register-file F1 only; no electrical or physical signoff",
            "electrical_status": "not measured by F1",
            "ppa_status": "not measured by F1",
            "native_layout": "unsupported for more than two total physical ports",
        },
    }
    paths = {}
    try:
        paths = _admission_paths(admission)
        report_path = paths.get("report")
        if report_path is None:
            if not execute:
                raise ValueError("no registered register-file F1 report; local execution not authorized")
            report_path = generate(candidate, out_dir / "register-file-execution",
                                   iverilog=iverilog, vvp=vvp, timeout=timeout)
            paths.update({"report": report_path})
        expected_report_hash = paths.get("report_sha256")
        validate_paths = paths if expected_report_hash is None else {
            **paths, "report_sha256": expected_report_hash,
        }
        report, artifact_paths = _validate_report(report_path, validate_paths, candidate)
        refs = {"register_file_report": _ref(report_path)}
        refs.update(("register_file_" + name, reference)
                    for name, reference in report["artifacts"].items())
        result["artifacts"] = refs
        result["checks"]["functional"] = True
        result["details"].update(
            observation="hash-bound-iverilog-register-file-f1",
            report=report["results"], coverage=report["coverage"],
            execution=report["execution"],
        )
        result["measurements"] = _null_measurements(candidate, list(refs.values()))
    except (ValueError, ContractError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        result["details"]["error"] = str(exc)
        if not result["measurements"]:
            evidence = []
            for name, path in paths.items():
                if name == "report_sha256" or not Path(path).is_file():
                    continue
                evidence.append(_ref(path))
            if evidence:
                try:
                    result["measurements"] = _null_measurements(candidate, evidence)
                except (ContractError, ValueError, TypeError):
                    result["measurements"] = []
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--candidate", required=True, type=Path,
                        help="canonical openram-candidate/1 register_file JSON")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        candidate = validate_candidate(json.loads(args.candidate.read_text(encoding="utf-8")))
        report = generate(candidate, args.out_dir, iverilog=args.iverilog,
                          vvp=args.vvp, timeout=args.timeout)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print("ICsprout55 register-file F1 adapter: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"report": str(report.resolve()), "functional": True,
                      "adapter": ADAPTER}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
