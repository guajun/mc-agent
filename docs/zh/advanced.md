# 进阶指南

已经连接好了？按下一项任务选择指南。首次安装请先看[安装与首次运行](getting-started.md)。

## 智能体工作流

| 目标 | 指南 |
| --- | --- |
| 教智能体发现工具、解析玩家 | [安装可移植 Toolkit Skill](toolkit-skill.md) |
| 理解实时玩家上下文与聊天时刻 bundle | [玩家身份与上下文](player-identity.md) |
| 由游戏事件触发智能体 | [无人值守 Hermes：webhook + Skill](hermes-unattended.md) |
| 了解 Hermes 集成与遗留 loop | [Hermes 集成状态](hermes-setup.md) |

签名事件发送端与 Skill 已可用，webhook 路径已用 `tools/webhook_receiver.py` 里的本地
接收端验证。转发到 Hermes 通用路由使用文档化的 `hermes-v2` shim；请在你的网关版本
上验证。可选的遗留 `mc-agent-loop` 见 Hermes 指南，它依赖 Python bridge。

## 世界与实验

| 目标 | 指南 |
| --- | --- |
| 理解实体快照与遗留世界 fork | [世界 fork 协议（遗留）](protocol-snapshot.md) |
| 检查恢复后的 fork | [Fork 验证](fork-verify.md) |
| 准备隔离服务器 | [无头 lab 服务器](lab-server.md) |
| 为 lab 构建观察代码 | [构建 mod](mod-building.md) |

远程路径绝不是本机路径。`snapshot` 在游戏主机上写实体顺序记录；`fork`/`restore` 仍是
遗留本地 Python 工具，对远程目标会被拒绝。不承诺完整冻结/重接入保证。

## 集成与证据

### 已验证示例：Minecart ROM { #verified-example }

[Issue #13](https://github.com/guajun/mc-agent/issues/13) 及其子 issue #14–#21 已
全部完成。三个阶段均在 2026-09-26 执行、独立评审并合并：

| 阶段 | 验收结果 | 证据与复现 |
| --- | --- | --- |
| 工具链就绪 | 实机门禁 **8/8 PASS** | [验收 runbook](minecart-rom-runbook.md) |
| 智能体自主冷启动 | 智能体编写 logger，操作/捕获 5 辆矿车，**5/5 审计 PASS** | [原始冷启动证据](evidence/rom20-coldstart/README.md) |
| 固化的真实游戏回归 | **3 个新实例、2 个程序、1 次全新下载、8 个反例** | [回归指南](rom21-regression.md) · [提交的证据](evidence/rom21-regression/README.md) |

冷启动记录原始自主模型运行；回归在无模型条件下复用其成功配方，是重复性的证据。
[runbook](minecart-rom-runbook.md)区分离线验证、录制的演示与全新的实机运行。

### 技术 runbook

这些是开发者与实验 runbook，不是安装 Toolkit 或接入智能体的必需步骤。它们记录 MCP
时期的实验工具，仅作历史保留（MCP 已在 bridge #12 中移除）。

- [可审计冷启动运行](coldstart-protocol.md)
- [只读矿车审计 mod](minecart-audit.md)
- [阶段一集成门禁](stage1-gate.md)
- [阶段一集成运行](stage1-integration.md)
- [Minecart ROM 验收 runbook](minecart-rom-runbook.md)
- [Minecart ROM 回归](rom21-regression.md)

## 设计与贡献

- [架构与术语](concepts.md) —— 当前组件边界。
- [RFC 0001](rfc/0001-agent-interface.md) —— 历史接口与 daemon 设计。
- [RFC 0002](rfc/0002-programmable-tool-calls.md) —— 延期的可编程工具调用。
- [文档开发](contributing-docs.md) —— 预览并检查两种语言。
