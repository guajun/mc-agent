"""Minecart ROM fixture: artifact fetch, safe import, deterministic initialization.

This is the test-side library behind ``minecart_rom.py``. It owns four things:

* the immutable map artifact - download, size/SHA-256 check, safe extraction;
* a lab import - hand a verified world copy to ``tools/lab_server.py``;
* the deterministic fixture - a real Carpet fake player facing the machine,
  a frozen world, and a recorded stack of chest minecarts on the rail;
* the ready/validity checks - reload, premature output and failed
  initialization are detected and reported instead of being handed to an agent.

Only the standard library is used. The Minecraft console is spoken over RCON by
importing ``tools/lab_server.py`` (its :class:`Rcon`) so there is exactly one
RCON implementation in the repository.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = REPO_ROOT / "examples" / "minecart-rom"
TOOLS = REPO_ROOT / "tools"
LABS = REPO_ROOT / "labs"
MAP_CACHE = LABS / "_cache" / "maps"

sys.path.insert(0, str(TOOLS))
import lab_server  # noqa: E402  (path set up above on purpose)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interface_mod  # noqa: E402  (same directory)

USER_AGENT = "mc-agent-minecart-rom/1.0 (https://github.com/guajun/mc-agent)"

# An artifact bigger than this is not the map; refuse to unpack it. The shipped
# base world is ~12 MB uncompressed, a couple of MB zipped.
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000

RECORD_FORMAT = "mc-agent/minecart-rom-record@1"

CART_MARK = "Minecart with Chest has the following entity data: "

# The machine as the world ships it. The authoritative copy lives in
# fixture-spec.json; this fallback keeps older specs working.
DEFAULT_MACHINE_CHECKS: list[dict[str, Any]] = [
    {"pos": [11, -54, -23], "block": "minecraft:note_block[instrument=harp,powered=false]"},
    {"pos": [11, -54, -22], "block": "minecraft:sticky_piston[extended=false,facing=up]"},
    {"pos": [11, -53, -22], "block": "minecraft:slime_block"},
    {"pos": [12, -53, -22], "block": "minecraft:slime_block"},
    {"pos": [13, -53, -22], "block": "minecraft:slime_block"},
    {"pos": [14, -53, -22], "block": "minecraft:slime_block"},
    {"pos": [11, -52, -22], "block": "minecraft:redstone_block"},
    {"pos": [14, -52, -22], "block": "minecraft:powered_rail[powered=false,shape=north_south]"},
    {"pos": [11, -51, -22], "block": "minecraft:sand"},
    {"pos": [11, -50, -22], "block": "minecraft:sand"},
    {"pos": [13, -49, -22], "block": "minecraft:slime_block"},
    {"pos": [11, -48, -22], "block": "minecraft:sticky_piston[extended=false,facing=east]"},
    {"pos": [12, -48, -22], "block": "minecraft:slime_block"},
    {"pos": [13, -48, -22], "block": "minecraft:slime_block"},
    {"pos": [12, -47, -22], "block": "minecraft:redstone_block"},
]


class FixtureError(RuntimeError):
    """Something is wrong with the fixture; the message is the report."""


class HashMismatch(FixtureError):
    def __init__(self, file: Path, expected: str, actual: str) -> None:
        super().__init__(
            f"sha256 mismatch for {file.name}: manifest says {expected}, file is {actual}"
        )
        self.file = file
        self.expected = expected
        self.actual = actual


class UnsafeArchive(FixtureError):
    pass


# --------------------------------------------------------------------------- json / hashing


def read_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def world_tree_hash(world: Path) -> tuple[str, int, int]:
    """Canonical hash of a save directory: sorted relative paths + file hashes.

    Returns ``(sha256, file_count, total_bytes)``. Directory mtimes and
    ``session.lock`` are not part of it, so the same imported world hashes the
    same on every machine.
    """
    entries: list[str] = []
    total = 0
    for path in sorted(world.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(world).as_posix()
        if rel == "session.lock":
            continue
        total += path.stat().st_size
        entries.append(f"{rel}\0{sha256_file(path)}")
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return digest, len(entries), total


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def normalized_entity_hash(records: list[dict[str, Any]]) -> str:
    """Order-sensitive hash of stack records with volatile identity removed.

    UUIDs, entity ids and timestamps are deliberately not part of the hash: the
    fixture asks for a reproducible *state and order*, not for the same random
    UUIDs every time.
    """
    fields = []
    for record in records:
        fields.append(
            {
                "spawn_index": record.get("spawn_index"),
                "pos": [round(float(v), 4) for v in (record.get("pos") or [])],
                "motion": [round(float(v), 6) for v in (record.get("motion") or [])],
                "items": record.get("items"),
            }
        )
    return hashlib.sha256(canonical_json(fields).encode("utf-8")).hexdigest()


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


# --------------------------------------------------------------------------- manifest / spec


@dataclass
class Manifest:
    path: Path
    data: dict[str, Any]

    @property
    def filename(self) -> str:
        return self.data["artifact"]["filename"]

    @property
    def sha256(self) -> str:
        return self.data["artifact"]["sha256"].lower()

    @property
    def size(self) -> int:
        return int(self.data["artifact"]["size"])

    @property
    def urls(self) -> list[str]:
        artifact = self.data["artifact"]
        return [artifact["url"], *(artifact.get("mirrors") or [])]

    def mod(self, name: str) -> dict[str, Any] | None:
        for entry in self.data.get("mods") or []:
            if entry.get("name") == name:
                return entry
        return None


def load_manifest(path: Path | None = None) -> Manifest:
    path = path or (EXAMPLES / "map-manifest.json")
    data = read_json(path)
    if data.get("format") != "mc-agent/map-manifest@1":
        raise FixtureError(f"{path} is not a map-manifest@1 file")
    for key in ("artifact", "game", "world"):
        if key not in data:
            raise FixtureError(f"{path} has no {key!r} section")
    for key in ("url", "sha256", "size", "filename"):
        if key not in data["artifact"]:
            raise FixtureError(f"{path}: artifact.{key} is missing")
    if not re.fullmatch(r"[0-9a-f]{64}", data["artifact"]["sha256"]):
        raise FixtureError(f"{path}: artifact.sha256 is not a sha256")
    return Manifest(path=path, data=data)


def load_spec(path: Path | None = None) -> dict[str, Any]:
    path = path or (EXAMPLES / "fixture-spec.json")
    data = read_json(path)
    if data.get("format") != "mc-agent/fixture-spec@1":
        raise FixtureError(f"{path} is not a fixture-spec@1 file")
    for key in ("machine", "user", "program", "validation"):
        if key not in data:
            raise FixtureError(f"{path} has no {key!r} section")
    return data


def load_program(path: Path) -> list[dict[str, Any]]:
    """A program is ``{"carts": [{"items": [...]}, ...]}`` in spawn order."""
    data = read_json(path)
    carts = data.get("carts") if isinstance(data, dict) else data
    if not isinstance(carts, list) or not carts:
        raise FixtureError(f"{path} holds no cart program")
    for index, entry in enumerate(carts):
        if not isinstance(entry, dict) or not isinstance(entry.get("items"), list):
            raise FixtureError(f"{path}: cart {index} needs an items list")
    return carts


# --------------------------------------------------------------------------- download / unpack


@dataclass
class DownloadResult:
    path: Path
    url: str
    status: int
    bytes: int
    sha256: str
    cache_hit: bool
    seconds: float

    def as_record(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "url": self.url,
            "http_status": self.status,
            "bytes": self.bytes,
            "sha256": self.sha256,
            "cache_hit": self.cache_hit,
            "seconds": round(self.seconds, 3),
        }


def _http_get(url: str, timeout: float = 300.0) -> tuple[int, Any, dict[str, str]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        raise FixtureError(
            f"{url} answered HTTP {error.code}; the manifest URL must be reachable"
        ) from error
    except OSError as error:
        raise FixtureError(f"{url} is unreachable: {error}") from error
    return response.status, response, dict(response.headers)


def verify_artifact(path: Path, manifest: Manifest) -> None:
    actual_size = path.stat().st_size
    if actual_size != manifest.size:
        raise FixtureError(
            f"{path.name} is {actual_size} bytes, manifest says {manifest.size}"
        )
    actual_hash = sha256_file(path)
    if actual_hash != manifest.sha256:
        raise HashMismatch(path, manifest.sha256, actual_hash)


def fetch_artifact(
    manifest: Manifest,
    cache_dir: Path | None = None,
    *,
    url: str | None = None,
    cold: bool = False,
    timeout: float = 300.0,
) -> DownloadResult:
    """Download and hash-check the map artifact. Never trusts the cache blindly."""
    cache_dir = cache_dir or MAP_CACHE
    target = cache_dir / manifest.filename
    started = time.monotonic()

    if target.exists() and not cold:
        verify_artifact(target, manifest)
        return DownloadResult(
            target, "(cache)", 0, target.stat().st_size, manifest.sha256, True, 0.0
        )

    urls = [url] if url else list(manifest.urls)
    last_error: Exception | None = None
    for candidate in urls:
        partial = target.with_name(target.name + ".part")
        try:
            status, response, headers = _http_get(candidate, timeout)
            partial.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with open(partial, "wb") as handle:
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise FixtureError(
                            f"{candidate} is larger than {MAX_ARCHIVE_BYTES} bytes; refusing it"
                        )
                    handle.write(chunk)
            declared = headers.get("Content-Length")
            if declared is not None and int(declared) != total:
                raise FixtureError(
                    f"{candidate} answered {total} bytes but announced {declared}"
                )
            if total != manifest.size:
                raise FixtureError(
                    f"{candidate} returned {total} bytes, manifest says {manifest.size}"
                )
            actual = sha256_file(partial)
            if actual != manifest.sha256:
                raise HashMismatch(partial, manifest.sha256, actual)
            partial.replace(target)
            return DownloadResult(
                target, candidate, status, total, actual, False, time.monotonic() - started
            )
        except HashMismatch:
            partial.unlink(missing_ok=True)
            raise
        except FixtureError as error:
            partial.unlink(missing_ok=True)
            last_error = error
        except OSError as error:
            partial.unlink(missing_ok=True)
            last_error = FixtureError(f"{candidate} failed: {error}")
    raise FixtureError(f"could not fetch the artifact: {last_error}")


def _safe_member(name: str) -> str:
    """Turn an archive member name into a safe relative path or refuse it."""
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        raise UnsafeArchive(f"archive entry {name!r} is absolute")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise UnsafeArchive(f"archive entry {name!r} escapes the extraction directory")
    return "/".join(parts)


def safe_extract(zip_path: Path, dest: Path) -> dict[str, Any]:
    """Extract a map archive without letting it write anywhere but ``dest``.

    Rejects absolute paths, parent traversal, symlinks and oversized archives.
    Returns a small report (entry count, bytes, root directory).
    """
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    count = 0
    roots: set[str] = set()
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise UnsafeArchive(f"{zip_path.name} has {len(infos)} entries; refusing it")
        for info in infos:
            safe = _safe_member(info.filename)
            if not safe:
                continue
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UnsafeArchive(f"archive entry {info.filename!r} is a symlink")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise UnsafeArchive(
                    f"{zip_path.name} expands to more than {MAX_UNCOMPRESSED_BYTES} bytes"
                )
            count += 1
            roots.add(safe.split("/", 1)[0])
            out = dest / safe
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, open(out, "wb") as handle:
                shutil.copyfileobj(source, handle)
    if count == 0:
        raise FixtureError(f"{zip_path.name} is an empty archive")
    return {"entries": count, "uncompressed_bytes": total, "roots": sorted(roots)}


def unpack_artifact(manifest: Manifest, zip_path: Path, dest_root: Path) -> dict[str, Any]:
    """Safely extract the artifact and locate the world directory inside it."""
    staging = dest_root / manifest.filename.removesuffix(".zip")
    if staging.exists():
        shutil.rmtree(staging)
    report = safe_extract(zip_path, staging)
    candidates = sorted(staging.glob("*/world/level.dat")) + sorted(staging.glob("world/level.dat"))
    if not candidates:
        raise FixtureError(
            f"{zip_path.name} has no */world/level.dat; expected an EXPORT.json + world/ layout"
        )
    world = candidates[0].parent
    export = world.parent / "EXPORT.json"
    export_data = read_json(export) if export.is_file() else None
    world_hash, files, size = world_tree_hash(world)
    expected = (export_data or {}).get("world_sha256")
    if expected and expected != world_hash:
        raise FixtureError(
            f"extracted world tree hash {world_hash} does not match EXPORT.json {expected}"
        )
    report.update(
        {
            "staging": str(staging),
            "world": str(world),
            "world_sha256": world_hash,
            "world_files": files,
            "world_bytes": size,
            "export": export_data,
        }
    )
    return report


# --------------------------------------------------------------------------- lab control


def run_lab_server(*args: str, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(TOOLS / "lab_server.py"), *args]
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def lab_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or "") or not name.strip("."):
        raise FixtureError(f"bad lab name {name!r}")
    return LABS / name


def patch_lab_ports(lab: Path, rcon_port: int | None, server_port: int | None) -> None:
    """Pin a lab to stable ports (issue-specific labs use a small port block)."""
    if rcon_port is None and server_port is None:
        return
    rcon_json = lab / "rcon.json"
    state_json = lab / "lab.json"
    if not rcon_json.is_file() or not state_json.is_file():
        raise FixtureError(f"{lab} is not provisioned; run provision first")
    rcon = read_json(rcon_json)
    state = read_json(state_json)
    if rcon_port is not None:
        rcon["port"] = rcon_port
        state["rconPort"] = rcon_port
    if server_port is not None:
        state["serverPort"] = server_port
    write_json(rcon_json, rcon)
    write_json(state_json, state)
    properties = lab / "server.properties"
    text = properties.read_text("utf-8")
    if rcon_port is not None:
        text = re.sub(r"^rcon\.port=.*$", f"rcon.port={rcon_port}", text, flags=re.M)
    if server_port is not None:
        text = re.sub(r"^server-port=.*$", f"server-port={server_port}", text, flags=re.M)
        text = re.sub(r"^query\.port=.*$", f"query.port={server_port}", text, flags=re.M)
    properties.write_text(text, "utf-8", newline="\n")


def import_world(
    lab_name: str,
    world: Path,
    *,
    manifest: Manifest | None = None,
    reset: bool = False,
    java: str = "",
    memory: str = "",
    rcon_port: int | None = None,
    server_port: int | None = None,
    vantage_port: int | None = None,
    bridge_port: int | None = None,
    interface_mod: Path | None = None,
    extra_mods: Iterable[Path] = (),
) -> dict[str, Any]:
    """Provision (or reset) a lab around a verified world copy."""
    lab = lab_dir(lab_name)
    provision = [
        "provision",
        "--name",
        lab_name,
        "--fabric-api",
        "--carpet",
        *(["--java", java] if java else []),
        *(["--memory", memory] if memory else []),
        # current lab_server pins the ports at provision time; patch_lab_ports
        # below still fixes labs provisioned by an older tool
        *(["--rcon-port", str(rcon_port)] if rcon_port else []),
        *(["--server-port", str(server_port)] if server_port else []),
        *(["--vantage-port", str(vantage_port)] if vantage_port else []),
        *(["--bridge-port", str(bridge_port)] if bridge_port else []),
        *(["--mod-jar", str(interface_mod)] if interface_mod else []),
    ]
    if reset and lab.exists():
        run_lab_server("stop", "--name", lab_name, "--timeout", "120")
        shutil.rmtree(lab / "world", ignore_errors=True)
        result = run_lab_server(*provision)
        if result.returncode != 0:
            raise FixtureError(f"could not re-provision {lab}:\n{result.stdout}\n{result.stderr}")
        shutil.copytree(world, lab / "world", ignore=shutil.ignore_patterns("session.lock"))
    elif not (lab / "lab.json").is_file() or not (lab / "world" / "level.dat").is_file():
        result = run_lab_server(*provision, "--world", str(world))
        if result.returncode != 0:
            raise FixtureError(f"could not provision {lab}:\n{result.stdout}\n{result.stderr}")
    for mod in extra_mods:
        if mod.is_file():
            (lab / "mods").mkdir(exist_ok=True)
            shutil.copy2(mod, lab / "mods" / mod.name)
    patch_lab_ports(lab, rcon_port, server_port)
    copied_hash, files, size = world_tree_hash(lab / "world")
    expected_hash, expected_files, expected_size = world_tree_hash(world)
    if (copied_hash, files, size) != (expected_hash, expected_files, expected_size):
        raise FixtureError(
            "the imported lab world does not hash-match the verified artifact; use --reset"
        )
    return {
        "lab": lab_name,
        "lab_dir": str(lab),
        "world_sha256": copied_hash,
        "world_files": files,
        "world_bytes": size,
        "manifest_version": (manifest.data.get("version") if manifest else None),
    }


def start_lab(lab_name: str, wait: float = 300.0) -> None:
    result = run_lab_server("start", "--name", lab_name, "--wait", str(wait))
    if result.returncode != 0:
        raise FixtureError(f"lab {lab_name} did not start:\n{result.stdout}\n{result.stderr}")


def stop_lab(lab_name: str, timeout: float = 120.0) -> None:
    run_lab_server("stop", "--name", lab_name, "--timeout", str(timeout))


def lab_status(lab_name: str) -> dict[str, Any]:
    lab = lab_dir(lab_name)
    state = read_json(lab / "lab.json")
    run = read_json(lab / "run.json") if (lab / "run.json").is_file() else {}
    pid = int(run.get("pid") or 0)
    alive = bool(pid) and lab_server.process_matches(pid, float(run.get("startedAt") or 0))
    rcon = read_json(lab / "rcon.json") if (lab / "rcon.json").is_file() else {}
    mods = state.get("mods") or []
    interface = next(
        (mod for mod in mods if str(mod.get("name", "")).startswith("mc-agent-interface")), None
    )
    return {
        "lab": lab_name,
        "lab_dir": str(lab),
        "running": alive,
        "pid": pid if alive else None,
        "startedAt": run.get("startedAt"),
        "rcon_host": rcon.get("host"),
        "rcon_port": rcon.get("port"),
        "world": state.get("world"),
        "world_dir": state.get("worldDir"),
        "mods": [mod.get("name") for mod in mods],
        "mod_records": mods,
        "minecraft": state.get("minecraft"),
        "loader": state.get("loader"),
        "server_vantage_port": state.get("serverVantagePort"),
        "server_dir": state.get("serverDir"),
        "bridge_api_port": state.get("bridgeApiPort"),
        "interface_mod": interface,
    }


# --------------------------------------------------------------------------- console / snapshots


@dataclass
class Console:
    """A reconnecting RCON client that keeps a full command transcript."""

    lab_name: str
    rcon: lab_server.Rcon | None = None
    commands: list[dict[str, Any]] = field(default_factory=list)
    verbose: bool = False

    def connect(self) -> "Console":
        lab, _state = lab_server.load_lab(self.lab_name)
        self.rcon = lab_server.open_console(lab)
        self.rcon.connect()
        return self

    def reconnect(self) -> None:
        if self.rcon is not None:
            try:
                self.rcon.close()
            except Exception:
                pass
        self.connect()

    def cmd(self, command: str, idle: float = 0.4, retries: int = 4) -> str:
        last: Exception | None = None
        for _ in range(retries):
            try:
                assert self.rcon is not None
                output = lab_server.clean_console_text(self.rcon.command(command, idle=idle))
                self.commands.append({"command": command, "output": output})
                if self.verbose:
                    print(f"$ {command}\n{output}", flush=True)
                return output
            except (lab_server.RconError, OSError) as error:
                last = error
                time.sleep(0.5)
                try:
                    self.reconnect()
                except Exception as exc:  # pragma: no cover - depends on server state
                    last = exc
                    time.sleep(0.5)
        raise FixtureError(f"RCON failed after {retries} tries on {command!r}: {last}")

    def close(self) -> None:
        if self.rcon is not None:
            self.rcon.close()
            self.rcon = None


def parse_vec(text: str) -> list[float] | None:
    """Parse ``[1.0d, 2.0f, 3.0d]`` into floats."""
    if "[" not in text or "]" not in text:
        return None
    body = text.split("[", 1)[1].split("]", 1)[0]
    values = []
    for part in body.split(","):
        part = part.strip().rstrip("fd")
        try:
            values.append(float(part))
        except ValueError:
            return None
    return values or None


def after_mark(text: str, mark: str) -> str:
    return text.split(mark, 1)[-1].strip() if mark in text else text.strip()


def split_records(output: str, mark: str) -> list[str]:
    """Split concatenated ``data get`` answers into one string per entity.

    ``execute as @e[...] run data get entity @s Pos`` glues every answer
    together without a separator, so the entity prefix is the separator.
    """
    if not output:
        return []
    parts = output.split(mark)
    if len(parts) > 1:
        return [part.strip() for part in parts[1:] if part.strip()]
    return [output.strip()] if output.strip() else []


def uuid_from_text(text: str) -> str:
    """``[I; a, b, c, d]`` -> canonical 8-4-4-4-12 UUID."""
    match = re.search(r"\[I;\s*([-\d, ]+)\]", text)
    if not match:
        return after_mark(text, "entity data: ").strip()
    values = [int(part) & 0xFFFFFFFF for part in match.group(1).split(",")]
    if len(values) != 4:
        return text.strip()
    raw = "".join(f"{value:08x}" for value in values)
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def parse_items(text: str) -> list[dict[str, Any]]:
    """Parse ``[{count: 1, Slot: 0b, id: "minecraft:stone"}]`` without a MC parser."""
    if not text or text.strip() in ("[]", ""):
        return []
    items: list[dict[str, Any]] = []
    for match in re.finditer(r"\{(.*?)\}", text):
        item: dict[str, Any] = {}
        for pair in re.finditer(r"(\w+)\s*:\s*(\"[^\"]*\"|[-\w.]+)", match.group(1)):
            key, value = pair.group(1), pair.group(2)
            if value.startswith('"'):
                item[key] = value[1:-1]
            elif value.endswith("b") and re.fullmatch(r"-?\d+b", value):
                item[key] = int(value[:-1])
            elif re.fullmatch(r"-?\d+", value):
                item[key] = int(value)
            else:
                item[key] = value
        if "id" in item:
            items.append(item)
    items.sort(key=lambda entry: (entry.get("Slot", 0), entry.get("id", "")))
    return items


def cart_records(console: Console, tag: str = "") -> list[dict[str, Any]]:
    """Every chest minecart in the world, in server iteration order.

    The order is the order ``@e`` visits them in, which is the in-memory entity
    order the fixture cares about; it is never assumed to be the pop order.
    """
    selector = "type=minecraft:chest_minecart"
    if tag:
        selector += f",tag={tag}"
    where = f"@e[{selector}]"
    positions = split_records(
        console.cmd(f"execute as {where} run data get entity @s Pos"), CART_MARK
    )
    uuids = split_records(console.cmd(f"execute as {where} run data get entity @s UUID"), CART_MARK)
    motions = split_records(
        console.cmd(f"execute as {where} run data get entity @s Motion"), CART_MARK
    )
    items = split_records(
        console.cmd(f"execute as {where} run data get entity @s Items"), CART_MARK
    )
    records = []
    for index, chunk in enumerate(positions):
        raw_items = items[index] if index < len(items) else ""
        records.append(
            {
                "uuid": uuid_from_text(uuids[index]) if index < len(uuids) else "",
                "pos": parse_vec(chunk),
                "motion": parse_vec(motions[index]) if index < len(motions) else None,
                "items": parse_items(raw_items),
                # the raw ``Items`` fragment keeps item components comparable
                "items_raw": raw_items,
            }
        )
    return records


def item_key(item: dict[str, Any]) -> tuple:
    return (int(item.get("Slot", 0)), item.get("id"), int(item.get("count", 1)))


def player_record(console: Console, name: str) -> dict[str, Any] | None:
    mark = f"{name} has the following entity data: "
    first = console.cmd(f"data get entity {name} UUID")
    if not first.startswith(name):
        return None
    record: dict[str, Any] = {"name": name}
    for key in ("UUID", "Pos", "Rotation", "playerGameType"):
        text = console.cmd(f"data get entity {name} {key}")
        record[key] = after_mark(text, mark)
    return record


def machine_state(console: Console, checks: list[dict[str, Any]]) -> dict[str, Any]:
    entries = []
    ok = True
    for check in checks:
        x, y, z = check["pos"]
        matched = console.cmd(
            f"execute if block {x} {y} {z} {check['block']} run seed", idle=0.2
        ).startswith("Seed")
        entries.append({**check, "ok": matched})
        ok = ok and matched
    return {"ok": ok, "checks": entries}


def world_day_ticks(console: Console) -> int | None:
    """The 26.2 ``time query day`` tick count, when the server reports it."""
    match = re.search(r"at\s+(-?\d+)\s+tick", console.cmd("time query day", idle=0.2))
    return int(match.group(1)) if match else None


def note_value(console: Console, pos: list[int], values: range = range(0, 25)) -> int | None:
    x, y, z = pos
    for note in values:
        if console.cmd(
            f"execute if block {x} {y} {z} minecraft:note_block[note={note}] run seed",
            idle=0.1,
        ).startswith("Seed"):
            return note
    return None


def interface_binding(
    interface: "interface_mod.InterfaceClient",
    snapshot_name: str,
    carts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind the carts to an actual clean-mod SNAPSHOT (authoritative tick order)."""
    ack = interface.snapshot(snapshot_name)
    directory = Path(ack["dir"])
    meta, entities = interface_mod.read_snapshot(directory)
    order = interface_mod.tick_order(entities, "minecraft:chest_minecart")
    known = {record["uuid"] for record in carts}
    # keep only entities the fixture owns; the level also ticks the player seat
    order = [uuid for uuid in order if uuid in known]
    index = interface_mod.by_uuid(entities)
    checks = []
    for record in carts:
        entity = index.get(record["uuid"])
        entry: dict[str, Any] = {"uuid": record["uuid"], "present": entity is not None}
        if entity is not None:
            items = interface_mod.compare_items(
                str(entity.get("nbt") or ""), record.get("items_raw") or ""
            )
            pos = [float(value) for value in (entity.get("pos") or [])]
            vel = [float(value) for value in (entity.get("vel") or [])]
            entry.update(
                {
                    "items_match": items["match"],
                    "items": items,
                    "pos_match": bool(record.get("pos"))
                    and len(pos) == 3
                    and all(abs(a - b) <= 1e-4 for a, b in zip(record["pos"], pos)),
                    "motion_match": bool(record.get("motion"))
                    and len(vel) == 3
                    and all(abs(a - b) <= 1e-4 for a, b in zip(record["motion"], vel)),
                    "nbt_sha256": hashlib.sha256(
                        str(entity.get("nbt") or "").encode("utf-8")
                    ).hexdigest(),
                }
            )
        checks.append(entry)
    return {
        "name": ack.get("id"),
        "dir": ack.get("dir"),
        "entities": ack.get("entities"),
        "order_hash": ack.get("orderHash"),
        "tick": ack.get("tick"),
        "dimension": ack.get("dimension"),
        "bytes": ack.get("bytes"),
        "mod": meta.get("mod"),
        "mod_version": meta.get("modVersion"),
        "protocol": meta.get("protocol"),
        "players_skipped": meta.get("playersSkipped"),
        "tick_order": order,
        "cross_check": checks,
        "cross_check_ok": all(
            entry["present"]
            and entry.get("items_match")
            and entry.get("pos_match")
            and entry.get("motion_match")
            for entry in checks
        ),
    }


