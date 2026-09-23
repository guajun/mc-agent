#!/usr/bin/env python3
"""Run a command in the game and print the feedback it produced.

    python tools/game_cmd.py "player"
    python tools/game_cmd.py "player mcagent spawn at gua_jun" --wait 3
    python tools/game_cmd.py "data get entity gua_jun Pos"

This is how an agent reads what a command answered: the mod captures incoming
game messages (``events:game``), and command feedback arrives as one of them.
Server commands like Carpet's ``/player`` therefore need no extra plumbing -
send the command, then read the event stream.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from mc_agent_bridge.local_api import LocalApiClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", help="the command, with or without a leading slash")
    parser.add_argument("--api-port", type=int, default=8765)
    parser.add_argument("--wait", type=float, default=1.5, help="seconds to collect feedback")
    parser.add_argument("--limit", type=int, default=20, help="maximum feedback lines to print")
    parser.add_argument("--json", action="store_true", help="print the raw events")
    return parser.parse_args()


def show(text: str) -> str:
    """Keep the console out of the encoding business."""
    return text if all(ord(char) < 128 for char in text) else ascii(text)


async def main() -> int:
    args = parse_args()
    client = LocalApiClient(port=args.api_port)
    try:
        await client.connect(retry=False)
    except OSError as error:
        print(f"no bridge daemon on {args.api_port}: {error}", file=sys.stderr)
        return 2

    try:
        backlog = await client.call("events", {"category": "game", "limit": 1})
        cursor = max((event["seq"] for event in backlog["events"]), default=0)
        try:
            ack = await client.call("command", {"command": args.command})
        except RuntimeError as error:
            print(f"the game refused the command: {error}", file=sys.stderr)
            return 1
        print(f"sent: {ack.get('detail')}")
        await asyncio.sleep(args.wait)
        events = await client.call("events", {"since": cursor, "limit": args.limit})
        lines = [event for event in events["events"] if event.get("category") == "game"]
        if not lines:
            print("(no feedback: the command may have failed silently, or the server is quiet)")
        for event in lines:
            text = str(event.get("text") or "")
            print(json.dumps(event, ensure_ascii=True) if args.json else show(text))
    finally:
        await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
