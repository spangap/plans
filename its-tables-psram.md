# Moving connTable / itsPool to PSRAM

Reclaiming the 10240 B of internal `.bss` that ITS's two fixed tables occupy
(`connTable` 128 × 44 B = 5632, `itsPool` 128 × 36 B = 4608). Attempted with
`PSRAM_BSS`; it boots into a panic. Reverted. This is what is known.

## Why it looked safe

The rule the tables were placed under is "anything touched inside a critical
section stays internal". That rule is broader than the hardware requires, and
two things argue it does not reach plain data:

- **The lock, not the data, is what cannot move.** A spinlock acquire uses an
  `S32C1I` atomic that is unreliable on external RAM. That binds `portMUX_TYPE`
  itself and every FreeRTOS control block embedding one (`StaticQueue_t`,
  `StaticStreamBuffer_t`, `itsPool[].sbCtrl`) — but `connMux`, `itsPoolMux` and
  `itsLinkMux` are separate 8-byte statics, not part of the arrays.
- **A critical section does not open a cache-disabled window over its data.**
  `spi_flash_disable_interrupts_caches_and_other_cpu()` (IDF
  `spi_flash/cache_utils.c:125`) parks the other core by sending it an IPC
  **task** (`esp_ipc_call_nonblocking`) and busy-waits on `s_flash_op_can_start`
  until that core has parked itself in IRAM. A core inside `portENTER_CRITICAL`
  is not preemptible, so the cache goes down only after it has left.
- **Precedent in the same file.** `itsLinks` (`its_link_t[256]`, 8 KB) is
  `MALLOC_CAP_SPIRAM` and is read *and written* inside
  `portENTER_CRITICAL(&itsLinkMux)` in `linkAcquire` — in production, working.

`its-internals.md` already recorded the intent: "move them the same way (lazy,
count-guarded) if more headroom is needed."

## What happened

`PSRAM_BSS` on both arrays. Build is clean and the bytes land where intended:
static internal `.bss` 88864 → 78664 (−10200 B), both symbols relocated to
`0x3C480000`. On the device it panics during boot, in the **fs worker**:

```
Guru Meditation Error: Core 1 panic'ed (LoadProhibited)
EXCVADDR: 0x00000000   A3: 0x00000000

fsWorkerFn(fs.cpp:701) → itsPoll(its.cpp:1286) → processInboxMsg(its.cpp:1188)
  → onFsOp(fs.cpp:470) → handleOp(fs.cpp:273) → fopen → _fopen_r → __sflags
```

`__sflags` dereferences the mode string, so `req->path2` was NULL. `fs_op_t::OPEN`
is enumerator 0, so **an all-zero `fs_op_t` decodes exactly as this crash**:
`op = OPEN`, `path = NULL` (which `handleOp`'s tripwire deliberately passes — it
only rejects small *non-zero* pointers), `path2 = NULL`. The worker was handed a
pointer to a struct reading as zeros.

The pointer itself is not null: `onFsOp` dereferences `op->op` at `fs.cpp:470`
*before* `handleOp`, so a null `op` would have faulted one frame earlier.

## What is not the cause

- **Not a partition move.** `partitions.csv` is size-agnostic (`app` fixed at
  `0x10000`/`0x770000`, `state` created at runtime above a fixed 8 MB floor), so
  the image-size changes across these builds cannot relocate `/state`.
- **Not the pickup race.** `proxyOp` sends with `ITS_WAIT_PICKUP` and
  `processInboxMsg` gives `pickupSem` *after* dispatch (`its.cpp:1194`), so the
  caller's stack frame outlives `handleOp`. The struct is alive when read.

## Open question

Why a live `fs_op_t` reads as zeros to the worker. Candidates, untested:

1. The fs worker holds a DRAM stack precisely because it runs SPI-flash code
   with the cache disabled. If any ITS state it touches *inside* that window is
   now PSRAM, the read faults — but this crash faults at 0, not at a PSRAM
   address, so this would have to be an indirect zero read rather than a direct
   one.
2. Ordering: `EXT_RAM_BSS` is zeroed during PSRAM init. Anything reaching ITS
   before that reads zeros rather than faulting. Boot order says nothing should,
   but the crash is a boot crash and the timing is early.
3. Timing exposure of a pre-existing race: PSRAM scans lengthen the
   `connAlloc`/pool critical sections, and the boot burst of connects is where
   this fired.

Next step is instrumentation, not another attempt: log `req`, `req->op`,
`req->path`, `req->path2` on entry to `onFsOp`, and re-run with the annotations
restored. Distinguishing (1)/(2) from (3) matters — (3) would mean the bug
exists today and PSRAM only made it likely.

## Collateral worth fixing on its own merits

After the panic the node came up with factory-empty settings and no WiFi
credentials, on its own AP. **No SD card was inserted**, which excludes the
benign explanation (`fsSelectStateStore()` falling back to flash while the real
settings sat on a card): the flash store is the only store this node has, and it
came up empty. So the contents were destroyed, by one of:

1. **A LittleFS format.** `mountStateLittlefs()` sets `format_if_mount_failed`,
   so a partition that will not mount is reformatted. What argues against it is
   that settings go through `atomicWriteFile` (`storage.cpp:479`) — write
   `<path>.new`, close, rename, and only then remove the plain sibling — and
   LittleFS is copy-on-write with CRC'd metadata pairs, so an interrupted write
   should not cost the mount. What argues for it is that nothing else on this
   node erases the store without saying so.
2. **A restore marker.** `fsSelectStateStore()` calls `fsFormatStateStore()`
   when `RESTORE_MARKER` is present, logging "restore did not complete".

Both are now distinguishable after the fact rather than by inference: the mount
is tried without the format fallback first, and entering the fallback logs the
wipe and counts it in NVS (`spangap/state_wipes`, `state_wipe_err`) — a
different partition, so the record outlives the wipe. A node that comes up empty
can be asked whether it was wiped, and how often.

Also hardened: `handleOp`'s tripwire now treats NULL as bogus wherever the op
dereferences the pointer, and logs the request pointer alongside both paths. The
crash above becomes a refused op with a log line naming it, instead of a panic
inside the VFS — which matters more than usual here, since the panic is what put
the filesystem in the state that cost the settings.
