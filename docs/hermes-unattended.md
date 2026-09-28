# Unattended Hermes: webhook + Toolkit Skill

The event-driven way to run Hermes against Minecraft: a game event wakes a
Hermes agent run that loads the portable [Toolkit Skill](toolkit-skill.md) and
acts through the `mc-agent` CLI. This replaces the old shape where
`mc-agent-loop --backend hermes` owned the trigger **and** a copy of the Hermes
session/model logic.

```text
server-vantage mod ──events──► mc-agent daemon (replay buffer + webhook)
                                    │  signed POST (X-MC-Agent-*)
                                    ▼
                          local receiver / verifier  ──► Hermes Gateway
                          (webhook_receiver.py)          webhook route
                                                            │
                                                            ▼
                                            Hermes agent run: Toolkit Skill
                                            + permission to run the mc-agent CLI
                                                            │
                                             ┌──────────────┴──────────────┐
                                             ▼                             ▼
                                       delivery target            mc-agent command
                                       (log/telegram/...)          into the game
```

## Status and compatibility

- The Toolkit's signed, receiver-neutral webhook is implemented and was
  verified end to end against the local receiver in this repository
  (`tools/webhook_receiver.py`): the daemon delivered signed events, the
  receiver verified the signature, de-duplicated the event id and rejected a
  damaged signature and a stale timestamp.
- The daemon emits `X-MC-Agent-Signature`, `X-MC-Agent-Timestamp`,
  `X-MC-Agent-Event-Id`, `X-MC-Agent-Event-Type` and `X-MC-Agent-Attempt`.
- Hermes' generic webhook scheme is HMAC V2 (`X-Webhook-Signature-V2`,
  `X-Webhook-Timestamp`, signed `"<timestamp>.<raw body>"`) and de-duplicates on
  `webhook-id`/similar delivery ids. The local receiver can forward verified
  events in that scheme. That shim is exercised locally for signing and
  deduplication; verify it against your installed Hermes version before
  relying on it in production.
- The route runs the CLI, not an MCP server. Give it process-execution access
  and nothing more; permissions come from the control credential.

## Deployment assumption

- Hermes Gateway, the receiver and the daemon can run on the same machine; the
  receiver and the Hermes listener bind loopback. Nothing inbound is exposed
  by the Toolkit.
- The mod's control transport is on the game port; the daemon connects out to
  it. No SSH, extra public port or client-mod relay is involved.
- **mc-agent adds no Hermes API/model client.** Hermes owns the session, the
  model calls and the conversation; the Toolkit is a model-free tool surface.
- **The route is user-owned.** mc-agent never adds or edits a Hermes webhook
  subscription; the user configures it in Hermes Gateway.

## 1. Install the shared Skill into Hermes

Hermes has no native `gh skill` agent target; install into its home skills
directory with `--dir`, or use the release installer with
`--skill-harness hermes`:

```powershell
# From the mc-agent release bundle (pinned to the toolkit version)
./install.ps1 -Version 0.5.0 -SkillHarness hermes

# Or with GitHub CLI against the meta repository
gh skill install guajun/mc-agent minecraft-toolkit --dir "$env:HERMES_HOME\skills"
hermes skills list        # minecraft-toolkit | (no category) | local | enabled
```

## 2. Enable the Hermes webhook platform on loopback

Enable it with the wizard (`hermes gateway setup`) or in the Hermes config
file (`config.yaml` under your Hermes home; confirm the location with your
installed Hermes version - it is not fixed across releases). The `host` key is
what keeps the listener off the network:

```yaml
platforms:
  webhook:
    enabled: true
    extra:
      host: "127.0.0.1"
      port: 8644
      secret: "<global fallback secret>"   # optional; routes can carry their own
```

The environment-variable spelling in the Hermes `.env` file (under the Hermes
home) works too
(`WEBHOOK_ENABLED=true`, `WEBHOOK_PORT=8644`, `WEBHOOK_SECRET=...`). Start the
gateway and check it:

