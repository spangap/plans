# Storage needs work: problems and needs

Problem statement + requirements for a storage rework. Not a design yet.
Grounded in the 2026-07-13 incident (LXMF link setup failing repeatedly while
sending a long message from the browser) and the code reading it triggered.
Code references are to `spangap-core/esp-idf/src/storage.cpp` unless noted.

## What storage has become

`storage` was shaped as a small config store: a cJSON tree in PSRAM, one
actor task applying writes, a recursive big lock (`cfgMux`), synchronous
cross-task writes for read-your-writes, change fan-out, and a full JSON
mirror to the browser over a DataChannel.

It is now also the **database** for every subsystem: full LXMF message
history, saved announce stores (rnsd, lxmf), contacts, nomad pages, the
flattened IANA timezone DB. All of it permanently RAM-resident in one cJSON
tree, all serialized through one priority-1 actor, all mirrored to the
browser on every connect. Config-store semantics applied to bulk,
append-mostly domain data. Nothing is ever evicted (`lxmf.cpp` says so:
"No eviction yet"); the tree, and every whole-tree operation, grows without
bound as the device is used.

Measured on the T-Deck today: `s.lxmf.json` = 239 kB — one legacy external
holding the entire lxmf state; cfgRoot is ~300 kB-class overall. Every
problem below scales with this number.

## The incident, as evidence

Sending one long message produced a self-sustaining failure loop
(see the 17:34–17:36 log of 2026-07-13):

1. The storage actor stopped dispatching ops for ≥5 s — every task's sync
   `storageSet` parked (`owned aux to port 44 … not picked up` from auto,
   rnsd, lxmf simultaneously).
2. rnsd's hot path does *bursts* of sync `storageSet`s (link state, counters,
   timestamps — e.g. rnsd.cpp:4330). Parked per-op behind the actor, rnsd
   stopped draining its own ITS inbox: tcp iface frames dropped after 100 ms,
   and lxmf's `rnsdLinkOpen` connect (hardcoded 2 s ack budget,
   rnsd.cpp:2856) timed out.
3. On that timeout, `itsConnect` sends CONNECT_CANCEL (its.cpp:1737, correct
   slot-leak defense). rnsd later drained its backlog in order: processed the
   CONNECT — created the link, `kickoff … estab_timeout=24s` — then the
   CANCEL 250 ms later: `consumer detached, tearing down`. Every attempt
   built a real link and destroyed it before one LoRa round-trip.
4. Meanwhile the browser's 2 s liveness ping went unanswered → reconnect →
   full config re-dump → another whole-tree `cJSON_Duplicate` under
   `CFG_LOCK` → more actor stall → next flap. Self-sustaining.

No single component takes 5 s. The wedge is additive: repeated 100–500 ms
lock holds (save snapshots + dump snapshots) × unfair same-priority mutex
handoff × a priority-1 actor on the crowded core-1 × rnsd's sync-write
bursts × mismatched deadlines (2 s connect budget vs 5 s op timeout).

## General problems

**P1 — One resident cJSON tree for everything.** RAM cost grows with usage
forever; every deep-copy (save snapshot, browser dump) and every walk scales
with total history, not with the working set. cJSON specifics make it worse:
per-node heap allocs (duplicate of the lxmf subtree ≈ tens of thousands of
PSRAM allocs), deep recursion (stack hazards, the 16 KB dump-builder stack),
untyped leaves re-parsed at every read.

**P2 — One big lock.** `CFG_LOCK` serializes readers, the actor's applies,
save snapshots, and dump snapshots. The lock-held `cJSON_Duplicate`s
(storage.cpp:518 save; storage.cpp:1930 dump) hold it for hundreds of ms at
current sizes — and FreeRTOS same-priority mutex handoff lets the releasing
task re-take it before the woken actor runs, so the actor can starve across
consecutive grabs.

**P3 — Synchronous writes couple every task's latency to the actor.**
`storageSet` is a sync ITS round-trip (park up to
`SPANGAP_STORAGE_OP_TIMEOUT_MS` = 5 s) so read-your-writes holds — but most
writers (rnsd counters, state publishes) don't need it, and hot-path tasks
inherit every storage hiccup. rnsd's inbox blackout = burst length × actor
dispatch latency; upstream deadlines (2 s link connect ack) then fail even
for sub-second hiccups.

**P4 — Persistence granularity is the whole file.** One dirtied key rewrites
the entire external: 239 kB duplicated (lock-held), printed, and written to
SD per message state transition. Write amplification grows with history;
save frequency is per-activity. The externals mechanism splits the *files*
but not the *resident tree*, and the device still carries the legacy
monolith (see P7).

**P5 — The browser mirror is total.** Every (re)connect dumps the full
config tree — including all message history — via a whole-tree duplicate
under the big lock; every change is coalesced/patched on the actor
(`dcPollConfig` also parses/merges up to 64 KB browser patches on-actor).
The dump cost is O(total state), paid at exactly the worst moment (the
reconnect after a stall), which is what closes the flap feedback loop.

