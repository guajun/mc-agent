# Fork verification: did the lab reproduce the live world?

A save file contains blocks and entity NBT but not the order the level ticks
entities in, and that order decides the outcome of anything computed entity by
entity. So "fork a live world" is only worth something if the restored world
ticks its entities in the recorded order. [protocol-snapshot.md](protocol-snapshot.md)
defines what a snapshot is; `tools/fork_verify.py` is the consumer half of that
contract: it reads a fork directory, drives the bridge daemon over its loopback
API, and decides whether a restore was faithful. It does not take the game's
word for it - the verdict comes from the files and from a fresh snapshot read
back off disk.

## What it reads

A fork directory is whatever `mc_fork` copied: region files plus
`meta.json` and `entities.jsonl`. The tool only needs the last two.

| Input | Used for |
| --- | --- |
| `meta.json`: `protocol`, `entities`, `orderHash` | validation: protocol 1, record count, and the hash the file must reproduce |
| `meta.json`: `radius` | the fresh snapshot in `check` asks the same radius (0/absent = every entity the level ticks) |
| `meta.json`: `tick`, `dimension` | orientation only; they are printed and never compared |
| `entities.jsonl`: line order | the tick order - the authoritative thing this whole track exists to preserve |
| `entities.jsonl`: `uuid` | `orderHash`, duplicate detection, and matching entities between two snapshots |
| `entities.jsonl`: `type`, `pos`, `nbt` | `/summon` lines for `restore` |
| `entities.jsonl`: `pos`, `vel` | position and velocity deltas in `diff` |

`orderHash` is the one value that proves a restore reproduced the order:
`sha256(join(":", uuids in tick order))[:16]`. The tool recomputes it from the
file and never trusts the copy in `meta.json`.

## Subcommands

| Command | Does | Exit code |
| --- | --- | --- |
| `inspect <forkDir>` | prints the meta fields, record count, type histogram, the recomputed hash, and every validation error | 0 valid, 1 invalid |
| `restore <forkDir>` | prints the `/summon` lines in recorded order (dry run, the default) | 0, 1 if the recording cannot become commands |
| `restore <forkDir> --apply --api-port 8765` | issues them through the bridge one at a time, progress every 100 | 0, 1 if any command failed |
| `check <forkDir> --api-port 8765 [--name <tmp>]` | asks for a fresh snapshot and compares hash and per-type counts | 0 MATCH, 1 MISMATCH |
| `diff <forkDirA> <forkDirB>` | order hashes, per-type counts, first divergent index, largest position/velocity deltas | 0 identical, 1 different |
| `selftest` | builds synthetic snapshots in a temp dir and checks all of the above against a fake bridge | 0 pass, 1 fail |

`inspect` and `diff` are offline: they only touch the filesystem. `restore
--apply` and `check` need a bridge; both say which port they tried and exit 2 if
there is no daemon there.

```bash
python tools/fork_verify.py inspect <forkDir>
python tools/fork_verify.py diff    <forkDirA> <forkDirB>     # comparable experiment runs
python tools/fork_verify.py selftest                          # no bridge, no game
```

Dry-run output is one command per line on stdout and a one-line summary on
stderr, so it is also a function file:

```bash
python tools/fork_verify.py restore <forkDir> > restore.mcfunction
python tools/fork_verify.py restore <forkDir> --from 500 --limit 100
```

## The real sequence, once the mod and bridge land

Untouched from the rest of the toolchain: the bridge owns the only connection to
the game, and this tool is one more loopback client of it.

```bash
# 0. game side (frozen in-process, blocks copied): the bridge's fork tool
mc-bridge call fork '{"name": "before", "radius": 64}'

# 1. what did we record?  (offline; exits 1 if the file contradicts itself)
python tools/fork_verify.py inspect <forkDir>

# 2. bring it back into the isolated lab server, one /summon at a time
python tools/fork_verify.py restore <forkDir>                # look first
python tools/fork_verify.py restore <forkDir> --apply --api-port 8765

# 3. the acceptance test: fresh snapshot of the lab, compared with the recording
python tools/fork_verify.py check <forkDir> --api-port 8765

# 4. two runs of the same experiment, compared entity by entity
python tools/fork_verify.py diff <forkDirA> <forkDirB>
```

`check` leaves the fresh snapshot on disk (named `--name`, else
`forkverify-<utc timestamp>`) so the failed case can be inspected afterwards,
and prints the path it read.

## Reading a `check`

```
fork: <forkDir>  (521 records, hash 9f2c1d6a8b0e4f37, tick 104233, minecraft:overworld)
live: <snapshotDir>  (521 records, hash 9f2c1d6a8b0e4f37, tick 20, minecraft:overworld)
  type                   fork  live  delta
  ---------------------  ----  ----  -----
  minecraft:sulfur_cube   512   512      0
  minecraft:item            8     8      0
  minecraft:cow             1     1      0
  total                   521   521      0
orderHash: MATCH
counts: MATCH
result: MATCH
```

`orderHash` is the acceptance criterion: it can only match if the lab ticks the
same entities in the same order. `counts` catches the other half - a restore
that put the right entities in the right order but gave one of them the wrong
type, which the hash cannot see. A fork directory with any validation issue can
never come back MATCH, because an unverifiable recording is not evidence.

