# Installation reference

[中文](https://guajun.github.io/mc-agent/zh/install/)

**First installation? Follow [Install and first run](getting-started.md).** It
includes OS commands, mod enabling, credential minting and the first
connection. This page covers the supported matrix, placement, upgrades,
uninstall and removal.

## Supported platforms

The released binary is built, packaged and install-verified by the release
workflows on these platforms only:

| Platform | Archive |
| --- | --- |
| Windows 10/11 amd64 | `mc-agent-<version>-windows-amd64.zip` |
| Linux glibc amd64 | `mc-agent-<version>-linux-amd64.tar.gz` |
| macOS 14+ arm64 (Apple silicon) | `mc-agent-<version>-darwin-arm64.tar.gz` |

Windows arm64, Linux arm64, Linux musl/Alpine, macOS amd64 and other OS/CPU
pairs are not tested and not claimed. The installers refuse them explicitly.
The mod is plain Java and runs wherever Minecraft 26.2 with Fabric Loader
0.19.5, Fabric API 0.161.0 and Java 25 runs.

Component placement:

| Component | Requirement | Runs on |
| --- | --- | --- |
| Fabric mod | Minecraft 26.2, Fabric Loader 0.19.5+, Fabric API 0.161.0, Java 25 | dedicated server, LAN host or single-player integrated server |
| `mc-agent` binary | one of the platforms above; no Python, Go or MCP runtime | the environment that executes harness commands: local host, WSL, container or remote machine |
| Daemon | same binary, optional long-lived process | next to the harness or next to the server |

The mod's control socket is on the game's own TCP port and is authenticated;
keep normal firewall hygiene, but no extra public control port is needed. The
daemon's local IPC stays on loopback.

## Fabric mod

Enable the same-port control transport by adding `-Dmcagent.control=true` to
the instance's JVM arguments (Fabric `user_jvm_args.txt` for a server, client
JVM arguments for a client). The legacy loopback adapter stays available and
needs no flag.

| Property | Default | Purpose |
| --- | --- | --- |
| `mcagent.control` | `false` | enable the authenticated control transport on the game port |
| `mcagent.serverDir` | `mc-agent-server` | server adapter state, snapshots and port file |
| `mcagent.serverPort` | `25581` | first loopback port the legacy adapter tries |
| `mcagent.contextCacheSize` | `256` | maximum chat context bundles |
| `mcagent.contextCacheTtlSeconds` | `300` | bundle lifetime |
| `mcagent.dir` / `mcagent.port` | `<gameDir>/mc-agent` / `25580` | legacy client-vantage endpoint |

To upgrade, replace the jar and keep exactly one interface version plus one
matching Fabric API. To remove, delete the jar; remove
`mc-agent-server/` too only when its events and snapshots are no longer needed.

## Binary

Install with the versioned installer (see [Installing mc-agent](https://github.com/guajun/mc-agent-bridge/blob/main/docs/install.md)):

```bash
# Linux amd64 / macOS arm64
curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
    | sh -s -- --version 0.5.0 --skill-harness codex
```

```powershell
# Windows amd64
iwr -useb https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.ps1 -OutFile install.ps1
./install.ps1 -Version 0.5.0 -SkillHarness codex
```

Default locations: `~/.mc-agent/bin` (Linux/macOS),
`%LOCALAPPDATA%\mc-agent\bin` (Windows). The state directory is
`$XDG_CONFIG_HOME/mc-agent`, `~/Library/Application Support/mc-agent` or
`%AppData%\mc-agent`; `MC_AGENT_HOME` and `--home DIR` override it.

Upgrade by running the installer for the new version; the previous binary is
kept as `mc-agent.previous`, and a failed download/checksum leaves the current
install untouched. Uninstall with `--uninstall` (`-Uninstall`) and add
`--remove-skill` / `--purge-state` (`-RemoveSkill` / `-PurgeState`) only when
you want the Skill copy or state removed too.

## Target and transport choices

| Situation | Target |
| --- | --- |
| Dedicated server with `-Dmcagent.control=true` | `--transport remote --address HOST:<game-port> --pin sha256:...` |
| LAN world | `--transport remote --address HOST:<published-port> --pin sha256:...` (read the fingerprint on the host) |
| Single player, no LAN | `--transport legacy --server-dir <instance> --vantage server` |
| Older mod / explicit client vantage | `--transport legacy --vantage client [--port-file FILE]` |

Remote verification is always on (`--pin` or `--ca`); there is no trust-all
mode. Credentials are stored in the state directory and shown only by source
(`store`/`env`/`file`).

## Verify

| Check | Expected result |
| --- | --- |
| server/client log | control transport armed (when enabled), legacy adapter listening |
| `mc-agent-server/control/fingerprint.txt` | `sha256:...` for the instance |
| `mc-agent doctor` | version, home, targets, daemon and per-target TLS/capability checks |
| `mc-agent capabilities` | supported/unsupported operations for the connected mod |
| `mc-agent state` | world data, not a connection error |

## Harness integration

Point the harness at the `mc-agent` executable. There is no MCP server to
register. Install the [Toolkit Skill](toolkit-skill.md) so the agent knows the
discovery, identity, cursor and unknown-write workflow. A harness that cannot
load Skills can still call the CLI directly; the command surface is in
[tools.md](tools.md).

`mc-agent-loop` is not required for user-driven harnesses. It remains an
optional compatibility listener for its echo tests and the temporary Hermes
HTTP path, with an explicit legacy status.

## Removal

| Component | Remove |
| --- | --- |
| Fabric mod | jar from `mods/`; optionally `mc-agent-server/` |
| Binary | `install.sh --uninstall --remove-skill --purge-state` (or the PowerShell equivalent) |
| Harness integration | nothing to deregister; remove the Skill copy if you installed one |
| Lab instances | only the chosen directory under `labs/` |
