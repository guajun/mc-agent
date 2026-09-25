#!/usr/bin/env python3
"""Provision, start and command a headless Fabric lab server.

A *lab* is one throwaway dedicated server under ``labs/<name>/``: its own
world, its own ports, its own mods, its own console log. An agent provisions
one, starts it detached, then types into its console over RCON - no GUI, no
window, no human, and nothing shared with the player's live client.

    python tools/lab_server.py provision --name smoke --void --fabric-api --carpet
    python tools/lab_server.py start --name smoke --wait 300
    python tools/lab_server.py exec --name smoke "list"
    python tools/lab_server.py exec --name smoke "summon minecraft:tnt 0 5 0 {}"
    python tools/lab_server.py stop --name smoke

Subcommands: ``provision``, ``start``, ``stop``, ``status``, ``exec``, ``list``,
``identity``, ``verify``.

Layout of one lab::

    labs/<lab>/fabric-server-mc.<mc>-loader.<loader>-launcher.<installer>.jar
                                     the launcher jar: a Fabric installer that
                                     downloads the server on the first start
    labs/<lab>/.fabric/server/       what it downloaded (<mc>-server.jar, the
                                     loader jar); versions/<mc>/ is the unpacked
                                     vanilla bundle
    labs/<lab>/server.properties     our keys, plus whatever the server adds
    labs/<lab>/eula.txt              eula=true; a lab still has to agree
    labs/<lab>/rcon.json             {host, port, password}: the console channel
    labs/<lab>/lab.json              what was provisioned (mc, loader, java, ports,
                                     identity, mod records with SHA-256)
    labs/<lab>/identity.json         derived identity/endpoints for other tools
    labs/<lab>/run.json              the last/current process (pid, started, argv,
                                     the hashes that were on disk at start)
    labs/<lab>/mods/                 Fabric API, Carpet, --mod-jar/--mod-url files
    labs/<lab>/world/                the level; --world copies a save in here
    labs/<lab>/audit/                where game-side mods write their samples
    labs/<lab>/mc-agent-server/      the server-vantage mod's dir; it writes
                                     port.txt here when it binds
    labs/<lab>/logs/console.log      merged stdout+stderr, one start per section
    labs/_cache/                     launcher and mods, downloaded once for all labs

Every port, the world, the audit directory and the instance id belong to one
lab and are recorded before the first start - two labs can run at once without
sharing a socket or a world. ``identity`` prints that record; ``verify``
re-hashes the deployed mods and, with ``--require-vantage``, checks that
``port.txt`` still names the recorded server-vantage port.

A jar is compared by SHA-256, not by size: re-provisioning with a rebuilt jar
that happens to have the same length replaces the old bytes. A lab that records
a required mod (``--test-mod`` or ``--require-mod``) refuses to start without
it, and refuses to start when a deployed jar's bytes no longer match lab.json
(``--allow-mod-drift`` overrides the latter, never the former). Fabric loads
mods at server start, so the order is always: deploy while stopped, then start.

Everything under ``labs/`` is disposable and git-ignored, and downloads happen
only when the cached file is missing. The launcher and the mods come from the
shared cache; the vanilla server is not cached there, because the launcher
insists on its own copy inside each lab.

RCON is spoken by this file directly (length/id/type header, payload plus two
NULs, one auth packet, one exec packet, a response that may span several
packets) - no third-party dependency, nothing to install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Iterable, NoReturn

ROOT = Path(__file__).resolve().parent.parent
LABS = ROOT / "labs"
CACHE = LABS / "_cache"

FABRIC_META = "https://meta.fabricmc.net/v2"
MODRINTH = "https://api.modrinth.com/v2"
USER_AGENT = "mc-agent-lab-server/1.0 (https://github.com/guajun/mc-agent)"

DEFAULT_MC = "26.2"
DEFAULT_MEMORY = "2G"

# One line per start, so "is the server up?" means "did a Done arrive after the
# last start marker?" - appending keeps the previous run readable in the log.
START_MARKER = "===== lab_server start"
DONE_MARKER = "Done ("

# Same skip list as docs/protocol-snapshot.md: a forked world carries blocks and
# entities, not another server's session lock, inventories or logs. 26.2 renamed
# playerdata/ to players/, so the list carries both spellings.
WORLD_COPY_SKIP = ("session.lock", "playerdata", "players", "stats", "advancements", "logs")

# A void lab is a flat world whose only layer is air, in the void biome: a clean,
# deterministic box with no terrain, no light, nothing to collide with.
VOID_LEVEL_TYPE = "minecraft:flat"
VOID_GENERATOR_SETTINGS = (
    '{"layers":[{"block":"minecraft:air","height":1}],"biome":"minecraft:the_void"}'
)

RCON_HOST = "127.0.0.1"


# --------------------------------------------------------------------------- console


def show(text: str) -> str:
    """Keep the console out of the encoding business; same rule as game_cmd.py.

    The game and the Fabric launcher both answer in whatever locale they were
    started in, so a log line can be in any script - print it as escapes
    rather than hand the terminal bytes it may decode as something else.
    """
    return text if all(ord(char) < 128 for char in text) else ascii(text)


def say(message: str = "") -> None:
    print(show(message), flush=True)


def die(message: str, code: int = 2) -> NoReturn:
    print(f"error: {show(message)}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def human_size(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------- network


def http_json(url: str) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        die(f"{url} answered HTTP {error.code}")
    except OSError as error:
        die(f"{url} is unreachable: {error}")


def download(url: str, dest: Path, what: str) -> Path:
    """Fetch ``url`` to ``dest`` unless it is already there. Returns ``dest``."""
    if dest.exists() and dest.stat().st_size > 0:
        say(f"  {what}: cached {rel(dest)}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=180) as response, open(partial, "wb") as handle:
            shutil.copyfileobj(response, handle, 1 << 16)
    except OSError as error:
        partial.unlink(missing_ok=True)
        die(f"download failed: {url}: {error}")
    partial.replace(dest)
    say(f"  {what}: downloaded {dest.name} ({human_size(dest.stat().st_size)})")
    return dest


def fabric_loader_version(mc: str) -> str:
    versions = http_json(f"{FABRIC_META}/versions/loader/{urllib.parse.quote(mc)}")
    if not versions:
        die(f"Fabric has no loader for Minecraft {mc}")
    stable = [entry["loader"]["version"] for entry in versions if entry["loader"].get("stable")]
    return (stable or [entry["loader"]["version"] for entry in versions])[0]


def fabric_installer_version() -> str:
    versions = http_json(f"{FABRIC_META}/versions/installer")
    if not versions:
        die("Fabric publishes no installer version")
    stable = [entry["version"] for entry in versions if entry.get("stable")]
    return (stable or [entry["version"] for entry in versions])[0]


def modrinth_download(slug: str, mc: str) -> tuple[Path, str]:
    """Newest fabric build of ``slug`` for ``mc``: (cached file, version number)."""
    query = urllib.parse.urlencode(
        {"loaders": json.dumps(["fabric"]), "game_versions": json.dumps([mc])}
    )
    versions = http_json(f"{MODRINTH}/project/{urllib.parse.quote(slug)}/version?{query}")
    if not versions:
        die(f"Modrinth has no {slug} build for Minecraft {mc}")
    version = versions[0]
    files = version.get("files") or []
    if not files:
        die(f"{slug} {version.get('version_number')} has no files")
    url = files[0]["url"]
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name)
    version_number = str(version.get("version_number") or "")
    return download(url, CACHE / "mods" / name, f"{slug} {version_number}"), version_number


def free_port() -> int:
    """A port nothing is listening on right now (bound on every interface)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("", 0))
        return int(probe.getsockname()[1])


