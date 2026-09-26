# Advanced guides

Already connected? Choose a guide for your next task. For a first installation,
start with [Install and first run](getting-started.md).

## Agent workflows

| Goal | Guide |
| --- | --- |
| Teach an agent to discover tools and resolve players | [Install the portable Toolkit Skill](toolkit-skill.md) |
| Understand live player context and chat-time bundles | [Player identity and context](player-identity.md) |
| Trigger an agent from game events | [Unattended Hermes: webhook + Skill](hermes-unattended.md) |
| Understand Hermes integration and the legacy loop | [Hermes integration status](hermes-setup.md) |

The signed event sender and Skill are available. Signed end-to-end Hermes
delivery still depends on [mc-agent-bridge#7](https://github.com/guajun/mc-agent-bridge/issues/7).
Treat the unattended guide as integration work, rather than the default first-run
path. The optional legacy `mc-agent-loop` is described in the Hermes guide.

## Worlds and experiments

| Goal | Guide |
| --- | --- |
| Fork and restore a world | [World-fork protocol](protocol-snapshot.md) |
| Check a restored fork | [Fork verification](fork-verify.md) |
| Provision an isolated server | [Headless lab servers](lab-server.md) |
| Build observation code for a lab | [Building a mod](mod-building.md) |

## Integration and evidence

These are developer and experiment runbooks; they are not required to install
the Toolkit or connect an agent.

- [Auditable cold-start runs](coldstart-protocol.md)
- [Read-only minecart audit mod](minecart-audit.md)
- [Stage-one integration gate](stage1-gate.md)
- [Stage-one integration run](stage1-integration.md)
- [Minecart ROM acceptance runbook](minecart-rom-runbook.md)

## Design and contributing

- [Architecture and terminology](concepts.md) — current component boundaries.
- [RFC 0001](rfc/0001-agent-interface.md) — historical interface and daemon design.
- [RFC 0002](rfc/0002-programmable-tool-calls.md) — deferred programmable tool calls.
- [Documentation development](contributing-docs.md) — preview and check both languages.
