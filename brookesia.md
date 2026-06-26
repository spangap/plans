# Brookesia — a phone-style on-device UI as the new `-lcd`

Replace the hand-rolled `spangap-lcd` launcher with a phone-style on-device UI
**derived from ESP-Brookesia's Phone system UI**: an *app object model* with a
real lifecycle, plus the *chrome* (status bar, launcher, navigation, recents) as
**stylesheet-as-data**.

**This plan is two sequential efforts, and only the first is detailed here.**

- **Effort 1 — the phone UI (this plan).** Stand up the new shell + `LcdApp`
  model, convert every launcher app to it, ship the 320×240 dark stylesheet on
  spangap's own fonts/icons, flip the default, delete the legacy launcher. This
  is **pure UI chrome and app lifecycle** — it touches no settings machinery.
- **Effort 2 — manifest-generated settings (deferred, §9).** Declare each
  setting once in `straddle.yaml` and generate the LCD pane, storage default,
  and browser descriptor. **Not started until Effort 1 ships.** Throughout
  Effort 1, settings keep working *exactly as they do today* — every straddle's
  `*LcdRegister` hook still calls `lcdRegisterSettings()`/`lcdSetting*` against
  an unchanged registry; the new Settings app is just a host that renders that
  same tree. Nothing in Effort 1 edits a single `*_lcd.cpp` settings pane,
  `*Panel.vue`, or `storageDefault()` call.
- **Effort 3 — resolution-aware asset pipeline (deferred, §10).** Generate
  icons/wallpaper from SVG and fonts from TTF at the resolutions the registered
  stylesheets need. Independent; can land any time after Effort 1.

Companion doc: [`apps_and_settings.md`](apps_and_settings.md) — the **current**
registration surfaces (programs, panes, storage, both sides). Read it first;
this plan does not repeat it.

Scope verified against `/straddles`, the live `spangap-lcd` source, the T-Deck
input HAL (`hw-tdeck/.../tdeck_lcd.cpp`), and a clone of esp-brookesia `v0.4.2`
(`/tmp/bk042`) on 2026-06-25. Target board: **T-Deck** (320×240, ESP32-S3 +
octal PSRAM, LVGL v9).

---

## 1. Goals & principles

1. **Functionality, not code.** Brookesia `v0.4.2` is a **reference spec**, not a
   dependency. We reimplement its *behavior* in spangap conventions. No
   `ESP_BROOKESIA_*` names, macros, log/guard helpers, or its `Core*` generality
   survive. No upstream seam — the phone UI was deleted from Brookesia master in
   the v0.7 rewrite, so there is nothing to track (Apache-2.0; fork freely).
2. **Apps become objects.** Every launcher program becomes a subclass of a new
   `LcdApp` base with a real lifecycle. The `lcdRegister(name, icon, fn)` +
   scattered-hook model is retired. *All* apps convert (§8).
3. **Settings untouched in Effort 1.** The existing `lcdRegisterSettings()` +
   `lcdSetting*` registry stays as-is; the new `SettingsApp` is a thin `LcdApp`
   wrapper around today's settings page-stack. Generation is **Effort 2** (§9).
4. **Spangap conventions throughout.** `info/warn/err/dbg` not `ESP_LOG*`; LVGL
   only on the lcd task via `lcdRun()`/`ON_LCD`; ITS for cross-task; `fs_*` for
   I/O; PSRAM-aware allocation; Kconfig for tunables. Keep the existing
   `lcd_input.h` HAL contract and the `lcdRun`/`ON_LCD`/lcd-task threading model
   **unchanged** — they already do exactly what the shell needs.
5. **Incremental and buildable.** Stand the new shell up *alongside* the legacy
   launcher behind `CONFIG_LCD_PHONE`; convert app-by-app; flip the default last
   (§7). Every milestone builds and flashes on the T-Deck.

---

## 2. Donor reference

