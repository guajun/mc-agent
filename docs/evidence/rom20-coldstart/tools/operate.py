#!/usr/bin/env python3
"""Operate the acquired Machine Cart ROM through the note block.

Runs the task action: right-click the note block with the Carpet fake player
`romuser` (Carpet `use once`), once per cycle, until every cart has been
ejected. Polls the evaluator audit log (experiment phase) and the agent's own
romlog for the transient output, then freezes the world again.

This script never edits the machine or the carts; it only presses the input.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

RUNNER = Path("F:/mc-agent-worktrees/rom13/coldstart/examples/minecart-rom/runner")
sys.path.insert(0, str(RUNNER))

import fixture  # noqa: E402

ROOT = Path("F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-exp")
AUDIT = ROOT / "mc-audit" / "audit-rom20-20260926T063100Z.jsonl"
ROMLOG = ROOT / "audit" / "romlogger" / "romlog.jsonl"


def audit_counts() -> tuple[int, int]:
    exits = removes = 0
    if AUDIT.is_file():
        for line in AUDIT.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("phase") != "experiment":
                continue
            if record.get("type") == "cart_exit":
                exits += 1
            elif record.get("type") == "cart_remove":
                removes += 1
    return exits, removes


def agent_captures() -> list[str]:
    uuids: list[str] = []
    if ROMLOG.is_file():
        for line in ROMLOG.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event") == "cart_observed" and record.get("via") == "left_stack_region":
                uuids.append(str(record.get("uuid")))
    return uuids


def main() -> int:
    console = fixture.Console("rom20-exp", verbose=False).connect()
    steps: list[dict] = []
    try:
        status = console.cmd("mcaudit status", idle=0.2)
        if "phase=experiment" not in status:
            print(json.dumps({"ok": False, "error": f"audit phase is not experiment: {status}"}))
            return 1
        if "game is frozen" in console.cmd("tick query", idle=0.2).lower():
            console.cmd("tick unfreeze", idle=0.2)
        console.cmd("mcaudit mark operation-begin", idle=0.2)
        for press in range(1, 6):
            before_exits, before_removes = audit_counts()
            before_captures = len(agent_captures())
            started = time.monotonic()
            console.cmd("player Romuser use once", idle=0.15)
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                exits, _removes = audit_counts()
                captures = len(agent_captures())
                if exits > before_exits and captures > before_captures:
                    break
                time.sleep(0.05)
            exits, removes = audit_counts()
            captures = len(agent_captures())
            steps.append(
                {
                    "press": press,
                    "audit_exits": exits,
                    "audit_removes": removes,
                    "agent_captures": captures,
                    "new_exit": exits > before_exits,
                    "new_capture": captures > before_captures,
                    "elapsed_s": round(time.monotonic() - started, 3),
                }
            )
            # one machine cycle is 4 ticks; leave the world running a few more
            time.sleep(0.35)
        deadline = time.monotonic() + 180.0
        while time.monotonic() < deadline:
            exits, removes = audit_counts()
            if removes >= 5:
                break
            time.sleep(0.25)
        exits, removes = audit_counts()
        console.cmd("tick freeze", idle=0.3)
        console.cmd("mcaudit mark operation-end", idle=0.2)
        report = {
            "ok": removes >= 5,
            "presses": 5,
            "audit_exits": exits,
            "audit_removes": removes,
            "agent_capture_uuids": agent_captures(),
            "steps": steps,
            "tick_query": console.cmd("tick query", idle=0.2),
        }
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    finally:
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
