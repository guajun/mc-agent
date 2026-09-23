#!/usr/bin/env python3
"""Drive a Carpet fake player: the agent's body without a second client.

    python tools/fake_player.py spawn deepseek 103 95 52
    python tools/fake_player.py action deepseek look north
    python tools/fake_player.py action deepseek jump
    python tools/fake_player.py say "hello from the agent" --as deepseek
    python tools/fake_player.py status deepseek
    python tools/fake_player.py kill deepseek

Carpet fake players live on the *server* (the integrated server in single
player), so this needs no LAN, no second account and no second client. They are
ordinary player entities: they show up in the entity list, appear to other
players, can use and break blocks, and their state can be read authoritatively
with `/data get`.

Setup: Carpet must be installed, `/carpet commandPlayer true`, and the caller
needs command permission (in single player: cheats on).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mc_agent_bridge.local_api import LocalApiClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("spawn", "kill", "action", "say", "status", "list"))
    parser.add_argument("name", nargs="?", help="fake player name")
    parser.add_argument("rest", nargs="*", help="coordinates, sub-action or message")
    parser.add_argument("--as", dest="speaker", default="", help="say this as the fake player")
    parser.add_argument("--api-port", type=int, default=8765)
    parser.add_argument("--wait", type=float, default=1.5)
    return parser.parse_args()


async def run_command(client: LocalApiClient, command: str, wait: float) -> list[str]:
    """Send a command and collect the feedback it produced."""
    backlog = await client.call("events", {"limit": 1})
    cursor = max((event["seq"] for event in backlog["events"]), default=0)
    await client.call("command", {"command": command})
    await asyncio.sleep(wait)
    events = await client.call("events", {"since": cursor, "limit": 30})
    return [
        str(event.get("text") or "")
        for event in events["events"]
        if event.get("category") in ("game", "chat")
    ]


def show(lines: list[str]) -> None:
    if not lines:
        print("(no output)")
    for line in lines:
        print(line if all(ord(char) < 128 for char in line) else ascii(line))


async def main() -> int:
    args = parse_args()
    client = LocalApiClient(port=args.api_port)
    try:
        await client.connect(retry=False)
    except OSError as error:
        print(f"no bridge daemon on {args.api_port}: {error}", file=sys.stderr)
        return 2

    try:
        if args.action == "spawn":
            where = " ".join(args.rest[:3])
            command = f"player {args.name} spawn" + (f" at {where}" if where else "")
            show(await run_command(client, command, max(args.wait, 3.0)))
        elif args.action == "kill":
            show(await run_command(client, f"player {args.name} kill", args.wait))
        elif args.action == "action":
            if not args.rest:
                print("action needs a sub-action, e.g. 'look north'", file=sys.stderr)
                return 2
            show(await run_command(client, f"player {args.name} {' '.join(args.rest)}", args.wait))
        elif args.action == "say":
            text = " ".join(args.rest) or (args.name or "")
            speaker = args.speaker or args.name
            if speaker:
                # The fake player has no command permission of its own, but the
                # server can broadcast on its behalf: the message shows up as
                # [<speaker>] text and other players' chat hooks see it.
                command = f"execute as {speaker} run say {text}"
                lines = await run_command(client, command, max(args.wait, 2.5))
                if not lines:
                    payload = json.dumps({"text": f"[{speaker}] {text}"}, ensure_ascii=False)
                    lines = await run_command(client, f"tellraw @a {payload}", args.wait)
                show(lines)
            else:
                payload = json.dumps({"text": text}, ensure_ascii=False)
                show(await run_command(client, f"tellraw @a {payload}", args.wait))
        elif args.action == "status":
            for tag in ("Pos", "Motion", "Rotation"):
                lines = await run_command(client, f"data get entity {args.name} {tag}", args.wait)
                print(f"{tag:9} {' | '.join(lines) if lines else '(no answer)'}")
            view = await client.call("entities", {"radius": 256}, timeout=60)
            players = [
                entity
                for entity in view.get("entities", [])
                if entity.get("type") == "minecraft:player"
            ]
            print(f"client view: {len(players)} player entities visible")
            for entity in players:
                print(
                    "  id=%s class=%s pos=(%.2f, %.2f, %.2f) vel=(%.3f, %.3f, %.3f) yaw=%.1f"
                    % (
                        entity["id"],
                        entity.get("class"),
                        entity["x"],
                        entity["y"],
                        entity["z"],
                        entity["vx"],
                        entity["vy"],
                        entity["vz"],
                        entity["yaw"],
                    )
                )
        elif args.action == "list":
            view = await client.call("entities", {"radius": 256}, timeout=60)
            names = [
                entity
                for entity in view.get("entities", [])
                if entity.get("type") == "minecraft:player"
            ]
            print(f"{len(names)} player entities visible from this client")
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
