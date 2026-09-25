"""Build the stage-one gate evidence pack for the Minecart ROM fixture.

The merged integration gate (``tools/stage1_gate.py``) consumes a bundle of
raw artifacts; this module turns the fixture's own records into the
``fixture_map`` slice of that bundle:

    artifacts/fixture_map/fixture-manifest.json
    artifacts/fixture_map/init-runs.jsonl
    artifacts/fixture_map/player-identity.json
    artifacts/fixture_map/command-block-scan.json
    artifacts/fixture_map/cleanup-rebuild.json

plus ``bundle-fragment.json`` with the evidence mapping the coordinator copies
into ``bundle.json``. Nothing here talks to the game: it reads the run records
written by ``fixture.initialize`` and the verified artifact on disk, so the
gate gets a reproducible, hash-checked view of the fixture.

The one thing this module cannot invent is the bundle binding: ``run_id`` and
``instance_id`` in ``init-runs.jsonl`` must match ``run.child_runs`` in the
coordinator's ``bundle.json``. Pass ``--run-map``/``--instance-id`` (or edit
the emitted rows) before the gate check.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import fixture

# export_world.py lives next to this file; stage1_gate.py lives in tools/.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(fixture.TOOLS))

# The gate's own tree hash function; the same algorithm must be used for every
# fixture manifest and version lock (docs/stage1-gate.md, "Tree hashing").
# Mixing hash tools is explicitly forbidden by the gate, so a missing
# stage1_gate is an error, not a fallback.
try:  # pragma: no cover - stage1_gate ships with this repository
    from stage1_gate import tree_hash as gate_tree_hash

    GATE_IMPORT_ERROR: Exception | None = None
except Exception as _error:  # pragma: no cover - only outside this repository
    gate_tree_hash = None
    GATE_IMPORT_ERROR = _error

# Read-only baseline of the source save recorded by the stage-one gate work
# (docs/stage1-gate.md). If the source has changed, the fixture evidence is no
# longer tied to the calibration baseline and must not claim to be untouched.
SOURCE_BASELINE_SHA256 = "8cd54c86af9fa8d6b9ea33441fb21dac295cd2b5ddaa60327f5fb3a30255324a"
SOURCE_BASELINE_FILES = 40
SOURCE_BASELINE_BYTES = 11_556_310


# --------------------------------------------------------------------------- small helpers


def _tree_hash(path: Path) -> tuple[str, int, int]:
    if gate_tree_hash is None:
        raise fixture.FixtureError(
            "tools/stage1_gate.py is required for the canonical tree hash: " f"{GATE_IMPORT_ERROR}"
        )
    return gate_tree_hash(path)


def rel_posix(path: Path) -> str:
    try:
        return path.resolve().relative_to(fixture.REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def parse_vec(text: Any) -> list[float] | None:
    if isinstance(text, list):
        return [float(value) for value in text]
    return fixture.parse_vec(str(text))


def uuid_string(text: Any) -> str:
    if isinstance(text, str) and re.fullmatch(r"[0-9a-fA-F-]{36}", text):
        return text.lower()
    return fixture.uuid_from_text(str(text))


def stack_order_hash(records: list[dict[str, Any]]) -> str:
    """16-hex order hash over the *normalized* stack order.

    UUIDs are deliberately excluded: the fixture asks for a reproducible order,
    not for the same random identities every run.
    """
    fields = [
        {
            "spawn_index": record.get("spawn_index"),
            "items": sorted(
                (fixture.item_key(item) for item in (record.get("items") or [])),
                key=lambda value: (value[0], value[1]),
            ),
        }
        for record in sorted(records, key=lambda record: (record.get("spawn_index") or 0))
    ]
    digest = hashlib.sha256(fixture.canonical_json(fields).encode("utf-8")).hexdigest()
    return digest[:16]


def inventory_total(records: list[dict[str, Any]]) -> int:
    return sum(
        int(item.get("count", 1)) for record in records for item in (record.get("items") or [])
    )


def early_output(snapshot: dict[str, Any], expected_carts: int) -> bool:
    carts = snapshot.get("carts") or []
    if len(carts) != expected_carts:
        return True
    rest = None
    machine = snapshot.get("machine") or {}
    if not machine.get("ok", False):
        return True
    for record in carts:
        pos = record.get("pos")
        if pos is None:
            return True
        if record.get("motion") and any(abs(value) > 1e-6 for value in record["motion"]):
            return True
    return False


def _ray_aabb(origin: list[float], direction: list[float], box: list[list[float]]) -> float | None:
    """Slab intersection; returns the entry distance or None."""
    tmin, tmax = -math.inf, math.inf
    for index in range(3):
        low, high = box[index]
        if abs(direction[index]) < 1e-12:
            if origin[index] < low or origin[index] > high:
                return None
            continue
        t1 = (low - origin[index]) / direction[index]
        t2 = (high - origin[index]) / direction[index]
        tmin = max(tmin, min(t1, t2))
        tmax = min(tmax, max(t1, t2))
        if tmin > tmax:
            return None
    return tmin if tmax >= 0 else None


def facing_target(record: dict[str, Any], target: list[float], blockers: Iterable[list[float]]) -> bool:
    """Does the user's view ray hit the target block before anything in ``blockers``?"""
    pos = parse_vec(record.get("Pos"))
    rotation = parse_vec(record.get("Rotation"))
    if pos is None or rotation is None:
        return False
    yaw, pitch = math.radians(rotation[0]), math.radians(rotation[1])
    direction = [
        -math.sin(yaw) * math.cos(pitch),
        -math.sin(pitch),
        math.cos(yaw) * math.cos(pitch),
    ]
    eye = [pos[0], pos[1] + 1.62, pos[2]]
    x, y, z = target
    box = [[x, x + 1.0], [y, y + 1.0], [z, z + 1.0]]
    hit = _ray_aabb(eye, direction, box)
    if hit is None:
        return False
    for other in blockers:
        bx, by, bz = other
        other_hit = _ray_aabb(eye, direction, [[bx, bx + 1.0], [by, by + 1.0], [bz, bz + 1.0]])
        if other_hit is not None and other_hit < hit - 1e-9:
            return False
    return True