Lives only in tags **≤ v0.4.2**, under `src/systems/phone/` + `src/widgets/` +
`src/core/`. Inventory (LOC, logic only — `assets/` 65k of Montserrat/wallpaper
C-arrays is **discarded**; we keep spangap's bitmap fonts/icons):

| Donor area | LOC | Disposition |
|---|---|---|
| `core/` (Core/CoreApp/CoreManager/CoreHome/CoreEvent + style/lv/stylesheet types) | 4.5k | **Collapse** the multi-"system" generality into the phone classes; **merge** its `lv_disp`/touch/lock plumbing into spangap-lcd's existing LVGL context — do **not** import a second copy. |
| `widgets/status_bar` | 1.1k | Reimplement as a renderer; feed from spangap storage keys (§5.3) |
| `widgets/navigation_bar` | 0.8k | Reimplement as one nav-intent producer (§5.4) |
| `widgets/recents_screen` | 1.0k | Reimplement as **icon+title cards**, not snapshots (§5.5) |
| `widgets/gesture` | 0.8k | Reimplement gesture→nav-intent on the trackball pointer (§5.4) |
| `widgets/app_launcher` | 1.1k | Reimplement paged icon grid (§5.2) |
| `systems/phone` (+ `stylesheets/`) | 7.0k | Reimplement manager/home; keep the **320×240 dark** stylesheet-as-data, drop the other 8 |

Donor external coupling is low: **LVGL + STL + one `esp_log` shim**; no
NVS/WiFi/esp_lcd/FreeRTOS entanglement. Allocation funnels through
`ESP_BROOKESIA_MEMORY_MALLOC/FREE` (Kconfig-overridable) — spangap's PSRAM hook
point, though we mostly inherit spangap-lcd's existing `gp_alloc`/`lv_malloc`
PSRAM routing instead (§6.4).

**App lifecycle in the donor** (the model we keep): base `PhoneApp : CoreApp`,
pure-virtual `run()`/`back()`, optional `init/close/pause/resume/deinit`;
`installApp()` returns an id; per-app flags `enable_default_screen` (auto create
+ clean the app's screen), `enable_recycle_resource` (auto-free anims/timers/
screens on close — donor walks LVGL's private `_lv_anim_ll`; we do **not** port
that, §4.2), `enable_resize_visual_area` (reflow when bars toggle). Status bar is
a **renderer you push values into**. Navigation is decoupled via
`sendNavigateEvent(BACK|HOME|RECENTS)` with gesture and nav-bar as two producers
— the key that makes touchless boards workable.

**What the donor adds over today's launcher.** spangap-lcd is *already*
phone-shaped: a 4×3 launcher, a 24px status bar (clock/wifi/battery), a
drag-up home-bar gesture, lazy-built persistent per-app layers with `onShow`,
a keypad focus group, edge-pan scroll, per-program fullscreen. The donor's net
new value is therefore narrow and concrete: **(a)** a real app object/lifecycle
(`LcdApp`) replacing `lcdRegister(fn)` + scattered free functions; **(b)** a
**recents/app-switcher** (today there is none); **(c)** a **Back** and
**Recents** nav intent (today only Home exists); **(d)** **stylesheet-as-data**
replacing hardcoded pixel constants, so a second board is a data change; **(e)**
a **paged** launcher and an **upstream** status element. Effort 1 is scoped to
exactly these.

---

## 3. Target architecture (the new `spangap-lcd`)

The new `-lcd` keeps its current responsibilities (owns the display + the one
LVGL context + the lcd task; `lcdInit()` from the generated init dispatch; input
via `lcd_input.h`; `lcdRun`/`ON_LCD` cross-task hop on ITS aux port 10) and
replaces the launcher internals with the phone shell:

```
spangap-lcd (new internals)
├── LcdApp            app object base + lifecycle + resource ledger      (§4)
├── shell/
│   ├── manager       foreground / back / home / recents state machine   (§5.1)
│   ├── launcher      paged icon grid                                     (§5.2)
│   ├── statusbar     battery / wifi / upstream / clock / app icons       (§5.3)
│   ├── nav           nav-intent: gesture-bar + nav-bar + ESC + Home btn  (§5.4)
│   ├── recents       app switcher (icon+title cards + memory label)      (§5.5)
│   └── stylesheet    320×240 dark theme as data + our fonts/icons        (§5.6)
└── apps/            Log, CLI, Settings — converted to LcdApp             (§8)
```

`SettingsApp` is **a host, not a generator**: it wraps the *existing*
`lcdRegisterSettings()` page-stack (`lcd_settings.cpp`) verbatim. Every other
straddle keeps registering its panes through its unchanged `*LcdRegister` hook.
The build system grows **nothing** in Effort 1 — manifest-driven settings
generation is Effort 2 (§9).

Public API direction: the launcher/scroll/fullscreen/standby/focus surface of
today's `lcd.h` is **subsumed into `LcdApp` methods**; `lcd_input.h`,
`lcdRun`/`ON_LCD`, `lcdInit`, display/standby ownership, the text-view/term API,
the `lcdSetting*` builders, and the Kconfig display config are **retained**.

---

## 4. The app object model — `LcdApp`

### 4.1 How the lifecycle maps to what already exists

The current launcher (`lcd_launcher.cpp`) already implements every state an
`LcdApp` needs — `LcdApp` is a *typed wrapper* over that machinery, not new
runtime behavior:

| `LcdApp` method | Today's mechanism (`lcd_launcher.cpp`) |
|---|---|
| `onCreate(root)` | `Entry::fn(layer)` — built once, lazily, on first open |
| `onShow()` | `Entry::showFn` — fired on every `openEntry()` after build |
| `onHide()` | the hide path when another layer becomes current (`saveLayerFocus`) |
| `onBack()` | **new** — today there is no Back; ESC routes to the focus group only |
| `onClose()` | the `LV_EVENT_DELETE` path on the layer (Log/CLI close their ITS conns there) |
| resident root | `findProgramLayer(idx)` — the layer persists, tagged via `user_data` |
| eviction | `lv_obj_del()` under memory pressure → next open rebuilds |

So the shell's manager **replaces the `Entry`/`openEntry` code** with a
`std::vector<LcdApp*>` and calls the virtuals at the same points the old code
called the function pointers. No new threading, no new persistence model.

### 4.2 Interface

```cpp
// All methods run on the lcd task. The app owns exactly its root layer.
class LcdApp {
public:
    struct Config {
        const char* name;            // launcher label + lookup key (string literal)
        const char* iconBasename;    // /fixed/lcd/icons/<res>/<name>.bin
        bool        statusBar    = true;
        bool        navBar       = false;  // request the on-screen Back/Home/Recents bar
        bool        fullscreen   = false;  // immersive: reclaim the status-bar rows
        uint8_t     launcherPage = 0;      // which launcher page the tile lands on
    };
    explicit LcdApp(const Config&);
    virtual ~LcdApp();

    // Lifecycle (was: lcdRegister fn / showFn / eviction)
    virtual void onCreate(lv_obj_t* root) = 0;  // build UI once, into root
    virtual void onShow() {}                     // brought to foreground
    virtual void onHide() {}                     // sent to background
    virtual bool onBack() { return false; }      // true = handled; false = go Home
    virtual void onClose() {}                     // evicted; ledger auto-freed after

    // Services the app calls (replace the old free functions)
    lv_obj_t*    root();
    lv_group_t*  inputGroup();                       // was lcdInputGroup()
    void         goHome();                           // was lcdGoHome()
    void         setFullscreen(bool);                // was lcdProgramFullscreen()
    void         setScrollwheelArrows(bool);         // was lcdProgramScrollwheelArrows()
    void         setScrollHandler(lcd_scroll_fn_t);  // was lcdProgramScrollHandler()
    void         setStatusIcon(int area, const char* iconBasename, int state); // §5.3
    void         setRecentsSubtitle(const char* s);  // one line shown on the recents card

    // Resource ledger (the enable_recycle_resource replacement, §4.3)
    lv_timer_t*  timer(lv_timer_cb_t, uint32_t period_ms, void* user = nullptr);
    lv_anim_t*   anim();   // returns a zeroed, tracked lv_anim_t; caller fills + starts it
};

// Registration (was lcdRegister): construct + hand to the shell.
int lcdInstall(LcdApp* app);     // returns id; shell owns lifecycle thereafter
```

`lcdInstall()` mirrors the donor's `installApp()`: it adds the tile to the
launcher page (`Config::launcherPage`) and records the app; the shell builds the
root lazily on first open (`onCreate`), keeps it resident, and may evict
(`onClose` → free root + ledger) under memory pressure, rebuilding on next open.
`Config::name`/`iconBasename` must be static (stored by pointer, as `Entry`
does today).

### 4.3 Resource ledger (the `enable_recycle_resource` replacement)

The donor auto-frees an app's resources on close by **snapshotting and walking
LVGL's private linked lists** — `_lv_ll_get_head(&LV_GC_ROOT(_lv_anim_ll))` for
anims, the display's `screens[]` array for screens, `lv_timer_get_next()` for
timers (`core/esp_brookesia_core_app.cpp` ~line 212). That `_lv_anim_ll`
coupling is the one LVGL-internals dependency in the donor and the most brittle
piece across LVGL 9.x. We do **not** port it. Instead:

- **Objects** free for free: the app owns `root()`; deleting the layer frees the
  whole tree (exactly today's `lv_obj_del()` eviction).
- **Animations & timers** (which live in LVGL globals with no owner) are created
  via `app.anim()` / `app.timer()`, which push into a per-app ledger:

```cpp
struct Ledger {
    std::vector<lv_timer_t*> timers;
    std::vector<lv_anim_t>   anims;   // value-stored; we hold {var, exec_cb} to cancel
};
// onClose runtime (in the shell, after the virtual onClose() returns):
for (auto* t : led.timers) lv_timer_delete(t);
for (auto& a : led.anims)  lv_anim_delete(a.var, a.exec_cb);
led.timers.clear(); led.anims.clear();
```

A few dozen lines; same "closed app can't leak" guarantee; **zero LVGL
internals**. This is the canonical "functionality not code" example. (Existing
apps that create raw `lv_timer_create`/`lv_anim_start` keep working — the ledger
is opt-in; a converted app should migrate its timers/anims to `app.timer()`/
`app.anim()` so eviction reclaims them, but that is per-app, not forced.)

### 4.4 Conversion shape (old → new)

```cpp
// OLD: lcd_apps.cpp
lcdRegister("Log", "log", logFn);            // build in logFn(layer)

// NEW
class LogApp : public LcdApp {
  public: LogApp() : LcdApp({.name="Log", .iconBasename="log"}) {}
  void onCreate(lv_obj_t* root) override { /* build the textview, open ITS log:1 */ }
  void onShow()  override { /* scroll to tail */ }
  void onClose() override { /* close the ITS handle (was the LV_EVENT_DELETE cb) */ }
};
lcdInstall(new LogApp());
```

---

## 5. The phone shell

### 5.1 Manager — the state machine

One foreground app at a time; a stack-free model (Home is the root, apps are
peers, like the donor's MAIN/APP/RECENTS screens). State:

```cpp
enum class Screen { LAUNCHER, APP, RECENTS };
LcdApp*  s_foreground = nullptr;   // null ⟺ LAUNCHER
Screen   s_screen     = Screen::LAUNCHER;
```

The single nav consumer:

```cpp
void shellNavigate(NavIntent intent);   // BACK | HOME | RECENTS
```

- **HOME**: hide `s_foreground` (`onHide`), reveal launcher (the existing slide-up
  animation in `lcdGoHomeInternal`). `lcdGoHome()` / the board's centre-button
  Home path repoint here.
- **BACK**: if a foreground app, call `app->onBack()`; if it returns `false`, do
  HOME. At LAUNCHER/RECENTS, BACK closes RECENTS or is a no-op.
- **RECENTS**: build/show the recents screen (§5.5).
- Opening an app: `openApp(LcdApp*)` → lazy `onCreate` if no root, then `onShow`,
  raise the layer, restore keypad focus (exactly `openEntry()` today).

"Running" apps (the recents set) = apps whose root layer currently exists
(`onCreate` called, `onClose` not yet). No separate bookkeeping — the resident
layer *is* the membership, as today.

### 5.2 Launcher

Paged icon grid. Today: one 4×3 page, 320×216 viewport (status bar reserves
24px), tile 72×64, icon 36×36, 8px pads, bg `0x101418`, tiles in the keypad
focus group (`lcd_launcher.cpp`). The new launcher keeps these as **stylesheet
fields** (§5.6) and adds:

- **Pages**: N screens, each its own flex grid; `Config::launcherPage` places a
  tile. A page-indicator row of dots at the bottom (active dot wider), donor
  style. With ~7 apps one page suffices, but the mechanism is paged from day one.
- **Paging producers**: horizontal trackball-drag on the grid (pointer indev),
  and keypad focus-group navigation that advances to the next/prev page when
  focus leaves the last/first tile (the one spot needing explicit keypad wiring,
  per the donor note). Tap/click opens the app.

### 5.3 Status bar (spangap data sources)

The bar is a **renderer with setters**; the shell subscribes the relevant
storage keys on the lcd task and pushes values in. Setter API (donor shape):

```cpp
void sbSetClock(const char* fmt);            // s.lcd.date_format
void sbSetWifi(int bars /*0..3, -1=off*/);   // wifi.sta.state + wifi.sta.rssi
void sbSetUpstream(bool reachable);          // wifi.sta.up  (NEW element)
void sbSetBattery(int percent, bool charge); // battery.percent (+ charge flag)
void sbSetVisualMode(SbMode);                // HIDE | SHOW_FIXED  (immersive apps)
int  sbAddAppIcon(int area, const char* iconBasename); // LcdApp::setStatusIcon
void sbSetAppIconState(int id, int state);
void sbRemoveAppIcon(int id);
```

| Element | Source key(s) | Phase-1 backing |
|---|---|---|
| Clock | `s.lcd.date_format` | existing label, 1 Hz / minute-aligned |
| WiFi | `wifi.sta.state`, `wifi.sta.rssi` | bars 0..3 by RSSI (replaces the opacity hack) |
| **Upstream** | `wifi.sta.up` | **new** glyph: lit when internet-reachable, dim when AP-only/down — the wifi-associated-vs-upstream distinction (`net.h NET_EV_UPSTREAM_UP` ≡ `wifi.sta.up` for subscribers) |
| Battery | `battery.percent` (+ charge flag) | hidden until a board publishes it; red ≤12% (as today) |
| App icons | `LcdApp::setStatusIcon` | 3 alignment areas, multi-state |

Backing is spangap's existing cheap glyph approach (LVGL symbol fonts / recolor)
behind the setter API, so phase 1 needs **no new multi-frame icon-set assets**;
swapping to donor-style `battery_levelN`/`wifi_levelN` bitmap sets is a later,
asset-only drop-in (Effort 3) behind the same setters. Bar height stays 24px (a
stylesheet field; denser than the donor's 36 to preserve app rows on 240px).

### 5.4 Navigation (committed for the T-Deck)

One abstraction — `NavIntent { BACK, HOME, RECENTS }` → `shellNavigate()`; the
producers are board-specific. The T-Deck's actual input HAL
(`tdeck_lcd.cpp`) dictates the wiring — **decided, no remaining fork**:

- **HOME** — the board's **centre-button hold** already calls `lcdGoHome()` and
  owns the standby tiers; we leave that hardware path untouched and repoint its
  target to `shellNavigate(HOME)`. *Plus* a trackball **gesture**: pointer drag
  up from the bottom, released in the top quarter (the existing home-bar drag on
  `lv_layer_top`).
- **BACK** — the keyboard exposes **`LV_KEY_ESC`** (`mapAsciiKey` 27 → ESC). The
  shell installs a top-level key handler: an ESC not consumed by a focused
  editing widget → `shellNavigate(BACK)`. No new hardware.
- **RECENTS** — there is **no hardware Recents key**, so the **gesture bar
  produces it**: generalize the existing home-bar drag — release-in-top-quarter =
  HOME (today's behavior), drag-up-and-**dwell** (hold >400ms near the top before
  release) = RECENTS. Reuses the implemented drag widget; no new key. The
  tappable **nav bar** (Back/Home/Recents) is the *fallback* producer: default
  **hidden** on the T-Deck (gesture+ESC+button cover everything), opt-in per app
  via `Config::navBar`, and the *required* producer on a future board with
  neither a pointer indev nor an ESC key.

Gesture thresholds (donor 320×240, reused): vertical swipe 50px, edge band 20px,
direction angle 60°, "short" 800ms, "slow" 0.1px/ms, detect period 20ms. The
donor's "auto-disable recents when gesture is off" rule is **dropped** — on a
gesture-less board we wire nav-bar/keypad recents instead of losing the switcher.

### 5.5 Recents / app switcher (cards, not snapshots — committed)

App-switcher screen built lazily, populated from the running set (§5.1). **Each
card is icon + app name + an optional one-line subtitle** (`LcdApp::
setRecentsSubtitle`, e.g. LXMF's current contact, Maps' coordinates) — **not** a
PSRAM screenshot thumbnail. Rationale: on 320×240 a thumbnail is barely legible
and the donor's `lv_snapshot_take_to_buf` holds a full-screen RGB buffer per app
in PSRAM (≈150KB each on this panel) — scarce, and illegible payoff. Cards cost
an icon + two labels.

- **Layout**: horizontal row of cards, ~60% screen width each, swipe/drag to
  page through them (pointer) or focus-group left/right (keypad).
- **Close an app**: swipe a card up past a 30px/60° threshold (donor), or
  trackball-click an explicit ✕ — calls `app->onClose()`, frees root + ledger,
  drops it from the running set.
- **Memory label**: keep the donor's RAM readout (internal/external free/total) —
  a genuinely useful on-device diagnostic. Driven by `heap_caps_get_*`.

### 5.6 Stylesheet & theming (as data)

Keep the donor's **stylesheet-as-data** mechanism — the bone most worth taking —
but seeded with spangap's *current* pixel values so the new shell visually
matches today, just data-driven instead of `#define`d magic numbers.

**Shape.** One nested struct per (name, screen_size), assembled from sub-structs
per widget. `StyleSize` carries px *or* percent (`enable_*_percent`, plus
`enable_square`); fonts are `FONT_HEIGHT_PERCENT(n)` *or* `FONT_SIZE(px)`. At
`begin()` the shell auto-selects the registered sheet matching the real panel
(`getStylesheet(display_size)`), else the built-in `default`, then **calibrates**
(resolves percent→px, runs sanity cross-checks).

**The 320×240 dark sheet (concrete, from current spangap values):**

```
core:        screen 320×240; bg 0x101418; default font lv_font_montserrat_12_latin;
             title font lv_font_montserrat_16_latin; max resident apps 4
status_bar:  height 24px; bg 0x0A2342; text montserrat_12 white;
             clock left; right cluster = upstream, wifi, battery; 3 app-icon areas
launcher:    page grid 4 cols × 3 rows; tile 72×64; icon 36×36; pad 8 (top/left/row/col);
             page-indicator dots at bottom (8px, active 20px)
nav_bar:     height 28px; 3 buttons (back/home/recents 24×24); default mode HIDE on T-Deck
recents:     card 60% × auto; icon 36×36; title montserrat_12; subtitle montserrat_12 grey;
             memory label montserrat_12; swipe-close 30px / 60°
gesture:     vertical 50px; edge 20px; angle 60°; short 800ms; slow 0.1px/ms; period 20ms;
             recents dwell 400ms
```

Fonts in play, all spangap bitmap fonts already in `lcd.h`:
`lv_font_montserrat_12_latin` (labels/clock/status), `…_16_latin` (titles),
`lv_font_spleen_5x8` (Log/CLI mono), `lv_font_tomthumb_4x6`,
`lv_font_micro_2x3`. Icons: `/fixed/lcd/icons/<res>/<name>.bin` (launcher res is
a sheet field — today 36×36; reconcile against the `s.lcd.icon_res` default
during implementation, it is a data value either way). **Ship the 320×240 dark
sheet only**; a per-board sheet (Heltec, Seeed) is later, as *data*; light/named
variants are supported by the mechanism if ever wanted. Per-size **bitmap
assets** are the real per-resolution cost — automating them is Effort 3 (§10);
until then the 320×240 asset set is hand-supplied (it already exists).

---

## 6. Build-alongside, file layout, and the flag

### 6.1 The flag

`CONFIG_LCD_PHONE` (bool, default **n** until §7 flip) selects the new shell vs
the legacy launcher. Both compile; the dispatch in `lcd.cpp` branches once at
`lcdInit()`. New shell sources live under `esp-idf/src/lcd_ui/shell/`; the legacy
`lcd_launcher.cpp` stays untouched until the flip, so a regression is one Kconfig
toggle away. The public `lcd.h` keeps every retained symbol; the new `LcdApp` /
`lcdInstall` go in a new `lcd_app.h` so legacy and new can coexist during
conversion.

### 6.2 Module layout

```
esp-idf/include/lcd.h          retained surface (init, run, input HAL, text view,
                               term, settings builders, fonts, display/standby)
esp-idf/include/lcd_app.h      NEW: LcdApp, LcdApp::Config, lcdInstall, NavIntent
esp-idf/src/lcd_ui/
  lcd.cpp, lcd_lvgl.cpp, lcd_panel.cpp, lcd_icons.cpp, lv_mem_spangap.cpp   (retained)
  lcd_launcher.cpp             (legacy; compiled when !CONFIG_LCD_PHONE)
  lcd_settings.cpp             (RETAINED unchanged — SettingsApp hosts it)
  lcd_textview.cpp, lcd_term.cpp  (retained — Log/CLI reuse)
  shell/                       NEW (compiled when CONFIG_LCD_PHONE):
    lcd_app.cpp, manager.cpp, launcher.cpp, statusbar.cpp, nav.cpp,
    recents.cpp, stylesheet.cpp, stylesheet_320x240.cpp
  apps/                        NEW: log_app.cpp, cli_app.cpp, settings_app.cpp
```

### 6.3 What stays identical

`lcdInit()` is still the single `init:` hook; the lcd task, `lcdRun` aux ports
(10/11), the icon loader task + `D:` FS driver, the input HAL contract
(`lcd_input.h`), the text-view and libvterm term backends, and standby ownership
(`sys.standby`, `lcdScreenSleep/Wake`) are **unchanged**. The board HAL
(`tdeck_lcd.cpp`) needs **no edit** — its `lcdGoHome()`/`lcdScroll()`/
`lcdInputGroup()`/`lcdScrollwheelArrowsActive()` calls all remain valid surface.

### 6.4 Allocator stance (resolved for Effort 1)

No new global `operator new`/PMR in Effort 1. LVGL already routes to PSRAM via
`lv_malloc_core → gp_alloc` (`lv_mem_spangap.cpp`, `CONFIG_LV_USE_CUSTOM_MALLOC`).
The shell's STL (`std::vector<LcdApp*>`, `std::string` labels) is the same modest
STL the current `lcd_launcher.cpp`/`lcd_settings.cpp` already use on the lcd
task — accepted, small, not in a hot path. Choosing **cards over snapshots**
(§5.5) removes the one large per-app allocation, so there is no new PSRAM
pressure to engineer. Add a **DRAM-before/after measurement** checkpoint at the
§7.1 milestone (watch WiFi static-RX + SD-DMA internal-pool headroom); only if it
regresses do we revisit a global PSRAM `operator new`. The donor's
`ESP_BROOKESIA_MEMORY_*` seam is not needed since we inherit spangap's routing.

---

## 7. Effort-1 phasing (phone UI only; each milestone builds & flashes on T-Deck)

1. **Shell skeleton** behind `CONFIG_LCD_PHONE`. `LcdApp` base + `lcd_app.h` +
   resource ledger (§4); manager state machine + launcher (paged) + 320×240
   stylesheet on spangap fonts/icons (§5.1–5.2, §5.6). Status bar shows clock
   only. **Port Log** as the first `LcdApp`. T-Deck only; legacy still default.
   **Checkpoint:** DRAM free before/after vs legacy (§6.4).
2. **Status bar wiring**: wifi (bars) / **upstream** (`wifi.sta.up`) / battery /
   clock from spangap keys; per-app `setStatusIcon` (§5.3). **Convert CLI**
   (scrollwheel-arrows + input group + term backend).
3. **Navigation + recents**: the committed T-Deck nav model — centre-button
   HOME, ESC BACK, gesture-bar HOME/RECENTS, optional nav-bar (§5.4); recents
   screen as cards + memory label (§5.5); launcher paging keypad wiring.
4. **Convert the Settings app**: `SettingsApp` wraps the **existing**
   `lcd_settings.cpp` page-stack unchanged (§3, §8). Every other straddle's
   `*LcdRegister` pane hook keeps registering into the same registry — **no pane
   edits**. Net/T-Deck/Internet/Mesh panes must render identically to today.
5. **Convert the remaining apps** (§8.1) to `LcdApp`: Info (viewer), LXMF, Maps,
   Nomad — each in its straddle's `conditional/spangap-lcd/`. Subsume
   scroll/fullscreen/focus into `LcdApp` methods; migrate per-app timers/anims to
   the ledger.
6. **Touchless & polish**: standby integration (already via `sys.standby` — verify
   under the shell), immersive/fullscreen apps (LXMF thread, Info), cursor-scroll
   edge-pan affordances, recents close/evict under memory pressure.
7. **Flip the default**: `CONFIG_LCD_PHONE=y`; delete `lcd_launcher.cpp` and the
   subsumed `lcd.h` surface that `LcdApp` replaced (`lcdRegister`,
   `lcdShowProgram`, `lcdProgramFullscreen`, `lcdProgramScrollwheelArrows`,
   `lcdProgramScrollHandler`). **Keep** `lcdInit`/`lcdRun`/`ON_LCD`/`lcd_input.h`/
   display + standby ownership / text-view / term / `lcdSetting*` — **and the
   board-HAL surface** the T-Deck input layer calls directly: `lcdGoHome`,
   `lcdAtLauncher`, `lcdScroll`, `lcdScrollwheelArrowsActive`, `lcdInputGroup`,
   `lcdNotifyActivity`, `lcdPointerSetVisibleMs`, `lcdTouchSetMultipoint`,
   `lcdSetHasKeyboard`, `lcdSetInput`, `lcdDisplaySize`, `lcdScreenSleep/Wake`.
   These keep their names and just delegate to the shell internally, so
   `tdeck_lcd.cpp` needs **no edit** (§6.3). `lcdGoHome` becomes a thin
   `shellNavigate(HOME)`; `lcdAtLauncher` reads the manager's `Screen`.
8. **Docs**: rewrite `spangap-lcd/README.md` + `INTERNALS.md` to the app-object
   model; note in `apps_and_settings.md` that LCD programs are now `LcdApp`
   subclasses (settings registration is **unchanged** until Effort 2).

Effort 1 is done when the T-Deck boots the phone shell by default, all seven apps
run as `LcdApp`s, settings work exactly as before, and the legacy launcher is
gone.

---

## 8. App conversion inventory (Effort 1)

### 8.1 Launcher apps → `LcdApp` subclasses

| App | Owner straddle | Current site | Target | Notes |
|---|---|---|---|---|
| Log | spangap-lcd | `lcd_apps.cpp` | `LogApp` | textview; `onShow`→tail; `onClose`→close `log:1` ITS |
| CLI | spangap-lcd | `lcd_apps.cpp` | `CliApp` | term backend; scrollwheel-arrows; input group; `cli:1` ITS |
| Settings | spangap-lcd | `lcd_settings.cpp` | `SettingsApp` | **hosts the existing pane registry unchanged** |
| Info | viewer | `conditional/spangap-lcd` | `ViewerApp` | `onShow`→home page; fullscreen |
| LXMF | lxmf | `lxmf_lcd.cpp` | `LxmfApp` | immersive thread view (fullscreen) |
| Maps | maps | `maps_lcd.cpp` | `MapsApp` | **custom scroll handler** (canvas pan) via `setScrollHandler` |
| Nomad | nomad | `nomad_lcd.cpp` | `NomadApp` | page client |

`SettingsApp::onCreate` calls into the existing `lcd_settings.cpp` entry point
(the page-stack host) and is otherwise a passthrough — the slash-path registry,
the `lcdSetting*` builders, two-way storage binding, scroll pills, and every
straddle's pane hook are **byte-for-byte the code that ships today**. The only
change is that the host now lives inside an `LcdApp` root instead of a launcher
`Entry` layer.

### 8.2 Settings panes are **not** touched in Effort 1

The ~18 settings panes (ACME, DuckDNS, SSH, UPnP, WiFi, mDNS, WireGuard, Maps,
RNS, LXMF, Nomad, the four RNS interfaces, System, T-Deck, Viewer) keep their
three hand-written copies (LCD pane / browser panel / storage default). Folding
them into `straddle.yaml settings:` is **Effort 2** (§9). This plan deliberately
leaves them alone so the phone-UI work has zero blast radius into 18 straddles.

---

## 9. Effort 2 — manifest-generated settings (DEFERRED; do not start until Effort 1 ships)

Recorded here so the phone-UI work above is built with Effort 2's shape in mind,
but **none of this is implemented in Effort 1**.

**The problem (ACME, today):** a setting exists three times — storage default in
`acme.cpp` (`storageDefault("s.acme.enable",0)`), LCD pane in
`conditional/spangap-lcd/acme_lcd.cpp` (`lcdSettingSwitch(...)`), browser pane in
`AcmePanel.vue` + `modules/acme.ts`. Same keys, same widget intent, three files.

**Declaration in `straddle.yaml`:**

```yaml
settings:
  - menu: Internet/ACME          # canonical path; label defaults to leaf
    when: spangap/spangap-web     # optional whole-pane build gate
    fields:
      - section: ACME
      - switch:   { label: Enable,    key: s.acme.enable, default: 0 }
      - text:     { label: Domain,    key: s.net.dns.fqdn }     # cross-straddle key
      - dropdown: { label: Method,    key: s.acme.method, default: "",
                    options: [ {label: Auto, value: ""},
                               {label: DNS-01, value: DNS-01} ] }
      - custom:   acmeDnsCaption    # escape hatch: hand-written slot
```

Field types map onto today's `lcdSetting*` + browser `Setting*`: `section`,
`caption`, `switch`, `slider`{min,max}, `text`{secret?}, `dropdown`{options},
`value`, `button`{action}, `custom`. Per field: `label`, `key`, optional
`default`/`when`/`secret`.

**Generation (one descriptor → three outputs)**, a new `spangap-inside` staging
pass alongside `spangap_requires.cmake`/`spangapInitStraddles()`:

1. **LCD C++** — per-straddle `<prefix>SettingsRegisterGenerated()` that calls
   the existing `lcdRegisterSettings`/`lcdSetting*` builders (codegen, no runtime
   parser on device). Output only when `CONFIG_SPANGAP_LCD`.
2. **Storage defaults** — `<prefix>StorageDefaultsGenerated()` calling
   `storageDefault()` for keys this straddle **owns**, wired into storage
   bring-up.
3. **Browser** — a generated **TS descriptor** consumed by one generic
   `<GeneratedSettingsPanel>` + automatic `menu.register(...)`; `custom:` →
   straddle-registered component slot.

**Cross-straddle keys & ownership:** panes generate from the pane-owner's
manifest; storage defaults from the **key-owner's** manifest (ACME's pane shows
`s.net.dns.fqdn`, but `spangap-net` declares its default). **Canonical menu
taxonomy:** define one tree in the manifest with a fixed top-level mapping
(`Internet→network`, `Mesh Network→mesh`, `System→system`, board→device) so LCD
and browser agree. **Escape hatch:** `custom:` for computed captions, live data,
the map picker — keep the YAML vocabulary small. **Build integration:** extend
`straddle.schema.json`, validate in `spangap validate`, emit into `staging/`,
wire into the dispatcher/storage-init/SPA bootstrap; regeneration-only (no
migrations — zero users). Open Effort-2 decisions: data-driven browser descriptor
vs generated `.vue` (recommend descriptor); the canonical taxonomy table;
secrets marked write-only in the browser descriptor; hold schema scope.

**Effort-2 conversion target (§8.2 panes):** ~18 panes × 3 sites collapse to ~18
manifest blocks (+ a few `custom:` slots); delete the hand-written trios.

---

## 10. Effort 3 — resolution-aware asset pipeline (DEFERRED; independent)

The per-size **bitmap** cost is the thing to automate (layout already reflows via
percent; assets do not). Extend the build to treat **SVG (icons, wallpaper) and
TTF (fonts) as the source of truth** and rasterize each to exactly the
resolutions the *registered stylesheets* require — generalizing what spangap does
piecemeal (`lcd-icons.py` SVG→RGB565; `gen-*-font.py` BDF→C font) into one
resolution-aware pass. Declaring vector/TTF sources + a sheet's target sizes →
build emits the `.bin` icons, bitmap fonts, and wallpapers for every resolution.
This makes adding a board (a new `screen_size`) a *data* change and is the
asset-side analog of Effort 2 (source-of-truth + target set → generated
artifacts). Sequence any time after Effort 1; until then the 320×240 asset set is
hand-supplied (it already exists).

---

## 11. Risks & decisions

**Resolved for Effort 1:**

- **Recents on 320×240** → **icon+title cards, no snapshots** (§5.5). Eliminates
  the per-app PSRAM screenshot buffer; keeps the memory label.
- **Navigation on the T-Deck** → **committed** (§5.4): centre-button HOME (board
  HAL, untouched), keyboard ESC BACK, gesture-bar HOME/RECENTS, nav-bar as opt-in
  fallback. Grounded in the real `tdeck_lcd.cpp` input HAL — there is no Recents
  key, hence the gesture-dwell producer.
- **`enable_recycle_resource`** → per-app ledger, no LVGL internals (§4.2).
- **PSRAM allocator** → inherit spangap-lcd's existing `gp_alloc`/`lv_malloc`
  PSRAM routing; no new global `operator new`; cards remove the big allocation;
  measure DRAM at §7.1 (§6.4).
- **Stylesheet for the T-Deck** → concrete 320×240 dark sheet seeded from current
  pixel values (§5.6); other boards later as data.
- **Build safety** → `CONFIG_LCD_PHONE` keeps the legacy launcher one toggle away
  through the whole conversion (§6.1).

**Deferred to Effort 2 (settings) — not gating the phone UI:**

- Browser generation: data-driven descriptor vs generated `.vue`.
- Canonical menu taxonomy (decide + record once).
- Secrets handling in the browser descriptor (write-only).
- Schema scope creep — hold the field vocabulary small.

**Standing:**

- **LVGL pin:** spangap-lcd owns the LVGL version; donor is LVGL 9, we are LVGL 9
  — keep the pin stable through the port. Exceptions already on
  (`CONFIG_COMPILER_CXX_EXCEPTIONS=y`); RTTI stays off (no `dynamic_cast` — the
  `LcdApp` hierarchy uses none).
