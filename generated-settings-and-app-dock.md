# Plan — Generated settings panels + web app-dock shell

Builds on the current-state snapshot in [`apps_and_settings.md`](apps_and_settings.md)
(the two registration surfaces, the manifest→dispatch wiring, and the exact
`lcdSetting*` / `Setting*` vocabularies). Read that first; this doc is the
**proposed change**, not the situation.

Two threads, landed together because they touch the same registries:

- **A. Declarative settings** — one `settings:` block per `straddle.yaml`
  becomes the single source that the build lowers into (1) the LCD pane, (2) the
  web panel, and (3) the storage defaults. Kills the three-way hand-duplication.
- **B. Web shell** — Settings stops being a left drawer/overlay and becomes a
  first-class app window; window **docking is deleted**; the menu bar / hamburger
  is replaced by a **macOS-style bottom dock** of app icons (the same launcher
  SVGs), degrading to a phone bottom-nav (≤4 icons + a "More" triple-dot).

---

## Part A — Generate settings from `straddle.yaml`

### Why

Today a single setting is written **three** times and kept in sync by discipline:
the LoRa frequency list, for instance, lives in `iface-lora/.../lora_lcd.cpp`
(`lcdSettingDropdown` CSV), `iface-lora/browser/.../LoraPanel.vue` (`freqOptions`),
and the default in `iface-lora/esp-idf/src/lora.cpp` (`storageDefault`). They
drift. The row vocabularies on both sides already line up 1:1 (snapshot §3/§4),
so a descriptor → codegen lowering is near-mechanical.

### The descriptor

A `settings:` block, validated by a new sub-schema added to
`build-system/schemas/straddle.schema.json`. Path + row vocabulary mirror the
existing APIs exactly:

```yaml
settings:
  - panel: "Mesh Network/RNS Interfaces/LoRa"   # == lcdRegisterSettings path == menu.register path
    label: "LoRa"
    placement: 0
    rows:
      - section: "LoRa"
      - switch:   { label: "Enable", key: "s.lora.0.enable", default: 0 }
      - section: "Radio"
      - dropdown: { label: "Frequency", key: "s.lora.0.frequency", default: "868100000",
                    options: [ {v: "433000000", l: "433 MHz (EU/AS)"},
                               {v: "915000000", l: "915 MHz (US)"} ] }
      - slider:   { label: "TX power", key: "s.lora.0.tx_power", min: -9, max: 22, default: 14 }
      - text:     { label: "IFAC network", key: "s.lora.0.ifac_netname", default: "" }
      - section: "Status"
      - value:    { label: "State", key: "lora.0.state" }     # bare key → read-only, NOT seeded
      - caption:  "Live, updates ~1 Hz."
```

Row → both-sides lowering (all already exist; see snapshot §3/§4):

| descriptor row | LCD helper | web component | default seeded? |
|---|---|---|---|
| `section` / `caption` | `lcdSettingSection/Caption` | heading / note div | — |
| `switch` | `lcdSettingSwitch` | `SettingToggle` | yes (int 0/1) |
| `slider {min,max}` | `lcdSettingSlider` | `SettingSlider` | yes (int) |
| `text {secret}` | `lcdSettingText` | `SettingText` | yes (empty if `secret`) |
| `dropdown {options}` | `lcdSettingDropdown` (CSV) | `SettingSelect` | yes |
| `value` (read-only) | `lcdSettingValue` | read-only field | **no** |
| `button {cmd}` | `lcdSettingButton` | action button | no |
| `list` (new) | clean+rebuild list | `v-for` array editor | no |

**Default-seeding rule:** a row's `default:` is emitted as the storage default
**iff** its key starts with `s.` (persisted config). Bare keys (`lora.0.state`)
are ephemeral live state and get nothing — this is exactly the `s.*` vs no-prefix
distinction in snapshot §5. `secret:` keys seed empty and never read back.

### Defaults — replacing the scattered `storageDefault()` calls

Per the explicit ask: the build also emits the **defaults** from the same block,
so `storageDefault("s.lora.0.enable", 0)` & friends in each straddle's init
disappear. Two viable emission shapes:

