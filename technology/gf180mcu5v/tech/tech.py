# See LICENSE for licensing information.
#
# Copyright (c) 2016-2021 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import os
from openram import drc as d

"""
File containing the process technology parameters for Global Foundaries 180nm
"""

###################################################
# Custom modules
###################################################

# This uses the default classes to instantiate module from
# '$OPENRAM_HOME/compiler/modules'.
# Using tech_modules['cellname'] you can override each class by providing a custom
# implementation in '$OPENRAM_TECHDIR/modules/'
# For example: tech_modules['contact'] = 'contact_scn4m'
tech_modules = d.module_type()

tech_modules["bitcell_1port"] = "gf180_bitcell"
# Use the parameterized NAND for the decoders.  The legacy hard cell in
# gds_lib (gf180mcu_3v3__nand2_1_dec) is only 2.71 um tall, while the
# parameterized inverter/NAND gate needs >= ~4.1 um at this technology's
# 0.57 um minimum diffusion width; the decoder cells are sized to the bitcell
# height (4.84 um), so the hard cell cannot be paired with them.
tech_modules["nand2_dec"] = "pnand2"
# No extracted sense-amp macro exists for this process; the PDK only ships
# the large single-ended column blocks of the GF 018SRAM IP.  Use the
# parameterized latch amplifier in custom/gf180_sense_amp.py instead.
tech_modules["sense_amp"] = "gf180_sense_amp"
tech_modules["write_driver"] = "gf180_write_driver"
# The vdd tap of the generic precharge sits 0.27 um from the br stripe, just
# under this technology's 0.28 um m2 spacing; shift the tap 0.03 um left.
tech_modules["precharge"] = "gf180_precharge"
# The array keeps its vdd/gnd rails on the leaf m1 layer: a via stack up to
# the m4 power grid at the pin centre would collide with the amp's own m3
# bitline trunks and m1 output straps (see custom/gf180_sense_amp_array.py).
tech_modules["sense_amp_array"] = "gf180_sense_amp_array"
# The write-driver/sense-amp bitline bridge must run on m3: the generic
# port_data route uses the sense amp's m2 bitline pin and would cross (and
# short to) the amp's m2 supply trunks.  See custom/gf180_port_data.py.
tech_modules["port_data"] = "gf180_port_data"

###################################################
# Custom cell properties
###################################################
cell_properties = d.cell_properties()

# is there a better way to tell if the user overrode the port order than this?
# this is needed to correctly create the bitcell_pins list in the bitcell_base_array
cell_properties.override_bitcell_1port_order = True
cell_properties.bitcell_1port.port_order = ['bl', 'br', 'wl', 'vdd', 'gnd']
cell_properties.bitcell_1port.port_types = ["OUTPUT", "OUTPUT", "INPUT", "POWER", "GROUND"]
cell_properties.bitcell_1port.port_map = {'bl': 'BL',
                                          'br': 'BR',
                                          'wl': 'WL',
                                          'vdd': 'VDD',
                                          'gnd': 'GND'}

cell_properties.bitcell_1port.wl_layer = "m3"
cell_properties.bitcell_1port.bl_layer = "m2"
cell_properties.bitcell_1port.vdd_layer = "m1"
cell_properties.bitcell_1port.gnd_layer = "m1"

# There is only one physical bitcell (cell1rw) in this technology.  The replica
# column and the dummy columns use the same 6T cell - the replica bitcell
# differs only in how the column is biased/used, and the dummy cell only needs
# to present the same bitline/wordline load.  Point both at the extracted cell
# instead of shipping byte-identical copies of the GDS/SPICE under other names.
cell_properties.names["replica_bitcell_1port"] = "cell1rw"
cell_properties.names["dummy_bitcell_1port"] = "cell1rw"
tech_modules["replica_bitcell_1port"] = "gf180_replica_bitcell"
tech_modules["dummy_bitcell_1port"] = "gf180_dummy_bitcell"
# cell1rw has no room for a well tie inside its pitch, so the outer dummy
# column uses a tapped copy of the same cell.  The wells are continuous across
# columns, so one tapped column ties every nwell/pwell band in the array.
tech_modules["tap_bitcell"] = "gf180_tap_bitcell"

# cell1rw is drawn with 0.34 um of well overhang on every side, so its
# boundary layer (0/0) bbox is 3.68 x 5.18.  The abutment pitch used by the
# foundry array is 3.0 x 4.5.  Adjacent cells are intended to overlap: the
# columns share the poly/wordline strapping (0.36 um) and the mirrored row
# pairs merge their nmos and pmos diffusions (0.42 um each).  Place the array
# on this logical pitch while keeping the drawn GDS untouched.
cell_properties.bitcell_1port.array_size = (3.0, 4.5)

