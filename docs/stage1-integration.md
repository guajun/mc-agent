# Stage-one integration run

This page documents the reproducible **combined** stage-one integration: two
real Fabric labs, the self-built smoke mod, the committed #18 audit mod, a real
tool trajectory, and the gate verdict. It is the bridge between the individual
prerequisites and the stage-two cold start, and it deliberately stops at the
gate's `blocked` line: the ROM map fixture (#15) and the restored-copy evidence
(bridge#6) do not exist yet.

!!! warning "No ROM solution"
    The scene is a single generic note block pushing one stack of chest
    minecarts into the void. It is instrumentation plumbing, not the ROM and
    not an agent run. No agent logger is written here, and no answer is fed to
    a future cold start.

## What ran

On 2026-09-26, from this worktree (gate repo head `32aa245`), ports
**27240-27249**:

| Step | Result |
| --- | --- |
| two labs provisioned (`rom13-src` source_audit 27240-27243, `rom13-exp` experiment 27244-27247), interface mod `0.6.0` (sha256 `45f12e16…`, commit `3b93ceb`) deployed | `lab_server.py identity`/`verify --require-vantage` pass on both |
| `examples/smoke-mod` built with `build_mod.py` and deployed as a required test mod | loaded on both labs; `mcagent-smoke status`/`sample` readable |
| stop/restart cycle on both labs | same `worldDir` before and after; instances stay distinct |
| same-size jar update: one-character marker change, same file name, re-deploy, restart | 15177 bytes both, sha256 changes, restarted server logs the **v2** marker |
| loud-failure probes: broken build, broken mod entrypoint, missing Fabric dependency | all three observed and recorded |
| committed #18 audit mod (`502f561`) copied read-only, built, run on the generic scene | real 303-event JSONL, note cycled, cart removed |
| tool trajectory recorded by `run_trace.py` for every operator command | 131 calls, all with results, category coverage terminal/file/source/mcp |
| source save hashed read-only before and after | unchanged (`8cd54c86…`) |

Exact commands (all paths under `labs/rom13-integration/`):

```bash
python tools/stage1_integration.py plan
python tools/stage1_integration.py run                 # full live run, ~4 min warm
python tools/stage1_integration.py run --bundle-only   # re-normalize without labs
```

The driver writes `summary.json`, `summary.md`, the lab dirs, the trajectory,
the run bundle and `assemble-report.json`. A compact, durable copy of the facts
lives in [`docs/evidence/rom13-stage1/integration-summary.json`](evidence/rom13-stage1/integration-summary.json).

## Lossless evidence mapping

`tools/stage1_evidence.py` projects the peer formats into the gate schema
without inventing facts. Every canonical record keeps the complete raw record
under `detail.raw` plus the source file sha256, and a mapping report records
counts, exclusions and gaps.

### #18 audit JSONL -> gate audit events

The real #18 schema (validated against `tools/minecart_audit.py` at commit
`502f561`) uses ``seq``/``tick``/``run``/``inst``/``session``/``phase``/``type``
and per-type fields. The adapter maps:

| #18 | Gate |
| --- | --- |
| `seq` + `session` | `event_id` = `"<session>:<seq>"`, `seq` (checked strictly increasing per identity) |
| `run` / `inst` | `run_id` / `instance_id`, checked against the declared run and instances |
| config dimension (or a checked event field) | `dimension` |
| `phase` | `experiment -> agent`, `restore -> restore`, everything else `init`; the raw phase is kept |
| `input_attempt` / `input_processed` | same event names; `operator` becomes `actor_uuid` (an object `{uuid, name}` is read for its `uuid`); a null actor is allowed only with a recorded `actor_provenance` |
| `input_request` | kept verbatim (not a gate operation) |
| `cart_exit` | `cart_emitted`; `captured_before_removal` is derived from a later `cart_remove` that carries an ordered inventory |
| `cart_remove` | `cart_removed`; `reason` becomes `removal_reason`, `pos` is projected |
| `cart_tracked`, `cart_sample`, inventory/teleport/reload events | kept verbatim, so the audit can still read them |
| `session_start`, `audit_ready`, `audit_end`, `audit_incomplete` | **excluded from the canonical tick stream** (they carry startup/shutdown ticks such as 0) and counted in the mapping report; the raw file stays hashed and complete |

The live run: **303 raw events, 300 projected, 0 gaps, 3 session-meta excluded**
(2 attempts, 1 processing, 1 emission, 1 removal, 289 samples).

### #19 trajectory.jsonl -> gate tool trace

