# Who is the agent, in game?

Short answer: the interface mod is a **client** mod, so it borrows the identity
of the client it runs in. To give the agent an identity of its own there are
three routes, and **Carpet fake players are the cheapest by a wide margin** - no
LAN, no second account, no second client.

## 0. A Carpet fake player (recommended: body, voice and data, single client)

A fake player is a *server-side* player. In single player the integrated server
is the server, so nothing else has to change: your own client creates it with a
command and it then exists in the world like any other player.

```bash
# one-time per world: allow the command for non-op callers
python tools/game_cmd.py "carpet commandPlayer true"

# body: spawn it, drive it, remove it
python tools/fake_player.py spawn deepseek 103 95 52
python tools/fake_player.py action deepseek look north
python tools/fake_player.py action deepseek jump
python tools/fake_player.py action deepseek attack continuous   # mines/attacks
python tools/fake_player.py kill deepseek

# voice: the server broadcasts on its behalf
python tools/fake_player.py say "hello from the agent" --as deepseek
# -> [deepseek] hello from the agent

# data: authoritative, straight from the server
python tools/fake_player.py status deepseek
```

Verified on a live world: `deepseek` spawned, `look north` moved the server's
`Rotation` to `[180.0f, 0.0f]`, the client saw it as a `RemotePlayer` at the same
position, `/data get entity deepseek Motion` returned the server's own numbers,
and `execute as deepseek run say ...` produced `[deepseek] ...` in chat.

| Pros | Cons |
| --- | --- |
| No LAN, no second account, no second client, no extra RAM | Needs Carpet on the server, `commandPlayer` enabled, and command permission |
| Authoritative state (`/data get`) instead of an interpolated client view | No client of its own: no screens, no camera, no client mods |
| Counts as a player for game rules, mob targeting and redstone | Because it has no client, the server answers its own command feedback to nobody - read state with `data get` on the caller, not `execute as` |
| Drivable at command granularity: `use`, `attack`, `jump`, `look`, `move`, `mount`, `hotbar`, `drop`, `sneak`, `sprint`, ... | One command per tick at best; not a tick-accurate instrument (see Scarpet / server adapter) |

The fake player has no command permission of its own (and `/op` does not exist
in single player), which is why its voice is the server broadcasting *for* it:
`execute as <name> run say <text>`. `mc-agent-loop` can do that directly:

```bash
mc-agent-loop run --backend hermes --trigger @codex \
  --reply-mode command --reply-command 'execute as deepseek run say {text}'
```

## 1. A second client (a real player with a client view)

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

Actions available in the version tested (Carpet 26.2+v260616):
`spawn`, `kill`, `rejoin`, `stop`, `use`, `jump`, `attack`, `drop`, `dropStack`,
`swapHands`, `hotbar`, `shadow`, `mount`, `dismount`, `sneak`, `unsneak`,
`sprint`, `unsprint`, `look`, `turn`, `move`, `startFallFlying`, `loadItems`.
`spawn at` takes coordinates, not a player name, in this build.

## 2. A server-side adapter (the RFC-grade option)

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
