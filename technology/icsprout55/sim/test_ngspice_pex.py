"""Behavioral regressions for measured versus unavailable PEX access times."""
import sys
from pathlib import Path
import tempfile
import unittest

SIM = Path(__file__).resolve().parent
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))

import ngspice_pex
from pex_debug import readwrite_target


class AccessTimeTest(unittest.TestCase):
    def check_wave(self, samples, expected="0"):
        with tempfile.TemporaryDirectory() as directory:
            wave = Path(directory) / "waves.dat"
            wave.write_text("time v(out)\n" + "".join(
                "{:.15g} {:.15g}\n".format(ns * 1e-9, value)
                for ns, value in samples), encoding="utf-8")
            return ngspice_pex.check_waveforms(
                wave, ["dout0[0]"], {0: {"dout0": expected}}, 2.5, 1.0, 0)

    def test_stuck_high_is_failure_not_zero_delay(self):
        result = self.check_wave([(0, 1), (5.5, 1)])
        self.assertFalse(result["passed"])
        self.assertIsNone(result["tacc_ns"])
        self.assertIsNone(result["observed_transition_delay_ns"])
        self.assertEqual(result["access_time_status"], "functional_failure")
        self.assertEqual(result["read_measurements"][0]["status"], "no_output_transition")
        self.assertEqual(result["failures"][0]["expected"], 0)

    def test_failed_read_with_crossing_has_no_valid_access_time(self):
        result = self.check_wave([(0, 0), (2.5, 0), (3, 0), (3.2, 1), (5.5, 1)])
        self.assertFalse(result["passed"])
        self.assertIsNone(result["tacc_ns"])
        self.assertAlmostEqual(result["observed_transition_delay_ns"], 0.6)
        self.assertEqual(result["read_measurements"][0]["status"], "invalid_read")

    def test_correct_stable_output_is_not_a_measured_zero_delay(self):
        result = self.check_wave([(0, 1), (5.5, 1)], expected="1")
        self.assertTrue(result["passed"])
        self.assertIsNone(result["tacc_ns"])
        self.assertEqual(result["access_time_status"], "already_at_expected_level")

    def test_successful_transition_measures_clock_to_output(self):
        result = self.check_wave([(0, 1), (2.5, 1), (3, 1), (3.2, 0), (5.5, 0)])
        self.assertTrue(result["passed"])
        self.assertEqual(result["access_time_status"], "measured")
        self.assertAlmostEqual(result["tacc_ns"], 0.6)

    def test_settling_without_midpoint_crossing_is_not_measured(self):
        result = self.check_wave([(0, 0.7), (2.5, 0.7), (3.2, 1), (5.5, 1)], expected="1")
        self.assertTrue(result["passed"])
        self.assertIsNone(result["tacc_ns"])
        self.assertEqual(result["access_time_status"], "no_output_transition")


class ProbeSampleTest(unittest.TestCase):
    def test_each_read_port_samples_its_own_skewed_event(self):
        targets = [dict(read_port=port, write_port=port, read_cycle=1, write_cycle=0)
                   for port in (0, 1)]
        profile = dict(profile="debug-readwrite", targets=targets,
                       vectors=["v(out0)", "v(out1)", "v(q)"],
                       signals=[dict(name="q", stage="storage", column=2)])
        with tempfile.TemporaryDirectory() as directory:
            wave = Path(directory) / "waves.dat"
            wave.write_text(
                "time v(out0) v(out1) v(q)\n"
                "0 0 0 0\n7.49e-9 0 0 0\n7.51e-9 0 0 1\n8.5e-9 0 0 1\n",
                encoding="utf-8")
            result = ngspice_pex.check_waveforms(wave, ["dout0[0]", "dout1[0]"],
                                                 {1: {"dout0": "0", "dout1": "0"}},
                                                 2.5, 1.0, 0.25, profile)
        sampled = {sample["target"]["read_port"]: sample["stages"]["storage"]["q"]
                   for sample in result["probe_samples"] if sample["phase"] == "read_sample"}
        self.assertLess(sampled[0], 0.1)
        self.assertGreater(sampled[1], 0.9)


class ReadwriteTargetTest(unittest.TestCase):
    def target(self, text, failures=None, read_port=None):
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "trace.txt"
            trace.write_text(text, encoding="utf-8")
            return readwrite_target(trace, failures, read_port=read_port)

    def test_cross_port_write_is_the_read_provenance(self):
        result = self.target(
            "C 0 csb0=1 web0=1 addr0=00 din0=00 csb1=0 web1=0 addr1=01 din1=10\n"
            "C 1 csb0=0 web0=1 addr0=01 din0=00 csb1=1 web1=1 addr1=00 din1=00\n"
            "E 1 dout0=10\n")
        self.assertEqual((result["read_port"], result["write_port"]), (0, 1))
        self.assertEqual((result["read_cycle"], result["write_cycle"]), (1, 0))
        self.assertEqual((result["address"], result["bit"], result["expected"]), (1, 0, 0))

    def test_failure_selects_the_reported_bit_not_the_first_zero(self):
        result = self.target(
            "C 0 csb0=0 web0=0 addr0=00 din0=00\n"
            "C 1 csb0=0 web0=1 addr0=00 din0=00\nE 1 dout0=00\n",
            [{"cycle": 1, "pin": "dout0[1]", "expected": 0}])
        self.assertEqual(result["bit"], 1)
        self.assertEqual(result["selection"], "first_reported_failure")

    def test_port_selection_keeps_an_all_ones_read(self):
        result = self.target(
            "C 0 csb0=0 web0=0 addr0=00 din0=0 csb1=0 web1=0 addr1=01 din1=1\n"
            "C 1 csb0=0 web0=1 addr0=00 din0=0 csb1=0 web1=1 addr1=01 din1=0\n"
            "E 1 dout0=0\nE 1 dout1=1\n", read_port=1)
        self.assertEqual((result["read_port"], result["write_port"]), (1, 1))
        self.assertEqual((result["address"], result["bit"], result["expected"]), (1, 0, 1))

    def test_each_port_selects_its_own_reported_failure(self):
        result = self.target(
            "C 0 csb0=0 web0=0 addr0=00 din0=00 csb1=0 web1=0 addr1=01 din1=00\n"
            "C 1 csb0=0 web0=1 addr0=00 din0=00 csb1=0 web1=1 addr1=01 din1=00\n"
            "E 1 dout0=00\nE 1 dout1=00\n",
            [{"cycle": 1, "pin": "dout0[0]", "expected": 0},
             {"cycle": 1, "pin": "dout1[1]", "expected": 0}], read_port=1)
        self.assertEqual((result["read_port"], result["bit"], result["write_port"]), (1, 1, 1))

    def test_simultaneous_same_address_writes_are_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "ambiguous simultaneous writes"):
            self.target(
                "C 0 csb0=0 web0=0 addr0=00 din0=0 csb1=0 web1=0 addr1=00 din1=1\n"
                "C 1 csb0=0 web0=1 addr0=00 din0=0 csb1=1 web1=1 addr1=00 din1=0\n"
                "E 1 dout0=0\n")

    def test_last_write_must_agree_with_expected_data(self):
        with self.assertRaisesRegex(ValueError, "disagrees"):
            self.target(
                "C 0 csb0=0 web0=0 addr0=00 din0=0\n"
                "C 1 csb0=0 web0=0 addr0=00 din0=1\n"
                "C 2 csb0=0 web0=1 addr0=00 din0=0\nE 2 dout0=0\n")


if __name__ == "__main__":
    unittest.main()
