# 安装说明

装什么、按什么顺序装、以及怎么确认装对了。[快速开始](getting-started.md) 是那条"走一遍"的路线；这一页是出问题时会回来查的参考。

这里除了一个 JDK，不需要任何编译工具链；也不会往系统里装东西。

## 需要什么

| 组件 | 用来做什么 | 放在哪 |
| --- | --- | --- |
| **Minecraft 26.2** + **Fabric Loader 0.19+** + **Fabric API** | 一切 | 你的实例 |
| **Java 25** | 编译 mod、运行实验室服务器 | `--jdk` 指向的 JDK；游戏本身用自带运行时即可 |
| **Python 3.11+** | bridge、loop、工具 | 仓库旁边的虚拟环境 |
| **接口 mod jar** | 一切 | `<实例>/mods/` |
| **一个智能体运行时** | 智能体的"大脑"——[Hermes](hermes-setup.md)，或任何会说 MCP/HTTP 的东西 | 它自己的目录 |
| **一台 Fabric 服务器**（可选） | 隔离实验室轨道 | `labs/<名字>/`，由 `tools/lab_server.py` 供给 |

## 1. Minecraft 与 Fabric

准备一个 26.2 的 Fabric 实例，`mods/` 里放 **Fabric API**。有两个细节后面会用到：

* mod 把状态写进实例的**游戏目录**（`<gameDir>/mc-agent/`）——开了版本隔离时它指的是版本文件夹，不是 `.minecraft`；
* 编译 mod 会读取该实例的 `libraries/` 和 `.fabric/processedMods/`，所以**先启动一次游戏**再编译。

## 2. 接口 mod

### 它在哪里运行

一个 jar、两个入口，Fabric 只加载与环境匹配的那个：

| 入口 | 运行在 | 提供 |
| --- | --- | --- |
| `client` | 任意客户端 | 那个客户端能看到和能做的事：界面、打开存档、把世界开放到局域网 |
| `main` | 任意服务端——**包括单机世界里的集成服务端** | 权威状态、控制台命令、快照 |

**单机会同时拥有两者**，在同一个进程里：客户端 vantage 监听 `mcagent.port`（25580），服务端 vantage 监听 `mcagent.serverPort`（25581）。这也是"不用专门开服务器就能做分叉"的原因——你边玩就能对一个权威世界取样。两者的数据写在不同的地方：`<gameDir>/mc-agent/` 与 `mc-agent-server/`（或用 `-Dmcagent.dir` / `-Dmcagent.serverDir` 指定）。

同一个进程里两者并存时有两件事要知道：`tick freeze` 会把你正在玩的世界冻住；你在服务端 vantage 里跑的重活，会和你客户端的帧预算抢时间。

```bash
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py \
    --minecraft-dir "C:/Users/me/AppData/Roaming/.minecraft" \
    --version 26.2-Fabric \
    --jdk "C:/Program Files/Java/jdk-25"
```

产物是 `dist/mc-agent-interface-<版本>.jar`。把它和 Fabric API 一起放进 `<实例>/mods/`，启动游戏。日志里出现这些就算成功：

```
[mc-agent-interface] initialized, dir=<gameDir>/mc-agent basePort=25580
[mc-agent-interface] server vantage armed, dir=mc-agent-server basePort=25581
[mc-agent-interface] listening on 127.0.0.1:25580
```

| 要做的事 | 怎么做 |
| --- | --- |
| **升级** | 替换 jar；**一次只留一个版本**，否则 Fabric 会报重复 mod id |
| **卸载** | 删掉 jar。mod 写过的东西都在 `<gameDir>/mc-agent/`（服务端 vantage 是 `mc-agent-server/`），想要干净一起删 |
| **挪数据目录** | `-Dmcagent.dir=<路径>`（客户端）、`-Dmcagent.serverDir=<路径>`（服务端 vantage） |
| **固定端口** | `-Dmcagent.port=25580`（客户端）、`-Dmcagent.serverPort=25581`（服务端 vantage）；不指定时 mod 会占用下一个空闲端口并写进 `port.txt` |

## 3. Python 侧

bridge 和 loop 是两个包，loop 依赖 bridge，所以装进同一个环境：

```bash
git clone https://github.com/guajun/mc-agent-bridge
git clone https://github.com/guajun/mc-agent-loop
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]" -e mc-agent-loop   # Windows
.venv/bin/pip     install -e "mc-agent-bridge[mcp]" -e mc-agent-loop   # POSIX
```

`[mcp]` 会拉 Model Context Protocol SDK；不装也能用守护进程、CLI 和本地 API。验证：

