# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
from openram.base import design
from openram.tech import cell_properties as props
from openram.tech import spice, parameter


class dff(design):
    """
    Memory address flip-flop
    """

    def __init__(self, name="dff"):
        super().__init__(name, prop=props.dff)
        # Optional override for the height of the array's m3 clock rail.  A
        # technology whose hard flop has its D pin close to the default rail
        # height cannot promote the D pin to m3 (the pad would short to the
        # rail), so it may push the rail down inside the cell instead.
        self.clk_rail_y = parameter.get("dff_clk_rail_y", None)
        # A hard flop whose VDD and GND pins share the same x would otherwise
        # get abutting dff_array supply straps.  Shift the ground strap by
        # this amount (0 for technologies whose pins are already apart).
        self.gnd_tap_xshift = parameter.get("dff_gnd_tap_xshift", 0.0)

    def analytical_power(self, corner, load):
        """Returns dynamic and leakage power. Results in nW"""
        c_eff = self.calculate_effective_capacitance(load)
        freq = spice["default_event_frequency"]
        power_dyn = self.calc_dynamic_power(corner, c_eff, freq)
        power_leak = spice["dff_leakage"]

        total_power = self.return_power(power_dyn, power_leak)
        return total_power

    def calculate_effective_capacitance(self, load):
        """Computes effective capacitance. Results in fF"""
        c_load = load
        c_para = spice["dff_out_cap"] # ff
        transition_prob = 0.5
        return transition_prob * (c_load + c_para)

    def build_graph(self, graph, inst_name, port_nets):
        """Adds edges based on inputs/outputs. Overrides base class function."""
        self.add_graph_edges(graph, port_nets)

