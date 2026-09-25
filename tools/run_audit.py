#!/usr/bin/env python3
"""Auditable judgement for one cold-start run directory.

``run_audit.py`` reads only records and artifacts that already exist: the
canonical trajectory, the test-side audit-mod events, the agent's own logger
output, the declared fixture/restore checks, the answer and an independently
verified oracle.  It never launches Minecraft and never talks to a model.

It computes the five required facts - ``machine_operated``,
``logger_armed_before_activation``, ``transient_outputs_captured``,
``agent_read_log`` and ``answer_correct`` - each with evidence references.
Missing critical records fail closed (PASS is impossible); an oracle that is
not yet verified yields PENDING, never PASS.  Failures classify as
AGENT_FAIL, FIXTURE_INVALID or INFRA_ERROR.

    python tools/run_audit.py run --run-dir labs/coldstart/run-01

Exit codes: 0 PASS, 1 FAIL, 2 PENDING.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import coldstart as cs


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="audit a run directory")
    run.add_argument("--run-dir", required=True, type=Path)
    run.add_argument("--out", type=Path, default=None)
    run.add_argument("--answer", type=Path, default=None, help="override the declared answer file")
    run.add_argument("--oracle", type=Path, default=None, help="override the declared oracle file")
    run.add_argument("--json", action="store_true", help="print the report to stdout")
    explain = sub.add_parser("explain", help="show what the audit would read")
    explain.add_argument("--run-dir", required=True, type=Path)
    return parser.parse_args(argv)


def load_evidence(run_dir: Path, run: dict[str, Any]) -> dict[str, Any]:
    path = run_dir / cs.EVIDENCE_FILE
    if path.is_file():
        try:
            return cs.read_json(path)
        except (OSError, ValueError):
            pass
    declared = run.get("evidence")
    return declared if isinstance(declared, dict) else {}


def declared_or_override(
    run_dir: Path, evidence: dict[str, Any], name: str, override: Path | None, kind: str
) -> tuple[dict[str, Any] | None, list[str]]:
    declared = evidence.get(name)
    if override is not None:
        declared = {"path": str(override), "source": "cli-override"}
        if override.is_relative_to(run_dir):
            declared["path"] = override.relative_to(run_dir).as_posix()
    if kind == "json":
        return cs.load_declared_json(run_dir, declared, name)
    return cs.load_declared_jsonl(run_dir, declared, name)


def load_agent_logger(run_dir: Path, declared: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Prefer a normalized view but verify it against the raw logger bytes."""
    if not declared:
        return [], ["agent_logger: no evidence declared"]
    normalized = declared.get("normalized")
    if normalized:
        raw_path = cs.resolve_evidence(run_dir, declared)
        raw_problems: list[str] = []
        if raw_path is None or not raw_path.is_file():
            raw_problems.append("agent_logger: raw logger file is missing")
        else:
            expected = declared.get("sha256")
            if expected and cs.sha256_file(raw_path) != expected:
                raw_problems.append("agent_logger: raw logger sha256 mismatch")
        normalized_declared = dict(declared)
        normalized_declared["path"] = normalized
        records, problems = cs.load_declared_jsonl(run_dir, normalized_declared, "agent_logger (normalized)")
        source_hashes = {
            str(record.get("source_sha256"))
            for record in records
            if isinstance(record, dict) and record.get("source_sha256")
        }
        if raw_path is not None and raw_path.is_file() and source_hashes:
            actual = cs.sha256_file(raw_path)
            if actual not in source_hashes:
                problems.append("agent_logger: normalized records do not name the current raw logger bytes")
        return records, raw_problems + problems
    return cs.load_declared_jsonl(run_dir, declared, "agent_logger")


