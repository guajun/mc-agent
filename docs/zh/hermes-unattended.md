# 无人值守 Hermes：webhook + Toolkit Skill

事件驱动运行 Hermes 的方式：游戏事件唤醒一次 Hermes agent 运行，它加载可移植的
[Toolkit Skill](toolkit-skill.md)，通过 `mc-agent` CLI 行动。它取代旧形态——由
`mc-agent-loop --backend hermes` 同时拥有触发**和**一份 Hermes 会话/模型逻辑。

```text
服务端视角 mod ──事件──► mc-agent daemon（回放缓冲 + webhook）
                              │  签名 POST（X-MC-Agent-*）
                              ▼
                    本地接收端/校验器  ──► Hermes Gateway
                    (webhook_receiver.py)   webhook 路由
                                                  │
                                                  ▼
                                  Hermes agent run：Toolkit Skill
                                  + 允许执行 mc-agent CLI 的权限
                                                  │
                                   ┌──────────────┴──────────────┐
                                   ▼                             ▼
                             投递目标                  mc-agent command
                             (log/telegram/...)          写回游戏
```

## 状态与兼容性

- Toolkit 的签名、接收端中立 webhook 已实现，并用本仓库的本地接收端
  （`tools/webhook_receiver.py`）做过端到端验证：daemon 投递签名事件，接收端校验
  签名、按事件 id 去重，并拒绝被篡改的签名与过期时间戳。
- daemon 发送 `X-MC-Agent-Signature`、`X-MC-Agent-Timestamp`、
  `X-MC-Agent-Event-Id`、`X-MC-Agent-Event-Type` 和 `X-MC-Agent-Attempt`。
- Hermes 通用 webhook 方案是 HMAC V2（`X-Webhook-Signature-V2`、
  `X-Webhook-Timestamp`，签名输入 `"<timestamp>.<raw body>"`），并按
  `webhook-id`/类似投递 id 去重。本地接收端可以把已验证事件按该方案转发。该 shim 的
  签名与去重已在本地验证；投入生产前请在你的 Hermes 版本上确认。
- 路由执行 CLI，而不是 MCP 服务器。给它进程执行权限即可，不要再给别的；权限来自
  控制凭证。

## 部署假设

- Hermes Gateway、接收端和 daemon 可以跑在同一台机器；接收端与 Hermes 监听绑定
  loopback。Toolkit 不暴露任何入站。
- mod 的控制传输在游戏端口；daemon 主动连出。不涉及 SSH、额外公网端口或客户端 mod
  中继。
- **mc-agent 不添加 Hermes API/模型客户端。** 会话、模型调用和对话归 Hermes；
  Toolkit 是无模型工具面。
- **路由由用户拥有。** mc-agent 不会添加或修改 Hermes webhook 订阅；由用户在
  Hermes Gateway 中配置。

## 1. 把共享 Skill 安装到 Hermes

Hermes 没有原生 `gh skill` agent 目标；用 `--dir` 装到它的 home skills 目录，或用
发行安装器的 `--skill-harness hermes`：

```powershell
# 来自 mc-agent 发行包（绑定 toolkit 版本）
./install.ps1 -Version 0.5.0 -SkillHarness hermes

# 或用 GitHub CLI 从 meta 仓库安装
gh skill install guajun/mc-agent minecraft-toolkit --dir "$env:LOCALAPPDATA\hermes\skills"
hermes skills list        # minecraft-toolkit | (no category) | local | enabled
```

## 2. 在 loopback 上启用 Hermes webhook 平台

用向导（`hermes gateway setup`）或编辑 `%LOCALAPPDATA%\hermes\config.yaml` 启用。
`host` 键决定监听器不暴露到网络：

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: 8644
      secret: "<全局回退 secret>"   # 可选；路由可自带
