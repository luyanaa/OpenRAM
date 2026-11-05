# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""Pure cycle-model coverage for the frozen register-file access contract."""
import unittest

from compiler.architecture.register_file import (
    CopyDivergenceError,
    RegisterFileModel,
    UninitializedReadError,
    UnsupportedRegisterFileLayout,
    reject_unsupported_layout,
    validate_port_roles,
)


class RegisterFileModelTest(unittest.TestCase):
    def setUp(self):
        self.model = RegisterFileModel(num_words=4, word_size=8)

    def tick(self, *, r0=False, a0=0, r1=False, a1=0, w=False, wa=None, wd=None):
        return self.model.tick(
            read0_enable=r0, read0_address=a0,
            read1_enable=r1, read1_address=a1,
            write_enable=w, write_address=wa, write_data=wd,
        )

    def test_requires_exact_ordered_read_read_write_roles_and_explicit_types(self):
        self.assertEqual(validate_port_roles(["R", "R", "W"]), ("R", "R", "W"))
        for roles in (("R", "W", "R"), ("R", "R"), ("R", "R", "W", "W"),
                      ("R", "R", 1)):
            with self.subTest(roles=roles), self.assertRaises((TypeError, ValueError)):
                validate_port_roles(roles)
        with self.assertRaises(TypeError):
            validate_port_roles("RRW")
        with self.assertRaises(TypeError):
            RegisterFileModel(True, 8)
        with self.assertRaises(TypeError):
            RegisterFileModel(4, 8.0)

    def test_simultaneous_two_reads_and_write_with_new_data_bypass(self):
        self.tick(w=True, wa=1, wd=0x12)
        self.tick(w=True, wa=2, wd=0xA5)

        result = self.tick(r0=True, a0=1, r1=True, a1=2, w=True, wa=1, wd=0x34)
        self.assertEqual((result.read0, result.read1), (0x34, 0xA5))
        self.assertTrue(result.read0_valid)
        self.assertTrue(result.read1_valid)
        self.assertEqual(result.cycle, 3)

        # Both independent read ports receive same-cycle new data when both
        # target the one writer's address.
        result = self.tick(r0=True, a0=2, r1=True, a1=2, w=True, wa=2, wd=0x5A)
        self.assertEqual((result.read0, result.read1), (0x5A, 0x5A))
        self.assertEqual(self.model._copies[0][2], self.model._copies[1][2])

    def test_read_only_cycles_support_distinct_and_equal_addresses(self):
        self.tick(w=True, wa=0, wd=0x11)
        self.tick(w=True, wa=1, wd=0x22)
        distinct = self.tick(r0=True, a0=0, r1=True, a1=1)
        self.assertEqual((distinct.read0, distinct.read1), (0x11, 0x22))

        same = self.tick(r0=True, a0=1, r1=True, a1=1)
        self.assertEqual((same.read0, same.read1), (0x22, 0x22))
        held = self.tick(a0=0, a1=0)
        self.assertEqual((held.read0, held.read1), (0x22, 0x22))
        self.assertFalse(held.read0_valid)
        self.assertFalse(held.read1_valid)

    def test_write_commits_for_later_reads(self):
        self.tick(w=True, wa=3, wd=0xD7)
        result = self.tick(r0=True, a0=3, r1=False, a1=0)
        self.assertEqual(result.read0, 0xD7)
        self.assertTrue(result.read0_valid)

    def test_uninitialized_read_rejects_entire_tick_without_partial_write(self):
        with self.assertRaises(UninitializedReadError) as raised:
            self.tick(r0=True, a0=0, w=True, wa=1, wd=0x44)
        self.assertEqual(raised.exception.port, 0)
        self.assertEqual(raised.exception.address, 0)
        self.assertEqual(self.model.cycle, 0)
        self.assertEqual(self.model._copies, ({}, {}))

    def test_same_cycle_write_initializes_read_address_through_bypass(self):
        result = self.tick(r0=True, a0=0, r1=True, a1=0, w=True, wa=0, wd=0xFE)
        self.assertEqual((result.read0, result.read1), (0xFE, 0xFE))
        self.assertEqual(self.model._copies, ({0: 0xFE}, {0: 0xFE}))

    def test_detects_replicated_copy_divergence(self):
        self.tick(w=True, wa=2, wd=0x65)
        self.model._copies[1][2] = 0x66
        with self.assertRaises(CopyDivergenceError) as raised:
            self.model.assert_copy_consistency()
        self.assertEqual(raised.exception.address, 2)
        with self.assertRaises(CopyDivergenceError):
            self.tick(a0=0, a1=0)

    def test_validates_input_types_ranges_and_write_disable_shape(self):
        for kwargs in (
            {"read0_enable": 1, "read0_address": 0, "read1_enable": False,
             "read1_address": 0, "write_enable": False},
            {"read0_enable": False, "read0_address": True, "read1_enable": False,
             "read1_address": 0, "write_enable": False},
            {"read0_enable": False, "read0_address": 0, "read1_enable": False,
             "read1_address": 0, "write_enable": False, "write_data": 0},
            {"read0_enable": False, "read0_address": 0, "read1_enable": False,
             "read1_address": 0, "write_enable": True, "write_address": 0,
             "write_data": True},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                self.model.tick(**kwargs)
        with self.assertRaises(ValueError):
            self.tick(a0=4)
        with self.assertRaises(ValueError):
            self.tick(w=True, wa=0, wd=256)

    def test_native_layout_has_stable_rejection_above_two_ports(self):
        self.assertIsNone(reject_unsupported_layout(2))
        with self.assertRaises(UnsupportedRegisterFileLayout) as raised:
            reject_unsupported_layout(3)
        self.assertEqual(raised.exception.code, "unsupported-register-file-layout")
        self.assertIn("at most 2 total physical ports", str(raised.exception))
        with self.assertRaises(TypeError):
            reject_unsupported_layout(True)


if __name__ == "__main__":
    unittest.main()
