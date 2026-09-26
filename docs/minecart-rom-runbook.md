# Minecart ROM acceptance runbook

This is the parent-level path for [issue #13](https://github.com/guajun/mc-agent/issues/13):
from tooling readiness ([#14](https://github.com/guajun/mc-agent/issues/14))
through one audited real-agent cold start
([#20](https://github.com/guajun/mc-agent/issues/20)) to a frozen real-game
regression ([#21](https://github.com/guajun/mc-agent/issues/21)). It records
the **current** state and the exact handoff commands; the detailed contracts
live in the linked pages.

**This page is not evidence.** A step counts only when it was actually run and
its raw artifacts are retained. Nothing in this repository may mark an
unfinished gate or issue as done, and no command below should be reported as a
pass until the gate or audit prints it.

Detailed contracts:

* gate checks and schema - [stage-one integration gate](stage1-gate.md)
* combined integration driver - [stage-one integration run](stage1-integration.md)
* stage-two run entry, trajectory and judgement -
  [auditable cold-start runs](coldstart-protocol.md)
* labs, mod build/deploy, forks and identity -
  [lab servers](lab-server.md), [building a mod](mod-building.md),
  [fork verification](fork-verify.md), [player identity](player-identity.md)

## Stages, entry conditions and exit evidence

| Stage | Issue | May start when | Exit evidence | Must not |
| --- | --- | --- | --- | --- |
| 1. Toolchain gate | [#14](https://github.com/guajun/mc-agent/issues/14) | prerequisites #15, bridge#6, #16-#19 developed | `stage1_gate.py check` **PASS** on a live bundle, plus coordinator review of the raw hashes | require stage-two agent facts; pre-write the ROM solution or the agent's logger |
| 2. Audited cold start | [#20](https://github.com/guajun/mc-agent/issues/20) | stage-one gate PASS reviewed; a fresh unseen fixture program | one run directory with the five audit flags PASS and independent test-mod evidence, from a fresh model context | receive this design history, the calibration answer or a pre-written logger; relabel stage-one evidence as agent evidence |
| 3. Frozen regression | [#21](https://github.com/guajun/mc-agent/issues/21) | a **reviewed, actually successful** #20 at a pinned head | real-game regression: >= 3 fresh instances, >= 2 calibrated programs, >= 1 cold download, negative cases | replay old logs; claim model autonomy from a scripted pass |

Dependency direction: stage-one evidence is produced by test-side tooling
**before** #20; #20 adds the agent's own operations, logger and answer; #21
extracts the reviewed #20 artifacts into a parameterized regression.

## Current status (verified 2026-09-26, repo head `9b028d3`)

| Item | State | Evidence |
| --- | --- | --- |
| stage-one gate tool | merged (#22) | `tools/stage1_gate.py`; `selftest` **152 checks** |
| cold-start protocol tools | merged (#25) | `tools/coldstart.py`, `harness_preflight.py`, `run_trace.py`, `run_audit.py` |
| combined integration driver | merged (#28, `2675825`) | `tools/stage1_integration.py`; [stage-one integration run](stage1-integration.md) |
| full gate acceptance driver | on branch `codex/rom13-fullgate` (PR #30 for #14), review-fix run | `tools/stage1_fullgate.py`; real bounded ROM calibration + no-mod parity + post-restart memory verify; [full gate summary](evidence/rom13-stage1/full-gate-summary.json) |
| fixture #15 | merged (#27, `8dd75c6`) | `examples/minecart-rom/` |
| audit mod #18 | merged (#26, `824c15d`) | `tests/mods/minecart-audit/` |
| live stage-one gate | **PASS 8/8** (exit 0) on branch `codex/rom13-fullgate`, pending human review | [`docs/evidence/rom13-stage1/full-gate-summary.json`](evidence/rom13-stage1/full-gate-summary.json) |
| stage #20 | packed for independent review, **not accepted** | one fresh model session (pi 0.87.1 / deepseek-flash) at `1deb735`; run + own logger + canonical trajectory in [`docs/evidence/rom20-coldstart/README.md`](evidence/rom20-coldstart/README.md); `run_audit` 2 PASS / 3 PENDING semantic reviews |
| stage #21 | not prepared | - |
| source save | unchanged through the full gate run | tree hash `8cd54c86...324a` (40 files, 11,556,310 bytes) |

Merged prerequisite PRs: bridge#8, bridge#9, #22, #23, #24, #25,
#26, #27, #28, #29. Issues #16, #17 and bridge#6 are closed with evidence
comments. The full gate pass above is the acceptance run for #14; only a
reviewed `pass` authorizes stage two, and the agent does not merge it.

The historical notes below were written when #15/#18 were still snapshots on
open branches. They are kept as history; the accepted heads are now merged in
`main` and are pinned in the full gate summary.

The merged heads above are the accepted inputs for the full gate run. Before
re-running a recipe, re-resolve them, for example
`gh pr view 27 --repo guajun/mc-agent --json state,headRefOid`, and check that
the tree still matches the pinned hashes in the full gate summary.

!!! note "Tracker history (historical record, 2026-09-25)"
    #14 and #19 were briefly closed right after the tooling PRs merged,
    then **reopened** at 18:26 UTC the same day once the still-blocked live gate
    and the unchecked #14 checklist were pointed out. This is history, not a
    status: an issue's state is never acceptance, and no closure should precede
    a reviewed gate `pass`.

## Stage 1: gate checks and the circularity guard

The gate ([implementation](stage1-gate.md)) evaluates a *live* bundle and
fails closed. The eight checks and who can produce each one **without the
stage-two agent**:

| Check | Owner | Produced by | Stage-one satisfiable |
| --- | --- | --- | --- |
| `fixture_map` | #15 | fixture runner: download, init, validate, evidence | yes |
| `restore_fidelity` | bridge#6 | guarded restore + `stage1_evidence.py restore-evidence` | yes |
| `player_context` | #16 | live identity probes (`docs/evidence/rom13-meta16`) | yes |
| `agent_dev_capability` | #17, #19 | `harness_preflight.py` (`tool-environment`) + smoke-mod build/deploy | yes, no model call |
| `independent_test_mod` | #18 | audit mod live smoke + negative cases | yes |
| `trace_persistence` | #19 | generic smoke tool calls + explicit-proof joins | yes, see below |
| `smoke_fixture_validity` | #14 | smoke suites, fixture calibration, version lock, evidence index | yes |
| `evidence_integrity` | #14 | computed by the gate | yes |

**The gate does not ask for stage-two facts.** It reads a bundle of files; it
never reads a model transcript, and it has no harness/session input. The
`phase: "agent"` in a canonical audit event is the audit lifecycle's
*experiment phase* - a test-side generic smoke call belongs to that phase just
as an agent call would. The stage-two-only facts (`agent_read_log`,
`answer_correct`, the agent's own logger, its tool-to-game joins) are checked by
`tools/run_audit.py` in the #20 run, **not** by this gate.

The one subtle requirement is the trace join:

* `trace-join.json` must join every `phase: "agent"` audit event to a traced
  tool call, with `verified: true`; the adapter promotes a candidate only with
  an explicit `proof` (`producer`, `basis`, `clock`) - see
  [the honest-join rule](stage1-integration.md#joins-verified-only-with-explicit-proof).
* The 2026-09-25 integration run has 71 traced calls, **135 candidates and 0
  verified joins** (`bundle/mapping/join-candidates.json`): the #18 audit
  events do not carry the issuing tool-call id, so RCON attribution stayed a
  candidate and the artifact was withheld.
* The fix must stay on the test side: record a receipt that ties the traced
  smoke call to the audit event (for example the audit command returning the
  sequence it logged, or an explicit `--joins` proof naming the command, the
  operator UUID and the shared clock). Never set `verified: true` by hand.

If a stage-one check can only be satisfied by #20's agent, that is an
implementation bug in the stage-one evidence path - fix the test-side smoke or
adapter and document it. It is never a reason to skip stage one or to feed the
gate stage-two prose.

## Stage 1: exact recipe

All paths are relative to this repository unless shown otherwise. `labs/` is
git-ignored raw evidence; keep it and never rewrite an old run.

### 1.1 Gate scaffolding and catalog

```bash
python tools/stage1_gate.py list
python tools/stage1_gate.py scaffold labs/stage1-evidence
python tools/stage1_gate.py selftest
```

### 1.2 Combined integration run

```bash
python tools/stage1_integration.py plan                   # ports, java, steps
python tools/stage1_integration.py run                    # live, ~4 min warm
python tools/stage1_integration.py run --bundle-only      # byte-reproducible re-normalize
```

Defaults and outputs (verified against `stage1_integration.py`):

* ports `27240-27249` (`rom13-src` source_audit 27240-27243, `rom13-exp`
  experiment 27244-27247);
* JDK 25 default:
  `C:\Users\MSI-NB\AppData\Roaming\.hmcl\java\windows-x86_64\mojang-java-runtime-epsilon`;
* the interface-mod jar is hash-pinned to
  `45f12e16b3979be6a699ac3c744b2a68dfcf8dd2379f5987bf9b9319adf4404f` and is
  read from the #16/#18 peer build directories; the driver refuses a different
  hash;
* the integration root `labs/rom13-integration/` holds `normalize-inputs.json`,
  `bundle/` (with `bundle/bundle-spec.json`), `summary.json`/`summary.md`,
  `build/`, `audit-mod-src/` and `bridge6-restore-collection/`; the live run
  directory `labs/rom13-integration/run-<stamp>/` holds `trajectory.jsonl` and
  the frozen `trajectory.final.jsonl` (plus `run.json`, `task.md`,
  `evidence.json`, `visibility.json`); see
  [`docs/evidence/rom13-stage1/README.md`](evidence/rom13-stage1/README.md).

The gaps a re-run must fill from accepted prerequisite output are: the #15
fixture evidence, the same-run restore artifacts, test-mod completion (manifest,
negative cases, audit lifecycle, missing-log detection), the stage-one harness
`tool_environment` and smoke-suite/version-lock/evidence-index set, and the
verified joins described above.

### 1.3 Fixture (#15, after PR #27 is accepted)

This recipe was verified against the PR #27 snapshot `37fb824` (2026-09-26);
after that snapshot the branch was still under repair, so it is **not** on
`main` yet. Before running, resolve the actual accepted/merged head
(`gh pr view 27 --repo guajun/mc-agent --json state,headRefOid`) and re-check
the runner flags against that tree. The recipe below is from that inspected
snapshot tree:

```powershell
python examples/minecart-rom/runner/minecart_rom.py fetch --cold
python examples/minecart-rom/runner/minecart_rom.py up --lab rom15 `
    --rcon-port 27150 --server-port 27151 --vantage-port 27152 --bridge-port 27153 `
    --interface-mod labs/_cache/mods/mc-agent-interface-0.6.0.jar `
    --java "<jdk25>/bin/java.exe" --memory 3G
python examples/minecart-rom/runner/minecart_rom.py init --lab rom15 `
    --records examples/minecart-rom/calibration/records/run-01
python examples/minecart-rom/runner/minecart_rom.py validate --lab rom15 `
    --ready examples/minecart-rom/calibration/records/run-01/ready-snapshot.json
python examples/minecart-rom/runner/minecart_rom.py evidence --out labs/stage1-evidence `
    --records <records dir> --instance-id <instance> --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
```

`init` refuses a bad state, a no-longer-frozen world, a changed cart NBT or a
changed tick order (`READY` still requires the live re-validation). The
`challenge` subcommand generates a fresh sealed program for evaluation
(`--seed`, `--out` outside the repository); the program is *input*, not the
answer.

### 1.4 Restore, identity, trace, audit evidence

```bash
python tools/stage1_evidence.py identity \
    --source docs/evidence/rom13-meta16/live-identity.json \
    --bundle labs/rom13-integration/bundle
python tools/stage1_evidence.py trace \
    --trajectory labs/rom13-integration/run-<stamp>/trajectory.final.jsonl \
    --bundle labs/rom13-integration/bundle \
    --run-id <run id> --instance-id rom13-exp --instances rom13-src,rom13-exp
python tools/stage1_evidence.py audit \
    --log labs/rom13-exp/mc-audit/audit-<run id>.jsonl \
    --bundle labs/rom13-integration/bundle \
    --run-id <run id> --instance-id rom13-exp --dimension minecraft:overworld
python tools/stage1_evidence.py assemble --bundle labs/rom13-integration/bundle \
    --spec labs/rom13-integration/bundle/bundle-spec.json \
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
```

`restore-evidence` verifies the merged bridge#6 collection; it is a **different
live run** and must never be presented as the new run's same-run restore
evidence (same-run snapshots/records are still required). The collection
command is
`python tools/stage1_evidence.py restore-evidence --out labs/rom13-integration/bridge6-restore-collection`.

### 1.5 Gate verdict

```bash
python tools/stage1_gate.py check labs/rom13-integration/bundle \
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test" \
    --report stage1-report.json
```

Exit codes `0` pass, `1` fail, `3` blocked, `2` usage. `--skip-source-rehash`
always blocks; a missing declared artifact blocks; a pass additionally needs
the coordinator to review the raw evidence (the gate cannot prove that raw
logs were not fabricated). Only then may #20 start.

### 1.6 Harness capability (stage-one artifact, no model)

`harness_preflight.py` proves the environment *before* a model is spent, and
its report is the source for the gate's `tool-environment.json`:
harness/model/provider, terminal, filesystem, ports, source fetch, JDK 25
build/install, bridge CLI (MCP optional), bridge smoke and lab management.

```bash
python tools/harness_preflight.py list
python tools/harness_preflight.py run --config examples/coldstart/harness-pi.json \
    --out labs/coldstart/preflight \
    --run-dir labs/coldstart/run-20-01 \
    --set "build.java=<jdk25>/bin/java.exe" \
    --set "bridge.command=<mc-bridge executable>"
```

Outputs: `preflight.json`, `preflight.md`, and per-check transcripts under
`probes/`. The reference config reserves `27190-27199` and currently records
model `deepseek-flash`, provider `deepseek`. `SKIP` never counts as `PASS`.

## Stage 2: exact recipe for the fresh cold start

Preconditions: stage-one gate PASS reviewed; a new sealed challenge program
generated for this run (never the public calibration default); the fixture
ready and validated; the #18 audit mod loaded on both labs; a fresh pi session.
The fixture runner itself comes with #15 (PR #27; snapshot and head resolution
in section 1.3) - stage two cannot start before that is accepted. Example ports
below stay inside the coldstart config's reserved `27190-27199` range.

### 2.1 Pinned harness defaults

| Item | Value |
| --- | --- |
| pi | `C:\Users\MSI-NB\.pi\agent\bin\pi.cmd` (0.87.1) |
| provider / model | `deepseek` / `deepseek-flash` (settings default) |
| thinking | `max` (`PI_REASONING_LEVEL`, settings default) |
| session file | `$PI_SESSION_FILE`, also passed explicitly with `--session` |
| harness config | `examples/coldstart/harness-pi.json` |
| task input | `examples/coldstart/task-minecart-rom.md` (no answer, no script) |

### 2.2 Clean-context rules

* Record what the session can see with `run_trace.py init --visible-root` and
  `--exclude`; the world's raw memory data (full NBT, tick order, inventory) is
  deliberately **not** hidden, but this design history, the calibration
  records, the answer, any prior solution script and any pre-written logger
  must not be visible or preloaded.
* There is no capability sandbox: full filesystem/source/tools remain allowed.
  Visibility is a recorded fact, not a filter.
* Use a challenge program different from the public calibration and keep its
  file outside the repository.

### 2.3 Ordered commands

```powershell
# 1. fresh unseen challenge for this cold start (evaluation-side input)
python examples/minecart-rom/runner/minecart_rom.py challenge --seed <fresh> --out <outside repo>/challenge.json
python examples/minecart-rom/runner/minecart_rom.py up --lab rom20 `
    --rcon-port 27194 --server-port 27195 --vantage-port 27196 --bridge-port 27197 `
    --interface-mod labs/_cache/mods/mc-agent-interface-0.6.0.jar --java "<jdk25>/bin/java.exe"
python examples/minecart-rom/runner/minecart_rom.py init --lab rom20 --program <outside repo>/challenge.json `
    --records labs/rom20/records
python examples/minecart-rom/runner/minecart_rom.py validate --lab rom20 --ready labs/rom20/records/ready-snapshot.json

# 2. open the run BEFORE the model session
python tools/run_trace.py init --run-dir labs/coldstart/run-20-01 --run-id rom13-stage20-01 `
    --task-file examples/coldstart/task-minecart-rom.md `
    --harness pi --model deepseek-flash --provider deepseek --harness-version 0.87.1 `
    --task-player-uuid <fixture fake-player uuid> `
    --visible-root <experiment workspace> --exclude <design/calibration paths>

# 3. preflight (also appends a mark to the run)
python tools/harness_preflight.py run --config examples/coldstart/harness-pi.json `
    --out labs/coldstart/run-20-01/preflight --run-dir labs/coldstart/run-20-01 `
    --set "build.java=<jdk25>/bin/java.exe" --set "bridge.command=<mc-bridge executable>"

# 4. fresh model session (default provider/config). Run it from a clean
#    worktree: --no-context-files prevents AGENTS.md/CLAUDE.md auto-loading,
#    and visibility.json must still record exactly what the session can see.
C:\Users\MSI-NB\.pi\agent\bin\pi.cmd --provider deepseek --model deepseek-flash --thinking max `
    --no-context-files --print --session F:\...\rom13-stage20-01-session.jsonl `
    "@examples/coldstart/task-minecart-rom.md"

# 5. import the agent's own trajectory, then declare evidence and audits
python tools/run_trace.py import-pi --run-dir labs/coldstart/run-20-01 --session "$env:PI_SESSION_FILE" --phase agent
python tools/run_trace.py validate --run-dir labs/coldstart/run-20-01
python tools/run_audit.py run --run-dir labs/coldstart/run-20-01 --json
```

The audit reads `evidence.json` declarations (`agent_logger`, `test_mod`,
`answer`, `oracle`, `fixture`, `restore`, `preflight`, `review`), computes the
five flags and prints `audit/audit.json` + `audit/audit.md`. Exit codes:
`0` PASS, `1` FAIL, `2` PENDING. The required semantic reviews
(`machine_operated.causality`, ...) are recorded with `run_trace.py review`;
an unresolved review keeps a flag PENDING and is never a pass. Missing critical
records fail closed.

Copy the exact command flags from
[auditable cold-start runs](coldstart-protocol.md) when this page and that one
ever disagree - that page is the contract.

## Stage 3: preconditions (do not start early)

#21 may only start after a **reviewed, actually successful** #20 at a pinned
head. Its acceptance needs real game data each run (no old-log replay, no model
required), three fresh instances, at least two calibrated programs, at least
one cold-download path, the negative cases listed in the issue, same-size jar
iteration with deploy/restart/re-restore, and results labelled separately as
"frozen regression result" versus "original cold-start capability evidence".
The stage-2 agent's logger is only written after a real success - never in
advance.

## Exact remaining handoff steps

1. Finish review and merge PR #26 (#18) and PR #27 (#15); do not treat either
   head as accepted before that.
2. Extend the integration run's bundle with the now-available artifacts:
   `tool_environment` (from `harness_preflight.py`), `smoke_mod` (generic
   build/deploy/restart), fixture evidence, same-run restore snapshots/record,
   test-mod manifest/negative cases/lifecycle, `missing-log-detection`, the
   smoke/calibration/version-lock/evidence-index set, and verified joins with
   explicit proof.
3. Re-run `python tools/stage1_integration.py run` (or `--bundle-only` for
   frozen inputs), then `stage1_gate.py check ... --source-world ... --report`.
4. Post the report and raw artifact locations to issue #14; the coordinator
   reviews hashes and raw logs. Only a reviewed full `pass` authorizes #20.
5. Keep #14/#19 open (reopened 2026-09-25) until a reviewed full `pass`; do
   not close either on a tooling merge, and never cite a tracker state as
   acceptance.
6. Prepare the fresh #20 environment per section 2 (new challenge, clean
   context, recorded visibility, no pre-written logger) and run it as a
   separate pi session once the gate pass is reviewed.
7. Only after #20 is audited as a real success, extract #21 from its artifacts
   under the section 3 conditions.

## Status integrity rules

* Keep every failed attempt and its raw logs; classify
  `AGENT_FAIL` / `FIXTURE_INVALID` / `INFRA_ERROR` with the precedence
  `INFRA_ERROR` > `FIXTURE_INVALID` > `AGENT_FAIL`.
* Never relabel another run's evidence as this run's (the merged bridge#6
  collection is a different live run; stage-one evidence is not agent
  evidence).
* Never mutate the source save or the original dirty checkouts; experiments
  run on disposable lab copies.
* A closed GitHub issue is not acceptance; a green selftest is not a live
  gate; a scripted pass is not model autonomy.

See also: [stage-one integration gate](stage1-gate.md),
[stage-one integration run](stage1-integration.md),
[auditable cold-start runs](coldstart-protocol.md),
[mod building](mod-building.md), [lab servers](lab-server.md),
[fork verification](fork-verify.md), [tools](tools.md).