**P6 — Everything storage-side runs at priority 1 on core 1** (actor, save
worker, notify worker, fs worker, dump builder) among ~10 other prio-1
tasks, below rnsh's prio-5 ssh worker. Dispatch latency has no bound under
load; all the costs above get wall-clock-multiplied.

**P7 — Migration/lifecycle hazards in externals.**
- `scanExternals` (storage.cpp:1229) resurrects registrations from
  *filenames on disk*, so the legacy monolithic `s.lxmf.json` persists
  forever; `storageNewTreeFile` dedups exact prefixes only, so per-conv
  registrations would merely coexist with it.
- Load-order clobber bug: externals are sorted longest-prefix-first (right
  for dirty routing, wrong for loading) — when a per-conv file exists next
  to the monolith, `loadExternals` attaches the conv file first, then the
  stale monolith **replaces** the whole `s.lxmf` node
  (`attachAtPath`, storage.cpp:1222), silently discarding the newer data on
  the next boot.

## Needs

**N1 — Split hot config from bulk data.** Two stores with different
contracts:
- *Config*: small (tens of kB), bounded, RAM-resident, fast reads,
  read-your-writes, change notify — roughly what storage promises today.
- *Bulk/domain data* (message history, announce stores, nomad pages):
  demand-paged reads from the filesystem, record-granular appends/updates,
  never fully resident, never mirrored wholesale. Consumers (lxmf UI, etc.)
  fetch pages/ranges.

**N2 — Drop cJSON as the internal representation.** Typed key/value (or
records) in the core; JSON only at the browser boundary, generated on demand
per subtree. No whole-tree deep copies anywhere on a lock; no per-node
allocation storms; no deep recursion.

**N3 — Read-what-you-need + write-back, off the hot path.** Load on demand
(or a small resident working set), dirty-record tracking, and a dedicated
writer task doing periodic/idle write-backs with record granularity —
amortized, bounded write amplification instead of whole-file rewrites. A
crash loses at most the write-back window (the 60 s `flash_delay` debounce
already accepts this class of loss).

**N4 — Async writes by default.** Fire-and-forget for counters/state
publishes; sync (read-your-writes) as the explicit exception. Hot-path tasks
(rnsd above all) must never park on the store. Latency budget: any storage
op visible from a network hot path bounded well under the tightest upstream
deadline (2 s link connect).

Diagnosis to build on: writes are only synchronous because of the
read/write **asymmetry** — reads bypass the actor (direct `cfgRoot` access
under `CFG_LOCK` in the caller) while writes queue behind it, so the writer
must wait for the actor to catch up or lose read-your-writes on its own
key. The guarantee only matters for the *writing task itself*; other tasks
never had ordering guarantees. Two consequences:

- *Available today:* an async per-op variant (`storageSubmit` already takes
  `sync=false`) is safe for any caller that doesn't immediately read back
  the key it wrote — all of rnsd's hot-path writes qualify.
- *Candidate shape for the rework:* **commit in the caller, side effects
  via the actor** — the caller applies the merge to the tree under the lock
  (making read-your-writes free and every write cheap and local), then
  hands notify fan-out, browser patch coalescing, and dirty-routing to the
  actor as fire-and-forget. This keeps what the actor detour genuinely
  buys (commit-ordered notifications, single-owner coalescing state,
  race-free subscription table) while removing it as a latency chokepoint:
  nothing waits on it anymore.

**N5 — Reads never blocked by snapshots or flushes.** Whatever replaces
`CFG_LOCK` must make persistence and browser-sync snapshots cheap
(per-subtree locks, copy-on-write, or single-writer with lock-free reads).
A save or a browser connect must not add >10 ms to an unrelated
`storageGet`/`storageSet`.

**N6 — Browser sync proportional to need.** On connect, send config and
*summaries* (conversation list, last-N messages); page the rest on demand.
Keep patches as deltas. The reconnect path must be cheap specifically
because it runs when the system is already degraded.

**N7 — Keep the good contracts.** Change notification as signal-not-transport
(subscribers re-read by key), the actor/worker split for fs I/O, dot-path
addressing for config, `fsStateDir()` routing. Subsystem code shouldn't need
rewriting beyond swapping bulk-data users onto the new bulk API.

**N8 — Migration.** One-time split of legacy monoliths (`s.lxmf.json`) into
the new layout; fix or sidestep the load-order clobber before per-conv files
ever coexist with the monolith; `scanExternals`-style disk-driven
registration needs an explicit story for renamed/retired prefixes.

## Independent of the rework (cheap, do anyway)

- Raise `rnsdLinkOpen`/`rnsdChannelOpen`'s hardcoded 2 s `itsConnect`
  budget above the storage op timeout, or make lxmf retry a timed-out
  connect instead of cancelling — a late-but-successful connect currently
  becomes a built-then-destroyed link.
- Take sync storage writes out of rnsd's per-packet/per-link-event path now;
  it's the amplifier in every stall regardless of storage internals.
- Add duration logging around the two lock-held duplicates and an
  op-dispatch-latency warn in the actor — the numbers that motivate and then
  validate the rework.