## Reading a `diff`

`diff` answers the experiment question ("did run B differ from run A, and by
how much?") rather than the restore question:

* `order: first divergent index N` - the first position where the tick order
  differs, with the uuid each side has there. Everything before N ticks alike.
* `orderHash: MATCH|MISMATCH` plus per-type counts.
* the largest position and velocity deltas for uuids present in both runs,
  each row with the entity's uuid and type. `--top N` sets the row count
  (0 = all).

Exit code 1 means "not identical", which is a finding, not an error: two
experiments are supposed to differ. The tool prints zero-delta tables as a
single line so a 500-entity comparison stays readable.

## Decisions this tool made, and what was ambiguous

1. **Line order is the tick order.** The `order` field is validated against the
   line it sits on (`order` must equal the 0-based line index, and the set of
   values must be exactly `0..n-1`), but a restore follows the lines, not the
   field. The protocol describes the file as "one JSON object per line, in tick
   order", and a file that disagrees with itself is reported instead of
   silently reordered.
2. **Dry run is the default.** `--apply` is required to touch the game, and
   `--dry-run` exists so a script can say what it means. Nothing is sent while
   the tool is only printing.
3. **Restore drives `command`, not `restore`.** The bridge has a whole-file
   `restore` tool, but this tool issues one `/summon` per bridge call so that
   `--from`, `--limit`, the 100-entity progress report and per-entity failure
   handling all mean something. The client class still wraps `snapshot`,
   `snapshots`, `fork`, `restore` and `order` exactly as the contract names
   them (the selftest asserts the method names and parameter shapes).
4. **`check` reads the fresh snapshot off disk.** The bridge's `snapshot`
   returns a directory, and the per-type counts need the entities themselves -
   so the tool assumes what the rest of the toolchain assumes: the bridge and
   the game are on the machine running the tool. If the lab moved the snapshot
   directory somewhere this tool cannot read, that is a hard error, not a
   silent downgrade to comparing hashes only.
5. **`check` uses the fork's radius.** A radius-limited recording is compared
   with a radius-limited fresh snapshot; only `radius <= 0` or an absent radius
   asks for every entity. The radius is measured from the player (client
   vantage), so a lab that restores entities around a different reference point
   will show up as a count mismatch - which is honest, not noise.
6. **`nbt` is required by validation, tolerated by restore.** A record with no
   `nbt` is a validation error (the protocol says `nbt` is exactly what
   `/summon` accepts), and `restore` prints a warning and summons without it -
   the entity appears, its in-memory state does not, so a faithful restore
   cannot be claimed for it.
7. **Restore refuses only what it cannot act on.** A missing or malformed
   `type`/`pos`, or an unparseable line, stops the run; order, uuid and hash
   problems are warnings, because the lines are still summonable. `inspect`
   always shows the complete list.
8. **One command per top-level record, passengers included.** The protocol
   writes one line per entity the level ticks, so the restore issues exactly
   those lines in file order and does nothing clever about `passengers` or
   `vehicle`; attachment is the NBT's job, and de-duplicating on this side would
   break the order guarantee.
9. **Counts are compared as recorded**, `minecraft:cow` and `cow` are two
   different types. The mod writes fully qualified registry names on both
   vantages, and hiding a spelling difference would hide a real bug.
10. **`--limit 0` means "all remaining"**, and `--from` is a 0-based index into
    the recorded order; `--from` past the end prints nothing and exits 0.
11. **Missing `vel` is not an error.** `pos`/`vel`/rotation are a convenience
    copy of what is in `nbt`, so a snapshot without `vel` still validates; it
    just has nothing to compare, and `diff` says so instead of inventing zeros.
12. **A fork with validation issues can never pass `check`**, even when the
    live side matches it. Comparing against a file that contradicts its own
    metadata proves nothing.
13. **`--api-port` selects the instance; `target` is not used.** The bridge's
    `restore` and `order` take an optional `target`, and the client class passes
    it through, but the CLI reaches a lab the way the rest of this toolchain
    does: point `--api-port` at the daemon that owns that instance.

## The selftest

```bash
python tools/fork_verify.py selftest
```

It builds seven synthetic snapshot directories under a temp directory (an
identical pair, a pair with two entities swapped, a pair where one entity moved
0.5 blocks, one where a type changed, one whose recorded hash lies, and a
deliberately broken one), serves them through a fake transport with the
`LocalApiClient` shape, and asserts what `inspect`, `restore`, `check` and
`diff` report - including exit codes, the printed `/summon` lines, the
divergent index, the 0.5-block delta, the per-type table, and the bridge method
names and parameters the client sends. No bridge, no game, no real snapshot:
it is the part of this track that can be verified on a laptop.

## What this tool is not

* It never starts, stops or freezes Minecraft, and it never copies files: that
  is the mod and the bridge.
* It does not compute physics or compare blocks. `diff` compares entities; the
  region files are the bridge's business and a block-level comparison would be
  a different tool.
* It does not decide what a difference means. It reports the first divergence
  and the largest deltas; the experiment decides whether that is a finding.
