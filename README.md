# mc-agent

Infrastructure for letting agent runtimes observe and act inside a Minecraft
client - generically. No cannons, no sulfur cubes, no use-case logic in the
core: the framework moves data and the agent decides what it means.

## Modules

Three independent repositories, one job each:

| Module | Role | Repository |
| --- | --- | --- |
| **interface mod** | game adapter: a versioned local interface inside the client | [mc-agent-interface-mod](https://github.com/guajun/mc-agent-interface-mod) |
| **bridge** | agent-agnostic bridge: owns the mod connection, serves a loopback API and an optional MCP front-end | [mc-agent-bridge](https://github.com/guajun/mc-agent-bridge) |
| **agent loop** | the active side: listen for chat, wake a backend, reply | [mc-agent-loop](https://github.com/guajun/mc-agent-loop) |

```
 agent runtime            bridge daemon             interface mod        Minecraft
 ┌───────────────┐       ┌──────────────┐          ┌─────────────┐      ┌─────────┐
 │ Hermes / Codex│─chat─►│ loop / MCP   │◄─lines──►│ TCP 25580   │◄────►│ client  │
 │ your own loop │◄events│ JSONL 8765   │          │ events.jsonl│      │ 26.2    │
 └───────────────┘       └──────────────┘          └─────────────┘      └─────────┘
```

The same bridge serves every runtime: swap the agent without touching the game,
restart the agent without dropping the game connection.

## Why not MCP alone

MCP servers are spawned by the agent, so nothing in the game can wake an agent
through them. Event-driven behaviour needs a resident listener on the agent's
side; that is the loop, and MCP stays an optional *pull* interface. See
[RFC 0001](docs/rfc/0001-agent-interface.md).

## Open questions live in RFCs

Design discussion that is not settled is filed as an issue rather than decided
in code. Phase 1 ships the plumbing; later phases are not designed yet, on
purpose.

- [RFC 0001 - agent interface and bridge architecture](docs/rfc/0001-agent-interface.md) - issue: [#1](https://github.com/guajun/mc-agent/issues/1)

## Quick start

```bash
# 1. build and install the mod, then start the game
python build.py --minecraft-dir <instance> --version 26.2-Fabric --jdk <jdk25>

# 2. bridge: owns the mod connection
pip install -e mc-agent-bridge
mc-bridge run

# 3. agent loop: answers chat
pip install -e mc-agent-loop
mc-agent-loop run --backend hermes --trigger @codex
```

Now `@codex <anything>` in game chat reaches the backend, and the backend can
read or change the world through `mc_state`, `mc_entities`, `mc_command`,
`mc_record_start` and friends.

## Phase 1 scope

In scope, and shipped:

* a versioned, generic interface inside the client (state, entities, command,
  chat, recording, waiting, markers, events);
* a bridge that owns the connection and re-serves it on loopback, with event
  replay;
* an agent loop that turns chat into backend turns;
* a Hermes-first backend, with echo (tests) and Codex (experimental) adapters.

Explicitly out of scope for now:

* any use-case logic (experiments, cannons, analysis);
* a mandatory always-on deterministic runtime - the agent may start, wait and
  stop things itself;
* new game-side abstractions beyond the interface above;
* phases 2+ - they will be designed later, in RFCs.

## Design principles

1. **Decoupled by default.** Each module is useful alone; the seams are the wire
   protocol and the loopback API.
2. **One connection owner.** Exactly one process talks to the game.
3. **The agent is the actor.** The bridge is a tool, not a scheduler; things
   happen because an agent asked for them.
4. **No use-case logic in the core.** If it only matters for one experiment, it
   lives in that experiment.

## Repositories

```
mc-agent/            meta: docs and RFCs (this repo)
mc-agent-interface-mod/   Fabric client mod
mc-agent-bridge/          Python bridge, CLI and MCP front-end
mc-agent-loop/            Python agent loop and backends
```

## License

MIT