def port_is_open(port: int, host: str = RCON_HOST) -> bool:
    """Is something accepting TCP connections on host:port right now?"""
    if not 1 <= port <= 65535:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def ports_of_other_labs(name: str) -> set[int]:
    """Every port another lab recorded, so a new lab cannot borrow one."""
    used: set[int] = set()
    if not LABS.is_dir():
        return used
    for other in LABS.iterdir():
        if not other.is_dir() or other.name == name or other.name.startswith("_"):
            continue
        state = read_json(other / "lab.json") or {}
        for key in ("serverPort", "rconPort", "serverVantagePort", "bridgeApiPort"):
            value = state.get(key)
            if isinstance(value, int):
                used.add(value)
    return used


def choose_port(requested: int, used: set[int], label: str, *, allow_open: bool = False) -> int:
    """Explicit port wins; otherwise any free port no other lab recorded.

    ``allow_open`` is for a port this lab already owns (its bridge may be
    listening while the Minecraft server is stopped): the value is identity,
    not a fresh bind request.
    """
    if requested:
        if not 1 <= requested <= 65535:
            die(f"{label} port {requested} is out of range")
        if requested in used:
            die(f"{label} port {requested} is already recorded for another lab in {rel(LABS)}")
        if port_is_open(requested) and not allow_open:
            die(f"{label} port {requested} is already in use on this machine")
        used.add(requested)
        return requested
    for _ in range(256):
        candidate = free_port()
        if candidate in used or port_is_open(candidate):
            continue
        used.add(candidate)
        return candidate
    die(f"could not find a free {label} port after 256 tries")


# --------------------------------------------------------------------------- lab state


def lab_path(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or "") or not name.strip("."):
        die(f"bad lab name {name!r}: use letters, digits, dot, dash, underscore")
    return LABS / name


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        say(f"warning: {rel(path)} is unreadable ({error}); ignoring it")
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", "utf-8")


def load_lab(name: str, *, required: bool = True) -> tuple[Path, dict[str, Any]]:
    lab = lab_path(name)
    state = read_json(lab / "lab.json") or {}
    if required and not state:
        die(f"no lab called {name!r} - provision it first (see docs/lab-server.md)")
    return lab, state


# --------------------------------------------------------------------------- deployment bytes


def sha256_file(path: Path) -> str:
    """Content hash; this is what decides whether a jar needs replacing."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {"sha256": sha256_file(path), "size": path.stat().st_size}


def deploy_file(source: Path, target: Path, what: str) -> dict[str, Any]:
    """Install ``source`` at ``target`` when the bytes differ, then verify.

    Comparing content - not size - is the point: a rebuilt jar can keep the
    same size while the code inside changed. The copy lands on a temporary
    name and is renamed into place, so a reader never sees a half-written jar.
    """
    stamp = file_record(source)
    action = "installed"
    if target.exists():
        action = "unchanged" if sha256_file(target) == stamp["sha256"] else "replaced"
    if action != "unchanged":
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".deploying")
        temporary.unlink(missing_ok=True)
        shutil.copy2(source, temporary)
        if sha256_file(temporary) != stamp["sha256"]:
            temporary.unlink(missing_ok=True)
            die(f"deploying {what} to {rel(target)} did not reproduce the source bytes")
        os.replace(temporary, target)
    return {"name": target.name, "action": action, "sha256": stamp["sha256"], "size": stamp["size"]}


def mod_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Provisioned mods as records; lab.json written before this tool is
    hashed only carried file names, and those still work."""
    records: list[dict[str, Any]] = []
    for raw in state.get("mods") or []:
        records.append({"name": raw} if isinstance(raw, str) else dict(raw))
    return records


def mod_names(state: dict[str, Any]) -> list[str]:
    return [record["name"] for record in mod_records(state)]


def lab_running(lab: Path) -> tuple[bool, int]:
    run = read_json(lab / "run.json") or {}
    pid = int(run.get("pid") or 0)
    return (bool(pid) and process_matches(pid, float(run.get("startedAt") or 0))), pid


def read_port_file(path: Path) -> str | None:
    try:
        text = path.read_text("utf-8").strip()
    except OSError:
        return None
    return text or None


def check_deployment(
    lab: Path, state: dict[str, Any], *, allow_drift: bool = False
) -> tuple[list[dict[str, Any]], list[str]]:
    """Hash every jar in ``mods/`` and compare it with the provisioned bytes.

    Returns the actual on-disk records and human-readable problems. A missing
    recorded or required jar is always a problem; content drift is a problem
    unless the caller explicitly allows it.
    """
    mods_dir = lab / "mods"
    recorded = {record["name"]: record for record in mod_records(state)}
    required = list(state.get("requiredMods") or [])
    for record in recorded.values():
        if record.get("required") and record["name"] not in required:
            required.append(record["name"])

    present = {path.name: path for path in sorted(mods_dir.glob("*.jar"))} if mods_dir.is_dir() else {}
    actual: list[dict[str, Any]] = []
    for name, path in present.items():
        stamp = file_record(path)
        prior = recorded.get(name) or {}
        drift = bool(prior.get("sha256")) and prior["sha256"] != stamp["sha256"]
        actual.append(
            {
                "name": name,
                "sha256": stamp["sha256"],
                "size": stamp["size"],
                "recorded": bool(prior),
                "required": name in required,
                "drift": drift,
            }
        )

    problems: list[str] = []
    for name, record in recorded.items():
        if not (mods_dir / name).is_file():
            problems.append(f"mod {name}: missing from {rel(mods_dir)} - re-run provision to deploy it")
        elif record.get("sha256") and not allow_drift:
            current = next((item["sha256"] for item in actual if item["name"] == name), "")
            if current != record["sha256"]:
                problems.append(
                    f"mod {name}: deployed sha256 {current[:12]} does not match the provisioned"
                    f" {record['sha256'][:12]} - re-run provision to deploy the bytes you mean"
                )
    for name in required:
        if not (mods_dir / name).is_file():
            problems.append(
                f"required mod {name}: missing; this instance cannot be run as an auditable lab without it"
            )
    return actual, problems


