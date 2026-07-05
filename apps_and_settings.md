# Apps & Settings — current situation

How apps (web + LCD) and Settings panes register across the spangap/reticulous
platform, the exact facilities each side exposes today, how they talk to the
central facilities, and what is **generated** by the build vs **supplied** by a
straddle.

Scope of the snapshot: the `reticulous/reticulous --with spangap/hw-tdeck
--with spangap/viewer` build (the kitchen-sink T-Deck image). Verified against
the staged source under `/straddles` on 2026-06-21.

---

## 1. The two registration surfaces

There is no single "app" abstraction. A straddle that wants UI plugs into **two
independent registries**, one per side, plus the central storage tree that both
read/write:

| Side | Registry | Unit it adds | Entry point |
|---|---|---|---|
| On-device LCD | `spangap-lcd` launcher + settings tree (in-RAM) | a **program** (launcher tile) and/or a **settings pane** | a firmware `*LcdRegister` / `*LcdInit` hook |
| Browser SPA | `spangap-web` Pinia `menu` store | a **menu leaf** (panel / toggle / action) and/or a **FloatingWindow** | a TS `register*` function |
| Both | `storage` tree (`s.*` config, ephemeral state, `secrets.*`) | config keys + live state | `storageGet/Set/Subscribe` |

Neither side knows about the other. They converge only on the storage tree: a
control on one side writes a key, the device's storage module coalesces and
fans the change out to every subscriber — the other side's control, the owning
task, and the browser store all follow. This is why a switch flipped in the
browser moves the on-device switch and vice-versa, with no app-side wiring.

---

## 2. The wiring contract (manifest → generated dispatch)

Apps do **not** hand-call their init or registration. They **declare** hooks in
`straddle.yaml`; the build generates the dispatchers that call them in
dependency order. Three manifest keys drive this:

```yaml
start:                       # bare-hardware bring-up, before spangapInit()
  - call: tdeckStart
  - call: tdeckLcdStart
    when: spangap/spangap-lcd # hook compiled+called ONLY when that straddle is staged
init:                        # firmware bring-up, after spangapInit()
  - call: viewerInit
  - call: viewerLcdRegister
    when: spangap/spangap-lcd
  - call: viewerWebRegister
    when: spangap/spangap-web
browser_register:            # browser-side UI registration (parallel to init:)
  - call: registerViewer
    module: modules/viewer   # default module path is modules/<call>
```

- **`when:`** gates a hook on another straddle being present. The firmware code
  for a gated hook lives in a **`conditional/<gating-straddle>/`** subtree of the
  straddle's `esp-idf/` — e.g. every LCD pane lives in
  `esp-idf/conditional/spangap-lcd/src/<name>_lcd.cpp`, compiled **only** when
  `spangap-lcd` is staged. The viewer's HTTP fetcher lives in
  `conditional/spangap-net/`, its `.md` transform in `conditional/spangap-web/`.
  No `#if` in the call site — presence is decided at staging time.
- Hook functions use **plain C++ linkage** (`void xLcdRegister(void);`), no
  `extern "C"`, no header wrapping — the generated forward decls assume this.
- `*LcdRegister` is the platform convention for "add my LCD programs/panes";
  `*LcdInit` is used when the hook needs the lcd task already running (e.g.
  T-Deck keyboard indev). Both may register programs/panes; the name is just a
  label in the manifest's `call:`.

### What the build generates (this image)

| Generated file | What it is | What's in it |
|---|---|---|
| `reticulous/esp-idf/staging/spangap_init_dispatch.gen.cpp` | The buildable's **entire** `app_main` + the four phase dispatchers. The buildable ships **no** `main.cpp`. | `spangapStartStraddles()` (tdeckStart, tdeckLcdStart) → `spangapInit()` → `spangapInitStraddles()` (every `init:` hook, platform band — storage/cron/tls/net/ntp/mdns/web/webrtc/lcd — then dep-topo: rnsd, lora, acme, upnp, wg, duckdns, sshd, tcp, auto, espnow, lxmf, nomad, maps, tdeckLcdInit, gps, viewer…) → `spangapPostAppInit()`. Each LCD/web hook (`netLcdRegister`, `rnsdLcdRegister`, …) is interleaved right after its straddle's init. |
| `reticulous/web-interface/src/boot/straddles.gen.ts` | The browser parallel to `spangapInitStraddles()`. | `import { registerX } from '<prefix>/modules/<m>'` for every staged `browser_register:` straddle, and a `registerStraddles()` that calls them in init order: net, rnsd, lora, acme, upnp, wg, duckdns, sshd, tcp, auto, espnow, lxmf, nomad, viewer. |
| `staging/main_requires.cmake`, `components/`, `sdkconfig.spangap-*`, package.json `file:` deps | dep graph + staging | symlinks to each straddle's source, `SPANGAP_REQUIRES`, presence Kconfig, npm `file:` links for browser halves. |

