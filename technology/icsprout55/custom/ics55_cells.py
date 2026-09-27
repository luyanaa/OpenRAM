# See LICENSE for licensing information.
#
# Dynamic support cells for the ICsprout55 OpenRAM port.

"""Generated support cells used because the PDK has no SRAM macros.

The released ICsprout55 libraries contain standard cells but no SRAM-specific
sense-amplifier or write-driver macros.  These classes therefore generate
transistor-level DFF, tri-state write-driver, and regenerative sense-amplifier
topologies.  They are structurally connected, but still use analytical timing
because no fitted analog model file is available.
"""

from openram import OPTS
from openram import debug
from openram.base import design, logical_effort, vector
from openram.sram_factory import factory
from openram.tech import drc, parameter, spice
from openram.drc.custom_cell_properties import cell as _hard_cell
from openram.modules.pinv import pinv


_STD_CELL_SUFFIX = {"H7CR": "H7R", "H7CL": "H7L", "H7CH": "H7H"}
_HARD_INV_PROP = _hard_cell(
    ["A", "Z", "vdd", "gnd"],
    ["INPUT", "OUTPUT", "POWER", "GROUND"],
    port_map={"A": "A", "Z": "Y", "vdd": "VDD", "gnd": "VSS"},
)
_HARD_TINV_PROP = _hard_cell(
    ["in", "out", "en", "vdd", "gnd"],
    ["INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"],
    port_map={"in": "A", "out": "Y", "en": "OE",
              "vdd": "VDD", "gnd": "VSS"},
)


def _std_cell_name(prefix):
    suffix = _STD_CELL_SUFFIX[spice["stdcell_library"]]
    return "{}X1{}".format(prefix, suffix)


class _ics55_support_cell(design):
    """Shared layout helpers for generated support cells."""
    def get_pin(self, pin_name):
        """Accept OpenRAM's lowercase API while emitting foundry-case labels."""
        aliases = {"gnd": "VSS", "GND": "VSS", "vdd": "VDD", "VDD": "VDD"}
        pin_name = aliases.get(pin_name, pin_name)
        if pin_name not in self.pin_map and pin_name.upper() in self.pin_map:
            pin_name = pin_name.upper()
        return super().get_pin(pin_name)
    def get_pin_name(self, text):
        """Resolve OpenRAM aliases for plural pin lookups as well."""
        aliases = {"gnd": "VSS", "GND": "VSS", "vdd": "VDD", "VDD": "VDD"}
        text = aliases.get(text, text)
        if text not in self.pin_map and text.upper() in self.pin_map:
            text = text.upper()
        return super().get_pin_name(text)


    def _expose_m2_pin(self, instance, source_name, pin_name, location=None):
        source_pin = instance.get_pin(source_name)
        if location is None:
            location = source_pin.center()
        if source_pin.layer == "poly":
            escape = vector(source_pin.cx(), self.height - self.m3_width)
            route_layer = "poly"
        else:
            escape = location
            route_layer = None

        if source_pin.layer != "m2" and route_layer is None:
            self.add_via_stack_center(
                offset=location,
                from_layer=source_pin.layer,
                to_layer="m2",
                min_area=False,
            )

        if route_layer is not None:
            self.add_path(
                layer=route_layer,
                coordinates=[source_pin.center(), escape],
            )
            self.add_via_stack_center(
                offset=escape,
                from_layer=source_pin.layer,
                to_layer="m2",
                min_area=False,
            )
            if escape != location:
                self.add_segment_center(
                    layer="m2",
                    start=escape,
                    end=location,
                )
        self.add_layout_pin_rect_center(
            text=pin_name.upper(),
            layer="m2",
            offset=location,
            width=self.m2_width,
            height=self.m2_width,
        )
        return location
    def _route_m3_net(
        self,
        pins,
        track_y,
        trunk_x=None,
        escape_xs=None,
        escape_ys=None,
    ):
        """Connect child pins to an isolated M2/M3 route."""
        if not pins:
            return
        if trunk_x is None:
            trunk_x = self.width - self.m2_pitch
        if escape_xs is None:
            escape_xs = [pin.center().x for pin in pins]
        if escape_ys is None:
            escape_ys = [
                self.height - self.m3_width
                if pin.layer == "poly"
                else pin.center().y
                if pin.layer == "active"
                else 0.8
                for pin in pins
            ]
        if len(escape_xs) != len(pins) or len(escape_ys) != len(pins):
            raise ValueError("escape coordinates must match pins")
        for pin, escape_x, escape_y in zip(pins, escape_xs, escape_ys):
            pin_center = pin.center()
            escape_point = vector(escape_x, escape_y)
            vertical_escape = vector(pin_center.x, escape_y)
            if pin_center != escape_point:
                route_layer = (
                    "poly" if pin.layer == "poly"
                    else "active" if pin.layer == "active"
                    else "m1"
                )
                self.add_path(
                    layer=route_layer,
                    coordinates=[pin_center, vertical_escape, escape_point],
                )
            self.add_via_stack_center(
                offset=escape_point,
                from_layer=pin.layer,
                to_layer="m2",
                min_area=False,
            )
            track_point = vector(escape_point.x, track_y)
            if escape_point.y != track_point.y:
                self.add_segment_center(
                    layer="m2",
                    start=escape_point,
                    end=track_point,
                )
            self.add_via_stack_center(
                offset=track_point,
                from_layer="m2",
                to_layer="m3",
                min_area=False,
            )
            trunk_point = vector(trunk_x, track_y)
            if track_point.x != trunk_point.x:
                self.add_segment_center(
                    layer="m3",
                    start=track_point,
                    end=trunk_point,
                )
    def _add_supply_rails(self, instance):
        for pin_name in ("vdd", "gnd"):
            source_name = (
                "VDD" if pin_name == "vdd" and "VDD" in instance.mod.pin_map
                else "VSS" if pin_name == "gnd" and "VSS" in instance.mod.pin_map
                else pin_name
            )
            pin = instance.get_pin(source_name)
            rail_name = "VSS" if pin_name == "gnd" else "VDD"
            self.add_layout_pin_rect_center(
                text=rail_name,
                layer=pin.layer,
                offset=vector(0.5 * self.width, pin.cy()),
                width=self.width,
                height=pin.height(),
            )

    def _finish_layout(self):
        self.add_boundary()

    def analytical_power(self, corner, load):
        return self.return_power()


