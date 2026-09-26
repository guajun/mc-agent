---
hide:
  - toc
---

# 让你的智能体走进 Minecraft 世界

**读取世界、执行命令、开展可重复的实验。**

Minecraft Agent Toolkit 通过本机工具连接 AI 智能体与 Minecraft。
沿用你熟悉的智能体应用，也可以先在终端体验，不必先配置模型。

<div class="landing-actions" markdown>

[安装并开始体验](getting-started.md){ .md-button .md-button--primary }
[查看工具能力](tools.md){ .md-button }

</div>

## 可以做什么？

<div class="grid cards" markdown>

- **了解世界**

    读取服务端的权威世界状态，查找在线玩家，检查附近实体。

    试着说：“概括当前世界状态和在线玩家。”

- **通过工具执行操作**

    从智能体或终端执行 Minecraft 命令，并读取命令结果。

    试着说：“列出当前在线玩家。”

- **重复和校验实验**

    采集实体顺序快照，分叉世界，检查恢复结果。

    [了解世界分叉 →](protocol-snapshot.md)

- **沿用你的智能体应用**

    接入 Codex、Claude Code、Hermes 等 MCP 客户端；有终端权限的智能体也能使用 CLI。
    模型与对话由你的应用提供。

    [接入智能体 →](getting-started.md#4-connect-your-agent)

</div>

**已通过实机验收：** [Minecart ROM](minecart-rom-runbook.md) 已于 2026-09-26
完成工具链门禁、智能体自主冷启动和全新实例回归。
[查看结果与复现入口 →](advanced.md#verified-example)

## 四步完成连接

**开始前准备：** Minecraft 26.2、Fabric Loader 0.19+、Fabric API、JDK 25、
Python 3.11+ 和 Git。Minecraft、Toolkit 和智能体运行在同一台机器。
可以从一个单机世界开始，也可以使用已有的独立 Fabric 服务端。

1. **安装 Fabric mod。** 从源码构建，将 jar 放入 `mods/`，进入世界。
2. **安装 Toolkit。** 克隆 `mc-agent-bridge`，安装到 Python 虚拟环境。
3. **检查连接。** 一个终端运行常驻进程，另一个终端依次检查 `status`、`capabilities` 和 `state`。
4. **接入智能体。** 注册 MCP 适配器，让智能体描述你的世界。

[逐步安装指南](getting-started.md)提供 Windows 与 macOS/Linux 命令、路径示例、
MCP 配置和成功标志。首次连接检查不需要 AI 模型。
目前安装仍包含 mod 源码构建，指南尚未提供全自动安装器。

<div class="landing-actions" markdown>

[开始安装](getting-started.md){ .md-button .md-button--primary }
[已经装好？检查连接](getting-started.md#3-check-the-connection){ .md-button }

</div>

## 架构方案

```text
你 → 智能体应用 → Minecraft Agent Toolkit ↔ Fabric mod ↔ Minecraft 世界
     (Harness)       MCP / CLI + 常驻进程       服务端视角
```

| 组件 | 负责什么 |
| --- | --- |
| 智能体应用（Harness） | 运行模型和对话，决定调用哪些工具。 |
| Toolkit（`mc-agent-bridge`） | 提供 `mc-bridge` 命令和 MCP 工具，保持与 Minecraft 的连接。 |
| Fabric mod | 读取服务端世界状态并执行请求；单机世界也适用。 |

使用期间保持 Toolkit 常驻进程运行，智能体的 MCP 适配器会连接它。
游戏控制连接仅限本机；实际可用工具从连接的 mod 查询。

[查看完整架构与部署说明 →](concepts.md)

## 接下来去哪里？

| 我想…… | 打开 |
| --- | --- |
| 安装、升级或更换游戏路径 | [安装参考](install.md) |
| 解决连接问题 | [首次运行帮助](getting-started.md#something-didnt-work) · [疑难排查](troubleshooting.md) |
| 教智能体正确使用 Toolkit | [可选的 Toolkit Skill](toolkit-skill.md) |
| 配置事件触发、无人值守或实验 | [进阶指南](advanced.md) |

Hermes 无人值守属于进阶集成，目前仍有签名投递问题待解决。
其[配置与状态](hermes-unattended.md)独立于上面的交互式使用流程。
