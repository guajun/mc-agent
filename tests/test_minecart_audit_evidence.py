"""Regression for the minecart-audit evidence exporter.

The exporter attaches the bridge fixture to an attestation only when the
source child run passed.  A closed but failed (or incomplete, or missing)
verdict must refuse the export instead of silently attesting it.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "tests" / "mods" / "minecart-audit" / "stage1_evidence.py"


def _load():
    spec = importlib.util.spec_from_file_location("minecart_audit_evidence", EXPORTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PassingChildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load()

    def test_pass_is_accepted(self) -> None:
        self.module.require_passing_child("pass", "run-1")

    def test_fail_refuses_the_export(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.module.require_passing_child("fail", "run-1")
        self.assertIn("passing child run is required", str(caught.exception))

    def test_incomplete_refuses_the_export(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self.module.require_passing_child("incomplete", "run-1")
        self.assertIn("passing child run is required", str(caught.exception))

    def test_missing_verdict_refuses_the_export(self) -> None:
        with self.assertRaises(SystemExit):
            self.module.require_passing_child(None, "run-1")


if __name__ == "__main__":
    unittest.main()
