"""Provenance regressions for the frozen full-gate run.

The reviewer requirement: the evidence must name the source commit that
actually ran, the driver bytes must match the committed Git blob (CRLF
normalized), and a stale head/file must fail loudly instead of being written
into a summary.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


FULLGATE = load_module("stage1_fullgate_provenance", "tools/stage1_fullgate.py")
SUMMARY = load_module("stage1_fullgate_summary", "tools/stage1_fullgate_summary.py")


class SourceProvenanceTests(unittest.TestCase):
    def test_head_and_byte_hashes_match_git(self) -> None:
        import hashlib

        provenance = FULLGATE.source_provenance()
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(provenance["head"], head)
        self.assertTrue(provenance["head"])
        entry = provenance["files"]["tools/stage1_fullgate.py"]
        actual = (ROOT / "tools/stage1_fullgate.py").read_bytes()
        blob = subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-p", "HEAD:tools/stage1_fullgate.py"],
            capture_output=True,
        ).stdout
        self.assertEqual(entry["actual_sha256"], hashlib.sha256(actual).hexdigest())
        self.assertEqual(entry["git_blob_sha256"], hashlib.sha256(blob).hexdigest())
        self.assertEqual(entry["bytes"], len(actual))
        # The normalization check is exact: it is true iff the working tree
        # matches the blob modulo line endings, so it works on a clean CI
        # checkout and on a deliberately modified worktree alike.
        dirty_for_file = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain", "--", "tools/stage1_fullgate.py"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(entry["normalized_matches_git_blob"], not dirty_for_file)

    def test_dirty_worktree_is_refused(self) -> None:
        original = FULLGATE.source_provenance
        try:
            FULLGATE.source_provenance = lambda: {
                "head": "a" * 40,
                "branch": "b",
                "dirty": [" M tools/stage1_fullgate.py"],
                "clean": False,
                "files": {"tools/stage1_fullgate.py": {"normalized_matches_git_blob": True}},
                "jars": {},
                "lineEndingConvention": "LF",
                "capturedAt": "now",
            }
            with self.assertRaises(RuntimeError):
                FULLGATE.require_clean_source("live")
        finally:
            FULLGATE.source_provenance = original

    def test_uncommitted_driver_bytes_are_refused(self) -> None:
        original = FULLGATE.source_provenance
        try:
            FULLGATE.source_provenance = lambda: {
                "head": "a" * 40,
                "branch": "b",
                "dirty": [],
                "clean": True,
                "files": {"tools/stage1_fullgate.py": {"normalized_matches_git_blob": False}},
                "jars": {},
                "lineEndingConvention": "LF",
                "capturedAt": "now",
            }
            with self.assertRaises(RuntimeError):
                FULLGATE.require_clean_source("live")
        finally:
            FULLGATE.source_provenance = original

    def test_phase_close_detects_a_changed_file(self) -> None:
        before = FULLGATE.source_provenance()
        original = FULLGATE.source_provenance
        try:
            changed = json.loads(json.dumps(before))
            changed["files"]["tools/stage1_fullgate.py"]["actual_sha256"] = "0" * 64
            FULLGATE.source_provenance = lambda: changed
            with self.assertRaises(RuntimeError):
                FULLGATE.close_phase("test-phase", before)
        finally:
            FULLGATE.source_provenance = original


class SummaryProvenanceTests(unittest.TestCase):
    def test_summary_generator_has_no_hardcoded_commit(self) -> None:
        text = (ROOT / "tools/stage1_fullgate_summary.py").read_text(encoding="utf-8")
        import re

        self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", text))

    def test_stale_run_or_version_lock_head_is_refused(self) -> None:
        original_evidence = SUMMARY.EVIDENCE
        original_bundle = SUMMARY.BUNDLE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                evidence = base / "evidence"
                bundle = evidence / "bundle"
                (base / "run").mkdir(parents=True)
                version_dir = bundle / "artifacts" / "smoke_fixture_validity"
                version_dir.mkdir(parents=True)
                head = "a" * 40
                (base / "run" / "run.json").write_text(
                    json.dumps({"repo": {"commit": "b" * 40, "dirty_entries": 0}}), encoding="utf-8"
                )
                (version_dir / "version-lock.json").write_text(
                    json.dumps({"components": [{"name": "mc-agent", "commit": head}]}), encoding="utf-8"
                )
                SUMMARY.EVIDENCE = evidence
                SUMMARY.BUNDLE = bundle
                facts = {"runDir": str(base / "run"), "driver": {"sha256": "c" * 64}}
                provenance = {
                    "sourceCommit": head,
                    "driver": {
                        "actualSha256": "c" * 64,
                        "normalizedMatchesGitBlob": True,
                    },
                    "files": {"tools/stage1_fullgate.py": {"actual_sha256": "c" * 64}},
                    "phases": {"live": {"files": {"tools/stage1_fullgate.py": {"actual_sha256": "c" * 64}}}},
                }
                with self.assertRaises(SystemExit):
                    SUMMARY.assert_consistency(facts, provenance)
        finally:
            SUMMARY.EVIDENCE = original_evidence
            SUMMARY.BUNDLE = original_bundle


if __name__ == "__main__":
    unittest.main()
