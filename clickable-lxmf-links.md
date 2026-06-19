# Clickable `lxmf@<hash>` links → open LXMF conversation

## Context
Micron pages rendered in the nomad browser can contain LXMF addresses written as
`lxmf@3fa116d1db88601752198526fa0bc01e` (literal `lxmf@` + 32-hex destination hash).
Today these are inert text. We want them to be clickable/tappable so that activating one
opens an LXMF conversation with that contact — including contacts we've never heard
announced (no display name or public key yet), in which case we fire a path request so the
name + send-capability light up once the announce arrives.

There are two browsers (web UI and on-device LCD), so two trigger storage vars. Each var is
an independent signal: the firmware **core** reacts to both (ensure conversation + path
request), while each **UI surface** reacts only to its own var (bring itself forward + open
the thread). This keeps nomad fully decoupled from lxmf — nomad only writes a storage var.

## Decisions (confirmed with user)
- **Each source pops its own UI.** `lxmf.url_web` → web LXMF panel comes forward (client-side);
  `lxmf.url_lcd` → on-device LCD LXMF program comes forward + opens thread. Firmware core ensures
  conversation + fires path-req for both.
- **Open immediately.** Open the thread right away on the bare/truncated hash and fire the path
  request; name + outbound send become available once the announce arrives.

## Storage var contract
Two **ephemeral** command vars (no `s.` prefix → not persisted, no re-trigger on reboot):
- `lxmf.url_web` — written by the web nomad browser (`device.sendJson`)
- `lxmf.url_lcd` — written by the on-device LCD nomad browser (`storageSet`)

Value = the 32-hex destination hash. The firmware **core** consumes (unsets) the key after
handling, which also makes re-tapping the *same* hash re-fire (empty→hash is a change).
Each subscriber reads the hash from the change-callback `val` argument (not by re-reading).

---

## Part A — Web nomad browser: detect + render clickable links
File: `/straddles/nomad/browser/src/lib/micron.ts` — in `renderInline()` (~line 91, plain-text
path after `escapeHtml`, line 106). Scan plain-text runs for `/lxmf@([0-9a-fA-F]{32})/g` and emit
`<a class="mlxmf" data-lxmf="<hash>">lxmf@<hash></a>` instead of the raw text. (Mirror how the
existing micron link emits `<a class="mlink" data-mtarget=…>` at line 136. Only scan text that is
*not* already inside an emitted tag.)

File: `/straddles/nomad/browser/src/panels/NomadWindow.vue`
- `onPageClick()` (~line 445): add a branch — if `ev.target.closest('a.mlxmf')`, read `data-lxmf`
  and call `nomad.openLxmf(hash)` (new module fn); `preventDefault`.
- Add `.page :deep(.mlxmf)` CSS near the existing `.mlink` style (~line 608): distinct colour +
  `cursor: pointer`.

File: `/straddles/nomad/browser/src/modules/nomad.ts` — add `openLxmf(hash)` mirroring `addBookmark`
(line ~192): validate against `HASH_RE`, then `device.sendJson(nest('lxmf.url_web', h))`. **No lxmf
import** — nomad only touches the shared device store.

## Part B — LCD nomad browser: detect + render tappable token
File: `/straddles/nomad/esp-idf/conditional/spangap-lcd/src/nomad_lcd.cpp`
- `scanInline()` (lines 265-310): detect `lxmf@<32hex>` and emit a new `SEG_LXMF` segment
  (extend the `SegKind` enum at line 262, carry the hash in `Seg.target`).
- `addLine()` (lines 364-368): handle `SEG_LXMF` like `SEG_LINK` via a new `addLxmf()` modeled on
  `addLink()` (lines 312-321) — clickable label, distinct colour, added to `lcdInputGroup()` for
  trackball/keyboard focus, with an `onLxmfClick` event cb.
- `onLxmfClick()` (new, modeled on `onLinkClick` line 251): `storageSet("lxmf.url_lcd", hash)`.
  Keep a parallel `std::vector<std::string> s_lxmfTargets` next to `s_linkTargets` (line 115), or
  reuse the target string directly via user_data indexing.