cell_properties.nand2_dec.port_order = ['A', 'B', 'Z', 'vdd', 'gnd']
cell_properties.nand2_dec.port_map = {'A': 'A',
                                      'B': 'B',
                                      'Z': 'Z',
                                      'vdd': 'VDD',
                                      'gnd': 'GND'}

# DFF hard cell: gf180mcu_fd_sc_mcu7t5v0__dffq_1, re-exported as gf180_dff
# (see gds_lib/gf180_dff.gds and sp_lib/gf180_dff.sp).  The PDK cell labels are
# D/CLK/Q/VDD/VSS; VSS is renamed to GND in both views.
cell_properties.dff.port_order = ['D', 'Q', 'clk', 'vdd', 'gnd']
cell_properties.dff.port_map = {'D': 'D',
                                'Q': 'Q',
                                'clk': 'CLK',
                                'vdd': 'VDD',
                                'gnd': 'GND'}
cell_properties.names["dff"] = "gf180_dff"


cell_properties.ptx.model_is_subckt = True

cell_properties.strap_placement = 8     # this means strap cell gets placed after every 8 bitcells

cell_properties.names["nand2_dec"] = ["gf180mcu_3v3__nand2_1_dec"]

###################################################
# Custom layer properties
###################################################
layer_properties = d.layer_properties()
# The channel router drops the bitline pins from m2 to m1 at each cell edge.
# Its m1 via landing is 0.26 + 2*0.15 = 0.56 um tall, and the precharge's m1
# vdd rail sits 0.04 um below the cell edge, so the landing shorts to (or at
# best violates 0.04 um of) the supply rail.  Use the m2 z-jog instead: the
# precharge-to-sense-amp channel is only ~2.5 um tall and empty on m2.
layer_properties.port_data.channel_route_bitlines = False
# The predecoder draws its AND-cell supplies as horizontal m1 straps centred
# on the cell boundary.  Its output-inverter rail is placed one m1 pitch below
# that boundary, which leaves only 0.08 um between the m1->m2 via landing and
# the supply strap (rule: 0.23).  Shift the output rail down 0.16 um so the
# landing clears the strap while staying inside the row.
layer_properties.hierarchical_predecode.output_route_supply_clearance = 0.16
# The generic RBL driver routes its B input to vdd with an m1 jog 3 m1 pitches
# to the left.  That jog crosses the A input (shorts wl_en to vdd) and lands
# 0.09 um from the A via stack.  Tie B at its own pin instead.
layer_properties.port_address.rbl_supply_at_pin = True
# The pgate of the pnand2/pinv cells is drawn 0.6 um beyond the declared cell
# width, so the row decoder's n-well reaches its declared right edge + 0.6.
# Abutting the wordline driver array there leaves only 0.12 um to its n-well
# (rule: 0.6).  Shift the driver array by the pgate overhang.
layer_properties.port_address.decoder_driver_spacing = 0.6
# The wordline via stack landing pad is 0.38 um wide while the minimum m3
# width is 0.28.  Drawing the via-to-bitcell route at the minimum width leaves
# a 0.05/0.08 um notch against the pad (rule: 0.28).  Use the pad width.
layer_properties.bank.wordline_via_strap = True
# The pinvbuf clock/column buffer exposes its vdd/gnd as full-width m1 rails of
# 10.16 um.  bank.route_supplies punches an m1->m3 via stack on each one; at the
# rail centre the 0.26x0.56 m1 pad lands 0.16 um from the second inverter's
# output strap (M1.2a).  The last ~1.07 um of every rail is empty m1, so put the
# stack 0.66 um in from the right edge: the pad spans 0.53..0.79 from the edge
# and keeps 0.28 to the nearest signal strap.  This inset stack is the only
# supply landing exposed for these rails; copying the full-width m1 pin too
# would let the top-level router punch a second stack at the rail centre.
layer_properties.bank.supply_via_inset_from_right = 0.66
# The control-logic gnd trunk sits at the left edge of the control rows, and
# one of its row supply via landings lands only 0.20 um below the wl_en m3 bus
# (the bus runs between two row pins).  The landing pad is 0.38 um tall inside
# a 0.56 um tall m2 pin, so move the taps 0.12 um down the pin: the pad then
# clears the bus by 0.32 um and still has the m2 enclosure it needs.
layer_properties.control_logic.supply_via_shift_y = 0.16
# The top-level m3 supply grid runs a rail at y = 48.855..49.135 straight over
# the delay chain.  The delay chain distributes each stage's output on m3 to
# all of its fanout loads, and one row's 0.38 um landing pads sit at
# 48.220..48.600 -- only 0.255 um below that rail, where M3.2a wants 0.28.
# (The 0.28 um wire itself is at 48.270..48.550 and clears at 0.305, so only
# the pads are at fault.)  Slide the pads 0.07 um toward the -y edge of their
# 0.56 um pins: the gap becomes 0.325 um and the pads still overlap both the
# pins (bottom edge 48.150 vs 48.130) and the wire.  The supply landings above
# already use their own shift.  (control_logic_base places the delay chain
# with mirror="MX", so this cell-coordinate value moves the pads down on the
# die.)
layer_properties.control_logic.delay_chain_via_shift_y = 0.07
# The bank's control bus is a full-height m2 stripe in the channel between the
# column decoder and the port data.  Carrying the column-mux select lines
# across it with the default m1_stack route (m1 trunk, m2 jog) shorts every
# select line to p_en_bar/s_en/w_en.  Flip the channel to its non-preferred
# directions instead: m2 vertical trunks in the empty channel, m1 horizontal
# jogs passing *under* the m2 bus.  (Raising the channel to m3/m4 instead
# collides with the supply grid.)
layer_properties.bank.column_select_directions = "nonpref"
# The column-mux select rails are drawn on m1 at the m1 pitch (0.49), but
# bank.route_column_address_lines routes each of them to the column decoder
# with a horizontal m2 run at the rail's y, and each run ends in a 0.38 um m2
# via pad landing on a 0.56 um tall m1 pad.  Two adjacent runs then sit 0.49
# apart with 0.38 pads -- i.e. 0.11 of m2 space where 0.28 is required
# (M2.2a) -- and the pads touch the neighbouring via's enclosure (V1.4).
# The binding constraint is the m1 landing pad: 0.56 + 0.23 = 0.79.  Use 0.80
# for the select rails so both the runs and the pads clear.
layer_properties.column_mux_array.select_pitch = 0.80
# The dff row is sandwiched between the m1 vdd/gnd buses, and the column
# decoder's input pad sits above the pinvbuf's own bottom gnd rail.  The
# default m1_stack column-address channel route therefore lands its via
# stacks on the dff-row buses and runs its bank-side m1 leg through the gnd
# rail, shorting every column address to both supplies.  Both endpoints are
# clear on m2 (the row just below the dff is empty except for the route), so
# run the whole net on m2 and drop to the bank pad with a single via there.
layer_properties.bank.col_addr_m2_route = True
# The gf180mcu foundry SRAM macros do not use a deep n-well isolation plane
# (their GDS has no 12/0 shapes).  Drawing one over the whole macro with
# add_dnwell also draws an n-well moat ring, which violates DF.4b/NW.5 against
# the standard-cell n-wells and adds NW.2a spacing failures.  Skip it.
layer_properties.sram_dnwell_isolation = False
# The sense-amp and write-driver cells have their vdd/gnd leaf rails only one
# m2 pitch apart.  Promoting both to full-width m3 straps puts two 0.38 um
# tall m3 stripes 0.405 um apart over the whole array (rule: 0.28) and, worse,
# lays them across the m3 supply mesh.  Keep the leaf rails on their own layer
# and let the top-level supply router join them with individual via pads,
# which land at the (much wider) vdd and gnd rail x positions.
layer_properties.supply_promote_leaf_rails = False
# The pins A/B/C are staggered by only 0.125 um in y, so m1 runs at the pin y
# merge the three rails of a row (and through the shared rails all 12 predecode
# nets); LVS on the 128x32 macro reported exactly that.  The pin columns have no
# room for an m1 vertical escape (the nand3 m1 plate sits 0.1..0.4 um above each
# pad), but they accept an m1->m3 via stack.  Place all three horizontally-run
# m3 tracks above the pin band, ordered A < B < C.  The vertical jog at pin x is
# only crossed by horizontals of inputs whose pin lies further right, so with
# this order no jog crosses a foreign track.  Each track also passes over the
# rail-side via pads of the inputs further right (A over B/C, B over C), so the
# center-to-center separation must be at least 0.14 (half track) + 0.19 (half
# via pad) + 0.28 (M3 spacing) = 0.61 um.  The previous 0.60/0.61 spacing left
# 0.27 between the A track and the B rail pad: 128 M3 spacing errors (two
# markers per row).  Use 0.66 for B and C.  A is different: the pin jog is the
# 0.28-wide wire over a 0.38-wide pin pad, which always leaves a 0.05-wide step
# at the pad edge.  Magic flags that step as an M3 notch while the pocket
# between the A track and the pin pad is shorter than 0.28 (it was 0.21).  Put
# A low enough that the track overlaps its own pin pad (pad top is pin y +
# 0.19, track bottom is offset - 0.14; 2.18 gives a 0.04 um overlap), which
# removes the pocket.  The higher B/C jogs have pockets taller than 0.28 and
# are not flagged.  The top track stays at 3.77 so its pad top (3.96) is
# still more than the 0.66 um bar-avoidance buffer below the predecode bars at
# the row boundary (4.5), and no bar gets pushed onto a track.  (A first
# attempt put A above the pins and B/C below; the bar placer then pushed a bar
# to B's track y and shorted two rails.)
layer_properties.hierarchical_decoder.input_route_layer = "m3"
layer_properties.hierarchical_decoder.input_track_offsets = (2.18, 3.11, 3.77)

