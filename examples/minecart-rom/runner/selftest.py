#!/usr/bin/env python3
"""Offline self-tests for the Minecart ROM fixture tooling.

Runs without a game client, a server or the network. It exercises the parts
that must never be wrong quietly: artifact hashing, archive safety, the world
tree hash, the snapshot parser and the ready/reload/premature classifiers.

    python examples/minecart-rom/runner/minecart_rom.py selftest
"""

from __future__ import annotations

import copy
import hashlib
import http.server
import json
import shutil
import socketserver
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

RUNNER = Path(__file__).resolve().parent
EXAMPLES = RUNNER.parent
sys.path.insert(0, str(RUNNER))

import fixture  # noqa: E402
import export_world  # noqa: E402

CHECKS = 0


def check(condition: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        raise AssertionError(f"failed: {label}")


def check_equal(actual, expected, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if actual != expected:
        raise AssertionError(f"failed: {label}: {actual!r} != {expected!r}")


def check_raises(exception, function, label: str) -> None:
    global CHECKS
    CHECKS += 1
    try:
        function()
    except exception:
        return
    raise AssertionError(f"failed: {label}: {exception.__name__} not raised")


# --------------------------------------------------------------------------- tests


def test_nbt_codec(tmp: Path) -> None:
    payload = {
        "Data": (
            10,
            {
                "LevelName": (8, "Minecart ROM test"),
                "LastPlayed": (4, 123456789),
                "singleplayer_uuid": (11, [1, 2, 3, 4]),
                "spawn": (
                    10,
                    {
                        "pos": (9, export_world.NbtList(3, [0, -60, 0])),
                        "pitch": (5, 0.0),
                    },
                ),
                "flag": (1, 1),
            },
        )
    }
    path = tmp / "level.dat"
    export_world.write_nbt(path, "", payload)
    name, parsed = export_world.read_nbt(path)
    check_equal(name, "", "nbt root name")
    check_equal(export_world.nbt_get(parsed, "Data")["LevelName"][1], "Minecart ROM test", "nbt string")
    check_equal(export_world.nbt_get(parsed, "Data")["singleplayer_uuid"][1], [1, 2, 3, 4], "nbt int array")
    spawn = export_world.nbt_get(parsed, "Data")["spawn"][1]
    check_equal(export_world.nbt_get(spawn, "pos").items, [0, -60, 0], "nbt list of ints")
    # sanitising keeps the fake uuid out of the shipped level
    data = export_world.nbt_get(parsed, "Data")
    changes = export_world.sanitize_level_dat(
        data, version="9.9.9", exported_at="2026-01-01T00:00:00Z"
    )
    check("singleplayer_uuid" in changes, "sanitize reports the uuid change")
    check_equal(export_world.nbt_get(data, "singleplayer_uuid"), [0, 0, 0, 0], "uuid zeroed")
    check_equal(export_world.nbt_get(data, "LastPlayed"), 0, "LastPlayed reset")
    check_equal(
        export_world.nbt_get(export_world.nbt_get(data, "mc_agent_export"), "exported_at"),
        "2026-01-01T00:00:00Z",
        "sanitize stores the deterministic export time",
    )


def test_hashes(tmp: Path) -> None:
    world = tmp / "world"
    (world / "data").mkdir(parents=True)
    (world / "level.dat").write_bytes(b"level")
    (world / "data" / "rules.dat").write_bytes(b"rules")
    (world / "session.lock").write_bytes(b"lock")
    first = fixture.world_tree_hash(world)
    (world / "session.lock").write_bytes(b"different lock")
    second = fixture.world_tree_hash(world)
    check_equal(first, second, "session.lock is outside the world hash")
    (world / "extra.dat").write_bytes(b"x")
    third = fixture.world_tree_hash(world)
    check(first[0] != third[0], "world hash changes with files")


def test_item_and_vec_parsers() -> None:
    check_equal(fixture.parse_vec("Minecart has the following entity data: [1.0d, -2.5d, 3.0d]"), [1.0, -2.5, 3.0], "vec parse")
    items = fixture.parse_items('[{count: 2, Slot: 1b, id: "minecraft:dirt"}, {count: 1, Slot: 0b, id: "minecraft:stone"}]')
    check_equal(
        [(item["Slot"], item["id"], item["count"]) for item in items],
        [(0, "minecraft:stone", 1), (1, "minecraft:dirt", 2)],
        "item parse and sort",
    )
    check_equal(
        fixture.uuid_from_text("[I; 1, 2, 3, 4]"),
        "00000001-0000-0002-0000-000300000004",
        "uuid text",
    )
    records = fixture.split_records(
        fixture.CART_MARK + "[1.0d, 2.0d, 3.0d]" + fixture.CART_MARK + "[4.0d, 5.0d, 6.0d]",
        fixture.CART_MARK,
    )
    check_equal(len(records), 2, "concatenated data get answers split")


def test_manifest_validation(tmp: Path) -> None:
    good = fixture.read_json(EXAMPLES / "map-manifest.json")
    path = tmp / "manifest.json"
    path.write_text(json.dumps(good), "utf-8")
    check_equal(fixture.load_manifest(path).sha256, good["artifact"]["sha256"], "manifest loads")
    broken = copy.deepcopy(good)
    broken["format"] = "nope"
    path.write_text(json.dumps(broken), "utf-8")
    check_raises(fixture.FixtureError, lambda: fixture.load_manifest(path), "bad manifest format")
    broken = copy.deepcopy(good)
    broken["artifact"]["sha256"] = "not-a-hash"
    path.write_text(json.dumps(broken), "utf-8")
    check_raises(fixture.FixtureError, lambda: fixture.load_manifest(path), "bad manifest sha")


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):  # keep the test output clean
        pass


def start_http(directory: Path) -> tuple[str, socketserver.TCPServer]:
    handler = lambda *args, **kwargs: _Handler(*args, directory=str(directory), **kwargs)  # noqa: E731
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{server.server_address[1]}/", server


def test_fetch_and_mismatch(tmp: Path) -> None:
    source = tmp / "payload.bin"
    source.write_bytes(b"minecart rom" * 1000)
    url_base, server = start_http(tmp)
    try:
        digest = fixture.sha256_file(source)
        manifest_data = fixture.read_json(EXAMPLES / "map-manifest.json")
        manifest_data["artifact"].update(
            {"filename": "payload.bin", "sha256": digest, "size": source.stat().st_size, "url": url_base + "payload.bin"}
        )
        manifest = fixture.Manifest(path=tmp / "m.json", data=manifest_data)
        result = fixture.fetch_artifact(manifest, tmp / "cache", cold=True)
        check(not result.cache_hit, "cold fetch is not a cache hit")
        check_equal(result.sha256, digest, "fetched sha256")
        cached = fixture.fetch_artifact(manifest, tmp / "cache")
        check(cached.cache_hit, "second fetch is a cache hit")
        # wrong hash: explicit failure, and nothing is left in the cache
        bad = copy.deepcopy(manifest_data)
        bad["artifact"]["sha256"] = "0" * 64
        bad_manifest = fixture.Manifest(path=tmp / "bad.json", data=bad)
        check_raises(
            fixture.HashMismatch,
            lambda: fixture.fetch_artifact(bad_manifest, tmp / "badcache", cold=True),
            "wrong hash fails",
        )
        check(not (tmp / "badcache" / "payload.bin").exists(), "failed download leaves no file")
        # wrong size fails too
        bad["artifact"]["size"] = source.stat().st_size + 1
        check_raises(
            fixture.FixtureError,
            lambda: fixture.fetch_artifact(fixture.Manifest(path=tmp / "s.json", data=bad), tmp / "sizecache", cold=True),
            "wrong size fails",
        )
    finally:
        server.shutdown()
        server.server_close()


def test_archive_safety(tmp: Path) -> None:
    world = tmp / "good" / "world"
    (world / "data").mkdir(parents=True)
    (world / "level.dat").write_bytes(b"level")
    export = {"format": "test", "world_sha256": fixture.world_tree_hash(world)[0]}
    (tmp / "good" / "EXPORT.json").write_text(json.dumps(export), "utf-8")
    good_zip = tmp / "good.zip"
    with zipfile.ZipFile(good_zip, "w") as archive:
        archive.writestr("EXPORT.json", json.dumps(export))
        archive.writestr("world/level.dat", b"level")
    report = fixture.safe_extract(good_zip, tmp / "out-good")
    check_equal(report["entries"], 2, "safe extract counts entries")

    evil = tmp / "evil.zip"
    with zipfile.ZipFile(evil, "w") as archive:
        archive.writestr("../escape.txt", b"nope")
    check_raises(fixture.UnsafeArchive, lambda: fixture.safe_extract(evil, tmp / "out-evil"), "zip slip rejected")
    check(not (tmp / "escape.txt").exists(), "zip slip wrote nothing outside")

    absolute = tmp / "absolute.zip"
    with zipfile.ZipFile(absolute, "w") as archive:
        archive.writestr("/tmp/absolute.txt", b"nope")
    check_raises(fixture.UnsafeArchive, lambda: fixture.safe_extract(absolute, tmp / "out-abs"), "absolute path rejected")

    corrupt = tmp / "corrupt.zip"
    corrupt.write_bytes(b"this is not a zip")
    check_raises(zipfile.BadZipFile, lambda: fixture.safe_extract(corrupt, tmp / "out-corrupt"), "corrupt zip rejected")

    empty = tmp / "empty.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    check_raises(fixture.FixtureError, lambda: fixture.safe_extract(empty, tmp / "out-empty"), "empty zip rejected")

    colon = tmp / "colon.zip"
    with zipfile.ZipFile(colon, "w") as archive:
        archive.writestr("world/file.txt:ads", b"nope")
    check_raises(fixture.UnsafeArchive, lambda: fixture.safe_extract(colon, tmp / "out-colon"), "ADS colon rejected")

    for member in ("world/CON.txt", "world/aux", "world/Com1.dat", "world/name.", "world/name "):
        reserved = tmp / "reserved.zip"
        with zipfile.ZipFile(reserved, "w") as archive:
            archive.writestr(member, b"nope")
        check_raises(
            fixture.UnsafeArchive,
            lambda: fixture.safe_extract(reserved, tmp / "out-reserved"),
            f"reserved Windows name rejected: {member}",
        )

    # symlink members are refused as well
    link = tmp / "link.zip"
    with zipfile.ZipFile(link, "w") as archive:
        info = zipfile.ZipInfo("world/link")
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, b"target")
    check_raises(fixture.UnsafeArchive, lambda: fixture.safe_extract(link, tmp / "out-link"), "symlink rejected")


