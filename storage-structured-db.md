# Storage: structured collections out of cJSON

Design for moving every large, homogeneous collection — LXMF message history,
the lxmf and rnsd announce catalogues, nomad page bodies — out of the resident
cJSON tree into packed, fixed-schema record stores with an index. cJSON goes
back to being what it was built for: the small, heterogeneous config/state tree.

**Persistence is one axis, not the point.** Some collections are durable and
paged to a file (messages); others are ephemeral and never touch disk
(announces, nomad pages), rebuilt from the network or simply capped. What they
share is the *representation* (packed records in a contiguous arena, not a graph
of tiny PSRAM-allocated cJSON nodes) and the *integration* (synthesized change
notifications + browser projection). An ephemeral capped collection is just a
durable one with the file and the reload turned off.

Grounded in the 2026-07-14 diagnosis below and in
[storage-needs-work.md](storage-needs-work.md) (P1 one resident tree, P2 one big
lock, P4 whole-file persistence, N1 split hot config from bulk data).

## The measured problem (2026-07-14)

Non-responsive LCD and a browser DataChannel that flaps on every LCD
interaction. An actor-loop probe added to `storage.cpp` (the `actor stall …`
warn — keep it, it validates the fix) pinned it:

```
actor stall 2396ms: applyPoll=2396(ops=11) cfgPoll=0 dump=0 patch=0
```

- The 2+ s is entirely in the op-apply drain — **not** the browser dump
  (`dup full dump` holds are 165–190 ms), **not** saves (zero `dup external`
  holds), **not** patch serialize.
- It's ~250 ms **per write**, not a flood: 7–11 ordinary writes taking a quarter
  second each.

Why each write is that slow: every message key
`s.lxmf.id.<n>.msgs.<peer>.<mid>.<field>` resolves through `navigatePath`
(`storage.cpp:141`), which at the `<peer>` node does `cJSON_GetObjectItem` — a
**linear scan of that conversation's entire message list** (cJSON children are a
linked list, no index). The commit does ~10 such scans per message write
(dedup read + deepMerge + browser echo), on PSRAM (cache-miss pointer-chasing;
flash program/erase windows disable the PSRAM cache mid-walk). Nothing is ever
evicted, so the list only grows. This is P1/P2 exactly, now measured.

The acute symptom was `markRead` (`lxmf_lcd.cpp`): opening a conversation wrote
`read=1` per unread message — each write one of those ~250 ms O(N) commits, each
notifying the LCD's `s.lxmf.id` subscription. That storm broke liveness. The
read-watermark (Sequencing §1) fixed it, independent of the store.

## What comparable projects do (2026-07-14 research)

- **Meshtastic** (closest analog, ESP32 LoRa messaging): does not durably store
  full history — keeps a bounded in-RAM cache; Store & Forward needs PSRAM to
  hold more *in RAM*. The nearest cousin bounds residency rather than persisting
  a resident database.
- **Reticulum/LXMF clients** (Sideband, MeshChat): use **SQLite** for the
  message DB. Messages on the wire are self-contained packed blobs.
- **Bitcask** (the canonical embedded pattern): append-only log + in-RAM index
  of key→location. O(1) read, tombstone deletes, periodic compaction, hint files
  for fast index rebuild. Pitfalls: all keys in RAM, no range queries, garbage
  accumulates until compacted.
- **Substrate**: Espressif's own docs — FAT is the weakest for power loss;
  LittleFS and NVS have real power-down protection. Argues for LittleFS under a
  new store rather than FAT/SD-FAT.
- **SQLite on ESP32**: real maintained ports exist and are PSRAM-aware, but it's
  reported slow on FatFS/LittleFS, carries large code size, and wants a RAM page
  cache. Credible fallback if we decide "don't hand-roll a store," but heavy for
  an append-mostly, lookup-by-id, per-conversation workload.

## North star: cJSON holds config, nothing bulk

The failure mode isn't messages specifically — it's using a JSON DOM as the
database for everything. So the fix isn't to optimize the DOM. Bracketing
writes, coalescing notifications, a side index, the read-watermark — all real,
all done or planned, none structural. They keep the device usable and they
**must not be mistaken for the fix**, because each relieves just enough pressure
to make the real change feel optional. It isn't. The target is: **cJSON contains
only the bounded config/state tree; every large homogeneous collection lives in
a structured store outside it, and none of it is a resident cJSON subtree.**

