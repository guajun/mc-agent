# Minecraft Agent Toolkit

Let your AI agent inspect a Minecraft world, find players and entities, run
commands, and work with entity snapshots. The product is a Fabric mod plus a
single Go binary; there is no MCP server and no Python runtime requirement.

**[Start here](docs/getting-started.md)** · **[Documentation website](https://guajun.github.io/mc-agent/)** · **[中文 README](README.zh.md)**

## What can I do with it?

| Capability | Example request |
| --- | --- |
| Read the world | “Summarize the current world state and online players.” |
| Inspect players and entities | “Find my player and describe the entities around me.” |
| Run commands and read results | “List the online players.” |
| Capture entity-order snapshots | “Capture an entity snapshot for this experiment.” |
| Operate from an event | “A player chatted; read the chat-time context and answer safely.” |

Available operations depend on the connected mod. The agent checks
`capabilities` before using them. Your agent application supplies the model and
conversation; the Toolkit never calls a model.

## How it fits together

```text
You → Agent application → mc-agent CLI ─┐
       (the Harness)                    │ loopback IPC
                                        └→ mc-agent daemon ↔ mc-agent-interface mod ↔ Minecraft
```

| Piece | What to install | Where it runs |
| --- | --- | --- |
| Game mod | [`mc-agent-interface` 0.8.0+](https://github.com/guajun/mc-agent-interface-mod/releases) in the instance's `mods/` folder | the game server: dedicated, LAN host or single-player integrated server |
| Toolkit | one Go binary, `mc-agent-0.5.0` for Windows amd64, Linux amd64 or macOS arm64 | the harness execution environment (local, WSL, container or remote host) |
| Agent application | any harness that can run a command, such as Codex, Claude Code or Hermes | wherever the harness runs |
| Operating instructions | the portable [Toolkit Skill](docs/toolkit-skill.md) | installed once per harness |

A dedicated server needs only the mod. A LAN host needs only the mod; the
Toolkit connects to the actual published LAN port. A single-player world uses
the integrated server and the same mod. A server-side daemon is optional: the
same binary can run next to the server or next to the harness, and multiple
daemons connect independently. No SSH, extra public port, proxy or client-mod
relay is required.

## Quick start

**You need:** Minecraft **26.2**, Fabric Loader **0.19.5**, Fabric API
**0.161.0** and Java **25** for the game, and a supported machine for the
binary (Windows amd64, Linux glibc amd64, macOS arm64). No Python or Go is
required to run the released binary.

1. **Install the game mod.** Download `mc-agent-interface-0.8.0.jar` from the
   [mod releases](https://github.com/guajun/mc-agent-interface-mod/releases) (or
   [build it](docs/mod-building.md)), put it in `mods/` next to Fabric API, and
   open a world or start the server.

2. **Install the binary and Skill.**

    === "Linux amd64 / macOS arm64"

        ```bash
        curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
            | sh -s -- --version 0.5.0 --skill-harness codex
        ```

    === "Windows amd64"

        ```powershell
        iwr -useb https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.ps1 -OutFile install.ps1
        ./install.ps1 -Version 0.5.0 -SkillHarness codex
        ```

3. **Connect and check.**

    ```bash
    mc-agent daemon start
    mc-agent doctor
    mc-agent state
    ```

    For a remote or LAN server, register the target first with the mod's
    fingerprint and a server-minted credential; see
    [Install and first run](docs/getting-started.md).

## Documentation

- [Install and first run](docs/getting-started.md) — the mod, the binary, the
  first connection and the first agent call.
- [Installation reference](docs/install.md) — upgrade, uninstall, supported
  platforms, deployment placement.
- [Architecture](docs/concepts.md) — components, terminology and boundaries.
- [Tools](docs/tools.md) — the CLI command reference and capability model.
- [Toolkit Skill](docs/toolkit-skill.md) — the portable operating guide.
- [Unattended Hermes: webhook + Skill](docs/hermes-unattended.md) — event-driven
  operation and uptime requirements.
- [Troubleshooting](docs/troubleshooting.md) — connection, identity and write
  outcomes.

This repository holds the documentation, the authoritative Skill source and the
experiment utilities. Release bundles are generated from this repository at a
pinned commit; the binary and its installers live in
[mc-agent-bridge](https://github.com/guajun/mc-agent-bridge).

## License

MIT
