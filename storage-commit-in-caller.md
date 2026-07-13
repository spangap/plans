# Storage: commit-in-caller, side-effects-via-actor

Implementation plan for the N4 candidate shape from [storage-needs-work.md]
(§Needs, N4 "Diagnosis to build on"). Scope: kill the actor as a write-latency
chokepoint by moving the COMMIT (patch build + deepMerge + dirty routing) into
the calling task under CFG_LOCK, and demoting the actor to a fire-and-forget
side-effect processor (change fan-out, browser patch coalescing). All code
refs `spangap-core/esp-idf/src/storage.cpp` unless noted.

**What this does and does not solve.** Solves P3 (sync writes coupled to actor
latency) and the write half of P2 (writers no longer park behind the actor's
scheduling); makes N4 moot (the sync/async distinction disappears — every
write is cheap and local, read-your-writes is free). Does NOT touch P1 (one
resident tree), P4 (whole-file persistence), P5 (total browser mirror), or N5
in full — the lock-held save/dump `cJSON_Duplicate`s (storage.cpp:666, :2194)
remain the longest single CFG_LOCK holds and now bound the worst-case *writer*
park instead of the actor park. That trades a 5 s additive queueing pileup for
a few-hundred-ms mutex wait — an order of magnitude, and under rnsd's 2 s
connect budget — but it is a trade, not a fix; the duration logging in phase 0
is what verifies the trade on hardware.

## Current write path (what moves)

`storageSet` → op-string encode → `storageEmitOp` (:1231) → `storageSubmit`
(:1211): fast-path applies directly if caller IS the storage task or pre-spawn;
otherwise gp_alloc copy + ITS send to port 44, caller parks up to
`CONFIG_SPANGAP_STORAGE_OP_TIMEOUT_MS` (5 s) with `ITS_WAIT_PICKUP`. The actor
runs `storageApplyOps` (:1080): parse/validate (pass 1), then under CFG_LOCK
build patch + dedup-vs-committed + `deepMerge` + `collectChanges` +
`routePatchDirty` + `startSaveTimer` + SUB/UNSUB table mutation (pass 2), then
post-unlock `notifyChange` fan-out (queued to the notify worker) and SAVE
semaphore handling.

Note what pass 2 already is: a self-contained critical section with no fs I/O,
no blocking sends, no callbacks. That is why it can move verbatim.

## Target shape

**Commit (any task, under CFG_LOCK):** pass 1 + pass 2 of today's
`storageApplyOps`, unchanged in content — patch build, dedup, DEFAULT
conditional, `deepMerge`, `markExternalsDeletedUnder`, `routePatchDirty`,
`startSaveTimer`. Plus, still under the lock: append the collected change
records to a global handoff deque, then `xTaskNotifyGive(storageHandle)`
(wakes `itsPoll` — the mechanism the dump builder already uses, :2250).

**Actor (side effects only):** drain the handoff deque each loop pass; for
each record run today's `notifyChange` (:1053) — sub-table iteration stays
actor-owned, direct call for its own "" subscription (`dcAccumulateChange`,
which re-reads cfgRoot under a brief lock), `notifyEnqueue` → notify worker
for remote subscribers. Dump/patch pumping, SUB/UNSUB ops, and the DC session
unchanged.

**Op routing after the split:**
- `'S'`/`'d'`/`'D'` (data): commit locally in the caller. No ITS message, no
  copy, no timeout, no park.
- `'+'`/`'-'` (SUB/UNSUB): unchanged — still port-44 ops applied by the actor.
  Rare and init-time; preserves the actor-owned sub table and with it
  `storageOnTaskDeath`'s no-concurrent-mutator assumption (:922). Data op
  lists never contain them today (subscribe builds its own buffer, bypassing
  the accumulator); assert that in the local-commit path.
- `'W'` (SAVE): op deleted. `storageSave` pushes its semaphore onto
  `pendingSaveSems` under CFG_LOCK and calls `requestSave()` directly — the
  actor was pure relay here.

