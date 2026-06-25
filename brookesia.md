# Brookesia — absorbing the phone UI as the new `-lcd`, with manifest-generated settings

Plan to replace the hand-rolled `spangap-lcd` launcher with a phone-style on-device
UI **derived from ESP-Brookesia's Phone system UI**, and to make **`straddle.yaml`
the single source** that generates settings panes (LCD + browser) and storage
defaults. Two efforts, one effort: the new shell gives us the *app object model* and
the *chrome*; manifest-generation fills the *settings content* on both surfaces.

Companion doc: [`apps_and_settings.md`](apps_and_settings.md) — the **current**
registration surfaces (the thing this plan replaces). Read it first; this plan does
not repeat it.

Scope verified against `/straddles` and a clone of esp-brookesia `v0.4.2`
(`/tmp/bk042`) on 2026-06-25. Target board: T-Deck (320×240, ESP32-S3 + octal PSRAM,
LVGL v9).

---

## 1. Goals & principles

1. **Functionality, not code.** Brookesia `v0.4.2` is a **reference spec**, not a
   dependency. We reimplement its *behavior* in spangap conventions. No
   `ESP_BROOKESIA_*` names, macros, log/guard helpers, or its `Core*` generality
   survive. No upstream seam — the phone UI was deleted from Brookesia master in the
   v0.7 rewrite, so there is nothing to track or stay compatible with (Apache-2.0;
   fork freely).
2. **Apps become objects.** Every launcher program becomes a subclass of a new
   `LcdApp` base with a real lifecycle. The `lcdRegister(name, icon, fn)` +
   scattered-hook model is retired. *All* apps convert (§8).
3. **Settings declared once.** A setting exists today three times (storage default in
   C++, LCD `lcdSetting*` pane, browser `Setting*` component). Declare it once in the
   owning straddle's `straddle.yaml`; generate all three (§6).
4. **Spangap conventions throughout.** `info/warn/err/dbg` not `ESP_LOG*`; LVGL only
   on the lcd task via `lcdRun()`/`ON_LCD`; ITS for cross-task; `fs_*` for I/O;
   PSRAM-aware allocation; Kconfig for tunables. Keep the existing `lcd_input.h` HAL
   contract and the `lcdRun`/`ON_LCD` threading model unchanged.
5. **Incremental and buildable.** Stand the new shell up *alongside* the old one
   behind a flag; convert app-by-app and pane-by-pane; flip the default last (§9).

---

## 2. Donor reference

Lives only in tags **≤ v0.4.2**, under `src/systems/phone/` + `src/widgets/` +
`src/core/`. Inventory (LOC, logic only — `assets/` 65k of Montserrat/wallpaper
C-arrays is **discarded**, we keep spangap's bitmap fonts/icons):

| Donor area | LOC | Disposition |
|---|---|---|
| `core/` (Core/CoreApp/CoreManager/CoreHome/CoreEvent + style/lv/stylesheet types) | 4.5k | **Collapse** the multi-"system" generality into the phone classes; merge its `lv_disp`/touch/lock plumbing into spangap-lcd's existing LVGL context (don't import a second copy) |
| `widgets/status_bar` | 1.1k | Reimplement; feed from spangap data sources (§5) |
| `widgets/navigation_bar` | 0.8k | Reimplement as one nav-intent source |
| `widgets/recents_screen` | 1.0k | Reimplement; reconsider snapshots on 320×240 (§10) |
| `widgets/gesture` | 0.8k | Reimplement gesture→nav-intent; touchless adaptation (§5.3) |
| `widgets/app_launcher` | 1.1k | Reimplement paged icon grid |
| `systems/phone` (+ `stylesheets/`) | 7.0k | Reimplement manager/home; keep the **320×240 dark** stylesheet-as-data, drop the other 8 |

Donor external coupling is low: **LVGL + STL + one `esp_log` shim**; no
NVS/WiFi/esp_lcd/FreeRTOS entanglement. Allocation already funnels through
`ESP_BROOKESIA_MEMORY_MALLOC/FREE` (Kconfig-overridable) — our PSRAM hook point.