###################################################
# GDS file info
###################################################
GDS={}
# gds units
# From http://www.cnf.cornell.edu/cnf_spie9.html: "The first
#is the size of a database unit in user units. The second is the size
#of a database unit in meters.  For example, if your library was
#created with the default units (user unit = 1 m and 1000 database
#units per user unit), then the first number would be 0.001 and the
#second number would be 10-9. Typically, the first number is less than
#1, since you use more than 1 database unit per user unit. To
#calculate the size of a user unit in meters, divide the second number
#by the first."
GDS["unit"]=(0.001,1e-6)
# default label zoom
GDS["zoom"] = 0.5

###################################################
# Interconnect stacks
###################################################

poly_stack = ("poly", "contact", "m1")
active_stack = ("active", "contact", "m1")
m1_stack = ("m1", "via1", "m2")
m2_stack = ("m2", "via2", "m3")
m3_stack = ("m3", "via3", "m4")
m4_stack = ("m4", "via4", "m5")

lef_rom_interconnect = ["m1", "m2", "m3", "m4", "m5"]

layer_indices = {"poly": 0,
                 "active": 0,
                 "nwell": 0,
                 "pwell": 0,
                 "m1": 1,
                 "m2": 2,
                 "m3": 3,
                 "m4": 4,
                 "m5": 5}

