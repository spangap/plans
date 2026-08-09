# Structuring the LoRa component

> Status: **implemented through Phase 5**; Phase 6 (the radio pool) remains
> deferred by intent. The structure it produced is recorded in
> `iface-lora/INTERNALS.md`, which is the current-contract document — this
> file is the plan that got there. Companion to [`SUPE.md`](SUPE.md), which
> specifies the protocol this carries, and
> [`simulation.md`](simulation.md), which is where the policy questions it
> exposes get answered.

## Objectives

1. **One direction of dependency.** Every module answers questions; one module
   makes decisions. No module reaches into another's state to decide whether it
   may act.
2. **One seam between the bridge and the radio**, and that seam is a queue. What
   arrives is enqueued; what transmits is dequeued; nothing else passes.
3. **No lock in the protocol engine.** Single-threaded by contract, serialised by
   the host at the boundary.
4. **Every decision findable.** Departure policy, budget resolution, power
   control and channel choice each live in one named function with stated inputs.
5. **The arithmetic is host-testable and the engine is host-runnable**, on a
   machine with no ITS, no FreeRTOS and no radio.
6. **A detour asks for a radio, not for its own radio.** One hailing channel is
   one interface; which silicon carries the detour is not the protocol's business.

## Executive summary

`lora.cpp` is 8583 lines and holds the ITS bridge, the RNode serial endpoint, the
CSMA machine, Reticulum packet observation, the neighbour table, adaptive power,
per-channel airtime, telemetry and the whole SUPE state machine. It works. It also
produced, in one week, four separate deadlocks and starvations of the same shape:
module A consulting module B's internal state to decide whether it was allowed to
act. `annPoll` waiting on `supeHoldsRadio` while `supePoll` waited on `annReplay`
is the canonical one, and it was not a coding error — the structure invites it.

This plan cuts the file into modules with a single direction of dependency, puts a
packet queue in the one place everything has to pass through, and rewrites the
SUPE state machine against the revised protocol as a lock-free single-threaded
engine behind a small host interface.

Seven phases, each of which builds and runs on its own. **Phases 0–2 are
restructuring with no protocol change**, so the device keeps working throughout
and any regression bisects to a mechanical move. **Phase 4 is the rewrite** and is
where the new protocol lands. **Phase 6 is optional** and exists so the seam is
built before it is needed rather than retrofitted.

The single most valuable item is Phase 2. The deadlock class it removes is removed
by construction, not by care.

---

## 1. Where we are

| Concern | Where it lives now |
|---|---|
| chip dispatch, RadioLib, families | `lora.cpp`, X-macro + `radioBegin`/`radioSet*` |
| ITS bridge to rnsd | `lora.cpp`, `drainOneOutbound`, `rnsdInject` |
| RNode serial endpoint | `lora.cpp`, ~600 lines of `rnode*` |
| CSMA / APPC | `lora.cpp`, `csma*`, `appc*` |
| Reticulum observation | `lora.cpp`, `neiObserve`, `neiAnnounce` |
| neighbour / peer table | `lora.cpp`, ~40 `nei*` functions |
| adaptive power | `lora.cpp`, `ap*` plus `supeAp*` |
| per-channel airtime | `lora.cpp`, `supeRing*`, `supeVerdict*` |
| SUPE frames, ladder, deadlines | `supe.{h,cpp}` — **already pure, already tested** |
| SUPE state machine | `lora.cpp`, ~2000 lines of `supe*` |
| telemetry / LoRaMon | `lora.cpp`, `loraMon*`, `publish*` |
| CLI | `lora.cpp`, `cli*` |

`supe.{h,cpp}` is the model for everything else here: pure functions, no radio, no
platform, 196 host checks. The rest of this plan is an argument for extending that
discipline outward rather than a new idea.

Two tasks run: the radio task (RX, TX-done, CSMA, polling) and the interface task
(ITS, publishing). `SupeState.lock` is a recursive mutex because both touch the
state machine.

## 2. Target structure