```

`%LOCALAPPDATA%\hermes\.env` 中的环境变量写法同样有效
（`WEBHOOK_ENABLED=true`、`WEBHOOK_PORT=8644`、`WEBHOOK_SECRET=...`）。启动 gateway
并检查：

```powershell
hermes gateway run
curl http://127.0.0.1:8644/health    # {"status":"ok","platform":"webhook"}
```

## 3. 创建路由：专用 secret、本 Skill、CLI 访问

路由过滤聊天事件、注入 `minecraft-toolkit`，并把运行结果投递到你选择的目标。给它
独立 secret：

```powershell
hermes webhook subscribe mc-chat `
  --events chat `
  --skills minecraft-toolkit `
  --secret "<路由专用 secret>" `
  --deliver log `
  --prompt "游戏内聊天，来自 {sender}: {data.text}`nContext id: {context_id}（服务器 tick {tick}）。使用 minecraft-toolkit skill：先取回该上下文 bundle，再在任务范围内行动。"
```

`{sender}`、`{data.text}`、`{context_id}`、`{tick}` 是 daemon 事件体中的字段；
需要时 `{__raw__}` 输出整个 payload。路由保存在
`%LOCALAPPDATA%\hermes\webhook_subscriptions.json`（权限 0600）。

然后允许该运行执行 CLI。webhook 运行默认只得到受限工具集，因此在
`webhook_subscriptions.json` 为该路由加入进程执行工具集（名称以你的 Hermes 版本
为准；用 `hermes webhook subscribe --help` 或 `hermes tools list` 确认）：

```jsonc
{
  "mc-chat": {
    "events": ["chat"],
    "secret": "<路由专用 secret>",
    "skills": ["minecraft-toolkit"],
    "toolsets": ["<你的 Hermes 版本提供的 shell/terminal 工具集>"],
    "deliver": "log",
    "profile": "default"
  }
}
```

保持 web 搜索、网页抓取、文件和 computer-use 工具**关闭**：聊天文本是不可信输入。
任务只观察世界时使用 `read` 控制凭证，这样即使模型尝试，`command`、`mark` 和
`snapshot` 也不可用。适配器会热加载订阅文件；重新运行 `hermes webhook subscribe`
会重建路由，记得重新加回工具集。

## 4. 启动 daemon webhook 并本地验证

`daemon start` 不接受 webhook 参数；请前台运行或交给服务管理器：

```bash
mc-agent daemon run --webhook-url http://127.0.0.1:8645/hook \
    --webhook-secret test-secret --webhook-events chat,game,mark,error
```

在另一个终端启动本地接收端。它校验签名与时间戳、按稳定事件 id 去重，并为每个事件
打印一行 JSON：

```bash
python tools/webhook_receiver.py serve --secret test-secret --port 8645
```

无需 daemon 的自检：

```bash
python tools/webhook_receiver.py selftest
# webhook receiver selftest passed (accept, deduplicate, reject bad signature, reject stale)
```

然后触发事件（`mc-agent command "say hello"` 会产生一个 `write` 事件；配置
`--webhook-events "*"` 时会被转发），确认接收端在验证 `X-MC-Agent-Signature` 后
打印了它。

## 5. 把已验证事件转发到 Hermes 路由

可以直接让 daemon 指向 Hermes 路由（如果你的 Hermes 版本接受 `X-MC-Agent-*` 方案），
或经由本地接收端按 Hermes 通用方案重新签名：

```bash
python tools/webhook_receiver.py serve --secret test-secret --port 8645 \
    --forward-url http://127.0.0.1:8644/webhooks/mc-chat \
    --forward-secret "<路由专用 secret>" --scheme hermes-v2
mc-agent daemon run --webhook-url http://127.0.0.1:8645/hook \
    --webhook-secret test-secret --webhook-events chat