def take_snapshot(
    console: Console,
    spec: dict[str, Any],
    *,
    spawn_order: list[str] | None = None,
    server: dict[str, Any] | None = None,
    program: list[dict[str, Any]] | None = None,
    interface: "interface_mod.InterfaceClient | None" = None,
    snapshot_name: str | None = None,
) -> dict[str, Any]:
    """Capture the full ready state: entities, machine, user, tick state.

    The RCON ``@e`` query order is recorded separately as ``rcon_order``: it is
    an observation, not the entity tick order. When an interface client is
    given, ``tick_order`` comes from the mod's real ``EntityTickList`` snapshot
    and the cart NBT/inventory is cross-checked against it.
    """
    fixture = spec["fixture"]
    checks = spec["machine"].get("checks") or DEFAULT_MACHINE_CHECKS
    carts = cart_records(console)
    for record in carts:
        # the full entity NBT dump, unsanitized, is part of the ready record
        dump = console.cmd(f"data get entity {record['uuid']}")
        record["nbt"] = after_mark(dump, " has the following entity data: ")
        record["nbt_sha256"] = hashlib.sha256(record["nbt"].encode("utf-8")).hexdigest()
    spawn_order = spawn_order or []
    for record in carts:
        record["spawn_index"] = (
            spawn_order.index(record["uuid"]) if record["uuid"] in spawn_order else None
        )
    machine = {
        "ok": False,
        "checks": [],
        "note": note_value(console, spec["machine"]["input"]["block"]),
        "note_block": spec["machine"]["input"]["block"],
    }
    machine.update(machine_state(console, checks))
    snapshot: dict[str, Any] = {
        "format": RECORD_FORMAT,
        "captured_at": iso_now(),
        "tick_frozen": "game is frozen" in console.cmd("tick query").lower(),
        "world_day_tick": world_day_ticks(console),
        "lab": console.lab_name,
        "server": server,
        "program": program if program is not None else spec["program"]["entries"],
        "spawn_order": spawn_order,
        "carts": carts,
        "cart_count": len(carts),
        "stack": spec["machine"]["stack"],
        "machine": machine,
        "user": player_record(console, canonical_user(spec["user"]["name"])),
        "rcon_order": [record["uuid"] for record in carts],
        "normalized_hash": normalized_entity_hash(carts),
    }
    if interface is not None:
        name = snapshot_name or f"fixture-{console.lab_name}"
        try:
            binding = interface_binding(interface, name, carts)
            snapshot["interface"] = binding
            snapshot["tick_order"] = binding["tick_order"]
            snapshot["tick_order_hash"] = tick_order_hash(carts, binding["tick_order"])
        except (interface_mod.InterfaceError, OSError) as error:
            # a validation caller must see this as missing evidence, not a crash
            snapshot["interface"] = {"error": str(error), "name": name}
            snapshot["tick_order"] = None
            snapshot["tick_order_hash"] = None
    else:
        snapshot["interface"] = None
        snapshot["tick_order"] = None
        snapshot["tick_order_hash"] = None
    return snapshot


