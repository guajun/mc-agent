# ROM20 cold start - actual run evidence (issue #20)

**Status: packed for independent review. Not accepted, not merged, issue #20 not
closed.** `tools/run_audit.py` reports `transient_outputs_captured` **PASS** and
`agent_read_log` **PASS**; `machine_operated`, `logger_armed_before_activation`
and `answer_correct` are mechanically satisfied and **PENDING** on the three
required human semantic reviews. The task agent did not resolve those reviews or
claim acceptance.

* Run id: `rom20-20260926T063100Z`, created `2026-09-26T06:27:17.874Z`
* Task player: `3ec122d5-fc27-4816-be47-bf8be8d7e56d` (Carpet fake player `Romuser`)
* Executed at commit `1deb735ba0f0b2ff36533868fc5d91fab8546d09`
  (`codex/rom13-coldstart`, `dirty_entries: 0`). Every commit after that one is
  packaging-only and was **not** part of the executed run.
* Harness: pi 0.87.1, provider `deepseek`, model `deepseek/deepseek-flash`,
  thinking `max`. One fresh model session: 282 records / 157 tool calls,
  `06:32:17.838Z` - `06:45:16.354Z` (sha256 `46c2e824f46a72b2e68257d416107945e1c3818097b20ce83a1646b285a313b0`).
* Operator preparation took 6 attempts (coordinator record); the task itself
  ran once, with no task-time assistant intervention.

## Result: ordered items of every popped cart (pop order)

| # | popped cart UUID | ordered items (slot, item, count) |
| - | ---------------- | --------------------------------- |
| 1 | `5304524c-6dbf-472e-ad88-bedf9456d95c` | iron_ingot x5 (7), sand x1 (15), gold_ingot x3 (23) |
| 2 | `4431ae3d-f0f4-4f51-9d14-b9a915ec1531` | stone x4 (0), glass x4 (7), sand x2 (22), glass x3 (23) |
| 3 | `d494cb2c-bdb9-43fc-9e14-314c3a561008` | iron_ingot x1 (7) |
| 4 | `5f2f69ba-c4ff-4cfe-98b1-9f709c241119` | coal x3 (0), redstone x2 (13), stone x4 (23), gold_ingot x1 (26) |
| 5 | `1c23aa8e-81a4-404c-8942-a706fc777d93` | coal x2 (26) |

