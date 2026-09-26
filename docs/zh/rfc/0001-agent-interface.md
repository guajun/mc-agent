# RFC 0001：智能体接口与 bridge 架构

!!! warning "历史架构"
    本 RFC 记录客户端优先的 Phase 1 设计。当前部署模型是
    [架构与术语](../concepts.md)中描述的服务端视角 Minecraft Agent Toolkit，
    由[方案 #8](https://github.com/guajun/mc-agent/issues/8)确立。agent loop
    现在只是可选兼容基础设施；Codex 与 Claude Code 是用户主动型 Harness，
    不是项目维护的 backend。

* 状态：已实现——Phase 1 已发出，讨论关闭。仍未定论的问题已拆成 issue：[#4](https://github.com/guajun/mc-agent/issues/4)（背压）、[#5](https://github.com/guajun/mc-agent/issues/5)（更丰富的游戏事件）、[#6](https://github.com/guajun/mc-agent/issues/6)（多个客户端）、[#7](https://github.com/guajun/mc-agent/issues/7)（游戏向智能体推送）
* 范围：Minecraft 客户端与智能体运行时之间的通用管道
* 不在范围：任何与某个具体实验或用例相关的东西
* 模块：[接口 mod](https://github.com/guajun/mc-agent-interface-mod) ·
  [bridge](https://github.com/guajun/mc-agent-bridge) ·
  [agent loop](https://github.com/guajun/mc-agent-loop)

## 背景

一个能在 Minecraft 客户端里观察并行动的智能体需要三件事，而这三件事很容易被混为一谈：

1. 读取游戏状态、并请游戏做点什么的方式；
2. 知道游戏里"什么时候发生了什么"的方式；
3. 让模型把 (1) 和 (2) 变成决定的方式。

把它们混在一起，就会得到那套常见的混乱：一个知道当前实验的 mod、一个只服务于某个智能体的 bridge、以及一个只要有人碰游戏就得重启的 loop。

## 目标

* 一个通用的客户端接口，跨游戏版本稳定、任何客户端都能用。
* 游戏连接**只有一个持有者**，这样智能体重启时不会打扰游戏。
* 事件历史在智能体重启后仍然存在，于是智能体可以问"我错过了什么"，而不必恰好活在那个时刻。
* 智能体运行时可以互换——包括那些**现在还不存在**的运行时。
* 核心里零用例逻辑。

## 非目标

* 决定要跑什么实验。
* 强制的常驻监管进程。智能体可以启动游戏、触发某件事、等待、然后停下——它不需要毫秒级反应。
* 本阶段的**服务端（Paper/Spigot）**支持。
* 和 Minecraft 自己的抽象较劲——接口只暴露客户端已经知道的东西。

## 参与者

| 参与者 | 职责 | 不做 |
| --- | --- | --- |
| 接口 mod | 通过本地 socket 暴露状态/动作/事件 | 解释它们 |
| bridge 守护进程 | 持有 mod 连接、缓冲事件、再通过 loopback + MCP 重新提供服务 | 思考 |
| agent loop | 决定什么时候该让后端思考、把回复送回去 | 了解游戏内部 |
| Harness | 运行 Agent 并持有会话（例如 Hermes 或 Codex） | 接触 mod 线协议 |

## 层边界

* **mod <-> bridge**：按行分隔的文本，一行一个 JSON 对象，请求与回复外加主动推送的事件。简单到可以用 `telnet` 调试，稳定到可以定版本。
* **bridge <-> 其它一切**：loopback 上按换行分隔的 JSON，带请求 id、事件订阅和事件游标。与语言无关。
* **bridge <-> MCP 客户端**：可选的前端，每个原语一个工具。

bridge 之上没有任何东西知道 mod 的线格式，mod 也不知道智能体的存在。

## 原语（协议 v1）

`state`、`entities`、`command`、`chat`、`record_start`、`record_stop`、`wait`、`screen`、`mark`、`connect`、`world`、`ping`、`capabilities`。

`connect` 和 `world` 是两个"把我放到某处"的原语：一个服务器，或一个单机存档。它们存在，是因为智能体应该能自己开始一段会话，也因为**客户端是唯一能判断自己何时足够空闲**的那一方。

刻意缺失：寻路、背包操作、放置方块、物理断言。这些是"用例形状"的东西；它们属于某个命令之后，或属于未来某篇有具体需求的 RFC。

实际发出的样子（mod 0.5.x）：客户端视角还提供 `lan`，服务端视角额外提供 `snapshot`。以 mod 在 `capabilities` 里报告的列表为准，而不是本文档。

## 事件模型

mod 推送 `hello`、`chat`、`game`、`mark`、`sample_start`、`sample_progress`、`sample_done` 以及诊断信息；bridge 分配单调递增的序号、维护一个环形缓冲，并允许客户端从游标重放（`events {since}` → `{events, next, dropped}`）。

开放问题——留在这里，作为 Phase 1 未定事项的记录；每一条现在都有对应的 issue：

* **更丰富的游戏事件。** [#5](https://github.com/guajun/mc-agent/issues/5)
* **服务端侧的唤醒。** [#7](https://github.com/guajun/mc-agent/issues/7)
* **一个服务端适配器。** 部分被 mod 的服务端视角（0.5.0，端口 25581）取代：剩下的是按 tick 率流式输出和更丰富的事件，见 [#5](https://github.com/guajun/mc-agent/issues/5)。客户端视图是插值的，所以它不是测量仪器；见 [智能体在游戏里是谁](../player-identity.md)。
* **背压。** [#4](https://github.com/guajun/mc-agent/issues/4)
* **多个客户端。** [#6](https://github.com/guajun/mc-agent/issues/6)

## MCP 还是 loopback API？

两个都要，但不对等。

MCP 是**拉取**式接口：由客户端拉起 server，所以游戏永远无法通过它唤醒智能体。这让 MCP 很适合"模型问世界的情况"，很不适合"对刚刚发生的事作出反应"。

因此 loopback API 才是主契约，MCP 是它之上的一层薄适配器。agent loop 始终是**主动监听**的那一方。

开放问题：bridge 是否应该在 JSON-lines socket 之外，同时提供 WebSocket（推送、多语言）？在出现具体需求之前延后——并入 [#7](https://github.com/guajun/mc-agent/issues/7)。

## 解耦

* bridge 与 loop 是两个进程、两个仓库、两个故障域。
* loop 依赖 bridge 的**包**（为了客户端类），而对守护进程只通过 socket 通信；它不 import 守护进程。
* mod 是一个普通 Fabric mod，不依赖上面两者中的任何一个；从 0.5.0 起它也带有服务端视角（见 [分叉一个活的世界](../protocol-snapshot.md)）。

## Phase 1 范围

把上述管道发出来：mod 协议 v1、带事件重放的 bridge 守护进程、CLI、可选 MCP 前端、带 Hermes 后端的 agent loop，以及**不需要游戏**就能跑的测试。

不在 Phase 1：任何用例逻辑、强制的运行时监管进程，以及 phase 2+ 的设计。等有具体东西要建时，它们会作为单独的 RFC 提出。
