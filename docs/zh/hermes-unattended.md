# Hermes 无人值守：webhook + Toolkit Skill

事件驱动地让 Hermes 跑在 Minecraft 上的方案：游戏事件唤醒一次 Hermes 运行，
运行加载可移植的 [Toolkit Skill](toolkit-skill.md)，再通过 Bridge MCP 行动。
它取代旧的形态——`mc-agent-loop --backend hermes` 既拥有触发、又保留一份
Hermes 会话/模型逻辑。

```
服务端视角 mod ──事件──► mc-bridge（守护进程）
                            │  JSON-lines API 于 127.0.0.1:8765
                            ▼
                 mc-bridge forward  ──签名 POST──►  Hermes Gateway
                 （接收方中立、只作出站）             webhook route
                                                          │
                                                          ▼
                                     Hermes agent run：Toolkit Skill
                                     + 受限的 mc-agent MCP 工具
                                                          │
                                      ┌───────────────────┴──────────────┐
                                      ▼                                  ▼
                                 结果投递目标                      mc_command
                                 （log/telegram/...）               回写游戏
```

## 部署假设

- **Hermes Gateway、Toolkit（守护进程 + forwarder）和服务端视角 mod 端点跑在
  同一台机器上。** forwarder 只向 loopback 发出站 HTTP 请求，不暴露任何入站端口。
- mod socket 和 bridge API 保持在 loopback（`127.0.0.1`），Hermes 的 webhook
  监听也绑 loopback。不要给其中任何一个做端口转发。
- **mc-agent 不新增 Hermes 模型 API 调用。** 会话、模型调用和对话归 Hermes；
  Toolkit 是不含模型的工具面。
- **route 归用户所有。** mc-agent 从不添加或修改 Hermes webhook 订阅；由用户在
  Hermes Gateway 里配置。

## 各部分的落地状态

