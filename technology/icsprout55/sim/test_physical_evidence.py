"""Isolated physical-evidence fixtures; no real calibration or EDA execution.

The accepted fixture uses a real serialized KLayout LVS database and synthetic
dimensional coupon values. It is an executable schema example ONLY. No fixture
is a production record, PDK coefficient, or historical provenance.
"""
import csv
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from physical_evidence import validate_physical_evidence, opensource, lvs_report
from gds_pex_rc import extract_parasitics, write_spice


def json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path

# Native report/source fixtures need neither the private PDK nor an EDA child.
NATIVE_FIXTURES = Path(opensource.__file__).with_name("fixtures")
NATIVE_LVSDB = (NATIVE_FIXTURES / "strict_two_mos.lvsdb").read_text()


def rect(layer, x0=0, y0=0, x1=10, y1=1):
    return dict(layer=layer, x0=x0, y0=y0, x1=x1, y1=y1)


def net(name, rectangles, anchors=()):
    return dict(name=name, rectangles=rectangles, anchors=list(anchors), vias=[])


def coupon(kind, layers):
    layer = layers[0]
    result = dict(schema_version=1, design="coupon", ports=[], devices=[])
    if kind == "resistance":
        # A 10um x 1um strip, finely partitioned. The native endpoint-center
        # mesh predicts 9.9 squares; the dimensional reference is 10 squares.
        anchors = [dict(name="left", layer=layer, x=0, y=0),
                   dict(name="right", layer=layer, x=10, y=0)]
        result.update(ports=["left", "right"], nets=[net("wire", [
            rect(layer, index / 10, 0, (index + 1) / 10, 1) for index in range(100)], anchors)])
    elif kind == "ground_capacitance":
        result["nets"] = [net("a", [rect(layer)])]
    elif kind == "lateral_capacitance":
        result["nets"] = [net("a", [rect(layer)]), net("b", [rect(layer, 0, 2, 10, 3)])]
    else:
        result["nets"] = [net("a", [rect(layer)]), net("b", [rect(layers[1])])]
    return result


class PhysicalEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.pdk = self.root / "pdk"
        lvs = self.pdk / "libs.tech/lvs"
        json_file(self.pdk / "libs.tech/drc/rule_decks/rules.json", {"fixture": True})
        self.drc_deck = self.pdk / "libs.tech/drc/ics55_drc.drc"
        self.drc_deck.write_text("# SYNTHETIC test deck, not executable production rules\n")
        (lvs / "rule_decks").mkdir(parents=True)
        (lvs / "ics55.lvs").write_text("LVS_MODULE_FILES = [\n 'rule_decks/compare.lvs'\n]\n")
        (lvs / "rule_decks/compare.lvs").write_text("# SYNTHETIC exact-pin/W/L test module\n")
        frozen = {"ics55.lvs": opensource._sha256(lvs / "ics55.lvs"),
                  "compare.lvs": opensource._sha256(lvs / "rule_decks/compare.lvs")}
        json_file(self.pdk / "frozen.json", {"frozen_hashes": {"lvs_rule_decks": frozen}})
        self.capability_source = json_file(lvs / opensource.STRICT_CAPABILITY_NAME, {
            "schema": opensource.STRICT_CAPABILITY_SCHEMA, "profile": "openram-macro",
            "pdk_revision": "SYNTHETIC-UNIT-FIXTURE", "klayout_version": "0.30.7",
            "strict_topology": True, "no_well_ties": True,
            "comparison": {"mos_w_l_absolute_um": 1e-9, "mos_w_l_relative": 0.0,
                           "declared_top_pins": "case-insensitive-exact",
                           "device_combination": "physical-parallel-only", "net_joining": False},
            "hash_manifest": "../../frozen.json", "hash_scope": "frozen_hashes.lvs_rule_decks",
        })
        self.paths = {name: self.root / filename for name, filename in {
            "gds": "macro.gds", "reference": "macro.lvs.sp", "netlist": "macro.pex.sp",
            "extraction": "pex_summary.json"}.items()}
        self.paths["gds"].write_text("SYNTHETIC GDS IDENTITY ONLY\n")
        self.paths["reference"].write_text(
            "* Native body connectivity probe\n"
            ".SUBCKT macro gnd vdd out_n out_p in_n in_p\n"
            "MN out_n in_n gnd gnd nm1p2_svt_lp W=20u L=10u\n"
            "MP out_p in_p vdd vdd pm1p2_svt_lp W=20u L=10u\n"
            ".ENDS macro\n")
        self.tool = self.root / "synthetic-klayout"
        self.tool.write_text("SYNTHETIC unit-test binary identity, never run\n")
        self.waivers = json_file(self.root / "waivers.json", {"version": 1, "waivers": []})
        self.run = self.root / "verification"

        def simulated_subprocess(command, **_kwargs):
            if command[-1] == "-v":
                return SimpleNamespace(returncode=0, stdout="KLayout 0.30.7\n")
            params = dict(item.split("=", 1) for index, item in enumerate(command)
                          if index and command[index - 1] == "-rd")
            report = Path(params["report"])
            if "schematic" in params:
                report.write_text(NATIVE_LVSDB.replace("body_probe", "macro"))
                Path(params["target_netlist"]).write_text(self.paths["reference"].read_text())
                output = "CONGRATULATIONS! Netlists match.\nICS55_LVS_COMPARE=PASS\n"
            else:
                report.write_text("<report-database><top-cell>macro</top-cell><items/></report-database>\n")
                output = "SYNTHETIC DRC complete\n"
            return SimpleNamespace(returncode=0, stdout=output)

        with patch.object(opensource, "_select_klayout", return_value=("executable", str(self.tool))):
            with patch.object(opensource.subprocess, "run", side_effect=simulated_subprocess):
                opensource._run_drc(self.paths["gds"], "macro", self.pdk, self.root,
                                    self.run / "drc", "full", self.waivers, "macro")
                opensource._run_lvs(self.paths["gds"], "macro", self.paths["reference"], self.pdk,
                                    self.root, self.run / "lvs", "openram-macro", "flat")
        self.stage_paths = {key: self.run / key / (key + ".evidence.json") for key in ("drc", "lvs")}
        self.generator = self.pdk / "libs.tech/pex/config_gen.py"
        self.generator.parent.mkdir(parents=True)
        self.generator.write_text("# SYNTHETIC unit fixture generator identity\n")
        self.process_input = json_file(self.root / "explicit_fixture.json", {"source": "synthetic-independent-data"})
        self.process = {
            "kind": "ics55_gds_pex_process", "schema_version": 1, "source": "lef",
            "corner": "typ", "status": "explicit_coupling", "signoff": False,
            "max_cross_layer": 3, "max_spacing": 5, "explicit_layers": ["MET1", "MET2"],
            "layers": {"MET" + str(index): {
                "order": index, "width_um": 1, "resistance_ohm_per_square": index,
                "ground_capacitance_pf_per_um2": .001 * index,
                "edge_capacitance_pf_per_um": .001 * index,
                "coupling": {"same_layer_pf_per_um": .001 * index, "cross_layer_pf_per_um2": .003},
            } for index in (1, 2)}, "vias": {},
            "limitations": ["SYNTHETIC fixture; ideal FEOL terminals; no foundry signoff"],
            "provenance": {"generator": str(self.generator), "process_json": str(self.process_input),
                           "assumptions": [], "stack_source": "lef"},
        }
        geometry = dict(schema_version=1, design="macro", input_gds_sha256=opensource._sha256(self.paths["gds"]),
                        ports=["a", "b"], devices=[], nets=[
                            net(name, [rect(layer)], [dict(name=name, layer=layer, x=0, y=0)])
                            for name, layer in (("a", "MET1"), ("b", "MET2"))])
        parasitics = extract_parasitics(geometry, self.process, "rcc")
        write_spice(geometry, parasitics, self.paths["netlist"])
        self.geometry_path = json_file(self.root / "geometry.json", geometry)
        self.process_path = json_file(self.root / "process.json", self.process)
        self.parasitics_path = json_file(self.root / "parasitics.json", parasitics)
        self.pex_evidence = json_file(self.root / "pex.evidence.json", {
            "schema": "ics55-gds-pex-evidence/1", "signoff": False,
            "started_ns": 1, "ended_ns": 2, "top": "macro", "source": "lef", "mode": "rcc",
            "input": self.ref(self.paths["gds"]), "inputs_unchanged": True,
            "sources": [self.ref(self.generator), self.ref(self.process_input)] + [
                self.ref(Path(opensource.__file__).parent / name)
                for name in ("opensource.py", "gds_pex_geometry.py", "gds_pex_rc.py")],
            "outputs": {"geometry": self.ref(self.geometry_path), "typ": {
                "process": self.ref(self.process_path), "parasitics": self.ref(self.parasitics_path),
                "netlist": self.ref(self.paths["netlist"])}}})
        self.extraction = {"kind": "ics55_gds_pex", "schema_version": 1, "design": "macro",
                           "input_gds_sha256": opensource._sha256(self.paths["gds"]),
                           "completed": True, "accepted": True, "signoff": False,
                           "evidence": self.ref(self.pex_evidence), "corners": [{
                               "corner": "typ", "netlist": str(self.paths["netlist"]),
                               "netlist_sha256": opensource._sha256(self.paths["netlist"]),
                               "status": "explicit_coupling", "mode": "rcc", "accepted": True}]}
        json_file(self.paths["extraction"], self.extraction)
        self.observation_paths = []
        # Analytic, dimensional reference values, NOT obtained by forwarding the
        # extractor's predictions into its reference fields.
        for kind, layers, measured in [
                ("resistance", ["MET1"], 10.0), ("resistance", ["MET2"], 20.0),
                ("ground_capacitance", ["MET1"], .032), ("ground_capacitance", ["MET2"], .064),
                ("lateral_capacitance", ["MET1"], .010), ("lateral_capacitance", ["MET2"], .020),
                ("cross_capacitance", ["MET1", "MET2"], .030)]:
            tag = kind + "_" + "_".join(layers)
            structure = json_file(self.root / "coupons" / (tag + ".json"), coupon(kind, layers))
            unit = "ohm" if kind == "resistance" else "pF"
            source = self.root / "coupons" / (tag + ".csv")
            with source.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["id", "kind", "layers", "rc_corner", "length_unit",
                                                            "unit", "value", "structure_sha256"])
                writer.writeheader()
                writer.writerow(dict(id=tag, kind=kind, layers=":".join(layers), rc_corner="typ", length_unit="um",
                                     unit=unit, value=measured, structure_sha256=opensource._sha256(structure)))
            observation = json_file(self.root / "observations" / (tag + ".json"), {
                "schema": "ics55-rc-observation/1", "signoff": False, "method": "measurement",
                "kind": kind, "layers": layers, "rc_corner": "typ", "length_unit": "um", "unit": unit,
                "value": measured, "structure": self.ref(structure), "sources": [self.ref(source)],
                "source_observation": tag})
            self.observation_paths.append(observation)
        self.calibration_path = json_file(self.root / "calibration.json", {
            "schema": "ics55-rc-calibration/1", "signoff": False, "classification": "quantitative-independent",
            "rc_corner": "typ", "bindings": {name: self.ref(path) for name, path in self.calibration_inputs().items()},
            "idealizations": sorted(set(self.process["limitations"] + parasitics["summary"]["limitations"])),
            "limits": {kind: .02 for kind in ("resistance", "ground_capacitance", "lateral_capacitance", "cross_capacitance")},
            "observations": [self.ref(path) for path in self.observation_paths]})
        self.manifest_path = self.root / "physical.json"
        self.manifest = {"schema": "ics55-physical-evidence/1", "signoff": False, "top": "macro",
                         "bindings": {name: self.ref(path) for name, path in self.paths.items()},
                         "drc": self.ref(self.stage_paths["drc"]), "lvs": self.ref(self.stage_paths["lvs"]),
                         "pex": {"corner": "typ", "geometry": self.ref(self.geometry_path),
                                 "process": self.ref(self.process_path), "parasitics": self.ref(self.parasitics_path),
                                 "calibration": self.ref(self.calibration_path)}}
        self.save()

    @staticmethod
    def ref(path):
        return opensource._artifact_ref(path)

    @staticmethod
    def read(path):
        return json.loads(Path(path).read_text())

    def calibration_inputs(self):
        return dict(self.paths, geometry=self.geometry_path, process=self.process_path, parasitics=self.parasitics_path)

    def save(self):
        json_file(self.manifest_path, self.manifest)

    def validate(self):
        return validate_physical_evidence(self.manifest_path, self.paths, self.extraction)

    def edit_stage(self, kind, edit):
        path = self.stage_paths[kind]
        stage = self.read(path)
        edit(stage)
        json_file(path, stage)
        self.manifest[kind] = self.ref(path)
        self.save()

    def edit_runtime(self, kind, edit):
        stage = self.read(self.stage_paths[kind])
        path = Path(stage["runtime"]["path"])
        runtime = self.read(path)
        edit(runtime)
        json_file(path, runtime)
        self.edit_stage(kind, lambda data: data.update(runtime=self.ref(path)))

    def edit_calibration(self, edit):
        data = self.read(self.calibration_path)
        edit(data)
        json_file(self.calibration_path, data)
        self.manifest["pex"]["calibration"] = self.ref(self.calibration_path)
        self.save()

    def edit_observation(self, index, edit):
        path = self.observation_paths[index]
        data = self.read(path)
        edit(data)
        json_file(path, data)
        self.edit_calibration(lambda calibration: calibration.update(
            observations=[self.ref(item) for item in self.observation_paths]))

    def refresh_pex(self):
        """Rebind intentional synthetic fixture edits, never production evidence."""
        evidence = self.read(self.pex_evidence)
        evidence["sources"] = [self.ref(item["path"]) for item in evidence["sources"]]
        for name in ("process", "parasitics", "netlist"):
            evidence["outputs"]["typ"][name] = self.ref(self.calibration_inputs()[name])
        json_file(self.pex_evidence, evidence)
        self.extraction["evidence"] = self.ref(self.pex_evidence)
        json_file(self.paths["extraction"], self.extraction)
        self.manifest["bindings"]["extraction"] = self.ref(self.paths["extraction"])
        for name in ("process", "parasitics"):
            self.manifest["pex"][name] = self.ref(self.calibration_inputs()[name])
        self.edit_calibration(lambda data: data.update(bindings={name: self.ref(path)
                                                               for name, path in self.calibration_inputs().items()}))

    def test_complete_synthetic_quantitative_fixture_accepts_research_only(self):
        result = self.validate()
        self.assertEqual(result["checks"], dict(drc=True, lvs=True, pex_accepted=True), result["reasons"])
        self.assertFalse(result["estimated"])
        self.assertEqual(result["reasons"], [])
        artifacts = {item["path"] for item in result["artifacts"].values()}
        self.assertIn(str(self.tool), artifacts)
        self.assertIn(str(self.waivers), artifacts)
        self.assertIn(str(self.drc_deck.parent / "rule_decks/rules.json"), artifacts)
        self.assertIn(self.read(self.observation_paths[0])["sources"][0]["path"], artifacts)
        self.assertNotIn("signoff", result)

    def test_missing_manifest_is_unknown_but_known_estimate_is_false(self):
        result = validate_physical_evidence(None, self.paths, self.extraction)
        self.assertEqual(result["checks"], dict(drc=None, lvs=None, pex_accepted=None))
        self.extraction["accepted"] = False
        self.extraction["corners"][0].update(status="estimated", accepted=False)
        result = validate_physical_evidence(None, self.paths, self.extraction)
        self.assertEqual(result["checks"], dict(drc=None, lvs=None, pex_accepted=False))
        self.assertTrue(result["estimated"])

    def test_explicit_estimated_bundle_cannot_pass(self):
        self.extraction["accepted"] = False
        self.extraction["corners"][0].update(status="estimated", accepted=False)
        self.refresh_pex()
        result = self.validate()
        self.assertIs(result["checks"]["pex_accepted"], False)
        self.assertTrue(result["estimated"])
        self.assertIs(result["checks"]["drc"], True)
        self.assertIs(result["checks"]["lvs"], True)

    def test_stale_gds_or_reference_cannot_be_rebound_by_pass_claim(self):
        for name in ("gds", "reference"):
            with self.subTest(name=name):
                path = self.paths[name]
                original = path.read_bytes()
                path.write_bytes(original + b"changed\n")
                result = self.validate()
                self.assertIsNone(result["checks"]["drc"])
                self.assertIsNone(result["checks"]["lvs"])
                self.assertTrue(result["estimated"])
                path.write_bytes(original)

    def test_wrong_top_and_signoff_claim_reject(self):
        for update in ({"top": "other"}, {"signoff": True}):
            original = deepcopy(self.manifest)
            self.manifest.update(update)
            self.save()
            self.assertTrue(self.validate()["estimated"])
            self.assertIsNone(self.validate()["checks"]["drc"])
            self.manifest = original
            self.save()

    def test_report_existence_and_self_declared_verdict_are_not_proof(self):
        self.edit_stage("drc", lambda stage: stage.update(runtime=None))
        self.assertIsNone(self.validate()["checks"]["drc"])
        self.edit_runtime("lvs", lambda runtime: self._rewrite_log(runtime, "finished without a verdict\n"))
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def _rewrite_log(self, runtime, text):
        path = Path(runtime["log"]["path"])
        path.write_text(text)
        runtime["log"] = self.ref(path)

    def test_nonzero_launcher_and_inconclusive_are_unknown_not_mismatches(self):
        self.edit_runtime("lvs", lambda runtime: runtime.update(returncode=17))
        self.assertIsNone(self.validate()["checks"]["lvs"])
        self.edit_runtime("lvs", lambda runtime: (runtime.update(returncode=0),
                          self._rewrite_log(runtime, "ICS55_LVS_COMPARE=INCONCLUSIVE\n")))
        self.edit_stage("lvs", lambda stage: stage.update(verdict="inconclusive"))
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_completed_strict_mismatch_is_false(self):
        self.edit_runtime("lvs", lambda runtime: self._rewrite_log(runtime, "ERROR: netlists do not match.\n"))
        self.edit_stage("lvs", lambda stage: stage.update(verdict="mismatch"))
        self.assertIs(self.validate()["checks"]["lvs"], False)

    def test_changed_native_report_invalidates_cache_and_physical_verdict(self):
        from technology.icsprout55.sim import architecture as native_adapter

        admission = {"artifacts": {"physical": self.manifest_path}}
        initial = native_adapter.fingerprint(admission)
        self.assertIs(self.validate()["checks"]["lvs"], True)
        report = Path(self.read(self.stage_paths["lvs"])["report"]["path"])
        report.write_text(report.read_text().replace("   D(2 2 1)\n", "", 1))
        changed = native_adapter.fingerprint(admission)
        self.assertNotEqual(initial, changed)
        self.assertEqual(changed["dependencies"][str(report)], native_adapter._digest(report))
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def rewrite_native_report(self, edit):
        """Fault-inject native data while retaining valid synthetic hash refs."""
        report = Path(self.read(self.stage_paths["lvs"])["report"]["path"])
        report.write_text(edit(report.read_text()))
        current = self.ref(report)
        self.edit_runtime("lvs", lambda runtime: runtime["outputs"]["report"].update(current))
        self.edit_stage("lvs", lambda stage: stage.update(report=current))

    def test_success_log_and_claim_cannot_bless_a_skipped_native_root(self):
        skipped = (NATIVE_FIXTURES / "strict_skipped_root.lvsdb").read_text().replace("body_probe", "macro")
        self.rewrite_native_report(lambda _text: skipped)
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_success_log_and_claim_cannot_hide_an_unpaired_native_device(self):
        self.rewrite_native_report(lambda text: text.replace("   D(2 2 1)\n", "", 1))
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_original_source_boundary_is_required_even_with_complete_native_pairs(self):
        source = self.paths["reference"]
        source.write_text(source.read_text().replace("out_n out_p in_n in_p", "out_n in_n in_p", 1))
        current = self.ref(source)
        self.manifest["bindings"]["reference"] = current
        self.save()
        def refresh(runtime):
            runtime["inputs"]["schematic"] = current
            runtime["sources"] = [self.ref(item["path"]) for item in runtime["sources"]]
        self.edit_runtime("lvs", refresh)
        result = self.validate()
        self.assertIs(result["checks"]["drc"], True)
        self.assertIsNone(result["checks"]["lvs"])

    def test_old_schematic_only_flat_arguments_cannot_claim_a_strict_match(self):
        def remove_physical_flat(runtime):
            args = runtime["args"]
            index = args.index("run_mode=flat")
            del args[index - 1:index + 1]
            runtime["parameters"].pop("run_mode")
            runtime["command"] = opensource._klayout_command(runtime["tool"]["selection"], args)
        self.edit_runtime("lvs", remove_physical_flat)
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_native_match_cannot_name_an_unrelated_comparison_reader(self):
        unrelated = self.root / "other-reader.py"
        unrelated.write_text("# Not the captured native comparison implementation\n")
        self.edit_stage("lvs", lambda stage: stage.update(comparison_reader=self.ref(unrelated)))
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_stale_report_and_deck_are_unknown(self):
        report = Path(self.read(self.stage_paths["drc"])["report"]["path"])
        report.write_text(report.read_text() + "<!-- modified -->")
        self.assertIsNone(self.validate()["checks"]["drc"])
        module = self.pdk / "libs.tech/lvs/rule_decks/compare.lvs"
        module.write_text("# changed semantics\n")
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_reduced_hierarchy_generic_lvs_and_nonfresh_report_reject(self):
        self.edit_stage("drc", lambda stage: stage.update(mode="top-interconnect"))
        self.assertIsNone(self.validate()["checks"]["drc"])
        self.edit_stage("lvs", lambda stage: stage.update(profile="generic"))
        self.assertIsNone(self.validate()["checks"]["lvs"])
        self.edit_stage("lvs", lambda stage: stage.update(profile="openram-macro"))
        self.edit_runtime("lvs", lambda runtime: runtime["outputs"]["report"].update(fresh=False))
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_unsafe_strict_capability_cannot_be_blessed_by_new_runtime_hashes(self):
        original = self.read(self.capability_source)
        for key, value in (("mos_w_l_relative", .1), ("mos_w_l_absolute_um", .01),
                           ("declared_top_pins", "optional"), ("net_joining", True)):
            with self.subTest(setting=key):
                data = deepcopy(original)
                data["comparison"][key] = value
                json_file(self.capability_source, data)
                self.edit_runtime("lvs", lambda runtime: runtime.update(
                    sources=[self.ref(item["path"]) for item in runtime["sources"]]))
                self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_drc_unwaived_is_completed_failure_not_unknown(self):
        stage = self.read(self.stage_paths["drc"])
        report = Path(stage["report"]["path"])
        report.write_text("<report-database><top-cell>macro</top-cell><items><item>"
                          "<cell>macro</cell><category>spacing</category><multiplicity>2</multiplicity>"
                          "<values><value>box:0,0,1,1</value></values></item></items></report-database>")
        summary_path = Path(stage["summary"]["path"])
        json_file(summary_path, opensource._parse_drc_report(report, self.waivers))
        self.edit_runtime("drc", lambda runtime: runtime["outputs"].update(report=dict(self.ref(report), fresh=True)))
        self.edit_stage("drc", lambda item: item.update(report=self.ref(report), summary=self.ref(summary_path)))
        self.assertIs(self.validate()["checks"]["drc"], False)

    def test_wildcard_waiver_is_rejected_even_with_fresh_hash(self):
        json_file(self.waivers, {"version": 1, "waivers": [{"cell": "*", "category": "spacing",
                   "reason": "unsafe broad waiver", "values": ["box:0,0,1,1"]}]})
        self.edit_stage("drc", lambda stage: stage.update(waivers=self.ref(self.waivers)))
        self.assertIsNone(self.validate()["checks"]["drc"])

    def test_calibrated_boolean_and_ratios_only_file_cannot_clear_pex(self):
        self.edit_calibration(lambda data: data.update(calibrated=True, accepted=True, observations=[]))
        self.assertIs(self.validate()["checks"]["pex_accepted"], False)
        self.edit_calibration(lambda data: data.update(schema="starrc-ratio-calibration/1", ratios={"M1": 1}))
        self.assertTrue(self.validate()["estimated"])

    def test_missing_lateral_cross_or_resistance_coverage_rejects(self):
        original = self.read(self.calibration_path)
        for kind in ("resistance", "lateral_capacitance", "cross_capacitance"):
            with self.subTest(kind=kind):
                self.edit_calibration(lambda data: data.update(observations=[self.ref(path) for path in self.observation_paths
                                                                           if self.read(path)["kind"] != kind]))
                self.assertIs(self.validate()["checks"]["pex_accepted"], False)
                json_file(self.calibration_path, original)

    def test_unresolved_units_wrong_corner_and_nan_cannot_be_calibration(self):
        original = self.read(self.observation_paths[0])
        for update in ({"unit": "ratio"}, {"length_unit": "unknown"}, {"rc_corner": "rcworst"}, {"value": float("nan")}):
            with self.subTest(update=update):
                self.edit_observation(0, lambda data: data.update(update))
                self.assertIs(self.validate()["checks"]["pex_accepted"], False)
                json_file(self.observation_paths[0], original)

    def test_quantitative_error_is_recomputed_not_claimed_pass(self):
        observation = self.read(self.observation_paths[0])
        source = Path(observation["sources"][0]["path"])
        with source.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        rows[0]["value"] = "100"
        with source.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        self.edit_observation(0, lambda data: data.update(value=100, sources=[self.ref(source)],
                                                         accepted=True, relative_error=0))
        result = self.validate()
        self.assertIs(result["checks"]["pex_accepted"], False)
        self.assertTrue(any("exceeds" in reason for reason in result["reasons"]))

    def test_ratios_source_cannot_replace_dimensional_raw_observations(self):
        ratios = json_file(self.root / "CALIBRATION.json", {"ratios": {"typ": 1.0}, "absolute_units": "unresolved"})
        self.edit_observation(0, lambda data: data.update(sources=[self.ref(ratios)]))
        self.assertIs(self.validate()["checks"]["pex_accepted"], False)

    def test_changed_transitive_calibration_source_is_fingerprinted_and_rejected(self):
        source = Path(self.read(self.observation_paths[0])["sources"][0]["path"])
        source.write_text(source.read_text() + "changed\n")
        result = self.validate()
        self.assertIs(result["checks"]["pex_accepted"], False)
        self.assertTrue(result["estimated"])
        self.assertIn(self.ref(source), result["artifacts"].values())

    def test_estimated_process_inheritance_cannot_clear_physical_gate(self):
        json_file(self.process_input, {"source": "pdk-estimate", "accepted": True, "calibrated": True})
        self.refresh_pex()
        result = self.validate()
        self.assertIs(result["checks"]["pex_accepted"], False)
        self.assertTrue(any("inherits estimated" in reason for reason in result["reasons"]))

    def test_missing_generation_capture_cannot_be_reconstructed_from_outputs(self):
        del self.extraction["evidence"]
        json_file(self.paths["extraction"], self.extraction)
        self.manifest["bindings"]["extraction"] = self.ref(self.paths["extraction"])
        self.edit_calibration(lambda data: data["bindings"].update(extraction=self.ref(self.paths["extraction"])))
        self.assertIs(self.validate()["checks"]["pex_accepted"], False)

    def test_zero_exit_tool_error_is_unknown_not_a_completed_mismatch(self):
        self.edit_runtime("lvs", lambda runtime: self._rewrite_log(runtime,
                          "ERROR: synthetic tool exception\nERROR: netlists do not match.\n"))
        self.edit_stage("lvs", lambda stage: stage.update(verdict="mismatch"))
        self.assertIsNone(self.validate()["checks"]["lvs"])

    def test_transitive_process_source_change_invalidates_pex_only(self):
        self.generator.write_text("# modified process source\n")
        result = self.validate()
        self.assertIs(result["checks"]["pex_accepted"], False)
        self.assertIs(result["checks"]["drc"], True)
        self.assertIs(result["checks"]["lvs"], True)

    def test_hidden_idealizations_and_unbounded_acceptance_reject(self):
        original = self.read(self.calibration_path)
        for edit in (lambda data: data.update(idealizations=["research only"]),
                     lambda data: data["limits"].update(resistance=float("inf")),
                     lambda data: data["limits"].update(resistance=True),
                     lambda data: data["limits"].update(resistance=1)):
            self.edit_calibration(edit)
            self.assertIs(self.validate()["checks"]["pex_accepted"], False)
            json_file(self.calibration_path, original)


if __name__ == "__main__":
    unittest.main()
