# Move LXMF contacts + announces into storage_db record stores

## Status: implemented, builds clean (not yet flashed)
- Contacts: schema 2, durable `lxmf/contacts/$1.db.gz`, per-store migration
  (`storageDbMigrateStore` + `/storage/migrated-lxmf_contacts` marker).
- Announces: schema 3 (`last` u32, `hops` u8, `cost` fixstr(6), `name` text),
  RAM-only STORAGE_DB_DROP cap = s.lxmf.max_announces. Decision B (unpacked).
  Manual `annOldest*` eviction deleted; ~11 packed-format sites rewritten via
  `readAnnounce()` / `forEachAnnounce()`. LCD `annCb` now accumulates per record.
- LCD O(N²) folded in: `convCb`/`annCb` O(1) adjacency; `g_annIx` hash index for
  `peerName`/`lastAnnounce`.
- Watch on first flash: (a) existing contacts survive on an already-migrated
  device; (b) the zero-wildcard `lxmf.announces` global store routes at runtime;
  (c) web lxmf module reads contacts/announces (should work like msgs via the
  storage.db registry — spot-check). Announce cap is now fixed at boot (was a
  per-insert dynamic read).


## Motivation
Both collections currently live in the cJSON config DOM and are walked
synchronously on the lcd task during `rebuildList()` (the deferred "refresh one
beat later"), which blocks input + the search cursor for up to seconds. They are
exactly the "large homogeneous collections" `storage_db.h` was built for
(messages already moved). Moving them:
- takes them out of the config save/deflate hot path (root.json no longer
  serializes contacts, and no longer churns with ephemeral announces — a source
  of the storage-actor `applyPoll` stalls),
- gives announces automatic capped eviction (replaces the manual `annOldest`
  scan/evict code), and
- packs them into a PSRAM arena instead of node-per-field cJSON.

Note: the LCD-side O(N²) hot spots (`convCb` linear find-per-leaf; `peerName`
scanning all `g_anns` per row; building both tabs) are **complementary** fixes —
the DB move alone does not remove them, since `storageForEach` still yields every
record. Do those alongside.

## Contacts — durable store
- keyPattern: `s.lxmf.id.$.contacts`  ($1 = identity slot; record = peer hash;
  field = leaf). Matches the existing `s.lxmf.id.N.contacts.<peer>.<field>` keys,
  so `contactPath()` writers and `convCb`/`peerName` readers are unchanged.
- persist: `lxmf/contacts/$1.db.gz` (one file per identity, all peers as records)
- evict: STORAGE_DB_RELOAD
- schema (id 2, ver 1) — fields observed under `contacts.<peer>.<field>`:
  - u32 `count`
  - u32 `last_ts`
  - u8  `unread`
  - u32 `read_ts`
  - text `preview`
  - text `display_name`
  (verify no other leaves before finalizing; `read_ts` note at contactPath.)

### Migration hazard (must solve)
`storageDbMigrate()` is **global-once**, guarded by `/storage/external.old`. On
any device that already migrated messages, that marker exists, so a newly
registered contacts store gets **no** migration — existing contacts stay in
cfgRoot but reads route to the empty DB → contacts appear lost.
Fix: a per-store migration with its own marker
(`/storage/migrated-contacts`), run once at lxmf init, that packs the existing
`s.lxmf.id.*.contacts.*` subtree into the store and detaches it from cfgRoot.
Reuse `migrateWalk` mechanics (may need a small public entry that migrates a
single registered store regardless of the global marker).

## Announces — ephemeral capped store
- Current format: a **packed single leaf** `lxmf.announces.<hex>` =
  `"<last_s>|<cost>|<hops>|<ratchet>|<name>"` (no `s.` prefix → already
  ephemeral in cfg). One segment after the prefix, so it does NOT fit the
  `<pattern>.<record>.<field>` routing as-is.
- evict: STORAGE_DB_DROP, cap = `s.lxmf.max_announces` (default 2048), persist
  = null (RAM-only). No migration (ephemeral; refills from the live stream).

### Decision: packed vs unpacked
- (A) Minimal — keep packed as one text field: key `lxmf.announces.<hex>.v`,
  schema = single `text v`. ~3 write/read sites gain a `.v`; LCD `annCb` parses
  the same pipe string. Least code; every re-hear rebuilds the record (fine,
  ephemeral).
- (B) Idiomatic — unpack into fields: u32 `last`, u32 `cost`, u8 `hops`,
  fixstr `ratchet`(32), text `name`. Mutable scalars update in place per the
  schema's design rule; only a name change rebuilds. Touches the announce
  parse/serialize (~lxmf.cpp 1177–1325) and the LCD `annCb`. Cleaner, more code.
Recommendation: (B) — the mutable-scalar-in-place model is the whole point of the
engine, and it drops the packed-string parsing on both sides.

## Browser
`storageStructuredDB` publishes the schema to the `storage.db` registry and
synthesizes the same change notifications; browser reads route transparently.
Verify the web lxmf module doesn't assume cJSON-only shapes for contacts/
announces before shipping.

## Order of work
1. Contacts store + per-store migration + guard. Verify existing contacts
   survive a build/flash on an already-migrated device.
2. LCD-side O(N²) fixes (convCb O(1) adjacency; g_anns hash index; visible-tab
   only) — independent, immediate stall relief.
3. Announces store (decision B), delete the manual eviction code.
