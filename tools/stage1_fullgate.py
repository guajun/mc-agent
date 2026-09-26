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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage1_gate as gate  # noqa: E402

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
#: The no-mod parity copy reuses the void lab's ports after the negatives stop.
CTL = {"name": "rom13-ctl", "server": 27150, "rcon": 27151, "vantage": 0, "bridge": 0}

#: The read-only source save baseline; a live run must observe it before and
#: after separately and both observations must equal this anchor.
SOURCE_BASELINE_SHA256 = "8cd54c86af9fa8d6b9ea33441fb21dac295cd2b5ddaa60327f5fb3a30255324a"

ROMUSER = "Romuser"
SEAT_POS = [11.5, -53.0, -24.5]
SEAT_FACING = [0.0, 50.0]
SEAT_TAG = "minecart-rom-fixture"
NOTE_BLOCK_POS = [11, -54, -23]
CALIBRATION_PRESSES = 1
CALIBRATION_SPRINT_TICKS = 220
#: Bounds the audit mod's own calibration: a popped cart is removed naturally
#: below the void within the fixture's 110..160 tick window plus margin.
EMISSION_MAX_TICK_DELTA = 40
REMOVAL_MAX_TICK_DELTA = 300

ROMUSER_UUID = "3ec122d5-fc27-4816-be47-bf8be8d7e56d"
SECOND_PLAYER = "Otherplayer"

NOTE_BLOCK = [11, -54, -23]
STACK_REGION = {"name": "stack", "from": [13, -53, -23], "to": [15, -50, -21]}
OUTPUT_REGION = {"name": "output", "from": [16, -140, -24], "to": [24, -45, -20]}
INPUT_REGION = {"name": "note-block", "from": NOTE_BLOCK, "to": NOTE_BLOCK}


def _uuid_from_nbt(value: Any) -> str:
    """Convert a serialized ``[I; a, b, c, d]`` UUID into the dashed form."""
    match = re.match(r"\[I;\s*(-?\d+),\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\]", str(value))
    if not match:
        return ""
    parts = [int(match.group(index)) & 0xFFFFFFFF for index in range(1, 5)]
    hexed = "".join(f"{part:08x}" for part in parts)
    return f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"


#: Source files whose bytes define the executable acceptance run.  Each phase
#: records the actual bytes hash and the Git blob hash separately, so a CRLF
#: checkout is visible as a distinct working-tree hash instead of a mismatch.
TRACKED_SOURCE_FILES = (
    "tools/stage1_fullgate.py",
    "tools/stage1_gate.py",
    "tools/stage1_evidence.py",
    "tools/minecart_audit.py",
    "tools/stage1_fullgate_summary.py",
    "tests/mods/minecart-audit/src/main/java/dev/mcagent/audit/AuditEngine.java",
    "tests/mods/minecart-audit/live_smoke.py",
)


def _git_bytes(*args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True)
    return result.stdout


def _file_provenance(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    data = path.read_bytes() if path.is_file() else b""
    blob = _git_bytes("cat-file", "-p", f"HEAD:{relative}")
    normalized = data.replace(b"\r\n", b"\n")
    return {
        "bytes": len(data),
        "actual_sha256": hashlib.sha256(data).hexdigest(),
        "git_blob_sha256": hashlib.sha256(blob).hexdigest() if blob else None,
        "crlf": b"\r\n" in data,
        "normalized_matches_git_blob": blob == normalized if blob else None,
    }


def source_provenance() -> dict[str, Any]:
    """The immutable source identity for one phase: HEAD, dirty state, bytes.

    ``actual_sha256`` is the working-tree byte hash (CRLF included);
    ``git_blob_sha256`` is the LF blob a reviewer can retrieve with
    ``git cat-file``.  ``normalized_matches_git_blob`` says whether the
    working-tree bytes are exactly the committed blob modulo line endings.
    """
    head = _git_bytes("rev-parse", "HEAD").decode("utf-8", errors="replace").strip()
    dirty = [
        line
        for line in _git_bytes("status", "--porcelain", "--untracked-files=no")
        .decode("utf-8", errors="replace")
        .splitlines()
        if line.strip()
    ]
    jars = {}
    for key, path in (("audit", AUDIT_JAR), ("interface", INTERFACE_JAR)):
        jars[key] = {
            "path": str(path),
            "bytes": path.stat().st_size if path.is_file() else 0,
            "sha256": sha256_file(path) if path.is_file() else None,
        }
    return {
        "head": head,
        "dirty": dirty,
        "clean": not dirty,
        "branch": _git_bytes("rev-parse", "--abbrev-ref", "HEAD").decode("utf-8", errors="replace").strip(),
        "files": {relative: _file_provenance(relative) for relative in TRACKED_SOURCE_FILES},
        "jars": jars,
        "lineEndingConvention": "CRLF working tree, LF Git blob"
        if any(_file_provenance(relative)["crlf"] for relative in TRACKED_SOURCE_FILES)
        else "LF",
        "capturedAt": _now_iso_ms(),
    }


def require_clean_source(phase: str) -> dict[str, Any]:
    """Refuse to start an authoritative phase on a dirty or stale worktree."""
    provenance = source_provenance()
    if not provenance["clean"] and os.environ.get("MC_AGENT_ALLOW_DIRTY") != "1":
        raise RuntimeError(
            f"the worktree is dirty before {phase}: {provenance['dirty'][:5]}; commit the source first "
            "(or set MC_AGENT_ALLOW_DIRTY=1 for a deliberately recorded mixed run)"
        )
    if not provenance["head"]:
        raise RuntimeError("the worktree has no Git HEAD; the run source would not be retrievable")
    for relative, entry in provenance["files"].items():
        if entry["normalized_matches_git_blob"] is False:
            raise RuntimeError(
                f"{relative} differs from its committed Git blob beyond line endings; "
                "commit the source before running"
            )
    return provenance


def close_phase(phase: str, before: dict[str, Any]) -> dict[str, Any]:
    """Fail the phase if any tracked source file changed while it ran."""
    after = source_provenance()
    changed = [
        relative
        for relative, entry in before["files"].items()
        if entry["actual_sha256"] != after["files"].get(relative, {}).get("actual_sha256")
    ]
    if after["head"] != before["head"] or changed or after["dirty"] != before["dirty"]:
        raise RuntimeError(
            f"the source changed while {phase} ran: head {before['head']} -> {after['head']}, "
            f"files {changed}, dirty {len(before['dirty'])} -> {len(after['dirty'])}; this phase's "
            "evidence mixes revisions and must not be used"
        )
    document = {"phase": phase, "before": before, "after": after, "changed": False}
    write_json(EVIDENCE / "provenance" / f"{phase}.json", document)
    return document


def _now_iso_ms() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
        if port.isdigit() and (27240 <= int(port) <= 27249 or 27150 <= int(port) <= 27151):
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
             "--at", _now_iso_ms()],
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
             "--at", _now_iso_ms()],
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


