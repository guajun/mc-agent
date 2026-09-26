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
        entry(DERIVED / "run" / "trajectory.jsonl", "canonical chronological trajectory (344 records)"),
        entry(DERIVED / "run" / "trajectory-derivation.json", "derivation manifest (raw hashes, mapping, validation)"),
        entry(DERIVED / "evidence" / "answer.json", "answer (regenerated, identical hash to original)"),
        entry(DERIVED / "evidence" / "oracle.json", "independent oracle (regenerated, identical hash to original)"),
        entry(DERIVED / "evidence" / "testmod.experiment.jsonl", "fixed canonical projection (raw session order)"),
        entry(DERIVED / "evidence" / "romlog.experiment.norm.jsonl", "normalized agent logger view"),
        entry(DERIVED / "evidence" / "restore-evidence.json", "restore verification summary"),
        entry(DERIVED / "evidence" / "minecart-audit-check.json", "independent test-mod verifier result"),
        entry(DERIVED / "run" / "audit" / "audit.json", "cold-start run audit (canonical trajectory)"),
        entry(DERIVED / "run" / "audit" / "audit.md", "cold-start run audit report"),
        entry(DERIVED / "build-check" / "rom20-agent-logger.jar", "reproducible rebuild of the deployed logger"),
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
            "note": "packaging commits happen after this commit; they are not part of the executed run",
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
        },
        "freeze_manifest_sha256": sha256(FREEZE / "manifest.json"),
        "raw_sources": raw_sources,
        "derived_products": derived_products,
    }
    (DERIVED / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