def identity_payload(lab: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Everything a caller needs to tell this lab apart from every other one."""
    port_file = Path(state.get("portFile") or (lab / "mc-agent-server" / "port.txt"))
    actual_mods, problems = check_deployment(lab, state)
    required = list(state.get("requiredMods") or [])
    missing = [name for name in required if not (lab / "mods" / name).is_file()]
    for name in missing:
        if not any(item["name"] == name for item in actual_mods):
            actual_mods.append(
                {"name": name, "sha256": "", "size": 0, "recorded": False, "required": True, "drift": False}
            )
    running, pid = lab_running(lab)
    return {
        "lab": state.get("name") or lab.name,
        "instanceId": state.get("instanceId"),
        "minecraft": state.get("minecraft"),
        "loader": state.get("loader"),
        "worldDir": state.get("worldDir") or str((lab / "world").resolve()),
        "auditDir": state.get("auditDir") or str((lab / "audit").resolve()),
        "serverDir": state.get("serverDir") or str((lab / "mc-agent-server").resolve()),
        "serverVantagePort": state.get("serverVantagePort"),
        "serverVantagePortFile": str(port_file),
        "serverVantagePortActual": read_port_file(port_file),
        "bridgeApiPort": state.get("bridgeApiPort"),
        "serverPort": state.get("serverPort"),
        "rconPort": state.get("rconPort"),
        "requiredMods": required,
        "mods": actual_mods,
        "deploymentProblems": problems,
        "running": running,
        "pid": pid if running else None,
        "restartPending": bool(state.get("restartPending")),
    }


def write_identity(lab: Path, state: dict[str, Any]) -> None:
    payload = identity_payload(lab, state)
    payload["generatedAt"] = time.time()
    write_json(lab / "identity.json", payload)


def write_properties(path: Path, updates: dict[str, str], remove: Iterable[str] = ()) -> None:
    """Set ``updates`` in a .properties file, keeping every other line as it is."""
    lines = path.read_text("utf-8").splitlines() if path.exists() else []
    pending = dict(updates)
    dropped = set(remove)
    written: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped[0] in "#!" or "=" not in stripped:
            written.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in dropped:
            continue
        written.append(f"{key}={pending.pop(key)}" if key in pending else line)
    written.extend(f"{key}={value}" for key, value in pending.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(written) + "\n", "utf-8", newline="\n")


# --------------------------------------------------------------------------- java


def resolve_java(cli_value: str, recorded: str, required: bool = True) -> str:
    """--java PATH, else $MC_AGENT_JAVA, else the java recorded at provision
    time, else ``java`` from PATH. Nothing machine-specific is ever assumed.
    Provisioning may leave it unset (``required=False``); starting may not."""
    for candidate, origin in (
        (cli_value, "--java"),
        (os.environ.get("MC_AGENT_JAVA", ""), "MC_AGENT_JAVA"),
        (recorded, "lab.json"),
    ):
        candidate = (candidate or "").strip().strip('"')
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
        if os.sep not in candidate and "/" not in candidate:
            found = shutil.which(candidate)
            if found:
                return found
        if origin == "--java" and required:
            die(f"--java does not name a file: {candidate}")
        say(f"note: {origin} does not name a java that exists ({candidate})")
    found = shutil.which("java")
    if found:
        return found
    if required:
        die("no java found: pass --java PATH, set MC_AGENT_JAVA, or put java on PATH")
    return ""


def java_runtime_version(java: str) -> str:
    """First line of ``java -version`` - the version that will load mods."""
    if not java:
        return ""
    try:
        result = subprocess.run(
            [java, "-version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"(unavailable: {error})"
    output = (result.stderr or result.stdout).strip()
    return next((line.strip() for line in output.splitlines() if line.strip()), "")


def jdk_home_from_candidate(candidate: Path) -> Path | None:
    """A JDK home for a ``--jdk`` path that names either the home or javac."""
    candidate = candidate.expanduser()
    javac_name = "javac.exe" if os.name == "nt" else "javac"
    if candidate.is_file() and candidate.name.lower() == javac_name:
        return candidate.parent.parent
    if (candidate / "bin" / javac_name).is_file():
        return candidate
    return None


def resolve_jdk(cli_value: str, recorded: str = "") -> tuple[str, str, str]:
    """Best-effort JDK for building mods: (home, javac version, javac sha256).

    Building is optional, so nothing here is fatal: an empty home means the
    machine has no javac next to its java and none on PATH.
    """
    candidates: list[Path] = []
    for candidate in (cli_value, os.environ.get("JAVA_HOME", ""), recorded):
        candidate = (candidate or "").strip().strip('"')
        if candidate:
            candidates.append(Path(candidate))
    javac_name = "javac.exe" if os.name == "nt" else "javac"
    found = shutil.which("javac")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        home = jdk_home_from_candidate(candidate)
        if home is None:
            continue
        javac = home / "bin" / javac_name
        version = ""
        try:
            result = subprocess.run(
                [str(javac), "-version"], capture_output=True, text=True, timeout=30
            )
            version = (result.stdout or result.stderr).strip().splitlines()[0]
        except (OSError, subprocess.SubprocessError) as error:
            version = f"(unavailable: {error})"
        return str(home), version, sha256_file(javac)
    return "", "", ""


# --------------------------------------------------------------------------- processes

_WAIT_TIMEOUT = 0x102
_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WINDOWS_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def _kernel32():
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_process_info(pid: int) -> dict[str, Any] | None:
    """{"exe": ..., "created": unix seconds} for a live pid, else None."""
    import ctypes
    from ctypes import wintypes

    kernel32 = _kernel32()
    access = _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        return None
    try:
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        exe = ""
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            exe = buffer.value
        return {"exe": exe, "created": ticks / 10_000_000 - _WINDOWS_EPOCH_OFFSET}
    finally:
        kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    """Is this pid running? Never signals it: os.kill(pid, 0) on Windows would
    call TerminateProcess, which is how a stop tool kills the wrong process."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_info(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_matches(pid: int, started_at: float) -> bool:
    """Guard against a recycled pid before we ever terminate anything: the
    process must be a JVM that was born when we spawned it."""
    if not process_alive(pid):
        return False
    if os.name == "nt":
        info = _windows_process_info(pid)
        if info is None:
            return False
        if Path(info["exe"]).name.lower() not in ("java.exe", "javaw.exe", "java"):
            return False
        if started_at and abs(info["created"] - started_at) > 120:
            return False
    return True


def terminate(pid: int) -> None:
    if os.name == "nt":
        import ctypes

        kernel32 = _kernel32()
        handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _SYNCHRONIZE, False, pid)
        if not handle:
            return
        try:
            kernel32.TerminateProcess(handle, 1)
            kernel32.WaitForSingleObject(handle, 10_000)
        finally:
            kernel32.CloseHandle(handle)
        return
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def spawn_detached(argv: list[str], cwd: Path, log: Path) -> int:
    """Start a process that outlives us, with its own invisible console."""
    creationflags = 0
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        # no window, own process group: the server outlives this command and
        # never opens a console or a Swing window nobody can close.
        creationflags = 0x08000000 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    with open(log, "ab", buffering=0) as handle:
        process = subprocess.Popen(  # noqa: S603 - argv is built here, never a shell string
            argv,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
            **kwargs,
        )
        handle.write(f"\n{START_MARKER} {iso_now()} pid={process.pid}\n".encode("utf-8"))
    return process.pid


def wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.5)
    return not process_alive(pid)


# --------------------------------------------------------------------------- rcon


class RconError(RuntimeError):
    pass


class RconClosed(RconError):
    """The server hung up. At a packet boundary that is simply the end of the
    answer: ``stop`` closes the console before it finishes replying."""


