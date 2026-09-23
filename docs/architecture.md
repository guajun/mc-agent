# Architecture: two environments, one seam

中文版: [zh/architecture.md](zh/architecture.md)

The mental model the project settled on, and the rules that follow from it. It
exists because most of the confusion in this codebase came from mixing up *where
something runs* with *what it is*.

Related: [RFC 0001](rfc/0001-agent-interface.md) (interface and bridge),
[RFC 0002](rfc/0002-programmable-tool-calls.md) (running agent-written code),
[protocol-snapshot.md](protocol-snapshot.md) (forking a live world),
[player-identity.md](player-identity.md) (who the agent is in game).

## The two environments, and the bridge between them

| | what it is | owns |
| --- | --- | --- |
| **Hermes, the experiment environment** | where an agent lives: terminal, files, analysis, approvals, model calls | *judgement* - what to do, when, with which parameters |
| **the Minecraft side, the production environment** | where the world actually runs: a player's live client, someone's live server, or an instance the agent provisioned for itself | the world, and the facts that only exist inside its process |
| **the bridge** | the seam between them: composition and transport | turning a pile of I/O steps into one tool, and moving data |

The bridge is **not** an environment and not a track. It is the same binary
against any instance that runs the interface mod; the difference between tracks
below is *which instance*, not which software.

## Where does a new capability belong?

One rule, then examples:

1. Expressible as "primitives + waiting + shaping"? -> **bridge**.
2. Needs every tick, or needs state that only exists in the game process? -> **mod**.
3. Needs judgement (whether, when, with what)? -> **Hermes**.

| capability | layer | why |
| --- | --- | --- |
| `mc_command_output` (send a command, collect its answer) | bridge | two primitives and a wait |
| `mc_entities` (counts by type + the N closest) | bridge | reshaping data the mod already reports |
| `mc_fork` (freeze, save, snapshot, copy world) | bridge | an ordered sequence of steps over existing primitives |
| `SNAPSHOT` (entity set *in tick order*, with NBT) | mod | the order is an in-process fact a save file does not have |
| hot-compiled probes (deferred, see below) | mod | per-tick execution inside the process |
| "is this run repeatable, and does it matter?" | Hermes | a decision, not a mechanism |

## Three tracks: the same mod, three kinds of instance

| track | where the interface mod runs | for | iteration cost |
| --- | --- | --- | --- |
| **1. probe** (deferred) | the live instance, with Java compiled at runtime and hot-swapped | tick-rate observation, computation and driving in one place | seconds, no restart |
| **2. live server mod** | the live instance, as an ordinary Fabric mod | *fidelity*: the live entity order, the players present, mob AI state; and anything that needs mixins or new registry content | a build and a restart (~1-2 min) |
| **3. lab** | an instance the agent provisions itself: a copied world and a headless server | *repeatability*: frozen ticks, stepwise execution, batch runs, no human, no permission from anyone, no risk to the live world | seconds |

Track 1 is **deferred on purpose, and data-driven**: run tracks 2 and 3 first,
watch which helpers keep being hand-rolled, and only build a probe SDK if the
same need shows up repeatedly. Its ceiling is real - no mixins, no registration,
no changing vanilla code - and its convenience is real too, so the decision
deserves evidence rather than enthusiasm.

## Brain, hands, body

* A **probe** (or a script) is *brain and hands*: it reads, computes and acts
  within a tick, without a round trip.
* A **Carpet fake player** is a *body*: a real `ServerPlayer` that pressure
  plates notice, mobs can target, chunks load around, and other players can see.
* The two compose: code can summon a fake player and drive it at tick rate
  (which is strictly better than typing `/player <name> use` once per tick), and
  anything that depends on *presence* still needs the entity, not the code.

Reading state is the other half: a client's view is interpolated for rendering
(the same jumping fake player read `vy=0.333` client-side and `-0.078`
server-side), so measurements belong on the server vantage.

## Why a fork needs two things, not one

A save file carries blocks and entity NBT, but **not the entity tick order**:
`EntityTickList` is an insertion-ordered map rebuilt as chunks load, and that
order changes the result of anything computed entity by entity - pushes,
cramming, explosions.

So "fork the live world" is:

* **blocks**: `/tick freeze` -> `/save-all flush` -> copy the world files;
* **entities + order + in-memory state**: an in-process snapshot, with an
  `orderHash` so a restore can be *checked* instead of believed.

What that buys, and what it does not:

| | live instance (track 2) | lab (track 3) |
| --- | --- | --- |
| entity order | the real one, as it evolved | recorded, reproducible, and *canonicalisable* |
| observation | exactly what is happening now | what the experiment says should happen |
| repeatability | not reproducible from a save | reproducible, to be verified by a re-snapshot |

A lab cannot make itself identical to a live session - it can record that
session's order, replay it, and quantify how much the outcome depends on it.

## What actually has to be written

Everything else is the agent's terminal and files.

| # | thing | why only we can | size |
| --- | --- | --- | --- |
| 1 | the mod's server entrypoint | only the process has the facts | S |
| 2 | `SNAPSHOT`: entities in tick order, with NBT and an order hash | as above; a save cannot record it | M |
| 3 | tick-order observation (optional) | in-process fact | S |
| 4 | headless lab provisioning | game knowledge: Fabric server, mod set, first-run world | S-M |
| 5 | `mc_fork`: freeze -> save -> snapshot -> copy | a composed sequence with game knowledge | S-M |
| 6 | the bridge's pluggable backend (client / server / lab) | architecture | S |

## Deliberately not built (yet)

* A scripting host with a large typed host API (the first draft of RFC 0002):
  too much design for a need that has not been demonstrated.
* A procedure "skill" written before the procedure has been run once - the skill
  should record practice, not guess at it.
* Third-party script runners (Minescript, Neo Scripts Lua): different shape
  (external process or GPL), useful as reference only.
* Any use-case logic: no cannons, no sulfur cubes, no analysis pipelines in the
  core. If it only matters for one experiment, it lives with that experiment.
