# See LICENSE for licensing information.
#
# YAML database and config loading for the chip assembly layer.

import os
import re
from typing import Dict, Optional

import yaml

from .models import ChipConfig, ConfigError, MacroSpec, SlotSpec

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dbfiles")
SLOT_DB = os.path.join(DB_DIR, "slots.yaml")
MACRO_DB = os.path.join(DB_DIR, "macros.yaml")

_ENV_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}")


def _expand_env(value):
    """Expand ${VAR} and ${VAR:-default} in a string."""
    if not isinstance(value, str):
        return value

    def repl(match):
        name = match.group("name")
        default = match.group("default")
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        return match.group(0)

    return _ENV_RE.sub(repl, value)


def _load_yaml(path):
    if not os.path.exists(path):
        raise ConfigError("YAML file not found: {}".format(path))
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError("{}: expected a top-level mapping".format(path))
    return data


def _walk_strings(node):
    if isinstance(node, str):
        return _expand_env(node)
    if isinstance(node, list):
        return [_walk_strings(item) for item in node]
    if isinstance(node, dict):
        return {key: _walk_strings(value) for key, value in node.items()}
    return node


class SlotDB:
    """Dictionary of slot specifications loaded from slots.yaml."""

    def __init__(self, path=SLOT_DB):
        self.path = path
        data = _walk_strings(_load_yaml(path))
        slots = data.get("slots")
        if not isinstance(slots, dict) or not slots:
            raise ConfigError("{}: no 'slots' mapping".format(path))
        self._slots = {}
        for name, spec in slots.items():
            self._slots[name] = SlotSpec.from_dict(name, spec)

    def names(self):
        return sorted(self._slots)

    def get(self, name):
        if name not in self._slots:
            raise ConfigError(
                "unknown slot '{}'; available: {}".format(name, ", ".join(self.names())))
        return self._slots[name]

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name):
        return name in self._slots


class MacroDB:
    """Dictionary of macro specifications loaded from macros.yaml."""

    def __init__(self, path=MACRO_DB):
        self.path = path
        data = _walk_strings(_load_yaml(path))
        macros = data.get("macros")
        if not isinstance(macros, dict) or not macros:
            raise ConfigError("{}: no 'macros' mapping".format(path))
        base = os.path.dirname(os.path.abspath(path))
        self._macros = {}
        for name, spec in macros.items():
            spec = dict(spec)
            spec["_base"] = base
            self._macros[name] = MacroSpec.from_dict(name, spec)

    def names(self):
        return sorted(self._macros)

    def get(self, name):
        if name not in self._macros:
            raise ConfigError(
                "unknown macro '{}'; available: {}".format(name, ", ".join(self.names())))
        return self._macros[name]

    def __getitem__(self, name):
        return self.get(name)

    def __contains__(self, name):
        return name in self._macros


def load_chip_config(path):
    """Parse and validate a chip YAML config, checking the macro/slot DB."""
    data = _walk_strings(_load_yaml(path))
    cfg = ChipConfig.from_dict(data, where=path)

    slots = SlotDB()
    macros = MacroDB()
    slot = slots.get(cfg.slot)
    macro = macros.get(cfg.macro)

    if macro.data_width != (cfg.data_width or macro.data_width):
        raise ConfigError(
            "chip config {}: interface data_width ({}) does not match macro "
            "data_width ({}); width banking is not implemented".format(
                path, cfg.data_width, macro.data_width))
    if cfg.rows is not None and cfg.cols is not None:
        if cfg.count != "auto" and cfg.rows * cfg.cols != cfg.count:
            raise ConfigError(
                "chip config {}: rows*cols ({}x{}) != count ({})".format(
                    path, cfg.rows, cfg.cols, cfg.count))
    return cfg, slot, macro


def available_macros():
    return MacroDB()


def available_slots():
    return SlotDB()