- one generated `storageDefaultTree("s.lora", R"({...})")` per straddle
  (matches the existing `cli.cpp` / `net.cpp` pattern), or
- a flat list of `storageDefault(key, val)` calls.

Prefer the **tree** form — it is one silent bracket (`storageDefaultTree` opens
the no-notify bracket, snapshot storage.cpp:1042) and reads cleanly. The
generated seeding must run even on **web-only / headless** builds (no LCD), so it
is emitted into the *firmware* init band, not the `when: spangap-lcd` LCD hook.

### Generation wiring

This rides the existing per-staged-straddle collector machinery that already
produces `spangap_init_dispatch.gen.cpp` and `straddles.gen.ts` (snapshot §2).
Add a fourth collector in `spangap-inside` that, for every staged straddle
carrying a `settings:` block, emits:

1. **LCD pane** → `staging/.../<repo>_settings.gen.cpp`: a generated
   `<prefix>SettingsGenRegister()` that calls `lcdRegisterSettings(path,label,fn)`
   and fills `fn` with `lcdSetting*` calls. Gated like every LCD hook (compiled
   only when `spangap-lcd` is staged). Reuses the existing helpers verbatim — **no
   new firmware runtime**.
2. **Defaults** → folded into the generated firmware init (the `storageDefaultTree`
   per straddle), called in the normal init band.
3. **Web panel** → the **`settings:` block itself**, serialized to JSON (the build
   already has it parsed for readers 1–2) and **inlined as a JS object literal**
   into the generated `straddles.gen.ts` — e.g. `registerGeneratedPanel({panel,
   rows})`. No separate asset, no runtime fetch, no YAML parser in the SPA; it
   tree-shakes with the module. **One** generic `GeneratedPanel.vue` (`spangap-web`)
   interprets that object at runtime into `Setting*` components, registered through
   the existing `menu.register(path, label, {type:'panel', component})` path so it
   slots into the same settings tree (hosted by the Settings app, Part B).
   **Runtime-interpreted data, not generated SFCs** — far less fragile codegen.

A straddle then adds a `settings:` block and **deletes** its `*_lcd.cpp` pane fn,
its `*Panel.vue`, and its `storageDefault()` calls. Three copies → one.

### Lists / list-boxes — add/remove (peers, wifi nets, identities)

There is **no list abstraction today** — LXMF identities, nomad bookmarks, and
TCP peers each re-implement it. The shared shapes (from the explore):

- **LCD**: a container built once; a `rebuildList()` does `lv_obj_clean(list)` then
  re-adds one row per item in a loop, stashing `item-key-by-index` in a
  `std::vector` and passing the index as `user_data` to the row callback; scroll
  pos saved/restored; live refresh via `onStorageChange → rebuildList`
  (`lxmf_lcd.cpp:721`, `nomad_lcd.cpp:1004`).
- Add/remove backing is inconsistent today — a **command-sentinel** key in some
  places (`storageSet("nomad.cmd.bookmark.del", hash)`, `nomad_lcd.cpp:1189`,
  matching `wg.keygen` / `lxmf.cmd.identity_new`, snapshot §5) and a **whole-array
  rewrite** in others (`device.sendJson({s:{tcp:{peers:arr}}})`, `TcpPanel.vue:190`).

**Decision: standardise everything on `xx.cmd.yy` storage sentinels.** The
generated list widget is **display + intent only** — it renders the array at
`key:` and, on a button, writes a command key. It **never mutates the array
itself**. Whatever owns the real add/delete — the firmware task, or a browser-side
dialog/action handler — **subscribes** to that command key and performs the work
(validating, popping an "add peer" dialog to collect fields, writing the array,
etc.). This is the single sanctioned path; the whole-array-rewrite path is
dropped. It also keeps LCD and web symmetric: both sides do nothing but
`storageSet(cmd, …)`, and the same subscriber on the device reacts regardless of
which surface triggered it (snapshot §5 — storage is the universal bus).

