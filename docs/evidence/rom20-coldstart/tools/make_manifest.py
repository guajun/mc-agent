#!/usr/bin/env python3
"""Build the packaging manifest for the derived ROM20 evidence set.

Verifies the frozen snapshot's own manifest, then records sha256/bytes for the
raw sources, the derived products and the reproducible build check. Model
configuration, attempt counts and the execution commit are copied from the
frozen run.json / prep records.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-20260926T063100Z")
FREEZE = ROOT / "operator" / "post-task-freeze"
DERIVED = ROOT / "operator" / "post-task-derived"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def entry(path: Path, role: str) -> dict:
    return {"path": str(path), "role": role, "bytes": path.stat().st_size, "sha256": sha256(path)}


def main() -> int:
    freeze_manifest = json.loads((FREEZE / "manifest.json").read_text(encoding="utf-8"))
    assert len(freeze_manifest["files"]) == 100
    for item in freeze_manifest["files"]:
        path = FREEZE / item["path"]
        assert path.is_file(), item["path"]
        assert sha256(path) == item["sha256"], item["path"]

    run = json.loads((FREEZE / "run-as-agent-left" / "run.json").read_text(encoding="utf-8"))
    derivation = json.loads((DERIVED / "run" / "trajectory-derivation.json").read_text(encoding="utf-8"))

    raw_sources = [
        entry(FREEZE / "agent-session.jsonl", "frozen pi session (282 records, 157 tool calls)"),
        entry(FREEZE / "agent-final.txt", "agent final answer text"),
        entry(FREEZE / "testmod" / "audit-rom20-20260926T063100Z.jsonl", "raw independent test-mod audit log"),
        entry(FREEZE / "testmod" / "config.json", "raw audit mod config"),
        entry(FREEZE / "testmod" / "status-rom20-20260926T063100Z.json", "raw audit status"),
        entry(FREEZE / "agent-logger-raw" / "romlog.jsonl", "raw agent logger output"),
        entry(FREEZE / "run-as-agent-left" / "run.json", "original run manifest"),
        entry(FREEZE / "run-as-agent-left" / "evidence.json", "original evidence declarations"),
        entry(FREEZE / "run-as-agent-left" / "trajectory.jsonl", "original 30-record trajectory"),
        entry(FREEZE / "run-as-agent-left" / "visibility.json", "visibility snapshot"),
        entry(FREEZE / "workspace-as-agent-left" / "artifacts" / "rom20-agent-logger.jar", "deployed agent logger jar"),
        entry(FREEZE / "workspace-as-agent-left" / "artifacts" / "rom20-agent-logger.jar.build.json", "raw build sidecar"),
        entry(FREEZE / "workspace-as-agent-left" / "agent-logger" / "src/main/java/dev/mcagent/romlog/RomLog.java", "logger source"),
        entry(FREEZE / "workspace-as-agent-left" / "agent-logger" / "src/main/java/dev/mcagent/romlog/RomLogMod.java", "logger source"),
        entry(FREEZE / "workspace-as-agent-left" / "agent-logger" / "src/main/java/dev/mcagent/romlog/mixin/MinecartContainerMixin.java", "logger source"),
        entry(FREEZE / "workspace-as-agent-left" / "agent-logger" / "src/main/resources/fabric.mod.json", "logger resource"),
        entry(FREEZE / "workspace-as-agent-left" / "agent-logger" / "src/main/resources/romlog.mixins.json", "logger resource"),
        entry(FREEZE / "workspace-as-agent-left" / "evidence" / "answer.json", "original answer"),
        entry(FREEZE / "workspace-as-agent-left" / "evidence" / "oracle.json", "original oracle"),
        entry(FREEZE / "workspace-as-agent-left" / "evidence" / "testmod.experiment.jsonl", "original (pre-fix) projection"),
        entry(FREEZE / "workspace-as-agent-left" / "evidence" / "romlog.experiment.norm.jsonl", "original normalized logger view"),
        entry(FREEZE / "workspace-as-agent-left" / "evidence" / "restore-attempt-1-failed.json", "failed restore attempt record"),
    ]
    derived_products = [
        entry(DERIVED / "run" / "trajectory.jsonl", f"canonical chronological trajectory ({derivation['merge']['canonical_records']} records)"),
        entry(DERIVED / "run" / "trajectory-derivation.json", "derivation manifest (raw hashes, mapping, validation, import max-text)"),
        entry(DERIVED / "run" / "reviews.jsonl", "independent review resolutions (3 required semantic reviews)"),
        entry(DERIVED / "run" / "audit" / "audit.json", "cold-start run audit after review resolutions (5 PASS)"),
        entry(DERIVED / "run" / "audit" / "audit.md", "cold-start run audit report after review resolutions"),
        entry(DERIVED / "run" / "audit" / "audit-task-time-pending.json", "task-time audit report preserved (PENDING)"),
        entry(DERIVED / "run" / "audit" / "audit-task-time-pending.md", "task-time audit report preserved (PENDING)"),
        entry(DERIVED / "evidence" / "answer.json", "answer (regenerated, identical hash to original)"),
        entry(DERIVED / "evidence" / "oracle.json", "independent oracle (regenerated, identical hash to original)"),
        entry(DERIVED / "evidence" / "testmod.experiment.jsonl", "fixed canonical projection (raw session order, phase provenance retained)"),
        entry(DERIVED / "evidence" / "romlog.experiment.norm.jsonl", "normalized agent logger view"),
        entry(DERIVED / "evidence" / "restore-evidence.json", "restore verification summary"),
        entry(DERIVED / "evidence" / "minecart-audit-check.json", "independent test-mod verifier result"),
        entry(DERIVED / "build-check" / "rom20-agent-logger.jar", "reproducible rebuild of the deployed logger"),
        entry(DERIVED / "failures" / "restore-attempt-1-failed.session-result.txt", "byte-exact failed-restore tool result from the frozen session (line 169)"),
        entry(DERIVED / "failures" / "first-spawn-romuser-console-excerpt.txt", "host-local console excerpt for the first-spawn failure (not frozen)"),
        entry(DERIVED / "failures" / "failure-evidence.json", "failure evidence provenance and hashes"),
    ]

    failure_facts = json.loads((DERIVED / "failures" / "failure-evidence.json").read_text(encoding="utf-8"))
    host_local_sources = [
        {
            "path": failure_facts["first_spawn_console_excerpt"]["host_local_source"],
            "role": "host-local game console log (not part of the frozen archive)",
            "bytes": failure_facts["first_spawn_console_excerpt"]["host_local_source_bytes"],
            "sha256": failure_facts["first_spawn_console_excerpt"]["host_local_source_sha256"],
        }
    ]

    manifest = {
        "format": "mc-agent/rom20-coldstart-manifest@1",
        "run_id": run.get("run_id"),
        "created_at": run.get("created_at"),
        "execution": {
            "repo_commit": (run.get("repo") or {}).get("commit"),
            "branch": (run.get("repo") or {}).get("branch"),
            "dirty_entries": (run.get("repo") or {}).get("dirty_entries"),
            "harness": run.get("harness"),
            "thinking": "max",
            "task_sha256": (run.get("task") or {}).get("sha256"),
            "player_uuid": (run.get("task") or {}).get("player_uuid"),
            "note": (
                "no commit after 1deb735 was part of the executed run; later commits are packaging "
                "(this evidence package) and preparation-recipe fixes/tests (eb2bedf, 666d740, ...)"
            ),
        },
        "attempts": {
            "operator_preparation_attempts": 6,
            "task_sessions": 1,
            "in_session_failures": [
                "restore attempt 1: 6/6 entities lost to stale copied region data (preserved)",
                "Romuser spawn attempt 1: fell into the void before the hover seat could be mounted (preserved)",
            ],
        },
        "counts": {
            "session_records": derivation["sources"]["raw_session"]["records"],
            "session_tool_calls": len(
                json.loads((FREEZE / "tool-calls-index.json").read_text(encoding="utf-8"))
            ),
            "audit_events": len((FREEZE / "testmod" / "audit-rom20-20260926T063100Z.jsonl").read_text(encoding="utf-8").splitlines()),
            "derived_trajectory_records": derivation["merge"]["canonical_records"],
            "manual_records": derivation["merge"]["original_records"],
            "imported_records": derivation["merge"]["imported_records"],
            "truncated_imported_results": derivation["import"]["truncated_result_count"],
        },
        "external_review": {
            "url": "https://github.com/guajun/mc-agent/pull/32#pullrequestreview-5325010883",
            "review_id": 5325010883,
            "reviewed_head": "666d74004d2b41869fafbc83e05392cad7a150d9",
            "submitted_at": "2026-09-26T07:03:53Z",
            "reviewer": "guajun (independent pi reviewer)",
            "verdict": "executed run 1deb735 independently proven successful; three semantic reviews justified",
            "resolved_reviews": [
                "machine_operated.causality",
                "logger_armed_before_activation.running",
                "answer_correct.oracle_independence",
            ],
            "audit_after_resolution": "5/5 PASS",
        },
        "freeze_manifest_sha256": sha256(FREEZE / "manifest.json"),
        "raw_sources": raw_sources,
        "derived_products": derived_products,
        "host_local_sources": host_local_sources,
    }
    (DERIVED / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({
        "freeze_verified": True,
        "raw_sources": len(raw_sources),
        "derived_products": len(derived_products),
        "execution_commit": manifest["execution"]["repo_commit"],
        "counts": manifest["counts"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