**Handoff deque contract:** entries are `(key, val≤STORAGE_NOTIFY_VAL_MAX)` —
same truncation as today's cross-task CHANGED messages, signal-not-transport.
Appended under CFG_LOCK ⇒ deque order == commit order ⇒ fan-out order == commit
order (the property the actor detour used to provide). Bounded (~512): on
overflow coalesce same-key entries first, then drop-oldest with a warn AND set
`dcDumpPending = true` if a client is connected — a dropped record is a missed
*signal* (handler never runs), not a stale value, and for the browser a forced
re-dump self-heals what a missed patch would silently desync. Pre-spawn
(`storageHandle == nullptr`): drain inline after commit, matching today's
fast path where boot writes notify synchronously.

**Silent commits** (`OP_F_SILENT`: defaults floods, browser-originated
deletes) skip `collectChanges` today and therefore append nothing — no
notifications, no browser echo. Semantics preserved by construction.

## Steps

**Phase 0 — instrumentation (land first, independently; also the
"do anyway" list in storage-needs-work.md):**
1. Duration warn (>50 ms) + debug log around every CFG_LOCK section that
   duplicates or prints: `writeExternalFile` (:664), `writeSettingsFileOnly`
   (:687), `dcBuildDumpInto` (:2193), the re-home dup (:2505).
2. Commit-latency measurement in `storageSubmit`: time from call to applied,
   warn past ~500 ms. Today this measures actor dispatch; after the swap it
   measures lock waits. Same probe, before/after comparison for free.
3. Flash + capture a baseline on the T-Deck (browser connect, LXMF traffic).

**Phase 1 — the swap:**
1. Split `storageApplyOps` into `opsParse` (pass 1, unchanged),
   `opsCommit(parsed, silent)` (pass 2 under CFG_LOCK, returns the change
   vector), and `sideEffects` (deque append). Reject `'+'`/`'-'`/`'W'` in the
   data path (warn + skip; they can't occur).
2. `storageSubmit(ops, sync)` for data lists: call parse+commit+append on the
   calling task, always. Delete the gp_alloc copy, the ITS send, the `sync`
   parameter, and the "op send timed out (storage task stuck?)" failure mode —
   a timed-out sync write today *loses the write* (:1224); after this, data
   writes cannot be lost in flight.
3. Handoff deque + cap/coalesce/overflow policy; drain call in
   `storageTaskFn`'s loop next to `dcPollConfig()`; inline drain pre-spawn.
4. `storageSave` direct semaphore path; delete the `'W'` opcode.
5. `onStorageOp` (port 44) keeps serving SUB/UNSUB lists only.
6. Update the contract comments: storage.h header block, storage.cpp preamble
   (":an ACTOR" description no longer covers writes), `storageBegin/End` note
   (bracket semantics unchanged — see footgun F4).

**Phase 2 — semantics dividends (small, same series):**
1. `storageDefault` returns the exact outcome (computed inside the commit
   lock) instead of the benign-TOCTOU pre-check (:1645).
2. `mergeIncomingPatch` (:2405) wraps its leaf loop in a bracket — one commit
   per browser patch instead of one lock take per leaf.

**Phase 3 — cleanups (separate, after soak; each optional):**
1. Shrink the port-44 inbox guard: `CONFIG_SPANGAP_STORAGE_OP_MSG_MAX`
   (192 KB, sized for 128 KB Nomad 'J' ops that no longer travel — :2518).
2. Patch-tree accumulator: build cJSON patches directly in
   `storageBegin/End` and `storageSetTree` instead of op strings, killing the
   print→parse round-trip for big subtrees (:1728 prints, :1111 re-parses —
   now both on the same task, pure waste). Defer: touches the bracket
   format, no latency urgency once writes are local.
3. Delete dead sync-park plumbing in its.cpp if port-44 was its last
   `ITS_WAIT_PICKUP` user with big payloads (check other users first).

## Implications & footguns

**F1 — Never block, never call out, under CFG_LOCK in the commit path.** The
deque append must be plain memory + `xTaskNotifyGive`. The invariant that made
the old design safe — subscriber callbacks run *after* unlock, on the actor —
must survive: the commit path invokes no user code at all. Any future "just
call the handler inline" shortcut reintroduces P2 with interest (arbitrary
subscriber code under the global lock, from arbitrary tasks).

