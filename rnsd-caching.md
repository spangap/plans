# rnsd caching: one directory store, three pools

Design for replacing microReticulum's (µR's) two parallel caches — the identity
cache (`Identity::_known_destinations`) and the path table
(`Transport::_new_path_table`) — with a single arena of packed records owned by
rnsd, read lock-free by every consumer, and persisted as a raw image.

The store is written to be **µR-resident and portable**: it depends only on µR
types and a small platform-hook struct, never on spangap-core's storage,
allocator or filesystem helpers. Everything spangap-specific — the key-space
projection, the claim marshalling, interface policy — sits in the `rns`
straddle on top of it, and is described in §9. §10 maps every persistent and
runtime artefact to its storage key or file path.

## The measured problem (2026-08-11)

A T3-S3 gateway (2 MB PSRAM) bridging a busy public TCP peer to a LoRa mesh
aborts after ~40 minutes of uptime:

```
rnsdTaskMain                       rnsd.cpp:6373      try { Transport::jobs(); }
 └ Transport::jobs                 Transport.cpp:911  for (auto& packet : outgoing) packet.send();
    └ Packet::send                 Packet.cpp:493
       └ Transport::outbound       Transport.cpp:1057 _new_path_table.get(dest_hash, entry)
          └ Codec<DestinationEntry>::decode   DestinationEntry.cpp:170   announce_packet.unpack()
             └ Packet::unpack      Packet.cpp:421     _data.assign(raw+35, raw.size()-35)
                └ operator new → gp_new  mem_new.cpp:21  throw std::bad_alloc
                   └ __cxa_throw → __cxa_get_globals  eh_globals.cc:150 → terminate → abort
```

Four independent defects compose into it.

**D1 — The identity cache is a duplicate.** `Identity::validate_announce`
(`Identity.cpp:483`) stores `{packet_hash, public_key, app_data}` for every
announce; a few hundred lines later `Transport::inbound`
(`Transport.cpp:2571`) stores the *entire announce packet* for the same
destination, and that packet's data is
`public_key ‖ name_hash ‖ random_hash ‖ signature ‖ app_data`. Every byte of the
identity entry is already present in the path entry.

**D2 — It is stored in the worst available container.**
`std::map<Bytes, IdentityEntry>` with three `shared_ptr`-backed `Bytes` per
entry is roughly ten heap blocks for ~100 bytes of payload. Measured at the cap
of 1000 entries: ~520 kB, 70% of rnsd's PSRAM and a quarter of the board.

**D3 — The two tables evict independently, so neither invariant holds.** The
identity cache evicts by announce time; the path table by
`(last_used, timestamp)`. A route in daily use loses its key; a key survives for
900 destinations with no route. The cap was raised to 1000 to paper over the
first, which is what costs the 520 kB.

**D4 — Every `bad_alloc` is fatal.** `CONFIG_COMPILER_CXX_EXCEPTIONS_EMG_POOL_SIZE`
is 0, so `__cxa_throw` cannot allocate the per-thread exception globals and
`eh_globals.cc:150` calls `std::terminate()` directly. The `catch
(const std::bad_alloc&)` handlers in `cull_path_table`, `cull_announce_table`,
`Identity::remember` and `cull_known_destinations` are unreachable code.

Underneath all four: the path table has **no ingress-interface gate at all**.
Insert happens for every validated announce from any interface; only `expires`
varies by interface mode (`Transport.cpp:2364`), and both interfaces on this
device default to `MODE_GATEWAY` (`tcp.cpp:213`, `lora_bridge.cpp:12`), so even
that differentiation is inert. A hundred path slots are shared between a dozen
LoRa neighbours and a testnet firehose, evicted by one global ordering. The
gateway forgets the mesh it is the sole custodian of — which is a correctness
failure, not a memory one: a path response is a *signed* announce that only a
node holding the original bytes can emit.

## Target state

| | before | after |
|---|---|---|
| identity cache | 1000 × ~520 B = 520 kB | gone |
| path table | 100 × ~340 B = 34 kB | folded into the directory pool |
| replay state | inside each path entry | guard pool, 512 × 28 B = 14 kB |
| directory | — | 256 × 160 B = 40 kB |
| announce blobs | inline in every path entry | 64 × 320 B = 20 kB |
| total | ~554 kB | ~74 kB (default budget; see §3.3) |

