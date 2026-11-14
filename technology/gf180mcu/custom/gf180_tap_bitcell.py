#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California
# All rights reserved.
#
from openram import debug
from openram.modules import bitcell_base
from openram.tech import cell_properties as props
from .gf180_bitcell import collapse_duplicate_layer_pins


class gf180_tap_bitcell(bitcell_base):
    """Dummy-column bitcell that carries real n+/p+ well taps.

    The physical cell is ``cell1rw_tap``: the same 6T bitcell plus one n+ tap
    in the nwell (to VDD) and one p+ tap in the pwell (to GND) drawn in the
    left overhang.  It is used only for the outer dummy column of the array
    (``capped_replica_bitcell_array.row_cap_left``), where the wells are
    continuous with the array but the space left of the column is free.

    The taps add no devices, so the five-pin netlist is identical to
    ``cell1rw``; electrically they only bias the wells.  The cell name is
    passed explicitly because ``cell_properties.names`` keys the stock
    ``bitcell_1port`` name and the factory names this class after the
    technology override.
    """

    def __init__(self, name=""):
        bitcell_base.__init__(self, name,
                              cell_name="cell1rw_tap",
                              prop=props.bitcell_1port)
        collapse_duplicate_layer_pins(self)
        debug.info(2, "Create gf180 tap bitcell")
