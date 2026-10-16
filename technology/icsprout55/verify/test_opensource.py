"""Regression tests for the generic/OpenRAM KLayout LVS profile boundary."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import opensource

STRICT_DECK = opensource.DEFAULT_PDK_ROOT / "libs.tech" / "lvs" / "ics55.lvs"


class StrictCapabilityTests(unittest.TestCase):
    def test_strict_capability_manifest_is_valid(self):
        capability = opensource._validate_strict_capability(opensource.DEFAULT_PDK_ROOT)
        self.assertEqual(capability["schema"], opensource.STRICT_CAPABILITY_SCHEMA)
        self.assertEqual(capability["klayout_version"], "0.30.7")
        self.assertIn("ics55.lvs", capability["deck_hashes"])

    def test_strict_profile_rejects_deck_hash_drift_before_tool_launch(self):
        with patch.object(opensource, "_sha256", return_value="0" * 64):
            with self.assertRaises(opensource.OpenSourceError):
                opensource._validate_strict_capability(opensource.DEFAULT_PDK_ROOT)

class LvsProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pdk = root / "pdk"
        lvs = self.pdk / "libs.tech/lvs"
        rule_decks = lvs / "rule_decks"
        rule_decks.mkdir(parents=True)
        (lvs / "ics55.lvs").write_text("# test deck\n")
        (rule_decks / "combine_fingers.lvs").write_text("# test combine\n")
        manifest = self.pdk / "tests/regression/klayout_alignment_manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"frozen_hashes": {"lvs_rule_decks": {
            "ics55.lvs": opensource._sha256(lvs / "ics55.lvs"),
            "combine_fingers.lvs": opensource._sha256(rule_decks / "combine_fingers.lvs"),
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
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()
    def fake_klayout(self, args, _librelane_root, log_path):
        self.calls.append(list(args))
        Path(log_path).write_text("CONGRATULATIONS! Netlists match.\n")
        return 0, "CONGRATULATIONS! Netlists match."

    def test_generic_profile_keeps_pdk_defaults(self):
        with patch.object(opensource, "_run_klayout", self.fake_klayout):
            matched = opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "generic")
        self.assertEqual(matched[0:2], (True, "matched"))
        self.assertNotIn("strict_topology=true", self.calls[0])
        self.assertNotIn("no_well_ties=true", self.calls[0])

    def test_openram_profile_defaults_to_strict_flat_comparison(self):
        with patch.object(opensource, "_run_klayout", self.fake_klayout):
            matched = opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "openram-macro")
        self.assertEqual(matched[0:2], (True, "matched"))
        self.assertIn("strict_topology=true", self.calls[0])
        self.assertIn("no_well_ties=true", self.calls[0])
        self.assertNotIn("strict_hierarchy=true", self.calls[0])

    def test_hierarchical_mode_is_opt_in_on_strict_profile(self):
        with patch.object(opensource, "_run_klayout", self.fake_klayout):
            matched = opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "openram-macro", "hierarchical")
        self.assertEqual(matched[0:2], (True, "matched"))
        self.assertIn("strict_topology=true", self.calls[0])
        self.assertIn("no_well_ties=true", self.calls[0])
        self.assertIn("strict_hierarchy=true", self.calls[0])

    def test_generic_profile_rejects_hierarchy_before_tool_launch(self):
        with self.assertRaises(ValueError):
            opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "generic", "hierarchical")
        self.assertEqual(self.calls, [])

    def test_hierarchy_mapping_failure_is_not_reported_as_mismatch(self):
        def inconclusive_klayout(_args, _librelane_root, log_path):
            Path(log_path).write_text("ICS55_LVS_COMPARE=INCONCLUSIVE\n")
            return 0, "ICS55_LVS_COMPARE=INCONCLUSIVE"

        with patch.object(opensource, "_run_klayout", inconclusive_klayout):
            matched = opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "openram-macro", "hierarchical")
        self.assertEqual(matched[0:2], (False, "inconclusive"))

    def test_strict_hierarchy_source_retains_boundary_and_device_invariants(self):
        if not STRICT_DECK.is_file():
            self.skipTest("adjacent ICsprout55 PDK checkout is unavailable")
        deck = STRICT_DECK.read_text()
        for invariant in (
                "COMBINE_DEVICES = STRICT_TOPOLOGY || bool_check?($combine_devices)",
                "PURGE = !STRICT_TOPOLOGY && bool_check?($purge)",
                "SIMPLIFY = !STRICT_TOPOLOGY && !bool_check?($no_simplify)",
                "absolute_tolerance = STRICT_TOPOLOGY ? 1e-9 : 0.0",
                "width_relative_tolerance = STRICT_TOPOLOGY ? 0.0 : 0.10",
                "layout_by_name.keys.sort == reference_by_name.keys.sort",
                "comparer.same_nets(layout_net, reference_net, true)",
                "layout_names == reference_names",
                "layout_pins.keys.sort == reference_pins.keys.sort",
                "schematic.flatten unless NET_ONLY || STRICT_HIERARCHY",
                "align unless NET_ONLY || STRICT_TOPOLOGY",
                "strict_hierarchy requires strict_topology=true",
                "validate_spice_circuit_definitions(schematic_source) if STRICT_HIERARCHY",
                "RBA::DeviceClassMOS4Transistor::PARAM_L",
                "RBA::DeviceClassMOS4Transistor::PARAM_W",
                "%w[NM1P2_SVT_LP NM1P2_LVT_LP NM1P2_HVT_LP PM1P2_SVT_LP PM1P2_LVT_LP PM1P2_HVT_LP NM3P3_LP PM3P3_LP]"):
            self.assertIn(invariant, deck)

    def test_unknown_profile_is_rejected_before_tool_launch(self):
        with self.assertRaises(ValueError):
            opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "unexpected")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
