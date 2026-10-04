# Architecture and terminology

[中文](https://guajun.github.io/mc-agent/zh/concepts/)

## The call chain

The supported architecture has one authoritative game path:

```text
Harness -> mc-agent CLI -> mc-agent daemon -> control transport -> server-vantage Fabric mod -> server
```

The daemon is one Go binary and can run in different deployment positions:

- **Client side / harness environment** (default): the daemon runs where the
  harness runs and connects to the server's game port. This is enough for a
  dedicated server or LAN world that only has the mod.
- **Server side** (optional): the same binary runs next to the game and can be
  addressed by local harnesses. It is not a gateway other daemons must pass
  through, and it is never required.
- **Multiple daemons** can connect independently with independent credentials
  and event streams; a human player leaving does not end a dedicated-server
  control connection.

The Toolkit is local infrastructure, not an agent runtime. It exposes facts and
primitives, but it does not choose a model, maintain a conversation, manage a
harness session or decide whether an action is allowed; it also does not manage
bodies, fake players or chunk loading.

## Two invocation modes

| Mode | What starts the turn | How context is obtained |
| --- | --- | --- |
| **User-driven** | a user asks Codex, Claude Code or another harness to do something | the harness calls `player`, `state`, `entities` or another operation on demand |
| **Unattended** | a separately configured receiver (for example a Hermes route) accepts a signed event webhook | the event's `context_id` retrieves the sender's bounded chat-time context; the harness can then make fresh calls |

An outside-game request has no captured chat moment. The harness resolves the
caller's stable player identity and asks for current context. An in-game chat
event can carry context captured when the server received the message, before
the agent starts working. The two mechanisms are complementary.

Player identity is context, not authorization. A name or UUID does not prove
that a caller may run privileged commands; permission comes from the control
credential, and the policy belongs to the harness and its operator.

## Glossary

| Term | Meaning |
| --- | --- |
| **Agent** | the reasoning process deciding what to do |
| **Harness** | the host application that runs the agent, supplies tools and owns its session; examples include Codex, Claude Code and Hermes |
| **Minecraft Agent Toolkit** | the Fabric mod plus the `mc-agent` Go CLI/daemon and the portable Skill |
| **CLI** | `mc-agent` command surface; finite data calls return JSON, with text and streaming exceptions documented in [Getting started](getting-started.md) |
| **daemon** | the long-running process that owns the game connection, replay buffer, unknown-write ledger and optional webhook |
| **control transport** | the authenticated TLS connection on the actual Minecraft game port (control protocol 1) |
| **legacy adapter** | the pre-0.8 plaintext loopback JSON-lines transport, kept for older mods, explicit client vantage and the single-player local path |
| **Fabric mod** | `mc-agent-interface-mod`, running in Minecraft and exposing server-known state |
| **server vantage** | the authoritative endpoint in a dedicated or integrated server |
| **client vantage** | the older opt-in endpoint tied to one client; retained for screen/client operations |
| **Skill** | portable instructions that teach a harness how to operate the Toolkit; it does not implement transport, retries or sessions |
| **agent-loop** | an optional legacy compatibility listener that depends on the Python bridge; not part of the product contract |
| **context bundle** | a bounded, short-lived snapshot of a chat sender's server-known identity, transform and view, retrieved by opaque `context_id` |
| **webhook** | optional outbound, signed event delivery to a receiver configured by the user |

## Ownership boundaries

| Concern | Owner |
| --- | --- |
| facts available only inside the game process; per-tick capture | Fabric mod |
| connection ownership, reconnect, event replay, unknown-write ledger, transport adapters | daemon |
| deciding which facts to fetch, interpreting them, choosing actions | agent in its harness |
| model provider, conversation state, approvals, webhook route and reply delivery | harness/operator |
| operating guidance shared between harnesses | portable Skill |
| bodies, fake players, chunk loading | Carpet Skill or the server's own Skill |

A new capability belongs in the mod if only the game process can know it. It
belongs in the daemon when it composes existing primitives or adapts transport.
It belongs in the agent when it requires judgement.

## Events, reconnects and context

The daemon buffers events and exposes cursored replay. A cursor is a
`(streamId, seq)` pair: `events --stream-id <id> --since <seq>` reports
`reset: true` when the cursor belongs to another daemon run, `dropped` for
buffer eviction, and `truncated` only when more pages exist (not loss).
Reconnects are reported instead of hidden: `bridge_connected`/
`bridge_disconnected`, `event_gap`, `game_restarted` (no replay crosses a
restart), and `request_resolved` when an unknown write becomes known.
Non-idempotent writes are never replayed automatically; each write carries a
stable end-to-end request id, the ledger persists it, and `request-status`
reports the outcome (`resultUnknown` errors include the id and a resolve hint).

The optional webhook sends selected events to one HTTP(S) receiver using
HMAC-SHA256 signatures. It is receiver-neutral, in-memory, best effort and
makes no model calls.

A server chat event can include a `context_id`. The mod keeps a bounded number
of bundles for a bounded time; unknown or expired IDs return structured results
and never substitute another player's data. `timing` distinguishes a network
chat packet (`receipt`) from a server-side broadcast such as a Carpet fake
player's say (`broadcast`); a broadcast bundle is not packet-time history.

## World forks and experiment boundaries

The first target is Minecraft **26.2**: conveniently move a world branch to the
harness machine so the agent can use local files, analysis programs, JDKs and
mods. Preserving runtime state serves the goal of sufficient experimental
conditions; it is not a promise to checkpoint any JVM or arbitrary mod state.
The current Go product exposes entity NBT/tick order and commands, not full
world `fork`/`restore`. Entity snapshots alone are not a world branch.

Protecting the source world remains the priority: capture must not require
unloading/reloading its chunks to manufacture a copy. For now, the effects of
`save-all` are an accepted boundary. Saving is not read-only and can perform
save maintenance, including processing pending chunk/entity work. `mc-agent
save` only reads save metadata; executing `save-all` requires a game command.
No atomic tick-boundary world capture is implemented by these existing calls.

For an experiment sensitive to autosave or pause/save behavior, choose an
earlier clean starting point that tolerates saving, fork there, and advance the
branch to the state under study. Check the required conditions and an unchanged
baseline before interpreting modified trials. This method can remove the need
to preserve a sensitive intermediate state, but its validity depends on the
experiment; it does not make all unsupported states reproducible. Describe the
supported state and known gaps rather than claiming an exact continuation.

## Honest boundaries

- **Remote paths are remote.** A server's `worldDir` is never interpreted as a
  path on the daemon host; `snapshot` writes on the game host.
- **Snapshots are entity-order records, not memory checkpoints.** Full freeze,
  fork and re-attach guarantees are a separate design discussion and are not
  claimed here.
- **Proxy compatibility.** Byte-transparent TCP relaying is the boundary;
  Minecraft-aware or TLS-terminating proxies are untested and not claimed.
- **The game must run.** When the game stops, the mod interface stops; the
  daemon waits and reconnects. Starting a stopped remote game process is out
  of scope.

## Historical paths

The client-vantage endpoint and `mc-agent-loop` still support old workflows and
offline tests, and the Python bridge remains the explicit legacy path for local
`fork`/`restore` tooling. Use them only when a legacy capability is actually
required. [RFC 0001](rfc/0001-agent-interface.md) records the earlier
client-first, MCP-era architecture and is kept as history; it does not describe
the current product.