The collections to move, and their knobs:

| Collection | Key today | Persist | Eviction / bound |
|---|---|---|---|
| LXMF messages | `s.lxmf.id.<n>.msgs.<peer>.*` | file, one per conversation | evict idle, reload on access |
| LXMF announces | `lxmf.announces.<hash>` | none (rebuilt from mesh) | hard cap (`max_announces`), LRU-drop |
| rnsd saved announces | rnsd's announce store | per its owner | cap |
| Nomad page bodies | nomad page cache | none (cache) | cap / TTL |

Two knobs cover all of them: **persistence** (a file behind it, or not) and
**eviction** (reload-from-file, drop-and-rebuild-from-source, or a hard cap).
The message store exercises the durable+paged corner; announces exercise the
ephemeral+capped corner; both use the same records, index, and integration.

## The design

A registration API alongside `storageNewTreeFile`:

```c
storageStructuredDB("s.lxmf.id.$1.msgs.$2",   // key pattern (wildcards bind)
                    "<schema>",                 // fixed record layout
                    { persist: "lxmf/msgs/$1/$2.db.gz",  // omit → in-RAM only
                      evict:   RELOAD });                // RELOAD | DROP | cap N
```

Announces register the same way with `persist` omitted and a hard cap
(`{ cap: 2048, evict: DROP }`) — an ephemeral collection is the same store minus
the file and the reload.

- **One store per bound collection.** Wildcards (`$1` = identity index, `$2` =
  peer) bind per instance. The key pattern's two path segments *after* the
  matched prefix are the record key and the field name. Storage stays otherwise
  generic; only the routing layer learns these patterns. Durable collections get
  one file per instance; ephemeral ones live only in the arena.
- **On disk**: a gzipped block of packed fixed-schema records, one file per
  instance. **In RAM when resident**: the same block, uncompressed, in one PSRAM
  allocation with slack for appends; grows by copy-to-a-bigger-block.
- **Load on demand**: first access decompresses the file into RAM.
- **Evict when idle**: write-if-dirty, then free. Bounded residency — never the
  whole history at once.

### The load-bearing rule

**Fields that change must be fixed-width, so they are overwritten in place.
Variable-length fields (text) are immutable once written.** In-place mutation is
what removes reindexing, pointer churn, and reallocation from the common path.
Consequences for the LXMF message schema:

- Mutable → fixed-width: `stage` (enum), `method` (enum), `attempts` (u8),
  `last_error` (enum — see Open decisions), `deleted` (hidden tombstone bit).
- Immutable → variable-length: `title`, `content`, `thread`.
- **Not in the record at all.** Addressing is not data: `peer` is the filename,
  `<mid>` is the record key — neither goes in the body. `wire` (the packed
  message) is transient scratch, dead after delivery — RAM-only outbox, never the
  file. `message_id` is redundant for inbound (the record key already is the
  hash) — store only for outbound. **Read state is not per-message** — it is the
  per-conversation watermark in the directory (below), never a `read` field.

Record layout: a fixed header (all fixed-width fields, at known offsets) + the
variable text fields appended after it (length-prefixed). Mutating a fixed field
is a write at a known offset from the record start; the text offsets never move
because text never changes.

### Persistence and delete

**Rewrite-whole-on-flush, mutate in place in RAM.** A dirty instance's whole
(small, per-conversation) gzip file is rewritten on the debounced flush; in RAM,
fixed-width fields are overwritten in place. The debounce bounds the cost — a
busy conversation rewrites at most once per flush window, not per change — and
the file stays a clean snapshot, which is what lets it be shipped raw to the
browser (see browser sync).

*Append-only per instance was considered and rejected:* it can't overwrite in
place, so every `stage` transition becomes an appended delta (turning the clean
snapshot into a delta log the browser would have to replay), delete stops being
free (tombstone + separate compaction), and the cheap-write it buys is a cost the
flush debounce already absorbs.

