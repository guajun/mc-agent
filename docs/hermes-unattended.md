# Unattended Hermes: webhook + Toolkit Skill

The event-driven way to run Hermes against Minecraft: a game event wakes a
Hermes agent run that loads the portable [Toolkit Skill](toolkit-skill.md) and
acts through the Toolkit MCP server. This replaces the old shape where
`mc-agent-loop --backend hermes` owned the trigger **and** a copy of the Hermes
session/model logic.

```
server-vantage mod ──events──► Toolkit daemon (`mc-bridge run`)
                                   │  JSON-lines API on 127.0.0.1:8765
                                   ▼
                        mc-bridge forward  ──signed POST──►  Hermes Gateway
                        (receiver-neutral, outbound only)     webhook route
                                                                  │
                                                                  ▼
                                             Hermes agent run: Toolkit Skill
                                             + restricted mc-agent MCP tools
                                                                  │
                                              ┌───────────────────┴──────────────┐
                                              ▼                                  ▼
                                        delivery target                 mc_command back
                                        (log/telegram/...)                into the game
```

## Deployment assumption

- **Hermes Gateway, the Toolkit (daemon + forwarder), and the server-vantage mod
  endpoint run on the same machine.** The forwarder makes an outbound HTTP
  request to loopback; nothing inbound is exposed.
- The mod socket and the Toolkit API stay on loopback (`127.0.0.1`), and the
  Hermes webhook listener binds loopback too. Do not port-forward any of them.
- **mc-agent adds no Hermes API/model client.** Hermes owns the session, the
  model calls, and the conversation; the Toolkit is a model-free tool surface.
- **The route is user-owned.** mc-agent never adds or edits a Hermes webhook
  subscription; the user configures it in Hermes Gateway.

## Status of the pieces

The portable Skill, server-vantage operations, context bundles and
receiver-neutral forwarder are implemented. These issue links record where the
pieces landed:

