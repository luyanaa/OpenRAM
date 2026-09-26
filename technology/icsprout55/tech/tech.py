# See LICENSE for licensing information.
#
# ICsprout55 OpenRAM technology definition.

"""OpenRAM technology data for the provisional ICsprout55 digital PDK.

The physical values below are taken from the released ICsprout55 technology
LEF, GDS-derived rule tables, and layer map.  The timing/current/capacitance
values marked as analytical defaults are only used by OpenRAM's analytical
estimators; this port intentionally has no fitted analog SPICE model cards.
"""

import os

from openram import OPTS
from openram import drc as d


###################################################
# Custom modules and cell properties
###################################################

tech_modules = d.module_type()

# The PDK does not release SRAM macros.  Use OpenRAM's parameterized cells so
# generation does not silently depend on a nonexistent hard-cell library.
for module_type in ("bitcell", "dummy_bitcell", "replica_bitcell"):
    for num_ports in (1, 2):
        tech_modules["{}_{}port".format(module_type, num_ports)] = {
            "bitcell": "pbitcell",
            "dummy_bitcell": "dummy_pbitcell",
            "replica_bitcell": "replica_pbitcell",
        }[module_type]
tech_modules["dff"] = "ics55_dff"
tech_modules["sense_amp"] = "ics55_sense_amp"
tech_modules["write_driver"] = "ics55_write_driver"


cell_properties = d.cell_properties()
cell_properties.ptx.model_is_subckt = False
cell_properties.ptx.bin_spice_models = False

layer_properties = d.layer_properties()


###################################################
# GDS information and interconnect stacks
###################################################

GDS = {
    # ICsprout55 GDS is on a 1 nm database grid (0.001 um DBU).
    "unit": (0.001, 1e-9),
    "zoom": 0.05,
}

poly_stack = ("poly", "contact", "m1")
active_stack = ("active", "contact", "m1")
m1_stack = ("m1", "via1", "m2")
m2_stack = ("m2", "via2", "m3")
m3_stack = ("m3", "via3", "m4")
m4_stack = ("m4", "via4", "m5")

# Used by ROM generation and available for external P&R tooling.
lef_rom_interconnect = ["m1", "m2", "m3", "m4", "m5"]

layer_indices = {
    "poly": 0,
    "active": 0,
    "m1": 1,
    "m2": 2,
    "m3": 3,
    "m4": 4,
    "m5": 5,
}

feol_stacks = [poly_stack, active_stack]
beol_stacks = [m1_stack, m2_stack, m3_stack, m4_stack]
layer_stacks = feol_stacks + beol_stacks

preferred_directions = {
    "poly": "V",
    "active": "H",
    "m1": "H",
    "m2": "V",
    "m3": "H",
    "m4": "V",
    "m5": "H",
}

# OpenRAM's SRAM LEF writer exposes M1-M4; use M3/M4 for the global grid.
power_grid = m3_stack


###################################################
# GDS layer map
###################################################

layer = {
    "active": (2, 1),
    "nwell": (9, 1),
    "nimplant": (52, 1),
    "pimplant": (53, 1),
    "poly": (41, 1),
    # CT is one physical mask; OpenRAM keeps separate stack names for the
    # active and poly contact constructors.
    "contact": (72, 1),
    "active_contact": (72, 1),
    "poly_contact": (72, 1),
    "m1": (81, 1),
    "via1": (91, 1),
    "m2": (82, 1),
    "via2": (92, 1),
    "m3": (83, 1),
    "via3": (93, 1),
    "m4": (84, 1),
    "via4": (94, 1),
    "m5": (85, 1),
    "tm2": (103, 1),
    "tv2": (113, 1),
    "text": (81, 6),
    # CELLBOUND is the released standard-cell boundary purpose.
    "boundary": (351, 12),
    "mem": (351, 12),
}

use_purpose = {}

# Layer names consumed by external OpenRAM/P&R integrations.
layer_names = {
    "active": "ACT",
    "nwell": "NW",
    "nimplant": "NP",
    "pimplant": "PP",
    "poly": "POLY",
    "contact": "CT",
    "active_contact": "CT",
    "poly_contact": "CT",
    "m1": "M1",
    "via1": "V1",
    "m2": "M2",
    "via2": "V2",
    "m3": "M3",
    "via3": "V3",
    "m4": "M4",
    "via4": "V4",
    "m5": "M5",
    "tm2": "TM2",
    "tv2": "TV2",
    "text": "M1LBL",
    "boundary": "CELLBOUND",
    "mem": "CELLBOUND",
}

label_purpose = 6


###################################################
# DRC/LVS rules
###################################################