So **the orchestration is generated; the behaviour is supplied.** Adding a
straddle with `init:`/`browser_register:` hooks is enough to wire it into both
dispatchers — no edit to any buildable file.

### App-supplied vs generated, at a glance

- **App-supplied:** every `*LcdRegister`/`*Init` function body, every `register*`
  TS module, every `*Panel.vue` / `*Window.vue`, the `straddle.yaml` hook
  declarations, the config-key schema, the icons.
- **Generated:** `spangap_init_dispatch.gen.cpp` (incl. `app_main`),
  `straddles.gen.ts`, `main_requires.cmake`, the staging tree, the SPA's
  package.json `file:` dep list. Marked `AUTO-GENERATED … Do not edit`.
- **Buildable-supplied (reticulous, hand-written but thin):**
  `web-interface/src/boot/modules.ts` (registers the global `Setting*`
  components, then calls `registerSystem()` → `registerStraddles()` →
  `registerAdvanced()`), `MainLayout.vue` (mounts every FloatingWindow), the
  `data/` LittleFS payload, `assets/lcd-icons/`. It defines **no** menus of its
  own — it is declarative assembly.

---

## 3. LCD side — programs & settings

### Mechanism

`spangap-lcd` owns the display, the single LVGL context, and the lcd task.
Everything that touches LVGL runs on that task; other tasks hop on with
`lcdRun(ON_LCD {…})`. Two things get registered:

- **Programs** — `lcdRegister(name, iconBasename, fn[, onShow])`. `fn(layer)`
  runs once, on the lcd task, the first time the tile is opened; the layer
  persists. Tile icon is `/fixed/lcd/icons/36x36/<basename>.bin`.
- **Settings panes** — `lcdRegisterSettings("Seg/Sub", label, fn[, placement])`.
  Slash-path; intermediate segments auto-become submenus (matched
  **case-sensitively** — convention is First-Letter-Upper). `fn(pane)` builds
  storage-bound rows with the `lcdSetting*` helpers:

  | Helper | Control | Binds |
  |---|---|---|
  | `lcdSettingSection(p,title)` / `lcdSettingCaption(p,text)` | divider / grey note | — |
  | `lcdSettingSwitch(p,label,key)` | toggle | int key 0/1 |
  | `lcdSettingSlider(p,label,key,min,max)` | slider | int key |
  | `lcdSettingText(p,label,key[,secret])` | text (inline edit w/ HW kbd, else on-screen) | string key |
  | `lcdSettingDropdown(p,label,key,csv)` | dropdown | string key |
  | `lcdSettingValue(p,label,key)` | read-only, live (event-driven) | string key |
  | `lcdSettingButton(p,label,onClick)` | action button | — |

  Every bound control is **two-way subscribed** to its key; writes apply
  immediately (no "save"). Keys are stored **by pointer** → must be string
  literals/static.

The lcd module ships three programs of its own — **Settings** (gear),
**Log**, **CLI** (the last two are ITS clients to the `log:1`/`cli:1` DC ports)
— and the gear's nested menu. The platform's first non-core pane is **Net/WiFi**
from `spangap-net`.

### Programs registered (this image)

| Program | Icon | Straddle | What it is |
|---|---|---|---|
| Settings | `gear` | spangap-lcd | the settings menu tree (built-in) |
| Log | `log` | spangap-lcd | scrollback of the device log (ITS client → `log:1`, `{"ansi":0}`) |
| CLI | `cli` | spangap-lcd | one-line command box (ITS client → `cli:1`, LINE mode) |
| Info | `viewer` | viewer | HTML/Markdown document viewer; `onShow` → `s.viewer.home_lcd`, boots `s.viewer.once_lcd` once |
| Maps | `maps` | maps | offline slippy-map of SD tiles, follows GPS; worker task owns tile cache |
| LXMF | `rns` | lxmf | LXMF messenger: identity picker → contact list → chat thread |
| Nomad | `nomad` | nomad | Nomad Network page browser (Micron→LVGL); LCD owns session 6 |

