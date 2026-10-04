# Install and first run

Read your Minecraft world's state from a terminal, then connect the same tools
to your agent. The terminal check needs no model account. A single-player world
or a dedicated server both work; the game server only needs the mod, and the
binary runs where your agent runs.

## Before you start

| You need | Check |
| --- | --- |
| Minecraft 26.2, Fabric Loader 0.19.5+, Fabric API 0.161.0 | Launch that profile once and confirm it opens. |
| Java 25 for the game | The Fabric server/client profile starts normally. |
| A supported machine for the binary: Windows amd64, Linux glibc amd64 or macOS arm64 | See [Installation reference](install.md#supported-platforms). |
| An agent application, for the final step | Any harness that can run a command (Codex, Claude Code, Hermes, a shell script). |

Python and Go are **not** required to run the released toolkit. There is no MCP
server and nothing to register in an MCP settings file.

## 1. Install the game mod { #1-install-the-game-mod }

Download the latest `mc-agent-interface-0.8.0.jar` and its `checksums.txt` from
the [mod releases](https://github.com/guajun/mc-agent-interface-mod/releases),
verify the SHA-256, and copy the jar next to Fabric API in the instance's
`mods/` directory:

=== "Windows · PowerShell"

    ```powershell
    # Verify the download (compare with the published checksums.txt)
    (Get-FileHash -Algorithm SHA256 .\mc-agent-interface-0.8.0.jar).Hash.ToLower()
    Copy-Item .\mc-agent-interface-0.8.0.jar "C:/Minecraft/instances/my-world/mods/"
    ```

=== "macOS / Linux"

    ```bash
    # Linux
    sha256sum mc-agent-interface-0.8.0.jar   # compare with checksums.txt
    # macOS (no GNU sha256sum by default)
    shasum -a 256 mc-agent-interface-0.8.0.jar
    cp mc-agent-interface-0.8.0.jar /path/to/instance/mods/
    ```

If there is no release for your version yet, [build the mod](mod-building.md)
and put exactly one interface jar in `mods/`, alongside a matching Fabric API
jar.

The mod has two adapters, and the one you need depends on the game shape:

| Adapter | Where the game listens | Enable |
| --- | --- | --- |
| **Control transport** (recommended) | the actual Minecraft game port, shared with players | add `-Dmcagent.control=true` to the JVM arguments |
| **Legacy loopback adapter** | a separate loopback port | on by default; used for the explicit single-player local path |

For a dedicated server, add the flag to `user_jvm_args.txt` (Fabric) and start
the server. For a client, add the same flag to the JVM arguments of the
instance.

**Success:** the instance directory contains
`mc-agent-server/control/fingerprint.txt` with a `sha256:...` line, and the
server console answered your first `/mcagent control` command.

### Credentials

Control credentials are minted on the server console by the **owner**
(permission level 4):

```
/mcagent control status
/mcagent control token add <label> read|write|read+write [ttlSeconds]
/mcagent control token list
/mcagent control token revoke <id>
```

The secret is shown exactly once, as `mca1.<id>.<base64url>`. A `read`
credential is required for observation (state, entities, player/chat context,
snapshots, events); `write` adds `command`, `mark` and `snapshot`. A write-only
credential receives no events and no replay. If the server has
no usable credential yet, the mod issues one bootstrap `read+write` credential
and writes it to `<gameDir>/mc-agent-server/control/bootstrap-token.txt`
(owner-only). Move it into the daemon (stdin) and revoke the bootstrap
credential after issuing per-daemon credentials. A `read` credential can
observe; `write` adds `command`, `mark` and `snapshot`. Chat identity is never
authorization - permissions come from the credential.

## 2. Install the binary and the Skill { #2-install-the-toolkit }

=== "Windows amd64 · PowerShell"

    ```powershell
    iwr -useb https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.ps1 -OutFile install.ps1
    ./install.ps1 -Version 0.5.0 -SkillHarness codex
    ```

=== "Linux amd64 · shell"

    ```bash
    curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
        | sh -s -- --version 0.5.0 --skill-harness codex
    ```

=== "macOS arm64 · shell"

    ```bash
    curl -fsSL https://raw.githubusercontent.com/guajun/mc-agent-bridge/v0.5.0/install/install.sh \
        | sh -s -- --version 0.5.0 --skill-harness codex
    ```

Replace the skill target with your harness (`claude-code`, `universal`,
`hermes`) or use `--skill-dir DIR` for a custom directory; `--no-skill` skips
it. The installer verifies the published checksum, refuses to overwrite an
existing Skill directory unless `--update-skill` is given, and prints the
`version` and `doctor` commands. It does not modify PATH unless you pass
`--add-to-path`; for the current shell run
`export PATH="$HOME/.mc-agent/bin:$PATH"` (Windows:
`$env:Path = "$env:LOCALAPPDATA\mc-agent\bin;$env:Path"`). See
[Installing the Toolkit Skill](toolkit-skill.md) for the mapping and the
manual alternative.

**Success:** `mc-agent version` prints
`mc-agent 0.5.0 (control protocol 1, mod >= 0.8.0)`.

For scripts, run `mc-agent --pretty version`: it returns JSON fields
`version`, `controlProtocol`, and `modMinVersion` plus runtime metadata.
Finite data commands return one JSON value on stdout. `version` defaults to
text, `help` prints text on stderr and exits 0, `events --follow` streams JSON values, and
`daemon run` stays in the foreground without a final result. Errors are JSON
on stderr with a non-zero exit code; check that code before accepting a result.
Usage failures may also print help text on stderr; invoking without a command prints only help
and exits 2. Do not assume all stderr is a single JSON value.

## 3. Check the connection { #3-check-the-connection }

### Dedicated server or LAN world

Register the target using the mod's fingerprint. The address is the **actual
game port**; on a LAN world it is the published port shown in chat, not an
assumed 25565.

=== "Windows · PowerShell"

    ```powershell
    $fp = (Get-Content "C:/Minecraft/server/mc-agent-server/control/fingerprint.txt").Trim()
    Get-Content "C:/Minecraft/server/mc-agent-server/control/bootstrap-token.txt" |
        mc-agent target add dedicated --transport remote --address 127.0.0.1:25565 `
            --pin $fp --token-stdin --default
    mc-agent daemon start
    mc-agent doctor
    mc-agent state
    ```

=== "macOS / Linux"

    ```bash
    fp="$(cat /srv/minecraft/mc-agent-server/control/fingerprint.txt)"
    cat /srv/minecraft/mc-agent-server/control/bootstrap-token.txt |
        mc-agent target add dedicated --transport remote --address 127.0.0.1:25565 \
            --pin "$fp" --token-stdin --default
    mc-agent daemon start
    mc-agent doctor
    mc-agent state
    ```

On a remote server, replace `127.0.0.1` with the server host and use a
credential minted for that daemon. Never paste the credential on the command
line where it would be logged; `--token-stdin`, `--token-env` and
`--token-file` avoid that.

| Check | Success looks like |
| --- | --- |
| `mc-agent doctor` | the `version`, `targets`, `credential`, `daemon` and `target:dedicated` checks are green |
| `mc-agent capabilities` | the connected mod's supported and unsupported operations |
| `mc-agent state` | world data and the online player list, not a connection error |

**You are connected.** These checks only inspect the connection and world.

### Single-player without opening LAN

Use the explicit legacy loopback adapter, which reads the port the mod's server
adapter bound:

```bash
mc-agent target add singleplayer --transport legacy \
    --server-dir "/path/to/instance" --vantage server --default
mc-agent daemon start
mc-agent doctor
mc-agent state
```

Publishing the single-player world to LAN switches to the recommended path:
the control transport listens on the actual published port, and a `remote`
target is built from the LAN host's fingerprint exactly as above.

## 4. Connect your agent { #4-connect-your-agent }

The harness calls the same `mc-agent` command; there is no MCP registration.
Give the agent the Toolkit Skill and a first task:

> Check the Minecraft connection with `mc-agent version`, `doctor`,
> `capabilities` and `state`. Summarize the world and online players. Do not
> change the world.

**Success:** the agent runs the commands and summarizes actual world data. The
[Toolkit Skill](toolkit-skill.md) teaches the discovery, identity, context,
cursor and unknown-write workflow, including what to do when an operation is
not advertised.

## Something didn't work? { #something-didnt-work }

| Symptom | Try this first |
| --- | --- |
| No `mc-agent-server/control/fingerprint.txt` | add `-Dmcagent.control=true` and restart the game/server; confirm the mod and Fabric API loaded |
| `/mcagent` says the control transport is disabled | the flag is missing from the JVM arguments of that instance |
| `doctor` reports `daemon_not_running` | `mc-agent daemon start`; check `mc-agent daemon status` and the daemon log |
| `connection_failed` or a pin/auth error | re-read `fingerprint.txt`; re-mint or re-copy the credential; verification is always on |
| `target_unknown` | `mc-agent target list`; pass `--target NAME` or mark a target `--default` |
| A write timed out with `resultUnknown` | do not repeat it; check `mc-agent request-status <id>` and the `requests` ledger |
| The agent replies to chat but nothing is delivered in game | the daemon never posts replies; configure the delivery path in your harness (see [Hermes unattended](hermes-unattended.md)) |

[More troubleshooting](troubleshooting.md) · [Installation reference](install.md)

## Next steps

- [Tool reference](tools.md) — the CLI command surface and capability model.
- [Architecture](concepts.md) — components, deployment choices and boundaries.
- [Unattended Hermes](hermes-unattended.md) — signed webhook events, uptime and reply delivery.

Stop the daemon with `mc-agent daemon stop`. Next time, open your world and
repeat step 3; installation and Skill setup are one-time steps.
