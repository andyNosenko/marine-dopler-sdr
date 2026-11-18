#!/usr/bin/env python3
import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP = os.path.join(ROOT, "app")


class CalibrateCheckMockTests(unittest.TestCase):
    def _run(self, *args):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [APP, ROOT, env.get("PYTHONPATH", "")])
        return subprocess.run(
            [sys.executable, os.path.join(APP, "calibrate_check.py"), *args],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)

    def test_pilot_check_runs_without_rtlsdr(self):
        result = self._run("--mock")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Pilot SNR:", result.stdout)
        self.assertIn("Норма", result.stdout)

    def test_offset_calibration_runs_without_rtlsdr(self):
        result = self._run("offset", "45", "--mock")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("RDF_CAL_OFFSET =", result.stdout)


if __name__ == "__main__":
    unittest.main()
