#!/usr/bin/env python3
"""Normalize peer evidence into the stage-one gate's canonical artifacts.

The stage-one gate (`tools/stage1_gate.py`) reads one canonical schema.  The
prerequisite workstreams produce their own, lossless formats:

* #18 writes a test-mod JSONL where every event carries ``seq``, ``tick``,
  ``run``, ``inst``, ``session``, ``phase`` and ``type`` (see
  ``docs/minecart-audit.md`` in the #18 PR and ``tools/minecart_audit.py``);
* #19 writes an append-only ``trajectory.jsonl`` with ``call``/``result``
  records (``docs/coldstart-protocol.md``);
* #16's live-identity evidence is a JSON report of raw PLAYER/context replies.

This tool projects those formats into the gate's artifacts **without
inventing facts**:

* every canonical record keeps the complete raw record under ``detail.raw``
  plus the source file hash;
* a required field that cannot be resolved is a *gap*: the artifact is not
  written at all, so the gate reports ``blocked`` instead of a false pass or a
  misleading ``fail``;
* ``audit`` refuses to guess a run, instance or dimension - the caller must
  pass the values the test mod was configured with, and every event that
  carries one is checked against them;
* ``trace`` only emits calls that have a matching result, computes the
  unmatched counts from the artifacts themselves (never from an assertion in a
  hand-written file), and resolves joins against the mapped audit events.

    python tools/stage1_evidence.py audit  --log audit-run.jsonl \
        --run-id rom-1 --instance-id exp-1 --dimension minecraft:overworld \
        --bundle labs/rom13-integration/bundle
    python tools/stage1_evidence.py trace --trajectory labs/.../trajectory.jsonl \
        --run-id rom-1 --instance-id exp-1 --bundle labs/rom13-integration/bundle
    python tools/stage1_evidence.py identity --source docs/evidence/rom13-meta16/live-identity.json \
        --bundle labs/rom13-integration/bundle
    python tools/stage1_evidence.py assemble --bundle labs/rom13-integration/bundle \
        --spec labs/rom13-integration/bundle-spec.json
    python tools/stage1_evidence.py selftest

See ``docs/stage1-integration.md`` for the mapping tables and the exact
remaining dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence, TextIO

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import stage1_gate as gate  # noqa: E402

SCHEMA_VERSION = 1

# canonical paths inside a gate bundle, keyed by (check_id, artifact_kind)
CANONICAL: dict[str, str] = {
    "audit_events": "artifacts/independent_test_mod/audit-events.jsonl",
    "test_mod_manifest": "artifacts/independent_test_mod/test-mod-manifest.json",
    "negative_cases": "artifacts/independent_test_mod/negative-cases.jsonl",
    "audit_lifecycle": "artifacts/independent_test_mod/audit-lifecycle.json",
    "tool_trace": "artifacts/trace_persistence/tool-trace.jsonl",
    "trace_join": "artifacts/trace_persistence/trace-join.json",
    "missing_log_detection": "artifacts/trace_persistence/missing-log-detection.jsonl",
    "identity_records": "artifacts/player_context/identity-records.jsonl",
    "entry_contract": "artifacts/player_context/entry-contract.json",
    "version_pins": "artifacts/player_context/version-pins.json",
    "fixture_manifest": "artifacts/fixture_map/fixture-manifest.json",
    "init_runs": "artifacts/fixture_map/init-runs.jsonl",
    "player_identity": "artifacts/fixture_map/player-identity.json",
    "command_block_scan": "artifacts/fixture_map/command-block-scan.json",
    "cleanup_rebuild": "artifacts/fixture_map/cleanup-rebuild.json",
    "snapshot_before": "artifacts/restore_fidelity/snapshot-before",
    "snapshot_after": "artifacts/restore_fidelity/snapshot-after",
    "restore_record": "artifacts/restore_fidelity/restore-record.json",
    "source_unchanged": "artifacts/restore_fidelity/source-unchanged.json",
    "failure_cases": "artifacts/restore_fidelity/failure-cases.jsonl",
    "tool_environment": "artifacts/agent_dev_capability/tool-environment.json",
    "jar_update": "artifacts/agent_dev_capability/jar-update.json",
    "smoke_mod": "artifacts/agent_dev_capability/smoke-mod.json",
    "instance_isolation": "artifacts/agent_dev_capability/instance-isolation.jsonl",
    "smoke_report": "artifacts/smoke_fixture_validity/smoke-report.json",
    "fixture_validity": "artifacts/smoke_fixture_validity/fixture-validity.json",
    "version_lock": "artifacts/smoke_fixture_validity/version-lock.json",
    "evidence_index": "artifacts/smoke_fixture_validity/evidence-index.json",
}

# #18 type -> gate event name (types not listed keep their own name)
AUDIT_TYPE_MAP = {"cart_exit": "cart_emitted", "cart_remove": "cart_removed"}
#: session lifecycle records carry startup/shutdown ticks (often 0) and cannot
#: be ordered inside a tick stream; they stay in the raw file (hashed) and are
#: counted, not projected into the canonical stream.
AUDIT_META_TYPES = {"session_start", "audit_ready", "audit_end", "audit_incomplete"}
# #18 phase -> gate phase
AUDIT_PHASE_MAP = {"experiment": "agent", "restore": "restore"}
GATE_PHASES = {"init", "agent", "restore"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}: line {number} is not JSON: {error}") from error
        if not isinstance(row, dict):
            raise ValueError(f"{path}: line {number} is not a JSON object")
        rows.append(row)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _vec3(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        return None
    return [float(item) for item in value]


@dataclass
class Gap:
    """One fact that could not be mapped; the artifact is withheld."""

    artifact: str
    code: str
    message: str

    def as_json(self) -> dict[str, str]:
        return {"artifact": self.artifact, "code": self.code, "message": self.message}


@dataclass
class MappingReport:
    tool: str
    source: str
    source_sha256: str
    mapped: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    excluded: dict[str, int] = field(default_factory=dict)
    lifecycle: dict[str, Any] = field(default_factory=dict)
    join_candidates: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.gaps

    def as_json(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "source": self.source,
            "source_sha256": self.source_sha256,
            "mapped": self.mapped,
            "counts": self.counts,
            "excluded": self.excluded,
            "lifecycle": self.lifecycle,
            "join_candidates": self.join_candidates,
            "gaps": [gap.as_json() for gap in self.gaps],
            "notes": self.notes,
            "ok": self.ok,
        }


# --------------------------------------------------------------------------- #18 audit


def _audit_phase(value: Any) -> str:
    if not isinstance(value, str):
        return "init"
    return AUDIT_PHASE_MAP.get(value, value if value in GATE_PHASES else "init")


def _actor_fields(row: dict[str, Any], mapped_type: str) -> tuple[dict[str, Any], list[Gap]]:
    """actor_uuid plus, for an absent actor, only a *recorded* provenance."""
    if mapped_type not in ("input_attempt", "input_processed"):
        return {}, []
    actor = row.get("operator")
    if isinstance(actor, dict):
        uuid = actor.get("uuid")
        if isinstance(uuid, str) and uuid:
            return {"actor_uuid": uuid}, []
        name = actor.get("name")
        if isinstance(name, str) and name:
            return {
                "actor_uuid": None,
                "actor_provenance": f"operator supplied without uuid (name={name})",
            }, []
        return {}, []
    if isinstance(actor, str) and actor:
        return {"actor_uuid": actor}, []
    if actor is None:
        provenance = row.get("operatorSource") or row.get("cause") or row.get("source")
        if isinstance(provenance, str) and provenance.strip():
            return {"actor_uuid": None, "actor_provenance": provenance}, []
        return {}, []
    return {}, [Gap("audit_events", "actor_type", f"operator is not a string or object: {actor!r}")]


def _epoch_of(row: dict[str, Any]) -> int | None:
    """``epoch`` is part of the cart identity; do not coerce a missing/0 value."""
    epoch = row.get("epoch")
    if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 1:
        return epoch
    return None


def _raw_run(row: dict[str, Any]) -> Any:
    return row.get("run") or row.get("runId")


def _raw_instance(row: dict[str, Any]) -> Any:
    return row.get("inst") or row.get("instanceId") or row.get("instance")


def _raw_dimension(row: dict[str, Any]) -> Any:
    return row.get("dimension") or row.get("dim")


def _link_one(
    row: dict[str, Any],
    by_session_seq: dict[tuple[str, int], dict[str, Any]],
    kind: str,
    source_field: str,
    target_field: str,
    expected_type: str,
    used: dict[tuple[str, int], Any],
    session: str,
    where: str,
) -> tuple[dict[str, Any], list[Gap]]:
    """Resolve one ``requestSeq``/``attemptSeq`` link in its own clock domain.

    A reference is only valid inside the same session/run/instance/dimension
    and strictly before the referencing event.  A reference that exists only
    in another session is a scope gap; a same-session reference that does not
    precede the referrer is a forward gap; an unknown seq is missing.
    """
    promoted: dict[str, Any] = {}
    gaps: list[Gap] = []
    value = row.get(source_field)
    if value is None:
        return promoted, gaps
    if not _is_int(value):
        gaps.append(
            Gap("audit_events", f"{kind}_missing", f"{where}: {source_field} {value!r} is not an integer seq")
        )
        return promoted, gaps
    referenced = by_session_seq.get((session, value))
    if referenced is None:
        other_sessions = sorted({key[0] for key in by_session_seq if key[1] == value})
        if other_sessions:
            gaps.append(
                Gap(
                    "audit_events",
                    f"{kind}_scope",
                    f"{where}: {source_field} {value!r} belongs to session(s) {other_sessions}, "
                    f"not {session!r}",
                )
            )
        else:
            gaps.append(
                Gap(
                    "audit_events",
                    f"{kind}_missing",
                    f"{where}: {source_field} {value!r} does not reference any event",
                )
            )
        return promoted, gaps
    if referenced.get("type") != expected_type:
        gaps.append(
            Gap(
                "audit_events",
                f"{kind}_missing",
                f"{where}: {source_field} {value!r} references a {referenced.get('type')!r}, "
                f"not {expected_type!r}",
            )
        )
        return promoted, gaps
    for label, getter in (("run", _raw_run), ("instance", _raw_instance), ("dimension", _raw_dimension)):
        own = getter(row)
        other = getter(referenced)
        if own is not None and other is not None and str(own) != str(other):
            gaps.append(
                Gap(
                    "audit_events",
                    f"{kind}_scope",
                    f"{where}: {source_field} {value!r} {label} {other!r} != {own!r}",
                )
            )
            return promoted, gaps
    row_seq = row.get("seq")
    ref_seq = referenced.get("seq")
    if not (_is_int(ref_seq) and _is_int(row_seq) and ref_seq < row_seq):
        gaps.append(
            Gap(
                "audit_events",
                f"{kind}_forward",
                f"{where}: {source_field} {value!r} does not precede seq {row_seq!r}",
            )
        )
        return promoted, gaps
    row_tick = row.get("tick")
    ref_tick = referenced.get("tick")
    if _is_int(row_tick) and _is_int(ref_tick) and ref_tick > row_tick:
        gaps.append(
            Gap(
                "audit_events",
                f"{kind}_forward",
                f"{where}: {source_field} {value!r} tick {ref_tick!r} follows tick {row_tick!r}",
            )
        )
        return promoted, gaps
    key = (session, value)
    if key in used:
        gaps.append(
            Gap(
                "audit_events",
                f"{kind}_reused",
                f"{where}: {source_field} {value!r} was already referenced at seq {used[key]!r}",
            )
        )
    used[key] = row_seq
    promoted[target_field] = value
    return promoted, gaps


def _processed_links(
    row: dict[str, Any],
    by_session_seq: dict[tuple[str, int], dict[str, Any]],
    used_requests: dict[tuple[str, int], Any],
    used_attempts: dict[tuple[str, int], Any],
    session: str,
    where: str,
) -> tuple[dict[str, Any], list[Gap]]:
    """Promote and validate ``requestSeq``/``attemptSeq``/``agentOp``."""
    promoted: dict[str, Any] = {}
    gaps: list[Gap] = []
    linked, link_gaps = _link_one(
        row, by_session_seq, "request", "requestSeq", "request_seq", "input_request", used_requests, session, where
    )
    promoted.update(linked)
    gaps.extend(link_gaps)
    linked, link_gaps = _link_one(
        row, by_session_seq, "attempt", "attemptSeq", "attempt_seq", "input_attempt", used_attempts, session, where
    )
    promoted.update(linked)
    gaps.extend(link_gaps)
    agent_op = row.get("agentOp")
    if agent_op is True and row.get("attemptSeq") is None:
        gaps.append(
            Gap(
                "audit_events",
                "agent_op_without_attempt",
                f"{where}: agentOp=true without an attemptSeq",
            )
        )
    if agent_op is not None:
        promoted["agent_op"] = agent_op
    return promoted, gaps


def _attempt_request_link(
    row: dict[str, Any],
    by_session_seq: dict[tuple[str, int], dict[str, Any]],
    used_attempt_requests: dict[tuple[str, int], Any],
    session: str,
    where: str,
) -> tuple[dict[str, Any], list[Gap]]:
    """An ``input_attempt`` may point at the request it belongs to; validate it.

    Attempt and processed references use separate reuse maps: one request is
    normally referenced by the attempt that caused it *and* by the processing
    that consumed it.  Only a second *processed* event citing the same request
    is the published #18 reuse defect.
    """
    return _link_one(
        row,
        by_session_seq,
        "request",
        "requestSeq",
        "request_seq",
        "input_request",
        used_attempt_requests,
        session,
        where,
    )


def _promoted_input_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Fields the acceptance criteria read directly; kept out of ``detail``."""
    promoted: dict[str, Any] = {}
    for source, target in (
        ("targetInput", "target_input"),
        ("note", "note"),
        ("instrument", "instrument"),
        ("source", "trigger_source"),
        ("from", "from"),
        ("to", "to"),
        ("hand", "hand"),
        ("item", "item"),
        ("result", "result"),
        ("requested", "requested"),
    ):
        value = row.get(source)
        if value is not None:
            promoted[target] = value
    operator = row.get("operator")
    if isinstance(operator, dict):
        for source, target in (("name", "operator_name"), ("uuid", "operator_uuid")):
            value = operator.get(source)
            if isinstance(value, str) and value:
                promoted[target] = value
    elif isinstance(operator, str) and operator:
        promoted["operator_uuid"] = operator
    return promoted


