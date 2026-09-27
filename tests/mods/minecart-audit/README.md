# mc-agent minecart audit mod

An independent, read-only Fabric server mod that records machine inputs and
transient chest-minecart outputs for the Minecart ROM evaluator. It is *not*
the agent's logger and contains no ROM logic; see
[`docs/minecart-audit.md`](../../../docs/minecart-audit.md) for the full
manual.

```powershell
# build (JDK 25 + a normal 26.2 installation; no Gradle)
python tests/mods/minecart-audit/build.py --minecraft-dir "D:\MC\MC_Game\.minecraft"

# install into a disposable lab and run the controlled live smoke
python tests/mods/minecart-audit/live_smoke.py

# read one raw server log
python tools/minecart_audit.py check --log labs/<lab>/mc-audit/audit-<run>.jsonl --json
python tools/minecart_audit.py selftest
```

Layout:

```
build.py                     javac-based build, mirrors interface-mod/build.py
src/main/java/dev/mcagent/audit/
    AuditMod.java            entry point: lifecycle events + /mcaudit command
    AuditEngine.java         read-only hooks, tracking, lifecycle, evidence
    AuditConfig.java         validated config.json
    AuditLog.java            JSONL writer, sessions, status, overflow
    JsonViews.java           entity/player/inventory -> JSON
    Region.java              inclusive block boxes
    TrackedCart.java         per-cart dedupe/order state
    HookStats.java           per-hook overhead counters
    mixin/NoteBlockMixin.java       attempt / request / processing hooks
    mixin/EntityMixin.java          removal + teleport hooks
    mixin/MinecartContainerMixin.java  inventory capture before the void drops it
src/main/resources/          fabric.mod.json, mcaudit.mixins.json
live_smoke.py                ports 27180-27189, positives + negatives + control
bridge_restore_smoke.py      bridge guarded restore + audit coverage + smoke-mod coexistence
stage1_evidence.py           canonical stage1-gate artifacts + partial gate check
dist/                        built jar (git-ignored)
```

The two hard rules: hooks are read-only (`@Inject` only, no redirects or
overwrites), and missing evidence must be loud (status file + `audit_incomplete`
+ verifier "incomplete"), never a silent pass.
