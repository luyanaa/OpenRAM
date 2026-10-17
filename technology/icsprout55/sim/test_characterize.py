"""Isolated numeric/domain regressions; no simulator or licensed PDK required."""
from copy import deepcopy
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from technology.icsprout55.sim import characterize, charz, mklib
from technology.icsprout55.sim.test_p1_interfaces import braced_group, measurement_file


def cycles():
    result = {}
    for cycle in range(9):
        result[cycle] = dict(role0="RW", csb0="1", web0="1", addr0="0", din0="0",
                             role1="RW", csb1="1", web1="1", addr1="0", din1="0")
    for cycle, operation in ((1, "W"), (2, "W"), (3, "R"), (4, "R"), (5, "R")):
        result[cycle].update(csb0="0", web0="0" if operation == "W" else "1")
    return result


def waves():
    # Real finite PWL data, including exact 50% samples and both directions.
    times_ns = sorted({0, 10, 4.5, 4.6, 4.7, 4.8, 5.5, 5.6, 5.7, 5.8} |
                      {index * .1 for index in range(101)})
    output = []
    for time in times_ns:
        if time <= 4.6:
            value = 0.0
        elif time < 4.8:
            value = (time - 4.6) / .2
        elif time <= 5.6:
            value = 1.0
        elif time < 5.8:
            value = 1.0 - (time - 5.6) / .2
        else:
            value = 0.0
        output.append(value)
    clock = [1.0 if time % 1 <= .4 else max(0.0, (0.6 - time % 1) / .2) for time in times_ns]
    data = {"clk0": clock, "dout0[0]": output,
            "i(vdd)": [-.002] * len(times_ns)}
    return [time * 1e-9 for time in times_ns], data


class NumericTest(unittest.TestCase):
    def measure(self, cycle_data=None, wave_data=None):
        times, data = waves() if wave_data is None else wave_data
        return charz.measure_port(times, data, cycles() if cycle_data is None else cycle_data,
                                  0, 1.0, 1.0, "i(vdd)", skew1=0)

    def test_signed_integral_and_both_polarities(self):
        result = self.measure()
        self.assertAlmostEqual(result["e_read_pj"], 2.0)
        self.assertAlmostEqual(result["e_write_pj"], 2.0)
        self.assertAlmostEqual(result["cell_rise"], .2)
        self.assertAlmostEqual(result["cell_fall"], .2)
        self.assertAlmostEqual(result["rise_transition"], .16)
        self.assertAlmostEqual(result["fall_transition"], .16)
        self.assertIsNone(result["leak_mw"])
        self.assertAlmostEqual(result["e_idle_pj"], 2.0)
        times, data = waves()
        data["i(vdd)"] = [.002] * len(times)
        self.assertAlmostEqual(self.measure(wave_data=(times, data))["e_read_pj"], -2.0)

    def test_clipped_linear_integration_is_exact(self):
        times, data = waves()
        data["i(vdd)"] = [-time * 1e6 for time in times]
        result = self.measure(wave_data=(times, data))
        # Maximum read window is 5..6ns; integral(t*1e6 dt)=5.5pJ.
        self.assertAlmostEqual(result["e_read_pj"], 5.5)

    def test_missing_nonfinite_truncated_or_duplicate_data_rejected(self):
        times, data = waves()
        for bad_times, bad_data in ((times, {key: value for key, value in data.items() if key != "i(vdd)"}),
                                    (times, {**data, "i(vdd)": data["i(vdd)"][:-1]}),
                                    ([times[0]] + times, data),
                                    (times, {**data, "i(vdd)": [float("nan")] * len(times)})):
            with self.subTest(case=len(bad_times)), self.assertRaises(ValueError):
                self.measure(wave_data=(bad_times, bad_data))
        stop = next(index for index, time in enumerate(times) if time >= 5.9e-9)
        with self.assertRaisesRegex(ValueError, "support"):
            self.measure(wave_data=(times[:stop], {key: values[:stop] for key, values in data.items()}))

    def test_skew_is_explicitly_rejected(self):
        times, data = waves()
        with self.assertRaisesRegex(ValueError, "zero"):
            charz.measure_port(times, data, cycles(), 0, 1, 1, "i(vdd)", skew1=.1)

    def test_next_half_cycle_input_change_invalidates_energy_isolation(self):
        schedule = cycles()
        schedule[4]["din1"] = "1"
        schedule[5]["din1"] = "1"
        schedule[6]["din1"] = "1"
        schedule[7]["din1"] = "1"
        schedule[8]["din1"] = "1"
        result = self.measure(cycle_data=schedule)
        self.assertFalse(any(item["cycle"] == 3 for item in result["raw_energy"]["read"]))
        schedule = cycles()
        schedule[2]["csb1"] = "0"
        result = self.measure(cycle_data=schedule)
        self.assertFalse(any(item["cycle"] == 3 for item in result["raw_energy"]["read"]))

    def test_same_port_next_input_changes_exclude_contaminated_operation_energy(self):
        for pin, changed in (("din0", "1"), ("wmask0", "0")):
            schedule = cycles()
            if pin == "wmask0":
                for record in schedule.values():
                    record[pin] = "1"
            schedule[4][pin] = changed
            result = self.measure(cycle_data=schedule)
            self.assertIsNone(result["e_read_pj"])
            self.assertAlmostEqual(result["cell_rise"], .2)
        schedule = cycles()
        schedule[2]["addr0"] = "1"
        self.assertIsNone(self.measure(cycle_data=schedule)["e_write_pj"])

    def test_explicit_operation_cannot_mislabel_electrical_write_enable(self):
        for cycle, web in ((1, "1"), (3, "0")):
            schedule = cycles()
            schedule[cycle].update(web0=web, op0="W" if cycle == 1 else "R")
            with self.assertRaisesRegex(ValueError, "operation disagrees"):
                self.measure(cycle_data=schedule)

    def test_stable_output_and_missing_bit_polarity_are_unknown_not_zero(self):
        times, data = waves()
        data["dout0[0]"] = [1.0] * len(times)
        result = self.measure(wave_data=(times, data))
        for key in ("dout_fall", "cell_rise", "cell_fall", "rise_transition", "fall_transition"):
            self.assertIsNone(result[key])
        times, data = waves()
        data["dout0[1]"] = [1.0] * len(times)
        result = self.measure(wave_data=(times, data))
        self.assertIsNone(result["cell_rise"])
        self.assertIsNone(result["cell_fall"])

    def test_eligible_port_coverage_and_functional_failure_gate_aggregates(self):
        per_port = {"0": {"e_read_pj": 3, "e_write_pj": 2}, "1": {"e_read_pj": None}}
        result = characterize.aggregate_energy(per_port, ["RW", "R"], True)
        self.assertIsNone(result["read_energy_pj"])
        self.assertEqual(result["write_energy_pj"], 2)
        per_port["1"]["e_read_pj"] = 4
        self.assertEqual(characterize.aggregate_energy(per_port, ["RW", "R"], True)["read_energy_pj"], 4)
        self.assertEqual(characterize.aggregate_energy(per_port, ["RW", "R"], False),
                         {"read_energy_pj": None, "write_energy_pj": None})


