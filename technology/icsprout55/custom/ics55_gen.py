# See LICENSE for licensing information.
#
# ICsprout55 small-cell layout generator (sense amplifier, write driver).
"""Deterministic generator for small custom ICsprout55 cells.

ICsprout55 has no SRAM periphery cells, so the sense amplifier and the write
driver are generated here.  The style is deliberately simple and rule-driven:

* transistors are drawn as horizontal *strips* of diffusion with cell-specific
  gate pitch; every diffusion node that needs a connection gets two contacts
  and an M1 strap, every gate a poly contact pad on one side;
* a *channel* between an NMOS strip and a PMOS strip carries one M3 track per
  net; each pin is lifted to M2 by a bare via1 and joined to its track by a
  vertical M2 stub (``route_channel`` chooses a track order with no stub
  conflicts);
* M2 *crossing lines* at fixed x connect channels to each other, to pins at
  the cell edge, and to the bit lines.

The SPICE netlist uses layout-less ``ics55_mos`` device instances, so the
netlist and the drawn devices are described by the same strip calls.
"""

import itertools

from openram import debug
from openram.base import design, vector
from openram.sram_factory import factory
from openram.tech import drc, spice
from openram import OPTS

# --- geometry (um), see tech.py for the rule sources
L = 0.06            # gate length
CT = 0.09           # contact / via size
CT_PITCH = 0.23     # contact centre pitch (0.14 spacing)
END = 0.275         # end diffusion: contact centre 0.23 from the gate centre (pin grid)
LSD = 0.40          # default internal source/drain spacing (0.46 um gate pitch)
PO_EXT = 0.16       # poly end-cap past active
PAD_OFF = 0.20      # poly-contact centre past the active edge
PAD = 0.17          # poly pad (contact + 2 x 0.04)
M1PAD_H = 0.27      # gate M1 pad height (meets MET1_A_1)
M2W = 0.10          # M2 stub width
TRK_W = 0.10        # M3 track width
TRK_PITCH = 0.24    # M3 track pitch
SET_GAP = 0.12      # extra space between the lower and upper track sets
TAP_TO_ACT = 0.26   # see tech.py tap_to_active
TAP_TO_POLY = 0.25
NW_ENC = 0.16       # N-well enclosure of / spacing to active
IMP_POLY = 0.16     # implant enclosure of field poly (NP/PP_EN_3, 0.15)
RAIL_H = 0.17       # supply rail height

_mos_cache = {}


class ics55_mos(design):
    """Layout-less MOS device: SPICE subcircuit, geometry drawn by the generator."""

    def __init__(self, name, tx_type, width):
        super().__init__(name)
        self.tx_type = tx_type
        self.tx_width = width
        body = "GROUND" if tx_type == "nmos" else "POWER"
        self.add_pin_list(["D", "G", "S", "B"], ["INOUT", "INPUT", "INOUT", body])
        model = spice["nmos"] if tx_type == "nmos" else spice["pmos"]
        self.spice_device = "X{{0}} {{1}} {0} w={1}u l={2}u nf=1 mismod=0".format(
            model, width, drc("minlength_channel"))
        self.lvs_device = "M{{0}} {{1}} {0} m=1 w={1}u l={2}u".format(
            model, width, drc("minlength_channel"))
        self.width = 0
        self.height = 0

    def gds_write_file(self, gds_layout):
        # The generator draws this device's geometry directly.
        return

    def build_graph(self, graph, inst_name, port_nets):
        """Model MOS gate-to-channel and source/drain connectivity for timing."""
        self.add_graph_edges(graph, port_nets)

    def is_non_inverting(self):
        """Match OpenRAM's primitive-PTX graph polarity convention."""
        return True

def mos(tx_type, width):
    key = (tx_type, round(width, 3))
    if key not in _mos_cache:
        _mos_cache[key] = ics55_mos("ics55_{}_w{}".format(tx_type, str(round(width, 3)).replace(".", "p")),
                                    tx_type, width)
    return _mos_cache[key]


