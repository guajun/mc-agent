#!/usr/bin/env python3
"""Read the independent minecart-audit JSONL and decide whether a run is auditable.

This is the test side of issue #18: the audit mod writes raw server-side
evidence, this program reads it and answers the questions the acceptance
criteria ask - did the agent actually operate the machine, was every machine
input correlated with the server processing it, and was every transient
minecart captured before it fell into the void.

    python tools/minecart_audit.py check --log <audit.jsonl> [--json out.json]
    python tools/minecart_audit.py selftest

Exit codes: 0 pass, 1 fail, 2 incomplete/unreadable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

KNOWN_TYPES = {
    "session_start",
    "audit_ready",
    "phase",
    "mark",
    "input_attempt",
    "input_request",
    "input_processed",
    "cart_tracked",
    "cart_reload",
    "cart_reappeared",
    "cart_sample",
    "cart_exit",
    "cart_remove",
    "cart_inventory_change",
    "cart_teleport",
    "audit_incomplete",
    "audit_end",
}

# Phases where an Agent operation may be credited.
OPERATION_PHASES = {"experiment"}
PHASE_ORDER = ("ready", "init", "restore", "experiment", "post", "end")


def read_events(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        return [], [f"cannot read {path}: {error}"]
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except ValueError as error:
            errors.append(f"line {number}: invalid JSON ({error})")
            continue
        if not isinstance(event, dict):
            errors.append(f"line {number}: event is not an object")
            continue
        events.append(event)
    return events, errors


class Checks:
    def __init__(self) -> None:
        self.results: dict[str, dict[str, Any]] = {}

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.results[name] = {"pass": bool(passed), "detail": detail}

    def add_info(self, name: str, detail: str) -> None:
        self.results[name] = {"pass": True, "detail": detail, "informational": True}

    def failed(self, *names: str) -> bool:
        return any(not self.results.get(name, {"pass": True})["pass"] for name in names)

    def all_pass(self, *names: str) -> bool:
        return all(self.results.get(name, {"pass": True})["pass"] for name in names)


def split_sessions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split a log into server sessions.

    A new-format log opens a session with ``session_start`` and closes it with
    ``audit_end``. Legacy logs (no ``session_start``) may still be restarted on
    the same file, so an ``audit_end`` also ends a session and the next event
    begins the next one.
    """
    sessions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in events:
        starts_new = (
            current is None
            or event.get("type") == "session_start"
            or (current["events"] and current["events"][-1].get("type") == "audit_end")
        )
        if starts_new:
            current = {
                "id": event.get("session") or event.get("sessionId") or "legacy",
                "events": [],
            }
            sessions.append(current)
        current["events"].append(event)
    return sessions


def session_summary(session: dict[str, Any]) -> dict[str, Any]:
    events = session["events"]
    ends = [event for event in events if event.get("type") == "audit_end"]
    return {
        "id": session["id"],
        "events": len(events),
        "firstSeq": events[0].get("seq") if events else None,
        "lastSeq": events[-1].get("seq") if events else None,
        "ready": any(event.get("type") == "audit_ready" for event in events),
        "ended": bool(ends),
        "endStatus": ends[-1].get("status") if ends else None,
    }


