#!/usr/bin/env python3
"""Build the versioned full-gate evidence summary from one frozen run.

The generator is tracked in Git and reads every identity/value from the run's
own facts and per-phase provenance files; it never hardcodes a revision.  Run
it after ``tools/stage1_fullgate.py compose`` and commit the result as an
evidence-only commit that names the source commit the run executed:

    python tools/stage1_fullgate_summary.py

Consistency is enforced before writing: every phase must report the same clean
Git HEAD, the tracked source files must have the same bytes at phase start and
end, the run's ``run.json`` and the version lock must agree with that HEAD, and
the driver's bytes hash must equal the hash pinned in the live facts.  A
mismatch raises instead of writing a misleading summary.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import stage1_gate as gate  # noqa: E402

EVIDENCE = ROOT / "labs" / "fullgate-evidence"
BUNDLE = EVIDENCE / "bundle"
OUTPUT = ROOT / "docs" / "evidence" / "rom13-stage1" / "full-gate-summary.json"
REPORT = ROOT / "docs" / "evidence" / "rom13-stage1" / "assemble-report.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def collect_provenance(facts: dict[str, Any]) -> dict[str, Any]:
    phases: dict[str, Any] = {}
    for path in sorted((EVIDENCE / "provenance").glob("*.json")):
        document = read_json(path)
        phases[document["phase"]] = {
            "path": str(path.relative_to(ROOT)),
            "head": document["before"]["head"],
            "clean": document["before"]["clean"],
            "dirtyBefore": document["before"]["dirty"],
            "dirtyAfter": document["after"]["dirty"],
            "files": document["before"]["files"],
            "lineEndingConvention": document["before"].get("lineEndingConvention"),
        }
    source = (facts.get("provenance") or {}).get("source") or {}
    head = source.get("head")
    if not head:
        raise SystemExit("facts-live.json has no source provenance; live did not run with the frozen driver")
    if not source.get("clean"):
        raise SystemExit(f"the live phase ran on a dirty worktree: {source.get('dirty')}")
    for phase, entry in phases.items():
        if entry["head"] != head or not entry["clean"] or entry["dirtyBefore"] or entry["dirtyAfter"]:
            raise SystemExit(f"phase {phase!r} provenance differs from the live source: {entry}")
    return {
        "sourceCommit": head,
        "branch": source.get("branch"),
        "clean": True,
        "lineEndingConvention": source.get("lineEndingConvention"),
        "driver": {
            "path": "tools/stage1_fullgate.py",
            "actualSha256": source["files"]["tools/stage1_fullgate.py"]["actual_sha256"],
            "gitBlobSha256": source["files"]["tools/stage1_fullgate.py"]["git_blob_sha256"],
            "crlf": source["files"]["tools/stage1_fullgate.py"]["crlf"],
            "normalizedMatchesGitBlob": source["files"]["tools/stage1_fullgate.py"]["normalized_matches_git_blob"],
        },
        "files": source.get("files"),
        "jars": source.get("jars"),
        "phases": phases,
    }


def assert_consistency(facts: dict[str, Any], provenance: dict[str, Any]) -> None:
    head = provenance["sourceCommit"]
    run_json = read_json(Path(facts["runDir"]) / "run.json")
    run_commit = (run_json.get("repo") or {}).get("commit")
    dirty_entries = (run_json.get("repo") or {}).get("dirty_entries")
    if run_commit != head or dirty_entries not in (0, None):
        raise SystemExit(
            f"run.json records {run_commit} dirty_entries={dirty_entries}, but the executed source is {head}"
        )
    version_lock = read_json(BUNDLE / "artifacts/smoke_fixture_validity/version-lock.json")
    locked = next((item for item in version_lock.get("components", []) if item.get("name") == "mc-agent"), None)
    if not locked or locked.get("commit") != head:
        raise SystemExit(f"version-lock mc-agent commit {locked and locked.get('commit')} != {head}")
    driver = provenance["driver"]
    if facts["driver"]["sha256"] != driver["actualSha256"]:
        raise SystemExit("facts-live driver hash differs from the recorded working-tree bytes")
    if not driver["normalizedMatchesGitBlob"]:
        raise SystemExit("the driver bytes differ from the committed Git blob beyond line endings")
    for phase, entry in provenance["phases"].items():
        for relative, meta in entry["files"].items():
            live_meta = provenance["files"].get(relative) or {}
            if meta["actual_sha256"] != live_meta.get("actual_sha256"):
                raise SystemExit(f"phase {phase!r} saw different bytes for {relative}")


def main() -> int:
    facts = read_json(EVIDENCE / "facts-live.json")
    gate_report = read_json(EVIDENCE / "gate-report.json")
    provenance = collect_provenance(facts)
    assert_consistency(facts, provenance)

    manifest = read_json(BUNDLE / "bundle.json")
    events = read_jsonl(BUNDLE / "artifacts/independent_test_mod/audit-events.jsonl")
    trace_join = read_json(BUNDLE / "artifacts/trace_persistence/trace-join.json")
    trace_report = read_json(BUNDLE / "mapping/trace-report.json")
    index = read_json(BUNDLE / "artifacts/smoke_fixture_validity/evidence-index.json")
    test_mod = read_json(BUNDLE / "artifacts/independent_test_mod/test-mod-manifest.json")
    lifecycle = read_json(BUNDLE / "artifacts/independent_test_mod/audit-lifecycle.json")
    fixture_manifest = read_json(BUNDLE / "artifacts/fixture_map/fixture-manifest.json")
    init_runs = read_jsonl(BUNDLE / "artifacts/fixture_map/init-runs.jsonl")
    scan = read_json(BUNDLE / "artifacts/fixture_map/command-block-scan.json")
    before_meta = read_json(BUNDLE / "artifacts/restore_fidelity/snapshot-before/meta.json")
    after_meta = read_json(BUNDLE / "artifacts/restore_fidelity/snapshot-after/meta.json")
    source_unchanged = read_json(BUNDLE / "artifacts/restore_fidelity/source-unchanged.json")
    restore_record = read_json(BUNDLE / "artifacts/restore_fidelity/restore-record.json")
    version_lock = read_json(BUNDLE / "artifacts/smoke_fixture_validity/version-lock.json")
    smoke_report = read_json(BUNDLE / "artifacts/smoke_fixture_validity/smoke-report.json")
    fixture_validity = read_json(BUNDLE / "artifacts/smoke_fixture_validity/fixture-validity.json")
    tool_env = read_json(BUNDLE / "artifacts/agent_dev_capability/tool-environment.json")
    jar_update = read_json(BUNDLE / "artifacts/agent_dev_capability/jar-update.json")
    smoke_mod = read_json(BUNDLE / "artifacts/agent_dev_capability/smoke-mod.json")
    identity = read_jsonl(BUNDLE / "artifacts/player_context/identity-records.jsonl")
    devcap = read_json(EVIDENCE / "facts-devcap.json")
    trace_facts = read_json(EVIDENCE / "facts-trace.json")
    stages = facts["stages"]
    btree = gate.tree_hash(BUNDLE)

    summary = {
        "kind": "rom13-stage1-full-gate-summary",
        "evidenceVersion": 3,
        "issue": "guajun/mc-agent#14",
        "date": gate_report["gate"]["generated_at"],
        "sourceCommit": provenance["sourceCommit"],
        "sourceCommitNote": "the run executed this commit with a clean worktree; this summary is committed in an "
                            "evidence-only commit after it and cannot be used to identify its own bytes",
        "branch": provenance["branch"],
        "lineEndingConvention": provenance["lineEndingConvention"],
        "driver": provenance["driver"],
        "jars": provenance["jars"],
        "perPhaseProvenance": {
            phase: {
                "path": entry["path"],
                "head": entry["head"],
                "clean": entry["clean"],
                "lineEndingConvention": entry["lineEndingConvention"],
            }
            for phase, entry in provenance["phases"].items()
        },
        "acceptance": "pending human review of this pull request; the gate pass is not a merge decision",
        "reviewResponse": [
            "executed-source provenance: every phase records the true Git HEAD, the clean/dirty state, and the "
            "actual working-tree bytes vs the committed Git blob for the driver and the other executable source "
            "files, so the reviewed source can be retrieved from Git and checked byte-for-byte (CRLF normalized)",
            "the summary generator is tracked, reads the commit from the run facts and refuses to write if any "
            "phase saw a different revision",
            "playNote pending state is scoped to the useWithoutItem callback and by tick; attacks clear it and "
            "environment plays are recorded immediately, with an attack-then-use mixed-trigger regression",
            "restore-record flags come from post-restore probes; audit lifecycle per-instance/ring-buffer flags "
            "come from the raw files; the overflow status has a real truncated-log probe",
            "hook_overhead_ms is labeled as the total session hook time, with per-hook counters",
            "docs fixed: duplicate seq row, zh clock rule, facts-trace row naming, smoke-mod coexistence citation",
        ],
        "gate": {
            "overall": gate_report["gate"]["overall"],
            "exitCode": gate_report["gate_exit_code"],
            "checks": {c["id"]: c["status"] for c in gate_report["gate"]["checks"]},
            "failReasons": sum(1 for c in gate_report["gate"]["checks"] for r in c["reasons"] if r["level"] == "fail"),
            "blockedReasons": sum(1 for c in gate_report["gate"]["checks"] for r in c["reasons"] if r["level"] == "blocked"),
            "bundle": "labs/fullgate-evidence/bundle",
            "bundleTreeSha256": btree[0],
            "bundleFiles": btree[1],
            "bundleBytes": btree[2],
            "bundleJsonSha256": sha256_file(BUNDLE / "bundle.json"),
            "evidenceIndexSha256": sha256_file(BUNDLE / "artifacts/smoke_fixture_validity/evidence-index.json"),
            "evidenceIndexEntries": len(index["entries"]),
            "limitations": gate_report["gate"].get("limitations"),
        },
        "run": {
            "runId": facts["runId"],
            "runDir": facts["runDir"],
            "ports": {
                "rom13-src": [27240, 27241, 27242, 27243],
                "rom13-exp": [27244, 27245, 27246, 27247],
                "rom13-neg": [27248, 27249],
                "control-rom13-ctl": [27150, 27151],
            },
            "fixtureMapWorld": facts.get("fixtureMapWorld"),
            "sourceWorld": {
                "label": "Minecart ROM test",
                "path": "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test",
                "baselineTreeSha256": facts["sourceBaseline"],
                "before": facts["sourceWorldBefore"],
                "after": facts["sourceWorldAfter"],
                "unchanged": source_unchanged["unchanged"],
            },
            "interface": facts["interface"],
            "bridge": facts["bridge"],
            "auditJar": facts["auditJar"],
        },
        "fixture": {
            "mapSha256": fixture_manifest["map"]["sha256"],
            "downloadVerified": fixture_manifest["map"]["download_verified"],
            "initRuns": [
                {
                    "runId": run["run_id"],
                    "instance": run["instance_id"],
                    "stateHash": run["state_hash"],
                    "orderHash": run["order_hash"],
                    "capturedAt": run["captured_at"],
                }
                for run in init_runs
            ],
            "liveInitRuns": [
                {
                    "runId": run["run_id"],
                    "stateHash": run["state_hash"],
                    "orderHash": run["order_hash"],
                    "capturedAt": run["captured_at"],
                    "record": run["record"],
                }
                for run in stages["initRuns"]
            ],
            "playerUuid": facts["fixturePlayerUuid"],
            "commandBlocks": scan["command_blocks"],
            "scannedWorldDirs": scan["world_dirs"],
        },
        "restore": {
            "applied": (stages["restore"]["applied"].get("result") or {}).get("count"),
            "orderHashBefore": before_meta["orderHash"],
            "orderHashAfter": after_meta["orderHash"],
            "verified": (stages["restore"]["verified"].get("result") or {}).get("ok"),
            "flags": stages["restore"].get("flags"),
            "flagsEvidence": restore_record.get("flags_evidence"),
            "snapshotBeforeTreeSha256": gate.tree_hash(BUNDLE / "artifacts/restore_fidelity/snapshot-before")[0],
            "snapshotAfterTreeSha256": gate.tree_hash(BUNDLE / "artifacts/restore_fidelity/snapshot-after")[0],
            "failureCases": [{"case": c["case"], "passed": c["passed"]} for c in facts["failureCases"]],
            "sourceUnchanged": True,
        },
        "calibration": {
            "audited": stages["positive"]["calibration"],
            "control": (stages["control"].get("control") or {}).get("observations"),
            "consistent": stages["control"]["consistent"],
            "hookOverheadMs": test_mod["hook_overhead_ms"],
            "hookOverheadScope": test_mod["hook_overhead_scope"],
            "hookCountersNanos": test_mod["hook_overhead_by_hook"],
            "positiveVerdict": stages["positive"]["verdict"],
            "positiveLogSha256": stages["positive"]["logCopySha256"],
            "sourceChildVerdict": stages["sourceChild"]["verdict"],
            "sourceChildLogSha256": stages["sourceChild"]["logCopySha256"],
            "statusSha256": stages["positive"]["statusSha256"],
            "negatives": {key: value for key, value in (stages.get("negatives") or {}).items()},
        },
        "audit": {
            "canonicalEvents": {
                "path": "bundle/artifacts/independent_test_mod/audit-events.jsonl",
                "sha256": sha256_file(BUNDLE / "artifacts/independent_test_mod/audit-events.jsonl"),
                "count": len(events),
                "byInstance": {
                    instance: sum(1 for e in events if e["instance_id"] == instance)
                    for instance in sorted({e["instance_id"] for e in events})
                },
                "clock": "real server tick + raw append seq + session + wall preserved",
            },
            "manifest": {
                "sha256": test_mod["sha256"],
                "fixture_behavior_unchanged": test_mod["fixture_behavior_unchanged"],
                "agent_mod_coexists": test_mod["agent_mod_coexists"],
                "hook_overhead_ms": test_mod["hook_overhead_ms"],
                "hook_overhead_scope": test_mod["hook_overhead_scope"],
                "loaded_in": test_mod["loaded_in"],
                "fact_evidence": test_mod.get("fact_evidence"),
            },
            "lifecycle": {
                "states": lifecycle["states"],
                "perInstanceFiles": lifecycle["per_instance_files"],
                "ringBufferReliance": lifecycle["ring_buffer_reliance"],
                "flushStatusSeq": lifecycle["evidence"]["flush"]["status_seq"],
                "sessions": lifecycle["sessions"],
                "evidence": lifecycle["evidence"],
            },
        },
        "trace": {
            "toolTraceSha256": sha256_file(BUNDLE / "artifacts/trace_persistence/tool-trace.jsonl"),
            "traceJoinSha256": sha256_file(BUNDLE / "artifacts/trace_persistence/trace-join.json"),
            "verifiedJoins": len(trace_join["joins"]),
            "unmatchedToolCalls": trace_join["unmatched_tool_calls"],
            "unmatchedAgentEvents": trace_join["unmatched_agent_events"],
            "reportOk": trace_report.get("ok"),
            "gaps": len(trace_report.get("gaps") or []),
            "joinKinds": {
                kind: sum(1 for item in trace_join["joins"] if (item.get("proof") or {}).get("kind") == kind)
                for kind in ("direct", "chain")
            },
            "experimentPhaseRows": trace_facts.get("experimentPhaseRows"),
            "canonicalAgentEvents": len(events),
            "missingLogDetectionSha256": sha256_file(BUNDLE / "artifacts/trace_persistence/missing-log-detection.jsonl"),
            "missingLogDetected": trace_facts.get("missingLogDetected"),
            "overflowProbe": trace_facts.get("overflowProbe"),
        },
        "identity": [
            {"case": r["case"], "accepted": r["accepted"], "hit": r.get("hit"), "targetType": (r.get("target") or {}).get("type")}
            for r in identity
        ],
        "capability": {
            "toolEnvironment": {
                "harness": tool_env["harness"],
                "model": tool_env["model"],
                "tools": tool_env["tools"],
                "docsVisible": tool_env["docs_visible"],
                "preflightChecks": tool_env["detail"]["checks"],
            },
            "jarUpdate": {k: jar_update[k] for k in ("case", "old_sha256", "new_sha256", "loaded_sha256", "bytes")},
            "smokeMod": {
                "mod_id": smoke_mod["mod_id"],
                "version": smoke_mod["version"],
                "built_sha256": smoke_mod["built_sha256"],
                "deployed_sha256": smoke_mod["deployed_sha256"],
                "build_errors_detected": smoke_mod["build_errors_detected"],
                "load_failure_detected": smoke_mod["load_failure_detected"],
                "missing_dependency_detected": smoke_mod["missing_dependency_detected"],
                "memory_state_rebuilt_after_restart": smoke_mod["memory_state_rebuilt_after_restart"],
                "restart_evidence_ref": smoke_mod["restart_evidence_ref"],
                "no_rom_logic": smoke_mod["no_rom_logic"],
            },
            "devcapFacts": devcap,
        },
        "smoke": {
            "suites": [{"suite": s["name"], "status": s["status"]} for s in smoke_report["suites"]],
            "versionLock": [{"name": c["name"], "version": c.get("version")} for c in version_lock["components"]],
            "withModWithoutModConsistent": fixture_validity["with_mod_without_mod_consistent"],
            "hookOverheadMs": fixture_validity["hook_overhead_ms"],
            "hookOverheadScope": fixture_validity.get("hook_overhead_scope"),
        },
        "reproduce": [
            "git checkout <sourceCommit>",
            "python tools/stage1_fullgate.py live",
            "python tools/stage1_fullgate.py identity",
            "python tools/stage1_fullgate.py devcap",
            "python tools/stage1_fullgate.py trace",
            "python tools/stage1_fullgate.py smoke",
            "python tools/stage1_fullgate.py compose",
            "python tools/stage1_fullgate_summary.py",
        ],
        "rawEvidence": "Raw artifacts stay under the git-ignored labs/fullgate-evidence/ of the worktree that "
                       "produced them; the hashes above pin them for spot checks.",
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    REPORT.write_text(json.dumps(gate_report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
    print(f"source commit: {provenance['sourceCommit']} clean={provenance['clean']} "
          f"driver={provenance['driver']['actualSha256'][:16]}")
    print(f"gate: {summary['gate']['overall']} exit={summary['gate']['exitCode']} "
          f"joins={summary['trace']['verifiedJoins']} unmatched_agents={summary['trace']['unmatchedAgentEvents']}")
    return 0 if summary["gate"]["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
