"""Native LVS coverage regressions without private PDK or EDA subprocesses."""
from pathlib import Path
import tempfile
import unittest

from lvs_report import inspect_report


FIXTURES = Path(__file__).with_name("fixtures")


class NativeCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.report = self.root / "native.lvsdb"
        self.reference = self.root / "source.sp"
        self.report.write_text((FIXTURES / "strict_two_mos.lvsdb").read_text())
        self.reference.write_text((FIXTURES / "strict_two_mos.sp").read_text())

    def inspect(self):
        return inspect_report(self.report, "body_probe", "flat", self.reference)

    def change_reference_graph(self, before, after):
        layout, marker, reference = self.report.read_text().partition("\nH(\n")
        self.report.write_text(layout + marker + reference.replace(before, after, 1))

    def test_complete_native_mapping_covers_devices_ports_and_all_incidences(self):
        result = self.inspect()
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual(result["root_status"], "Match")
        for category, total in (("devices", 2), ("nets", 6), ("pins", 6), ("device_terminals", 8)):
            self.assertEqual(result["counts"][category], dict(layout=total, reference=total, paired=total))
        self.assertEqual(result["unconnected_nets"], dict(layout=["body_probe:SUBSTRATE"], reference=[]))

    def test_unattempted_native_root_is_not_complete(self):
        self.report.write_text((FIXTURES / "strict_skipped_root.lvsdb").read_text())
        result = self.inspect()
        self.assertEqual(result["root_status"], "Skipped")
        self.assertFalse(result["complete"])

    def test_missing_device_correspondence_cannot_hide_behind_root_match(self):
        self.report.write_text(self.report.read_text().replace("   D(2 2 1)\n", "", 1))
        result = self.inspect()
        self.assertEqual(result["root_status"], "Match")
        self.assertFalse(result["complete"])

    def test_equal_counts_with_repeated_reference_device_are_not_bijective(self):
        self.report.write_text(self.report.read_text().replace("D(2 2 1)", "D(2 1 1)", 1))
        result = self.inspect()
        self.assertEqual(result["counts"]["devices"], dict(layout=2, reference=2, paired=2))
        self.assertFalse(result["complete"])

    def test_original_reference_boundary_is_not_reconstructed_from_report(self):
        self.reference.write_text(self.reference.read_text().replace("out_n out_p in_n in_p", "out_n in_n in_p", 1))
        result = self.inspect()
        self.assertEqual(result["root_status"], "Match")
        self.assertFalse(result["complete"])

    def test_claimed_native_match_does_not_waive_exact_width(self):
        self.change_reference_graph("E(W 20)", "E(W 20.1)")
        result = self.inspect()
        self.assertEqual(result["root_status"], "Match")
        self.assertFalse(result["complete"])

    def test_gate_and_body_connections_are_fixed(self):
        original = self.report.read_text()
        for before, after in (("T(G 5)", "T(G 3)"), ("T(B 1)", "T(B 2)")):
            with self.subTest(terminal=before):
                self.report.write_text(original)
                self.change_reference_graph(before, after)
                result = self.inspect()
                self.assertEqual(result["root_status"], "Match")
                self.assertFalse(result["complete"])

    def test_source_drain_symmetry_remains_electrically_valid(self):
        self.change_reference_graph("T(S 1)", "T(S 3)")
        self.change_reference_graph("T(D 3)", "T(D 1)")
        result = self.inspect()
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual(result["counts"]["device_terminals"], dict(layout=8, reference=8, paired=8))

    def test_unused_global_is_excluded_only_while_it_has_zero_incidence(self):
        layout, marker, rest = self.report.read_text().partition("\nH(\n")
        self.report.write_text(layout.replace("T(B 2)", "T(B 7)", 1) + marker + rest)
        result = self.inspect()
        self.assertEqual(result["counts"]["nets"]["layout"], 7)
        self.assertEqual(result["unconnected_nets"]["layout"], [])
        self.assertFalse(result["complete"])


if __name__ == "__main__":
    unittest.main()
