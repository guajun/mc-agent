#!/usr/bin/env python3
"""Export the Minecart ROM base world from a live save into a versioned archive.

Maintainer tool: it takes the source save (which is never modified), copies the
machine and the world data, strips player privacy and test residue, rewrites
``level.dat`` with a neutral identity, and writes a byte-for-byte reproducible
ZIP plus the manifest fragment that describes it.

    python examples/minecart-rom/runner/export_world.py \
        --source "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test" \
        --out examples/minecart-rom/dist/minecart-rom-base-1.0.0.zip \
        --version 1.0.0

Only the standard library is used. ``tools/lab_server.py`` supplies the world
copy skip list, so the exporter and the lab importer agree on what a world copy
is.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any, Iterable, NoReturn

RUNNER = Path(__file__).resolve().parent
EXAMPLES = RUNNER.parent
REPO_ROOT = RUNNER.parents[2]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))
import lab_server  # noqa: E402

# Fixed ZIP timestamps make the archive reproducible: the same world always
# produces the same SHA-256.
ZIP_DATE = (2026, 1, 1, 0, 0, 0)

EXPORT_FORMAT = "mc-agent/minecart-rom-export@1"
LEVEL_NAME = "Minecart ROM base"

# The fixture must not contain command blocks; the export proves it by scanning
# every chunk palette instead of trusting the source save.
COMMAND_BLOCKS = {
    "minecraft:command_block",
    "minecraft:chain_command_block",
    "minecraft:repeating_command_block",
}


def die(message: str, code: int = 2) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- NBT codec
#
# The tree keeps every tag id so a parsed level.dat can be written back
# unchanged except for the fields the exporter deliberately touches.
#   compound := {name: (tag, value)}
#   list     := NbtList(element_tag, [value, ...])


class NbtList:
    __slots__ = ("element", "items")

    def __init__(self, element: int, items: list[Any]) -> None:
        self.element = element
        self.items = items


class NbtReader:
    def __init__(self, blob: bytes) -> None:
        self.blob = blob
        self.offset = 0

    def take(self, count: int) -> bytes:
        chunk = self.blob[self.offset : self.offset + count]
        if len(chunk) != count:
            raise ValueError("truncated NBT")
        self.offset += count
        return chunk

    def u1(self) -> int:
        return self.take(1)[0]

    def i1(self) -> int:
        return struct.unpack(">b", self.take(1))[0]

    def i2(self) -> int:
        return struct.unpack(">h", self.take(2))[0]

    def u2(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def i4(self) -> int:
        return struct.unpack(">i", self.take(4))[0]

    def i8(self) -> int:
        return struct.unpack(">q", self.take(8))[0]

    def f4(self) -> float:
        return struct.unpack(">f", self.take(4))[0]

    def f8(self) -> float:
        return struct.unpack(">d", self.take(8))[0]

    def string(self) -> str:
        return self.take(self.u2()).decode("utf-8", "replace")

    def payload(self, tag: int) -> Any:
        if tag == 1:
            return self.i1()
        if tag == 2:
            return self.i2()
        if tag == 3:
            return self.i4()
        if tag == 4:
            return self.i8()
        if tag == 5:
            return self.f4()
        if tag == 6:
            return self.f8()
        if tag == 7:
            return list(self.take(self.i4()))
        if tag == 8:
            return self.string()
        if tag == 9:
            element = self.u1()
            return NbtList(element, [self.payload(element) for _ in range(self.i4())])
        if tag == 10:
            compound: dict[str, Any] = {}
            while True:
                child = self.u1()
                if child == 0:
                    return compound
                name = self.string()
                compound[name] = (child, self.payload(child))
        if tag == 11:
            return [self.i4() for _ in range(self.i4())]
        if tag == 12:
            return [self.i8() for _ in range(self.i4())]
        raise ValueError(f"unknown NBT tag {tag}")

    def root(self) -> tuple[str, Any]:
        tag = self.u1()
        if tag != 10:
            raise ValueError("NBT root is not a compound")
        name = self.string()
        return name, self.payload(tag)


class NbtWriter:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def payload(self, tag: int, value: Any) -> None:
        if tag == 1:
            self.buffer.write(struct.pack(">b", int(value)))
        elif tag == 2:
            self.buffer.write(struct.pack(">h", int(value)))
        elif tag == 3:
            self.buffer.write(struct.pack(">i", int(value)))
        elif tag == 4:
            self.buffer.write(struct.pack(">q", int(value)))
        elif tag == 5:
            self.buffer.write(struct.pack(">f", float(value)))
        elif tag == 6:
            self.buffer.write(struct.pack(">d", float(value)))
        elif tag == 7:
            self.buffer.write(struct.pack(">i", len(value)))
            self.buffer.write(bytes(value))
        elif tag == 8:
            encoded = str(value).encode("utf-8")
            self.buffer.write(struct.pack(">H", len(encoded)))
            self.buffer.write(encoded)
        elif tag == 9:
            assert isinstance(value, NbtList)
            self.buffer.write(struct.pack(">b", value.element))
            self.buffer.write(struct.pack(">i", len(value.items)))
            for item in value.items:
                self.payload(value.element, item)
        elif tag == 10:
            for name, (child_tag, child) in value.items():
                self.buffer.write(struct.pack(">b", child_tag))
                encoded = str(name).encode("utf-8")
                self.buffer.write(struct.pack(">H", len(encoded)))
                self.buffer.write(encoded)
                self.payload(child_tag, child)
            self.buffer.write(b"\x00")
        elif tag == 11:
            self.buffer.write(struct.pack(">i", len(value)))
            for item in value:
                self.buffer.write(struct.pack(">i", int(item)))
        elif tag == 12:
            self.buffer.write(struct.pack(">i", len(value)))
            for item in value:
                self.buffer.write(struct.pack(">q", int(item)))
        else:
            raise ValueError(f"unknown NBT tag {tag}")

    def root(self, name: str, payload: dict[str, Any]) -> bytes:
        self.buffer.write(b"\x0a")
        encoded = str(name).encode("utf-8")
        self.buffer.write(struct.pack(">H", len(encoded)))
        self.buffer.write(encoded)
        self.payload(10, payload)
        return self.buffer.getvalue()


def nbt_get(compound: dict[str, Any], name: str, default: Any = None) -> Any:
    node = compound.get(name)
    return node[1] if node else default


def nbt_set(compound: dict[str, Any], name: str, tag: int, value: Any) -> None:
    compound[name] = (tag, value)


def read_nbt(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return NbtReader(raw).root()


def write_nbt(path: Path, name: str, payload: dict[str, Any]) -> None:
    blob = NbtWriter().root(name, payload)
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as gz:
            gz.write(blob)


# --------------------------------------------------------------------------- world copy


def iter_mca(path: Path) -> Iterable[tuple[int, int, dict[str, Any]]]:
    """Yield ``(chunk_x, chunk_z, chunk_nbt)`` for one entity region file."""
    blob = path.read_bytes()
    if not blob:
        return
    for index in range(1024):
        offset = struct.unpack_from(">I", blob, index * 4)[0] >> 8
        if not offset:
            continue
        start = offset * 4096
        (length,) = struct.unpack_from(">i", blob, start)
        compression = blob[start + 4]
        payload = blob[start + 5 : start + 4 + length]
        if compression == 1:
            payload = gzip.decompress(payload)
        elif compression == 2:
            payload = zlib.decompress(payload)
        _, chunk = NbtReader(payload).root()
        yield index % 32, index // 32, chunk


def find_entities(world: Path) -> list[dict[str, Any]]:
    """List every saved entity in the source save, for the removal report."""
    found: list[dict[str, Any]] = []
    for region in sorted(world.glob("dimensions/*/*/entities/*.mca")):
        for chunk_x, chunk_z, chunk in iter_mca(region):
            entities = nbt_get(chunk, "Entities")
            if not isinstance(entities, NbtList):
                continue
            for entity in entities.items:
                pos = nbt_get(entity, "Pos")
                found.append(
                    {
                        "region": region.relative_to(world).as_posix(),
                        "chunk": [chunk_x, chunk_z],
                        "type": nbt_get(entity, "id"),
                        "pos": pos.items if isinstance(pos, NbtList) else None,
                    }
                )
    return found


def find_command_blocks(world: Path) -> list[dict[str, Any]]:
    """Every command block in every chunk palette of the exported world."""
    found: list[dict[str, Any]] = []
    for region in sorted(world.glob("dimensions/*/*/region/*.mca")):
        for chunk_x, chunk_z, chunk in iter_mca(region):
            sections = nbt_get(chunk, "sections")
            if not isinstance(sections, NbtList):
                continue
            for section in sections.items:
                states = nbt_get(section, "block_states")
                if not isinstance(states, dict):
                    continue
                palette = nbt_get(states, "palette")
                if not isinstance(palette, NbtList):
                    continue
                for entry in palette.items:
                    name = nbt_get(entry, "Name")
                    if name in COMMAND_BLOCKS:
                        found.append(
                            {
                                "region": region.relative_to(world).as_posix(),
                                "chunk": [chunk_x, chunk_z],
                                "block": name,
                            }
                        )
    return found


def copy_world_tree(source: Path, target: Path) -> dict[str, Any]:
    """Copy the save without player data, logs or any saved entity."""
    if not (source / "level.dat").is_file():
        die(f"{source} has no level.dat - that is not a save directory")
    skipped = set(lab_server.WORLD_COPY_SKIP)
    copied = 0
    entity_regions_removed = 0
    for root, dirs, files in os.walk(source):
        root_path = Path(root)
        rel = root_path.relative_to(source)
        if rel.parts and rel.parts[0] in skipped:
            dirs[:] = []
            continue
        if "entities" in rel.parts:
            # Never ship saved entities: the fixture creates them at run time.
            entity_regions_removed += sum(
                1 for entry in root_path.glob("*.mca") if entry.stat().st_size
            )
            dirs[:] = []
            continue
        (target / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            if name in skipped:
                continue
            shutil.copy2(root_path / name, target / rel / name)
            copied += 1
    return {"files": copied, "entity_regions_removed": entity_regions_removed}


# --------------------------------------------------------------------------- export


def sanitize_level_dat(data: dict[str, Any], *, version: str) -> list[str]:
    """Strip the owner's identity, keep the map's gameplay state."""
    changed: list[str] = []
    if "singleplayer_uuid" in data:
        nbt_set(data, "singleplayer_uuid", 11, [0, 0, 0, 0])
        changed.append("singleplayer_uuid")
    if "Player" in data:
        data.pop("Player")
        changed.append("Player")
    if nbt_get(data, "LevelName") != LEVEL_NAME:
        nbt_set(data, "LevelName", 8, LEVEL_NAME)
        changed.append("LevelName")
    nbt_set(data, "LastPlayed", 4, 0)
    changed.append("LastPlayed")
    nbt_set(
        data,
        "mc_agent_export",
        10,
        {
            "format": (8, EXPORT_FORMAT),
            "version": (8, version),
            "exported_at": (8, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        },
    )
    return changed


def deterministic_zip(files: Iterable[Path], base: Path, out: Path) -> dict[str, Any]:
    """Write a reproducible ZIP: sorted, fixed timestamps, fixed mode."""
    out.parent.mkdir(parents=True, exist_ok=True)
    entries = sorted(files)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in entries:
            rel = path.relative_to(base).as_posix()
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            info.create_system = 0
            archive.writestr(info, path.read_bytes())
    return {"entries": len(entries)}


def world_tree_hash(world: Path) -> tuple[str, int, int]:
    entries: list[str] = []
    total = 0
    for path in sorted(world.rglob("*")):
        if not path.is_file() or path.name == "session.lock":
            continue
        rel = path.relative_to(world).as_posix()
        total += path.stat().st_size
        entries.append(f"{rel}\0{sha256_file(path)}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest(), len(entries), total


def export(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    build = Path(args.build).expanduser().resolve() if args.build else out.parent / f".build-{args.version}"
    if build.exists():
        shutil.rmtree(build)
    world = build / "world"
    world.mkdir(parents=True)
    entities = [] if args.keep_entities else find_entities(source)
    copy = copy_world_tree(source, world)
    name, root = read_nbt(world / "level.dat")
    data = nbt_get(root, "Data")
    if not isinstance(data, dict):
        die("level.dat has no Data compound")
    changes = sanitize_level_dat(data, version=args.version)
    write_nbt(world / "level.dat", name, root)
    (world / "level.dat_old").unlink(missing_ok=True)
    (world / "session.lock").unlink(missing_ok=True)
    command_blocks = find_command_blocks(world)
    if command_blocks and not args.allow_command_blocks:
        die(
            "the source save contains command blocks, which the fixture forbids: "
            + json.dumps(command_blocks, ensure_ascii=False)
        )
    world_hash, files, size = world_tree_hash(world)
    export_record = {
        "format": EXPORT_FORMAT,
        "version": args.version,
        "source": str(source),
        "level_name": LEVEL_NAME,
        "world_sha256": world_hash,
        "world_files": files,
        "world_uncompressed_bytes": size,
        "level_dat": {"changed": changes},
        "copy": copy,
        "removed_entities": entities,
        "command_blocks": command_blocks,
        "tool": "examples/minecart-rom/runner/export_world.py",
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (build / "EXPORT.json").write_text(
        json.dumps(export_record, indent=2, ensure_ascii=False) + "\n", "utf-8"
    )
    zip_report = deterministic_zip([p for p in build.rglob("*") if p.is_file()], build, out)
    artifact = {
        "filename": out.name,
        "size": out.stat().st_size,
        "sha256": sha256_file(out),
        "zip_entries": zip_report["entries"],
        "world_sha256": world_hash,
        "world_files": files,
        "world_uncompressed_bytes": size,
        "removed_entities": entities,
        "command_blocks": command_blocks,
    }
    print(json.dumps(artifact, indent=2, ensure_ascii=False))
    if not args.keep_build:
        shutil.rmtree(build)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", required=True, help="the source save directory (read only)")
    parser.add_argument("--out", required=True, help="the .zip to write")
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--build", default="", help="staging directory (default: next to --out)")
    parser.add_argument("--keep-build", action="store_true")
    parser.add_argument("--keep-entities", action="store_true", help="do not strip saved entities")
    parser.add_argument(
        "--allow-command-blocks",
        action="store_true",
        help="do not refuse an export that contains command blocks",
    )
    parser.set_defaults(handler=export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
