# Lab servers the agent raises itself

`tools/lab_server.py` brings up one throwaway headless Fabric server per
experiment - a *lab* - and gives the agent a console into it. No GUI, no
launcher, no window to click, nothing shared with the player's live client:
provision a directory, start it detached, type commands over RCON.

```powershell
python tools/lab_server.py provision --name smoke --void --fabric-api --carpet `
    --server-port 27170 --rcon-port 27171 --vantage-port 27172 --bridge-port 27173
python tools/lab_server.py start --name smoke --wait 300     # detached; log to labs/smoke/logs/console.log
python tools/lab_server.py exec --name smoke "summon minecraft:tnt 0 100 0 {}"
python tools/lab_server.py exec --name smoke "tick freeze"
python tools/lab_server.py status --name smoke --tail 20
python tools/lab_server.py identity --name smoke --json       # world/audit dirs, ports, instance id
python tools/lab_server.py verify --name smoke --require-vantage   # re-hash mods, check port.txt
python tools/lab_server.py stop --name smoke
python tools/lab_server.py list
```

`provision` takes `--void` (a flat world of air in the void biome) or
`--world DIR` (copy an existing save in), `--fabric-api`, `--carpet`,
`--mod-jar PATH`, `--mod-url URL` (re-fetched every provision),
`--test-mod PATH` (install **and** require), `--require-mod NAME`,
`--forget-mod NAME` (drop a requirement explicitly), `--mc VERSION`,
`--memory 2G`, `--java PATH`, `--jdk PATH` (the JDK to build mods with;
remembered and hashed) and the four identity ports `--server-port`,
`--rcon-port`, `--vantage-port`, `--bridge-port` (each `0` means "pick a free
one"). `--server-dir` and `--audit-dir` override where the server-vantage mod
and the game-side audit output live. Java comes from `--java`, else
`$MC_AGENT_JAVA`, else the `java` on PATH; whatever provision resolved is
remembered for `start`, so a lab can be started later without repeating it.
`start --wait N` returns as soon as the log says `Done`.

```
labs/<lab>/       fabric-server-mc.*-launcher.*.jar, server.properties,
                  eula.txt, rcon.json (host/port/password), lab.json,
                  identity.json, run.json, mods/, world/, audit/,
                  mc-agent-server/port.txt (rewritten by the mod on start),
                  logs/console.log, .fabric/server/ (the server jar)
labs/_cache/      fabric launcher jar + mod jars, downloaded once for every lab
```

`exec` speaks RCON itself (length/id/type header, payload plus two NULs, type
3 to log in, type 2 to run, answers that may span packets) and prints what the
console answered - which is the command's feedback, so `list`, `tick freeze`
and errors all come back as text. A few quirks are the game's, not the tool's:
`save-all flush` arrives as one packet with its two messages glued together,
and `stop` closes the socket before it finishes replying.

## One lab per experiment: identity, directories and ports

A lab is not just a directory; it is a named instance with its own identity.
`provision` resolves and records every endpoint before the first start, and
writes the same record to `labs/<lab>/identity.json`, so an orchestrator never
has to guess which world or port belongs to which experiment:

| Field | Meaning |
| --- | --- |
| `instanceId` | random, stable for the life of the lab; survives re-provision |
| `worldDir` | the level directory (`labs/<lab>/world`) |
| `auditDir` | where game-side mods write samples (`labs/<lab>/audit`) |
| `serverDir` / `portFile` | where the server-vantage mod writes `port.txt` |
| `serverPort` / `rconPort` | game and console sockets |
| `serverVantagePort` | base port the server-vantage mod binds (scans +20) |
| `bridgeApiPort` | loopback API port a bridge should serve for this lab |

Two labs can run at the same time without sharing anything:

```powershell
python tools/lab_server.py provision --name lab-a --void --fabric-api --carpet `
    --server-port 27170 --rcon-port 27171 --vantage-port 27172 --bridge-port 27173
python tools/lab_server.py provision --name lab-b --void --fabric-api --carpet `
    --server-port 27174 --rcon-port 27175 --vantage-port 27176 --bridge-port 27177