def mini_artifact(
    tmp: Path,
    *,
    export_overrides: dict | None = None,
    manifest_overrides: dict | None = None,
    world_bytes: bytes = b"level",
) -> tuple[Path, fixture.Manifest, dict, str]:
    """A complete, consistent EXPORT.json + world/ ZIP for consistency tests."""
    staging = tmp / "build"
    world = staging / "world"
    (world / "data").mkdir(parents=True, exist_ok=True)
    (world / "level.dat").write_bytes(world_bytes)
    (world / "data" / "rules.dat").write_bytes(b"")
    world_hash, files, size = fixture.world_tree_hash(world)
    export = {
        "format": "mc-agent/minecart-rom-export@1",
        "version": "9.9.9",
        "world_sha256": world_hash,
        "world_files": files,
        "world_uncompressed_bytes": size,
        "exported_at": "2026-01-01T00:00:00Z",
    }
    export.update(export_overrides or {})
    archive = tmp / f"artifact-{abs(hash(json.dumps(export))) % 10**6}.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("EXPORT.json", json.dumps(export))
        for path in sorted(world.rglob("*")):
            if path.is_file():
                z.writestr("world/" + path.relative_to(world).as_posix(), path.read_bytes())
    manifest_data = fixture.read_json(EXAMPLES / "map-manifest.json")
    manifest_data["artifact"].update(
        {
            "filename": archive.name,
            "sha256": fixture.sha256_file(archive),
            "size": archive.stat().st_size,
        }
    )
    manifest_data["world"] = {"sha256": world_hash, "files": files, "uncompressed_bytes": size}
    manifest_data.update(manifest_overrides or {})
    manifest = fixture.Manifest(path=tmp / "m.json", data=manifest_data)
    return archive, manifest, export, world_hash


