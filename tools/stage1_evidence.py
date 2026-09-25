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


def map_audit_events(
    events: Sequence[dict[str, Any]],
    *,
    run_id: str,
    instance_id: str,
    dimension: str,
    source_sha256: str,
) -> tuple[list[dict[str, Any]], list[Gap], dict[str, int], dict[str, int]]:
    """Project #18 events into gate audit events.

    Returns (rows, gaps, counts, excluded).  Session lifecycle records are
    excluded from the canonical tick stream and counted instead; the raw file
    is retained and hashed.
    """
    gaps: list[Gap] = []
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    excluded: dict[str, int] = {}

    removals: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for row in events:
        if row.get("type") == "cart_remove":
            key = (str(row.get("uuid")), int(row.get("epoch") or 1))
            removals.setdefault(key, []).append(row)

    seen_event_ids: set[str] = set()
    for index, row in enumerate(events):
        where = f"event[{index}]"
        raw_type = str(row.get("type") or "")
        if raw_type in AUDIT_META_TYPES:
            excluded[raw_type] = excluded.get(raw_type, 0) + 1
            continue
        mapped_type = AUDIT_TYPE_MAP.get(raw_type, raw_type)
        counts[mapped_type] = counts.get(mapped_type, 0) + 1
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

        session = row.get("session") or row.get("sessionId") or "legacy"
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

        if mapped_type in ("cart_emitted", "cart_removed"):
            uuid = row.get("uuid")
            if not isinstance(uuid, str) or not uuid:
                gaps.append(Gap("audit_events", "missing_cart_uuid", f"{where}: {raw_type} has no uuid"))
                continue
            record["cart_uuid"] = uuid
        if mapped_type == "cart_emitted":
            key = (record.get("cart_uuid", ""), int(row.get("epoch") or 1))
            captured = any(
                _is_int(candidate.get("seq"))
                and candidate.get("seq") > seq
                and isinstance(candidate.get("inventory"), list)
                for candidate in removals.get(key, [])
            )
            record["captured_before_removal"] = captured
        if mapped_type == "cart_removed":
            reason = row.get("reason")
            if isinstance(reason, str) and reason:
                record["removal_reason"] = reason
        pos = _vec3(row.get("pos"))
        if pos is not None:
            record["pos"] = pos
        rows.append(record)
    return rows, gaps, counts, excluded


def cmd_audit(args: argparse.Namespace) -> int:
    log_path = Path(args.log)
    if not log_path.is_file():
        print(f"error: {log_path} is not a file", file=sys.stderr)
        return 2
    events = read_jsonl(log_path)
    bundle = Path(args.bundle)
    source_sha = sha256_file(log_path)
    rows, gaps, counts, excluded = map_audit_events(
        events,
        run_id=args.run_id,
        instance_id=args.instance_id,
        dimension=args.dimension,
        source_sha256=source_sha,
    )
    report = MappingReport("#18 audit JSONL -> gate audit-events", str(log_path), source_sha)
    report.counts = counts
    report.excluded = excluded
    report.gaps = gaps
    report.notes.append(f"{len(events)} raw event(s), {len(rows)} mapped, {sum(excluded.values())} session-meta excluded")
    if args.exclude_before is not None:
        report.notes.append("filtering is intentionally not applied: lossless means every event")
        _ = args.exclude_before
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


# --------------------------------------------------------------------------- #19 trajectory


