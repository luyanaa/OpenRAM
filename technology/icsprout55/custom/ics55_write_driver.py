# See LICENSE for licensing information.
#
# ICsprout55 write driver (generated).
"""Write driver with en-gated NMOS pull-downs, same function as scn4m_subm.

  dinb = not din
  bl  --[N en]--[N dinb]-- gnd     (din = 0 pulls bl low)
  br  --[N en]--[N din ]-- gnd     (din = 1 pulls br low)

The bit lines are precharged high, so only the side to be written low is
driven; with en low the driver is off.  Pitch matched to the bitcell.
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

    def __init__(self, name="write_driver", **kwargs):
        super().__init__(name)
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
        x1 = self.snap(cx - 0.5 * self.strip_length(1))

        y_en = 0.10
        y_gA = 0.45
        wN = 0.60
        nA0 = y_gA + 0.55
        nA1 = nA0 + wN
        y_v = nA1 + 1.50            # channel A (lower tracks only) + N-well edge
        wP = 0.45
        pB0 = y_v + 0.55
        pB1 = pB0 + wP
        nB0 = pB1 + 1.44
        nB1 = nB0 + wP
        y_gB = nB1 + 0.55
        self.height = self.snap(y_gB + 0.085 + 0.12)

        self.add_rail("gnd", y_gA)
        self.add_rail("vdd", y_v)
        self.add_rail("gnd", y_gB)
        if not OPTS.netlist_only:
            # The array draws a full-width M1 enable bar at this pin.
            self.add_layout_pin_rect_center(text="en", layer="m1",
                                            offset=vector(0.5 * W, y_en),
                                            width=W, height=0.10)
        self.nwell(nA1 + 0.285 + g.IMP_POLY, nB0 - 0.285 - g.IMP_POLY)
        for x in (xl, xr):
            self.add_tap(x, y_gA, "p")
            self.add_tap(x, y_v, "n")
            self.add_tap(x, y_gB, "p")

        self.add_strip(x4, nA0, wN, ["bl", "n1", "gnd", "n2", "br"],
                       ["en", "dinb", "din", "en"], "nmos", "top", "A",
                       nocontact=("n1", "n2"))
        self.add_strip(x1, pB0, wP, ["dinb", "vdd"], ["din"], "pmos", "top", "B")
        self.add_strip(x1, nB0, wP, ["dinb", "gnd"], ["din"], "nmos", "bottom", "B")
        if OPTS.netlist_only:
            return
        self.fix_special_pins({"gnd": {"A": y_gA, "B": y_gB}, "vdd": {"A": y_v, "B": y_v}})

        # crossings: en band -> A (xl); dinb B -> A (xl); din bottom -> A -> B (xr)
        self.pin("A", xl, y_en, "line", "en")
        self.via1_m2pad(xl, y_en)
        self.pin("A", xl, y_v, "line", "dinb")
        self.pin("B", xl, y_v, "line", "dinb")
        self.pin("A", xr, 0.0, "line", "din")
        self.pin("A", xr, y_v, "line", "din")
        self.pin("B", xr, y_v, "line", "din")

        m4xs = [x1 - 0.30, x1 + self.strip_length(1) + 0.30]
        self.plan_m4("A", m4xs)
        self.plan_m4("B", m4xs)
        self.route_channel("A", nA1 + 0.12, y_v - 0.40, order=["en", "din", "dinb"])
        self.route_channel("B", pB1 + 0.12, nB0 - 0.12)

        for name, x in (("bl", self.bl_x), ("br", self.br_x)):
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(x - 0.05, 0), width=0.10, height=self.height)
        self.add_layout_pin(text="din", layer="m2",
                            offset=vector(xr - 0.05, 0), width=0.10, height=0.40)
        self.add_boundary()

    # ---- write_driver interface (shared with the library-cell class)
    get_bl_names = write_driver_base.get_bl_names
    get_br_names = write_driver_base.get_br_names
    din_name = write_driver_base.din_name
    en_name = write_driver_base.en_name
    get_w_en_cin = write_driver_base.get_w_en_cin
    build_graph = write_driver_base.build_graph

    def analytical_power(self, corner, load):
        return self.return_power()
