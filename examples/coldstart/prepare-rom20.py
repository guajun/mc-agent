#!/usr/bin/env python3
"""meta20 (rom20) operator preparation for the Minecart ROM cold start.

This is *operator* tooling, not the cold-start agent.  It prepares a fresh,
reproducible ready environment and then stops: it never launches a model, never
operates the ROM machine, and never writes an answer or a logger.  Everything it
records lives under ``labs/<run-id>/`` (run trajectory + operator receipts) plus
the outcome report outside the repository.

What one full run does, in order:

1. ``context``    - verify the pinned toolpaths/jars, prepare the clean
                    agent-context workspace (TASK.md, ENV.md, skill) and open
                    the run trajectory (``run_trace.py init``);
2. ``source``     - verify the read-only source save against
                    ``source-before.json`` and the expected tree hash;
3. ``preflight``  - generic full harness preflight (``harness-pi.json``) on the
                    reserved ports, lab ``rom20-src``, all real checks PASS;
4. ``fixture``    - fetch the pinned map ZIP from the remote with a cold cache,
                    reset ``rom20-src`` into the fixture world, install the
                    independent audit mod as a required test mod, configure the
                    audit, start it, and initialize a fresh sealed challenge
                    (5 carts) leaving note=20 at the frozen tick 6000;
5. ``experiment`` - provision ``rom20-exp`` blank (void) with the same
                    dependencies and audit configuration; the agent itself must
                    fork/restore/verify the runtime state;
6. ``bridges``    - start one detached clean bridge per lab (correct
                    PYTHONPATH) and wait until each answers ``status``;
7. ``finalize``   - declare evidence, write the marks, the outcome report and
                    the launch command; leave both labs and bridges running.

Usage (always with the repository venv Python)::

    F:/mc-agent/.venv/Scripts/python.exe examples/coldstart/prepare-rom20.py run \
        --run-id rom20-20260926T060500Z

The tracked inputs live in ``examples/coldstart/rom20-environment.json`` and are
**host-specific**: copy the record, set ``repo.root`` to the checkout that runs
the script, adjust the other absolute paths and pass ``--env <file>`` (or set
``ROM20_ENV``).  A run root is never overwritten (reruns need a new ``--run-id``,
there is no ``--force``), and ``--stages`` is a diagnostic subset for one
process, not a resume.  The generated launch command is PowerShell and passes an
explicit ``--session``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[2]
ENV_PATH = Path(__file__).resolve().parent / "rom20-environment.json"
LABS_ROOT = REPO / "labs"

STAGES = ("clean", "context", "source", "preflight", "fixture", "experiment", "bridges", "finalize")

#: A run id is one path-free directory component (never ``..`` or an absolute path).
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?")

ENV_REQUIRED = (
    "repo", "python", "java", "jdk", "pi", "model", "bridge", "agent_loop",
    "interface_jar", "audit_jar", "map_manifest", "task_file", "skill_source",
    "source_save", "source_before", "source_tree_sha256", "source_files",
    "task_uuid", "sessions", "reports", "labs", "audit", "challenge",
    "cache_reuse",
)


class PrepError(RuntimeError):
    """A preparation step failed; the run root keeps the receipts."""


# --------------------------------------------------------------------------- small helpers


def say(message: str = "") -> None:
    print(f"[prep] {message}", flush=True)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text("utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, "utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def tail(text: str, limit: int = 2000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


# --------------------------------------------------------------------------- guards


def require_file(path: Path, what: str) -> Path:
    path = Path(path)
    if not path.is_file():
        raise PrepError(f"{what} is missing: {path}")
    return path


def require_dir(path: Path, what: str) -> Path:
    path = Path(path)
    if not path.is_dir():
        raise PrepError(f"{what} is missing: {path}")
    return path


def require_json(path: Path, what: str) -> Any:
    path = Path(path)
    require_file(path, what)
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PrepError(f"{what} is not readable JSON: {path} ({error})") from error


def require_hashed_file(path: Path, expected_sha256: str, what: str) -> Path:
    path = require_file(path, what)
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise PrepError(f"{what} sha256 {actual} != pinned {expected_sha256}: {path}")
    return path


def validate_run_id(run_id: str) -> str:
    if not run_id or run_id in (".", "..") or not RUN_ID_PATTERN.fullmatch(run_id):
        raise PrepError(
            f"bad --run-id {run_id!r}: use 1-63 letters, digits, dot, dash or underscore,"
            " starting and ending with a letter or digit"
        )
    return run_id


def contained_child(root: Path, name: str, what: str) -> Path:
    """Resolve ``root/name`` and fail closed when it escapes ``root``."""
    root = Path(root).resolve()
    child = (root / name).resolve()
    if child == root or root not in child.parents:
        raise PrepError(f"{what} {name!r} escapes {root}")
    return child


def is_reparse_point(path: Path) -> bool:
    """True for a symlink or a Windows junction; removal through both is refused."""
    if os.path.islink(path):
        return True
    try:
        return bool(getattr(os.lstat(path), "st_reparse_tag", 0))
    except OSError:
        return False


def remove_tree_safely(path: Path, *, root: Path, what: str) -> None:
    path = Path(path)
    if is_reparse_point(path):
        raise PrepError(f"refusing to remove {what} {path}: it is a symlink/junction")
    resolved = path.resolve()
    root = Path(root).resolve()
    if resolved == root or root not in resolved.parents:
        raise PrepError(f"refusing to remove {what} {path}: outside {root}")
    try:
        shutil.rmtree(path)
    except OSError as error:
        raise PrepError(f"could not remove {what} {path}: {error}") from error


def git_required(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        raise PrepError(
            f"git {' '.join(args)} failed in {root}: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def git_status_lines(root: Path) -> list[str] | None:
    """Porcelain status lines, or ``None`` when git could not answer."""
    result = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def git_state_entry(root: Path) -> dict[str, Any]:
    lines = git_status_lines(root)
    return {
        "root": str(root),
        "branch": git(root, "rev-parse", "--abbrev-ref", "HEAD") or None,
        "commit": git(root, "rev-parse", "HEAD") or None,
        "dirty_entries": lines,
        "clean": None if lines is None else not lines,
    }


def assert_dependencies(env: dict[str, Any]) -> dict[str, Any]:
    """Fail closed when the clean bridge/agent-loop checkouts are not at their pins."""
    result: dict[str, Any] = {}
    for key, root, pinned in (
        ("bridge", Path(env["bridge"]["worktree"]), env["bridge"]["commit"]),
        ("agent_loop", Path(env["agent_loop"]["root"]), env["agent_loop"]["commit"]),
    ):
        require_dir(root, f"{key} checkout")
        head = git_required(root, "rev-parse", "HEAD")
        if head != pinned:
            raise PrepError(f"{key} HEAD {head} != pinned {pinned} in {root}")
        lines = git_status_lines(root)
        if lines is None:
            raise PrepError(f"{key} checkout {root} is not a readable git worktree")
        if lines:
            raise PrepError(f"{key} checkout {root} is dirty: {lines[:5]}")
        result[key] = {
            "root": str(root), "commit": head, "clean": True,
            "src_tree": git(root, "rev-parse", "HEAD:src") or None,
        }
    return result


def pid_identity(pid: int) -> dict[str, Any] | None:
    """Current image and process creation time for a pid, or ``None``."""
    if pid <= 0:
        return None
    sys.path.insert(0, str(REPO / "tools"))
    import lab_server

    if os.name == "nt":
        return lab_server._windows_process_info(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return {"exe": "", "created": None}


def bridge_kill_plan(
    record: dict[str, Any], owner: int | None, identity_lookup=pid_identity
) -> dict[str, Any]:
    """Decide which recorded bridge pids may be terminated.

    Both the spawn pid and the socket owner (a child of the venv launcher) are
    recorded with a start time.  A pid is only killed when its current identity
    matches the recorded start time; anything else is skipped, never killed.
    """
    pids = {
        "pid": int(record.get("pid") or 0),
        "listen_pid": int(record.get("listen_pid") or 0),
    }
    candidates = {value for value in pids.values() if value}
    if owner is not None and owner not in candidates:
        raise PrepError(
            f"port {record.get('api_port')} is owned by pid {owner}, not by the recorded"
            f" bridge pids {sorted(candidates)}; refusing to kill an unrelated process"
        )
    targets: list[int] = []
    skipped: list[dict[str, Any]] = []
    for role in ("pid", "listen_pid"):
        pid = pids[role]
        if not pid:
            continue
        identity = identity_lookup(pid)
        if identity is None:
            skipped.append({"pid": pid, "role": role, "reason": "not running"})
            continue
        recorded = record.get("pid_created" if role == "pid" else "listen_created")
        if recorded is None:
            skipped.append({"pid": pid, "role": role, "reason": "no recorded start time; not killing"})
            continue
        created = identity.get("created")
        exe = str(identity.get("exe") or "").lower()
        if exe and Path(exe).name not in ("python.exe", "pythonw.exe", "python", "python3", "python3.exe"):
            skipped.append({"pid": pid, "role": role, "reason": f"not a python image ({identity.get('exe')})"})
            continue
        if created is None or abs(float(created) - float(recorded)) > 5:
            skipped.append({
                "pid": pid, "role": role, "reason": "identity/start time mismatch; not killing",
                "recorded_created": recorded, "actual_created": created,
            })
            continue
        targets.append(pid)
    return {"targets": sorted(set(targets)), "skipped": skipped}


def allowed_mod_names(env: dict[str, Any], manifest: dict[str, Any]) -> set[str]:
    allowed = {mod["filename"] for mod in (manifest.get("mods") or []) if mod.get("filename")}
    allowed.add(Path(env["interface_jar"]["path"]).name)
    allowed.add(Path(env["audit_jar"]["path"]).name)
    return allowed


def mods_to_prune(present: Iterable[str], allowed: set[str]) -> list[str]:
    return sorted(name for name in present if name not in allowed)


def assert_blank_lab(lab_state: dict[str, Any], audit_status: dict[str, Any] | None, lab: str) -> dict[str, Any]:
    """Positive proof that the experiment lab carries no fixture state yet."""
    world = (lab_state or {}).get("world")
    if world != "void":
        raise PrepError(f"lab {lab} world kind is {world!r}, expected 'void' (blank)")
    tracked = ((audit_status or {}).get("carts") or {}).get("tracked")
    if tracked != 0:
        raise PrepError(f"lab {lab} audit reports {tracked!r} tracked carts, expected 0 before the agent starts")
    return {"world": world, "tracked_carts": tracked}


def derive_versions(manifest: dict[str, Any], audit_jar_path: str | Path) -> dict[str, Any]:
    """Version metadata from the pinned manifest and the accepted audit jar name."""
    game = manifest.get("game") or {}
    mods = {mod.get("name"): mod for mod in (manifest.get("mods") or [])}

    def mod_version(name: str) -> str:
        return str((mods.get(name) or {}).get("version") or "")

    match = re.search(r"-(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.\-]+)?)\.jar$", Path(audit_jar_path).name)
    versions = {
        "minecraft": str(game.get("minecraft") or ""),
        "fabric_loader": str(game.get("fabric_loader") or ""),
        "fabric_installer": str(game.get("fabric_installer") or ""),
        "fabric_api": mod_version("fabric-api"),
        "carpet": mod_version("carpet"),
        "interface_mod": mod_version("mc-agent-interface-mod"),
        "minecart_audit": match.group(1) if match else "",
    }
    empty = sorted(key for key, value in versions.items() if not value)
    if empty:
        raise PrepError(f"cannot derive version metadata for {empty} from the pinned manifest/audit jar")
    return versions


def ordered_stages(value: str) -> list[str]:
    """Parse a ``--stages`` diagnostic subset; reject unknown, duplicate or unordered names."""
    selected = [stage for stage in (value.split(",") if value else STAGES) if stage]
    unknown = sorted(set(selected) - set(STAGES))
    if unknown:
        raise PrepError(f"unknown stage(s): {unknown}; known: {', '.join(STAGES)}")
    if len(set(selected)) != len(selected):
        raise PrepError("duplicate stage(s) requested")
    indexes = [STAGES.index(stage) for stage in selected]
    if indexes != sorted(indexes):
        raise PrepError(f"stages out of order: {selected}")
    return selected


def write_failure(
    operator: Path, run_id: str, stage: str, error: str, receipts_written: Iterable[str] = ()
) -> None:
    payload = {
        "schema": "mc-agent/rom20-prep-failure/1",
        "run_id": run_id,
        "stage": stage,
        "error": error,
        "receipts_written": sorted(set(receipts_written)),
        "at": utc_now(),
    }
    try:
        write_json(operator / "failure.json", payload)
    except OSError:
        pass  # a failure record must never mask the original failure


class Receipts:
    """Operator receipts; every write is hashed into the run record."""

    def __init__(self, operator_dir: Path, run_id: str = "") -> None:
        self.dir = operator_dir
        self.run_id = run_id
        (self.dir / "receipts").mkdir(parents=True, exist_ok=True)
        (self.dir / "logs").mkdir(parents=True, exist_ok=True)

    def write(self, name: str, payload: Any) -> dict[str, Any]:
        path = self.dir / "receipts" / f"{name}.json"
        if path.exists():
            raise PrepError(f"receipt {path} already exists; attempts are preserved, use a new --run-id")
        record = {"at": utc_now(), "name": name, "run_id": self.run_id, **payload}
        write_json(path, record)
        return {**record, "receipt_path": str(path), "receipt_sha256": sha256_file(path)}

    def log(self, name: str, text: str) -> dict[str, Any]:
        path = self.dir / "logs" / f"{name}.log"
        path.write_text(text, "utf-8", newline="\n")
        return {"path": str(path), "sha256": sha256_file(path), "bytes": len(text.encode("utf-8"))}


def run(
    argv: Iterable[str],
    *,
    cwd: Path = REPO,
    timeout: float = 900.0,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> dict[str, Any]:
    """Run one preparation command and return its transcript record."""
    command = [str(part) for part in argv]
    started = time.time()
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    shown = " ".join(f'"{part}"' if " " in part else part for part in command)
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=full_env,
        )
        code: int | None = completed.returncode
        out = completed.stdout or ""
        err = completed.stderr or ""
    except subprocess.TimeoutExpired as error:
        code = None
        out = str(error.stdout or "")
        err = f"[timeout after {timeout}s] {error.stderr or ''}"
    record = {
        "command": shown,
        "cwd": str(cwd),
        "exit_code": code,
        "seconds": round(time.time() - started, 3),
        "stdout": out,
        "stderr": err,
    }
    if check and code != 0:
        raise PrepError(
            f"command failed ({code}): {shown}\n--- stdout ---\n{tail(out)}\n--- stderr ---\n{tail(err)}"
        )
    return record


def run_json(argv: Iterable[str], **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    record = run(argv, **kwargs)
    text = record["stdout"].strip()
    try:
        return json.loads(text), record
    except json.JSONDecodeError as error:
        raise PrepError(f"expected JSON from {record['command']}: {error}\n{tail(text)}") from error


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


# --------------------------------------------------------------------------- environment


def load_env(path: Path = ENV_PATH) -> dict[str, Any]:
    path = Path(path)
    env = require_json(path, "rom20 environment record")
    if not isinstance(env, dict) or env.get("schema") != "mc-agent/rom20-environment/1":
        raise PrepError(f"{path} is not a rom20 environment record (schema mc-agent/rom20-environment/1)")
    missing = [key for key in ENV_REQUIRED if key not in env]
    if missing:
        raise PrepError(f"{path} is missing required keys: {missing}")
    return env


def ensure_repo(env: dict[str, Any], env_path: Path = ENV_PATH) -> None:
    """Keep every relative path anchored in the worktree that owns this script."""
    declared = Path(env["repo"]["root"]).resolve()
    if declared != REPO.resolve():
        raise PrepError(
            f"the environment record {env_path} names repo.root {declared}, but this script runs from"
            f" {REPO}. The record is host-specific: copy it (for example to a hosts/ file), set"
            " repo.root to this checkout, adjust the other absolute paths and pass --env <file>."
        )


def ensure_venv(env: dict[str, Any], argv: list[str]) -> None:
    """Re-exec under the pinned venv Python when invoked with another one."""
    want = Path(env["python"]).resolve()
    current = Path(sys.executable).resolve()
    if current != want:
        say(f"re-executing under {want}")
        os.execv(str(want), [str(want), str(Path(__file__).resolve()), *argv])


def check_pi(env: dict[str, Any]) -> dict[str, Any]:
    pi = env["pi"]["command"]
    result = run([pi, "--version"], timeout=60)
    version = result["stdout"].strip() or result["stderr"].strip()
    if env["pi"]["version"] not in version:
        raise PrepError(f"pi version mismatch: expected {env['pi']['version']}, got {version!r}")
    return {"command": pi, "version_output": version, "expected": env["pi"]["version"]}


def check_java(env: dict[str, Any]) -> dict[str, Any]:
    java = env["java"]
    version = run([java, "-version"], timeout=60)
    text = (version["stderr"] or version["stdout"]).strip()
    return {"path": java, "version_output": text.splitlines()[0] if text else "", "sha256": sha256_file(Path(java))}


def check_pycache(env: dict[str, Any]) -> dict[str, Any]:
    result = run([env["python"], "--version"], timeout=60)
    return {"path": env["python"], "version": (result["stdout"] or result["stderr"]).strip()}


# --------------------------------------------------------------------------- siblings / cache / wrapper


def ensure_junction(link: Path, target: Path) -> dict[str, Any]:
    """Create a directory junction (Windows) / symlink (POSIX) if missing."""
    if link.exists():
        resolved = Path(os.path.realpath(link)).resolve()
        if resolved != target.resolve():
            raise PrepError(f"{link} exists but resolves to {resolved}, not {target}; remove it and re-run")
        return {"path": str(link), "target": str(target), "action": "present"}
    if not target.is_dir():
        raise PrepError(f"junction target {target} does not exist")
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            raise PrepError(f"mklink /J failed: {result.stdout}{result.stderr}")
    else:
        link.symlink_to(target, target_is_directory=True)
    if not (link / "src").is_dir():
        raise PrepError(f"{link} does not expose a src/ tree after linking")
    return {"path": str(link), "target": str(target), "action": "created"}


def seed_cache(env: dict[str, Any], receipts: Receipts) -> dict[str, Any]:
    """Reuse the verified downloaded runtime cache without touching other labs."""
    manifest = require_json(REPO / env["map_manifest"], "map manifest")
    cache_source = Path(env["cache_reuse"]["mods_from"])
    target = LABS_ROOT / "_cache" / "mods"
    target.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    pins = {mod["filename"]: mod["sha256"] for mod in manifest.get("mods") or [] if mod.get("filename")}
    for filename, expected in pins.items():
        source = cache_source / filename
        if not source.is_file():
            continue
        destination = target / filename
        if sha256_file(source) != expected:
            raise PrepError(f"cached {source} does not match the pinned sha256 {expected}")
        if not destination.is_file() or sha256_file(destination) != expected:
            shutil.copy2(source, destination)
        copied.append({"file": filename, "sha256": expected, "origin": str(source)})
    launcher_source = Path(env["cache_reuse"]["launcher_from"])
    launcher_target = LABS_ROOT / "_cache"
    launcher_target.mkdir(parents=True, exist_ok=True)
    for launcher in sorted(launcher_source.glob("fabric-server-mc.*.jar")):
        destination = launcher_target / launcher.name
        if not destination.is_file():
            shutil.copy2(launcher, destination)
        copied.append({"file": launcher.name, "sha256": sha256_file(destination), "origin": str(launcher)})
    return receipts.write("cache-seed", {
        "copied": copied,
        "mods_dir": str(target),
        "source_present": cache_source.is_dir(),
        "note": "a missing host cache is fine: ensure_pinned_mods downloads the pinned jars later",
    })


def write_bridge_wrapper(env: dict[str, Any]) -> Path:
    """A .cmd shim that pins PYTHONPATH to the clean bridge source."""
    wrapper = REPO / env["bridge"]["wrapper"]
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    text = (
        "@echo off\n"
        "setlocal\n"
        f'set "PYTHONPATH={Path(env["bridge"]["src"])}"\n'
        f'"{Path(env["python"])}" -m mc_agent_bridge.cli %*\n'
        "exit /b %ERRORLEVEL%\n"
    )
    wrapper.write_text(text, "utf-8", newline="\n")
    result = run([str(wrapper), "--help"], cwd=REPO, timeout=120)
    if "run" not in result["stdout"]:
        raise PrepError("bridge wrapper did not answer --help")
    return wrapper


# --------------------------------------------------------------------------- workspace


ENV_TEMPLATE = string.Template(
    """# Environment - Minecart ROM cold start

