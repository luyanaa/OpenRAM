# Power distribution network for the gf180mcu chip flow.
#
# This is the LibreLane/OpenLane PDN configuration with one addition: a set of
# vertical Metal5 straps on the standard-cell grid, congruent with the Metal4
# vertical straps.
#
# Why: the OpenRAM macro's power pins are boundary rails, vccd1/vssd1 on Metal4
# along the top and bottom edges and on Metal3 along the left and right edges.
# The PDK default grid is Metal4 vertical / Metal5 horizontal, so a Metal4
# strap cannot cross the macro (its Metal4 obstruction covers the interior),
# and a horizontal Metal5 strap is parallel to the macro's Metal4 rails and
# never overlaps them.  The macro grid's "add_pdn_connect -layers {Metal4
# Metal5}" then has nothing to connect to and OpenROAD aborts with
# PDN-0232/PDN-0233.
#
# Vertical Metal5 straps cross the macro's horizontal Metal4 rails, so the
# macro grid can drop a via4 at each intersection.  Because the straps use the
# same pitch/offset as the Metal4 vertical straps they land directly on them,
# which also ties the two grids together outside the macros and lets via4 be
# dropped along the whole strap.  The PDN_*_HALO values must be 0 so the
# straps reach the macro boundary pins.

source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
source $::env(SCRIPTS_DIR)/openroad/common/set_global_connections.tcl
set_global_connections

set secondary []
foreach vdd $::env(VDD_NETS) gnd $::env(GND_NETS) {
    if { $vdd != $::env(VDD_NET)} {
        lappend secondary $vdd

        set db_net [[ord::get_db_block] findNet $vdd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $vdd]
            $net setSpecial
            $net setSigType "POWER"
        }
    }

    if { $gnd != $::env(GND_NET)} {
        lappend secondary $gnd

        set db_net [[ord::get_db_block] findNet $gnd]
        if {$db_net == "NULL"} {
            set net [odb::dbNet_create [ord::get_db_block] $gnd]
            $net setSpecial
            $net setSigType "GROUND"
        }
    }
}

set_voltage_domain -name CORE -power $::env(VDD_NET) -ground $::env(GND_NET) \
    -secondary_power $secondary

if { $::env(PDN_MULTILAYER) == 1 } {

    set arg_list [list]
    if { $::env(PDN_ENABLE_PINS) } {
        lappend arg_list -pins "$::env(PDN_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
    }

    define_pdn_grid \
        -name stdcell_grid \
        -starts_with POWER \
        -voltage_domain CORE \
        {*}$arg_list

    set arg_list [list]
    append_if_equals arg_list PDN_EXTEND_TO "core_ring" -extend_to_core_ring
    append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_VERTICAL_LAYER) \
        -width $::env(PDN_VWIDTH) \
        -pitch $::env(PDN_VPITCH) \
        -offset $::env(PDN_VOFFSET) \
        -spacing $::env(PDN_VSPACING) \
        -starts_with POWER \
        {*}$arg_list

    # Macro power connection straps (see the header comment).  Same width,
    # pitch, offset and spacing as the vertical straps so the layers are
    # congruent, and a via4 connect to the vertical layer.
    if { $::env(PDN_VERTICAL_LAYER) != "Metal5" } {
        add_pdn_stripe \
            -grid stdcell_grid \
            -layer Metal5 \
            -width $::env(PDN_VWIDTH) \
            -pitch $::env(PDN_VPITCH) \
            -offset $::env(PDN_VOFFSET) \
            -spacing $::env(PDN_VSPACING) \
            -starts_with POWER \
            {*}$arg_list

        add_pdn_connect \
            -grid stdcell_grid \
            -layers "$::env(PDN_VERTICAL_LAYER) Metal5"
    }
}

# Adds the standard cell rails if enabled.
if { $::env(PDN_ENABLE_RAILS) == 1 } {
    add_pdn_stripe \
        -grid stdcell_grid \
        -layer $::env(PDN_RAIL_LAYER) \
        -width $::env(PDN_RAIL_WIDTH) \
        -followpins

    add_pdn_connect \
        -grid stdcell_grid \
        -layers "$::env(PDN_RAIL_LAYER) $::env(PDN_VERTICAL_LAYER)"
}

# Adds the core ring if enabled.
if { $::env(PDN_CORE_RING) == 1 } {
    if { $::env(PDN_MULTILAYER) == 1 } {
        set arg_list [list]
        append_if_flag arg_list PDN_CORE_RING_ALLOW_OUT_OF_DIE -allow_out_of_die
        append_if_flag arg_list PDN_CORE_RING_CONNECT_TO_PADS -connect_to_pads
        append_if_equals arg_list PDN_EXTEND_TO "boundary" -extend_to_boundary

        set pdn_core_vertical_layer $::env(PDN_VERTICAL_LAYER)
        set pdn_core_horizontal_layer $::env(PDN_HORIZONTAL_LAYER)

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            set pdn_core_vertical_layer $::env(PDN_CORE_VERTICAL_LAYER)
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            set pdn_core_horizontal_layer $::env(PDN_CORE_HORIZONTAL_LAYER)
        }

        add_pdn_ring \
            -grid stdcell_grid \
            -layers "$pdn_core_vertical_layer $pdn_core_horizontal_layer" \
            -widths "$::env(PDN_CORE_RING_VWIDTH) $::env(PDN_CORE_RING_HWIDTH)" \
            -spacings "$::env(PDN_CORE_RING_VSPACING) $::env(PDN_CORE_RING_HSPACING)" \
            -core_offset "$::env(PDN_CORE_RING_VOFFSET) $::env(PDN_CORE_RING_HOFFSET)" \
            {*}$arg_list

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_HORIZONTAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_HORIZONTAL_LAYER) $::env(PDN_VERTICAL_LAYER)"
        }

        if { [info exists ::env(PDN_CORE_VERTICAL_LAYER)] && [info exists ::env(PDN_CORE_HORIZONTAL_LAYER)] } {
            add_pdn_connect \
                -grid stdcell_grid \
                -layers "$::env(PDN_CORE_VERTICAL_LAYER) $::env(PDN_CORE_HORIZONTAL_LAYER)"
        }

    } else {
        throw APPLICATION "PDN_CORE_RING cannot be used when PDN_MULTILAYER is set to false."
    }
}

# The macro power pins are boundary rails (Metal4 along the top and bottom
# edges, Metal3 along the left and right edges).  The vertical Metal4 straps of
# the core grid are cut at the macro obstructions and therefore land exactly on
# the Metal4 rails, so the macro is connected by abutment.  Defining an
# OpenROAD macro grid here would ask for a via between the Metal4 rail and a
# Metal5 shape of the macro grid, and the macro has no Metal5 at all, so
# pdngen aborts (PDN-0232/PDN-0233).