class Rcon:
    """The Minecraft RCON protocol, in twenty lines of struct.

    Packet: int32 length, int32 id, int32 type, payload, two NUL bytes; the
    length counts id+type+payload+NULs. Type 3 logs in, type 2 runs a command,
    and a long answer arrives as several packets, so the reader keeps going
    until the socket goes quiet.
    """

    TYPE_RESPONSE = 0
    TYPE_COMMAND = 2
    TYPE_AUTH = 3

    def __init__(self, host: str, port: int, password: str, timeout: float = 15.0) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def __enter__(self) -> "Rcon":
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def connect(self) -> None:
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as error:
            raise RconError(f"cannot reach the lab console on {self.host}:{self.port}: {error}") from error
        self._auth()

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    # -- wire ------------------------------------------------------------------

    def _send(self, request_id: int, packet_type: int, payload: str) -> None:
        if self.sock is None:
            raise RconError("not connected")
        body = struct.pack("<ii", request_id, packet_type) + payload.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(body)) + body)

    def _read_exact(self, count: int, idle: float | None) -> bytes:
        assert self.sock is not None
        chunks: list[bytes] = []
        remaining = count
        while remaining > 0:
            if idle is not None:
                self.sock.settimeout(idle)
            try:
                chunk = self.sock.recv(remaining)
            except socket.timeout as error:
                if not chunks:
                    raise TimeoutError(str(error)) from error
                raise RconError("the console sent a truncated packet") from error
            if not chunk:
                if not chunks:
                    raise RconClosed("the console closed the connection")
                raise RconError("the console sent a truncated packet")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_packet(self, idle: float | None) -> tuple[int, int, str] | None:
        """One packet, or None if nothing arrived before ``idle`` expired."""
        try:
            header = self._read_exact(4, idle)
        except TimeoutError:
            return None
        except RconClosed:
            return None
        (length,) = struct.unpack("<i", header)
        if length < 10 or length > 1 << 22:
            raise RconError(f"the console sent a nonsense packet length ({length})")
        body = self._read_exact(length, self.timeout)
        request_id, packet_type = struct.unpack("<ii", body[:8])
        payload = body[8 : length - 2]
        return request_id, packet_type, payload.decode("utf-8", "replace")

    def _auth(self) -> None:
        request_id = 1
        self._send(request_id, self.TYPE_AUTH, self.password)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            packet = self._read_packet(idle=self.timeout)
            if packet is None:
                raise RconError("the console did not answer the login")
            packet_id, _packet_type, _payload = packet
            if packet_id == -1:
                raise RconError("the console refused the password in rcon.json")
            if packet_id == request_id:
                return
        raise RconError("the console never confirmed the login")

    def command(self, command: str, idle: float = 0.5) -> str:
        request_id = 2
        self._send(request_id, self.TYPE_COMMAND, command)
        parts: list[str] = []
        while True:
            packet = self._read_packet(idle=idle)
            if packet is None:
                break
            _packet_id, packet_type, payload = packet
            # The server answers an exec packet with RESPONSE_VALUE (0), even
            # though some other servers use COMMAND (2) - take either, and keep
            # reading, because a long answer arrives as several packets.
            if packet_type in (self.TYPE_RESPONSE, self.TYPE_COMMAND) and payload:
                parts.append(payload)
            if packet_type not in (self.TYPE_COMMAND, self.TYPE_RESPONSE):
                break
        return "\n".join(parts)

_SECTION_CODE = re.compile("\u00a7.")


def clean_console_text(text: str) -> str:
    """Minecraft colours the feedback it sends to a console; RCON should not."""
    return _SECTION_CODE.sub("", text).replace("\r", "").strip("\n")


# --------------------------------------------------------------------------- logs


def read_log(log: Path) -> str:
    if not log.exists():
        return ""
    return log.read_bytes().decode("utf-8", "replace")


def log_ready(log: Path) -> bool:
    """Did the current run reach the server-ready line, after its start marker?"""
    text = read_log(log)
    start = text.rfind(START_MARKER)
    return DONE_MARKER in text[start if start >= 0 else 0 :]


def tail_log(log: Path, count: int) -> list[str]:
    lines = read_log(log).splitlines()
    return lines[-count:] if count > 0 else lines


def log_failure_lines(log: Path) -> list[str]:
    """The lines of the current run that explain a failed start."""
    text = read_log(log)
    start = text.rfind(START_MARKER)
    section = text[start:] if start >= 0 else text
    markers = ("exception", "error", "failed", "missing", "requires", "incompatible", "unable")
    return [
        f"!! {line.strip()}"
        for line in section.splitlines()
        if any(marker in line.lower() for marker in markers)
    ][:12]


# --------------------------------------------------------------------------- commands


