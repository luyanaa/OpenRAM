# See LICENSE for licensing information.
#
# Declarative models for the chip assembly layer.
#
# The models are plain dataclasses (no pydantic dependency) with explicit
# validation errors.  Unknown keys are rejected so that a typo in a YAML
# config fails in ``check`` instead of being silently ignored in ``gen``.

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


class ConfigError(Exception):
    """Raised when a chip/macro/slot configuration is invalid."""


def _require(d, key, where):
    if key not in d:
        raise ConfigError("{}: missing required key '{}'".format(where, key))
    return d[key]


def _check_keys(d, allowed, where):
    if not isinstance(d, dict):
        raise ConfigError("{}: expected a mapping, got {!r}".format(where, type(d).__name__))
    unknown = sorted(set(d) - set(allowed))
    if unknown:
        raise ConfigError("{}: unknown key(s) {}".format(where, ", ".join(unknown)))


def _expand(path, base=None):
    """Expand environment variables and make a path absolute.

    Relative paths are resolved against ``base`` when given, otherwise
    against the current working directory.
    """
    if path is None:
        return None
    path = os.path.expanduser(os.path.expandvars(str(path)))
    if base and not os.path.isabs(path):
        path = os.path.join(base, path)
    return os.path.abspath(path)


def _as_int(value, where, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("{}: expected an integer, got {!r}".format(where, value))
    if minimum is not None and value < minimum:
        raise ConfigError("{}: must be >= {}, got {}".format(where, minimum, value))
    return value


def _as_float(value, where, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("{}: expected a number, got {!r}".format(where, value))
    value = float(value)
    if minimum is not None and value < minimum:
        raise ConfigError("{}: must be >= {}, got {}".format(where, minimum, value))
    if maximum is not None and value > maximum:
        raise ConfigError("{}: must be <= {}, got {}".format(where, maximum, value))
    return value


def _power_of_two(value):
    return value > 0 and (value & (value - 1)) == 0


# ---------------------------------------------------------------------------
# Macro
# ---------------------------------------------------------------------------

@dataclass
class MacroSpec:
    """One memory macro that a chip can instantiate.

    ``ports`` maps the generic names used by the generated wrappers to the
    actual macro pin names:
        clk, cs_n, we_n, addr, din, dout, vdd, vss, wmask (optional)
    """

    name: str
    source: str
    gds: str
    lef: str
    width_um: float
    height_um: float
    words: int
    data_width: int
    addr_width: int
    ports: Dict[str, str]
    lib: Optional[str] = None
    verilog: Optional[str] = None
    liberty_corners: Dict[str, str] = field(default_factory=dict)
    description: Optional[str] = None
    class_name: Optional[str] = None
    # Free-form caveat printed next to the Liberty path in the datasheet.
    # Used to state that a .lib is a stand-in rather than a characterization
    # of the exact macro GDS.
    lib_note: Optional[str] = None

    DEFAULT_PORTS = {
        "clk": "clk0",
        "cs_n": "csb0",
        "we_n": "web0",
        "addr": "addr0",
        "din": "din0",
        "dout": "dout0",
        "vdd": "vccd1",
        "vss": "vssd1",
    }

    @classmethod
    def from_dict(cls, name, d, where=None):
        where = where or "macro '{}'".format(name)
        if not isinstance(d, dict):
            raise ConfigError("{}: expected a mapping".format(where))
        d = dict(d)
        base = d.pop("_base", None)
        allowed = {"source", "gds", "lef", "lib", "verilog", "width_um",
                   "height_um", "words", "data_width", "addr_width", "ports",
                   "liberty_corners", "description", "class_name", "lib_note"}
        _check_keys(d, allowed, where)
        ports = dict(cls.DEFAULT_PORTS)
        ports.update(d.get("ports") or {})
        for key in ("clk", "cs_n", "we_n", "addr", "din", "dout", "vdd", "vss"):
            if not ports.get(key):
                raise ConfigError("{}: ports.{} must be a non-empty pin name".format(where, key))

        widths = _as_int(_require(d, "words", where), where + ".words", 1)
        data_width = _as_int(_require(d, "data_width", where), where + ".data_width", 1)
        addr_width = _as_int(_require(d, "addr_width", where), where + ".addr_width", 1)
        if not _power_of_two(widths):
            raise ConfigError("{}: words ({}) must be a power of two".format(where, widths))
        if widths != (1 << addr_width):
            raise ConfigError(
                "{}: words ({}) does not match addr_width ({})".format(
                    where, widths, addr_width))

        source = d.get("source", "openram")
        if source not in ("openram", "pdk", "custom"):
            raise ConfigError("{}: source must be openram|pdk|custom, got {!r}".format(
                where, source))

        corners = {}
        for corner, path in (d.get("liberty_corners") or {}).items():
            corners[corner] = _expand(path, base)

        return cls(
            name=name,
            source=source,
            gds=_expand(_require(d, "gds", where), base),
            lef=_expand(_require(d, "lef", where), base),
            lib=_expand(d.get("lib"), base),
            verilog=_expand(d.get("verilog"), base),
            width_um=_as_float(_require(d, "width_um", where), where + ".width_um", 0.001),
            height_um=_as_float(_require(d, "height_um", where), where + ".height_um", 0.001),
            words=widths,
            data_width=data_width,
            addr_width=addr_width,
            ports=ports,
            liberty_corners=corners,
            description=d.get("description"),
            class_name=d.get("class_name") or name,
            lib_note=d.get("lib_note"),
        )

    @property
    def size_um(self):
        return (self.width_um, self.height_um)

    def missing_files(self):
        """Return a list of declared but missing artifact paths."""
        missing = []
        for label, path in (("gds", self.gds), ("lef", self.lef),
                            ("lib", self.lib), ("verilog", self.verilog)):
            if path and not os.path.exists(path):
                missing.append((label, path))
        for corner, path in self.liberty_corners.items():
            if path and not os.path.exists(path):
                missing.append(("lib:{}".format(corner), path))
        return missing

    def to_dict(self):
        return {
            "name": self.name,
            "source": self.source,
            "gds": self.gds,
            "lef": self.lef,
            "lib": self.lib,
            "verilog": self.verilog,
            "width_um": self.width_um,
            "height_um": self.height_um,
            "words": self.words,
            "data_width": self.data_width,
            "addr_width": self.addr_width,
            "ports": dict(self.ports),
            "liberty_corners": dict(self.liberty_corners),
            "description": self.description,
            "class_name": self.class_name,
            "lib_note": self.lib_note,
        }


# ---------------------------------------------------------------------------
# Slot
# ---------------------------------------------------------------------------

@dataclass
class Inset:
    left: float = 0.0
    bottom: float = 0.0
    right: float = 0.0
    top: float = 0.0

    @classmethod
    def from_dict(cls, d, where):
        _check_keys(d, {"left", "bottom", "right", "top"}, where)
        return cls(
            left=_as_float(d.get("left", 0), where + ".left", 0),
            bottom=_as_float(d.get("bottom", 0), where + ".bottom", 0),
            right=_as_float(d.get("right", 0), where + ".right", 0),
            top=_as_float(d.get("top", 0), where + ".top", 0),
        )


@dataclass
class IoBudget:
    dvdd: int = 0
    dvss: int = 0
    input: int = 0
    bidir: int = 0
    analog: int = 0

    @classmethod
    def from_dict(cls, d, where):
        _check_keys(d, {"dvdd", "dvss", "input", "bidir", "analog"}, where)
        kwargs = {}
        for key in ("dvdd", "dvss", "input", "bidir", "analog"):
            kwargs[key] = _as_int(d.get(key, 0), where + "." + key, 0)
        return cls(**kwargs)

    @property
    def signal_pins(self):
        return self.input + self.bidir + self.analog


@dataclass
class SlotSpec:
    name: str
    die_width: float
    die_height: float
    inset: Inset
    io_budget: IoBudget
    reserved_area_um2: float = 0.0
    description: Optional[str] = None

    @classmethod
    def from_dict(cls, name, d, where=None):
        where = where or "slot '{}'".format(name)
        _check_keys(d, {"die", "core", "io_budget", "reserved_area_um2",
                        "description"}, where)
        die = _require(d, "die", where)
        _check_keys(die, {"width", "height"}, where + ".die")
        core = d.get("core") or {}
        _check_keys(core, {"inset"}, where + ".core")
        inset = Inset.from_dict(core.get("inset") or {}, where + ".core.inset")
        io = IoBudget.from_dict(_require(d, "io_budget", where), where + ".io_budget")
        return cls(
            name=name,
            die_width=_as_float(_require(die, "width", where + ".die"),
                                where + ".die.width", 0.001),
            die_height=_as_float(_require(die, "height", where + ".die"),
                                 where + ".die.height", 0.001),
            inset=inset,
            io_budget=io,
            reserved_area_um2=_as_float(d.get("reserved_area_um2", 0),
                                        where + ".reserved_area_um2", 0),
            description=d.get("description"),
        )

    @property
    def core_width(self):
        return self.die_width - self.inset.left - self.inset.right

    @property
    def core_height(self):
        return self.die_height - self.inset.bottom - self.inset.top

    @property
    def core_area(self):
        return self.core_width * self.core_height

    def librelane_areas(self):
        die = [0.0, 0.0, self.die_width, self.die_height]
        core = [self.inset.left, self.inset.bottom,
                self.die_width - self.inset.right,
                self.die_height - self.inset.top]
        return die, core

    def to_dict(self):
        return {
            "name": self.name,
            "die": {"width": self.die_width, "height": self.die_height},
            "core": {"inset": {"left": self.inset.left,
                               "bottom": self.inset.bottom,
                               "right": self.inset.right,
                               "top": self.inset.top}},
            "io_budget": {"dvdd": self.io_budget.dvdd,
                          "dvss": self.io_budget.dvss,
                          "input": self.io_budget.input,
                          "bidir": self.io_budget.bidir,
                          "analog": self.io_budget.analog},
            "reserved_area_um2": self.reserved_area_um2,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# Chip configuration
# ---------------------------------------------------------------------------

@dataclass
class ChipConfig:
    name: str
    slot: str
    macro: str
    count: Union[int, str] = "auto"
    rows: Optional[int] = None
    cols: Optional[int] = None
    halo_um: float = 10.0
    density_target_pct: Optional[int] = None
    clock_frequency_mhz: float = 25.0
    core_voltage: float = 3.3
    output_routing: str = "mux"
    write_mask: bool = False
    registered_output: bool = False
    description: Optional[str] = None
    reset: bool = True
    reserved_area_um2: Optional[float] = None
    signoff: bool = True
    # Block signal routing on the macro array's top metal layer.  The chip PDN
    # runs its top-layer straps across the whole core, i.e. straight over the
    # macros; without the blockage the router happily crosses those straps
    # inside a macro (the macro's own LEF stops at Metal4, so its Metal5 is
    # "empty" and therefore routable).  Opt-in because a design that has a
    # routing channel over the array does not need it.
    macro_top_obstruction: bool = False
    # Hold slack margin for the placement/global-route resizers.  The default
    # 0.35 ns is enough for the 16x8 array; a taller macro stack (more clock
    # insertion delay through the array) needs a larger margin.
    hold_slack_margin_ns: Optional[float] = None

    ALLOWED_KEYS = {"chip", "slot", "memory", "interface", "clock", "power",
                    "io", "features", "halo_um", "density_target_pct",
                    "reserved_area_um2", "signoff", "macro_top_obstruction",
                    "hold_slack_margin_ns"}

    @classmethod
    def from_dict(cls, d, where="chip config"):
        _check_keys(d, cls.ALLOWED_KEYS, where)

        chip = _require(d, "chip", where)
        _check_keys(chip, {"name", "description"}, where + ".chip")
        name = _require(chip, "name", where + ".chip")
        if not isinstance(name, str) or not name:
            raise ConfigError("{}.chip.name must be a non-empty string".format(where))

        memory = _require(d, "memory", where)
        _check_keys(memory, {"macro", "count", "arrangement", "rows", "cols"},
                    where + ".memory")
        macro = _require(memory, "macro", where + ".memory")
        count = memory.get("count", "auto")
        if count != "auto":
            count = _as_int(count, where + ".memory.count", 1)
        rows = memory.get("rows")
        cols = memory.get("cols")
        if rows is not None:
            rows = _as_int(rows, where + ".memory.rows", 1)
        if cols is not None:
            cols = _as_int(cols, where + ".memory.cols", 1)
        arrangement = memory.get("arrangement") or {}
        _check_keys(arrangement, {"prefer", "orientation"},
                    where + ".memory.arrangement")
        prefer = arrangement.get("prefer", "square")
        if prefer not in ("rows", "columns", "square"):
            raise ConfigError(
                "{}.memory.arrangement.prefer must be rows|columns|square".format(where))

        interface = d.get("interface") or {}
        _check_keys(interface, {"scheme", "unified_bus"}, where + ".interface")
        scheme = interface.get("scheme", "unified_bus")
        if scheme != "unified_bus":
            raise ConfigError(
                "{}.interface.scheme '{}' is not implemented; only unified_bus "
                "is supported".format(where, scheme))
        bus = interface.get("unified_bus") or {}
        _check_keys(bus, {"data_width", "output_routing", "write_mask",
                          "registered_output"}, where + ".interface.unified_bus")
        data_width = bus.get("data_width")
        if data_width is not None:
            data_width = _as_int(data_width, where + ".interface.unified_bus.data_width", 1)
        output_routing = bus.get("output_routing", "mux")
        if output_routing not in ("mux", "tristate", "tristate_registered"):
            raise ConfigError(
                "{}.interface.unified_bus.output_routing must be "
                "mux|tristate|tristate_registered".format(where))
        write_mask = bool(bus.get("write_mask", False))
        registered_output = bool(bus.get("registered_output", False))

        clock = d.get("clock") or {}
        _check_keys(clock, {"frequency_mhz"}, where + ".clock")
        freq = _as_float(clock.get("frequency_mhz", 25.0), where + ".clock.frequency_mhz", 0.001)

        power = d.get("power") or {}
        _check_keys(power, {"core_voltage"}, where + ".power")
        voltage = _as_float(power.get("core_voltage", 3.3), where + ".power.core_voltage", 0.1)

        cfg = cls(
            name=name,
            slot=_require(d, "slot", where),
            macro=macro,
            count=count,
            rows=rows,
            cols=cols,
            halo_um=_as_float(d.get("halo_um", 10.0), where + ".halo_um", 0.0),
            density_target_pct=d.get("density_target_pct"),
            clock_frequency_mhz=freq,
            core_voltage=voltage,
            output_routing=output_routing,
            write_mask=write_mask,
            registered_output=registered_output,
            description=chip.get("description"),
            reserved_area_um2=(_as_float(d["reserved_area_um2"],
                                         where + ".reserved_area_um2", 0.0)
                               if "reserved_area_um2" in d else None),
            signoff=bool(d.get("signoff", True)),
            macro_top_obstruction=bool(d.get("macro_top_obstruction", False)),
            hold_slack_margin_ns=(_as_float(d["hold_slack_margin_ns"],
                                            where + ".hold_slack_margin_ns", 0.0)
                                  if "hold_slack_margin_ns" in d else None),
        )
        cfg._prefer = prefer
        cfg._data_width = data_width
        return cfg

    @property
    def data_width(self):
        return getattr(self, "_data_width", None)

    @property
    def prefer(self):
        return getattr(self, "_prefer", "square")

    def to_dict(self):
        return {
            "chip": {"name": self.name, "description": self.description},
            "slot": self.slot,
            "memory": {"macro": self.macro, "count": self.count,
                       "rows": self.rows, "cols": self.cols,
                       "arrangement": {"prefer": self.prefer}},
            "interface": {"scheme": "unified_bus",
                          "unified_bus": {
                              "data_width": self.data_width,
                              "output_routing": self.output_routing,
                              "write_mask": self.write_mask,
                              "registered_output": self.registered_output}},
            "clock": {"frequency_mhz": self.clock_frequency_mhz},
            "power": {"core_voltage": self.core_voltage},
            "halo_um": self.halo_um,
            "density_target_pct": self.density_target_pct,
            "reserved_area_um2": self.reserved_area_um2,
            "macro_top_obstruction": self.macro_top_obstruction,
            "hold_slack_margin_ns": self.hold_slack_margin_ns,
        }
