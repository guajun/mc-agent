# ROM21 regression evidence (issue #21)

**Status: regression PASS.** Three authoritative fresh-instance runs (two
calibrated sealed programs, one cold map download), the same-size jar
iteration/restart/re-restore run, and all eight declared negatives fail
closed. The committed generators and verifier re-verify this package offline.

> **These are regression results, not model-capability evidence.** No model was
> called in any run here. The original autonomous cold start remains the
> [ROM20 package](../rom20-coldstart/README.md); this package proves that its
> initialization/restore recipe, operation sequence, logger capture, log
> parsing and result assertions are reproducible against fresh real-game
> instances.

## Provenance

* Executed source: commit `c5fa37b7e902779baf5cb2daed94a3e06f87aa02`
  (`codex/rom13-regression`), worktree clean before and after every run
  (`provenanceStart`/`provenanceEnd` in each `runs/<id>/run.json`, with the
  actual working-tree bytes, Git blobs and jar hashes of every executable
  source).
* The package and its manifest are produced by a **later packaging commit** of
  `tools/rom21_regression.py` (`package_suite`) that reads the retained raw run
  directories; no run executed a revision other than `c5fa37b`.
* Original run extracted from: `rom20-20260926T063100Z`, executed at
  `1deb735ba0f0b2ff36533868fc5d91fab8546d09`; frozen session sha256
  `46c2e824f46a72b2e68257d416107945e1c3818097b20ce83a1646b285a313b0`;
  original logger jar `e445f461ab0de331189ce609dac60d53f2ffb4529fc22b5dfb6c77d5a8593f89`
  (16,778 bytes). The original logger source hashes and the exact regression
  behaviour changes are recorded in
  [`examples/minecart-rom/regression/logger/PROVENANCE.md`](../../../examples/minecart-rom/regression/logger/PROVENANCE.md);
  the frozen originals are not modified.
