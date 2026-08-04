# Safe mode: state backup, restore, and factory reset

Three device operations that all need the same thing — a running system that is
*not* doing anything else to the state store — plus the boot shape that provides
it.

- **Backup** — stream out a `<fw>_<host>_<yyyymmddhhmmss>_<used>kB.tgz` of the
  entire active state store, generated on the fly.
- **Restore** — take such a file back, inflate and untar it straight onto a
  freshly formatted store, reboot.
- **Factory reset** — overwrite the whole state partition with random bytes and
  reboot, so the next boot builds a fresh store wherever *this* firmware places
  one.

None of the three can hold the archive anywhere: not in RAM, not on flash. Both
transfer directions therefore stream, and the design is shaped almost entirely
by that constraint plus its consequence — that a restore has a point of no
return in the middle of it.

## Why a mode and not a seal

The first cut of this design ran backup/restore against the live system and
tried to make that safe: a `storageFlushHold` parking the persist worker, an
`fsStateSeal` enforced inside the `fs` worker for every write op whose path fell
under `fsStateDir()`, an originating-task field on `fs_op_t` so the restorer
could exempt itself, and a marker file so a crash mid-write resolved to factory
rather than to a corrupt store.

All of that exists only to survive a hostile concurrent world — rns announcing,
lxmf bumping conversation directories, cron firing, acme renewing, the browser
DataChannel writing config, WebDAV `PUT` on the `/state` mapping. Boot into a
world that isn't hostile and the entire apparatus is unnecessary. **Safe mode is
strictly less code than making the live path safe, and it is code whose
correctness you can reason about by enumeration rather than by audit.**

It also unlocks the thing the live path could never do: with nothing holding a
file under the state store, restore can format first. That gives maximum free
space, true replace semantics, and a clean failure floor, all by construction.

---

## 1. Safe mode

### Entry: the flag names the operation

A storage flag, not a URL, not RTC RAM — and the *key* says which operation, so
there is no mode menu and no landing page. The device boots and does the thing.

```
set s.sys.backup=1          → stream the archive out
set s.sys.restore=1         → take an archive in
set s.sys.factory_reset=N   → 1 = flash, 2 = SD, 3 = both
reboot
```

Reachable from the CLI, from the browser (an ordinary storage write), from a
`/state/boot` script, and over rnsh. No new transport, no new credential.

`spangapInit()` reads the three keys immediately after `storageLoad()`, unsets
whichever is set, and flushes once — **before anything else happens**. The flush
cannot go through `storageSave()`/`requestSave()`: the persist worker does not
exist until `storageInit()` runs inside `serviceRunInit()`, so the one blocks
and the other no-ops. Call `writeSettingsFile()` synchronously instead — the fs
worker it needs is already up. A crash
anywhere inside safe mode therefore comes back into a normal boot. This one
flush is the only write safe mode makes to the store outside of the operation
the operator asked for. `spangapSafeMode()` returns the resulting enum
(`NONE`/`BACKUP`/`RESTORE`/`FACTORY_RESET`); if more than one key is set it
takes `factory_reset` > `restore` > `backup` and warns.

The `factory_reset` value comes from a dialog in the SPA (later work) asking
what to destroy when the device has an SD-backed store.

RTC-RAM entry is dropped. Its only advantage was surviving a `storageLoad()` too
broken to read a key, and that case wants factory reset, not safe mode — which a
board's `onStart()` can trigger from a GPIO before storage exists.

### What comes up

`serviceRegister()` gains the band the generator already knows. The generated
dispatcher emits registrations in `init_order()` — platform band (core, net,
web, lcd) first, then the dependency-topological straddle band — so the cut is
a matter of naming a point in an order that already exists. Safe mode's band
ends after web: `serviceRunInit()` stops there, leaving lcd and every straddle
down.

| Phase | Safe mode |
|---|---|
| `serviceRunStart()` | unchanged — board HAL, bare hardware |
| `spangapInit()` | unchanged — fs mounts, state-store choice, `storageLoad`, log, CLI, pm, auth — **except the eager `cronWakeupHandler()` call, skipped** |
| `spangapSettingsGenDefaults()` | **skipped** |
| `serviceRunInit()` | **platform band up to web** — storage, net, web. **cron skipped**: firing scheduled commands in a recovery mode is wrong. **lcd skipped**: see below. |
| `spangapSettingsGenRegister()` | **skipped** |
| `spangapPostAppInit()` | boot script and first cron poll **skipped**; `sys.boot_complete` still published |