Read `TASK.md` first and work the task from your own research and operations.
This file only describes the machine you have been given.

## Identity
- Task / agent player UUID: `$task_uuid`
- Minecraft $v_minecraft, Fabric Loader $v_loader, Fabric API $v_fabric_api, Carpet $v_carpet
- Real operation and full runtime NBT/tick-order reads are expected; nothing is filtered.

## What is already running
Two headless Fabric lab servers, each with its own clean bridge daemon
(bridge source `$bridge_src`, commit `$bridge_commit`). Work in the experiment lab.

### Source lab `$source_lab` - the complete initialized ready state to acquire
- lab dir: `$source_lab_dir`
- bridge JSON API: `127.0.0.1:$source_bridge` (already running, connected)
- server-vantage mod: `127.0.0.1:$source_vantage` (port file `$source_lab_dir\\mc-agent-server\\port.txt`)
- game port: `$source_game`, RCON console: `$source_rcon`
- audit (evaluator) output: `$source_lab_dir\\mc-audit\\`
- keep this lab's world unoperated; it is the state you acquire.

### Experiment lab `$experiment_lab` - blank, yours to build
- lab dir: `$experiment_lab_dir`
- bridge JSON API: `127.0.0.1:$experiment_bridge` (already running, connected)
- server-vantage mod: `127.0.0.1:$experiment_vantage`
- game port: `$experiment_game`, RCON console: `$experiment_rcon`
- audit (evaluator) output: `$experiment_lab_dir\\mc-audit\\`
- the world is empty on purpose. The environment does not restore it for you:
  acquire, load/restore and verify the full runtime state yourself before you
  operate anything.

