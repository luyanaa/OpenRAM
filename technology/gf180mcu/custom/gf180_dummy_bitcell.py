#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California
# All rights reserved.
#
from openram import debug
from openram.modules import bitcell_base
from openram.modules.dummy_bitcell_1port import dummy_bitcell_1port
from openram.tech import cell_properties as props
from .gf180_bitcell import collapse_duplicate_layer_pins


class gf180_dummy_bitcell(dummy_bitcell_1port):
    """Dummy bitcell backed by the same physical ``cell1rw`` GDS.

    Only the bitline/wordline load matters, so the cell stays identical.  The
    cell name is passed explicitly because the factory names this class after
    the technology override.  The pin-map normalisation keeps ``WL``
    unambiguous like the main bitcell.
    """

    def __init__(self, name=""):
        bitcell_base.__init__(self, name,
                              cell_name="cell1rw",
                              prop=props.bitcell_1port)
        collapse_duplicate_layer_pins(self)
        debug.info(2, "Create gf180 dummy bitcell")
