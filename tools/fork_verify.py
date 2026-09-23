#!/usr/bin/env python3
"""Decide whether a fork of a live world is faithful.

The interface mod freezes a running instance and dumps the entity set in tick
order (``docs/protocol-snapshot.md``). Blocks travel as copied region files; the
part that cannot be copied is the order the level ticks entities in, because
that order is rebuilt when the world loads. So that is what this tool checks.

It is the consumer half of the snapshot protocol: it reads a fork directory,
drives a bridge daemon over its loopback JSON-lines API, and decides whether a
restored lab instance reproduced the recording.

    python tools/fork_verify.py inspect <forkDir>
    python tools/fork_verify.py restore <forkDir>                    # dry run
    python tools/fork_verify.py restore <forkDir> --apply --api-port 8765
    python tools/fork_verify.py check   <forkDir> --api-port 8765
    python tools/fork_verify.py diff    <forkDirA> <forkDirB>
    python tools/fork_verify.py selftest

``restore`` is a dry run unless ``--apply`` is given: it prints one ``/summon``
line per entity in recorded order, which is also a valid function file.
``check`` exits non-zero unless the live instance reproduces both the recorded
order hash and the per-type entity counts.

Every bridge call goes through :class:`BridgeClient`, so the selftest can run
the real code paths against a fake transport: no network, no game, no real
snapshot needed.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence, TextIO

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8765
PROTOCOL_VERSION = 1
HASH_HEX_CHARS = 16
REQUIRED_KEYS = ("order", "uuid", "type", "pos", "nbt")
PROGRESS_EVERY = 100
ISSUE_LIMIT = 5
DELTA_ROWS = 5
MISSING_TYPE = "(missing type)"


class SnapshotError(RuntimeError):
    """The snapshot cannot be read or used at all (as opposed to being invalid)."""


# --------------------------------------------------------------------------- data


@dataclass(frozen=True)
class Issue:
    """One validation problem, with a stable code the selftest can assert on."""

    code: str
    message: str
    line: int | None = None


@dataclass(frozen=True)
class Entity:
    """One line of ``entities.jsonl``, plus what the tool could parse out of it."""

    index: int  # position in the file, which is the tick order
    line: int  # 1-based line number in entities.jsonl, for messages
    record: dict[str, Any]
    uuid: str
    type: str
    pos: tuple[float, float, float] | None
    vel: tuple[float, float, float] | None
    nbt: str


@dataclass(frozen=True)
class Snapshot:
    """A fork directory: ``meta.json`` plus ``entities.jsonl`` in tick order."""

    path: Path
    meta: dict[str, Any]
    entities: list[Entity]
    issues: list[Issue]  # problems hit while reading, before validation


# ----------------------------------------------------------------------- reading


def _vec(value: Any) -> tuple[float, float, float] | None:
    """Accept ``[x, y, z]`` of finite numbers, reject everything else."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        if not math.isfinite(float(item)):
            return None
        numbers.append(float(item))
    return (numbers[0], numbers[1], numbers[2])


def _entity(index: int, line: int, record: dict[str, Any]) -> Entity:
    uuid = record.get("uuid")
    entity_type = record.get("type")
    nbt = record.get("nbt")
    return Entity(
        index=index,
        line=line,
        record=record,
        uuid=uuid if isinstance(uuid, str) else "",
        type=entity_type if isinstance(entity_type, str) else "",
        pos=_vec(record.get("pos")),
        vel=_vec(record.get("vel")),
        nbt=nbt if isinstance(nbt, str) else "",
    )


def load_snapshot(fork_dir: Path | str) -> Snapshot:
    """Read ``meta.json`` and ``entities.jsonl``.

    Raises :class:`SnapshotError` when the directory is not a snapshot at all;
    a snapshot that exists but is wrong comes back with issues instead, so the
    caller can report all of them at once.
    """
    path = Path(fork_dir)
    if not path.is_dir():
        raise SnapshotError(f"not a snapshot directory: {path}")
    meta_path = path / "meta.json"
    data_path = path / "entities.jsonl"
    if not meta_path.is_file():
        raise SnapshotError(f"{meta_path} is missing")
    if not data_path.is_file():
        raise SnapshotError(f"{data_path} is missing")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise SnapshotError(f"{meta_path}: cannot be read: {error}") from error
    except json.JSONDecodeError as error:
        raise SnapshotError(f"{meta_path}: not valid JSON: {error}") from error
    if not isinstance(meta, dict):
        raise SnapshotError(f"{meta_path}: expected a JSON object")

    try:
        text = data_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SnapshotError(f"{data_path}: cannot be read: {error}") from error

    entities: list[Entity] = []
    issues: list[Issue] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            issues.append(
                Issue("bad_json", f"line {line_number}: not valid JSON: {error}", line_number)
            )
            continue
        if not isinstance(record, dict):
            issues.append(
                Issue("bad_record", f"line {line_number}: expected a JSON object", line_number)
            )
            continue
        entities.append(_entity(len(entities), line_number, record))
    return Snapshot(path=path, meta=meta, entities=entities, issues=issues)


def order_hash(entities: Sequence[Entity]) -> str:
    """The one value that proves a restore reproduced the order.

    ``orderHash`` = first 16 hex chars of ``sha256(join(":", uuids))``.
    """
    joined = ":".join(entity.uuid for entity in entities)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:HASH_HEX_CHARS]


def type_counts(entities: Iterable[Entity]) -> Counter[str]:
    """Entity counts by type, exactly as recorded (namespace included)."""
    counts: Counter[str] = Counter()
    for entity in entities:
        counts[entity.type or MISSING_TYPE] += 1
    return counts


# -------------------------------------------------------------------- validation


def _short_list(values: Sequence[Any], limit: int = 3) -> str:
    shown = ", ".join(str(value) for value in values[:limit])
    if len(values) > limit:
        shown += f", +{len(values) - limit} more"
    return shown or "-"


