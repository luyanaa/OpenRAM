# See LICENSE for licensing information.
#
# ICsprout55 OpenRAM technology definition.

"""OpenRAM technology data for ICsprout 55LLULP (1P6M, 1.2 V core devices).

Geometry rules are taken from the ICsprout 55nm design-rule manual and the
foundry Calibre DRC decks (55LLULP1225 tapeout deck V1.21 and the open
55LLULP1233 REV1_0_OS deck), rounded up to keep generated layout clean under
both.  Implants (NP/PP) are not drawn to final shape by the generators; see
``ics55_finalize.py``, which derives legal implants after GDS generation.

Standard cells: OpenRAM only takes the D flip-flop from a foundry library.
``stdcell_library`` selects it:

  ===========  =============  =======================================
  key          cell           library
  ===========  =============  =======================================
  SC9T         DFFQX1_9TSVT   official 9-track SVT (tapeout library)
  SC9TL/SC9TH  DFFQX1_9T{LVT,HVT}
  H7CR         DFFQX1H7R      open 7-track SVT (icsprout55-pdk)
  H7CL/H7CH    DFFQX1H7{L,H}
  ===========  =============  =======================================

The DFF is wrapped by ``ics55_dff`` into one fixed 9-track-sized slot with its
own well taps, so the macro floorplan does not depend on the library choice.
Timing/current values marked analytical are only used by OpenRAM's analytical
estimators; the foundry analog models are not used by this port.
"""

import os

from openram import OPTS
from openram import drc as d


###################################################
# Custom modules and cell properties
###################################################

tech_modules = d.module_type()
tech_modules["dff"] = "ics55_dff"
tech_modules["sense_amp"] = "ics55_sense_amp"
tech_modules["write_driver"] = "ics55_write_driver"

cell_properties = d.cell_properties()
cell_properties.ptx.model_is_subckt = False
cell_properties.ptx.bin_spice_models = False

layer_properties = d.layer_properties()


###################################################
# Standard-cell library selection
###################################################

_stdcell_variants = {
    #          dff cell          vt     nmos model        pmos model     track height
    "SC9T":  ("DFFQX1_9TSVT", "svt", "nm1p2_svt_lp", "pm1p2_svt_lp", 1.8),
    "SC9TL": ("DFFQX1_9TLVT", "lvt", "nm1p2_lvt_lp", "pm1p2_lvt_lp", 1.8),
    "SC9TH": ("DFFQX1_9THVT", "hvt", "nm1p2_hvt_lp", "pm1p2_hvt_lp", 1.8),
    "H7CR":  ("DFFQX1H7R", "svt", "nm1p2_svt_lp", "pm1p2_svt_lp", 1.4),
    "H7CL":  ("DFFQX1H7L", "lvt", "nm1p2_lvt_lp", "pm1p2_lvt_lp", 1.4),
    "H7CH":  ("DFFQX1H7H", "hvt", "nm1p2_hvt_lp", "pm1p2_hvt_lp", 1.4),
}
_aliases = {"9T": "SC9T", "SC9TSVT": "SC9T", "7T": "H7CR"}

stdcell_library = str(getattr(OPTS, "stdcell_library", "SC9T")).upper()
stdcell_library = _aliases.get(stdcell_library, stdcell_library)
if stdcell_library not in _stdcell_variants:
    raise ValueError("Unsupported ICsprout55 stdcell_library '{}'; choose one of {}".format(
        stdcell_library, ", ".join(_stdcell_variants)))
stdcell_variant = dict(zip(("dff", "vt", "nmos", "pmos", "height"),
                           _stdcell_variants[stdcell_library]))

# The foundry DFF as a hard cell.  Its pins keep the foundry names
# (D/Q/CK/VDD/VSS); ics55_dff presents OpenRAM's D/Q/clk/vdd/gnd.
# The cells are tapless, so their wells are separate terminals (VNW/VPW).
cell_properties.names["dff"] = stdcell_variant["dff"]
cell_properties._dff = d.cell(["D", "Q", "clk", "vdd", "gnd", "vnw", "vpw"],
                              ["INPUT", "OUTPUT", "INPUT", "POWER", "GROUND",
                               "POWER", "GROUND"],
                              port_map={"D": "D", "Q": "Q", "clk": "CK",
                                        "vdd": "VDD", "gnd": "VSS",
                                        "vnw": "VNW", "vpw": "VPW"})


def lib_cell_names():
    """Names of foundry hard cells whose layout must not be altered."""
    return [v[0] for v in _stdcell_variants.values()]


###################################################
# GDS information and interconnect stacks
###################################################

GDS = {
    # ICsprout55 GDS uses a 1 nm database unit.
    "unit": (0.001, 1e-9),
    "zoom": 0.05,
}

poly_stack = ("poly", "contact", "m1")
active_stack = ("active", "contact", "m1")
m1_stack = ("m1", "via1", "m2")
m2_stack = ("m2", "via2", "m3")
m3_stack = ("m3", "via3", "m4")
m4_stack = ("m4", "via4", "m5")

