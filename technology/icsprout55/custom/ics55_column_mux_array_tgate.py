"""Column-mux array wrapper that supplies the optional CMOS TG cells."""
from openram.base import vector
from openram.modules.column_mux_array import column_mux_array as column_mux_array_base


class ics55_column_mux_array_tgate(column_mux_array_base):
    """Generic select/bitline routing plus an explicit VDD rail for TG muxes."""

    uses_vdd = True

    def add_pins(self):
        super().add_pins()
        self.add_pin("vdd", "POWER")

    def create_array(self):
        self.mux_inst = []
        for col_num in range(self.columns):
            name = "XMUX{}".format(col_num)
            self.mux_inst.append(self.add_inst(name=name, mod=self.mux))
            self.connect_inst(["bl_{}".format(col_num),
                               "br_{}".format(col_num),
                               "bl_out_{}".format(int(col_num / self.words_per_row)),
                               "br_out_{}".format(int(col_num / self.words_per_row)),
                               "sel_{}".format(col_num % self.words_per_row),
                               "gnd", "vdd"])

    def route_supplies(self):
        self.route_horizontal_pins("gnd", self.insts)

        # The raised landing must avoid the BR-output M2 riser at x=2.045.
        # At this height x=2.58 is between the PMOS select and BR-input risers;
        # the continuous leaf M1 rail feeds the vertical strap below it.
        last_via = None
        via_y = None
        for inst in self.mux_inst:
            pin = inst.get_pin("vdd")
            via_x = pin.lx() + 2.58
            via_y = pin.cy() + 1.82
            self.add_rect(layer=pin.layer,
                          offset=vector(via_x - 0.085, pin.cy()),
                          width=0.17,
                          height=via_y - pin.cy())
            # The vertical M1 strap already supplies area. A horizontal
            # minimum-area landing would encroach on the leaf's M1 riser.
            last_via = self.add_via_stack_center(from_layer=pin.layer,
                                                 to_layer="m3",
                                                 offset=vector(via_x, via_y))

        via_width = last_via.mod.second_layer_width
        via_height = last_via.mod.second_layer_height
        self.add_layout_pin_segment_center(text="vdd",
                                           layer="m3",
                                           start=vector(0.5 * via_width, via_y),
                                           end=vector(self.width - 0.5 * via_width, via_y),
                                           width=via_height)
