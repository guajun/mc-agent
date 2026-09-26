#!/usr/bin/env python3
"""Derive the ROM20 evidence products from the frozen raw logs (fixed projection).

This is the packaging-time fixed version of the script the task agent ran
(`workspace/scripts/make_evidence.py`, artifact 008 of the frozen run). The only
behavioural fix is the canonical test-mod projection:

  * the projection is now emitted in one pass, in the raw audit log's own
    session order (asserted monotonic `seq`), so tick/seq order is preserved;
  * `input_request` events are included, so the input chain is complete;
  * `instance_ready` / `phase` / `end` are emitted where they occurred instead
    of being appended at the end.

Answer, oracle and normalized-logger derivations are unchanged. Old outputs and
the raw logs are never touched: everything is written under `--out`.

Inputs are deliberately explicit paths so the derivation can be anchored to the
immutable post-task-freeze snapshot:

    python make_evidence_fixed.py \
        --raw-audit  <freeze>/testmod/audit-<run>.jsonl \
        --raw-logger <freeze>/agent-logger-raw/romlog.jsonl \
        --restore-apply <freeze>/workspace-as-agent-left/evidence/restore-apply.json \
        --restore-verify <freeze>/workspace-as-agent-left/evidence/restore-verify.json \
        --out <derived>/evidence
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

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


def compact(record: dict) -> dict:
    return {key: value for key, value in record.items() if value is not None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-audit", required=True, type=Path)
    parser.add_argument("--raw-logger", required=True, type=Path)
    parser.add_argument("--restore-apply", required=True, type=Path)
    parser.add_argument("--restore-verify", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-id", default=RUN_ID)
    parser.add_argument("--instance", default=INSTANCE)
    parser.add_argument("--dimension", default=DIMENSION)
    args = parser.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    audit = read_jsonl(args.raw_audit)
    logger = read_jsonl(args.raw_logger)

    # The raw audit log must be in session order; the projection relies on it.
    seqs = [record.get("seq") for record in audit if isinstance(record.get("seq"), int)]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "raw audit seq is not monotonic"

    # ---------------------------------------------------------------- answer
    pops = [
        record
        for record in logger
        if record.get("event") == "cart_observed" and record.get("via") == "left_stack_region"
    ]
    assert len(pops) == 5, f"expected 5 experiment captures, got {len(pops)}"
    answer = {
        "format": "mc-agent/minecart-rom-answer@1",
        "run_id": args.run_id,
        "instance": args.instance,
        "dimension": args.dimension,
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
    write_json(out / "answer.json", answer)

    # ---------------------------------- canonical projection + oracle (one pass)
    projected: list[dict] = []
    oracle_carts: list[dict] = []
    exit_seq: dict[str, int] = {}
    for record in audit:
        kind = record.get("type")
        phase = record.get("phase")
        if kind in ("input_attempt", "input_request", "input_processed", "cart_exit", "cart_remove") and phase == "experiment":
            event = compact(
                {
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
                    "run_id": args.run_id,
                    "phase": phase,
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
            )
            projected.append(event)
            if kind == "cart_exit" and record.get("uuid") not in exit_seq:
                exit_seq[record.get("uuid")] = record.get("seq")
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
        elif kind == "phase" and record.get("to") == "experiment":
            projected.append(
                compact(
                    {
                        "event": "instance_ready",
                        "seq": record.get("seq"),
                        "tick": record.get("tick"),
                        "at": iso_from_wall(int(record["wall"])),
                        "wall": record.get("wall"),
                        "instance": record.get("inst"),
                        "dimension": args.dimension,
                        "run_id": args.run_id,
                        "phase": "experiment",
                        "raw_type": kind,
                    }
                )
            )
        elif kind == "phase" and record.get("from") == "experiment":
            projected.append(
                compact(
                    {
                        "event": "phase",
                        "seq": record.get("seq"),
                        "tick": record.get("tick"),
                        "at": iso_from_wall(int(record["wall"])),
                        "wall": record.get("wall"),
                        "instance": record.get("inst"),
                        "dimension": args.dimension,
                        "run_id": args.run_id,
                        "phase": "post",
                        "from": record.get("from"),
                        "to": record.get("to"),
                        "raw_type": kind,
                    }
                )
            )
        elif kind == "audit_end":
            projected.append(
                compact(
                    {
                        "event": "end",
                        "seq": record.get("seq"),
                        "tick": record.get("tick"),
                        "at": iso_from_wall(int(record["wall"])),
                        "wall": record.get("wall"),
                        "instance": record.get("inst"),
                        "dimension": args.dimension,
                        "run_id": args.run_id,
                        "phase": "end",
                        "reason": record.get("reason"),
                        "raw_type": kind,
                    }
                )
            )
    with (out / "testmod.experiment.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in projected:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    oracle = {
        "format": "mc-agent/minecart-rom-oracle@1",
        "run_id": args.run_id,
        "instance": args.instance,
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
    write_json(out / "oracle.json", oracle)

    # ------------------------------------------------ normalized agent logger
    raw_logger_sha = sha256(args.raw_logger)
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
    with (out / "romlog.experiment.norm.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in normalized:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ------------------------------------------------------- restore evidence
    apply_report = json.loads(args.restore_apply.read_text(encoding="utf-8"))
    verify_report = json.loads(args.restore_verify.read_text(encoding="utf-8"))
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
    write_json(out / "restore-evidence.json", restore_evidence)

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

    projection_seqs = [record["seq"] for record in projected]
    if projection_seqs != sorted(projection_seqs):
        print("PROJECTION ORDER FAILED: seq is not monotonic")
        return 1

    # ------------------------------------------------------------- hashes
    raw_hashes = {
        "raw_audit": {"path": str(args.raw_audit), "sha256": sha256(args.raw_audit), "bytes": args.raw_audit.stat().st_size},
        "raw_agent_logger": {"path": str(args.raw_logger), "sha256": raw_logger_sha, "bytes": args.raw_logger.stat().st_size},
        "outputs": {
            name: {"path": str(out / name), "sha256": sha256(out / name), "bytes": (out / name).stat().st_size}
            for name in (
                "answer.json",
                "testmod.experiment.jsonl",
                "oracle.json",
                "romlog.experiment.norm.jsonl",
                "restore-evidence.json",
            )
        },
    }
    write_json(out / "raw-hashes.json", raw_hashes)

    summary = {
        "pops": [
            {"order": cart["order"], "uuid": cart["uuid"], "items": [normalize_item(item) for item in cart["items"]]}
            for cart in answer["carts"]
        ],
        "oracle_refs": oracle["evidence_refs"],
        "projection_records": len(projected),
        "projection_seq_monotonic": True,
        "cross_check": "ok",
        "raw_logger_sha256": raw_logger_sha,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
