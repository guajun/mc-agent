# ROM20 cold start - actual run evidence (issue #20)

**Status: independently reviewed as a proven-successful cold start; run audit
5/5 PASS; awaiting final code review at the pushed head. Not merged, issue #20
not closed by this package.**

The independent review at PR #32 head `666d74004d2b41869fafbc83e05392cad7a150d9`
([review 5325010883](https://github.com/guajun/mc-agent/pull/32#pullrequestreview-5325010883),
2026-09-26T07:03:53Z) re-verified the frozen archive and the package and found
the run executed at `1deb735` a proven success. Its three required semantic
reviews were recorded from that review (with the reviewer identity, the formal
review URL and the supporting refs) in `verification/reviews.jsonl`; the
regenerated run audit is 5/5 PASS (`verification/audit.json`). The task-time
audit was PENDING and is preserved as
`verification/audit-task-time-pending.json|md`; the original task-time report
also remains in the frozen run snapshot.

* Run id: `rom20-20260926T063100Z`. Run manifest `created_at` is
  `2026-09-26T06:27:17.874Z`; the canonical trajectory header preserves the
  original trajectory header's `2026-09-26T06:27:17.978Z`. Both are from the
  same `run_trace init` (written ~100 ms apart) and both are preserved; no fresh
  run was created.
* Task player: `3ec122d5-fc27-4816-be47-bf8be8d7e56d` (Carpet fake player `Romuser`)
* Executed at commit `1deb735ba0f0b2ff36533868fc5d91fab8546d09`
  (`codex/rom13-coldstart`, `dirty_entries: 0`). **No commit after `1deb735`
  was part of the executed run**; later commits are packaging (this evidence
  package) and preparation-recipe fixes/tests (`eb2bedf`, `666d740`, ...).
* Harness: pi 0.87.1, provider `deepseek`, model `deepseek/deepseek-flash`,
  thinking `max`. One fresh model session: 282 records / 157 tool calls,
  `06:32:17.838Z` - `06:45:16.354Z` (sha256 `46c2e824f46a72b2e68257d416107945e1c3818097b20ce83a1646b285a313b0`).
* Operator preparation took 6 attempts (coordinator record); the task itself
  ran once, with no task-time assistant intervention.

## Context contract (clean-context acceptance)

Initial visibility was exactly four files - `TASK.md`, `ENV.md`, the toolkit
skill (`skills/minecraft-toolkit/SKILL.md`) and its reference
(`references/toolkit-operations.md`) - recorded in the run's `visibility.json`
with their hashes. No solution, calibration answer, prior trajectory or
pre-written logger was supplied, and there was no capability sandbox: full
filesystem, shell, build and network access. During normal research the agent
read repository documentation and the evaluator/audit sources (for example
`tools/coldstart.py`, the audit mod source and the run protocol); those reads
are part of the canonical trajectory.

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
   (the explicit read call is `call_00_ET_jBVnWPls1Aj90rbfN2EY2368` in the
   canonical trajectory; the audit's `agent_read_log` mechanical witness is the
   earlier combined command `call_00_hjdNlRHJlnwjYpsOpZRy1938` that recorded
   the operation result and tailed the logger in one shell call). The answer
   was generated only from those captures and cross-checked against the test
   mod's independent inventories.

## Evaluator feedback in the control loop (disclosed)

`tools/operate.py` paced the five presses by polling the evaluator audit log's
phase/exit/remove counters *and* the agent logger between presses; that is
visible in the canonical trajectory. The answer itself was generated only from
the agent logger's `left_stack_region` captures, and the oracle is the
independent test-mod log - no oracle inventory was used to produce the answer.

## Independent verification

* `tools/minecart_audit.py check` on the raw audit log: **overall pass** -
  5 calibrated agent operations, 0 uncalibrated player triggers, 0 environment
  triggers, 5 tracked carts, exits in occurrence order, complete lifecycle
  (`verification/minecart-audit-check.json`; raw log 1027 events, sha256
  `bdc736ca9ae734f64cbe189c215483f905496892a95fd9be344cde7058d4ba32`).
* Cold-start audit: **5/5 PASS** after the independently-reviewed resolutions
  were recorded (`verification/audit.json`, `verification/audit.md`,
  `verification/reviews.jsonl`). The task-time PENDING report is preserved
  separately (see status above).
* The mechanical audits witness `logger_armed_before_activation` with an early
  grep call (`call_01_WZij1nfMJFeTereaDDak0836`) that matches arm tokens, and
  `agent_read_log` with the combined echo/read command above. The resolved
  review records carry the stronger explicit chain instead:
  `trajectory:call_00_ET_A6387Us9qClvqubgFIqr5901` (the rebuild that produced
  the deployed jar) plus `logger:seq10`/`logger:seq11`, and
  `trajectory:call_00_ET_jBVnWPls1Aj90rbfN2EY2368` for the logger read. No core
  heuristic was changed; the review citation is the stronger evidence.
* Raw agent logger sha256 `054a3970381e3a0a47755b0fbec65a850e0367f028181172b86f4f99d32e0dc0`;
  deployed logger jar `e445f461ab0de331189ce609dac60d53f2ffb4529fc22b5dfb6c77d5a8593f89`
  (16778 bytes). The frozen logger source rebuilds to **byte-identical** bytes
  against the existing lab runtime (`logger/build-provenance.json`).
* Offline checks during this fix round: `tools/tests` **130 tests OK**;
  `minecart_rom.py selftest` 125 checks; `minecart_audit.py selftest` all
  cases; `mkdocs build` OK.

## Canonical trajectory (trace packaging)

The original run left 30 trajectory records: operator prep marks plus eight key
calls/results the agent appended with `run_trace.py`, but no full harness
import. The packaging therefore produces a **derived** canonical trajectory in
a separate directory (the original run snapshot is untouched):

* `trajectory/trajectory.canonical.jsonl` - 347 records: the original 30 plus
  the frozen session's 157 calls + 157 results, plus the three reviewer marks
  appended by `run_trace.py review`, stably sorted by their actual `at`
  timestamps and renumbered `1..N`.
* The frozen session was imported with `run_trace.py import-pi --max-text
  200000`, larger than the largest session result (23,574 chars); **no result is
  truncated** (`trajectory-derivation.json.import.truncated_result_count = 0`).
* `trajectory/trajectory-derivation.json` - raw session hash, original
  trajectory hash, merge strategy, import max-text/truncation facts,
  per-manual-call links to the raw pi call that executed the action, related
  observations (the failed first dry-run and apply attempts, and the rebuild
  that produced the deployed jar), and the validation result.
* Every record keeps its original timestamp; imported records stay labelled
  `source: "import:pi"`; manually recorded calls are labelled
  `trajectory_origin: "original-run-agent-manual"` and carry `raw_pi_call_ids`;
  the reviewer marks are labelled `external-review-resolution`. Nothing is
  relabelled as a harness call.
* The committed `trajectory/` directory is a review summary, not a standalone
  run directory (`task.md`, `visibility.json` and `artifacts/` are not
  committed). To validate it, reconstruct the derived run as shown under
  Reproduction.

The canonical trajectory proves ordering and provenance, not causation; the
`machine_operated.causality` review exists precisely because a time-adjacent
call is not by itself causal proof, and it was resolved with the reviewer's
evidence.

## Projection fix (offline, documented)

`tools/make_evidence_fixed.py` replaces the originally executed
`tools/make_evidence-original.py`. The original projection appended
`instance_ready`/`phase`/`end` after the input/cart events and omitted
`input_request`; the fixed one emits all experiment-phase events in the raw
audit log's own session order and asserts monotonicity. The synthetic
`instance_ready` now retains the raw `from`/`to`/`requested`/`actor`/`reason`
fields plus an explicit `derived_from` mapping to raw phase seq 872. The
original outputs and the raw logs are preserved in the freeze snapshot; answer,
oracle and normalized logger view are byte-identical (`8e42af94...`,
`9b722a05...`, `32c88150...`). Raw logs are never modified.

## Failures and limitations

See `failures.md`; the byte-exact failed-restore session result and the
host-local console excerpt are committed under `failures/`. In short: the first
restore attempt failed (copied region files still carried entities and the
chunks were not loaded while frozen) and the first `romuser` spawn fell into the
void; both are retained with raw evidence. The projection is deliberately
experiment-phase scoped so the pre-experiment cleanup kills of the *same UUIDs*
do not poison the transient capture window; the full raw log is preserved and
hashed. The three semantic reviews are resolved from the independent review,
not by the task agent. Raw large artifacts live in the git-ignored
`labs/rom20-20260926T063100Z/operator/post-task-freeze` snapshot and are pinned
by sha256 in `manifest.json`; the one host-local source used (the game console
log) is documented with its full path and hash there. No RCON password or
environment credential is committed.

`docs/evidence/rom20-coldstart/**` is marked `-text` in `.gitattributes`, so
committed bytes equal working-tree bytes exactly (LF JSON/JSONL, CRLF failure
excerpts) and manifest hashes verify against a plain checkout without
autocrlf-style conversion.

## Package layout

```
README.md                        this report
manifest.json                    raw/derived hashes, execution, attempts, external review, committed-file hashes
failures.md                      preserved failures with raw references
failures/                        byte-exact failed-restore result + host-local console excerpt + provenance
result/answer.json               the 5-cart answer (agent logger)
result/oracle.json               independent oracle (test mod)
result/testmod.experiment.jsonl  canonical projection (audit input)
result/agent-logger.norm.jsonl   normalized logger view (source_sha256-linked)
verification/                    verifier result, post-review audit (5/5 PASS), task-time PENDING audit, reviews.jsonl, restore evidence
trajectory/                      canonical trajectory, derivation manifest, derived run/evidence manifests
logger/                          logger sources + sanitized build provenance
tools/                           executed and packaging scripts (original + fixed projection, derivation, failure extraction)
```

## Reproduction (offline)

```bash
# rebuild the deployed logger (byte-compare with e445f461...)
python tools/build_mod.py --source docs/evidence/rom20-coldstart/logger \
    --lab rom20-exp --out /tmp/rom20-agent-logger.jar --version 0.1.0

# regenerate the projection and the derived evidence from the frozen raw logs
python docs/evidence/rom20-coldstart/tools/make_evidence_fixed.py \
    --raw-audit  labs/rom20-20260926T063100Z/operator/post-task-freeze/testmod/audit-rom20-20260926T063100Z.jsonl \
    --raw-logger labs/rom20-20260926T063100Z/operator/post-task-freeze/agent-logger-raw/romlog.jsonl \
    --restore-apply  labs/rom20-20260926T063100Z/operator/post-task-freeze/workspace-as-agent-left/evidence/restore-apply.json \
    --restore-verify labs/rom20-20260926T063100Z/operator/post-task-freeze/workspace-as-agent-left/evidence/restore-verify.json \
    --out /tmp/rom20-evidence

# reconstruct the derived run (canonical trajectory + review resolutions + 5/5 audit)
#   1. copy the frozen run, 2. import the session with --max-text 200000,
#   3. record the three reviews from verification/reviews.jsonl,
#   4. run tools/derive_trajectory.py, 5. fix_derived_evidence.py, 6. validate + run_audit
python tools/run_trace.py validate --run-dir labs/rom20-20260926T063100Z/operator/post-task-derived/run
python tools/run_audit.py  run      --run-dir labs/rom20-20260926T063100Z/operator/post-task-derived/run
```
