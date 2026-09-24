# RFC 0002: programmable tool calls (running agent-written code in the game)

* Status: deferred - nothing here is implemented, and that is a decision rather
  than a gap: the first draft was judged too much design for a need that has not
  been demonstrated. See "Deliberately not built (yet)" in
  [concepts.md](../concepts.md). Reopen when the same hand-rolled helper keeps
  showing up in more than one experiment; until then, doing it from outside
  (~35 ms per look) is enough
* Scope: letting an agent inject code that runs **inside** the game process
* Related: [RFC 0001](0001-agent-interface.md), [player identity](../player-identity.md)

## The problem, in measured numbers

Everything an agent does today crosses a socket and a tick boundary. Measured on
a live world (521 entities within 64 blocks, 256 KB of JSON):

| Operation from outside the game | Cost |
| --- | --- |
| round-trip of a trivial request (`state`) | median **35 ms** (the request is queued to the next client tick) |
| entity snapshot, radius 64 | **521 entities, 256 KB, 59 ms** |
| command plus reading its answer | **34 ms**, before any deliberate wait |

A Minecraft tick is 50 ms and the client thread is also rendering. So an
external probe is limited to roughly 10-20 samples/second, pays tens of
kilobytes each time, and cannot compute anything derived without shipping the
raw data out first. Commands (/player, /data get, Scarpet) have the same shape:
one command per tick at best, no numeric libraries, no trigger logic that is not
already written in the game.

That ceiling is the reason for this RFC. The agent should be able to say "watch
these entities for 600 ticks, record Δv and collision-box size around every
impulse above X, and give me the summary" - and have that run at tick rate with
only the summary crossing the socket.

## What the agent needs (requirements)

1. **Tick-rate observation** with derived quantities computed in place
   (Δv, impulse windows, AABB groups, counts, percentiles).
2. **Triggers and capture windows**: "record ±N ticks around this event".
3. **Scripted actions with tick timing**: spawn at T, place at T+3, ignite at
   T+5, sample until T+40 - deterministic, not one command per round trip.
4. **Compact results**: kilobytes back to the agent, not hundreds.
5. **Reproducibility**: a script file *is* the definition of an experiment.
6. **One contract, several vantage points** (see below), so the same script can
   run client-side today and server-side later.

Non-goals: a general-purpose modding API for humans, a security sandbox against
hostile code, and replacing datapacks/command blocks.

## Vantage points

