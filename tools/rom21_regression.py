#!/usr/bin/env python3
"""Live issue-#21 regression driver for the Minecart ROM.

The regression freezes the proven issue-#20 cold-start *executables* (the
initialization/restore recipe, the agent logger, the operation sequence, the
log parsing and the result assertions) into a repeatable real-game test.  It
does **not** call a model: every run is a fresh real Minecraft instance that
is initialized, forked, guarded-restored, operated through the note block,
captured by an independent agent logger and judged by the unchanged issue-#18
audit mod.

One run, in order:

1. ``source``     - verify the read-only source save and the map artifact;
2. ``fixture``    - provision a fresh source lab from the committed map world,
                     start it and initialize the sealed cart program with
                     ``minecart_rom.py init`` (test-side input, no agent);
3. ``jars``       - build the parameterized logger from the committed source
                     (both same-size iteration jars when requested);
4. ``fork``       - snapshot and fork the unoperated ready state through the
                     clean bridge (``4116ebb`` pinned);
5. ``experiment`` - provision a fresh experiment lab from the fork, deploy the
                     independent audit mod and the logger as required mods,
                     start, guarded-restore (dry run, apply, verify) and park
                     the task player on the hover seat;
6. ``operate``    - open the audit experiment window and press the note block
                     once per cart, paced by the regression's own logger (never
                     by the audit oracle), until every cart is removed by the
                     natural void;
7. ``answer``     - read the agent logger, derive the ordered answer, run the
                     independent ``minecart_audit.py check`` and the offline
                     semantic verifier (``tools/rom21_verify.py``);
8. ``iterate``    - optional: prove a same-size jar iteration is replaced by
                     hash (lab refuses drifted bytes), restart the instance
                     and re-restore a fresh ready state, then run a second
                     complete operation/capture/verification generation;
9. ``evidence``   - copy raw logs into the run directory and write the run
                     manifest with true HEAD, clean state and every source/jar
                     hash observed at start and end.

Usage (always with the repository venv Python):

    python tools/rom21_regression.py selftest
    python tools/rom21_regression.py run --config examples/minecart-rom/regression/configs/calibrated-a.json \
        --run-id rom21-20260926T080000Z-a1 --out labs/rom21-regression
    python tools/rom21_regression.py suite --stamp 20260926T080000Z
    python tools/rom21_regression.py package --suite labs/rom21-regression --out docs/evidence/rom21-regression

Failed attempts are retained as ``run.failed.json`` next to the partial run;
nothing is overwritten (a run directory must be new).
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
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
LABS = ROOT / "labs"
REG = ROOT / "examples" / "minecart-rom" / "regression"
LOGGER_SOURCE = REG / "logger"
CONFIG_DIR = REG / "configs"
RUNNER = ROOT / "examples" / "minecart-rom" / "runner" / "minecart_rom.py"
LAB_SERVER = TOOLS / "lab_server.py"
BUILD_MOD = TOOLS / "build_mod.py"
AUDIT_CHECK = TOOLS / "minecart_audit.py"
MAP_MANIFEST = ROOT / "examples" / "minecart-rom" / "map-manifest.json"
FIXTURE_SPEC = ROOT / "examples" / "minecart-rom" / "fixture-spec.json"

BRIDGE_SOURCE = Path("F:/mc-agent-worktrees/rom13/bridge6")
BRIDGE_HEAD = "4116ebb34b60e962e95834791d521b369fd52c65"
VENV_PY = Path("F:/mc-agent/.venv/Scripts/python.exe")
JAVA = Path(
    "C:/Users/MSI-NB/AppData/Roaming/.hmcl/java/windows-x86_64/"
    "mojang-java-runtime-epsilon/bin/java.exe"
)
JDK = JAVA.parent.parent
INTERFACE_JAR = Path("F:/mc-agent-worktrees/rom13/meta16/labs/_build/mods/mc-agent-interface-0.6.0.jar")
INTERFACE_SHA = "45f12e16b3979be6a699ac3c744b2a68dfcf8dd2379f5987bf9b9319adf4404f"
AUDIT_JAR = Path(
    "F:/mc-agent-worktrees/rom13/fullgate/tests/mods/minecart-audit/dist/mc-minecart-audit-0.1.0.jar"
)
AUDIT_SHA = "7a77e89d72969f23ce7b7c2543bfe992c2a3674171f3eb190a8e7faa8f2ec4ac"
SOURCE_SAVE = Path("D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test")
SOURCE_BASELINE_SHA256 = "8cd54c86af9fa8d6b9ea33441fb21dac295cd2b5ddaa60327f5fb3a30255324a"
PORT_RANGE = range(27200, 27220)
WARM_CACHE_SOURCE = Path("F:/mc-agent-worktrees/rom13/coldstart/labs/_cache")
#: Host-local, read-only lab runtime caches (pinned Minecraft/Fabric jars) that
#: may be copied into a fresh lab so a first start does not depend on the
#: launcher re-downloading the same files.  The donor is never modified.
LAB_RUNTIME_DONORS = (
    Path("F:/mc-agent-worktrees/rom13/fullgate/labs/rom13-src"),
    Path("F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-exp"),
)

TRACKED_SOURCES = (
    "tools/rom21_regression.py",
    "tools/rom21_verify.py",
    "tools/lab_server.py",
    "tools/build_mod.py",
    "tools/minecart_audit.py",
    "tools/stage1_gate.py",
    "examples/minecart-rom/runner/minecart_rom.py",
    "examples/minecart-rom/runner/fixture.py",
    "examples/minecart-rom/runner/interface_mod.py",
)
LOGGER_SOURCE_FILES = (
    "examples/minecart-rom/regression/logger/src/main/java/dev/mcagent/rom21/RomLog.java",
    "examples/minecart-rom/regression/logger/src/main/java/dev/mcagent/rom21/RomLogMod.java",
    "examples/minecart-rom/regression/logger/src/main/java/dev/mcagent/rom21/mixin/MinecartContainerMixin.java",
    "examples/minecart-rom/regression/logger/src/main/resources/fabric.mod.json",
    "examples/minecart-rom/regression/logger/src/main/resources/rom21log.mixins.json",
    "examples/minecart-rom/regression/logger/src/main/resources/romlog.properties",
)

sys.path.insert(0, str(TOOLS))
import rom21_verify as rv  # noqa: E402

BRIDGES: list[subprocess.Popen] = []


def say(message: str = "") -> None:
    try:
        print(message, flush=True)
    except (OSError, UnicodeEncodeError):
        pass


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def read_json(path: Path, default: Any = None) -> Any:
    if not Path(path).is_file():
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return rv.read_jsonl(path)


def sha256_file(path: Path) -> str:
    return rv.sha256_file(path)


def sha256_bytes(data: bytes) -> str:
    return rv.sha256_bytes(data)


# --------------------------------------------------------------------------- subprocess


def run_cmd(
    argv: Sequence[object],
    *,
    check: bool = True,
    timeout: float = 900,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
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
            f"stdout:\n{result.stdout[-3000:]}\nstderr:\n{result.stderr[-3000:]}"
        )
    return result


def git(*args: str) -> str:
    return run_cmd(["git", "-C", str(ROOT), *args], timeout=120).stdout.strip()


def free_reserved_ports() -> list[int]:
    """Terminate leftover listeners inside the issue's 27200-27219 range only."""
    netstat = run_cmd(["netstat", "-ano"], check=False, timeout=60).stdout
    killed: set[str] = set()
    for line in netstat.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "TCP" or parts[3] != "LISTENING":
            continue
        port = parts[1].rsplit(":", 1)[-1]
        if port.isdigit() and int(port) in PORT_RANGE:
            killed.add(parts[4])
    for pid in killed:
        run_cmd(["taskkill", "/PID", pid, "/F"], check=False, timeout=60)
    return sorted(int(pid) for pid in killed)


def lab(*args: object, check: bool = True, timeout: float = 900) -> str:
    return run_cmd([VENV_PY, LAB_SERVER, *args], check=check, timeout=timeout).stdout.strip()


def retry(description: str, function: Any, *, attempts: int = 4, delays: Sequence[float] = (5, 15, 30)) -> Any:
    """Bounded retry around network-dependent steps; genuine errors still surface."""
    last: Exception | None = None
    for index in range(attempts):
        try:
            return function()
        except RuntimeError as error:
            last = error
            if index < attempts - 1:
                say(f"  retry {index + 1}/{attempts - 1} for {description}: {str(error)[:200]}")
                time.sleep(delays[min(index, len(delays) - 1)])
    assert last is not None
    raise last


def seed_lab_runtime(lab_name: str) -> list[str]:
    """Copy the read-only runtime cache into a fresh lab's first-start files.

    Only the launcher-downloaded, world-independent parts are copied (the
    vanilla server jar, the Fabric loader libraries and the unpacked vanilla
    bundle).  ``.fabric/processedMods`` is deliberately *not* copied: those are
    derived from the mod set and would go stale against a different logger jar.
    """
    target = LABS / lab_name
    if (target / ".fabric" / "server").is_dir():
        return []
    donor = next(
        (
            candidate for candidate in LAB_RUNTIME_DONORS
            if (candidate / ".fabric" / "server").is_dir()
            and any((candidate / "versions").glob("*/server-*.jar"))
        ),
        None,
    )
    if donor is None:
        return []
    copied: list[str] = []
    for part in (".fabric/server", "libraries", "versions"):
        source = donor / part
        if not source.is_dir():
            continue
        destination = target / part
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)
        copied.append(part)
    return copied