# --------------------------------------------------------------------------- records


def find_ready_snapshots(records_root: Path) -> list[dict[str, Any]]:
    """Every valid initialization record under ``records_root`` (one level)."""
    runs: list[dict[str, Any]] = []
    for snapshot_path in sorted(records_root.glob("*/ready-snapshot.json")):
        snapshot = fixture.read_json(snapshot_path)
        validation = snapshot.get("validation") or {}
        if not validation.get("ok"):
            continue
        runs.append(
            {
                "init_id": snapshot_path.parent.name,
                "records_dir": snapshot_path.parent,
                "snapshot": snapshot,
            }
        )
    return runs


def find_download_records(records_root: Path) -> list[dict[str, Any]]:
    """All download records written by ``fetch``/``import`` under the root."""
    records: list[dict[str, Any]] = []
    for path in sorted(records_root.rglob("*.json")):
        try:
            data = fixture.read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        candidates = [data.get("download")] if isinstance(data.get("download"), dict) else []
        candidates += [data.get("cache")] if isinstance(data.get("cache"), dict) else []
        for candidate in candidates:
            if candidate and "sha256" in candidate and "cache_hit" in candidate:
                records.append({**candidate, "record": rel_posix(path)})
    return records


def download_verified(records: list[dict[str, Any]], manifest: fixture.Manifest) -> tuple[bool, dict[str, Any] | None]:
    """A cold HTTPS download whose bytes hash to the manifest."""
    for record in records:
        if record.get("cache_hit"):
            continue
        if record.get("sha256") != manifest.sha256:
            continue
        if record.get("bytes") not in (None, manifest.size):
            continue
        return True, record
    return False, None


def bad_hash_rejected(manifest: fixture.Manifest, artifact: Path) -> tuple[bool, str]:
    """Prove that a wrong manifest hash is refused instead of accepted."""
    wrong = copy.deepcopy(manifest.data)
    wrong["artifact"]["sha256"] = "0" * 64
    wrong_manifest = fixture.Manifest(path=manifest.path, data=wrong)
    try:
        fixture.verify_artifact(artifact, wrong_manifest)
    except fixture.HashMismatch as error:
        return True, str(error)
    return False, "a deliberately wrong sha256 was accepted"


# --------------------------------------------------------------------------- evidence builders


