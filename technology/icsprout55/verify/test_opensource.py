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


if __name__ == "__main__":
    unittest.main()
