#!/usr/bin/env python3
"""Build the stage-one gate's independent_test_mod artifacts from live evidence.

This is the adapter between the raw audit JSONL (the primary evidence) and the
canonical schema that `tools/stage1_gate.py` consumes for issue #18. It then
assembles a *partial* live bundle and reports the ``independent_test_mod``
check status. It never claims a gate pass: the other prerequisites' artifacts
(#15 fixture, bridge#6 restore, #16 identity, #17 deployment, #19 trace) are
owned by their own work and stay missing.

    python tests/mods/minecart-audit/stage1_evidence.py

Inputs (all produced by the live drivers in this directory):

* ``labs/rom18-b6-evidence/summary.json`` - bridge/mod combined run, restored
  copy, player.view and smoke-mod coexistence;
* ``labs/rom18-evidence/summary.json`` - generic positive and the four live
  negative cases (no interaction, wrong position, marker only, answer only).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

MODULE = Path(__file__).resolve().parent
ROOT = MODULE.parents[2]
TOOLS = ROOT / "tools"
LABS = ROOT / "labs"
VERIFIER = TOOLS / "minecart_audit.py"
GATE = TOOLS / "stage1_gate.py"
SOURCE_WORLD = Path("D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test")
SOURCE_HASH = "8cd54c86af9fa8d6b9ea33441fb21dac295cd2b5ddaa60327f5fb3a30255324a"

NEGATIVE_KEYS = {
    "no_operation": "no_interaction",
    "wrong_position": "wrong_position",
    "marker_only": "marker_only",
    "answer_only": "answer_only",
    "attack_only": "attack_only",
}


def run(argv: list[object], check: bool = True, timeout: float = 600) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [str(item) for item in argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise SystemExit(
            f"command failed ({result.returncode}): {' '.join(str(item) for item in argv)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bridge-evidence", default=str(LABS / "rom18-b6-evidence"))
    parser.add_argument("--live-evidence", default=str(LABS / "rom18-evidence"))
    parser.add_argument("--out", default=str(LABS / "rom18-evidence" / "stage1-fragment"))
    parser.add_argument("--audit-jar", default=str(MODULE / "dist" / "mc-minecart-audit-0.1.0.jar"))
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()

    bridge_evidence = Path(args.bridge_evidence)
    live_evidence = Path(args.live_evidence)
    out = Path(args.out)
    bridge_summary = json.loads((bridge_evidence / "summary.json").read_text(encoding="utf-8"))
    live_summary = json.loads((live_evidence / "summary.json").read_text(encoding="utf-8"))
    audit_jar = Path(args.audit_jar)
    current_jar_sha = hashlib.sha256(audit_jar.read_bytes()).hexdigest()
    used_jar_sha = (bridge_summary.get("auditJar") or {}).get("sha256")
    if used_jar_sha and current_jar_sha != used_jar_sha:
        raise SystemExit(
            "audit jar bytes changed since the live bridge run "
            f"({current_jar_sha} != {used_jar_sha}); rebuild and re-run the drivers "
            "before exporting, or pass the jar the run actually used"
        )

    # The attestation flags must come from the verified live summaries, never
    # from constants: a regression that broke parity or coexistence must not
    # be exported as passed.
    fixture_behavior_unchanged = bool(live_summary.get("checks", {}).get("fixture_behavior_unchanged"))
    agent_mod_coexists = bool(bridge_summary.get("checks", {}).get("smoke_mod_coexists"))
    if not fixture_behavior_unchanged or not agent_mod_coexists:
        raise SystemExit(
            "refusing to export the manifest: live summaries report "
            f"fixture_behavior_unchanged={fixture_behavior_unchanged}, "
            f"smoke_mod_coexists={agent_mod_coexists}"
        )

    summary_flags = {
        "fixture_behavior_unchanged": fixture_behavior_unchanged,
        "smoke_mod_coexists": agent_mod_coexists,
    }
    src_run = bridge_summary["checks"]["source_run_id"]
    dst_run = bridge_summary["checks"]["experiment_run_id"]
    src_log = bridge_evidence / "rom18-b6-src" / f"audit-{src_run}.jsonl"
    dst_log = bridge_evidence / "rom18-b6-dst" / f"audit-{dst_run}.jsonl"
    for path in (src_log, dst_log):
        if not path.is_file():
            raise SystemExit(f"missing raw audit log: {path}")
    # The child run must be finalized too; the exporter refuses open sessions,
    # but fail here with a specific message first.
    src_check = run(
        [sys.executable, VERIFIER, "check", "--log", str(src_log), "--allow-no-operation", "--json"],
        check=False,
    )
    try:
        src_report = json.loads(src_check.stdout)
    except ValueError:
        raise SystemExit(f"source child run {src_run} is unreadable: {src_check.stdout}")
    if src_report.get("verdict") == "incomplete":
        raise SystemExit(f"source child run {src_run} is incomplete; close it with /mcaudit end")
    summary_flags["source_child_verdict"] = src_report.get("verdict")

    negative_specs = []
    negative_refs = {}
    for key, case in NEGATIVE_KEYS.items():
        scenario = live_summary.get("scenarios", {}).get(key)
        if scenario is None:
            raise SystemExit(
                f"live summary has no {key!r} scenario; re-run live_smoke.py before exporting"
            )
        report = scenario.get("verdict")
        if not isinstance(report, dict) or report.get("verdict") != "fail":
            raise SystemExit(f"live negative {key} is not a recorded failure: {scenario}")
        run_id = report["run"]
        log = live_evidence / "rom18-b" / f"audit-{run_id}.jsonl"
        if not log.is_file():
            raise SystemExit(f"missing negative log: {log}")
        negative_specs += ["--negative", f"{case}={log}"]
        negative_refs[case] = str(log)

    if out.exists() and any(out.iterdir()):
        out.rename(out.with_name(out.name + ".attempt-" + time.strftime("%Y%m%d-%H%M%S")))
    out.mkdir(parents=True)
    artifacts = out / "independent_test_mod"
    artifacts.mkdir()
    export_command = [
        sys.executable, VERIFIER, "export",
        "--out", artifacts,
        "--parent-run", dst_run,
        "--jar", args.audit_jar,
        "--version", args.version,
        *(["--fixture-behavior-unchanged"] if fixture_behavior_unchanged else []),
        *(["--agent-mod-coexists"] if agent_mod_coexists else []),
        "--interface-sha", bridge_summary["interface"]["sha256"],
        "--bridge-commit", bridge_summary["bridge"]["head"],
        "--note", f"bridge_commit={bridge_summary['bridge']['head']}",
        "--note", f"interface_sha256={bridge_summary['interface']['sha256']}",
        "--events", f"{dst_log},{dst_run},exp-1,minecraft:overworld",
        "--events", f"{src_log},{src_run},src-audit,minecraft:overworld",
        *negative_specs,
    ]
    exported = run(export_command)
    print(exported.stdout.strip())
    print(json.dumps({"attestations": summary_flags}, indent=2))

    # -- partial gate bundle for schema validation --------------------------
    bundle_dir = out / "gate-bundle"
    run([sys.executable, GATE, "scaffold", bundle_dir])
    target = bundle_dir / "artifacts" / "independent_test_mod"
    target.mkdir(parents=True, exist_ok=True)
    for artifact in artifacts.iterdir():
        shutil.copy2(artifact, target / artifact.name)
    bundle_path = bundle_dir / "bundle.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["origin"] = "live"
    bundle["run"]["run_id"] = dst_run
    bundle["run"]["allowed_port_ranges"] = ["27180-27189"]
    bundle["run"]["child_runs"] = [{"run_id": src_run, "instance_id": "src-audit"}]
    bundle["run"]["instances"] = [
        {
            "instance_id": "src-audit",
            "role": "source_audit",
            "dimension": "minecraft:overworld",
            "world_dir": "labs/rom18-b6-src/world",
            "rcon_port": 27180,
            "bridge_port": 27183,
        },
        {
            "instance_id": "exp-1",
            "role": "experiment",
            "dimension": "minecraft:overworld",
            "world_dir": "labs/rom18-b6-dst/world",
            "rcon_port": 27184,
            "bridge_port": 27187,
        },
    ]
    bundle["run"]["source_world"] = {
        "label": "Minecart ROM test",
        "path": str(SOURCE_WORLD),
        "before_tree_sha256": SOURCE_HASH,
        "after_tree_sha256": SOURCE_HASH,
    }
    bundle_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    report_path = bundle_dir / "gate-report.json"
    check = run(
        [sys.executable, GATE, "check", bundle_dir, "--source-world", str(SOURCE_WORLD),
         "--report", str(report_path)],
        check=False,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    independent = next((entry for entry in report["checks"] if entry.get("id") == "independent_test_mod"), None)
    status = independent.get("status") if independent else "missing"
    summary = {
        "auditJar": {"path": str(audit_jar), "sha256": current_jar_sha},
        "attestations": summary_flags,
        "overall": report.get("overall"),
        "independent_test_mod": status,
        "reasons": [reason["message"] for reason in (independent or {}).get("reasons", [])],
        "assertions": (independent or {}).get("assertions", []),
        "gateReport": str(report_path),
        "artifacts": sorted(str(path) for path in artifacts.iterdir()),
        "negativeRefs": negative_refs,
        "note": (
            "partial live bundle: only issue #18 artifacts are present, so the overall "
            "gate is blocked by the other prerequisites. This records the adapter status, "
            "not a gate pass."
        ),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"stage1 fragment: {out}")
    if check.returncode not in (0, 1, 3):
        raise SystemExit(f"stage1_gate.py failed unexpectedly ({check.returncode}): {check.stderr}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
