# Preserved failures (ROM20 cold start)

Both failures happened during fixture setup, before the experiment window
opened, and were diagnosed and fixed autonomously by the task agent. Raw
evidence is retained in the freeze snapshot; nothing was hidden or rewritten.

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
* Raw references: `restore-attempt-1-failed.walk.txt` and
  `restore-attempt-1-failed.json` in the freeze snapshot's
  `workspace-as-agent-left/evidence/`, the `restore-pre` / `restore-check`
  snapshot meta files under the experiment lab, and the console log of the
  second session.

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
* Raw reference: `labs/rom20-exp/logs/console.log` (first join/removal and the
  successful placement) in the freeze snapshot's console log path documented by
  `manifest.json`.

## Classification

Both are fixture-setup/INFRA-class events inside the single task session, not
agent-operation failures and not fixture invalidity of the final state: the
cleanup and retry produced a copy that matched the operator's ready snapshot
41/41 fields and re-verified against the fork (order, counts, full NBT). They
are preserved because the run protocol requires keeping failed attempts.