class ics55_gen(design):
    """Base class with the strip / rail / tap / channel helpers."""

    def __init__(self, name, source_drain_spacing=LSD):
        super().__init__(name)
        self.source_drain_spacing = source_drain_spacing
        self.gate_pitch = L + source_drain_spacing
        self.n_dev = 0
        self.net_pins = {}      # channel -> list of (x, y, side, net)
        self.lines = []         # (x, y0, y1, net)
        self.m4_x = {}          # (channel, net) -> [x]

    # ---------------------------------------------------------------- utils
    def snap(self, v):
        g = drc["grid"]
        return round(v / g) * g

    def strip_length(self, ngates):
        return 2 * END + ngates * L + (ngates - 1) * self.source_drain_spacing

    # ---------------------------------------------------------------- strip
    def add_strip(self, x0, y0, w, nodes, gates, tx_type, pad_side, channel, nocontact=()):
        """Draw a diffusion strip and add its devices to the netlist.

        nodes: len(gates)+1 net names
        nocontact: node nets left uncontacted (series-internal diffusion)
        gates: gate net names
        pad_side: "top" or "bottom" (side of the gate contact pads)
        channel: channel name the contacted pins are routed in
        """
        n = len(gates)
        length = self.strip_length(n)
        bulk = "gnd" if tx_type == "nmos" else "vdd"
        # netlist
        for i, g in enumerate(gates):
            self.n_dev += 1
            inst = self.add_inst(name="M{}".format(self.n_dev), mod=mos(tx_type, w))
            self.connect_inst([nodes[i], g, nodes[i + 1], bulk])
        if OPTS.netlist_only:
            return
        self.add_rect(layer="active", offset=vector(x0, y0), width=length, height=w)
        # node x centres
        xs = [x0 + 0.03 + 0.5 * CT]
        for i in range(n):
            gx = x0 + END + i * self.gate_pitch
            if i < n - 1:
                xs.append(gx + L + 0.5 * self.source_drain_spacing)
        xs.append(x0 + length - 0.03 - 0.5 * CT)
        ncont = max(1, int((w - 0.06 + 0.14) // CT_PITCH))
        cy = y0 + 0.5 * w
        cys = [cy + (k - 0.5 * (ncont - 1)) * CT_PITCH for k in range(ncont)]
        for i, net in enumerate(nodes):
            if net is None or net in nocontact:
                continue
            x = xs[i]
            for yy in cys:
                self.add_rect_center(layer="contact", offset=vector(x, yy), width=CT, height=CT)
            top = cys[-1] + 0.5 * CT
            bot = cys[0] - 0.5 * CT
            self.add_rect(layer="m1", offset=vector(x - 0.5 * PAD, bot),
                          width=PAD, height=top - bot)
            self.pin(channel, x, cy, "top" if pad_side == "top" else "bottom", net,
                     rect=(x - 0.5 * PAD, bot, x + 0.5 * PAD, top))
        # gates
        sgn = 1 if pad_side == "top" else -1
        edge = y0 + w if pad_side == "top" else y0
        far = y0 if pad_side == "top" else y0 + w
        for i, g in enumerate(gates):
            gx = x0 + END + i * self.gate_pitch
            gc = gx + 0.5 * L
            pad_y = edge + sgn * PAD_OFF
            ylo = min(far - sgn * PO_EXT, pad_y)
            yhi = max(far - sgn * PO_EXT, pad_y)
            self.add_rect(layer="poly", offset=vector(gx, ylo), width=L, height=yhi - ylo)
            self.add_rect_center(layer="poly", offset=vector(gc, pad_y), width=PAD, height=PAD)
            self.add_rect_center(layer="contact", offset=vector(gc, pad_y), width=CT, height=CT)
            m1y = pad_y + sgn * 0.5 * (M1PAD_H - PAD)
            self.add_rect_center(layer="m1", offset=vector(gc, m1y), width=PAD, height=M1PAD_H)
            self.pin(channel, gc, m1y, pad_side, g,
                     rect=(gc - 0.5 * PAD, m1y - 0.5 * M1PAD_H, gc + 0.5 * PAD, m1y + 0.5 * M1PAD_H))
        return dict(x0=x0, x1=x0 + length, y0=y0, y1=y0 + w, xs=xs,
                    pad_lo=min(edge + sgn * (PAD_OFF - 0.5 * PAD), edge + sgn * (PAD_OFF + 0.5 * PAD)) if sgn < 0 else edge,
                    pad_far=edge + sgn * (PAD_OFF + 0.5 * PAD))

    def pin(self, channel, x, y, side, net, rect=None):
        """Register an M1 pin that must reach `channel` (via1 at (x, y))."""
        self.net_pins.setdefault(channel, []).append(
            dict(x=self.snap(x), y=self.snap(y), side=side, net=net, rect=rect))

    def line(self, x, y0, y1, net, channels=()):
        """A vertical M2 crossing line; joins the given channels' tracks."""
        x = self.snap(x)
        self.lines.append((x, min(y0, y1), max(y0, y1), net))
        if not OPTS.netlist_only:
            self.add_rect(layer="m2", offset=vector(x - 0.5 * M2W, min(y0, y1)),
                          width=M2W, height=abs(y1 - y0))
        for ch in channels:
            self.net_pins.setdefault(ch, []).append(dict(x=x, y=None, side="line", net=net, rect=None))

    # ---------------------------------------------------------------- rails/taps
    def add_rail(self, name, y, pin=True):
        if OPTS.netlist_only:
            return
        if pin:
            self.add_layout_pin_rect_center(text=name, layer="m1",
                                            offset=vector(0.5 * self.width, y),
                                            width=self.width, height=RAIL_H)
        else:
            self.add_rect_center(layer="m1", offset=vector(0.5 * self.width, y),
                                 width=self.width, height=RAIL_H)

    def add_tap(self, x, y, tap_type):
        if OPTS.netlist_only:
            return
        t = self.add_via_center(layers=self.active_stack,
                                offset=vector(self.snap(x), self.snap(y)),
                                implant_type=tap_type,
                                well_type=tap_type,
                                directions=("V", "V"))
        return t

    def via1_m2pad(self, x, y):
        """Bare via1 with a narrow M2 pad (VIAn_EN_5: 0.005 / 0.04)."""
        self.add_rect_center(layer="via1", offset=vector(x, y), width=CT, height=CT)
        self.add_rect_center(layer="m2", offset=vector(x, y), width=M2W, height=CT + 0.08)

    # ---------------------------------------------------------------- channel
    def route_channel(self, channel, y_lo, y_hi, order=None):
        """Route the pins registered for `channel` on M3 tracks in [y_lo, y_hi].

        Pins below the channel (side "top") use the lower track set, pins
        above it (side "bottom") the upper set, so their M2 stubs never meet.
        A net with tracks in both sets is joined by a short M4 connector.
        Crossing lines ("line") attach to the track nearest to where they
        enter (from below: lower set first).
        """
        pins = self.net_pins.get(channel, [])
        lower, upper = [], []
        for p in pins:
            if p["side"] == "top" and p["net"] not in lower:
                lower.append(p["net"])
            if p["side"] == "bottom" and p["net"] not in upper:
                upper.append(p["net"])
        for p in pins:
            if p["side"] == "line" and p["net"] not in lower and p["net"] not in upper:
                (lower if p["y"] <= y_lo else upper).append(p["net"])
        if order:
            rank = {n: i for i, n in enumerate(order)}
            lower.sort(key=lambda n: rank.get(n, len(rank)))
            upper.sort(key=lambda n: rank.get(n, len(rank)))
        ntr = len(lower) + len(upper)
        # extra gap between the sets: stubs at one x may end on the top lower
        # and the bottom upper track (M2 via pads are 0.17 tall)
        gap = SET_GAP if lower and upper else 0
        need = (ntr - 1) * TRK_PITCH + TRK_W + gap
        avail = y_hi - y_lo
        debug.check(need <= avail + 1e-6,
                    "{}: channel {} needs {} tracks in {:.3f} um".format(self.name, channel, ntr, avail))
        base = y_lo + 0.5 * (avail - need + TRK_W)
        ty_lo = {net: self.snap(base + k * TRK_PITCH) for k, net in enumerate(lower)}
        ty_hi = {net: self.snap(base + gap + (len(lower) + k) * TRK_PITCH) for k, net in enumerate(upper)}

        def track_for(p):
            if p["side"] == "top":
                return ty_lo[p["net"]]
            if p["side"] == "bottom":
                return ty_hi[p["net"]]
            if p["y"] <= y_lo:
                return ty_lo.get(p["net"], ty_hi.get(p["net"]))
            return ty_hi.get(p["net"], ty_lo.get(p["net"]))

        # stub conflict check (same layer M2, different nets)
        segs = []
        for p in pins:
            y = track_for(p)
            y0, y1 = sorted((p["y"], y))
            segs.append((p["x"], y0 - 0.085, y1 + 0.085, p["net"]))
        for a, b in itertools.combinations(segs, 2):
            if a[3] != b[3] and abs(a[0] - b[0]) < M2W + 0.12 - 1e-6 and \
               a[1] < b[2] + 0.12 and b[1] < a[2] + 0.12:
                debug.error("{}: channel {} M2 stub conflict {} / {}".format(self.name, channel, a, b), -1)
        if OPTS.netlist_only:
            return
        for tracks in (ty_lo, ty_hi):
            for net, y in tracks.items():
                xs = [p["x"] for p in pins if track_for(p) == y and p["net"] == net]
                xs += self.m4_x.get((channel, net), [])
                if not xs:
                    continue
                # 0.045 past each via cut on the track (VIAn_EN_2b 0.04)
                x0 = min(xs) - 0.5 * CT - 0.045
                x1 = max(xs) + 0.5 * CT + 0.045
                self.add_rect(layer="m3", offset=vector(x0, y - 0.5 * TRK_W),
                              width=x1 - x0, height=TRK_W)
        for p in pins:
            y = track_for(p)
            self.add_rect_center(layer="via2", offset=vector(p["x"], y), width=CT, height=CT)
            self.add_rect_center(layer="m2", offset=vector(p["x"], y), width=M2W, height=CT + 0.08)
            if p["side"] != "line":
                self.via1_m2pad(p["x"], p["y"])
            y0, y1 = sorted((p["y"], y))
            self.add_rect(layer="m2", offset=vector(p["x"] - 0.5 * M2W, y0),
                          width=M2W, height=y1 - y0)
        # M4 connectors for nets with both a lower and an upper track
        for net in lower:
            if net not in ty_hi:
                continue
            x = self.m4_x[(channel, net)][0]
            ya, yb = ty_lo[net], ty_hi[net]
            for y in (ya, yb):
                self.add_rect_center(layer="via3", offset=vector(x, y), width=CT, height=CT)
            length = max(yb - ya + CT + 0.08, 0.56)
            self.add_rect(layer="m4", offset=vector(x - 0.5 * M2W, 0.5 * (ya + yb) - 0.5 * length),
                          width=M2W, height=length)
        return ty_lo, ty_hi

    def plan_m4(self, channel, xs):
        """Before routing: give every net that needs a lower/upper join an M4
        connector x (from `xs`, spaced >= 0.22 um)."""
        pins = self.net_pins.get(channel, [])
        lower = {p["net"] for p in pins if p["side"] == "top"}
        upper = {p["net"] for p in pins if p["side"] == "bottom"}
        both = [n for n in sorted(lower & upper)]
        debug.check(len(both) <= len(xs), "{}: not enough M4 connector positions".format(self.name))
        for net, x in zip(both, xs):
            self.m4_x[(channel, net)] = [self.snap(x)]

    def fix_special_pins(self, rail_y):
        """Supply nodes strap to their rail on M1 (rail_y[net][channel]),
        bit-line nodes jog on M1 to the bit-line tracks; only signal nets
        stay in the channels."""
        for ch in list(self.net_pins):
            keep = []
            for p in self.net_pins[ch]:
                net = p["net"]
                if net in ("vdd", "gnd") and p["rect"] is not None:
                    x0, y0, x1, y1 = p["rect"]
                    target = rail_y[net][ch]
                    lo, hi = sorted((target, 0.5 * (y0 + y1)))
                    self.add_rect(layer="m1", offset=vector(x0, lo), width=x1 - x0, height=hi - lo)
                elif net in ("bl", "br") and p["rect"] is not None:
                    bx = self.bl_x if net == "bl" else self.br_x
                    lo, hi = sorted((bx, p["x"]))
                    self.add_rect(layer="m1", offset=vector(lo - 0.085, p["y"] - 0.085),
                                  width=hi - lo + 0.17, height=0.17)
                    self.add_rect_center(layer="via1", offset=vector(bx, p["y"]), width=CT, height=CT)
                else:
                    keep.append(p)
            self.net_pins[ch] = keep

    # ---------------------------------------------------------------- misc
    def nwell(self, y0, y1):
        if not OPTS.netlist_only:
            self.add_rect(layer="nwell", offset=vector(0, y0), width=self.width, height=y1 - y0)

    def bitline_x(self):
        """x of the port-0 bl/br M2 tracks of the bitcell (pitch matching)."""
        bitcell = factory.create(module_type=OPTS.bitcell)
        names = bitcell.get_all_bitline_names() if hasattr(bitcell, "get_all_bitline_names") else ["bl", "br"]
        bl = bitcell.get_pin(names[0])
        br = bitcell.get_pin(names[1])
        return bitcell.width, bl.cx(), br.cx()
