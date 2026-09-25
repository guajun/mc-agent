#!/usr/bin/env python3
"""Shared primitives for the auditable cold-start run protocol.

This module is imported by ``run_trace.py`` (the run entry and trajectory
sink), ``harness_preflight.py`` (the environment contract) and
``run_audit.py`` (the evidence-based judgement).  It contains no model
calls and no Minecraft logic: it moves and checks facts.

Vocabulary used throughout (see ``docs/coldstart-protocol.md``):

* **run** - one cold-start attempt, rooted in a run directory.
* **phase** - ``prepare`` (test-side setup), ``restore`` (copy restoration),
  ``agent`` (the agent's own work), ``audit`` (post-run review).
* **actor** - who produced a record: ``operator``, ``test``, ``agent`` or
  ``restore``.
* **evidence ref** - a stable string such as ``trajectory:call_12`` or
  ``testmod:evt_7`` pointing at the record an audit decision used.

Stdlib only, on purpose: the tools must run in the harness environment even
before the bridge's optional dependencies exist.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------- constants

SCHEMA_RUN = "mc-agent-coldstart-run/1"
SCHEMA_VISIBILITY = "mc-agent-coldstart-visibility/1"
SCHEMA_TRAJECTORY = "mc-agent-coldstart-trajectory/1"
SCHEMA_PREFLIGHT = "mc-agent-harness-preflight/1"
SCHEMA_AUDIT = "mc-agent-coldstart-audit/1"
SCHEMA_AGENT_LOGGER = "mc-agent-coldstart-logger/1"
SCHEMA_AUDITMOD = "mc-agent-auditmod/1"
SCHEMA_ANSWER = "mc-agent-coldstart-answer/1"
SCHEMA_ORACLE = "mc-agent-coldstart-oracle/1"

PHASES = ("prepare", "restore", "agent", "audit")
ACTORS = ("operator", "test", "agent", "restore", "harness")
RESULT_STATUSES = ("ok", "error", "open")

RUN_FILE = "run.json"
TRAJECTORY_FILE = "trajectory.jsonl"
VISIBILITY_FILE = "visibility.json"
EVIDENCE_FILE = "evidence.json"

# The five required audit facts and the classification vocabulary.  Kept here
# so the recorder, the harness and the audit agree without importing each other.
AUDIT_FLAGS = (
    "machine_operated",
    "logger_armed_before_activation",
    "transient_outputs_captured",
    "agent_read_log",
    "answer_correct",
)
FAILURE_CLASSES = ("AGENT_FAIL", "FIXTURE_INVALID", "INFRA_ERROR")
FLAG_STATUSES = ("PASS", "FAIL", "PENDING", "INCOMPLETE")

# The canonical test-mod event names (#18 owns the mod; this is the projection
# the audit consumes).  ``aliases`` let a differently named field still be
# understood instead of silently mis-reading the file.
EVENT_ALIASES: Mapping[str, Sequence[str]] = {
    "event": ("event", "type", "kind", "name"),
    "instance": ("instance", "instance_id", "lab", "instanceId"),
    "dimension": ("dimension", "dim", "dimension_id"),
    "tick": ("tick", "server_tick", "serverTick", "game_tick"),
    "seq": ("seq", "sequence", "sequence_in_tick", "index", "order"),
    "uuid": ("uuid", "cart_uuid", "entity_uuid", "entity_id"),
    "at": ("at", "time", "timestamp", "wall_time", "captured_at"),
    "actor": ("actor", "actor_uuid", "player", "player_uuid", "operator"),
    "run_id": ("run_id", "runId"),
}

# Event families the audit understands.  Anything else is preserved but not
# interpreted.
TEST_EVENTS = (
    "instance_ready",
    "restore_started",
    "restore_finished",
    "input_attempt",
    "input_processed",
    "cart_ejected",
    "cart_removed",
    "phase",
    "end",
)
LOGGER_EVENTS = ("logger_armed", "cart_observed", "logger_flushed", "logger_error")


def utc_now() -> str:
    """RFC 3339 UTC, second precision plus milliseconds, no local timezone."""
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"


def epoch_now() -> float:
    return time.time()


# --------------------------------------------------------------------------- io


def say(message: str = "", stream: Any = None) -> None:
    """Print ASCII-escaped text: the console may be a legacy Windows codepage."""
    text = "" if message is None else str(message)
    if not all(ord(char) < 128 for char in text):
        text = ascii(text)
    print(text, file=stream or sys.stdout, flush=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8", newline="\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    """Append one record with a fresh ``seq`` and flush it to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    if "seq" not in record:
        record["seq"] = last_seq(path) + 1
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name} line {number}: not JSON ({error})") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} line {number}: record is not an object")
            records.append(value)
    return records


