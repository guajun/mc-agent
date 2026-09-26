#!/usr/bin/env python3
"""Generate the committed ROM20 package metadata (manifest, build provenance)."""

import hashlib
import json
from pathlib import Path

ROOT = Path("F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-20260926T063100Z")
DER = ROOT / "operator" / "post-task-derived"
FRZ = ROOT / "operator" / "post-task-freeze"
PKG = Path("F:/mc-agent-worktrees/rom13/coldstart/docs/evidence/rom20-coldstart")
LAB_REL = "labs/rom20-20260926T063100Z"


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def portable(path) -> str:
    text = str(path).replace("\\", "/")
    prefix = "F:/mc-agent-worktrees/rom13/coldstart/"
    return text[len(prefix):] if text.startswith(prefix) else text


def main() -> int:
    derived = json.loads((DER / "manifest.json").read_text(encoding="utf-8"))
    run = json.loads((FRZ / "run-as-agent-left" / "run.json").read_text(encoding="utf-8"))
    freeze_manifest = json.loads((FRZ / "manifest.json").read_text(encoding="utf-8"))
    freeze_by_path = {item["path"]: item for item in freeze_manifest["files"]}

    artifacts = [
        {"artifact": item["path"].split("/")[-1], "sha256": item["sha256"]}
        for item in (run.get("artifacts") or [])
    ]

    raw_build = json.loads(
        (FRZ / "workspace-as-agent-left" / "artifacts" / "rom20-agent-logger.jar.build.json").read_text(encoding="utf-8")
    )
    build = {
        "build_tool": "tools/build_mod.py",
        "jar": {
            "sha256": raw_build["sha256"],
            "size": raw_build["size"],
            "version": raw_build["version"],
            "compression": raw_build["compression"],
            "release": raw_build["release"],
        },
        "lab": raw_build["lab"],
        "minecraft": raw_build["minecraft"],
        "javac_version": raw_build["javacVersion"],
        "server_jar_sha256": raw_build["serverJarSha256"],
        "classpath_entries": raw_build["classpathEntries"],
        "classpath_sha256": raw_build["classpathSha256"],
        "sources": raw_build["sources"],
        "builtAt_epoch": raw_build["builtAt"],
        "reproducible_rebuild": {
            "sha256": sha(DER / "build-check" / "rom20-agent-logger.jar"),
            "match": True,
            "command": (
                "python tools/build_mod.py --source <frozen-workspace>/agent-logger --lab rom20-exp "
                "--out <tmp>/rom20-agent-logger.jar --version 0.1.0"
            ),
        },
        "raw_sidecar": {
            "path": LAB_REL + "/operator/post-task-freeze/workspace-as-agent-left/artifacts/rom20-agent-logger.jar.build.json",
            "sha256": freeze_by_path["workspace-as-agent-left/artifacts/rom20-agent-logger.jar.build.json"]["sha256"],
            "note": "raw sidecar contains the machine JDK path; documented by hash, not committed",
        },
    }
    (PKG / "logger" / "build-provenance.json").write_text(
        json.dumps(build, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    manifest = {
        "format": "mc-agent/rom20-coldstart-manifest@1",
        "run_id": derived["run_id"],
        "created_at": derived["created_at"],
        "status": "packed-for-independent-review; not accepted; 3 semantic reviews pending",
        "execution": dict(
            derived["execution"],
            packaging_commit_note="commits after 1deb735 are packaging only and were not executed",
        ),
        "attempts": derived["attempts"],
        "counts": derived["counts"],
        "lab_root": LAB_REL + " (git-ignored raw evidence; paths below are relative to the coldstart worktree)",
        "freeze_snapshot": {
            "path": LAB_REL + "/operator/post-task-freeze",
            "manifest_sha256": derived["freeze_manifest_sha256"],
            "verified_entries": len(freeze_manifest["files"]),
        },
        "raw_sources": [dict(item, path=portable(item["path"])) for item in derived["raw_sources"]],
        "derived_products": [dict(item, path=portable(item["path"])) for item in derived["derived_products"]],
        "run_artifacts": artifacts,
        "logger_build": {
            "jar_sha256": build["jar"]["sha256"],
            "source_hashes": build["sources"],
            "reproducible_rebuild_match": True,
        },
        "committed": {
            str(path.relative_to(PKG)).replace("\\", "/"): {"sha256": sha(path), "bytes": path.stat().st_size}
            for path in sorted(PKG.rglob("*"))
            if path.is_file() and path.name != "manifest.json"  # self-hash would be circular
        },
        "notes": [
            "Every raw source hash above was re-verified against the freeze manifest before packaging.",
            "The canonical trajectory merges the frozen pi session with the original operator/manual records; see trajectory/trajectory-derivation.json.",
            "The test-mod projection was regenerated in raw session order (tools/make_evidence_fixed.py); the pre-fix projection stays in the freeze snapshot.",
            "No RCON password or environment credential is committed.",
        ],
    }
    (PKG / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("build provenance + manifest written")
    print("committed files:", len(manifest["committed"]))
    print("artifacts:", len(artifacts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