`call`/`result` records are paired by `call_id`; a call without a result is a
gap, never a silent row. `arguments`, `result`, `error`, timestamps and the
recorded instance are carried over; the full records are preserved under
`detail`. `trace-join.json` is written only when there is at least one join,
and its `unmatched_*` counts are **computed from the mapped artifacts** - not
copied from a declaration.

The driver joins the RCON calls it actually issued to the audit events they
caused or observed. In the live run: 131 calls, 136 joins,
`unmatched_agent_events = 0`, `unmatched_tool_calls = 129` (the non-game build,
file, git and probe calls). The gate verifies each join on the full
run/instance/dimension identity and tick.

### #16 live identity -> gate player context

`tools/stage1_evidence.py identity` extracts the five gate cases from the
verified `docs/evidence/rom13-meta16/live-identity.json`:

| Gate case | Source (raw probe step) |
| --- | --- |
| `task_bind` | `task_entry.user.uuid` + `raw.task-start.alice` |
| `hit` | `raw.player.alice.uuid` (`found: true`, `matchedBy: uuid`) |
| `miss` | `raw.player.bob.miss` (identity found; the *view* ray was aimed off-target) |
| `two_players` | Alice + Bob responses, `other_uuid` = Bob |
| `unknown_identity` | `raw.player.unknown.uuid` (`found: false`, error recorded verbatim) |

It also carries the entry contract (`external-cli`, `native_chat_verified:
false` with the `timing: broadcast` note) and the interface/bridge commits the
evidence itself recorded.

## Gate result

`python tools/stage1_evidence.py assemble --bundle labs/rom13-integration/bundle --spec … --source-world "<save>"`

| Check | Verdict | Why |
| --- | --- | --- |
| `fixture_map` | blocked | #15 map manifest/init runs not landed |
| `restore_fidelity` | blocked | bridge#6 snapshot/restore evidence not landed |
| `player_context` | **pass** | real #16 identity, all five cases, pins |
| `agent_dev_capability` | blocked | cold-start harness `tool_environment` and the fixture-specific `smoke_mod` are stage-two evidence |
| `independent_test_mod` | blocked | test-mod manifest, negative cases and audit lifecycle still required |
| `trace_persistence` | blocked | missing-log detection evidence and the agent-phase run are still required |
| `smoke_fixture_validity` | blocked | smoke suites (offline/player/restore), calibration and fixture version lock need #15/#19 |
| `evidence_integrity` | blocked | downstream of the missing artifacts |

Overall: **blocked** (exit 3), 1 pass, 0 fail. The driver summary reports no
gaps: every fact it does provide was observed live, and every fact it cannot
yet provide is withheld rather than asserted - which is exactly why the gate
stays blocked instead of false-passing.

## Remaining dependencies (exact)

1. **#15 fixture**: `fixture-manifest.json` (immutable map URL/sha256), three
   `init-runs.jsonl` samples with declared child-run provenance, the fixture
   player identity, the command-block scan covering the fixture and both lab
   worlds, and the cleanup/rebuild record.
2. **bridge#6 restore**: `snapshot-before/` and `snapshot-after/` protocol
   trees, the bound `restore-record.json` (declared source/experiment
   instances), `source-unchanged.json` and the six failure cases.
3. **#18/#19 test-mod completeness**: `test-mod-manifest.json`,
   `negative-cases.jsonl` (no interaction, wrong position, marker only, answer
   only) and `audit-lifecycle.json`; `missing-log-detection.jsonl` from #19.
4. **Cold start (#19/#20)**: the harness `tool-environment.json`, the five
   smoke suites (`smoke_offline`, `lab_boot`, `fake_player_mcp`,
   `snapshot_restore`, `test_mod_load`), fixture calibration,
   `version-lock.json` and the agent's own tool-to-game joins.
5. Re-run `stage1_integration.py run` (or the mapping commands) once those
   artifacts exist; the gate then decides. Only a full `pass` authorizes
   stage two.

## Limitations

* The scene is generic instrumentation; it does not exercise the ROM machine
  and is not acceptance for #14.
* Joins in this run are test-side (RCON calls); the stage-two run must join an
  agent's own calls, and the gate enforces that distinction.
* The #18 audit mod was built from the committed PR #26 head `502f561`; if that
  PR changes before merge, the adapter mapping must be re-verified against the
  new revision.
* Snapshot `meta.json` cannot prove which live instance produced it, so the
  restore check binds endpoint identity through the restore record and the
  audit/trace provenance (see [the gate limitations](stage1-gate.md#limits)).

See also: [stage-one gate](stage1-gate.md), [cold-start protocol](coldstart-protocol.md),
[mod building](mod-building.md), [lab servers](lab-server.md).
