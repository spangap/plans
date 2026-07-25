# storage_db: generic schema auto-migration

## Status — implemented 2026-07-20

Landed in `spangap-core/esp-idf/{include,src}/storage_db.*` and
`lxmf/esp-idf/src/lxmf.cpp`; builds clean for hw-lilygo-tdeck. What shipped:

- **Format v2 descriptor.** `SDB_FORMAT_VER = 2`; the disk image is
  `gz([16-byte header][u16 desc_len][descriptor][records])`, descriptor written
  from the live schema on every flush (`buildDiskImage`). The resident block is
  unchanged (`[header][records]`, records at offset 16) — the descriptor is a
  disk-only frame, spliced in by `buildDiskImage` / stripped by `sdbLoad`, so no
  record-walking loop had to learn a new record start.
- **Auto-migrator in `sdbLoad`.** Descriptor equal → adopt; differ → decode with
  the file's schema and re-pack under the current one via the string round-trip
  (`sdbMigrateRecords`). v1 files resolve their layout by hdr match (current) or
  the legacy hint table.
- **Legacy hint table** (`sdbRegisterLegacyLayout`) for descriptor-less v1 files.
- **Bounded peek**: `gzInflatePrefix` inflates only the leading window;
  `sdbPeekHeader` and `sdbFileMatchesSchema` use it, so the per-boot sweep is a
  cheap descriptor compare in steady state.
- **lxmf**: registers the v3-message and v1-contact hint layouts; contacts and
  the v3/current-frame message files upgrade through `sdbUpgradeFileIfStale`
  (generic); only the semantic v2-message hop (`migrateMsgFile`) stays bespoke.
  All per-layout marker files are gone.

Two deviations from the design below:

- **Sweep placement.** The eager sweep is not run from `storageStructuredDB`
  registration; it runs from lxmf's existing per-identity file loops
  (`lxmfMigrateContacts`, `lxmfMigrateMsgs`) calling `sdbUpgradeFileIfStale`,
  reusing the directory enumeration that has to exist anyway and keeping
  storage_db free of any path-glob logic.
- **Browser untouched.** The raw-`.db.gz` binary decoder (cold-ship, seam 3 of
  storage-structured-db.md) is not built yet, so there is no v2-framing consumer
  to update. When cold-ship lands it must skip `desc_len` to find records.



Goal: adding or deleting a field in an `sdb_schema` must need **no bespoke
migration code** — edit the builder chain, done. Bespoke migrations remain only
for *semantic* conversions, and shrink to converting exactly the field(s) whose
meaning changed; a generic migrator does all structural work (add / delete /
kind-or-width change) automatically.

Grounded in what the three migrations so far actually cost
(`lxmf.cpp` `migrateMsgFile`, `migrateMsgFileV3toV4`, `lxmfMigrateMsgs`): a
retained full copy of each old schema (`lxmfMsgSchemaV2`, `lxmfMsgSchemaV3`),
dispatch on peeked `hdr_size` magic numbers (88, 40), a hand-maintained list of
every field to copy, and a marker file per layout. Under this plan, v3→v4
(drop `thread`, add `reply_to`, `message_id` hex-text → DATA) would have needed
**zero** code, and v2→v3 would reduce to just the `stage`/`last_error` →
`status`/`tries` computation.

## Can we tell the two schemas apart today?

- **Desired schema (firmware): yes.** The registered `sdb_schema` carries the
  full field table — name, kind, offset, width per field. It is the source of
  truth and is already published to the browser via the `storage.db` registry.
- **Current schema (file): no.** The 16-byte SGDB file header carries only
  `schema_id`, `schema_ver`, `hdr_size` — not the field layout. The loader
  (`storage_db.cpp` `sdbLoad`) validates only id + `hdr_size` and otherwise
  *assumes* the registered layout. That gap is why every migration so far had
  to keep a full copy of the old schema in firmware.

Conclusion: **make the file self-describing.** Embed the field table in the
file, then schema comparison is a byte-diff of two descriptors and no old
schema ever needs retaining again.