## 1. Data model

Three independent pools in one arena. **The destination hash is the join key;
no pool stores an index into another.** A stored link would be a second
expression of a relationship the key already carries, and therefore a
consistency obligation across a lock-free reader and a raw persisted image, in
exchange for a lookup that costs microseconds at these pool sizes.

### 1.1 Guard pool — 28 B, ~512 slots

Replay and recency suppression for *every* destination whose announce we have
validated, whether or not we retain anything else about it. This pool exists
because `should_add` (`Transport.cpp:2282`) currently reads `_expires` and
`_random_blobs` off the retained path entry: dropping retention without a guard
would make every repeat announce look novel and re-enter the retransmission
queue, at ~1.5 s of LoRa airtime each.

```
off  size  field
  0     4  dest4        truncated destination hash
  4     4  emitted      announce emission time from the random hash, seconds
  8     2  local_age    coarse local time (minutes, wrapping) at last update;
                        basis for aging and eviction ordering
 10    16  fp[4]        4 × 4-byte random-blob fingerprints, ring
 26     1  cursor       ring write position + occupancy count
 27     1  flags        bit0 in-use; bits 1-7 reserved, zero
```

*Truncation is deliberate.* At 512 resident slots a 4-byte destination collides
on roughly one insertion in 8×10⁶; at 10 announces/second that is about one
collision every ten days. Under a collision two destinations share one entry
and the emission comparison mixes two originators' clocks, so the loser can be
suppressed **persistently while the winner keeps refreshing** — not once.
Mitigation: two consecutive fingerprint misses on an entry reset it (treat as a
new destination). One line of code; a reset costs one possible duplicate
forward.

*Emission comparison is relative, never absolute.* `announce_emitted` comes from
the originator's clock (bytes 5..9 of the random hash, Unix seconds), so it is
only ever compared against the same destination's previous value, by signed
difference so wrap is harmless. The idiom and its rationale are already in the
tree at `rnsd.cpp:6396`. `emitted` is a full u32: a 16-bit field was considered
and rejected — its ±9.1 h window breaks for destinations announcing daily (real
on public networks) and for any persisted guard image reloaded after a longer
power-off, both of which would judge fresh announces "older" and suppress them.

*`local_age` exists because nothing else in the record is on our clock.*
`emitted` values are originator-time and only comparable within one
destination, so without a local stamp the pool has no basis for LRU eviction or
for aging out stale entries. Coarse wrapping minutes suffice; ambiguity after a
wrap merely evicts a slightly-wrong slot.

The guard pool is **written and read only on the rnsd task**, so it needs no
sequence counter.

### 1.2 Directory pool — 160 B, ~256 slots

Everything shared, announce-derived and application-agnostic, plus the routing
fields. This is the only pool read cross-task.

```
off  size  field
  0    16  dest             full destination hash — exact, this is the key
 16    64  pubkey           announce public key
 80    10  name_hash        aspect hash, carried in the announce
 90     4  last_heard       Unix seconds
 94     1  hops
 95     1  flags
 96     4  claims           claim mask: which consumers claim, class + layer bits
100    16  received_from    routing: next-hop transport id
116    16  iface_hash       routing: receiving interface hash
132     4  expires          routing
136     4  last_used        routing
140     4  timestamp        routing: announce time
144     2  seq              sequence counter, see §2
146     1  prio             compiled eviction priority, written at claim time (§4)
147     1  pad              zero
148     4  claim_touch      Unix seconds of last claim assert/touch
152     8  reserved         zero-filled
160
```

**`pubkey` lives here, not in the blob.** Deriving the key from the announce
would weld the two layers together and make "drop the blob, keep the entry"
useless — precisely the degradation the design exists to provide.

**`name_hash` is stored, not an aspect string.** `Destination::name_hash`
(`Destination.cpp:134`) computes `full_hash(expand_name(NONE, app_name,
aspects))[:10]` — with a NONE identity, so it is a pure function of the aspect
text and independent of who announced. Any consumer can classify by comparing
against a value precomputed once at startup. See §8.

**Times narrow from `double` to `u32` Unix seconds.** Expiry granularity is
hours and `PATH_LAST_USED_STALE` is 48 h, so second resolution is ample.

**What does *not* live here:** display name, cost, ratchet, capability bits,
page metadata, `app_data` generally. Those are parsed from `app_data` in
per-application formats and belong in each application's own record store.

