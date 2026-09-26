# Cold-start inputs

Two files that belong together:

- `harness-pi.json` - the fixed harness/model environment contract checked by
  `tools/harness_preflight.py`. It names the harness command, model strings,
  source endpoints, minimum JDK, bridge command and the reserved ports
  (27190-27199). Machine-specific paths are supplied with `--set` instead of
  editing the file.
- `task-minecart-rom.md` - the exact task input for the Minecart ROM cold
  start. It contains the task and the requirement to really run the note-block
  machine, and nothing else: no answer, no solution script, no prior
  trajectory.

Protocol and required evidence: [Auditable cold-start runs](../../docs/coldstart-protocol.md).

## Operator preparation for a fresh run (rom20)

`prepare-rom20.py` and `rom20-environment.json` are the tracked operator recipe
for a repeatable fresh environment. They are **not** agent context. One full
run verifies the pinned toolpaths and read-only source save, prepares the clean
workspace (`TASK.md`, `ENV.md`, the generic kit skill) under
`labs/<run-id>/workspace`, opens `labs/<run-id>/run` with `run_trace.py init`,
runs the generic full harness preflight on the reserved ports, fetches the
pinned map ZIP from the remote with a cold cache, initializes a fresh sealed
challenge in `rom20-src`, provisions `rom20-exp` blank, starts one detached
clean bridge per lab and writes the outcome report. The operator never launches
the model:

```powershell
F:/mc-agent/.venv/Scripts/python.exe examples/coldstart/prepare-rom20.py run `
    --run-id rom20-20260926T060500Z
```

The run root keeps the operator receipts and the sealed challenge; the clean
agent context is only the four files in `labs/<run-id>/workspace`. See the
generated `meta20-prepared.json` next to the run for the exact launch command
and live resource state.
