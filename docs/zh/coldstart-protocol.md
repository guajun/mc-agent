# 可审计的冷启动运行

**冷启动运行**是一个真实智能体在全新上下文里，通过 Toolkit 研究并操作一个
Minecraft 世界的一次尝试。本页的目标比实验本身更窄：定义*环境契约*、*运行入口*、
*轨迹落盘*和*判定*，让事后审查不必相信智能体的最终回答，也能看清实际发生了什么。

这是 [mc-agent#19](https://github.com/guajun/mc-agent/issues/19) 的方案。它不给
mc-agent 新增模型客户端或会话后端：模型归 Harness，证据归 run 目录，这些工具只负责
记录和核查事实。

!!! warning "不预写解法"
    Minecart ROM 冷启动的任务输入是
    [`examples/coldstart/task-minecart-rom.md`](https://github.com/guajun/mc-agent/blob/main/examples/coldstart/task-minecart-rom.md)。
    它只说明任务和"必须真实运行机器"的要求，不含答案、成功脚本或既有轨迹。
    冷启动之前不要往里加解法。

## 1. 先固定 Harness

首轮冷启动使用**用户手动启动、带普通开发工具的 Harness**（终端、文件读写、源码/依赖
获取、可构建的 JDK，以及 bridge JSON CLI 或 MCP）。受限的 Hermes webhook route
**不能**充当这个 Harness：它的 per-route `toolsets` 刻意只暴露 Bridge MCP，
没有终端和文件工具，route 上的智能体无法自己写、编译、安装 logger。Webhook 是后续
可选路径，见 [Hermes 无人值守](hermes-unattended.md)。

Harness 配置是一个小 JSON 文件，参考配置是
[`examples/coldstart/harness-pi.json`](https://github.com/guajun/mc-agent/blob/main/examples/coldstart/harness-pi.json)。
它声明 harness 命令、版本探测方式、记录用的 model/provider、shell 探测、要访问的
源码端点、最低 JDK 版本、bridge 命令和保留端口。机器相关值（JDK 路径、bridge 可执行
文件）可以不改文件直接覆盖：

```bash
python tools/harness_preflight.py run \
    --config examples/coldstart/harness-pi.json \
    --out labs/coldstart/preflight \
    --run-dir labs/coldstart/run-01 \
    --set "build.java=C:\path\to\jdk-25\bin\java.exe" \
    --set "bridge.command=C:\path\to\.venv\Scripts\mc-bridge.exe"
```

配置在智能体启动**之前**固定。之后修改配置会让本次运行失效：preflight 报告记录了它
实际检查过的值。

## 2. 环境契约：`harness_preflight.py`

`tools/harness_preflight.py` 回答一个问题：在花掉模型预算去踩坑之前，这个 Harness
能不能碰到智能体需要的一切？每项检查都把自己的命令、退出码、完整输出和哈希写进
`<out>/probes/`；汇总在 `<out>/preflight.json` 和 `preflight.md`。

| 检查 | 证明什么 | 说明 |
| --- | --- | --- |
| `harness_identity` | 配置的 harness 命令能回应版本探测 | model/provider 作为记录，不涉及密钥 |
| `terminal` | Harness 能运行 shell 命令 | shell 来自配置 |
| `filesystem` | 写入、读回并哈希一个 nonce 文件 | 字节一致 |
| `ports` | 预留的 API/mod 端口可用且在范围内 | 默认范围 27190–27199 |
| `source_fetch` | 所有配置的源码/依赖端点可达（Fabric meta、Modrinth） | 配置 `build.pip_packages` 可加 pip 探测；任一端点失败即检查失败 |
| `build_install` | 满足 `build.min_java` 的 JDK 能编译并打包探测 jar，且安装后的字节哈希一致 | 26.2 需要 Java 25；PATH 上的 JDK 21 会按设计失败 |
| `bridge_cli` | `mc-bridge --help` 可运行 | JSON CLI 是必需的调用面 |
| `bridge_mcp` | `mc-bridge mcp --help` 可运行 | 可选；缺 `mcp` 附加依赖时 SKIP，因为 JSON CLI 是可接受的替代 |
| `bridge_smoke` | `tools/smoke_offline.py` 以 echo 后端跑通 | 在保留端口上用假 mod 跑真实 bridge + loop 进程 |
| `lab_management` | `lab_server.py list` 可用；配置实验室且启用 `start` 时，真实无头 Fabric 服务器被创建、固定到保留端口、启动、回应 RCON `list`，然后停止 | 记录 `lab.json` 与控制台哈希；start 被禁用时是 SKIP，审计的 preflight 交叉校验会拒绝它 |

`--checks`/`--skip` 选择子集；`SKIP` 绝不算 `PASS`。退出码 0 表示总体 PASS，
1 表示至少一项被选检查失败。探测本身崩溃算该检查失败，不会让 preflight 崩溃。

`lab_management` 会启动真实服务器，是慢检查；参考配置会跑它。开发时用快速子集：

```bash
python tools/harness_preflight.py list
python tools/harness_preflight.py run --config <config> --out <dir> \
    --checks harness_identity,terminal,filesystem,ports
```

传入 `--run-dir` 时，报告还会以 `mark` 记录追加到运行轨迹，便于审计看到本次运行的
环境。

## 3. 开启一次运行：`run_trace.py init`

```bash
python tools/run_trace.py init \
    --run-dir labs/coldstart/run-01 \
    --run-id run-01 \
    --task-file examples/coldstart/task-minecart-rom.md \
    --harness pi --model <model> --provider <provider> --harness-version <version> \
    --note "cold start; fresh session"
```

`init` 写出：

```
labs/coldstart/run-01/
  run.json            运行清单：harness、model、任务哈希、仓库 commit、导入与产物
  task.md             交给智能体的任务原文（绝不是答案）
  visibility.json     智能体可见的每份文档/Skill：路径 + sha256
  visibility/files/    这些文件的字节副本（小文件）
  trajectory.jsonl    仅追加的规范记录
  evidence.json       外部证据声明（审计前填写）
  preflight/          可选的 harness_preflight.py 输出
  audit/              run_audit.py 输出
```

**干净上下文规则。** 会话不得携带本仓库的设计讨论、标准答案、先前成功脚本或解题轨迹。
任务就是上面的任务文件；可见文档和 Skill 由 `visibility.json` 记录；不提供其它输入。
世界的完整内存数据**不隐藏**：协议只记录可见内容，绝不过滤库存、NBT 或 tick 顺序。
证据快照是清单，不是沙箱。

**导入 Harness 自己的轨迹。** pi 运行会把工具调用和返回写进会话 JSONL
（`$PI_SESSION_FILE`）。会话结束后把它拷进 run 并翻译成规范记录：

```bash
python tools/run_trace.py import-pi --run-dir labs/coldstart/run-01 \
    --session "$PI_SESSION_FILE" --phase agent
```

原始会话复制到 `imports/` 并计算哈希。其它 Harness 可以直接用
`record`/`call`/`result` 追加同样的记录，或自带适配器；规范 schema 不依赖 pi。

!!! note "先开启运行，再启动会话"
    `init` 记录运行创建时间。之后导入的会话必须晚于该时间，否则 `validate` 会报
    时间戳乱序。先建 run，再启动全新的 Harness 会话。

## 4. 轨迹落盘

`trajectory.jsonl` 仅追加，每行一个 JSON 对象，每次追加都刷盘。`seq` 由记录器分配；
`at` 是 RFC 3339 UTC 时间。

| 记录 | 必填字段 | 含义 |
| --- | --- | --- |
| `header` | `schema`、`run_id` | 由 `init` 写入 |
| `call` | `call_id`、`tool`、`arguments` | 一次工具调用：文件、终端、bridge、构建、脚本、MCP 调用 |
| `result` | `call_id`、`status`（`ok`/`error`/`open`） | 调用的返回或错误；`duration_ms` 可选 |
| `phase` | `phase`、`actor` | 生命周期切换 |
| `mark` | `name` | 标注事实（preflight、产物、fixture……） |

阶段是 `prepare`（测试侧准备）、`restore`（副本恢复）、`agent`（智能体自身工作）、
`audit`（事后审查）。角色是 `operator`、`test`、`agent`、`restore`、`harness`、
`reviewer`。"测试准备""智能体操作""副本恢复"因此能在同一个文件里区分开。

便捷命令：

```bash
python tools/run_trace.py call   --run-dir <run> --call-id c1 --tool bash \
    --arguments '{"command": "javac LoggerMod.java"}' --phase agent --actor agent
python tools/run_trace.py result --run-dir <run> --call-id c1 --status ok --result '{"text": "compiled"}'
python tools/run_trace.py phase  --run-dir <run> --phase restore --actor restore --instance lab-a
python tools/run_trace.py artifact --run-dir <run> --path build/logger.jar --label "agent logger jar"
python tools/run_trace.py review --run-dir <run> --id machine_operated.causality \
    --status resolved --by "reviewer name" --note "watched the replay" --evidence trajectory:c2
python tools/run_trace.py validate --run-dir <run>
```

`artifact` 把自写代码与构建产物复制进 `artifacts/`，把哈希记进 `run.json`。
`review` 追加一条显式审查结论（见第 5 节）。`validate` 检查结构：call id 唯一、
每次调用恰好一个返回、`seq` 与时间戳单调、阶段合法、task/visibility 哈希、导入
与产物哈希、证据声明哈希以及审查记录格式。缺返回是错误而非警告。

## 5. 证据声明

外部产物留在产生它的地方（智能体 logger 输出、测试 mod 文件），`evidence.json`
用相对 run 的路径声明它们；有 sha256 时审计会复核：

```jsonc
{
  "agent_logger": {
    "path": "agent/logger.jsonl",          // 智能体自己 logger 的原始输出
    "sha256": "...",                       // 会复核；变化即 INFRA_ERROR
    "normalized": "agent/logger.norm.jsonl" // 可选规范视图
  },
  "test_mod": { "path": "test/testmod.jsonl", "sha256": "..." },
  "answer":   { "path": "answer.json", "sha256": "..." },
  "oracle":   { "path": "oracle.json", "sha256": "..." },
  "fixture":  { "path": "fixture/ready.json", "sha256": "..." },
  "restore":  { "path": "restore/verify.json", "sha256": "..." },
  "preflight":{ "path": "preflight/preflight.json", "sha256": "..." },
  "review":   { "path": "reviews.jsonl", "sha256": "..." }   // 省略时用默认位置
}
```

### 测试侧事件（独立 oracle 侧）

独立测试 mod（[mc-agent#18](https://github.com/guajun/mc-agent/issues/18)）写一个
JSONL 文件。审计消费它的以下投影；mod 可以用自己的字段名对应这些别名（括号内）：

| 事件 | 字段 | 含义 |
| --- | --- | --- |
| `instance_ready` | `instance`、`dimension`、`tick` | fixture 已初始化 |
| `restore_started` / `restore_finished` | `instance`、`tick` | 副本恢复记录 |
| `input_attempt` | `actor`、`tick` | 只是*尝试*（右键回调、marker、命令 ack） |
| `input_processed` | `actor`、`tick` | 服务器真正处理了机器输入 |
| `cart_ejected` | `uuid`、`tick` | 一辆矿车离开了堆 |
| `cart_removed` | `uuid`、`tick`、`reason` | 矿车被移除（例如掉入虚空） |
| `end` | `tick` | 实验结束 |

`input_attempt` 绝不当作操作；`machine_operated` 需要 `input_processed`。真实格式归
#18 所有；如果字段名不同，用适配器映射。原始文件始终保留并哈希。

每条事件都可以带 `run_id`（别名 `runId`）；带就必须与运行清单一致。来自别的运行
的事件是 `INFRA_ERROR`，不是证据。

### 智能体 logger 事件（智能体侧）

logger 怎么写由智能体决定。为了审计，其输出（或经校验的规范视图）包含：

| 事件 | 字段 | 含义 |
| --- | --- | --- |
| `logger_armed` | `at`、`tick`、`instance` | 智能体 logger 已在运行，且**早于**机器被触发 |
| `cart_observed` | `uuid`、`at`、`tick`、`position`、`items` | 一辆被弹出的矿车在移除前被捕获 |
| `logger_flushed` / `logger_error` | `at` | 输出持久化 / 失败 |

如果声明了 `normalized` 视图，审计用声明的 `sha256` 核对原始文件，用 `normalized_sha256`
（绝不用原始哈希）核对规范文件，并要求**每条**规范记录都带等于原始 logger 哈希的
`source_sha256`；缺失或过期的 `source_sha256` 是 `INFRA_ERROR`。原始输出绝不被
规范视图替换。

### 显式审查结论

有些事实是语义性的：单靠解析无法判定，协议拒绝猜测。审计把这些事实列为
**required review**（必须审查项）；在运行携带显式结论之前，对应标志不能 PASS。
结论写在 `reviews.jsonl`（仅追加，同一 id 以最后一条为准），用
`run_trace.py review` 写入：

```jsonc
{
  "id": "machine_operated.causality",   // audit.json 里的必须审查项 id
  "status": "resolved",                 // resolved | rejected | pending
  "by": "审查者身份",                    // resolved 必须有 by 和 at
  "at": "2026-09-26T00:00:00Z",
  "note": "看了回放；这条被追踪的命令就是测试 mod 处理的那次",
  "evidence": ["trajectory:c2-noteblock", "testmod:seq3"]
}
```

- 没有记录或 `pending`：标志保持 PENDING；
- `resolved`（带 `by` 和 `at`）：恢复标志的机械结论；
- `rejected`：该标志 FAIL（`AGENT_FAIL`），因为审查者判定证据不足。

本次修订中的必须审查项 id：`machine_operated.causality`、
`machine_operated.ordering`、`logger_armed_before_activation.running`、
`logger_armed_before_activation.ordering`、`transient_outputs_captured.window`、
`transient_outputs_captured.inventory`、`agent_read_log.linkage`、
`answer_correct.oracle_independence`。机械检查通过但审查未完成的运行是 PENDING，
绝不是 PASS。

### 关联与来源

在计算任何标志之前，审计先核对运行身份，不一致就失败关闭：

- 每条记录都可以带 `run_id`；与运行清单不一致是 `INFRA_ERROR`，测试 mod、智能体
  logger 文件以及轨迹都适用；
- `imports/` 与 `artifacts/` 文件会按 `run.json` 重新哈希，被篡改的原始会话或构建
  产物是 `INFRA_ERROR`；
- 测试 mod 事件不得混用实例或维度；智能体 logger 事件必须命名与测试 mod 相同的
  实验实例/维度（字段支持别名 `instance`/`instance_id`、`dimension`/`dim`）；
- 声明的 preflight 必须与运行清单一致：Harness 名称/模型/提供方、仓库 commit、
  通过的 `ports` 检查，以及真正启动过服务器的通过态 `lab_management`；SKIP 或
  不同环境是 `INFRA_ERROR`；
- oracle 的 `evidence_refs` 必须能对到已声明的测试 mod/fixture/restore 证据
  （`testmod:seqN` 必须指向存在的记录）；`trajectory:`、`logger:`、`answer` 等
  智能体侧引用不算独立；无法解析的引用是 PENDING，完全没有独立引用则失败。
  答案必须恰好覆盖 oracle 的矿车：漏掉一辆就是 `AGENT_FAIL`。

## 6. 审计判定：`run_audit.py`

```bash
python tools/run_audit.py run --run-dir labs/coldstart/run-01
```

它计算五个标志，每个都带指向上述记录的证据引用，输出 `audit/audit.json` 与
`audit/audit.md`。退出码：`0` PASS、`1` FAIL、`2` PENDING。

| 标志 | PASS 条件 | 必须审查项（id） | 证据形式 |
| --- | --- | --- | --- |
| `machine_operated` | 至少 1 条 `input_processed`；至少 1 次 `agent` 阶段的智能体调用按 时间/tick/序号 排在首个处理事件**之前** | `machine_operated.causality`；没有共同排序层时加 `.ordering` | `testmod:seq…`、`trajectory:<call_id>` |
| `logger_armed_before_activation` | `logger_armed` 按 时间→tick→同 tick 序号 排在首个 `input_processed` 之前（同实例/维度）；有智能体写入/构建/启动 logger 的调用 | `logger_armed_before_activation.running`；没有共同排序层时加 `.ordering` | `logger:seq…`、`testmod:seq…`、`trajectory:<call_id>` |
| `transient_outputs_captured` | 每个 `cart_ejected` uuid 都有 `cart_observed`，且捕获排在弹出**之后**、移除**之前**（同实例/维度）；每条捕获带 items 列表 | 无移除证据或无法排序时 `.window`；捕获无 items 时 `.inventory` | `testmod:seq…`、`logger:seq…` |
| `agent_read_log` | 最后一次捕获之后，有智能体调用既点名 logger、又返回被捕获的观测内容（uuid + 其物品，或完整有序物品表），且是合理的读取/执行调用 | 关联模糊时（只回显文件名、只有 uuid、有内容但没点名 logger、或无法排序）`.linkage` | `trajectory:<call_id>`、`logger:seq…` |
| `answer_correct` | 答案逐车匹配**已验证** oracle、覆盖 oracle 的每一辆车，且 oracle 引用能解析到独立游戏侧证据 | `answer_correct.oracle_independence` | `oracle:<ref>`、`oracle` |

审计刻意分成机械部分和语义部分。存在性、顺序、哈希和物品比较是机械的。因果（"这条
轨迹调用正是测试 mod 处理的那次"）、logger 是否真的被游戏加载、捕获窗口是否被证明、
读取关联以及 oracle 是否真正独立是**必须审查项**：审计用稳定 id 报出它们，
`apply_reviews` 让标志保持 PENDING，直到 `reviews.jsonl` 里有显式 `resolved` 记录
（如果是 `rejected` 则 FAIL）。没有 id 的 `needs_review` 只是信息备注，永远不能决定
PASS。第一轮允许人工语义审查；不要求另一个模型来判分。`audit.md` 会列出每个必须
审查项及其是否已解决。

机械排序绝不猜测。先比时间，再比 tick，再比同 tick 内序号。两条记录没有共同可比的
层时结果是 `unknown`，转为必须审查项；同一 tick 且没有序号也是 `unknown`。捕获排在
矿车弹出之前（例如触发前的盘点读取）直接 FAIL，只有 `input_attempt` 永远不能证明
操作。

**关键记录缺失时失败关闭。** 没有 `test_mod` 文件、没有 `input_processed`、没有
`logger_armed`、没有捕获、没有读取、没有 oracle，对应标志就失败或保持 pending——
不存在通往 PASS 的捷径。

**Oracle 规则。** `answer_correct` 要求 oracle 的 `status` 为 `verified`，
`evidence_refs` 能解析到已声明的测试 mod / fixture / restore 证据，且至少有一条
独立游戏侧引用。把智能体答案复制成 oracle 会失败；无法解析的引用是 PENDING。答案
必须恰好覆盖 oracle 的矿车——漏车的部分答案即使其余车匹配也是 `AGENT_FAIL`。
fixture 全新或未核验时，oracle 保持 `pending`；审计报 PENDING，绝不提前宣称正确。

## 7. 失败分类

| 分类 | 含义 | 例子 |
| --- | --- | --- |
| `AGENT_FAIL` | 证据存在，但智能体没操作/没捕获/没读取/答案错 | 只有 `input_attempt`、logger 太晚、漏捕获、答案不符 |
| `FIXTURE_INVALID` | 初始化或恢复不忠实 | fixture 报 `invalid`、restore 状态不是 `ok` |
| `INFRA_ERROR` | 工具或审计本身不可用/不一致 | 轨迹损坏、证据缺失或哈希不符、preflight 失败 |

优先级是 INFRA_ERROR > FIXTURE_INVALID > AGENT_FAIL。PENDING 不是失败：它表示这次
运行暂时无法判定（oracle 待核验、fixture/restore 证据尚未声明，或必须审查项未解决）。
第 19 项阶段可以是 PENDING，等前置工作落地后再判定。

## 8. 与前置项的关系

本页定义契约；真实冷启动在阶段一全部绿了之后才发生。其它工作流各自负责自己的部分：

| 部分 | 负责方 | 审计从中读取 |
| --- | --- | --- |
| 确定性 fixture + ready 快照 | [mc-agent#15](https://github.com/guajun/mc-agent/issues/15) | `fixture` 证据、`instance_ready` |
| 假玩家身份与服务端视角上下文 | [mc-agent#16](https://github.com/guajun/mc-agent/issues/16) | 测试 mod 事件里的 actor 身份 |
| mod 部署与实验实例身份 | [mc-agent#17](https://github.com/guajun/mc-agent/issues/17) | `restore`/重载证据、实验室端口与目录 |
| 独立测试 mod | [mc-agent#18](https://github.com/guajun/mc-agent/issues/18) | `test_mod` 事件 |
| 忠实快照恢复 | [mc-agent-bridge#6](https://github.com/guajun/mc-agent-bridge/issues/6) | `restore` 证据 |

集成验收（真实冷启动被判 PASS）必须等待它们全部完成。本文档与工具可以并行开发、
并行测试——`tools/tests/` 下的合成 fixture 正是这么做的。

## 9. 不启动 Minecraft 验证工具

```bash
python -m unittest discover -s tools/tests -v
python tools/harness_preflight.py run --config <config> --out <dir> --checks terminal,filesystem
python tools/run_trace.py validate --run-dir labs/coldstart/run-01
python tools/run_audit.py run --run-dir labs/coldstart/run-01 --json
```

单元测试会构造正确和故意损坏的 run 目录（只尝试没处理、logger 太晚、移除后才捕获、
只有测试侧观察、oracle 待核验、fixture 无效、哈希被篡改），断言各标志与分类结果。
它们从不启动 Minecraft、不访问网络；真实证据来自 `preflight` 探测与独立的阶段一
冒烟检查。同一套测试在 CI（`.github/workflows/tools.yml`）里随 `tools/` 的每次
变更运行。