def audit_config(
    lab_name: str,
    run_id: str,
    instance: str,
    provenance: dict,
    regions: str = "fixture",
    *,
    agent_uuid: str | None = None,
) -> None:
    input_region = GENERIC_INPUT if regions == "generic" else INPUT_REGION
    stack_region = GENERIC_STACK if regions == "generic" else STACK_REGION
    output_region = GENERIC_OUTPUT if regions == "generic" else OUTPUT_REGION
    uuid = agent_uuid or fixture_uuid()
    if not uuid:
        raise RuntimeError("audit config needs the fixture player uuid observed in this run")
    config = {
        "runId": run_id,
        "instanceId": instance,
        "dimension": "minecraft:overworld",
        "provenance": provenance,
        "inputRegions": [input_region],
        "stackRegion": stack_region,
        "outputRegion": output_region,
        "cartTypes": ["minecraft:chest_minecart"],
        "sampleIntervalTicks": 20,
        "agentUuids": [uuid],
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


def _parse_count(output: str) -> int:
    match = re.search(r"Count:\s*(\d+)", output)
    if match:
        return int(match.group(1))
    if "Test failed" in output:
        return 0
    return -1


def machine_base_ok(instance: str) -> tuple[bool, list[dict[str, Any]]]:
    """Check the accepted fixture's base-state block checks on a live lab.

    The caller must have frozen the world first; force-loading here loads the
    machine chunks without letting a tick drop the gravity blocks.
    """
    rcon(instance, "forceload add 0 -40 32 -10")
    spec = read_json(FIXTURE_SPEC)
    broken: list[dict[str, Any]] = []
    for check in spec["machine"].get("checks") or []:
        position = check.get("pos") or []
        if len(position) != 3:
            continue
        output = rcon(instance, f"execute if block {position[0]} {position[1]} {position[2]} {check['block']}")
        if "Test passed" not in output:
            broken.append(check)
    return (not broken), broken


def _cart_counts(instance: str) -> dict[str, int]:
    """Observe the real fixture geometry: stack volume and total carts."""
    stack = rcon(instance, "execute if entity @e[type=minecraft:chest_minecart,x=13,y=-53,z=-23,dx=3,dy=4,dz=3]")
    total = rcon(instance, "execute if entity @e[type=minecraft:chest_minecart]")
    return {"stack": _parse_count(stack), "total": _parse_count(total)}


def _note_value(instance: str, expected: int) -> bool:
    return "Test passed" in rcon(instance, f"execute if block 11 -54 -23 minecraft:note_block[note={expected}]")


def _read_audit(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            rows.append(json.loads(stripped))
        except ValueError:
            continue
    return rows


class AuditTail:
    """Incremental reader for a growing audit JSONL (never re-parses the file)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.rows: list[dict[str, Any]] = []

    def read_new(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return self.rows
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset = 0
                self.rows = []
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
                self.offset = handle.tell()
        except OSError:
            return self.rows
        for line in data.decode("utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                self.rows.append(json.loads(stripped))
            except ValueError:
                continue
        return self.rows

    def count(self, event_type: str) -> int:
        return sum(1 for row in self.rows if row.get("type") == event_type)

    def wait(self, predicate: Any, *, timeout: float, poll: float = 0.25) -> list[dict[str, Any]]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.read_new()
            if predicate(self.rows):
                return self.rows
            time.sleep(poll)
        self.read_new()
        return self.rows


def wait_for_audit(path: Path, predicate: Any, *, timeout: float, poll: float = 0.25) -> list[dict[str, Any]]:
    """Convenience wrapper for one-off waits; ``predicate`` sees the rows."""
    tail = AuditTail(path)
    return tail.wait(lambda rows: any(predicate(row) for row in rows), timeout=timeout, poll=poll)


def ensure_seat_player(rec: Recorder | None, info: dict, *, recorded: bool = True, timeout: float = 90.0) -> dict[str, Any]:
    """Spawn the fixture fake player and park it on the restored hover seat.

    The fixture world holds the player as a passenger of the invisible marker
    seat; the probe is only complete when the player neither falls nor drifts.
    """
    name = info["name"]

    def command(label: str, text: str) -> str:
        if recorded and rec is not None:
            return rec.exec_rcon(label, text, instance=name)
        return rcon(name, text)

    # Freeze before force-loading: a machine chunk that loads in a ticking
    # world would drop its gravity blocks before the seat is even placed.  The
    # player is parked while frozen; the calibration unfreezes only after the
    # recorded press so a scheduled machine tick cannot fire first.
    command("seat-freeze", "tick freeze")
    command("seat-forceload", "forceload add 0 -40 32 -10")
    probe = command("seat-probe", f"data get entity {ROMUSER} Pos")
    if "has the following entity data" not in probe:
        command(
            "seat-spawn",
            f"player {ROMUSER} spawn at {SEAT_POS[0]} {SEAT_POS[1]} {SEAT_POS[2]} facing "
            f"{SEAT_FACING[0]} {SEAT_FACING[1]} in minecraft:overworld in creative",
        )
        deadline = time.time() + timeout
        while time.time() < deadline:
            probe = command("seat-poll", f"data get entity {ROMUSER} Pos")
            if "has the following entity data" in probe:
                break
            time.sleep(1.0)
    if "has the following entity data" not in probe:
        raise RuntimeError(f"{ROMUSER} never spawned on {name}")
    seat = command("seat-tag-check", f"execute if entity @e[type=minecraft:armor_stand,tag={SEAT_TAG}]")
    if "Test passed" not in seat:
        spec = read_json(FIXTURE_SPEC)
        seat_nbt = str((spec.get("user", {}).get("hover_seat") or {}).get("nbt") or "")
        if not seat_nbt:
            raise RuntimeError("the fixture spec has no hover-seat NBT to place")
        command(
            "seat-summon",
            f"summon minecraft:armor_stand {SEAT_POS[0]} {SEAT_POS[1]} {SEAT_POS[2]} {seat_nbt}",
        )
        time.sleep(0.3)
    command("seat-ride", f"ride {ROMUSER} mount @e[type=minecraft:armor_stand,tag={SEAT_TAG},limit=1]")
    command(
        "seat-place",
        f"tp @e[type=minecraft:armor_stand,tag={SEAT_TAG},limit=1] {SEAT_POS[0]} {SEAT_POS[1]} {SEAT_POS[2]}",
    )
    # Carpet's look is "look <pitch> <yaw>" while the spec stores [yaw, pitch]
    command("seat-look", f"player {ROMUSER} look {SEAT_FACING[1]} {SEAT_FACING[0]}")
    time.sleep(0.3)
    first = command("seat-stable-1", f"data get entity {ROMUSER} Pos")
    time.sleep(1.0)
    second = command("seat-stable-2", f"data get entity {ROMUSER} Pos")
    return {"first": first, "second": second}


def _player_position(output: str) -> list[float] | None:
    match = re.search(r"\[(-?[0-9.]+)d,\s*(-?[0-9.]+)d,\s*(-?[0-9.]+)d\]", output)
    if not match:
        return None
    return [float(match.group(1)), float(match.group(2)), float(match.group(3))]


def rom_calibration(
    rec: Recorder | None,
    info: dict,
    run_id: str,
    *,
    audited: bool,
    log: Path | None = None,
    recorded: bool = True,
) -> dict[str, Any]:
    """Operate the real fixture note block and observe natural void removal.

    Every press is followed by the real observer (when audited) and by a tick
    sprint; the cart leaves the stack through the fixture's output plane and is
    removed by the void below the world.  No platforms and no kill substitute.
    """
    name = info["name"]

    def command(label: str, text: str) -> str:
        if recorded and rec is not None:
            return rec.exec_rcon(label, text, instance=name)
        return rcon(name, text)

    observations: list[dict[str, Any]] = []
    removed = 0
    tail = AuditTail(log) if (audited and log is not None) else None
    for index in range(1, CALIBRATION_PRESSES + 1):
        before = _cart_counts(name)
        # Unfreeze only for the press itself: the machine's scheduled tick
        # must not run ahead of the recorded player input.
        command(f"rom-unfreeze-press-{index}", "tick unfreeze")
        command(f"rom-press-{index}", f"player {ROMUSER} use once")
        if tail is not None:
            tail.wait(lambda rows: sum(1 for row in rows if row.get("type") == "input_processed") >= index,
                      timeout=15.0)
            if tail.count("input_processed") < index:
                raise RuntimeError(f"press {index} produced no input_processed event")
        else:
            time.sleep(0.5)
        note_ok = _note_value(name, 20 + index)
        command(f"rom-sprint-{index}", f"tick sprint {CALIBRATION_SPRINT_TICKS}")
        if tail is not None:
            tail.wait(lambda rows: sum(1 for row in rows if row.get("type") == "cart_remove") >= index,
                      timeout=60.0)
            removed = tail.count("cart_remove")
            if removed < index:
                raise RuntimeError(f"press {index} produced no natural cart_remove event")
        else:
            after = before
            deadline = time.time() + 45.0
            while time.time() < deadline:
                after = _cart_counts(name)
                if after["total"] >= 0 and after["total"] < before["total"]:
                    break
                time.sleep(1.0)
            delta = before["total"] - after["total"] if after["total"] >= 0 else 0
            removed = removed + max(0, delta)
        after = _cart_counts(name)
        observations.append(
            {
                "press": index,
                "note_step_ok": note_ok,
                "stack_before": before["stack"],
                "stack_after": after["stack"],
                "total_before": before["total"],
                "total_after": after["total"],
                "removed_total": removed,
            }
        )
    return {
        "instance": name,
        "runId": run_id,
        "audited": audited,
        "log": str(log) if log is not None else "",
        "observations": observations,
        "final_total": observations[-1]["total_after"] if observations else None,
    }


def source_child(rec: Recorder, src: dict, run_id: str) -> dict[str, Any]:
    """Bounded ROM calibration on the copied source world (same parent run)."""
    log = LABS / src["name"] / "mc-audit" / f"audit-{run_id}.jsonl"
    calibration = rom_calibration(rec, src, run_id, audited=True, log=log)
    rec.exec_rcon("child-flush", "mcaudit flush", instance=src["name"])
    rec.exec_rcon("child-phase-end", "mcaudit phase experiment_end", instance=src["name"])
    rec.exec_rcon("child-end", "mcaudit end", instance=src["name"])
    status = LABS / src["name"] / "mc-audit" / f"status-{run_id}.json"
    return {"runId": run_id, "log": str(log), "calibration": calibration, "status": str(status)}


async def failure_probes(
    rec: Recorder, api: Any, src: dict, exp: dict, directory: str, exp_world: str, stage: str
) -> list[dict[str, Any]]:
    """Real refusal/mismatch probes for the gate's failure cases.

    ``stage="pre"`` runs the non-destructive refusals while the restored carts
    are still present; the inventory mutation probe restores the original
    value before it returns, so the bounded ROM calibration still operates the
    restored machine.  ``stage="post"`` runs the mutating probes after the
    calibration session has been closed and copied.
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
        cart_uuid = None
        original_nbt = ""
        for line in (Path(directory) / "entities.jsonl").read_text(encoding="utf-8").splitlines():
            ent = json.loads(line)
            if ent.get("type") == "minecraft:chest_minecart":
                cart_uuid = ent.get("uuid")
                original_nbt = str(ent.get("nbt") or "")
                break
        if not cart_uuid:
            rows.append({"case": "inventory_mutation_order_hash", "injected": "no chest minecart in the snapshot",
                         "expected": "a cart to mutate", "observed": "none", "passed": False, "evidence_ref": "snapshot"})
        else:
            match = re.search(r'"Items":\[\{.*?"count":(\d+)', original_nbt)
            original_count = int(match.group(1)) if match else None
            rcon(exp["name"], f"data modify entity {cart_uuid} Items[0].count set value 64")
            _r, mutated = await bridge_step(rec, api, "probe-inventory-mutation", "verify",
                                            {"directory": directory, "target": exp["name"]}, instance=exp["name"])
            verification = ((mutated.get("result") or {}).get("verification") or {})
            mutated_ok = bool(mutated.get("ok")) \
                and verification.get("orderHash", {}).get("match") \
                and verification.get("nbtMismatches", {}).get("count", 0) > 0
            restored = False
            if original_count is not None:
                rcon(exp["name"], f"data modify entity {cart_uuid} Items[0].count set value {original_count}")
                _r, back = await bridge_step(rec, api, "probe-inventory-mutation-restore", "verify",
                                             {"directory": directory, "target": exp["name"]}, instance=exp["name"])
                back_verification = ((back.get("result") or {}).get("verification") or {})
                restored = bool(back.get("ok")) and back_verification.get("orderHash", {}).get("match") \
                    and back_verification.get("nbtMismatches", {}).get("count", 0) == 0
            row("inventory_mutation_order_hash", f"Items[0].count of {cart_uuid} modified then restored",
                "NBT mismatch detected while orderHash still matches; original value restored",
                mutated, mutated_ok and restored)

        _r, wrong = await bridge_step(rec, api, "probe-wrong-endpoint", "restore",
                                      {"directory": directory, "target": exp["name"],
                                       "expect_instance": "server",
                                       "expect_world_dir": str(LABS / src["name"] / "world")},
                                      instance=exp["name"])
        row("wrong_endpoint", "expect_world_dir points at the source lab world",
            "refused before any mutation", wrong,
            wrong.get("ok") is False and "endpoint" in str(wrong.get("error", "")).lower())

        _r, duplicate = await bridge_step(rec, api, "probe-duplicate", "restore",
                                          {"directory": directory, "dry_run": False, "target": exp["name"],
                                           "expect_instance": "server", "expect_world_dir": exp_world,
                                           "replace_existing": False}, instance=exp["name"])
        row("duplicate_pre_existing", "second restore with replace_existing=false over the restored carts",
            "duplicate entities rejected", duplicate,
            duplicate.get("ok") is False or "duplicate" in json.dumps(duplicate.get("result")))
        return rows

    _r, unverified = await bridge_step(rec, api, "probe-unverified", "restore",
                                       {"directory": directory, "dry_run": False, "target": exp["name"],
                                        "expect_instance": "server", "expect_world_dir": exp_world,
                                        "replace_existing": True, "verify": False}, instance=exp["name"])
    unverified_result = unverified.get("result") or {}
    row("unverified", "restore with replace_existing=true and verification disabled",
        "verdict=unverified, verified=false, never a success claim", unverified,
        unverified_result.get("verdict") == "unverified"
        and unverified_result.get("verified") is False
        and unverified_result.get("ok") is not True)

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

    partial = EVIDENCE / "probe-partial-failure"
    shutil.rmtree(partial, ignore_errors=True)
    shutil.copytree(directory, partial)
    lines = (partial / "entities.jsonl").read_text(encoding="utf-8").splitlines()
    bad = json.loads(lines[0])
    bad["type"] = "minecraft:not_a_real_entity"
    (partial / "entities.jsonl").write_text(json.dumps(bad) + "\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8")
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

    refusal = EVIDENCE / "probe-summon-refusal"
    shutil.rmtree(refusal, ignore_errors=True)
    shutil.copytree(directory, refusal)
    lines = (refusal / "entities.jsonl").read_text(encoding="utf-8").splitlines()
    bad = json.loads(lines[0])
    bad["nbt"] = "{NotValid"
    (refusal / "entities.jsonl").write_text(json.dumps(bad) + "\n" + "\n".join(lines[1:]) + "\n", encoding="utf-8")
    _r, refused = await bridge_step(rec, api, "probe-summon-refusal", "restore",
                                    {"directory": str(refusal), "dry_run": False, "target": exp["name"],
                                     "expect_instance": "server", "expect_world_dir": exp_world,
                                     "replace_existing": True}, instance=exp["name"], timeout=900)
    row("summon_refusal", "one snapshot entity carries invalid NBT",
        "server refusal surfaced as a failed command", refused,
        refused.get("ok") is False or bool(refused.get("result", {}).get("failed")))
    return rows


async def identity_probe(rec: Recorder, api: Any, info: dict) -> dict[str, Any]:
    """Measure the real player context on the restored machine.

    The player is parked on the fixture hover seat; ``hit`` requires the
    server-side view ray to land on the fixture note block and ``miss``
    requires a real ``type: "miss"`` target after looking away.  Both records
    carry the raw reply and the observed position.
    """
    name = info["name"]
    records: list[dict[str, Any]] = []

    def view_of(response: Any) -> dict[str, Any]:
        payload = response if isinstance(response, dict) else {}
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        view = player.get("view") if isinstance(player.get("view"), dict) else {}
        if not view and isinstance(payload.get("view"), dict):
            view = payload["view"]
        return view

    def target_of(response: Any) -> dict[str, Any]:
        view = view_of(response)
        target = view.get("target") if isinstance(view.get("target"), dict) else {}
        return target

    def record(case: str, response: Any, *, accepted: bool, hit: bool, other_uuid: str | None = None) -> dict[str, Any]:
        payload = response if isinstance(response, dict) else {}
        player = payload.get("player") if isinstance(payload.get("player"), dict) else {}
        position = _player_position(rcon(name, f"data get entity {ROMUSER} Pos")) or [0.0, 0.0, 0.0]
        return {
            "case": case,
            "uuid": str(player.get("uuid") or payload.get("uuid") or payload.get("query") or ""),
            "viewed_uuid": str(player.get("uuid") or payload.get("uuid") or payload.get("query") or ""),
            "dimension": player.get("dimension") or "minecraft:overworld",
            "pos": position,
            "yaw": float(player.get("yaw", 0.0)),
            "pitch": float(player.get("pitch", 0.0)),
            "task_entry": "external_task",
            "channel": "cli",
            "accepted": accepted,
            "hit": hit,
            "target": target_of(response),
            "other_uuid": other_uuid,
            "detail": {"raw": payload, "probe": "mc-bridge call player", "seat": SEAT_POS},
        }

    romuser, romuser_record = await call(api, "player", {"player": ROMUSER})
    romuser_rec = romuser_record.get("result")
    if not romuser or not romuser.get("found"):
        raise RuntimeError(f"the fixture player {ROMUSER} has no server context: {romuser_record}")
    position = _player_position(rcon(name, f"data get entity {ROMUSER} Pos"))
    if position is None or any(abs(a - b) > 1.0 for a, b in zip(position, SEAT_POS)):
        raise RuntimeError(f"{ROMUSER} is not parked on the fixture seat: {position}")
    hit_target = target_of(romuser)
    hit_block = hit_target.get("block") if isinstance(hit_target.get("block"), dict) else {}
    if hit_target.get("type") != "block" or hit_block.get("id") != "minecraft:note_block":
        raise RuntimeError(f"the fixture view does not resolve the note block: {hit_target}")
    records.append(record("task_bind", romuser, accepted=True, hit=True))
    records.append(record("hit", romuser, accepted=True, hit=True))

    rcon(name, f"player {ROMUSER} look -90 0")
    time.sleep(0.4)
    miss, _miss_record = await call(api, "player", {"player": ROMUSER})
    miss_target = target_of(miss)
    if miss_target.get("type") != "miss":
        raise RuntimeError(f"looking away did not produce a miss target: {miss_target}")
    records.append(record("miss", miss, accepted=bool((miss or {}).get("found")), hit=False))
    rcon(name, f"player {ROMUSER} look {SEAT_FACING[1]} {SEAT_FACING[0]}")
    time.sleep(0.4)

    rcon(
        name,
        f"player {SECOND_PLAYER} spawn at 20 -53 -24.5 facing 0 50 in minecraft:overworld in creative",
    )
    deadline = time.time() + 15.0
    other: Any = None
    while time.time() < deadline:
        time.sleep(1.0)
        candidate, _other_record = await call(api, "player", {"player": SECOND_PLAYER})
        if candidate and candidate.get("found"):
            other = candidate
            break
    other_uuid = str(((other or {}).get("player") or {}).get("uuid") or "")
    if not other_uuid or other_uuid == records[0]["uuid"]:
        raise RuntimeError(f"the second player did not resolve distinctly: {other}")
    records.append(record("two_players", romuser, accepted=True, hit=True, other_uuid=other_uuid))

    unknown, _unknown_record = await call(api, "player", {"player": "11111111-2222-3333-4444-555555555555"})
    unknown_record = record("unknown_identity", unknown, accepted=False, hit=False)
    unknown_record["uuid"] = "11111111-2222-3333-4444-555555555555"
    unknown_record["viewed_uuid"] = "11111111-2222-3333-4444-555555555555"
    unknown_record["rejected_reason"] = str((unknown or {}).get("error") or "found=false")
    records.append(unknown_record)
    return {"records": records, "position": position, "hit": True, "miss": True}


def control_parity(control_world: Path, run_id: str, audited: dict[str, Any]) -> dict[str, Any]:
    """Run the same ROM calibration without the audit mod on a copied world.

    This is the like-for-like parity run: same world copy, same fixture
    geometry, same player/presses/sprints, no audit mod.  Its calls are not
    part of the audited trajectory.
    """
    lab("stop", "--name", CTL["name"], "--timeout", "120", check=False)
    shutil.rmtree(LABS / CTL["name"], ignore_errors=True)
    lab(
        "provision", "--name", CTL["name"], "--force", "--fabric-api", "--carpet",
        "--java", str(JAVA), "--jdk", str(JDK),
        "--server-port", str(CTL["server"]), "--rcon-port", str(CTL["rcon"]),
        "--world", str(control_world),
    )
    lab("start", "--name", CTL["name"], "--wait", "300")
    try:
        ensure_seat_player(None, CTL, recorded=False)
        control = rom_calibration(None, CTL, run_id, audited=False, recorded=False)
    finally:
        lab("stop", "--name", CTL["name"], "--timeout", "120", check=False)
    audited_observations = audited.get("observations") or []
    control_observations = control.get("observations") or []
    consistent = (
        len(audited_observations) == len(control_observations) == CALIBRATION_PRESSES
        and all(
            left.get("note_step_ok") is True
            and right.get("note_step_ok") is True
            and left.get("removed_total") == right.get("removed_total")
            and left.get("total_after") == right.get("total_after")
            for left, right in zip(audited_observations, control_observations)
        )
        and audited.get("final_total") == control.get("final_total")
        and (audited.get("observations") or [{}])[-1].get("removed_total") == 1
    )
    return {"control": control, "consistent": consistent}


async def live(args: argparse.Namespace) -> int:
    provenance = require_clean_source("live")
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_id = f"rom13-fullgate-{stamp}"
    run_dir = EVIDENCE / f"run-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    rec = Recorder(run_id, run_dir)
    rec.init(
        "Issue #14 full stage-one gate: deterministic fixture, guarded same-run restore, bounded real "
        "ROM calibration (source copy + restored copy, natural void removal), identity on the fixture "
        "seat, no-mod parity, dev-capability probes, frozen trajectory and gate. No ROM solution, no "
        "agent logger."
    )
    for info in (SRC, EXP, NEG, CTL):
        lab("stop", "--name", info["name"], "--timeout", "120", check=False)
    free_reserved_ports()
    for stale in ("rom18-ctl", "rom13-ctl"):
        shutil.rmtree(ROOT / "labs" / stale, ignore_errors=True)
    facts: dict[str, Any] = {
        "runId": run_id, "runDir": str(run_dir), "stamp": stamp,
        "labs": {"src": SRC, "exp": EXP, "neg": NEG, "ctl": CTL},
        "interface": {"jar": str(INTERFACE_JAR), "sha256": INTERFACE_SHA, "commit": INTERFACE_COMMIT},
        "bridge": {"source": str(BRIDGE_SOURCE), "runtimeHead": BRIDGE_HEAD, "mergedHead": BRIDGE_MERGED},
        "auditJar": {"path": str(AUDIT_JAR), "sha256": sha256_file(AUDIT_JAR)},
        "stages": {},
        "sourceBaseline": SOURCE_BASELINE_SHA256,
        "driver": {
            "path": str(TOOLS / "stage1_fullgate.py"),
            "sha256": sha256_file(TOOLS / "stage1_fullgate.py"),
        },
    }
    try:
        if INTERFACE_JAR.is_file() and sha256_file(INTERFACE_JAR) != INTERFACE_SHA:
            raise RuntimeError("interface jar hash mismatch")
        head = run(["git", "-C", str(BRIDGE_SOURCE), "rev-parse", "HEAD"]).stdout.strip()
        if head != BRIDGE_HEAD:
            raise RuntimeError(f"bridge source head {head} != expected {BRIDGE_HEAD}")
        if str(BRIDGE_SOURCE / "src") not in sys.path:
            sys.path.insert(0, str(BRIDGE_SOURCE / "src"))

        # ---- run-level source-world observations (separate, before/after) --
        before_hash, before_files, before_bytes = gate.tree_hash(SOURCE_SAVE)
        facts["sourceWorldBefore"] = {
            "tree_sha256": before_hash, "files": before_files, "bytes": before_bytes,
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if before_hash != SOURCE_BASELINE_SHA256:
            raise RuntimeError(
                f"source world before the run is {before_hash}, not the documented baseline "
                f"{SOURCE_BASELINE_SHA256}"
            )

        # ---- fixture: three independent initializations on this run's source
        cache_world = LABS / "_cache" / "maps" / "unpacked" / "minecart-rom-base-1.0.0" / "world"
        if not cache_world.is_dir():
            raise RuntimeError(f"the extracted fixture map world is missing: {cache_world}")
        facts["fixtureMapWorld"] = str(cache_world)
        shutil.rmtree(LABS / SRC["name"], ignore_errors=True)
        lab(
            "provision", "--name", SRC["name"], "--force", "--fabric-api", "--carpet",
            "--java", str(JAVA), "--jdk", str(JDK),
            "--server-port", str(SRC["server"]), "--rcon-port", str(SRC["rcon"]),
            "--vantage-port", str(SRC["vantage"]), "--bridge-port", str(SRC["bridge"]),
            "--mod-jar", str(INTERFACE_JAR), "--world", str(cache_world),
        )
        lab("start", "--name", SRC["name"], "--wait", "300", check=False)
        rcon(SRC["name"], "tick freeze")
        base_ok, base_broken = machine_base_ok(SRC["name"])
        if not base_ok:
            raise RuntimeError(f"the fresh fixture map is not in its base state: {base_broken}")
        live_records = EVIDENCE / "fixture" / "live-records"
        live_records = EVIDENCE / "fixture" / f"live-records-{stamp}"
        live_records.mkdir(parents=True, exist_ok=True)
        facts["fixtureRecords"] = str(live_records)
        fetched = rec.step(
            "fixture-fetch-cold",
            [PY, ROOT / "examples" / "minecart-rom" / "runner" / "minecart_rom.py", "fetch", "--cold",
             "--records", str(live_records), "--cache", str(LABS / "_cache" / "maps")],
            tool="bash", instance=SRC["name"], timeout=600,
        )
        if fetched["returncode"] != 0:
            raise RuntimeError(f"the fixture cold fetch failed: {fetched['stderr'][-400:]}")
        init_runs: list[dict[str, Any]] = []
        for index in range(1, 4):
            init: dict[str, Any] | None = None
            for attempt in range(2):
                rcon(SRC["name"], "forceload add 0 -40 32 -10")
                time.sleep(1.5)
                rcon(SRC["name"], "kill @e[type=minecraft:armor_stand]")
                rcon(SRC["name"], "kill @e[type=minecraft:chest_minecart]")
                init = rec.step(
                    f"fixture-live-init-{index}-{attempt + 1}",
                    [PY, ROOT / "examples" / "minecart-rom" / "runner" / "minecart_rom.py", "init",
                     "--lab", SRC["name"], "--records", str(live_records),
                     "--run-id", f"live-source-{stamp}-{index}"],
                    tool="bash", instance=SRC["name"], timeout=900,
                )
                if init["returncode"] == 0:
                    break
                time.sleep(2.0)
            if init is None or init["returncode"] != 0:
                raise RuntimeError(f"fixture live init {index} failed: {(init or {}).get('stderr', '')[-800:]}")
            payload = json.loads(init["stdout"][init["stdout"].find("{"):]) if "{" in init["stdout"] else {}
            record_dir = Path(str(payload.get("records") or ""))
            ready = record_dir / "ready-snapshot.json"
            if not ready.is_file():
                candidates = sorted(live_records.glob(f"*{index}/ready-snapshot.json"))
                if not candidates:
                    raise RuntimeError(f"fixture init {index} left no ready-snapshot: {init['stdout'][-400:]}")
                ready = candidates[-1]
            snapshot = read_json(ready)
            validation = snapshot.get("validation") if isinstance(snapshot.get("validation"), dict) else {}
            init_runs.append(
                {
                    "init_id": f"live-source-{stamp}-{index}",
                    "run_id": f"live-source-{stamp}-{index}",
                    "instance_id": SRC["name"],
                    "record": str(ready),
                    "state_hash": snapshot.get("normalized_hash"),
                    "order_hash": snapshot.get("tick_order_hash"),
                    "tick": snapshot.get("world_day_tick"),
                    "captured_at": snapshot.get("captured_at"),
                    "user": (snapshot.get("user") or {}),
                    "validated": bool(validation.get("ok", True)),
                }
            )
        facts["stages"]["initRuns"] = init_runs
        say(f"live: {len(init_runs)} fixture initializations validated")
        states = {run.get("state_hash") for run in init_runs}
        orders = {run.get("order_hash") for run in init_runs}
        if None in states or None in orders or len(states) != 1 or len(orders) != 1:
            raise RuntimeError(f"the three fixture initializations are not identical: {init_runs}")
        if not all(run.get("validated") for run in init_runs):
            raise RuntimeError(f"a fixture initialization did not validate: {init_runs}")
        user = init_runs[-1].get("user") or {}
        uuid = str(user.get("uuid") or user.get("UUID") or "")
        if uuid.startswith("[I;"):
            uuid = _uuid_from_nbt(uuid)
        if not uuid or "-" not in uuid:
            raise RuntimeError(f"the final init record has no fixture player uuid: {init_runs[-1]}")
        facts["fixturePlayerUuid"] = uuid

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
        facts["stages"]["fork"] = {
            key: fork_result.get(key) for key in ("snapshotDir", "forkDir", "worldDir", "orderHash", "entities")
        } if fork_result else None
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
        # The ready world keeps its machine only while its chunks are frozen;
        # drop the force-loads before the restart so the next start cannot
        # tick gravity blocks before the freeze command arrives.
        rcon(SRC["name"], "forceload remove all")
        await api_src.close()
        lab("stop", "--name", SRC["name"], "--timeout", "120", check=False)
        stop_bridges()

        # keep the ready world for the no-mod parity copy before the source
        # child operates its own copy
        control_world = EVIDENCE / "control-world"
        shutil.rmtree(control_world, ignore_errors=True)
        shutil.copytree(LABS / SRC["name"] / "world", control_world)

        # ---- source child: bounded ROM calibration on the copied source ----
        lab(
            "provision", "--name", SRC["name"], "--fabric-api", "--carpet",
            "--java", str(JAVA), "--jdk", str(JDK),
            "--server-port", str(SRC["server"]), "--rcon-port", str(SRC["rcon"]),
            "--vantage-port", str(SRC["vantage"]), "--bridge-port", str(SRC["bridge"]),
            "--test-mod", str(AUDIT_JAR), "--mod-jar", str(INTERFACE_JAR),
        )
        audit_config(
            SRC["name"], run_id, SRC["name"],
            {"kind": "source-world-copy", "reference": str(SOURCE_SAVE)}, regions="fixture",
            agent_uuid=uuid,
        )
        rec.mark("src-provision", {"lab": SRC, "auditJar": sha256_file(AUDIT_JAR)})
        lab("start", "--name", SRC["name"], "--wait", "300")
        rcon(SRC["name"], "tick freeze")
        base_ok, base_broken = machine_base_ok(SRC["name"])
        if not base_ok:
            raise RuntimeError(f"the source copy is not in its base state before calibration: {base_broken}")
        ensure_seat_player(rec, SRC)
        rec.exec_rcon("child-phase-init", "mcaudit phase init", instance=SRC["name"])
        rec.exec_rcon("child-phase-start", "mcaudit phase experiment_start", instance=SRC["name"])
        child = source_child(rec, SRC, run_id)
        child_copy = EVIDENCE / "audit" / f"source-audit-{run_id}-src.jsonl"
        child_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(child["log"], child_copy)
        child["logCopy"] = str(child_copy)
        child["logCopySha256"] = sha256_file(child_copy)
        child_check = run(
            [PY, ROOT / "tools" / "minecart_audit.py", "check", "--log", child["log"], "--json"],
            check=False, timeout=300,
        )
        (EVIDENCE / "audit-source-check.log").write_text(child_check.stdout + child_check.stderr, encoding="utf-8")
        child_report = json.loads(child_check.stdout[child_check.stdout.find("{"):]) if "{" in child_check.stdout else {}
        child["verdict"] = child_report.get("verdict")
        child["verifierExit"] = child_check.returncode
        if child_check.returncode != 0 or child["verdict"] != "pass":
            raise RuntimeError(f"source child calibration did not pass: {child_report}")
        facts["stages"]["sourceChild"] = child
        say("live: source-copy ROM calibration passed")
        lab("stop", "--name", SRC["name"], "--timeout", "120", check=False)

        # ---- experiment: guarded restore, identity, bounded calibration -----
        shutil.rmtree(LABS / EXP["name"], ignore_errors=True)
        provision_with_mods(EXP, world=Path(fork_result["forkDir"]), test_mod=AUDIT_JAR, extra_mods=[])
        audit_config(
            EXP["name"], run_id, EXP["name"],
            {"kind": "fork", "reference": str(fork_result["forkDir"]),
             "snapshotId": Path(str(fork_result["snapshotDir"])).name, "snapshotHash": fork_result.get("orderHash")},
            regions="fixture", agent_uuid=uuid,
        )
        lab("start", "--name", EXP["name"], "--wait", "300")
        rcon(EXP["name"], "tick freeze")
        base_ok, base_broken = machine_base_ok(EXP["name"])
        if not base_ok:
            raise RuntimeError(f"the experiment fork is not in its base state: {base_broken}")
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
        # Real post-restore probes instead of hardcoded restore-record flags.
        frozen_before = rcon(EXP["name"], "tick query")
        seat_probe = rcon(EXP["name"], "execute if entity @e[type=minecraft:armor_stand,tag=minecart-rom-fixture]")
        cart_probe = rcon(
            EXP["name"], "execute if entity @e[type=minecraft:chest_minecart,x=13,y=-53,z=-23,dx=3,dy=4,dz=3]"
        )
        frozen_after = rcon(EXP["name"], "tick query")
        # ``applied``/``verified`` are the bridge replies; the wrapper records
        # are stored separately in facts["stages"]["restore"].
        applied_result_probe = applied or {}
        verified_probe = verified or {}
        restore_flags = {
            "chunks_loaded": "Test passed" in seat_probe and _parse_count(cart_probe) > 0,
            "tick_controlled": "frozen" in frozen_before.lower() and "frozen" in frozen_after.lower(),
            "pause_state_preserved": "frozen" in frozen_after.lower(),
            "issued_is_not_success": int(applied_result_probe.get("issued") or 0) > 0
            and int((verified_probe.get("verification") or {}).get("matched") or 0)
            == int(applied_result_probe.get("count") or -1),
            "evidence": {
                "seat_probe": seat_probe.strip()[:200],
                "cart_probe": cart_probe.strip()[:200],
                "tick_query_before": frozen_before.strip()[:120],
                "tick_query_after": frozen_after.strip()[:120],
                "issued": applied_result_probe.get("issued"),
                "count": applied_result_probe.get("count"),
                "matched": (verified_probe.get("verification") or {}).get("matched"),
            },
        }
        if not all(restore_flags[key] for key in ("chunks_loaded", "tick_controlled", "pause_state_preserved", "issued_is_not_success")):
            raise RuntimeError(f"the real restore flag probes failed: {restore_flags}")
        facts["stages"]["restore"]["flags"] = restore_flags
        after_copy = snapshots / "after"
        shutil.rmtree(after_copy, ignore_errors=True)
        after_copy.mkdir(parents=True)
        for file_name in ("meta.json", "entities.jsonl"):
            (after_copy / file_name).write_bytes(Path(verified["snapshotDir"], file_name).read_bytes())
        facts["stages"]["restore"]["snapshotBeforeCopy"] = str(before_copy)
        facts["stages"]["restore"]["snapshotAfterCopy"] = str(after_copy)

        # identity on the restored machine: the player is parked on the seat
        # and the view ray hits the fixture note block (real hit/miss); the
        # world stays frozen so no scheduled machine tick alters the target
        ensure_seat_player(rec, EXP)
        identity = await identity_probe(rec, api_exp, EXP)
        facts["stages"]["identity"] = identity
        say("live: identity hit/miss measured on the restored seat")

        pre_cases = await failure_probes(rec, api_exp, SRC, EXP, directory, exp_world, "pre")

        # ---- positive: bounded real ROM calibration on the restored copy ---
        exp_log = LABS / EXP["name"] / "mc-audit" / f"audit-{run_id}.jsonl"
        rec.exec_rcon("audit-phase-start", "mcaudit phase experiment_start", instance=EXP["name"])
        calibration = rom_calibration(rec, EXP, run_id, audited=True, log=exp_log)
        flush_output = rec.exec_rcon("audit-flush", "mcaudit flush", instance=EXP["name"])
        rec.exec_rcon("audit-phase-end", "mcaudit phase experiment_end", instance=EXP["name"])
        rec.exec_rcon("audit-end", "mcaudit end", instance=EXP["name"])
        audit_dir = EVIDENCE / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        positive_copy = audit_dir / f"positive-{run_id}.jsonl"
        shutil.copyfile(exp_log, positive_copy)
        status_copy = audit_dir / f"status-{run_id}.json"
        shutil.copyfile(LABS / EXP["name"] / "mc-audit" / f"status-{run_id}.json", status_copy)
        positive_check = run(
            [PY, ROOT / "tools" / "minecart_audit.py", "check", "--log", str(exp_log), "--json"],
            check=False, timeout=300,
        )
        (EVIDENCE / "audit-positive-check.log").write_text(positive_check.stdout + positive_check.stderr, encoding="utf-8")
        positive_report = json.loads(positive_check.stdout[positive_check.stdout.find("{"):]) if "{" in positive_check.stdout else {}
        raw_positive = _read_audit(exp_log)
        audit_end = next((row for row in raw_positive if row.get("type") == "audit_end"), {})
        hooks_raw = audit_end.get("hooks") if isinstance(audit_end.get("hooks"), dict) else {}
        hook_overhead_ms = round(
            sum(float(entry.get("totalNanos") or 0) for entry in hooks_raw.values()) / 1_000_000.0, 3
        ) if hooks_raw else None
        positive = {
            "runId": run_id,
            "log": str(exp_log),
            "logCopy": str(positive_copy),
            "logCopySha256": sha256_file(positive_copy),
            "calibration": calibration,
            "verdict": positive_report.get("verdict"),
            "verifierExit": positive_check.returncode,
            "flush": {"call_id": "audit-flush", "command": "mcaudit flush", "output": flush_output},
            "status": str(status_copy),
            "statusSha256": sha256_file(status_copy),
            "hookOverheadMs": hook_overhead_ms,
            "hookOverheadScope": "total session hook time across all hooks (sum of per-hook totalNanos / 1e6)",
            "hookCountersNanos": hooks_raw,
        }
        if positive_check.returncode != 0 or positive["verdict"] != "pass":
            raise RuntimeError(f"the restored-copy calibration did not pass: {positive_report}")
        facts["stages"]["positive"] = positive
        say("live: restored-copy ROM calibration passed")

        # mutating probes after the calibration session is closed and copied
        facts["failureCases"] = pre_cases + await failure_probes(
            rec, api_exp, SRC, EXP, directory, exp_world, "post"
        )
        facts["stages"]["restore"]["preCases"] = pre_cases
        await api_exp.close()
        lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)
        stop_bridges()

        # ---- negative cases on a disposable void lab ------------------------
        facts["stages"]["negatives"] = run_negatives(rec, run_id)

        # ---- no-mod parity on the copied ready world ------------------------
        say("live: running the no-mod parity calibration")
        facts["stages"]["control"] = control_parity(control_world, run_id, calibration)
        say("live: no-mod parity matched")
        if not facts["stages"]["control"].get("consistent"):
            raise RuntimeError("the no-mod ROM parity run did not match the audited run")

        # trace category coverage for terminal/file/source/mcp
        rec.step("pin-revisions", ["git", "rev-parse", "HEAD"], tool="git", instance=SRC["name"])
        rec.step("mcp-probe-help", [PY, ROOT / "tools" / "mcp_probe.py", "--help"], tool="mcp_probe", instance=EXP["name"])
        marker_file = EVIDENCE / "build" / "INTEGRATION-MARKER.txt"
        rec.step(
            "write-integration-marker",
            [PY, "-c", f"from pathlib import Path; Path(r'{marker_file}').write_text('{run_id}', encoding='utf-8')"],
            tool="write", instance=EXP["name"],
        )

        # ---- run-level source-world after observation -----------------------
        after_hash, after_files, after_bytes = gate.tree_hash(SOURCE_SAVE)
        facts["sourceWorldAfter"] = {
            "tree_sha256": after_hash, "files": after_files, "bytes": after_bytes,
            "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if after_hash != before_hash or after_hash != SOURCE_BASELINE_SHA256:
            raise RuntimeError(
                f"the source world changed during the run: before={before_hash} after={after_hash} "
                f"baseline={SOURCE_BASELINE_SHA256}"
            )

        facts["trajectory"] = rec.finalize()
    except BaseException as error:  # noqa: BLE001 - keep the failed attempt
        facts["error"] = f"{type(error).__name__}: {error}"
        say(f"live error: {facts['error']}")
        for info in (SRC, EXP, NEG, CTL):
            lab("stop", "--name", info["name"], "--timeout", "120", check=False)
        stop_bridges()
        (EVIDENCE / f"facts-live-failed-{stamp}.json").write_text(
            json.dumps(facts, indent=2) + "\n", encoding="utf-8"
        )
        return 1
    facts["provenance"] = {"source": provenance, "phase": close_phase("live", provenance)}
    write_json(EVIDENCE / "facts-live.json", facts)
    say(f"live: run {run_id} completed")
    return 0


def run_negatives(rec: Recorder, run_id: str) -> dict[str, Any]:
    """The four gate negatives plus attack/environment extras and the mixed-trigger regression."""
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
        # extras beyond the gate's four: an attack-only punch and a redstone
        # trigger must still not count as an agent operation.
        ("attack_only", live_smoke.scenario_attack_only),
        ("environment_only", live_smoke.scenario_environment_only),
    ]
    for case, function in scenarios:
        neg_run = f"{run_id}-neg-{case}"
        try:
            result[case] = function("rom13-neg", neg_run, "rom13-neg")
        except BaseException as error:  # noqa: BLE001
            result[case] = {"error": f"{type(error).__name__}: {error}"}
    # mixed-trigger regression: punch then use in the same window must yield
    # exactly one processed operation (the use), never a stale double count
    try:
        result["attack_then_use"] = live_smoke.scenario_attack_then_use(
            "rom13-neg", f"{run_id}-neg-attack_then_use", "rom13-neg"
        )
    except BaseException as error:  # noqa: BLE001
        result["attack_then_use"] = {"error": f"{type(error).__name__}: {error}"}
    run([PY, LAB_SERVER, "stop", "--name", "rom13-neg", "--timeout", "120"], check=False)
    return result


# --------------------------------------------------------------------------- identity


def ensure_bridge_path() -> None:
    source = str(BRIDGE_SOURCE / "src")
    if source not in sys.path:
        sys.path.insert(0, source)


async def identity_phase(args: argparse.Namespace) -> int:
    provenance = require_clean_source("identity")
    """Emit the player_context artifact from the live identity probe.

    The probe ran on the restored machine with the player parked on the
    fixture hover seat (real hit on the note block, real miss after looking
    away).  This phase only projects those observed records; it never invents
    a sample.
    """
    facts = read_json(EVIDENCE / "facts-live.json")
    identity = (facts.get("stages") or {}).get("identity") or {}
    records = identity.get("records") or []
    if not identity.get("hit") or not identity.get("miss") or len(records) < 5:
        raise RuntimeError("facts-live.json has no complete live identity probe; run the live phase first")
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
        "detail": {
            "entry": "external-cli task entry",
            "note": "Carpet /say is a broadcast, not receipt-time chat",
            "seat": [SEAT_POS, SEAT_FACING],
            "probe": "restored machine, hover seat, view ray",
        },
    })
    write_json(target / "version-pins.json", {
        "interface_mod": {"repo": "guajun/mc-agent-interface-mod", "commit": INTERFACE_COMMIT, "tested": True},
        "bridge": {"repo": "guajun/mc-agent-bridge", "commit": BRIDGE_MERGED, "tested": True},
    })
    write_json(EVIDENCE / "facts-identity.json", {"records": len(records), "path": str(target)})
    close_phase("identity", provenance)
    say(f"identity: wrote {len(records)} live records to {target}")
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


