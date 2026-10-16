"""Regression tests for the generic/OpenRAM KLayout LVS profile boundary."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import opensource


class LvsProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.pdk = root / "pdk"
        (self.pdk / "libs.tech/lvs").mkdir(parents=True)
        (self.pdk / "libs.tech/lvs/ics55.lvs").write_text("# test deck\n")
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

    def test_openram_profile_requests_strict_physical_boundary(self):
        with patch.object(opensource, "_run_klayout", self.fake_klayout):
            matched = opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "openram-macro")
        self.assertEqual(matched[0:2], (True, "matched"))
        self.assertIn("strict_topology=true", self.calls[0])
        self.assertIn("no_well_ties=true", self.calls[0])

    def test_unknown_profile_is_rejected_before_tool_launch(self):
        with self.assertRaises(ValueError):
            opensource._run_lvs(
                self.gds, "TOP", self.netlist, self.pdk, self.pdk,
                self.out_dir, "unexpected")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
