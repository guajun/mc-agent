# Stage-one integration evidence

`full-gate-summary.json` (version 3) is the durable, compact record of the
**full issue-#14 acceptance run** on branch `codex/rom13-fullgate`: the merged
#15 fixture, three live initializations, the same-run guarded restore, the
**bounded real ROM calibration** on the source and restored copies, the
like-for-like no-mod parity, the real hit/miss identity probe, the real
dev-capability probes (including the post-restart memory verify), the causal
tool/game joins, the five smoke suites and the gate verdict **pass 8/8**
(exit 0, run id `rom13-fullgate-20260926T044858Z`, executed source commit
`6e0091081aa7ccc2250d968415d5c5895b84e6f9`). Its values (order hash
`790ea415…`, 61 projected trace rows, 109.835 ms total session hook time,
bundle tree `7c3d872f…`) are the authoritative reference used by the
integration pages. It pins the sha256 of every artifact the gate consumed
(bundle tree, evidence index, canonical audit events, tool trace, joins,
snapshots, trajectory, positive log) plus the unchanged source-world tree hash
and the per-phase executed-source provenance. Raw artifacts stay under the
git-ignored `labs/fullgate-evidence/` directory of the worktree that produced
them, so a reviewer can spot-check any pinned hash against the raw file.

`attack-use-window-probe.json` is the compact record of the supplemental
stage-two prerequisite probe: `tools/attack_use_window_probe.py` (driver
commit `5f135267947e91910ab70a8740f07c36a481a8a8`, raw log sha256 starts
`9269bce8…`) sent a punch and a use over one persistent RCON connection on a
fresh disposable lab with the unchanged audit jar. The raw requests are **1
tick apart** (<= `correlationWindowTicks: 2`), exactly one `input_processed`
(`playNote`) belongs to the use, and no processed event is attributed to the
attack. The probe is supplemental; the frozen full-gate bundle and its
provenance are untouched.

## Failed attempts (retained)

The failed attempts are preserved under `labs/fullgate-evidence/` and
`labs/fullgate-logs/` and are part of the honest record (none was resolved by
weakening a check): the dirty/forceloaded fixture world copies, the audit
mod's missing `triggerEvent` coverage for the fixture's harp note block, the
wrong Carpet `look` argument order, stale smoke jars beside the failure probe
jars, the whole-world memory verify rejecting falling init-time items, the
two aborted authoritative runs (restore-flag reply and stale devcap
references) fixed in later source commits, and the original 14-tick
two-process attack/use gap that motivated the same-window probe above. Each
fix was its own source commit; no run claims a single hash over mixed
revisions.

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
