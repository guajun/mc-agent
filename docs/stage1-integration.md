# Stage-one integration run

This page documents the reproducible **combined** stage-one integration: two
real Fabric labs, the self-built smoke mod, the committed #18 audit mod, a
finalized tool trajectory, the merged bridge#6 restore evidence and the gate
verdict. It is the bridge between the individual prerequisites and the
stage-two cold start, and it deliberately stops at the gate's `blocked` line:
the ROM map fixture (#15) and the same-run restore artifacts do not exist yet.

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
| committed #18 audit mod (`502f561`) copied read-only, built, run on the generic scene | real JSONL, note cycled, cart captured and removed, complete `audit_end` |
| tool trajectory recorded by `run_trace.py` for every operator command, then **frozen** | 71 calls / 71 results / 71 unique ids; category coverage terminal/file/source/mcp |
| source save hashed read-only before and after | unchanged (`8cd54c86…`) |

The attempt is uniquely identified (`rom13-integration-run-20260925T174503Z`,
directory `labs/rom13-integration/run-20260925T174503Z/`); a re-run creates a
new run directory and never appends to an old one.

Exact commands (all paths under `labs/rom13-integration/`):

```bash
python tools/stage1_integration.py plan
python tools/stage1_integration.py run                 # full live run, ~4 min warm
python tools/stage1_integration.py run --bundle-only   # byte-reproducible re-normalize from normalize-inputs.json
```

The driver writes `summary.json`, `summary.md`, the lab dirs, the finalized
trajectory, `normalize-inputs.json`, the run bundle and `assemble-report.json`.
A compact, durable copy of the facts lives in
[`docs/evidence/rom13-stage1/integration-summary.json`](evidence/rom13-stage1/integration-summary.json).

## Immutable, finalized trace

The projection never reads a file that is still being appended to:

1. every traced step completes and every lab is stopped;
2. the trajectory is copied to `run-<stamp>/trajectory.final.jsonl` and
   `finalized.json`/`normalize-inputs.json` pin its sha256, byte size, call and
   result counts, and require `calls == uniqueCallIds` (a duplicate call id
   aborts finalization instead of being collapsed);
3. normalization uses only the frozen copy, with `--expect-sha256`, and runs
   untraced.

`--bundle-only` re-reads `normalize-inputs.json`, re-verifies every pinned
hash and reproduces `tool-trace.jsonl`, `audit-events.jsonl`, the mapping
reports and `bundle.json` byte for byte (two consecutive runs were compared).

## Lossless evidence mapping

`tools/stage1_evidence.py` projects the peer formats into the gate schema
without inventing facts: every canonical record keeps the raw record under
`detail.raw` plus the source sha256, and a mapping report records counts,
exclusions, lifecycle validation and gaps.

### #18 audit JSONL -> gate audit events

The real #18 schema (validated against `tools/minecart_audit.py` at commit
`502f561`) uses `seq`/`tick`/`run`/`inst`/`session`/`phase`/`type` and
per-type fields. The adapter:

| #18 | Gate |
| --- | --- |
| `seq` + `session` | `event_id = "<session>:<seq>"`; `(tick, seq)` strictly increasing per identity |
| `run` / `inst` | `run_id` / `instance_id`, checked against the declared run and instances |
| config dimension (or a checked event field) | `dimension` |
| `phase` | `experiment -> agent`, `restore -> restore`, everything else `init`; the raw phase is kept |
| `input_attempt` / `input_processed` | same names; `operator` becomes `actor_uuid` (object `{uuid, name}` read for `uuid`); a null actor is allowed only with a recorded `actor_provenance` |
| `input_processed` links | `requestSeq`/`attemptSeq`/`agentOp` are **promoted** into the canonical record and validated: a dangling reference, a reused request/attempt, or `agentOp` without an attempt is a gap |
| `input_request` | kept verbatim (not a gate operation) |
| `cart_exit` | `cart_emitted`; `captured_before_removal` requires the removal's recorded `capturedPath` to say `before_drop` (and an ordered inventory) |
| `cart_remove` | `cart_removed`; `capturedPath` is required, `reason` becomes `removal_reason` |
| `cart_tracked`/`cart_sample`/inventory/teleport/reload | kept verbatim; `epoch` is required for cart identities (no silent `epoch=1` coercion) |
| `session_start`/`audit_ready`/`audit_end`/`audit_incomplete` | validated and counted, not silently dropped: an open session, a non-complete `audit_end`, `truncated`, or an `audit_incomplete` marker becomes a gap and withholds the artifact |

The live run: 296 projected events, 0 gaps, complete lifecycle
(`session_start` -> `audit_end status=complete`), 3 session-meta records
validated/excluded.

### #19 trajectory.jsonl -> gate tool trace

`call`/`result` records are paired by `call_id`; a call without a result is a
gap, a duplicate `call_id` is a gap (never last-wins), a missing `run_id`
mismatch is a gap, and a missing instance is a gap when several instances are
declared. The only defaulting allowed is a *single* declared instance, and the
row then records `detail.instance_source = "single-declared-instance-default"`.
The live run has 71 rows, all with explicit instances (18 `rom13-src`,
53 `rom13-exp`), zero defaulted.

### Joins: verified only with explicit proof