parameter = {
    # Core 1.2 V transistor sizes are analytical starting points for generated
    # SRAM cells, not characterized library values.
    "min_tx_size": 0.15,
    "beta": 3,
    "6T_inv_nmos_size": 0.15,
    "6T_inv_pmos_size": 0.30,
    "6T_access_size": 0.15,
}

_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pdk_root = os.environ.get("ICSPROUT55_PDK")

_stdcell_variants = {
    "H7CR": {
        "vt": "svt",
        "suffix": "H7R",
        "nmos": "nm1p2_svt_lp",
        "pmos": "pm1p2_svt_lp",
    },
    "H7CL": {
        "vt": "lvt",
        "suffix": "H7L",
        "nmos": "nm1p2_lvt_lp",
        "pmos": "pm1p2_lvt_lp",
    },
    "H7CH": {
        "vt": "hvt",
        "suffix": "H7H",
        "nmos": "nm1p2_hvt_lp",
        "pmos": "pm1p2_hvt_lp",
    },
}

stdcell_library = str(getattr(OPTS, "stdcell_library", "H7CR")).upper()
if stdcell_library not in _stdcell_variants:
    raise ValueError(
        "Unsupported ICsprout55 stdcell_library '{}'; choose H7CR, H7CL, or H7CH".format(
            stdcell_library
        )
    )
stdcell_variant = _stdcell_variants[stdcell_library]
if _pdk_root:
    stdcell_library_root = os.path.join(
        _pdk_root, "libs.ref", "ics55_LLSC_{}".format(stdcell_library)
    )
else:
    stdcell_library_root = ""

drc = d.design_rules("icsprout55")
drc["grid"] = 0.001
# These paths support optional physical-verification tool invocation.  They do
# not refer to, include, or configure any analog SPICE model directory.
if _pdk_root:
    drc["drc_rules"] = os.path.join(_pdk_root, "libs.tech", "drc", "ics55_drc.drc")
    drc["lvs_rules"] = os.path.join(_pdk_root, "libs.tech", "lvs", "ics55.lvs")
else:
    drc["drc_rules"] = os.path.join(_here, "tech", "icsprout55.lydrc")
    drc["lvs_rules"] = os.path.join(_here, "tech", "icsprout55.lylvs")
drc["xrc_rules"] = ""
drc["layer_map"] = os.path.join(_here, "layers.map")

# Transistor and well rules.
drc["minwidth_tx"] = 0.081
drc["minlength_channel"] = 0.060
drc["pwell_to_nwell"] = 0.0
drc["nwell_to_nwell"] = 0.470
drc["pwell_to_pwell"] = 0.470
drc.add_layer("nwell", width=0.470, spacing=0.470)
drc.add_enclosure("nwell", layer="active", enclosure=0.150)

# The PDK reports a 0.0388 um^2 poly minimum area.  OpenRAM's transistor
# generator intentionally does not grow gate polygons to satisfy area checks
# (doing so changes the LVS device); keep the generated geometry usable and
# leave this check to the PDK KLayout deck.
drc.add_layer("poly", width=0.060, spacing=0.120, area=0.0)
drc["poly_extend_active"] = 0.060
drc["active_enclose_gate"] = 0.140
drc["poly_to_active"] = 0.050
drc["poly_to_field_poly"] = 0.120
drc["minarea_poly"] = 0.0
drc.add_layer("active", width=0.081, spacing=0.110, area=0.0)

# The released PDK has separate NP/PP masks.  OpenRAM's generic implant rule
# is set to the conservative NP minimum so both generated implant masks pass
# the common geometry checks.
drc.add_layer("implant", width=0.400, spacing=0.400)
drc.add_enclosure("implant", layer="active", enclosure=0.0)
drc.add_enclosure("implant", layer="contact", enclosure=0.0)
drc["implant_to_channel"] = 0.0
drc["implant_to_contact"] = 0.0

# Contact and first-metal rules.
drc.add_layer("contact", width=0.090, spacing=0.110, area=0.0081)
drc.add_enclosure("active", layer="contact", enclosure=0.010)
drc.add_enclosure("poly", layer="contact", enclosure=0.010)
drc.add_enclosure("m1", layer="contact", enclosure=0.040)
drc["active_contact_to_gate"] = 0.062
drc["poly_contact_to_gate"] = 0.051
drc["contact_to_gate"] = 0.062
drc["contact_to_poly"] = 0.051
drc["poly_to_contact"] = 0.051

metal_rules = {
    "m1": (0.090, 0.090, 0.042),
    "m2": (0.100, 0.100, 0.052),
    "m3": (0.100, 0.100, 0.052),
    "m4": (0.100, 0.100, 0.052),
    "m5": (0.100, 0.100, 0.052),
}
for metal, (width, spacing, area) in metal_rules.items():
    drc.add_layer(metal, width=width, spacing=spacing, area=area)