No per-service virtual, no per-straddle opt-in, no default for a future straddle
to get wrong. One argument on `serviceRegister()` and one `if` in the walk.

Storage comes up fully — safe mode gets real WiFi credentials, hostname, TLS
certificate and admin hash with no special-casing. This is a normal boot that
stops early, not a stripped-down parallel bring-up.

The band ends at web: spangap-lcd stays down and a T-Deck goes dark in safe
mode. Bringing the screen up would add a hardware-verification dependency —
whether spangap-lcd's `onInit` tolerates the straddle band never running — for
a mode the operator drives from a browser anyway. Revisit if a dark screen
during recovery turns out to matter.

### What is suppressed

**Storage flushing.** Once a restore or a factory reset begins, storage must not
flush again, or the stale in-RAM tree lands on top of the restored files, or
into a partition being erased. One one-way boolean checked at the top of
`writeSettingsFile`:

```c
void storageStopFlushing();   /* one-way; cleared only by reboot */
```

Five lines. It needs no enforcement in the `fs` worker and no caller exemption
table, because in safe mode nothing else is writing to the store. That is the
whole argument for the mode. (Backup does not need it — it only reads.)

**Web's mapping table.** Skipping the `webMapAddIfAbsent` seeding buys nothing:
it early-returns when the mapping is already in storage, so it writes only on a
first boot — and the mappings persist in `s.web.map.N`, so seeding does not
control reachability. In safe mode web instead skips **loading** the mapping
table and short-circuits routing ahead of `findMapping`: every request resolves
to the safe-mode page or the one gated endpoint. That — not seeding — is what
makes `/state`, `/fixed` and `/sdcard` unreachable over HTTP and WebDAV during
the window, and it bounds the entire reachable surface to one page and one
endpoint.

### The page

One compiled-in HTML string per operation (~4 KB rodata total), with inline CSS
and JS. Served from the safe-mode routing short-circuit ahead of `findMapping`
(`serveRootIndex` is only the auth-denied fallback, not a catch-all), so `/` and
any stray path get it. **Not** a file in `/fixed` — a recovery mode that depends on the
webroot being intact has a hole in it, and a broken webroot is one of the states
you enter safe mode to repair.

Each page runs its operation on load; there is nothing to choose:

| Mode | Page |
|---|---|
| `BACKUP` | "Backing up", navigates to the download |
| `RESTORE` | "Restore system", file picker or drop zone → `fetch('/backup/' + name, {method:'POST', body: file})`. The only page that waits for the user: a file picker needs a gesture, so it cannot self-start the way the other two do. |
| `FACTORY_RESET` | "Erasing", client-side progress bar over a served estimate, ending in "done — the device is now on its own access point" |

The factory-reset page is pure client side: the served HTML carries one
printf'd value — the wipe-duration estimate (extent size × measured per-MB
cost, plus margin) — and the page animates a bar over it. No forward at the
end: the wipe took the WiFi credentials with it, so the rebooted device comes
up on its own access point and this browser's network is no longer where the
device is. The bar ends in a static "done — connect to the device's access
point to set it up again". No progress endpoint, no polling: the wipe makes
HTTP janky anyway (every erase stalls flash-resident tasks), and an estimate
with margin communicates the same thing at zero firmware cost.

### Endpoints, gated by mode

The endpoint for the other operation **does not exist** — `404` on a mode check.
So the window in which an unauthenticated device will hand out an archive
containing every secret exists only when the operator explicitly asked for a
backup.

### Exit

**Every** exit is a reboot, and it is decided server-side — last chunk drained,
or extraction verified, or wipe finished → `esp_restart()`. No client
acknowledgement and no polling handshake; the browser sees the connection go
away, which is what it would see anyway. The restart fires from a one-shot
timer armed as the handler returns, not inline: web drains and disconnects
*after* a handler returns, so an inline `esp_restart()` would cut its own
response off.