## File format v2: embedded schema descriptor

Bump the header's `format_ver` (u16 at offset 4) to 2. After the fixed 16-byte
header, before the records:

```
u16 desc_len                          — total descriptor bytes (records start at 16+desc_len)
per field: u8 kind, u16 off, u16 width, u8 name_len, name bytes
```

~12 B per field, so ~120 B pre-gzip for a 10-field schema — noise against a
conversation file. `SDB_TEXT` fields keep their order index in `off`, so text
ordering survives the round trip.

- `writeFileHeader` always serializes the descriptor **from the live
  `s->schema`** — never copied from the loaded file — so every flush rewrites
  the file as current-format, current-schema (files self-heal; old formats age
  out on first write, as storage-structured-db.md already promises).
- `sdbPeekHeader` learns to return the descriptor, and gets a **bounded
  inflate**: today it inflates the entire file to read 16 bytes; a streaming
  inflate of the first ~512 B window covers header + descriptor and makes a
  per-boot sweep affordable.

## The auto-migrator

Lives inside `sdbLoad` (storage_db.cpp — stays generic, no consumer names in
code or comments). After inflate + magic + `schema_id` check:

1. **Descriptor identical** to the registered schema → load as today. This is
   every ordinary boot; cost is one memcmp.
2. **Descriptor differs** → build a temporary `sdb_schema` from the descriptor,
   convert record-by-record (below) into a fresh block under the registered
   schema, adopt it, set `dirty` — the next flush persists the new layout via
   the existing `.new` + rename path, so a crash mid-anything leaves the old
   file intact.
3. **Format v1 file** (no descriptor): resolve the layout through a small
   hardcoded **hint table** — `(schema_id, hdr_size)` → retained schema — then
   proceed exactly as case 2. Only three v1 layouts exist in the field: msg
   hdr 88 (v2), msg hdr 40 (v3), contact hdr 29 (v1); the current layouts
   match by (id, ver, hdr_size) directly. The hint table is registered by the
   owner alongside the store (storage_db stays consumer-agnostic) and is
   deletable once v1 files have aged out. An unknown v1 layout keeps today's
   behavior: warn, start empty.

Conversion rules — diff **by field name**:

- **In file, not in target** → dropped.
- **In target, not in file** → default: 0 / "" / all-zero DATA / empty text
  (`sdbSetField`'s record creation already defaults everything).
- **In both** → convert through the string round-trip the store API already
  defines: read with the old descriptor via the `sdbGetField` renderer, write
  with the new schema via the `sdbSetField` parser. That one rule yields all
  the easy conversions for free:
  - u8 ↔ u32: decimal, clamp on narrowing;
  - fixstr ↔ text: copy, fixstr shrink truncates;
  - DATA ↔ text: hex round trip (exactly how v3→v4 converted `message_id`);
  - text/fixstr → numeric: best-effort atoi.
  - **Universal fallback:** anything the target parser rejects (e.g. a DATA
    width change → wrong-length hex) leaves the field at its default. A
    conversion can lose a field's value; it can never fail the load.
- Preserve arena (arrival) order — record order is meaningful (recv_ts
  monotonicity, cap eviction).

Mechanics: the `MigRec` walk pattern from lxmf.cpp, generalized into
storage_db.cpp — walk the old block with the file descriptor, `sdbSetField`
into a scratch store with the registered schema, swap blocks. All in RAM;
per-instance files are small by design.

`schema_ver` stops gating anything — equality of descriptors is the test, so a
field add/delete needs **no version bump**. Keep `schema_ver` as provenance
for logs (and bump it when a bespoke migration needs a trigger).

## Bespoke migrations: the reduced contract

Audit of the existing migrations against the auto rules + hint table:
**contacts v1→v2 and msgs v3→v4 are fully automatic** (name-copy, hex-text ↔
DATA, drop `thread`, default `pubkey`/`reply_to`). Exactly one bespoke
migration survives — msgs v2 — and only for three duties the generic rules
cannot express:

1. the `stage`/`last_error`/`attempts` → `status`/`tries` decision table
   (many-to-many rename with semantics: `migStatusFromText`, the stage
   switch, the `tries == 255` terminal marker);
2. synthesizing `recv_ts` as the running max of `ts` in arena order —
   a new field's *value* derived with cross-record state (auto would
   default it to 0 and break the stable sort key for migrated history);
