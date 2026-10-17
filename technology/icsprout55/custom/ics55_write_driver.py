# See LICENSE for licensing information.
#
# ICsprout55 full-swing, tri-state write driver.
"""Differential full-CMOS write driver.

When en is asserted, bl=din and br=~din. When en is deasserted, both
bitlines are high impedance. Separate enable devices isolate both pull-up
and pull-down paths; dinb and enb are generated locally.

The macro provides break-before-make at the interface: data is captured by
the top-level DFF on the rising edge, while control_logic gates w_en to the
low-phase write window. The input must remain stable for the full en-active
interval; this cell does not generate an independent analog dead-time.
"""

import os as _os
import sys as _sys
import importlib.util as _ilu
if "ics55_gen" in _sys.modules:
    g = _sys.modules["ics55_gen"]
else:
    _spec = _ilu.spec_from_file_location(
        "ics55_gen", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ics55_gen.py"))
    g = _ilu.module_from_spec(_spec)
    _sys.modules["ics55_gen"] = g
    _spec.loader.exec_module(g)

from openram.base import vector
from openram.modules.write_driver import write_driver as write_driver_base
from openram import OPTS


class ics55_write_driver(g.ics55_gen):

    def __init__(self, name="write_driver", write_driver_strength=1.0):
        if (isinstance(write_driver_strength, bool)
                or not isinstance(write_driver_strength, (int, float))
                or write_driver_strength not in (0.75, 1.0)):
            raise ValueError("write_driver_strength must be exactly 0.75 or 1.0")
        self.write_driver_strength = write_driver_strength
        # 0.58 um gate pitch leaves 0.12 um M1 clearance around supply straps.
        super().__init__(name, source_drain_spacing=0.52)
        self.add_pin_list(["din", "bl", "br", "en", "vdd", "gnd"],
                          ["INPUT", "OUTPUT", "OUTPUT", "INPUT", "POWER", "GROUND"])
        self.width, self.bl_x, self.br_x = self.bitline_x()
        self.build()

    def build(self):
        W = self.width
        xl = self.snap(self.bl_x + 0.235)
        xr = self.snap(self.br_x - 0.235)
        cx = 0.5 * (xl + xr)
        x4 = self.snap(cx - 0.5 * self.strip_length(4))
        x2 = self.snap(cx - 0.5 * self.strip_length(2))

        # Only the eight output MOS scale; four local inverter MOS stay fixed.
        wN = wP = 0.60
        wout = {1.0: 0.60, 0.75: 0.45}[self.write_driver_strength]
        y_en = 0.10
        y_gnd = 3.55
        n_out_y = 1.10 + (0.60 - wout) / 2
        n_inv_y = 5.25
        p_out_y = 6.30 + (0.60 - wout) / 2
        y_vdd = 8.65
        p_inv_y = 11.00
        self.height = self.snap(p_inv_y + wP + 0.75)

        self.add_rail("gnd", y_gnd)
        self.add_rail("vdd", y_vdd)
        self.nwell(6.30 - 0.25, self.height - 0.20)
        for x in (xl, xr):
            self.add_tap(x, y_gnd, "p")
            self.add_tap(x, y_vdd, "n")

        if not OPTS.netlist_only:
            # The write-driver array distributes the shared enable bar.
            self.add_layout_pin_rect_center(text="en", layer="m1",
                                            offset=vector(0.5 * W, y_en),
                                            width=W, height=0.10)

        # Two series devices per output make each side actively pull to either
        # rail only while enabled; the center diffusion contacts the supply.
        self.add_strip(x4, n_out_y, wout, ["bl", "n1", "gnd", "n2", "br"],
                       ["dinb", "en", "en", "din"], "nmos", "top", "A",
                       nocontact=("n1", "n2"))
        self.add_strip(x4, p_out_y, wout, ["bl", "p1", "vdd", "p2", "br"],
                       ["dinb", "enb", "enb", "din"], "pmos", "top", "B",
                       nocontact=("p1", "p2"))

        # Shared full-CMOS inverters generate the complementary data and enable.
        self.add_strip(x2, n_inv_y, wN, ["dinb", "gnd", "enb"],
                       ["din", "en"], "nmos", "bottom", "A")
        self.add_strip(x2, p_inv_y, wP, ["dinb", "vdd", "enb"],
                       ["din", "en"], "pmos", "bottom", "B")
        if OPTS.netlist_only:
            return

        self.fix_special_pins({"gnd": {"A": y_gnd}, "vdd": {"B": y_vdd}})

        # Local M4 joins connect each lower/upper track pair; aligned M4
        # bridges then join the corresponding controls across the two channels.
        self.plan_m4("A", [0.35, 0.60, 0.85])
        self.plan_m4("B", [0.35, 0.60, 0.85])
        bridge_x = {"din": 1.15, "dinb": 1.40, "en": 1.65, "enb": 1.90}
        for channel in ("A", "B"):
            for net, x in bridge_x.items():
                self.m4_x.setdefault((channel, net), []).append(self.snap(x))

        # Cross external data and enable pins into channel B from the cell edge.
        # The en rail needs via1; din is M2 and route_channel uses via2 directly.
        self.pin("B", xl, y_en, "line", "en")
        self.via1_m2pad(xl, y_en)

        self.pin("B", xr, 0.0, "line", "din")
        low_a, high_a = self.route_channel(
            "A", 2.35, 4.75, order=["dinb", "en", "din", "enb"])
        low_b, high_b = self.route_channel(
            "B", 7.45, 10.45, order=["dinb", "enb", "din", "en"])
        for net, x in bridge_x.items():
            y_a = low_a.get(net, high_a.get(net))
            y_b = low_b.get(net, high_b.get(net))
            if y_a is None or y_b is None:
                raise ValueError("write-driver control {} is not routed in both channels".format(net))
            self.add_rect_center(layer="via3", offset=vector(self.snap(x), y_a),
                                 width=g.CT, height=g.CT)
            self.add_rect_center(layer="via3", offset=vector(self.snap(x), y_b),
                                 width=g.CT, height=g.CT)
            y_lo, y_hi = sorted((y_a, y_b))
            length = max(y_hi - y_lo + g.CT + 0.08, 0.56)
            self.add_rect(layer="m4",
                          offset=vector(self.snap(x) - 0.5 * g.M2W,
                                        0.5 * (y_lo + y_hi) - 0.5 * length),
                          width=g.M2W, height=length)

        for name, x in (("bl", self.bl_x), ("br", self.br_x)):
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(x - 0.05, 0), width=0.10, height=self.height)
        self.add_layout_pin(text="din", layer="m2",
                            offset=vector(xr - 0.05, 0), width=0.10,
                            height=low_b["din"] + 0.05)
        self.add_boundary()

    @property
    def supply_rail_extension(self):
        """Extra endpoint landing required by the ICsprout55 power grid."""
        return self.m2_pitch

    # ---- write_driver interface (shared with the library-cell class)
    get_bl_names = write_driver_base.get_bl_names
    get_br_names = write_driver_base.get_br_names
    din_name = write_driver_base.din_name
    en_name = write_driver_base.en_name
    get_w_en_cin = write_driver_base.get_w_en_cin
    build_graph = write_driver_base.build_graph

    def analytical_power(self, corner, load):
        return self.return_power()
