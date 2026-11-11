#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Chip assembly driver: turns a chip YAML config plus the macro/slot database
# into a pad-level Verilog project, an SDC, a datasheet, and a LibreLane
# configuration.
#
# Usage:
#   chip_compiler.py list [macros|slots] [--json]
#   chip_compiler.py calc --slot 0p5x0p5 --macro gf180_16x8_pdn_pex [--json]
#   chip_compiler.py check <config.yaml>
#   chip_compiler.py gen <config.yaml> -o <outdir> [--only verilog,librelane,docs]

import sys

from common import *
make_openram_package()

from openram.chip.cli import main

if __name__ == "__main__":
    sys.exit(main())
