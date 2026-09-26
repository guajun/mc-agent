#!/usr/bin/env python3
"""Offline semantic verifier for the Minecart ROM issue-#21 regression.

The regression drives a real Minecraft server: a fresh sealed cart program is
initialized on an unoperated source instance, forked, guarded-restored into a
fresh experiment instance, and then the note block is pressed once per cart.
Two independent sources of truth are read back:

* the **agent logger** (``examples/minecart-rom/regression/logger``, derived
  from the frozen issue-#20 logger) writes its own JSONL: one transient
  ``cart_observed`` capture per cart with the ordered live inventory, and one
  natural ``cart_void_capture`` at the cart's own removal, with the real
  ``RemovalReason``;
* the **independent audit mod** (issue #18, unchanged jar) writes the
  evaluator's JSONL: the exact ``input_request``/``input_attempt``/
  ``input_processed`` chain and the ``cart_exit``/``cart_remove`` inventories.

``verify_run`` answers the questions issue #21 asks: was the machine really
operated through the note block by the recorded player, did each processed
input cause exactly one pop, did every cart leave the stack, was every live
inventory captured before destruction, did the carts reach the natural void
removal, does the agent-logger answer equal the independent audit oracle, and
does every observed inventory match the sealed program.  It never treats a
time-adjacent event as causal: the audit engine's own ``orderingEvidence``
plus the exact ``requestSeq``/``attemptSeq`` chain are required, and each
press must precede exactly one pop in order.

This module is stdlib-only and runs without a game.  ``selftest`` builds
synthetic logs for the positive case and every declared negative, so the
fail-closed behaviour is itself verified offline.

    python tools/rom21_verify.py selftest
    python tools/rom21_verify.py config --config examples/.../configs/calibrated-a.json
    python tools/rom21_verify.py verify --run <run-dir>
    python tools/rom21_verify.py negative --case output_order_wrong --run <run-dir>
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CONFIG_FORMAT = "mc-agent/rom21-config@1"
RUN_FORMAT = "mc-agent/rom21-run@1"
REPORT_FORMAT = "mc-agent/rom21-verification@1"
NEGATIVE_FORMAT = "mc-agent/rom21-negative@1"

CHECK_ORDER = (
    "run_manifest",
    "logger_session",
    "audit_oracle",
    "agent_operations",
    "causal_joins",
    "transient_captures",
    "natural_void_captures",
    "capture_timing",
    "output_order",
    "inventory_matches",
    "inventory_matches_program",
    "restore_verified",
    "jar_provenance",
    "source_world",
    "answer_matches_oracle",
)

# Fail-closed negative probes.  ``live`` probes are produced by a real game
# operation (see tools/rom21_regression.py negatives); ``offline`` probes
# mutate a copy of a real run's raw evidence.  Declared here so the report and
# the tests agree.
NEGATIVE_CASES = {
    "no_machine_operation": {
        "kind": "live",
        "expected_check": "agent_operations",
        "injected": "restored ready machine, no note-block press at all",
    },
    "logger_missed_capture": {
        "kind": "live",
        "expected_check": "transient_captures",
        "injected": "logger exit plane set far away (no cart is ever captured)",
    },
    "logger_late_capture": {
        "kind": "live",
        "expected_check": "transient_captures",
        "injected": "logger capture delay larger than the pop-to-removal window",
    },
    "inventory_wrong": {
        "kind": "live",
        "expected_check": "inventory_matches_program",
        "injected": "a restored cart's Items[0].count modified before the operation",
    },
    "restore_order_wrong": {
        "kind": "live",
        "expected_check": "restore_verified",
        "injected": "two entities swapped in the guarded-restore snapshot before apply",
    },
    "output_order_wrong": {
        "kind": "offline",
        "expected_check": "output_order",
        "injected": "cart_observed records reversed in a copy of the raw logger",
    },
    "audit_missing": {
        "kind": "offline",
        "expected_check": "audit_oracle",
        "injected": "the independent audit log removed from a copy of the run",
    },
    "wrong_instance": {
        "kind": "offline",
        "expected_check": "logger_session",
        "injected": "logger instance/run id changed to a different instance",
    },
}


class VerifyError(RuntimeError):
    """The evidence cannot be interpreted; the verdict is fail-closed."""


# --------------------------------------------------------------------------- io helpers


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not Path(path).is_file():
        return rows
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except ValueError as error:
            raise VerifyError(f"{path} line {number}: invalid JSON ({error})") from error
        if not isinstance(value, dict):
            raise VerifyError(f"{path} line {number}: record is not an object")
        rows.append(value)
    return rows


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def normalize_items(items: Any) -> list[tuple[int, str, int]]:
    """Ordered (slot, id, count) list; accepts dicts and already-normalized tuples."""
    normalized: list[tuple[int, str, int]] = []
    if not isinstance(items, list):
        return normalized
    for item in items:
        if isinstance(item, (tuple, list)) and len(item) == 3:
            normalized.append((int(item[0]), str(item[1]), int(item[2])))
            continue
        if not isinstance(item, dict):
            continue
        identifier = item.get("id", item.get("item"))
        if identifier is None:
            continue
        normalized.append((int(item.get("slot", -1)), str(identifier), int(item.get("count", 0))))
    return sorted(normalized, key=lambda entry: entry[0])


def items_digest(items: Any) -> str:
    text = ";".join(f"{slot}|{identifier}|{count}" for slot, identifier, count in normalize_items(items))
    return sha256_bytes(text.encode("utf-8"))


_SHA256_HEX = re.compile(r"^[0-9a-fA-F]{64}$")


def _int_field(row: Mapping[str, Any], name: str) -> int | None:
    """A real integer (never a bool) field, or None."""
    value = row.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_HEX.fullmatch(value.strip()))


def source_observation_problem(observation: Any) -> str | None:
    """A source-world observation is only accepted with real, hashed content.

    ``source_save_present`` alone (or an empty object) is not proof of anything:
    the observation must carry a non-empty 64-hex tree digest and the matching
    baseline digest, a true ``matches_baseline`` flag and non-zero file/byte
    counts.
    """
    if not isinstance(observation, Mapping):
        return "no observation recorded"
    if observation.get("source_save_present") is not True:
        return "source_save_present is not true"
    for field in ("tree_sha256", "baseline_sha256"):
        if not _sha256_hex(observation.get(field)):
            return f"{field} is not a 64-hex digest"
    if str(observation["tree_sha256"]).lower() != str(observation["baseline_sha256"]).lower():
        return "observed tree hash does not equal the recorded baseline"
    if observation.get("matches_baseline") is not True:
        return "matches_baseline is not true"
    files = observation.get("files")
    total = observation.get("bytes")
    if isinstance(files, bool) or not isinstance(files, int) or files <= 0:
        return "files is not a positive integer"
    if isinstance(total, bool) or not isinstance(total, int) or total <= 0:
        return "bytes is not a positive integer"
    return None


def program_carts(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    program = config.get("program") or {}
    carts = program.get("carts")
    if not isinstance(carts, list) or not carts:
        raise VerifyError("config.program.carts is empty")
    for index, entry in enumerate(carts):
        if not isinstance(entry, dict) or not isinstance(entry.get("items"), list):
            raise VerifyError(f"config.program.carts[{index}] needs an items list")
    return carts


def program_sha256(carts: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(canonical_json(list(carts)).encode("utf-8"))


def validate_config(config: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    if config.get("format") != CONFIG_FORMAT:
        problems.append(f"format must be {CONFIG_FORMAT}")
    if not config.get("configId"):
        problems.append("configId is required")
    try:
        carts = program_carts(config)
    except VerifyError as error:
        problems.append(str(error))
        return problems
    expected = config.get("expectedCartCount")
    if expected != len(carts):
        problems.append(f"expectedCartCount {expected} != program carts {len(carts)}")
    if config.get("programSha256") != program_sha256(carts):
        problems.append("programSha256 does not match the canonical program carts")
    scene = config.get("scene") or {}
    if not scene.get("dimension"):
        problems.append("scene.dimension is required")
    if scene.get("exitAxis") not in ("x", "y", "z"):
        problems.append("scene.exitAxis must be x, y or z")
    if not isinstance(scene.get("exitGreaterThan"), (int, float)):
        problems.append("scene.exitGreaterThan must be a number")
    for region in ("inputRegion", "stackRegion", "outputRegion"):
        value = scene.get(region)
        if not isinstance(value, dict) or not isinstance(value.get("from"), list) or not isinstance(value.get("to"), list):
            problems.append(f"scene.{region} must hold from/to lists")
    operations = config.get("operations") or {}
    if operations.get("presses") != len(carts):
        problems.append(f"operations.presses {operations.get('presses')} != carts {len(carts)}")
    for field in ("sprintTicks", "causalMaxTicks", "transientMaxDeltaTicks", "timeoutSeconds"):
        value = operations.get(field)
        if not isinstance(value, int) or value <= 0:
            problems.append(f"operations.{field} must be a positive integer")
    logger = config.get("logger") or {}
    if not logger.get("buildVersion"):
        problems.append("logger.buildVersion is required")
    return problems


def load_config(path: Path) -> dict[str, Any]:
    config = read_json(path)
    problems = validate_config(config)
    if problems:
        raise VerifyError("; ".join(problems))
    return config


# --------------------------------------------------------------------------- extraction


def logger_sessions(rows: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split the logger file into server sessions at ``logger_armed`` records."""
    sessions: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] | None = None
    for row in rows:
        if row.get("event") == "logger_armed" or current is None:
            current = []
            sessions.append(current)
        current.append(dict(row))
    return [session for session in sessions if session]


