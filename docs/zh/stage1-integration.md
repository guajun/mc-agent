# 阶段一集成运行

!!! success "完整门禁通过（评审修复运行，分支 `codex/rom13-fullgate`）"
    issue #14 的验收运行（`tools/stage1_fullgate.py`，run id
    `rom13-fullgate-20260926T011803Z`）在已合并的 #15 fixture 与 #18 审计 mod
    上执行**完整**组合阶段一流程，门禁结果为 **pass 8/8**（exit 0），并通过只读源世界重新哈希。带全部固定哈希的版本化摘要见
    [`docs/evidence/rom13-stage1/full-gate-summary.json`](evidence/rom13-stage1/full-gate-summary.json)。
    下文记录的 blocked 结果是更早的通用接线运行（#28，head `32aa245`），仅作历史保留。

本页记录可复现的**组合式**阶段一集成：两个真实 Fabric 实验室、自构建的 smoke mod、已提交的 #18 审计 mod、最终冻结的工具轨迹、已合并的 bridge#6 恢复证据，以及门禁结论。并停在门禁线上：这里不做任何阶段二工作。

## 完整门禁运行（`tools/stage1_fullgate.py`）

完整门禁驱动在端口 27240-27249 上以六个阶段运行（无 mod 对照使用 27150/27151）：`live`（本 run 三次独立 fixture 初始化、fork + 同 run 守卫恢复、在源副本与恢复副本上执行**有界真实 ROM 标定**：夹具玩家在悬停座上右键夹具音符盒，一个原始箱子矿车越过输出平面并自然落入虚空被移除，随后在同一世界副本上执行无 mod 对照；七类失败用例、真实 hit/miss 身份探针、四个负例与冻结轨迹）、`identity`（五条 player_context 记录）、`devcap`（真实构建/加载/依赖探针、同尺寸 jar 更新、重启后机器实体快照+验证、preflight 派生的工具环境、真实重启/状态隔离与冲突拒绝）、`trace`（冻结轨迹到门禁工具轨迹、因果 join、缺失日志检测）、`smoke`（五套 smoke 套件、无 mod 对照、原始计数器 hook 开销、版本锁）、`compose`（审计导出、恢复快照与失败用例、bundle 规格、门禁运行）。

评审修复运行 `rom13-fullgate-20260926T011803Z` 的关键事实：地图 `469548…1387`；本 run 三次 init 一致（`86215e40…`/`a3ba3757…`，09:19-09:21 本地时间）；恢复 10 个实体并验证（顺序哈希 `2df69346…`）；七类失败用例全部通过（含修复后的 `unverified`）；10 条 canonical agent 事件（两个实例，`input_processed` 走真实 `playNote` 路径）；60 次冻结调用、10 条 verified join（6 条直接 receipt + 4 条有界 tick 链）、0 个未匹配 agent 事件；无 mod 对照一致；hook 开销 101.99 ms（来自原始计数器）；27 条 evidence index 固定项；源世界 `8cd54c86…` 在运行前后分别观测且未改变。

```bash
python tools/stage1_fullgate.py live && python tools/stage1_fullgate.py identity \
  && python tools/stage1_fullgate.py devcap && python tools/stage1_fullgate.py trace \
  && python tools/stage1_fullgate.py smoke && python tools/stage1_fullgate.py compose
```

门禁只接受原始工件；原始日志保留在执行运行的 worktree 的 git-ignore 目录 `labs/fullgate-evidence/` 下，摘要固定其哈希。只有经过人工评审的 `pass` 才授权阶段二；驱动不做合并，也不关闭 issue。


!!! warning "不做 ROM 解法"
    阶段一只做夹具机器上的有界标定与独立审计（真实输入 -> 输出 -> 虚空移除），不是 ROM 解法，不是 Agent 运行。这里不写 Agent logger，也不给未来的冷启动喂答案。

## 运行内容

2026-09-26，在本 worktree（门禁仓库 head `32aa245`）内，端口 **27240-27249**：

