# Troubleshooting

Most of these were found the hard way, on a real world, while building the
thing.

## Connecting

### `doctor` reports `daemon_not_running` (exit 4)

```bash
mc-agent daemon start
mc-agent daemon status
mc-agent doctor
```

The daemon writes a log under its state directory (`daemon.log`). If
`daemon status` shows `staleState`, the state file points at a dead process:
remove it or just start a new daemon.

### `connection_failed`, TLS or pin errors

1. Does `<gameDir>/mc-agent-server/control/fingerprint.txt` exist? If not,
   start the game/server with `-Dmcagent.control=true`.
2. Is the address the **actual** game port (for a LAN world, the published
   port)? `mc-agent target show <name>` prints the configured address.
3. Re-read the fingerprint and re-run `target add --force` when it changed.
   Certificate verification cannot be disabled; that is deliberate.
4. For a credential error, mint a fresh token on the server console and store
   it with `--token-stdin`/`--token-env`/`--token-file`.

### `target_unknown` or the wrong world is addressed

`mc-agent target list` shows targets and the default. Use `--target NAME`
per command or `target use NAME`. The daemon never guesses a world.

### The game side: `/mcagent` says the control transport is disabled

The flag is missing from that instance's JVM arguments. Add
`-Dmcagent.control=true` and restart. A newly added mod version also only takes
effect after a restart; check the mod list in the log first.

### Two clients, one port file (legacy adapter)

Every legacy endpoint writes a port file, so two clients sharing a game
directory can collide. Give the second one its own data directory and port:

```
-Dmcagent.dir=<instance>/mc-agent-b -Dmcagent.port=25591
```

### A mod crashes the client at startup (and only on remote desktop)

Seen with Axiom 5.5.0 over RDP: `Dear ImGui Assertion Failed: ... Out of
texture memory` while the editor builds its font atlas. Not our code - updating
the mod fixed it. If a crash looks unrelated to the interface, take the other
mod out and see whether it survives.

## Commands and feedback

### A command worked but returned nothing

Feedback is chat. `mc-agent command` only *sends*; use
`mc-agent command-output "<line>"` (or `tools/game_cmd.py`) when you want the
answer - it collects the messages the command produced.

### "Incorrect argument for command" for a gamerule

Minecraft 26.2 renamed the gamerules to `snake_case`:

| old | 26.2 |
| --- | --- |
| `doMobSpawning` | `spawn_monsters` |
| `randomTickSpeed` | `random_tick_speed` |
| `doDaylightCycle` | `advance_time` |

### A write timed out with `resultUnknown`

Do not repeat it blindly. Check `mc-agent request-status <id>` and the
`mc-agent requests` ledger; a request that timed out before the server claimed
it is cancelled and safe to retry, while one that was already running keeps its
entry until the server reports the final state. If the server has no record
(for example after a game restart), the entry stays `unresolved` for the
operator to decide.

### A `/summon` silently did nothing

If the target position is in an unloaded chunk the entity is simply not
created, and the command still reports success. In a lab, force-load the area
first - `fork_verify.py restore` does it for you from the recording's bounding
box (legacy local tooling).

## Ticks, order and determinism

### Entities that die but never go away

Kill them **while the game is running**, then `save-all flush`, then freeze. A
frozen game never runs the loop that removes dying entities, so they sit in the
tick list as ghosts - invisible to selectors, visible to anything that reads
the list.

### A restored fork is short of entities

Check, in order: chunks loaded (see above), the recording's radius (a radius is
measured *from a player*; a headless lab has none, so ask for everything), and
whether the recorded entity is a passenger - a passenger's NBT is nested inside
its vehicle and comes back with it, so it is not summoned separately.

### The same experiment gives different numbers on different runs

The entity tick order is rebuilt at load time from the order the chunks load,
and physics is computed entity by entity. In a lab you can own the order:
record it and compare snapshots. If two runs differ, the diff shows which
entities moved differently - that is the measurement, not a failure.

Related knobs worth pinning in a lab: `gamerule spawn_monsters false`,
`gamerule random_tick_speed 0`, `gamerule advance_time false`,
`difficulty peaceful`, and `tick freeze` + `tick step N` for deterministic
stepping.

## The lab

### The lab has no entities although the world was copied

A headless server has no player, so no chunks are loaded. Force-load the area
you care about (`forceload add <x1> <z1> <x2> <z2>`) and remember that the
copied world still carries entity data inside the region files - if you want
only the recorded entities, clear in game and `save-all flush` before
restoring.

### Two servers, one port

The legacy adapter scans upward from its base port, so a second instance lands
on the next free one. Read each instance's own port file rather than assuming
25580/25581. The control transport uses each server's own game port.

## The agent

### The agent cannot see the game

Call `mc-agent version`, `doctor` and `capabilities` first. Distinguish an
unreachable daemon, a disconnected mod and an unsupported operation from the
actual error code. If the wrong player is described, call `player` with that
player's stable UUID; one server-vantage daemon resolves every online player.

### The compatibility loop answers itself, or never answers

This applies only to the legacy client-vantage `mc-agent-loop`, not to a
user-driven harness or the webhook design. The loop ignores chat from the
client it is attached to, so a single-player session cannot trigger it with its
own chat. Either use a second player
([player identity](player-identity.md)), or use one-shot turns:
`mc-agent-loop once "..."`. The loop depends on the legacy Python bridge and is
not required for Codex, Claude Code or any harness that runs the CLI.

### Replies are cut off

Chat lines are short. `--chunk-size` splits longer answers, `--chunk-delay`
paces them. This is loop configuration, not a Toolkit command.

### An agent answer never appears in game

The daemon never posts replies into Minecraft. Configure delivery in the
harness or webhook route; see
[Hermes and unattended operation](hermes-setup.md).
