#!/usr/bin/env python3
"""Launch a Minecraft instance directly, without the GUI launcher.

Why this exists: the big Windows launchers have no usable CLI (HMCL only knows
``--apply-to``), so an agent that wants to start its own client has to build the
command line the launcher would have built. That is all this does - read the
version JSON, resolve the classpath and natives the launcher already extracted,
substitute the placeholders, and start the game detached.

    python tools/launch_instance.py --minecraft-dir "D:/MC/MC_Game/.minecraft" \
        --version 26.2-Fabric --world "my test world"

Auth comes from the account the launcher already stored (HMCL's
``accounts.json``): the real name and uuid, plus whatever token is on disk. A
stale token is harmless for single player and for offline-mode servers; if you
need online mode, launch once through HMCL first so it refreshes.

The client mod writes its port to ``<gameDir>/mc-agent/port.txt``, so a bridge
started after the game can find it without any configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--minecraft-dir",
        default=os.environ.get("MC_AGENT_MINECRAFT_DIR", ""),
        help="the launcher's game root, i.e. the folder holding versions/ libraries/ assets/",
    )
    parser.add_argument("--version", required=True, help="version folder name, e.g. 26.2-Fabric")
    parser.add_argument("--java", default="", help="java executable (default: JAVA_HOME or PATH)")
    parser.add_argument("--memory", default="4G", help="heap size, e.g. 4G")
    parser.add_argument(
        "--width",
        type=int,
        default=0,
        help="window width (passing it sets has_custom_resolution, like the GUI launchers do)",
    )
    parser.add_argument("--height", type=int, default=0, help="window height")
    parser.add_argument("--world", default="", help="jump straight into this single-player level")
    parser.add_argument(
        "--world-file",
        default="",
        help="file holding the level name (robust for non-ASCII names on Windows)",
    )
    parser.add_argument("--server", default="", help="jump straight onto this server (host:port)")
    parser.add_argument("--natives", default="", help="override the natives directory")
    parser.add_argument("--account-file", default="", help="launcher account json")
    parser.add_argument("--username", default="", help="override the account's player name")
    parser.add_argument(
        "--jvm-property",
        action="append",
        default=[],
        help="extra -DKEY=VALUE, e.g. mcagent.autoConnect=127.0.0.1:25565 (repeatable)",
    )
    parser.add_argument("--jvm-arg", action="append", default=[], help="extra JVM argument")
    parser.add_argument("--game-arg", action="append", default=[], help="extra game argument")
    parser.add_argument("--log", default="", help="file for the game's output")
    parser.add_argument("--dry-run", action="store_true", help="print the command line and stop")
    return parser.parse_args()


def find_java(explicit: str) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get("JAVA_HOME"):
        candidate = Path(os.environ["JAVA_HOME"]) / "bin" / "java.exe"
        if candidate.exists():
            return candidate
    # HMCL keeps its runtimes in a predictable place; the newest wins.
    hmcl_java = Path(os.environ["APPDATA"]) / ".hmcl" / "java"
    candidates = sorted(hmcl_java.glob("**/bin/java.exe"), key=lambda p: p.stat().st_mtime)
    if candidates:
        return candidates[-1]
    return Path("java")


def maven_path(name: str) -> str:
    """group:artifact:version[:classifier] -> libraries/... path."""
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = f"-{parts[3]}" if len(parts) > 3 else ""
    return f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{classifier}.jar"


def library_enabled(
    library: dict, os_name: str = "windows", features: dict[str, bool] | None = None
) -> bool:
    """Evaluate a rule list the way the launcher does.

    Libraries *and* game arguments carry ``rules``; the entries gated behind
    ``features`` (demo user, custom resolution, quick play) must only fire when
    the launcher really asked for that feature. Ignoring them hands the game
    several conflicting quick-play arguments and it refuses to start.
    """
    rules = library.get("rules")
    if not rules:
        return True
    features = features or {}
    allowed = False
    for rule in rules:
        applies = True
        condition = rule.get("os") or {}
        if "name" in condition and condition["name"] != os_name:
            applies = False
        if "arch" in condition and condition["arch"] not in ("x86_64", "amd64"):
            applies = False
        for name, expected in (rule.get("features") or {}).items():
            if bool(features.get(name, False)) is not bool(expected):
                applies = False
        if applies:
            allowed = rule.get("action") == "allow"
    return allowed


def read_account(path: Path) -> dict[str, str]:
    if not path.exists():
        print(f"[launch] no account file at {path}; starting with an offline identity")
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = data[0] if data else {}
    return {
        "name": data.get("displayName") or data.get("username") or "Player",
        "uuid": (data.get("uuid") or "").replace("-", ""),
        "token": data.get("accessToken") or "0",
        "userid": data.get("userid") or "",
    }


def build_command(args: argparse.Namespace) -> tuple[list[str], Path, Path]:
    if args.world_file:
        args.world = Path(args.world_file).read_text(encoding="utf-8").strip()
    minecraft_dir = Path(args.minecraft_dir).resolve()
    version_dir = minecraft_dir / "versions" / args.version
    version_json = version_dir / f"{args.version}.json"
    client_jar = version_dir / f"{args.version}.jar"
    if not version_json.exists():
        raise SystemExit(f"missing version json: {version_json}")
    if not client_jar.exists():
        raise SystemExit(f"missing client jar: {client_jar}")

    data = json.loads(version_json.read_text(encoding="utf-8"))
    libraries = minecraft_dir / "libraries"

    classpath: list[str] = []
    for library in data.get("libraries", []):
        if not library_enabled(library):
            continue
        artifact = (library.get("downloads") or {}).get("artifact") or {}
        path = artifact.get("path") or maven_path(library["name"])
        candidate = libraries / path
        if candidate.exists():
            classpath.append(str(candidate))
        else:
            print(f"[launch] warning: missing library {path}")
    classpath.append(str(client_jar))

    natives = Path(args.natives) if args.natives else version_dir / "natives-windows-x86_64"
    if not natives.exists():
        raise SystemExit(
            f"missing natives directory {natives}; launch the version once through the "
            "GUI launcher so it extracts them, or pass --natives"
        )

    account = read_account(Path(args.account_file) if args.account_file else default_account_file())
    if args.username:
        account["name"] = args.username
        account["uuid"] = offline_uuid(args.username)
        account["token"] = "0"
        account["userid"] = ""
    features = {
        "is_demo_user": False,
        "has_custom_resolution": bool(args.width and args.height),
        "has_quick_plays_support": False,
        "is_quick_play_singleplayer": bool(args.world),
        "is_quick_play_multiplayer": bool(args.server),
        "is_quick_play_realms": False,
    }
    placeholders = {
        "natives_directory": str(natives),
        "classpath": os.pathsep.join(classpath),
        "launcher_name": "mc-agent-launch",
        "launcher_version": "1.0",
        "classpath_separator": os.pathsep,
        "library_directory": str(libraries),
        "auth_player_name": account.get("name", "Player"),
        "auth_uuid": account.get("uuid", ""),
        "auth_access_token": account.get("token", "0"),
        "auth_session": account.get("token", "0"),
        "auth_xuid": account.get("userid", ""),
        "clientid": account.get("userid", ""),
        "version_name": args.version,
        "version_type": data.get("type", "release"),
        "game_directory": str(version_dir),
        "assets_root": str(minecraft_dir / "assets"),
        "assets_index_name": (data.get("assetIndex") or {}).get("id", data.get("assets", "legacy")),
        "resolution_width": str(args.width or 1280),
        "resolution_height": str(args.height or 720),
        "user_properties": "{}",
        "quickPlayPath": "",
        "quickPlaySingleplayer": args.world,
        "quickPlayMultiplayer": args.server,
        "quickPlayRealms": "",
    }

    def substitute(value: str) -> str:
        for key, replacement in placeholders.items():
            value = value.replace("${" + key + "}", replacement)
        return value

    command = [str(find_java(args.java)), f"-Xmx{args.memory}"]
    command.extend(f"-D{prop}" for prop in args.jvm_property)
    for entry in data.get("arguments", {}).get("jvm", []):
        if isinstance(entry, str):
            command.append(substitute(entry))
        elif library_enabled(entry, features=features):
            command.extend(substitute(str(value)) for value in entry.get("value", []))
    command.extend(args.jvm_arg)
    command.append(data["mainClass"])
    for entry in data.get("arguments", {}).get("game", []):
        if isinstance(entry, str):
            command.append(substitute(entry))
        elif library_enabled(entry, features=features):
            command.extend(substitute(str(value)) for value in entry.get("value", []))
    command.extend(args.game_arg)
    return command, version_dir, natives


def default_account_file() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / ".hmcl" / "accounts.json"


def offline_uuid(name: str) -> str:
    """The uuid an offline-mode server derives from a name (Java's UUID.nameUUIDFromBytes)."""
    import hashlib

    digest = bytearray(hashlib.md5(f"OfflinePlayer:{name}".encode("utf-8")).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30  # version 3
    digest[8] = (digest[8] & 0x3F) | 0x80  # IETF variant
    return digest.hex()


def main() -> int:
    args = parse_args()
    if not args.minecraft_dir:
        print("error: pass --minecraft-dir (or set MC_AGENT_MINECRAFT_DIR)", file=sys.stderr)
        return 2
    command, version_dir, natives = build_command(args)

    if args.dry_run:
        print(" ".join(f'"{part}"' if " " in part else part for part in command))
        return 0

    log_path = Path(args.log) if args.log else version_dir / "mc-agent" / "game-launch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")
    print(f"[launch] java: {command[0]}")
    print(f"[launch] game dir: {version_dir}")
    print(f"[launch] natives: {natives}")
    print(f"[launch] log: {log_path}")
    process = subprocess.Popen(
        command,
        cwd=str(version_dir),
        stdout=handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    print(f"[launch] started pid {process.pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
