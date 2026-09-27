# Hermes and unattended operation

[中文](https://guajun.github.io/mc-agent/zh/hermes-setup/)

Hermes is one possible Harness. It does not get a separate Minecraft backend:
it uses the same local Minecraft Agent Toolkit as Codex, Claude Code and every
other caller.

## Status

| Part | Status |
| --- | --- |
| server-vantage Toolkit, player lookup and chat context bundles | implemented |
| receiver-neutral signed webhook sender | implemented in the Toolkit |
| portable Toolkit Skill and installation guide | implemented; see [Toolkit Skill](toolkit-skill.md) |
| Hermes webhook route, restricted toolset and reply-delivery guide | published in [Hermes unattended operation](hermes-unattended.md) |
| signed webhook and delivery-ID interoperability | pending [mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7) |
| direct Hermes HTTP backend in mc-agent-loop | temporary compatibility path; removal tracked in [loop #3](https://github.com/guajun/mc-agent-loop/issues/3) |

The configuration is documented, but do not treat signed end-to-end webhook
delivery as operational until the interoperability issue is verified.

## Deployment model

Hermes Gateway, the Toolkit and the server-vantage mod endpoint initially run
on the same machine. The mod and Toolkit stay on loopback. Only the Toolkit
forwarder's outbound HTTPS request crosses that boundary.

~~~text
server chat -> mod captures context_id -> Toolkit event buffer
                                           |
                                           +-> signed outbound webhook -> Hermes route
                                                                          |
                                                                          v
Hermes Agent ----------------------- local MCP ------------------------> Toolkit
~~~

Hermes owns its route, session, model, Skill subscription and reply destination.
The Toolkit daemon owns event forwarding and the game connection. Neither
component duplicates the other's job.

## Use the Toolkit interactively today

Start the daemon and register its MCP adapter with Hermes using the MCP
configuration supported by the installed Hermes version:

~~~powershell
mc-bridge run
# Configure Hermes to spawn:
C:/path/to/.venv/Scripts/mc-bridge.exe mcp
~~~

Start a new Hermes session after changing MCP configuration. First call
**mc_status** and **mc_capabilities**. Use **mc_player** for current caller
context and **mc_context** only when a received event supplies a **context_id**.

Install the portable Skill with the commands in
[Installing the Toolkit Skill](toolkit-skill.md). Hermes has no native
**gh skill** target; the verified path installs it into the Hermes custom Skill
directory. Configure the webhook route, restricted MCP toolset and delivery
target with [Hermes unattended operation](hermes-unattended.md).

## Configure the event sender

Toolkit forwarding is off unless both URL and secret are configured. Credentials
belong in the environment or a protected JSON config, never command flags.

~~~powershell
$env:MC_AGENT_WEBHOOK_URL = "https://<receiver>/hooks/mc-agent"
$env:MC_AGENT_WEBHOOK_SECRET = "<dedicated-random-secret>"
mc-bridge forward --events chat,game,mark,error
~~~

The receiver must verify **X-MC-Agent-Signature**, reject stale timestamps and
deduplicate **X-MC-Agent-Event-Id**. Configure the final receiver URL because
redirects are not followed. Restrict the MCP tools available to the Hermes
route and keep privileged command authorization separate from player identity.

A chat payload can include **context_id**. The Agent should fetch it promptly,
handle structured expired/not-found results, then query current state as needed.
Response delivery is a Hermes route concern; the forwarder does not post Agent
answers back into Minecraft.

## Temporary compatibility loop

Until the webhook/Skill workflow is verified, the existing loop can still
listen to chat and call Hermes through its OpenAI-compatible endpoint:

~~~powershell
mc-agent-loop run --backend hermes --env-file .env
~~~

Its default trigger is **@agent**. This path requires the loop package, model
API variables and the client-vantage reply mechanisms documented by that
package. It is retained for compatibility, not the intended final architecture,
and should not be described as a required Toolkit component.

## Security checklist

* Keep mod and Toolkit control sockets on loopback.
* Use a dedicated high-entropy webhook secret.
* Verify signatures and timestamps before parsing or routing.
* Deduplicate delivery IDs because retries reuse the event ID.
* Give the event route only the MCP tools it needs.
* Treat UUID/name as context, not authorization.
* Configure reply delivery explicitly in Hermes; never infer it from sender
  identity alone.