**F2 — The snapshot holds are now writer stalls.** A writer landing during
`dcBuildDumpInto`'s whole-tree duplicate waits the full duplicate (~hundreds
of ms at today's 300 kB cfgRoot). That is the new worst case for rnsd — under
its 2 s budget, but the phase-0 numbers must confirm it, and P1/N5 (shrink the
tree / shrink the holds) remain the real fix. Mitigation available if numbers
disappoint: duplicate cfgRoot child-by-child with lock release between
top-level subtrees (dump correctness tolerates per-subtree tearing — patches
are held until the dump drains, :2557, and each chunk is per-subtree anyway).

**F3 — Missed signals on deque overflow.** Drop-oldest loses *distinct* keys,
not just duplicates — a subscriber (lcd battery icon, net state) may never
hear a transition. Today's notifyQueue has the same policy (:999) at the same
class of cap, so this is not a regression, but the deque sits earlier in the
chain and absorbs *all* subscribers' traffic. Same-key coalescing before
drop-oldest keeps counter bursts (the realistic flood) from evicting real
transitions. Log drops with the key.

**F4 — Read-your-writes inside brackets is still absent.** Commit-in-caller
makes lone writes read-your-writes for free, but `storageBegin/End` still
accumulates and commits at End — reads inside an open bracket see committed
state (:1324). Don't let the headline feature suggest otherwise; the header
comment must keep saying it.

**F5 — Same-priority mutex unfairness persists, relocated.** FreeRTOS wakes
the highest-priority waiter, and a releasing task can re-take before the woken
task runs. Before: a spinning writer starved the actor. After: it starves
other writers/readers. Holds are now µs-class patch merges except the F2
snapshots, so pressure drops massively, but a tight `storageSet` loop on one
task remains antisocial. Priority inheritance now actually helps (mutex waits
inherit; ITS inbox parks never did — that asymmetry is a big part of why the
incident's 5 s wedge was possible).

**F6 — Fan-out is now more deferred than before.** Commit returns before any
subscriber has heard. Today's sync write already returned only pickup-order
(fan-out went through the notify worker), so the *kind* of guarantee is
unchanged, but the window widens from "actor picked it up" to "actor woke and
drained". Audit: nothing in-tree waits on a subscriber as write confirmation
(subscribers re-read by key — the documented contract), but grep for
subscribe-then-set handshake patterns in lxmf/rnsd during implementation.

**F7 — Heap attribution shifts.** cJSON nodes are allocated by whichever task
commits, so `top` blames writers (lxmf, rnsd) instead of `storage` for tree
growth. Cosmetic, but the re-home duplicate at task start (:2504) becomes even
less representative — leave it (boot attribution) and note it.

**F8 — Caller CPU/stack for big commits.** A 128 kB `storageSetTree` (Nomad
page) now deep-merges and (until phase 3.2) print+parses on the caller. Depth
is tree-depth (~9) — fine on 8 kB PSRAM stacks; the alloc storm is the
caller's own data and one copy *cheaper* than today (no op buffer, no inbox
copy). Verify nomad's task stack headroom anyway.

