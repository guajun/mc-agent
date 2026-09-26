# 安装与首次运行

完成本页后，你将能在终端读取 Minecraft 世界状态，并把同一套工具接入智能体。
终端检查不需要模型账号。如果还没有独立服务端，建议从单机世界开始。

## 开始前准备

| 需要什么 | 如何检查 |
| --- | --- |
| Minecraft 26.2、Fabric Loader 0.19+、Fabric API | 启动对应游戏配置一次，确认可以正常打开。 |
| JDK 25，包含编译器 | `java -version` 和 `javac -version` 显示 25，或在下方指定 JDK 路径。 |
| Python 3.11+ 与 Git | `python --version`（macOS/Linux 用 `python3`）和 `git --version`。 |
| 智能体应用，仅最后一步需要 | 支持 MCP，或有运行 CLI 命令的权限。 |

游戏接口、Toolkit 和智能体应用使用同一台机器。先创建工作目录，
例如 `C:/Minecraft/toolkit-workspace`，在其中打开终端。
以下命令都在这个工作目录运行，无需激活虚拟环境。示例路径需要改为实际路径。

## 1. 安装游戏 mod { #1-install-the-game-mod }

目前文档提供源码构建安装方式。先确认以下四个值：

| 设置 | 应该填什么？ |
| --- | --- |
| `MinecraftDir` / `MINECRAFT_DIR` | 包含 `versions/` 和 `libraries/` 的资源根目录。构建需要 `versions/<版本>/<版本>.json` 和 `<版本>.jar`。 |
| `Version` / `MC_VERSION` | `versions/` 中实际的文件夹名，例如 `26.2-Fabric`。 |
| `GameDir` / `GAME_DIR` | 实际运行实例的目录，包含 `mods/` 和 `saves/`；独立服务端则填包含 `mods/` 的服务端目录。 |
| `JdkDir` / `JDK_DIR` | JDK 25 安装目录，其中应有 `bin/javac` 或 `javac.exe`。 |

启用启动器的版本隔离后，资源根目录与游戏目录可能不同。
构建脚本从资源根目录的 `mods/` 或 `versions/<版本>/.fabric/processedMods/` 查找 Fabric API。
如果两处都没有，在资源根目录的 `mods/` 中也放一份匹配版本的 Fabric API jar 用于编译。

只有服务端文件的目录不能直接作为这个脚本所需的客户端构建资源目录；
先使用已安装的客户端配置构建，再把 jar 复制到服务端。
[实验室 mod 构建](mod-building.md)是另一条进阶路径。

复制 mod 前关闭游戏或服务端。修改四个值后运行：

=== "Windows · PowerShell"

    ```powershell
    git clone https://github.com/guajun/mc-agent-interface-mod
    $MinecraftDir = "C:/Minecraft"
    $GameDir = "C:/Minecraft/instances/my-world"
    $Version = "26.2-Fabric"
    $JdkDir = "C:/Program Files/Java/jdk-25"
    python mc-agent-interface-mod/build.py --minecraft-dir "$MinecraftDir" --version "$Version" --jdk "$JdkDir"
    New-Item -ItemType Directory -Force -Path "$GameDir/mods" | Out-Null
    Copy-Item mc-agent-interface-mod/dist/mc-agent-interface-*.jar "$GameDir/mods/"
    ```

=== "macOS / Linux"

    ```bash
    git clone https://github.com/guajun/mc-agent-interface-mod
    MINECRAFT_DIR="/path/to/minecraft"
    GAME_DIR="/path/to/minecraft/instances/my-world"
    MC_VERSION="26.2-Fabric"
    JDK_DIR="/path/to/jdk-25"
    python3 mc-agent-interface-mod/build.py --minecraft-dir "$MINECRAFT_DIR" --version "$MC_VERSION" --jdk "$JDK_DIR"
    mkdir -p "$GAME_DIR/mods"
    cp mc-agent-interface-mod/dist/mc-agent-interface-*.jar "$GAME_DIR/mods/"
    ```

构建成功会输出 `built: ...jar`。目标 `mods/` 中只保留**一个版本**的 interface mod，
并放入匹配的 **Fabric API** jar。启动 Fabric 服务端，或启动客户端并**进入单机世界**；
停留在主菜单不会启动集成服务端。

**成功标志：** `<GameDir>/mc-agent-server/port.txt` 出现，里面是一个端口号。
第 3 步的 `--server-dir` 就填这里的 `GameDir`，单机世界也一样。
如果只是加入别人的多人服务器，这条服务端视角路径需要那个服务器也安装 mod。

## 2. 安装 Toolkit { #2-install-the-toolkit }

继续在同一个工作目录运行：

=== "Windows · PowerShell"

    ```powershell
    git clone https://github.com/guajun/mc-agent-bridge
    python -m venv .venv
    .venv/Scripts/python -m pip install -e "mc-agent-bridge[mcp]"
    .venv/Scripts/mc-bridge --help
    ```

=== "macOS / Linux"

    ```bash
    git clone https://github.com/guajun/mc-agent-bridge
    python3 -m venv .venv
    .venv/bin/python -m pip install -e "mc-agent-bridge[mcp]"
    .venv/bin/mc-bridge --help
    ```

