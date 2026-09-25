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
ACTORS = ("operator", "test", "agent", "restore", "harness", "reviewer")
RESULT_STATUSES = ("ok", "error", "open")
REVIEW_STATUSES = ("pending", "resolved", "rejected")
REVIEW_FILE = "reviews.jsonl"

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
        self.required_review: list[dict[str, str]] = []
        self.review_resolutions: list[dict[str, Any]] = []
        self.review_gated = False
        self.mechanical_status = "FAIL"
        self.mechanical_summary = ""

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
        """Informational note; it does not block PASS."""
        self.needs_review.extend(notes)
        return self

    def require_review(self, review_id: str, question: str) -> "FlagResult":
        """A semantic fact that must be explicitly resolved before PASS.

        The flag immediately drops out of PASS while the review is unresolved;
        ``apply_reviews`` restores the mechanical outcome only when the run's
        review record carries a ``resolved`` entry for the id.
        """
        if review_id not in {item["id"] for item in self.required_review}:
            self.required_review.append({"id": review_id, "question": question})
        if self.status == "PASS":
            if not self.review_gated:
                self.review_gated = True
                self.mechanical_status = self.status
                self.mechanical_summary = self.summary
            self.status = "PENDING"
            self.summary = f"{self.mechanical_summary}; awaiting explicit review {review_id!r}"
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
            "required_review": self.required_review,
            "review_resolutions": self.review_resolutions,
        }


