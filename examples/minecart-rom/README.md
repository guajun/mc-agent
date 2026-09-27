# Minecart ROM fixture

Test-side infrastructure for the Minecart ROM use case ([issue #13](https://github.com/guajun/mc-agent/issues/13), [stage-one gate #14](https://github.com/guajun/mc-agent/issues/14)).
It publishes the base map as a versioned, hash-checked artifact and turns a
fresh copy of it into a deterministic, auditable *ready* state: a real Carpet
fake player facing the machine, the world frozen, a recorded stack of chest
minecarts on the rail, and the authoritative entity tick order read from a real
`SNAPSHOT` of the interface mod's `EntityTickList`.

This is stage-one fixture work only. It does **not** solve the ROM, and it does
not contain a research logger or an answer.

```
examples/minecart-rom/
  README.md                 this file
  map-manifest.json         the immutable artifact URL, SHA-256, size, versions
  fixture-spec.json         machine geometry, user, calibration program, scene parameters
  dist/                     the exported base world ZIP (committed)
  runner/
    minecart_rom.py         the CLI: fetch / import / up / init / validate / evidence / challenge
    fixture.py              download, safe unpack, RCON snapshots, initialization, validation
    interface_mod.py        the server-vantage SNAPSHOT client (real EntityTickList order)
    evidence.py             the stage-one gate fixture_map evidence pack
    export_world.py         maintainer tool that produced dist/*.zip
    selftest.py             offline tests (no game, no network)
  calibration/              measured parameters and the raw evidence records
```

## Quick start

```powershell
# 1. download + hash-check + unpack the artifact into labs/_cache/maps/
python examples/minecart-rom/runner/minecart_rom.py fetch --cold

# 2. provision a fresh lab copy with the pinned interface mod and fixed ports
python examples/minecart-rom/runner/minecart_rom.py up --lab rom15 `
    --rcon-port 27150 --server-port 27151 --vantage-port 27152 --bridge-port 27153 `
    --interface-mod labs/_cache/mods/mc-agent-interface-0.6.0.jar `
    --java "$env:MC_AGENT_JAVA" --memory 3G

# 3. create the ready fixture (fake player + cart stack + tick-order snapshot)
python examples/minecart-rom/runner/minecart_rom.py init --lab rom15 `
    --records examples/minecart-rom/calibration/records/run-01

# 4. re-read the live world and classify it before handing it to an agent
python examples/minecart-rom/runner/minecart_rom.py validate --lab rom15 `
    --ready examples/minecart-rom/calibration/records/run-01/ready-snapshot.json

# 5. done
python examples/minecart-rom/runner/minecart_rom.py stop --lab rom15
```

`up` uses the shared `labs/_cache` for the Fabric launcher and the mods, exactly
like [`tools/lab_server.py`](../../docs/lab-server.md); it pins the lab to
Minecraft 26.2, Fabric loader 0.19.5 and Java 25, and the lab server passes the
server-vantage port to the mod at start. The base world itself is always
downloaded into `labs/_cache/maps/` and verified before it is unpacked.

Offline checks for the runner (hash mismatch, corrupt/unsafe archives, snapshot
parsers, ready/reload/premature/machine/tick-order classifiers, evidence and
challenge helpers) run without a game or a network:

```powershell
python examples/minecart-rom/runner/minecart_rom.py selftest
```

## The map artifact

`map-manifest.json` pins everything needed to rebuild the fixture:

| Item | Value |
| --- | --- |
| artifact | `minecart-rom-base-1.0.0.zip`, 748,712 bytes |
| SHA-256 | `46954828…a01387` (full value in the manifest) |
| world tree hash | `b9d7c2cd…413ae51` (sorted path + file hash, `session.lock` excluded) |
| Minecraft | 26.2 (DataVersion 4903), flat void overworld |
| Fabric loader / installer | 0.19.5 / 1.1.2 |
| Java | 25 |
| mods | Fabric API `0.161.0+26.2`, Carpet `26.2+v260616`, mc-agent-interface `0.6.0` (hashes pinned) |

The export keeps the datapack switches exactly as the source save had them;
`minecart_improvements` **must stay disabled**, because the cart stack only
forms under the classic minecart behaviour.

The artifact is byte-for-byte reproducible: the ZIP entries, gzip streams and
the `exported_at` stamp inside `level.dat`/`EXPORT.json` are all deterministic
(`--exported-at` or `SOURCE_DATE_EPOCH` override the fixed default). Exporting
the same source path twice with the same options produces the same SHA-256, and `evidence` re-exports the
imported world twice to prove it rather than asserting it. The manifest's
`world` block (hash, file count, byte count) is checked against `EXPORT.json`
and the extracted tree before a world is imported.

`artifact.url` is a versioned URL pinned to commit `f7d8e43` of this branch.
The SHA-256 in the manifest is the check that matters: a fresh cache downloads
that exact file and verifies it. A GitHub release asset
(`minecart-rom-base-v1.0.0`) is optional housekeeping and would not change the
file or its hash. `--url`/`mirrors` can point at any mirror for a run, and the
runner records the URL it actually used.

The source save `D:\MC\MC_Game\.minecraft\versions\26.2-Fabric\saves\Minecart ROM test`
is never modified. `runner/export_world.py` reads it, strips player data, the
`singleplayer_uuid` in `level.dat`, `session.lock`, logs and every saved entity
(three leftover falling items), and writes a reproducible ZIP:

```powershell
python examples/minecart-rom/runner/export_world.py `
    --source "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test" `
    --out examples/minecart-rom/dist/minecart-rom-base-1.0.0.zip --version 1.0.0
```

The deterministic ZIP (sorted entries, fixed timestamps, fixed modes) always
produces the same SHA-256 for the same world at the same source path with the same export options. `EXPORT.json` records the absolute source path, so relocating identical source bytes changes the ZIP hash while preserving the exported world hash.

## What `init` does

1. force-loads the machine and output chunks, freezes the world, and verifies
   all 15 machine block signatures from `fixture-spec.json`; a dirty machine
   aborts the run with the broken positions;
2. cleans fixture leftovers (carts, the hover seat, the previous fake player);
3. summons a real Carpet fake player (`/player romuser spawn … in creative`),
   mounts it on an invisible `NoGravity` marker armor stand (the machine floats
   over the void and a fake player cannot toggle flight), aims it at the note
   block and freezes the world again;
4. resets the note block to `note=20, powered=false`;
5. for every program entry, in order: summons a chest minecart at the rail
   spawn point, reads back its UUID, fills it with `data merge`, and records the
   spawn index — the carts overlap and form the stack;
6. takes a real `SNAPSHOT` through the interface mod's server vantage, which
   reads `ServerLevel.entityTickList`; the cart order from `entities.jsonl` is
   the **authoritative tick order**, and the mod's full NBT is cross-checked
   against the RCON reads (items including components, position, motion);
7. captures the full ready snapshot and the complete command transcript.

No command block is placed anywhere. Initialization is pure test-side command
traffic (RCON plus the read-only mod snapshot). The note block is the only
input; the fixture never presses it.

The RCON `@e` selector order is recorded separately as `rcon_order` — an
observation, never labelled as the tick order. `tick_order` comes only from the
mod snapshot, and `tick_order_hash` is a 16-hex hash over that order normalized
by spawn index (so three independent runs produce the same value).

The ready snapshot (`ready-snapshot.json`) is deliberately not sanitized: cart
UUIDs, positions, motions, inventories, the full entity NBT dump of every cart,
the in-memory tick order, the machine block states, the user identity and the
frozen tick state. UUIDs are used for correlation only — no name, tag or
scoreboard encodes an order or an answer.

## Ready contract and failure classification

`validate` re-reads the live world and compares it with the ready snapshot on
every invariant:

* `READY` — same server process, same user UUID/position/rotation, the same
  hover-seat entity (UUID, position, NBT) with the user still mounted, same
  cart UUIDs, at rest on the rail, the same **full entity NBT** (inventory and
  item components included), the calibrated start note value, the frozen ready
  day tick, machine at its base state, world frozen, and the same authoritative
  tick order plus the same mod full-level order hash;
* `PREMATURE_OUTPUT` — a cart left the stack, is moving, or disappeared before
  the experiment started;
* `FIXTURE_INVALID` — machine broken, world unfrozen, cart NBT changed, tick
  order/order hash changed, user moved, or the interface evidence is missing;
* `FIXTURE_INVALID:RELOAD` — the server process restarted between ready and
  hand-off.

A fixture that is not `READY` must not be handed to an agent.

## Challenge programs

`fixture-spec.json` carries the **public calibration program** used by the
committed evidence. It must not be used to grade a cold-start agent. Generate a
fresh sealed program for evaluation runs and keep it out of the repository:

```powershell
python examples/minecart-rom/runner/minecart_rom.py challenge `
    --seed 20260926 --carts 4 --out labs/challenges/challenge-20260926.json
python examples/minecart-rom/runner/minecart_rom.py init --lab rom15 `
    --program labs/challenges/challenge-20260926.json --records labs/runs/challenge-01
```

The generator records the seed and the program SHA-256 and deliberately does
not produce the pop order: the order is observed live and sealed on the
evaluation side. `init` records the program in the ready snapshot, so
`validate` checks a custom run against its own program.

## Stage-one gate integration

The merged integration gate (`tools/stage1_gate.py`, issue #14) consumes a
bundle of raw artifacts. `evidence` turns this fixture's records into the
`fixture_map` slice of that bundle:

```powershell
python examples/minecart-rom/runner/minecart_rom.py evidence `
    --records examples/minecart-rom/calibration/records `
    --out labs/stage1-evidence `
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
```

It writes `artifacts/fixture_map/{fixture-manifest,player-identity,command-block-scan,cleanup-rebuild}.json`
and `init-runs.jsonl`, plus `bundle-fragment.json` with the evidence mapping.
The builder verifies instead of asserting:

* `map.download_verified` is true only when a cold download record hashes to
  the manifest;
* `map.bad_hash_rejected` re-runs the wrong-hash rejection against the artifact;
* `world.tree_sha256` uses the gate's own `hash-tree` algorithm;
* `source_world_untouched` is true only when the source save hashes to the
  read-only baseline recorded in `docs/stage1-gate.md`;
* `command-block-scan.json` scans every world copy passed with `--world-dir`
  (and the lab copies referenced by the records);
* every `init-runs.jsonl` row carries the interface-mod tick order
  (`order_source: interface-snapshot`) and the normalized 16-hex order hash;
* item comparisons are quote-aware and key-order tolerant (compound keys are
  sorted, whitespace inside strings is preserved), and the item parser handles
  nested component compounds;
* `player-identity.json` proves the user's view ray hits the note block;
* `map.immutable` is true only when the URL carries a 40-hex commit pin (the
  pin type is recorded as `url_pin`);
* `rebuild_reproducible` is true only when two fresh exports of the imported
  world are byte-identical (the check and both hashes are recorded).

Before the gate check, bind the emitted `run_id`/`instance_id` values to the
bundle's `run.child_runs` (the coordinator declares them; `--run-map OLD=NEW`
can rewrite run ids at build time). A minimal bundle with only the
`fixture_map` slice checked passes both `fixture_map` and `evidence_integrity`;
the remaining checks stay blocked until the other prerequisites contribute
their artifacts.

## Limitations

* This fixture prepares the game, not the experiment. Pressing the note block,
  recording transient carts and answering the ROM are agent work.
* The fake player rides an invisible marker armor stand because the machine
  floats over the void and Carpet fake players cannot toggle creative flight.
  The seat is recorded in every snapshot and can be disabled in the spec.
* The interface mod is required for ready evidence: `init` refuses to run
  without it, and the fixture refuses to call a fixture READY without the real
  `EntityTickList` order plus a matching snapshot protocol and dimension.
* The public calibration program is three carts; evaluation runs should use a
  sealed `challenge` program, and any new program needs a live calibration of
  its pop order before its answers are trusted.
* **Stage-two context note (not a capability sandbox):** the committed
  `calibration/` tree (records, logs, gate slice) and the `program` block of
  `fixture-spec.json` contain the calibration program and its observed pop
  order. Before a cold-start run, initialize with a fresh `--program` challenge
  that appears nowhere in the committed records, and simply do not preload the
  calibration history or those answers into the task/context. This is only
  about starting context: the agent keeps full source, file, shell and full-NBT
  access, and no runtime capability is removed.
* The calibrator's ready state is intentionally *not* a solution: the pop order
  is observed only when a real run presses the note block.
