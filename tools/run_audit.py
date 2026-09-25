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
from typing import Any, Iterable, Mapping

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


def load_review_records(run_dir: Path, evidence: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Explicit review resolutions, declared or at the canonical run path."""
    declared = evidence.get("review")
    if declared:
        path = cs.resolve_evidence(run_dir, declared)
        if path is None or not path.is_file():
            return {}, [f"review: file not found: {path}"]
        problems: list[str] = []
        expected = declared.get("sha256")
        if expected and cs.sha256_file(path) != expected:
            problems.append("review: sha256 mismatch on reviews.jsonl")
        reviews, more = cs.load_reviews(path)
        return reviews, problems + more
    return cs.load_reviews(run_dir / cs.REVIEW_FILE)


def load_agent_logger(run_dir: Path, declared: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Prefer a normalized view but verify it against the raw logger bytes."""
    if not declared:
        return [], ["agent_logger: no evidence declared"]
    normalized = declared.get("normalized")
    if not normalized:
        return cs.load_declared_jsonl(run_dir, declared, "agent_logger")
    problems: list[str] = []
    raw_path = cs.resolve_evidence(run_dir, declared)
    raw_sha = ""
    if raw_path is None or not raw_path.is_file():
        problems.append("agent_logger: raw logger file is missing")
    else:
        raw_sha = cs.sha256_file(raw_path)
        expected = declared.get("sha256")
        if expected and raw_sha != expected:
            problems.append("agent_logger: raw logger sha256 mismatch")
    normalized_declared: dict[str, Any] = {"path": normalized}
    if declared.get("normalized_sha256"):
        normalized_declared["sha256"] = declared["normalized_sha256"]
    records, more = cs.load_declared_jsonl(run_dir, normalized_declared, "agent_logger (normalized)")
    problems.extend(more)
    if raw_sha:
        missing = [r for r in records if r.get("record") != "header" and not r.get("source_sha256")]
        if missing:
            problems.append(f"agent_logger: {len(missing)} normalized record(s) carry no source_sha256")
        wrong = [r for r in records if r.get("source_sha256") and str(r["source_sha256"]) != raw_sha]
        if wrong:
            problems.append("agent_logger: normalized records do not name the current raw logger bytes")
    return records, problems


def event_scope(
    records: Sequence[dict[str, Any]], names: Sequence[str]
) -> tuple[set[str], set[str]]:
    """Distinct instance/dimension values on the named events."""
    instances: set[str] = set()
    dimensions: set[str] = set()
    for record in records:
        if cs.event_name(record) not in names:
            continue
        instance = cs.get_field(record, "instance")
        dimension = cs.get_field(record, "dimension")
        if instance is not None:
            instances.add(str(instance))
        if dimension is not None:
            dimensions.add(str(dimension))
    return instances, dimensions


def preflight_contract_problems(preflight: dict[str, Any] | None, run: dict[str, Any]) -> list[str]:
    """Cross-check a declared preflight report against the run manifest."""
    if not preflight:
        return []
    if str(preflight.get("overall") or "").upper() != "PASS":
        return [f"declared preflight did not pass: overall={preflight.get('overall')!r}"]
    problems: list[str] = []
    harness = run.get("harness") or {}
    report_harness = preflight.get("harness") or {}
    for field in ("name", "model", "provider"):
        expected, actual = harness.get(field), report_harness.get(field)
        if expected and actual and str(expected) != str(actual):
            problems.append(f"preflight {field} {actual!r} != run {field} {expected!r}")
    repo = (run.get("repo") or {}).get("commit")
    report_repo = (preflight.get("environment") or {}).get("repo")
    if repo and report_repo and str(repo) != str(report_repo):
        problems.append(f"preflight repo {report_repo!r} != run repo {repo!r}")
    checks = {str(check.get("id")): check for check in preflight.get("checks") or []}
    ports = checks.get("ports")
    if not ports or ports.get("status") != "PASS":
        problems.append("preflight has no passing ports check")
    lab = checks.get("lab_management")
    if not lab or lab.get("status") != "PASS":
        problems.append("preflight has no passing live lab_management check")
    else:
        versions = lab.get("versions") or {}
        if not (versions.get("serverPort") and versions.get("rconPort")):
            problems.append("preflight lab_management did not start a live server (no ports recorded)")
    return problems


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
            for entry in run.get("imports") or []:
                imported = run_dir / str(entry.get("path") or "")
                if not imported.is_file():
                    infra.append(f"import {entry.get('path')} is missing")
                elif entry.get("sha256") and cs.sha256_file(imported) != entry["sha256"]:
                    infra.append(f"import {entry.get('path')} hash changed")

    trajectory: list[dict[str, Any]] = []
    trajectory_path = run_dir / cs.TRAJECTORY_FILE
    if not trajectory_path.is_file():
        infra.append(f"{trajectory_path.name} is missing")
    else:
        try:
            trajectory = cs.read_jsonl(trajectory_path)
        except ValueError as error:
            infra.append(str(error))
        problems = cs.validate_trajectory(trajectory, run_id=run.get("run_id"))
        infra.extend(f"trajectory: {problem}" for problem in problems)

    evidence = load_evidence(run_dir, run)

    test_events, test_problems = declared_or_override(run_dir, evidence, "test_mod", None, "jsonl")
    infra.extend(test_problems)

    logger_events, logger_problems = load_agent_logger(run_dir, evidence.get("agent_logger"))
    infra.extend(logger_problems)

    test_instances, test_dimensions = event_scope(
        test_events, ("instance_ready", "input_processed", "cart_ejected", "cart_removed", "end")
    )
    logger_instances, logger_dimensions = event_scope(
        logger_events, ("logger_armed", "cart_observed", "logger_flushed", "logger_error")
    )
    if len(test_instances) > 1:
        infra.append("test-mod events mix instances: " + ", ".join(sorted(test_instances)))
    if len(test_dimensions) > 1:
        infra.append("test-mod events mix dimensions: " + ", ".join(sorted(test_dimensions)))
    stray_instances = logger_instances - test_instances
    if test_instances and stray_instances:
        infra.append(
            "agent logger events name instance(s) outside the test-mod experiment: "
            + ", ".join(sorted(stray_instances))
        )
    stray_dimensions = logger_dimensions - test_dimensions
    if test_dimensions and stray_dimensions:
        infra.append(
            "agent logger events name dimension(s) outside the test-mod experiment: "
            + ", ".join(sorted(stray_dimensions))
        )

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
    infra.extend(preflight_contract_problems(preflight, run))

    ejected_uuids = {
        str(cs.get_field(record, "uuid"))
        for record in test_events
        if cs.event_name(record) == "cart_ejected" and cs.get_field(record, "uuid") is not None
    }
    if oracle and str(oracle.get("status") or "").lower() == "verified":
        oracle_uuids = {
            str(cart.get("uuid"))
            for cart in (oracle.get("carts") or [])
            if isinstance(cart, Mapping)
        }
        missing_oracle = sorted(ejected_uuids - oracle_uuids)
        if missing_oracle:
            infra.append("verified oracle omits ejected cart(s): " + ", ".join(missing_oracle))
    declared_evidence = {name: bool(evidence.get(name)) for name in ("test_mod", "fixture", "restore", "preflight", "agent_logger", "answer")}
    available_seqs = {
        "testmod": {
            seq
            for seq in (cs.as_int(cs.get_field(record, "seq")) for record in test_events)
            if seq is not None
        }
    }
    provenance_problems, independent_refs, _agent_refs = cs.oracle_provenance(
        oracle, declared_evidence, available_seqs
    )

    flags = {
        "machine_operated": cs.flag_machine_operated(test_events, trajectory),
        "logger_armed_before_activation": cs.flag_logger_armed(
            test_events, logger_events, trajectory, evidence.get("agent_logger")
        ),
        "transient_outputs_captured": cs.flag_transient_captured(test_events, logger_events),
        "agent_read_log": cs.flag_agent_read_log(logger_events, trajectory, evidence.get("agent_logger")),
        "answer_correct": cs.flag_answer_correct(
            answer, oracle, oracle_problems, provenance_problems, bool(independent_refs)
        ),
    }
    reviews, review_problems = load_review_records(run_dir, evidence)
    infra.extend(review_problems)
    cs.apply_reviews(flags, reviews)

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
            "review": str((evidence.get("review") or {}).get("path") or cs.REVIEW_FILE),
        },
    }
    code = {"PASS": 0, "FAIL": 1, "PENDING": 2}[overall]
    return report, code


def cmd_explain(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    run = cs.read_json(run_dir / cs.RUN_FILE)
    evidence = load_evidence(run_dir, run)
    cs.say(f"run {run.get('run_id')} task sha256 {run.get('task', {}).get('sha256')}")
    for name in ("test_mod", "agent_logger", "answer", "oracle", "fixture", "restore", "preflight", "review"):
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
