# Player identity and game context

[中文](https://guajun.github.io/mc-agent/zh/player-identity/)

The Toolkit does not have one permanent player body. On the default server
vantage it can resolve any online player and return that player's server-known
context. Which player is relevant comes from the user request, Harness identity
mapping or an in-game event.

## Current caller context

Use a stable UUID when the Harness knows it; names are accepted as a convenience:

~~~powershell
mc-bridge call player '{"player":"<uuid-or-name>"}'
~~~

The result includes identity, dimension, position, velocity, rotation, health,
game mode, eye position and a server-side view ray. It is authoritative server
state, not a client crosshair or render interpolation.

For a game-chat task, the event can include **context_id**. That bundle freezes
the sender's compact context when the server received the message:

~~~powershell
mc-bridge call context '{"id":"<context_id>"}'
~~~

Fetch it early because the cache is bounded and expires entries. Then use
**state**, **entities** or other Toolkit calls for any fresh information needed
while the Agent works.

Identity is not authorization. A matched UUID/name or context bundle never
proves that the sender may execute commands, change files or control another
player. The Harness/operator owns that policy.

## A body for the Agent

Context answers “whose request and viewpoint is relevant”; it does not create a
player controlled by the Agent. When an experiment needs a visible body, a
Carpet fake player is usually the cheapest option:

~~~powershell
python tools/fake_player.py spawn agent 103 95 52
python tools/fake_player.py action agent look north
python tools/fake_player.py action agent jump
python tools/fake_player.py status agent
python tools/fake_player.py say "hello" --as agent
python tools/fake_player.py kill agent
~~~

A fake player is a real server-side player entity for pressure plates, mob
targeting and chunk loading. It has no client screen or camera, and it requires
Carpet plus command permission. The Toolkit can drive it with commands and read
its authoritative state by name/UUID.

## User-driven Harnesses

Codex, Claude Code and other user-driven Harnesses do not need a game-chat
trigger or a dedicated backend. The user starts the Harness, identifies the
relevant player if needed, and the Harness pulls context from the Toolkit.

If a response should appear in Minecraft, the Harness must explicitly choose a
delivery mechanism, such as a server **tellraw** command. Do not infer a reply
target solely from an untrusted display name.

## Unattended events

A receiver such as Hermes can be awakened through the separately configured,
signed event webhook. The event supplies sender metadata and possibly a
**context_id**; Hermes still uses the same Toolkit for reads and actions.
Webhook routing, authorization and reply delivery are Harness configuration,
not player identity features.

The route and delivery setup is documented in
[Hermes unattended operation](hermes-unattended.md). Signed end-to-end delivery
still fails closed until the header and delivery-ID interoperability in
[mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7) lands.

## Legacy client identity

Use client vantage only when the Agent truly needs client-only capabilities,
such as screen state, a client camera, opening a save or joining a server:

~~~powershell
mc-bridge run --vantage client --port-file "C:/path/to/mc-agent/port.txt"
~~~

A second Minecraft client can give an Agent its own account, inventory, screen
and camera, but costs a full client and, on authenticated servers, another
account. Its Toolkit daemon must use a distinct API port and port file.

The optional compatibility **mc-agent-loop** can still respond to chat and
defaults to **@agent**. That loop/client design is not the default Toolkit
architecture and should not be used merely to obtain server-known player
context.
