# How it fits together

Most confusion in a system like this comes from mixing up **where something
runs** with **what it is**. This page is the shortest version of the model the
project settled on.

## Two environments, one seam

| | What it is | Owns |
| --- | --- | --- |
| **The experiment environment** | where the agent lives: terminal, files, analysis, approvals, model calls | *judgement* - what to do, when, with which parameters |
| **The production environment** | where the world actually runs: a player's live client, someone's live server, or an instance the agent provisioned for itself | the world, and the facts that only exist inside its process |
| **The bridge** | the seam between them: composition and transport | turning a pile of I/O steps into one tool, and moving data both ways |

The bridge is not part of the experiment environment - it is what the experiment
environment uses to reach the world. It has no opinions about what you are
doing.

## Where does a new capability belong?

One test, applied in this order:

1. Can it be expressed as **primitives + waiting + shaping**? Then it belongs in
   the **bridge**, as a composed tool. `mc_command_output` is the canonical
   example: send a command, collect the feedback it produced, return it as data.
2. Does it need **per-tick timing**, or a fact that only exists **inside the game
   process**? Then it belongs in the **mod**. The snapshot is the canonical
   example: the entity tick order is not in the save file, so nothing outside the
   process can read it.
3. Is it **judgement** - what to do, when, with what parameters? Then it belongs
   to the **agent**. Nothing in this repository decides anything.

Everything else - analysis, comparison, world file surgery, one-off scripts -
belongs to the agent's own toolbelt (a terminal and a filesystem), and this
project deliberately does not reimplement it.

## Three tracks, one mod

The tracks are not three frameworks. They are three kinds of *instance* the same
mod can run in:

| Track | Instance | The bridge's job there |
| --- | --- | --- |
| 1 | the player's live client | realtime probing and driving |
| 2 | the live server - including the integrated server inside a single-player world | authoritative probing, driving players, reacting to what happened |
| 3 | an instance the agent provisioned: a headless server on a copied world | provisioning and operations: load a world, start, command, freeze, step, collect, tear down |

Because the seam is the same, a tool written for one track usually works in the
others. When it does not, the difference is usually *what is loaded* rather than
*what is possible* - a lab has no player, so no chunks are loaded and a radius
has nothing to measure from.

## Brain, hands, body

| | What it is | What it gives you |
| --- | --- | --- |
| **brain + hands** | the interface mod's server vantage: code inside the process | per-tick reading and computing, authoritative numbers, and the ability to act in the same tick |
| **body** | a player entity in the world - a [Carpet fake player](player-identity.md), or a second client | presence: pressure plates, mob aggro, chunk loading, being seen by other players |
| **voice** | chat as the agent's own name, or a server broadcast | being part of the conversation |

An agent can have any subset. A server-side probe can press a button by calling
the same interaction path a player would; what it cannot do is *exist* - so
anything that depends on a player merely being there needs a body.

## Why a fork needs two things

A save file is not a state, it is a state *plus the order things get rebuilt in*:

| | Recorded by | Restored by |
| --- | --- | --- |
| blocks, block entities, light | region files | copying the world |
| entities, their NBT, and **their tick order** | the snapshot, read from the live process | `/summon` in recorded order |

Entity updates are computed one at a time, so order decides the outcome of
pushes, cramming and explosions. The snapshot records the order and hashes it, so
a restore can be *checked* instead of believed - that is what
[`fork_verify.py`](fork-verify.md) does, and why a fork that matches is evidence.

## What actually has to be written

Very little, on purpose:

* in the **mod**: what only the process can know - the tick order, in-memory
  state, and (later, if it earns its place) running agent-written code inside the
  game;
* in the **bridge**: composed tools that turn multi-step I/O into one call;
* in the **agent**: everything that requires judgement.

Skills and recipes (how to obtain a world copy, how to bring up a lab, how to
step ticks deterministically) are documentation, written *after* doing the thing
once - not a framework.

## Deliberately not built

* No use-case logic, ever: no cannon code, no experiment definitions.
* No mandatory always-on daemon on the agent side: an agent may start something,
  wait, and come back later.
* No scripting host inside the game until a real need shows up. The measured
  alternative - doing everything from outside - costs about 35 ms and one tick
  per look, which is enough for everything tried so far.
* No sandbox: agent-written code is trusted local code for your own game. The
  guardrails are budgets, isolation of failures and a kill switch.

The open questions that are *not* decided are collected in the
[RFCs](rfc/0001-agent-interface.md).
