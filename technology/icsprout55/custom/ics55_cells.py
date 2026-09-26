# See LICENSE for licensing information.
#
# Dynamic support cells for the ICsprout55 OpenRAM port.

"""Small generated support cells used because the PDK has no SRAM macros.

The released ICsprout55 libraries contain standard cells but no SRAM-specific
sense-amplifier or write-driver macros.  These cells keep OpenRAM generation
structural and analytical: the digital CDL primitive names come from the
selected H7C library, while no fitted analog model file is loaded.
"""

from openram import OPTS
from openram import debug
from openram.base import design, logical_effort, vector
from openram.sram_factory import factory
from openram.tech import drc, parameter, spice


class _ics55_support_cell(design):
    """Shared layout helpers for generated support cells."""

    def _expose_m2_pin(self, instance, source_name, pin_name, location=None):
        source_pin = instance.get_pin(source_name)
        if location is None:
            location = source_pin.center()
        if source_pin.layer != "m2":
            self.add_via_stack_center(
                offset=location,
                from_layer=source_pin.layer,
                to_layer="m2",
            )
        self.add_layout_pin_rect_center(
            text=pin_name,
            layer="m2",
            offset=location,
            width=self.m2_width,
            height=self.m2_width,
        )
        return location

    def _add_supply_rails(self, instance):
        for pin_name in ("vdd", "gnd"):
            pin = instance.get_pin(pin_name)
            self.add_layout_pin_rect_center(
                text=pin_name,
                layer=pin.layer,
                offset=vector(0.5 * self.width, pin.cy()),
                width=self.width,
                height=pin.height(),
            )

    def _finish_layout(self):
        self.add_boundary()

    def analytical_power(self, corner, load):
        return self.return_power()


class ics55_dff(_ics55_support_cell):
    """Generated DFF-shaped support cell for OpenRAM control logic.

    The storage function is intentionally represented by a compact inverter
    wrapper; this is a physical-generation fallback, not a timing/signoff
    replacement for a characterized standard-cell DFF.
    """

    def __init__(self, name="dff"):
        super().__init__(name)
        self.add_pin_list(
            ["D", "Q", "clk", "vdd", "gnd"],
            ["INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"],
        )
        self.inv = factory.create(module_type="pinv", size=1)
        self.inv_inst = self.add_inst(name="dff_inv", mod=self.inv)
        self.connect_inst(["D", "Q", "vdd", "gnd"])
        self.create_layout()

    def create_layout(self):
        if OPTS.netlist_only:
            self.width = self.inv.width
            self.height = self.inv.height
            return

        self.inv_inst.place(vector(0, 0))
        self.width = self.inv.width + 2 * self.m2_pitch
        self.height = self.inv.height
        self._expose_m2_pin(self.inv_inst, "A", "D")
        self._expose_m2_pin(self.inv_inst, "Z", "Q")
        self.add_layout_pin_rect_center(
            text="clk",
            layer="m2",
            offset=vector(self.width - 0.5 * self.m2_width, 0.5 * self.height),
            width=self.m2_width,
            height=self.m2_width,
        )
        self._add_supply_rails(self.inv_inst)
        self._finish_layout()


