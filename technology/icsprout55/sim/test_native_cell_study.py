"""Deterministic extraction and geometry-boundary tests for the native cell study."""
import math
import tempfile
import unittest
from pathlib import Path

from technology.icsprout55.sim import native_cell_study as study


class NativeCellStudyMathTest(unittest.TestCase):
    def test_linf_square_uses_the_largest_boundary_clearance(self):
        result = study.max_inscribed_square_linf(
            [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)])
        self.assertAlmostEqual(result["side_v"], 2.0)
        self.assertAlmostEqual(result["center_uv_v"][1], 1.0)

    def test_high_gain_symmetric_inverter_has_half_supply_snm_limit(self):
        epsilon = 0.0001
        inputs = [0.0, 0.49, 0.5, 0.51, 1.0]
        outputs = [1.0, 1.0 - epsilon * 0.49, 0.5, epsilon * 0.49, 0.0]
        q_curve = list(zip(outputs, inputs))
        qb_curve = list(zip(inputs, outputs))
        result = study.extract_butterfly_snm(q_curve, qb_curve, 1.0)
        for state in ("stable_state_q0_qb1", "stable_state_q1_qb0"):
            self.assertAlmostEqual(result[state]["side_v"], 0.49, delta=0.005)

    def test_butterfly_extraction_reports_both_stable_state_lobes(self):
        def inverter(value):
            return 1.0 - value * value

        inputs = [index / 100.0 for index in range(101)]
        q_curve = [(inverter(value), value) for value in inputs]
        qb_curve = [(value, inverter(value)) for value in inputs]
        result = study.extract_butterfly_snm(q_curve, qb_curve, 1.0)
        self.assertEqual(result["status"], "measured_from_dc_vtcs")
        self.assertGreater(result["stable_state_q0_qb1"]["side_v"], 0.0)
        self.assertGreater(result["stable_state_q1_qb0"]["side_v"], 0.0)

    def test_q_inverter_vtc_is_transposed_into_storage_axes(self):
        with tempfile.TemporaryDirectory() as directory:
            data_path = Path(directory) / "vtc.dat"
            data_path.write_text("sweep output\n0 1\n1 0\n", encoding="utf-8")
            case = {"deck_metadata": {"curve": "q"}, "data": str(data_path)}
            result = study._analyze_snm_curve(case)
        self.assertEqual(result["curve_points"], [(1.0, 0.0), (0.0, 1.0)])

    def test_wrdata_threshold_crossing_is_linearly_interpolated(self):
        crossing = study._crossing_time([0.0, 1.0], [0.0, 1.0], 0.5,
                                        0.0, 1.0, rising=True)
        self.assertAlmostEqual(crossing, 0.5)

    def test_wrdata_parser_rejects_non_finite_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "waves.dat"
            path.write_text("time v(q)\n0 0\n1 nan\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-finite"):
                study.parse_wrdata(path)

    def test_sampled_write_margin_does_not_hide_nonmonotonicity(self):
        result = study.summarize_write_sweep([0.1, 0.2, 0.3], [False, True, False])
        self.assertTrue(result["observed_nonmonotonicity"])
        self.assertIsNone(result["sampled_transition_bracket_ns"])
        self.assertEqual(result["interpolation_or_extrapolation"], "none")

    def test_monotonic_sampled_write_bracket_uses_observed_points_only(self):
        result = study.summarize_write_sweep([0.1, 0.2, 0.3], [False, False, True])
        self.assertFalse(result["observed_nonmonotonicity"])
        self.assertEqual(result["sampled_transition_bracket_ns"], {
            "largest_sampled_failure_ns": 0.2,
            "smallest_sampled_success_ns": 0.3,
        })