**App lifecycle in the donor** (the model we keep): base `PhoneApp : CoreApp`,
pure-virtual `run()`/`back()`, optional `init/close/pause/resume/deinit`,
`installApp()` returns an id; per-app flags `enable_default_screen` (auto create +
clean the app's screen), `enable_recycle_resource` (auto-free on close — see §4.2),
`enable_resize_visual_area` (reflow when bars toggle). Status bar is a **renderer you
push values into** (no drivers). Recents has per-app snapshots + a RAM "memory label".
Navigation is decoupled via `sendNavigateEvent(BACK|HOME|RECENTS)` with gesture and
nav-bar as two producers — the key that makes touchless boards workable.

---

## 3. Target architecture (the new `spangap-lcd`)

The new `-lcd` keeps its current responsibilities (owns the display + the one LVGL
context + the lcd task; `lcdInit()` from `spangapInit()`; input via `lcd_input.h`) and
replaces the launcher internals with the phone shell:

```
spangap-lcd (new internals)
├── LcdApp            app object base + lifecycle + resource ledger      (§4)
├── shell/
│   ├── manager       foreground/back/recents state machine             (§5)
│   ├── launcher      paged icon grid                                    (§5.1)
│   ├── statusbar     battery / wifi+upstream / clock / app icons        (§5.2)
│   ├── navbar        Back/Home/Recents (tappable + keypad)              (§5.3)
│   ├── recents       app switcher                                       (§5.4)
│   └── stylesheet    320×240 data theme + our fonts                     (§5.5)
├── settings/
│   ├── SettingsApp   host that renders generated + custom panes         (§6)
│   └── widgets       switch/slider/text/dropdown/value/button builders  (reuse lcdSetting*)
└── built-in apps     Log, CLI (converted to LcdApp)                     (§8)
```

The **build system** grows a settings-generation pass that reads every staged
straddle's `straddle.yaml settings:` block and emits LCD pane code, storage-default
calls, and a browser descriptor (§6, §7).

Public API direction: the launcher/scroll/fullscreen/standby/font/input-group surface
of today's `lcd.h` is largely **subsumed into `LcdApp` methods**; `lcd_input.h`,
`lcdRun`/`ON_LCD`, `lcdInit`, display/standby ownership, and the Kconfig display config
are **retained**.

---

## 4. The app object model — `LcdApp`

### 4.1 Interface (proposed)

```cpp
// All methods run on the lcd task. The app owns exactly its root layer.
class LcdApp {
public:
    struct Config {
        const char* name;            // launcher label + lookup key
        const char* iconBasename;    // /fixed/lcd/icons/36x36/<name>.bin
        bool        statusBar   = true;
        bool        navBar      = false;   // most apps use Home gesture/back
        uint8_t     launcherPage = 0;
    };
    explicit LcdApp(const Config&);
    virtual ~LcdApp();

    // Lifecycle (was: lcdRegister fn / onShow / lcdGoHome / eviction)
    virtual void onCreate(lv_obj_t* root) = 0; // build UI once
    virtual void onShow() {}                    // brought to foreground
    virtual void onHide() {}                    // sent to background
    virtual bool onBack() { return false; }     // true = handled, false = go Home
    virtual void onClose() {}                   // evicted; ledger auto-freed after

    // Services the app calls (replace the old free functions)
    lv_obj_t*    root();
    lv_group_t*  inputGroup();                       // was lcdInputGroup()
    void         goHome();                            // was lcdGoHome()
    void         setFullscreen(bool);                 // was lcdProgramFullscreen()
    void         setScrollwheelArrows(bool);          // was lcdProgramScrollwheelArrows()
    void         setScrollHandler(lcd_scroll_fn_t);   // was lcdProgramScrollHandler()
    void         setStatusIcon(/* multi-state icon */);
    // Resource ledger (replaces enable_recycle_resource, §4.2)
    lv_timer_t*  timer(lv_timer_cb_t, uint32_t period, void* user = nullptr);
    lv_anim_t&   anim();                               // tracked; auto-deleted on close
};

// Registration (was lcdRegister): construct + hand to the shell.
int lcdInstall(LcdApp* app);     // returns id; shell owns lifecycle
```

`lcdInstall()` mirrors Brookesia's `installApp()`. The shell builds the root layer
lazily on first open (calls `onCreate`), keeps it resident (background), and may evict
(`onClose` → free) under memory pressure, rebuilding on next open.

### 4.2 Resource ledger (the `enable_recycle_resource` replacement)

Brookesia auto-frees an app's resources on close by **snapshotting and walking LVGL's
private animation linked list** (`_lv_ll`/`LV_GC_ROOT(_lv_anim_ll)`) — the one
LVGL-internals coupling in the donor and the part most brittle across LVGL 9.x. We do
**not** port it. Instead:

- **Objects** free for free: the app owns `root()`; deleting the layer frees the tree.
- **Animations & timers** (which live in LVGL globals with no owner) are created via
  `app.anim()` / `app.timer()`, which register them in a per-app **ledger**. `onClose`
  deletes the ledger's anims/timers. No LVGL internals touched.

This is a few dozen lines, gives the same "closed app can't leak" guarantee, and is the
canonical example of "functionality not code".

### 4.3 Conversion shape (old → new)

```cpp
// OLD: lcd_apps.cpp
lcdRegister("Log", "log", logRunFn);            // build in logRunFn(layer)

// NEW
class LogApp : public LcdApp {
  public: LogApp() : LcdApp({.name="Log", .iconBasename="log"}) {}
  void onCreate(lv_obj_t* root) override { /* build the textview */ }
  void onShow()  override { /* follow tail */ }
};
lcdInstall(new LogApp());
```

---

## 5. The phone shell

### 5.1 Launcher
Paged icon grid (multiple pages via `Config::launcherPage`). Icons are spangap's
existing `/fixed/lcd/icons/36x36/*.bin` (no Brookesia assets). Paging by gesture
(pointer drag) or — on keypad boards — focus-group navigation (§5.3).

### 5.2 Status bar (spangap data sources)
The bar is a renderer; we wire spangap's existing signals to its setters on the lcd
task (subscribe to the relevant storage/ephemeral keys):

| Element | Source | Notes |
|---|---|---|
| Battery | board battery key (e.g. hw-tdeck) | multi-frame icon + %; charge flag |
| WiFi | `net` wifi state | donor has 3 bars + off |
| **Upstream** | `net` upstream up/down | **donor has no upstream state** → add a custom multi-state status icon (spangap distinguishes wifi-associated vs upstream-reachable) |
| Clock | time/NTP | 12/24h |
| App icons | `LcdApp::setStatusIcon` | per-app, 3 alignment areas, multi-state |

Visual modes HIDE / SHOW_FIXED / SHOW_FLEX retained.

### 5.3 Navigation on touchless boards
Reimplement the donor's split: **nav intent** (`BACK / HOME / RECENTS`) is the
abstraction; **producers** are (a) gesture (needs an `LV_INDEV_TYPE_POINTER`) and (b)
the tappable nav bar. The T-Deck trackball **is** a pointer indev (already fed via
`lcd_input.h pointer_read`), so gesture math works on cursor drags; alternatively
disable gesture, show the nav bar, and map keypad keys → nav intent. The current
`lcdScroll()` edge-pan + `lcdInputGroup()` keypad-focus behaviors fold into the shell:
keep them as the touchless affordances the donor lacks. Launcher *paging* is the one
spot needing explicit keypad wiring.

### 5.4 Recents / app switcher
App-switcher with per-app cards. **Decision needed** (§10): full snapshot thumbnails
(screenshots held in PSRAM, donor behavior) vs. lightweight icon+title cards on
320×240 where a thumbnail is barely legible and costs PSRAM. Keep the donor's RAM
"memory label" (internal/external free/total) — useful on-device diagnostic.

### 5.5 Stylesheet & theming

Keep the donor's **stylesheet-as-data** mechanism — it's the bone most worth taking.

**What a stylesheet is.** One nested struct per (name, screen_size), assembled from a
sub-file per widget: `core_data` (screen_size, name, wallpaper, default font/colors),
`status_bar_data` (height, areas, battery/wifi icon *sets*, clock), `navigation_bar_data`,
`app_launcher_data` (tile/icon/label sizes, page-indicator spots), `recents_screen_data`,
`gesture_data` (swipe distance/speed/angle thresholds). The donor ships 9 sizes, all
dark: 320×240, 320×480, 480×480 (square), 800×480, 1024×600, 1280×800, 720×1280,
800×1280, plus `default`.

**Selection & calibration.** At `begin()` the shell auto-selects the registered sheet
matching the real panel (`getStylesheet(display_size)`), else the built-in `default`;
or an explicit `activateStylesheet(name, size)`. Then a **calibrate** step resolves
percent values to pixels and runs sanity cross-checks — notably it **auto-disables the
recents screen when gesture is disabled**, which directly bites our touchless boards: if
we turn gesture off we must wire nav-bar/keypad recents or lose the switcher (§5.3, §5.4).

**px vs percent — what reflows for free.** Every dimension is px *or* percent, mixed
(`StyleSize` carries both + `enable_square`; fonts are `FONT_HEIGHT_PERCENT(n)` *or*
`FONT_SIZE(px)`). In 320×240: status bar `100%×36px`, areas `50%`, icon `56%` square,
bar font `56%` of bar height are **relative** (reflow across nearby sizes); launcher tile
`140px`, icon `98px`, label `16px`, gesture spot `12px` are **absolute**. Consequence:
**one sheet tolerates a range of nearby resolutions** — you author a genuinely new sheet
only when the *design* differs (round 480×480 ≠ 320×240 landscape), not for every panel.

**What actually costs per-size: the bitmaps.** Layout reflows via percent, but
fixed-resolution assets do not — the status bar references explicit icon sets
(`battery_level1_20_20`, `wifi_level2_20_20`…), launcher icons, and a per-size wallpaper,
each needed at the matching resolution. So "support a size" = layout numbers (cheap,
mostly reused) + fonts that stay legible + an **asset set at that resolution**.

**Spangap adaptation.** Keep the px/percent model and auto-select flow; re-point all
asset references at spangap's own — `/fixed/lcd/icons/<res>/*.bin` + our bitmap fonts,
not Brookesia's PNG/Montserrat. Ship the **320×240 dark** sheet first; add a sheet per
board (Heltec, Seeed) as data, not code; light/named variants are supported by the
mechanism if ever wanted.

**Future — generate all assets from SVG + TTF at the needed resolutions.** The per-size
bitmap cost above is the thing to automate. Extend the build to treat **SVG (icons,
wallpaper) and TTF (fonts) as the source of truth** and rasterize each to exactly the
resolutions the *registered stylesheets* require — the set of `screen_size`s in play,
plus each sheet's icon/tile/font sizes, drive what gets rendered. This generalizes what
spangap already does piecemeal (`spangap-lcd/scripts/lcd-icons.py` does SVG→RGB565;
`gen-*-font.py` does BDF→C font) into one resolution-aware asset pipeline: declare
vector/TTF sources + a sheet's target sizes → build emits the `.bin` icons, bitmap fonts,
and wallpapers for every resolution, no hand-exported PNGs. This makes adding a board (a
new `screen_size`) a *data* change — register the sheet, and its assets render
themselves — and is the asset-side analog of the manifest-driven settings generation in
§6 (source-of-truth + target set → generated artifacts). Sequence it after the shell and
settings work land (post-§9.7); until then, hand-supply the 320×240 asset set.

---

## 6. Manifest-generated settings & storage defaults

### 6.1 The problem (ACME, verbatim today)
- storage defaults — `acme/esp-idf/src/acme.cpp`: `storageDefault("s.acme.enable",0)` ×3
- LCD pane — `acme/.../conditional/spangap-lcd/acme_lcd.cpp`: `lcdSettingSwitch(p,"Enable","s.acme.enable")` …
- browser pane — `acme/browser/src/panels/AcmePanel.vue` + `modules/acme.ts`: `<SettingToggle k="s.acme.enable"/>` + `menu.register('settings/network/acme', …)`

Three files, same keys, same widget intent. Generation collapses to one declaration.

### 6.2 Declaration in `straddle.yaml` (proposed schema)

```yaml
settings:
  - menu: Internet/ACME          # canonical path (§6.4); label defaults to leaf
    label: ACME
    when: spangap/spangap-web     # optional: whole-pane build gate (straddle present)
    fields:
      - section: ACME
      - switch:   { label: Enable,    key: s.acme.enable, default: 0 }
      - text:     { label: Domain,    key: s.net.dns.fqdn }     # cross-straddle key (§6.5)
      - dropdown: { label: Method,    key: s.acme.method, default: "",
                    when: CONFIG_SPANGAP_WEB,                    # per-field build gate
                    options: [ {label: Auto, value: ""},
                               {label: DNS-01, value: DNS-01},
                               {label: HTTP-01, value: HTTP-01} ] }
      - text:     { label: Directory, key: s.acme.url, default: "" }
      - custom:   acmeDnsCaption    # escape hatch (§6.6): hand-written slot
```

Field types (cover today's `lcdSetting*` + browser `Setting*`): `section`, `caption`,
`switch`, `slider`{min,max}, `text`{secret?}, `dropdown`{options}, `value` (read-only),
`button`{action}, `custom` (slot). Each value field: `label`, `key`, optional
`default`, optional `when` (CONFIG gate), optional `secret` (→ `secrets.*`).

### 6.3 Generation pipeline (one descriptor, three outputs)
The staging pass (§7) builds an in-memory descriptor from all `settings:` blocks, then
emits:

1. **LCD C++** into `staging/`: per-straddle `<prefix>SettingsRegisterGenerated()` that
   calls `lcdRegisterSettings(menu,label,paneFn)` and builds each field via the existing
   `lcdSetting*` builders; `custom:` fields call the straddle's hand-written slot fn.
   Wired into the Settings app / dispatcher. (Compile-time codegen — no runtime parser
   shipped on the device.)
2. **Storage defaults** into `staging/`: `<prefix>StorageDefaultsGenerated()` calling
   `storageDefault(key, default)` for keys this straddle **owns** (§6.5), wired into
   storage bring-up. Replaces the hand-written `storageDefault()` calls for
   settings-backed keys (non-settings runtime defaults stay in code).
3. **Browser**: a generated **TS descriptor** consumed by one generic
   `<GeneratedSettingsPanel>` renderer + automatic `menu.register(...)`. `custom:`
   fields resolve to a straddle-registered component slot. (Recommend data-driven over
   generating `.vue` files — more idiomatic, one renderer to maintain.)

Per-surface gating: LCD output only when `CONFIG_SPANGAP_LCD`; browser output only when
the web side is staged. `when:` gates drop fields/panes from the relevant output.

### 6.4 Menu taxonomy (unify)
Today LCD uses `Internet/ACME` and browser uses `settings/network/acme` — different
trees. Define **one canonical taxonomy** in the manifest; the generator maps it to each
surface. Provide a fixed top-level mapping table (e.g. `Internet→network`,
`Mesh Network→mesh`, `System→system`, board name→device) so both surfaces agree. This
is a one-time normalization decision to record in the schema docs.

### 6.5 Cross-straddle keys & ownership
A pane may surface keys owned by another straddle (ACME edits `s.net.dns.fqdn`, owned by
`spangap-net`). Rule: **panes** are generated from the pane-owner's manifest;
**storage defaults** are generated from the *key-owner's* manifest (prefix/explicit
`owns`). So ACME's pane shows the field but `spangap-net` declares the default for
`s.net.dns.fqdn`. Keeps defaults single-sourced and avoids double-definition.

### 6.6 Escape hatch
Generation can't express computed captions (ACME's DNS-TXT-capable note), live data, or
bespoke widgets (the map picker). `custom:` declares a named slot; the straddle provides
the slot impl (an LVGL builder fn on LCD, a registered component on browser). Generated
and custom fields interleave in one pane. This keeps the schema small — anything awkward
falls back to code instead of bloating the YAML vocabulary.

