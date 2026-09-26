# 进阶指南

已经连通了？按下一步要做的事情选择指南。
首次安装请从[安装与首次运行](getting-started.md)开始。

## 智能体工作流

| 目标 | 指南 |
| --- | --- |
| 教智能体发现工具、定位玩家 | [安装可移植的 Toolkit Skill](toolkit-skill.md) |
| 了解实时玩家上下文与聊天时刻快照 | [玩家身份与上下文](player-identity.md) |
| 用游戏事件触发智能体 | [Hermes 无人值守：webhook + Skill](hermes-unattended.md) |
| 了解 Hermes 集成状态与旧版 loop | [Hermes 集成说明](hermes-setup.md) |

签名事件发送器与 Skill 已可用，但 Hermes 端到端签名投递仍依赖
[mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7)。
无人值守指南属于集成工作，不是默认入门路径。
可选的旧版 `mc-agent-loop` 兼容用法见 Hermes 指南。

## 世界与实验

| 目标 | 指南 |
| --- | --- |
| 分叉和恢复世界 | [世界分叉协议](protocol-snapshot.md) |
| 检查恢复后的分叉 | [分叉校验](fork-verify.md) |
| 创建隔离的服务端 | [无头实验室服务器](lab-server.md) |
| 为实验室构建观测代码 | [构建 mod](mod-building.md) |

## 集成与证据

### 已验收案例：Minecart ROM { #verified-example }

[Issue #13](https://github.com/guajun/mc-agent/issues/13) 及子任务 #14–#21 已完成。
三个阶段均于 2026-09-26 完成实测、独立审查并合并：

| 阶段 | 验收结果 | 证据与复现 |
| --- | --- | --- |
| 工具链就绪 | 实机完整门禁 **8/8 PASS** | [总运行手册](minecart-rom-runbook.md) |
| 智能体自主冷启动 | 自写 logger，实际操作并捕获 5 辆矿车，**5/5 审计 PASS** | [原始冷启动证据](evidence/rom20-coldstart/README.md) |
| 成功产物固化为实机回归 | **3 个新实例、2 种程序、1 次冷下载、8 个负例** | [回归指南](rom21-regression.md) · [已提交证据](evidence/rom21-regression/README.md) |

冷启动记录原始模型自主运行；回归不调用模型，复用成功配方验证可重复性。
[总运行手册](minecart-rom-runbook.md)分别提供离线复验、已有结果 Demo 和全新实机运行入口。
可选的 Hermes webhook 兼容仍是独立跟进项。

### 技术运行手册

以下是开发和实验验收手册，安装 Toolkit、接入智能体时无需阅读。

- [可审计的冷启动运行](coldstart-protocol.md)
- [只读矿车审计 mod](minecart-audit.md)
- [阶段一集成门禁](stage1-gate.md)
- [阶段一集成运行](stage1-integration.md)
- [Minecart ROM 验收手册](minecart-rom-runbook.md)
- [Minecart ROM 回归](rom21-regression.md)

## 设计与贡献

- [架构与术语](concepts.md)：当前组件边界。
- [RFC 0001](rfc/0001-agent-interface.md)：接口和常驻进程的历史设计。
- [RFC 0002](rfc/0002-programmable-tool-calls.md)：已推迟的可编程工具调用方案。
- [文档开发](contributing-docs.md)：本地预览和双语检查。