def pick_logger_session(rows: Sequence[Mapping[str, Any]], ordinal: int) -> list[dict[str, Any]]:
    sessions = logger_sessions(rows)
    if ordinal < 1 or ordinal > len(sessions):
        raise VerifyError(f"logger session {ordinal} does not exist ({len(sessions)} sessions)")
    return sessions[ordinal - 1]


def sort_key(row: Mapping[str, Any]) -> tuple[int, int]:
    return int(row.get("tick", -1)), int(row.get("seq", -1))


def extract_answer(session: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    observed = sorted(
        (dict(row) for row in session if row.get("event") == "cart_observed"),
        key=sort_key,
    )
    return [
        {
            "uuid": str(row.get("uuid")),
            "tick": row.get("tick"),
            "seq": row.get("seq"),
            "via": row.get("via"),
            "items": normalize_items(row.get("items")),
            "rawItems": row.get("items"),
            "digest": row.get("inventoryDigest"),
        }
        for row in observed
    ]


def extract_void_captures(session: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(
        (dict(row) for row in session if row.get("event") == "cart_void_capture"),
        key=sort_key,
    )
    return [
        {
            "uuid": str(row.get("uuid")),
            "tick": row.get("tick"),
            "seq": row.get("seq"),
            "reason": row.get("removalReason"),
            "items": normalize_items(row.get("items")),
            "rawItems": row.get("items"),
            "digest": row.get("inventoryDigest"),
        }
        for row in rows
    ]


def audit_by_seq(rows: Sequence[Mapping[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row["seq"]): dict(row) for row in rows if isinstance(row.get("seq"), int)}


def experiment_session_id(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        if row.get("type") == "input_processed" and row.get("phase") == "experiment":
            return str(row.get("session"))
    for row in rows:
        if row.get("type") == "cart_exit" and row.get("phase") == "experiment":
            return str(row.get("session"))
    return None


def experiment_rows(rows: Sequence[Mapping[str, Any]], kind: str) -> list[dict[str, Any]]:
    return sorted(
        (
            dict(row)
            for row in rows
            if row.get("type") == kind and row.get("phase") == "experiment"
        ),
        key=sort_key,
    )


def extract_oracle(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    removes = [
        row
        for row in experiment_rows(rows, "cart_remove")
        if str(row.get("reason", "")).upper() in ("DISCARDED", "OUT_OF_WORLD")
    ]
    return [
        {
            "uuid": str(row.get("uuid")),
            "tick": row.get("tick"),
            "seq": row.get("seq"),
            "reason": row.get("reason"),
            "epoch": row.get("epoch"),
            "session": row.get("session"),
            "items": normalize_items(row.get("inventory")),
            "rawItems": row.get("inventory"),
            "digest": row.get("inventoryDigest"),
        }
        for row in removes
    ]


# --------------------------------------------------------------------------- checks


class Checks:
    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.results[name] = {"pass": bool(passed), "detail": detail}

    def failed(self) -> list[str]:
        return [name for name, result in self.results.items() if not result["pass"]]


def _same_session(rows: Iterable[Mapping[str, Any]], expected: str | None) -> bool:
    return all(str(row.get("session")) == str(expected) for row in rows)


def _operator_uuid(row: Mapping[str, Any]) -> str | None:
    operator = row.get("operator")
    if isinstance(operator, dict):
        value = operator.get("uuid")
        return str(value) if value else None
    return None


def verify_run(
    run: Mapping[str, Any],
    logger_rows: Sequence[Mapping[str, Any]],
    audit_rows: Sequence[Mapping[str, Any]],
    *,
    audit_check: Mapping[str, Any] | None = None,
    restore_verify: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify one regression run and return the report.  Never raises for evidence faults."""
    checks = Checks()
    problems: list[str] = []
    if run.get("auditSessionId"):
        wanted = str(run["auditSessionId"])
        audit_rows = [row for row in audit_rows if str(row.get("session")) == wanted]

    # -- run manifest -------------------------------------------------------
    manifest_problems: list[str] = []
    if run.get("format") != RUN_FORMAT:
        manifest_problems.append(f"format must be {RUN_FORMAT}")
    if not run.get("runId"):
        manifest_problems.append("runId is missing")
    if not run.get("instance"):
        manifest_problems.append("instance is missing")
    try:
        carts = program_carts(run)
        expected = int(run.get("expectedCartCount"))
        if run.get("programSha256") != program_sha256(carts):
            manifest_problems.append("programSha256 does not match the run program")
        if expected != len(carts):
            manifest_problems.append(f"expectedCartCount {expected} != program carts {len(carts)}")
        condition = str((run.get("scene") or {}).get("exitAxis", "")).lower() in ("x", "y", "z")
        if not condition:
            manifest_problems.append("scene.exitAxis is missing or invalid")
    except (VerifyError, TypeError, ValueError) as error:
        manifest_problems.append(str(error))
        expected = 0
    checks.add("run_manifest", not manifest_problems, "; ".join(manifest_problems) or "run manifest consistent")

    # -- logger session -----------------------------------------------------
    required_dimension = str((run.get("scene") or {}).get("dimension") or run.get("dimension") or "")
    required_identity = {
        "instance": str(run.get("instance") or ""),
        "run_id": str(run.get("runId") or ""),
        "dimension": required_dimension,
        "build": str((run.get("loggerJar") or {}).get("build") or ""),
    }
    session_problems: list[str] = []
    session: list[dict[str, Any]] = []
    try:
        session = pick_logger_session(logger_rows, int(run.get("loggerSessionOrdinal", 1)))
    except (VerifyError, TypeError, ValueError) as error:
        session_problems.append(str(error))
    if not required_dimension:
        session_problems.append("the run has no dimension to bind the logger session to")
    armed = next((row for row in session if row.get("event") == "logger_armed"), None)
    # Every relevant logger event must carry the full identity, dimension
    # included; a capture from another dimension or instance is not evidence.
    for row in session:
        if row.get("event") not in ("logger_armed", "cart_observed", "cart_void_capture", "logger_flushed"):
            continue
        for field, wanted in required_identity.items():
            if wanted and str(row.get(field)) != wanted:
                session_problems.append(
                    f"{row.get('event')} seq {row.get('seq')}: {field}={row.get(field)!r} != {wanted!r}"
                )
    if armed is None:
        session_problems.append("no logger_armed record in the selected session")
    else:
        armed_tick = _int_field(armed, "tick")
        armed_wall = _int_field(armed, "wall")
        if armed_tick is None or armed_tick < 0 or armed_wall is None or armed_wall <= 0:
            session_problems.append("logger_armed has no valid tick/wall timestamp")
        if int(armed.get("expectedCarts") or -1) != expected:
            session_problems.append("logger_armed.expectedCarts does not match the run program")
        expected_axis = str((run.get("scene") or {}).get("exitAxis", "")).lower()
        if str(armed.get("exitAxis", "")).lower() != expected_axis:
            session_problems.append("logger_armed.exitAxis does not match the run scene")
        # Strong temporal binding to the selected audit server session: the
        # arm must happen inside that session's lifetime and before every
        # input and capture.  This rejects missing, future-dated and
        # cross-session arms instead of trusting the record's presence.
        processed_rows = experiment_rows(audit_rows, "input_processed")
        exit_rows = experiment_rows(audit_rows, "cart_exit")
        session_start = next((row for row in audit_rows if row.get("type") == "session_start"), None)
        if run.get("auditSessionId") and session_start is None:
            session_problems.append("cannot bind the logger session: the audit session has no session_start")
        if session_start is not None:
            start_wall = _int_field(session_start, "wall")
            if start_wall is not None and armed_wall is not None and armed_wall < start_wall:
                session_problems.append("logger_armed.wall precedes the selected audit session start")
        # The two mods both arm at SERVER_STARTED, so their relative order is
        # not deterministic; the meaningful binding is: inside the selected
        # server session and before the experiment window, every input and
        # every capture.
        anchors = (
            ("experiment phase", next(
                (row for row in audit_rows if row.get("type") == "phase" and row.get("to") == "experiment"), None)),
            ("first processed input", processed_rows[0] if processed_rows else None),
            ("first cart exit", exit_rows[0] if exit_rows else None),
            ("first transient capture", next(
                (row for row in session if row.get("event") == "cart_observed"), None)),
            ("first natural capture", next(
                (row for row in session if row.get("event") == "cart_void_capture"), None)),
        )
        for label, anchor in anchors:
            if anchor is None:
                continue
            anchor_tick = _int_field(anchor, "tick")
            anchor_wall = _int_field(anchor, "wall")
            if anchor_tick is not None and armed_tick is not None and armed_tick > anchor_tick:
                session_problems.append(f"logger_armed.tick {armed_tick} is after {label} tick {anchor_tick}")
            if anchor_wall is not None and armed_wall is not None and armed_wall > anchor_wall:
                session_problems.append(f"logger_armed.wall {armed_wall} is after {label} wall {anchor_wall}")
    checks.add(
        "logger_session",
        not session_problems,
        "; ".join(session_problems[:6]) or f"logger session {run.get('loggerSessionOrdinal', 1)} scoped and identified",
    )

    # -- audit oracle -------------------------------------------------------
    oracle_problems: list[str] = []
    if not audit_rows:
        oracle_problems.append("the independent audit log is missing or empty")
    else:
        runs = {str(row.get("run")) for row in audit_rows}
        instances = {str(row.get("inst")) for row in audit_rows}
        if runs != {str(run.get("runId"))} or instances != {str(run.get("instance"))}:
            oracle_problems.append(f"audit run/inst {sorted(runs)}/{sorted(instances)} do not match the run")
        # Every oracle-relevant audit event must be in the run's dimension
        # (`dimension` and `level` are the produced aliases).
        cart_event_types = (
            "cart_tracked", "cart_reload", "cart_reappeared", "cart_sample", "cart_exit",
            "cart_remove", "cart_inventory_change", "cart_teleport",
        )
        dimension_problems: list[str] = []
        for row in audit_rows:
            kind = row.get("type")
            if kind == "audit_ready":
                config = row.get("config") if isinstance(row.get("config"), Mapping) else {}
                dimension = config.get("dimension") or row.get("dimension")
                if str(dimension) != required_dimension:
                    dimension_problems.append(
                        f"audit_ready dimension {dimension!r} != {required_dimension!r}"
                    )
            elif kind in cart_event_types:
                dimension = row.get("dimension", row.get("level"))
                if str(dimension) != required_dimension:
                    dimension_problems.append(
                        f"{kind} seq {row.get('seq')} dimension {dimension!r} != {required_dimension!r}"
                    )
        oracle_problems.extend(dimension_problems[:4])
        session_id = experiment_session_id(audit_rows)
        if session_id is None:
            oracle_problems.append("no experiment input_processed/cart_exit in the audit log")
        else:
            session_events = [row for row in audit_rows if str(row.get("session")) == str(session_id)]
            ends = [
                row for row in session_events
                if row.get("type") == "audit_end" and row.get("status") == "complete"
            ]
            if not ends:
                oracle_problems.append("the experiment session has no complete audit_end record")
        if audit_check is None:
            oracle_problems.append("the independent minecart-audit check report is missing")
        elif str(audit_check.get("verdict")) != "pass":
            oracle_problems.append(f"minecart-audit verdict is {audit_check.get('verdict')!r}, not pass")
    checks.add("audit_oracle", not oracle_problems, "; ".join(oracle_problems) or "independent oracle present and PASS")

    # -- agent operations ---------------------------------------------------
    processed = experiment_rows(audit_rows, "input_processed")
    requests = audit_by_seq(audit_rows)
    operations_problems: list[str] = []
    agent_uuid = str(run.get("agentUuid") or "")
    if len(processed) != int(run.get("expectedCartCount") or 0):
        operations_problems.append(f"{len(processed)} processed input(s) for {run.get('expectedCartCount')} expected pops")
    for row in processed:
        request = requests.get(int(row.get("requestSeq") or -1))
        attempt = requests.get(int(row.get("attemptSeq") or -1))
        if request is None or request.get("type") != "input_request":
            operations_problems.append(f"seq {row.get('seq')}: requestSeq {row.get('requestSeq')} has no input_request")
            continue
        if attempt is None or attempt.get("type") != "input_attempt":
            operations_problems.append(f"seq {row.get('seq')}: attemptSeq {row.get('attemptSeq')} has no input_attempt")
            continue
        if attempt.get("requestSeq") != row.get("requestSeq"):
            operations_problems.append(f"seq {row.get('seq')}: attempt does not carry the credited requestSeq")
        if row.get("orderingEvidence") is not True:
            operations_problems.append(f"seq {row.get('seq')}: orderingEvidence is not true")
        if request.get("session") != row.get("session") or attempt.get("session") != row.get("session"):
            operations_problems.append(f"seq {row.get('seq')}: request/attempt come from another server session")
        if row.get("operator") is None:
            operations_problems.append(f"seq {row.get('seq')}: processed input carries no operator")
        if agent_uuid and _operator_uuid(row) != agent_uuid:
            operations_problems.append(f"seq {row.get('seq')}: operator {_operator_uuid(row)} != {agent_uuid}")
        if agent_uuid and _operator_uuid(request) != agent_uuid:
            operations_problems.append(f"seq {request.get('seq')}: request operator is not the task player")
        if row.get("agentOp") is not True:
            operations_problems.append(f"seq {row.get('seq')}: agentOp is not true")
        delta = int(row.get("tick", -1)) - int(request.get("tick", -1))
        if delta < 0 or delta > int((run.get("operations") or {}).get("causalMaxTicks", 40)):
            operations_problems.append(f"seq {row.get('seq')}: request tick delta {delta} outside the causal window")
    checks.add(
        "agent_operations",
        not operations_problems,
        "; ".join(operations_problems[:6]) or f"{len(processed)} calibrated note-block operation(s)",
    )

    # -- causal joins -------------------------------------------------------
    exits = experiment_rows(audit_rows, "cart_exit")
    removes = extract_oracle(audit_rows)
    causal_problems: list[str] = []
    causal_window = int((run.get("operations") or {}).get("causalMaxTicks", 40))
    session_id = experiment_session_id(audit_rows)
    if not (len(processed) == len(exits) == len(removes) == expected):
        causal_problems.append(
            f"counts differ: processed={len(processed)} exits={len(exits)} removes={len(removes)} expected={expected}"
        )
    epochs = {str(row.get("epoch")) for row in exits} | {str(row.get("epoch")) for row in removes}
    if len(epochs) > 1:
        causal_problems.append(f"cart exits/removes span multiple epochs {sorted(epochs)}")
    if not _same_session(list(exits) + list(removes) + list(processed), session_id):
        causal_problems.append("experiment evidence spans multiple server sessions")
    for index in range(min(len(processed), len(exits), len(removes))):
        processed_row = processed[index]
        exit_row = exits[index]
        remove_row = removes[index]
        delta = int(exit_row.get("tick", -1)) - int(processed_row.get("tick", -1))
        if not (0 <= delta <= causal_window):
            causal_problems.append(
                f"exit {index}: pop tick {exit_row.get('tick')} is not 0..{causal_window} ticks after its press "
                f"{processed_row.get('tick')}"
            )
        if int(remove_row.get("tick", -1)) <= int(exit_row.get("tick", -1)):
            causal_problems.append(f"remove {index}: void removal is not after the pop")
        if index:
            if int(processed_row.get("tick", -1)) <= int(exits[index - 1].get("tick", -1)):
                causal_problems.append(
                    f"press {index}: the operation is not after the previous pop; one press must cause one pop"
                )
    checks.add(
        "causal_joins",
        not causal_problems,
        "; ".join(causal_problems[:6]) or f"{len(exits)} pop(s) joined to exact presses in tick order",
    )

    # -- logger captures ----------------------------------------------------
    answer = extract_answer(session)
    void_captures = extract_void_captures(session)
    capture_problems: list[str] = []
    if len(answer) != expected:
        capture_problems.append(f"{len(answer)} transient capture(s) for {expected} expected cart(s)")
    if len({cart["uuid"] for cart in answer}) != len(answer):
        capture_problems.append("duplicate cart UUIDs in the transient captures")
    void_by_uuid = {cart["uuid"]: cart for cart in void_captures}
    if len(void_by_uuid) != len(void_captures):
        capture_problems.append("duplicate cart UUIDs in the natural void captures")
    checks.add(
        "transient_captures",
        not capture_problems,
        "; ".join(capture_problems) or f"{len(answer)} transient live-inventory capture(s)",
    )

    void_problems: list[str] = []
    if len(void_captures) != expected:
        void_problems.append(f"{len(void_captures)} natural void capture(s) for {expected} expected cart(s)")
    for index, cart in enumerate(answer):
        void = void_by_uuid.get(cart["uuid"])
        if void is None:
            void_problems.append(f"cart {index} {cart['uuid']} has no natural void capture")
            continue
        if str(void.get("reason", "")).upper() != "DISCARDED":
            void_problems.append(f"cart {index} removal reason {void.get('reason')!r} is not DISCARDED")
        if void.get("digest") != cart.get("digest"):
            void_problems.append(f"cart {index} void digest differs from the transient capture digest")
        if normalize_items(void.get("items")) != normalize_items(cart.get("items")):
            void_problems.append(f"cart {index} void inventory differs from the transient capture inventory")
    checks.add(
        "natural_void_captures",
        not void_problems,
        "; ".join(void_problems[:6]) or f"{len(void_captures)} natural void capture(s) at removal",
    )

    timing_problems: list[str] = []
    transient_max = int((run.get("operations") or {}).get("transientMaxDeltaTicks", 80))
    for index, cart in enumerate(answer):
        if index < len(exits):
            delta = int(cart["tick"]) - int(exits[index].get("tick", -1))
            if delta < 0:
                timing_problems.append(f"cart {index}: transient capture tick precedes the audit cart_exit")
            elif delta > transient_max:
                timing_problems.append(f"cart {index}: transient capture {delta} ticks after the pop (max {transient_max})")
        if index < len(removes):
            matched_remove = removes[index]
            if str(matched_remove.get("uuid")) != cart["uuid"]:
                timing_problems.append(f"cart {index}: transient capture does not match the removal UUID")
            remove_tick = int(matched_remove.get("tick", -1))
            # A capture at or after the destruction tick cannot be a transient
            # observation, no matter how wide the configured window is.
            if int(cart["tick"]) >= remove_tick:
                timing_problems.append(
                    f"cart {index}: transient capture tick {cart['tick']} is not strictly before the matched "
                    f"removal tick {remove_tick}"
                )
        if index < len(removes) and cart["uuid"] in void_by_uuid:
            void = void_by_uuid[cart["uuid"]]
            drift = abs(int(void["tick"]) - int(removes[index].get("tick", -1)))
            if drift > 1:
                timing_problems.append(f"cart {index}: void capture tick differs from the audit removal by {drift}")
    checks.add(
        "capture_timing",
        not timing_problems,
        "; ".join(timing_problems[:6]) or "capture before removal and inside the calibrated windows",
    )

    # -- order --------------------------------------------------------------
    order_problems: list[str] = []
    sequences = [
        ("transient", [(int(cart["tick"]), int(cart["seq"])) for cart in answer]),
        ("void", [(int(cart["tick"]), int(cart["seq"])) for cart in void_captures]),
        ("exit", [(int(row.get("tick", -1)), int(row.get("seq", -1))) for row in exits]),
        ("remove", [(int(row.get("tick", -1)), int(row.get("seq", -1))) for row in removes]),
    ]
    for name, keys in sequences:
        if any(left >= right for left, right in zip(keys, keys[1:])):
            order_problems.append(f"{name} events are not strictly increasing")
    uuids = {
        "transient": [cart["uuid"] for cart in answer],
        "void": [cart["uuid"] for cart in void_captures],
        "exit": [str(row.get("uuid")) for row in exits],
        "remove": [str(row.get("uuid")) for row in removes],
    }
    for name in ("void", "exit", "remove"):
        if len(uuids[name]) == len(uuids["transient"]) and uuids[name] != uuids["transient"]:
            order_problems.append(f"{name} order differs from the transient capture order")
    checks.add(
        "output_order",
        not order_problems,
        "; ".join(order_problems[:6]) or "observed pop order is consistent across logger and audit",
    )

    # -- inventory equality -------------------------------------------------
    inventory_problems: list[str] = []
    for index, cart in enumerate(answer):
        if index < len(exits):
            if normalize_items(exits[index].get("inventory")) != normalize_items(cart.get("items")):
                inventory_problems.append(f"cart {index}: audit cart_exit inventory differs from the capture")
        if index < len(removes):
            if normalize_items(removes[index].get("items")) != normalize_items(cart.get("items")):
                inventory_problems.append(f"cart {index}: audit cart_remove inventory differs from the capture")
        if index < len(exits) and index < len(removes):
            if exits[index].get("uuid") != removes[index].get("uuid"):
                inventory_problems.append(f"cart {index}: audit exit/remove UUIDs differ")
        void = void_by_uuid.get(cart["uuid"])
        if void is not None and normalize_items(void.get("items")) != normalize_items(cart.get("items")):
            inventory_problems.append(f"cart {index}: natural void inventory differs")
    checks.add(
        "inventory_matches",
        not inventory_problems,
        "; ".join(inventory_problems[:6]) or "live inventory equal across logger, audit exit and audit removal",
    )

    program_problems: list[str] = []
    expected_signatures = [canonical_json(normalize_items(entry.get("items"))) for entry in carts]
    observed_signatures = [canonical_json(cart["items"]) for cart in answer]
    if sorted(expected_signatures) != sorted(observed_signatures):
        missing = [sig for sig in expected_signatures if observed_signatures.count(sig) < expected_signatures.count(sig)]
        unexpected = [sig for sig in observed_signatures if expected_signatures.count(sig) < observed_signatures.count(sig)]
        program_problems.append(
            f"observed inventories do not match the sealed program (missing={len(missing)} unexpected={len(unexpected)})"
        )
    if len(observed_signatures) != len(set(observed_signatures)):
        program_problems.append("two observed carts carry the same inventory signature")
    checks.add(
        "inventory_matches_program",
        not program_problems,
        "; ".join(program_problems) or f"all {expected} observed inventories match the sealed program exactly",
    )

    # -- restore ------------------------------------------------------------
    restore_problems: list[str] = []
    restore = restore_verify or {}
    verification = (restore.get("verification") or {}) if isinstance(restore, dict) else {}
    if not restore:
        restore_problems.append("the guarded-restore verify record is missing")
    else:
        if restore.get("ok") is not True:
            restore_problems.append(f"restore verify ok={restore.get('ok')}")
        if (verification.get("dimension") or {}).get("match") is not True:
            restore_problems.append("restore dimension does not match")
        if (verification.get("orderHash") or {}).get("match") is not True:
            restore_problems.append("restore order hash does not match")
        expected_order = run.get("expectedOrderHash")
        actual_order = (verification.get("orderHash") or {}).get("actual")
        if expected_order and actual_order and str(actual_order) != str(expected_order):
            restore_problems.append(
                f"restored order hash {actual_order} does not match the accepted ready order {expected_order}"
            )
        if (verification.get("uuidOrder") or {}).get("match") is not True:
            restore_problems.append("restore UUID order does not match")
        matched = verification.get("matched")
        if not isinstance(matched, int) or matched < expected:
            restore_problems.append(f"restore matched {matched} entities, expected at least {expected}")
    checks.add(
        "restore_verified",
        not restore_problems,
        "; ".join(restore_problems) or "guarded restore verified (order, UUID order, counts, dimension)",
    )

    # -- jars ---------------------------------------------------------------
    jar_problems: list[str] = []
    jars = [run.get("loggerJar") or {}]
    iteration = run.get("loggerJarIteration")
    if iteration:
        jars.append(iteration)
    for jar in jars:
        if not jar.get("sha256") or not isinstance(jar.get("size"), int) or jar["size"] <= 0:
            jar_problems.append("a logger jar record is missing sha256/size")
        if jar.get("deployedSha256") and jar.get("deployedSha256") != jar.get("sha256"):
            jar_problems.append("the deployed jar SHA-256 differs from the built jar")
        if not (run.get("auditJar") or {}).get("sha256"):
            jar_problems.append("the audit jar record is missing")
    if iteration:
        first, second = jars[0], jars[1]
        if first.get("size") != second.get("size"):
            jar_problems.append("iteration jars do not have identical sizes")
        if first.get("sha256") == second.get("sha256"):
            jar_problems.append("iteration jars did not change bytes")
        if first.get("sourceSha256") != second.get("sourceSha256"):
            jar_problems.append("iteration jars were not built from the same source bytes")
    checks.add(
        "jar_provenance",
        not jar_problems,
        "; ".join(jar_problems) or "logger jar(s) pinned by SHA-256 (and size+source for iteration)",
    )

    # -- source world -------------------------------------------------------
    source_problems: list[str] = []
    source = run.get("sourceWorld") if isinstance(run.get("sourceWorld"), Mapping) else {}
    before = source.get("before") if isinstance(source.get("before"), Mapping) else None
    after = source.get("after") if isinstance(source.get("after"), Mapping) else None
    for label, observation in (("before", before), ("after", after)):
        problem = source_observation_problem(observation)
        if problem:
            source_problems.append(f"{label}: {problem}")
    if not source_problems:
        if str(before["tree_sha256"]).lower() != str(after["tree_sha256"]).lower():
            source_problems.append("the read-only source world changed during the run")
        if str(before["baseline_sha256"]).lower() != str(after["baseline_sha256"]).lower():
            source_problems.append("the source-world baseline differs between the before/after observations")
    checks.add(
        "source_world",
        not source_problems,
        "; ".join(source_problems[:6]) or "source world observed before and after with matching hashed baselines",
    )

    # -- answer vs oracle ---------------------------------------------------
    answer_problems: list[str] = []
    oracle = [
        {
            "uuid": row["uuid"],
            "tick": row.get("tick"),
            "seq": row.get("seq"),
            "items": normalize_items(row.get("items")),
            "digest": row.get("digest"),
        }
        for row in removes
    ]
    if [cart["uuid"] for cart in answer] != [row["uuid"] for row in oracle]:
        answer_problems.append("answer cart order differs from the independent oracle")
    if [cart["items"] for cart in answer] != [row["items"] for row in oracle]:
        answer_problems.append("answer inventories differ from the independent oracle")
    checks.add(
        "answer_matches_oracle",
        not answer_problems and bool(oracle),
        "; ".join(answer_problems) or f"{len(oracle)} cart(s) match the independent oracle exactly",
    )

    checks_payload = {name: checks.results.get(name, {"pass": False, "detail": "not evaluated"}) for name in CHECK_ORDER}
    failures = [name for name, result in checks_payload.items() if not result["pass"]]
    verdict = "pass" if not failures else "fail"
    if failures:
        problems = [f"{name}: {checks_payload[name]['detail']}" for name in failures]
    return {
        "format": REPORT_FORMAT,
        "runId": run.get("runId"),
        "configId": run.get("configId"),
        "instance": run.get("instance"),
        "expectedCartCount": expected,
        "verdict": verdict,
        "failures": failures,
        "problems": problems,
        "checks": checks_payload,
        "answer": answer,
        "oracle": oracle,
        "observedPopOrder": [cart["uuid"] for cart in answer],
        "popOrderSource": "observed live (logger transient captures joined to audit cart_exit/cart_remove); spawn order never assumed",
        "pressedOperations": len(processed),
    }


def run_audit_check(audit_path: Path, *, timeout: float = 300.0) -> dict[str, Any]:
    """Run the independent issue-#18 verifier as a subprocess (never imported)."""
    import subprocess

    script = Path(__file__).resolve().parent / "minecart_audit.py"
    result = subprocess.run(
        [sys.executable, str(script), "check", "--log", str(audit_path), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    stdout = result.stdout or ""
    report = json.loads(stdout[stdout.find("{"):]) if "{" in stdout else {}
    report["_exitCode"] = result.returncode
    return report


# --------------------------------------------------------------------------- negatives


def offline_negative(run_dir: Path, case: str) -> dict[str, Any]:
    """Mutate a real run's raw evidence in memory and require fail-closed."""
    if case not in NEGATIVE_CASES or NEGATIVE_CASES[case]["kind"] != "offline":
        raise VerifyError(f"{case!r} is not an offline negative case")
    run_dir = Path(run_dir)
    run = read_json(run_dir / "run.json")
    logger_rows = read_jsonl(run_dir / "logger.jsonl")
    audit_path = run_dir / "audit.jsonl"
    audit_rows = read_jsonl(audit_path)
    audit_check = read_json(run_dir / "audit-check.json") if (run_dir / "audit-check.json").is_file() else None
    restore = read_json(run_dir / "restore-verify.json") if (run_dir / "restore-verify.json").is_file() else None
    if case == "output_order_wrong":
        observed = [row for row in logger_rows if row.get("event") == "cart_observed"]
        if len(observed) < 2:
            raise VerifyError("output_order_wrong needs at least two transient captures")
        first, second = observed[0], observed[1]
        first["tick"], second["tick"] = second["tick"], first["tick"]
    elif case == "audit_missing":
        audit_rows = []
    elif case == "wrong_instance":
        for row in logger_rows:
            if "instance" in row:
                row["instance"] = "some-other-instance"
            if "run_id" in row:
                row["run_id"] = "some-other-run"
    report = verify_run(run, logger_rows, audit_rows, audit_check=audit_check, restore_verify=restore)
    expected_check = NEGATIVE_CASES[case]["expected_check"]
    closed = report["verdict"] == "fail" and expected_check in report["failures"]
    return {
        "format": NEGATIVE_FORMAT,
        "case": case,
        "kind": "offline",
        "injected": NEGATIVE_CASES[case]["injected"],
        "expectedCheck": expected_check,
        "verdict": report["verdict"],
        "failedChecks": report["failures"],
        "failClosed": bool(closed),
        "runId": run.get("runId"),
    }


def live_negative(case: str, run: Mapping[str, Any], report: Mapping[str, Any], *, evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Record a live fault probe that was produced by a real game operation."""
    if case not in NEGATIVE_CASES or NEGATIVE_CASES[case]["kind"] != "live":
        raise VerifyError(f"{case!r} is not a live negative case")
    expected_check = NEGATIVE_CASES[case]["expected_check"]
    closed = report.get("verdict") == "fail" and expected_check in (report.get("failures") or [])
    return {
        "format": NEGATIVE_FORMAT,
        "case": case,
        "kind": "live",
        "injected": NEGATIVE_CASES[case]["injected"],
        "expectedCheck": expected_check,
        "verdict": report.get("verdict"),
        "failedChecks": report.get("failures"),
        "failClosed": bool(closed),
        "runId": run.get("runId"),
        "evidence": dict(evidence or {}),
    }


# --------------------------------------------------------------------------- selftest


def _synthetic_run() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """A positive fixture that exercises every check without a game."""
    carts = [
        {"index": 0, "items": [{"slot": 0, "id": "minecraft:stone", "count": 1}]},
        {"index": 1, "items": [{"slot": 1, "id": "minecraft:dirt", "count": 2}]},
        {"index": 2, "items": [{"slot": 2, "id": "minecraft:gold_ingot", "count": 3}]},
    ]
    program = {"carts": carts}
    valid_source = {
        "source_save_present": True,
        "tree_sha256": "c" * 64,
        "baseline_sha256": "c" * 64,
        "matches_baseline": True,
        "files": 40,
        "bytes": 123456,
    }
    run = {
        "format": RUN_FORMAT,
        "runId": "selftest-run",
        "configId": "selftest",
        "instance": "experiment",
        "dimension": "minecraft:overworld",
        "program": program,
        "programSha256": program_sha256(carts),
        "expectedCartCount": 3,
        "agentUuid": "3ec122d5-fc27-4816-be47-bf8be8d7e56d",
        "scene": {"exitAxis": "x", "exitGreaterThan": 15.0, "dimension": "minecraft:overworld"},
        "operations": {"causalMaxTicks": 40, "transientMaxDeltaTicks": 80},
        "loggerSessionOrdinal": 1,
        "loggerJar": {"sha256": "a" * 64, "size": 100, "build": "romlog-rom21-1", "sourceSha256": "s" * 64,
                      "deployedSha256": "a" * 64},
        "auditJar": {"sha256": "b" * 64},
        "sourceWorld": {"before": dict(valid_source), "after": dict(valid_source)},
    }
    base_wall = 1_790_000_000_000
    logger_rows: list[dict[str, Any]] = [
        {"event": "logger_armed", "seq": 1, "tick": 0, "wall": base_wall + 10,
         "instance": "experiment", "run_id": "selftest-run", "dimension": "minecraft:overworld",
         "build": "romlog-rom21-1", "expectedCarts": 3, "exitAxis": "x", "exitGreaterThan": 15.0},
    ]
    audit_rows: list[dict[str, Any]] = [
        {"type": "session_start", "seq": 1, "tick": 0, "wall": base_wall, "run": "selftest-run",
         "inst": "experiment", "session": "s1", "phase": "ready"},
        {"type": "audit_ready", "seq": 2, "tick": 0, "wall": base_wall + 1, "run": "selftest-run",
         "inst": "experiment", "session": "s1", "phase": "ready",
         "config": {"dimension": "minecraft:overworld"}},
        {"type": "phase", "seq": 3, "tick": 100, "wall": base_wall + 500, "run": "selftest-run",
         "inst": "experiment", "session": "s1", "phase": "experiment", "from": "ready",
         "to": "experiment", "requested": "experiment_start"},
    ]
    seq = 10
    tick = 1000
    wall = base_wall + 2000
    for index, cart in enumerate(carts):
        request_seq, attempt_seq, processed_seq = seq, seq + 1, seq + 2
        audit_rows.append({"type": "input_request", "seq": request_seq, "tick": tick, "wall": wall,
                           "run": "selftest-run", "inst": "experiment", "session": "s1",
                           "phase": "experiment", "operator": {"uuid": run["agentUuid"], "name": "Romuser"}})
        audit_rows.append({"type": "input_attempt", "seq": attempt_seq, "tick": tick, "wall": wall,
                           "run": "selftest-run", "inst": "experiment", "session": "s1",
                           "phase": "experiment", "requestSeq": request_seq,
                           "operator": {"uuid": run["agentUuid"], "name": "Romuser"}})
        audit_rows.append({"type": "input_processed", "seq": processed_seq, "tick": tick, "wall": wall,
                           "run": "selftest-run", "inst": "experiment", "session": "s1",
                           "phase": "experiment", "requestSeq": request_seq, "attemptSeq": attempt_seq,
                           "orderingEvidence": True, "agentOp": True,
                           "operator": {"uuid": run["agentUuid"], "name": "Romuser"}})
        exit_tick = tick + 4
        remove_tick = tick + 140
        uuid = f"00000000-0000-0000-0000-00000000000{index}"
        audit_rows.append({"type": "cart_exit", "seq": seq + 3, "tick": exit_tick, "wall": wall + 50,
                           "run": "selftest-run", "inst": "experiment", "session": "s1",
                           "phase": "experiment", "uuid": uuid, "epoch": 1, "level": "minecraft:overworld",
                           "inventory": cart["items"], "inventoryDigest": f"d{index}"})
        audit_rows.append({"type": "cart_remove", "seq": seq + 4, "tick": remove_tick, "wall": wall + 200,
                           "run": "selftest-run", "inst": "experiment", "session": "s1",
                           "phase": "experiment", "uuid": uuid, "epoch": 1, "level": "minecraft:overworld",
                           "reason": "DISCARDED", "inventory": cart["items"], "inventoryDigest": f"d{index}"})
        logger_rows.append({"event": "cart_observed", "seq": seq + 5, "tick": exit_tick + 1, "wall": wall + 60,
                            "instance": "experiment", "run_id": "selftest-run",
                            "dimension": "minecraft:overworld", "build": "romlog-rom21-1", "uuid": uuid,
                            "via": "left_stack_region", "items": cart["items"], "inventoryDigest": f"d{index}"})
        logger_rows.append({"event": "cart_void_capture", "seq": seq + 6, "tick": remove_tick, "wall": wall + 200,
                            "instance": "experiment", "run_id": "selftest-run",
                            "dimension": "minecraft:overworld", "build": "romlog-rom21-1", "uuid": uuid,
                            "removalReason": "DISCARDED", "items": cart["items"], "inventoryDigest": f"d{index}"})
        seq += 7
        tick += 16
        wall += 16000
    logger_rows.append({"event": "logger_flushed", "seq": seq, "tick": tick, "wall": wall + 1000,
                        "instance": "experiment", "run_id": "selftest-run",
                        "dimension": "minecraft:overworld", "build": "romlog-rom21-1",
                        "observed": 3, "voidCaptured": 3})
    audit_rows.append({"type": "cart_remove", "seq": seq + 1, "tick": tick + 100, "wall": wall + 1100,
                       "run": "selftest-run", "inst": "experiment", "session": "s1", "phase": "experiment",
                       "uuid": "ffffffff-0000-0000-0000-000000000000", "level": "minecraft:overworld",
                       "reason": "KILLED"})
    audit_rows.append({"type": "audit_end", "seq": seq + 2, "tick": tick + 101, "wall": wall + 1200,
                       "run": "selftest-run", "inst": "experiment", "session": "s1", "phase": "end",
                       "status": "complete"})
    restore = {"ok": True, "verification": {"dimension": {"match": True}, "orderHash": {"match": True},
                                            "uuidOrder": {"match": True}, "matched": 4}}
    check = {"verdict": "pass"}
    return run, logger_rows, audit_rows, {"audit_check": check, "restore_verify": restore}


def selftest() -> int:
    run, logger_rows, audit_rows, extras = _synthetic_run()
    failures: list[str] = []

    def expect(name: str, report: dict[str, Any], *, verdict: str, failing: str | None = None) -> None:
        ok = report.get("verdict") == verdict
        if failing:
            ok = ok and failing in (report.get("failures") or [])
        print(f"[{'ok' if ok else 'FAIL'}] {name}: verdict={report.get('verdict')} failures={report.get('failures')}")
        if not ok:
            failures.append(name)

    expect("positive", verify_run(run, logger_rows, audit_rows, **extras), verdict="pass")
    # logger late
    late = copy.deepcopy(logger_rows)
    for row in late:
        if row.get("event") == "cart_observed":
            row["tick"] = int(row["tick"]) + 500
    expect("negative-logger-late", verify_run(run, late, audit_rows, **extras), verdict="fail", failing="capture_timing")
    # missed capture
    missed = [row for row in logger_rows if row.get("event") != "cart_observed"]
    expect("negative-logger-missed", verify_run(run, missed, audit_rows, **extras), verdict="fail", failing="transient_captures")
    # output order reversed
    reversed_rows = copy.deepcopy(logger_rows)
    observed = [row for row in reversed_rows if row.get("event") == "cart_observed"]
    observed[0]["tick"], observed[1]["tick"] = observed[1]["tick"], observed[0]["tick"]
    expect("negative-output-order", verify_run(run, reversed_rows, audit_rows, **extras), verdict="fail", failing="output_order")
    # inventory wrong
    tampered = copy.deepcopy(logger_rows)
    first_capture = next(row for row in tampered if row.get("event") == "cart_observed")
    first_capture["items"] = [{"slot": 0, "id": "minecraft:stone", "count": 64}]
    expect("negative-inventory", verify_run(run, tampered, audit_rows, **extras), verdict="fail", failing="inventory_matches")
    # audit missing
    expect("negative-audit-missing",
           verify_run(run, logger_rows, [], audit_check=None, restore_verify=extras["restore_verify"]),
           verdict="fail", failing="audit_oracle")
    # wrong instance
    wrong_instance = copy.deepcopy(logger_rows)
    for row in wrong_instance:
        if "instance" in row:
            row["instance"] = "other"
    expect("negative-wrong-instance", verify_run(run, wrong_instance, audit_rows, **extras), verdict="fail", failing="logger_session")
    # no operations (all processed removed; contract: agent_operations fails)
    no_ops = [row for row in audit_rows if row.get("type") != "input_processed"]
    expect("negative-no-operation", verify_run(run, logger_rows, no_ops, **extras), verdict="fail", failing="agent_operations")
    # restore order wrong
    broken_restore = {"ok": True, "verification": {"dimension": {"match": True}, "orderHash": {"match": False},
                                                   "uuidOrder": {"match": False}, "matched": 4}}
    expect("negative-restore-order", verify_run(run, logger_rows, audit_rows, audit_check=extras["audit_check"],
                                                 restore_verify=broken_restore), verdict="fail", failing="restore_verified")
    # same-size jar iteration accepted; different sizes rejected
    iter_run = copy.deepcopy(run)
    iter_run["loggerJarIteration"] = {"sha256": "e" * 64, "size": 100, "build": "romlog-rom21-2",
                                      "sourceSha256": "s" * 64, "deployedSha256": "e" * 64}
    expect("iteration-ok", verify_run(iter_run, logger_rows, audit_rows, **extras), verdict="pass")
    bad_iter = copy.deepcopy(iter_run)
    bad_iter["loggerJarIteration"]["size"] = 101
    expect("iteration-size-mismatch", verify_run(bad_iter, logger_rows, audit_rows, **extras), verdict="fail",
           failing="jar_provenance")

    print(f"selftest: {'PASS' if not failures else 'FAIL ' + ', '.join(failures)}")
    return 0 if not failures else 1


# --------------------------------------------------------------------------- cli


def cmd_selftest(_args: argparse.Namespace) -> int:
    return selftest()


def cmd_config(args: argparse.Namespace) -> int:
    config = load_config(Path(args.config))
    print(json.dumps({
        "configId": config["configId"],
        "expectedCartCount": config["expectedCartCount"],
        "programSha256": config["programSha256"],
    }, indent=2))
    return 0


def _load_run_dir(run_dir: Path) -> dict[str, Any]:
    run_dir = Path(run_dir)
    run = read_json(run_dir / "run.json")
    logger_rows = read_jsonl(run_dir / "logger.jsonl")
    audit_rows = read_jsonl(run_dir / "audit.jsonl")
    audit_check = read_json(run_dir / "audit-check.json") if (run_dir / "audit-check.json").is_file() else None
    restore = read_json(run_dir / "restore-verify.json") if (run_dir / "restore-verify.json").is_file() else None
    return verify_run(run, logger_rows, audit_rows, audit_check=audit_check, restore_verify=restore)


def cmd_verify(args: argparse.Namespace) -> int:
    report = _load_run_dir(Path(args.run))
    output = Path(args.out) if args.out else None
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "failures": report["failures"],
                      "problems": report["problems"], "answer": report["answer"]}, indent=2))
    return 0 if report["verdict"] == "pass" else 1


def cmd_negative(args: argparse.Namespace) -> int:
    report = offline_negative(Path(args.run), args.case)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["failClosed"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("selftest", help="offline positive + fail-closed negatives")
    p.set_defaults(handler=cmd_selftest)
    p = sub.add_parser("config", help="validate a regression config")
    p.add_argument("--config", required=True)
    p.set_defaults(handler=cmd_config)
    p = sub.add_parser("verify", help="verify one run directory")
    p.add_argument("--run", required=True)
    p.add_argument("--out", default="")
    p.set_defaults(handler=cmd_verify)
    p = sub.add_parser("negative", help="offline negative mutation of a real run")
    p.add_argument("--case", required=True, choices=sorted(NEGATIVE_CASES))
    p.add_argument("--run", required=True)
    p.add_argument("--out", default="")
    p.set_defaults(handler=cmd_negative)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except VerifyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