# The FEOL stacks get us up to m1
feol_stacks = [poly_stack,
               active_stack]

# The BEOL stacks are m1 and up
beol_stacks = [m1_stack,
               m2_stack,
               m3_stack,
               m4_stack]

layer_stacks = feol_stacks + beol_stacks

preferred_directions = {"poly": "V",
                        "active": "V",
                        "m1": "V",
                        "m2": "H",
                        "m3": "V",
                        "m4": "H",
                        "m5": "V"}

###################################################
# Power grid
###################################################
# Use M3/M4
power_grid = m3_stack

###################################################
##GDS Layer Map
###################################################

# create the GDS layer map
layer={}
layer["pwell"]          = (204, 0)
layer["nwell"]          = (21, 0)
layer["dnwell"]         = (12, 0)
layer["active"]         = (22, 0)
layer["pimplant"]       = (31, 0)
layer["nimplant"]       = (32, 0)
layer["poly"]           = (30, 0)
layer["contact"]        = (33, 0)
layer["m1"]             = (34, 0)
layer["via1"]           = (35, 0)
layer["m2"]             = (36, 0)
layer["via2"]           = (38, 0)
layer["m3"]             = (42, 0)
layer["via3"]           = (40, 0)
layer["m4"]             = (46, 0)
layer["via4"]           = (41, 0)
layer["m5"]             = (81, 0)
# Not an official layer
layer["dualgate"]       = (55, 0)   # DUALGATE / MV oxide marker
layer["fet5v"]          = (112, 1)  # 5 V device marker (V5_XTOR)
layer["text"]           = (234, 5)
layer["mem"]            = (108, 5)
layer["boundary"]       = (0, 0)

label_purpose = 10
#use_purpose = {}

