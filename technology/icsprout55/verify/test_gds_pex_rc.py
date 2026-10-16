"""Regression tests for distributed GDS PEX device-to-metal connectivity."""
import unittest

from gds_pex_rc import extract_parasitics


class RccAnchorTests(unittest.TestCase):
    @staticmethod
    def geometry():
        return {
            "design": "anchor_regression",
            "ports": ["probe"],
            "nets": [
                {
                    "name": "probe",
                    "rectangles": [
                        {"layer": "MET1", "x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 1.0}
                    ],
                    "vias": [],
                    "anchors": [
                        {"name": "probe", "layer": "MET1", "x": 4.5, "y": 0.5},
                        {"name": "feol:P_SD:0", "layer": "MET1", "x": 2.5, "y": 0.5},
                        {"name": "feol:P_SD:0", "layer": "MET1", "x": 4.5, "y": 0.5},
                        {"name": "feol:P_SD:1", "layer": "MET1", "x": 8.5, "y": 0.5},
                    ],
                },
                {
                    "name": "gate",
                    "rectangles": [
                        {"layer": "MET1", "x0": 0.0, "y0": 2.0, "x1": 1.0, "y1": 3.0}
                    ],
                    "vias": [],
                    "anchors": [
                        {"name": "feol:POLY_CON:0", "layer": "MET1", "x": 0.5, "y": 2.5}
                    ],
                },
                {
                    "name": "body",
                    "rectangles": [
                        {"layer": "MET1", "x0": 0.0, "y0": 4.0, "x1": 1.0, "y1": 5.0}
                    ],
                    "vias": [],
                    "anchors": [
                        {"name": "feol:NW:0", "layer": "MET1", "x": 0.5, "y": 4.5}
                    ],
                },
            ],
            "devices": [
                {
                    "name": "M1",
                    "model": "pm1p2_svt_lp",
                    "w_um": 0.3,
                    "l_um": 0.06,
                    "terminals": {"D": "probe", "G": "gate", "S": "probe", "B": "body"},
                    "terminal_components": {
                        "D": "feol:P_SD:0",
                        "G": "feol:POLY_CON:0",
                        "S": "feol:P_SD:1",
                        "B": "feol:NW:0",
                    },
                }
            ],
        }

    @staticmethod
    def process():
        return {
            "status": "estimated",
            "source": "test",
            "max_cross_layer": 1,
            "max_spacing": 5.0,
            "layers": {
                "MET1": {
                    "width_um": 1.0,
                    "resistance_ohm_per_square": 1.0,
                    "ground_capacitance_pf_per_um2": 0.001,
                    "edge_capacitance_pf_per_um": 0.001,
                    "coupling": {},
                }
            },
            "vias": {},
            "limitations": [],
        }

    def test_rcc_preserves_feol_shorts_and_separate_metal_accesses(self):
        parasitics = extract_parasitics(self.geometry(), self.process(), mode="rcc")

        self.assertEqual(parasitics["conductor_contract"], "common-logical-and-physical-v1")
        device_drain = parasitics["terminal_nodes"]["M1:D"]
        device_source = parasitics["terminal_nodes"]["M1:S"]
        self.assertEqual(device_drain, "probe")
        self.assertNotEqual(device_drain, device_source)
        self.assertTrue(parasitics["resistors"])
        self.assertEqual(parasitics["conductor_nodes"]["port:probe"]["node"], "probe")
        self.assertEqual(parasitics["conductor_nodes"]["component:feol:P_SD:0"]["logical_net"], "probe")
        self.assertEqual(parasitics["conductor_nodes"]["component:feol:P_SD:0"]["kind"], "feol-or-port")
    def test_c_mode_uses_the_same_logical_conductor_contract(self):
        parasitics = extract_parasitics(self.geometry(), self.process(), mode="c")

        self.assertEqual(parasitics["port_nodes"]["probe"], "probe")
        self.assertEqual(parasitics["terminal_nodes"]["M1:D"], "probe")
        self.assertEqual(parasitics["conductor_nodes"]["net:probe"]["node"], "probe")
        self.assertEqual(parasitics["conductor_nodes"]["port:probe"]["kind"], "port")
        self.assertEqual(
            parasitics["conductor_nodes"]["component:feol:P_SD:0"]["logical_net"],
            "probe")

    def test_repeated_port_shapes_share_the_top_level_node(self):
        geometry = self.geometry()
        signal = geometry["nets"][0]
        signal["rectangles"].append(
            {"layer": "MET2", "x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 1.0}
        )
        signal["anchors"].append(
            {"name": "probe", "layer": "MET2", "x": 4.5, "y": 0.5}
        )
        signal["vias"].append(
            {"layer": "VIA1", "lower_layer": "MET1", "upper_layer": "MET2", "x": 4.5, "y": 0.5}
        )
        process = self.process()
        process["layers"]["MET2"] = dict(process["layers"]["MET1"])
        process["vias"] = {
            "VIA1": {"resistance_ohm": 0.2, "lower_layer": "MET1", "upper_layer": "MET2"}
        }

        parasitics = extract_parasitics(geometry, process, mode="rcc")

        self.assertEqual(parasitics["port_nodes"]["probe"], "probe")
        self.assertEqual(parasitics["terminal_nodes"]["M1:D"], "probe")

    def test_rcc_maps_top_level_ports_to_their_named_nodes(self):
        parasitics = extract_parasitics(self.geometry(), self.process(), mode="rcc")

        self.assertEqual(parasitics["port_nodes"]["probe"], "probe")
        self.assertEqual(parasitics["terminal_nodes"]["M1:D"], "probe")

    def test_rcc_inventories_unanchored_metal_without_grounding(self):
        geometry = self.geometry()
        geometry["nets"].append({
            "name": "floating_metal",
            "rectangles": [{"layer": "MET1", "x0": 20.0, "y0": 0.0,
                            "x1": 22.0, "y1": 1.0}],
            "vias": [],
            "anchors": [],
        })

        parasitics = extract_parasitics(geometry, self.process(), mode="rcc")

        self.assertEqual(len(parasitics["floating_conductors"]), 1)
        component = parasitics["floating_conductors"][0]
        self.assertEqual(component["logical_nets"], ["floating_metal"])
        self.assertEqual(component["representative_node"], component["nodes"][0])
        self.assertNotIn(component["representative_node"].casefold(), {"0", "gnd"})
        self.assertIsNone(component["initial_voltage_v"])
        self.assertEqual(component["initial_voltage_status"], "unknown")
        self.assertIsNone(component["initial_charge_c"])
        self.assertEqual(component["initial_charge_status"], "unknown")
        self.assertEqual(component["state_status"], "unknown")
        floating_key = next(key for key in parasitics["conductor_nodes"]
                            if key.startswith("floating:"))
        self.assertEqual(parasitics["conductor_nodes"][floating_key]["kind"],
                         "floating-metal")
        self.assertEqual(parasitics["conductor_nodes"][floating_key]["node"],
                         component["representative_node"])
        self.assertTrue(any(
            capacitor["node1"] == component["representative_node"] and capacitor["node2"] == "0"
            for capacitor in parasitics["capacitors"]))
        self.assertFalse(any(
            resistor["node1"].casefold() in {"0", "gnd"} or
            resistor["node2"].casefold() in {"0", "gnd"}
            for resistor in parasitics["resistors"]))

    def test_uncontacted_diffusion_keeps_a_shared_ideal_node(self):
        geometry = self.geometry()
        geometry["nets"].append({
            "name": "floating",
            "special": False,
            "rectangles": [],
            "vias": [],
            "anchors": [],
        })
        geometry["devices"][0]["terminals"]["S"] = "floating"
        geometry["devices"][0]["terminal_components"]["S"] = "feol:P_SD:2"
        geometry["devices"].append({
            "name": "M2",
            "model": "pm1p2_svt_lp",
            "w_um": 0.3,
            "l_um": 0.06,
            "terminals": {"D": "floating", "G": "gate", "S": "probe", "B": "body"},
            "terminal_components": {
                "D": "feol:P_SD:2",
                "G": "feol:POLY_CON:0",
                "S": "feol:P_SD:1",
                "B": "feol:NW:0",
            },
        })

        parasitics = extract_parasitics(geometry, self.process(), mode="rcc")

        first = parasitics["terminal_nodes"]["M1:S"]
        second = parasitics["terminal_nodes"]["M2:D"]
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("__PEX_FEOL_"))
        self.assertNotEqual(first, parasitics["port_nodes"]["probe"])

    def test_one_feol_identity_cannot_span_nets(self):
        geometry = self.geometry()
        geometry["nets"][1]["anchors"].append(
            {"name": "feol:P_SD:0", "layer": "MET1", "x": 0.5, "y": 2.5}
        )

        with self.assertRaisesRegex(ValueError, "anchor feol:P_SD:0 belongs to different nets"):
            extract_parasitics(geometry, self.process(), mode="rcc")


if __name__ == "__main__":
    unittest.main()