def test_artifact_layout(tmp: Path) -> None:
    archive, manifest, export, world_hash = mini_artifact(tmp)
    report = fixture.unpack_artifact(manifest, archive, tmp / "unpacked")
    check_equal(report["world_sha256"], world_hash, "unpacked world hash")
    check(report["world"].endswith("world"), "world dir found")
    check_equal(report["archive_sha256"], manifest.sha256, "archive hash recorded")

    # each inconsistency is refused, not silently accepted
    _, manifest_bad_hash, _, _ = mini_artifact(
        tmp, manifest_overrides={"artifact": {**manifest.data["artifact"], "sha256": "0" * 64}}
    )
    check_raises(
        fixture.HashMismatch,
        lambda: fixture.unpack_artifact(manifest_bad_hash, archive, tmp / "unpacked-hash"),
        "manifest archive hash mismatch rejected",
    )
    bad_bytes_export = {**export, "world_uncompressed_bytes": export["world_uncompressed_bytes"] + 1}
    archive2, manifest2, _, _ = mini_artifact(tmp, export_overrides=bad_bytes_export)
    check_raises(
        fixture.FixtureError,
        lambda: fixture.unpack_artifact(manifest2, archive2, tmp / "unpacked-bytes"),
        "EXPORT.json byte count mismatch rejected",
    )
    _, manifest3, _, _ = mini_artifact(
        tmp,
        manifest_overrides={
            "world": {**manifest.data["world"], "sha256": "0" * 64},
        },
    )
    check_raises(
        fixture.FixtureError,
        lambda: fixture.unpack_artifact(manifest3, archive, tmp / "unpacked-worldhash"),
        "manifest world hash mismatch rejected",
    )
    archive4, manifest4, _, _ = mini_artifact(tmp, export_overrides={"format": "nope"})
    check_raises(
        fixture.FixtureError,
        lambda: fixture.unpack_artifact(manifest4, archive4, tmp / "unpacked-format"),
        "EXPORT.json with an unknown format is rejected",
    )


