# See LICENSE for licensing information.
#
# Copyright (c) 2016-2024 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
import os
import shutil
from openram import debug
from openram.base import vector
from openram.base import pin_layout
from openram.tech import layer_names
from openram.tech import layer as tech_layer
from openram import OPTS


class lef:
    """
    SRAM LEF Class open GDS file, read pins information, obstruction
    and write them to LEF file.
    This is inherited by the sram_1bank class.
    """
    def __init__(self, layers):
        # LEF db units per micron
        self.lef_units = 2000
        # These are the layers of the obstructions
        self.lef_layers = layers
        # Round to ensure float values are divisible by 0.0025 (the manufacturing grid)
        self.round_grid = 4

    def magic_lef_write(self, lef_name):
        """ Use a magic script to perform LEF creation. """
        debug.info(3, "Writing abstracted LEF to {0}".format(lef_name))

        # Copy .magicrc file into the output directory
        magic_file = OPTS.openram_tech + "tech/.magicrc"
        if os.path.exists(magic_file):
            shutil.copy(magic_file, OPTS.openram_temp)
        else:
            debug.warning("Could not locate .magicrc file: {}".format(magic_file))

        gds_name = OPTS.openram_temp + "{}.gds".format(self.name)
        self.gds_write(gds_name)

        run_file = OPTS.openram_temp + "run_lef.sh"
        f = open(run_file, "w")
        f.write("#!/bin/sh\n")
        f.write('export OPENRAM_TECH="{}"\n'.format(os.environ['OPENRAM_TECH']))
        f.write('echo "$(date): Starting GDS to MAG using Magic {}"\n'.format(OPTS.drc_exe[1]))
        f.write('\n')
        f.write("{} -dnull -noconsole << EOF\n".format(OPTS.drc_exe[1]))
        f.write("drc off\n")
        f.write("gds polygon subcell true\n")
        f.write("gds warning default\n")
        f.write("gds flatten true\n")
        f.write("gds ordering true\n")
        f.write("gds readonly true\n")
        f.write("gds read {}\n".format(gds_name))
        f.write('puts "Finished reading gds {}"\n'.format(gds_name))
        f.write("load {}\n".format(self.name))
        f.write('puts "Finished loading cell {}"\n'.format(self.name))
        f.write("cellname delete \\(UNNAMED\\)\n")
        f.write("lef write {} -hide\n".format(lef_name))
        f.write('puts "Finished writing LEF cell {}"\n'.format(self.name))
        f.close()
        os.system("chmod u+x {}".format(run_file))
        from openram.verify.run_script import run_script
        (outfile, errfile, resultsfile) = run_script(self.name, "lef")

    def lef_write(self, lef_name):
        """ Write the entire lef of the object to the file. """
        # Can possibly use magic lef write to create the LEF
        # if OPTS.drc_exe and OPTS.drc_exe[0] == "magic":
        #     self.magic_lef_write(lef_name)
        #     return

        # To maintain the indent level easily
        self.indent = ""

        if OPTS.detailed_lef:
            debug.info(3, "Writing detailed LEF to {0}".format(lef_name))
            self.lef_layers = self.used_lef_layers()
        else:
            debug.info(3, "Writing abstract LEF to {0}".format(lef_name))
            self.lef_layers = self.used_lef_layers()
            self.compute_abstract_blockages()

        self.lef  = open(lef_name, "w")
        self.lef_write_header()

        for pin_name in self.pins:
            self.lef_write_pin(pin_name)

        self.lef_write_obstructions(OPTS.detailed_lef)
        self.lef_write_footer()
        self.lef.close()

    def used_lef_layers(self):
        """ Return the obstruction layers that this design actually draws on.

        Abstract LEF blockages are full-block rectangles.  Emitting one on a
        layer that the block never uses (e.g. Metal5 on a macro whose top
        routing layer is Metal4) tells the P&R tool that the whole layer is
        occupied above the macro and makes the chip power grid unroutable
        there.  Only keep layers with real geometry, including geometry that
        lives in a sub-instance.
        """
        used = set()

        def collect(design, seen):
            if id(design) in seen:
                return
            seen.add(id(design))
            for obj in getattr(design, "objs", []):
                used.add((obj.layerNumber, obj.layerPurpose))
            for inst in getattr(design, "insts", []):
                collect(inst.mod, seen)

        collect(self, set())

        layers = []
        for layer_name in self.lef_layers:
            lpp = tech_layer[layer_name]
            if (lpp[0], lpp[1]) in used:
                layers.append(layer_name)
            else:
                debug.info(2, "No {0} geometry: skipping obstruction layer.".format(layer_name))
        # A power pin on the block's top routing layer is also reached by a
        # chip-level strap on the metal one layer up; that band must be a
        # blockage on the upper layer or signals cross the strap and short.
        # Ensure the layer above each power pin is emitted even when the block
        # draws nothing on it.
        for pin_name in self.pins:
            if self.get_pin_type(pin_name) in ["POWER", "GROUND"]:
                for pin in self.get_pins(pin_name):
                    above = self._layer_above(pin.layer)
                    if above is not None and above not in layers:
                        layers.append(above)
        return layers

    def _layer_above(self, layer):
        """ Routing layer one above `layer`, or None at the top of the stack. """
        names = [l for l in self.lef_layers]
        try:
            idx = names.index(layer)
        except ValueError:
            return None
        return names[idx + 1] if idx + 1 < len(names) else None

    def compute_abstract_blockages(self):
        # Start with blockages on all layers the size of the block
        # minus the pin escape margin (hard coded to 4 x m3 pitch)
        # These are a pin_layout to use their geometric functions
        perimeter_margin = self.m3_pitch
        self.blockages = {}
        for layer_name in self.lef_layers:
            self.blockages[layer_name]=[]
        for layer_name in self.lef_layers:
            ll = vector(perimeter_margin, perimeter_margin)
            ur = vector(self.width - perimeter_margin, self.height - perimeter_margin)
            self.blockages[layer_name].append(pin_layout("",
                                                         [ll, ur],
                                                         layer_name))

        # For each pin, remove the blockage and add the pin.
        # Exception: power/ground rails do NOT carve a hole.  They are
        # boundary abutment rails reached top-down by a special-wire via, not
        # by in-band signal routing; carving their hole leaves an open
        # corridor through which the router threads a signal wire across the
        # rail pin shape itself (a hard power<->signal short - 156 Metal4
        # shorts on the 128x32 chip).  The pin PORT is still written, so the
        # rail stays a real pin for LVS/connectivity; only the OBS-free band
        # disappears.  dbObstruction is insufficient (TritonRoute treats it as
        # soft), so the coverage has to come from the master's own OBS.
        # Seal the power rail bands on the layer *above* each rail, inflated
        # ~17 um into the block.  A rail is reached top-down by a chip-level
        # special-net strap on that upper layer; the thin pin-height OBS used
        # earlier left a ~5.7 um stripe inside each ~16.8 um gcell, so GRT
        # still counted the straddling gcell as routable and assigned guide
        # edges across the strap.  DRT then had no legal path and shorted to
        # the strap/pins - and re-rolled the same infeasible guides every
        # pass, which is why runs oscillated instead of converging (run6/run7,
        # 128x32).  Inflating the OBS to cover a full gcell height makes the
        # band gcells show ~zero capacity so GRT detours around them; the
        # ~20 um is under one routing track pitch below what a gcell spans.
        # The pin layer itself keeps the original carve: the rail is a pin,
        # and signals legally crossing it are the strap-ducking jogs DRT makes
        # - they disappear once the M5 guides stop demanding the crossing.
        band_h = 5.66 + self.m3_pitch
        inflate = 17.0
        top_edge = self.height - band_h
        for pin_name in self.pins:
            if self.get_pin_type(pin_name) in ["POWER", "GROUND"]:
                for pin in self.get_pins(pin_name):
                    above = self._layer_above(pin.layer)
                    if above is None or above not in self.blockages:
                        continue
                    # Cover the band the strap rides in, plus ~17 um inward,
                    # on the layer above the rail.  Both top and bottom rails:
                    # the bottom strap suffers the same guide-crossing, and a
                    # 17 um skirt there is cheap because the bottom-edge
                    # signal pins escape on the *rail* layer (M3/M4), not on
                    # the layer above (their via lands below the band).
                    (llx, lly), (urx, ury) = pin.rect[0], pin.rect[1]
                    if lly > top_edge:               # top band
                        ny1, ny2 = lly - inflate, self.height
                    elif ury < band_h:               # bottom band
                        ny1, ny2 = 0.0, ury + inflate
                    else:                            # interior rail, skip
                        continue
                    self.blockages[above].append(pin_layout(
                        "", [vector(llx, max(0.0, ny1)),
                             vector(urx, min(self.height, ny2))], above))
            pins = self.get_pins(pin_name)
            for pin in pins:
                inflated_pin = pin.inflated_pin(multiple=2)
                continue_fragmenting = True
                while continue_fragmenting:
                    continue_fragmenting = False
                    old_blockages = list(self.blockages[pin.layer])
                    for blockage in old_blockages:
                        if blockage.overlaps(inflated_pin):
                            intersection_pin = blockage.intersection(inflated_pin)
                            # If it is zero area, don't split the blockage
                            if not intersection_pin or intersection_pin.area() == 0:
                                continue

                            # Remove the old blockage and add the new ones
                            self.blockages[pin.layer].remove(blockage)
                            new_blockages = blockage.cut(intersection_pin)
                            self.blockages[pin.layer].extend(new_blockages)
                            # We split something so make another pass
                            continue_fragmenting = True

    def lef_write_header(self):
        """ Header of LEF file """
        self.lef.write("VERSION 5.4 ;\n")
        self.lef.write("NAMESCASESENSITIVE ON ;\n")
        self.lef.write("BUSBITCHARS \"[]\" ;\n")
        self.lef.write("DIVIDERCHAR \"/\" ;\n")
        self.lef.write("UNITS\n")
        self.lef.write("  DATABASE MICRONS {0} ;\n".format(self.lef_units))
        self.lef.write("END UNITS\n")

        self.lef.write("{0}MACRO {1}\n".format(self.indent, self.name))
        self.indent += "   "
        self.lef.write("{0}CLASS BLOCK ;\n".format(self.indent))
        self.lef.write("{0}SIZE {1} BY {2} ;\n" .format(self.indent,
                                                        round(self.width, self.round_grid),
                                                        round(self.height, self.round_grid)))
        self.lef.write("{0}SYMMETRY X Y R90 ;\n".format(self.indent))

    def lef_write_footer(self):
        self.indent = self.indent[:-3]
        self.lef.write("{0}END    {1}\n".format(self.indent, self.name))
        self.lef.write("END    LIBRARY\n")

    def lef_write_pin(self, name):
        pin_dir = self.get_pin_dir(name)
        pin_type = self.get_pin_type(name)
        self.lef.write("{0}PIN {1}\n".format(self.indent, name))
        self.indent += "   "

        self.lef.write("{0}DIRECTION {1} ;\n".format(self.indent, pin_dir))

        if pin_type in ["POWER", "GROUND"]:
            self.lef.write("{0}USE {1} ; \n".format(self.indent, pin_type))
            self.lef.write("{0}SHAPE ABUTMENT ; \n".format(self.indent))

        self.lef.write("{0}PORT\n".format(self.indent))
        self.indent += "   "

        # We could sort these together to minimize different layer sections, but meh.
        pin_list = self.get_pins(name)
        for pin in pin_list:
            self.lef.write("{0}LAYER {1} ;\n".format(self.indent, layer_names[pin.layer]))
            self.lef_write_shape(pin.rect)

        # End the PORT
        self.indent = self.indent[:-3]
        self.lef.write("{0}END\n".format(self.indent))

        # End the PIN
        self.indent = self.indent[:-3]
        self.lef.write("{0}END {1}\n".format(self.indent, name))

    def lef_write_obstructions(self, detailed=False):
        """ Write all the obstructions on each layer """
        self.lef.write("{0}OBS\n".format(self.indent))
        for layer in self.lef_layers:
            self.lef.write("{0}LAYER  {1} ;\n".format(self.indent, layer_names[layer]))
            self.indent += "   "
            if detailed:
                blockages = self.get_blockages(layer, True)
                for b in blockages:
                    self.lef_write_shape(b)
            else:
                blockages = self.blockages[layer]
                for b in blockages:
                    self.lef_write_shape(b.rect)
            self.indent = self.indent[:-3]
        self.lef.write("{0}END\n".format(self.indent))

    def lef_write_shape(self, obj):
        if len(obj) == 2:
            """ Write a LEF rectangle """
            self.lef.write("{0}RECT ".format(self.indent))
            for item in obj:
                # print(obj)
                self.lef.write(" {0} {1}".format(round(item[0],
                                                       self.round_grid),
                                                 round(item[1],
                                                       self.round_grid)))
            self.lef.write(" ;\n")
        else:
            """ Write a LEF polygon """
            self.lef.write("{0}POLYGON ".format(self.indent))
            for item in obj:
                self.lef.write(" {0} {1}".format(round(item[0],
                                                       self.round_grid),
                                                 round(item[1],
                                                       self.round_grid)))
            self.lef.write(" ;\n")