```powershell
hermes gateway run
curl http://127.0.0.1:8644/health    # {"status":"ok","platform":"webhook"}
```

## 3. Create the route: dedicated secret, this Skill, CLI access

The route filters chat events, injects `minecraft-toolkit`, and delivers the
run's answer to a target you choose. Give it its own secret:

```powershell
hermes webhook subscribe mc-chat `
  --events chat `
  --skills minecraft-toolkit `
  --secret "<dedicated route secret>" `
  --deliver log `
  --prompt "In-game chat from {sender}: {data.text}`nContext id: {context_id} (server tick {tick}). Use the minecraft-toolkit skill: fetch this context bundle before acting, then act within your task."
```

`{sender}`, `{data.text}`, `{context_id}` and `{tick}` are payload fields from
the daemon's event body; `{__raw__}` dumps the whole payload if you need it.
The route is stored in `webhook_subscriptions.json` under the Hermes home
(mode 0600); confirm the path for your Hermes version.

Then allow the run to execute the CLI. Webhook runs deliberately get a
restricted toolset, so add the process-execution toolset for this route in
`webhook_subscriptions.json` (name it exactly as your Hermes version does; run
`hermes webhook subscribe --help` or `hermes tools list` to check):

```jsonc
{
  "mc-chat": {
    "events": ["chat"],
    "secret": "<dedicated route secret>",
    "skills": ["minecraft-toolkit"],
    "toolsets": ["<the shell/terminal toolset your Hermes version provides>"],
    "deliver": "log",
    "profile": "default"
  }
}
```

Keep web search, web fetch, file and computer-use tools **off** this route:
chat text is untrusted input. If the task only observes the world, use a
`read` control credential so `command`, `mark` and `snapshot` are unavailable
even if the model tries them. The adapter hot-reloads the subscriptions file;
re-running `hermes webhook subscribe` rebuilds the route, so re-add the toolset
after changing it.

## 4. Start the daemon webhook and verify it locally

`daemon start` does not accept webhook flags; run the daemon in the foreground
or under a service manager:

```bash
mc-agent daemon run --webhook-url http://127.0.0.1:8645/hook \
    --webhook-secret test-secret --webhook-events chat,game,mark,error
```

Start the local receiver in another terminal. It verifies the signature and
timestamp, de-duplicates the stable event id and prints one JSON line per
event:

```bash
python tools/webhook_receiver.py serve --secret test-secret --port 8645
```

Self-check the receiver without a daemon:

```bash
python tools/webhook_receiver.py selftest
# webhook receiver selftest passed (accept, deduplicate, reject bad signature, reject stale)
```

Then trigger an event (`mc-agent command "say hello"` writes a `write` event,
which is forwarded when `--webhook-events "*"` is configured) and confirm the
receiver printed it with `X-MC-Agent-Signature` verified.

## 5. Forward verified events to the Hermes route

Either point the daemon directly at the Hermes route (if your Hermes version
accepts the `X-MC-Agent-*` scheme) or forward through the local receiver, which
re-signs in Hermes' generic scheme:

```bash
python tools/webhook_receiver.py serve --secret test-secret --port 8645 \
    --forward-url http://127.0.0.1:8644/webhooks/mc-chat \
    --forward-secret "<dedicated route secret>" --scheme hermes-v2
mc-agent daemon run --webhook-url http://127.0.0.1:8645/hook \
    --webhook-secret test-secret --webhook-events chat
