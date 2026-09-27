"""Focused regressions for the generic stage-one integration driver.

These cover the review findings on the mapping-status projection: a reused
recorder must not duplicate summary steps, and a projection command that
exited 0 without writing its report is an INFRA failure rather than silence.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import stage1_integration as integration  # noqa: E402


class _Step:
    def __init__(self, name: str) -> None:
        self.name = name

    def as_json(self) -> dict[str, str]:
        return {"name": self.name}


class _Recorder:
    def __init__(self, *names: str) -> None:
        self.steps = [_Step(name) for name in names]


class MergedStepRowsTests(unittest.TestCase):
    def test_same_recorder_is_not_appended_twice(self) -> None:
        rec = _Recorder("normalize-a", "assemble")
        rows = integration.merged_step_rows(rec, rec)
        self.assertEqual([row["name"] for row in rows], ["normalize-a", "assemble"])

    def test_distinct_recorders_are_concatenated(self) -> None:
        primary = _Recorder("live-a")
        secondary = _Recorder("normalize-b")
        rows = integration.merged_step_rows(primary, secondary)
        self.assertEqual([row["name"] for row in rows], ["live-a", "normalize-b"])

    def test_missing_recorder_is_just_empty(self) -> None:
        self.assertEqual(integration.merged_step_rows(None, None), [])


class MappingStatusTests(unittest.TestCase):
    def _step(self, name: str, returncode: int) -> integration.StepResult:
        return integration.StepResult(
            name=name, command=["python", "tool.py", name], returncode=returncode,
            stdout="", stderr="", seconds=0.1,
        )

    def _note(self, name: str, returncode: int, report: dict | None) -> dict:
        summary: dict = {"gaps": []}
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            if report is not None:
                report_path.write_text(json.dumps(report), encoding="utf-8")
            integration.Driver._note_mapping(
                None, summary, name, self._step(name, returncode), report_path
            )
        return summary["normalizeStatus"]

    def test_rc0_missing_report_is_infra(self) -> None:
        status = self._note("normalize-trace", 0, None)
        self.assertEqual(status["mappingGaps"], [])
        self.assertEqual(len(status["infraErrors"]), 1)
        self.assertIn("no report", status["infraErrors"][0])

    def test_rc1_with_gaps_is_a_mapping_gap(self) -> None:
        status = self._note(
            "normalize-trace", 1, {"ok": False, "gaps": [{"code": "missing_result", "message": "no result"}]}
        )
        self.assertEqual(status["infraErrors"], [])
        self.assertEqual(len(status["mappingGaps"]), 1)
        self.assertIn("missing_result", status["mappingGaps"][0])

    def test_rc1_without_gaps_is_infra(self) -> None:
        status = self._note("normalize-trace", 1, {"ok": False, "gaps": []})
        self.assertEqual(status["mappingGaps"], [])
        self.assertEqual(len(status["infraErrors"]), 1)

    def test_rc0_with_clean_report_is_silent(self) -> None:
        status = self._note("normalize-trace", 0, {"ok": True, "gaps": []})
        self.assertEqual(status["mappingGaps"], [])
        self.assertEqual(status["infraErrors"], [])

    def test_rc2_is_infra(self) -> None:
        status = self._note("assemble", 2, None)
        self.assertEqual(len(status["infraErrors"]), 1)
        self.assertIn("usage/infra", status["infraErrors"][0])

    def test_assemble_blocked_is_expected(self) -> None:
        status = self._note("assemble", 3, {"gate": {"overall": "blocked"}})
        self.assertEqual(status["infraErrors"], [])
        self.assertEqual(status["gateOverall"], "blocked")
        self.assertTrue(status["commands"][0].get("expectedBlocked"))

    def test_assemble_fail_is_a_mapping_gap(self) -> None:
        status = self._note("assemble", 1, {"gate": {"overall": "fail"}})
        self.assertEqual(status["infraErrors"], [])
        self.assertIn("gate FAIL", status["mappingGaps"][0])


if __name__ == "__main__":
    unittest.main()
