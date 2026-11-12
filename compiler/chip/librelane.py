# See LICENSE for licensing information.
#
# LibreLane configuration generation for the chip assembly layer.
#
# The config uses LibreLane 3's built-in ``Chip`` flow: OpenROAD.PadRing is
# substituted for Odb.SetPowerConnections, so the pad instances emitted by
# verilog.py are placed on the die edges using the PDK's pad site/filler
# definitions.

import os

import yaml

from .models import ChipConfig, MacroSpec, SlotSpec
from .verilog import pad_plan


def placement_density_pct(utilization):
    """Placer density target that avoids OpenROAD GPL-0305 on macro-heavy dies."""
    if utilization > 0.80:
        return 2
    if utilization > 0.70:
        return 5
    if utilization > 0.50:
        return 10
    if utilization > 0.30:
        return 15
    return 20


def _clean_glob(macro):
    """Cells to flatten in Magic for this macro family."""
    if macro.source == "openram":
        # Flatten the generated macro hierarchy so Magic sees the same
        # geometry that already passed macro-level DRC/LVS.
        return ["{}_*".format(macro.name)]
    return []


def _usable_lib(path):
    """A Liberty file only counts if it exists and has real content.

    Characterization writes/truncates the .lib early on, so an interrupted or
    failed run leaves a 0-byte file that would make every Library step fail.
    """
    try:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


# (default corner, [(corner key, SCL pvt, IO pvt), ...])
_CORNER_SETS = {
    "5v0": (
        "nom_tt_025C_5v00",
        [
            ("*_tt_025C_5v00", "tt_025C_5v00", "tt_025C_5v00"),
            ("*_ff_n40C_5v50", "ff_n40C_5v50", "ff_n40C_5v50"),
            ("*_ss_125C_4v50", "ss_125C_4v50", "ss_125C_4v50"),
        ],
    ),
    "3v3": (
        "nom_tt_025C_3v30",
        [
            ("*_tt_025C_3v30", "tt_025C_3v30", "tt_025C_3v30"),
            ("*_ff_n40C_3v60", "ff_n40C_3v60", "ff_n40C_3v63"),
            ("*_ss_125C_3v00", "ss_125C_3v00", "ss_125C_2v97"),
        ],
    ),
}


def _pdk_corner_config(cfg):
    """Explicit LIB/PAD_LIBS/corners from the gf180mcuD reference libraries.

    The CIEL gf180mcuD ``libs.tech/librelane/config.tcl`` predates LibreLane 3:
    it publishes ``CELL_LIBS``, while LibreLane 3 reads ``LIB``.  That leaves
    ``LIB`` empty, ``DEFAULT_CORNER`` collapsed to ``nom_`` and no Liberty at
    all, which breaks synthesis of the pad ring.  Emitting the maps ourselves
    keeps the chip flow working with the PDK as installed.
    """
    pdk_root = os.environ.get("PDK_ROOT")
    if not pdk_root:
        return {}
    ref = os.path.join(pdk_root, "gf180mcuD", "libs.ref")
    if not os.path.isdir(ref):
        return {}

    family = "5v0" if float(cfg.core_voltage) >= 4.5 else "3v3"
    default_corner, entries = _CORNER_SETS[family]
    scl_dir = os.path.join(ref, "gf180mcu_fd_sc_mcu7t5v0", "lib")
    io_dir = os.path.join(ref, "gf180mcu_fd_io", "lib")

    lib = {}
    pad_libs = {}
    for key, scl_pvt, io_pvt in entries:
        scl = os.path.join(
            scl_dir, "gf180mcu_fd_sc_mcu7t5v0__{}.lib".format(scl_pvt))
        io = os.path.join(io_dir, "gf180mcu_fd_io__{}.lib".format(io_pvt))
        if os.path.isfile(scl):
            lib[key] = [scl]
        if os.path.isfile(io):
            pad_libs[key] = [io]
    if not lib:
        return {}

    out = {
        "LIB": lib,
        "DEFAULT_CORNER": default_corner,
        "SYNTH_CORNER": default_corner,
        "STA_CORNERS": [default_corner],
        "TIMING_VIOLATION_CORNERS": ["*tt*"],
    }
    if pad_libs:
        out["PAD_LIBS"] = pad_libs
    return out


