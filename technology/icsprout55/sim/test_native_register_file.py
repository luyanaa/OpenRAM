"""Contract and boundary tests for the native ICsprout55 2R1W backend."""
import json
from pathlib import Path
import tempfile
import unittest

from technology.icsprout55.sim import native_register_file as native
from compiler.architecture.register_file import UnsupportedRegisterFileLayout


class NativeRegisterFileContractTest(unittest.TestCase):
    def test_checked_in_config_has_explicit_cpu_and_compiler_role_maps(self):
        contract = native.native_config()
        self.assertEqual(contract["cpu_port_roles"], ["R", "R", "W"])
        self.assertEqual(contract["compiler_port_roles"], ["W", "R", "R"])
        self.assertEqual(contract["compiler_port_map"], {"write": 0, "read0": 1, "read1": 2})
        self.assertEqual(contract["backend"], "native-pbitcell")

    def test_config_rejects_implicit_or_wrong_port_order(self):
        path, config = native._config_values(native.CONFIG_PATH)
        del path
        config["register_file_compiler_port_roles"] = ("R", "R", "W")
        with self.assertRaisesRegex(ValueError, "compiler roles must be exactly"):
            native.validate_native_config(config)
    def test_config_rejects_string_role_map(self):
        _, config = native._config_values(native.CONFIG_PATH)
        config["register_file_port_roles"] = "RRW"
        with self.assertRaisesRegex(ValueError, "explicit list/tuple"):
            native.validate_native_config(config)

    def test_native_pin_contract_is_explicit(self):
        self.assertEqual(native.EXPECTED_NATIVE_PINS,
                         ("bl0", "br0", "bl1", "br1", "bl2", "br2",
                          "wl0", "wl1", "wl2", "vdd", "gnd"))
        self.assertEqual(native.EXPECTED_NATIVE_PIN_TYPES["bl0"], "INPUT")
        self.assertEqual(native.EXPECTED_NATIVE_PIN_TYPES["bl1"], "OUTPUT")


    def test_three_port_full_macro_fails_with_stable_error_before_layout(self):
        with self.assertRaises(UnsupportedRegisterFileLayout) as raised:
            native.reject_native_full_macro_layout()
        self.assertEqual(raised.exception.code, "unsupported-register-file-layout")
        self.assertEqual(raised.exception.total_ports, 3)
        self.assertIn("at most 2 total physical ports", str(raised.exception))

    def test_manifest_schema_is_declared_without_claiming_full_macro(self):
        self.assertEqual(native.MANIFEST_SCHEMA, "openram-native-register-file-cell/1")
        self.assertEqual(native.NATIVE_PORT_ROLES, ("R", "R", "W"))
        self.assertEqual(native.COMPILER_PORT_ROLES, ("W", "R", "R"))


if __name__ == "__main__":
    unittest.main()
