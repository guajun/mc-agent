# Toolkit tools

[中文](https://guajun.github.io/mc-agent/zh/tools/)

The Minecraft Agent Toolkit is implemented by **mc-agent-bridge**. Its live
**capabilities** result is authoritative: the MCP surface is filtered to match
the connected mod and vantage.

## CLI

| Command | Purpose |
| --- | --- |
| **mc-bridge discover** | explain server-vantage port discovery without starting the daemon |
| **mc-bridge run** | own the mod connection and serve the loopback API |
| **mc-bridge call <method> [json]** | make one JSON call to the daemon |
| **mc-bridge watch --events chat,game** | stream buffered/live events |
| **mc-bridge mcp** | expose the Toolkit over MCP for a Harness |
| **mc-bridge forward --events ...** | send selected events to one configured signed webhook |

Run **status** and **capabilities** first. Common server-vantage methods are:

| Method | Purpose |
| --- | --- |
| **status**, **capabilities** | connection, discovery and supported surface |
| **state** | authoritative world/tick state |
| **player** | resolve an online player by UUID or name and return live context/view |
| **context** | fetch a bounded chat-time bundle by context ID |
| **entities** | nearby entities |
| **command**, **command_output** | issue a command; the latter also waits for its answer |
| **events** | replay buffered events from a cursor |
| **save** | report world-save metadata |
| **snapshot**, **snapshots** | capture/list entity-order snapshots |
| **fork**, **restore**, **verify**, **order** | copy and validate a world fork |
| **wait**, **mark**, **stop** | timing, annotation and daemon control |

Client-only methods such as **screen**, **chat**, **connect**, **world**, **lan**
and recording appear only after explicitly selecting client vantage and only
when the mod advertises them.

## MCP

MCP tools use the **mc_** prefix: **mc_status**, **mc_capabilities**,
**mc_player**, **mc_context**, **mc_state**, **mc_entities**,
**mc_command_output**, **mc_events**, **mc_snapshot** and so on. The MCP process
is an adapter that connects to the running daemon. It does not own the game
connection.

A Harness should:

1. call **mc_status** and **mc_capabilities**;
2. resolve the relevant player when the request needs player context;
3. fetch only the live world data needed;
4. use **mc_context** promptly when an event supplied a context ID;
5. treat identity as context, not command authorization.

## Event forwarding

The optional forwarder is receiver-neutral. It signs each outbound POST, retries
transient failures and reuses a stable event ID for deduplication. URL and secret
come from environment or a protected config file. It does not run a model,
manage a session or deliver Agent replies.

See [Hermes and unattended operation](hermes-setup.md) and the
[Toolkit reference](https://github.com/guajun/mc-agent-bridge#forwarding-events-to-a-webhook).

## Repository utilities

| Tool | Purpose |
| --- | --- |
| **lab_server.py** | provision and control a headless Fabric lab |
| **fork_verify.py** | inspect, restore and compare recorded forks |
| **fake_player.py** | create and drive a Carpet fake player |
| **game_cmd.py** | run a game command and print feedback |
| **mcp_probe.py** | call one MCP tool against a running Toolkit |
| **launch_instance.py** | launch a client without a GUI launcher |
| **smoke_offline.py** | compatibility test for the old daemon/agent-loop path |

## Legacy agent-loop

**mc-agent-loop** is not part of the Toolkit surface. It remains an optional
compatibility listener with **echo** and temporary **hermes** backends. Its
default chat trigger is **@agent**. Codex and Claude Code run as user-driven
Harnesses and must not be described as loop backends.
