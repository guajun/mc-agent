# Installation reference

[中文](https://guajun.github.io/mc-agent/zh/install/)

**First installation? Follow [Install and first run](getting-started.md).** It
includes OS-specific commands, mod copying, connection checks and MCP settings.
This page covers deployment settings, upgrades, discovery and removal.

## Requirements and placement

| Component | Requirement | Runs on |
| --- | --- | --- |
| Fabric mod | Minecraft 26.2, Fabric Loader 0.19+, Fabric API, Java 25 | dedicated server or single-player integrated server |
| Toolkit (`mc-agent-bridge`) | Python 3.11+ | the same machine as the mod endpoint and Harness |
| Harness | MCP support or permission to run the CLI | the same machine as the Toolkit |

Keeping the mod socket and Toolkit API on loopback is a deployment assumption,
not an inconvenience to work around. Do not expose either game-control port to
the Internet.

## Fabric mod

Use the [mod build and installation steps](getting-started.md#1-install-the-game-mod).
The build resource root (containing `versions/` and `libraries/`) can differ from
the running instance directory. The first-run guide explains both paths.

Copy the built jar and Fabric API into the server or client instance's
**mods/** directory. The same jar has client and server entrypoints. The
Toolkit uses the server entrypoint by default, including the integrated server
in a single-player world.

| Property | Default | Purpose |
| --- | --- | --- |
| **mcagent.serverDir** | **mc-agent-server** | server-vantage state, snapshots and port file |
| **mcagent.serverPort** | **25581** | first loopback port to try |
| **mcagent.contextCacheSize** | **256** | maximum chat context bundles |
| **mcagent.contextCacheTtlSeconds** | **300** | bundle lifetime |
| **mcagent.dir** / **mcagent.port** | **<gameDir>/mc-agent** / **25580** | legacy client-vantage endpoint |

To upgrade, replace the jar and keep exactly one version. To uninstall, remove
the jar; remove the data directories too only when their events and snapshots
are no longer needed.

## Toolkit (`mc-agent-bridge`)

Use the [Toolkit installation commands](getting-started.md#2-install-the-toolkit)
for Windows or macOS/Linux.

Use **pip install -e mc-agent-bridge** without the MCP extra when only the
daemon, CLI or JSON-lines API is needed. An editable install upgrades with a
pull in that checkout; delete the virtual environment to uninstall it.

The commands below assume the virtual environment is active. Alternatively use
the explicit `.venv/Scripts/mc-bridge` (Windows) or `.venv/bin/mc-bridge`
(macOS/Linux) executable from your working folder, as in the first-run guide.
Verify the executable:

~~~powershell
mc-bridge --help
mc-bridge discover --server-dir "C:/minecraft/server"
~~~

## Server-vantage discovery

The default **mc-bridge run** resolution order is:

1. explicit **--mod-port**;
2. **--port-file** or **MC_AGENT_PORT_FILE**;
3. **<--server-dir | MC_AGENT_SERVER_DIR | current directory>/mc-agent-server/port.txt**;
4. **<--server-dir>/port.txt** when a server directory was explicitly named.

If none resolves, the daemon reports an error and keeps watching. It never
guesses a server port or silently connects to client vantage.

~~~powershell
mc-bridge run --server-dir "C:/minecraft/server"
~~~

Leave the daemon running. In a second terminal, using the same environment:

~~~powershell
mc-bridge call status
mc-bridge call capabilities
~~~

Client vantage is a deliberate legacy opt-in:

~~~powershell
mc-bridge run --vantage client --port-file "C:/minecraft/client/mc-agent/port.txt"
~~~

## Harness integration

For MCP, use the [MCP settings and example configuration](getting-started.md#4-connect-your-agent).
Configure the Harness to spawn **mc-bridge mcp** from its absolute path. This adapter
connects to the long-running daemon; it does not replace the daemon. For a
Harness without MCP, use **mc-bridge call** or the loopback JSON-lines API.

No **mc-agent-loop** installation is required for Codex, Claude Code or another
user-driven Harness. The loop is an optional compatibility package for its echo
tests and temporary Hermes HTTP path.

## Verify

| Check | Expected result |
| --- | --- |
| server log | server vantage armed/listening |
| **mc-agent-server/port.txt** | contains the actual loopback port |
| **mc-bridge discover** | reports **vantage: server** and its source |
| **mc-bridge call status** | daemon and mod connected |
| **mc-bridge call capabilities** | filtered server tool surface |
| **mc-bridge call player** | structured found/not-found player result |

## Removal

| Component | Remove |
| --- | --- |
| Fabric mod | jar from **mods/**; optionally **mc-agent-server/** |
| Toolkit | its virtual environment and checkout |
| Harness configuration | the MCP server entry and any webhook secret/route |
| lab instances | only the chosen directory under **labs/** |
