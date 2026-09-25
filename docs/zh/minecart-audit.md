# 矿车审计测试 mod

`tests/mods/minecart-audit/` 是一个独立的**测试侧** Fabric mod：它在服务端记录机器真实发生了什么——玩家的输入尝试、服务端对音符盒触发的处理、每辆离开堆叠的箱子矿车，以及矿车被虚空移除之前捕获的有序库存。

它不是智能体的 logger，不含 ROM 解题逻辑，也不放置命令方块。它只读取世界并追加自己的 JSONL 文件。智能体仍需自建 logger；这个 mod 是评测者对同一次运行的独立证据来源。

它服务的任务是 [issue #14](https://github.com/guajun/mc-agent/issues/14) 的 Minecart ROM；mod 本身刻意保持通用——区域、矿车类型和标识来自配置文件，因此可以审计任何由音符盒驱动、带矿车堆叠的机器。

## 构建与安装

Minecraft Java 26.2 自带未混淆类，因此只要有 JDK 和一份正常游戏安装即可构建——不需要 Gradle、不需要重映射。构建脚本与 `interface-mod/build.py` 采用同一模式。

```powershell
python tests/mods/minecart-audit/build.py `
    --minecraft-dir "D:\MC\MC_Game\.minecraft" `
    --jdk "C:\path\to\jdk-25"
# -> tests/mods/minecart-audit/dist/mc-minecart-audit-0.1.0.jar
```

`--jdk` 依次回退到 `$MC_AGENT_JAVA`、`labs/*/lab.json` 中记录的 JDK、`$JAVA_HOME`，最后是 PATH 上的 `javac`。`--minecraft-dir` 默认 `D:\MC\MC_Game\.minecraft`，可用 `$MC_AGENT_MINECRAFT_DIR` 覆盖。

用 `lab_server.py` 把 jar 和 Fabric API 一起装进实验室：

```powershell
python tools/lab_server.py provision --name audit --void --fabric-api --carpet `
    --mod-jar tests/mods/minecart-audit/dist/mc-minecart-audit-0.1.0.jar
```

> `lab_server.copy_if_different` 目前只比较文件大小，同尺寸重建的 jar 会被跳过。在 [issue #17](https://github.com/guajun/mc-agent/issues/17) 落地前，重新 provision 之前请先删掉 `labs/<lab>/mods/` 里的旧 jar。

## 配置

服务端启动时读取 `<服务端运行目录>/mc-audit/config.json`（可用 `-Dmcaudit.config=<path>` 覆盖位置）。配置缺失或格式错误时不会静默通过：mod 会写 `mc-audit/status-unconfigured.json`、在控制台报错并停用自己；测试程序可以检测到该状态，因此"没装好"不可能被当作"已审计"。

```json
{
  "runId": "rom-2026-09-26-1",
  "instanceId": "lab-a",
  "dimension": "minecraft:overworld",
  "provenance": {
    "kind": "source-world | fork | restore",
    "reference": "D:/.../Minecart ROM test",
    "snapshotId": "...",
    "snapshotHash": "..."
  },
  "outputDir": "mc-audit",
  "maxBytes": 67108864,
  "sampleIntervalTicks": 1,
  "correlationWindowTicks": 2,
  "cartTypes": ["minecraft:chest_minecart"],
  "agentUuids": ["49e96464-aa17-46b3-b5c6-87574b6b48b4"],
  "inputRegions": [
    { "name": "note-block", "from": [0, -59, 0], "to": [0, -59, 0] }
  ],
  "stackRegion": { "from": [2, -60, -1], "to": [3, -59, 1] },
  "outputRegion": { "from": [4, -140, -8], "to": [8, -60, 8] }
}
```

区域是闭区间方块盒：某点所属方块（各轴取 `floor`）落在 `from..to` 内即算在区域内。`inputRegions` 命名机器输入位置；`stackRegion` 是矿车堆叠所在、也作为追踪起点；`outputRegion` 是可选的第二个越界标记。当 `cartTypes` 实体在 `dimension` 中加载进 `stackRegion` 时被追踪；矿车首次离开 `stackRegion` 或进入 `outputRegion` 时记为"出堆"。`agentUuids` 可选：设置后只有这些玩家会被分类为智能体；留空则任何玩家都算玩家操作者（verifier 仍会报告 UUID）。

## 生命周期与控制命令

`/mcaudit`（权限等级 gamemaster，RCON 为 owner）是测试侧的控制面。这些命令绝不操作机器。

| 命令 | 含义 |
| --- | --- |
| `/mcaudit status` | 当前 run、实例、阶段、序号、追踪矿车、不完整原因 |
| `/mcaudit phase init` | fixture 初始化（可重复） |
| `/mcaudit phase restore` | 快照恢复 / fork 准备 |
| `/mcaudit phase experiment_start` | 打开智能体操作窗口 |
| `/mcaudit phase experiment_end` | 关闭窗口 |
| `/mcaudit mark <label>` | 测试脚本标记；不会被误判为操作 |
| `/mcaudit flush` | 强制刷新状态文件 |
| `/mcaudit end` | 写入 `audit_end` 并关闭 JSONL |

服务端启动时写 `audit_ready`，关闭时写 `audit_end`。若服务端在 `audit_end` 之前死亡，会话保持打开；verifier 会把文件里**任何**未关闭会话视为不完整（除非显式传 `--allow-open-history`），缺日志是响亮的失败而非静默通过。live smoke 里有专门的 kill/restart 负例。

## 证据文件

```
mc-audit/
  audit-<runId>.jsonl          原始事件流，每个事件都 flush
  status-<runId>.json          当前快照（阶段、计数、矿车、hook 开销）
  latest.json                  指向当前 run 的文件
  status-unconfigured.json     配置缺失/损坏时写入
  config.json                  mod 实际读取的配置
```

每个事件都带 `seq`（文件内严格递增，服务端重启后继续播种）、`tick`（服务端 tick）、`wall`、`run`、`inst`、`session`、`phase`、`type`。重启会追加新的 `session_start`；因此一个 `runId` 可以跨多次服务端会话（mod 重新部署、快照恢复都需要），同时不丢失顺序。`maxBytes` 限制的是整个文件而不是单个会话：字节计数从已有文件大小续起，重启多次也不会重置预算；溢出会置 `truncated`，verifier 将其视为不完整。

主要事件类型：

| 类型 | 含义 |
| --- | --- |
| `input_attempt` | 玩家对音符盒的交互尝试：操作者 UUID、手、物品、结果，以及位置是否属于配置的输入区 |
| `input_request` | `NoteBlock.playNote`，真正的请求；携带触发实体（`player`、`entity` 或 `redstone_or_environment`） |
| `input_processed` | `NoteBlock.triggerEvent`，服务端处理音符；关联 `requestSeq`/`attemptSeq`，记录 note/instrument 与 `agentOp` |
| `cart_tracked` | 配置类型矿车加载进堆叠区：有序库存与来源阶段 |
| `cart_sample` | 周期性位置/运动/库存采样，按矿车与 tick 去重 |
| `cart_exit` | 矿车首次离开堆叠区 / 进入输出区（采样或移除时） |
| `cart_remove` | 移除：原因（虚空为 `DISCARDED`）、位置、运动，以及在容器掉落内容**之前**捕获的有序库存 |
| `cart_inventory_change` | 追踪期间观测到的库存变化 |
| `cart_teleport`, `cart_reload`, `cart_reappeared` | 替代行为证据（传送、区块重载、UUID 复用） |
| `audit_incomplete` | 审计不可信的原因（关联缺失、hook 异常等） |
| `audit_end` | 每个会话的收尾：计数、矿车汇总、hook 开销 |

矿车 hook 刻意注入在 `Entity.remove` 与 `AbstractMinecartContainer.remove` 的 HEAD，早于内容销毁——因为 vanilla 快速路径会先掉落内容，仅靠 tick 末采样会漏掉快速下坠。音符盒 hook 区分玩家尝试（`useItemOn`/`useWithoutItem`，以及 26.2 的 `attack` 左键，记为 `path: "attack"`，避免左键污染证据链）、携带触发实体的请求（`playNote`）与服务端处理（`triggerEvent`）；任意右键、发声和命令反馈都不会被当成机器操作。

只有 `useItemOn`/`useWithoutItem` 会被算作已校准的机器操作。26.2 的 `attack` 左键会调用 `playNote` 但不会改变音符状态，因此不能通过校准路径触发观察者/活塞机器；它仍会作为尝试记录（`path: "attack"`）以保持证据链完整，但 `machine_operated` 会忽略它。live smoke 有专门的 attack-only 负例：把假玩家设为生存模式后左键，证明音符盒仍在、矿车未移动、verifier 判 `fail`。

一个请求或尝试只会记到一次处理事件，并且顺序与服务端处理方块事件的顺序一致：请求按 FIFO 消费，尝试通过与处理事件相同的 `requestSeq` 匹配（而不是时间窗口里的邻居）；匹配不到就是 fail-closed 缺口。verifier 在一个 `requestSeq`/`attemptSeq` 被多个目标 `input_processed` 引用、尝试携带的 `requestSeq` 与处理事件不一致，或尝试/请求/处理的 operator UUID 不一致时判失败。只有配置的输入位置进入关联表，过期条目每 tick 清理。

所有 hook 只用 `@Inject`：没有 redirect、没有 overwrite、不修改状态、不取消回调。每个 hook 的调用次数、总纳秒与最大纳秒都写进状态文件和 `audit_end`，观测开销可度量。

## 读取证据

`tools/minecart_audit.py check` 读取一个 JSONL 并给出判定：

```powershell
python tools/minecart_audit.py check --log labs/audit/mc-audit/audit-<run>.jsonl `
    --status labs/audit/mc-audit/status-<run>.json --json
python tools/minecart_audit.py selftest        # 合成正/负用例
```

退出码：`0` 通过、`1` 失败、`2` 不完整/不可读。报告直接回答验收问题：

* `machine_operated` — 至少一次目标输入在服务端被真正处理，并关联到玩家请求与使用尝试，且发生在实验阶段；
* `input_chain` — 每个目标处理事件都有先行的请求；智能体操作还必须有关联窗口内的尝试，否则显式以"顺序证据不足"失败；
* `transient_outputs` — 实验期间被移除的每辆矿车都有出堆证据，并在销毁前捕获有序库存；重复出堆与重复移除都会失败；
* `ordering` — 文件顺序即发生顺序，出堆序号严格递增，同 tick 多输出必须有不同序号；
* `completeness` — `audit_end` 为 complete、无 `audit_incomplete`、无截断，并且会话模型与状态文件一致。

负例既由 verifier 的 selftest 覆盖（无操作、错误位置、仅 marker、仅答案、缺请求、仅环境触发、重复输出、实例串日志、截断、缺结尾），也由下面的 live smoke 覆盖。

## Live smoke

```powershell
python tests/mods/minecart-audit/live_smoke.py
```

脚本会构建 mod，在 issue 指定端口（27180-27189）拉起一次性虚空实验室，并运行：

* 一次跨服务端重启的正向运行（两个会话、一次智能体操作、一辆矿车在被虚空移除前被捕获）；
* `无交互`、`错误位置`、`仅 marker`、`仅答案`、`仅左键`、`仅红石` 六个负向运行——都必须被 verifier 判失败；左键负例把假玩家设为生存模式，记录左键但不破坏音符盒，证明方块与矿车都没有变化；
* kill/restart 负例（`stop --force` 后重启、只关闭第二个会话）——verifier 必须对未关闭会话报 `incomplete`；
* 装了 mod 但没有配置的实验室——必须报告"not configured"；
* 一个不装 mod 的对照实验室跑同一脚本，对比可观察结果（音符被调、矿车消失），证明 hook 不改变 fixture 行为；
* 源存档的只读副本在装了 mod 的情况下正常加载；
* 实例间日志隔离。

证据写进 `labs/rom18-evidence/`（`summary.json`、原始日志、verifier 报告、控制台日志）；该目录被 git 忽略，是本地产物而非仓库内容。旧尝试会保留为 `<evidence>.attempt-<时间戳>/`，不会被删掉。

## bridge / interface-mod 组合验证

```powershell
python tests/mods/minecart-audit/bridge_restore_smoke.py
```

这是合并后的组合运行：干净的 bridge 源码（默认为 `codex/rom13-bridge6` worktree，head 在脚本里固定，即带守卫恢复 + `player.view` 的版本）、干净的 interface mod 0.6.0（SHA-256 `45f12e16...404f`），并在 source-audit 实验室与 experiment 实验室里把审计 mod 作为**必需**测试 mod 安装（`--test-mod`，缺了会拒绝启动）。

所有阶段都跑在真实的 26.2 专用服务器上：

1. 用 bridge 的守卫 `fork`（冻结、快照、复制世界）分叉一个通用的三辆叠加箱子矿车 fixture（物品各不相同）；
2. experiment 实验室加载 fork 世界，bridge 用 `expect_world_dir` + `replace_existing` 执行守卫 `restore`，随后 `verify` 对比完整状态（顺序哈希、数量、维度、位置、速度、NBT/物品）——必须 `ok: true` 才继续审计阶段；
3. 审计日志中恢复矿车的 `cart_tracked` 有序库存与 bridge 恢复后快照逐项交叉核对（相同 UUID、相同有序物品）；
4. 在恢复后的目标上调用 `player`/`player.view`，必须返回 `found`、维度及归一化的 eye/direction；
5. experiment 实验室里的通用音符盒机器产生规范的 输入 -> 处理 -> 矿车输出 链，同时恢复副本仍在被追踪；
6. #17 的 smoke mod（`examples/smoke-mod`，用 `tools/build_mod.py` 针对该实验室构建）与审计、interface mod 共存部署；`mcagent-smoke status`/`sample` 和 `lab_server.py verify --require-vantage` 必须全部成功。该 smoke mod 只证明部署能力与共存，**不是**智能体的 logger，也绝不当作 logger。

证据写进 `labs/rom18-b6-evidence/`（`summary.json`、含 bridge 调用记录的逐阶段 JSON、恢复审计交叉核对、共存输出、原始审计日志与控制台日志）。

## 阶段一门禁适配器

#22 的 `tools/stage1_gate.py` 消费的是规范 bundle，而不是原始 JSONL。`tools/minecart_audit.py export` 把 live 证据映射成它的 `independent_test_mod` 产物：`test-mod-manifest.json`、`audit-events.jsonl`、`negative-cases.jsonl`、`audit-lifecycle.json`。映射在每行 `detail` 中保留原始事件类型、服务端 tick 与序号；规范 `(tick, seq)` 使用按身份单调的计数器，因为服务端重启会重置游戏 tick 而审计序号仍在增长。

```powershell
python tests/mods/minecart-audit/stage1_evidence.py
```

读取两份 live 证据、导出产物、用真实 run/instance/端口/源世界字段组装部分 live bundle，并对它运行门禁。最近一次结果为 `independent_test_mod: pass`（断言 *input -> processing -> output chain*、*negative cases rejected*），而整体 bundle 因其他前置缺失仍是 `blocked`。这刻意不是门禁通过：#15 的 fixture 与其他检查各自负责。

导出会**绑定**证据而不是改写身份：事件自身的 `run`/`inst`/维度与声明不符会被拒绝，存在未关闭会话或缺少 `audit_end` 的日志同样被拒绝。负例必须 verifier 判定恰为 `fail`（不完整/崩溃日志不算"已拒绝的负例"），门禁会断言每行 `verifier_verdict == "fail"`；环境触发的处理行会带 `actor_provenance` 而不是裸 null。manifest 的 `fixture_behavior_unchanged` 与 `agent_mod_coexists` 来自 live summary，任一为假时适配器拒绝导出。仓库另有 #28 提供的完整 bundle 无损适配器 `tools/stage1_evidence.py`；bridge 组合脚本在复制证据前会用 `/mcaudit end` 收尾源会话。

## 限制

* 这是插桩，不是对抗性沙箱。它不承诺抵抗同权限恶意代码；它为人工审查记录证据。
* 真实 ROM 地图与确定性 fixture 初始化仍属于 [#15](https://github.com/guajun/mc-agent/issues/15) 的工作；这里所有运行都用通用虚空实验室 fixture。组合 bridge/restore 运行确实证明了恢复副本的审计覆盖，但这不是 fixture 集成。
* 把工具调用关联到 `input_*` 事件属于 [#19](https://github.com/guajun/mc-agent/issues/19) 的审计 harness：mod 精确记录操作者 UUID、tick 和序号，使关联成为可能，但它自己不读取工具轨迹。
* 同 tick 多输出的顺序在 verifier 中有强制校验与单测；live fixture 每次只产生一辆矿车，因此该点由合成用例覆盖而非真实同 tick 双输出。
