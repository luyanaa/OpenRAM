"""Hash-binding and structural tests for the register-file F1 adapter."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

SIM = Path(__file__).resolve().parent
REPO = SIM.parents[2]
for path in (SIM, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from compiler.architecture.contracts import REGISTER_FILE_ACCESS_CONTRACT, validate_candidate
import register_file as rf


def make_candidate():
    return validate_candidate({
        "schema": "openram-candidate/1",
        "capability_id": "fixture.register_file.2r1w",
        "family": "register_file",
        "backend": "replicated-1r1w",
        "access_contract": deepcopy(REGISTER_FILE_ACCESS_CONTRACT),
        "parameters": {"word_size": 8, "num_words": 4},
        "environment": {
            "corner": "tt",
            "voltage_v": 1.08,
            "temperature_c": 25,
            "period_ns": 10,
            "cload_ff": 5,
            "slew_ns": 0.03,
            "skew1": 0,
            "initialization": "uninitialized",
            "fidelity": "F1",
        },
    })


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RegisterFileAdapterEvidenceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.candidate = make_candidate()
        self.report_path = self.root / "register_file_f1.json"
        self.paths = {
            "model": self.root / "rf_1r1w_leaf.v",
            "cycle_model": self.root / "register_file.py",
            "wrapper": self.root / "rf_replicated_2r1w.v",
            "testbench": self.root / "tb.v",
            "executable": self.root / "sim.vvp",
            "compile_log": self.root / "compile.log",
            "run_log": self.root / "run.log",
        }
        for name, path in self.paths.items():
            path.write_text("fixture " + name, encoding="utf-8")
        self.report = {
            "schema": rf.REPORT_SCHEMA,
            "adapter": rf.ADAPTER,
            "candidate": deepcopy(self.candidate),
            "candidate_sha256": rf.identity(self.candidate),
            "artifacts": {name: rf._ref(path) for name, path in self.paths.items()},
            "tools": {},
            "execution": {},
            "results": {},
            "coverage": {},
        }
        self._write_report()

    def _write_report(self):
        self.report_path.write_text(json.dumps(self.report, sort_keys=True), encoding="utf-8")

    def test_wrapper_exposes_two_copies_broadcast_ownership_and_bypass(self):
        wrapper = rf._wrapper_source(word_size=8, num_words=4, address_width=2)
        leaf = rf._leaf_source(word_size=8, num_words=4, address_width=2)
        self.assertIn("output reg [DATA_WIDTH-1:0] r_data", leaf)
        self.assertIn("if (r_en) r_data <= words[r_addr];", leaf)
        self.assertEqual(wrapper.count("rf_1r1w_leaf #"), 2)
        self.assertEqual(wrapper.count(".w_en(w_en), .w_addr(w_addr), .w_data(w_data)"), 2)
        self.assertIn("copy0 (", wrapper)
        self.assertIn("copy1 (", wrapper)
        self.assertIn(".r_addr(r0_addr), .r_data(copy0_read_data)", wrapper)
        self.assertIn(".r_addr(r1_addr), .r_data(copy1_read_data)", wrapper)
        self.assertIn("r0_bypass_valid <= w_en && (w_addr == r0_addr);", wrapper)
        self.assertIn("r1_bypass_valid <= w_en && (w_addr == r1_addr);", wrapper)
        self.assertIn("assign r0_data = r0_bypass_valid ? r0_bypass_data : copy0_read_data;", wrapper)

    def test_generated_testbench_covers_valid_contract_without_fatal_or_invalid_read(self):
        testbench = rf._testbench_source(word_size=8, num_words=4, address_width=2)
        self.assertIn("step(1, 0, 1, 1, 1, 0, 0, 0, 0);", testbench)
        self.assertIn("step(1, 1, 1, 1, 1, 1, DATA_ONE, DATA_ONE, DATA_ONE);", testbench)
        self.assertIn("RF_F1_PASS", testbench)
        self.assertNotIn("$fatal", testbench)
        self.assertIn("Initialize valid read locations without observing uninitialized data", testbench)
    def test_generated_testbench_explicitly_checks_uninitialized_unknown(self):
        testbench = rf._testbench_source(word_size=8, num_words=4, address_width=2)
        self.assertIn("DATA_UNKNOWN = {DATA_WIDTH{1'bx}}", testbench)
        self.assertIn("step(1, 0, 0, 0, 0, 0, 0, DATA_UNKNOWN, 0);", testbench)

    def test_candidate_shape_rejects_backend_and_non_power_of_two_depth(self):
        wrong_backend = deepcopy(self.candidate)
        wrong_backend["backend"] = "native-pbitcell"
        with self.assertRaisesRegex(ValueError, "backend replicated-1r1w"):
            rf._candidate_shape(wrong_backend)
        non_power_two = deepcopy(self.candidate)
        non_power_two["parameters"]["num_words"] = 3
        with self.assertRaisesRegex(ValueError, "power of two"):
            rf._candidate_shape(non_power_two)


    def test_replay_rejects_model_wrapper_testbench_and_log_hash_drift(self):
        for name in ("model", "wrapper", "testbench", "compile_log", "run_log"):
            with self.subTest(artifact=name):
                original = self.paths[name].read_text(encoding="utf-8")
                self.paths[name].write_text(original + " changed", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "artifact hash drift"):
                    rf._validate_report(self.report_path, {}, self.candidate)
                self.paths[name].write_text(original, encoding="utf-8")

    def test_replay_rejects_cycle_model_artifact_hash_drift(self):
        path = self.paths["cycle_model"]
        original = path.read_text(encoding="utf-8")
        path.write_text(original + " changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "artifact hash drift"):
            rf._validate_report(self.report_path, {}, self.candidate)

    def test_replay_rejects_wrong_candidate_contract(self):
        wrong = deepcopy(self.candidate)
        wrong["access_contract"]["read_latency_cycles"] = 0
        with self.assertRaisesRegex(ValueError, "register-file access contract differs"):
            rf._validate_report(self.report_path, {}, wrong)

    def test_replay_rejects_report_with_wrong_candidate_contract(self):
        self.report["candidate"]["access_contract"]["read_latency_cycles"] = 0
        self._write_report()
        with self.assertRaisesRegex(ValueError, "candidate contract or tuple differs"):
            rf._validate_report(self.report_path, {}, self.candidate)

    def test_replay_rejects_unknown_report_schema(self):
        self.report["schema"] = "openram-register-file-f1/unsupported"
        self._write_report()
        with self.assertRaisesRegex(ValueError, "unsupported register-file F1 report schema"):
            rf._validate_report(self.report_path, {}, self.candidate)

    def test_replay_rejects_report_hash_drift(self):
        original_hash = digest(self.report_path)
        self.report_path.write_text('{"tampered": true}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "report hash drift"):
            rf._validate_report(self.report_path, {"report_sha256": original_hash}, self.candidate)

    def test_replay_rejects_tool_hash_drift(self):
        source = {
            "model": rf._leaf_source(8, 4, 2),
            "cycle_model": (rf.ROOT / "compiler/architecture/register_file.py").read_text(encoding="utf-8"),
            "wrapper": rf._wrapper_source(8, 4, 2),
            "testbench": rf._testbench_source(8, 4, 2),
        }
        for name, content in source.items():
            self.paths[name].write_text(content, encoding="utf-8")
        for name in ("executable", "compile_log", "run_log"):
            self.paths[name].write_bytes(b"fixture " + name.encode("ascii"))
        tool = self.root / "simulator-tool"
        tool.write_text("not an Icarus binary", encoding="utf-8")
        self.report["artifacts"] = {name: rf._ref(path) for name, path in self.paths.items()}
        self.report["tools"] = {
            name: {"path": str(tool), "sha256": "0" * 64, "version": "fixture", "mode": "execution-enabled"}
            for name in ("iverilog", "vvp")
        }
        self._write_report()
        with self.assertRaisesRegex(ValueError, "tool hash drift"):
            rf._validate_report(self.report_path, {}, self.candidate)

    def test_fingerprint_hashes_report_artifacts_and_tool_identity(self):
        tool = self.root / "simulator-tool"
        tool.write_bytes(b"tool identity fixture")
        self.report["tools"] = {
            "iverilog": {"path": str(tool), "sha256": digest(tool), "version": "recorded", "mode": "execution-enabled"},
            "vvp": {"path": str(tool), "sha256": digest(tool), "version": "recorded", "mode": "execution-enabled"},
        }
        self._write_report()
        fingerprint = rf.fingerprint({"artifacts": {"report": self.report_path}})
        dependencies = fingerprint["dependencies"]
        for path in (*self.paths.values(), self.report_path, tool):
            self.assertIn(str(path.resolve()), dependencies)
        self.assertTrue(any(Path(ref["path"]).name == "register_file.py"
                            for ref in fingerprint["sources"]))

    def test_evaluator_does_not_accept_tampered_registered_report(self):
        registered_report_hash = digest(self.report_path)
        admission = {
            "candidate": self.candidate,
            "artifacts": {"report": self.report_path},
            "model": {"artifacts": {"report": {
                "path": str(self.report_path), "sha256": registered_report_hash,
            }}},
        }
        self.report_path.write_text("tampered report", encoding="utf-8")
        result = rf.evaluate(admission, self.root / "replay")
        self.assertIsNone(result["checks"]["functional"])
        self.assertFalse(result["signoff"])
        self.assertIn("changed: report", result["details"]["error"])

    @unittest.skipUnless(shutil.which("iverilog") and shutil.which("vvp"),
                         "Icarus execution is optional for pure adapter validation")
    def test_real_icarus_execution_generates_replayable_f1_report(self):
        report_path = rf.generate(self.candidate, self.root / "execution")
        report, _ = rf._validate_report(report_path, {}, self.candidate)
        self.assertTrue(report["results"]["functional"])
        self.assertEqual(report["coverage"], rf.COVERAGE)
        admission = {
            "candidate": self.candidate,
            "artifacts": {"report": report_path},
            "model": {"artifacts": {"report": rf._ref(report_path)}},
        }
        result = rf.evaluate(admission, self.root / "replay")
        self.assertTrue(result["checks"]["functional"])
        self.assertTrue(all(value is None for name, value in result["checks"].items()
                            if name != "functional"))
        self.assertTrue(result["estimated"])
        self.assertFalse(result["signoff"])
        self.assertTrue(all(item["value"] is None for item in result["measurements"]))


if __name__ == "__main__":
    unittest.main()
