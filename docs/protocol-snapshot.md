# Snapshot protocol v1 (forking a live world)

The contract between the in-game mod, the bridge, and the lab tooling. Written
before the code so the three pieces can be built in parallel; anything here can
change, but not silently.

## Why this exists

A save file contains blocks and entity NBT, but **not the entity tick order**.
That order is rebuilt at load time (entities are appended to the level's
`EntityTickList`, an insertion-ordered map, as chunks load), and it changes the
result of anything computed entity by entity - pushes, cramming, explosions.

So "fork the live world" needs two different things:

* **blocks/chunks**: `/tick freeze` -> `/save-all flush` -> copy the region files;
* **entities + their order + in-memory state**: only readable inside the process.

## Instance primitive (mod side)

```
SNAPSHOT [radius] [name]     radius absent or 0 = every entity the level ticks
SNAPSHOTS                    list what is already on disk
```

Reply:

```json
{"type":"snapshot_ack","id":"before","dir":"<absolute path>","entities":521,
 "orderHash":"9f2c...","tick":104233,"dimension":"minecraft:overworld","bytes":48213}
```

`SNAPSHOT`'s arguments are positional: a numeric first token is the radius and
the next token is the name. A caller that wants a name and no radius sends an
explicit `0` (`SNAPSHOT 0 before`), so the mod never has to guess whether the
token after `SNAPSHOT` is a radius or a name.

`SNAPSHOTS` answers `{"type":"snapshots","snapshots":[...]}`, one entry per
directory on disk with the same fields as the ack (`name`, `dir`, `entities`,
`orderHash`, `tick`, `createdAt`, `bytes`) so a consumer can pick one to restore
without reading `meta.json` itself.

Files, under `<mcagent.dir>/snapshots/<name>/`:

`entities.jsonl` - one JSON object per line, **in tick order**:

```json
{"order":0,"uuid":"466e11e1-...","type":"minecraft:sulfur_cube","entityId":2096,
 "pos":[103.5,56.0,52.5],"vel":[0.0,-0.0784,0.0],"yaw":180.0,"pitch":0.0,
 "nbt":"{Motion:[0.0d,-0.0784d,0.0d],...}","passengers":[],"vehicle":null}
```

`meta.json`:

```json
{"protocol":1,"mod":"mc-agent-interface","modVersion":"0.5.0","minecraft":"26.2",
 "tick":104233,"dimension":"minecraft:overworld","radius":64.0,"entities":521,
 "orderHash":"9f2c...","createdAt":1790145600000,"frozen":true,
 "worldDir":"<absolute path or null>","instance":"server|client"}
```

Rules:

* `orderHash` = first 16 hex chars of `sha256(join(":", uuids in tick order))`.
  It is the one value that proves a restore reproduced the order.
* `nbt` is `Entity#saveWithoutId` rendered as SNBT, i.e. exactly what
  `/summon <type> <x> <y> <z> <nbt>` accepts.
* `pos`/`vel`/rotation are repeated outside `nbt` so a consumer does not have to
  parse SNBT for the common case.
* `SNAPSHOT` writes `entities.jsonl` first and `meta.json` last, so a reader
  that finds `meta.json` finds the entities that belong to it.
* Snapshots never contain blocks. Blocks are region files, copied by the bridge.
* Snapshots are append-only per name; asking for an existing name overwrites it
  and says so in the reply (`"replaced": true`).

## Server-side `STATE` additions

When the mod runs with the server vantage, `STATE` also reports where the world
lives, so the bridge can find the region files without being told:

```json
{"type":"state","inWorld":true,"instance":"server","levelName":"量子硫方怪",
 "worldDir":"C:/.../saves/量子硫方怪","tick":104233,"players":1,"entities":521}
```

Ports: the client vantage listens on `mcagent.port` (25580), the server vantage on
`mcagent.serverPort` (25581) with data under `mcagent.serverDir`.

## Bridge tools (composition over the primitive)

| tool | does |
| --- | --- |
| `mc_snapshot(radius, name)` | runs `SNAPSHOT`, returns the ack |
| `mc_snapshots()` | lists snapshots on the instance |
| `mc_fork(name, radius, regions, world_dir)` | freeze -> `save-all flush` -> snapshot -> copy world files -> unfreeze; returns the fork directory + manifest |
| `mc_restore(dir, dry_run, target)` | reads `entities.jsonl` and issues `/summon` **in recorded order** (dry run by default) |
| `mc_order(dir, target)` | re-snapshots the target and compares `orderHash` with `dir` |

`mc_fork` copies the instance's world directory - `level.dat`, `level.dat_old`,
the world's `data/`, `datapacks/` and *every* dimension's region, entity and POI
files (26.2 keeps those under `dimensions/<namespace>/<dimension>/`; older
layouts used top-level `region/`, `entities/`, `poi/` and `DIM*/`) - and skips
`session.lock`, the player data (`playerdata/` before 1.21, `players/` in 26.2),
`stats/`, `advancements/`, `logs/`. The skip list is the filter, not an include
list: the lab has to *load* the same world, so everything else in the directory
comes along, matched by name at any depth, and the manifest reports what was
left behind.

`target` on `mc_restore`/`mc_order` names the instance the call is aimed at. A
bridge owns a single mod connection, so it is a label carried through in the
reply - and used to name the throwaway snapshot `mc_order` takes, which is
`order-<target>` (`order-check` when no target is given) and stays on disk.

## Restoring, and why it needs no code of ours

```bash
/summon minecraft:sulfur_cube 103.5 56.0 52.5 {Motion:[0.0d,-0.0784d,0.0d],...}
```

one line per entity, in file order. Vanilla rebuilds the tick list in that
order, so a re-snapshot should report the same `orderHash` - that equality is
the acceptance test for the whole track.

## Known limits (deliberate)

* Frozen ticks do not freeze the *client*; a client-vantage snapshot is a view,
  not the truth.
* Command length: a very large entity NBT could exceed the command limit when
  restoring through a player connection; the lab restores through the server
  console, which has far more room.
* No mixins, no new registry content: this protocol observes and reproduces, it
  does not change game behaviour.

## Verified on a live world (26.2, single player, integrated server)

| Step | Result |
| --- | --- |
| client and server vantage in one process | ports 25580 (client) and 25581 (integrated server) |
| `SNAPSHOT 64 compact` | 521 entities, 610 KB, order hash `a600f3f3ab41890c`, identical on a second snapshot in the same session |
| `mc_fork live-fork-01` | 82 files, 47.5 MB copied, `players/` and `session.lock` skipped, world held 56 `.mca` files under `dimensions/` |
| `fork_verify.py inspect` | 521 records, type histogram, order hash matches meta, no validation errors |
| one entity's NBT through the game's own parser | `data modify storage mcagent:probe entity set value <nbt>` succeeded and read back identically, then was removed - compact SNBT is summonable |
| open question | a restore into a lab and a re-snapshot has to produce the same hash; that is the acceptance test the lab track is for |