class ics55_hard_inv(design):
    """H7C standard-cell inverter used inside generated support cells."""

    def __init__(self, name="ics55_hard_inv", size=1, **kwargs):
        del size, kwargs
        super().__init__(name=name,
                         cell_name=_std_cell_name("INV"),
                         prop=_HARD_INV_PROP)

    def analytical_power(self, corner, load):
        return self.return_power()


class ics55_hard_tinv(design):
    """H7C active-high tri-state inverter standard cell."""

    def __init__(self, name="ics55_hard_tinv", size=1, **kwargs):
        del size, kwargs
        super().__init__(name=name,
                         cell_name=_std_cell_name("TINV"),
                         prop=_HARD_TINV_PROP)

    def analytical_power(self, corner, load):
        return self.return_power()

class ics55_pinv(pinv):
    """ICsprout55 inverter with foundry-case supply labels."""

    def add_pins(self):
        self.add_pin_list(
            ["A", "Z", "VDD", "VSS"],
            ["INPUT", "OUTPUT", "POWER", "GROUND"],
        )

    def create_ptx(self):
        self.pmos_inst = self.add_inst(name="pinv_pmos", mod=self.pmos)
        self.connect_inst(["Z", "A", "VDD", "VDD"])
        self.nmos_inst = self.add_inst(name="pinv_nmos", mod=self.nmos)
        self.connect_inst(["Z", "A", "VSS", "VSS"])

    def route_supply_rails(self):
        self.add_layout_pin_rect_center(
            text="VSS",
            layer=self.route_layer,
            offset=vector(0.5 * self.width, 0),
            width=self.width,
        )
        self.add_layout_pin_rect_center(
            text="VDD",
            layer=self.route_layer,
            offset=vector(0.5 * self.width, self.height),
            width=self.width,
        )

    def get_pin(self, pin_name):
        aliases = {"vdd": "VDD", "gnd": "VSS"}
        return super().get_pin(aliases.get(pin_name, pin_name))

    def analytical_power(self, corner, load):
        return self.return_power()




