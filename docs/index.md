# Minecraft Agent Toolkit

Give an Agent an authoritative, local tool surface for a Minecraft world without
putting model or Harness logic inside the game.

[中文](https://guajun.github.io/mc-agent/zh/)

## One Toolkit, any Harness

```text
user -> Harness ---------------- MCP / CLI -------------------+
                                                             |
game chat -> context bundle -> signed webhook -> Harness     |
                                                             v
Minecraft server <-> server-vantage Fabric mod <-> Minecraft Agent Toolkit daemon
```

The normal path starts with a user in Codex, Claude Code or another Harness.
The Harness and Toolkit run on the same machine and the Harness pulls the live
player or world context it needs.

The unattended path starts with an event. The server-vantage mod captures the
sender's identity, transform and view when chat is received; the event carries
a short-lived `context_id`. The optional Toolkit forwarder sends selected events
to a separately configured receiver such as Hermes. That Harness then reads the
bundle and uses the same Toolkit as every other caller.

!!! info "Implementation status"
    Server-vantage tools, player lookup, chat-time context bundles and the
    receiver-neutral signed webhook sender and portable
    [Toolkit Skill](toolkit-skill.md) are implemented. The
    [Hermes unattended guide](hermes-unattended.md) documents the receiver,
    route and delivery setup, but signed end-to-end delivery remains blocked by
    [mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7).
    The older `mc-agent-loop` remains a compatibility path, not a required
    Toolkit component.

## Components

| Name | Runs where | Responsibility |
| --- | --- | --- |
| **Minecraft Agent Toolkit** | beside the Harness and mod endpoint | the `mc-agent-bridge` package: daemon, CLI, JSON-lines API, MCP adapter and webhook forwarder |
| **Toolkit daemon** | local Python process | the long-running `mc-bridge run` process; formerly called the Bridge daemon |
| **Fabric mod** | dedicated server or single-player integrated server | exposes authoritative state and actions over loopback |
| **Harness** | same machine as the Toolkit | runs the Agent, owns sessions and decides what to do |
| **Skill** | loaded by a Harness | portable instructions for operating the Toolkit; no protocol or session code |
| **agent-loop** | optional legacy process | listens for chat and calls the temporary Hermes or echo backend |

There is no Codex backend, Claude Code backend or Hermes-specific game API.
Harnesses share the same Toolkit.

## Quick start

```powershell
# Install the Toolkit (the mc-agent-bridge repository).
git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]"

# After installing the Fabric mod and starting Minecraft:
.venv/Scripts/mc-bridge run
.venv/Scripts/mc-bridge call status
.venv/Scripts/mc-bridge call capabilities
.venv/Scripts/mc-bridge call state
```

The default is the server vantage. It discovers
`<server-dir>/mc-agent-server/port.txt`; it never silently falls back to a
client endpoint. A Harness can spawn `mc-bridge mcp`, while a shell or a
Harness without MCP can use `mc-bridge call`.

[Getting started :material-arrow-right:](getting-started.md){ .md-button .md-button--primary }
[Architecture and terms :material-arrow-right:](concepts.md){ .md-button }
[Installation :material-arrow-right:](install.md){ .md-button }

## Documentation

* [Getting started](getting-started.md) - server-vantage setup and first calls
* [Installation](install.md) - versions, deployment and discovery
* [Architecture and terminology](concepts.md) - component boundaries and both invocation modes
* [Toolkit commands and MCP tools](tools.md) - current surface and capability discovery
* [Installing the Toolkit Skill](toolkit-skill.md) - one portable Skill for supported Harnesses
* [Hermes and unattended operation](hermes-unattended.md) - webhook route, restricted tools and delivery status
* [Hermes compatibility backend](hermes-setup.md) - the temporary agent-loop path
* [Player identity](player-identity.md) - caller context, fake players and legacy client identities
* [Forking a live world](protocol-snapshot.md) - snapshots and restore
* [Headless lab servers](lab-server.md) - isolated experiment instances
* [Auditable cold-start runs](coldstart-protocol.md) - Harness execution and evidence contract
* [Troubleshooting](troubleshooting.md) - connection and context failures
* [RFCs](rfc/0001-agent-interface.md) - historical decisions and superseding plans
