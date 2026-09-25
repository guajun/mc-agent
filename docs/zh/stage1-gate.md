# 阶段一集成门禁

[Issue #14](https://github.com/guajun/mc-agent/issues/14) 是矿车 ROM 前置需求（#15、bridge#6、#16、#17、#18、#19）与阶段二冷启动之间的就绪门禁。某个仓库的单测通过、或者某个工具返回 `issued`，都**不是**整条实机链路可用的证据。本门禁消费这些前置项产出的实机证据，尽可能自行重算，并在任何缺项上拒绝通过。

门禁由 `tools/stage1_gate.py` 实现（纯标准库）。它不启动游戏、也不制造证据：它读取一个*门禁包*（包含 `bundle.json` 与原始证据的目录），并严格失败关闭：

```text
缺少 bundle / check / 证据，或 origin 为 scaffold     -> blocked
证据存在但无效、不一致、不完整                        -> fail
每个 check 都通过                                      -> pass
```

退出码：`0` 通过、`1` 失败、`3` 阻塞、`2` 用法错误。脚本无法把"前置未就绪"误当成成功。

## 状态（2026-09-26，本分支）

* 门禁工具、证据 schema 与离线自测已实现；`selftest` 通过 **152 项检查**（不涉及游戏与实机证据）。
* **目前没有任何实机前置证据**，所以对任何真实 bundle 执行 `check` 都是 `blocked`。本文档刻意不声称门禁已通过。
* 开发期间记录了独立基线：源存档（26.2-Fabric 实例下的 `Minecart ROM test`）的只读 tree 哈希为 `8cd54c86af9fa8d6b9ea33441fb21dac295cd2b5ddaa60327f5fb3a30255324a`（40 个文件、11 556 310 字节，使用下方规范排除列表）。没有写入任何文件，源存档只被以只读方式打开。
* 集成运行预留端口：**27240-27249**，通过 `run.allowed_port_ranges` 显式配置（编排便利，不是产品硬编码要求——见下）。

## 命令

```bash
python tools/stage1_gate.py scaffold labs/stage1-evidence      # 生成规范的空目录结构
python tools/stage1_gate.py list                                # 列出 check 与所需证据
python tools/stage1_gate.py list --json                         # 机器可读目录
python tools/stage1_gate.py check labs/stage1-evidence \
    --source-world "<只读源存档路径>" \
    --report stage1-report.json
python tools/stage1_gate.py hash-tree "<世界目录>"              # 确定性只读目录哈希
python tools/stage1_gate.py selftest                            # 门禁逻辑自测，不需要游戏
```

`check` 选项：

| 选项 | 含义 |
| --- | --- |
| `--report PATH` | 写出完整 JSON 报告 |
| `--json` | 打印 JSON 报告而非文本 |
| `--verbose` | 打印每条断言，而不只是问题 |
| `--source-world PATH` | 只读重算该目录的哈希，与 `run.source_world` 对比 |
| `--skip-source-rehash` | 跳过独立哈希；整个运行会变为 **blocked**，永远不会是 pass |
| `--port-range LOW-HIGH` | 收窄 `run.allowed_port_ranges`；超出已声明区间的值会失败 |

## 包结构

```text
stage1-evidence/
  bundle.json
  artifacts/
    fixture_map/               fixture-manifest.json, init-runs.jsonl,
                               player-identity.json, command-block-scan.json,
                               cleanup-rebuild.json, raw/...
    restore_fidelity/          snapshot-before/（协议快照目录）,
                               snapshot-after/, restore-record.json,
                               source-unchanged.json, failure-cases.jsonl
    player_context/            identity-records.jsonl, entry-contract.json,
                               version-pins.json
    agent_dev_capability/      tool-environment.json, jar-update.json,
                               smoke-mod.json, instance-isolation.jsonl
    independent_test_mod/      test-mod-manifest.json, audit-events.jsonl,
                               negative-cases.jsonl, audit-lifecycle.json
    trace_persistence/         tool-trace.jsonl, trace-join.json,
                               missing-log-detection.jsonl
    smoke_fixture_validity/    smoke-report.json, fixture-validity.json,
                               version-lock.json, evidence-index.json
```

`bundle.json` 是清单。`checks.<id>.evidence.<kind>` 指定每个证据相对包根目录的路径；路径使用 POSIX（`/`）、必须留在包内、不能含 `..`。

```json
{
  "schema_version": 1,
  "kind": "mc-agent.stage1.gate-bundle",
  "origin": "live",
  "generated_at": "2026-09-26T12:00:00Z",
  "run": {
    "run_id": "rom13-stage1-20260926",
    "issue": "guajun/mc-agent#14",
    "allowed_port_ranges": ["27240-27249"],
    "tool_category_map": { "terminal": ["bash"], "file": ["write"], "source": ["git"], "mcp": ["mcp"] },
    "child_runs": [
      { "run_id": "init-child-1", "instance_id": "exp-1" },
      { "run_id": "init-child-2", "instance_id": "exp-1" },
      { "run_id": "init-child-3", "instance_id": "exp-1" }
    ],
    "source_world": {
      "label": "Minecart ROM test",
      "path": "<只读源存档路径>",
      "before_tree_sha256": "<64 位十六进制>",
      "after_tree_sha256": "<64 位十六进制>"
    },
    "instances": [
      { "instance_id": "src-audit", "role": "source_audit", "dimension": "minecraft:overworld",
        "world_dir": "labs/rom13-src/world", "rcon_port": 27240, "bridge_port": 27241 },
      { "instance_id": "exp-1", "role": "experiment", "dimension": "minecraft:overworld",
        "world_dir": "labs/rom13-exp/world", "rcon_port": 27242, "bridge_port": 27243 }
    ]
  },
  "checks": {
    "fixture_map": {
      "evidence": {
        "fixture_manifest": "artifacts/fixture_map/fixture-manifest.json",
        "init_runs": "artifacts/fixture_map/init-runs.jsonl",
        "player_identity": "artifacts/fixture_map/player-identity.json",
        "command_block_scan": "artifacts/fixture_map/command-block-scan.json",
        "cleanup_rebuild": "artifacts/fixture_map/cleanup-rebuild.json"
      }
    }
  }
}
```

只有 `origin` 为 `live` 才可能通过。`scaffold`（以及其它任何值）无论其余内容多完整都是 blocked。`child_runs` 声明各次独立初始化；每个初始化样本引用其中之一，而不必强行共用同一个父 run id。

## Check 与证据 schema

`python tools/stage1_gate.py list` 会连同规范路径打印同一份目录。类型约定：`s` 字符串、`h64` 64 位小写十六进制、`h40` 40 位小写十六进制（commit）、`h16` 16 位小写十六进制（协议 `orderHash`）、`b` 布尔、`i` 整数、`n` 有限数、`v3` `[x, y, z]`、`t` ISO-8601 时间戳。

### 1. `fixture_map` —— 地图 fixture 固定且初始化确定（#15）

`fixture-manifest.json`

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `map.url` | s | 不可变/带版本的下载入口 |
| `map.sha256` | h64 | 地图文件哈希（与 `version_lock.fixture-map` 交叉核对） |
| `map.bytes` | i | > 0 |
| `map.mc_version` | s | 如 `26.2`（与 `version_lock.minecraft` 交叉核对） |
| `map.immutable` | b | true |
| `map.download_verified` | b | 冷缓存下载已验证 |
| `map.bad_hash_rejected` | b | 错误哈希被拒绝而非接受 |
| `mods[]` | list | >= 1 项，每项 `{name, version, sha256(h64)}`；`carpet` 项**必需**并与 `version_lock.carpet` 交叉核对 |
| `world.directory` | s | 副本名，绝不是源存档本身 |
| `world.tree_sha256` | h64 | 准备好的世界副本的 tree 哈希 |
| `world.files` | i | > 0 |

`init-runs.jsonl` —— 每次独立初始化一行，**至少 3 行**：

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `init_id` | s | 每次运行唯一 |
| `run_id` | s | 必须在 `run.child_runs` 中声明；必须使用 >= 3 个不同的 child run |
| `instance_id` | s | 必须等于该 child run 声明的实例 |
| `state_hash` | h64 | 所有初始化一致 |
| `order_hash` | h16 | 所有初始化一致 |
| `entity_count` | i | > 0，各次一致 |
| `inventory_total` | i | > 0，各次一致 |
| `early_output` | b | false |
| `ready` | b | true |
| `tick` | i | >= 0 |

`player-identity.json`：`uuid`、`name`、`dimension`、`pos`（v3）、`yaw`、`pitch`（n）、`source`（s，如 `carpet`）、`facing_target`（b true）、`server_vantage_uuid`（s，必须等于 `uuid`）。该 `uuid` 是绑定的任务玩家：`identity-records.jsonl` 中除 `unknown_identity` 外的每一行都必须使用它，每个 `input_attempt`/`input_processed` 审计事件也必须携带它（或按下方规则显式声明缺失）。

`command-block-scan.json`：`method`（s）、`world_dirs`（非空字符串列表）、`command_blocks`（i，必须为 0）、`scanned`（b true）、`placed_by_init`（b false）。`world_dirs` 必须覆盖 fixture `world.directory` 以及每个已声明的 `run.instances[].world_dir`，扫描无关目录不能代替已声明世界。

`cleanup-rebuild.json`：`steps`（非空字符串列表）、`source_world_untouched`（b true）、`rebuild_reproducible`（b true）。

### 2. `restore_fidelity` —— 恢复经过验证，而不只是 issued（bridge#6）

| 证据 | 内容 |
| --- | --- |
| `snapshot-before/` | 协议快照目录（`meta.json` + `entities.jsonl`），格式与 `tools/fork_verify.py` 读取的一致 |
| `snapshot-after/` | 受控恢复之后、继续推进 tick 之前拍下的快照 |
| `restore-record.json` | 端点/命令结果记录 |
| `source-unchanged.json` | 源存档恢复前后的 tree 哈希 |
| `failure-cases.jsonl` | 每个注入失败场景一行 |

门禁自行用 `fork_verify` 加载并校验两份快照，然后比较：`orderHash`、按类型计数、按 UUID 顺序的完整 `nbt` 字符串，以及双方都提供时的便利字段 `pos`/`vel`。两侧每个实体都必须有**非空 NBT 字符串**——null、缺失或空 NBT 一律失败（`restore_nbt_missing`），因此完整库存比较不可能变成空谈。双方都缺 `vel` 是允许的（它是 NBT 的便利副本，见 `docs/fork-verify.md` 规则 11）；只有一侧有该字段则失败。

`restore-record.json`：`endpoint.source` 与 `endpoint.target` 必须各自能按 `instance_id` 或唯一 `role` 解析到 `run.instances` 中声明的实例；source 必须是 `source_audit` 实例、target 必须是 `experiment` 实例，两者必须不同。`dimension` 必须等于 target 实例声明的 dimension。另外要求：`endpoint.target_resolved`（true）、`endpoint.wrong_target_rejected`（true）、`chunks_loaded`（true）、`tick_controlled`（true）、`duplicates_pre_existing`（= 0）、`commands_issued`（i >= 1）、`commands_failed`（= 0）、`partial_failure`（false）、`pause_state_preserved`（true）、`issued_is_not_success`（true —— 记录本身必须声明 issued != 已恢复）。

`source-unchanged.json`：`before_tree_sha256`（h64）、`after_tree_sha256`（h64，相等）、`unchanged`（true）、`hash_tool`（s）、`exclusions`（列表）。

`failure-cases.jsonl` 必须覆盖六个场景——`summon_refusal`、`duplicate_pre_existing`、`wrong_endpoint`、`inventory_mutation_order_hash`、`corrupt_metadata`、`partial_failure`——每行 `{case, injected, expected, observed, passed: true}`。

### 3. `player_context` —— 真实假人身份与服务端 vantage（#16）

`identity-records.jsonl` 必须覆盖 `task_bind`、`hit`、`miss`、`two_players`、`unknown_identity`。公共字段：`case`、`uuid`、`viewed_uuid`、`dimension`、`pos`（v3）、`yaw`、`pitch`、`task_entry`、`channel`（`mcp` 或 `cli` —— 实际使用的服务端 vantage 通路）、`accepted`（b）。`task_bind`、`hit`、`miss`、`two_players` 都必须看到任务 `uuid`（不得串人）；`two_players` 还需 `other_uuid`；`unknown_identity` 必须 `accepted: false` 且有非空 `rejected_reason`。

`entry-contract.json`：`mode`（`external_task` 或 `manual_external`）、`fields`（非空字符串列表）、`native_chat_verified`（b —— 允许 `false` 并如实记录）、`unsupported_entries`（字符串列表）。

`version-pins.json`：`interface_mod` 与 `bridge` 对象，含 `repo`、`commit`（h40）与 `tested: true`；commit 会与 `version_lock.mc-agent-interface-mod` 和 `version_lock.mc-agent-bridge` 交叉核对。

### 4. `agent_dev_capability` —— 在可定位实例上构建并安装（#17）

`tool-environment.json`：`harness`、`model`（s）、`docs_visible`（非空字符串列表），以及 `tools` 中 `terminal`、`file`、`filesystem_write`、`source_access`、`build`、`install`、`mcp_or_cli`、`lab_manage` 全为 `true`。

`jar-update.json`：`case: same_size_different_content`、`old_sha256`、`new_sha256`（h64，不同）、`bytes`（i，大小相同）、`loaded_sha256`（h64，必须等于 `new_sha256`）、`runtime_evidence`（s）。

`smoke-mod.json`：`mod_id`、`version`（s）、`built_sha256` 与 `deployed_sha256`（h64，相等）、`server_log_ref`、`sample_output_ref`（s）、`build_errors_detected`、`load_failure_detected`、`missing_dependency_detected`、`memory_state_rebuilt_after_restart`（true）、`restart_evidence_ref`（s）、`no_rom_logic`（true —— 这里的每个布尔都必须为 true）。

`instance-isolation.jsonl`：>= 2 行，含 `instance_id`、`role`（`source_audit`/`experiment`）、`world_dir`、`rcon_port`、`bridge_port`、`restarted`（true）、`resolves_correct_world`（true）、`conflicting_instance`（false）。每行都必须在 id/role/world/端口上与 `run.instances` 声明一致；每个声明实例都必须被覆盖；端口必须唯一且落在已声明/生效区间内。

### 5. `independent_test_mod` —— 可审计的机器输入与瞬态输出（#18）

`test-mod-manifest.json`：`mod_id`、`version`（s）、`sha256`（h64，与 `version_lock.test-mod` 交叉核对）、`read_only`（true）、`hook_overhead_ms`（n >= 0）、`fixture_behavior_unchanged`（true）、`loaded_in`（非空字符串列表，含 `source_audit` 与 `experiment`）、`agent_mod_coexists`（true）、`no_command_blocks`（true）。

`audit-events.jsonl` —— 规范的服务端事件 schema：

| 字段 | 类型 | 要求 |
| --- | --- | --- |
| `event_id` | s | 全文件唯一；重复即失败 |
| `run_id` | s | 父 `run.run_id`，或 `init`/`restore` 阶段所属的 `run.child_runs` id |
| `instance_id` | s | 在 `run.instances` 中声明；child run 则为其声明的实例 |
| `dimension` | s | 必须等于该 `instance_id` 声明的 `dimension` |
| `tick` | i | >= 0；`(tick, seq)` 按 `run_id/instance_id/dimension` 严格递增 |
| `seq` | i | >= 0；相同/碰撞的序号对失败，不能据此建立顺序 |
| `event` | s | 见下 |
| `phase` | s | `init`、`agent` 或 `restore`；`agent` 事件必须属于父 run |
| `actor_uuid` | s/null | `input_attempt`/`input_processed` 上必需：绑定的任务玩家 UUID，或 `null` 加非空 `actor_provenance` 说明缺失原因 |
| `cart_uuid` | s | `cart_emitted` 与 `cart_removed` 上必需 |
| `pos` | v3 | 视事件而定 |
| `captured_before_removal` | b | `cart_emitted` 上必须为 true |
| `removal_reason` | s | `cart_removed` 上必须存在（如 `void`） |

任何关联之前先强制来源校验：即使把改动后的字节正确刷新进证据索引，来自未声明 run、实例或维度的事件也会让门禁失败；非 null 的 `actor_uuid` 必须等于 fixture 假人 UUID（`audit_actor_mismatch`），键缺失失败（`audit_actor_missing`），`null` 且无 `actor_provenance` 失败（`audit_actor_provenance`）。必需事件：`input_attempt`、`input_processed`、`cart_emitted`、`cart_removed`。attempt -> processing -> emission -> removal 链按完整 `run_id/instance_id/dimension` 身份关联（移除另加 `cart_uuid`），`input_processed` 的 actor 必须与其 `input_attempt` 一致，每个 `cart_emitted` 都必须有同一身份上更早的处理输入，每个弹出的矿车都必须有对应的 `cart_removed`，每个弹出的矿车都在移除前被持久化，每次移除都带原因。`init`/`restore` 事件永远不算 Agent 操作。

`negative-cases.jsonl` 必须覆盖 `no_interaction`、`wrong_position`、`marker_only`、`answer_only`，每行含 `attempted`（b）、`processed`（= 0）与 `evidence_ref`（s）。明确归入负例的事件绝不能是 `input_processed`。

`audit-lifecycle.json`：`states`（非空字符串列表）包含 `ready`、`init`、`experiment_start`、`experiment_end`、`flush`；`missing_log_status` 与 `overflow_status` 均为 `error`；`per_instance_files`（true）；`ring_buffer_reliance`（false）。

### 6. `trace_persistence` —— 工具与游戏事件按 run/实例关联（#19）

`tool-trace.jsonl` —— 每次工具调用一行：`call_id`（唯一）、`run_id`（必须等于 `run.run_id`）、`instance_id`（必须在 `run.instances` 中声明）、`tool`、`args`（对象）、`result`（任意，必须存在）、`error`（字符串或 null，必须存在）、`started_at`/`ended_at`（t，有序）。trace 必须覆盖 `terminal`、`file`、`source`、`mcp` 四类。`run.tool_category_map` 可以按类别覆盖名称匹配，但它只会在默认类别之上合并（部分或空 map 不能删掉必需类别），空/全空白模式会失败。

`trace-join.json`：`joins[]` 含 `call_id`（必须存在于 trace）、`audit_ref`（`run_id`、`instance_id`、`dimension`、`event_id`、`tick`）与 `verified: true`。每个 `audit_ref` 都会在 `audit-events.jsonl` 中解析：事件必须恰好存在一次、在完整身份与 tick 上匹配，且 `phase: agent`。`unmatched_agent_events` 必须为 0，并等于没有 join 的 agent 阶段审计事件计算值（每个 agent 侧游戏事件都要有工具调用）；`unmatched_tool_calls` 必须等于没有 join 的工具调用计算值（trace 可以包含构建、写文件等非游戏调用）。

`missing-log-detection.jsonl`：场景 `trace_missing` 与 `audit_missing`，每行 `{case, detected: true, exit_nonzero: true, message_ref}`。缺日志必须明确报错，绝不能被静默忽略。

### 7. `smoke_fixture_validity` —— 通用 smoke、校准与锁定版本（#14）

`smoke-report.json`：`suites[]` 覆盖 `smoke_offline`、`lab_boot`、`fake_player_mcp`、`snapshot_restore`、`test_mod_load`，每项 `{name, command, status: "pass", checks >= 1, log_ref}`。

`fixture-validity.json`：`input_semantics`（s）、`stack_positions`（非空 `[x, y, z]` 列表）、`output_boundary`（非空对象）、`void_window_ticks`（i >= 1）、`end_condition`（s）、`timeout_s`（i >= 1）、`hook_overhead_ms`（n >= 0）、`with_mod_without_mod_consistent`（true）。

`version-lock.json`：`components[]` 必须包含以下名称，每项带其必需固定值（`version` 字符串在它本身就是固定值时必需，否则可选）：

| 组件 | 必需固定 | 交叉核对对象 |
| --- | --- | --- |
| `mc-agent` | `commit`（h40） | - |
| `mc-agent-interface-mod` | `commit`（h40） | `version-pins.json` |
| `mc-agent-bridge` | `commit`（h40） | `version-pins.json` |
| `minecraft` | `version`（s） | `fixture-manifest.map.mc_version` |
| `fabric-loader` | `version`（s） | - |
| `jdk` | `version`（s） | - |
| `carpet` | `sha256`（h64） | `fixture-manifest.mods[carpet]` |
| `test-mod` | `sha256`（h64） | `test-mod-manifest.sha256` |
| `fixture-map` | `sha256`（h64） | `fixture-manifest.map.sha256` |

固定值与其指名的证据不一致即失败（`version_lock_conflict`）。

`evidence-index.json`：`tool`（s）与 `entries[]`，每项对文件为 `{path, sha256, bytes}`、对目录为 `{path, tree_sha256, files, bytes}`。它必须固定**除自身以外的所有已声明证据**；哈希不匹配、重复、文件缺失或漏固定都会让门禁失败。额外的原始证据也可以列入。

### 8. `evidence_integrity` —— 包结构、哈希、端口与源存档（工具自算）

无证据文件。门禁自行校验 schema 版本与 kind、`origin`、`run.issue`、`run.child_runs`、运行实例（id/端口唯一、两种角色齐全、每个实例声明 `dimension`）、已声明证据类型与路径、证据索引与重算哈希、审计事件的 run/instance/dimension 来源、以及 `run.source_world` 前后相等。

端口区间是显式配置而非产品硬编码要求：必须声明 `run.allowed_port_ranges`（缺失即失败），每个实例与 `instance-isolation` 端口都必须落在其中，`--port-range` 只能收窄（超出已声明区间会失败 `port_range_conflict`）。

带 `--source-world`（或清单中的路径）时，门禁只读重算源存档哈希并比较。`--skip-source-rehash` 跳过这项独立检查，因此整个运行会变为 **blocked** —— 不存在未重算却报 `pass` 的退出码。

## 目录树哈希

`python tools/stage1_gate.py hash-tree <dir>` 输出确定性摘要：按相对 POSIX 路径排序，每项含大小与单文件 SHA-256；排除 `session.lock`、`logs`、`*.log`、`.DS_Store`、`Thumbs.db`、`__pycache__`。门禁与 `version_lock`/`fixture-manifest` 的世界哈希使用同一算法与排除列表；不要混用不同哈希工具。

## 集成运行手册（前置项合并之后）

父级当前状态、阶段一/阶段二交接命令与循环依赖防护见
[Minecart ROM 验收 runbook](minecart-rom-runbook.md)。

1. 从 #15、bridge#6、#16-#19 收集原始证据到一个包里（`scaffold` 会打印规范结构）。保留原始日志，并一并列入索引。
2. 填写 `bundle.json`：`origin: live`、run id、child run id、源存档路径与前后 tree 哈希、声明的端口区间，以及落在这些端口上的实例。
3. 对所有文件与目录树生成 `evidence-index.json`。
4. 带 `--source-world` 与 `--report` 运行 `check`；把报告附到 issue #14。任何 `blocked` 或 `fail` 都阻止进入阶段二。
5. 协调者审阅报告并抽查原始证据后才宣布门禁通过。门禁不能替代这次审阅。

## 限制

* 门禁校验协调者提供的证据，无法证明证据没有被伪造。原始日志应留在仓库/附件链路中并接受抽查。
* "hook 只读"之类的布尔事实与规范事件日志交叉核对，但本工具不会实机重测。
* 各前置项的原始格式必须无损映射到上述 schema；该映射属于协调者审阅范围。
* 门禁通过是阶段二的必要条件而非充分条件：issue 的其它验收点（人工审阅、正确答案证据）仍然必须满足。
* 快照 `meta.json` 无法证明它来自哪个实机实例（`instance` 只是 `server`/`client`，`worldDir` 可为 null），因此前后快照对只证明状态相等，不证明端点身份；端点证据由绑定的 `restore-record.json` 加审计/轨迹来源提供。

另见：[实验室服务器](lab-server.md)、[分叉校验](fork-verify.md)、[快照协议](protocol-snapshot.md)、[工具](tools.md)。
