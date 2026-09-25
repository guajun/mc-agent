#!/usr/bin/env python3
"""Drive disposable labs and prove the audit mod's behavior on a real server.

This is the live half of tests/mods/minecart-audit: it builds the mod, raises
void lab servers on the issue-specific ports (27180-27189), runs a generic
note-block machine with chest minecarts, exercises the negative cases the
acceptance criteria name, and runs tools/minecart_audit.py against the raw
server evidence.

    python tests/mods/minecart-audit/live_smoke.py [--skip-build]

Every lab is throwaway and lives under this worktree's ``labs/``; the evidence
is copied to ``labs/rom18-evidence/`` and summarised in ``summary.json``.
Nothing here touches the source save and no lab outlives the script.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MODULE = Path(__file__).resolve().parent
ROOT = MODULE.parents[2]
TOOLS = ROOT / "tools"
LABS = ROOT / "labs"
EVIDENCE = LABS / "rom18-evidence"
LAB_SERVER = TOOLS / "lab_server.py"
VERIFIER = TOOLS / "minecart_audit.py"
BUILDER = MODULE / "build.py"
MOD_JAR = MODULE / "dist" / "mc-minecart-audit-0.1.0.jar"
DEFAULT_MC_DIR = Path("D:/MC/MC_Game/.minecraft")


def default_mc_dir() -> str:
    configured = os.environ.get("MC_AGENT_MINECRAFT_DIR")
    if configured:
        return configured
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / ".minecraft")
    candidates.append(DEFAULT_MC_DIR)
    candidates.append(Path.home() / ".minecraft")
    for candidate in candidates:
        if (candidate / "versions" / "26.2-Fabric" / "26.2-Fabric.json").is_file():
            return str(candidate)
    for candidate in candidates:
        if any((candidate / "versions").glob("*/[!.]*.json")):
            return str(candidate)
    return str(candidates[0])


def java_major(java: Path) -> int:
    try:
        result = subprocess.run(
            [str(java), "-version"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    import re as _re

    match = _re.search(r'version "(\d+)', (result.stderr or "") + (result.stdout or ""))
    return int(match.group(1)) if match else 0


def _java_candidates() -> list[Path]:
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
    return candidates


def default_java() -> str:
    """A Java 25+ runtime: $MC_AGENT_JAVA, lab metadata, JAVA_HOME, PATH, HMCL."""
    for candidate in _java_candidates():
        if candidate.is_file() and java_major(candidate) >= 25:
            return str(candidate)
    return ""

# Ports 27180-27189 are reserved for this issue's labs.
LABS_CONFIG = {
    "rom18-a": (27180, 27181),
    "rom18-b": (27182, 27183),
    "rom18-ctl": (27184, 27185),
    "rom18-src": (27186, 27187),
}

def default_source_save(mc_dir: Path) -> Path:
    """Find Minecart ROM test under the launcher's saves or a version instance."""
    candidates: list[Path] = []
    versions = mc_dir / "versions"
    if versions.is_dir():
        for version in sorted(versions.iterdir()):
            candidates.append(version / "saves" / "Minecart ROM test")
    candidates.append(mc_dir / "saves" / "Minecart ROM test")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]

NOTE_1 = {"name": "note-block", "from": [0, -59, 0], "to": [0, -59, 0]}
STACK_1 = {"name": "stack", "from": [2, -60, -1], "to": [3, -59, 1]}
OUTPUT_1 = {"name": "output", "from": [4, -140, -8], "to": [8, -60, 8]}


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


def rotate_evidence(path: Path) -> None:
    """Keep every prior attempt: rename the old evidence dir instead of deleting."""
    if path.exists() and any(path.iterdir()):
        archived = path.with_name(path.name + ".attempt-" + time.strftime("%Y%m%d-%H%M%S"))
        path.rename(archived)


