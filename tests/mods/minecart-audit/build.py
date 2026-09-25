#!/usr/bin/env python3
"""Build the independent minecart audit Fabric mod without Gradle.

Minecraft Java 26.2 ships unobfuscated class files, so this mod is compiled
directly against the client jar, Fabric Loader, Sponge Mixin and the few
Fabric API modules it uses. The same pattern is used by
``interface-mod/build.py``; it keeps this test instrument buildable with a JDK
and a normal game installation, without a Gradle/Maven cache.

Example:
    python tests/mods/minecart-audit/build.py \
        --minecraft-dir "D:/MC/MC_Game/.minecraft" \
        --jdk "C:/Users/me/AppData/Roaming/.hmcl/java/windows-x86_64/mojang-java-runtime-epsilon"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
SRC = PROJECT / "src" / "main" / "java"
RESOURCES = PROJECT / "src" / "main" / "resources"

# Only the modules this mod actually references. Keeping the list small makes
# the missing-dependency error obvious instead of silently pulling the world.
API_MODULES = (
    "fabric-api-base-",
    "fabric-lifecycle-events-v1-",
    "fabric-command-api-v2-",
)


def default_minecraft_dir() -> str:
    """$MC_AGENT_MINECRAFT_DIR, else the first candidate with a 26.2-Fabric install."""
    configured = os.environ.get("MC_AGENT_MINECRAFT_DIR")
    if configured:
        return configured
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / ".minecraft")
    candidates.append(Path(r"D:\MC\MC_Game\.minecraft"))
    candidates.append(Path.home() / ".minecraft")
    for candidate in candidates:
        if (candidate / "versions" / "26.2-Fabric" / "26.2-Fabric.json").is_file():
            return str(candidate)
    for candidate in candidates:
        if any((candidate / "versions").glob("*/[!.]*.json")):
            return str(candidate)
    return str(candidates[0])


def java_major(java: Path) -> int:
    """Major version of a java executable, 0 when it cannot be read."""
    try:
        result = subprocess.run(
            [str(java), "-version"], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    match = re.search(r'version "(\d+)', (result.stderr or "") + (result.stdout or ""))
    return int(match.group(1)) if match else 0


def find_jdk(explicit: str | None) -> Path:
    """A Java 25 JDK: --jdk, MC_AGENT_JAVA, lab metadata, JAVA_HOME, PATH, HMCL.

    Minecraft 26.2 is class-file 69; a JDK 21 on PATH is a common trap, so
    candidates are version-checked instead of taking the first hit.
    """
    candidates: list[Path] = []
    for value in (explicit, os.environ.get("MC_AGENT_JAVA")):
        if value:
            candidates.append(Path(value))
    # tools/lab_server.py records the java it was provisioned with; reuse it
    # when no explicit JDK was given so a lab and its build agree on the JVM.
    for lab_json in sorted((PROJECT.parents[2] / "labs").glob("*/lab.json")):
        try:
            recorded = json.loads(lab_json.read_text(encoding="utf-8")).get("java") or ""
        except (OSError, ValueError):
            continue
        if recorded:
            candidates.append(Path(recorded))
    home = os.environ.get("JAVA_HOME")
    if home:
        candidates.append(Path(home))
    javac = shutil.which("javac")
    if javac:
        candidates.append(Path(javac).resolve().parent.parent)
    appdata = os.environ.get("APPDATA")
    if appdata:
        for pattern in ("*/*/bin/java.exe", "*/*/bin/java"):
            candidates.extend(sorted((Path(appdata) / ".hmcl" / "java").glob(pattern)))

    checked: list[str] = []
    for candidate in candidates:
        java = candidate
        if java.is_dir():
            java = java / "bin" / ("java.exe" if os.name == "nt" else "java")
        if not java.is_file():
            continue
        major = java_major(java)
        checked.append(f"{java} (java {major or '?'})")
        if major >= 25:
            return java.parent.parent
    detail = "; ".join(checked) if checked else "no java candidates"
    raise SystemExit(f"no Java 25+ JDK found ({detail}); pass --jdk or set MC_AGENT_JAVA/JAVA_HOME")


def version_key(path: Path) -> list[int]:
    numbers = [int(part) for part in re.findall(r"\d+", path.name)]
    return numbers or [0]


def extract_api_modules(minecraft_dir: Path, version_dir: Path, existing: list[Path]) -> list[Path]:
    """Find the Fabric API modules to compile against.

    A game installation that ran once keeps remapped modules under
    ``versions/<version>/.fabric/processedMods``; a plain install needs the
    modules extracted from ``mods/fabric-api-*.jar``.
    """
    found: dict[str, Path | None] = {prefix: None for prefix in API_MODULES}
    processed = version_dir / ".fabric" / "processedMods"
    for prefix in API_MODULES:
        matches = sorted(processed.glob(prefix + "*.jar")) if processed.exists() else []
        if matches:
            found[prefix] = max(matches, key=version_key)
    missing = [prefix for prefix, value in found.items() if value is None]
    if not missing:
        return [value for value in found.values() if value is not None]
    candidates = sorted((minecraft_dir / "mods").glob("fabric-api-*.jar"))
    if not candidates:
        raise SystemExit(
            "missing Fabric API modules "
            f"{missing}: install fabric-api in {minecraft_dir / 'mods'} or run the game once"
        )
    target = Path(tempfile.mkdtemp(prefix="mc-agent-audit-api-"))
    with zipfile.ZipFile(max(candidates, key=version_key)) as archive:
        names = archive.namelist()
        for prefix in missing:
            for entry in names:
                if Path(entry).name.startswith(prefix):
                    archive.extract(entry, target)
                    found[prefix] = target / entry
                    break
    still = [prefix for prefix, value in found.items() if value is None]
    if still:
        raise SystemExit(f"missing Fabric API modules {still} in {candidates[-1]}")
    return [value for value in found.values() if value is not None]


def build_classpath(minecraft_dir: Path, version: str) -> list[str]:
    version_dir = minecraft_dir / "versions" / version
    version_json = version_dir / f"{version}.json"
    game_jar = version_dir / f"{version}.jar"
    if not version_json.exists():
        raise SystemExit(f"missing version json: {version_json}")
    if not game_jar.exists():
        raise SystemExit(f"missing game jar: {game_jar}")
    data = json.loads(version_json.read_text(encoding="utf-8"))
    libraries = minecraft_dir / "libraries"
    entries: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        text = str(path)
        if text not in seen and path.exists():
            seen.add(text)
            entries.append(text)

    add(game_jar)
    for library in data.get("libraries", []):
        artifact = (library.get("downloads") or {}).get("artifact") or {}
        path = artifact.get("path")
        if path:
            add(libraries / path)
    for pattern in (
        "net/fabricmc/fabric-loader/*/fabric-loader-*.jar",
        "net/fabricmc/sponge-mixin/*/sponge-mixin-*.jar",
    ):
        matches = sorted(libraries.glob(pattern), key=version_key)
        if not matches:
            raise SystemExit(f"missing compile dependency under {libraries}: {pattern}")
        add(max(matches, key=version_key))
    for module in extract_api_modules(minecraft_dir, version_dir, []):
        add(module)
    return entries


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--minecraft-dir",
        default=default_minecraft_dir(),
        help="path to the .minecraft directory",
    )
    parser.add_argument("--version", default="26.2-Fabric", help="version folder name")
    parser.add_argument("--jdk", default=None, help="path to a JDK 25 installation")
    parser.add_argument("--output", default=str(PROJECT / "dist"), help="output directory")
    arguments = parser.parse_args()

    minecraft_dir = Path(arguments.minecraft_dir).expanduser()
    jdk = find_jdk(arguments.jdk)
    javac = jdk / "bin" / ("javac.exe" if os.name == "nt" else "javac")
    jar = jdk / "bin" / ("jar.exe" if os.name == "nt" else "jar")
    if not javac.exists():
        raise SystemExit(f"javac not found: {javac}")

    classpath = build_classpath(minecraft_dir, arguments.version)
    out_dir = PROJECT / "out"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    sources = sorted(str(path) for path in SRC.rglob("*.java"))
    if not sources:
        raise SystemExit(f"no java sources under {SRC}")
    # javac @argfile must live outside the packaged directory: absolute paths
    # must not end up inside the distributed jar.
    args_dir = Path(tempfile.mkdtemp(prefix="mc-agent-audit-build-"))
    args_file = args_dir / "javac.args"
    args_file.write_text(
        "\n".join(
            [
                "-encoding",
                "UTF-8",
                "-nowarn",
                "-g",
                "-cp",
                os.pathsep.join(classpath),
                "-d",
                str(out_dir),
                *sources,
            ]
        ),
        encoding="utf-8",
    )
    print(f"compiling {len(sources)} sources with {javac}")
    subprocess.run([str(javac), f"@{args_file}"], check=True)

    metadata = json.loads((RESOURCES / "fabric.mod.json").read_text(encoding="utf-8"))
    output = Path(arguments.output)
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"mc-minecart-audit-{metadata['version']}.jar"
    subprocess.run(
        [str(jar), "--create", "--file", str(target), "-C", str(out_dir), ".", "-C", str(RESOURCES), "."],
        check=True,
    )
    print(f"built: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
