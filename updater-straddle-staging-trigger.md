# Plan: `updater` straddle — app-side staging + trigger (manual-file phase)

## Context

We're replacing the A/B `ota` model with a **single main-app + tiny updater-partition** design
(reached over a long design discussion). The full picture:

- One main app slot (`ota_0`) + one small, permanent **updater** app slot (`ota_1`), no factory.
- A size-agnostic 4 MB-floor image; the app discovers real flash size at first boot and creates a
  `state` LittleFS partition (~½ flash, ¼ fallback if firmware is "underwater"), then mounts it.
- Updates are applied by the updater partition: it mounts `/state` read-only, **streams**
  `/state/flashme.bin` to flash (partition table + shrink-wrapped `app` + `fixed`, skipping
  bootloader and itself; nvs/otadata are never in the image), sets the main app to boot, reboots.
  A power-loss brick window is accepted (serial recovery); bootloader + updater are serial-only updated.
- Boot selection is plain `esp_ota_set_boot_partition` / otadata — **no custom bootloader**.

**This plan covers only the first slice:** the app-side `updater` straddle that stages-and-triggers,
with the file placed **manually** for now. Download (an https `wget` living in `spangap-net`),
the updater partition's flashing code, and **signature/crypto verification are explicitly deferred**
to later plans.

## Scope

**In scope (this plan)**
- New straddle `spangap/updater` (prefix `updater`), firmware-only, auto-init `updaterInit`.
- On boot: delete a leftover `/state/flashme.bin` (post-update cleanup).
- A trigger function: check the staged file, check it won't overwrite `state` (unless forced),
  set boot to the updater partition, reboot.
- Storage command interface: subscribe to `updater.cmd.`, act on `updater.cmd.update` /
  `updater.cmd.update_force`; report problems on `updater.error`.
- CLI tool `updater [-f]` that drives the same trigger and prints the error.

**Deferred / assumed (named, not built here)**
- Partition-layout rework (size-agnostic image, `updater` ota slot, runtime `state` sizing) — sibling plan.
  This plan **assumes** a partition table already has an `updater` app slot + a `state` partition.
- The **updater partition app** (the flasher). Needed for an end-to-end reboot to land somewhere;
  for testing this slice a **stub** updater app (logs, sets boot back to the main app, reboots) is enough.
- Download path (`wget` in `spangap-net`); signature verification (added inside `updaterTrigger` later).

## Files

Create:
- `straddles/updater/straddle.yaml` — `name: spangap/updater`, `prefix: updater`, `firmware: esp-idf`,
  `init: [ {call: updaterInit} ]`, `requires: []` (core is implicit; net added later for download).
  Model the shape on `straddles/ota/straddle.yaml`.
- `straddles/updater/esp-idf/include/updater.h` — `void updaterInit(void);` (C++ linkage, **no** `extern "C"`,
  per the auto-init dispatcher convention).
- `straddles/updater/esp-idf/src/updater.cpp` — the logic below.
- `straddles/updater/esp-idf/CMakeLists.txt` — `idf_component_register` using `${SPANGAP_REQUIRES}`
  (include `spangap_requires.cmake`); deps `app_update` (for `esp_ota_*`), `esp_partition`, `spi_flash`.
- `straddles/updater/README.md` — brief: the command/CLI interface and the manual-staging workflow.

## Behavior spec (`updater.cpp`)

Use platform idioms: `info/warn/err`, `fsStatePath("/flashme.bin")` (see `fs.cpp:fsStateDir`),
`storageSet`/`storageGetStr`, `cliRegisterCmd`, `esp_ota_*`.