python tools/lab_server.py start --name lab-a --wait 300
python tools/lab_server.py start --name lab-b --wait 300
python tools/lab_server.py list
python tools/lab_server.py identity --name lab-a --json

# one bridge per lab: it reads only that lab's port.txt and serves its own API port
.venv/Scripts/mc-bridge run --server-dir labs/lab-a --api-port 27173
.venv/Scripts/mc-bridge --api-port 27173 call status
```

`list` shows the RCON, VANTAGE and BRIDGE ports of every lab side by side.
`start` clears a stale `port.txt` before spawning, and the mod rewrites it as
soon as it binds, so a bridge that follows the file always lands on the running
server - even after a restart, in the middle of a `labs/lab-a`/`labs/lab-b` pair.
`provision` refuses to reuse a port already recorded for another lab.

`identity` prints the record as text or `--json`; `verify --require-vantage`
re-hashes the deployed jars and, when asked, checks that `port.txt` still names
the recorded server-vantage port. Both are read-only, so they are safe to run
against a live lab right before a measured experiment.

## Deploying a jar: content hashes, not sizes

`provision` decides whether an installed jar needs replacing by comparing its
**SHA-256**, not its byte size. A rebuilt mod that happens to keep its size is
replaced instead of skipped (the old size-only check could leave a live server
running the previous code while the lab record claimed the new one). The copy
lands on a temporary name and is renamed into place, and the deployed bytes are
re-hashed after the copy; a mismatch fails loudly.

Every jar in `mods/` is recorded in `lab.json` with its file name, SHA-256,
size, origin and, when known, version. `run.json` records the same hashes again
at start (`modsAtStart`), so a run can always be tied to the exact bytes that
were on disk.

```powershell
# build a new logger, deploy it while stopped, then start
python tools/build_mod.py --source my-mod --lab lab-a --out labs/build/logger.jar --version 1.1
python tools/lab_server.py stop --name lab-a
python tools/lab_server.py provision --name lab-a --mod-jar labs/build/logger.jar
#   mods: logger.jar (replaced, 4b3c...9f)   <- even if the size is identical
python tools/lab_server.py start --name lab-a --wait 300
```

Rules the tool enforces:

* **No hot reload.** Fabric loads mods at server start. `provision` refuses to
  deploy mods into a running lab; stop it first. (Property-only updates are
  fine while running, and are marked `restartPending` until the next start.)
* **A required mod cannot go missing quietly.** `--test-mod PATH` installs a
  jar and marks it required; `--require-mod NAME` only marks it. `start`
  returns a non-zero exit and names every missing jar instead of running an
  instance that could no longer be audited. Required names survive a later
  `provision` even while the jar is absent; only `--forget-mod NAME` drops one
  explicitly.
* **Drift is visible.** If a deployed jar's bytes no longer match `lab.json`,
  `start` refuses with both hashes. `--allow-mod-drift` starts anyway, and the
  actual bytes are recorded in `run.json` with `drift: true`.
* **An unrecorded jar is not silently trusted.** A jar copied into `mods/`
  after provisioning would still be loaded by Fabric, so `start` and `verify`
  report it as a problem and name it. `--allow-unrecorded-mod` starts anyway,
  and `run.json` keeps `recorded: false` for that jar.
* **A mutable URL is re-fetched.** `--mod-url` downloads every provision, so a
  rebuilt jar served under the same file name is compared by hash and deployed
  as `replaced` instead of being served from cache as `unchanged`. Offline
  provisioning should use `--mod-jar` with a local file (or a seeded
  `labs/_cache/mods`).

Fabric still loads mods at start, so the safe order for an experiment is:
**deploy while stopped -> start -> restore the original in-memory entities and
verify their order -> run the experiment.** Do not trust the entity order a
save reload produced; that is what the restore prerequisite is for
([Forking a live world](protocol-snapshot.md)).

## Building a mod for a lab

Minecraft 26.2 ships unobfuscated class files, so a mod can be compiled
directly against the jars a lab already downloaded - no Gradle, no extra
downloads. `tools/build_mod.py` compiles a source tree with javac and writes a
deterministic jar plus a `<out>.build.json` sidecar with the JDK, classpath and
source hashes:

```powershell
python tools/build_mod.py --source examples/smoke-mod --lab lab-a `
    --out labs/build/smoke-mod.jar --version 0.1.0 --compression store
```

