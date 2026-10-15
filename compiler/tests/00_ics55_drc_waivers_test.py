#!/usr/bin/env python3
"""Check that known vendor markers cannot waive generated SRAM geometry."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET


SCRIPT = Path(__file__).resolve().parents[2] / "technology/icsprout55/verify/opensource.py"
spec = importlib.util.spec_from_file_location("ics55_opensource", SCRIPT)
opensource = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opensource)


class DrcWaiversTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.marker = "edge-pair: (0,0;0,1)|(0.01,0;0.01,1)"
        self.waivers = self.root / "waivers.json"
        self.waivers.write_text(json.dumps({"version": 1, "waivers": [{
            "cell": "vendor_dff", "category": "active.act.width",
            "values": [self.marker], "reason": "Approved vendor geometry"
        }]}))

    def report(self, entries, top="macro"):
        root = ET.Element("report-database")
        ET.SubElement(root, "top-cell").text = top
        items = ET.SubElement(root, "items")
        for cell, marker, multiplicity in entries:
            item = ET.SubElement(items, "item")
            ET.SubElement(item, "category").text = "'active.act.width'"
            ET.SubElement(item, "cell").text = cell
            ET.SubElement(item, "multiplicity").text = str(multiplicity)
            ET.SubElement(ET.SubElement(item, "values"), "value").text = marker
        path = self.root / "drc.lyrdb"
        ET.ElementTree(root).write(path)
        return path

    def test_marker_identity_does_not_hide_generated_or_changed_geometry(self):
        path = self.report([
            ("vendor_dff", self.marker, 2),
            ("macro", self.marker, 1),
            ("generated_dff_wrapper", self.marker, 1),
            ("vendor_dff", self.marker.replace("0.01", "0.02"), 1),
        ])
        original = path.read_bytes()
        summary = opensource.summarize_drc_report(path, self.waivers, out=lambda _: None)
        self.assertEqual((summary["raw"], summary["waived"], summary["unwaived"]), (5, 2, 3))
        self.assertEqual(summary["cells"]["macro"]["unwaived"], 1)
        self.assertEqual(summary["cells"]["generated_dff_wrapper"]["unwaived"], 1)
        self.assertEqual(path.read_bytes(), original)

    def test_standalone_or_missing_scope_is_not_waived(self):
        for top in ("vendor_dff", None):
            with self.subTest(top=top):
                path = self.report([("vendor_dff", self.marker, 1)], top=top)
                summary = opensource._parse_drc_report(path, self.waivers)
                self.assertEqual((summary["waived"], summary["unwaived"]), (0, 1))

    def test_invalid_multiplicity_cannot_turn_findings_into_a_pass(self):
        for count in (0, -1, "invalid"):
            with self.subTest(count=count):
                path = self.report([("vendor_dff", self.marker, count)])
                with self.assertRaises(opensource.OpenSourceError):
                    opensource._parse_drc_report(path, self.waivers)

    def test_wildcard_waiver_is_rejected(self):
        data = json.loads(self.waivers.read_text())
        data["waivers"][0]["cell"] = "*dff*"
        self.waivers.write_text(json.dumps(data))
        with self.assertRaises(opensource.OpenSourceError):
            opensource._load_drc_waivers(self.waivers)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(SCRIPT.parents[3]))
    import common
    common.make_openram_package()
    from openram import parse_args
    parse_args()
    del sys.argv[1:]
    unittest.main()