---

## 7. Build-system integration (`spangap-inside`)

A new staging pass, alongside the existing generators (`spangap_requires.cmake`,
`spangapInitStraddles()`, `CONFIG_STRADDLE_*`):

1. **Schema**: extend `build-system/schemas/straddle.schema.json` with the `settings:`
   block (§6.2) and `jsonschema`-validate it (fast, in `spangap validate`).
2. **Collect & validate** `settings:` from each staged straddle (respect `--without`/
   `--no-lcd`/web presence and `when:` gates).
3. **Emit**: generated LCD `.cpp` + storage-defaults `.cpp` into a synthetic staging
   component; generated browser descriptor `.ts` into the browser build tree.
4. **Wire**: register the generated LCD fns into the dispatcher / Settings app; the
   storage-default fns into storage init; the browser descriptor into the SPA bootstrap.
5. Keep it **regeneration-only** (no migrations — zero users; consistent with the
   no-config-version-migrations stance).

Generation is the same shape as auto-init: manifest → topologically-aware emit into
`staging/`. No new runtime parser on device; the firmware sees plain generated C++.

---

## 8. Conversion inventory (everything that changes)

### 8.1 Launcher apps → `LcdApp` subclasses

| App | Owner straddle | Current site | Target | Notes |
|---|---|---|---|---|
| Log | spangap-lcd | `lcd_apps.cpp` | `LogApp` | textview; tail-follow on `onShow` |
| CLI | spangap-lcd | `lcd_apps.cpp` | `CliApp` | scrollwheel-arrows mode; input group |
| Settings | spangap-lcd | `lcd_settings.cpp` | `SettingsApp` | hosts generated panes (§6) |
| Info | viewer | `viewer/.../conditional/spangap-lcd` | `ViewerApp` | `onShow` → home page; fullscreen |
| LXMF | lxmf | `lxmf_lcd.cpp` | `LxmfApp` | immersive thread view (fullscreen) |
| Maps | maps | `maps_lcd.cpp` | `MapsApp` | **custom scroll handler** (canvas pan) |
| Nomad | nomad | `nomad_lcd.cpp` | `NomadApp` | page client |

