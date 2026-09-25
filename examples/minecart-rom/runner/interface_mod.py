"""Client for the in-game interface mod's server vantage (SNAPSHOT etc.).

The mod (``mc-agent-interface``) serves newline-delimited text commands on a
loopback port and answers with one JSON object per line. The server vantage
exposes the real ``ServerLevel.entityTickList`` through ``SNAPSHOT``: the
returned ``entities.jsonl`` is written in *tick order* and ``orderHash`` is the
first 16 hex of ``sha256(join(":", uuids))``. That is the authoritative entity
order a save file does not contain, and it is what the fixture binds its ready
evidence to instead of the ``@e`` selector visitation order.

This module is intentionally tiny and dependency-free; it speaks just enough of
the protocol for the fixture: ``PING``, ``STATE``, ``SNAPSHOTS`` and
``SNAPSHOT [radius] [name]``, plus reading the files the mod wrote.
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_SERVER_PORT = 25581
SNAPSHOT_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class InterfaceError(RuntimeError):
    pass


@dataclass
class InterfaceClient:
    """A reconnecting line client. All calls run on the caller's thread."""

    host: str = "127.0.0.1"
    port: int = DEFAULT_SERVER_PORT
    timeout: float = 20.0
    sock: socket.socket | None = None
    stream: Any = None  # io.TextIOWrapper from makefile

    def __post_init__(self) -> None:
        if not self.port:
            raise InterfaceError("the interface mod has no port configured")

    # -- connection ---------------------------------------------------------

    def connect(self) -> "InterfaceClient":
        self.close()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as error:
            raise InterfaceError(
                f"cannot reach the interface mod on {self.host}:{self.port}: {error}"
            ) from error
        self.sock.settimeout(self.timeout)
        self.stream = self.sock.makefile("rw", encoding="utf-8", newline="\n")
        return self

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                pass
            self.stream = None
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> "InterfaceClient":
        return self.connect()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- protocol -----------------------------------------------------------

    def _read_json(self, deadline_seconds: float) -> dict[str, Any]:
        assert self.stream is not None
        if self.sock is not None:
            self.sock.settimeout(deadline_seconds)
        while True:
            try:
                line = self.stream.readline()
            except (socket.timeout, TimeoutError) as error:
                raise InterfaceError("timed out waiting for the interface mod") from error
            if not line:
                raise InterfaceError("the interface mod closed the connection")
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    def request(
        self,
        line: str,
        *,
        match: Callable[[dict[str, Any]], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Send one command, skip pushed events, return the first matching reply."""
        if self.stream is None:
            self.connect()
        assert self.stream is not None
        try:
            self.stream.write(line + "\n")
            self.stream.flush()
        except OSError as error:
            raise InterfaceError(f"cannot send {line!r}: {error}") from error
        deadline = timeout_seconds if timeout_seconds is not None else self.timeout
        skipped = 0
        while True:
            message = self._read_json(deadline)
            if match is not None:
                if match(message):
                    return message
            else:
                message_type = str(message.get("type", ""))
                if message_type.endswith("_ack") or message_type in (
                    "state",
                    "snapshots",
                    "error",
                    "pong",
                ):
                    return message
            skipped += 1
            if skipped > 20000:
                raise InterfaceError(f"no reply to {line!r} after {skipped} event lines")

    def ping(self) -> bool:
        reply = self.request("PING", match=lambda message: message.get("type") == "pong")
        return reply.get("type") == "pong"

    def state(self) -> dict[str, Any]:
        return self.request("STATE", match=lambda message: message.get("type") == "state")

    def snapshot(self, name: str, radius: float = 0.0) -> dict[str, Any]:
        safe = SNAPSHOT_NAME_RE.sub("-", name or "fixture")
        reply = self.request(
            f"SNAPSHOT {radius:g} {safe}",
            match=lambda message: message.get("type") == "snapshot_ack" and message.get("id") == safe,
            timeout_seconds=max(self.timeout, 60.0),
        )
        if reply.get("type") == "error":
            raise InterfaceError(f"SNAPSHOT failed: {reply.get('message') or reply}")
        return reply


# --------------------------------------------------------------------------- snapshot files


def read_snapshot(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read a snapshot directory the mod wrote; entities are in tick order."""
    meta_path = directory / "meta.json"
    entities_path = directory / "entities.jsonl"
    if not meta_path.is_file():
        raise InterfaceError(f"{directory} has no meta.json; the snapshot is incomplete")
    meta = json.loads(meta_path.read_text("utf-8"))
    entities = [
        json.loads(line)
        for line in entities_path.read_text("utf-8").splitlines()
        if line.strip()
    ]
    entities.sort(key=lambda entry: int(entry.get("order", 0)))
    return meta, entities


def tick_order(entities: list[dict[str, Any]], entity_type: str | None = None) -> list[str]:
    """UUIDs in tick order, optionally filtered to one entity type."""
    return [
        str(entity["uuid"])
        for entity in entities
        if entity_type is None or entity.get("type") == entity_type
    ]


def by_uuid(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(entity["uuid"]): entity for entity in entities}


def lab_interface(lab: Path) -> tuple[int, Path]:
    """The server-vantage port and directory a lab recorded at provision time."""
    state_path = lab / "lab.json"
    if not state_path.is_file():
        raise InterfaceError(f"{lab} is not provisioned")
    state = json.loads(state_path.read_text("utf-8"))
    port = int(state.get("serverVantagePort") or 0)
    server_dir = Path(str(state.get("serverDir") or (lab / "mc-agent-server")))
    if not port:
        raise InterfaceError(
            f"{lab} has no server-vantage port; provision it with --vantage-port and --mod-jar"
        )
    return port, server_dir


# --------------------------------------------------------------------------- NBT comparison


def _extract_items(text: str) -> str:
    """The raw ``Items`` list fragment of an SNBT/data-get string, balanced.

    Keys may be quoted (``"Items":[...]``, mod SNBT) or not (``Items:[...]``,
    ``/data get``), so the key is located with a regex before balancing.
    """
    match = re.search(r'"?Items"?\s*:\s*\[', text)
    if not match:
        return ""
    index = text.index("[", match.start())
    depth = 0
    quote = False
    escaped = False
    for offset in range(index, len(text)):
        char = text[offset]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            quote = not quote
            continue
        if quote:
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return text[index : offset + 1]
    return ""


def canonical_items(text: str) -> str:
    """Whitespace/key-quote-insensitive form of an ``Items`` list.

    Both writers emit the same NBT types for the same item, so the only
    differences are layout: ``{"count":1,"Slot":0b}`` (mod SNBT) versus
    ``{count: 1, Slot: 0b}`` (``/data get``). Normalizing both makes the
    comparison component-aware without a full NBT parser.
    """
    fragment = _extract_items(text)
    if not fragment:
        return ""
    canonical = re.sub(r'"\s*([A-Za-z0-9_:.\-]+)\s*"\s*:', r"\1:", fragment)
    canonical = re.sub(r"[\s]+", "", canonical)
    return canonical


def compare_items(mod_nbt: str, rcon_items_text: str) -> dict[str, Any]:
    mod = canonical_items(mod_nbt)
    rcon = canonical_items(rcon_items_text) if "Items:[" in rcon_items_text else canonical_items(
        "Items:" + rcon_items_text
    )
    return {
        "match": bool(mod) and mod == rcon,
        "mod_items": mod,
        "rcon_items": rcon,
    }


def snapshot_dir(server_dir: Path, name: str) -> Path:
    return server_dir / "snapshots" / SNAPSHOT_NAME_RE.sub("-", name or "fixture")
