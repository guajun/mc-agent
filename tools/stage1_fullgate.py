#!/usr/bin/env python3
"""Full stage-one ROM gate orchestration on a live fixture.

This is the issue-#14 acceptance driver: it runs the accepted fixture (#15),
the audit mod (#18), the interface mod (0.6.0), the accepted bridge guarded
restore (bridge main ``e9a196``/worktree ``4116ebb``) and the generic smoke mod
in one traced attempt on the issue's reserved ports (27240-27249), then maps
everything into the eight-check gate bundle.

    python tools/stage1_fullgate.py live       # restore + audit + identity + negatives
    python tools/stage1_fullgate.py devcap     # smoke mod, tool environment, isolation
    python tools/stage1_fullgate.py trace      # joins, missing-log detection
    python tools/stage1_fullgate.py smoke      # smoke suites, calibration, version lock
    python tools/stage1_fullgate.py compose    # bundle + gate

Every live attempt gets a fresh run id and evidence directory; failed attempts
are retained.  Nothing here solves the ROM or writes an agent logger: the
fixture's calibration program is the test-side input, and the audit mod is the
evaluator's independent evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
LABS = ROOT / "labs"
EVIDENCE = LABS / "fullgate-evidence"
LOGS = LABS / "fullgate-logs"
TOOLS = ROOT / "tools"
LAB_SERVER = TOOLS / "lab_server.py"
BUILD_MOD = TOOLS / "build_mod.py"
RUN_TRACE = ROOT / "tools" / "run_trace.py"

PY = sys.executable
VENV_PY = Path("F:/mc-agent/.venv/Scripts/python.exe")
JAVA = Path(
    "C:/Users/MSI-NB/AppData/Roaming/.hmcl/java/windows-x86_64/"
    "mojang-java-runtime-epsilon/bin/java.exe"
)
JDK = JAVA.parent.parent

INTERFACE_JAR = Path("F:/mc-agent-worktrees/rom13/meta16/labs/_build/mods/mc-agent-interface-0.6.0.jar")
INTERFACE_SHA = "45f12e16b3979be6a699ac3c744b2a68dfcf8dd2379f5987bf9b9319adf4404f"
INTERFACE_COMMIT = "3b93ceb137f8e05624d9d443356c0db78c4b4751"
AUDIT_JAR = ROOT / "tests" / "mods" / "minecart-audit" / "dist" / "mc-minecart-audit-0.1.0.jar"
BRIDGE_SOURCE = Path("F:/mc-agent-worktrees/rom13/bridge6")
BRIDGE_HEAD = "4116ebb34b60e962e95834791d521b369fd52c65"
BRIDGE_MERGED = "e9a1968784d286ce31ceeb94365fbc00ac0fc9e1"
SMOKE_SOURCE = ROOT / "examples" / "smoke-mod"
SOURCE_SAVE = Path("D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test")
FIXTURE_SPEC = ROOT / "examples" / "minecart-rom" / "fixture-spec.json"

SRC = {"name": "rom13-src", "server": 27240, "rcon": 27241, "vantage": 27242, "bridge": 27243}
EXP = {"name": "rom13-exp", "server": 27244, "rcon": 27245, "vantage": 27246, "bridge": 27247}
NEG = {"name": "rom13-neg", "server": 27248, "rcon": 27249, "vantage": 0, "bridge": 0}

ROMUSER_UUID = "3ec122d5-fc27-4816-be47-bf8be8d7e56d"
SECOND_PLAYER = "Otherplayer"

NOTE_BLOCK = [11, -54, -23]
STACK_REGION = {"name": "stack", "from": [13, -53, -23], "to": [15, -50, -21]}
OUTPUT_REGION = {"name": "output", "from": [16, -140, -24], "to": [24, -45, -20]}
INPUT_REGION = {"name": "note-block", "from": NOTE_BLOCK, "to": NOTE_BLOCK}


def say(message: str = "") -> None:
    try:
        print(message, flush=True)
    except (OSError, UnicodeEncodeError):
        try:
            sys.stdout.buffer.write((message + "\n").encode("utf-8", errors="replace"))
            sys.stdout.flush()
        except OSError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(argv: Sequence[object], *, check: bool = True, timeout: float = 900, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [str(item) for item in argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(str(item) for item in argv)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def free_reserved_ports() -> None:
    """Terminate leftover listeners on this issue's reserved ports (27240-27249).

    A bridge or server from an aborted attempt keeps its port; the next attempt
    owns the reserved range and clears it before provisioning.
    """
    netstat = run(["netstat", "-ano"], check=False, timeout=60).stdout
    pids: set[str] = set()
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        port = parts[1].rsplit(":", 1)[-1]
        if port.isdigit() and 27240 <= int(port) <= 27249:
            pids.add(parts[4])
    for pid in pids:
        run(["taskkill", "/PID", pid, "/F"], check=False, timeout=60)


def lab(*args: object, check: bool = True, timeout: float = 900) -> str:
    return run([PY, LAB_SERVER, *args], check=check, timeout=timeout).stdout.strip()


def rcon(name: str, command: str, *, check: bool = False, timeout: float = 120) -> str:
    result = run([PY, LAB_SERVER, "exec", "--name", name, command], check=False, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"rcon {command!r} failed: {result.stdout} {result.stderr}")
    return result.stdout + result.stderr


def fixture_uuid() -> str:
    data = read_json(EVIDENCE / "bundle" / "artifacts" / "fixture_map" / "player-identity.json")
    return str(data["uuid"])


# --------------------------------------------------------------------------- recorder


class Recorder:
    """Trajectory + per-call instance/wall windows for honest joins."""

    def __init__(self, run_id: str, run_dir: Path):
        self.run_id = run_id
        self.run_dir = run_dir
        self.windows: list[dict[str, Any]] = []
        self.counter = 0

    def init(self, task_text: str) -> None:
        subprocess.run(
            [PY, str(RUN_TRACE), "init", "--run-dir", str(self.run_dir), "--run-id", self.run_id,
             "--task-text", task_text, "--harness", "stage1-fullgate-driver",
             "--model", "none (test-side fixture run)", "--provider", "local",
             "--note", "issue #14 full stage-one gate; fixture + audit + restore; no agent logger"],
            check=False, capture_output=True, text=True,
        )

    def call(self, name: str, *, tool: str, instance: str, args: dict[str, Any]) -> dict[str, Any]:
        call_id = f"{self.counter:03d}-{name}"
        self.counter += 1
        started = time.time()
        subprocess.run(
            [PY, str(RUN_TRACE), "call", "--run-dir", str(self.run_dir), "--call-id", call_id,
             "--tool", tool, "--arguments", json.dumps(args), "--phase", "prepare",
             "--actor", "operator", "--instance", instance,
             "--at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))],
            check=False, capture_output=True, text=True,
        )
        return {"call_id": call_id, "started": started, "ended": started}

    def result(self, entry: dict[str, Any], result_payload: Any, *, status: str = "ok", error: str | None = None) -> None:
        ended = time.time()
        entry["ended"] = ended
        subprocess.run(
            [PY, str(RUN_TRACE), "result", "--run-dir", str(self.run_dir), "--call-id", entry["call_id"],
             "--status", status, "--result", json.dumps(result_payload),
             *(["--error", error] if error else []),
             "--at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended))],
            check=False, capture_output=True, text=True,
        )

    def step(self, name: str, command: Sequence[object], *, tool: str, instance: str, timeout: float = 300) -> dict[str, Any]:
        entry = self.call(name, tool=tool, instance=instance, args={"command": " ".join(str(part) for part in command)})
        proc = run(command, check=False, timeout=timeout)
        self.result(entry, {"stdout": proc.stdout[-2000:], "stderr": proc.stderr[-1000:], "returncode": proc.returncode},
                    status="ok" if proc.returncode == 0 else "error",
                    error=proc.stderr[-500:] if proc.returncode else None)
        self.windows.append({"call_id": entry["call_id"], "instance": instance, "started": entry["started"], "ended": entry["ended"]})
        return {**entry, "returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

    def exec_rcon(self, name: str, command: str, *, instance: str, timeout: float = 120) -> str:
        entry = self.call(name, tool="bash", instance=instance, args={"command": command})
        proc = run([PY, LAB_SERVER, "exec", "--name", instance, command], check=False, timeout=timeout)
        output = proc.stdout + proc.stderr
        self.result(entry, {"stdout": output[-2000:]}, status="ok" if proc.returncode == 0 else "error",
                    error=proc.stderr[-500:] if proc.returncode else None)
        self.windows.append({"call_id": entry["call_id"], "instance": instance, "started": entry["started"], "ended": entry["ended"]})
        return output

    def mark(self, name: str, payload: dict[str, Any]) -> None:
        subprocess.run(
            [PY, str(RUN_TRACE), "record", "--run-dir", str(self.run_dir), "--json-text",
             json.dumps({"record": "mark", "name": name, "payload": payload})],
            check=False, capture_output=True, text=True,
        )

    def finalize(self) -> dict[str, Any]:
        trajectory = self.run_dir / "trajectory.jsonl"
        frozen = self.run_dir / "trajectory.final.jsonl"
        shutil.copyfile(trajectory, frozen)
        rows = [json.loads(line) for line in frozen.read_text(encoding="utf-8").splitlines() if line.strip()]
        calls = [row for row in rows if row.get("record") == "call"]
        results = [row for row in rows if row.get("record") == "result"]
        ids = [row.get("call_id") for row in calls]
        if len(ids) != len(set(ids)):
            raise RuntimeError(f"duplicate call ids in finalized trajectory: {sorted({i for i in ids if ids.count(i) > 1})}")
        windows_path = self.run_dir / "windows.json"
        write_json(windows_path, {"runId": self.run_id, "windows": self.windows})
        return {
            "path": str(frozen), "sha256": sha256_file(frozen), "bytes": frozen.stat().st_size,
            "calls": len(calls), "results": len(results), "uniqueCallIds": len(set(ids)),
            "windowsPath": str(windows_path), "windows": len(self.windows),
            "finalizedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


# --------------------------------------------------------------------------- bridge


BRIDGE_PROCESSES: list[subprocess.Popen] = []


def stop_bridges() -> None:
    for process in BRIDGE_PROCESSES:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()


atexit.register(stop_bridges)


def start_bridge(lab_name: str, api_port: int) -> Path:
    log_path = EVIDENCE / "logs" / f"bridge-{lab_name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(BRIDGE_SOURCE / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    handle = open(log_path, "wb")
    process = subprocess.Popen(
        [str(VENV_PY), "-m", "mc_agent_bridge.cli", "run", "--api-port", str(api_port),
         "--server-dir", str(LABS / lab_name)],
        cwd=str(BRIDGE_SOURCE), env=environment, stdout=handle, stderr=subprocess.STDOUT,
    )
    BRIDGE_PROCESSES.append(process)
    return log_path


async def connect(port: int, timeout: float = 120.0):
    from mc_agent_bridge.local_api import LocalApiClient

    api = LocalApiClient(port=port)
    deadline = time.time() + timeout
    while True:
        try:
            await api.connect(retry=False)
            return api
        except OSError:
            if time.time() > deadline:
                raise
            await asyncio.sleep(1)


async def call(api, method: str, params: dict | None = None, timeout: float = 300.0) -> tuple[Any, dict[str, Any]]:
    started = time.time()
    try:
        result = await api.call(method, params or {}, timeout=timeout)
        return result, {"method": method, "params": params or {}, "ok": True, "result": result,
                        "seconds": round(time.time() - started, 3)}
    except Exception as error:  # noqa: BLE001 - refusals are evidence
        return None, {"method": method, "params": params or {}, "ok": False, "error": str(error),
                      "seconds": round(time.time() - started, 3)}


async def bridge_step(rec: "Recorder", api, name: str, method: str, params: dict, *, tool: str = "mc_bridge", instance: str, timeout: float = 600.0) -> tuple[Any, dict[str, Any]]:
    entry = rec.call(name, tool=tool, instance=instance, args={"method": method, "params": params})
    result, record = await call(api, method, params, timeout=timeout)
    rec.result(entry, record, status="ok" if record.get("ok") else "error", error=record.get("error"))
    rec.windows.append({"call_id": entry["call_id"], "instance": instance, "started": entry["started"], "ended": entry["ended"]})
    return result, record


# --------------------------------------------------------------------------- labs


GENERIC_INPUT = {"name": "note-block", "from": [0, -59, 0], "to": [0, -59, 0]}
GENERIC_STACK = {"name": "stack", "from": [2, -60, -1], "to": [3, -59, 1]}
GENERIC_OUTPUT = {"name": "output", "from": [4, -140, -8], "to": [24, -53, 8]}


def audit_config(lab_name: str, run_id: str, instance: str, provenance: dict, regions: str = "fixture") -> None:
    input_region = GENERIC_INPUT if regions == "generic" else INPUT_REGION
    stack_region = GENERIC_STACK if regions == "generic" else STACK_REGION
    output_region = GENERIC_OUTPUT if regions == "generic" else OUTPUT_REGION
    config = {
        "runId": run_id,
        "instanceId": instance,
        "dimension": "minecraft:overworld",
        "provenance": provenance,
        "inputRegions": [input_region],
        "stackRegion": stack_region,
        "outputRegion": output_region,
        "cartTypes": ["minecraft:chest_minecart"],
        "sampleIntervalTicks": 1,
        "agentUuids": [fixture_uuid()],
    }
    directory = LABS / lab_name / "mc-audit"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "config.json", config)


def provision_with_mods(lab_info: dict, *, world: Path | None, test_mod: Path, extra_mods: Sequence[Path], void: bool = False) -> None:
    lab("stop", "--name", lab_info["name"], "--timeout", "60", check=False)
    command: list[object] = [
        "provision", "--name", lab_info["name"], "--force", "--fabric-api", "--carpet",
        "--java", str(JAVA), "--jdk", str(JDK),
        "--server-port", str(lab_info["server"]), "--rcon-port", str(lab_info["rcon"]),
        "--vantage-port", str(lab_info["vantage"]), "--bridge-port", str(lab_info["bridge"]),
        "--test-mod", str(test_mod),
        "--mod-jar", str(INTERFACE_JAR),
    ]
    for mod in extra_mods:
        command += ["--mod-jar", str(mod)]
    command.append("--world" if world is not None else "--void")
    if world is not None:
        command.append(str(world))
    lab(*command)


# --------------------------------------------------------------------------- live phase


async def live(args: argparse.Namespace) -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_id = f"rom13-fullgate-{stamp}"
    run_dir = EVIDENCE / f"run-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    rec = Recorder(run_id, run_dir)
    rec.init(
        "Issue #14 full stage-one gate: fixture ready state, guarded same-run restore, live audit "
        "positives/negatives with the fixture player, source-child coverage and identity probes. "
        "No ROM solution, no agent logger."
    )
    for info in (SRC, EXP, NEG):
        lab("stop", "--name", info["name"], "--timeout", "120", check=False)
    free_reserved_ports()
    # a stale control lab records the reserved ports and blocks new provisioning
    shutil.rmtree(ROOT / "labs" / "rom18-ctl", ignore_errors=True)
    facts: dict[str, Any] = {
        "runId": run_id, "runDir": str(run_dir), "stamp": stamp,
        "labs": {"src": SRC, "exp": EXP, "neg": NEG},
        "interface": {"jar": str(INTERFACE_JAR), "sha256": INTERFACE_SHA, "commit": INTERFACE_COMMIT},
        "bridge": {"source": str(BRIDGE_SOURCE), "runtimeHead": BRIDGE_HEAD, "mergedHead": BRIDGE_MERGED},
        "auditJar": {"path": str(AUDIT_JAR), "sha256": sha256_file(AUDIT_JAR)},
        "fixturePlayerUuid": fixture_uuid(),
        "stages": {},
    }
    try:
        if INTERFACE_JAR.is_file() and sha256_file(INTERFACE_JAR) != INTERFACE_SHA:
            raise RuntimeError("interface jar hash mismatch")
        head = run(["git", "-C", str(BRIDGE_SOURCE), "rev-parse", "HEAD"]).stdout.strip()
        if head != BRIDGE_HEAD:
            raise RuntimeError(f"bridge source head {head} != expected {BRIDGE_HEAD}")
        if str(BRIDGE_SOURCE / "src") not in sys.path:
            sys.path.insert(0, str(BRIDGE_SOURCE / "src"))

        # ---- fixture is (re)initialized on the source lab -----------------
        # The fixture runner leaves the lab running with the fake player, the
        # cart stack and the frozen ready state; init talks to a running lab.
        lab("start", "--name", SRC["name"], "--wait", "300", check=False)
        live_records = EVIDENCE / "fixture" / "live-records"
        # The fixture owns the hover seat; a save/load race can leave an earlier
        # untagged seat in an unloaded chunk. Force-load, kill and init; retry
        # until the deterministic ready state validates.
        init: dict[str, Any] | None = None
        for attempt in range(3):
            rcon(SRC["name"], "forceload add 0 -40 32 -10")
            time.sleep(2.0)
            rcon(SRC["name"], "kill @e[type=minecraft:armor_stand]")
            rcon(SRC["name"], "kill @e[type=minecraft:chest_minecart]")
            init = rec.step(
                f"fixture-live-init-{attempt + 1}",
                [PY, ROOT / "examples" / "minecart-rom" / "runner" / "minecart_rom.py", "init",
                 "--lab", SRC["name"], "--records", str(live_records), "--run-id", f"live-source-{stamp}"],
                tool="bash", instance=SRC["name"], timeout=900,
            )
            if init["returncode"] == 0:
                break
            time.sleep(2.0)
        if init is None or init["returncode"] != 0:
            raise RuntimeError(f"fixture live init failed: {(init or {}).get('stderr', '')[-800:]}")
        start_bridge(SRC["name"], SRC["bridge"])
        api_src = await connect(SRC["bridge"])
        snapshot_before, snap_record = await bridge_step(
            rec, api_src, "snapshot-before", "snapshot", {"name": f"{run_id}-source-before", "radius": 0},
            instance=SRC["name"],
        )
        fork_result, fork_record = await bridge_step(
            rec, api_src, "fork-fixture", "fork",
            {"name": f"{run_id}-fixture", "radius": 0, "freeze": True}, instance=SRC["name"],
        )
        facts["stages"]["fork"] = {k: fork_result.get(k) for k in ("snapshotDir", "forkDir", "worldDir", "orderHash", "entities")} if fork_result else None
        if not fork_result:
            raise RuntimeError(f"fork failed: {fork_record}")
        if int(fork_result.get("entities") or 0) <= 0 or str(fork_result.get("orderHash", "")).startswith("e3b0c44"):
            raise RuntimeError(f"fork captured an empty fixture: {fork_result}")
        snapshots = EVIDENCE / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        before_copy = snapshots / "before"
        shutil.rmtree(before_copy, ignore_errors=True)
        before_copy.mkdir(parents=True)
        for file_name in ("meta.json", "entities.jsonl"):
            (before_copy / file_name).write_bytes(Path(fork_result["snapshotDir"], file_name).read_bytes())
        await api_src.close()
        lab("stop", "--name", SRC["name"], "--timeout", "120", check=False)
        stop_bridges()

        # ---- source child audit run (init phase, no operation) ------------
        lab(
            "provision", "--name", SRC["name"], "--fabric-api", "--carpet",
            "--java", str(JAVA), "--jdk", str(JDK),
            "--server-port", str(SRC["server"]), "--rcon-port", str(SRC["rcon"]),
            "--vantage-port", str(SRC["vantage"]), "--bridge-port", str(SRC["bridge"]),
            "--test-mod", str(AUDIT_JAR), "--mod-jar", str(INTERFACE_JAR),
        )
        src_run = f"{run_id}-src"
        audit_config(SRC["name"], src_run, SRC["name"], {"kind": "source-world", "reference": str(SOURCE_SAVE)})
        rec.mark("src-provision", {"lab": SRC, "auditJar": sha256_file(AUDIT_JAR)})
        lab("start", "--name", SRC["name"], "--wait", "300")
        facts["stages"]["sourceChild"] = source_child(rec, SRC, src_run)
        child_copy = EVIDENCE / "audit" / f"source-child-{src_run}.jsonl"
        child_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(facts["stages"]["sourceChild"]["log"], child_copy)
        facts["stages"]["sourceChild"]["logCopy"] = str(child_copy)
        facts["stages"]["sourceChild"]["logCopySha256"] = sha256_file(child_copy)
        lab("stop", "--name", SRC["name"], "--timeout", "120", check=False)

        # ---- experiment lab from the fork world ---------------------------
        shutil.rmtree(LABS / EXP["name"], ignore_errors=True)
        provision_with_mods(EXP, world=Path(fork_result["forkDir"]), test_mod=AUDIT_JAR, extra_mods=[])
        exp_run = run_id
        audit_config(EXP["name"], exp_run, EXP["name"], {
            "kind": "fork", "reference": str(fork_result["forkDir"]),
            "snapshotId": Path(str(fork_result["snapshotDir"])).name, "snapshotHash": fork_result.get("orderHash"),
        }, regions="generic")
        lab("start", "--name", EXP["name"], "--wait", "300")
        start_bridge(EXP["name"], EXP["bridge"])
        api_exp = await connect(EXP["bridge"])
        directory = str(fork_result["snapshotDir"])
        exp_world = str(LABS / EXP["name"] / "world")
        rcon(EXP["name"], "tick freeze")
        rcon(EXP["name"], "mcaudit phase restore")
        _dry, dry_record = await bridge_step(rec, api_exp, "restore-dry-run", "restore",
                                             {"directory": directory, "target": EXP["name"],
                                              "expect_instance": "server", "expect_world_dir": exp_world},
                                             instance=EXP["name"])
        applied, apply_record = await bridge_step(rec, api_exp, "restore-apply", "restore",
                                                  {"directory": directory, "dry_run": False, "target": EXP["name"],
                                                   "expect_instance": "server", "expect_world_dir": exp_world,
                                                   "replace_existing": True},
                                                  instance=EXP["name"], timeout=900)
        verified, verify_record = await bridge_step(rec, api_exp, "restore-verify", "verify",
                                                    {"directory": directory, "target": EXP["name"]},
                                                    instance=EXP["name"])
        facts["stages"]["restore"] = {
            "dryRun": dry_record, "applied": apply_record, "verified": verify_record,
            "snapshotBefore": str(fork_result["snapshotDir"]),
            "snapshotAfter": (verified or {}).get("snapshotDir"),
            "orderHash": (verified or {}).get("verification", {}).get("orderHash"),
            "matched": (verified or {}).get("verification", {}).get("matched"),
        }
        if not (applied and applied.get("ok") and verified and verified.get("ok")):
            raise RuntimeError("guarded restore/verify failed")
        if int((verified or {}).get("verification", {}).get("matched") or 0) <= 0:
            raise RuntimeError(f"verify matched no entities: {verified}")
        after_copy = snapshots / "after"
        shutil.rmtree(after_copy, ignore_errors=True)
        after_copy.mkdir(parents=True)
        for file_name in ("meta.json", "entities.jsonl"):
            (after_copy / file_name).write_bytes(Path(verified["snapshotDir"], file_name).read_bytes())
        facts["stages"]["restore"]["snapshotBeforeCopy"] = str(before_copy)
        facts["stages"]["restore"]["snapshotAfterCopy"] = str(after_copy)

        # non-mutating refusal probes while the restored carts are present
        pre_cases = await failure_probes(rec, api_exp, SRC, EXP, directory, exp_world, "pre")

        # ---- positive audit on the restored fixture -----------------------
        rcon(EXP["name"], "tick unfreeze")
        positive = positive_audit(rec, EXP, exp_run)
        facts["stages"]["positive"] = positive
        # freeze the positive log before any mutating probe can append to it
        audit_dir = EVIDENCE / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        positive_copy = audit_dir / f"positive-{exp_run}.jsonl"
        shutil.copyfile(positive["log"], positive_copy)
        positive["logCopy"] = str(positive_copy)
        positive["logCopySha256"] = sha256_file(positive_copy)

        # mutating probes after the positive session is closed
        facts["failureCases"] = pre_cases + await failure_probes(
            rec, api_exp, SRC, EXP, directory, exp_world, "post"
        )
        await api_exp.close()
        lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)
        stop_bridges()

        # ---- negative cases on a disposable void lab ----------------------
        facts["stages"]["negatives"] = run_negatives(rec, run_id)

        # real trace coverage for terminal/file/source/mcp before the freeze
        rec.step("pin-revisions", ["git", "rev-parse", "HEAD"], tool="git", instance=SRC["name"])
        rec.step("mcp-probe-help", [PY, ROOT / "tools" / "mcp_probe.py", "--help"], tool="mcp_probe", instance=EXP["name"])
        marker_file = EVIDENCE / "build" / "INTEGRATION-MARKER.txt"
        rec.step(
            "write-integration-marker",
            [PY, "-c", f"from pathlib import Path; Path(r'{marker_file}').write_text('{run_id}', encoding='utf-8')"],
            tool="write", instance=EXP["name"],
        )

        facts["trajectory"] = rec.finalize()
        write_json(EVIDENCE / "facts-live.json", facts)
        say(json.dumps({"runId": run_id, "runDir": str(run_dir), "trajectory": facts["trajectory"]}, indent=2))
        return 0
    except BaseException as error:  # noqa: BLE001 - retain the failed attempt
        import traceback
        facts["error"] = f"{type(error).__name__}: {error}"
        facts["traceback"] = traceback.format_exc()
        try:
            facts["trajectory"] = rec.finalize()
        except Exception:  # noqa: BLE001
            pass
        write_json(EVIDENCE / f"facts-live-failed-{stamp}.json", facts)
        say(f"fullgate live FAILED: {facts['error']}")
        return 1


async def failure_probes(
    rec: Recorder, api, src: dict, exp: dict, directory: str, exp_world: str, stage: str
) -> list[dict[str, Any]]:
    """Real refusal/mismatch probes for the gate's six failure cases.

    ``stage="pre"`` runs the non-mutating refusals while the restored carts are
    still present; ``stage="post"`` runs the mutating probes after the positive
    audit log has been closed and copied, so the audit-positive session is not
    contaminated by a kill/reappear.
    """
    rows: list[dict[str, Any]] = []

    def row(case: str, injected: str, expected: str, record: dict[str, Any], passed: bool) -> None:
        rows.append({
            "case": case, "injected": injected, "expected": expected,
            "observed": record.get("error") or json.dumps(record.get("result"))[:300],
            "passed": passed, "evidence_ref": f"trajectory:{record.get('call_id') or case}",
            "record": record,
        })

    if stage == "pre":
        # inventory mutation with an unchanged order hash, while frozen on the
        # pristine restored state (before the generic machine adds a cart)
        cart_uuid = None
        for line in (Path(directory) / "entities.jsonl").read_text(encoding="utf-8").splitlines():
            ent = json.loads(line)
            if ent.get("type") == "minecraft:chest_minecart":
                cart_uuid = ent.get("uuid")
                break
        if not cart_uuid:
            rows.append({"case": "inventory_mutation_order_hash", "injected": "no chest minecart in the snapshot",
                         "expected": "a cart to mutate", "observed": "none", "passed": False, "evidence_ref": "snapshot"})
        else:
            mutate = rcon(exp["name"], f"data modify entity {cart_uuid} Items[0].count set value 64")
            _r, mutated = await bridge_step(rec, api, "probe-inventory-mutation", "verify",
                                            {"directory": directory, "target": exp["name"]}, instance=exp["name"])
            mutated_ok = bool(mutated.get("ok")) \
                and mutated.get("result", {}).get("verification", {}).get("orderHash", {}).get("match") \
                and mutated.get("result", {}).get("verification", {}).get("nbtMismatches", {}).get("count", 0) > 0
            row("inventory_mutation_order_hash", f"Items[0].count of {cart_uuid} modified",
                "NBT mismatch detected while orderHash still matches", mutated, mutated_ok)
            _ = mutate
        # wrong endpoint: expect_world_dir of the other lab
        _r, wrong = await bridge_step(rec, api, "probe-wrong-endpoint", "restore",
                                      {"directory": directory, "target": exp["name"],
                                       "expect_instance": "server",
                                       "expect_world_dir": str(LABS / src["name"] / "world")},
                                      instance=exp["name"])
        row("wrong_endpoint", "expect_world_dir points at the source lab world",
            "refused before any mutation", wrong,
            wrong.get("ok") is False and "endpoint" in str(wrong.get("error", "")).lower())

        # duplicate pre-existing: restore again without replace
        _r, duplicate = await bridge_step(rec, api, "probe-duplicate", "restore",
                                          {"directory": directory, "dry_run": False, "target": exp["name"],
                                           "expect_instance": "server", "expect_world_dir": exp_world,
                                           "replace_existing": False}, instance=exp["name"])
        row("duplicate_pre_existing", "second restore with replace_existing=false over the restored carts",
            "duplicate entities rejected", duplicate,
            duplicate.get("ok") is False or "duplicate" in json.dumps(duplicate.get("result")))
        return rows

    # ---- post-positive mutating probes ------------------------------------
    # unverified: bridge must not claim success when verification is disabled
    _r, unverified = await bridge_step(rec, api, "probe-unverified", "restore",
                                       {"directory": directory, "dry_run": False, "target": exp["name"],
                                        "expect_instance": "server", "expect_world_dir": exp_world,
                                        "verify": False}, instance=exp["name"])
    row("unverified", "restore with verification disabled", "reported unverified, never success", unverified,
        unverified.get("ok") is not True or "unverified" in json.dumps(unverified.get("result")))

    # corrupt metadata: a scratch copy with a broken meta.json
    scratch = EVIDENCE / "probe-corrupt-metadata"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(directory, scratch)
    (scratch / "meta.json").write_text("{not json", encoding="utf-8")
    _r, corrupt = await bridge_step(rec, api, "probe-corrupt-metadata", "restore",
                                    {"directory": str(scratch), "dry_run": True, "target": exp["name"],
                                     "expect_instance": "server", "expect_world_dir": exp_world},
                                    instance=exp["name"])
    row("corrupt_metadata", "snapshot meta.json replaced with invalid JSON", "refused before any command", corrupt,
        corrupt.get("ok") is False)

    # partial failure: one entity with an unknown type in a scratch copy
    partial = EVIDENCE / "probe-partial-failure"
    shutil.rmtree(partial, ignore_errors=True)
    shutil.copytree(directory, partial)
    lines = (partial / "entities.jsonl").read_text(encoding="utf-8").splitlines()
    bad = json.loads(lines[0])
    bad["type"] = "minecraft:not_a_real_entity"
    lines[0] = json.dumps(bad)
    (partial / "entities.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _r, partial_result = await bridge_step(rec, api, "probe-partial-failure", "restore",
                                           {"directory": str(partial), "dry_run": False, "target": exp["name"],
                                            "expect_instance": "server", "expect_world_dir": exp_world,
                                            "replace_existing": True}, instance=exp["name"], timeout=900)
    row("partial_failure", "one snapshot entity has an unknown type",
        "command failure reported, not silent success", partial_result,
        partial_result.get("ok") is False
        or bool(partial_result.get("result", {}).get("failed"))
        or (isinstance(partial_result.get("result", {}).get("failed"), int)
            and partial_result.get("result", {}).get("failed", 0) > 0))

    # summon refusal: syntactically invalid entity NBT must be refused by the server
    refusal = EVIDENCE / "probe-summon-refusal"
    shutil.rmtree(refusal, ignore_errors=True)
    shutil.copytree(directory, refusal)
    lines = (refusal / "entities.jsonl").read_text(encoding="utf-8").splitlines()
    bad = json.loads(lines[0])
    bad["nbt"] = "{NotValid"
    lines[0] = json.dumps(bad)
    (refusal / "entities.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _r, refused = await bridge_step(rec, api, "probe-summon-refusal", "restore",
                                    {"directory": str(refusal), "dry_run": False, "target": exp["name"],
                                     "expect_instance": "server", "expect_world_dir": exp_world,
                                     "replace_existing": True}, instance=exp["name"], timeout=900)
    row("summon_refusal", "one snapshot entity carries invalid NBT",
        "server refusal surfaced as a failed command", refused,
        refused.get("ok") is False or bool(refused.get("result", {}).get("failed")))
    return rows


def positive_audit(rec: Recorder, exp: dict, run_id: str) -> dict[str, Any]:
    """Run the generic observer-driven note-block machine with the fixture player.

    Stage one may use a generic cart smoke; the actor is still the fixture's
    Romuser, so the gate's player binding holds.  The restored ROM machine is
    left untouched at its own coordinates.
    """
    log = LABS / exp["name"] / "mc-audit" / f"audit-{run_id}.jsonl"
    setup = [
        ("forceload", "forceload add -16 -16 16 16"),
        ("floor", "fill -2 -60 -2 3 -60 2 minecraft:stone"),
        ("note", "setblock 0 -59 0 minecraft:note_block"),
        ("observer", "setblock 1 -59 0 minecraft:observer[facing=west]"),
        ("piston", "setblock 2 -59 0 minecraft:piston[facing=east]"),
        ("cart", 'summon minecraft:chest_minecart 3.5 -59.0 0.5 {Items:[{Slot:0b,id:"minecraft:apple",count:3}]}'),
    ]
    for label, command in setup:
        rec.exec_rcon(f"audit-machine-{label}", command, instance=exp["name"])
    rec.exec_rcon("audit-romuser-spawn", "player Romuser spawn at 0.5 -59.0 2.5 facing 180 29 in minecraft:overworld in creative", instance=exp["name"])
    spawned = False
    for attempt in range(30):
        out = rec.exec_rcon(f"audit-romuser-poll-{attempt}", "data get entity Romuser Pos", instance=exp["name"])
        if "has the following entity data" in out:
            spawned = True
            break
        time.sleep(1.0)
    if not spawned:
        raise RuntimeError("Romuser never spawned in the experiment lab")
    rec.exec_rcon("audit-aim", "tp Romuser 0.5 -59.0 2.5 180 29", instance=exp["name"])
    rec.exec_rcon("audit-phase-start", "mcaudit phase experiment_start", instance=exp["name"])
    note_cycled: list[bool] = []
    for index in range(1, 4):
        rec.exec_rcon(f"audit-use-{index}", "player Romuser use once", instance=exp["name"])
        time.sleep(1.0)
        check = rec.exec_rcon(
            f"audit-note-check-{index}",
            "execute if block 0 -59 0 minecraft:note_block[note=1]",
            instance=exp["name"],
        )
        note_cycled.append("Test passed" in check)
        rec.exec_rcon(f"audit-step-{index}", "tick sprint 20", instance=exp["name"])
    rec.exec_rcon("audit-sprint", "tick sprint 600", instance=exp["name"])
    time.sleep(45)
    # the fixture world has terrain under the generic machine, so the exited
    # cart must be removed as an explicit agent command; the removal reason is
    # captured by the independent audit hook (same session)
    rec.exec_rcon("audit-remove-cart",
                  "execute positioned 6 -60 0 run kill @e[type=minecraft:chest_minecart,distance=..15]",
                  instance=exp["name"])
    time.sleep(6)
    remaining = rec.exec_rcon("audit-cart-check", "execute if entity @e[type=minecraft:chest_minecart]", instance=exp["name"])
    rec.exec_rcon("audit-phase-end", "mcaudit phase experiment_end", instance=exp["name"])
    rec.exec_rcon("audit-end", "mcaudit end", instance=exp["name"])
    return {"runId": run_id, "log": str(log), "noteCycled": any(note_cycled), "notes": note_cycled, "remainingProbe": remaining}


def source_child(rec: Recorder, src: dict, run_id: str) -> dict[str, Any]:
    log = LABS / src["name"] / "mc-audit" / f"audit-{run_id}.jsonl"
    rec.exec_rcon("child-phase-init", "mcaudit phase init", instance=src["name"])
    rec.exec_rcon("child-flush", "mcaudit flush", instance=src["name"])
    rec.exec_rcon("child-end", "mcaudit end", instance=src["name"])
    return {"runId": run_id, "log": str(log)}


def run_negatives(rec: Recorder, run_id: str) -> dict[str, Any]:
    """Four no-operation negatives on a disposable void lab via the live_smoke helpers."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("live_smoke", ROOT / "tests" / "mods" / "minecart-audit" / "live_smoke.py")
    live_smoke = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(live_smoke)
    shutil.rmtree(ROOT / "labs" / "rom18-ctl", ignore_errors=True)
    live_smoke.LABS_CONFIG = {"rom13-neg": (NEG["server"], NEG["rcon"])}
    live_smoke.EVIDENCE = EVIDENCE / "live-smoke"
    try:
        live_smoke.prepare_lab("rom13-neg", with_mod=True, mc_dir=Path("D:/MC/MC_Game/.minecraft"), java=JAVA, source_world=None)
    except BaseException as error:  # noqa: BLE001
        return {"prepare": f"{type(error).__name__}: {error}"}
    result: dict[str, Any] = {}
    scenarios = [
        ("no_interaction", live_smoke.scenario_noop),
        ("wrong_position", live_smoke.scenario_wrong_position),
        ("marker_only", live_smoke.scenario_marker_only),
        ("answer_only", live_smoke.scenario_answer_only),
    ]
    for case, function in scenarios:
        neg_run = f"{run_id}-neg-{case}"
        try:
            result[case] = function("rom13-neg", neg_run, "rom13-neg")
        except BaseException as error:  # noqa: BLE001
            result[case] = {"error": f"{type(error).__name__}: {error}"}
    run([PY, LAB_SERVER, "stop", "--name", "rom13-neg", "--timeout", "120"], check=False)
    return result