def evaluate(
    events: list[dict[str, Any]],
    parse_errors: list[str] | None = None,
    status: dict[str, Any] | None = None,
    require_machine: bool = True,
) -> dict[str, Any]:
    checks = Checks()
    parse_errors = list(parse_errors or [])
    reasons: list[str] = []

    # ----- session shape and stream ----------------------------------------
    if not events:
        checks.add("stream", False, "no events parsed")
        checks.add("sessions", False, "no sessions")
        return _report(checks, events, [], "incomplete", ["no events"], require_machine)
    missing_fields = [
        event.get("seq")
        for event in events
        if any(field not in event for field in ("seq", "tick", "type", "run", "inst"))
    ]
    seqs = [event["seq"] for event in events if "seq" in event]
    sessions = split_sessions(events)

    def strictly_increasing(values: list[int]) -> bool:
        return all(left < right for left, right in zip(values, values[1:]))

    per_session_seq_ok = all(
        strictly_increasing([event["seq"] for event in session["events"] if "seq" in event])
        and len({event["seq"] for event in session["events"] if "seq" in event})
        == len([event for event in session["events"] if "seq" in event])
        for session in sessions
    )
    new_format = any(event.get("type") == "session_start" for event in events)
    seq_ok = per_session_seq_ok and (not new_format or seqs == sorted(seqs))
    tick_ok = all(
        isinstance(event.get("tick"), int) and event["tick"] >= 0
        for event in events
        if "tick" in event
    )
    checks.add(
        "stream",
        not parse_errors and not missing_fields and seq_ok and tick_ok,
        "; ".join(
            parse_errors
            + ([f"missing common fields on seq {missing_fields[:3]}"] if missing_fields else [])
            + ([] if seq_ok else ["seq is not strictly increasing and unique within a session"])
            + ([] if tick_ok else ["some events have no non-negative integer tick"])
        ),
    )
    unknown = sorted({str(event.get("type")) for event in events} - KNOWN_TYPES)
    if unknown:
        checks.add_info("unknown_event_types", f"unrecognized types present: {unknown}")

    runs = {str(event.get("run")) for event in events}
    insts = {str(event.get("inst")) for event in events}
    checks.add(
        "run_identity",
        len(runs) == 1 and len(insts) == 1,
        f"run={sorted(runs)} inst={sorted(insts)}",
    )

    summaries = [session_summary(session) for session in sessions]
    ended_sessions = [session for session in sessions if session_summary(session)["ended"]]
    open_sessions = [session for session in sessions if not session_summary(session)["ended"]]
    # The run under evaluation is always the latest session: if it is still
    # open the verdict is incomplete, even when an earlier session completed.
    checked_session = sessions[-1] if sessions else None
    checked_id = session_summary(checked_session)["id"] if checked_session else None

    checks.add(
        "sessions",
        bool(ended_sessions),
        f"{len(sessions)} session(s); ended={len(ended_sessions)}; open={len(open_sessions)}; "
        f"evaluated={checked_id}",
    )
    if not ended_sessions:
        reasons.append("no completed session (audit_end missing)")

    session_events = checked_session["events"] if checked_session else []
    incomplete_events = [
        event for event in session_events if event.get("type") == "audit_incomplete"
    ]
    truncated = any(
        event.get("type") == "audit_end" and event.get("truncated") for event in session_events
    ) or bool((status or {}).get("truncated"))
    end_events = [event for event in session_events if event.get("type") == "audit_end"]
    end = end_events[-1] if end_events else None
    if end is not None and end.get("status") != "complete":
        reasons.extend(str(reason) for reason in end.get("incompleteReasons") or [])
    reasons.extend(str(event.get("reason")) for event in incomplete_events)
    if truncated:
        reasons.append("audit log truncated by maxBytes")
    checks.add(
        "completeness",
        not incomplete_events and not truncated and end is not None and end.get("status") == "complete",
        f"end={None if end is None else end.get('status')} "
        f"incomplete={[event.get('reason') for event in incomplete_events]} truncated={truncated}",
    )
    if status is not None and not status.get("ended") and open_sessions:
        checks.add("status_agrees", False, "status.json says the run is still open; audit may have died")
    else:
        checks.add("status_agrees", True, "status snapshot consistent" if status else "no status file given")

    # ----- phase model ------------------------------------------------------
    phase = None
    phase_errors: list[str] = []
    experiment_windows: list[tuple[int, int | None]] = []
    experiment_start_seq: int | None = None
    for event in session_events:
        kind = event.get("type")
        if kind == "audit_ready":
            phase = "ready"
        elif kind == "phase":
            source, target = event.get("from"), event.get("to")
            if target not in PHASE_ORDER or (phase is not None and source != phase):
                phase_errors.append(
                    f"seq {event.get('seq')}: phase {source}->{target} does not continue {phase}"
                )
            if target == "experiment":
                experiment_start_seq = event.get("seq")
            if target == "post" and experiment_start_seq is not None:
                experiment_windows.append((experiment_start_seq, event.get("seq")))
            phase = target or phase
    checks.add(
        "lifecycle",
        bool(session_events) and any(event.get("type") == "audit_ready" for event in session_events)
        and not phase_errors,
        "; ".join(phase_errors) or f"phases valid; experiment windows={len(experiment_windows)}",
    )

    # ----- input correlation ------------------------------------------------
    by_seq = {event.get("seq"): event for event in session_events}
    request_seqs = {
        event.get("seq")
        for event in session_events
        if event.get("type") == "input_request"
    }
    processed = [event for event in session_events if event.get("type") == "input_processed"]
    target_processed = [event for event in processed if event.get("targetInput")]
    chain_errors: list[str] = []
    agent_ops: list[dict[str, Any]] = []
    environment_ops = 0
    for event in target_processed:
        request_seq = event.get("requestSeq")
        request = by_seq.get(request_seq)
        if request is None or request_seq not in request_seqs:
            chain_errors.append(
                f"seq {event.get('seq')}: target input processed without a playNote request"
            )
            continue
        if not (isinstance(request_seq, int) and request_seq < event.get("seq", 0)):
            chain_errors.append(f"seq {event.get('seq')}: request is not before the processing event")
            continue
        window = _correlation_window(session_events)
        tick_delta = (event.get("tick") or 0) - (request.get("tick") or 0)
        if tick_delta < 0 or tick_delta > window + 1:
            chain_errors.append(
                f"seq {event.get('seq')}: request tick {request.get('tick')} is outside the "
                f"{window}-tick window of processing tick {event.get('tick')}"
            )
            continue
        if event.get("agentOp"):
            attempt_seq = event.get("attemptSeq")
            attempt = by_seq.get(attempt_seq)
            if attempt is None or attempt.get("type") != "input_attempt":
                chain_errors.append(
                    f"seq {event.get('seq')}: agent operation without a matching use attempt"
                )
                continue
            agent_ops.append(event)
        elif event.get("operator") is not None:
            chain_errors.append(
                f"seq {event.get('seq')}: a player request was not classified as an agent op"
            )
        else:
            environment_ops += 1
    checks.add(
        "input_chain",
        not chain_errors,
        "; ".join(chain_errors[:5]) or f"{len(target_processed)} target processing event(s), all correlated",
    )
    phase_isolation_ok = all(
        event.get("phase") in OPERATION_PHASES for event in agent_ops
    )
    checks.add(
        "phase_isolation",
        phase_isolation_ok,
        "agent operations confined to the experiment phase"
        if phase_isolation_ok
        else f"agent operations outside {sorted(OPERATION_PHASES)}",
    )
    machine_operated = bool(agent_ops)
    checks.add(
        "machine_operated",
        machine_operated or not require_machine,
        f"{len(agent_ops)} correlated agent operation(s); {environment_ops} environment trigger(s)",
    )

    # ----- cart capture -----------------------------------------------------
    carts: dict[tuple[str, int], dict[str, Any]] = {}
    duplicates: list[str] = []
    for event in session_events:
        kind = event.get("type")
        key = (str(event.get("uuid")), int(event.get("epoch") or 1))
        if kind == "cart_tracked":
            if key in carts:
                duplicates.append(f"seq {event.get('seq')}: duplicate cart_tracked {key}")
            carts.setdefault(
                key,
                {
                    "trackedSeq": event.get("seq"),
                    "trackedPhase": event.get("phase"),
                    "trackedInventory": event.get("inventory"),
                    "type": event.get("cartType"),
                    "exit": None,
                    "remove": None,
                },
            )
        elif kind == "cart_exit" and key in carts:
            if carts[key]["exit"] is not None:
                duplicates.append(f"seq {event.get('seq')}: duplicate cart_exit {key}")
            carts[key]["exit"] = event
        elif kind == "cart_remove" and key in carts:
            if carts[key]["remove"] is not None:
                duplicates.append(f"seq {event.get('seq')}: duplicate cart_remove {key}")
            carts[key]["remove"] = event
    capture_errors = list(duplicates)
    for key, cart in carts.items():
        remove = cart["remove"]
        if remove is None:
            # still alive when the audit ended; the verifier reports it but does
            # not call that a capture failure by itself
            continue
        if remove.get("phase") not in OPERATION_PHASES and remove.get("phase") != "post":
            continue
        inventory = remove.get("inventory")
        if not isinstance(inventory, list):
            capture_errors.append(
                f"{key}: permanent removal has no ordered inventory ({remove.get('inventory')!r})"
            )
        exit_event = cart["exit"]
        if exit_event is None:
            capture_errors.append(f"{key}: removed without any exit/out-of-bounds evidence")
            continue
        if exit_event.get("seq") >= remove.get("seq"):
            capture_errors.append(f"{key}: exit is not before removal")
    checks.add(
        "transient_outputs",
        not capture_errors,
        "; ".join(capture_errors[:5]) or f"{len(carts)} tracked cart(s) checked",
    )

    # ----- ordering ---------------------------------------------------------
    ordering_errors: list[str] = []
    exits = [cart["exit"] for cart in carts.values() if cart["exit"]]
    exit_seqs = [event.get("seq") for event in exits]
    if exit_seqs != sorted(exit_seqs) or len(set(exit_seqs)) != len(exit_seqs):
        ordering_errors.append("cart exits are not in unique occurrence order")
    for key, cart in carts.items():
        exit_event, remove = cart["exit"], cart["remove"]
        if exit_event is not None and cart["trackedSeq"] is not None:
            if exit_event.get("seq") <= cart["trackedSeq"]:
                ordering_errors.append(f"{key}: exit before the cart was tracked")
        if exit_event is not None and remove is not None and remove.get("seq") <= exit_event.get("seq"):
            ordering_errors.append(f"{key}: removal is not after the exit")
    per_tick: dict[int, list[int]] = {}
    for event in exits:
        per_tick.setdefault(event.get("tick"), []).append(event.get("seq"))
    for tick, seq_list in per_tick.items():
        if len(seq_list) > 1 and len(set(seq_list)) != len(seq_list):
            ordering_errors.append(f"tick {tick}: multiple outputs share a sequence number")
    checks.add(
        "ordering",
        not ordering_errors,
        "; ".join(ordering_errors[:5]) or f"{len(exits)} exit(s) in actual occurrence order",
    )

    # ----- verdict ----------------------------------------------------------
    # Infrastructure-level problems mean the evidence cannot be judged; a
    # behavioral problem means the run is judged and fails.
    infra = ("stream", "sessions", "completeness", "status_agrees")
    behavior = ("run_identity", "lifecycle", "input_chain", "phase_isolation",
                "machine_operated", "transient_outputs", "ordering")
    if checks.failed(*infra):
        verdict = "incomplete"
        reasons.extend(
            name for name in ("stream", "sessions", "completeness", "status_agrees")
            if checks.failed(name)
        )
    elif checks.failed(*behavior):
        verdict = "fail"
    else:
        verdict = "pass"
    return _report(checks, events, sessions, verdict, reasons, require_machine, agent_ops, carts)