The full workflow, the classpath sources, and the error output to expect are in
[Building a mod for a lab](mod-building.md). `examples/smoke-mod/` is a
generic observation mod (load log, `start.json`, `mcagent-smoke sample`) used
to verify build, deploy, restart and result reading; it contains no use-case
logic.

## What a 26.2 headless server makes you do

* **The launcher is an installer.** The jar behind
  `meta.fabricmc.net/.../server/jar` carries `install.properties`
  (`game-version=26.2`), downloads the vanilla server to
  `.fabric/server/26.2-server.jar`, unpacks its bundle into
  `versions/26.2/server-26.2.jar` and installs the libraries. First `start` is
  therefore ~40 s and ~140 MB; `labs/_cache/` only spares you the launcher and
  the mods.
* **Java 25, not 21.** The server's own mod list shows `java 25` next to
  `minecraft 26.2`, and an older JVM dies before the world loads:
  `UnsupportedClassVersionError: net/minecraft/bundler/Main has been compiled
  by a more recent version of the Java Runtime (class file version 69.0), this
  version of the Java Runtime only recognizes class file versions up to 65.0`.
  A machine whose PATH has Java 21 must pass `--java` (or set
  `MC_AGENT_JAVA`).
* **Nobody logged in means the world may not tick.** 26.2 has
  `pause-when-empty-seconds` (default 60); the tool sets it to `0`, otherwise
  a lab that only talks over RCON pauses itself and "run for N ticks"
  experiments silently do nothing.
* **Nothing is loaded.** A lab with no players has no chunks loaded, so
  `if block`/`data get block` answer *That position is not loaded* until you
  `forceload add 0 0`. Void-lab checks need that first.
* **Time is a timeline now.** `time query daytime` is gone; 26.2 answers
  `time query day` with `Timeline minecraft:day is at 362 tick(s)` and
  `time set day` with `Set minecraft:overworld to time marker minecraft:day`.
* **server.properties gets rewritten** on first start: `:` is escaped
  (`level-type=minecraft\:flat`), defaults are appended, and 26.2 adds a whole
  `management-server-*` block - a second, disabled-by-default admin listener
  with its own generated secret, unrelated to RCON.
* **The void syntax that works** is what the tool writes:
  `level-type=minecraft:flat` with
  `generator-settings={"layers":[{"block":"minecraft:air","height":1}],"biome":"minecraft:the_void"}`.
  Verified: chunk (0,0) is air at y=-64, y=0 and y=200, bedrock at y=-64 fails
  the same test, and `locate biome minecraft:the_void` reports the origin.
* **The 26.2 level layout moved.** Region/entity/poi files live in
  `world/dimensions/minecraft/<dimension>/`, world-wide state in
  `world/data/minecraft/*.dat` (game rules, weather, scoreboard, world clocks)
  and player data in `world/players/` rather than `world/playerdata/`.
  `--world` skips `session.lock`, `playerdata/`, `players/`, `stats/`,
  `advancements/` and `logs/`; note that `mc_fork`'s copy list in
  [protocol-snapshot.md](protocol-snapshot.md) still describes the pre-26.2
  shape and should be re-checked against a real 26.2 save.