def cmd_provision(args: argparse.Namespace) -> int:
    if args.world and args.void:
        die("--world and --void are two different labs: pick one")
    lab = lab_path(args.name)
    previous = read_json(lab / "lab.json") or {}
    if lab.exists() and not previous and not args.force:
        die(f"{rel(lab)} exists but holds no lab.json - use --force to adopt that directory")
    if previous:
        say(f"updating lab {args.name} ({rel(lab)})")
    running, running_pid = lab_running(lab)
    if running and (args.mod_jar or args.mod_url or args.fabric_api or args.carpet or args.test_mod):
        die(
            f"lab {args.name} is running (pid {running_pid}); a live server keeps its loaded jars."
            " stop it first, then provision, then start"
        )

    mc = args.mc or previous.get("minecraft") or DEFAULT_MC
    memory = args.memory or previous.get("memory") or DEFAULT_MEMORY

    # Ports first: a port mistake must not leave a half-updated deployment.
    used_ports = ports_of_other_labs(args.name)
    rcon_port = choose_port(
        int(args.rcon_port or previous.get("rconPort") or 0),
        used_ports,
        "rcon",
        allow_open=not args.rcon_port and bool(previous.get("rconPort")),
    )
    server_port = choose_port(
        int(args.server_port or previous.get("serverPort") or 0),
        used_ports,
        "game",
        allow_open=not args.server_port and bool(previous.get("serverPort")),
    )
    vantage_port = choose_port(
        int(args.vantage_port or previous.get("serverVantagePort") or 0),
        used_ports,
        "server-vantage",
        allow_open=not args.vantage_port and bool(previous.get("serverVantagePort")),
    )
    bridge_port = choose_port(
        int(args.bridge_port or previous.get("bridgeApiPort") or 0),
        used_ports,
        "bridge API",
        allow_open=not args.bridge_port and bool(previous.get("bridgeApiPort")),
    )

    say(f"resolving Fabric for Minecraft {mc}")
    loader = args.loader or previous.get("loader") or fabric_loader_version(mc)
    installer = previous.get("installer") or fabric_installer_version()
    say(f"  fabric-loader {loader}, fabric-installer {installer}")

    launcher_name = f"fabric-server-mc.{mc}-loader.{loader}-launcher.{installer}.jar"
    launcher_url = f"{FABRIC_META}/versions/loader/{mc}/{loader}/{installer}/server/jar"
    launcher = download(launcher_url, CACHE / launcher_name, "fabric launcher")
    launcher_stamp = deploy_file(launcher, lab / launcher_name, "fabric launcher")
    check_launcher_metadata(lab / launcher_name, mc, loader)
    for stale in lab.glob("fabric-server-mc.*.jar"):
        if stale.name != launcher_name:
            stale.unlink()  # the lab moved to another Minecraft version
    say(f"  launcher: {launcher_stamp['action']} sha256 {launcher_stamp['sha256'][:12]}")

    # Mods: install every requested jar, then record what is actually on disk.
    # deploy_file compares content hashes, so a rebuilt jar that happens to
    # keep its size is replaced instead of skipped.
    mods_dir = lab / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    wanted: list[tuple[Path, str, str, bool]] = []
    if args.fabric_api:
        path, version = modrinth_download("fabric-api", mc)
        wanted.append((path, "modrinth", version, False))
    if args.carpet:
        path, version = modrinth_download("carpet", mc)
        wanted.append((path, "modrinth", version, False))
    for path in args.mod_jar:
        source = Path(path).expanduser()
        if not source.is_file():
            die(f"--mod-jar {path} is not a file")
        wanted.append((source.resolve(), "local", "", False))
    for url in args.mod_url:
        name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).name) or "mod.jar"
        wanted.append((download(url, CACHE / "mods" / name, "mod"), f"url:{url}", "", False))
    required = set(args.require_mod or [])
    for path in args.test_mod:
        source = Path(path).expanduser()
        if not source.is_file():
            die(f"--test-mod {path} is not a file")
        source = source.resolve()
        wanted.append((source, "test-mod", "", True))
        required.add(source.name)

    prior_records = {record["name"]: record for record in mod_records(previous)}
    wanted_meta: dict[str, dict[str, Any]] = {}
    actions: list[str] = []
    for source, origin, version, is_required in wanted:
        stamp = deploy_file(source, mods_dir / source.name, f"mod {source.name}")
        actions.append(f"{source.name} ({stamp['action']}, {stamp['sha256'][:12]})")
        prior = prior_records.get(source.name) or {}
        wanted_meta[source.name] = {
            "origin": origin,
            "version": version or prior.get("version") or "",
            "required": bool(is_required or prior.get("required")),
        }
    if actions:
        say(f"  mods: {'; '.join(actions)}")

    mods_state: list[dict[str, Any]] = []
    for jar in sorted(mods_dir.glob("*.jar")):
        prior = prior_records.get(jar.name) or {}
        meta = wanted_meta.get(jar.name) or {}
        stamp = file_record(jar)
        record = {
            "name": jar.name,
            "sha256": stamp["sha256"],
            "size": stamp["size"],
            "origin": meta.get("origin") or prior.get("origin") or "present-at-provision",
            "version": meta.get("version") or prior.get("version") or "",
            "required": bool(meta.get("required") or prior.get("required") or jar.name in required),
            "deployedAt": prior.get("deployedAt") or time.time(),
        }
        mods_state.append(record)
        if record["required"]:
            required.add(jar.name)

    rcon_state = read_json(lab / "rcon.json") or {}
    password = str(rcon_state.get("password") or secrets.token_hex(16))

    properties = {
        "online-mode": "false",
        "level-name": "world",
        "spawn-protection": "0",
        "max-players": "8",
        "view-distance": "10",
        "sync-chunk-writes": "false",
        "enable-rcon": "true",
        "rcon.port": str(rcon_port),
        "rcon.password": password,
        "broadcast-rcon-to-ops": "false",
        "server-port": str(server_port),
        "query.port": str(server_port),
        "motd": f"mc-agent lab {args.name}",
        # A lab usually has nobody logged in; without this the server pauses
        # itself (and the world stops ticking) once it has been empty for a
        # while, which silently breaks "run for N ticks and look" experiments.
        "pause-when-empty-seconds": "0",
    }
    if args.void:
        properties["level-type"] = VOID_LEVEL_TYPE
        properties["generator-settings"] = VOID_GENERATOR_SETTINGS
    write_properties(
        lab / "server.properties",
        properties,
        remove=() if args.void else ("level-type", "generator-settings"),
    )
    say(f"  server.properties: rcon {rcon_port}, game {server_port}, level {properties['level-name']}")

    (lab / "eula.txt").write_text(
        "# by changing the setting below to TRUE you are indicating your agreement"
        " to the Minecraft EULA (https://aka.ms/MinecraftEULA).\neula=true\n",
        "utf-8",
        newline="\n",
    )
    write_json(lab / "rcon.json", {"host": RCON_HOST, "port": rcon_port, "password": password})

    world_kind = "void" if args.void else "copied save" if args.world else "new world"
    world_note = {
        "void": "flat, every layer air, biome minecraft:the_void",
        "copied save": f"copy of {Path(args.world).expanduser().resolve()}" if args.world else "",
        "new world": "vanilla generation, as if hand-started",
    }[world_kind]
    if args.world:
        copy_world(Path(args.world).expanduser().resolve(), lab / "world")
    elif previous.get("world") and previous["world"] != world_kind:
        say(
            f"  world: this lab was {previous['world']} before; the level in"
            f" {rel(lab / 'world')} is untouched, so delete it to regenerate"
        )

    java = resolve_java(args.java, previous.get("java", ""), required=False)
    java_sha = sha256_file(Path(java)) if java and Path(java).is_file() else ""
    jdk_home, jdk_version, jdk_sha = resolve_jdk(args.jdk, previous.get("jdk", ""))
    if not jdk_home and java and Path(java).is_file():
        jdk_home, jdk_version, jdk_sha = resolve_jdk(str(Path(java).parent.parent), "")

    server_dir = (
        Path(args.server_dir).expanduser()
        if args.server_dir
        else Path(previous.get("serverDir") or (lab / "mc-agent-server"))
    ).resolve()
    audit_dir = (
        Path(args.audit_dir).expanduser()
        if args.audit_dir
        else Path(previous.get("auditDir") or (lab / "audit"))
    ).resolve()
    server_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)

    instance_id = previous.get("instanceId") or uuid.uuid4().hex
    state = {
        "name": args.name,
        "instanceId": instance_id,
        "minecraft": mc,
        "loader": loader,
        "installer": installer,
        "launcher": launcher_name,
        "launcherSha256": launcher_stamp["sha256"],
        "memory": memory,
        "java": java,
        "javaSha256": java_sha,
        "javaVersion": java_runtime_version(java),
        "jdk": jdk_home,
        "jdkVersion": jdk_version,
        "jdkSha256": jdk_sha,
        "serverPort": server_port,
        "rconPort": rcon_port,
        "serverVantagePort": vantage_port,
        "bridgeApiPort": bridge_port,
        "serverDir": str(server_dir),
        "portFile": str(server_dir / "port.txt"),
        "auditDir": str(audit_dir),
        "worldDir": str((lab / "world").resolve()),
        "worldSource": (
            str(Path(args.world).expanduser().resolve())
            if args.world
            else previous.get("worldSource") or ""
        ),
        "world": world_kind,
        "mods": mods_state,
        "requiredMods": sorted(required),
        "extraJvmArgs": list(previous.get("extraJvmArgs") or []) + list(args.jvm_arg or []),
        "provisionedAt": previous.get("provisionedAt") or time.time(),
        "updatedAt": time.time(),
        "restartPending": bool(running),
    }
    write_json(lab / "lab.json", state)
    (lab / "logs").mkdir(parents=True, exist_ok=True)

    say(f"provisioned {rel(lab)}")
    say(f"  minecraft {mc}, loader {loader}, memory {memory}")
    say(f"  java {java or '(none recorded - pass --java, set MC_AGENT_JAVA, or have java on PATH)'}")
    if java:
        say(f"  java version {state['javaVersion']}")
    say(
        f"  jdk {jdk_home or '(no javac found - pass --jdk to build mods)'}"
        + (f" ({jdk_version})" if jdk_version else "")
    )
    say(f"  identity {instance_id}")
    say(f"  world {world_kind} ({world_note})")
    say(f"  world dir {state['worldDir']}")
    say(f"  audit dir {state['auditDir']}")
    say(f"  console 127.0.0.1:{rcon_port} (password in {rel(lab / 'rcon.json')})")
    say(
        f"  game 127.0.0.1:{server_port}, server-vantage base {vantage_port}"
        f" ({rel(server_dir / 'port.txt')}), bridge API {bridge_port}"
    )
    if running:
        say("  note: the lab is running; restart it to load this deployment (restartPending=true)")
    say(f"start it with: python tools/lab_server.py start --name {args.name}")
    write_identity(lab, state)
    return 0


def copy_if_different(source: Path, target: Path) -> Path:
    """Backwards-compatible name for ``deploy_file``: content hash, atomic replace."""
    deploy_file(source, target, target.name)
    return target