(rns/iface-*/wg/upnp/acme/duckdns/sshd/net register **panes only**, no program.)

### Settings tree (LCD, this image)

Root order is by placement (`System`=1, `T-Deck`=1, then alphabetic). Submenu
segments merge by exact id.

```
Settings (gear)
├─ System                         (spangap-net netLcdRegister, placement 1)
│   Hostname=s.net.hostname · Timezone=s.ntp.tz · NTP Server=s.ntp.server
├─ T-Deck                         (hw-tdeck tdeckLcdRegister, placement 1)
│   GPS model/touch (value) · GPS Enable=s.gps.enable, Interval=s.gps.interval, Status=gps.state
│   Trackball: speed=s.tdeck.trackball_speed, dwell=s.tdeck.pointer_visible_time
│   Display: Backlight=s.lcd.backlight, Sleep=s.lcd.inactivity_timeout
├─ Internet                       (submenu)
│   ├─ WiFi      (spangap-net) Enable=s.net.wifi.enable; live wifi.sta.{state,ssid,ip,rssi};
│   │            AP ssid/pass/ip/mask = s.net.wifi.ap.*
│   ├─ mDNS      (spangap-net) Enable=s.net.mdns_enable; http/https port = s.net.mdns.{http,https}
│   ├─ SSH       (sshd)       Enabled=s.sshd.enabled  (live start/stop)
│   ├─ UPnP      (upnp)       Enable=s.upnp.enable; Ext port=s.upnp.ext_port
│   ├─ WireGuard (wg)         Our side addr/netmask/dns/keepalive=s.wg.*; pubkey (value);
│   │            "Generate key" button → set wg.keygen=1; Other side endpoint/peer_pubkey
│   ├─ DuckDNS   (duckdns)    Subdomain=s.duckdns.domain; Token=s.duckdns.token (secret)
│   └─ ACME      (acme)       Enable=s.acme.enable; Domain=s.net.dns.fqdn; Method=s.acme.method; Directory=s.acme.url
├─ Mesh Network                   (submenu)
│   ├─ General        (rns,  placement 1) "Reticulum / RNS": identity (value), Enable=s.rnsd.enable,
│   │                 Transport=s.rnsd.transport_enabled, name, announce interval, path max/ttl
│   ├─ LXMF Messages  (lxmf, placement 2) re-announce interval, catalogue cap, per-identity
│   │                 enable/destroy, add/import identity
│   ├─ Nomad          (nomad) admin pane (when present)
│   └─ RNS Interfaces (submenu)
│       ├─ AutoInterface (iface-auto)  Enable=s.auto.enable, Group, Mode; live auto.{state,peers,addr}
│       ├─ ESPnow        (iface-espnow) Enable=s.espnow.enable, channel, rate, conflict policy; live espnow.*
│       ├─ LoRa          (iface-lora)  Enable=s.lora.0.enable; freq/bw/sf/cr/power/preamble/sync/mode;
│       │                live lora.0.{state,chip,bitrate_eff,stats.rssi_last}  (radio 0 only)
│       └─ TCP           (iface-tcp)   live tcp.peers.0..3.state + "edit peers in web UI" note
├─ Maps                           (maps) Zoom=s.maps.zoom; Status=maps.state; Tile dir=s.maps.tiledir
└─ Viewer                         (viewer) caption + Location=viewer.lcd.url
```

LCD config keys owned by the launcher itself: `s.lcd.backlight`,
`s.lcd.date_format` (live; both sync to browser).

---

## 4. Web side — menus, panels & windows

### Mechanism

`spangap-web` is the npm package `spangap-browser`: it exports the SPA shell
(MenuBar, SettingsPanel drawer, FloatingWindow, Log/CLI windows, WebRTC session,
auth, login/setup pages) and the `Setting*` components. The consuming Quasar app
(`reticulous/web-interface`) assembles them.

UI registers through a Pinia **menu store** (`stores/menu.ts`):

