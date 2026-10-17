"""Isolated PWL timing, model-dependency and initialization regressions."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SIM = Path(__file__).resolve().parent
REPO = SIM.parents[2]
for path in (SIM, REPO):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
import architecture as adapter


class TimingTest(unittest.TestCase):
    def metrics(self, *, early=True, wrong_sign=False, wen=True, skew=0, release=3.4):
        # Full numeric PWL traces, not delegated checker echoes.
        times = [0, 2, 2.2, 2.4, 3, release, release+.2, 4, 4.1, 4.3, 4.5, 4.7, 5, 5.2, 5.4, 6, 8]
        times = sorted(set(times))
        def sampled(points):
            return [adapter.value_at([p[0] for p in points], [p[1] for p in points], t) for t in times]
        sa_start = 4.1 if early else 5.0
        waves = [sampled([(0, 0), (sa_start, 0), (sa_start+.2, 1), (5.4, 1), (5.6, 0), (8, 0)]),
                 sampled([(0, 0), (2.2, 0), (2.4, int(wen)), (release, int(wen)), (release+.2, 0), (8, 0)]),
                 sampled([(0, 1), (4.5, 1), (4.7, .8), (8, .8)]),
                 sampled([(0, 1), (8, 1)]),
                 sampled([(0, 1), (2.4, 1), (3, 0), (8, 0)])]
        if wrong_sign:
            waves[2], waves[3] = waves[3], waves[2]
        signals = [dict(name='sa', node='sa', reference_device='BANK0.PORT_DATA0.SENSE_AMP_ARRAY0.SA_D0.M2', terminal='G', column=0),
                   dict(name='wen', node='wen', reference_device='BANK0.PORT_DATA0.WRITE_DRIVER_ARRAY0.WRITE_DRIVER0.M4', terminal='G', column=1),
                   dict(name='bl', node='bl', reference_net='BANK0.BL_0_0', column=2),
                   dict(name='br', node='br', reference_net='BANK0.BR_0_0', column=3),
                   dict(name='q', node='q', reference_net='BANK0.BITCELL_ARRAY.BIT_R0_C0.Q', column=4)]
        mapping = {'nets': {'S_EN0': {'nodes': ['sa']}, 'W_EN0': {'nodes': ['wen']}}}
        target = dict(read_cycle=1, read_port=0, write_cycle=0, write_port=0, expected=0, written=0,
                      bit=0, row=0, column=0, address=0)
        env = dict(period_ns=2, voltage_v=1, skew1=skew)
        cycles = {0: {'csb0': '0', 'web0': '0'}, 1: {'csb0': '0', 'web0': '1'}}
        return adapter._target_metrics(times, waves, {'signals': signals}, target, env, .1, mapping, cycles)

    def test_early_sa_is_negative_setup(self):
        metric = self.metrics()
        self.assertAlmostEqual(metric['sa_setup_ns'], -.4)
        self.assertAlmostEqual(metric['bl_diff_v'], 0)
        self.assertLess(metric['sa_activation_ns'][0], 5.99)  # Pulse ended before sample.

    def test_ready_before_sa_is_positive_setup(self):
        metric = self.metrics(early=False)
        self.assertAlmostEqual(metric['sa_setup_ns'], .5)
        self.assertAlmostEqual(metric['bl_diff_v'], .2)

    def test_wrong_signed_bl_does_not_become_absolute_margin(self):
        metric = self.metrics(early=False, wrong_sign=True)
        self.assertLess(metric['bl_diff_v'], 0)
        self.assertIsNone(metric['sa_setup_ns'])

    def test_absent_write_pulse_is_measured_zero(self):
        metric = self.metrics(wen=False)
        self.assertEqual(metric['wen_pulse_ns'], 0)
        self.assertIsNone(metric['write_recovery_ns'])

    def test_recovery_is_release_to_next_access_not_q_flip(self):
        metric = self.metrics()
        self.assertAlmostEqual(metric['write_recovery_ns'], .44)
        self.assertNotEqual(metric['write_recovery_ns'], metric['storage_completion_ns'])

    def test_late_release_produces_negative_recovery(self):
        self.assertLess(self.metrics(release=4.4)['write_recovery_ns'], 0)

    def test_missing_gate_probe_is_unknown(self):
        times = [0, 8]
        target = dict(read_cycle=1, read_port=0, write_cycle=0, write_port=0, expected=0,
                      written=0, bit=0, row=0, column=0)
        metric = adapter._target_metrics(times, [], {'signals': []}, target,
                                        dict(period_ns=2, voltage_v=1, skew1=0), .1,
                                        {'nets': {}}, {0: {'csb0': '0'}, 1: {'csb0': '0'}})
        for name in adapter.MARGINS:
            self.assertIsNone(metric[name])

    def test_pulse_between_samples_retains_interpolated_edges(self):
        self.assertEqual(adapter._intervals([0, 1, 2, 3, 4], [0, 0, 1, 0, 0], .5, 0, 4), [[1.5, 2.5]])

    def test_threshold_exact_sample_and_uncovered_interval(self):
        self.assertEqual(adapter._intervals([0, 1, 2], [0, .5, 1], .5, 0, 2), [[1, 2]])
        self.assertIsNone(adapter._intervals([0, 1], [0, 1], .5, 0, 2))

    def test_secondary_clock_skew_changes_actual_access_endpoint(self):
        times = [0, 2, 2.2, 2.4, 3.4, 3.6, 8]
        waves = [[0, 0, 0, 1, 1, 0, 0]]
        signal = dict(name='wen', node='wen', reference_device='BANK0.PORT_DATA0.WRITE_DRIVER_ARRAY0.WRITE_DRIVER0.M4', terminal='G', column=0)
        target = dict(read_cycle=1, read_port=1, write_cycle=0, write_port=0,
                      expected=0, written=0, bit=0, row=0, column=0)
        metric = adapter._target_metrics(times, waves, {'signals': [signal]}, target,
                                        dict(period_ns=2, voltage_v=1, skew1=.25), .1,
                                        {'nets': {'W_EN0': {'nodes': ['wen']}}},
                                        {0: {'csb0': '0'}, 1: {'csb1': '0'}})
        self.assertAlmostEqual(metric['next_access_ns'], 4.5)
        self.assertAlmostEqual(metric['write_recovery_ns'], .94)

    def test_wordline_already_high_is_not_a_zero_delay_edge(self):
        times = [0, 2, 3, 4, 4.2, 4.4, 5, 6, 8]
        signal = dict(name='wl', node='wl', stage='storage', terminal='G', column=0,
                      reference_device='BANK0.BITCELL_ARRAY.BIT_R0_C0.M2')
        target = dict(read_cycle=1, read_port=0, write_cycle=0, write_port=0,
                      expected=0, written=0, bit=0, row=0, column=0)
        for values, status in (([1] * len(times), 'already_high'),
                               ([0, 0, 0, 0, 0, 1, 1, 0, 0], 'measured')):
            metric = adapter._target_metrics(
                times, [values], {'signals': [signal]}, target,
                dict(period_ns=2, voltage_v=1, skew1=0), .1,
                {'nets': {'BANK0.WL_0_0': {'nodes': ['wl']}}},
                {0: {'csb0': '0'}, 1: {'csb0': '0'}})
            self.assertEqual(metric['selected_wl_events']['0_read'][0]['status'], status)
            if status == 'already_high':
                self.assertIsNone(metric['selected_wl_delay_ns']['0_read'])
            else:
                self.assertAlmostEqual(metric['selected_wl_delay_ns']['0_read'], .3)

    def test_read_coverage_cannot_prove_missing_write_port_margins(self):
        targets = [dict(target={'read_port': port, 'write_port': 0},
                        wen_pulse_ns=.2, sa_setup_ns=.1, bl_diff_v=.1, write_recovery_ns=.2)
                   for port in (0, 1)]
        aggregate = adapter._aggregate_margins(targets, ['RW', 'RW'])
        self.assertEqual(aggregate['sa_setup_ns'], .1)
        self.assertIsNone(aggregate['wen_pulse_ns'])
        self.assertIsNone(aggregate['write_recovery_ns'])
        targets[1]['target']['write_port'] = 1
        targets[1]['wen_pulse_ns'] = .15
        self.assertEqual(adapter._aggregate_margins(targets, ['RW', 'RW'])['wen_pulse_ns'], .15)


class DependencyTest(unittest.TestCase):
    def test_recursive_lib_include_and_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            top, child, leaf = (root / name for name in ('top.lib', 'child.lib', 'leaf.inc'))
            top.write_text("* don't parse this unmatched ' comment\n.lib ss\n.lib 'child.lib' ss\n.endl ss\n")
            child.write_text('.include "leaf.inc"\n')
            leaf.write_text('.param x=1\n')
            first = adapter._dependencies(top)
            leaf.write_text('.param x=2\n')
            self.assertNotEqual(first, adapter._dependencies(top))

    def test_initialization_component_cannot_touch_mos_or_port(self):
        with tempfile.TemporaryDirectory() as directory:
            netlist = Path(directory) / 'macro.sp'
            netlist.write_text('.subckt macro vdd gnd clk0 dout0[0]\nXM1 q gate gnd gnd nmos\nR1 q bad 1\nR2 metal spare 1\nC1 metal gnd 1f\n.ends macro\n')
            adapter._parasitic_only(netlist, 'macro', ['metal'])
            with self.assertRaisesRegex(ValueError, 'MOS/storage/port'):
                adapter._parasitic_only(netlist, 'macro', ['bad'])
            with self.assertRaisesRegex(ValueError, 'MOS/storage/port'):
                adapter._parasitic_only(netlist, 'macro', ['clk0'])

    def test_policy_and_legacy_explicit_plan(self):
        plan = {'initial_conditions': [{'node': 'metal', 'voltage_v': 0,
                                        'no_MOS_or_port_in_resistor_component': True}]}
        self.assertEqual(adapter._initial_nodes(plan, 'zero-voltage'), ['metal'])
        with self.assertRaisesRegex(ValueError, 'policy mismatch'):
            adapter._initial_nodes(plan, 'uninitialized')
        plan['initial_conditions'][0]['voltage_v'] = 1
        with self.assertRaisesRegex(ValueError, 'parasitic-only'):
            adapter._initial_nodes(plan, 'zero-voltage')

    def test_zero_voltage_plan_cannot_claim_known_initial_charge(self):
        base = dict(policy='zero-voltage', floating_nodes=['metal'], initial_voltage_v=0,
                    initial_charge_c=None, charge_status='unknown')
        for extra in ({'initial_charge_c': 0}, {'charge_status': 'specified'}):
            with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'initial charge'):
                adapter._initial_nodes(dict(base, **extra), 'zero-voltage')


class ObservationModelTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = {name: self.root / (name + '.dat')
                      for name in ('gds', 'netlist', 'trace', 'probe_map', 'topology')}
        for name, path in self.paths.items():
            path.write_text('synthetic unit input: ' + name)
        self.paths['model'] = self.root / 'top.lib'
        self.child = self.root / 'child.inc'
        self.paths['model'].write_text('.include "child.inc"\n')
        self.child.write_text('.model n nmos vto=.5\n')
        self.bundle = {name: self.root / (name + '.dat')
                       for name in ('run', 'waves', 'probes', 'init', 'deck', 'extraction')}
        for name, path in self.bundle.items():
            path.write_text('synthetic unit observation: ' + name)
        self.manifest = {
            'schema': 'openram-ngspice-observation/1',
            'model_dependencies': adapter._dependencies(self.paths['model']),
            'bindings': adapter._observation_bindings(self.paths, self.bundle),
            'execution': {'model_capture': 'before-launch', 'post_execution_unchanged': True,
                          'returncode': 0,
                          'tool': {'version': 'synthetic-unit-only', 'sha256': '0' * 64}}}
        self.bundle['models'] = self.root / 'models.json'
        self.save()

    def save(self):
        self.bundle['models'].write_text(json.dumps(self.manifest))

    def test_historical_bundle_cannot_gain_current_model_provenance(self):
        self.bundle.pop('models')
        with self.assertRaisesRegex(ValueError, 'execution-time model-dependency manifest'):
            adapter._validate_model_manifest(self.bundle, self.paths)

    def test_included_model_change_invalidates_old_observation(self):
        adapter._validate_model_manifest(self.bundle, self.paths)
        self.child.write_text('.model n nmos vto=.8\n')
        with self.assertRaisesRegex(ValueError, 'model-dependency closure'):
            adapter._validate_model_manifest(self.bundle, self.paths)

    def test_old_model_snapshot_cannot_be_attached_to_other_waveforms(self):
        self.bundle['waves'].write_text('different synthetic transient')
        with self.assertRaisesRegex(ValueError, 'exact inputs/outputs'):
            adapter._validate_model_manifest(self.bundle, self.paths)

    def test_posthoc_snapshot_is_not_execution_time_evidence(self):
        self.manifest['execution']['model_capture'] = 'after-launch'
        self.save()
        with self.assertRaisesRegex(ValueError, 'execution-time model/tool'):
            adapter._validate_model_manifest(self.bundle, self.paths)

    def test_nonzero_launcher_cannot_be_replayed_as_success(self):
        self.manifest['execution']['returncode'] = 17
        self.save()
        with self.assertRaises(ValueError):
            adapter._validate_model_manifest(self.bundle, self.paths)


class LauncherFailureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.launcher = self.root / 'launcher'
        self.launcher.mkdir()
        self.output = self.root / 'output'
        self.output.mkdir()
        self.paths = {name: self.root / (name + '.dat')
                      for name in ('gds', 'netlist', 'trace', 'probe_map', 'topology', 'extraction')}
        self.paths['gds'].write_text('synthetic GDS input')
        self.paths['netlist'].write_text('synthetic SPICE input')
        self.paths['trace'].write_text('synthetic trace input')
        gds_hash = adapter._digest(self.paths['gds'])
        netlist_hash = adapter._digest(self.paths['netlist'])
        binding = dict(input_gds_sha256=gds_hash, netlist_sha256=netlist_hash,
                       completed=True, equivalent=True)
        for name in ('probe_map', 'topology'):
            self.paths[name].write_text(json.dumps(binding))
        self.paths['extraction'].write_text(json.dumps(dict(
            input_gds_sha256=gds_hash, completed=True, accepted=False,
            corners=[dict(netlist=str(self.paths['netlist']), netlist_sha256=netlist_hash)])))
        model = self.root / 'pdk/libs.tech/spice/foundry/ngspice/ICsprout_55LLULP1225_V1p1_hsp.lib'
        model.parent.mkdir(parents=True)
        model.write_text('.model unit_fixture nmos\n')
        self.paths['model'] = model
        self.candidate = dict(
            parameters={'words_per_row': 1}, access_contract={'ports': ['RW', 'RW']},
            environment=dict(corner='snfp', voltage_v=1.08, temperature_c=125, period_ns=7.5,
                             cload_ff=5, slew_ns=.03, skew1=0, initialization='zero-voltage',
                             fidelity='F3'))
        self.outputs = {
            'simulation.json': json.dumps(dict(passed=True, reads=6, bit_checks=48)),
            'waves.dat': 'time v(dout0)\n0 0\n1e-9 1.08\n',
            'probes.json': json.dumps(dict(signals=[], targets=[])),
            'initialization_plan.json': json.dumps(dict(policy='zero-voltage')),
            'transient.sp': '* synthetic unit fixture, not a simulator result\n.end\n'}

    def write_launcher(self, returncode, outputs):
        (self.launcher / 'ngspice_pex.py').write_text(
            'from pathlib import Path\nimport sys\n'
            'out = Path(sys.argv[sys.argv.index("--out-dir") + 1])\n'
            'outputs = ' + repr(outputs) + '\n'
            'for name, text in outputs.items():\n'
            '    (out / name).write_text(text)\n'
            'print("unit fixture: outputs written; launcher termination follows", flush=True)\n'
            'sys.exit(' + str(returncode) + ')\n')

    def test_successful_looking_outputs_do_not_override_nonzero_exit(self):
        self.write_launcher(17, self.outputs)
        admission = dict(candidate=self.candidate, artifacts=self.paths,
                         model={'artifacts': {name: adapter._ref(path)
                                              for name, path in self.paths.items()}})
        with patch.object(adapter, 'SIM', self.launcher):
            result = adapter.evaluate(admission, self.output, execute=True,
                                      ngspice=sys.executable, timeout=10)
        self.assertIsNone(result['checks']['functional'])
        self.assertTrue(all(item['value'] is None for item in result['measurements']))
        self.assertIn('17', result['details']['error'])
        run_dir, = self.output.iterdir()
        self.assertTrue(json.loads((run_dir / 'simulation.json').read_text())['passed'])
        self.assertIn('outputs written', (run_dir / 'adapter-launch.log').read_text())
        self.assertFalse((run_dir / 'observation-manifest.json').exists())

    def test_successful_exit_requires_every_nonempty_output(self):
        for filename in self.outputs:
            for empty in (False, True):
                with self.subTest(filename=filename, empty=empty):
                    outputs = dict(self.outputs)
                    if empty:
                        outputs[filename] = ''
                    else:
                        del outputs[filename]
                    self.write_launcher(0, outputs)
                    with patch.object(adapter, 'SIM', self.launcher), self.assertRaises(ValueError):
                        adapter._execute(self.paths, self.candidate, self.output, sys.executable, 10)
        for run_dir in self.output.iterdir():
            self.assertTrue((run_dir / 'adapter-launch.log').is_file())
            self.assertFalse((run_dir / 'observation-manifest.json').exists())




if __name__ == '__main__':
    unittest.main()