def synthetic_snapshot(spec: dict, program: list[dict] | None = None) -> dict:
    entries = program if program is not None else spec["program"]["entries"]
    carts = []
    for index, entry in enumerate(entries):
        items = [
            {"Slot": item.get("slot", item.get("Slot", 0)), "id": item["id"], "count": item.get("count", 1)}
            for item in entry["items"]
        ]
        nbt = "{" + ",".join(
            f'count:{item["count"]},Slot:{item["Slot"]}b,id:"{item["id"]}"' for item in items
        ) + "}"
        carts.append(
            {
                "uuid": f"00000000-0000-0000-0000-{index:012d}",
                "pos": list(spec["machine"]["stack"]["rest_pos"]),
                "motion": [0.0, 0.0, 0.0],
                "items": items,
                "nbt": nbt,
                "nbt_sha256": hashlib.sha256(nbt.encode("utf-8")).hexdigest(),
                "spawn_index": index,
            }
        )
    spawn_order = [record["uuid"] for record in carts]
    tick_order = list(spawn_order)
    seat_config = spec["user"].get("hover_seat") or {}
    seat = []
    if seat_config.get("enabled", False):
        seat_nbt = '{"NoGravity":1b,"Invisible":1b,"Marker":1b}'
        seat = [
            {
                "uuid": "11111111-1111-1111-1111-111111111111",
                "pos": list(seat_config["pos"]),
                "nbt": seat_nbt,
                "nbt_sha256": hashlib.sha256(seat_nbt.encode("utf-8")).hexdigest(),
            }
        ]
    interface = {
        "name": "synthetic",
        "dir": "/tmp/synthetic",
        "entities": len(carts) + len(seat),
        "order_hash": "0123456789abcdef",
        "tick": 6000,
        "mod_version": "0.6.0",
        "tick_order": tick_order,
        "seat_present": True if seat else None,
        "cross_check_ok": True,
        "cross_check": [
            {"uuid": record["uuid"], "present": True, "items_match": True, "pos_match": True, "motion_match": True}
            for record in carts
        ],
    }
    return {
        "format": fixture.RECORD_FORMAT,
        "tick_frozen": True,
        "program": entries,
        "spawn_order": spawn_order,
        "rcon_order": list(spawn_order),
        "tick_order": tick_order,
        "tick_order_hash": fixture.tick_order_hash(carts, tick_order),
        "interface": interface,
        "carts": carts,
        "cart_count": len(carts),
        "seat": seat,
        "machine": {"ok": True, "checks": [], "note": 20},
        "user": {
            "name": "Romuser",
            "UUID": "[I; 1, 2, 3, 4]",
            "Pos": "[0d, 0d, 0d]",
            "Rotation": "[0f, 0f]",
            "vehicle_uuid": seat[0]["uuid"] if seat else "",
        },
        "normalized_hash": fixture.normalized_entity_hash(carts),
    }


def test_validate_ready() -> None:
    spec = fixture.load_spec()
    good = synthetic_snapshot(spec)
    check(fixture.validate_ready(spec, good)["ok"], "valid ready snapshot passes")
    no_tick = copy.deepcopy(good)
    no_tick["tick_order"] = None
    no_tick["interface"]["cross_check_ok"] = False
    report = fixture.validate_ready(spec, no_tick)
    check(
        any(problem["check"] == "tick-order-missing" for problem in report["problems"])
        and any(problem["check"] == "interface-cross-check" for problem in report["problems"]),
        "missing tick order fails the ready contract",
    )
    no_seat = copy.deepcopy(good)
    no_seat["seat"] = []
    report = fixture.validate_ready(spec, no_seat)
    check(
        any(problem["check"] == "seat-count" for problem in report["problems"]),
        "a missing hover seat fails the ready contract",
    )
    not_mounted = copy.deepcopy(good)
    not_mounted["user"]["vehicle_uuid"] = ""
    report = fixture.validate_ready(spec, not_mounted)
    check(
        any(problem["check"] == "seat-mount" for problem in report["problems"]),
        "a user not riding the seat fails the ready contract",
    )
    seat_missing_from_snapshot = copy.deepcopy(good)
    seat_missing_from_snapshot["interface"]["seat_present"] = False
    report = fixture.validate_ready(spec, seat_missing_from_snapshot)
    check(
        any(problem["check"] == "seat-in-snapshot" for problem in report["problems"]),
        "a seat absent from the mod snapshot fails the ready contract",
    )
    custom = synthetic_snapshot(spec, program=spec["program"]["entries"][:2])
    check(fixture.validate_ready(spec, custom)["ok"], "a custom program is validated against itself")
    custom_bad = copy.deepcopy(custom)
    custom_bad["program"] = spec["program"]["entries"]
    report = fixture.validate_ready(spec, custom_bad)
    check(not report["ok"], "a custom run with a mismatched program fails")
    off_stack = copy.deepcopy(good)
    off_stack["carts"][0]["pos"][1] += 1.0
    report = fixture.validate_ready(spec, off_stack)
    check(not report["ok"] and report["problems"][0]["check"] == "cart-off-stack", "off-stack cart fails")
    moving = copy.deepcopy(good)
    moving["carts"][1]["motion"] = [0.0, 0.5, 0.0]
    report = fixture.validate_ready(spec, moving)
    check(any(problem["check"] == "cart-moving" for problem in report["problems"]), "moving cart fails")
    wrong_items = copy.deepcopy(good)
    wrong_items["carts"][2]["items"] = [{"Slot": 0, "id": "minecraft:stone", "count": 1}]
    report = fixture.validate_ready(spec, wrong_items)
    check(any(problem["check"] == "cart-inventory" for problem in report["problems"]), "wrong inventory fails")
    unfrozen = copy.deepcopy(good)
    unfrozen["tick_frozen"] = False
    report = fixture.validate_ready(spec, unfrozen)
    check(any(problem["check"] == "world-not-frozen" for problem in report["problems"]), "unfrozen world fails")