def map_trajectory(
    trajectory: Sequence[dict[str, Any]],
    *,
    run_id: str,
    instance_id: str,
    instance_ids: Sequence[str] | None = None,
    joins: Sequence[dict[str, Any]] | None = None,
    audit_events: Sequence[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Gap]]:
    """Pair call/result records; compute joins and unmatched counts from facts."""
    gaps: list[Gap] = []
    allowed_instances = {instance_id, *(instance_ids or [])} - {""}
    calls: dict[str, dict[str, Any]] = {}
    results: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(trajectory):
        kind = row.get("record")
        if kind == "call":
            call_id = row.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                gaps.append(Gap("tool_trace", "missing_call_id", f"trajectory[{index}]: no call_id"))
                continue
            calls[call_id] = row
        elif kind == "result":
            call_id = row.get("call_id")
            if isinstance(call_id, str) and call_id:
                results[call_id] = row

    rows: list[dict[str, Any]] = []
    for call_id, call in calls.items():
        result = results.get(call_id)
        if result is None:
            gaps.append(Gap("tool_trace", "missing_result", f"call {call_id!r} has no result record"))
            continue
        status = result.get("status")
        if status not in ("ok", "error", "open"):
            gaps.append(Gap("tool_trace", "bad_status", f"call {call_id!r}: status {status!r}"))
            continue
        record_instance = call.get("instance") or instance_id
        if str(record_instance) not in allowed_instances:
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
        record_instance = call.get("instance") or instance_id
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
                },
            }
        )

    join_rows: list[dict[str, Any]] = []
    event_by_id: dict[str, dict[str, Any]] = {}
    if audit_events is not None:
        event_by_id = {str(row.get("event_id")): row for row in audit_events}
    for index, join in enumerate(joins or []):
        call_id = join.get("call_id")
        ref = join.get("audit_ref")
        if not isinstance(call_id, str) or not isinstance(ref, dict):
            gaps.append(Gap("trace_join", "malformed_join", f"joins[{index}]: needs call_id + audit_ref"))
            continue
        if calls and call_id not in calls:
            gaps.append(Gap("trace_join", "unknown_call", f"joins[{index}]: call {call_id!r} not in trace"))
            continue
        event_id = ref.get("event_id")
        event = event_by_id.get(str(event_id))
        if audit_events is not None and event is None:
            gaps.append(
                Gap("trace_join", "unknown_event", f"joins[{index}]: event {event_id!r} not in audit events")
            )
            continue
        full_ref = {
            "run_id": join.get("run_id", run_id),
            "instance_id": join.get("instance_id", instance_id),
            "dimension": join.get("dimension"),
            "event_id": event_id,
            "tick": ref.get("tick", event.get("tick") if event else None),
        }
        if event is not None:
            full_ref["dimension"] = event.get("dimension", full_ref["dimension"])
        join_rows.append({"call_id": call_id, "audit_ref": full_ref, "verified": True})

    joined_calls = {row["call_id"] for row in join_rows}
    joined_events = {str(row["audit_ref"].get("event_id")) for row in join_rows}
    agent_event_ids = {
        str(row.get("event_id"))
        for row in (audit_events or [])
        if row.get("phase") == "agent" and row.get("event_id")
    }
    join_doc = {
        "joins": join_rows,
        "unmatched_tool_calls": len(set(calls) - joined_calls),
        "unmatched_agent_events": len(agent_event_ids - joined_events),
        "derived_from": {
            "trajectory_calls": len(calls),
            "audit_agent_events": len(agent_event_ids),
        },
    }
    return rows, join_doc, gaps


def cmd_trace(args: argparse.Namespace) -> int:
    trajectory_path = Path(args.trajectory)
    if not trajectory_path.is_file():
        print(f"error: {trajectory_path} is not a file", file=sys.stderr)
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
    source_sha = sha256_file(trajectory_path)
    rows, join_doc, gaps = map_trajectory(
        trajectory,
        run_id=args.run_id,
        instance_id=args.instance_id,
        instance_ids=(args.instances or "").split(",") if args.instances else None,
        joins=joins,
        audit_events=audit_events,
    )
    report = MappingReport("#19 trajectory.jsonl -> gate tool-trace", str(trajectory_path), source_sha)
    report.counts = {"calls": len(rows), "joins": len(join_doc["joins"])}
    report.gaps = gaps
    report.notes.append(
        "unmatched counts are computed from the mapped artifacts, not copied from a declaration"
    )
    if report.ok:
        trace_out = bundle / CANONICAL["tool_trace"]
        write_jsonl(trace_out, rows)
        report.mapped.append(str(trace_out.relative_to(bundle)))
        if join_doc["joins"]:
            join_out = bundle / CANONICAL["trace_join"]
            write_json(join_out, join_doc)
            report.mapped.append(str(join_out.relative_to(bundle)))
        else:
            report.notes.append(
                "no joins emitted: this run has no agent-phase audit events to join "
                "(trace_join.json withheld so the gate blocks instead of failing)"
            )
            stale = bundle / CANONICAL["trace_join"]
            if stale.exists():
                stale.unlink()
    write_json(bundle / "mapping" / "trace-report.json", report.as_json())
    print(json.dumps(report.as_json(), indent=2) if args.json else _summary(report))
    return 0 if report.ok else 1


