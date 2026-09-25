# Stage-one integration evidence

`integration-summary.json` is the durable, compact record of the combined
stage-one integration run (two real labs, smoke mod, committed #18 audit mod,
tool trajectory, gate verdict). The raw files stay under the git-ignored
`labs/rom13-integration/` directory in the worktree that produced them:

```
labs/rom13-integration/
  summary.json, summary.md          driver report + step table
  run-01/trajectory.jsonl           #19 canonical trajectory (all operator commands)
  bundle/                           gate bundle, mapping reports, assemble report
  bundle/artifacts/...              canonical gate artifacts (audit events, trace, identity)
  bundle-spec.json                  run/instance declarations used for the gate
  build/                            smoke-mod v1/v2 jars + build metadata
  audit-mod-src/                    read-only copy of the committed #18 source (502f561)
labs/rom13-exp/mc-audit/            raw #18 audit JSONL + status/config
```

The summary pins the sha256 of the raw audit JSONL and trajectory, so a raw
copy can be verified against it. To reproduce without a game server:

```bash
python tools/stage1_evidence.py selftest
python tools/stage1_integration.py plan
python tools/stage1_integration.py run --bundle-only   # re-normalize the last run
```

To reproduce end to end (needs Java 25, the Minecraft 26.2-Fabric install and
network for the first Fabric download):

```bash
python tools/stage1_integration.py run
```

This run is **not** ROM acceptance: the scene is a generic note block and one
stack of chest minecarts, the fixture and restore prerequisites have not
landed, and the gate therefore reports `blocked`.