def _correlation_window(events: list[dict[str, Any]]) -> int:
    for event in events:
        if event.get("type") == "audit_ready":
            config = event.get("config") or {}
            try:
                return int(config.get("correlationWindowTicks") or 2)
            except (TypeError, ValueError):
                return 2
    return 2


def _report(
    checks: Checks,
    events: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    verdict: str,
    reasons: list[str],
    require_machine: bool,
    agent_ops: list[dict[str, Any]] | None = None,
    carts: dict[tuple[str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    outputs = []
    for key, cart in (carts or {}).items():
        remove = cart["remove"] or {}
        exit_event = cart["exit"] or {}
        outputs.append(
            {
                "uuid": key[0],
                "epoch": key[1],
                "exitSeq": exit_event.get("seq"),
                "exitTick": exit_event.get("tick"),
                "exitPos": exit_event.get("pos"),
                "removeSeq": remove.get("seq"),
                "removeReason": remove.get("reason"),
                "inventory": remove.get("inventory"),
            }
        )
    return {
        "verdict": verdict,
        "require_machine": require_machine,
        "reasons": sorted(set(reasons)),
        "sessions": [session_summary(session) for session in sessions],
        "machine_operated": bool(agent_ops),
        "agentOperations": [
            {
                "seq": event.get("seq"),
                "tick": event.get("tick"),
                "operator": event.get("operator"),
                "requestSeq": event.get("requestSeq"),
                "attemptSeq": event.get("attemptSeq"),
            }
            for event in (agent_ops or [])
        ],
        "outputs": outputs,
        "checks": checks.results,
        "events": len(events),
        "run": events[0].get("run") if events else None,
        "instance": events[0].get("inst") if events else None,
    }


def print_report(report: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2))
        return
    print(f"verdict: {report['verdict']}  run={report['run']} inst={report['instance']} events={report['events']}")
    print(f"  machine_operated: {report['machine_operated']} ({len(report['agentOperations'])} ops)")
    print(f"  outputs captured: {len(report['outputs'])}")
    for name, result in report["checks"].items():
        mark = "ok " if result["pass"] else "FAIL"
        suffix = f" - {result['detail']}" if result.get("detail") else ""
        print(f"  [{mark}] {name}{suffix}")
    if report["reasons"]:
        print("  reasons: " + ", ".join(report["reasons"]))


def cmd_check(args: argparse.Namespace) -> int:
    path = Path(args.log)
    events, errors = read_events(path)
    status = None
    if args.status:
        try:
            status = json.loads(Path(args.status).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"warning: cannot read status file: {error}", file=sys.stderr)
    report = evaluate(events, errors, status, require_machine=not args.allow_no_operation)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print_report(report, args.json)
    if args.expect_verdict:
        expected = args.expect_verdict.split(",")
        if report["verdict"] not in expected:
            print(f"expected verdict in {expected}, got {report['verdict']}", file=sys.stderr)
            return 1
        return 0
    return {"pass": 0, "fail": 1, "incomplete": 2}[report["verdict"]]


def cmd_summary(args: argparse.Namespace) -> int:
    events, errors = read_events(Path(args.log))
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.get("type"))] = counts.get(str(event.get("type")), 0) + 1
    print(json.dumps({"events": len(events), "errors": errors, "counts": counts}, indent=2))
    return 0 if not errors else 1


