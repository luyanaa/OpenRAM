# See LICENSE for licensing information.
#
# OpenRAM gf180mcu sense-amplifier array.
"""Sense-amp array whose supply rails stay on the leaf (m1) layer.

``sense_amp_array.route_supplies`` promotes each leaf vdd/gnd pin up to
``supply_stack[0]`` (m4 for gf180mcu) with a via stack centred on the pin.
The gf180mcu sense amp already uses m3 for the bl/br bitline trunks and m1
for its internal output straps, so a via stack at the pin centre lands on
both of them (M3.2a / M1.2a spacing errors) and cannot be routed cleanly.

The leaf vdd/gnd pins are full-width m1 rails, so they can simply be
extended across the array on m1.  The bank-level supply router does the
promotion to the metal grid at the array boundary, where there is room.
"""

from openram.base import vector
from openram.modules.sense_amp_array import sense_amp_array
from openram.tech import layer_indices as tech_layer_indices


class gf180_sense_amp_array(sense_amp_array):
    """Sense-amp array with m1 supply rails (leaf layer)."""

    def route_rails(self):
        # The baseline centres the enable bus on en_pin.lc().y, i.e. on the
        # lower edge of the 0.26 um m1 en pad.  The bus is itself 0.26 um wide,
        # so half of it (0.13 um) hangs below the pad and ends up within the
        # m1 spacing rule of the tail gate riser (DRC at y ~= 18.5,
        # once per 3.0 um column).  Centre the bus on the pad instead; the
        # per-column via stacks already land on get_pin(...).center().
        en_pin = self.amp.get_pin(self.amp.en_name)
        start_offset = en_pin.center().scale(0, 1)
        end_offset = start_offset + vector(self.width, 0)
        self.add_layout_pin_segment_center(text=self.amp.en_name,
                                           layer=self.en_layer,
                                           start=start_offset,
                                           end=end_offset)
        for inst in self.local_insts:
            self.add_via_stack_center(from_layer=en_pin.layer,
                                      to_layer=self.en_layer,
                                      offset=inst.get_pin(self.amp.en_name).center())

    def route_supplies(self):
        leaf_layer = self.local_insts[0].get_pins("vdd")[0].layer
        supply_layer = self.supply_stack[0] if leaf_layer == "m2" else leaf_layer
        if supply_layer not in tech_layer_indices:
            supply_layer = self.top_layer
        for name in ("vdd", "gnd"):
            self.route_horizontal_pins(name, layer=supply_layer)