### 8.2 Settings panes → `straddle.yaml settings:` (delete the three hand-written copies each)

| Canonical menu | Owner | Today: LCD file / browser panel / storage defaults |
|---|---|---|
| Internet/ACME | acme | `acme_lcd.cpp` / `AcmePanel.vue` / `acme.cpp` |
| Internet/DuckDNS | duckdns | per-straddle trio |
| Internet/SSH | sshd | trio |
| Internet/UPnP | upnp | trio |
| Internet/WiFi | spangap-net | trio |
| Internet/mDNS | spangap-net | trio |
| Internet/WireGuard | wg | trio |
| Maps | maps | trio |
| Mesh Network/General | rns | trio |
| Mesh Network/LXMF | lxmf | trio |
| Mesh Network/Nomad | nomad | trio |
| Mesh Network/RNS Interfaces/AutoInterface | iface-auto | trio |
| Mesh Network/RNS Interfaces/ESPnow | iface-espnow | trio |
| Mesh Network/RNS Interfaces/LoRa | iface-lora | trio |
| Mesh Network/RNS Interfaces/TCP | iface-tcp | trio |
| System | spangap-lcd/core | trio |
| T-Deck | hw-tdeck | trio |
| Viewer | viewer | trio |

~18 panes × 3 sites collapse to ~18 manifest blocks (+ a few `custom:` slots for
computed/dynamic bits). Storage-default call sites (e.g. `acme.cpp` ×3) move to the
key-owner's manifest.

