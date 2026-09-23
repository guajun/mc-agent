# 把 Hermes 接到 bridge 上

这套框架里的智能体实际是怎么跑起来的：Hermes 是 loop 的后端，bridge 是它的工具面，Minecraft 是世界。

下面全部是 Windows 原生路径；换到 Linux/macOS 只要替换路径。

## 1. 安装 Hermes

官方安装脚本（自带 uv、Python 3.11、Node、ripgrep、ffmpeg）：

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

无人值守安装时跳过向导和可选的 computer-use 包：

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -NonInteractive -SkipComputerUse
```

文件落在 `%LOCALAPPDATA%\hermes`（`config.yaml`、`.env`、`hermes-agent\`、`bin\hermes.exe`）。不装到系统里，也不需要管理员权限。

> Hermes 要求 Python <3.14，并自带一份 3.11，所以机器上只有更新的解释器也没问题。

## 2. 指向一个模型

Hermes 支持的任何 provider 都可以。想复用 Codex 已经在用的模型（走内置 DeepSeek provider 的 `deepseek-flash`）：

```powershell
hermes config set model.provider deepseek
hermes config set model.default deepseek-flash
```

key 放进 `%LOCALAPPDATA%\hermes\.env`（**不要**写进 `config.yaml`，更不要提交进仓库）：

```
DEEPSEEK_API_KEY=sk-...
```

不起聊天会话也能自检：

```powershell
hermes status
```

## 3. 暴露 API server

loop 是通过 Hermes 的 OpenAI 兼容端点跟它说话的，所以 loop 侧不需要任何 Hermes SDK：

```powershell
hermes config set API_SERVER_ENABLED true
```

```
# %LOCALAPPDATA%\hermes\.env
API_SERVER_KEY=<随机本地密钥>
```

```powershell
hermes gateway        # [API Server] listening on http://127.0.0.1:8642
```

```powershell
curl http://localhost:8642/v1/chat/completions `
  -H "Authorization: Bearer <API_SERVER_KEY>" -H "Content-Type: application/json" `
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"ping"}]}'
```

这个 key 是有意义的：智能体在这台机器上有终端权限，所以即使只监听 loopback，也要用密钥把端点保护起来。

## 4. 把游戏给它

注册 bridge 的 MCP 前端，模型就拿到了 `mc_state`、`mc_entities`、`mc_command`、`mc_record_start`、`mc_events` 这些工具：

```powershell
hermes mcp add mc-agent `
  --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8765 `
  --args mcp
```

如果是两个玩家（见 [智能体在游戏里是谁](player-identity.md)），注册两个 server：智能体的工具描述它自己的身体，你的那个是第二个、有明确命名的窗口：

```powershell
hermes mcp add mc-agent --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8766 --args mcp      # 智能体自己的客户端
hermes mcp add mc-host  --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8765 --args mcp      # 人类的客户端
```

`--args` 必须放在最后。命令会先发现工具、问你要不要启用，然后把结果写进 `config.yaml` 的 `mcp_servers`。

注意这个契约：MCP server 是 bridge 守护进程的**客户端**。守护进程持有游戏连接、必须正在运行（`mc-bridge run`）；MCP server 由 Hermes 拉起，连到配置的端口。

## 5. 把 loop 指向 Hermes

`mc-agent-loop` 读三个变量：

```
HERMES_API_BASE=http://127.0.0.1:8642
HERMES_MODEL=hermes-agent
HERMES_API_KEY=<第 3 步里的 API_SERVER_KEY>
```

把它们放在 meta 仓库旁边一个**未跟踪**的 `.env` 里（`.env` 已 gitignore），然后：

```powershell
mc-agent-loop run --backend hermes --trigger @codex --env-file .env
```

`--env-file`（默认 `./.env`）让 key 不出现在命令行里。现在游戏聊天里的 `@codex <任何话>` 会到达模型，而模型可以先看看世界再回答。

单机有一个注意点：loop 会忽略**它自己那个客户端**的玩家名发出的聊天，而它附着的正是这个客户端。所以聊天触发只在**别人**说的时候生效——服务器上的另一个玩家，或第二个跑着 mod 的客户端（见 `mc-agent-interface-mod` 的 README）。单客户端会话请用一次性模式：

```powershell
mc-agent-loop once "看看周围 64 格内有什么" --sender operator --env-file .env
```

## 6. 不需要 Minecraft 也能验证

```powershell
python tools/smoke_offline.py --backend hermes
```

假 mod、真守护进程、真 loop、真模型。跑通的输出长这样：

```
[smoke] lines received by the mod: ['STATE', 'STATE', 'STATE',
        "CHAT Hello! My character's name is Bot - currently in-world and connected."]
```

第三个 `STATE` 是模型通过 MCP 调了 `mc_state`：工具链路是活的，不是摆设。

## 运维要点

* gateway 是常驻进程，`/v1/chat/completions` 由它提供。改过 `config.yaml` 后要重启它（`hermes gateway restart`），新会话才会看到 MCP server 或模型的变更。
* 模型变更只对**新**会话生效；已经在跑的智能体保持原来的模型。
* bridge 守护进程和 loop 是两个进程、两个故障域。重启其中一个不会打断另一个的游戏连接。