**Delete is free on write-back.** Set the hidden tombstone bit on the record
(RAM-only — deleted records simply never get written, so the bit need not
persist); the next flush drops them, so the flush *is* the compaction, no
separate GC pass. Two non-free parts: the delete must still fire the synthesized
change events (record-null + directory update), and it isn't durable until the
flush (same debounce-window loss we already accept). A **bulk/predicate delete**
("these records", "where ts < cutoff") is a required primitive: one marking
pass, one flush, **coalesced** notifications — never N single deletes (that is
the markRead storm again). Retention, disappearing-messages, and clear-chat are
callers of this primitive, deciding what/when; storage stays generic and knows
none of them.

### Per-instance index

Build a small `record-key → offset` table when an instance loads. Bounded (one
conversation), cheap, and makes lookup and in-place mutation O(1) instead of a
scan. This is the index that replaces cJSON's linear child lookup. It is stable
under append / tombstone / in-place mutation; a compacting flush (drop deleted)
shifts offsets, so **rebuild it after compaction** (and on load).

### The conversation directory (summary; stays in cJSON)

Once message bodies leave cJSON, nothing can cheaply answer "what conversations
exist, newest first, with unread badges and a last-message preview" without
loading every conversation — exactly what we're removing. So that summary must be
**maintained, not derived**: a small per-conversation record — `last_ts`,
`preview`, `count`, `unread`, peer name — kept in cJSON (it already is, as
`s.lxmf.id.<n>.contacts.<peer>.*`), updated by LXMF as it writes:

- inbound/outbound message → bump `last_ts`, `preview`, `count`, and (inbound)
  `unread`;
- mark-read → `unread = 0`, advance the read watermark (`read_ts`).

This is an **LXMF-side** concern, not a storage primitive — storage owns the
bodies, LXMF owns the summary. It is what makes both the browser connect and the
LCD conversation list O(conversation-count), never O(messages): both read the
directory, neither walks the store. Note the evolution: the landed read-watermark
(§1) currently *derives* unread by walking messages in cJSON; when bodies move to
the store, unread becomes this maintained `unread` counter.

### The registry (self-hosted; carries the schemas to the browser)

The table of structured-DB declarations lives in the config tree itself, under
the **ephemeral** prefix `storage.db` (bare, no `s.` — in-RAM, browser-synced,
not persisted). Each owner (LXMF, rnsd, nomad) declares its collections by
registering an entry: key-pattern, file-pattern, schema. Why this beats a private
C++ table:

- **The browser needs the schemas anyway** to decode the raw `.db.gz` files it is
  shipped (see browser sync). With the registry in the tree, the schemas travel
  to the browser as ordinary config, in the dump it already receives — one
  mechanism, two needs.
- **Inspectable** — `show storage.db` lists the live stores.

Rules:

- **Ephemeral, rebuilt from code each boot.** The running firmware is the source
  of truth for schemas; persisting the registry would let a stale declaration
  fight a firmware whose layout changed. Bare `storage.db` gives inspectability
  and browser transport without durability. (The *files* carry their own version
  header for migrating old data; the registry always reflects current code.)
- **Declaration in the tree, index in owned memory.** Do **not** cache a raw
  `cfgRoot` pointer into `storage.db` for routing — the re-home duplicate at
  storage-task start (and any tree realloc) invalidates it, the exact
  use-after-free class we are leaving behind. Compile the array once into an owned
  side table (matched patterns + precomputed schema offsets), rebuilt when the
  array changes. Routing a write is then an O(1) pattern match, not a cJSON walk.
- **Firmware-writable only.** Gate `storage.db` like `fw.*` — a browser patch
  must not be able to register a store or make storage create files.
- **No bootstrap cycle.** The registry is plain cJSON; it is never itself stored
  in a structured DB.

### Schema versioning (design in now, not later)

A binary fixed-schema file breaks the moment a field is added. Each file carries
a small header: magic + schema-version + field layout. The loader reads an older
file best-effort and rewrites it in the current layout on next flush; the browser
keeps decoders for supported versions. This trades away cJSON's "add a key and it
appears" flexibility for speed — acceptable only with the header from day one.

## The three integration seams (the real work)

The store is easy; keeping it invisible to everything that reads cfgRoot today is
the work.

