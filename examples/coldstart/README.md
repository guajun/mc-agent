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
