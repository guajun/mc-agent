#!/usr/bin/env python3
"""Harness/model environment contract for a cold-start run.

Answers one question before a model is started: can *this* fixed harness, with
its own tools, reach everything the agent will need - a terminal, the
filesystem, source/dependency endpoints, a JDK to build a mod, the bridge JSON
CLI (or MCP surface), and independent lab-server management?

The checks are generic.  None of them mention Minecart ROM, and the task prompt
is never sent anywhere.  Evidence (commands, exit codes, transcript hashes,
versions) lands in ``<out>/preflight.json`` plus one transcript per probe under
``<out>/probes/``; the live lab check snapshots ``lab.json`` and its console.

    python tools/harness_preflight.py run --config examples/coldstart/harness-pi.json \
        --out labs/coldstart/preflight --run-dir labs/coldstart/run-01

``--checks``/``--skip`` select a subset; ``--list`` names the checks.  Exit code
0 means overall PASS, 1 means at least one required check failed (never a
silent skip).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Iterable

import coldstart as cs

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
USER_AGENT = "mc-agent-harness-preflight/1.0 (https://github.com/guajun/mc-agent)"

ALL_CHECKS = (
    "harness_identity",
    "terminal",
    "filesystem",
    "ports",
    "source_fetch",
    "build_install",
    "bridge_cli",
    "bridge_mcp",
    "bridge_smoke",
    "lab_management",
)


# --------------------------------------------------------------------------- config


def load_config(path: Path) -> dict[str, Any]:
    config = cs.read_json(path)
    if not isinstance(config, dict):
        raise SystemExit("error: harness config is not a JSON object")
    config.setdefault("schema", "mc-agent-harness-config/1")
    config.setdefault("name", "unnamed")
    config.setdefault("fetch_urls", [])
    config.setdefault("build", {})
    config.setdefault("bridge", {})
    config.setdefault("lab", {})
    config.setdefault("ports", {})
    return config


# --------------------------------------------------------------------------- probes


class Probe:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.probe_dir = out_dir / "probes"
        self.probe_dir.mkdir(parents=True, exist_ok=True)
        self.counter = 0

    def command(
        self,
        check: str,
        argv: list[str],
        cwd: Path | None = None,
        timeout: float = 120.0,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.counter += 1
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{self.counter:02d}-{check}")[:80]
        transcript = self.probe_dir / f"{slug}.txt"
        started = time.time()
        full_env = dict(os.environ)
        if env:
            full_env.update(env)
        shown = " ".join(f'"{part}"' if " " in part else part for part in argv)
        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=full_env,
            )
            exit_code: int | None = completed.returncode
            output = (completed.stdout or "") + (completed.stderr or "")
        except subprocess.TimeoutExpired as error:
            exit_code = None
            output = f"[timeout after {timeout}s]\n{error.stdout or ''}{error.stderr or ''}"
        except OSError as error:
            exit_code = None
            output = f"[could not run: {error}]"
        elapsed = round(time.time() - started, 3)
        transcript.write_text(f"$ {shown}\n\n{output}", encoding="utf-8", newline="\n")
        return {
            "kind": "command",
            "command": shown,
            "exit_code": exit_code,
            "seconds": elapsed,
            "stdout_path": transcript.relative_to(self.out_dir).as_posix(),
            "stdout_sha256": cs.sha256_file(transcript),
            "stdout_excerpt": cs.tail_text(output.strip(), 1200),
        }

    def file(self, check: str, name: str, payload: bytes) -> dict[str, Any]:
        path = self.probe_dir / name
        path.write_bytes(payload)
        return {
            "kind": "file",
            "path": path.relative_to(self.out_dir).as_posix(),
            "sha256": cs.sha256_bytes(payload),
            "size": len(payload),
        }


def check_result(check_id: str, ok: bool, detail: str = "", evidence: Iterable[dict[str, Any]] = (), versions: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "PASS" if ok else "FAIL",
        "detail": detail,
        "evidence": list(evidence),
        "versions": versions or {},
    }


def skipped(check_id: str, reason: str) -> dict[str, Any]:
    return {"id": check_id, "status": "SKIP", "detail": reason, "evidence": [], "versions": {}}


def free_port(port: int) -> bool:
    if port <= 0:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def resolved_argv(command: str, arguments: list[str] | None = None) -> list[str]:
    """Resolve a command to an argv that CreateProcess can actually launch.

    Windows cannot start a ``.cmd``/``.bat`` shim directly, and ``shutil.which``
    returns it without a suffix the shell would add; wrap those in cmd.exe.
    """
    args = [str(part) for part in (arguments or [])]
    path = shutil.which(command) or command
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path, *args]
    return [path, *args]


# --------------------------------------------------------------------------- checks


def check_harness_identity(config: dict[str, Any], probe: Probe) -> dict[str, Any]:
    name = str(config.get("name") or "")
    command = str(config.get("command") or "")
    evidence: list[dict[str, Any]] = []
    versions: dict[str, Any] = {}
    ok = True
    detail: list[str] = []
    if not name:
        ok = False
        detail.append("config has no harness name")
    if command:
        argv = resolved_argv(command, [str(arg) for arg in config.get("version_args") or ["--version"]])
        result = probe.command("harness-version", argv, timeout=30)
        evidence.append(result)
        found = shutil.which(command)
        versions["command_path"] = found or ""
        if result["exit_code"] != 0:
            ok = False
            detail.append(f"`{command}` did not answer its version probe")
        else:
            versions["version_output"] = (result.get("stdout_excerpt") or "").strip().splitlines()[:2]
    else:
        ok = False
        detail.append("config has no harness command")
    session_env = str(config.get("session_env") or "")
    if session_env:
        versions["session_env_name"] = session_env
        versions["session_env_present"] = bool(os.environ.get(session_env))
    detail.append(f"model={config.get('model') or '(unset)'} provider={config.get('provider') or '(unset)'}")
    return check_result("harness_identity", ok, "; ".join(detail), evidence, versions)


def check_terminal(config: dict[str, Any], probe: Probe) -> dict[str, Any]:
    shell = config.get("shell") or ["bash", "--version"]
    result = probe.command("terminal", [str(part) for part in shell], timeout=30)
    return check_result(
        "terminal",
        result["exit_code"] == 0,
        "shell probe answered" if result["exit_code"] == 0 else "shell probe failed",
        [result],
    )


def check_filesystem(config: dict[str, Any], probe: Probe, out_dir: Path) -> dict[str, Any]:
    nonce = f"coldstart-{os.getpid()}-{int(time.time() * 1000)}"
    target = out_dir / "scratch" / "filesystem-probe.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(nonce + "\n", encoding="utf-8", newline="\n")
    read_back = target.read_text(encoding="utf-8").strip()
    ok = read_back == nonce and cs.sha256_file(target) == cs.sha256_bytes((nonce + "\n").encode())
    evidence = [
        probe.file("filesystem", "filesystem-probe-expected.txt", (nonce + "\n").encode()),
        {
            "kind": "readback",
            "path": target.relative_to(out_dir).as_posix(),
            "sha256": cs.sha256_file(target),
            "matched": ok,
        },
    ]
    return check_result("filesystem", ok, "wrote, read back and hashed a nonce file", evidence)


def check_ports(config: dict[str, Any], probe: Probe) -> dict[str, Any]:
    ports = config.get("ports") or {}
    wanted = {"api": int(ports.get("api") or 0), "mod": int(ports.get("mod") or 0)}
    lo, hi = (int(x) for x in (ports.get("range") or [0, 0])) if ports.get("range") else (0, 0)
    evidence: list[dict[str, Any]] = []
    ok = True
    details: list[str] = []
    for name, port in wanted.items():
        free = free_port(port)
        evidence.append({"kind": "port", "name": name, "port": port, "free": free})
        if not free:
            ok = False
            details.append(f"{name} port {port} is not free")
        elif lo and not (lo <= port <= hi):
            ok = False
            details.append(f"{name} port {port} is outside the reserved range {lo}-{hi}")
    if not details:
        details.append("reserved ports are free")
    return check_result("ports", ok, "; ".join(details), evidence)


def check_source_fetch(config: dict[str, Any], probe: Probe, out_dir: Path) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    successes = 0
    attempts = 0
    timeout = float((config.get("timeouts") or {}).get("fetch", 20))
    for url in config.get("fetch_urls") or []:
        attempts += 1
        started = time.time()
        record: dict[str, Any] = {"kind": "http", "url": url}
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(1 << 20)
                record.update(
                    {
                        "status": response.status,
                        "bytes": len(body),
                        "sha256": cs.sha256_bytes(body),
                        "seconds": round(time.time() - started, 3),
                    }
                )
                if response.status == 200:
                    successes += 1
        except (urllib.error.URLError, OSError, ValueError) as error:
            record.update({"error": str(error), "seconds": round(time.time() - started, 3)})
        evidence.append(record)
    pip_probe = (config.get("build") or {}).get("pip_packages") or []
    for package in pip_probe:
        destination = out_dir / "pip"
        destination.mkdir(parents=True, exist_ok=True)
        result = probe.command(
            "source-fetch",
            [sys.executable, "-m", "pip", "download", "--no-deps", "--dest", str(destination), str(package)],
            timeout=180,
        )
        evidence.append(result)
        attempts += 1
        if result["exit_code"] == 0:
            successes += 1
    ok = attempts > 0 and successes > 0
    detail = f"{successes}/{attempts} source/dependency endpoint(s) reachable" if attempts else "no fetch URLs configured"
    return check_result("source_fetch", ok, detail, evidence)


def discover_java_candidates(config: dict[str, Any], repo_root: Path) -> list[tuple[str, str]]:
    """--java, MC_AGENT_JAVA, lab metadata (labs/*/lab.json), then PATH."""
    candidates: list[tuple[str, str]] = []
    configured = str((config.get("build") or {}).get("java") or "")
    if configured:
        candidates.append((configured, "config"))
    if os.environ.get("MC_AGENT_JAVA"):
        candidates.append((os.environ["MC_AGENT_JAVA"], "MC_AGENT_JAVA"))
    for lab_json in sorted((repo_root / "labs").glob("*/lab.json")):
        try:
            recorded = cs.read_json(lab_json).get("java") or ""
        except (OSError, ValueError):
            continue
        if recorded:
            candidates.append((str(recorded), f"lab metadata {lab_json.parent.name}"))
    for name in ("javac", "java"):
        found = shutil.which(name)
        if found:
            candidates.append((found, f"PATH {name}"))
            break
    resolved: list[tuple[str, str]] = []
    seen: set[str] = set()
    for candidate, origin in candidates:
        path = Path(candidate)
        if not path.is_file():
            found = shutil.which(candidate)
            if not found:
                continue
            path = Path(found)
        key = str(path.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        javac = path.with_name("javac.exe" if os.name == "nt" else "javac")
        if not javac.is_file() and path.name.lower().startswith(("javac",)):
            javac = path
            path = path.with_name("java.exe" if os.name == "nt" else "java")
        resolved.append((str(path), origin))
    return resolved


def javac_major(version_output: str) -> int | None:
    match = re.search(r"javac\s+(\d+)(?:\.(\d+))?", version_output)
    if not match:
        return None
    major = int(match.group(1))
    if major == 1 and match.group(2) is not None:
        return int(match.group(2))
    return major


def check_build_install(config: dict[str, Any], probe: Probe, out_dir: Path) -> dict[str, Any]:
    build = config.get("build") or {}
    min_java = int(build.get("min_java") or 0)
    candidates = discover_java_candidates(config, ROOT)
    if not candidates:
        return check_result("build_install", False, "no JDK found (config, MC_AGENT_JAVA, labs/*/lab.json, PATH)")
    evidence: list[dict[str, Any]] = []
    chosen: tuple[str, str, int | None] | None = None
    for java, origin in candidates:
        javac = Path(java).with_name("javac.exe" if os.name == "nt" else "javac")
        if not javac.is_file():
            continue
        version = probe.command("build", [str(javac), "-version"], timeout=60)
        major = javac_major(version.get("stdout_excerpt") or "")
        evidence.append({"kind": "candidate", "java": java, "origin": origin, "javac_major": major, "probe": version})
        if major is None or (min_java and major < min_java):
            continue
        chosen = (java, origin, major)
        break
    if chosen is None:
        return check_result(
            "build_install",
            False,
            f"no JDK >= {min_java} found among {len(candidates)} candidate(s)",
            evidence,
            {"min_java": min_java, "candidates": [c[0] for c in candidates]},
        )
    java, origin, major = chosen
    java_path = Path(java)
    javac = java_path.with_name("javac.exe" if os.name == "nt" else "javac")
    jar = java_path.with_name("jar.exe" if os.name == "nt" else "jar")
    if not jar.is_file():
        fallback = shutil.which("jar")
        jar = Path(fallback) if fallback else jar
    if not jar.is_file():
        return check_result("build_install", False, f"JDK at {java} has no jar tool", evidence)
    versions: dict[str, Any] = {"java": java, "java_origin": origin, "javac_major": major}
    work = out_dir / "build"
    source_dir = work / "src"
    classes = work / "classes"
    install_dir = work / "install"
    for directory in (source_dir, classes, install_dir):
        directory.mkdir(parents=True, exist_ok=True)
    source = source_dir / "ColdstartProbe.java"
    source.write_text(
        "public final class ColdstartProbe {\n"
        "    public static void main(String[] args) {\n"
        '        System.out.println("coldstart-probe-ok");\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
        newline="\n",
    )
    version = probe.command("build", [str(javac), "-version"], timeout=60)
    evidence.append(version)
    evidence.append({"kind": "chosen", "java": java, "origin": origin, "javac_major": major})
    compile_result = probe.command("build", [str(javac), "-d", str(classes), str(source)], timeout=120)
    evidence.append(compile_result)
    if compile_result["exit_code"] != 0:
        return check_result("build_install", False, "javac failed", evidence, versions)
    jar_path = work / "coldstart-probe.jar"
    jar_result = probe.command("build", [str(jar), "--create", "--file", str(jar_path), "-C", str(classes), "."], timeout=120)
    evidence.append(jar_result)
    if jar_result["exit_code"] != 0:
        return check_result("build_install", False, "jar packaging failed", evidence, versions)
    try:
        with zipfile.ZipFile(jar_path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as error:
        return check_result("build_install", False, f"built jar is not readable: {error}", evidence, versions)
    installed = install_dir / jar_path.name
    shutil.copy2(jar_path, installed)
    same = cs.sha256_file(jar_path) == cs.sha256_file(installed)
    versions["jar_sha256"] = cs.sha256_file(jar_path)
    evidence.append(
        {
            "kind": "artifact",
            "path": jar_path.relative_to(out_dir).as_posix(),
            "installed": installed.relative_to(out_dir).as_posix(),
            "entries": names,
            "sha256": versions["jar_sha256"],
            "install_sha256_matches": same,
        }
    )
    ok = "ColdstartProbe.class" in names and same
    return check_result("build_install", ok, "compiled, packaged and installed a probe jar", evidence, versions)


def bridge_command(config: dict[str, Any]) -> str:
    configured = str((config.get("bridge") or {}).get("command") or "")
    if configured:
        return configured
    found = shutil.which("mc-bridge") or shutil.which("mc-bridge.exe")
    if found:
        return found
    venv = ROOT / ".venv" / "Scripts" / "mc-bridge.exe"
    if venv.is_file():
        return str(venv)
    return "mc-bridge"


def check_bridge_cli(config: dict[str, Any], probe: Probe) -> dict[str, Any]:
    command = bridge_command(config)
    result = probe.command("bridge-cli", resolved_argv(command, ["--help"]), timeout=60)
    return check_result(
        "bridge_cli",
        result["exit_code"] == 0,
        "mc-bridge CLI answered --help" if result["exit_code"] == 0 else "mc-bridge CLI is not runnable",
        [result],
    )


def check_bridge_mcp(config: dict[str, Any], probe: Probe) -> dict[str, Any]:
    command = bridge_command(config)
    result = probe.command("bridge-mcp", resolved_argv(command, ["mcp", "--help"]), timeout=60)
    if result["exit_code"] == 0:
        return check_result("bridge_mcp", True, "mc-bridge MCP front-end answers --help", [result])
    return {
        "id": "bridge_mcp",
        "status": "SKIP",
        "detail": "MCP front-end unavailable; the JSON CLI (checked separately) is the accepted surface",
        "evidence": [result],
        "versions": {},
    }


def find_sibling(config: dict[str, Any], name: str) -> Path | None:
    roots: list[Path] = []
    configured = (config.get("bridge") or {}).get("siblings_root")
    if configured:
        roots.append(Path(str(configured)))
    roots.append(ROOT)
    roots.append(ROOT.parent)
    for root in roots:
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def check_bridge_smoke(config: dict[str, Any], probe: Probe, out_dir: Path) -> dict[str, Any]:
    smoke = TOOLS / "smoke_offline.py"
    bridge = find_sibling(config, "bridge")
    loop = find_sibling(config, "agent-loop")
    if not smoke.is_file():
        return check_result("bridge_smoke", False, f"{smoke} is missing")
    if bridge is None or loop is None:
        return check_result(
            "bridge_smoke",
            False,
            "sibling checkouts not found; set bridge.siblings_root or place bridge/ and agent-loop/ next to this repo",
        )
    ports = config.get("ports") or {}
    argv = [
        sys.executable,
        str(smoke),
        "--backend",
        "echo",
        "--mod-port",
        str(int(ports.get("mod") or 27191)),
        "--api-port",
        str(int(ports.get("api") or 27190)),
        "--timeout",
        str(float((config.get("timeouts") or {}).get("smoke", 60))),
    ]
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    result = probe.command("bridge-smoke", argv, cwd=ROOT, timeout=float((config.get("timeouts") or {}).get("smoke_total", 300)), env=env)
    ok = result["exit_code"] == 0 and "[smoke] OK" in (result.get("stdout_excerpt") or "")
    return check_result("bridge_smoke", ok, "generic offline smoke with the echo backend", [result])


def check_lab_management(config: dict[str, Any], probe: Probe, out_dir: Path) -> dict[str, Any]:
    lab = config.get("lab") or {}
    script = TOOLS / "lab_server.py"
    evidence: list[dict[str, Any]] = []
    versions: dict[str, Any] = {}
    listing = probe.command("lab-list", [sys.executable, str(script), "list", "--json"], cwd=ROOT, timeout=60)
    evidence.append(listing)
    if listing["exit_code"] != 0:
        return check_result("lab_management", False, "lab_server.py list failed", evidence)
    if not lab.get("enabled", True):
        return check_result("lab_management", True, "lab_server.py list works (provision/start disabled in config)", evidence)
    name = str(lab.get("name") or "preflight")
    java = str(lab.get("java") or (config.get("build") or {}).get("java") or "")
    if not java:
        candidates = discover_java_candidates(config, ROOT)
        java = candidates[0][0] if candidates else ""
    provision_argv = [sys.executable, str(script), "provision", "--name", name, "--void", "--fabric-api", "--carpet"]
    if java:
        provision_argv += ["--java", java]
    provision = probe.command("lab-provision", provision_argv, cwd=ROOT, timeout=float((config.get("timeouts") or {}).get("provision", 600)))
    evidence.append(provision)
    if provision["exit_code"] != 0:
        return check_result("lab_management", False, "lab provision failed", evidence)
    lab_dir = ROOT / "labs" / name
    lab_json = lab_dir / "lab.json"
    if lab_json.is_file():
        state = cs.read_json(lab_json)
        versions.update({key: state.get(key) for key in ("minecraft", "loader", "launcher", "java", "serverPort", "rconPort")})
        evidence.append(probe.file("lab", f"lab-{name}.json", lab_json.read_bytes()))
    wanted_ports = lab.get("ports") or {}
    if wanted_ports:
        patched, patch_detail = patch_lab_ports(lab_dir, int(wanted_ports.get("game") or 0), int(wanted_ports.get("rcon") or 0))
        versions["ports_patched"] = patched
        if patch_detail:
            versions["ports_patch_detail"] = patch_detail
        if not patched:
            return check_result("lab_management", False, f"could not pin the lab to the reserved ports: {patch_detail}", evidence, versions)
        if lab_json.is_file():
            state = cs.read_json(lab_json)
            versions["serverPort"] = state.get("serverPort")
            versions["rconPort"] = state.get("rconPort")
    if not lab.get("start", False):
        return check_result("lab_management", True, "lab provisioned; start disabled in config", evidence, versions)
    start = probe.command(
        "lab-start",
        [sys.executable, str(script), "start", "--name", name, "--wait", str(int(lab.get("wait") or 300))],
        cwd=ROOT,
        timeout=float((config.get("timeouts") or {}).get("lab_start", 900)),
    )
    evidence.append(start)
    ready = start["exit_code"] == 0 and "ready in" in (start.get("stdout_excerpt") or "")
    status = probe.command("lab-status", [sys.executable, str(script), "status", "--name", name, "--json"], cwd=ROOT, timeout=60)
    evidence.append(status)
    exec_result = probe.command(
        "lab-exec",
        [sys.executable, str(script), "exec", "--name", name, "list", "--json"],
        cwd=ROOT,
        timeout=60,
    )
    evidence.append(exec_result)
    stop = probe.command("lab-stop", [sys.executable, str(script), "stop", "--name", name], cwd=ROOT, timeout=180)
    evidence.append(stop)
    console = lab_dir / "logs" / "console.log"
    if console.is_file():
        versions["console_sha256"] = cs.sha256_file(console)
        versions["console_bytes"] = console.stat().st_size
    ok = ready and status["exit_code"] == 0 and exec_result["exit_code"] == 0 and stop["exit_code"] == 0
    return check_result("lab_management", ok, f"real lab {name} started, answered RCON and stopped" if ok else "live lab check failed", evidence, versions)


def patch_lab_ports(lab_dir: Path, game_port: int, rcon_port: int) -> tuple[bool, str]:
    """Pin a freshly provisioned lab to the run's reserved ports.

    ``lab_server.py`` allocates free ports; a cold-start run wants them
    deterministic and inside the reserved range.  Only the lab's own data
    files are rewritten here - the shared tool is untouched.
    """
    if not game_port and not rcon_port:
        return False, ""
    if game_port and not free_port(game_port):
        return False, f"game port {game_port} is not free"
    if rcon_port and not free_port(rcon_port):
        return False, f"rcon port {rcon_port} is not free"
    lab_json = lab_dir / "lab.json"
    rcon_json = lab_dir / "rcon.json"
    properties = lab_dir / "server.properties"
    if not (lab_json.is_file() and rcon_json.is_file() and properties.is_file()):
        return False, "lab is not fully provisioned"
    state = cs.read_json(lab_json)
    if game_port:
        state["serverPort"] = game_port
    if rcon_port:
        state["rconPort"] = rcon_port
    cs.write_json(lab_json, state)
    rcon = cs.read_json(rcon_json)
    if rcon_port:
        rcon["port"] = rcon_port
    cs.write_json(rcon_json, rcon)
    lines = properties.read_text(encoding="utf-8").splitlines()
    rewritten: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if game_port and key in ("server-port", "query.port"):
            rewritten.append(f"{key}={game_port}")
        elif rcon_port and key == "rcon.port":
            rewritten.append(f"{key}={rcon_port}")
        else:
            rewritten.append(line)
    properties.write_text("\n".join(rewritten) + "\n", encoding="utf-8", newline="\n")
    return True, f"pinned game={game_port} rcon={rcon_port}"


# --------------------------------------------------------------------------- report


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Harness preflight - {report.get('harness', {}).get('name') or '(unnamed)'}",
        "",
        f"- schema: `{report.get('schema')}`",
        f"- started: {report.get('started_at')}",
        f"- ended: {report.get('ended_at')}",
        f"- **overall**: {report.get('overall')}",
        "",
        "| check | status | detail |",
        "| --- | --- | --- |",
    ]
    for check in report.get("checks", []):
        lines.append(f"| `{check.get('id')}` | {check.get('status')} | {check.get('detail')} |")
    lines += ["", "## Environment", ""]
    for key, value in (report.get("environment") or {}).items():
        lines.append(f"- {key}: `{value}`")
    return "\n".join(lines) + "\n"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="execute the preflight checks")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--out", required=True, type=Path)
    run.add_argument("--run-dir", type=Path, default=None, help="append a mark record to this run")
    run.add_argument("--checks", default="", help="comma-separated subset of check ids")
    run.add_argument("--skip", default="", help="comma-separated check ids to skip")
    run.add_argument("--set", action="append", default=[], metavar="DOTTED.KEY=VALUE", help="override a config value")
    run.add_argument("--force", action="store_true", help="allow a non-empty output directory")
    sub.add_parser("list", help="print the available check ids")
    selftest = sub.add_parser("selftest", help="run the built-in quick check")
    selftest.add_argument("--out", type=Path, default=None)
    return parser.parse_args(argv)


def apply_overrides(config: dict[str, Any], overrides: Iterable[str]) -> dict[str, Any]:
    for override in overrides:
        if "=" not in override:
            raise SystemExit(f"error: --set expects DOTTED.KEY=VALUE, got {override!r}")
        dotted, value = override.split("=", 1)
        try:
            parsed: Any = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        cursor = config
        parts = dotted.split(".")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = parsed
    return config


def run_checks(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    out_dir: Path = args.out
    if out_dir.exists() and any(out_dir.iterdir()) and not args.force:
        raise SystemExit(f"error: {out_dir} is not empty; pass --force to reuse it")
    out_dir.mkdir(parents=True, exist_ok=True)
    probe = Probe(out_dir)

    selected = [c for c in (args.checks.split(",") if args.checks else ALL_CHECKS) if c]
    skipped = set(c for c in (args.skip.split(",") if args.skip else []) if c)
    unknown = sorted(set(selected) - set(ALL_CHECKS))
    if unknown:
        raise SystemExit(f"error: unknown check(s): {', '.join(unknown)}")

    checks: list[dict[str, Any]] = []
    started = cs.utc_now()
    for check_id in selected:
        if check_id in skipped:
            checks.append(skipped_result(check_id, "skipped by request"))
            continue
        cs.say(f"[preflight] {check_id} ...")
        try:
            if check_id == "harness_identity":
                result = check_harness_identity(config, probe)
            elif check_id == "terminal":
                result = check_terminal(config, probe)
            elif check_id == "filesystem":
                result = check_filesystem(config, probe, out_dir)
            elif check_id == "ports":
                result = check_ports(config, probe)
            elif check_id == "source_fetch":
                result = check_source_fetch(config, probe, out_dir)
            elif check_id == "build_install":
                result = check_build_install(config, probe, out_dir)
            elif check_id == "bridge_cli":
                result = check_bridge_cli(config, probe)
            elif check_id == "bridge_mcp":
                result = check_bridge_mcp(config, probe)
            elif check_id == "bridge_smoke":
                result = check_bridge_smoke(config, probe, out_dir)
            elif check_id == "lab_management":
                result = check_lab_management(config, probe, out_dir)
            else:
                result = skipped_result(check_id, "not implemented")
        except Exception as error:  # a broken probe is a failed check, not a crash
            result = check_result(check_id, False, f"probe raised {type(error).__name__}: {error}")
        cs.say(f"  {result['status']}: {result['detail']}")
        checks.append(result)

    required = [c for c in checks if c["status"] != "SKIP"]
    overall = "PASS" if required and all(c["status"] == "PASS" for c in required) else "FAIL"
    report = {
        "schema": cs.SCHEMA_PREFLIGHT,
        "harness": {"name": config.get("name"), "model": config.get("model"), "provider": config.get("provider")},
        "config": redact_config(config),
        "started_at": started,
        "ended_at": cs.utc_now(),
        "overall": overall,
        "checks": checks,
        "environment": {
            "python": sys.version.split()[0],
            "executable": sys.executable,
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
            "repo": repo_head(ROOT),
            "run_dir": str(args.run_dir) if args.run_dir else "",
        },
    }
    cs.write_json(out_dir / "preflight.json", report)
    (out_dir / "preflight.md").write_text(render_markdown(report), encoding="utf-8", newline="\n")
    if args.run_dir:
        append_mark(args.run_dir, report, out_dir)
    cs.say(f"[preflight] overall {overall} -> {out_dir / 'preflight.json'}")
    return report


def skipped_result(check_id: str, reason: str) -> dict[str, Any]:
    return skipped(check_id, reason)


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Keep the config for the record; nothing here is expected to hold secrets."""
    copy = json.loads(json.dumps(config, ensure_ascii=False, default=str))
    for key in list(copy):
        if "secret" in key.lower() or "token" in key.lower() or "key" in key.lower():
            copy[key] = "<redacted>"
    return copy


def repo_head(root: Path) -> str:
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def append_mark(run_dir: Path, report: dict[str, Any], out_dir: Path) -> None:
    trajectory = run_dir / cs.TRAJECTORY_FILE
    if not trajectory.exists():
        cs.say(f"note: {trajectory} does not exist; writing preflight as a standalone report")
        return
    cs.append_jsonl(
        trajectory,
        {
            "schema": cs.SCHEMA_TRAJECTORY,
            "record": "mark",
            "name": "harness_preflight",
            "actor": "operator",
            "at": cs.utc_now(),
            "data": {
                "overall": report.get("overall"),
                "report": str(out_dir / "preflight.json"),
                "report_sha256": cs.sha256_file(out_dir / "preflight.json"),
                "checks": {check["id"]: check["status"] for check in report.get("checks", [])},
            },
        },
    )


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "list":
        cs.say("checks: " + ", ".join(ALL_CHECKS))
        return 0
    if args.command == "selftest":
        out_dir = args.out or (ROOT / "labs" / "_preflight-selftest")
        config = {
            "name": "selftest",
            "command": sys.executable,
            "version_args": ["--version"],
            "shell": [sys.executable, "--version"],
            "fetch_urls": [],
            "ports": {"api": 0, "mod": 0},
            "lab": {"enabled": False},
        }
        args.out = out_dir
        args.checks = "harness_identity,terminal,filesystem"
        args.skip = ""
        args.force = True
        args.run_dir = None
        args.set = []
        report = run_checks(args, config)
        cs.say(json.dumps({c["id"]: c["status"] for c in report["checks"]}, ensure_ascii=False))
        return 0 if report["overall"] == "PASS" else 1
    config = apply_overrides(load_config(args.config), args.set)
    report = run_checks(args, config)
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
