"""Column-mux array wrapper that supplies the optional CMOS TG cells."""
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
        self.route_horizontal_pins("vdd", self.insts)