def check_launcher_metadata(jar: Path, mc: str, loader: str) -> None:
    """The Fabric launcher jar carries install.properties; if it disagrees with
    the file name, the download and the request were not the same thing."""
    try:
        with zipfile.ZipFile(jar) as archive:
            embedded = archive.read("install.properties").decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        die(f"{rel(jar)} is not a Fabric server launcher ({error})")
    fields = dict(
        line.split("=", 1) for line in embedded.splitlines() if "=" in line
    )
    if fields.get("game-version") != mc or fields.get("fabric-loader-version") != loader:
        die(
            f"{rel(jar)} is for game {fields.get('game-version')}"
            f"/loader {fields.get('fabric-loader-version')}, not {mc}/{loader}"
        )


def copy_world(source: Path, target: Path) -> None:
    if not (source / "level.dat").is_file():
        die(f"{source} has no level.dat - that is not a save directory")
    if target.exists() and any(target.iterdir()):
        say(f"  world: {rel(target)} already holds a level; leaving it alone")
        return
    target.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(*WORLD_COPY_SKIP)
    copied = 0
    for entry in sorted(source.iterdir()):
        if entry.name in WORLD_COPY_SKIP:
            continue
        destination = target / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination, ignore=ignore, dirs_exist_ok=True)
        else:
            shutil.copy2(entry, destination)
        copied += 1
    say(
        f"  world: copied {copied} entries from {source} to {rel(target)}"
        f" (skipped {', '.join(WORLD_COPY_SKIP)})"
    )


def cmd_start(args: argparse.Namespace) -> int:
    lab, state = load_lab(args.name)
    launcher = lab / state["launcher"]
    if not launcher.is_file():
        die(f"{rel(launcher)} is missing - re-run provision")
    if not (lab / "server.properties").is_file() or not (lab / "rcon.json").is_file():
        die(f"{rel(lab)} is not provisioned completely - re-run provision")
    if not (lab / "eula.txt").is_file():
        (lab / "eula.txt").write_text("eula=true\n", "utf-8", newline="\n")

    running, running_pid = lab_running(lab)
    if running:
        die(f"lab {args.name} is already running (pid {running_pid}); stop it first")

    actual_mods, problems = check_deployment(lab, state, allow_drift=args.allow_mod_drift)
    if problems:
        joined = "\n  ".join(problems)
        die(
            f"lab {args.name} refused to start: the deployed mods do not match the provisioned lab.\n"
            f"  {joined}\n"
            "re-run provision with the intended jars, or pass --allow-mod-drift to run anyway"
        )

    java = resolve_java(args.java, state.get("java", ""))
    memory = args.memory or state.get("memory") or DEFAULT_MEMORY
    extra = list(state.get("extraJvmArgs") or []) + list(args.jvm_arg or [])
    identity = [
        ("mcagent.serverDir", state.get("serverDir")),
        ("mcagent.serverPort", state.get("serverVantagePort")),
        ("mcagent.labName", state.get("name") or args.name),
        ("mcagent.labInstance", state.get("instanceId")),
        ("mcagent.auditDir", state.get("auditDir")),
        ("mcagent.worldDir", state.get("worldDir")),
    ]
    identity_args = [f"-D{key}={value}" for key, value in identity if value not in (None, "")]
    argv = [
        java,
        f"-Xmx{memory}",
        "-Dfile.encoding=UTF-8",
        "-Dstdout.encoding=UTF-8",
        "-Dstderr.encoding=UTF-8",
        *extra,
        *identity_args,
        "-jar",
        launcher.name,
        "nogui",
    ]
    log = lab / "logs" / "console.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    port_file = Path(state.get("portFile") or (lab / "mc-agent-server" / "port.txt"))
    if port_file.exists():
        try:
            port_file.unlink()
        except OSError as error:
            say(f"warning: cannot clear stale {rel(port_file)} ({error})")
        else:
            say(f"  cleared stale {rel(port_file)} - the server-vantage mod rewrites it when it binds")
    first_start = not (lab / ".fabric").exists()
    say(f"starting lab {args.name} in {rel(lab)}")
    say(f"  java {java}")
    say(f"  {' '.join(argv[1:])}")
    if first_start:
        say("  first start: the Fabric launcher downloads the Minecraft server and its libraries")
    started_at = time.time()
    pid = spawn_detached(argv, lab, log)
    write_json(
        lab / "run.json",
        {
            "pid": pid,
            "startedAt": started_at,
            "java": java,
            "memory": memory,
            "argv": argv,
            "log": rel(log),
            "instanceId": state.get("instanceId"),
            "worldDir": state.get("worldDir"),
            "auditDir": state.get("auditDir"),
            "serverDir": state.get("serverDir"),
            "portFile": str(port_file),
            "serverPort": state.get("serverPort"),
            "rconPort": state.get("rconPort"),
            "serverVantagePort": state.get("serverVantagePort"),
            "bridgeApiPort": state.get("bridgeApiPort"),
            "modsAtStart": actual_mods,
            "requiredMods": list(state.get("requiredMods") or []),
            "modDriftAllowed": bool(args.allow_mod_drift),
        },
    )
    state["restartPending"] = False
    state["lastStartedAt"] = started_at
    write_json(lab / "lab.json", state)
    say(f"  pid {pid}, log {rel(log)}")

    if args.wait <= 0:
        say(f"wait for it with: python tools/lab_server.py status --name {args.name} --tail 5")
        return 0
    say(f"waiting up to {args.wait:.0f}s for the server to say Done")
    deadline = time.monotonic() + args.wait
    while time.monotonic() < deadline:
        if log_ready(log):
            ready_line = next(
                (line for line in reversed(tail_log(log, 200)) if DONE_MARKER in line), ""
            )
            say(f"ready in {time.time() - started_at:.1f}s: {clean_console_text(ready_line)}")
            actual_port = read_port_file(port_file)
            if not actual_port and state.get("serverVantagePort"):
                # The server-vantage mod binds just after Done; give it a moment.
                port_deadline = time.monotonic() + 15
                while not actual_port and time.monotonic() < port_deadline:
                    time.sleep(0.5)
                    actual_port = read_port_file(port_file)
            if actual_port:
                say(f"  server-vantage: 127.0.0.1:{actual_port} ({rel(port_file)})")
            elif state.get("serverVantagePort"):
                say(f"  note: {rel(port_file)} has not appeared; the server-vantage mod writes it when it binds")
            write_identity(lab, state)
            return 0
        if not process_matches(pid, started_at):
            say("the server exited before it was ready; last log lines:")
            for line in tail_log(log, 15):
                say(f"  {line}")
            for line in log_failure_lines(log):
                say(f"  {line}")
            return 1
        time.sleep(1.0)
    say(f"still not ready after {args.wait:.0f}s; last log lines:")
    for line in tail_log(log, 10):
        say(f"  {line}")
    return 1