Machine-readable: `result/answer.json` (from the agent's own logger captures)
and `result/oracle.json` (independently derived from the test mod's
before-destruction `cart_remove` inventories). They agree cart-for-cart and in
order; the pop order equals the restored ticking order.

## What was actually executed

1. **Acquire without operating the source.** `mc-bridge call fork`
   (`freeze=false`) on `rom20-src` while its ready world was frozen: 6 entities,
   orderHash `545a11e87afce73b`, `players/` and `session.lock` skipped, entity
   storage stripped. The source lab was left frozen, phase `init`, unoperated.
2. **Load and clean.** The fork world was copied into `rom20-exp`; its region
   files still carried the source entities, so they were cleared in game
   (`kill @e[type=!minecraft:player]`, `save-all flush`, restart, chunk
   preload) before the guarded restore. This is the documented recipe; the
   first restore attempt before the cleanup failed and is preserved.
3. **Guarded restore + verify.** 6/6 entities summoned in recorded order;
   orderHash `545a11e87afce73b` match, counts match, UUID order match, 0
   NBT/position/velocity deltas (`verification/restore-evidence.json`). A
   follow-up comparison of the live copy against the operator ready snapshot
   matched **41/41** fields (carts, full NBT hashes, items, order, machine base
   state, note=20, hover seat, user pose).
4. **Own logger.** Independently written Fabric mod `rom20-agent-logger`
   (`logger/`), built with `tools/build_mod.py`, deployed while the lab was
   stopped, and armed at server start (`logger_armed`) before any operation. It
   captures each chest minecart one tick after it leaves the stack region, with
   the ordered inventory read while the cart still exists, plus a
   `container_remove` fallback. Raw output is separate from the evaluator's
   `mc-audit/`.
5. **Real operation.** The audit window was opened, the world unfrozen, and the
   note block at `11,-54,-23` was right-clicked 5 times with
   `player Romuser use once` (the `player.view` target was the note block).
   Each press produced exactly one `input_processed` (`agentOp=true`,
   `playNote`), one audit `cart_exit`, one agent capture, and one natural void
   `cart_remove(DISCARDED)`.
6. **Read then answer.** The agent read its own logger after the last capture
   (raw trajectory calls listed in `trajectory/trajectory-derivation.json`);
   the answer was generated from those captures and cross-checked against the
   test mod's independent inventories.

## Independent verification

* `tools/minecart_audit.py check` on the raw audit log: **overall pass** -
  5 calibrated agent operations, 0 uncalibrated player triggers, 0 environment
  triggers, 5 tracked carts, exits in occurrence order, complete lifecycle
  (`verification/minecart-audit-check.json`; raw log 1027 events, sha256
  `bdc736ca9ae734f64cbe189c215483f905496892a95fd9be344cde7058d4ba32`).
* Cold-start audit on the canonical trajectory: 2 PASS / 3 PENDING as above
  (`verification/audit.json`, `verification/audit.md`).
* Raw agent logger sha256 `054a3970381e3a0a47755b0fbec65a850e0367f028181172b86f4f99d32e0dc0`;
  deployed logger jar `e445f461ab0de331189ce609dac60d53f2ffb4529fc22b5dfb6c77d5a8593f89`
  (16778 bytes). The frozen logger source rebuilds to **byte-identical** bytes
  against the existing lab runtime (`logger/build-provenance.json`).
* Offline checks during packaging: `tools/tests` 89 tests OK;
  `minecart_rom.py selftest` 125 checks; `minecart_audit.py selftest` all cases;
  `mkdocs build` OK.

## Canonical trajectory (trace packaging)

The original run left 30 trajectory records: operator prep marks plus eight key
calls/results the agent appended with `run_trace.py`, but no full harness
import. The packaging therefore produces a **derived** canonical trajectory in
a separate directory (the original run snapshot is untouched):

* `trajectory/trajectory.canonical.jsonl` - 344 records: the original 30 plus
  the frozen session's 157 calls + 157 results, stably sorted by their actual
  `at` timestamps and renumbered `1..N`.
* `trajectory/trajectory-derivation.json` - raw session hash, original
  trajectory hash, merge strategy, per-manual-call links to the raw pi call
  that executed the action, related observations (the failed first dry-run and
  apply attempts, and the rebuild that produced the deployed jar), and the
  validation result.
* Every record keeps its original timestamp; imported records stay labelled
  `source: "import:pi"`; manually recorded calls are labelled
  `trajectory_origin: "original-run-agent-manual"` and carry `raw_pi_call_ids`.
  Nothing is relabelled as a harness call.
* `created_at` is the original run's `2026-09-26T06:27:17.874Z`; no fresh run
  was fabricated.
* The derived run validates (`run_trace.py validate`) and re-audits to the same
  2 PASS / 3 PENDING. With the full trace present, the audit's causal
  references now land on raw harness calls (e.g. the `operate.py` execution
  `call_00_ET_Z8Jf2dzy92rgwL9OifKQ1547` for `machine_operated`).

The canonical trajectory proves ordering and provenance, not causation; the
`machine_operated.causality` review exists precisely because a time-adjacent
call is not by itself causal proof.

## Projection fix (offline, documented)

`tools/make_evidence_fixed.py` replaces the originally executed
`tools/make_evidence-original.py`. The original projection appended
`instance_ready`/`phase`/`end` after the input/cart events and omitted
`input_request`; the fixed one emits all experiment-phase events in the raw
audit log's own session order and asserts monotonicity. The original outputs
and the raw logs are preserved in the freeze snapshot; answer, oracle and
normalized logger view are byte-identical (`8e42af94...`, `9b722a05...`,
`32c88150...`). Raw logs are never modified.

## Failures and limitations

See `failures.md`. In short: the first restore attempt failed (copied region
files still carried entities and the chunks were not loaded while frozen) and
the first `romuser` spawn fell into the void; both are retained with raw
evidence. The projection is deliberately experiment-phase scoped so the
pre-experiment cleanup kills of the *same UUIDs* do not poison the transient
capture window; the full raw log is preserved and hashed. The three semantic
reviews are unresolved by design. Raw large artifacts live in the git-ignored
`labs/rom20-20260926T063100Z/operator/post-task-freeze` snapshot and are pinned
by sha256 in `manifest.json`; no RCON password or environment credential is
committed.

## Package layout

```
README.md                        this report
manifest.json                    raw/derived hashes, execution, attempts, committed-file hashes
failures.md                      preserved failures with raw references
result/answer.json               the 5-cart answer (agent logger)
result/oracle.json               independent oracle (test mod)
result/testmod.experiment.jsonl  canonical projection (audit input)
result/agent-logger.norm.jsonl   normalized logger view (source_sha256-linked)
verification/                    verifier + run audit outputs, restore evidence
trajectory/                      canonical trajectory, derivation manifest, derived run/evidence manifests
logger/                          logger sources + sanitized build provenance
tools/                           executed and packaging scripts (original + fixed projection, derivation)
```

## Reproduction (offline)

```bash
# rebuild the deployed logger (byte-compare with e445f461...)
python tools/build_mod.py --source docs/evidence/rom20-coldstart/logger \
    --lab rom20-exp --out /tmp/rom20-agent-logger.jar --version 0.1.0

# regenerate the projection from the frozen raw audit log
python docs/evidence/rom20-coldstart/tools/make_evidence_fixed.py \
    --raw-audit  labs/rom20-20260926T063100Z/operator/post-task-freeze/testmod/audit-rom20-20260926T063100Z.jsonl \
    --raw-logger labs/rom20-20260926T063100Z/operator/post-task-freeze/agent-logger-raw/romlog.jsonl \
    --restore-apply  labs/rom20-20260926T063100Z/operator/post-task-freeze/workspace-as-agent-left/evidence/restore-apply.json \
    --restore-verify labs/rom20-20260926T063100Z/operator/post-task-freeze/workspace-as-agent-left/evidence/restore-verify.json \
    --out /tmp/rom20-evidence

# validate the derived run and re-run the cold-start audit
python tools/run_trace.py validate --run-dir labs/rom20-20260926T063100Z/operator/post-task-derived/run
python tools/run_audit.py  run      --run-dir labs/rom20-20260926T063100Z/operator/post-task-derived/run
```