# --------------------------------------------------------------------------- selftest


def _event(seq: int, kind: str, run: str = "run-1", inst: str = "inst-1", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "seq": seq,
        "tick": 100 + seq,
        "wall": 1700000000000 + seq,
        "run": run,
        "inst": inst,
        "type": kind,
        "phase": extra.pop("phase", "experiment"),
    }
    base.update(extra)
    return base


def _base_stream() -> list[dict[str, Any]]:
    """A minimal complete session: ready -> init -> one agent operation -> one cart output."""
    return [
        _event(1, "session_start", phase="bootstrap"),
        _event(2, "audit_ready", phase="ready", config={"correlationWindowTicks": 2}),
        _event(3, "phase", phase="ready", **{"from": "ready", "to": "init"}),
        _event(4, "cart_tracked", phase="init", uuid="u1", epoch=1,
               cartType="minecraft:chest_minecart", pos={"x": 1, "y": 2, "z": 3},
               inventory=[{"slot": 0, "item": "minecraft:apple", "count": 3}]),
        _event(5, "phase", phase="init", **{"from": "init", "to": "experiment"}),
        _event(6, "input_attempt", phase="experiment", pos={"x": 0, "y": 0, "z": 0},
               targetInput=True, operator={"uuid": "agent-1", "name": "Bot"},
               path="useWithoutItem", result="SUCCESS", requestSeq=None),
        _event(7, "input_request", phase="experiment", pos={"x": 0, "y": 0, "z": 0},
               targetInput=True, trigger="player", operator={"uuid": "agent-1", "name": "Bot"}),
        _event(8, "input_processed", phase="experiment", pos={"x": 0, "y": 0, "z": 0},
               targetInput=True, requestSeq=7, attemptSeq=6,
               operator={"uuid": "agent-1", "name": "Bot"}, agentOp=True,
               orderingEvidence=True, played=True),
        _event(9, "cart_exit", phase="experiment", uuid="u1", epoch=1,
               cartType="minecraft:chest_minecart", pos={"x": 4, "y": -59, "z": 0},
               via="left_stack_region",
               inventory=[{"slot": 0, "item": "minecraft:apple", "count": 3}]),
        _event(10, "cart_remove", phase="experiment", uuid="u1", epoch=1,
               cartType="minecraft:chest_minecart", reason="DISCARDED", permanent=True,
               pos={"x": 4, "y": -128, "z": 0},
               inventory=[{"slot": 0, "item": "minecraft:apple", "count": 3}]),
        _event(11, "phase", phase="experiment", **{"from": "experiment", "to": "post"}),
        _event(12, "audit_end", phase="post", status="complete", sessionId="s1",
               incompleteReasons=[], truncated=False),
    ]