def _console_segment(name: str) -> str:
    console = LABS / name / "logs" / "console.log"
    text = console.read_text(encoding="utf-8", errors="replace") if console.is_file() else ""
    marker = "Starting minecraft server version"
    index = text.rfind(marker)
    return text[index:] if index >= 0 else text[-20000:]


def _provision_smoke(info: dict, smoke_jar: Path | None) -> None:
    run([PY, LAB_SERVER, "stop", "--name", info["name"], "--timeout", "120"], check=False)
    mods = LABS / info["name"] / "mods"
    for pattern in ("smoke-mod*.jar", "load-failure.jar", "missing-dep.jar"):
        for stale in mods.glob(pattern):
            stale.unlink()
    command: list[object] = [
        "provision", "--name", info["name"], "--fabric-api", "--carpet", "--force",
        "--java", str(JAVA), "--jdk", str(JDK),
        "--server-port", str(info["server"]), "--rcon-port", str(info["rcon"]),
        "--vantage-port", str(info["vantage"]), "--bridge-port", str(info["bridge"]),
        "--test-mod", str(AUDIT_JAR), "--mod-jar", str(INTERFACE_JAR),
    ]
    if smoke_jar is not None:
        command += ["--mod-jar", str(smoke_jar)]
    lab(*command)


def _rebuild_jar(reference: Path, dst: Path, mutate: Any) -> Path:
    import zipfile

    with zipfile.ZipFile(reference) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "fabric.mod.json":
                payload = json.loads(data.decode("utf-8"))
                mutate(payload)
                data = json.dumps(payload, indent=2).encode("utf-8")
            info = zipfile.ZipInfo(item.filename, (1980, 1, 1, 0, 0, 0))
            info.compress_type = item.compress_type
            zout.writestr(info, data)
    return dst


