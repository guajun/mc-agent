# Minecraft Agent Toolkit

让你的 AI 智能体读取 Minecraft 世界、查找玩家和实体、执行命令，并使用世界快照。
沿用你熟悉的 MCP 智能体应用，也可以直接在终端体验。

**[从这里开始](docs/zh/getting-started.md)** · **[中文文档网站](https://guajun.github.io/mc-agent/zh/)** · **[English README](README.md)**

## 能做什么？

| 能力 | 你可以这样使用 |
| --- | --- |
| 读取世界状态 | “概括当前世界状态和在线玩家。” |
| 查看玩家和实体 | “找到我的玩家，描述我周围的实体。” |
| 执行命令并读取结果 | “列出当前在线玩家。” |
| 采集快照 | “为这次实验采集一份实体顺序快照。” |
| 重复实验 | 按[世界分叉指南](docs/zh/protocol-snapshot.md)复制世界并校验恢复结果。 |

实际可用操作由连接的 mod 决定，智能体先查询 `capabilities` 再调用工具。
模型和对话由你使用的智能体应用提供。

**已验收案例：** [Minecart ROM](docs/zh/minecart-rom-runbook.md) 已于 2026-09-26
完成实机工具链门禁、真实智能体自主冷启动和全新游戏实例回归三个阶段。
证据与复现入口见指南；脚本回归与原始自主运行分别记录。

## 安装并完成第一次连接

**需要准备：** Minecraft **26.2**、Fabric Loader **0.19+**、Fabric API、
**JDK 25**、**Python 3.11+** 和 Git。游戏接口、Toolkit 和智能体应用运行在同一台机器。
建议先用一个单机世界体验。

1. **安装游戏 mod。** 按[构建与安装步骤](docs/zh/getting-started.md)操作，随后进入世界或启动独立服务端。目前文档提供的是源码构建路径。
2. **安装 Toolkit。** 在准备长期保留的工作目录中，运行对应系统的命令。
3. **检查连接，再接入智能体。** [首次运行指南](docs/zh/getting-started.md)提供成功标志和 MCP 配置。

### Windows · PowerShell

```powershell
git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/python -m pip install -e "mc-agent-bridge[mcp]"

# 改成包含 mc-agent-server/ 的游戏实例或服务端目录。
# 使用 Toolkit 期间保持此终端打开。
.venv/Scripts/mc-bridge run --server-dir "C:/Minecraft/my-instance"
```

在**相同工作目录打开第二个终端**：

```powershell
.venv/Scripts/mc-bridge call status
.venv/Scripts/mc-bridge call capabilities
.venv/Scripts/mc-bridge call state
```

### macOS / Linux · shell

```bash
git clone https://github.com/guajun/mc-agent-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -e "mc-agent-bridge[mcp]"

# 改成包含 mc-agent-server/ 的游戏实例或服务端目录。
# 使用 Toolkit 期间保持此终端打开。
.venv/bin/mc-bridge run --server-dir "/path/to/minecraft-instance"
```

在**相同工作目录打开第二个终端**：

```bash
.venv/bin/mc-bridge call status
.venv/bin/mc-bridge call capabilities
.venv/bin/mc-bridge call state
```

**成功标志：** `status` 显示 `connected: true`、`vantage: server`，
`state` 返回世界数据。完成这些检查无需模型账号。
遇到问题时，先查[首次运行帮助](docs/zh/getting-started.md)。

## 架构方案

```text
你 → 智能体应用 → Minecraft Agent Toolkit ↔ Fabric mod ↔ Minecraft 世界
     (Harness)       MCP / CLI + 常驻进程       服务端视角
```

mod 从服务端读取权威世界状态，单机世界使用内置的集成服务端。
Toolkit 保持游戏连接，智能体应用负责决定何时调用哪些工具。
MCP 适配器连接已经运行的 Toolkit 常驻进程。游戏控制连接仅限本机 loopback。

| 组件 | 安装内容 |
| --- | --- |
| 游戏 mod | 将 [mc-agent-interface-mod](https://github.com/guajun/mc-agent-interface-mod) 放入实例的 `mods/` |
| Toolkit | 安装 [mc-agent-bridge](https://github.com/guajun/mc-agent-bridge)，获得 `mc-bridge` 命令 |
| 智能体应用 | 使用现有 MCP 客户端（如 Codex、Claude Code、Hermes），或能运行 CLI 的智能体 |
| 可选操作说明 | 安装可移植的 [Toolkit Skill](docs/zh/toolkit-skill.md) |

本仓库存放文档、共享 Skill 和实验工具。标准用法只需在智能体应用之外安装 mod 和 Toolkit。
组件术语与部署方案见[架构说明](docs/zh/concepts.md)。
Hermes 无人值守模式仍有签名投递联调问题，状态与配置见[独立指南](docs/zh/hermes-unattended.md)。

## 继续探索

- [安装细节、升级与卸载](docs/zh/install.md)
- [CLI 命令与 MCP 工具](docs/zh/tools.md)
- [进阶指南、实验与设计历史](docs/zh/advanced.md)
- [开发和预览文档](docs/zh/contributing-docs.md)

## 许可证

MIT
