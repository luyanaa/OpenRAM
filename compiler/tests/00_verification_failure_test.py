#!/usr/bin/env python3
# See LICENSE for licensing information.
#
# Copyright (c) 2016-2026 Regents of the University of California and The Board
# of Regents for the Oklahoma Agricultural and Mechanical College
# (acting for and on behalf of Oklahoma State University)
# All rights reserved.
#
"""Requested physical checks must fail closed, independently of scheduling flags."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("OPENRAM_HOME", str(REPO / "compiler"))
os.environ.setdefault("OPENRAM_TECH", str(REPO / "technology"))
import common
common.make_openram_package()
import openram


class VerificationFailureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="openram_verify_policy_")
        root = Path(cls.directory.name)
        tech = openram.OPTS.tech_name or "freepdk45"
        config = root / "config.py"
        config.write_text(
            "tech_name = {!r}\nword_size = 1\nnum_words = 16\n"
            "check_lvsdrc = False\nuse_nix = False\n"
            "nominal_corner_only = True\noutput_path = {!r}\n".format(
                tech, str(root) + os.sep))
        openram.init_openram(str(config), is_unit_test=True)
        from openram.base.hierarchy_design import hierarchy_design
        cls.design_type = hierarchy_design

    @classmethod
    def tearDownClass(cls):
        try:
            openram.end_openram()
        finally:
            cls.directory.cleanup()

    def probe(self, stack, *, inline=False, enabled=True, unit_test=False,
              netlist_only=False, drc=0, lvs=0):
        from openram import verify
        for name, value in {"inline_lvsdrc": inline, "check_lvsdrc": enabled,
                            "is_unit_test": unit_test,
                            "netlist_only": netlist_only}.items():
            stack.enter_context(patch.object(openram.OPTS, name, value))
        probe = object.__new__(self.design_type)
        probe.name = probe.cell_name = "verification_probe"
        probe.drc_errors = probe.lvs_errors = "skipped"
        stack.enter_context(patch.object(probe, "sp_write"))
        stack.enter_context(patch.object(probe, "gds_write"))
        stack.enter_context(patch.object(verify, "run_drc", return_value=drc))
        stack.enter_context(patch.object(verify, "run_lvs", return_value=lvs))
        return probe

    def test_requested_checks_reject_violations_and_tool_errors(self):
        requests = ({"force_check": True},
                    {"final_verification": True},
                    {"force_check": True, "inline": True})
        for request in requests:
            for drc, lvs in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                with self.subTest(request=request, drc=drc, lvs=lvs):
                    with contextlib.ExitStack() as stack:
                        options = dict(request)
                        probe = self.probe(stack, inline=options.pop("inline", False),
                                           drc=drc, lvs=lvs)
                        stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                        with self.assertRaises(AssertionError):
                            probe.DRC_LVS(**options)
                        # Preserve both reports even when the first check fails.
                        self.assertEqual((probe.drc_errors, probe.lvs_errors), (drc, lvs))

    def test_disabled_and_netlist_only_requests_remain_skipped(self):
        for settings, request in (({"enabled": False}, {}),
                                  ({"unit_test": True}, {}),
                                  ({"netlist_only": True}, {"force_check": True})):
            with self.subTest(settings=settings):
                with contextlib.ExitStack() as stack:
                    probe = self.probe(stack, drc=1, lvs=1, **settings)
                    probe.DRC_LVS(**request)
                    self.assertEqual((probe.drc_errors, probe.lvs_errors),
                                     ("skipped", "skipped"))

    def test_forced_success_records_clean_results(self):
        with contextlib.ExitStack() as stack:
            probe = self.probe(stack, unit_test=True)
            probe.DRC_LVS(force_check=True)
            self.assertEqual((probe.drc_errors, probe.lvs_errors), (0, 0))

    def test_klayout_rejects_stale_reports_and_failed_processes(self):
        from openram.verify import klayout

        passed = "LVSDB\nZ(\n X(1 1 1\n  Z(\n   N(1 1 1)\n  )\n )\n)\n"
        for fresh_report, exitcode in ((False, 7), (True, 7), (False, 0)):
            with self.subTest(fresh_report=fresh_report, exitcode=exitcode):
                with tempfile.TemporaryDirectory(prefix="openram_failed_tool_") as directory:
                    root = Path(directory)
                    report = root / "probe.lvs.report"
                    report.write_text(passed)
                    executable = root / "klayout"
                    executable.write_text(
                        "#!{}\nfrom pathlib import Path\nimport sys\n"
                        "{}\nprint('verification did not complete', file=sys.stderr)\n"
                        "sys.exit({})\n".format(
                            sys.executable,
                            "Path('probe.lvs.report').write_text({!r})".format(passed)
                            if fresh_report else "",
                            exitcode))
                    executable.chmod(0o755)
                    with patch.object(openram.OPTS, "openram_temp", str(root) + os.sep), \
                         patch.object(openram.OPTS, "lvs_exe", ("klayout", str(executable))), \
                         contextlib.redirect_stderr(io.StringIO()):
                        errors = klayout.run_lvs("probe", "probe.gds", "probe.sp",
                                                 final_verification=True)
                    self.assertNotEqual(errors, 0)


if __name__ == "__main__":
    openram.parse_args()
    del sys.argv[1:]
    unittest.main()