```ts
menu.register(path, label, leaf, opts)   // leaf: {type:'panel',component}
                                          //     | {type:'toggle',key}
                                          //     | {type:'action',action}
menu.setMenu(path, {label, placement, hidden})   // name/sort a container
```

- Path is slash-separated: **first segment = top-level menu-bar group**, middle =
  submenus (auto-created, title-cased, merged by id), last = leaf. This mirrors
  the LCD `lcdRegisterSettings` path scheme exactly.
- `placement`: `>0` left/top block ascending, `0` middle alphabetic, `<0`
  right/bottom block ascending.
- Panels (`*Panel.vue`) build rows from `SettingToggle / SettingSlider /
  SettingSelect / SettingText / PanelHeading`, each bound via
  `device.get("s.…")` / `device.set("s.…", v)` against the Pinia device store,
  which syncs bidirectionally with the device storage tree over the `storage:1`
  DataChannel on the shared WebRTC session.
- FloatingWindows are not menu items — they are components mounted in
  `MainLayout.vue`, toggled by exported reactive `*Visible` refs, raised by
  `*Focus` nonces. A `window/*` menu **action** flips the ref.

### Menu-bar structure (this image)

```
<progName>   ← MenuBar app dropdown (s.sys.progname / s.sys.project); also opens About (hidden)
Settings  (placement 1)
├─ System                  (spangap-web SystemPanel)  hostname, NTP server, timezone(IANA)
├─ Internet                (submenu, spangap-net, placement 2)
│   ├─ WiFi  (NetworkPanel)  STA enable + known nets s.net.wifi.nets[], AP s.net.wifi.ap.*;
│   │        live wifi.sta.* / wifi.ap.*; WifiScanDialog
│   ├─ mDNS  (MdnsPanel)     s.net.mdns_enable, s.net.mdns.{http,https}
│   ├─ ACME  (AcmePanel)     s.acme.{enable,method,url}, s.net.dns.fqdn
│   ├─ DuckDNS (DuckDnsPanel) s.duckdns.{domain,token}
│   ├─ SSH   (SshdPanel)     s.sshd.enabled + s.sshd.authorized_keys[]
│   ├─ UPnP  (UpnpPanel)     s.upnp.{enable,ext_port}
│   └─ WireGuard (WireGuardPanel) s.wg.* + wg.keygen trigger
└─ Mesh Network            (submenu, rns, placement 3)
    ├─ Reticulum / RNS  (RnsdPanel, placement 1)  s.rnsd.*
    ├─ RNS Interfaces   (submenu, placement 2)
    │   ├─ AutoInterface (AutoPanel)  s.auto.* + secrets.auto.ifac_netkey; live auto.*
    │   ├─ ESPnow        (EspnowPanel) s.espnow.* + secrets.espnow.ifac_netkey
    │   ├─ LoRa          (LoraPanel)   s.lora.0.* + secrets.lora.0.ifac_netkey
    │   └─ TCP           (TcpPanel)    s.tcp.peers[] + s.tcp.listen.* + per-peer secrets
    ├─ LXMF Messages   (LxmfPanel, placement 3)   s.lxmf.{announce_interval_s,max_announces}, per-id config
    └─ Nomad Network   (NomadPanel, placement 4)  s.nomad.max_nodes, bookmarks
Window  (placement 4)
├─ CLI         → action showCli()      (TerminalWindow, cli:1 DC)
├─ System Log  → action showLog()      (LogWindow, log:1 DC)
└─ Viewer      → action showViewer()   (ViewerWindow, iframe)
LXMF Messages  (placement 2)   dynamic: one action per usable identity (lxmf/id-<n>), or single lxmf/messages
Nomad Browser  (placement 3)   nomad/browser → action (NomadWindow)
Status                          status/announces → "Announces" action (lxmf AnnouncesWindow)
```

> **Currently hidden behind `#if 0` / `if(false)` (code kept, not registered):**
> rns `status/nodes` "Show Nodes" + `status/map` "Show Map" (the `NodesWindow` /
> `MapWindow` components are still *mounted* in MainLayout and toggled by
> `nodesVisible`/`mapVisible`, but no menu item flips them); spangap-web's
> `advanced/backlog`, `advanced/edit/*`, and the `Developer Options`
> (`DeveloperPanel`). spangap-web's `About` lives in a **hidden** `app/about`
> group opened by id from the MenuBar. OTA's browser panel exists in the `ota`
> straddle but OTA is **not staged** in this build.

