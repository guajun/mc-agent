# mc-agent

让 AI 智能体**观测**、**驱动**并**分叉**一个 Minecraft 世界，同时让智能体的运行时和游戏彻底解耦。

这个项目不关心你在研究什么：没有大炮代码、没有 TNT 逻辑、没有分析。框架只做两件事——把游戏里的事实搬出来、把意图送进去；怎么解释由智能体决定。

```
Hermes（或任意运行时）  <->  bridge  <->  接口 mod  <->  Minecraft
     判断力                 接缝          进程内的事实       世界
```

## 有什么

| 模块 | 是什么 | 仓库 |
| --- | --- | --- |
| **接口 mod** | 一个 Fabric 客户端 **且** 服务端 mod，通过本地 socket 暴露状态、实体、命令、聊天、录制、快照和事件 | [mc-agent-interface-mod](https://github.com/guajun/mc-agent-interface-mod) |
| **bridge** | 唯一与游戏通信的进程：守护进程 + JSON-lines API + MCP 前端 + 在其上组合出来的工具 | [mc-agent-bridge](https://github.com/guajun/mc-agent-bridge) |
| **agent loop** | 主动的一侧：监听聊天、唤醒后端、把答案送回游戏；也可以按需跑单次 | [mc-agent-loop](https://github.com/guajun/mc-agent-loop) |
| **本仓库** | 文档、驱动整套东西的工具、以及 RFC | 你在这里 |

## 三种运行方式

同一个 mod、同一个 bridge 通吃三种；不同的只是**智能体附着在哪一个实例上**。

| 轨道 | 智能体是 | 适合 |
| --- | --- | --- |
| **活客户端** | 你自己游戏里的一个 mod | "看看我正在看的东西"、和人类一起玩 |
| **活服务端** | 你所在服务器里的一个 mod——或者单机世界里的集成服务端 | 权威数据、驱动假人、对真实发生的事作出反应 |
| **隔离实验室** | 智能体自己拉起来的无头服务器 | 可复现的物理实验：冻结、步进、分叉世界、跑一百次 |

轨道三是让实验变"诚实"的那个，也是这个项目存在的原因：存档记录了方块和实体 NBT，但**不记录实体的 tick 顺序**——而这个顺序会改变任何"逐个实体计算"的结果。mod 能读到它，快照协议把它记下来，[`fork_verify.py`](fork-verify.md) 来证明还原是否复现了它。

## 快速开始

```bash
git clone https://github.com/guajun/mc-agent && cd mc-agent

# 1. 编译 mod，丢进实例的 mods/ 目录
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py --minecraft-dir <实例> \
    --version 26.2-Fabric --jdk <jdk25>

# 2. bridge 持有游戏连接
python -m venv .venv && .venv/Scripts/pip install -e "mc-agent-bridge[mcp]" -e mc-agent-loop
.venv/Scripts/mc-bridge run

# 3. 和它说话
.venv/Scripts/mc-bridge call state
.venv/Scripts/mc-agent-loop run --backend hermes --trigger @codex
```

然后在游戏聊天里打 `@codex 你能看到什么？`；想自己看接口，就在聊天框里敲 `/mcagent state`。

[快速开始 :material-arrow-right:](getting-started.md){ .md-button .md-button--primary }
[安装说明 :material-arrow-right:](install.md){ .md-button }
[整体结构 :material-arrow-right:](concepts.md){ .md-button }

## 手边没有游戏？

整套链路都可以在没有 Minecraft 的情况下验证：

```bash
python tools/smoke_offline.py                     # 假 mod + 守护进程 + loop
python tools/smoke_offline.py --backend hermes    # 真模型驱动真工具
```

## 文档

* [快速开始](getting-started.md) —— 安装、运行、第一次对话、第一个实验
* [安装说明](install.md) —— 版本要求、升级、卸载，以及各种文件落在哪
* [整体结构](concepts.md) —— 心智模型，以及"新能力该放进哪一层"
* [智能体在游戏里是谁](player-identity.md) —— 第二个客户端、Carpet 假人，或服务端 mod
* [分叉一个活的世界](protocol-snapshot.md) —— 快照协议与还原配方
* [用 Hermes 运行智能体](hermes-setup.md) —— 本项目对着开发的运行时
* [安装 Toolkit Skill](toolkit-skill.md) —— 每个运行时共用的一份可移植 Skill
* [Hermes 无人值守](hermes-unattended.md) —— webhook 触发、加载同一份 Skill
* [无头实验室服务器](lab-server.md) —— 供给、启停与控制
* [工具](tools.md) —— 全部 CLI 与 MCP 工具
* [疑难排查](troubleshooting.md) —— 那些坑，大多是真机上撞出来的
* [RFC 提案](rfc/0001-agent-interface.md) —— 已经定了什么、什么还开放

!!! note "部分页面暂时只有英文"
    站点使用按语言的目录结构，未翻译的页面会**回退到英文原版**（比如 `hermes-setup`、`lab-server`、
    `fork-verify` 和两篇 RFC）。如果你需要，我可以继续翻。
