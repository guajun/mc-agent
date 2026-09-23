# RFC 0001: agent interface and bridge architecture

* Status: draft, open for discussion
* Scope: the generic plumbing between a Minecraft client and an agent runtime
* Out of scope: anything specific to one experiment or use case

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
`screen`, `mark`, `connect`, `ping`, `capabilities`.

Deliberately missing: pathfinding, inventory manipulation, block placing,
physics assertions. Those are use-case shaped; they belong behind a command or
in a later RFC with a concrete need.

## Event model

The mod pushes `hello`, `chat`, `game`, `mark`, `sample_start`,
`sample_progress`, `sample_done`, and diagnostics; the bridge assigns a
monotonic sequence number, keeps a ring buffer, and lets a client replay from a
cursor (`events {since}` -> `{events, next, dropped}`).

Open questions:

* **Richer game events.** The client offers no general event bus. Which events
  are worth subscribing to (entity spawn/remove, damage, explosions, player
  joins/leaves), and is polling `entities` at a tick interval good enough for
  the rest? Deferred: Phase 1+.
* **Server-side wake-up.** A client mod cannot call into an agent. Any
  push-triggered flow is therefore agent-side, or requires a small sidecar that
  the agent polls or that speaks MCP over a long-lived connection.
* **Backpressure.** Cursors plus `dropped` are a start; a slow consumer currently
  loses old events. Is a per-subscriber queue worth it?
* **Multiple clients.** The mod accepts several connections and broadcasts to
  all. Should the bridge arbitrate, so two agents cannot fight over the same
  player?

## MCP or loopback API?

Both, but not as equals.

MCP is a *pull* interface: the client spawns the server, so the game can never
wake the agent through it. That makes MCP a good fit for "the model asks about
the world" and a bad fit for "react to what just happened".

The loopback API is therefore the primary contract, and MCP is a thin adapter on
top of it. The agent loop stays the active listener.

Open question: should the bridge also offer a WebSocket (push, multi-language)
alongside the JSON-lines socket? Deferred until a concrete need appears.

## Decoupling

* The bridge and the loop are separate processes, separate repositories, and
  separate failure domains.
* The loop depends on the bridge *package* for the client class and on the
  daemon only over the socket; it does not import the daemon.
* The mod is a plain client mod with no dependency on either.

## Phase 1 scope

Ship the plumbing above: mod protocol v1, bridge daemon with event replay, CLI,
optional MCP front-end, an agent loop with a Hermes backend, and tests that run
without a game.

Not in Phase 1: any use-case logic, a mandatory runtime supervisor, and the
design of phases 2+. Those will be proposed as separate RFCs when there is
something concrete to build.
