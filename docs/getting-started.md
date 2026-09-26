# Getting started

[中文](https://guajun.github.io/mc-agent/zh/getting-started/)

This guide brings up the server-vantage Minecraft Agent Toolkit and exercises
it first from the CLI, then from a Harness over MCP.

## Requirements

* Minecraft 26.2, Fabric Loader 0.19+ and Fabric API
* Java 25
* Python 3.11+
* a dedicated Fabric server or a single-player client whose integrated server
  loads the interface mod

A Harness is optional for the first check. Codex, Claude Code, Hermes and other
callers all use the same Toolkit; none needs a project-specific backend.

## 1. Build and install the Fabric mod

~~~powershell
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py --minecraft-dir <instance> --version 26.2-Fabric --jdk <jdk25>
~~~

Copy the jar from **dist/** next to Fabric API in the instance's **mods/**
directory and start the server or single-player world. The server entrypoint
writes the chosen port to **<server-dir>/mc-agent-server/port.txt**.

## 2. Install the Toolkit

~~~powershell
git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]"
~~~

The MCP extra is required only for Harnesses that use MCP. The daemon, CLI and
JSON-lines API work without it.

## 3. Start and inspect the Toolkit

Run the daemon from the server directory, or name that directory explicitly:

~~~powershell
.venv/Scripts/mc-bridge run
.venv/Scripts/mc-bridge run --server-dir "C:/path/to/server"
~~~

In another shell:

~~~powershell
.venv/Scripts/mc-bridge call status
.venv/Scripts/mc-bridge call capabilities
.venv/Scripts/mc-bridge call state
~~~

**status** explains discovery and connection state. **capabilities** is the
authority for operations supported by the installed mod. Default discovery
never guesses a port or falls back to client vantage.

Useful server-vantage calls:

~~~powershell
.venv/Scripts/mc-bridge call player '{"player":"<uuid-or-name>"}'
.venv/Scripts/mc-bridge call entities '{"radius":32}'
.venv/Scripts/mc-bridge call command_output '{"command":"list"}'
.venv/Scripts/mc-bridge call events '{"since":0,"limit":20}'
~~~

Prefer UUID for durable identity; a name is a convenience. Treat the result as
context, not as permission to run privileged commands.

## 4. Connect a user-driven Harness

Configure the Harness to spawn:

~~~text
C:/path/to/.venv/Scripts/mc-bridge.exe mcp
~~~

The MCP adapter is a client of the already-running daemon. In a new Harness
session, call **mc_status** and **mc_capabilities** before using **mc_player**,
**mc_state**, **mc_entities**, **mc_command_output** or another advertised tool.

A user request made outside the game does not have a chat snapshot. Resolve the
intended player with **mc_player**, then fetch only the additional live context
needed for the task.

## 5. Understand in-game events

Server chat events can include **context_id** and a compact context summary.
Fetch the full bounded bundle while it is still live:

~~~powershell
.venv/Scripts/mc-bridge call context '{"id":"<context_id>"}'
~~~

The default cache holds 256 bundles for 300 seconds. An expired or unknown ID
returns a structured status. Fresh state remains available through normal
Toolkit calls.

For unattended operation, **mc-bridge forward** can send selected events to one
signed webhook. Receiver/Harness routing, reply delivery and authorization are
separate configuration. See [Hermes and unattended operation](hermes-setup.md)
for current implementation status.

## 6. Compatibility paths

The client vantage is opt-in:

~~~powershell
mc-bridge run --vantage client --port-file "C:/path/to/mc-agent/port.txt"
~~~

Use it only for client-only operations such as screen state, opening a local
save or joining a server. The old **mc-agent-loop** remains available for
compatibility and tests. Its default trigger is **@agent**; it is not required
for a user-driven Harness and is planned to lose its Hermes model backend.

## Next steps

* [Installation](install.md) for discovery, upgrades and security boundaries
* [Architecture and terminology](concepts.md) for component ownership
* [Toolkit tools](tools.md) for the current CLI/MCP surface
* [Forking a live world](protocol-snapshot.md) for snapshots and restoration