def prepare_lab(name: str, with_mod: bool, mc_dir: Path, java: Path, source_world: Path | None = None) -> Path:
    path = LABS / name
    path.mkdir(parents=True, exist_ok=True)
    # a running JVM holds the mod jar open on Windows; stop it before replacing files
    run([sys.executable, LAB_SERVER, "stop", "--name", name, "--timeout", "60"], check=False)
    game_port, rcon_port = LABS_CONFIG[name]
    state = path / "lab.json"
    if not state.exists():
        state.write_text(json.dumps({"serverPort": game_port, "rconPort": rcon_port}, indent=2) + "\n",
                         encoding="utf-8")
    if with_mod:
        # lab_server.copy_if_different only compares size, so a rebuilt jar of
        # the same size would be skipped; remove the target first (issue #17).
        (path / "mods" / MOD_JAR.name).unlink(missing_ok=True)
    command = [
        "provision", "--name", name, "--fabric-api", "--carpet", "--force",
        "--java", str(java),
    ]
    if source_world is not None:
        command += ["--world", str(source_world)]
    else:
        command.append("--void")
    if with_mod:
        command += ["--mod-jar", str(MOD_JAR)]
    lab(*command)
    if with_mod:
        shutil.copy2(MOD_JAR, path / "mods" / MOD_JAR.name)
    return path


def write_config(
    name: str,
    run_id: str,
    instance: str,
    input_regions: list[dict],
    stack: dict,
    output: dict,
    provenance: dict | None = None,
    agent_uuids: list[str] | None = None,
) -> Path:
    directory = LABS / name / "mc-audit"
    directory.mkdir(parents=True, exist_ok=True)
    config = {
        "runId": run_id,
        "instanceId": instance,
        "dimension": "minecraft:overworld",
        "provenance": provenance or {"kind": "synthetic-void", "reference": "live_smoke.py"},
        "inputRegions": input_regions,
        "stackRegion": stack,
        "outputRegion": output,
        "cartTypes": ["minecraft:chest_minecart"],
        "sampleIntervalTicks": 1,
        "agentUuids": agent_uuids or [],
    }
    path = directory / "config.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path


def reset_machine(name: str) -> None:
    rcon(name, "forceload add -16 -16 16 16")
    rcon(name, "fill -2 -60 -2 3 -60 2 minecraft:stone")
    rcon(name, "fill 8 -60 -2 13 -60 2 minecraft:stone")
    rcon(name, "kill @e[type=minecraft:chest_minecart]")
    rcon(name, "setblock 0 -59 0 minecraft:note_block")
    rcon(name, "setblock 1 -59 0 minecraft:observer[facing=west]")
    rcon(name, "setblock 2 -59 0 minecraft:piston[facing=east]")
    rcon(name, "setblock 10 -59 0 minecraft:note_block")
    rcon(name, "setblock 11 -59 0 minecraft:observer[facing=west]")
    rcon(name, "setblock 12 -59 0 minecraft:piston[facing=east]")


def summon_cart(name: str, x: float, z: float, item: str, count: int) -> None:
    nbt = f'{{Items:[{{Slot:0b,id:"minecraft:{item}",count:{count}}}]}}'
    rcon(name, f"summon minecraft:chest_minecart {x} -59.0 {z} {nbt}")


def ensure_bot(name: str, x: float, timeout: float = 20.0) -> None:
    """Spawn the Carpet fake player and wait until it is actually online.

    ``player ... spawn`` is asynchronous: the fake connection joins a moment
    later, and ``use once`` before that answers "Can only manipulate existing
    players". Poll the entity before aiming and using.
    """
    rcon(name, f"player Bot spawn at {x} -59.0 2.5 facing 180 29")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "has the following entity data" in rcon(name, "data get entity Bot Pos"):
            break
        time.sleep(0.5)
    else:
        raise SystemExit(f"fake player Bot never came online in {name}")
    rcon(name, f"tp Bot {x} -59.0 2.5 180 29")
    time.sleep(0.5)


def fresh_world(name: str) -> None:
    """Regenerate the void level so no cart or block from a prior run leaks in."""
    lab("stop", "--name", name, "--timeout", "60")
    shutil.rmtree(LABS / name / "world", ignore_errors=True)


def test_passed(output: str) -> bool:
    return "Test passed" in output


