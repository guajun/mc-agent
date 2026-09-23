# Who is the agent, in game?

Short answer: the interface mod is a **client** mod, so it borrows the identity
of the client it runs in. If you want the agent to be a player of its own, there
are three ways - and only one of them needs new code.

## 1. A second client (what the mod already supports)

Run another Minecraft instance with the same mod, point a second bridge at that
instance's `port.txt`, and the agent *is* that player: its own inventory,
position, view, and client-side mods. Launch it with
`-Dmcagent.autoConnect=host:port` and it joins by itself.

| Pros | Cons |
| --- | --- |
| Works on any server, no server-side mod | Needs a second account (online mode) or just a name (offline mode) |
| The agent sees what a player sees (entities, screens, client commands) | A whole client's worth of CPU/GPU and RAM |
| Two agents = two clients, fully independent | |

### Verified recipe (single-player world, two identities)

```bash
# client A: yours, hosting the world
python tools/launch_instance.py --minecraft-dir <instance> --version 26.2-Fabric
mc-bridge run --api-port 8765            # bridge A -> your client
mc-bridge call world '{"level": "<level folder>"}'    # open the save
mc-bridge call lan '{"port": 25577, "mode": "offline"}'   # Open to LAN, no session check

# client B: the agent's own player
python tools/launch_instance.py --minecraft-dir <instance> --version 26.2-Fabric \
    --username deepseek \
    --jvm-property mcagent.dir=<instance>/mc-agent-b \
    --jvm-property mcagent.port=25591 \
    --jvm-property mcagent.autoConnect=127.0.0.1:25577
mc-bridge run --api-port 8766 --mod-port 25591        # bridge B -> the agent's client
mc-agent-loop run --backend hermes --trigger @codex --api-port 8766 --env-file .env
```

Then, in your own client, type `@codex ...` and the agent answers from its own
player. Observed on a live world: `gua_jun` asked for the agent's coordinates and
`deepseek` answered "我在主世界（overworld），坐标 X 7.5 / Y 113 / Z -3.5".

Three details that matter:

* **`mode="offline"`** - a client with no Mojang session (the agent) cannot pass
  the LAN server's session check; the host has to accept offline names
  (`MinecraftServer.setUsesAuthentication(false)`). LAN only, trusted networks
  only.
* **One bridge per player.** Both clients write a port file, so give the second
  one its own `-Dmcagent.dir` (and a port) or they overwrite each other.
* **MCP tools point at one bridge, therefore at one player.** The agent's
  `mc_*` tools must use the *agent's* bridge (8766); a second MCP server entry
  pointed at 8765 lets it look at your client as well. With the tools pointed at
  your bridge it happily reports *your* coordinates - which is what happened
  until the MCP env was repointed.

## 2. A Carpet fake player (no new code, but server-side)

Carpet's `/player <name> spawn` creates a *server-side* player entity. The
interface mod cannot attach to it - there is no client behind it - but the
framework already drives it through the `command` primitive, and reads it back
through `entities` and the game-event stream:

```bash
mc-bridge call command '{"command": "player mcagent spawn at 101 95 52"}'
mc-bridge call command '{"command": "player mcagent look north"}'
mc-bridge call command '{"command": "player mcagent jump"}'
mc-bridge call entities '{"radius": 32}'          # it shows up as minecraft:player
mc-bridge call command '{"command": "data get entity <uuid> Motion"}'   # authoritative
mc-bridge call command '{"command": "player mcagent kill"}'
```

Verified on a real world: the fake player spawned 3.5 blocks away, `look north`
set its yaw to 180°, `jump` showed `vy = 0.333` in the client's view, and
`/data get entity <uuid> Motion` returned the server's own numbers
(`[0.0, -0.078, 0.0]`). Command feedback arrives as game messages, which the mod
already captures as `events:game`, so an agent can read what a command answered
without any extra plumbing (`tools/game_cmd.py` does exactly that).

Actions available in the version tested (Carpet 26.2+v260616):
`spawn`, `kill`, `rejoin`, `stop`, `use`, `jump`, `attack`, `drop`, `dropStack`,
`swapHands`, `hotbar`, `shadow`, `mount`, `dismount`, `sneak`, `unsneak`,
`sprint`, `unsprint`, `look`, `turn`, `move`, `startFallFlying`, `loadItems`.

Notes and gotchas:

* Requires Carpet (or a compatible fork) **on the server**, plus permission for
  `/player` - Carpet's `commandPlayer` rule defaults to `ops`, and on a
  single-player world the player must be an op (cheats on).
* `spawn at` takes coordinates, not a player name, in this build.
* `/player ...` is a server command: it crosses the network like any other, so
  it is not tick-precise. For deterministic stepping use `/tick freeze` +
  `/tick step`, and for per-tick server-side recording use Scarpet or a server
  mod (see below).

## 3. A server-side adapter (the RFC-grade option)

Everything above drives the server through commands. If the agent needs
authoritative per-tick data - the actual subject of most cannon/TNT work - the
right shape is a small **server-side** interface that streams entity state at
tick rate, next to the existing client-side one. That is a second adapter
behind the same bridge, not a change to this one, and it belongs in an RFC
(see `docs/rfc/0001-agent-interface.md`, "richer game events").

Scarpet (Carpet's scripting language, `/script`) is a middle ground that already
exists: scripts run inside the server, can read entity NBT and schedule work,
and are installed as files. An agent can manage those scripts through the same
two primitives it already has (write a file, run a command).

## What "client view" means for measurements

The client's entity positions and velocities are interpolated for rendering, so
a `record_start` capture is a *client view* at 20 Hz. It is fine for shape and
timing, but for exact numbers prefer the server's own answer (`/data get`,
Scarpet, or a future server adapter). Observed in the test above: the client
reported `vy = 0.333` for a jumping fake player while the server said `-0.078`
at the moment it was queried - same entity, different vantage points and ticks.
