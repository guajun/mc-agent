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

**Host-specific inputs.** `rom20-environment.json` records the absolute paths of
the host that prepared the historical run (worktree root, venv Python, Java/JDK,
pi, the clean bridge/agent-loop checkouts, jars, source save, report outputs).
It is a record, not a portable default: on another host or checkout, copy it
(for example to an untracked `hosts/<name>.json`), set `repo.root` to that
checkout, adjust the other absolute paths and pass `--env <file>` (or set
`ROM20_ENV`). The script refuses to run when `repo.root` is not the checkout it
lives in, and it fails closed when the bridge/agent-loop checkouts are not
exactly at their pinned commits and clean.

**Fresh run ids only.** A run root that already exists is rejected: attempts and
their receipts are never overwritten (there is no `--force`). Pick a new
`--run-id`; the run id must be a single path-free directory name (letters,
digits, `.`, `-`, `_`) and is resolved under `labs/` before anything is created
or removed.

**`--stages` is a diagnostic subset, not a resume.** It runs the named stages
in order inside one process (for example `--stages clean,context,source`), and a
later stage that needs an earlier stage's in-process receipt fails with an
explicit message instead of reusing stale on-disk state.

The generated launch command is real PowerShell and names the planned raw
session with an explicit `--session` (pi 0.87.1 selects the session file with
that flag; `PI_SESSION_FILE` is only a marker that pi injects into shell-tool
commands). The historical rom20 environment was prepared from source revision
`1deb735`, and the coordinator launched the live agent with the equivalent
explicit-session command; this post-review source revision has not executed
that run. See the generated `meta20-prepared.json` next to the run for the exact
launch command and live resource state.