class ics55_dff(_ics55_support_cell):
    """Positive-edge D flip-flop built from transmission-gate-style latches."""

    def __init__(self, name="dff"):
        super().__init__(name)
        self.add_pin_list(
            ["D", "Q", "clk", "vdd", "gnd"],
            ["INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"],
        )

        self.tinv = factory.create(module_type="ptristate_inv", size=1)
        self.inv = factory.create(module_type="pinv", size=1)

        # Clock complement.
        self.clk_inv = self.add_inst(name="clk_inv", mod=self.inv)
        self.connect_inst(["clk", "clk_bar", "vdd", "gnd"])

        # Master latch: transparent when clk is low, held by feedback
        # when clk is high.
        self.master_fwd = self.add_inst(name="master_fwd", mod=self.tinv)
        self.connect_inst(["D", "master", "clk_bar", "clk", "vdd", "gnd"])
        self.master_inv = self.add_inst(name="master_inv", mod=self.inv)
        self.connect_inst(["master", "master_bar", "vdd", "gnd"])
        self.master_fb = self.add_inst(name="master_fb", mod=self.tinv)
        self.connect_inst(["master_bar", "master", "clk", "clk_bar", "vdd", "gnd"])

        # Slave latch: transparent when clk is high, producing a positive
        # edge-triggered Q from the value held by the master.
        self.slave_fwd = self.add_inst(name="slave_fwd", mod=self.tinv)
        self.connect_inst(["master_bar", "slave_bar", "clk", "clk_bar", "vdd", "gnd"])
        self.slave_inv = self.add_inst(name="slave_inv", mod=self.inv)
        self.connect_inst(["slave_bar", "Q", "vdd", "gnd"])
        self.slave_fb = self.add_inst(name="slave_fb", mod=self.tinv)
        self.connect_inst(["Q", "slave_bar", "clk_bar", "clk", "vdd", "gnd"])

        self.create_layout()

    def create_layout(self):
        modules = [
            self.clk_inv,
            self.master_fwd,
            self.master_inv,
            self.master_fb,
            self.slave_fwd,
            self.slave_inv,
            self.slave_fb,
        ]
        child_height = max(inst.mod.height for inst in modules)
        route_count = 6
        route_margin = 2 * route_count * self.m3_pitch + self.m3_width
        self.height = child_height + route_margin
        self.width = sum(inst.mod.width for inst in modules) + len(modules) * self.m2_pitch
        self.width += self.m2_pitch

        if OPTS.netlist_only:
            return

        xoffset = 0
        for inst in modules:
            inst.place(vector(xoffset, 0))
            xoffset += inst.mod.width + self.m2_pitch

        trunk_x = self.width - self.m2_pitch
        track_y = child_height + self.m3_pitch

        self._route_m3_net(
            [self.clk_inv.get_pin("A"),
             self.master_fwd.get_pin("en_bar"),
             self.master_fb.get_pin("en"),
             self.slave_fwd.get_pin("en"),
             self.slave_fb.get_pin("en_bar")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.clk_inv.get_pin("Z"),
             self.master_fwd.get_pin("en"),
             self.master_fb.get_pin("en_bar"),
             self.slave_fwd.get_pin("en_bar"),
             self.slave_fb.get_pin("en")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.master_fwd.get_pin("out"),
             self.master_inv.get_pin("A"),
             self.master_fb.get_pin("out")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.master_inv.get_pin("Z"),
             self.master_fb.get_pin("in"),
             self.slave_fwd.get_pin("in")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.slave_fwd.get_pin("out"),
             self.slave_inv.get_pin("A"),
             self.slave_fb.get_pin("out")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.slave_inv.get_pin("Z"), self.slave_fb.get_pin("in")],
            track_y,
            trunk_x,
        )

        self._expose_m2_pin(self.master_fwd, "in", "D")
        self._expose_m2_pin(self.slave_inv, "Z", "Q")
        self._expose_m2_pin(self.clk_inv, "A", "clk")
        self._add_supply_rails(self.clk_inv)
        self._finish_layout()


