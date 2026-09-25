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
| Drivable at command granularity: `use`, `attack`, `jump`, `look`, `move`, `mount`, `hotbar`, `drop`, `sneak`, `sprint`, ... | One command per tick at best; not a tick-accurate instrument (see Scarpet / the server vantage) |

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

## 2. The server vantage (shipped in mod 0.5.0)

Everything above drives the server through commands. If the agent needs
authoritative data - the actual subject of most cannon/TNT work - the honest
vantage is the **server side**, and it no longer needs a second adapter: the same
interface mod has a server entrypoint (port 25581, snapshots included), which
also covers single player, where the integrated server runs in the client's own
process. See `protocol-snapshot.md`.

What is still open is streaming that vantage at tick rate and subscribing to
richer game events - [issue #5](https://github.com/guajun/mc-agent/issues/5).

Scarpet (Carpet's scripting language, `/script`) is a middle ground that already
exists: scripts run inside the server, can read entity NBT and schedule work,
and are installed as files. An agent can manage those scripts through the same
two primitives it already has (write a file, run a command).

## 3. The task user: identity and view target (verified live)

The server vantage is also the *identity* vantage. When a task names its user -
normally by UUID - the agent resolves that user with `mc_player` (MCP) or
`mc-bridge call player` (CLI) and gets the **live** server record of exactly
that player: identity, dimension, position, yaw/pitch, eye, and
`player.view.target`, the server-side ray from that player's own eye and look
direction. It never falls back to the host client or to the first entry of the
player list.

Verified combination (2026-09-26), dedicated Fabric 26.2 server, two Carpet
fake players, no player client attached:

| Component | Version / commit | Note |
| --- | --- | --- |
| interface mod | 0.6.0, `3b93ceb` | server-vantage `PLAYER`/`CONTEXT` (PR #4 + #5) |
| bridge | 0.4.1, [`#8`](https://github.com/guajun/mc-agent-bridge/pull/8) | 0.4.0 dropped the mod's split `view`; 0.4.1 keeps it under `player.view` |
| Minecraft / Fabric loader | 26.2 / 0.19.5 | Carpet 26.2+v260616, Fabric API 0.161.0+26.2 |
| Java | 25.0.1 | the lab server's java |

The lab scene: Alice at `(0.5, 100.0, 0.5)` facing a dispenser at
`(0, 101, 4)`, Bob at `(4.5, 100.0, 0.5)` facing an armor stand. The full
26-step transcript was produced by the identity-matrix runner over the bridge
loopback API (`LocalApiClient`, the `cli.*` steps); the MCP captures come from
`tools/mcp_probe.py` against the same daemon (`mc_player`, `mc_context`). Both
surfaces share one daemon method, so the envelopes match; the raw mod replies
are in the bundle as the comparison authority.

| Call | Result |
| --- | --- |
| `mc_player` with Alice's UUID | `found: true`, `name: Alice`, `view.target.type: block`, `block.id: minecraft:dispenser`, `distance: 3.5` |
| `mc_player` with Bob's UUID (Alice is first in `state.playerList`) | `found: true`, `name: Bob`, `view.target.type: entity`, `entity.type: minecraft:armor_stand` |
| Bob looking straight up (pitch -90) | `view.target.type: miss`, `distance: 5.0` |
| an unknown UUID, or an unknown name | `found: false` - no substitution of the other or first player |
| task-start call, then `tp` Alice two blocks forward | same `uuid`/`name`; `z` 0.5 -> 2.5 and the dispenser distance 3.5 -> 1.5; each call is current, not a cached start record |

A `mc_player` reply, abridged:

```json
"player": {
  "uuid": "f0a8f4ba-99f5-412a-9189-db832c934913", "name": "Alice",
  "dimension": "minecraft:overworld", "x": 0.5, "y": 100.0, "z": 0.5,
  "yaw": 0.0, "pitch": 0.0, "eye": [0.5, 101.62000000476837, 0.5],
  "view": {
    "blockRange": 5.0, "entityRange": 5.0,
    "target": {"type": "block", "distance": 3.5,
      "block": {"x": 0, "y": 101, "z": 4, "id": "minecraft:dispenser", "face": "north"}}
  }
}
```

### The cold-start entry

The first cold start needs no webhook and no model backend: an external task
entry carries the identity, and the agent then makes a **live** `mc_player`
call. That is the path verified above.

* **entry**: whatever produced the task - a CLI, a queue, a webhook, an
  operator - passes the player. No game-side receipt is required.
* **fields**: `{"player": "<uuid-or-name>"}`; prefer the dashed UUID. A
  32-character undashed UUID also works, and a name is a convenience. The
  reply's `uuid` is the stable value to carry forward, not the display name.
* **`found: false` is an answer**: report it; never retry against another
  player. `state.playerList` lists who is actually online.
* **`context_id` only if the entry produced one.** A chat event carries
  `contextId`; `mc_context` fetches the frozen bundle. Check `timing`:
  `receipt` is a network chat packet, `broadcast` is a server-side `say` -
  a Carpet fake player's `execute as <name> run say ...` is `broadcast`, so it
  is a real, fetchable bundle but **not** packet-time history. Live network
  chat from a real client was not exercised by this lab, and is not claimed.
* **no Harness backend is added here.** The task entry and the live
  `mc_player` call are the whole identity path; a model loop is a separate
  deliverable.

The full transcript, versions and the pre-fix comparison are in the
[evidence bundle](evidence/rom13-meta16/live-identity.json).

## What "client view" means for measurements

The client's entity positions and velocities are interpolated for rendering, so
a `record_start` capture is a *client view* at 20 Hz. It is fine for shape and
timing, but for exact numbers prefer the server's own answer (`/data get`,
Scarpet, or the server vantage on 25581). Observed in the test above: the client
reported `vy = 0.333` for a jumping fake player while the server said `-0.078`
at the moment it was queried - same entity, different vantage points and ticks.
