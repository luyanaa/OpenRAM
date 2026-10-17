"""Isolated real geometry/netlist sizing regressions; no physical signoff claims."""
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "sizing_variants", REPO / "technology/icsprout55/scripts/sizing_variants.py")
sizing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sizing)


def _geometry_case(case):
    import openram
    from openram.sram_factory import factory

    tech = "freepdk45" if case == "generic" else "icsprout55"
    with tempfile.TemporaryDirectory(prefix="sizing_geometry_") as directory:
        root = Path(directory)
        config = root / "config.py"
        config.write_text('tech_name = {!r}\nword_size = 2\nnum_words = 16\n'
                          'bitcell = "pbitcell"\ncheck_lvsdrc = False\n'
                          'nominal_corner_only = True\nuse_nix = False\n'
                          'output_path = {!r}\n'.format(tech, str(root) + os.sep))
        openram.init_openram(str(config), is_unit_test=True)
        try:
            if case == "geometry":
                _check_driver_geometry(factory, root)
            elif case == "cache":
                _check_cache_and_mask(factory)
            elif case == "generic":
                _check_generic(factory)
            elif case == "delay":
                _check_delay(factory, root)
            elif case == "option_path":
                _check_option_path(factory)
            else:
                raise ValueError(case)
        finally:
            openram.end_openram()


