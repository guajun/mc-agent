---
hide:
  - toc
---

# Give your agent a Minecraft world

**Read the world. Run commands. Build repeatable experiments.**

Minecraft Agent Toolkit connects your AI agent to Minecraft through a small Go
CLI and a Fabric mod. Use your existing agent application, or try the tools from
a terminal first. There is no MCP server and no Python runtime requirement.

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

    Capture entity-order snapshots and keep controlled trials in place.

    [Entity snapshots and forks →](protocol-snapshot.md)

- **Use your preferred agent**

    Connect any harness that can run a command: Codex, Claude Code, Hermes or a
    shell script. Your app supplies the model and conversation.

    [Connect your agent →](getting-started.md#4-connect-your-agent)

</div>

**Verified in a real game:** [Minecart ROM](minecart-rom-runbook.md) completed
its toolchain gate, autonomous agent cold start and fresh-instance regression
on 2026-09-26. [See the results and reproduction paths →](advanced.md#verified-example)

## Get connected in four steps

**Before you start:** Minecraft 26.2, Fabric Loader 0.19.5, Fabric API 0.161.0
and Java 25 for the game, plus a supported machine for the binary (Windows
amd64, Linux glibc amd64 or macOS arm64). Install the binary in the environment
that runs your agent; the game server needs only the mod.

1. **Install the Fabric mod.** Download the 0.8.0 jar (or build it), copy it into `mods/`, and open your world.
2. **Install the binary and Skill.** Use the versioned installer for your platform.
3. **Check the connection.** Start the daemon; run `doctor`, `capabilities` and `state`.
4. **Connect your agent.** Point the harness at the `mc-agent` command; no MCP registration is involved.

The [step-by-step guide](getting-started.md) includes Windows, macOS and Linux
commands, remote/LAN target setup, the Skill install and expected results. The
first connection check does not need an AI model.

<div class="landing-actions" markdown>

[Start the installation](getting-started.md){ .md-button .md-button--primary }
[Already installed? Check your connection](getting-started.md#3-check-the-connection){ .md-button }

</div>

## How it works

```text
You → Agent application → mc-agent CLI → mc-agent daemon ↔ Fabric mod ↔ Minecraft world
       (the Harness)       short call     long connection  server view
```

| Piece | Its job |
| --- | --- |
| Agent application (Harness) | Runs your model and conversation; decides which tools to call. |
| Toolkit (`mc-agent`) | Provides the CLI and the daemon that keeps the connection to Minecraft alive. |
| Fabric mod | Reads server-side world state and executes requested operations, including in single-player. |

The daemon runs while you play and reconnects on its own; the CLI is a short
call that talks to it over loopback. Available operations are discovered from
the connected mod, and game-control connections stay local unless you choose a
remote target.

[Architecture and deployment details →](concepts.md)

## Find your next step

| I want to… | Go to |
| --- | --- |
| Install, upgrade or change the game path | [Installation reference](install.md) |
| Fix a failed connection | [First-run help](getting-started.md#something-didnt-work) · [Troubleshooting](troubleshooting.md) |
| Teach my agent the Toolkit workflow | [Toolkit Skill](toolkit-skill.md) |
| Set up events, unattended agents or experiments | [Advanced guides](advanced.md) |
