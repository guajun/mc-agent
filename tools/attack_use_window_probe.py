#!/usr/bin/env python3
"""Focused same-window attack->use probe for the #18 pending-play contract.

The round-3 review accepted the frozen full-gate run but required a real
regression where the punch and the use land inside the audit mod's
``correlationWindowTicks`` (the previous live run's commands were 14 ticks
apart, so it did not exercise the stale-pending window).

This probe runs one fresh disposable void lab with the already-reviewed audit
jar, places a non-air block above the note block (the unscheduled ``playNote``
path), then sends ``player Bot attack once`` and ``player Bot use once``
back-to-back over a single persistent RCON TCP connection (no process spawn
between them).  It then asserts directly from the raw audit log:

* the attack request and the use request are at most
  ``correlationWindowTicks`` ticks apart;
* exactly one ``input_processed`` exists, it belongs to the use's
  ``useWithoutItem`` attempt, and no processed event is attributed to the
  attack request/attempt (no stale pending, no double count).

The report records the probe driver's own Git commit and byte/blob hashes, the
audit jar hash, the raw log hash and the raw request/attempt/processed rows.

    python tools/attack_use_window_probe.py --out labs/attack-window-probe
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
LABS = ROOT / "labs"
LAB_SERVER = ROOT / "tools" / "lab_server.py"
AUDIT_JAR = ROOT / "tests" / "mods" / "minecart-audit" / "dist" / "mc-minecart-audit-0.1.0.jar"
JAVA = Path(
    "C:/Users/MSI-NB/AppData/Roaming/.hmcl/java/windows-x86_64/"
    "mojang-java-runtime-epsilon/bin/java.exe"
)
JDK = JAVA.parent.parent
CORRELATION_WINDOW_TICKS = 2

DEFAULT_LAB = "rom13-win"
DEFAULT_PORTS = (27152, 27153)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True).stdout


def driver_provenance() -> dict[str, Any]:
    relative = "tools/attack_use_window_probe.py"
    data = (ROOT / relative).read_bytes()
    blob = git("cat-file", "-p", f"HEAD:{relative}")
    return {
        "path": relative,
        "head": git("rev-parse", "HEAD").decode("utf-8", errors="replace").strip(),
        "actual_sha256": hashlib.sha256(data).hexdigest(),
        "git_blob_sha256": hashlib.sha256(blob).hexdigest() if blob else None,
        "normalized_matches_git_blob": blob == data.replace(b"\r\n", b"\n") if blob else None,
        "bytes": len(data),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def analyse(events: list[dict[str, Any]], correlation_window: int = CORRELATION_WINDOW_TICKS) -> dict[str, Any]:
    """Pure raw-log analysis: the pending contract, no game access."""

    def of_type(kind: str) -> list[dict[str, Any]]:
        return [row for row in events if row.get("type") == kind and isinstance(row.get("seq"), int)]

    requests = sorted(of_type("input_request"), key=lambda row: (int(row["seq"]), int(row.get("tick") or 0)))
    attempts = sorted(of_type("input_attempt"), key=lambda row: (int(row["seq"]), int(row.get("tick") or 0)))
    processed = sorted(of_type("input_processed"), key=lambda row: (int(row["seq"]), int(row.get("tick") or 0)))
    request_by_seq = {int(row["seq"]): row for row in requests}
    attempt_by_seq = {int(row["seq"]): row for row in attempts}

    attack_attempts = [row for row in attempts if row.get("path") == "attack"]
    use_attempts = [row for row in attempts if row.get("path") in ("useItemOn", "useWithoutItem")]

    def linked_request(attempt: dict[str, Any]) -> dict[str, Any] | None:
        request_seq = attempt.get("requestSeq")
        if isinstance(request_seq, int) and request_seq in request_by_seq:
            return request_by_seq[request_seq]
        # The attack hook can log before the request; take the nearest earlier
        # request at the same position.
        earlier = [
            row
            for row in requests
            if int(row["seq"]) < int(attempt["seq"])
            and (row.get("pos") or {}).get("x") == (attempt.get("pos") or {}).get("x")
            and (row.get("pos") or {}).get("z") == (attempt.get("pos") or {}).get("z")
        ]
        return earlier[-1] if earlier else None

    attack_request = next((linked_request(row) for row in attack_attempts if linked_request(row)), None)
    # Select the use's own explicit request, never let a potentially stale
    # processed record choose the request that will validate itself.
    use_without_item = [row for row in use_attempts if row.get("path") == "useWithoutItem"]
    use_request = next(
        (request_by_seq[row["requestSeq"]] for row in use_without_item
         if isinstance(row.get("requestSeq"), int) and row["requestSeq"] in request_by_seq),
        None,
    )
    attack_request_seqs = {
        int(request["seq"]) for attempt in attack_attempts
        if (request := linked_request(attempt)) is not None
    }
    attack_seqs = {int(row["seq"]) for row in attack_attempts}
    use_seqs = {int(row["seq"]) for row in use_attempts}
    attack_request_seq = int(attack_request["seq"]) if attack_request else None
    use_request_seq = int(use_request["seq"]) if use_request else None

    def processed_for(request_seq: int | None, attempt_seqs: set[int]) -> list[dict[str, Any]]:
        rows = []
        for row in processed:
            if row.get("attemptSeq") in attempt_seqs or row.get("requestSeq") == request_seq:
                rows.append(row)
        return rows

    attack_processed = [row for row in processed
                        if row.get("attemptSeq") in attack_seqs
                        or row.get("requestSeq") in attack_request_seqs]
    use_processed = processed_for(use_request_seq, use_seqs)
    request_gap_ticks = (
        int(use_request.get("tick") or 0) - int(attack_request.get("tick") or 0)
        if attack_request and use_request
        else None
    )
    within_window = (
        request_gap_ticks is not None and 0 <= request_gap_ticks <= correlation_window
    )
    exactly_one_use = (
        use_request is not None and len(use_processed) == 1
        and use_processed[0].get("path") == "playNote"
        and use_processed[0].get("requestSeq") == use_request_seq
        and use_processed[0].get("attemptSeq") in {int(row["seq"]) for row in use_without_item}
    )
    no_stale_attack = not attack_processed
    return {
        "correlationWindowTicks": correlation_window,
        "requestGapTicks": request_gap_ticks,
        "withinWindow": within_window,
        "attackRequestSeq": attack_request_seq,
        "attackRequestSeqs": sorted(attack_request_seqs),
        "useRequestSeq": use_request_seq,
        "attackAttemptSeqs": sorted(attack_seqs),
        "useAttemptSeqs": sorted(use_seqs),
        "requests": requests,
        "attempts": attempts,
        "processed": processed,
        "attackProcessed": attack_processed,
        "useProcessed": use_processed,
        "exactlyOneUseProcessed": exactly_one_use,
        "noStaleAttackAttribution": no_stale_attack,
        "ok": bool(within_window and exactly_one_use and no_stale_attack and len(processed) == 1),
    }


def run(argv: list[object]) -> subprocess.CompletedProcess:
    return subprocess.run([str(item) for item in argv], capture_output=True, text=True, timeout=1200)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="labs/attack-window-probe")
    parser.add_argument("--lab", default=DEFAULT_LAB)
    parser.add_argument("--server-port", type=int, default=DEFAULT_PORTS[0])
    parser.add_argument("--rcon-port", type=int, default=DEFAULT_PORTS[1])
    parser.add_argument("--jar", default=str(AUDIT_JAR))
    parser.add_argument("--keep-lab", action="store_true", help="leave the disposable lab running for inspection")
    args = parser.parse_args(argv)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    driver = driver_provenance()
    if not driver["normalized_matches_git_blob"]:
        raise SystemExit(
            "the probe driver is not committed/modified-only-by-line-endings; commit it before running "
            f"(head={driver['head']} actual={driver['actual_sha256'][:12]})"
        )
    jar = Path(args.jar)
    if not jar.is_file():
        raise SystemExit(f"audit jar not found: {jar}")
    jar_sha = sha256_file(jar)

    run([sys.executable, str(LAB_SERVER), "stop", "--name", args.lab, "--timeout", "120"])
    provision = run([
        sys.executable, str(LAB_SERVER), "provision", "--name", args.lab, "--force", "--void",
        "--fabric-api", "--carpet", "--java", str(JAVA), "--jdk", str(JDK),
        "--server-port", str(args.server_port), "--rcon-port", str(args.rcon_port),
        "--test-mod", str(jar),
    ])
    if provision.returncode != 0:
        raise SystemExit(f"provision failed: {provision.stderr[-500:]}")
    lab_dir = LABS / args.lab
    run_id = f"{args.lab}-window-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"
    config = {
        "runId": run_id,
        "instanceId": args.lab,
        "dimension": "minecraft:overworld",
        "provenance": {"kind": "same-window-probe", "reference": "tools/attack_use_window_probe.py"},
        "inputRegions": [{"name": "note-block", "from": [0, -59, 0], "to": [0, -59, 0]}],
        "stackRegion": {"name": "stack", "from": [2, -60, -1], "to": [3, -59, 1]},
        "outputRegion": {"name": "output", "from": [4, -140, -8], "to": [8, -60, 8]},
        "cartTypes": ["minecraft:chest_minecart"],
        "sampleIntervalTicks": 20,
        "correlationWindowTicks": CORRELATION_WINDOW_TICKS,
        "agentUuids": [],
    }
    (lab_dir / "mc-audit").mkdir(parents=True, exist_ok=True)
    write_json(lab_dir / "mc-audit" / "config.json", config)
    started = run([sys.executable, str(LAB_SERVER), "start", "--name", args.lab, "--wait", "300"])
    if started.returncode != 0:
        raise SystemExit(f"lab start failed: {started.stderr[-500:]}")

    sys.path.insert(0, str(ROOT / "tools"))
    import lab_server  # noqa: E402

    log = lab_dir / "mc-audit" / f"audit-{run_id}.jsonl"
    report: dict[str, Any] = {
        "kind": "mc-agent/attack-use-window-probe@1",
        "lab": args.lab,
        "ports": {"server": args.server_port, "rcon": args.rcon_port},
        "auditJar": {"path": str(jar), "sha256": jar_sha, "bytes": jar.stat().st_size},
        "config": config,
        "driver": driver,
        "logPath": str(log),
    }
    def c(console: Any, command: str) -> str:
        # A freshly force-loaded void world can lag behind for a moment, but
        # the replies themselves are immediate; a long idle here would only
        # delay the setup while the probe bot is standing in the void.
        return console.command(command, idle=2.0)

    def setup(console: Any) -> None:
        c(console, "forceload add -16 -16 16 16")
        time.sleep(1.0)
        c(console, "fill -3 -60 -3 3 -60 3 minecraft:stone")
        c(console, "setblock 0 -59 0 minecraft:note_block")
        # A non-air block above keeps vanilla from scheduling the block event,
        # so the playNote pending contract is the one under test.
        c(console, "setblock 0 -58 0 minecraft:stone")
        c(console, "player Bot spawn at 0.5 -59.0 2.5 facing 180 30 in minecraft:overworld in creative")
        deadline = time.time() + 120
        while time.time() < deadline:
            probe = c(console, "data get entity Bot Pos")
            if "has the following entity data" in probe:
                break
            time.sleep(1.0)
        else:
            raise RuntimeError("the probe fake player never spawned")
        c(console, "gamemode survival Bot")
        # Idempotent placement: a fake player saved from an earlier probe can
        # sit anywhere in the world, so always park it on the pad and aim it.
        c(console, "tp Bot 0.5 -59.0 2.5 180 30")
        c(console, "player Bot look 30 180")
        note = c(console, "execute if block 0 -59 0 minecraft:note_block")
        if "Test passed" not in note:
            c(console, "setblock 0 -59 0 minecraft:note_block")
        above = c(console, "execute if block 0 -58 0 minecraft:stone")
        if "Test passed" not in above:
            c(console, "setblock 0 -58 0 minecraft:stone")
        time.sleep(1.0)
        c(console, "mcaudit phase init")
        c(console, "mcaudit phase experiment_start")

    console = lab_server.open_console(lab_dir)
    console.connect()
    try:
        for attempt in range(3):
            try:
                setup(console)
                break
            except (lab_server.RconError, OSError) as error:
                console.close()
                time.sleep(5.0)
                console = lab_server.open_console(lab_dir)
                console.connect()
        else:
            raise RuntimeError("the RCON console could not stay connected for setup")
        # The two commands are written to the socket back-to-back without
        # waiting for the first reply: no process spawn and no round trip
        # between them, so the server processes both in the same server-thread
        # batch (the same or adjacent tick).
        # Park and aim the bot once more immediately before the pair so the
        # elapsed setup time cannot leave it falling in the void.
        c(console, "tp Bot 0.5 -59.0 2.5 180 30")
        c(console, "player Bot look 30 180")
        report["botProbe"] = c(console, "data get entity Bot Pos").strip()[:200]
        time.sleep(0.2)
        # Two synchronous sends with a minimal reply idle: the first command
        # is processed and answered (or a 50 ms idle elapses) before the
        # second goes out, so both land in the same or adjacent tick without
        # any process spawn between them.
        start = time.time()
        attack_reply = console.command("player Bot attack once", idle=0.05)
        use_reply = console.command("player Bot use once", idle=0.05)
        report["commandSendGapMs"] = round((time.time() - start) * 1000.0, 3)
        report["attackReply"] = attack_reply[-200:]
        report["useReply"] = use_reply[-200:]
        time.sleep(1.5)
        c(console, "mcaudit phase experiment_end")
        c(console, "mcaudit end")
        report["events"] = read_jsonl(log)
        report["analysis"] = analyse(report["events"])
        report["rawLogSha256"] = sha256_file(log)
        report["rawCopy"] = str(out / "audit-window.jsonl")
        (out / "audit-window.jsonl").write_bytes(log.read_bytes())
        write_json(out / "probe-report.json", report)
    finally:
        try:
            console.close()
        except Exception:  # noqa: BLE001 - closing a broken probe console is best effort
            pass
        if not args.keep_lab:
            run([sys.executable, str(LAB_SERVER), "stop", "--name", args.lab, "--timeout", "120"])

    analysis = report.get("analysis") or {}
    print(json.dumps({
        "ok": analysis.get("ok"),
        "requestGapTicks": analysis.get("requestGapTicks"),
        "withinWindow": analysis.get("withinWindow"),
        "exactlyOneUseProcessed": analysis.get("exactlyOneUseProcessed"),
        "noStaleAttackAttribution": analysis.get("noStaleAttackAttribution"),
        "processed": len(analysis.get("processed") or []),
        "sendGapMs": report.get("commandSendGapMs"),
        "report": str(out / "probe-report.json"),
    }, indent=2))
    return 0 if analysis.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
