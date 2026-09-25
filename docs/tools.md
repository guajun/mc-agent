# Tools

Everything in this repository is a thin tool over a primitive. If a tool is
missing here, the answer is usually `mc-bridge call <method>` rather than a new
program.

## Repository layout

```
mc-agent/                  this repo: docs, tools, RFCs
mc-agent-interface-mod/    the Fabric mod (client + server vantage)
mc-agent-bridge/           the daemon, local API, MCP front-end
mc-agent-loop/             the agent loop and its backends
```

## mc-bridge

The daemon and its client.

| Command | Does |
| --- | --- |
| `mc-bridge run` | run the daemon: owns the game connection, serves the loopback API |
| `mc-bridge run --api-port 8766 --port-file labs/<lab>/mc-agent-server/port.txt` | attach to a lab instance instead of the default one |
| `mc-bridge call <method> [json]` | one call against a running daemon |
| `mc-bridge watch --events chat,game` | stream events as JSON lines |
| `mc-bridge mcp` | serve the tools over MCP (stdio) for an agent runtime |

Methods: `status`, `capabilities`, `state`, `entities`, `screen`, `command`,
`chat`, `mark`, `wait`, `record_start`, `record_stop`, `connect`, `world`, `lan`,
`snapshot`, `snapshots`, `fork`, `restore`, `order`, `events`, `stop`.

The loopback API is newline-delimited JSON, so anything that speaks a socket can
use it - see the [bridge README](https://github.com/guajun/mc-agent-bridge).

## MCP tools

What an agent runtime sees when the bridge is registered as an MCP server.

| Tool | Use it for |
| --- | --- |
| `mc_status`, `mc_capabilities` | is it connected, what can this instance do |
| `mc_state` | where the player is, health, dimension, tick |
| `mc_entities` | **summarised** entity list: counts by type plus the N closest |
| `mc_command` | send a command |
| `mc_command_output` | send a command **and read its answer** - use this when the answer matters |
| `mc_chat` | say something in chat |
| `mc_record_start` / `mc_record_stop` | per-tick entity sampling into `samples.jsonl` |
| `mc_wait` | block until the game advanced N ticks |
| `mc_mark` | annotate the event stream (start/end of an experiment) |
| `mc_screen`, `mc_connect`, `mc_world`, `mc_lan` | what the client is showing, join a server, open a save, publish a world to the LAN |
| `mc_snapshot`, `mc_snapshots`, `mc_fork`, `mc_restore`, `mc_order` | fork a live world and check a restore |
| `mc_events` | replay buffered events from a cursor |

`mc_entities` returns a summary on purpose: a real world answered with 256 KB of
JSON for a 64-block radius, which is not something a model can read usefully.
Ask for `types=` filtering and a larger `limit` when you want more.

!!! info "The server-vantage Toolkit and its Skill"
    The bridge is becoming a Harness-neutral server-vantage Toolkit: `player`
    and `context` join the surface for per-player and chat-time context, and
    `capabilities` reports exactly which operations the connected mod supports.
    Agents learn the workflow from the portable [Toolkit Skill](toolkit-skill.md);
    `mc-bridge call capabilities` (or `mc_capabilities`) is the authority for
    what a given connection can do. To run that skill unattended from game
    events, see [Unattended Hermes](hermes-unattended.md).

## mc-agent-loop

| Command | Does |
| --- | --- |
| `mc-agent-loop run --backend hermes --trigger @codex` | stay connected, answer chat |
| `mc-agent-loop once "<prompt>" --backend hermes` | one turn, no chat needed |
| `mc-agent-loop backends` | what is available: `hermes`, `echo` |

Useful flags: `--env-file .env` (keeps the API key out of the command line),
`--reply-mode command --reply-command 'execute as <name> run say {text}'` (gives
the agent a voice of its own, see [player identity](player-identity.md)),
`--trigger`, `--ignore-sender`, `--cooldown`, `--chunk-size`, `--history`.

## tools/

The programs that make the tracks usable. All of them are thin: they compose the
primitives above.

| Tool | Does |
| --- | --- |
| `smoke_offline.py` | run the whole stack against a fake mod, with or without a model |
| `launch_instance.py` | start a Minecraft instance directly, without a GUI launcher; `--world`, `--username`, `--jvm-property mcagent.autoConnect=host:port` |
| `lab_server.py` | provision, start, stop, `exec` against a headless Fabric lab under `labs/` (RCON console, pure stdlib) |
| `fork_verify.py` | `inspect` a recording, `restore` it in order, `check` that a lab reproduced it, `diff` two recordings entity by entity |
| `fake_player.py` | spawn, drive, and query a Carpet fake player - the agent's body without a second client |
| `game_cmd.py` | run a game command and print the feedback it produced |
| `mcp_probe.py` | call one MCP tool against a running bridge, exactly as an agent would |

Examples:

```bash
python tools/fake_player.py spawn deepseek 103 95 52
python tools/fake_player.py action deepseek jump
python tools/fake_player.py say "hello from the agent" --as deepseek
python tools/fake_player.py status deepseek

python tools/lab_server.py provision --name lab-01 --void --fabric-api --carpet
python tools/lab_server.py exec --name lab-01 "tick freeze"

python tools/fork_verify.py diff "<recording A>" "<recording B>"
```

## Protocols

| Protocol | Between | Reference |
| --- | --- | --- |
| interface protocol v1 | mod and bridge | [mod README](https://github.com/guajun/mc-agent-interface-mod) |
| loopback JSON-lines API | bridge and everything else | [bridge README](https://github.com/guajun/mc-agent-bridge) |
| snapshot / fork protocol | mod, bridge and lab tooling | [Forking a live world](protocol-snapshot.md) |
