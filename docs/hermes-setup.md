# Hermes and the Toolkit

[中文](https://guajun.github.io/mc-agent/zh/hermes-setup/)

Hermes is one possible harness. It does not get a separate Minecraft backend:
it uses the same CLI-only Minecraft Agent Toolkit as Codex, Claude Code and
every other caller. There is no MCP server to register.

## Two integration paths

| Path | What starts a run | What must keep running |
| --- | --- | --- |
| **User-driven** | a user asks Hermes to inspect or change the world | the game + the `mc-agent` daemon while Hermes calls the CLI |
| **Unattended** | the daemon's signed webhook reaches a Hermes route | the game, the daemon, the receiver and the Hermes gateway |

## Status of the pieces

| Part | Status |
| --- | --- |
| server-vantage Toolkit, player lookup and chat context bundles | implemented |
| Go CLI/daemon with same-port control transport | implemented (control protocol 1) |
| portable Toolkit Skill and installation guide | implemented; this repository is the single source |
| receiver-neutral signed webhook sender | implemented; verified against a local receiver |
| Hermes webhook route, Skill subscription and reply delivery | user-owned configuration; the route must be allowed to run the CLI |
| Hermes' generic HMAC V2 route scheme vs `X-MC-Agent-*` headers | a documented compatibility shim (`webhook_receiver.py --scheme hermes-v2`); verify in your gateway |
| direct Hermes HTTP backend in mc-agent-loop | legacy compatibility path, depends on the Python bridge; removal tracked in [loop #3](https://github.com/guajun/mc-agent-loop/issues/3) |

## User-driven setup

1. Install the binary and point it at the world
   ([Install and first run](getting-started.md)).
2. Install the [Toolkit Skill](toolkit-skill.md) so Hermes knows the discovery,
   identity, cursor and unknown-write workflow.
3. Let Hermes run the CLI through its normal command execution. Prompt example:

   > Check the Minecraft connection with `mc-agent version`, `doctor`,
   > `capabilities` and `state`, then summarize the world and online players.
   > Do not change the world.

The daemon is not restarted by each run; it reconnects on its own and reports
gaps. Hermes owns the session, the model calls and the conversation; the
Toolkit is a model-free tool surface.

## Uptime expectations

- The game must run with the mod for any call to work. When the game stops, the
  interface stops; the daemon waits and reconnects.
- User-driven: the daemon must be running while Hermes calls the CLI.
- Unattended: the daemon, the webhook receiver (or Hermes' own listener) and the
  Hermes gateway must all stay up. Webhook delivery is in memory: queued events
  are lost if the daemon restarts.
- The daemon never calls a model and never manages a Hermes session. It does
  not post agent answers into Minecraft; that is a route/delivery concern.

## Unattended path

The daemon forwards selected events to one HTTP(S) receiver with an
HMAC-SHA256 signature. A local, harness-neutral receiver is included for
verification, and it can forward verified events to a Hermes generic webhook
route. The full walkthrough is
[Unattended Hermes: webhook + Skill](hermes-unattended.md).

The short version:

```bash
# receiver (verifies signatures, prints events; optional forwarding)
python tools/webhook_receiver.py serve --secret test-secret --port 8645

# daemon (foreground or under a service manager; `daemon start` has no webhook flags)
mc-agent daemon run --webhook-url http://127.0.0.1:8645/hook \
    --webhook-secret test-secret --webhook-events chat,game,mark,error
```

On the Toolkit side, permissions come from the control credential: give an
observing route a `read` credential and only grant `read+write` when the task
really needs `command`/`mark`/`snapshot`. Chat text is untrusted input and is
never authorization.

## Security checklist

- Keep the game control port firewalled like the game itself; the daemon's
  local IPC stays on loopback.
- Use a dedicated high-entropy webhook secret; verify signature and timestamp;
  de-duplicate the stable event id across retries.
- Give the route only the tool access it needs. Running the CLI requires
  process execution; keep web/file/computer tools off that route and template
  the prompt narrowly.
- Use a dedicated, least-privilege control credential; revoke it when the
  deployment ends.
- Treat UUID/name and chat text as context, not authorization.
- Configure reply delivery explicitly in Hermes; never infer it from sender
  identity alone.
