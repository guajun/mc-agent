# Installation

What to install, in what order, and how to tell that it worked. The
[Getting started](getting-started.md) page is the tour; this one is the
reference you come back to when something is off.

Nothing here needs a compiler toolchain beyond a JDK, and nothing installs
system-wide.

## What you need

| Piece | Needed for | Where it goes |
| --- | --- | --- |
| **Minecraft 26.2** with **Fabric Loader 0.19+** and **Fabric API** | everything | your instance |
| **Java 25** | building the mod, and running a lab server | the JDK you point `--jdk` at; the game's own runtime is fine for the game |
| **Python 3.11+** | bridge, loop, tools | a virtual environment next to the repos |
| **interface mod jar** | everything | `<instance>/mods/` |
| **an agent runtime** | the agent's mind - [Hermes](hermes-setup.md), or anything that speaks MCP/HTTP; install the portable [Toolkit Skill](toolkit-skill.md) so it knows how to use the server-vantage Toolkit | its own directory |
| **a Fabric server** (optional) | the isolated-lab track | `labs/<name>/`, provisioned by `tools/lab_server.py` |

## 1. Minecraft and Fabric

Use a Fabric instance for 26.2 with **Fabric API** in `mods/`. Two details
matter later:

* the mod writes its state into the **game directory** of the instance
  (`<gameDir>/mc-agent/`), which with per-version isolation is the version
  folder, not `.minecraft`;
* building the mod reads that instance's `libraries/` and
  `.fabric/processedMods/`, so **start the game once** before building.

## 2. The interface mod

### Where it runs

One jar, two entrypoints, and Fabric loads whichever matches:

| Entrypoint | Runs in | Serves |
| --- | --- | --- |
| `client` | any client | what that client sees and can do: screens, opening a save, publishing to the LAN |
| `main` | any server - **including the integrated server inside a single-player world** | authoritative state, console commands, snapshots |

**Single player gets both**, at the same time, in the same process: the client
vantage on `mcagent.port` (25580) and the server vantage on `mcagent.serverPort`
(25581). That is the whole reason the fork workflow works without a dedicated
server - you can snapshot an authoritative world while you are playing it.
The two write to different places: `<gameDir>/mc-agent/` and
`mc-agent-server/` (or wherever `-Dmcagent.dir` / `-Dmcagent.serverDir` point).

Two things to keep in mind when both run in one process: `tick freeze` freezes
the world you are playing in, and anything expensive you run in the server
vantage shares the frame budget of your client.

```bash
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py \
    --minecraft-dir "C:/Users/me/AppData/Roaming/.minecraft" \
    --version 26.2-Fabric \
    --jdk "C:/Program Files/Java/jdk-25"
```

The result is `dist/mc-agent-interface-<version>.jar`. Copy it next to Fabric
API in `<instance>/mods/` and start the game. It worked when the log says:

```
[mc-agent-interface] initialized, dir=<gameDir>/mc-agent basePort=25580
[mc-agent-interface] server vantage armed, dir=mc-agent-server basePort=25581
[mc-agent-interface] listening on 127.0.0.1:25580
```

| Task | How |
| --- | --- |
| **upgrade** | replace the jar; one version at a time, or Fabric will complain about a duplicate mod id |
| **uninstall** | delete the jar. Everything the mod wrote lives in `<gameDir>/mc-agent/` (and `mc-agent-server/` for the server vantage) - delete those too for a clean slate |
| **move the data** | `-Dmcagent.dir=<path>` (client) and `-Dmcagent.serverDir=<path>` (server vantage) |
| **fix the port** | `-Dmcagent.port=25580` (client), `-Dmcagent.serverPort=25581` (server vantage); without them the mod takes the next free port and records it in `port.txt` |

## 3. The Python side

The bridge and the loop are two packages; the loop depends on the bridge, so
install both into one environment:

```bash
git clone https://github.com/guajun/mc-agent-bridge
git clone https://github.com/guajun/mc-agent-loop
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]" -e mc-agent-loop   # Windows
.venv/bin/pip     install -e "mc-agent-bridge[mcp]" -e mc-agent-loop   # POSIX
```

`[mcp]` pulls the Model Context Protocol SDK; without it you still get the
daemon, the CLI and the loopback API. Check the install:

```bash
.venv/Scripts/mc-bridge --help
.venv/Scripts/mc-agent-loop backends
```