**F9 — Timer/ISR contexts.** No storageSet from ISRs exists (the ITS path
couldn't serve them either). esp_timer-callback writers would now block the
esp_timer task on CFG_LOCK, bounded by F2 — grep for `storageSet` reachable
from `esp_timer` callbacks during implementation; today's 5 s park was worse,
so any hit is an improvement, not a blocker.

**F10 — SUB/UNSUB ordering vs local commits.** A subscribe is live when the
actor applies it; commits from other tasks meanwhile don't notify the
new subscriber. Unchanged in kind (the same race existed between a sub op and
other tasks' write ops), and `NOW_AND_ON_CHANGE` covers the gap by re-reading
after subscribe returns. State it in the subscribe comment.

**F11 — The actor's fast path disappears as a special case.** Browser patches
(`mergeIncomingPatch`, on the storage task) become ordinary local commits —
identical code path as every other task. Deleting the `me == storageHandle`
branch removes a whole class of "which context am I in" bugs, but check the
one behavioral difference: today's fast path notifies *inline*; after the
swap the actor's own commits go through the deque like everyone else's and
drain later in the same loop pass. `dcAccumulateChange` re-reads cfgRoot, so
value-wise nothing changes; ordering-wise dump-vs-patch holding (:2557)
already covers the connect window.

**F12 — What port-44 pickup-parking still gates.** After the swap, the only
sync ITS round-trips to the actor are subscribes. A wedged actor (shouldn't
happen once nothing heavy runs on it, but) would stall init-time subscribes,
not data. Acceptable; the 5 s timeout keeps its one remaining user.

## Validation

- Phase-0 vs post-phase-1 numbers on the T-Deck: commit latency p99 from rnsd
  hot path (expect µs–ms except during snapshot holds), zero
  "owned aux to port 44 … not picked up" warns.
- Incident repro (storage-needs-work.md §incident): long LXMF message from the
  browser over a LoRa link + forced browser reconnects. Expect: no link
  build-then-teardown loop, no DC liveness flap.
- Burst test: rnsd-style storageSet loop from a prio-1 task while a browser
  connects (dump build) and a save flushes — watch F2 warns and deque
  high-water.
- Boot paths: cold boot with a legacy `s.lxmf` monolith (migration commits
  pre-spawn), first-boot overlay, `set`/`show`/`unset`/`save` CLI, browser
  patch echo both directions, NOW_AND_ON_CHANGE consumers (lcd status bar).
- Soak: notify-drop and deque-drop counters at zero over a day of normal use.

## Hardware result — first attempt, REVERTED (2026-07-13)

Phases 0–2 were implemented and landed **together** (mistake: phase 0 was
supposed to land alone first for a baseline) and flashed to the T-Deck. Result:
a sustained storm of `storage: commit latency` warns, **0.5–1.3 s on every
commit**, across every writer — `auto`, `gps`, `espnow`, `lora`, `tcp`, `rnsd`,
and even `esp_timer`. Reverted (spangap-core revert of the swap; reticulous-builds
zips reverted to the prior firmware). The commit-in-caller branch survives in
git history for a second attempt.

What the numbers say:
- **Not the F2 snapshot holds.** Zero `cfgHoldReport` (`CFG_LOCK hold …`) warns
  fired — no single holder exceeded 50 ms. So the save/dump/re-home
  `cJSON_Duplicate`s were *not* the problem this run.
- **It is a mutex herd (F5), under-weighted in this plan.** Many short holds ×
  a high write rate × FreeRTOS same-priority unfairness = a given low-priority
  writer is repeatedly jumped and accumulates ~1 s of wait. Moving write
  serialization off the actor's fair ITS queue onto `CFG_LOCK` is what exposed
  this: the old design let exactly one task (the actor) take the write lock, so
  there was no herd; commit-in-caller has ~10 tasks contending. Per-hold cost is
  ms-class (not the µs the Target-shape assumed) because `navigatePath` is
  O(siblings-per-level) on the bloated `s.lxmf.…msgs` tree, and it runs in both
  the commit dedup and `dcAccumulateChange`.
- **No baseline.** Because phase 0 didn't land alone first, there is no
  before-number — we cannot say how much of the ~1 s is a true regression vs.
  dispatch latency that already existed and the new probe merely made visible
  (the old path had no per-write timing, only the 5 s ITS timeout warn).

Before a second attempt:
1. Land **phase 0 only**, flash, capture the T-Deck baseline (commit/dispatch
   latency distribution under the same LXMF-backchannel + iface-stat load). Only
   then is the swap judged against real numbers.
2. Cut the write rate at the source: bracket lxmf's per-field message-status
   writes (`stage`/`last_error`/`method`/`ts`/… are landing as separate commits
   in the send path — see lxmf.cpp send/proof paths) into one
   `storageBegin`/`storageEnd`. Fewer commits ⇒ fewer lock acquisitions ⇒ less
   herd, and it helps the *current* actor design too.
3. Only if 1–2 leave a real case for the swap, redo it — and treat F5, not F2,
   as the risk to design against (mutex fairness / acquisition frequency, e.g.
   a dedicated lightweight handoff lock so the actor's drain never contends on
   `CFG_LOCK`, and keeping `dcAccumulateChange` off the per-change hot path).

## Out of scope (tracked in storage-needs-work.md)

Bulk/config store split (N1), cJSON replacement (N2), demand paging +
record-granular persistence (N3), snapshot-free reads (N5 beyond F2's
mitigation), browser summary sync (N6), externals migration hazards (N8), and
the lxmf 2 s `itsConnect` budget fix — that last one is independent and should
land regardless (rnsd.cpp:2856 hardcodes the budget; a late-but-successful
connect still becomes a built-then-destroyed link until it does).