def last_seq(path: Path) -> int:
    records = read_jsonl(path)
    seqs = [int(r["seq"]) for r in records if isinstance(r.get("seq"), int)]
    return max(seqs, default=0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def tail_text(text: str, limit: int = 8000) -> str:
    """Cheap bound for bytes we copy into summaries; never drops the head."""
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n...[truncated {len(text) - limit} chars]...\n" + text[-limit // 2 :]


def head_of(text: str, length: int = 12) -> str:
    return text[:length]


# --------------------------------------------------------------------------- record helpers


def get_field(record: Mapping[str, Any], name: str) -> Any:
    """Read a canonical field, accepting the documented aliases."""
    for key in EVENT_ALIASES.get(name, (name,)):
        if key in record:
            return record[key]
    return None


def as_time(value: Any) -> float | None:
    """Seconds since the epoch for an ISO-8601 string or an epoch number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value.strip())
    return None


def normalize_item(item: Any) -> str:
    """One comparable item string: counts stay in the string, names normalise."""
    if isinstance(item, str):
        name = item.strip()
    elif isinstance(item, Mapping):
        name = str(item.get("item") or item.get("id") or item.get("name") or "").strip()
        count = item.get("count", item.get("Count"))
        count_int = as_int(count)
        if count_int is not None and count_int != 1:
            name = f"{name} x{count_int}"
    else:
        name = str(item).strip()
    if name and ":" not in name.split(" x")[0] and not name.startswith("minecraft:"):
        name = f"minecraft:{name}"
    return name


def normalize_items(items: Any) -> list[str]:
    if items is None:
        return []
    if isinstance(items, Mapping):
        # {"0": ..., "1": ...} slot map
        items = [items[key] for key in sorted(items, key=lambda k: as_int(k) if as_int(k) is not None else 0)]
    if not isinstance(items, (list, tuple)):
        return []
    return [normalize_item(item) for item in items if normalize_item(item)]


# --------------------------------------------------------------------------- validation


class ValidationError(ValueError):
    pass


def validate_trajectory(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Structural checks on a trajectory; returns a list of problems.

    The rules are deliberately mechanical: unique call ids, one result per
    call, results after their call, monotonic ``seq`` and non-decreasing
    timestamps, known phases/actors/statuses.
    """
    problems: list[str] = []
    if not records:
        return ["trajectory is empty"]
    header = records[0]
    if header.get("record") != "header":
        problems.append("first record is not the header")
    if header.get("schema") != SCHEMA_TRAJECTORY:
        problems.append(f"header schema is {header.get('schema')!r}, expected {SCHEMA_TRAJECTORY!r}")

    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    seqs: list[int] = []
    times: list[float] = []
    for index, record in enumerate(records):
        kind = record.get("record")
        if "seq" in record:
            if not isinstance(record["seq"], int):
                problems.append(f"line {index + 1}: seq is not an integer")
            else:
                seqs.append(record["seq"])
        at = as_time(record.get("at"))
        if at is not None:
            times.append(at)
        if record.get("phase") is not None and record["phase"] not in PHASES:
            problems.append(f"line {index + 1}: unknown phase {record['phase']!r}")
        if record.get("actor") is not None and record["actor"] not in ACTORS:
            problems.append(f"line {index + 1}: unknown actor {record['actor']!r}")

        if kind == "call":
            call_id = str(record.get("call_id") or "")
            if not call_id:
                problems.append(f"line {index + 1}: call without call_id")
            elif call_id in calls:
                problems.append(f"line {index + 1}: duplicate call_id {call_id!r}")
            else:
                calls[call_id] = record
            if not record.get("tool"):
                problems.append(f"line {index + 1}: call {call_id!r} has no tool")
        elif kind == "result":
            call_id = str(record.get("call_id") or "")
            if not call_id:
                problems.append(f"line {index + 1}: result without call_id")
            elif call_id in results:
                problems.append(f"line {index + 1}: duplicate result for {call_id!r}")
            else:
                results[call_id] = record
            status = record.get("status")
            if status not in RESULT_STATUSES:
                problems.append(f"line {index + 1}: result status {status!r} not in {RESULT_STATUSES}")
        elif kind == "phase":
            if record.get("phase") not in PHASES:
                problems.append(f"line {index + 1}: phase record without a valid phase")
        elif kind == "mark":
            if not record.get("name"):
                problems.append(f"line {index + 1}: mark without a name")
        elif kind not in ("header", "note"):
            problems.append(f"line {index + 1}: unknown record kind {kind!r}")

    if seqs != sorted(seqs):
        problems.append("seq values are not monotonically increasing")
    if len(set(seqs)) != len(seqs):
        problems.append("seq values repeat")
    if times != sorted(times):
        problems.append("timestamps are not monotonically non-decreasing")

    for call_id in calls:
        if call_id not in results:
            problems.append(f"call {call_id!r} has no result")
    for call_id in results:
        if call_id not in calls:
            problems.append(f"result for unknown call {call_id!r}")
    return problems


def validate_call_shape(record: Mapping[str, Any]) -> str | None:
    if record.get("record") != "call":
        return "record must be a tool call"
    if not record.get("call_id"):
        return "call_id is required"
    if not record.get("tool"):
        return "tool is required"
    if record.get("phase") is not None and record["phase"] not in PHASES:
        return f"phase must be one of {PHASES}"
    if record.get("actor") is not None and record["actor"] not in ACTORS:
        return f"actor must be one of {ACTORS}"
    return None


def validate_phase(phase: str) -> str | None:
    if phase not in PHASES:
        return f"phase must be one of {PHASES}"
    return None


# --------------------------------------------------------------------------- evidence loading


class EvidenceError(Exception):
    """An evidence file the audit must read is missing, unreadable or changed."""


def resolve_evidence(run_dir: Path, declared: Mapping[str, Any] | None) -> Path | None:
    if not declared:
        return None
    value = declared.get("path")
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else (run_dir / path)


def load_declared_jsonl(
    run_dir: Path, declared: Mapping[str, Any] | None, what: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Load a declared JSONL evidence file and verify its recorded hash."""
    problems: list[str] = []
    path = resolve_evidence(run_dir, declared)
    if path is None:
        return [], [f"{what}: no evidence path declared"]
    if not path.is_file():
        return [], [f"{what}: file not found: {path}"]
    expected = (declared or {}).get("sha256")
    if expected:
        actual = sha256_file(path)
        if actual != expected:
            problems.append(f"{what}: sha256 mismatch (declared {head_of(str(expected))}, actual {head_of(actual)})")
    try:
        records = read_jsonl(path)
    except ValueError as error:
        return [], [f"{what}: {error}"]
    return records, problems


def load_declared_json(run_dir: Path, declared: Mapping[str, Any] | None, what: str) -> tuple[dict[str, Any] | None, list[str]]:
    problems: list[str] = []
    path = resolve_evidence(run_dir, declared)
    if path is None:
        return None, [f"{what}: no evidence path declared"]
    if not path.is_file():
        return None, [f"{what}: file not found: {path}"]
    expected = (declared or {}).get("sha256")
    if expected:
        actual = sha256_file(path)
        if actual != expected:
            problems.append(f"{what}: sha256 mismatch (declared {head_of(str(expected))}, actual {head_of(actual)})")
    try:
        payload = read_json(path)
    except (OSError, ValueError) as error:
        return None, [f"{what}: not readable JSON ({error})"]
    if not isinstance(payload, dict):
        return None, [f"{what}: top level is not an object"]
    return payload, problems


# --------------------------------------------------------------------------- audit engine


class FlagResult:
    def __init__(self, flag: str) -> None:
        self.flag = flag
        self.status = "FAIL"
        self.summary = ""
        self.evidence: list[str] = []
        self.missing: list[str] = []
        self.needs_review: list[str] = []

    def ok(self, summary: str) -> "FlagResult":
        self.status = "PASS"
        self.summary = summary
        return self

    def fail(self, summary: str, missing: Iterable[str] = ()) -> "FlagResult":
        self.status = "FAIL"
        self.summary = summary
        self.missing.extend(missing)
        return self

    def pending(self, summary: str, missing: Iterable[str] = ()) -> "FlagResult":
        self.status = "PENDING"
        self.summary = summary
        self.missing.extend(missing)
        return self

    def incomplete(self, summary: str, missing: Iterable[str] = ()) -> "FlagResult":
        self.status = "INCOMPLETE"
        self.summary = summary
        self.missing.extend(missing)
        return self

    def review(self, *notes: str) -> "FlagResult":
        self.needs_review.extend(notes)
        return self

    def ref(self, *refs: str) -> "FlagResult":
        self.evidence.extend(refs)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "flag": self.flag,
            "status": self.status,
            "summary": self.summary,
            "evidence": self.evidence,
            "missing": self.missing,
            "needs_review": self.needs_review,
        }


def event_name(record: Mapping[str, Any]) -> str:
    value = get_field(record, "event")
    return str(value) if value is not None else ""


def event_ref(prefix: str, record: Mapping[str, Any]) -> str:
    seq = record.get("seq")
    if seq is not None:
        return f"{prefix}:seq{seq}"
    return f"{prefix}:{head_of(sha256_text(json.dumps(record, sort_keys=True, default=str)))}"


def call_ref(call_id: str) -> str:
    return f"trajectory:{call_id}"


def agent_calls(trajectory: Sequence[Mapping[str, Any]], phase: str | None = None) -> list[dict[str, Any]]:
    """Tool calls authored by the agent (or by anything claiming that actor)."""
    calls = [r for r in trajectory if r.get("record") == "call" and r.get("actor") == "agent"]
    if phase is not None:
        calls = [r for r in calls if r.get("phase") == phase]
    return calls


def paired_results(trajectory: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r["call_id"]): r for r in trajectory if r.get("record") == "result" and r.get("call_id")}


def call_text(call: Mapping[str, Any], result: Mapping[str, Any] | None) -> str:
    data: dict[str, Any] = {"tool": call.get("tool"), "arguments": call.get("arguments")}
    if result is not None:
        data["result"] = result.get("result")
        data["error"] = result.get("error")
    return json.dumps(data, ensure_ascii=False, default=str)


def flag_machine_operated(
    test_events: Sequence[Mapping[str, Any]], trajectory: Sequence[Mapping[str, Any]]
) -> FlagResult:
    result = FlagResult("machine_operated")
    processed = [r for r in test_events if event_name(r) == "input_processed"]
    attempts = [r for r in test_events if event_name(r) == "input_attempt"]
    if not processed and not attempts:
        return result.fail(
            "the test side recorded no input event at all: the machine was never actuated in this run",
            ["testmod:input_processed"],
        )
    if not processed:
        return result.fail(
            f"only {len(attempts)} input attempt(s) recorded, no actual machine processing: attempts do not count as operation",
            ["testmod:input_processed"],
        )
    agent = agent_calls(trajectory, phase="agent")
    if not agent:
        return result.fail(
            f"{len(processed)} server-side processing event(s) exist but the trajectory has no agent call in the agent phase",
            ["trajectory:agent-call"],
        )
    first = min(processed, key=lambda r: as_time(get_field(r, "at")) or 0.0)
    first_at = as_time(get_field(first, "at"))
    before = [c for c in agent if first_at is None or (as_time(c.get("at")) or 0.0) <= first_at + 1e-6]
    if not before:
        return result.fail(
            "agent calls exist, but none precedes the first server-side processing event"
        )
    witness = before[-1]
    result.ok(
        f"{len(processed)} actual processing event(s), {len(attempts)} attempt(s); "
        f"agent call {witness.get('call_id')!r} precedes the first processing event"
    )
    result.ref(event_ref("testmod", first), call_ref(str(witness.get("call_id"))))
    result.review("semantic cause is not mechanical: confirm the traced call is what the test mod observed")
    if attempts:
        result.review(f"{len(attempts)} input attempt(s) exist; attempts alone must not be accepted as operation")
    return result


def flag_logger_armed(
    test_events: Sequence[Mapping[str, Any]],
    logger_events: Sequence[Mapping[str, Any]],
    trajectory: Sequence[Mapping[str, Any]],
) -> FlagResult:
    result = FlagResult("logger_armed_before_activation")
    processed = [r for r in test_events if event_name(r) == "input_processed"]
    armed = [r for r in logger_events if event_name(r) == "logger_armed"]
    if not armed:
        return result.fail("the agent logger has no logger_armed record", ["logger:logger_armed"])
    if not processed:
        return result.fail("no server-side processing event to order the logger against", ["testmod:input_processed"])
    first_armed = min(armed, key=lambda r: as_time(get_field(r, "at")) or 0.0)
    first_processed = min(processed, key=lambda r: as_time(get_field(r, "at")) or 0.0)
    armed_at = as_time(get_field(first_armed, "at"))
    armed_tick = as_int(get_field(first_armed, "tick"))
    processed_at = as_time(get_field(first_processed, "at"))
    processed_tick = as_int(get_field(first_processed, "tick"))
    if armed_at is not None and processed_at is not None and armed_at > processed_at + 1e-6:
        return result.fail(
            f"logger was armed at {armed_at} after the first processing event at {processed_at}"
        )
    if armed_tick is not None and processed_tick is not None and armed_tick > processed_tick:
        return result.fail(f"logger armed at tick {armed_tick}, after processing at tick {processed_tick}")
    arm_calls = [
        c
        for c in agent_calls(trajectory)
        if any(
            token in call_text(c, None).lower()
            for token in ("log", "logger", "trace", "record")
        )
    ]
    if not arm_calls:
        return result.fail(
            "logger_armed exists, but no agent call in the trajectory shows the logger being written, built or started",
            ["trajectory:logger-arm-call"],
        )
    witness = arm_calls[0]
    result.ok(
        f"logger armed at {armed_at if armed_at is not None else 'tick ' + str(armed_tick)} "
        f"before first processing at {processed_at if processed_at is not None else 'tick ' + str(processed_tick)}"
    )
    result.ref(event_ref("logger", first_armed), event_ref("testmod", first_processed), call_ref(str(witness.get("call_id"))))
    result.review("confirm from game-side load/restart evidence that the armed logger was actually running, not only written")
    return result


def flag_transient_captured(
    test_events: Sequence[Mapping[str, Any]], logger_events: Sequence[Mapping[str, Any]]
) -> FlagResult:
    result = FlagResult("transient_outputs_captured")
    ejected = [r for r in test_events if event_name(r) == "cart_ejected"]
    removed = {str(get_field(r, "uuid")): r for r in test_events if event_name(r) == "cart_removed"}
    observed = [r for r in logger_events if event_name(r) == "cart_observed"]
    if not ejected:
        return result.fail(
            "the test side recorded no cart_ejected event: there is no independent output to capture",
            ["testmod:cart_ejected"],
        )
    if not observed:
        return result.fail("the agent logger captured no cart output", ["logger:cart_observed"])
    observed_by_uuid: dict[str, list[dict[str, Any]]] = {}
    for record in observed:
        uuid = get_field(record, "uuid")
        if uuid is not None:
            observed_by_uuid.setdefault(str(uuid), []).append(record)
    missing: list[str] = []
    late: list[str] = []
    for eject in ejected:
        uuid = get_field(eject, "uuid")
        if uuid is None:
            missing.append("ejection without uuid")
            continue
        candidates = observed_by_uuid.get(str(uuid), [])
        if not candidates:
            missing.append(f"cart {uuid}")
            continue
        removal = removed.get(str(uuid))
        removal_at = as_time(get_field(removal, "at")) if removal else None
        removal_tick = as_int(get_field(removal, "tick")) if removal else None
        captured = candidates[0]
        captured_at = as_time(get_field(captured, "at"))
        captured_tick = as_int(get_field(captured, "tick"))
        if removal_at is not None and captured_at is not None and captured_at > removal_at + 1e-6:
            late.append(f"cart {uuid}")
        elif removal_tick is not None and captured_tick is not None and captured_tick > removal_tick:
            late.append(f"cart {uuid}")
    if missing:
        return result.fail(f"no agent capture for ejected cart(s): {', '.join(missing)}", missing)
    if late:
        return result.fail(f"agent captured cart(s) only after they were removed: {', '.join(late)}", late)
    result.ok(f"{len(observed)} capture(s) cover {len(ejected)} ejected cart(s)")
    for eject in ejected[:8]:
        uuid = str(get_field(eject, "uuid"))
        result.ref(event_ref("testmod", eject), event_ref("logger", observed_by_uuid[uuid][0]))
    if not removed:
        result.review("the test side recorded no cart_removed events; capture-before-removal is not mechanically provable from this run")
    result.review("items in each captured cart still need comparison against the server-side entity NBT")
    return result


def flag_agent_read_log(
    logger_events: Sequence[Mapping[str, Any]],
    trajectory: Sequence[Mapping[str, Any]],
    logger_declared: Mapping[str, Any] | None,
) -> FlagResult:
    result = FlagResult("agent_read_log")
    observed = [r for r in logger_events if event_name(r) == "cart_observed"]
    if not observed:
        return result.fail("the agent logger has no cart_observed records to read", ["logger:cart_observed"])
    captures = sorted(observed, key=lambda r: as_time(get_field(r, "at")) or 0.0)
    last_capture_at = as_time(get_field(captures[-1], "at")) or 0.0
    uuids = {str(get_field(r, "uuid")) for r in observed if get_field(r, "uuid") is not None}
    item_tokens = {normalize_item(item) for r in observed for item in normalize_items(r.get("items") or r.get("inventory"))}
    item_tokens.discard("")
    results = paired_results(trajectory)
    logger_name = Path(str((logger_declared or {}).get("path") or "log")).name.lower()
    strong: list[tuple[dict[str, Any], dict[str, Any]]] = []
    medium: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for call in agent_calls(trajectory):
        if call.get("record") != "call":
            continue
        at = as_time(call.get("at")) or 0.0
        if at < last_capture_at:
            continue
        result_record = results.get(str(call.get("call_id")))
        if result_record is None or result_record.get("status") != "ok":
            continue
        haystack = call_text(call, result_record)
        args_only = json.dumps(call.get("arguments"), ensure_ascii=False, default=str).lower()
        if logger_name and logger_name in args_only:
            strong.append((call, result_record))
            continue
        if uuids and any(uuid in haystack for uuid in uuids):
            strong.append((call, result_record))
            continue
        if item_tokens and sum(1 for token in item_tokens if token in haystack) >= min(2, len(item_tokens)):
            medium.append((call, result_record))
    if strong:
        call, _ = strong[0]
        result.ok("the agent's own trajectory reads the logger output after capture")
        result.ref(call_ref(str(call.get("call_id"))), event_ref("logger", captures[-1]))
        return result
    if medium:
        call, _ = medium[0]
        result.pending(
            "a later agent call returned text that contains captured item names, "
            "but no call was mechanically tied to the logger file or to a cart uuid",
            ["trajectory:read-logger"],
        )
        result.ref(call_ref(str(call.get("call_id"))))
        result.review("human review: confirm the returned text really came from the agent's own logger read")
        return result
    return result.fail(
        "no agent call after capture reads the logger output; test-side observation does not count",
        ["trajectory:read-logger"],
    )


def flag_answer_correct(
    answer: Mapping[str, Any] | None,
    oracle: Mapping[str, Any] | None,
    oracle_problems: Sequence[str],
) -> FlagResult:
    result = FlagResult("answer_correct")
    if answer is None:
        return result.fail("no agent answer artifact was declared or readable", ["answer"])
    carts = answer.get("carts") or answer.get("ordered_items_by_cart")
    if not isinstance(carts, list) or not carts:
        return result.fail("the answer has no carts list", ["answer:carts"])
    if oracle is None:
        return result.pending("no independently verified oracle exists for this run", ["oracle"])
    if oracle_problems:
        return result.pending("the oracle is unreadable or has changed: " + "; ".join(oracle_problems), ["oracle"])
    status = str(oracle.get("status") or "").lower()
    if status != "verified":
        return result.pending(
            f"oracle status is {status or 'missing'!r}, so correctness cannot be claimed yet",
            ["oracle:verified"],
        )
    refs = [str(ref) for ref in oracle.get("evidence_refs") or []]
    source = str(oracle.get("source") or "").lower()
    if not refs:
        return result.fail("the oracle is marked verified but carries no evidence references", ["oracle:evidence_refs"])
    if "agent" in source and "answer" in source:
        return result.fail("the oracle copies the agent answer instead of independent game evidence", ["oracle:source"])
    if any(ref.startswith("trajectory:") or ref.startswith("answer") for ref in refs):
        result.review("oracle references include agent-side records; confirm at least one independent game-side reference grounds it")
    oracle_carts = {
        str(cart.get("uuid")): cart for cart in (oracle.get("carts") or []) if isinstance(cart, Mapping)
    }
    if not oracle_carts:
        return result.fail("verified oracle has no carts", ["oracle:carts"])
    mismatches: list[str] = []
    for cart in carts:
        if not isinstance(cart, Mapping):
            mismatches.append("answer entry is not an object")
            continue
        uuid = str(cart.get("uuid"))
        expected = oracle_carts.get(uuid)
        if expected is None:
            mismatches.append(f"cart {uuid} absent from the oracle")
            continue
        got = normalize_items(cart.get("items"))
        want = normalize_items(expected.get("items"))
        if got != want:
            mismatches.append(f"cart {uuid}: answer {got} != oracle {want}")
    if mismatches:
        return result.fail("answer disagrees with the verified oracle: " + "; ".join(mismatches[:6]))
    result.ok(f"answer matches the verified oracle for {len(carts)} cart(s)")
    result.ref("oracle", *(f"oracle:{ref}" for ref in refs[:6]))
    result.review("human review: the oracle's own evidence chain (fixture plus test mod) is independent of the answer")
    return result


def classify(flags: Mapping[str, FlagResult], infra: Sequence[str], fixture: Sequence[str]) -> tuple[str, str | None]:
    if infra:
        return "FAIL", "INFRA_ERROR"
    if fixture:
        return "FAIL", "FIXTURE_INVALID"
    statuses = [flag.status for flag in flags.values()]
    if any(status == "FAIL" for status in statuses):
        return "FAIL", "AGENT_FAIL"
    if any(status in ("PENDING", "INCOMPLETE") for status in statuses):
        return "PENDING", None
    return "PASS", None


def render_audit_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Cold-start audit - {report.get('run_id')}",
        "",
        f"- schema: `{report.get('schema')}`",
        f"- audited at: {report.get('at')}",
        f"- **overall**: {report.get('overall')}"
        + (f" ({report.get('failure_class')})" if report.get("failure_class") else ""),
        "",
        "## Required flags",
        "",
        "| flag | status | summary | evidence |",
        "| --- | --- | --- | --- |",
    ]
    for flag in report.get("flags", []):
        evidence = "<br>".join(f"`{ref}`" for ref in flag.get("evidence", [])) or "-"
        lines.append(
            f"| `{flag.get('flag')}` | {flag.get('status')} | {flag.get('summary')} | {evidence} |"
        )
    if report.get("infra_errors"):
        lines += ["", "## Infrastructure errors", ""] + [f"- {item}" for item in report["infra_errors"]]
    if report.get("fixture_errors"):
        lines += ["", "## Fixture errors", ""] + [f"- {item}" for item in report["fixture_errors"]]
    review = [item for flag in report.get("flags", []) for item in flag.get("needs_review", [])]
    if review:
        lines += ["", "## Needs human semantic review", ""] + [f"- {item}" for item in review]
    lines += ["", "## Inputs", ""]
    for name, value in (report.get("inputs") or {}).items():
        lines.append(f"- {name}: `{value}`")
    return "\n".join(lines) + "\n"
