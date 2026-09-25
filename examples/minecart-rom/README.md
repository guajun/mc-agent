# Minecart ROM fixture

Test-side infrastructure for the [Minecart ROM use case](../../docs/issues/minecart-rom-e2e-use-case.md).
It publishes the base map as a versioned, hash-checked artifact and turns a
fresh copy of it into a deterministic, auditable *ready* state: a real Carpet
fake player facing the machine, the world frozen, and a recorded stack of chest
minecarts waiting on the rail.

This is stage-one fixture work only. It does **not** solve the ROM, and it does
not contain a research logger or an answer.

```
examples/minecart-rom/
  README.md                 this file
  map-manifest.json         the immutable artifact URL, SHA-256, size, versions
  fixture-spec.json         machine geometry, user, program, scene parameters
  dist/                     the exported base world ZIP (committed)
  runner/
    minecart_rom.py         the CLI: fetch / import / up / init / validate / ...
    fixture.py              download, safe unpack, RCON snapshots, initialization
    export_world.py         maintainer tool that produced dist/*.zip
    selftest.py             offline tests (no game, no network)
  calibration/              measured parameters and the raw evidence records
```

## Quick start

```powershell
# 1. download + hash-check + unpack the artifact into labs/_cache/maps/
python examples/minecart-rom/runner/minecart_rom.py fetch --cold

# 2. provision a fresh lab copy and start it on fixed ports
python examples/minecart-rom/runner/minecart_rom.py up --lab rom15 `
    --rcon-port 27150 --server-port 27151 `
    --java "$env:MC_AGENT_JAVA" --memory 3G

# 3. create the ready fixture (fake player + cart stack), frozen
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
Minecraft 26.2, Fabric loader 0.19.5 and Java 25. The base world itself is
always downloaded into `labs/_cache/maps/` and verified before it is unpacked.

Offline checks for the runner (hash mismatch, corrupt/unsafe archives, snapshot
parsers, ready/reload/premature classifiers) run without a game or a network:

```powershell
python examples/minecart-rom/runner/minecart_rom.py selftest
```

## The map artifact

`map-manifest.json` pins everything needed to rebuild the fixture:

| Item | Value |
| --- | --- |
| artifact | `minecart-rom-base-1.0.0.zip`, 748,718 bytes |
| SHA-256 | `11d45a85…157bd` (full value in the manifest) |
| world tree hash | `c766c971…14361` (sorted path + file hash, `session.lock` excluded) |
| Minecraft | 26.2 (DataVersion 4903), flat void overworld |
| Fabric loader / installer | 0.19.5 / 1.1.2 |
| Java | 25 |
| mods | Fabric API `0.161.0+26.2`, Carpet `26.2+v260616` (hashes pinned) |

The export keeps the datapack switches exactly as the source save had them;
`minecart_improvements` **must stay disabled**, because the cart stack only
forms under the classic minecart behaviour.

`artifact.url` is a versioned, immutable URL. It is currently pinned to the
commit in this branch that added the ZIP. A GitHub release asset
(`minecart-rom-base-v1.0.0`) is the preferred long-term home; publishing one
does not change the file or its hash. `--url`/`mirrors` can point at any mirror
for a run, and the runner records the URL it actually used.

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
produces the same SHA-256 for the same world.

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
6. captures the full ready snapshot and the complete command transcript.

No command block is placed anywhere; the exporter scans every chunk palette and refuses an export that contains one. Initialization is pure test-side command
traffic (RCON). The note block is the only input; the fixture never presses it.

The ready snapshot (`ready-snapshot.json`) is deliberately not sanitized: cart
UUIDs, positions, motions, inventories, the full entity NBT dump of every
cart, the in-memory entity order, the machine block states, the user identity
and the frozen tick state. UUIDs are
used for correlation only — no name, tag or scoreboard encodes an order or an
answer.

## Ready contract and failure classification

`validate` re-reads the live world and compares it with the ready snapshot:

* `READY` — same server process, same user UUID/position/rotation, same cart
  UUIDs in the same in-memory order, at rest on the rail, same inventories,
  machine at its base state, world frozen;
* `PREMATURE_OUTPUT` — a cart left the stack, is moving, or disappeared before
  the experiment started;
* `FIXTURE_INVALID:RELOAD` — the server process restarted (or the user/order
  changed) between ready and hand-off;
* `FIXTURE_INVALID` — init failed, the machine is dirty, or anything else.

A fixture that is not `READY` must not be handed to an agent.

## Calibrated scene parameters

Measured on 2026-09-26 against Minecraft 26.2 / Fabric loader 0.19.5 / Carpet
`26.2+v260616`; the machine values live in `fixture-spec.json` and the raw logs
in `calibration/`.

| Parameter | Measured value |
| --- | --- |
| input | right-click the note block at `(11, -54, -23)`; it plays and advances `note` by 1 |
| input semantics | `powered=true` for one tick; the adjacent up-facing sticky piston fires |
| stack position | rail block `(14, -52, -22)`, spawn `(14.5, -52, -21.5)`, rest `(14.5, -51.9375, -21.5)` |
| pop rate | one cart per press; pop order followed spawn order for the calibrated stack (recorded, not assumed) |
| cycle | 4 ticks; a press every ≥4 ticks is safe |
| output boundary | first tick with `x > 15.0` (crossed at cycle tick 3) |
| pop trajectory | peaks near `y=-44.6`, drifts east to `x≈23.5` |
| void window | entities are removed below `y=-128`; popped cart removed ~5.7–8 s after the press |
| end condition | every cart popped and removed; fixture timeout 180 s |
| user | fake player at the operating position, aimed at the note block's north face |

The machine returns to its base 16-block state after every cycle; the only
persistent change is the note block's `note` value, which `init` resets.

## Limitations

* This fixture prepares the game, not the experiment. Pressing the note block,
  recording transient carts and answering the ROM are agent work.
* The fake player rides an invisible marker armor stand because the machine
  floats over the void and Carpet fake players cannot toggle creative flight.
  The seat is recorded in every snapshot and can be disabled in the spec.
* The manifest URL is pinned to a commit of this branch; publish the release
  asset and update `artifact.url` when the branch lands.
* The calibrated stack uses three carts; other programs can be passed with
  `init --program program.json` and must be re-calibrated before their answers
  are trusted.
