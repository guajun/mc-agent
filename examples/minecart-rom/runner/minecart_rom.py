#!/usr/bin/env python3
"""Minecart ROM fixture driver.

One entry point for the test-side operations described in the use case:

    # download, hash-check and unpack the map artifact into the lab cache
    python examples/minecart-rom/runner/minecart_rom.py fetch --cold

    # provision a fresh lab copy and start it (ports are optional)
    python examples/minecart-rom/runner/minecart_rom.py up --lab rom15 \
        --rcon-port 27150 --server-port 27151

    # create the ready fixture (fake player + cart stack) and freeze the world
    python examples/minecart-rom/runner/minecart_rom.py init --lab rom15 \
        --records examples/minecart-rom/calibration/records/run-01

    # re-read the live world and classify it before handing it to an agent
    python examples/minecart-rom/runner/minecart_rom.py validate --lab rom15 \
        --records examples/minecart-rom/calibration/records/run-01

Every subcommand that talks to the game keeps a full command transcript in the
record directory, so a run can be audited afterwards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parent
sys.path.insert(0, str(RUNNER))

import fixture  # noqa: E402


def record_dir(args: argparse.Namespace) -> Path:
    base = Path(args.records).expanduser() if args.records else fixture.LABS / args.lab / "fixture"
    run = args.run_id or time_stamp()
    path = base / run if args.run_id else base
    path.mkdir(parents=True, exist_ok=True)
    return path


def time_stamp() -> str:
    import time

    return time.strftime("%Y%m%d-%H%M%S")


def interface_client(status: dict, port: int | None = None, timeout: float = 20.0):
    """The lab's server-vantage client, or None when the lab has no mod port."""
    vantage = port or status.get("server_vantage_port")
    if not vantage:
        return None
    import interface_mod

    return interface_mod.InterfaceClient(port=int(vantage), timeout=timeout).connect()


