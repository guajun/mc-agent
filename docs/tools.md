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
| `mc-agent capabilities`, `mc-agent schema [op]`, `mc-agent call <op> [--request-id ID]` | discover and call operations; `--request-id` exists only on `call` |
| `mc-agent status` | daemon and per-target connection state |
| `mc-agent state` | authoritative world/tick state and online players |
| `mc-agent player <name\|uuid>` | resolve a player by UUID (or name) and return live context/view |
| `mc-agent context <id>` | fetch a bounded chat-time bundle by `context_id` |
| `mc-agent entities [--dimension D]` | server non-player NBT and tick order (requires `entities:nbt`) |
| `mc-agent command "<line>"` | issue a command (write; returns `writeSeq`) |
| `mc-agent command-output "<line>" [--wait S]` | issue a command and read its answer |
| `mc-agent events [--stream-id ID] [--since N] [--limit N] [--category C] [--follow]` | replay or stream buffered events by (streamId, seq) cursor |
| `mc-agent requests`, `mc-agent request-status <id>` | unknown-write ledger and lookup |
| `mc-agent save`, `mc-agent snapshots` | world-save metadata and snapshot list |
| `mc-agent snapshot [--name N] [--dimension D]` | entity-order snapshot written on the game host |
| `mc-agent mark <text>`, `mc-agent wait <ticks>` | annotate the stream; wait for tick callbacks on the connected vantage |
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

## Clocks and state readback

For a server vantage, `state.tick` and entity capture `tick` are the server tick
counter, not a level's gametime. `wait N` counts server tick callbacks. Under
`tick freeze`, those callbacks and the server counter continue while level
gametime stays frozen; a successful `wait` does not prove the world advanced.
Legacy client ticks are another clock, not a substitute for server gametime.
The `--wait S` option on `command-output` is an output collection timeout in
seconds, not either tick clock.

For a permitted, frozen-world measurement, a limited reference sequence is:

1. Set the intended state, then read back the target UUID, relevant NBT and
   position/size in the chosen dimension. A command acknowledgement or collected
   feedback alone does not prove game execution succeeded. Check CLI exit codes,
   errors and unknown writes; stop if the required state cannot be verified.
2. Read `time query gametime` in that dimension and record it as `t0`, together
   with the actual initial state. Use `execute in minecraft:overworld run time
   query gametime` when that is the selected dimension. Retain the raw reply and
   fail if it cannot be interpreted; feedback is not a structured gametime API.
3. Request `tick step 1`, poll the same gametime until `t0 + 1` with a bounded
   timeout, then capture `entities --dimension minecraft:overworld`. Select the
   target by UUID, not the first array entry. Record that the sample is after
   the step and retains separate gametime and server-tick fields. These calls
   are separate observations, not an atomic capture; reject an unexpected
   gametime advance or an unverified freeze assumption.

Repeat only as needed for the task. Cleanup can create delayed descendants;
where relevant, allow them to settle and verify the intended population again.
This is a usage example, not a required experiment template. State injection
does not establish that a real player interaction such as shearing succeeded.

