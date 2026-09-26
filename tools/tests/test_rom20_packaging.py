#!/usr/bin/env python3
"""Tests for the review-#32 packaging hardening (offline, no host files).

The packaging tools stamp accepted-run labels; these tests prove they now
compute those labels from an actual PASS audit plus resolved reviews, and that
the trajectory derivation computes the largest result from the records.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "docs" / "evidence" / "rom20-coldstart" / "tools"


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"rom20_{name}", TOOLS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


make_manifest = load("make_manifest")
derive_trajectory = load("derive_trajectory")


def write_run_dir(
    root: Path,
    *,
    overall: str = "PASS",
    flag_status: str = "PASS",
    review_status: str = "resolved",
    omit_review: str | None = None,
) -> Path:
    run = root / "run"
    (run / "audit").mkdir(parents=True)
    flags = [{"flag": "machine_operated", "status": flag_status}]
    (run / "audit" / "audit.json").write_text(
        json.dumps({"overall": overall, "flags": flags}), encoding="utf-8")
    lines = []
    for review_id in make_manifest.REQUIRED_REVIEW_IDS:
        if review_id == omit_review:
            continue
        lines.append(json.dumps({"id": review_id, "status": review_status}))
    (run / "reviews.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run


class AuditAssertionTests(unittest.TestCase):
    def test_pass_assertion_returns_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = write_run_dir(Path(tmp))
            fact = make_manifest.assert_audit_resolved(run)
        self.assertEqual(fact["overall"], "PASS")
        self.assertEqual(fact["flag_count"], 1)
        self.assertEqual(fact["reviews_resolved"], list(make_manifest.REQUIRED_REVIEW_IDS))

    def test_pending_audit_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = write_run_dir(Path(tmp), overall="PENDING", flag_status="PENDING")
            with self.assertRaises(SystemExit) as caught:
                make_manifest.assert_audit_resolved(run)
        self.assertIn("not PASS", str(caught.exception))

    def test_unresolved_review_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = write_run_dir(Path(tmp), review_status="pending")
            with self.assertRaises(SystemExit) as caught:
                make_manifest.assert_audit_resolved(run)
        self.assertIn("not resolved", str(caught.exception))

    def test_missing_review_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = write_run_dir(Path(tmp), omit_review=make_manifest.REQUIRED_REVIEW_IDS[0])
            with self.assertRaises(SystemExit) as caught:
                make_manifest.assert_audit_resolved(run)
        self.assertIn("missing", str(caught.exception))

    def test_host_snapshot_notes_cover_the_known_stale_pins(self) -> None:
        suffixes = set(make_manifest.HOST_SNAPSHOT_NOTES)
        self.assertIn("run/audit/audit.json", suffixes)
        self.assertIn("run/audit/audit.md", suffixes)
        self.assertIn("evidence/minecart-audit-check.json", suffixes)
        products = [
            {"path": "labs/x/run/audit/audit.json", "sha256": "a"},
            {"path": "labs/x/other.json", "sha256": "b"},
        ]
        annotated = make_manifest.apply_host_snapshot_notes(products)
        self.assertIn("host_snapshot_note", annotated[0])
        self.assertNotIn("host_snapshot_note", annotated[1])


class DerivationTests(unittest.TestCase):
    def test_largest_result_is_computed_from_records(self) -> None:
        records = [
            {"result": {"text": "abc"}},
            {"result": {"text": "abcdefg"}},
            {"result": {}},
            {"result": {"text": "xy"}},
        ]
        self.assertEqual(derive_trajectory.largest_result_chars(records), 7)

    def test_largest_result_defaults_to_zero(self) -> None:
        self.assertEqual(derive_trajectory.largest_result_chars([]), 0)


if __name__ == "__main__":
    unittest.main()