def map_audit_events(
    events: Sequence[dict[str, Any]],
    *,
    run_id: str,
    instance_id: str,
    dimension: str,
    source_sha256: str,
) -> tuple[list[dict[str, Any]], list[Gap], dict[str, int], dict[str, int], dict[str, Any]]:
    """Project #18 events into gate audit events.

    Returns ``(rows, gaps, counts, excluded, lifecycle)``.  Session lifecycle
    records stay in the raw file (hashed) and are validated instead of being
    silently dropped: an open session, a non-complete ``audit_end``, a
    ``truncated`` log or an ``audit_incomplete`` marker withhold the artifact.
    ``requestSeq``/``attemptSeq``/``agentOp`` are promoted and checked for
    dangling references and reuse.
    """
    gaps: list[Gap] = []
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    excluded: dict[str, int] = {}
    lifecycle: dict[str, Any] = {
        "sessions": [],
        "open_sessions": [],
        "incomplete": [],
        "truncated": False,
        "end_statuses": [],
    }
    session_state: dict[str, dict[str, Any]] = {}

    def session_of(row: dict[str, Any]) -> str:
        return str(row.get("session") or row.get("sessionId") or "legacy")

    by_session_seq: dict[tuple[str, int], dict[str, Any]] = {}
    for row in events:
        if _is_int(row.get("seq")):
            by_session_seq[(session_of(row), row["seq"])] = row

    removals: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in events:
        if row.get("type") != "cart_remove":
            continue
        epoch = _epoch_of(row)
        uuid = row.get("uuid")
        if epoch is not None and isinstance(uuid, str) and uuid:
            removals.setdefault((uuid, epoch), []).append(row)

    seen_event_ids: set[str] = set()
    used_requests: dict[tuple[str, int], Any] = {}
    used_attempts: dict[tuple[str, int], Any] = {}
    used_attempt_requests: dict[tuple[str, int], Any] = {}
    for index, row in enumerate(events):
        where = f"event[{index}]"
        raw_type = str(row.get("type") or "")
        if raw_type in AUDIT_META_TYPES:
            excluded[raw_type] = excluded.get(raw_type, 0) + 1
            session_id = session_of(row)
            state = session_state.setdefault(session_id, {"session": session_id, "started": False, "ready": False, "ended": False, "status": None})
            if raw_type == "session_start":
                state["started"] = True
            elif raw_type == "audit_ready":
                state["ready"] = True
            elif raw_type == "audit_incomplete":
                lifecycle["incomplete"].append(
                    {"session": session_id, "reason": f"audit_incomplete: {row.get('reason')!r}"}
                )
            elif raw_type == "audit_end":
                state["ended"] = True
                state["status"] = row.get("status")
                lifecycle["end_statuses"].append(row.get("status"))
                if row.get("status") != "complete":
                    lifecycle["incomplete"].append(
                        {"session": session_id, "reason": f"audit_end status={row.get('status')!r}"}
                    )
                if row.get("truncated"):
                    lifecycle["truncated"] = True
                    lifecycle["incomplete"].append({"session": session_id, "reason": "truncated log"})
            continue
        mapped_type = AUDIT_TYPE_MAP.get(raw_type, raw_type)
        seq = row.get("seq")
        tick = row.get("tick")
        if not _is_int(seq) or not _is_int(tick):
            gaps.append(Gap("audit_events", "missing_seq_tick", f"{where}: seq/tick must be integers"))
            continue
        row_run = row.get("run") or row.get("runId")
        if row_run is not None and str(row_run) != run_id:
            gaps.append(
                Gap("audit_events", "run_mismatch", f"{where}: run {row_run!r} != declared {run_id!r}")
            )
            continue
        row_inst = row.get("inst") or row.get("instanceId") or row.get("instance")
        if row_inst is not None and str(row_inst) != instance_id:
            gaps.append(
                Gap(
                    "audit_events",
                    "instance_mismatch",
                    f"{where}: instance {row_inst!r} != declared {instance_id!r}",
                )
            )
            continue
        row_dim = row.get("dimension") or row.get("dim")
        if row_dim is not None and str(row_dim) != dimension:
            gaps.append(
                Gap(
                    "audit_events",
                    "dimension_mismatch",
                    f"{where}: dimension {row_dim!r} != declared {dimension!r}",
                )
            )
            continue

        session = session_of(row)
        counts[mapped_type] = counts.get(mapped_type, 0) + 1
        event_id = f"{session}:{seq}"
        if event_id in seen_event_ids:
            gaps.append(Gap("audit_events", "duplicate_event_id", f"{where}: {event_id!r} repeats"))
            continue
        seen_event_ids.add(event_id)

        record: dict[str, Any] = {
            "event_id": event_id,
            "run_id": run_id,
            "instance_id": instance_id,
            "dimension": dimension,
            "tick": tick,
            "seq": seq,
            "event": mapped_type,
            "phase": _audit_phase(row.get("phase")),
            "detail": {
                "raw": row,
                "raw_type": raw_type,
                "raw_phase": row.get("phase"),
                "source_sha256": source_sha256,
            },
        }
        actor, actor_gaps = _actor_fields(row, mapped_type)
        record.update(actor)
        gaps.extend(actor_gaps)
        if mapped_type in ("input_attempt", "input_processed"):
            record.update(_promoted_input_fields(row))
            if mapped_type == "input_processed":
                promoted, link_gaps = _processed_links(
                    row, by_session_seq, used_requests, used_attempts, session, where
                )
                record.update(promoted)
                gaps.extend(link_gaps)
            elif mapped_type == "input_attempt" and row.get("requestSeq") is not None:
                promoted, link_gaps = _attempt_request_link(
                    row, by_session_seq, used_attempt_requests, session, where
                )
                record.update(promoted)
                gaps.extend(link_gaps)

        if mapped_type in ("cart_emitted", "cart_removed"):
            uuid = row.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                gaps.append(Gap("audit_events", "missing_cart_uuid", f"{where}: {raw_type} has no uuid"))
                continue
            epoch = _epoch_of(row)
            if epoch is None:
                gaps.append(
                    Gap("audit_events", "missing_epoch", f"{where}: {raw_type} needs an integer epoch >= 1")
                )
                continue
            record["cart_uuid"] = uuid
            record["epoch"] = epoch
        if mapped_type == "cart_emitted":
            key = (record["cart_uuid"], record["epoch"])
            captured = any(
                _is_int(candidate.get("seq"))
                and candidate["seq"] > seq
                and isinstance(candidate.get("capturedPath"), str)
                and "before_drop" in candidate["capturedPath"]
                and isinstance(candidate.get("inventory"), list)
                for candidate in removals.get(key, [])
            )
            record["captured_before_removal"] = captured
            record["detail"]["captured_from_remove_seq"] = [
                candidate.get("seq")
                for candidate in removals.get(key, [])
                if isinstance(candidate.get("capturedPath"), str)
                and "before_drop" in candidate["capturedPath"]
                and isinstance(candidate.get("inventory"), list)
            ]
        if mapped_type == "cart_removed":
            captured_path = row.get("capturedPath")
            if not isinstance(captured_path, str) or not captured_path:
                gaps.append(
                    Gap(
                        "audit_events",
                        "captured_path_missing",
                        f"{where}: cart_remove has no recorded capturedPath; a pre-drop capture cannot be proven",
                    )
                )
                continue
            record["captured_path"] = captured_path
            record["captured_before_drop"] = "before_drop" in captured_path
            record["detail"]["inventory_present"] = isinstance(row.get("inventory"), list)
            reason = row.get("reason")
            if isinstance(reason, str) and reason:
                record["removal_reason"] = reason
        pos = _vec3(row.get("pos"))
        if pos is not None:
            record["pos"] = pos
        rows.append(record)

    lifecycle["sessions"] = [session_state[key] for key in sorted(session_state)]
    for state in lifecycle["sessions"]:
        if not state.get("ended"):
            lifecycle["open_sessions"].append(state["session"])
    if session_state and lifecycle["open_sessions"]:
        gaps.append(
            Gap(
                "audit_events",
                "session_open",
                f"open session(s) without audit_end: {lifecycle['open_sessions']}",
            )
        )
    if lifecycle["incomplete"]:
        gaps.append(
            Gap(
                "audit_events",
                "audit_incomplete",
                f"{len(lifecycle['incomplete'])} incomplete/truncated marker(s): "
                f"{lifecycle['incomplete'][:3]}",
            )
        )
    if not session_state:
        gaps.append(Gap("audit_events", "missing_sessions", "no session_start/audit_end records"))
    return rows, gaps, counts, excluded, lifecycle


