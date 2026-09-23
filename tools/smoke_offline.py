#!/usr/bin/env python3
"""Offline integration smoke test: fake mod + bridge daemon + agent loop.

Runs the real CLI entry points in subprocesses against a stand-in for the
in-game mod, so the whole chain between the three modules can be verified
without launching Minecraft. Useful after touching the protocol or the loop.

    python tools/smoke_offline.py

Expects the sibling repositories to be checked out next to this one:

    mc-agent/
      bridge/        (mc-agent-bridge)
      agent-loop/    (mc-agent-loop)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_SRC = ROOT / "bridge" / "src"
LOOP_SRC = ROOT / "agent-loop" / "src"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mod-port", type=int, default=25598, help="port for the fake mod")
    parser.add_argument("--api-port", type=int, default=8799, help="port for the bridge API")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    for path in (BRIDGE_SRC, LOOP_SRC):
        if not path.is_dir():
            print(f"missing {path}; check out the sibling repositories first")
            return 2

    sys.path.insert(0, str(ROOT / "bridge" / "tests"))
    sys.path.insert(0, str(BRIDGE_SRC))
    from fake_mod import FakeMod  # noqa: PLC0415 - needs the path fix-ups above

    from mc_agent_bridge.local_api import LocalApiClient  # noqa: PLC0415

    env = dict(os.environ, PYTHONPATH=f"{BRIDGE_SRC}{os.pathsep}{LOOP_SRC}")
    mod = FakeMod(args.mod_port)
    await mod.start()
    print(f"[smoke] fake mod listening on {args.mod_port}")

    daemon = subprocess.Popen(
        [
            sys.executable, "-m", "mc_agent_bridge", "run",
            "--mod-port", str(args.mod_port),
            "--api-port", str(args.api_port),
        ],
        env=env,
    )
    loop: subprocess.Popen | None = None
    failures: list[str] = []
    try:
        api = LocalApiClient(port=args.api_port)
        for _ in range(60):
            try:
                await api.connect(retry=False)
                break
            except OSError:
                await asyncio.sleep(0.25)

        status = await api.call("status")
        print(f"[smoke] daemon connected to mod: {status['connected']}")
        if not status["connected"]:
            failures.append("daemon did not connect to the mod")

        state = await api.call("state")
        print(f"[smoke] state passthrough: {state}")
        if state.get("type") != "state":
            failures.append("state passthrough failed")

        loop = subprocess.Popen(
            [
                sys.executable, "-m", "mc_agent_loop", "run",
                "--backend", "echo", "--trigger", "@codex",
                "--api-port", str(args.api_port),
                "--cooldown", "0", "--min-reply-interval", "0",
            ],
            env=env,
        )
        for _ in range(40):
            if (await api.call("status"))["api"]["clients"] >= 1:
                break
            await asyncio.sleep(0.25)
        print("[smoke] agent loop attached to the bridge")

        # The mod does not replay to a client that subscribed after the event,
        # so retry until the loop is definitely listening.
        for attempt in range(6):
            await mod.push(
                {
                    "type": "chat",
                    "text": f"@codex how fast is it going (#{attempt})",
                    "sender": "LiteralComponent{content='player_one'}",
                    "millis": int(time.time() * 1000),
                }
            )
            for _ in range(20):
                if any(line.startswith("CHAT ") for line in mod.lines):
                    break
                await asyncio.sleep(0.1)
            if any(line.startswith("CHAT ") for line in mod.lines):
                break

        replies = [line for line in mod.lines if line.startswith("CHAT ")]
        print(f"[smoke] lines received by the mod: {mod.lines}")
        if not replies or "player_one" not in replies[0]:
            failures.append("the loop did not answer in chat")

        replay = await api.call("events", {"since": 0, "category": "chat"})
        print(f"[smoke] event replay: {len(replay['events'])} chat event(s), next={replay['next']}")
        if not replay["events"]:
            failures.append("no chat events were buffered")

        await api.call("stop")
        await asyncio.sleep(1.0)
        await api.close()
    finally:
        for process in (loop, daemon):
            if process is not None and process.poll() is None:
                process.terminate()
        await mod.stop()

    if failures:
        print("[smoke] FAILED: " + "; ".join(failures))
        return 1
    print("[smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