1. **Change notifications — the crux.** Every in-place mutation must fan out the
   *same* dot-path change events a cfgRoot write does
   (`s.lxmf.id.1.msgs.<peer>.<mid>.stage = "sent"`), or the browser mirror and
   the LCD's `s.lxmf.id` subscription silently stop updating. So a structured-DB
   write feeds the existing notify bus with synthesized keys. Payoff: **LXMF's
   code barely changes** — it keeps calling `storageSet`/`storageGetStr` with the
   same key strings; storage transparently routes message keys to the DB. That
   transparency is the highest-risk, highest-value piece — make the emit
   structurally inseparable from the write (see Footguns).

2. **Loading from flash off the actor.** Decompress is slow and must not run on
   the storage task's poll loop (the rule that created the save worker). Writes
   are fine — the caller already blocks until the write is applied, so the load
   time is absorbed into a wait that already happens. Reads are the trap: readers
   don't block today, they take the lock and go. Rule: **only a UI-initiated read
   may trigger a load** (opening a conversation on screen can tolerate a short
   pause); keep active conversations pinned; evict only idle ones; and hold it as
   an invariant that no network hot path ever reads an evicted conversation (in
   practice lxmf only reads conversations it's actively messaging, which stay
   pinned — verify, don't assume).

3. **Browser sync — directory always, bodies on demand.** Messages no longer
   live in the tree, so the connect dump can't just serialize them. Three layers:
   - **Always (cheap, JSON):** the conversation *directory* (above) rides the
     normal config dump — chat list, unread badges, previews, sized by
     conversation-count, not messages. Connect is O(conversations).
   - **On demand (raw files):** when a client opens a conversation, ship its
     bytes — the `.db.gz` straight off flash for a cold (evicted, clean)
     conversation (the device does *no* decompress/parse/serialize), or the
     serialized in-RAM block for a resident one. **Never ship the on-disk file
     for a dirty resident conversation** — it is a stale pre-flush snapshot; ship
     the block. The browser decodes with the schema it got from the registry.
   - **Live changes (JSON patches):** synthesized keys as in seam 1, routed by
     scope — directory changes to every client, body changes only to clients with
     that conversation open. A new message can be one "append this record" event
     rather than N field patches.

   This makes the reconnect-after-a-stall cheap — the actual flap bug — and it is
   the only shape that also works when the browser is *remote* (over the mesh,
   where shipping all history on every connect is impossible regardless of device
   speed). The cost: the record format becomes a client-facing format (hence the
   version header, and the device rewriting old files to current on flush so old
   versions age out). The browser's reactive model also splits: config +
   directory stay in the JSON-mirrored tree it has today; message bodies become a
   separate binary-fed store.

## Migration (one-way, with a backup)

`storageNewTreeFile` has exactly one caller in the whole tree — LXMF's
`ensureConvFile` — and the only factory-seeded external is the legacy
`s.lxmf.json` monolith. So `<stateDir>/storage/external/` holds **nothing but
LXMF message data** (per-conversation `s.lxmf.id.<n>.msgs.<peer>.json[.gz]`
files, plus possibly the un-split monolith). That is what makes the
whole-directory backup below safe — verified, not assumed.

A one-way batch conversion, run once on the first boot of the new firmware,
after the `storage.db` registry is populated (so the message schema is known):

1. **Read message data straight from the assembled `cfgRoot`.** `storageLoad`
   has already parsed every external (and the monolith, if present) into the
   tree, so this is agnostic to how the data was stored — it **subsumes**
   `migrateLxmfMonolith`, which can be retired.
2. **Convert each conversation** `s.lxmf.id.<n>.msgs.<peer>`: pack it into the
   new record file at its own path (`<stateDir>/lxmf/msgs/<n>/<peer>.db.gz` —
   *not* under `storage/external/`), seed the directory summary in
   `contacts.<peer>` (`last_ts`, `preview`, `count`; `read_ts`/`unread` already
   migrated), then **detach the `msgs.<peer>` subtree from `cfgRoot`** so it
   can't fall back into `root.json` or re-mirror to the browser.
3. **Last step — completion marker and recovery net:** rename
   `<stateDir>/storage/external/` → `storage/external.old/`. One rename, not a
   per-file delete. **Keep it** (don't delete) so a bad conversion is
   recoverable; don't overwrite an `external.old` that already exists.

Crash model: `external/` is the source of truth until that final rename, and the
new files live in a separate directory, so a crash mid-run leaves the old data
intact and simply re-runs the conversion next boot (harmlessly overwriting any
partial new files). The done-marker is just that `external/` is gone. No
keep-both-in-sync, no incremental bookkeeping — convert, back up, move on.

## Sequencing

1. **Read-watermark — DONE (2026-07-14).** Replaced the per-message `read` field
   with a per-conversation `read_ts` watermark under
   `s.lxmf.id.<n>.contacts.<peer>.read_ts`, across firmware (CLI + LCD) and the
   browser (`lxmf/browser/src/modules/lxmf.ts`); `markRead` is now one write. This
   killed the liveness-breaking burst and is the target model (a per-conversation
   scalar). It currently *derives* unread by walking messages; once bodies move to
   the store that becomes the maintained directory `unread` counter.
2. **`storageStructuredDB` + LXMF messages.** The store; the schema + version
   header; the self-hosted `storage.db` registry + compiled routing table; the
   notify-synthesis seam; paging/eviction with the pin/evict rules; the
   conversation directory (LXMF-side, maintained); the layered browser sync; the
   JSON→records migration.
3. **The ephemeral collections (not optional — half the point).** Move
   `lxmf.announces.*`, rnsd's announce store, and nomad page bodies onto the same
   mechanism with `persist` off: same records, index, and browser projection, no
   file and no paging — just a cap. This is where cJSON stops holding anything
   bulk. The dump cost and RAM growth are driven as much by the announce firehose
   as by messages, so skipping this leaves the tree bloated and the connect-dump
   slow even after messages move.

## Footguns

- **Notify-synthesis must be impossible to skip.** A forgotten change event is a
  silently-stale browser/LCD — no crash, invisible in a quick test, permanent
  until reload. Make the *only* way to mutate a field an API call that also emits
  the event; no raw pokes into the memory block anywhere.
- **Evict/read race and the moving block.** Eviction frees the block; a reader
  mid-walk dies. The block also relocates when it grows past its slack — any
  reader holding a pointer across a lock release is reading freed memory (cJSON's
  current crash class in new clothes). Keep a conversation pinned while a
  reader/iterator is active; readers never hold a raw pointer across the lock —
  re-fetch the base or copy out.
- **Rebuild the index after compaction** (see Per-instance index) — else lookups
  land on the wrong record.
- **Watermark poisoned by a bad clock.** A future-dated inbound message pushes the
  read watermark ahead and hides later legit-unread messages. Clamp a received ts
  to ~now. (ts=0 pre-clock messages never show unread — minor.)
- **A dot in a field name or record key breaks routing** (the tail is split into
  `<record>.<field>`). Enforce no dots in schema field names and record keys.
- **Bulk-delete notification storm.** Expiring 500 messages must not send 500
  patches to an open client — one "re-fetch this conversation" nudge for open
  ones, a directory patch for the rest.

## Speedups

- **Cold-ship off flash** — the biggest one: a cold conversation costs the device
  a file read, no decode; the browser (a real computer) does the decompress.
- **The directory does triple duty** — browser connect payload, LCD contact list,
  and unread source. The LCD list stops walking every message; it reads the
  directory (this also removes the remaining O(all-messages) LCD refresh cost).
- **A field change is one byte at a known offset + one event** — versus ~250 ms
  today.
- **Record-granular updates** for new messages ("append this record") instead of
  N field patches — matches the model, halves the update chatter.

## Open decisions

- **Substrate.** LittleFS (crash-safe) vs staying on FAT/SD — decide before the
  file format freezes. Torn-write detection (length + CRC per record, truncate a
  partial tail on load) is cheap insurance regardless.
- **`last_error` enum** loses the free-text detail the UI shows today (retry
  reason, route errors). Accept the coarsening, or keep one small free-text slot
  for the current error only.
- **Drafts stay out of the store** — text mutates as you type; keep them
  transient (root.json / ephemeral) until sent.
