# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""Consumer-visible verdicts from KLayout's native comparison records."""
import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location(
    "klayout_report", Path(__file__).resolve().parents[1] / "verify" / "report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class KLayoutReportTest(unittest.TestCase):
    def verdict(self, comparison):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.lvs.report"
            path.write_text(comparison)
            return report.lvs_verdict(path)

    def test_matched_symmetric_net_warning_is_not_a_mismatch(self):
        # W is the native MatchWithWarning status, seen for unnamed load nets.
        compared, matched, _ = self.verdict(
            "LVSDB\nZ(\n X(1 1 1\n  Z(\n   N(32 6 W)\n"
            "   P(1 1 1)\n   D(1 1 1)\n  )\n )\n)\n")
        self.assertTrue(compared)
        self.assertTrue(matched)

    def test_matched_net_warning_does_not_hide_device_parameter_failure(self):
        compared, matched, circuits = self.verdict(
            "LVSDB\nZ(\n X(1 1 1\n  Z(\n   N(32 6 W)\n"
            "   D(1 1 P)\n  )\n )\n)\n")
        self.assertTrue(compared)
        self.assertFalse(matched)
        self.assertEqual(circuits[0][3], [("D", "1", "1", "P")])

    def test_unperformed_comparison_is_not_a_match(self):
        compared, matched, _ = self.verdict("LVSDB\n")
        self.assertFalse(compared)
        self.assertFalse(matched)

    def test_malformed_correspondence_is_rejected(self):
        with self.assertRaises(report.ReportError):
            self.verdict("LVSDB\nZ(\n X(1 1 1\n  Z(\n   N(1 W)\n  )\n )\n)\n")


if __name__ == "__main__":
    import os
    import sys

    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo))
    os.environ.setdefault("OPENRAM_HOME", str(repo / "compiler"))
    os.environ.setdefault("OPENRAM_TECH", str(repo / "technology"))
    import common
    common.make_openram_package()
    import openram

    openram.parse_args()
    del sys.argv[1:]
    unittest.main()
