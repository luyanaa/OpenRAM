# See LICENSE for licensing information.
#
# Chip assembly layer for the gf180mcu memory generator.
#
# This package turns one or more generated memory macros (or foundry hard
# macros) into a chip-level project: a slot/fit calculation, a pad-level
# Verilog top, and a LibreLane configuration.  It deliberately reuses the
# OpenRAM macro artifacts (GDS/LEF/lib/Verilog) instead of re-implementing
# memory generation.

from .models import ChipConfig, MacroSpec, SlotSpec, ConfigError
from .db import MacroDB, SlotDB, load_chip_config
from .fit import FitResult, calculate_fit
from .generate import generate_all

__all__ = [
    "ChipConfig",
    "MacroSpec",
    "SlotSpec",
    "ConfigError",
    "MacroDB",
    "SlotDB",
    "load_chip_config",
    "FitResult",
    "calculate_fit",
    "generate_all",
]
