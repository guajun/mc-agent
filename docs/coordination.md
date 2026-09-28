# Multi-daemon write coordination

This page is the contract for [mc-agent#6](https://github.com/guajun/mc-agent/issues/6): what happens when two or more independent daemons talk to the same server at the same time. It is implemented in `mc-agent-interface-mod` (server authorization, ordering, leases - PR [#10](https://github.com/guajun/mc-agent-interface-mod/pull/10)) and in the Go runtime (client-side unknown-write handling - PRs [#14](https://github.com/guajun/mc-agent-bridge/pull/14)/[#15](https://github.com/guajun/mc-agent-bridge/pull/15)); the wire details live in [control-protocol.md](https://github.com/guajun/mc-agent-interface-mod/blob/main/docs/control-protocol.md).

## Sessions and permissions

- Each daemon authenticates with its own long-lived credential. A session is not a player and not a fake player; it is `sessionId` inside one server `runId` and `instanceId`.
- A credential carries `read` and/or `write`. Reads may run concurrently. Write authorization is enforced on the server thread, before the command is executed; a read-only session receives `forbidden`.
- Permissions are re-checked against the live credential store before every operation. Expiry and revocation take effect immediately (a per-tick sweep closes idle revoked sessions), a reload that downgrades permissions closes the affected sessions, and a write revoked before the game thread claims it is cancelled rather than executed. Events are read data: a write-only credential receives no pushed events and no replay.
- Player context and chat identity never grant authorization. A token cannot mint or revoke tokens over the protocol: `/mcagent control` is owner-only, and a remote write credential runs game commands at ADMIN level, so direct calls and `/execute`/`/function` chains cannot elevate. Revocation closes every live session that uses the credential and releases its leases. Rotation is issue/update/revoke, with no shared global secret.

## Ordinary writes: ordering and observability

- `command`, `mark` and `snapshot` are writes. The server executes them on the game thread (the network side only frames and queues), so per-session request order is preserved.
- Write ids are reserved atomically before execution: a first use creates a `pending` record, the same id with a different payload is `conflict`, the same id with the same payload while running is `request_in_flight`, and after completion it is the stored result with `"duplicate":true`. In-flight records are never evicted; a full ledger returns `server_busy` rather than losing a status.
- Every completed write gets a monotonic `writeSeq`; the reply carries it and a sequenced `write` event is appended to the same event stream every daemon receives:

  ```json
  {"type":"write","writeSeq":4,"op":"command","requestId":"c1","tokenId":"tk_...","ok":true}
  ```

  Two daemons can therefore agree on a total order of writes by following `seq`/`writeSeq`, without a lock or a coordinator daemon.
- Request ids are de-duplicated per credential: resending the same id returns the stored result with `"duplicate":true` instead of executing twice. This is the safe retry path for a lost reply.
- No daemon is a gateway. Any number of daemons can connect directly; the server does not nominate an owner for ordinary operations.

## Result-unknown, disconnects and restarts

- If a write's reply is lost (timeout or socket loss), the client must not replay it blindly. The client persists the request (target, instance, run, credential fingerprint) before it can leave the process; the server keeps a bounded (1024 entries / 30 minutes) `request_status` ledger keyed by `(credential, request id)`.
- A request that timed out before the game thread claimed it is cancelled before execution and is safe to retry; one that already started keeps its ledger record until the game thread reports the terminal outcome, even if the socket is gone. Running requests count against the session's pending budget after a timeout, so a slow client cannot pile unbounded work onto the game thread.
- `request_status` answers `pending`, `completed`, `failed`, or `unknown`. The Go daemon records an unknown write in `unknown_writes.json`, queries `request_status` after reconnecting, and publishes `request_resolved`; if the server has no record (for example after a game restart) or the target/run/credential scope no longer matches, the entry stays visible as `unresolved` and the CLI reports the request id instead of guessing. If the client ledger cannot be persisted, the daemon refuses to send the write.
- On a game restart the server has a new `runId`, an empty ledger and an empty event buffer; the client reports `game_restarted` and `event_gap`, and leases are gone. Nothing pretends the previous run's state or events survived.

## Exclusive operations and leases

The only exclusive primitive is a lease:

```
exclusive_acquire {key, ttlSeconds, label?}
exclusive_renew   {key, ttlSeconds}
exclusive_release {key}
exclusive_status  {key?}
```

- **Leases are advisory today.** They are reported and enforced only among the `exclusive_*` operations themselves. Ordinary writes (`command`, `mark`, `snapshot`) do **not** consult leases, and no composite write consumes a lease yet. The conflict matrix is therefore explicit:

  | Operation | Lease interaction | Result |
  | --- | --- | --- |
  | ordinary read (`state`, `player`, `context`, events) | none | concurrent, always allowed |
  | ordinary write (`command`, `mark`, `snapshot`) | none | serialized on the game thread, ordered by `writeSeq` |
  | `exclusive_acquire` while another credential holds the key | holder checked | `conflict` with the holder JSON |
  | `exclusive_*` on a key this credential holds | owner checked | success (renew/release/status) |
  | `freeze`, `fork`, `restore`, `verify`, `order` | not implemented | `capability_not_supported` with a reason |

- A lease belongs to the credential that acquired it, not to the socket. If the daemon disconnects or crashes, the lease **stays held until its TTL expires**; another credential gets `conflict` with the holder's JSON, and the original owner can renew or release after reconnecting with the same credential.
- The TTL is capped at one hour and renewal is explicit. Leases are released when the owning credential is revoked and dropped by a game restart (reported through the new `runId`).
- This is deliberately the minimal mechanism; there is no general scheduler, no cross-daemon queue and no daemon gateway. A future composite operation must acquire its lease and then check the holder around every step; until such an operation exists, nothing in this page claims that a lease protects ordinary commands.

## Composite freeze / fork / restore

`freeze`, `fork`, `restore`, `verify` and `order` are **not implemented** over the control protocol. They are refused with `capability_not_supported` and a reason. A tick freeze is not a checkpoint, a save-file copy is not a complete in-memory snapshot, and a remote `worldDir` is never treated as a local path. Until a complete freeze/snapshot/fork design exists (mc-agent#39), the honest answer is "unsupported"; the lease API above is the coordination primitive that design can build on. Local fork/restore remains available only through the explicit legacy Python path on the machine that owns the files.

## Acceptance mapping (mc-agent#6)

| Acceptance item | Where it is shown |
| --- | --- |
| Two real independent daemons read one server without cross-session replies/events | interface-mod#10 dedicated E2E: two Go daemons, concurrent routed calls, events on both (38/38) |
| Conflicting writes get a documented success/refusal/wait result, no silent interleaving | ordered `writeSeq` + `write` events; exclusive leases return `conflict` with the holder; composite operations are explicitly refused |
| An unauthorized session cannot borrow another connection's or a player's authority | per-credential `read`/`write` enforcement on the game thread; `forbidden` verified in the E2E |
| Disconnect, timeout, holder crash and game restart do not misreport unknown results | `request_status` ledger + daemon `unknown_writes.json`/`request_resolved`/`unresolved`; E2E covers timeout, daemon restart with `event_gap`, and game restart |
| Server with only the mod: no server daemon, player or client mod required | both E2E runs use a dedicated server and a LAN-hosted integrated server with only the mod installed |
