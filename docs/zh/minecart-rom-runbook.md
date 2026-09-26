# Minecart ROM 验收 runbook

本页是 [issue #13](https://github.com/guajun/mc-agent/issues/13) 的父级验收链记录：工具链
就绪（[#14](https://github.com/guajun/mc-agent/issues/14)）、一次可审计的真实智能体冷启动
（[#20](https://github.com/guajun/mc-agent/issues/20)），以及固化的真实游戏回归
（[#21](https://github.com/guajun/mc-agent/issues/21)）。三个阶段均已执行、独立评审并合并；
子 issue 已关闭，接受的证据包链接如下。详细契约在链接的页面里。

**本页不是证据。** 已接受的事实以证据包和已合并 PR 为准；只有真正运行过并保留原始产物的
步骤才算数。本页不得在没有证据的情况下把任何门禁或 issue 标成通过。

详细契约：

* 门禁检查与 schema - [阶段一集成门禁](stage1-gate.md)
* 组合集成驱动 - [阶段一集成运行](stage1-integration.md)
* 阶段二入口、轨迹与判定 - [可审计的冷启动运行](coldstart-protocol.md)
* 实验室、mod 构建/部署、分叉与身份 - [实验室服务器](lab-server.md)、
  [构建 mod](mod-building.md)、[分叉校验](fork-verify.md)、[玩家身份](player-identity.md)

## 阶段、进入条件与退出证据

| 阶段 | Issue | 何时可以开始 | 退出证据 | 禁止 |
| --- | --- | --- | --- | --- |
| 1. 工具链门禁 | [#14](https://github.com/guajun/mc-agent/issues/14) | 前置 #15、bridge#6、#16-#19 开发完成 | 对真实 bundle 的 `stage1_gate.py check` **PASS**，且协调者复核原始哈希 | 要求阶段二事实；预写 ROM 解法或 Agent logger |
| 2. 可审计冷启动 | [#20](https://github.com/guajun/mc-agent/issues/20) | 阶段一门禁 PASS 且已复核；全新未见过 fixture 程序 | 一个 run 目录，五项审计 flag PASS 且有独立测试 mod 证据，来自全新模型上下文 | 收到本设计历史、校准答案或预写 logger；把阶段一证据重标为 Agent 证据 |
| 3. 固化回归 | [#21](https://github.com/guajun/mc-agent/issues/21) | 在固定 head 上**已复核、真实成功**的 #20 | 真实游戏回归：>=3 个全新实例、>=2 个校准程序、>=1 次冷下载、负例 | 回放旧日志；把脚本通过宣称为模型自主 |

依赖方向：阶段一证据由测试端工具在 #20 **之前**产出；#20 增加 Agent 自己的操作、logger 与
答案；#21 从复核过的 #20 产物提取参数化回归。三个阶段均按这些规则完成；接受的证据见下方
最终验收一节。

## 最终验收（main `b54e1e8`，2026-09-26）

三个阶段均已执行、独立评审并合并。Issue #14-#21 已关闭；父级验收记录见
[issue #13](https://github.com/guajun/mc-agent/issues/13)。

| 阶段 | Issue | 已合并 PR | 验收证据 | 独立评审 |
| --- | --- | --- | --- | --- |
| 1. 工具链门禁 | [#14](https://github.com/guajun/mc-agent/issues/14) | [#30](https://github.com/guajun/mc-agent/pull/30) `ce08fd3` | 门禁 **PASS 8/8**（exit 0）；run `rom13-fullgate-20260926T044858Z`、执行源码 `6e009108...`、审计 jar `7a77e89d...`；[完整门禁摘要](evidence/rom13-stage1/full-gate-summary.json)、[阶段一证据 README](evidence/rom13-stage1/README.md) | [评审 5324700330](https://github.com/guajun/mc-agent/pull/30#pullrequestreview-5324700330) 及已接受的补充探针 |
| 2. 可审计冷启动 | [#20](https://github.com/guajun/mc-agent/issues/20) | [#32](https://github.com/guajun/mc-agent/pull/32) `ef05c0b` | 一个全新 pi 会话（0.87.1、`deepseek/deepseek-flash`、thinking `max`），157 次工具调用，任务期间无人工帮助；Agent 自己的 logger 捕获 5 辆真实矿车；run 审计 **5/5 PASS**；run `rom20-20260926T063100Z`、执行源码 `1deb735`；[ROM20 证据](evidence/rom20-coldstart/README.md) | [评审 5325036876](https://github.com/guajun/mc-agent/pull/32#pullrequestreview-5325036876)，head `1fa769e` |
| 3. 固化回归 | [#21](https://github.com/guajun/mc-agent/issues/21) | [#34](https://github.com/guajun/mc-agent/pull/34) `b54e1e8` | 3 个全新实例、2 个校准程序、1 次冷下载、同大小 jar 迭代（21,530 字节、新哈希、重启+重新恢复）、8 个负例（5 实机、3 离线）；游戏执行 `c5fa37b`、离线验证器加固 `8f28aea`；[回归页](rom21-regression.md)、[ROM21 证据](evidence/rom21-regression/README.md) | [评审 5325226293](https://github.com/guajun/mc-agent/pull/34#pullrequestreview-5325226293)，head `ae5cc96` |

已合并前置 PR：bridge#8、bridge#9、#22-#29、#30、#32、#34。Issue #14-#21 均已带证据关闭
（bridge#6 通过 bridge PR #9 关闭）。[bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7)
（可选 Hermes webhook 兼容）仍开启，**不**属于本次验收。根 issue #13 记录整条链的最终验收。

源存档在所有验收运行前后均未变化：tree hash `8cd54c86...324a`，40 个文件，11,556,310 字节。

### 原始模型能力 vs 固化回归

| | 原始 #20 冷启动 | #21 回归 |
| --- | --- | --- |
| 证明什么 | 全新模型上下文自主研究机器、自行编写 logger 并操作 | 成功配方可在全新实机实例上无模型复现 |
| 模型参与 | pi 0.87.1 / `deepseek-flash`、thinking `max`、157 次工具调用、任务期间无人工帮助 | 无；脚本化实机运行 |
| 证据 | [ROM20 包](evidence/rom20-coldstart/README.md)（冻结的答案与独立 oracle） | [ROM21 包](evidence/rom21-regression/README.md) 与[回归页](rom21-regression.md) |
| 离线复现 | 从冻结原始日志重建 logger 并重新推导投影（[ROM20 复现说明](evidence/rom20-coldstart/README.md)） | `python tools/rom21_regression.py selftest`；`python tools/rom21_verify.py verify --run docs/evidence/rom21-regression/runs/<run-id>/verify-gen1` |
| 实机复现 | 除非再开一次全新自主会话，否则不可重复；冻结包即证据 | `python tools/rom21_regression.py suite --stamp <new-stamp>` 与 `python tools/rom21_regression.py negatives`（需要 Java 25、游戏与网络） |
| Demo | - | `python examples/minecart-rom/regression/demo/demo.py`（复用已提交包） |

**回归结果不是模型能力证据。** #21 页面与包明确写明；原始 #20 证据保持冻结，校准运行从不
假设弹出顺序等于生成顺序，也不宣称覆盖空/重复库存。

!!! note "历史快照说明（合并前）"
    本页曾在 PR #27、PR #26 仍开启时写作，并把已检查快照标为 `37fb824` 与 `0e8c15b`。两者
    现已合并（#27 `8dd75c6`、#26 `824c15d`），完整门禁运行固定了接受哈希。那些快照标签属于
    历史，不是当前 head。

!!! note "跟踪器历史（历史记录，2026-09-25）"
    #14 与 #19 在工具 PR 合并后曾被短暂关闭，当天 18:26 UTC 在仍为 blocked 的实机门禁
    与未勾选的 #14 checklist 被指出后已**重新开启**。这是历史而非状态：issue 状态从不是验收，
    任何关闭都不应先于复核过的门禁 `pass`。

## 阶段一：门禁检查与循环依赖防护

门禁（[实现](stage1-gate.md)）评估一个*真实* bundle 并 fail closed。八个检查以及每个检查
**无需阶段二 Agent** 即可由谁产出：

| 检查 | 归属 | 产出方式 | 阶段一可满足 |
| --- | --- | --- | --- |
| `fixture_map` | #15 | fixture runner：下载、初始化、校验、evidence | 是 |
| `restore_fidelity` | bridge#6 | 受保护恢复 + `stage1_evidence.py restore-evidence` | 是 |
| `player_context` | #16 | 真实身份探针（`docs/evidence/rom13-meta16`） | 是 |
| `agent_dev_capability` | #17、#19 | `harness_preflight.py`（`tool-environment`）+ smoke mod 构建/部署 | 是，无需模型调用 |
| `independent_test_mod` | #18 | 审计 mod 实机 smoke + 负例 | 是 |
| `trace_persistence` | #19 | 通用 smoke 工具调用 + 显式证明 join | 是，见下 |
| `smoke_fixture_validity` | #14 | smoke 套件、fixture 校准、版本锁、证据索引 | 是 |
| `evidence_integrity` | #14 | 由门禁计算 | 是 |

**门禁不要求阶段二事实。** 它只读 bundle 里的文件，从不读模型轨迹，也没有 harness/session
输入。规范审计事件里的 `phase: "agent"` 是审计生命周期的*实验阶段*——测试端通用 smoke 调用
与 Agent 调用同属该阶段。仅在阶段二成立的事实（`agent_read_log`、`answer_correct`、Agent
自己的 logger、它自己的工具—游戏 join）由 #20 的 `tools/run_audit.py` 检查，**不是**本门禁。

唯一微妙的要求是 trace join：

* `trace-join.json` 必须把每条 `phase: "agent"` 审计事件 join 到一条被追踪的工具调用，且
  `verified: true`；适配器只有在显式 `proof`（`producer`、`basis`、`clock`）存在时才把候选
  提升为已验证——见[诚实 join 规则](stage1-integration.md#join-verified)。
* 2026-09-25 的集成运行有 71 条追踪调用、**135 个候选、0 个已验证**
  （`bundle/mapping/join-candidates.json`）：#18 审计事件不带发起它的工具调用 id，RCON 归因
  只能停留在候选，产物被扣留。
* 修复必须在测试端：记录能把追踪的 smoke 调用与审计事件绑定的回执（例如审计命令返回它记录的
  序号，或显式 `--joins` 证明给出命令、操作者 UUID 与共同时钟）。绝不手改
  `verified: true`。
* 已接受的完整门禁运行补齐了这一缺口并产出已验证 join；其固定哈希见
  [完整门禁摘要](evidence/rom13-stage1/full-gate-summary.json)。

如果某个阶段一检查只能由 #20 的 Agent 满足，那是阶段一证据链的实现 bug——修测试端 smoke 或
适配器并记录。绝不能以此跳过阶段一，或给门禁喂阶段二的说辞。

## 阶段一：精确配方

以下路径相对于本仓库。`labs/` 是被 git 忽略的原始证据；保留它，绝不改写旧 run。

### 1.1 门禁脚手架与目录

```bash
python tools/stage1_gate.py list
python tools/stage1_gate.py scaffold labs/stage1-evidence
python tools/stage1_gate.py selftest
```

### 1.2 组合集成运行

```bash
python tools/stage1_integration.py plan                   # 端口、java、步骤
python tools/stage1_integration.py run                    # 实机，热缓存约 4 分钟
python tools/stage1_integration.py run --bundle-only      # 逐字节可复现地重新归一化
```

默认与输出（对照 `stage1_integration.py` 核实）：

* 端口 `27240-27249`（`rom13-src` source_audit 27240-27243、`rom13-exp` experiment 27244-27247）；
* 默认 JDK 25：
  `C:\Users\MSI-NB\AppData\Roaming\.hmcl\java\windows-x86_64\mojang-java-runtime-epsilon`；
* interface mod jar 哈希固定为
  `45f12e16b3979be6a699ac3c744b2a68dfcf8dd2379f5987bf9b9319adf4404f`，从 #16/#18 对等
  worktree 的构建目录只读取得；哈希不符会被拒绝；
* 集成根目录 `labs/rom13-integration/` 含 `normalize-inputs.json`、`bundle/`
  （内有 `bundle/bundle-spec.json`）、`summary.json`/`summary.md`、`build/`、
  `audit-mod-src/` 与 `bridge6-restore-collection/`；实机 run 目录
  `labs/rom13-integration/run-<stamp>/` 含 `trajectory.jsonl` 与冻结的
  `trajectory.final.jsonl`（以及 `run.json`、`task.md`、`evidence.json`、
  `visibility.json`）；见
  [`docs/evidence/rom13-stage1/README.md`](evidence/rom13-stage1/README.md)。

完整门禁验收运行已补齐这些缺口：合并后的 #15 fixture、同 run 恢复、测试 mod 完整性、
harness `tool_environment`、smoke/版本锁/证据索引，以及显式证明的 join。其固定哈希见
[完整门禁摘要](evidence/rom13-stage1/full-gate-summary.json)；更早的 `integration-summary.json`
保留为诚实的 `blocked` 历史。

### 1.3 Fixture（#15，已合并）

fixture runner 已合并进 `main`（`examples/minecart-rom/`）。验收的三次初始化与完整门禁证据
都使用已合并的 runner；下面命令复现 fixture 流程：

```powershell
python examples/minecart-rom/runner/minecart_rom.py fetch --cold
python examples/minecart-rom/runner/minecart_rom.py up --lab rom15 `
    --rcon-port 27150 --server-port 27151 --vantage-port 27152 --bridge-port 27153 `
    --interface-mod labs/_cache/mods/mc-agent-interface-0.6.0.jar `
    --java "<jdk25>/bin/java.exe" --memory 3G
python examples/minecart-rom/runner/minecart_rom.py init --lab rom15 `
    --records examples/minecart-rom/calibration/records/run-01
python examples/minecart-rom/runner/minecart_rom.py validate --lab rom15 `
    --ready examples/minecart-rom/calibration/records/run-01/ready-snapshot.json
python examples/minecart-rom/runner/minecart_rom.py evidence --out labs/stage1-evidence `
    --records <records dir> --instance-id <instance> --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
```

`init` 会拒绝坏状态、已解冻世界、变化的矿车 NBT 或变化的 tick 顺序（`READY` 仍需实时再校验）。
`challenge` 子命令为评测端生成全新密封程序（`--seed`、`--out` 写到仓库外）；程序是*输入*，
不是答案。

### 1.4 恢复、身份、轨迹、审计证据

```bash
python tools/stage1_evidence.py identity \
    --source docs/evidence/rom13-meta16/live-identity.json \
    --bundle labs/rom13-integration/bundle
python tools/stage1_evidence.py trace \
    --trajectory labs/rom13-integration/run-<stamp>/trajectory.final.jsonl \
    --bundle labs/rom13-integration/bundle \
    --run-id <run id> --instance-id rom13-exp --instances rom13-src,rom13-exp
python tools/stage1_evidence.py audit \
    --log labs/rom13-exp/mc-audit/audit-<run id>.jsonl \
    --bundle labs/rom13-integration/bundle \
    --run-id <run id> --instance-id rom13-exp --dimension minecraft:overworld
python tools/stage1_evidence.py assemble --bundle labs/rom13-integration/bundle \
    --spec labs/rom13-integration/bundle/bundle-spec.json \
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
```

（bridge#6 的单独收集可用 `restore-evidence --out labs/rom13-integration/bridge6-restore-collection`。）

`restore-evidence` 校验已合并的 bridge#6 集合；它是**另一次真实运行**，绝不能冒充新 run 的
同 run 恢复证据（同 run snapshot/record 仍然必需）。

### 1.5 门禁裁决

```bash
python tools/stage1_gate.py check labs/rom13-integration/bundle \
    --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test" \
    --report stage1-report.json
```

退出码 `0` 通过、`1` 失败、`3` blocked、`2` 用法错误。`--skip-source-rehash` 永远 block；
声明产物缺失也 block；通过还需要协调者复核原始证据（门禁无法证明原始日志未被伪造）。该复核
已在阶段二之前完成：完整门禁运行 8/8 通过，即 #14 的验收证据。

### 1.6 Harness 能力（阶段一产物，无模型）

`harness_preflight.py` 在花模型预算**之前**证明环境可用，其报告是门禁
`tool-environment.json` 的来源：harness/model/provider、终端、文件系统、端口、源码获取、
JDK 25 构建/安装、bridge CLI（MCP 可选）、bridge smoke 与 lab 管理。

```bash
python tools/harness_preflight.py list
python tools/harness_preflight.py run --config examples/coldstart/harness-pi.json \
    --out labs/coldstart/preflight \
    --run-dir labs/coldstart/run-20-01 \
    --set "build.java=<jdk25>/bin/java.exe" \
    --set "bridge.command=<mc-bridge executable>"
```

输出：`preflight.json`、`preflight.md` 与 `probes/` 下的逐项记录。参考配置保留
`27190-27199` 端口，当前记录 model `deepseek-flash`、provider `deepseek`。`SKIP` 绝不算
`PASS`。

## 阶段二：全新冷启动精确配方

本节是已接受 #20 运行的复现配方（见最终验收表）。它需要已复核的阶段一通过、不同于公开校准的
全新密封 challenge、校验通过的 fixture、两个 lab 加载 #18 审计 mod，以及全新 pi 会话；这些
现在都已合并进 `main`。下面的示例端口保持在冷启动配置保留的 `27190-27199` 区间内。

### 2.1 固定 Harness 默认值

| 项目 | 值 |
| --- | --- |
| pi | `C:\Users\MSI-NB\.pi\agent\bin\pi.cmd`（0.87.1） |
| provider / model | `deepseek` / `deepseek-flash`（settings 默认） |
| thinking | `max`（`PI_REASONING_LEVEL`、settings 默认） |
| session 文件 | `$PI_SESSION_FILE`，同时用 `--session` 显式传入 |
| harness 配置 | `examples/coldstart/harness-pi.json` |
| 任务输入 | `examples/coldstart/task-minecart-rom.md`（无答案、无脚本） |

### 2.2 干净上下文规则

* 用 `run_trace.py init --visible-root` 与 `--exclude` 记录会话实际可见的内容；世界的原始内存
  数据（完整 NBT、tick 顺序、库存）刻意**不**隐藏，但本设计历史、校准记录、答案、任何既有
  解法脚本与预写 logger 都不得可见或预载。
* 没有能力沙箱：完整文件系统/源码/工具仍然允许。可见性是记录的事实，不是过滤。
* 使用不同于公开校准的 challenge 程序，并把它的文件放在仓库之外。

### 2.3 有序命令

```powershell
# 1. 为本次冷启动生成全新未见过 challenge（评测端输入）
python examples/minecart-rom/runner/minecart_rom.py challenge --seed <fresh> --out <outside repo>/challenge.json
python examples/minecart-rom/runner/minecart_rom.py up --lab rom20 `
    --rcon-port 27194 --server-port 27195 --vantage-port 27196 --bridge-port 27197 `
    --interface-mod labs/_cache/mods/mc-agent-interface-0.6.0.jar --java "<jdk25>/bin/java.exe"
python examples/minecart-rom/runner/minecart_rom.py init --lab rom20 --program <outside repo>/challenge.json `
    --records labs/rom20/records
python examples/minecart-rom/runner/minecart_rom.py validate --lab rom20 --ready labs/rom20/records/ready-snapshot.json

# 2. 在模型会话之前先建立 run
python tools/run_trace.py init --run-dir labs/coldstart/run-20-01 --run-id rom13-stage20-01 `
    --task-file examples/coldstart/task-minecart-rom.md `
    --harness pi --model deepseek-flash --provider deepseek --harness-version 0.87.1 `
    --task-player-uuid <fixture fake-player uuid> `
    --visible-root <experiment workspace> --exclude <design/calibration paths>

# 3. 预检（同时向 run 追加 mark）
python tools/harness_preflight.py run --config examples/coldstart/harness-pi.json `
    --out labs/coldstart/run-20-01/preflight --run-dir labs/coldstart/run-20-01 `
    --set "build.java=<jdk25>/bin/java.exe" --set "bridge.command=<mc-bridge executable>"

# 4. 全新模型会话（默认 provider/config）。在干净 worktree 中启动：
#    --no-context-files 阻止自动加载 AGENTS.md/CLAUDE.md，
#    同时仍要用 visibility.json 记录会话实际可见的内容。
C:\Users\MSI-NB\.pi\agent\bin\pi.cmd --provider deepseek --model deepseek-flash --thinking max `
    --no-context-files --print --session F:\...\rom13-stage20-01-session.jsonl `
    "@examples/coldstart/task-minecart-rom.md"

# 5. 导入 Agent 自己的轨迹，然后声明证据并审计
python tools/run_trace.py import-pi --run-dir labs/coldstart/run-20-01 --session "$env:PI_SESSION_FILE" --phase agent
python tools/run_trace.py validate --run-dir labs/coldstart/run-20-01
python tools/run_audit.py run --run-dir labs/coldstart/run-20-01 --json
```

审计读取 `evidence.json` 声明（`agent_logger`、`test_mod`、`answer`、`oracle`、`fixture`、
`restore`、`preflight`、`review`），计算五项 flag 并写出 `audit/audit.json` + `audit/audit.md`。
退出码：`0` PASS、`1` FAIL、`2` PENDING。必需的语义复核
（`machine_operated.causality` 等）用 `run_trace.py review` 记录；未解决的复核让 flag 保持
PENDING，绝不是通过。关键记录缺失一律 fail closed。

若本页与[可审计的冷启动运行](coldstart-protocol.md)冲突，以那一页为契约，逐字复用那里的命令
参数。

## 阶段三：已接受回归

#21 已接受并合并（[#34](https://github.com/guajun/mc-agent/pull/34) `b54e1e8`），最终独立评审
[5325226293](https://github.com/guajun/mc-agent/pull/34#pullrequestreview-5325226293)（head
`ae5cc96`）。其原有前置均已满足：已复核、真实成功的 #20；每次运行都用真实游戏数据（不回放
旧日志、不调用模型）；3 个全新实例；2 个校准程序；1 次冷下载；8 个声明负例；以及同大小 jar
迭代后的部署/重启/重新恢复。回归结果与原始 #20 能力证据在上表中明确区分。

复现命令与语义：[rom21-regression.md](rom21-regression.md) 与
[ROM21 证据包](evidence/rom21-regression/README.md)。

## 最终状态

* 子 issue #14-#21 均已带证据关闭；父级验收记录见
  [issue #13](https://github.com/guajun/mc-agent/issues/13)。
* [bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7)（可选 Hermes webhook 兼容）
  仍开启，不在本次验收范围内。
* 本次验收不再规划运行时工作。任何新工作都应是新 issue 加新证据包，而不是改写已冻结的包。

## 状态诚实性规则

* 保留每次失败及其原始日志；按 `INFRA_ERROR` > `FIXTURE_INVALID` > `AGENT_FAIL` 优先级分类。
* 绝不把他人的运行证据重标为本 run 的证据（已合并的 bridge#6 集合是另一次真实运行；阶段一
  证据不是 Agent 证据）。
* 绝不修改源存档或原始脏 checkout；实验只在一次性 lab 副本上进行。
* GitHub issue 已关闭不是验收；绿色 selftest 不是实机门禁；脚本通过不是模型自主。

另见：[阶段一集成门禁](stage1-gate.md)、[阶段一集成运行](stage1-integration.md)、
[可审计的冷启动运行](coldstart-protocol.md)、[ROM21 回归](rom21-regression.md)、
[审计测试 mod](minecart-audit.md)、[构建 mod](mod-building.md)、
[实验室服务器](lab-server.md)、[分叉校验](fork-verify.md)、[工具](tools.md)。