## Part C — LXMF firmware core: ensure conversation + path request
File: `/straddles/lxmf/esp-idf/src/lxmf.cpp`, in `lxmfTaskMain` near the other
`storageSubscribeChanges` calls (lines 3224-3236). Add a handler `onOpenContactUrl(key, val)` and:
```
storageSubscribeChanges("lxmf.url_web", onOpenContactUrl);
storageSubscribeChanges("lxmf.url_lcd", onOpenContactUrl);
```
Handler logic:
1. Ignore empty `val` (the unset echo). Parse/validate 32-hex → `dh[16]`.
2. `storageUnset(key)` to consume.
3. `int n = selectedId();` (line 2471) — bail if no usable identity (`idAt(n)`).
4. `ensureConvFile(n, peer)` (lines 240-245) so the conversation exists immediately.
5. Path request if unknown: if `rnsdRecallPubkey(dh, …)` misses, call `rnsdRequestPath(dh)`
   (rnsd.h:104). This reuses the exact unknown-sender flow at lxmf.cpp:1577-1594 — the announce it
   triggers populates the pubkey + display name via `onAnnounceFromRnsd` (line 1008).
   Core does **not** touch any UI (keeps non-LCD builds clean).

## Part D — LXMF LCD slice: bring program forward + open thread
New public API in spangap-lcd (the "function in -lcd" anticipated):
- File `/straddles/spangap-lcd/esp-idf/include/lcd.h`: declare `void lcdShowProgram(const char* name);`
- File `/straddles/spangap-lcd/esp-idf/src/lcd_ui/lcd_launcher.cpp`: implement it — scan `s_entries`
  for the entry whose registered name matches and call the existing `openEntry(idx)` (lines 123-144).
  Must run on the lcd task (touches LVGL); document as "call on lcd task" (invoked inside `lcdRun`).

File: `/straddles/lxmf/esp-idf/conditional/spangap-lcd/src/lxmf_lcd.cpp`
- In `lxmfLcdRegister()` (line 1028) add `storageSubscribeChanges("lxmf.url_lcd", onLcdOpenUrl);`
- `onLcdOpenUrl(key, val)` (new): ignore empty; parse hash; then
  `lcdRun(...)` a lambda that calls `lcdShowProgram("LXMF")` then the existing `openThread(peer)`
  (lines 548-559) — both run synchronously in order on the lcd task. `peerName()` (line 226) already
  falls back to the truncated hash for unknown contacts. (Core Part C handles conv-ensure + path-req
  for this same var, so the slice only does UI.)

## Part E — LXMF web module: bring panel forward + open peer
File: `/straddles/lxmf/browser/src/modules/lxmf.ts`. The store already exposes
`openPeer(peer)` (line 259); the menu store exposes `openPanel(id)` (menu.ts:220). Panels register as
`lxmf/id-<n>` or `lxmf/messages` (lines 620/626). Add a reactive `watch` on
`device.settings?.lxmf?.url_web` (registered in `registerLxmf`, line 602): when it becomes a valid
hash, `openPeer(hash)`, `menu.openPanel('lxmf/id-' + selectedId)` (fall back to `'lxmf/messages'` when
single-identity), then clear the local copy. The local `sendJson` merge fires this synchronously in
the same tab — no firmware round-trip needed for the panel switch.

## Files to modify (summary)
- nomad web: `browser/src/lib/micron.ts`, `browser/src/panels/NomadWindow.vue`, `browser/src/modules/nomad.ts`
- nomad LCD: `esp-idf/conditional/spangap-lcd/src/nomad_lcd.cpp`
- lxmf core: `esp-idf/src/lxmf.cpp`
- lxmf LCD: `esp-idf/conditional/spangap-lcd/src/lxmf_lcd.cpp`
- spangap-lcd: `esp-idf/include/lcd.h`, `esp-idf/src/lcd_ui/lcd_launcher.cpp`
- lxmf web: `browser/src/modules/lxmf.ts`

## Verification
1. Build: `spangap build reticulous/reticulous --with reticulous/hw-tdeck` from the workspace root.
   Confirms both firmware (core + both -lcd slices + new lcdShowProgram) and the Vite SPA compile.
2. Flash: `spangap flash`; watch `spangap log -f` for clean boot.
3. **Web path**: open the nomad browser web UI, load a micron page containing an `lxmf@<hash>` token
   (or craft one) → it renders as a styled link → click it → the LXMF panel comes forward with a
   conversation open on that hash; for a never-seen hash, verify (`spangap log`) a path request is
   issued and the display name fills in after the announce.
4. **LCD path**: on the T-Deck nomad browser, focus the `lxmf@` token with the trackball and activate
   → the on-device LXMF program comes to front with the thread open on that hash.
5. Re-tap the same link to confirm it re-fires (consume-on-handle works).

Device-side interaction (clicking, trackball, sending) is driven by the user — in-container can only
verify build + boot/log.