def cmd_stop(args: argparse.Namespace) -> int:
    lab, _state = load_lab(args.name)
    run = read_json(lab / "run.json") or {}
    pid = int(run.get("pid") or 0)
    if not pid or not process_matches(pid, float(run.get("startedAt") or 0)):
        say(f"lab {args.name} is not running")
        return 0

    graceful = False
    if not args.force:
        try:
            with open_console(lab) as rcon:
                rcon.command("stop", idle=2.0)
            graceful = True
        except RconError as error:
            say(f"warning: {error}; terminating the process instead")
            wait_for_exit(pid, 5)
    if graceful:
        if wait_for_exit(pid, args.timeout):
            say(f"lab {args.name} saved its world and exited (pid {pid})")
        else:
            say(f"still running after {args.timeout:.0f}s; terminating pid {pid}")
            terminate(pid)
            wait_for_exit(pid, 15)
    else:
        if not process_matches(pid, float(run.get("startedAt") or 0)):
            say(f"lab {args.name} exited (pid {pid})")
        else:
            terminate(pid)
            wait_for_exit(pid, 15)
            say(f"terminated pid {pid}")
    run["stoppedAt"] = time.time()
    write_json(lab / "run.json", run)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    lab, state = load_lab(args.name)
    run = read_json(lab / "run.json") or {}
    pid = int(run.get("pid") or 0)
    alive = bool(pid) and process_matches(pid, float(run.get("startedAt") or 0))
    log = lab / "logs" / "console.log"
    ready = alive and log_ready(log)
    rcon_state = read_json(lab / "rcon.json") or {}
    port_file = Path(state.get("portFile") or (lab / "mc-agent-server" / "port.txt"))
    vantage_actual = read_port_file(port_file)
    uptime = time.time() - float(run.get("startedAt") or 0) if alive else 0.0
    info = {
        "name": args.name,
        "instanceId": state.get("instanceId"),
        "lab": str(lab),
        "minecraft": state.get("minecraft"),
        "loader": state.get("loader"),
        "java": run.get("java") or state.get("java"),
        "memory": run.get("memory") or state.get("memory"),
        "running": alive,
        "pid": pid if alive else None,
        "ready": ready,
        "uptimeSeconds": round(uptime, 1) if alive else None,
        "serverPort": state.get("serverPort"),
        "rcon": f"{rcon_state.get('host', RCON_HOST)}:{rcon_state.get('port')}",
        "serverVantagePort": state.get("serverVantagePort"),
        "serverVantagePortFile": str(port_file),
        "serverVantagePortActual": vantage_actual,
        "bridgeApiPort": state.get("bridgeApiPort"),
        "world": state.get("world"),
        "worldDir": state.get("worldDir"),
        "auditDir": state.get("auditDir"),
        "mods": mod_names(state),
        "requiredMods": list(state.get("requiredMods") or []),
        "restartPending": bool(state.get("restartPending")),
        "consoleLog": str(log),
    }
    if args.json:
        say(json.dumps(info, indent=2))
        return 0
    say(f"lab {args.name}")
    say(f"  directory   {rel(lab)}")
    if info["instanceId"]:
        say(f"  identity    {info['instanceId']}")
    say(f"  minecraft   {info['minecraft']} (fabric-loader {info['loader']})")
    say(f"  java        {info['java']} -Xmx{info['memory']}")
    if alive:
        state_text = f"running (pid {pid}"
        state_text += f", ready, up {format_duration(uptime)})" if ready else ", still starting)"
    else:
        state_text = "stopped (restartPending)" if info["restartPending"] else "stopped"
    say(f"  state       {state_text}")
    say(f"  ports       game {info['serverPort']}, rcon {info['rcon']}")
    say(
        f"  vantage     base {info['serverVantagePort']}, active"
        f" {vantage_actual or 'no port.txt'} ({rel(port_file)})"
    )
    say(f"  bridge API  {info['bridgeApiPort']}")
    say(f"  world       {info['world']} at {info['worldDir']}")
    say(f"  audit       {info['auditDir']}")
    say(f"  mods        {', '.join(info['mods']) if info['mods'] else 'none'}")
    if info["requiredMods"]:
        say(f"  required    {', '.join(info['requiredMods'])}")
    say(f"  log         {rel(log)} ({len(tail_log(log, 0))} lines)")
    if args.tail:
        say(f"  last {args.tail} log lines:")
        for line in tail_log(log, args.tail):
            say(f"    {line}")
    return 0


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60:02d}s"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def open_console(lab: Path) -> Rcon:
    rcon_state = read_json(lab / "rcon.json") or {}
    if not rcon_state.get("port"):
        die(f"{rel(lab / 'rcon.json')} is missing - re-run provision")
    return Rcon(
        str(rcon_state.get("host") or RCON_HOST),
        int(rcon_state["port"]),
        str(rcon_state.get("password") or ""),
    )


def cmd_exec(args: argparse.Namespace) -> int:
    lab, _state = load_lab(args.name)
    run = read_json(lab / "run.json") or {}
    pid = int(run.get("pid") or 0)
    if not pid or not process_matches(pid, float(run.get("startedAt") or 0)):
        die(f"lab {args.name} is not running - start it first (status shows why)")
    command = args.command.strip()
    command = command[1:].strip() if command.startswith("/") else command
    if not command:
        die("no command given")
    try:
        with open_console(lab) as rcon:
            output = clean_console_text(rcon.command(command))
    except RconError as error:
        die(str(error), 1)
    if args.json:
        say(json.dumps({"lab": args.name, "command": command, "output": output}, indent=2))
    elif output:
        say(output)
    else:
        say(f"(no output from {command!r} - the console said nothing)")
    return 0


