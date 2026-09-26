# Cold-start audit - rom20-20260926T063100Z

- schema: `mc-agent-coldstart-audit/1`
- audited at: 2026-09-26T06:51:20.569Z
- **overall**: PENDING

## Required flags

| flag | status | summary | evidence |
| --- | --- | --- | --- |
| `machine_operated` | PENDING | mechanically satisfied but awaiting explicit review: machine_operated.causality | `testmod:seq953`<br>`trajectory:call_00_ET_Z8Jf2dzy92rgwL9OifKQ1547` |
| `logger_armed_before_activation` | PENDING | mechanically satisfied but awaiting explicit review: logger_armed_before_activation.running | `logger:seq10`<br>`testmod:seq953`<br>`trajectory:call_01_WZij1nfMJFeTereaDDak0836` |
| `transient_outputs_captured` | PASS | 5 capture(s) cover 5 ejected cart(s) | `testmod:seq954`<br>`logger:seq11`<br>`testmod:seq964`<br>`logger:seq12`<br>`testmod:seq974`<br>`logger:seq13`<br>`testmod:seq984`<br>`logger:seq14`<br>`testmod:seq994`<br>`logger:seq15` |
| `agent_read_log` | PASS | the agent's own trajectory read the logger output (content linkage to the named agent logger after the last capture) | `trajectory:call_00_hjdNlRHJlnwjYpsOpZRy1938`<br>`logger:seq15` |
| `answer_correct` | PENDING | mechanically satisfied but awaiting explicit review: answer_correct.oracle_independence | `oracle`<br>`oracle:testmod:seq954`<br>`oracle:testmod:seq964`<br>`oracle:testmod:seq974`<br>`oracle:testmod:seq984`<br>`oracle:testmod:seq994`<br>`oracle:testmod:seq1010` |

## Required review (must be resolved before PASS)

- `machine_operated.causality` (machine_operated): confirm the traced call is what the test mod processed, not a coincidental call in the same window - **unresolved**
- `logger_armed_before_activation.running` (logger_armed_before_activation): confirm from game-side load/restart evidence that the armed logger was actually running, not only written - **unresolved**
- `answer_correct.oracle_independence` (answer_correct): confirm the oracle's evidence chain (fixture plus test mod) is independent of the agent answer - **unresolved**

## Needs human semantic review

- 10 input attempt(s) exist; attempts alone must not be accepted as operation
- items and order still need comparison against the server-side entity NBT (see answer_correct/oracle)

## Inputs

- run: `run.json`
- trajectory: `344 records`
- test_mod: `F:\mc-agent-worktrees\rom13\coldstart\labs\rom20-20260926T063100Z\operator\post-task-derived\evidence\testmod.experiment.jsonl`
- agent_logger: `F:\mc-agent-worktrees\rom13\coldstart\labs\rom20-20260926T063100Z\operator\post-task-freeze\agent-logger-raw\romlog.jsonl`
- answer: `F:\mc-agent-worktrees\rom13\coldstart\labs\rom20-20260926T063100Z\operator\post-task-derived\evidence\answer.json`
- oracle: `F:\mc-agent-worktrees\rom13\coldstart\labs\rom20-20260926T063100Z\operator\post-task-derived\evidence\oracle.json`
- fixture: `F:\mc-agent-worktrees\rom13\coldstart\labs\rom20-20260926T063100Z\operator\fixture\ready-snapshot.json`
- restore: `F:\mc-agent-worktrees\rom13\coldstart\labs\rom20-20260926T063100Z\operator\post-task-derived\evidence\restore-evidence.json`
- review: `reviews.jsonl`
