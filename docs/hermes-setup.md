# Wiring Hermes to the bridge

How the agent in this framework is actually run: Hermes as the agent loop's
backend, the bridge as its tool surface, Minecraft as the world.

!!! info "Event-driven Hermes has its own path"
    This page wires Hermes as the **loop's model backend**: `mc-agent-loop` owns
    the chat trigger and calls Hermes over its OpenAI-compatible API. For
    unattended, event-triggered runs, Hermes can own the trigger too - a
    user-configured webhook route loads the same portable
    [Toolkit Skill](toolkit-skill.md) and uses the Bridge MCP tools directly.
    See [Unattended Hermes: webhook + Toolkit Skill](hermes-unattended.md). The
    loop's Hermes backend is slated for removal once that path is verified
    ([mc-agent-loop#3](https://github.com/guajun/mc-agent-loop/issues/3)).

Everything below is Windows-native; the same steps work on Linux/macOS with the
paths swapped.

## 1. Install Hermes

Official installer (brings its own uv, Python 3.11, Node, ripgrep, ffmpeg):

```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

For unattended installs, skip the wizard and the optional computer-use bundle:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 -NonInteractive -SkipComputerUse
```

Files land in `%LOCALAPPDATA%\hermes` (`config.yaml`, `.env`, `hermes-agent\`,
`bin\hermes.exe`). Nothing is installed system-wide and no admin rights are
needed.

> Hermes requires Python <3.14 and provisions its own 3.11, so a machine whose
> only interpreter is newer is fine.

## 2. Point it at a model

Any provider Hermes supports works. To reuse the model a Codex setup already
uses (deepseek-flash via the native DeepSeek provider):

```powershell
hermes config set model.provider deepseek
hermes config set model.default deepseek-flash
```

Put the key in `%LOCALAPPDATA%\hermes\.env` (never in `config.yaml`, never in a
repository):

```
DEEPSEEK_API_KEY=sk-...
```

Sanity check without a chat session:

```powershell
hermes status
```

## 3. Expose the API server

The agent loop talks to Hermes over its OpenAI-compatible endpoint, so no
Hermes SDK is needed on the loop side:

```powershell
hermes config set API_SERVER_ENABLED true
```

```
# %LOCALAPPDATA%\hermes\.env
API_SERVER_KEY=<random local secret>
```

```powershell
hermes gateway        # [API Server] listening on http://127.0.0.1:8642
```

```powershell
curl http://localhost:8642/v1/chat/completions `
  -H "Authorization: Bearer <API_SERVER_KEY>" -H "Content-Type: application/json" `
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"ping"}]}'
```

The key matters: the agent has terminal access on this machine, so keep the
endpoint behind a secret even though it only binds to loopback.

## 4. Give it the game

Register the bridge's MCP front-end so the model gets `mc_state`, `mc_entities`,
`mc_command`, `mc_record_start`, `mc_events` and friends:

```powershell
hermes mcp add mc-agent `
  --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8765 `
  --args mcp
```

With two players (see `docs/player-identity.md`) register two servers, so the
agent's tools describe its own body and yours is a second, explicitly named
window into the world:

```powershell
hermes mcp add mc-agent --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8766 --args mcp      # the agent's own client
hermes mcp add mc-host  --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8765 --args mcp      # the human's client
```

`--args` must be last. The command discovers the tools, asks whether to enable
them, and writes the result into `config.yaml` under `mcp_servers`.

Note the contract: the MCP server is a *client* of the bridge daemon. The daemon
owns the connection to the game and must be running (`mc-bridge run`); the MCP
server is spawned by Hermes and connects to it on the configured port.

## 5. Point the loop at Hermes

`mc-agent-loop` reads three variables:

```
HERMES_API_BASE=http://127.0.0.1:8642
HERMES_MODEL=hermes-agent
HERMES_API_KEY=<the API_SERVER_KEY from step 3>
```

Keep them in an untracked `.env` next to the meta repository (`.env` is
git-ignored), then:

```powershell
mc-agent-loop run --backend hermes --trigger @codex --env-file .env
```

`--env-file` (default `./.env`) keeps the key off the command line. Now
`@codex <anything>` in game chat reaches the model, and the model can look at
the world before answering.

One caveat about single player: the loop ignores chat from the client's own
player name, which is the client it is attached to. So the chat trigger only
fires when *somebody else* says it - another player on a server, or a second
client running the mod (see the `mc-agent-interface-mod` README). For a
single-client session use the one-shot form instead:

```powershell
mc-agent-loop once "look around and tell me what is within 64 blocks" --sender operator --env-file .env
```

## 6. Verify without Minecraft

```powershell
python tools/smoke_offline.py --backend hermes
```

Fake mod, real daemon, real loop, real model. A passing run looks like:

```
[smoke] lines received by the mod: ['STATE', 'STATE', 'STATE',
        "CHAT Hello! My character's name is Bot - currently in-world and connected."]
```

The third `STATE` is the model calling `mc_state` through MCP: the tool path is
live, not decorative.

## Operating notes

* The gateway is a long-running process; it is what serves `/v1/chat/completions`.
  Restart it after changing `config.yaml` (`hermes gateway restart`) so new
  sessions pick up MCP servers or model changes.
* Model changes only apply to *new* sessions; agents already running keep their
  model.
* The bridge daemon and the loop are separate processes with separate failure
  domains. Restarting either does not disturb the game connection of the other.
