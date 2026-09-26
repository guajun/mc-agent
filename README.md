# Minecraft Agent Toolkit

Harness-neutral infrastructure for letting an Agent observe and act in a
Minecraft world from the server's authoritative point of view.

**[Documentation](https://guajun.github.io/mc-agent/)** ·
**[中文文档](https://guajun.github.io/mc-agent/zh/)**

## Architecture

`mc-agent-bridge` is the **Minecraft Agent Toolkit**. The repository still
ships the `mc-bridge` CLI for compatibility, and `mc-bridge run` starts the
Toolkit daemon (historically called the Bridge daemon). Toolkit and Bridge are
not two layers, and neither is a backend for Codex, Claude Code or Hermes.

```text
user -> Harness (Codex / Claude Code / another MCP client)
             |
             | MCP or mc-bridge call
             v
    Minecraft Agent Toolkit daemon <-> server-vantage Fabric mod <-> Minecraft server
             ^
             | optional signed event webhook
             |
       unattended Harness (for example Hermes)
```

The Harness and Toolkit run on the same machine. That can be a dedicated game
server or the computer running a single-player world's integrated server. The
mod and Toolkit listen on loopback; do not publish their control ports.

There are two ways to start work:

* **User-driven Harness.** The user starts Codex, Claude Code or another
  Harness. The Harness calls the Toolkit to resolve the caller and read only
  the live world context it needs.
* **Unattended Harness.** A separately configured receiver such as Hermes is
  awakened by the Toolkit's optional signed webhook, then calls the same
  Toolkit. A chat event can carry a short-lived `context_id` for the sender's
  identity, position and view at message receipt.

The portable [Toolkit Skill](docs/toolkit-skill.md), receiver-neutral webhook
sender and [Hermes unattended setup guide](docs/hermes-unattended.md) are
published. Signed end-to-end Hermes delivery still fails closed until the
header and delivery-ID interoperability tracked in
[mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7) lands.

## Components

| Term | Responsibility | Repository |
| --- | --- | --- |
| **Toolkit** | The `mc-agent-bridge` package: daemon, CLI, JSON-lines API, MCP adapter and optional webhook forwarder | [mc-agent-bridge](https://github.com/guajun/mc-agent-bridge) |
| **Toolkit daemon** | The long-running `mc-bridge run` process; “Bridge daemon” is its historical name | part of the Toolkit |
| **Fabric mod** | Authoritative server-side facts and primitives inside Minecraft | [mc-agent-interface-mod](https://github.com/guajun/mc-agent-interface-mod) |
| **Harness** | Runs the Agent and decides when and how to use tools | Codex, Claude Code, Hermes, or another caller |
| **Skill** | Portable operating instructions for a Harness; no transport or session logic | [`skills/minecraft-toolkit`](skills/minecraft-toolkit/SKILL.md) |
| **agent-loop** | Legacy compatibility listener for chat-driven Hermes/echo runs | [mc-agent-loop](https://github.com/guajun/mc-agent-loop) |

## Quick Start

Requirements: Minecraft 26.2 with Fabric Loader 0.19+ and Fabric API, Java 25,
and Python 3.11+.

```powershell
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py --minecraft-dir <instance> `
  --version 26.2-Fabric --jdk <jdk25>

git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]"

# Start Minecraft with the mod installed, then start the Toolkit daemon.
.venv/Scripts/mc-bridge run
.venv/Scripts/mc-bridge call status
.venv/Scripts/mc-bridge call capabilities
.venv/Scripts/mc-bridge call state
```

`mc-bridge run` discovers `<server-dir>/mc-agent-server/port.txt` and uses the
server vantage by default. Register `.venv/Scripts/mc-bridge.exe mcp` with a
Harness for MCP, or use `mc-bridge call <method> [json]` from any shell.

Start with `status` and `capabilities`; the live capability list is authoritative.
For a player-specific request use the stable UUID when possible:

```powershell
mc-bridge call player '{"player":"<uuid-or-name>"}'
mc-bridge call entities '{"radius":32}'
mc-bridge call command_output '{"command":"list"}'
```

See [Getting started](https://guajun.github.io/mc-agent/getting-started/)
for MCP setup, deployment layouts and the status of event-driven operation.

## Repository Scope

This repository contains the cross-project documentation, RFC history and
standalone utilities. It does not contain a Codex or Claude Code backend, model
client or conversation/session manager. The Toolkit reports facts and executes
requested primitives; the Harness retains judgement and authorization policy.

The older client-vantage and `mc-agent-loop` workflows remain available for
compatibility and testing, but they are not the default architecture. The
default chat trigger in the loop is `@agent`, not a Harness name.

## Documentation Development

```powershell
.venv/Scripts/pip install -r requirements-docs.txt
.venv/Scripts/python -m mkdocs serve
```

The site is built from `docs/` and `docs/zh/` with strict MkDocs link checking.

## License

MIT