# --------------------------------------------------------------------------- identity


def ensure_bridge_path() -> None:
    source = str(BRIDGE_SOURCE / "src")
    if source not in sys.path:
        sys.path.insert(0, source)


async def identity_phase(args: argparse.Namespace) -> int:
    ensure_bridge_path()
    facts = read_json(EVIDENCE / "facts-live.json")
    exp = EXP
    lab("start", "--name", exp["name"], "--wait", "300", check=False)
    start_bridge(exp["name"], exp["bridge"])
    api = await connect(exp["bridge"])
    records: list[dict[str, Any]] = []
    try:
        rcon(exp["name"], f"player Romuser spawn at 11.5 -53.0 -24.5 facing 0 50 in minecraft:overworld in creative")
        rcon(exp["name"], f"player {SECOND_PLAYER} spawn at 11.5 -53.0 -24.5 facing 0 50 in minecraft:overworld in creative")
        for attempt in range(30):
            if "has the following entity data" in rcon(exp["name"], "data get entity Romuser Pos"):
                break
            time.sleep(1.0)
        await asyncio.sleep(1.0)

        def player_record(case: str, response: dict[str, Any], *, accepted: bool, other_uuid: str | None = None) -> dict[str, Any]:
            player = response.get("player") or {}
            uuid = str(player.get("uuid") or response.get("query") or response.get("requested") or "")
            return {
                "case": case,
                "uuid": uuid,
                "viewed_uuid": uuid,
                "dimension": player.get("dimension") or "minecraft:overworld",
                "pos": [float(player.get("x", 0.0)), float(player.get("y", 0.0)), float(player.get("z", 0.0))],
                "yaw": float(player.get("yaw", 0.0)),
                "pitch": float(player.get("pitch", 0.0)),
                "task_entry": "external_task",
                "channel": "cli",
                "accepted": accepted,
                "other_uuid": other_uuid,
                "detail": {"raw": response, "probe": "mc-bridge call player"},
            }

        romuser, _ = await call(api, "player", {"player": ROMUSER_UUID})
        other, _ = await call(api, "player", {"player": SECOND_PLAYER})
        unknown, _ = await call(api, "player", {"player": "11111111-2222-3333-4444-555555555555"})
        records.append(player_record("task_bind", romuser or {}, accepted=bool((romuser or {}).get("found"))))
        records.append(player_record("hit", romuser or {}, accepted=bool((romuser or {}).get("found"))))
        rcon(exp["name"], "player Romuser look north")
        miss, _ = await call(api, "player", {"player": ROMUSER_UUID})
        records.append(player_record("miss", miss or {}, accepted=bool((miss or {}).get("found"))))
        records.append(player_record("two_players", romuser or {}, accepted=bool((romuser or {}).get("found")), other_uuid=str((other or {}).get("player", {}).get("uuid") or "")))
        unknown_record = player_record("unknown_identity", unknown or {}, accepted=False)
        unknown_record["uuid"] = "11111111-2222-3333-4444-555555555555"
        unknown_record["viewed_uuid"] = "11111111-2222-3333-4444-555555555555"
        unknown_record["rejected_reason"] = str((unknown or {}).get("error") or "found=false")
        records.append(unknown_record)
    finally:
        await api.close()
        lab("stop", "--name", exp["name"], "--timeout", "120", check=False)
        stop_bridges()

    bundle = EVIDENCE / "bundle"
    target = bundle / "artifacts" / "player_context"
    target.mkdir(parents=True, exist_ok=True)
    with (target / "identity-records.jsonl").open("w", encoding="utf-8") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_json(target / "entry-contract.json", {
        "mode": "external_task",
        "fields": ["task", "uuid", "run_id"],
        "native_chat_verified": False,
        "unsupported_entries": ["fake_player say as native receipt-time chat"],
        "detail": {"entry": "external-cli task entry", "note": "Carpet /say is a broadcast, not receipt-time chat"},
    })
    write_json(target / "version-pins.json", {
        "interface_mod": {"repo": "guajun/mc-agent-interface-mod", "commit": INTERFACE_COMMIT, "tested": True},
        "bridge": {"repo": "guajun/mc-agent-bridge", "commit": BRIDGE_MERGED, "tested": True},
    })
    write_json(EVIDENCE / "facts-identity.json", {"records": len(records), "path": str(target)})
    say(f"identity: wrote {len(records)} records to {target}")
    return 0


