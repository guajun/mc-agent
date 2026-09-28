# Toolkit tools and commands

[中文](https://guajun.github.io/mc-agent/zh/tools/)

The Minecraft Agent Toolkit is implemented by the `mc-agent` Go binary. Its live
**capabilities** result is authoritative: the command surface is filtered to
match the connected mod and vantage. There is no MCP server.

## CLI

Global options: `--home DIR`, `--target NAME`, `--pretty`.

| Command | Purpose |
| --- | --- |
| `mc-agent version` | product, control protocol and mod minimum |
| `mc-agent doctor` | version, state dir, targets, daemon, TLS and capability checks |
| `mc-agent daemon start` / `run` / `stop` / `status` / `doctor` | daemon lifecycle |
| `mc-agent target add/list/show/remove/use/reload` | target and credential management |
| `mc-agent capabilities`, `mc-agent schema [op]`, `mc-agent call <op>` | discover and call operations |
| `mc-agent status` | daemon and per-target connection state |
| `mc-agent state` | authoritative world/tick state and online players |
| `mc-agent player <name\|uuid>` | resolve a player by UUID (or name) and return live context/view |
| `mc-agent context <id>` | fetch a bounded chat-time bundle by `context_id` |
| `mc-agent entities [--radius N]` | nearby entities, summarised |
| `mc-agent command "<line>"` | issue a command (write; returns `writeSeq`) |
| `mc-agent command-output "<line>" [--wait S]` | issue a command and read its answer |
| `mc-agent events [--since N] [--limit N] [--category C] [--follow]` | replay or stream buffered events |
| `mc-agent requests`, `mc-agent request-status <id>` | unknown-write ledger and lookup |
| `mc-agent save`, `mc-agent snapshots` | world-save metadata and snapshot list |
| `mc-agent snapshot [--name N] [--dimension D] [--radius R]` | entity-order snapshot written on the game host |
| `mc-agent mark <text>`, `mc-agent wait <ticks>` | annotate the stream; wait for game ticks |
| `mc-agent exclusive-* <key>` | cross-daemon leases |

Client-only legacy commands (`chat`, `screen`, `connect`, `world`, `lan`,
`record-start`, `record-stop`) appear only for an explicit client vantage and
only when the mod advertises them.

An agent should:

1. run `version` and `doctor` to understand the environment;
2. run `capabilities` / `schema` and call only supported operations;
3. resolve the relevant player by UUID when the request needs player context;
4. fetch only the live world data it needs, and `context` promptly when an
   event supplied a `context_id`;
5. treat identity as context, not command authorization;
6. check `request-status` instead of replaying an unknown write.

Exit codes and error codes are stable; see the
[operation reference](https://github.com/guajun/mc-agent/blob/main/skills/minecraft-toolkit/references/toolkit-operations.md).

## Boundaries

- **Remote paths are remote.** A server's world directory is never a path on
  the daemon host. `snapshot` writes on the game host.
- **Snapshots are entity-order records, not memory checkpoints.** Full freeze,
  fork and re-attach guarantees are not claimed by this CLI. `fork`/`restore`
  are legacy local Python tooling and are refused for remote targets.
- **Bodies, fake players and chunk loading are external.** Use a Carpet Skill
  or the server's own Skill; the Toolkit exposes observable primitives.
- **An unavailable operation is reported, never faked.** The capability reply
  carries `unsupported` entries, reasons and upstream dependencies when known.

## Event forwarding

The daemon can forward selected events to one webhook receiver. Delivery is
signed with HMAC-SHA256, retried with a stable event id, and never posts a
model reply back into Minecraft. Configure it on `daemon run` (not
`daemon start`) or with `MC_AGENT_WEBHOOK_*`; see
[Hermes and unattended operation](hermes-setup.md).

## Repository utilities

These tools support experiments and documentation; they are development
utilities, not product runtime requirements.

| Tool | Purpose |
| --- | --- |
| `lab_server.py` | provision and control a headless Fabric lab |
| `launch_instance.py` | launch a client without a GUI launcher |
| `game_cmd.py` | run a game command and print feedback |
| `fake_player.py` | create and drive a Carpet fake player |
| `fork_verify.py` | inspect, restore and compare recorded forks (legacy local path) |
| `webhook_receiver.py` | local HMAC-verifying receiver for webhook testing |
| `smoke_offline.py` | developer smoke test of the legacy Python daemon/agent-loop path |

## Legacy agent-loop

`mc-agent-loop` is not part of the Toolkit contract. It remains an optional
compatibility listener with `echo` and temporary `hermes` backends and depends
on the legacy Python bridge. Codex, Claude Code and other user-driven harnesses
run the CLI directly and must not be described as loop backends. Its default
chat trigger is `@agent`.
