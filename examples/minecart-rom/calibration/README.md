# Fixture calibration evidence

Measured on 2026-09-26 on this machine:

| Item | Value |
| --- | --- |
| Minecraft | 26.2 (`DataVersion 4903`) |
| Fabric loader / installer | 0.19.5 / 1.1.2 |
| Java | Microsoft OpenJDK 25.0.1 (HMCL runtime `mojang-java-runtime-epsilon`) |
| Fabric API | `0.161.0+26.2` (`e5b858ce…e216a`) |
| Carpet | `26.2+v260616` (`f6ada912…1311c`) |
| Lab | `labs/rom15b`, RCON `27152`, game `27153`, heap 3G |
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
| reload behaviour | a graceful server restart preserves cart UUIDs, inventories, positions and the query order, but the fixture treats any restart as a reload and refuses the fixture | `logs/reload_test.log` |

## Reproducibility

Three independent runs, each on a fresh import of the artifact (`import --reset`),
each with new cart UUIDs. The normalized hash (spawn index + position + motion +
items, UUIDs excluded) was identical:

```
run-01  READY  hash 86215e40a0d3d9ba…  carts 3
run-02  READY  hash 86215e40a0d3d9ba…  carts 3
run-03  READY  hash 86215e40a0d3d9ba…  carts 3
```

The full records are in `records/run-01` … `records/run-03`: `download.json`/
`import.out.json`, `init-record.json`, `init-commands.jsonl`, `ready-snapshot.json`,
`validate.json`.

## Negative evidence

| Case | Result | Record |
| --- | --- | --- |
| premature output (a cart was pressed and launched before hand-off) | `PREMATURE_OUTPUT`, `premature-motion` + `normalized-state-changed` | `negative/premature-output/` |
| server restart after ready | `FIXTURE_INVALID:RELOAD`, `server-restarted` (pid change) + `user-missing` | `negative/reload/` |
| dirty machine (one slime block removed before init) | init aborts: `machine is not in its calibrated base state`, exact broken positions listed | `negative/init-failure/` |
| wrong artifact SHA-256, corrupt/absolute/zip-slip/symlink archives | explicit failures, nothing written outside the target | `runner/selftest.py` (offline) |

## Exact commands behind the evidence

```powershell
# three fresh runs (run-01 shown; runs 02/03 identical)
python examples/minecart-rom/runner/minecart_rom.py import --lab rom15b --reset `
    --rcon-port 27152 --server-port 27153 --java $JAVA --memory 3G --url $MIRROR
python examples/minecart-rom/runner/minecart_rom.py start --lab rom15b
python examples/minecart-rom/runner/minecart_rom.py init --lab rom15b `
    --records examples/minecart-rom/calibration/records/run-01
python examples/minecart-rom/runner/minecart_rom.py validate --lab rom15b `
    --ready examples/minecart-rom/calibration/records/run-01/ready-snapshot.json
python examples/minecart-rom/runner/minecart_rom.py stop --lab rom15b
```

`--url` pointed at a local mirror of the committed ZIP during the three runs so
the runs did not depend on GitHub being reachable; the bytes, size and SHA-256
are the ones in `map-manifest.json`, and a real-URL cold download is recorded
separately in `records/cold-download/`.

Machine-cycle measurement inside an initialized, ready world:

```text
lab_server.py exec --name rom15b "tick query"                 # frozen
lab_server.py exec --name rom15b "player Romuser use"          # the input
lab_server.py exec --name rom15b "tick sprint 1"               # cycle tick 1..4
lab_server.py exec --name rom15b "execute as @e[type=minecraft:chest_minecart] run data get entity @s Pos"
lab_server.py exec --name rom15b "execute if block 14 -52 -22 minecraft:powered_rail run seed"
```

## Limitations

* The machine parameters above are calibrated for the shipped stack position and
  the shipped program. A different cart count or stack placement needs a fresh
  calibration before its pop order is trusted.
* Wall-clock void windows were measured while polling over RCON, which lowers the
  effective tick rate; treat 5.7–8.0 s as the practical observation window and
  drive experiments with `tick sprint` when tick accuracy matters.
* The source save's player vantage was 6.8 blocks from the note block (outside
  interaction range), so `user.spawn` in the spec is the operating position and
  the source vantage is recorded only as discovery context.