def _record_issues(entities: Sequence[Entity], count: int) -> list[Issue]:
    issues: list[Issue] = []
    order_values: list[int] = []
    uuid_counts: Counter[str] = Counter()
    for entity in entities:
        record = entity.record
        absent = [key for key in REQUIRED_KEYS if key not in record]
        if absent:
            issues.append(
                Issue(
                    "missing_field",
                    f"line {entity.line}: missing required field(s): {', '.join(absent)}",
                    entity.line,
                )
            )
        if "pos" in record and entity.pos is None:
            issues.append(
                Issue(
                    "bad_field",
                    f"line {entity.line}: pos must be three finite numbers, "
                    f"got {record.get('pos')!r}",
                    entity.line,
                )
            )
        if "vel" in record and entity.vel is None:
            issues.append(
                Issue(
                    "bad_field",
                    f"line {entity.line}: vel must be three finite numbers, "
                    f"got {record.get('vel')!r}",
                    entity.line,
                )
            )
        if not entity.uuid:
            issues.append(
                Issue(
                    "bad_field",
                    f"line {entity.line}: uuid must be a non-empty string, "
                    f"got {record.get('uuid')!r}",
                    entity.line,
                )
            )
        else:
            uuid_counts[entity.uuid] += 1
        if not entity.type:
            issues.append(
                Issue(
                    "bad_field",
                    f"line {entity.line}: type must be a non-empty string, "
                    f"got {record.get('type')!r}",
                    entity.line,
                )
            )
        order = record.get("order")
        if isinstance(order, bool) or not isinstance(order, int):
            issues.append(
                Issue(
                    "order_type",
                    f"line {entity.line}: order must be an integer, got {order!r}",
                    entity.line,
                )
            )
            continue
        order_values.append(order)
        if order != entity.index:
            issues.append(
                Issue(
                    "order_mismatch",
                    f"line {entity.line}: order={order}, expected {entity.index} "
                    "(the file's line order is the tick order)",
                    entity.line,
                )
            )
    duplicated_orders = sorted(value for value, times in Counter(order_values).items() if times > 1)
    missing_orders = sorted(set(range(count)) - set(order_values))
    if duplicated_orders:
        issues.append(
            Issue("order_duplicate", f"order values repeat: {_short_list(duplicated_orders)}")
        )
    if missing_orders:
        issues.append(
            Issue(
                "order_missing",
                f"order values unused: {_short_list(missing_orders)} "
                f"(a faithful file uses 0..{count - 1})",
            )
        )
    repeated_uuids = sorted(uuid for uuid, times in uuid_counts.items() if times > 1)
    if repeated_uuids:
        issues.append(
            Issue(
                "uuid_duplicate",
                f"{len(repeated_uuids)} uuid(s) appear more than once: "
                f"{_short_list(repeated_uuids)}",
            )
        )
    return issues


def validate_snapshot(snapshot: Snapshot) -> list[Issue]:
    """Everything wrong with a snapshot: meta, order fields, uuids, required keys."""
    issues: list[Issue] = list(snapshot.issues)
    meta = snapshot.meta
    entities = snapshot.entities
    count = len(entities)

    if "protocol" not in meta:
        issues.append(Issue("meta_missing", "meta.json: protocol is missing"))
    elif meta.get("protocol") != PROTOCOL_VERSION:
        issues.append(
            Issue(
                "meta_protocol",
                f"meta.json: protocol={meta.get('protocol')!r}, expected {PROTOCOL_VERSION}",
            )
        )
    declared = meta.get("entities")
    if isinstance(declared, bool) or not isinstance(declared, int):
        issues.append(Issue("meta_missing", "meta.json: entities is missing or not an integer"))
    elif declared != count:
        issues.append(
            Issue(
                "entity_count",
                f"meta.json: entities={declared} but entities.jsonl has {count} record(s)",
            )
        )

    computed = order_hash(entities)
    recorded = meta.get("orderHash")
    if not isinstance(recorded, str) or not re.fullmatch(
        rf"[0-9a-f]{{{HASH_HEX_CHARS}}}", recorded or ""
    ):
        issues.append(
            Issue(
                "order_hash_format",
                f"meta.json: orderHash={recorded!r} is not "
                f"{HASH_HEX_CHARS} lowercase hex characters",
            )
        )
    elif recorded != computed:
        issues.append(
            Issue(
                "order_hash",
                f"meta.json: orderHash={recorded} but the file hashes to {computed}",
            )
        )
    return issues + _record_issues(entities, count)


def restore_blockers(snapshot: Snapshot) -> list[Issue]:
    """Problems that stop the recording from becoming ``/summon`` commands.

    Everything else validation finds (order fields, uuids, the meta hash) is a
    warning: the file's line order is what drives a restore, so those problems
    make a snapshot suspect, not unusable.
    """
    blockers: list[Issue] = list(snapshot.issues)
    for entity in snapshot.entities:
        if not entity.type:
            blockers.append(
                Issue(
                    "bad_field",
                    f"line {entity.line}: type is missing, so no /summon can be built",
                    entity.line,
                )
            )
        if entity.pos is None:
            blockers.append(
                Issue(
                    "bad_field",
                    f"line {entity.line}: pos is missing or malformed, so no /summon can be built",
                    entity.line,
                )
            )
    return blockers


def format_issues(issues: Sequence[Issue], limit: int = ISSUE_LIMIT) -> list[str]:
    """One printable line per issue, capped per code so a broken file stays readable."""
    lines: list[str] = []
    seen: Counter[str] = Counter()
    for issue in issues:
        count = seen[issue.code]
        if count < limit:
            lines.append(f"[{issue.code}] {issue.message}")
        elif count == limit:
            lines.append(f"[{issue.code}] ... more of this kind not shown")
        seen[issue.code] = count + 1
    return lines


# ------------------------------------------------------------------- comparison


def table_rows(counts_a: Counter[str], counts_b: Counter[str]) -> list[tuple[str, int, int]]:
    """Union of both histograms, biggest total first, zeros filled in."""
    names = sorted(
        set(counts_a) | set(counts_b),
        key=lambda name: (-(counts_a[name] + counts_b[name]), name),
    )
    return [(name, counts_a[name], counts_b[name]) for name in names]


@dataclass
class CheckReport:
    """A restored instance against the fork it was restored from."""

    fork_hash: str
    live_hash: str
    fork_count: int
    live_count: int
    rows: list[tuple[str, int, int]]

    @property
    def hash_ok(self) -> bool:
        return self.fork_hash == self.live_hash

    @property
    def counts_ok(self) -> bool:
        return all(fork == live for _, fork, live in self.rows)

    @property
    def ok(self) -> bool:
        return self.hash_ok and self.counts_ok


def check_snapshot(fork: Snapshot, live: Snapshot) -> CheckReport:
    return CheckReport(
        fork_hash=order_hash(fork.entities),
        live_hash=order_hash(live.entities),
        fork_count=len(fork.entities),
        live_count=len(live.entities),
        rows=table_rows(type_counts(fork.entities), type_counts(live.entities)),
    )


@dataclass
class EntityDelta:
    uuid: str
    type: str
    magnitude: float
    components: tuple[float, float, float]


@dataclass
class DiffReport:
    """Two recordings compared: order, counts, and how far shared entities moved."""

    a_hash: str
    b_hash: str
    rows: list[tuple[str, int, int]]
    common_prefix: int
    divergence: tuple[int, str | None, str | None] | None
    shared_uuids: int
    position_deltas: list[EntityDelta]
    velocity_deltas: list[EntityDelta]

    @property
    def identical(self) -> bool:
        return (
            self.divergence is None
            and self.a_hash == self.b_hash
            and all(left == right for _, left, right in self.rows)
            and all(delta.magnitude == 0.0 for delta in self.position_deltas)
            and all(delta.magnitude == 0.0 for delta in self.velocity_deltas)
        )