| 步骤 | 结果 |
| --- | --- |
| 配置两个实验室（`rom13-src` source_audit 27240-27243，`rom13-exp` experiment 27244-27247），部署 interface mod `0.6.0`（sha256 `45f12e16…`，commit `3b93ceb`） | 两个 lab 的 `lab_server.py identity` / `verify --require-vantage` 均通过 |
| 用 `build_mod.py` 构建 `examples/smoke-mod` 并作为必需测试 mod 部署 | 两个 lab 加载成功；`mcagent-smoke status`/`sample` 可读 |
| 两个 lab 停止/重启 | 前后 `worldDir` 相同，实例保持隔离 |
| 同大小 jar 更新：改动一个字符的 marker、同名重新部署、重启 | 两次都是 15177 字节，sha256 变化，重启后日志显示 **v2** marker |
| 响亮失败探针：构建错误、入口点错误、缺失 Fabric 依赖 | 三者都被观测并记录 |
| 只读复制已提交的 #18 审计 mod（`502f561`）并构建，在通用场景上运行 | 真实 JSONL，音符生效，矿车被捕获并移除，`audit_end` 完整 |
| 用 `run_trace.py` 记录每个操作者命令并 **冻结** | 71 次调用 / 71 条结果 / 71 个唯一 id；覆盖 terminal/file/source/mcp 四类 |
| 前后只读哈希源存档 | 未变化（`8cd54c86…`） |

每次尝试都有唯一 run id（`rom13-integration-run-20260925T174503Z`，目录 `labs/rom13-integration/run-20260925T174503Z/`）；重跑会新建 run 目录，绝不向旧 run 追加。

命令（路径都在 `labs/rom13-integration/`）：

```bash
python tools/stage1_integration.py plan
python tools/stage1_integration.py run                 # 完整实机运行，热缓存约 4 分钟
python tools/stage1_integration.py run --bundle-only   # 从 normalize-inputs.json 逐字节可复现地重新归一化
```

驱动会写出 `summary.json`、`summary.md`、lab 目录、冻结轨迹、`normalize-inputs.json`、门禁包与 `assemble-report.json`。持久化的精简事实见 [`docs/evidence/rom13-stage1/integration-summary.json`](evidence/rom13-stage1/integration-summary.json)。

## 不可变、先冻结的轨迹

投影永远不读取仍在追加的文件：

1. 所有被追踪步骤结束、所有 lab 停止；
2. 轨迹复制为 `run-<stamp>/trajectory.final.jsonl`，`normalize-inputs.json` 固定其 sha256、字节数、调用/结果数，并要求 `calls == uniqueCallIds`（重复 call id 会让冻结失败，而不是被折叠）；
3. 归一化只使用冻结副本，带 `--expect-sha256`，且自身不被追踪。

`--bundle-only` 重新读取 `normalize-inputs.json`、校验所有固定哈希，并逐字节复现 `tool-trace.jsonl`、`audit-events.jsonl`、映射报告与 `bundle.json`（已连续三次比较一致）。

驱动 summary 会显式记录投影阶段：每个映射命令的退出码与缺口数、门禁总体状态、映射缺口与 **INFRA 错误**分开存放。退出码 1 且报告含缺口属于证据缺口，门禁应当保持 `blocked`；报告缺失、用法错误退出、或退出码 1 但无缺口属于 INFRA 失败。本修订复用了实机运行的冻结输入（未重跑实机）：唯一产物变化是新提升的 `input_attempt.request_seq`（原始事件 seq 183 -> 182）以及重新哈希它的证据索引；其余产物哈希不变，所有映射保持无缺口。

## 无损证据映射

`tools/stage1_evidence.py` 把各前置格式投影到门禁 schema，不制造事实：每条规范记录都在 `detail.raw` 下保留原始记录并带源文件 sha256；映射报告记录计数、排除项、生命周期校验与缺口。

### #18 审计 JSONL -> 门禁审计事件

真实 #18 schema（按 `502f561` 的 `tools/minecart_audit.py` 校验）使用 `seq`/`tick`/`run`/`inst`/`session`/`phase`/`type` 及分类型字段。适配器：