class DomainTest(unittest.TestCase):
    def test_domain_pair_rejects_missing_mismatched_bool_nan_or_failed_data(self):
        valid = measurement_file("SC9T", {0: "RW"})["tt"]
        mklib.measurement_pair(valid, 0, .03)
        for mutate in (lambda item: item["m50"]["domain"].pop("period_ns"),
                       lambda item: item["m50"]["domain"].update(load_ff=5),
                       lambda item: item["m50"]["domain"].update(input_slew_ns=.04),
                       lambda item: item["m50"]["domain"].update(vdd=1.08),
                       lambda item: item["m50"]["domain"].update(load_ff=True),
                       lambda item: item["m50"]["domain"].update(temp=float("nan")),
                       lambda item: item["m50"].update(functional=False)):
            item = deepcopy(valid)
            mutate(item)
            with self.assertRaises(ValueError):
                mklib.measurement_pair(item, 0, .03)

    def test_diagnostic_liberty_is_explicit_and_has_no_guessed_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "measurements.json"
            source.write_text(json.dumps(measurement_file("SC9T", {0: "RW", 1: "R"})))
            argv = ["mklib", str(source), "macro", str(root), "--words", "16", "--bits", "8",
                    "--area", "42", "--stdcell-library", "SC9T", "--port-roles", "0:RW,1:R"]
            with mock.patch("sys.argv", argv), self.assertRaises(SystemExit):
                mklib.main()
            with mock.patch("sys.argv", argv + ["--diagnostic-table-only"]):
                mklib.main()
            text = (root / "macro_tt.lib").read_text()
            self.assertIn("dont_use : true", text)
            for unsupported in ("setup_rising", "hold_rising", "minimum_period;", "min_pulse_width_",
                                "internal_power", "cell_leakage_power"):
                self.assertNotIn(unsupported, text)
            self.assertNotRegex(text, r"\n\s+capacitance\s*:")

    def test_diagnostic_export_omits_incomplete_polarity_without_inventing_values(self):
        for missing in (("m5", "m50"), ("m50",), ("transition-only",)):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                measurements = measurement_file("SC9T", {0: "RW"})
                for label in missing:
                    if label == "transition-only":
                        measurements["tt"]["m50"]["ports"]["0"]["rise_transition"] = None
                    else:
                        for key in ("cell_rise", "rise_transition"):
                            measurements["tt"][label]["ports"]["0"][key] = None
                source = root / "measurements.json"
                source.write_text(json.dumps(measurements))
                argv = ["mklib", str(source), "macro", str(root), "--words", "16",
                        "--bits", "8", "--area", "42", "--stdcell-library", "SC9T",
                        "--port-roles", "0:RW", "--diagnostic-table-only"]
                with mock.patch("sys.argv", argv):
                    mklib.main()
                text = (root / "macro_tt.lib").read_text()
                output = braced_group(text, "bus (dout0)")
                self.assertNotIn("cell_rise (", output)
                self.assertNotIn("rise_transition (", output)
                fall = braced_group(output, "cell_fall (delay_tbl0)")
                values = re.search(r'values\s*\("([^"]+)"\)', fall).group(1)
                self.assertEqual([float(value) for value in values.split(",")], [.655, .7])
                self.assertIn("fall_transition (delay_tbl0)", output)
                self.assertIn("dont_use : true", text)

    def test_diagnostic_tables_reject_invalid_values_and_totally_unobserved_reads(self):
        valid = measurement_file("SC9T", {0: "RW"})["tt"]
        mutations = [
            lambda item: item["m50"]["ports"]["0"].update(cell_rise=float("nan")),
            lambda item: item["m50"]["ports"]["0"].update(cell_rise=True),
            lambda item: item["m50"]["ports"]["0"].pop("cell_rise"),
            lambda item: item["m50"]["ports"]["0"].update(
                cell_rise=None, rise_transition=None, cell_fall=None, fall_transition=None),
        ]
        for mutate in mutations:
            item = deepcopy(valid)
            mutate(item)
            pair = mklib.measurement_pair(item, 0)
            with self.assertRaises(ValueError):
                mklib._observed_read_tables(pair)

    def test_replay_rejects_load_relabel_and_mixed_tuple_before_native_work(self):
        candidate = dict(environment=dict(skew1=0, cload_ff=5, period_ns=1, slew_ns=.03,
                                          corner="tt", voltage_v=1, temperature_c=25),
                         parameters={"variant": 1})
        manifest = dict(schema=characterize.SCHEMA, signoff=False, complete_sta=False,
                        base_candidate=candidate, loads_ff=[5, 50], observations=[])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "characterization.json"
            path.write_text(json.dumps(manifest))
            changed = deepcopy(candidate)
            changed["environment"]["cload_ff"] = 20
            with self.assertRaisesRegex(ValueError, "candidate/domain"):
                characterize.load_characterization(path, {"candidate": changed}, None)
            changed = deepcopy(candidate)
            changed["parameters"]["variant"] = 2
            with self.assertRaisesRegex(ValueError, "candidate/domain"):
                characterize.load_characterization(path, {"candidate": changed}, None)

    def test_selected_load_replay_recomputes_and_rejects_stale_hash_or_numeric_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = dict(environment=dict(skew1=0, cload_ff=5, period_ns=8, slew_ns=.03,
                corner="tt", voltage_v=1.2, temperature_c=25), parameters={"variant": 1})
            observations = []
            for load in (5, 50):
                exact = deepcopy(candidate)
                exact["environment"]["cload_ff"] = load
                bundle = {}
                for kind in characterize.BUNDLE_KINDS:
                    artifact = root / ("{}_{}".format(load, kind))
                    artifact.write_text("synthetic unit replay " + kind)
                    bundle[kind] = characterize.native._ref(artifact)
                sample = dict(schema_version=3, domain=characterize._domain(exact), ports={})
                observations.append(dict(candidate=exact, domain=sample["domain"], sample=sample,
                    bundle=bundle, artifacts={"bound": bundle["waves"]}, status="measured", functional=True,
                    energy={"read_energy_pj": load}))
            samples_path = root / "measurements.json"
            samples_path.write_text(json.dumps({"tt": dict(m5=observations[0]["sample"], m50=observations[1]["sample"])}))
            manifest = dict(schema=characterize.SCHEMA, signoff=False, complete_sta=False,
                base_candidate=candidate, loads_ff=[5, 50], observations=observations,
                measurements=characterize.native._ref(samples_path))
            manifest_path = root / "characterization.json"
            manifest_path.write_text(json.dumps(manifest))
            def replay(admission, bundle, margins):
                load = admission["candidate"]["environment"]["cload_ff"]
                record = observations[0 if load == 5 else 1]
                return deepcopy({key: record[key] for key in ("candidate", "sample", "functional", "energy")})
            with mock.patch.object(characterize, "_measure", side_effect=replay), \
                    mock.patch.object(characterize, "_evidence", side_effect=lambda admission, bundle: {
                        "bound": characterize.native._ref(bundle["waves"])}), \
                    mock.patch.object(characterize.native, "_execute", side_effect=AssertionError("no simulator on replay")):
                selected = {"candidate": observations[1]["candidate"]}
                result = characterize.load_characterization(manifest_path, selected, None)
                self.assertEqual(result["sample"]["domain"]["load_ff"], 50)
                self.assertEqual(result["energy"]["read_energy_pj"], 50)
                changed = deepcopy(manifest)
                changed["observations"][1]["energy"]["read_energy_pj"] = 5
                manifest_path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError, "numeric sample"):
                    characterize.load_characterization(manifest_path, selected, None)
                manifest_path.write_text(json.dumps(manifest))
                Path(observations[0]["bundle"]["waves"]["path"]).write_text("stale other-load waveform")
                with self.assertRaises(ValueError):
                    characterize.load_characterization(manifest_path, selected, None)



if __name__ == "__main__":
    unittest.main()
