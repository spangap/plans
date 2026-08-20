# cron: entries in storage, next-minute scheduling

> **Status: IMPLEMENTED (2026-08-19), not yet built or device-tested.**
> Deviations from the text below: owners host their enable→entry coupling on
> the storage task via the new `storageSubscribeChanges(scope, cb,
> onStorageTask=true)` (added for this — modules without a long-lived task had
> nowhere to receive change callbacks), instead of one-shot net-task
> subscriptions; and a dow field of `7` now really matches Sunday.

Replace the `/state/crontab` file with per-entry storage keys, and replace the
every-minute match loop with a precomputed next-run minute checked on a coarse
poll or deep-sleep wake.

## 1. Storage model

- One entry = one key: **`s.cron.tab.<name>`**, value is exactly one crontab
  line as today: `min hour dom mon dow flags command` (single-spaced; the
  `%-4s` column formatting dies with the file). Field syntax and the `-`/`A`/`N`
  flags column are unchanged.
- `s.` prefix → persists to `root.json`, syncs to the browser config tree, and
  is user-editable via the normal config surfaces (web config, CLI `set`).
- Owners create their entry when their feature is enabled and remove it when
  disabled:
  - enabled → `storageDefault("s.cron.tab.upnp", "*/15 * * * * N upnp update")`
    — *Default*, not *Set*, so a user's tweak to the schedule survives reboots
    while the feature stays enabled.
  - disabled → `storageUnset("s.cron.tab.upnp")` (fires subscriptions, unlike
    `storageDeleteTree`).
- `cronDefault()` is deleted. No replacement helper in cron.h — owners write
  the two storage calls themselves; the key prefix is documented in cron.md.
  Version-gating of cron entries in owners disappears (the calls are
  idempotent and run every init).

### Owner changes

| Owner | Entry | Condition |
|---|---|---|
| upnp | `s.cron.tab.upnp` = `*/15 * * * * N upnp update` | `s.upnp.enable` — apply at init and on enable change (subscription) |
| duckdns | `s.cron.tab.duckdns` = `*/15 * * * * N duckdns update` | duckdns's enable/configured condition, same pattern |
| acme | `s.cron.tab.acme` = `0 3 * * * N acme renew 30` | acme active/configured (verify exact gate at implementation) |
| log (core) | `s.cron.tab.logrotate` = `0 0 * * * A logrotate 7` | unconditional `storageDefault` at init |

## 2. cron core rework (cron.cpp)

### Next-run computation

New `cronNextRun(const char* entry, uint32_t fromMinute)` → earliest epoch
minute ≥ `fromMinute` at which the entry's five time fields match (local
time), or `UINT32_MAX` if none within a ~400-day horizon (malformed entry).
Reuses `cronMatchField()` unchanged. Iterates day-first (check dom/mon/dow
per day, then hour, then minute) so worst case is a few thousand cheap field
checks, not half a million.

### State

- `RTC_DATA_ATTR uint32_t cronNextMinute` — the stored "first minute one or
  more entries need to run". Survives deep sleep in RTC RAM, recomputed on
  cold boot (entries live in storage, so nothing is lost). **Deviation from
  the ask:** spec says nvram; NVS would cost a flash write per reschedule and
  buys nothing since cold boot recomputes anyway. Guarded by the existing
  `rtcRamValid()` magic, as `cronLastMinute` is today.
- `cronLastMinute` stays: last minute already serviced, used for dedup and
  one-slot catch-up.

### Recompute triggers

`storageSubscribeChanges("s.cron", …)` already covers `s.cron.tab.*` adds,
edits and unsets plus `s.cron.enable`. On any change: recompute
`cronNextMinute = min over entries of cronNextRun(now)` and `cronUpdateLock()`.

### Check (task loop, unchanged 30 s cadence)

```
now = epoch minute
if now < cronNextMinute: return          // the entire common case
for each s.cron.tab.* entry:
    due = cronNextRun(entry, cronLastMinute + 1) <= now
    if due: run (subject to A/N flags, via cronStream → CLI as today)
cronLastMinute = now
cronNextMinute = min over entries of cronNextRun(entry, now + 1)
```

