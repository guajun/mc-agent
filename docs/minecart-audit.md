# Minecart audit test mod

`tests/mods/minecart-audit/` is an independent, **test-side** Fabric mod that
records what a machine actually does on the server: the player input attempts,
the server processing the note-block trigger, every tracked chest minecart
leaving its stack, and the ordered inventory captured before the cart is
removed by the void.

It is not the agent's logger, it is not ROM logic, and it places no command
blocks. The mod only ever reads the world and appends to its own JSONL file.
The agent still has to build its own logger; this mod is the evaluator's
independent source of evidence for the same run.

The task it instruments is the Minecart ROM from
[issue #14](https://github.com/guajun/mc-agent/issues/14); the mod itself is
deliberately generic - regions, cart types and identifiers come from a config
file, so it can audit any note-block-fed machine and any minecart stack.

## Build and install

Minecraft Java 26.2 ships unobfuscated classes, so the mod builds with a JDK
and a normal game installation - no Gradle, no remapping. The build script
follows the same pattern as `interface-mod/build.py`.

```powershell
python tests/mods/minecart-audit/build.py `
    --minecraft-dir "D:\MC\MC_Game\.minecraft" `
    --jdk "C:\path\to\jdk-25"
# -> tests/mods/minecart-audit/dist/mc-minecart-audit-0.1.0.jar
```

`--jdk` falls back to `$MC_AGENT_JAVA`, the JDK recorded in a `labs/*/lab.json`,
`$JAVA_HOME`, and finally `javac` on PATH. `--minecraft-dir` defaults to
`D:\MC\MC_Game\.minecraft` and can be set with `$MC_AGENT_MINECRAFT_DIR`.

Provision a lab (or any Fabric server) with the jar and Fabric API:

```powershell
python tools/lab_server.py provision --name audit --void --fabric-api --carpet `
    --mod-jar tests/mods/minecart-audit/dist/mc-minecart-audit-0.1.0.jar
```

> `lab_server.copy_if_different` currently compares file size only, so a
> rebuilt jar of the same size would be skipped. Remove the jar from
> `labs/<lab>/mods/` before re-provisioning until
> [issue #17](https://github.com/guajun/mc-agent/issues/17) lands.

## Configuration

The mod reads `<server run dir>/mc-audit/config.json` when the server starts
(`-Dmcaudit.config=<path>` overrides the location). A missing or malformed
config is loud but harmless: the mod writes `mc-audit/status-unconfigured.json`,
prints an error on the console, and disables itself. That state is detectable
by the test program, so a run cannot silently pass as "audited".

```json
{
  "runId": "rom-2026-09-26-1",
  "instanceId": "lab-a",
  "dimension": "minecraft:overworld",
  "provenance": {
    "kind": "source-world | fork | restore",
    "reference": "D:/.../Minecart ROM test",
    "snapshotId": "...",
    "snapshotHash": "..."
  },
  "outputDir": "mc-audit",
  "maxBytes": 67108864,
  "sampleIntervalTicks": 1,
  "correlationWindowTicks": 2,
  "cartTypes": ["minecraft:chest_minecart"],
  "agentUuids": ["49e96464-aa17-46b3-b5c6-87574b6b48b4"],
  "inputRegions": [
    { "name": "note-block", "from": [0, -59, 0], "to": [0, -59, 0] }
  ],
  "stackRegion": { "from": [2, -60, -1], "to": [3, -59, 1] },
  "outputRegion": { "from": [4, -140, -8], "to": [8, -60, 8] }
}
```

Regions are inclusive block boxes: a point belongs to a region when the block
containing it (`floor` of each axis) is inside `from..to`. `inputRegions` name
the machine inputs; `stackRegion` is where carts are tracked as fixture
outputs; `outputRegion` is an optional second out-of-bounds marker. A cart is
tracked when a `cartTypes` entity loads inside `stackRegion` in `dimension`;
it *exits* the first time it leaves `stackRegion` or enters `outputRegion`.
`agentUuids` is optional: when set, only those players can be classified as
the agent; when empty, any player counts as a player operator (the verifier
still reports the UUID).

## Lifecycle and commands

`/mcaudit` (permission level gamemaster; RCON has owner level) is the test
side's control surface. The commands never operate the machine.

| Command | Meaning |
| --- | --- |
| `/mcaudit status` | run, instance, phase, sequence, tracked carts, incomplete reasons |
| `/mcaudit phase init` | fixture initialization (repeats allowed) |
| `/mcaudit phase restore` | snapshot restore / fork setup |
| `/mcaudit phase experiment_start` | opens the agent-operation window |
| `/mcaudit phase experiment_end` | closes it |
| `/mcaudit mark <label>` | marker for test scripts; cannot be mistaken for an operation |
| `/mcaudit flush` | force the status file |
| `/mcaudit end` | writes `audit_end` and closes the JSONL |

The server also writes `audit_ready` at startup and `audit_end` on shutdown. A
server that dies before `audit_end` leaves an open session; the verifier treats
**any** open session in the file as incomplete (an explicit
`--allow-open-history` is required to ignore an earlier crashed session), so
missing evidence is a loud result, not a silent pass. The live smoke includes a
kill/restart negative for exactly this case.

## Evidence files

```
mc-audit/
  audit-<runId>.jsonl          the raw event stream, flushed per event
  status-<runId>.json          current snapshot (phase, counters, carts, hooks)
  latest.json                  pointer to the current run's files
  status-unconfigured.json     written when the config is missing/broken
  config.json                  the config the mod consumed
```

Every event carries `seq` (strictly increasing in the file, seeded across
server restarts), `tick` (the **real** server tick, which a restart resets),
`wall`, `run`, `inst`, `session`, `phase` and `type`. The gate exporter keeps
the real tick and the append sequence, plus the session and wall clock, so an
ordering decision names its clock scope instead of renumbering ticks. A restart appends a new `session_start`; one `runId` can
therefore span several server sessions (needed for mod redeploys and snapshot
restores) without losing ordering. `maxBytes` caps the whole file, not one
session: the byte counter is seeded from the existing file size, so a run
restarted several times cannot reset the budget. Overflow sets `truncated` and
the verifier treats the run as incomplete.

`hook_overhead_ms` in the gate manifest is the **total session hook time**
(sum of every hook's `totalNanos` divided by 1e6); the per-hook
calls/total/max/avg/errors live in the same manifest under
`hook_overhead_by_hook` (`audit_end.hooks`), so the figure cannot be read as
per-call or per-tick.  The mixed-trigger regression
(`scenario_attack_then_use`) punches the note block and then uses it within
the correlation window: the use must be the only `input_processed`, and a
punch's unscheduled play must never be consumed by a later use callback.

Key event types:

| Type | Meaning |
| --- | --- |
| `input_attempt` | player interaction with a note block, with operator UUID, hand, item, result and whether the position is a configured input |
| `input_request` | `NoteBlock.playNote`, the actual request; carries the triggering entity (`player`, `entity`, or `redstone_or_environment`) |
| `input_processed` | The server processing the note; links `requestSeq`/`attemptSeq`, records note/instrument and `agentOp`. `path` names the vanilla path: `triggerEvent` when the block event is scheduled, or `playNote` when the note block's instrument does not work above the block and the block above is not air, so the play itself is the processing (the fixture's harp note block with leaves above uses `playNote`). |
| `cart_tracked` | a configured cart loaded inside the stack region, with ordered inventory and origin phase |
| `cart_sample` | periodic position/motion/inventory sample, deduplicated per cart and tick |
| `cart_exit` | first time the cart left the stack region / entered the output region (via sample or removal) |
| `cart_remove` | removal with reason (`DISCARDED` for the void), position, motion and the **ordered inventory captured before the container drops it** |
| `cart_inventory_change` | observed inventory mutation while tracked |
| `cart_teleport`, `cart_reload`, `cart_reappeared` | alternative-behaviour evidence (teleports, chunk reloads, UUID reuse) |
| `audit_incomplete` | a reason the audit cannot be trusted (missing correlation, hook error, …) |
| `audit_end` | per-session close with counts, cart summary and hook overhead |

The minecart hooks deliberately inject at `Entity.remove` and
`AbstractMinecartContainer.remove` *before* content destruction, because the
vanilla fast path drops contents first; end-of-tick sampling alone would lose
a fast fall. The note-block hooks distinguish the player attempts
(`useItemOn`/`useWithoutItem`, and 26.2's `attack` punch, recorded with
`path: "attack"` so a punch cannot poison the chain), the request that carries
the triggering entity (`playNote`) and the processing (`triggerEvent`);
right-clicks, sounds and command feedback are never treated as machine
operation.

Only `useItemOn`/`useWithoutItem` count as a calibrated machine operation.
26.2's `attack` punch calls `playNote` but never changes the note state, so it
cannot trigger the observer/piston machine through the calibrated path; it is
still recorded as an attempt (`path: "attack"`) so the chain stays intact, but
`machine_operated` ignores it. The live smoke has an attack-only negative that
forces the fake player to survival, punches the note block, and shows the
block still present, the cart unmoved and the verifier `fail`.

A request or attempt is credited to exactly one processing event, in the same
order the server processes block events: requests are consumed FIFO, and the
attempt is the one whose recorded `requestSeq` equals the processing request
(not merely a neighbour in the time window). No matching attempt is a
fail-closed gap. The verifier fails a run when one
`requestSeq`/`attemptSeq` is referenced by more than one target
`input_processed`, when an attempt carries a different `requestSeq` than the
processing it is credited to, or when the attempt/request/processing operator
UUIDs disagree. Only configured input positions feed the correlation maps, and
stale entries are pruned each tick.

All hooks use `@Inject` only: no redirects, no overwrites, no mutation, no
cancellation. Per-hook call counts, total nanoseconds and max nanoseconds are
reported in the status file and in `audit_end` so observation cost is
measurable.

## Reading the evidence

`tools/minecart_audit.py check` reads one JSONL file and returns a verdict:

```powershell
python tools/minecart_audit.py check --log labs/audit/mc-audit/audit-<run>.jsonl `
    --status labs/audit/mc-audit/status-<run>.json --json
python tools/minecart_audit.py selftest        # synthetic positive/negative cases
```

Exit codes: `0` pass, `1` fail, `2` incomplete/unreadable. The report answers
the acceptance questions directly:

* `machine_operated` - at least one target input was actually processed on the
  server and correlated to a player request and use attempt, inside the
  experiment phase;
* `input_chain` - every target processing event has a preceding request and,
  for agent operations, an attempt inside the correlation window; otherwise
  the run fails with "insufficient ordering evidence";
* `transient_outputs` - every cart removed during the experiment has exit
  evidence and an ordered inventory captured before destruction; duplicate
  exits and duplicate removals fail;
* `ordering` - file order equals occurrence order, exits are strictly
  increasing, same-tick outputs need distinct sequence numbers;
* `completeness` - `audit_end` says complete, no `audit_incomplete`, no
  truncation, and the session model matches the status file.

Negative cases are covered both by the verifier selftest (no operation, wrong
position, marker-only, answer-only, missing request, environment-only,
duplicate output, mixed instance, truncation, missing end) and by the live
smoke script below.

## Live smoke

```powershell
python tests/mods/minecart-audit/live_smoke.py
```

The script builds the mod, raises disposable void labs on the issue's ports
(27180-27189), and runs:

* a positive run across a server restart (two sessions, one agent operation,
  one cart captured before the void);
* `no interaction`, `wrong position`, `marker only`, `answer only`,
  `attack only` and `redstone only` negative runs - all must fail the
  verifier; the attack run forces the fake player to survival so the punch is
  recorded without destroying the note block, and shows the block and cart
  unmoved;
* a killed-server run (`stop --force`, restart, close the second session) -
  the verifier must report `incomplete` for the abandoned session;
* a lab with the mod but no config - must report "not configured";
* a control lab without the mod running the same script, compared on the
  observable results (the note cycles, the cart is gone) to show the hooks do
  not change fixture behaviour;
* a read-only copy of the source save loading with the mod installed; the save
  is discovered under `--mc-dir`'s version instances (or `--source-save`), and
  a missing save fails the run instead of silently passing subsections;
* cross-instance log separation.

Evidence lands in `labs/rom18-evidence/` (`summary.json`, the raw logs, the
verifier reports and the console logs); the directory is git-ignored, so it is
a local artifact, not repository content. Prior attempts are kept as
`<evidence>.attempt-<timestamp>/` instead of being deleted.

## Combined bridge / interface-mod verification

```powershell
python tests/mods/minecart-audit/bridge_restore_smoke.py
```

This is the merged-stack run: a clean bridge source (default the
`codex/rom13-bridge6` worktree whose head is pinned in the script, i.e. the
guarded restore plus `player.view`), the clean interface mod 0.6.0
(SHA-256 `45f12e16...404f`), and the audit mod installed as a **required**
test mod (`--test-mod`, so a missing audit mod refuses to start) in both a
source-audit lab and an experiment lab.

The stages, all live on dedicated 26.2 servers:

1. a generic three-cart stacked chest-minecart fixture (distinct items) is
   forked under the bridge's guarded `fork` (frozen, snapshot, world copy);
2. the experiment lab loads the fork world and the bridge runs the guarded
   `restore` with `expect_world_dir` + `replace_existing`, then `verify`
   compares full state (order hash, counts, dimension, positions, velocities,
   NBT/items) - `ok: true` is required before the audit stage continues;
3. the audit log's `cart_tracked` inventories for the restored carts are
   cross-checked item-by-item against the bridge's post-restore snapshot
   (same UUIDs, same ordered items);
4. `player`/`player.view` is called on the restored destination and must
   return `found`, dimension and a normalised eye/direction;
5. a generic note-block machine in the experiment lab produces the canonical
   input -> processing -> cart-output chain while the restored copy is still
   tracked;
6. the #17 smoke mod (`examples/smoke-mod`, built with
   `tools/build_mod.py` against the lab) is deployed alongside the audit and
   interface mods; `mcagent-smoke status`/`sample` and
   `lab_server.py verify --require-vantage` must all succeed. That smoke mod
   proves deployment capability and coexistence; it is **not** the agent's
   logger and is never treated as one;
