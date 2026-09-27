# Toolkit 工具

[English](https://guajun.github.io/mc-agent/tools/)

Minecraft Agent Toolkit 由 **mc-agent-bridge** 实现。实时 **capabilities**
结果才是权威来源：MCP 工具面会根据已连接 mod 与视角进行筛选。

## CLI

| 命令 | 用途 |
| --- | --- |
| **mc-bridge discover** | 不启动守护进程，解释服务端视角端口发现 |
| **mc-bridge run** | 持有 mod 连接并提供 loopback API |
| **mc-bridge call <方法> [json]** | 对守护进程发一次 JSON 调用 |
| **mc-bridge watch --events chat,game** | 流式读取缓冲/实时事件 |
| **mc-bridge mcp** | 通过 MCP 向 Harness 暴露 Toolkit |
| **mc-bridge forward --events ...** | 把选定事件发给一个已配置的签名 webhook |

先运行 **status** 和 **capabilities**。常见服务端视角方法：

| 方法 | 用途 |
| --- | --- |
| **status**、**capabilities** | 连接、发现与支持的工具面 |
| **state** | 权威世界/tick 状态 |
| **player** | 按 UUID 或名字解析在线玩家，返回实时上下文/视角 |
| **context** | 按 context ID 读取有界聊天时刻上下文包 |
| **entities** | 附近实体 |
| **command**、**command_output** | 发命令；后者还等待回答 |
| **events** | 从游标重放缓冲事件 |
| **save** | 报告世界存档元数据 |
| **snapshot**、**snapshots** | 捕获/列出实体顺序快照 |
| **fork**、**restore**、**verify**、**order** | 复制并验证世界分叉 |
| **wait**、**mark**、**stop** | 等待、标记和守护进程控制 |

**screen**、**chat**、**connect**、**world**、**lan** 和录制等客户端专属方法
只有显式选择客户端视角，且 mod 公布对应能力时才出现。

## MCP

MCP 工具使用 **mc_** 前缀，例如 **mc_status**、**mc_capabilities**、
**mc_player**、**mc_context**、**mc_state**、**mc_entities**、
**mc_command_output**、**mc_events**、**mc_snapshot**。MCP 进程只是连接已运行
守护进程的适配器，不持有游戏连接。

Harness 应：

1. 调用 **mc_status** 与 **mc_capabilities**；
2. 任务需要玩家上下文时解析相关玩家；
3. 只读取任务所需的实时世界数据；
4. 事件提供 context ID 时尽快调用 **mc_context**；
5. 把身份视为上下文，而不是命令授权。

## 事件转发

可选 forwarder 与接收方无关。它为每个出站 POST 签名、重试临时失败，并复用
稳定 event ID 供接收方去重。URL 与 secret 来自环境或受保护配置文件。它不
运行模型、不管理会话，也不投递 Agent 回复。

见 [Hermes 与无人值守运行](hermes-setup.md) 和
[Toolkit 参考](https://github.com/guajun/mc-agent-bridge#forwarding-events-to-a-webhook)。

## 仓库工具

| 工具 | 用途 |
| --- | --- |
| **lab_server.py** | 供应并控制无头 Fabric 实验室 |
| **fork_verify.py** | 检查、还原并比较世界分叉 |
| **fake_player.py** | 创建并驱动 Carpet 假人 |
| **game_cmd.py** | 运行游戏命令并打印反馈 |
| **mcp_probe.py** | 对运行中的 Toolkit 调一个 MCP 工具 |
| **launch_instance.py** | 不通过 GUI 启动器启动客户端 |
| **smoke_offline.py** | 旧 daemon/agent-loop 路径的兼容测试 |

## 旧 agent-loop

**mc-agent-loop** 不属于 Toolkit 工具面。它仍是可选兼容监听器，包含 **echo**
与临时 **hermes** backend，默认聊天触发词是 **@agent**。Codex 与 Claude
Code 作为用户主动型 Harness 运行，不能再描述为 loop backend。
