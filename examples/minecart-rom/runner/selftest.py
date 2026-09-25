#!/usr/bin/env python3
"""Offline self-tests for the Minecart ROM fixture tooling.

Runs without a game client, a server or the network. It exercises the parts
that must never be wrong quietly: artifact hashing, archive safety, the world
tree hash, the snapshot parser and the ready/reload/premature classifiers.

    python examples/minecart-rom/runner/minecart_rom.py selftest
"""

from __future__ import annotations

import copy
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
    changes = export_world.sanitize_level_dat(data, version="9.9.9")
    check("singleplayer_uuid" in changes, "sanitize reports the uuid change")
    check_equal(export_world.nbt_get(data, "singleplayer_uuid"), [0, 0, 0, 0], "uuid zeroed")
    check_equal(export_world.nbt_get(data, "LastPlayed"), 0, "LastPlayed reset")


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

    # symlink members are refused as well
    link = tmp / "link.zip"
    with zipfile.ZipFile(link, "w") as archive:
        info = zipfile.ZipInfo("world/link")
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, b"target")
    check_raises(fixture.UnsafeArchive, lambda: fixture.safe_extract(link, tmp / "out-link"), "symlink rejected")


def test_artifact_layout(tmp: Path) -> None:
    staging = tmp / "build"
    world = staging / "world"
    (world / "data").mkdir(parents=True)
    (world / "level.dat").write_bytes(b"level")
    (world / "data" / "rules.dat").write_bytes(b"")
    world_hash = fixture.world_tree_hash(world)[0]
    (staging / "EXPORT.json").write_text(json.dumps({"world_sha256": world_hash}), "utf-8")
    archive = tmp / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("EXPORT.json", (staging / "EXPORT.json").read_text())
        z.writestr("world/level.dat", b"level")
        z.writestr("world/data/rules.dat", b"")
    manifest_data = fixture.read_json(EXAMPLES / "map-manifest.json")
    manifest_data["artifact"].update(
        {"filename": "artifact.zip", "sha256": fixture.sha256_file(archive), "size": archive.stat().st_size}
    )
    manifest = fixture.Manifest(path=tmp / "m.json", data=manifest_data)
    report = fixture.unpack_artifact(manifest, archive, tmp / "unpacked")
    check_equal(report["world_sha256"], world_hash, "unpacked world hash")
    check(report["world"].endswith("world"), "world dir found")
    # a tampered EXPORT.json hash is refused
    staging2 = tmp / "build2"
    world2 = staging2 / "world"
    world2.mkdir(parents=True)
    (world2 / "level.dat").write_bytes(b"level")
    (staging2 / "EXPORT.json").write_text(json.dumps({"world_sha256": "0" * 64}), "utf-8")
    archive2 = tmp / "artifact2.zip"
    with zipfile.ZipFile(archive2, "w") as z:
        z.writestr("EXPORT.json", (staging2 / "EXPORT.json").read_text())
        z.writestr("world/level.dat", b"level")
    manifest_data["artifact"].update(
        {"filename": "artifact2.zip", "sha256": fixture.sha256_file(archive2), "size": archive2.stat().st_size}
    )
    check_raises(
        fixture.FixtureError,
        lambda: fixture.unpack_artifact(
            fixture.Manifest(path=tmp / "m2.json", data=manifest_data), archive2, tmp / "unpacked2"
        ),
        "EXPORT.json hash mismatch rejected",
    )


def synthetic_snapshot(spec: dict, nbt: str = "{items:[{Slot:0b,id:\"minecraft:stone\",count:1b}]}") -> dict:
    carts = []
    for index, entry in enumerate(spec["program"]["entries"]):
        carts.append(
            {
                "uuid": f"00000000-0000-0000-0000-{index:012d}",
                "pos": list(spec["machine"]["stack"]["rest_pos"]),
                "motion": [0.0, 0.0, 0.0],
                "items": [
                    {"Slot": item.get("slot", 0), "id": item["id"], "count": item.get("count", 1)}
                    for item in entry["items"]
                ],
                "spawn_index": index,
            }
        )
    return {
        "format": fixture.RECORD_FORMAT,
        "tick_frozen": True,
        "spawn_order": [record["uuid"] for record in carts],
        "entity_order": [record["uuid"] for record in carts],
        "carts": carts,
        "cart_count": len(carts),
        "machine": {"ok": True, "checks": [], "note": 20},
        "user": {"name": "Romuser", "UUID": "[I; 1, 2, 3, 4]", "Pos": "[0d, 0d, 0d]", "Rotation": "[0f, 0f]"},
        "normalized_hash": fixture.normalized_entity_hash(carts),
    }


def test_validate_ready() -> None:
    spec = fixture.load_spec()
    good = synthetic_snapshot(spec)
    check(fixture.validate_ready(spec, good)["ok"], "valid ready snapshot passes")
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
    spec = fixture.load_spec()
    ready = synthetic_snapshot(spec)
    ready["server"] = {"pid": 111}
    console = FakeConsole("fake", spec, copy.deepcopy(ready))
    # the fake console reports the same machine checks and the frozen state
    original = fixture.lab_status
    fixture.lab_status = lambda name: dict(console.status, lab=name)
    try:
        report = fixture.check_live_state(console, spec, ready)
        check(report["ok"] and report["verdict"] == "READY", "classifier: ready")
        console.status["pid"] = 999
        report = fixture.check_live_state(console, spec, ready)
        check(report["verdict"] == "FIXTURE_INVALID:RELOAD", "classifier: reload")
        console.status["pid"] = 111
        console.snapshot["carts"] = console.snapshot["carts"][:2]
        console.snapshot["entity_order"] = [record["uuid"] for record in console.snapshot["carts"]]
        console.snapshot["normalized_hash"] = fixture.normalized_entity_hash(console.snapshot["carts"])
        report = fixture.check_live_state(console, spec, ready)
        check(report["verdict"] == "PREMATURE_OUTPUT", "classifier: missing cart is premature output")
    finally:
        fixture.lab_status = original


def test_evidence_helpers() -> None:
    """The gate evidence helpers, against the committed calibration records."""
    import evidence

    spec = fixture.load_spec()
    records = EXAMPLES / "calibration" / "records"
    runs = evidence.find_ready_snapshots(records)
    check(len(runs) >= 3, "at least three ready records committed")
    rows = evidence.init_runs(runs, len(spec["program"]["entries"]))
    check(len({row["state_hash"] for row in rows}) == 1, "state hash equal across committed runs")
    check(len({row["order_hash"] for row in rows}) == 1, "order hash equal across committed runs")
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
    check(evidence.early_output(snapshot, 3) is False, "early-output helper")
    check(evidence.early_output({**snapshot, "carts": snapshot["carts"][:2]}, 3) is True, "short stack is early output")
    # fail closed when the source save was not provided
    rebuild = evidence.cleanup_rebuild(None)
    check(rebuild["source_world_untouched"] is False, "no source world means untouched=false")


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