class FakeConsole:
    """Duck-typed console for the reload/premature classifier tests."""

    def __init__(self, lab_name: str, spec: dict, snapshot: dict, pid: int = 111):
        self.lab_name = lab_name
        self.spec = spec
        self.snapshot = snapshot
        self.status = {"running": True, "pid": pid}
        self.commands: list[dict] = []

    def cmd(self, command: str, idle: float = 0.4, retries: int = 4) -> str:
        if command == "tick query":
            return "The game is frozen"
        if command.startswith("execute if block"):
            return "Seed: [0]"
        if command.startswith("data get entity Romuser"):
            user = self.snapshot["user"]
            key = command.rsplit(" ", 1)[-1]
            if key == "UUID":
                return f"Romuser has the following entity data: {user['UUID']}"
            return f"Romuser has the following entity data: {user.get(key, '[0d, 0d, 0d]')}"
        if "chest_minecart" in command:
            if command.endswith("Pos"):
                return "".join(
                    fixture.CART_MARK + f"[{record['pos'][0]}d, {record['pos'][1]}d, {record['pos'][2]}d]"
                    for record in self.snapshot["carts"]
                )
            if command.endswith("UUID"):
                return "".join(
                    fixture.CART_MARK + uuid_ints(record["uuid"]) for record in self.snapshot["carts"]
                )
            if command.endswith("Motion"):
                return "".join(
                    fixture.CART_MARK
                    + f"[{record['motion'][0]}d, {record['motion'][1]}d, {record['motion'][2]}d]"
                    for record in self.snapshot["carts"]
                )
            if command.endswith("Items"):
                return "".join(
                    fixture.CART_MARK + "[" + ", ".join(
                        f'{{count: {item["count"]}, Slot: {item["Slot"]}b, id: "{item["id"]}"}}'
                        for item in record["items"]
                    ) + "]"
                    for record in self.snapshot["carts"]
                )
        return ""


def uuid_ints(uuid: str) -> str:
    raw = uuid.replace("-", "")
    values = [int(raw[i : i + 8], 16) for i in range(0, 32, 8)]
    signed = [value - (1 << 32) if value >= (1 << 31) else value for value in values]
    return "[I; " + ", ".join(str(value) for value in signed) + "]"


