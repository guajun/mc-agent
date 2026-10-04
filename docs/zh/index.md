---
hide:
  - toc
---

# 让智能体进入 Minecraft 世界

**读取世界。执行命令。构建可复现的实验。**

Minecraft Agent Toolkit 通过一个轻量 Go CLI 和 Fabric mod 把 AI 智能体连接到
Minecraft。可以使用你现有的智能体应用，也可以先在终端里试用。不需要 MCP 服务器，
也不需要运行 Python。

世界分叉首阶段针对 **Minecraft 26.2**，目标是把分支搬到智能体本机开展实验。
当前 Go Toolkit 提供命令、状态和实体 NBT／顺序采集，尚未实现完整世界
`fork`／`restore`。保存影响是可接受的能力边界；保存敏感的实验可考虑更早的干净
起点。参见[范围与实验设计](concepts.md#world-forks-and-experiment-boundaries)。

<div class="landing-actions" markdown>

[安装并试用](getting-started.md){ .md-button .md-button--primary }
[查看工具](tools.md){ .md-button }

</div>

## 你能做什么

<div class="grid cards" markdown>

- **理解世界**

    读取权威世界状态、查找在线玩家、查看附近实体。

    试试：“总结当前世界和在线玩家。”

- **通过工具行动**

    从智能体或终端执行 Minecraft 命令并读取结果。

    试试：“列出在线玩家。”

- **让实验可复现**

    采集实体顺序快照，并保持受控试次在原位进行。

    [实体快照与 fork →](protocol-snapshot.md)

- **使用你偏好的智能体**

    任何能执行命令的 Harness 都能接入：Codex、Claude Code、Hermes 或 shell 脚本。
    模型与对话由你的应用提供。

    [接入智能体 →](getting-started.md#4-connect-your-agent)

</div>

**真实游戏验证：**[Minecart ROM](minecart-rom-runbook.md) 于 2026-09-26 完成
工具链门禁、智能体自主冷启动和新实例回归。[查看结果与复现路径 →](advanced.md#verified-example)

## 四步完成连接

**开始前需要：** 游戏侧 Minecraft 26.2、Fabric Loader 0.19.5、Fabric API
0.161.0、Java 25；二进制需要受支持平台（Windows amd64、Linux glibc amd64 或
macOS arm64）。请把二进制安装在运行智能体的环境；游戏服务器只需要 mod。

1. **安装 Fabric mod。** 下载 0.8.0 jar（或自行构建），复制到 `mods/`，打开世界。
2. **安装二进制与 Skill。** 使用对应平台的固定版本安装器。
3. **检查连接。** 启动 daemon；运行 `doctor`、`capabilities` 和 `state`。
4. **接入智能体。** 让 Harness 调用 `mc-agent` 命令；不需要注册 MCP。

[分步指南](getting-started.md)包含 Windows、macOS 和 Linux 命令、远程/LAN 目标
设置、Skill 安装和预期结果。首次连接检查不需要 AI 模型。

<div class="landing-actions" markdown>

[开始安装](getting-started.md){ .md-button .md-button--primary }
[已安装？检查连接](getting-started.md#3-check-the-connection){ .md-button }

</div>

## 工作原理

```text
你 → 智能体应用 → mc-agent CLI → mc-agent daemon ↔ Fabric mod ↔ Minecraft 世界
     （Harness）   短调用         长连接           服务端视角
```

| 组件 | 职责 |
| --- | --- |
| 智能体应用（Harness） | 运行模型与对话；决定调用哪些工具。 |
| Toolkit（`mc-agent`） | 提供 CLI，以及维持 Minecraft 连接的 daemon。 |
| Fabric mod | 读取服务端世界状态并执行请求的操作，单机同样适用。 |

daemon 在你游玩时持续运行并自行重连；CLI 只是通过 loopback 与它通信的短命令。
可用操作由连接的 mod 发现，除非你选择远程目标，游戏控制连接保持在本机。

[架构与部署细节 →](concepts.md)

## 找到下一步

| 我想要… | 前往 |
| --- | --- |
| 安装、升级或修改游戏路径 | [安装参考](install.md) |
| 修复连接失败 | [首次运行帮助](getting-started.md#something-didnt-work) · [故障排查](troubleshooting.md) |
| 教会智能体 Toolkit 工作流 | [Toolkit Skill](toolkit-skill.md) |
| 配置事件、无人值守或实验 | [进阶指南](advanced.md) |