---

## 9. Phasing (incremental, each milestone builds & flashes)

1. **Shell skeleton** behind `CONFIG_LCD_PHONE` (new shell vs legacy launcher). `LcdApp`
   base + resource ledger; manager/launcher/status bar/recents; 320×240 stylesheet on
   spangap fonts. Port **Log** as the first `LcdApp`. T-Deck only.
2. **Status bar wiring**: battery/wifi/**upstream**/clock from spangap sources; per-app
   status icon. **Navigation** on the trackball (pointer-indev gesture + nav-bar) and
   keypad nav intent; launcher paging.
3. **Settings generation MVP**: schema + validator; generator emitting LCD panes +
   storage defaults + browser descriptor for **one** straddle (acme) behind a flag;
   generic `<GeneratedSettingsPanel>`; `SettingsApp` renders generated panes.
4. **Convert all settings** (§8.2) to `settings:` blocks; delete hand-written
   `*_lcd.cpp` panes, `*Panel.vue` + `register*`, and settings-backed `storageDefault()`
   calls. Resolve cross-straddle ownership and `custom:` slots.
5. **Convert all apps** (§8.1) to `LcdApp`; delete `lcdRegister` + per-program hook
   API; subsume scroll/fullscreen/focus into `LcdApp` methods.
6. **Touchless & polish**: standby integration, keyboard-vs-on-screen, recents snapshot
   decision (§10), cursor-scroll affordances, immersive fullscreen apps.