### 1.3 Blob pool — 320 B, ~64 slots

The raw signed announce, retained only for destinations we may be asked to
answer a path request for.

```
off  size  field
  0    16  dest
 16     2  len
 18     2  flags + reserved
 20   300  raw              announce packet as received
320
```

An announce whose raw form exceeds the slot is simply not retained; a path
request for such a destination falls through to the normal discovery path.
Application data on the expensive edge is small by necessity, so this bounds
the pool without bounding usefulness. The slot size is a tunable (§10).

Like the guard pool, blobs are touched only on the rnsd task — no application
reads them — so no sequence counter.

### 1.4 Layered lifetime

A record may exist at any of three depths, and eviction removes them in
reverse order under memory pressure:

```
guard only          "I have seen this announce"     28 B
+ directory         "I know who this is"           188 B
+ blob              "I can answer for this"        508 B
```

The ordering is the point: losing the ability to serve a path request for a
destination while still being able to tell the user who it is degrades a
network service before it degrades information.

## 2. Concurrency

Single writer (the rnsd task), many readers. Only the **directory pool** is read
cross-task, and only it carries a sequence counter.

Reader protocol, copy-out only, no pointer ever escapes the store:

```
s1 = rec->seq                    /* even = stable */
if (s1 & 1) retry
copy record into caller's buffer
s2 = rec->seq
if (s1 != s2) retry
```

