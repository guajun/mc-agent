#!/usr/bin/env python3
"""Call one tool on the bridge's MCP server, exactly as an agent would.

    python tools/mcp_probe.py mc_entities --arg radius=64 --arg types=sulfur_cube
    python tools/mcp_probe.py mc_state

Handy for checking the agent-facing surface (summaries, filters, tool names)
without going through a model. Needs the bridge daemon running and the `mcp`
extra installed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", help="tool name, e.g. mc_state")
    parser.add_argument("--arg", action="append", default=[], help="KEY=VALUE, repeatable")
    parser.add_argument("--api-port", type=int, default=8765)
    parser.add_argument("--list", action="store_true", help="only list the tools")
    parser.add_argument("--binary", default=str(ROOT / ".venv" / "Scripts" / "mc-bridge.exe"))
    return parser.parse_args()


def coerce(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


async def main() -> int:
    args = parse_args()
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError:
        print("install the MCP extra first: pip install 'mc-agent-bridge[mcp]'", file=sys.stderr)
        return 2

    server = StdioServerParameters(
        command=args.binary,
        args=["mcp"],
        env=dict(os.environ, MC_AGENT_API_PORT=str(args.api_port)),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            if args.list:
                for tool in tools.tools:
                    print(tool.name)
                return 0

            arguments = {}
            for pair in args.arg:
                key, _, value = pair.partition("=")
                arguments[key] = coerce(value)
            result = await session.call_tool(args.tool, arguments)
            for block in result.content:
                print(getattr(block, "text", block))
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