def test_live_classifier() -> None:
    """Every readiness invariant must fail closed on a modified current state.

    This is the focused regression for the coordinator's P1: a broken machine,
    an unfrozen world, mutated NBT, a changed tick order or a changed order hash
    must all stop the fixture from being reported READY.
    """
    import copy as _copy

    spec = fixture.load_spec()
    ready = synthetic_snapshot(spec)
    ready["server"] = {"pid": 111}
    console = FakeConsole("fake", spec, _copy.deepcopy(ready))
    status = {"running": True, "pid": 111}
    current = {"value": _copy.deepcopy(ready)}

    original_status = fixture.lab_status
    original_snapshot = fixture.take_snapshot
    fixture.lab_status = lambda name: dict(status, lab=name)

    def stub_snapshot(*_args, **_kwargs):
        return _copy.deepcopy(current["value"])

    fixture.take_snapshot = stub_snapshot
    try:
        report = fixture.check_live_state(console, spec, ready)
        check(report["ok"] and report["verdict"] == "READY", "classifier: ready")

        status["pid"] = 999
        report = fixture.check_live_state(console, spec, ready)
        check(report["verdict"] == "FIXTURE_INVALID:RELOAD", "classifier: reload")
        status["pid"] = 111

        def reset(**changes):
            current["value"] = _copy.deepcopy(ready)
            for key, value in changes.items():
                current["value"][key] = value

        reset(machine={"ok": False, "checks": [{"pos": [11, -53, -22], "block": "minecraft:slime_block", "ok": False}]})
        report = fixture.check_live_state(console, spec, ready)
        check(
            not report["ok"] and any(p["check"] == "machine-state" for p in report["problems"]),
            "classifier: broken machine is not ready",
        )

        reset(tick_frozen=False)
        report = fixture.check_live_state(console, spec, ready)
        check(
            any(p["check"] == "world-not-frozen" for p in report["problems"]),
            "classifier: unfrozen world is not ready",
        )

        corrupted = _copy.deepcopy(ready)
        corrupted["carts"][0]["nbt"] = corrupted["carts"][0]["nbt"].replace("count:1", "count:9")
        corrupted["carts"][0]["nbt_sha256"] = hashlib.sha256(
            corrupted["carts"][0]["nbt"].encode("utf-8")
        ).hexdigest()
        reset(carts=corrupted["carts"], normalized_hash=fixture.normalized_entity_hash(corrupted["carts"]))
        report = fixture.check_live_state(console, spec, ready)
        check(
            any(p["check"] == "cart-nbt-changed" for p in report["problems"]),
            "classifier: mutated cart NBT is not ready",
        )

        reset(tick_order=list(reversed(ready["tick_order"])), tick_order_hash="deadbeefdeadbeef")
        report = fixture.check_live_state(console, spec, ready)
        check(
            any(p["check"] == "tick-order-changed" for p in report["problems"]),
            "classifier: changed tick order is not ready",
        )

        interface = _copy.deepcopy(ready["interface"])
        interface["order_hash"] = "ffffffffffffffff"
        reset(interface=interface)
        report = fixture.check_live_state(console, spec, ready)
        check(
            any(p["check"] == "interface-order-hash-changed" for p in report["problems"]),
            "classifier: changed interface order hash is not ready",
        )

        reset(rcon_order=list(reversed(ready["rcon_order"])))
        report = fixture.check_live_state(console, spec, ready)
        check(
            any(p["check"] == "rcon-order-changed" for p in report["problems"]),
            "classifier: changed selector observation is reported",
        )

        reset(tick_order=None, interface=None, tick_order_hash=None)
        report = fixture.check_live_state(console, spec, ready)
        check(
            not report["ok"]
            and any(p["check"] == "tick-order-unavailable" for p in report["problems"]),
            "classifier: a missing current tick order is not ready",
        )
        ready_without_tick = copy.deepcopy(ready)
        ready_without_tick["tick_order"] = None
        current["value"] = copy.deepcopy(ready_without_tick)
        report = fixture.check_live_state(console, spec, ready_without_tick)
        check(
            not report["ok"] and any(p["check"] == "tick-order-missing" for p in report["problems"]),
            "classifier: a ready record without tick-order evidence is not ready",
        )

        reset(seat=[])
        report = fixture.check_live_state(console, spec, ready)
        check(
            not report["ok"] and any(p["check"] == "seat-count" for p in report["problems"]),
            "classifier: a missing hover seat is not ready",
        )

        changed_seat = copy.deepcopy(ready["seat"])
        changed_seat[0]["nbt_sha256"] = "0" * 64
        reset(seat=changed_seat)
        report = fixture.check_live_state(console, spec, ready)
        check(
            any(p["check"] == "seat-changed" for p in report["problems"]),
            "classifier: a changed hover seat is not ready",
        )

        reset(carts=list(ready["carts"][:2]), normalized_hash=fixture.normalized_entity_hash(ready["carts"][:2]))
        report = fixture.check_live_state(console, spec, ready)
        check(report["verdict"] == "PREMATURE_OUTPUT", "classifier: missing cart is premature output")
    finally:
        fixture.lab_status = original_status
        fixture.take_snapshot = original_snapshot


