"""Pure boundary-stimulus and waveform-verdict regressions; no EDA is invoked."""
import os
import re
import sys
import tempfile
from pathlib import Path
import unittest

from technology.icsprout55.sim import timing_boundaries as boundaries


def base_candidate():
    return dict(environment=dict(period_ns=7.5, cload_ff=5, slew_ns=.03,
                                corner="tt", voltage_v=1.08, temperature_c=125,
                                skew1=0, initialization="init"),
                parameters=dict(num_words=16),
                access_contract=dict(ports=["RW", "R"]))


def interpolate(times, points):
    values = []
    for time in times:
        for (left, first), (right, second) in zip(points, points[1:]):
            if left <= time <= right:
                values.append(first + (second - first) * (time - left) / (right - left))
                break
    return values


class SimulatorGuardTest(unittest.TestCase):
    def test_memory_guard_kills_a_trial_that_exceeds_the_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            # Touch each OS page; zero-filled allocation can remain sparse.
            command = [sys.executable, "-c",
                       "import os, time\n"
                       "held = bytearray(300 * 1024 * 1024)\n"
                       "for page in range(0, len(held), os.sysconf('SC_PAGE_SIZE')):\n"
                       "    held[page] = 1\n"
                       "time.sleep(30)"]
            with self.assertRaisesRegex(ValueError, "memory guard"):
                boundaries._supervised_run(command, Path(directory), 60, 50)

    def test_timeout_kills_a_trial_that_exceeds_its_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-c", "import time; time.sleep(30)"]
            with self.assertRaisesRegex(ValueError, "timed out"):
                boundaries._supervised_run(command, Path(directory), 1, None)

    def test_live_process_resident_memory_is_observable(self):
        observed = boundaries._process_rss_mb(os.getpid())
        self.assertIsInstance(observed, float)
        self.assertGreater(observed, 0.0)
        self.assertIsNone(boundaries._process_rss_mb(0))


class BoundaryStimulusTest(unittest.TestCase):
    def test_explicit_input_trial_preserves_pin_identity_and_domain(self):
        candidate, trial = boundaries._candidate_and_trial(
            dict(kind="setup", pin="addr0[2]", setup_ns=.2, hold_ns=.3),
            base_candidate())
        self.assertEqual((trial["pin"], trial["port"], trial["bit"]),
                         ("addr0[2]", 0, 2))
        self.assertEqual((trial["axis"], trial["axis_value"]), ("setup_ns", .2))
        self.assertEqual(trial["domain"]["load_ff"], 5)
        self.assertEqual(candidate["environment"]["period_ns"], 7.5)
        with self.assertRaisesRegex(ValueError, "explicit pin"):
            boundaries._candidate_and_trial(
                dict(kind="hold", setup_ns=.2, hold_ns=.3), base_candidate())
        with self.assertRaisesRegex(ValueError, "outside the macro address width"):
            boundaries._candidate_and_trial(
                dict(kind="setup", pin="addr0[4]", setup_ns=.2, hold_ns=.3),
                base_candidate())

    def test_inserted_trace_and_pwl_encode_setup_and_hold_at_requested_clock_edge(self):
        cycles = {}
        for cycle in range(6):
            selected = cycle == 2
            cycles[cycle] = dict(role0="RW", csb0="0" if selected else "1",
                                 web0="1", op0="R" if selected else "I",
                                 addr0="0000", din0="0")
        expected = {2: {"dout0": "1"}}
        pin = boundaries._pin_spec("csb0", ["RW"], {"num_words": 16})
        modified, shifted, target_cycle = boundaries._insert_input_boundary(
            cycles, expected, 2, pin)
        self.assertEqual(target_cycle, 3)
        self.assertEqual(shifted, {3: {"dout0": "1"}})
        self.assertEqual((modified[2]["csb0"], modified[3]["csb0"],
                          modified[4]["csb0"]), ("1", "0", "1"))
        with tempfile.TemporaryDirectory() as directory:
            deck = Path(directory) / "boundary.cir"
            deck.write_text("Vi0 e0 0 PULSE(0 1.08 0 .03n .03n 3.72n 7.5n)\n")
            boundaries._input_source(deck, ["csb0"], modified, pin, target_cycle,
                                    .2, .3, 7.5, 1.08, .03, 0)
            line = next(item for item in deck.read_text().splitlines()
                        if item.startswith("Vi0 "))
            points = [(float(time) * 1e-9, float(value)) for time, value in
                      re.findall(r"([0-9.eE+-]+)n ([0-9.eE+-]+)", line)]
            times = [time for time, _ in points]
            values = [value for _, value in points]
            crossings, directions = boundaries.directed_crossings(times, values, .54)
            rising = next(time for time, direction in zip(crossings, directions)
                          if direction < 0)
            falling = next(time for time, direction in zip(crossings, directions)
                           if direction > 0)
            edge = (target_cycle + 1) * 7.5e-9 + .03e-9 / 2
            self.assertAlmostEqual((edge - rising) * 1e9, .2, places=6)
            self.assertAlmostEqual((falling - edge) * 1e9, .3, places=6)

    def test_input_trial_verdict_measures_both_transition_sides_of_real_clock_edge(self):
        times = [index * .005 for index in range(801)]
        clock = interpolate(times, [(0, 0), (2, 0), (2.1, 1), (2.5, 1),
                                    (2.6, 0), (3, 0), (3.1, 1), (4, 1)])
        control = interpolate(times, [(0, 1), (1.8, 1), (1.9, 0), (2.2, 0),
                                      (2.3, 1), (4, 1)])
        profile = dict(signals=[dict(name="clk0", column=0),
                                dict(name="csb0", column=1)])
        trial = dict(kind="setup", pin="csb0", pin_field="csb", port=0, bit=None,
                     setup_ns=.2, hold_ns=.2, target_cycle=1,
                     domain=dict(period_ns=1, input_slew_ns=.1, vdd=1))
        result = boundaries._waveform_verdict(
            trial, {0: {"csb0": "1"}, 1: {"csb0": "0"}, 2: {"csb0": "1"}},
            Path("unused"), profile, times, {0: clock, 1: control})
        self.assertTrue(result["passed"])
        self.assertAlmostEqual(result["observed_setup_ns"], .2, places=6)
        self.assertAlmostEqual(result["observed_hold_ns"], .2, places=6)
        self.assertAlmostEqual(result["clock_rising_ns"], 2.05, places=6)

    def test_pulse_and_load_slew_trials_require_waveform_measurements(self):
        times = [index * .005 for index in range(801)]
        clock = interpolate(times, [(0, 0), (2, 0), (2.1, 1), (2.5, 1),
                                    (2.6, 0), (3, 0), (3.1, 1), (3.5, 1),
                                    (3.6, 0), (4, 0)])
        profile = dict(signals=[dict(name="clk0", column=0)])
        pulse = boundaries._waveform_verdict(
            dict(kind="pulse", clock_port=0, target_read_cycle=1,
                 clock_high_ns=.5, clock_low_ns=.5,
                 domain=dict(period_ns=1, input_slew_ns=.1, vdd=1)),
            {}, Path("unused"), profile, times, {0: clock})
        self.assertTrue(pulse["passed"])
        self.assertAlmostEqual(pulse["observed_high_ns"], .5, places=6)
        self.assertAlmostEqual(pulse["observed_low_ns"], .5, places=6)

        slew = interpolate(times, [(0, 0), (1, 0), (1.1, 1), (4, 1)])
        domain_profile = dict(signals=[dict(name="csb0", column=1, stage="input")])
        with tempfile.TemporaryDirectory() as directory:
            deck = Path(directory) / "domain.cir"
            deck.write_text("Cload0 output 0 5f\n")
            domain = boundaries._waveform_verdict(
                dict(kind="domain", domain=dict(period_ns=4, load_ff=5,
                                                 input_slew_ns=.1, vdd=1)),
                {}, deck, domain_profile, times, {1: slew})
        self.assertTrue(domain["passed"])
        self.assertEqual(domain["output_load_ff"], [5])
        self.assertAlmostEqual(domain["observed_10_90_slew_ns"], .08, places=6)