def cmd_audit(args: argparse.Namespace) -> int:
    log_path = Path(args.log)
    if not log_path.is_file():
        print(f"error: {log_path} is not a file", file=sys.stderr)
        return 2
    source_sha = sha256_file(log_path)
    if args.expect_sha256:
        expected = str(args.expect_sha256)
        if source_sha != expected:
            print(
                f"error: audit log sha256 {source_sha} does not match --expect-sha256 {expected}; "
                "the input is not the finalized file",
                file=sys.stderr,
            )
            return 2
    events = read_jsonl(log_path)
    bundle = Path(args.bundle)
    rows, gaps, counts, excluded, lifecycle = map_audit_events(
        events,
        run_id=args.run_id,
        instance_id=args.instance_id,
        dimension=args.dimension,
        source_sha256=source_sha,
    )
    report = MappingReport("#18 audit JSONL -> gate audit-events", str(log_path), source_sha)
    report.counts = counts
    report.excluded = excluded
    report.lifecycle = lifecycle
    report.gaps = gaps
    report.notes.append(
        f"{len(events)} raw event(s), {len(rows)} mapped, {sum(excluded.values())} session-meta validated/excluded"
    )
    out = bundle / CANONICAL["audit_events"]
    if report.ok:
        write_jsonl(out, rows)
        report.mapped.append(str(out.relative_to(bundle)))
    else:
        if out.exists():
            out.unlink()
    write_json(bundle / "mapping" / "audit-report.json", report.as_json())
    print(json.dumps(report.as_json(), indent=2) if args.json else _summary(report))
    return 0 if report.ok else 1


