"""Focused tests for the #16/#18/#19 -> gate evidence adapter.

These never touch Minecraft: they pin the lossless projection rules and the
fail-closed negatives the reviews require - the #18 event schema (including
the object ``operator``), lifecycle/open-session validation, request/attempt
reuse, ``capturedPath`` proof, duplicate ``call_id`` detection, explicit
instance provenance and verified-vs-candidate joins.

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


def audit_fixture() -> list[dict]:
    return [
        {"seq": 1, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "session_start"},
        {"seq": 2, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "audit_ready"},
        {"seq": 3, "tick": 10, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "init", "type": "phase", "from": "bootstrap", "to": "init"},
        {"seq": 4, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "aaaa", "name": "Bot"}},
        {"seq": 5, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
        {"seq": 6, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "targetInput": True, "agentOp": True, "requestSeq": 5, "attemptSeq": 4, "note": 0, "instrument": "harp"},
        {"seq": 7, "tick": 30, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_tracked", "uuid": "cart-1", "epoch": 1, "inventory": []},
        {"seq": 8, "tick": 40, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_exit", "uuid": "cart-1", "epoch": 1, "pos": [2.5, 60.0, 0.5]},
        {"seq": 9, "tick": 80, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_remove", "uuid": "cart-1", "epoch": 1, "reason": "DISCARDED", "capturedPath": "minecart_container_remove_before_drop", "inventory": [{"slot": 0, "item": "minecraft:stone", "count": 3}]},
        {"seq": 10, "tick": 90, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_end", "status": "complete", "truncated": False},
    ]


def trajectory_fixture() -> list[dict]:
    return [
        {"record": "call", "call_id": "c1", "tool": "bash", "arguments": {"command": "status"}, "at": "2026-09-26T00:00:00Z", "phase": "prepare", "actor": "operator", "instance": "exp-1"},
        {"record": "result", "call_id": "c1", "status": "ok", "result": {"text": "up"}, "at": "2026-09-26T00:00:01Z"},
        {"record": "call", "call_id": "c2", "tool": "write", "arguments": {"path": "x"}, "at": "2026-09-26T00:00:02Z", "phase": "prepare", "actor": "operator", "instance": "src-1"},
        {"record": "result", "call_id": "c2", "status": "ok", "result": "written", "at": "2026-09-26T00:00:03Z"},
        {"record": "call", "call_id": "c3", "tool": "git", "arguments": {"args": ["rev-parse", "HEAD"]}, "at": "2026-09-26T00:00:04Z", "phase": "prepare", "actor": "operator", "instance": "src-1"},
        {"record": "result", "call_id": "c3", "status": "ok", "result": "abc", "at": "2026-09-26T00:00:05Z"},
        {"record": "call", "call_id": "c4", "tool": "mcp_probe", "arguments": {"tool": "mc_state"}, "at": "2026-09-26T00:00:06Z", "phase": "prepare", "actor": "operator", "instance": "exp-1"},
        {"record": "result", "call_id": "c4", "status": "ok", "result": {"tick": 21}, "at": "2026-09-26T00:00:07Z"},
    ]


def map_audit(events: list[dict] | None = None):
    return EVIDENCE.map_audit_events(
        events if events is not None else audit_fixture(),
        run_id="r1",
        instance_id="exp-1",
        dimension="minecraft:overworld",
        source_sha256="0" * 64,
    )


def map_trace(trajectory: list[dict], *, joins=None, audit=None):
    return EVIDENCE.map_trajectory(
        trajectory,
        run_id="r1",
        instance_id="exp-1",
        instance_ids=["src-1"],
        joins=joins,
        audit_events=audit if audit is not None else map_audit()[0],
    )


class AuditMappingTests(unittest.TestCase):
    def test_operator_object_and_promoted_links(self):
        rows, gaps, _, _, lifecycle = map_audit()
        self.assertEqual(gaps, [])
        processed = next(row for row in rows if row["event"] == "input_processed")
        self.assertEqual(processed["actor_uuid"], "aaaa")
        self.assertEqual(processed["phase"], "agent")
        self.assertEqual(processed["request_seq"], 5)
        self.assertEqual(processed["attempt_seq"], 4)
        self.assertIs(processed["agent_op"], True)
        self.assertEqual(processed["operator_name"], "Bot")
        self.assertEqual(lifecycle["end_statuses"], ["complete"])

    def test_session_meta_is_validated_not_dropped_silently(self):
        _, _, _, excluded, lifecycle = map_audit()
        self.assertEqual(excluded, {"session_start": 1, "audit_ready": 1, "audit_end": 1})
        self.assertEqual(lifecycle["open_sessions"], [])
        self.assertEqual(lifecycle["incomplete"], [])

    def test_open_session_is_a_gap(self):
        rows = [row for row in audit_fixture() if row.get("type") != "audit_end"]
        _, gaps, _, _, lifecycle = map_audit(rows)
        self.assertTrue(any(gap.code == "session_open" for gap in gaps))
        self.assertEqual(lifecycle["open_sessions"], ["s1"])

    def test_audit_incomplete_and_truncated_are_gaps(self):
        incomplete = audit_fixture() + [
            {"seq": 11, "tick": 91, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_incomplete", "reason": "hook error"}
        ]
        _, gaps, _, _, _ = map_audit(incomplete)
        self.assertTrue(any(gap.code == "audit_incomplete" for gap in gaps))
        truncated = audit_fixture()
        truncated[-1]["truncated"] = True
        _, gaps, _, _, _ = map_audit(truncated)
        self.assertTrue(any(gap.code == "audit_incomplete" for gap in gaps))

    def test_request_and_attempt_reuse_are_gaps(self):
        rows = audit_fixture() + [
            {"seq": 11, "tick": 22, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "requestSeq": 5, "attemptSeq": 4, "agentOp": True}
        ]
        _, gaps, _, _, _ = map_audit(rows)
        self.assertTrue(any(gap.code == "request_reused" for gap in gaps))
        self.assertTrue(any(gap.code == "attempt_reused" for gap in gaps))

    def test_cross_session_reference_is_a_scope_gap(self):
        rows = [
            {"seq": 1, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "session_start"},
            {"seq": 2, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "audit_ready"},
            {"seq": 3, "tick": 10, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "aaaa", "name": "Bot"}},
            {"seq": 4, "tick": 10, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
            {"seq": 5, "tick": 11, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_end", "status": "complete"},
            {"seq": 6, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "ready", "type": "session_start"},
            {"seq": 7, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "ready", "type": "audit_ready"},
            {"seq": 8, "tick": 12, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "requestSeq": 4, "attemptSeq": 3, "agentOp": True},
            {"seq": 9, "tick": 13, "run": "r1", "inst": "exp-1", "session": "s2", "phase": "end", "type": "audit_end", "status": "complete"},
        ]
        _, gaps, _, _, _ = map_audit(rows)
        self.assertTrue(any(gap.code == "request_scope" for gap in gaps))
        self.assertTrue(any(gap.code == "attempt_scope" for gap in gaps))

    def test_forward_reference_is_a_gap(self):
        rows = audit_fixture() + [
            {"seq": 12, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
            {"seq": 13, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "bbbb", "name": "Bot2"}},
        ]
        rows.append({"seq": 11, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "bbbb", "name": "Bot2"}, "requestSeq": 12, "attemptSeq": 13, "agentOp": True})
        _, gaps, _, _, _ = map_audit(rows)
        self.assertTrue(any(gap.code == "request_forward" for gap in gaps))
        self.assertTrue(any(gap.code == "attempt_forward" for gap in gaps))

    def test_tick_order_forward_reference_is_a_gap(self):
        rows = audit_fixture()
        next(row for row in rows if row["type"] == "input_request")["tick"] = 99
        _, gaps, _, _, _ = map_audit(rows)
        self.assertTrue(any(gap.code == "request_forward" for gap in gaps))

    def test_referenced_identity_must_match(self):
        rows = audit_fixture()
        next(row for row in rows if row["type"] == "input_attempt")["inst"] = "other-lab"
        _, gaps, _, _, _ = map_audit(rows)
        self.assertTrue(any(gap.code == "attempt_scope" for gap in gaps))

    def test_attempt_request_link_is_promoted_and_checked(self):
        rows = audit_fixture() + [
            {"seq": 11, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
            {"seq": 12, "tick": 22, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "bbbb", "name": "Bot2"}, "requestSeq": 11},
        ]
        mapped, gaps, _, _, _ = map_audit(rows)
        self.assertEqual(gaps, [])
        attempt_row = next(row for row in mapped if row["event"] == "input_attempt" and row["detail"]["raw"].get("requestSeq") == 11)
        self.assertEqual(attempt_row.get("request_seq"), 11)
        forward = audit_fixture() + [
            {"seq": 12, "tick": 22, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "bbbb", "name": "Bot2"}, "requestSeq": 13},
            {"seq": 13, "tick": 23, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
        ]
        _, gaps, _, _, _ = map_audit(forward)
        self.assertTrue(any(gap.code == "request_forward" for gap in gaps))

    def test_dangling_and_missing_attempt_are_gaps(self):
        dangling = audit_fixture()
        next(row for row in dangling if row["type"] == "input_processed")["attemptSeq"] = 999
        _, gaps, _, _, _ = map_audit(dangling)
        self.assertTrue(any(gap.code == "attempt_missing" for gap in gaps))
        missing = audit_fixture()
        next(row for row in missing if row["type"] == "input_processed").pop("attemptSeq")
        _, gaps, _, _, _ = map_audit(missing)
        self.assertTrue(any(gap.code == "agent_op_without_attempt" for gap in gaps))

    def test_captured_path_is_required_and_authoritative(self):
        missing = audit_fixture()
        next(row for row in missing if row["type"] == "cart_remove").pop("capturedPath")
        _, gaps, _, _, _ = map_audit(missing)
        self.assertTrue(any(gap.code == "captured_path_missing" for gap in gaps))
        after = audit_fixture()
        next(row for row in after if row["type"] == "cart_remove")["capturedPath"] = "minecart_container_remove_after_drop"
        rows, gaps, _, _, _ = map_audit(after)
        self.assertEqual(gaps, [])
        emitted = next(row for row in rows if row["event"] == "cart_emitted")
        self.assertFalse(emitted["captured_before_removal"])

    def test_missing_epoch_is_a_gap(self):
        rows = audit_fixture()
        next(row for row in rows if row["type"] == "cart_exit").pop("epoch")
        _, gaps, _, _, _ = map_audit(rows)
        self.assertTrue(any(gap.code == "missing_epoch" for gap in gaps))

    def test_foreign_run_and_null_actor(self):
        foreign = audit_fixture()
        foreign[3]["run"] = "other"
        _, gaps, _, _, _ = map_audit(foreign)
        self.assertTrue(any(gap.code == "run_mismatch" for gap in gaps))
        null_actor = audit_fixture()
        null_actor[3]["operator"] = None
        rows, _, _, _, _ = map_audit(null_actor)
        attempt = next(row for row in rows if row["event"] == "input_attempt")
        self.assertNotIn("actor_uuid", attempt)

    def test_raw_event_and_source_hash_are_kept(self):
        rows, _, _, _, _ = map_audit()
        processed = next(row for row in rows if row["event"] == "input_processed")
        self.assertEqual(processed["detail"]["raw"]["type"], "input_processed")
        self.assertEqual(processed["detail"]["source_sha256"], "0" * 64)


class TraceMappingTests(unittest.TestCase):
    def test_calls_pair_and_keep_explicit_instances(self):
        rows, join_doc, gaps, candidates = map_trace(trajectory_fixture())
        self.assertEqual(gaps, [])
        self.assertEqual({row["call_id"] for row in rows}, {"c1", "c2", "c3", "c4"})
        self.assertEqual({row["instance_id"] for row in rows}, {"exp-1", "src-1"})
        self.assertIsNone(join_doc)
        self.assertEqual(candidates, [])

    def test_duplicate_call_id_is_a_gap_not_last_wins(self):
        rows, _, gaps, _ = map_trace(trajectory_fixture() + [dict(trajectory_fixture()[0])])
        self.assertTrue(any(gap.code == "duplicate_call_id" for gap in gaps))

    def test_missing_and_orphan_results_are_gaps(self):
        missing = [row for row in trajectory_fixture() if not (row.get("record") == "result" and row.get("call_id") == "c1")]
        _, _, gaps, _ = map_trace(missing)
        self.assertTrue(any(gap.code == "missing_result" for gap in gaps))
        orphan = trajectory_fixture() + [{"record": "result", "call_id": "c9", "status": "ok", "at": "2026-09-26T00:00:09Z"}]
        _, _, gaps, _ = map_trace(orphan)
        self.assertTrue(any(gap.code == "orphan_result" for gap in gaps))

    def test_missing_instance_needs_an_explicit_default(self):
        rows = trajectory_fixture()
        for row in rows:
            if row.get("record") == "call":
                row.pop("instance")
        _, _, gaps, _ = map_trace(rows)
        self.assertTrue(any(gap.code == "missing_instance" for gap in gaps))
        single = EVIDENCE.map_trajectory(
            rows, run_id="r1", instance_id="exp-1", instance_ids=None, joins=None, audit_events=map_audit()[0]
        )
        mapped, _, single_gaps, _ = single
        self.assertEqual(single_gaps, [])
        self.assertEqual(
            {row["detail"]["instance_source"] for row in mapped},
            {"single-declared-instance-default"},
        )

    def test_run_id_mismatch_is_a_gap(self):
        rows = trajectory_fixture()
        rows[0]["run_id"] = "other"
        _, _, gaps, _ = map_trace(rows)
        self.assertTrue(any(gap.code == "run_id_mismatch" for gap in gaps))

    def test_unproven_join_stays_a_candidate(self):
        unproven = [{"call_id": "c4", "run_id": "r1", "instance_id": "exp-1", "dimension": "minecraft:overworld", "audit_ref": {"event_id": "s1:6", "tick": 21}, "basis": "nearest call"}]
        _, join_doc, gaps, candidates = map_trace(trajectory_fixture(), joins=unproven)
        self.assertEqual(gaps, [])
        self.assertIsNone(join_doc)
        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0]["verified"])
        self.assertTrue(any("no explicit proof" in problem for problem in candidates[0]["problems"]))

    def test_proven_join_is_verified_and_counts_are_computed(self):
        proven = [{
            "call_id": "c4",
            "run_id": "r1",
            "instance_id": "exp-1",
            "dimension": "minecraft:overworld",
            "audit_ref": {"event_id": "s1:6", "tick": 21},
            "proof": {"producer": "integration driver", "basis": "requestSeq 5 -> attemptSeq 4", "clock": "tick 21"},
        }]
        _, join_doc, gaps, candidates = map_trace(trajectory_fixture(), joins=proven)
        self.assertEqual(gaps, [])
        self.assertIsNotNone(join_doc)
        self.assertEqual(len(join_doc["joins"]), 1)
        self.assertEqual(join_doc["unmatched_agent_events"], 5)
        self.assertEqual(candidates, [])

    def test_unresolved_join_event_is_a_candidate(self):
        bogus = [{"call_id": "c4", "audit_ref": {"tick": 21, "event_id": "nope"}, "proof": {"producer": "x", "basis": "y", "clock": "z"}}]
        _, join_doc, _, candidates = map_trace(trajectory_fixture(), joins=bogus)
        self.assertIsNone(join_doc)
        self.assertEqual(len(candidates), 1)


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


class RestoreCollectionTests(unittest.TestCase):
    def test_bridge6_evidence_collects_and_verifies(self):
        source = Path("F:/mc-agent-worktrees/rom13/bridge6/labs/evidence")
        if not source.is_dir():
            self.skipTest("bridge6 evidence is not checked out")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "collection"
            report = EVIDENCE.collect_restore_evidence(source, out)
            self.assertTrue(report["ok"], report.get("problems"))
            self.assertEqual(report["restoreComparison"]["problems"], [])
            self.assertEqual(report["sourceUnchanged"]["problems"], [])
            self.assertTrue(report["failureEvidence"]["wrongEndpointRejected"])
            self.assertTrue(report["failureEvidence"]["duplicatePreExistingRejected"])
            self.assertTrue((out / "provenance.json").is_file())
            self.assertFalse(report["gateCompatibility"]["sameRunBinding"])


class AssembleTests(unittest.TestCase):
    def test_assemble_indexes_canonical_artifacts_and_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            rows, gaps, _, _, _ = map_audit()
            self.assertEqual(gaps, [])
            EVIDENCE.write_jsonl(bundle / EVIDENCE.CANONICAL["audit_events"], rows)
            spec = {
                "run": {
                    "run_id": "r1",
                    "issue": "guajun/mc-agent#14",
                    "allowed_port_ranges": ["27240-27249"],
                    "child_runs": [{"run_id": f"init-{i}", "instance_id": "exp-1"} for i in range(1, 4)],
                    "source_world": {"label": "x", "path": str(Path(tmp) / "world"), "before_tree_sha256": "1" * 64, "after_tree_sha256": "1" * 64},
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
            self.assertNotIn(EVIDENCE.CANONICAL["evidence_index"], paths)


if __name__ == "__main__":
    unittest.main()