# Layer names for external PDKs
# LEF layer names must match the PDK tech LEF verbatim: OpenROAD is
# case-sensitive and a macro pin on an undefined layer gets no physical
# location, which makes the floorplanner fail with "Can not find physical
# location of iterm".  The PDK names are Pwell/Nwell/Poly2/CON/Metal1..5.
layer_names = {}
layer_names["active"]  = "Comp"
layer_names["pwell"]   = "Pwell"
layer_names["nwell"]   = "Nwell"
layer_names["dnwell"]   = "DNwell"
layer_names["nimplant"]= "Nplus"
layer_names["pimplant"]= "Pplus"
layer_names["poly"]    = "Poly2"
layer_names["contact"] = "CON"
layer_names["m1"]      = "Metal1"
layer_names["via1"]    = "Via1"
layer_names["m2"]      = "Metal2"
layer_names["via2"]    = "Via2"
layer_names["m3"]      = "Metal3"
layer_names["via3"]    = "Via3"
layer_names["m4"]      = "Metal4"
layer_names["via4"]    = "Via4"
layer_names["m5"]      = "Metal5"
layer_names["text"]    = "TEXT"
layer_names["mem"]     = "SramCore"
layer_names["boundary"]= "PR_bndry"

###################################################
# DRC/LVS Rules Setup
###################################################

# technology parameter
parameter={}

parameter["min_tx_size"] = 0.250
# The parameterized logic gates (pinv/pnand/ptx) are laid out at the bitcell
# height (4.84 um for cell1rw).  With the gf180 contact-constrained diffusion
# width (minwidth_tx = 0.57 um) a beta of 3 needs a 1.71 um PMOS, which does
# not fit in the ~1.55 um of transistor height available per device and forces
# a second finger - that in turn shrinks the NMOS below the minimum width.
# beta=2 (1.14 um PMOS) fits a single finger and is the ratio used by the
# other 180 nm-class technologies in this repo (scn3me/scn4m).
#
# The DFF hard macro (gf180mcu_fd_sc_mcu7t5v0__dffq_1) is 3.92 um tall and its
# rails sit on the cell boundary, so every gate that the control logic builds
# at dff_height (pinv, pand2, pbuf, pdriver) must fit 3.92 um.  There the
# single-device height available is ~1.07 um, and the number of fingers is
# ceil(beta*size*minwidth_tx / 1.07); the finger width then has to stay at or
# above minwidth_tx, i.e. ceil(0.533*beta*size) <= size.  That holds only for
# beta <= 1.877 at 3.3 V, so the 3.3 V ratio is 1.8.
#
# The 5 V rules shrink that budget further: min_channel grows to a poly
# contact plus two CO.8 spaces and PL.3a (poly space) is 0.24 instead of
# 0.20, so only ~1.015 um per device is left inside the 3.92 um flop.  The
# limit becomes ceil(beta*size*0.57/1.015) <= size, i.e. beta <= 1.78, so the
# 5 V ratio is 1.7.  (1.8 would need three NMOS fingers for a size-2 inverter
# and push each finger to 0.38 um, below minwidth_tx = 0.57.)
parameter["beta"] = 1.7
# The gf180mcu hard DFF is only 3.92 um tall.  Building the control logic
# parameterized gates at that height puts the pmos n-well 0.265 um from the
# nmos diffusion (DF.8 requires 0.43) and trips several device rules.  Use the
# same 4.84 um gate height that the wordline driver/decoder cells are built at.
parameter["control_logic_height"] = 6.0
# The column decoder's parameterized gates have the same problem: a generated
# inverter at the hard flop's height (4.05 um) has its nmos source strap
# 0.005 um from the output pin, and a supply via punched at the buffer's rail
# centre lands 0.18 um from an internal signal strap.  Build them at the same
# 6.0 um height as the control logic.
parameter["column_decoder_gate_height"] = 6.0
# Three-input gates exported to a shared m1/m2 bus channel need enough vertical
# room between their input pins: an exported gate pin is a 0.63 um tall m1
# rectangle (poly contact + m1 enclosure) and the neighbouring signal's 0.26 um
# m1 stub is routed at the next pin's y, so the pitch must clear
# 0.63/2 + 0.13 + 0.23 = 0.675 um, and the m1 landing pads of the m1->m2 vias
# (0.56 um tall) must clear each other by 0.56 + 0.23 = 0.79 um.  0.80 um
# satisfies both.  The gate height is raised to 6.0 um so the three contacts
# still fit below the pmos gate / output route.
parameter["pnand3_input_pitch"] = 0.8
# Height (inside the 3.92 um hard flop) of the dff_array's horizontal m3 clock
# rail.  The flop's D pin (m2) is at y=1.96 and the top level promotes it to a
# 0.38 um m3 pad at y 1.77-2.15.  The default rail height 2*m3_pitch+m3_width
# = 1.60 would leave only 0.03 um to that pad (M3.2a).  Putting the rail at
# 1.25 (1.11-1.39) restores 0.38 um and still clears every m2 pin.
parameter["dff_clk_rail_y"] = 1.25
# The hard flop's VDD and GND pins are both centred at x=1.65 (0.38 um wide),
# so a vdd strap at the pin's left edge and a gnd strap at its right edge
# abut exactly on m4 (short) and leave the supply router no via3 landing on
# the vdd strap.  The flop's m1 supply rails span the full 16.24 um cell
# width, so move the ground strap 0.38 um right of the pin and extend the
# ground pin to it.  This leaves 0.10 um of margin over the 0.28 um m4
# spacing rule.
parameter["dff_gnd_tap_xshift"] = 0.38
# The hard flop's Q pin (m2, 0.20 um tall at y 1.20-1.40 of the dff_array cell)
# leaves only 0.26 um to the neighbouring flop's m1 strap above it, so the
# column-address channel route's via stack at pin.uc() overlapped the flop's
# Q via1 (hierarchical DRC: "can't abut or partially overlap between
# subcells").  Grow the exported dout pin by 0.40 um so the route lands its
# via stack 0.40 um higher, clear of both the flop's m1 strap and its via1.
# Only the column-address dff array uses this (rows=1, so an upward growth
# cannot run into a row above).
parameter["dff_dout_pin_extend"] = 0.40
# The pgate well tap of a parameterized gate is drawn past the declared cell
# width; the neighbouring pdriver's tap does not abut it here, so pand2/pand3
# must give their embedded NAND its own nwell tap (otherwise the nwell is a
# floating net in extraction).
parameter["pand_nand_add_wells"] = True

