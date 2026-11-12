# Post-PDN fix-ups for the chip flow.
#
# Sourced from pdn_cfg.tcl, which installs the `pdngen` wrapper defined at the
# bottom of this file: pdngen deletes any special wire it did not create itself
# (measured on the 16x8 ODB: a bridged DEF and the unmodified DEF come out
# byte-identical), so the macro connections have to be added *after* pdngen has
# run, not before it.
#
# ---------------------------------------------------------------------------
# Why the macro needs help
#
# The SRAM macro exposes its power as abutment rails, nested one inside the
# other: on Metal4 the outer rail (y 0..1.7 and top-1.7) is vccd1 and the rail
# 1.7 um further in (y 3.4..5.1) is vssd1; on Metal3 the same nesting holds on
# the left and right edges.  vssd1 is therefore completely enclosed by vccd1 and
# no in-plane (Metal3/Metal4) patch can reach it from outside the macro without
# crossing vccd1.
#
# The core PDN was expected to connect the macros by abutment: its vertical
# Metal4 straps are cut at the macro obstruction and are supposed to land on the
# bottom vccd1 rail.  They do not - they stop 0.56 um short (y 1103.985 vs macro
# bottom 1104.545 on the 16x8), and even if they touched they would only reach
# vccd1.  Measured consequence: Magic extraction puts every macro's vccd1/vssd1
# on an isolated net, and Netgen LVS reports exactly those 8 nets as mismatches.
#
# The fix is one Metal5 strap on every macro power rail (the macro has no Metal5
# of its own, so the top metal is free there), a Via4 from that strap down into
# the rail, and a Via4 from the strap down to the same net's full-height Metal4
# core strap where they cross.  The strap spans the core width so a single strap
# serves every macro in the row; it is 1.6 um wide and sits on the same 3.3 um
# spacing the core grid already uses, so it costs almost no routing resource -
# and because it is installed before detailed routing, the router sees it as a
# fixed obstacle instead of shorting to it.
#
# The power pads are *not* bridged here.  The pad ring is 347 um away from the
# core ring and a Metal5 bridge to it crosses the I/O cells' Metal5 obstruction
# (668 Magic "Illegal overlap between obsm5 and metal5" errors), while the pads
# are excluded from LVS anyway (IGNORE_DISCONNECTED_MODULES).  Connecting the
# pad ring is tracked separately - PSM reports the PDN as having no source
# island (PSM-0069) with or without the bridge.
# ---------------------------------------------------------------------------