class ics55_sense_amp(_ics55_support_cell):
    """Generated differential-interface placeholder sense amplifier."""

    def __init__(self, name="sense_amp"):
        super().__init__(name)
        self.add_pin_list(
            ["bl", "br", "dout", "en", "vdd", "gnd"],
            ["INPUT", "INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"],
        )
        self.inv = factory.create(module_type="pinv", size=1)
        self.inv_inst = self.add_inst(name="sense_inv", mod=self.inv)
        self.connect_inst(["bl", "dout", "vdd", "gnd"])
        self.create_layout()

    def create_layout(self):
        if OPTS.netlist_only:
            self.width = self.inv.width + 3 * self.m2_pitch
            self.height = self.inv.height
            return

        self.inv_inst.place(vector(0, 0))
        self.width = self.inv.width + 3 * self.m2_pitch
        self.height = self.inv.height
        self._expose_m2_pin(self.inv_inst, "A", "bl")
        self._expose_m2_pin(self.inv_inst, "Z", "dout")
        self.add_layout_pin_rect_center(
            text="br",
            layer="m2",
            offset=vector(self.inv.width + self.m2_pitch, 0.5 * self.height),
            width=self.m2_width,
            height=self.m2_width,
        )
        self.add_layout_pin_rect_center(
            text="en",
            layer="m2",
            offset=vector(self.inv.width + 2.0 * self.m2_pitch, 0.5 * self.height),
            width=self.m2_width,
            height=self.m2_width,
        )
        self._add_supply_rails(self.inv_inst)
        self._finish_layout()

    def get_bl_names(self):
        return "bl"

    def get_br_names(self):
        return "br"

    @property
    def dout_name(self):
        return "dout"

    @property
    def en_name(self):
        return "en"

    def get_cin(self):
        return spice["min_tx_drain_c"] * 8

    def get_stage_effort(self, load):
        parasitic_delay = 1
        cin = (parameter["sa_inv_pmos_size"] + parameter["sa_inv_nmos_size"]) / drc("minwidth_tx")
        sa_size = parameter["sa_inv_nmos_size"] / drc("minwidth_tx")
        return logical_effort("column_mux", sa_size, cin, load + cin, parasitic_delay, False)

    def get_enable_name(self):
        debug.check(self.en_name in self.pin_names, "Enable name {} not found in pin list".format(self.en_name))
        return self.en_name

    def build_graph(self, graph, inst_name, port_nets):
        self.add_graph_edges(graph, port_nets)

    def is_non_inverting(self):
        return True

    def get_on_resistance(self):
        return self.tr_r_on(parameter["sa_inv_nmos_size"], True, 1, False)

    def get_input_capacitance(self):
        return self.gate_c(parameter["sa_inv_nmos_size"])

    def get_intrinsic_capacitance(self):
        return self.drain_c_(parameter["sa_inv_nmos_size"], 1, 1)

    def cacti_rc_delay(self, inputramptime, tf, vs1, vs2, rise, extra_param_dict):
        c_senseamp = extra_param_dict["load"]
        vdd = extra_param_dict["vdd"]
        return c_senseamp / spice["sa_transconductance"] * __import__("math").log(vdd / (0.1 * vdd))


class ics55_write_driver(_ics55_support_cell):
    """Generated dual-bitline write-driver interface."""

    def __init__(self, name="write_driver"):
        super().__init__(name)
        self.add_pin_list(
            ["din", "bl", "br", "en", "vdd", "gnd"],
            ["INPUT", "OUTPUT", "OUTPUT", "INPUT", "POWER", "GROUND"],
        )
        self.inv = factory.create(module_type="pinv", size=1)
        self.bl_inv = self.add_inst(name="write_bl_inv", mod=self.inv)
        self.connect_inst(["din", "bl", "vdd", "gnd"])
        self.br_inv = self.add_inst(name="write_br_inv", mod=self.inv)
        self.connect_inst(["din", "br", "vdd", "gnd"])
        self.create_layout()

    def create_layout(self):
        if OPTS.netlist_only:
            self.width = 2 * self.inv.width + 3 * self.m2_pitch
            self.height = self.inv.height
            return

        self.bl_inv.place(vector(0, 0))
        self.br_inv.place(vector(self.inv.width + self.m2_pitch, 0))
        self.width = 2 * self.inv.width + 3 * self.m2_pitch
        self.height = self.inv.height
        self._expose_m2_pin(self.bl_inv, "A", "din")
        self._expose_m2_pin(self.bl_inv, "Z", "bl")
        self._expose_m2_pin(self.br_inv, "Z", "br")
        self.add_layout_pin_rect_center(
            text="en",
            layer="m2",
            offset=vector(self.width - 0.5 * self.m2_width, 0.5 * self.height),
            width=self.m2_width,
            height=self.m2_width,
        )
        self._add_supply_rails(self.bl_inv)
        self._finish_layout()

    def get_bl_names(self):
        return "bl"

    def get_br_names(self):
        return "br"

    @property
    def din_name(self):
        return "din"

    @property
    def en_name(self):
        return "en"

    def get_w_en_cin(self):
        return 5 * 3

    def build_graph(self, graph, inst_name, port_nets):
        self.add_graph_edges(graph, port_nets)