Computing due from `cronLastMinute + 1` (not just "matches this minute")
means a check that lands late — skew overshoot, busy CLI, long ITS drain —
still fires the entry once instead of silently skipping the slot.

**Clock-jump guard:** if `now` differs from `cronLastMinute` by more than a
day in either direction (NTP first sync from 1970, manual clock set), do not
catch up — set `cronLastMinute = now`, recompute `cronNextMinute`, run
nothing.

Entry enumeration uses `storageForEach("s.cron.tab.", …)` collecting into
file-static state (cron task only; the callback has no user-data pointer).

### Lock

`cronUpdateLock()`: `allow = s.cron.enable && at least one s.cron.tab.* key`.
`crontabHasEntries()` (file scan) is replaced by a key-existence check.

## 3. Deep sleep: skew-safe wake (written, stays disabled)

The deep-sleep entry path stays exactly as disabled as it is today (`#if 0`
around `cronDeepSleep`, commented `sys.going_down` subscription, pm side
untouched). The *logic inside it* is rewritten to the new scheme so enabling
later is just reconnecting the wires:

- **Wake target:** `min over non-A entries of cronNextRun(entry, now + 1)`.
  A-flagged entries are skipped on deep-sleep wakes anyway, so waking for one
  is pure waste; the all-entries `cronNextMinute` still drives awake polling.
  If only A entries exist, there is no wake target and the lock stays held
  (no sleep) — same "no schedule that can wake us → no sleep" invariant as
  today.
- **Iterate to the minute:** target instant `T = targetMinute*60 + 1` (the
  existing +1 s lands inside the minute). Each hop sleeps
  `0.85 * (T - now)` — 15 % early, covering single-digit-percent oscillator
  skew with margin — until `T - now ≤ 5 s`, then sleeps the full remainder
  (worst-case skew on 5 s is tens of ms). `cronWakeupHandler()` on each timer
  wake: if `now` has reached `cronNextMinute` → stay up, boot, the task loop
  services it; otherwise re-sleep for the next hop without finishing boot.
  Converges in a handful of hops (remaining shrinks ~7× per hop).
- **RTC later:** on hardware with a working RTC, the timer-iterate and the
  30 s poll both collapse into "program RTC alarm for targetMinute, wake on
  int pin, run the check". Separate straddle, not written now — noted in
  cron-internals.md as the intended replacement seam.

## 4. Removals & migration

- Delete `/state/crontab` handling: `crontabHasEntries()` file scan, the file
  read in `cronPoll`, `cronDefault()`, `extractCronCommand()`.
- Delete `esp-idf/data/factory_state/crontab` (factory seeding just stops
  shipping it).
- Existing devices: `cronInit()` unlinks `<stateDir>/crontab` if present. No
  import — sibling entries are recreated by their owners on next init; the
  fleet flashes together so hand-added lines on a live device are assumed
  not worth a migration path. (If that's wrong: the alternative is importing
  unrecognized non-comment lines as `s.cron.tab.file<n>` before unlinking.)

## 5. Docs

- `cron.md` / `cron-internals.md`: rewritten for the storage model and
  next-minute scheduler (current invariant only, no "used to"). Wire/flow
  sections lead with the sequence. RTC-pin future noted in internals.
- Touch-ups where the crontab file is referenced: `spangap-core/README.md`,
  `docs/storage.md`, `docs/init-internals.md`,
  `docs/power-management{,-internals}.md`, `spangap/INTERNALS.md`.

## 6. Semantics changes (accepted by design)

- Disabling a feature now *deletes* its entry; the old "comment it out to
  disable while keeping a hint" affordance is gone.
- A user schedule tweak survives while the feature stays enabled
  (`storageDefault`), but a disable→enable cycle restores the owner's stock
  line.
- Ad-hoc user jobs are `set s.cron.tab.<name> "…"` instead of editing a file.