## Tools
- Python: `$python`
- Java 25: `$java`
- Lab manager: `$lab_server` (`status`, `identity`, `exec` for RCON,
  `start` / `stop` / `provision` when you deliberately change a lab)
- Bridge CLI - always set `PYTHONPATH` to the clean bridge source, because the
  shared venv's editable install points at another checkout:
  ```powershell
  $$env:PYTHONPATH = "$bridge_src"
  & "$python" -m mc_agent_bridge.cli --api-port $source_bridge call capabilities
  & "$python" -m mc_agent_bridge.cli --api-port $source_bridge call status
  ```
  The daemons are already running. Use `call` / `watch` / `mcp`;
  do not start a second `run` daemon on the same API port.
- Map artifact manifest: `$map_manifest`
  (`$minecart_runner fetch --cold` re-downloads and hash-checks the exact map ZIP)
- Runner / fixture CLI: `$minecart_runner` (read-only helpers: `manifest`, `fetch`,
  `snapshot`, `validate`; use `--help` for the rest)
- Interface mod jar (server vantage, already deployed): `$interface_jar`
  (`sha256 $interface_sha`)
- Mod builder (build your own observation/logger mod against a lab):
  `$build_mod`
- Generic kit skill: `skills\\minecraft-toolkit\\SKILL.md` and its reference.
  Repository docs worth reading: `docs\\protocol-snapshot.md`,
  `docs\\lab-server.md`, `docs\\mod-building.md`, `docs\\tools.md`.

## Evaluator instrumentation you must preserve
- Both labs load the independent audit mod `mc-minecart-audit-$v_audit.jar`
  (`sha256 $audit_sha`) as a **required** test mod. It is the evaluator's
  read-only oracle for this run; it is **not** your logger.
- Do not remove it, do not edit `labs\\<lab>\\mc-audit\\config.json`, and do not
  delete or truncate its logs.
- Its control surface is RCON: `mcaudit status`,
  `mcaudit phase init|restore|experiment_start|experiment_end`,
  `mcaudit mark <label>`, `mcaudit flush`, `mcaudit end`.
- Starting or restarting a server resets its phase to `ready`. After you have
  restored and verified the experiment state, and again after every restart,
  open the experiment window before your task actions:
  ```powershell
  & "$python" "$lab_server" exec --name $experiment_lab "mcaudit phase experiment_start"
  ```
- Choose and write your own logger, keep its output separate from
  `labs\\$experiment_lab\\mc-audit\\`, and preserve both.

