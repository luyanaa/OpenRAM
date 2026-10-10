# See LICENSE for licensing information.
#
# ICsprout55 D flip-flop slot: foundry DFF + well taps in a fixed footprint.
"""Wrap the selected foundry DFF (7T or 9T) in one shared 9-track slot.

Both ICsprout55 standard-cell libraries are tapless, so a DFF needs well taps
from its surroundings.  This cell adds an N-tap and a P-tap in a column to
the right of the DFF and normalizes the interface, so the same footprint is
used whichever library is selected:

* slot width  ``SLOT_W`` (the 7T DFF is the wider cell), height 2.7 um
  (1.5 x the 9T row; the control-logic gates OpenRAM builds at the DFF
  height need >= 2.4 um under the ICsprout55 rules);
* gnd rail centred on y=0 with the library rail thickness, vdd rail centred
  on y=2.7; the library vdd rail (1.4 or 1.8 um) is strapped up in the tap
  column and the N-well is extended to the slot top;
* D, Q and clk are brought to M2 with a bare via1 on the foundry M1 pin, so
  the foundry M1 geometry is never touched.

The foundry cell layout itself is not modified.
"""

from openram.base import design, vector
from openram.modules.dff import dff as hard_dff
from openram.sram_factory import factory
from openram.tech import drc, spice, stdcell_variant


SLOT_H = 2.7      # 1.5 x the 9-track row: room for the 1.4/1.8 um DFFs and
                  # for the control-logic gates built at this height
SLOT_W = 5.2      # widest DFF (7T, 4.4) + tap column
NW_TOP = SLOT_H + 0.2  # mirrored rows share the well
TAP_X = 4.75      # tap active left edge
CLK_X = 0.225     # left edge of the M2 clock strip
CLK_RAIL_Y = 2.25 # requested array clock rail height (above every DFF)
D_SHIFT = 0.45    # D pin published this far right of the foundry pin


