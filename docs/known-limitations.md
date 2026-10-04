# Known experiment and fork limitations (Minecraft 26.2)

This page records fidelity gaps and usable experiment boundaries, not guarantees
of full world or JVM checkpoint recovery. The Go CLI does not yet implement
world `fork`/`restore`; entity NBT/order capture is only part of a branch. See
[project scope](concepts.md#world-forks-and-experiment-boundaries).

## Moving pistons and pending block events

A branch captured during piston movement is not currently guaranteed to continue
exactly like the source. In 26.2, `PistonMovingBlockEntity.saveAdditional` writes
`progressO` under the NBT key `progress`; `loadAdditional` restores both
`progress` and `progressO` to that saved value. `lastTicked` is not persisted.
The saved moving block state therefore does not retain all of the live movement
state. The server's pending block-event queue and its order are also not encoded
by ordinary world saving. A boundary between ticks does not prove that queue is
empty: events can remain pending for the next tick.

These are branch save/load fidelity gaps. Writing piston NBT does not itself
reset the source piston's fields to the loaded values; source save maintenance
effects and branch reconstruction must be assessed separately. `save-all` and
its maintenance effects remain an accepted project boundary.

For experiments that can start from a stationary mechanism, fork that clean
baseline, then activate the piston and run the trial entirely in the branch.
Do not carry an in-flight movement across the fork. Check that the baseline has
the required blocks, orientation, timing and relevant conditions; visually
stationary pistons alone do not establish that no block events are pending.
This method is unsuitable when the research question requires the precise
already-running intermediate state. Direct branching from dynamic states is a
future support goal, to be added and verified incrementally.

## Entity lifecycle in experiments

Entity NBT/order and sequential summon remain useful for restoring supported
entities. For experiments where entities can be created for each trial and stay
loaded/ticking through the measured period, create them after branching and use
them immediately in the branch. Validate relevant identity/references, order,
motion and activation conditions. This can avoid crossing a lifecycle transition
at the fork, but it does not cover experiments that specifically depend on an
existing entity's history or unsupported runtime state.

## Water flow and random ticks

Water flow is not wholly lost on saving: fluid block state and planned
`fluid_ticks` are persisted. In 26.2 water uses scheduled fluid ticks (delay 5),
not random ticks. Fidelity still depends on a consistent view of files, in-memory
state and scheduled queues, the game-time basis of saved delays, cross-chunk
ordering, and which chunks are actually loaded/ticking. Persisted fluid data
alone does not establish identical continuation.

Saved ticks retain relative order within each chunk, but do not encode the original
global `subTickOrder` across chunks. Restoring equal-time, equal-priority ticks can
therefore change their execution order. Water spreading itself uses scheduled
ticks; secondary effects such as drops may still consume random numbers. Lava
also has random spread delays and random-tick ignition, so it needs a separate
assessment.

Random ticks have a different gap: the current state of `Level.randValue` used
to select positions and `Level.random` used by block behavior is not saved.
World reload reconstructs these sources; unloading one chunk does not itself
reconstruct the whole level's random sources. Changes to the ticking set and
random-call consumption can nevertheless change the trajectory. Statistical
experiments remain possible with a suitable design, but the same seed does not
guarantee the same next random result after branching.

These methods belong to experiment design; they neither redefine the research
question nor turn an unsupported state into a successful exact continuation.