parameter["6T_inv_nmos_size"] = 0.6
parameter["6T_inv_pmos_size"] = 0.95
parameter["6T_access_size"] = 0.6
drc = d.design_rules("gf180")

# grid size
drc["grid"] = 0.005

# minwidth_tx with contact (no dog bone transistors)
drc["minwidth_tx"] = 0.57
# PL.2_MV thick-oxide gate length.  The 5 V variant draws every generated
# transistor on the thick oxide, so the drawn and simulated gate length is a
# separate, per-type rule that ptx consumes (`gate_length_<type>`).
drc["minlength_channel"] = 0.60

drc["gate_length_nmos"] = 0.60
# PL.2_MV allows 0.50 um for PMOS, but the parameterized gate-array cells
# (pinv/pnand/pdriver) align their n and p taps on one x offset.  A shorter
# PMOS gate would shift the p tap and break that alignment (gf180_sense_amp
# asserts gf180_pinv_nwell.width == pinv.width), so both types use 0.60 um.
# 0.60 is inside the same pfet_06v0 model bin as 0.50.
drc["gate_length_pmos"] = 0.60

drc["pwell_to_nwell"] = 0 # assuming same potential

drc.add_layer("nwell",
              width=0.86,
              spacing=0.6)

drc.add_layer("pwell",
              width=0.74, # 0.6 for 3.3v
              spacing=0.86) # equal potential

# PL.1 minwidth of interconnect poly 3v3
# PL.3a poly spacing 3v3
drc.add_layer("poly",
              width=0.28,
              spacing=0.24)

# PL.5a_MV wants 0.30 um between field poly and marked (5 V) diffusion.  The
# gate stripe extends `poly_extend_active` past the active edge, so the
# extension itself must be at least the MV poly-to-diffusion rule.  Keeping the
# 3.3 V value (0.22) leaves a 0.08 um notch at every gate-to-MV-diffusion edge.
drc["poly_extend_active"] = 0.30

drc["poly_to_contact"] = 0

#drc["active_enclose_gate"] = 0.075

# PL.5a_MV asks for 0.30 um between field poly and marked diffusion.  The
# rule is waived near transistors (`corner_ok allfets`), so the gate contact
# is drawn at 0.30 um and the joining strap is widened to the gate width
# (route_input_gate) to stay a continuation of the transistor poly.
drc["poly_to_active"] = 0.30

# The device well enclosure (DF.7, 0.60 um) is larger than the space a
# standard-height row leaves above/below the diffusion, so drawing it inside
# every ptx makes the well spill into the neighbouring (mirrored) row.  The
# enclosing pgate cell draws the well across its whole area instead.
drc["ptx_draw_well"] = 0

# Thick-oxide marker rules (DUALGATE 55/0, FET5V 112/1).  Every 5 V diffusion
# must be enclosed by the markers (DV.6, DV.8) and marked diffusion must be
# 0.24 um from unmarked COMP (DV.3).
drc["dualgate_enclose_active"] = 0.24   # DV.6, DV.3
drc["dualgate_enclose_poly"] = 0.40     # DV.8
drc["dualgate_to_active"] = 0.40        # marker-to-COMP pad in x