## Ground rules
- Full local filesystem, shell, build and network access; no capability sandbox.
- No solution, no prior trajectory, no answer is provided. You decide the method.
"""
)


def build_workspace(env: dict[str, Any], run_root: Path, run_id: str, receipts: Receipts) -> dict[str, Any]:
    workspace = run_root / "workspace"
    if workspace.exists():
        raise PrepError(f"{workspace} already exists; attempts are preserved, choose a new --run-id")
    workspace.mkdir(parents=True)
    task_source = require_file(REPO / env["task_file"], "task file")
    shutil.copy2(task_source, workspace / "TASK.md")
    skill_source = require_dir(REPO / env["skill_source"], "toolkit skill source")
    skill_target = workspace / "skills" / "minecraft-toolkit"
    shutil.copytree(skill_source, skill_target)
    versions = derive_versions(
        require_json(REPO / env["map_manifest"], "map manifest"), env["audit_jar"]["path"]
    )
    labs = env["labs"]
    env_text = ENV_TEMPLATE.substitute(
        task_uuid=env["task_uuid"],
        v_minecraft=versions["minecraft"],
        v_loader=versions["fabric_loader"],
        v_fabric_api=versions["fabric_api"],
        v_carpet=versions["carpet"],
        v_audit=versions["minecart_audit"],
        bridge_src=env["bridge"]["src"],
        bridge_commit=env["bridge"]["commit"],
        source_lab=labs["source"]["name"],
        source_lab_dir=str((REPO / "labs" / labs["source"]["name"]).resolve()),
        source_bridge=labs["source"]["bridge_port"],
        source_vantage=labs["source"]["vantage_port"],
        source_game=labs["source"]["server_port"],
        source_rcon=labs["source"]["rcon_port"],
        experiment_lab=labs["experiment"]["name"],
        experiment_lab_dir=str((REPO / "labs" / labs["experiment"]["name"]).resolve()),
        experiment_bridge=labs["experiment"]["bridge_port"],
        experiment_vantage=labs["experiment"]["vantage_port"],
        experiment_game=labs["experiment"]["server_port"],
        experiment_rcon=labs["experiment"]["rcon_port"],
        python=env["python"],
        java=env["java"],
        lab_server=str((REPO / "tools" / "lab_server.py").resolve()),
        map_manifest=str((REPO / env["map_manifest"]).resolve()),
        minecart_runner=str((REPO / "examples/minecart-rom/runner/minecart_rom.py").resolve()),
        interface_jar=env["interface_jar"]["path"],
        interface_sha=env["interface_jar"]["sha256"],
        build_mod=str((REPO / "tools" / "build_mod.py").resolve()),
        audit_sha=env["audit_jar"]["sha256"],
    )
    (workspace / "ENV.md").write_text(env_text, "utf-8", newline="\n")
    files = sorted(path for path in workspace.rglob("*") if path.is_file())
    names = [path.relative_to(workspace).as_posix() for path in files]
    expected = {"TASK.md", "ENV.md", "skills/minecraft-toolkit/SKILL.md",
                "skills/minecraft-toolkit/references/toolkit-operations.md"}
    if set(names) != expected or len(names) != len(expected):
        raise PrepError(f"workspace must contain exactly {sorted(expected)}, found {names}")
    return {
        "workspace": str(workspace),
        "files": [{"path": name, "sha256": sha256_file(workspace / name), "size": (workspace / name).stat().st_size}
                  for name in names],
    }


# --------------------------------------------------------------------------- run trace


def trace_init(env: dict[str, Any], run_trace_dir: Path, workspace: Path, run_id: str, receipts: Receipts) -> dict[str, Any]:
    run_trace_dir.mkdir(parents=True, exist_ok=True)
    argv = [
        env["python"],
        str(REPO / "tools" / "run_trace.py"),
        "init",
        "--run-dir", str(run_trace_dir),
        "--run-id", run_id,
        "--task-file", str(REPO / env["task_file"]),
        "--harness", "pi",
        "--model", env["model"]["model"],
        "--provider", env["model"]["provider"],
        "--harness-version", env["pi"]["version"],
        "--task-player-uuid", env["task_uuid"],
        "--visible-root", str(workspace),
        "--note", "operator preparation before the agent phase; no task-time assistance",
        "--note", f"agent raw session path (planned): {env['sessions']['agent_raw']}",
        "--note", f"operator preparation raw session: {env['sessions']['operator_prep_raw']}",
        "--note", f"run root: {run_trace_dir.parent}",
    ]
    # init from the run root so the visible workspace is recorded without the
    # ignored ``labs`` path component; the repo commit still resolves through
    # git from any directory inside the worktree.
    record = run(argv, cwd=run_trace_dir.parent, timeout=120)
    receipts.log("run-trace-init", record["stdout"] + record["stderr"])
    run_json = require_json(run_trace_dir / "run.json", "run manifest")
    if run_json.get("run_id") != run_id:
        raise PrepError("run_trace init did not produce the run manifest")
    run_json.setdefault("repo", {})
    run_json["repo"].update({
        "root": str(REPO),
        "branch": git_required(REPO, "rev-parse", "--abbrev-ref", "HEAD"),
        "commit": git_required(REPO, "rev-parse", "HEAD"),
    })
    write_json(run_trace_dir / "run.json", run_json)
    visibility = require_json(run_trace_dir / "visibility.json", "run visibility record")
    if visibility.get("file_count") != 4:
        raise PrepError(f"run_trace visibility recorded {visibility.get('file_count')} files, expected 4")
    return {"run_trace_dir": str(run_trace_dir), "run": run_json, "visibility_file_count": visibility["file_count"]}


def trace_record(env: dict[str, Any], run_trace_dir: Path, record: dict[str, Any]) -> None:
    record = {"schema": "mc-agent-coldstart-trajectory/1", "at": utc_now(), **record}
    run(
        [env["python"], str(REPO / "tools" / "run_trace.py"), "record",
         "--run-dir", str(run_trace_dir), "--json-text", json.dumps(record, ensure_ascii=False)],
        timeout=120,
    )


# --------------------------------------------------------------------------- source save


def stage_source(env: dict[str, Any], receipts: Receipts) -> dict[str, Any]:
    source = require_dir(env["source_save"], "source save")
    before = require_json(env["source_before"], "source-before baseline")
    if not isinstance(before, list) or not before:
        raise PrepError(f"source-before baseline {env['source_before']} holds no file records")
    problems: list[dict[str, Any]] = []
    for entry in before:
        relative = entry["path"].lstrip("\\").replace("\\", "/")
        path = source / relative
        if not path.is_file():
            problems.append({"path": relative, "problem": "missing"})
            continue
        actual = sha256_file(path)
        if actual.upper() != entry["sha256"].upper():
            problems.append({"path": relative, "problem": "sha256 changed",
                             "expected": entry["sha256"].upper(), "actual": actual.upper()})
        if path.stat().st_size != entry["length"]:
            problems.append({"path": relative, "problem": "size changed",
                             "expected": entry["length"], "actual": path.stat().st_size})
    if problems:
        raise PrepError(f"source save does not match source-before.json: {json.dumps(problems, ensure_ascii=False)}")
    result = run_json([
        env["python"], str(REPO / "tools" / "stage1_gate.py"), "hash-tree", str(source), "--json",
    ], timeout=900)[0]
    if result.get("tree_sha256") != env["source_tree_sha256"]:
        raise PrepError(
            f"source tree hash {result.get('tree_sha256')} != expected {env['source_tree_sha256']}"
        )
    if result.get("files") != env["source_files"]:
        raise PrepError(f"source tree has {result.get('files')} files, expected {env['source_files']}")
    return receipts.write("source-save", {
        "path": str(source),
        "baseline": str(Path(env["source_before"])),
        "entries_checked": len(before),
        "tree_sha256": result["tree_sha256"],
        "files": result["files"],
        "bytes": result.get("bytes"),
        "exclusions": result.get("exclusions"),
        "problems": [],
    })


# --------------------------------------------------------------------------- preflight


def stage_preflight(env: dict[str, Any], run_trace_dir: Path, operator: Path, wrapper: Path, receipts: Receipts) -> dict[str, Any]:
    out = operator / "preflight"
    if out.exists():
        raise PrepError(f"{out} already exists; attempts are preserved, choose a new --run-id")
    require_file(wrapper, "bridge wrapper")
    labs = env["labs"]
    argv = [
        env["python"], str(REPO / "tools" / "harness_preflight.py"), "run",
        "--config", str(REPO / "examples/coldstart/harness-pi.json"),
        "--out", str(out),
        "--run-dir", str(run_trace_dir),
        "--set", f"command={env['pi']['command']}",
        "--set", f"model={env['model']['model']}",
        "--set", f"provider={env['model']['provider']}",
        "--set", f"build.java={env['java']}",
        "--set", f"bridge.command={wrapper}",
        "--set", f"lab.name={labs['source']['name']}",
        "--set", f"lab.ports.game={labs['source']['server_port']}",
        "--set", f"lab.ports.rcon={labs['source']['rcon_port']}",
        "--set", "timeouts.lab_start=1200",
    ]
    record = run(argv, cwd=REPO, timeout=2400, check=False)
    receipts.log("preflight", record["stdout"] + record["stderr"])
    report = read_json(out / "preflight.json")
    if not report:
        raise PrepError("preflight produced no report")
    statuses = {check["id"]: check["status"] for check in report.get("checks", [])}
    required = {
        "harness_identity", "terminal", "filesystem", "ports", "source_fetch",
        "build_install", "bridge_cli", "bridge_smoke", "lab_management",
    }
    bad = {key: statuses.get(key) for key in sorted(required) if statuses.get(key) != "PASS"}
    if bad:
        raise PrepError(f"preflight checks did not PASS: {bad}")
    if statuses.get("bridge_mcp") not in ("PASS", "SKIP"):
        raise PrepError(f"bridge_mcp ended {statuses.get('bridge_mcp')}")
    if report.get("overall") != "PASS":
        raise PrepError("preflight overall is not PASS")
    labs_check = next((check for check in report["checks"] if check["id"] == "lab_management"), {})
    if labs_check.get("versions", {}).get("ports_patched") is not True:
        raise PrepError("preflight did not pin the lab to the reserved ports")
    return receipts.write("preflight-summary", {
        "report": str(out / "preflight.json"),
        "report_sha256": sha256_file(out / "preflight.json"),
        "markdown": str(out / "preflight.md"),
        "overall": report["overall"],
        "checks": statuses,
        "lab_versions": labs_check.get("versions"),
        "environment": report.get("environment") or {},
        "config": report.get("config") or {},
    })


# --------------------------------------------------------------------------- labs / audit


def lab_exec(env: dict[str, Any], lab: str, command: str, receipts: Receipts | None = None, name: str | None = None) -> str:
    result = run([
        env["python"], str(REPO / "tools" / "lab_server.py"), "exec", "--name", lab, command,
    ], timeout=180)
    if receipts is not None and name:
        receipts.log(name, result["stdout"] + result["stderr"])
    return result["stdout"]


def write_audit_config(env: dict[str, Any], run_id: str, lab_key: str, provenance_reference: str) -> dict[str, Any]:
    lab = env["labs"][lab_key]
    audit = env["audit"]
    provenance = {
        "kind": audit[f"provenance_{lab_key}"]["kind"],
        "reference": provenance_reference,
        # the fixture init names its interface snapshot ready-<lab>; the ready
        # stage asserts the live name before this provenance can be trusted
        "snapshotId": f"ready-{env['labs']['source']['name']}" if lab_key == "source" else "",
        "snapshotHash": "",
    }
    config = {
        "runId": run_id,
        "instanceId": lab["instance_id"],
        "dimension": "minecraft:overworld",
        "provenance": provenance,
        "outputDir": "mc-audit",
        "maxBytes": 67108864,
        "sampleIntervalTicks": audit["sample_interval_ticks"],
        "correlationWindowTicks": audit["correlation_window_ticks"],
        "cartTypes": audit["cart_types"],
        "agentUuids": [env["task_uuid"]],
        "inputRegions": [audit["input_region"]],
        "stackRegion": audit["stack_region"],
        "outputRegion": audit["output_region"],
    }
    directory = REPO / "labs" / lab["name"] / "mc-audit"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "config.json", config)
    return {"path": str(directory / "config.json"), "sha256": sha256_file(directory / "config.json"), "config": config}


def read_audit_status(lab: str) -> dict[str, Any]:
    directory = REPO / "labs" / lab / "mc-audit"
    latest = read_json(directory / "latest.json") or {}

    def resolve(value: str | None) -> Path | None:
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else (directory / path).resolve()

    status_path = resolve(latest.get("status"))
    log_path = resolve(latest.get("log"))
    status = read_json(status_path) if status_path and status_path.is_file() else None
    return {
        "latest": latest,
        "status": status,
        "status_sha256": sha256_file(status_path) if status_path and status_path.is_file() else None,
        "log": str(log_path) if log_path else None,
        "log_sha256": sha256_file(log_path) if log_path and log_path.is_file() else None,
    }


def provision_test_mod(env: dict[str, Any], lab_key: str, receipts: Receipts) -> dict[str, Any]:
    lab = env["labs"][lab_key]
    result = run([
        env["python"], str(REPO / "tools" / "lab_server.py"), "provision",
        "--name", lab["name"],
        "--test-mod", env["audit_jar"]["path"],
        "--java", env["java"],
        "--jdk", env["jdk"],
    ], timeout=900)
    receipts.log(f"provision-testmod-{lab['name']}", result["stdout"] + result["stderr"])
    return {"command": result["command"], "exit_code": result["exit_code"], "stdout_tail": tail(result["stdout"], 800)}


def start_lab(env: dict[str, Any], lab_key: str, receipts: Receipts) -> dict[str, Any]:
    lab = env["labs"][lab_key]
    result = run([
        env["python"], str(REPO / "tools" / "lab_server.py"), "start",
        "--name", lab["name"], "--wait", "600",
    ], timeout=900)
    receipts.log(f"start-{lab['name']}", result["stdout"] + result["stderr"])
    return {"command": result["command"], "exit_code": result["exit_code"], "stdout_tail": tail(result["stdout"], 800)}


def lab_identity(env: dict[str, Any], lab_key: str, receipts: Receipts) -> dict[str, Any]:
    lab = env["labs"][lab_key]
    payload, result = run_json([
        env["python"], str(REPO / "tools" / "lab_server.py"), "identity",
        "--name", lab["name"], "--json",
    ], timeout=120)
    receipts.log(f"identity-{lab['name']}", result["stdout"] + result["stderr"])
    if not payload.get("running"):
        raise PrepError(f"lab {lab['name']} is not running")
    if payload.get("deploymentProblems"):
        raise PrepError(f"lab {lab['name']} has deployment problems: {payload['deploymentProblems']}")
    if any(mod.get("drift") for mod in payload.get("mods", [])):
        raise PrepError(f"lab {lab['name']} has mod drift")
    audit_name = Path(env["audit_jar"]["path"]).name
    if audit_name not in (payload.get("requiredMods") or []):
        raise PrepError(f"lab {lab['name']} does not require the audit mod {audit_name}")
    for pinned, what in ((env["audit_jar"], "audit mod"), (env["interface_jar"], "interface mod")):
        name = Path(pinned["path"]).name
        if not any(mod.get("name") == name and mod.get("sha256") == pinned["sha256"]
                   for mod in payload.get("mods", [])):
            raise PrepError(f"lab {lab['name']} {what} {name} bytes do not match the accepted jar")
    return payload


# --------------------------------------------------------------------------- challenge


def challenge_has_duplicate_items(program: dict[str, Any]) -> bool:
    seen: dict[str, int] = {}
    for cart in program.get("carts", []):
        for item in cart.get("items", []):
            seen[item["id"]] = seen.get(item["id"], 0) + 1
    return any(count > 1 for count in seen.values())


def generate_challenge(env: dict[str, Any], sealed: Path, receipts: Receipts) -> dict[str, Any]:
    sealed.parent.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []
    challenge = env["challenge"]
    for attempt in range(1, 9):
        sealed.unlink(missing_ok=True)
        summary, record = run_json([
            env["python"], str(REPO / "examples/minecart-rom/runner/minecart_rom.py"), "challenge",
            "--carts", str(challenge["carts"]),
            "--max-slots", str(challenge["max_slots"]),
            "--max-count", str(challenge["max_count"]),
            "--out", str(sealed),
        ], timeout=120)
        program = read_json(sealed)
        attempts.append({
            "attempt": attempt,
            "seed": summary["seed"],
            "program_sha256": summary["program_sha256"],
            "duplicate_item_ids": challenge_has_duplicate_items(program),
        })
        if not challenge["require_duplicate_item_ids"] or attempts[-1]["duplicate_item_ids"]:
            break
    else:
        raise PrepError("could not generate a challenge with duplicate item ids")
    if summary["carts"] != challenge["carts"]:
        raise PrepError(f"challenge has {summary['carts']} carts, expected {challenge['carts']}")
    return receipts.write("challenge", {
        "path": str(sealed),
        "sha256": sha256_file(sealed),
        "seed": summary["seed"],
        "carts": summary["carts"],
        "inventory_total": summary["inventory_total"],
        "program_sha256": summary["program_sha256"],
        "attempts": attempts,
        "empty_cart_supported": False,
        "limit_note": "the accepted challenge generator emits distinct cart signatures and at least one slot per cart; duplicate item ids are present and checked",
    })


# --------------------------------------------------------------------------- fixture stage


def uuid_from_ints(value: str) -> str:
    plain = value.replace("[I;", "").replace("]", "").replace(" ", "")
    parts = plain.split(",")
    if len(parts) != 4:
        raise PrepError(f"cannot read UUID int array {value!r}")
    raw = b"".join(int(part).to_bytes(4, "big", signed=True) for part in parts)
    hexed = raw.hex()
    return f"{hexed[0:8]}-{hexed[8:12]}-{hexed[12:16]}-{hexed[16:20]}-{hexed[20:32]}"


def stage_fixture(env: dict[str, Any], run_id: str, run_root: Path, operator: Path, receipts: Receipts) -> dict[str, Any]:
    labs = env["labs"]
    source = labs["source"]
    runner = require_file(REPO / "examples/minecart-rom/runner/minecart_rom.py", "minecart runner")
    require_hashed_file(env["interface_jar"]["path"], env["interface_jar"]["sha256"], "interface jar")
    require_hashed_file(env["audit_jar"]["path"], env["audit_jar"]["sha256"], "audit jar")
    source_lab_dir = REPO / "labs" / source["name"]
    manifest = require_json(REPO / env["map_manifest"], "map manifest")

    # 1. remote cold fetch of the pinned map ZIP
    fetch, fetch_record = run_json([
        env["python"], str(runner), "fetch", "--cold", "--timeout", "300",
        "--records", str(operator / "receipts"),
    ], timeout=900)
    receipts.log("fetch", fetch_record["stdout"] + fetch_record["stderr"])
    cache = fetch["cache"]
    if cache.get("sha256") != manifest["artifact"]["sha256"]:
        raise PrepError(f"cold fetch sha256 {cache.get('sha256')} != manifest {manifest['artifact']['sha256']}")
    if cache.get("bytes") != manifest["artifact"]["size"]:
        raise PrepError(f"cold fetch size {cache.get('bytes')} != manifest {manifest['artifact']['size']}")
    if cache.get("cache_hit") is not False:
        raise PrepError(f"fetch was not cold (cache_hit={cache.get('cache_hit')!r})")
    if fetch.get("extract", {}).get("world_sha256") != manifest["world"]["sha256"]:
        raise PrepError("unpacked world does not match the manifest world hash")

    # 2. the generic preflight lab still carries its own floating Modrinth
    #    mods; stop it, prune anything outside the pinned set, and only then
    #    install the required audit mod, so a floating jar can never coexist
    #    with the pinned bytes after the import
    pruned = prune_stray_mods(env, "source", receipts)
    testmod = provision_test_mod(env, "source", receipts)

    # 3. reset the preflight lab into the fixture world (stops it first); this
    #    re-provision keeps the required audit mod and labels the world
    #    "copied save" with its source
    import_result, import_record = run_json([
        env["python"], str(runner), "import", "--lab", source["name"], "--reset",
        "--java", env["java"], "--memory", "3G",
        "--rcon-port", str(source["rcon_port"]),
        "--server-port", str(source["server_port"]),
        "--vantage-port", str(source["vantage_port"]),
        "--bridge-port", str(source["bridge_port"]),
        "--interface-mod", env["interface_jar"]["path"],
        "--records", str(operator / "receipts"),
    ], timeout=1200)
    receipts.log("import-world", import_record["stdout"] + import_record["stderr"])
    if import_result.get("manifest_version") != manifest["version"]:
        raise PrepError("imported world does not carry the pinned manifest version")
    lab_state = require_json(REPO / "labs" / source["name"] / "lab.json", "source lab state")
    if lab_state.get("world") != "copied save" or not lab_state.get("worldSource"):
        raise PrepError(
            f"source lab world metadata is {lab_state.get('world')!r}/{lab_state.get('worldSource')!r},"
            " expected a copied save with a source"
        )
    mods_check = assert_lab_mods_exact(env, "source", manifest)

    # 4. audit config before the first fixture start
    audit_config = write_audit_config(env, run_id, "source", str((source_lab_dir / "world").resolve()))

    # 5. start, open the init phase, initialize the sealed challenge
    start = start_lab(env, "source", receipts)
    identity_before = lab_identity(env, "source", receipts)
    if identity_before["serverVantagePort"] != source["vantage_port"]:
        raise PrepError(f"source lab vantage port {identity_before['serverVantagePort']} != {source['vantage_port']}")
    lab_exec(env, source["name"], "mcaudit status", receipts, "mcaudit-status-before-init")
    lab_exec(env, source["name"], "mcaudit phase init", receipts, "mcaudit-phase-init")

    challenge = generate_challenge(env, operator / "sealed" / "challenge.json", receipts)
    sealed = Path(challenge["path"])

    init_summary, init_record = run_json([
        env["python"], str(runner), "init", "--lab", source["name"],
        "--program", str(sealed),
        "--records", str(operator / "fixture"),
    ], timeout=1800)
    receipts.log("fixture-init", init_record["stdout"] + init_record["stderr"])

    validate_summary, validate_record = run_json([
        env["python"], str(runner), "validate", "--lab", source["name"],
        "--ready", str(operator / "fixture" / "ready-snapshot.json"),
    ], timeout=900)
    receipts.log("fixture-validate", validate_record["stdout"] + validate_record["stderr"])
    if not validate_summary.get("ok"):
        raise PrepError(f"fixture validate did not report ok: {validate_summary.get('problems')}")
    if not init_summary.get("validation", {}).get("ok"):
        raise PrepError("fixture init validation did not report ok")

    ready_path = operator / "fixture" / "ready-snapshot.json"
    ready = require_json(ready_path, "ready snapshot")
    user = ready.get("user") or {}
    user_uuid = uuid_from_ints(user.get("UUID", ""))
    if user_uuid != env["task_uuid"]:
        raise PrepError(f"fixture user UUID {user_uuid} != task UUID {env['task_uuid']}")
    if ready.get("world_day_tick") != 6000:
        raise PrepError(f"ready day tick {ready.get('world_day_tick')} != 6000")
    if not ready.get("tick_frozen"):
        raise PrepError("ready fixture is not frozen")
    if (ready.get("machine") or {}).get("note") != 20:
        raise PrepError(f"ready note is {(ready.get('machine') or {}).get('note')}, expected 20")
    if ready.get("cart_count") != env["challenge"]["carts"]:
        raise PrepError(f"ready cart count {ready.get('cart_count')} != {env['challenge']['carts']}")
    seat = ready.get("seat") or []
    if len(seat) != 1 or not user.get("vehicle_uuid") or user.get("vehicle_uuid") != seat[0].get("uuid"):
        raise PrepError("the fake player is not mounted on the single invisible hover seat")
    snapshot_name = (ready.get("interface") or {}).get("name")
    expected_snapshot = f"ready-{source['name']}"
    if snapshot_name != expected_snapshot:
        # the audit provenance must name the interface snapshot that really exists
        raise PrepError(
            f"ready interface snapshot name {snapshot_name!r} != audit provenance {expected_snapshot!r}"
        )
    lab_exec(env, source["name"], "mcaudit flush", receipts, "mcaudit-flush")
    audit = read_audit_status(source["name"])
    phase = ((audit.get("status") or {}).get("phase"))
    if phase != "init":
        raise PrepError(f"source audit phase is {phase!r}, expected 'init'")
    return receipts.write("fixture-ready", {
        "lab": source["name"],
        "instance_id": source["instance_id"],
        "world_dir": str((source_lab_dir / "world").resolve()),
        "ready_snapshot": str(ready_path),
        "ready_sha256": sha256_file(ready_path),
        "challenge": challenge,
        "cart_count": ready["cart_count"],
        "spawn_order": ready.get("spawn_order"),
        "tick_order_hash": ready.get("tick_order_hash"),
        "normalized_hash": ready.get("normalized_hash"),
        "world_day_tick": ready.get("world_day_tick"),
        "tick_frozen": ready.get("tick_frozen"),
        "note": (ready.get("machine") or {}).get("note"),
        "user": {"name": user.get("name"), "uuid": user_uuid, "vehicle_uuid": user.get("vehicle_uuid"),
                 "seat_uuid": seat[0].get("uuid"), "seat_pos": seat[0].get("pos")},
        "interface": {
            "port": source["vantage_port"],
            "name": snapshot_name,
            "order_hash": (ready.get("interface") or {}).get("order_hash"),
            "cross_check_ok": (ready.get("interface") or {}).get("cross_check_ok"),
            "tick": (ready.get("interface") or {}).get("tick"),
        },
        "mods": mods_check,
        "pruned_stray_mods": pruned,
        "validate": validate_summary,
        "audit_config": {"path": audit_config["path"], "sha256": audit_config["sha256"]},
        "audit_status": {"phase": phase, "log": audit["log"], "log_sha256": audit["log_sha256"]},
        "fetch": {"url": cache.get("url"), "sha256": cache.get("sha256"), "bytes": cache.get("bytes"),
                  "cache_hit": cache.get("cache_hit"), "http_status": cache.get("http_status")},
        "import": {"world_sha256": import_result.get("world_sha256"), "world_files": import_result.get("world_files"),
                   "mod_pins": import_result.get("mod_pins")},
        "test_mod_provision": testmod,
        "start": start,
    })


def stage_experiment(env: dict[str, Any], run_id: str, operator: Path, receipts: Receipts) -> dict[str, Any]:
    labs = env["labs"]
    source, experiment = labs["source"], labs["experiment"]
    source_lab_dir = REPO / "labs" / source["name"]
    experiment_dir = REPO / "labs" / experiment["name"]

    manifest = require_json(REPO / env["map_manifest"], "map manifest")
    require_hashed_file(env["interface_jar"]["path"], env["interface_jar"]["sha256"], "interface jar")
    require_hashed_file(env["audit_jar"]["path"], env["audit_jar"]["sha256"], "audit jar")
    pinned: list[str] = []
    for mod in manifest.get("mods") or []:
        if mod.get("required") and mod.get("filename"):
            path = require_hashed_file(
                LABS_ROOT / "_cache" / "mods" / mod["filename"], mod["sha256"], f"pinned mod {mod['filename']}"
            )
            pinned += ["--mod-jar", str(path)]
    provision = run([
        env["python"], str(REPO / "tools" / "lab_server.py"), "provision",
        "--name", experiment["name"], "--void",
        "--java", env["java"], "--jdk", env["jdk"], "--memory", "3G",
        "--server-port", str(experiment["server_port"]),
        "--rcon-port", str(experiment["rcon_port"]),
        "--vantage-port", str(experiment["vantage_port"]),
        "--bridge-port", str(experiment["bridge_port"]),
        *pinned,
        "--mod-jar", env["interface_jar"]["path"],
        "--test-mod", env["audit_jar"]["path"],
    ], timeout=1200)
    receipts.log("provision-experiment", provision["stdout"] + provision["stderr"])
    mods_check = assert_lab_mods_exact(env, "experiment", manifest)
    lab_state = require_json(experiment_dir / "lab.json", "experiment lab state")
    audit_config = write_audit_config(env, run_id, "experiment", str((source_lab_dir / "world").resolve()))

    # reuse the downloaded/processed server runtime from the source lab
    fabric_source = source_lab_dir / ".fabric"
    fabric_target = experiment_dir / ".fabric"
    if fabric_source.is_dir() and not fabric_target.exists():
        shutil.copytree(fabric_source, fabric_target, symlinks=True)
    server_jar = fabric_target / "server" / "26.2-server.jar"
    if not server_jar.is_file():
        raise PrepError(f"experiment lab has no reusable server runtime at {server_jar}")

    start = start_lab(env, "experiment", receipts)
    identity = lab_identity(env, "experiment", receipts)
    if identity["serverVantagePort"] != experiment["vantage_port"]:
        raise PrepError(f"experiment vantage port {identity['serverVantagePort']} != {experiment['vantage_port']}")
    if identity["worldDir"] != str((experiment_dir / "world").resolve()):
        raise PrepError(f"experiment world dir {identity['worldDir']} is not {experiment_dir / 'world'}")
    lab_exec(env, experiment["name"], "mcaudit flush", receipts, "mcaudit-flush-experiment")
    audit = read_audit_status(experiment["name"])
    phase = ((audit.get("status") or {}).get("phase"))
    if phase != "ready":
        raise PrepError(f"experiment audit phase is {phase!r}, expected 'ready' before the agent starts")
    blank = assert_blank_lab(lab_state, audit.get("status"), experiment["name"])
    return receipts.write("experiment-blank", {
        "lab": experiment["name"],
        "instance_id": experiment["instance_id"],
        "world_dir": identity["worldDir"],
        "world_kind": lab_state.get("world"),
        "blank": blank,
        "mods": mods_check,
        "identity": identity,
        "audit_config": {"path": audit_config["path"], "sha256": audit_config["sha256"]},
        "audit_status": {"phase": phase, "log": audit["log"], "log_sha256": audit["log_sha256"]},
        "fabric_reused_from": str(fabric_source),
        "server_jar_sha256": sha256_file(server_jar),
        "provision": {"command": provision["command"], "exit_code": provision["exit_code"],
                      "stdout_tail": tail(provision["stdout"], 800)},
        "start": start,
    })


# --------------------------------------------------------------------------- clean / bridges


def ensure_lab_stopped(env: dict[str, Any], lab_name: str, lab_dir: Path, receipts: Receipts) -> dict[str, Any]:
    """Stop a lab and prove the server process is gone before anyone removes it."""
    if is_reparse_point(lab_dir):
        raise PrepError(f"refusing to stop/remove lab {lab_name} {lab_dir}: it is a symlink/junction")
    sys.path.insert(0, str(REPO / "tools"))
    import lab_server

    running, pid = lab_server.lab_running(lab_dir)
    if not running:
        _assert_lab_ports_free(lab_dir, lab_name)
        return {"lab": lab_name, "action": "already-stopped"}
    stop = run([
        env["python"], str(REPO / "tools" / "lab_server.py"), "stop",
        "--name", lab_name, "--timeout", "120",
    ], timeout=300, check=False)
    receipts.log(f"stop-{lab_name}", stop["stdout"] + stop["stderr"])
    running, pid = lab_server.lab_running(lab_dir)
    if running:
        raise PrepError(
            f"lab {lab_name} is still running (pid {pid}); refusing to continue or remove {lab_dir}."
            f" stop output:\n{tail(stop['stdout'])}"
        )
    if stop["exit_code"] != 0:
        say(f"warning: stop {lab_name} exited {stop['exit_code']} but the process is gone")
    _assert_lab_ports_free(lab_dir, lab_name)
    return {"lab": lab_name, "action": "stopped", "stop_exit_code": stop["exit_code"]}


def _assert_lab_ports_free(lab_dir: Path, lab_name: str) -> None:
    """A stopped lab must not still own its game or console port."""
    state = read_json(lab_dir / "lab.json") or {}
    for field, label in (("serverPort", "game"), ("rconPort", "rcon")):
        port = int(state.get(field) or 0)
        owner = port_owner(port) if port else None
        if owner is not None:
            raise PrepError(
                f"lab {lab_name} {label} port {port} is still owned by pid {owner}; refusing to continue"
            )


def prune_stray_mods(env: dict[str, Any], lab_key: str, receipts: Receipts) -> dict[str, Any]:
    """Remove jars the generic preflight downloaded that the pinned set does not name."""
    lab = env["labs"][lab_key]
    lab_dir = LABS_ROOT / lab["name"]
    if not (lab_dir / "lab.json").is_file():
        return receipts.write("prune-stray-mods", {
            "removed": [], "kept": [], "note": "lab not provisioned yet",
        })
    stopped = ensure_lab_stopped(env, lab["name"], lab_dir, receipts)
    manifest = require_json(REPO / env["map_manifest"], "map manifest")
    mods_dir = lab_dir / "mods"
    present = sorted(path.name for path in mods_dir.glob("*.jar")) if mods_dir.is_dir() else []
    prune = mods_to_prune(present, allowed_mod_names(env, manifest))
    removed: list[dict[str, Any]] = []
    for name in prune:
        path = mods_dir / name
        if is_reparse_point(path) or mods_dir.resolve() not in path.resolve().parents:
            raise PrepError(f"refusing to remove stray mod path {path}: outside {mods_dir} or a reparse point")
        digest = sha256_file(path)
        path.unlink()
        removed.append({"name": name, "sha256": digest})
    return receipts.write("prune-stray-mods", {
        "removed": removed,
        "kept": [name for name in present if name not in prune],
        "stopped": stopped,
    })


def assert_lab_mods_exact(env: dict[str, Any], lab_key: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """After provisioning, only the pinned allowed jars may sit in ``mods/``."""
    lab = env["labs"][lab_key]
    mods_dir = LABS_ROOT / lab["name"] / "mods"
    present = sorted(path.name for path in mods_dir.glob("*.jar")) if mods_dir.is_dir() else []
    allowed = sorted(allowed_mod_names(env, manifest))
    if present != allowed:
        raise PrepError(f"lab {lab['name']} mods {present} != pinned allowed set {allowed}")
    return {"mods": present, "allowed": allowed}


def terminate_pid(pid: int) -> bool:
    sys.path.insert(0, str(REPO / "tools"))
    import lab_server

    if pid <= 0 or not lab_server.process_alive(pid):
        return False
    lab_server.terminate(pid)
    return True


def port_owner(port: int) -> int | None:
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60
        )
    except OSError:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and f":{port}" in parts[1] and parts[3].upper() == "LISTENING":
            try:
                return int(parts[4])
            except ValueError:
                continue
    return None


def stage_clean(env: dict[str, Any], receipts: Receipts) -> dict[str, Any]:
    killed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    recorded = read_json(LABS_ROOT / "_bridges.json") or {}
    for key, info in recorded.items():
        port = int(info.get("api_port") or 0)
        owner = port_owner(port) if port else None
        # The venv python.exe launcher starts the real interpreter as a child,
        # so the spawned pid and the socket owner differ; both are recorded with
        # a start time and a pid is killed only when that identity still matches.
        plan = bridge_kill_plan(info, owner)
        for pid in plan["targets"]:
            if terminate_pid(pid):
                killed.append({"key": key, "pid": pid, "api_port": port})
        skipped.extend({"key": key, **entry} for entry in plan["skipped"])
    # stop and prove stopped before removing anything; a fresh authoritative run
    # must not inherit a previous lab's world, mods or audit config. The shared
    # download cache stays and is reused.
    labs = [
        ensure_lab_stopped(env, env["labs"][key]["name"], LABS_ROOT / env["labs"][key]["name"], receipts)
        for key in ("source", "experiment")
    ]
    removed: list[str] = []
    for key in ("source", "experiment"):
        lab_dir = LABS_ROOT / env["labs"][key]["name"]
        if lab_dir.exists():
            remove_tree_safely(lab_dir, root=LABS_ROOT, what=f"lab {env['labs'][key]['name']}")
            removed.append(env["labs"][key]["name"])
    return receipts.write("clean", {
        "killed_bridges": killed,
        "skipped_bridge_pids": skipped,
        "labs": labs,
        "removed_labs": removed,
    })


def start_bridge(env: dict[str, Any], lab_key: str, run_root: Path, receipts: Receipts) -> dict[str, Any]:
    sys.path.insert(0, str(REPO / "tools"))
    import lab_server

    lab = env["labs"][lab_key]
    lab_dir = REPO / "labs" / lab["name"]
    log = run_root / "bridges" / f"bridge-{lab['name']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        env["python"], "-u", "-m", "mc_agent_bridge.cli", "run",
        "--api-port", str(lab["bridge_port"]),
        "--server-dir", str(lab_dir),
    ]
    previous = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = env["bridge"]["src"]
    try:
        pid = lab_server.spawn_detached(argv, cwd=REPO / "labs", log=log)
    finally:
        if previous is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = previous
    status = None
    deadline = time.time() + 90
    while time.time() < deadline:
        if status is not None:
            break
        try:
            payload, _record = run_json([
                env["python"], "-m", "mc_agent_bridge.cli",
                "--api-port", str(lab["bridge_port"]), "call", "status",
            ], timeout=30, check=False, env={"PYTHONPATH": env["bridge"]["src"]})
            if payload.get("connected"):
                status = payload
                break
        except PrepError:
            pass
        time.sleep(2)
    if status is None:
        raise PrepError(f"bridge for {lab['name']} did not report connected within 90s; see {log}")
    listen_pid = port_owner(lab["bridge_port"])
    spawn_identity = pid_identity(pid) or {}
    listen_identity = pid_identity(listen_pid) if listen_pid else {}
    return {
        "lab": lab["name"],
        "pid": pid,
        "pid_created": spawn_identity.get("created"),
        "pid_exe": spawn_identity.get("exe"),
        "listen_pid": listen_pid,
        "listen_created": listen_identity.get("created"),
        "listen_exe": listen_identity.get("exe"),
        "api_port": lab["bridge_port"],
        "server_dir": str(lab_dir),
        "log": str(log),
        "status": status,
        "source": env["bridge"],
    }


def stage_bridges(env: dict[str, Any], run_root: Path, receipts: Receipts) -> dict[str, Any]:
    bridges = {
        "source": start_bridge(env, "source", run_root, receipts),
        "experiment": start_bridge(env, "experiment", run_root, receipts),
    }
    write_json(LABS_ROOT / "_bridges.json", {
        key: {
            "pid": value["pid"], "pid_created": value.get("pid_created"), "pid_exe": value.get("pid_exe"),
            "listen_pid": value.get("listen_pid"), "listen_created": value.get("listen_created"),
            "listen_exe": value.get("listen_exe"), "api_port": value["api_port"],
        }
        for key, value in bridges.items()
    })
    return receipts.write("bridges", {"bridges": bridges})


# --------------------------------------------------------------------------- finalize


def stage_finalize(
    env: dict[str, Any],
    run_id: str,
    run_root: Path,
    run_trace_dir: Path,
    operator: Path,
    receipts: Receipts,
    receipts_index: dict[str, dict[str, Any]],
    launch_command: str,
) -> None:
    context = receipts_index.get("workspace") or {}
    fixture = receipts_index.get("fixture-ready") or {}
    experiment = receipts_index.get("experiment-blank") or {}
    bridges = receipts_index.get("bridges") or {}
    preflight = receipts_index.get("preflight-summary") or {}
    source_save = receipts_index.get("source-save") or {}
    challenge = fixture.get("challenge") or receipts_index.get("challenge") or {}

    # evidence declarations relative to the run-trace dir
    evidence = require_json(run_trace_dir / "evidence.json", "run evidence template")
    evidence["preflight"] = {
        "path": "../operator/preflight/preflight.json",
        "sha256": preflight.get("report_sha256"),
    }
    evidence["fixture"] = {
        "path": "../operator/fixture/ready-snapshot.json",
        "sha256": fixture.get("ready_sha256"),
    }
    write_json(run_trace_dir / "evidence.json", evidence)

    # canonical marks in the run trajectory
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "operator_prep", "actor": "operator", "phase": "prepare",
        "data": {
            "run_root": str(run_root),
            "operator_prep_session": env["sessions"]["operator_prep_raw"],
            "model": env["model"],
            "harness": {"name": "pi", **env["pi"]},
            "note": "all preparation was performed by the operator before the agent phase; no task-time assistance is provided",
        },
    })
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "workspace_ready", "actor": "operator", "phase": "prepare",
        "data": {"workspace": context.get("workspace"), "files": context.get("files")},
    })
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "challenge_sealed", "actor": "operator", "phase": "prepare",
        "data": {
            "path": challenge.get("path"),
            "sha256": challenge.get("sha256"),
            "seed": challenge.get("seed"),
            "carts": challenge.get("carts"),
            "program_sha256": challenge.get("program_sha256"),
            "empty_cart_supported": False,
        },
    })
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "fixture_ready", "actor": "operator", "phase": "prepare",
        "data": {
            "lab": fixture.get("lab"),
            "instance_id": fixture.get("instance_id"),
            "ready_snapshot": fixture.get("ready_snapshot"),
            "ready_sha256": fixture.get("ready_sha256"),
            "cart_count": fixture.get("cart_count"),
            "tick_order_hash": fixture.get("tick_order_hash"),
            "world_day_tick": fixture.get("world_day_tick"),
            "tick_frozen": fixture.get("tick_frozen"),
            "note": fixture.get("note"),
            "user": fixture.get("user"),
        },
    })
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "experiment_blank", "actor": "operator", "phase": "prepare",
        "data": {"lab": experiment.get("lab"), "instance_id": experiment.get("instance_id"),
                 "world_dir": experiment.get("world_dir")},
    })
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "bridges_ready", "actor": "operator", "phase": "prepare",
        "data": {key: {"pid": value.get("pid"), "listen_pid": value.get("listen_pid"),
                       "api_port": value.get("api_port"),
                       "log": value.get("log"), "source": value.get("source")}
                 for key, value in (bridges.get("bridges") or {}).items()},
    })
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "agent_session_plan", "actor": "operator", "phase": "prepare",
        "data": {
            "raw_session": env["sessions"]["agent_raw"],
            "harness": {"name": "pi", "command": env["pi"]["command"], "version": env["pi"]["version"]},
            "model": env["model"],
            "launch_command": launch_command,
            "note": "the coordinator launches this after inspecting the prepared environment; the operator does not launch it",
        },
    })

    # keep an operator snapshot of the live preparation session (the raw file stays authoritative)
    prep_session = require_file(env["sessions"]["operator_prep_raw"], "operator preparation raw session")
    target = operator / "prep-session.snapshot.jsonl"
    shutil.copy2(prep_session, target)
    prep_snapshot = {"path": str(target), "sha256": sha256_file(target),
                     "raw": str(prep_session), "note": "snapshot at preparation end; raw file keeps growing"}
    trace_record(env, run_trace_dir, {
        "record": "mark", "name": "prep_session_snapshot", "actor": "operator", "phase": "prepare",
        "data": prep_snapshot,
    })

    validation, validation_record = run_json([
        env["python"], str(REPO / "tools" / "run_trace.py"), "validate",
        "--run-dir", str(run_trace_dir), "--json",
    ], timeout=120, check=False)
    receipts.log("run-trace-validate", validation_record["stdout"] + validation_record["stderr"])
    if not validation.get("valid"):
        raise PrepError(f"run_trace validate failed: {validation.get('problems')}")

    # final live state
    live: dict[str, Any] = {}
    for lab_key in ("source", "experiment"):
        lab = env["labs"][lab_key]
        identity = lab_identity(env, lab_key, receipts)
        audit = read_audit_status(lab["name"])
        live[lab_key] = {
            "identity": identity,
            "audit_phase": (audit.get("status") or {}).get("phase"),
            "audit_log": audit.get("log"),
            "audit_log_sha256": audit.get("log_sha256"),
        }
    receipts.write("live-state", live)

    git_state = {
        "coldstart": git_state_entry(REPO),
        "bridge": {**git_state_entry(Path(env["bridge"]["worktree"])),
                   "src_tree": git(Path(env["bridge"]["worktree"]), "rev-parse", "HEAD:src") or None},
        "agent_loop": {**git_state_entry(Path(env["agent_loop"]["root"])),
                       "src_tree": git(Path(env["agent_loop"]["root"]), "rev-parse", "HEAD:src") or None},
    }

    report = {
        "schema": "mc-agent/rom20-prepared/1",
        "status": "READY",
        "prepared_at": utc_now(),
        "run_id": run_id,
        "run_root": str(run_root),
        "run_trace_dir": str(run_trace_dir),
        "workspace": context.get("workspace"),
        "workspace_files": context.get("files"),
        "task_file": str(REPO / env["task_file"]),
        "task_sha256": sha256_file(REPO / env["task_file"]),
        "env_file": str(Path(context.get("workspace", "")) / "ENV.md") if context.get("workspace") else None,
        "env_sha256": next((entry["sha256"] for entry in context.get("files", []) if entry["path"] == "ENV.md"), None),
        "launch_command": launch_command,
        "sessions": env["sessions"],
        "harness": {"name": "pi", **env["pi"]},
        "model": env["model"],
        "python": env["python"],
        "java": env["java"],
        "git": git_state,
        "versions": derive_versions(
            require_json(REPO / env["map_manifest"], "map manifest"), env["audit_jar"]["path"]
        ),
        "artifacts": {
            "map": {"manifest": str(REPO / env["map_manifest"]),
                    "sha256": require_json(REPO / env["map_manifest"], "map manifest")["artifact"]["sha256"]},
            "interface_jar": env["interface_jar"],
            "audit_jar": env["audit_jar"],
        },
        "source_save": source_save,
        "preflight": preflight,
        "fixture": fixture,
        "experiment": experiment,
        "bridges": bridges,
        "challenge": challenge,
        "audit": {
            "run_id": run_id,
            "source_config": fixture.get("audit_config"),
            "experiment_config": experiment.get("audit_config"),
            "testmod_is_oracle_not_agent_logger": True,
        },
        "live_state": live,
        "validation": validation,
        "receipts": {name: value for name, value in receipts_index.items()},
        "no_task_launch": True,
        "notes": [
            "this report is operator-side; it is outside the clean workspace and was not part of the agent prompt",
            "the agent must acquire/fork/copy/load/restore/verify the runtime state itself; rom20-exp is blank",
            "challenge seed/inventory and the ready snapshot are operator evidence under the run root, not agent context",
            "the historical rom20 environment was prepared from source revision 1deb735; this post-review source revision has not executed that run - the coordinator launched the live agent with an explicit --session",
        ],
        "limitations": [
            "the accepted challenge generator cannot emit an empty cart and forces distinct per-cart signatures; duplicate item ids are present and checked",
            "the harness preflight is generic; the real task is a model session launched later by the coordinator",
        ],
    }
    # strip transcripts from the receipt index to keep the report readable; paths + hashes remain
    for value in report["receipts"].values():
        if isinstance(value, dict):
            value.pop("stdout", None)
            value.pop("stderr", None)
    prepared_json = Path(env["reports"]["prepared_json"])
    write_json(prepared_json, report)

    summary_lines = [
        f"# meta20 preparation - {report['status']}",
        "",
        f"- prepared: {report['prepared_at']}",
        f"- run id: `{run_id}`",
        f"- run root: `{run_root}`",
        f"- run trajectory: `{run_trace_dir}`",
        f"- agent workspace (clean cwd): `{context.get('workspace')}`",
        f"- task: `{report['task_file']}` (sha256 `{report['task_sha256']}`)",
        f"- env: `{report['env_file']}` (sha256 `{report['env_sha256']}`)",
        f"- raw agent session (planned): `{env['sessions']['agent_raw']}`",
        f"- model: `{env['model']['model']}` (provider `{env['model']['provider']}`) thinking `{env['model']['thinking']}`",
        f"- preflight: `{preflight.get('overall')}` - `{preflight.get('report')}`",
        "",
        "## Labs",
        "",
        f"- source `{env['labs']['source']['name']}`: pid {live['source']['identity']['pid']}, "
        f"rcon {env['labs']['source']['rcon_port']}, vantage {env['labs']['source']['vantage_port']}, "
        f"bridge {env['labs']['source']['bridge_port']}, audit phase `{live['source']['audit_phase']}`",
        f"- experiment `{env['labs']['experiment']['name']}`: pid {live['experiment']['identity']['pid']}, "
        f"rcon {env['labs']['experiment']['rcon_port']}, vantage {env['labs']['experiment']['vantage_port']}, "
        f"bridge {env['labs']['experiment']['bridge_port']}, audit phase `{live['experiment']['audit_phase']}` (blank world)",
        "",
        "## Bridges",
        "",
    ]
    for key, value in (bridges.get("bridges") or {}).items():
        summary_lines.append(
            f"- {key}: pid {value.get('pid')} (listen {value.get('listen_pid')}) "
            f"api {value.get('api_port')} log `{value.get('log')}`"
        )
    summary_lines += [
        "",
        "## Launch command (coordinator; not executed by the operator)",
        "",
        "```powershell",
        launch_command,
        "```",
        "",
        "## Evidence",
        "",
        f"- receipts: `{operator / 'receipts'}`",
        f"- preflight report: `{preflight.get('report')}`",
        f"- ready snapshot (operator only): `{fixture.get('ready_snapshot')}` (sha256 `{fixture.get('ready_sha256')}`)",
        f"- sealed challenge (operator only): `{challenge.get('path')}` (seed {challenge.get('seed')}, sha256 `{challenge.get('sha256')}`)",
        f"- source save verified: `{source_save.get('tree_sha256')}` ({source_save.get('files')} files, read-only)",
    ]
    prepared_md = Path(env["reports"]["prepared_md"])
    prepared_md.parent.mkdir(parents=True, exist_ok=True)
    prepared_md.write_text("\n".join(summary_lines) + "\n", "utf-8", newline="\n")
    (operator / "summary.md").write_text("\n".join(summary_lines) + "\n", "utf-8", newline="\n")
    say(f"prepared report: {prepared_json}")
    say(f"human summary:   {env['reports']['prepared_md']}")


# --------------------------------------------------------------------------- driver


def launch_command(env: dict[str, Any], workspace: Path) -> str:
    """The coordinator launch: real PowerShell with an explicit --session.

    ``PI_SESSION_FILE`` is only a shell-tool marker that pi writes *for* its
    commands; pi 0.87.1 selects the session file with ``--session`` (see
    ``docs/cli.md``), so the launch names the planned raw session explicitly.
    """
    return (
        f'Set-Location -LiteralPath "{workspace}"\n'
        f'& "{env["pi"]["command"]}" --print --no-context-files '
        f'--provider {env["model"]["provider"]} --model {env["model"]["model"]} '
        f'--thinking {env["model"]["thinking"]} '
        f'--session "{Path(env["sessions"]["agent_raw"])}" '
        f'"Read TASK.md and ENV.md in this directory, then carry out the task."'
    )


def cmd_run(args: argparse.Namespace) -> int:
    env_path = Path(args.env) if getattr(args, "env", None) else Path(os.environ.get("ROM20_ENV") or ENV_PATH)
    env = load_env(env_path)
    ensure_repo(env, env_path)
    run_id = validate_run_id(args.run_id or f"rom20-{utc_stamp()}")
    run_root = contained_child(LABS_ROOT, run_id, "run root")
    run_trace_dir = run_root / "run"
    operator = run_root / "operator"
    workspace = run_root / "workspace"
    if run_root.exists() and any(run_root.iterdir()):
        raise PrepError(
            f"{run_root} already exists; attempts and evidence are preserved - choose a new --run-id"
        )
    operator.mkdir(parents=True, exist_ok=True)
    receipts = Receipts(operator, run_id)
    receipts_index: dict[str, dict[str, Any]] = {}
    current = "stages"
    try:
        selected = ordered_stages(args.stages)
        if selected != list(STAGES):
            say(
                "note: --stages is a diagnostic subset, not a resume; a later stage without its"
                " in-process prerequisites fails instead of reusing stale receipts"
            )
        say(f"run {run_id} at {run_root} (stages: {', '.join(selected)})")
        current = "setup"
        if "clean" in selected:
            current = "clean"
            receipts_index["clean"] = stage_clean(env, receipts)
            say("clean: stale bridges stopped, labs stopped")

        if "context" in selected:
            current = "context"
            receipts_index["toolchain"] = receipts.write("toolchain", {
                "python": check_pycache(env), "java": check_java(env), "pi": check_pi(env),
            })
            junctions = [
                ensure_junction(REPO / "bridge", Path(env["bridge"]["worktree"])),
                ensure_junction(REPO / "agent-loop", Path(env["agent_loop"]["root"])),
            ]
            receipts_index["junctions"] = receipts.write("junctions", {"links": junctions})
            receipts_index["dependencies"] = receipts.write("dependencies", assert_dependencies(env))
            receipts_index["cache-seed"] = seed_cache(env, receipts)
            wrapper = write_bridge_wrapper(env)
            receipts_index["wrapper"] = receipts.write("bridge-wrapper", {"path": str(wrapper), "sha256": sha256_file(wrapper)})
            receipts_index["workspace"] = receipts.write("workspace", build_workspace(env, run_root, run_id, receipts))
            receipts_index["trace"] = receipts.write("run-trace", trace_init(env, run_trace_dir, workspace, run_id, receipts))
            say("context: toolchain verified, workspace built, run trajectory opened")

        if "source" in selected:
            current = "source"
            receipts_index["source-save"] = stage_source(env, receipts)
            say("source: read-only save verified against source-before.json")

        if "preflight" in selected:
            current = "preflight"
            wrapper = REPO / env["bridge"]["wrapper"]
            receipts_index["preflight-summary"] = stage_preflight(env, run_trace_dir, operator, wrapper, receipts)
            say("preflight: all real checks PASS")

        if "fixture" in selected:
            current = "fixture"
            receipts_index["fixture-ready"] = stage_fixture(env, run_id, run_root, operator, receipts)
            say("fixture: remote cold fetch + fresh 5-cart challenge + frozen ready state")

        if "experiment" in selected:
            current = "experiment"
            receipts_index["experiment-blank"] = stage_experiment(env, run_id, operator, receipts)
            say("experiment: blank lab provisioned, audit phase ready")

        if "bridges" in selected:
            current = "bridges"
            receipts_index["bridges"] = stage_bridges(env, run_root, receipts)
            say("bridges: both detached bridges connected")

        if "finalize" in selected:
            current = "finalize"
            needed = ("workspace", "fixture-ready", "experiment-blank", "bridges", "preflight-summary")
            missing = [name for name in needed if name not in receipts_index]
            if missing:
                raise PrepError(
                    f"finalize also needs receipts {missing} from earlier stages in this same process;"
                    " --stages is a diagnostic subset, not a resume"
                )
            command = launch_command(env, workspace)
            stage_finalize(env, run_id, run_root, run_trace_dir, operator, receipts, receipts_index, command)
            say("finalize: evidence declared, marks written, report written")

        say("preparation complete")
        return 0
    except PrepError as error:
        write_failure(operator, run_id, current, str(error), receipts_index)
        raise
    except Exception as error:  # unexpected faults still leave a bounded record
        write_failure(operator, run_id, current, f"{type(error).__name__}: {error}", receipts_index)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--env", type=Path, default=None,
        help="host-specific rom20 environment JSON (default: examples/coldstart/rom20-environment.json or ROM20_ENV)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="prepare the rom20 environment")
    run_parser.add_argument("--env", type=Path, default=argparse.SUPPRESS, help="same as the top-level --env")
    run_parser.add_argument("--run-id", default="", help="run id / directory name (default rom20-<utc>)")
    run_parser.add_argument(
        "--stages", default="",
        help="diagnostic subset of stages to run in this process (not a resume); default: all",
    )
    run_parser.set_defaults(handler=cmd_run)
    sub.add_parser("stages", help="list stages")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(raw)
    if args.command == "stages":
        print("\n".join(STAGES))
        return 0
    try:
        env_path = Path(args.env) if getattr(args, "env", None) else Path(os.environ.get("ROM20_ENV") or ENV_PATH)
        env = load_env(env_path)
        ensure_repo(env, env_path)
        ensure_venv(env, raw)
        return int(args.handler(args))
    except PrepError as error:
        print(f"[prep] FAILED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
