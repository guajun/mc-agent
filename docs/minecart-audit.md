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
server that dies before `audit_end` leaves an open session and the status file
or a later verifier run reports an incomplete audit - missing evidence is a
loud result, not a silent pass.

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
server restarts), `tick` (server tick), `wall`, `run`, `inst`, `session`,
`phase` and `type`. A restart appends a new `session_start`; one `runId` can
therefore span several server sessions (needed for mod redeploys and snapshot
restores) without losing ordering. `maxBytes` caps the log; overflow sets
`truncated` and the verifier treats the run as incomplete.

Key event types:

| Type | Meaning |
| --- | --- |
| `input_attempt` | player interaction with a note block, with operator UUID, hand, item, result and whether the position is a configured input |
| `input_request` | `NoteBlock.playNote`, the actual request; carries the triggering entity (`player`, `entity`, or `redstone_or_environment`) |
| `input_processed` | `NoteBlock.triggerEvent`, the server processing the note; links `requestSeq`/`attemptSeq`, records note/instrument and `agentOp` |
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
a fast fall. The note-block hooks distinguish the player attempt
(`useItemOn`/`useWithoutItem`), the request that carries the triggering entity
(`playNote`) and the processing (`triggerEvent`); right-clicks, sounds and
command feedback are never treated as machine operation.

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
* `no operation`, `wrong position`, `marker only` and `redstone only`
  negative runs - all must fail the verifier;
* a lab with the mod but no config - must report "not configured";
* a control lab without the mod running the same script, compared on the
  observable results (the note cycles, the cart is gone) to show the hooks do
  not change fixture behaviour;
* a read-only copy of the source save loading with the mod installed;
* cross-instance log separation.

Evidence lands in `labs/rom18-evidence/` (`summary.json`, the raw logs, the
verifier reports and the console logs); the directory is git-ignored, so it is
a local artifact, not repository content.

## Limitations

* This is instrumentation, not an adversarial sandbox. It does not promise to
  resist same-privilege malicious code; it records evidence for review.
* Full fixture integration (the actual ROM map, deterministic cart
  initialization and faithful snapshot restore) depends on
  [#15](https://github.com/guajun/mc-agent/issues/15),
  [bridge #6](https://github.com/guajun/mc-agent-bridge/issues/6) and
  [#17](https://github.com/guajun/mc-agent/issues/17). The config carries the
  `provenance` fields needed to join those runs.
* Joining tool calls to `input_*` events is
  [#19](https://github.com/guajun/mc-agent/issues/19)'s audit harness: the
  mod records operator UUID, tick and sequence precisely so the join is
  possible, but it does not read the tool trajectory itself.
* Same-tick multi-output ordering is enforced and unit-tested in the verifier;
  the live smoke fixture produced a single cart per run, so it is covered
  synthetically rather than by a live same-tick pair.
