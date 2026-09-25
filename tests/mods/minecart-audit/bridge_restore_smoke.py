#!/usr/bin/env python3
"""Combined live verification for the audit mod on the merged bridge/mod stack.

This is the integration half of tests/mods/minecart-audit and issue #18. It
does not solve any ROM: the fixture is a generic three-cart stack, the machine
is a generic note-block/observer/piston, and the destination is a throwaway
void lab.

Stages:

1. clean bridge under test (default: the ``codex/rom13-bridge6`` worktree, the
   merged guarded-restore + ``player.view`` revision) with the clean interface
   mod 0.6.0;
2. the audit mod installed in the source-audit lab and the experiment lab
   (``--test-mod``, so a missing audit mod refuses to start);
3. generic stacked chest-minecart fixture, forked under the bridge's guarded
   fork;
4. the destination lab loads the fork world and the bridge restores it under
   guard with ``expect_world_dir`` and ``replace_existing``, then verifies full
   state (order, counts, dimension, positions, velocities, NBT/items);
5. the audit log's restored-cart inventories are cross-checked against the
   bridge's post-restore snapshot;
6. ``player``/``player.view`` smoke on the restored destination;
7. a generic note-block machine in the destination produces a real
   input -> server processing -> cart output chain while the restored copy is
   still tracked;
8. coexistence with the generic smoke mod from #17 (``examples/smoke-mod``)
   deployed as a normal agent mod. That smoke mod proves deployment only; it
   is never a substitute for the agent's own logger.

    python tests/mods/minecart-audit/bridge_restore_smoke.py [--skip-build]
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

MODULE = Path(__file__).resolve().parent
ROOT = MODULE.parents[2]
TOOLS = ROOT / "tools"
LABS = ROOT / "labs"
EVIDENCE = LABS / "rom18-b6-evidence"
LAB_SERVER = TOOLS / "lab_server.py"
BUILDER = MODULE / "build.py"
MOD_JAR = MODULE / "dist" / "mc-minecart-audit-0.1.0.jar"
INTERFACE_JAR = LABS / "_build" / "mods" / "mc-agent-interface-0.6.0.jar"
INTERFACE_SHA = "45f12e16b3979be6a699ac3c744b2a68dfcf8dd2379f5987bf9b9319adf4404f"
SMOKE_JAR = LABS / "_build" / "mods" / "mcagent-smoke-0.1.0.jar"
SMOKE_SOURCE = ROOT / "examples" / "smoke-mod"
DEFAULT_BRIDGE_SOURCE = Path("F:/mc-agent-worktrees/rom13/bridge6")
DEFAULT_BRIDGE_HEAD = "4116ebb34b60e962e95834791d521b369fd52c65"
DEFAULT_VENV_PYTHON = Path("F:/mc-agent/.venv/Scripts/python.exe")
DEFAULT_MC_DIR = Path("D:/MC/MC_Game/.minecraft")


def java_major(java: Path) -> int:
    try:
        result = subprocess.run(
            [str(java), "-version"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    match = re.search(r'version "(\d+)', (result.stderr or "") + (result.stdout or ""))
    return int(match.group(1)) if match else 0


def default_java() -> str:
    """A Java 25+ runtime: $MC_AGENT_JAVA, lab metadata, JAVA_HOME, PATH, HMCL."""
    candidates: list[Path] = []
    configured = os.environ.get("MC_AGENT_JAVA")
    if configured:
        candidates.append(Path(configured))
    for lab_json in sorted((ROOT / "labs").glob("*/lab.json")):
        try:
            recorded = json.loads(lab_json.read_text(encoding="utf-8")).get("java") or ""
        except (OSError, ValueError):
            continue
        if recorded:
            candidates.append(Path(recorded))
    home = os.environ.get("JAVA_HOME")
    if home:
        candidates.append(Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java"))
    found = shutil.which("java")
    if found:
        candidates.append(Path(found))
    appdata = os.environ.get("APPDATA")
    if appdata:
        for pattern in ("*/*/bin/java.exe", "*/*/bin/java"):
            candidates.extend(sorted((Path(appdata) / ".hmcl" / "java").glob(pattern)))
    for candidate in candidates:
        if candidate.is_file() and java_major(candidate) >= 25:
            return str(candidate)
    return ""

FIXTURE = [
    ("11111111-1111-4111-8111-111111111111", 0.5, 100.0, 0.5, "minecraft:diamond", 5),
    ("22222222-2222-4222-8222-222222222222", 0.5, 101.0, 0.5, "minecraft:emerald", 2),
    ("33333333-3333-4333-8333-333333333333", 0.5, 102.0, 0.5, "minecraft:redstone", 7),
]

LABS_CONFIG = {
    "rom18-b6-src": {"ports": (27180, 27181, 27182, 27183), "instance": "src-audit"},
    "rom18-b6-dst": {"ports": (27184, 27185, 27186, 27187), "instance": "exp-1"},
}


def run(argv: list[object], check: bool = True, timeout: float = 900) -> subprocess.CompletedProcess:
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


def lab(*args: object) -> str:
    return run([sys.executable, LAB_SERVER, *args]).stdout.strip()


def rcon(name: str, command: str) -> str:
    return lab("exec", "--name", name, command)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_passed(output: str) -> bool:
    return "Test passed" in output


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_audit_config(name: str, run_id: str, instance: str, stack: dict, provenance: dict) -> None:
    config = {
        "runId": run_id,
        "instanceId": instance,
        "dimension": "minecraft:overworld",
        "provenance": provenance,
        "inputRegions": [{"name": "note-block", "from": [0, -59, 0], "to": [0, -59, 0]}],
        "stackRegion": stack,
        "outputRegion": {"name": "output", "from": [4, -140, -8], "to": [8, 103, 8]},
        "cartTypes": ["minecraft:chest_minecart"],
        "sampleIntervalTicks": 1,
        "agentUuids": [],
    }
    directory = LABS / name / "mc-audit"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def rotate_evidence(path: Path) -> None:
    """Keep every prior attempt: rename the old evidence dir instead of deleting."""
    if path.exists() and any(path.iterdir()):
        archived = path.with_name(path.name + ".attempt-" + time.strftime("%Y%m%d-%H%M%S"))
        path.rename(archived)


def prepare_lab(name: str, with_mod: bool, java: Path, jdk: Path, ports: tuple, world: Path | None, test_mod: bool) -> Path:
    path = LABS / name
    path.mkdir(parents=True, exist_ok=True)
    run([sys.executable, LAB_SERVER, "stop", "--name", name, "--timeout", "60"], check=False)
    command = [
        "provision", "--name", name, "--fabric-api", "--carpet", "--force",
        "--java", str(java), "--jdk", str(jdk),
        "--server-port", str(ports[0]), "--rcon-port", str(ports[1]),
        "--vantage-port", str(ports[2]), "--bridge-port", str(ports[3]),
    ]
    if world is not None:
        command += ["--world", str(world)]
    else:
        command.append("--void")
    if with_mod:
        command += ["--mod-jar", str(INTERFACE_JAR)]
    if test_mod:
        command += ["--test-mod", str(MOD_JAR)]
    else:
        command += ["--mod-jar", str(MOD_JAR)]
    if name == "rom18-b6-dst" and SMOKE_JAR.is_file():
        command += ["--mod-jar", str(SMOKE_JAR)]
    lab(*command)
    return path


BRIDGE_PROCESSES: list[subprocess.Popen] = []


def _stop_bridges() -> None:
    for process in BRIDGE_PROCESSES:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()


atexit.register(_stop_bridges)


def start_bridge(name: str, bridge_source: Path, python: Path, api_port: int) -> tuple[subprocess.Popen, Path]:
    lab_dir = LABS / name
    log_path = EVIDENCE / f"bridge-{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(bridge_source / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    handle = open(log_path, "wb")
    process = subprocess.Popen(
        [str(python), "-m", "mc_agent_bridge.cli", "run", "--api-port", str(api_port), "--server-dir", str(lab_dir)],
        cwd=str(bridge_source),
        env=environment,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )
    BRIDGE_PROCESSES.append(process)
    return process, log_path


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


async def call(api, method: str, params: dict | None = None, timeout: float = 180.0):
    started = time.time()
    try:
        result = await api.call(method, params or {}, timeout=timeout)
        record = {
            "method": method,
            "params": params or {},
            "ok": True,
            "result": result,
            "seconds": round(time.time() - started, 3),
        }
        return result, record
    except Exception as error:  # noqa: BLE001 - refusals are evidence too
        record = {
            "method": method,
            "params": params or {},
            "ok": False,
            "error": str(error),
            "seconds": round(time.time() - started, 3),
        }
        return None, record


def uuid_ints(value: str) -> list[int]:
    plain = value.replace("-", "")
    return [
        int.from_bytes(bytes.fromhex(plain[index : index + 8]), "big", signed=True)
        for index in range(0, 32, 8)
    ]


def cart_nbt(uuid: str, item: str, count: int) -> str:
    ints = ",".join(str(value) for value in uuid_ints(uuid))
    return (
        "{NoGravity:1b,"
        f'Items:[{{Slot:0b,id:"{item}",count:{count}}}],'
        'Motion:[0.0d,0.0d,0.0d],'
        f"UUID:[I;{ints}]}}"
    )


_ITEM_RE = re.compile(r'\{([^{}]*)\}')


def parse_nbt_items(nbt: str) -> list[dict]:
    """Pull the ordered Items list out of a snapshot NBT string.

    The NBT is JSON-ish (``{"count":5,"Slot":0b,"id":"minecraft:diamond"}``)
    both before and after 26.2's component era, so the patterns accept quoted
    and unquoted keys alike.
    """
    match = re.search(r'"?Items"?\s*:\s*\[(.*)\]', nbt or "")
    if not match:
        return []
    items = []
    for body in _ITEM_RE.findall(match.group(1)):
        item = re.search(r'"?id"?\s*:\s*"([^"]+)"', body)
        count = re.search(r'"?count"?\s*:\s*(\d+)', body)
        slot = re.search(r'"?Slot"?\s*:\s*(\d+)b', body)
        if item and count:
            items.append(
                {
                    "slot": int(slot.group(1)) if slot else 0,
                    "item": item.group(1),
                    "count": int(count.group(1)),
                }
            )
    items.sort(key=lambda entry: entry["slot"])
    return items


def audit_inventory_for(log_path: Path, uuid: str) -> list[dict] | None:
    inventory = None
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        event = json.loads(line)
        if event.get("type") == "cart_tracked" and str(event.get("uuid")) == uuid:
            raw = event.get("inventory")
            if isinstance(raw, list):
                inventory = [
                    {"slot": entry["slot"], "item": entry["item"], "count": entry["count"]}
                    for entry in raw
                ]
    return inventory


async def run_stages(args: argparse.Namespace) -> int:
    bridge_source = Path(args.bridge_source)
    if not (bridge_source / "src" / "mc_agent_bridge" / "restore.py").is_file():
        raise SystemExit(f"not a bridge source tree with restore.py: {bridge_source}")
    head = run(["git", "-C", str(bridge_source), "rev-parse", "HEAD"]).stdout.strip()
    if str(bridge_source / "src") not in sys.path:
        sys.path.insert(0, str(bridge_source / "src"))
    if args.expect_bridge_head and head != args.expect_bridge_head:
        raise SystemExit(
            f"bridge source is at {head}, expected {args.expect_bridge_head}; "
            "update --expect-bridge-head after review"
        )
    if INTERFACE_JAR.is_file() and sha256(INTERFACE_JAR) != INTERFACE_SHA:
        raise SystemExit(f"interface jar hash mismatch: {INTERFACE_JAR}")
    if not MOD_JAR.is_file():
        raise SystemExit(f"audit jar missing: {MOD_JAR}")

    jdk = Path(args.java).parent.parent
    python = Path(args.python)
    if not python.is_file():
        raise SystemExit(f"python with bridge dependencies not found: {python}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    rotate_evidence(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "stamp": stamp,
        "bridge": {"source": str(bridge_source), "head": head, "expectedHead": args.expect_bridge_head},
        "interface": {"jar": str(INTERFACE_JAR), "sha256": INTERFACE_SHA},
        "auditJar": {"path": str(MOD_JAR), "sha256": sha256(MOD_JAR)},
        "stages": {},
        "checks": {},
    }

    src_run = f"meta18-b6-src-{stamp}"
    dst_run = f"meta18-b6-dst-{stamp}"
    src_stack = {"name": "stack", "from": [0, 99, 0], "to": [1, 103, 1]}
    dst_stack = {"name": "stack", "from": [0, -60, -1], "to": [3, 103, 1]}
    write_audit_config(
        "rom18-b6-src", src_run, "src-audit", src_stack,
        {"kind": "synthetic-void", "reference": "bridge_restore_smoke.py stage 1"},
    )

    # -- source lab, fixture, fork ------------------------------------------
    prepare_lab("rom18-b6-src", with_mod=True, java=Path(args.java), jdk=jdk,
                ports=LABS_CONFIG["rom18-b6-src"]["ports"], world=None, test_mod=True)
    lab("start", "--name", "rom18-b6-src", "--wait", "300")
    src_bridge, _src_log = start_bridge("rom18-b6-src", bridge_source, python, LABS_CONFIG["rom18-b6-src"]["ports"][3])
    api_src = await connect(LABS_CONFIG["rom18-b6-src"]["ports"][3])
    rcon("rom18-b6-src", "mcaudit phase init")
    try:
        fixture_records = []
        for command in (
            "gamerule spawn_monsters false",
            "gamerule advance_time false",
            "gamerule random_tick_speed 0",
            "difficulty peaceful",
            "forceload add 0 0 0 0",
        ):
            _result, record = await call(api_src, "command_output", {"command": command, "wait": 0.3})
            fixture_records.append(record)
        for uuid, x, y, z, item, count in FIXTURE:
            _result, record = await call(
                api_src,
                "command_output",
                {"command": f"summon minecraft:chest_minecart {x} {y} {z} {cart_nbt(uuid, item, count)}", "wait": 0.3},
            )
            fixture_records.append(record)
        _result, record = await call(api_src, "command_output", {"command": "save-all flush", "wait": 1.0})
        fixture_records.append(record)
        before, record = await call(api_src, "snapshot", {"name": "meta18-source-before", "radius": 0})
        fixture_records.append(record)
        fork_result, record = await call(
            api_src, "fork", {"name": "meta18-b6-fixture", "radius": 0, "freeze": True}, timeout=600
        )
        fixture_records.append(record)
    finally:
        await api_src.close()
    write_json(EVIDENCE / "stage-src-fixture-fork.json", fixture_records)
    if not fork_result:
        raise SystemExit("fork failed; see stage-src-fixture-fork.json")
    summary["stages"]["fork"] = {
        "orderHash": fork_result.get("orderHash"),
        "entities": fork_result.get("entities"),
        "snapshotDir": fork_result.get("snapshotDir"),
        "forkDir": fork_result.get("forkDir"),
        "worldDir": fork_result.get("worldDir"),
        "manifest": fork_result.get("manifest"),
    }

    # -- smoke mod from #17 (coexistence, built against the lab) ------------
    if not args.skip_build:
        build = run([
            sys.executable, TOOLS / "build_mod.py",
            "--source", SMOKE_SOURCE, "--lab", "rom18-b6-src",
            "--out", SMOKE_JAR, "--version", "0.1.0", "--json",
        ])
        json_start = build.stdout.find("{")
        if json_start < 0:
            raise SystemExit(
                "build_mod.py --json printed no JSON: " + build.stdout + " / " + build.stderr
            )
        build_meta = json.loads(build.stdout[json_start:])
        write_json(EVIDENCE / "smoke-mod-build.json", build_meta)
        summary["stages"]["smokeModBuild"] = {
            "jar": str(SMOKE_JAR),
            "sha256": sha256(SMOKE_JAR),
            "source": str(SMOKE_SOURCE),
        }

    # -- destination lab from the fork world --------------------------------
    dst_world = Path(fork_result["forkDir"])
    shutil.rmtree(LABS / "rom18-b6-dst", ignore_errors=True)
    prepare_lab("rom18-b6-dst", with_mod=True, java=Path(args.java), jdk=jdk,
                ports=LABS_CONFIG["rom18-b6-dst"]["ports"], world=dst_world, test_mod=True)
    write_audit_config(
        "rom18-b6-dst", dst_run, "exp-1", dst_stack,
        {
            "kind": "fork",
            "reference": str(dst_world),
            "snapshotId": Path(fork_result["snapshotDir"]).name,
            "snapshotHash": fork_result.get("orderHash"),
        },
    )
    lab("start", "--name", "rom18-b6-dst", "--wait", "300")
    dst_bridge, _dst_log = start_bridge("rom18-b6-dst", bridge_source, python, LABS_CONFIG["rom18-b6-dst"]["ports"][3])
    api_dst = await connect(LABS_CONFIG["rom18-b6-dst"]["ports"][3])
    directory = fork_result["snapshotDir"]
    dst_world_dir = LABS / "rom18-b6-dst" / "world"
    restore_records = []
    try:
        rcon("rom18-b6-dst", "mcaudit phase restore")
        dry, record = await call(api_dst, "restore", {
            "directory": directory, "target": "rom18-b6-dst",
            "expect_instance": "server", "expect_world_dir": str(dst_world_dir),
        })
        restore_records.append(record)
        applied, record = await call(api_dst, "restore", {
            "directory": directory, "dry_run": False, "target": "rom18-b6-dst",
            "expect_instance": "server", "expect_world_dir": str(dst_world_dir),
            "replace_existing": True,
        }, timeout=600)
        restore_records.append(record)
        verified, record = await call(api_dst, "verify", {"directory": directory, "target": "rom18-b6-dst"})
        restore_records.append(record)
    finally:
        await api_dst.close()
    write_json(EVIDENCE / "stage-restore.json", restore_records)
    if not (applied and applied.get("ok") and verified and verified.get("ok")):
        raise SystemExit("guarded restore or verification failed; see stage-restore.json")
    summary["stages"]["restore"] = {
        "applied": {
            "ok": applied.get("ok"),
            "issued": applied.get("issued"),
            "failed": applied.get("failed"),
            "existing": applied["checks"]["existing"],
            "dimension": applied["checks"]["dimension"],
            "tick": applied.get("tick"),
        },
        "verify": {
            "ok": verified.get("ok"),
            "matched": verified["verification"]["matched"],
            "orderHash": verified["verification"]["orderHash"],
            "missing": verified["verification"]["missing"],
            "nbtMismatches": verified["verification"]["nbtMismatches"]["count"],
            "snapshotDir": verified.get("snapshotDir"),
        },
    }

    # -- audit cross-check: restored carts vs the audit inventory -----------
    dst_log_path = LABS / "rom18-b6-dst" / "mc-audit" / f"audit-{dst_run}.jsonl"
    from mc_agent_bridge import fork as bridge_fork  # noqa: E402

    _meta, snapshot_entities = bridge_fork.read_snapshot(verified["snapshotDir"])
    crosscheck = []
    for entity in snapshot_entities:
        uuid = str(entity.get("uuid"))
        expected = parse_nbt_items(str(entity.get("nbt") or ""))
        actual = audit_inventory_for(dst_log_path, uuid)
        crosscheck.append({"uuid": uuid, "nbtItems": expected, "auditInventory": actual,
                           "match": actual == expected})
    summary["stages"]["restoredAuditCrosscheck"] = crosscheck
    summary["checks"]["restored_audit_inventory_match"] = bool(crosscheck) and all(
        row["match"] for row in crosscheck
    )
    write_json(EVIDENCE / "stage-restored-audit-crosscheck.json", crosscheck)

    # -- player.view smoke on the restored destination ----------------------
    api_dst = await connect(LABS_CONFIG["rom18-b6-dst"]["ports"][3])
    player_records = []
    try:
        _result, record = await call(api_dst, "command_output", {
            "command": "player Bot spawn at 0.5 100.0 0.5 facing 90 0", "wait": 0.5})
        player_records.append(record)
        player = None
        for _ in range(20):
            player, record = await call(api_dst, "player", {"player": "Bot"})
            if player and player.get("found"):
                break
            await asyncio.sleep(0.25)
        player_records.append(record)
    finally:
        await api_dst.close()
    context = (player or {}).get("player") or {}
    view = context.get("view") or {}
    summary["stages"]["playerView"] = {
        "found": bool((player or {}).get("found")),
        "uuid": context.get("uuid"),
        "dimension": context.get("dimension"),
        "eye": view.get("eye"),
        "direction": view.get("direction"),
    }
    summary["checks"]["player_view_ok"] = bool(
        (player or {}).get("found")
        and isinstance(view.get("eye"), list)
        and isinstance(view.get("direction"), list)
    )
    write_json(EVIDENCE / "stage-player-view.json", player_records)

    # -- generic machine in the destination, audit chain --------------------
    rcon("rom18-b6-dst", "mcaudit phase experiment_start")
    for command in (
        "forceload add -16 -16 16 16",
        "fill -2 -60 -2 3 -60 2 minecraft:stone",
        "setblock 0 -59 0 minecraft:note_block",
        "setblock 1 -59 0 minecraft:observer[facing=west]",
        "setblock 2 -59 0 minecraft:piston[facing=east]",
        'summon minecraft:chest_minecart 3.5 -59.0 0.5 {Items:[{Slot:0b,id:"minecraft:apple",count:3}]}',
    ):
        rcon("rom18-b6-dst", command)
    rcon("rom18-b6-dst", "tp Bot 0.5 -59.0 2.5 180 29")
    time.sleep(0.5)
    rcon("rom18-b6-dst", "player Bot use once")
    time.sleep(1)
    note_cycled = test_passed(rcon("rom18-b6-dst", "execute if block 0 -59 0 minecraft:note_block[note=1]"))
    if not note_cycled:
        raise SystemExit("destination machine: the fake player did not tune the note block")
    rcon("rom18-b6-dst", "tick sprint 600")
    time.sleep(3)
    cart_gone = not test_passed(rcon(
        "rom18-b6-dst",
        "execute if entity @e[type=minecraft:chest_minecart,x=3,y=-140,z=-1,dx=6,dy=80,dz=2]",
    ))
    rcon("rom18-b6-dst", "mcaudit phase experiment_end")
    rcon("rom18-b6-dst", "mcaudit end")
    summary["stages"]["destinationMachine"] = {"note_cycled": note_cycled, "cart_gone": cart_gone}
    summary["checks"]["destination_machine_ok"] = bool(note_cycled and cart_gone)

    # -- coexistence with the #17 smoke mod ---------------------------------
    smoke_status = rcon("rom18-b6-dst", "mcagent-smoke status")
    smoke_sample = rcon("rom18-b6-dst", "mcagent-smoke sample coalesce")
    verify_output = run(
        [sys.executable, LAB_SERVER, "verify", "--name", "rom18-b6-dst", "--require-vantage"], check=False
    )
    sample_file = LABS / "rom18-b6-dst" / "audit" / "sample-coalesce.json"
    audit_status = rcon("rom18-b6-dst", "mcaudit status")
    coexistence = {
        "smokeStatus": smoke_status,
        "smokeSample": smoke_sample,
        "smokeSampleFile": str(sample_file),
        "smokeSampleWritten": sample_file.is_file(),
        "auditStatus": audit_status,
        "labVerifyExit": verify_output.returncode,
        "labVerifyOutput": verify_output.stdout.strip(),
    }
    summary["stages"]["coexistence"] = coexistence
    summary["checks"]["smoke_mod_coexists"] = (
        "[mc-agent-smoke]" in smoke_status
        and sample_file.is_file()
        and verify_output.returncode == 0
        and "phase=" in audit_status
    )
    write_json(EVIDENCE / "stage-coexistence.json", coexistence)

    # -- bookkeeping ---------------------------------------------------------
    for name in ("rom18-b6-src", "rom18-b6-dst"):
        target = EVIDENCE / name
        shutil.rmtree(target, ignore_errors=True)
        if (LABS / name / "mc-audit").exists():
            shutil.copytree(LABS / name / "mc-audit", target)
        console = LABS / name / "logs" / "console.log"
        if console.exists():
            shutil.copy2(console, EVIDENCE / f"console-{name}.log")
    run([sys.executable, LAB_SERVER, "stop", "--name", "rom18-b6-dst", "--timeout", "120"], check=False)
    run([sys.executable, LAB_SERVER, "stop", "--name", "rom18-b6-src", "--timeout", "120"], check=False)
    for process in (src_bridge, dst_bridge):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
    if not args.keep_labs:
        # Evidence is copied; dropping the labs frees the ports for the next run.
        for name in ("rom18-b6-src", "rom18-b6-dst"):
            shutil.rmtree(LABS / name, ignore_errors=True)

    summary["checks"]["restore_faithful"] = bool(applied.get("ok") and verified.get("ok"))
    summary["checks"]["source_run_id"] = src_run
    summary["checks"]["experiment_run_id"] = dst_run
    summary["all_passed"] = all(
        value for key, value in summary["checks"].items() if key not in ("source_run_id", "experiment_run_id")
    )
    write_json(EVIDENCE / "summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"evidence: {EVIDENCE}")
    return 0 if summary["all_passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--keep-labs", action="store_true", help="keep the disposable labs for inspection")
    parser.add_argument("--bridge-source", default=str(DEFAULT_BRIDGE_SOURCE))
    parser.add_argument("--expect-bridge-head", default=DEFAULT_BRIDGE_HEAD)
    parser.add_argument("--python", default=str(DEFAULT_VENV_PYTHON))
    parser.add_argument("--java", default=default_java())
    args = parser.parse_args()
    return asyncio.run(run_stages(args))


if __name__ == "__main__":
    raise SystemExit(main())