One fixed deadline, armed at boot: **10 minutes, then reboot**, regardless of
progress. A state store is a few hundred KB over WiFi; a transfer that has not
finished inside the deadline is dead, and progress-extension plumbing would
serve only already-dead transfers. Operation completed → drain, reboot, without
waiting for the deadline.

A power-management lock is held for the duration so nothing deep-sleeps
mid-window.

**Factory reset ignores all of this and starts at boot regardless of any
client.** Bring up the band, start wiping, serve the estimate page to whoever
turns up. It therefore works on a headless or LoRa-only node reached over rnsh, and it
still happens if net or web fail to come up at all.

### Auth

No tokens. The entry gate is the authentication: setting the flag requires an
authenticated storage write or physical console access, and a device you cannot
reach at all wants factory reset, not a login prompt.

Because storage is up, the existing cookie check works with zero new mechanism.
Safe mode enforces the `admin` realm when `authEnabled()` and falls open when it
is not — which is exactly the broken-or-fresh-store case that must stay open,
and is acceptable given the surface is one page and one mode-gated endpoint.

---

## 2. Backup

```
GET /backup/state.tgz                       (BACKUP mode only; 404 otherwise)
  → 200
    Content-Type: application/gzip
    Content-Disposition: attachment; filename="reticulous_tdeck1_20260804101500_412kB.tgz"
    Transfer-Encoding: chunked
    <chunk><chunk>…0\r\n\r\n
  → esp_restart()
```

### Why chunked

HTTP gives a receiver two ways to know where a body ends: a `Content-Length`
declared up front, or `Transfer-Encoding: chunked`, which frames the body as
`<hex length>\r\n<bytes>\r\n` runs terminated by a zero-length chunk
(`0\r\n\r\n`). A third, HTTP/1.0-era option is to declare neither and let the
connection close mark the end.

`Content-Length` is unavailable: the body is a gzip of a tar we generate while
walking the filesystem, so its length isn't known until it has been produced,
and producing it in advance means storing it — the one thing we cannot do.

That leaves chunked or connection-close, and connection-close is the trap.
Under it a truncated transfer — device reboot, WiFi drop, TCP reset — is
byte-for-byte indistinguishable from a complete one: the client sees the socket
close and assumes it has everything. The operator keeps a truncated `.tgz` and
finds out at restore time, which is the worst possible moment. Under chunked the
client never sees the terminating zero chunk, so curl exits non-zero and the
browser marks the download failed.

The server has no chunked-response path today — every existing response
declares a `Content-Length` — but adding one costs about ten lines and no
memory: a hex header before each buffer, a trailer after, a terminator at the
end.

This is a *transport* completeness check, and it stacks with the gzip footer's
CRC32/ISIZE, which is a *content* correctness check. They catch different
failures — a stream can arrive complete and corrupt, or intact and truncated —
and the restore path checks both.

No `Range`, no resume: the stream is generated, not stored.

Nothing is writing to the store in safe mode, so there is nothing to freeze.
This is a plain read.

### Flow

1. `webGetHeader` → method/path → mode gate → auth.
2. Walk `fsStateDir()` recursively with `fs_listdir` (one round-trip per
   directory; a PSRAM listing array per level, depth-capped at 8).
3. Per file: 512-byte ustar header, then `fs_open`/`fs_read` in 16 KB bites,
   zero-padded to a 512 boundary. Two zero blocks terminate.
4. Everything passes through `tdefl_compress` incrementally (`TDEFL_NO_FLUSH`,
   `TDEFL_FINISH` last) and out as HTTP chunks, with `itsWaitForSpace` for
   backpressure.

Paths are stored **relative to the state dir**, so a `/state` backup restores
onto an SD store and vice versa.

### Exclusions

`*.new` (in-flight atomic writes), `flashme.bin` (updater's staged firmware
image — megabytes, meaningless in a state backup). Logs and recordings live
under `/sdcard`, outside the store, and fall out naturally.

### The filename is the manifest

```
reticulous_tdeck1_20260804101500_412kB.tgz
└ fw stub   └ host  └ localtime      └ allocation needed
```

