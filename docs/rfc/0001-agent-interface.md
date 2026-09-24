# RFC 0001: agent interface and bridge architecture

* Status: implemented - Phase 1 shipped, and the discussion is closed. What is
  still open moved to issues [#4](https://github.com/guajun/mc-agent/issues/4)
  (backpressure), [#5](https://github.com/guajun/mc-agent/issues/5) (richer
  game events), [#6](https://github.com/guajun/mc-agent/issues/6) (multiple
  clients) and [#7](https://github.com/guajun/mc-agent/issues/7) (push from the
  game to the agent)
* Scope: the generic plumbing between a Minecraft client and an agent runtime
* Out of scope: anything specific to one experiment or use case
* Modules: [interface mod](https://github.com/guajun/mc-agent-interface-mod) ·
  [bridge](https://github.com/guajun/mc-agent-bridge) ·
  [agent loop](https://github.com/guajun/mc-agent-loop)

## Context

An agent that can observe and act inside a Minecraft client needs three things
that are easy to conflate:

1. a way to read game state and ask the game to do something;
2. a way to know when something happened in the game;
3. a way for a model to turn (1) and (2) into decisions.

Conflating them produces the usual mess: a mod that knows about the current
experiment, a bridge that only works with one agent, and a loop that has to be
restarted whenever anyone touches the game.

## Goals

* One generic client-side interface, stable across game versions and usable by
  any client.
* Exactly one owner of the game connection, so the game is not disturbed when an
  agent restarts.
* Event history that survives an agent restart, so an agent can ask what it
  missed instead of needing to be alive at the right moment.
* Agent runtimes that are interchangeable, including ones that do not exist yet.
* Zero use-case logic in the core.

## Non-goals

* Deciding what experiments to run.
* A mandatory always-on supervisor. The agent may start the game, trigger
  something, wait, and stop - it does not need millisecond reactions.
* Server-side (Paper/Spigot) support in this phase.
* Fighting Minecraft's own abstractions - the interface exposes what the client
  already knows.

## Actors

| Actor | Responsibility | Does not |
| --- | --- | --- |
| interface mod | expose state/actions/events over a local socket | interpret them |
| bridge daemon | own the mod connection, buffer events, re-serve over loopback + MCP | think |
| agent loop | decide when the backend should think, deliver replies | know game internals |
| backend | reason (Hermes, Codex, a script) | touch sockets |

## Layer boundaries

* **mod <-> bridge**: line-delimited text, one JSON object per line, request and
  reply plus unsolicited events. Simple enough to debug with `telnet`, stable
  enough to version.
* **bridge <-> everything else**: newline-delimited JSON on loopback, request
  ids, event subscriptions, and an event cursor. Language-agnostic.
* **bridge <-> MCP clients**: an optional front-end with one tool per primitive.

Nothing above the bridge knows about the mod's wire format, and the mod knows
nothing about agents.

## Primitives (protocol v1)

`state`, `entities`, `command`, `chat`, `record_start`, `record_stop`, `wait`,
`screen`, `mark`, `connect`, `world`, `ping`, `capabilities`.

`connect` and `world` are the two "put me somewhere" primitives: a server, or a
single-player save. They exist because the agent should be able to start its own
session, and because the client is the only part that can decide when the client
is idle enough to do it.

Deliberately missing: pathfinding, inventory manipulation, block placing,
physics assertions. Those are use-case shaped; they belong behind a command or
in a later RFC with a concrete need.

As shipped (mod 0.5.x): the client vantage also offers `lan`, and the server
vantage adds `snapshot`. The list the mod reports in `capabilities` - not this
document - is the authority.

## Event model

The mod pushes `hello`, `chat`, `game`, `mark`, `sample_start`,
`sample_progress`, `sample_done`, and diagnostics; the bridge assigns a
monotonic sequence number, keeps a ring buffer, and lets a client replay from a
cursor (`events {since}` -> `{events, next, dropped}`).

Open questions - kept here as the record of what Phase 1 did not settle, each
one now tracked as an issue:

* **Richer game events.** [#5](https://github.com/guajun/mc-agent/issues/5)
* **Server-side wake-up.** [#7](https://github.com/guajun/mc-agent/issues/7)
* **A server-side adapter.** Partly overtaken by the mod's server vantage
  (0.5.0, port 25581): what is left is tick-rate streaming and richer events,
  [#5](https://github.com/guajun/mc-agent/issues/5). The client view
  interpolates, so it is not a measurement instrument. See
  `docs/player-identity.md`.
* **Backpressure.** [#4](https://github.com/guajun/mc-agent/issues/4)
* **Multiple clients.** [#6](https://github.com/guajun/mc-agent/issues/6)

## MCP or loopback API?

Both, but not as equals.

MCP is a *pull* interface: the client spawns the server, so the game can never
wake the agent through it. That makes MCP a good fit for "the model asks about
the world" and a bad fit for "react to what just happened".

The loopback API is therefore the primary contract, and MCP is a thin adapter on
top of it. The agent loop stays the active listener.

Open question: should the bridge also offer a WebSocket (push, multi-language)
alongside the JSON-lines socket? Deferred until a concrete need appears - folded
into [#7](https://github.com/guajun/mc-agent/issues/7).

## Decoupling

* The bridge and the loop are separate processes, separate repositories, and
  separate failure domains.
* The loop depends on the bridge *package* for the client class and on the
  daemon only over the socket; it does not import the daemon.
* The mod is a plain Fabric mod with no dependency on either. Since 0.5.0 it
  also ships a server vantage (see `docs/protocol-snapshot.md`).

## Phase 1 scope

Ship the plumbing above: mod protocol v1, bridge daemon with event replay, CLI,
optional MCP front-end, an agent loop with a Hermes backend, and tests that run
without a game.

Not in Phase 1: any use-case logic, a mandatory runtime supervisor, and the
design of phases 2+. Those will be proposed as separate RFCs when there is
something concrete to build.
