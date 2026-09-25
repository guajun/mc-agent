# Installing the Toolkit Skill

One **portable Agent Skill** teaches any harness how to use the local
server-vantage Toolkit: discover what the connected mod supports, resolve the
caller by stable UUID, fetch a chat-time context bundle by `context_id`, query
authoritative state/entities/snapshots/world-save metadata, and treat player
identity as context rather than authorization. It contains no Hermes, Codex, or
Claude Code session logic - the same instructions work for every harness that
can call MCP or run the Toolkit CLI.

The skill lives in this repository at
[`skills/minecraft-toolkit/SKILL.md`](https://github.com/guajun/mc-agent/tree/main/skills/minecraft-toolkit)
with one supporting reference,
[`references/toolkit-operations.md`](https://github.com/guajun/mc-agent/blob/main/skills/minecraft-toolkit/references/toolkit-operations.md).

## What the skill teaches

| Step | What the agent does |
| --- | --- |
| Discover | calls `mc_capabilities` (MCP) or `mc-bridge call capabilities` (CLI) first; calls only operations the connected mod advertises |
| Resolve | finds the caller in `state` and asks the `player` operation by UUID; a name is a convenience, the reply's UUID is the identity |
| Correlate | takes `context_id` from a pushed game event and fetches the bundle with `context`; never rebuilds the id from a player name |
| Query | requests the minimum it needs: `state`, `entities`, `save` metadata, `snapshots`, or `events` by cursor |
| Act | uses `command` / `command_output` within its task, treats chat identity as context (not authorization), and reports unsupported operations instead of faking them |

The skill is deliberately discovery-first: the Toolkit's capability reply is the
authority, so the instructions stay correct as the mod gains operations.

## Prerequisites

- **GitHub CLI with `gh skill`.** Agent skills in the GitHub CLI are in
  **preview** and may change. These instructions were verified with
  `gh 2.95.0`. Run `gh skill install --help` to see the supported `--agent`
  values and flags in your build.
- **The Toolkit installed locally**, so the agent can actually call it - see
  [Installation](install.md). The skill explains the Toolkit; it does not
  install it.
- If a command asks you to authenticate, run `gh auth login`.

## Install

`gh skill install` discovers skills through the standard `skills/*/SKILL.md`
convention. At project scope it installs into the current repository; at user
scope into your home directory.

### Universal / shared target

```bash
# user scope: available to every harness for this user
gh skill install guajun/mc-agent minecraft-toolkit --agent universal --scope user

# project scope: the shared .agents/skills directory used by GitHub Copilot,
# Cursor, Codex, Gemini CLI, Antigravity, Amp, Cline, OpenCode, Warp and more
gh skill install guajun/mc-agent minecraft-toolkit --agent universal --scope project
```

### One explicit harness

Any value from the supported `--agent` list works. For example, Claude Code:

```bash
gh skill install guajun/mc-agent minecraft-toolkit --agent claude-code --scope user
```

Verified destinations for this skill (Windows reference machine, `gh 2.95.0`):

| Command | Lands in |
| --- | --- |
| `--agent universal --scope user` | `~/.config/agents/skills/minecraft-toolkit/` |
| `--agent universal --scope project` | `.agents/skills/minecraft-toolkit/` (shared) |
| `--agent claude-code --scope user` | `~/.claude/skills/minecraft-toolkit/` |
| `--agent codex --scope user` | `~/.codex/skills/minecraft-toolkit/` |
| `--agent pi --scope user` | `~/.pi/agent/skills/minecraft-toolkit/` |

The exact path form installs the same skill without a repository scan:

```bash
gh skill install guajun/mc-agent skills/minecraft-toolkit --agent universal --scope user
```

### Hermes (custom directory)

Hermes is **not** a native `gh skill` agent target: its name is not in the
supported `--agent` list, and `--agent hermes` is rejected with
`invalid argument "hermes" for "--agent" flag`. Do not expect native support.

Hermes discovers skills by walking its home skills directory, so install into
that directory with `--dir`. `--dir` creates
`<dir>/minecraft-toolkit/SKILL.md`:

=== "Windows (PowerShell)"

    ```powershell
    gh skill install guajun/mc-agent minecraft-toolkit --dir "$env:LOCALAPPDATA\hermes\skills"
    ```

=== "macOS / Linux"

    ```bash
    gh skill install guajun/mc-agent minecraft-toolkit --dir "${HERMES_HOME:-$HOME/.hermes}/skills"
    ```

Hermes homes: `%LOCALAPPDATA%\hermes` on Windows, `~/.hermes` on macOS/Linux
(or whatever `HERMES_HOME` names).

## Verify the install

```bash
gh skill list                       # shows minecraft-toolkit and its target
```

For Hermes, ask Hermes itself:

```bash
hermes skills list                  # minecraft-toolkit | (no category) | local | enabled
```

A harness uses the skill when a task matches its description;
`gh skill preview guajun/mc-agent minecraft-toolkit` shows the content before
you load it into an agent.

!!! note "Keep the skill current"
    Re-run the install with `-f` to replace the copy, or use
    `gh skill update minecraft-toolkit` (`gh skill update --all`) to refresh
    every installed skill. Installed skills carry source metadata in their
    frontmatter, which is what `update` reads.

## Next: unattended Hermes

The skill is one half of the event-driven setup. The other half is a
user-configured Hermes webhook route that loads this skill and restricts the
route to the Bridge MCP toolset: see
[Unattended Hermes: webhook + Toolkit Skill](hermes-unattended.md).
