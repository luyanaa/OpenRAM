# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#

from .rom_base_cell import rom_base_cell
from .pgate import pgate
from openram.base import vector
from openram import OPTS
from openram.sram_factory import factory
from openram.tech import drc

class rom_precharge_cell(rom_base_cell):

    def __init__(self, name="", bitline_layer="m1", supply_layer="li"):
        self.supply_layer = supply_layer
        super().__init__(name=name, bitline_layer=bitline_layer)

    def create_layout(self):
        super().create_layout()

        self.place_tap()
        self.extend_well()


    def add_modules(self):
        if OPTS.tech_name == "sky130":
            width = pgate.nearest_bin("pmos", drc["minwidth_tx"])
        else:
            width = drc("minwidth_tx")
        self.pmos  = factory.create(module_type="ptx",
                                    module_name="pre_pmos_mod",
                                    tx_type="pmos",
                                    width=width,
                                    add_source_contact=self.supply_layer,
                                    add_drain_contact=self.bitline_layer
                                    )

    def create_tx(self):
        self.cell_inst = self.add_inst( name="precharge_pmos",
                                        mod=self.pmos,
                                        )
        self.connect_inst(["bitline", "gate", "vdd", "vdd"])

    def add_pins(self):
        pin_list = ["vdd", "gate", "bitline"]
        dir_list = ["POWER", "INPUT", "OUTPUT"]

        self.add_pin_list(pin_list, dir_list)

    def setup_drc_offsets(self):

        self.poly_size = (self.cell_inst.width + self.active_space) - (self.cell_inst.height + 2 * self.poly_extend_active)

    def extend_well(self):
        # Cover the whole PMOS (active, contacts and the source strap) with
        # N-well, clipped to the cell boundary so the array tiling stays
        # DRC-clean.  The old version stopped at the D pin centre, which left
        # the upper part of the PMOS active in the P substrate: the device
        # bulk then tied vdd to the substrate and LVS merged vdd with gnd.
        inst = self.cell_inst
        ll = inst.ll() - vector(self.nwell_enclose_active,
                                self.nwell_enclose_active)
        ur = inst.ur() + vector(self.nwell_enclose_active,
                                self.nwell_enclose_active)
        # The cell boundary is drawn at (0,0) while the geometry extends
        # below it, so clamp to the drawn extent instead of the boundary.
        lowest = self.find_lowest_coords()
        highest = self.find_highest_coords()
        ll = vector(max(ll.x, lowest.x), max(ll.y, lowest.y))
        ur = vector(min(ur.x, highest.x), min(ur.y, highest.y))
        if ur.x > ll.x and ur.y > ll.y:
            self.add_rect("nwell", ll, ur.x - ll.x, ur.y - ll.y)

    def place_tap(self):
        source = self.cell_inst.get_pin("S")
        tap_y = source.cy() - self.contact_width - 5 * self.active_enclose_contact - self.active_space
        self.tap_offset = abs(tap_y)
        pos  = vector(source.cx(), tap_y )

        self.tap = self.add_via_center(layers=self.active_stack,
                offset=pos,
                implant_type="n",
                well_type="n",
                directions="nonpref")
        self.add_via_stack_center(offset=pos,
                        from_layer=self.active_stack[2],
                        to_layer=self.supply_layer)

        bitline_offset = vector( 1.5 * (drc("minwidth_{}".format(self.bitline_layer)) + drc("{0}_to_{0}".format(self.bitline_layer))) ,0)

        self.add_layout_pin_rect_center("vdd", self.supply_layer, pos - bitline_offset)

        self.add_path(self.supply_layer, [self.get_pin("vdd").center(), pos, self.get_pin("S").center()])

        self.remove_layout_pin("S")

    def place_bitline(self):
        pass

    def short_gate(self):
        pass
