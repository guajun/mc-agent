#!/usr/bin/env python3
"""Cold-start run entry: create a run directory and record its trajectory.

``run_trace.py`` is the harness-neutral half of the auditable cold-start
protocol.  It does not run a model and it does not touch Minecraft.  A harness
(or an operator, or a test script) calls its subcommands to:

* ``init``       - create the run, pin the task input, snapshot the documents
                   and skills visible to the agent, open the trajectory;
* ``record``     - append one canonical call/result/phase/mark record;
* ``call``/``result`` - the same, with the common fields as flags;
* ``phase``      - switch the lifecycle phase (prepare/restore/agent/audit);
* ``import-pi``  - translate a pi harness session JSONL into canonical records;
* ``validate``   - structural checks over the run and its trajectory.

Everything the audit later needs is written here, append-only, with hashes.
See ``docs/coldstart-protocol.md`` for the schema and the required evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import coldstart as cs

DEFAULT_EXCLUDES = (
    ".git",
    "labs",
    "site",
    ".venv",
    "__pycache__",
    "node_modules",
    "*.jsonl",
    "*.log",
    "*.pid",
    "*.session.jsonl",
)

MAX_VISIBLE_BYTES = 5 * 1024 * 1024


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a run directory")
    init.add_argument("--run-dir", required=True, type=Path)
    init.add_argument("--run-id", default="")
    task = init.add_mutually_exclusive_group(required=True)
    task.add_argument("--task-file", type=Path)
    task.add_argument("--task-text")
    init.add_argument("--harness", default="")
    init.add_argument("--model", default="")
    init.add_argument("--provider", default="")
    init.add_argument("--harness-version", default="")
    init.add_argument("--task-player-uuid", default="")
    init.add_argument("--visible-root", action="append", default=[], type=Path)
    init.add_argument("--exclude", action="append", default=[])
    init.add_argument("--force", action="store_true")
    init.add_argument("--note", action="append", default=[])

    record = sub.add_parser("record", help="append one canonical record")
    record.add_argument("--run-dir", required=True, type=Path)
    record.add_argument("--json", type=Path, help="a JSON object from a file")
    record.add_argument("--json-text", help="a JSON object as text")
    record.add_argument("--stdin", action="store_true", help="read the JSON object from stdin")

    call = sub.add_parser("call", help="append a tool call")
    call.add_argument("--run-dir", required=True, type=Path)
    call.add_argument("--call-id", required=True)
    call.add_argument("--tool", required=True)
    call.add_argument("--arguments", default="{}", help="JSON object")
    call.add_argument("--phase", default="agent")
    call.add_argument("--actor", default="agent")
    call.add_argument("--instance", default=None)
    call.add_argument("--dimension", default=None)
    call.add_argument("--tick", type=int, default=None)
    call.add_argument("--at", default=None)
    call.add_argument("--note", default=None)

    result = sub.add_parser("result", help="append the result of a tool call")
    result.add_argument("--run-dir", required=True, type=Path)
    result.add_argument("--call-id", required=True)
    result.add_argument("--status", choices=cs.RESULT_STATUSES, default="ok")
    result.add_argument("--result", default=None, help="JSON value or plain text")
    result.add_argument("--error", default=None)
    result.add_argument("--phase", default=None)
    result.add_argument("--actor", default="agent")
    result.add_argument("--at", default=None)
    result.add_argument("--duration-ms", type=float, default=None)

    phase = sub.add_parser("phase", help="append a lifecycle phase transition")
    phase.add_argument("--run-dir", required=True, type=Path)
    phase.add_argument("--phase", required=True, choices=cs.PHASES)
    phase.add_argument("--actor", default="operator")
    phase.add_argument("--instance", default=None)
    phase.add_argument("--at", default=None)
    phase.add_argument("--evidence", action="append", default=[])
    phase.add_argument("--note", default=None)

    importer = sub.add_parser("import-pi", help="import a pi session JSONL")
    importer.add_argument("--run-dir", required=True, type=Path)
    importer.add_argument("--session", required=True, type=Path)
    importer.add_argument("--phase", default="agent", choices=cs.PHASES)
    importer.add_argument("--actor", default="agent", choices=cs.ACTORS)
    importer.add_argument("--no-copy", action="store_true")
    importer.add_argument("--max-text", type=int, default=20000)

    artifact = sub.add_parser("artifact", help="persist a self-written file or build output")
    artifact.add_argument("--run-dir", required=True, type=Path)
    artifact.add_argument("--path", required=True, type=Path, help="file or directory to keep")
    artifact.add_argument("--label", default="")
    artifact.add_argument("--phase", default="agent", choices=cs.PHASES)
    artifact.add_argument("--actor", default="agent", choices=cs.ACTORS)
    artifact.add_argument("--max-bytes", type=int, default=20 * 1024 * 1024)

    review = sub.add_parser("review", help="record an explicit review resolution")
    review.add_argument("--run-dir", required=True, type=Path)
    review.add_argument("--id", required=True, help="e.g. machine_operated.causality")
    review.add_argument("--status", required=True, choices=cs.REVIEW_STATUSES)
    review.add_argument("--by", required=True, help="the reviewer identity")
    review.add_argument("--note", default="")
    review.add_argument("--evidence", action="append", default=[])
    review.add_argument("--at", default=None)

    validate = sub.add_parser("validate", help="check the run and its trajectory")
    validate.add_argument("--run-dir", required=True, type=Path)
    validate.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="print a short run summary")
    status.add_argument("--run-dir", required=True, type=Path)
    return parser.parse_args(argv)


# --------------------------------------------------------------------------- helpers


def load_run(run_dir: Path) -> dict[str, Any]:
    path = run_dir / cs.RUN_FILE
    if not path.is_file():
        raise SystemExit(f"error: {path} not found; run `init` first")
    return cs.read_json(path)


def save_run(run_dir: Path, run: dict[str, Any]) -> None:
    cs.write_json(run_dir / cs.RUN_FILE, run)


def trajectory_path(run_dir: Path) -> Path:
    return run_dir / cs.TRAJECTORY_FILE


def ensure_open(run_dir: Path) -> dict[str, Any]:
    run = load_run(run_dir)
    path = trajectory_path(run_dir)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        header = {
            "schema": cs.SCHEMA_TRAJECTORY,
            "record": "header",
            "run_id": run.get("run_id"),
            "created_at": cs.utc_now(),
            "harness": run.get("harness"),
        }
        cs.append_jsonl(path, header)
    return run


def repo_state(root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return out.stdout.strip() if out.returncode == 0 else ""

    dirty = git("status", "--porcelain")
    return {
        "root": str(root),
        "commit": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_entries": len([line for line in dirty.splitlines() if line.strip()]),
    }


def should_exclude(relative: str, patterns: list[str]) -> bool:
    parts = Path(relative).parts
    name = Path(relative).name
    for pattern in patterns:
        if pattern in parts or name == pattern:
            return True
        if Path(pattern).name == pattern and Path(pattern).suffix and name.endswith(Path(pattern).suffix):
            return True
    return False


def snapshot_visibility(run_dir: Path, roots: list[Path], excludes: list[str]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    missing: list[str] = []
    run_resolved = run_dir.resolve()
    for root in roots:
        if not root.exists():
            missing.append(str(root))
            continue
        candidates = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in candidates:
            resolved = path.resolve()
            if resolved == run_resolved or run_resolved in resolved.parents:
                continue  # a run nested under a visible root must not snapshot itself
            try:
                relative = resolved.relative_to(Path.cwd().resolve()).as_posix()
            except ValueError:
                # Outside this repository: keep a safe, unique name inside the run.
                safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", resolved.as_posix().lstrip("/"))
                relative = f"_external/{cs.sha256_text(str(resolved))[:16]}_{safe[-120:]}"
            if should_exclude(relative, excludes):
                continue
            size = path.stat().st_size
            entry: dict[str, Any] = {"path": relative, "size": size, "sha256": cs.sha256_file(path)}
            if size <= MAX_VISIBLE_BYTES:
                copy = run_dir / "visibility" / "files" / relative
                copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, copy)
                entry["copy"] = copy.relative_to(run_dir).as_posix()
            else:
                entry["copy"] = None
                entry["note"] = f"larger than {MAX_VISIBLE_BYTES} bytes; hash only"
            files.append(entry)
    visibility = {
        "schema": cs.SCHEMA_VISIBILITY,
        "created_at": cs.utc_now(),
        "cwd": str(Path.cwd()),
        "roots": [str(root) for root in roots],
        "exclude": excludes,
        "file_count": len(files),
        "files": files,
        "missing": missing,
    }
    cs.write_json(run_dir / cs.VISIBILITY_FILE, visibility)
    return visibility


# --------------------------------------------------------------------------- commands


def cmd_init(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    run_dir = run_dir if run_dir.is_absolute() else (Path.cwd() / run_dir)
    if run_dir.exists() and any(run_dir.iterdir()) and not args.force:
        raise SystemExit(f"error: {run_dir} is not empty; pass --force to reuse it")
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.task_file is not None:
        task_bytes = args.task_file.read_bytes()
        task_name = args.task_file.name
    else:
        task_bytes = args.task_text.encode("utf-8")
        task_name = "inline"
    (run_dir / "task.md").write_bytes(task_bytes)
    task_sha = cs.sha256_bytes(task_bytes)

    roots = args.visible_root or [Path.cwd()]
    excludes = list(DEFAULT_EXCLUDES) + list(args.exclude)
    visibility = snapshot_visibility(run_dir, roots, excludes)

    run_id = args.run_id or run_dir.name
    run = {
        "schema": cs.SCHEMA_RUN,
        "run_id": run_id,
        "created_at": cs.utc_now(),
        "repo": repo_state(Path.cwd()),
        "harness": {
            "name": args.harness,
            "version": args.harness_version,
            "model": args.model,
            "provider": args.provider,
        },
        "task": {
            "path": "task.md",
            "source": task_name,
            "sha256": task_sha,
            "player_uuid": args.task_player_uuid,
            "note": "exact bytes shown to the agent; no answer or prior solution is part of the run",
        },
        "trajectory": {"path": cs.TRAJECTORY_FILE},
        "visibility": {"path": cs.VISIBILITY_FILE, "file_count": visibility["file_count"]},
        "imports": [],
        "status": "open",
        "notes": list(args.note),
    }
    cs.write_json(run_dir / cs.RUN_FILE, run)
    evidence_template = {
        "schema": "mc-agent-coldstart-evidence/1",
        "note": "declare external artifacts here before the audit; sha256 is checked when present",
        "agent_logger": None,
        "test_mod": None,
        "answer": None,
        "oracle": None,
        "fixture": None,
        "restore": None,
        "preflight": None,
    }
    cs.write_json(run_dir / cs.EVIDENCE_FILE, evidence_template)
    ensure_open(run_dir)
    cs.append_jsonl(
        trajectory_path(run_dir),
        {
            "schema": cs.SCHEMA_TRAJECTORY,
            "record": "phase",
            "run_id": run_id,
            "phase": "prepare",
            "actor": "operator",
            "at": cs.utc_now(),
            "note": "run created",
            "evidence": [f"task:{cs.head_of(task_sha)}", f"visibility:{visibility['file_count']} files"],
        },
    )
    cs.say(f"run {run_id} initialised at {run_dir}")
    cs.say(f"  task sha256 {task_sha}")
    cs.say(f"  visible files {visibility['file_count']}")
    return 0


def read_record(args: argparse.Namespace) -> dict[str, Any]:
    if args.json is not None:
        return cs.read_json(args.json)
    if args.json_text is not None:
        return json.loads(args.json_text)
    if args.stdin:
        return json.loads(sys.stdin.read())
    raise SystemExit("error: pass --json, --json-text or --stdin")


def approve(run_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    run = ensure_open(run_dir)
    record.setdefault("schema", cs.SCHEMA_TRAJECTORY)
    record.setdefault("run_id", run.get("run_id"))
    record.setdefault("at", cs.utc_now())
    if str(record.get("run_id")) != str(run.get("run_id")):
        raise SystemExit(
            f"error: record run_id {record.get('run_id')!r} does not belong to run {run.get('run_id')!r}"
        )
    kind = record.get("record")
    if kind == "call":
        problem = cs.validate_call_shape(record)
    elif kind == "result":
        problem = None if record.get("call_id") and record.get("status") in cs.RESULT_STATUSES else "result needs call_id and a valid status"
    elif kind == "phase":
        problem = cs.validate_phase(str(record.get("phase") or ""))
    elif kind == "mark":
        problem = None if record.get("name") else "mark needs a name"
    else:
        problem = f"record must be one of call/result/phase/mark, not {kind!r}"
    if problem:
        raise SystemExit(f"error: {problem}")
    return cs.append_jsonl(trajectory_path(run_dir), record)


def cmd_record(args: argparse.Namespace) -> int:
    record = read_record(args)
    record = approve(args.run_dir, record)
    cs.say(f"recorded {record.get('record')} seq={record.get('seq')}")
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as error:
        raise SystemExit(f"error: --arguments is not JSON: {error}") from error
    record = {
        "record": "call",
        "call_id": args.call_id,
        "tool": args.tool,
        "arguments": arguments,
        "phase": args.phase,
        "actor": args.actor,
    }
    if args.instance:
        record["instance"] = args.instance
    if args.dimension:
        record["dimension"] = args.dimension
    if args.tick is not None:
        record["tick"] = args.tick
    if args.note:
        record["note"] = args.note
    if args.at:
        record["at"] = args.at
    record = approve(args.run_dir, record)
    cs.say(f"recorded call {record['call_id']} seq={record['seq']}")
    return 0


def cmd_result(args: argparse.Namespace) -> int:
    value: Any = args.result
    if value is not None:
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {"text": value}
    record: dict[str, Any] = {
        "record": "result",
        "call_id": args.call_id,
        "status": args.status,
        "actor": args.actor,
    }
    if args.phase:
        record["phase"] = args.phase
    if value is not None:
        record["result"] = value
    if args.error is not None:
        record["error"] = args.error
    if args.duration_ms is not None:
        record["duration_ms"] = args.duration_ms
    if args.at:
        record["at"] = args.at
    record = approve(args.run_dir, record)
    cs.say(f"recorded result for {record['call_id']} status={record['status']} seq={record['seq']}")
    return 0


def cmd_phase(args: argparse.Namespace) -> int:
    record = {
        "record": "phase",
        "phase": args.phase,
        "actor": args.actor,
        "evidence": list(args.evidence),
    }
    if args.instance:
        record["instance"] = args.instance
    if args.note:
        record["note"] = args.note
    if args.at:
        record["at"] = args.at
    record = approve(args.run_dir, record)
    cs.say(f"phase -> {record['phase']} seq={record['seq']}")
    return 0


def extract_tool_text(message: dict[str, Any], max_text: int) -> tuple[str, str]:
    parts: list[str] = []
    for part in message.get("content") or []:
        if not isinstance(part, dict):
            parts.append(str(part))
        elif part.get("type") == "text":
            parts.append(str(part.get("text") or ""))
        elif part.get("type") == "thinking":
            parts.append(str(part.get("thinking") or ""))
        elif part.get("type") == "image":
            parts.append("[image]")
        else:
            parts.append(json.dumps(part, ensure_ascii=False, default=str))
    text = "\n".join(parts)
    digest = cs.sha256_text(text)
    return cs.tail_text(text, max_text), digest


def cmd_import_pi(args: argparse.Namespace) -> int:
    run = ensure_open(args.run_dir)
    session: Path = args.session
    if not session.is_file():
        raise SystemExit(f"error: {session} not found")
    records = cs.read_jsonl(session)
    if not records:
        raise SystemExit(f"error: {session} holds no records")

    copied_path: Path | None = None
    if not args.no_copy:
        copied_path = args.run_dir / "imports" / session.name
        copied_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(session, copied_path)
        run.setdefault("imports", []).append(
            {
                "source": str(session),
                "path": copied_path.relative_to(args.run_dir).as_posix(),
                "sha256": cs.sha256_file(session),
                "records": len(records),
                "imported_at": cs.utc_now(),
            }
        )
        save_run(args.run_dir, run)

    calls = 0
    results = 0
    for entry in records:
        if entry.get("type") != "message":
            continue
        message = entry.get("message") or {}
        role = message.get("role")
        timestamp = entry.get("timestamp") or message.get("timestamp") or cs.utc_now()
        if role == "assistant":
            for part in message.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "toolCall":
                    continue
                call_id = str(part.get("id") or f"import-{calls}")
                cs.append_jsonl(
                    trajectory_path(args.run_dir),
                    {
                        "schema": cs.SCHEMA_TRAJECTORY,
                        "record": "call",
                        "run_id": run.get("run_id"),
                        "call_id": call_id,
                        "tool": str(part.get("name") or "unknown"),
                        "arguments": part.get("arguments") or {},
                        "phase": args.phase,
                        "actor": args.actor,
                        "at": timestamp,
                        "source": "import:pi",
                        "model": message.get("model"),
                        "provider": message.get("provider"),
                    },
                )
                calls += 1
        elif role in ("toolResult", "tool"):
            call_id = str(message.get("toolCallId") or "")
            if not call_id:
                continue
            text, digest = extract_tool_text(message, args.max_text)
            cs.append_jsonl(
                trajectory_path(args.run_dir),
                {
                    "schema": cs.SCHEMA_TRAJECTORY,
                    "record": "result",
                    "run_id": run.get("run_id"),
                    "call_id": call_id,
                    "status": "error" if message.get("isError") else "ok",
                    "actor": args.actor,
                    "phase": args.phase,
                    "at": timestamp,
                    "source": "import:pi",
                    "tool_name": message.get("toolName"),
                    "result": {"text": text, "sha256": digest, "truncated": len(text) >= args.max_text},
                },
            )
            results += 1
    cs.say(f"imported {calls} call(s) and {results} result(s) from {session.name}")
    if copied_path is not None:
        cs.say(f"  raw session copied to {copied_path}")
    return 0


def cmd_artifact(args: argparse.Namespace) -> int:
    source: Path = args.path
    if not source.exists():
        raise SystemExit(f"error: {source} does not exist")
    sources = [source] if source.is_file() else sorted(path for path in source.rglob("*") if path.is_file())
    run = ensure_open(args.run_dir)
    stored: list[dict[str, Any]] = []
    for path in sources:
        if path.stat().st_size > args.max_bytes:
            cs.say(f"skip {path}: {path.stat().st_size} bytes exceeds --max-bytes")
            continue
        name = f"{len(run.setdefault('artifacts', [])) + 1:03d}-{path.name}"
        target = args.run_dir / "artifacts" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        entry = {
            "label": args.label or path.name,
            "source": str(path),
            "path": target.relative_to(args.run_dir).as_posix(),
            "sha256": cs.sha256_file(path),
            "size": path.stat().st_size,
            "phase": args.phase,
            "at": cs.utc_now(),
        }
        run["artifacts"].append(entry)
        stored.append(entry)
    save_run(args.run_dir, run)
    if stored:
        cs.append_jsonl(
            trajectory_path(args.run_dir),
            {
                "schema": cs.SCHEMA_TRAJECTORY,
                "record": "mark",
                "name": "artifact",
                "actor": args.actor,
                "phase": args.phase,
                "at": cs.utc_now(),
                "data": {"artifacts": [entry["path"] for entry in stored], "sha256": [entry["sha256"] for entry in stored]},
            },
        )
    cs.say(f"stored {len(stored)} artifact(s) under {args.run_dir / 'artifacts'}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    run = ensure_open(args.run_dir)
    record = {
        "id": args.id,
        "status": args.status,
        "by": args.by,
        "at": args.at or cs.utc_now(),
        "note": args.note,
        "evidence": list(args.evidence),
    }
    if args.status == "resolved" and not (args.by and record["at"]):
        raise SystemExit("error: a resolved review needs --by and a timestamp")
    cs.append_jsonl(args.run_dir / cs.REVIEW_FILE, record)
    cs.append_jsonl(
        trajectory_path(args.run_dir),
        {
            "schema": cs.SCHEMA_TRAJECTORY,
            "record": "mark",
            "run_id": run.get("run_id"),
            "name": "review",
            "actor": "reviewer",
            "at": record["at"],
            "data": {"id": args.id, "status": args.status, "by": args.by, "evidence": record["evidence"]},
        },
    )
    cs.say(f"review {args.id} -> {args.status} (by {args.by})")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    run_dir: Path = args.run_dir
    problems: list[str] = []
    try:
        run = load_run(run_dir)
    except SystemExit as error:
        problems.append(str(error))
        run = None
    if run is not None:
        if run.get("schema") != cs.SCHEMA_RUN:
            problems.append(f"run schema is {run.get('schema')!r}")
        task_path = run_dir / "task.md"
        if not task_path.is_file():
            problems.append("task.md is missing")
        else:
            actual = cs.sha256_file(task_path)
            if run.get("task", {}).get("sha256") != actual:
                problems.append("task.md has changed since init")
        visibility_path = run_dir / cs.VISIBILITY_FILE
        if not visibility_path.is_file():
            problems.append("visibility.json is missing")
        else:
            visibility = cs.read_json(visibility_path)
            if visibility.get("schema") != cs.SCHEMA_VISIBILITY:
                problems.append("visibility schema mismatch")
        trajectory = cs.read_jsonl(trajectory_path(run_dir))
        problems.extend(cs.validate_trajectory(trajectory, run_id=run.get("run_id")))
        for entry in run.get("imports") or []:
            raw = run_dir / entry["path"]
            if not raw.is_file():
                problems.append(f"import {entry['path']} is missing")
            elif cs.sha256_file(raw) != entry.get("sha256"):
                problems.append(f"import {entry['path']} hash changed")
        for entry in run.get("artifacts") or []:
            artifact = run_dir / entry["path"]
            if not artifact.is_file():
                problems.append(f"artifact {entry['path']} is missing")
            elif entry.get("sha256") and cs.sha256_file(artifact) != entry["sha256"]:
                problems.append(f"artifact {entry['path']} hash changed")
        evidence_path = run_dir / cs.EVIDENCE_FILE
        if evidence_path.is_file():
            evidence = cs.read_json(evidence_path)
            for name, declared in evidence.items():
                if not isinstance(declared, dict):
                    continue
                path = cs.resolve_evidence(run_dir, declared)
                if path is None:
                    continue
                if not path.is_file():
                    problems.append(f"evidence {name}: {path} is missing")
                elif declared.get("sha256") and cs.sha256_file(path) != declared["sha256"]:
                    problems.append(f"evidence {name}: sha256 changed")
        reviews_path = run_dir / cs.REVIEW_FILE
        if reviews_path.is_file():
            _reviews, review_problems = cs.load_reviews(reviews_path)
            problems.extend(review_problems)
    report = {
        "schema": "mc-agent-coldstart-validation/1",
        "valid": not problems,
        "problems": problems,
    }
    if args.json:
        cs.say(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        cs.say("valid" if not problems else f"invalid: {len(problems)} problem(s)")
        for problem in problems:
            cs.say(f"  - {problem}")
    return 0 if not problems else 1


def cmd_status(args: argparse.Namespace) -> int:
    run = load_run(args.run_dir)
    trajectory = cs.read_jsonl(trajectory_path(args.run_dir))
    calls = [r for r in trajectory if r.get("record") == "call"]
    results = [r for r in trajectory if r.get("record") == "result"]
    phases = [r for r in trajectory if r.get("record") == "phase"]
    cs.say(f"run {run.get('run_id')} ({run.get('status')})")
    cs.say(f"  harness {run.get('harness', {}).get('name')} model {run.get('harness', {}).get('model')}")
    cs.say(f"  calls {len(calls)}, results {len(results)}, phase records {len(phases)}")
    if phases:
        cs.say(f"  last phase {phases[-1].get('phase')}")
    if (args.run_dir / cs.REVIEW_FILE).is_file():
        reviews, _ = cs.load_reviews(args.run_dir / cs.REVIEW_FILE)
        cs.say(f"  reviews {len(reviews)}")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    handlers = {
        "init": cmd_init,
        "record": cmd_record,
        "call": cmd_call,
        "result": cmd_result,
        "phase": cmd_phase,
        "import-pi": cmd_import_pi,
        "artifact": cmd_artifact,
        "review": cmd_review,
        "validate": cmd_validate,
        "status": cmd_status,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
