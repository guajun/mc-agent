---
hide:
  - toc
---

# Give your agent a Minecraft world

**Read the world. Run commands. Build repeatable experiments.**

Minecraft Agent Toolkit connects your AI agent to Minecraft through local tools.
Use your existing agent application, or try the tools from a terminal first.

<div class="landing-actions" markdown>

[Install and try it](getting-started.md){ .md-button .md-button--primary }
[See the tools](tools.md){ .md-button }

</div>

## What you can do

<div class="grid cards" markdown>

- **Understand the world**

    Read authoritative world state, find online players, and inspect nearby entities.

    Try: “Summarize the current world and online players.”

- **Act through tools**

    Run Minecraft commands and read their results from your agent or terminal.

    Try: “List the online players.”

- **Make experiments repeatable**

    Capture entity-order snapshots, fork worlds, and check restoration.

    [Explore world forks →](protocol-snapshot.md)

- **Use your preferred agent**

    Connect an MCP client such as Codex, Claude Code or Hermes. Agents with shell
    access can also use the CLI. Your app supplies the model and conversation.

    [Connect your agent →](getting-started.md#4-connect-your-agent)

</div>

## Get connected in four steps

**Before you start:** Minecraft 26.2, Fabric Loader 0.19+, Fabric API, JDK 25,
Python 3.11+, and Git. Keep Minecraft, the Toolkit and the agent on the same
machine. Start with a single-player world or an existing dedicated Fabric server.

1. **Install the Fabric mod.** Build it from source, copy the jar into `mods/`, and open your world.
2. **Install the Toolkit.** Clone `mc-agent-bridge` and install it into a Python virtual environment.
3. **Check the connection.** Start the daemon in one terminal; run `status`, `capabilities` and `state` in another.
4. **Connect your agent.** Register the MCP adapter, then ask the agent to describe your world.

The [step-by-step guide](getting-started.md) includes Windows and macOS/Linux
commands, path examples, MCP settings and expected results. The first connection
check does not need an AI model. Installation currently includes building the
mod; there is no all-in-one installer in this guide.

<div class="landing-actions" markdown>

[Start the installation](getting-started.md){ .md-button .md-button--primary }
[Already installed? Check your connection](getting-started.md#3-check-the-connection){ .md-button }

</div>

## How it works

```text
You → Agent application → Minecraft Agent Toolkit ↔ Fabric mod ↔ Minecraft world
       (the Harness)       MCP / CLI + daemon         server view
```

| Piece | Its job |
| --- | --- |
| Agent application (Harness) | Runs your model and conversation; decides which tools to call. |
| Toolkit (`mc-agent-bridge`) | Provides the `mc-bridge` command and MCP tools; keeps the connection to Minecraft alive. |
| Fabric mod | Reads server-side world state and executes requested operations, including in single-player. |

The Toolkit daemon runs while you play. Your agent's MCP adapter connects to it.
Game-control connections stay on the local machine, and available tools are
discovered from the connected mod.

[Architecture and deployment details →](concepts.md)

## Find your next step

| I want to… | Go to |
| --- | --- |
| Install, upgrade or change the game path | [Installation reference](install.md) |
| Fix a failed connection | [First-run help](getting-started.md#something-didnt-work) · [Troubleshooting](troubleshooting.md) |
| Teach my agent the Toolkit workflow | [Optional Toolkit Skill](toolkit-skill.md) |
| Set up events, unattended agents or experiments | [Advanced guides](advanced.md) |

Unattended Hermes is an advanced integration with a pending signed-delivery
issue. Its [setup and status](hermes-unattended.md) are separate from the
interactive setup above.