def scenario_positive(name: str, run_id: str, instance: str, split_restart: bool) -> dict:
    write_config(name, run_id, instance, [NOTE_1], STACK_1, OUTPUT_1)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit phase init")
    reset_machine(name)
    summon_cart(name, 3.5, 0.5, "apple", 3)
    if split_restart:
        rcon(name, "mcaudit flush")
        lab("stop", "--name", name, "--timeout", "120")
        lab("start", "--name", name, "--wait", "300")
    ensure_bot(name, 0.5)
    rcon(name, "mcaudit phase experiment_start")
    rcon(name, "player Bot use once")
    time.sleep(1)
    note_cycled = test_passed(rcon(name, "execute if block 0 -59 0 minecraft:note_block[note=1]"))
    if not note_cycled:
        raise SystemExit(f"positive scenario on {name}: the fake player did not tune the note block")
    position_output = rcon(name, "data get entity @e[type=minecraft:chest_minecart,limit=1] Pos")
    pushed = False
    if "has the following entity data" in position_output:
        try:
            pushed = float(position_output.split("[", 1)[1].split("d", 1)[0]) >= 4.0
        except (IndexError, ValueError):
            pushed = False
    observable = {
        "note_cycled": note_cycled,
        "cart_pushed": pushed,
    }
    rcon(name, "tick sprint 600")
    time.sleep(3)
    observable["cart_removed"] = not test_passed(rcon(name, "execute if entity @e[type=minecraft:chest_minecart]"))
    rcon(name, "mcaudit phase experiment_end")
    rcon(name, "mcaudit end")
    lab("stop", "--name", name, "--timeout", "120")
    return observable


def scenario_noop(name: str, run_id: str, instance: str) -> dict:
    write_config(name, run_id, instance, [NOTE_1], STACK_1, OUTPUT_1)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit phase init")
    reset_machine(name)
    summon_cart(name, 3.5, 0.5, "apple", 3)
    ensure_bot(name, 0.5)
    rcon(name, "mcaudit phase experiment_start")
    time.sleep(1)
    rcon(name, "tick sprint 100")
    time.sleep(2)
    rcon(name, "mcaudit mark no-operation-negative")
    rcon(name, "mcaudit phase experiment_end")
    rcon(name, "mcaudit end")
    observable = {"cart_still_present": test_passed(rcon(name, "execute if entity @e[type=minecraft:chest_minecart]"))}
    lab("stop", "--name", name, "--timeout", "120")
    return observable


def scenario_wrong_position(name: str, run_id: str, instance: str) -> dict:
    # only machine 1 is a configured input; the bot uses machine 2
    write_config(name, run_id, instance, [NOTE_1], STACK_1, OUTPUT_1)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit phase init")
    reset_machine(name)
    summon_cart(name, 13.5, 0.5, "apple", 3)
    ensure_bot(name, 10.5)
    rcon(name, "mcaudit phase experiment_start")
    rcon(name, "player Bot use once")
    time.sleep(1)
    if not test_passed(rcon(name, "execute if block 10 -59 0 minecraft:note_block[note=1]")):
        raise SystemExit(f"wrong-position scenario on {name}: the fake player did not tune machine 2")
    observable = {
        "wrong_note_cycled": test_passed(rcon(name, "execute if block 10 -59 0 minecraft:note_block[note=1]")),
        "configured_note_untouched": test_passed(rcon(name, "execute if block 0 -59 0 minecraft:note_block[note=0]")),
    }
    rcon(name, "tick sprint 100")
    time.sleep(2)
    rcon(name, "mcaudit phase experiment_end")
    rcon(name, "mcaudit end")
    lab("stop", "--name", name, "--timeout", "120")
    return observable


def scenario_marker_only(name: str, run_id: str, instance: str) -> dict:
    write_config(name, run_id, instance, [NOTE_1], STACK_1, OUTPUT_1)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit phase init")
    reset_machine(name)
    summon_cart(name, 3.5, 0.5, "apple", 3)
    rcon(name, "mcaudit mark answer=minecraft:apple")
    rcon(name, "mcaudit phase experiment_start")
    rcon(name, "mcaudit mark answer=minecraft:apple,count=3")
    rcon(name, "mcaudit phase experiment_end")
    rcon(name, "mcaudit end")
    lab("stop", "--name", name, "--timeout", "120")
    return {"marker_written": True}


def scenario_answer_only(name: str, run_id: str, instance: str) -> dict:
    """The correct answer without any operation: still not an auditable run."""
    write_config(name, run_id, instance, [NOTE_1], STACK_1, OUTPUT_1)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit phase init")
    reset_machine(name)
    summon_cart(name, 3.5, 0.5, "apple", 3)
    assert_dir = LABS / name / "mc-audit"
    assert_dir.mkdir(parents=True, exist_ok=True)
    answer = assert_dir / "answer.json"
    answer.write_text(
        json.dumps({"answer": [{"item": "minecraft:apple", "count": 3}], "submitted_by": "test-side"}),
        encoding="utf-8",
    )
    rcon(name, "mcaudit phase experiment_start")
    rcon(name, "mcaudit phase experiment_end")
    rcon(name, "mcaudit end")
    observable = {"answer_submitted": answer.exists(), "answer_file": str(answer)}
    lab("stop", "--name", name, "--timeout", "120")
    return observable


