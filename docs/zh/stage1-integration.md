# 阶段一集成运行

本页记录可复现的**组合式**阶段一集成：两个真实 Fabric 实验室、自构建的 smoke mod、已提交的 #18 审计 mod、真实工具轨迹，以及门禁结论。它是各前置项与阶段二冷启动之间的桥；并刻意停在门禁的 `blocked` 线上：#15 地图 fixture 与 bridge#6 恢复证据尚未落地。

!!! warning "不做 ROM 解法"
    场景只是一个通用音符盒把一摞箱子矿车推入虚空。它是接线验证，不是 ROM，也不是 Agent 运行。这里不写 Agent logger，也不给未来的冷启动喂答案。

## 运行内容

2026-09-26，在本 worktree（门禁仓库 head `32aa245`）内，端口 **27240-27249**：

| 步骤 | 结果 |
| --- | --- |
| 配置两个实验室（`rom13-src` source_audit 27240-27243，`rom13-exp` experiment 27244-27247），部署 interface mod `0.6.0`（sha256 `45f12e16…`，commit `3b93ceb`） | 两个 lab 的 `lab_server.py identity` / `verify --require-vantage` 均通过 |
| 用 `build_mod.py` 构建 `examples/smoke-mod` 并作为必需测试 mod 部署 | 两个 lab 加载成功；`mcagent-smoke status`/`sample` 可读 |
| 两个 lab 停止/重启 | 前后 `worldDir` 相同，实例保持隔离 |
| 同大小 jar 更新：改动一个字符的 marker、同名重新部署、重启 | 两次都是 15177 字节，sha256 变化，重启后服务端日志显示 **v2** marker |
| 响亮失败探针：构建错误、入口点错误、缺失 Fabric 依赖 | 三者都被观测并记录 |
| 只读复制已提交的 #18 审计 mod（`502f561`）并构建，在通用场景上运行 | 产生真实 303 事件 JSONL，音符生效，矿车被移除 |
| 用 `run_trace.py` 记录每个操作者命令 | 131 次调用且都有结果，覆盖 terminal/file/source/mcp 四类 |
| 前后只读哈希源存档 | 未变化（`8cd54c86…`） |

命令（路径都在 `labs/rom13-integration/`）：

```bash
python tools/stage1_integration.py plan
python tools/stage1_integration.py run                 # 完整实机运行，热缓存约 4 分钟
python tools/stage1_integration.py run --bundle-only   # 不起 lab，只重新归一化
```

驱动会写出 `summary.json`、`summary.md`、lab 目录、轨迹、门禁包与 `assemble-report.json`。持久化的精简事实见 [`docs/evidence/rom13-stage1/integration-summary.json`](evidence/rom13-stage1/integration-summary.json)。

## 无损证据映射

`tools/stage1_evidence.py` 把各前置格式投影到门禁 schema，不制造事实。每条规范记录都在 `detail.raw` 下保留完整原始记录并带源文件 sha256；映射报告记录计数、排除项与缺口。

### #18 审计 JSONL -> 门禁审计事件

真实 #18 schema（按 `502f561` 的 `tools/minecart_audit.py` 校验）使用 `seq`/`tick`/`run`/`inst`/`session`/`phase`/`type` 及分类型字段。适配规则：

| #18 | 门禁 |
| --- | --- |
| `seq` + `session` | `event_id = "<session>:<seq>"`，`seq` 按身份严格递增校验 |
| `run` / `inst` | `run_id` / `instance_id`，与声明的 run/实例核对 |
| 配置里的 dimension（或事件携带值） | `dimension` |
| `phase` | `experiment -> agent`、`restore -> restore`、其余 `init`；原始 phase 保留 |
| `input_attempt` / `input_processed` | 同名事件；`operator` 映射为 `actor_uuid`（对象 `{uuid, name}` 取 `uuid`）；null actor 只有在有记录的 `actor_provenance` 时才允许 |
| `input_request` | 原样保留（不算门禁操作） |
| `cart_exit` | `cart_emitted`；`captured_before_removal` 由后续带有序库存的 `cart_remove` 推导 |
| `cart_remove` | `cart_removed`；`reason` 映射为 `removal_reason`，`pos` 投影 |
| `cart_tracked`、`cart_sample`、库存/传送/重载事件 | 原样保留，审计仍可读取 |
| `session_start`、`audit_ready`、`audit_end`、`audit_incomplete` | **从规范 tick 流中排除**（它们带启动/关闭 tick，常常是 0），在映射报告里计数；原始文件完整保留并带哈希 |

实机运行：**303 条原始事件，投影 300 条，0 缺口，排除 3 条 session meta**（2 次尝试、1 次处理、1 次出堆、1 次移除、289 次采样）。

### #19 trajectory.jsonl -> 门禁工具轨迹