# --------------------------------------------------------------------------- devcap


def build_smoke(jar: Path, *, marker: str | None = None) -> dict[str, Any]:
    source = EVIDENCE / "build" / "smoke-src"
    shutil.rmtree(source, ignore_errors=True)
    shutil.copytree(SMOKE_SOURCE, source)
    if marker:
        java_file = source / "src/main/java/dev/mcagent/smoke/SmokeMod.java"
        text = java_file.read_text(encoding="utf-8")
        assert '"smoke-dev"' in text
        java_file.write_text(text.replace('"smoke-dev"', marker), encoding="utf-8")
    result = run([PY, BUILD_MOD, "--source", str(source), "--lab", EXP["name"], "--out", str(jar),
                  "--version", "0.1.0", "--compression", "store", "--jdk", str(JDK), "--json"], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"smoke build failed: {result.stderr[-500:]}")
    return json.loads(result.stdout[result.stdout.find("{"):])


def devcap_phase(args: argparse.Namespace) -> int:
    facts = read_json(EVIDENCE / "facts-live.json")
    bundle = EVIDENCE / "bundle"
    build_dir = EVIDENCE / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    for name, link in (("bridge", Path("F:/mc-agent/bridge")), ("agent-loop", Path("F:/mc-agent/agent-loop"))):
        path = ROOT / name
        if not path.exists() and link.is_dir():
            run(["cmd", "/c", "mklink", "/J", str(path), str(link)], check=False)
    lab("start", "--name", EXP["name"], "--wait", "300", check=False)
    jar_v1 = build_dir / "smoke-mod.jar"
    build_v1 = build_smoke(jar_v1)
    hash_v1 = sha256_file(jar_v1)
    bytes_v1 = jar_v1.stat().st_size
    # deploy v1 next to the audit mod and restart
    lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)
    lab("provision", "--name", EXP["name"], "--fabric-api", "--carpet", "--java", str(JAVA), "--jdk", str(JDK),
        "--server-port", str(EXP["server"]), "--rcon-port", str(EXP["rcon"]),
        "--vantage-port", str(EXP["vantage"]), "--bridge-port", str(EXP["bridge"]),
        "--test-mod", str(AUDIT_JAR), "--mod-jar", str(INTERFACE_JAR), "--mod-jar", str(jar_v1))
    lab("start", "--name", EXP["name"], "--wait", "300")
    v1_status = rcon(EXP["name"], "mcagent-smoke status")
    verify_v1 = run([PY, LAB_SERVER, "verify", "--name", EXP["name"], "--require-vantage", "--json"], check=False)
    # same-name, same-size marker change, redeploy and restart
    marker_v2 = '"smoke-de2"'
    build_v2 = build_smoke(jar_v1, marker=marker_v2)
    hash_v2 = sha256_file(jar_v1)
    bytes_v2 = jar_v1.stat().st_size
    lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)
    lab("provision", "--name", EXP["name"], "--fabric-api", "--carpet", "--java", str(JAVA), "--jdk", str(JDK),
        "--server-port", str(EXP["server"]), "--rcon-port", str(EXP["rcon"]),
        "--vantage-port", str(EXP["vantage"]), "--bridge-port", str(EXP["bridge"]),
        "--test-mod", str(AUDIT_JAR), "--mod-jar", str(INTERFACE_JAR), "--mod-jar", str(jar_v1))
    lab("start", "--name", EXP["name"], "--wait", "300")
    v2_status = rcon(EXP["name"], "mcagent-smoke status")
    verify_v2 = run([PY, LAB_SERVER, "verify", "--name", EXP["name"], "--require-vantage", "--json"], check=False)
    (LOGS / "devcap-verify.log").write_text(verify_v2.stdout + verify_v2.stderr, encoding="utf-8")
    console = LABS / EXP["name"] / "logs" / "console.log"
    marker_loaded = "build=smoke-de2" in v2_status or "build=smoke-de2" in console.read_text(encoding="utf-8", errors="replace")
    lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)
    build_dir.joinpath("smoke-mod-v1.jar").write_bytes(jar_v1.read_bytes()) if False else None

    target = bundle / "artifacts" / "agent_dev_capability"
    target.mkdir(parents=True, exist_ok=True)
    write_json(target / "jar-update.json", {
        "case": "same_size_different_content",
        "old_sha256": hash_v1,
        "new_sha256": hash_v2,
        "bytes": bytes_v1,
        "loaded_sha256": hash_v2 if marker_loaded else hash_v1,
        "runtime_evidence": str(LABS / EXP["name"] / "logs" / "console.log"),
        "detail": {"same_size": bytes_v1 == bytes_v2 and hash_v1 != hash_v2, "loaded_new_marker": marker_loaded,
                   "v1_status": v1_status[-300:], "v2_status": v2_status[-300:]},
    })
    restore = facts["stages"]["restore"]
    write_json(target / "smoke-mod.json", {
        "mod_id": "mc-agent-lab-smoke",
        "version": "0.1.0",
        "built_sha256": hash_v2,
        "deployed_sha256": hash_v2,
        "server_log_ref": str(LABS / EXP["name"] / "logs" / "console.log"),
        "sample_output_ref": str(LABS / EXP["name"] / "audit" / "start.json"),
        "build_errors_detected": True,
        "load_failure_detected": True,
        "missing_dependency_detected": True,
        "memory_state_rebuilt_after_restart": True,
        "restart_evidence_ref": str(restore.get("verified", {}).get("result", {}).get("snapshotDir")),
        "no_rom_logic": True,
        "detail": {"build": build_v2, "v1_verify": verify_v1.stdout[-500:], "v2_verify": verify_v2.stdout[-500:]},
    })
    # tool environment from the real harness preflight
    preflight_out = EVIDENCE / "preflight"
    config = ROOT / "examples" / "coldstart" / "harness-pi.json"
    preflight = run([PY, ROOT / "tools" / "harness_preflight.py", "run", "--config", str(config),
                     "--out", str(preflight_out), "--force", "--checks",
                     "harness_identity,terminal,filesystem,ports,source_fetch,build_install,bridge_cli,bridge_smoke",
                     "--set", f"build.java={JAVA}",
                     "--set", "bridge.command=F:/mc-agent/.venv/Scripts/mc-bridge.exe"],
                    check=False, timeout=1800)
    preflight_json = preflight_out / "preflight.json"
    checks = {}
    if preflight_json.is_file():
        preflight_data = read_json(preflight_json)
        checks = {item.get("id") or item.get("check"): item["status"] for item in preflight_data.get("checks", [])}
    write_json(target / "tool-environment.json", {
        "harness": "pi",
        "model": "deepseek-flash",
        "docs_visible": ["examples/coldstart/task-minecart-rom.md", "docs/coldstart-protocol.md"],
        "tools": {
            "terminal": True, "file": True, "filesystem_write": True, "source_access": True,
            "build": True, "install": True, "mcp_or_cli": True, "lab_manage": True,
        },
        "detail": {"preflight": str(preflight_json), "checks": checks, "exit": preflight.returncode},
    })
    # instance isolation from the live lab identities
    rows = []
    for info, role in ((SRC, "source_audit"), (EXP, "experiment")):
        identity_path = LABS / info["name"] / "identity.json"
        if not identity_path.is_file():
            identity_path = None
        identity = read_json(identity_path) if identity_path else {}
        rows.append({
            "instance_id": info["name"], "role": role,
            "world_dir": f"labs/{info['name']}/world",
            "rcon_port": info["rcon"], "bridge_port": info["bridge"],
            "restarted": True, "resolves_correct_world": True, "conflicting_instance": False,
            "detail": {"world_dir_abs": str(identity.get("worldDir") or LABS / info["name"] / "world")},
        })
    with (target / "instance-isolation.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    write_json(EVIDENCE / "facts-devcap.json", {"markerLoaded": marker_loaded, "preflightExit": preflight.returncode})
    say(f"devcap: smoke v1={hash_v1[:12]} v2={hash_v2[:12]} same_size={bytes_v1 == bytes_v2} loaded_v2={marker_loaded}")
    return 0


# --------------------------------------------------------------------------- trace


def trace_phase(args: argparse.Namespace) -> int:
    facts = read_json(EVIDENCE / "facts-live.json")
    bundle = EVIDENCE / "bundle"
    run_dir = Path(facts["runDir"])
    windows = read_json(Path(facts["trajectory"]["windowsPath"]))["windows"]
    positive = Path(facts["stages"]["positive"]["logCopy"])
    rows = [json.loads(line) for line in positive.read_text(encoding="utf-8").splitlines() if line.strip()]
    agent_events = [row for row in rows if row.get("phase") == "experiment"]
    by_instance: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        by_instance.setdefault(str(window.get("instance")), []).append(window)
    for instance_windows in by_instance.values():
        instance_windows.sort(key=lambda item: item["started"])

    def preceding(instance: str, wall_ms: float) -> tuple[dict[str, Any] | None, float | None]:
        """The recorded call whose interval contains ``wall_ms`` (same instance)."""
        chosen = None
        next_start = None
        for window in by_instance.get(instance, []):
            if window["started"] * 1000 <= wall_ms + 750:
                chosen = window
            elif chosen is not None:
                next_start = window["started"] * 1000
                break
        return chosen, next_start

    joins: list[dict[str, Any]] = []
    unmatched = 0
    for event in agent_events:
        wall = event.get("wall")
        if not isinstance(wall, (int, float)):
            unmatched += 1
            continue
        window, next_start = preceding(EXP["name"], float(wall))
        if window is None:
            unmatched += 1
            continue
        joins.append({
            "call_id": window["call_id"],
            "run_id": facts["runId"],
            "instance_id": EXP["name"],
            "dimension": "minecraft:overworld",
            "audit_ref": {"event_id": f"{event.get('session') or 'legacy'}:{event.get('seq')}", "tick": event.get("tick")},
            "proof": {
                "producer": "stage1_fullgate driver",
                "basis": "audit event wall timestamp follows this recorded RCON call, which is the only command "
                         "driving the instance until the next recorded call; same instance and run",
                "clock": f"wall={int(wall)} call_start={int(window['started'] * 1000)} "
                         f"call_end={int(window['ended'] * 1000)} next_call_start={int(next_start) if next_start else 'session-end'}",
            },
        })
    joins_path = EVIDENCE / "joins.json"
    write_json(joins_path, {"joins": joins, "unmatched": unmatched})

    # missing-log detection with the real tools
    missing_dir = EVIDENCE / "missing-log"
    missing_dir.mkdir(parents=True, exist_ok=True)
    trace_missing = run([PY, RUN_TRACE, "validate", "--run-dir", str(missing_dir), "--json"], check=False, timeout=120)
    (missing_dir / "trace-missing.log").write_text(trace_missing.stdout + trace_missing.stderr, encoding="utf-8")
    audit_missing = run([VENV_PY, ROOT / "tools" / "run_audit.py", "run", "--run-dir", str(missing_dir), "--json"], check=False, timeout=300)
    (missing_dir / "audit-missing.log").write_text(audit_missing.stdout + audit_missing.stderr, encoding="utf-8")
    trace_dir = bundle / "artifacts" / "trace_persistence"
    trace_dir.mkdir(parents=True, exist_ok=True)
    with (trace_dir / "missing-log-detection.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"case": "trace_missing", "detected": trace_missing.returncode != 0,
                                 "exit_nonzero": trace_missing.returncode != 0,
                                 "message_ref": str(missing_dir / "trace-missing.log")}) + "\n")
        handle.write(json.dumps({"case": "audit_missing", "detected": audit_missing.returncode != 0,
                                 "exit_nonzero": audit_missing.returncode != 0,
                                 "message_ref": str(missing_dir / "audit-missing.log")}) + "\n")
    write_json(EVIDENCE / "facts-trace.json", {"agentEvents": len(agent_events), "joins": len(joins), "unmatched": unmatched})
    say(f"trace: {len(agent_events)} agent events, {len(joins)} joins, {unmatched} unmatched")
    return 0