def evaluate(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    run_dir: Path = args.run_dir
    infra: list[str] = []
    fixture: list[str] = []
    pending: list[str] = []

    run: dict[str, Any] = {}
    run_path = run_dir / cs.RUN_FILE
    if not run_path.is_file():
        infra.append(f"{run_path} is missing")
    else:
        try:
            run = cs.read_json(run_path)
        except (OSError, ValueError) as error:
            infra.append(f"{cs.RUN_FILE} is unreadable: {error}")
        if run and run.get("schema") != cs.SCHEMA_RUN:
            infra.append(f"run schema is {run.get('schema')!r}, expected {cs.SCHEMA_RUN!r}")
        if run:
            for entry in run.get("artifacts") or []:
                artifact = run_dir / str(entry.get("path") or "")
                if not artifact.is_file():
                    infra.append(f"artifact {entry.get('path')} is missing")
                elif entry.get("sha256") and cs.sha256_file(artifact) != entry["sha256"]:
                    infra.append(f"artifact {entry.get('path')} hash changed")

    trajectory: list[dict[str, Any]] = []
    trajectory_path = run_dir / cs.TRAJECTORY_FILE
    if not trajectory_path.is_file():
        infra.append(f"{trajectory_path.name} is missing")
    else:
        try:
            trajectory = cs.read_jsonl(trajectory_path)
        except ValueError as error:
            infra.append(str(error))
        problems = cs.validate_trajectory(trajectory)
        infra.extend(f"trajectory: {problem}" for problem in problems)

    evidence = load_evidence(run_dir, run)

    test_events, test_problems = declared_or_override(run_dir, evidence, "test_mod", None, "jsonl")
    infra.extend(test_problems)

    logger_events, logger_problems = load_agent_logger(run_dir, evidence.get("agent_logger"))
    infra.extend(logger_problems)

    declared_run_id = run.get("run_id")
    if declared_run_id:
        for kind, records in (("test_mod", test_events), ("agent_logger", logger_events)):
            for record in records:
                event_run = cs.get_field(record, "run_id")
                if event_run is not None and str(event_run) != str(declared_run_id):
                    infra.append(
                        f"{kind} {cs.event_ref(kind, record)} belongs to run {event_run!r}, not {declared_run_id!r}"
                    )

    answer, answer_problems = declared_or_override(run_dir, evidence, "answer", args.answer, "json")
    infra.extend(answer_problems)

    oracle, oracle_problems = declared_or_override(run_dir, evidence, "oracle", args.oracle, "json")

    fixture_payload, fixture_problems = declared_or_override(run_dir, evidence, "fixture", None, "json")
    if evidence.get("fixture"):
        infra.extend(fixture_problems)
    else:
        pending.append("no fixture evidence is declared")
    if fixture_payload:
        status = str(fixture_payload.get("status") or fixture_payload.get("result") or "").lower()
        if fixture_payload.get("ready") is False or status in ("invalid", "failed", "fail", "error"):
            fixture.append(f"fixture report marks the fixture invalid ({(evidence.get('fixture') or {}).get('path')})")

    restore_payload, restore_problems = declared_or_override(run_dir, evidence, "restore", None, "json")
    if evidence.get("restore"):
        infra.extend(restore_problems)
    else:
        pending.append("no restore-verification evidence is declared")
    if restore_payload:
        status = str(restore_payload.get("status") or restore_payload.get("result") or "").lower()
        if status and status not in ("ok", "verified", "pass", "passed", "success"):
            restore_path = (evidence.get("restore") or {}).get("path")
            fixture.append(f"restore verification did not pass ({restore_path}): status={status}")

    preflight, preflight_problems = declared_or_override(run_dir, evidence, "preflight", None, "json")
    if preflight_problems and evidence.get("preflight"):
        infra.extend(preflight_problems)
    if preflight and str(preflight.get("overall") or "").upper() != "PASS":
        infra.append(f"declared preflight did not pass: overall={preflight.get('overall')!r}")

    flags = {
        "machine_operated": cs.flag_machine_operated(test_events, trajectory),
        "logger_armed_before_activation": cs.flag_logger_armed(test_events, logger_events, trajectory),
        "transient_outputs_captured": cs.flag_transient_captured(test_events, logger_events),
        "agent_read_log": cs.flag_agent_read_log(logger_events, trajectory, evidence.get("agent_logger")),
        "answer_correct": cs.flag_answer_correct(answer, oracle, oracle_problems),
    }

    overall, failure_class = cs.classify(flags, infra, fixture)
    if overall == "PASS" and pending:
        overall, failure_class = "PENDING", None

    report = {
        "schema": cs.SCHEMA_AUDIT,
        "at": cs.utc_now(),
        "run_id": run.get("run_id") or run_dir.name,
        "harness": run.get("harness"),
        "overall": overall,
        "failure_class": failure_class,
        "flags": [flags[name].as_dict() for name in cs.AUDIT_FLAGS],
        "infra_errors": infra,
        "fixture_errors": fixture,
        "pending_reasons": pending,
        "inputs": {
            "run": cs.RUN_FILE,
            "trajectory": f"{len(trajectory)} records",
            "test_mod": str((evidence.get("test_mod") or {}).get("path") or "(none)"),
            "agent_logger": str((evidence.get("agent_logger") or {}).get("path") or "(none)"),
            "answer": str((evidence.get("answer") or {}).get("path") or "(none)"),
            "oracle": str((evidence.get("oracle") or {}).get("path") or "(none)"),
            "fixture": str((evidence.get("fixture") or {}).get("path") or "(none)"),
            "restore": str((evidence.get("restore") or {}).get("path") or "(none)"),
        },
    }
    code = {"PASS": 0, "FAIL": 1, "PENDING": 2}[overall]
    return report, code


def cmd_explain(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    run = cs.read_json(run_dir / cs.RUN_FILE)
    evidence = load_evidence(run_dir, run)
    cs.say(f"run {run.get('run_id')} task sha256 {run.get('task', {}).get('sha256')}")
    for name in ("test_mod", "agent_logger", "answer", "oracle", "fixture", "restore", "preflight"):
        declared = evidence.get(name)
        if not declared:
            cs.say(f"  {name}: (not declared)")
            continue
        path = cs.resolve_evidence(run_dir, declared)
        state = "missing"
        if path is not None and path.is_file():
            state = f"present sha256={cs.head_of(cs.sha256_file(path))}"
        cs.say(f"  {name}: {declared.get('path')} [{state}]")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "explain":
        return cmd_explain(args)
    report, code = evaluate(args)
    out_dir = args.out or (args.run_dir / "audit")
    out_dir.mkdir(parents=True, exist_ok=True)
    cs.write_json(out_dir / "audit.json", report)
    (out_dir / "audit.md").write_text(cs.render_audit_markdown(report), encoding="utf-8", newline="\n")
    if args.json:
        cs.say(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        cs.say(f"[audit] overall {report['overall']}" + (f" ({report['failure_class']})" if report["failure_class"] else ""))
        for flag in report["flags"]:
            cs.say(f"  {flag['status']:>7}  {flag['flag']}: {flag['summary']}")
        if report["infra_errors"]:
            cs.say("  infra errors: " + "; ".join(report["infra_errors"][:5]))
        if report["fixture_errors"]:
            cs.say("  fixture errors: " + "; ".join(report["fixture_errors"][:5]))
        if report["pending_reasons"]:
            cs.say("  pending: " + "; ".join(report["pending_reasons"]))
        cs.say(f"[audit] wrote {out_dir / 'audit.json'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
