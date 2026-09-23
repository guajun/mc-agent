# Lab servers the agent raises itself

`tools/lab_server.py` brings up one throwaway headless Fabric server per
experiment - a *lab* - and gives the agent a console into it. No GUI, no
launcher, no window to click, nothing shared with the player's live client:
provision a directory, start it detached, type commands over RCON.

```powershell
python tools/lab_server.py provision --name smoke --void --fabric-api --carpet
python tools/lab_server.py start --name smoke --wait 300     # detached; log to labs/smoke/logs/console.log
python tools/lab_server.py exec --name smoke "summon minecraft:tnt 0 100 0 {}"
python tools/lab_server.py exec --name smoke "tick freeze"
python tools/lab_server.py status --name smoke --tail 20
python tools/lab_server.py stop --name smoke
python tools/lab_server.py list
```

`provision` takes `--void` (a flat world of air in the void biome) or
`--world DIR` (copy an existing save in), `--fabric-api`, `--carpet`,
`--mod-jar PATH`, `--mod-url URL`, `--mc VERSION`, `--memory 2G` and
`--java PATH`. Java comes from `--java`, else `$MC_AGENT_JAVA`, else the
`java` on PATH; whatever provision resolved is remembered for `start`, so a
lab can be started later without repeating it. `start --wait N` returns as
soon as the log says `Done`.

```
labs/<lab>/       fabric-server-mc.*-launcher.*.jar, server.properties,
                  eula.txt, rcon.json (host/port/password), lab.json,
                  run.json, mods/, world/, logs/console.log,
                  .fabric/server/ (the server jar the launcher fetched)
labs/_cache/      fabric launcher jar + mod jars, downloaded once for every lab
```

`exec` speaks RCON itself (length/id/type header, payload plus two NULs, type
3 to log in, type 2 to run, answers that may span packets) and prints what the
console answered - which is the command's feedback, so `list`, `tick freeze`
and errors all come back as text. A few quirks are the game's, not the tool's:
`save-all flush` arrives as one packet with its two messages glued together,
and `stop` closes the socket before it finishes replying.

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