There is no `MANIFEST` tar entry, and only one field is ever machine-read: the
fw stub. The rest exist for the human reading a file listing.

| Field | Used for |
|---|---|
| fw stub | Cross-project refusal — **the only parsed field**. `s.sys.project` mismatch makes the next boot factory-reset the flash store (or reset-loop on SD), so a spangap archive restored onto a reticulous device destroys what it just restored. Mismatch → refuse; absent or unparseable → warn and proceed, since a stripped filename cannot be told from a foreign one. |
| host | Which device this came from. Informational. |
| `yyyymmddhhmmss` | Localtime at backup; the literal `nodate` when `sys.time.valid` is 0, rather than a fabricated 1970 stamp. Informational. |
| `<x>kB` | The source store's **used** bytes (`total − free`), rounded up to 1024-byte units — what the archive needs allocated at the far end (LittleFS allocates in blocks, so this predicts fit better than summed file sizes). Informational: there is no size precheck. |

Served as `Content-Disposition: attachment; filename="…"`, so a browser saves it
under that name by default (`curl -OJ` likewise; plain `curl -O` would keep the
request path and lose the metadata).

The upload body is raw, so it carries no filename of its own — a
`multipart/form-data` part would have had one in its own header, but that is the
parser we deleted. Instead the name **is the last path segment** of the upload
URL, which costs nothing at either end:

- the page does `fetch('/backup/' + encodeURIComponent(file.name), {method:'POST', body: file})`,
  taking the name from the `File` object the picker handed it;
- `curl -T <file> https://host/backup/` appends the local filename to a URL
  ending in `/` by itself.

Both verbs are accepted on the prefix, so nobody ever types the name in either
direction.

The safety comes from format-first and `.restore-active`, not from the
metadata: every restore failure, including an archive that turns out not to
fit, lands in the clean-factory recovery path. The fw-stub check earns its
parser because its failure mode is the one exception — a cross-project restore
*succeeds* and then self-destructs at the next boot — so it is the only check
there is.

---

## 3. Restore

```
POST /backup/reticulous_tdeck1_20260804101500_412kB.tgz     ← the page
PUT  /backup/reticulous_tdeck1_20260804101500_412kB.tgz     ← curl -T
    Content-Type: application/gzip          (RESTORE mode only; 404 otherwise)
  <raw streamed body>

  parse the fw stub from the trailing path segment — absent/unparseable warns and continues
    → fw stub mismatch                   → 409, nothing touched
    → gzip magic on the first bytes      → 415, nothing touched
  storageStopFlushing()
  format the store                       ← point of no return
  write .restore-active
  inflate + untar directly onto it
  verify gzip CRC32 / ISIZE
  remove .restore-active
  → 200 {"ok":true,"entries":37,"bytes":412996}
  → esp_restart()
```

**Raw body, no multipart.** Our page uploads with
`fetch(url, {method:'POST', body: fileObject})`, and `curl --data-binary @f.tgz`
already works that way. spangap-web has no multipart parser and this keeps it
that way: multipart exists only to serve a `<form>` submit we do not use, and
never writing the parser avoids a boundary-splitting bug class outright.

### Format, not erase

`fsFormatFlash()` gives an empty, mounted, writable LittleFS at the current
computed location in one call, and extraction starts immediately. A raw erase
would leave nothing mountable and force a reboot between wipe and write — which
is the staging problem again in a different costume. On an SD-backed store it is
a recursive clear of `/sdcard/state`, not a card format.

Both run on the DRAM-stack worker the existing `format` verbs use; the restore
task is PSRAM-stacked.

Format-first is what buys the space guarantee: you need room for the expanded
state, not for the archive *plus* the state. Given no room to stage, that is as
good as the space problem gets.

### Failure model

There is no rollback to the previous state. Without space to stage, no scheme
has one — the only choice is which failure you get, and format-first makes it
the good one:

- The store is already empty when the risky part starts.
- A crash, stall, truncated upload, or CRC mismatch leaves a partial store.
- `.restore-active` is written immediately after the format and removed only
  after the CRC verifies. `fsSelectStateStore()` treats its presence as
  "suspect — format and treat as first boot", repopulating from
  `/fixed/factory_state` + `/fixed/additional_state`.

