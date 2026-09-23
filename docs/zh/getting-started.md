# 快速开始

这一页带你从零走到"智能体能回答一个关于你世界的问题"，再往前走一步：得到一个可以做实验的实验室实例。下面每条命令都在 Minecraft 26.2 / Windows 上真实跑过；换平台只需改路径。

## 需要什么

| | |
| --- | --- |
| **Minecraft** | 26.2 + **Fabric Loader 0.19+** + **Fabric API** |
| **Java** | 25（游戏自带的那份运行时就可以） |
| **Python** | 3.11+，用于 bridge、loop 和工具 |
| **智能体运行时** | 本项目是对着 [Hermes](hermes-setup.md) 开发的；任何能调 MCP 或 HTTP 的东西也行 |

## 1. 编译并安装接口 mod

mod 直接对着游戏自己的（未混淆）jar 编译——不需要 Gradle，也不需要反编译：

```bash
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py \
    --minecraft-dir "C:/Users/me/AppData/Roaming/.minecraft" \
    --version 26.2-Fabric \
    --jdk "C:/Program Files/Java/jdk-25"
```

把 `dist/mc-agent-interface-<版本>.jar` 和 Fabric API 一起放进 `<实例>/mods/`，启动游戏。日志里应该能看到：

```
[mc-agent-interface] initialized, dir=<实例>/mc-agent basePort=25580
[mc-agent-interface] listening on 127.0.0.1:25580
```

mod 会把实际拿到的端口写进 `<实例>/mc-agent/port.txt`。25580 被占用时它会自动往后退一格，bridge 通过那个文件找到它。

!!! tip "游戏里现就可以用"
    mod 自带客户端指令，不需要 bridge 就能自检：

    ```
    /mcagent status      版本、端口、已连接的 bridge 数、tick
    /mcagent state       坐标、速度、血量、维度
    /mcagent entities 32 32 格内的实体列表
    /mcagent record start 200 32    连续采样 200 tick 写入 samples.jsonl
    ```

## 2. 装 bridge 和 loop

```bash
git clone https://github.com/guajun/mc-agent-bridge
git clone https://github.com/guajun/mc-agent-loop
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]" -e mc-agent-loop
```

`[mcp]` 是可选的：不装也有守护进程、CLI 和本地 API。

## 3. 起 bridge

```bash
.venv/Scripts/mc-bridge run
```

```
[mc-agent-bridge] local API on 127.0.0.1:8765
[mc-agent-bridge] connected to interface mod on port 25580
```

游戏没开它也会一直重试，所以什么时候起都行；游戏可以随便开关，不会打断它。另开一个终端：

```bash
.venv/Scripts/mc-bridge call state
.venv/Scripts/mc-bridge call entities '{"radius": 32}'
.venv/Scripts/mc-bridge call chat '{"message": "来自外部的问候"}'
.venv/Scripts/mc-bridge watch --events chat,game      # 实时事件流
```

## 4. 给它一个大脑

loop 把聊天变成后端的一轮对话。想先看它跑通，不需要模型：

```bash
.venv/Scripts/mc-agent-loop run --backend echo --trigger @codex
```

在游戏聊天里打 `@codex 你好`，游戏会自己回声一句——这证明链路是通的：聊天进、后端出、回复再回到聊天。

要接真模型，就起 Hermes 并让 loop 指过去（见 [用 Hermes 运行智能体](hermes-setup.md)）：

```bash
.venv/Scripts/mc-agent-loop run --backend hermes --trigger @codex --env-file .env
```

loop 会忽略自己发出的消息（否则它会和自己无限对话），所以单机世界里触发必须来自别人：另一个玩家，或者智能体自己的玩家（见 [智能体在游戏里是谁](player-identity.md)）。单客户端场景用一次性模式：

```bash
.venv/Scripts/mc-agent-loop once "看看周围 64 格内有什么" \
    --sender operator --backend hermes --env-file .env
```

## 5. 没有游戏也能验证

仓库里带了一个假 mod，可以单独测接缝：

```bash
python tools/smoke_offline.py                     # echo 后端，不用模型
python tools/smoke_offline.py --backend hermes    # 真模型、真工具
```

第二条会跑真正的整条链——守护进程、loop、MCP、模型——只是把游戏换成了替身。它是判断"问题出在你的配置还是游戏"最快的方法。

## 6. 给智能体一个实验室

做研究时你想要的实例是：没有人、不渲染、可确定性步进。一条命令加一次启动：

```bash
python tools/lab_server.py provision --name my-lab --void --fabric-api --carpet \
    --mod-jar mc-agent-interface-mod/dist/mc-agent-interface-0.5.2.jar \
    --java <java25>
python tools/lab_server.py start --name my-lab --wait 300
python tools/lab_server.py exec --name my-lab "tick freeze"
```

实验室以 mod 的**服务端 vantage** 监听 `labs/my-lab/mc-agent-server/port.txt` 里的端口（默认 25581，占用则顺延）。把 bridge 接上去，它说同一套工具：

```bash
.venv/Scripts/mc-bridge run --api-port 8766 \
    --port-file labs/my-lab/mc-agent-server/port.txt
.venv/Scripts/mc-bridge --api-port 8766 call state
```

## 7. 把一个活世界分叉进去

实验室的意义在于：你可以把**正在运行的世界**整个带走，包括实体的 tick 顺序。

```bash
# 对着活实例的服务端 vantage
.venv/Scripts/mc-bridge --api-port 8767 call fork '{"name": "before", "radius": 64}'

# 在实验室里还原，并证明顺序没丢
python tools/fork_verify.py inspect "<分叉目录>"
python tools/fork_verify.py restore "<分叉目录>" --apply --api-port 8766
python tools/fork_verify.py check   "<分叉目录>" --api-port 8766 --radius 0
```

`check` 会对实验室重新取样并比对顺序哈希：`MATCH` 意味着隔离实例里实体的 tick 顺序和你分叉的那个世界一致。完整配方（包括那些容易踩错的地方）见 [分叉一个活的世界](protocol-snapshot.md)。

## 接下来

* [整体结构](concepts.md) —— 这样你会把新代码放进正确的层
* [工具](tools.md) —— 其余的工具箱
* [疑难排查](troubleshooting.md) —— 收集好的坑