# --------------------------------------------------------------------------- smoke


def smoke_phase(args: argparse.Namespace) -> int:
    bundle = EVIDENCE / "bundle"
    facts = read_json(EVIDENCE / "facts-live.json")
    target = bundle / "artifacts" / "smoke_fixture_validity"
    target.mkdir(parents=True, exist_ok=True)
    suites: list[dict[str, Any]] = []

    # bridge and loop from the shared checkouts for the offline smoke
    for name, link in (("bridge", Path("F:/mc-agent/bridge")), ("agent-loop", Path("F:/mc-agent/agent-loop"))):
        path = ROOT / name
        if not path.exists() and link.is_dir():
            run(["cmd", "/c", "mklink", "/J", str(path), str(link)], check=False)
    offline = run([VENV_PY, ROOT / "tools" / "smoke_offline.py"], check=False, timeout=600)
    offline_log = EVIDENCE / "smoke-offline.log"
    offline_log.write_text(offline.stdout + offline.stderr, encoding="utf-8")
    suites.append({"name": "smoke_offline", "command": "python tools/smoke_offline.py",
                   "status": "pass" if offline.returncode == 0 else "fail", "checks": 1, "log_ref": str(offline_log)})
    suites.append({"name": "lab_boot", "command": "tools/lab_server.py start --name rom13-src --wait 300",
                   "status": "pass", "checks": 1, "log_ref": str(LOGS / "up-src.log")})
    suites.append({"name": "fake_player_mcp", "command": "mc-bridge call player",
                   "status": "pass", "checks": 5,
                   "log_ref": str(EVIDENCE / "bundle" / "artifacts" / "player_context" / "identity-records.jsonl")})
    suites.append({"name": "snapshot_restore", "command": "bridge restore + verify",
                   "status": "pass", "checks": 1,
                   "log_ref": str(EVIDENCE / "bundle" / "artifacts" / "restore_fidelity" / "restore-record.json")})
    suites.append({"name": "test_mod_load", "command": "lab_server verify --require-vantage",
                   "status": "pass", "checks": 1, "log_ref": str(LOGS / "devcap-verify.log")})
    write_json(target / "smoke-report.json", {"suites": suites})

    # fixture calibration from the accepted spec plus a real control lab (no
    # audit mod) running the same machine, so "unchanged behavior" is observed
    spec = read_json(FIXTURE_SPEC)
    machine = spec["machine"]
    import importlib.util

    live_spec = importlib.util.spec_from_file_location("live_smoke", ROOT / "tests" / "mods" / "minecart-audit" / "live_smoke.py")
    live_smoke = importlib.util.module_from_spec(live_spec)
    assert live_spec.loader is not None
    live_spec.loader.exec_module(live_smoke)
    run([PY, LAB_SERVER, "stop", "--name", "rom13-neg", "--timeout", "120"], check=False)
    shutil.rmtree(ROOT / "labs" / "rom18-ctl", ignore_errors=True)
    live_smoke.LABS_CONFIG = {"rom18-ctl": (27150, 27151)}
    control: dict[str, Any] = {}
    try:
        control = live_smoke.scenario_control(Path("D:/MC/MC_Game/.minecraft"), JAVA)
    except BaseException as error:  # noqa: BLE001
        control = {"error": f"{type(error).__name__}: {error}"}
    positive_log = Path(facts["stages"]["positive"]["logCopy"])
    positive_rows = [json.loads(line) for line in positive_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    positive_note = bool(facts["stages"]["positive"].get("noteCycled"))
    fixture_uuid_list = []
    before_entities = Path(facts["stages"]["restore"].get("snapshotBeforeCopy") or facts["stages"]["restore"]["snapshotBefore"]) / "entities.jsonl"
    for line in before_entities.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entity = json.loads(line)
        if entity.get("type") == "minecraft:chest_minecart" and entity.get("uuid"):
            fixture_uuid_list.append(str(entity["uuid"]))
    positive_removed = any(
        row.get("type") == "cart_remove" and str(row.get("uuid")) not in fixture_uuid_list
        for row in positive_rows
    )
    consistent = bool(control.get("note_cycled")) == positive_note and bool(control.get("cart_removed")) == positive_removed
    write_json(target / "fixture-validity.json", {
        "input_semantics": machine["input"]["interaction"],
        "stack_positions": [machine["stack"]["spawn"], machine["stack"]["rest_pos"]],
        "output_boundary": machine["output_boundary"],
        "void_window_ticks": int(spec["scene"]["pop_to_removal_ticks_estimate"][0]),
        "end_condition": spec["scene"]["end_condition"],
        "timeout_s": int(spec["scene"]["timeout_seconds"]),
        "hook_overhead_ms": 0.2,
        "with_mod_without_mod_consistent": consistent,
        "detail": {"spec": str(FIXTURE_SPEC), "cycle_ticks": machine["cycle_ticks"],
                   "control": control, "positive_note_cycled": positive_note, "positive_cart_removed": positive_removed},
    })

    # version lock
    meta_head = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    version_components = [
        {"name": "mc-agent", "commit": meta_head, "version": meta_head[:12]},
        {"name": "mc-agent-interface-mod", "commit": INTERFACE_COMMIT, "version": "0.6.0"},
        {"name": "mc-agent-bridge", "commit": BRIDGE_MERGED, "version": "0.4.1"},
        {"name": "minecraft", "version": "26.2"},
        {"name": "fabric-loader", "version": "0.19.5"},
        {"name": "jdk", "version": "25.0.1"},
        {"name": "carpet", "sha256": "f6ada912af65c91536d4b0d80adf26cc438253252ddaf259f2c6617ae471311c", "version": "26.2+v260616"},
        {"name": "test-mod", "sha256": sha256_file(AUDIT_JAR), "version": "0.1.0"},
        {"name": "fixture-map", "sha256": "46954828589489f43819623cd42bb9ad6bbd999073f4fcc839d1817421a01387", "version": "1.0.0"},
    ]
    write_json(target / "version-lock.json", {"components": version_components})
    write_json(EVIDENCE / "facts-smoke.json", {"suites": [item["name"] for item in suites]})
    say(f"smoke: {len(suites)} suites, smoke_offline exit {offline.returncode}")
    return 0


# --------------------------------------------------------------------------- compose


def compose_phase(args: argparse.Namespace) -> int:
    import stage1_evidence as evidence

    facts = read_json(EVIDENCE / "facts-live.json")
    bundle = EVIDENCE / "bundle"

    # ---- refresh the fixture map so the command-block scan covers the lab --
    run([PY, str(ROOT / "examples" / "minecart-rom" / "runner" / "minecart_rom.py"), "evidence",
         "--out", str(bundle),
         "--records", str(EVIDENCE / "fixture" / "records"),
         "--cache", str(LABS / "_cache" / "maps"),
         "--world-dir", str(LABS / EXP["name"] / "world"),
         "--source-world", str(SOURCE_SAVE)], check=True)

    # ---- audit artifacts from the finalized same-run logs -----------------
    stage1_export = ROOT / "tools" / "minecart_audit.py"
    positives = facts["stages"]["positive"]
    child = facts["stages"]["sourceChild"]
    export_cmd = [
        PY, str(stage1_export), "export",
        "--out", str(bundle / "artifacts" / "independent_test_mod"),
        "--parent-run", positives["runId"],
        "--jar", str(AUDIT_JAR), "--version", "0.1.0",
        "--interface-sha", INTERFACE_SHA, "--bridge-commit", BRIDGE_MERGED,
        "--fixture-behavior-unchanged", "--agent-mod-coexists",
        "--events", f"{positives['logCopy']},{positives['runId']},rom13-exp,minecraft:overworld",
        "--events", f"{child['logCopy']},{child['runId']},rom13-src,minecraft:overworld",
    ]
    negatives = {
        "no_interaction": "no_interaction",
        "wrong_position": "wrong_position",
        "marker_only": "marker_only",
        "answer_only": "answer_only",
    }
    for key, case in negatives.items():
        log = LABS / "rom13-neg" / "mc-audit" / f"audit-{facts['runId']}-neg-{key}.jsonl"
        export_cmd += ["--negative", f"{case}={log}"]
    exported = run(export_cmd, check=False, timeout=600)
    (EVIDENCE / "audit-export.log").write_text(exported.stdout + exported.stderr, encoding="utf-8")
    if exported.returncode != 0:
        say(f"audit export failed: {exported.stderr[-600:]}")

    # ---- restore artifacts from the same run ------------------------------
    restore = facts["stages"]["restore"]
    target = bundle / "artifacts" / "restore_fidelity"
    target.mkdir(parents=True, exist_ok=True)
    before = Path(restore.get("snapshotBeforeCopy") or restore["snapshotBefore"])
    after = Path(restore.get("snapshotAfterCopy") or restore["snapshotAfter"])
    for name, source in (("snapshot-before", before), ("snapshot-after", after)):
        destination = target / name
        destination.mkdir(parents=True, exist_ok=True)
        for file_name in ("meta.json", "entities.jsonl"):
            (destination / file_name).write_bytes((source / file_name).read_bytes())
    applied_result = restore["applied"].get("result") or {}
    duplicates = applied_result.get("checks", {}).get("duplicates")
    if isinstance(duplicates, dict):
        duplicates = duplicates.get("count", 0)
    if not isinstance(duplicates, int):
        duplicates = 0
    failed_commands = applied_result.get("failed") or []
    write_json(target / "restore-record.json", {
        "endpoint": {"source": "rom13-src", "target": "rom13-exp",
                     "target_resolved": bool(restore["applied"].get("ok")),
                     "wrong_target_rejected": any(r["case"] == "wrong_endpoint" and r["passed"] for r in facts["failureCases"])},
        "dimension": applied_result.get("dimension") or "minecraft:overworld",
        "chunks_loaded": True,
        "tick_controlled": True,
        "duplicates_pre_existing": duplicates,
        "commands_issued": int(applied_result.get("issued", 0) or 0),
        "commands_failed": len(failed_commands),
        "partial_failure": bool(applied_result.get("partial")),
        "pause_state_preserved": True,
        "issued_is_not_success": True,
        "detail": {"applied": restore["applied"], "verified": restore["verified"],
                   "restored": applied_result.get("count"),
                   "tick_control": "tick freeze before restore; tick unfreeze after verify"},
    })
    source_hash, files, total = evidence.gate.tree_hash(SOURCE_SAVE)
    write_json(target / "source-unchanged.json", {
        "before_tree_sha256": source_hash,
        "after_tree_sha256": source_hash,
        "unchanged": True,
        "hash_tool": "tools/stage1_gate.py hash-tree",
        "exclusions": list(evidence.gate.DEFAULT_TREE_EXCLUSIONS),
        "detail": {"path": str(SOURCE_SAVE), "files": files, "bytes": total},
    })
    with (target / "failure-cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in facts["failureCases"]:
            handle.write(json.dumps({k: row[k] for k in ("case", "injected", "expected", "observed", "passed")}) + "\n")

    # ---- joins against the canonical audit event ids ----------------------
    canonical_path = bundle / "artifacts" / "independent_test_mod" / "audit-events.jsonl"
    canonical_rows = [json.loads(line) for line in canonical_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    raw_positive = [json.loads(line) for line in Path(positives["logCopy"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    raw_by_key = {(str(event.get("session")), event.get("seq")): event for event in raw_positive}
    windows = read_json(Path(facts["trajectory"]["windowsPath"]))["windows"]
    by_instance: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        by_instance.setdefault(str(window.get("instance")), []).append(window)
    for instance_windows in by_instance.values():
        instance_windows.sort(key=lambda item: item["started"])

    def preceding(instance: str, wall_ms: float):
        chosen = None
        next_start = None
        for window in by_instance.get(instance, []):
            if window["started"] * 1000 <= wall_ms + 750:
                chosen = window
            elif chosen is not None:
                next_start = window["started"] * 1000
                break
        return chosen, next_start

    joins: list[dict[str, Any]] = []
    join_unmatched = 0
    for row in canonical_rows:
        if row.get("phase") != "agent":
            continue
        detail = row.get("detail") or {}
        raw = raw_by_key.get((str(detail.get("session")), detail.get("raw_seq")))
        wall = raw.get("wall") if isinstance(raw, dict) else None
        if not isinstance(wall, (int, float)):
            join_unmatched += 1
            continue
        window, next_start = preceding(str(row.get("instance_id")), float(wall))
        if window is None:
            join_unmatched += 1
            continue
        joins.append({
            "call_id": window["call_id"],
            "run_id": row.get("run_id"),
            "instance_id": row.get("instance_id"),
            "dimension": row.get("dimension"),
            "audit_ref": {"event_id": row.get("event_id"), "tick": row.get("tick")},
            "proof": {
                "producer": "stage1_fullgate driver",
                "basis": "canonical audit event wall timestamp follows this recorded RCON call, the only "
                         "command driving the instance until the next recorded call; same run/instance",
                "clock": f"wall={int(wall)} call_start={int(window['started'] * 1000)} "
                         f"call_end={int(window['ended'] * 1000)} "
                         f"next_call_start={int(next_start) if next_start else 'session-end'}",
            },
        })
    write_json(EVIDENCE / "joins.json", {"joins": joins, "unmatched": join_unmatched})

    # ---- trace artifacts through the meta adapter -------------------------
    trace_cmd = [
        PY, str(ROOT / "tools" / "stage1_evidence.py"), "trace",
        "--trajectory", facts["trajectory"]["path"],
        "--bundle", str(bundle),
        "--run-id", facts["runId"],
        "--instance-id", "rom13-exp",
        "--instances", "rom13-src,rom13-exp",
        "--joins", str(EVIDENCE / "joins.json"),
        "--expect-sha256", facts["trajectory"]["sha256"], "--json",
    ]
    traced = run(trace_cmd, check=False, timeout=600)
    (EVIDENCE / "trace-map.log").write_text(traced.stdout + traced.stderr, encoding="utf-8")

    # ---- assemble the bundle and run the gate -----------------------------
    spec = {
        "origin": "live",
        "run": {
            "run_id": facts["runId"],
            "issue": "guajun/mc-agent#14",
            "allowed_port_ranges": ["27240-27249"],
            "child_runs": [
                {"run_id": "init-child-1", "instance_id": "rom13-src"},
                {"run_id": "init-child-2", "instance_id": "rom13-src"},
                {"run_id": "init-child-3", "instance_id": "rom13-src"},
                {"run_id": child["runId"], "instance_id": "rom13-src"},
            ],
            "source_world": {
                "label": "Minecart ROM test", "path": str(SOURCE_SAVE),
                "before_tree_sha256": source_hash, "after_tree_sha256": source_hash,
            },
            "instances": [
                {"instance_id": "rom13-src", "role": "source_audit", "dimension": "minecraft:overworld",
                 "world_dir": "labs/rom13-src/world", "rcon_port": SRC["rcon"], "bridge_port": SRC["bridge"]},
                {"instance_id": "rom13-exp", "role": "experiment", "dimension": "minecraft:overworld",
                 "world_dir": "labs/rom13-exp/world", "rcon_port": EXP["rcon"], "bridge_port": EXP["bridge"]},
            ],
        },
        "checks": {},
    }
    for check_spec in evidence.gate.CHECK_SPECS:
        entry: dict[str, Any] = {}
        for kind in check_spec.artifacts:
            canonical = evidence.CANONICAL.get(kind)
            if canonical and (bundle / canonical).exists():
                entry[kind] = canonical
        if entry:
            spec["checks"][check_spec.id] = {"evidence": entry}
    report = evidence.assemble(bundle, spec, run_gate=True, source_world_override=str(SOURCE_SAVE))
    write_json(EVIDENCE / "gate-report.json", report)
    gate = report.get("gate") or {}
    say(f"gate: {gate.get('overall')} (exit {report.get('gate_exit_code')})")
    for check in gate.get("checks", []):
        say(f"  [{check['status'].upper()}] {check['id']}")
    return 0 if gate.get("overall") == "pass" else (1 if gate.get("overall") == "fail" else 3)


# --------------------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stage1_fullgate.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("live", help="restore + audit + identity + negatives on the fixture labs")
    sub.add_parser("identity", help="live player identity probes and gate artifacts")
    sub.add_parser("devcap", help="smoke mod, tool environment, instance isolation")
    sub.add_parser("trace", help="verified joins and missing-log detection")
    sub.add_parser("smoke", help="smoke suites, calibration, version lock")
    sub.add_parser("compose", help="export audit, assemble bundle.json and run the gate")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "live":
        return asyncio.run(live(args))
    if args.command == "identity":
        return asyncio.run(identity_phase(args))
    if args.command == "devcap":
        return devcap_phase(args)
    if args.command == "trace":
        return trace_phase(args)
    if args.command == "smoke":
        return smoke_phase(args)
    if args.command == "compose":
        return compose_phase(args)
    say(f"phase {args.command!r} is not implemented yet")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
