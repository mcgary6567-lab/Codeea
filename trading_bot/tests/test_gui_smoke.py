"""Runs the headless GUI-drawing smoke harness in a subprocess.

The harness installs a tkinter stub, so it must run in its own interpreter to
avoid leaking that stub into the rest of the suite. This test just asserts the
harness drew the candles/curves without raising. Skipped only if Python can't
spawn a subprocess (it always can in normal CI/dev)."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

HARNESS = os.path.join(os.path.dirname(__file__), "gui_smoke_harness.py")


class TestGuiSmoke(unittest.TestCase):
    def test_canvas_windows_draw_without_error(self):
        r = subprocess.run([sys.executable, HARNESS], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0,
                         msg=f"harness failed:\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}")
        self.assertIn("SMOKE OK", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
