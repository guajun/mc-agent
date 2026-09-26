"""Unit regressions for the same-window attack->use probe analysis.

The probe's pure ``analyse`` function must accept a back-to-back pair, and
must fail closed on the exact defects the round-3 review named: a request gap
outside the correlation window, a stale attack-attributed processed event and
a doubly counted use.
"""

from __future__ import annotations

import importlib.util
import sys
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


PROBE = load_module("attack_use_window_probe", "tools/attack_use_window_probe.py")


def base_events(gap_ticks: int = 0, *, stale_attack_processed: bool = False, double_use: bool = False):
    attack_tick = 100
    use_tick = 100 + gap_ticks
    return [
        {"seq": 10, "tick": attack_tick, "type": "input_request", "pos": {"x": 0, "y": -59, "z": 0}, "trigger": "player"},
        {"seq": 11, "tick": attack_tick, "type": "input_attempt", "path": "attack", "requestSeq": 10, "pos": {"x": 0, "y": -59, "z": 0}},
        {"seq": 12, "tick": use_tick, "type": "input_attempt", "path": "useItemOn", "requestSeq": None, "pos": {"x": 0, "y": -59, "z": 0}},
        {"seq": 13, "tick": use_tick, "type": "input_request", "pos": {"x": 0, "y": -59, "z": 0}, "trigger": "player"},
        {"seq": 14, "tick": use_tick, "type": "input_attempt", "path": "useWithoutItem", "requestSeq": 13, "pos": {"x": 0, "y": -59, "z": 0}},
        {"seq": 15, "tick": use_tick + 1, "type": "input_processed", "path": "playNote", "attemptSeq": 14, "requestSeq": 13, "targetInput": True},
    ] + (
        [{"seq": 16, "tick": attack_tick + 1, "type": "input_processed", "path": "playNote", "attemptSeq": 11, "requestSeq": 10, "targetInput": True}]
        if stale_attack_processed
        else []
    ) + (
        [{"seq": 17, "tick": use_tick + 1, "type": "input_processed", "path": "playNote", "attemptSeq": 14, "requestSeq": 13, "targetInput": True}]
        if double_use
        else []
    )


class AnalyseTests(unittest.TestCase):
    def test_within_window_single_use_is_ok(self) -> None:
        analysis = PROBE.analyse(base_events(gap_ticks=0), correlation_window=2)
        self.assertTrue(analysis["withinWindow"])
        self.assertEqual(analysis["requestGapTicks"], 0)
        self.assertTrue(analysis["exactlyOneUseProcessed"])
        self.assertTrue(analysis["noStaleAttackAttribution"])
        self.assertTrue(analysis["ok"])

    def test_one_tick_gap_is_still_inside_the_window(self) -> None:
        analysis = PROBE.analyse(base_events(gap_ticks=1), correlation_window=2)
        self.assertEqual(analysis["requestGapTicks"], 1)
        self.assertTrue(analysis["ok"])

    def test_fourteen_tick_gap_is_outside_the_window(self) -> None:
        analysis = PROBE.analyse(base_events(gap_ticks=14), correlation_window=2)
        self.assertFalse(analysis["withinWindow"])
        self.assertFalse(analysis["ok"])
        self.assertEqual(analysis["requestGapTicks"], 14)

    def test_stale_attack_processed_fails_closed(self) -> None:
        analysis = PROBE.analyse(base_events(stale_attack_processed=True), correlation_window=2)
        self.assertFalse(analysis["noStaleAttackAttribution"])
        self.assertFalse(analysis["ok"])
        self.assertEqual(len(analysis["attackProcessed"]), 1)

    def test_use_request_follows_the_processed_and_use_without_item_link(self) -> None:
        events = base_events(gap_ticks=2)
        # The useItemOn attempt can still point at the attack request (the use
        # playNote has not happened yet); the processed event names the real
        # use request two ticks later.
        events[2]["requestSeq"] = 10
        analysis = PROBE.analyse(events, correlation_window=2)
        self.assertEqual(analysis["useRequestSeq"], 13)
        self.assertEqual(analysis["requestGapTicks"], 2)
        self.assertTrue(analysis["withinWindow"])
        self.assertTrue(analysis["ok"])

    def test_second_attack_request_cannot_validate_itself_as_use(self) -> None:
        events = base_events(gap_ticks=1)
        for row in events[2:]:
            row["seq"] += 2
            if row.get("requestSeq") == 13:
                row["requestSeq"] = 15
            if row.get("attemptSeq") == 14:
                row["attemptSeq"] = 16
        events[2:2] = [
            {"seq": 12, "tick": 100, "type": "input_request", "pos": {"x": 0, "y": -59, "z": 0}},
            {"seq": 13, "tick": 100, "type": "input_attempt", "path": "attack", "requestSeq": 12},
        ]
        events[-1].update(requestSeq=12, attemptSeq=14)
        result = PROBE.analyse(events)
        self.assertFalse(result["noStaleAttackAttribution"])
        self.assertFalse(result["exactlyOneUseProcessed"])
        self.assertFalse(result["ok"])

    def test_processed_requires_the_use_without_item_attempt(self) -> None:
        events = base_events(gap_ticks=1)
        events[-1]["attemptSeq"] = 12
        self.assertFalse(PROBE.analyse(events)["ok"])

    def test_double_counted_use_fails_closed(self) -> None:
        analysis = PROBE.analyse(base_events(double_use=True), correlation_window=2)
        self.assertFalse(analysis["exactlyOneUseProcessed"])
        self.assertFalse(analysis["ok"])


class DriverProvenanceTests(unittest.TestCase):
    def test_probe_driver_is_committed_and_reports_its_own_hash(self) -> None:
        provenance = PROBE.driver_provenance()
        self.assertTrue(provenance["head"])
        self.assertEqual(len(provenance["actual_sha256"]), 64)
        self.assertEqual(provenance["actual_sha256"], PROBE.sha256_file(ROOT / "tools/attack_use_window_probe.py"))
        # Committed after the driver-freeze commit: the blob must match modulo
        # line endings, exactly like the full-gate provenance contract.
        self.assertTrue(provenance["normalized_matches_git_blob"])


if __name__ == "__main__":
    unittest.main()
