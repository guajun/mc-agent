# Fixture calibration evidence

Measured on 2026-09-26 on this machine:

| Item | Value |
| --- | --- |
| Artifact | `minecart-rom-base-1.0.0.zip`, 748,712 bytes, SHA-256 `46954828…a01387`, world hash `b9d7c2cd…413ae51` (byte-for-byte reproducible export) |
| Minecraft | 26.2 (`DataVersion 4903`) |
| Fabric loader / installer | 0.19.5 / 1.1.2 |
| Java | Microsoft OpenJDK 25.0.1 (HMCL runtime `mojang-java-runtime-epsilon`) |
| Fabric API | `0.161.0+26.2` (`e5b858ce…e216a`) |
| Carpet | `26.2+v260616` (`f6ada912…1311c`) |
| mc-agent-interface | `0.6.0` (`45f12e16…f4404f`, built from `3b93ceb`) |
| Lab | `labs/rom15d`, RCON `27156`, game `27157`, server-vantage `27158`, bridge `27159`, heap 3G |
| Source save | `D:\MC\MC_Game\.minecraft\versions\26.2-Fabric\saves\Minecart ROM test` (read only) |

The machine was found and calibrated live, with `tick freeze` / `tick sprint`
tick control. No command block was placed at any point.

## Measured scene parameters

| Parameter | Measurement | Raw evidence |
| --- | --- | --- |
| note-block input | a player right-click plays the note block and advances `note` by 1 (20 → 21 → 22 …); `powered` is true for one tick and fires the adjacent up-facing sticky piston | `logs/cycle_window.log`, `logs/full_cycle.log` |
| stack position | carts rest on the powered rail at block `(14, -52, -22)`: spawn `(14.5, -52, -21.5)`, rest `(14.5, -51.9375, -21.5)`, zero motion | `logs/full_cycle.log`, records |
| one press, one cart | a real `player Romuser use` popped exactly one cart; the remaining carts stayed at rest and the machine returned to its 16-block base state | `logs/cycle_window.log`, `logs/full_cycle.log` |
| pop order | three carts summoned stone → dirt → gold were popped in that order in the calibrated run; recorded as an observation, never assumed | `logs/full_cycle.log` |
| cycle time | the machine is back at its base state 4 ticks after the press; presses 10 ticks apart also work | `logs/cycle_window.log` |
| output boundary | the popped cart crosses `x = 15.0` on cycle tick 3, then drifts east to `x ≈ 23.5`, apex near `y = -44.6` | `logs/cycle_window.log` |
| void window | entities are removed below `y = -128`; a popped cart was removed 5.7–8.0 s (≈110–160 ticks) after the press | `logs/void_window.log`, `logs/cycle_window.log` |
| tick order source | the interface mod's `SNAPSHOT` reads `ServerLevel.entityTickList`; the cart order in `entities.jsonl` is the authoritative tick order, and `orderHash` is the mod's first-16-hex sha256 over the raw UUID sequence, recomputed locally from the entities file before it is accepted | `records/*/ready-snapshot.json`, `records/*/init.out.json` |
| input note | the note block is reset to `start_note=20` and read back; a READY fixture must still be at 20, and `validate` reports `machine-note-changed` otherwise | `negative/note-changed/`, `records/*/ready-snapshot.json` |
| reload behaviour | a graceful server restart preserves cart UUIDs, inventories, positions and the query order, but the fixture treats any restart as a reload and refuses the fixture | `logs/reload_test.log` |

## Reproducibility

Each run used a fresh import of the artifact (`import --reset`), a real Carpet
fake player, the pinned interface mod, and new cart UUIDs. The normalized state
hash (spawn index + position + motion + items) and the normalized **tick-order**
hash were identical:

