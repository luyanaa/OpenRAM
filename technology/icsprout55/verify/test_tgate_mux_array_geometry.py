#!/usr/bin/env python3
"""Geometry regressions for ICsprout55 transmission-gate mux arrays."""
import os
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("OPENRAM_HOME", str(REPO_ROOT / "compiler"))
import common
common.make_openram_package()
import openram


CONFIG = (REPO_ROOT / "technology/icsprout55/configs/"
          "ics55_1rw_256x8_mux4_tgate.py")


def rect_bounds(rect):
    ll, ur = rect
    return (float(ll.x), float(ll.y), float(ur.x), float(ur.y))


def overlap(first, second):
    return (min(first[2], second[2]) > max(first[0], second[0]) and
            min(first[3], second[3]) > max(first[1], second[1]))


def conflicts(first, second, spacing):
    x_gap = max(0.0, first[0] - second[2], second[0] - first[2])
    y_gap = max(0.0, first[1] - second[3], second[1] - first[3])
    return x_gap + 1e-9 < spacing and y_gap + 1e-9 < spacing


def same_rect(first, second):
    return all(abs(left - right) < 1e-9
               for left, right in zip(first, second))


def placed_layer_rectangles(geometry, inst, layer_name):
    local = geometry._module_layer_rectangles(inst.mod, layer_name)
    return [geometry._transform_rect(rect, inst) for rect in local]


def contact_rectangles(inst, layer_name, tech):
    lpp = tech.layer[layer_name]
    rectangles = []
    for shape in inst.mod.objs:
        if (shape.name == "rect" and shape.layerNumber == lpp[0] and
                shape.layerPurpose == lpp[1]):
            rectangles.append((shape.offset.x + inst.offset.x,
                               shape.offset.y + inst.offset.y,
                               shape.offset.x + inst.offset.x + shape.width,
                               shape.offset.y + inst.offset.y + shape.height))
    return rectangles


def contact_center(inst):
    return (inst.offset.x + 0.5 * inst.mod.width,
            inst.offset.y + 0.5 * inst.mod.height)


def root_rectangles(design, layer_name, tech):
    lpp = tech.layer[layer_name]
    rectangles = []
    for shape in design.objs:
        if (shape.name == "rect" and shape.layerNumber == lpp[0] and
                shape.layerPurpose == lpp[1]):
            rectangles.append((shape.offset.x, shape.offset.y,
                               shape.offset.x + shape.width,
                               shape.offset.y + shape.height))
    return rectangles


class TgMuxArrayGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openram.init_openram(config_file=str(CONFIG), is_unit_test=True)
        from openram import OPTS, tech
        from openram.sram_factory import factory
        cls.OPTS = OPTS
        cls.tech = tech
        cls.factory = factory

    @classmethod
    def tearDownClass(cls):
        cls.factory.reset()
        openram.end_openram()

    def assert_landing_legality_and_connectivity(self, array, columns):
        geometry = sys.modules[array.__class__.__module__]
        self.assertEqual(len(array.mux_inst), columns)
        self.assertAlmostEqual(array.width, columns * array.mux.width)
        self.assertGreater(array.height, array.mux.height)

        vdd_pin = array.get_pin("vdd")
        self.assertEqual(vdd_pin.layer, "m3")
        rail = rect_bounds(vdd_pin.rect)
        rail_spacing = self.tech.drc("m3_to_m3")
        m3_rail_shapes = []
        for leaf in array.mux_inst:
            m3_rail_shapes.extend(placed_layer_rectangles(geometry, leaf, "m3"))
        m3_rail_shapes.extend(root_rectangles(array, "m3", self.tech))
        for shape in m3_rail_shapes:
            self.assertFalse(conflicts(rail, shape, rail_spacing),
                             "VDD M3 rail violates spacing to an existing M3 shape")

        via1_instances = [inst for inst in array.insts
                          if tuple(getattr(inst.mod, "layer_stack", ())) ==
                          tuple(array.m1_stack)]
        via2_instances = [inst for inst in array.insts
                          if tuple(getattr(inst.mod, "layer_stack", ())) ==
                          tuple(array.m2_stack)]
        supply_via2 = [inst for inst in via2_instances
                       if any(overlap(shape, rail)
                              for shape in contact_rectangles(inst, "m3", self.tech))]
        self.assertEqual(len(supply_via2), columns)

        for via2 in supply_via2:
            x, y = contact_center(via2)
            matching_via1 = [inst for inst in via1_instances
                             if abs(contact_center(inst)[0] - x) < 1e-9 and
                             abs(contact_center(inst)[1] - y) < 1e-9]
            self.assertEqual(len(matching_via1), 1)
            via1 = matching_via1[0]
            self.assertTrue(any(overlap(shape, rail)
                                for shape in contact_rectangles(via2, "m3", self.tech)),
                            "VDD rail does not overlap its M3 via enclosure")

            leaf = min(array.mux_inst,
                       key=lambda inst: 0 if inst.lx() - 1e-9 <= x <= inst.rx() + 1e-9
                       and inst.by() - 1e-9 <= y <= inst.uy() + 1e-9 else 1)
            pin = leaf.get_pin("vdd")
            pin_rect = rect_bounds(pin.rect)
            layer_shapes = {
                "m1": contact_rectangles(via1, "m1", self.tech),
                "via1": contact_rectangles(via1, "via1", self.tech),
                "m2": (contact_rectangles(via1, "m2", self.tech) +
                       contact_rectangles(via2, "m2", self.tech)),
                "via2": contact_rectangles(via2, "via2", self.tech),
                "m3": contact_rectangles(via2, "m3", self.tech),
            }
            # The intermediate M2 minimum-area enclosure is a top-level rectangle
            # created by add_via_stack_center at this exact via-stack center.
            for shape in root_rectangles(array, "m2", self.tech):
                if abs(0.5 * (shape[0] + shape[2]) - x) < 1e-9 and \
                   abs(0.5 * (shape[1] + shape[3]) - y) < 1e-9:
                    layer_shapes["m2"].append(shape)

            for layer_name, via_shapes in layer_shapes.items():
                spacing = self.tech.drc("{}_to_{}".format(layer_name, layer_name))
                leaf_shapes = placed_layer_rectangles(geometry, leaf, layer_name)
                for via_shape in via_shapes:
                    for leaf_shape in leaf_shapes:
                        if layer_name == "m1" and same_rect(leaf_shape, pin_rect):
                            continue
                        self.assertFalse(
                            conflicts(via_shape, leaf_shape, spacing),
                            "{} via-stack footprint violates spacing at ({}, {})"
                            .format(layer_name, x, y))

            m1_pin_shapes = placed_layer_rectangles(geometry, leaf, "m1")
            route_shapes = [shape for shape in root_rectangles(array, "m1", self.tech)
                            if overlap(shape, pin_rect) and
                            any(overlap(shape, via_shape)
                                for via_shape in layer_shapes["m1"])]
            self.assertTrue(route_shapes,
                            "No M1 strap joins the leaf VDD rail to its via landing")
            for route in route_shapes:
                for leaf_shape in m1_pin_shapes:
                    if same_rect(leaf_shape, pin_rect):
                        continue
                    self.assertFalse(conflicts(route, leaf_shape,
                                               self.tech.drc("m1_to_m1")),
                                     "M1 VDD strap violates same-layer spacing")

    def test_mux_array_landing_geometry_sweep(self):
        for n_size, p_size in ((8, 8), (8, 12)):
            for columns in (8, 16, 32):
                with self.subTest(n_size=n_size, p_size=p_size, columns=columns):
                    self.factory.reset()
                    self.OPTS.ics55_column_mux_style = "tgate"
                    self.OPTS.ics55_column_mux_nmos_size = n_size
                    self.OPTS.ics55_column_mux_pmos_size = p_size
                    array = self.factory.create(module_type="column_mux_array",
                                                columns=columns, word_size=8,
                                                bitcell_bl="bl0", bitcell_br="br0")
                    self.assertAlmostEqual(
                        array.mux.pass_n_width,
                        n_size * self.tech.drc("minwidth_tx"))
                    self.assertAlmostEqual(
                        array.mux.pass_p_width,
                        p_size * self.tech.drc("minwidth_tx"))
                    self.assert_landing_legality_and_connectivity(array, columns)


if __name__ == "__main__":
    unittest.main()