def _renumber(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping: dict[int, int] = {}
    for index, event in enumerate(events, start=1):
        mapping[event.get("seq")] = index
        event["seq"] = index
        event["tick"] = 100 + index
    for event in events:
        for reference in ("requestSeq", "attemptSeq"):
            if event.get(reference) in mapping:
                event[reference] = mapping[event[reference]]
    return events


def _drop_types(events: list[dict[str, Any]], *types: str) -> list[dict[str, Any]]:
    return _renumber([dict(event) for event in events if event["type"] not in types])


def _selftest_cases() -> list[tuple[str, list[dict[str, Any]], str]]:
    cases: list[tuple[str, list[dict[str, Any]], str]] = []

    def add(name: str, events: list[dict[str, Any]], expected: str) -> None:
        cases.append((name, _renumber([dict(event) for event in events]), expected))

    add("positive", _base_stream(), "pass")

    # negatives the acceptance criteria name explicitly
    add("no_operation", _drop_types(_base_stream(), "input_attempt", "input_request", "input_processed"), "fail")

    wrong_position = [dict(event) for event in _base_stream()]
    for event in wrong_position:
        if event["type"].startswith("input_"):
            event["targetInput"] = False
            event["region"] = None
    add("wrong_position", wrong_position, "fail")

    marker_only = _drop_types(_base_stream(), "input_attempt", "input_request", "input_processed")
    marker_only.insert(5, _event(0, "mark", phase="init", label="answer=apple x3"))
    add("marker_only", marker_only, "fail")

    answer_only = _drop_types(_base_stream(), "input_attempt", "input_request", "input_processed")
    answer_only.insert(5, _event(0, "mark", phase="init", label="answer",
                                 answer=["minecraft:apple x3"]))
    add("answer_only", answer_only, "fail")

    missing_request = [dict(event) for event in _base_stream()]
    for event in missing_request:
        if event["type"] == "input_processed":
            event["requestSeq"] = None
    add("missing_request", missing_request, "fail")

    environment_only = [dict(event) for event in _base_stream()]
    environment_only = [event for event in environment_only if event["type"] != "input_attempt"]
    for event in environment_only:
        if event["type"] == "input_processed":
            event["agentOp"] = False
            event["operator"] = None
            event["attemptSeq"] = None
    add("environment_only", environment_only, "fail")

    pre_experiment_operation = [dict(event) for event in _base_stream()]
    for event in pre_experiment_operation:
        if event["type"] == "input_processed":
            event["phase"] = "init"
    add("pre_experiment_operation", pre_experiment_operation, "fail")

    same_tick = [
        event for event in _base_stream()
        if event["type"] != "audit_end" and not (event["type"] == "phase" and event.get("to") == "post")
    ]
    same_tick.insert(4, _event(0, "cart_tracked", phase="init", uuid="u2", epoch=1,
                               cartType="minecraft:chest_minecart", pos={"x": 1, "y": 2, "z": 4},
                               inventory=[{"slot": 0, "item": "minecraft:gold_ingot", "count": 1}]))
    last_tick = same_tick[-1]["tick"]
    same_tick.append(_event(0, "cart_exit", phase="experiment", uuid="u2", epoch=1,
                            tick=last_tick, cartType="minecraft:chest_minecart",
                            pos={"x": 4, "y": -59, "z": 1}, via="left_stack_region",
                            inventory=[{"slot": 0, "item": "minecraft:gold_ingot", "count": 1}]))
    same_tick.append(_event(0, "cart_remove", phase="experiment", uuid="u2", epoch=1,
                            tick=last_tick, cartType="minecraft:chest_minecart",
                            reason="DISCARDED", permanent=True, pos={"x": 4, "y": -128, "z": 1},
                            inventory=[{"slot": 0, "item": "minecraft:gold_ingot", "count": 1}]))
    same_tick.append(_event(0, "phase", phase="experiment", **{"from": "experiment", "to": "post"}))
    same_tick.append(_event(0, "audit_end", phase="post", status="complete", sessionId="s1",
                            incompleteReasons=[], truncated=False))
    add("same_tick_multi_output", same_tick, "pass")

    duplicate_exit = [dict(event) for event in _base_stream()]
    duplicate_exit.insert(9, dict(duplicate_exit[8]))
    add("duplicate_exit", duplicate_exit, "fail")

    mixed_instance = [dict(event) for event in _base_stream()]
    mixed_instance[-1]["inst"] = "other-inst"
    add("mixed_instance", mixed_instance, "fail")

    truncated = [dict(event) for event in _base_stream()]
    truncated[-1]["truncated"] = True
    truncated[-1]["status"] = "incomplete"
    add("truncated", truncated, "incomplete")

    add("missing_end", _drop_types(_base_stream(), "audit_end"), "incomplete")
    return cases


def cmd_selftest(_args: argparse.Namespace) -> int:
    failures = 0
    for name, events, expected in _selftest_cases():
        report = evaluate(events)
        ok = report["verdict"] == expected
        print(f"[{'ok' if ok else 'FAIL'}] {name}: verdict={report['verdict']} expected={expected}")
        if not ok:
            failures += 1
            print_report(report, as_json=False)
    print(f"selftest: {'all cases passed' if failures == 0 else str(failures) + ' case(s) failed'}")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="evaluate one audit JSONL file")
    check.add_argument("--log", required=True)
    check.add_argument("--status", default="", help="optional status-<run>.json")
    check.add_argument("--json", action="store_true", help="print the report as JSON")
    check.add_argument("--json-out", default="", help="also write the report here")
    check.add_argument("--expect-verdict", default="", help="comma separated allowed verdicts")
    check.add_argument("--allow-no-operation", action="store_true", help="do not require a machine operation")
    check.set_defaults(handler=cmd_check)
    summary = commands.add_parser("summary", help="event type counts")
    summary.add_argument("--log", required=True)
    summary.set_defaults(handler=cmd_summary)
    selftest = commands.add_parser("selftest", help="run the synthetic check cases")
    selftest.set_defaults(handler=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