Dependencies point one way — into the engine. Nothing in blocks 1–8 knows the
engine exists, so all eight are host-testable without a radio.

```
              ┌──────────────────────────────────────────┐
   rnsd ──ITS─┤                                          │
              │  9  bridge      enqueue / dequeue only   │
  RNode ─ser──┤                                          │
              └────────────────┬─────────────────────────┘
                               │
                        ┌──────▼──────┐
                        │  3  queue   │  the only seam
                        └──────┬──────┘
                               │
              ┌────────────────▼─────────────────────────┐
              │ 10  SUPE engine    the only decider      │
              └─┬──┬──┬──┬──┬──┬──┬──┬────────────────────┘
                │  │  │  │  │  │  │  │
     1 radio ───┘  │  │  │  │  │  │  └─── 8 telemetry
     2 pool ───────┘  │  │  │  │  └────── 7 medium access
     4 peers ─────────┘  │  │  └───────── 6 airtime
     5 chanplan ─────────┘  └──────────── 0 supe core (pure)
```

| # | Module | Files | Answers |
|---|---|---|---|
| 0 | **supe core** | `supe.{h,cpp}` | frame codec, ladder resolution, deadlines. Pure, exists, keeps its host tests |
| 1 | **radio** | `lora_radio.{h,cpp}` | chip dispatch, RadioLib calls, family capabilities, time-on-air. Knows nothing of Reticulum or SUPE |
| 2 | **radio pool** | `lora_pool.{h,cpp}` | `grant(band, channel, ms) → radio \| none`. Size 1 until Phase 6 |
| 3 | **queue** | `lora_queue.{h,cpp}` | holds packets, refcounts, per-peer caps, backpressure. No radio knowledge at all |
| 4 | **peer table** | `lora_peers.{h,cpp}` | `family`, `speaks_supe`, `path_loss`, `last_heard`, `absent_until` |
| 5 | **channel plan** | `lora_chanplan.{h,cpp}` | `channels(regime)`, `max_bw`, `txp_cap`, `recently_quiet`, `reuse_gap_ok` |
| 6 | **airtime** | `lora_airtime.{h,cpp}` | `may_i(ch, ms)`, `record(ch, ms)`. Counts aborted transmissions |
| 7 | **medium access** | `lora_csma.{h,cpp}` | hailing channel only: `may_i_now()`, `hold_until(t)` |
| 8 | **telemetry** | `lora_mon.{h,cpp}` | the event ring; LoRaMon and the graph feed |
| 9 | **bridge** | `lora_bridge.{h,cpp}`, `lora_rnode.{h,cpp}` | ITS ↔ queue, RNode serial ↔ queue, routing rule |
| 10 | **engine** | `supe_engine.{h,cpp}` | the state machine. Calls all of the above; nothing calls it |
| — | **observer** | `lora_observe.{h,cpp}` | Reticulum packet inspection → peer table. One direction, no decisions |
| — | **service** | `lora.cpp` | tasks, config lifecycle, wiring |
| — | **CLI** | `lora_cli.{h,cpp}` | `lora`, `lora n`, `lora supe` |

**The observer is the one Reticulum-specific module** and is kept behind
`peers_observe(bytes, meta)` so a port can replace or omit it.

**Modules 0, 3, 4, 5, 6 and 10 must stay free of ESP-IDF, RadioLib and FreeRTOS**
— that is what makes them host-testable, and the test Makefile enforces it by
simply not having the include paths. Modules 1, 2, 7, 8, 9 and the service are
platform code and never compile on the host.

## 3. The five stores

Every piece of state has exactly one owner. Naming them is most of the work.

| Store | Holds | Owner |
|---|---|---|
| **packet queue** | in-flight packets, refcounted | queue |
| **peer table** | per-peer aggregates: family, SUPE support, path loss, last power used, last heard, absence record | peer table |
| **channel state** | per channel: last sensed level, last used (reuse gap), airtime accumulator | channel plan + airtime |
| **event ring** | recent TX/RX with channel, power, SF/BW, RSSI/SNR | telemetry |
| **session state** | one per active detour, ephemeral | engine |

