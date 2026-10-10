# See LICENSE for licensing information.
#
# ICsprout55 D flip-flop slot: foundry DFF + well taps in a fixed footprint.
"""Wrap the selected foundry DFF (7T or 9T) in one shared 9-track slot.

Both ICsprout55 standard-cell libraries are tapless, so a DFF needs well taps
from its surroundings.  This cell adds an N-tap and a P-tap in a column to
the right of the DFF and normalizes the interface, so the same footprint is
used whichever library is selected:

* slot width  ``SLOT_W`` (the 7T DFF is the wider cell), height 1.8 um;
* gnd rail centred on y=0 with the library rail thickness, vdd rail centred
  on y=1.8 (for the 1.4 um 7T cell the vdd rail is strapped up in the tap
  column; the 7T N-well is extended up to the 9T N-well top);
* D, Q and clk are brought to M2 with a bare via1 on the foundry M1 pin, so
  the foundry M1 geometry is never touched.

The foundry cell layout itself is not modified.
"""

from openram.base import design, vector
from openram.modules.dff import dff as hard_dff
from openram.sram_factory import factory
from openram.tech import drc, spice, stdcell_variant


SLOT_H = 1.8      # 9-track row height
SLOT_W = 5.2      # widest DFF (7T, 4.4) + tap column
NW_TOP = 2.0      # 9T N-well top (mirrored rows share the well)
TAP_X = 4.75      # tap active left edge
CLK_X = 0.225     # left edge of the M2 clock strip


class ics55_dff(design):

    def __init__(self, name="dff"):
        super().__init__(name)
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

        # --- signal pins on M2 (bare via1 on the foundry M1 pin)
        for name in ("D", "Q", "clk"):
            self._m2_pin(name)

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

    def _m2_pin(self, name):
        """Bring a foundry M1 pin to M2 with a bare via1 (no extra M1)."""
        pin = max(self.dff_inst.get_pins(name), key=lambda p: p.area())
        g = drc["grid"]
        snap = lambda v: round(v / g) * g
        v = drc["minwidth_via1"]
        ey = 0.045   # M1 enclosure of via1 along the pin (VIAn_EN_2b 0.04)
        x = snap(pin.cx())
        if name == "clk":
            # Via at the top of the pin, joined to a full-height M2 clock
            # strip at x=CLK_X (dff_array overlays its own strip and M3 via on
            # the published pin).  The strip clears the supply via stacks
            # parents drop on the rail ends at x=0 and the D pad.
            y = snap(pin.uy() - ey - 0.5 * v)
            self.add_rect_center(layer="via1", offset=vector(x, y), width=v, height=v)
            left = min(CLK_X, x - 0.5 * v - 0.005)
            right = max(CLK_X + 0.10, x + 0.5 * v + 0.005)
            self.add_rect(layer="m2",
                          offset=vector(left, y - 0.5 * v - 0.04),
                          width=right - left,
                          height=v + 0.08)
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(CLK_X, 0),
                                width=0.10, height=SLOT_H)
        else:
            # Via at the bottom of the pin; the pad grows downward to meet
            # M2 minimum area, away from the clock jog above it.
            y = snap(pin.by() + ey + 0.5 * v)
            self.add_rect_center(layer="via1", offset=vector(x, y), width=v, height=v)
            # Publish only the via pad as the pin (parents size wires from the
            # pin height); the extra M2 for minimum area is plain geometry.
            top = y + 0.5 * v + 0.04
            h = 0.56
            self.add_rect(layer="m2",
                          offset=vector(x - 0.5 * v - 0.005, top - h),
                          width=v + 0.01, height=h)
            self.add_layout_pin(text=name, layer="m2",
                                offset=vector(x - 0.5 * v - 0.005, y - 0.5 * v - 0.04),
                                width=v + 0.01, height=v + 0.08)

    def get_cin(self):
        return spice["dff_in_cap"]

    def analytical_power(self, corner, load):
        return self.dff.analytical_power(corner, load)

    def build_graph(self, graph, inst_name, port_nets):
        self.add_graph_edges(graph, port_nets)
