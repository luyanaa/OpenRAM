"""Behavioral regression for technology-specific write-driver rail endpoints."""
import os
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _run_geometry_case(tech_name):
    if tech_name not in ("icsprout55", "freepdk45"):
        raise ValueError("Unsupported write-driver geometry case: {}".format(tech_name))

    import openram
    from openram.sram_factory import factory

    with tempfile.TemporaryDirectory(prefix="write_driver_rails_") as output_dir:
        config_path = os.path.join(output_dir, "config.py")
        with open(config_path, "w") as config_file:
            config_file.write('tech_name = "{}"\n'.format(tech_name))
            config_file.write('word_size = 2\nnum_words = 16\nbitcell = "pbitcell"\n')
            config_file.write('check_lvsdrc = False\nnominal_corner_only = True\n')
            config_file.write('output_path = "{}/"\n'.format(output_dir))

        openram.init_openram(config_path, is_unit_test=True)
        try:
            array = factory.create(module_type="write_driver_array", columns=4, word_size=2)
            driver = array.driver
            if tech_name == "icsprout55":
                if not hasattr(driver, "supply_rail_extension"):
                    raise AssertionError("ICsprout55 write driver must declare its rail extension")
                extension = array.m2_pitch
                if abs(driver.supply_rail_extension - extension) > 1e-9:
                    raise AssertionError("ICsprout55 policy must remain one M2 pitch")
            else:
                if driver.__class__.__module__ != "openram.modules.write_driver":
                    raise AssertionError("freepdk45 must exercise the generic write-driver module")
                if hasattr(driver, "supply_rail_extension"):
                    raise AssertionError("generic write driver unexpectedly declares an extension")
                extension = 0

            # Assert the centers of actual emitted pin rectangles, not the input
            # rail pins or a policy-forwarding stub.
            for net in ("vdd", "gnd"):
                pins = list(array.get_pins(net))
                if len(pins) < 2:
                    raise AssertionError("{} must retain both supply-pin endpoints".format(net))
                pin_centers = [pin.cx() for pin in pins]
                left = min(pin_centers)
                right = max(pin_centers)
                if abs(left + extension) > 1e-6:
                    raise AssertionError("{} left endpoint is {}, expected {}".format(
                        net, left, -extension))
                expected_right = array.width + extension
                if abs(right - expected_right) > 1e-6:
                    raise AssertionError("{} right endpoint is {}, expected {}".format(
                        net, right, expected_right))
        finally:
            openram.end_openram()


class WriteDriverSupplyRailGeometryTests(unittest.TestCase):
    def run_technology_case(self, tech_name):
        with tempfile.TemporaryDirectory(prefix="write_driver_rails_pkg_") as package_dir:
            os.symlink(REPO_ROOT, os.path.join(package_dir, "openram"))
            env = os.environ.copy()
            env["OPENRAM_HOME"] = os.path.join(REPO_ROOT, "compiler")
            env["OPENRAM_TECH"] = os.path.join(REPO_ROOT, "technology")
            inherited_pythonpath = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = os.pathsep.join(
                value for value in (package_dir, inherited_pythonpath) if value)
            result = subprocess.run(
                [sys.executable, __file__, "--case", tech_name],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_icsprout55_keeps_one_m2_pitch_extension(self):
        self.run_technology_case("icsprout55")

    def test_generic_freepdk45_keeps_endpoints_at_array_boundary(self):
        self.run_technology_case("freepdk45")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--case":
        _run_geometry_case(sys.argv[2])
    else:
        unittest.main()