"Where did that packet go out, and at what power" is the **event ring**, not the
packet header — the packet is freed at transmit. The graph feed is already 90 % of
this store and becomes its only consumer plus the debugger's.

## 4. The packet and the queue

```c
struct LoraPkt {
    uint8_t*  bytes;           /* heap block we own; free() when refs hits 0 */
    uint16_t  len;
    uint32_t  first_seen_ms;   /* the only timestamp; everything else is policy */
    uint16_t  peer_id;         /* index into the peer table; PEER_NONE if unknown */
    uint8_t   refs;            /* destinations counted at ingress */
    uint8_t   flags;           /* origin: its / rnode / radio */
};
```

**The zero-copy mechanism exists and is `itsRecvRef` / `itsSendOwned`**
(`spangap-core/esp-idf/include/its.h`), packet links only: the block is a plain
heap allocation, ownership transfers on success, whoever ends up with it calls
`free()`. `RNSD_PORT_IFACE` is opened `ITS_PACKET` (`rns/esp-idf/src/rnsd.cpp`),
so both directions of the rnsd leg qualify:

- **outbound**: `itsRecvRef` at ingress hands us the block; it lives in the queue
  untouched and is freed at transmit-done. The current code's copying `itsRecv`
  into a stack buffer goes away.
- **inbound**: allocate `4 + len` (the `rx_signal` RSSI/SNR prefix rides in
  front), fill, `itsSendOwned`. On backpressure the call returns 0 and we still
  own the block — that is the drop point, not a retry loop.
- **the RNode leg is a stream port** (`packetBased=false`) and stays copied: its
  ingress allocates a block and its egress serialises out of one. Only the rnsd
  leg is zero-copy, and that is where the volume is.
- **fan-out costs one allocation**: `refs > 1` with `itsSendOwned` means the last
  consumer gets the original and earlier ones get copies. With three pipes this
  is at most one copy per packet, only when an RNode is attached.

Deliberately absent, and each for a reason:

- **no modulation, channel or power.** Not known at enqueue; decided at
  negotiation for a whole train. A field here invites setting it early and then
  two code paths disagreeing about which is authoritative.
- **no expiry.** `first_seen_ms` is the fact; age limits are read by whoever
  looks.
- **no type.** SUPE control frames never enter the queue — they are latency
  critical, never batched, and emitted by the engine directly. On receive the type
  is needed for dispatch and lives in the receive path, not here.

**Routing rule**, stated once and applied at ingress: *a packet crosses only within
its radio's group — never between rnsd interfaces, never between hailing channels.
That is rnsd's job.* With the rule stated, `refs` is one lookup at ingress.

**Backpressure has two levels**, because the congestion is per-peer and the signal
to rnsd is one bit:

1. per-peer cap → drop oldest. Our decision, invisible to rnsd. Reticulum
   tolerates loss.
2. global cap → stop accepting. The one bit rnsd sees.

**Open, and to be read rather than guessed:** what the reference implementation
and ours actually do when an interface stops accepting. `RNS/Interfaces/` plus
`Transport`'s handling of a full outbound queue. "Drops silently" and "blocks the
whole transport" want different numbers, and the caps should not be picked before
someone has looked.

## 5. The engine

**Single-threaded by contract.** Every entry point is called from one context; the
host serialises. On ESP-IDF that is a mutex held at the boundary, on a bare
Arduino nothing at all. The engine itself has no locking, which removes the
re-entrancy hazards that recursive mutex exists to paper over.

The contexts the ESP-IDF boundary must actually cover — this list is why the
current recursive mutex exists, and the wrapper owns all of it:

| Context | Entry points arriving from it |
|---|---|
| radio task (`loraTaskMain`) | RX-done, TX-done, CSMA grants, polling |
| interface task (`loraIfTaskMain`) | enqueue notifications, publishing |
| `ESP_TIMER_TASK` | the engine's step timer |
| console task | CLI (`lora supe`, `lora a`) |
| storage callbacks | config changes |

