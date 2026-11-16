# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
from openram import debug
from openram.base import vector
from openram.base import design
from openram.sram_factory import factory
from openram.tech import layer
from openram import OPTS


class and2_dec(design):
    """
    This is an AND with configurable drive strength.
    """
    def __init__(self, name, size=1, height=None, add_wells=True,
                 b_escape_clear_a=False):

        design.__init__(self, name)

        debug.info(1, "Creating and2_dec {}".format(name))
        self.add_comment("size: {}".format(size))
        self.size = size
        self.height = height
        # See add_wires: some parents run an m2 jog from the A pin centre, so
        # the escaped B route must clear that spot.
        self.b_escape_clear_a = b_escape_clear_a

        self.create_netlist()
        if not OPTS.netlist_only:
            self.create_layout()

    def create_netlist(self):
        self.add_pins()
        self.create_modules()
        self.create_insts()

    def create_modules(self):
        self.nand = factory.create(module_type="nand2_dec",
                                   height=self.height)

        self.inv = factory.create(module_type="inv_dec",
                                  height=self.height,
                                  size=self.size)

    def create_layout(self):

        if "li" in layer:
            self.route_layer = "li"
        else:
            self.route_layer = "m1"
        self.width = self.nand.width + self.inv.width
        self.height = self.nand.height

        self.place_insts()
        self.add_wires()
        self.add_layout_pins()
        self.route_supply_rails()
        self.add_boundary()
        self.DRC_LVS()

    def add_pins(self):
        self.add_pin("A", "INPUT")
        self.add_pin("B", "INPUT")
        self.add_pin("Z", "OUTPUT")
        self.add_pin("vdd", "POWER")
        self.add_pin("gnd", "GROUND")

    def create_insts(self):
        self.nand_inst = self.add_inst(name="pand2_dec_nand",
                                       mod=self.nand)
        self.connect_inst(["A", "B", "zb_int", "vdd", "gnd"])

        self.inv_inst = self.add_inst(name="pand2_dec_inv",
                                      mod=self.inv)
        self.connect_inst(["zb_int", "Z", "vdd", "gnd"])

    def place_insts(self):
        # Add NAND to the right
        self.nand_inst.place(offset=vector(0, 0))

        # Add INV to the right
        self.inv_inst.place(offset=vector(self.nand_inst.rx(), 0))

    def route_supply_rails(self):
        """ Add vdd/gnd rails to the top, (middle), and bottom. """
        if OPTS.tech_name == "sky130":
            for name in ["vdd", "gnd"]:
                for inst in [self.nand_inst, self.inv_inst]:
                    self.copy_layout_pin(inst, name)
        else:
            self.add_layout_pin_rect_center(text="gnd",
                                            layer=self.route_layer,
                                            offset=vector(0.5 * self.width, 0),
                                            width=self.width)
            self.add_layout_pin_rect_center(text="vdd",
                                            layer=self.route_layer,
                                            offset=vector(0.5 * self.width, self.height),
                                            width=self.width)

    def add_wires(self):
        # nand Z to inv A
        z1_pin = self.nand_inst.get_pin("Z")
        a2_pin = self.inv_inst.get_pin("A")
        if OPTS.tech_name == "sky130":
            mid1_point = vector(a2_pin.cx(), z1_pin.cy())
        else:
            mid1_point = vector(z1_pin.cx(), a2_pin.cy())
        self.add_path(self.route_layer,
                      [z1_pin.center(), mid1_point, a2_pin.center()])

        # On technologies whose NAND draws both inputs at (nearly) the same
        # height, the B stub is walled in on the routing layer: below it lies
        # the NAND's own gnd stub, above it the internal zb_int wire, and the
        # gaps to the neighbouring metal are narrower than a wire plus spacing
        # (gf180mcu).  A parent therefore cannot reach B on the input layer.
        # Bring B out over the free left margin on m2 and expose the pin there;
        # the parent then approaches on the routing layer and drops a via onto
        # this pin.  The approach is gated on the two pins actually colliding,
        # so technologies with separated inputs are untouched.
        self.b_escape = False
        apin = self.nand_inst.get_pin("A")
        bpin = self.nand_inst.get_pin("B")
        if ("m2" in layer
                and abs(bpin.cy() - apin.cy()) < bpin.height() + 0.23):
            self.b_escape = True
            esc_x = -0.3
            if self.b_escape_clear_a:
                # port_address' RBL driver drops a via stack on the A pin
                # centre and runs an m2 jog from it.  The default B escape
                # crosses that spot on m2 and would silently short A to B
                # (and hence wl_en to vdd).  Lift the escape above the A pin
                # first, then run left in the free upper margin.
                esc_y = apin.cy() + 0.75
            else:
                esc_y = apin.cy() - 0.9
            self.b_escape_pos = vector(esc_x, esc_y)
            self.add_via_stack_center(from_layer=bpin.layer,
                                      to_layer="m2",
                                      offset=bpin.center())
            if self.b_escape_clear_a:
                self.add_path("m2",
                              [bpin.center(),
                               vector(bpin.cx(), esc_y),
                               self.b_escape_pos])
            else:
                self.add_path("m2",
                              [bpin.center(),
                               vector(esc_x, bpin.cy()),
                               self.b_escape_pos])

    def add_layout_pins(self):
        pin = self.inv_inst.get_pin("Z")
        self.add_layout_pin_rect_center(text="Z",
                                        layer=pin.layer,
                                        offset=pin.center(),
                                        width=pin.width(),
                                        height=pin.height())

        for pin_name in ["A", "B"]:
            if pin_name == "B" and getattr(self, "b_escape", False):
                # Escaped to m2 (see add_wires) so the parent can reach it.
                self.add_layout_pin_rect_center(text="B",
                                                layer="m2",
                                                offset=self.b_escape_pos)
            else:
                pin = self.nand_inst.get_pin(pin_name)
                self.add_layout_pin_rect_center(text=pin_name,
                                                layer=pin.layer,
                                                offset=pin.center(),
                                                width=pin.width(),
                                                height=pin.height())
