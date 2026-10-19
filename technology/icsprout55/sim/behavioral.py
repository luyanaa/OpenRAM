#!/usr/bin/env python3
"""Hash-bound Icarus behavioral-contract evidence for ICsprout55 SRAMs.

This adapter proves only the generated OpenRAM behavioral access contract.  It
checks independent two-port accesses, cross-port read/write visibility, and
same-physical-row write rejection under ``OPENRAM_ASSERTIONS``.  It does not
measure electrical timing, parasitics, physical equivalence, or signoff.
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
ROOT = SIM.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compiler.architecture.contracts import (ContractError, fields, load_json, number,
                                             validate_candidate, validate_measurement)

if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))
import tbgen

ADAPTER = "ics55-iverilog"
REPORT_SCHEMA = "openram-iverilog-behavior/1"
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


def _tool_identity(command):
    executable = _resolve_executable(command)
    result = subprocess.run([str(executable), "-V"], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=15, check=False)
    if not result.stdout.strip():
        result = subprocess.run([str(executable), "--version"], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=15, check=False)
    return {"path": str(executable), "sha256": _digest(executable),
            "version": result.stdout.strip(), "mode": "execution-enabled"}


def _validate_tool_identity(identity, name):
    if (not isinstance(identity, dict) or not identity.get("path")
            or not identity.get("sha256") or not identity.get("version")):
        raise ValueError("F1 report lacks complete {} identity".format(name))
    if not re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]):
        raise ValueError("F1 {} identity has an invalid SHA-256".format(name))
    return identity


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


def _validate_ref(reference, base):
    fields(reference, {"path", "sha256"}, label="behavioral artifact")
    if (not isinstance(reference["path"], str) or not reference["path"]
            or not isinstance(reference["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", reference["sha256"])):
        raise ValueError("behavioral artifact requires a path and lowercase SHA-256")
    path = Path(reference["path"]).expanduser()
    path = path.resolve() if path.is_absolute() else (Path(base) / path).resolve()
    if not path.is_file():
        raise ValueError("behavioral artifact is missing: " + str(path))
    if _digest(path) != reference["sha256"]:
        raise ValueError("behavioral artifact hash drift: " + str(path))
    return path


def _admission_paths(admission):
    paths = {name: Path(path).resolve() for name, path in admission["artifacts"].items()}
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError("registered behavioral artifact is missing: " + name)
        reference = admission["model"]["artifacts"].get(name)
        if reference is None:
            raise ValueError("registered behavioral artifact is not declared by model: " + name)
        if _digest(path) != reference["sha256"]:
            raise ValueError("registered behavioral artifact changed: " + name)
    if "model" not in paths:
        raise ValueError("F1 model requires a model artifact")
    return paths


def fingerprint(admissions_or_registry, *, iverilog="iverilog", vvp="vvp",
                 replay_only=True, timeout=120):
    """Return the adapter/source/input identity used by the search cache."""
    obj = admissions_or_registry
    paths = set()
    if hasattr(obj, "capabilities"):
        for entry in obj.capabilities.values():
            for implementation in entry["implementations"]:
                for model in implementation["models"].values():
                    if model["adapter"] != ADAPTER:
                        continue
                    for reference in model["artifacts"].values():
                        path = Path(reference["path"]).expanduser()
                        paths.add((obj.path.parent / path).resolve()
                                  if not path.is_absolute() else path.resolve())
    else:
        admissions = [obj] if isinstance(obj, dict) else obj
        for admission in admissions:
            paths.update(Path(path).resolve() for path in admission["artifacts"].values())
    dependencies = {}
    pending = sorted(paths)
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        dependencies[str(path)] = _digest(path)
        if path.suffix.lower() == ".json":
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            pending.extend(_referenced_paths(value, path.parent))
    sources = [SIM / "behavioral.py", SIM / "tbgen.py"]
    sources.extend(SIM.parents[2] / ("compiler/architecture/" + name)
                   for name in ("contracts.py", "search.py", "__main__.py"))
    tool = {"iverilog": {"requested": iverilog}, "vvp": {"requested": vvp},
            "mode": "replay-only"}
    if not replay_only:
        tool = {"iverilog": _tool_identity(iverilog), "vvp": _tool_identity(vvp),
                "mode": "execution-enabled"}
    return {"adapter": ADAPTER, "sources": [_ref(path) for path in sources],
            "dependencies": dependencies, "tool": tool, "python": sys.version,
            "execution_timeout_s": None if replay_only else number(timeout, "timeout", 0, strict=True)}


def _parse_fields(tokens):
    values = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError("trace token lacks '=': " + token)
        name, value = token.split("=", 1)
        if not name or name in values:
            raise ValueError("trace contains duplicate/empty field: " + name)
        values[name] = value
    return values


def _read_trace(path):
    cycles, outputs = {}, {}
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            tokens = line.split()
            if not tokens:
                continue
            if tokens[0] == "C":
                if len(tokens) < 3:
                    raise ValueError("incomplete C trace record at line {}".format(line_number))
                cycle = int(tokens[1])
                if cycle in cycles:
                    raise ValueError("duplicate C trace cycle {}".format(cycle))
                cycles[cycle] = _parse_fields(tokens[2:])
            elif tokens[0] == "E":
                if len(tokens) != 3:
                    raise ValueError("incomplete E trace record at line {}".format(line_number))
                cycle = int(tokens[1])
                values = _parse_fields(tokens[2:])
                for name, value in values.items():
                    key = (cycle, name)
                    if key in outputs:
                        raise ValueError("duplicate E trace output {}".format(name))
                    outputs[key] = value
            else:
                raise ValueError("unknown trace record at line {}".format(line_number))
    if not cycles:
        raise ValueError("behavioral trace has no cycles")
    return cycles, outputs


def _bits(value, width, label):
    if not re.fullmatch(r"[01]{{{}}}".format(width), value):
        raise ValueError("{} is not a {}-bit binary value: {}".format(label, width, value))
    return int(value, 2)


def _row(address, words_per_row):
    if type(words_per_row) is not int or words_per_row <= 0:
        raise ValueError("words_per_row must be a positive integer")
    return address // words_per_row


def _validate_trace(path, roles, address_width, data_width, words_per_row):
    cycles, outputs = _read_trace(path)
    memory = {}
    expected = {}
    coverage = {
        "port_reads": {str(port): 0 for port in roles},
        "port_writes": {str(port): 0 for port in roles},
        "concurrent_distinct_writes": 0,
        "cross_port_read_write_cycles": 0,
        "read_checks": 0,
        "bit_checks": 0,
    }
    for cycle in sorted(cycles):
        values = cycles[cycle]
        operations = {}
        writes, reads = [], []
        for port, role in roles.items():
            prefix = str(port)
            if values.get("role{}".format(port)) != role:
                raise ValueError("trace role mismatch on port {} cycle {}".format(port, cycle))
            operation = values.get("op{}".format(port))
            if operation not in ("I", "R", "W"):
                raise ValueError("trace operation missing/invalid on port {} cycle {}".format(port, cycle))
            csb = values.get("csb{}".format(port))
            if csb != ("1" if operation == "I" else "0"):
                raise ValueError("trace CSB disagrees with op{} on cycle {}".format(port, cycle))
            address = _bits(values.get("addr{}".format(port), ""), address_width,
                            "addr{}".format(port))
            operations[port] = (operation, address)
            if role == "RW":
                web = values.get("web{}".format(port))
                expected_web = "0" if operation == "W" else "1"
                if web != expected_web:
                    raise ValueError("trace WEB disagrees with op{} on cycle {}".format(port, cycle))
            if role in ("RW", "W"):
                data = _bits(values.get("din{}".format(port), ""), data_width,
                             "din{}".format(port))
            else:
                data = None
            if operation == "W":
                if role not in ("RW", "W"):
                    raise ValueError("trace writes through read-only port {}".format(port))
                writes.append((port, address, data))
                coverage["port_writes"][prefix] += 1
            elif operation == "R":
                if role not in ("RW", "R"):
                    raise ValueError("trace reads through write-only port {}".format(port))
                reads.append((port, address))
                coverage["port_reads"][prefix] += 1
        if len(writes) > 1:
            rows = {_row(address, words_per_row) for _, address, _ in writes}
            if len(rows) == 1:
                raise ValueError("positive behavioral trace contains a same-row write conflict")
            coverage["concurrent_distinct_writes"] += 1
        if len(writes) and len(reads):
            if any(_row(waddr, words_per_row) == _row(raddr, words_per_row)
                   for _, waddr, _ in writes for _, raddr in reads):
                raise ValueError("positive behavioral trace contains same-row read/write ambiguity")
            coverage["cross_port_read_write_cycles"] += 1
        for _, address, data in writes:
            memory[address] = data
        for port, address in reads:
            if address not in memory:
                raise ValueError("trace reads an unwritten address {} on cycle {}".format(address, cycle))
            expected[(cycle, "dout{}".format(port))] = format(memory[address], "0{}b".format(data_width))
    for key, value in expected.items():
        actual = outputs.get(key)
        if actual != value:
            raise ValueError("behavioral read mismatch at {}: expected {}, got {}".format(
                key, value, actual))
        _bits(actual, data_width, key[1])
    extras = set(outputs) - set(expected)
    if extras:
        raise ValueError("behavioral trace has unexpected read outputs: {}".format(sorted(extras)))
    coverage["read_checks"] = len(expected)
    coverage["bit_checks"] = len(expected) * data_width
    coverage["passed"] = bool(expected) and all(coverage["port_reads"].values())
    return coverage


def _decls(aw, dw, ports):
    declarations, connections = [], []
    for port in ports:
        index = port["idx"]
        declarations.extend(["reg clk{}; reg csb{};".format(index, index),
                             "reg [{}:0] addr{};".format(aw - 1, index)])
        if port["kind"] == "RW":
            declarations.append("reg web{};".format(index))
        if port["kind"] in ("RW", "W"):
            declarations.append("reg [{}:0] din{};".format(dw - 1, index))
        if port["kind"] in ("RW", "R"):
            declarations.append("wire [{}:0] dout{};".format(dw - 1, index))
        connections.extend(".{0}({0})".format(signal) for signal in port["sigs"])
    return declarations, connections


def _cycle_values(roles, aw, dw, operations):
    assigns, fields_out = [], []
    for port, role in roles.items():
        op, address, data = operations.get(port, ("I", 0, 0))
        selected = 0 if op != "I" else 1
        assigns.extend(["csb{}={};".format(port, selected),
                        "addr{}={}'d{};".format(port, aw, address)])
        fields_out.extend(["role{}={}".format(port, role), "op{}={}".format(port, op),
                           "csb{}={}".format(port, selected),
                           "addr{}={:0{}b}".format(port, address, aw)])
        if role == "RW":
            web = 0 if op == "W" else 1
            assigns.append("web{}={};".format(port, web))
            fields_out.append("web{}={}".format(port, web))
        if role in ("RW", "W"):
            data = 0 if data is None else data
            assigns.append("din{}={}'d{};".format(port, dw, data))
            fields_out.append("din{}={:0{}b}".format(port, data, dw))
    return " ".join(assigns), " ".join(fields_out)


def _write_contract_testbench(model, out_dir, module, aw, dw, ports):
    roles = {port["idx"]: port["kind"] for port in ports}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    if roles != {0: "RW", 1: "RW"}:
        raise ValueError("current F1 contract smoke requires exactly two RW ports")
    declarations, connections = _decls(aw, dw, ports)
    schedule = [
        {},
        {0: ("W", 1, 0xA5), 1: ("W", 2, 0x5A)},
        {0: ("R", 2, None), 1: ("R", 1, None)},
        {0: ("W", 3, 0x3C), 1: ("R", 2, None)},
        {0: ("R", 3, None), 1: ("W", 4, 0xC3)},
        {},
    ]
    lines = ["`timescale 1ns/1ps", "module tb;"] + declarations
    lines.append("  {} #(.DELAY(0), .T_HOLD(0), .VERBOSE(0)) dut ({});".format(module, ", ".join(connections)))
    lines.extend(["  integer f;", "  initial begin", '    f = $fopen("trace.txt");'])
    for port in ports:
        index = port["idx"]
        lines.append("    clk{}=0; csb{}=1; addr{}=0;".format(index, index, index))
        if port["kind"] == "RW":
            lines.append("    web{}=1;".format(index))
        if port["kind"] in ("RW", "W"):
            lines.append("    din{}=0;".format(index))
    for cycle, operations in enumerate(schedule):
        assigns, trace_fields = _cycle_values(roles, aw, dw, operations)
        lines.append("    " + assigns)
        lines.append('    $fdisplay(f, "C {} {}");'.format(cycle, trace_fields))
        lines.append("    #5; clk0=1; clk1=1;")
        lines.append("    #5; clk0=0; clk1=0;")
        for port, operation in operations.items():
            if operation[0] == "R":
                lines.append('    #1 $fdisplay(f, "E {} dout{}=%b", dout{});'.format(
                    cycle, port, port))
        if any(operation[0] == "R" for operation in operations.values()):
            lines.append("    #0;")
    lines.extend(["    $fclose(f); $finish;", "  end", "endmodule", ""])
    path = Path(out_dir) / "tb.v"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _write_conflict_testbench(out_dir, module, aw, dw, ports):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    roles = {port["idx"]: port["kind"] for port in ports}
    if roles != {0: "RW", 1: "RW"}:
        raise ValueError("current F1 conflict smoke requires exactly two RW ports")
    declarations, connections = _decls(aw, dw, ports)
    lines = ["`timescale 1ns/1ps", "module tb;"] + declarations
    lines.append("  {} #(.DELAY(0), .T_HOLD(0), .VERBOSE(0)) dut ({});".format(module, ", ".join(connections)))
    lines.extend(["  // F1_CONFLICT_STIMULUS same-row two-port writes: port0=1 port1=1",
                  "  initial begin", "    clk0=0; clk1=0; csb0=1; csb1=1; web0=1; web1=1;",
                  "    addr0=0; addr1=0; din0=0; din1=0;",
                  "    #1; csb0=0; csb1=0; web0=0; web1=0; addr0={}'d1; addr1={}'d1;".format(aw, aw),
                  "    din0={}'hA5; din1={}'h5A;".format(dw, dw),
                  "    #4; clk0=1; clk1=1;",
                  '    #2 $fatal(1, "F1 conflict guard did not fire");',
                  "  end", "endmodule", ""])
    path = Path(out_dir) / "tb.v"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _run_case(model, tb, out_dir, iverilog, vvp, timeout):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    executable = out_dir / "sim.vvp"
    compile_command = [str(iverilog), "-g2012", "-DOPENRAM_ASSERTIONS", "-s", "tb",
                       "-o", str(executable), str(model), str(tb)]
    try:
        compiled = subprocess.run(compile_command, cwd=str(out_dir), text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        (out_dir / "compile.log").write_text(str(exc), encoding="utf-8")
        raise ValueError("Icarus compile timed out: " + str(tb)) from exc
    (out_dir / "compile.log").write_text(compiled.stdout, encoding="utf-8")
    if compiled.returncode != 0:
        raise ValueError("Icarus compile failed: " + str(tb))
    try:
        ran = subprocess.run([str(vvp), str(executable)], cwd=str(out_dir), text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        (out_dir / "run.log").write_text(str(exc), encoding="utf-8")
        raise ValueError("vvp run timed out: " + str(tb)) from exc
    (out_dir / "run.log").write_text(ran.stdout, encoding="utf-8")
    return {"compile_returncode": compiled.returncode, "run_returncode": ran.returncode,
            "compile_command": compile_command,
            "run_command": [str(vvp), str(executable)]}




def _validate_report(report_path, paths, candidate):
    report_path = Path(report_path).resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    fields(report, {"schema", "candidate", "environment", "model", "artifacts", "tools", "execution", "results", "coverage"},
           label="F1 behavioral report")
    if report["schema"] != REPORT_SCHEMA:
        raise ValueError("unsupported F1 behavioral report schema")
    if report["candidate"]["parameters"] != candidate["parameters"]:
        raise ValueError("F1 report parameter tuple differs from candidate")
    if report["candidate"]["access_contract"] != candidate["access_contract"]:
        raise ValueError("F1 report access contract differs from candidate")
    if report["environment"] != candidate["environment"]:
        raise ValueError("F1 report environment differs from candidate")
    required_artifacts = {
        "model", "tbgen_tb", "tbgen_trace", "tbgen_meta", "tbgen_compile_log",
        "tbgen_run_log", "contract_tb", "contract_trace", "contract_compile_log",
        "contract_run_log", "conflict_tb", "conflict_compile_log", "conflict_run_log",
    }
    if set(report["artifacts"]) != required_artifacts:
        raise ValueError("F1 report artifact set is incomplete or contains unknown files")
    model_path = _validate_ref(report["model"], report_path.parent)
    if model_path != paths["model"]:
        raise ValueError("F1 report model path differs from registered model")
    artifact_paths = {}
    for name in required_artifacts:
        reference = report["artifacts"][name]
        artifact_paths[name] = _validate_ref(reference, report_path.parent)
    if report["artifacts"]["model"] != report["model"]:
        raise ValueError("F1 report artifact model differs from top-level model")
    for tool_name in ("iverilog", "vvp"):
        _validate_tool_identity(report["tools"].get(tool_name), tool_name)
    execution = report["execution"]
    if execution.get("model_capture") != "before-launch" or execution.get("post_execution_unchanged") is not True:
        raise ValueError("F1 report lacks execution-time model stability")
    if execution.get("model_sha256_before") != report["model"]["sha256"]:
        raise ValueError("F1 report model pre-launch hash differs from model reference")
    if execution.get("model_sha256_after") != report["model"]["sha256"]:
        raise ValueError("F1 report model post-launch hash differs from model reference")
    for tool_name in ("iverilog", "vvp"):
        before = execution.get("tool_sha256_before", {}).get(tool_name)
        after = execution.get("tool_sha256_after", {}).get(tool_name)
        identity = report["tools"][tool_name]
        if before != identity["sha256"] or after != identity["sha256"] or before != after:
            raise ValueError("F1 tool hash drift: " + tool_name)
    metadata_path = artifact_paths["tbgen_meta"]
    metadata_value = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata_value.get("model_parameters") != {"DELAY": 0, "T_HOLD": 0, "VERBOSE": 0}:
        raise ValueError("F1 report does not bind deterministic behavioral parameters")
    module, address_width, data_width, ports, _ = tbgen.parse_ports(model_path)
    if (metadata_value.get("name") != module or metadata_value.get("aw") != address_width
            or metadata_value.get("dw") != data_width or metadata_value.get("ports") != ports):
        raise ValueError("F1 tbgen metadata does not match the registered model")
    if candidate["parameters"]["word_size"] != data_width:
        raise ValueError("F1 model width differs from candidate")
    if candidate["parameters"]["num_words"] != (1 << address_width):
        raise ValueError("F1 model depth differs from candidate")
    roles = {port["idx"]: port["kind"] for port in ports}
    if roles != {0: "RW", 1: "RW"}:
        raise ValueError("F1 behavioral interface must be two RW ports")
    expected_roles = {index: role for index, role in enumerate(candidate["access_contract"]["ports"])}
    if roles != expected_roles:
        raise ValueError("behavioral model roles do not match candidate access contract")
    positive_stats = _validate_trace(artifact_paths["tbgen_trace"], roles, address_width, data_width,
                                     candidate["parameters"]["words_per_row"])
    contract_stats = _validate_trace(artifact_paths["contract_trace"], roles, address_width, data_width,
                                     candidate["parameters"]["words_per_row"])
    for stats, label in ((positive_stats, "positive"), (contract_stats, "contract")):
        if not stats.get("passed"):
            raise ValueError("F1 {} behavioral trace did not pass".format(label))
        if any((role in ("RW", "R") and not stats["port_reads"].get(str(port), 0))
               or (role in ("RW", "W") and not stats["port_writes"].get(str(port), 0))
               for port, role in roles.items()):
            raise ValueError("F1 {} trace lacks eligible port coverage".format(label))
    if contract_stats["concurrent_distinct_writes"] < 1 or contract_stats["cross_port_read_write_cycles"] < 1:
        raise ValueError("F1 contract trace lacks required cross-port coverage")
    if report["results"].get("positive") != positive_stats or report["results"].get("contract") != contract_stats:
        raise ValueError("F1 report trace statistics drifted")
    expected_coverage = {
        "ports": sorted(roles), "roles": {str(port): role for port, role in roles.items()},
        "independent_dual_port_access": True, "cross_port_read_write": True,
        "same_physical_row_write_rejection": True,
        "scope": "generated behavioral Verilog only; no electrical or physical claim",
    }
    if report["coverage"] != expected_coverage:
        raise ValueError("F1 report coverage does not match parsed model interface")
    conflict = report["results"].get("conflict_rejection")
    conflict_text = artifact_paths["conflict_run_log"].read_text(encoding="utf-8")
    if conflict is not True or "OpenRAM overlapping writes to the same physical row" not in conflict_text:
        raise ValueError("F1 conflict run did not prove the assertion message")
    if report["results"].get("functional") is not True:
        raise ValueError("F1 report does not claim functional pass")
    expected_tbs = {"positive": artifact_paths["tbgen_tb"], "contract": artifact_paths["contract_tb"],
                    "conflict": artifact_paths["conflict_tb"]}
    for case, tb in expected_tbs.items():
        record = execution.get(case)
        if (not isinstance(record, dict) or not isinstance(record.get("compile"), list)
                or not isinstance(record.get("run"), list)
                or not isinstance(record.get("compile_returncode"), int)
                or not isinstance(record.get("run_returncode"), int)):
            raise ValueError("F1 report lacks complete execution record: " + case)
        compile_command, run_command = record["compile"], record["run"]
        expected_executable = str(tb.parent / "sim.vvp")
        if (str(model_path) not in compile_command or str(tb) not in compile_command
                or "-DOPENRAM_ASSERTIONS" not in compile_command
                or "-o" not in compile_command
                or compile_command[compile_command.index("-o") + 1] != expected_executable):
            raise ValueError("F1 execution command is not bound to model/testbench/output/assertions: " + case)
        if (compile_command[0] != report["tools"]["iverilog"]["path"]
                or run_command != [report["tools"]["vvp"]["path"], expected_executable]):
            raise ValueError("F1 execution command tool/runtime binding drifted: " + case)
    if execution["positive"].get("compile_returncode") != 0 or execution["positive"].get("run_returncode") != 0:
        raise ValueError("F1 positive execution did not succeed")
    if execution["contract"].get("compile_returncode") != 0 or execution["contract"].get("run_returncode") != 0:
        raise ValueError("F1 contract execution did not succeed")
    if execution["conflict"].get("compile_returncode") != 0 or execution["conflict"].get("run_returncode", 0) == 0:
        raise ValueError("F1 conflict execution status is invalid")
    conflict_tb = artifact_paths["conflict_tb"].read_text(encoding="utf-8")
    if "F1_CONFLICT_STIMULUS" not in conflict_tb or "addr0=" not in conflict_tb or "addr1=" not in conflict_tb:
        raise ValueError("F1 conflict testbench lacks expected same-row two-port stimulus")
    return report, positive_stats, contract_stats


def generate(model, out_dir, candidate, *, iverilog="iverilog", vvp="vvp", timeout=120):
    """Execute and emit one immutable F1 behavioral evidence report."""
    candidate = validate_candidate(deepcopy(candidate))
    model = Path(model).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    before_model = _digest(model)
    module, address_width, data_width, ports, _ = tbgen.parse_ports(model)
    roles = {port["idx"]: port["kind"] for port in ports}
    expected_roles = {index: role for index, role in
                      enumerate(candidate["access_contract"]["ports"])}
    if roles != expected_roles:
        raise ValueError("behavioral model roles do not match candidate access contract")
    if candidate["parameters"]["word_size"] != data_width:
        raise ValueError("behavioral model width differs from candidate word_size")
    if candidate["parameters"]["num_words"] != (1 << address_width):
        raise ValueError("behavioral model depth differs from candidate num_words")
    iverilog_path = _resolve_executable(iverilog)
    vvp_path = _resolve_executable(vvp)
    tools = {"iverilog": _tool_identity(str(iverilog_path)),
             "vvp": _tool_identity(str(vvp_path))}
    tool_sha256_before = {name: identity["sha256"] for name, identity in tools.items()}

    tbgen_dir = out_dir / "tbgen"
    tbgen.gen(model, tbgen_dir, 24, 1)
    positive_run = _run_case(model, tbgen_dir / "tb.v", tbgen_dir,
                             iverilog_path, vvp_path, timeout)
    if positive_run["run_returncode"] != 0:
        raise ValueError("positive behavioral smoke failed")
    positive_stats = _validate_trace(tbgen_dir / "trace.txt", roles, address_width,
                                     data_width, candidate["parameters"]["words_per_row"])

    contract_dir = out_dir / "contract"
    contract_tb = _write_contract_testbench(model, contract_dir, module, address_width,
                                            data_width, ports)
    contract_run = _run_case(model, contract_tb, contract_dir,
                             iverilog_path, vvp_path, timeout)
    if contract_run["run_returncode"] != 0:
        raise ValueError("positive cross-port contract smoke failed")
    contract_stats = _validate_trace(contract_dir / "trace.txt", roles, address_width,
                                     data_width, candidate["parameters"]["words_per_row"])
    if contract_stats["concurrent_distinct_writes"] < 1 or contract_stats["cross_port_read_write_cycles"] < 1:
        raise ValueError("cross-port contract smoke lacks required independent accesses")

    conflict_dir = out_dir / "conflict_same_row"
    conflict_tb = _write_conflict_testbench(conflict_dir, module, address_width,
                                             data_width, ports)
    conflict_run = _run_case(model, conflict_tb, conflict_dir,
                             iverilog_path, vvp_path, timeout)
    conflict_log = (conflict_dir / "run.log").read_text(encoding="utf-8")
    conflict_rejection = (
        conflict_run["compile_returncode"] == 0
        and conflict_run["run_returncode"] != 0
        and "OpenRAM overlapping writes to the same physical row" in conflict_log)
    if not conflict_rejection:
        raise ValueError("same-row conflict smoke did not terminate on the assertion")
    if _digest(model) != before_model:
        raise ValueError("behavioral model changed during Icarus execution")

    artifact_paths = {
        "model": model,
        "tbgen_tb": tbgen_dir / "tb.v",
        "tbgen_trace": tbgen_dir / "trace.txt",
        "tbgen_meta": tbgen_dir / "trace.meta.json",
        "tbgen_compile_log": tbgen_dir / "compile.log",
        "tbgen_run_log": tbgen_dir / "run.log",
        "contract_tb": contract_tb,
        "contract_trace": contract_dir / "trace.txt",
        "contract_compile_log": contract_dir / "compile.log",
        "contract_run_log": contract_dir / "run.log",
        "conflict_tb": conflict_tb,
        "conflict_compile_log": conflict_dir / "compile.log",
        "conflict_run_log": conflict_dir / "run.log",
    }
    if any(not path.is_file() for path in artifact_paths.values()):
        raise ValueError("F1 behavioral execution produced an incomplete artifact bundle")
    tool_sha256_after = {"iverilog": _digest(iverilog_path), "vvp": _digest(vvp_path)}
    if tool_sha256_after != tool_sha256_before:
        raise ValueError("Icarus tool changed during behavioral execution")
    if _digest(model) != before_model:
        raise ValueError("behavioral model changed during Icarus execution")
    report = {
        "schema": REPORT_SCHEMA,
        "candidate": {"parameters": deepcopy(candidate["parameters"]),
                       "access_contract": deepcopy(candidate["access_contract"])},
        "environment": deepcopy(candidate["environment"]),
        "model": _ref(model),
        "artifacts": {name: _ref(path) for name, path in artifact_paths.items()},
        "tools": tools,
        "execution": {
            "model_capture": "before-launch",
            "model_sha256_before": before_model,
            "model_sha256_after": _digest(model),
            "post_execution_unchanged": True,
            "tool_sha256_before": tool_sha256_before,
            "tool_sha256_after": tool_sha256_after,
            "timeout_s": timeout,
            "positive": {"compile": positive_run["compile_command"],
                         "run": positive_run["run_command"],
                         "compile_returncode": positive_run["compile_returncode"],
                         "run_returncode": positive_run["run_returncode"]},
            "contract": {"compile": contract_run["compile_command"],
                         "run": contract_run["run_command"],
                         "compile_returncode": contract_run["compile_returncode"],
                         "run_returncode": contract_run["run_returncode"]},
            "conflict": {"compile": conflict_run["compile_command"],
                         "run": conflict_run["run_command"],
                         "compile_returncode": conflict_run["compile_returncode"],
                         "run_returncode": conflict_run["run_returncode"]},
        },
        "results": {
            "positive": positive_stats,
            "contract": contract_stats,
            "conflict_rejection": conflict_rejection,
            "functional": True,
        },
        "coverage": {
            "ports": sorted(roles),
            "roles": {str(port): role for port, role in roles.items()},
            "independent_dual_port_access": True,
            "cross_port_read_write": True,
            "same_physical_row_write_rejection": True,
            "scope": "generated behavioral Verilog only; no electrical or physical claim",
        },
    }
    report_path = out_dir / "behavioral.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def _null_measurements(candidate, evidence):
    env = candidate["environment"]
    return [validate_measurement({
        "name": name, "value": None, "unit": unit, "operation": operation,
        "port": None, "domain": deepcopy(env), "fidelity": env["fidelity"],
        "evidence": evidence,
    }) for name, (unit, operation) in METRICS.items()]


def evaluate(admission, out_dir, *, execute=False, iverilog="iverilog", vvp="vvp",
             timeout=120, required_margins=None):
    """Replay a registered F1 report, or explicitly run an unregistered child."""
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    candidate = admission["candidate"]
    result = {
        "measurements": [],
        "checks": {"functional": None, "topology": None, "drc": None, "lvs": None,
                    "pex_accepted": None, "characterization": None},
        "artifacts": {}, "estimated": True, "signoff": False,
        "details": {"scope": "generated behavioral Verilog contract only; not electrical/physical signoff",
                     "characterization_status": "not measured by F1",
                     "electrical_status": "not measured by F1"},
    }
    try:
        paths = _admission_paths(admission)
        report_path = paths.get("report")
        if report_path is None:
            if not execute:
                raise ValueError("no registered F1 behavioral report; local execution not authorized")
            report_path = generate(paths["model"], out_dir / "behavioral-execution", candidate,
                                   iverilog=iverilog, vvp=vvp, timeout=timeout)
        report, positive, contract = _validate_report(report_path, paths, candidate)
        refs = {"behavioral_report": _ref(report_path), "behavioral_model": _ref(paths["model"])}
        refs.update(("behavioral_" + name, reference)
                    for name, reference in report["artifacts"].items())
        result["artifacts"] = refs
        evidence = list(refs.values())
        result["checks"]["functional"] = True
        result["details"].update(
            report=report["results"], coverage=report["coverage"],
            positive_coverage=positive, contract_coverage=contract,
            execution=report["execution"], observation="hash-bound-iverilog-behavior")
        result["measurements"] = _null_measurements(candidate, evidence)
    except (ValueError, ContractError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        result["details"]["error"] = str(exc)
        if not result["measurements"]:
            evidence = list(result["artifacts"].values())
            if evidence:
                result["measurements"] = _null_measurements(candidate, evidence)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--candidate", required=True, type=Path,
                        help="canonical openram-candidate/1 JSON object")
    parser.add_argument("--iverilog", default="iverilog")
    parser.add_argument("--vvp", default="vvp")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        candidate = validate_candidate(load_json(args.candidate))
        report = generate(args.model, args.out_dir, candidate, iverilog=args.iverilog,
                          vvp=args.vvp, timeout=args.timeout)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print("ICsprout55 behavioral adapter: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"report": str(report.resolve()), "functional": True,
                      "adapter": ADAPTER}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
