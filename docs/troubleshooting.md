# Troubleshooting

Most of these were found the hard way, on a real world, while building the thing.

## The game side

### The Toolkit daemon says it cannot reach the mod

```
[mc-agent-bridge] cannot reach interface mod on 127.0.0.1:25580; retrying
```

Check, in this order:

1. is the mod in `<instance>/mods/` and does the log show
   `[mc-agent-interface] listening on ...`?
2. is the game *in a world*? Many primitives (commands, chat, entities) need one.
3. is another process holding the port? The mod moves to the next free port and
   writes `port.txt`; point the Toolkit daemon at it with `--port-file`.
4. is a firewall blocking loopback? (Rare, but a corporate VPN client can.)

### `/mcagent` says "unknown or incomplete command"

Client commands need the client to be in a world for anything that touches it, and
a *newly added* mod version only takes effect after a restart. If the command has
never worked, the jar is not loaded - check the mod list in the log.

### Two clients, one port file

Every client writes `<gameDir>/mc-agent/port.txt`, so two clients sharing a
game directory overwrite each other's file. Give the second one its own data
directory and port:

```
-Dmcagent.dir=<instance>/mc-agent-b, -Dmcagent.port=25591
```

### A mod crashes the client at startup (and only on remote desktop)

Seen with Axiom 5.5.0 over RDP: `Dear ImGui Assertion Failed: ... Out of texture
memory` while the editor builds its font atlas. Not our code - updating the mod
fixed it. If a crash looks unrelated to the interface, take the other mod out and
see whether it survives.

## Commands and feedback

### A command worked but returned nothing

Feedback is chat. `mc_command` only *sends*; use **`mc_command_output`** (or
`tools/game_cmd.py`) when you want the answer - it collects the messages the
command produced.

### "Incorrect argument for command" for a gamerule

Minecraft 26.2 renamed the gamerules to `snake_case`:

| old | 26.2 |
| --- | --- |
| `doMobSpawning` | `spawn_monsters` |
| `randomTickSpeed` | `random_tick_speed` |
| `doDaylightCycle` | `advance_time` |

### A `/summon` silently did nothing

If the target position is in an unloaded chunk the entity is simply not created,
and the command still reports success. In a lab, force-load the area first -
`fork_verify.py restore` does it for you from the recording's bounding box.

## Ticks, order and determinism

### Entities that die but never go away

Kill them **while the game is running**, then `save-all flush`, then freeze. A
frozen game never runs the loop that removes dying entities, so they sit in the
tick list as ghosts - invisible to selectors, visible to anything that reads the
list.

### A restored fork is short of entities

Check, in order: chunks loaded (see above), the recording's radius (a radius is
measured *from a player*; a headless lab has none, so ask for everything), and
whether the recorded entity is a passenger - a passenger's NBT is nested inside
its vehicle and comes back with it, so it is not summoned separately.

### The same experiment gives different numbers on different runs

The entity tick order is rebuilt at load time from the order the chunks load, and
physics is computed entity by entity. In a lab you can own the order:
`mc_fork` records it and `fork_verify.py check` compares it. If two runs differ,
`diff` shows which entities moved differently - that is the measurement, not a
failure.

Related knobs worth pinning in a lab: `gamerule spawn_monsters false`,
`gamerule random_tick_speed 0`, `gamerule advance_time false`,
`difficulty peaceful`, and `tick freeze` + `tick step N` for deterministic
stepping.

## The lab

### The lab has no entities although the world was copied

A headless server has no player, so no chunks are loaded. Force-load the area
you care about (`forceload add <x1> <z1> <x2> <z2>`) and remember that the copied
world still carries entity data inside the region files - if you want only the
recorded entities, clear in game and `save-all flush` before restoring.

### Two servers, one port

The mod scans upward from its base port, so a second instance lands on the next
free one. Read each instance's own `port.txt` rather than assuming 25580/25581.

## The agent

### The compatibility loop answers itself, or never answers

This applies only to the legacy client-vantage `mc-agent-loop`, not to a
user-driven Harness or the server-vantage webhook design. The loop ignores chat
from the client it is attached to, so a single-player session cannot trigger it
with its own chat. Either use a second player ([player identity](player-identity.md)),
or use one-shot turns:
`mc-agent-loop once "..."`.

### Replies are cut off

Chat lines are short. `--chunk-size` splits longer answers, `--chunk-delay`
paces them.

### The Harness cannot see the game

Call `mc_status` and `mc_capabilities` first. If they cannot reach the daemon,
check that `mc-bridge run` is active and discovered the server-vantage
`mc-agent-server/port.txt`. If the wrong player is described, call `mc_player`
with that player's stable UUID (or name as a convenience); one server-vantage
Toolkit can resolve every online player in its instance. See
[player identity](player-identity.md).