```yaml
- list:
    key: "s.tcp.peers"             # array-of-objects in storage (display source only)
    item_label: "{host}:{port}"    # template over item fields → row title
    add:    { cmd: "tcp.cmd.peer.add" }   # "+" writes this sentinel; a subscriber does the add
    remove: { cmd: "tcp.cmd.peer.del" }   # row delete writes the item id to this key
    fields:                        # optional per-item editor rows (reuse row types)
      - text: { label: "Host", key: "host" }
      - text: { label: "Port", key: "port" }
```

Generator emits the clean+rebuild loop (LCD) / `v-for` (web) plus buttons that
`storageSet` the declared `cmd:` keys (delete writes the item id; add writes a
trigger/payload). The **subscriber is supplied** by the owning straddle — usually
its firmware task via `storageSubscribeChanges`, or a registered browser
dialog/action for add flows that collect fields. The generated widget stays
domain-agnostic; the domain logic lives behind the command key.

### Long entries scrolling left/right (Brookesia watch marquee)

Today long values either **wrap** (`lcdSettingValue` stacks label-over-value with
`LV_LABEL_LONG_WRAP` past 18 chars) or **ellipsize** (`LV_LABEL_LONG_DOT`) — no
horizontal scroll anywhere. LVGL ships `LV_LABEL_LONG_SCROLL_CIRCULAR` (the
marquee), just unused.

Brookesia's watch scrolls **only the focused/selected** row, not every label at
once (a panel full of LXMF hashes all marqueeing would be noise). So: a
**focus-driven marquee** in `lcd_settings.cpp` — for `value` / hash / list-title
labels, register a callback that flips the label to `LV_LABEL_LONG_SCROLL_CIRCULAR`
on `LV_EVENT_FOCUSED` and back to `LV_LABEL_LONG_DOT` on `LV_EVENT_DEFOCUSED`.
With keypad nav via `lcdInputGroup()` the row you land on scrolls its full hash;
the rest stay ellipsized. One contained helper; replaces the >18-char vertical
stack for hashes. Gate behind a tunable so touch-only boards can keep tap-to-expand.

### Exceptions / escape hatches (stay hand-written)

1. **Custom interaction flows** — LXMF "create identity" inline-keyboard +
   two-tap *Destroy* arming, drag-to-reorder peers. Keep the escape hatch: a
   straddle may still register a hand-written pane, and/or a `custom:` row that
   calls a named C++/TS hook appended after the generated rows.
2. **Computed/conditional rows** — LXMF shows only identities that exist; menus
   that rebuild on a `watch`. Static structure is declarative; dynamic show/hide
   stays a hook (later: a `visible_if` predicate).
3. **Live status `value` rows** — declarative as read-only; the generator must
   **not** seed defaults for their bare keys.
4. **Secrets** — `secret: true` → masked LCD text / write-only web field under
   `secrets.*`, seeded empty, never read back.
5. **Runtime-derived option lists** (scanned wifi networks) — not a static
   `options:`; needs `options_key:` pointing at a storage array, rendered live.

---

## Part B — Web shell: app-dock, no drawer, no docking

### B1. Settings becomes a first-class app (kill the drawer/overlay)

Today Settings is a `q-drawer` (left, push on desktop / overlay on mobile) wrapping
`SettingsPanel.vue`, opened by `menuStore.activePanel !== null`
(`MainLayout.vue:10-22,117`). Replace with: **Settings is an app window** like any
other — a `SettingsWindow.vue` (a `FloatingWindow`) whose body is the existing
settings tree navigation + `GeneratedPanel`/hand-written panes. Launched from the
dock (gear icon). 

Delete:
- the `<q-drawer class="settings-drawer">` block and `drawerWidth` / `onDrawerToggle`
  / click-outside-dismiss logic in `MainLayout.vue`,
- the drawer-specific overlay/scrim and the `compact` drawer behavior,
- `SettingsPanel.vue`'s drawer-mobile-header (back button) — the window chrome
  provides close/back now.

