"""Native ICsprout55 2R1W cell/netlist boundary.

OpenRAM's parameterized ``pbitcell`` can construct one write-only and two
read-only access paths.  The generic bank and port-data layout generators do
not support three physical ports, so this module deliberately stops at a
standalone cell/netlist smoke and exposes a fail-closed full-macro boundary.

The CPU-facing role order is ``[R, R, W]``.  OpenRAM's physical port order is
``[W, R, R]`` for ``num_rw_ports=0, num_w_ports=1, num_r_ports=2``.  The map
is explicit; no signal-name inference is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys

SIM = Path(__file__).resolve().parent
TECH = SIM.parent
ROOT = TECH.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compiler.architecture.register_file import (
    UnsupportedRegisterFileLayout,
    reject_unsupported_layout,
)

CONFIG_PATH = TECH / "configs" / "ics55_2r1w_native.py"
NATIVE_PORT_ROLES = ("R", "R", "W")
COMPILER_PORT_ROLES = ("W", "R", "R")
COMPILER_PORT_MAP = {"write": 0, "read0": 1, "read1": 2}
EXPECTED_NATIVE_PINS = ("bl0", "br0", "bl1", "br1", "bl2", "br2",
                        "wl0", "wl1", "wl2", "vdd", "gnd")
EXPECTED_NATIVE_PIN_TYPES = {
    "bl0": "INPUT", "br0": "INPUT", "bl1": "OUTPUT", "br1": "OUTPUT",
    "bl2": "OUTPUT", "br2": "OUTPUT", "wl0": "INPUT", "wl1": "INPUT",
    "wl2": "INPUT", "vdd": "POWER", "gnd": "GROUND",
}
MANIFEST_SCHEMA = "openram-native-register-file-cell/1"


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ref(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": _digest(path)}


def _config_values(config_path):
    config_path = Path(config_path).expanduser().resolve()
    if not config_path.is_file():
        raise ValueError("native register-file config is missing: " + str(config_path))
    return config_path, runpy.run_path(str(config_path))


def validate_native_config(config):
    """Validate the explicit native config and return its role contract."""
    required = {
        "tech_name", "bitcell", "stdcell_library", "word_size", "num_words",
        "register_file_backend", "register_file_port_roles",
        "register_file_compiler_port_roles", "register_file_compiler_port_map",
        "num_rw_ports", "num_w_ports", "num_r_ports", "netlist_only",
    }
    missing = sorted(required - set(config))
    if missing:
        raise ValueError("native register-file config is missing: " + ", ".join(missing))
    if config["tech_name"] != "icsprout55" or config["bitcell"] != "pbitcell":
        raise ValueError("native register-file config must select ICsprout55 pbitcell")
    if config["register_file_backend"] != "native-pbitcell":
        raise ValueError("unsupported native register-file backend")
    for name, expected in (("register_file_port_roles", NATIVE_PORT_ROLES),
                           ("register_file_compiler_port_roles", COMPILER_PORT_ROLES)):
        value = config[name]
        if type(value) not in (list, tuple) or any(type(role) is not str for role in value):
            raise ValueError(name + " must be an explicit list/tuple of role strings")
        if tuple(value) != expected:
            label = "native register-file roles" if name == "register_file_port_roles" else "native compiler roles"
            raise ValueError(label + " must be exactly " + str(list(expected)))
    if config["register_file_compiler_port_map"] != COMPILER_PORT_MAP:
        raise ValueError("native compiler port map is inconsistent")
    expected_counts = {"num_rw_ports": 0, "num_w_ports": 1, "num_r_ports": 2}
    for name, expected in expected_counts.items():
        if config[name] != expected:
            raise ValueError("native register-file {} must be {}".format(name, expected))
    if type(config["word_size"]) is not int or config["word_size"] < 1:
        raise ValueError("native register-file word_size must be a positive integer")
    if type(config["num_words"]) is not int or config["num_words"] < 1:
        raise ValueError("native register-file num_words must be a positive integer")
    if config["netlist_only"] is not True:
        raise ValueError("native register-file config must remain netlist_only")
    return {
        "backend": config["register_file_backend"],
        "cpu_port_roles": list(NATIVE_PORT_ROLES),
        "compiler_port_roles": list(COMPILER_PORT_ROLES),
        "compiler_port_map": dict(COMPILER_PORT_MAP),
        "word_size": config["word_size"],
        "num_words": config["num_words"],
    }


def native_config(config_path=CONFIG_PATH):
    """Load and validate the checked-in native 2R1W configuration."""
    path, config = _config_values(config_path)
    contract = validate_native_config(config)
    contract["config"] = _ref(path)
    return contract


def reject_native_full_macro_layout(total_ports=3):
    """Reject the unsupported three-physical-port full-macro layout."""
    reject_unsupported_layout(total_ports)


def generate_native_pbitcell(out_dir, config_path=CONFIG_PATH):
    """Generate a standalone native pbitcell SPICE bundle.

    This function intentionally does not instantiate ``sram`` or ``bank``.
    The returned manifest records the explicit role map and generated cell
    netlist, but does not represent a CPU-facing full macro.
    """
    config_path, config = _config_values(config_path)
    contract = validate_native_config(config)
    try:
        reject_native_full_macro_layout()
    except UnsupportedRegisterFileLayout as exc:
        full_macro_error = {
            "type": type(exc).__name__, "code": exc.code,
            "message": str(exc),
        }
    else:
        raise ValueError("three-port native full-macro rejection did not fire")
    out_dir = Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    import common
    common.make_openram_package()
    import openram
    from openram import OPTS
    from openram.sram_factory import factory

    spice_path = out_dir / "native_2r1w_pbitcell.sp"
    manifest_path = out_dir / "native_2r1w_pbitcell.json"
    initialized = False
    try:
        openram.init_openram(str(config_path), is_unit_test=True)
        initialized = True
        OPTS.output_path = str(out_dir) + "/"
        OPTS.output_name = "native_2r1w_pbitcell"
        if (OPTS.num_rw_ports, OPTS.num_w_ports, OPTS.num_r_ports) != (0, 1, 2):
            raise ValueError("OpenRAM options do not match native 2R1W port counts")
        cell = factory.create(module_type="pbitcell", module_name="native_2r1w_pbitcell")
        cell.sp_write(str(spice_path), lvs=True)
        pins = list(cell.pins)
        pin_types = {pin: cell.get_pin_type(pin) for pin in pins}
    finally:
        if initialized:
            openram.end_openram()
    if tuple(pins) != EXPECTED_NATIVE_PINS:
        raise ValueError("generated native pbitcell pins do not match the explicit role contract")
    if pin_types != EXPECTED_NATIVE_PIN_TYPES:
        raise ValueError("generated native pbitcell pin directions do not match the explicit role contract")

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "scope": "standalone native pbitcell/netlist only",
        "signoff": False,
        "contract": contract,
        "config": _ref(config_path),
        "netlist": _ref(spice_path),
        "pins": pins,
        "pin_types": pin_types,
        "physical_port_count": 3,
        "full_macro": False,
        "full_macro_status": "unsupported-register-file-layout",
        "full_macro_error": full_macro_error,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args(argv)
    try:
        manifest = generate_native_pbitcell(args.out_dir, args.config)
    except (OSError, ValueError, UnsupportedRegisterFileLayout) as exc:
        print("native register-file backend: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