class NativeCellStudyGeometryTest(unittest.TestCase):
    def _pins(self):
        pins = {}
        for index, name in enumerate(study.EXPECTED_BL_PINS):
            x0 = 0.2 + index * 0.2
            pins[name] = [{"layer": "m2", "rect": [[x0, 0.0], [x0 + 0.1, 3.0]]}]
        for index, name in enumerate(study.EXPECTED_WL_PINS):
            y0 = 0.4 + index * 0.3
            pins[name] = [{"layer": "m1", "rect": [[0.0, y0], [2.0, y0 + 0.1]]}]
        pins["vdd"] = [{"layer": "m1", "rect": [[0.0, 2.955], [2.0, 3.045]]}]
        pins["gnd"] = [{"layer": "m1", "rect": [[0.0, -0.045], [2.0, 0.045]]}]
        return pins

    def test_layout_inventory_measures_access_pitch_without_signoff_claim(self):
        result = study.audit_layout_geometry(2.0, 3.0, self._pins())
        self.assertEqual(result["status"], "pin_access_geometry_observed")
        self.assertTrue(result["checks"]["bitlines_are_vertical_m2_full_height"])
        self.assertTrue(result["checks"]["wordlines_are_horizontal_m1_full_width"])
        self.assertTrue(result["checks"]["supply_rails_centered_on_bbox_edges"])
        self.assertAlmostEqual(result["supply_rail_straddles"]["gnd"][0]["straddle_um"], 0.045)
        self.assertEqual(len(result["adjacent_bitline_centerline_pitches_and_edge_gaps"]), 5)
        self.assertIn("geometry inventory is not a DRC result", result["limitations"])

    def test_layout_boundary_rejects_rails_that_are_not_abutted_full_width_tracks(self):
        cases = (("vdd", "m1", [[0.8, 2.955], [0.9, 3.045]]),
                 ("gnd", "m1", [[0.0, -0.5], [2.0, -0.41]]),
                 ("gnd", "m2", [[0.0, -0.045], [2.0, 0.045]]))
        for name, layer, rect in cases:
            pins = self._pins()
            pins[name] = [{"layer": layer, "rect": rect}]
            result = study.audit_layout_geometry(2.0, 3.0, pins)
            with self.subTest(name=name, layer=layer, rect=rect):
                self.assertEqual(result["status"], "pin_access_geometry_issue")
                self.assertFalse(result["checks"]["pin_shapes_inside_bbox"])
                self.assertFalse(result["checks"]["supply_rails_centered_on_bbox_edges"])

    def test_layout_boundary_flags_an_out_of_bbox_access_shape(self):
        pins = self._pins()
        pins["bl1"] = [{"layer": "m2", "rect": [[0.4, 0.0], [0.5, 3.1]]}]
        result = study.audit_layout_geometry(2.0, 3.0, pins)
        self.assertEqual(result["status"], "pin_access_geometry_issue")
        self.assertFalse(result["checks"]["pin_shapes_inside_bbox"])

    def test_layout_boundary_flags_overlapping_standalone_tracks(self):
        pins = self._pins()
        pins["bl1"] = [{"layer": "m2", "rect": [[0.4, 0.0], [0.7, 3.0]]}]
        bitline_result = study.audit_layout_geometry(2.0, 3.0, pins)
        self.assertEqual(bitline_result["status"], "pin_access_geometry_issue")
        self.assertFalse(bitline_result["checks"]["adjacent_bitlines_do_not_overlap_geometrically"])

        pins = self._pins()
        pins["wl1"] = [{"layer": "m1", "rect": [[0.0, 0.95], [2.0, 1.05]]}]
        wordline_result = study.audit_layout_geometry(2.0, 3.0, pins)
        self.assertEqual(wordline_result["status"], "pin_access_geometry_issue")
        self.assertFalse(wordline_result["checks"]["adjacent_wordlines_do_not_overlap_geometrically"])


class NativeCellStudyInputBoundaryTest(unittest.TestCase):
    def test_generated_cell_feedback_isolated_from_read_access_gate(self):
        netlist = """.subckt native_2r1w_pbitcell bl0 br0 bl1 br1 bl2 br2 wl0 wl1 wl2 vdd gnd
Xinv_nl Q Q_bar gnd gnd nmos_storage
Xinv_nr gnd Q Q_bar gnd nmos_storage
Xinv_pl Q Q_bar vdd vdd pmos_storage
Xinv_pr vdd Q Q_bar vdd pmos_storage
Xread_access RA Q_bar gnd gnd nmos_read
.ENDS native_2r1w_pbitcell
"""
        transformed, metadata = study.expose_storage_feedback(netlist)
        lines = transformed.splitlines()
        self.assertIn("q_drive qb_drive", lines[0])
        self.assertEqual(len(metadata["storage_q_inverter_instances"]), 2)
        self.assertEqual(len(metadata["storage_qb_inverter_instances"]), 2)
        read_access = next(line for line in lines if line.startswith("Xread_access"))
        self.assertIn("RA Q_bar gnd gnd", read_access)
        self.assertIn("qb_drive", "\n".join(lines))
        self.assertIn("q_drive", "\n".join(lines))

    def test_pvt_selection_is_explicit_and_rejects_unknown_sections(self):
        selected = study.select_pvt_points(["tt_1p2_25", "ss_1p08_125"])
        self.assertEqual(selected[0][1]["section"], "tt_mos")
        self.assertEqual(selected[1][1]["vdd_v"], 1.08)
        with self.assertRaisesRegex(ValueError, "unsupported PVT"):
            study.select_pvt_points(["typical_guess"])

    def test_monte_carlo_audit_does_not_treat_model_markers_as_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "models.lib"
            model.write_text(".lib mc_mos\n.param vth=agauss(0,1,3)\n", encoding="utf-8")
            result = study.audit_monte_carlo({str(model): "unused-hash"})
        self.assertEqual(result["status"], "statistical_models_present_but_unvalidated_for_ngspice")
        self.assertEqual(result["execution"], "not_run")
        self.assertIn("no Monte Carlo deck", result["reason"])


if __name__ == "__main__":
    unittest.main()