7. both audit sessions are closed with `/mcaudit end` and the labs are stopped
   before the raw logs are copied, so the retained source-child log is complete
   instead of an open session.

Evidence lands in `labs/rom18-b6-evidence/` (`summary.json`, per-stage JSON
including the recorded bridge calls, the restored-audit crosscheck, the
coexistence output, raw audit logs and console logs).

## Stage-one gate adapter

`tools/stage1_gate.py` from #22 consumes a canonical bundle, not the raw
JSONL. The shared lossless adapter `tools/stage1_evidence.py` (from #28)
projects the raw formats for the full bundle; the module-level
`tools/minecart_audit.py export` maps the live evidence into the gate's
`independent_test_mod` artifacts: `test-mod-manifest.json`,
`audit-events.jsonl`, `negative-cases.jsonl` and `audit-lifecycle.json`. The
mapping keeps the raw event type, server tick and sequence in each row's
`detail`; the canonical `(tick, seq)` is a per-identity monotonic counter
because a server restart resets the game tick while the audit sequence keeps
going.

The export binds evidence instead of relabelling it: an event whose own
`run`/`inst`/dimension does not match the declared identity is refused, and a
log with an open session or without `audit_end` is refused. Negative cases
must have verifier verdict exactly `fail` (an `incomplete`/crashed log is not a
rejected negative), the gate asserts each row's `verifier_verdict == "fail"`,
and environment-triggered processing rows carry an `actor_provenance` instead
of a bare null actor. The manifest's `fixture_behavior_unchanged` and
`agent_mod_coexists` claims are derived from the live summaries and the
adapter refuses to export when either is false.

```powershell
python tests/mods/minecart-audit/stage1_evidence.py
```

reads the two live evidence sets, exports the artifacts, assembles a partial
live bundle with the real run/instance/port/source-world fields, and runs the
gate on it. The historical partial-bundle run reports `independent_test_mod: pass` with the
assertions *input -> processing -> output chain* and *negative cases rejected*
while the overall bundle stays `blocked` on the other prerequisites' missing
artifacts. That is deliberately not a gate pass: #15's fixture and the other
checks are owned by their own work. The later combined gate passed **8/8** and
the full issue #13 chain is accepted; see the [acceptance runbook](minecart-rom-runbook.md).

## Limitations

* This is instrumentation, not an adversarial sandbox. It does not promise to
  resist same-privilege malicious code; it records evidence for review.
* The actual ROM map and deterministic fixture initialization were delivered by
  [#15](https://github.com/guajun/mc-agent/issues/15); the historical component runs here
  uses generic void-lab fixtures. The combined bridge/restore run does prove
  the restored-copy audit coverage, but it is not fixture integration.
* Joining tool calls to `input_*` events is
  [#19](https://github.com/guajun/mc-agent/issues/19)'s audit harness: the
  mod records operator UUID, tick and sequence precisely so the join is
  possible, but it does not read the tool trajectory itself.
* Same-tick multi-output ordering is enforced and unit-tested in the verifier;
  the live fixtures produced one cart per run, so it is covered synthetically
  rather than by a live same-tick pair.