lef_rom_interconnect = ["m1", "m2", "m3", "m4"]

layer_indices = {
    "poly": 0,
    "active": 0,
    "nwell": 0,
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
    "active": "V",
    "m1": "H",
    "m2": "V",
    "m3": "H",
    "m4": "V",
    "m5": "H",
}

# Macro-level supply grid on M3/M4; M5 and above are left for the SoC.
power_grid = m3_stack


###################################################
# GDS layer map (Calibre deck LAYER MAP)
###################################################

layer = {
    "active": (2, 1),           # ACTi
    "nwell": (9, 1),            # NW1i
    "nimplant": (52, 1),        # NPi   (regenerated by ics55_finalize)
    "pimplant": (53, 1),        # PPi   (regenerated by ics55_finalize)
    "poly": (41, 1),            # POLYi
    "contact": (72, 1),         # CTi
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
    "text": (81, 6),
    "boundary": (351, 12),      # cell boundary, not a mask layer
}

# Labels go on the *text* purpose (MET1text etc. = datatype 6); pin shapes
# stay on the drawing purpose.  Never use purpose 2: it is metal dummy.
label_purpose = 6

# Names as in the ICsprout55 technology LEF (N551P6M.lef) and the foundry
# memory-compiler LEFs, so the macro LEF reads into Innovus/OpenROAD as is.
layer_names = {
    "active": "ACT", "nwell": "NW1", "nimplant": "NP", "pimplant": "PP",
    "poly": "POLY", "contact": "CT", "active_contact": "CT",
    "poly_contact": "CT", "m1": "MET1", "via1": "VIA1", "m2": "MET2",
    "via2": "VIA2", "m3": "MET3", "via3": "VIA3", "m4": "MET4",
    "via4": "VIA4", "m5": "MET5",
    "text": "MET1", "boundary": "OVERLAP",
}


###################################################
# DRC rules
###################################################

drc = d.design_rules("icsprout55")
# Database is 1 nm but keep generated geometry on a 5 nm grid.
drc["grid"] = 0.005

_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
drc["drc_rules"] = ""
drc["lvs_rules"] = ""
drc["xrc_rules"] = ""
drc["layer_map"] = os.path.join(_here, "layers.map")

# --- Transistors (1.2 V core devices)
# ACT_W_3a allows 0.12, but below 0.15 the contacted active becomes a dog-bone
# that must clear the gate by 0.1 (PO_S_14/15).  Use 0.15 as the minimum.
drc["minwidth_tx"] = 0.15
drc["minlength_channel"] = 0.06      # PO_W_2a

# --- N-well
drc["pwell_to_nwell"] = 0
drc.add_layer("nwell", width=0.47, spacing=0.47)            # NW1_W_1/NW1_S_1
drc.add_enclosure("nwell", layer="active", enclosure=0.16,  # ACT_EN_1/2, ACT_S_5/8
                  extension=0.16)

# --- Poly
# PO_A_1 (0.04 um2) is met by the poly contact pad / wordline every gate
# connects to, not by the bare gate, so it is left to Calibre.
drc.add_layer("poly", width=0.06, spacing=0.18, area=0.0)   # PO_S_2 (wide)
drc["poly_extend_active"] = 0.15     # PO_EX_2 0.14
drc["poly_to_contact"] = 0.07        # CT_S_4
drc["active_enclose_gate"] = 0.12    # PO_EX_1 0.11
drc["poly_to_active"] = 0.06         # PO_S_11 0.05
drc["poly_to_field_poly"] = 0.18

# --- Active
drc.add_layer("active", width=0.12, spacing=0.13, area=0.04)  # ACT_S_1/2, ACT_A_1

# --- Implants.  Used by the generators only for spacing decisions and as type
# hints; final NP/PP shapes come from ics55_finalize.
drc.add_layer("implant", width=0.18, spacing=0.0)
drc.add_enclosure("implant", layer="active", enclosure=0.12)  # NP/PP_EN_2, _O_1
drc.add_enclosure("implant", layer="contact", enclosure=0.06)
drc["implant_to_channel"] = 0.30     # NP_S_5/PP_S_5
drc["implant_to_contact"] = 0.06     # CT_S_6
# Well-tap active to transistor active: the finalizer gives the tap an
# opposite-type implant island (tap + 0.08), and the transistor active needs
# 0.16 of its own implant beyond the gate exit (NP/PP_EX_1, SNP_68 macro).
drc["tap_to_active"] = 0.26
# ... and field poly needs 0.15 of its own implant (NP_EN_3/PP_EN_3).
drc["tap_to_poly"] = 0.25