* Pinned runtime: interface jar `45f12e16b3979be6a699ac3c744b2a68dfcf8dd2379f5987bf9b9319adf4404f`,
  independent audit jar `7a77e89d72969f23ce7b7c2543bfe992c2a3674171f3eb190a8e7faa8f2ec4ac`
  (unchanged issue-#18 oracle), bridge source `4116ebb34b60e962e95834791d521b369fd52c65`,
  Minecraft 26.2 / Fabric 0.19.5 / Java 25, map ZIP
  `46954828589489f43819623cd42bb9ad6bbd999073f4fcc839d1817421a01387` (748,712 bytes).

## The three authoritative runs

All runs used dedicated ports `27200-27219` and fresh lab names per run; the
unoperated source world was hashed against the documented baseline
`8cd54c86af9fa8d6b9ea33441fb21dac295cd2b5ddaa60327f5fb3a30255324a` before and
after each run (unchanged).

| run | config | carts | path | logger jar | audit | verifier |
| --- | --- | --- | --- | --- | --- | --- |
| `rom21-20260926T083500Z-a1` | calibrated-a (seed 2101) | 5 | warm cache | `3c1dae36…` 21,530 B | PASS | PASS |
| `rom21-20260926T083500Z-b1` gen1 | calibrated-b (seed 2102) | 4 | warm cache | `3c1dae36…` 21,530 B | PASS | PASS |
| `rom21-20260926T083500Z-b1` gen2 | calibrated-b, same-size iteration | 4 | restart, re-fork, re-restore | `8068999c…` 21,530 B | PASS | PASS |
| `rom21-20260926T083500Z-a2` | calibrated-a (seed 2101) | 5 | **cold download** | `3c1dae36…` 21,530 B | PASS | PASS |

Observed pop order (from the live logger transient captures, joined to the
audit `cart_exit`/`cart_remove` events, never assumed from the spawn order):

* **a1** (5 carts): `gold_ingot x1 @11, redstone x2 @22, copper_ingot x2 @23`
  -> `sand x2 @1, dirt x4 @5, gold_ingot x2 @17` -> `copper_ingot x2 @12` ->
  `gold_ingot x4 @9, glass x3 @11, redstone x2 @18` -> `redstone x3 @13`;
* **b1** (4 carts, identical in gen1 and the post-restart gen2):
  `stone x4 @16, copper_ingot x5 @22` -> `glass x1 @2, coal x5 @13` ->
  `glass x4 @0` -> `copper_ingot x6 @1, redstone x4 @2`;
* **a2** (cold, 5 carts): the same program as a1 with fresh cart UUIDs and the
  same observed inventories.

Cold download record (`runs/rom21-20260926T083500Z-a2/fetch.json`): HTTP 200,
`cache_hit: false`, 748,712 bytes, SHA-256 equal to the pinned map artifact.

Same-size jar iteration (`runs/rom21-20260926T083500Z-b1/jar-swap-refusal.json`
and `run.json`):

* both jars were built from **identical source bytes**
  (`sourceSha256` equal) with versions `romlog-rom21-1` and `romlog-rom21-2`;
* both are exactly **21,530 bytes** and differ in SHA-256
  (`3c1dae36a56e3f2c…` vs `8068999c…`);
* copying the v2 bytes over the deployed v1 jar was refused by the lab start
  gate (deployed-bytes hash drift, not size/mtime);
* re-provisioning replaced the bytes by hash (`deployedSha256` equals the v2
  jar), the server restarted, a fresh ready state was re-forked and
  guarded-restored, and generation 2 passed with the logger sequence continued
  across the restart.

## What each committed verifier sample proves

Every generation directory contains `verify-<label>/` with a complete,
self-contained verifier input. A reviewer can re-run:

```powershell
python tools/rom21_verify.py verify --run docs/evidence/rom21-regression/runs/<run-id>/verify-gen1
```

The verifier checks, per generation: logger arm-before-operation and
instance/run/dimension/build identity, the guarded restore (order hash, UUID
order, matched entities), one calibrated note-block operation per cart with the
audit engine's own `orderingEvidence` and exact `requestSeq`/`attemptSeq`
chains, one transient live-inventory capture per cart inside the pop window,
one natural `DISCARDED` void capture per cart, monotone observed order
consistent across logger and audit, exit/removal inventories equal to the
captures, all observed inventories equal to the sealed program, the answer
equal to the independent oracle, the jar/provenance pins and the unchanged
source world. `minecart_audit.py check` (the independent issue-#18 evaluator)
is required to be PASS as well.

## Negatives (all fail closed)

| case | kind | injection | failing check(s) |
| --- | --- | --- | --- |
| `no_machine_operation` | live | restored machine, no press at all | audit oracle + agent operations + causal joins |
| `logger_missed_capture` | live | exit plane far away; nothing captured | transient captures |
| `logger_late_capture` | live | capture delay beyond pop-to-removal | transient captures |
| `inventory_wrong` | live | live cart item count modified | inventory vs program |
| `restore_order_wrong` | live | two snapshot entities swapped | bridge refuses on `order_hash`; restore check fails |
| `output_order_wrong` | offline | transient captures reordered | output order |
| `audit_missing` | offline | audit log removed | audit oracle |
| `wrong_instance` | offline | logger instance/run changed | logger session identity |

Live probes are produced by real game operations; offline probes mutate copies
of the real raw evidence. The per-case records are under `negatives/`.

## Reproduction

The generators, logger source, configs, drivers and tests are committed:

```powershell
python tools/rom21_regression.py selftest          # offline
python -m unittest discover -s tools/tests         # 153 tests
python tools/rom21_regression.py suite --stamp <new-stamp>   # needs game+network
python tools/rom21_regression.py negatives                   # needs game+network
python tools/rom21_regression.py package
python examples/minecart-rom/regression/demo/demo.py         # reuses this package
```

A fresh live suite writes to `labs/rom21-regression/` (git-ignored); the raw
audit files, jars and bridge logs stay there and their full SHA-256 values are
pinned in each `runs/<id>/run.json` and in this package's `manifest.json`.
Committed audit files are projections of the raw logs with only the periodic
`cart_sample` stream removed (no verifier-relevant event is dropped).

## Honest limitations

* No model runs here: the regression does not re-test autonomous research or
  cold-start model capability.
* Both calibrated programs pop in the stack's spawn order in the observed runs
  (the fixture's calibrated behaviour). The verifier never assumes that: the
  answer order comes only from the live capture/audit events, and
  `output_order_wrong` proves a mismatched order fails closed. No program with
  a pop order different from spawn order is claimed.
* The challenge generator guarantees distinct inventory signatures and at
  least one item per cart; empty/duplicate-inventory coverage is therefore not
  claimed. The verifier still rejects duplicate or unexpected signatures if
  the data ever contains them.
* A pre-freeze development probe observed that stopping/restarting the
  unoperated *source* instance lost its hover-seat armor stand while keeping
  the carts. The frozen harness therefore forks both generations from the
  still-running, unoperated source; the required restart + re-restore cycle is
  exercised on the experiment instance (fresh provision, restart, guarded
  restore, full second operation).
* The three authoritative runs and all negatives passed on the frozen commit;
  no authoritative failed attempt was hidden. The declared negative probes are
  the intentional failures and are committed here.