def _loud_probe(info: dict, good_jar: Path, probe_dir: Path, name: str, mutate: Any, markers: Sequence[str]) -> dict[str, Any]:
    """Run one real loud-failure probe and record what the server actually did."""
    bad = _rebuild_jar(good_jar, probe_dir / f"{name}.jar", mutate)
    run([PY, LAB_SERVER, "stop", "--name", info["name"], "--timeout", "120"], check=False)
    provision = run([PY, LAB_SERVER, "provision", "--name", info["name"], "--force", "--fabric-api", "--carpet",
                     "--java", str(JAVA), "--jdk", str(JDK),
                     "--server-port", str(info["server"]), "--rcon-port", str(info["rcon"]),
                     "--vantage-port", str(info["vantage"]), "--bridge-port", str(info["bridge"]),
                     "--test-mod", str(AUDIT_JAR), "--mod-jar", str(INTERFACE_JAR),
                     "--mod-jar", str(bad)], check=False, timeout=600)
    start = run([PY, LAB_SERVER, "start", "--name", info["name"], "--wait", "90"], check=False, timeout=150)
    segment = _console_segment(info["name"])
    log = EVIDENCE / "devcap" / f"probe-{name}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(segment[-20000:], encoding="utf-8")
    status = run([PY, LAB_SERVER, "exec", "--name", info["name"], "mcagent-smoke status"], check=False, timeout=60)
    detected = start.returncode != 0 or status.returncode != 0 or any(marker in segment for marker in markers)
    run([PY, LAB_SERVER, "stop", "--name", info["name"], "--timeout", "120"], check=False)
    return {"detected": detected, "provisionExit": provision.returncode, "startExit": start.returncode,
            "statusExit": status.returncode, "markers": [m for m in markers if m in segment],
            "log": str(log)}


