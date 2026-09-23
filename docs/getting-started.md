# Getting started

This page takes you from nothing to an agent that can answer a question about
your world, and then to a lab instance you can experiment on. Every command here
has been run against Minecraft 26.2 on Windows; the same steps work on other
platforms with the paths changed.

## What you need

| | |
| --- | --- |
| **Minecraft** | 26.2 with **Fabric Loader 0.19+** and **Fabric API** |
| **Java** | 25 (the version the game itself ships with is fine) |
| **Python** | 3.11+ for the bridge, the loop and the tools |
| **An agent runtime** | [Hermes](hermes-setup.md) is what this was built against; anything that can call MCP or HTTP works too |

## 1. Install the pieces

Everything is installed once, from
[Installation](install.md) - the mod jar into `<instance>/mods/`, the bridge
and the loop into one virtual environment. The short version:

The mod compiles against the game's own (unobfuscated) jar - no Gradle, no
decompiler:

```bash
git clone https://github.com/guajun/mc-agent-interface-mod
python mc-agent-interface-mod/build.py \
    --minecraft-dir "C:/Users/me/AppData/Roaming/.minecraft" \
    --version 26.2-Fabric \
    --jdk "C:/Program Files/Java/jdk-25"
```

Copy `dist/mc-agent-interface-<version>.jar` into `<instance>/mods/` next to
Fabric API, then start the game. In the log you should see:

```
[mc-agent-interface] initialized, dir=<instance>/mc-agent basePort=25580
[mc-agent-interface] listening on 127.0.0.1:25580
```

The mod writes the port it actually got into `<instance>/mc-agent/port.txt`. If
something else holds 25580 it moves to the next free port, and the bridge finds
it through that file.

!!! tip "In game, right now"
    The mod adds client-side commands, so you can check the interface without any
    of the rest:

    ```
    /mcagent status      mod version, port, connected bridges, tick
    /mcagent state       position, velocity, health, dimension
    /mcagent entities 32 entity list within 32 blocks
    /mcagent record start 200 32    sample 200 ticks into samples.jsonl
    ```

## 2. Install the bridge and the loop

```bash
git clone https://github.com/guajun/mc-agent-bridge
git clone https://github.com/guajun/mc-agent-loop
python -m venv .venv
.venv/Scripts/pip install -e "mc-agent-bridge[mcp]" -e mc-agent-loop
```

`[mcp]` is optional: without it you still get the daemon, the CLI and the
loopback API.

## 3. Run the bridge

!!! tip "Single player already has both vantages"
    The same jar gives you the client vantage (25580) *and* the server vantage
    (25581) while you play single player, because the world runs on an integrated
    server inside the same process. Everything below - authoritative state,
    snapshots, forking - works there without any server to set up.

```bash
.venv/Scripts/mc-bridge run
```

```
[mc-agent-bridge] local API on 127.0.0.1:8765
[mc-agent-bridge] connected to interface mod on port 25580
```

It keeps trying while the game is closed, so start it whenever; the game can come
and go without disturbing it. In another shell:

```bash
.venv/Scripts/mc-bridge call state
.venv/Scripts/mc-bridge call entities '{"radius": 32}'
.venv/Scripts/mc-bridge call chat '{"message": "hello from outside"}'
.venv/Scripts/mc-bridge watch --events chat,game      # live event stream
```

## 4. Give it a mind

The loop turns chat into backend turns. The quickest way to see it work needs no
model at all:

```bash
.venv/Scripts/mc-agent-loop run --backend echo --trigger @codex
```

Type `@codex hello` in game chat and the game will answer itself with an echo.
That proves the plumbing: chat in, backend out, reply back into chat.

For a real model, start Hermes and point the loop at it - see
[Running the agent on Hermes](hermes-setup.md):

```bash
.venv/Scripts/mc-agent-loop run --backend hermes --trigger @codex --env-file .env
```

The loop ignores its own messages (otherwise it would answer itself forever), so
in a single-player world the trigger has to come from somebody else - another
player, or the agent's own player as described in
[Who is the agent in game](player-identity.md). For the single-client case there
is one-shot mode:

```bash
.venv/Scripts/mc-agent-loop once "look around and tell me what is within 64 blocks" \
    --sender operator --backend hermes --env-file .env
```

## 5. Check it without a game

The repository ships a fake mod so the seams can be tested on their own:

```bash
python tools/smoke_offline.py                     # echo backend, no model
python tools/smoke_offline.py --backend hermes    # real model, real tools
```

The second one drives the actual stack - daemon, loop, MCP, model - against a
stand-in for the game, and is the fastest way to tell whether a problem is in
your setup or in the game.

## 6. Give the agent a lab

For research you want an instance the agent owns: no humans, no rendering,
deterministic stepping. That is one command plus a server start:

```bash
python tools/lab_server.py provision --name my-lab --void --fabric-api --carpet \
    --mod-jar mc-agent-interface-mod/dist/mc-agent-interface-0.5.2.jar \
    --java <java25>
python tools/lab_server.py start --name my-lab --wait 300
python tools/lab_server.py exec --name my-lab "tick freeze"
```

The lab listens with the mod's **server vantage** on the port in
`labs/my-lab/mc-agent-server/port.txt` (25581 by default, or the next free one).
Attach a bridge to it and it speaks the same tools:

```bash
.venv/Scripts/mc-bridge run --api-port 8766 \
    --port-file labs/my-lab/mc-agent-server/port.txt
.venv/Scripts/mc-bridge --api-port 8766 call state
```

## 7. Fork a live world into it

The point of the lab is that you can take a *running* world with you, including
the entity tick order:

```bash
# against the live instance's server vantage
.venv/Scripts/mc-bridge --api-port 8767 call fork '{"name": "before", "radius": 64}'

# restore it into the lab and prove the order survived
python tools/fork_verify.py inspect "<fork directory>"
python tools/fork_verify.py restore "<fork directory>" --apply --api-port 8766
python tools/fork_verify.py check   "<fork directory>" --api-port 8766 --radius 0
```

`check` re-snapshots the lab and compares the order hash with the recording:
`MATCH` means the isolated instance ticks its entities in the same order as the
world you forked. The full recipe, including the parts that are easy to get
wrong, is in [Forking a live world](protocol-snapshot.md).

## Where to next

* [How it fits together](concepts.md) - so you put new code in the right layer
* [Tools](tools.md) - the rest of the toolbelt
* [Troubleshooting](troubleshooting.md) - the traps, collected