1. **`updaterInit()`** (auto-init, main-app band):
   - If `fsStatePath("/flashme.bin")` exists, delete it (we're the main app → any prior update is done).
     *Note edge:* staging by hand then a bare reboot before triggering discards the file — acceptable
     given the CLI stages-and-triggers in one shot.
   - `storageSubscribeChanges("updater.cmd.", ON_CHANGE { ... })`: when the changed key is
     `updater.cmd.update` (truthy) call `updaterTrigger(false)`; when `updater.cmd.update_force` call
     `updaterTrigger(true)`. (Bare/ephemeral keys — no persistence, edge-trigger is natural.)
   - `cliRegisterCmd("updater", cb)`.

2. **`bool updaterTrigger(bool force, std::string& err)`** — shared by the CLI and the subscription:
   - Clear `updater.error`.
   - **File check:** if `flashme.bin` absent → `err = "no image staged"`, `storageSet("updater.error", err)`, return false.
   - **Overwrite check** (skipped when `force`): parse the partition table embedded in `flashme.bin`
     (at its 0x8000 region), take `max(app.end, fixed.end)`; find the current `state` partition start via
     `esp_partition_find_first(DATA, …, "state")`. If the staged firmware end > `state` start →
     `err = "would overwrite state"`, set `updater.error`, return false.
   - **Commit:** `esp_ota_set_boot_partition(<updater partition>)` (find by label/subtype `ota_1`).
     On success `esp_restart()` (does not return). On `esp_ota_set_boot_partition` failure → set `err`/`updater.error`, return false.

3. **CLI `updater [-f]`**: parse `-f`; call `updaterTrigger(<-f>, err)`; on false, write `err` to the
   CLI (`cli_write_fn`) and return; on true it reboots (so success is implicit — the session drops).

## Storage / CLI interface

| key (bare, ephemeral)      | meaning |
|----------------------------|---------|
| `updater.cmd.update`       | set truthy → stage-check + reboot to updater (honors overwrite guard) |
| `updater.cmd.update_force` | same, bypasses the would-overwrite-state guard |
| `updater.error`            | last failure: `"no image staged"` / `"would overwrite state"` (cleared each attempt) |

CLI: `updater` (guarded) / `updater -f` (forced). Same path as the cmd keys, for SSH/TCP-CLI use.

## Verification

In-container: `spangap validate` (manifest + dep graph), then
`spangap build reticulous/reticulous --with reticulous/hw-tdeck` (also `--with spangap/updater` until it's
in a default set) — confirm the straddle stages, links, and the CLI cmd registers.

On-device (user-driven, per the device-CLI-not-reachable note): user flashes, then via `spangap cli`:
- `updater` with no file staged → prints `no image staged`, `updater.error` set; no reboot.
- Stage a dummy `/state/flashme.bin` (scp via sshd, or any fs path under `fsStateDir()`); run `updater`
  → device reboots; `spangap log` shows boot into the updater slot (the **stub** updater app, until the
  real flasher exists), which sets boot back and returns. (Requires the assumed partition layout +
  stub updater app — call out if absent.)
- Stage an oversized image and run `updater` (no `-f`) → `would overwrite state`; `updater -f` → reboots.

## Notes / open items
- Straddle name `updater` (prefix `updater`) is new; it supersedes the A/B `ota` straddle, which can be
  retired in a later pass — not touched here.
- `requires` stays empty now; add `spangap/spangap-net` when the download (`wget`) lands.
- **Signature/crypto verification stays app-side** — in the download/stage path (later `updaterTrigger`
  before it reboots). The **updater partition never verifies**; it trusts the staged file and just flashes.
  So no mbedtls in the updater. Left as a TODO marker on the app side.
- **Updater partition is shrink-wrapped, no reserved margin** (64 K-aligned, like `app`/`fixed`).
  Rationale: padding only helps if you can grow into it without a reflash — but the updater can *only*
  change via a serial flash, which rewrites the whole layout anyway, so a bigger updater costs a serial
  flash whether or not it was pre-padded. Padding just steals flash from `state`. A no-network, no-crypto
  flasher is ~340–580 KB (IDF baseline + littlefs + esp_ota/partition write + optional inflate + logic;
  no ECDSA/mbedtls — that's app-side), so expect ~`0x60000`–`0x80000` once built. Caveat: the updater is
  the flasher and structurally skips the slot it runs from, so it can never write a new copy of *itself*
  (XIP self-overwrite) — updating the updater binary is therefore serial-only, which is already its update
  path. Finalize by measuring; belongs to the deferred partition-layout plan.
