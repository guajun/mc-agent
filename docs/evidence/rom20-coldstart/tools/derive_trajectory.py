#!/usr/bin/env python3
"""Derive the canonical chronological trajectory for the ROM20 cold start.

Input: the derived run's trajectory.jsonl after `run_trace.py import-pi` added
the full frozen pi session (157 calls + 157 results) to the original 30-record
operator/manual trajectory.

Output: `trajectory.jsonl` (canonical: every non-header record stably sorted by
its actual `at` timestamp, `seq` renumbered 1..N), `trajectory.canonical.jsonl`
(a copy), and `trajectory-derivation.json` (raw source hashes, merge strategy,
validation, manual-call -> raw-pi-call mapping).

Raw timestamps are never changed; imported records keep `source: import:pi` and
manually recorded calls keep their own identity plus an added
`trajectory_origin` label and `raw_pi_call_ids` link.

Execution-call mapping: each manual call is matched to the imported bash call
whose command really executed the action. `run_trace.py` recorder commands are
excluded, except for `c-read-romlog-1`, whose single shell command contained
both the recorder and the logger read. Related observations (the failed first
dry-run/apply attempts and the rebuild that produced the deployed jar) are
listed explicitly instead of being hidden.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ORIGINAL_ORIGINS = {
    "operator": "original-run-operator",
    "agent": "original-run-agent-manual",
}

# manual call id -> markers that identify the real execution command
EXECUTION_MARKERS = {
    "c-build-logger-1": ("build_mod.py", "rom20-agent-logger.jar", "--out"),
    "c-fork-src-1": ("call fork", "rom20-acquire"),
    "c-restore-dry-1": ("call restore", '"dry_run":true'),
    "c-restore-apply-1": ("call restore", '"dry_run":false'),
    "c-place-user-1": ("scripts/place_user.py",),
    "c-check-acquired-1": ("scripts/check_acquired.py",),
    "c-operate-1": ("scripts/operate.py",),
    "c-read-romlog-1": ("scripts/read_romlog.py", "--file"),
}

# related raw observations worth naming in the derivation manifest
RELATED_MARKERS = {
    "first_dry_run_attempt_failed_escape": ("call restore", "\\rom20-src"),
    "first_apply_attempt_failed_chunks": ("call restore", '"dry_run":false', "not json:"),
    "deployed_jar_rebuild": ("build_mod.py", "rom20-agent-logger.jar", "--version 0.1.0"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def command_of(call: dict) -> str:
    arguments = call.get("arguments")
    if isinstance(arguments, dict):
        return str(arguments.get("command") or "")
    return str(arguments or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--raw-session", required=True, type=Path)
    parser.add_argument("--original-trajectory", required=True, type=Path)
    parser.add_argument("--out-derivation", required=True, type=Path)
    args = parser.parse_args()

    run_dir: Path = args.run_dir
    trajectory_path = run_dir / "trajectory.jsonl"
    pre_merge_sha = sha256(trajectory_path)
    records = read_jsonl(trajectory_path)
    header = records[0]
    assert header.get("record") == "header", "first record is not the header"
    body = records[1:]

    original_lines = [line for line in args.original_trajectory.read_text(encoding="utf-8").splitlines() if line.strip()]
    original_records = [json.loads(line) for line in original_lines]
    assert original_records[0].get("record") == "header"
    original_body = original_records[1:]
    first_body_path = trajectory_path.read_text(encoding="utf-8").splitlines()[: len(original_lines)]
    assert first_body_path == original_lines, "the importer changed original trajectory lines"

    imported = [record for record in body if record.get("source") == "import:pi"]
    original = [record for record in body if record.get("source") != "import:pi"]
    imported_calls = [record for record in imported if record.get("record") == "call"]
    imported_results = [record for record in imported if record.get("record") == "result"]
    assert len(imported_calls) == 157 and len(imported_results) == 157, (
        len(imported_calls),
        len(imported_results),
    )
    imported_bash = [call for call in imported_calls if call.get("tool") == "bash"]

    def is_recorder_only(command: str, call_id: str) -> bool:
        if "run_trace.py" not in command:
            return False
        # c-read-romlog-1's command did both the recording and the read
        return call_id != "c-read-romlog-1"

    manual_links: dict[str, dict] = {}
    for call in original:
        if call.get("record") != "call":
            continue
        call_id = str(call.get("call_id"))
        markers = EXECUTION_MARKERS.get(call_id)
        if not markers:
            continue
        manual_at = parse_at(str(call.get("at")))
        candidates = []
        for candidate in imported_bash:
            command = command_of(candidate)
            if all(marker in command for marker in markers) and not is_recorder_only(command, call_id):
                candidates.append(
                    {
                        "pi_call_id": str(candidate.get("call_id")),
                        "at": candidate.get("at"),
                        "command": command,
                        "delta_seconds": round((parse_at(str(candidate.get("at"))) - manual_at).total_seconds(), 3),
                    }
                )
        candidates.sort(key=lambda entry: abs(entry["delta_seconds"]))
        assert candidates, f"manual call {call_id} has no raw pi execution match"
        manual_links[call_id] = {
            "selected_pi_call_id": candidates[0]["pi_call_id"],
            "basis": "imported bash command contains " + ", ".join(repr(m) for m in markers),
            "candidates": candidates,
        }

    related: dict[str, list[dict]] = {}
    for name, markers in RELATED_MARKERS.items():
        related[name] = [
            {"pi_call_id": str(candidate.get("call_id")), "at": candidate.get("at"), "command": command_of(candidate)}
            for candidate in imported_bash
            if all(marker in command_of(candidate) for marker in markers)
            and not is_recorder_only(command_of(candidate), "")
        ]

    # annotate: keep every raw field, add provenance
    canonical: list[dict] = []
    for record in original:
        entry = dict(record)
        entry["trajectory_origin"] = ORIGINAL_ORIGINS.get(str(record.get("actor")), "original-run")
        call_id = str(record.get("call_id") or "")
        if call_id in manual_links:
            entry["raw_pi_call_ids"] = [
                candidate["pi_call_id"] for candidate in manual_links[call_id]["candidates"]
            ]
        canonical.append(entry)
    for record in imported:
        entry = dict(record)
        entry["trajectory_origin"] = "pi-session-import"
        canonical.append(entry)

    canonical.sort(key=lambda record: parse_at(str(record.get("at"))))

    merged = [dict(header)] + canonical
    for index, record in enumerate(merged, start=1):
        record["seq"] = index

    text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in merged)
    trajectory_path.write_text(text, encoding="utf-8", newline="\n")
    (run_dir / "trajectory.canonical.jsonl").write_text(text, encoding="utf-8", newline="\n")

    # ------------------------------------------------------------------ validation
    calls = {str(r.get("call_id")): r for r in merged if r.get("record") == "call"}
    results = {str(r.get("call_id")): r for r in merged if r.get("record") == "result"}
    problems: list[str] = []
    if set(calls) != set(results):
        problems.append("call ids without exactly one result")
    times = [parse_at(str(r.get("at"))) for r in merged[1:]]
    if times != sorted(times):
        problems.append("timestamps not monotonic")
    seqs = [r["seq"] for r in merged]
    if seqs != list(range(1, len(merged) + 1)):
        problems.append("seq not 1..N")
    if any(str(r.get("run_id")) != str(header.get("run_id")) for r in merged if r.get("run_id")):
        problems.append("run_id mismatch")
    if problems:
        print("VALIDATION FAILED: " + "; ".join(problems))
        return 1

    derivation = {
        "format": "mc-agent/rom20-trajectory-derivation@1",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "run_id": header.get("run_id"),
        "created_at_original": header.get("created_at"),
        "sources": {
            "raw_session": {
                "path": str(args.raw_session),
                "sha256": sha256(args.raw_session),
                "records": len(read_jsonl(args.raw_session)),
                "tool_calls": len(imported_calls),
            },
            "original_trajectory": {
                "path": str(args.original_trajectory),
                "sha256": sha256(args.original_trajectory),
                "records": len(original_records),
            },
            "pre_merge_trajectory": {
                "path": str(trajectory_path),
                "sha256": pre_merge_sha,
                "note": "original run trajectory after import-pi appended the raw session",
            },
        },
        "merge": {
            "strategy": (
                "stable sort of all non-header records by their actual `at` timestamp; "
                "seq renumbered 1..N; no timestamp, argument, status or result text changed"
            ),
            "original_records": len(original),
            "imported_records": len(imported),
            "canonical_records": len(merged),
            "fields_added": ["trajectory_origin", "raw_pi_call_ids"],
            "labels": {
                "original-run-operator": "operator prep records from the original run trajectory",
                "original-run-agent-manual": "key calls/results the agent appended manually with run_trace.py during the task",
                "pi-session-import": "raw harness tool calls/results imported from the frozen pi session",
            },
        },
        "manual_call_links": manual_links,
        "related_observations": related,
        "validation": {
            "call_result_pairs": set(calls) == set(results),
            "timestamps_monotonic": times == sorted(times),
            "seq_monotonic": seqs == list(range(1, len(merged) + 1)),
            "run_id_consistent": not any(
                str(r.get("run_id")) != str(header.get("run_id")) for r in merged if r.get("run_id")
            ),
        },
        "outputs": {
            "trajectory": "trajectory.jsonl",
            "trajectory_copy": "trajectory.canonical.jsonl",
            "sha256": sha256(trajectory_path),
        },
    }
    args.out_derivation.write_text(json.dumps(derivation, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "canonical_records": len(merged),
        "original_records": len(original),
        "imported_records": len(imported),
        "manual_calls_linked": len(manual_links),
        "validation": derivation["validation"],
        "output_sha256": derivation["outputs"]["sha256"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