The settings **registry** (`menu.register('settings/...')`) is **kept** — those
panels now render *inside* the Settings app window instead of the drawer. The
in-window nav is the existing path-tree (groups → submenus → leaves), reusing
`menu.ts` `sortedMenus` / `activePanelComponent`.

### B2. Delete window docking

Docking is "annoying and surprising" — remove it wholesale:

- `advanced.ts` — delete the dock store: `docks`, `dockOrder`, `dockWindow`,
  `undockWindow`, and the `layout` computed that carves edge rects
  (`advanced.ts:28-110`).
- `FloatingWindow.vue` — delete `detectDockEdge`, `performDock`, `performUndock`,
  `canDock`, `DOCK_THRESH`, `preDockW/H`, `wasDockedOnDragStart`, `isDocked`; in
  `endDrag` always `clamp()+saveState()` (drop the `if (edge) performDock` branch
  at `:328-329`). Windows become pure floating (desktop) / fullscreen (phone).
- `TerminalWindow.vue` default-dock-to-top on phone (`:51-61`) → just open
  fullscreen like every other window on phone.
- Drop `dock` fields from persisted window state in `windows.ts` / localStorage
  (migrate-by-ignore: unknown fields are dropped; no users, so no migration code —
  per project convention).

### B3. The dock (replaces MenuBar + hamburger)

Replace `MenuBar.vue` (desktop horizontal menu) and its mobile hamburger entirely
with a **bottom Dock** component (`Dock.vue` in `spangap-web`), mounted once by
`MainLayout.vue`. The dock renders one **icon per launchable app**.

**App registry.** Today there is no "app" abstraction and no icon system — apps
are FloatingWindows toggled by exported `*Visible` refs, surfaced as menu
`action` items (snapshot §4). Introduce a small registry (extend the menu store or
a sibling `apps` store):

```ts
registerApp({
  id:    'maps',
  label: 'Maps',
  icon:  'maps',            // basename of the launcher SVG (see B5)
  open:  () => { mapVisible.value = true },   // wraps the existing visibility ref
  placement: 0,
})
```

Existing app-bearing straddles (maps, lxmf, nomad, viewer, cli, log) register an
app instead of (or alongside) a menu action; Settings registers as the gear app
(B1). The dock iterates `apps` sorted by placement. Clicking an icon calls
`open()` (raises/shows the window); a running-app dot can mark visible windows.
The `Window`/`Status`/`LXMF`/`Nomad` top-level menu groups (snapshot §4) collapse
into dock icons.

### B4. Responsive overflow (desktop dock ↔ phone bottom-nav)

One breakpoint already exists: `useCompact()` = `$q.screen.lt.md`
(`lib/viewport.ts`). Drive the dock off it:

- **Desktop (`!compact`)**: centered macOS-style dock floating above the bottom
  edge, all app icons, labels on hover.
- **Phone (`compact`)**: a fixed bottom **nav bar** (iOS-style), icon + small
  label. **Overflow rule:** if app count > 5, show the **first 4** (by placement)
  as icons and a **5th "More" triple-dot** that opens a sheet/popover listing the
  remainder. ≤5 apps → show all, no overflow. (4+1 is the standard iOS tab-bar
  budget; matches the ask.)

The "first 4" selection is by `placement` ascending, so a straddle can pin its app
into the primary row with `placement`.

### B5. Icons — "same SVGs"

The launcher icons already exist as per-straddle SVGs and are rasterized for the
firmware by `spangap_lcd_icons()` from `assets/lcd-icons/` (e.g.
`reticulous/assets/lcd-icons/{maps,viewer,rns}.svg`,
`spangap-lcd/esp-idf/assets/lcd-icons/{gear,cli,log}.svg`). The dock reuses **the
same SVG files**:

- During staging, copy/expose each staged straddle's `assets/lcd-icons/*.svg` into
  the web build's asset path (parallel to how `spangap_lcd_icons` collects them for
  firmware), so `icon: 'maps'` resolves to that SVG in the browser.
- `registerApp({icon})` names the basename; `Dock.vue` renders the SVG inline
  (currentColor-friendly) so it themes with the shell.