```
run-01            READY  state 86215e40a0d3d9ba…  tick-order a3ba375742d0de28  mod 0.6.0  cross-check true
run-02            READY  state 86215e40a0d3d9ba…  tick-order a3ba375742d0de28  mod 0.6.0  cross-check true
run-03            READY  state 86215e40a0d3d9ba…  tick-order a3ba375742d0de28  mod 0.6.0  cross-check true
real-url-run      READY  state 86215e40a0d3d9ba…  tick-order a3ba375742d0de28  mod 0.6.0  cross-check true
```

Every ready snapshot also binds the snapshot protocol (`protocol=1`) and
dimension (`minecraft:overworld`) and constrains the snapshot directory to the
lab's `mc-agent-server/snapshots` root.

`real-url-run` is the acceptance run for this issue: it starts from an empty map
cache and downloads the ZIP over HTTPS from the URL pinned in
`map-manifest.json` (HTTP 200, 748,712 bytes, SHA-256 match), imports the world,
starts a fresh lab with the interface mod, initializes, and validates `READY` –
all in one sequence. `records/cold-download/` holds the download-only record
from the same URL.

The artifact is byte-for-byte reproducible: exporting the unchanged source save
twice gives the same ZIP (`46954828…`) and world hash (`b9d7c2cd…`) —
`export-reproducibility.json` records both runs, the source tree hash and the
match with the committed artifact — and the evidence pack proves it again by
exporting the imported world twice (`rebuild_check.identical: true`). Provisioning deploys the pinned Fabric API,
Carpet and mc-agent-interface jars and fails closed if the deployed hashes do
not match `map-manifest.json`.

A custom-program run is recorded separately in `records-challenge/run-01`: a
sealed 4-cart challenge (seed 20260926, program SHA-256 `9f97d515…`) was
initialized and validated end to end (`READY`, cross-check true), which proves
the runner validates a run against its own program instead of the public
calibration default. The challenge program is input, not the answer; the pop
order was not produced by that run.

The full records are in `records/run-01` … `records/run-03` and
`records/real-url-run`: `import.out.json` (download + import + world hash),
`init-record.json`, `init-commands.jsonl`, `ready-snapshot.json` (full NBT,
`rcon_order` and authoritative `tick_order` + mod cross-check) and
`validate.json` / `validate.out.json`.

## Negative evidence

Every readiness invariant fails closed. Each case starts from a fresh copy and
a validated ready state, then modifies it:

| Case | Result | Record |
| --- | --- | --- |
| machine broken after ready (one slime block removed) | `FIXTURE_INVALID`, `machine-state` | `negative/machine-broken/` |
| note value changed after ready (`setblock` note=7) | `FIXTURE_INVALID`, `machine-note-changed` (20 → 7) | `negative/note-changed/` |
| world unfrozen after ready (`tick unfreeze`) | `FIXTURE_INVALID`, `world-not-frozen` | `negative/unfrozen/` |
| inventory/NBT tampered after ready (`data merge` changes an item) | `FIXTURE_INVALID`, `cart-nbt-changed` + `normalized-state-changed` | `negative/nbt-tampered/` |
| tick order / full order hash changed (seat replaced) | `FIXTURE_INVALID`, `interface-order-hash-changed`, `seat-unexpected`, `user-vehicle-changed` (+ `user-pos-changed`) | `negative/tick-order/` |
| premature output (a cart pressed and launched before hand-off) | `PREMATURE_OUTPUT`, plus `machine-state`/`machine-note-changed` (the press advanced 20 → 21)/`cart-nbt-changed`/`premature-motion` | `negative/premature-output/` |
| server restart after ready | `FIXTURE_INVALID:RELOAD`, `server-restarted`, `seat-count`, `tick-order-unavailable` | `negative/reload/` |
| dirty machine before init | init aborts: `machine is not in its calibrated base state`, exact broken positions listed | `negative/init-failure/` |
| wrong artifact SHA-256, corrupt/absolute/zip-slip/symlink archives | explicit failures, nothing written outside the target | `runner/selftest.py` (offline, 75 checks) |

