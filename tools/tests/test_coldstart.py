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


def valid_run(
    base: Path,
    mutate_trajectory: Callable[[list[dict[str, Any]]], None] | None = None,
    mutate_events: Callable[[list[dict[str, Any]]], None] | None = None,
    mutate_logger: Callable[[list[dict[str, Any]]], None] | None = None,
    mutate_evidence: Callable[[dict[str, Any]], None] | None = None,
    oracle_status: str = "verified",
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
            self.assertTrue(machine["needs_review"])

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
