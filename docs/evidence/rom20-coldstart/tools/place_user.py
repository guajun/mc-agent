#!/usr/bin/env python3
"""Restore the fixture's operating user on the acquired experiment copy.

Spawns the Carpet fake player `romuser` (task player UUID
3ec122d5-fc27-4816-be47-bf8be8d7e56d), immediately freezes the world, mounts
the hover seat and aims at the note block, then leaves the world frozen.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RUNNER = Path("F:/mc-agent-worktrees/rom13/coldstart/examples/minecart-rom/runner")
sys.path.insert(0, str(RUNNER))

import fixture  # noqa: E402

LAB = "rom20-exp"
USER = "romuser"
AIM_TARGET = [11.5, -53.8, -23.0]


def main() -> int:
    console = fixture.Console(LAB, verbose=False).connect()
    try:
        if "game is frozen" in console.cmd("tick query").lower():
            console.cmd("tick unfreeze", idle=0.2)
        console.cmd(
            "player romuser spawn at 11.5 -53.0 -24.5 facing 0.0 50.0 "
            "in minecraft:overworld in creative",
            idle=0.4,
        )
        spawned = fixture.wait_for_entity(console, "Romuser", timeout=60.0)
        console.cmd("tick freeze", idle=0.3)
        if not spawned:
            print(json.dumps({"ok": False, "error": "Romuser never spawned"}))
            return 1
        console.cmd(
            "ride Romuser mount @e[type=minecraft:armor_stand,"
            "tag=minecart-rom-fixture,limit=1]",
            idle=0.4,
        )
        console.cmd(
            "tp @e[type=minecraft:armor_stand,tag=minecart-rom-fixture,limit=1] "
            "11.5 -53.0 -24.5",
            idle=0.4,
        )
        fixture.sprint(console, 1)
        aim = fixture.aim_at(console, "Romuser", AIM_TARGET)
        report = {
            "ok": True,
            "uuid": console.cmd("data get entity Romuser UUID", idle=0.2),
            "pos": console.cmd("data get entity Romuser Pos", idle=0.2),
            "rotation": console.cmd("data get entity Romuser Rotation", idle=0.2),
            "vehicle": console.cmd("data get entity Romuser Vehicle", idle=0.2),
            "tick_query": console.cmd("tick query", idle=0.2),
            "aim": aim,
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    finally:
        console.close()


if __name__ == "__main__":
    raise SystemExit(main())
