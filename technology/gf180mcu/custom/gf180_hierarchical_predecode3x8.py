# See LICENSE for licensing information.
#
"""gf180mcu hierarchical_predecode3x8 for the parameterized-bitcell path.

At the pbitcell cell height (6.405 um) the and3_dec input pins A/B/C are
0.80 um apart while the historical track step is 0.86 um.  The historical
rule ("step B whenever it is closer than a step to A") therefore moves B's
track onto C's own pin band (0.06 um away); the two m1 tracks merge, which
DRC cannot see as an error but LVS extracts as in_1 shorted to in_2 (and the
whole decode rail bank with them).

This subclass keeps the 1RW (cell1rw height) layout untouched -- it is only
registered when the config selects ``bitcell = "pbitcell"`` -- and assigns
tracks with an actual clearance test: a track stays on its pin centre while
it clears every other input pin on the input layer and every track already
assigned; only a colliding input is stepped.  At the pbitcell height the
0.80 um pin pitch already clears the 0.675 um a straight track needs, so all
three inputs route on their pins with no jogs.
"""

from openram import debug
from openram.modules.hierarchical_predecode3x8 import hierarchical_predecode3x8


class gf180_hierarchical_predecode3x8(hierarchical_predecode3x8):

    def _input_track_offsets(self, inst):
        names = ["A", "B", "C", "D"][:self.number_of_inputs]
        pins = {name: inst.get_pin(name) for name in names}
        first = pins[names[0]]
        step = max(0.8, first.height() + self.m1_space)
        step_dir = -1 if getattr(inst, "mirror", "R0") == "MX" else 1
        wire = self.m1_width
        clear_track = wire + self.m1_space
        clear_pin = 0.5 * first.height() + self.m1_space + 0.5 * wire
        tracks = []
        offsets = {}
        for name in names:
            pin = pins[name]
            if pin.layer != self.input_layer:
                # Pin escaped to another layer: needs no track, constrains
                # nothing.
                offsets[name] = pin.cy()
                continue
            y = pin.cy()
            guard = 0
            while True:
                clash = any(abs(y - t) < clear_track - 1e-6 for t in tracks)
                if not clash:
                    for other, opin in pins.items():
                        if other == name or opin.layer != self.input_layer:
                            continue
                        if abs(y - opin.cy()) < clear_pin - 1e-6:
                            clash = True
                            break
                if not clash:
                    break
                y += step_dir * step
                guard += 1
                if guard > len(names) + 2:
                    debug.error("Unable to place decode input tracks for {0}".format(inst.name), -1)
            tracks.append(y)
            offsets[name] = y
        return offsets

    def _input_y_offset(self, pin, index, inst=None):
        if pin.layer != self.input_layer or inst is None:
            return pin.cy()
        name = ["A", "B", "C", "D"][index]
        return self._input_track_offsets(inst)[name]