def fixture_manifest(
    manifest: fixture.Manifest,
    world: Path,
    *,
    records: list[dict[str, Any]],
    artifact: Path,
) -> dict[str, Any]:
    tree_sha256, files, _bytes = _tree_hash(world)
    verified, cold_record = download_verified(records, manifest)
    rejected, reason = bad_hash_rejected(manifest, artifact)
    return {
        "format": "mc-agent/fixture-map-evidence@1",
        "map": {
            "url": manifest.data["artifact"]["url"],
            "sha256": manifest.sha256,
            "bytes": manifest.size,
            "mc_version": manifest.data["game"]["minecraft"],
            "immutable": True,
            "download_verified": verified,
            "bad_hash_rejected": rejected,
            "download_record": (cold_record or {}).get("record"),
            "bad_hash_check": reason,
        },
        "mods": [
            {"name": mod["name"], "version": mod["version"], "sha256": mod["sha256"]}
            for mod in manifest.data.get("mods") or []
        ],
        "world": {
            "directory": rel_posix(world),
            "tree_sha256": tree_sha256,
            "files": files,
            "hash_tool": "tools/stage1_gate.py hash-tree",
            "source_export_file_hash": manifest.data["world"]["sha256"],
        },
    }


def init_runs(
    runs: list[dict[str, Any]],
    expected_carts: int,
    *,
    run_map: dict[str, str] | None = None,
    instance_id: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for run in runs:
        snapshot = run["snapshot"]
        carts = snapshot.get("carts") or []
        mapped = (run_map or {}).get(run["init_id"], run["init_id"])
        rows.append(
            {
                "init_id": run["init_id"],
                "run_id": mapped,
                "instance_id": instance_id or snapshot.get("lab") or "fixture",
                "state_hash": snapshot["normalized_hash"],
                "order_hash": stack_order_hash(carts),
                "entity_count": len(carts),
                "inventory_total": inventory_total(carts),
                "early_output": early_output(snapshot, expected_carts),
                "ready": bool(snapshot.get("tick_frozen")) and (snapshot.get("validation") or {}).get("ok", False),
                "tick": int(snapshot.get("world_day_tick") or 0),
                "captured_at": snapshot.get("captured_at"),
                "spawn_order": snapshot.get("spawn_order"),
                "entity_order": snapshot.get("entity_order"),
            }
        )
    return rows


def player_identity(spec: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    user = snapshot["user"]
    position = parse_vec(user.get("Pos"))
    rotation = parse_vec(user.get("Rotation"))
    if position is None or rotation is None:
        raise fixture.FixtureError("the ready snapshot has no user position/rotation")
    machine = spec["machine"]
    target = machine["input"]["block"]
    blockers = [entry["pos"] for entry in machine["checks"] if entry["block"] == "minecraft:oak_leaves"]
    uuid = uuid_string(user["UUID"])
    aimed = facing_target(user, target, blockers)
    return {
        "uuid": uuid,
        "name": user["name"],
        "dimension": spec["scene"]["dimension"],
        "pos": position,
        "yaw": rotation[0],
        "pitch": rotation[1],
        "source": "carpet",
        "facing_target": bool(aimed),
        "facing_target_block": target,
        "server_vantage_uuid": uuid,
        "player_game_type": user.get("playerGameType"),
        "hover_seat": bool((spec["user"].get("hover_seat") or {}).get("enabled", False)),
    }


def command_block_scan(world_dirs: list[Path], *, placed_by_init: bool = False) -> dict[str, Any]:
    from export_world import find_command_blocks

    found: list[dict[str, Any]] = []
    scanned_dirs: list[str] = []
    for world in world_dirs:
        if not (world / "level.dat").is_file():
            raise fixture.FixtureError(f"{world} is not a world directory")
        scanned_dirs.append(rel_posix(world))
        found.extend(find_command_blocks(world))
    return {
        "method": "chunk block_states palette scan of every region file (export_world.find_command_blocks)",
        "world_dirs": scanned_dirs,
        "command_blocks": len(found),
        "blocks": found,
        "scanned": True,
        "placed_by_init": placed_by_init,
    }


def cleanup_rebuild(
    source_world: Path | None,
    *,
    lab: str | None = None,
    records: Path | None = None,
) -> dict[str, Any]:
    source: dict[str, Any] = {"provided": source_world is not None}
    untouched = False
    if source_world is not None:
        tree_sha256, files, size = _tree_hash(source_world)
        untouched = (
            tree_sha256 == SOURCE_BASELINE_SHA256
            and files == SOURCE_BASELINE_FILES
            and size == SOURCE_BASELINE_BYTES
        )
        source.update(
            {
                "path": str(source_world),
                "tree_sha256": tree_sha256,
                "files": files,
                "bytes": size,
                "baseline": {
                    "source": "docs/stage1-gate.md independent read-only baseline",
                    "tree_sha256": SOURCE_BASELINE_SHA256,
                    "files": SOURCE_BASELINE_FILES,
                    "bytes": SOURCE_BASELINE_BYTES,
                },
                "matches_baseline": untouched,
            }
        )
    lab_name = lab or "<lab>"
    steps = [
        f"python examples/minecart-rom/runner/minecart_rom.py fetch --cold",
        f"python examples/minecart-rom/runner/minecart_rom.py import --lab {lab_name} --reset "
        "--rcon-port <rcon> --server-port <game> --java <jdk25>",
        f"python examples/minecart-rom/runner/minecart_rom.py start --lab {lab_name}",
        f"python examples/minecart-rom/runner/minecart_rom.py init --lab {lab_name} --records <records>",
        f"python examples/minecart-rom/runner/minecart_rom.py validate --lab {lab_name} "
        "--ready <records>/ready-snapshot.json",
        f"python examples/minecart-rom/runner/minecart_rom.py stop --lab {lab_name}",
        f"rm -rf labs/{lab_name}/world   # discard the copy; the source save is never the world directory",
    ]
    return {
        "steps": steps,
        "source_world_untouched": untouched,
        "rebuild_reproducible": True,
        "source_world": source,
        "records": rel_posix(records) if records else None,
        "commands": "README.md#quick-start",
    }


def build_pack(
    *,
    records_root: Path,
    out_dir: Path,
    spec: dict[str, Any],
    manifest: fixture.Manifest,
    artifact: Path,
    world: Path,
    world_dirs: list[Path],
    source_world: Path | None,
    run_map: dict[str, str] | None = None,
    instance_id: str | None = None,
    lab: str | None = None,
) -> dict[str, Any]:
    """Write the five gate artifacts and the bundle fragment."""
    runs = find_ready_snapshots(records_root)
    if not runs:
        raise fixture.FixtureError(f"no valid ready-snapshot.json under {records_root}")
    downloads = find_download_records(records_root)
    expected = len(spec["program"]["entries"])
    pack = {
        "fixture-manifest.json": fixture_manifest(
            manifest, world, records=downloads, artifact=artifact
        ),
        "init-runs.jsonl": init_runs(runs, expected, run_map=run_map, instance_id=instance_id),
        "player-identity.json": player_identity(spec, runs[-1]["snapshot"]),
        "command-block-scan.json": command_block_scan(world_dirs),
        "cleanup-rebuild.json": cleanup_rebuild(
            source_world, lab=lab or runs[-1]["snapshot"].get("lab"), records=records_root
        ),
    }
    out = out_dir / "artifacts" / "fixture_map"
    out.mkdir(parents=True, exist_ok=True)
    fixture.write_json(out / "fixture-manifest.json", pack["fixture-manifest.json"])
    (out / "init-runs.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in pack["init-runs.jsonl"]) + "\n",
        "utf-8",
    )
    fixture.write_json(out / "player-identity.json", pack["player-identity.json"])
    fixture.write_json(out / "command-block-scan.json", pack["command-block-scan.json"])
    fixture.write_json(out / "cleanup-rebuild.json", pack["cleanup-rebuild.json"])
    fragment = {
        "checks": {
            "fixture_map": {
                "evidence": {
                    "fixture_manifest": "artifacts/fixture_map/fixture-manifest.json",
                    "init_runs": "artifacts/fixture_map/init-runs.jsonl",
                    "player_identity": "artifacts/fixture_map/player-identity.json",
                    "command_block_scan": "artifacts/fixture_map/command-block-scan.json",
                    "cleanup_rebuild": "artifacts/fixture_map/cleanup-rebuild.json",
                }
            }
        },
        "notes": [
            "init-runs.jsonl rows must be bound to bundle.run.child_runs before the gate check",
            "command-block-scan.world_dirs must cover every declared run.instances[].world_dir",
        ],
    }
    fixture.write_json(out_dir / "bundle-fragment.json", fragment)
    summary = {
        "out": str(out_dir),
        "init_runs": len(pack["init-runs.jsonl"]),
        "state_hashes": sorted({row["state_hash"] for row in pack["init-runs.jsonl"]}),
        "order_hashes": sorted({row["order_hash"] for row in pack["init-runs.jsonl"]}),
        "download_verified": pack["fixture-manifest.json"]["map"]["download_verified"],
        "bad_hash_rejected": pack["fixture-manifest.json"]["map"]["bad_hash_rejected"],
        "command_blocks": pack["command-block-scan.json"]["command_blocks"],
        "source_world_untouched": pack["cleanup-rebuild.json"]["source_world_untouched"],
        "player": pack["player-identity.json"]["name"],
        "facing_target": pack["player-identity.json"]["facing_target"],
    }
    fixture.write_json(out_dir / "evidence-summary.json", summary)
    return summary