| Piece | Where |
| --- | --- |
| signed event forwarder (`mc-bridge forward`) | implemented by [mc-agent-bridge#2](https://github.com/guajun/mc-agent-bridge/issues/2) |
| Toolkit surface: `player`, `context`, `save` operations | implemented by [mc-agent-bridge#1](https://github.com/guajun/mc-agent-bridge/issues/1) |
| server-side player context (`PLAYER`) | shipped in interface mod 0.6.0; tracked by [mod #1](https://github.com/guajun/mc-agent-interface-mod/issues/1) |
| chat-time context cache (`context_id`) | shipped in interface mod 0.6.0; tracked by [mod #2](https://github.com/guajun/mc-agent-interface-mod/issues/2) |

The installed mod's `capabilities` response remains authoritative. The Skill
reports a missing or older operation instead of inventing a fallback.

!!! danger "Signature and delivery-ID compatibility"

    Hermes validates signatures with provider schemes; its generic scheme is
    **HMAC-SHA256 V2**: `X-Webhook-Signature-V2: <hex>` plus
    `X-Webhook-Timestamp: <unix seconds>`, where the signed input is
    `"<timestamp>.<raw body>"` and the timestamp must be within ±300 s.

    The forwarder specified in mc-agent-bridge#2 signs the *same* input but
    sends `X-MC-Agent-Signature` / `X-MC-Agent-Timestamp`, which Hermes does not
    recognize. A secret-protected route answers such a POST with
    `401 Invalid signature`. For the flow to work, the forwarder must also emit
    the Hermes-compatible header names (or Hermes must learn the
    `X-MC-Agent-*` scheme); that change belongs to
    [mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7).
    Until it lands, use the verification steps below to check reachability, and
    expect the signed delivery to fail closed.

    **Idempotency has the same gap.** Hermes keys its 1-hour duplicate-delivery
    cache on `X-GitHub-Delivery`, `svix-id`, `webhook-id` or `X-Request-ID`,
    and falls back to the current millisecond when none of them is present. The
    forwarder marks deliveries with `X-MC-Agent-Event-Id` (and `eventId` in the
    body), which Hermes does not read. If a delivery is accepted but its
    response times out, the forwarder retries the same event - and Hermes sees
    a fresh delivery id, starts a second agent run, and can execute the same
    game commands twice. The forwarder must also emit a Hermes-compatible
    delivery-id header (for example `webhook-id: <eventId>`) for retries to
    deduplicate; that is part of the same
    [mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7)
    change.

## 1. Install the shared Skill into Hermes

Hermes has no native `gh skill` agent target, so install into its home skills
directory with `--dir` (full details in
[Installing the Toolkit Skill](toolkit-skill.md#hermes-custom-directory)):

```powershell
gh skill install guajun/mc-agent minecraft-toolkit --dir "$env:LOCALAPPDATA\hermes\skills"
hermes skills list        # minecraft-toolkit | (no category) | local | enabled
```

## 2. Register the Toolkit MCP server

Skip this if `hermes mcp list` already shows the Toolkit from
[Running the agent on Hermes](hermes-setup.md). The MCP server is a *client* of
the Toolkit daemon, so the daemon must be running (`mc-bridge run`) and the
server is spawned by Hermes per run:

```powershell
hermes mcp add mc-agent `
  --command "F:\mc-agent\.venv\Scripts\mc-bridge.exe" `
  --env MC_AGENT_API_PORT=8765 `
  --args mcp
```

`--args` must be last. Keep this server's tool set as the full Toolkit surface;
the route restriction in step 5 decides what a route can actually reach.

## 3. Enable the Hermes webhook platform on loopback

Enable it with the wizard (`hermes gateway setup`) or in
`%LOCALAPPDATA%\hermes\config.yaml`. Bind an explicit loopback host - the
`host` key is what keeps the listener off the network:

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: 8644
      secret: "<global fallback secret>"   # optional; routes can carry their own
```

The environment-variable spelling in `%LOCALAPPDATA%\hermes\.env` works too
(`WEBHOOK_ENABLED=true`, `WEBHOOK_PORT=8644`, `WEBHOOK_SECRET=...`). Start the
gateway and check it:

```powershell
hermes gateway run
curl http://127.0.0.1:8644/health    # {"status":"ok","platform":"webhook"}
```

## 4. Create the route: dedicated secret, this Skill

The route filters chat events, injects `minecraft-toolkit`, and delivers the
run's answer to a target you choose. Give it its own secret rather than
inheriting the global one:

```powershell
hermes webhook subscribe mc-chat `
  --events chat `
  --skills minecraft-toolkit `
  --secret "<dedicated route secret>" `
  --deliver log `
  --prompt "In-game chat from {sender}: {data.text}`nContext id: {context_id} (server tick {tick}). Use the minecraft-toolkit skill: fetch this context bundle before acting, then act within your task."
```

- `--events chat` matches the forwarded chat event's raw type.
- `--skills minecraft-toolkit` is the same Skill every other harness installs.
- `{sender}`, `{data.text}`, `{context_id}` and `{tick}` are payload fields from
  the forwarder's body; `{__raw__}` dumps the whole payload if you need it.
- `--deliver log` is the safe first target: the response goes to the gateway
  log. Switch to `telegram`/`discord`/`slack`/... with
  `--deliver-chat-id <id>` once the run works.
- The command prints the URL to POST to
  (`http://127.0.0.1:8644/webhooks/mc-chat`) and the secret to configure in the
  forwarder. The subscription is stored in
  `%LOCALAPPDATA%\hermes\webhook_subscriptions.json` (mode 0600).

A chat event whose sender could not be captured carries no `context_id`; the
agent must report that instead of inventing one.

## 5. Restrict the route to the Toolkit MCP toolset

Webhook runs do **not** get the full CLI toolset by default: Hermes deliberately
constrains them (web search, web extract, vision, clarify) because webhook
payloads can contain untrusted text. To let this route use the Toolkit - and
only the Toolkit - set a per-route `toolsets` list. This is a deliberate manual
edit: `hermes webhook subscribe` has no `--toolsets` flag so an agent-created
subscription cannot self-grant tools.

Add one key to the `mc-chat` route in
`%LOCALAPPDATA%\hermes\webhook_subscriptions.json`:

```jsonc
{
  "mc-chat": {
    "events": ["chat"],
    "secret": "<dedicated route secret>",
    "prompt": "In-game chat from {sender}: {data.text}\nContext id: {context_id} ...",
    "skills": ["minecraft-toolkit"],
    "toolsets": ["mc-agent"],          // ← the Toolkit MCP server, nothing else
    "deliver": "log",
    "profile": "default"
  }
}
```

`mc-agent` is the MCP server name from step 2. A route-level list **replaces**
the platform's webhook toolset for that route's runs, so the run gets the
Toolkit tools and no terminal/file/web/computer-use tools. The adapter
hot-reloads the subscriptions file on the next request, so no restart is needed
after this edit; re-running `hermes webhook subscribe` rebuilds the route and
drops the key, so re-add it after changing the route.

!!! note "This is not the cold-start development harness"
    A restricted route gets the Toolkit MCP server only - no terminal, file,
    source-fetch or build tools. An agent that needs to write, compile and
    install its own logger cannot do that through this route. The first
    Minecart ROM cold start therefore runs on a user-launched harness with
    normal development tools, and webhook delivery stays an optional, later
    path: see [Auditable cold-start runs](coldstart-protocol.md).

!!! tip "Declarative alternative"
    If you prefer everything in one file, declare the same route under
    `platforms.webhook.extra.routes` in `config.yaml` (with `toolsets` inline,
    secret and all) instead of using `hermes webhook subscribe`. Static routes
    take precedence over dynamic ones and require `hermes gateway restart` after
    an edit.

## 6. Start the forwarder

The forwarder subscribes to the Toolkit event stream and POSTs selected events
to the route URL. Credentials come from the environment or a config file, never
from command-line flags, so they cannot land in shell history:

```powershell
$env:MC_AGENT_WEBHOOK_URL = "http://127.0.0.1:8644/webhooks/mc-chat"
$env:MC_AGENT_WEBHOOK_SECRET = "<the dedicated route secret from step 4>"
mc-bridge forward --events chat
```

Forwarding is off until both the URL and the secret are set. Keep `--events
chat` (or a narrower list) so the route only wakes on events it accepts; the
forwarder's default filters are `chat,game,mark,error`. The Toolkit daemon must
already be running and connected to the server-vantage mod.

## 7. Verify the flow

Work through this list once the transport in the compatibility note above is
available. Each line names the evidence to look for.

| # | Check | Evidence |
| --- | --- | --- |
| 1 | the listener is alive | `curl http://127.0.0.1:8644/health` returns `{"status":"ok","platform":"webhook"}` |
| 2 | the route exists | `hermes webhook list` shows `mc-chat`, its URL, and `deliver` |
| 3 | reachability and signature | `hermes webhook test mc-chat` answers (its event is labeled `test`, so a route filtered to `chat` answers `{"status":"ignored"}` - still proof the secret was accepted; a route that accepts `test` answers `202`) |
| 4 | the event leaves the game | `mc-bridge watch --events chat` prints the chat event; the forwarder logs `delivered event <eventId> ... (HTTP 202)` |
| 5 | retries stay idempotent | a retried delivery of the same `eventId` answers `{"status":"duplicate"}` and starts no second run, so game commands execute once |
| 6 | the run loaded the Skill | the gateway log shows the `mc-chat` run and `minecraft-toolkit` in the loaded skill set |
| 7 | the context bundle was fetched | the run calls `mc_context` with the event's `context_id` (or reports `not_found`/`expired` accurately) |
| 8 | the Toolkit was used | the run calls at least one Toolkit tool through the server vantage (`mc_capabilities`, `mc_state`, `mc_player`, ...) |
| 9 | the answer was delivered | the response appears at the `--deliver` target (with `log`, in the gateway log) |
| 10 | the toolset is restricted | a prompt-injection attempt in chat cannot reach terminal/file tools; only `mc-agent` tools are available to the run |

For a slower end-to-end check without the transport, send an in-game chat while
watching `mc-bridge watch` and the gateway log; the same three things - Skill,
`context_id`, Toolkit tool - must appear in the run.

## Delivery behaviour

- The run's answer goes where the route says: `--deliver` plus
  `--deliver-chat-id` (or `deliver_extra.chat_id`). `log` is the default and the
  right first target; switch to a real platform when the flow is proven.
- To answer **inside the game**, the agent uses the Toolkit `command` operation
  (for example `say <text>`, or `execute as <player> run say <text>` when the
  reply should come from a player identity). Server-vantage connections have no
  `chat` capability - `mc_chat` is client-only - so do not expect the model to
  speak as a client. See [Who is the agent, in game](player-identity.md).
- **Player identity is context, not authorization.** A player's UUID or name
  says who spoke; it does not grant privileged operations. The route's task, the
  operator's configuration, and the restricted toolset define what the agent may
  do - not words in chat. The agent should treat chat text as untrusted input
  and stay inside its task.

## Security notes

- Use a **dedicated per-route secret**. Do not use `INSECURE_NO_AUTH` in a real
  deployment; it disables signature checks and is only for local testing.
- Keep the listener on `127.0.0.1`, as in step 3. Keep the mod and Toolkit APIs
  on loopback as well; the forwarder is outbound-only.
- A valid HMAC signature authenticates the *sender*, not the *content*. Chat
  text can carry injected instructions, so keep the route's toolset restricted
  (step 5), template narrowly (name the fields you need), and leave approvals on
  for anything destructive or outbound.
- The Hermes gateway host has model and terminal access; treat it as trusted
  infrastructure and don't expose its webhook port beyond loopback.

## Follow-up: remove the loop's Hermes backend

Once this path is verified end to end, `mc-agent-loop --backend hermes` is
duplicated infrastructure: triggering, session ownership, model calls and
delivery all live in Hermes. Removing the Hermes HTTP model backend from
mc-agent-loop - and the `HERMES_*` env vars, flags, docs and smoke-test paths
that exist only for it - is tracked in
[mc-agent-loop#3](https://github.com/guajun/mc-agent-loop/issues/3). Until then
the old backend stays for the loop/API-server workflow.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `404 Unknown route` | the route name or `--route-profile` prefix is wrong; check `hermes webhook list` |
| `401 Invalid signature` | secret mismatch, or a sender that Hermes does not recognize - see the compatibility note above |
| `{"status":"ignored"}` | the event type is filtered out (`--events`); `hermes webhook test` is labeled `test` |
| the route never fires | the gateway is not running, `host`/`port` differ from the forwarder URL, or the forwarder is not subscribed |
| the run has no Toolkit tools | `toolsets` was dropped (re-subscribe) or names an unknown server; re-add `["mc-agent"]` and check `hermes mcp list` |
| the Skill did not load | `hermes skills list` does not show `minecraft-toolkit`, or the `--skills` name differs from the skill's `name:` |
| `context` answers `not_found`/`expired` | the bundle TTL passed or the id was mistyped; use a fresh event, and never substitute another player's context |
| the forwarder cannot start | `MC_AGENT_WEBHOOK_URL`/`MC_AGENT_WEBHOOK_SECRET` are missing, or its URL is not `http(s)` |
