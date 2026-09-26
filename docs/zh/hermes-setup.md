# Hermes 与无人值守运行

[English](https://guajun.github.io/mc-agent/hermes-setup/)

Hermes 只是可选 Harness 之一。它没有单独的 Minecraft backend；它与 Codex、
Claude Code 和其它调用者使用同一套本地 Minecraft Agent Toolkit。

## 状态

| 部分 | 状态 |
| --- | --- |
| 服务端视角 Toolkit、玩家查询和聊天上下文包 | 已实现 |
| 与接收方无关的签名 webhook 发送端 | 已在 Toolkit 实现 |
| 便携 Toolkit Skill 与安装指南 | 已实现；见 [Toolkit Skill](toolkit-skill.md) |
| Hermes webhook route、受限 toolset 与回复投递指南 | 已发布于 [Hermes 无人值守运行](hermes-unattended.md) |
| 签名 webhook 与 delivery-ID 互操作 | 等待 [mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7) |
| mc-agent-loop 中直连 Hermes HTTP backend | 临时兼容路径；移除由 [loop #3](https://github.com/guajun/mc-agent-loop/issues/3) 跟踪 |

配置已经写入文档，但互操作问题验证完成前，不要把签名端到端 webhook 投递
当作可运行能力。

## 部署模型

Hermes Gateway、Toolkit 与服务端视角 mod endpoint 初期在同一台机器上运行。
mod 和 Toolkit 留在 loopback；只有 Toolkit forwarder 发出的 HTTPS 请求越过
该边界。

~~~text
服务端聊天 -> mod 捕获 context_id -> Toolkit 事件缓冲
                                      |
                                      +-> 签名出站 webhook -> Hermes route
                                                                  |
                                                                  v
Hermes Agent ---------------- 本地 MCP ------------------------> Toolkit
~~~

Hermes 负责路由、会话、模型、Skill 订阅与回复目标；Toolkit daemon 负责事件
转发与游戏连接。双方都不复制对方职责。

## 今天可用的交互式 Toolkit

启动守护进程，并按已安装 Hermes 版本支持的 MCP 配置注册适配器：

~~~powershell
mc-bridge run
# 配置 Hermes 拉起：
C:/path/to/.venv/Scripts/mc-bridge.exe mcp
~~~

修改 MCP 配置后启动新的 Hermes 会话。先调用 **mc_status** 与
**mc_capabilities**。当前调用者上下文使用 **mc_player**；只有收到的事件带
**context_id** 时才使用 **mc_context**。

按 [安装 Toolkit Skill](toolkit-skill.md) 中已验证的命令安装便携 Skill。
Hermes 没有原生 **gh skill** target；受支持路径是装进 Hermes 自定义 Skill
目录。webhook route、受限 MCP toolset 与投递目标见
[Hermes 无人值守运行](hermes-unattended.md)。

## 配置事件发送端

只有同时配置 URL 与 secret 时 Toolkit forwarding 才会启动。凭据放在环境变量
或受保护 JSON 配置中，不放进命令参数。

~~~powershell
$env:MC_AGENT_WEBHOOK_URL = "https://<receiver>/hooks/mc-agent"
$env:MC_AGENT_WEBHOOK_SECRET = "<独立随机密钥>"
mc-bridge forward --events chat,game,mark,error
~~~

接收方必须验证 **X-MC-Agent-Signature**、拒绝过期时间戳，并按
**X-MC-Agent-Event-Id** 去重。forwarder 不跟随重定向，因此应配置最终 URL。
限制该 Hermes route 可用的 MCP 工具，并把特权命令授权与玩家身份分开。

聊天 payload 可以包含 **context_id**。Agent 应尽快读取它，处理结构化的
expired/not-found 结果，再按需查询当前状态。回复投递属于 Hermes route；
forwarder 不会把 Agent 答案发回 Minecraft。

## 临时兼容 loop

在 webhook/Skill 工作流验证完成前，现有 loop 仍可监听聊天，并通过
OpenAI-compatible endpoint 调用 Hermes：

~~~powershell
mc-agent-loop run --backend hermes --env-file .env
~~~

默认触发词是 **@agent**。该路径需要 loop 包、模型 API 环境变量和该包所记录的
客户端视角回复机制。它只为兼容保留，不是目标架构，也不是 Toolkit 必需组件。

## 安全检查表

* mod 与 Toolkit 控制 socket 只监听 loopback。
* 使用独立、高熵 webhook secret。
* 在解析或路由前验证签名与时间戳。
* 按 delivery ID 去重，因为重试沿用同一个事件 ID。
* 只给事件 route 必需的 MCP 工具。
* UUID/名字只是上下文，不是授权。
* 在 Hermes 中显式配置回复投递，不从发送者身份推断。