# --------------------------------------------------------------------------- #16 identity


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
        {"seq": 1, "tick": 10, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "bootstrap", "type": "audit_ready", "config": {"dimension": "minecraft:overworld"}},
        {"seq": 2, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_attempt", "operator": "aaaa", "from": [0, 0, 0], "to": [0, 0, 0]},
        {"seq": 3, "tick": 20, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_request", "source": "player"},
        {"seq": 4, "tick": 21, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "input_processed", "operator": "aaaa", "targetInput": True, "agentOp": True, "requestSeq": 3, "attemptSeq": 2, "note": 0, "instrument": "harp"},
        {"seq": 5, "tick": 30, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_tracked", "uuid": "cart-1", "epoch": 1, "inventory": []},
        {"seq": 6, "tick": 40, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_exit", "uuid": "cart-1", "epoch": 1, "pos": [2.5, 60.0, 0.5]},
        {"seq": 7, "tick": 80, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "cart_remove", "uuid": "cart-1", "epoch": 1, "reason": "DISCARDED", "pos": [2.5, -70.0, 0.5], "inventory": [{"slot": 0, "id": "minecraft:stone", "count": 3}]},
        {"seq": 8, "tick": 90, "run": "r1", "inst": "exp-1", "session": "s1", "phase": "experiment", "type": "audit_end", "status": "complete"},
    ]


def _fixture_trajectory() -> list[dict[str, Any]]:
    return [
        {"record": "call", "call_id": "c1", "tool": "bash", "arguments": {"command": "python tools/lab_server.py status"}, "at": "2026-09-26T00:00:00Z", "phase": "prepare", "actor": "operator"},
        {"record": "result", "call_id": "c1", "status": "ok", "result": {"text": "up"}, "at": "2026-09-26T00:00:01Z"},
        {"record": "call", "call_id": "c2", "tool": "write", "arguments": {"path": "notes.md"}, "at": "2026-09-26T00:00:02Z", "phase": "prepare", "actor": "operator"},
        {"record": "result", "call_id": "c2", "status": "ok", "result": "written", "at": "2026-09-26T00:00:03Z"},
        {"record": "call", "call_id": "c3", "tool": "git", "arguments": {"args": ["rev-parse", "HEAD"]}, "at": "2026-09-26T00:00:04Z", "phase": "prepare", "actor": "operator"},
        {"record": "result", "call_id": "c3", "status": "ok", "result": "abc", "at": "2026-09-26T00:00:05Z"},
        {"record": "call", "call_id": "c4", "tool": "mcp_probe", "arguments": {"tool": "mc_state"}, "at": "2026-09-26T00:00:06Z", "phase": "prepare", "actor": "operator"},
        {"record": "result", "call_id": "c4", "status": "ok", "result": {"tick": 21}, "at": "2026-09-26T00:00:07Z"},
    ]


def run_selftest(out: TextIO | None = None) -> int:
    stream = out if out is not None else sys.stdout
    test = _Selftest(stream)
    with tempfile.TemporaryDirectory(prefix="stage1-evidence-selftest-") as tmp:
        base = Path(tmp)

        # audit mapping -----------------------------------------------------------
        rows, gaps, counts, excluded = map_audit_events(
            _fixture_audit(), run_id="r1", instance_id="exp-1", dimension="minecraft:overworld", source_sha256="0" * 64
        )
        test.check("audit maps without gaps", not gaps, str([g.as_json() for g in gaps]))
        by_type = {str(row["event"]) for row in rows}
        test.check("audit maps cart_exit to cart_emitted", "cart_emitted" in by_type)
        test.check("audit maps cart_remove to cart_removed", "cart_removed" in by_type)
        emitted = next(row for row in rows if row["event"] == "cart_emitted")
        test.check("audit capture-before-removal is derived", emitted.get("captured_before_removal") is True)
        processed = next(row for row in rows if row["event"] == "input_processed")
        test.equal("audit keeps the raw event", processed["detail"]["raw"]["type"], "input_processed")
        test.equal("audit maps phase experiment -> agent", processed["phase"], "agent")
        test.equal("audit event id", processed["event_id"], "s1:4")

        bad = list(_fixture_audit())
        bad[1]["run"] = "other-run"
        _, gaps, _, _ = map_audit_events(
            bad, run_id="r1", instance_id="exp-1", dimension="minecraft:overworld", source_sha256="0" * 64
        )
        test.check("audit rejects foreign run", any(gap.code == "run_mismatch" for gap in gaps))
        missing_dim = [dict(row) for row in _fixture_audit()]
        del missing_dim[1]["seq"]
        _, gaps, _, _ = map_audit_events(
            missing_dim, run_id="r1", instance_id="exp-1", dimension="minecraft:overworld", source_sha256="0" * 64
        )
        test.check("audit gaps on missing seq", any(gap.code == "missing_seq_tick" for gap in gaps))

        no_actor = [dict(row) for row in _fixture_audit()]
        del no_actor[1]["operator"]
        mapped, _, _, _ = map_audit_events(
            no_actor, run_id="r1", instance_id="exp-1", dimension="minecraft:overworld", source_sha256="0" * 64
        )
        attempt = next(row for row in mapped if row["event"] == "input_attempt")
        test.check("audit withholds actor when nothing recorded", "actor_uuid" not in attempt)
        sourced = [dict(row) for row in no_actor]
        sourced[1]["operatorSource"] = "redstone"
        mapped, _, _, _ = map_audit_events(
            sourced, run_id="r1", instance_id="exp-1", dimension="minecraft:overworld", source_sha256="0" * 64
        )
        attempt = next(row for row in mapped if row["event"] == "input_attempt")
        test.equal("audit records explicit absent actor provenance", attempt.get("actor_provenance"), "redstone")

        # trace mapping -----------------------------------------------------------
        trace_rows, join_doc, trace_gaps = map_trajectory(
            _fixture_trajectory(), run_id="r1", instance_id="exp-1", joins=None, audit_events=rows
        )
        test.check("trace maps without gaps", not trace_gaps, str([g.as_json() for g in trace_gaps]))
        test.equal("trace row count", len(trace_rows), 4)
        test.equal("trace categories present", {row["tool"] for row in trace_rows}, {"bash", "write", "git", "mcp_probe"})
        test.equal("trace unmatched agent events computed", join_doc["unmatched_agent_events"], 6)
        join = [{"call_id": "c4", "audit_ref": {"tick": 21, "event_id": "s1:4"}}]
        _, join_doc, trace_gaps = map_trajectory(
            _fixture_trajectory(), run_id="r1", instance_id="exp-1", joins=join, audit_events=rows
        )
        test.check("join resolves without gaps", not trace_gaps, str([g.as_json() for g in trace_gaps]))
        test.equal("join unmatched agent events drops to 5", join_doc["unmatched_agent_events"], 5)
        test.equal("join unmatched tool calls", join_doc["unmatched_tool_calls"], 3)
        bogus = [{"call_id": "c4", "audit_ref": {"tick": 21, "event_id": "nope"}}]
        _, _, trace_gaps = map_trajectory(
            _fixture_trajectory(), run_id="r1", instance_id="exp-1", joins=bogus, audit_events=rows
        )
        test.check("unknown join event is a gap", any(gap.code == "unknown_event" for gap in trace_gaps))
        missing_result = [
            row
            for row in _fixture_trajectory()
            if not (row.get("record") == "result" and row.get("call_id") == "c2")
        ]
        _, _, trace_gaps = map_trajectory(
            missing_result, run_id="r1", instance_id="exp-1", joins=None, audit_events=rows
        )
        test.check("call without result is a gap", any(gap.code == "missing_result" for gap in trace_gaps))

        # identity mapping --------------------------------------------------------
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

        # assemble ---------------------------------------------------------------
        bundle = base / "bundle"
        write_jsonl(bundle / CANONICAL["audit_events"], rows)
        write_jsonl(bundle / CANONICAL["tool_trace"], trace_rows)
        write_json(bundle / CANONICAL["trace_join"], join_doc)
        spec = {
            "run": {
                "run_id": "r1",
                "issue": "guajun/mc-agent#14",
                "allowed_port_ranges": ["27240-27249"],
                "child_runs": [{"run_id": f"init-{i}", "instance_id": "exp-1"} for i in range(1, 4)],
                "source_world": {"label": "x", "path": str(base / "world"), "before_tree_sha256": "1" * 64, "after_tree_sha256": "1" * 64},
                "instances": [
                    {"instance_id": "src-audit", "role": "source_audit", "dimension": "minecraft:overworld", "world_dir": "labs/src/world", "rcon_port": 27240, "bridge_port": 27241},
                    {"instance_id": "exp-1", "role": "experiment", "dimension": "minecraft:overworld", "world_dir": "labs/exp/world", "rcon_port": 27242, "bridge_port": 27243},
                ],
            }
        }
        report = assemble(bundle, spec, run_gate=False)
        test.check("assemble declares mapped artifacts", report["index_entries"] >= 3)
        index = read_json(bundle / CANONICAL["evidence_index"])
        index_paths = {entry["path"] for entry in index["entries"]}
        test.check("assemble index covers audit events", CANONICAL["audit_events"] in index_paths)
        test.check("assemble index covers tool trace", CANONICAL["tool_trace"] in index_paths)

    if test.failures:
        stream.write(f"selftest FAILED: {len(test.failures)} of {test.total} check(s): {', '.join(test.failures)}\n")
        return 1
    stream.write(f"selftest OK: {test.total} check(s) passed (no game, no live evidence)\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
