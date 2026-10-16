"""Regression coverage for source-free MOS topology and emitted RCC wire checks."""
import tempfile
import unittest
from pathlib import Path

from gds_pex_topology import (
    _normalize_physical_parallel,
    check_rc_ideal_wire_invariant,
    compare_source_netlist,
)


class GdsTopologyTests(unittest.TestCase):
    @staticmethod
    def geometry():
        return {
            "schema_version": 1,
            "design": "tiny",
            "input_gds_sha256": "a" * 64,
            "ports": ["gnd", "in", "out", "vdd"],
            "nets": [
                {"name": "gnd", "anchors": [{"name": "gnd"}]},
                {"name": "in", "anchors": [{"name": "in"}]},
                {"name": "out", "anchors": [{"name": "out"}]},
                {"name": "vdd", "anchors": [{"name": "vdd"}]},
            ],
            "devices": [{
                "name": "M1",
                "model": "nm1p2_svt_lp",
                "w_um": 0.15,
                "l_um": 0.06,
                "as_um2": 0.0,
                "ad_um2": 0.0,
                "ps_um": 0.0,
                "pd_um": 0.0,
                "terminals": {"D": "out", "G": "in", "S": "gnd", "B": "gnd"},
                "terminal_components": {"D": "feol:N_SD:1", "G": "feol:POLY_CON:1",
                                         "S": "feol:N_SD:0", "B": "feol:SUBSTRATE"},
                "active_group": "n:0",
                "origin_um": {"x": 1.0, "y": 2.0},
            }],
        }

    def test_parallel_normalization_requires_identical_logical_terminals(self):
        geometry = self.geometry()
        reverse = dict(geometry["devices"][0])
        reverse["name"] = "M2"
        reverse["terminals"] = {"D": "gnd", "G": "in", "S": "out", "B": "gnd"}
        nonparallel = dict(geometry["devices"][0])
        nonparallel["name"] = "M3"
        nonparallel["terminals"] = {"D": "vdd", "G": "in", "S": "gnd", "B": "gnd"}
        geometry["devices"].extend([reverse, nonparallel])

        normalized, groups = _normalize_physical_parallel(geometry)

        self.assertEqual(len(normalized["devices"]), 2)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["members"], ["M1", "M2"])
        self.assertEqual(groups[0]["normalized_width_um"], 0.3)
        self.assertEqual(normalized["devices"][1]["terminals"]["D"], "vdd")

    @staticmethod
    def source(reverse_sd=False, body="gnd"):
        drain, source = ("gnd", "out") if reverse_sd else ("out", "gnd")
        return ".SUBCKT tiny in out gnd vdd\nMref %s in %s %s nm1p2_svt_lp w=0.15u l=0.06u\n.ENDS tiny\n" % (
            drain, source, body)

    @staticmethod
    def rcc(short=False, gate_wire=True):
        lines = [
            ".subckt tiny gnd in out vdd",
            "XM1 __D __G gnd gnd nm1p2_svt_lp w=0.15u l=0.06u nf=1 mismod=0",
            "R_D __D out 2",
        ]
        if gate_wire:
            lines.append("R_G __G in 3")
        if short:
            lines.append("R_SHORT out in 1")
        lines.extend(["C_IGNORED __D gnd 1e-12", ".ends tiny", ""])
        return "\n".join(lines)

    def test_default_mos4_symmetry_matches_and_maps_swapped_source_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reference.sp"
            source.write_text(self.source(reverse_sd=True))

            result = compare_source_netlist(self.geometry(), source)

        self.assertTrue(result["reference_equivalent"])
        self.assertEqual(result["comparison"]["matched_devices"], 1)
        self.assertEqual(result["comparison"]["matched_ports"], 4)
        self.assertEqual(result["ambiguous_nets"], {})
        self.assertTrue(result["nets"]["OUT"]["usable_exact_identity"])
        device_map = next(iter(result["devices"].values()))
        self.assertEqual(device_map["match_status"], "Match")
        self.assertEqual(device_map["terminal_match_statuses"],
                         {"D": "Match", "G": "Match", "S": "Match", "B": "Match"})
        self.assertTrue(device_map["usable_exact_identity"])
        self.assertEqual(device_map["gds_device"], "M1")
        self.assertEqual(device_map["terminals"]["D"], "gnd")
        self.assertEqual(device_map["terminals"]["S"], "out")

    def test_parallel_device_map_includes_all_raw_default_symmetry_members(self):
        geometry = self.geometry()
        reverse = dict(geometry["devices"][0])
        reverse["name"] = "M2"
        reverse["terminals"] = {"D": "gnd", "G": "in", "S": "out", "B": "gnd"}
        geometry["devices"].append(reverse)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reference.sp"
            source.write_text(self.source().replace("w=0.15u", "w=0.3u"))

            result = compare_source_netlist(geometry, source)

        self.assertTrue(result["reference_equivalent"])
        device_map = next(iter(result["devices"].values()))
        self.assertIsNone(device_map["gds_device"])
        self.assertEqual(device_map["gds_members"], ["M1", "M2"])
        self.assertEqual(device_map["terminals"]["D"], "out")
        self.assertEqual(device_map["terminals"]["S"], "gnd")
        self.assertEqual(device_map["member_terminal_nodes"]["D"],
                         {"M1": ["out"], "M2": ["out"]})

    def test_symmetric_device_match_does_not_assert_unique_terminal_identity(self):
        geometry = self.geometry()
        geometry["ports"] = ["in", "gnd"]
        geometry["nets"] = [{"name": name, "anchors": [{"name": name}]}
                            for name in ("in", "gnd", "a", "b")]
        geometry["devices"][0]["terminals"]["D"] = "a"
        other = dict(geometry["devices"][0], name="M2")
        other["terminals"] = dict(other["terminals"], D="b")
        geometry["devices"].append(other)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reference.sp"
            source.write_text(
                ".SUBCKT tiny in gnd\n"
                "MMa alpha in gnd gnd nm1p2_svt_lp w=.15u l=.06u\n"
                "MMb beta in gnd gnd nm1p2_svt_lp w=.15u l=.06u\n"
                ".ENDS tiny\n")
            result = compare_source_netlist(geometry, source)

        self.assertTrue(result["reference_equivalent"])
        self.assertEqual(result["comparison"]["matched_devices"], 2)
        self.assertFalse(any(record["usable_exact_identity"]
                             for record in result["devices"].values()))
        self.assertFalse(any(name in result["nets"] for name in ("ALPHA", "BETA")))

    def test_bulk_mismatch_is_not_repaired_by_supply_aliasing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reference.sp"
            source.write_text(self.source(body="vdd"))

            result = compare_source_netlist(self.geometry(), source)

        self.assertFalse(result["reference_equivalent"])

    def test_declared_unused_port_rename_is_not_pruned_into_equivalence(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reference.sp"
            source.write_text(self.source().replace(
                "tiny in out gnd vdd", "tiny in out gnd unused_vdd"))

            result = compare_source_netlist(self.geometry(), source)

        self.assertFalse(result["reference_equivalent"])

    def test_invariant_a_unions_emitted_resistors_and_ignores_capacitors(self):
        with tempfile.TemporaryDirectory() as directory:
            emitted = Path(directory) / "rcc.sp"
            emitted.write_text(self.rcc())

            result = check_rc_ideal_wire_invariant(self.geometry(), emitted)

        self.assertTrue(result["equivalent"])
        self.assertEqual(result["resistor_count"], 2)
        self.assertEqual(result["capacitor_count_ignored"], 1)
        self.assertIn("__D", result["nodes_by_logical_net"]["out"])
        self.assertIn("out", result["nodes_by_logical_net"]["out"])

    def test_invariant_a_reports_resistor_shorts_and_missing_wires(self):
        with tempfile.TemporaryDirectory() as directory:
            emitted = Path(directory) / "short.sp"
            emitted.write_text(self.rcc(short=True))
            short = check_rc_ideal_wire_invariant(self.geometry(), emitted)
            emitted.write_text(self.rcc(gate_wire=False))
            disconnected = check_rc_ideal_wire_invariant(self.geometry(), emitted)

        self.assertFalse(short["equivalent"])
        self.assertTrue(short["shorted_logical_nets"])
        self.assertFalse(disconnected["equivalent"])
        self.assertTrue(disconnected["disconnected_logical_nets"])


if __name__ == "__main__":
    unittest.main()
