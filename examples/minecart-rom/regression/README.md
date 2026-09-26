# Minecart ROM regression (issue #21)

This directory holds the frozen executables of the issue-#21 regression: the
parameterized agent logger, the two calibrated sealed cart programs and the
config-driven scene/operation parameters. The drivers live in
[`tools/rom21_regression.py`](../../../tools/rom21_regression.py) and
[`tools/rom21_verify.py`](../../../tools/rom21_verify.py); the reviewed,
committed results live in
[`docs/evidence/rom21-regression`](../../../docs/evidence/rom21-regression).

**Honesty boundary.** These are *regression* results. Every run starts a real
Minecraft server, initializes a fresh sealed challenge, forks and guarded-
restores a fresh instance, presses the note block with the real Carpet fake
player, captures the carts with the agent logger and judges the evidence with
the unchanged independent audit mod. No model is called: the regression proves
the *machinery and result assertions* of the original cold start are
reproducible, not that an autonomous model was re-tested. The original
autonomous model-capability evidence remains the #20 package.

## Layout

```
configs/calibrated-a.json      5-cart sealed program (seed 2101)
configs/calibrated-b.json      4-cart sealed program (seed 2102, different pool)
logger/                        the parameterized logger source + PROVENANCE.md
demo/                          demo that reuses the committed run artifacts
```

Each config pins the full sealed program and its `programSha256`, the scene
regions and exit plane, the expected cart count and the operation timing. The
programs are test-side inputs generated with
`minecart_rom.py challenge --seed ...`; they are intentionally committed here
because the regression never hands anything to an agent.

## Running

All commands need the repository venv Python, Java 25, Minecraft 26.2 Fabric
resources and network for the cold download; offline they skip/stop with a
clear error and never pretend to pass.

```powershell
# one live run (fresh source + fresh experiment instance, guarded restore, real presses)
python tools/rom21_regression.py run --config examples/minecart-rom/regression/configs/calibrated-a.json `
    --run-id rom21-20260926T080000Z-a1

# the authoritative suite: 2 calibrated configs, 3 fresh instances, 1 cold download,
# plus the same-size jar iteration/restart/re-restore run
python tools/rom21_regression.py suite --stamp 20260926T080000Z

# the declared negatives (live fault probes + offline evidence mutations)
python tools/rom21_regression.py negatives

# stage the reviewable evidence from a passing suite
python tools/rom21_regression.py package
```

Offline checks (no game, no network) run through the normal test discovery:

```powershell
python tools/rom21_regression.py selftest
python tools/rom21_verify.py selftest
python -m unittest discover -s tools/tests
```

The live suite exits non-zero on the first failing run and retains
`run.failed.json`, `steps.jsonl` and the raw files beside the partial run; a
run directory is never overwritten.

## What one run proves

1. a fresh sealed program is initialized on an unoperated source instance
   (`minecart_rom.py init`; test-side commands only);
2. the source is forked through the pinned clean bridge (`4116ebb`) and the
   experiment instance is built from the fork and restored through the guarded
   restore (dry run, apply, strict verify: order hash, UUID order, counts);
3. the logger is deployed as a required test mod and armed before the audit
   experiment window opens;
4. the note block is pressed once per cart by the real task player; the
   transient live inventories are captured by the agent logger and every cart
   reaches the natural void removal (`DISCARDED`);
5. the independent audit mod (accepted issue-#18 jar, unchanged) provides the
   oracle: exact `input_request`/`input_attempt`/`input_processed` chains with
   `orderingEvidence`, `cart_exit` and `cart_remove` inventories;
6. `tools/rom21_verify.py` checks the answer order/inventories against the
   oracle, the program inventories, the restore record, the jar hashes and the
   source-world baseline; `minecart_audit.py check` remains the independent
   evaluator;
7. with `--jar-iteration`, a second jar with the same size but different
   bytes is built from the same source, the lab refuses the drifted deployed
   bytes, re-provisioning replaces by hash, the server restarts, a fresh ready
   state is re-forked/re-restored and a complete second capture/verification
   generation runs; the logger sequence continues across the restart.

## Declared negatives

`tools/rom21_regression.py negatives` runs, and requires fail-closed:

| case | kind | injection |
| --- | --- | --- |
| `no_machine_operation` | live | restored ready machine, no note-block press |
| `logger_missed_capture` | live | `romlog.exitGreaterThan` far away, so nothing is captured |
| `logger_late_capture` | live | `romlog.captureDelayTicks` beyond the pop-to-removal window |
| `inventory_wrong` | live | a restored cart's live item count modified before the press |
| `restore_order_wrong` | live | two entities swapped in the snapshot; the bridge refuses on `order_hash` |
| `output_order_wrong` | offline | transient captures reordered in a copy of the real logger |
| `audit_missing` | offline | the independent audit log removed from a run copy |
| `wrong_instance` | offline | logger instance/run id changed to another instance |

The offline mutations operate on *copies* of real raw evidence; the live
probes are produced by real game operations. Both are declared per case in the
negative records and in the verifier (`NEGATIVE_CASES`).

## Evidence

`docs/evidence/rom21-regression/` contains the committed per-run records (run
manifest with start/end HEAD, clean state and source/jar hashes, the raw agent
logger JSONL, the audit log projected of its periodic `cart_sample` stream,
answer, oracle and verification reports), the negative records and a manifest
with the committed-file hashes. Each generation also carries a
`verify-<label>/` directory with a complete verifier input, so a reviewer can
re-run the offline verifier against the committed raw logger and projected
audit:

```powershell
python tools/rom21_verify.py verify --run docs/evidence/rom21-regression/runs/<run-id>/verify-gen1
```

Raw large files stay under the git-ignored `labs/rom21-regression/`; every
full raw log hash is pinned in the run manifest and the package manifest. The
demo reuses only the committed artifacts.
