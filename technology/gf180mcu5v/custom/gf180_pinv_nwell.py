# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""Inverter variant whose source rail is a signal (``nsense``), not ground.

The gf180mcu cross-coupled sense amplifier uses the source rail of two
inverters as the switched source node (``nsense``).  The stock ``pinv`` calls
that rail ``gnd`` and adds a p-substrate tap on it.  Both are wrong here:

  * the p-substrate tap makes Magic extract the rail as the global substrate
    net (``set SUB gnd`` in the extraction script), so the rail is welded to
    gnd and the whole amplifier collapses into one net;
  * the rail pin named ``gnd`` collides with that same substrate name, so even
    without the tap the extracted leaf has its source pin on the global gnd
    net and any parent that wires it to ``nsense`` is shorted.

This variant therefore has four pins ``A Z vdd nsense``: the nwell tap keeps
the PMOS body on vdd, the NMOS source is the ``nsense`` rail, and the NMOS
body stays on the internal substrate net (named ``gnd``, the same name Magic
uses for the substrate, but a local node inside this subcircuit).

The width is unchanged with respect to the stock pinv because the n and p
taps are drawn at the same x offset, so the normal ``determine_width``
expression evaluated on the nwell tap alone yields the stock pinv width
(``gf180_sense_amp`` asserts this).
"""

from openram import debug
from openram.base import vector
from openram.modules.pinv import pinv


class gf180_pinv_nwell(pinv):
    """Parameterized inverter with an nwell tap and a signal source rail."""

    def add_well_contacts(self):
        self.add_nwell_contact(self.pmos, self.pmos_pos)

    def determine_width(self):
        # Same expression as pgate.determine_width with add_wells=True, but
        # only the nwell tap exists in this variant.
        self.width = (self.nwell_contact.rx()
                      + self.m1_space
                      + 0.5 * self.m1_via.width)

    def add_pins(self):
        """Five pins: the bottom rail is the switched source node and the
        substrate/bulk terminal is exposed as ``gnd`` (the parent must tie it
        to the array ground, exactly as Magic extracts the p-substrate)."""
        pin_list = ["A", "Z", "vdd", "nsense", "gnd"]
        dir_list = ["INPUT", "OUTPUT", "POWER", "POWER", "GROUND"]
        self.add_pin_list(pin_list, dir_list)

    def create_ptx(self):
        """PMOS body on vdd, NMOS source on nsense, NMOS body on the gnd pin."""
        self.pmos_inst = self.add_inst(name="pinv_pmos",
                                       mod=self.pmos)
        self.connect_inst(["Z", "A", "vdd", "vdd"])

        self.nmos_inst = self.add_inst(name="pinv_nmos",
                                      mod=self.nmos)
        self.connect_inst(["Z", "A", "nsense", "gnd"])

    def route_supply_rails(self):
        """Add the nsense (bottom) and vdd (top) rails.

        The amplifier columns sit on the 3.0 um bitcell pitch.  Adjacent
        columns' *vdd* rails may touch (same net), but the nsense rails carry
        different nets, so each one must stop short of the next column.

        The 3.3 V cell is 2.90 um wide, so the stock 0.24 um inset already
        clears the pitch.  The 5 V cell is 3.66 um wide (0.60 um gates and
        wider diffusions), and the same inset leaves a 3.42 um rail that
        overruns the pitch by 0.42 um: every neighbouring nsense net merged
        and LVS reported the eight amplifiers as one shorted net.  Instead
        size the rail from the pitch itself: 3.00 - 0.24 = 2.76 um, i.e. a
        0.90 um inset from this cell's right edge (M1 spacing is 0.23 um).
        The switched-source via stacks land at x = 1.53 um, well inside.
        """
        pitch = 3.00
        nsense_width = min(self.width - 0.24, pitch - 0.24)
        debug.check(nsense_width + 0.23 <= pitch,
                    "nsense rail {0} um overruns the {1} um column pitch".format(
                        nsense_width, pitch))
        self.add_layout_pin_rect_center(text="nsense",
                                        layer=self.route_layer,
                                        offset=vector(0.5 * nsense_width, 0),
                                        width=nsense_width)

        self.add_layout_pin_rect_center(text="vdd",
                                        layer=self.route_layer,
                                        offset=vector(0.5 * self.width, self.height),
                                        width=self.width)

    def connect_rails(self):
        """Connect the PMOS source to vdd and the NMOS source to nsense."""
        self.connect_pin_to_rail(self.pmos_inst, "S", "vdd")
        self.connect_source_to_rail(self.nmos_inst, "nsense")

    def connect_source_to_rail(self, inst, rail_name):
        """Mirror pgate.connect_pin_to_rail for a downward (bottom) rail."""
        supply_pin = self.get_pin(rail_name)
        for source_pin in inst.get_pins("S"):
            height = supply_pin.by() - source_pin.by()
            debug.check(supply_pin.layer == source_pin.layer,
                        "Supply pin is not on correct layer.")
            self.add_rect(layer=source_pin.layer,
                          offset=source_pin.ll(),
                          height=height,
                          width=source_pin.width())