def cmd_manifest(args: argparse.Namespace) -> int:
    manifest = fixture.load_manifest(Path(args.manifest) if args.manifest else None)
    spec = fixture.load_spec(Path(args.spec) if args.spec else None)
    summary = {
        "manifest": str(manifest.path),
        "version": manifest.data.get("version"),
        "game": manifest.data.get("game"),
        "mods": manifest.data.get("mods"),
        "artifact": manifest.data.get("artifact"),
        "world": manifest.data.get("world"),
        "spec_version": spec.get("spec_version"),
        "fixture": spec.get("fixture", {}).get("name"),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    manifest = fixture.load_manifest(Path(args.manifest) if args.manifest else None)
    cache = Path(args.cache).expanduser() if args.cache else fixture.MAP_CACHE
    result = fixture.fetch_artifact(
        manifest, cache, url=args.url or None, cold=args.cold, timeout=args.timeout
    )
    verify = {"cache": result.as_record()}
    if not args.no_extract:
        verify["extract"] = fixture.unpack_artifact(manifest, result.path, cache / "unpacked")
    print(json.dumps(verify, indent=2, ensure_ascii=False))
    if args.records:
        fixture.write_json(Path(args.records).expanduser() / "download.json", verify)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    manifest = fixture.load_manifest(Path(args.manifest) if args.manifest else None)
    cache = Path(args.cache).expanduser() if args.cache else fixture.MAP_CACHE
    result = fixture.fetch_artifact(
        manifest, cache, url=args.url or None, cold=args.cold, timeout=args.timeout
    )
    extract = fixture.unpack_artifact(manifest, result.path, cache / "unpacked")
    report = fixture.import_world(
        args.lab,
        Path(extract["world"]),
        manifest=manifest,
        reset=args.reset,
        java=args.java,
        memory=args.memory,
        rcon_port=args.rcon_port,
        server_port=args.server_port,
        vantage_port=args.vantage_port,
        bridge_port=args.bridge_port,
        interface_mod=Path(args.interface_mod).expanduser() if args.interface_mod else None,
    )
    report["download"] = result.as_record()
    report["extract"] = {key: value for key, value in extract.items() if key != "export"}
    report["export"] = extract.get("export")
    if args.records:
        fixture.write_json(Path(args.records).expanduser() / "import.json", report)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    fixture.start_lab(args.lab, wait=args.wait)
    print(json.dumps(fixture.lab_status(args.lab), indent=2))
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    fixture.stop_lab(args.lab, timeout=args.timeout)
    print(f"lab {args.lab} stopped")
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    cmd_import(args)
    fixture.start_lab(args.lab, wait=args.wait)
    print(json.dumps(fixture.lab_status(args.lab), indent=2))
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    spec = fixture.load_spec(Path(args.spec) if args.spec else None)
    manifest = fixture.load_manifest(Path(args.manifest) if args.manifest else None)
    program = fixture.load_program(Path(args.program)) if args.program else None
    records = record_dir(args)
    status = fixture.lab_status(args.lab)
    interface = interface_client(status, args.interface_port)
    console = fixture.Console(args.lab, verbose=args.verbose).connect()
    try:
        ready = fixture.initialize(
            console,
            spec,
            program,
            record_dir=records,
            interface=interface,
            snapshot_name=args.snapshot_name or f"ready-{args.lab}",
            allow_rcon_order=args.allow_rcon_order,
            manifest=manifest,
        )
    finally:
        if interface is not None:
            interface.close()
        console.close()
    summary = {
        "records": str(records),
        "cart_count": ready["cart_count"],
        "spawn_order": ready["spawn_order"],
        "normalized_hash": ready["normalized_hash"],
        "tick_order": ready.get("tick_order"),
        "tick_order_hash": ready.get("tick_order_hash"),
        "rcon_order": ready.get("rcon_order"),
        "interface": {
            "order_hash": (ready.get("interface") or {}).get("order_hash"),
            "tick": (ready.get("interface") or {}).get("tick"),
            "mod_version": (ready.get("interface") or {}).get("mod_version"),
            "cross_check_ok": (ready.get("interface") or {}).get("cross_check_ok"),
        },
        "machine": ready["machine"],
        "user": ready["user"],
        "validation": ready["validation"],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if ready["validation"]["ok"] else 1


def cmd_snapshot(args: argparse.Namespace) -> int:
    spec = fixture.load_spec(Path(args.spec) if args.spec else None)
    records = record_dir(args)
    status = fixture.lab_status(args.lab)
    interface = interface_client(status, args.interface_port)
    console = fixture.Console(args.lab, verbose=args.verbose).connect()
    try:
        snapshot = fixture.take_snapshot(
            console,
            spec,
            server=status,
            interface=interface,
            snapshot_name=args.snapshot_name or f"snapshot-{args.lab}",
        )
        result = fixture.validate_ready(spec, snapshot)
    finally:
        if interface is not None:
            interface.close()
        console.close()
    snapshot["validation"] = result
    out = Path(args.out).expanduser() if args.out else records / "snapshot.json"
    fixture.write_json(out, snapshot)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_validate(args: argparse.Namespace) -> int:
    spec = fixture.load_spec(Path(args.spec) if args.spec else None)
    ready_path = Path(args.ready).expanduser() if args.ready else record_dir(args) / "ready-snapshot.json"
    if not ready_path.is_file():
        raise fixture.FixtureError(
            f"no ready snapshot at {ready_path}; run init first or pass --ready"
        )
    ready = fixture.read_json(ready_path)
    status = fixture.lab_status(args.lab)
    interface = interface_client(status, args.interface_port)
    console = fixture.Console(args.lab, verbose=args.verbose).connect()
    try:
        result = fixture.check_live_state(
            console,
            spec,
            ready,
            status=status,
            interface=interface,
            snapshot_name=args.snapshot_name or f"validate-{args.lab}",
        )
    finally:
        if interface is not None:
            interface.close()
        console.close()
    result["ready_snapshot"] = str(ready_path)
    result["records_dir"] = str(ready_path.parent)
    fixture.write_json(ready_path.parent / "validate.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "snapshot"}, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def cmd_evidence(args: argparse.Namespace) -> int:
    """Build the fixture_map slice of the stage-one gate bundle from records."""
    sys.path.insert(0, str(RUNNER))
    import evidence as evidence_module

    if not args.records:
        raise fixture.FixtureError("evidence needs --records <root with run-*/ready-snapshot.json>")
    spec = fixture.load_spec(Path(args.spec) if args.spec else None)
    manifest = fixture.load_manifest(Path(args.manifest) if args.manifest else None)
    cache = Path(args.cache).expanduser() if args.cache else fixture.MAP_CACHE
    artifact = cache / manifest.filename
    if not artifact.is_file():
        raise fixture.FixtureError(
            f"{artifact} is missing; run `fetch --cold` first (the evidence pack verifies the bytes)"
        )
    fixture.verify_artifact(artifact, manifest)
    world = Path(args.world).expanduser() if args.world else (
        cache / "unpacked" / manifest.filename.removesuffix(".zip") / "world"
    )
    if not (world / "level.dat").is_file():
        raise fixture.FixtureError(
            f"{world} is not an extracted world; pass --world or run `fetch` first"
        )
    world_dirs = [world]
    for extra in args.world_dir or []:
        path = Path(extra).expanduser()
        if path not in world_dirs:
            world_dirs.append(path)
    for run in evidence_module.find_ready_snapshots(Path(args.records).expanduser()):
        lab = run["snapshot"].get("lab")
        if not lab:
            continue
        lab_world = fixture.LABS / lab / "world"
        if (lab_world / "level.dat").is_file() and lab_world not in world_dirs:
            world_dirs.append(lab_world)
    run_map = {}
    for mapping in args.run_map or []:
        if "=" not in mapping:
            raise fixture.FixtureError(f"--run-map needs OLD=NEW, got {mapping!r}")
        old, new = mapping.split("=", 1)
        run_map[old] = new
    summary = evidence_module.build_pack(
        records_root=Path(args.records).expanduser(),
        out_dir=Path(args.out).expanduser(),
        spec=spec,
        manifest=manifest,
        artifact=artifact,
        world=world,
        world_dirs=world_dirs,
        source_world=Path(args.source_world).expanduser() if args.source_world else None,
        run_map=run_map or None,
        instance_id=args.instance_id or None,
        lab=args.lab or None,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


DEFAULT_POOL = (
    "minecraft:stone",
    "minecraft:dirt",
    "minecraft:gold_ingot",
    "minecraft:iron_ingot",
    "minecraft:redstone",
    "minecraft:coal",
    "minecraft:oak_planks",
    "minecraft:glass",
    "minecraft:sand",
    "minecraft:copper_ingot",
)


def cmd_challenge(args: argparse.Namespace) -> int:
    """Generate a fresh, sealed cart program for evaluation runs.

    The committed ``fixture-spec.json`` program is the public calibration
    program; it must not be used to grade a cold-start agent. This generator
    produces a new program from a seed, records the seed and the program hash
    for the audit trail, and deliberately does **not** produce the pop order:
    that is observed live and sealed on the evaluation side.
    """
    import hashlib
    import random

    if args.seed:
        seed = int(args.seed)
    else:
        seed = random.SystemRandom().getrandbits(63)
    pool = [item.strip() for item in (args.pool or ",".join(DEFAULT_POOL)).split(",") if item.strip()]
    if not pool:
        raise fixture.FixtureError("the item pool is empty")
    rng = random.Random(seed)
    carts = []
    seen: set[tuple] = set()
    for index in range(max(1, args.carts)):
        for _attempt in range(100):
            slots = sorted(rng.sample(range(27), rng.randint(1, min(args.max_slots, 27))))
            items = [
                {
                    "slot": slot,
                    "id": rng.choice(pool),
                    "count": rng.randint(1, max(1, args.max_count)),
                }
                for slot in slots
            ]
            signature = tuple(sorted((item["slot"], item["id"], item["count"]) for item in items))
            if signature not in seen:
                seen.add(signature)
                break
        else:  # pragma: no cover - only with a tiny pool/slots combination
            raise fixture.FixtureError("could not generate distinct carts; widen the pool")
        carts.append({"index": index, "items": items})
    program = {"format": "mc-agent/challenge@1", "name": f"challenge-{seed}", "seed": seed, "carts": carts}
    program["program_sha256"] = hashlib.sha256(
        fixture.canonical_json(carts).encode("utf-8")
    ).hexdigest()
    program["notes"] = [
        "sealed evaluator-side input; do not commit and do not hand to the Agent",
        "the pop order is observed live and sealed separately; it is not generated here",
    ]
    out = Path(args.out).expanduser()
    fixture.write_json(out, program)
    print(
        json.dumps(
            {
                "out": str(out),
                "seed": seed,
                "carts": len(carts),
                "inventory_total": sum(
                    item["count"] for cart in carts for item in cart["items"]
                ),
                "program_sha256": program["program_sha256"],
            },
            indent=2,
        )
    )
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(RUNNER))
    import selftest

    return selftest.run(keep=args.keep, verbose=args.verbose)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--manifest", default="", help="alternative map-manifest.json")
    common.add_argument("--spec", default="", help="alternative fixture-spec.json")
    common.add_argument("--cache", default="", help="artifact cache directory")
    common.add_argument("--records", default="", help="directory for run records")
    common.add_argument("--json", action="store_true", help="(all output is JSON already)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("manifest", parents=[common], help="show the pinned map manifest")
    p.set_defaults(handler=cmd_manifest)

    p = sub.add_parser("fetch", parents=[common], help="download, verify and unpack the artifact")
    p.add_argument("--url", default="", help="override the artifact URL (mirror)")
    p.add_argument("--cold", action="store_true", help="ignore the local cache")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--no-extract", action="store_true")
    p.set_defaults(handler=cmd_fetch)

    p = sub.add_parser("import", parents=[common], help="verify the artifact and provision a lab")
    p.add_argument("--lab", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--cold", action="store_true")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--reset", action="store_true", help="replace the lab world with a fresh copy")
    p.add_argument("--java", default="")
    p.add_argument("--memory", default="")
    p.add_argument("--rcon-port", type=int)
    p.add_argument("--server-port", type=int)
    p.add_argument("--vantage-port", type=int, help="server-vantage mod base port")
    p.add_argument("--bridge-port", type=int, help="bridge loopback API port for this lab")
    p.add_argument(
        "--interface-mod",
        default="",
        help="the mc-agent-interface jar to deploy (pinned version/sha in the manifest)",
    )
    p.set_defaults(handler=cmd_import)

    p = sub.add_parser("start", help="start a provisioned lab")
    p.add_argument("--lab", required=True)
    p.add_argument("--wait", type=float, default=300.0)
    p.set_defaults(handler=cmd_start)

    p = sub.add_parser("stop", help="stop a lab")
    p.add_argument("--lab", required=True)
    p.add_argument("--timeout", type=float, default=120.0)
    p.set_defaults(handler=cmd_stop)

    p = sub.add_parser("up", parents=[common], help="import a fresh copy and start it")
    p.add_argument("--lab", required=True)
    p.add_argument("--url", default="")
    p.add_argument("--cold", action="store_true")
    p.add_argument("--timeout", type=float, default=300.0)
    p.add_argument("--reset", action="store_true")
    p.add_argument("--java", default="")
    p.add_argument("--memory", default="")
    p.add_argument("--rcon-port", type=int)
    p.add_argument("--server-port", type=int)
    p.add_argument("--vantage-port", type=int, help="server-vantage mod base port")
    p.add_argument("--bridge-port", type=int, help="bridge loopback API port for this lab")
    p.add_argument("--interface-mod", default="", help="the mc-agent-interface jar to deploy")
    p.add_argument("--wait", type=float, default=300.0)
    p.set_defaults(handler=cmd_up)

    p = sub.add_parser("init", parents=[common], help="create the ready fixture")
    p.add_argument("--lab", required=True)
    p.add_argument("--program", default="", help="JSON cart program override (sealed challenge)")
    p.add_argument("--run-id", default="")
    p.add_argument("--interface-port", type=int, default=0, help="override the server-vantage port")
    p.add_argument("--snapshot-name", default="", help="interface snapshot name to write")
    p.add_argument(
        "--allow-rcon-order",
        action="store_true",
        help="development only: accept the RCON selector order instead of the tick order",
    )
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(handler=cmd_init)

    p = sub.add_parser("snapshot", parents=[common], help="capture and check the live state")
    p.add_argument("--lab", required=True)
    p.add_argument("--out", default="")
    p.add_argument("--run-id", default="")
    p.add_argument("--interface-port", type=int, default=0)
    p.add_argument("--snapshot-name", default="")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(handler=cmd_snapshot)

    p = sub.add_parser("validate", parents=[common], help="re-check the live state against ready")
    p.add_argument("--lab", required=True)
    p.add_argument("--ready", default="", help="the ready-snapshot.json to compare against")
    p.add_argument("--run-id", default="")
    p.add_argument("--interface-port", type=int, default=0)
    p.add_argument("--snapshot-name", default="")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(handler=cmd_validate)

    p = sub.add_parser("evidence", parents=[common], help="build the gate fixture_map evidence pack")
    p.add_argument("--out", required=True, help="bundle root to write into")
    p.add_argument("--world", default="", help="the extracted base world (default: cache copy)")
    p.add_argument("--world-dir", action="append", default=[], help="extra world copy to scan")
    p.add_argument("--source-world", default="", help="the read-only source save for the baseline check")
    p.add_argument("--instance-id", default="", help="instance id for every init run")
    p.add_argument("--lab", default="", help="lab name for the rebuild steps")
    p.add_argument("--run-map", action="append", default=[], help="OLD=NEW run id mapping (repeatable)")
    p.set_defaults(handler=cmd_evidence)

    p = sub.add_parser("challenge", help="generate a fresh sealed cart program (evaluation side)")
    p.add_argument("--seed", default="", help="deterministic seed (default: random)")
    p.add_argument("--out", required=True, help="where to write the sealed program (not in the repo)")
    p.add_argument("--carts", type=int, default=3)
    p.add_argument("--max-slots", type=int, default=3)
    p.add_argument("--max-count", type=int, default=4)
    p.add_argument("--pool", default="", help="comma-separated item ids")
    p.set_defaults(handler=cmd_challenge)

    p = sub.add_parser("selftest", help="offline tests (no game, no network required)")
    p.add_argument("--keep", action="store_true", help="keep the temporary files")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(handler=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.handler(args))
    except fixture.FixtureError as error:
        print(f"fixture error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
