# Architecture and terminology

[中文](https://guajun.github.io/mc-agent/zh/concepts/)

## The call chain

The supported architecture has one authoritative game path:

```text
Harness -> Minecraft Agent Toolkit daemon -> server-vantage Fabric mod -> server
```

The Toolkit and Harness are co-located with the mod endpoint. On a dedicated
server that means the server machine. In single player it means the player's
computer, where the integrated server runs inside the client process.

The Toolkit is local infrastructure, not an Agent runtime. It exposes facts and
primitives, but it does not choose a model, maintain a conversation or decide
whether an action is allowed.

## Two invocation modes

| Mode | What starts the turn | How context is obtained |
| --- | --- | --- |
| **User-driven** | a user asks Codex, Claude Code or another Harness to do something | the Harness calls `player`, `state`, `entities` or another Toolkit operation on demand |
| **Unattended** | a separately configured receiver such as Hermes accepts a signed event webhook | the event's `context_id` retrieves the sender's bounded chat-time context; the Harness can then make fresh Toolkit calls |

An outside-game request has no captured chat moment. The Harness resolves the
caller's stable player identity and asks for current context. An in-game chat
event can carry context captured when the server received the message, before
the Agent starts working. The two mechanisms are complementary.

Player identity is context, not authorization. A name or UUID does not prove
that a caller may run privileged commands; that policy belongs to the Harness
and its operator.

## Glossary

| Term | Meaning |
| --- | --- |
| **Agent** | the reasoning process deciding what to do |
| **Harness** | the host application that runs the Agent, supplies tools and owns its session; examples include Codex, Claude Code and Hermes |
| **Minecraft Agent Toolkit** | the `mc-agent-bridge` package and its complete local Minecraft capability surface |
| **Toolkit daemon** | the long-running process started by `mc-bridge run`; “Bridge daemon” is its historical name, not another layer |
| **Fabric mod** | `mc-agent-interface-mod`, running in Minecraft and exposing server-known state over loopback |
| **server vantage** | the authoritative endpoint in a dedicated or integrated server; the Toolkit default |
| **client vantage** | the older opt-in endpoint tied to one client; retained for screen/client operations and compatibility |
| **Skill** | portable instructions that teach a Harness how to operate the Toolkit; it does not implement transport, retries or sessions |
| **agent-loop** | an optional compatibility listener, not part of the Toolkit contract and not required by user-driven Harnesses |
| **context bundle** | a bounded, short-lived snapshot of a chat sender's server-known identity, transform and view, retrieved by opaque `context_id` |
| **webhook** | optional outbound, signed event delivery to a receiver configured by the user |

The former Bridge project has become the Toolkit; the repository and CLI retain
the `mc-agent-bridge` / `mc-bridge` names for compatibility. A Bridge daemon is
therefore the Toolkit daemon, not a component behind the Toolkit. Codex, Claude
Code and Hermes are Harnesses, not Toolkit backends.

## Ownership boundaries

| Concern | Owner |
| --- | --- |
| facts available only inside the game process; per-tick capture | Fabric mod |
| connection ownership, event replay, primitive composition and transport adapters | Toolkit daemon |
| deciding which facts to fetch, interpreting them and choosing actions | Agent in its Harness |
| model provider, conversation state, approvals, webhook route and delivery target | Harness/operator |
| operating guidance shared between Harnesses | portable Skill |

A new capability belongs in the mod if only the game process can know it. It
belongs in the Toolkit when it composes existing primitives or adapts transport. It
belongs in the Agent when it requires judgement.

## Events and context

The Toolkit daemon buffers events and exposes cursored replay. Its optional
`forward` command sends selected events to one HTTP(S) receiver using
HMAC-SHA256 signatures. It is receiver-neutral and makes no model calls.

A server chat event can include a `context_id`. The mod keeps at most a bounded
number of bundles for a bounded time; unknown or expired IDs return structured
results and never substitute another player's data. Agents should fetch the
bundle early, then query fresh world state as needed.

The webhook sender and portable [Toolkit Skill](toolkit-skill.md) are shipped.
The [Hermes unattended guide](hermes-unattended.md) documents route setup,
restricted tools and reply delivery. Signed end-to-end delivery remains blocked
by the header and delivery-ID interoperability follow-up in
[mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7).

## Historical paths

The client-vantage endpoint and `mc-agent-loop` still support old workflows and
offline tests. Use them only when a client-only capability such as screen state
is actually required. They are not the architectural default, and no Harness
name is a default game-chat trigger. The compatibility loop currently defaults
to `@agent`.

[RFC 0001](rfc/0001-agent-interface.md) records the earlier client-first
architecture. [Plan #8](https://github.com/guajun/mc-agent/issues/8) supersedes
that deployment model with the server-vantage Toolkit described here.
