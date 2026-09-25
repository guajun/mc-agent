"""Focused tests for the #16/#18/#19 -> gate evidence adapter.

These never touch Minecraft: they pin the lossless projection rules that the
live integration depends on - the #18 event schema (including the object
``operator``), session-meta exclusion, explicit absent-actor provenance, #19
call/result pairing and join arithmetic, and the #16 identity extraction.

    python -m unittest discover -s tests -v
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


EVIDENCE = load_module("stage1_evidence", "tools/stage1_evidence.py")
GATE = load_module("stage1_gate", "tools/stage1_gate.py")


def audit_fixture() -> list[dict]:
    return [
        {"seq": 1, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "session_start"},
        {"seq": 2, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "audit_ready"},
        {"seq": 3, "tick": 10, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "init", "type": "phase", "from": "bootstrap", "to": "init"},
        {"seq": 4, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "aaaa", "name": "Bot"}},
        {"seq": 5, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "targetInput": True, "agentOp": True},
        {"seq": 6, "tick": 30, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_exit", "uuid": "cart-1", "epoch": 1},
        {"seq": 7, "tick": 40, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_remove", "uuid": "cart-1", "epoch": 1, "reason": "DISCARDED", "inventory": [{"slot": 0, "item": "minecraft:apple", "count": 3}]},
        {"seq": 8, "tick": 40, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_end", "status": "complete"},
    ]


def trajectory_fixture() -> list[dict]:
    return [
        {"record": "call", "call_id": "c1", "tool": "bash", "arguments": {"command": "status"}, "at": "2026-09-26T00:00:00Z", "phase": "prepare", "actor": "operator", "instance": "exp-1"},
        {"record": "result", "call_id": "c1", "status": "ok", "result": {"text": "up"}, "at": "2026-09-26T00:00:01Z"},
        {"record": "call", "call_id": "c2", "tool": "write", "arguments": {"path": "x"}, "at": "2026-09-26T00:00:02Z", "phase": "prepare", "actor": "operator", "instance": "src-1"},
        {"record": "result", "call_id": "c2", "status": "ok", "result": "written", "at": "2026-09-26T00:00:03Z"},
    ]


class AuditMappingTests(unittest.TestCase):
    def map(self, rows):
        return EVIDENCE.map_audit_events(
            rows,
            run_id="r1",
            instance_id="exp-1",
            dimension="minecraft:overworld",
            source_sha256="0" * 64,
        )

    def test_operator_object_becomes_actor_uuid(self):
        rows, gaps, _, _ = self.map(audit_fixture())
        self.assertEqual(gaps, [])
        processed = next(row for row in rows if row["event"] == "input_processed")
        self.assertEqual(processed["actor_uuid"], "aaaa")
        self.assertEqual(processed["phase"], "agent")

    def test_session_meta_is_excluded_not_projected(self):
        rows, _, counts, excluded = self.map(audit_fixture())
        self.assertNotIn("session_start", {row["event"] for row in rows})
        self.assertNotIn("audit_end", {row["event"] for row in rows})
        self.assertEqual(excluded, {"session_start": 1, "audit_ready": 1, "audit_end": 1})
        self.assertEqual(counts["input_processed"], 1)

    def test_cart_exit_maps_and_capture_is_derived(self):
        rows, _, _, _ = self.map(audit_fixture())
        emitted = next(row for row in rows if row["event"] == "cart_emitted")
        removed = next(row for row in rows if row["event"] == "cart_removed")
        self.assertTrue(emitted["captured_before_removal"])
        self.assertEqual(removed["removal_reason"], "DISCARDED")
        self.assertEqual(emitted["cart_uuid"], "cart-1")

    def test_foreign_run_is_a_gap(self):
        rows = [dict(row) for row in audit_fixture()]
        rows[3]["run"] = "other"
        _, gaps, _, _ = self.map(rows)
        self.assertTrue(any(gap.code == "run_mismatch" for gap in gaps))

    def test_null_actor_without_provenance_is_withheld(self):
        rows = [dict(row) for row in audit_fixture()]
        rows[3]["operator"] = None
        mapped, _, _, _ = self.map(rows)
        attempt = next(row for row in mapped if row["event"] == "input_attempt")
        self.assertNotIn("actor_uuid", attempt)

    def test_null_actor_with_recorded_provenance_is_explicit(self):
        rows = [dict(row) for row in audit_fixture()]
        rows[3]["operator"] = None
        rows[3]["source"] = "redstone_or_environment"
        mapped, gaps, _, _ = self.map(rows)
        self.assertEqual(gaps, [])
        attempt = next(row for row in mapped if row["event"] == "input_attempt")
        self.assertIsNone(attempt["actor_uuid"])
        self.assertEqual(attempt["actor_provenance"], "redstone_or_environment")

    def test_raw_event_is_preserved(self):
        rows, _, _, _ = self.map(audit_fixture())
        processed = next(row for row in rows if row["event"] == "input_processed")
        self.assertEqual(processed["detail"]["raw"]["type"], "input_processed")
        self.assertEqual(processed["detail"]["source_sha256"], "0" * 64)


class TraceMappingTests(unittest.TestCase):
    def test_calls_pair_with_results_and_instance_is_kept(self):
        rows, join_doc, gaps = EVIDENCE.map_trajectory(
            trajectory_fixture(), run_id="r1", instance_id="exp-1", instance_ids=["src-1"], joins=None, audit_events=[]
        )
        self.assertEqual(gaps, [])
        self.assertEqual({row["call_id"] for row in rows}, {"c1", "c2"})
        self.assertEqual({row["instance_id"] for row in rows}, {"exp-1", "src-1"})
        self.assertEqual(join_doc["unmatched_tool_calls"], 2)

    def test_missing_result_is_a_gap(self):
        rows = [row for row in trajectory_fixture() if not (row.get("record") == "result" and row.get("call_id") == "c1")]
        _, _, gaps = EVIDENCE.map_trajectory(
            rows, run_id="r1", instance_id="exp-1", instance_ids=None, joins=None, audit_events=[]
        )
        self.assertTrue(any(gap.code == "missing_result" for gap in gaps))

    def test_join_must_resolve_to_a_mapped_event(self):
        audit, _, _, _ = EVIDENCE.map_audit_events(
            audit_fixture(), run_id="r1", instance_id="exp-1", dimension="minecraft:overworld", source_sha256="0" * 64
        )
        good = [{"call_id": "c1", "run_id": "r1", "instance_id": "exp-1", "dimension": "minecraft:overworld", "audit_ref": {"event_id": "s1:5", "tick": 21}}]
        _, join_doc, gaps = EVIDENCE.map_trajectory(
            trajectory_fixture(), run_id="r1", instance_id="exp-1", instance_ids=["src-1"], joins=good, audit_events=audit
        )
        self.assertEqual(gaps, [])
        self.assertEqual(join_doc["unmatched_agent_events"], 3)  # attempt, exit and remove remain
        self.assertEqual(join_doc["joins"][0]["audit_ref"]["run_id"], "r1")
        bad = [{"call_id": "c1", "audit_ref": {"event_id": "nope", "tick": 1}}]
        _, _, gaps = EVIDENCE.map_trajectory(
            trajectory_fixture(), run_id="r1", instance_id="exp-1", instance_ids=None, joins=bad, audit_events=audit
        )
        self.assertTrue(any(gap.code == "unknown_event" for gap in gaps))


class IdentityMappingTests(unittest.TestCase):
    def test_real_verified_identity_evidence_maps(self):
        source = ROOT / "docs/evidence/rom13-meta16/live-identity.json"
        if not source.is_file():
            self.skipTest("verified #16 evidence is not checked out")
        payload, gaps = EVIDENCE.map_identity(json.loads(source.read_text(encoding="utf-8")))
        self.assertEqual(gaps, [])
        cases = {row["case"] for row in payload["records"]}
        self.assertEqual(cases, {"task_bind", "hit", "miss", "two_players", "unknown_identity"})
        self.assertEqual(payload["records"][0]["uuid"], "f0a8f4ba-99f5-412a-9189-db832c934913")
        self.assertTrue(payload["pins"]["interface_mod"]["commit"])


class AssembleTests(unittest.TestCase):
    def test_assemble_indexes_canonical_artifacts_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            audit_rows, _, _, _ = EVIDENCE.map_audit_events(
                audit_fixture(), run_id="r1", instance_id="exp-1", dimension="minecraft:overworld", source_sha256="0" * 64
            )
            EVIDENCE.write_jsonl(bundle / EVIDENCE.CANONICAL["audit_events"], audit_rows)
            spec = {
                "run": {
                    "run_id": "r1",
                    "issue": "guajun/mc-agent#14",
                    "allowed_port_ranges": ["27240-27249"],
                    "child_runs": [{"run_id": f"init-{i}", "instance_id": "exp-1"} for i in range(1, 4)],
                    "source_world": {
                        "label": "x",
                        "path": str(Path(tmp) / "world"),
                        "before_tree_sha256": "1" * 64,
                        "after_tree_sha256": "1" * 64,
                    },
                    "instances": [
                        {"instance_id": "src-1", "role": "source_audit", "dimension": "minecraft:overworld", "world_dir": "labs/src/world", "rcon_port": 27240, "bridge_port": 27241},
                        {"instance_id": "exp-1", "role": "experiment", "dimension": "minecraft:overworld", "world_dir": "labs/exp/world", "rcon_port": 27242, "bridge_port": 27243},
                    ],
                }
            }
            report = EVIDENCE.assemble(bundle, spec, run_gate=True)
            self.assertIn(report["gate"]["overall"], ("blocked", "fail"))
            checks = {check["id"]: check for check in report["gate"]["checks"]}
            self.assertEqual(checks["independent_test_mod"]["status"], "blocked")
            index = json.loads((bundle / EVIDENCE.CANONICAL["evidence_index"]).read_text(encoding="utf-8"))
            paths = {entry["path"] for entry in index["entries"]}
            self.assertIn(EVIDENCE.CANONICAL["audit_events"], paths)
            # the index never pins itself
            self.assertNotIn(EVIDENCE.CANONICAL["evidence_index"], paths)


if __name__ == "__main__":
    unittest.main()