def build_config(cfg, slot, macro, fit, out_dir, signoff=True):
    src = os.path.join(out_dir, "src")
    pad = pad_plan(cfg, slot, fit)
    vdd = "VDD"
    vss = "VSS"

    period_ns = 1000.0 / cfg.clock_frequency_mhz

    macros = {}
    macro_entry = {
        "gds": [macro.gds],
        "lef": [macro.lef],
        "instances": {},
    }
    if macro.verilog:
        # A synthesizable blackbox view is emitted next to the netlist.
        macro_entry["vh"] = [os.path.join(src, "{}_blackbox.v".format(macro.name))]
    libs = {}
    if _usable_lib(macro.lib):
        libs["*"] = [macro.lib]
    for corner, path in macro.liberty_corners.items():
        if not _usable_lib(path):
            continue
        if "5v" in corner or "4v" in corner or "3v" in corner:
            libs["*{}*".format(corner.replace("_", ""))] = [path]
    if libs:
        macro_entry["lib"] = libs
    for placement in fit.placements:
        macro_entry["instances"]["u_core.u_sram_array.u_sram_{}".format(
            placement.index)] = {
            "location": [placement.x, placement.y],
            "orientation": placement.orientation,
        }
    macros[macro.class_name] = macro_entry

    pdn_macro = []
    for placement in fit.placements:
        pdn_macro.append(
            "u_core.u_sram_array.u_sram_{} {} {} {} {}".format(
                placement.index, vdd, vss, macro.ports["vdd"],
                macro.ports["vss"]))

    config = {
        "meta": {
            "version": 3,
            "flow": "Chip",
        },
        "DESIGN_NAME": "{}_top".format(cfg.name),
        "VERILOG_FILES": [
            os.path.join(src, "slot_defines.svh"),
            os.path.join(src, "{}.sv".format(cfg.name)),
            os.path.join(src, "{}_core.sv".format(cfg.name)),
            os.path.join(src, "{}_top.sv".format(cfg.name)),
        ],
        "VERILOG_DEFINES": ["USE_POWER_PINS"],
        "CLOCK_PORT": "clk_PAD",
        "CLOCK_NET": "clk_i",
        "CLOCK_PERIOD": round(period_ns, 4),
        "PNR_SDC_FILE": os.path.join(src, "{}.sdc".format(cfg.name)),
        "SIGNOFF_SDC_FILE": os.path.join(src, "{}.sdc".format(cfg.name)),
        "FALLBACK_SDC": os.path.join(src, "{}.sdc".format(cfg.name)),
        "VDD_NETS": [vdd],
        "GND_NETS": [vss],
        "PL_TARGET_DENSITY_PCT": (cfg.density_target_pct
                                  if cfg.density_target_pct is not None
                                  else placement_density_pct(fit.utilization)),
        "PL_RESIZER_HOLD_SLACK_MARGIN": (cfg.hold_slack_margin_ns
                                         if cfg.hold_slack_margin_ns is not None
                                         else 0.35),
        "GRT_RESIZER_HOLD_SLACK_MARGIN": (round(max(cfg.hold_slack_margin_ns - 0.05, 0.0), 3)
                                          if cfg.hold_slack_margin_ns is not None
                                          else 0.3),
        "GRT_ALLOW_CONGESTION": True,
        "DRT_ANTENNA_REPAIR_ITERS": 10,
        "DRT_ANTENNA_MARGIN": 10,
        "PDN_CORE_RING": True,
        "PDN_CORE_RING_CONNECT_TO_PADS": True,
        # Vertical Metal5 straps so the macro grid can reach the macro's
        # boundary power rails; see pdn_cfg.tcl.  The halos are zero because
        # the straps must touch the macro boundary pins.
        "PDN_CFG": os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "pdn_cfg.tcl"),
        "FP_SIZING": "absolute",
        "DIE_AREA": [0.0, 0.0, slot.die_width, slot.die_height],
        "CORE_AREA": [slot.inset.left, slot.inset.bottom,
                      slot.die_width - slot.inset.right,
                      slot.die_height - slot.inset.top],
        "PDN_CONNECT_MACROS_TO_GRID": True,
        "PDN_MACRO_CONNECTIONS": pdn_macro,
        "FP_MACRO_HORIZONTAL_HALO": cfg.halo_um,
        "FP_MACRO_VERTICAL_HALO": cfg.halo_um,
        "PDN_HORIZONTAL_HALO": 0.0,
        "PDN_VERTICAL_HALO": 0.0,
        "MACROS": macros,
        "EXTRA_GDS": [macro.gds],
        "EXTRA_LEFS": [macro.lef],
        "IGNORE_DISCONNECTED_MODULES": [
            "gf180mcu_fd_io__bi_24t",
            "gf180mcu_fd_io__dvdd",
            "gf180mcu_fd_io__dvss",
            "gf180mcu_fd_io__cor",
        ],
        "PAD_SOUTH": pad["south"],
        "PAD_NORTH": pad["north"],
        "PAD_EAST": pad["east"],
        "PAD_WEST": pad["west"],
        "MAGIC_EXT_UNIQUE": "notopports",
        # The macro is a hard block: Magic extracts only the macro's abstract
        # port view, so Netgen has to compare that view against the abstract
        # view of the netlist side.  Flattening the macro class (the previous
        # setting) makes Netgen descend into the black box's empty body and
        # report 92 net mismatches plus 4 unmatched instances on a design that
        # is otherwise clean; without it the remaining mismatches are exactly
        # the macro power nets, which post_pdn.tcl connects.
    }
    # Cut the top-metal PDN straps back to the edge of the macro array.  The
    # chip PDN runs its top-layer straps across the whole core, i.e. straight
    # over the macros, and a macro LEF whose own stack stops at Metal4 leaves
    # its Metal5 "free" for the router, which then crosses the straps and
    # shorts to them (38 Metal5 shorts in the 2x4 run1).  Blocking Metal5 over
    # the array for the router is not an option - the array has to stay
    # traversable - so instead the straps themselves are clipped: a PDN
    # obstruction is only consumed by pdngen, and Odb.RemovePDNObstructions
    # drops it again right after the PDN, leaving the top metal free to route.
    if cfg.clip_pdn_top_over_array and fit.placements:
        x0 = min(p.x for p in fit.placements)
        y0 = min(p.y for p in fit.placements)
        x1 = max(p.x for p in fit.placements) + fit.macro_width_um
        y1 = max(p.y for p in fit.placements) + fit.macro_height_um
        config["PDN_OBSTRUCTIONS"] = [[
            "Metal5", round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]]
    # Keep signal Metal4 out of each macro's open power-pin band.  The macro
    # LEF leaves the rail bands free of OBS so the vccd1/vssd1 rails can be
    # reached: M4 OBS stops at local y 386.955 and only resumes at 389.775 /
    # 390.355, leaving three 0.56 um open channels (one routing track each)
    # at the top edge, mirrored at the bottom.  post_pdn drops a Via4_VH on
    # each rail inside that band; on the 2x4 chip the router threads vertical
    # signal Metal4 through the channels and shorts to the via pads (155
    # Metal4.Shorts + 4 spacings, all VDD/VSS<->signal inside macro bboxes,
    # measured on 128x32 run4 DRT).  A routing obstruction is a dbObstruction:
    # it steers the router away but does not touch the already-placed
    # special-net via, and no macro signal pin sits on M4 in the band (dout
    # are M3 at the bottom edge), so connectivity and LVS are unaffected.
    # The band is blocked across each macro's interior x only; the inter-row
    # and inter-column channels stay free for the signal detour.
    if cfg.macro_power_pin_band_obstruction and fit.placements:
        w = fit.macro_width_um
        h = fit.macro_height_um
        # Local open M4 window where the LEF stops OBS over the rail band:
        # bottom edge y 0 .. 5.66, top edge h-5.66 .. h.  Cover the whole
        # window (not just the 1.7 um rail) so a signal cannot sit beside or
        # above the via pad inside the band.  Overhang ~1 um in x because a
        # via dropped every 20 um can land within M4 spacing of the macro's
        # left/right edge, where a band-y signal just outside the bbox would
        # otherwise still be legal.
        band = 5.66
        xover = 1.0
        obs = []
        for p in fit.placements:
            xlo = round(p.x - xover, 3)
            xhi = round(p.x + w + xover, 3)
            obs.append(["Metal4", xlo, round(p.y, 3),
                        xhi, round(p.y + band, 3)])
            obs.append(["Metal4", xlo, round(p.y + h - band, 3),
                        xhi, round(p.y + h, 3)])
        config["ROUTING_OBSTRUCTIONS"] = obs
    # Supply the SCL/pad Liberty + corner names that the PDK config.tcl fails
    # to expose to LibreLane 3 (see _pdk_corner_config).
    config.update(_pdk_corner_config(cfg))
    # The macro Liberty is already listed in MACROS[...]["lib"].  Listing it a
    # second time in EXTRA_LIBS makes OpenROAD complain that the library
    # already exists and can leave a duplicate macro master without pins.
    globs = _clean_glob(macro)
    if globs:
        config["MAGIC_GDS_FLATGLOB"] = globs

    if not signoff:
        config["meta"]["substituting_steps"] = {
            "KLayout.DRC": None,
            "Checker.KLayoutDRC": None,
            "KLayout.Antenna": None,
            "Checker.KLayoutAntenna": None,
            "KLayout.Density": None,
            "Checker.KLayoutDensity": None,
            "Magic.DRC": None,
            "Checker.MagicDRC": None,
            "KLayout.XOR": None,
            "Netgen.LVS": None,
            "Checker.LVS": None,
        }
    return config


def generate(cfg, slot, macro, fit, out_dir, signoff=True):
    lane_dir = os.path.join(out_dir, "librelane")
    os.makedirs(lane_dir, exist_ok=True)
    config = build_config(cfg, slot, macro, fit, out_dir, signoff=signoff)
    path = os.path.join(lane_dir, "config.yaml")
    with open(path, "w") as handle:
        yaml.safe_dump(config, handle, sort_keys=False, default_flow_style=False)
    return [path], config
