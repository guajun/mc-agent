# Stage-one integration evidence

`full-gate-summary.json` (version 1) is the durable, compact record of the
**full issue-#14 acceptance run** on branch `codex/rom13-fullgate`: the merged
#15 fixture, the same-run guarded restore, the positive/negative audit
sessions, the five smoke suites, the finalized trajectory and the gate verdict
**pass 8/8** (exit 0, run id `rom13-fullgate-20260925T210725Z`). It pins the
sha256 of every artifact the gate consumed (bundle tree, evidence index,
canonical audit events, tool trace, joins, snapshots, trajectory, positive log)
plus the unchanged source-world tree hash. Raw artifacts stay under the
git-ignored `labs/fullgate-evidence/` directory of the worktree that produced
them, so a reviewer can spot-check any pinned hash against the raw file.

`integration-summary.json` is the earlier durable record of the combined
stage-one integration run (two real labs, smoke mod, committed #18 audit mod,
tool trajectory, gate verdict) before the #15 fixture landed; the gate was
honestly `blocked` there, and that record is kept as history. The raw files of
that run stay under the git-ignored `labs/rom13-integration/` directory in the
worktree that produced them:

```
labs/rom13-integration/
  summary.json, summary.md          driver report + step table
  run-<stamp>/trajectory.jsonl      #19 append-only trajectory
  run-<stamp>/trajectory.final.jsonl frozen copy; only this is ever mapped
  normalize-inputs.json             pinned inputs (hashes) for bundle-only reproduction
  bundle/                           gate bundle, mapping reports, assemble report
  bundle/artifacts/...              canonical gate artifacts (audit events, trace, identity)
  bundle-spec.json                  run/instance declarations used for the gate
  build/                            smoke-mod v1/v2 jars + build metadata
  audit-mod-src/                    read-only copy of the committed #18 source (502f561)
  bridge6-restore-collection/       verified bridge6 snapshots + collection report
labs/rom13-exp/mc-audit/            raw #18 audit JSONL + status/config
```

The summary pins the sha256 of the raw audit JSONL and trajectory, so a raw
copy can be verified against it. To reproduce without a game server (byte-identical bundle from the frozen
inputs, all pinned hashes re-checked):

```bash
python tools/stage1_evidence.py selftest
python tools/stage1_evidence.py restore-evidence --out labs/rom13-integration/bridge6-restore-collection
python tools/stage1_integration.py plan
python tools/stage1_integration.py run --bundle-only
```

To reproduce end to end (needs Java 25, the Minecraft 26.2-Fabric install and
network for the first Fabric download):

```bash
python tools/stage1_integration.py run
```

This run is **not** ROM acceptance: the scene is a generic note block and one
stack of chest minecarts, the fixture and restore prerequisites have not
landed, and the gate therefore reports `blocked`.