### FloatingWindows mounted by MainLayout

| Window | Component (origin) | Toggle ref | DataChannel |
|---|---|---|---|
| CLI | `TerminalWindow` (spangap-browser) | `cliVisible`/`cliFocus` | `cli:1` |
| System Log | `LogWindow` (spangap-browser) | `logVisible`/`logFocus` | `log:1` |
| Reticulum Nodes | `NodesWindow` (rns) | `nodesVisible` | (no menu trigger now) |
| Reticulum Map | `MapWindow` (rns) | `mapVisible` | (no menu trigger now) |
| LXMF Messages | `MessagesWindow` (lxmf) ×N, one per identity | `messagesVisibleById[n]` | — |
| Announces | `AnnouncesWindow` (lxmf) | `announcesVisible` | — |
| Nomad Browser | `NomadWindow` (nomad) | `nomadVisible`/`nomadFocus` | — |
| Viewer | `ViewerWindow` (viewer) | `viewerWebVisible`/`viewerWebFocus` | iframe (no DC) |

---

## 5. How a straddle interacts with the central facilities

The same pattern repeats on both sides; the central facilities are owned by
spangap-core / spangap-net / spangap-web tasks, and apps reach them by **storage
keys** and **ITS** rather than direct calls.

- **Storage tree** is the universal bus. `s.*` = persisted config (synced to
  browser), `secrets.*` = persisted but never leaves the device, no-prefix keys
  = ephemeral live state (e.g. `wifi.sta.ip`, `lora.0.state`, `gps.lat`).
  Controls bind to keys; owning tasks `storageSubscribeChanges` and react.
  **Command sentinels** are ephemeral keys written to trigger one-shot actions
  (`wg.keygen=1`, `lxmf.cmd.identity_new`, `nomad.cmd.go`, `ota.check`).
- **Config sync (browser↔device):** the Pinia device store mirrors the storage
  tree over the `storage:1` DC; the device sends a full dump on open then nested
  merge-patches; the browser sends patches back. `sys.build_time` change →
  browser auto-reload.
- **ITS** is task-to-task transport on-device. The web task forwards matching
  HTTP/WS connections to the owning task via ITS (`web_path_msg_t` /
  `net_port_msg_t` registered during init); the LCD Log/CLI programs are ITS
  *clients* of the `log:1`/`cli:1` DC ports; `lcdRun`/`lcdRegister` are ITS aux
  messages onto the lcd task.
- **WebRTC** is one shared `RTCPeerConnection` (signaled over `/webrtc`);
  every browser consumer builds its own DataChannel on it (`storage:1`,
  `log:1`, `cli:1`, app video/data channels).
- **LCD live values** are pure storage subscriptions — the status-bar WiFi glyph
  follows `wifi.sta.*`, `lcdSettingValue` rows follow their key, the clock
  follows `s.lcd.date_format` — no polling, no net calls.

---

## 6. One straddle, both sides — the viewer as the canonical example

`viewer` shows every mechanism in one package:

- `init: viewerInit` (always) — registers the `webview` CLI verb.
- `init: viewerLcdRegister when: spangap-lcd` — the **Info** LCD program +
  **Viewer** pane (code in `conditional/spangap-lcd/`).
- `init: viewerWebRegister when: spangap-web` — registers a `.md`→HTML
  file-extension transform with the web server (code in
  `conditional/spangap-web/`), so headless builds still serve rendered docs that
  the LCD viewer fetches over loopback.
- `browser_register: registerViewer` — the **Window → Viewer** menu action +
  `ViewerWindow.vue` (an `<iframe>` onto the device's own web server) +
  `viewerWebVisible`/`viewerWebFocus` refs that `MainLayout.vue` binds.
- Shared state: `s.viewer.{home_lcd,once_lcd,home_web,once_web}` config, and
  ephemeral `viewer.lcd.url` / `viewer.web.url` so each side tracks its own
  location and the CLI/firmware can pop the right window open.

The build stages whichever halves apply, generates the two dispatchers that call
the hooks, npm-links the browser half, and the viewer is live on every surface
present — with no edit to the buildable.
