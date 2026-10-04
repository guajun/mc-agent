# 安装与首次运行

先在终端读取 Minecraft 世界状态，再把同样的工具交给智能体。终端检查不需要模型
账号。单机世界或专用服务器都可以：游戏服务器只需要 mod，二进制安装在你运行
智能体的环境。

## 开始之前

这里接通命令和游戏状态／实体采集。完整世界分叉是 Minecraft 26.2 的设计目标，
尚未实现为正式 Go `fork`／`restore` 命令。实验中使用保存前，先了解简短的
[研究边界](concepts.md#world-forks-and-experiment-boundaries)。

| 需要 | 检查 |
| --- | --- |
| 游戏：Minecraft 26.2、Fabric Loader 0.19.5+、Fabric API 0.161.0 | 该配置能正常启动并进入世界。 |
| Java 25 | Fabric 服务端/客户端配置可正常启动。 |
| 二进制支持的平台：Windows amd64、Linux glibc amd64 或 macOS arm64 | 见[安装参考](install.md)。 |
| 智能体应用（最后一步） | 任何能执行命令的 Harness（Codex、Claude Code、Hermes、shell）。 |

运行发行版 Toolkit **不需要** Python 和 Go。没有 MCP 服务器，也没有需要注册的
MCP 设置。

## 1. 安装游戏 mod { #1-install-the-game-mod }

从 [mod releases](https://github.com/guajun/mc-agent-interface-mod/releases) 下载
最新的 `mc-agent-interface-0.8.0.jar` 和 `checksums.txt`，校验 SHA-256，然后把
jar 与 Fabric API 一起放入实例的 `mods/`：

=== "Windows · PowerShell"

    ```powershell
    # 校验下载（与发布的 checksums.txt 对比）
    (Get-FileHash -Algorithm SHA256 .\mc-agent-interface-0.8.0.jar).Hash.ToLower()
    Copy-Item .\mc-agent-interface-0.8.0.jar "C:/Minecraft/instances/my-world/mods/"
    ```

=== "macOS / Linux"

    ```bash
    # Linux
    sha256sum mc-agent-interface-0.8.0.jar   # 与 checksums.txt 对比
    # macOS（默认没有 GNU sha256sum）
    shasum -a 256 mc-agent-interface-0.8.0.jar
    cp mc-agent-interface-0.8.0.jar /path/to/instance/mods/
    ```

如果对应版本还没有发行包，请[自行构建 mod](mod-building.md)，并确保 `mods/` 中
只有一个 interface jar，且 Fabric API 与之匹配。

mod 有两个适配器，取决于游戏形态：

| 适配器 | 游戏监听位置 | 启用方式 |
| --- | --- | --- |
| **控制传输**（推荐） | 实际 Minecraft 游戏端口，与玩家共用 | 在 JVM 参数中加入 `-Dmcagent.control=true` |
| **遗留 loopback 适配器** | 独立 loopback 端口 | 默认启用；用于显式单机本地路径 |

专用服务器在 `user_jvm_args.txt`（Fabric）中加入该参数后启动；客户端则加入实例的
JVM 参数。

**成功标志：** 实例目录出现 `mc-agent-server/control/fingerprint.txt`，其中是一行
`sha256:...`；服务器控制台也能响应 `/mcagent control` 命令。

### 凭证

控制凭证由服务器控制台的**所有者**（权限等级 4）签发：

```
/mcagent control status
/mcagent control token add <label> read|write|read+write [ttlSeconds]
/mcagent control token list
/mcagent control token revoke <id>
```

密钥只显示一次，格式为 `mca1.<id>.<base64url>`。观察类操作（state、entities、
player/chat 上下文、snapshots、events）需要 `read` 凭证；`write` 额外允许
`command`、`mark` 和 `snapshot`。只有 write 的凭证收不到事件与回放。如果服务器还没有可用凭证，mod 会
签发一个 bootstrap `read+write` 凭证并写入
`<gameDir>/mc-agent-server/control/bootstrap-token.txt`（仅属主可读）。把它交给
daemon（标准输入）并在签发每个 daemon 专属凭证后吊销 bootstrap 凭证。`read` 凭证
只能观察；`write` 额外允许 `command`、`mark` 和 `snapshot`。聊天身份永远不是授权，
权限来自凭证。

## 2. 安装二进制与 Skill { #2-install-the-toolkit }

=== "Windows amd64 · PowerShell"

    ```powershell
    iwr -useb https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.ps1 -OutFile install.ps1
    ./install.ps1 -Version 0.5.0 -SkillHarness codex
    ```

=== "Linux amd64 · shell"

    ```bash
    curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
        | sh -s -- --version 0.5.0 --skill-harness codex
    ```

=== "macOS arm64 · shell"

    ```bash
    curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
        | sh -s -- --version 0.5.0 --skill-harness codex
    ```

把 skill 目标换成你的 Harness（`claude-code`、`universal`、`hermes`），或用
`--skill-dir DIR` 指定自定义目录；`--no-skill` 跳过安装。安装器会校验发布校验和，
除非指定 `--update-skill` 否则不会覆盖已有 Skill 目录，并打印后续 `version` 和
`doctor` 命令。除非传入 `--add-to-path`，安装器不会修改 PATH；当前 shell 可运行
`export PATH="$HOME/.mc-agent/bin:$PATH"`（Windows：
`$env:Path = "$env:LOCALAPPDATA\mc-agent\bin;$env:Path"`）。映射表和手动方式见
[安装 Toolkit Skill](toolkit-skill.md)。

**成功标志：** `mc-agent version` 输出
`mc-agent 0.5.0 (control protocol 1, mod >= 0.8.0)`。

脚本应运行 `mc-agent --pretty version`：它返回包含 `version`、`controlProtocol`、
`modMinVersion` 及运行时信息的 JSON。有限的数据命令在 stdout 输出一个 JSON 值；
`version` 默认输出文本，`help` 在 stderr 输出文本，`events --follow` 持续输出 JSON 值，
`daemon run` 在前台运行，不返回最终结果。错误在 stderr 输出 JSON，并以非零退出码结束；
接受结果前应检查退出码。
用法错误还可能在 stderr 输出帮助文本；不提供命令时只输出帮助并以退出码 2 结束。
不要假定整个 stderr 都是一个 JSON 值。

## 3. 检查连接 { #3-check-the-connection }

### 专用服务器或 LAN 世界

用 mod 指纹注册目标。地址是**实际游戏端口**；LAN 世界的地址是聊天中公布的实际
端口，而不是假定的 25565。

=== "Windows · PowerShell"

    ```powershell
    $fp = (Get-Content "C:/Minecraft/server/mc-agent-server/control/fingerprint.txt").Trim()
    Get-Content "C:/Minecraft/server/mc-agent-server/control/bootstrap-token.txt" |
        mc-agent target add dedicated --transport remote --address 127.0.0.1:25565 `
            --pin $fp --token-stdin --default
    mc-agent daemon start
    mc-agent doctor
    mc-agent state
    ```

=== "macOS / Linux"

    ```bash
    fp="$(cat /srv/minecraft/mc-agent-server/control/fingerprint.txt)"
    cat /srv/minecraft/mc-agent-server/control/bootstrap-token.txt |
        mc-agent target add dedicated --transport remote --address 127.0.0.1:25565 \
            --pin "$fp" --token-stdin --default
    mc-agent daemon start
    mc-agent doctor
    mc-agent state
    ```

远程服务器把 `127.0.0.1` 换成服务器主机，并使用为该 daemon 签发的凭证。不要把
凭证写在会被记录的命令行里；`--token-stdin`、`--token-env`、`--token-file` 可避免
泄露。

| 检查 | 成功表现 |
| --- | --- |
| `mc-agent doctor` | `version`、`targets`、`credential`、`daemon`、`target:dedicated` 均为绿色 |
| `mc-agent capabilities` | 连接 mod 支持与不支持的操作列表 |
| `mc-agent state` | 返回世界数据和在线玩家，而不是连接错误 |

**连接成功。** 以上只检查连接与世界。

### 未开放 LAN 的单机

使用显式的遗留 loopback 适配器，它读取 mod 服务端适配器绑定的端口：

```bash
mc-agent target add singleplayer --transport legacy \
    --server-dir "/path/to/instance" --vantage server --default
mc-agent daemon start
mc-agent doctor
mc-agent state
```

把单机世界开放到 LAN 会切换到推荐路径：控制传输监听实际公布端口，用 LAN 房主的
指纹按上面的方式创建 `remote` 目标。

## 4. 接入智能体 { #4-connect-your-agent }

Harness 调用同一个 `mc-agent` 命令；没有 MCP 注册。把 Toolkit Skill 和首个任务
交给它：

> 用 `mc-agent version`、`doctor`、`capabilities` 和 `state` 检查 Minecraft
> 连接，总结世界与在线玩家。不要改变世界。

**成功标志：** 智能体执行命令并总结真实世界数据。[Toolkit Skill](toolkit-skill.md)
教会它发现、身份、上下文、游标和未知写结果的完整流程。

## 出问题了？ { #something-didnt-work }

| 症状 | 先检查 |
| --- | --- |
| 没有 `mc-agent-server/control/fingerprint.txt` | 加入 `-Dmcagent.control=true` 并重启；确认 mod 与 Fabric API 已加载 |
| `/mcagent` 提示控制传输关闭 | 该实例的 JVM 参数缺少开关 |
| `doctor` 报 `daemon_not_running` | `mc-agent daemon start`；查看 `mc-agent daemon status` 和 daemon 日志 |
| `connection_failed` 或 pin/认证错误 | 重新读取 `fingerprint.txt`；重新签发/复制凭证；校验始终开启 |
| `target_unknown` | `mc-agent target list`；传 `--target NAME` 或设置 `--default` |
| 写操作超时且 `resultUnknown` | 不要重放；查看 `mc-agent request-status <id>` 和 `requests` 账本 |
| 智能体能回复聊天但游戏内没有消息 | daemon 从不回写回复；在 Harness 中配置投递路径（见 [Hermes 无人值守](hermes-unattended.md)） |

[更多故障排查](troubleshooting.md) · [安装参考](install.md)

## 下一步

- [工具参考](tools.md) —— CLI 命令面与能力模型。
- [架构](concepts.md) —— 组件、部署选择与边界。
- [无人值守 Hermes](hermes-unattended.md) —— 签名 webhook、在线要求与回复投递。

用 `mc-agent daemon stop` 停止 daemon。下次打开世界后重复第 3 步；安装和 Skill
只需做一次。