def map_trajectory(
    trajectory: Sequence[dict[str, Any]],
    *,
    run_id: str,
    instance_id: str,
    instance_ids: Sequence[str] | None = None,
    joins: Sequence[dict[str, Any]] | None = None,
    audit_events: Sequence[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[Gap], list[dict[str, Any]]]:
    """Pair call/result records without collapsing duplicates.

    A repeated ``call_id`` is a gap (an overlay of two attempts cannot be
    projected), a missing instance is a gap when several instances are
    declared, and a producer join is only ``verified`` when it carries explicit
    proof (producer, basis, clock).  Everything else stays in the candidate
    list and is never handed to the gate as a verified join.
    """
    gaps: list[Gap] = []
    allowed_instances = {instance_id, *(instance_ids or [])} - {""}
    require_instance = len(allowed_instances) > 1

    calls: dict[str, list[dict[str, Any]]] = {}
    results: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(trajectory):
        kind = row.get("record")
        row_run = row.get("run_id")
        if row_run is not None and str(row_run) != run_id:
            gaps.append(
                Gap(
                    "tool_trace",
                    "run_id_mismatch",
                    f"trajectory[{index}]: run_id {row_run!r} != declared {run_id!r}",
                )
            )
            continue
        call_id = row.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            if kind in ("call", "result"):
                gaps.append(Gap("tool_trace", "missing_call_id", f"trajectory[{index}]: no call_id"))
            continue
        if kind == "call":
            calls.setdefault(call_id, []).append(row)
        elif kind == "result":
            results.setdefault(call_id, []).append(row)

    for call_id, entries in calls.items():
        if len(entries) > 1:
            gaps.append(
                Gap(
                    "tool_trace",
                    "duplicate_call_id",
                    f"call {call_id!r} appears {len(entries)} times (an overlaid attempt cannot be projected)",
                )
            )
    for call_id, entries in results.items():
        if len(entries) > 1:
            gaps.append(
                Gap(
                    "tool_trace",
                    "duplicate_result",
                    f"call {call_id!r} has {len(entries)} result records",
                )
            )
    for call_id in results:
        if call_id not in calls:
            gaps.append(Gap("tool_trace", "orphan_result", f"result {call_id!r} has no call record"))

    rows: list[dict[str, Any]] = []
    for call_id, entries in calls.items():
        if len(entries) != 1:
            continue
        call = entries[0]
        result_entries = results.get(call_id, [])
        if not result_entries:
            gaps.append(Gap("tool_trace", "missing_result", f"call {call_id!r} has no result record"))
            continue
        if len(result_entries) != 1:
            continue
        result = result_entries[0]
        status = result.get("status")
        if status not in ("ok", "error", "open"):
            gaps.append(Gap("tool_trace", "bad_status", f"call {call_id!r}: status {status!r}"))
            continue
        record_instance = call.get("instance")
        instance_source = "recorded"
        if not isinstance(record_instance, str) or not record_instance:
            if require_instance:
                gaps.append(
                    Gap(
                        "tool_trace",
                        "missing_instance",
                        f"call {call_id!r}: no instance recorded and several instances are declared",
                    )
                )
                continue
            record_instance = instance_id
            instance_source = "single-declared-instance-default"
        elif allowed_instances and str(record_instance) not in allowed_instances:
            gaps.append(
                Gap(
                    "tool_trace",
                    "instance_mismatch",
                    f"call {call_id!r}: instance {record_instance!r} is not a declared instance",
                )
            )
            continue
        started = call.get("at")
        ended = result.get("at") or started
        if not (isinstance(started, str) and isinstance(ended, str)):
            gaps.append(Gap("tool_trace", "missing_time", f"call {call_id!r}: no ISO timestamp"))
            continue
        payload = result.get("result")
        if payload is None and "text" in result:
            payload = result.get("text")
        rows.append(
            {
                "call_id": call_id,
                "run_id": run_id,
                "instance_id": str(record_instance),
                "tool": str(call.get("tool") or "unknown"),
                "args": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
                "result": payload,
                "error": (str(payload) if status == "error" else None),
                "started_at": started,
                "ended_at": ended,
                "detail": {
                    "raw_call": call,
                    "raw_result": result,
                    "phase": call.get("phase"),
                    "actor": call.get("actor"),
                    "instance_source": instance_source,
                },
            }
        )

    event_by_id: dict[str, dict[str, Any]] = {}
    if audit_events is not None:
        event_by_id = {str(row.get("event_id")): row for row in audit_events}
    verified: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    resolved_event_ids: set[str] = set()
    for index, join in enumerate(joins or []):
        call_id = join.get("call_id")
        ref = join.get("audit_ref")
        candidate = {
            "call_id": call_id,
            "audit_ref": ref,
            "basis": join.get("basis") or join.get("note"),
            "verified": False,
            "problems": [],
        }
        if not isinstance(call_id, str) or not isinstance(ref, dict):
            candidate["problems"].append("needs call_id + audit_ref")
            candidates.append(candidate)
            continue
        if call_id not in calls or len(calls.get(call_id, [])) != 1:
            candidate["problems"].append(f"call {call_id!r} is not a unique trace call")
            candidates.append(candidate)
            continue
        event_id = ref.get("event_id")
        event = event_by_id.get(str(event_id))
        if audit_events is not None and event is None:
            candidate["problems"].append(f"event {event_id!r} is not in the mapped audit events")
            candidates.append(candidate)
            continue
        if event is not None:
            for field in ("run_id", "instance_id", "dimension"):
                value = join.get(field, ref.get(field))
                if value is not None and value != event.get(field):
                    candidate["problems"].append(
                        f"{field}={value!r} does not match the event value {event.get(field)!r}"
                    )
            if ref.get("tick") is not None and ref.get("tick") != event.get("tick"):
                candidate["problems"].append(
                    f"tick={ref.get('tick')!r} does not match the event tick {event.get('tick')!r}"
                )
            if event.get("phase") != "agent":
                candidate["problems"].append(f"event phase {event.get('phase')!r} is not agent")
        proof = join.get("proof")
        proof_ok = (
            isinstance(proof, dict)
            and isinstance(proof.get("producer"), str)
            and bool(proof["producer"].strip())
            and isinstance(proof.get("basis"), str)
            and bool(proof["basis"].strip())
            and isinstance(proof.get("clock"), str)
            and bool(proof["clock"].strip())
        )
        if candidate["problems"]:
            candidates.append(candidate)
            continue
        if not proof_ok:
            candidate["problems"].append(
                "no explicit proof (producer/basis/clock); kept as an unverified candidate"
            )
            candidates.append(candidate)
            continue
        full_ref = {
            "run_id": event.get("run_id", run_id),
            "instance_id": event.get("instance_id", instance_id),
            "dimension": event.get("dimension"),
            "event_id": event_id,
            "tick": event.get("tick"),
        }
        verified.append(
            {
                "call_id": call_id,
                "audit_ref": full_ref,
                "verified": True,
                "proof": proof,
                "note": join.get("note"),
            }
        )
        resolved_event_ids.add(str(event_id))

    if verified:
        joined_calls = {row["call_id"] for row in verified}
        agent_event_ids = {
            str(row.get("event_id"))
            for row in (audit_events or [])
            if row.get("phase") == "agent" and row.get("event_id")
        }
        join_doc = {
            "joins": verified,
            "unmatched_tool_calls": len(set(calls) - joined_calls),
            "unmatched_agent_events": len(agent_event_ids - resolved_event_ids),
            "derived_from": {
                "trajectory_calls": len(calls),
                "audit_agent_events": len(agent_event_ids),
                "verified_joins": len(verified),
                "unverified_candidates": len(candidates),
            },
        }
    else:
        join_doc = None
    return rows, join_doc, gaps, candidates


def cmd_trace(args: argparse.Namespace) -> int:
    trajectory_path = Path(args.trajectory)
    if not trajectory_path.is_file():
        print(f"error: {trajectory_path} is not a file", file=sys.stderr)
        return 2
    source_sha = sha256_file(trajectory_path)
    if args.expect_sha256 and source_sha != str(args.expect_sha256):
        print(
            f"error: trajectory sha256 {source_sha} does not match --expect-sha256 "
            f"{args.expect_sha256}; the input is not the finalized file",
            file=sys.stderr,
        )
        return 2
    trajectory = read_jsonl(trajectory_path)
    bundle = Path(args.bundle)
    audit_events = None
    audit_path = bundle / CANONICAL["audit_events"]
    if audit_path.is_file():
        audit_events = read_jsonl(audit_path)
    joins = None
    if args.joins:
        joins = read_json(Path(args.joins))
        if isinstance(joins, dict):
            joins = joins.get("joins")
    rows, join_doc, gaps, candidates = map_trajectory(
        trajectory,
        run_id=args.run_id,
        instance_id=args.instance_id,
        instance_ids=(args.instances or "").split(",") if args.instances else None,
        joins=joins,
        audit_events=audit_events,
    )
    report = MappingReport("#19 trajectory.jsonl -> gate tool-trace", str(trajectory_path), source_sha)
    report.counts = {
        "calls": len(rows),
        "calls_raw": sum(1 for row in trajectory if row.get("record") == "call"),
        "results_raw": sum(1 for row in trajectory if row.get("record") == "result"),
        "verified_joins": len(join_doc["joins"]) if join_doc else 0,
        "join_candidates": len(candidates),
    }
    report.gaps = gaps
    report.join_candidates = candidates
    report.notes.append(
        "duplicate call ids are gaps; verified joins require explicit producer/basis/clock proof"
    )
    if report.ok:
        trace_out = bundle / CANONICAL["tool_trace"]
        write_jsonl(trace_out, rows)
        report.mapped.append(str(trace_out.relative_to(bundle)))
        if join_doc is not None:
            join_out = bundle / CANONICAL["trace_join"]
            write_json(join_out, join_doc)
            report.mapped.append(str(join_out.relative_to(bundle)))
        else:
            report.notes.append(
                "no verified joins: trace-join.json withheld so the gate blocks instead of asserting causality"
            )
            stale = bundle / CANONICAL["trace_join"]
            if stale.exists():
                stale.unlink()
    if candidates:
        write_json(bundle / "mapping" / "join-candidates.json", {"candidates": candidates})
    write_json(bundle / "mapping" / "trace-report.json", report.as_json())
    print(json.dumps(report.as_json(), indent=2) if args.json else _summary(report))
    return 0 if report.ok else 1


def _player_fields(player: dict[str, Any]) -> dict[str, Any]:
    pos = _vec3([player.get("x"), player.get("y"), player.get("z")])
    fields: dict[str, Any] = {
        "uuid": player.get("uuid"),
        "dimension": player.get("dimension"),
        "pos": pos,
        "yaw": player.get("yaw"),
        "pitch": player.get("pitch"),
    }
    return fields


def map_identity(source: dict[str, Any]) -> tuple[dict[str, Any], list[Gap]]:
    """Extract the five gate identity cases from the #16 live-identity report."""
    gaps: list[Gap] = []
    steps = {
        str(row.get("step")): row
        for row in source.get("steps", [])
        if isinstance(row, dict) and row.get("step")
    }
    task_entry = source.get("task_entry") or {}
    user = task_entry.get("user") or {}
    bound = user.get("uuid")

    def raw_player(step: str) -> dict[str, Any] | None:
        row = steps.get(step)
        if not row:
            return None
        response = row.get("response")
        if isinstance(response, dict):
            player = response.get("player")
            if isinstance(player, dict):
                return player
        player = row.get("player")
        return player if isinstance(player, dict) else None

    alice = raw_player("raw.task-start.alice") or raw_player("raw.player.alice.uuid")
    bob = raw_player("raw.player.bob.uuid")
    miss_player = raw_player("raw.player.bob.miss")
    if not isinstance(bound, str) or not bound:
        gaps.append(Gap("identity_records", "task_uuid", "task_entry.user.uuid is missing"))
    if alice is None:
        gaps.append(Gap("identity_records", "alice", "raw.player.alice.uuid response not found"))
    if bob is None:
        gaps.append(Gap("identity_records", "bob", "raw.player.bob.uuid response not found"))
    if miss_player is None:
        gaps.append(Gap("identity_records", "miss", "raw.player.bob.miss response not found"))
    unknown_step = steps.get("raw.player.unknown.uuid") or {}
    unknown_response = unknown_step.get("response") if isinstance(unknown_step.get("response"), dict) else {}
    if not unknown_response:
        gaps.append(Gap("identity_records", "unknown", "raw.player.unknown.uuid response not found"))
    if alice is not None and isinstance(bound, str) and alice.get("uuid") != bound:
        gaps.append(
            Gap(
                "identity_records",
                "task_binding",
                f"alice uuid {alice.get('uuid')!r} != task user {bound!r}",
            )
        )
    if gaps:
        return {}, gaps

    def record(case: str, player: dict[str, Any], *, channel: str, accepted: bool, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        fields = _player_fields(player)
        row = {
            "case": case,
            "uuid": fields["uuid"],
            "viewed_uuid": fields["uuid"],
            "dimension": fields["dimension"],
            "pos": fields["pos"],
            "yaw": fields["yaw"],
            "pitch": fields["pitch"],
            "task_entry": str(task_entry.get("entry") or "external"),
            "channel": channel,
            "accepted": accepted,
            "detail": {"probe_step": extra.pop("probe_step", case) if extra else case, **(extra or {})},
        }
        return row

    records = [
        record("task_bind", alice, channel="cli", accepted=True, extra={"probe_step": "raw.task-start.alice"}),
        record("hit", alice, channel="cli", accepted=True, extra={"probe_step": "raw.player.alice.uuid"}),
        record("miss", miss_player, channel="cli", accepted=True, extra={"probe_step": "raw.player.bob.miss"}),
        {
            **record("two_players", alice, channel="cli", accepted=True, extra={"probe_step": "raw.player.alice.uuid+raw.player.bob.uuid"}),
            "other_uuid": bob.get("uuid"),
        },
        {
            "case": "unknown_identity",
            "uuid": unknown_response.get("query") or unknown_response.get("requested"),
            "viewed_uuid": unknown_response.get("query") or unknown_response.get("requested"),
            "dimension": alice.get("dimension"),
            "pos": _player_fields(alice)["pos"],
            "yaw": 0.0,
            "pitch": 0.0,
            "task_entry": str(task_entry.get("entry") or "external"),
            "channel": "cli",
            "accepted": False,
            "rejected_reason": str(
                unknown_response.get("error")
                or f"found={unknown_response.get('found')} matchedBy={unknown_response.get('matchedBy')}"
            ),
            "detail": {"probe_step": "raw.player.unknown.uuid", "raw": unknown_response},
        },
    ]
    contract = {
        "mode": "external_task",
        "fields": sorted(task_entry.keys()) or ["entry", "session", "prompt", "user"],
        "native_chat_verified": False,
        "unsupported_entries": list(source.get("not_tested_here") or []),
        "detail": {
            "entry": task_entry.get("entry"),
            "session": task_entry.get("session"),
            "note": "the fake player's /say context is timing=broadcast, not receipt-time chat",
        },
    }
    combination = source.get("combination") or {}
    interface = combination.get("mc-agent-interface-mod") or {}
    bridge = combination.get("mc-agent-bridge") or {}
    pins = {
        "interface_mod": {
            "repo": "guajun/mc-agent-interface-mod",
            "commit": interface.get("commit"),
            "tested": True,
        },
        "bridge": {
            "repo": "guajun/mc-agent-bridge",
            "commit": bridge.get("commit"),
            "tested": True,
        },
    }
    if not (isinstance(pins["interface_mod"]["commit"], str) and HEX40.fullmatch(pins["interface_mod"]["commit"])):
        gaps.append(Gap("version_pins", "interface_commit", "combination interface commit is not a 40-hex commit"))
    if not (isinstance(pins["bridge"]["commit"], str) and HEX40.fullmatch(pins["bridge"]["commit"])):
        gaps.append(Gap("version_pins", "bridge_commit", "combination bridge commit is not a 40-hex commit"))
    return {"records": records, "contract": contract, "pins": pins}, gaps


def cmd_identity(args: argparse.Namespace) -> int:
    source_path = Path(args.source)
    if not source_path.is_file():
        print(f"error: {source_path} is not a file", file=sys.stderr)
        return 2
    source = read_json(source_path)
    bundle = Path(args.bundle)
    payload, gaps = map_identity(source)
    source_sha = sha256_file(source_path)
    report = MappingReport("#16 live-identity.json -> gate player context", str(source_path), source_sha)
    report.counts = {"records": len(payload.get("records", []))}
    report.gaps = gaps
    if report.ok:
        records_out = bundle / CANONICAL["identity_records"]
        contract_out = bundle / CANONICAL["entry_contract"]
        pins_out = bundle / CANONICAL["version_pins"]
        write_jsonl(records_out, payload["records"])
        write_json(contract_out, payload["contract"])
        write_json(pins_out, payload["pins"])
        report.mapped.extend(
            [
                str(records_out.relative_to(bundle)),
                str(contract_out.relative_to(bundle)),
                str(pins_out.relative_to(bundle)),
            ]
        )
    write_json(bundle / "mapping" / "identity-report.json", report.as_json())
    print(json.dumps(report.as_json(), indent=2) if args.json else _summary(report))
    return 0 if report.ok else 1


# --------------------------------------------------------------------------- assemble


# ------------------------------------------------------------------ bridge6 restore


BRIDGE6_EVIDENCE = Path("F:/mc-agent-worktrees/rom13/bridge6/labs/evidence")


def _compare_snapshots(before: Any, after: Any) -> tuple[list[str], str, str]:
    """The gate's restore comparison, applied to two protocol snapshots."""
    problems: list[str] = []
    order_before = gate.fork_verify.order_hash(before.entities)
    order_after = gate.fork_verify.order_hash(after.entities)
    if order_before != order_after:
        problems.append(f"orderHash before={order_before} after={order_after}")
    counts_before = gate.fork_verify.type_counts(before.entities)
    counts_after = gate.fork_verify.type_counts(after.entities)
    if counts_before != counts_after:
        problems.append(f"per-type counts differ: {counts_before} vs {counts_after}")
    if len(before.entities) != len(after.entities):
        problems.append(f"entity counts {len(before.entities)} vs {len(after.entities)}")
        return problems, order_before, order_after
    for left, right in zip(before.entities, after.entities):
        if left.nbt != right.nbt:
            problems.append(f"{left.uuid}: NBT differs")
            break
        if left.pos is not None and right.pos is not None:
            if any(abs(a - b) > 1e-6 for a, b in zip(left.pos, right.pos)):
                problems.append(f"{left.uuid}: position differs")
                break
        if left.vel is not None and right.vel is not None:
            if any(abs(a - b) > 1e-6 for a, b in zip(left.vel, right.vel)):
                problems.append(f"{left.uuid}: velocity differs")
                break
    return problems, order_before, order_after


def collect_restore_evidence(evidence_dir: Path, out: Path) -> dict[str, Any]:
    """Independently verify and copy the merged bridge#6 restore evidence.

    This is a *separate* live run (bridge6-src/bridge6-dst).  It is collected
    and verified here, but it is never joined to the generic integration run:
    there is no shared tool/event clock between the two, so the gate's
    same-run binding is not claimed.
    """
    report: dict[str, Any] = {
        "tool": "bridge6 restore evidence collection",
        "evidenceDir": str(evidence_dir),
        "ok": False,
        "checks": [],
        "problems": [],
        "sameRunBinding": False,
    }
    index_path = evidence_dir / "index.json"
    if not index_path.is_file():
        report["problems"].append(f"missing {index_path}")
        return report
    index = read_json(index_path)
    repo_root = evidence_dir.parents[1]

    for stage in index.get("stages", []):
        rel = stage.get("file")
        expected = stage.get("sha256")
        path = repo_root / rel if isinstance(rel, str) else None
        if path is None or not path.is_file():
            report["problems"].append(f"stage {stage.get('stage')}: {rel} is missing")
            continue
        actual = sha256_file(path)
        report["checks"].append(
            {"check": f"stage-hash:{stage.get('stage')}", "path": str(path), "sha256": actual, "ok": actual == expected}
        )
        if actual != expected:
            report["problems"].append(f"stage {stage.get('stage')}: sha256 {actual} != {expected}")

    def list_rows(name: str) -> list[dict[str, Any]]:
        path = evidence_dir / name
        return read_json(path) if path.is_file() else []

    fork_rows = list_rows("fork.json")
    source_snapshot_dir = next(
        (Path(row["result"]["snapshotDir"]) for row in fork_rows if row.get("method") == "fork" and row.get("ok")),
        None,
    )
    after_rows = list_rows("after-reload.json")
    destination_snapshot_dir = next(
        (Path(row["result"]["snapshotDir"]) for row in after_rows if row.get("method") == "verify" and row.get("ok")),
        None,
    )
    report["sourceSnapshotDir"] = str(source_snapshot_dir)
    report["destinationSnapshotDir"] = str(destination_snapshot_dir)
    if source_snapshot_dir is None or destination_snapshot_dir is None:
        report["problems"].append("source or destination snapshot directory is missing from the evidence")
        return report

    try:
        before = gate.fork_verify.load_snapshot(source_snapshot_dir)
        after = gate.fork_verify.load_snapshot(destination_snapshot_dir)
    except gate.fork_verify.SnapshotError as error:
        report["problems"].append(f"snapshot cannot be read: {error}")
        return report
    validation = gate.fork_verify.validate_snapshot(before) + gate.fork_verify.validate_snapshot(after)
    if validation:
        report["problems"].extend(gate.fork_verify.format_issues(validation))
    compare_problems, order_before, order_after = _compare_snapshots(before, after)
    report["restoreComparison"] = {
        "orderHashBefore": order_before,
        "orderHashAfter": order_after,
        "problems": compare_problems,
    }
    if compare_problems:
        report["problems"].extend(f"restore: {problem}" for problem in compare_problems)

    # source unchanged: the source lab snapshots before the restore and after it
    snapshot_root = source_snapshot_dir.parent
    source_before_dir = snapshot_root / "source-before"
    source_after_dir = snapshot_root / "source-after-restore"
    if source_before_dir.is_dir() and source_after_dir.is_dir():
        try:
            source_before = gate.fork_verify.load_snapshot(source_before_dir)
            source_after = gate.fork_verify.load_snapshot(source_after_dir)
            source_problems, source_hash_before, source_hash_after = _compare_snapshots(source_before, source_after)
        except gate.fork_verify.SnapshotError as error:
            source_problems, source_hash_before, source_hash_after = [str(error)], "", ""
        report["sourceUnchanged"] = {
            "before": str(source_before_dir),
            "after": str(source_after_dir),
            "orderHashBefore": source_hash_before,
            "orderHashAfter": source_hash_after,
            "problems": source_problems,
        }
        if source_problems:
            report["problems"].extend(f"source unchanged: {problem}" for problem in source_problems)
    else:
        report["problems"].append("source-before/source-after-restore snapshots are missing")

    restore_rows = list_rows("restore.json")
    duplicate_rows = list_rows("duplicate.json")
    mutate_rows = list_rows("mutate.json")
    report["failureEvidence"] = {
        "wrongEndpointRejected": any(
            row.get("ok") is False and "endpoint check failed" in str(row.get("error", ""))
            for row in restore_rows
        ),
        "duplicatePreExistingRejected": any(
            row.get("ok") is False and "duplicate" in str(row.get("error", "")).lower()
            for row in duplicate_rows
        ),
        "inventoryMutationTested": any(row.get("method") in ("verify", "snapshot", "command_output") for row in mutate_rows),
        "unverifiedScenarioRows": len(list_rows("unverified.json")),
    }
    for label in ("wrongEndpointRejected", "duplicatePreExistingRejected"):
        if not report["failureEvidence"][label]:
            report["problems"].append(f"failure evidence missing: {label}")

    # copy only the protocol files the gate reads; the raw peer tree stays read-only
    out.mkdir(parents=True, exist_ok=True)
    for label, snapshot in (("snapshot-before", before), ("snapshot-after", after)):
        target = out / label
        target.mkdir(parents=True, exist_ok=True)
        for name in ("meta.json", "entities.jsonl"):
            source = snapshot.path / name
            (target / name).write_bytes(source.read_bytes())
    provenance = {
        "kind": "bridge6-restore-evidence",
        "evidenceDir": str(evidence_dir),
        "bridgeFinalHead": index.get("bridgeFinalHead"),
        "fixtureOrderHash": (index.get("fixture") or {}).get("orderHash"),
        "sourceSnapshotDir": str(source_snapshot_dir),
        "destinationSnapshotDir": str(destination_snapshot_dir),
        "interfaceSha256": (index.get("interface") or {}).get("jarSha256"),
        "copied": {
            "snapshot-before": sha256_file(out / "snapshot-before" / "entities.jsonl"),
            "snapshot-after": sha256_file(out / "snapshot-after" / "entities.jsonl"),
        },
        "sameRunBinding": False,
        "note": "separate live run; not joined to the generic integration run",
    }
    write_json(out / "provenance.json", provenance)
    report["copied"] = provenance["copied"]
    report["gateCompatibility"] = {
        "snapshotStateComparison": "ok" if not compare_problems else "problems",
        "sameRunBinding": False,
        "withheld": [
            "restore_record bound to one declared run",
            "failure_cases covering all six gate cases",
            "source_unchanged bound to the gate run's declared source",
        ],
    }
    report["ok"] = not report["problems"]
    return report


def cmd_restore_evidence(args: argparse.Namespace) -> int:
    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_dir():
        print(f"error: {evidence_dir} is not a directory", file=sys.stderr)
        return 2
    report = collect_restore_evidence(evidence_dir, Path(args.out))
    write_json(Path(args.out) / "collection-report.json", report)
    print(json.dumps(report, indent=2) if args.json else _restore_summary(report))
    return 0 if report["ok"] else 1


def _restore_summary(report: dict[str, Any]) -> str:
    lines = [
        f"bridge6 restore collection: {'ok' if report['ok'] else 'PROBLEMS'}",
        f"  evidence {report.get('evidenceDir')}",
    ]
    for check in report.get("checks", []):
        lines.append(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['check']}")
    comparison = report.get("restoreComparison") or {}
    if comparison:
        lines.append(
            f"  restore order {comparison.get('orderHashBefore')} -> {comparison.get('orderHashAfter')} "
            f"({len(comparison.get('problems') or [])} problem(s))"
        )
    source = report.get("sourceUnchanged") or {}
    if source:
        lines.append(
            f"  source unchanged {source.get('orderHashBefore')} -> {source.get('orderHashAfter')} "
            f"({len(source.get('problems') or [])} problem(s))"
        )
    for problem in report.get("problems", []):
        lines.append(f"  problem: {problem}")
    return "\n".join(lines)


def assemble(
    bundle: Path,
    spec: dict[str, Any],
    *,
    run_gate: bool = True,
    source_world_override: str | None = None,
) -> dict[str, Any]:
    """Write bundle.json + evidence-index.json from the artifacts present."""
    run = spec.get("run")
    if not isinstance(run, dict):
        raise ValueError("spec.run must be an object")
    checks: dict[str, Any] = {}
    declared: list[str] = []
    for check_spec in gate.CHECK_SPECS:
        if not check_spec.artifacts:
            continue
        evidence: dict[str, Any] = {}
        for kind, artifact in check_spec.artifacts.items():
            canonical = CANONICAL.get(kind)
            if canonical is None or kind == "evidence_index":
                continue
            path = bundle / canonical
            if path.is_file() or path.is_dir():
                evidence[kind] = canonical
                declared.append(canonical)
        if evidence:
            checks[check_spec.id] = {"evidence": evidence}
    manifest = {
        "schema_version": gate.SCHEMA_VERSION,
        "kind": gate.BUNDLE_KIND,
        "origin": spec.get("origin", "live"),
        "generated_at": spec.get("generated_at") or gate._now_iso(),
        "run": run,
        "checks": checks,
    }
    write_json(bundle / "bundle.json", manifest)

    entries: list[dict[str, Any]] = []
    for rel in sorted(set(declared)):
        path = bundle / rel
        if path.is_dir():
            digest, files, total = gate.tree_hash(path)
            entries.append({"path": rel, "tree_sha256": digest, "files": files, "bytes": total})
        else:
            entries.append({"path": rel, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    index_rel = CANONICAL["evidence_index"]
    write_json(
        bundle / index_rel,
        {"tool": "tools/stage1_evidence.py assemble", "entries": entries},
    )
    report: dict[str, Any] = {
        "bundle": str(bundle),
        "declared": sorted(set(declared)),
        "index_entries": len(entries),
    }
    if run_gate:
        gate_report = gate.run_gate(bundle, source_world=source_world_override)
        report["gate"] = gate_report.to_json()
        report["gate_exit_code"] = gate._exit_code(gate_report)
    return report


def cmd_assemble(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle)
    spec_path = Path(args.spec)
    if not spec_path.is_file():
        print(f"error: {spec_path} is not a file", file=sys.stderr)
        return 2
    spec = read_json(spec_path)
    report = assemble(
        bundle,
        spec,
        run_gate=not args.no_gate,
        source_world_override=args.source_world,
    )
    out = bundle / "assemble-report.json"
    write_json(out, report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"assembled {bundle}: {len(report['declared'])} declared artifact path(s)")
        if "gate" in report:
            print(f"gate: {report['gate']['overall']} (exit {report['gate_exit_code']})")
            for check in report["gate"]["checks"]:
                print(f"  [{check['status'].upper()}] {check['id']}")
    return report.get("gate_exit_code", 0)


# --------------------------------------------------------------------------- CLI


def _summary(report: MappingReport) -> str:
    lines = [
        f"{report.tool}: {'ok' if report.ok else 'GAPS'}",
        f"  source {report.source} sha256={report.source_sha256[:16]}",
        f"  mapped {', '.join(report.mapped) or '(none)'}",
    ]
    for gap in report.gaps:
        lines.append(f"  gap [{gap.code}] {gap.artifact}: {gap.message}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stage1_evidence.py",
        description="Losslessly normalize #16/#18/#19 evidence into the stage-one gate schema.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="#18 minecart-audit JSONL -> gate audit events")
    audit.add_argument("--log", required=True, help="raw audit-<run>.jsonl")
    audit.add_argument("--bundle", required=True, help="gate bundle root")
    audit.add_argument("--run-id", required=True)
    audit.add_argument("--instance-id", required=True)
    audit.add_argument("--dimension", required=True)
    audit.add_argument("--expect-sha256", help="refuse to project unless the log matches this finalized hash")
    audit.add_argument("--exclude-before", help=argparse.SUPPRESS)
    audit.add_argument("--json", action="store_true")
    audit.set_defaults(func=cmd_audit)

    trace = sub.add_parser("trace", help="#19 trajectory.jsonl -> gate tool trace")
    trace.add_argument("--trajectory", required=True)
    trace.add_argument("--bundle", required=True)
    trace.add_argument("--run-id", required=True)
    trace.add_argument("--instance-id", required=True)
    trace.add_argument("--instances", help="comma-separated declared instance ids for multi-instance traces")
    trace.add_argument("--joins", help="optional JSON list of {call_id, audit_ref} joins")
    trace.add_argument("--expect-sha256", help="refuse to project unless the trajectory matches this finalized hash")
    trace.add_argument("--json", action="store_true")
    trace.set_defaults(func=cmd_trace)

    identity = sub.add_parser("identity", help="#16 live-identity report -> gate player context")
    identity.add_argument("--source", required=True)
    identity.add_argument("--bundle", required=True)
    identity.add_argument("--json", action="store_true")
    identity.set_defaults(func=cmd_identity)

    assemble_cmd = sub.add_parser("assemble", help="write bundle.json + index and run the gate")
    assemble_cmd.add_argument("--bundle", required=True)
    assemble_cmd.add_argument("--spec", required=True)
    assemble_cmd.add_argument("--source-world", help="read-only source save path for the re-hash")
    assemble_cmd.add_argument("--no-gate", action="store_true")
    assemble_cmd.add_argument("--json", action="store_true")
    assemble_cmd.set_defaults(func=cmd_assemble)

    restore = sub.add_parser("restore-evidence", help="independently verify and copy the merged bridge#6 restore evidence")
    restore.add_argument("--evidence-dir", default=str(BRIDGE6_EVIDENCE))
    restore.add_argument("--out", required=True)
    restore.add_argument("--json", action="store_true")
    restore.set_defaults(func=cmd_restore_evidence)

    sub.add_parser("selftest", help="mapping tests (no game, no live evidence)")
    return parser


def cli(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    stream = out if out is not None else sys.stdout
    args = build_parser().parse_args(argv)
    if args.command == "selftest":
        return run_selftest(stream)
    return int(args.func(args))


# --------------------------------------------------------------------------- selftest


class _Selftest:
    def __init__(self, out: TextIO):
        self.out = out
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            self.out.write(f"PASS  {name}\n")
        else:
            self.failures.append(name)
            self.out.write(f"FAIL  {name}  <- {detail}\n")

    def equal(self, name: str, got: Any, want: Any) -> None:
        self.check(name, got == want, f"got {got!r}, want {want!r}")


def _fixture_audit() -> list[dict[str, Any]]:
    return [
        {"seq": 1, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "session_start"},
        {"seq": 2, "tick": 0, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "audit_ready", "config": {"dimension": "minecraft:overworld"}},
        {"seq": 3, "tick": 10, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "init", "type": "phase", "from": "bootstrap", "to": "init"},
        {"seq": 4, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "aaaa", "name": "Bot"}, "from": [0, 0, 0], "to": [0, 0, 0]},
        {"seq": 5, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
        {"seq": 6, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "targetInput": True, "agentOp": True, "requestSeq": 5, "attemptSeq": 4, "note": 0, "instrument": "harp"},
        {"seq": 7, "tick": 30, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_tracked", "uuid": "cart-1", "epoch": 1, "inventory": []},
        {"seq": 8, "tick": 40, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_exit", "uuid": "cart-1", "epoch": 1, "pos": [2.5, 60.0, 0.5]},
        {"seq": 9, "tick": 80, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_remove", "uuid": "cart-1", "epoch": 1, "reason": "DISCARDED", "pos": [2.5, -70.0, 0.5], "capturedPath": "minecart_container_remove_before_drop", "inventory": [{"slot": 0, "id": "minecraft:stone", "count": 3}]},
        {"seq": 10, "tick": 90, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_end", "status": "complete", "truncated": False},
    ]


def _fixture_trajectory() -> list[dict[str, Any]]:
    return [
        {"record": "call", "call_id": "c1", "tool": "bash", "arguments": {"command": "status"}, "at": "2026-09-26T00:00:00Z", "phase": "prepare", "actor": "operator", "instance": "exp-1"},
        {"record": "result", "call_id": "c1", "status": "ok", "result": {"text": "up"}, "at": "2026-09-26T00:00:01Z"},
        {"record": "call", "call_id": "c2", "tool": "write", "arguments": {"path": "notes.md"}, "at": "2026-09-26T00:00:02Z", "phase": "prepare", "actor": "operator", "instance": "src-1"},
        {"record": "result", "call_id": "c2", "status": "ok", "result": "written", "at": "2026-09-26T00:00:03Z"},
        {"record": "call", "call_id": "c3", "tool": "git", "arguments": {"args": ["rev-parse", "HEAD"]}, "at": "2026-09-26T00:00:04Z", "phase": "prepare", "actor": "operator", "instance": "src-1"},
        {"record": "result", "call_id": "c3", "status": "ok", "result": "abc", "at": "2026-09-26T00:00:05Z"},
        {"record": "call", "call_id": "c4", "tool": "mcp_probe", "arguments": {"tool": "mc_state"}, "at": "2026-09-26T00:00:06Z", "phase": "prepare", "actor": "operator", "instance": "exp-1"},
        {"record": "result", "call_id": "c4", "status": "ok", "result": {"tick": 21}, "at": "2026-09-26T00:00:07Z"},
    ]


def _map_audit(events=None):
    return map_audit_events(
        events if events is not None else _fixture_audit(),
        run_id="r1",
        instance_id="exp-1",
        dimension="minecraft:overworld",
        source_sha256="0" * 64,
    )


def run_selftest(out: TextIO | None = None) -> int:
    stream = out if out is not None else sys.stdout
    test = _Selftest(stream)
    with tempfile.TemporaryDirectory(prefix="stage1-evidence-selftest-") as tmp:
        base = Path(tmp)

        # audit positives ---------------------------------------------------------
        rows, gaps, counts, excluded, lifecycle = _map_audit()
        test.check("audit maps without gaps", not gaps, str([g.as_json() for g in gaps]))
        by_type = {str(row["event"]) for row in rows}
        test.check("audit maps cart_exit to cart_emitted", "cart_emitted" in by_type)
        test.check("audit maps cart_remove to cart_removed", "cart_removed" in by_type)
        emitted = next(row for row in rows if row["event"] == "cart_emitted")
        test.check("audit capture-before-removal uses capturedPath", emitted.get("captured_before_removal") is True)
        processed = next(row for row in rows if row["event"] == "input_processed")
        test.equal("audit keeps the raw event", processed["detail"]["raw"]["type"], "input_processed")
        test.equal("audit maps phase experiment -> agent", processed["phase"], "agent")
        test.equal("audit event id", processed["event_id"], "s1:6")
        test.equal("audit promotes requestSeq", processed.get("request_seq"), 5)
        test.equal("audit promotes attemptSeq", processed.get("attempt_seq"), 4)
        test.equal("audit promotes agentOp", processed.get("agent_op"), True)
        test.equal("audit promotes operator name", processed.get("operator_name"), "Bot")
        test.equal("audit lifecycle end status", lifecycle["end_statuses"], ["complete"])
        test.equal("audit session meta excluded", excluded, {"session_start": 1, "audit_ready": 1, "audit_end": 1})
        removed = next(row for row in rows if row["event"] == "cart_removed")
        test.equal("audit promotes capturedPath", removed.get("captured_path"), "minecart_container_remove_before_drop")
        test.check("audit marks before-drop", removed.get("captured_before_drop") is True)

        # audit negatives ---------------------------------------------------------
        no_end = [row for row in _fixture_audit() if row.get("type") != "audit_end"]
        _, gaps, _, _, lifecycle = _map_audit(no_end)
        test.check("open session is a gap", any(gap.code == "session_open" for gap in gaps))
        test.equal("open session recorded", lifecycle["open_sessions"], ["s1"])
        incomplete = [dict(row) for row in _fixture_audit()]
        incomplete.append({"seq": 11, "tick": 91, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_incomplete", "reason": "hook error"})
        _, gaps, _, _, _ = _map_audit(incomplete)
        test.check("audit_incomplete is a gap", any(gap.code == "audit_incomplete" for gap in gaps))
        truncated = [dict(row) for row in _fixture_audit()]
        truncated[-1]["truncated"] = True
        _, gaps, _, _, _ = _map_audit(truncated)
        test.check("truncated log is a gap", any(gap.code == "audit_incomplete" for gap in gaps))
        no_capture = [dict(row) for row in _fixture_audit()]
        no_capture[-2].pop("capturedPath")
        _, gaps, _, _, _ = _map_audit(no_capture)
        test.check("missing capturedPath is a gap", any(gap.code == "captured_path_missing" for gap in gaps))
        after_drop = [dict(row) for row in _fixture_audit()]
        after_drop[-2]["capturedPath"] = "minecart_container_remove_after_drop"
        after_rows, after_gaps, _, _, _ = _map_audit(after_drop)
        after_emitted = next(row for row in after_rows if row["event"] == "cart_emitted")
        test.check("after-drop capture is not before-removal", after_emitted.get("captured_before_removal") is False)
        test.check("after-drop path has no gap", not after_gaps, str([g.as_json() for g in after_gaps]))
        no_epoch = [dict(row) for row in _fixture_audit()]
        no_epoch[-3].pop("epoch")
        _, gaps, _, _, _ = _map_audit(no_epoch)
        test.check("missing epoch is a gap", any(gap.code == "missing_epoch" for gap in gaps))
        reuse = [dict(row) for row in _fixture_audit()]
        reuse.append({"seq": 11, "tick": 22, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "aaaa", "name": "Bot"}, "requestSeq": 5, "attemptSeq": 4, "agentOp": True})
        _, gaps, _, _, _ = _map_audit(reuse)
        test.check("request reuse is a gap", any(gap.code == "request_reused" for gap in gaps))
        test.check("attempt reuse is a gap", any(gap.code == "attempt_reused" for gap in gaps))
        dangling = [dict(row) for row in _fixture_audit()]
        next(row for row in dangling if row["type"] == "input_processed")["attemptSeq"] = 999
        _, gaps, _, _, _ = _map_audit(dangling)
        test.check("dangling attemptSeq is a gap", any(gap.code == "attempt_missing" for gap in gaps))
        no_attempt = [dict(row) for row in _fixture_audit()]
        processed_row = next(row for row in no_attempt if row["type"] == "input_processed")
        processed_row.pop("attemptSeq")
        _, gaps, _, _, _ = _map_audit(no_attempt)
        test.check("agentOp without attemptSeq is a gap", any(gap.code == "agent_op_without_attempt" for gap in gaps))
        cross_session = [
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
        _, gaps, _, _, _ = _map_audit(cross_session)
        test.check("cross-session request is a scope gap", any(gap.code == "request_scope" for gap in gaps))
        test.check("cross-session attempt is a scope gap", any(gap.code == "attempt_scope" for gap in gaps))
        forward = _fixture_audit() + [
            {"seq": 12, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
            {"seq": 13, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "bbbb", "name": "Bot2"}},
            {"seq": 11, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": {"uuid": "bbbb", "name": "Bot2"}, "requestSeq": 12, "attemptSeq": 13, "agentOp": True},
        ]
        _, gaps, _, _, _ = _map_audit(forward)
        test.check("forward request is a gap", any(gap.code == "request_forward" for gap in gaps))
        test.check("forward attempt is a gap", any(gap.code == "attempt_forward" for gap in gaps))
        identity_scope = _fixture_audit()
        next(row for row in identity_scope if row["type"] == "input_attempt")["inst"] = "other-lab"
        _, gaps, _, _, _ = _map_audit(identity_scope)
        test.check("referenced identity must match", any(gap.code == "attempt_scope" for gap in gaps))
        attempt_link = _fixture_audit() + [
            {"seq": 11, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
            {"seq": 12, "tick": 22, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": {"uuid": "bbbb", "name": "Bot2"}, "requestSeq": 11},
        ]
        attempt_rows, attempt_gaps, _, _, _ = _map_audit(attempt_link)
        test.check("attempt request link is clean", not attempt_gaps, str([g.as_json() for g in attempt_gaps]))
        linked_attempt = next(row for row in attempt_rows if row["event"] == "input_attempt" and row["detail"]["raw"].get("requestSeq") == 11)
        test.equal("attempt request link is promoted", linked_attempt.get("request_seq"), 11)
        foreign = [dict(row) for row in _fixture_audit()]
        foreign[3]["run"] = "other"
        _, gaps, _, _, _ = _map_audit(foreign)
        test.check("audit rejects foreign run", any(gap.code == "run_mismatch" for gap in gaps))

        # trace positives ---------------------------------------------------------
        trace_rows, join_doc, trace_gaps, candidates = map_trajectory(
            _fixture_trajectory(), run_id="r1", instance_id="exp-1", instance_ids=["src-1"], joins=None, audit_events=rows
        )
        test.check("trace maps without gaps", not trace_gaps, str([g.as_json() for g in trace_gaps]))
        test.equal("trace row count", len(trace_rows), 4)
        test.equal("trace categories present", {row["tool"] for row in trace_rows}, {"bash", "write", "git", "mcp_probe"})
        test.equal("trace keeps explicit instances", {row["instance_id"] for row in trace_rows}, {"exp-1", "src-1"})
        test.check("no joins means no trace-join doc", join_doc is None)

        # trace negatives ---------------------------------------------------------
        duplicated = _fixture_trajectory() + [dict(_fixture_trajectory()[0])]
        _, _, trace_gaps, _ = map_trajectory(
            duplicated, run_id="r1", instance_id="exp-1", instance_ids=None, joins=None, audit_events=rows
        )
        test.check("duplicate call_id is a gap", any(gap.code == "duplicate_call_id" for gap in trace_gaps))
        orphan = [dict(row) for row in _fixture_trajectory()]
        orphan.append({"record": "result", "call_id": "c9", "status": "ok", "at": "2026-09-26T00:00:09Z"})
        _, _, trace_gaps, _ = map_trajectory(
            orphan, run_id="r1", instance_id="exp-1", instance_ids=None, joins=None, audit_events=rows
        )
        test.check("orphan result is a gap", any(gap.code == "orphan_result" for gap in trace_gaps))
        missing_result = [row for row in _fixture_trajectory() if not (row.get("record") == "result" and row.get("call_id") == "c2")]
        _, _, trace_gaps, _ = map_trajectory(
            missing_result, run_id="r1", instance_id="exp-1", instance_ids=None, joins=None, audit_events=rows
        )
        test.check("call without result is a gap", any(gap.code == "missing_result" for gap in trace_gaps))
        no_instance = [dict(row) for row in _fixture_trajectory()]
        for row in no_instance:
            if row.get("record") == "call":
                row.pop("instance")
        _, _, trace_gaps, _ = map_trajectory(
            no_instance, run_id="r1", instance_id="exp-1", instance_ids=["src-1"], joins=None, audit_events=rows
        )
        test.check("missing instance with several declared is a gap", any(gap.code == "missing_instance" for gap in trace_gaps))
        single_rows, _, single_gaps, _ = map_trajectory(
            no_instance, run_id="r1", instance_id="exp-1", instance_ids=None, joins=None, audit_events=rows
        )
        test.check("single declared instance may default", not single_gaps)
        test.equal(
            "defaulted instance is marked",
            {row["detail"]["instance_source"] for row in single_rows},
            {"single-declared-instance-default"},
        )
        wrong_run = [dict(row) for row in _fixture_trajectory()]
        wrong_run[0]["run_id"] = "other"
        _, _, trace_gaps, _ = map_trajectory(
            wrong_run, run_id="r1", instance_id="exp-1", instance_ids=None, joins=None, audit_events=rows
        )
        test.check("trajectory run_id mismatch is a gap", any(gap.code == "run_id_mismatch" for gap in trace_gaps))

        # joins: verified requires explicit proof ---------------------------------
        unproven = [{"call_id": "c4", "run_id": "r1", "instance_id": "exp-1", "dimension": "minecraft:overworld", "audit_ref": {"event_id": "s1:6", "tick": 21}, "basis": "nearest call"}]
        _, join_doc, join_gaps, candidates = map_trajectory(
            _fixture_trajectory(), run_id="r1", instance_id="exp-1", instance_ids=["src-1"], joins=unproven, audit_events=rows
        )
        test.check("unproven join has no gaps", not join_gaps)
        test.check("unproven join is not verified", join_doc is None)
        test.check("unproven join is a candidate", len(candidates) == 1 and not candidates[0]["verified"])
        proven = [dict(unproven[0])]
        proven[0]["proof"] = {"producer": "integration driver", "basis": "requestSeq 5 -> attemptSeq 4", "clock": "tick 21"}
        _, join_doc, join_gaps, candidates = map_trajectory(
            _fixture_trajectory(), run_id="r1", instance_id="exp-1", instance_ids=["src-1"], joins=proven, audit_events=rows
        )
        test.check("proven join has no gaps", not join_gaps)
        test.check("proven join is verified", join_doc is not None and len(join_doc["joins"]) == 1)
        test.equal("verified join unmatched agent events", join_doc["unmatched_agent_events"], 5)
        test.check("proven join leaves no candidates", candidates == [])
        bogus = [{"call_id": "c4", "audit_ref": {"tick": 21, "event_id": "nope"}, "proof": {"producer": "x", "basis": "y", "clock": "z"}}]
        _, join_doc, _, candidates = map_trajectory(
            _fixture_trajectory(), run_id="r1", instance_id="exp-1", instance_ids=["src-1"], joins=bogus, audit_events=rows
        )
        test.check("unknown join event stays unverified", join_doc is None and len(candidates) == 1)

        # #16 identity ------------------------------------------------------------
        evidence = ROOT / "docs/evidence/rom13-meta16/live-identity.json"
        if evidence.is_file():
            payload, identity_gaps = map_identity(read_json(evidence))
            test.check("real #16 identity maps", not identity_gaps, str([g.as_json() for g in identity_gaps]))
            if not identity_gaps:
                cases = {row["case"] for row in payload["records"]}
                test.equal(
                    "identity covers the five gate cases",
                    cases,
                    {"task_bind", "hit", "miss", "two_players", "unknown_identity"},
                )
                task_bind = next(row for row in payload["records"] if row["case"] == "task_bind")
                test.check("identity binds to task user", task_bind["uuid"] == "f0a8f4ba-99f5-412a-9189-db832c934913")
                test.check("identity records a version pin", payload["pins"]["interface_mod"]["commit"] is not None)
        else:
            test.check("real #16 identity evidence present", False, f"missing {evidence}")

        # assemble ----------------------------------------------------------------
        bundle = base / "bundle"
        write_jsonl(bundle / CANONICAL["audit_events"], rows)
        write_jsonl(bundle / CANONICAL["tool_trace"], trace_rows)
        spec = {
            "run": {
                "run_id": "r1",
                "issue": "guajun/mc-agent#14",
                "allowed_port_ranges": ["27240-27249"],
                "child_runs": [{"run_id": f"init-{i}", "instance_id": "exp-1"} for i in range(1, 4)],
                "source_world": {"label": "x", "path": str(base / "world"), "before_tree_sha256": "1" * 64, "after_tree_sha256": "1" * 64},
                "instances": [
                    {"instance_id": "src-1", "role": "source_audit", "dimension": "minecraft:overworld", "world_dir": "labs/src/world", "rcon_port": 27240, "bridge_port": 27241},
                    {"instance_id": "exp-1", "role": "experiment", "dimension": "minecraft:overworld", "world_dir": "labs/exp/world", "rcon_port": 27242, "bridge_port": 27243},
                ],
            }
        }
        report = assemble(bundle, spec, run_gate=False)
        test.check("assemble declares mapped artifacts", report["index_entries"] >= 2)
        index = read_json(bundle / CANONICAL["evidence_index"])
        index_paths = {entry["path"] for entry in index["entries"]}
        test.check("assemble index covers audit events", CANONICAL["audit_events"] in index_paths)
        test.check("assemble index covers tool trace", CANONICAL["tool_trace"] in index_paths)
        test.check("assemble index never pins itself", CANONICAL["evidence_index"] not in index_paths)

    if test.failures:
        stream.write(f"selftest FAILED: {len(test.failures)} of {test.total} check(s): {', '.join(test.failures)}\n")
        return 1
    stream.write(f"selftest OK: {test.total} check(s) passed (no game, no live evidence)\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
