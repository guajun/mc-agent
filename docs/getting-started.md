# Install and first run

Read your Minecraft world's state from a terminal, then connect the same tools
to your agent. The terminal check needs no model account. A single-player world
is a good starting point if you do not already run a server.

## Before you start

| You need | Check |
| --- | --- |
| Minecraft 26.2, Fabric Loader 0.19+ and Fabric API | Launch that profile once and confirm it opens. |
| JDK 25, including its compiler | `java -version` and `javac -version` report 25, or supply the JDK path below. |
| Python 3.11+ and Git | `python --version` (`python3` on macOS/Linux) and `git --version`. |
| An agent application, for the final step | MCP support or permission to run CLI commands. |

Use one machine for the game endpoint, Toolkit and agent application. Create a
working folder, for example `C:/Minecraft/toolkit-workspace`, and open a terminal
there. Run all commands below from that folder. No virtual environment activation
is needed. Replace the example paths with your own.

## 1. Install the game mod { #1-install-the-game-mod }

The documented installation builds the mod from source. Locate these paths first:

| Setting | Which folder? |
| --- | --- |
| `MinecraftDir` / `MINECRAFT_DIR` | Resource root containing `versions/` and `libraries/`. The builder needs `versions/<version>/<version>.json` and `<version>.jar`. |
| `Version` / `MC_VERSION` | Actual folder name inside `versions/`, such as `26.2-Fabric`. |
| `GameDir` / `GAME_DIR` | Running instance directory containing `mods/` and `saves/`, or the dedicated server directory containing `mods/`. |
| `JdkDir` / `JDK_DIR` | JDK 25 installation containing `bin/javac` or `javac.exe`. |

Launcher instance isolation can make the resource root and game directory
different. The builder looks for Fabric API in the resource root's `mods/` or
`versions/<version>/.fabric/processedMods/`. If neither contains it, also place
a matching Fabric API jar in the resource root's `mods/` for compilation.

A dedicated-server-only folder does not provide this script's client build
resources. Build against an installed client profile, then copy the jar to the
server. [Lab mod builds](mod-building.md) are a separate advanced workflow.

Close the game/server before copying the mod. Edit the four values, then run:

=== "Windows · PowerShell"

    ```powershell
    git clone https://github.com/guajun/mc-agent-interface-mod
    $MinecraftDir = "C:/Minecraft"
    $GameDir = "C:/Minecraft/instances/my-world"
    $Version = "26.2-Fabric"
    $JdkDir = "C:/Program Files/Java/jdk-25"
    python mc-agent-interface-mod/build.py --minecraft-dir "$MinecraftDir" --version "$Version" --jdk "$JdkDir"
    New-Item -ItemType Directory -Force -Path "$GameDir/mods" | Out-Null
    Copy-Item mc-agent-interface-mod/dist/mc-agent-interface-*.jar "$GameDir/mods/"
    ```

=== "macOS / Linux"

    ```bash
    git clone https://github.com/guajun/mc-agent-interface-mod
    MINECRAFT_DIR="/path/to/minecraft"
    GAME_DIR="/path/to/minecraft/instances/my-world"
    MC_VERSION="26.2-Fabric"
    JDK_DIR="/path/to/jdk-25"
    python3 mc-agent-interface-mod/build.py --minecraft-dir "$MINECRAFT_DIR" --version "$MC_VERSION" --jdk "$JDK_DIR"
    mkdir -p "$GAME_DIR/mods"
    cp mc-agent-interface-mod/dist/mc-agent-interface-*.jar "$GAME_DIR/mods/"
    ```

The build ends with `built: ...jar`. Keep **one version** of the interface mod
in the target `mods/`, alongside a matching **Fabric API** jar. Start the Fabric
server, or launch the client and **enter a single-player world**. The title
screen alone does not start the integrated server.

**Success:** `<GameDir>/mc-agent-server/port.txt` appears with a port number.
This same `GameDir` is the `--server-dir` in step 3, including in single-player.
If you joined someone else's multiplayer server, that server also needs the mod
for this server-view setup.

## 2. Install the Toolkit { #2-install-the-toolkit }

In the same working folder:

=== "Windows · PowerShell"

    ```powershell
    git clone https://github.com/guajun/mc-agent-bridge
    python -m venv .venv
    .venv/Scripts/python -m pip install -e "mc-agent-bridge[mcp]"
    .venv/Scripts/mc-bridge --help
    ```

=== "macOS / Linux"

    ```bash
    git clone https://github.com/guajun/mc-agent-bridge
    python3 -m venv .venv
    .venv/bin/python -m pip install -e "mc-agent-bridge[mcp]"
    .venv/bin/mc-bridge --help
    ```