So every failure resolves to a clean factory store, never to a plausible-looking
corrupt one. This marker is the **only** crash-safety artifact left in the
design; factory reset needs none (see below).

Integrity is confirmed only *after* application, because the gzip footer arrives
last — inherent to streaming without staging, and the price of the constraint.
`Content-Length` is an early sanity check, not the truncation detector; the gzip
footer is. Chunked uploads work.

### Merge mode dropped

`?merge=1` overlay semantics are not implemented. Format-first is cleaner, the
space argument favours it, and an overlay leaves stale externals and orphaned
`.db.gz` files that make a "restore" not one.

---

## 4. Factory reset

**Write random bytes over the entire state partition, then reboot.** No fast
path, no head-only variant. Starts at boot, needs no client.

### Why random and not erase

Erasing to 0xFF unlinks the store but leaves everything past the superblock
intact on flash — the Reticulum identity, WiFi passwords, WireGuard and TLS keys
all recoverable with a flash dump. A factory reset has to make a device safe to
hand on, so the whole extent is overwritten. Random rather than zeros because it
is the same cost and leaves no structure at all.

### Why not format

Formatting rewrites LittleFS at whatever location *this* firmware computes, and
leaves a perfectly findable superblock behind. flashmon's `det_state_partition()`
scans 4 K-aligned offsets upward from its table top for the 8-byte `littlefs`
magic in block 0 and reads geometry from the superblock struct — so a formatted
store is still detected, still reported, and still triggers the
"this image writes into the state partition" warning, permanently.

Formatting also cannot *move* the store. Position is not stored anywhere; it is
recomputed from the table top every boot. A store left low by an older,
lower-floored firmware stays low through any number of formats. Overwriting and
rebooting lets `statePartitionEnsure()` place a fresh one where the current
firmware thinks it belongs, and `format_if_mount_failed` create it — both
existing paths.

### Extent

From the **end of the last firmware partition to the end of the physical
chip** — the table top excluding `state`, with `reserved` folded in when the
table has one — not from the floor. `reserved` is inert filler by construction
so overwriting it costs nothing, and it is the one region where a stale, lower
superblock can survive: a predecessor firmware with a lower floor put its store
inside what this firmware's table calls `reserved`, and flashing the new image
does not write there. Floor-to-end leaves that one behind — which is exactly
the case this change exists to fix. The extent cannot be phrased as
"`reserved`'s start": gen-partitions emits no `reserved` row at all when
`fixed` reaches the floor (reticulous's and demo's current tables do exactly
that), so the anchor is the end of the last firmware partition, which equals
`reserved`'s start whenever one exists.

This extent already covers flashmon's entire detection window —
`det_state_partition()` scans a 64 KB window upward from the table top — so no
separate superblock scan is needed to make the detector come up empty.

When a board pins `state` in its own table there is no filler and no ambiguity:
overwrite that partition's extent and nothing else. A stale runtime store
elsewhere above such a board's table would survive; accept that — no hw-*
board pins `state` today, and scanning for it is machinery for a board that
does not exist.

`nvs` sits below the floor and is untouched — a factory reset must not discard
WiFi PHY calibration.

### SD

`s.sys.factory_reset` selects the target: `1` flash, `2` SD, `3` both. The flash
region can hold a stale store even when the active one is on the card, which is
why "both" exists.

The SD side is a plain recursive delete of `/sdcard/state` — not a card format
(recordings and logs live outside the store) and **not** a random overwrite. An
SD controller does its own wear levelling, so writing over a file's logical
blocks says nothing about the physical ones; the overwrite would cost time and
buy no guarantee. The flash path has no such indirection, which is why the
random overwrite is real there and pointless here.

### Implementation constraints

- **Wipe low-to-high.** The superblock at block 0 dies first, so a crash
  mid-wipe leaves a store whose mount fails → `format_if_mount_failed` → empty →
  first boot. That is why factory reset needs no marker and no recovery path.
- **The random source buffer must be internal DRAM.** A flash program disables
  the PSRAM cache; reading the source out of PSRAM mid-write faults. The whole
  operation runs on a DRAM-stack worker, same discipline as `fsFormatFlash`.