# CO.8: space from a Poly2 contact to COMP (no MV variant of this rule).  Used
# for the gate contact that sits between the NMOS and PMOS stripes.
drc["poly_contact_to_active"] = 0.17

#drc["poly_to_field_poly"] = 0.210

#
# DF.1a - minwidth of active (3v3)
# min space of tap to diff across butted junction
# DF.9 - minarea of active area=0.2025
drc.add_layer("active",
              width=0.30,   # DF.1a_MV thick-oxide COMP width
              spacing=0.36) # DF.3a_MV

drc.add_enclosure("dnwell",
                layer="pwell",
                enclosure=2.5,
                extension=2.5)

drc.add_enclosure("nwell",
                  layer="active",
                  enclosure=0.60,   # DF.7 MV P-diff enclosure
                  extension=0.6)

drc.add_enclosure("pwell",
                  layer="active",
                  enclosure=0.60,
                  extension=0.6)

drc.add_enclosure("implant",
                  layer="active",
                  enclosure=0.125)

# Same as active enclosure?
#drc["implant_to_contact"] = 0.070

drc.add_layer("implant",
              width=0.4,
              spacing=0.4,
              area=0.35)

drc.add_layer("contact",
              width=0.22,
              spacing=0.25)
# CO.4 - active enclosure of contact
# extension is not a true drc rule, used to extend active to reach active min area
drc.add_enclosure("active",
                  layer="contact",
                  enclosure=0.07,
                  extension=0.175)

drc.add_enclosure("poly",
                  layer="contact",
                  enclosure=0.07,
                  extension=0.07)

# PL.5a_MV needs 0.30 um between thick-oxide field poly and marked
# diffusion.  The gate end caps are field poly beside the source/drain
# contact active, so the contact-to-gate spacing has to meet the rule;
# 0.18 um (the 3.3 V value) leaves every ptx gate corner short.
drc["active_contact_to_gate"] = 0.30

drc["poly_contact_to_gate"] = 0.165

#drc["npc_enclose_poly"] = 0.1

# M1.1 - width
# M1.2a - space
# M1.3 - area
drc.add_layer("m1",
              width=0.26,
              spacing=0.23)

drc.add_enclosure("m1",
                  layer="contact",
                  enclosure=0,
                  extension=0.205)

drc.add_enclosure("m1",
                  layer="via1",
                  enclosure=0,
                  extension=0.15)

drc.add_layer("via1",
              width=0.26,
              spacing=0.26)

drc.add_layer("m2",
              width=0.28,
              spacing=0.28,
              area=0.1444)

drc.add_enclosure("m2",
                  layer="via1",
                  enclosure=0.06,
                  extension=0.06)

drc.add_enclosure("m2",
                  layer="via2",
                  enclosure=0.06,
                  extension=0.06)

drc.add_layer("via2",
              width=0.26,
              spacing=0.26)

drc.add_layer("m3",
              width=0.28,
              spacing=0.28,
              area=0.1444)

drc.add_enclosure("m3",
                  layer="via2",
                  enclosure=0.06)


drc.add_enclosure("m3",
                  layer="via3",
                  enclosure=0.06,
                  extension=0.06)

drc.add_layer("via3",
              width=0.26,
              spacing=0.26)

drc.add_layer("m4",
              width=0.28,
              spacing=0.28,
              area=0.1444)

drc.add_enclosure("m4",
                  layer="via3",
                  enclosure=0.06)

drc.add_enclosure("m4",
                  layer="via4",
                  enclosure=0.06)

drc.add_layer("via4",
              width=0.26,
              spacing=0.26)

# gf180mcuD.tech: MT.1 width 0.44, MT.2a spacing 0.46, MT.4 area 0.5265.
# Metal5 is the thick top metal and is much wider than m1-m4; using the
# 0.28 um lower-metal value produced MT.1/MT.2a errors on every supply strap.
drc.add_layer("m5",
              width=0.44,
              spacing=0.46,
              area=0.5265)

drc.add_enclosure("m5",
                  layer="via4",
                  enclosure=0.06)

drc.add_enclosure("m5",
                  layer="via5",
                  enclosure=0.06)

drc.add_layer("via5",
              width=0.26,
              spacing=0.26)

# m5.1 Minimum width of metal5
# m5.2 Minimum spacing of metal5
# m5.7 Minimum area of metal5
#drc.add_layer("m5",
#              width=1.600,
#              spacing=1.600,
#              area=4.000)
# m5.3 Minimum enclosure around via4
#drc.add_enclosure("m5",
#                  layer="via4",
#                  enclosure=0.310)



# Metal 5-10 are ommitted

###################################################
# Spice Simulation Parameters
###################################################