Both installs are **editable**: `git pull` in those repositories is enough to
update them, no reinstall. To uninstall, delete the virtual environment.

## 4. The agent runtime

The loop talks to an OpenAI-compatible endpoint, so anything with one works.
Hermes is what this was developed against, and
[Running the agent on Hermes](hermes-setup.md) covers it end to end - install,
model, API server, MCP registration.

Keep its address and key in a file the loop reads, rather than in your shell
history:

```
# .env   (git-ignored)
HERMES_API_BASE=http://127.0.0.1:8642
HERMES_MODEL=hermes-agent
HERMES_API_KEY=<the API server key>
```

Install the portable [Toolkit Skill](toolkit-skill.md) next: it is what teaches
the runtime how to discover the Toolkit, resolve a caller by UUID, fetch a
chat-time context bundle, and query authoritative state. For unattended runs
where Hermes owns the trigger instead of the loop, see
[Unattended Hermes](hermes-unattended.md).

## 5. Optional: a lab server

`tools/lab_server.py` provisions a headless Fabric server under `labs/<name>/`.
On the first run it downloads the Fabric server launcher (which in turn
downloads the vanilla server), Fabric API and Carpet, and caches them in
`labs/_cache/` - so an offline machine has to be seeded by copying that cache.

```bash
python tools/lab_server.py provision --name lab-01 --void --fabric-api --carpet --java <java25>
python tools/lab_server.py start --name lab-01 --wait 300
python tools/lab_server.py exec --name lab-01 "list"
python tools/lab_server.py stop --name lab-01
```

It needs Java 25 as well, and it writes an RCON password into
`labs/<name>/rcon.json`; that is the console the tool uses. Nothing about a lab
touches your game instance.

## 6. Did it work?

In order, each step proves a bit more:

| Check | Expected |
| --- | --- |
| the game log shows `listening on 127.0.0.1:...` | the mod is up |
| `<gameDir>/mc-agent/port.txt` exists | the mod could write its state |
| `/mcagent status` in game chat | version, port, connected bridges |
| `mc-bridge run` then `mc-bridge call state` | `inWorld`, coordinates |
| `mc-agent-loop run --backend echo --trigger @codex` then `@codex hi` in chat | an echo in chat |
| `python tools/smoke_offline.py` | the whole stack, no game required |

When something fails, [Troubleshooting](troubleshooting.md) lists the traps in
the order they usually bite.

## 7. Files and ports worth knowing

| Path or port | What it is |
| --- | --- |
| `<gameDir>/mc-agent/port.txt` | the port the mod actually got |
| `<gameDir>/mc-agent/events.jsonl` | event history (chat, game, marks, sample lifecycle) |
| `<gameDir>/mc-agent/samples.jsonl` | recordings from `record_start` |
| `<gameDir>/mc-agent/snapshots/<name>/` | snapshots: `entities.jsonl` + `meta.json` |
| `mc-agent-server/` | the same, for the server vantage (a lab writes it into its own directory) |
| `127.0.0.1:8765` | the bridge's loopback API (default) |
| `127.0.0.1:25580` | the mod, client vantage |
| `127.0.0.1:25581` | the mod, server vantage |
| `.env` | API keys for the loop; git-ignored |

All of it is loopback only. Treat the machine as trusted: the interface can run
commands as the player, and the lab console can run them as an operator.

## 8. Uninstall

| Piece | Remove |
| --- | --- |
| mod | the jar from `<instance>/mods/`, and `<gameDir>/mc-agent/` |
| Python side | the virtual environment |
| labs | `labs/<name>/` (each lab is self-contained) |

## 9. Other setups

* **Linux / macOS**: the same commands with `.venv/bin/` instead of
  `.venv/Scripts/`, and `/`-separated paths. `launch_instance.py` finds Java
  through `--java`, `JAVA_HOME` or `PATH`.
* **A dedicated server**: put the same interface mod jar into the server's
  `mods/` and it serves the server vantage on `mcagent.serverPort`. Note that a
  server owner has to install it - the client mod alone cannot reach a server's
  world files.
* **Air-gapped**: `build.py` needs the instance's `libraries/` and
  `.fabric/processedMods/`; `pip install` needs wheels; the lab needs its
  download cache. Seed all three from a connected machine first.
