# Building a mod for a lab

The agent may want its own observation code in the game - a logger, a tick
sampler, a command. A lab makes that a two-step loop: **build** a jar against
the jars the lab already has, then **deploy** it with `lab_server.py` and
restart. Minecraft 26.2 ships unobfuscated class files, so no Gradle and no
mapping download are needed; `tools/build_mod.py` compiles with `javac`
directly.

## Prerequisites

* **A provisioned lab that has started at least once.** The first start makes
  the Fabric launcher unpack the server jar into
  `labs/<lab>/versions/<mc>/server-<mc>.jar` and install libraries. Those files
  are the compile classpath.
* **A JDK 25** with `javac`. A JDK home can be passed with `--jdk`, or recorded
  at provision time (`provision --jdk PATH`), or found through `JAVA_HOME` or
  `PATH`. The lab's own `java` is remembered in `lab.json`, so `--jdk` is
  normally the only extra flag. A plain JRE cannot compile.
* You do **not** need internet access for the build: everything comes from
  `labs/<lab>/` and `labs/_cache/`.

## Source layout

```text
my-mod/
  src/main/java/...            Java sources
  src/main/resources/
    fabric.mod.json            mod metadata (required)
    assets/...                 anything else, copied into the jar
```

`fabric.mod.json` must at least name the entrypoint and the environment, for
example:

```json
{
  "schemaVersion": 1,
  "id": "example-logger",
  "version": "${version}",
  "name": "Example Logger",
  "environment": "*",
  "entrypoints": { "main": ["dev.example.logger.LoggerMod"] },
  "depends": {
    "fabricloader": ">=0.19.0",
    "minecraft": "*",
    "fabric-api": "*",
    "java": ">=25"
  }
}
```

`${version}` is replaced from `--version` when the jar is packed. Declare only
dependencies the lab actually has; Fabric refuses to start when a declared
dependency (`depends`) is missing, which is the correct behaviour for an
auditable instance.

`environment` is `"*"` for a server-side mod that only registers server
callbacks and commands; use `"server"` if it must never load in a client. A lab
is a dedicated server, so the entrypoint runs in the `main` stage
(`net.fabricmc.api.ModInitializer`).

## Build it

```powershell
python tools/build_mod.py --source examples/smoke-mod --lab lab-a `
    --out labs/build/smoke-mod.jar --version 0.1.0 --compression store
```

Output:

```text
building examples\\smoke-mod for lab lab-a (minecraft 26.2)
  javac C:\\...\\bin\\javac.exe (javac 25.0.1)
  javac @...\\javac.args (1 sources, 87 classpath entries)
built labs\\build\\smoke-mod.jar (14.8 KB, sha256 e66ebdb16d53238a...)
  version 0.1.0, compression store, release 25
  87 classpath entries from lab lab-a
  metadata labs\\build\\smoke-mod.jar.build.json
```

The classpath is assembled from the lab, in this order:

1. `labs/<lab>/versions/<mc>/server-<mc>.jar` - the unpacked, unobfuscated
   Minecraft server classes;
2. every jar under `labs/<lab>/libraries/` - Fabric loader, mixin, Brigadier,
   Gson and the rest of the server's libraries;
3. every jar under `labs/<lab>/.fabric/processedMods/` - the Fabric API
   modules the running server will actually load;
4. every jar in `labs/<lab>/mods/` - so a mod can compile against Carpet or
   another installed mod;
5. anything passed with `--classpath-extra`.

Everything is compiled with `--release 25` and packaged by the same tool. The
jar is deterministic: entries are sorted, timestamps are fixed, and the default
`--compression store` keeps equal-length edits at equal size. Rebuilding
identical sources gives byte-identical output; changing one string changes the
hash. The sidecar `<out>.build.json` records the jar hash and size, the JDK and
javac version, the server jar hash, a classpath digest and every source file
hash - keep it with the experiment's audit trail.

## Deploy it and read the result

Fabric loads mods at server start, never while running. Deploy while stopped,
then start:

```powershell
python tools/lab_server.py stop --name lab-a
python tools/lab_server.py provision --name lab-a --mod-jar labs/build/smoke-mod.jar
python tools/lab_server.py start --name lab-a --wait 300
python tools/lab_server.py exec --name lab-a "mcagent-smoke status"
python tools/lab_server.py exec --name lab-a "mcagent-smoke sample first"
```

`provision` compares the jar with the deployed one by SHA-256. If it says
`replaced`, the bytes are new even when the size did not change; if it says
`unchanged`, the bytes really are identical. It refuses to deploy mods into a
running lab. `start` records the on-disk hashes in `run.json` (`modsAtStart`),
so the run is tied to the exact build.

A game-side mod writes its evidence where the lab points it: `start` passes
`-Dmcagent.auditDir=...` (and `-Dmcagent.labName`, `-Dmcagent.labInstance`,
`-Dmcagent.worldDir`, `-Dmcagent.serverDir`, `-Dmcagent.serverPort`) so the mod
never has to guess which lab it is in. Files under `labs/<lab>/audit/` and the
console log `labs/<lab>/logs/console.log` are the result to read back.

## The smoke mod

`examples/smoke-mod/` is a deliberately generic observation mod: it logs
`[mc-agent-smoke] loaded build=... lab=...` at load time, writes
`audit/start.json` plus a `smoke.log` line when the server is ready, and adds
two console commands (`mcagent-smoke status`, `mcagent-smoke sample <label>`).
It has no ROM or use-case logic - it exists to verify the build/deploy/restart
path. Copy it as a starting point for a real logger.

## When the build or the load fails

The tool never hides compiler output. A missing symbol looks like:

```text
...\\SmokeMod.java:51: error: cannot find symbol
    private static final Object BROKEN = doesNotExist();
                                         ^
  symbol:   method doesNotExist()
error: build failed: javac exited 1; the compiler output above is the error
```

A load failure is in the server log and in `start`'s failure output. Fabric
prints a clear block for a missing dependency:

```text
[main/ERROR]: Incompatible mods found!
  Fix: add [add:mc-agent-absent-mod 1 (( -∞,∞ ))], remove [], replace []
  Mod 'MC Agent Lab Smoke Mod' (mc-agent-lab-smoke) 0.1.0 requires mc-agent-absent-mod ...
```

and for a broken entrypoint:

```text
java.lang.ClassNotFoundException: dev.mcagent.smoke.AbsentEntrypoint
```

`lab_server.py start` exits non-zero, prints the last log lines, and repeats the
lines that look like errors (`exception`, `failed`, `missing`, `requires`,
`incompatible`) prefixed with `!!`. A lab that recorded a required mod
(`--test-mod` or `--require-mod`) refuses to start when that jar is missing, so
a broken audit environment cannot be mistaken for a clean run.

## Limitations

* `build_mod.py` compiles server-side mods. A lab has server jars only; a
  client-side mod has to be built against an installed client instance (the
  interface mod's own `build.py` is an example of that).
* The tool builds against the *committed lab state*. Re-provision the lab with
  the new jars before building if Minecraft, the loader or a mod was updated.
* Gradle/loom-style resource processing is not implemented; `fabric.mod.json`
  and plain resources are copied (with `${version}` substitution).
* A lab build has no remapping step, which is correct for the unobfuscated
  26.2 server; an older, obfuscated Minecraft would need intermediary mappings
  and a different build path.
