# 架构与术语

[English](https://guajun.github.io/mc-agent/concepts/)

## 调用链

当前支持的架构只有一条权威游戏访问路径：

```text
Harness -> Minecraft Agent Toolkit daemon -> 服务端视角 Fabric mod -> 服务端
```

Toolkit、Harness 与 mod endpoint 在同一台机器上。独立服务器场景就是服务器
机器；单机场景就是玩家电脑，因为集成服务端运行在客户端进程内。

Toolkit 是本地基础设施，不是 Agent 运行时。它暴露事实和原语，但不选择模型、
维护对话，也不判断某项操作是否获准。

## 两种启动方式

| 模式 | 谁发起一轮任务 | 如何获得上下文 |
| --- | --- | --- |
| **用户主动** | 用户要求 Codex、Claude Code 或其它 Harness 做某件事 | Harness 按需调用 `player`、`state`、`entities` 或其它 Toolkit 操作 |
| **无人值守** | Hermes 等单独配置的接收方收到签名事件 webhook | 用事件的 `context_id` 读取发送者的有界聊天时刻上下文，再按需发起新的 Toolkit 调用 |

游戏外请求没有被捕获的聊天时刻。Harness 通过稳定玩家身份解析调用者，并查询
当前上下文。游戏内聊天事件则可以携带服务端收到消息时捕获的上下文，早于
Agent 开始执行。两种机制互补。

玩家身份只是上下文，不是授权。名字或 UUID 不能证明调用者有权执行特权命令；
策略属于 Harness 及其操作者。

## 术语表

| 术语 | 含义 |
| --- | --- |
| **Agent** | 决定做什么的推理过程 |
| **Harness** | 运行 Agent、提供工具并持有会话的宿主应用，例如 Codex、Claude Code、Hermes |
| **Minecraft Agent Toolkit** | `mc-agent-bridge` 包及其完整本地 Minecraft 能力面 |
| **Toolkit daemon** | `mc-bridge run` 启动的常驻进程；Bridge daemon 是旧称，不是另一层 |
| **Fabric mod** | `mc-agent-interface-mod`，运行在 Minecraft 内，通过 loopback 暴露服务端已知状态 |
| **服务端视角** | 独立或集成服务端内的权威 endpoint；Toolkit 默认目标 |
| **客户端视角** | 绑定单个客户端的旧式显式 opt-in endpoint；保留给屏幕/客户端操作和兼容用途 |
| **Skill** | 教 Harness 如何操作 Toolkit 的便携说明；不实现传输、重试或会话 |
| **agent-loop** | 可选兼容监听器，不属于 Toolkit 契约，用户主动型 Harness 不需要它 |
| **上下文包** | 聊天发送者在消息时刻的服务端身份、位置和视角的有界短期快照，通过不透明 `context_id` 读取 |
| **webhook** | 可选的出站签名事件投递；接收方由用户配置 |

原 Bridge 项目已经成为 Toolkit；仓库和 CLI 为兼容仍保留
`mc-agent-bridge` / `mc-bridge` 名称。因此 Bridge daemon 就是 Toolkit daemon，
不是 Toolkit 背后的另一个组件。Codex、Claude Code 和 Hermes 是 Harness，
不是 Toolkit backend。

## 职责边界

| 关注点 | 负责方 |
| --- | --- |
| 只有游戏进程知道的事实、逐 tick 捕获 | Fabric mod |
| 连接持有、事件重放、原语组合与传输适配 | Toolkit daemon |
| 决定读取哪些事实、解释事实和选择动作 | Harness 中的 Agent |
| 模型提供方、对话状态、审批、webhook 路由与回复目标 | Harness/操作者 |
| 跨 Harness 共享的操作说明 | 便携 Skill |

只有游戏进程能知道的新能力属于 mod；组合已有原语或适配传输的能力属于
Toolkit；需要判断的能力属于 Agent。

## 事件与上下文

Toolkit daemon 缓冲事件并提供基于游标的重放。可选的 `forward` 命令使用
HMAC-SHA256 签名，把选定事件发给一个 HTTP(S) 接收方。它与接收方无关，也
不进行模型调用。

服务端聊天事件可以包含 `context_id`。mod 只在有限容量和有限时间内保存
上下文包；未知或过期 ID 返回结构化结果，绝不会换成另一位玩家的数据。Agent
应尽早读取上下文包，之后再按需查询新的世界状态。

webhook 发送端和便携 [Toolkit Skill](toolkit-skill.md) 已经发布。
[Hermes 无人值守指南](hermes-unattended.md)记录 route、受限工具和回复投递。
签名端到端投递仍受请求头与 delivery-ID 互操作 follow-up 阻塞，见
[mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7)。

## 历史路径

客户端视角 endpoint 与 `mc-agent-loop` 仍支持旧工作流和离线测试。只有确实
需要屏幕状态等客户端能力时才应选用；它们不是默认架构，也没有任何 Harness
名称是默认游戏聊天触发词。兼容 loop 当前默认使用 `@agent`。

[RFC 0001](rfc/0001-agent-interface.md) 记录较早的客户端优先架构。
[方案 #8](https://github.com/guajun/mc-agent/issues/8) 已用本文描述的服务端视角
Toolkit 取代其部署模型。
