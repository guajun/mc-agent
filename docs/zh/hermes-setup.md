# Hermes 与 Toolkit

[English](https://guajun.github.io/mc-agent/hermes-setup/)

Hermes 是可能的 Harness 之一。它没有单独的 Minecraft 后端：它和 Codex、
Claude Code 以及所有其他调用者一样，使用同一个 CLI-only Minecraft Agent Toolkit。
没有需要注册的 MCP 服务器。

## 两种集成路径

| 路径 | 谁发起运行 | 什么必须保持运行 |
| --- | --- | --- |
| **用户主动** | 用户让 Hermes 查看或修改世界 | Hermes 调用 CLI 期间：游戏 + `mc-agent` daemon |
| **无人值守** | daemon 的签名 webhook 到达 Hermes 路由 | 游戏、daemon、接收端和 Hermes gateway |

## 各部分状态

| 部分 | 状态 |
| --- | --- |
| 服务端视角 Toolkit、玩家查询与聊天上下文 bundle | 已实现 |
| 带同端口控制传输的 Go CLI/daemon | 已实现（控制协议 1） |
| 可移植 Toolkit Skill 与安装指南 | 已实现；本仓库为唯一来源 |
| 接收端中立的签名 webhook 发送端 | 已实现；已用本地接收端验证 |
| Hermes webhook 路由、Skill 订阅与回复投递 | 用户自有配置；路由需要被允许执行 CLI |
| Hermes 通用 HMAC V2 路由方案与 `X-MC-Agent-*` 头 | 有文档化的兼容 shim（`webhook_receiver.py --scheme hermes-v2`）；请在网关中验证 |
| mc-agent-loop 里的 Hermes HTTP 直连后端 | 依赖 Python bridge 的遗留兼容路径；移除进度见 [loop #3](https://github.com/guajun/mc-agent-loop/issues/3) |

## 用户主动配置

1. 安装二进制并指向世界（[安装与首次运行](getting-started.md)）。
2. 安装 [Toolkit Skill](toolkit-skill.md)，让 Hermes 了解发现、身份、游标和未知写
   结果流程。
3. 让 Hermes 通过正常的命令执行调用 CLI。提示示例：

   > 用 `mc-agent version`、`doctor`、`capabilities` 和 `state` 检查 Minecraft
   > 连接，然后总结世界与在线玩家。不要改变世界。

daemon 不会随每次运行重启；它自行重连并报告缺口。会话、模型调用和对话归 Hermes；
Toolkit 是无模型的工具面。

## 在线要求

- 游戏必须带 mod 运行，任何调用才可用。游戏停止后接口停止；daemon 等待并重连。
- 用户主动：Hermes 调用 CLI 期间 daemon 必须运行。
- 无人值守：daemon、webhook 接收端（或 Hermes 自己的监听）和 Hermes gateway 都必须
  保持在线。webhook 投递在内存中：daemon 重启会丢失排队事件。
- daemon 从不调用模型，也不管理 Hermes 会话；它不会把回答写回 Minecraft，这属于
  路由/投递职责。

## 无人值守路径

daemon 用 HMAC-SHA256 签名把选定事件转发到一个 HTTP(S) 接收端。仓库自带一个本地、
接收端中立的接收器用于验证，并可以把已验证事件转发到 Hermes 通用 webhook 路由。
完整步骤见[无人值守 Hermes：webhook + Skill](hermes-unattended.md)。

简要版本：

```bash
# 接收端（校验签名、打印事件；可选转发）
python tools/webhook_receiver.py serve --secret test-secret --port 8645

# daemon（前台或由服务管理器托管；`daemon start` 不接受 webhook 参数）
mc-agent daemon run --webhook-url http://127.0.0.1:8645/hook \
    --webhook-secret test-secret --webhook-events chat,game,mark,error
```

在 Toolkit 侧，权限来自控制凭证：只观察的路由给 `read` 凭证，只有任务确实需要
`command`/`mark`/`snapshot` 时才给 `read+write`。聊天文本是不可信输入，永远不是授权。

## 安全检查清单

- 游戏控制端口按游戏本身的方式做防火墙；daemon 本地 IPC 保持在 loopback。
- 使用专用的高熵 webhook secret；校验签名与时间戳；按稳定事件 id 去重。
- 路由只保留需要的工具访问。运行 CLI 需要进程执行权限；关闭该路由的 web/file/
  computer 工具并窄化提示模板。
- 使用专用的最小权限控制凭证，部署结束时吊销。
- UUID/名字与聊天文本是上下文，不是授权。
- 在 Hermes 中显式配置回复投递，绝不只凭发送者身份推断。