**No blocking inside the engine, anywhere.** A step that needs to happen later is
scheduled via `host->schedule` and the entry point returns. No delay, no wait on
a completion, no spin on a deadline — the transmit path is `tx()` plus a
continuation run from `supe_on_tx_done`.

**Everything the engine needs from the platform, in one struct:**

```c
struct SupeHost {
    uint32_t (*now_ms)(void);
    void     (*schedule)(void* ctx, uint32_t at_ms);
    /* radio */
    bool     (*tune)(void* ctx, uint32_t hz, uint8_t sf, uint16_t bw_khz,
                     uint8_t cr, uint16_t sync, uint16_t preamble);
    bool     (*tx)(void* ctx, const uint8_t* f, uint16_t len, int8_t dbm);
    void     (*rx)(void* ctx);
    int16_t  (*rssi)(void* ctx);
    /* queue */
    bool     (*peek)(void* ctx, uint16_t peer, LoraPkt** out);
    void     (*consume)(void* ctx, LoraPkt* p);
    /* up */
    void     (*deliver)(void* ctx, const uint8_t* b, uint16_t len);
};
```

Callbacks in: `supe_on_rx`, `supe_on_tx_done`, `supe_on_timer`, `supe_on_enqueue`.
That is the whole surface. Nothing in it names ITS, FreeRTOS, esp_timer or
RadioLib, which is what makes the engine runnable on the host and, later, on
Mark's RNode.

**The one decision the protocol deliberately does not make:**

```c
enum { DETOUR_NO, DETOUR_NOW, DETOUR_WAIT };
int should_detour(const PeerView*, const QueueView*, const ChanView*,
                  uint32_t* wait_until_ms);
```

Pure, no side effects, no radio access, one call site. SUPE.md §18 requires only
that it be findable; what it should decide is a question for
[`simulation.md`](simulation.md) §7. Do not scatter it across the transmit path,
and do not commit to a rule before it can be measured.

## 6. What the revised protocol forces

Independent of the restructuring, these follow from [`SUPE.md`](SUPE.md) and
should be built once rather than twice:

| Change | Touches |
|---|---|
| `SUPE_GRANT` (`0xC5`); `HERE` deleted, `0xC8` burned | core codec, engine |
| `SUPE_START` carries family/ceiling and a byte **load** in 32-byte units, no duration | core codec, engine, queue (load is computed from it) |
| the peer chooses channel and budget; refusal with reason | engine, channel plan, airtime |
| both sides send `MANIFEST`; `count 0` closes, `count 0 + length` is the grace | engine |
| four path-loss pairs per detour | peer table |
| absence ladder: three requests, power up and ceiling down, then a minute of drop; one request thereafter; cancelled by any evidence of life | engine, peer table |
| `START` adapts in power, `GRANT` never does | power control, engine |
| ladder resolution integer-only, ties stated, golden vectors normative | core, host tests |
| hold released early by the access procedure's fixed interval | medium access |
| airtime counts aborted transmissions | airtime |

The last two are small and easy to lose. Both are stated as requirements in the
spec precisely because getting them wrong fails silently.

## 7. Phases

Each phase builds, runs, and is worth stopping after.

### Phase 0 — mechanical extraction, no behaviour change

Move code into the files of §2 with no logic changes: pure cuts, headers, and the
minimum forward declarations. Nothing renamed, nothing reordered, no function
bodies touched.

Best value per unit of risk in the plan, and it makes every later diff readable.
The one discipline: if a move requires a logic change to compile, stop and leave
that piece behind for its own phase.

Mechanics that are part of this phase, not afterthoughts:

- every new source file joins `LORA_SRCS` in `esp-idf/CMakeLists.txt`
- a function crossing a file boundary loses `static` and gains its module's
  prefix; one that stays inside keeps both. This is the *only* renaming the
  no-renaming rule permits
- `conditional/spangap-lcd/loramon_lcd.cpp` reads the telemetry ring in-process
  through `loraMonSnapshot`; when the ring moves to `lora_mon.{h,cpp}` that
  export moves with it and the pane must keep linking