- Per 64 K block: `esp_flash_erase_region` then `esp_flash_write` of a
  DRAM-resident random buffer, refreshed per block from `esp_fill_random()`.
  WiFi is up in safe mode, so the hardware RNG is properly seeded.
- Use 64 K block erases for the aligned body of the region; a head remainder
  that is not 64 K-aligned gets 4 K sector erases (~5× the per-byte cost,
  bounded at <64 K). Never round the start down — that clobbers the firmware
  table — or up, which leaves data behind.
- Feed the task watchdog between blocks.

### Timing

Estimates from datasheet-typical figures for the W25Q/GD25Q-class parts these
boards carry — **measure on hardware before quoting to a user**:

| Operation | Per MB | 12 MB state region |
|---|---|---|
| 64 K block erase | ~2–3 s | ~30 s |
| Page program (256 B, ~0.5 ms) | ~2 s | ~25 s |
| **Erase + random overwrite** | **~4.5–5 s** | **~55–60 s** |

Cross-check: the chip-erase spec is ~20 s for 8 MB, consistent with 16 blocks/MB
at ~150 ms.

A minute of unexplained silence reads as a crash, so the page's estimate-driven
bar is not optional — and the measured per-MB figure is what the served
estimate is computed from.

### `reset factory` CLI

The existing verb takes on this behaviour: set the flag and reboot. It currently
formats the flash partition and reboots, and refuses outright when booted from
SD — the refusal goes away, since target selection is now explicit.

---

## 5. Code changes

| Where | What | Rough size |
|---|---|---|
| `spangap-core/include/service.h`, `spangap_init.cpp` | band argument on `serviceRegister`, boundary check in `serviceRunInit` | ~10 |
| `spangap-inside` generator | emit the band per registration | ~10 |
| `spangap-core/spangap_init.cpp` | read/clear/flush the three flags, `spangapSafeMode()`, skips in `spangapPostAppInit` | ~70 |
| `spangap-core/storage.cpp` | `storageStopFlushing()`; null-worker guards in `requestSave` / `storageSave` | ~20 |
| `spangap-core/targz.h/.cpp` | streaming ustar writer + streaming gunzip/untar reader, host-unit-testable | ~400 |
| `spangap-core/fs.cpp` | `.restore-active` check in `fsSelectStateStore`; random-overwrite wipe on the DRAM worker; superblock scan | ~180 |
| `spangap-core/cli_cmd_sys.cpp` | `reset factory` re-pointed at the flag | ~20 |
| `spangap-web/web.cpp` | safe mode: skip mapping-table load, routing short-circuit ahead of `findMapping` | ~15 |
| `spangap-web/safe_mode.cpp` | three pages, two gated endpoints, deadline timer | ~300 |
| `spangap-web/browser/` | Settings → System: three generated-panel `button` rows (the existing action widget — `fireCmd` → `device.set(key, value)` writes exactly the flag); warning dialogs for restore and factory reset (the latter picking target 1/2/3); "rebooting" page that polls `/` and `location.reload()`s | ~120 |

The buttons write exactly the storage keys the CLI writes, so the firmware side
is identical whichever way you enter. Restore and factory reset get warning
dialogs — both are irreversible and neither has an undo.

The reload page is required, not optional: without a real `location.reload()`
the browser sits on the stale app shell and never sees the safe-mode page.

Nothing here is on a hot path; the only performance-sensitive code is the inner
copy loop of the tar walk and the wipe.

## 6. Memory budget

| Path | Cost |
|---|---|
| Backup | `tdefl_compressor` ~160 KB PSRAM + 16 KB read + 16 KB deflate out |
| Restore | `tinfl_decompressor` ~11 KB + 32 KB wrapping LZ dict + 512 B tar header + 4 KB net buffer |
| Wipe | one 64 KB DRAM random buffer |

The restore's 32 KB dict must wrap — output is not resident, which is the whole
point.

There is no uncompressed-`.tar` fallback for a board where the 160 KB won't
allocate. Safe mode is the boot where that is least likely to be true; reinstate
it if a real board fails.

