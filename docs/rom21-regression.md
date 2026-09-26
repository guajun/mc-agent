# Minecart ROM regression (issue #21)

The [auditable cold-start protocol](coldstart-protocol.md) proves one
autonomous run. This regression turns the *successful* cold-start machinery
into a repeatable real-game test: the same initialization/restore recipe, the
same operation sequence and a parameterized extraction of the agent-written
logger, run against fresh sealed challenges and judged by the independent
[audit test mod](minecart-audit.md).

**Regression results are not model-capability evidence.** Every regression run
is a real Minecraft server, a real guarded restore and a real note-block
operation, but no model is called. The original autonomous evidence remains
[frozen under the #20 package](evidence/rom20-coldstart/README.md); the #21
package labels its own results as regression results.

## What it runs

* two calibrated sealed cart programs (5 carts, seed 2101; 4 carts, seed 2102
  with a different item pool), committed as test-side inputs;
* three fresh source+experiment instance pairs on the dedicated
  `27200-27219` ports, one of them with a cold map download;
* the agent logger rebuilt from
  [`examples/minecart-rom/regression/logger`](https://github.com/guajun/mc-agent/tree/main/examples/minecart-rom/regression/logger)
  (provenance: the frozen #20 source hashes plus the documented behaviour
  changes), deployed as a required test mod;
* the unchanged accepted audit mod (`7a77e89d…`) as the independent oracle,
  producing exact request/processing chains and `cart_exit`/`cart_remove`
  inventories;
* the same-size jar iteration proof: two jars with identical size and
  different bytes, deployment refusal on drifted bytes, restart, re-restore
  and a second complete capture/verification generation;
* eight declared negatives (five produced by real game operations, three by
  offline mutation of real evidence), each required to fail closed.

## Commands

```powershell
python tools/rom21_regression.py suite --stamp <stamp>   # three live runs
python tools/rom21_regression.py negatives               # fail-closed probes
python tools/rom21_regression.py package                 # stage evidence
python tools/rom21_regression.py selftest                # offline
```

The full harness description and the per-check semantics live in the
[regression README](https://github.com/guajun/mc-agent/tree/main/examples/minecart-rom/regression);
the committed results and raw-hash manifest live in
`docs/evidence/rom21-regression/`.

## CI behaviour

The repository CI runs the offline tests and the `selftest` commands only.
Without a game environment the live commands are not invoked at all; there is
no fake PASS. The opt-in live entry points (`suite`, `negatives`) fail closed
with a clear error when Java/Minecraft resources are missing.

## Result assertions

`tools/rom21_verify.py` requires, per run: the logger session identity
(instance/run/dimension/build), the guarded restore (order hash, UUID order,
counts), one calibrated note-block operation per cart with the audit engine's
own `orderingEvidence` and exact `requestSeq`/`attemptSeq` chains, one
transient live-inventory capture per cart with a bounded pop-to-capture
window, one natural `DISCARDED` void capture per cart, a strictly increasing
observed pop order consistent across logger and audit, the audit exit/removal
inventories matching the captures, all observed inventories matching the
sealed program exactly, the jar hashes and the unchanged source-world
baseline. The answer is only ever derived from the observed pop order; the
spawn order is never assumed to be the answer.
