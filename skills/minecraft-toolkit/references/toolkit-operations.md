# Toolkit operation reference

Details behind [SKILL.md](../SKILL.md): the command catalog, parameter and
event shapes, error codes, target setup, cursors and the webhook contract.

The connected mod's capability reply is the authority. The daemon filters the
catalog against it and reports anything else as `capability_not_supported`,
with an upstream dependency when the supporting mod API has not shipped yet.

## Surfaces

| Surface | How to use it |
| --- | --- |
| CLI | `mc-agent <command>`; finite data commands return JSON; see output exceptions below |
| Daemon | `mc-agent daemon start` (detached) or `daemon run` (foreground/supervised) |
| Local IPC | token-protected loopback socket; the CLI uses it automatically |
| Remote transport | `target add ... --transport remote`; the daemon speaks TLS to the mod |
| Legacy transport | `target add ... --transport legacy` for the pre-0.8 loopback adapter |

There is no MCP server and no Python bridge in the product path. The optional
webhook is outbound-only; the daemon never calls a model and never manages a
harness session.

Finite data commands print one JSON value on stdout. Output exceptions:

- `version` defaults to human-readable text; `mc-agent --pretty version`
  returns JSON with `version`, `controlProtocol`, `modMinVersion`, and runtime
  metadata. Use this JSON entry point in scripts.
- `help` prints human-readable text on stderr and exits successfully.
- `events --follow` streams JSON values; consume successive values rather
  than parsing all stdout as one result.
- `daemon run` runs in the foreground without a final result.

Errors are JSON on stderr with a stable code and non-zero process exit code.
Check the exit code and parse the appropriate stream; an absent JSON result
must not silently count as success.
Usage failures may also print help text on stderr; invoking without a command
prints only help and exits 2. Do not assume all stderr is a single JSON value.

## Command catalog

Global options: `--home DIR`, `--target NAME` / `-t`, `--pretty`. Every command
accepts `--target` unless it manages targets or the daemon itself.

| Command | Requires | What it does |
| --- | --- | --- |
| `version` | - | product, control protocol and mod minimum |
| `doctor` | - | version, state dir, targets, daemon, TLS and capability checks |
| `status` | daemon | daemon and per-target connection state |
| `daemon run` | - | foreground daemon (accepts webhook flags) |
| `daemon start` / `stop` / `status` / `doctor` | - | detached daemon lifecycle |
| `target add <name> --transport remote --address H:P (--pin sha256:HEX \| --ca FILE) [--token-stdin \| --token-env VAR \| --token-file FILE] [--default]` | - | register a server; `--force` replaces |
| `target list` / `show NAME` / `remove NAME` / `use NAME` / `reload` | - | target management; credentials are never printed |
| `capabilities` / `caps` | daemon | supported and unsupported operations for the instance |
| `schema [op]` | daemon (full params) | operation contract and parameter schema |
| `call <op> [--params JSON] [--param key=value] [--request-id ID] [--timeout SECONDS]` | daemon | raw operation call; `--request-id` only exists here |
| `state` | `state` | world/server state, tick, level, world dir, online players |
| `player <name\|uuid>` | `player` | server-known context and live view target |
| `entities [--radius N]` | `entities` | summarised entity list |
| `context <id>` | `context` | chat-time context bundle by `context_id` |
| `command <line>` | `command` | write; returns `writeSeq` |
| `command-output <line> [--wait S]` | `command` | write plus collected output |
| `mark <text>` | `mark` | write; annotates the event stream |
| `wait <ticks>` | `wait` | block until the game advanced |
| `save` | `state` | world-save metadata |
| `snapshot [--name N] [--dimension D] [--radius R]` | `snapshot` | write an entity-order snapshot on the game host |
| `snapshots` | `snapshot` | list snapshots on the instance |
| `events [--stream-id ID] [--since N] [--limit N] [--category C] [--follow]` | daemon | replay/stream buffered events by (streamId, seq) |
| `requests` | daemon | unknown-write ledger |
| `request-status <id>` | daemon | resolved/unknown state of one write |
| `exclusive-acquire/renew/release/status <key> [--ttl S]` | daemon | cross-daemon leases |
| `chat` / `screen` / `connect` / `world` / `lan` / `record-start` / `record-stop` | legacy client vantage | client-only operations |

`fork`, `restore`, `order` and `stop`-style Python operations are not part of
the Go CLI. Remote targets refuse fork/restore: local file snapshots need the
daemon host to read the game's world directory, and a remote path is never
interpreted as a local one. Entity snapshots are not process-memory
checkpoints, and full freeze/re-attach guarantees are not claimed.

`command-output` returns output from the command ack (`source: "ack"`) or, when
needed, from the event buffer; use it when the reply matters.

## Errors and exit codes

Errors on stderr have a stable shape:

```json
{"ok":false,"error":{"code":"timeout","message":"...","retryable":true,"resultUnknown":true,"requestId":"..."}}
```

| Exit | Codes |
| --- | --- |
| 2 | `usage` |
| 3 | `target_required`, `target_unknown`, `not_found` |
| 4 | `connection_failed`, `connection_lost`, `daemon_not_running` |
| 5 | `unauthorized`, `forbidden` |
| 6 | `capability_not_supported`, `unsupported_transport` |
| 7 | `timeout`, `result_unknown` |
| 1 | anything else (`internal`, `bad_request`, `game_error`) |