LittleFS write throughput is ~3–7 s/MB in practice: raw programming is ~2 s/MB,
and this platform's 8 KB-chunk-with-a-tick-yield discipline adds ~1.3 s/MB of
pure sleep at a 100 Hz tick. That yield is not optional during restore — we are
receiving over TCP while writing, and each program disables the PSRAM cache, so
long unyielded bursts stall the network task feeding us. A typical state store
is a few hundred KB, so extraction is 1–3 s and the upload dominates.

## 7. Rejected

| Alternative | Why not |
|---|---|
| Hot backup/restore against the live system | Needs `storageFlushHold` + `fsStateSeal` + fs-worker write enforcement + an `fs_op_t` originating-task field, and still cannot format-first. Strictly more code than the mode. |
| Stage the archive to flash, apply at boot | No space. This was the original design and the constraint killed it. |
| Extract to `<stateDir>.new/` and swap | Double the space, and LittleFS has no atomic directory swap. |
| Two-pass upload (`?verify=1` then apply) | Doubles the transfer and still leaves the apply pass unprotected. |
| RTC-RAM entry magic | The storage flag covers every reachable case; the unreachable case wants factory reset. |
| One generic safe mode with a menu page | The flag can name the operation, which deletes the menu, the landing page, and most of the client handshake. |
| One-time token for safe-mode auth | If you can enter safe mode you are already authenticated; the existing cookie check costs nothing, and the endpoint gate bounds the exposure. |
| `Service::inMaintenance()` virtual | Per-straddle opt-in with a default a future straddle gets wrong, where the registry already encodes the band. |
| Multipart upload parsing | Our page controls the upload; a raw body serves both it and curl. |
| A `MANIFEST` tar entry | The filename carries the same fields, where a human already reads them, with no writer, no parser, and no first-entry ordering constraint. |
| Overwriting SD files before unlink | The card's wear levelling makes a logical overwrite meaningless. |
| Following a hostname change after restore | More complexity than the problem is worth. |
| Head-only factory reset | Leaves every secret recoverable from a flash dump. |
| Uncompressed `.tar` fallback | Dead code in the one boot with the most free PSRAM. |
| Size-fit precheck (507) on the filename's `<x>kB` | ENOSPC mid-extraction lands in the same clean-factory path; dropping the check deletes the parser's warn/refuse taxonomy with it. |
| Duplicate-download suffix (` (n)`) tolerance | Dropped with the rest of the filename parsing; only the fw stub is read. |
| Factory-reset progress endpoint | A client-side bar over a served estimate says the same thing with no endpoint and no polling through a janky mid-wipe HTTP stack. |
| Progress-extended deadline | One fixed 10-minute timer covers every live transfer; extension plumbing serves only dead ones. |
| LCD in the safe-mode band | A hardware dependency to verify, for a screen the operator isn't looking at. Revisit if dark-screen recovery matters. |

## 8. Pitfalls

- **Clear the flag before doing anything else**, not after. Clearing late means
  a crash inside safe mode re-enters safe mode.
- **Wipe low-to-high.** High-to-low leaves a valid superblock over a destroyed
  filesystem — mountable, and garbage.
- **The wipe's source buffer must be internal DRAM.** PSRAM faults with the
  cache disabled mid-flash-op.
- **Respond and drain before wiping, not after.** A minute-long flash operation
  outlives the connection.
- **Do not restore across projects.** `s.sys.project` mismatch formats the
  flash `state` partition and reboots at next boot — on a flash store the
  cross-project archive silently destroys what it just restored; on an
  SD-backed store the format hits the wrong medium, the mismatch persists, and
  the device reset-loops. Refuse on a mismatched fw stub in the
  filename — and accept that a stripped filename cannot catch it, which is why
  the upload URL carries the name rather than leaving it optional.
- **Store tar paths relative to the state dir.** Absolute `/state` paths break
  flash↔SD portability, and the archive is the one place the two stores meet.
- **Reject `..`, absolute paths, over-long ustar names, and any typeflag other
  than regular file or directory** (links, devices, FIFOs) on extraction. The
  archive is attacker-supplied input written directly to the filesystem.

## 9. Open

Nothing at present.
