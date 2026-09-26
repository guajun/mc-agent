"""Regressions for the #18 audit exporter used by the full gate.

The reviewer findings pinned here: the canonical artifact must keep the real
server tick and the raw append sequence plus the session/wall scope (never a
``tick = index`` counter); the lifecycle artifact must be derived from the
logs and the flushed status snapshot instead of a constant list; and the
manifest attestations must come from derived facts rather than flags.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


AUDIT = load_module("minecart_audit", "tools/minecart_audit.py")


def raw_events() -> list[dict]:
    return [
        {"seq": 1, "tick": 0, "wall": 1000, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "session_start"},
        {"seq": 2, "tick": 0, "wall": 1001, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "audit_ready"},
        {"seq": 3, "tick": 10, "wall": 1100, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "init", "type": "phase", "from": "bootstrap", "to": "init"},
        {"seq": 4, "tick": 19, "wall": 1190, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "phase", "from": "init", "to": "experiment"},
        {"seq": 5, "tick": 20, "wall": 1200, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "aaaa", "name": "Bot"}, "from": [0, 0, 0], "to": [0, 0, 0]},
        {"seq": 6, "tick": 20, "wall": 1201, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
        {"seq": 7, "tick": 21, "wall": 1210, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "targetInput": True, "agentOp": True, "requestSeq": 6, "attemptSeq": 5, "note": 21, "instrument": "harp"},
        {"seq": 8, "tick": 30, "wall": 1300, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_tracked", "uuid": "cart-1", "epoch": 1, "inventory": []},
        {"seq": 9, "tick": 33, "wall": 1330, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_exit", "uuid": "cart-1", "epoch": 1, "pos": [16.5, -44.6, -22.0], "inventory": [{"slot": 0, "id": "minecraft:stone", "count": 1}]},
        {"seq": 10, "tick": 140, "wall": 1400, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_remove", "uuid": "cart-1", "epoch": 1, "reason": "DISCARDED", "pos": [23.5, -130.0, -22.0], "capturedPath": "minecart_container_remove_before_drop", "inventory": [{"slot": 0, "id": "minecraft:stone", "count": 1}]},
        {"seq": 11, "tick": 200, "wall": 2000, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "post", "type": "phase", "from": "experiment", "to": "post"},
        {"seq": 12, "tick": 201, "wall": 2001, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_end", "status": "complete", "truncated": False, "hooks": {"frame": {"totalNanos": 242999000}}},
    ]


class CanonicalRowsTests(unittest.TestCase):
    def _write(self, directory: Path, events: list[dict]) -> Path:
        path = directory / "audit-r1.jsonl"
        path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
        return path

    def test_real_tick_and_sequence_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), raw_events())
            rows = AUDIT.canonical_audit_rows(
                [{"path": str(path), "run_id": "r1", "instance_id": "exp-1", "dimension": "minecraft:overworld"}]
            )
        processed = next(row for row in rows if row["event"] == "input_processed")
        self.assertEqual(processed["tick"], 21)  # not the row index
        self.assertEqual(processed["seq"], 7)  # the raw append sequence
        self.assertEqual(processed["session"], "s1")
        self.assertEqual(processed["detail"]["wall"], 1210)
        self.assertEqual(processed["detail"]["server_tick"], 21)
        removed = next(row for row in rows if row["event"] == "cart_removed")
        self.assertEqual(removed["tick"], 140)

    def test_tick_reset_across_sessions_keeps_the_sequence_order(self) -> None:
        events = raw_events()
        events.append({"seq": 13, "tick": 5, "wall": 2100, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "ready", "type": "session_start"})
        events.append({"seq": 14, "tick": 6, "wall": 2110, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "ready", "type": "audit_ready"})
        events.append({"seq": 15, "tick": 7, "wall": 2120, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "aaaa", "name": "Bot"}, "from": [0, 0, 0], "to": [0, 0, 0]})
        events.append({"seq": 16, "tick": 8, "wall": 2130, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "targetInput": True, "agentOp": True, "requestSeq": None, "attemptSeq": 15, "note": 22, "instrument": "harp"})
        events.append({"seq": 17, "tick": 9, "wall": 2140, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "end", "type": "audit_end", "status": "complete", "truncated": False})
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), events)
            rows = AUDIT.canonical_audit_rows(
                [{"path": str(path), "run_id": "r1", "instance_id": "exp-1", "dimension": "minecraft:overworld"}]
            )
        seqs = [row["seq"] for row in rows]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(seqs[-2:], [15, 16])
        self.assertLess(rows[-1]["tick"], max(row["tick"] for row in rows[:-1]))  # a real reset is visible

    def test_non_increasing_sequence_is_refused(self) -> None:
        events = raw_events()
        events[5]["seq"] = 4
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp), events)
            with self.assertRaises(SystemExit):
                AUDIT.canonical_audit_rows(
                    [{"path": str(path), "run_id": "r1", "instance_id": "exp-1", "dimension": "minecraft:overworld"}]
                )


class LifecycleTests(unittest.TestCase):
    def _status(self, directory: Path, tail: dict) -> Path:
        path = directory / "status-r1.json"
        path.write_text(
            json.dumps(
                {
                    "run": "r1", "inst": "exp-1", "session": tail.get("session"),
                    "phase": tail.get("phase"), "seq": tail.get("seq"),
                    "events": 12, "bytes": 1, "truncated": False, "ended": True,
                    "log": str(directory / "audit-r1.jsonl"),
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_states_come_from_events_and_the_flushed_status(self) -> None:
        events = raw_events()
        with tempfile.TemporaryDirectory() as tmp:
            status = self._status(Path(tmp), events[-1])
            lifecycle = AUDIT.derive_lifecycle(
                events,
                parent_tail=events,
                status_path=status,
                flush_receipt={"call_id": "c9", "command": "mcaudit flush", "output": "mcaudit: flushed"},
            )
        self.assertEqual(
            set(lifecycle["states"]),
            {"ready", "init", "experiment_start", "experiment_end", "flush"},
        )
        self.assertEqual(lifecycle["per_instance_files"], True)
        self.assertFalse(lifecycle["ring_buffer_reliance"])
        self.assertEqual(lifecycle["evidence"]["flush"]["status_seq"], 12)

    def test_flush_without_a_receipt_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = self._status(Path(tmp), raw_events()[-1])
            with self.assertRaises(SystemExit):
                AUDIT.derive_lifecycle(raw_events(), status_path=status, flush_receipt=None)

    def test_stale_status_snapshot_is_refused(self) -> None:
        events = raw_events()
        stale = dict(events[-1])
        stale["seq"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            status = self._status(Path(tmp), stale)
            with self.assertRaises(SystemExit):
                AUDIT.derive_lifecycle(
                    events,
                    parent_tail=events,
                    status_path=status,
                    flush_receipt={"call_id": "c9", "command": "mcaudit flush", "output": "ok"},
                )


if __name__ == "__main__":
    unittest.main()
