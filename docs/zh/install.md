# 安装参考

[English](https://guajun.github.io/mc-agent/install/)

**首次安装请先看[安装与首次运行](getting-started.md)。** 那里有各系统命令、mod
启用、凭证签发和首次连接。本页覆盖支持矩阵、部署位置、升级、卸载与移除。

## 支持平台

发行版二进制只在下述平台由发布工作流构建、打包并做安装验证：

| 平台 | 归档 |
| --- | --- |
| Windows 10/11 amd64 | `mc-agent-<version>-windows-amd64.zip` |
| Linux glibc amd64 | `mc-agent-<version>-linux-amd64.tar.gz` |
| macOS 14+ arm64（Apple 芯片） | `mc-agent-<version>-darwin-arm64.tar.gz` |

Windows arm64、Linux arm64、Linux musl/Alpine、macOS amd64 及其他 OS/CPU 组合
不在测试和承诺范围内；安装器会明确拒绝。mod 是纯 Java，在 Minecraft 26.2 +
Fabric Loader 0.19.5 + Fabric API 0.161.0 + Java 25 上运行。

组件位置：

| 组件 | 要求 | 运行位置 |
| --- | --- | --- |
| Fabric mod | Minecraft 26.2、Fabric Loader 0.19.5+、Fabric API 0.161.0、Java 25 | 专用服务器、LAN 房主或单机集成服务器 |
| `mc-agent` 二进制 | 上述平台之一；运行不需要 Python、Go 或 MCP | Harness 的实际执行环境：本机、WSL、容器或远程主机 |
| daemon | 同一个二进制，可选的长驻进程 | Harness 旁或服务器旁 |

mod 的控制监听在游戏自身的 TCP 端口并带认证；保持正常防火墙卫生即可，不需要额外
公网控制端口。daemon 的本地 IPC 只在 loopback。

## Fabric mod

在实例 JVM 参数中加入 `-Dmcagent.control=true`（Fabric 服务端的
`user_jvm_args.txt`，或客户端 JVM 参数）以启用同端口控制传输。遗留 loopback
适配器无需开关，仍然可用。

| 属性 | 默认值 | 用途 |
| --- | --- | --- |
| `mcagent.control` | `false` | 在游戏端口启用带认证的控制传输 |
| `mcagent.serverDir` | `mc-agent-server` | 服务端适配器状态、快照与端口文件 |
| `mcagent.serverPort` | `25581` | 遗留适配器尝试的第一个 loopback 端口 |
| `mcagent.contextCacheSize` | `256` | 聊天上下文 bundle 上限 |
| `mcagent.contextCacheTtlSeconds` | `300` | bundle 生命周期 |
| `mcagent.dir` / `mcagent.port` | `<gameDir>/mc-agent` / `25580` | 遗留客户端视角端点 |

升级时替换 jar，并只保留一个 interface 版本与匹配的 Fabric API。卸载时删除 jar；
只有不再需要其事件与快照时才删除 `mc-agent-server/`。

## 二进制

使用固定版本安装器（详见 [mc-agent-bridge 安装文档](https://github.com/guajun/mc-agent-bridge/blob/main/docs/install.md)）：

```bash
# Linux amd64 / macOS arm64
curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
    | sh -s -- --version 0.5.0 --skill-harness codex
```

```powershell
# Windows amd64
iwr -useb https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.ps1 -OutFile install.ps1
./install.ps1 -Version 0.5.0 -SkillHarness codex
```

默认位置：`~/.mc-agent/bin`（Linux/macOS）、`%LOCALAPPDATA%\mc-agent\bin`
（Windows）。状态目录为 `$XDG_CONFIG_HOME/mc-agent`、
`~/Library/Application Support/mc-agent` 或 `%AppData%\mc-agent`；
`MC_AGENT_HOME` 与 `--home DIR` 可覆盖。

升级时用新版本再次运行安装器；旧二进制保留为 `mc-agent.previous`，下载或校验失败
不会改变当前安装。卸载用 `--uninstall`（`-Uninstall`），需要时再加
`--remove-skill` / `--purge-state`（`-RemoveSkill` / `-PurgeState`）。

## 目标与传输选择

| 场景 | 目标 |
| --- | --- |
| 启用 `-Dmcagent.control=true` 的专用服务器 | `--transport remote --address HOST:<游戏端口> --pin sha256:...` |
| LAN 世界 | `--transport remote --address HOST:<公布端口> --pin sha256:...`（在房主上读取指纹） |
| 未开 LAN 的单机 | `--transport legacy --server-dir <实例目录> --vantage server` |
| 旧 mod / 显式客户端视角 | `--transport legacy --vantage client [--port-file FILE]` |

远程校验始终开启（`--pin` 或 `--ca`），没有 trust-all 模式。凭证保存在状态目录，
只显示来源（`store`/`env`/`file`）。

## 验证

| 检查 | 预期结果 |
| --- | --- |
| 服务端/客户端日志 | 启用时控制传输就绪；遗留适配器监听 |
| `mc-agent-server/control/fingerprint.txt` | 该实例的 `sha256:...` |
| `mc-agent doctor` | 版本、home、目标、daemon 及每目标 TLS/能力检查 |
| `mc-agent capabilities` | 连接 mod 的支持/不支持操作 |
| `mc-agent state` | 返回世界数据而非连接错误 |

## Harness 集成

让 Harness 调用 `mc-agent` 可执行文件。没有需要注册的 MCP 服务器。安装
[Toolkit Skill](toolkit-skill.md)，让智能体了解发现、身份、游标与未知写结果流程。
无法加载 Skill 的 Harness 也可以直接调用 CLI；命令面见[工具](tools.md)。

用户主动型 Harness 不需要 `mc-agent-loop`。它仍是可选的兼容监听器，用于其 echo
测试与临时 Hermes HTTP 路径，状态为显式遗留。

## 移除

| 组件 | 移除方式 |
| --- | --- |
| Fabric mod | 从 `mods/` 删除 jar；可选删除 `mc-agent-server/` |
| 二进制 | `install.sh --uninstall --remove-skill --purge-state`（或 PowerShell 等价命令） |
| Harness 集成 | 无需注销；如安装过 Skill 副本可删除 |
| lab 实例 | 只删除 `labs/` 下选定的目录 |
