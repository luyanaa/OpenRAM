"""Consumer tests for generated-netlist and generated-layout efficiency metrics."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SIM = Path(__file__).resolve().parent
ROOT = SIM.parents[2]
for path in (SIM, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from efficiency import (MetricUnavailable, actual_size_metrics,
                        generated_layout_metrics, total_hierarchical_mos_gate_width_um)
from optimizer import main as optimizer_main


class FakeBox:
    def __init__(self, area):
        self._area = area

    def area(self):
        return self._area


class FakeCell:
    def __init__(self, name, index, area, instances=()):
        self.name = name
        self._index = index
        self._area = area
        self._instances = list(instances)

    def cell_index(self):
        return self._index

    def bbox(self):
        return FakeBox(self._area)

    def each_inst(self):
        return iter(self._instances)


class FakeInstance:
    def __init__(self, cell, *, na=1, nb=1, array=False):
        self.cell = cell
        self._na = na
        self._nb = nb
        self._array = array

    def is_regular_array(self):
        return self._array

    def na(self):
        return self._na

    def nb(self):
        return self._nb


def fake_klayout(layout_for_path):
    package = types.ModuleType("klayout")
    db = types.ModuleType("klayout.db")

    class Layout:
        dbu = 0.1

        def read(self, path):
            if not Path(path).is_file():
                raise OSError("generated GDS is missing")
            self.top = layout_for_path(path)

        def cell(self, name):
            return self.top if name == self.top.name else None

    db.Layout = Layout
    package.db = db
    return package, db


def netlist(width):
    return (".subckt macro A Z vdd gnd\n"
            "XM1 Z A gnd gnd nm1p2_svt_lp w={} l=60n nf=1\n"
            ".ends macro\n".format(width))


class NetlistMetricsTest(unittest.TestCase):
    def test_hierarchical_parameter_expansion_counts_each_instantiated_mos(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.sp"
            path.write_text(
                ".subckt leaf A Z vdd gnd\n"
                "XM1 Z A gnd gnd nm1p2_svt_lp w={w} nf=2 l=60n\n"
                "M2 Z A vdd vdd pmos w={w/2} l=60n\n"
                ".ends leaf\n"
                ".subckt macro A Z vdd gnd\n"
                "XU0 A Z vdd gnd leaf w=0.2u\n"
                "XU1 A Z vdd gnd leaf w=0.2u\n"
                "XM3 Z A gnd gnd nm1p2_svt_lp w=1u m=2 l=60n\n"
                ".ends macro\n")
            self.assertAlmostEqual(total_hierarchical_mos_gate_width_um(path, "macro"), 3.0)

    def test_foundry_mr_multiplicity_and_defaults_are_counted_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.sp"
            path.write_text(
                ".subckt leaf A Z vdd gnd mr=2\n"
                "XM1 Z A gnd gnd nm1p2_svt_lp w=0.3u nf=1 l=60n\n"
                ".ends leaf\n"
                ".subckt macro A Z vdd gnd\n"
                "XM0 Z A gnd gnd pm1p2_svt_lp w=0.72u mr=5 nf=1 l=60n\n"
                "XM2 Z A gnd gnd pm1p2_svt_lp w=0.2u m=2 mr=2 nf=1 l=60n\n"
                "XU0 A Z vdd gnd leaf\n"
                ".ends macro\n")
            # 0.72 * 5 + 0.2 * 2 (one multiplicity, not squared) + 0.3 * 2 (subckt default)
            self.assertAlmostEqual(total_hierarchical_mos_gate_width_um(path, "macro"), 4.6)

    def test_conflicting_m_and_mr_multiplicity_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.sp"
            path.write_text(
                ".subckt macro A Z vdd gnd\n"
                "XM1 Z A gnd gnd nm1p2_svt_lp w=0.2u m=2 mr=3 l=60n\n"
                ".ends macro\n")
            with self.assertRaisesRegex(MetricUnavailable, "conflicting m and mr"):
                total_hierarchical_mos_gate_width_um(path, "macro")

    def test_unresolved_hierarchy_never_returns_a_partial_gate_width(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.sp"
            path.write_text(
                ".subckt macro A Z vdd gnd\n"
                "XM1 Z A gnd gnd nm1p2_svt_lp w=0.2u l=60n\n"
                "XUNKNOWN A Z vdd gnd missing_cell\n"
                ".ends macro\n")
            with self.assertRaisesRegex(MetricUnavailable, "unresolved hierarchical instance"):
                total_hierarchical_mos_gate_width_um(path, "macro")


class LayoutMetricTest(unittest.TestCase):
    def make_layout(self, driver_cell_area):
        driver = FakeCell("ics55_write_driver0", 3, driver_cell_area)
        array = FakeCell("write_driver_array", 2, 400,
                         [FakeInstance(driver, na=2, nb=1, array=True)])
        return FakeCell("macro", 1, 10_000, [FakeInstance(array)])

    def test_actual_netlist_and_named_layout_area_discriminate_without_bbox_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = []
            for tag, width, driver_cell_area in (("wide", "0.60u", 100),
                                                  ("narrow", "0.45u", 150)):
                spice = root / (tag + ".sp")
                gds = root / (tag + ".gds")
                spice.write_text(netlist(width))
                gds.write_bytes(b"generated GDS identity placeholder")
                observations.append((spice, gds, driver_cell_area))
            package, db = fake_klayout(
                lambda path: self.make_layout(100 if path.endswith("wide.gds") else 150))
            with patch.dict(sys.modules, {"klayout": package, "klayout.db": db}):
                wide_spice, wide_gds, _ = observations[0]
                narrow_spice, narrow_gds, _ = observations[1]
                wide = actual_size_metrics(wide_spice, wide_gds, "macro",
                                           {"num_words": 32, "word_size": 8})
                narrow = actual_size_metrics(narrow_spice, narrow_gds, "macro",
                                             {"num_words": 32, "word_size": 8})
            self.assertAlmostEqual(wide["total_mos_gate_width_um"], 0.60)
            self.assertAlmostEqual(narrow["total_mos_gate_width_um"], 0.45)
            self.assertEqual(wide["macro_bbox_area_um2"], narrow["macro_bbox_area_um2"])
            self.assertEqual(wide["macro_bbox_area_um2"], 100.0)
            self.assertAlmostEqual(wide["effective_bit_area_um2"], 100.0 / 256)
            self.assertAlmostEqual(narrow["effective_bit_area_um2"], 100.0 / 256)
            self.assertAlmostEqual(wide["buffer_driver_cell_area_um2"], 2.0)
            self.assertAlmostEqual(narrow["buffer_driver_cell_area_um2"], 3.0)
            self.assertIn("ics55_write_driver0", wide["details"]["named_buffer_driver_cells"])
            self.assertIn("not silicon area", wide["details"]["gate_width_interpretation"])

    def test_missing_named_cells_leave_bbox_measured_and_cell_area_unknown(self):
        top = FakeCell("macro", 1, 10_000, [FakeInstance(FakeCell("logic", 2, 500))])
        package, db = fake_klayout(lambda path: top)
        with tempfile.TemporaryDirectory() as directory:
            gds = Path(directory) / "macro.gds"
            gds.write_bytes(b"layout evidence")
            with patch.dict(sys.modules, {"klayout": package, "klayout.db": db}):
                metrics = generated_layout_metrics(gds, "macro",
                                                   {"num_words": 32, "word_size": 8})
        self.assertEqual(metrics["macro_bbox_area_um2"], 100.0)
        self.assertIsNone(metrics["buffer_driver_cell_area_um2"])
        self.assertIn("no identifiable named", metrics["buffer_driver_cell_area_reason"])


    def test_missing_netlist_or_layout_preserves_independent_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spice = root / "macro.sp"
            gds = root / "macro.gds"
            spice.write_text(netlist("0.45u"))
            gds.write_bytes(b"layout evidence")
            package, db = fake_klayout(lambda path: FakeCell("macro", 1, 10_000))
            with patch.dict(sys.modules, {"klayout": package, "klayout.db": db}):
                missing_netlist = actual_size_metrics(
                    root / "missing.sp", gds, "macro", {"num_words": 32, "word_size": 8})
                missing_layout = actual_size_metrics(
                    spice, root / "missing.gds", "macro", {"num_words": 32, "word_size": 8})
        self.assertIsNone(missing_netlist["total_mos_gate_width_um"])
        self.assertEqual(missing_netlist["macro_bbox_area_um2"], 100.0)
        self.assertAlmostEqual(missing_layout["total_mos_gate_width_um"], 0.45)
        self.assertIsNone(missing_layout["macro_bbox_area_um2"])


class ProductionReportTest(unittest.TestCase):
    def test_optimizer_cli_analysis_hash_checks_and_consumes_report_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spice = root / "generated.sp"
            gds = root / "generated.gds"
            extraction = root / "extraction.json"
            spice.write_text(netlist("0.45u"))
            gds.write_bytes(b"actual netlist-associated layout artifact")
            extraction.write_text(json.dumps({"design": "macro"}))
            references = {}
            for name, path in (("netlist", spice), ("gds", gds), ("extraction", extraction)):
                references[name] = {"path": str(path),
                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            environment = dict(corner="snfp", voltage_v=1.08, temperature_c=125,
                               period_ns=7.5, cload_ff=5, slew_ns=0.03, skew1=0,
                               initialization="uninitialized", fidelity="F3")
            candidate = dict(schema="openram-candidate/1", capability_id="ics55.test",
                             family="sram", backend="generated", access_contract={
                                 "ports": ["RW"], "clocking": "common", "same_row_write": "single",
                                 "read_during_write": "defined"},
                             parameters={"num_words": 32, "word_size": 8,
                                         "delay_chain_stages": 9, "delay_chain_fanout": 4,
                                         "write_driver_strength": 0.75},
                             environment=environment)
            measurements = []
            for name, value, unit, operation in (
                    ("wen_pulse_ns", 0.11, "ns", "write"),
                    ("sa_setup_ns", 0.02, "ns", "read"),
                    ("bl_diff_v", 0.06, "V", "read"),
                    ("write_recovery_ns", 0.02, "ns", "write")):
                measurements.append({"name": name, "value": value, "unit": unit,
                                     "operation": operation, "port": None})
            request = {"schema": "openram-search/1", "capability_id": "ics55.test",
                       "parameters": candidate["parameters"], "environment": environment,
                       "search": {"environment.period_ns": [7.5]},
                       "objectives": ["cycle_time_ns"],
                       "required_margins": {"wen_pulse_ns": 0.1, "sa_setup_ns": 0,
                                             "bl_diff_v": 0.05, "write_recovery_ns": 0.01}}
            report = {"schema": "openram-search-report/1", "request": request,
                      "candidates": [{"id": "generated-candidate", "candidate": candidate,
                                      "status": "evaluated", "evaluation": {
                                          "checks": {"functional": True},
                                          "measurements": measurements,
                                          "artifacts": references}}]}
            report_path = root / "search-report.json"
            report_path.write_text(json.dumps(report))
            output_path = root / "efficiency-analysis.json"
            package, db = fake_klayout(lambda path: self._layout())
            with patch.dict(sys.modules, {"klayout": package, "klayout.db": db}):
                optimizer_main([str(report_path), "--out", str(output_path)])
            result = json.loads(output_path.read_text())
            candidate_result = result["candidates"][0]
            self.assertEqual(result["sampled_period_feasibility"]["sets"][0]["feasible_samples_ns"],
                             [7.5])
            self.assertAlmostEqual(candidate_result["metrics"]["total_mos_gate_width_um"]["value"], 0.45)
            self.assertEqual(candidate_result["metrics"]["macro_bbox_area_um2"]["value"], 100.0)
            self.assertEqual(candidate_result["metrics"]["buffer_driver_cell_area_um2"]["value"], 2.0)
            self.assertIsNone(candidate_result["metrics"]["read_energy_pj"]["value"])
            self.assertIsNone(candidate_result["metrics"]["leakage_power_uw"]["value"])

    @staticmethod
    def _layout():
        driver = FakeCell("write_driver", 3, 100)
        array = FakeCell("write_driver_array", 2, 400,
                         [FakeInstance(driver, na=2, nb=1, array=True)])
        return FakeCell("macro", 1, 10_000, [FakeInstance(array)])


if __name__ == "__main__":
    unittest.main()
