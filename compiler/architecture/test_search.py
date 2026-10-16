"""Behavioral engine tests using explicitly synthetic, hash-bound unit evidence.

These numeric records test search policy; they are not device characterization
and are never registered as technology observations.
"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from .contracts import ContractError, Registry, file_ref, load_json
from .search import enumerate_candidates, run_search


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proof = self.root / "synthetic-basic.json"
        self.proof.write_text(json.dumps({"synthetic_unit_evidence": True, "validated": True}))
        self.parameters = {"word_size": 8, "num_words": 32, "mux_ratio": 1}
        self.environment = dict(corner="ss", voltage_v=1.08, temperature_c=-40,
                                period_ns=2.5, cload_ff=5, slew_ns=0.03, skew1=0,
                                initialization="uninitialized", fidelity="F2")
        self.margins = dict(wen_pulse_ns=0.1, sa_setup_ns=0.02,
                            bl_diff_v=0.05, write_recovery_ns=0.05)
        model = {"adapter": "synthetic-unit-only", "artifacts": {"proof": file_ref(self.proof)},
                 "basic_checks": [{"artifact": "proof", "pointer": "/validated", "equals": True}]}
        self.data = {"schema": "openram-capabilities/1", "technology": "synthetic-unit-only",
                     "pdk": {"revision": "unit-test", "evidence": file_ref(self.proof)},
                     "capabilities": [{"id": "synthetic.2rw", "family": "sram", "backend": "unit-only",
                         "access_contract": dict(ports=["RW", "RW"], clocking="independent",
                                                 same_row_write="forbidden", read_during_write="undefined"),
                         "parameters": {"word_size": {"values": [8, 16]},
                                        "num_words": {"values": [32, 64]}, "mux_ratio": {"values": [1, 4]}},
                         "environment": {key: ({"minimum": -100, "maximum": 200} if key == "temperature_c"
                                                else {"values": [value]})
                                         for key, value in self.environment.items()},
                         "excludes": [], "implementations": [{"parameters": deepcopy(self.parameters),
                             "models": {fidelity: deepcopy(model) for fidelity in ("F2", "F3", "F4")}}]}]}
        env_rules = self.data["capabilities"][0]["environment"]
        env_rules["period_ns"] = {"minimum": 1, "maximum": 20}
        env_rules["corner"] = {"values": ["ss", "tt"]}
        env_rules["initialization"] = {"values": ["uninitialized", "zero-voltage"]}
        env_rules["fidelity"] = {"values": ["F0", "F1", "F2", "F3", "F4"]}
        self.registry_path = self.root / "registry.json"
        self.save_registry()
        self.request = dict(schema="openram-search/1", capability_id="synthetic.2rw",
                            parameters=deepcopy(self.parameters), environment=deepcopy(self.environment),
                            search={}, objectives=["cycle_time_ns"], required_margins=deepcopy(self.margins))
        self.fingerprint = {"adapter_source": "synthetic-unit-evaluator/1", "tool": "none"}
        self.calls = []

    def save_registry(self):
        self.registry_path.write_text(json.dumps(self.data))
        self.registry = Registry(self.registry_path)

    def evaluation(self, admission, directory):
        candidate = admission["candidate"]
        self.calls.append(deepcopy(candidate))
        proof = directory / "synthetic-observation.json"
        proof.write_text(json.dumps({"synthetic": True, "environment": candidate["environment"]}))
        evidence = file_ref(proof)
        records = []
        specifications = {
            "cycle_time_ns": (candidate["environment"]["period_ns"], "ns", "macro"),
            "area_um2": (100, "um^2", "macro"),
            "read_energy_pj": (2, "pJ", "read"),
            "write_energy_pj": (3, "pJ", "write"),
            "wen_pulse_ns": (0.1, "ns", "write"),
            "sa_setup_ns": (0.02, "ns", "read"),
            "bl_diff_v": (0.05, "V", "read"),
            "write_recovery_ns": (0.05, "ns", "write"),
        }
        for name, (value, unit, operation) in specifications.items():
            records.append(dict(name=name, value=value, unit=unit, port=None, operation=operation,
                                domain=deepcopy(candidate["environment"]),
                                fidelity=candidate["environment"]["fidelity"], evidence=[deepcopy(evidence)]))
        return dict(measurements=records, checks=dict(functional=True, topology=True, drc=True,
                    lvs=True, pex_accepted=True), artifacts={"synthetic_observation": evidence},
                    estimated=False, signoff=False, details={"scope": "synthetic unit test only"})

    def run_search(self, evaluator=None, request=None, **kwargs):
        fingerprint = kwargs.pop("evaluator_fingerprint", self.fingerprint)
        return run_search(self.registry, request or self.request, self.root / "output",
                          evaluator or self.evaluation, evaluator_fingerprint=fingerprint, **kwargs)

    @staticmethod
    def metric(evaluation, name):
        return next(record for record in evaluation["measurements"] if record["name"] == name)

    def test_deterministic_enumeration_never_evaluates_unsupported_combinations(self):
        self.request["search"] = {"parameters.num_words": [64, 32], "parameters.mux_ratio": [4, 1]}
        first = enumerate_candidates(self.registry, self.request)
        reordered = deepcopy(self.request)
        reordered["search"] = {"parameters.mux_ratio": [1, 4], "parameters.num_words": [32, 64]}
        self.assertEqual(first, enumerate_candidates(self.registry, reordered))
        self.assertEqual(len(first), 4)
        report = self.run_search()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["parameters"], self.parameters)
        rejected = [record for record in report["candidates"] if record["status"] == "rejected"]
        self.assertEqual(len(rejected), 3)
        for record in rejected:
            self.assertIn("no basic-validated implementation", record["reason"])
            self.assertEqual(record["first_failed_constraint"], "admission")
            self.assertTrue((self.root / "output" / record["id"] / "result.json").exists())

    def test_unknown_axes_and_fields_reject_before_any_evaluation(self):
        for change in (lambda req: req["search"].update({"parameters.mux_ratios": [1]}),
                       lambda req: req["search"].update({"environment.voltage": [1.08]}),
                       lambda req: req["parameters"].update({"made_up": 1}),
                       lambda req: req["environment"].update({"made_up": 1}),
                       lambda req: req.update({"made_up": True})):
            request = deepcopy(self.request)
            change(request)
            with self.subTest(request=request), self.assertRaises(ContractError):
                self.run_search(request=request)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.root / "output").exists())

    def test_invalid_axis_values_remain_visible_as_rejections(self):
        self.request["search"] = {"environment.period_ns": [0, 2.5]}
        report = self.run_search()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(report["candidates"]), 2)
        invalid = next(record for record in report["candidates"] if record["status"] == "rejected")
        self.assertEqual(invalid["candidate"]["environment"]["period_ns"], 0)
        self.assertIn("period_ns", invalid["reason"])

    def test_search_axes_override_base_values_before_candidate_validation(self):
        self.request["environment"]["period_ns"] = 0
        self.request["search"] = {"environment.period_ns": [2.5, 5]}
        report = self.run_search()
        self.assertEqual({row["candidate"]["environment"]["period_ns"]
                          for row in report["candidates"] if row["status"] == "evaluated"}, {2.5, 5})

    def test_invalid_nonaxis_value_remains_a_visible_rejected_tuple(self):
        self.request["environment"]["voltage_v"] = 0
        report = self.run_search()
        self.assertEqual(report["candidates"][0]["status"], "rejected")
        self.assertIn("voltage_v", report["candidates"][0]["reason"])
        self.assertEqual(self.calls, [])

    def test_rejections_and_explicit_evaluation_limit_are_distinct(self):
        self.request["search"] = {"parameters.mux_ratio": [1, 4]}
        report = self.run_search(max_evaluations=0)
        self.assertEqual(self.calls, [])
        states = {record["status"] for record in report["candidates"]}
        self.assertEqual(states, {"limited", "rejected"})
        limited = next(record for record in report["candidates"] if record["status"] == "limited")
        self.assertEqual(limited["screening_feasibility"], "unknown")
        self.assertIsNone(limited["first_failed_constraint"])
        self.assertEqual(limited["first_unknown_constraint"], "limited")

    def test_dominated_failure_is_preserved_and_failure_cannot_dominate(self):
        self.request["search"] = {"environment.period_ns": [2, 3, 4]}

        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            period = admission["candidate"]["environment"]["period_ns"]
            if period in (2, 4):
                evaluation["checks"]["functional"] = False
            return evaluation

        report = self.run_search(evaluate)
        records = {record["candidate"]["environment"]["period_ns"]: record for record in report["candidates"]}
        self.assertEqual(report["screening_frontier"], [records[3]["id"]])
        for period in (2, 4):
            self.assertEqual(records[period]["screening_feasibility"], "fail")
            self.assertEqual(records[period]["first_failed_constraint"], "functional")
            self.assertEqual(records[period]["status"], "evaluated")
        self.assertEqual(report["physical_frontier"], [])

    def test_null_and_absent_objectives_never_become_zero_or_enter_frontier(self):
        self.request["objectives"] = ["area_um2", "read_energy_pj", "write_energy_pj", "cycle_time_ns"]

        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            self.metric(evaluation, "read_energy_pj")["value"] = None
            evaluation["measurements"] = [record for record in evaluation["measurements"]
                                          if record["name"] != "write_energy_pj"]
            return evaluation

        report = self.run_search(evaluate)
        record = report["candidates"][0]
        self.assertEqual(record["screening_feasibility"], "pass")
        self.assertIsNone(record["objective_values"]["read_energy_pj"])
        self.assertIsNone(record["objective_values"]["write_energy_pj"])
        self.assertFalse(record["objectives_complete"])
        self.assertEqual(report["screening_frontier"], [])
        self.assertEqual(report["physical_frontier"], [])
        self.assertEqual(report["objective_scope"], "full-ppa")
        missing = {item["name"] for item in record["objective_constraints"] if item["status"] == "unknown"}
        self.assertEqual(missing, {"objective:read_energy_pj", "objective:write_energy_pj"})

    def test_per_port_zero_is_not_copied_to_other_ports_or_macro_aggregate(self):
        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            for measurement in evaluation["measurements"]:
                measurement["port"] = 0
            return evaluation

        report = self.run_search(evaluate)
        record = report["candidates"][0]
        self.assertEqual(record["screening_feasibility"], "unknown")
        self.assertIsNone(record["objective_values"]["cycle_time_ns"])
        self.assertEqual(report["screening_frontier"], [])
        self.assertTrue(all(measurement["port"] == 0 for measurement in record["evaluation"]["measurements"]))
        self.assertFalse(record["promotion"]["eligible"])

    def test_comparison_domains_isolate_pvt_fidelity_and_workload(self):
        capability = self.data["capabilities"][0]
        template = deepcopy(capability["implementations"][0])
        for word_size, num_words in ((8, 64), (16, 32), (16, 64)):
            implementation = deepcopy(template)
            implementation["parameters"].update(word_size=word_size, num_words=num_words)
            capability["implementations"].append(implementation)
        self.save_registry()
        self.request["search"] = {"environment.corner": ["ss", "tt"], "environment.fidelity": ["F2", "F3"],
                                  "parameters.word_size": [8, 16], "parameters.num_words": [32, 64],
                                  "environment.period_ns": [2, 3]}
        report = self.run_search()
        self.assertEqual(len(report["candidates"]), 32)
        self.assertEqual(len(report["screening_frontier"]), 16)
        by_id = {record["id"]: record for record in report["candidates"]}
        domains = {by_id[identifier]["comparison_domain"] for identifier in report["screening_frontier"]}
        self.assertEqual(len(domains), 16)
        self.assertTrue(all(by_id[identifier]["objective_values"]["cycle_time_ns"] == 2
                            for identifier in report["screening_frontier"]))

    def test_initialization_domains_and_ties_are_not_dominated(self):
        self.request["search"] = {"environment.period_ns": [2, 3],
                                  "environment.initialization": ["uninitialized", "zero-voltage"]}

        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            self.metric(evaluation, "cycle_time_ns")["value"] = 2
            return evaluation

        report = self.run_search(evaluate)
        self.assertEqual(len(report["screening_frontier"]), 4)
        self.assertEqual(len({record["comparison_domain"] for record in report["candidates"]}), 2)

    def test_margin_boundaries_are_inclusive_and_negative_margins_fail(self):
        self.request["search"] = {"environment.period_ns": [2, 3, 4]}

        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            period = admission["candidate"]["environment"]["period_ns"]
            if period == 3:
                self.metric(evaluation, "bl_diff_v")["value"] = 0.049999999
            elif period == 4:
                self.metric(evaluation, "sa_setup_ns")["value"] = -0.01
            return evaluation

        report = self.run_search(evaluate)
        records = {record["candidate"]["environment"]["period_ns"]: record for record in report["candidates"]}
        self.assertEqual(records[2]["screening_feasibility"], "pass")
        self.assertEqual(records[3]["first_failed_constraint"], "bl_diff_v")
        self.assertEqual(records[4]["first_failed_constraint"], "sa_setup_ns")
        self.assertEqual(report["screening_frontier"], [records[2]["id"]])

    def test_stale_output_hash_cannot_reuse_cached_pass(self):
        first = self.run_search()
        record = first["candidates"][0]
        source = Path(record["evaluation"]["measurements"][0]["evidence"][0]["path"])
        source.write_text("changed waveform stand-in")
        limited = self.run_search(max_evaluations=0)
        stale = limited["candidates"][0]
        self.assertEqual(stale["status"], "limited")
        self.assertFalse(stale["cache"]["hit"])
        self.assertIn("hash drift", stale["cache"]["rejection_reason"])
        self.assertEqual(limited["screening_frontier"], [])
        fresh = self.run_search()
        self.assertEqual(len(self.calls), 2)
        refreshed = fresh["candidates"][0]
        self.assertEqual(refreshed["screening_feasibility"], "pass")
        self.assertNotEqual(record["cache"]["cache_key"], refreshed["cache"]["cache_key"])
        self.assertTrue(source.exists())
        self.assertNotEqual(str(source), refreshed["evaluation"]["measurements"][0]["evidence"][0]["path"])

    def test_artifact_not_used_by_metric_is_also_revalidated(self):
        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            extra = directory / "extraction.json"
            extra.write_text("accepted extraction evidence")
            evaluation["artifacts"]["extraction"] = file_ref(extra)
            return evaluation

        first = self.run_search(evaluate)
        extra = first["candidates"][0]["evaluation"]["artifacts"]["extraction"]
        Path(extra["path"]).write_text("changed extraction")
        second = self.run_search(evaluate)
        self.assertEqual(len(self.calls), 2)
        self.assertFalse(second["candidates"][0]["cache"]["hit"])
        self.assertIn("hash drift", second["candidates"][0]["cache"]["rejection_reason"])

    def test_same_directory_cache_reuses_evidence_without_cleaning_unknown_files(self):
        first = self.run_search()
        identifier = first["candidates"][0]["id"]
        unknown = self.root / "output" / identifier / "user-notes.txt"
        unknown.write_text("must survive")
        second = self.run_search(max_evaluations=0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(second["evaluations_performed"], 0)
        self.assertTrue(second["candidates"][0]["cache"]["hit"])
        self.assertEqual(first["screening_frontier"], second["screening_frontier"])
        self.assertEqual(unknown.read_text(), "must survive")
        persisted = load_json(self.root / "output" / "report.json")
        self.assertEqual(persisted, second)
        self.assertEqual(load_json(self.root / "output" / identifier / "result.json"), second["candidates"][0])
        self.assertEqual(load_json(self.root / "output" / identifier / "candidate.json"), second["candidates"][0]["candidate"])

    def test_limit_counts_only_actual_callbacks_and_can_resume(self):
        self.request["search"] = {"environment.period_ns": [2, 3, 4]}
        first = self.run_search(max_evaluations=1)
        self.assertEqual(first["evaluations_performed"], 1)
        self.assertEqual(sum(record["status"] == "limited" for record in first["candidates"]), 2)
        second = self.run_search(max_evaluations=1)
        self.assertEqual(second["evaluations_performed"], 1)
        self.assertEqual(sum(record["status"] == "limited" for record in second["candidates"]), 1)
        self.assertEqual(sum(record["cache"]["hit"] for record in second["candidates"]), 1)
        self.assertEqual(len(self.calls), 2)

    def test_cache_keys_bind_initialization_period_fingerprint_margins_and_objectives(self):
        first = self.run_search()
        input_keys = [first["candidates"][0]["cache"]["input_key"]]
        requests = []
        for key, value in (("initialization", "zero-voltage"), ("period_ns", 3)):
            request = deepcopy(self.request)
            request["environment"][key] = value
            requests.append(request)
        margin_request = deepcopy(self.request)
        margin_request["required_margins"]["sa_setup_ns"] = 0.03
        requests.append(margin_request)
        objective_request = deepcopy(self.request)
        objective_request["objectives"] = ["area_um2", "cycle_time_ns"]
        requests.append(objective_request)
        for request in requests:
            report = self.run_search(request=request)
            input_keys.append(report["candidates"][0]["cache"]["input_key"])
        changed_adapter = self.run_search(evaluator_fingerprint={"adapter_source": "synthetic-unit-evaluator/2"})
        input_keys.append(changed_adapter["candidates"][0]["cache"]["input_key"])
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len(set(input_keys)), 6)
        baseline = self.run_search()
        self.assertTrue(baseline["candidates"][0]["cache"]["hit"])
        self.assertEqual(len(self.calls), 6)

    def test_registry_model_identity_changes_invalidate_reuse(self):
        first = self.run_search()
        self.data["capabilities"][0]["implementations"][0]["models"]["F2"]["adapter"] = "synthetic-unit-only/2"
        self.save_registry()
        second = self.run_search()
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(first["candidates"][0]["cache"]["input_key"],
                            second["candidates"][0]["cache"]["input_key"])

    def test_corrupt_cached_value_is_not_trusted_even_with_valid_evidence_files(self):
        first = self.run_search()
        record = first["candidates"][0]
        cache_path = self.root / "output" / record["id"] / "cache" / (record["cache"]["input_key"] + ".json")
        cache = load_json(cache_path)
        self.metric(cache["evaluation"], "cycle_time_ns")["value"] = 0
        cache_path.write_text(json.dumps(cache))
        second = self.run_search()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(second["candidates"][0]["objective_values"]["cycle_time_ns"], 2.5)
        self.assertIn("cache output identity differs", second["candidates"][0]["cache"]["rejection_reason"])

    def test_evaluation_domain_units_and_check_types_fail_closed(self):
        mutations = [
            lambda evaluation: self.metric(evaluation, "cycle_time_ns")["domain"].update(period_ns=3),
            lambda evaluation: self.metric(evaluation, "cycle_time_ns").update(unit="ps"),
            lambda evaluation: self.metric(evaluation, "cycle_time_ns").update(fidelity="F3"),
            lambda evaluation: self.metric(evaluation, "cycle_time_ns").update(operation="read"),
            lambda evaluation: self.metric(evaluation, "cycle_time_ns").update(port=2),
            lambda evaluation: self.metric(evaluation, "cycle_time_ns").update(evidence=[]),
            lambda evaluation: evaluation["checks"].update(functional="PASS"),
            lambda evaluation: evaluation.update(signoff=True),
            lambda evaluation: evaluation.update(estimated="false"),
        ]
        for index, mutate in enumerate(mutations):
            def evaluate(admission, directory):
                evaluation = self.evaluation(admission, directory)
                mutate(evaluation)
                return evaluation

            with self.subTest(index=index):
                report = self.run_search(evaluate, evaluator_fingerprint={"mutation": index})
                record = report["candidates"][0]
                self.assertEqual(record["status"], "evaluation_error")
                self.assertIn("ContractError", record["reason"])
                self.assertEqual(record["screening_feasibility"], "unknown")
                self.assertFalse(record["promotion"]["eligible"])
                self.assertEqual(report["screening_frontier"], [])

    def test_evaluator_failure_is_unknown_not_functional_failure(self):
        def evaluate(admission, directory):
            raise TimeoutError("simulator timed out")

        report = self.run_search(evaluate)
        record = report["candidates"][0]
        self.assertEqual(record["status"], "evaluation_error")
        self.assertEqual(record["screening_feasibility"], "unknown")
        self.assertIsNone(record["first_failed_constraint"])
        self.assertIn("TimeoutError: simulator timed out", record["reason"])
        self.assertEqual(report["evaluations_performed"], 1)

    def test_passed_screening_promotes_only_to_admitted_next_model(self):
        passed = self.run_search()
        promotion = passed["candidates"][0]["promotion"]
        self.assertTrue(promotion["eligible"])
        self.assertEqual(promotion["next_fidelity"], "F3")
        del self.data["capabilities"][0]["implementations"][0]["models"]["F3"]
        self.save_registry()
        unavailable = self.run_search()
        promotion = unavailable["candidates"][0]["promotion"]
        self.assertFalse(promotion["eligible"])
        self.assertIn("next fidelity is not admitted", promotion["reason"])

    def test_failed_or_unknown_previous_gates_block_promotion(self):
        for value in (False, None):
            def evaluate(admission, directory):
                evaluation = self.evaluation(admission, directory)
                evaluation["checks"]["functional"] = value
                return evaluation

            report = self.run_search(evaluate, evaluator_fingerprint={"functional": value})
            record = report["candidates"][0]
            self.assertFalse(record["promotion"]["eligible"])
            self.assertEqual(record["screening_feasibility"], "fail" if value is False else "unknown")
            self.assertEqual(report["screening_frontier"], [])

    def test_f4_promotion_requires_accepted_nonestimated_extraction(self):
        self.request["environment"]["fidelity"] = "F3"
        for estimated, accepted, eligible in ((False, False, False), (False, None, False),
                                               (True, True, False), (False, True, True)):
            def evaluate(admission, directory):
                evaluation = self.evaluation(admission, directory)
                evaluation["estimated"] = estimated
                evaluation["checks"]["pex_accepted"] = accepted
                return evaluation

            with self.subTest(estimated=estimated, accepted=accepted):
                report = self.run_search(evaluate, evaluator_fingerprint={"estimated": estimated, "accepted": accepted})
                record = report["candidates"][0]
                self.assertEqual(record["screening_feasibility"], "pass")
                self.assertEqual(record["promotion"]["eligible"], eligible)
                self.assertEqual(record["physical_feasibility"], "fail" if accepted is False or estimated else "unknown")

    def test_f0_and_f1_never_establish_electrical_proof(self):
        models = self.data["capabilities"][0]["implementations"][0]["models"]
        models["F0"] = deepcopy(models["F2"])
        models["F1"] = deepcopy(models["F2"])
        self.save_registry()
        self.request["search"] = {"environment.fidelity": ["F0", "F1"]}
        self.request["objectives"] = ["area_um2"]
        report = self.run_search()
        self.assertEqual(len(self.calls), 2)
        for record in report["candidates"]:
            self.assertEqual(record["screening_feasibility"], "unknown")
            self.assertEqual(record["stage_feasibility"], "pass")
            self.assertTrue(record["promotion"]["eligible"])
            self.assertEqual(record["status"], "evaluated")
            self.assertEqual(report["fidelity_frontiers"][record["candidate"]["environment"]["fidelity"]],
                             [record["id"]])
        self.assertEqual(report["screening_frontier"], [])
        self.assertEqual(report["physical_frontier"], [])

    def test_low_fidelity_estimates_do_not_require_accepted_pex(self):
        models = self.data["capabilities"][0]["implementations"][0]["models"]
        models["F0"] = deepcopy(models["F2"])
        models["F1"] = deepcopy(models["F2"])
        self.save_registry()
        self.request["objectives"] = ["area_um2"]

        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            evaluation["estimated"] = True
            evaluation["checks"]["pex_accepted"] = False
            return evaluation

        for fidelity in ("F0", "F1"):
            self.request["environment"]["fidelity"] = fidelity
            record = self.run_search(evaluate)["candidates"][0]
            with self.subTest(fidelity=fidelity):
                self.assertEqual(record["stage_feasibility"], "pass")
                self.assertTrue(record["promotion"]["eligible"])
                self.assertEqual(record["physical_feasibility"], "fail")

    def test_known_f0_margin_failure_blocks_ranking_and_promotion(self):
        models = self.data["capabilities"][0]["implementations"][0]["models"]
        models["F0"] = deepcopy(models["F2"])
        models["F1"] = deepcopy(models["F2"])
        self.save_registry()
        self.request["environment"]["fidelity"] = "F0"
        self.request["objectives"] = ["area_um2"]

        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            self.metric(evaluation, "sa_setup_ns")["value"] = -0.01
            return evaluation

        report = self.run_search(evaluate)
        record = report["candidates"][0]
        self.assertEqual(record["stage_feasibility"], "fail")
        self.assertFalse(record["promotion"]["eligible"])
        self.assertEqual(report["fidelity_frontiers"]["F0"], [])
        self.assertEqual(report["screening_frontier"], [])

    def test_multifidelity_execution_promotes_only_surviving_frontier(self):
        models = self.data["capabilities"][0]["implementations"][0]["models"]
        models["F0"] = deepcopy(models["F2"])
        models["F1"] = deepcopy(models["F2"])
        self.save_registry()
        self.request["search"] = {"environment.fidelity": ["F0", "F1", "F2"],
                                  "environment.period_ns": [2, 3, 4]}

        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            env = admission["candidate"]["environment"]
            if env["fidelity"] == "F0" and env["period_ns"] == 2:
                self.metric(evaluation, "sa_setup_ns")["value"] = -0.01
            return evaluation

        report = self.run_search(evaluate)
        electrical_calls = [(row["environment"]["fidelity"], row["environment"]["period_ns"])
                            for row in self.calls if row["environment"]["fidelity"] != "F0"]
        self.assertEqual(electrical_calls, [("F1", 3), ("F2", 3)])
        self.assertEqual(len(report["candidates"]), 9)
        self.assertEqual(sum(row["status"] == "not_promoted" for row in report["candidates"]), 4)

    def test_failed_predecessor_blocks_cached_higher_fidelity_reuse(self):
        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            evaluation["checks"]["functional"] = admission["candidate"]["environment"]["fidelity"] != "F2"
            return evaluation

        direct = deepcopy(self.request)
        direct["environment"]["fidelity"] = "F3"
        self.assertEqual(self.run_search(evaluate, request=direct)["candidates"][0]["screening_feasibility"], "pass")
        self.calls.clear()
        self.request["search"] = {"environment.fidelity": ["F2", "F3"]}
        report = self.run_search(evaluate)
        higher = next(row for row in report["candidates"] if row["candidate"]["environment"]["fidelity"] == "F3")
        self.assertEqual(higher["status"], "not_promoted")
        self.assertEqual([row["environment"]["fidelity"] for row in self.calls], ["F2"])
        self.assertEqual(report["screening_frontier"], [])

    def test_multifidelity_request_does_not_skip_missing_intermediate_stage(self):
        models = self.data["capabilities"][0]["implementations"][0]["models"]
        models["F0"] = deepcopy(models["F2"])
        models["F1"] = deepcopy(models["F2"])
        self.save_registry()
        self.request["search"] = {"environment.fidelity": ["F0", "F2"]}
        report = self.run_search()
        higher = next(row for row in report["candidates"] if row["candidate"]["environment"]["fidelity"] == "F2")
        self.assertEqual(higher["status"], "not_promoted")
        self.assertIn("F1", higher["reason"])
        self.assertEqual([row["environment"]["fidelity"] for row in self.calls], ["F0"])

    def test_low_fidelity_ranking_and_promotion_require_actual_stage_evidence(self):
        models = self.data["capabilities"][0]["implementations"][0]["models"]
        models["F0"] = deepcopy(models["F2"])
        models["F1"] = deepcopy(models["F2"])
        self.save_registry()
        self.request["objectives"] = ["area_um2"]
        for fidelity, functional, missing_objective, expected in (
                ("F0", None, True, "unknown"), ("F0", False, False, "fail"),
                ("F1", None, False, "unknown"), ("F1", False, False, "fail"),
                ("F1", True, True, "pass")):
            def evaluate(admission, directory):
                evaluation = self.evaluation(admission, directory)
                evaluation["checks"]["functional"] = functional
                if missing_objective:
                    self.metric(evaluation, "area_um2")["value"] = None
                return evaluation

            request = deepcopy(self.request)
            request["environment"]["fidelity"] = fidelity
            report = self.run_search(evaluate, request=request,
                                     evaluator_fingerprint={"fidelity": fidelity, "functional": functional,
                                                            "missing": missing_objective})
            record = report["candidates"][0]
            self.assertEqual(record["stage_feasibility"], expected)
            self.assertEqual(record["promotion"]["eligible"], expected == "pass")
            self.assertEqual(report["screening_frontier"], [])
            self.assertEqual(report["physical_frontier"], [])
            self.assertEqual(report["fidelity_frontiers"][fidelity], [])

    def test_physical_frontier_requires_all_final_gates_and_objectives(self):
        self.request["environment"]["fidelity"] = "F4"
        self.request["objectives"] = ["area_um2", "read_energy_pj", "write_energy_pj", "cycle_time_ns"]
        cases = [(None, None, None), ("drc", False, None), ("lvs", None, None),
                 ("pex_accepted", False, None), ("topology", False, None),
                 (None, None, "estimated"), (None, None, "missing-energy")]
        for index, (check, value, change) in enumerate(cases):
            def evaluate(admission, directory):
                evaluation = self.evaluation(admission, directory)
                if check:
                    evaluation["checks"][check] = value
                if change == "estimated":
                    evaluation["estimated"] = True
                elif change == "missing-energy":
                    self.metric(evaluation, "read_energy_pj")["value"] = None
                return evaluation

            report = self.run_search(evaluate, evaluator_fingerprint={"physical_case": index})
            record = report["candidates"][0]
            if index == 0:
                self.assertEqual(record["physical_feasibility"], "pass")
                self.assertEqual(report["physical_frontier"], [record["id"]])
            else:
                self.assertEqual(report["physical_frontier"], [])
            self.assertFalse(record["signoff"])
            self.assertFalse(record["promotion"]["eligible"])
            self.assertIsNone(record["promotion"]["next_fidelity"])


    def test_known_checks_without_any_bound_evidence_are_not_proof(self):
        def evaluate(admission, directory):
            evaluation = self.evaluation(admission, directory)
            evaluation["artifacts"] = {}
            evaluation["measurements"] = []
            return evaluation

        report = self.run_search(evaluate)
        record = report["candidates"][0]
        self.assertEqual(record["status"], "evaluation_error")
        self.assertIn("known checks require hash-bound", record["reason"])
        self.assertEqual(record["stage_feasibility"], "unknown")
        self.assertEqual(report["screening_frontier"], [])


if __name__ == "__main__":
    unittest.main()
