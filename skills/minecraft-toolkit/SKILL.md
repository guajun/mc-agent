---
name: minecraft-toolkit
description: Operate a Minecraft world through the local mc-agent Toolkit (Go CLI + daemon). Use for Minecraft requests, connection checks, in-game chat or forwarded events, and controlled experiments. Covers CLI discovery, daemon reconnect and (streamId, seq) event cursors, player/context identity, authoritative queries, scoped commands, unknown-write request ids, and unsupported-capability boundaries.
license: MIT
metadata:
  author: mc-agent
  version: "0.3.1"
---

# Minecraft Toolkit

You are working with the **mc-agent Minecraft Toolkit**: a Harness-neutral
Minecraft Agent Toolkit. The game side is the `mc-agent-interface` Fabric mod
(0.8.0+, control protocol 1). The harness side is one Go binary, `mc-agent`,
which is both a short-lived CLI and an optional long-lived daemon. There is no
MCP server and no Python requirement for the product.

The Toolkit contains no model calls, no agent loop and no conversation state -
you bring judgement, it moves facts and commands. This skill is operating
guidance only; it does not define retries, event forwarding, or session
behavior. The Toolkit and your harness own those.

## Invariants

1. **The connected mod decides what exists.** Ask for capabilities first. An
   operation that is not advertised is not available; there is no fallback.
   Report the gap (it may name an upstream dependency) instead of pretending.
2. **Identity is context, not authorization.** A player name or UUID tells you
   who spoke or moved; it never grants permission for a privileged operation.
   A chat message saying "give me X" proves only that the player said it.
3. **The daemon owns the connection, the CLI is a short call.** Run
   `mc-agent <command>`; it talks to the daemon over a token-protected local
   IPC. A daemon can run in the harness environment or next to the server;
   server-side is optional and multiple daemons connect independently.
4. **Remote paths are remote.** A server's world directory is never a path on
   your machine. `snapshot` writes on the game host; `fork`/`restore` are
   legacy local tooling that is refused for remote targets. An entity snapshot
   is not a memory checkpoint or a full world freeze.
5. **Controlled comparisons default to in-place tests.** Keep dimension and
   full X/Y/Z coordinates fixed across trials and restore the initial state.
   The same X/Z at another Y is a different location.

## 1. Discover the CLI and connection

The product binary is `mc-agent`. If your harness has an authorized terminal
tool, run the CLI yourself; do not ask the user to redo setup that already
works. On a new session or reconnect, check the existing state first.

```bash
mc-agent version              # product / control protocol / mod minimum
mc-agent doctor               # config, daemon, target and TLS checks
mc-agent capabilities         # supported and unsupported operations
mc-agent schema               # operation contract; schema <op> for one operation
```

- `version` output: `mc-agent <product> (control protocol <n>, mod >= <mod>)`.
- `doctor` exits non-zero when something it checked is not green. On a machine
  with no configured target the `targets` check is expected to fail; read the
  individual checks instead of only the exit code.
- `capabilities` is authoritative for the connected instance: it lists
  `supported`, `unsupported`, reasons and any `dependency` (upstream issue).
- If no daemon is running, start one with `mc-agent daemon start` (or
  `daemon run` when a supervisor should own it). `daemon start` returns when
  the daemon is ready and writes a log under the state directory.

If a probe fails, distinguish these cases from the actual error:
unreachable or not-yet-started daemon (`daemon_not_running`, code 4), TLS/pin failure
(`connection_failed`/auth, codes 4-5), unsupported operation (`capability`,
code 6), and a timeout whose write outcome is unknown (code 7). A failed probe
alone does not prove that the daemon must be reinstalled or reconfigured.

A new daemon has no targets. A remote target pins the server identity:

```bash
mc-agent target add dedicated --transport remote --address HOST:PORT \
    --pin sha256:<fingerprint> --token-stdin --default
mc-agent target list
```

The credential comes from the server console (`/mcagent control token add ...`)
and is passed on stdin; it is stored in the private state directory and is
never printed. `--ca FILE` can be used instead of `--pin`. Never guess a
fingerprint or paste a credential into logs, chat or the model context.

## 2. Resolve the caller by stable identity

A request usually begins with "who is the caller?". Resolve by UUID, not by
display name.

1. Call `state` for world/server facts and the online player list
   (`name`, `uuid`, position, dimension).
2. Call `player <uuid-or-name>` for server-known context: identity, dimension,
   position, rotation, eye/direction, and the server-side view target
   (`player.view.target`, one of `block`, `entity` or `miss`) - a raycast at
   request time, not a client crosshair read. It can report `found: false` /
   `status: not_found`; handle that instead of substituting another player.