## Exact commands behind the evidence

```powershell
# three fresh runs (run-01 shown; runs 02/03 identical)
python examples/minecart-rom/runner/minecart_rom.py import --lab rom15d --reset `
    --rcon-port 27156 --server-port 27157 --vantage-port 27158 --bridge-port 27159 `
    --interface-mod labs/_cache/mods/mc-agent-interface-0.6.0.jar `
    --java $JAVA --memory 3G --cold
python examples/minecart-rom/runner/minecart_rom.py start --lab rom15d
python examples/minecart-rom/runner/minecart_rom.py init --lab rom15d `
    --records examples/minecart-rom/calibration/records/run-01
python examples/minecart-rom/runner/minecart_rom.py validate --lab rom15d `
    --ready examples/minecart-rom/calibration/records/run-01/ready-snapshot.json
python examples/minecart-rom/runner/minecart_rom.py stop --lab rom15d
```

Machine-cycle measurement inside an initialized, ready world:

```text
lab_server.py exec --name rom15d "tick query"                 # frozen
lab_server.py exec --name rom15d "player Romuser use"          # the input
lab_server.py exec --name rom15d "tick sprint 1"               # cycle tick 1..4
lab_server.py exec --name rom15d "execute as @e[type=minecraft:chest_minecart] run data get entity @s Pos"
lab_server.py exec --name rom15d "execute if block 14 -52 -22 minecraft:powered_rail run seed"
```

## Stage-one gate slice

The records above were turned into the gate's `fixture_map` evidence with:

```powershell
python examples/minecart-rom/runner/minecart_rom.py evidence `
    --records examples/minecart-rom/calibration/records `
    --out labs/stage1-evidence `
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
```

Result on 2026-09-26 against `tools/stage1_gate.py` (the merged #14 gate), with
the four initialization runs bound to declared child runs and the world copies
declared as instances:

```text
[PASS] fixture_map  (Map fixture pinned and initialized deterministically)
[PASS] evidence_integrity  (Bundle structure, artifact hashes, ports and source world)
```

Every `init-runs.jsonl` row carries `order_source: interface-snapshot` and the
normalized tick-order hash; `order_hash` is therefore the real tick order, not
a renamed selector list.

The full bundle still reports the other six checks as `blocked` because their
prerequisites (bridge#6, #16, #17, #18, #19) have not contributed their own
artifacts yet; that is the integration gate's job, not this fixture's. The
generated slice is committed under `gate/` and can be rebuilt from the records
at any time. The slice now derives both evidence booleans: `map.immutable` is
true because the URL carries a 40-hex commit pin (`url_pin: commit`), and
`rebuild_reproducible` is true only after two byte-identical exports of the
imported world.

The source save re-hashed to the gate's own read-only baseline
`8cd54c86…5324a` (40 files, 11,556,310 bytes) both before and after the export,
which is what backs `source_world_untouched: true`.

## Limitations

* The machine parameters above are calibrated for the shipped stack position and
  the shipped calibration program. A different cart count or stack placement
  needs a fresh calibration before its pop order is trusted.
* Wall-clock void windows were measured while polling over RCON, which lowers the
  effective tick rate; treat 5.7–8.0 s as the practical observation window and
  drive experiments with `tick sprint` when tick accuracy matters.
* The source save's player vantage was 6.8 blocks from the note block (outside
  interaction range), so `user.spawn` in the spec is the operating position and
  the source vantage is recorded only as discovery context.
* The public calibration program is not the grading challenge: evaluation runs
  should generate a sealed `challenge` program and seal the observed pop order
  separately.
* **Stage-two operator note:** this calibration tree contains the calibration
  program and its observed pop order. Before a cold-start run, initialize with
  a fresh `--program` challenge and keep `examples/minecart-rom/calibration/**`
  out of the agent-visible workspace/context.