def _docs_visible_probe(paths: Sequence[str]) -> tuple[list[str], dict[str, Any]]:
    visible: list[str] = []
    detail: dict[str, Any] = {}
    for relative in paths:
        path = ROOT / relative
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            detail[relative] = {"readable": False, "error": str(error)}
            continue
        visible.append(relative)
        detail[relative] = {"readable": True, "bytes": len(text.encode("utf-8")),
                            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    return visible, detail


async def devcap_phase(args: argparse.Namespace) -> int:
    provenance = require_clean_source("devcap")
    if str(BRIDGE_SOURCE / "src") not in sys.path:
        sys.path.insert(0, str(BRIDGE_SOURCE / "src"))
    facts = read_json(EVIDENCE / "facts-live.json")
    bundle = EVIDENCE / "bundle"
    build_dir = EVIDENCE / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    devcap_dir = EVIDENCE / "devcap"
    devcap_dir.mkdir(parents=True, exist_ok=True)
    for name, link in (("bridge", Path("F:/mc-agent/bridge")), ("agent-loop", Path("F:/mc-agent/agent-loop"))):
        path = ROOT / name
        if not path.exists() and link.is_dir():
            run(["cmd", "/c", "mklink", "/J", str(path), str(link)], check=False)
    target = bundle / "artifacts" / "agent_dev_capability"
    target.mkdir(parents=True, exist_ok=True)
    for info in (SRC, EXP):
        run([PY, LAB_SERVER, "stop", "--name", info["name"], "--timeout", "120"], check=False)
    # stale smoke jars would load beside a broken probe jar and hide its error
    for stale in (LABS / EXP["name"] / "mods").glob("smoke-mod*.jar"):
        stale.unlink()
    for stale in (LABS / EXP["name"] / "mods").glob("load-failure.jar"):
        stale.unlink()
    for stale in (LABS / EXP["name"] / "mods").glob("missing-dep.jar"):
        stale.unlink()

    # ---- real builds: v1 and a same-size v2 with a different marker ----
    jar = build_dir / "smoke-mod.jar"
    build_v1 = build_smoke(jar)
    hash_v1 = sha256_file(jar)
    bytes_v1 = jar.stat().st_size
    build_v2 = build_smoke(jar, marker='"smoke-de2"')
    hash_v2 = sha256_file(jar)
    bytes_v2 = jar.stat().st_size
    if not (bytes_v1 == bytes_v2 and hash_v1 != hash_v2):
        raise RuntimeError(f"the smoke jar update is not same-size different-content: {bytes_v1}/{bytes_v2}")

    # ---- loud failure probes (build/load/dependency) on the exp lab ----
    probe_dir = devcap_dir / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    broken = build_dir / "broken-src"
    shutil.rmtree(broken, ignore_errors=True)
    shutil.copytree(SMOKE_SOURCE, broken)
    broken_java = broken / "src/main/java/dev/mcagent/smoke/SmokeMod.java"
    broken_java.write_text(broken_java.read_text(encoding="utf-8") + chr(10) + "this is not java;" + chr(10), encoding="utf-8")
    broken_build = run([PY, BUILD_MOD, "--source", str(broken), "--lab", EXP["name"],
                        "--out", str(build_dir / "broken.jar"), "--jdk", str(JDK)], check=False, timeout=300)
    probes: dict[str, Any] = {
        "build_errors_detected": {
            "detected": broken_build.returncode != 0,
            "exit": broken_build.returncode,
            "log": str(build_dir / "broken-build.log"),
        }
    }
    (build_dir / "broken-build.log").write_text(broken_build.stdout + broken_build.stderr, encoding="utf-8")
    probes["load_failure_detected"] = _loud_probe(
        EXP, jar, probe_dir, "load-failure",
        lambda payload: payload.setdefault("entrypoints", {}).__setitem__("server", ["dev.mcagent.smoke.DoesNotExist"]),
        ("Could not execute entrypoint", "ClassNotFoundException", "Failed to start",
         "net.fabricmc.loader.impl.FormattedException"),
    )
    probes["missing_dependency_detected"] = _loud_probe(
        EXP, jar, probe_dir, "missing-dep",
        lambda payload: payload.setdefault("depends", {}).__setitem__("mcagent-does-not-exist", "*"),
        ("requires", "which is missing", "Missing or unsupported", "Dependency resolution failed",
         "Could not execute entrypoint"),
    )
    for key in ("build_errors_detected", "load_failure_detected", "missing_dependency_detected"):
        if not probes[key].get("detected"):
            raise RuntimeError(f"the {key} probe did not observe the failure: {probes[key]}")

    # ---- same-size jar update with a real restart, then memory/order ----
    lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)
    v1_jar = build_dir / "smoke-mod-v1.jar"
    v2_jar = build_dir / "smoke-mod-v2.jar"
    build_smoke(v1_jar)
    build_smoke(v2_jar, marker='"smoke-de2"')
    deployed_v1 = sha256_file(v1_jar)
    deployed_v2 = sha256_file(v2_jar)
    if sha256_file(v1_jar) != hash_v1 or sha256_file(v2_jar) != hash_v2:
        raise RuntimeError("the recorded smoke jar hashes do not match the builds")
    _provision_smoke(EXP, v1_jar)
    lab("start", "--name", EXP["name"], "--wait", "300")
    v1_status = rcon(EXP["name"], "mcagent-smoke status")
    _provision_smoke(EXP, v2_jar)
    lab("start", "--name", EXP["name"], "--wait", "300")
    v2_status = rcon(EXP["name"], "mcagent-smoke status")
    console_text = (LABS / EXP["name"] / "logs" / "console.log").read_text(encoding="utf-8", errors="replace")
    marker_loaded = "build=smoke-de2" in v2_status or "build=smoke-de2" in _console_segment(EXP["name"])
    if not marker_loaded:
        raise RuntimeError(f"the v2 marker was not loaded: {v2_status[-300:]}")

    # Restore the fork, then prove that a real restart reconstructs the same
    # machine entity memory/order/NBT.  Both sides of the comparison are
    # snapshots from this run; the comparison is filtered to the machine
    # entities because the world still contains init-time falling item
    # leftovers that cannot hold a position across a startup tick.  The filter
    # is symmetric and recorded, and the bridge's whole-world strict verify is
    # not used for this test.
    start_bridge(EXP["name"], EXP["bridge"])
    fork_snapshot = str((facts.get("stages", {}).get("fork") or {}).get("snapshotDir") or "")
    if not (fork_snapshot and Path(fork_snapshot).is_dir()):
        raise RuntimeError("facts-live.json has no fork snapshot for the restart memory test")
    memory_snapshot = devcap_dir / "memory-snapshot"
    shutil.rmtree(memory_snapshot, ignore_errors=True)
    shutil.copytree(fork_snapshot, memory_snapshot)
    entity_rows = [
        json.loads(line)
        for line in (memory_snapshot / "entities.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    kept = [row for row in entity_rows if row.get("type") != "minecraft:item"]
    (memory_snapshot / "entities.jsonl").write_text(
        chr(10).join(json.dumps(row) for row in kept) + chr(10), encoding="utf-8"
    )
    meta = read_json(memory_snapshot / "meta.json")
    meta["entities"] = len(kept)
    meta["orderHash"] = hashlib.sha256(
        ":".join(str(row.get("uuid")) for row in kept).encode("utf-8")
    ).hexdigest()[:16]
    meta["memoryFilter"] = "carts + hover seat; falling init items excluded"
    write_json(memory_snapshot / "meta.json", meta)
    api = await connect(EXP["bridge"])
    rcon(EXP["name"], "tick freeze")
    restore_result, restore_record = await call(api, "restore",
                                                {"directory": str(memory_snapshot), "dry_run": False,
                                                 "target": EXP["name"], "expect_instance": "server",
                                                 "expect_world_dir": str(LABS / EXP["name"] / "world"),
                                                 "replace_existing": True, "verify": False},
                                                timeout=900)
    devcap_dir.joinpath("restore-for-memory.json").write_text(
        json.dumps({"restore": restore_record}, indent=2), encoding="utf-8"
    )
    if not restore_result or restore_result.get("error") or restore_result.get("verdict") not in ("unverified", "ok"):
        raise RuntimeError(f"the devcap memory restore failed: {restore_record}")
    rcon(EXP["name"], "kill @e[type=minecraft:item]")
    time.sleep(0.5)
    pre_result, pre_record = await call(api, "snapshot",
                                        {"name": f"devcap-{facts['runId']}-memory-pre", "radius": 0},
                                        timeout=600)
    pre_dir = str((pre_result or {}).get("snapshotDir") or (pre_result or {}).get("dir") or "")
    if not pre_dir:
        raise RuntimeError(f"the pre-restart memory snapshot failed: {pre_record}")
    await api.close()
    stop_bridges()
    lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)
    lab("start", "--name", EXP["name"], "--wait", "300")
    rcon(EXP["name"], "tick freeze")
    rcon(EXP["name"], "kill @e[type=minecraft:item]")
    restart_status = rcon(EXP["name"], "mcagent-smoke status")
    start_bridge(EXP["name"], EXP["bridge"])
    api = await connect(EXP["bridge"])
    post_result, post_record = await call(api, "snapshot",
                                          {"name": f"devcap-{facts['runId']}-memory-post", "radius": 0},
                                          timeout=600)
    state_result, state_record = await call(api, "state", {})
    await api.close()
    stop_bridges()
    post_dir = str((post_result or {}).get("snapshotDir") or (post_result or {}).get("dir") or "")
    if not post_dir:
        raise RuntimeError(f"the post-restart memory snapshot failed: {post_record}")
    pre = gate.fork_verify.load_snapshot(Path(pre_dir))
    post = gate.fork_verify.load_snapshot(Path(post_dir))
    expected_list = [entity for entity in pre.entities if entity.type != "minecraft:item"]
    actual_list = [entity for entity in post.entities if entity.type != "minecraft:item"]
    order_expected = gate.fork_verify.order_hash(expected_list)
    order_actual = gate.fork_verify.order_hash(actual_list)
    order_match = len(expected_list) == len(actual_list) and order_expected == order_actual
    nbt_ok = bool(expected_list) and len(expected_list) == len(actual_list) and all(
        left.nbt == right.nbt for left, right in zip(expected_list, actual_list)
    )
    pos_ok = bool(expected_list) and all(
        left.pos is not None
        and right.pos is not None
        and all(abs(a - b) <= 1e-3 for a, b in zip(left.pos, right.pos))
        for left, right in zip(expected_list, actual_list)
    )
    memory_rebuilt = bool(order_match and nbt_ok and pos_ok)
    memory_log = devcap_dir / "memory-restart-verify.json"
    memory_log.write_text(json.dumps({
        "filter": "carts + hover seat; falling item entities excluded on both sides",
        "preSnapshot": pre_dir,
        "postSnapshot": post_dir,
        "expected": {"count": len(expected_list), "orderHash": order_expected},
        "actual": {"count": len(actual_list), "orderHash": order_actual},
        "orderMatch": bool(order_match),
        "nbtMatch": bool(nbt_ok),
        "positionMatch": bool(pos_ok),
        "state": state_record,
    }, indent=2), encoding="utf-8")
    if not memory_rebuilt:
        raise RuntimeError(f"the post-restart memory/order comparison failed: {memory_log}")
    expected_entities = len(expected_list)
    matched_entities = len(actual_list)

    lab("stop", "--name", EXP["name"], "--timeout", "120", check=False)

    target.joinpath("jar-update.json").write_text(json.dumps({
        "case": "same_size_different_content",
        "old_sha256": deployed_v1,
        "new_sha256": deployed_v2,
        "bytes": bytes_v1,
        "loaded_sha256": deployed_v2,
        "runtime_evidence": str(LABS / EXP["name"] / "logs" / "console.log"),
        "detail": {"same_size": bytes_v1 == bytes_v2 and deployed_v1 != deployed_v2,
                   "loaded_new_marker": marker_loaded,
                   "v1_status": v1_status[-300:], "v2_status": v2_status[-300:],
                   "restart_status": restart_status[-300:]},
    }, indent=2), encoding="utf-8")

    # no ROM logic: scan the smoke source for forbidden tokens
    scan = run(["findstr", "/s", "/i", "/n", "minecart note_block answer", str(SMOKE_SOURCE / "src")],
               check=False, timeout=60)
    forbidden = [line for line in (scan.stdout + scan.stderr).splitlines() if line.strip()]
    no_rom_logic = len(forbidden) == 0
    if not no_rom_logic:
        raise RuntimeError(f"the smoke mod source looks ROM-specific: {forbidden[:3]}")
    status_file = devcap_dir / "smoke-v2-status.txt"
    status_file.write_text(v2_status, encoding="utf-8")
    target.joinpath("smoke-mod.json").write_text(json.dumps({
        "mod_id": "mc-agent-lab-smoke",
        "version": "0.1.0",
        "built_sha256": deployed_v2,
        "deployed_sha256": deployed_v2,
        "server_log_ref": str(LABS / EXP["name"] / "logs" / "console.log"),
        "sample_output_ref": str(status_file),
        "build_errors_detected": probes["build_errors_detected"]["detected"],
        "load_failure_detected": probes["load_failure_detected"]["detected"],
        "missing_dependency_detected": probes["missing_dependency_detected"]["detected"],
        "memory_state_rebuilt_after_restart": bool(memory_rebuilt),
        "restart_evidence_ref": str(memory_log),
        "no_rom_logic": no_rom_logic,
        "detail": {"probes": probes, "memory": {
            "expected": expected_entities, "matched": matched_entities,
            "orderHashMatch": bool(order_match),
            "nbtMatch": bool(nbt_ok),
            "positionMatch": bool(pos_ok),
            "preSnapshot": pre_dir,
            "postSnapshot": post_dir,
            "report": str(memory_log),
        }, "forbidden_scan": {"command": "findstr /s /i /n minecart note_block answer src", "hits": 0}},
    }, indent=2), encoding="utf-8")

    # ---- tool environment from the real preflight (no constants) ----
    preflight_out = EVIDENCE / "preflight"
    config = ROOT / "examples" / "coldstart" / "harness-pi.json"
    preflight = run([PY, ROOT / "tools" / "harness_preflight.py", "run", "--config", str(config),
                     "--out", str(preflight_out), "--force", "--checks",
                     "harness_identity,terminal,filesystem,ports,source_fetch,build_install,bridge_cli,bridge_smoke",
                     "--set", f"build.java={JAVA}",
                     "--set", "bridge.command=F:/mc-agent/.venv/Scripts/mc-bridge.exe"],
                    check=False, timeout=1800)
    preflight_json = preflight_out / "preflight.json"
    if not preflight_json.is_file():
        raise RuntimeError(f"the harness preflight wrote no report: exit {preflight.returncode}")
    preflight_data = read_json(preflight_json)
    checks = {item.get("id"): item.get("status") for item in preflight_data.get("checks", [])}
    mapping = {
        "terminal": ["terminal"],
        "file": ["filesystem"],
        "filesystem_write": ["filesystem"],
        "source_access": ["source_fetch"],
        "build": ["build_install"],
        "install": ["build_install"],
        "mcp_or_cli": ["bridge_cli"],
        "lab_manage": ["ports", "bridge_smoke"],
    }
    tools = {key: all(checks.get(name) == "PASS" for name in needed) for key, needed in mapping.items()}
    if not all(tools.values()):
        raise RuntimeError(f"the harness preflight did not prove every tool: {tools} vs {checks}")
    docs_visible, docs_detail = _docs_visible_probe(
        ("docs/stage1-gate.md", "docs/minecart-rom-runbook.md", "examples/minecart-rom/fixture-spec.json")
    )
    if not docs_visible:
        raise RuntimeError("the docs visibility probe read nothing")
    harness = preflight_data.get("harness") or {}
    target.joinpath("tool-environment.json").write_text(json.dumps({
        "harness": str(harness.get("name") or ""),
        "model": str(harness.get("model") or ""),
        "docs_visible": docs_visible,
        "tools": tools,
        "detail": {"preflight": str(preflight_json), "checks": checks, "exit": preflight.returncode,
                   "mapping": mapping, "docs": docs_detail,
                   "environment": preflight_data.get("environment")},
    }, indent=2), encoding="utf-8")

    # ---- instance isolation from real probes ----
    rows: list[dict[str, Any]] = []
    for info, role in ((SRC, "source_audit"), (EXP, "experiment")):
        run([PY, LAB_SERVER, "stop", "--name", info["name"], "--timeout", "120"], check=False)
        started = run([PY, LAB_SERVER, "start", "--name", info["name"], "--wait", "300"], check=False, timeout=600)
        segment = _console_segment(info["name"])
        segment_path = devcap_dir / f"isolation-{info['name']}.log"
        segment_path.write_text(segment[-20000:], encoding="utf-8")
        if started.returncode != 0:
            raise RuntimeError(f"the {info['name']} restart failed: {segment[-400:]}")
        start_bridge(info["name"], info["bridge"])
        api = await connect(info["bridge"])
        state_result, state_record = await call(api, "state", {})
        await api.close()
        stop_bridges()
        lab("stop", "--name", info["name"], "--timeout", "120", check=False)
        state_path = devcap_dir / f"isolation-{info['name']}-state.json"
        state_path.write_text(json.dumps(state_record, indent=2), encoding="utf-8")
        world_dir = str((state_result or {}).get("worldDir") or "")
        try:
            expected_path = (LABS / info["name"] / "world").resolve()
            actual_path = Path(world_dir).resolve() if world_dir else None
        except OSError:
            actual_path = None
        resolves = actual_path is not None and actual_path == expected_path
        if not resolves:
            raise RuntimeError(f"{info['name']} resolved world {world_dir!r}, expected {expected_path!r}")
        rows.append({
            "instance_id": info["name"], "role": role,
            "world_dir": f"labs/{info['name']}/world",
            "rcon_port": info["rcon"], "bridge_port": info["bridge"],
            "restarted": True, "resolves_correct_world": True, "conflicting_instance": False,
            "detail": {"world_dir_abs": world_dir, "state": str(state_path),
                       "restart_log": str(segment_path), "restart_exit": started.returncode},
        })
    conflict = run([PY, LAB_SERVER, "provision", "--name", "rom13-conflict", "--force", "--void",
                    "--java", str(JAVA), "--jdk", str(JDK),
                    "--server-port", str(SRC["server"]), "--rcon-port", str(SRC["rcon"])],
                   check=False, timeout=600)
    conflict_log = devcap_dir / "isolation-conflict.log"
    conflict_log.write_text(conflict.stdout + conflict.stderr, encoding="utf-8")
    conflict_rejected = conflict.returncode != 0 and "already recorded" in (conflict.stdout + conflict.stderr)
    shutil.rmtree(LABS / "rom13-conflict", ignore_errors=True)
    if not conflict_rejected:
        raise RuntimeError(f"a conflicting instance was not rejected: {conflict.stdout[-300:]}")
    for row in rows:
        row["detail"]["conflict_probe"] = {"command": "lab_server.py provision (same ports)",
                                           "exit": conflict.returncode, "log": str(conflict_log),
                                           "rejected": True}
    with target.joinpath("instance-isolation.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + chr(10))
    close_phase("devcap", provenance)
    write_json(EVIDENCE / "facts-devcap.json", {
        "markerLoaded": marker_loaded, "preflightExit": preflight.returncode,
        "memoryRebuilt": bool(memory_rebuilt), "expectedEntities": expected_entities,
        "matchedEntities": matched_entities, "conflictRejected": conflict_rejected,
        "smokeJar": {"sha256": deployed_v2, "bytes": bytes_v2, "path": str(v2_jar)},
        "probeFlags": {
            "build_errors_detected": probes["build_errors_detected"]["detected"],
            "load_failure_detected": probes["load_failure_detected"]["detected"],
            "missing_dependency_detected": probes["missing_dependency_detected"]["detected"],
        },
    })
    say(f"devcap: probes ok, v1={deployed_v1[:12]} v2={deployed_v2[:12]} memory matched={matched_entities}")
    return 0


# --------------------------------------------------------------------------- trace


def trace_phase(args: argparse.Namespace) -> int:
    provenance = require_clean_source("trace")
    facts = read_json(EVIDENCE / "facts-live.json")
    bundle = EVIDENCE / "bundle"
    run_dir = Path(facts["runDir"])
    positive = Path(facts["stages"]["positive"]["logCopy"])
    rows = [json.loads(line) for line in positive.read_text(encoding="utf-8").splitlines() if line.strip()]
    agent_events = [row for row in rows if row.get("phase") == "experiment"]

    # Missing-log detection with the real tools; the compose phase maps the
    # causal joins once the canonical audit export exists.
    missing_dir = EVIDENCE / "missing-log"
    missing_dir.mkdir(parents=True, exist_ok=True)
    trace_missing = run([PY, RUN_TRACE, "validate", "--run-dir", str(missing_dir), "--json"], check=False, timeout=120)
    (missing_dir / "trace-missing.log").write_text(trace_missing.stdout + trace_missing.stderr, encoding="utf-8")
    audit_missing = run([VENV_PY, ROOT / "tools" / "run_audit.py", "run", "--run-dir", str(missing_dir), "--json"], check=False, timeout=300)
    (missing_dir / "audit-missing.log").write_text(audit_missing.stdout + audit_missing.stderr, encoding="utf-8")
    trace_dir = bundle / "artifacts" / "trace_persistence"
    trace_dir.mkdir(parents=True, exist_ok=True)
    detections = [
        {"case": "trace_missing", "detected": trace_missing.returncode != 0,
         "exit_nonzero": trace_missing.returncode != 0, "message_ref": str(missing_dir / "trace-missing.log")},
        {"case": "audit_missing", "detected": audit_missing.returncode != 0,
         "exit_nonzero": audit_missing.returncode != 0, "message_ref": str(missing_dir / "audit-missing.log")},
    ]
    with (trace_dir / "missing-log-detection.jsonl").open("w", encoding="utf-8") as handle:
        for row in detections:
            handle.write(json.dumps(row) + chr(10))
    # Real overflow probe: a syntactically valid log whose audit_end reports a
    # truncated stream must make the verifier refuse a pass.
    overflow_dir = EVIDENCE / "overflow-probe"
    overflow_dir.mkdir(parents=True, exist_ok=True)
    overflow_log = overflow_dir / "audit-truncated.jsonl"
    overflow_rows = [
        {"seq": 1, "tick": 0, "run": "overflow-probe", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "session_start"},
        {"seq": 2, "tick": 0, "run": "overflow-probe", "inst": "exp-1", "session": "s1", "phase": "ready", "type": "audit_ready"},
        {"seq": 3, "tick": 10, "run": "overflow-probe", "inst": "exp-1", "session": "s1", "phase": "end", "type": "audit_end", "status": "incomplete", "truncated": True, "bytes": 999999, "incompleteReasons": ["maxBytes"]},
    ]
    overflow_log.write_text("".join(json.dumps(row) + chr(10) for row in overflow_rows), encoding="utf-8")
    overflow_check = run(
        [PY, ROOT / "tools" / "minecart_audit.py", "check", "--log", str(overflow_log), "--json"],
        check=False, timeout=120,
    )
    overflow_text = overflow_check.stdout + overflow_check.stderr
    (overflow_dir / "overflow-check.log").write_text(overflow_text, encoding="utf-8")
    overflow_report = {}
    if "{" in overflow_check.stdout:
        try:
            overflow_report = json.loads(overflow_check.stdout[overflow_check.stdout.find("{"):])
        except ValueError:
            overflow_report = {}
    overflow_detected = overflow_check.returncode != 0 or str(overflow_report.get("verdict")) not in ("pass",)
    if not overflow_detected:
        raise RuntimeError(f"the truncated-log overflow probe passed the verifier: {overflow_report}")
    write_json(EVIDENCE / "facts-trace.json", {
        "experimentPhaseRows": len(agent_events),
        "canonicalAgentEventsNote": "canonical agent events are counted by the compose export; "
                                    "experimentPhaseRows counts raw experiment-phase rows incl. samples",
        "detections": detections,
        "missingLogDetected": all(row["detected"] for row in detections),
        "overflowDetected": overflow_detected,
        "overflowProbe": {
            "log": str(overflow_log),
            "exit": overflow_check.returncode,
            "verdict": overflow_report.get("verdict"),
            "message_ref": str(overflow_dir / "overflow-check.log"),
        },
    })
    close_phase("trace", provenance)
    say(f"trace: {len(agent_events)} agent events, missing-log detection={all(r['detected'] for r in detections)}")
    return 0


# --------------------------------------------------------------------------- smoke


def smoke_phase(args: argparse.Namespace) -> int:
    provenance = require_clean_source("smoke")
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
    devcap = read_json(EVIDENCE / "facts-devcap.json") if (EVIDENCE / "facts-devcap.json").is_file() else {}
    identity_records = len((facts.get("stages", {}).get("identity") or {}).get("records") or [])
    restore = facts.get("stages", {}).get("restore") or {}
    restore_ok = bool((restore.get("verified") or {}).get("ok")) and bool((restore.get("applied") or {}).get("ok"))
    restart_logs = sorted((EVIDENCE / "devcap").glob("isolation-*.log"))
    suites.append({"name": "smoke_offline", "command": "python tools/smoke_offline.py",
                   "status": "pass" if offline.returncode == 0 else "fail", "checks": 1, "log_ref": str(offline_log)})
    suites.append({"name": "lab_boot", "command": "tools/lab_server.py start --name rom13-src --wait 300",
                   "status": "pass" if restart_logs else "fail", "checks": 1,
                   "log_ref": str(restart_logs[0]) if restart_logs else str(LOGS)})
    suites.append({"name": "fake_player_mcp", "command": "mc-bridge call player (live seat probe)",
                   "status": "pass" if identity_records >= 5 else "fail", "checks": identity_records,
                   "log_ref": str(EVIDENCE / "bundle" / "artifacts" / "player_context" / "identity-records.jsonl")})
    suites.append({"name": "snapshot_restore", "command": "bridge restore + verify + post-restart memory",
                   "status": "pass" if restore_ok and devcap.get("memoryRebuilt") else "fail",
                   "checks": int(devcap.get("matchedEntities") or 0),
                   "log_ref": str(EVIDENCE / "bundle" / "artifacts" / "restore_fidelity" / "restore-record.json")})
    suites.append({"name": "test_mod_load", "command": "lab_server verify --require-vantage + smoke marker",
                   "status": "pass" if devcap.get("markerLoaded") else "fail", "checks": 1,
                   "log_ref": str(EVIDENCE / "devcap" / "smoke-v2-status.txt")})
    if any(suite["status"] != "pass" for suite in suites):
        raise RuntimeError(f"a smoke suite is not pass: {[s for s in suites if s['status'] != 'pass']}")
    write_json(target / "smoke-report.json", {"suites": suites})
    close_phase("smoke", provenance)

    # fixture calibration from the accepted spec plus the live like-for-like
    # parity run (same copied world, same presses, no audit mod)
    spec = read_json(FIXTURE_SPEC)
    machine = spec["machine"]
    control = (facts.get("stages", {}).get("control") or {})
    if not control.get("consistent") or not (control.get("control") or {}).get("observations"):
        raise RuntimeError("facts-live.json has no completed no-mod parity run")
    positive = facts["stages"]["positive"]
    positive_log = Path(positive["logCopy"])
    positive_rows = [
        json.loads(line)
        for line in positive_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    hooks = next(
        (row.get("hooks") for row in positive_rows if row.get("type") == "audit_end"),
        {},
    )
    overhead_nanos = sum(float(entry.get("totalNanos") or 0) for entry in hooks.values()) if isinstance(hooks, dict) else 0.0
    hook_overhead_ms = round(overhead_nanos / 1_000_000.0, 3)
    if not hooks:
        raise RuntimeError("the positive audit log has no raw hook counters; overhead cannot be attested")
    calibration = positive.get("calibration") or {}
    write_json(target / "fixture-validity.json", {
        "input_semantics": machine["input"]["interaction"],
        "stack_positions": [machine["stack"]["spawn"], machine["stack"]["rest_pos"]],
        "output_boundary": machine["output_boundary"],
        "void_window_ticks": int(spec["scene"]["pop_to_removal_ticks_estimate"][1]),
        "end_condition": spec["scene"]["end_condition"],
        "timeout_s": int(spec["scene"]["timeout_seconds"]),
        "hook_overhead_ms": hook_overhead_ms,
        "hook_overhead_scope": "total session hook time across all hooks "
                               "(sum of per-hook totalNanos / 1e6), not per-call or per-tick",
        "with_mod_without_mod_consistent": True,
        "detail": {
            "spec": str(FIXTURE_SPEC),
            "cycle_ticks": machine["cycle_ticks"],
            "audited_observations": calibration.get("observations"),
            "control_observations": (control.get("control") or {}).get("observations"),
            "hook_counters_nanos": hooks,
            "hook_overhead_source": str(positive_log),
            "parity": "same world copy, same player/presses/sprints, with vs without the audit mod",
        },
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
    provenance = require_clean_source("compose")
    import stage1_evidence as evidence

    facts = read_json(EVIDENCE / "facts-live.json")
    bundle = EVIDENCE / "bundle"

    # ---- refresh the fixture map so the command-block scan covers the lab --
    run([PY, str(ROOT / "examples" / "minecart-rom" / "runner" / "minecart_rom.py"), "evidence",
         "--out", str(bundle),
         "--records", str(facts["fixtureRecords"]),
         "--instance-id", "rom13-src",
         "--cache", str(LABS / "_cache" / "maps"),
         "--world-dir", str(LABS / EXP["name"] / "world"),
         "--source-world", str(SOURCE_SAVE)], check=True)

    # ---- audit artifacts from the finalized same-run logs -----------------
    stage1_export = ROOT / "tools" / "minecart_audit.py"
    positives = facts["stages"]["positive"]
    child = facts["stages"]["sourceChild"]
    trace_facts = read_json(EVIDENCE / "facts-trace.json") if (EVIDENCE / "facts-trace.json").is_file() else {}
    devcap = read_json(EVIDENCE / "facts-devcap.json") if (EVIDENCE / "facts-devcap.json").is_file() else {}
    control = facts["stages"]["control"]
    capability = {
        "control_parity": (control.get("control") or {}).get("observations"),
        "audited_parity": (positives.get("calibration") or {}).get("observations"),
        "same_world_copy": True,
        "same_presses_and_sprints": True,
    }
    audit_facts = {
        "fixture_behavior_unchanged": {
            "value": bool(control.get("consistent")),
            "evidence": capability,
        },
        "agent_mod_coexists": {
            "value": bool(
                positives.get("verdict") == "pass"
                and (positives.get("calibration") or {}).get("observations")
                and (positives["calibration"]["observations"][-1].get("removed_total") or 0) >= 1
                and facts["stages"]["restore"].get("matched")
            ),
            "evidence": {
                "audit_verdict": positives.get("verdict"),
                "restored_entities": facts["stages"]["restore"].get("matched"),
                "interface_sha256": INTERFACE_SHA,
                "audit_jar_sha256": sha256_file(AUDIT_JAR),
                "smoke_mod": (devcap.get("smokeJar") or {}),
                "smoke_mod_probe_flags": devcap.get("probeFlags"),
                "calibration": positives.get("calibration", {}).get("observations"),
                "note": "the self-built smoke mod, the interface mod and the audit mod were loaded together "
                        "while the restored machine was operated by the real fixture player and a cart exited "
                        "and was removed",
            },
        },
        "missing_log_detection": {
            "value": bool(trace_facts.get("missingLogDetected")),
            "evidence": trace_facts.get("detections"),
        },
        "overflow_detection": {
            "value": bool(trace_facts.get("overflowDetected")),
            "evidence": trace_facts.get("overflowProbe"),
        },
        "flush_receipt": positives["flush"],
    }
    facts_path = EVIDENCE / "audit-facts.json"
    write_json(facts_path, audit_facts)
    export_cmd = [
        PY, str(stage1_export), "export",
        "--out", str(bundle / "artifacts" / "independent_test_mod"),
        "--parent-run", positives["runId"],
        "--jar", str(AUDIT_JAR), "--version", "0.1.0",
        "--interface-sha", INTERFACE_SHA, "--bridge-commit", BRIDGE_MERGED,
        "--facts", str(facts_path),
        "--status", positives["status"],
        "--loaded-in", "source_audit,experiment",
        "--events", f"{positives['logCopy']},{positives['runId']},rom13-exp,minecraft:overworld",
        "--events", f"{child['logCopy']},{positives['runId']},rom13-src,minecraft:overworld",
    ]
    negatives = {
        "no_interaction": "no_interaction",
        "wrong_position": "wrong_position",
        "marker_only": "marker_only",
        "answer_only": "answer_only",
        "attack_only": "attack_only",
        "environment_only": "environment_only",
    }
    for key, case in negatives.items():
        log = LABS / "rom13-neg" / "mc-audit" / f"audit-{facts['runId']}-neg-{key}.jsonl"
        export_cmd += ["--negative", f"{case}={log}"]
    # mixed-trigger regression stays a supporting probe (it passes the
    # verifier because the use is a real operation); assert the raw count.
    mixed = (facts["stages"].get("negatives") or {}).get("attack_then_use") or {}
    if not mixed.get("exactly_one_processed"):
        raise RuntimeError(f"attack-then-use mixed-trigger regression failed: {mixed}")
    mixed_log = Path(str(mixed.get("log") or ""))
    if mixed_log.is_file():
        mixed_copy = EVIDENCE / "audit" / f"attack-then-use-{facts['runId']}.jsonl"
        shutil.copyfile(mixed_log, mixed_copy)
        mixed["logCopy"] = str(mixed_copy)
        mixed["logCopySha256"] = sha256_file(mixed_copy)
        facts["stages"]["negatives"]["attack_then_use"] = mixed
        write_json(EVIDENCE / "facts-live.json", facts)
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
    restore_flags = restore.get("flags") or {}
    for key in ("chunks_loaded", "tick_controlled", "pause_state_preserved", "issued_is_not_success"):
        if not restore_flags.get(key):
            raise RuntimeError(f"the real restore flag probe {key!r} is missing or false: {restore_flags}")
    write_json(target / "restore-record.json", {
        "endpoint": {"source": "rom13-src", "target": "rom13-exp",
                     "target_resolved": bool(restore["applied"].get("ok")),
                     "wrong_target_rejected": any(r["case"] == "wrong_endpoint" and r["passed"] for r in facts["failureCases"])},
        "dimension": applied_result.get("dimension") or "minecraft:overworld",
        "chunks_loaded": bool(restore_flags["chunks_loaded"]),
        "tick_controlled": bool(restore_flags["tick_controlled"]),
        "duplicates_pre_existing": duplicates,
        "commands_issued": int(applied_result.get("issued", 0) or 0),
        "commands_failed": len(failed_commands),
        "partial_failure": bool(applied_result.get("partial")),
        "pause_state_preserved": bool(restore_flags["pause_state_preserved"]),
        "issued_is_not_success": bool(restore_flags["issued_is_not_success"]),
        "flags_evidence": restore_flags.get("evidence"),
        "detail": {"applied": restore["applied"], "verified": restore["verified"],
                   "restored": applied_result.get("count"),
                   "tick_control": "tick freeze before restore; tick unfreeze after verify",
                   "flag_probes": "post-restore entity visibility + tick query + issued/matched counts"},
    })
    source_before = facts.get("sourceWorldBefore") or {}
    source_after = facts.get("sourceWorldAfter") or {}
    if not source_before.get("tree_sha256") or not source_after.get("tree_sha256"):
        raise RuntimeError("the run has no separate before/after source-world observations")
    write_json(target / "source-unchanged.json", {
        "before_tree_sha256": source_before["tree_sha256"],
        "after_tree_sha256": source_after["tree_sha256"],
        "baseline_tree_sha256": SOURCE_BASELINE_SHA256,
        "unchanged": bool(
            source_before["tree_sha256"] == source_after["tree_sha256"] == SOURCE_BASELINE_SHA256
        ),
        "hash_tool": "tools/stage1_gate.py hash-tree",
        "exclusions": list(evidence.gate.DEFAULT_TREE_EXCLUSIONS),
        "detail": {"path": str(SOURCE_SAVE), "before": source_before, "after": source_after},
    })
    with (target / "failure-cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in facts["failureCases"]:
            handle.write(json.dumps({k: row[k] for k in ("case", "injected", "expected", "observed", "passed")}) + "\n")

    # ---- joins with receipts and bounded causal chains --------------------
    canonical_path = bundle / "artifacts" / "independent_test_mod" / "audit-events.jsonl"
    canonical_rows = [json.loads(line) for line in canonical_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    trajectory_rows = [
        json.loads(line)
        for line in Path(facts["trajectory"]["path"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    call_arguments = {
        row.get("call_id"): (row.get("arguments") if isinstance(row.get("arguments"), dict) else {})
        for row in trajectory_rows
        if row.get("record") == "call"
    }
    windows = read_json(Path(facts["trajectory"]["windowsPath"]))["windows"]
    by_instance: dict[str, list[dict[str, Any]]] = {}
    for window in windows:
        by_instance.setdefault(str(window.get("instance")), []).append(window)
    for instance_windows in by_instance.values():
        instance_windows.sort(key=lambda item: item["started"])

    def press_receipt(instance: str, wall_ms: float) -> dict[str, Any] | None:
        chosen = None
        next_start = None
        for window in by_instance.get(instance, []):
            if window["started"] * 1000 <= wall_ms:
                chosen = window
                next_start = None
            elif chosen is not None:
                next_start = window["started"] * 1000
                break
        if chosen is None:
            return None
        command = str((call_arguments.get(chosen["call_id"]) or {}).get("command") or "")
        if not re.search(r"\bplayer\s+\S+\s+use\b", command, re.IGNORECASE):
            return None
        if next_start is not None and wall_ms >= next_start:
            return None
        return {"call_id": chosen["call_id"], "command": command,
                "started_ms": int(chosen["started"] * 1000), "ended_ms": int(chosen["ended"] * 1000),
                "next_start_ms": int(next_start) if next_start is not None else None}

    def identity_key(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        raw = detail.get("raw") if isinstance(detail.get("raw"), dict) else {}
        return (
            row.get("run_id"), row.get("instance_id"), row.get("dimension"),
            row.get("session") or detail.get("session") or raw.get("session"),
        )

    by_seq = sorted(
        (row for row in canonical_rows if isinstance(row.get("seq"), int)),
        key=lambda row: int(row["seq"]),
    )
    joins: list[dict[str, Any]] = []
    call_by_event: dict[str, str] = {}
    proof_base = {"producer": "stage1_fullgate driver",
                  "basis": "recorded RCON receipt + raw audit sequence and ticks",
                  "clock": "receipt window [call start, next recorded call)"}
    for row in by_seq:
        if row.get("phase") != "agent" or row.get("event") not in ("input_attempt", "input_processed"):
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        wall = detail.get("wall")
        if not isinstance(wall, (int, float)):
            continue
        receipt = press_receipt(str(row.get("instance_id")), float(wall))
        if receipt is None:
            continue
        proof = {
            **proof_base,
            "kind": "direct",
            "actor": row.get("actor_uuid"),
            "max_event_latency_ms": 2000,
            "receipt_command": receipt["command"],
        }
        joins.append({
            "call_id": receipt["call_id"],
            "run_id": row.get("run_id"),
            "instance_id": row.get("instance_id"),
            "dimension": row.get("dimension"),
            "audit_ref": {"event_id": row.get("event_id"), "tick": row.get("tick")},
            "proof": proof,
        })
        call_by_event[str(row.get("event_id"))] = receipt["call_id"]

    def last_predecessor(row: dict[str, Any], kinds: set[str], *, cart_uuid: str | None = None) -> dict[str, Any] | None:
        found = None
        for candidate in by_seq:
            if candidate.get("event") not in kinds:
                continue
            if identity_key(candidate) != identity_key(row):
                continue
            if int(candidate.get("seq", -1)) >= int(row.get("seq", -1)):
                continue
            if cart_uuid is not None and candidate.get("cart_uuid") != cart_uuid:
                continue
            found = candidate
        return found

    for row in by_seq:
        if row.get("phase") != "agent":
            continue
        event = row.get("event")
        if event == "cart_emitted":
            predecessor = last_predecessor(row, {"input_processed"})
            rule = "input_to_emission"
            max_delta = EMISSION_MAX_TICK_DELTA
        elif event == "cart_removed":
            predecessor = last_predecessor(row, {"cart_emitted"}, cart_uuid=str(row.get("cart_uuid")))
            rule = "emission_to_removal"
            max_delta = REMOVAL_MAX_TICK_DELTA
        else:
            continue
        if predecessor is None or str(predecessor.get("event_id")) not in call_by_event:
            continue
        call_id = call_by_event[str(predecessor.get("event_id"))]
        joins.append({
            "call_id": call_id,
            "run_id": row.get("run_id"),
            "instance_id": row.get("instance_id"),
            "dimension": row.get("dimension"),
            "audit_ref": {"event_id": row.get("event_id"), "tick": row.get("tick")},
            "proof": {
                **proof_base,
                "kind": "chain",
                "rule": rule,
                "derived_from_event_id": predecessor.get("event_id"),
                "max_tick_delta": max_delta,
            },
        })
        call_by_event[str(row.get("event_id"))] = call_id
    join_unmatched = sum(
        1
        for row in by_seq
        if row.get("phase") == "agent" and str(row.get("event_id")) not in call_by_event
    )
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
                {"run_id": run["run_id"], "instance_id": "rom13-src"}
                for run in (facts["stages"].get("initRuns") or [])
            ],
            "source_world": {
                "label": "Minecart ROM test", "path": str(SOURCE_SAVE),
                "before_tree_sha256": source_before["tree_sha256"],
                "after_tree_sha256": source_after["tree_sha256"],
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
    close_phase("compose", provenance)
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
        return asyncio.run(devcap_phase(args))
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