**Success:** the last command displays CLI help. The `[mcp]` extra installs the
adapter used by agent applications. This setup requires no `mc-agent-loop`.

## 3. Check the connection { #3-check-the-connection }

**Terminal A — start the daemon and leave it running.** Set `--server-dir` to
the game directory from step 1, the parent of `mc-agent-server/`:

=== "Windows · PowerShell"

    ```powershell
    .venv/Scripts/mc-bridge run --server-dir "C:/Minecraft/instances/my-world"
    ```

=== "macOS / Linux"

    ```bash
    .venv/bin/mc-bridge run --server-dir "/path/to/minecraft/instances/my-world"
    ```

This process will not return to the prompt while serving connections. Keep the
world open too.

**Terminal B — open another terminal in the same working folder**, then run:

=== "Windows · PowerShell"

    ```powershell
    .venv/Scripts/mc-bridge call status
    .venv/Scripts/mc-bridge call capabilities
    .venv/Scripts/mc-bridge call state
    ```

=== "macOS / Linux"

    ```bash
    .venv/bin/mc-bridge call status
    .venv/bin/mc-bridge call capabilities
    .venv/bin/mc-bridge call state
    ```

| Check | Success looks like |
| --- | --- |
| `status` | `connected: true` and `vantage: server` in the JSON result. |
| `capabilities` | Supported operations from the connected mod; this list determines which tools you can use. |
| `state` | World data returned by the server, rather than a connection error. |

**You are connected.** These checks only inspect the connection and world.
Keep using the CLI, or continue to connect your agent.

## 4. Connect your agent { #4-connect-your-agent }

In your agent application's **MCP server settings**, add a local **stdio** server:

| Field | Value |
| --- | --- |
| Name | `minecraft` (or another recognizable label) |
| Command · Windows | Absolute path to `.venv/Scripts/mc-bridge.exe` in your working folder |
| Command · macOS/Linux | Absolute path to `.venv/bin/mc-bridge` in your working folder |
| Arguments | `mcp` |

For clients accepting an `mcpServers` JSON configuration, replace the command
path in this example. Other clients use their own settings format; the command
and argument stay the same.

```json
{
  "mcpServers": {
    "minecraft": {
      "command": "C:/Minecraft/toolkit-workspace/.venv/Scripts/mc-bridge.exe",
      "args": ["mcp"]
    }
  }
}
```

On macOS/Linux, use `/path/to/toolkit-workspace/.venv/bin/mc-bridge` instead.
Use an **absolute** path because the agent may start in a different directory.
Keep Terminal A running: the MCP adapter connects to the daemon; it does not start it.

Reload the client's MCP servers or start a new agent session. Then send:

> Check the Minecraft connection with mc_status and discover tools with
> mc_capabilities. Read mc_state and summarize the current world and online
> players. Do not change the world.

**Success:** the agent calls the tools and summarizes actual world data.
An agent without MCP can run the same CLI commands from step 3.
Optionally [install the Toolkit Skill](toolkit-skill.md) to teach your agent the
discovery and player-context workflow.

## Something didn't work? { #something-didnt-work }

| Symptom | Try this first |
| --- | --- |
| Build says `missing version json` or `missing client jar` | Check the resource root and actual profile folder name from step 1; both files are required. |
| Build cannot find Fabric API or `javac` | Check the Fabric API locations and JDK path from step 1. |
| No `mc-agent-server/port.txt` | Check that the mod and Fabric API loaded; enter a world or start the dedicated server. |
| `status` says `connected: false` | Check Terminal A's error and `--server-dir`; use the running instance, not the Toolkit checkout or `saves/`. |
| Connection refused when calling `status` | Start the daemon in Terminal A and keep it running. |
| CLI works but the agent has no `mc_*` tools | Check the absolute MCP executable path, `mcp` argument and `[mcp]` install; reload MCP settings. |
| The agent does not reply to game chat | This setup starts requests from your agent application. See the separate [unattended setup](hermes-unattended.md), which has a pending delivery integration issue. |

[More troubleshooting](troubleshooting.md) · [Installation reference](install.md)

## Next steps

- [Tool reference](tools.md) — more CLI calls and MCP operations.
- [Architecture](concepts.md) — deployment choices and component responsibilities.
- [Advanced guides](advanced.md) — events, unattended agents, world forks and experiments.

To finish, stop the daemon with **Ctrl+C in Terminal A**. Next time, open your
world and repeat step 3; installation and MCP registration are one-time setup.
