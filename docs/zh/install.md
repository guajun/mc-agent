# 安装参考

[English](https://guajun.github.io/mc-agent/install/)

**第一次安装？请从[安装与首次运行](getting-started.md)开始。**
那里提供各系统的命令、mod 复制、连接检查和 MCP 配置。
本页记录部署设置、升级、发现规则和卸载方式。

## 要求与部署位置

| 组件 | 要求 | 运行位置 |
| --- | --- | --- |
| Fabric mod | Minecraft 26.2、Fabric Loader 0.19+、Fabric API、Java 25 | 独立服务端或单机集成服务端 |
| Toolkit（`mc-agent-bridge`） | Python 3.11+ | 与 mod endpoint、Harness 同一台机器 |
| Harness | 支持 MCP，或能运行 CLI | 与 Toolkit 同一台机器 |

mod socket 与 Toolkit API 只监听 loopback 是部署假设，不是需要绕开的限制。
不要把任何游戏控制端口暴露到互联网。

## Fabric mod

请使用[mod 构建与安装步骤](getting-started.md#1-install-the-game-mod)。
包含 `versions/` 和 `libraries/` 的构建资源根目录可能与实际运行实例目录不同，
首次运行指南解释了这两个路径。

把构建出的 jar 和 Fabric API 放进服务端或客户端实例的 **mods/**。同一个 jar
同时包含客户端和服务端入口。Toolkit 默认使用服务端入口，包括单机世界里的
集成服务端。

| 属性 | 默认值 | 用途 |
| --- | --- | --- |
| **mcagent.serverDir** | **mc-agent-server** | 服务端视角状态、快照与端口文件 |
| **mcagent.serverPort** | **25581** | 首选 loopback 端口 |
| **mcagent.contextCacheSize** | **256** | 聊天上下文包容量 |
| **mcagent.contextCacheTtlSeconds** | **300** | 上下文包有效期 |
| **mcagent.dir** / **mcagent.port** | **<gameDir>/mc-agent** / **25580** | 旧客户端视角 endpoint |

升级时替换 jar，并确保只留一个版本。卸载时删除 jar；只有不再需要事件与快照
时才一起删除数据目录。

## Toolkit（`mc-agent-bridge`）

按 Windows 或 macOS/Linux 使用 [Toolkit 安装命令](getting-started.md#2-install-the-toolkit)。

只需要守护进程、CLI 或 JSON-lines API 时可不装 MCP extra。可编辑安装在
checkout 中 pull 即升级；删除虚拟环境即可卸载。

以下命令假设虚拟环境已激活。也可以像首次运行指南一样，从工作目录使用完整的
`.venv/Scripts/mc-bridge`（Windows）或 `.venv/bin/mc-bridge`（macOS/Linux）路径。
验证可执行文件：

~~~powershell
mc-bridge --help
mc-bridge discover --server-dir "C:/minecraft/server"
~~~

## 服务端视角发现

默认 **mc-bridge run** 按以下顺序解析：

1. 显式 **--mod-port**；
2. **--port-file** 或 **MC_AGENT_PORT_FILE**；
3. **<--server-dir | MC_AGENT_SERVER_DIR | 当前目录>/mc-agent-server/port.txt**；
4. 显式指定 server directory 时的 **<--server-dir>/port.txt**。

如果都找不到，守护进程会报告错误并继续等待。它不会猜服务端端口，也不会
静默连接客户端视角。

~~~powershell
mc-bridge run --server-dir "C:/minecraft/server"
~~~

保持常驻进程运行，在使用同一环境的第二个终端中检查：

~~~powershell
mc-bridge call status
mc-bridge call capabilities
~~~

客户端视角是明确的旧式 opt-in：

~~~powershell
mc-bridge run --vantage client --port-file "C:/minecraft/client/mc-agent/port.txt"
~~~

## Harness 集成

使用 MCP 时，参考 [MCP 设置与配置示例](getting-started.md#4-connect-your-agent)，
让 Harness 通过绝对路径拉起 **mc-bridge mcp**。此适配器连接长期运行的
守护进程，不能替代守护进程。不支持 MCP 的 Harness 使用 **mc-bridge call**
或 loopback JSON-lines API。

Codex、Claude Code 或其它用户主动型 Harness 不需要安装 **mc-agent-loop**。
loop 只是 echo 测试和临时 Hermes HTTP 路径的可选兼容包。

## 验证

| 检查 | 期望结果 |
| --- | --- |
| 服务端日志 | server vantage armed/listening |
| **mc-agent-server/port.txt** | 含实际 loopback 端口 |
| **mc-bridge discover** | 报告 **vantage: server** 及来源 |
| **mc-bridge call status** | 守护进程与 mod 已连接 |
| **mc-bridge call capabilities** | 经过筛选的服务端工具面 |
| **mc-bridge call player** | 结构化 found/not-found 玩家结果 |

## 卸载

| 组件 | 删除内容 |
| --- | --- |
| Fabric mod | **mods/** 中的 jar；可选删除 **mc-agent-server/** |
| Toolkit | 虚拟环境与 checkout |
| Harness 配置 | MCP server 项与 webhook secret/route |
| 实验室实例 | 只删除 **labs/** 下明确选定的目录 |