Skill 和本页的 Hermes 侧现在就可以用。端到端流程依赖
[mc-agent#8](https://github.com/guajun/mc-agent/issues/8) 计划里的 Toolkit 工作：

| 部分 | 位置 |
| --- | --- |
| 签名事件 forwarder（`mc-bridge forward`） | [mc-agent-bridge#2](https://github.com/guajun/mc-agent-bridge/issues/2) |
| toolkit 操作面：`player`、`context`、`save` | [mc-agent-bridge#1](https://github.com/guajun/mc-agent-bridge/issues/1) |
| 服务端玩家上下文（`PLAYER`） | [mc-agent-interface-mod#1](https://github.com/guajun/mc-agent-interface-mod/issues/1) |
| 聊天瞬间的上下文缓存（`context_id`） | [mc-agent-interface-mod#2](https://github.com/guajun/mc-agent-interface-mod/issues/2) |

在这些落地之前，`mc-bridge call capabilities` 会把上下文相关操作报为不支持
（并给出依赖项），已安装的 bridge 甚至可能还没有 `mc-bridge forward`。Skill 对此
的处理是：如实报告缺口，绝不编造兜底。

!!! danger "签名与投递 ID 的兼容性"

    Hermes 按各来源的 scheme 校验签名；它的通用方案是 **HMAC-SHA256 V2**：
    `X-Webhook-Signature-V2: <hex>` 加 `X-Webhook-Timestamp: <unix 秒>`，
    签名输入是 `"<timestamp>.<raw body>"`，时间戳须在 ±300 秒内。

    mc-agent-bridge#2 规格里的 forwarder 对**同一个输入**签名，但发送的是
    `X-MC-Agent-Signature` / `X-MC-Agent-Timestamp`，Hermes 不认识这两个头。
    带 secret 的 route 会以 `401 Invalid signature` 拒绝这类 POST。要让流程真正
    跑通，forwarder 必须同时发出 Hermes 兼容的请求头（或者让 Hermes 认识
    `X-MC-Agent-*`）；这个改动属于
    [mc-agent-bridge#2](https://github.com/guajun/mc-agent-bridge/issues/2)。
    在它落地之前，可以用下面的验证步骤检查可达性，并预期签名投递会失败关闭
    （fail closed）。

    **幂等性有同样的缺口。** Hermes 的 1 小时去重缓存只认
    `X-GitHub-Delivery`、`svix-id`、`webhook-id` 或 `X-Request-ID`；都没有时用
    当前毫秒数生成 ID。forwarder 用 `X-MC-Agent-Event-Id`（以及 body 里的
    `eventId`）标记每次投递，Hermes 读不到。一旦投递已被接受但响应超时，
    forwarder 会重试同一个事件；Hermes 看到的是新的投递 ID，于是又起一次独立的
    agent 运行，同一批游戏命令可能执行两次。forwarder 必须同时发出 Hermes 兼容的
    投递 ID 头（例如 `webhook-id: <eventId>`）才能让重试去重——这同样属于
    [mc-agent-bridge#2](https://github.com/guajun/mc-agent-bridge/issues/2)
    的改动。

## 1. 把共享 Skill 装进 Hermes

Hermes 没有原生 `gh skill` agent target，所以用 `--dir` 装进它的 home skills
目录（完整细节见
[安装 Toolkit Skill](toolkit-skill.md)）：

```powershell
gh skill install guajun/mc-agent minecraft-toolkit --dir "$env:LOCALAPPDATA\hermes\skills"
hermes skills list        # minecraft-toolkit | （无分类）| local | enabled
```

## 2. 注册 Bridge MCP server

如果 `hermes mcp list` 里已经有[用 Hermes 运行智能体](hermes-setup.md)注册的
bridge，可以跳过。MCP server 是 bridge 守护进程的*客户端*：守护进程必须先跑着
（`mc-bridge run`），server 由 Hermes 在每次运行时拉起：

```powershell
hermes mcp add mc-agent `
  --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8765 `
  --args mcp
```

`--args` 必须放在最后。这个 server 保留完整的 Bridge 工具面；route 实际能用到
多少由第 5 步的限制决定。

## 3. 在 loopback 上启用 Hermes webhook 平台

用向导（`hermes gateway setup`）或在 `%LOCALAPPDATA%\hermes\config.yaml` 里启用。
显式绑定 loopback host——`host` 这一项才是把监听器挡在网络之外的关键：

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: 8644
      secret: "<全局兜底 secret>"   # 可选；route 可以自带 secret
```

`%LOCALAPPDATA%\hermes\.env` 里的环境变量写法也可以（`WEBHOOK_ENABLED=true`、
`WEBHOOK_PORT=8644`、`WEBHOOK_SECRET=...`）。启动 gateway 并检查：

```powershell
hermes gateway run
curl http://127.0.0.1:8644/health    # {"status":"ok","platform":"webhook"}
```

## 4. 创建 route：专用 secret + 这份 Skill

route 过滤聊天事件、注入 `minecraft-toolkit`，并把运行结果投递到你选的目标。
给它一个自己的 secret，而不是继承全局的：

```powershell
hermes webhook subscribe mc-chat `
  --events chat `
  --skills minecraft-toolkit `
  --secret "<该 route 专用的 secret>" `
  --deliver log `
  --prompt "In-game chat from {sender}: {data.text}`nContext id: {context_id} (server tick {tick}). Use the minecraft-toolkit skill: fetch this context bundle before acting, then act within your task."
```

- `--events chat` 匹配转发过来的聊天事件的原始类型。
- `--skills minecraft-toolkit` 和其他运行时装的是同一份 Skill。
- `{sender}`、`{data.text}`、`{context_id}`、`{tick}` 都是 forwarder body 里的
  字段；需要看整个 payload 时用 `{__raw__}`。
- `--deliver log` 是最安全的第一站：回答进 gateway 日志。等运行跑通后再换成
  `telegram`/`discord`/`slack`/... 并配 `--deliver-chat-id <id>`。
- 命令会打印接收 POST 的 URL（`http://127.0.0.1:8644/webhooks/mc-chat`）和要
  配给 forwarder 的 secret。订阅存在
  `%LOCALAPPDATA%\hermes\webhook_subscriptions.json`（权限 0600）。

发送者无法捕获的聊天事件不带 `context_id`；智能体必须如实说明，不能自己编一个。

## 5. 把 route 限制到 Bridge MCP toolset

webhook 运行**默认拿不到完整的 CLI 工具集**：Hermes 刻意收窄它（网页搜索、网页
抓取、视觉、追问），因为 webhook payload 可能包含不可信文本。要让这个 route 能用
Bridge——且只能用 Bridge——给 route 设一份 `toolsets`。这是刻意设计的手工编辑：
`hermes webhook subscribe` 没有 `--toolsets` 参数，这样智能体自己创建的订阅
无法自我提权。

在 `%LOCALAPPDATA%\hermes\webhook_subscriptions.json` 里给 `mc-chat` route
加一个键：

```jsonc
{
  "mc-chat": {
    "events": ["chat"],
    "secret": "<该 route 专用的 secret>",
    "prompt": "In-game chat from {sender}: {data.text}\nContext id: {context_id} ...",
    "skills": ["minecraft-toolkit"],
    "toolsets": ["mc-agent"],          // ← 只有第 2 步的 Bridge MCP server
    "deliver": "log",
    "profile": "default"
  }
}
```

`mc-agent` 就是第 2 步的 MCP server 名。route 级的列表会**替换**该 route 运行的
平台 webhook toolset，因此这次运行只有 Bridge 工具，没有 terminal/file/web/
computer-use。适配器会在下一次请求时热加载订阅文件，改完不用重启；但重新执行
`hermes webhook subscribe` 会重建 route 并丢掉这个键，改完 route 后要重新加上。

!!! note "这不是冷启动的开发 Harness"
    受限 route 只有 Bridge MCP server——没有终端、文件、源码获取或构建工具。
    需要自己写、编译、安装 logger 的智能体无法通过这条 route 完成。首轮
    Minecart ROM 冷启动因此采用带普通开发工具、由用户手动启动的 Harness；
    webhook 投递只是后续可选路径：见[可审计的冷启动运行](coldstart-protocol.md)。

!!! tip "声明式替代方案"
    如果你希望所有配置都在一个文件里，可以在 `config.yaml` 的
    `platforms.webhook.extra.routes` 下声明同一条 route（`toolsets` 直接写进去，
    secret 也在里面），而不使用 `hermes webhook subscribe`。静态 route 优先于
    动态订阅，改完需要 `hermes gateway restart`。

## 6. 启动 forwarder

forwarder 订阅 bridge 事件流，把选定事件 POST 到 route URL。凭据来自环境变量或
配置文件，绝不用会进 shell 历史的命令行参数：

```powershell
$env:MC_AGENT_WEBHOOK_URL = "http://127.0.0.1:8644/webhooks/mc-chat"
$env:MC_AGENT_WEBHOOK_SECRET = "<第 4 步的 route 专用 secret>"
mc-bridge forward --events chat
```

URL 和 secret 两者齐备之前转发是关闭的。保持 `--events chat`（或更窄的列表），
让 route 只被它接受的事件唤醒；forwarder 默认过滤 `chat,game,mark,error`。
bridge 守护进程必须已经在跑，并且连着服务端视角 mod。

## 7. 验证流程

等上面兼容性说明里的传输可用后，逐条走一遍。每条都写明了要找的证据。

| # | 检查 | 证据 |
| --- | --- | --- |
| 1 | 监听器活着 | `curl http://127.0.0.1:8644/health` 返回 `{"status":"ok","platform":"webhook"}` |
| 2 | route 存在 | `hermes webhook list` 显示 `mc-chat`、URL 和 `deliver` |
| 3 | 可达性与签名 | `hermes webhook test mc-chat` 有响应（它的事件类型是 `test`，所以只收 `chat` 的 route 会回答 `{"status":"ignored"}`——这仍证明 secret 被接受；接受 `test` 的 route 会回答 `202`） |
| 4 | 事件离开游戏 | `mc-bridge watch --events chat` 打印聊天事件；forwarder 日志出现 `delivered event <eventId> ... (HTTP 202)` |
| 5 | 重试保持幂等 | 同一个 `eventId` 的重试投递得到 `{"status":"duplicate"}`，不会起第二次运行，游戏命令只执行一次 |
| 6 | 运行加载了 Skill | gateway 日志显示 `mc-chat` 运行，且加载的技能集里有 `minecraft-toolkit` |
| 7 | 取到了上下文包 | 运行用事件里的 `context_id` 调 `mc_context`（或如实报告 `not_found`/`expired`） |
| 8 | 用上了 Bridge | 运行至少调到一个服务端视角 Bridge 工具（`mc_capabilities`、`mc_state`、`mc_player`……） |
| 9 | 结果投递成功 | 回答出现在 `--deliver` 目标（用 `log` 时在 gateway 日志里） |
| 10 | toolset 确实受限 | 聊天里的提示注入无法触达 terminal/file；这次运行只有 `mc-agent` 工具 |

想不依赖传输做一次慢速端到端检查：一边 `mc-bridge watch`、一边看 gateway 日志，
然后在游戏里发一条聊天；三样东西——Skill、`context_id`、Bridge 工具——都要出现在
这次运行里。

## 投递行为

- 运行的回答去 route 指定的地方：`--deliver` 加 `--deliver-chat-id`（或
  `deliver_extra.chat_id`）。默认的 `log` 是最合适的第一站；流程验证后再换真实
  平台。
- 要在**游戏内**回答，智能体用 Bridge 的 `command` 操作（例如 `say <text>`；
  希望以玩家身份发言时用 `execute as <player> run say <text>`）。服务端视角连接
  没有 `chat` 能力——`mc_chat` 只属于客户端视角——所以不要期待模型以客户端身份
  说话。见[智能体在游戏里是谁](player-identity.md)。
- **玩家身份是上下文，不是授权。** 玩家的 UUID 或名字只说明是谁在说话，并不授予
  特权操作。route 的任务、运维者的配置、受限的 toolset 才定义智能体能做什么——
  而不是聊天里的话。智能体应把聊天文本当作不可信输入，并待在任务范围内。

## 安全提示

- 使用**每个 route 专用的 secret**。真实部署不要用 `INSECURE_NO_AUTH`；它会关闭
  签名校验，只适合本地测试。
- 按第 3 步把监听器绑在 `127.0.0.1`；mod 和 Bridge API 也留在 loopback；
  forwarder 只出站。
- HMAC 签名认证的是*发送方*，不是*内容*。聊天文本可能夹带注入指令，所以保持
  route 的 toolset 受限（第 5 步）、prompt 模板只点名需要的字段、破坏性或对外的
  操作保留审批。
- Hermes gateway 主机有模型和终端访问权；把它当受信基础设施，不要把 webhook
  端口暴露到 loopback 之外。

## 后续：移除 loop 的 Hermes 后端

这条路径端到端验证通过后，`mc-agent-loop --backend hermes` 就是重复设施：触发、
会话归属、模型调用和投递都已在 Hermes 里。从 mc-agent-loop 移除 Hermes HTTP
模型后端——连同只为它存在的 `HERMES_*` 环境变量、参数、文档和冒烟测试路径——
记录在 [mc-agent-loop#3](https://github.com/guajun/mc-agent-loop/issues/3)。
在那之前，旧后端继续服务 loop/API-server 工作流。

## 疑难排查

| 现象 | 可能原因 |
| --- | --- |
| `404 Unknown route` | route 名写错，或 `--route-profile` 前缀不对；查 `hermes webhook list` |
| `401 Invalid signature` | secret 不一致，或发送方的请求头 Hermes 不认识——见上面的兼容性说明 |
| `{"status":"ignored"}` | 事件类型被 `--events` 过滤了；`hermes webhook test` 的类型是 `test` |
| route 一直不触发 | gateway 没跑、`host`/`port` 与 forwarder 的 URL 不一致，或 forwarder 没订阅上 |
| 运行里没有 Bridge 工具 | `toolsets` 被重建时丢掉了，或 server 名不对；重新加上 `["mc-agent"]`，查 `hermes mcp list` |
| Skill 没加载 | `hermes skills list` 里没有 `minecraft-toolkit`，或 `--skills` 的名字与 Skill 的 `name:` 不一致 |
| `context` 回答 `not_found`/`expired` | 上下文包的 TTL 过了，或 id 写错；用新事件，绝不用别的玩家的上下文顶替 |
| forwarder 起不来 | 缺 `MC_AGENT_WEBHOOK_URL`/`MC_AGENT_WEBHOOK_SECRET`，或 URL 不是 `http(s)` |
