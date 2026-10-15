"""Optional full-swing transmission-gate column mux for ICsprout55.

The select inverter is local to each mux cell. Existing NMOS-only muxes stay
as the default; select this implementation with ics55_column_mux_style="tgate".
"""
import importlib.util as _ilu
import os as _os
import sys as _sys

if "ics55_gen" in _sys.modules:
    g = _sys.modules["ics55_gen"]
else:
    _spec = _ilu.spec_from_file_location(
        "ics55_gen", _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "ics55_gen.py"))
    g = _ilu.module_from_spec(_spec)
    _sys.modules["ics55_gen"] = g
    _spec.loader.exec_module(g)

from openram import OPTS
from openram.base import vector
from openram.sram_factory import factory
from openram.tech import drc, parameter


class ics55_column_mux_tgate(g.ics55_gen):
    """Differential BL/BR transmission gates with a local select inverter."""

    def __init__(self, name="column_mux", bitcell_bl="bl", bitcell_br="br", **kwargs):
        super().__init__(name)
        self.bitcell_bl = bitcell_bl
        self.bitcell_br = bitcell_br
        n_size = float(getattr(OPTS, "ics55_column_mux_nmos_size", 8))
        p_size = float(getattr(OPTS, "ics55_column_mux_pmos_size", 8))
        if n_size <= 0 or p_size <= 0:
            raise ValueError("ICS55 column mux transistor sizes must be positive")
        self.pass_n_width = n_size * drc("minwidth_tx")
        self.pass_p_width = p_size * drc("minwidth_tx")
        self.inv_n_width = drc("minwidth_tx")
        self.inv_p_width = parameter["beta"] * self.inv_n_width
        self.width, self.bl_x, self.br_x = self.bitline_x()
        self.add_pin_list(["bl", "br", "bl_out", "br_out", "sel", "gnd", "vdd"],
                          ["INOUT", "INOUT", "INOUT", "INOUT", "INPUT", "GROUND", "POWER"])
        self.build()

    def get_bl_names(self):
        return "bl"

    def get_br_names(self):
        return "br"

    def build(self):
        pass_length = self.strip_length(1)
        pass_x = self.snap(0.5 * (self.width - pass_length))
        br_pass_x = self.snap(pass_x + 0.75)
        inv_x = self.snap(pass_x - 0.90)

        y_gnd = 0.45
        n0_y = y_gnd + 0.55
        n1_y = n0_y + self.pass_n_width + 0.60
        inv_n_y = n1_y + self.pass_n_width + 0.60
        y_vdd = inv_n_y + self.inv_n_width + 0.60
        inv_p_y = y_vdd + 0.60
        p0_y = inv_p_y + self.inv_p_width + 0.60
        p1_y = p0_y + self.pass_p_width + 0.60
        self.height = self.snap(p1_y + self.pass_p_width + 0.75)

        if not OPTS.netlist_only:
            self.add_rail("gnd", y_gnd)
            self.add_rail("vdd", y_vdd)
            self.nwell(y_vdd - 0.20, self.height - 0.20)
            for x in (inv_x + 0.08, inv_x + pass_length - 0.08):
                self.add_tap(self.snap(x), y_gnd, "p")
                self.add_tap(self.snap(x), y_vdd, "n")

        # NMOS and PMOS pass devices form parallel switches on each differential leg.
        self.add_strip(pass_x, n0_y, self.pass_n_width, ["bl", "bl_out"],
                       ["sel"], "nmos", "top", "mux")
        self.add_strip(br_pass_x, n1_y, self.pass_n_width, ["br", "br_out"],
                       ["sel"], "nmos", "top", "mux")
        self.add_strip(pass_x, p0_y, self.pass_p_width, ["bl", "bl_out"],
                       ["sel_b"], "pmos", "bottom", "mux")
        self.add_strip(br_pass_x, p1_y, self.pass_p_width, ["br", "br_out"],
                       ["sel_b"], "pmos", "bottom", "mux")

        # Full-CMOS local inverter: its output drives the two PMOS pass gates.
        self.add_strip(inv_x, inv_n_y, self.inv_n_width, ["sel_b", "gnd"],
                       ["sel"], "nmos", "top", "mux")
        self.add_strip(inv_x, inv_p_y, self.inv_p_width, ["sel_b", "vdd"],
                       ["sel"], "pmos", "bottom", "mux")
        if OPTS.netlist_only:
            return

        self.fix_special_pins({"gnd": {"mux": y_gnd}, "vdd": {"mux": y_vdd}})
        m4_xs = [self.snap(pass_x + 0.10 + 0.24 * index) for index in range(4)]
        self.plan_m4("mux", m4_xs)
        y_track_lo = 0.70
        y_track_hi = self.height - 0.50
        low_tracks, high_tracks = self.route_channel(
            "mux", y_track_lo, y_track_hi,
            order=["bl_out", "br_out", "sel", "sel_b"])

        # Select is exposed on the gate-contact pad; the column-mux array routes
        # its decoder rail on poly and contacts this same gate.
        select_pad = next(pin for pin in self.net_pins["mux"]
                          if pin["net"] == "sel" and pin["side"] == "top")
        x0, y0, x1, y1 = select_pad["rect"]
        self.add_layout_pin_rect_center(text="sel", layer="m1",
                                        offset=vector(0.5 * (x0 + x1), 0.5 * (y0 + y1)),
                                        width=x1 - x0, height=y1 - y0)

        bitcell = factory.create(module_type=OPTS.bitcell)
        self.pin_layer = bitcell.get_pin(self.bitcell_bl).layer
        pin_width = drc("minwidth_{}".format(self.pin_layer))
        for name, x in (("bl", self.bl_x), ("br", self.br_x)):
            self.add_layout_pin(text=name, layer=self.pin_layer,
                                offset=vector(x - 0.5 * pin_width, 0),
                                width=pin_width, height=self.height)

        # Place output trunks in unused M2 channels, away from pass-device stubs
        # and the input bitline rails.
        reserved_xs = [self.bl_x, self.br_x]
        reserved_xs.extend(pin["x"] for pin in self.net_pins["mux"])
        candidates = [self.snap(0.18 + 0.24 * index)
                      for index in range(int((self.width - 0.36) / 0.24) + 1)]
        output_x = []
        for candidate in candidates:
            if all(abs(candidate - reserved) >= 0.24 for reserved in reserved_xs) and \
               all(abs(candidate - selected) >= 0.24 for selected in output_x):
                output_x.append(candidate)
                if len(output_x) == 2:
                    break
        if len(output_x) != 2:
            raise ValueError("ICS55 column mux has no two isolated M2 output tracks")
        for net, x in zip(("bl_out", "br_out"), output_x):
            y = low_tracks[net]
            signal_xs = [pin["x"] for pin in self.net_pins["mux"] if pin["net"] == net]
            track_lo = min([x] + signal_xs)
            track_hi = max([x] + signal_xs)
            self.add_rect(layer="m3", offset=vector(track_lo, y - 0.05),
                          width=track_hi - track_lo, height=0.10)
            self.add_rect_center(layer="via2", offset=vector(x, y), width=0.09, height=0.09)
            self.add_layout_pin(text=net, layer=self.pin_layer,
                                offset=vector(x - 0.5 * pin_width, 0),
                                width=pin_width, height=y + 0.5 * pin_width)

        self.add_boundary()