**成功标志：** 最后一条命令显示 CLI 帮助。`[mcp]` 会安装智能体应用所需的 MCP 适配器。
标准用法无需安装 `mc-agent-loop`。

## 3. 检查连接 { #3-check-the-connection }

**终端 A：启动常驻进程，保持运行。** `--server-dir` 填第 1 步的游戏目录，
也就是 `mc-agent-server/` 的上一级：

=== "Windows · PowerShell"

    ```powershell
    .venv/Scripts/mc-bridge run --server-dir "C:/Minecraft/instances/my-world"
    ```

=== "macOS / Linux"

    ```bash
    .venv/bin/mc-bridge run --server-dir "/path/to/minecraft/instances/my-world"
    ```

这是长期运行的进程，正常工作时不会返回命令提示符。游戏世界也要保持打开。

**终端 B：在同一个工作目录另开终端**，依次运行：

=== "Windows · PowerShell"

    ```powershell
    .venv/Scripts/mc-bridge call status
    .venv/Scripts/mc-bridge call capabilities
    .venv/Scripts/mc-bridge call state
    ```

=== "macOS / Linux"

    ```bash
    .venv/bin/mc-bridge call status
    .venv/bin/mc-bridge call capabilities
    .venv/bin/mc-bridge call state
    ```

| 检查 | 成功标志 |
| --- | --- |
| `status` | JSON 结果中显示 `connected: true` 和 `vantage: server`。 |
| `capabilities` | 返回当前 mod 支持的操作列表，以此确定可用工具。 |
| `state` | 返回服务端世界数据，没有连接错误。 |

**连接完成。** 以上检查只读取连接与世界信息。
可以继续使用 CLI，也可以接入智能体。

## 4. 接入智能体 { #4-connect-your-agent }

在智能体应用的 **MCP 服务设置**中，添加一个本地 **stdio** 服务：

| 字段 | 填写内容 |
| --- | --- |
| 名称 | `minecraft`，或你容易识别的名称 |
| 命令 · Windows | 工作目录下 `.venv/Scripts/mc-bridge.exe` 的绝对路径 |
| 命令 · macOS/Linux | 工作目录下 `.venv/bin/mc-bridge` 的绝对路径 |
| 参数 | `mcp` |

如果客户端支持 `mcpServers` JSON 配置，可使用下面的例子，并替换命令路径。
其它客户端使用各自的设置格式，但启动命令和参数相同。

```json
{
  "mcpServers": {
    "minecraft": {
      "command": "C:/Minecraft/toolkit-workspace/.venv/Scripts/mc-bridge.exe",
      "args": ["mcp"]
    }
  }
}
```

macOS/Linux 的命令路径例如 `/path/to/toolkit-workspace/.venv/bin/mc-bridge`。
务必使用**绝对路径**，因为智能体可能从其它目录启动。
保持终端 A 运行：MCP 适配器连接已有常驻进程，不会替你启动它。

重新加载客户端的 MCP 服务，或新开一个智能体会话，然后发送：

> 使用 mc_status 检查 Minecraft 连接，用 mc_capabilities 查询工具，
> 再读取 mc_state，概括当前世界状态和在线玩家。不要修改世界。

**成功标志：** 智能体调用工具，并根据实际世界数据给出概括。
不支持 MCP 的智能体也可以运行第 3 步的 CLI 命令。
还可以[安装 Toolkit Skill](toolkit-skill.md)，教智能体按能力发现、玩家定位的顺序使用工具。

## 遇到问题？ { #something-didnt-work }

| 现象 | 先检查这里 |
| --- | --- |
| 构建提示 `missing version json` 或 `missing client jar` | 核对第 1 步的资源根目录和实际版本文件夹名；两个文件都要存在。 |
| 找不到 Fabric API 或 `javac` | 核对第 1 步说明的 Fabric API 位置和 JDK 路径。 |
| 没有 `mc-agent-server/port.txt` | 确认 mod 和 Fabric API 已加载；进入世界或启动独立服务端。 |
| `status` 显示 `connected: false` | 查看终端 A 的错误和 `--server-dir`，应指向运行实例，不是 Toolkit 仓库或 `saves/`。 |
| 调用 `status` 时连接被拒绝 | 启动终端 A 的常驻进程，并保持运行。 |
| CLI 正常，智能体却没有 `mc_*` 工具 | 检查 MCP 可执行文件的绝对路径、`mcp` 参数以及是否安装了 `[mcp]`，然后重新加载配置。 |
| 游戏聊天里没有智能体回复 | 本指南从智能体应用主动发起请求；聊天触发需要另行配置[无人值守模式](hermes-unattended.md)，该模式仍有投递联调问题。 |

[更多疑难排查](troubleshooting.md) · [安装参考](install.md)

## 接下来

- [工具参考](tools.md)：更多 CLI 调用与 MCP 操作。
- [架构说明](concepts.md)：部署方案与组件职责。
- [进阶指南](advanced.md)：事件触发、无人值守、世界分叉与实验。

使用结束后，在**终端 A 按 Ctrl+C** 停止常驻进程。
下次只需打开世界并重复第 3 步，无需重新安装或注册 MCP。
