# Installing the Toolkit Skill

One **portable Agent Skill** teaches any harness how to use the CLI-only
server-vantage Toolkit: discover the connection and capability surface, resolve
the caller by stable UUID, fetch a chat-time context bundle by `context_id`,
read event cursors and gaps, handle unknown write results, and run controlled
in-place experiments. It contains no Hermes, Codex or Claude Code session logic
- the same instructions work for every harness that can run a command.

The Skill lives in this repository at
[`skills/minecraft-toolkit/SKILL.md`](https://github.com/guajun/mc-agent/tree/main/skills/minecraft-toolkit)
with one supporting reference,
[`references/toolkit-operations.md`](https://github.com/guajun/mc-agent/blob/main/skills/minecraft-toolkit/references/toolkit-operations.md).
This repository is the **single authoritative source**: release bundles are
packaged from a pinned commit recorded in `skill-pin.json` in the bridge
release, never from a hand-maintained copy.

## What the skill teaches

| Step | What the agent does |
| --- | --- |
| Discover | runs `mc-agent version`/`doctor`, then `capabilities` and `schema`; calls only operations the connected mod advertises |
| Connect | starts or reuses the daemon; distinguishes a down daemon, a disconnected mod, an unsupported operation and an unknown write result by error code |
| Resolve | finds the caller in `state` and calls `player` by UUID; a name is a convenience, the reply's UUID is the identity |
| Correlate | takes `context_id` from a pushed game event and fetches the bundle promptly; never rebuilds the id from a player name |
| Query | requests the minimum it needs: `state`, `entities`, `save` metadata, `snapshots`, or `events` by cursor |
| Act | uses `command`/`command-output` within its task, treats chat identity as context (not authorization), and reports unsupported operations instead of faking them |
| Recover | treats `event_gap`, `game_restarted` and `truncated` honestly; checks `request-status` instead of replaying a non-idempotent write |
| Experiment | repeats trials at the same dimension and full X/Y/Z; restores and checks the baseline, changes only the declared variable, and records observed outcomes |

The skill is deliberately discovery-first: the capability reply is the
authority, so the instructions stay correct as the mod gains operations.

For experiments, the same X/Z at a different height is not an in-place repeat.
Height/location may vary when explicitly selected as the independent variable.
If baseline restoration cannot be verified, the agent must report the limitation
instead of presenting a relocated test as a controlled comparison.

These are harness-side behavioral instructions. The Toolkit does not enforce
them as command guards. After updating an installed copy, verify the actual run
loads `minecraft-toolkit` and follows the connection and experiment guidance;
an enabled entry in the skill list alone does not establish that.

## Install from the release bundle (pinned)

The bridge installer verifies the Skill bundle against the release checksums
and refuses to overwrite an existing directory without `--update-skill`:

```bash
# explicit harness
sh install.sh --version 0.5.0 --skill-harness codex
# or a custom parent directory; installs <DIR>/minecraft-toolkit/
sh install.sh --version 0.5.0 --skill-dir /path/to/skills
```

| `--skill-harness` | Target |
| --- | --- |
| `codex` | `$CODEX_HOME/skills` or `~/.codex/skills` |
| `claude-code` (`claude`) | `$CLAUDE_CONFIG_DIR/skills` or `~/.claude/skills` |
| `universal` | `$XDG_CONFIG_HOME/agents/skills` or `~/.config/agents/skills` |
| `hermes` | `$HERMES_HOME/skills` or `~/.hermes/skills` |

On Windows use `./install.ps1 -Version 0.5.0 -SkillHarness codex` (or
`-SkillDir DIR`).

## Install with `gh skill`

Harnesses with native Skill support can use their own flow. GitHub CLI skills
are in preview; this was verified with `gh 2.95.0`:

```bash
gh skill install guajun/mc-agent minecraft-toolkit --agent codex --scope user
# or an explicit destination
gh skill install guajun/mc-agent minecraft-toolkit --dir "$HOME/.hermes/skills"
```

For Hermes (no native `gh skill` agent target), install into the Hermes skill
directory with `--dir`: `%LOCALAPPDATA%\hermes\skills` on Windows,
`${HERMES_HOME:-$HOME/.hermes}/skills` on macOS/Linux.

`gh skill` reads the repository branch; the release installer is the path that
is pinned to the exact toolkit version. After updating an installed copy,
verify the new content is actually loaded.

## Prerequisites

- The binary installed and a target configured - see
  [Install and first run](getting-started.md). The Skill explains the Toolkit;
  it does not install it.
- For `gh skill`, an authenticated GitHub CLI (`gh auth login`).

## Next: unattended operation

The Skill is one half of the event-driven setup. The other half is a
user-configured route (for example Hermes) that loads this skill and subscribes
to the daemon's signed webhook: see
[Unattended Hermes: webhook + Skill](hermes-unattended.md).
