#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California
# All rights reserved.
#
from openram import debug
from openram.modules import bitcell_base
from openram.modules.replica_bitcell_1port import replica_bitcell_1port
from openram.tech import cell_properties as props
from .gf180_bitcell import collapse_duplicate_layer_pins


class gf180_replica_bitcell(replica_bitcell_1port):
    """Replica bitcell backed by the same physical ``cell1rw`` GDS.

    The replica column biases the same 6T cell differently, but the layout is
    identical.  The cell name is passed explicitly because the factory names
    this class after the technology override rather than after the stock
    ``replica_bitcell_1port`` key used by ``cell_properties.names``.  The
    pin-map normalisation collapses the duplicated ``WL`` labels to the m3
    abutment shape.
    """

    def __init__(self, name=""):
        bitcell_base.__init__(self, name,
                              cell_name="cell1rw",
                              prop=props.bitcell_1port)
        collapse_duplicate_layer_pins(self)
        debug.info(2, "Create gf180 replica bitcell")