def scenario_environment_only(name: str, run_id: str, instance: str) -> dict:
    write_config(name, run_id, instance, [NOTE_1], STACK_1, OUTPUT_1)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit phase init")
    reset_machine(name)
    summon_cart(name, 3.5, 0.5, "apple", 3)
    rcon(name, "mcaudit phase experiment_start")
    # redstone, not a player: a real machine trigger that cannot be an Agent act
    rcon(name, "setblock 0 -60 0 minecraft:redstone_block")
    time.sleep(1)
    rcon(name, "tick sprint 600")
    time.sleep(3)
    observable = {"cart_removed": not test_passed(rcon(name, "execute if entity @e[type=minecraft:chest_minecart]"))}
    rcon(name, "mcaudit phase experiment_end")
    rcon(name, "mcaudit end")
    lab("stop", "--name", name, "--timeout", "120")
    return observable


def scenario_source_world(mc_dir: Path, java: Path, source_save: Path) -> dict:
    """Load a read-only copy of the source save with the audit mod installed.

    This is the source-world half of "both the source world and the restored
    copy can load the test mod"; it does not touch the machine or the original
    save. The restored-copy half depends on the bridge restore work.
    """
    name = "rom18-src"
    if not source_save.is_dir():
        return {"skipped": f"source save not present: {source_save}"}
    prepare_lab(name, with_mod=True, mc_dir=mc_dir, java=java, source_world=source_save)
    run_id = f"meta18-src-{time.strftime('%Y%m%d-%H%M%S')}"
    write_config(
        name,
        run_id,
        "lab-rom18-src",
        [NOTE_1],
        {"name": "stack", "from": [-64, -64, -64], "to": [64, 64, 64]},
        OUTPUT_1,
        provenance={"kind": "source-world", "reference": str(source_save)},
    )
    lab("start", "--name", name, "--wait", "300")
    status = rcon(name, "mcaudit status")
    console = (LABS / name / "logs" / "console.log").read_text(encoding="utf-8", errors="replace")
    observable = {
        "run_id": run_id,
        "server_loaded_source_world": "Done (" in console.split("===== lab_server start")[-1],
        "audit_ready": "phase=ready" in status,
        "no_mixin_or_audit_errors": "Mixin apply" not in console and "mcaudit: audit disabled" not in console,
    }
    rcon(name, "mcaudit end")
    lab("stop", "--name", name, "--timeout", "120")
    return observable


def scenario_crash(name: str, run_id: str, instance: str) -> dict:
    """Kill the server mid-experiment: the abandoned session must be incomplete."""
    write_config(name, run_id, instance, [NOTE_1], STACK_1, OUTPUT_1)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit phase init")
    reset_machine(name)
    summon_cart(name, 3.5, 0.5, "apple", 3)
    rcon(name, "mcaudit phase experiment_start")
    time.sleep(1)
    lab("stop", "--name", name, "--force")  # no console, no audit_end
    lab("start", "--name", name, "--wait", "300")
    rcon(name, "mcaudit end")  # closes the second session only
    lab("stop", "--name", name, "--timeout", "120")
    return {"first_session_abandoned": True}


def scenario_control(mc_dir: Path, java: Path) -> dict:
    name = "rom18-ctl"
    prepare_lab(name, with_mod=False, mc_dir=mc_dir, java=java)
    fresh_world(name)
    lab("start", "--name", name, "--wait", "300")
    reset_machine(name)
    summon_cart(name, 3.5, 0.5, "apple", 3)
    ensure_bot(name, 0.5)
    rcon(name, "player Bot use once")
    time.sleep(1)
    observable = {
        "note_cycled": test_passed(rcon(name, "execute if block 0 -59 0 minecraft:note_block[note=1]")),
        "cart_pushed": not test_passed(rcon(name, "execute if entity @e[type=minecraft:chest_minecart]")),
    }
    rcon(name, "tick sprint 600")
    time.sleep(3)
    observable["cart_removed"] = not test_passed(rcon(name, "execute if entity @e[type=minecraft:chest_minecart]"))
    lab("stop", "--name", name, "--timeout", "120")
    return observable


