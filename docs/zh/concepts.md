# 架构与术语

[English](https://guajun.github.io/mc-agent/concepts/)

## 调用链

受支持架构只有一条权威游戏路径：

```text
Harness -> mc-agent CLI -> mc-agent daemon -> 控制传输 -> 服务端视角 Fabric mod -> 服务器
```

daemon 是单个 Go 二进制，可部署在不同位置：

- **客户端侧 / Harness 环境**（默认）：daemon 随 Harness 运行，连接服务器的游戏
  端口。对只装 mod 的专用服务器或 LAN 世界来说这就够了。
- **服务器侧**（可选）：同一个二进制跑在游戏旁，供本地 Harness 使用。它不是其他
  daemon 必须经过的网关，也从不被强制要求。
- **多个 daemon** 可用各自凭证和事件流独立连接；玩家退出不会中断专用服务器的控制
  连接。

Toolkit 是本地基础设施，不是智能体运行时。它暴露事实与基本操作，但不选择模型、
不维护对话、不管理 Harness 会话、不判断动作是否允许，也不管理身体、假玩家或区块
加载。

## 两种调用模式

| 模式 | 谁来发起回合 | 如何取得上下文 |
| --- | --- | --- |
| **用户主动** | 用户让 Codex、Claude Code 或其他 Harness 做事 | Harness 按需调用 `player`、`state`、`entities` 等操作 |
| **无人值守** | 用户配置的接收端（如 Hermes 路由）收到签名事件 webhook | 事件的 `context_id` 取回发送者受限的聊天时刻上下文；Harness 随后再做新的调用 |

游戏外的请求没有聊天时刻。Harness 通过稳定玩家身份解析调用者并请求当前上下文。
游戏内聊天事件可以携带服务器收到消息时捕获的上下文，早于智能体开始工作。两种机制
互补。

玩家身份是上下文，不是授权。名字或 UUID 不能证明调用者可以做特权操作；权限来自
控制凭证，策略属于 Harness 与其运营者。

## 术语

| 术语 | 含义 |
| --- | --- |
| **Agent** | 决定做什么的推理过程 |
| **Harness** | 运行智能体、提供工具并拥有会话的宿主应用，如 Codex、Claude Code、Hermes |
| **Minecraft Agent Toolkit** | Fabric mod + `mc-agent` Go CLI/daemon + 可移植 Skill |
| **CLI** | `mc-agent` 命令入口；有限的数据调用返回 JSON，文本与流式输出例外见[入门](getting-started.md) |
| **daemon** | 长驻进程，拥有游戏连接、回放缓冲、未知写账本和可选 webhook |
| **控制传输** | 游戏实际端口上的认证 TLS 连接（控制协议 1） |
| **遗留适配器** | 0.8 之前的明文 loopback JSON-lines 传输，用于旧 mod、显式客户端视角和单机本地路径 |
| **Fabric mod** | 在 Minecraft 内运行、暴露服务端已知状态的 `mc-agent-interface-mod` |
| **服务端视角** | 专用或集成服务器中的权威端点 |
| **客户端视角** | 绑定单个客户端的旧式可选端点，保留屏幕/客户端操作 |
| **Skill** | 教 Harness 使用 Toolkit 的可移植说明；不实现传输、重试或会话 |
| **agent-loop** | 依赖 Python bridge 的可选遗留兼容监听器，不属于产品契约 |
| **context bundle** | 由不透明 `context_id` 取回的、聊天发送者服务端已知身份/朝向/视线的受限短期快照 |
| **webhook** | 可选的出站签名事件投递，接收端由用户配置 |

## 职责边界

| 关注点 | 归属 |
| --- | --- |
| 只有游戏进程知道的事实；逐 tick 捕获 | Fabric mod |
| 连接所有权、重连、事件回放、未知写账本、传输适配 | daemon |
| 决定取哪些事实、如何解读、做什么动作 | Harness 中的智能体 |
| 模型提供方、对话状态、审批、webhook 路由与回复投递 | Harness/运营者 |
| Harness 之间共享的操作指南 | 可移植 Skill |
| 身体、假玩家、区块加载 | Carpet Skill 或服务器自己的 Skill |

只有游戏进程能知道的新能力属于 mod；组合已有基本操作或适配传输的属于 daemon；
需要判断的属于智能体。

## 事件、重连与上下文

daemon 缓冲事件并提供游标回放。游标是 `(streamId, seq)` 对：
`events --stream-id <id> --since <seq>` 在游标属于另一次 daemon 运行时返回
`reset: true`，`dropped` 表示缓冲区淘汰，`truncated` 仅表示还有分页（不是丢失）。
重连会明确报告而不是隐藏：`bridge_connected` /
`bridge_disconnected`、缓冲无法到达游标时的 `event_gap`、run id 变化时的
`game_restarted`（不跨重启回放），以及未知写变为已知时的 `request_resolved`。
非幂等写不会自动重放；账本持久化它们，`request-status` 报告结果。

可选 webhook 用 HMAC-SHA256 签名把选定事件发给一个 HTTP(S) 接收端，保持接收端中立、
内存内、尽力投递，且不调用任何模型。

服务器聊天事件可带 `context_id`。mod 在受限时间和数量内保存 bundle；未知或过期 ID
返回结构化结果，绝不替换成其他玩家的数据。`timing` 区分网络聊天包（`receipt`）与
服务端广播（如 Carpet 假玩家的 say，`broadcast`）；广播 bundle 不是包到达时刻的历史。

## 世界分叉与实验边界 { #world-forks-and-experiment-boundaries }

首阶段针对 **Minecraft 26.2**：方便地把世界分支搬到 Harness 机器，让智能体自由
使用本地文件、分析程序、JDK 和 Mod。保留运行态服务于“实验条件足够”，不承诺
任意 JVM 或任意 Mod 状态的检查点恢复。当前正式 Go 产品提供实体 NBT／tick 顺序
和命令能力，尚未提供完整世界 `fork`／`restore`；实体快照本身不是世界分支。

保护源服仍是首要约束：不能为了制造副本要求源服区块卸载／重载。现阶段接受
`save-all` 的影响作为能力边界。保存不是只读操作，可能执行保存维护，包括处理
待处理的区块／实体任务。`mc-agent save` 只读保存元数据，执行 `save-all` 要通过
游戏命令；这些现有调用并未实现 tick 边界上的原子世界采集。

对依赖自动保存或暂停／保存行为的实验，可选择允许保存的更早干净起点，在那里
分叉，再在分支中推进到待研究状态。解读修改后的实验前，验证必要条件和未改动的
基线。这种方法可避免保留敏感的中间状态，但是否有效取决于具体实验，并不让所有
不支持的状态都变得可复现。应说明支持的状态和已知缺口，不承诺精确续演。

已知缺口包括移动活塞的保存／加载状态及待执行方块事件。静止基线和实体生命周期
方法的适用条件见[已知实验限制](known-limitations.md)。

## 诚实的边界

- **远程路径就是远程。** 服务器 `worldDir` 绝不会被解释为 daemon 本机路径；
  `snapshot` 在游戏主机上写文件。
- **快照是实体顺序记录，不是内存检查点。** 完整冻结、fork 与重接入保证属于独立设计
  讨论，这里不做承诺。
- **代理兼容性。** 边界是字节透明的 TCP 转发；能理解 Minecraft 或终止 TLS 的代理
  未经测试、不做承诺。
- **游戏必须运行。** 游戏停止后 mod 接口停止；daemon 等待并重连。启动已停止的远程
  游戏进程不在范围内。

## 历史路径

客户端视角端点和 `mc-agent-loop` 仍支持旧工作流与离线测试，Python bridge 仍是本地
`fork`/`restore` 工具的显式遗留路径。仅在确实需要遗留能力时使用。
[RFC 0001](rfc/0001-agent-interface.md) 记录客户端优先、MCP 时期的早期架构，仅作为
历史保留，不描述当前产品。
