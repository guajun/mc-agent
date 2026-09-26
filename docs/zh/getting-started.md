# 快速开始

[English](https://guajun.github.io/mc-agent/getting-started/)

本指南先启动服务端视角的 Minecraft Agent Toolkit，用 CLI 验证，再通过 MCP
接入 Harness。

## 要求

* Minecraft 26.2、Fabric Loader 0.19+ 与 Fabric API
* Java 25
* Python 3.11+
* 一台加载 interface mod 的独立 Fabric 服务端，或加载它的单机集成服务端

第一次检查不需要 Harness。Codex、Claude Code、Hermes 和其它调用者都使用
同一套 Toolkit，不需要项目维护专用 backend。

## 1. 编译并安装 Fabric mod

~~~powershell
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py --minecraft-dir <实例> --version 26.2-Fabric --jdk <jdk25>
~~~

把 **dist/** 中的 jar 与 Fabric API 一起放进实例的 **mods/**，再启动服务端或
单机世界。服务端入口把实际端口写入
**<server-dir>/mc-agent-server/port.txt**。

## 2. 安装 Toolkit

~~~powershell
git clone https://github.com/guajun/mc-agent-bridge
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]"
~~~

只有使用 MCP 的 Harness 才需要 MCP extra；守护进程、CLI 和 JSON-lines API
本身不需要它。

## 3. 启动并检查 Toolkit

从 server directory 启动守护进程，或显式指定目录：

~~~powershell
.venv/Scripts/mc-bridge run
.venv/Scripts/mc-bridge run --server-dir "C:/path/to/server"
~~~

另开 shell：

~~~powershell
.venv/Scripts/mc-bridge call status
.venv/Scripts/mc-bridge call capabilities
.venv/Scripts/mc-bridge call state
~~~

**status** 解释发现与连接状态；**capabilities** 才是当前 mod 支持哪些操作的
权威来源。默认发现绝不会猜端口，也不会回退到客户端视角。

常用服务端视角调用：

~~~powershell
.venv/Scripts/mc-bridge call player '{"player":"<uuid-or-name>"}'
.venv/Scripts/mc-bridge call entities '{"radius":32}'
.venv/Scripts/mc-bridge call command_output '{"command":"list"}'
.venv/Scripts/mc-bridge call events '{"since":0,"limit":20}'
~~~

持久身份优先使用 UUID，名字只是便捷方式。返回的玩家身份是上下文，不是执行
特权命令的授权。

## 4. 接入用户主动型 Harness

让 Harness 拉起：

~~~text
C:/path/to/.venv/Scripts/mc-bridge.exe mcp
~~~

MCP 适配器是已经运行的守护进程的客户端。新 Harness 会话应先调用
**mc_status** 和 **mc_capabilities**，再使用 **mc_player**、**mc_state**、
**mc_entities**、**mc_command_output** 或其它已公布工具。

游戏外用户请求没有聊天快照。先用 **mc_player** 确认目标玩家，再只读取任务
需要的额外实时上下文。

## 5. 理解游戏内事件

服务端聊天事件可以包含 **context_id** 与精简上下文摘要。应在过期前读取完整
有界上下文包：

~~~powershell
.venv/Scripts/mc-bridge call context '{"id":"<context_id>"}'
~~~

默认缓存 256 个包，保留 300 秒。过期或未知 ID 会返回结构化状态；新的世界
状态始终可通过普通 Toolkit 调用获得。

无人值守模式下，**mc-bridge forward** 可以把选定事件发给一个签名 webhook。
接收方/Harness 路由、回复投递和授权是单独配置。当前实现状态见
[Hermes 与无人值守运行](hermes-setup.md)。

## 6. 兼容路径

客户端视角必须显式选择：

~~~powershell
mc-bridge run --vantage client --port-file "C:/path/to/mc-agent/port.txt"
~~~

只有屏幕状态、打开本地存档或加入服务器等客户端专属操作才需要它。旧的
**mc-agent-loop** 仍可用于兼容和测试，默认触发词是 **@agent**；用户主动型
Harness 不需要它，其 Hermes 模型 backend 也计划移除。

## 下一步

* [安装](install.md)：发现规则、升级和安全边界
* [架构与术语](concepts.md)：组件职责
* [Toolkit 工具](tools.md)：当前 CLI/MCP 工具面
* [分叉一个活的世界](protocol-snapshot.md)：快照与还原