def scenario_unconfigured(mc_dir: Path, java: Path) -> dict:
    name = "rom18-b"
    prepare_lab(name, with_mod=True, mc_dir=mc_dir, java=java)
    config = LABS / name / "mc-audit" / "config.json"
    backup = config.with_suffix(".json.bak")
    if config.exists():
        shutil.move(str(config), str(backup))
    try:
        lab("start", "--name", name, "--wait", "300")
        status = rcon(name, "mcaudit status")
        unconfigured = LABS / name / "mc-audit" / "status-unconfigured.json"
        observable = {
            "command_reports_unconfigured": "not configured" in status,
            "status_file_written": unconfigured.exists(),
        }
        lab("stop", "--name", name, "--timeout", "120")
    finally:
        if backup.exists():
            shutil.move(str(backup), str(config))
    return observable


def verify(name: str, run_id: str, expect: list[str], allow_no_operation: bool = False) -> dict:
    log = LABS / name / "mc-audit" / f"audit-{run_id}.jsonl"
    status = LABS / name / "mc-audit" / f"status-{run_id}.json"
    report_path = EVIDENCE / f"report-{name}-{run_id}.json"
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, VERIFIER, "check", "--log", str(log), "--status", str(status),
               "--json", "--json-out", str(report_path)]
    if allow_no_operation:
        command.append("--allow-no-operation")
    result = run(command, check=False)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        raise SystemExit(f"verifier did not return JSON for {log}:\n{result.stdout}\n{result.stderr}")
    report["expected"] = expect
    report["matched_expectation"] = report["verdict"] in expect
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--keep-labs", action="store_true", help="keep the disposable labs for inspection")
    parser.add_argument("--mc-dir", default=default_mc_dir())
    parser.add_argument("--source-save", default="", help="read-only source save for the load check")
    parser.add_argument("--allow-missing-source-world", action="store_true")
    parser.add_argument("--java", default=default_java(), help="Java 25 executable; discovered by default")
    args = parser.parse_args()
    mc_dir = Path(args.mc_dir)
    if not args.java:
        raise SystemExit("no Java found: pass --java or set MC_AGENT_JAVA")
    java = Path(args.java)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    rotate_evidence(EVIDENCE)
    if not args.skip_build:
        run([sys.executable, BUILDER, "--minecraft-dir", str(mc_dir), "--jdk", str(java.parent.parent)])
    if not MOD_JAR.is_file():
        raise SystemExit(f"mod jar missing: run {BUILDER}")

    summary: dict = {"stamp": stamp, "modJar": str(MOD_JAR), "scenarios": {}, "checks": {}}
    prepare_lab("rom18-a", with_mod=True, mc_dir=mc_dir, java=java)
    prepare_lab("rom18-b", with_mod=True, mc_dir=mc_dir, java=java)
    for existing in ("rom18-a", "rom18-b", "rom18-src"):
        run([sys.executable, LAB_SERVER, "stop", "--name", existing, "--timeout", "60"], check=False)
        shutil.rmtree(LABS / existing / "mc-audit", ignore_errors=True)

    # 1) unconfigured mod must be loud, not silently accepted
    summary["scenarios"]["unconfigured"] = scenario_unconfigured(mc_dir, java)

    # 2) positive run across a server restart: two sessions, one machine op
    run_a = f"meta18-a-{stamp}"
    summary["scenarios"]["positive"] = scenario_positive("rom18-a", run_a, "lab-rom18-a", split_restart=True)
    summary["scenarios"]["positive"]["verdict"] = verify("rom18-a", run_a, ["pass"])

    # 3) negative behaviors
    run_noop = f"meta18-noop-{stamp}"
    summary["scenarios"]["no_operation"] = scenario_noop("rom18-b", run_noop, "lab-rom18-b")
    summary["scenarios"]["no_operation"]["verdict"] = verify("rom18-b", run_noop, ["fail"])

    run_wrong = f"meta18-wrong-{stamp}"
    summary["scenarios"]["wrong_position"] = scenario_wrong_position("rom18-b", run_wrong, "lab-rom18-b")
    summary["scenarios"]["wrong_position"]["verdict"] = verify("rom18-b", run_wrong, ["fail"])

    run_marker = f"meta18-marker-{stamp}"
    summary["scenarios"]["marker_only"] = scenario_marker_only("rom18-b", run_marker, "lab-rom18-b")
    summary["scenarios"]["marker_only"]["verdict"] = verify("rom18-b", run_marker, ["fail"])

    run_answer = f"meta18-answer-{stamp}"
    summary["scenarios"]["answer_only"] = scenario_answer_only("rom18-b", run_answer, "lab-rom18-b")
    summary["scenarios"]["answer_only"]["verdict"] = verify("rom18-b", run_answer, ["fail"])

    run_env = f"meta18-env-{stamp}"
    summary["scenarios"]["environment_only"] = scenario_environment_only("rom18-b", run_env, "lab-rom18-b")
    summary["scenarios"]["environment_only"]["verdict"] = verify("rom18-b", run_env, ["fail"])

    # 4) the source save (read-only copy) loads with the audit mod installed
    source_save = Path(args.source_save) if args.source_save else default_source_save(mc_dir)
    summary["scenarios"]["source_world"] = scenario_source_world(mc_dir, java, source_save)
    if "run_id" in summary["scenarios"]["source_world"]:
        source_run = summary["scenarios"]["source_world"]["run_id"]
        summary["scenarios"]["source_world"]["verdict"] = verify(
            "rom18-src", source_run, ["pass"], allow_no_operation=True)

    # 4b) a killed server leaves an open session; the verifier must say incomplete
    run_crash = f"meta18-crash-{stamp}"
    summary["scenarios"]["crash_abandoned_session"] = scenario_crash("rom18-b", run_crash, "lab-rom18-b")
    summary["scenarios"]["crash_abandoned_session"]["verdict"] = verify(
        "rom18-b", run_crash, ["incomplete"])

    # 5) control lab without the audit mod: fixture behavior is unchanged
    summary["scenarios"]["control_no_mod"] = scenario_control(mc_dir, java)

    # 6) evidence bookkeeping and cross-instance separation
    for name in ("rom18-a", "rom18-b", "rom18-src"):
        target = EVIDENCE / name
        source = LABS / name / "mc-audit"
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            shutil.copytree(source, target)
        console = LABS / name / "logs" / "console.log"
        if console.exists():
            shutil.copy2(console, EVIDENCE / f"console-{name}.log")
    if not args.keep_labs:
        # Evidence is copied; dropping the labs frees the ports for the next driver.
        for name in ("rom18-a", "rom18-b", "rom18-ctl", "rom18-src"):
            shutil.rmtree(LABS / name, ignore_errors=True)
    logs = sorted(EVIDENCE.glob("rom18-a/audit-*.jsonl")) + sorted(EVIDENCE.glob("rom18-b/audit-*.jsonl"))
    cross = []
    for log in logs:
        text = log.read_text(encoding="utf-8", errors="replace")
        other = "lab-rom18-b" if "rom18-a" in str(log) else "lab-rom18-a"
        if other in text:
            cross.append(str(log))
    summary["checks"]["cross_instance_logs"] = cross == []

    positive = summary["scenarios"]["positive"]
    control = summary["scenarios"]["control_no_mod"]
    summary["checks"]["positive_pass"] = positive["verdict"]["verdict"] == "pass"
    summary["checks"]["negative_cases_fail"] = all(
        summary["scenarios"][key]["verdict"]["verdict"] == "fail"
        for key in ("no_operation", "wrong_position", "marker_only", "answer_only", "environment_only")
    )
    summary["checks"]["unconfigured_detected"] = (
        summary["scenarios"]["unconfigured"]["command_reports_unconfigured"]
        and summary["scenarios"]["unconfigured"]["status_file_written"]
    )
    summary["checks"]["fixture_behavior_unchanged"] = (
        control.get("note_cycled") and control.get("cart_removed")
        and positive.get("note_cycled") and positive.get("cart_removed")
    )
    summary["checks"]["sessions_after_restart"] = len(positive["verdict"]["sessions"]) >= 2
    source = summary["scenarios"]["source_world"]
    if source.get("skipped"):
        summary["checks"]["source_world_loads_mod"] = bool(args.allow_missing_source_world)
        summary["checks"]["source_world_reason"] = str(source.get("skipped"))
    else:
        summary["checks"]["source_world_loads_mod"] = (
            source.get("server_loaded_source_world")
            and source.get("audit_ready")
            and source.get("no_mixin_or_audit_errors")
            and source.get("verdict", {}).get("verdict") == "pass"
        )
    summary["checks"]["crash_marks_incomplete"] = (
        summary["scenarios"]["crash_abandoned_session"]["verdict"]["verdict"] == "incomplete"
    )
    summary["all_passed"] = all(
        value for key, value in summary["checks"].items() if key != "source_world_reason"
    )

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"evidence: {EVIDENCE}")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