| #18 | 门禁 |
| --- | --- |
| `seq` + `session` | `event_id = "<session>:<seq>"`；`(tick, seq)` 按身份严格递增 |
| `run` / `inst` | `run_id` / `instance_id`，与声明的 run/实例核对 |
| 配置里的 dimension（或事件携带值） | `dimension` |
| `phase` | `experiment -> agent`、`restore -> restore`、其余 `init`；原始 phase 保留 |
| `input_attempt` / `input_processed` | 同名；`operator` 映射为 `actor_uuid`（对象取 `uuid`）；null actor 只有在有记录的 `actor_provenance` 时才允许 |
| `input_processed` 链接 | `requestSeq`/`attemptSeq`/`agentOp` **提升**到规范记录，并在各自时钟域内校验：被引用事件必须同 session/run/instance/dimension 且严格早于引用者（seq 与 tick）。跨 session、身份不符、前向或悬空引用、第二个 *processed* 复用 request/attempt、`agentOp` 无 attempt 都是缺口 |
| `input_attempt.requestSeq` | 同样提升并按同 session/先后规则校验（一个 request 可由其 attempt 与消费它的 processing 各引用一次；第二个 attempt 复用即缺口） |
| `input_request` | 原样保留（不算门禁操作） |
| `cart_exit` | `cart_emitted`；`captured_before_removal` 要求移除事件记录的 `capturedPath` 含 `before_drop`（且有序库存存在） |
| `cart_remove` | `cart_removed`；`capturedPath` 必需，`reason` 映射为 `removal_reason` |
| `cart_tracked`/`cart_sample`/库存/传送/重载 | 原样保留；cart 身份要求 `epoch`（不再静默把缺失/0 当成 1） |
| `session_start`/`audit_ready`/`audit_end`/`audit_incomplete` | 校验并计数，不再静默丢弃：未闭合 session、`audit_end` 非 complete、`truncated` 或 `audit_incomplete` 都会变成缺口并扣留产物 |

实机运行：投影 296 条事件、0 缺口、生命周期完整（`session_start` -> `audit_end status=complete`），3 条 session meta 校验/排除。

### #19 trajectory.jsonl -> 门禁工具轨迹

按 `call_id` 配对 `call`/`result`；无结果、重复 `call_id`（绝不后写覆盖）、`run_id` 不匹配都是缺口；声明多个实例时缺少实例也是缺口。唯一允许的默认是"只声明一个实例"，并在行内标注 `detail.instance_source = "single-declared-instance-default"`。实机运行 71 行全部显式标注实例（18 条 `rom13-src`、53 条 `rom13-exp`），0 条默认。

### join：只有显式证明才算 verified

只有当 join (a) 唯一解析到一次调用与一个审计事件、(b) 在 run/instance/dimension 与 tick 上匹配、(c) 指向 agent 阶段事件、(d) 带显式 `proof`（`producer`、`basis`、`clock`）时，才写入 `trace-join.json`。更弱的关联——包括驱动把 RCON 命令按类归因到可能由它造成的事件——只留在 `mapping/join-candidates.json`（`verified: false` 并附 basis 说明），门禁产物扣留、门禁阻塞。marker 或最近时间窗永远不算操作。

### #16 真实身份 -> 门禁 player context

`tools/stage1_evidence.py identity` 从已验证的 `docs/evidence/rom13-meta16/live-identity.json` 提取五个 case（`task_bind`、`hit`、`miss`、`two_players`、`unknown_identity`），并带上入口契约（`external-cli`，`native_chat_verified: false` 与 `timing: broadcast` 说明）和证据自身记录的 interface/bridge commit。

## 独立的 bridge#6 恢复收集

已合并的 bridge#6 证据（`bridge6` worktree，head `4116ebb`，真实运行 `bridge6-src`/`bridge6-dst`，端口 27060-27065）由 `tools/stage1_evidence.py restore-evidence` 收集并校验：

* 重新核对全部 9 个 index stage 哈希；
* 用门禁自身的规则比较源快照 `bridge6-fixture` 与目标验证快照 `restore-check-bridge6-dst`：orderHash（`a663c5c0dfd7ec6f`）、计数、NBT、pos/vel 全部一致；
* `source-before` 与 `source-after-restore` 证明源 lab 状态未变；
* 失败证据齐全：错误端点被拒绝、重复矿车被拒绝、库存突变探针存在。