```

The forward mode emits `X-Webhook-Signature-V2`, `X-Webhook-Timestamp` and a
stable `webhook-id`, so retries de-duplicate in Hermes instead of starting a
second run. If the downstream route answers with an error, the receiver returns
`503` and does **not** mark the event delivered, so the daemon retries the same
event id instead of losing it.

## 6. Verify the flow

| # | Check | Evidence |
| --- | --- | --- |
| 1 | the receiver verifies signatures | `webhook_receiver.py selftest` passes |
| 2 | the daemon reaches the receiver | the daemon log shows forwarding; the receiver prints the event with its type/category |
| 3 | retries stay idempotent | a repeated `X-MC-Agent-Event-Id` answers `{"status":"duplicate"}` |
| 4 | the listener is alive | `curl http://127.0.0.1:8644/health` returns `{"status":"ok","platform":"webhook"}` |
| 5 | the route exists | `hermes webhook list` shows `mc-chat`, its URL and `deliver` |
| 6 | reachability and signature | `hermes webhook test mc-chat` answers; a route filtered to `chat` answers `{"status":"ignored"}` (still proof the secret was accepted) |
| 7 | the event leaves the game | `mc-agent events --category chat --follow` prints the chat event |
| 8 | the run loaded the Skill | the gateway log shows the `mc-chat` run and `minecraft-toolkit` in the loaded skill set |
| 9 | the context bundle was fetched | the run calls `mc-agent context <id>` (or reports `not_found`/`expired` accurately) |
| 10 | the CLI was used | the run calls at least one `mc-agent` operation |
| 11 | the answer was delivered | the response appears at the `--deliver` target (with `log`, in the gateway log) |
| 12 | the toolset is restricted | a prompt-injection attempt in chat cannot reach web/file tools; only CLI execution is available |

## Delivery behaviour

- The run's answer goes where the route says: `--deliver` plus
  `--deliver-chat-id`. `log` is the default and the right first target.
- To answer **inside the game**, the agent uses `mc-agent command "say <text>"`
  (or `execute as <player> run say <text>`), which requires a `write`
  credential. Server-vantage connections have no client `chat` operation.
- **Player identity is context, not authorization.** The route's task, the
  credential's permissions and the restricted toolset define what the agent may
  do - not words in chat.

## Security notes

- Use a dedicated per-route secret and a dedicated least-privilege control
  credential; revoke it when the deployment ends.
- Keep the receiver and the Hermes listener on `127.0.0.1`. The Toolkit never
  listens for the receiver; all Toolkit webhook traffic is outbound.
- A valid HMAC signature authenticates the *sender*, not the *content*. Keep
  the route's toolset restricted, template narrowly, and leave approvals on for
  anything destructive or outbound.
- Webhook delivery is in memory and best effort: a daemon restart loses queued
  events, and a receiver outage loses deliveries after retries are exhausted.
  For durable wake-ups, keep the receiver up and treat gaps as gaps.

## Uptime summary

| Component | User-driven | Unattended |
| --- | --- | --- |
| Game + mod | required while the agent works | required while the agent works |
| Daemon | required while the CLI is called | required for the whole session |
| Receiver / Hermes gateway | not required | required for wake-up and replies |
| Credential | read or read+write per task | read for observation; read+write only for commands |

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| The receiver answers `401` | secret mismatch, or a sender that Hermes does not recognize; check the scheme |
| The receiver never prints | the daemon has no `--webhook-url`/secret, the event category is filtered out, or the URL is not HTTP(S) |
| `404 Unknown route` | route name or profile prefix is wrong; check `hermes webhook list` |
| `{"status":"ignored"}` | the event type is filtered out (`--events`); a Hermes test event is labeled `test` |
| The route never fires | gateway not running, `host`/`port` differ from the receiver's forward URL, or the receiver is not forwarding |
| The run cannot use the CLI | the route lacks its process-execution toolset, or `mc-agent` is not on PATH in the gateway environment |
| The Skill did not load | `hermes skills list` does not show `minecraft-toolkit`, or the `--skills` name differs from the skill's `name:` |
| `context` answers `not_found`/`expired` | the bundle TTL passed or the id was mistyped; use a fresh event, never another player's context |

## Follow-up: remove the loop's Hermes backend

Once this path is verified end to end on your deployment,
`mc-agent-loop --backend hermes` is duplicated infrastructure: triggering,
session ownership, model calls and delivery all live in Hermes. Removing the
Hermes HTTP model backend from mc-agent-loop is tracked in
[mc-agent-loop#3](https://github.com/guajun/mc-agent-loop/issues/3). Until then
the old backend stays for the loop/API-server workflow and depends on the
legacy Python bridge.
