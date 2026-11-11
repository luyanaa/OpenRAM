# See LICENSE for licensing information.
#
# OpenRAM gf180mcu data port.
"""Data port whose write-driver/sense-amp bitline bridge runs on m3.

``port_data.route_write_driver_to_sense_amp`` uses ``connect_bitlines``,
which z-jogs on the *sense amplifier's* bitline pin layer.  In gf180mcu that
pin is m2, and the sense amp sits between the write driver and the bitcell /
precharge side, so the generic route draws an m2 stripe straight through the
amplifier body.  The gf180mcu sense amp keeps its own vdd/gnd trunks on m2
(see ``custom/gf180_sense_amp.py``), so that stripe would short the bitlines
to the supplies -- and it does: Magic reports M2.2a spacing around the gnd
trunk, and the overlap with the gnd jog is a silent short.

Both arrays carry a full-height m3 bitline stripe at the same x, and those
stripes are exposed at the two facing cell edges.  A short m3 bridge between
them therefore connects the nets without entering the amplifier's m2 area.
The sense-amp side already uses m3 for its bitline stripes, so this also
matches the layer that the bitcell's m2 bitlines are promoted to at the
sense-amp pads.
"""

from openram.base import vector
from openram.modules.port_data import port_data


class gf180_port_data(port_data):
    """port_data with an m3 write-driver/sense-amp bitline bridge."""

    def route_supplies(self):
        """Promote the child supply rails, landing wide-pin vias at one end.

        ``copy_power_pin`` defaults to the pin *centre*.  The sense-amp
        array's vdd/gnd pins are full-width m1 straps, so that centre lands
        in the middle of a bit column.  The promotion via stack's 0.38 um m3
        pad then sits 0.275 um from the end of the bitline trunk (M3.2a) and
        overlaps the array's own m1 strap ("can't abut between subcells").
        Landing the via at the left end of a wide strap keeps it in the empty
        margin left of the first sense amplifier, where there is no m2/m3.
        """
        for inst in self.insts:
            for name in ("vdd", "gnd"):
                for pin in inst.get_pins(name):
                    if pin.layer in self.pwr_grid_layers:
                        self.add_layout_pin(pin.name, pin.layer, pin.ll(),
                                            pin.width(), pin.height())
                        continue
                    if pin.width() > 1.0:
                        loc = vector(pin.lx() + 0.19, pin.cy())
                    else:
                        loc = pin.center()
                    self.copy_power_pin(pin, loc=loc)

    def route_write_driver_to_column_mux_or_precharge_array(self, port):
        """Skip the redundant write-driver/precharge bitline route.

        With no column mux the write driver and the precharge array sit at
        opposite ends of the same bitline, and ``route_write_driver_to_sense_amp``
        already joins the write driver to the sense amp on m3 while the
        precharge joins the sense amp on m2.  Drawing the generic route as
        well would run a second m2 stripe from the precharge (y ~7) all the
        way up to the write driver (y ~30), straight through the sense-amp
        array's m2 supply pads.  Keep the generic behaviour once a column mux
        exists, since then the two are no longer the same net.
        """
        if self.col_addr_size == 0:
            return
        super().route_write_driver_to_column_mux_or_precharge_array(port)

    @staticmethod
    def _trunk_x(inst, tx):
        """x of a leaf's m3 bitline stripe in this instance's coordinates."""
        if inst.mirror in ("MY", "XY"):
            return inst.offset.x + inst.mod.width - tx
        return inst.offset.x + tx

    def route_write_driver_to_sense_amp(self, port):
        sa = self.sense_amp_array_inst
        wd = self.write_driver_array_inst
        amp = sa.mod.amp
        drv = wd.mod.driver

        # The stripe must land on the write driver's m3 trunk, not merely on
        # its m2 pad, so overshoot the facing edge by a little.
        y0 = sa.ur().y
        y1 = wd.by() + 0.30

        for bit in range(self.word_size + self.num_spare_cols):
            sa_inst = sa.mod.local_insts[bit]
            wd_inst = wd.mod.local_insts[bit]
            for tx_sa, tx_wd in ((amp.BL_TX, drv.BL_TX),
                                 (amp.BR_TX, drv.BR_TX)):
                x_sa = self._trunk_x(sa_inst, tx_sa)
                x_wd = self._trunk_x(wd_inst, tx_wd)
                if abs(x_sa - x_wd) > 1e-6:
                    raise RuntimeError(
                        "gf180_port_data: bitline trunk misalignment at bit "
                        "{0}: sa x={1}, wd x={2}".format(bit, x_sa, x_wd))
                self.add_rect("m3", offset=(x_sa - 0.14, y0),
                              width=0.28, height=y1 - y0)
