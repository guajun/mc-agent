# Minecraft Agent Toolkit

为 Minecraft 世界提供服务端权威视角的本地工具面，同时不把模型或
Harness 逻辑塞进游戏。

[English](https://guajun.github.io/mc-agent/)

## 一套 Toolkit，任意 Harness

```text
用户 -> Harness ---------------- MCP / CLI -------------------+
                                                             |
游戏聊天 -> 上下文包 -> 签名 webhook -> Harness              |
                                                             v
Minecraft 服务端 <-> 服务端视角 Fabric mod <-> Minecraft Agent Toolkit daemon
```

常规路径由用户在 Codex、Claude Code 或其它 Harness 中主动发起。
Harness 与 Toolkit 在同一台机器上；Harness 按需拉取实时玩家或世界上下文。

无人值守路径由事件发起。服务端视角 mod 在收到聊天时捕获发送者的身份、
位置和视角，事件携带一个短期有效的 `context_id`。可选的 Toolkit
forwarder 把选定事件发给用户单独配置的接收方，例如 Hermes。该 Harness
随后读取上下文包，并使用与其它调用者完全相同的 Toolkit。

!!! info "实现状态"
    服务端视角工具、玩家查询、聊天时刻上下文包和与接收方无关的签名
    webhook 发送端与便携 [Toolkit Skill](toolkit-skill.md) 已经实现。
    [Hermes 无人值守指南](hermes-unattended.md)记录接收方、route 与投递配置，
    但签名端到端投递仍受
    [mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7)
    阻塞。旧的 `mc-agent-loop` 仍可用于兼容，但不是 Toolkit 必需组件。

## 组件

| 名称 | 运行位置 | 职责 |
| --- | --- | --- |
| **Minecraft Agent Toolkit** | Harness 与 mod endpoint 所在机器 | `mc-agent-bridge` 包：守护进程、CLI、JSON-lines API、MCP 适配器和 webhook forwarder |
| **Toolkit daemon** | 本地 Python 进程 | `mc-bridge run` 启动的常驻进程；Bridge daemon 是旧称 |
| **Fabric mod** | 独立服务端或单机集成服务端 | 通过 loopback 暴露权威状态与动作 |
| **Harness** | 与 Toolkit 同机 | 运行 Agent、维护会话并决定做什么 |
| **Skill** | 由 Harness 加载 | 便携的 Toolkit 操作说明；不含协议或会话代码 |
| **agent-loop** | 可选旧进程 | 监听聊天并调用临时保留的 Hermes 或 echo backend |

项目不维护 Codex backend、Claude Code backend 或 Hermes 专用游戏接口。
所有 Harness 使用同一套 Toolkit。

## 快速开始

```powershell
# 安装 Toolkit（mc-agent-bridge 仓库）。
git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]"

# 安装 Fabric mod 并启动 Minecraft 后：
.venv/Scripts/mc-bridge run
.venv/Scripts/mc-bridge call status
.venv/Scripts/mc-bridge call capabilities
.venv/Scripts/mc-bridge call state
```

默认目标是服务端视角。它发现
`<server-dir>/mc-agent-server/port.txt`，绝不会静默回退到客户端 endpoint。
Harness 可以拉起 `mc-bridge mcp`；shell 或不支持 MCP 的 Harness 可以使用
`mc-bridge call`。

[快速开始 :material-arrow-right:](getting-started.md){ .md-button .md-button--primary }
[架构与术语 :material-arrow-right:](concepts.md){ .md-button }
[安装 :material-arrow-right:](install.md){ .md-button }

## 文档

* [快速开始](getting-started.md) - 服务端视角配置与第一次调用
* [安装](install.md) - 版本、部署位置与发现规则
* [架构与术语](concepts.md) - 组件边界和两种启动方式
* [Toolkit 命令与 MCP 工具](tools.md) - 当前工具面与能力发现
* [安装 Toolkit Skill](toolkit-skill.md) - 供受支持 Harness 共用的一份便携 Skill
* [Hermes 无人值守运行](hermes-unattended.md) - webhook route、受限工具与投递状态
* [Hermes 兼容 backend](hermes-setup.md) - 临时 agent-loop 路径
* [玩家身份](player-identity.md) - 调用者上下文、假人和旧客户端身份
* [分叉一个活的世界](protocol-snapshot.md) - 快照与还原
* [无头实验室服务器](lab-server.md) - 隔离实验实例
* [可审计的冷启动运行](coldstart-protocol.md) - Harness 执行与证据契约
* [疑难排查](troubleshooting.md) - 连接与上下文问题
* [RFC](rfc/0001-agent-interface.md) - 历史决策和后续方案
