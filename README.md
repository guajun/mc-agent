# Minecraft Agent Toolkit

Let your AI agent inspect a Minecraft world, find players and entities, run
commands, and work with world snapshots. Use your existing MCP-compatible agent
application, or try the tools directly from a terminal.

**[Start here](docs/getting-started.md)** · **[Documentation website](https://guajun.github.io/mc-agent/)** · **[中文 README](README.zh.md)**

## What can I do with it?

| Capability | Example request |
| --- | --- |
| Read the world | “Summarize the current world state and online players.” |
| Inspect players and entities | “Find my player and describe the entities around me.” |
| Run commands and read results | “List the online players.” |
| Capture snapshots | “Capture an entity-order snapshot for this experiment.” |
| Repeat experiments | Fork a world and check restoration with the [world-fork guide](docs/protocol-snapshot.md). |

Available operations depend on the connected mod. The agent checks `capabilities`
before using them. Your agent application supplies the model and conversation.

## Install and make your first connection

**You need:** Minecraft **26.2**, Fabric Loader **0.19+**, Fabric API,
**JDK 25**, **Python 3.11+**, and Git. Run the game endpoint, Toolkit and agent
application on the same machine. A single-player world is a good starting point.

1. **Install the game mod.** Follow [the build and install steps](docs/getting-started.md), then open a world or start the server. The documented path currently builds the mod from source.
2. **Install the Toolkit** using the commands for your OS below, in a folder you want to keep.
3. **Check the connection, then connect your agent.** The [first-run guide](docs/getting-started.md) includes expected results and MCP settings.

### Windows · PowerShell

```powershell
git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/python -m pip install -e "mc-agent-bridge[mcp]"

# Replace with the game/server folder containing mc-agent-server/.
# Keep this terminal open while using the Toolkit.
.venv/Scripts/mc-bridge run --server-dir "C:/Minecraft/my-instance"
```

Open a **second terminal in the same folder**:

```powershell
.venv/Scripts/mc-bridge call status
.venv/Scripts/mc-bridge call capabilities
.venv/Scripts/mc-bridge call state
```

### macOS / Linux · shell

```bash
git clone https://github.com/guajun/mc-agent-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -e "mc-agent-bridge[mcp]"

# Replace with the game/server folder containing mc-agent-server/.
# Keep this terminal open while using the Toolkit.
.venv/bin/mc-bridge run --server-dir "/path/to/minecraft-instance"
```

Open a **second terminal in the same folder**:

```bash
.venv/bin/mc-bridge call status
.venv/bin/mc-bridge call capabilities
.venv/bin/mc-bridge call state
```

**Success:** `status` reports `connected: true` and `vantage: server`, and
`state` returns world data. No model account is needed for these checks.
If a check fails, use the [first-run help](docs/getting-started.md).

## How it fits together

```text
You → Agent application → Minecraft Agent Toolkit ↔ Fabric mod ↔ Minecraft world
       (the Harness)       MCP / CLI + daemon         server view
```

The mod reads the server's authoritative world state, including the integrated
server in single-player. The Toolkit keeps the game connection alive; the agent
application decides which tools to use. Its MCP adapter connects to that running
daemon. Game-control connections stay local (loopback).

| Piece | What to install |
| --- | --- |
| Game mod | [mc-agent-interface-mod](https://github.com/guajun/mc-agent-interface-mod) in your instance's `mods/` folder |
| Toolkit | [mc-agent-bridge](https://github.com/guajun/mc-agent-bridge), which provides the `mc-bridge` command |
| Agent application | Your existing MCP client (such as Codex, Claude Code or Hermes), or an agent able to run the CLI |
| Optional operating instructions | The portable [Toolkit Skill](docs/toolkit-skill.md) |

This repository holds documentation, the shared Skill and experiment utilities.
The standard setup needs the mod and Toolkit alongside your agent app.
See [Architecture](docs/concepts.md) for deployment choices and terminology.
Unattended Hermes still has a signed-delivery integration issue; its status and
setup live in the [separate guide](docs/hermes-unattended.md).

## Go further

- [Install, upgrade or remove components](docs/install.md)
- [Browse CLI commands and MCP tools](docs/tools.md)
- [Explore advanced guides, experiments and design history](docs/advanced.md)
- [Develop and preview this documentation](docs/contributing-docs.md)

## License

MIT
