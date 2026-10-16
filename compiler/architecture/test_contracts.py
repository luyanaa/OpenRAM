"""Admission must not infer support from legal individual parameter values."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from .contracts import (ContractError, Registry, candidate_id, comparison_domain,
                        file_ref, load_json, validate_candidate, validate_measurement, validate_request)


class AdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.proof = self.root / "basic.json"
        self.proof.write_text(json.dumps({"completed": True, "equivalent": True}))
        self.parameters = {"word_size": 8, "num_words": 32, "mux_ratio": 1}
        self.environment = dict(corner="ss", voltage_v=1.08, temperature_c=-40,
                                period_ns=2.5, cload_ff=5, slew_ns=0.03, skew1=0,
                                initialization="uninitialized", fidelity="F2")
        self.contract = dict(ports=["RW", "RW"], clocking="independent",
                             same_row_write="forbidden", read_during_write="undefined-same-row")
        self.data = {"schema": "openram-capabilities/1", "technology": "fixture-pdk",
                     "pdk": {"revision": "fixture", "evidence": file_ref(self.proof)},
                     "capabilities": [{"id": "fixture.2rw", "family": "sram", "backend": "fixture",
                         "access_contract": self.contract,
                         "parameters": {"word_size": {"values": [8]}, "num_words": {"values": [32, 64]},
                                        "mux_ratio": {"values": [1, 4]}},
                         "environment": {key: ({"minimum": -100, "maximum": 200} if key == "temperature_c"
                                                else {"values": [value]})
                                         for key, value in self.environment.items()},
                         "excludes": [], "implementations": [{"parameters": self.parameters,
                             "models": {"F2": {"adapter": "fixture", "artifacts": {"proof": file_ref(self.proof)},
                                 "basic_checks": [{"artifact": "proof", "pointer": "/equivalent", "equals": True}]}}}]}]}
        self.path = self.root / "registry.json"
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data))

    def candidate(self):
        return Registry(self.path).candidate("fixture.2rw", self.parameters, self.environment)

    def test_identity_and_domain_are_semantic(self):
        original = self.candidate()
        reordered = dict(reversed(list(original.items())))
        self.assertEqual(candidate_id(original), candidate_id(reordered))
        numeric = deepcopy(original)
        numeric["environment"]["cload_ff"] = 5
        self.assertEqual(candidate_id(original), candidate_id(numeric))
        self.assertEqual(comparison_domain(original), comparison_domain(numeric))
        slower = deepcopy(original)
        slower["environment"]["period_ns"] = 10
        self.assertNotEqual(candidate_id(original), candidate_id(slower))
        self.assertEqual(comparison_domain(original), comparison_domain(slower))
        colder = deepcopy(original)
        colder["environment"]["temperature_c"] = -50
        self.assertNotEqual(comparison_domain(original), comparison_domain(colder))

    def test_cartesian_product_is_not_implementation_evidence(self):
        registry = Registry(self.path)
        registry.admit(self.candidate())
        for overrides in ({"num_words": 64}, {"mux_ratio": 4}, {"word_size": True}, {"unknown": 1}):
            candidate = self.candidate()
            candidate["parameters"].update(overrides)
            with self.subTest(overrides=overrides), self.assertRaises(ContractError):
                registry.admit(candidate)

    def test_evidence_drift_and_failure_do_not_admit(self):
        registry = Registry(self.path)
        candidate = self.candidate()
        self.proof.write_text(json.dumps({"completed": True, "equivalent": False}))
        with self.assertRaisesRegex(ContractError, "hash drift"):
            registry.admit(candidate)
        ref = file_ref(self.proof)
        self.data["pdk"]["evidence"] = ref
        self.data["capabilities"][0]["implementations"][0]["models"]["F2"]["artifacts"]["proof"] = ref
        self.save()
        with self.assertRaisesRegex(ContractError, "validation failed"):
            Registry(self.path).admit(candidate)

    def test_fidelity_and_access_contract_cannot_be_relabelled(self):
        registry = Registry(self.path)
        for field, value in (("family", "register_file"), ("backend", "other")):
            candidate = self.candidate()
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                registry.admit(candidate)
        candidate = self.candidate()
        candidate["access_contract"]["ports"] = ["R", "R", "W"]
        with self.assertRaisesRegex(ContractError, "access_contract"):
            registry.admit(candidate)
        candidate = self.candidate()
        candidate["environment"]["fidelity"] = "F4"
        with self.assertRaises(ContractError):
            registry.admit(candidate)

    def test_invalid_environment_and_unknown_fields_reject(self):
        for key, value in (("period_ns", 0), ("slew_ns", 0.7), ("skew1", 0.5),
                           ("voltage_v", float("nan")), ("cload_ff", True)):
            candidate = self.candidate()
            candidate["environment"][key] = value
            with self.subTest(key=key), self.assertRaises(ContractError):
                validate_candidate(candidate)
        candidate = self.candidate()
        candidate["estimated_area"] = 0
        with self.assertRaisesRegex(ContractError, "unknown"):
            validate_candidate(candidate)
        self.path.write_text('{"schema": 1, "schema": 2}')
        with self.assertRaisesRegex(ContractError, "duplicate JSON key"):
            load_json(self.path)

    def test_unknown_exclusion_does_not_silently_disable_constraint(self):
        self.data["capabilities"][0]["excludes"] = [
            {"when": {"mux_ratios": 1}, "reason": "forbidden topology"}]
        self.save()
        with self.assertRaisesRegex(ContractError, "undeclared parameter"):
            Registry(self.path)
        self.data["capabilities"][0]["excludes"][0]["when"] = {"mux_ratio": 1}
        self.save()
        registry = Registry(self.path)
        with self.assertRaisesRegex(ContractError, "excluded combination"):
            registry.admit(self.candidate())

    def test_json_pointer_cannot_use_python_negative_or_signed_indices(self):
        self.proof.write_text(json.dumps({"checks": [{"passed": False}, {"passed": True}]}))
        self.data["pdk"]["evidence"] = file_ref(self.proof)
        model = self.data["capabilities"][0]["implementations"][0]["models"]["F2"]
        model["artifacts"]["proof"] = file_ref(self.proof)
        for token in ("-1", "+1", "01", "0"):
            model["basic_checks"] = [{"artifact": "proof", "pointer": "/checks/" + token + "/passed",
                                      "equals": True}]
            self.save()
            registry = Registry(self.path)
            with self.subTest(token=token), self.assertRaises(ContractError):
                registry.admit(self.candidate())

    def test_measurement_requires_a_valid_domain_and_hash_checked_evidence(self):
        measured = dict(name="cycle_time_ns", value=2.5, unit="ns", port=None,
                        operation="macro", domain=deepcopy(self.environment), fidelity="F2",
                        evidence=[file_ref(self.proof)])
        for field, value in (("domain", None), ("evidence", True),
                             ("evidence", [{"path": "missing.json", "sha256": "0" * 64}])):
            invalid = deepcopy(measured)
            invalid[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContractError):
                validate_measurement(invalid, self.root)
        valid = validate_measurement(deepcopy(measured), self.root)
        self.assertEqual(valid["domain"]["cload_ff"], 5.0)
        self.proof.write_text('{"equivalent": false}')
        with self.assertRaisesRegex(ContractError, "hash drift"):
            validate_measurement(measured, self.root)

    def test_search_requires_explicit_margins_and_unique_axes(self):
        request = dict(schema="openram-search/1", capability_id="fixture.2rw",
                       parameters=self.parameters, environment=self.environment,
                       search={"environment.period_ns": [2.5, 10]}, objectives=["cycle_time_ns"],
                       required_margins=dict(wen_pulse_ns=0.1, sa_setup_ns=0, bl_diff_v=0.05,
                                             write_recovery_ns=0.05))
        duplicate = deepcopy(request)
        duplicate["search"]["environment.period_ns"] = [2.5, 2.5]
        with self.assertRaisesRegex(ContractError, "duplicate search"):
            validate_request(duplicate)
        missing = deepcopy(request)
        missing["required_margins"].pop("bl_diff_v")
        with self.assertRaisesRegex(ContractError, "all write/sense margins"):
            validate_request(missing)

    def test_malformed_request_values_return_contract_errors(self):
        request = dict(schema="openram-search/1", capability_id="fixture.2rw",
                       parameters=self.parameters, environment=self.environment,
                       search={}, objectives=["cycle_time_ns"],
                       required_margins=dict(wen_pulse_ns=.1, sa_setup_ns=0, bl_diff_v=.05,
                                             write_recovery_ns=.05))
        for key, value in (("capability_id", []), ("parameters", []), ("environment", []),
                           ("objectives", [{}]), ("objectives", [[]]), ("objectives", [False])):
            invalid = deepcopy(request)
            invalid[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                validate_request(invalid)


if __name__ == "__main__":
    unittest.main()