def load_reviews(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Load explicit review resolutions; the latest entry per id wins."""
    reviews: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    if not path.exists():
        return reviews, problems
    try:
        records = read_jsonl(path)
    except ValueError as error:
        return reviews, [str(error)]
    for number, record in enumerate(records, start=1):
        review_id = record.get("id")
        status = record.get("status")
        if not review_id or status not in REVIEW_STATUSES:
            problems.append(f"{path.name} line {number}: id and status ({REVIEW_STATUSES}) are required")
            continue
        if status == "resolved" and not (record.get("by") and record.get("at")):
            problems.append(f"{path.name} line {number}: a resolved review needs by and at")
            continue
        reviews[str(review_id)] = dict(record)
    return reviews, problems


def apply_reviews(flags: Mapping[str, "FlagResult"], reviews: Mapping[str, dict[str, Any]]) -> None:
    """Resolve or reject the required reviews of mechanically-satisfied flags.

    A required review with no entry or a ``pending`` entry keeps the flag
    PENDING; a ``rejected`` entry fails it; a ``resolved`` entry must carry the
    reviewer and timestamp.  When every required review is resolved, the flag's
    mechanical outcome is restored - this is the only path that turns a
    review-gated flag back into PASS.  An unresolved ``needs_review`` can
    therefore never be silently successful.
    """
    for flag in flags.values():
        if not flag.required_review:
            continue
        unresolved: list[str] = []
        rejected: list[str] = []
        for item in flag.required_review:
            review_id = item["id"]
            record = reviews.get(review_id)
            if record is None or record.get("status") == "pending":
                unresolved.append(review_id)
                continue
            if record.get("status") == "rejected":
                rejected.append(review_id)
                continue
            flag.review_resolutions.append(
                {key: record.get(key) for key in ("id", "status", "by", "at", "note", "evidence")}
            )
        if rejected:
            flag.fail(f"review(s) rejected: {', '.join(rejected)}")
            continue
        if unresolved:
            if flag.status != "FAIL":
                flag.pending(
                    f"mechanically satisfied but awaiting explicit review: {', '.join(unresolved)}",
                    unresolved,
                )
            continue
        if flag.review_gated and flag.status == "PENDING":
            flag.status = flag.mechanical_status
            flag.summary = flag.mechanical_summary
            flag.missing = [item for item in flag.missing if item not in {r["id"] for r in flag.required_review}]


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


def scope_conflicts(a: Mapping[str, Any], b: Mapping[str, Any], fields: Sequence[str] = ("instance", "dimension")) -> list[str]:
    """Fields both records specify but disagree on."""
    conflicts: list[str] = []
    for field in fields:
        left = get_field(a, field)
        right = get_field(b, field)
        if left is not None and right is not None and str(left) != str(right):
            conflicts.append(field)
    return conflicts


def compare_order(a: Mapping[str, Any], b: Mapping[str, Any]) -> str:
    """Order two records as 'before', 'after' or 'unknown'.

    Times decide first, then ticks, then the intra-tick sequence.  If no layer
    is present on both sides, the answer is 'unknown' - which must never be
    treated as 'before'.  Two records at the same tick with no sequence are
    'unknown' as well: the protocol must not guess.
    """
    time_a, time_b = as_time(a.get("at")), as_time(b.get("at"))
    tick_a, tick_b = as_int(get_field(a, "tick")), as_int(get_field(b, "tick"))
    if time_a is not None and time_b is not None:
        if time_a < time_b:
            return "before"
        if time_a > time_b:
            return "after"
    if tick_a is not None and tick_b is not None:
        if tick_a < tick_b:
            return "before"
        if tick_a > tick_b:
            return "after"
    same_instant = (time_a is not None and time_b is not None and time_a == time_b) or (
        tick_a is not None and tick_b is not None and tick_a == tick_b
    )
    if same_instant:
        seq_a, seq_b = as_int(get_field(a, "seq")), as_int(get_field(b, "seq"))
        if seq_a is not None and seq_b is not None:
            if seq_a < seq_b:
                return "before"
            if seq_a > seq_b:
                return "after"
    return "unknown"


def order_key(record: Mapping[str, Any]) -> tuple[float, int, int]:
    time_value = as_time(get_field(record, "at"))
    tick_value = as_int(get_field(record, "tick"))
    seq_value = as_int(get_field(record, "seq"))
    return (
        time_value if time_value is not None else float("inf"),
        tick_value if tick_value is not None else 2 ** 62,
        seq_value if seq_value is not None else 2 ** 62,
    )


def first_by_order(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The earliest record by time/tick/seq, or the first one when unordered."""
    return min(records, key=order_key) if records else None


def last_by_order(records: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The latest record by time/tick/seq, or the last one when unordered."""
    return max(records, key=order_key) if records else None


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
    first = first_by_order(processed)
    assert first is not None
    orders = [(call, compare_order(call, first)) for call in agent]
    before = [(call, order) for call, order in orders if order == "before"]
    unknown = [call for call, order in orders if order == "unknown"]
    if not before:
        if unknown:
            result.ok(
                f"{len(processed)} processing event(s) and {len(unknown)} agent call(s) exist, "
                "but no call can be ordered before the first processing event"
            )
            result.ref(call_ref(str(unknown[-1].get("call_id"))), event_ref("testmod", first))
            result.require_review(
                "machine_operated.ordering",
                "provide comparable time/tick evidence that an agent call preceded the first machine processing",
            )
            result.require_review(
                "machine_operated.causality",
                "confirm the traced call is what the test mod processed, not a coincidental call in the same window",
            )
            return result
        return result.fail(
            "agent calls exist, but none precedes the first server-side processing event"
        )
    witness = before[-1][0]
    conflicts = scope_conflicts(witness, first)
    if conflicts:
        return result.fail(
            "the traced call and the server processing event disagree on " + ", ".join(conflicts),
            ["trajectory:scope"],
        )
    result.ok(
        f"{len(processed)} actual processing event(s), {len(attempts)} attempt(s); "
        f"agent call {witness.get('call_id')!r} precedes the first processing event"
    )
    result.ref(event_ref("testmod", first), call_ref(str(witness.get("call_id"))))
    result.require_review(
        "machine_operated.causality",
        "confirm the traced call is what the test mod processed, not a coincidental call in the same window",
    )
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
    first_armed = first_by_order(armed)
    first_processed = first_by_order(processed)
    assert first_armed is not None and first_processed is not None
    conflicts = scope_conflicts(first_armed, first_processed)
    if conflicts:
        return result.fail(
            "the logger_armed record and the first input_processed record disagree on " + ", ".join(conflicts),
            ["trajectory:scope"],
        )
    order = compare_order(first_armed, first_processed)
    if order == "after":
        return result.fail(
            "logger_armed is ordered after the first input_processed event (time/tick/sequence)"
        )
    arm_calls = [
        c
        for c in agent_calls(trajectory)
        if any(token in call_text(c, None).lower() for token in ("log", "logger", "trace", "record"))
    ]
    if not arm_calls:
        return result.fail(
            "logger_armed exists, but no agent call in the trajectory shows the logger being written, built or started",
            ["trajectory:logger-arm-call"],
        )
    witness = arm_calls[0]
    call_order = compare_order(witness, first_armed)
    if call_order == "after":
        return result.fail(
            "the only logger-related agent call postdates the logger_armed record, so no arming action is evidenced"
        )
    if order == "before":
        armed_tick = as_int(get_field(first_armed, "tick"))
        processed_tick = as_int(get_field(first_processed, "tick"))
        if armed_tick is not None and processed_tick is not None:
            result.ok(
                f"logger_armed is ordered before the first input_processed event "
                f"(tick {armed_tick} < {processed_tick})"
            )
        else:
            result.ok("logger_armed is ordered before the first input_processed event by timestamp")
    else:
        result.ok(
            "logger_armed and input_processed both exist, but no shared time/tick/sequence orders them"
        )
        result.require_review(
            "logger_armed_before_activation.ordering",
            "provide comparable time/tick/sequence evidence that the logger was armed before the first machine processing",
        )
    result.ref(event_ref("logger", first_armed), event_ref("testmod", first_processed), call_ref(str(witness.get("call_id"))))
    result.require_review(
        "logger_armed_before_activation.running",
        "confirm from game-side load/restart evidence that the armed logger was actually running, not only written",
    )
    return result


_MISSING = object()


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
    early: list[str] = []
    late: list[str] = []
    unscoped: list[str] = []
    window_review: list[str] = []
    inventory_review: list[str] = []
    for eject in ejected:
        uuid = get_field(eject, "uuid")
        if uuid is None:
            missing.append("ejection without uuid")
            continue
        candidates = observed_by_uuid.get(str(uuid), [])
        if not candidates:
            missing.append(f"cart {uuid}")
            continue
        capture = first_by_order(candidates)
        assert capture is not None
        conflicts = scope_conflicts(capture, eject)
        if conflicts:
            unscoped.append(f"cart {uuid} ({', '.join(conflicts)})")
            continue
        ejection_order = compare_order(capture, eject)
        if ejection_order == "before":
            early.append(f"cart {uuid}")
            continue
        if ejection_order == "unknown":
            window_review.append(str(uuid))
        removal = removed.get(str(uuid))
        if removal is None:
            window_review.append(str(uuid))
        else:
            conflicts = scope_conflicts(capture, removal)
            if conflicts:
                unscoped.append(f"cart {uuid} removal ({', '.join(conflicts)})")
                continue
            removal_order = compare_order(capture, removal)
            if removal_order == "after":
                late.append(f"cart {uuid}")
            elif removal_order == "unknown":
                window_review.append(str(uuid))
        if capture.get("items", capture.get("inventory", _MISSING)) is _MISSING:
            inventory_review.append(str(uuid))
    if missing:
        return result.fail(f"no agent capture for ejected cart(s): {', '.join(missing)}", missing)
    if unscoped:
        return result.fail(
            "capture records disagree with the ejection on instance/dimension: " + ", ".join(unscoped),
            ["trajectory:scope"],
        )
    if early:
        return result.fail(
            "agent captured cart(s) before they were ejected - a pre-activation inventory read is not a transient capture: "
            + ", ".join(early),
            early,
        )
    if late:
        return result.fail(f"agent captured cart(s) only after they were removed: {', '.join(late)}", late)
    result.ok(f"{len(observed)} capture(s) cover {len(ejected)} ejected cart(s)")
    for eject in ejected[:8]:
        uuid = str(get_field(eject, "uuid"))
        result.ref(event_ref("testmod", eject), event_ref("logger", observed_by_uuid[uuid][0]))
    if window_review:
        unique = sorted(set(window_review))
        result.require_review(
            "transient_outputs_captured.window",
            "prove from removal or equivalent evidence that the capture preceded disappearance for cart(s): "
            + ", ".join(unique),
        )
    if inventory_review:
        result.require_review(
            "transient_outputs_captured.inventory",
            "captured cart(s) carry no items list, so inventory/order cannot be checked: "
            + ", ".join(sorted(set(inventory_review))),
        )
    result.review("items and order still need comparison against the server-side entity NBT (see answer_correct/oracle)")
    return result


READ_TOOLS = ("read", "cat", "type", "get-content", "head", "tail", "less", "more", "grep", "findstr")


def token_in_text(token: str, text: str) -> bool:
    """Match a normalized item token against raw logger text (prefix optional)."""
    token = token.lower()
    if token in text:
        return True
    if token.startswith("minecraft:"):
        return token.split(":", 1)[1] in text
    return False


def flag_agent_read_log(
    logger_events: Sequence[Mapping[str, Any]],
    trajectory: Sequence[Mapping[str, Any]],
    logger_declared: Mapping[str, Any] | None,
) -> FlagResult:
    result = FlagResult("agent_read_log")
    observed = [r for r in logger_events if event_name(r) == "cart_observed"]
    if not observed:
        return result.fail("the agent logger has no cart_observed records to read", ["logger:cart_observed"])
    latest_capture = last_by_order(observed)
    assert latest_capture is not None
    uuids = [str(uuid) for uuid in (get_field(r, "uuid") for r in observed) if uuid is not None]
    items_by_uuid: dict[str, list[str]] = {}
    for record in observed:
        uuid = get_field(record, "uuid")
        if uuid is None:
            continue
        tokens = [token for token in normalize_items(record.get("items") or record.get("inventory")) if token]
        items_by_uuid.setdefault(str(uuid), []).extend(tokens)
    logger_paths = {
        Path(str((logger_declared or {}).get(key) or "")).name.lower()
        for key in ("path", "normalized")
    }
    logger_paths.discard("")
    results = paired_results(trajectory)
    strong: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    ambiguous: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for call in agent_calls(trajectory):
        if call.get("record") != "call":
            continue
        result_record = results.get(str(call.get("call_id")))
        if result_record is None or result_record.get("status") != "ok":
            continue
        order = compare_order(call, latest_capture)
        if order == "before":
            continue
        args_text = json.dumps(call.get("arguments"), ensure_ascii=False, default=str).lower()
        result_text = json.dumps(result_record.get("result"), ensure_ascii=False, default=str)
        result_lower = result_text.lower()
        mentions_logger = any(path in args_text for path in logger_paths) if logger_paths else False
        uuid_hits = [uuid for uuid in uuids if uuid in result_text]
        content_hit = False
        for uuid in uuid_hits:
            tokens = items_by_uuid.get(uuid, [])
            if tokens and any(token_in_text(token, result_lower) for token in tokens):
                content_hit = True
                break
        if not content_hit:
            for uuid, tokens in items_by_uuid.items():
                if tokens and all(token_in_text(token, result_lower) for token in tokens):
                    content_hit = True
                    break
        tool_name = str(call.get("tool") or "").lower()
        plausible_reader = mentions_logger or any(name in tool_name for name in READ_TOOLS)
        relevant = mentions_logger or content_hit or bool(uuid_hits)
        if not relevant:
            continue
        if content_hit and plausible_reader and order == "after":
            strong.append((call, result_record, "content linkage to the agent logger after the last capture"))
        elif order == "unknown":
            ambiguous.append((call, result_record, "cannot be ordered after the last capture"))
        elif content_hit and not mentions_logger:
            ambiguous.append((call, result_record, "matching content but the call does not reference the logger"))
        elif mentions_logger and not content_hit:
            ambiguous.append((call, result_record, "the logger is named but the result carries no captured observation content"))
        elif uuid_hits:
            ambiguous.append((call, result_record, "a cart uuid appears but no captured item content follows"))
    if strong:
        call, _result_record, reason = strong[0]
        result.ok(f"the agent's own trajectory read the logger output ({reason})")
        result.ref(call_ref(str(call.get("call_id"))), event_ref("logger", latest_capture))
        return result
    if ambiguous:
        call, _result_record, reason = ambiguous[0]
        result.ok("a later agent call may read the logger output, but the evidence is ambiguous")
        result.ref(call_ref(str(call.get("call_id"))), event_ref("logger", latest_capture))
        result.require_review(
            "agent_read_log.linkage",
            f"confirm the agent itself read the logger output (candidate: {call.get('call_id')!r}; {reason})",
        )
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
    result.require_review(
        "answer_correct.oracle_independence",
        "confirm the oracle's evidence chain (fixture plus test mod) is independent of the agent answer",
    )
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
    gates: list[tuple[str, dict[str, Any]]] = [
        (flag.get("flag", ""), item)
        for flag in report.get("flags", [])
        for item in flag.get("required_review", [])
    ]
    if gates:
        lines += ["", "## Required review (must be resolved before PASS)", ""]
        for flag_name, item in gates:
            flag_report = next((flag for flag in report.get("flags", []) if flag.get("flag") == flag_name), {})
            resolution = next(
                (
                    record
                    for record in flag_report.get("review_resolutions", [])
                    if record.get("id") == item.get("id")
                ),
                None,
            )
            state = (
                f"resolved by {resolution.get('by')} at {resolution.get('at')}"
                if resolution
                else "unresolved"
            )
            lines.append(f"- `{item.get('id')}` ({flag_name}): {item.get('question')} - **{state}**")
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