class ics55_dff(design):

    def __init__(self, name="dff"):
        super().__init__(name)
        # dff_array/dff_buf_array put their M3 clock rail here: high in the
        # slot, away from the D/Q pins that parents land via stacks on.
        self.clk_rail_y = CLK_RAIL_Y
        self.create_netlist()
        self.create_layout()

    def create_netlist(self):
        self.add_pin_list(["D", "Q", "clk", "vdd", "gnd"],
                          ["INPUT", "OUTPUT", "INPUT", "POWER", "GROUND"])
        self.dff = hard_dff(name="dff")
        self.dff_inst = self.add_inst(name="dff", mod=self.dff)
        # Foundry wells (VNW/VPW) are tied to the supplies by this cell's taps.
        self.connect_inst(["D", "Q", "clk", "vdd", "gnd", "vdd", "gnd"])

    def create_layout(self):
        self.width = SLOT_W
        self.height = SLOT_H
        self.dff_inst.place(vector(0, 0))

        cell_h = self.dff.height
        gnd = max(self.dff_inst.get_pins("gnd"), key=lambda p: p.width())
        vdd = max(self.dff_inst.get_pins("vdd"), key=lambda p: p.width())
        rail_h = gnd.height()
        nw_bot = self._lib_nwell_bottom()

        # --- N-well over the slot (taps, and 7T extension up to NW_TOP)
        self.add_rect(layer="nwell",
                      offset=vector(0, nw_bot),
                      width=self.width,
                      height=NW_TOP - nw_bot)

        # --- supply rails: drawn with the library rail thickness, but the
        # published pins are minimum-width so parents (e.g. dff_buf) that
        # copy the pin height do not draw thick rails over their own cells.
        self.add_rect_center(layer="m1",
                             offset=vector(0.5 * self.width, 0),
                             width=self.width, height=rail_h)
        self.add_rect_center(layer="m1",
                             offset=vector(0.5 * self.width, SLOT_H),
                             width=self.width, height=vdd.height())
        self.add_layout_pin_rect_center(text="gnd", layer="m1",
                                        offset=vector(0.5 * self.width, 0),
                                        width=self.width, height=self.m1_width)
        self.add_layout_pin_rect_center(text="vdd", layer="m1",
                                        offset=vector(0.5 * self.width, SLOT_H),
                                        width=self.width, height=self.m1_width)
        if abs(cell_h - SLOT_H) > 0.001:
            # Extend the library vdd rail into the tap column and strap it
            # up to the slot vdd rail.
            x0 = self.dff.width
            self.add_rect(layer="m1",
                          offset=vector(x0, vdd.by()),
                          width=self.width - x0,
                          height=vdd.height())
            strap_x = TAP_X - 0.2
            self.add_rect(layer="m1",
                          offset=vector(strap_x, vdd.by()),
                          width=0.2,
                          height=SLOT_H - vdd.by())

        # --- well taps in the right-hand column
        tap_w = 0.15
        cx = TAP_X + 0.5 * tap_w
        ptap = self.add_via_center(layers=self.active_stack,
                                   offset=vector(cx, 0.32),
                                   implant_type="p",
                                   well_type="p",
                                   directions=("V", "V"))
        self.add_rect(layer="m1",
                      offset=vector(cx - 0.5 * ptap.mod.second_layer_width, 0),
                      width=ptap.mod.second_layer_width,
                      height=0.32)
        ntap_y = max(nw_bot + 0.16, 0.9) + 0.2
        ntap = self.add_via_center(layers=self.active_stack,
                                   offset=vector(cx, ntap_y),
                                   implant_type="n",
                                   well_type="n",
                                   directions=("V", "V"))
        self.add_rect(layer="m1",
                      offset=vector(cx - 0.5 * ntap.mod.second_layer_width, ntap_y),
                      width=ntap.mod.second_layer_width,
                      height=SLOT_H - ntap_y)

        # --- signal pins on M2 (bare via1 on the foundry M1 pin).  CK and D
        # vias go to opposite ends of their pins (the 7T pins are only
        # 0.215 um apart), whichever order separates them more.
        ck = max(self.dff_inst.get_pins("clk"), key=lambda p: p.area())
        dp = max(self.dff_inst.get_pins("D"), key=lambda p: p.area())
        lo_hi = abs(self._end_y(ck, "bottom") - self._end_y(dp, "top"))
        hi_lo = abs(self._end_y(ck, "top") - self._end_y(dp, "bottom"))
        ck_end, d_end = ("bottom", "top") if lo_hi >= hi_lo else ("top", "bottom")
        self._m2_pin("clk", ck_end)
        self._m2_pin("D", d_end)
        self._m2_pin("Q", "bottom")

        self.add_boundary()

    def _lib_nwell_bottom(self):
        """Bottom of the foundry DFF N-well (from its GDS)."""
        from openram.base import utils
        from openram.tech import GDS, layer
        import os
        from openram import OPTS
        try:
            import klayout.db as kdb
            gds = os.path.join(OPTS.openram_tech, "gds_lib", self.dff.cell_name + ".gds")
            ly = kdb.Layout()
            ly.read(gds)
            li = ly.find_layer(layer["nwell"][0], layer["nwell"][1])
            bot = min(s.bbox().bottom for s in ly.top_cell().shapes(li).each())
            return bot * ly.dbu
        except Exception:
            # 9T default
            return 0.84 if stdcell_variant["height"] > 1.6 else 0.615

    def _end_y(self, pin, end):
        """Via1 centre y at one end of a foundry pin (M1 enclosure 0.045)."""
        g = drc["grid"]
        v = drc["minwidth_via1"]
        y = pin.by() + 0.045 + 0.5 * v if end == "bottom" else pin.uy() - 0.045 - 0.5 * v
        return round(y / g) * g

    def _m2_pin(self, name, end):
        """Bring a foundry M1 pin to M2 with a bare via1 (no extra M1)."""
        pin = max(self.dff_inst.get_pins(name), key=lambda p: p.area())
        g = drc["grid"]
        snap = lambda v: round(v / g) * g
        v = drc["minwidth_via1"]
        x = snap(pin.cx())
        y = self._end_y(pin, end)
        self.add_rect_center(layer="via1", offset=vector(x, y), width=v, height=v)
        if name == "clk":
            # Jog on M2 to a full-height clock strip at x=CLK_X (the array
            # overlays its own strip and M3 rail via on the published pin).
            left = min(CLK_X, x - 0.5 * v - 0.005)
            right = max(CLK_X + 0.10, x + 0.5 * v + 0.005)
            self.add_rect(layer="m2",
                          offset=vector(left, y - 0.5 * v - 0.04),
                          width=right - left,
                          height=v + 0.08)
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(CLK_X, 0),
                                width=0.10, height=SLOT_H)
        elif name == "D":
            # Via pad, then an M2 stub to the right; the pin is published at
            # the stub end so a parent's landing pad stays clear of the clock.
            xp = snap(x + D_SHIFT)
            self.add_rect(layer="m2", offset=vector(x - 0.5 * v - 0.005, y - 0.5 * v - 0.04),
                          width=v + 0.01, height=v + 0.08)
            self.add_rect(layer="m2", offset=vector(x - 0.05, y - 0.05),
                          width=xp - x + 0.10, height=0.10)
            self.add_layout_pin_rect_center(text=name, layer="m2", offset=vector(xp, y),
                                            width=0.10, height=v + 0.08)
        else:
            # Publish only the via pad as the pin (parents size wires from the
            # pin height); extra M2 for minimum area is plain geometry that
            # grows *into* the slot (mirrored array rows abut at y=0).
            bot = y - 0.5 * v - 0.04
            self.add_rect(layer="m2",
                          offset=vector(x - 0.5 * v - 0.005, bot),
                          width=v + 0.01, height=0.56)
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(x - 0.5 * v - 0.005, y - 0.5 * v - 0.04),
                                width=v + 0.01, height=v + 0.08)

    def get_cin(self):
        return spice["dff_in_cap"]

    def analytical_power(self, corner, load):
        return self.dff.analytical_power(corner, load)

    def build_graph(self, graph, inst_name, port_nets):
        self.add_graph_edges(graph, port_nets)
