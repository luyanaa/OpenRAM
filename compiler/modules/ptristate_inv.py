# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
from openram import debug
from openram.base import vector
from openram.sram_factory import factory
from openram.tech import drc, parameter, spice
from .pgate import *
import math


class ptristate_inv(pgate):
    """
    ptristate generates gds of a parametrically sized tristate inverter.
    There is some flexibility in the size, but we do not allow multiple fingers
    to fit in the cell height.

    """

    def __init__(self, name, size=1, height=None):

        debug.info(2,
                   "creating ptristate inv {0} with size of {1}".format(name,
                                                                        size))
        self.add_comment("size: {}".format(size))

        # We are 2x since there are two series devices
        self.size = 2 * size
        self.nmos_size = size
        self.beta = parameter["beta"]
        self.pmos_size = self.beta * size

        self.nmos_width = self.nmos_size * drc("minwidth_tx")
        self.pmos_width = self.pmos_size * drc("minwidth_tx")

        # Creates the netlist and layout
        super().__init__(name, height)

    def create_netlist(self):
        """ Calls all functions related to the generation of the netlist """
        self.add_pins()
        self.add_ptx()
        self.create_ptx()

    def create_layout(self):
        """ Calls all functions related to the generation of the layout """
        self.setup_layout_constants()
        self.route_supply_rails()
        self.place_ptx()
        self.add_well_contacts()
        self.extend_wells()
        self.connect_rails()
        self.route_inputs()
        self.route_outputs()
        self.add_boundary()

    def add_pins(self):
        """ Adds pins for spice netlist """
        self.add_pin_list(["in", "out", "en", "en_bar", "vdd", "gnd"])

    def setup_layout_constants(self):
        """
        Pre-compute some handy layout parameters.
        """

        # Compute the other pmos2 location, but determining offset to overlap the
        # source and drain pins
        self.overlap_offset = self.pmos.get_pin("D").ll() - self.pmos.get_pin("S").ll()

        # Two PMOS devices and a well contact. Separation between each.
        # Enclosure space on the sides.
        self.well_width = 2 * self.pmos.active_width + self.nwell_enclose_active

        # Add an extra space because we route the output on the right of the S/D
        self.width = self.well_width + 0.5 * self.m1_space
        # Height is an input parameter, so it is not recomputed.

    def add_ptx(self):
        # The two series devices share an internal drain node.  Leave the
        # first device's drain as active-only and keep the contact on the
        # output-side device; this removes redundant adjacent M1 contacts
        # while preserving the same extracted transistor topology.
        self.nmos = factory.create(module_type="ptx",
                                   width=self.nmos_width,
                                   mults=1,
                                   tx_type="nmos",
                                   add_source_contact="m1",
                                   add_drain_contact=False)
        self.nmos_output = factory.create(module_type="ptx",
                                          width=self.nmos_width,
                                          mults=1,
                                          tx_type="nmos",
                                          add_source_contact="m1",
                                          add_drain_contact="m1")
        self.pmos = factory.create(module_type="ptx",
                                   width=self.pmos_width,
                                   mults=1,
                                   tx_type="pmos",
                                   add_source_contact="m1",
                                   add_drain_contact=False)
        self.pmos_output = factory.create(module_type="ptx",
                                          width=self.pmos_width,
                                          mults=1,
                                          tx_type="pmos",
                                          add_source_contact="m1",
                                          add_drain_contact="m1")
    def route_supply_rails(self):
        """ Add vdd/gnd rails to the top and bottom. """
        self.add_layout_pin_rect_center(text="gnd",
                                        layer="m1",
                                        offset=vector(0.5 * self.width, 0),
                                        width=self.width)

        self.add_layout_pin_rect_center(text="vdd",
                                        layer="m1",
                                        offset=vector(0.5 * self.width, self.height),
                                        width=self.width)

    def create_ptx(self):
        """
        Create the PMOS and NMOS netlist.
        """

        # These are the inverter PMOS/NMOS
        self.pmos1_inst = self.add_inst(name="ptri_pmos1",
                                        mod=self.pmos)
        self.connect_inst(["vdd", "in", "n1", "vdd"])
        self.nmos1_inst = self.add_inst(name="ptri_nmos1",
                                        mod=self.nmos)
        self.connect_inst(["gnd", "in", "n2", "gnd"])


        # These are the tristate PMOS/NMOS
        self.pmos2_inst = self.add_inst(name="ptri_pmos2",
                                        mod=self.pmos_output)
        self.connect_inst(["out", "en_bar", "n1", "vdd"])
        self.nmos2_inst = self.add_inst(name="ptri_nmos2",
                                        mod=self.nmos_output)
        self.connect_inst(["out", "en", "n2", "gnd"])

    def place_ptx(self):
        """
        Place PMOS and NMOS to the layout at the upper-most and lowest position
        to provide maximum routing in channel
        """

        pmos_yoff = self.height - self.pmos.active_height \
                    - self.top_bottom_space - 0.5 * self.active_contact.height
        nmos_yoff = self.top_bottom_space + 0.5 * self.active_contact.height

        # Tristate transistors
        pmos1_pos = vector(self.pmos.active_offset.x, pmos_yoff)
        self.pmos1_inst.place(pmos1_pos)
        nmos1_pos = vector(self.pmos.active_offset.x, nmos_yoff)
        self.nmos1_inst.place(nmos1_pos)

        # Inverter transistors
        self.pmos2_pos = pmos1_pos + self.overlap_offset
        self.pmos2_inst.place(self.pmos2_pos)
        self.nmos2_pos = nmos1_pos + self.overlap_offset
        self.nmos2_inst.place(self.nmos2_pos)

        # Output position will be in between the PMOS and NMOS
        self.output_pos = vector(0,
                                 0.5 * (pmos_yoff + nmos_yoff + self.nmos.height))

        # This will help with the wells
        self.well_pos = vector(0, self.nmos1_inst.uy())

    def route_single_gate(self, inst, name, position="left"):
        """Expose one transistor gate on the routing layer.

        The gate contact cannot sit on the active area: CO.8 requires 0.165 um
        between a poly contact and *any* diffusion, and ptx only extends the
        gate poly 0.22 um past the diffusion.  The contact is therefore moved
        into the routing channel in the middle of the cell, on a poly stub that
        extends the gate, where there is no diffusion at all.  The pmos and
        nmos tristate gates use different y positions so that the two stubs
        and their metal1 pins stay apart.

        ptx runs the gate poly vertically and puts the source/drain contacts on
        either side of it, so the metal1 pin of the gate contact also has to
        clear those pads on the diagonal.  The y is clamped so that the
        contact itself clears the diffusion and the metal1 pad clears the
        source/drain pads.
        """
        gate_pin = inst.get_pin("G")
        d_pin = inst.get_pin("D")
        s_pin = inst.get_pin("S")

        contact_w = drc("minwidth_contact")
        pad_h = max(contact_w + 2 * drc("m1_extend_contact"),
                    drc("minwidth_m1"))
        m1_space = drc("m1_to_m1")

        # poly enclose contact (vertical, "V" direction), from the contact
        # module: max(extend, (poly_minwidth - contact)/2)
        poly_enclosure = max(drc("poly_extend_contact"),
                             (drc("minwidth_poly") - contact_w) / 2)
        poly_pad_h = contact_w + 2 * poly_enclosure

        # Horizontal clearance from the gate poly to the nearest source/drain
        # m1 pad; used to turn the diagonal m1 spacing rule into a y clearance
        dx = min(gate_pin.ll().x - max(d_pin.ur().x, s_pin.ur().x),
                 min(d_pin.ll().x, s_pin.ll().x) - gate_pin.ur().x)
        dx = max(dx, 0)
        required_dy = math.sqrt(max(m1_space ** 2 - dx ** 2, 0))

        # Diffusion edges from the poly ends.  ptx extends the poly by
        # poly_extend_active on each side of the diffusion.
        diff_bottom = gate_pin.ll().y + drc("poly_extend_active")
        diff_top = gate_pin.ur().y - drc("poly_extend_active")

        if gate_pin.center().y > self.output_pos.y:
            # PMOS (upper device): contact goes below the diffusion
            y = min(max(d_pin.ll().y, s_pin.ll().y) - required_dy
                    - 0.5 * pad_h,
                    diff_bottom - drc("poly_contact_to_gate")
                    - 0.5 * contact_w)
        else:
            # NMOS (lower device): contact goes above the diffusion
            y = max(min(d_pin.ur().y, s_pin.ur().y) + required_dy
                    + 0.5 * pad_h,
                    diff_top + drc("poly_contact_to_gate")
                    + 0.5 * contact_w)

        offset = vector(gate_pin.center().x, y)

        via = self.add_via_stack_center(
            offset=offset,
            from_layer="poly",
            to_layer=self.route_layer,
            directions=("V", "V"),
        )

        # Stretch the gate poly up/down to meet the contact's poly enclosure so
        # the stub is on the same net as the gate.
        if gate_pin.center().y > self.output_pos.y:
            bridge_bottom = y - 0.5 * poly_pad_h
            self.add_rect("poly",
                          offset=vector(gate_pin.ll().x, bridge_bottom),
                          width=gate_pin.width(),
                          height=gate_pin.ll().y - bridge_bottom)
        else:
            bridge_top = y + 0.5 * poly_pad_h
            self.add_rect("poly",
                          offset=vector(gate_pin.ll().x, gate_pin.ur().y),
                          width=gate_pin.width(),
                          height=bridge_top - gate_pin.ur().y)

        self.add_layout_pin_rect_center(
            text=name,
            layer=self.route_layer,
            offset=offset,
            width=via.mod.second_layer_width,
            height=via.mod.second_layer_height,
        )

    def route_inputs(self):
        """ Route the gates """

        self.route_input_gate(self.pmos1_inst,
                              self.nmos1_inst,
                              self.output_pos.y,
                              "in",
                              position="farleft")
        self.route_single_gate(self.pmos2_inst, "en_bar", position="left")
        self.route_single_gate(self.nmos2_inst, "en", position="left")

    def route_outputs(self):
        """ Route the output (drains) together. """

        nmos_drain_pin = self.nmos2_inst.get_pin("D")
        pmos_drain_pin = self.pmos2_inst.get_pin("D")

        nmos_drain_pos = nmos_drain_pin.lr()
        pmos_drain_pos = pmos_drain_pin.ur()

        self.add_layout_pin(text="out",
                            layer="m1",
                            offset=nmos_drain_pos,
                            height=pmos_drain_pos.y - nmos_drain_pos.y)

    def add_well_contacts(self):
        """
        Add n/p well taps to the layout and connect to
        supplies AFTER the wells are created
        """

        layer_stack = self.active_stack

        drain_pos = self.nmos1_inst.get_pin("S").center()
        vdd_pos = self.get_pin("vdd").center()
        self.add_via_center(layers=layer_stack,
                            offset=vector(drain_pos.x, vdd_pos.y),
                            implant_type="n",
                            well_type="n")

        gnd_pos = self.get_pin("gnd").center()
        self.add_via_center(layers=layer_stack,
                            offset=vector(drain_pos.x, gnd_pos.y),
                            implant_type="p",
                            well_type="p")

    def connect_rails(self):
        """ Connect the nmos and pmos to its respective power rails """

        self.connect_pin_to_rail(self.nmos1_inst, "S", "gnd")
        self.connect_pin_to_rail(self.pmos1_inst, "S", "vdd")

    def analytical_power(self, corner, load):
        """Returns dynamic and leakage power. Results in nW"""
        # Power in this module currently not defined.
        # Returns 0 nW (leakage and dynamic).
        total_power = self.return_power()
        return total_power

    def get_cin(self):
        return 9 * spice["min_tx_gate_c"]
