# Stage-one gate evidence (fixture_map slice)

This directory is generated from `../records/` by the fixture runner and shows
that the #15 fixture satisfies the merged integration gate
(`tools/stage1_gate.py`, issue #14). It is committed so the coordinator can see
the exact bytes without rerunning the lab.

Regenerate it with:

```powershell
python examples/minecart-rom/runner/minecart_rom.py evidence `
    --records examples/minecart-rom/calibration/records `
    --out labs/stage1-evidence `
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
```

The builder verifies rather than asserts: the cold-download record must hash to
the manifest, a deliberately wrong hash must be rejected, the world tree hash
uses the gate's own algorithm, `source_world_untouched` is true only when the
source save still hashes to the read-only baseline recorded in
`docs/stage1-gate.md` (`8cd54c86…5324a`, 40 files, 11,556,310 bytes), the URL
pin is classified (`url_pin: commit`), and `rebuild_reproducible` comes from two
byte-identical fresh exports.

## Result

Checked 2026-09-26 against `tools/stage1_gate.py` from the merged #14 gate, with
the four initialization runs bound to declared child runs and the world copies
declared as instances:

```text
stage-one gate (rom13-stage1): BLOCKED  bundle=labs/stage1-fixture-bundle
  [PASS] fixture_map  (Map fixture pinned and initialized deterministically)
  [PASS] evidence_integrity  (Bundle structure, artifact hashes, ports and source world)
  8 check(s): 2 pass, 0 fail, 6 blocked
```

The other six checks stay `blocked` because their prerequisites (bridge#6, #16,
#17, #18, #19) have not contributed artifacts to this bundle. The full output is
in `gate-check-output.txt`.

## What each file contains

| File | Content |
| --- | --- |
| `artifacts/fixture_map/fixture-manifest.json` | artifact URL/hash/size (`46954828…`, 748,712 bytes), game version, mod pins (including `carpet` and `mc-agent-interface-mod`), world tree hash; `immutable` derived from the 40-hex commit pin (`url_pin: commit`) |
| `artifacts/fixture_map/init-runs.jsonl` | four initialization runs: equal state hashes, equal 16-hex tick-order hashes with `order_source: interface-snapshot`, entity count 3, inventory total 6, `early_output=false`, `ready=true`, `tick=6000` |
| `artifacts/fixture_map/player-identity.json` | `Romuser` UUID/name/dimension/position/yaw/pitch, `source=carpet`, `facing_target=true`, server-vantage UUID equal to the task UUID |
| `artifacts/fixture_map/command-block-scan.json` | palette scan of the base world and the lab copy; `command_blocks=0`, `scanned=true`, `placed_by_init=false` |
| `artifacts/fixture_map/cleanup-rebuild.json` | ordered rebuild/cleanup steps; `source_world_untouched=true` (source matches the #14 baseline) and `rebuild_reproducible=true` derived from two byte-identical fresh exports (`rebuild_check` carries both hashes) |
| `bundle-fragment.json` | the `checks.fixture_map.evidence` mapping to merge into `bundle.json` |
| `evidence-summary.json` | one-line summary of the generated pack |

## Order evidence

The ready state is bound to a real `SNAPSHOT` of the interface mod's server
vantage, which reads `ServerLevel.entityTickList` (`mc-agent-interface` 0.6.0,
sha `45f12e16…`). Each `init-runs.jsonl` row carries:

* `order_hash` — 16-hex hash over the **tick order** normalized by spawn index,
  equal across runs;
* `order_source: interface-snapshot`;
* the raw `tick_order` and the RCON observation `rcon_order` in the full ready
  records under `../records/`.

The RCON `@e` selector order is never labelled as the tick order.

## Binding notes for the coordinator

* `init-runs.jsonl` rows carry `run_id` = the record directory and
  `instance_id` = the lab that produced the run (`rom15d`). Declare those in
  `run.child_runs`; the runner can also rewrite them at build time with
  `--run-map OLD=NEW`.
* `command-block-scan.world_dirs` must cover the fixture `world.directory` and
  every declared `run.instances[].world_dir`. Scan extra copies by passing
  `--world-dir` to the `evidence` command.
* `run.source_world.before_tree_sha256`/`after_tree_sha256` must both be the
  read-only baseline; the gate re-hashes the save itself when `check` is run
  with `--source-world`.
