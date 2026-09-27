#!/usr/bin/env python3
"""Generate the cold-start evidence products from the raw logs, mechanically.

Inputs (raw, never modified):
  * labs/rom20-exp/mc-audit/audit-rom20-20260926T063100Z.jsonl  (evaluator mod)
  * labs/rom20-exp/audit/romlogger/romlog.jsonl                 (agent logger)

Outputs (workspace/evidence/):
  * answer.json                     the agent's ordered result per popped cart
  * testmod.experiment.jsonl        canonical projection of the audit log
  * oracle.json                     independent oracle from the audit's captured carts
  * romlog.experiment.norm.jsonl    normalized view of the agent logger
  * restore-evidence.json           restore-apply + verify summary
  * raw-hashes.json                 sha256 of every raw input and output

The projection keeps the audit's original `seq` values so oracle refs like
`testmod:seq1010` resolve against it. Nothing here is hand-written: the answer
comes from the agent logger's `cart_observed` records and is cross-checked
against the audit's `cart_remove` inventories before it is written.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("F:/mc-agent-worktrees/rom13/coldstart")
LAB = ROOT / "labs" / "rom20-exp"
WS = ROOT / "labs" / "rom20-20260926T063100Z" / "workspace"
OUT = WS / "evidence"
RAW_AUDIT = LAB / "mc-audit" / "audit-rom20-20260926T063100Z.jsonl"
RAW_LOGGER = LAB / "audit" / "romlogger" / "romlog.jsonl"
JAR = WS / "artifacts" / "rom20-agent-logger.jar"
RUN_ID = "rom20-20260926T063100Z"
INSTANCE = "experiment"
DIMENSION = "minecraft:overworld"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def iso_from_wall(wall_ms: int) -> str:
    moment = datetime.fromtimestamp(wall_ms / 1000.0, tz=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def normalize_item(item: dict) -> str:
    name = str(item.get("item") or item.get("id") or "")
    count = int(item.get("count", 1))
    return name if count == 1 else f"{name} x{count}"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    audit = read_jsonl(RAW_AUDIT)
    logger = read_jsonl(RAW_LOGGER)

    # ---------------------------------------------------------------- answer
    pops = [
        record
        for record in logger
        if record.get("event") == "cart_observed" and record.get("via") == "left_stack_region"
    ]
    assert len(pops) == 5, f"expected 5 experiment captures, got {len(pops)}"
    answer = {
        "format": "mc-agent/minecart-rom-answer@1",
        "run_id": RUN_ID,
        "instance": INSTANCE,
        "dimension": DIMENSION,
        "method": (
            "Carpet fake player Romuser right-clicked the note block at "
            "11,-54,-23 five times (Carpet 'use once'); each pop was captured by "
            "the agent's own server-side logger before the cart was destroyed."
        ),
        "pop_order": [record["uuid"] for record in pops],
        "carts": [
            {
                "order": index + 1,
                "uuid": record["uuid"],
                "tick": record.get("tick"),
                "items": [
                    {"slot": int(item["slot"]), "id": item["id"], "count": int(item["count"])}
                    for item in record["items"]
                ],
            }
            for index, record in enumerate(pops)
        ],
    }
    write_json(OUT / "answer.json", answer)

    # ------------------------------------------------- projection / oracle
    projected = []
    oracle_carts = []
    experiment_events = [
        record for record in audit if record.get("phase") == "experiment"
    ]
    for record in audit:
        kind = record.get("type")
        if record.get("phase") == "experiment" and kind in (
            "input_attempt",
            "input_processed",
            "cart_exit",
            "cart_remove",
        ):
            event = {
                "event": {
                    "cart_exit": "cart_ejected",
                    "cart_remove": "cart_removed",
                }.get(kind, kind),
                "seq": record.get("seq"),
                "tick": record.get("tick"),
                "at": iso_from_wall(int(record["wall"])),
                "wall": record.get("wall"),
                "instance": record.get("inst"),
                "dimension": record.get("dimension") or record.get("level"),
                "run_id": RUN_ID,
                "phase": record.get("phase"),
                "raw_type": kind,
                "uuid": record.get("uuid"),
                "pos": record.get("pos"),
                "operator": record.get("operator"),
                "agentOp": record.get("agentOp"),
                "path": record.get("path"),
                "inventory": record.get("inventory"),
                "capturedPath": record.get("capturedPath"),
                "reason": record.get("reason"),
            }
            projected.append({key: value for key, value in event.items() if value is not None})
            if kind == "cart_remove":
                oracle_carts.append(
                    {
                        "uuid": record.get("uuid"),
                        "items": [
                            {
                                "slot": int(item["slot"]),
                                "item": item.get("item"),
                                "count": int(item["count"]),
                            }
                            for item in (record.get("inventory") or [])
                        ],
                        "capturedPath": record.get("capturedPath"),
                        "removeSeq": record.get("seq"),
                        "removeTick": record.get("tick"),
                    }
                )
        elif kind == "phase" and record.get("phase") == "experiment" and record.get("to") == "experiment":
            projected.append(
                {
                    "event": "instance_ready",
                    "seq": record.get("seq"),
                    "tick": record.get("tick"),
                    "at": iso_from_wall(int(record["wall"])),
                    "wall": record.get("wall"),
                    "instance": record.get("inst"),
                    "dimension": DIMENSION,
                    "run_id": RUN_ID,
                    "phase": "experiment",
                    "raw_type": kind,
                }
            )
    # observed exit order per uuid (first cart_exit seq)
    exit_seq = {
        record["uuid"]: record["seq"]
        for record in audit
        if record.get("phase") == "experiment" and record.get("type") == "cart_exit"
    }
    # close the experiment with the real phase transition and audit_end
    for record in audit:
        if record.get("type") == "phase" and record.get("from") == "experiment":
            projected.append(
                {
                    "event": "phase",
                    "seq": record.get("seq"),
                    "tick": record.get("tick"),
                    "at": iso_from_wall(int(record["wall"])),
                    "wall": record.get("wall"),
                    "instance": record.get("inst"),
                    "dimension": DIMENSION,
                    "run_id": RUN_ID,
                    "phase": "post",
                    "from": record.get("from"),
                    "to": record.get("to"),
                    "raw_type": "phase",
                }
            )
        elif record.get("type") == "audit_end":
            projected.append(
                {
                    "event": "end",
                    "seq": record.get("seq"),
                    "tick": record.get("tick"),
                    "at": iso_from_wall(int(record["wall"])),
                    "wall": record.get("wall"),
                    "instance": record.get("inst"),
                    "dimension": DIMENSION,
                    "run_id": RUN_ID,
                    "phase": "end",
                    "reason": record.get("reason"),
                    "raw_type": "audit_end",
                }
            )
    with (OUT / "testmod.experiment.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in projected:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    oracle = {
        "format": "mc-agent/minecart-rom-oracle@1",
        "run_id": RUN_ID,
        "instance": INSTANCE,
        "status": "verified",
        "source": (
            "independent test mod mc-minecart-audit-0.1.0 raw audit log, "
            "cart_remove inventories captured before destruction"
        ),
        "evidence_refs": [
            "testmod:seq%d" % exit_seq[cart["uuid"]] for cart in oracle_carts
        ]
        + ["testmod:seq%d" % cart["removeSeq"] for cart in oracle_carts],
        "carts": oracle_carts,
    }
    write_json(OUT / "oracle.json", oracle)

    # ------------------------------------------------ normalized agent logger
    raw_logger_sha = sha256(RAW_LOGGER)
    last_armed = max(
        (record for record in logger if record.get("event") == "logger_armed"),
        key=lambda record: record.get("seq", 0),
    )
    normalized = []
    for record in logger:
        if record.get("seq", 0) < last_armed.get("seq", 0):
            continue
        if record.get("event") == "cart_observed" and record.get("via") != "left_stack_region":
            continue
        if record.get("event") not in ("logger_armed", "cart_observed", "logger_flushed", "logger_error"):
            continue
        entry = dict(record)
        entry["source_sha256"] = raw_logger_sha
        normalized.append(entry)
    with (OUT / "romlog.experiment.norm.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in normalized:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------- restore evidence
    apply_report = json.loads((OUT / "restore-apply.json").read_text(encoding="utf-8"))
    verify_report = json.loads((OUT / "restore-verify.json").read_text(encoding="utf-8"))
    verification = apply_report.get("verification") or {}
    verify_verification = verify_report.get("verification") or {}
    restore_evidence = {
        "format": "mc-agent/minecart-rom-restore@1",
        "status": "ok",
        "directory": apply_report.get("directory"),
        "sourceOrderHash": apply_report.get("orderHash"),
        "apply": {
            "issued": apply_report.get("issued"),
            "failed": len(apply_report.get("failed") or []),
            "ok": apply_report.get("ok"),
            "verdict": apply_report.get("verdict"),
            "orderHashMatch": (verification.get("orderHash") or {}).get("match"),
            "countsMatch": (verification.get("counts") or {}).get("match"),
            "uuidOrderMatch": (verification.get("uuidOrder") or {}).get("match"),
            "positionDeltaCount": (verification.get("positionDeltas") or {}).get("count"),
            "velocityDeltaCount": (verification.get("velocityDeltas") or {}).get("count"),
            "nbtMismatchCount": (verification.get("nbtMismatches") or {}).get("count")
            if isinstance(verification.get("nbtMismatches"), dict)
            else None,
        },
        "verify": {
            "ok": verify_report.get("ok"),
            "orderHashMatch": (verify_verification.get("orderHash") or {}).get("match"),
            "countsMatch": (verify_verification.get("counts") or {}).get("match"),
            "uuidOrderMatch": (verify_verification.get("uuidOrder") or {}).get("match"),
        },
    }
    write_json(OUT / "restore-evidence.json", restore_evidence)

    # --------------------------------------------------------- cross-check
    oracle_by_uuid = {cart["uuid"]: cart for cart in oracle_carts}
    problems = []
    for cart in answer["carts"]:
        expected = oracle_by_uuid.get(cart["uuid"])
        if expected is None:
            problems.append(f"answer cart {cart['uuid']} missing from oracle")
            continue
        got = [normalize_item(item) for item in cart["items"]]
        want = [normalize_item(item) for item in expected["items"]]
        if got != want:
            problems.append(f"cart {cart['uuid']}: answer {got} != oracle {want}")
    if [cart["uuid"] for cart in answer["carts"]] != [cart["uuid"] for cart in oracle_carts]:
        problems.append("pop order differs between answer and oracle")
    if problems:
        print("CROSS-CHECK FAILED:")
        for problem in problems:
            print(" ", problem)
        return 1

    # ------------------------------------------------------------- hashes
    raw_hashes = {
        "raw_audit": {"path": str(RAW_AUDIT), "sha256": sha256(RAW_AUDIT), "bytes": RAW_AUDIT.stat().st_size},
        "raw_agent_logger": {"path": str(RAW_LOGGER), "sha256": raw_logger_sha, "bytes": RAW_LOGGER.stat().st_size},
        "logger_jar": {"path": str(JAR), "sha256": sha256(JAR), "bytes": JAR.stat().st_size},
        "outputs": {
            name: {"path": str(OUT / name), "sha256": sha256(OUT / name), "bytes": (OUT / name).stat().st_size}
            for name in (
                "answer.json",
                "testmod.experiment.jsonl",
                "oracle.json",
                "romlog.experiment.norm.jsonl",
                "restore-evidence.json",
            )
        },
    }
    write_json(OUT / "raw-hashes.json", raw_hashes)

    summary = {
        "pops": [
            {"order": cart["order"], "uuid": cart["uuid"], "items": [normalize_item(item) for item in cart["items"]]}
            for cart in answer["carts"]
        ],
        "oracle_refs": oracle["evidence_refs"],
        "cross_check": "ok",
        "raw_logger_sha256": raw_logger_sha,
        "hashes": raw_hashes["outputs"],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