# --- Contact
drc.add_layer("contact", width=0.09, spacing=0.14)          # CT_W_1, CT_S_1/1a
drc.add_enclosure("active", layer="contact", enclosure=0.03, extension=0.03)  # CT_EN_2
drc.add_enclosure("poly", layer="contact", enclosure=0.04, extension=0.04)    # CT_EN_3
drc["tap_extend_contact"] = 0.03
drc["active_contact_to_gate"] = 0.07   # CT_S_4 0.055
drc["poly_contact_to_gate"] = 0.08     # CT_S_3 0.065
drc["contact_to_gate"] = 0.07
drc["contact_to_poly"] = 0.07
drc["npc_enclose_poly"] = 0

# --- Metals and vias
drc.add_layer("m1", width=0.09, spacing=0.12, area=0.045)   # MET1_W_1, _S_1/_S_5, _A_1
# CT_EN_4; the 0.09 extension makes a single-contact pad meet MET1_A_1.
drc.add_enclosure("m1", layer="contact", enclosure=0.04, extension=0.09)
drc.add_enclosure("m1", layer="via1", enclosure=0.04, extension=0.04)     # VIAn_EN_2

for n in range(1, 5):
    via = "via{}".format(n)
    lower = "m{}".format(n)
    upper = "m{}".format(n + 1)
    drc.add_layer(via, width=0.09, spacing=0.14)            # VIAn_W_1, VIAn_S_1/S_2
    drc.add_enclosure(upper, layer=via, enclosure=0.04, extension=0.04)
    if n > 1:
        drc.add_enclosure(lower, layer=via, enclosure=0.04, extension=0.04)

for n in range(2, 6):
    drc.add_layer("m{}".format(n), width=0.10, spacing=0.12, area=0.055)  # METn_W/S/A


###################################################
# SPICE names and analytical estimates
###################################################

spice = {}
spice["stdcell_library"] = stdcell_library
spice["nmos"] = stdcell_variant["nmos"]
spice["pmos"] = stdcell_variant["pmos"]
spice["fet_models"] = {}
spice["analytical_only"] = True
spice["power"] = "vdd"
spice["ground"] = "gnd"

# Corner metadata used by OpenRAM's analytical delay path.
spice["feasible_period"] = 5
spice["supply_voltages"] = [1.08, 1.20, 1.32]
spice["nom_supply_voltage"] = 1.20
spice["rise_time"] = 0.005
spice["fall_time"] = 0.005
spice["temperatures"] = [0, 25, 85]
spice["nom_temperature"] = 25

# Analytical defaults only (not fitted to the foundry models).
spice["nom_threshold"] = 0.40
spice["wire_unit_r"] = 0.1122       # M1 sheet resistance, ohm/square (LEF)
spice["wire_unit_c"] = 7.63e-16     # M1 area capacitance, F/um^2 (LEF)
spice["min_tx_drain_c"] = 0.7       # fF
spice["min_tx_gate_c"] = 0.2        # fF
spice["dff_setup"] = 60             # ps, foundry DFF Liberty order of magnitude
spice["dff_hold"] = 20              # ps
spice["dff_in_cap"] = 0.9           # fF
spice["dff_out_cap"] = 2.0          # fF
spice["i_on_n"] = 4.0e-4            # A/um
spice["i_on_p"] = 1.5e-4            # A/um
spice["tox"] = 0.0015               # um
spice["eps_ox"] = 0.00245e-14       # F/um
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

parameter = {
    "min_tx_size": 0.15,
    "beta": 2,
    # Logic-rule 6T bitcell (no foundry SRAM device rules are used).
    "6T_inv_nmos_size": 0.36,
    "6T_inv_pmos_size": 0.15,
    "6T_access_size": 0.24,
}
spice["sa_transconductance"] = (spice["mobility_n"] * spice["cox"]
                                * (parameter["6T_access_size"] / parameter["min_tx_size"])
                                * spice["V_dsat"])

spice["bitcell_leakage"] = 1
spice["inv_leakage"] = 1
spice["nand2_leakage"] = 1
spice["nand3_leakage"] = 1
spice["nand4_leakage"] = 1
spice["nor2_leakage"] = 1
spice["dff_leakage"] = 1
spice["default_event_frequency"] = 100

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
# GDS finalization (called by sram.save after writing the GDS)
###################################################

def finalize_gds(gds_file, top_name):
    """Derive legal NP/PP implants, fill N-well notches and fix metal
    area/holes in the final macro GDS (see ics55_finalize.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ics55_finalize", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "ics55_finalize.py"))
    fin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fin)
    from openram import debug
    info = fin.finalize_gds(gds_file, gds_file, top_name, lib_cell_names(),
                            report=lambda s: debug.warning(s))
    debug.info(1, "ics55_finalize: {}".format(info))
    return info


###################################################
# Tool preferences
###################################################

# Physical verification is run with the foundry Calibre decks outside
# OpenRAM (see technology/icsprout55/README.md).
drc_name = "none"
lvs_name = "none"
pex_name = "none"

blackbox_bitcell = False