```

转发模式发送 `X-Webhook-Signature-V2`、`X-Webhook-Timestamp` 和稳定的 `webhook-id`，
因此重试会在 Hermes 去重，而不会启动第二次运行。

## 6. 验证流程

| # | 检查 | 证据 |
| --- | --- | --- |
| 1 | 接收端能校验签名 | `webhook_receiver.py selftest` 通过 |
| 2 | daemon 能到达接收端 | daemon 日志显示转发；接收端打印事件类型/类别 |
| 3 | 重试幂等 | 重复的 `X-MC-Agent-Event-Id` 返回 `{"status":"duplicate"}` |
| 4 | 监听器存活 | `curl http://127.0.0.1:8644/health` 返回 `{"status":"ok","platform":"webhook"}` |
| 5 | 路由存在 | `hermes webhook list` 显示 `mc-chat`、URL 与 `deliver` |
| 6 | 可达性与签名 | `hermes webhook test mc-chat` 有响应；过滤为 `chat` 的路由返回 `{"status":"ignored"}`（仍证明 secret 被接受） |
| 7 | 事件离开游戏 | `mc-agent events --category chat --follow` 打印聊天事件 |
| 8 | 运行加载了 Skill | gateway 日志显示 `mc-chat` 运行与已加载的 `minecraft-toolkit` |
| 9 | 取回了上下文 bundle | 运行调用 `mc-agent context <id>`（或如实报告 `not_found`/`expired`） |
| 10 | 使用了 CLI | 运行至少调用一个 `mc-agent` 操作 |
| 11 | 回答已投递 | 响应出现在 `--deliver` 目标（`log` 时在 gateway 日志） |
| 12 | 工具集受限 | 聊天中的提示注入无法触达 web/文件工具；只开放 CLI 执行 |

## 投递行为

- 运行回答按路由配置投递：`--deliver` 加 `--deliver-chat-id`。`log` 是默认值，也是
  合适的第一步。
- 想在**游戏内**回答，智能体使用 `mc-agent command "say <text>"`（或
  `execute as <player> run say <text>`），这需要 `write` 凭证。服务端视角没有客户端
  `chat` 操作。
- **玩家身份是上下文，不是授权。** 路由任务、凭证权限和受限工具集决定智能体能做
  什么，而不是聊天里的文字。

## 安全说明

- 使用路由专用 secret 和专用的最小权限控制凭证；部署结束时吊销。
- 接收端与 Hermes 监听保持在 `127.0.0.1`。Toolkit 从不为接收端监听；webhook 流量
  都是出站。
- 有效 HMAC 签名认证的是*发送者*，不是*内容*。保持路由工具集受限、模板窄化，对破坏性
  或出站操作保留审批。
- webhook 投递是内存内、尽力而为：daemon 重启会丢失排队事件，接收端故障在重试耗尽后
  会丢失投递。需要可靠唤醒时请保持接收端在线，并把缺口当缺口处理。

## 在线要求汇总

| 组件 | 用户主动 | 无人值守 |
| --- | --- | --- |
| 游戏 + mod | 智能体工作期间必需 | 智能体工作期间必需 |
| daemon | 调用 CLI 期间必需 | 整个会话期间必需 |
| 接收端 / Hermes gateway | 不需要 | 唤醒与回复必需 |
| 凭证 | 按任务选 read 或 read+write | 观察用 read；只有命令才用 read+write |

## 故障排查

| 症状 | 可能原因 |
| --- | --- |
| 接收端返回 `401` | secret 不匹配，或发送者方案不被识别；检查 scheme |
| 接收端从不打印 | daemon 未配置 `--webhook-url`/secret，事件类别被过滤，或 URL 不是 HTTP(S) |
| `404 Unknown route` | 路由名或 profile 前缀错误；查看 `hermes webhook list` |
| `{"status":"ignored"}` | 事件类型被 `--events` 过滤；Hermes 测试事件标记为 `test` |
| 路由从不触发 | gateway 未运行、`host`/`port` 与接收端转发 URL 不一致，或接收端没有转发 |
| 运行无法使用 CLI | 路由缺少进程执行工具集，或 gateway 环境中 `mc-agent` 不在 PATH |
| Skill 未加载 | `hermes skills list` 没有 `minecraft-toolkit`，或 `--skills` 名称与 skill 的 `name:` 不一致 |
| `context` 返回 `not_found`/`expired` | bundle TTL 过期或 id 写错；用新事件，绝不用其他玩家的上下文 |

## 后续：移除 loop 的 Hermes 后端

在你的部署上端到端验证后，`mc-agent-loop --backend hermes` 就是重复基础设施：触发、
会话所有权、模型调用与投递都在 Hermes 里。移除 mc-agent-loop 的 Hermes HTTP 模型
后端进度见 [mc-agent-loop#3](https://github.com/guajun/mc-agent-loop/issues/3)。
在此之前旧后端仍服务于 loop/API-server 工作流，并依赖遗留 Python bridge。
