#!/usr/bin/env python3
"""Unit tests for the auditable cold-start tooling.

    python -m unittest discover -s tools/tests -v

The tests build small synthetic run directories.  They cover the structural
trajectory checks, each of the five audit flags (positive and negative), the
failure classification and the pi harness adapter.  They never launch
Minecraft and never touch the network.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

TOOLS = Path(__file__).resolve().parent.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import coldstart as cs  # noqa: E402
import harness_preflight  # noqa: E402
import run_audit  # noqa: E402
import run_trace  # noqa: E402


EPOCH = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def ts(offset: float) -> str:
    return (EPOCH + dt.timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


REQUIRED_REVIEWS = (
    "machine_operated.causality",
    "logger_armed_before_activation.running",
    "answer_correct.oracle_independence",
)


def add_review(
    run_dir: Path,
    review_id: str,
    status: str = "resolved",
    by: str = "test-reviewer",
    note: str = "synthetic test review",
    evidence: tuple[str, ...] = ("trajectory:c2-noteblock",),
    at: str | None = None,
) -> None:
    record = {
        "id": review_id,
        "status": status,
        "by": by,
        "at": at or ts(40 + len(cs.read_jsonl(run_dir / cs.REVIEW_FILE))),
        "note": note,
        "evidence": list(evidence),
    }
    cs.append_jsonl(run_dir / cs.REVIEW_FILE, record)


def valid_run(
    base: Path,
    mutate_trajectory: Callable[[list[dict[str, Any]]], None] | None = None,
    mutate_events: Callable[[list[dict[str, Any]]], None] | None = None,
    mutate_logger: Callable[[list[dict[str, Any]]], None] | None = None,
    mutate_evidence: Callable[[dict[str, Any]], None] | None = None,
    oracle_status: str = "verified",
    with_reviews: bool = True,
) -> Path:
    run_dir = base
    run_dir.mkdir(parents=True, exist_ok=True)
    task = "task text: run the machine\n"
    (run_dir / "task.md").write_text(task, encoding="utf-8", newline="\n")
    run = {
        "schema": cs.SCHEMA_RUN,
        "run_id": "test-run",
        "created_at": ts(0),
        "harness": {"name": "pi", "model": "test-model", "provider": "test"},
        "task": {"path": "task.md", "sha256": cs.sha256_text(task)},
        "trajectory": {"path": cs.TRAJECTORY_FILE},
        "visibility": {"path": cs.VISIBILITY_FILE, "file_count": 0},
        "imports": [],
        "status": "open",
    }
    write_json(run_dir / cs.RUN_FILE, run)
    write_json(
        run_dir / cs.VISIBILITY_FILE,
        {"schema": cs.SCHEMA_VISIBILITY, "created_at": ts(0), "file_count": 0, "files": []},
    )

    trajectory: list[dict[str, Any]] = [
        {"schema": cs.SCHEMA_TRAJECTORY, "record": "header", "run_id": "test-run", "created_at": ts(0)},
        {"record": "phase", "phase": "prepare", "actor": "test", "at": ts(1), "seq": 2},
        {
            "record": "call",
            "call_id": "c1-build-logger",
            "tool": "bash",
            "arguments": {"command": "javac LoggerMod.java && install LoggerMod.jar"},
            "phase": "agent",
            "actor": "agent",
            "at": ts(4),
            "seq": 3,
        },
        {"record": "result", "call_id": "c1-build-logger", "status": "ok", "actor": "agent", "at": ts(4.5), "seq": 4},
        {
            "record": "call",
            "call_id": "c2-noteblock",
            "tool": "mc_bridge",
            "arguments": {"command": "execute use note_block"},
            "phase": "agent",
            "actor": "agent",
            "at": ts(10.5),
            "seq": 5,
        },
        {"record": "result", "call_id": "c2-noteblock", "status": "ok", "actor": "agent", "at": ts(10.6), "seq": 6},
        {
            "record": "call",
            "call_id": "c3-read-logger",
            "tool": "bash",
            "arguments": {"command": "cat agent/logger.jsonl"},
            "phase": "agent",
            "actor": "agent",
            "at": ts(17),
            "seq": 7,
        },
        {
            "record": "result",
            "call_id": "c3-read-logger",
            "status": "ok",
            "actor": "agent",
            "at": ts(17.1),
            "seq": 8,
            "result": {"text": "cart-1 iron_ingot redstone"},
        },
        {"record": "phase", "phase": "audit", "actor": "test", "at": ts(30), "seq": 9},
    ]
    if mutate_trajectory:
        mutate_trajectory(trajectory)
    write_jsonl(run_dir / cs.TRAJECTORY_FILE, trajectory)

    test_events = [
        {"schema": cs.SCHEMA_AUDITMOD, "event": "instance_ready", "instance": "lab-a", "dimension": "minecraft:overworld", "tick": 100, "seq": 1, "at": ts(0)},
        {"event": "input_attempt", "instance": "lab-a", "tick": 200, "seq": 2, "at": ts(10)},
        {"event": "input_processed", "instance": "lab-a", "actor": "fake-1", "tick": 201, "seq": 3, "at": ts(11)},
        {"event": "cart_ejected", "instance": "lab-a", "uuid": "cart-1", "tick": 205, "seq": 4, "at": ts(15)},
        {"event": "cart_removed", "instance": "lab-a", "uuid": "cart-1", "tick": 260, "seq": 5, "at": ts(20)},
        {"event": "end", "instance": "lab-a", "tick": 300, "seq": 6, "at": ts(25)},
    ]
    if mutate_events:
        mutate_events(test_events)
    write_jsonl(run_dir / "test" / "testmod.jsonl", test_events)

    logger_events = [
        {"schema": cs.SCHEMA_AGENT_LOGGER, "event": "logger_armed", "instance": "lab-a", "tick": 150, "at": ts(5), "seq": 1},
        {
            "event": "cart_observed",
            "instance": "lab-a",
            "uuid": "cart-1",
            "tick": 220,
            "at": ts(16),
            "seq": 2,
            "position": [1, 2, 3],
            "items": ["minecraft:iron_ingot", "minecraft:redstone"],
        },
    ]
    if mutate_logger:
        mutate_logger(logger_events)
    write_jsonl(run_dir / "agent" / "logger.jsonl", logger_events)

    write_json(
        run_dir / "answer.json",
        {
            "schema": cs.SCHEMA_ANSWER,
            "carts": [
                {"uuid": "cart-1", "items": ["minecraft:iron_ingot", "minecraft:redstone"]},
            ],
        },
    )
    oracle: dict[str, Any] = {
        "schema": cs.SCHEMA_ORACLE,
        "status": oracle_status,
        "source": "testmod+fixture",
        "evidence_refs": ["testmod:seq4", "fixture:ready"],
        "carts": [{"uuid": "cart-1", "items": ["iron_ingot", "redstone"]}],
    }
    write_json(run_dir / "oracle.json", oracle)
    write_json(run_dir / "fixture" / "ready.json", {"status": "ready", "ready": True})
    write_json(run_dir / "restore" / "verify.json", {"status": "ok"})

    evidence: dict[str, Any] = {
        "schema": "mc-agent-coldstart-evidence/1",
        "agent_logger": {"path": "agent/logger.jsonl", "sha256": cs.sha256_file(run_dir / "agent" / "logger.jsonl")},
        "test_mod": {"path": "test/testmod.jsonl", "sha256": cs.sha256_file(run_dir / "test" / "testmod.jsonl")},
        "answer": {"path": "answer.json", "sha256": cs.sha256_file(run_dir / "answer.json")},
        "oracle": {"path": "oracle.json", "sha256": cs.sha256_file(run_dir / "oracle.json")},
        "fixture": {"path": "fixture/ready.json", "sha256": cs.sha256_file(run_dir / "fixture" / "ready.json")},
        "restore": {"path": "restore/verify.json", "sha256": cs.sha256_file(run_dir / "restore" / "verify.json")},
    }
    if mutate_evidence:
        mutate_evidence(evidence)
    write_json(run_dir / cs.EVIDENCE_FILE, evidence)
    if with_reviews:
        for review_id in REQUIRED_REVIEWS:
            add_review(run_dir, review_id)
    return run_dir


def audit(run_dir: Path) -> dict[str, Any]:
    args = argparse.Namespace(run_dir=run_dir, answer=None, oracle=None)
    report, _code = run_audit.evaluate(args)
    return report


def flag(report: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in report["flags"] if item["flag"] == name)


class TrajectoryValidationTests(unittest.TestCase):
    def test_valid_trajectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            records = cs.read_jsonl(run_dir / cs.TRAJECTORY_FILE)
            self.assertEqual(cs.validate_trajectory(records), [])

    def test_missing_result_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            records = [r for r in cs.read_jsonl(run_dir / cs.TRAJECTORY_FILE) if r.get("call_id") != "c3-read-logger" or r.get("record") == "call"]
            self.assertTrue(any("no result" in problem for problem in cs.validate_trajectory(records)))

    def test_duplicate_call_id_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(
                Path(tmp) / "run",
                mutate_trajectory=lambda records: records.append(
                    {"record": "call", "call_id": "c1-build-logger", "tool": "bash", "at": ts(6), "seq": 99}
                ),
            )
            records = cs.read_jsonl(run_dir / cs.TRAJECTORY_FILE)
            self.assertTrue(any("duplicate call_id" in problem for problem in cs.validate_trajectory(records)))


class AuditFlagTests(unittest.TestCase):
    def test_valid_run_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run"))
            self.assertEqual(report["overall"], "PASS")
            self.assertIsNone(report["failure_class"])
            self.assertEqual([f["status"] for f in report["flags"]], ["PASS"] * 5)
            machine = flag(report, "machine_operated")
            self.assertTrue(machine["evidence"])
            self.assertEqual([item["id"] for item in machine["required_review"]], ["machine_operated.causality"])
            self.assertEqual(len(machine["review_resolutions"]), 1)
            self.assertEqual(machine["review_resolutions"][0]["by"], "test-reviewer")

    def test_unresolved_reviews_block_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", with_reviews=False))
            self.assertEqual(report["overall"], "PENDING")
            self.assertIsNone(report["failure_class"])
            for name in ("machine_operated", "logger_armed_before_activation", "answer_correct"):
                item = flag(report, name)
                self.assertEqual(item["status"], "PENDING")
                self.assertTrue(item["required_review"])
                self.assertIn("awaiting explicit review", item["summary"])

    def test_resolved_reviews_restore_mechanical_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run", with_reviews=False)
            for review_id in REQUIRED_REVIEWS:
                add_review(run_dir, review_id)
            report = audit(run_dir)
            self.assertEqual(report["overall"], "PASS")

    def test_rejected_review_is_agent_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            add_review(run_dir, "machine_operated.causality", status="rejected", note="the traced call is unrelated")
            report = audit(run_dir)
            self.assertEqual(report["overall"], "FAIL")
            self.assertEqual(report["failure_class"], "AGENT_FAIL")
            self.assertEqual(flag(report, "machine_operated")["status"], "FAIL")

    def test_bare_logger_filename_echo_is_not_pass(self) -> None:
        def echo_filename(records: list[dict[str, Any]]) -> None:
            for record in records:
                if record.get("call_id") == "c3-read-logger":
                    record["tool"] = "powershell"
                    record["arguments"] = {"command": 'Write-Output "logger.jsonl"'}
                if record.get("record") == "result" and record.get("call_id") == "c3-read-logger":
                    record["result"] = {"text": "logger.jsonl"}

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run", mutate_trajectory=echo_filename)
            report = audit(run_dir)
            read_flag = flag(report, "agent_read_log")
            self.assertEqual(read_flag["status"], "PENDING")
            self.assertEqual([item["id"] for item in read_flag["required_review"]], ["agent_read_log.linkage"])
            self.assertNotEqual(report["overall"], "PASS")
            add_review(run_dir, "agent_read_log.linkage")
            self.assertEqual(flag(audit(run_dir), "agent_read_log")["status"], "PASS")

    def test_uuid_echo_only_is_not_pass(self) -> None:
        def echo_uuid(records: list[dict[str, Any]]) -> None:
            for record in records:
                if record.get("call_id") == "c3-read-logger":
                    record["arguments"] = {"command": "Write-Output cart-1"}
                if record.get("record") == "result" and record.get("call_id") == "c3-read-logger":
                    record["result"] = {"text": "cart-1"}

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_trajectory=echo_uuid))
            self.assertEqual(flag(report, "agent_read_log")["status"], "PENDING")
            self.assertNotEqual(report["overall"], "PASS")

    def test_content_without_logger_reference_is_ambiguous(self) -> None:
        def unrelated_content(records: list[dict[str, Any]]) -> None:
            for record in records:
                if record.get("call_id") == "c3-read-logger":
                    record["arguments"] = {"command": "Write-Output 'cart-1 iron_ingot redstone'"}
                if record.get("record") == "result" and record.get("call_id") == "c3-read-logger":
                    record["result"] = {"text": "cart-1 iron_ingot redstone"}

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_trajectory=unrelated_content))
            read_flag = flag(report, "agent_read_log")
            self.assertEqual(read_flag["status"], "PENDING")
            self.assertIn("agent_read_log.linkage", [item["id"] for item in read_flag["required_review"]])

    def test_missing_ordering_evidence_is_not_pass(self) -> None:
        def strip_ordering(events: list[dict[str, Any]]) -> None:
            for event in events:
                if event["event"] in ("input_processed",):
                    event.pop("at")
                    event.pop("tick")
                    event.pop("seq", None)

        def strip_logger_ordering(logger: list[dict[str, Any]]) -> None:
            for event in logger:
                event.pop("at", None)
                event.pop("tick", None)
                event.pop("seq", None)

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(
                valid_run(
                    Path(tmp) / "run",
                    mutate_events=strip_ordering,
                    mutate_logger=strip_logger_ordering,
                )
            )
            armed = flag(report, "logger_armed_before_activation")
            self.assertNotEqual(armed["status"], "PASS")
            self.assertIn("logger_armed_before_activation.ordering", [item["id"] for item in armed["required_review"]])
            self.assertNotEqual(report["overall"], "PASS")

    def test_same_tick_late_arm_is_fail(self) -> None:
        def late_arm(logger: list[dict[str, Any]]) -> None:
            logger[0]["at"] = ts(11)
            logger[0]["tick"] = 201
            logger[0]["seq"] = 999

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_logger=late_arm))
            self.assertEqual(flag(report, "logger_armed_before_activation")["status"], "FAIL")
            self.assertEqual(report["failure_class"], "AGENT_FAIL")

    def test_instance_mismatch_is_fail(self) -> None:
        def other_instance(logger: list[dict[str, Any]]) -> None:
            logger[0]["instance"] = "lab-b"

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_logger=other_instance))
            self.assertEqual(flag(report, "logger_armed_before_activation")["status"], "FAIL")
            self.assertEqual(report["failure_class"], "AGENT_FAIL")

    def test_capture_before_ejection_is_fail(self) -> None:
        def pre_activation(logger: list[dict[str, Any]]) -> None:
            logger[1]["at"] = ts(12)
            logger[1]["tick"] = 150

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_logger=pre_activation))
            transient = flag(report, "transient_outputs_captured")
            self.assertEqual(transient["status"], "FAIL")
            self.assertIn("before they were ejected", transient["summary"])

    def test_missing_removal_requires_review(self) -> None:
        def no_removal(events: list[dict[str, Any]]) -> None:
            events[:] = [event for event in events if event["event"] != "cart_removed"]

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run", mutate_events=no_removal)
            report = audit(run_dir)
            transient = flag(report, "transient_outputs_captured")
            self.assertEqual(transient["status"], "PENDING")
            self.assertIn("transient_outputs_captured.window", [item["id"] for item in transient["required_review"]])
            self.assertNotEqual(report["overall"], "PASS")
            add_review(run_dir, "transient_outputs_captured.window")
            report = audit(run_dir)
            self.assertEqual(flag(report, "transient_outputs_captured")["status"], "PASS")
            self.assertEqual(report["overall"], "PASS")

    def test_capture_without_inventory_requires_review(self) -> None:
        def no_items(logger: list[dict[str, Any]]) -> None:
            logger[1].pop("items", None)

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_logger=no_items))
            transient = flag(report, "transient_outputs_captured")
            self.assertEqual(transient["status"], "PENDING")
            self.assertIn("transient_outputs_captured.inventory", [item["id"] for item in transient["required_review"]])

    def test_attempts_only_is_agent_fail(self) -> None:
        def only_attempts(events: list[dict[str, Any]]) -> None:
            events[:] = [event for event in events if event["event"] != "input_processed"]

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_events=only_attempts))
            self.assertEqual(report["overall"], "FAIL")
            self.assertEqual(report["failure_class"], "AGENT_FAIL")
            self.assertEqual(flag(report, "machine_operated")["status"], "FAIL")
            self.assertIn("attempt", flag(report, "machine_operated")["summary"])

    def test_late_logger_is_agent_fail(self) -> None:
        def late(logger: list[dict[str, Any]]) -> None:
            logger[0]["at"] = ts(12)
            logger[0]["tick"] = 300

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_logger=late))
            self.assertEqual(report["failure_class"], "AGENT_FAIL")
            self.assertEqual(flag(report, "logger_armed_before_activation")["status"], "FAIL")

    def test_capture_after_removal_is_agent_fail(self) -> None:
        def late(logger: list[dict[str, Any]]) -> None:
            logger[1]["at"] = ts(21)
            logger[1]["tick"] = 280

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_logger=late))
            self.assertEqual(report["failure_class"], "AGENT_FAIL")
            self.assertEqual(flag(report, "transient_outputs_captured")["status"], "FAIL")

    def test_test_side_only_observation_is_agent_fail(self) -> None:
        def strip_read(records: list[dict[str, Any]]) -> None:
            records[:] = [r for r in records if r.get("call_id") != "c3-read-logger"]

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_trajectory=strip_read))
            self.assertEqual(report["failure_class"], "AGENT_FAIL")
            self.assertEqual(flag(report, "agent_read_log")["status"], "FAIL")
            self.assertIn("test-side", flag(report, "agent_read_log")["summary"])

    def test_oracle_pending_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", oracle_status="pending"))
            self.assertEqual(report["overall"], "PENDING")
            self.assertEqual(flag(report, "answer_correct")["status"], "PENDING")

    def test_answer_mismatch_is_agent_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            write_json(
                run_dir / "answer.json",
                {"schema": cs.SCHEMA_ANSWER, "carts": [{"uuid": "cart-1", "items": ["minecraft:gold_ingot"]}]},
            )
            evidence = cs.read_json(run_dir / cs.EVIDENCE_FILE)
            evidence["answer"]["sha256"] = cs.sha256_file(run_dir / "answer.json")
            write_json(run_dir / cs.EVIDENCE_FILE, evidence)
            report = audit(run_dir)
            self.assertEqual(report["failure_class"], "AGENT_FAIL")
            self.assertEqual(flag(report, "answer_correct")["status"], "FAIL")

    def test_agent_answer_as_oracle_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            oracle = cs.read_json(run_dir / "oracle.json")
            oracle["source"] = "agent answer copy"
            write_json(run_dir / "oracle.json", oracle)
            evidence = cs.read_json(run_dir / cs.EVIDENCE_FILE)
            evidence["oracle"]["sha256"] = cs.sha256_file(run_dir / "oracle.json")
            write_json(run_dir / cs.EVIDENCE_FILE, evidence)
            report = audit(run_dir)
            self.assertEqual(flag(report, "answer_correct")["status"], "FAIL")

    def test_invalid_fixture_is_fixture_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            write_json(run_dir / "fixture" / "ready.json", {"status": "invalid", "ready": False})
            evidence = cs.read_json(run_dir / cs.EVIDENCE_FILE)
            evidence["fixture"]["sha256"] = cs.sha256_file(run_dir / "fixture" / "ready.json")
            write_json(run_dir / cs.EVIDENCE_FILE, evidence)
            report = audit(run_dir)
            self.assertEqual(report["overall"], "FAIL")
            self.assertEqual(report["failure_class"], "FIXTURE_INVALID")

    def test_failed_restore_is_fixture_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            write_json(run_dir / "restore" / "verify.json", {"status": "partial"})
            evidence = cs.read_json(run_dir / cs.EVIDENCE_FILE)
            evidence["restore"]["sha256"] = cs.sha256_file(run_dir / "restore" / "verify.json")
            write_json(run_dir / cs.EVIDENCE_FILE, evidence)
            report = audit(run_dir)
            self.assertEqual(report["failure_class"], "FIXTURE_INVALID")

    def test_missing_fixture_declaration_is_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_evidence=lambda evidence: evidence.update({"fixture": None})))
            self.assertEqual(report["overall"], "PENDING")
            self.assertTrue(report["pending_reasons"])

    def test_broken_trajectory_is_infra_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            (run_dir / cs.TRAJECTORY_FILE).write_text("{not json}\n", encoding="utf-8")
            report = audit(run_dir)
            self.assertEqual(report["overall"], "FAIL")
            self.assertEqual(report["failure_class"], "INFRA_ERROR")

    def test_hash_mismatch_is_infra_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = valid_run(Path(tmp) / "run")
            with (run_dir / "test" / "testmod.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event": "tampered"}) + "\n")
            report = audit(run_dir)
            self.assertEqual(report["failure_class"], "INFRA_ERROR")

    def test_foreign_run_id_is_infra_error(self) -> None:
        def foreign(events: list[dict[str, Any]]) -> None:
            events[2]["run_id"] = "some-other-run"

        with tempfile.TemporaryDirectory() as tmp:
            report = audit(valid_run(Path(tmp) / "run", mutate_events=foreign))
            self.assertEqual(report["overall"], "FAIL")
            self.assertEqual(report["failure_class"], "INFRA_ERROR")
            self.assertTrue(any("some-other-run" in item for item in report["infra_errors"]))


class PiImportTests(unittest.TestCase):
    def _session(self, path: Path) -> None:
        # Imported sessions postdate the run; use wall-clock times so the
        # trajectory's non-decreasing timestamp check is meaningful.
        now = dt.datetime.now(dt.timezone.utc)

        def session_time(offset: float) -> str:
            return (now + dt.timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")

        records = [
            {"type": "session", "version": 3, "id": "s1", "timestamp": session_time(0), "cwd": str(path.parent)},
            {
                "type": "message",
                "id": "m1",
                "timestamp": session_time(1),
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "toolCall", "id": "call_a", "name": "bash", "arguments": {"command": "echo hi"}},
                        {"type": "toolCall", "id": "call_b", "name": "read", "arguments": {"path": "x.txt"}},
                    ],
                    "model": "test-model",
                    "provider": "test",
                },
            },
            {
                "type": "message",
                "id": "m2",
                "timestamp": session_time(2),
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call_a",
                    "toolName": "bash",
                    "content": [{"type": "text", "text": "hi"}],
                    "isError": False,
                },
            },
            {
                "type": "message",
                "id": "m3",
                "timestamp": session_time(3),
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call_b",
                    "toolName": "read",
                    "content": [{"type": "text", "text": "x"}],
                    "isError": True,
                },
            },
        ]
        write_jsonl(path, records)

    def test_import_and_validate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "run"
            code = run_trace.main(
                [
                    "init",
                    "--run-dir",
                    str(run_dir),
                    "--run-id",
                    "import-test",
                    "--task-text",
                    "t",
                    "--harness",
                    "pi",
                    "--visible-root",
                    str(base),
                ]
            )
            self.assertEqual(code, 0)
            session = base / "session.jsonl"
            self._session(session)
            code = run_trace.main(
                ["import-pi", "--run-dir", str(run_dir), "--session", str(session), "--phase", "agent"]
            )
            self.assertEqual(code, 0)
            records = cs.read_jsonl(run_dir / cs.TRAJECTORY_FILE)
            calls = [r for r in records if r.get("record") == "call"]
            results = [r for r in records if r.get("record") == "result"]
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(results), 2)
            self.assertEqual(cs.validate_trajectory(records), [])
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_trace.main(["validate", "--run-dir", str(run_dir), "--json"])
            self.assertEqual(code, 0)
            self.assertTrue((run_dir / "imports" / "session.jsonl").is_file())
            run = cs.read_json(run_dir / cs.RUN_FILE)
            self.assertTrue(run["imports"][0]["sha256"])

    def test_import_missing_result_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "run"
            run_trace.main(["init", "--run-dir", str(run_dir), "--run-id", "x", "--task-text", "t"])
            session = base / "session.jsonl"
            self._session(session)
            lines = session.read_text(encoding="utf-8").splitlines()
            session.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8", newline="\n")
            run_trace.main(["import-pi", "--run-dir", str(run_dir), "--session", str(session)])
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_trace.main(["validate", "--run-dir", str(run_dir)])
            self.assertEqual(code, 1)


class ReviewCommandTests(unittest.TestCase):
    def test_review_command_writes_review_and_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                run_trace.main(["init", "--run-dir", str(run_dir), "--run-id", "r", "--task-text", "t"])
                code = run_trace.main(
                    [
                        "review",
                        "--run-dir",
                        str(run_dir),
                        "--id",
                        "machine_operated.causality",
                        "--status",
                        "resolved",
                        "--by",
                        "reviewer-1",
                        "--note",
                        "watched the replay",
                        "--evidence",
                        "trajectory:c2-noteblock",
                    ]
                )
                self.assertEqual(code, 0)
                reviews, problems = cs.load_reviews(run_dir / cs.REVIEW_FILE)
                self.assertEqual(problems, [])
                self.assertEqual(reviews["machine_operated.causality"]["by"], "reviewer-1")
                records = cs.read_jsonl(run_dir / cs.TRAJECTORY_FILE)
                marks = [r for r in records if r.get("record") == "mark" and r.get("name") == "review"]
                self.assertEqual(len(marks), 1)
                self.assertEqual(marks[0]["actor"], "reviewer")
                validate_code = run_trace.main(["validate", "--run-dir", str(run_dir)])
            self.assertEqual(validate_code, 0)

    def test_resolved_review_without_reviewer_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                run_trace.main(["init", "--run-dir", str(run_dir), "--run-id", "r", "--task-text", "t"])
            (run_dir / cs.REVIEW_FILE).write_text(
                json.dumps({"id": "machine_operated.causality", "status": "resolved"}) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_trace.main(["validate", "--run-dir", str(run_dir)])
            self.assertEqual(code, 1)


class VisibilityTests(unittest.TestCase):
    def test_visible_root_outside_cwd_stays_inside_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside.txt"
            outside.write_text("external\n", encoding="utf-8", newline="\n")
            run_dir = base / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_trace.main(
                    [
                        "init",
                        "--run-dir",
                        str(run_dir),
                        "--run-id",
                        "visibility",
                        "--task-text",
                        "t",
                        "--visible-root",
                        str(outside),
                    ]
                )
            self.assertEqual(code, 0)
            visibility = cs.read_json(run_dir / cs.VISIBILITY_FILE)
            entry = next(item for item in visibility["files"] if item["path"].startswith("_external/"))
            copy = (run_dir / entry["copy"]).resolve()
            self.assertTrue(copy.is_relative_to(run_dir.resolve()))
            self.assertTrue(copy.is_file())


class ArtifactTests(unittest.TestCase):
    def test_artifact_is_stored_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            run_dir = base / "run"
            with contextlib.redirect_stdout(io.StringIO()):
                run_trace.main(["init", "--run-dir", str(run_dir), "--run-id", "art", "--task-text", "t"])
                script = base / "LoggerMod.java"
                script.write_text("class LoggerMod {}\n", encoding="utf-8", newline="\n")
                code = run_trace.main(
                    ["artifact", "--run-dir", str(run_dir), "--path", str(script), "--label", "agent logger source"]
                )
                self.assertEqual(code, 0)
                run = cs.read_json(run_dir / cs.RUN_FILE)
                self.assertEqual(len(run["artifacts"]), 1)
                self.assertTrue((run_dir / run["artifacts"][0]["path"]).is_file())
                validate_code = run_trace.main(["validate", "--run-dir", str(run_dir)])
            self.assertEqual(validate_code, 0)
            artifact_path = run_dir / run["artifacts"][0]["path"]
            artifact_path.write_text("class Changed {}\n", encoding="utf-8", newline="\n")
            with contextlib.redirect_stdout(io.StringIO()):
                tampered_code = run_trace.main(["validate", "--run-dir", str(run_dir)])
            self.assertEqual(tampered_code, 1)


class PreflightTests(unittest.TestCase):
    def test_fast_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.json"
            write_json(
                config,
                {
                    "name": "selftest",
                    "command": sys.executable,
                    "version_args": ["--version"],
                    "shell": [sys.executable, "--version"],
                    "fetch_urls": [],
                    "ports": {"api": 0, "mod": 0},
                    "lab": {"enabled": False},
                },
            )
            out = base / "out"
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = harness_preflight.main(
                    ["run", "--config", str(config), "--out", str(out), "--checks", "harness_identity,terminal,filesystem"]
                )
            self.assertEqual(code, 0)
            report = cs.read_json(out / "preflight.json")
            self.assertEqual(report["overall"], "PASS")
            self.assertEqual([c["status"] for c in report["checks"]], ["PASS"] * 3)

    def test_missing_harness_command_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = base / "config.json"
            write_json(
                config,
                {
                    "name": "broken",
                    "command": "definitely-not-a-real-harness-command-xyz",
                    "version_args": ["--version"],
                    "fetch_urls": [],
                    "lab": {"enabled": False},
                },
            )
            out = base / "out"
            with contextlib.redirect_stdout(io.StringIO()):
                code = harness_preflight.main(["run", "--config", str(config), "--out", str(out), "--checks", "harness_identity"])
            self.assertEqual(code, 1)
            report = cs.read_json(out / "preflight.json")
            self.assertEqual(report["overall"], "FAIL")

    def test_checks_are_printed(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as buffer:
            code = harness_preflight.main(["list"])
        self.assertEqual(code, 0)
        self.assertIn("lab_management", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
