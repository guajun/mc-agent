# Stage-one integration gate

[Issue #14](https://github.com/guajun/mc-agent/issues/14) is the readiness gate
between the Minecart ROM prerequisites (#15, bridge#6, #16, #17, #18, #19) and
the stage-two cold start. A unit test passing in one repository, or a tool
returning `issued`, is **not** evidence that the combined live pipeline works.
This gate consumes the live-game evidence produced by those prerequisites,
recomputes everything it can, and refuses to pass while anything is missing.

The gate is implemented by `tools/stage1_gate.py` (stdlib only). It never
launches a game and it never fabricates evidence: it reads a *gate bundle* - a
directory with `bundle.json` and the raw artifacts - and fails closed.

```text
missing bundle / check / artifact, or scaffold origin   -> blocked
artifact present but invalid, inconsistent, incomplete  -> fail
every check passes                                      -> pass
```

Exit codes: `0` pass, `1` fail, `3` blocked, `2` usage. A script cannot mistake
"prerequisites are not ready" for success.

## Status (2026-09-26, this branch)

* Gate tooling, schema and offline selftest are implemented; `selftest` passes
  **85 checks** (no game, no live evidence involved).
* **No live prerequisite evidence exists yet**, so `check` on any real bundle is
  `blocked`. This document deliberately does not claim a gate pass.
* Independent baseline recorded while the gate was developed: the read-only hash
  of the source save `D:\MC\MC_Game\.minecraft\versions\26.2-Fabric\saves\Minecart ROM test`
  is `8cd54c86af9fa8d6b9ea33441fb21dac295cd2b5ddaa60327f5fb3a30255324a`
  (40 files, 11 556 310 bytes) with the canonical exclusion list below. No file
  was written; the save is only ever opened for reading.
* Ports reserved for the integration run: **27240-27249** (two instances,
  RCON + bridge each, inside that range).

## Commands

```bash
python tools/stage1_gate.py scaffold labs/stage1-evidence      # canonical empty layout
python tools/stage1_gate.py list                                # checks + required artifacts
python tools/stage1_gate.py list --json                         # machine-readable catalog
python tools/stage1_gate.py check labs/stage1-evidence \
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test" \
    --report stage1-report.json
python tools/stage1_gate.py hash-tree "<world dir>"             # deterministic read-only tree hash
python tools/stage1_gate.py selftest                            # gate logic, no game needed
```

`check` options:

| Option | Meaning |
| --- | --- |
| `--report PATH` | write the full JSON report |
| `--json` | print the JSON report instead of text |
| `--verbose` | print every assertion, not only problems |
| `--source-world PATH` | re-hash this directory read-only and compare it with `run.source_world` |
| `--skip-source-rehash` | skip the independent hash; records a limitation, never for final acceptance |
| `--port-range LOW-HIGH` | override the allowed integration port ranges (repeatable) |

## Bundle layout

```text
stage1-evidence/
  bundle.json
  artifacts/
    fixture_map/               fixture-manifest.json, init-runs.jsonl,
                               player-identity.json, command-block-scan.json,
                               cleanup-rebuild.json, raw/...
    restore_fidelity/          snapshot-before/ (protocol snapshot tree),
                               snapshot-after/, restore-record.json,
                               source-unchanged.json, failure-cases.jsonl
    player_context/            identity-records.jsonl, entry-contract.json,
                               version-pins.json
    agent_dev_capability/      tool-environment.json, jar-update.json,
                               smoke-mod.json, instance-isolation.jsonl
    independent_test_mod/      test-mod-manifest.json, audit-events.jsonl,
                               negative-cases.jsonl, audit-lifecycle.json
    trace_persistence/         tool-trace.jsonl, trace-join.json,
                               missing-log-detection.jsonl
    smoke_fixture_validity/    smoke-report.json, fixture-validity.json,
                               version-lock.json, evidence-index.json
```

`bundle.json` is the manifest. `checks.<id>.evidence.<kind>` names the path of
each artifact relative to the bundle root; paths are POSIX (`/`), must stay
inside the bundle and may not use `..`.

```json
{
  "schema_version": 1,
  "kind": "mc-agent.stage1.gate-bundle",
  "origin": "live",
  "generated_at": "2026-09-26T12:00:00Z",
  "run": {
    "run_id": "rom13-stage1-20260926",
    "issue": "guajun/mc-agent#14",
    "tool_category_map": { "terminal": ["bash"], "file": ["write"], "source": ["git"], "mcp": ["mcp"] },
    "source_world": {
      "label": "Minecart ROM test",
      "path": "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test",
      "before_tree_sha256": "<64 hex>",
      "after_tree_sha256": "<64 hex>"
    },
    "allowed_port_ranges": ["27240-27249"],
    "instances": [
      { "instance_id": "src-audit", "role": "source_audit",
        "world_dir": "labs/rom13-src/world", "rcon_port": 27240, "bridge_port": 27241 },
      { "instance_id": "exp-1", "role": "experiment",
        "world_dir": "labs/rom13-exp/world", "rcon_port": 27242, "bridge_port": 27243 }
    ]
  },
  "checks": {
    "fixture_map": {
      "evidence": {
        "fixture_manifest": "artifacts/fixture_map/fixture-manifest.json",
        "init_runs": "artifacts/fixture_map/init-runs.jsonl",
        "player_identity": "artifacts/fixture_map/player-identity.json",
        "command_block_scan": "artifacts/fixture_map/command-block-scan.json",
        "cleanup_rebuild": "artifacts/fixture_map/cleanup-rebuild.json"
      }
    }
  }
}
```

`origin` must be `live` for a pass. `scaffold` (and anything else) is blocked no
matter how complete the rest looks.

## Checks and evidence schema

`python tools/stage1_gate.py list` prints the same catalog with the canonical
paths. Types below: `s` string, `h64` 64 lowercase hex, `h40` 40 lowercase hex
(a commit), `h16` 16 lowercase hex (the protocol `orderHash`), `b` boolean,
`i` integer, `n` finite number, `v3` `[x, y, z]`, `t` ISO-8601 timestamp.

### 1. `fixture_map` - map fixture pinned and initialized deterministically (#15)

`fixture-manifest.json`

| Field | Type | Requirement |
| --- | --- | --- |
| `map.url` | s | immutable/versioned download entry |
| `map.sha256` | h64 | hash of the map artifact |
| `map.bytes` | i | > 0 |
| `map.mc_version` | s | e.g. `26.2` |
| `map.immutable` | b | true |
| `map.download_verified` | b | fresh-cache download verified |
| `map.bad_hash_rejected` | b | a wrong hash is rejected, not accepted |
| `mods[]` | list | >= 1 entry, each `{name, version, sha256(h64)}` |
| `world.directory` | s | copy name, never the source save itself |
| `world.tree_sha256` | h64 | tree hash of the prepared world copy |
| `world.files` | i | > 0 |

`init-runs.jsonl` - one record per independent initialization, **at least 3**:

| Field | Type | Requirement |
| --- | --- | --- |
| `init_id` | s | unique per run |
| `run_id` | s | |
| `state_hash` | h64 | equal across all initializations |
| `order_hash` | h16 | equal across all initializations |
| `entity_count` | i | > 0, equal across runs |
| `inventory_total` | i | > 0, equal across runs |
| `early_output` | b | false |
| `ready` | b | true |
| `tick` | i | >= 0 |

`player-identity.json`: `uuid`, `name`, `dimension`, `pos` (v3), `yaw`, `pitch`
(n), `source` (s, e.g. `carpet`), `facing_target` (b true),
`server_vantage_uuid` (s, must equal `uuid`).

`command-block-scan.json`: `method` (s), `world_dirs` (non-empty list),
`command_blocks` (i, must be 0), `scanned` (b true), `placed_by_init` (b false).

`cleanup-rebuild.json`: `steps` (non-empty list), `source_world_untouched`
(b true), `rebuild_reproducible` (b true).

### 2. `restore_fidelity` - restore is verified, not just issued (bridge#6)

| Artifact | Contents |
| --- | --- |
| `snapshot-before/` | protocol snapshot tree (`meta.json` + `entities.jsonl`), same format `tools/fork_verify.py` reads |
| `snapshot-after/` | snapshot taken after the guarded restore, before any further ticking |
| `restore-record.json` | endpoint/command outcome |
| `source-unchanged.json` | before/after tree hash of the source save |
| `failure-cases.jsonl` | one record per injected failure case |

The gate loads both snapshots with `fork_verify`, validates them, and compares
them itself: `orderHash`, per-type counts, and - for every UUID in order - the
full `nbt` string, `pos` and `vel` (tolerance `1e-6`). A passing hash with a
mutated inventory therefore fails.

`restore-record.json` fields: `endpoint.source`, `endpoint.target` (s),
`endpoint.target_resolved` (true), `endpoint.wrong_target_rejected` (true),
`dimension` (s), `chunks_loaded` (true), `tick_controlled` (true),
`duplicates_pre_existing` (= 0), `commands_issued` (i >= 1),
`commands_failed` (= 0), `partial_failure` (false), `pause_state_preserved`
(true), `issued_is_not_success` (true - the record must say issued != restored).

`source-unchanged.json`: `before_tree_sha256` (h64), `after_tree_sha256` (h64,
equal), `unchanged` (true), `hash_tool` (s), `exclusions` (list; the gate's
re-hash uses its canonical list, see below).

`failure-cases.jsonl` must cover all six cases - `summon_refusal`,
`duplicate_pre_existing`, `wrong_endpoint`, `inventory_mutation_order_hash`,
`corrupt_metadata`, `partial_failure` - each `{case, injected, expected,
observed, passed: true}`.

### 3. `player_context` - real fake player identity and server vantage (#16)

`identity-records.jsonl` must cover `task_bind`, `hit`, `miss`, `two_players`,
`unknown_identity`. Common fields: `case`, `uuid`, `viewed_uuid`, `dimension`,
`pos` (v3), `yaw`, `pitch`, `task_entry`, `channel` (`mcp` or `cli` - the
server-vantage path actually used), `accepted` (b). `task_bind`, `hit`,
`miss` and `two_players` must all view the task `uuid` (no crosstalk);
`two_players` also needs `other_uuid`; `unknown_identity` must be
`accepted: false` with a non-empty `rejected_reason`.

`entry-contract.json`: `mode` (`external_task` or `manual_external`), `fields`
(non-empty list), `native_chat_verified` (b - `false` is allowed and recorded),
`unsupported_entries` (list).

`version-pins.json`: `interface_mod` and `bridge` objects with `repo`, `commit`
(h40) and `tested: true`.

### 4. `agent_dev_capability` - build and install into a located instance (#17)

`tool-environment.json`: `harness`, `model` (s), `docs_visible` (non-empty
list), and `tools` with all of `terminal`, `file`, `filesystem_write`,
`source_access`, `build`, `install`, `mcp_or_cli`, `lab_manage` set to `true`.

`jar-update.json`: `case: same_size_different_content`, `old_sha256`,
`new_sha256` (h64, different), `bytes` (i, same size), `loaded_sha256` (h64,
must equal `new_sha256`), `runtime_evidence` (s).

`smoke-mod.json`: `mod_id`, `version` (s), `built_sha256` and `deployed_sha256`
(h64, equal), `server_log_ref`, `sample_output_ref` (s),
`build_errors_detected`, `load_failure_detected`, `missing_dependency_detected`,
`memory_state_rebuilt_after_restart` (true), `restart_evidence_ref` (s),
`no_rom_logic` (true - every boolean here must be true).

`instance-isolation.jsonl`: >= 2 records with `instance_id`, `role`
(`source_audit`/`experiment`), `world_dir`, `rcon_port`, `bridge_port`,
`restarted` (true), `resolves_correct_world` (true), `conflicting_instance`
(false). Instance ids, world dirs and ports must be unique.

### 5. `independent_test_mod` - auditable machine input and transient output (#18)

`test-mod-manifest.json`: `mod_id`, `version` (s), `sha256` (h64),
`read_only` (true), `hook_overhead_ms` (n >= 0), `fixture_behavior_unchanged`
(true), `loaded_in` (contains `source_audit` and `experiment`),
`agent_mod_coexists` (true), `no_command_blocks` (true).

`audit-events.jsonl` - the canonical server-side event schema:

| Field | Type | Requirement |
| --- | --- | --- |
| `event_id` | s | unique |
| `run_id`, `instance_id`, `dimension` | s | |
| `tick` | i | >= 0; `(tick, seq)` must not go backwards per run+instance |
| `seq` | i | >= 0, monotonic within the tick |
| `event` | s | see below |
| `phase` | s | `init`, `agent` or `restore` |
| `actor_uuid`, `cart_uuid` | s | as applicable |
| `pos` | v3 | as applicable |
| `captured_before_removal` | b | required true on `cart_emitted` |
| `removal_reason` | s | required on `cart_removed` (e.g. `void`) |

Required events: `input_attempt`, `input_processed`, `cart_emitted`,
`cart_removed`. The gate requires at least one `input_processed` that follows an
`input_attempt`, every `cart_emitted` to follow a processed input, every emitted
cart to be persisted before removal, and every removal to carry a reason.
`init`/`restore` events never count as agent operations.

`negative-cases.jsonl` must cover `no_interaction`, `wrong_position`,
`marker_only`, `answer_only`, each with `attempted` (b), `processed` (= 0) and
`evidence_ref` (s). Events explicitly attributed to a negative case must never
be `input_processed`.

`audit-lifecycle.json`: `states` including `ready`, `init`, `experiment_start`,
`experiment_end`, `flush`; `missing_log_status` and `overflow_status` both
`error`; `per_instance_files` (true); `ring_buffer_reliance` (false).

### 6. `trace_persistence` - tools and game events join by run/instance (#19)

`tool-trace.jsonl` - one record per tool call: `call_id` (unique), `run_id`,
`instance_id`, `tool`, `args` (object), `result` (any, present), `error`
(string or null, present), `started_at`/`ended_at` (t, ordered). The trace must
cover the categories `terminal`, `file`, `source` and `mcp` (name patterns can
be overridden by `run.tool_category_map`).

`trace-join.json`: `joins[]` with `call_id` (must exist in the trace),
`audit_ref` (`instance_id`, `event_id`, `tick`) and `verified: true`, plus
`unmatched_tool_calls` and `unmatched_agent_events` (must be 0 - every
agent-side audit event needs a tool call).

`missing-log-detection.jsonl`: cases `trace_missing` and `audit_missing`, each
`{case, detected: true, exit_nonzero: true, message_ref}`. Missing logs must
fail loudly, never be silently ignored.

### 7. `smoke_fixture_validity` - generic smoke, calibration, locked versions (#14)

`smoke-report.json`: `suites[]` covering `smoke_offline`, `lab_boot`,
`fake_player_mcp`, `snapshot_restore`, `test_mod_load`, each
`{name, command, status: "pass", checks >= 1, log_ref}`.

`fixture-validity.json`: `input_semantics` (s), `stack_positions` (non-empty
list), `output_boundary` (non-empty object), `void_window_ticks` (i >= 1),
`end_condition` (s), `timeout_s` (i >= 1), `hook_overhead_ms` (n >= 0),
`with_mod_without_mod_consistent` (true).

`version-lock.json`: `components[]` that must include these names, pinned as:

| Component | Pin |
| --- | --- |
| `mc-agent`, `mc-agent-interface-mod`, `mc-agent-bridge` | `commit` (h40) |
| `minecraft`, `fabric-loader`, `jdk` | `version` (s, non-empty) |
| `carpet`, `test-mod`, `fixture-map` | `sha256` (h64) |

`evidence-index.json`: `tool` (s) and `entries[]` where each entry is
`{path, sha256, bytes}` for a file or `{path, tree_sha256, files, bytes}` for a
directory. It must pin **every declared artifact except itself**; mismatch,
duplicates, missing files or missing pins fail the gate. Extra raw evidence may
be listed too.

### 8. `evidence_integrity` - bundle, hashes, ports, source world (computed)

No artifact. The gate itself validates the schema version and kind, `origin`,
`run.issue`, run instances (unique ids/ports, both roles, ports inside the
allowed ranges, default `27240-27249`), declared evidence kinds and paths, the
evidence index against recomputed hashes, and `run.source_world` before/after
equality. With `--source-world` (or the path in the manifest) it re-hashes the
save read-only and compares the result.

## Tree hashing

`python tools/stage1_gate.py hash-tree <dir>` writes a deterministic digest:
sorted relative POSIX paths, each with size and per-file SHA-256, excluding
`session.lock`, `logs`, `*.log`, `.DS_Store`, `Thumbs.db`, `__pycache__`.
The same algorithm and exclusion list are used by the gate and by
`version_lock`/`fixture-manifest` world hashes; do not mix hash tools.

## Integration runbook (after the prerequisites merge)

1. Collect the raw artifacts from #15, bridge#6, #16-#19 into one bundle
   (`scaffold` prints the canonical layout). Keep raw logs; index them too.
2. Fill `bundle.json`: `origin: live`, run id, source-world path plus before and
   after tree hashes, instances on ports 27240-27249.
3. Generate `evidence-index.json` over every file and tree.
4. Run `check` **with** `--source-world` and `--report`; attach the report to
   issue #14. Any `blocked` or `fail` stops stage two.
5. The coordinator reviews the report and spot-checks the raw evidence before
   declaring the gate passed. The gate cannot substitute for that review.

## Limits

* The gate validates coordinator-provided evidence; it cannot prove the
  artifacts were not fabricated. Raw logs stay in the repository/attachment
  trail and must be spot-checked.
* Boolean facts such as "hook is read-only" are cross-checked against the
  canonical event log, not re-measured live by this tool.
* Peer-specific raw formats must be mapped losslessly into the schema above;
  that mapping is part of coordinator review.
* A gate pass is necessary but not sufficient for stage two: the issue's other
  acceptance points (manual review, correct answer evidence) are still required.

See also: [lab servers](lab-server.md), [fork verification](fork-verify.md),
[snapshot protocol](protocol-snapshot.md), [tools](tools.md).