def _expect_value_error(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("unsupported sizing must fail explicitly")


def _gds_regions(driver, root, tag):
    import klayout.db as kdb
    from openram.tech import layer

    path = root / (tag + ".gds")
    driver.gds_write(str(path))
    layout = kdb.Layout()
    layout.read(str(path))
    top = layout.top_cell()
    regions = {}
    for name in ("active", "poly", "contact", "nwell"):
        spec = layer[name]
        regions[name] = kdb.Region(top.begin_shapes_rec(layout.layer(*spec)))
    gates = regions["active"] & regions["poly"]
    boxes = sorted((round(p.bbox().left * layout.dbu, 6),
                    round(p.bbox().bottom * layout.dbu, 6),
                    round(p.bbox().width() * layout.dbu, 6),
                    round(p.bbox().height() * layout.dbu, 6))
                   for p in gates.each())
    signature = {name: sorted(p.to_s() for p in region.each())
                 for name, region in regions.items()}
    contacts = regions["contact"] & regions["active"]
    signature["diffusion_contacts"] = sorted(p.to_s() for p in contacts.each())
    return boxes, signature


def _pin_signature(driver):
    return {name: sorted((pin.layer, (pin.lx(), pin.by()), (pin.rx(), pin.uy()))
                         for pin in driver.get_pins(name))
            for name in ("din", "en", "bl", "br", "vdd", "gnd")}


def _spice_devices(driver, root, tag, lvs):
    path = root / (tag + (".lvs.sp" if lvs else ".sp"))
    driver.sp_write(str(path), lvs=lvs)
    # Both real emitted views use one primitive line per physical MOS.
    devices = []
    for line in path.read_text().splitlines():
        if line[:1].upper() not in ("M", "X"):
            continue
        width = re.search(r"\bw=([\d.eE+-]+)u\b", line)
        length = re.search(r"\bl=([\d.eE+-]+)u\b", line)
        if width and length:
            fields = line.split()
            devices.append((fields[1:5], float(width.group(1)), float(length.group(1))))
    if len(devices) != 12:
        raise AssertionError("write driver must emit twelve real MOS devices")
    return devices


def _check_driver_geometry(factory, root):
    default = factory.create(module_type="write_driver")
    baseline = factory.create(module_type="write_driver", write_driver_strength=1.0)
    narrow = factory.create(module_type="write_driver", write_driver_strength=0.75)
    default_gates, default_shapes = _gds_regions(default, root, "default")
    base_gates, base_shapes = _gds_regions(baseline, root, "baseline")
    gates, shapes = _gds_regions(narrow, root, "narrow")
    assert default_shapes == base_shapes, "explicit baseline must preserve actual GDS geometry"
    assert default_gates == base_gates
    assert len(gates) == len(base_gates) == 12
    assert Counter(box[3] for box in gates) == {0.45: 8, 0.60: 4}
    assert Counter(box[3] for box in base_gates) == {0.60: 12}
    for old, new in zip(base_gates, gates):
        assert old[0] == new[0] and old[2] == new[2] == 0.06
        assert abs((old[1] + old[3] / 2) - (new[1] + new[3] / 2)) < 1e-6
    assert shapes["nwell"] == base_shapes["nwell"]
    assert shapes["diffusion_contacts"] == base_shapes["diffusion_contacts"]
    assert (baseline.width, baseline.height, baseline.supply_rail_extension) == (
        narrow.width, narrow.height, narrow.supply_rail_extension)
    assert _pin_signature(default) == _pin_signature(baseline) == _pin_signature(narrow)
    for lvs in (False, True):
        base_devices = _spice_devices(baseline, root, "baseline", lvs)
        devices = _spice_devices(narrow, root, "narrow", lvs)
        assert [d[0] for d in devices] == [d[0] for d in base_devices]
        assert Counter(d[1] for d in devices) == Counter(box[3] for box in gates)
        assert all(d[2] == 0.06 for d in devices)
        assert all(d[1] == 0.60 for d in base_devices)
        assert all(d[1] == 0.45 for d in devices[:8])
        assert all(d[1] == 0.60 for d in devices[8:])


def _check_cache_and_mask(factory):
    baseline = factory.create(module_type="write_driver_array", columns=4, word_size=2)
    narrow = factory.create(module_type="write_driver_array", columns=4, word_size=2,
                            write_driver_strength=0.75)
    assert baseline is not narrow and baseline.driver is not narrow.driver
    assert narrow is factory.create(module_type="write_driver_array", columns=4, word_size=2,
                                     write_driver_strength=0.75)
    mask = factory.create(module_type="write_mask_and_array", columns=8, word_size=8,
                          write_size=4, write_driver_strength=0.75)
    assert mask.driver is narrow.driver, "mask measurement must use the exact sized leaf"
    assert baseline.driver.write_driver_strength == 1.0
    assert narrow.driver.write_driver_strength == 0.75
    for bad in (0, 0.750001, 0.9, 1.1, float("nan"), float("inf"), True, "0.75"):
        for module, kwargs in (("write_driver", {}),
                               ("write_driver_array", {"columns": 4, "word_size": 2}),
                               ("write_mask_and_array", {"columns": 8, "word_size": 8, "write_size": 4})):
            _expect_value_error(lambda: factory.create(module_type=module,
                                                       write_driver_strength=bad, **kwargs))


def _check_generic(factory):
    default = factory.create(module_type="write_driver_array", columns=4, word_size=2)
    explicit = factory.create(module_type="write_driver_array", columns=4, word_size=2,
                              write_driver_strength=1.0)
    assert default.driver is explicit.driver
    assert default.driver.__class__.__module__ == "openram.modules.write_driver"
    mask = factory.create(module_type="write_mask_and_array", columns=8, word_size=8, write_size=4)
    assert mask.driver is default.driver
    for module, kwargs in (("write_driver_array", {"columns": 4, "word_size": 2}),
                           ("write_mask_and_array", {"columns": 8, "word_size": 8, "write_size": 4})):
        _expect_value_error(lambda: factory.create(module_type=module,
                                                   write_driver_strength=0.75, **kwargs))


def _check_delay(factory, root):
    signatures = []
    widths = []
    for fanout in (4, 3):
        chain = factory.create(module_type="delay_chain", fanout_list=[fanout] * 9)
        assert len(chain.driver_inst_list) == 9
        assert all(len(loads) == fanout for loads in chain.load_inst_map.values())
        path = root / ("delay{}.sp".format(fanout))
        chain.sp_write(str(path))
        flattened = re.sub(r"\n\+\s*", " ", path.read_text())
        drivers = re.findall(r"^Xdinv\d+\s+(.+)$", flattened, re.MULTILINE)
        loads = re.findall(r"^Xdload_\d+_\d+\s+(.+)$", flattened, re.MULTILINE)
        assert len(drivers) == 9 and len(loads) == 9 * fanout
        for stage, driver in enumerate(drivers):
            output = "out" if stage == 8 else "dout_{}".format(stage + 1)
            assert driver.split()[:2] == ["in" if stage == 0 else "dout_{}".format(stage), output]
            assert sum(load.split()[0] == output for load in loads) == fanout
        signatures.append(loads)
        widths.append(chain.width)
    assert signatures[0] != signatures[1] and widths[0] > widths[1]


def _check_option_path(factory):
    from openram import OPTS
    from openram.sram_config import sram_config
    OPTS.write_driver_strength = 0.75
    config = sram_config(word_size=8, num_words=16, words_per_row=1, write_size=4)
    port = factory.create(module_type="port_data", sram_config=config, port=0, has_rbl=True)
    assert port.write_driver_array.driver.write_driver_strength == 0.75
    assert port.write_mask_and_array.driver is port.write_driver_array.driver


class SizingGeometryTests(unittest.TestCase):
    def run_case(self, case):
        with tempfile.TemporaryDirectory(prefix="sizing_pkg_") as directory:
            os.symlink(REPO, Path(directory) / "openram")
            env = dict(os.environ, OPENRAM_HOME=str(REPO / "compiler"),
                       OPENRAM_TECH=str(REPO / "technology"))
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (directory, env.get("PYTHONPATH", ""))))
            result = subprocess.run([sys.executable, __file__, "--case", case],
                                    cwd=REPO, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_output_only_gds_and_both_spice_views_preserve_baseline(self):
        self.run_case("geometry")

    def test_factory_isolation_mask_measurement_and_finite_strengths(self):
        self.run_case("cache")

    def test_generic_library_baseline_and_unsupported_strength(self):
        self.run_case("generic")

    def test_real_delay_chain_stage_connections_and_load_geometry(self):
        self.run_case("delay")

    def test_option_reaches_port_driver_and_mask(self):
        self.run_case("option_path")


class SizingMaterializationTests(unittest.TestCase):

    def test_conflicting_destination_or_changed_config_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            unrelated = Path(directory) / "existing.txt"
            unrelated.write_text("preserve")
            with self.assertRaises(ValueError):
                sizing.materialize(directory)
            self.assertEqual(unrelated.read_text(), "preserve")
        with tempfile.TemporaryDirectory() as directory:
            _, manifest = sizing.materialize(directory)
            config = Path(manifest["variants"][0]["config"]["path"])
            config.write_text("word_size = 99\n")
            with self.assertRaises(ValueError):
                sizing.materialize(directory)
            self.assertEqual(config.read_text(), "word_size = 99\n")

    def test_preexisting_generator_output_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path, manifest = sizing.materialize(directory)
            output = Path(manifest["variants"][2]["output_dir"])
            output.mkdir(parents=True)
            sentinel = output / "keep"
            sentinel.write_text("unrelated output")
            with self.assertRaises(ValueError):
                sizing.generate(path, manifest)
            self.assertEqual(sentinel.read_text(), "unrelated output")
            self.assertTrue(all(v["status"] == "materialized" for v in manifest["variants"]))
            self.assertFalse(any(Path(directory).glob("*.launcher.log")))

    def test_nonexperiment_tuple_is_rejected(self):
        for point in ((7, 4, 1.0), (9, 3, 0.75), (9, 4, 0.5)):
            with self.assertRaises(ValueError):
                sizing.variant_name(*point)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--case":
        _geometry_case(sys.argv[2])
    else:
        unittest.main()
