# Toolkit 工具与命令

[English](https://guajun.github.io/mc-agent/tools/)

Minecraft Agent Toolkit 由 `mc-agent` Go 二进制实现。实时的 **capabilities**
结果才是权威：命令面会按连接的 mod 与视角过滤。没有 MCP 服务器。

## CLI

全局选项：`--home DIR`、`--target NAME`、`--pretty`。

| 命令 | 用途 |
| --- | --- |
| `mc-agent version` | 产品版本、控制协议与 mod 最低版本 |
| `mc-agent doctor` | 版本、状态目录、目标、daemon、TLS 与能力检查 |
| `mc-agent daemon start` / `run` / `stop` / `status` / `doctor` | daemon 生命周期 |
| `mc-agent target add/list/show/remove/use/reload` | 目标与凭证管理 |
| `mc-agent capabilities`、`mc-agent schema [op]`、`mc-agent call <op> [--request-id ID]` | 发现并调用操作；`--request-id` 仅存在于 `call` |
| `mc-agent status` | daemon 与每个目标的连接状态 |
| `mc-agent state` | 权威世界/tick 状态与在线玩家 |
| `mc-agent player <name\|uuid>` | 按 UUID（或名字）解析玩家并返回实时上下文/视线 |
| `mc-agent context <id>` | 按 `context_id` 取回受限聊天时刻 bundle |
| `mc-agent entities [--dimension D]` | 服务端非玩家实体 NBT 和 tick 顺序（需 `entities:nbt`） |
| `mc-agent command "<line>"` | 执行命令（写；返回 `writeSeq`） |
| `mc-agent command-output "<line>" [--wait S]` | 执行命令并读取回答 |
| `mc-agent events [--stream-id ID] [--since N] [--limit N] [--category C] [--follow]` | 按 (streamId, seq) 游标回放或流式读取事件 |
| `mc-agent requests`、`mc-agent request-status <id>` | 未知写账本与查询 |
| `mc-agent save`、`mc-agent snapshots` | 存档元数据与快照列表 |
| `mc-agent snapshot [--name N] [--dimension D]` | 在游戏主机写入实体顺序快照 |
| `mc-agent mark <text>`、`mc-agent wait <ticks>` | 标注事件流；等待所连接视角的 tick 回调 |
| `mc-agent exclusive-* <key>` | 跨 daemon 租约 |

客户端专用的遗留命令（`chat`、`screen`、`connect`、`world`、`lan`、
`record-start`、`record-stop`）只在显式客户端视角且 mod 声明支持时出现。

智能体应当：

1. 用 `version` 和 `doctor` 了解环境；
2. 用 `capabilities` / `schema` 并只调用支持的操作；
3. 需要玩家上下文时按 UUID 解析相关玩家；
4. 只取需要的实时世界数据；事件带 `context_id` 时及时调用 `context`；
5. 把身份当上下文而非命令授权；
6. 未知写先查 `request-status`，不要重放。

退出码与错误码稳定，见
[操作参考](https://github.com/guajun/mc-agent/blob/main/skills/minecraft-toolkit/references/toolkit-operations.md)。

## 连接路径与视角

| 游戏会话 | target 的传输与端点 | 视角和范围 |
| --- | --- | --- |
| 专用服 | `remote`，实际游戏主机/端口，提供 TLS pin 或 CA 及有效凭据 | 服务端；权威状态及声明支持的服务端操作 |
| 已发布 LAN 的整合服 | `remote`，该世界实际发布的 LAN 游戏端口，提供其 TLS pin 或 CA 及凭据 | 服务端；与专用服相同的控制路径 |
| 未发布 LAN 的单人世界 | 显式 `legacy`，本地 server adapter 的端口文件/地址 | `--vantage server`；本地兼容路径，不证明 LAN remote 已连接 |
| 客户端端点 | 显式 `legacy`，本地 client adapter 的端口文件/地址 | `--vantage client`；声明支持的 screen/world/LAN 等客户端操作 |

即使地址是 loopback，remote 也表示 TLS 控制传输。legacy adapter 端口（常见
25580/25581）不是 LAN 游戏端口，不能用 remote target 连它们。端口可能变化：
legacy 读对应端口文件，LAN 使用该世界实际发布的游戏端口。remote 失败不会自动
回退 legacy。配置见[连接检查](getting-started.md#3-check-the-connection)。

连接或操作失败时，用 `target show`、`status`、`doctor`、`capabilities` 和 `state`
检查当前所选 target：

- **没有发布 LAN 端点：** remote 前先确认目标世界已经打开并发布 LAN；否则显式
  选择本地兼容路径。控制文件存在不证明游戏端口已监听。重开/重新发布后核对当前端口。
- **端点或身份错误：** 核对主机、实际游戏端口、transport，以及目标 serverDir 的
  fingerprint/凭据。直接排查 TLS 或认证错误，不关闭验证，也不改连 legacy 让验收
  看似成功。凭据通过 stdin、环境或文件传入，不打印到日志。
- **视角错误：** 服务端 remote 不提供客户端 `screen`、`world`、`lan`；客户端
  legacy 也不是权威服务端 NBT/order。检查返回的 `instance` 和支持能力，不能只看
  socket 是否连通。

## 时钟与状态读回

服务端视角的 `state.tick` 和实体采集的 `tick` 是服务器 tick 计数，不是维度的
gametime。`wait N` 按服务器 tick 回调计数；`tick freeze` 下回调和服务器计数仍
前进，而维度 gametime 冻结。因此 wait 成功不能证明世界已经推进。遗留客户端 tick
又是另一种时钟。`command-output --wait S` 是按秒计的输出收集超时，不是刻等待。

适用于获准操作的冻结世界测量的有限参考流程：

1. 设置状态后，读回所选维度中的目标 UUID、相关 NBT、位置和尺寸。命令 ack 或反馈
   不证明游戏执行成功；分别检查 CLI 退出码、错误与未知写，无法确认必要状态时停止。
2. 在同一维度读 `time query gametime`，将其和实际初始状态一起记录为 `t0`。
   例如主世界用 `execute in minecraft:overworld run time query gametime`。保留原始
   回复，无法解析时失败；文本反馈不是结构化 gametime API。
3. 发出 `tick step 1`，在有界超时内轮询同一 gametime，确认到达 `t0 + 1` 后，调用
   `entities --dimension minecraft:overworld`，按 UUID 选目标而非数组第一项。
   记录样本在 step 后，分别保留 gametime 和 server tick。这些调用不是原子采样；
   若 gametime 意外推进或无法确认冻结假设，不接受该样本。

按任务需要重复即可；清理可能产生延迟后代，适用时等待其稳定并再次核验实体群体。
这只是使用案例，不是强制实验模板。注入状态不能算真实玩家交互（如剪毛）通过。

历史 [#45](https://github.com/guajun/mc-agent/issues/45) runner 的 `series[].tick`
表示 gametime，与 Toolkit 的 `tick` 不同。离线复算 77 个试验的 2,010 条样本，全部
满足 `gametime - t0 = step`。遍历完整 series 并匹配 `meta.cubeUUID`：
`order_noai_d0_cube_first_r1` 首次观察到 BODY 是 step 2（4430 - 4428）；
`misc_dispenser_body` 的 `pre_body: true` 表示 step 1 仍持有预装物品，不是新装填。
没有确认初态为空时，首次观察到持有也不能证明新变化。目标/字段缺失、脚本失败与
完整采样窗口内未观察到事件必须区分，均不能单独证明事件永不发生。这是历史数据
离线核对，不是当前版本真实游戏验收。

保留的 `summary.json` 另有七项与 UUID 完整 series 不一致：`misc_multi3_r1/r2/r3`
和 `misc_prime` 的摘要为 null，但 step 1 已观察到持有；早期的
`summon_d0_cube_first`、`summon_d0_item_first` 和 `summon_d10_cube_first` 摘要报有值，
但 series 未匹配到所选 UUID。保留历史文件并报告差异，不能覆盖原始数据，也不能
把摘要当成权威观测。

## 边界

- **远程路径就是远程。** 服务器世界目录绝不是 daemon 本机路径；`snapshot` 在游戏
  主机上写文件。
- **快照是实体顺序记录，不是内存检查点。** 本 CLI 不承诺完整冻结、fork 与重接入。
  `fork`/`restore` 是遗留本地 Python 工具，对远程目标会被拒绝。
- **身体、假玩家与区块加载是外部能力。** 使用 Carpet Skill 或服务器自己的 Skill；
  Toolkit 只暴露可观察的基本操作。
- **不可用操作会被如实报告，绝不伪造。** capability 回复包含 `unsupported` 条目、
  原因，以及已知时的上游依赖。

## 事件转发

daemon 可以把选定事件转发到一个 webhook 接收端。投递使用 HMAC-SHA256 签名、带稳定
事件 id 重试，且绝不把模型回复写回 Minecraft。在 `daemon run`（不是
`daemon start`）或通过 `MC_AGENT_WEBHOOK_*` 配置；见
[Hermes 与无人值守](hermes-setup.md)。

## 仓库工具

这些工具服务于实验与文档，是开发工具，不是产品运行依赖。

| 工具 | 用途 |
| --- | --- |
| `lab_server.py` | 创建并控制无头 Fabric lab |
| `launch_instance.py` | 不借助图形启动器启动客户端 |
| `game_cmd.py` | 执行游戏命令并打印反馈 |
| `fake_player.py` | 创建并驱动 Carpet 假玩家 |
| `fork_verify.py` | 检查、恢复和比较录制的 fork（遗留本地路径） |
| `webhook_receiver.py` | 用于 webhook 测试的本地 HMAC 校验接收端 |
| `smoke_offline.py` | 遗留 Python daemon/agent-loop 路径的开发者冒烟测试 |

### 用独立数据目录启动客户端

`launch_instance.py` 从 `--minecraft-dir` 读取已安装的版本、库与资源。
指定 `--game-dir`，把实验的客户端数据、默认启动日志和 mc-agent 服务端状态
放在独立目录：

```powershell
python tools/launch_instance.py --minecraft-dir "D:/MC/.minecraft" `
  --version 26.2-Fabric --game-dir "F:/research/client-game" `
  --jvm-property mcagent.control=true --world "research world"
```

启动前在该 gameDir 中准备实验自己的 mods、配置和存档；工具不会复制用户实例或世界。
工具先按调用者的 cwd 把 gameDir 解析成绝对路径，再启动 Java。
如果 `--jvm-property`、`--jvm-arg` 或版本的 JVM 参数中没有指定 serverDir，
就补上 `-Dmcagent.serverDir=<绝对 gameDir>/mc-agent-server`。
原有的 `--game-arg=--gameDir=...`（以及分成两个参数的形式）也会这样处理；
互相冲突的游戏目录会被拒绝。

启动前会打印进程 cwd、客户端 gameDir、服务端 serverDir 和 controlDir。
先核对这些路径，再在启动后确认控制文件、快照实际出现在打印的服务端目录下。
既有文件会保留。Java 的 cwd 仍是已安装的版本目录，因此显式指定的相对 serverDir
仍相对此 cwd 解析。不覆盖游戏目录时保留原默认行为：客户端数据和 cwd 使用版本目录，
服务端状态使用其 `mc-agent-server` 子目录。专用服也仍保留 Mod 的默认值
`mc-agent-server`，相对于专用服进程 cwd。这些设置指定文件落点，不是 Minecraft
或其他 Mod 的文件系统沙箱。

## 遗留 agent-loop

`mc-agent-loop` 不属于 Toolkit 契约。它仍是可选的兼容监听器，带 `echo` 和临时
`hermes` 后端，并依赖遗留 Python bridge。Codex、Claude Code 等用户主动型 Harness
直接运行 CLI，不应被描述成 loop 后端。其默认聊天触发词是 `@agent`。

## 当前实体 NBT 契约（Mod 0.9.0）

`entities` 需 `entities:nbt`，`snapshot` 需 `snapshot:entity-nbt`，仅服务端提供。
旧 Mod 或未声明能力的连接会拒绝这些新操作；不会把旧客户端投影视为 NBT。
可选 `dimension` 默认 `minecraft:overworld`，不隐含选择玩家，所有 `radius` 参数或标志均拒绝。

在线结果含 `type: entities`、`schema: entity-nbt/1`、`instance`、`tick`、`dimension`、
`playersSkipped`、`orderHash` 和 `entities` 数组。每条记录和快照 JSONL 行含 `order`、
`uuid`、`type`、`pos`、`vel`、`nbt`（SNBT）、`passengers`（UUID 数组）、
`vehicle`（UUID 或 null）、`restorable`。玩家及已移除/死亡项不进入记录；范围是存活的实体
 tick 列表，并非世界中所有已存储或已加载实体。删除 `entityId`、`yaw`、`pitch` 投影；
`vel` 为现有保真比较工具保留，完整持久化状态由 NBT 承载。

按记录顺序逐个 summon `restorable: true` 的根实体，使用其类型、位置和 NBT；乘客随载具
嵌套恢复。顺序 hash 包含所有记录 UUID，包括乘客，单独一致不证明保真。
`snapshot` 在游戏主机写同一实体记录和元数据，然后返回含 schema、id、dir、数量、hash、
tick、维度、字节数和覆盖状态的确认；它不是世界文件复制。

在线 NBT 受协商帧大小约束（默认 8 MiB），没有分页，大结果可能失败。这些采集不主动卸载区块
或保存世界，也不单独承诺完整无侵入分叉。普通筛选用游戏命令或本地分析；旧客户端采样仍是
独立的同步投影。
