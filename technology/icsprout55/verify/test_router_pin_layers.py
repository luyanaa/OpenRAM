#!/usr/bin/env python3
"""Regression for gdsMill's datatype-less ICsprout55 pin shapes."""
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
          "ics55_1rw_16x4.py")


class PinShapeLayout:
    def __init__(self, shapes):
        self.shapes = shapes

    def getAllPinShapes(self, _pin_name):
        return self.shapes


class RouterPinLayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openram.init_openram(config_file=str(CONFIG), is_unit_test=True)
        from openram.router.router import router
        from openram.sram_factory import factory
        cls.router_type = router
        cls.factory = factory

    @classmethod
    def tearDownClass(cls):
        cls.factory.reset()
        openram.end_openram()

    def test_missing_datatype_accepts_drawing_layer_only(self):
        # gdsMill returns (layer, None) for the real M3 pin; explicit text and
        # dummy purposes, including TR-1um's dedicated label layer, stay out.
        shapes = [
            ((83, None), [0.0, 0.0, 0.17, 0.17]),
            ((83, 1), [1.0, 0.0, 1.17, 0.17]),
            ((83, 2), [2.0, 0.0, 2.17, 0.17]),
            ((81, 6), [3.0, 0.0, 3.17, 0.17]),
            ((48, 0), [4.0, 0.0, 4.17, 0.17]),
        ]
        instance = object.__new__(self.router_type)
        instance.layout = PinShapeLayout(shapes)
        instance.pins = {}
        instance.all_pins = set()
        instance.merge_shapes = lambda _shape, _shape_list: None

        instance.find_pins("csb0")

        pins = instance.pins["csb0"]
        self.assertEqual(len(pins), 2)
        self.assertEqual({pin.lpp for pin in pins}, {(83, 1)})


if __name__ == "__main__":
    unittest.main()