3. carrying each conversation's max `recv_ts` into the contact directory's
   `last_ts` (the monotonic-clamp seed) — a cross-store side effect, and
   auto-migration is single-store by design. (The v3→v4 path also does this
   today, but there it is belt-and-braces — `recv_ts` already existed — and
   can be dropped.)

The directory backfill (`dir_seeded2`) and the cfgRoot-JSON → records
conversion are not schema migrations and stay as they are.

For such conversions with *meaning*, bespoke code remains, but the contract
shrinks to:

- **Runs before the store is registered** (hence before any load can
  auto-migrate). Ordering is load-bearing: if the auto-migrator ran first, it
  would delete or default the semantic source fields before the bespoke code
  saw them.
- **Reads the input using the file's embedded descriptor** — no retained old
  schema copies once v1 files have aged out.
- **Converts only the semantic field(s)** and writes the output under
  old-schema-with-those-fields-changed. Everything structural is left to the
  auto-migrator on the following load. No more hand-maintained
  copy-these-fields lists.

## Eager sweep at registration (why lazy-only is not enough)

Cold conversations ship raw off flash to the browser without ever being loaded
(the cold-ship speedup), so a lazy-only migrator would leave old-format files
that the browser can't decode with the registry's current schema. Keep an
eager sweep when a durable store's pattern is registered: peek each file's
descriptor (cheap with the bounded inflate); on mismatch, load + flush + evict,
`vTaskDelay(1)` between files like the current sweep. On ordinary boots the
sweep is peek-only. This replaces the per-layout marker files
(`migrated-lxmf-msgs-hdrNN`).

Browser: with the sweep, shipped files are always current-schema, so browser
changes are limited to the format-v2 framing (skip `desc_len` to find
records). Teaching the browser to prefer the embedded descriptor over the
registry is optional hardening for the mid-upgrade window.

RAM-only stores (announces, msg-meta) have no file and need none of this —
schema changes there stay free.

## Sequencing

1. **Format v2 in storage_db**: descriptor write/read, `sdbPeekHeader`
   descriptor + bounded inflate, loader accepts v1-current files. Browser
   decoder learns the v2 framing.
2. **Auto-migrator in `sdbLoad`** + the generalized record-copy. Verify with
   hand-built old-layout files covering: field add, delete, u8→u32, fixstr→
   text, DATA↔text, DATA width change (→ empties), order preservation.
3. **Eager sweep** at registration; retire the marker files.
4. **Retire the automatic ones**: delete `migrateContactFile`/
   `lxmfMigrateContacts` and `migrateMsgFileV3toV4` outright (covered by the
   hint table + auto-migrator); shrink `migrateMsgFile` to the three v2-only
   duties above. The retained schemas move into the hint table; delete the
   table once v1-format files are no longer in the field.

## Footguns

- **Descriptor written from the live schema on every flush**, never echoed
  from the loaded file — else a migrated store re-persists its old descriptor
  and re-migrates every load.
- **Rename is indistinguishable from delete+add** and silently loses the
  field's data. Renames are semantic by definition: do them bespoke, or don't
  rename. State this in storage_db.h next to the builder helpers.
- **Warn-log every dropped or defaulted field** during a conversion — a silent
  best-effort migration is undiagnosable after the fact.
- **`schema_id` mismatch stays fatal.** Auto-migration is same-store,
  different-layout only.
- **The sweep must not regress boot time.** The peek must be the bounded
  inflate; whole-file inflation per conversation per boot is the thing that
  forced marker files in the first place.
- **Bespoke-before-register must be structural**, not conventional — e.g. the
  registration helper asserts the store has no pending bespoke marker, or the
  owner's init order makes it impossible to invert.
