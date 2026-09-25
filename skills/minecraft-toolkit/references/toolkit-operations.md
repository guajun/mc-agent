# Toolkit operation reference

Details behind [SKILL.md](../SKILL.md): the operation catalog, the MCP and CLI
invocation forms, the event and context shapes, endpoint discovery, and cursors.

The connected mod's capability reply is the authority. The bridge filters this
catalog against it and reports anything else as unsupported, with an upstream
dependency when the supporting mod API has not shipped yet.

## Surfaces

| Surface | Start | Call |
| --- | --- | --- |
| MCP (preferred) | the harness spawns `mc-bridge mcp` (stdio) | one `mc_*` tool per operation |
| JSON CLI | `mc-bridge run` keeps the daemon alive | `mc-bridge call <operation> '<json>'` |
| Raw loopback API | `mc-bridge run` | newline-delimited JSON on `127.0.0.1:8765` (`MC_AGENT_API_HOST`, `MC_AGENT_API_PORT`) |

MCP tools follow the `mc_<operation>` convention (for example `mc_state`,
`mc_player`, `mc_context`). Use `mc_capabilities` / `mc-bridge call
capabilities` to confirm which ones this connection actually exposes before
relying on a name.

## Operation catalog

| Operation | MCP tool | CLI | Requires | What it does |
| --- | --- | --- | --- | --- |
| `status` | `mc_status` | `mc-bridge call status` | - | bridge health: connection, resolved port/vantage, buffered events |
| `capabilities` | `mc_capabilities` | `mc-bridge call capabilities` | - | the connected mod's capabilities and the filtered operation surface |
| `state` | `mc_state` | `mc-bridge call state` | `state` | world/server state and the online player list |
| `player` | `mc_player` | `mc-bridge call player '{"player":"<uuid>"}'` | player context | one player's server-known context and view target |
| `entities` | `mc_entities` | `mc-bridge call entities '{"radius":64}'` | `entities` | summarised entity list: counts by type plus the closest N |
| `command` | `mc_command` | `mc-bridge call command '{"command":"time set day"}'` | `command` | run a command, without the leading slash |
| `command_output` | `mc_command_output` | `mc-bridge call command_output '{"command":"time set day"}'` | `command` | run a command and collect the answer it produced |
| `events` | `mc_events` | `mc-bridge call events '{"since":0}'` | - | replay buffered events after a cursor |
| `context` | `mc_context` | `mc-bridge call context '{"id":"<context_id>"}'` | chat context | fetch a chat-time context bundle by `context_id` |
| `save` | `mc_save` | `mc-bridge call save` | `state` | world-save metadata reported by `state` (`levelName`, `worldDir`, whether that directory exists on this host) |
| `snapshot` | `mc_snapshot` | `mc-bridge call snapshot '{"name":"before"}'` | `snapshot` | write the entity set, in tick order, to the instance's disk |
| `snapshots` | `mc_snapshots` | `mc-bridge call snapshots` | `snapshot` | list snapshots already on the instance |
| `fork` | `mc_fork` | `mc-bridge call fork '{"name":"before"}'` | `snapshot`, `command` | freeze, save, snapshot entities, copy the world, resume |
| `restore` | `mc_restore` | `mc-bridge call restore '{"directory":"<dir>","dry_run":true}'` | `command` | summon a fork's entities back in the recorded order |
| `order` | `mc_order` | `mc-bridge call order '{"directory":"<dir>"}'` | `snapshot` | compare a fresh snapshot's order hash with a saved fork |
| `wait` | `mc_wait` | `mc-bridge call wait '{"ticks":20}'` | `wait` | block until the game advanced N ticks |
| `mark` | `mc_mark` | `mc-bridge call mark '{"text":"start"}'` | `mark` | annotate the event stream |
| `chat` | `mc_chat` | `mc-bridge call chat '{"message":"..."}'` | `chat` | send chat as the client-vantage player (client only) |
| `record_start` / `record_stop` | `mc_record_start` / `mc_record_stop` | `mc-bridge call record_start '{"ticks":200}'` | `record` | per-tick entity sampling (client only) |
| `screen` | `mc_screen` | `mc-bridge call screen` | `screen` | current client GUI screen (client only) |
| `connect` | `mc_connect` | `mc-bridge call connect '{"address":"host:port"}'` | `connect` | join a server (client only) |
| `world` | `mc_world` | `mc-bridge call world '{"level":"<name>"}'` | `world` | open a single-player save (client only) |
| `lan` | `mc_lan` | `mc-bridge call lan '{"mode":"offline"}'` | `lan` | publish the single-player world to the LAN (client only) |
| `stop` | - | `mc-bridge call stop` | - | shut the daemon down |

