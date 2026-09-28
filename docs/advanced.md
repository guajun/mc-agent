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

The signed event sender and Skill are available, and the webhook path is
verified against the local receiver shipped in `tools/webhook_receiver.py`.
Forwarding into a Hermes generic route uses the documented `hermes-v2` shim;
verify it in your gateway version. The optional legacy `mc-agent-loop` is
described in the Hermes guide and depends on the Python bridge.

## Worlds and experiments

| Goal | Guide |
| --- | --- |
| Understand entity snapshots and legacy world forks | [World-fork protocol (legacy)](protocol-snapshot.md) |
| Check a restored fork | [Fork verification](fork-verify.md) |
| Provision an isolated server | [Headless lab servers](lab-server.md) |
| Build observation code for a lab | [Building a mod](mod-building.md) |

Remote paths are never local paths. `snapshot` writes entity-order records on
the game host; `fork`/`restore` remain local legacy Python tooling and are
refused for remote targets. Full freeze/re-attach guarantees are not claimed.

## Integration and evidence

### Verified example: Minecart ROM { #verified-example }

[Issue #13](https://github.com/guajun/mc-agent/issues/13) and its child issues
#14–#21 are complete. All three stages were run, independently reviewed and
merged on 2026-09-26:

| Stage | Accepted result | Evidence and reproduction |
| --- | --- | --- |
| Toolchain readiness | Live gate **8/8 PASS** | [Acceptance runbook](minecart-rom-runbook.md) |
| Autonomous agent cold start | Agent-written logger, 5 operated/captured carts, **5/5 audit PASS** | [Original cold-start evidence](evidence/rom20-coldstart/README.md) |
| Frozen real-game regression | **3 fresh instances, 2 programs, 1 cold download, 8 negatives** | [Regression guide](rom21-regression.md) · [Committed evidence](evidence/rom21-regression/README.md) |

The cold start records the original autonomous model run. The regression reuses
its successful recipe without a model and is evidence of repeatability. The
[runbook](minecart-rom-runbook.md) separates offline verification, the recorded
demo and fresh live-game runs.

### Technical runbooks

These are developer and experiment runbooks; they are not required to install
the Toolkit or connect an agent. They document the MCP-era experiment tooling
and are kept as historical records (MCP was removed in bridge #12).

- [Auditable cold-start runs](coldstart-protocol.md)
- [Read-only minecart audit mod](minecart-audit.md)
- [Stage-one integration gate](stage1-gate.md)
- [Stage-one integration run](stage1-integration.md)
- [Minecart ROM acceptance runbook](minecart-rom-runbook.md)
- [Minecart ROM regression](rom21-regression.md)

## Design and contributing

- [Architecture and terminology](concepts.md) — current component boundaries.
- [RFC 0001](rfc/0001-agent-interface.md) — historical interface and daemon design.
- [RFC 0002](rfc/0002-programmable-tool-calls.md) — deferred programmable tool calls.
- [Documentation development](contributing-docs.md) — preview and check both languages.
