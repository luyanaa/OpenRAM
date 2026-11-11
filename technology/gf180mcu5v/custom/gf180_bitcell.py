#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2022 Regents of the University of California
# All rights reserved.
#

from openram import debug
from openram.modules import bitcell_base
from openram.tech import cell_properties as props


def collapse_duplicate_layer_pins(cell):
    """Keep a single abutment pin when a name is labelled on several layers.

    The vendor ``cell1rw`` carries the wordline twice: one label on the m3
    strap and one on the horizontal access-gate poly.  Magic merges the two by
    name, which is how the standalone cell ties the gate to the strap (there
    is no room for a via stack).  For the layout pin map, only the highest
    routing layer is a real abutment pin; leaving both makes ``get_pin("WL")``
    ambiguous.  Apply the same rule to any duplicated name so the replica and
    dummy bitcells behave identically.
    """
    from openram.tech import layer_indices

    def rank(pin):
        idx = layer_indices.get(pin.layer, 0)
        return idx if isinstance(idx, int) else 0

    for name, pins in list(cell.pin_map.items()):
        if len(pins) <= 1:
            continue
        best = max(rank(pin) for pin in pins)
        cell.pin_map[name] = [pin for pin in pins if rank(pin) == best]


class gf180_bitcell(bitcell_base):
    """
    A single bit cell (6T, 8T, etc.)  This module implements the
    single memory cell used in the design. It is a hand-made cell, so
    the layout and netlist should be available in the technology
    library.
    """

    # The vendor cell labels ``WL`` twice: once on the horizontal access-gate
    # poly band and once on the m3 strap, but does not draw a connection
    # between the two (the leaf m1 gnd comb and the two bitline m2 straps
    # leave no in-cell room for a via stack).  A standalone extraction only
    # ties them because both labels name the same net; in an array they stay
    # two separate nets, so ``capped_replica_bitcell_array`` draws one
    # poly -> m1 -> m2 -> m3 stack per row in the free margin left of the
    # outer dummy column (the poly bands and the m3 straps are each continuous
    # along a row).  All values are in the cell frame in um.
    wordline_tie = {
        "band_y": (0.26, 1.03),     # access-gate poly band
        "band_x0": -0.18,           # poly band left edge
        "strap_y": (1.65, 3.45),    # m3 wordline strap (the abutment pin)
        # The p+ well tap in the tapped dummy cell sits at y=0.74..1.24 in
        # the cell's left overhang, directly above the poly band's lower edge.
        # A full-height hand-off to the band passes under it with only 0.12 um
        # to spare, which violates the 0.30 um "poly to MV diffusion" rule.
        # Keep the via/contact pad well left of the tap (x <= -1.22), and
        # cross into the band through a thin arm that hugs the band's bottom
        # edge: 0.30 um clear of the tap diffusion.
        "stub_y": (0.26, 0.62),     # poly pad at the tie column
        "arm_y": (0.26, 0.44),      # poly arm that slips under the tap
        "contact_y": (0.33, 0.55),  # poly contact
        "pad_y": (0.23, 0.65),      # m1 landing pad
        "via1_y": (0.30, 0.56),     # m1 -> m2
        "x_center": -2.10,          # tie column, left of the dummy column
        "via2_from_strap": 0.50,    # m2 -> m3 via, measured from strap bottom
        "stub_overlap": 0.10,       # overlap into the poly band
        "m3_pad": 0.07,             # m3 landing enclosure of the via2 stack
        "half": {"stub": 0.19, "contact": 0.11, "pad": 0.19, "via": 0.13},
    }

    # ``cell1rw``'s extracted SPICE calls the two internal storage nodes
    # ``a_18_103#`` (BL side) and ``a_63_149#`` (BR side) instead of Q/Q_bar,
    # and the vendor GDS carries no Q/Q_bar labels, so ``bitcell_base`` cannot
    # locate the storage nets for the PEX labels.  These are the cell-frame
    # centres of the two m1 drain straps, identified by extracting the vendor
    # cell with temporary probe labels: Q is the BL-side node (the same
    # convention as sky130) and Q_bar the BR-side node.
    storage_nets = ("Q", "Q_bar")
    storage_net_positions = ((2.0, 3.3),    # Q,     BL side
                             (1.0, 2.5))    # Q_bar, BR side

    def __init__(self, version="opt1", name=""):
        cell_name = "cell1rw"
        super().__init__(name, cell_name=cell_name, prop=props.bitcell_1port)
        debug.info(2, "Create bitcell")
        collapse_duplicate_layer_pins(self)

    def get_storage_net_names(self):
        """Storage net names for PEX labelling.

        The vendor SPICE does not name the internal nodes Q/Q_bar, so the
        ``do_nets_exist`` check inherited from ``bitcell_base`` would reject
        them.  The two physical nodes are known (see
        ``storage_net_positions``), so report them unconditionally.
        """
        return list(self.storage_nets)

    def get_storage_net_offset(self):
        """M1 label positions of Q and Q_bar, in the cell frame (um)."""
        return [tuple(pos) for pos in self.storage_net_positions]

    def build_graph(self, graph, inst_name, port_nets):
        """
        Adds edges based on inputs/outputs.
        Overrides base class function.
        """
        self.add_graph_edges(graph, port_nets)