`trace-join.json` is written only for joins that (a) resolve to one call and
one audit event, (b) match on run/instance/dimension and tick, (c) concern an
agent-phase event, and (d) carry an explicit `proof` (`producer`, `basis`,
`clock`).  Anything weaker - including the driver's own test-side attribution
of RCON commands to the events they plausibly caused - stays in
`mapping/join-candidates.json` with `verified: false` and a basis note, the
gate artifact is withheld, and the gate blocks. A marker or a nearest-time
window is never an operation.

### #16 live identity -> gate player context

`tools/stage1_evidence.py identity` extracts the five gate cases from the
verified `docs/evidence/rom13-meta16/live-identity.json` (`task_bind`, `hit`,
`miss`, `two_players`, `unknown_identity`), plus the entry contract
(`external-cli`, `native_chat_verified: false` with the `timing: broadcast`
note) and the interface/bridge commits the evidence recorded.

## Independent bridge#6 restore collection

The merged bridge#6 evidence (`bridge6` worktree, head `4116ebb`, real run
`bridge6-src`/`bridge6-dst` on ports 27060-27065) is collected and verified by
`tools/stage1_evidence.py restore-evidence`:

* all nine index stage hashes re-checked;
* source snapshot `bridge6-fixture` vs destination verification snapshot
  `restore-check-bridge6-dst` compared with the gate's own rule: identical
  orderHash (`a663c5c0dfd7ec6f`), counts, NBT and pos/vel;
* `source-before` vs `source-after-restore` confirm the source lab state did
  not change;
* failure evidence present: wrong-endpoint rejection and duplicate-cart
  rejection, plus the inventory-mutation probe.

The collection copies only `meta.json`/`entities.jsonl` for the two snapshots
and pins their hashes. It is a **different live run** and is deliberately not
joined to the generic integration run: there is no shared tool/event clock
across the old labs, so the gate's same-run restore binding is not claimed.

## Gate result

`python tools/stage1_evidence.py assemble --bundle labs/rom13-integration/bundle --spec … --source-world "<save>"`

| Check | Verdict | Why |
| --- | --- | --- |
| `fixture_map` | blocked | #15 map manifest/init runs are not accepted yet |
| `restore_fidelity` | blocked | no same-run snapshot/restore artifacts for this run |
| `player_context` | **pass** | real #16 identity, all five cases, pins |
| `agent_dev_capability` | blocked | cold-start harness `tool_environment` and the fixture-specific `smoke_mod` are stage-two evidence |
| `independent_test_mod` | blocked | test-mod manifest, negative cases and audit lifecycle declaration still required |
| `trace_persistence` | blocked | no verified agent-phase joins and no missing-log-detection artifact yet |
| `smoke_fixture_validity` | blocked | smoke suites (offline/player/restore), calibration and fixture version lock need #15/#19 |
| `evidence_integrity` | blocked | downstream of the missing artifacts |

Overall: **blocked** (exit 3), 1 pass, 0 fail. Every fact the driver provides
was observed live; every fact it cannot yet provide is withheld rather than
asserted, which is why the gate blocks instead of false-passing.

## Remaining dependencies (exact)

1. **#15 fixture**: map manifest with immutable URL/sha256, three init runs with
   declared child-run provenance, the fixture player identity, the
   command-block scan covering the fixture and both lab worlds, and the
   cleanup/rebuild record. PR #27 candidates (`f2a636f`) exist but are still
   under review with open P1 fixes and are **not accepted** here.
2. **Same-run restore (`bridge#6`)**: snapshot-before/after trees, a bound
   restore record, `source-unchanged.json` and the six failure cases for the
   *gate run's* instances. The merged bridge6 evidence is collected separately
   (above); it cannot be re-labelled as this run's evidence.
3. **#18/#19 test-mod completeness**: `test-mod-manifest.json`,
   `negative-cases.jsonl` (no interaction, wrong position, marker only, answer
   only) and the `audit-lifecycle.json` declaration; `missing-log-detection.jsonl`.
4. **Cold start (#19/#20)**: harness `tool-environment.json`, the five smoke
   suites (`smoke_offline`, `lab_boot`, `fake_player_mcp`, `snapshot_restore`,
   `test_mod_load`), fixture calibration, `version-lock.json`, and the agent's
   own tool-to-game joins with explicit proof.
5. Re-run `stage1_integration.py run` (or `--bundle-only` after adding inputs)
   once those artifacts exist; the gate then decides. Only a full `pass`
   authorizes stage two.

## Limitations

* The scene is generic instrumentation; it does not exercise the ROM machine
  and is not acceptance for #14.
* The driver's RCON-to-event attribution is intentionally unverified
  (candidacy, not proof); the stage-two run must join the agent's own calls.
* The #18 adapter targets the committed PR #26 head `502f561`; if that PR
  changes before merge the mapping must be re-verified.
* Snapshot `meta.json` cannot prove which live instance produced it, so the
  restore check binds endpoint identity through the restore record and the
  audit/trace provenance (see [the gate limitations](stage1-gate.md#limits)).

See also: [stage-one gate](stage1-gate.md), [cold-start protocol](coldstart-protocol.md),
[mod building](mod-building.md), [lab servers](lab-server.md).
