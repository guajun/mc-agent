# Auditable cold-start runs

A **cold-start run** is one attempt by a real agent, with a fresh context, to
research and operate a Minecraft world through the Toolkit. The goal of this
page is narrower than the science: define the *environment contract*, the
*run entry*, the *trajectory sink* and the *judgement*, so that a later review
can tell what actually happened without trusting the agent's final message.

This is the plan tracked by [mc-agent#19](https://github.com/guajun/mc-agent/issues/19).
It adds no model client and no session backend to mc-agent: the harness owns
the model, the run directory owns the evidence, and these tools only check and
record facts.

!!! warning "No pre-written solution"
    The task input for the Minecart ROM cold start is
    [`examples/coldstart/task-minecart-rom.md`](https://github.com/guajun/mc-agent/blob/main/examples/coldstart/task-minecart-rom.md).
    It states the task and the requirement to really run the machine; it does
    not contain an answer, a solution script or a prior trajectory. Do not add
    one before the cold start.

## 1. Fix the harness first

The first cold start uses a **user-launched harness with ordinary development
tools** (a terminal, file read/write, source and dependency fetching, a JDK to
build, and the bridge JSON CLI or MCP). The restricted Hermes webhook route is
*not* that harness: its per-route `toolsets` deliberately expose the Toolkit MCP
server only, with no terminal or file tools, so an agent on that route cannot
write, compile and install its own logger. Webhook delivery is a later,
optional path; see [Unattended Hermes](hermes-unattended.md).

A harness configuration is a small JSON file; the reference one is
[`examples/coldstart/harness-pi.json`](https://github.com/guajun/mc-agent/blob/main/examples/coldstart/harness-pi.json).
It names the harness command, how to read its version, the model and provider
strings for the record, the shell probe, the source endpoints to fetch, the
minimum JDK, the bridge command and the reserved ports. Machine-specific
values (JDK path, bridge executable) can be supplied without editing the file:

```bash
python tools/harness_preflight.py run \
    --config examples/coldstart/harness-pi.json \
    --out labs/coldstart/preflight \
    --run-dir labs/coldstart/run-01 \
    --set "build.java=C:\path\to\jdk-25\bin\java.exe" \
    --set "bridge.command=C:\path\to\.venv\Scripts\mc-bridge.exe"
```

The configuration is fixed **before** the agent starts. Changing it afterwards
invalidates the run: the preflight report records the exact values it checked.

## 2. The environment contract: `harness_preflight.py`

`tools/harness_preflight.py` answers one question: can this harness reach
everything the agent will need, *before* a model is spent on discovering it?
Each check writes its command, exit code, transcript and hash under
`<out>/probes/`; the summary is `<out>/preflight.json` and `preflight.md`.

| Check | Proves | Notes |
| --- | --- | --- |
| `harness_identity` | the configured harness command answers its version probe | model/provider strings are recorded, not secrets |
| `terminal` | the harness can run a shell command | `shell` from the config |
| `filesystem` | write, read-back and hash a nonce file | exact bytes preserved |
| `ports` | the reserved API/mod ports are free and inside the range | default range 27190–27199 |
| `source_fetch` | every configured source/dependency endpoint answers (Fabric meta, Modrinth) | optional `build.pip_packages` adds a pip probe; one failing endpoint fails the check |
| `build_install` | a JDK >= `build.min_java` compiles and packages a probe jar, and the installed bytes hash-match | Java 25 is required for 26.2; a PATH JDK 21 fails this check on purpose |
| `bridge_cli` | `mc-bridge --help` runs | the JSON CLI is the required call surface |
| `bridge_mcp` | `mc-bridge mcp --help` runs | optional; skipped when the `mcp` extra is absent, because the JSON CLI is an accepted alternative |
| `bridge_smoke` | `tools/smoke_offline.py` completes with the echo backend | real bridge + loop processes against a fake mod on the reserved ports |
| `lab_management` | `lab_server.py list` works; with a lab configured and `start` enabled, a real headless Fabric server is provisioned, pinned to the reserved ports, started, answers RCON `list`, and stops | records `lab.json` and the console hash; a disabled start is SKIP, so the audit's preflight cross-check rejects it |

`--checks`/`--skip` select a subset; `SKIP` never counts as `PASS`. Exit code 0
means overall PASS, 1 means at least one selected check failed. A probe that
crashes is a failed check, not a crashed preflight.

`lab_management` starts a real server, so it is the slow check. The reference
config runs it. Run the fast subset during development:

```bash
python tools/harness_preflight.py list
python tools/harness_preflight.py run --config <config> --out <dir> \
    --checks harness_identity,terminal,filesystem,ports
```

When `--run-dir` is given, the report is also appended to the run trajectory as
a `mark` record, so the audit can see which environment the run started in.

## 3. Open a run: `run_trace.py init`

```bash
python tools/run_trace.py init \
    --run-dir labs/coldstart/run-01 \
    --run-id run-01 \
    --task-file examples/coldstart/task-minecart-rom.md \
    --harness pi --model <model> --provider <provider> --harness-version <version> \
    --note "cold start; fresh session"
```

`init` writes:

```
labs/coldstart/run-01/
  run.json            run manifest: harness, model, task hash, repo commit, imports, artifacts
  task.md             the exact task bytes handed to the agent (never the answer)
  visibility.json     every document/skill visible to the agent, with path + sha256
  visibility/files/   byte copies of those files (small files)
  trajectory.jsonl    append-only canonical records
  evidence.json       declarations of external evidence (filled in before the audit)
  preflight/          optional harness_preflight.py output
  audit/              run_audit.py output
```

**Clean-context rules.** The session must not carry the design discussion of
this repository, a standard answer, a previously successful script or a
solution trajectory. The task is the task file above; the visible documents and
skills are recorded by `visibility.json`; nothing else is supplied. The world's
full memory data is **not** hidden: the protocol records what is visible and
never filters inventory, NBT or tick order. The evidence snapshot is an
inventory, not a sandbox.

**Import the harness's own trajectory.** A pi run records tool calls and
results in its session JSONL (`$PI_SESSION_FILE`). Copy it into the run and
translate it into canonical records when the session ends:

```bash
python tools/run_trace.py import-pi --run-dir labs/coldstart/run-01 \
    --session "$PI_SESSION_FILE" --phase agent
```

The raw session is copied to `imports/` and hashed. Other harnesses append the
same records directly through `record`/`call`/`result` (see below) or get their
own importer; the canonical schema does not depend on pi.

!!! note "Open the run before the session starts"
    `init` records the run creation time. A session imported later must have
    started after it, otherwise `validate` reports the timestamps out of order.
    Create the run first, then launch the fresh harness session.

## 4. The trajectory sink

`trajectory.jsonl` is append-only, one JSON object per line, flushed to disk on
every append. `seq` is assigned by the recorder; `at` is RFC 3339 UTC.

| Record | Required fields | Meaning |
| --- | --- | --- |
| `header` | `schema`, `run_id` | written by `init` |
| `call` | `call_id`, `tool`, `arguments` | one tool invocation: file, terminal, bridge, build, script, MCP call |
| `result` | `call_id`, `status` (`ok`/`error`/`open`) | the invocation's return or error; `duration_ms` optional |
| `phase` | `phase`, `actor` | a lifecycle transition |
| `mark` | `name` | an annotating fact (preflight, artifact, fixture, …) |

Phases are `prepare` (test-side setup), `restore` (copy restoration), `agent`
(the agent's own work), `audit` (post-run review). Actors are `operator`,
`test`, `agent`, `restore`, `harness`, `reviewer`. This is how "test
preparation", "agent operation" and "copy restoration" stay distinguishable in
one file.

Convenience commands:

```bash
python tools/run_trace.py call   --run-dir <run> --call-id c1 --tool bash \
    --arguments '{"command": "javac LoggerMod.java"}' --phase agent --actor agent
python tools/run_trace.py result --run-dir <run> --call-id c1 --status ok --result '{"text": "compiled"}'
python tools/run_trace.py phase  --run-dir <run> --phase restore --actor restore --instance lab-a
python tools/run_trace.py artifact --run-dir <run> --path build/logger.jar --label "agent logger jar"
python tools/run_trace.py review --run-dir <run> --id machine_operated.causality \
    --status resolved --by "reviewer name" --note "watched the replay" \
    --evidence trajectory:c2-noteblock --evidence testmod:seq3
python tools/run_trace.py validate --run-dir <run>
```

`artifact` copies self-written code and build outputs into `artifacts/` and
records their hashes in `run.json`. `review` appends an explicit review
resolution (section 5). `validate` checks the structure: unique call ids, one
result per call, monotonic `seq` and timestamps, known phases,
task/visibility hashes, import and artifact hashes, declared evidence hashes,
and well-formed review records. Missing a result is an error, not a warning.

## 5. Evidence declarations

External artifacts stay where they were produced (the agent's logger output,
the test mod's file), and `evidence.json` declares them with a path relative to
the run and, when available, a sha256 that the audit re-checks:

```jsonc
{
  "agent_logger": {
    "path": "agent/logger.jsonl",          // raw output written by the agent's own logger
    "sha256": "...",                       // re-checked; a change is INFRA_ERROR
    "normalized": "agent/logger.norm.jsonl" // optional canonical view
  },
  "test_mod": { "path": "test/testmod.jsonl", "sha256": "..." },
  "answer":   { "path": "answer.json", "sha256": "..." },
  "oracle":   { "path": "oracle.json", "sha256": "..." },
  "fixture":  { "path": "fixture/ready.json", "sha256": "..." },
  "restore":  { "path": "restore/verify.json", "sha256": "..." },
  "preflight":{ "path": "preflight/preflight.json", "sha256": "..." },
  "review":   { "path": "reviews.jsonl", "sha256": "..." }   // default location if omitted
}
```

### Test-side events (the independent oracle side)

The independent test mod ([mc-agent#18](https://github.com/guajun/mc-agent/issues/18))
writes a JSONL file. The audit consumes this projection of it; the mod may use
its own field names for the aliases in brackets:

| Event | Fields | Meaning |
| --- | --- | --- |
| `instance_ready` | `instance`, `dimension`, `tick` | fixture initialised |
| `restore_started` / `restore_finished` | `instance`, `tick` | copy restoration bookkeeping |
| `input_attempt` | `actor`, `tick` | something was *tried* (right-click callback, marker, command ack) |
| `input_processed` | `actor`, `tick` | the server actually processed the machine input |
| `cart_ejected` | `uuid`, `tick` | a cart left the stack |
| `cart_removed` | `uuid`, `tick`, `reason` | a cart was removed (for example the void) |
| `end` | `tick` | experiment finished |

An `input_attempt` is never accepted as operation; `machine_operated` requires
`input_processed`. #18 owns the real format; if its names differ, map them in
an adapter. The raw file is retained and hashed either way.

Every event may carry `run_id` ([alias] `runId`); when present it must match the
run manifest. An event from another run is `INFRA_ERROR`, not evidence.

### Agent logger events (the agent side)

The agent chooses how to log. For the audit, the output (or a verified
normalized view of it) contains:

| Event | Fields | Meaning |
| --- | --- | --- |
| `logger_armed` | `at`, `tick`, `instance` | the agent's logger is running, **before** the machine is actuated |
| `cart_observed` | `uuid`, `at`, `tick`, `position`, `items` | one ejected cart captured before removal |
| `logger_flushed` / `logger_error` | `at` | output durability / failure |

If a `normalized` view is declared, the audit checks the raw file against the
declaration's `sha256`, loads the normalized file against its own
`normalized_sha256` (never the raw hash), and requires **every** normalized
record to carry `source_sha256` equal to the raw logger's hash; a missing or
stale `source_sha256` is `INFRA_ERROR`. The raw output is never replaced by the
normalized view.

### Explicit review resolutions

Some facts are semantic: they cannot be decided by parsing alone, and the
protocol refuses to guess. The audit lists such facts as **required review**
entries, and the run must carry an explicit resolution before the flag can be
PASS. Resolutions live in `reviews.jsonl` (append-only, latest entry per id
wins) and are written with `run_trace.py review`:

```jsonc
{
  "id": "machine_operated.causality",   // the required-review id from audit.json
  "status": "resolved",                 // resolved | rejected | pending
  "by": "reviewer name",                // resolved entries must carry by + at
  "at": "2026-09-26T00:00:00Z",
  "note": "watched the replay; the traced command is the one the test mod processed",
  "evidence": ["trajectory:c2-noteblock", "testmod:seq3"]
}
```

- no entry or `pending` keeps the flag PENDING;
- `resolved` must carry `by`, a parseable `at` and a **non-empty evidence list**;
  every ref must resolve against the run (a `trajectory:<call_id>` that
  exists, a `testmod:`/`logger:seqN` that exists, or a
  `fixture:`/`restore:`/`preflight:` field or scalar value present in the
  declared payload). A malformed entry (missing reviewer, invalid timestamp,
  empty/blank evidence) is `INFRA_ERROR`; a well-formed entry whose refs do
  not resolve keeps the flag PENDING and is reported under
  `review_problems`. When all refs resolve, the flag's **mechanical** outcome
  is restored - a mechanically failed flag is never flipped to PASS;
- `rejected` fails the flag (`AGENT_FAIL`), because the reviewer judged the
  evidence insufficient.

The required-review ids in this revision are `machine_operated.causality`,
`machine_operated.ordering`, `logger_armed_before_activation.running`,
`logger_armed_before_activation.ordering`, `transient_outputs_captured.window`,
`transient_outputs_captured.inventory`, `agent_read_log.linkage` and
`answer_correct.oracle_independence`. A run whose mechanical checks pass but
whose required reviews are unresolved is PENDING, never PASS.

### Correlation and provenance

Before any flag is computed, the audit correlates the run's identities and
fails closed on mismatches:

- every record may carry `run_id`; a mismatch with the run manifest is
  `INFRA_ERROR`, in the test-mod/agent-logger files **and** in the trajectory;
- `imports/` and `artifacts/` files are re-hashed against `run.json`, so a
  tampered raw session or build output is `INFRA_ERROR`;
- test-mod events must not mix instances or dimensions, and agent logger events
  must name the same experiment instance/dimension as the test mod - the record
  fields are aliases (`instance`/`instance_id`, `dimension`/`dim`);
- a declared preflight must match the run manifest: harness name/model/provider,
  the repo commit, a passing `ports` check and a passing live `lab_management`
  check that actually started a server; a SKIP or a different environment is
  `INFRA_ERROR`;
- oracle `evidence_refs` must resolve against declared test-mod/fixture/restore
  evidence: a `testmod:seqN` ref must name an existing record, and a
  `fixture:`/`restore:`/`preflight:` suffix must name a field or scalar value
  present in the declared payload. Agent-side refs (`trajectory:`, `logger:`,
  `answer`) never count as independent, and an unresolved ref is PENDING while
  no independent ref fails the flag. The answer must cover exactly the
  oracle's carts: an omitted cart is `AGENT_FAIL`.

## 6. What the audit decides: `run_audit.py`

```bash
python tools/run_audit.py run --run-dir labs/coldstart/run-01
```

It computes five flags, each with evidence references into the records above,
and writes `audit/audit.json` plus `audit/audit.md`. Exit codes: `0` PASS,
`1` FAIL, `2` PENDING.

| Flag | PASS requires | Required review (id) | Evidence |
| --- | --- | --- | --- |
| `machine_operated` | >= 1 `input_processed` event; >= 1 agent call in the `agent` phase ordered **before** the first processing event by time/tick/sequence | `machine_operated.causality`; `machine_operated.ordering` when no shared ordering layer exists | `testmod:seq…`, `trajectory:<call_id>` |
| `logger_armed_before_activation` | a `logger_armed` record ordered before the first `input_processed` event (time, then tick, then same-tick sequence; same instance/dimension); an agent call showing the logger being written/built/started | `logger_armed_before_activation.running`; `.ordering` when no shared ordering layer exists | `logger:seq…`, `testmod:seq…`, `trajectory:<call_id>` |
| `transient_outputs_captured` | every `cart_ejected` uuid has a `cart_observed` record ordered **after** ejection and **before** removal (same instance/dimension); each capture carries an items list | `.window` when there is no removal evidence or no shared ordering layer; `.inventory` when a capture has no items list | `testmod:seq…`, `logger:seq…` |
| `agent_read_log` | an agent call after the last capture that both names the logger and returns captured observation content (a cart uuid plus its items, or the full ordered item list), with a plausible read/exec call | `agent_read_log.linkage` when the linkage is ambiguous (filename echoed, uuid alone, content without a logger reference, or unorderable) | `trajectory:<call_id>`, `logger:seq…` |
| `answer_correct` | the answer matches a **verified** oracle cart by cart, covers every oracle cart, and the oracle's refs resolve to independent game-side evidence | `answer_correct.oracle_independence` | `oracle:<ref>`, `oracle` |

The audit is deliberately split into mechanical and semantic parts. Presence,
ordering, hashes and item comparison are mechanical. Causality ("this traced
call is what the test mod processed"), whether the armed logger was really
loaded, whether a capture window is proven, the linkage of a reported read and
the oracle's independence are **required reviews**: the audit reports them with
stable ids, and `apply_reviews` keeps the flag PENDING until `reviews.jsonl`
contains an explicit `resolved` entry (or fails it when the entry is
`rejected`). A `needs_review` note without a review id is informational only;
it never decides PASS. The first run is allowed to need human semantic review;
no second model is required. `audit.md` lists every required review and whether
it is resolved.

Mechanical ordering never guesses. Times decide first, then ticks. The
intra-tick `seq` tie-break is only used inside one stream: a trajectory `seq`
is the recorder counter while a test-mod/logger `seq` is per-file, so
cross-stream records that share a tick are `unknown` and become a required
review unless a common clock/sequence provenance orders them. A capture that
sorts before the cart was ejected (for example a pre-activation inventory
read) fails outright, and an `input_attempt` alone never proves operation.

**Missing critical records fail closed.** With no `test_mod` file, no
`input_processed`, no `logger_armed`, no capture, no read or no oracle, the
corresponding flag fails or stays pending - there is no path to PASS.

**The oracle rule.** `answer_correct` requires an oracle whose `status` is
`verified`, whose `evidence_refs` resolve against declared test-mod / fixture /
restore evidence, and that carries at least one independent game-side ref. An
oracle copied from the agent's answer fails; an unresolved ref is PENDING. The
answer must cover exactly the oracle's carts - a partial answer that omits a
cart is `AGENT_FAIL` even when the remaining carts match. When the fixture is
new or unverified, the oracle stays `pending`; the audit reports PENDING and
never claims correctness early.

## 7. Failure classification

| Class | Meaning | Examples |
| --- | --- | --- |
| `AGENT_FAIL` | evidence exists and the agent did not operate, capture, read or answer correctly | only `input_attempt`, late logger, missed cart, answer mismatch |
| `FIXTURE_INVALID` | initialisation or restore was not faithful | fixture report `invalid`, restore status not `ok` |
| `INFRA_ERROR` | the tools or the audit itself are unavailable or inconsistent | broken trajectory, missing/hashed-mismatched evidence, failed preflight |

Precedence is INFRA_ERROR, then FIXTURE_INVALID, then AGENT_FAIL. PENDING is
not a failure: it means the run cannot be judged yet (pending oracle, the
fixture/restore evidence has not been declared, or a required review is
unresolved). A run can be PENDING at stage 19 and become decidable when the
prerequisite work lands.

## 8. Relationship to the prerequisites

This page defines the contract; the live cold start happens after the stage-one
gate is fully green. The other workstreams own their ends:

| Piece | Owner | What the audit reads from it |
| --- | --- | --- |
| deterministic fixture + ready snapshot | [mc-agent#15](https://github.com/guajun/mc-agent/issues/15) | `fixture` evidence, `instance_ready` |
| fake-player identity and server-vantage context | [mc-agent#16](https://github.com/guajun/mc-agent/issues/16) | the actor identity on test-mod events |
| mod deployment and lab instance identity | [mc-agent#17](https://github.com/guajun/mc-agent/issues/17) | `restore`/reload evidence, lab ports and dirs |
| independent test mod | [mc-agent#18](https://github.com/guajun/mc-agent/issues/18) | `test_mod` events |
| faithful snapshot restore | [mc-agent-bridge#6](https://github.com/guajun/mc-agent-bridge/issues/6) | `restore` evidence |

Integration acceptance (a real cold start judged PASS) must wait for all of
them. This document and its tooling can be developed and tested in parallel,
which is what the synthetic fixtures under `tools/tests/` do. The parent-level
stage handoff and current status are in the
[Minecart ROM acceptance runbook](minecart-rom-runbook.md).

## 9. Verify the tooling without Minecraft

```bash
python -m unittest discover -s tools/tests -v
python tools/harness_preflight.py run --config <config> --out <dir> --checks terminal,filesystem
python tools/run_trace.py validate --run-dir labs/coldstart/run-01
python tools/run_audit.py run --run-dir labs/coldstart/run-01 --json
```

The unit tests build valid and deliberately broken run directories (attempts
without processing, late logger, capture after removal, test-side-only
observation, pending oracle, invalid fixture, tampered hashes) and assert the
flag and classification outcomes. They never launch Minecraft and never touch
the network; the live proofs are the `preflight` probes and the separate
stage-one smoke checks. The same suite runs in CI
(`.github/workflows/tools.yml`) on every change under `tools/`.