def test_evidence_helpers() -> None:
    """The gate evidence helpers, against the committed calibration records."""
    import evidence

    spec = fixture.load_spec()
    records = EXAMPLES / "calibration" / "records"
    runs = evidence.find_ready_snapshots(records)
    check(len(runs) >= 3, "at least three ready records committed")
    rows = evidence.init_runs(runs, spec, len(spec["program"]["entries"]))
    check(len({row["state_hash"] for row in rows}) == 1, "state hash equal across committed runs")
    check(len({row["order_hash"] for row in rows}) == 1, "order hash equal across committed runs")
    check(all(row["order_source"] == "interface-snapshot" for row in rows), "committed runs carry the tick-order evidence")
    check(all(len(row["tick_order"] or []) == 3 for row in rows), "tick order covers every cart")
    check(all(row["ready"] and not row["early_output"] for row in rows), "committed runs are ready")
    check(all(row["entity_count"] == 3 for row in rows), "three carts per run")
    check(all(row["inventory_total"] == 6 for row in rows), "inventory totals match the program")
    check(all(len(row["order_hash"]) == 16 for row in rows), "order hash is 16 hex")
    snapshot = runs[-1]["snapshot"]
    identity = evidence.player_identity(spec, snapshot)
    check(identity["facing_target"] is True, "the committed user faces the note block")
    check(identity["server_vantage_uuid"] == identity["uuid"], "server vantage uuid equals task uuid")
    check(len(identity["uuid"]) == 36, "uuid is canonically formatted")
    check(evidence.inventory_total(snapshot["carts"]) == 6, "inventory total helper")
    check(evidence.early_output(snapshot, spec, 3) is False, "early-output helper")
    check(
        evidence.early_output({**snapshot, "carts": snapshot["carts"][:2]}, spec, 3) is True,
        "short stack is early output",
    )
    off_stack = copy.deepcopy(snapshot)
    off_stack["carts"][0]["pos"] = [v for v in off_stack["carts"][0]["pos"]]
    off_stack["carts"][0]["pos"][0] += 0.5
    check(
        evidence.early_output(off_stack, spec, 3) is True,
        "a cart nudged off the rest position is early output",
    )
    check_equal(evidence.url_pin("https://raw.githubusercontent.com/o/r/" + "a" * 40 + "/x.zip"), "commit", "commit URL pin")
    check_equal(evidence.url_pin("https://example.invalid/map.zip"), "mutable", "mutable URL pin")
    # fail closed when the source save was not provided
    rebuild = evidence.cleanup_rebuild(None)
    check(rebuild["source_world_untouched"] is False, "no source world means untouched=false")


def test_export_reproducible(tmp: Path) -> None:
    """Two exports of an unchanged source must be byte-identical."""
    import argparse
    import contextlib
    import io

    source = tmp / "synthetic-save"
    source.mkdir()
    payload = {
        "Data": (
            10,
            {
                "LevelName": (8, "synthetic"),
                "LastPlayed": (4, 1),
                "singleplayer_uuid": (11, [1, 2, 3, 4]),
            },
        )
    }
    export_world.write_nbt(source / "level.dat", "", payload)

    def run(out: Path, exported_at: str = "") -> Path:
        with contextlib.redirect_stdout(io.StringIO()):
            export_world.export(
                argparse.Namespace(
                    source=str(source),
                    out=str(out),
                    version="test",
                    build="",
                    keep_build=False,
                    keep_entities=False,
                    allow_command_blocks=False,
                    exported_at=exported_at,
                )
            )
        return out

    first = run(tmp / "first.zip")
    second = run(tmp / "second.zip")
    check_equal(
        fixture.sha256_file(first), fixture.sha256_file(second), "two exports are byte-identical"
    )
    with zipfile.ZipFile(first) as archive:
        export = json.loads(archive.read("EXPORT.json"))
    check_equal(export["exported_at"], export_world.DEFAULT_EXPORTED_AT, "deterministic export time")
    with zipfile.ZipFile(first) as archive:
        archive.extractall(tmp / "first-extracted")
    _, level = export_world.read_nbt(tmp / "first-extracted" / "world" / "level.dat")
    stamp = export_world.nbt_get(
        export_world.nbt_get(export_world.nbt_get(level, "Data"), "mc_agent_export"), "exported_at"
    )
    check_equal(stamp, export_world.DEFAULT_EXPORTED_AT, "level.dat carries the deterministic time")
    other = run(tmp / "other.zip", exported_at="2027-02-02T00:00:00Z")
    check(
        fixture.sha256_file(other) != fixture.sha256_file(first),
        "an explicit exported-at changes the artifact",
    )