收集只复制两个快照的 `meta.json`/`entities.jsonl` 并固定哈希。它是**另一次实机运行**，刻意不与通用集成 run 关联：旧 lab 之间没有共享的工具/事件时钟，因此不声称门禁的同 run 恢复绑定。

## 更早运行：门禁结果（blocked）

`python tools/stage1_evidence.py assemble --bundle labs/rom13-integration/bundle --spec … --source-world "<存档>"`

| Check | 结论 | 原因 |
| --- | --- | --- |
| `fixture_map` | blocked | #15 地图 manifest/初始化尚未被接受 |
| `restore_fidelity` | blocked | 本 run 没有同 run 的快照/恢复产物 |
| `player_context` | **pass** | 真实 #16 身份，五个 case 与版本固定齐全 |
| `agent_dev_capability` | blocked | harness `tool_environment` 记录（来自 `harness_preflight.py`，无模型调用）与通用 `smoke_mod` 记录未在本 bundle 声明 |
| `independent_test_mod` | blocked | 仍需 test-mod manifest、负例与审计生命周期声明 |
| `trace_persistence` | blocked | 尚无经证明的 agent 阶段 join 与缺日志检测产物 |
| `smoke_fixture_validity` | blocked | smoke 套件（offline/player/restore）、校准与 fixture 版本锁依赖 #15/#19 |
| `evidence_integrity` | blocked | 取决于缺失产物 |

总体：**blocked**（退出码 3），1 个 pass、0 个 fail。驱动提供的每个事实都是实机观测的，无法提供的事实一律扣留而不是断言——这正是门禁保持 blocked 而非误报通过的原因。

## 精确剩余依赖

1. **#15 fixture**：带不可变 URL/sha256 的地图 manifest、三条带 child-run 来源的初始化、fixture 假人身份、覆盖 fixture 与两个 lab 世界的无命令方块扫描、清理/重建记录。PR #27 仍在评审，**本页不视为已接受**；本页的 fixture 陈述基于其快照 `37fb824`，下次门禁运行前需按已接受/已合并 head 重新核实。
2. **同 run 恢复（bridge#6）**：针对门禁 run 实例的 snapshot-before/after、绑定 restore record、`source-unchanged.json` 与六个失败场景。已合并的 bridge6 证据另行收集（见上），不能重新标为本次 run 的证据。
3. **#18/#19 测试 mod 完整性**：`test-mod-manifest.json`、`negative-cases.jsonl`（无操作、错误位置、仅 marker、仅答案）与 `audit-lifecycle.json` 声明；`missing-log-detection.jsonl`。
4. **阶段一 harness 能力（#17/#19）**：harness `tool-environment.json`（来自 `harness_preflight.py`）、五个 smoke 套件、fixture 校准与 `version-lock.json` 都是阶段一产物。唯一属于阶段二的是 Agent 自己带显式证明的工具—游戏 join，它归 #20 审计，而非本门禁。
5. 这些产物就绪后重跑 `stage1_integration.py run`（或补齐输入后用 `--bundle-only`），由门禁裁决。只有完整 `pass` 才放行阶段二。

## 限制

* 场景是通用接线验证，不操作 ROM 机器，不构成 #14 验收。
* 驱动的 RCON—事件归因刻意保持未验证（候选而非证明）。门禁仍需要测试端带显式证明的 smoke join；阶段二审计另行 join Agent 自己的调用。
* #18 适配跟踪 PR #26 分支；本次 run 映射的是其 `502f561` 快照。下次门禁运行前需解析已接受/已合并 head，并按该树重新验证映射。
* 快照 `meta.json` 无法证明来自哪个实机实例，因此恢复校验通过 restore record 与审计/轨迹来源绑定端点身份（见[门禁限制](stage1-gate.md)）。

另见：[阶段一集成门禁](stage1-gate.md)、[冷启动协议](coldstart-protocol.md)、[构建 mod](mod-building.md)、[实验室服务器](lab-server.md)、[Minecart ROM 验收 runbook](minecart-rom-runbook.md)。
