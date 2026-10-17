"""Regression tests for the generic/OpenRAM KLayout LVS profile boundary."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import opensource


class LvsProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pdk = root / "pdk"
        lvs = self.pdk / "libs.tech/lvs"
        rule_decks = lvs / "rule_decks"
        rule_decks.mkdir(parents=True)
        (lvs / "ics55.lvs").write_text(
            "# test deck\nLVS_MODULE_FILES = [\n"
            "  'rule_decks/combine_fingers.lvs',\n"
            "  'rule_decks/devices_connections.lvs'\n]\n")
        (rule_decks / "combine_fingers.lvs").write_text("# test combine\n")
        (rule_decks / "devices_connections.lvs").write_text("# test connections\n")
        self.manifest = self.pdk / "tests/regression/klayout_alignment_manifest.json"
        self.manifest.parent.mkdir(parents=True)
        self.manifest.write_text(json.dumps({"frozen_hashes": {"lvs_rule_decks": {
            "ics55.lvs": opensource._sha256(lvs / "ics55.lvs"),
            "combine_fingers.lvs": opensource._sha256(rule_decks / "combine_fingers.lvs"),
            "devices_connections.lvs": opensource._sha256(
                rule_decks / "devices_connections.lvs"),
        }}}))
        (lvs / "openram_strict_capability.json").write_text(json.dumps({
            "schema": opensource.STRICT_CAPABILITY_SCHEMA,
            "profile": "openram-macro",
            "pdk_revision": "fixture-revision",
            "klayout_version": "0.30.7",
            "strict_topology": True,
            "no_well_ties": True,
            "comparison": {
                "mos_w_l_absolute_um": 1e-9,
                "mos_w_l_relative": 0.0,
                "declared_top_pins": "case-insensitive-exact",
                "device_combination": "physical-parallel-only",
                "net_joining": False,
            },
            "hash_manifest": "../../tests/regression/klayout_alignment_manifest.json",
            "hash_scope": "frozen_hashes.lvs_rule_decks",
        }))
        self.gds = root / "macro.gds"
        self.netlist = root / "macro.lvs.sp"
        self.gds.write_text("gds")
        self.netlist.write_text("spice")
        self.out_dir = root / "run"
        self.librelane = root / "librelane"
        self.librelane.mkdir()
        (self.librelane / "shell.nix").write_text("# fixture\n")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_klayout(self, args, _librelane_root, log_path, selection=None):
        self.calls.append(list(args))
        Path(log_path).write_text("CONGRATULATIONS! Netlists match.\n")
        return 0, "CONGRATULATIONS! Netlists match."

    def test_changed_included_rule_deck_rejects_before_launch(self):
        deck = self.pdk / "libs.tech/lvs/rule_decks/devices_connections.lvs"
        deck.write_text("# changed connectivity semantics\n")
        with self.assertRaisesRegex(opensource.OpenSourceError, "hash drift"):
            opensource._validate_strict_capability(self.pdk)


    def test_missing_included_deck_digest_rejects_strict_launch(self):
        manifest = json.loads(self.manifest.read_text())
        del manifest["frozen_hashes"]["lvs_rule_decks"]["devices_connections.lvs"]
        self.manifest.write_text(json.dumps(manifest))
        with patch.object(opensource, "_select_klayout",
                          return_value=("executable", "klayout")):
            with patch.object(opensource, "_query_klayout_version",
                              return_value="0.30.7"):
                with patch.object(opensource, "_run_klayout", self.fake_klayout):
                    with self.assertRaises(opensource.OpenSourceError):
                        opensource._run_lvs(
                            self.gds, "TOP", self.netlist, self.pdk, self.librelane,
                            self.out_dir, "openram-macro")
        self.assertEqual(self.calls, [])
        self.assertFalse((self.out_dir / "strict_capability.json").exists())

    def test_mismatched_klayout_version_rejects_strict_launch(self):
        with patch.object(opensource, "_select_klayout",
                          return_value=("executable", "klayout")):
            with patch.object(opensource, "_query_klayout_version",
                              return_value="0.30.6"):
                with patch.object(opensource, "_run_klayout", self.fake_klayout):
                    with self.assertRaisesRegex(
                            opensource.OpenSourceError, "requires KLayout 0.30.7"):
                        opensource._run_lvs(
                            self.gds, "TOP", self.netlist, self.pdk, self.librelane,
                            self.out_dir, "openram-macro")
        self.assertEqual(self.calls, [])
        self.assertFalse((self.out_dir / "strict_capability.json").exists())


    def test_unknown_klayout_version_rejects_strict_launch(self):
        with patch.object(opensource, "_select_klayout",
                          return_value=("executable", "klayout")):
            with patch.object(
                    opensource.subprocess, "run",
                    return_value=SimpleNamespace(returncode=0, stdout="unrecognized output")):
                with patch.object(opensource, "_run_klayout", self.fake_klayout):
                    with self.assertRaises(opensource.OpenSourceError):
                        opensource._run_lvs(
                            self.gds, "TOP", self.netlist, self.pdk, self.librelane,
                            self.out_dir, "openram-macro")
        self.assertEqual(self.calls, [])
        self.assertFalse((self.out_dir / "strict_capability.json").exists())


    def test_generic_profile_rejects_hierarchy_before_tool_launch(self):
        with self.assertRaises(ValueError):
            opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "generic", "hierarchical")
        self.assertEqual(self.calls, [])

    def test_hierarchy_mapping_failure_is_not_reported_as_mismatch(self):
        def inconclusive_klayout(_args, _librelane_root, log_path, selection=None):
            Path(log_path).write_text("ICS55_LVS_COMPARE=INCONCLUSIVE\n")
            return 0, "ICS55_LVS_COMPARE=INCONCLUSIVE"

        with patch.object(opensource, "_select_klayout",
                          return_value=("executable", "klayout")):
            with patch.object(opensource, "_query_klayout_version",
                              return_value="0.30.7"):
                with patch.object(opensource, "_run_klayout", inconclusive_klayout):
                    matched = opensource._run_lvs(
                        self.gds, "TOP", self.netlist, self.pdk, self.librelane,
                        self.out_dir, "openram-macro", "hierarchical")
        self.assertEqual(matched[0:2], (False, "inconclusive"))


    def test_unknown_profile_is_rejected_before_tool_launch(self):
        with self.assertRaises(ValueError):
            opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "unexpected")
        self.assertEqual(self.calls, [])


    def test_success_text_without_native_comparison_is_inconclusive(self):
        def text_only(_args, _root, log_path, selection=None):
            output = "ICS55_LVS_COMPARE=PASS\nCONGRATULATIONS! Netlists match.\n"
            Path(log_path).write_text(output)
            return 0, output
        with patch.object(opensource, "_select_klayout", return_value=("executable", "klayout")):
            with patch.object(opensource, "_query_klayout_version", return_value="0.30.7"):
                with patch.object(opensource, "_run_klayout", text_only):
                    result = opensource._run_lvs(
                        self.gds, "TOP", self.netlist, self.pdk, self.librelane,
                        self.out_dir, "openram-macro", "flat")
        self.assertEqual(result[:2], (False, "inconclusive"))

    def test_stale_matching_native_outputs_cannot_be_reused_after_zero_exit(self):
        fixtures = Path(__file__).with_name("fixtures")
        self.netlist.write_text((fixtures / "strict_two_mos.sp").read_text().replace("body_probe", "TOP"))
        self.out_dir.mkdir()
        (self.out_dir / "lvs.lvsdb").write_text(
            (fixtures / "strict_two_mos.lvsdb").read_text().replace("body_probe", "TOP"))
        (self.out_dir / "extracted.spice").write_text(self.netlist.read_text())
        tool = self.out_dir / "synthetic-tool"
        tool.write_text("SYNTHETIC unit tool identity, never executed\n")
        output = "ICS55_LVS_COMPARE=PASS\nCONGRATULATIONS! Netlists match.\n"
        with patch.object(opensource, "_select_klayout", return_value=("executable", str(tool))):
            with patch.object(opensource, "_query_klayout_version", return_value="0.30.7"):
                with patch.object(opensource.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0, stdout=output)):
                    result = opensource._run_lvs(
                        self.gds, "TOP", self.netlist, self.pdk, self.librelane,
                        self.out_dir, "openram-macro", "flat")
        self.assertEqual(result[:2], (False, "inconclusive"))


class RuntimeProvenanceTests(unittest.TestCase):
    """Exercise the real producer at its subprocess boundary, without EDA."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.deck = self.root / "deck" / "ics55.lvs"
        self.deck.parent.mkdir()
        self.deck.write_text("# SYNTHETIC unit deck\n")
        self.gds = self.root / "macro.gds"
        self.gds.write_text("SYNTHETIC layout identity\n")
        self.reference = self.root / "source.sp"
        self.child = self.root / "included.sp"
        self.reference.write_text('.include "included.sp"\n.subckt macro a b\n.ends\n')
        self.child.write_text("* SYNTHETIC include\n")
        self.tool = self.root / "synthetic-tool"
        self.tool.write_text("SYNTHETIC binary identity, not executed\n")
        self.report = self.root / "run" / "report.lvsdb"
        self.log = self.root / "run" / "lvs.log"
        self.args = ["-b", "-r", str(self.deck), "-rd", "input=" + str(self.gds),
                     "-rd", "schematic=" + str(self.reference), "-rd", "report=" + str(self.report)]

    def invoke(self, result):
        with patch.object(opensource, "_query_klayout_version", return_value="0.30.7"):
            with patch.object(opensource.subprocess, "run", side_effect=result):
                return opensource._run_klayout(
                    self.args, self.root, self.log, selection=("executable", str(self.tool)))

    def runtime(self):
        return json.loads(Path(str(self.log) + ".runtime.json").read_text())

    def test_real_runner_captures_prelaunch_inputs_tool_and_transitive_include(self):
        def child(_command, **_kwargs):
            self.report.write_text("SYNTHETIC fresh report\n")
            return SimpleNamespace(returncode=0, stdout="CONGRATULATIONS! Netlists match.\n")
        code, _ = self.invoke(child)
        self.assertEqual(code, 0)
        runtime = self.runtime()
        self.assertEqual(runtime["schema"], "ics55-klayout-runtime/1")
        self.assertEqual(runtime["inputs"]["input"], opensource._artifact_ref(self.gds))
        self.assertEqual(runtime["tool"]["executable"], opensource._artifact_ref(self.tool))
        self.assertIn(opensource._artifact_ref(self.child), runtime["sources"])
        self.assertTrue(runtime["inputs_unchanged"])
        self.assertTrue(runtime["outputs"]["report"]["fresh"])
        self.assertEqual(runtime["log"], opensource._artifact_ref(self.log))
        self.assertFalse(runtime["signoff"])

    def test_failed_launcher_keeps_returncode_and_success_looking_stdout(self):
        code, _ = self.invoke(lambda *_args, **_kwargs: SimpleNamespace(
            returncode=17, stdout="CONGRATULATIONS! Netlists match.\n"))
        self.assertEqual(code, 17)
        self.assertEqual(self.runtime()["returncode"], 17)
        self.assertIn("CONGRATULATIONS", self.log.read_text())
        self.assertEqual(self.runtime()["outputs"], {})

    def test_changed_input_during_execution_retains_original_hash_and_rejects_integrity(self):
        original = opensource._artifact_ref(self.child)
        def child(*_args, **_kwargs):
            self.child.write_text("* changed by synthetic child\n")
            return SimpleNamespace(returncode=0, stdout="finished\n")
        self.invoke(child)
        runtime = self.runtime()
        self.assertFalse(runtime["inputs_unchanged"])
        self.assertIn(original, runtime["sources"])
        self.assertNotEqual(original, opensource._artifact_ref(self.child))

    def test_preexisting_report_is_not_recorded_as_fresh_output(self):
        self.report.parent.mkdir()
        self.report.write_text("SYNTHETIC old report\n")
        start = self.report.stat().st_mtime_ns + 1
        with patch.object(opensource.time, "time_ns", return_value=start):
            self.invoke(lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="finished\n"))
        self.assertFalse(self.runtime()["outputs"]["report"]["fresh"])

    def test_spawn_error_keeps_unknown_returncode_and_diagnostic(self):
        def fail(*_args, **_kwargs):
            raise OSError("synthetic spawn failure")
        with self.assertRaisesRegex(opensource.OpenSourceError, "cannot start"):
            self.invoke(fail)
        runtime = self.runtime()
        self.assertIsNone(runtime["returncode"])
        self.assertFalse(runtime["inputs_unchanged"])
        self.assertIn("synthetic spawn failure", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