class BoundaryEvidenceTest(unittest.TestCase):
    def test_only_measured_distinct_pass_fail_points_are_reported_without_limits(self):
        def record(value, passed, status="measured"):
            return dict(status=status, passed=passed,
                        trial=dict(condition={"pin": "csb0", "hold_ns": .2},
                                   axis="setup_ns", axis_value=value, id=str(value)))
        support = boundaries._trial_support([
            record(.1, True), record(.2, False),
            record(.15, False, "stimulus-invalid"),
            record(.3, None, "generated-not-measured")])
        self.assertEqual(len(support), 1)
        self.assertEqual(support[0]["support_status"], "sampled_pass_and_fail")
        self.assertEqual(len(support[0]["sampled_pass_fail_pairs"]), 1)
        self.assertIsNone(support[0]["limit"])
        self.assertFalse(support[0]["continuous_domain_proven"])
        inconsistent = boundaries._trial_support([record(.2, True), record(.2, False)])[0]
        self.assertEqual(inconsistent["support_status"], "inconsistent_repeated_sample")
        self.assertFalse(inconsistent["sampled_pass_fail_pairs"])

    def test_unresolved_setup_hold_pulse_load_slew_and_signoff_are_named(self):
        unresolved = set(boundaries._unresolved_constraints(
            dict(access_contract=dict(ports=["RW", "R", "W"]))))
        for required in ("addr0_setup", "addr0_hold", "csb0_setup", "csb0_hold",
                         "web0_setup", "web0_hold", "clk0_minimum_high_pulse",
                         "clk0_minimum_low_pulse", "addr1_setup", "csb1_hold",
                         "clk1_minimum_high_pulse", "output_load_continuity",
                         "input_slew_continuity", "minimum_period", "STA/Liberty signoff"):
            self.assertIn(required, unresolved)
        self.assertNotIn("web1_setup", unresolved)
        self.assertFalse(any(item.startswith("addr2") for item in unresolved))


if __name__ == "__main__":
    unittest.main()