- the split leaves everything in one component, so include paths do not change

*Done when:* `lora.cpp` is under ~800 lines of service wiring, the build is
clean, and the device behaves identically.

### Phase 1 — the stores, one owner each

Give each of §3's five stores a module and an interface. Split the neighbour table
into **peer table** (aggregates, answers questions) and **observer** (Reticulum
inspection, fills it). Move adaptive power's per-peer state into the peer table
and its control loop into its own module.

*Done when:* no module reads another's struct fields directly.

### Phase 2 — the queue

The one that matters. Everything inbound is enqueued; the engine dequeues.
`drainOneOutbound`, `supeQFill`, `supeHeadVerdict`, `annReplay` and the flags that
coordinate them all collapse into it.

Delete the lateral gates as they become unreachable — `supeHoldsRadio` consulted
from `annPoll`, `csmaResetAccess` called from a drain pass. If a gate is still
needed after this phase, that is information: it means the seam is in the wrong
place.

*Done when:* the SUPE path and the announce path share no state except the queue,
and the deadlock class is unreachable rather than avoided.

### Phase 3 — the pure core, extended

Ladder resolution to integer rules with a stated tie-break; generate
`supe-ladder-vectors.txt` over the full cross-product; extend the existing host
tests to the new frames (GRANT codec, the load quantisation, the deadline table
of SUPE.md §14.7). No device behaviour changes in this phase at all.

The rig already exists: `esp-idf/test/`, plain `make` builds, runs and
regenerates `golden.txt`. The vector file lives there too, checked in, generated
by the same run. The Makefile's rule stands: if the test build ever needs an IDF
header, something has leaked into the core.

*Done when:* the vector file exists, is checked in, and the host tests reproduce
it.

### Phase 4 — the engine

Rewrite the state machine behind `SupeHost`, on the revised protocol, with no
lock. This is the large one and it should be written new rather than edited: the
existing machine encodes a sender-chooses protocol and a channel-and-step
escalation that no longer exist.

Build it host-first — the engine plus a stub host is a program that can be stepped
through a whole transaction on a laptop, and that should work before it is wired
to a radio.

There is no compatibility work: the old frames are deleted outright. Every node
that ever spoke them is on this bench, and the 14-day expiry retires stale builds
by itself.

*Done when*, in two halves: **host-verifiable** — a full bidirectional transaction
including a refusal, an absence ladder and the count-0 close runs against the stub
host, and the device build is clean. **On-air** — two devices complete a
bidirectional detour, which is the user's to run.

### Phase 5 — access order and the ledger

The hold releasing early by the fixed interval; airtime counting aborted
transmissions; per-channel reuse gaps consulted before a grant; refusal reasons
mapped to backoffs.

Small, and separated from Phase 4 so a fairness regression does not bisect into a
protocol rewrite.

### Phase 6 — the radio pool (optional)

Make `grant(band, channel, ms)` real. `SUPE.worker` marks a radio as available;
workers present no interface to the daemon and serve any hailing channel in their
own band. Interface number stays equal to radio number, so numbering has gaps and
nobody has to hold a mapping in their head.

Deferred by intent. The seam is built in Phase 2 and 4 so that this phase is
additive rather than a second rewrite.

## 8. Not doing

- **Cross-band workers.** 868 handing off to 2.4 GHz is a routing problem with a
  different peer set on each side. Workers serve their own band.
- **Retransmission inside a detour.** Packets are freed at transmit; the layers
  above retry. If this ever changes, it changes the queue's contract and should be
  its own decision.
- **A generic message bus between modules.** The dependency graph in §2 is a
  DAG; direct calls express it and a bus would hide it.
- **Renaming for its own sake.** `nei*` becomes `peers_*` because the module moves
  and the table's job is being restated. Nothing else gets renamed.

## 9. Risks

**Phase 0 is boring and will be tempting to skip or to merge with Phase 1.** Don't.
A mechanical phase is what makes the next four bisectable.

