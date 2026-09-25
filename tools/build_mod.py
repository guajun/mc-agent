#!/usr/bin/env python3
"""Build a small Fabric mod for a lab server without Gradle.

Minecraft 26.2 ships unobfuscated class files, so a server-side mod can be
compiled directly against the jars a lab has already downloaded. This is the
supported path for an agent that wants to add a logger or an observation mod:

    python tools/build_mod.py --source examples/smoke-mod --lab lab-a \
        --out labs/build/smoke-mod.jar --version 0.1.0

The classpath is the lab's unpacked server jar plus every jar under the lab's
``libraries/`` and ``.fabric/processedMods/`` directories (the Fabric API
modules), plus the jars in the lab's ``mods/`` directory. Sources come from
``<source>/src/main/java`` and resources from ``<source>/src/main/resources``;
``${version}`` in text resources is replaced with ``--version``.

The jar is deterministic: entries are sorted, timestamps are fixed, and the
default compression is store. Rebuilding identical sources produces identical
bytes; changing one string changes the hash (and, with store, often not the
size). A sidecar ``<out>.build.json`` records the deployed SHA-256, the JDK and
javac used, and the source hashes, which is what the lab audit trail should
reference.

Fabric loads mods at server start. Deploy, then start; there is no hot reload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parent.parent
LABS = ROOT / "labs"
FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
TEXT_SUFFIXES = {".json", ".properties", ".txt", ".mcmeta", ".yml", ".yaml"}


def say(message: str = "") -> None:
    try:
        print(message, flush=True)
    except OSError:
        pass  # a closed pipe (for example `| head`) is not a build failure


def die(message: str, code: int = 2) -> NoReturn:
    print(f"error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def human_size(count: int) -> str:
    value = float(count)
    for unit in ("B", "KB", "MB"):
        if value < 1024 or unit == "MB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} MB"


def resolve_lab(name: str) -> tuple[Path, dict[str, Any]]:
    lab = LABS / name
    try:
        state = json.loads((lab / "lab.json").read_text("utf-8"))
    except FileNotFoundError:
        die(f"no lab called {name!r} under {rel(LABS)} - provision it first")
    except (OSError, ValueError) as error:
        die(f"{rel(lab / 'lab.json')} is unreadable: {error}")
    return lab, state


def find_javac(cli_value: str, lab_state: dict[str, Any]) -> tuple[str, str]:
    """Locate javac: --jdk, JAVA_HOME, the JDK recorded in lab.json, then PATH."""
    name = "javac.exe" if os.name == "nt" else "javac"
    candidates: list[Path] = []
    for candidate in (cli_value, os.environ.get("JAVA_HOME", ""), lab_state.get("jdk", "")):
        candidate = (candidate or "").strip().strip('"')
        if candidate:
            candidates.append(Path(candidate))
    found = shutil.which("javac")
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file() and candidate.name.lower() == name:
            return str(candidate), capture_version(str(candidate))
        tool = candidate / "bin" / name
        if tool.is_file():
            return str(tool), capture_version(str(tool))
    die("no javac found: pass --jdk PATH, set JAVA_HOME, record --jdk at provision, or put javac on PATH")


def capture_version(javac: str) -> str:
    try:
        result = subprocess.run([javac, "-version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        return f"(unavailable: {error})"
    return (result.stdout or result.stderr).strip().splitlines()[0] if result.stdout or result.stderr else ""


def lab_classpath(lab: Path, mc: str, extra: list[str]) -> list[Path]:
    """Server jar + libraries + processed Fabric API modules + lab mods."""
    server_candidates = sorted((lab / "versions").glob(f"*/server-{mc}.jar"))
    if not server_candidates:
        server_candidates = sorted((lab / "versions").glob("*/server-*.jar"))
    if not server_candidates:
        die(f"{rel(lab)} has no versions/*/server-*.jar - start the lab once so Fabric downloads it")
    entries: list[Path] = [server_candidates[0]]
    for directory in (lab / "libraries", lab / ".fabric" / "processedMods", lab / "mods"):
        if directory.is_dir():
            entries.extend(sorted(directory.rglob("*.jar")))
    for value in extra:
        path = Path(value).expanduser()
        if not path.is_file():
            die(f"--classpath-extra {value} is not a jar file")
        entries.append(path.resolve())
    return [path for path in entries if path.is_file()]


def source_files(source: Path) -> list[Path]:
    java = source / "src" / "main" / "java"
    if not java.is_dir():
        die(f"{rel(source)} has no src/main/java - that is not a mod source tree")
    files = sorted(java.rglob("*.java"))
    if not files:
        die(f"{rel(java)} holds no .java files")
    return files


def resource_files(source: Path) -> list[Path]:
    resources = source / "src" / "main" / "resources"
    if not resources.is_dir():
        die(f"{rel(source)} has no src/main/resources (a Fabric mod needs fabric.mod.json)")
    files = sorted(path for path in resources.rglob("*") if path.is_file())
    if not any(path.name == "fabric.mod.json" for path in files):
        die(f"{rel(resources)} has no fabric.mod.json - a Fabric mod needs one")
    return files


def source_manifest(source: Path, javas: list[Path], resources: list[Path]) -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    for path in javas + resources:
        manifest.append({"path": path.relative_to(source).as_posix(), "sha256": sha256_file(path)})
    return manifest


def classpath_digest(entries: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(entries, key=lambda item: str(item).lower()):
        digest.update(str(path.resolve()).lower().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def compile_sources(
    javac: str, source: Path, javas: list[Path], classpath: list[Path], classes: Path, release: int
) -> None:
    """javac through an @argfile: no command-line length limit on Windows."""
    classes.mkdir(parents=True, exist_ok=True)
    argfile = classes.parent / "javac.args"
    # javac argfiles treat backslash as an escape, so paths go in with forward
    # slashes (Java accepts them on Windows) and quotes for spaces/colons.
    classpath_line = os.pathsep.join(path.as_posix() for path in classpath)
    lines = [
        "-encoding",
        "UTF-8",
        "-nowarn",
        "--release",
        str(release),
        "-cp",
        '"' + classpath_line + '"',
        "-d",
        f'"{classes.as_posix()}"',
    ]
    lines.extend(f'"{path.as_posix()}"' for path in javas)
    argfile.write_text("\n".join(lines) + "\n", "utf-8", newline="\n")
    say(f"  javac @{rel(argfile)} ({len(javas)} sources, {len(classpath)} classpath entries)")
    try:
        result = subprocess.run(
            [javac, f"@{argfile}"],
            cwd=str(source),
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as error:
        die(f"javac could not run: {error}", 1)
    output = (result.stdout or "") + (result.stderr or "")
    if output.strip():
        say(output.rstrip())
    if result.returncode != 0:
        die(f"build failed: javac exited {result.returncode}; the compiler output above is the error", 1)


def substituted_bytes(path: Path, version: str) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        return data.replace(b"${version}", version.encode("utf-8"))
    return data


def pack_jar(
    out_jar: Path,
    classes: Path,
    source: Path,
    resources: list[Path],
    version: str,
    compression: str,
) -> None:
    out_jar.parent.mkdir(parents=True, exist_ok=True)
    method = zipfile.ZIP_DEFLATED if compression == "deflate" else zipfile.ZIP_STORED
    written: set[str] = set()
    with zipfile.ZipFile(out_jar, "w", compression=method) as archive:
        for path in sorted(classes.rglob("*")):
            if path.is_file():
                arcname = path.relative_to(classes).as_posix()
                if arcname in written:
                    die(f"duplicate jar entry {arcname} (classes and resources collide)")
                written.add(arcname)
                info = zipfile.ZipInfo(arcname, date_time=FIXED_TIMESTAMP)
                info.compress_type = method
                info.create_system = 0
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes())
        for path in resources:
            arcname = path.relative_to(source / "src" / "main" / "resources").as_posix()
            if arcname in written:
                die(f"duplicate jar entry {arcname} (classes and resources collide)")
            written.add(arcname)
            info = zipfile.ZipInfo(arcname, date_time=FIXED_TIMESTAMP)
            info.compress_type = method
            info.create_system = 0
            info.external_attr = 0o644 << 16
            archive.writestr(info, substituted_bytes(path, version))


def build(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    out_jar = Path(args.out).expanduser()
    out_jar = out_jar if out_jar.is_absolute() else (ROOT / out_jar).resolve()
    if not source.is_dir():
        die(f"--source {args.source} is not a directory")

    lab, state = resolve_lab(args.lab)
    mc = args.mc or str(state.get("minecraft") or "26.2")
    version = args.version or "0.1.0"
    javac, javac_version = find_javac(args.jdk, state)

    javas = source_files(source)
    resources = resource_files(source)
    classpath = lab_classpath(lab, mc, args.classpath_extra or [])
    say(f"building {rel(source)} for lab {args.lab} (minecraft {mc})")
    say(f"  javac {javac} ({javac_version})")

    with tempfile.TemporaryDirectory(prefix="mc-agent-mod-build-") as work:
        classes = Path(work) / "classes"
        compile_sources(javac, source, javas, classpath, classes, args.release)
        pack_jar(out_jar, classes, source, resources, version, args.compression)

    digest = sha256_file(out_jar)
    size = out_jar.stat().st_size
    manifest = source_manifest(source, javas, resources)
    server_jar = classpath[0]
    metadata = {
        "jar": str(out_jar),
        "sha256": digest,
        "size": size,
        "version": version,
        "compression": args.compression,
        "lab": args.lab,
        "minecraft": mc,
        "jdk": str(Path(javac).parent.parent),
        "javac": javac,
        "javacVersion": javac_version,
        "release": args.release,
        "serverJar": str(server_jar),
        "serverJarSha256": sha256_file(server_jar),
        "classpathEntries": len(classpath),
        "classpathSha256": classpath_digest(classpath),
        "sources": manifest,
        "builtAt": time.time(),
    }
    metadata_path = out_jar.with_name(out_jar.name + ".build.json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", "utf-8")

    if args.json:
        say(json.dumps(metadata, indent=2))
    else:
        say(f"built {rel(out_jar)} ({human_size(size)}, sha256 {digest[:16]}...)")
        say(f"  version {version}, compression {args.compression}, release {args.release}")
        say(f"  {len(classpath)} classpath entries from lab {args.lab}")
        say(f"  metadata {rel(metadata_path)}")
        say(f"deploy it with: python tools/lab_server.py provision --name {args.lab} --mod-jar {rel(out_jar)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a small Fabric mod against a provisioned lab's jars (no Gradle).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python tools/build_mod.py --source examples/smoke-mod --lab lab-a --out labs/build/smoke-mod.jar\n"
            "  python tools/build_mod.py --source my-mod --lab lab-a --out labs/build/logger.jar --version 1.2\n"
        ),
    )
    parser.add_argument("--source", required=True, help="mod source tree (src/main/java + resources)")
    parser.add_argument("--lab", required=True, help="a provisioned lab whose jars become the classpath")
    parser.add_argument("--out", required=True, help="output .jar path")
    parser.add_argument("--version", default="", help="mod version substituted for ${version} (default 0.1.0)")
    parser.add_argument("--mc", default="", help="Minecraft version (default: from lab.json)")
    parser.add_argument("--jdk", default="", help="JDK home or javac; default JAVA_HOME, lab.json jdk, or PATH")
    parser.add_argument("--release", type=int, default=25, help="javac --release (default 25)")
    parser.add_argument(
        "--compression",
        choices=("store", "deflate"),
        default="store",
        help="jar entry compression; store is deterministic and keeps same-length edits same-size",
    )
    parser.add_argument(
        "--classpath-extra", action="append", default=[], help="additional jar for the classpath (repeatable)"
    )
    parser.add_argument("--json", action="store_true", help="print the full build metadata as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return build(args)


if __name__ == "__main__":
    raise SystemExit(main())