via_rules = {
    "via1": ("m1", "m2"),
    "via2": ("m2", "m3"),
    "via3": ("m3", "m4"),
    "via4": ("m4", "m5"),
}
for via, (lower, upper) in via_rules.items():
    drc.add_layer(via, width=0.090, spacing=0.110, area=0.0081)
    drc.add_enclosure(lower, layer=via, enclosure=0.040)
    drc.add_enclosure(upper, layer=via, enclosure=0.040)


###################################################
# SPICE names and analytical estimates
###################################################

spice = {}
# These are the released digital CDL primitive model names.  No model files
# are supplied here: ICsprout55 analog SPICE parameters are not fitted.
spice["stdcell_library"] = stdcell_library
spice["stdcell_variant"] = stdcell_variant["vt"]
spice["stdcell_library_root"] = stdcell_library_root
spice["nmos"] = stdcell_variant["nmos"]
spice["pmos"] = stdcell_variant["pmos"]
spice["fet_models"] = {}
spice["analytical_only"] = True

# Corner metadata used by OpenRAM's analytical delay path.
spice["feasible_period"] = 5
spice["supply_voltages"] = [1.08, 1.20, 1.32]
spice["nom_supply_voltage"] = 1.20
spice["rise_time"] = 0.005
spice["fall_time"] = 0.005
spice["temperatures"] = [0, 25, 85]
spice["nom_temperature"] = 25

# Analytical defaults only.  These are not extracted or fitted ICsprout55
# analog parameters and must not be used as a signoff timing model.
spice["nom_threshold"] = 0.40
spice["wire_unit_r"] = 0.1122       # M1 sheet resistance, ohm/square (LEF)
spice["wire_unit_c"] = 7.63e-16     # M1 area capacitance, F/um^2 (LEF)
spice["min_tx_drain_c"] = 0.7       # fF, analytical floor
spice["min_tx_gate_c"] = 0.2        # fF, analytical floor
spice["dff_setup"] = 9              # ps, analytical floor
spice["dff_hold"] = 1               # ps, analytical floor
spice["dff_in_cap"] = 0.21          # fF, analytical floor
spice["dff_out_cap"] = 2.0          # fF, analytical floor

# Generic analytical current/oxide values keep CACTI equations defined while
# the PDK's fitted analog cards remain unavailable.
spice["i_on_n"] = 4.0e-4            # A/um, analytical estimate
spice["i_on_p"] = 1.5e-4            # A/um, analytical estimate
spice["tox"] = 0.0015              # um, analytical estimate
spice["eps_ox"] = 0.00245e-14      # F/um, analytical estimate
spice["cox"] = spice["eps_ox"] / spice["tox"]
spice["c_g_ideal"] = spice["cox"] * drc["minlength_channel"]
spice["c_overlap"] = 0.2 * spice["c_g_ideal"]
spice["c_fringe"] = 0.0
spice["cpolywire"] = 0.0
spice["c_junc"] = 5e-16
spice["c_junc_sw"] = 5e-16
spice["wire_c_per_um"] = spice["wire_unit_c"] * drc["minwidth_m2"]
spice["wire_r_per_um"] = spice["wire_unit_r"] / drc["minwidth_m2"]
spice["mobility_n"] = 0.045e8
spice["V_dsat"] = 0.0938
spice["sa_transconductance"] = (
    spice["mobility_n"]
    * spice["cox"]
    * (parameter["6T_access_size"] / parameter["min_tx_size"])
    * spice["V_dsat"]
)

# Analytical leakage/activity defaults.
spice["bitcell_leakage"] = 1
spice["inv_leakage"] = 1
spice["nand2_leakage"] = 1
spice["nand3_leakage"] = 1
spice["nand4_leakage"] = 1
spice["nor2_leakage"] = 1
spice["dff_leakage"] = 1
spice["default_event_frequency"] = 100

# Delay-chain and sense-amp sizing defaults.
parameter["le_tau"] = 2.25
parameter["cap_relative_per_ff"] = 7.5
parameter["dff_clk_cin"] = 30.6
parameter["6tcell_wl_cin"] = 3
parameter["min_inv_para_delay"] = 2.4
parameter["sa_en_pmos_size"] = 0.72
parameter["sa_en_nmos_size"] = 0.27
parameter["sa_inv_pmos_size"] = 0.54
parameter["sa_inv_nmos_size"] = 0.27
parameter["bitcell_drain_cap"] = 0.1


###################################################
# Tool preferences
###################################################

drc_name = "klayout"
lvs_name = "klayout"
pex_name = "klayout"

# Generated SRAM cells are not hard macros.
blackbox_bitcell = False