| Where the code runs | Sees | Tick exact | Notes |
| --- | --- | --- | --- |
| **Client** (today's mod) | interpolated entity view, screens, client commands, render distance limit | yes (client tick) | works in single player and on any server, no server install |
| **Server** (a server mod) | authoritative entities, all loaded chunks, no render distance limit, real impulses | yes (server tick) | single player: the integrated server runs in the same process, so a server mod is still usable there |
| **External process** (Minescript-style) | whatever the API exposes | no | fastest to write, slowest loop; see prior art |

The measured differences are real: the same jumping fake player read `vy=0.333`
through the client's interpolated view and `-0.078` from the server. For physics,
the server vantage is the honest one.

## Runtime survey (checked, not guessed)

The game runs Mojang's Java 25 runtime, and `java --list-modules` on it shows
`jdk.compiler`, `jdk.jshell` and `java.scripting` are all present. That means
**the game can compile Java at runtime with no extra dependency**.

| Runtime | Size | License | Notes |
| --- | --- | --- | --- |
| Java via `jdk.compiler` + a child classloader | 0 (already there) | n/a | full speed, direct access to game classes, compile errors come back as text; no sandbox |
| Rhino (JS, ES5+) | ~1.4 MB | MPL-2.0 | embeddable, Java interop, interpreter speed |
| LuaJ (Lua 5.2) | ~250 KB | MIT | tiny, decent speed, clunkier Java interop |
| Nashorn-core + ASM (ES5.1) | ~2 MB | GPLv2+CE | maintained standalone version of the old JDK engine |
| GraalJS | ~40 MB | UPL | fastest scripted option, heavy dependency |
| Kotlin scripting | ~50 MB | Apache-2.0 | nicest language, heaviest |

### Prior art

| Project | What it is | Fit for us |
| --- | --- | --- |
| **Minescript** (Fabric/Forge/NeoForge 26.2, GPL-3.0) | Python scripts in an *external* process driving the client | good reference for an ergonomic API; external process = the latency problem above |
| **Neo Scripts Lua** (Fabric/NeoForge 26.2, GPL-3.0) | `/lua` (client) and `/slua` (server) in-process Lua | closest existing thing; would make a fine *optional* runtime |
| **Allium** (Fabric, MIT) | Lua loader | only for 26.3+, too new for this instance's 26.2 |
| **Carpet Scarpet** | server-side interpreter, tick-precise, `/script` | proves the concept; its language and reach are the ceiling we are trying to lift |
| KubeJS | JS for pack authors | not available for Fabric 26.2 |

Licensing note: our repositories are MIT. Depending on a GPL program is fine;
bundling or linking GPL code is not, so anything we *ship* has to be
MIT/Apache/MPL/UPL-compatible.

## Design proposal (for discussion)

### Lifecycle primitives

```
script_start  { id, source, mode: once|tick|on_event, budget_ms, caps, vantage }
script_stop   { id }
script_list   {}
script_status { id }         -> state, ticks run, errors, result
script_output { id, since }  -> records written by the script, as a cursor
```

Scripts are keyed by id and hot-replaceable; a failed script is disabled (not
retried) and reports why.

### The host API is the contract

The script does not get raw internals by default. It gets a small host object,
and the same names must exist on the client adapter and a future server adapter:

```java
interface ScriptHost {
    long tick();                       // game tick counter
    Self self();                       // position, velocity, health, gamemode
    List<Entity> entities(double radius, Filter filter);   // typed, cheap
    void record(String key, Object value);     // -> script-<id>.jsonl
    void result(Object value);                 // final, bounded payload
    void log(String message);                  // -> event stream
    void schedule(long tick, Runnable action); // tick-timed actions
    void command(String command);              // server command, no round trip
    void stop();                               // end cleanly
}
```

Escape hatch: a capability flag (`allowGameInternals`) that additionally exposes
the raw game objects for experiments the API does not cover yet - opt-in, and
the reason this is not sandboxable in general.

### Results

`record(...)` appends to `<gameDir>/mc-agent/script-<id>.jsonl` (bounded, rotated)
and pushes at most a few compact lines per second into the normal event stream,
so the agent can watch progress without drowning in data. `result(...)` is
returned by `script_status` and clamped (64 KB), which is where aggregates and
verdicts belong.

### Safety posture (explicit)

Scripts are **trusted local code** written by the agent for the user's own game.
The goal is protecting the frame rate and the world, not defending against a
hostile author:

* per-tick wall-clock budget (default ~2 ms) with an automatic stop and a
  reported reason after repeated overruns;
* exceptions are caught per tick and disable just that script;
* every script is listed, logged and killable;
* optional restricted imports (deny `java.io`, `java.net`, reflection) enforced
  by the compiler's file manager - a guardrail, not a sandbox;
* nothing implicit: a script that wants to write files or open sockets has to
  say so in its capability list and the user has to allow it.

### Why Java first

Zero dependency, the full speed of the JVM for tight per-tick loops, no interop
boundary for game classes, and compile errors are good feedback for a model. The
cost is a ~100 ms compile per script change and no sandbox. A JS or Lua runtime
can be added later as an additional `language` field - the host API is what
scripts are written against, so the runtime is a replaceable part.

## Open questions for the discussion

1. **Language for v1**: Java-only, or Java + one scripted language (Rhino/LuaJ)?
2. **Vantage**: client first, or client and server adapters together? (The
   server one is what makes physics measurements honest.)
3. **Host API shape**: how much typed API is worth designing before we know the
   first three experiments, versus a thin `gameInternals` escape hatch now?
4. **Safety**: is "trusted code, budgeted, killable" acceptable, or do you want
   restricted imports from day one?
5. **External runners**: ignore Minescript/Neo Scripts, or keep them as an
   optional alternative runtime behind the same tool names?
6. **Where results live**: files only, stream only, or both (proposed)?