class ics55_sense_amp(_ics55_support_cell):
    """Static regenerative differential sense amplifier."""

    def __init__(self, name="sense_amp"):
        super().__init__(name)
        self.add_pin_list(
            ["BL", "BR", "DOUT", "EN", "VDD", "VSS"],
            ["INPUT", "INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"],
        )

        nmos_input_width = max(
            drc("minwidth_tx"),
            parameter["sa_en_nmos_size"],
        )
        nmos_latch_width = max(
            drc("minwidth_tx"),
            parameter["sa_inv_nmos_size"],
        )
        pmos_latch_width = max(
            drc("minwidth_tx"),
            parameter["sa_inv_pmos_size"],
        )
        self.pmos_x = self._make_tx("pmos", pmos_latch_width)
        self.pmos_y = self._make_tx("pmos", pmos_latch_width)
        self.nmos_x = self._make_tx("nmos", nmos_latch_width)
        self.nmos_y = self._make_tx("nmos", nmos_latch_width)
        self.bl_tx = self._make_tx("nmos", nmos_input_width)
        self.br_tx = self._make_tx("nmos", nmos_input_width)
        self.tail_tx = self._make_tx("nmos", nmos_input_width)
        output_size = max(
            1,
            parameter["sa_inv_nmos_size"] / drc("minwidth_tx"),
        )
        self.output_inv = factory.create(module_type="ics55_pinv", size=output_size)

        self.pmos_x_inst = self.add_inst(name="sa_pmos_x", mod=self.pmos_x)
        self.connect_inst(["x", "y", "VDD", "VDD"])
        self.pmos_y_inst = self.add_inst(name="sa_pmos_y", mod=self.pmos_y)
        self.connect_inst(["y", "x", "VDD", "VDD"])
        self.nmos_x_inst = self.add_inst(name="sa_nmos_x", mod=self.nmos_x)
        self.connect_inst(["x", "y", "VSS", "VSS"])
        self.nmos_y_inst = self.add_inst(name="sa_nmos_y", mod=self.nmos_y)
        self.connect_inst(["y", "x", "VSS", "VSS"])
        self.bl_tx_inst = self.add_inst(name="sa_bl", mod=self.bl_tx)
        self.connect_inst(["x", "BL", "tail", "VSS"])
        self.br_tx_inst = self.add_inst(name="sa_br", mod=self.br_tx)
        self.connect_inst(["y", "BR", "tail", "VSS"])
        self.tail_tx_inst = self.add_inst(name="sa_tail", mod=self.tail_tx)
        self.connect_inst(["tail", "EN", "VSS", "VSS"])
        self.output_inv_inst = self.add_inst(name="sa_output_inv", mod=self.output_inv)
        self.connect_inst(["x", "DOUT", "VDD", "VSS"])
        self.create_layout()

    @staticmethod
    def _make_tx(tx_type, width):
        return factory.create(
            module_type="ptx",
            width=width,
            tx_type=tx_type,
            add_source_contact="m1",
            add_drain_contact="m1",
        )

    def create_layout(self):
        modules = [
            self.pmos_x_inst,
            self.pmos_y_inst,
            self.nmos_x_inst,
            self.nmos_y_inst,
            self.bl_tx_inst,
            self.br_tx_inst,
            self.tail_tx_inst,
            self.output_inv_inst,
        ]
        child_height = max(inst.mod.height for inst in modules)
        route_count = 8
        route_margin = 2 * route_count * self.m3_pitch + self.m3_width
        self.height = child_height + route_margin
        module_gap = 0.82
        self.width = sum(inst.mod.width for inst in modules) + len(modules) * module_gap
        self.width += module_gap

        if OPTS.netlist_only:
            return

        xoffset = 0
        for inst in modules:
            inst.place(vector(xoffset, 0))
            xoffset += inst.mod.width + module_gap

        trunk_x = self.width - self.m2_pitch
        track_y = child_height + self.m3_pitch
        self._route_m3_net(
            [self.pmos_x_inst.get_pin("D"),
             self.nmos_x_inst.get_pin("D"),
             self.bl_tx_inst.get_pin("D"),
             self.pmos_y_inst.get_pin("G"),
             self.nmos_y_inst.get_pin("G"),
             self.output_inv_inst.get_pin("A")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.pmos_y_inst.get_pin("D"),
             self.nmos_y_inst.get_pin("D"),
             self.br_tx_inst.get_pin("D"),
             self.pmos_x_inst.get_pin("G"),
             self.nmos_x_inst.get_pin("G")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.bl_tx_inst.get_pin("S"),
             self.br_tx_inst.get_pin("S"),
             self.tail_tx_inst.get_pin("D")],
            track_y,
            trunk_x,
        )
        track_y += 2 * self.m3_pitch
        for pin, rail_name in (
            (self.pmos_x_inst.get_pin("S"), "VDD"),
            (self.pmos_y_inst.get_pin("S"), "VDD"),
            (self.nmos_x_inst.get_pin("S"), "VSS"),
            (self.nmos_y_inst.get_pin("S"), "VSS"),
            (self.tail_tx_inst.get_pin("S"), "VSS"),
        ):
            self.add_label(rail_name, "m1", pin.center())

        self._expose_m2_pin(self.bl_tx_inst, "G", "bl")
        self._expose_m2_pin(self.br_tx_inst, "G", "br")
        self._expose_m2_pin(self.tail_tx_inst, "G", "en")
        self._expose_m2_pin(self.output_inv_inst, "Z", "dout")
        self._add_supply_rails(self.output_inv_inst)
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
        # BL high / BR low pulls x low; the output inverter makes dout high.
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
    """Complementary dual-bitline write driver with tri-state outputs."""

    def __init__(self, name="ics55_write_driver", **kwargs):
        del kwargs
        super().__init__(name)
        self.add_pin_list(
            ["BL", "BR", "DIN", "EN", "VDD", "VSS"],
            ["OUTPUT", "OUTPUT", "INPUT", "INPUT", "POWER", "GROUND"],
        )

        self.inv = factory.create(module_type="ics55_hard_inv")
        self.tinv = factory.create(module_type="ics55_hard_tinv")

        self.din_inv = self.add_inst(name="din_inv", mod=self.inv)
        self.connect_inst(["DIN", "DIN_BAR", "VDD", "VSS"])
        self.bl_driver = self.add_inst(name="bl_driver", mod=self.tinv)
        self.connect_inst(["DIN_BAR", "BL", "EN", "VDD", "VSS"])
        self.br_driver = self.add_inst(name="br_driver", mod=self.tinv)
        self.connect_inst(["DIN", "BR", "EN", "VDD", "VSS"])
        self.create_layout()

    def create_layout(self):
        modules = [self.din_inv, self.bl_driver, self.br_driver]
        child_height = max(inst.mod.height for inst in modules)
        route_count = 3
        route_margin = 2 * route_count * self.m3_pitch + self.m3_width
        self.height = child_height + route_margin
        self.width = sum(inst.mod.width for inst in modules) + len(modules) * self.m2_pitch
        self.width += self.m2_pitch

        if OPTS.netlist_only:
            return

        xoffset = 0
        for inst in modules:
            inst.place(vector(xoffset, 0))
            xoffset += inst.mod.width + self.m2_pitch

        trunk_x = self.width - self.m2_pitch
        track_y = child_height + self.m3_pitch
        self._route_m3_net(
            [self.din_inv.get_pin("A"), self.br_driver.get_pin("in")],
            track_y,
            trunk_x,
            escape_xs=[0.10, 2.84],
            escape_ys=[0.70, 0.70],
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.din_inv.get_pin("Z"), self.bl_driver.get_pin("in")],
            track_y,
            trunk_x,
            escape_xs=[0.50, 1.37],
            escape_ys=[0.90, 0.70],
        )
        track_y += 2 * self.m3_pitch
        self._route_m3_net(
            [self.bl_driver.get_pin("en"), self.br_driver.get_pin("en")],
            track_y,
            trunk_x,
            escape_xs=[1.17, 2.64],
            escape_ys=[0.50, 0.50],
        )
        self._expose_m2_pin(self.din_inv, "A", "din")
        self._expose_m2_pin(self.bl_driver, "en", "en")
        self._expose_m2_pin(self.bl_driver, "out", "bl")
        self._expose_m2_pin(self.br_driver, "out", "br")
        self._add_supply_rails(self.din_inv)
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