def test_mod_pins(tmp: Path) -> None:
    manifest_data = fixture.read_json(EXAMPLES / "map-manifest.json")
    carpet = next(mod for mod in manifest_data["mods"] if mod["name"] == "carpet")
    pinned = copy.deepcopy(manifest_data)
    pinned["mods"] = [carpet]
    manifest = fixture.Manifest(path=tmp / "m.json", data=pinned)
    lab = tmp / "pins-lab"
    lab.mkdir()
    (lab / "lab.json").write_text(
        json.dumps({"mods": [{"name": "fabric-carpet.jar", "sha256": carpet["sha256"]}]}),
        "utf-8",
    )
    original_labs = fixture.LABS
    fixture.LABS = tmp
    try:
        check(fixture.check_mod_pins("pins-lab", manifest)["ok"], "matching mod pins pass")
        (lab / "lab.json").write_text(json.dumps({"mods": []}), "utf-8")
        check_raises(
            fixture.FixtureError,
            lambda: fixture.check_mod_pins("pins-lab", manifest),
            "missing pinned mods fail closed",
        )
    finally:
        fixture.LABS = original_labs

    # the pin downloader verifies bytes (a local file:// URL keeps it offline)
    jar = tmp / "pinned-carpet.jar"
    jar.write_bytes(b"jar-bytes")
    pinned = copy.deepcopy(manifest_data)
    pinned["mods"] = [
        {
            "name": "carpet",
            "version": "x",
            "sha256": fixture.sha256_file(jar),
            "url": jar.as_uri(),
            "filename": "pinned-carpet.jar",
            "required": True,
        }
    ]
    paths = fixture.ensure_pinned_mods(fixture.Manifest(path=tmp / "p.json", data=pinned), tmp / "cache")
    check_equal(paths, [tmp / "cache" / "pinned-carpet.jar"], "pinned mod cached")
    bad = copy.deepcopy(pinned)
    bad["mods"][0]["sha256"] = "0" * 64
    check_raises(
        fixture.HashMismatch,
        lambda: fixture.ensure_pinned_mods(fixture.Manifest(path=tmp / "b.json", data=bad), tmp / "cache"),
        "wrong pinned mod hash rejected",
    )


def test_challenge_generator(tmp: Path) -> None:
    """A challenge is deterministic per seed, fresh per seed, and never an answer."""
    import argparse
    import contextlib
    import io

    import minecart_rom

    def generate(seed: int, name: str):
        out = tmp / name
        with contextlib.redirect_stdout(io.StringIO()):
            minecart_rom.cmd_challenge(
                argparse.Namespace(
                    seed=str(seed), out=str(out), carts=4, max_slots=3, max_count=4, pool=""
                )
            )
        return json.loads(out.read_text("utf-8"))

    first = generate(1234, "challenge-a.json")
    again = generate(1234, "challenge-b.json")
    other = generate(1235, "challenge-c.json")
    check(first["program_sha256"] == again["program_sha256"], "challenge is deterministic per seed")
    check(first["program_sha256"] != other["program_sha256"], "a fresh seed makes a fresh challenge")
    check(len(first["carts"]) == 4, "challenge cart count honoured")
    check(all(cart["items"] for cart in first["carts"]), "every challenge cart has items")
    check("answer" not in json.dumps(first).lower(), "the generator never emits an answer")


def test_import_helpers(tmp: Path) -> None:
    lab = tmp / "portlab"
    lab.mkdir()
    (lab / "rcon.json").write_text(json.dumps({"host": "127.0.0.1", "port": 1, "password": "x"}), "utf-8")
    (lab / "lab.json").write_text(json.dumps({"name": "portlab"}), "utf-8")
    (lab / "server.properties").write_text(
        "rcon.port=1\nserver-port=2\nquery.port=2\n", "utf-8", newline="\n"
    )
    fixture.patch_lab_ports(lab, 27150, 27151)
    check_equal(fixture.read_json(lab / "rcon.json")["port"], 27150, "rcon port patched")
    text = (lab / "server.properties").read_text()
    check("rcon.port=27150" in text and "server-port=27151" in text, "properties patched")
    check_raises(fixture.FixtureError, lambda: fixture.lab_dir("bad/name"), "lab name sanitised")


# --------------------------------------------------------------------------- runner


def run(keep: bool = False, verbose: bool = False) -> int:
    tmp = Path(tempfile.mkdtemp(prefix="minecart-rom-selftest-"))
    tests = [
        ("nbt codec", lambda: test_nbt_codec(tmp)),
        ("world hash", lambda: test_hashes(tmp)),
        ("parsers", test_item_and_vec_parsers),
        ("manifest", lambda: test_manifest_validation(tmp)),
        ("fetch/hash", lambda: test_fetch_and_mismatch(tmp)),
        ("archive safety", lambda: test_archive_safety(tmp)),
        ("artifact layout", lambda: test_artifact_layout(tmp)),
        ("validate-ready", test_validate_ready),
        ("live classifier", test_live_classifier),
        ("evidence helpers", test_evidence_helpers),
        ("challenge", lambda: test_challenge_generator(tmp)),
        ("export reproducible", lambda: test_export_reproducible(tmp)),
        ("mod pins", lambda: test_mod_pins(tmp)),
        ("lab helpers", lambda: test_import_helpers(tmp)),
    ]
    failures = 0
    for name, test in tests:
        try:
            test()
            print(f"ok   {name}")
        except Exception as error:  # noqa: BLE001 - the point is to report every failure
            failures += 1
            print(f"FAIL {name}: {error}")
            if verbose:
                import traceback

                traceback.print_exc()
    print(f"\n{CHECKS} checks, {len(tests) - failures}/{len(tests)} groups ok")
    if keep:
        print(f"temporary files: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(run())