```bash
.venv/Scripts/mc-bridge --help
.venv/Scripts/mc-agent-loop backends
```

两个都是**可编辑安装**：在这两个仓库里 `git pull` 就等于升级，不用重装。卸载就是删掉虚拟环境。

## 4. 智能体运行时

loop 对的是一个 OpenAI 兼容端点，所以任何提供这种端点的东西都能用。本项目是对着 Hermes 开发的，[用 Hermes 运行智能体](hermes-setup.md) 从头到尾讲了它：安装、模型、API server、MCP 注册。

把地址和 key 放进 loop 会读的文件里，而不是留在 shell 历史里：

```
# .env   （已 gitignore）
HERMES_API_BASE=http://127.0.0.1:8642
HERMES_MODEL=hermes-agent
HERMES_API_KEY=<API server 的 key>
```

## 5. 可选：实验室服务器

`tools/lab_server.py` 会在 `labs/<名字>/` 下供给一台无头 Fabric 服务器。第一次运行会下载 Fabric 服务端启动器（它再去下载 vanilla 服务端）、Fabric API 和 Carpet，并缓存在 `labs/_cache/`——所以离线机器要先把这个缓存拷过去。

```bash
python tools/lab_server.py provision --name lab-01 --void --fabric-api --carpet --java <java25>
python tools/lab_server.py start --name lab-01 --wait 300
python tools/lab_server.py exec --name lab-01 "list"
python tools/lab_server.py stop --name lab-01
```

它同样需要 Java 25，并把 RCON 密码写进 `labs/<名字>/rcon.json`——那就是工具使用的控制台。实验室不会碰你的游戏实例。

## 6. 装对了吗？

按顺序，每一步都多证明一点：

| 检查 | 期望 |
| --- | --- |
| 游戏日志出现 `listening on 127.0.0.1:...` | mod 起来了 |
| `<gameDir>/mc-agent/port.txt` 存在 | mod 能写状态 |
| 游戏聊天里 `/mcagent status` | 版本、端口、已连接的 bridge |
| `mc-bridge run` 后 `mc-bridge call state` | `inWorld`、坐标 |
| `mc-agent-loop run --backend echo --trigger @codex` 然后在聊天打 `@codex 你好` | 聊天里出现回声 |
| `python tools/smoke_offline.py` | 整条链，不需要游戏 |

出问题时，[疑难排查](troubleshooting.md) 按"通常先咬人的顺序"列了那些坑。

## 7. 值得知道的文件与端口

| 路径或端口 | 是什么 |
| --- | --- |
| `<gameDir>/mc-agent/port.txt` | mod 实际拿到的端口 |
| `<gameDir>/mc-agent/events.jsonl` | 事件历史（聊天、游戏消息、标记、采样生命周期） |
| `<gameDir>/mc-agent/samples.jsonl` | `record_start` 的采样结果 |
| `<gameDir>/mc-agent/snapshots/<名字>/` | 快照：`entities.jsonl` + `meta.json` |
| `mc-agent-server/` | 同样的东西，服务端 vantage 用（实验室会写进它自己的目录） |
| `127.0.0.1:8765` | bridge 的本地 API（默认） |
| `127.0.0.1:25580` | mod，客户端 vantage |
| `127.0.0.1:25581` | mod，服务端 vantage |
| `.env` | loop 用的 API key；已 gitignore |

全部只监听 loopback。请把这台机器当作可信环境：接口能以玩家身份执行命令，实验室控制台能以管理员身份执行命令。

## 8. 卸载

| 组件 | 删掉什么 |
| --- | --- |
| mod | `<实例>/mods/` 里的 jar，以及 `<gameDir>/mc-agent/` |
| Python 侧 | 虚拟环境 |
| 实验室 | `labs/<名字>/`（每个实验室都是自包含的） |

## 9. 其它环境

* **Linux / macOS**：同样的命令，把 `.venv/Scripts/` 换成 `.venv/bin/`，路径用 `/`。`launch_instance.py` 通过 `--java`、`JAVA_HOME` 或 `PATH` 找 Java。
* **专用服务器**：把同一个 mod jar 放进服务端的 `mods/`，它就会在 `mcagent.serverPort` 上提供**服务端 vantage**。注意这需要**服主**安装——只有客户端 mod 是碰不到服务端世界文件的。
* **离线机器**：`build.py` 需要实例的 `libraries/` 和 `.fabric/processedMods/`；`pip install` 需要 wheel；实验室需要下载缓存。三样都先在能联网的机器上备好。