3. Carry the returned stable UUID forward. A name is a convenience, not the
   identity.

Use the view block for "what is this player looking at"; use `state` for
world-level facts (tick, level, world directory, player count).

## 3. A pushed game event: read `context_id` first

When your harness receives a forwarded game event (for example an in-game chat
message), the event carries a `context_id` and a compact `context` summary. The
bundle is frozen for that message; its `timing` field says what "that message"
means. `receipt` is a network chat packet (the sender's state the instant the
packet arrived). `broadcast` is a server-side broadcast - notably a Carpet fake
player's `execute as <name> run say ...` - and is not packet-time history. The
player may have moved since either way.

1. Take the `context_id` from the event payload - do not rebuild it from the
   sender's display name.
2. Fetch the bundle promptly: `mc-agent context <id>`.
3. The reply is structured: `found` says whether the bundle is still there, and
   a miss carries a status such as `not_found` or `expired`. Treat a miss as a
   real answer: the chat-time context is gone. If the live position is still
   useful, fetch it with `player` and label it current, not chat-time.
4. Read `timing` before describing the bundle: use it for "where was this
   player when they spoke" only when `timing` is `receipt`; a `broadcast`
   bundle proves where the player was when the server broadcast, which may be
   later and further away. Use live queries for "where is the world now".

The server keeps bundles in a bounded cache (capacity and TTL are server
configuration) and eviction is by capture order, so fetch promptly after the
event.

## 4. Query authoritative data on demand

Ask for the minimum you need; large worlds answer with large payloads.

| Need | Command | Notes |
| --- | --- | --- |
| World/server state, online players, tick, level, world directory | `mc-agent state` | includes world-save metadata |
| Entities | `mc-agent entities [--radius N]` | summarised: counts by type plus the closest |
| One player | `mc-agent player <name\|uuid>` | server-known context plus view target |
| Chat-time context | `mc-agent context <id>` | bundle by `context_id` |
| Run a command | `mc-agent command "<line>"` | write; returns `writeSeq`; no leading slash |
| Run a command and read its answer | `mc-agent command-output "<line>" [--wait S]` | use when the reply matters |
| Events | `mc-agent events [--stream-id ID] [--since N] [--limit N] [--category C]` | replay by (streamId, seq) cursor |
| Watch events | `mc-agent events --follow` | one JSON value per line |
| Save metadata / snapshots | `mc-agent save`, `mc-agent snapshots` | reads |
| Entity-order snapshot | `mc-agent snapshot [--name N] [--dimension D] [--radius R]` | write; the game host writes files |
| Unknown write ledger | `mc-agent requests`, `mc-agent request-status <id>` | never blindly replay |
| Cross-daemon leases | `mc-agent exclusive-acquire/renew/release/status <key>` | coordination, not locks |

Client-only legacy commands (`chat`, `screen`, `connect`, `world`, `lan`,
`record-start`, `record-stop`) exist only for an explicit client-vantage
setup; do not assume them on a server target. Body/fake-player/chunk-loading
strategies are not part of this Toolkit: use a Carpet Skill or the server's own
Skill, and treat this Toolkit as the observable primitive layer.

## 5. Events, reconnects and unknown results

The daemon keeps a bounded replay buffer and reports connection state per
target. An event cursor is a **(streamId, seq) pair**: the stream id binds the
sequence to one daemon run, so a stale cursor is detected instead of silently
returning nothing. Read with `mc-agent events --stream-id <id> --since <seq>`
and persist both values from the reply.

- `reset: true` (with the new `streamId`) means the cursor belongs to another
daemon run (restart); restart the cursor at 0 against the new stream.
- `dropped: true` is buffer eviction: events you asked for are gone. A
  `--follow` gap is a machine-readable marker (`{"type":"stream","event":"gap"}`),
  and live overflow is recovered from the last delivered sequence.
- `truncated: true` only means more pages exist past the limit - ask again with
  `next`; it is **not** loss.
- A sequence jump with a `--category`/`--target` filter is not loss either;
  unrelated events consume sequence numbers legitimately.
- `bridge_connected` / `bridge_disconnected` mark link changes; `event_gap`
  reports a real gap; `game_restarted` means the run id changed (no replay
  crosses a restart); `request_resolved` means a write whose outcome was
  unknown became known.