**Phase 4 is a rewrite of working code.** The mitigation is that Phase 3 makes the
arithmetic independently testable and the host-first order means the state machine
is exercised before hardware is involved. The thing not to do is rewrite and
re-tune at once.

**The storage stalls seen during earlier testing** — `applyPoll` blocking for
seconds with no operations pending — are not caused by any of this and are not
fixed by any of this. They turned latent races into reliable failures, so a
restructure that removes the races will also hide them. Worth keeping in view
rather than declaring solved.

**The engine has no lock, and that is only safe if the contract holds.** One
context, enforced at the boundary. A future caller that invokes an engine entry
point from an interrupt or a second task breaks it silently. Assert the calling
context in debug builds rather than trusting a comment.

## 10. Open questions

- **What rnsd does when its `itsSend` toward us stalls** (§4). The mechanism is
  now known — we stop consuming, the packet link backs up, rnsd's send times out
  — but what rnsd *does* on that timeout is not. `onTransportRecv` and callers in
  `rns/esp-idf/src/rnsd.cpp` are where to read before sizing any cap.
- **What `should_detour` should decide** (§5). Simulation, not argument.
- **Whether the observer's identity inference survives contact with the reference
  implementation.** The virtual RNode interface of `simulation.md` §6 is how to
  find out.
- **Whether the event ring should persist across a reboot.** It is the only record
  of what actually went out, and every debugging session so far has wanted the
  minute before the reset.

## 11. Ground truth for the implementing session

Everything above says what to build; this says where things are and how work is
verified. All paths relative to `iface-lora/` unless stated.

**Sources.** `esp-idf/src/` — `lora.cpp` (8583 lines, the subject),
`supe.{h,cpp}` (the pure core, keep), `rolling.{h,cpp}` (rolling average,
keep), `esp_idf_hal.cpp` (RadioLib HAL, keep). New files register in
`LORA_SRCS` in `esp-idf/CMakeLists.txt`.

**Host tests.** `cd esp-idf/test && make` — builds with plain g++, runs 196
checks, regenerates `golden.txt`. This is the fast loop; use it constantly.

**Device builds.** Build to verify compilation; the user flashes and runs
everything on-air. State the board after every build — the target flips
silently with `.spangap-build`. Builds are slow: finish a whole change set, then
build once.

**The spec is [`SUPE.md`](SUPE.md)** and it describes the *revised* protocol; the
code implements the superseded one. Where code and spec disagree, the spec wins
and the code is what this plan exists to change. `afa.md` and `psa.md` carry
derivations; [`simulation.md`](simulation.md) holds the deliberately-unanswered
policy questions — do not answer them in code.

**Settings.** Keys live in `straddle.yaml` (SUPE section: `enable`, `afa`,
`adaptive_txpower`, `announce_interval`; Phase 6 adds `worker`). The web panel
`browser/src/panels/LoraPanel.vue` is hand-written — `web: false` rows in
straddle.yaml render on the LCD only, so a new key needs both touched.

**Docs.** `INTERNALS.md` is the engineering record; §§2, 6, 6a, 12–15 and 18–19
describe the structure this plan replaces. Each phase rewrites the sections it
invalidates,
as the current contract, not as a change log. Facts learned during the work go
there, never into session memory.

**Frame dispatch rests on one assumption** (INTERNALS §19.3): SUPE types are
`0xC0`–`0xDF`, never ending in 0 or 1, disjoint from split framing's reachable
bytes and from Reticulum flags without an access code. Any change to receive
dispatch preserves it.

**Logging.** Through the component's own logger; per-peer chatter behind
`logIsVerbose(tag)`; `CONFIG_LOG_MAXIMUM_LEVEL_VERBOSE=y` is already in the
buildable's `sdkconfig.defaults`. Library diagnostics route through the log
callback tagged by library name, never printf.

**Habits that earn their keep here**: after moving code, scan for
use-before-declaration before building, not after the build fails; grep every
diff that touches transmit or wait paths for `delay`, `vTaskDelay`,
`transmit(`, and `while` around a clock.
