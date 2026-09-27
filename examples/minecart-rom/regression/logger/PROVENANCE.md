# ROM21 regression logger provenance

This logger is the issue-#21 parameterized extraction of the logger the
autonomous agent actually wrote and deployed during the proven ROM20 cold
start. The original sources are frozen in the repository at
`docs/evidence/rom20-coldstart/logger`; nothing there was edited.

## Original source identity (frozen, #20)

| Original source (under `docs/evidence/rom20-coldstart/logger/src`) | sha256 |
| --- | --- |
| `main/java/dev/mcagent/romlog/RomLog.java` | `4e2b5d0f0cc9defcf5e0f1bc8b36c8c9954678fd07120e89895d650cdf458777` |
| `main/java/dev/mcagent/romlog/RomLogMod.java` | `32ca8d636776833a062edb8fd93ab21c93bdca31b2cf4f8aa1d34731f7d394ff` |
| `main/java/dev/mcagent/romlog/mixin/MinecartContainerMixin.java` | `61dd10d999c5030808cd1052e01cf01e1db58ee26df99489dac6d5da591e4b5a` |
| `main/resources/fabric.mod.json` | `46ed94d210c6b25a12647c9ceefb2b7f036f008b83aae252a1c88bae1e083e97` |
| `main/resources/romlog.mixins.json` | `d7b86c84a46077f81f4946916a178fde852d2bedd380df38ce664d5517ef2e92` |

The deployed #20 jar was `rom20-agent-logger.jar`, sha256
`e445f461ab0de331189ce609dac60d53f2ffb4529fc22b5dfb6c77d5a8593f89`, 16,778
bytes, built reproducibly from those sources.

## Behaviour kept

* own JSONL file (`<auditDir>/romlogger/romlog.jsonl`, `romlog.dir` override),
  never the evaluator's `mc-audit/` directory;
* `logger_armed` / `logger_flushed` records with instance, run id, dimension,
  build, level name and file;
* the transient capture of the ordered live inventory after the cart leaves
  the stack region, one capture per UUID, strictly after the audit's
  `cart_exit`;
* removal of the cart from tracking after capture and continuation of the
  sequence across server restarts (`lastSeq`);
* no world mutation.

## Behaviour deliberately changed (parameterization for #21)

* the stack-exit plane is configurable: `romlog.exitAxis` (`x|y|z`, default
  `x`) and `romlog.exitGreaterThan` (default `15.0`), matching the original
  hard-coded `cart.getX() >= 16.0d` condition at the default values;
* `romlog.captureDelayTicks` (default `1`) delays the transient capture after
  the first out-of-region sample, so a deliberately late/missed capture can be
  produced as a real fault probe;
* `romlog.expectedCarts` is recorded in `logger_armed`/`logger_flushed` as an
  expectation, not an assertion;
* the natural void capture is a first-class record: the rename mixin now
  always writes `cart_void_capture` with the live inventory, the real
  `RemovalReason` and an inventory digest, in addition to the transient
  `cart_observed` capture. This makes the natural void removal (the cart's own
  `remove` with `DISCARDED` below the world) part of the agent-owned evidence
  instead of a fallback that the earlier transient capture would suppress;
* the transient and void records carry an `inventoryDigest` over the slots
  actually read and a `build` string;
* the build identity is substituted from `src/main/resources/romlog.properties`
  (`build=${version}`), so two same-length versions build two jars with
  identical size and different bytes - the same-size iteration proof;
* package/class renaming (`dev.mcagent.rom21`, mod id
  `rom21-regression-logger`) so the regression jar cannot be confused with
  the frozen #20 artifact.

## Rebuild (deterministic, store compression)

```powershell
python tools/build_mod.py --source examples/minecart-rom/regression/logger `
    --lab <any provisioned lab> --out labs/build/rom21-logger.jar --version romlog-rom21-1
```

The build sidecar records the source hashes, the classpath digest and the
resulting jar hash; the regression run manifest pins those values before the
authoritative runs and re-verifies them after.