This keeps one icon source of truth for both the LCD launcher tiles and the web
dock.

---

## How A and B meet

- Part A's generated **web panels** register via the unchanged
  `menu.register('settings/...')` path and now render **inside the Settings app
  window** (B1) instead of the drawer. No change to the generator from B.
- Part A's generated **LCD panes** are unaffected by B (LCD already launches
  Settings as a program tile — snapshot §3).
- Part B's **app registry** is orthogonal to settings generation, but a future
  `app:` block in `straddle.yaml` could declare the dock app (id/label/icon/open)
  the same way `settings:` declares panels — a natural follow-on, not in scope here.

---

## Files: add / change / delete (concrete)

**Add**
- `build-system/schemas/` — `settings:` (+ optional `list:`) sub-schema.
- `spangap-inside` — the fourth collector + three emitters (LCD `.gen.cpp`,
  defaults tree, web JSON descriptor).
- `spangap-web/.../components/GeneratedPanel.vue` — runtime descriptor renderer.
- `spangap-web/.../components/Dock.vue` — desktop dock + phone bottom-nav + overflow.
- `spangap-web/.../stores/apps.ts` (or extend `menu.ts`) — `registerApp`.
- staging step to surface `assets/lcd-icons/*.svg` to the web build.

**Change**
- `lcd_settings.cpp` — focus-driven marquee helper; wire into `lcdSettingValue` +
  list row builder.
- `MainLayout.vue` — drop the drawer; mount `Dock` + `SettingsWindow`.
- `FloatingWindow.vue`, `advanced.ts`, `windows.ts`, `TerminalWindow.vue` — strip
  docking.
- each converted straddle's `straddle.yaml` (+ delete its `*_lcd.cpp` pane,
  `*Panel.vue`, `storageDefault()` calls).
- app-bearing straddles' `register*` modules → call `registerApp`.

**Delete**
- `MenuBar.vue` (and its hamburger), the dock store in `advanced.ts`,
  `SettingsPanel.vue` drawer chrome, dock fields in window state.

---

## Phased rollout

1. **Settings codegen spine** — schema + collector + the three emitters + the web
   `GeneratedPanel`. Convert **one** pure-static straddle end-to-end
   (LoRa or espnow), delete its three hand-written copies, build & flash, confirm
   LCD + web + defaults parity.
2. **Marquee** — focus-driven horizontal scroll in `lcd_settings.cpp`; verify on
   an LXMF identity hash row with the trackball/keypad.
3. **Convert the static panels** — net/wifi, gps, duckdns, cli, rns, mdns, ssh,
   upnp, wg, acme.
4. **Web shell** — kill docking (B2), Settings-as-app + drawer removal (B1), Dock
   + app registry + icons + responsive overflow (B3–B5). Land as its own change;
   it is independent of A except that settings panes now mount in the Settings app.
5. **`list` type** — last, against TCP peers + LXMF identities + nomad bookmarks,
   since it standardises add/remove and touches firmware command handlers.

---

## Risks / open questions

- **`list` add/remove now standardises on `xx.cmd.yy` storage sentinels** — the
  widget only emits commands; the owning straddle supplies the subscriber that
  does the real mutation (firmware task, or a browser add-dialog). So each
  converted list still needs that subscriber written/verified (e.g. a
  `tcp.cmd.peer.add/del` handler), but the UI side is now uniform and the
  whole-array-rewrite path is gone. Deliberately sequenced last.
- **Web rendering choice** — runtime-interpreted descriptor (recommended: one
  `GeneratedPanel`, no fragile TS codegen) vs generated SFCs. Plan assumes the
  former.
- **Custom panes** (LXMF identity create, two-tap destroy, drag reorder) must keep
  a hand-written escape hatch; don't force them into the descriptor.
- **Dock running-state** — whether to show a per-app "open" indicator and how
  many windows can be open at once on desktop (phone is already single-window via
  `focusedWindowId`). Cosmetic; decide during B4.
- **No config migrations** (project convention, zero users) — dropping dock fields
  from persisted window state needs no migration shim.