Writer increments `seq` to odd before mutating a record and to even after.
`seq` is accessed with atomic loads/stores (release on the writer's closing
increment, acquire on the reader's first load) so neither the compiler nor the
other core reorders the payload copy around the checks. Slots never relocate;
eviction marks a slot free without compaction, and the sequence counter turns a
reader racing a reuse into a retry rather than a torn read.

There is no blob-index race, because there is no blob index: a reader that
wants a blob looks it up by destination hash, and applications never want one.

## 3. Persistence

One file (§10), atomic `.new` + rename, written on a debounce. Contents are a
cache — see §3.2 — so a crash costs at most the debounce window.

### 3.1 Format

```
header
  magic          "RDIR"
  format_ver     u16   structural; mismatch → discard the whole image
  feature_ver    u16   additive; lower is accepted, unknown fields read zero
  guard_slot_sz, guard_slots
  dir_slot_sz,   dir_slots
  blob_slot_sz,  blob_slots
section 1  guard pool, verbatim, holes included
section 2  directory pool, verbatim, holes included
section 3  blob pool, verbatim, holes included
```

Dumping verbatim including free slots keeps load a single read with no
rewriting, and each section stands alone because no section references another.

**Free lists are rebuilt by scanning occupancy on load, never persisted** — one
fewer structure that can be internally inconsistent in a half-written image, at
a cost of microseconds.

**Slot counts live in the header because they are boot-time values (§3.3).** A
loaded image with *smaller* pools than the running configuration is accepted —
slots copy into the larger arena and the remainder starts free. Larger-on-disk
than configured, or a slot-size mismatch, discards the image.

**The two version numbers are what make `reserved` usable.** A structural change
bumps `format_ver` and discards; an added flag consumes reserved bytes and bumps
`feature_ver` only. The discipline that rides with it: **zero must be the safe,
legacy-equivalent meaning of anything ever carved out of the reserve.** A flag
where 0 means "off" is fine; a field where 0 means "answer for this" would
silently reinterpret every older record.

`static_assert` on `sizeof` of each record *and* on the offset of every named
field. Reserved bytes give a false sense of layout safety while the compiler
can still pad elsewhere.

**Stale routes are lazily invalidated after load.** A loaded routing layer may
reference an `iface_hash` that no longer resolves (interface re-registration
changes hashes), which would make `has_path()` true while sends silently drop —
the exact trap documented in `rns/INTERNALS.md` §Gotchas. The first
`rdirPeekRoute` whose `iface_hash` fails to resolve clears that record's
routing fields and returns a miss, so the caller path-requests instead of
black-holing.

### 3.2 What may live here

Only reconstructible data, because discard-on-mismatch must be safe.

The public key qualifies at **zero marginal cost**: sending to a destination
requires a path, acquiring a path means a path request, and the path response
*is* an announce carrying the key. A cached key only ever saves work when you
hold a live path but no key, which cannot happen once both live in the same
record.

Applications therefore stay authoritative for *"this is a contact"* — their own
versioned, migratable record, holding the destination hash. The store is
authoritative for *"here is what I currently know about that destination"*. The
destination hash is stored in both places; sixteen bytes per contact is the
correct price for that ownership boundary.

Claims ride the image *compiled into the record* (mask, `prio`, `claim_touch` —
§4) and are likewise reconstructible: the authoritative intent is each
application's own records, and consumers re-assert at startup. A discarded
image therefore loses no intent, only the head start.

**One exception to name:** a key seeded out-of-band — `rlpg.cpp:812` injects a
mailbox owner's key from a signed authentication frame, not from an announce —
is *not* reconstructible from the network. It is re-seeded on the next owner
session, so a discarded image is self-healing, but if that ever stops being
acceptable the key belongs in rlpg's own record rather than here.

### 3.3 Sizing

`rdirInit` takes a **byte budget**, not slot counts. rnsd derives the budget at
boot from free PSRAM (clamped between a floor and a ceiling; overridable, §10)
and the store splits it across pools in fixed proportion. Slot counts land in
the image header. This is what prevents the next instance of the defect that
motivated this design: constants tuned on an 8 MB board and inherited by a
2 MB one. Pools are boot-time constant; resizing live under lock-free readers
is not worth its complexity.

### 3.4 Write path

The store never performs file I/O. `rdirSnapshot(buf, cap)` memcpys the arena
into a caller-owned buffer *on the writer task* — consistent by construction,
no lock, a few tens of kB. The rns straddle debounces (60 s class, matching
`s.storage.flash_delay` semantics), calls `rdirSnapshot` on the rnsd task, and
hands the buffer to the existing storage persist worker for the atomic
write-and-rename. A blocking filesystem write never runs on the rnsd task —
the same discipline the storage actor already follows.

## 4. Claims

A claim is a **preference, not a lifetime**. Retention is the maximum over all
claims on a record; the store arbitrates and may break any of them under
pressure.

```c
typedef struct {
    uint8_t  consumer;   /* LXMF | NOMAD | RNSH | RLPG | RNSD */
    uint8_t  klass;      /* PERSIST | EPHEMERAL */
    uint8_t  layers;     /* DIR | DIR_BLOB */
    uint32_t decay_s;    /* ordering scale since last touch; 0 = default */
} rdir_claim_t;
```

There is deliberately **no `expires_at`**, because a duration reads as a
guarantee and is the first thing that has to break at 88% memory. Claims order
eviction; they do not bound it.

The invariant that keeps this safe: *an unbounded claim population may not carry
a long duration.* Contacts are bounded by the address book, mesh neighbours by
the size of the mesh — both may persist indefinitely. Internet announces are
unbounded, and therefore carry no claim at all.

**Claims are compiled into the record at assert time.** The claim call updates
the record's `claims` mask, `prio` byte and `claim_touch` stamp; `rdirEvictTo`
reads *only in-record data*. This is what keeps eviction µR-resident and
portable while claim vocabulary and defaults stay in the rns straddle: the
straddle compiles, the store compares. `decay_s` enters the comparison as
"ephemeral claim considered lapsed when `now − claim_touch > decay_s`".

**Transport: ITS aux message, app task → rnsd task.** Verified: every
browser-originated action that could create a claim (add contact, bookmark)
arrives as a storage write (`*.cmd.*` sentinel or `s.*` patch) that lxmf /
nomad / rlpg observe via `storageSubscribeChanges`, whose callbacks run on the
*subscribing app's task* — so claims always originate on an app task, never
from the browser path directly. The rns straddle marshals them with
`itsSendAuxOwnedByTaskHandle` to an aux port on rnsd (the pattern storage ops
already use); the aux handler calls `rdirClaim`/`rdirClaimTouch`/
`rdirClaimDrop`/`rdirSeedPubkey` on the rnsd task. Fire-and-forget — claims
are advisory, no reply exists to wait for.

**Eviction order**, first evicted → last, within each pool:

| order | category | ordered by |
|---|---|---|
| 1 | guard-only entries (no directory record) | `local_age`, oldest first |
| 2 | unclaimed directory entries, no live route | `last_heard` |
| 3 | ephemeral claims, lapsed (`claim_touch` + `decay_s` past) | `claim_touch` |
| 4 | ephemeral claims, live | `claim_touch` |
| 5 | interface-retained (edge custody), unclaimed | `last_heard` |
| 6 | persist claims | `claim_touch` |
| 7 | in-use routes (`last_used` within `PATH_LAST_USED_STALE`) | `last_used` |

Blob slots free in the same category order among blob-holding records —
dropping a blob demotes "can answer" to "know who", it never touches the
directory entry.

**Reserve the `ANSWER_FOR` claim bit now.** The mask sits in the persisted
image, so adding a class later forces a `format_ver` bump and a discarded
arena — on a gateway, at exactly the moment an empty directory hurts most.

## 5. Ingest

```
announce on iface I for dest D
    ├── validate_announce                            (unchanged, µR)
    ├── bypass = outstanding path request for D      (see below)
    ├── rdirGuardFresh(D, blob, emitted, bypass) ─┬─ false → drop, no forward
    │                                             └─ true  ↓
    ├── forward decision                             (unchanged for now, see §12)
    └── retain decision → rdirIngest(D, announce, layers)
```

**The path-response bypass is an explicit input.** Relays answer path requests
from cached announces, so a requested `PATH_RESPONSE` *always* carries an
already-seen random blob; treating it as a replay breaks path discovery after
its first success (the fork already fixed exactly this once — `rns/INTERNALS.md`
§1.1, keyed on an outstanding `_path_requests` entry). `rdirGuardFresh` takes
`bypass` and skips the fingerprint check (still updating `emitted` and the
ring) when it is set.

The single `should_add` boolean at `Transport.cpp:2315` splits in two. Today it
gates the retransmission queue (`_announce_table.insert`, lines 2429 and 2460),
the immediate rebroadcasts (`new_announce.send()`, lines 2495, 2516, 2552) *and*
the path-table put (line 2571) from one test computed over retained state.
After the split:

```c
bool fresh  = rdirGuardFresh(D, blob, emitted, bypass);   /* forwarding input */
bool retain = I.retain_on_announce()                      /* storage decision */
           || rdirHasClaim(D)
           || rdirInUse(D);
```

**`retain_on_announce` is a flag on µR's `Interface`**, set at registration the
way `mode` is — this is what keeps the ingest split portable while the *policy*
(which interfaces retain) stays outside µR (§9). Per ingress: expensive or edge
interfaces retain on announce because re-acquisition is costly and because we
are the custodian; cheap or vast interfaces retain only what was resolved on
demand, claimed, or is in active use. `last_used` already protects a route in
daily use — it fails today only because the firehose flushes the table around
it.

## 6. Store API — µR-resident

No allocation, no locking, copy-out. Callable from any task except where noted.

```c
/* readers — any task */
bool   rdirPeekRoute (const uint8_t dest[16], rdir_route_t* out);
bool   rdirPeekPubkey(const uint8_t dest[16], uint8_t out[64]);
bool   rdirPeekEntry (const uint8_t dest[16], rdir_entry_t* out);
size_t rdirCopyBlob  (const uint8_t dest[16], uint8_t* buf, size_t cap);  /* 0 = none */
void   rdirForEach   (void (*cb)(const rdir_entry_t*, void*), void* ctx);

/* rnsd task only (the rns straddle marshals app calls here via ITS aux, §4) */
bool   rdirGuardFresh(const uint8_t dest[16], const uint8_t blob[10],
                      uint32_t emitted, bool bypass);
void   rdirIngest    (const uint8_t dest[16], const rdir_announce_t*, uint8_t layers);
void   rdirTouchUsed (const uint8_t dest[16]);
void   rdirClaim     (const uint8_t dest[16], const rdir_claim_t*);
void   rdirClaimTouch(const uint8_t dest[16], uint8_t consumer);
void   rdirClaimDrop (const uint8_t dest[16], uint8_t consumer);
void   rdirSeedPubkey(const uint8_t dest[16], const uint8_t pk[64]);
void   rdirEvictTo   (size_t dir_budget, size_t blob_budget);
size_t rdirSnapshot  (void* buf, size_t cap);      /* consistent arena copy */
```

### 6.1 Platform hooks

The only things the store may not do for itself. Supplied at init by whoever
embeds it; on this platform, by rnsd.

```c
typedef struct {
    void* (*arena_alloc)(size_t);              /* once, at init */
    bool  (*image_load)(void* buf, size_t* len);   /* boot only */
    uint16_t (*local_minutes)(void);           /* guard local_age basis */
} rdir_platform_t;

bool rdirInit(const rdir_platform_t*, size_t byte_budget);
```

Writing the image is *not* a hook — the embedder calls `rdirSnapshot` and owns
the file (§3.4). Nothing else in the store references a filesystem, an
allocator policy, a configuration key or a logging facility outside µR's own
macros.

### 6.2 µR call sites that change

- `Transport::outbound` (`Transport.cpp:1057`) — `_new_path_table.get` becomes
  `rdirPeekRoute`. This is the per-packet path and the crash site; after the
  change it allocates nothing and constructs no `Packet`. The returned
  `iface_hash` resolves through the existing `find_interface_from_hash`.
- `Transport::inbound` announce ingest — `should_add` splits per §5;
  `_random_blobs` leaves the record entirely and the `std::set<Bytes>` whose
  destructor `rnsd.cpp:2033` complains about goes with it.
- `cull_path_table` — becomes `rdirEvictTo`.
- `Identity::recall` — reads the directory pool. `_known_destinations`,
  `save_known_destinations` and `load_known_destinations` are deleted.
- `Identity::recall_app_data` — **blob-present only**: `app_data` is not a
  directory field, so the call succeeds only for destinations holding a blob.
  Its consumers (`rnsdRecallAppData`; lxmf's mailbox-dest fallback chain)
  already fall back to their own catalogues, which after §8 receive `app_data`
  with every announce — the right long-term home.
- `Transport.cpp:2824` and `:3842` — the two remaining `recall()` sites, both
  transport-node duties (validating a link-request proof we are transporting
  for two other parties; answering a path request). Both are co-extensive with
  holding a path, so the directory satisfies them by construction.

## 7. Storage interface — outside µR

spangap-core gains exactly one generic addition. It names no consumer.

```c
typedef struct {
    bool (*get)    (const char* key, char* out, size_t outLen);
    bool (*exists) (const char* key);
    void (*forEach)(const char* prefix, void (*cb)(const char*, const char*));
} storage_provider_t;

bool storageRegisterProvider(const char* prefix, const storage_provider_t*);
```

**Dispatch happens before `CFG_LOCK`.** `storageGetInt`, `storageGetStr` and
`storageExists` all take the recursive configuration mutex first
(`storage.cpp:2042`, `:2067`, `:2031`) and only then consult the structured-DB
router. A provider hook placed inside that path would inherit exactly the
contention this design exists to escape, so it is a new branch ahead of the
lock, not an addition to `sdbRoute`.

Contract on the provider: safe to call from any task, never blocks, never
allocates on behalf of the caller. A provider namespace is excluded from the
browser dump — a directory pages, it does not mirror.

rnsd registers `rnsd.dir.` and translates keys to `rdirPeek*` calls. That
adapter is the *whole* of the coupling between the store and spangap-core.

## 8. Announce fan-out

```
rnsd → subscriber:  hops(1) | dest(16) | identity_hash(16) | pubkey(64) | app_data(N)
```

The public key is added. Without it an application cannot cache anything
actionable and must call back into a node-global identity map to send — which is
the structural reason `_known_destinations` had to be large in the first place.

Separately, the per-subscriber aspect filter at `rnsd.cpp:1893` recomputes
`hash_from_name_and_identity` for every subscriber on every announce; the
comment at `rnsd.cpp:1909` and the `vTaskDelay(1)` beside it exist because that
cost stalls the browser transport during bursts. Since `validate_announce` has
already proven `dest_hash == full_hash(carried_name_hash ‖ identity)[:16]`,
comparing the announce's carried `name_hash` against a value precomputed at
subscribe time is exactly equivalent and costs a ten-byte comparison. This is a
standalone fix, independent of everything else here.

## 9. Where each piece lives

| piece | home | portable to upstream µR |
|---|---|---|
| three pools, records, seqlock, eviction | µR | yes |
| image load + snapshot, via hooks | µR | yes |
| guard / ingest split | µR (`Transport::inbound`) | yes |
| `retain_on_announce` interface flag | µR (`Interface`), set at registration | yes |
| `recall` from directory | µR (`Identity`) | yes |
| claim mask / prio / touch in the record | µR | yes |
| claim vocabulary, compilation, ITS aux marshalling | `rns` straddle | no |
| which interfaces retain (policy + config) | `rns` straddle | no |
| image file, debounce, persist-worker write | `rns` straddle | no |
| storage provider adapter (`rnsd.dir.`) | `rns` straddle | no |
| `storageRegisterProvider` | spangap-core, generic | no |
| fan-out frame + aspect filter | `rns` straddle | no |

## 10. Layout: storage keys and filesystem

**Config (persisted, `s.` / per-iface namespaces):**

| key | default | meaning |
|---|---|---|
| `s.rnsd.dir.budget_kb` | 0 | arena byte budget; 0 = auto from free PSRAM at boot, clamped |
| `s.rnsd.dir.blob_slot` | 320 | blob slot size; change discards the image (structural) |
| `s.tcp.peers.<n>.retain_announces` | 0 | per-ingress retention (§5), passed at iface registration |
| `s.lora.<n>.retain_announces` | 1 | ditto |
| `s.rnsd.identity.cache_max` | 250 (stage 0) | legacy cap; **deleted at stage 3** with the map |

**No config keys for claims.** Authoritative intent stays in each app's own
records (`s.lxmf.id.<n>.contacts.*`, nomad bookmarks); the compiled form rides
the arena image (§3.2); apps re-assert at startup. This closes the former
open question about claim persistence at boot: loaded records carry their last
compiled claims until re-assertion refreshes them, and a consumer removed from
the build leaves claims that lapse by decay.

**Runtime keys (not persisted):**

| key space | mechanism |
|---|---|
| `rnsd.dir.<hex32>.{pubkey,name_hash,hops,last_heard,claims,route}` | provider (§7); read-only, excluded from browser dump, paged on demand |
| `rnsd.stats.dir.{guard_drops,evictions,recall_miss,seq_retries}` | ordinary storage keys, published from rnsd's existing 1 Hz stats block, gated by `uiTelemetryWanted` like the rest |

**Filesystem (under `fsStateDir()`):**

| path | content |
|---|---|
| `<state>/rnsd/dir.img` | the arena image (§3.1) |
| `<state>/rnsd/dir.img.new` | atomic-replace temporary, renamed over the image |

Existing artefacts are untouched: `storage/root.json` and
`storage/external/*` stay as they are; µR's own no-op'd `OS::read_file` /
`write_file` stay no-op'd — the image moves through the platform hooks and the
persist worker, never through µR's filesystem shim.

**RAM:** one `arena_alloc` (PSRAM) block at init, sized by the budget. An
optional DRAM `dest4 → slot` index for the per-packet `rdirPeekRoute` scan is
noted in §13 as a measured-only addition.

## 11. Staging

**0 — mitigations, independent, ship first.** `s.rnsd.identity.cache_max`
→ **250** (frees ~390 kB immediately, applied by the existing change hook);
`CONFIG_COMPILER_CXX_EXCEPTIONS_EMG_POOL_SIZE` → **2048**, so D4 stops turning
every allocation failure into an abort and the existing `bad_alloc` handlers
become reachable.

**1 — lazy unpack.** `Codec<DestinationEntry>::decode` stops calling
`announce_packet.unpack()`; the packet stays packed in the record and the two
consumers that need its contents unpack on demand. Kills the crash frame with
a change of a few lines and no layout or API change. (A fuller record
re-layout was considered here and dropped: stage 2 deletes this codec anyway.)

**2 — the arena.** Three pools inside µR; path table folded in
(`Transport::outbound` → `rdirPeekRoute`, `cull_path_table` → `rdirEvictTo`,
`_random_blobs` → guard pool); image load/snapshot; rns straddle wires budget,
debounce and persist-worker write. Replay behaviour unchanged (every interface
still retains, guard bypass wired per §5).

**3 — recall switch + deletions.** `Identity::recall` reads the directory;
`_known_destinations` and the stubbed save/load are deleted **in the same
stage** rlpg moves to `rdirSeedPubkey` and lxmf drops its capture/feed pair
(`lxmf.cpp:812`, `:828`) and the `pubkey` contact field — no window in which a
writer has no home. Fan-out gains the pubkey; provider and claim marshalling
land; `s.rnsd.identity.cache_max` is deleted.

**4 — per-ingress retention policy.** `retain_on_announce` flags become
operative. The only stage that changes observable network behaviour.

## 12. Validation

Counters, cheap and unconditional: guard suppressions (split: fingerprint /
emitted-ordering / bypass taken), evictions per pool per category, recall
misses, seqlock retries, snapshot count. Surfaced two ways: new rows in
`rnsd memory` (replacing the identity-cache row), and `rnsd.stats.dir.*` in
the existing 1 Hz publish block.

Acceptance: the measured failure was an abort at ~44 min on the T3-S3 against
a busy public TCP peer plus LoRa. Target: multi-day soak in the same
configuration, PSRAM steady-state below 70%, zero `rnsd ITS send dropped`
bursts attributable to allocation stalls, and LoRa neighbours resolvable from
the TCP side throughout (the custodian property, checked by periodic path
requests from the far side).

## 13. Deferred

Recorded, not scheduled. Nothing above depends on them.

- **`_announce_table` and `_held_announces`.** Still `std::map`s holding full
  `Packet` copies (~52 kB measured at cap). Deliberately out of scope: they are
  bounded, and the retransmission queue has real structure (timers, retries)
  that the arena's flat records don't model. Revisit only if they show up in
  `rnsd memory` after the arena lands.
- **Forwarding policy.** `Reticulum::transport_enabled()` is one global boolean,
  so a node transits everything between everything. Two flags per interface
  cannot express the constraint either: wanting TCP→LoRa forces
  `fwd_in(TCP)`, wanting LoRa→TCP2 forces `fwd_out(TCP2)`, and TCP→TCP2 is
  exactly their conjunction. The semantic is an ordered pair — of which the
  same-interface echo is the diagonal, and the point-to-point suppression already
  hand-built in `request_path` is its one implemented corner. Interface *zones*
  with a class × class table keep the configuration linear.
- **Egress cost as a policy input.** The interface mode taxonomy has no axis
  for it, and the tell is that ACCESS_POINT — the closest fit for a LoRa mesh —
  gives it `AP_PATH_TIME` (6 h) against the TCP side's `PATHFINDER_E` (1 day).
  What exists (`announce_cap`) is a throttle: it decides *when*, never
  *whether*.
- **The LoRa forwarding whitelist.** A natural default is the persisted claim
  set — your contacts and your community's nodes reach your mesh, nothing else.
  It should imply an `ANSWER_FOR` claim: having advertised a destination onto
  the mesh, being unable to answer for it means spending airtime on a request
  we induced. The claim bit is reserved now (§4).
- **Announce provenance.** No wire signal distinguishes a native three-hop LoRa
  announce from one injected by another gateway. The signature covers
  `dest_hash ‖ public_key ‖ name_hash ‖ random_hash ‖ app_data`, so the only
  relay-mutable fields are `hops` and the HEADER_2 transport id — nothing
  annotatable exists, and adding one is a wire change. Available instead: the
  whitelist (removes our own contribution entirely), and a learned classifier —
  a destination seen on both TCP and LoRa identifies the relaying transport id
  as a bridge.
- **Interface Access Code (IFAC) alongside SUPE.** Mutually exclusive as
  written, which removes the one mechanism that would prevent injection by
  others and puts the whole load on the whitelist.

## 14. Soft spots

- **The guard cap.** The guard population is sized by the announce traffic we
  are *exposed* to, not by what we care about. Evicting an entry means
  occasionally re-forwarding a stale announce. Cheap to be wrong about; the
  alternative — a forwarding decision that does not depend on per-destination
  history — is a forwarding-policy answer and sits in §13.
- **`rdirPeekRoute` is a linear PSRAM scan per outbound packet.** ~256 × 16 B
  compares; low single-digit CPU% at gateway packet rates. Accepted; if
  measurement disagrees, a DRAM `dest4 → slot` open-addressed index
  (single-writer, verify full hash in the record) makes it near-free.
- **Blob slot size.** 320 B refuses to retain announces with large application
  data. Believed harmless because the destinations we retain are on the
  expensive edge, where application data is small by necessity. Worth measuring
  against a real mesh before fixing the number.
- **Guard collisions merge two destinations.** Mitigated by the
  reset-on-consecutive-fingerprint-miss heuristic (§1.1); residual cost is a
  rare duplicate forward.