def _at(values: Sequence[str], index: int) -> str | None:
    return values[index] if index < len(values) else None


def diff_snapshot(a: Snapshot, b: Snapshot) -> DiffReport:
    a_uuids = [entity.uuid for entity in a.entities]
    b_uuids = [entity.uuid for entity in b.entities]
    common_prefix = 0
    for left, right in zip(a_uuids, b_uuids):
        if left != right:
            break
        common_prefix += 1
    divergence: tuple[int, str | None, str | None] | None = None
    if len(a_uuids) != len(b_uuids) or common_prefix < min(len(a_uuids), len(b_uuids)):
        divergence = (common_prefix, _at(a_uuids, common_prefix), _at(b_uuids, common_prefix))

    b_by_uuid = {entity.uuid: entity for entity in b.entities if entity.uuid}
    position_deltas: list[EntityDelta] = []
    velocity_deltas: list[EntityDelta] = []
    shared = 0
    for entity in a.entities:
        other = b_by_uuid.get(entity.uuid) if entity.uuid else None
        if other is None:
            continue
        shared += 1
        entity_type = entity.type or other.type
        if entity.pos is not None and other.pos is not None:
            components = (
                other.pos[0] - entity.pos[0],
                other.pos[1] - entity.pos[1],
                other.pos[2] - entity.pos[2],
            )
            position_deltas.append(
                EntityDelta(
                    uuid=entity.uuid,
                    type=entity_type,
                    magnitude=math.dist(entity.pos, other.pos),
                    components=components,
                )
            )
        if entity.vel is not None and other.vel is not None:
            components = (
                other.vel[0] - entity.vel[0],
                other.vel[1] - entity.vel[1],
                other.vel[2] - entity.vel[2],
            )
            velocity_deltas.append(
                EntityDelta(
                    uuid=entity.uuid,
                    type=entity_type,
                    magnitude=math.sqrt(sum(component * component for component in components)),
                    components=components,
                )
            )
    position_deltas.sort(key=lambda delta: (-delta.magnitude, delta.uuid))
    velocity_deltas.sort(key=lambda delta: (-delta.magnitude, delta.uuid))
    return DiffReport(
        a_hash=order_hash(a.entities),
        b_hash=order_hash(b.entities),
        rows=table_rows(type_counts(a.entities), type_counts(b.entities)),
        common_prefix=common_prefix,
        divergence=divergence,
        shared_uuids=shared,
        position_deltas=position_deltas,
        velocity_deltas=velocity_deltas,
    )


# ----------------------------------------------------------------------- output


def _write(stream: TextIO, text: str) -> None:
    """Keep the console out of the encoding business (Windows still defaults to cp1252)."""
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = text.encode("ascii", "backslashreplace").decode("ascii")
    stream.write(text + "\n")


def _num(value: float) -> str:
    if value == 0:
        return "0"
    return f"{value:.6g}"


def _meta_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    return json.dumps(value, ensure_ascii=False)


def _iso_time(millis: Any) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(millis) / 1000.0))
    except (OverflowError, OSError, TypeError, ValueError):
        return ""


def render_table(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], right_from: int = 1
) -> list[str]:
    """A tiny aligned table: no dependency, no box drawing, no surprises."""
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in cells:
        for position, cell in enumerate(row[: len(widths)]):
            widths[position] = max(widths[position], len(cell))

    def render(row: Sequence[str]) -> str:
        parts = []
        for position, cell in enumerate(row[: len(widths)]):
            parts.append(
                cell.rjust(widths[position])
                if position >= right_from
                else cell.ljust(widths[position])
            )
        return "  ".join(parts).rstrip()

    lines = [render(headers), "  ".join("-" * width for width in widths)]
    lines.extend(render(row) for row in cells)
    return lines


def describe_snapshot(snapshot: Snapshot, hash_value: str) -> str:
    parts = [f"{len(snapshot.entities)} records", f"hash {hash_value}"]
    tick = snapshot.meta.get("tick")
    if isinstance(tick, int) and not isinstance(tick, bool):
        parts.append(f"tick {tick}")
    dimension = snapshot.meta.get("dimension")
    if isinstance(dimension, str) and dimension:
        parts.append(dimension)
    return f"{snapshot.path}  ({', '.join(parts)})"


# --------------------------------------------------------------------- commands


def _one_line(text: str) -> str:
    """The mod protocol is line based, so collapse anything that breaks framing."""
    return text.replace("\r", " ").replace("\n", " ")


def summon_command(entity: Entity) -> str:
    """``/summon <type> <x> <y> <z> <nbt>`` - the exact restore line for one entity."""
    if not entity.type:
        raise SnapshotError(f"line {entity.line}: cannot build /summon without a type")
    if entity.pos is None:
        raise SnapshotError(f"line {entity.line}: cannot build /summon without a usable pos")
    parts = ["/summon", entity.type, *(repr(value) for value in entity.pos)]
    nbt = _one_line(entity.nbt)
    if nbt.strip():
        parts.append(nbt)
    return " ".join(parts)


def select_entities(
    entities: Sequence[Entity], start: int = 0, limit: int | None = None
) -> list[Entity]:
    """``--from``/``--limit``: a window over the recorded order (limit 0 = all of it)."""
    if start < 0:
        raise SnapshotError(f"--from must be 0 or greater, got {start}")
    if limit is not None and limit < 0:
        raise SnapshotError(f"--limit must be 0 or greater, got {limit}")
    window = list(entities[start:])
    if limit:
        window = window[:limit]
    return window


# ------------------------------------------------------------------ bridge client


def load_local_api_client() -> type:
    """Import the bridge's loopback client, with the sibling checkout as a fallback."""
    try:
        from mc_agent_bridge.local_api import LocalApiClient  # noqa: PLC0415
    except ImportError:
        sibling = Path(__file__).resolve().parent.parent / "bridge" / "src"
        if not sibling.is_dir():
            raise
        sys.path.insert(0, str(sibling))
        from mc_agent_bridge.local_api import LocalApiClient  # noqa: PLC0415
    return LocalApiClient


