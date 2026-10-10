# See LICENSE for licensing information.
#
# ICsprout55 latch-type sense amplifier (generated).
"""Latch-type sense amplifier, same topology as the scn4m_subm library cell.

  bl --[P iso, en]-- dint    dintb --[P iso, en]-- br
  cross-coupled P (to vdd) and N (to tail) pair on dint/dintb
  tail --[N, en] x2-- gnd
  dout = buffer(dint) through two inverters

en low: the PMOS isolation devices conduct and the bit-line differential
reaches dint/dintb; en high: the isolation turns off and the tail turns on,
so the latch regenerates.  Pitch matched to the bitcell.
"""

from openram.base import vector
from openram.modules.sense_amp import sense_amp as sense_amp_base
from openram import OPTS
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


class ics55_sense_amp(g.ics55_gen):

    def __init__(self, name="sense_amp"):
        super().__init__(name)
        self.add_pin_list(["bl", "br", "dout", "en", "vdd", "gnd"],
                          ["INPUT", "INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"])
        self.width, self.bl_x, self.br_x = self.bitline_x()
        self.build()

    def build(self):
        W = self.width
        xl = self.snap(self.bl_x + 0.235)
        xr = self.snap(self.br_x - 0.235)
        len4 = self.strip_length(4)
        len2 = self.strip_length(2)
        cx = 0.5 * (xl + xr)
        x4 = self.snap(cx - 0.5 * len4)
        x2 = self.snap(cx - 0.5 * len2)

        y_en = 0.10
        y_gA = 0.45
        nA0 = y_gA + 0.55
        wN = 0.60
        nA1 = nA0 + wN
        pA0 = nA1 + 1.92
        wP = 0.45
        pA1 = pA0 + wP
        y_v = pA1 + 0.55
        pB0 = y_v + 0.55
        pB1 = pB0 + wP
        nB0 = pB1 + 1.68
        nB1 = nB0 + wP
        y_gB = nB1 + 0.55
        self.height = self.snap(y_gB + 0.085 + 0.12)

        # rails, wells, taps
        self.add_rail("gnd", y_gA)
        self.add_rail("vdd", y_v)
        self.add_rail("gnd", y_gB)
        if not OPTS.netlist_only:
            self.add_layout_pin_rect_center(text="en", layer="m1",
                                            offset=vector(0.5 * W, y_en),
                                            width=W, height=0.10)
        self.nwell(nA1 + 0.285 + g.IMP_POLY, nB0 - 0.285 - g.IMP_POLY)
        for x in (xl, xr):
            self.add_tap(x, y_gA, "p")
            self.add_tap(x, y_v, "n")
            self.add_tap(x, y_gB, "p")

        # devices
        self.supply = {"gnd": None, "vdd": None}
        sA = self.add_strip(x4, nA0, wN, ["dint", "tail", "gnd", "tail", "dintb"],
                            ["dintb", "en", "en", "dint"], "nmos", "top", "A")
        sP = self.add_strip(x4, pA0, wP, ["bl", "dint", "vdd", "dintb", "br"],
                            ["en", "dintb", "dint", "en"], "pmos", "bottom", "A")
        sPB = self.add_strip(x2, pB0, wP, ["doutb", "vdd", "dout"],
                             ["dint", "doutb"], "pmos", "top", "B")
        sNB = self.add_strip(x2, nB0, wP, ["doutb", "gnd", "dout"],
                             ["dint", "doutb"], "nmos", "bottom", "B")
        if OPTS.netlist_only:
            return
        self.fix_special_pins({"gnd": {"A": y_gA, "B": y_gB}, "vdd": {"A": y_v, "B": y_v}})

        # crossings
        self.pin("A", xl, y_en, "line", "en")
        self.via1_m2pad(xl, y_en)
        self.pin("A", xl, y_v, "line", "dint")
        self.pin("B", xl, y_v, "line", "dint")
        self.pin("B", xr, 0.0, "line", "dout")

        m4xs = [x4 + 0.30 + 0.30 * i for i in range(6)]
        self.plan_m4("A", m4xs)
        self.plan_m4("B", m4xs)
        self.route_channel("A", nA1 + 0.12, pA0 - 0.12)
        self.route_channel("B", pB1 + 0.12, nB0 - 0.12)

        # bit lines (full height) and dout pin at the bottom edge
        for name, x in (("bl", self.bl_x), ("br", self.br_x)):
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(x - 0.05, 0), width=0.10, height=self.height)
        # dout on M3 over the NMOS strip (no M3 there), clear of the M3
        # supply rails the array runs at the rail heights; parents land on it
        # with a single via3.
        y_dout = self.snap(nA0 + 0.5 * wN)
        self.add_rect_center(layer="via2", offset=vector(xr, y_dout), width=g.CT, height=g.CT)
        self.add_rect_center(layer="m2", offset=vector(xr, y_dout), width=g.M2W, height=g.CT + 0.08)
        self.add_layout_pin_rect_center(text="dout", layer="m3",
                                        offset=vector(xr, y_dout), width=0.17, height=0.34)
        self.add_boundary()

    # ---- sense_amp interface (shared with the library-cell class)
    get_bl_names = sense_amp_base.get_bl_names
    get_br_names = sense_amp_base.get_br_names
    dout_name = sense_amp_base.dout_name
    en_name = sense_amp_base.en_name
    get_cin = sense_amp_base.get_cin
    get_stage_effort = sense_amp_base.get_stage_effort
    analytical_power = sense_amp_base.analytical_power
    build_graph = sense_amp_base.build_graph
    is_non_inverting = sense_amp_base.is_non_inverting
    get_on_resistance = sense_amp_base.get_on_resistance
    get_input_capacitance = sense_amp_base.get_input_capacitance
    get_intrinsic_capacitance = sense_amp_base.get_intrinsic_capacitance
    cacti_rc_delay = sense_amp_base.cacti_rc_delay

    def get_enable_name(self):
        return self.en_name