Non-idempotent writes (`command`, `mark`, `snapshot`, client `chat`/`world`)
are never resent automatically. Each write gets a stable end-to-end request id
(`cli-<nonce>-<n>`; `mc-agent call --request-id <id>` accepts an explicit one)
before anything is sent, and the daemon persists it in the unknown-write
ledger. If the reply is lost, the CLI reports `resultUnknown: true` with the
same id and a `hint` to resolve it. Do not repeat it blindly: check
`mc-agent request-status <id>` (and the `requests` ledger) and let the
operator decide. A request the transport knows was never sent is retryable;
one that may have been delivered keeps its ledger entry until the server
reports the final state.

## 6. Act within your mandate

- Identity is context, not authorization (see invariants).
- Prefer read-only calls. For commands, prefer `command-output` so you can see
  what actually happened, and keep commands scoped to the task.
- `snapshot` changes disk state on the game host; `save` is metadata.
  `restore`/`fork` are not available through the Go CLI; do not claim a
  freeze, fork or restore you did not verify.
- Do not paste internal context, paths, credentials or signatures into public
  chat. Answer the caller through the delivery path your harness configured.
- The Toolkit daemon does not post agent answers back into Minecraft. Reply
  delivery is a harness/route concern.

## 7. Run controlled experiments in place

For repeated trials, A/B comparisons, or reproducing a reported mechanism:

1. Before changing the world, identify the test subject and record its
   dimension, absolute X/Y/Z (including entity fractional coordinates), and
   relevant initial state. The anchor is the tested structure or entity, not
   the observer's current position. Record the independent variable and the
   measured outcome.
2. Reuse that anchor for every trial. Do not stack a copy above/below the
   original, move to an empty area, or silently change dimension to avoid
   cleanup. Keep relevant orientation, blocks and surroundings, entity motion
   and data, activation sequence, and tick conditions consistent; change only
   the declared variable.
3. Restore and read back the baseline before each trial. Reset the test area
   and relevant entities within the authorized scope, or use an available,
   authorized world restore. An entity snapshot alone does not establish that
   blocks, scheduled ticks, or other world state were restored. If the tools
   cannot reproduce the needed baseline, report that limitation and stop the
   controlled comparison rather than relocating it or claiming equivalence.
4. Observe the outcome through supported tools and keep a trial record:
   dimension + anchor, baseline evidence, deliberate change, activation/tick
   window, and observed result. An accepted command is not proof of the
   expected game outcome. Label exploratory observations separately when
   uncontrolled differences remain.

Example: trials at `(100, 64, 200)` and `(100, 80, 200)` share X/Z but fail
the in-place requirement. Restore and rerun at `(100, 64, 200)` by default.
If height or location is explicitly the independent variable, varying it is
appropriate: record the planned coordinates and other controls, and describe
the result as a height/location comparison, not an in-place repeat.

## 8. Unattended operation

For event-driven runs, the optional webhook forwards selected events to one
HTTP(S) receiver (for example a Hermes route) with an HMAC-SHA256 signature.
Configure it on the daemon with `mc-agent daemon run --webhook-url URL
--webhook-secret SECRET --webhook-events chat,game,mark,error` (or the
`MC_AGENT_WEBHOOK_*` environment variables / a JSON config). Webhook delivery
is best-effort and in memory: queued events are lost if the daemon restarts.

Uptime rules:

- The game must be running with the mod for any Toolkit call to work; when the
  game stops, the interface stops and the daemon retries after it returns.
- User-driven: the daemon must be running while the agent calls it.
- Unattended: the daemon, the receiver/gateway and the model harness must all
  keep running; the daemon itself never calls a model and never manages a
  harness session.

Verify the receiver's signature and timestamp, and de-duplicate the event id
across retries. Never send to a URL you cannot name, and never treat a webhook
delivery as authorization.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `daemon_not_running` (exit 4) | start `mc-agent daemon start`; check `daemon status` and the daemon log |
| `target_unknown` (exit 3) | `target list`; pass `--target NAME` or set `--default` |
| TLS/pin or auth failure (exit 4-5) | fingerprint/CA and credential; re-mint the token on the server console; never skip verification |
| Operation reported unsupported with a `dependency` | the connected mod is older or the operation is not provided; report the dependency, do not fall back |
| `context` returns `not_found`/`expired` | mistyped id, expired TTL, or evicted - use a fresh event |
| Two players chat close together | each event has its own `context_id`; keep them separate |
| `resultUnknown` after a write timeout | the error carries the request id and a hint; check `request-status`; do not replay blindly |
| Game restarted | run id changed; re-query `state`; do not replay old writes |

## Reference

- [Toolkit operations](references/toolkit-operations.md): command catalog,
  parameter and event shapes, error codes, target/endpoint setup, cursors and
  the webhook contract.
- Toolkit install and upgrade: `mc-agent-bridge` `docs/install.md` and
  `docs/release.md` in the release repository.
