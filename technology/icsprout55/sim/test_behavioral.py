"""Fail-closed F1 behavioral evidence and replay tests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

SIM = Path(__file__).resolve().parent
REPO = SIM.parents[2]
for path in (SIM, REPO):
    if str(path) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(path))

import behavioral
import tbgen
from compiler.architecture.contracts import validate_candidate


class BehavioralEvidenceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.model = self.root / "fixture.v"
        self.model.write_text(
            """// OpenRAM SRAM model
// Words: 2
// Word size: 1
module fixture(
// Port 0: RW
    clk0,csb0,web0,addr0,din0,dout0,
// Port 1: RW
    clk1,csb1,web1,addr1,din1,dout1
  );
  parameter DATA_WIDTH = 1;
  parameter ADDR_WIDTH = 1;
  parameter DELAY = 0;
  parameter T_HOLD = 0;
  parameter VERBOSE = 0;
endmodule
""",
            encoding="utf-8",
        )
        _, address_width, data_width, ports, _ = tbgen.parse_ports(self.model)
        self.candidate = validate_candidate({
            "schema": "openram-candidate/1",
            "capability_id": "fixture.sram.2rw",
            "family": "sram",
            "backend": "fixture",
            "access_contract": {
                "ports": ["RW", "RW"],
                "clocking": "independent",
                "same_row_write": "forbidden",
                "read_during_write": "undefined-same-row",
            },
            "parameters": {
                "word_size": data_width,
                "num_words": 1 << address_width,
                "words_per_row": 1,
                "variant": 1,
            },
            "environment": {
                "corner": "snfp",
                "voltage_v": 1.08,
                "temperature_c": 125,
                "period_ns": 10,
                "cload_ff": 5,
                "slew_ns": 0.03,
                "skew1": 0,
                "initialization": "zero-voltage",
                "fidelity": "F1",
            },
        })
        self.roles = {port["idx"]: port["kind"] for port in ports}
        self.artifacts = self._write_artifacts()
        self.report_path = self._write_report()

    @staticmethod
    def _trace_line(cycle, operations):
        fields = []
        for port in (0, 1):
            operation, address, data = operations.get(port, ("I", 0, 0))
            selected = 0 if operation != "I" else 1
            fields.extend([
                "role{}=RW".format(port),
                "op{}={}".format(port, operation),
                "csb{}={}".format(port, selected),
                "addr{}={:01b}".format(port, address),
                "web{}={}".format(port, 0 if operation == "W" else 1),
                "din{}={}".format(port, data),
            ])
        return "C {} {}\n".format(cycle, " ".join(fields))

    def _write_trace(self, path, cycles, outputs):
        text = "".join(self._trace_line(cycle, operations)
                       for cycle, operations in enumerate(cycles))
        text += "".join("E {} dout{}={}\n".format(cycle, port, value)
                        for cycle, port, value in outputs)
        path.write_text(text, encoding="utf-8")

    def _write_artifacts(self):
        root = self.root / "evidence"
        root.mkdir()
        paths = {name: root / filename for name, filename in {
            "tbgen_tb": "tbgen_tb.v", "tbgen_trace": "tbgen_trace.txt",
            "tbgen_meta": "tbgen_meta.json", "tbgen_compile_log": "tbgen_compile.log",
            "tbgen_run_log": "tbgen_run.log", "contract_tb": "contract_tb.v",
            "contract_trace": "contract_trace.txt", "contract_compile_log": "contract_compile.log",
            "contract_run_log": "contract_run.log", "conflict_tb": "conflict_tb.v",
            "conflict_compile_log": "conflict_compile.log", "conflict_run_log": "conflict_run.log",
        }.items()}
        paths["tbgen_tb"].write_text("module tb; endmodule\n", encoding="utf-8")
        paths["contract_tb"].write_text("module tb; endmodule\n", encoding="utf-8")
        paths["conflict_tb"].write_text(
            "// F1_CONFLICT_STIMULUS\naddr0=1; addr1=1;\n", encoding="utf-8")
        paths["conflict_run_log"].write_text(
            "OpenRAM overlapping writes to the same physical row on ports 0 and 1 are undefined\n",
            encoding="utf-8")
        positive_cycles = [
            {},
            {0: ("W", 0, 1)},
            {0: ("R", 0, 0), 1: ("W", 1, 0)},
            {1: ("R", 1, 0)},
            {0: ("W", 0, 0)},
            {1: ("W", 1, 1)},
            {0: ("R", 0, 0), 1: ("R", 1, 0)},
        ]
        self._write_trace(paths["tbgen_trace"], positive_cycles,
                          [(2, 0, "1"), (3, 1, "0"), (6, 0, "0"), (6, 1, "1")])
        contract_cycles = [
            {},
            {0: ("W", 0, 1), 1: ("W", 1, 0)},
            {0: ("R", 1, 0), 1: ("W", 0, 0)},
            {1: ("R", 0, 0)},
            {0: ("R", 1, 0)},
        ]
        self._write_trace(paths["contract_trace"], contract_cycles,
                          [(2, 0, "0"), (3, 1, "0"), (4, 0, "0")])
        _, address_width, data_width, ports, _ = tbgen.parse_ports(self.model)
        paths["tbgen_meta"].write_text(json.dumps({
            "schema_version": 1,
            "kind": "ics55_tbgen_trace",
            "name": "fixture",
            "aw": address_width,
            "dw": data_width,
            "ports": ports,
            "model_parameters": {"DELAY": 0, "T_HOLD": 0, "VERBOSE": 0},
        }, sort_keys=True), encoding="utf-8")
        for name, path in paths.items():
            if name in ("tbgen_trace", "contract_trace", "tbgen_meta"):
                continue
            if not path.exists():
                path.write_text("", encoding="utf-8")
        return paths

    def _write_report(self):
        report_root = self.root / "report"
        report_root.mkdir()
        all_artifacts = {name: behavioral._ref(path) for name, path in self.artifacts.items()}
        all_artifacts["model"] = behavioral._ref(self.model)
        positive = behavioral._validate_trace(
            self.artifacts["tbgen_trace"], self.roles, 1, 1, 1)
        contract = behavioral._validate_trace(
            self.artifacts["contract_trace"], self.roles, 1, 1, 1)
        tool_dir = self.root / "tools"
        tool_dir.mkdir()
        iverilog = tool_dir / "iverilog"
        vvp = tool_dir / "vvp"
        iverilog.write_text("fixture iverilog\n", encoding="utf-8")
        vvp.write_text("fixture vvp\n", encoding="utf-8")
        tool_refs = {
            "iverilog": {"path": str(iverilog), "sha256": behavioral._digest(iverilog),
                         "version": "fixture-iverilog", "mode": "execution-enabled"},
            "vvp": {"path": str(vvp), "sha256": behavioral._digest(vvp),
                    "version": "fixture-vvp", "mode": "execution-enabled"},
        }

        def execution(case, tb, returncode=0):
            executable = str(tb.parent / "sim.vvp")
            return {
                "compile": [str(iverilog), "-g2012", "-DOPENRAM_ASSERTIONS", "-s", "tb",
                            "-o", executable, str(self.model), str(tb)],
                "run": [str(vvp), executable],
                "compile_returncode": 0,
                "run_returncode": returncode,
            }

        execution_records = {
            "positive": execution("positive", self.artifacts["tbgen_tb"]),
            "contract": execution("contract", self.artifacts["contract_tb"]),
            "conflict": execution("conflict", self.artifacts["conflict_tb"], 1),
        }
        report = {
            "schema": behavioral.REPORT_SCHEMA,
            "candidate": {
                "parameters": deepcopy(self.candidate["parameters"]),
                "access_contract": deepcopy(self.candidate["access_contract"]),
            },
            "environment": deepcopy(self.candidate["environment"]),
            "model": all_artifacts["model"],
            "artifacts": all_artifacts,
            "tools": tool_refs,
            "execution": {
                "model_capture": "before-launch",
                "model_sha256_before": all_artifacts["model"]["sha256"],
                "model_sha256_after": all_artifacts["model"]["sha256"],
                "post_execution_unchanged": True,
                "tool_sha256_before": {name: value["sha256"] for name, value in tool_refs.items()},
                "tool_sha256_after": {name: value["sha256"] for name, value in tool_refs.items()},
                "timeout_s": 30,
                **execution_records,
            },
            "results": {
                "positive": positive,
                "contract": contract,
                "conflict_rejection": True,
                "functional": True,
            },
            "coverage": {
                "ports": [0, 1],
                "roles": {"0": "RW", "1": "RW"},
                "independent_dual_port_access": True,
                "cross_port_read_write": True,
                "same_physical_row_write_rejection": True,
                "scope": "generated behavioral Verilog only; no electrical or physical claim",
            },
        }
        report_path = report_root / "behavioral.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                               encoding="utf-8")
        return report_path

    def _load_report(self):
        return json.loads(self.report_path.read_text(encoding="utf-8"))

    def _validate(self, report=None, candidate=None):
        if report is not None:
            self.report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                        encoding="utf-8")
        return behavioral._validate_report(
            self.report_path, {"model": self.model}, candidate or self.candidate)

    def test_valid_report_replays_all_required_coverage(self):
        _, positive, contract = self._validate()
        self.assertTrue(positive["passed"])
        self.assertTrue(contract["passed"])
        self.assertEqual(contract["concurrent_distinct_writes"], 1)
        self.assertGreaterEqual(contract["cross_port_read_write_cycles"], 1)

    def test_report_model_must_match_registered_model(self):
        report = self._load_report()
        report["model"] = behavioral._ref(self.artifacts["tbgen_tb"])
        with self.assertRaisesRegex(ValueError, "model path differs"):
            self._validate(report)

    def test_report_artifact_model_must_match_top_level_model(self):
        report = self._load_report()
        report["artifacts"]["model"] = behavioral._ref(self.artifacts["tbgen_tb"])
        with self.assertRaisesRegex(ValueError, "artifact model differs"):
            self._validate(report)

    def test_trace_statistics_cannot_be_relabelled(self):
        report = self._load_report()
        report["results"]["contract"]["cross_port_read_write_cycles"] = 0
        with self.assertRaisesRegex(ValueError, "trace statistics drifted"):
            self._validate(report)

    def test_execution_record_must_bind_assertions_and_testbench(self):
        report = self._load_report()
        report["execution"]["positive"]["compile"].remove("-DOPENRAM_ASSERTIONS")
        with self.assertRaisesRegex(ValueError, "bound to model/testbench/output/assertions"):
            self._validate(report)

    def test_report_tuple_must_match_admitted_candidate(self):
        wrong = deepcopy(self.candidate)
        wrong["parameters"]["variant"] = 2
        with self.assertRaisesRegex(ValueError, "parameter tuple differs"):
            self._validate(candidate=wrong)

    def test_public_replay_returns_behavior_only_and_null_electrical_metrics(self):
        admission = {
            "candidate": deepcopy(self.candidate),
            "artifacts": {"model": self.model, "report": self.report_path},
            "model": {"artifacts": {
                "model": behavioral._ref(self.model),
                "report": behavioral._ref(self.report_path),
            }},
        }
        result = behavioral.evaluate(admission, self.root / "replay")
        self.assertTrue(result["checks"]["functional"])
        self.assertTrue(result["estimated"])
        self.assertFalse(result["signoff"])
        self.assertTrue(all(item["value"] is None for item in result["measurements"]))
        self.assertIsNone(result["checks"]["lvs"])


if __name__ == "__main__":
    unittest.main()