proc chip_post_pdn {} {
    set blk [ord::get_db_block]
    set tech [ord::get_db_tech]
    set l4 [$tech findLayer Metal4]
    set l5 [$tech findLayer Metal5]
    set via [$tech findVia Via4_VH]

    # The die-strap x extent pdngen uses (11 um past the core on each side).
    set xlo 863000
    set xhi 7001640

    array set strap_of {}
    array set via_done {}
    set nstrap 0
    set nvia 0

    foreach inst [$blk getInsts] {
        if {[[$inst getMaster] getType] ne "BLOCK"} {
            continue
        }
        # Only the unrotated placement is handled; the instance transform for
        # other orientations is not applied to the LEF pin geometry below.
        if {[lsearch -exact {N R0} [$inst getOrient]] < 0} {
            puts "\[WARNING\] chip_post_pdn: skipping rotated macro [$inst getName]"
            continue
        }
        set bb [$inst getBBox]

        foreach iterm [$inst getITerms] {
            set net [$iterm getNet]
            if {$net == "NULL"} {
                continue
            }
            set nn [$net getName]
            if {[lsearch -exact {VDD VSS} $nn] < 0} {
                continue
            }

            foreach mpin [[$iterm getMTerm] getMPins] {
                foreach box [$mpin getGeometry] {
                    if {[$box getTechLayer] != $l4} {
                        continue
                    }
                    set w [expr {[$box xMax] - [$box xMin]}]
                    set h [expr {[$box yMax] - [$box yMin]}]
                    # Power rails are the wide, 1.7 um tall Metal4 boundary
                    # rectangles; skip anything else on the pin.
                    if {$w < 10 * $h} {
                        continue
                    }
                    set y [expr {[$bb yMin] + ([$box yMin] + [$box yMax]) / 2}]
                    set key "$nn $y"

                    if {![info exists strap_of($key)]} {
                        # Reuse pdngen's own swire: TritonRoute's parser
                        # segfaults reading a power net that has more than one
                        # `dbSWire` (measured: Signal 11 in
                        # `drt::io::Parser::updateNetRouting` as soon as the
                        # step-41 ODB carries the extra swires, read fine once
                        # they are merged back into the single original one).
                        set sw [lindex [$net getSWires] 0]
                        if {$sw == ""} {
                            set sw [odb::dbSWire_create $net ROUTED]
                        }
                        # A real box, not a zero-height `x1 y1 x2 y1` +
                        # `setDesignRuleWidth` pair: TritonRoute's parser
                        # segfaults on the latter (measured: Signal 11 in
                        # `drt::io::Parser::updateNetRouting`; pdngen's own
                        # straps are stored as 3200 dbdu tall boxes with the
                        # design-rule width unset, -1).
                        # The rail straps span the *die-strap* x extent, not the
                        # core: pdngen's own Metal5 stripes run x 863000-7001640
                        # dbdu (11 um past the core on each side, to reach the
                        # die ring).  Using the core area leaves a 10.9 um
                        # Metal5 stub on each end, which Magic DRC flags as
                        # 81 MT.1 + 16 MT.4 hits at the four rail ys (measured
                        # on 16x8 run4); matching the die-strap x removes them.
                        odb::dbSBox_create $sw $l5 863000 [expr {$y - 1600}] \
                            7001640 [expr {$y + 1600}] STRIPE
                        set strap_of($key) $sw
                        incr nstrap
                        puts [format "\[INFO\] post_pdn: %s rail strap y=%.3f um (core width)" \
                                  $nn [expr {$y / 2000.0}]]
                    }

                    # Tie the rail to the strap along the whole macro so the
                    # connection is distributed and not a single point.
                    set x [expr {[$bb xMin] + 5000}]
                    while {$x <= [$bb xMax] - 2000} {
                        if {![info exists via_done($key,$x)]} {
                            odb::dbSBox_create $strap_of($key) $via $x $y STRIPE
                            set via_done($key,$x) 1
                            incr nvia
                        }
                        set x [expr {$x + 40000}]
                    }
                }
            }
        }
    }

    # Drop to the core grid: the vertical Metal4 straps outside the macro array
    # are the only same-net Metal4 geometry that reaches the strap's y.
    foreach key [array names strap_of] {
        lassign [split $key " "] nn y
        set net [$blk findNet $nn]
        foreach swire [$net getSWires] {
            foreach w [$swire getWires] {
                if {[$w getTechLayer] != $l4 || [$w isVia]} {
                    continue
                }
                # DEF writes the design-rule width explicitly, so a "vertical"
                # strap is wider in y than in x, not xMin == xMax.
                if {([$w xMax] - [$w xMin]) >= ([$w yMax] - [$w yMin])} {
                    continue
                }
                set vx [$w xMin]
                if {[$w yMin] <= $y && $y <= [$w yMax] \
                        && ![info exists via_done($key,$vx)]} {
                    odb::dbSBox_create $strap_of($key) $via $vx $y STRIPE
                    set via_done($key,$vx) 1
                    incr nvia
                }
            }
        }
    }

    puts "\[INFO\] post_pdn: $nstrap macro rail straps, $nvia Via4"
}

# pdngen is invoked by LibreLane's pdn.tcl as `log_cmd pdngen ...`, i.e. after
# the whole of pdn_cfg.tcl (and therefore this file) has been sourced.
rename pdngen chip_pdngen_orig
proc pdngen {args} {
    chip_pdngen_orig {*}$args
    chip_post_pdn
}