7. **Flip default** to the phone shell; remove the legacy launcher and the old `lcd.h`
   surface that `LcdApp` replaced (keep `lcdInit`/`lcdRun`/`ON_LCD`/`lcd_input.h`/display
   ownership). Add other board stylesheets as data (hand-supplied 320×240 asset set until
   milestone 9).
8. **Docs**: rewrite `spangap-lcd/README.md` + `INTERNALS.md`; update `apps_and_settings.md`
   to the generated model; document the `settings:` schema and the canonical taxonomy.
9. **Resolution-aware asset pipeline** (§5.5, later/independent): build renders all
   icons/wallpaper from **SVG** and fonts from **TTF** at exactly the resolutions the
   registered stylesheets require — generalizing `lcd-icons.py` + `gen-*-font.py` into one
   pass so adding a board `screen_size` is a data change. Decouples from the shell work;
   can land any time after milestone 1 once a second board size exists to justify it.

---

## 10. Risks & open decisions

- **PSRAM allocator.** STL containers + recents snapshots default to internal DRAM —
  spangap's scarce resource. Point the donor's allocator seam at PSRAM and add a global
  `operator new`/PMR for STL nodes; LVGL objects already route via our `lv_conf`.
  Measure DRAM before/after (WiFi static RX + SD DMA headroom).
- **Recents snapshots on 320×240** (§5.4): thumbnails vs icon+title cards. Lean toward
  cards on small screens to save PSRAM; keep the memory label.
- **Browser generation**: data-driven descriptor + generic renderer (recommended) vs
  generating `.vue`. Decide before milestone 3.
- **Canonical menu taxonomy** (§6.4): must be decided and recorded once; both surfaces
  derive from it.
- **Schema scope creep**: hold the field vocabulary small; push anything dynamic to
  `custom:` rather than growing YAML.
- **`enable_recycle_resource`**: resolved — per-app ledger (§4.2), no LVGL internals.
- **Secrets**: `secret:`/`secrets.*` keys generate the same panes but never leave the
  device; ensure the browser descriptor marks them write-only.
- **LVGL pin**: we own the lvgl version (spangap-lcd pins it); the donor is LVGL 9, we
  are LVGL 9 — keep the pin stable through the port. Exceptions already on
  (`CONFIG_COMPILER_CXX_EXCEPTIONS=y`); RTTI stays off (donor uses no `dynamic_cast`).
```
