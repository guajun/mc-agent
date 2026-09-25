# mc-agent

Let an AI agent **observe**, **drive** and **fork** a Minecraft world - and keep
the agent's runtime completely separate from the game.

Nothing here knows what you are researching. There is no cannon code, no TNT
logic, no analysis: the framework moves facts out of the game and intentions
into it, and the agent decides what they mean.

```
Hermes (or any runtime)  <->  bridge  <->  interface mod  <->  Minecraft
   judgement                 seam         in-game facts        the world
```

## What you get

| Piece | What it is | Where |
| --- | --- | --- |
| **interface mod** | a Fabric client *and* server mod that exposes state, entities, commands, chat, recording, snapshots and events over a loopback socket | [mc-agent-interface-mod](https://github.com/guajun/mc-agent-interface-mod) |
| **bridge** | the only process that talks to the game: a daemon, a JSON-lines API, an MCP front-end, and the composed tools built on top | [mc-agent-bridge](https://github.com/guajun/mc-agent-bridge) |
| **agent loop** | the active side: listens for chat, wakes a backend, answers - or runs a single turn on demand | [mc-agent-loop](https://github.com/guajun/mc-agent-loop) |
| **this repository** | the documentation, the tools that drive the whole thing, and the RFCs | you are here |

## Three ways to run the agent

The same mod and the same bridge serve all three. What changes is *which
instance* the agent is attached to.

| Track | The agent is | Use it for |
| --- | --- | --- |
| **live client** | a mod inside your own game | "look at what I am looking at", playing alongside a human |
| **live server** | a mod inside the server you play on - or inside the integrated server of your single-player world | authoritative state, driving fake players, reacting to what actually happened |
| **isolated lab** | a headless server the agent provisioned itself | repeatable physics: freeze, step, fork a world, run it a hundred times |

Track 3 is the one that makes experiments honest, and the reason this project
exists: a save file records blocks and entity NBT, but **not the entity tick
order** - and that order changes the result of anything computed entity by
entity. The mod can read it, the snapshot protocol records it, and
[`fork_verify.py`](fork-verify.md) proves a restore reproduced it.

## Quick start

```bash
git clone https://github.com/guajun/mc-agent && cd mc-agent

# 1. build the mod and drop it into your instance's mods/ folder
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py --minecraft-dir <instance> \
    --version 26.2-Fabric --jdk <jdk25>

# 2. the bridge owns the game connection
python -m venv .venv && .venv/Scripts/pip install -e "mc-agent-bridge[mcp]" -e mc-agent-loop
.venv/Scripts/mc-bridge run

# 3. talk to it
.venv/Scripts/mc-bridge call state
.venv/Scripts/mc-agent-loop run --backend hermes --trigger @codex
```

Then type `@codex what can you see?` in game chat, or `/mcagent state` in the
chat box to look at the interface yourself.

[Getting started :material-arrow-right:](getting-started.md){ .md-button .md-button--primary }
[Installation :material-arrow-right:](install.md){ .md-button }
[How it fits together :material-arrow-right:](concepts.md){ .md-button }

## No game handy?

The whole plumbing can be exercised without Minecraft:

```bash
python tools/smoke_offline.py                     # fake mod + daemon + loop
python tools/smoke_offline.py --backend hermes    # the real model drives the tools
```

## Documentation

* [Getting started](getting-started.md) - install, run, first conversation, first experiment
* [Installation](install.md) - versions, upgrades, uninstalls, and where everything lands
* [How it fits together](concepts.md) - the mental model, and where a new capability belongs
* [Who is the agent in game](player-identity.md) - a second client, a Carpet fake player, or a server mod
* [Forking a live world](protocol-snapshot.md) - the snapshot protocol and the restore recipe
* [Running the agent on Hermes](hermes-setup.md) - the runtime this was built against
* [Installing the Toolkit Skill](toolkit-skill.md) - one portable skill for every harness
* [Unattended Hermes](hermes-unattended.md) - webhook-triggered runs with the same skill
* [Headless lab servers](lab-server.md) - provision, start and command a lab
* [Auditable cold-start runs](coldstart-protocol.md) - the environment contract, trajectory sink and evidence-based judgement
* [Tools](tools.md) - every CLI and MCP tool
* [Troubleshooting](troubleshooting.md) - the traps, most of them found the hard way
* [RFCs](rfc/0001-agent-interface.md) - what is decided, and what is still open
