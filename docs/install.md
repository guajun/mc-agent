# Installation

[中文](https://guajun.github.io/mc-agent/zh/install/)

[Getting started](getting-started.md) is the shortest working path. This page
records versions, placement, discovery and removal.

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

~~~powershell
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py --minecraft-dir <instance> --version 26.2-Fabric --jdk <jdk25>
~~~

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

~~~powershell
git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]"
~~~

Use **pip install -e mc-agent-bridge** without the MCP extra when only the
daemon, CLI or JSON-lines API is needed. An editable install upgrades with a
pull in that checkout; delete the virtual environment to uninstall it.

Verify the executable:

~~~powershell
.venv/Scripts/mc-bridge --help
.venv/Scripts/mc-bridge discover
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
mc-bridge call status
mc-bridge call capabilities
~~~

Client vantage is a deliberate legacy opt-in:

~~~powershell
mc-bridge run --vantage client --port-file "C:/minecraft/client/mc-agent/port.txt"
~~~

## Harness integration

For MCP, configure the Harness to spawn **mc-bridge.exe mcp**. This adapter
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