按 `call_id` 配对 `call`/`result`；没有结果的调用是缺口而不是静默行。`arguments`、`result`、`error`、时间戳与记录的实例都会带过，完整记录保留在 `detail`。只有存在 join 时才写 `trace-join.json`，且其中 `unmatched_*` 计数**由映射后的产物计算**，不是照抄声明。

驱动把它实际发出的 RCON 调用 join 到由其造成或观测到的审计事件。实机运行：131 次调用、136 条 join、`unmatched_agent_events = 0`、`unmatched_tool_calls = 129`（构建、文件、git、探针等非游戏调用）。门禁按完整 run/instance/dimension 身份与 tick 校验每条 join。

### #16 真实身份 -> 门禁 player context

`tools/stage1_evidence.py identity` 从已验证的 `docs/evidence/rom13-meta16/live-identity.json` 提取门禁的五个 case：

| 门禁 case | 来源（原始探针步骤） |
| --- | --- |
| `task_bind` | `task_entry.user.uuid` + `raw.task-start.alice` |
| `hit` | `raw.player.alice.uuid`（`found: true`，`matchedBy: uuid`） |
| `miss` | `raw.player.bob.miss`（身份查到；视线射线偏向别处） |
| `two_players` | Alice + Bob 响应，`other_uuid` = Bob |
| `unknown_identity` | `raw.player.unknown.uuid`（`found: false`，错误原文记录） |

同时带上入口契约（`external-cli`，`native_chat_verified: false` 并附 `timing: broadcast` 说明）以及证据自身记录的 interface/bridge commit。

## 门禁结果

`python tools/stage1_evidence.py assemble --bundle labs/rom13-integration/bundle --spec … --source-world "<存档>"`

| Check | 结论 | 原因 |
| --- | --- | --- |
| `fixture_map` | blocked | #15 地图 manifest/初始化未落地 |
| `restore_fidelity` | blocked | bridge#6 快照/恢复证据未落地 |
| `player_context` | **pass** | 真实 #16 身份，五个 case 与版本固定齐全 |
| `agent_dev_capability` | blocked | 冷启动 harness 的 `tool_environment` 与 fixture 相关 `smoke_mod` 属阶段二证据 |
| `independent_test_mod` | blocked | 仍需 test-mod manifest、负例与审计生命周期 |
| `trace_persistence` | blocked | 仍需缺日志检测证据与 Agent 阶段运行 |
| `smoke_fixture_validity` | blocked | smoke 套件（offline/player/restore）、校准与 fixture 版本锁依赖 #15/#19 |
| `evidence_integrity` | blocked | 取决于缺失产物 |

总体：**blocked**（退出码 3），1 个 pass、0 个 fail。驱动报告无缺口：它提供的每个事实都是实机观测的，无法提供的事实一律扣留而不是断言——这正是门禁保持 blocked 而非误报通过的原因。

## 精确剩余依赖

1. **#15 fixture**：`fixture-manifest.json`（不可变地图 URL/sha256）、三条带 child-run 来源的 `init-runs.jsonl`、fixture 假人身份、覆盖 fixture 与两个 lab 世界的无命令方块扫描、清理/重建记录。
2. **bridge#6 恢复**：`snapshot-before/` 与 `snapshot-after/` 协议树、绑定声明 source/experiment 实例的 `restore-record.json`、`source-unchanged.json` 与六个失败场景。
3. **#18/#19 测试 mod 完整性**：`test-mod-manifest.json`、`negative-cases.jsonl`（无操作、错误位置、仅 marker、仅答案）与 `audit-lifecycle.json`；#19 的 `missing-log-detection.jsonl`。
4. **冷启动（#19/#20）**：harness `tool-environment.json`、五个 smoke 套件（`smoke_offline`、`lab_boot`、`fake_player_mcp`、`snapshot_restore`、`test_mod_load`）、fixture 校准、`version-lock.json`，以及 Agent 自己的工具—游戏 join。
5. 这些产物就绪后重跑 `stage1_integration.py run`（或映射命令），由门禁裁决。只有完整 `pass` 才放行阶段二。

## 限制

* 场景是通用接线验证，不操作 ROM 机器，不构成 #14 验收。
* 本次 join 是测试端（RCON 调用）的；阶段二必须 join Agent 自己的调用，门禁会强制区分。
* #18 审计 mod 构建自已提交的 PR #26 head `502f561`；该 PR 若在合并前变化，需按新版本重新验证适配映射。
* 快照 `meta.json` 无法证明来自哪个实机实例，因此恢复校验通过 restore record 与审计/轨迹来源绑定端点身份（见[门禁限制](stage1-gate.md)）。

另见：[阶段一集成门禁](stage1-gate.md)、[冷启动协议](coldstart-protocol.md)、[构建 mod](mod-building.md)、[实验室服务器](lab-server.md)。
