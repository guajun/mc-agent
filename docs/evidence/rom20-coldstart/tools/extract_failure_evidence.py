#!/usr/bin/env python3
"""Re-extract the preserved failure evidence byte-exactly (review item 3 + 2).

1. The failed-restore tool result is taken from the frozen pi session, line 169
   (1-indexed), the toolResult with toolCallId
   `call_00_MR03DuPnyR02Opv6Y1bu4504`, and written with the exact CRLF bytes
   (6,120 bytes / 121 lines). The freeze's `restore-attempt-1-failed.walk.txt`
   is the older CRCRLF reformat produced during the task; it is kept unchanged
   and its hash is recorded here for history.
2. The first-spawn console excerpt is a contiguous host-local slice of
   `labs/rom20-exp/logs/console.log` (lines 845-856: Romuser join, fall, retry),
   written byte-exact. The full console log is host-local and is NOT part of
   the 100-file freeze; its path/hash are recorded so the excerpt can be
   checked without touching the freeze.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-20260926T063100Z")
FREEZE = ROOT / "operator" / "post-task-freeze"
DERIVED = ROOT / "operator" / "post-task-derived"
OUT = DERIVED / "failures"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    # ---- 1. failed-restore tool result, byte exact -------------------------
    session_path = FREEZE / "agent-session.jsonl"
    raw_lines = session_path.read_bytes().split(b"\n")
    row = raw_lines[168]  # 1-indexed line 169
    record = json.loads(row.decode("utf-8"))
    message = record.get("message") or {}
    assert message.get("role") == "toolResult", message.get("role")
    assert message.get("toolCallId") == "call_00_MR03DuPnyR02Opv6Y1bu4504", message.get("toolCallId")
    text = "".join(
        part.get("text", "")
        for part in (message.get("content") or [])
        if isinstance(part, dict) and part.get("type") == "text"
    )
    exact = text.encode("utf-8")  # preserves \r\n exactly; no newline translation
    target = OUT / "restore-attempt-1-failed.session-result.txt"
    target.write_bytes(exact)

    walk = FREEZE / "workspace-as-agent-left" / "evidence" / "restore-attempt-1-failed.walk.txt"

    # ---- 2. host-local console excerpt, byte exact -------------------------
    console = ROOT.parent.parent / "labs" / "rom20-exp" / "logs" / "console.log"
    console = Path("F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-exp/logs/console.log")
    console_bytes = console.read_bytes()
    console_lines = console_bytes.split(b"\n")
    first, last = 845, 856  # 1-indexed inclusive
    excerpt = b"\n".join(console_lines[first - 1:last]) + b"\n"
    excerpt_target = OUT / "first-spawn-romuser-console-excerpt.txt"
    excerpt_target.write_bytes(excerpt)

    facts = {
        "format": "mc-agent/rom20-failure-evidence@1",
        "failed_restore_tool_result": {
            "source": str(session_path),
            "source_sha256": hashlib.sha256(session_path.read_bytes()).hexdigest(),
            "session_line": 169,
            "tool_call_id": message.get("toolCallId"),
            "file": str(target),
            "bytes": len(exact),
            "lines": exact.count(b"\n"),
            "sha256": sha256_bytes(exact),
            "note": "byte-exact copy of the frozen session tool result; CRLF preserved",
        },
        "failed_restore_walk_history": {
            "file": str(walk),
            "bytes": walk.stat().st_size,
            "lines": walk.read_bytes().count(b"\n"),
            "sha256": sha256_bytes(walk.read_bytes()),
            "note": "the task-time reformatted copy in the freeze; kept unchanged, not committed",
        },
        "first_spawn_console_excerpt": {
            "host_local_source": str(console),
            "host_local_source_sha256": sha256_bytes(console_bytes),
            "host_local_source_bytes": len(console_bytes),
            "frozen": False,
            "line_range_1_indexed": [first, last],
            "file": str(excerpt_target),
            "bytes": len(excerpt),
            "lines": excerpt.count(b"\n"),
            "sha256": sha256_bytes(excerpt),
            "note": "host-local console excerpt, not part of the 100-file freeze; full source path/hash above",
        },
    }
    (OUT / "failure-evidence.json").write_text(
        json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(facts, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