def tick_order_hash(carts: list[dict[str, Any]], tick_order: list[str] | None) -> str | None:
    """16-hex hash over the *tick order* of the carts, normalized by spawn index.

    UUIDs are excluded, so three independent initializations with the same
    program produce the same value; a different tick order produces a different
    value. This is the fixture's order evidence; the mod's own ``orderHash``
    over raw UUIDs is kept separately as ``interface.order_hash``.
    """
    if not tick_order:
        return None
    index = {record["uuid"]: record for record in carts}
    sequence = []
    for uuid in tick_order:
        record = index.get(uuid)
        if record is None:
            continue
        sequence.append(
            {
                "spawn_index": record.get("spawn_index"),
                "items": sorted(
                    (item_key(item) for item in (record.get("items") or [])),
                    key=lambda value: (value[0], value[1]),
                ),
            }
        )
    digest = hashlib.sha256(canonical_json(sequence).encode("utf-8")).hexdigest()
    return digest[:16]


def canonical_user(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


# --------------------------------------------------------------------------- initialization


def wait_until_frozen(console: Console, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "game is frozen" in console.cmd("tick query", idle=0.05).lower():
            return True
        time.sleep(0.05)
    return False


def sprint(console: Console, ticks: int = 1) -> None:
    console.cmd(f"tick sprint {ticks}", idle=0.2)
    wait_until_frozen(console)


def wait_for_entity(console: Console, name: str, timeout: float = 120.0, poll: float = 0.1) -> bool:
    """Wait for a queued Carpet fake player to log in.

    The world must be running while waiting (Carpet completes the join), so the
    caller freezes it again the moment this returns. Polling in real time is
    used deliberately: a sprinted tick does not process the login.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if console.cmd(f"data get entity {name} UUID").startswith(name):
            return True
        time.sleep(poll)
    return False


def aim_at(console: Console, name: str, target: list[float]) -> dict[str, Any]:
    """Point a fake player at a target and return the resulting rotation."""
    pos = parse_vec(after_mark(console.cmd(f"data get entity {name} Pos"), "entity data: "))
    rotation = parse_vec(
        after_mark(console.cmd(f"data get entity {name} Rotation"), "entity data: ")
    )
    if pos is None or rotation is None:
        raise FixtureError(f"cannot read {name} position/rotation")
    eye = [pos[0], pos[1] + 1.62, pos[2]]
    dx, dy, dz = (target[0] - eye[0], target[1] - eye[1], target[2] - eye[2])
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = math.degrees(-math.atan2(dy, math.hypot(dx, dz)))
    delta_yaw = (yaw - rotation[0] + 540) % 360 - 180
    console.cmd(f"player {name} turn {pitch - rotation[1]:.4f} {delta_yaw:.4f}", idle=0.3)
    sprint(console, 1)
    actual = parse_vec(
        after_mark(console.cmd(f"data get entity {name} Rotation"), "entity data: ")
    )
    return {"requested": [round(yaw, 4), round(pitch, 4)], "actual": actual}


def prepare_world(console: Console, spec: dict[str, Any]) -> dict[str, Any]:
    """Force-load the machine chunks, freeze the world, verify the base state."""
    bounds = spec["machine"]["bounds"]
    x1, _y1, z1 = bounds["from"]
    x2, _y2, z2 = bounds["to"]
    chunks = f"{x1 >> 4} {z1 >> 4} {x2 >> 4} {z2 >> 4}"
    console.cmd(f"forceload add {chunks}")
    if "game is frozen" not in console.cmd("tick query").lower():
        console.cmd("tick freeze")
    checks = spec["machine"].get("checks") or DEFAULT_MACHINE_CHECKS
    machine = machine_state(console, checks)
    if not machine["ok"]:
        broken = [entry for entry in machine["checks"] if not entry["ok"]]
        raise FixtureError(
            "the machine is not in its calibrated base state; the fixture copy is dirty: "
            + json.dumps(broken, ensure_ascii=False)
        )
    return {"forceload": chunks, "machine": machine}


def initialize(
    console: Console,
    spec: dict[str, Any],
    program: list[dict[str, Any]] | None = None,
    *,
    record_dir: Path | None = None,
    interface: "interface_mod.InterfaceClient | None" = None,
    snapshot_name: str | None = None,
    allow_rcon_order: bool = False,
) -> dict[str, Any]:
    """Create the ready fixture and return the ready snapshot plus the log.

    Steps, in order:

    1. force-load the machine chunks, verify the machine is at its base state;
    2. spawn the real Carpet fake player, mount the hover seat, aim it at the
       note block, then freeze the world;
    3. reset the note block to the calibrated start value;
    4. for every program entry: summon a chest minecart on the rail, fill it,
       stack it, and record its UUID / spawn index;
    5. verify the ready state, bind it to an interface-mod SNAPSHOT (the real
       ``EntityTickList`` order) and capture the unsanitized snapshot.
    """
    fixture = spec["fixture"]
    user = spec["user"]
    machine = spec["machine"]
    entries = program if program is not None else spec["program"]["entries"]
    start = time.monotonic()
    steps: list[dict[str, Any]] = []

    def note(step: str, detail: Any = None) -> None:
        steps.append({"at": iso_now(), "step": step, "detail": detail})

    if interface is None and not allow_rcon_order:
        raise FixtureError(
            "the interface mod is required for authoritative tick-order evidence: "
            "provision the lab with --interface-mod and pass the server-vantage port. "
            "Pass --allow-rcon-order only for development; that order is not the tick order."
        )
    if interface is not None:
        try:
            interface.ping()
        except (interface_mod.InterfaceError, OSError) as error:
            raise FixtureError(f"the interface mod is not reachable: {error}") from error
        note(
            "interface",
            {
                "port": interface.port,
                "protocol": "SNAPSHOT",
                "snapshot_name": snapshot_name or f"fixture-{console.lab_name}",
            },
        )

    if "game is frozen" not in console.cmd("tick query").lower():
        console.cmd("tick freeze")
    note("machine-base", prepare_world(console, spec))

    console.cmd("kill @e[type=minecraft:chest_minecart]", idle=0.5)
    console.cmd(f"kill @e[type=minecraft:armor_stand,tag={fixture['tag']}]")
    leftovers = cart_records(console)
    if leftovers:
        raise FixtureError(
            "a chest minecart survived cleanup: " + json.dumps(leftovers, ensure_ascii=False)
        )
    console.cmd(f"player {user['name'].lower()} kill", idle=0.5)

    seat = user.get("hover_seat") or {}
    if seat.get("enabled", True):
        at = " ".join(str(value) for value in seat["pos"])
        console.cmd(f"summon minecraft:armor_stand {at} {seat['nbt']}", idle=0.4)
    user_name = user["name"].lower()
    spawn_at = " ".join(str(value) for value in user["spawn"])
    console.cmd("tick unfreeze", idle=0.2)
    console.cmd(
        f"player {user_name} spawn at {spawn_at} facing {user['facing'][0]} {user['facing'][1]}"
        f" in {user.get('dimension', 'minecraft:overworld')} in {user.get('gamemode', 'creative')}",
        idle=0.6,
    )
    canonical = canonical_user(user_name)
    if not wait_for_entity(console, canonical, timeout=120.0):
        raise FixtureError(f"the Carpet fake player {user_name} never spawned")
    # Freeze immediately: the user spawns in mid-air over the void and would
    # otherwise fall out of the world while the fixture is being assembled.
    console.cmd("tick freeze", idle=0.3)
    if not console.cmd(f"data get entity {canonical} UUID").startswith(canonical):
        raise FixtureError(f"the Carpet fake player {canonical} was removed before assembly")
    if seat.get("enabled", True):
        console.cmd(
            f"ride {canonical} mount @e[type=minecraft:armor_stand,tag={fixture['tag']},limit=1]",
            idle=0.4,
        )
        at = " ".join(str(value) for value in seat["pos"])
        console.cmd(
            f"tp @e[type=minecraft:armor_stand,tag={fixture['tag']},limit=1] {at}", idle=0.4
        )
        sprint(console, 1)
    note("user", {"name": canonical, "aim": aim_at(console, canonical, user["aim_target"]), "seat": seat})

    input_block = machine["input"]["block"]
    note_start = int(machine["input"].get("start_note", 20))
    x, y, z = input_block
    console.cmd(
        f"setblock {x} {y} {z} minecraft:note_block[instrument=harp,note={note_start},powered=false]",
        idle=0.3,
    )
    note("note-block-reset", note_start)

    spawn = machine["stack"]["spawn"]
    spawn_order: list[str] = []
    for index, entry in enumerate(entries):
        known = {record["uuid"] for record in cart_records(console)}
        at = " ".join(str(value) for value in spawn)
        console.cmd(f"summon minecraft:chest_minecart {at}", idle=0.4)
        new: list[str] = []
        for _ in range(10):
            current = {record["uuid"] for record in cart_records(console)}
            new = sorted(current - known)
            if len(new) == 1:
                break
            sprint(console, 1)
            time.sleep(0.1)
        if len(new) != 1:
            raise FixtureError(f"could not identify the cart for program entry {index}: {new}")
        uuid = new[0]
        items = json.dumps(
            [
                {
                    "Slot": int(item.get("slot", item.get("Slot", 0))),
                    "id": item["id"],
                    "count": int(item.get("count", 1)),
                }
                for item in entry.get("items", [])
            ],
            separators=(",", ":"),
        )
        console.cmd(f"data merge entity {uuid} {{Items:{items}}}", idle=0.4)
        spawn_order.append(uuid)
        note("cart", {"index": index, "uuid": uuid, "items": entry.get("items", [])})

    # one tick settles the summoned carts onto the rail (y=-51.9375) and lets
    # the merge take effect before the ready snapshot
    sprint(console, 1)

    ready = take_snapshot(
        console,
        spec,
        spawn_order=spawn_order,
        server=lab_status(console.lab_name),
        program=entries,
        interface=interface,
        snapshot_name=snapshot_name,
    )
    ready["initialization"] = {
        "started_at": iso_now(),
        "seconds": round(time.monotonic() - start, 3),
        "steps": steps,
        "program_entries": len(entries),
        "program_name": (spec["program"].get("name") if program is None else "custom"),
        "user": user_name,
        "spec_version": spec.get("spec_version"),
        "order_source": "interface-snapshot" if interface is not None else "rcon-selector",
    }
    if not allow_rcon_order and not ready.get("tick_order"):
        raise FixtureError(
            "the interface SNAPSHOT produced no authoritative tick order: "
            + json.dumps((ready.get("interface") or {}).get("error") or ready.get("interface"))
        )
    report = validate_ready(spec, ready)
    ready["validation"] = report
    if record_dir is not None:
        write_json(record_dir / "ready-snapshot.json", ready)
        write_json(record_dir / "init-record.json", ready["initialization"])
        (record_dir / "init-commands.jsonl").write_text(
            "\n".join(json.dumps(entry, ensure_ascii=False) for entry in console.commands) + "\n",
            "utf-8",
        )
    if not report["ok"]:
        raise FixtureError(
            "initialization did not produce a valid ready fixture: "
            + json.dumps(report["problems"], ensure_ascii=False)
        )
    return ready


# --------------------------------------------------------------------------- validation


def _close(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance


def validate_ready(spec: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Does this snapshot satisfy the ready contract in the fixture spec?"""
    problems: list[dict[str, Any]] = []
    fixture = spec["fixture"]
    machine = spec["machine"]
    validation = spec.get("validation") or {}
    rest = machine["stack"]["rest_pos"]
    tolerance = float(validation.get("position_tolerance", 0.05))
    # a custom run carries its own program; fall back to the calibrated default
    entries = snapshot.get("program") or spec["program"]["entries"]
    carts = snapshot.get("carts") or []

    if len(carts) != len(entries):
        problems.append({"check": "cart-count", "expected": len(entries), "actual": len(carts)})
    for record in carts:
        if not record.get("pos"):
            problems.append({"check": "cart-position-missing", "uuid": record.get("uuid")})
            continue
        if not all(_close(a, b, tolerance) for a, b in zip(record["pos"], rest)):
            problems.append({"check": "cart-off-stack", "uuid": record.get("uuid"), "pos": record["pos"]})
        if record.get("motion") and any(abs(value) > 1e-6 for value in record["motion"]):
            problems.append({"check": "cart-moving", "uuid": record.get("uuid"), "motion": record["motion"]})
        index = record.get("spawn_index")
        if index is None:
            problems.append({"check": "cart-unexpected", "uuid": record.get("uuid")})
        else:
            expected = entries[index].get("items", [])
            if sorted(map(item_key, _normalize_expected(expected))) != sorted(
                map(item_key, record.get("items") or [])
            ):
                problems.append(
                    {
                        "check": "cart-inventory",
                        "uuid": record.get("uuid"),
                        "expected": expected,
                        "actual": record.get("items"),
                    }
                )
    if not (snapshot.get("machine") or {}).get("ok", False):
        problems.append(
            {
                "check": "machine-state",
                "broken": [entry for entry in snapshot["machine"]["checks"] if not entry["ok"]],
            }
        )
    if validation.get("require_frozen", True) and not snapshot.get("tick_frozen"):
        problems.append({"check": "world-not-frozen"})
    if not snapshot.get("user"):
        problems.append({"check": "user-missing"})
    if validation.get("require_tick_order", True):
        tick_order = snapshot.get("tick_order")
        if not isinstance(tick_order, list) or len(tick_order) != len(carts):
            problems.append(
                {
                    "check": "tick-order-missing",
                    "note": "the ready record must carry the interface-mod EntityTickList order",
                    "tick_order": tick_order,
                    "cart_count": len(carts),
                }
            )
        interface = snapshot.get("interface") or {}
        if not interface.get("cross_check_ok", False):
            problems.append(
                {
                    "check": "interface-cross-check",
                    "note": "the mod snapshot must contain every cart with the same items/pose",
                    "cross_check": interface.get("cross_check"),
                }
            )
    return {
        "ok": not problems,
        "problems": problems,
        "normalized_hash": snapshot.get("normalized_hash"),
        "tick_order_hash": snapshot.get("tick_order_hash"),
        "cart_count": len(carts),
    }


def _normalize_expected(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "Slot": int(item.get("slot", item.get("Slot", 0))),
            "id": item["id"],
            "count": int(item.get("count", 1)),
        }
        for item in items
    ]


def check_live_state(
    console: Console,
    spec: dict[str, Any],
    ready: dict[str, Any],
    *,
    status: dict[str, Any] | None = None,
    interface: "interface_mod.InterfaceClient | None" = None,
    snapshot_name: str | None = None,
) -> dict[str, Any]:
    """Re-read the world and classify it as ready / premature / reloaded / broken.

    Every readiness invariant is re-checked on the *current* state: server
    process, user identity, machine base state, frozen world, cart set/order,
    full entity NBT, and the authoritative tick order from an interface-mod
    SNAPSHOT. Anything that cannot be proven is a problem, never a silent pass.
    """
    problems: list[dict[str, Any]] = []
    status = status or lab_status(console.lab_name)
    current = take_snapshot(
        console,
        spec,
        spawn_order=ready.get("spawn_order") or [],
        server=status,
        program=ready.get("program"),
        interface=interface,
        snapshot_name=snapshot_name,
    )

    if not status.get("running"):
        problems.append({"check": "server-not-running"})
    else:
        ready_pid = (ready.get("server") or {}).get("pid")
        if ready_pid is not None and status.get("pid") != ready_pid:
            problems.append(
                {"check": "server-restarted", "pid_at_ready": ready_pid, "pid_now": status.get("pid")}
            )

    # machine base state again: init is not a one-time check
    if not (current.get("machine") or {}).get("ok", False):
        problems.append(
            {
                "check": "machine-state",
                "broken": [
                    entry for entry in (current.get("machine") or {}).get("checks", []) if not entry["ok"]
                ],
            }
        )
    if not current.get("tick_frozen"):
        problems.append({"check": "world-not-frozen", "tick_frozen": current.get("tick_frozen")})

    ready_user = ready.get("user") or {}
    current_user = current.get("user") or {}
    if not current_user:
        problems.append({"check": "user-missing"})
    else:
        for key in ("UUID", "Pos", "Rotation"):
            if ready_user.get(key) != current_user.get(key):
                problems.append(
                    {
                        "check": f"user-{key.lower()}-changed",
                        "at_ready": ready_user.get(key),
                        "now": current_user.get(key),
                    }
                )

    current_uuids = [record["uuid"] for record in current["carts"]]
    missing = [uuid for uuid in (ready.get("spawn_order") or []) if uuid not in current_uuids]
    if missing:
        problems.append({"check": "carts-missing", "uuids": missing})

    # the RCON selector order stays a separate observation
    if current.get("rcon_order") != ready.get("rcon_order"):
        problems.append(
            {
                "check": "rcon-order-changed",
                "at_ready": ready.get("rcon_order"),
                "now": current.get("rcon_order"),
            }
        )
    if current.get("normalized_hash") != ready.get("normalized_hash"):
        problems.append(
            {
                "check": "normalized-state-changed",
                "at_ready": ready.get("normalized_hash"),
                "now": current.get("normalized_hash"),
            }
        )

    # full entity NBT per cart: inventory contents and components included
    ready_carts = {record["uuid"]: record for record in (ready.get("carts") or [])}
    for record in current["carts"]:
        previous = ready_carts.get(record["uuid"])
        if previous is None:
            continue
        if previous.get("nbt_sha256") != record.get("nbt_sha256") or (
            previous.get("nbt") and previous["nbt"] != record.get("nbt")
        ):
            problems.append(
                {
                    "check": "cart-nbt-changed",
                    "uuid": record["uuid"],
                    "at_ready": previous.get("nbt_sha256"),
                    "now": record.get("nbt_sha256"),
                    "at_ready_nbt": (previous.get("nbt") or "")[:200],
                    "now_nbt": (record.get("nbt") or "")[:200],
                }
            )

    # authoritative tick order, from the mod snapshot
    if not ready.get("tick_order"):
        problems.append(
            {
                "check": "tick-order-missing",
                "note": "the ready record has no interface-mod EntityTickList order",
            }
        )
    elif not current.get("tick_order"):
        problems.append(
            {
                "check": "tick-order-unavailable",
                "note": "no current interface snapshot; cannot prove the tick order",
            }
        )
    else:
        if current.get("tick_order") != ready.get("tick_order"):
            problems.append(
                {
                    "check": "tick-order-changed",
                    "at_ready": ready.get("tick_order"),
                    "now": current.get("tick_order"),
                }
            )
        ready_interface = ready.get("interface") or {}
        current_interface = current.get("interface") or {}
        if ready_interface.get("order_hash") != current_interface.get("order_hash"):
            problems.append(
                {
                    "check": "interface-order-hash-changed",
                    "at_ready": ready_interface.get("order_hash"),
                    "now": current_interface.get("order_hash"),
                }
            )
        if not current_interface.get("cross_check_ok", False):
            problems.append(
                {
                    "check": "interface-cross-check",
                    "cross_check": current_interface.get("cross_check"),
                }
            )

    boundary = float(spec["machine"]["output_boundary"]["greater_than"])
    stack_z = float(spec["machine"]["stack"]["rest_pos"][2])
    for record in current["carts"]:
        pos = record.get("pos")
        if pos and (pos[0] > boundary or abs(pos[2] - stack_z) > 0.6):
            problems.append({"check": "premature-output", "uuid": record.get("uuid"), "pos": pos})
        if record.get("motion") and any(abs(value) > 1e-6 for value in record["motion"]):
            problems.append(
                {"check": "premature-motion", "uuid": record.get("uuid"), "motion": record["motion"]}
            )
    verdict = "READY"
    if any(problem["check"] == "server-restarted" for problem in problems):
        verdict = "FIXTURE_INVALID:RELOAD"
    elif any(
        problem["check"] in ("carts-missing", "premature-output", "premature-motion")
        for problem in problems
    ):
        verdict = "PREMATURE_OUTPUT"
    elif problems:
        verdict = "FIXTURE_INVALID"
    return {
        "format": RECORD_FORMAT,
        "checked_at": iso_now(),
        "verdict": verdict,
        "ok": not problems,
        "problems": problems,
        "snapshot": current,
    }
