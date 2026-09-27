# Preserved failures (ROM20 cold start)

Both failures happened during fixture setup, before the experiment window
opened, and were diagnosed and fixed autonomously by the task agent. Raw
evidence is retained; nothing was hidden or rewritten. The 100-file freeze
snapshot was not modified by this fix round.

## 1. Restore attempt 1 - 6/6 entities lost to stale copied region data

* What happened: the first guarded `restore` into `rom20-exp` issued all 6
  summons (`Summoned new Armor Stand` / `Minecart with Chest`) but the
  post-restore snapshot held 0 entities: orderHash `e3b0c44298fc1c14` instead
  of `545a11e87afce73b`, counts 5/0 and 1/0.
* Cause: the fork deliberately strips `entities/*.mca`, but the copied
  overworld region files still carried the same entity data. The experiment
  chunks had never been loaded, and the guarded restore freezes the world
  before its `forceload` can load them, so the fresh summons were replaced when
  the saved chunks later loaded. This is the documented "clear the copied
  chunks in game and save" step.
* Fix: `kill @e[type=!minecraft:player]` until the count stopped dropping,
  `save-all flush`, restart, preload the machine chunks while running, save
  again, then re-run the restore (attempt 2: 6/6 issued, orderHash match,
  counts match, 0 NBT/position/velocity deltas).
* Raw references:
  * `failures/restore-attempt-1-failed.session-result.txt` - the **byte-exact**
    tool result from the frozen pi session, line 169 (1-indexed), toolCallId
    `call_00_MR03DuPnyR02Opv6Y1bu4504`: 6,120 bytes, 120 LF bytes / 121 display
    lines, CRLF preserved, sha256
    `eeba70d42d9d43a9a86e2574d9492a11ec02520f33325779c9eaa99f5bd3bf93`.
  * The freeze snapshot also keeps the task-time reformatted copy
    `workspace-as-agent-left/evidence/restore-attempt-1-failed.walk.txt`
    (6,240 bytes, sha256 `29598705cf535876077a0159029508fa98a266591eb8e0e36fe9e6f7d5991f22`),
    produced by a Windows newline translation during the task; it is kept
    unchanged for history, is not committed, and is not the canonical copy.
  * `failures/failure-evidence.json` records both hashes and the extraction
    provenance; the freeze's `restore-attempt-1-failed.json` remains as the
    task-time summary.

## 2. First `romuser` spawn fell into the void

* What happened: the first `/player romuser spawn` joined, but the world kept
  running while the next commands were issued; with the machine floating over
  the void the player fell below the removal plane before the hover seat could
  be mounted.
* Fix: the fixture's own timing was used on the retry - spawn, poll for the
  entity, freeze immediately, mount the hover seat, aim, stay frozen. The
  placed user then matched the ready snapshot exactly (UUID
  `3ec122d5-fc27-4816-be47-bf8be8d7e56d`, pos `[11.5, -53.6, -24.5]`,
  rotation `[0.0, 50.5055]`).
* Raw references:
  * `failures/first-spawn-romuser-console-excerpt.txt` - byte-exact host-local
    console excerpt, lines 845-856: Romuser join (`entity id 7`), `fell out of
    the world` / `lost connection: Romuser died`, and the successful retry
    (`entity id 8`). 915 bytes, sha256
    `2836ea0bdee6a00ef71b10388435dbf01be49de73e82ef0f1717f80a4f47c495`.
  * The source console log is **host-local, not part of the frozen archive**:
    `labs/rom20-exp/logs/console.log`, 73,119 bytes, sha256
    `a75570222e5d3b0c8e98c9d87d741f465d2ba05e9306cd156a96512881a4b18e`;
    its path and hash are pinned in `manifest.json.host_local_sources`.

## Classification

Both are fixture-setup/INFRA-class events inside the single task session, not
agent-operation failures and not fixture invalidity of the final state: the
cleanup and retry produced a copy that matched the operator's ready snapshot
41/41 fields and re-verified against the fork (order, counts, full NBT). They
are preserved because the run protocol requires keeping failed attempts.