# spice info
spice = {}
spice["nmos"] = "nfet_05v0"
spice["pmos"] = "pfet_05v0"
spice["power"]="vccd1"
spice["ground"]="vssd1"

# sm141064 wrapper subcircuits expect w/l in microns; see ptx.create_netlist.
spice["subckt_model_micron_units"] = True

spice["fet_libraries"] = {"TT": [[os.environ.get("SPICE_MODEL_DIR") + "/sm141064.ngspice", "typical"]]}

# design.ngspice defines the matching/Monte-Carlo switches
# (sw_stat_global, sw_stat_mismatch, mc_skew, ...) that sm141064.ngspice
# references from its device wrapper subcircuits.  Without it ngspice aborts
# with "Undefined parameter [sw_stat_mismatch]".
spice["spice_includes"] = [os.environ.get("SPICE_MODEL_DIR") + "/design.ngspice"]

# spice stimulus related variables
# NOTE: every generated transistor in this technology is an nfet_05v0 /
# pfet_05v0 thick-oxide device (0.60 um drawn gate), and the 1RW bitcell
# (cell1rw) is the extracted 6V-oxide cell, so the supply corners are the
# 5 V class.  The nominal is 5.0 V; 4.5 / 5.5 V bracket it.
spice["feasible_period"] = 15        # estimated feasible period in ns
spice["supply_voltages"] = [4.5, 5.0, 5.5] # Supply voltage corners in [Volts]
spice["nom_supply_voltage"] = 5.0    # Nominal supply voltage in [Volts]
spice["rise_time"] = 0.005           # rise time in [Nano-seconds]
spice["fall_time"] = 0.005           # fall time in [Nano-seconds]
spice["temperatures"] = [0, 25, 100] # Temperature corners (celcius)
spice["nom_temperature"] = 25        # Nominal temperature (celcius)

# analytical delay parameters
spice["nom_threshold"] = 0.49     # Typical Threshold voltage in Volts
spice["wire_unit_r"] = 0.125     # Unit wire resistance in ohms/square
spice["wire_unit_c"] = 0.134     # Unit wire capacitance ff/um^2
spice["min_tx_drain_c"] = 0.7    # Minimum transistor drain capacitance in ff
spice["min_tx_gate_c"] = 0.2     # Minimum transistor gate capacitance in ff
spice["dff_setup"] = 102.5391    # DFF setup time in ps
spice["dff_hold"] = -56          # DFF hold time in ps
spice["dff_in_cap"] = 6.89       # Input capacitance (D) [Femto-farad]
spice["dff_out_cap"] = 6.89      # Output capacitance (Q) [Femto-farad]

# analytical power parameters, many values are temporary
spice["bitcell_leakage"] = 1     # Leakage power of a single bitcell in nW
spice["inv_leakage"] = 1         # Leakage power of inverter in nW
spice["nand2_leakage"] = 1       # Leakage power of 2-input nand in nW
spice["nand3_leakage"] = 1       # Leakage power of 3-input nand in nW
spice["nor2_leakage"] = 1        # Leakage power of 2-input nor in nW
spice["dff_leakage"] = 1         # Leakage power of flop in nW

spice["default_event_frequency"] = 100     # Default event activity of every gate. MHz

# Parameters related to sense amp enable timing and delay chain/RBL sizing
parameter["le_tau"] = 2.25                  # In pico-seconds.
parameter["cap_relative_per_ff"] = 7.5      # Units of Relative Capacitance/ Femto-Farad
parameter["dff_clk_cin"] = 30.6             # relative capacitance
parameter["6tcell_wl_cin"] = 3              # relative capacitance
parameter["min_inv_para_delay"] = 2.4       # Tau delay units
parameter["sa_en_pmos_size"] = 0.72         # micro-meters
parameter["sa_en_nmos_size"] = 0.27         # micro-meters
parameter["sa_inv_pmos_size"] = 0.54        # micro-meters
parameter["sa_inv_nmos_size"] = 0.27        # micro-meters
parameter["bitcell_drain_cap"] = 0.1        # In Femto-Farad, approximation of drain capacitance

###################################################
# Technology Tool Preferences
###################################################

#if use_calibre:
#    drc_name = "calibre"
#    lvs_name = "calibre"
#    pex_name = "calibre"
#if use_klayout:
#    drc_name = "klayout"
#    lvs_name = "klayout"
#    pex_name = "klayout"
#else:
drc_name = "magic"
lvs_name = "netgen"
pex_name = "magic"


flatglob = ["*_?mos_m*"]

ignore_drc_lvs_on = ["wl_strap"]