In the historical [#45](https://github.com/guajun/mc-agent/issues/45) data, the
runner's `series[].tick` means gametime, unlike the Toolkit `tick` field.
Recomputing all 77 trials / 2,010 samples gives no `gametime - t0 != step`
mismatches. Match `meta.cubeUUID` across the complete series: the
`order_noai_d0_cube_first_r1` target first has a body at step 2 (4430 - 4428).
In `misc_dispenser_body`, `pre_body: true` means step 1 is continued possession,
not a new load. First observed presence without a verified absent baseline is
not a proven transition. Missing targets/fields, script failure, and no event
observed within a complete sampling window are distinct results; none alone
proves that an event can never happen. This is offline historical verification,
not a current-version game acceptance result.

The preserved `summary.json` also disagrees with UUID-matched series in seven
trials: `misc_multi3_r1/r2/r3` and `misc_prime` show a null summary despite
presence at step 1; three early `summon_d0_cube_first`, `summon_d0_item_first`,
and `summon_d10_cube_first` summaries report presence without matching the
selected UUID in the series. Retain the historical files and report these
differences rather than replacing the source data or treating summaries as
authoritative observations.

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

### Launching a client with its own data directory

`launch_instance.py` reads the installed version, libraries and assets from
`--minecraft-dir`. Use `--game-dir` to keep the experiment's client data,
default launch log and mc-agent server state in a separate directory:

```powershell
python tools/launch_instance.py --minecraft-dir "D:/MC/.minecraft" `
  --version 26.2-Fabric --game-dir "F:/research/client-game" `
  --jvm-property mcagent.control=true --world "research world"
```

Provision the experiment's mods, configuration and saves in that game directory
before launching. The tool does not copy the user's instance or world.
It resolves `--game-dir` against the caller's cwd before starting Java and adds
`-Dmcagent.serverDir=<absolute gameDir>/mc-agent-server` unless a serverDir
property is already present in `--jvm-property`, `--jvm-arg` or the version's JVM
arguments. Raw `--game-arg=--gameDir=...` (or the two-argument form) is also
normalized this way; conflicting game directories are rejected.

Before launch, the tool prints the process cwd, client game directory, server
directory and control directory. Check those paths; after launch, verify the
control files and snapshots appear under the printed server directory. Existing
files are preserved. Java's cwd remains the installed version directory, so an
explicit relative serverDir is relative to that cwd. Without a game directory
override, the original defaults remain: client data and cwd use the version
directory, and server state uses its `mc-agent-server` subdirectory. Dedicated
servers retain the Mod's default `mc-agent-server`, relative to their process
cwd. These settings select file destinations; they do not sandbox Minecraft or
other mods.

## Legacy agent-loop

`mc-agent-loop` is not part of the Toolkit contract. It remains an optional
compatibility listener with `echo` and temporary `hermes` backends and depends
on the legacy Python bridge. Codex, Claude Code and other user-driven harnesses
run the CLI directly and must not be described as loop backends. Its default
chat trigger is `@agent`.

## Current entity NBT contract (Mod 0.9.0)

`entities` requires `entities:nbt`; `snapshot` requires `snapshot:entity-nbt`.
They are server-only. Old Mods and unknown capability lists are refused for
these operations. Optional `dimension` defaults to `minecraft:overworld`;
there is no implicit player selection. Every `radius` parameter/flag is rejected.

The inline result has `type: entities`, `schema: entity-nbt/1`, `instance`,
`tick`, `dimension`, `playersSkipped`, `orderHash` and `entities` (array).
Each array record and snapshot JSONL line has `order`, `uuid`, `type`, `pos`,
`vel`, `nbt` (SNBT), `passengers` (UUIDs), `vehicle` (UUID or null), and
`restorable`. Players and removed/dead entries are excluded; this is the
surviving entity tick list, not every entity stored in or loaded by the world.
`entityId`, `yaw`, `pitch` projections are removed. `vel` remains because
existing fidelity comparisons need it; the full persistent state is in NBT.

Restore roots (`restorable: true`) sequentially using their type, position and
NBT; passengers come back nested with their vehicle. The order hash covers all
record UUIDs in order, including passengers, and alone does not prove fidelity.
`snapshot` writes the identical entity records and a metadata file on the game
host, then returns an acknowledgement including schema, id, dir, count, hash,
tick, dimension, byte count and replacement status. It is not a world-file copy.

Inline NBT remains subject to the negotiated frame limit (8 MiB default), with
no pagination. Large results can fail. These captures do not unload chunks or
initiate a world save; they alone do not promise a complete non-invasive fork.
Use ordinary game commands or local analysis for filtering. Legacy client
entity recording remains a separate synchronized projection.