Client-only operations disappear on a server-vantage connection; the
capability reply, not this table, decides. `command_output` is one operation:
on the server vantage the answer comes back in the command's own ack, and on a
client vantage the toolkit falls back to collecting it from the event buffer.

## Typical sequences

Look around:

```bash
mc-bridge call capabilities
mc-bridge call state
mc-bridge call entities '{"radius": 32, "limit": 10, "types": "minecraft:zombie"}'
```

Handle a pushed chat event:

```jsonc
// event (from the forwarder, or mc-bridge watch --events chat)
{
  "eventId": "9f2c0a1b...:7",
  "sequence": 7,
  "streamId": "9f2c0a1b...",
  "event": "chat",
  "type": "chat",
  "category": "chat",
  "timestamp": 1730000000123,
  "tick": 4211,
  "sender": "Alice",
  "context_id": "ctx-42",
  "data": {
    "type": "chat",
    "seq": 7,
    "text": "what is in front of me?",
    "sender": "Alice",
    "context_id": "ctx-42",
    "context": {
      "schema": "player-context/1",
      "uuid": "1a2b3c4d-...",
      "name": "Alice",
      "tick": 4210,
      "dimension": "minecraft:overworld",
      "x": 103.5, "y": 95.0, "z": 52.5,
      "yaw": 180.0, "pitch": 0.0,
      "view": { "type": "block" }
    }
  }
}
```

```bash
# fetch what Alice saw when she spoke, by the opaque id
mc-bridge call context '{"id": "ctx-42"}'
# then act with the live, authoritative view if the task needs it
mc-bridge call player '{"player": "1a2b3c4d-..."}'
```

The toolkit returns a stable envelope around whatever the connected mod reports;
`found` is the field to branch on:

```jsonc
{
  "type": "context_bundle",
  "id": "ctx-42",
  "found": true,
  "status": "ok",                    // present when the mod reports one
  "context": {
    "schema": "player-context/1",
    "context_id": "ctx-42",
    "seq": 7,
    "capturedAt": 1730000000000,
    "tick": 4210,
    "uuid": "1a2b3c4d-...",
    "name": "Alice",
    "dimension": "minecraft:overworld",
    "x": 103.5, "y": 95.0, "z": 52.5,
    "yaw": 180.0, "pitch": 0.0,
    "view": { "...": "the ray result, in the player view's shape" }
  }
}
```

A miss is structured and never another player's bundle:

```jsonc
{ "type": "context_bundle", "id": "ctx-42", "found": false, "status": "expired", "context": null }
```

`player` wraps its answer the same way (`type: "player_context"`, `found`,
`uuid`, `name`, and the `player` object), so `found` is always the branch.

Read a command's answer:

```bash
mc-bridge call command_output '{"command": "data get entity Alice Pos"}'
# {"type":"command_output","command":"...","output":["..."],"source":"ack"}
```

On the server vantage the answer comes back in the command ack
(`source: "ack"`); on a client vantage the toolkit collects it from the event
buffer (`source: "events"`), which is why that call can take a few seconds.

## Events and cursors

`events` returns `{"events": [...], "next": <cursor>, "dropped": <bool>}`. Pass
`next` back as `since` to poll incrementally; `dropped: true` means the ring
buffer discarded events you never saw. Categories include `chat`, `game`,
`mark`, `sample`, `error`, `other`. `mc-bridge watch --events chat,game` streams
the same records as JSON lines for a human or a terminal-driven harness.

## Endpoint discovery

The Toolkit targets the server vantage:

- port file: `<gameDir>/mc-agent-server/port.txt`;
- `--server-dir <gameDir>` (or `MC_AGENT_SERVER_DIR`) when the game directory is
  not the working directory;
- `--port-file <path>` (or `MC_AGENT_PORT_FILE`) for the exact file;
- `--mod-port <port>` when the port is known and no file exists.

No client-port guessing happens. `--vantage client` (client port file
`<gameDir>/mc-agent/port.txt`, default 25580) exists only for an explicit legacy
setup. The MCP front-end talks to the daemon, not the game: `MC_AGENT_API_HOST`
and `MC_AGENT_API_PORT` (default `127.0.0.1:8765`).