class BridgeClient:
    """The only thing that talks to the daemon, and the seam the selftest fakes.

    ``transport`` is anything with the ``LocalApiClient`` shape
    (``connect``/``call``/``close``); it defaults to the real one on first use.
    Method names and parameter shapes are the bridge half of the contract:
    ``snapshot``, ``snapshots``, ``fork``, ``restore`` and ``order`` - plus the
    generic ``command`` method that already exists for driving the game, which
    is what a one-by-one restore uses.
    """

    def __init__(
        self,
        host: str = DEFAULT_API_HOST,
        port: int = DEFAULT_API_PORT,
        timeout: float = 60.0,
        transport: Any | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._transport = transport

    async def connect(self, retry: bool = False) -> None:
        await self._ensure().connect(retry=retry)

    async def close(self) -> None:
        if self._transport is not None:
            await self._transport.close()

    def _ensure(self) -> Any:
        if self._transport is None:
            self._transport = load_local_api_client()(self.host, self.port)
        return self._transport

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        return await self._ensure().call(method, params or {}, timeout=timeout or self.timeout)

    async def snapshot(
        self, name: str | None = None, radius: float | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if radius:
            params["radius"] = radius
        return await self.call("snapshot", params)

    async def snapshots(self) -> dict[str, Any]:
        return await self.call("snapshots")

    async def fork(
        self,
        name: str | None = None,
        radius: float | None = None,
        world_dir: str | None = None,
        regions: bool | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if name:
            params["name"] = name
        if radius:
            params["radius"] = radius
        if world_dir:
            params["world_dir"] = world_dir
        if regions is not None:
            params["regions"] = regions
        return await self.call("fork", params)

    async def restore(
        self, fork_dir: Path | str, dry_run: bool = True, target: str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"dir": str(fork_dir), "dry_run": dry_run}
        if target:
            params["target"] = target
        return await self.call("restore", params)

    async def order(self, fork_dir: Path | str, target: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"dir": str(fork_dir)}
        if target:
            params["target"] = target
        return await self.call("order", params)

    async def command(self, line: str) -> dict[str, Any]:
        return await self.call("command", {"command": line})


# ------------------------------------------------------------------- subcommands


def run_inspect(
    fork_dir: Path | str, out: TextIO | None = None, err: TextIO | None = None
) -> int:
    """Validate a fork directory and print what is in it."""
    out = out or sys.stdout
    snapshot = load_snapshot(fork_dir)
    issues = validate_snapshot(snapshot)
    computed = order_hash(snapshot.entities)
    recorded = snapshot.meta.get("orderHash")
    counts = type_counts(snapshot.entities)

    _write(out, f"snapshot: {snapshot.path}")
    _write(out, "meta:")
    if snapshot.meta:
        width = max(len(str(key)) for key in snapshot.meta)
        for key, value in snapshot.meta.items():
            extra = ""
            if key == "createdAt":
                stamp = _iso_time(value)
                if stamp:
                    extra = f"  ({stamp})"
            _write(out, f"  {str(key).ljust(width)} = {_meta_value(value)}{extra}")
    else:
        _write(out, "  (empty)")
    _write(out, f"entities: {len(snapshot.entities)} records")
    if counts:
        _write(out, "types:")
        rows = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        for line in render_table(["type", "count"], rows):
            _write(out, "  " + line)
    else:
        _write(out, "types: (none)")
    _write(out, f"orderHash: meta {recorded if isinstance(recorded, str) else repr(recorded)}")
    matched = isinstance(recorded, str) and recorded == computed
    _write(out, f"orderHash: file {computed} ({'MATCH' if matched else 'MISMATCH'})")
    _write(out, f"validation: {len(issues)} error(s)")
    for line in format_issues(issues):
        _write(out, "  " + line)
    _write(out, f"result: {'OK' if not issues else 'INVALID'}")
    return 0 if not issues else 1


async def run_restore(
    fork_dir: Path | str,
    *,
    dry_run: bool = True,
    start: int = 0,
    limit: int | None = None,
    keep_going: bool = False,
    client: BridgeClient | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Print or issue ``/summon`` lines for a fork directory, in recorded order."""
    out = out or sys.stdout
    err = err or sys.stderr
    snapshot = load_snapshot(fork_dir)
    issues = validate_snapshot(snapshot)
    blockers = restore_blockers(snapshot)
    if blockers:
        _write(err, f"{snapshot.path}: refusing to restore, the recording cannot become commands:")
        for line in format_issues(blockers):
            _write(err, "  " + line)
        if issues:
            _write(
                err,
                f"  (run `inspect` for the whole picture: {len(issues)} validation issue(s) here)",
            )
        return 1
    if issues:
        _write(err, f"{snapshot.path}: {len(issues)} validation warning(s):")
        for line in format_issues(issues):
            _write(err, "  " + line)
    without_nbt = sum(1 for entity in snapshot.entities if not entity.nbt.strip())
    if without_nbt:
        _write(
            err,
            f"note: {without_nbt} record(s) carry no nbt, so they are summoned without their "
            "in-memory state and cannot be expected to restore faithfully",
        )
    try:
        chosen = select_entities(snapshot.entities, start=start, limit=limit)
        commands = [summon_command(entity) for entity in chosen]
    except SnapshotError as error:
        _write(err, f"{snapshot.path}: {error}")
        return 1
    last_index = start + len(chosen) - 1 if chosen else start
    if dry_run:
        _write(
            err,
            f"dry run: {len(commands)} command(s) for index {start}..{last_index} "
            f"of {len(snapshot.entities)}; pass --apply to send them",
        )
        for command in commands:
            _write(out, command)
        return 0
    if client is None:
        _write(err, "restore --apply needs a bridge client")
        return 2

    _write(
        err,
        f"issuing {len(commands)} /summon command(s), index {start}..{last_index}, "
        "through the bridge...",
    )
    issued = 0
    failures: list[str] = []
    for offset, command in enumerate(commands):
        index = start + offset
        try:
            await client.command(command)
        except Exception as error:  # noqa: BLE001 - one entity must not hide the rest of the run
            failures.append(f"index {index}: {error}")
            _write(err, f"[restore] index {index} failed: {error}")
            if not keep_going:
                break
            continue
        issued += 1
        if issued % PROGRESS_EVERY == 0:
            _write(err, f"[restore] {issued}/{len(commands)} command(s) issued")
    _write(err, f"[restore] issued {issued}/{len(commands)} command(s), {len(failures)} failure(s)")
    if failures:
        _write(
            out,
            f"restore: FAILED after {issued}/{len(commands)} command(s), "
            f"{len(failures)} failure(s)",
        )
        return 1
    _write(out, f"restore: issued {issued} command(s) in recorded order")
    return 0


def default_check_name(now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(time.time() if now is None else now))
    return f"forkverify-{stamp}"


def _snapshot_radius(snapshot: Snapshot) -> float | None:
    """The radius the recording used, so the fresh snapshot asks the same question."""
    radius = snapshot.meta.get("radius")
    if isinstance(radius, bool) or not isinstance(radius, (int, float)):
        return None
    value = float(radius)
    return value if value > 0 else None


async def run_check(
    fork_dir: Path | str,
    *,
    client: BridgeClient,
    name: str | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Re-snapshot the live instance and compare it with the fork: the acceptance test."""
    out = out or sys.stdout
    err = err or sys.stderr
    fork = load_snapshot(fork_dir)
    issues = validate_snapshot(fork)
    radius = _snapshot_radius(fork)
    snapshot_name = name or default_check_name()
    _write(
        err,
        f"asking the bridge for a fresh snapshot (name={snapshot_name}, "
        f"radius={radius if radius else 'every entity the level ticks'})",
    )
    ack = await client.snapshot(name=snapshot_name, radius=radius)
    if not isinstance(ack, dict) or not ack.get("dir"):
        raise SnapshotError(f"the bridge did not report a snapshot directory: {ack!r}")
    live = load_snapshot(Path(str(ack["dir"])))
    report = check_snapshot(fork, live)

    _write(out, f"fork: {describe_snapshot(fork, report.fork_hash)}")
    _write(out, f"live: {describe_snapshot(live, report.live_hash)}")
    rows: list[list[Any]] = [
        [type_name, fork_count, live_count, live_count - fork_count]
        for type_name, fork_count, live_count in report.rows
    ]
    rows.append(
        ["total", report.fork_count, report.live_count, report.live_count - report.fork_count]
    )
    for line in render_table(["type", "fork", "live", "delta"], rows):
        _write(out, "  " + line)
    _write(out, f"orderHash: {'MATCH' if report.hash_ok else 'MISMATCH'}")
    _write(out, f"counts: {'MATCH' if report.counts_ok else 'MISMATCH'}")

    # The file is the source of truth, so an ack that disagrees is a note, not a verdict.
    ack_entities = ack.get("entities")
    if isinstance(ack_entities, int) and not isinstance(ack_entities, bool):
        if ack_entities != len(live.entities):
            _write(
                out,
                f"note: the ack said {ack_entities} entities, the file has {len(live.entities)}",
            )
    ack_hash = ack.get("orderHash")
    if isinstance(ack_hash, str) and ack_hash != report.live_hash:
        _write(
            out,
            f"note: the ack said orderHash {ack_hash}, the file hashes to {report.live_hash}",
        )
    if issues:
        _write(out, f"note: the fork has {len(issues)} validation issue(s):")
        for line in format_issues(issues):
            _write(out, "  " + line)
    ok = report.ok and not issues
    _write(out, f"result: {'MATCH' if ok else 'MISMATCH'}")
    return 0 if ok else 1


def run_diff(
    fork_dir_a: Path | str,
    fork_dir_b: Path | str,
    *,
    top: int = DELTA_ROWS,
    out: TextIO | None = None,
) -> int:
    """Compare two recordings: order, counts, and the largest shared-entity deltas."""
    out = out or sys.stdout
    a = load_snapshot(fork_dir_a)
    b = load_snapshot(fork_dir_b)
    report = diff_snapshot(a, b)
    checked = [
        (name, snapshot, validate_snapshot(snapshot))
        for name, snapshot in (("A", a), ("B", b))
    ]

    _write(out, f"A: {describe_snapshot(a, report.a_hash)}")
    _write(out, f"B: {describe_snapshot(b, report.b_hash)}")
    if report.divergence is None:
        _write(out, f"order: identical, {len(a.entities)} vs {len(b.entities)} entities")
    else:
        index, left, right = report.divergence
        _write(out, f"order: first divergent index {index} (A={left or '-'}, B={right or '-'})")
        _write(
            out,
            f"order: common prefix {report.common_prefix} of "
            f"{min(len(a.entities), len(b.entities))}",
        )
    _write(out, f"orderHash: {'MATCH' if report.a_hash == report.b_hash else 'MISMATCH'}")
    rows: list[list[Any]] = [
        [type_name, left, right, right - left] for type_name, left, right in report.rows
    ]
    rows.append(["total", len(a.entities), len(b.entities), len(b.entities) - len(a.entities)])
    _write(out, "entity counts by type:")
    for line in render_table(["type", "A", "B", "delta"], rows):
        _write(out, "  " + line)

    limit = top or None
    _write(out, f"largest position deltas ({report.shared_uuids} uuid(s) present in both):")
    if not report.position_deltas:
        _write(out, "  (no entity has positions on both sides)")
    elif all(delta.magnitude == 0.0 for delta in report.position_deltas):
        _write(
            out,
            f"  (all {len(report.position_deltas)} shared entities sit at the same position)",
        )
    else:
        delta_rows = [
            [
                delta.uuid,
                delta.type,
                _num(delta.magnitude),
                *(_num(value) for value in delta.components),
            ]
            for delta in report.position_deltas[:limit]
        ]
        for line in render_table(["uuid", "type", "dist", "dx", "dy", "dz"], delta_rows):
            _write(out, "  " + line)

    comparable = len(report.velocity_deltas)
    _write(out, f"largest velocity deltas ({comparable} of those have vel on both sides):")
    if not report.velocity_deltas:
        _write(out, "  (no entity has vel on both sides)")
    elif all(delta.magnitude == 0.0 for delta in report.velocity_deltas):
        _write(out, f"  (all {comparable} shared entities have the same velocity)")
    else:
        delta_rows = [
            [
                delta.uuid,
                delta.type,
                _num(delta.magnitude),
                *(_num(value) for value in delta.components),
            ]
            for delta in report.velocity_deltas[:limit]
        ]
        for line in render_table(["uuid", "type", "|dv|", "dvx", "dvy", "dvz"], delta_rows):
            _write(out, "  " + line)

    for name, snapshot, found in checked:
        if found:
            _write(
                out,
                f"note: {name} ({snapshot.path}) has {len(found)} validation issue(s); "
                "run `inspect` - the comparison may be meaningless",
            )
    broken = any(found for _, _, found in checked)
    return 0 if report.identical and not broken else 1


# --------------------------------------------------------------------- selftest


def fake_record(
    uuid: str,
    entity_type: str,
    pos: Sequence[float],
    vel: Sequence[float] = (0.0, 0.0, 0.0),
    nbt: str = "{}",
    **extra: Any,
) -> dict[str, Any]:
    """One synthetic ``entities.jsonl`` line, shaped like the protocol's example."""
    record: dict[str, Any] = {
        "uuid": uuid,
        "type": entity_type,
        "entityId": 100,
        "pos": list(pos),
        "vel": list(vel),
        "yaw": 0.0,
        "pitch": 0.0,
        "nbt": nbt,
        "passengers": [],
        "vehicle": None,
    }
    record.update(extra)
    return record


def write_snapshot(
    directory: Path | str,
    records: Sequence[dict[str, Any]],
    *,
    entities_override: int | None = None,
    order_hash_override: str | None = None,
    meta_extra: dict[str, Any] | None = None,
) -> Path:
    """Write a synthetic snapshot directory; the selftest's only fixture builder."""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    uuids: list[str] = []
    for index, record in enumerate(records):
        item = dict(record)
        item.setdefault("order", index)
        lines.append(json.dumps(item, ensure_ascii=False))
        uuids.append(str(item.get("uuid") or ""))
    digest = hashlib.sha256(":".join(uuids).encode("utf-8")).hexdigest()[:HASH_HEX_CHARS]
    meta: dict[str, Any] = {
        "protocol": PROTOCOL_VERSION,
        "mod": "mc-agent-interface",
        "modVersion": "0.5.0-selftest",
        "minecraft": "26.2",
        "tick": 104233,
        "dimension": "minecraft:overworld",
        "radius": 64.0,
        "entities": len(records) if entities_override is None else entities_override,
        "orderHash": digest if order_hash_override is None else order_hash_override,
        "createdAt": 1790145600000,
        "frozen": True,
        "worldDir": None,
        "instance": "server",
    }
    if meta_extra:
        meta.update(meta_extra)
    (path / "entities.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (path / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


class FakeTransport:
    """Stand-in for ``mc_agent_bridge.local_api.LocalApiClient``: no sockets, no game."""

    def __init__(self, snapshot_dir: Path | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.commands: list[str] = []
        self.attempts = 0
        self.fail_on_command: set[int] = set()
        self.snapshot_dir = snapshot_dir
        self.connected = False
        self.closed = False

    async def connect(self, retry: bool = True) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "snapshot":
            if self.snapshot_dir is None:
                raise RuntimeError("FakeTransport has no snapshot_dir")
            snapshot = load_snapshot(self.snapshot_dir)
            return {
                "type": "snapshot_ack",
                "id": params.get("name"),
                "dir": str(snapshot.path),
                "entities": len(snapshot.entities),
                "orderHash": order_hash(snapshot.entities),
                "tick": snapshot.meta.get("tick"),
                "dimension": snapshot.meta.get("dimension"),
            }
        if method == "snapshots":
            return {"snapshots": []}
        if method == "fork":
            return {"name": params.get("name"), "dir": str(self.snapshot_dir or ""), "manifest": {}}
        if method == "restore":
            return {
                "dir": params.get("dir"),
                "dryRun": bool(params.get("dry_run", True)),
                "issued": 0,
            }
        if method == "order":
            return {"dir": params.get("dir"), "orderHash": None, "match": None}
        if method == "command":
            position = self.attempts
            self.attempts += 1
            if position in self.fail_on_command:
                raise RuntimeError("the game refused the command")
            self.commands.append(str(params.get("command")))
            return {"type": "ack", "detail": params.get("command")}
        raise AssertionError(f"unexpected bridge method: {method}")


class Selftest:
    """A tiny assertion accumulator: prints as it goes, fails the run at the end."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            _write(self.out, f"PASS  {name}")
        else:
            self.failures.append(name)
            _write(self.out, f"FAIL  {name}" + (f"  <- {detail}" if detail else ""))

    def equal(self, name: str, got: Any, want: Any) -> None:
        self.check(name, got == want, f"got {got!r}, want {want!r}")

    def contains(self, name: str, text: str, needle: str) -> None:
        self.check(name, needle in text, f"{needle!r} not found in:\n{text}")


async def run_selftest(out: TextIO | None = None, err: TextIO | None = None) -> int:
    """Exercise inspect/restore/check/diff against synthetic fixtures and a fake bridge."""
    out = out or sys.stdout
    err = err or sys.stderr
    test = Selftest(out)

    cube_a = "11111111-1111-1111-1111-111111111111"
    cube_b = "22222222-2222-2222-2222-222222222222"
    cow = "33333333-3333-3333-3333-333333333333"
    item = "44444444-4444-4444-4444-444444444444"
    base = [
        fake_record(
            cube_a,
            "minecraft:sulfur_cube",
            (100.5, 64.0, -20.5),
            (0.0, -0.0784, 0.0),
            "{Fuse:0}",
        ),
        fake_record(
            cube_b,
            "minecraft:sulfur_cube",
            (101.5, 64.0, -20.5),
            (0.1, 0.0, 0.0),
            "{Fuse:10}",
        ),
        fake_record(cow, "minecraft:cow", (50.0, 70.0, 3.0), nbt="{Health:10.0f}"),
        fake_record(
            item, "minecraft:item", (100.5, 63.0, -20.5), nbt='{Item:{id:"minecraft:bone",count:1}}'
        ),
    ]
    expected_commands = [
        "/summon minecraft:sulfur_cube 100.5 64.0 -20.5 {Fuse:0}",
        "/summon minecraft:sulfur_cube 101.5 64.0 -20.5 {Fuse:10}",
        "/summon minecraft:cow 50.0 70.0 3.0 {Health:10.0f}",
        '/summon minecraft:item 100.5 63.0 -20.5 {Item:{id:"minecraft:bone",count:1}}',
    ]

    with tempfile.TemporaryDirectory(prefix="fork-verify-selftest-") as tmp:
        root = Path(tmp)
        _write(err, f"fixtures: {root}")
        a = write_snapshot(root / "a", base)
        a_copy = write_snapshot(root / "a-copy", base)
        swapped = write_snapshot(root / "b-swapped", [base[0], base[2], base[1], base[3]])
        moved = write_snapshot(
            root / "c-moved",
            [
                base[0],
                base[1],
                fake_record(cow, "minecraft:cow", (50.5, 70.0, 3.0), nbt="{Health:10.0f}"),
                base[3],
            ],
        )
        retyped = write_snapshot(
            root / "d-retyped",
            [
                base[0],
                base[1],
                fake_record(cow, "minecraft:pig", (50.0, 70.0, 3.0), nbt="{Health:10.0f}"),
                base[3],
            ],
        )
        lying_hash = write_snapshot(
            root / "e-lying-hash", base, order_hash_override="0" * HASH_HEX_CHARS
        )
        broken = write_snapshot(
            root / "f-broken",
            [
                dict(base[0], order=5),
                dict(base[1], uuid=cube_a),
                {key: value for key, value in base[2].items() if key != "nbt"},
                dict(base[3], pos="not a position"),
            ],
            entities_override=99,
            order_hash_override="deadbeefdeadbeef",
        )

        # ---- the order hash itself
        independent_hash = hashlib.sha256(
            ":".join([cube_a, cube_b, cow, item]).encode("utf-8")
        ).hexdigest()[:HASH_HEX_CHARS]
        test.equal(
            "orderHash is sha256('<uuid>:<uuid>:...')[:16]",
            order_hash(load_snapshot(a).entities),
            independent_hash,
        )

        # ---- inspect
        buffer = io.StringIO()
        code = run_inspect(a, out=buffer)
        text = buffer.getvalue()
        test.equal("inspect exits 0 on a faithful snapshot", code, 0)
        test.contains("inspect prints meta fields", text, "orderHash")
        test.contains("inspect prints the entity count", text, "entities: 4 records")
        test.contains("inspect prints the type histogram", text, "minecraft:sulfur_cube")
        test.contains(
            "inspect recomputes the hash and matches it",
            text,
            f"orderHash: file {independent_hash} (MATCH)",
        )
        test.contains("inspect reports no validation errors", text, "validation: 0 error(s)")
        test.contains("inspect prints the verdict", text, "result: OK")

        buffer = io.StringIO()
        code = run_inspect(broken, out=buffer)
        text = buffer.getvalue()
        test.equal("inspect exits 1 on a broken snapshot", code, 1)
        for code_name in (
            "[order_mismatch]",
            "[uuid_duplicate]",
            "[missing_field]",
            "[bad_field]",
            "[entity_count]",
            "[order_hash]",
        ):
            test.contains(f"inspect reports {code_name}", text, code_name)
        test.contains("a broken snapshot ends INVALID", text, "result: INVALID")

        try:
            load_snapshot(root / "does-not-exist")
            test.check("a missing directory raises SnapshotError", False, "no error raised")
        except SnapshotError:
            test.check("a missing directory raises SnapshotError", True)

        # ---- diff
        buffer = io.StringIO()
        code = run_diff(a, a_copy, out=buffer)
        text = buffer.getvalue()
        test.equal("diff of an identical pair exits 0", code, 0)
        test.contains("identical pair: order is identical", text, "order: identical")
        test.contains("identical pair: hashes match", text, "orderHash: MATCH")
        test.contains("identical pair: no position drift", text, "sit at the same position")

        buffer = io.StringIO()
        code = run_diff(a, swapped, out=buffer)
        text = buffer.getvalue()
        test.equal("diff of a swapped pair exits 1", code, 1)
        test.contains(
            "swapped pair: first divergent index is 1", text, "order: first divergent index 1"
        )
        test.contains("swapped pair: hashes differ", text, "orderHash: MISMATCH")
        swap_report = diff_snapshot(load_snapshot(a), load_snapshot(swapped))
        test.equal(
            "swapped pair: the divergent uuids are reported",
            swap_report.divergence,
            (1, cube_b, cow),
        )
        test.check(
            "swapped pair: per-type counts are unchanged",
            all(left == right for _, left, right in swap_report.rows),
            f"{swap_report.rows}",
        )

        buffer = io.StringIO()
        code = run_diff(a, moved, out=buffer)
        text = buffer.getvalue()
        move_report = diff_snapshot(load_snapshot(a), load_snapshot(moved))
        test.equal("diff of a moved pair exits 1", code, 1)
        test.check(
            "a 0.5-block move keeps the tick order",
            move_report.divergence is None and move_report.a_hash == move_report.b_hash,
            f"divergence={move_report.divergence}",
        )
        top = move_report.position_deltas[0] if move_report.position_deltas else None
        test.equal(
            "the moved entity is the largest position delta",
            (top.uuid, top.type) if top else None,
            (cow, "minecraft:cow"),
        )
        test.check(
            "the largest position delta is exactly 0.5 blocks",
            top is not None and abs(top.magnitude - 0.5) < 1e-12,
            f"{top.magnitude if top else None}",
        )
        test.check(
            "the move leaves velocity deltas at zero",
            all(delta.magnitude == 0.0 for delta in move_report.velocity_deltas),
            f"{move_report.velocity_deltas}",
        )
        test.contains("diff prints the moved uuid", text, cow)

        # ---- restore
        fake = FakeTransport(snapshot_dir=a)
        client = BridgeClient(transport=fake)
        buffer = io.StringIO()
        errors = io.StringIO()
        code = await run_restore(a, dry_run=True, client=client, out=buffer, err=errors)
        test.equal("restore dry run exits 0", code, 0)
        test.equal(
            "restore dry run prints one /summon line per entity, in recorded order",
            buffer.getvalue().splitlines(),
            expected_commands,
        )
        test.equal("restore dry run sends nothing to the bridge", fake.commands, [])

        buffer = io.StringIO()
        code = await run_restore(a, dry_run=True, start=1, limit=2, out=buffer, err=io.StringIO())
        test.equal("restore --from/--limit exits 0", code, 0)
        test.equal(
            "--from 1 --limit 2 prints exactly that window",
            buffer.getvalue().splitlines(),
            expected_commands[1:3],
        )

        fake = FakeTransport(snapshot_dir=a)
        buffer = io.StringIO()
        code = await run_restore(
            a, dry_run=False, client=BridgeClient(transport=fake), out=buffer, err=io.StringIO()
        )
        test.equal("restore --apply exits 0", code, 0)
        test.equal(
            "restore --apply sends one command per entity, in recorded order",
            fake.commands,
            expected_commands,
        )
        test.equal(
            "restore --apply uses the bridge's command method",
            fake.calls[0],
            ("command", {"command": expected_commands[0]}),
        )
        test.contains("restore --apply reports success", buffer.getvalue(), "restore: issued 4")

        fake = FakeTransport(snapshot_dir=a)
        fake.fail_on_command = {1}
        code = await run_restore(
            a,
            dry_run=False,
            client=BridgeClient(transport=fake),
            out=io.StringIO(),
            err=io.StringIO(),
        )
        test.equal("a refused command makes restore --apply exit 1", code, 1)
        test.equal(
            "restore --apply stops at the first failure", fake.commands, expected_commands[:1]
        )

        fake = FakeTransport(snapshot_dir=a)
        fake.fail_on_command = {1}
        code = await run_restore(
            a,
            dry_run=False,
            keep_going=True,
            client=BridgeClient(transport=fake),
            out=io.StringIO(),
            err=io.StringIO(),
        )
        test.equal("--keep-going still exits 1 when a command failed", code, 1)
        test.equal(
            "--keep-going issues the remaining commands",
            fake.commands,
            [expected_commands[0], *expected_commands[2:]],
        )

        buffer = io.StringIO()
        code = await run_restore(broken, dry_run=True, out=buffer, err=io.StringIO())
        test.equal("restore refuses a recording it cannot turn into commands", code, 1)
        test.equal("restore prints no commands when it refuses", buffer.getvalue(), "")

        buffer = io.StringIO()
        errors = io.StringIO()
        code = await run_restore(lying_hash, dry_run=True, out=buffer, err=errors)
        test.equal("restore still runs when only the meta hash is wrong", code, 0)
        test.equal(
            "a lying meta hash still prints every command",
            buffer.getvalue().splitlines(),
            expected_commands,
        )
        test.contains("the bad hash is a warning, not a refusal", errors.getvalue(), "[order_hash]")

        # ---- check
        fake = FakeTransport(snapshot_dir=a_copy)
        buffer = io.StringIO()
        code = await run_check(
            a, client=BridgeClient(transport=fake), name="lab-1", out=buffer, err=io.StringIO()
        )
        text = buffer.getvalue()
        test.equal("check exits 0 when the lab reproduced the fork", code, 0)
        test.contains("check prints the per-type table", text, "minecraft:sulfur_cube")
        test.contains("check reports the hash match", text, "orderHash: MATCH")
        test.contains("check reports the counts match", text, "counts: MATCH")
        test.contains("check prints the verdict", text, "result: MATCH")
        test.equal(
            "check asks for exactly one fresh snapshot",
            [name for name, _ in fake.calls],
            ["snapshot"],
        )
        test.equal(
            "check asks for the fork's radius under the given name",
            fake.calls[0][1],
            {"name": "lab-1", "radius": 64.0},
        )

        fake = FakeTransport(snapshot_dir=swapped)
        code = await run_check(
            a,
            client=BridgeClient(transport=fake),
            name="lab-2",
            out=io.StringIO(),
            err=io.StringIO(),
        )
        test.equal("check exits 1 when the tick order changed", code, 1)

        fake = FakeTransport(snapshot_dir=retyped)
        buffer = io.StringIO()
        code = await run_check(
            a, client=BridgeClient(transport=fake), name="lab-3", out=buffer, err=io.StringIO()
        )
        text = buffer.getvalue()
        test.equal("check exits 1 when a type changed but the hash matched", code, 1)
        test.contains("retyped entity: the hash still matches", text, "orderHash: MATCH")
        test.contains("retyped entity: the counts mismatch", text, "counts: MISMATCH")

        # ---- the client's surface is the bridge contract
        transport = FakeTransport(snapshot_dir=a)
        client = BridgeClient(transport=transport)
        await client.connect()
        await client.snapshots()
        await client.fork(name="before", radius=64.0, world_dir="C:/world", regions=True)
        await client.restore(a, dry_run=True, target="lab")
        await client.order(a, target="lab")
        await client.snapshot(name="after", radius=64.0)
        await client.command(expected_commands[2])
        await client.close()
        test.equal(
            "bridge method names match the contract",
            [name for name, _ in transport.calls],
            ["snapshots", "fork", "restore", "order", "snapshot", "command"],
        )
        test.equal(
            "fork params",
            transport.calls[1][1],
            {"name": "before", "radius": 64.0, "world_dir": "C:/world", "regions": True},
        )
        test.equal(
            "restore params",
            transport.calls[2][1],
            {"dir": str(a), "dry_run": True, "target": "lab"},
        )
        test.equal("order params", transport.calls[3][1], {"dir": str(a), "target": "lab"})
        test.check(
            "the client connects and closes its transport",
            transport.connected and transport.closed,
        )

    _write(out, "")
    if test.failures:
        _write(
            out,
            f"selftest FAILED: {len(test.failures)} of {test.total} check(s) failed: "
            + ", ".join(test.failures),
        )
        return 1
    _write(out, f"selftest OK: {test.total} check(s) passed (no bridge, no game, no real snapshot)")
    return 0


# -------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fork_verify.py",
        description=(
            "Decide whether a fork of a live world is faithful (docs/protocol-snapshot.md)."
        ),
        epilog=(
            "restore is a dry run unless --apply is given; check exits non-zero on any mismatch."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="validate a fork directory and print what is in it")
    inspect.add_argument("fork_dir", help="snapshot directory holding meta.json and entities.jsonl")

    restore = sub.add_parser("restore", help="print or send the /summon commands in recorded order")
    restore.add_argument("fork_dir")
    restore.add_argument(
        "--api-port",
        type=int,
        default=DEFAULT_API_PORT,
        help=f"bridge loopback API port (default {DEFAULT_API_PORT})",
    )
    restore_mode = restore.add_mutually_exclusive_group()
    restore_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands instead of sending them (default)",
    )
    restore_mode.add_argument(
        "--apply", action="store_true", help="send the commands through the bridge"
    )
    restore.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="K",
        help="stop after K entities (0 = all remaining)",
    )
    restore.add_argument(
        "--from",
        dest="from_index",
        type=int,
        default=0,
        metavar="INDEX",
        help="start at this 0-based position in the recorded order",
    )
    restore.add_argument(
        "--keep-going",
        action="store_true",
        help="keep sending after a command fails, then report the failures",
    )

    check = sub.add_parser("check", help="re-snapshot the live instance and compare it with a fork")
    check.add_argument("fork_dir")
    check.add_argument(
        "--api-port",
        type=int,
        default=DEFAULT_API_PORT,
        help=f"bridge loopback API port (default {DEFAULT_API_PORT})",
    )
    check.add_argument(
        "--name",
        default=None,
        help="name for the fresh snapshot (default: forkverify-<utc timestamp>)",
    )

    diff = sub.add_parser("diff", help="compare two fork directories")
    diff.add_argument("fork_dir_a")
    diff.add_argument("fork_dir_b")
    diff.add_argument(
        "--top", type=int, default=DELTA_ROWS, metavar="N", help="rows per delta table (0 = all)"
    )

    sub.add_parser("selftest", help="run the built-in checks (no bridge, no game needed)")
    return parser


def _no_bridge(port: int, error: Exception) -> int:
    _write(sys.stderr, f"cannot reach a bridge daemon on {DEFAULT_API_HOST}:{port}: {error}")
    return 2


async def run_command(args: argparse.Namespace) -> int:
    if args.command == "inspect":
        return run_inspect(args.fork_dir)
    if args.command == "diff":
        return run_diff(args.fork_dir_a, args.fork_dir_b, top=args.top)
    if args.command == "selftest":
        return await run_selftest()
    if args.command == "restore":
        client: BridgeClient | None = None
        if args.apply:
            client = BridgeClient(port=args.api_port)
            try:
                await client.connect(retry=False)
            except OSError as error:
                return _no_bridge(args.api_port, error)
            except ImportError as error:
                return _no_bridge(args.api_port, error)
        try:
            return await run_restore(
                args.fork_dir,
                dry_run=not args.apply,
                start=args.from_index,
                limit=args.limit,
                keep_going=args.keep_going,
                client=client,
            )
        finally:
            if client is not None:
                await client.close()
    if args.command == "check":
        client = BridgeClient(port=args.api_port)
        try:
            await client.connect(retry=False)
        except OSError as error:
            return _no_bridge(args.api_port, error)
        except ImportError as error:
            return _no_bridge(args.api_port, error)
        try:
            return await run_check(args.fork_dir, client=client, name=args.name)
        finally:
            await client.close()
    raise ValueError(f"unknown command: {args.command}")


async def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return await run_command(args)
    except SnapshotError as error:
        _write(sys.stderr, f"error: {error}")
        return 2
    except RuntimeError as error:
        _write(sys.stderr, f"bridge error: {error}")
        return 2
    except OSError as error:
        _write(sys.stderr, f"error: {error}")
        return 2


def cli(argv: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(main(argv))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