A write with `resultUnknown: true` must not be replayed blindly; see the
unknown-write ledger below.

## Identity, context and events

`state` lists online players with stable UUIDs. `player` accepts a name but the
reply's UUID is what to carry forward:

```jsonc
{"type":"player_context","found":true,"uuid":"1a2b3c4d-...","name":"Alice",
 "player":{"dimension":"minecraft:overworld","x":103.5,"y":95.0,"z":52.5,
           "yaw":180.0,"pitch":0.0,
           "view":{"target":"block"}}}
```

`context <id>` fetches the bundle captured with a chat event:

```jsonc
{"type":"context_bundle","id":"ctx-42","found":true,
 "context":{"schema":"player-context/1","context_id":"ctx-42","seq":7,
            "capturedAt":1730000000000,"tick":4210,"timing":"receipt",
            "uuid":"1a2b3c4d-...","name":"Alice","dimension":"minecraft:overworld",
            "x":103.5,"y":95.0,"z":52.5,"view":{"target":"block"}}}
```

`timing` is `receipt` for a network chat packet and `broadcast` for a
server-side broadcast (including a Carpet fake player's `execute as <name> run
say ...`); a broadcast bundle is not packet-time history. A miss is structured
(`found:false`, `status:"not_found"|"expired"`) and never substitutes another
player.

`events` returns:

```jsonc
{"events":[ ... ], "next": 42, "dropped": false, "truncated": false,
 "streamId":"...", "reset": false}
```

The cursor is a **(streamId, seq)** pair; persist both. Call with
`--stream-id <id> --since <seq>`:

- `reset: true` (with the new `streamId`) means the cursor belongs to another
  daemon run (the local sequence resets on restart); start again from 0 on the
  new stream instead of trusting the old position;
- `truncated: true` means more events exist past `limit`; ask again with
  `next`. It is not loss;
- `dropped: true` means the ring buffer no longer reaches your cursor;
- a plain sequence jump with `--category`/`--target` filters is not loss
  (unrelated events consume sequence numbers);
- `--follow` subscribes first, pages the whole replay, then streams live with
  sequence de-duplication and emits `{"type":"stream","event":"gap"}` when a
  client-side overflow was recovered from the last delivered sequence;
- `game_restarted` (run id change) never replays across the restart;
- `event_gap` reports a gap the daemon noticed; treat it as loss, not silence.

No automatic replay of non-idempotent writes happens on reconnect. Each write
is assigned a stable end-to-end request id (`cli-<nonce>-<n>`, or an explicit
`mc-agent call --request-id <id>`) before it is sent, persisted in the
unknown-write ledger, and resolved with the server's `request_status` when the
link returns; if the server has no record the entry stays visible as
`unresolved`. A lost local reply produces `resultUnknown: true` with the same
`requestId` and a `hint` to resolve it with `request-status`. `requests` lists
the ledger; `request-status <id>` queries one entry.

## Target setup and discovery

```bash
# Remote server with the control transport on the game port
mc-agent target add dedicated --transport remote --address 203.0.113.10:25565 \
    --pin sha256:<fingerprint> --token-stdin --default
mc-agent daemon start
mc-agent doctor
```

- The mod writes the certificate fingerprint to
  `<gameDir>/mc-agent-server/control/fingerprint.txt`; the credential is minted
  on the server console with `/mcagent control token add ...`. Pass it on
  stdin, never on the command line that gets logged.
- `--ca FILE [--server-name NAME]` verifies with a CA/PEM instead of a pin.
  Verification is always on.
- `target list`/`show`/`doctor` report the credential source (`store`, `env`,
  `file`) but never the secret.
- `daemon start` picks a random loopback IPC port; the CLI reads the state
  file. `MC_AGENT_DAEMON_ADDR` + `MC_AGENT_IPC_TOKEN` cover containers and
  remote shells.

Legacy transport (pre-0.8 loopback JSON lines) uses
`--transport legacy [--server-dir DIR | --port-file FILE | --address H:P]` and
`--vantage client|server`; it exists for older mods and the explicit client
vantage, not as a fallback for a failed remote connection.

## Webhook contract

Optional and off by default. Start the daemon in the foreground (or under a
service manager) with webhook flags; `daemon start` does not accept them:

```bash
mc-agent daemon run --webhook-url https://receiver.example/hook \
    --webhook-secret "$SECRET" --webhook-events chat,game,mark,error
```

`MC_AGENT_WEBHOOK_URL`, `MC_AGENT_WEBHOOK_SECRET`, `MC_AGENT_WEBHOOK_EVENTS`,
`MC_AGENT_WEBHOOK_CONFIG` and `--webhook-config` are also accepted. Delivery
is best-effort, in-memory and bounded; queued events are lost on daemon
restart.

Every POST carries a stable event id, timestamp, event type and attempt number,
plus `X-MC-Agent-Signature: sha256=<hex(HMAC-SHA256(secret, "<timestamp>.<body>"))>`.
The receiver must verify the signature and timestamp and de-duplicate the
stable id across retries. Replies are not posted back into Minecraft; the
harness/route owns that.

Uptime: the game must run with the mod; user-driven use needs the daemon up,
unattended use needs the daemon, the receiver and the model harness all up.
