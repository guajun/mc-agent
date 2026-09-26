#!/usr/bin/env python3
"""Compare the acquired experiment copy against the operator's ready snapshot.

Re-reads the live world (RCON + interface SNAPSHOT) and reports every field
that differs from the source lab's ready record: cart set, order, NBT/inventory
hashes, machine base state, note value, hover seat and user pose.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNNER = Path("F:/mc-agent-worktrees/rom13/coldstart/examples/minecart-rom/runner")
sys.path.insert(0, str(RUNNER))

import fixture  # noqa: E402
import interface_mod  # noqa: E402

READY = Path(
    "F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-20260926T063100Z/"
    "operator/fixture/ready-snapshot.json"
)
OUT = Path(
    "F:/mc-agent-worktrees/rom13/coldstart/labs/rom20-20260926T063100Z/"
    "workspace/evidence/acquired-check.json"
)


def main() -> int:
    spec = fixture.load_spec(None)
    ready = fixture.read_json(READY)
    status = fixture.lab_status("rom20-exp")
    interface = interface_mod.InterfaceClient(port=27195, timeout=20).connect()
    console = fixture.Console("rom20-exp", verbose=False).connect()
    try:
        current = fixture.take_snapshot(
            console,
            spec,
            spawn_order=ready.get("spawn_order") or [],
            server=status,
            program=ready.get("program"),
            interface=interface,
            snapshot_name="exp-acquired",
        )
    finally:
        interface.close()
        console.close()

    differences: list[dict] = []
    matches: list[str] = []

    def compare(name: str, expected, actual) -> None:
        if expected == actual:
            matches.append(name)
        else:
            differences.append({"field": name, "expected": expected, "actual": actual})

    compare("tick_frozen", ready.get("tick_frozen"), current.get("tick_frozen"))
    compare("rcon_order", ready.get("rcon_order"), current.get("rcon_order"))
    compare("normalized_hash", ready.get("normalized_hash"), current.get("normalized_hash"))
    compare("tick_order", ready.get("tick_order"), current.get("tick_order"))
    compare("tick_order_hash", ready.get("tick_order_hash"), current.get("tick_order_hash"))
    compare(
        "interface.order_hash",
        (ready.get("interface") or {}).get("order_hash"),
        (current.get("interface") or {}).get("order_hash"),
    )
    compare("machine.ok", (ready.get("machine") or {}).get("ok"), (current.get("machine") or {}).get("ok"))
    compare("machine.note", (ready.get("machine") or {}).get("note"), (current.get("machine") or {}).get("note"))
    compare("user.UUID", (ready.get("user") or {}).get("UUID"), (current.get("user") or {}).get("UUID"))
    compare("user.Pos", (ready.get("user") or {}).get("Pos"), (current.get("user") or {}).get("Pos"))
    compare("user.Rotation", (ready.get("user") or {}).get("Rotation"), (current.get("user") or {}).get("Rotation"))
    compare("user.vehicle_uuid", (ready.get("user") or {}).get("vehicle_uuid"), (current.get("user") or {}).get("vehicle_uuid"))

    ready_carts = {record["uuid"]: record for record in ready.get("carts") or []}
    current_carts = {record["uuid"]: record for record in current.get("carts") or []}
    compare("cart.uuids", sorted(ready_carts), sorted(current_carts))
    for uuid in sorted(set(ready_carts) & set(current_carts)):
        compare(f"cart[{uuid}].nbt_sha256", ready_carts[uuid].get("nbt_sha256"), current_carts[uuid].get("nbt_sha256"))
        compare(f"cart[{uuid}].pos", ready_carts[uuid].get("pos"), current_carts[uuid].get("pos"))
        compare(f"cart[{uuid}].motion", ready_carts[uuid].get("motion"), current_carts[uuid].get("motion"))
        compare(f"cart[{uuid}].items", ready_carts[uuid].get("items"), current_carts[uuid].get("items"))
        compare(f"cart[{uuid}].spawn_index", ready_carts[uuid].get("spawn_index"), current_carts[uuid].get("spawn_index"))

    ready_seat = {record["uuid"]: record for record in ready.get("seat") or []}
    current_seat = {record["uuid"]: record for record in current.get("seat") or []}
    compare("seat.uuids", sorted(ready_seat), sorted(current_seat))
    for uuid in sorted(set(ready_seat) & set(current_seat)):
        compare(f"seat[{uuid}].nbt_sha256", ready_seat[uuid].get("nbt_sha256"), current_seat[uuid].get("nbt_sha256"))
        compare(f"seat[{uuid}].pos", ready_seat[uuid].get("pos"), current_seat[uuid].get("pos"))

    informational = {
        "ready_day_tick": ready.get("world_day_tick"),
        "current_day_tick": current.get("world_day_tick"),
        "ready_lab": ready.get("lab"),
        "current_lab": current.get("lab"),
        "ready_pid": (ready.get("server") or {}).get("pid"),
        "current_pid": (current.get("server") or {}).get("pid"),
    }
    report = {
        "ok_without_time": not [d for d in differences if not d["field"].startswith("world_")],
        "match_count": len(matches),
        "differences": differences,
        "matches": matches,
        "informational": informational,
        "snapshot": current,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "snapshot"}, indent=2, ensure_ascii=False))
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