def cmd_identity(args: argparse.Namespace) -> int:
    lab, state = load_lab(args.name)
    payload = identity_payload(lab, state)
    if args.json:
        say(json.dumps(payload, indent=2))
        return 1 if payload["deploymentProblems"] else 0
    say(f"lab {payload['lab']}")
    say(f"  identity      {payload['instanceId']}")
    say(f"  world dir     {payload['worldDir']}")
    say(f"  audit dir     {payload['auditDir']}")
    say(f"  server dir    {payload['serverDir']}")
    say(
        f"  server-vantage base {payload['serverVantagePort']}, port.txt"
        f" {payload['serverVantagePortActual'] or 'not written'}"
    )
    say(f"  bridge API    {payload['bridgeApiPort']}")
    say(f"  game/rcon     {payload['serverPort']}/{payload['rconPort']}")
    state_text = "running" if payload["running"] else "stopped"
    if payload["pid"]:
        state_text += f", pid {payload['pid']}"
    say(f"  state         {state_text}")
    say(f"  required      {', '.join(payload['requiredMods']) or 'none'}")
    for record in payload["mods"]:
        drift = " (drift)" if record.get("drift") else ""
        say(f"  mod           {record['name']} sha256 {record['sha256'][:12]}{drift}")
    if payload["deploymentProblems"]:
        say("  deployment problems:")
        for problem in payload["deploymentProblems"]:
            say(f"    {problem}")
        return 1
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    lab, state = load_lab(args.name)
    actual, problems = check_deployment(lab, state)
    world_dir = Path(state.get("worldDir") or (lab / "world"))
    if not (world_dir / "level.dat").is_file():
        problems.append(f"world {rel(world_dir)} has no level.dat - start the lab once so it generates its world")
    if args.require_vantage:
        running, _pid = lab_running(lab)
        if not running:
            problems.append("lab is not running, so there is no live server-vantage to verify")
        port_file = Path(state.get("portFile") or (lab / "mc-agent-server" / "port.txt"))
        actual_port = read_port_file(port_file)
        expected = state.get("serverVantagePort")
        if actual_port is None:
            problems.append(f"server-vantage port file {rel(port_file)} is missing or empty")
        else:
            try:
                bound = int(actual_port)
            except ValueError:
                problems.append(f"server-vantage port file {rel(port_file)} does not hold a port ({actual_port!r})")
            else:
                if not port_is_open(bound):
                    problems.append(f"nothing is listening on the server-vantage port {bound}")
                if expected and bound != int(expected):
                    problems.append(f"server-vantage port file says {bound}, expected {expected}")
    payload = {"lab": args.name, "ok": not problems, "problems": problems, "mods": actual}
    if args.json:
        say(json.dumps(payload, indent=2))
        return 0 if not problems else 1
    if problems:
        say(f"lab {args.name}: NOT VERIFIED")
        for problem in problems:
            say(f"  {problem}")
        return 1
    say(
        f"lab {args.name}: verified - {len(actual)} mods match the provisioned bytes,"
        f" world {state.get('worldDir')}"
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    if not LABS.is_dir():
        say("no labs yet - see docs/lab-server.md")
        return 0
    rows: list[list[str]] = []
    details: list[dict[str, Any]] = []
    for lab in sorted(LABS.iterdir()):
        if not lab.is_dir() or lab.name.startswith("_"):
            continue
        state = read_json(lab / "lab.json") or {}
        if not state:
            continue
        run = read_json(lab / "run.json") or {}
        pid = int(run.get("pid") or 0)
        alive = bool(pid) and process_matches(pid, float(run.get("startedAt") or 0))
        if not alive:
            state_text = "stopped"
        elif log_ready(lab / "logs" / "console.log"):
            state_text = "ready"
        else:
            state_text = "starting"
        rcon_state = read_json(lab / "rcon.json") or {}
        port_file = Path(state.get("portFile") or (lab / "mc-agent-server" / "port.txt"))
        details.append(
            {
                "lab": lab.name,
                "instanceId": state.get("instanceId"),
                "minecraft": state.get("minecraft"),
                "state": state_text,
                "pid": pid if alive else None,
                "rcon": rcon_state.get("port"),
                "serverPort": state.get("serverPort"),
                "serverVantagePort": state.get("serverVantagePort"),
                "serverVantagePortActual": read_port_file(port_file),
                "bridgeApiPort": state.get("bridgeApiPort"),
                "world": state.get("world"),
                "mods": mod_names(state),
            }
        )
        rows.append(
            [
                lab.name,
                str(state.get("minecraft") or "?"),
                state_text,
                str(pid) if alive else "-",
                str(rcon_state.get("port") or "?"),
                str(state.get("serverVantagePort") or "?"),
                str(state.get("bridgeApiPort") or "?"),
                str(state.get("world") or "?"),
                str(len(mod_names(state))),
            ]
        )
    if not rows:
        say("no labs yet - see docs/lab-server.md")
        return 0
    headers = ["LAB", "MC", "STATE", "PID", "RCON", "VANTAGE", "BRIDGE", "WORLD", "MODS"]
    if args.json:
        say(json.dumps(details, indent=2))
        return 0
    widths = [max(len(row[i]) for row in rows + [headers]) for i in range(len(headers))]
    say("  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)).rstrip())
    for row in rows:
        say("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return 0


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Provision, start and command headless Fabric lab servers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python tools/lab_server.py provision --name smoke --void --fabric-api --carpet\n"
            "  python tools/lab_server.py start --name smoke --wait 300\n"
            '  python tools/lab_server.py exec --name smoke "tick freeze"\n'
        ),
    )
    commands = parser.add_subparsers(dest="subcommand", required=True)

    provision = commands.add_parser("provision", help="create or update a lab directory")
    provision.add_argument("--name", required=True, help="lab name, e.g. smoke")
    provision.add_argument("--mc", default="", help=f"Minecraft version (default {DEFAULT_MC})")
    provision.add_argument("--fabric-api", action="store_true", help="add the Fabric API mod")
    provision.add_argument("--carpet", action="store_true", help="add the Carpet mod")
    provision.add_argument("--mod-jar", action="append", default=[], help="a local jar to copy in")
    provision.add_argument("--mod-url", action="append", default=[], help="a jar URL to fetch")
    provision.add_argument(
        "--test-mod",
        action="append",
        default=[],
        help="a required test-mod jar: copied in and audited as a hard dependency",
    )
    provision.add_argument(
        "--require-mod",
        action="append",
        default=[],
        help="jar name that must be present for an auditable run (repeatable)",
    )
    provision.add_argument("--world", default="", help="an existing save to copy in")
    provision.add_argument("--void", action="store_true", help="a flat world of air in the void biome")
    provision.add_argument("--java", default="", help="java executable to remember for this lab")
    provision.add_argument("--jdk", default="", help="JDK home (or javac) for building mods; remembered and hashed")
    provision.add_argument("--memory", default="", help=f"heap size (default {DEFAULT_MEMORY})")
    provision.add_argument("--loader", default="", help="pin a Fabric loader version")
    provision.add_argument(
        "--jvm-arg", action="append", default=[], help="JVM argument remembered for start (repeatable)"
    )
    provision.add_argument("--server-port", type=int, default=0, help="game TCP port (0 picks a free one)")
    provision.add_argument("--rcon-port", type=int, default=0, help="RCON port (0 picks a free one)")
    provision.add_argument(
        "--vantage-port",
        type=int,
        default=0,
        help="base port for the server-vantage mod, which scans upward 20 (0 picks a free one)",
    )
    provision.add_argument(
        "--bridge-port", type=int, default=0, help="loopback API port of the bridge for this lab (0 picks one)"
    )
    provision.add_argument(
        "--server-dir",
        default="",
        help="where the server-vantage mod writes port.txt (default labs/<name>/mc-agent-server)",
    )
    provision.add_argument(
        "--audit-dir",
        default="",
        help="where mods write audit output (default labs/<name>/audit)",
    )
    provision.add_argument("--force", action="store_true", help="adopt a non-empty directory")
    provision.set_defaults(handler=cmd_provision)

    start = commands.add_parser("start", help="start a lab detached")
    start.add_argument("--name", required=True)
    start.add_argument("--java", default="", help="override the java to use")
    start.add_argument("--memory", default="", help="override the heap size")
    start.add_argument(
        "--jvm-arg", action="append", default=[], help="extra JVM argument for this start (repeatable)"
    )
    start.add_argument(
        "--allow-mod-drift",
        action="store_true",
        help="start even when a deployed mod's bytes differ from what lab.json recorded",
    )
    start.add_argument(
        "--wait",
        type=float,
        default=0.0,
        help="seconds to wait for the server's Done line before returning",
    )
    start.set_defaults(handler=cmd_start)

    stop = commands.add_parser("stop", help="stop a lab (save and quit, then terminate)")
    stop.add_argument("--name", required=True)
    stop.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for a clean exit")
    stop.add_argument("--force", action="store_true", help="terminate instead of asking the console")
    stop.set_defaults(handler=cmd_stop)

    status = commands.add_parser("status", help="what is this lab doing?")
    status.add_argument("--name", required=True)
    status.add_argument("--tail", type=int, default=0, help="also print the last N log lines")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=cmd_status)

    exec_parser = commands.add_parser("exec", help="run one command in the lab console")
    exec_parser.add_argument("--name", required=True)
    exec_parser.add_argument("command", help="the command, with or without a leading slash")
    exec_parser.add_argument("--json", action="store_true")
    exec_parser.set_defaults(handler=cmd_exec)

    list_parser = commands.add_parser("list", help="every lab, and whether it is up")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(handler=cmd_list)

    identity = commands.add_parser("identity", help="this lab's identity, directories and ports")
    identity.add_argument("--name", required=True)
    identity.add_argument("--json", action="store_true")
    identity.set_defaults(handler=cmd_identity)

    verify = commands.add_parser(
        "verify", help="check that deployed mod bytes and the world still match the lab record"
    )
    verify.add_argument("--name", required=True)
    verify.add_argument(
        "--require-vantage",
        action="store_true",
        help="also demand that port.txt matches the recorded server-vantage port",
    )
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(handler=cmd_verify)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
