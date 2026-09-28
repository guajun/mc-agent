# Minecraft Agent Toolkit

让 AI 智能体读取 Minecraft 世界、查找玩家与实体、执行命令并处理实体快照。
产品由一个 Fabric mod 和一个 Go 单二进制组成：不需要 MCP 服务器，也不需要运行
Python。

**[从这里开始](docs/zh/getting-started.md)** · **[文档站点](https://guajun.github.io/mc-agent/zh/)** · **[English README](README.md)**

## 我能做什么？

| 能力 | 示例请求 |
| --- | --- |
| 读取世界 | “总结当前世界状态和在线玩家。” |
| 查看玩家与实体 | “找到我并描述附近的实体。” |
| 执行命令并读取结果 | “列出在线玩家。” |
| 采集实体顺序快照 | “为这次实验采集一个实体快照。” |
| 由事件驱动 | “有玩家发言；读取发言时刻的上下文并安全作答。” |

可用操作取决于连接的 mod。智能体会先查询 `capabilities`。模型与对话由你的
智能体应用提供；Toolkit 从不调用模型。

## 组件关系

```text
你 → 智能体应用 → mc-agent CLI ─┐
     （Harness）                 │ loopback IPC
                                └→ mc-agent daemon ↔ mc-agent-interface mod ↔ Minecraft
```

| 组件 | 需要安装什么 | 运行位置 |
| --- | --- | --- |
| 游戏 mod | 在实例的 `mods/` 中放入 [`mc-agent-interface` 0.8.0+](https://github.com/guajun/mc-agent-interface-mod/releases) | 游戏服务端：专用服务器、LAN 房主或单机集成服务器 |
| Toolkit | 一个 Go 二进制 `mc-agent-0.5.0`（Windows amd64、Linux amd64、macOS arm64） | Harness 的实际执行环境（本机、WSL、容器或远端） |
| 智能体应用 | 任何能执行命令的 Harness，如 Codex、Claude Code、Hermes | Harness 所在位置 |
| 操作说明 | 可移植的 [Toolkit Skill](docs/zh/toolkit-skill.md) | 每个 Harness 安装一次 |

专用服务器只需要 mod；LAN 房主只需要 mod，Toolkit 连接实际公布的 LAN 端口；
单机世界使用集成服务器和同一个 mod。服务器侧 daemon 是可选的：同一个二进制
既可以跑在服务器旁，也可以跑在 Harness 环境，多个 daemon 可独立连接。不需要
SSH、额外公网端口、代理或客户端 mod 中继。

## 快速开始

**游戏侧需要：** Minecraft **26.2**、Fabric Loader **0.19.5**、Fabric API
**0.161.0**、Java **25**；二进制需要受支持的平台（Windows amd64、Linux glibc
amd64、macOS arm64）。运行发行版二进制不需要 Python 或 Go。

1. **安装游戏 mod。** 从 [mod releases](https://github.com/guajun/mc-agent-interface-mod/releases)
   下载 `mc-agent-interface-0.8.0.jar`（或[自行构建](docs/zh/mod-building.md)），
   与 Fabric API 一起放入 `mods/`，然后打开世界或启动服务器。

2. **安装二进制与 Skill。**

    === "Linux amd64 / macOS arm64"

        ```bash
        curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
            | sh -s -- --version 0.5.0 --skill-harness codex
        ```

    === "Windows amd64"

        ```powershell
        iwr -useb https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.ps1 -OutFile install.ps1
        ./install.ps1 -Version 0.5.0 -SkillHarness codex
        ```

3. **把二进制加入当前 shell 的 PATH，然后连接。** 安装器不会主动修改 PATH
   （除非使用了 `--add-to-path` / `-AddToPath`）；也可以为当前会话手动设置：

    === "Linux amd64 / macOS arm64"

        ```bash
        export PATH="$HOME/.mc-agent/bin:$PATH"
        ```

    === "Windows amd64"

        ```powershell
        $env:Path = "$env:LOCALAPPDATA\mc-agent\bin;$env:Path"
        ```

4. **连接并检查。**

    ```bash
    mc-agent daemon start
    mc-agent doctor
    mc-agent state
    ```

    远程或 LAN 服务器需要先用 mod 指纹和服务器签发的凭证注册目标，见
    [安装与首次运行](docs/zh/getting-started.md)。

## 文档

- [安装与首次运行](docs/zh/getting-started.md) —— mod、二进制、首次连接与首次调用。
- [安装参考](docs/zh/install.md) —— 升级、卸载、支持平台与部署位置。
- [架构](docs/zh/concepts.md) —— 组件、术语与边界。
- [工具](docs/zh/tools.md) —— CLI 命令参考与能力模型。
- [Toolkit Skill](docs/zh/toolkit-skill.md) —— 可移植操作指南。
- [无人值守 Hermes：webhook + Skill](docs/zh/hermes-unattended.md) —— 事件驱动运行与在线要求。
- [故障排查](docs/zh/troubleshooting.md) —— 连接、身份与写操作结果。

本仓库保存文档、权威 Skill 源和实验工具。发行包在固定提交上从这里生成；
二进制与安装器位于 [mc-agent-bridge](https://github.com/guajun/mc-agent-bridge)。

## 许可证

MIT