def cleanup_range_labs() -> list[str]:
    """Stop and remove stopped labs that own ports in the reserved range.

    Runs are sequential and every run's raw logs are copied into its evidence
    directory before teardown, so the throwaway lab directories can be removed
    to let the next run reuse the dedicated ports without touching anything
    outside 27200-27219.
    """
    removed: list[str] = []
    for lab_json in sorted(LABS.glob("*/lab.json")):
        state = read_json(lab_json, {}) or {}
        ports = {state.get(key) for key in ("serverPort", "rconPort", "serverVantagePort", "bridgeApiPort")}
        if not any(isinstance(port, int) and port in PORT_RANGE for port in ports):
            continue
        name = lab_json.parent.name
        lab("stop", "--name", name, "--timeout", "120", check=False)
        shutil.rmtree(lab_json.parent, ignore_errors=True)
        removed.append(name)
    return removed


def rcon(name: str, command: str, *, check: bool = False, timeout: float = 180) -> str:
    result = run_cmd([VENV_PY, LAB_SERVER, "exec", "--name", name, command], check=False, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"rcon {command!r} failed: {result.stdout} {result.stderr}")
    return result.stdout + result.stderr


# --------------------------------------------------------------------------- provenance


def file_entry(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    data = path.read_bytes() if path.is_file() else b""
    blob = run_cmd(["git", "-C", str(ROOT), "cat-file", "-p", f"HEAD:{relative}"], check=False).stdout
    return {
        "bytes": len(data),
        "sha256": sha256_bytes(data),
        "git_blob_sha256": sha256_bytes(blob.encode("utf-8")) if blob else None,
        "crlf": b"\r\n" in data,
    }


def provenance_snapshot(label: str) -> dict[str, Any]:
    status = run_cmd(["git", "-C", str(ROOT), "status", "--porcelain"], timeout=120).stdout
    dirty = [line for line in status.splitlines() if line.strip()]
    head = git("rev-parse", "HEAD")
    bridge_head = run_cmd(["git", "-C", str(BRIDGE_SOURCE), "rev-parse", "HEAD"], timeout=120).stdout.strip()
    return {
        "label": label,
        "at": utc_now(),
        "head": head,
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "clean": not dirty,
        "dirtyEntries": dirty,
        "sources": {relative: file_entry(relative) for relative in TRACKED_SOURCES + LOGGER_SOURCE_FILES},
        "jars": {
            "audit": {"path": str(AUDIT_JAR), "sha256": sha256_file(AUDIT_JAR), "bytes": AUDIT_JAR.stat().st_size},
            "interface": {"path": str(INTERFACE_JAR), "sha256": sha256_file(INTERFACE_JAR), "bytes": INTERFACE_JAR.stat().st_size},
        },
        "bridge": {"source": str(BRIDGE_SOURCE), "head": bridge_head, "pinned": BRIDGE_HEAD},
    }


def check_provenance(provenance: Mapping[str, Any], *, allow_dirty: bool) -> str | None:
    if provenance.get("head") != git("rev-parse", "HEAD"):
        return "HEAD changed while the run was prepared"
    if not provenance.get("clean") and not allow_dirty:
        return f"worktree is dirty: {provenance.get('dirtyEntries')[:5]}"
    if provenance["jars"]["interface"]["sha256"] != INTERFACE_SHA:
        return "interface jar hash mismatch"
    if provenance["jars"]["audit"]["sha256"] != AUDIT_SHA:
        return "audit jar hash mismatch: the independent oracle must be the accepted jar"
    if provenance["bridge"]["head"] != BRIDGE_HEAD:
        return f"bridge head {provenance['bridge']['head']} != pinned {BRIDGE_HEAD}"
    return None


def source_world_observation() -> dict[str, Any]:
    sys.path.insert(0, str(TOOLS))
    import stage1_gate as gate

    if not SOURCE_SAVE.is_dir():
        return {"source_save_present": False}
    digest, files, total = gate.tree_hash(SOURCE_SAVE)
    return {
        "source_save_present": True,
        "tree_sha256": digest,
        "files": files,
        "bytes": total,
        "baseline_sha256": SOURCE_BASELINE_SHA256,
        "matches_baseline": digest == SOURCE_BASELINE_SHA256,
        "observedAt": utc_now(),
    }


# --------------------------------------------------------------------------- bridges


def start_bridge(lab_name: str, api_port: int, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(BRIDGE_SOURCE / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    handle = open(log_path, "wb")
    process = subprocess.Popen(
        [str(VENV_PY), "-m", "mc_agent_bridge.cli", "run", "--api-port", str(api_port),
         "--server-dir", str(LABS / lab_name)],
        cwd=str(BRIDGE_SOURCE),
        env=environment,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    BRIDGES.append(process)


def stop_bridges() -> None:
    for process in list(BRIDGES):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
        BRIDGES.remove(process)


atexit.register(stop_bridges)


async def connect(port: int, timeout: float = 150.0):
    if str(BRIDGE_SOURCE / "src") not in sys.path:
        sys.path.insert(0, str(BRIDGE_SOURCE / "src"))
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
            await asyncio.sleep(1.0)


async def call(api: Any, method: str, params: dict | None = None, timeout: float = 600.0) -> tuple[Any, dict[str, Any]]:
    started = time.time()
    try:
        result = await api.call(method, params or {}, timeout=timeout)
        return result, {"method": method, "params": params or {}, "ok": True, "result": result,
                        "seconds": round(time.time() - started, 3)}
    except Exception as error:  # noqa: BLE001 - refusals are evidence
        return None, {"method": method, "params": params or {}, "ok": False, "error": str(error),
                      "seconds": round(time.time() - started, 3)}


# --------------------------------------------------------------------------- labs


def lab_info(slug: str, base_port: int) -> dict[str, dict[str, Any]]:
    return {
        "src": {"name": f"rom21-{slug}-src", "server": base_port + 0, "rcon": base_port + 1,
                "vantage": base_port + 2, "bridge": base_port + 3},
        "exp": {"name": f"rom21-{slug}-exp", "server": base_port + 4, "rcon": base_port + 5,
                "vantage": base_port + 6, "bridge": base_port + 7},
        "neg": {"name": f"rom21-{slug}-neg", "server": base_port + 8, "rcon": base_port + 9,
                "vantage": base_port + 10, "bridge": base_port + 11},
    }


def provision_lab(
    info: Mapping[str, Any],
    *,
    world: Path | None,
    test_mods: Sequence[Path] = (),
    extra_mods: Sequence[Path] = (),
    void: bool = False,
) -> None:
    lab("stop", "--name", info["name"], "--timeout", "120", check=False)
    command: list[object] = [
        "provision", "--name", info["name"], "--force", "--fabric-api", "--carpet",
        "--java", str(JAVA), "--jdk", str(JDK),
        "--server-port", str(info["server"]), "--rcon-port", str(info["rcon"]),
        "--vantage-port", str(info["vantage"]), "--bridge-port", str(info["bridge"]),
        "--mod-jar", str(INTERFACE_JAR),
    ]
    for mod in test_mods:
        command += ["--test-mod", str(mod)]
    for mod in extra_mods:
        command += ["--mod-jar", str(mod)]
    if void:
        command.append("--void")
    elif world is not None:
        command += ["--world", str(world)]
    retry(f"provision {info['name']}", lambda: lab(*command, timeout=1200))
    seed_lab_runtime(info["name"])


def audit_config(
    lab_name: str,
    run_id: str,
    instance: str,
    scene: Mapping[str, Any],
    *,
    agent_uuid: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    config = {
        "runId": run_id,
        "instanceId": instance,
        "dimension": scene["dimension"],
        "provenance": dict(provenance),
        "inputRegions": [scene["inputRegion"]],
        "stackRegion": scene["stackRegion"],
        "outputRegion": scene["outputRegion"],
        "cartTypes": ["minecraft:chest_minecart"],
        "sampleIntervalTicks": 20,
        "agentUuids": [agent_uuid],
    }
    directory = LABS / lab_name / "mc-audit"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "config.json", config)
    return config


def machine_base_ok(instance: str) -> tuple[bool, list[dict[str, Any]]]:
    sys.path.insert(0, str(TOOLS))
    import stage1_fullgate as fg

    return fg.machine_base_ok(instance)


def ensure_seat_player(info: Mapping[str, Any]) -> dict[str, Any]:
    sys.path.insert(0, str(TOOLS))
    import stage1_fullgate as fg

    return fg.ensure_seat_player(None, dict(info), recorded=False, timeout=120.0)


def read_lab_mods(lab_name: str) -> dict[str, Any]:
    state = read_json(LABS / lab_name / "lab.json", {}) or {}
    run = read_json(LABS / lab_name / "run.json", {}) or {}
    mods = {record.get("name"): record for record in state.get("mods") or []}
    at_start = {record.get("name"): record for record in run.get("modsAtStart") or []}
    return {
        "recorded": {name: {"sha256": record.get("sha256"), "bytes": record.get("bytes")} for name, record in mods.items()},
        "atStart": {name: {"sha256": record.get("sha256"), "bytes": record.get("bytes")} for name, record in at_start.items()},
        "requiredMods": list(state.get("requiredMods") or []),
    }


# --------------------------------------------------------------------------- logger build


def scene_jvm_args(
    scene: Mapping[str, Any], expected_carts: int, overrides: Sequence[str]
) -> list[str]:
    """Config-driven scene properties, with explicit overrides winning."""
    configured = [
        f"-Dromlog.exitAxis={scene['exitAxis']}",
        f"-Dromlog.exitGreaterThan={scene['exitGreaterThan']}",
        f"-Dromlog.expectedCarts={expected_carts}",
    ]
    overridden = {argument.split("=", 1)[0] for argument in overrides if argument.startswith("-D")}
    return [argument for argument in configured if argument.split("=", 1)[0] not in overridden] + list(overrides)


def build_logger(lab_name: str, version: str, out: Path) -> dict[str, Any]:
    result = run_cmd(
        [VENV_PY, BUILD_MOD, "--source", str(LOGGER_SOURCE), "--lab", lab_name,
         "--out", str(out), "--version", version],
        timeout=900,
    )
    sidecar = Path(str(out) + ".build.json")
    metadata = read_json(sidecar, {})
    return {
        "version": version,
        "build": version,
        "path": str(out),
        "sha256": sha256_file(out),
        "size": out.stat().st_size,
        "sidecar": str(sidecar),
        "sources": metadata.get("sources"),
        "sourceSha256": sha256_bytes(rv.canonical_json(metadata.get("sources") or []).encode("utf-8")),
        "buildTool": str(BUILD_MOD),
        "buildOutput": result.stdout.strip().splitlines()[-3:] if result.stdout else [],
    }


# --------------------------------------------------------------------------- run state


class RunLog:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "steps.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def step(self, name: str, payload: Mapping[str, Any] | None = None) -> None:
        record = {"at": utc_now(), "epoch": round(time.time(), 3), "step": name}
        record.update(dict(payload or {}))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def wait_for_logger(logger_path: Path, event: str, count: int, *, timeout: float, poll: float = 0.25) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = read_jsonl(logger_path)
        found = sum(1 for row in rows if row.get("event") == event)
        if found >= count:
            return found
        time.sleep(poll)
    return sum(1 for row in read_jsonl(logger_path) if row.get("event") == event)


def cart_count(instance: str) -> int:
    output = rcon(instance, "execute if entity @e[type=minecraft:chest_minecart]")
    match = re.search(r"Count:\s*(\d+)", output)
    return int(match.group(1)) if match else -1


def session_event_count(logger_path: Path, ordinal: int, event: str) -> int:
    try:
        session = rv.pick_logger_session(read_jsonl(logger_path), ordinal)
    except rv.VerifyError:
        return 0
    return sum(1 for row in session if row.get("event") == event)


def operate(
    info: Mapping[str, Any],
    config: Mapping[str, Any],
    logger_path: Path,
    runlog: RunLog,
    *,
    paced: bool,
    label: str,
    baseline_observed: int = 0,
    baseline_void: int = 0,
) -> dict[str, Any]:
    """Press the note block once per cart; pacing uses the own logger when asked."""
    expected = int(config["expectedCartCount"])
    operations = config["operations"]
    sprint_ticks = int(operations["sprintTicks"])
    timeout = float(operations["timeoutSeconds"])
    observations: list[dict[str, Any]] = []
    rcon(info["name"], "mcaudit mark operation-begin")
    rcon(info["name"], "tick unfreeze")
    for index in range(1, expected + 1):
        before = cart_count(info["name"])
        started = time.monotonic()
        rcon(info["name"], "player Romuser use once")
        observed = (
            wait_for_logger(logger_path, "cart_observed", baseline_observed + index, timeout=45.0)
            if paced else 0
        )
        rcon(info["name"], f"tick sprint {sprint_ticks}")
        voided = wait_for_logger(logger_path, "cart_void_capture", baseline_void + index, timeout=timeout)
        if paced and observed < baseline_observed + index:
            raise RuntimeError(f"press {index}: no transient capture within 45s")
        if voided < baseline_void + index:
            raise RuntimeError(f"press {index}: no natural void capture within {timeout:.0f}s")
        after = cart_count(info["name"])
        observation = {
            "press": index,
            "stack_before": before,
            "stack_after": after,
            "transient_captures": observed,
            "void_captures": voided,
            "elapsed_s": round(time.monotonic() - started, 3),
        }
        observations.append(observation)
        runlog.step("operation", {"generation": label, **observation})
        say(f"  press {index}/{expected}: carts {before}->{after}, void captures {voided}")
    rcon(info["name"], "tick freeze")
    rcon(info["name"], "mcaudit mark operation-end")
    remaining = cart_count(info["name"])
    return {"presses": expected, "observations": observations, "remaining_carts": remaining}


# --------------------------------------------------------------------------- one run


async def execute_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    config = rv.load_config(config_path)
    expected = int(config["expectedCartCount"])
    run_id = args.run_id
    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = ROOT / out_root
    run_dir = out_root / f"run-{run_id}"
    if run_dir.exists():
        raise RuntimeError(f"run directory already exists (attempts are never overwritten): {run_dir}")
    run_dir.mkdir(parents=True)
    runlog = RunLog(run_dir)
    slug = re.sub(r"[^a-z0-9-]", "-", run_id.lower())[-24:]
    infos = lab_info(slug, int(args.base_port))
    scene = config["scene"]

    provenance_start = provenance_snapshot("start")
    problem = check_provenance(provenance_start, allow_dirty=args.allow_dirty)
    if problem:
        raise RuntimeError(f"provenance check failed: {problem}")
    write_json(run_dir / "provenance-start.json", provenance_start)
    manifest_provenance_start = provenance_start
    source_before = source_world_observation()
    if source_before.get("source_save_present") and not source_before.get("matches_baseline"):
        if not args.allow_dirty:
            raise RuntimeError(
                f"source save tree {source_before.get('tree_sha256')} does not match the documented "
                f"baseline {SOURCE_BASELINE_SHA256}"
            )
    runlog.step("provenance", {"head": provenance_start["head"], "clean": provenance_start["clean"]})

    manifest: dict[str, Any] = {
        "format": rv.RUN_FORMAT,
        "runId": run_id,
        "configId": config["configId"],
        "configPath": str(config_path),
        "configSha256": sha256_file(config_path),
        "instance": infos["exp"]["name"],
        "dimension": scene["dimension"],
        "program": config["program"],
        "programSha256": config["programSha256"],
        "expectedCartCount": expected,
        "scene": scene,
        "operations": config["operations"],
        "loggerSessionOrdinal": 1,
        "cold": bool(args.cold),
        "jarIteration": bool(args.jar_iteration),
        "labs": {"src": infos["src"], "exp": infos["exp"]},
        "auditJar": {"path": str(AUDIT_JAR), "sha256": sha256_file(AUDIT_JAR)},
        "interfaceJar": {"path": str(INTERFACE_JAR), "sha256": sha256_file(INTERFACE_JAR)},
        "sourceWorld": {"before": source_before, "after": None},
        "provenanceStart": manifest_provenance_start,
        "generations": [],
    }

    def finish(status: str, error: str | None = None) -> None:
        manifest["status"] = status
        manifest["error"] = error
        manifest["finishedAt"] = utc_now()
        try:
            manifest["provenanceEnd"] = provenance_snapshot("end")
        except Exception as failure:  # noqa: BLE001 - retain what we can
            manifest["provenanceEnd"] = {"error": str(failure)}
        if manifest["sourceWorld"].get("after") is None:
            manifest["sourceWorld"]["after"] = source_world_observation()
        write_json(run_dir / ("run.json" if status == "ok" else "run.failed.json"), manifest)

    try:
        free_reserved_ports()
        cleanup_range_labs()
        for info in infos.values():
            lab("stop", "--name", info["name"], "--timeout", "120", check=False)

        # ---- map artifact / warm cache ------------------------------------
        if args.cold:
            cold_cache = Path(getattr(args, "cold_cache", "") or (LABS / f"rom21-cold-cache-{run_id}"))
            if cold_cache.exists():
                shutil.rmtree(cold_cache)
            cold_cache.mkdir(parents=True)
            fetched = retry(
                "cold map fetch",
                lambda: run_cmd(
                    [VENV_PY, RUNNER, "fetch", "--cold", "--cache", str(cold_cache),
                     "--records", str(run_dir / "fetch")],
                    timeout=900,
                ),
            )
            write_json(run_dir / "fetch.json", {"stdout": fetched.stdout[-4000:], "cache": str(cold_cache),
                                                "sha256": sha256_file(cold_cache / "minecart-rom-base-1.0.0.zip")
                                                if (cold_cache / "minecart-rom-base-1.0.0.zip").is_file() else None})
            map_world = cold_cache / "unpacked" / "minecart-rom-base-1.0.0" / "world"
        else:
            ensure_warm_cache()
            map_world = LABS / "_cache" / "maps" / "unpacked" / "minecart-rom-base-1.0.0" / "world"
        if not map_world.is_dir():
            raise RuntimeError(f"the extracted map world is missing: {map_world}")
        manifest["mapWorld"] = str(map_world)

        # ---- source fixture ------------------------------------------------
        provision_lab(infos["src"], world=map_world)
        retry(f"start {infos['src']['name']}", lambda: lab("start", "--name", infos["src"]["name"], "--wait", "420"))
        rcon(infos["src"]["name"], "tick freeze")
        base_ok, base_broken = machine_base_ok(infos["src"]["name"])
        if not base_ok:
            raise RuntimeError(f"the fresh source map is not in its base state: {base_broken}")
        write_json(run_dir / "program.json", config["program"])
        init_records = run_dir / "init"
        init = run_cmd(
            [VENV_PY, RUNNER, "init", "--lab", infos["src"]["name"], "--program", str(run_dir / "program.json"),
             "--records", str(init_records), "--run-id", "source-ready"],
            timeout=1200,
        )
        init_summary = json.loads(init.stdout[init.stdout.find("{"):]) if "{" in init.stdout else {}
        write_json(run_dir / "init-summary.json", init_summary)
        if not (init_summary.get("validation") or {}).get("ok"):
            raise RuntimeError(f"the fresh sealed fixture did not validate: {init_summary.get('validation')}")
        if int(init_summary.get("cart_count") or -1) != expected:
            raise RuntimeError(f"initialized {init_summary.get('cart_count')} carts, expected {expected}")
        ready = read_json(init_records / "source-ready" / "ready-snapshot.json", {})

        def user_uuid(container: Mapping[str, Any]) -> str:
            user = container.get("user") if isinstance(container.get("user"), Mapping) else {}
            return str(user.get("uuid") or user.get("UUID") or "")

        agent_uuid = user_uuid(init_summary) or user_uuid(ready)
        if agent_uuid.startswith("[I;"):
            sys.path.insert(0, str(TOOLS))
            import stage1_fullgate as fg

            agent_uuid = fg._uuid_from_nbt(agent_uuid)
        if not agent_uuid:
            raise RuntimeError("the init summary and ready snapshot carry no task player UUID")
        rcon(infos["src"]["name"], "tick freeze")
        manifest["agentUuid"] = agent_uuid
        manifest["init"] = {
            "cartCount": init_summary.get("cart_count"),
            "spawnOrder": init_summary.get("spawn_order"),
            "tickOrderHash": init_summary.get("tick_order_hash"),
            "normalizedHash": init_summary.get("normalized_hash"),
            "validation": init_summary.get("validation", {}).get("ok"),
        }
        runlog.step("fixture", {"carts": init_summary.get("cart_count"), "uuid": agent_uuid})

        # ---- logger jars ---------------------------------------------------
        jars_dir = run_dir / "jars"
        version1 = str((config.get("logger") or {}).get("buildVersion"))
        jar1 = build_logger(infos["src"]["name"], version1, jars_dir / "v1" / "rom21-logger.jar")
        manifest["loggerJar"] = jar1
        iteration = None
        if args.jar_iteration:
            version2 = str((config.get("logger") or {}).get("iterationVersion") or "romlog-rom21-2")
            jar2 = build_logger(infos["src"]["name"], version2, jars_dir / "v2" / "rom21-logger.jar")
            if jar1["size"] != jar2["size"]:
                raise RuntimeError(f"same-size iteration failed: {jar1['size']} != {jar2['size']}")
            if jar1["sha256"] == jar2["sha256"]:
                raise RuntimeError("same-size iteration did not change the jar bytes")
            if jar1["sourceSha256"] != jar2["sourceSha256"]:
                raise RuntimeError("iteration jars were not built from identical source bytes")
            iteration = jar2
            manifest["loggerJarIteration"] = iteration
        runlog.step("jars", {"v1": jar1["sha256"], "v1Size": jar1["size"],
                             "v2": (iteration or {}).get("sha256"), "v2Size": (iteration or {}).get("size")})

        # ---- fork ----------------------------------------------------------
        start_bridge(infos["src"]["name"], infos["src"]["bridge"], run_dir / "logs" / "bridge-src.log")
        api_src = await connect(infos["src"]["bridge"])
        _snapshot, snapshot_record = await call(api_src, "snapshot", {"name": f"{run_id}-source-before", "radius": 0})
        fork, fork_record = await call(api_src, "fork",
                                       {"name": f"{run_id}-ready", "radius": 0, "freeze": True})
        await api_src.close()
        stop_bridges()
        if not fork:
            raise RuntimeError(f"fork failed: {fork_record}")
        if int(fork.get("entities") or 0) != expected + 1:
            raise RuntimeError(f"fork captured {fork.get('entities')} entities, expected {expected + 1}")
        write_json(run_dir / "fork.json", fork)
        manifest["forkPreserved"] = preserve_fork(out_root, run_id, fork)
        runlog.step("fork", {"orderHash": fork.get("orderHash"), "entities": fork.get("entities")})

        # ---- generation 1 --------------------------------------------------
        generation1 = await run_generation(
            args, config, infos, run_dir, runlog, fork, jar1, agent_uuid, "gen1", ordinal=1,
            run_manifest=manifest,
        )
        manifest["generations"].append(generation1)
        manifest["loggerSessionOrdinal"] = 1
        # canonical primary-generation copies for offline verification/packaging
        shutil.copyfile(run_dir / "audit-gen1.jsonl", run_dir / "audit.jsonl")
        shutil.copyfile(run_dir / "audit-check-gen1.json", run_dir / "audit-check.json")
        shutil.copyfile(run_dir / "restore-verify-gen1.json", run_dir / "restore-verify.json")

        if iteration is not None:
            generation2 = await run_iteration(
                args, config, infos, run_dir, runlog, iteration, agent_uuid, generation1,
                run_manifest=manifest,
            )
            manifest["generations"].append(generation2)

        # ---- source world after observation / evidence copies ---------------
        after_world = source_world_observation()
        manifest["sourceWorld"]["after"] = after_world
        if (
            source_before.get("source_save_present")
            and after_world.get("tree_sha256") != source_before.get("tree_sha256")
        ):
            raise RuntimeError("the read-only source save changed during the regression run")
        exp_logger = generation1["loggerPath"]
        shutil.copyfile(exp_logger, run_dir / "logger.jsonl")
        manifest["raw"] = {"logger": str(exp_logger), "loggerSha256": sha256_file(exp_logger)}
        runlog.step("evidence", {"copied": str(run_dir / "logger.jsonl")})
        lab("stop", "--name", infos["src"]["name"], "--timeout", "180", check=False)
        finish("ok")
        say(f"run {run_id}: PASS (gen1={generation1['verification']['verdict']})")
        return 0
    except BaseException as error:  # noqa: BLE001 - keep the failed attempt
        runlog.step("error", {"error": f"{type(error).__name__}: {error}"})
        finish("failed", f"{type(error).__name__}: {error}")
        for info in infos.values():
            lab("stop", "--name", info["name"], "--timeout", "120", check=False)
        stop_bridges()
        say(f"run {run_id}: FAILED: {type(error).__name__}: {error}")
        return 1


async def run_generation(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    infos: Mapping[str, Mapping[str, Any]],
    run_dir: Path,
    runlog: RunLog,
    fork: Mapping[str, Any],
    logger_jar: Mapping[str, Any],
    agent_uuid: str,
    label: str,
    *,
    ordinal: int,
    run_manifest: Mapping[str, Any],
    jvm_args: Sequence[str] = (),
    no_press: bool = False,
    paced: bool = True,
    tamper: str | None = None,
) -> dict[str, Any]:
    """One restore → operate → answer → verify generation on a fresh fork."""
    exp = infos["exp"]
    scene = config["scene"]
    provision_lab(exp, world=Path(str(fork["forkDir"])), test_mods=[AUDIT_JAR, Path(str(logger_jar["path"]))])
    audit_config(
        exp["name"], args.run_id, exp["name"], scene, agent_uuid=agent_uuid,
        provenance={"kind": "fork", "reference": str(fork["forkDir"]),
                    "snapshotId": Path(str(fork["snapshotDir"])).name,
                    "snapshotHash": fork.get("orderHash")},
    )
    effective_jvm = scene_jvm_args(scene, int(config["expectedCartCount"]), jvm_args)
    start_command: list[object] = ["start", "--name", exp["name"], "--wait", "420"]
    for argument in effective_jvm:
        start_command += ["--jvm-arg", argument]
    retry(f"start {exp['name']}", lambda: lab(*start_command))
    rcon(exp["name"], "tick freeze")
    base_ok, base_broken = machine_base_ok(exp["name"])
    if not base_ok:
        raise RuntimeError(f"the {label} experiment fork is not in its base state: {base_broken}")
    start_bridge(exp["name"], exp["bridge"], run_dir / "logs" / f"bridge-{label}.log")
    api = await connect(exp["bridge"])
    directory = str(fork["snapshotDir"])
    exp_world = str(LABS / exp["name"] / "world")
    rcon(exp["name"], "mcaudit phase restore")
    _dry, dry_record = await call(api, "restore", {"directory": directory, "target": exp["name"],
                                                    "expect_instance": "server", "expect_world_dir": exp_world})
    applied, apply_record = await call(
        api, "restore",
        {"directory": directory, "dry_run": False, "target": exp["name"], "expect_instance": "server",
         "expect_world_dir": exp_world, "replace_existing": True},
        timeout=900,
    )
    verified, verify_record = await call(api, "verify", {"directory": directory, "target": exp["name"]})
    await api.close()
    stop_bridges()
    write_json(run_dir / f"restore-apply-{label}.json", {"dryRun": dry_record, "applied": apply_record})
    write_json(run_dir / f"restore-verify-{label}.json", verified or {"error": verify_record})
    if not (applied and applied.get("ok") and verified and verified.get("ok")):
        raise RuntimeError(f"{label}: guarded restore/verify failed: {apply_record.get('error') or verify_record.get('error')}")
    if int((verified.get("verification") or {}).get("matched") or 0) != int(config["expectedCartCount"]) + 1:
        raise RuntimeError(f"{label}: restore matched {verified.get('verification', {}).get('matched')} entities")
    ensure_seat_player(exp)
    rcon(exp["name"], "mcaudit phase experiment_start")
    if tamper:
        rcon(exp["name"], tamper, check=True)
    logger_path = LABS / exp["name"] / "audit" / "romlogger" / "romlog.jsonl"
    armed = wait_for_logger(logger_path, "logger_armed", ordinal, timeout=60.0)
    if armed < ordinal:
        raise RuntimeError(f"{label}: logger was not armed (expected session {ordinal})")
    if no_press:
        operation = {"presses": 0, "observations": [], "remaining_carts": cart_count(exp["name"])}
        rcon(exp["name"], "mcaudit mark operation-begin")
        rcon(exp["name"], "mcaudit mark operation-end")
    else:
        operation = operate(
            exp, config, logger_path, runlog, paced=paced, label=label,
            baseline_observed=session_event_count(logger_path, ordinal, "cart_observed"),
            baseline_void=session_event_count(logger_path, ordinal, "cart_void_capture"),
        )
    rcon(exp["name"], "mcaudit flush")
    rcon(exp["name"], "mcaudit phase experiment_end")
    rcon(exp["name"], "mcaudit end")
    time.sleep(1.0)
    audit_path = LABS / exp["name"] / "mc-audit" / f"audit-{args.run_id}.jsonl"
    if not audit_path.is_file():
        candidates = sorted((LABS / exp["name"] / "mc-audit").glob("audit-*.jsonl"))
        if not candidates:
            raise RuntimeError(f"{label}: no audit log written")
        audit_path = candidates[-1]
    shutil.copyfile(audit_path, run_dir / f"audit-{label}.jsonl")
    shutil.copyfile(logger_path, run_dir / f"logger-{label}.jsonl")
    audit_check = rv.run_audit_check(audit_path)
    write_json(run_dir / f"audit-check-{label}.json", audit_check)
    audit_sessions = [
        row.get("session") for row in read_jsonl(audit_path) if row.get("type") == "session_start"
    ]
    verify_manifest = json.loads(json.dumps(run_manifest))
    verify_manifest["auditSessionId"] = str(audit_sessions[-1]) if audit_sessions else None
    verify_manifest["loggerSessionOrdinal"] = ordinal
    verify_manifest["expectedOrderHash"] = str(fork.get("orderHash"))
    verify_manifest["loggerJar"] = dict(logger_jar)
    verify_manifest["loggerJarIteration"] = None
    verify_manifest["sourceWorld"] = dict(
        (run_manifest.get("sourceWorld") or {})
        | {"after": (run_manifest.get("sourceWorld") or {}).get("before")}
    )
    report = rv.verify_run(verify_manifest, read_jsonl(logger_path), read_jsonl(audit_path),
                           audit_check=audit_check, restore_verify=verified)
    write_json(run_dir / f"verification-{label}.json", report)
    generation = {
        "label": label,
        "ordinal": ordinal,
        "loggerJar": dict(logger_jar),
        "auditCheck": {"verdict": audit_check.get("verdict"), "exitCode": audit_check.get("_exitCode")},
        "restore": {"applied": bool(applied and applied.get("ok")), "verified": bool(verified and verified.get("ok")),
                    "matched": (verified.get("verification") or {}).get("matched")},
        "operation": operation,
        "verification": report,
        "loggerPath": str(logger_path),
        "auditPath": str(audit_path),
        "loggerSha256": sha256_file(logger_path),
        "auditSha256": sha256_file(audit_path),
    }
    if report["verdict"] != "pass":
        raise RuntimeError(f"{label}: verification failed: {report['problems']}")
    lab("stop", "--name", exp["name"], "--timeout", "180", check=False)
    return generation


async def run_iteration(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    infos: Mapping[str, Mapping[str, Any]],
    run_dir: Path,
    runlog: RunLog,
    iteration_jar: Mapping[str, Any],
    agent_uuid: str,
    generation1: Mapping[str, Any],
    *,
    run_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Same-size jar iteration: refusal, hash-based deploy, restart, re-restore, second operation."""
    exp = infos["exp"]
    src = infos["src"]
    # 1. same-size bytes replaced behind the lab's back: start must refuse.
    deployed = LABS / exp["name"] / "mods" / Path(str(generation1["loggerJar"]["path"])).name
    before_record = read_lab_mods(exp["name"])
    shutil.copyfile(Path(str(iteration_jar["path"])), deployed)
    refusal = run_cmd([VENV_PY, LAB_SERVER, "start", "--name", exp["name"], "--wait", "5"], check=False, timeout=180)
    drift_evidence = {
        "deployedBefore": before_record,
        "swappedBytes": {"sha256": iteration_jar["sha256"], "size": iteration_jar["size"]},
        "startReturncode": refusal.returncode,
        "startOutput": (refusal.stdout + refusal.stderr)[-4000:],
        "refused": refusal.returncode != 0 and "refused" in (refusal.stdout + refusal.stderr).lower(),
    }
    write_json(run_dir / "jar-swap-refusal.json", drift_evidence)
    runlog.step("jar-swap", {"refused": drift_evidence["refused"], "returncode": refusal.returncode})
    if not drift_evidence["refused"]:
        raise RuntimeError("the lab started with drifted same-size jar bytes; the hash gate did not refuse")

    # 2. re-provision by hash and re-fork the still-running unoperated source.
    #    The source server is never operated and never restarted between the two
    #    forks: its ready state stays the same live memory state that produced
    #    the first fork, so the second fork is a true re-fork of the ready state.
    lab("stop", "--name", exp["name"], "--timeout", "120", check=False)
    rcon(src["name"], "tick freeze")
    base_ok, base_broken = machine_base_ok(src["name"])
    if not base_ok:
        raise RuntimeError(f"the source lab is not in its base state for the iteration fork: {base_broken}")
    start_bridge(src["name"], src["bridge"], run_dir / "logs" / "bridge-src-2.log")
    api_src = await connect(src["bridge"])
    fork, fork_record = await call(api_src, "fork", {"name": f"{args.run_id}-ready-2", "radius": 0, "freeze": True})
    await api_src.close()
    stop_bridges()
    if not fork:
        raise RuntimeError(f"iteration fork failed: {fork_record}")
    if int(fork.get("entities") or 0) != int(config["expectedCartCount"]) + 1:
        raise RuntimeError(f"iteration fork captured {fork.get('entities')} entities, expected "
                           f"{int(config['expectedCartCount']) + 1}")
    write_json(run_dir / "fork-2.json", fork)

    generation2 = await run_generation(
        args, config, infos, run_dir, runlog, fork, iteration_jar, agent_uuid, "gen2", ordinal=2,
        run_manifest=run_manifest,
    )
    deployed_after = read_lab_mods(exp["name"])
    generation2["deployedAfter"] = deployed_after
    generation2["deployedSha256"] = (
        (deployed_after.get("atStart") or {}).get(deployed.name) or {}
    ).get("sha256")
    if generation2["deployedSha256"] != iteration_jar["sha256"]:
        raise RuntimeError("the iteration jar was not deployed by hash")
    generation2["jarSwapRefusal"] = drift_evidence
    return generation2


def preserve_fork(out_root: Path, run_id: str, fork: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the fork snapshot inside the suite directory across lab cleanup.

    The lab directories are throwaway and are removed when the next run
    reuses the dedicated ports; the negative probes need the fork snapshot
    later, so it is copied here and referenced by hash evidence.
    """
    base = out_root / "snapshots" / run_id
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    shutil.copytree(Path(str(fork["snapshotDir"])), base / "snapshot")
    shutil.copytree(Path(str(fork["forkDir"])), base / "fork")
    preserved = {
        "snapshotDir": str(base / "snapshot"),
        "forkDir": str(base / "fork"),
        "orderHash": fork.get("orderHash"),
        "entities": fork.get("entities"),
        "sourceSnapshotDir": fork.get("snapshotDir"),
        "sourceForkDir": fork.get("forkDir"),
        "snapshotEntitySha256": sha256_file(base / "snapshot" / "entities.jsonl"),
    }
    write_json(base / "fork.json", preserved)
    write_json(base / "fork.original.json", dict(fork))
    return preserved


def ensure_warm_cache() -> None:
    """Copy the read-only coldstart/fullgate cache once; never modify the source."""
    target = LABS / "_cache"
    if (target / "maps" / "unpacked" / "minecart-rom-base-1.0.0" / "world").is_dir():
        return
    if not WARM_CACHE_SOURCE.is_dir():
        raise RuntimeError(f"no warm cache to copy from: {WARM_CACHE_SOURCE}")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(WARM_CACHE_SOURCE, target)


# --------------------------------------------------------------------------- suite


SUITE_PLAN = (
    {"config": "calibrated-a.json", "suffix": "a1", "cold": False, "jarIteration": False},
    {"config": "calibrated-b.json", "suffix": "b1", "cold": False, "jarIteration": True},
    {"config": "calibrated-a.json", "suffix": "a2", "cold": True, "jarIteration": False},
)


async def execute_suite(args: argparse.Namespace) -> int:
    stamp = args.stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_root = Path(args.out) if Path(args.out).is_absolute() else (ROOT / args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    suite = {
        "format": "mc-agent/rom21-suite@1",
        "stamp": stamp,
        "startedAt": utc_now(),
        "plan": list(SUITE_PLAN),
        "runs": [],
    }
    for entry in SUITE_PLAN:
        run_id = f"rom21-{stamp}-{entry['suffix']}"
        run_args = argparse.Namespace(**vars(args))
        run_args.config = str(CONFIG_DIR / entry["config"])
        run_args.run_id = run_id
        run_args.cold = entry["cold"]
        run_args.jar_iteration = entry["jarIteration"]
        run_args.run_manifest = None
        say(f"suite: {run_id} ({entry['config']}, cold={entry['cold']}, iteration={entry['jarIteration']})")
        status = await execute_run(run_args)
        record = {
            "runId": run_id,
            "config": entry["config"],
            "cold": entry["cold"],
            "jarIteration": entry["jarIteration"],
            "status": "ok" if status == 0 else "failed",
            "runDir": str(out_root / f"run-{run_id}"),
        }
        suite["runs"].append(record)
        write_json(out_root / "suite.json", suite)
        if status != 0:
            suite["status"] = "failed"
            suite["finishedAt"] = utc_now()
            write_json(out_root / "suite.json", suite)
            return 1
    suite["status"] = "ok"
    suite["finishedAt"] = utc_now()
    write_json(out_root / "suite.json", suite)
    say("suite: all runs PASS")
    return 0


# --------------------------------------------------------------------------- negatives


async def execute_negatives(args: argparse.Namespace) -> int:
    """Live fault probes plus offline evidence mutations, all fail-closed."""
    out_root = Path(args.out) if Path(args.out).is_absolute() else (ROOT / args.out)
    suite = read_json(out_root / "suite.json", {})
    base_run = next((r for r in suite.get("runs", []) if r.get("status") == "ok"), None)
    if not base_run:
        raise RuntimeError("negative probes need at least one passing suite run")
    base_dir = Path(base_run["runDir"])
    base_manifest = read_json(base_dir / "run.json", {})
    fork = base_manifest.get("forkPreserved") or read_json(base_dir / "fork.json", {})
    config = rv.load_config(Path(str(base_manifest.get("configPath"))))
    agent_uuid = str(base_manifest["agentUuid"])
    slug = re.sub(r"[^a-z0-9-]", "-", f"neg-{suite.get('stamp', '')}".lower())[-24:]
    infos = lab_info(slug, int(args.base_port))
    neg_dir = out_root / "negatives"
    neg_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []

    async def live_case(
        case: str,
        *,
        jvm_args: Sequence[str] = (),
        no_press: bool = False,
        paced: bool = True,
        tamper: bool = False,
        expect: str,
    ) -> None:
        label = f"neg-{case}"
        runlog = RunLog(neg_dir / label)
        run_dir = neg_dir / label
        run_dir.mkdir(parents=True, exist_ok=True)
        run_name = f"rom21-neg-{case}-{int(time.time())}"
        run_args = argparse.Namespace(**vars(args))
        run_args.run_id = run_name
        case_infos = lab_info(re.sub(r"[^a-z0-9-]", "-", f"{slug}-{case}".lower())[-24:], int(args.base_port))
        generation = await run_generation_negative(
            run_args, config, case_infos, run_dir, runlog, fork, agent_uuid,
            logger_jar=Path(str(base_manifest["loggerJar"]["path"])),
            jvm_args=jvm_args, no_press=no_press, paced=paced, tamper=tamper,
        )
        record = rv.live_negative(case, {"runId": run_name}, generation["verification"],
                                  evidence={"loggerSha256": generation["loggerSha256"],
                                            "auditSha256": generation["auditSha256"]})
        record["expectedCheck"] = expect
        record["failClosed"] = record["verdict"] == "fail" and expect in (record["failedChecks"] or [])
        write_json(neg_dir / f"{case}.json", record)
        records.append(record)
        say(f"negative {case}: {'fail-closed ok' if record['failClosed'] else 'NOT fail-closed'} ({record['failedChecks']})")

    free_reserved_ports()
    await live_case("inventory_wrong", tamper=True, expect="inventory_matches_program")
    await live_case("logger_missed_capture", jvm_args=("-Dromlog.exitGreaterThan=1.0E9",), paced=False,
                    expect="transient_captures")
    await live_case("logger_late_capture", jvm_args=("-Dromlog.captureDelayTicks=100000",), paced=False,
                    expect="transient_captures")
    await live_case("no_machine_operation", no_press=True, expect="agent_operations")

    # restore order wrong: a genuine swapped snapshot must fail the bridge guard
    swapped_dir = neg_dir / "restore_order_wrong"
    swapped_dir.mkdir(parents=True, exist_ok=True)
    snapshot_copy = swapped_dir / "snapshot"
    if snapshot_copy.exists():
        shutil.rmtree(snapshot_copy)
    shutil.copytree(Path(str(fork["snapshotDir"])), snapshot_copy)
    entities_path = snapshot_copy / "entities.jsonl"
    lines = [line for line in entities_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cart_indexes = [index for index, line in enumerate(lines) if "chest_minecart" in line]
    if len(cart_indexes) < 2:
        raise RuntimeError("restore_order_wrong needs at least two chest minecarts in the snapshot")
    first, second = cart_indexes[0], cart_indexes[1]
    lines[first], lines[second] = lines[second], lines[first]
    entities_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    exp = infos["exp"]
    free_reserved_ports()
    cleanup_range_labs()
    provision_lab(exp, world=Path(str(fork["forkDir"])), test_mods=[AUDIT_JAR, Path(str(base_manifest["loggerJar"]["path"]))])
    audit_config(exp["name"], f"rom21-neg-restore-order-{int(time.time())}", exp["name"], config["scene"],
                 agent_uuid=agent_uuid, provenance={"kind": "swapped-snapshot"})
    retry(f"start {exp['name']}", lambda: lab("start", "--name", exp["name"], "--wait", "420"))
    rcon(exp["name"], "tick freeze")
    rcon(exp["name"], "mcaudit phase restore")
    start_bridge(exp["name"], exp["bridge"], neg_dir / "logs" / "bridge-restore-order.log")
    api = await connect(exp["bridge"])
    exp_world = str(LABS / exp["name"] / "world")
    _applied, apply_record = await call(api, "restore",
                                        {"directory": str(snapshot_copy), "dry_run": False, "target": exp["name"],
                                         "expect_instance": "server", "expect_world_dir": exp_world,
                                         "replace_existing": True}, timeout=900)
    verified, verify_record = await call(api, "verify", {"directory": str(snapshot_copy), "target": exp["name"]})
    await api.close()
    stop_bridges()
    write_json(swapped_dir / "restore-apply.json", apply_record)
    write_json(swapped_dir / "restore-verify.json", verified or {"error": verify_record})
    order_match = ((verified or {}).get("verification") or {}).get("orderHash", {}).get("match")
    synthetic_run = {
        "format": rv.RUN_FORMAT, "runId": f"rom21-neg-restore-order", "instance": exp["name"],
        "dimension": config["scene"]["dimension"], "program": config["program"],
        "programSha256": config["programSha256"], "expectedCartCount": config["expectedCartCount"],
        "scene": config["scene"], "operations": config["operations"], "loggerSessionOrdinal": 1,
        "loggerJar": base_manifest["loggerJar"], "auditJar": {"sha256": sha256_file(AUDIT_JAR)},
        "sourceWorld": {"before": {"tree_sha256": "x"}, "after": {"tree_sha256": "x"}},
    }
    synthetic_run["expectedOrderHash"] = str(fork.get("orderHash"))
    apply_error = str(apply_record.get("error") or "")
    verify_error = str((verify_record or {}).get("error") or "")
    guard_refused = (not bool(apply_record.get("ok"))) and "order_hash" in (apply_error + verify_error)
    verify_result = verified if isinstance(verified, dict) and "verification" in verified else {}
    report = rv.verify_run(synthetic_run, [], [], audit_check=None, restore_verify=verify_result)
    record = rv.live_negative("restore_order_wrong", synthetic_run, report,
                              evidence={"orderHashMatch": order_match, "applyOk": bool(apply_record.get("ok")),
                                        "guardRefusedOrderHash": guard_refused,
                                        "applyError": apply_error[:400], "verifyError": verify_error[:400]})
    record["failClosed"] = "restore_verified" in report["failures"] and guard_refused
    write_json(swapped_dir / "negative.json", record)
    records.append(record)
    lab("stop", "--name", exp["name"], "--timeout", "120", check=False)

    # offline mutations of the base run's real evidence
    for case in ("output_order_wrong", "audit_missing", "wrong_instance"):
        record = rv.offline_negative(base_dir, case)
        write_json(neg_dir / f"{case}.json", record)
        records.append(record)
        say(f"negative {case}: {'fail-closed ok' if record['failClosed'] else 'NOT fail-closed'} "
            f"({record['failedChecks']})")

    summary = {
        "format": "mc-agent/rom21-negatives@1",
        "at": utc_now(),
        "baseRunId": base_run["runId"],
        "cases": records,
        "allFailClosed": all(record["failClosed"] for record in records),
    }
    write_json(neg_dir / "summary.json", summary)
    say(f"negatives: {'all fail-closed' if summary['allFailClosed'] else 'SOME DID NOT FAIL CLOSED'}")
    return 0 if summary["allFailClosed"] else 1


async def run_generation_negative(
    args: argparse.Namespace,
    config: Mapping[str, Any],
    infos: Mapping[str, Mapping[str, Any]],
    run_dir: Path,
    runlog: RunLog,
    fork: Mapping[str, Any],
    agent_uuid: str,
    *,
    logger_jar: Path,
    jvm_args: Sequence[str] = (),
    no_press: bool = False,
    paced: bool = True,
    tamper: bool = False,
) -> dict[str, Any]:
    """A generation whose verification is expected to fail (negative probes)."""
    exp = infos["exp"]
    free_reserved_ports()
    cleanup_range_labs()
    provision_lab(exp, world=Path(str(fork["forkDir"])), test_mods=[AUDIT_JAR, logger_jar])
    audit_config(exp["name"], args.run_id, exp["name"], config["scene"], agent_uuid=agent_uuid,
                 provenance={"kind": "negative"})
    effective_jvm = scene_jvm_args(config["scene"], int(config["expectedCartCount"]), jvm_args)
    start_args: list[object] = ["start", "--name", exp["name"], "--wait", "420"]
    for arg in effective_jvm:
        start_args += ["--jvm-arg", arg]
    retry(f"start {exp['name']}", lambda: lab(*start_args))
    rcon(exp["name"], "tick freeze")
    start_bridge(exp["name"], exp["bridge"], run_dir / "logs" / "bridge.log")
    api = await connect(exp["bridge"])
    directory = str(fork["snapshotDir"])
    exp_world = str(LABS / exp["name"] / "world")
    rcon(exp["name"], "mcaudit phase restore")
    _applied, apply_record = await call(api, "restore",
                                        {"directory": directory, "dry_run": False, "target": exp["name"],
                                         "expect_instance": "server", "expect_world_dir": exp_world,
                                         "replace_existing": True}, timeout=900)
    verified, verify_record = await call(api, "verify", {"directory": directory, "target": exp["name"]})
    await api.close()
    stop_bridges()
    applied_result = apply_record.get("result") if isinstance(apply_record.get("result"), Mapping) else {}
    if not (applied_result.get("ok") or (applied_result.get("issued") and applied_result.get("count"))) \
            or not verified or not verified.get("ok"):
        raise RuntimeError(f"negative probe restore failed: {apply_record.get('error') or verify_record.get('error')}")
    ensure_seat_player(exp)
    rcon(exp["name"], "mcaudit phase experiment_start")
    if tamper:
        cart_uuid, slot = first_cart_uuid_and_slot(Path(directory))
        rcon(exp["name"], f"data modify entity {cart_uuid} Items[{{Slot:{slot}b}}].count set value 64", check=True)
    logger_path = LABS / exp["name"] / "audit" / "romlogger" / "romlog.jsonl"
    if no_press:
        rcon(exp["name"], "mcaudit mark operation-begin")
        rcon(exp["name"], "mcaudit mark operation-end")
    else:
        operate(exp, config, logger_path, runlog, paced=paced, label="negative")
    rcon(exp["name"], "mcaudit flush")
    rcon(exp["name"], "mcaudit phase experiment_end")
    rcon(exp["name"], "mcaudit end")
    time.sleep(1.0)
    audit_path = LABS / exp["name"] / "mc-audit" / f"audit-{args.run_id}.jsonl"
    if not audit_path.is_file():
        candidates = sorted((LABS / exp["name"] / "mc-audit").glob("audit-*.jsonl"))
        audit_path = candidates[-1] if candidates else audit_path
    shutil.copyfile(logger_path, run_dir / "logger.jsonl")
    if audit_path.is_file():
        shutil.copyfile(audit_path, run_dir / "audit.jsonl")
    # Hash the retained copies now: the server's shutdown flush appends a
    # logger_flushed record after this point, and the evidence hash must name
    # the artifact that is actually retained.
    retained_logger_sha = sha256_file(run_dir / "logger.jsonl")
    retained_audit_sha = sha256_file(run_dir / "audit.jsonl") if (run_dir / "audit.jsonl").is_file() else None
    audit_check = rv.run_audit_check(audit_path) if audit_path.is_file() else {"verdict": "missing"}
    write_json(run_dir / "audit-check.json", audit_check)
    run_manifest = {
        "format": rv.RUN_FORMAT, "runId": args.run_id, "configId": config["configId"], "instance": exp["name"],
        "dimension": config["scene"]["dimension"], "program": config["program"],
        "programSha256": config["programSha256"], "expectedCartCount": config["expectedCartCount"],
        "scene": config["scene"], "operations": config["operations"], "loggerSessionOrdinal": 1,
        "agentUuid": agent_uuid,
        "loggerJar": {"sha256": sha256_file(logger_jar), "size": logger_jar.stat().st_size,
                      "build": config["logger"]["buildVersion"], "sourceSha256": "same"},
        "auditJar": {"sha256": sha256_file(AUDIT_JAR)},
        "sourceWorld": {"before": {"tree_sha256": "x"}, "after": {"tree_sha256": "x"}},
    }
    report = rv.verify_run(run_manifest, read_jsonl(logger_path), read_jsonl(audit_path) if audit_path.is_file() else [],
                           audit_check=audit_check, restore_verify=verified)
    write_json(run_dir / "verification.json", report)
    lab("stop", "--name", exp["name"], "--timeout", "180", check=False)
    return {"verification": report, "loggerSha256": retained_logger_sha, "auditSha256": retained_audit_sha}


def first_cart_uuid_and_slot(snapshot_dir: Path) -> tuple[str, int]:
    """The first chest minecart in the fork and the slot of its first item."""
    for line in (Path(snapshot_dir) / "entities.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("type") != "minecraft:chest_minecart":
            continue
        match = re.search(r"\"Slot\":\s*(\d+)b", str(row.get("nbt") or ""))
        return str(row.get("uuid")), int(match.group(1)) if match else 0
    raise RuntimeError("snapshot holds no chest minecart")


# --------------------------------------------------------------------------- package


def portable_path(value: Any) -> Any:
    """Replace the checkout root in a packaging-generated path with ``repo``."""
    if not isinstance(value, str):
        return value
    text = value.replace("\\", "/")
    root = str(ROOT).replace("\\", "/").rstrip("/")
    if text.startswith(root + "/"):
        return "repo/" + text[len(root) + 1:]
    return text


def portable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: portable_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [portable_value(item) for item in value]
    return portable_path(value)


def _cold_fetch_record(run_dir: Path) -> dict[str, Any] | None:
    raw = read_json(run_dir / "fetch.json")
    if not raw or "stdout" not in raw:
        return None
    text = str(raw["stdout"])
    start = text.find("{")
    if start < 0:
        return None
    try:
        payload = json.loads(text[start:])
    except ValueError:
        return None
    cache = payload.get("cache") or {}
    return {
        "url": cache.get("url"),
        "http_status": cache.get("http_status"),
        "bytes": cache.get("bytes"),
        "sha256": cache.get("sha256"),
        "cache_hit": cache.get("cache_hit"),
        "seconds": cache.get("seconds"),
        "cachePath": portable_path(cache.get("path")),
        "pinnedSha256": "46954828589489f43819623cd42bb9ad6bbd999073f4fcc839d1817421a01387",
    }


def package_suite(args: argparse.Namespace) -> int:
    """Copy a passing suite's reviewable artifacts into the committed evidence dir."""
    suite_dir = Path(args.suite) if Path(args.suite).is_absolute() else (ROOT / args.suite)
    dest = Path(args.out) if Path(args.out).is_absolute() else (ROOT / args.out)
    suite = read_json(suite_dir / "suite.json")
    if suite.get("status") != "ok":
        raise RuntimeError("refusing to package a suite that is not ok")
    dest.mkdir(parents=True, exist_ok=True)
    runs_dir = dest / "runs"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True)
    packaged_runs: list[dict[str, Any]] = []
    for entry in suite["runs"]:
        run_dir = Path(entry["runDir"])
        run = read_json(run_dir / "run.json")
        if run.get("status") != "ok":
            raise RuntimeError(f"run {entry['runId']} is not ok")
        target = runs_dir / entry["runId"]
        target.mkdir(parents=True)
        for name in ("program.json", "init-summary.json", "fork.json", "logger.jsonl", "fetch.json",
                     "audit-check-gen1.json", "audit-check-gen2.json", "restore-verify-gen1.json",
                     "restore-verify-gen2.json", "verification-gen1.json", "verification-gen2.json",
                     "jar-swap-refusal.json", "fork-2.json", "steps.jsonl"):
            source = run_dir / name
            if source.is_file():
                shutil.copyfile(source, target / name)
        # Commit the raw logger and a projected audit: every audit record except
        # the periodic `cart_sample` stream, which does not take part in any
        # answer/order/inventory check.  The manifest pins the full raw hashes.
        for label in ("gen1", "gen2"):
            raw_audit = run_dir / f"audit-{label}.jsonl"
            if raw_audit.is_file():
                projected = [row for row in read_jsonl(raw_audit) if row.get("type") != "cart_sample"]
                projected_path = target / f"audit-{label}.projected.jsonl"
                projected_path.write_text(
                    "\n".join(json.dumps(row, ensure_ascii=False) for row in projected) + "\n",
                    encoding="utf-8", newline="\n",
                )
            raw_logger = run_dir / f"logger-{label}.jsonl"
            if raw_logger.is_file():
                shutil.copyfile(raw_logger, target / f"logger-{label}.jsonl")
        compact = {
            "runId": run["runId"], "configId": run["configId"], "configSha256": run["configSha256"],
            "instance": run["instance"], "programSha256": run["programSha256"],
            "expectedCartCount": run["expectedCartCount"], "agentUuid": run["agentUuid"],
            "cold": run.get("cold"), "jarIteration": run.get("jarIteration"),
            "loggerJar": portable_value(run.get("loggerJar")),
            "loggerJarIteration": portable_value(run.get("loggerJarIteration")),
            "auditJar": portable_value(run.get("auditJar")),
            "interfaceJar": portable_value(run.get("interfaceJar")),
            "mapWorld": portable_path(run.get("mapWorld")),
            "provenanceStart": {"head": run["provenanceStart"]["head"], "clean": run["provenanceStart"]["clean"],
                                "sources": run["provenanceStart"]["sources"]},
            "provenanceEnd": {"head": run.get("provenanceEnd", {}).get("head"),
                              "clean": run.get("provenanceEnd", {}).get("clean")},
            "sourceWorld": run.get("sourceWorld"),
            "coldFetch": _cold_fetch_record(run_dir),
            "auditProjection": (
                "audit-<label>.projected.jsonl is the raw audit log with the periodic cart_sample stream "
                "removed; it keeps every session/phase/input/cart event used by the verifier. The full raw "
                "audit files stay in the git-ignored lab directories and their sha256 is pinned per generation."
            ),
            "verifierInputs": (
                "verify-<label>/ is a complete verifier input (run.json + logger.jsonl + audit.jsonl + "
                "audit-check.json + restore-verify.json); `python tools/rom21_verify.py verify --run "
                "docs/evidence/rom21-regression/runs/<run-id>/verify-<label>` must exit 0"
            ),
            "generations": [
                {"label": generation["label"], "ordinal": generation["ordinal"],
                 "loggerJar": generation["loggerJar"], "auditCheck": generation["auditCheck"],
                 "restore": generation["restore"],
                 "verificationVerdict": generation["verification"]["verdict"],
                 "failures": generation["verification"]["failures"],
                 "answer": generation["verification"]["answer"],
                 "oracle": generation["verification"]["oracle"],
                 "observedPopOrder": generation["verification"]["observedPopOrder"],
                 "loggerSha256": generation["loggerSha256"], "auditSha256": generation["auditSha256"],
                 "deployedSha256": generation.get("deployedSha256")}
                for generation in run.get("generations", [])
            ],
        }
        write_json(target / "run.json", compact)
        # A self-verifiable sample per generation: a complete verifier input
        # directory.  A reviewer runs the offline verifier against the
        # committed raw logger + projected audit and must get PASS; packaging
        # itself asserts that before recording the package.
        verify_dirs: dict[str, str] = {}
        for generation in run.get("generations", []):
            label = generation["label"]
            verify_dir = target / f"verify-{label}"
            verify_dir.mkdir()
            projected = target / f"audit-{label}.projected.jsonl"
            projected_rows = read_jsonl(projected) if projected.is_file() else []
            sessions = [row.get("session") for row in projected_rows if row.get("type") == "session_start"]
            fork_path = run_dir / ("fork.json" if label == "gen1" else "fork-2.json")
            fork_record = read_json(fork_path, {}) or {}
            verify_manifest = {
                "format": rv.RUN_FORMAT,
                "runId": run["runId"], "configId": run["configId"], "instance": run["instance"],
                "dimension": run["dimension"], "program": run["program"],
                "programSha256": run["programSha256"], "expectedCartCount": run["expectedCartCount"],
                "scene": run["scene"], "operations": run["operations"],
                "loggerSessionOrdinal": generation["ordinal"], "agentUuid": run["agentUuid"],
                "loggerJar": portable_value(generation["loggerJar"]),
                "loggerJarIteration": None,
                "auditJar": portable_value(run["auditJar"]),
                "expectedOrderHash": fork_record.get("orderHash"),
                "auditSessionId": str(sessions[-1]) if sessions else None,
                "sourceWorld": run["sourceWorld"],
            }
            write_json(verify_dir / "run.json", verify_manifest)
            for source_name, target_name in (
                (f"logger-{label}.jsonl", "logger.jsonl"),
                (f"audit-{label}.projected.jsonl", "audit.jsonl"),
            ):
                source = target / source_name
                if source.is_file():
                    shutil.copyfile(source, verify_dir / target_name)
            for source_name, target_name in (
                (f"audit-check-{label}.json", "audit-check.json"),
                (f"restore-verify-{label}.json", "restore-verify.json"),
            ):
                source = run_dir / source_name
                if source.is_file():
                    shutil.copyfile(source, verify_dir / target_name)
            report = rv.verify_run(
                verify_manifest,
                read_jsonl(verify_dir / "logger.jsonl") if (verify_dir / "logger.jsonl").is_file() else [],
                projected_rows,
                audit_check=(read_json(verify_dir / "audit-check.json")
                             if (verify_dir / "audit-check.json").is_file() else None),
                restore_verify=(read_json(verify_dir / "restore-verify.json")
                                if (verify_dir / "restore-verify.json").is_file() else None),
            )
            if report["verdict"] != "pass":
                raise RuntimeError(
                    f"packaged verifier sample for {run['runId']}/{label} does not pass: {report['problems']}"
                )
            verify_dirs[label] = str(verify_dir.relative_to(dest).as_posix())
        packaged_runs.append({"runId": run["runId"], "dir": target.relative_to(dest).as_posix(),
                              "config": entry["config"],
                              "cold": entry["cold"], "jarIteration": entry["jarIteration"],
                              "verifyInputs": verify_dirs})
    negatives_dir = suite_dir / "negatives"
    if negatives_dir.is_dir():
        target_neg = dest / "negatives"
        if target_neg.exists():
            shutil.rmtree(target_neg)
        shutil.copytree(negatives_dir, target_neg,
                        ignore=shutil.ignore_patterns("snapshot", "logs", "*.jsonl"))
    manifest = {
        "format": "mc-agent/rom21-evidence@1",
        "suite": {"stamp": suite["stamp"], "startedAt": suite.get("startedAt"), "finishedAt": suite.get("finishedAt")},
        "runs": packaged_runs,
        "notes": [
            "These are regression results: real game instances, no model called. The original autonomous "
            "cold-start capability evidence is docs/evidence/rom20-coldstart.",
            "Copied raw records (logger/audit/restore/fork/init) are byte-faithful and therefore keep the "
            "host-local absolute paths the server wrote; package-generated fields use repo-relative paths.",
            "audit-<label>.projected.jsonl removes only the periodic cart_sample stream; the full raw audit "
            "hashes are pinned per generation in runs/<run-id>/run.json.",
            "verify-<label>/ is a complete verifier input; packaging re-ran tools/rom21_verify.py against it "
            "and required PASS before recording this manifest.",
        ],
        "committed": {},
    }
    write_json(dest / "manifest.json", manifest)
    # fill committed hashes after writing all files
    committed: dict[str, Any] = {}
    for path in sorted(dest.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            committed[path.relative_to(dest).as_posix()] = {"bytes": path.stat().st_size,
                                                           "sha256": sha256_file(path)}
    manifest["committed"] = committed
    write_json(dest / "manifest.json", manifest)
    say(f"packaged {len(packaged_runs)} run(s) into {dest}")
    return 0


# --------------------------------------------------------------------------- cli


def cmd_selftest(_args: argparse.Namespace) -> int:
    """Offline checks: config validation, verifier selftest, mutation helpers."""
    problems: list[str] = []
    for name in sorted(CONFIG_DIR.glob("*.json")):
        try:
            config = rv.load_config(name)
            print(f"[ok] config {name.name}: {config['expectedCartCount']} carts, "
                  f"program {config['programSha256'][:12]}")
        except rv.VerifyError as error:
            print(f"[FAIL] config {name.name}: {error}")
            problems.append(name.name)
    verify_code = rv.selftest()
    if verify_code != 0:
        problems.append("rom21_verify selftest")
    # The live commands must fail closed without game resources.
    for info in lab_info("selftest", 27200).values():
        if not (27000 <= info["server"] <= 27999):
            problems.append("port allocation outside the reserved range")
    print(f"rom21_regression selftest: {'PASS' if not problems else 'FAIL ' + ', '.join(problems)}")
    return 0 if not problems else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("selftest", help="offline checks; no game, no network")
    p.set_defaults(handler=cmd_selftest)

    p = sub.add_parser("run", help="one live regression run")
    p.add_argument("--config", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--out", default="labs/rom21-regression")
    p.add_argument("--base-port", type=int, default=27200)
    p.add_argument("--cold", action="store_true")
    p.add_argument("--cold-cache", default="")
    p.add_argument("--jar-iteration", action="store_true")
    p.add_argument("--allow-dirty", action="store_true", help="diagnostic only; recorded in the manifest")
    p.set_defaults(handler=lambda args: asyncio.run(execute_run(args)))

    p = sub.add_parser("suite", help="the three authoritative runs (2 configs, 1 cold)")
    p.add_argument("--stamp", default="")
    p.add_argument("--out", default="labs/rom21-regression")
    p.add_argument("--base-port", type=int, default=27200)
    p.add_argument("--allow-dirty", action="store_true")
    p.set_defaults(handler=lambda args: asyncio.run(execute_suite(args)))

    p = sub.add_parser("negatives", help="live fault probes + offline mutations")
    p.add_argument("--out", default="labs/rom21-regression")
    p.add_argument("--base-port", type=int, default=27208)
    p.set_defaults(handler=lambda args: asyncio.run(execute_negatives(args)))

    p = sub.add_parser("package", help="stage reviewable evidence from a passing suite")
    p.add_argument("--suite", default="labs/rom21-regression")
    p.add_argument("--out", default="docs/evidence/rom21-regression")
    p.set_defaults(handler=package_suite)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except (RuntimeError, rv.VerifyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
