# spangap/viewer — a micro HTML/Markdown document viewer straddle

> **Renamed `browser` → `viewer`** (avoids confusion with the user's web browser):
> straddle `spangap/viewer`, prefix `viewer`, config `s.viewer.*`, CLI verb
> `viewer`, launcher "Viewer", icon `viewer.svg`, symbols `viewerInit`/
> `viewerLcdRegister`/`viewerFetch`. The framework's `browser:`/`browser_register:`
> manifest keys (the web-UI system) are unchanged. Body below still says "browser"
> in places — read as "viewer".

## Context

The mesh devices (T-Deck on LCD, plus the web UI) need a way to *display* documents — help pages,
READMEs, status/info pages served by other straddles — without a real browser. We want a small
renderer that shows a **subset** of HTML and a **subset** of Markdown, identical on both the LCD and
in the web UI, and that can **grow** its supported markup over time without rework.

No off-the-shelf HTML→LVGL micro-browser exists (LVGL's "Browser" docs are the *reverse* — LVGL→web
via Emscripten). The two hard parts have solid dependency-free C libraries we lean on:
**MD4C** (Markdown→HTML, single .c/.h, no deps, CommonMark) and our **own small HTML-subset
tokenizer**. The design's spine is an **intermediate representation (IR)**: both input formats lower
into one render-oriented tree, and two backends (LVGL / web DOM) walk it — so device and browser
render identically, and adding a tag is "one node type + one case per backend."

Key facts established from the codebase:

- **HTTP client** is not wrapped — ACME/DuckDNS/OTA call IDF's `esp_http_client` directly with
  `crt_bundle_attach = esp_crt_bundle_attach` for TLS (`straddles/acme/esp-idf/src/acme.cpp`,
  `straddles/duckdns/esp-idf/src/duckdns.cpp`). The fetcher is ~40 lines reusing that pattern.
- **`spangap-net`** brings WiFi/TCP; its TLS is server-side (irrelevant). Without net staged there is
  no network stack, so http(s) is gated on it; `file://` is always available.
- **Soft deps** = `additional_installs:` (default-on, droppable via `--without`). There is **no `-net`
  prefix syntax**. Conditional code uses auto-generated `#if CONFIG_SPANGAP_<NAME>` symbols and/or
  `esp-idf/conditional/<straddle>/` source slices compiled only when that straddle is staged.
  `init:` / `browser_register:` hooks take `when: spangap/<dep>` gates.
- **LVGL 9.5.0** is the managed component; **`lv_spangroup`** is available for flowed rich text
  (existing UI uses only plain `lv_label` + manual positioning — terminal/settings in
  `straddles/spangap-lcd/esp-idf/src/lcd_ui/`).
- **LVGL malloc already routes to PSRAM** (`straddles/spangap-lcd/esp-idf/src/lcd_ui/lv_mem_spangap.cpp`),
  so `lv_obj` structs already live in PSRAM, not the scarce internal DRAM.
- **Fonts are an LCD concern**, owned/published by `spangap-lcd`. Existing assets:
  `lv_font_spleen_5x8` (mono, declared in internal `lcd_internal.h`), `lv_font_montserrat_12_latin`,
  plus LVGL built-in `lv_font_montserrat_16`. New fonts are plain `.c` blobs auto-globbed into the lcd
  component; generator at `straddles/spangap-lcd/esp-idf/scripts/gen-text-font.py`.

### Settled design decisions
- One straddle, two backends gated by `CONFIG_SPANGAP_LCD` / `CONFIG_SPANGAP_WEB`; fetcher gated by
  `CONFIG_SPANGAP_NET`; shared parser+IR core always compiled.
- Our own HTML-subset tokenizer (not Gumbo); MD4C for md→html. The subset is the sanitizer
  (`<script>`/event handlers never reach the IR).
- **Emphasis by color, one face**: body = dark grey, **bold = black**, *italic = light grey*; mono via
  lcd's mono font; headings = one larger size. **White background always.**
- Fonts owned by `spangap-lcd`, consumed **by role** via a new `lcdFont(role)` API so a future
  Kconfig "which fonts ship" + fallback cascade lives inside lcd, not in browser.
- **No viewport virtualization.** Render the whole IR tree as widgets — LVGL already clips drawing to
  invalidated areas (off-screen widgets cost ~0), scrolls by offsetting cached child coords (no
  relayout), and area-prunes traversal; widgets already live in PSRAM. A giant doc just gets slow/heavy;
  acceptable. A document/memory cap can be added later *if* a real doc blows the budget — not built now.
- Web backend renders into a **sandboxed `<iframe>`** (no `allow-scripts`) — defense-in-depth atop the
  stripped subset.
- `s.browser.urlbar` (bool) toggles the urlbar; **back button always present** (history stack
  independent of urlbar).
- Links: `file://`, `http://`, `https://`, plus relative resolution against the current location.

---

## Architecture

```
   file:// / http(s)  ──fetch──►  bytes
                                   │
              md ──MD4C──► html ───┤
                                   ▼
                          our subset parser ──► IR tree (PSRAM)
                                                  │
                          ┌───────────────────────┴───────────────┐
                          ▼ CONFIG_SPANGAP_LCD       CONFIG_SPANGAP_WEB ▼
                   LCD backend (LVGL)                    web backend (DOM)
                   whole tree as widgets                 sandboxed <iframe>
```

Markup subset (initial; grows by adding IR node types): headings, paragraphs, emphasis/strong, inline
+ fenced code, ordered/unordered lists, links, thematic break.

---

## Straddle scaffold
- `straddles/browser/straddle.yaml`: `name: spangap/browser`, `prefix: browser`, firmware `esp-idf`,
  browser `browser`. `additional_installs: [spangap/spangap-net]` (soft, default-on, droppable).
  `init:` → `browserInit`, plus `browserNetInit` with `when: spangap/spangap-net`.
  `browser_register:` → `registerBrowser`, plus a `when:`-gated network-UI hook.
  Model on `straddles/duckdns/straddle.yaml` (compact example with `browser_register`).
- `straddles/browser/esp-idf/CMakeLists.txt`: glob `src/*.cpp src/*.c`; `REQUIRES` spangap-core,
  spangap-lcd, and (in the conditional slice) `esp_http_client`. Model on
  `straddles/acme/esp-idf/CMakeLists.txt`.
- `straddles/browser/esp-idf/idf_component.yml`: add MD4C via the component manager if available, else
  vendor `md4c.c/.h` + `md4c-html.c/.h` into `src/third_party/md4c/`.

## Parser + IR core (always compiled)
- **No-backend edge case:** the build can't OR-gate staging, so a profile with browser but neither
  `spangap-lcd` nor `spangap-web` still stages. The core then compiles into unreferenced (linker-GC'd)
  dead code; `browserInit` guards its body with `#if CONFIG_SPANGAP_LCD || CONFIG_SPANGAP_WEB` (legal
  at the C preprocessor layer; undefined presence symbols evaluate to 0) and otherwise no-ops with a
  one-line "no display surface" log. Inert and builds clean — nobody should ship that profile.
- `src/ir.h` — node model (`DOC/BLOCK/HEADING/PARA/LIST/ITEM/RULE/TEXT/LINK/IMAGE/CODE`, `flags` for
  bold/italic/code/heading-level, `text`, `href`, `children`, `next`). Arena/bump allocator in PSRAM;
  freed wholesale per navigation.
- `src/parse_md.cpp` — thin MD4C wrapper: bytes → HTML string (via `md4c-html.h`).
- `src/parse_html.cpp/.h` — our subset tokenizer: HTML string → IR. Whitelist of recognized tags;
  unknown tags/attrs and any `<script>`/`on*` dropped silently. New tag = a new whitelist entry + IR
  node mapping.
- `src/url.cpp/.h` — current-location tracking + per-scheme resolver: `file://` absolute + relative
  (path join against current doc's dir on SD/flash fs); `http(s)://` absolute + RFC-3986 relative
  resolution against the current URL.
- `src/browser.cpp` — entry (`browserInit`), navigation controller, **history stack** (back),
  content-type/extension dispatch (`.md`/`text/markdown` → MD4C; `.html`/`text/html` → tokenizer),
  load→parse→render orchestration, current-location state.

## Network fetcher (`CONFIG_SPANGAP_NET`)
- `esp-idf/conditional/spangap-net/src/fetch.cpp` — compiled **only when net is staged**. Thin
  `esp_http_client` GET: `esp_http_client_config_t` with `crt_bundle_attach = esp_crt_bundle_attach`,
  an `ON_DATA` callback appending to a PSRAM buffer, returning status + content-type. Directly mirrors
  `acmeGet()`/`duckdnsGet()`.
- `browser.cpp` routes http(s) loads through the fetcher under `#if CONFIG_SPANGAP_NET`; with net
  absent, http(s) yields a friendly "file:// only" message and `file://` works unchanged.

## LCD backend (`CONFIG_SPANGAP_LCD`)
- `src/backend_lcd.cpp` — IR → LVGL. Block nodes → flex containers (vertical); inline runs →
  `lv_spangroup` with one span per style change, color = grey/black/light-grey by `flags`; code →
  mono font; headings → larger font. White page bg. **Whole tree built; no virtualization.**
- Reuse existing patterns from `straddles/spangap-lcd/esp-idf/src/lcd_ui/lcd_term.cpp` (label/font
  creation, `lv_font_get_line_height`/`lv_font_get_glyph_width` metrics) and `lcd_settings.cpp`
  (flex rows, heading font).
- Chrome: back button always present; editable urlbar shown/hidden per `s.browser.urlbar`; loading and
  error states (timeout, TLS failure, 4xx/5xx, unsupported-scheme-without-net).

## Fonts in spangap-lcd (modify lcd straddle)
- Add `lcd_font_role_t { LCD_FONT_BODY, LCD_FONT_HEADING, LCD_FONT_MONO }` and
  `const lv_font_t *lcdFont(lcd_font_role_t)` to a **public** header (promote needed externs out of
  `lcd_internal.h` into `lcd.h` or new `lcd_fonts.h`).
- Implementation: a switch returning fixed symbols today — `MONO`→`lv_font_spleen_5x8`,
  `BODY`→`lv_font_montserrat_12_latin`, `HEADING`→a larger size.
- Generate one heading font (e.g. Montserrat ~16–18) via `gen-text-font.py`, drop the `.c` into
  `src/lcd_ui/`, declare it. (Fallback cascade + Kconfig "which fonts ship" deferred — see Out of scope.)

## Web backend (`CONFIG_SPANGAP_WEB`)
- `browser/` (web side): IR → DOM emitter rendering into a **sandboxed `<iframe>`**; `registerBrowser`
  wires the sub-window + back/urlbar chrome via `browser_register:`. Matches LCD subset/styling (same
  three text colors, white bg). Auto npm-linked by the build per the `browser_register` mechanism.

## Config
- `browser` firmware tree + `s.browser` browser tree. Keys: `s.browser.urlbar` (bool). Defaults in the
  owning straddle (no version gates — zero users).

---

## Out of scope (later)
- Images (`lv_image` + decoder + fetch), tables, nested/complex lists, forms/inputs.
- Document/memory cap (only if a real doc is found to exceed the budget — measure first).
- Font selection Kconfig ("which fonts ship") + fallback cascade inside `lcdFont()` (survey LVGL
  ecosystem for subsetting/fallback first).
- Feeding MD4C's SAX callbacks straight into the IR (skip the HTML string) as an optimization.

---

## Suggested build order
1. Scaffold + MD4C wiring; `parse_md` → HTML string; `ir.h` + `parse_html` → IR (unit-testable on host).
2. `url.cpp` (`file://` first), `browser.cpp` orchestration + history.
3. LCD backend (spangroup rendering, fonts via `lcdFont()`), `lcdFont()` + heading font in spangap-lcd.
4. Web backend (IR→DOM, iframe, chrome).
5. Network fetcher + http(s) URL resolution + error/loading states.

---

## Files at a glance

**New — `straddles/browser/`**
- `straddle.yaml`, `esp-idf/CMakeLists.txt`, `esp-idf/idf_component.yml`
- `esp-idf/src/`: `browser.cpp`, `ir.h`, `parse_md.cpp`, `parse_html.cpp/.h`, `url.cpp/.h`,
  `backend_lcd.cpp` (`CONFIG_SPANGAP_LCD`), `third_party/md4c/*` (if vendored)
- `esp-idf/conditional/spangap-net/src/fetch.cpp`
- `browser/` web side: IR→DOM emitter + iframe host + chrome, `package.json`

**Modify — `straddles/spangap-lcd/`**
- Public font header (`lcd.h` or new `lcd_fonts.h`): `lcd_font_role_t` + `lcdFont()`; promote externs.
- `src/lcd_ui/lcdFont` implementation + one new heading font `.c`.

**Reuse (reference, no change)**
- `straddles/acme/esp-idf/src/acme.cpp`, `straddles/duckdns/esp-idf/src/duckdns.cpp` — esp_http_client + crt bundle pattern.
- `straddles/spangap-lcd/esp-idf/src/lcd_ui/lcd_term.cpp`, `lcd_settings.cpp` — label/font/flex patterns + font metrics.
- `straddles/duckdns/straddle.yaml` — compact manifest with `browser_register`.

---

## Implementation status (v0, 2026-06-20)

Built the LCD viewer end-to-end (mirrors maps' structure):
- `straddles/browser/` — `straddle.yaml` (LCD-only; `browser:`/web side deferred), `esp-idf/CMakeLists.txt`,
  `idf_component.yml`, `include/browser.h`, `src/browser.cpp` (no-op `browserInit`).
- **MD4C vendored** at `esp-idf/md4c/src/` (6 files, `-w` like libvterm) — confirmed offline-buildable.
- **Viewer**: `esp-idf/conditional/spangap-lcd/src/browser_lcd.cpp` — IR (blocks+runs) + HTML subset parser
  (h1-6, p, br, hr, ul/ol/li, b/strong, i/em, code, pre, blockquote, a; entities; drops script/style/unknown)
  + MD4C md→html bridge + LVGL render (flex column of `lv_spangroup` + mono labels for `pre`), colour-emphasis
  on `montserrat_12_latin`/`spleen_5x8`, white bg. Launcher program "Browser", CLI verb `browser <path>`,
  Settings pane. `file://`/bare-path loading with md/html sniff + 512KB sanity cap.
- **Icons**: `reticulous/assets/lcd-icons/browser.svg` (new globe) and `maps.svg` (replaced globe → red drop-pin).
- **Fonts**: used lcd's public externs directly; the `lcdFont()` role API was **not** built (fonts already
  "exposed by -lcd"; role/cascade is future per agreement).

### v1 (2026-06-20, after rename to viewer)
- **Renamed** browser→viewer throughout (dir, manifest, files, symbols, CLI verb, launcher, config, icon).
- **Clickable links**: hybrid render — link-free prose stays `lv_spangroup`; link-bearing blocks render as a
  flex-wrap row of word-labels, link words clickable + underlined. History stack + floating **Back** (`<`) button.
- **Relative URL resolution** (`resolveUrl`/`normalizePath`) for `file://`, bare paths, and `http(s)://`.
- **Nav worker task** (`STACK_PSRAM` 24KB): file/http loads run off the lcd task; render hops back via `lcdRun`.
- **http(s) fetch**: `conditional/spangap-net/src/fetch.cpp` (esp_http_client + IDF cert bundle), `#if CONFIG_SPANGAP_NET`,
  soft dep via `additional_installs: [spangap/spangap-net]`. Declared in `include/viewer_net.h`.
- **Heading font**: generated accented `lv_font_montserrat_16_latin` into spangap-lcd (public extern in `lcd.h`);
  headings now larger. (Used externs directly; `lcdFont()` role API still deferred.)
- **Address bar**: `s.viewer.urlbar` toggles an in-app `lv_textarea` (joined to `lcdInputGroup`); off by default.

Deferred: web-UI viewer window — per decision, just render the sanitized IR→HTML with the real browser's fonts
and open http(s) links in a new tab (`target=_blank`); needs study of spangap-web's window API. Plus images/tables.

## Verification

Per build/log conventions (device CLI not reachable in-container; verify via `spangap log`; user drives
on-device interaction):

1. **Build:** `reticulous/reticulous --with spangap/hw-tdeck --with spangap/browser` (from repo root,
   not a sub-straddle). Then build `--without spangap-net` to confirm graceful degrade compiles.
2. **Boot/log:** confirm `browserInit` runs and `browserNetInit` only appears with net staged — check
   `spangap log -f`.
3. **On-device (user-driven):** open `file://` md and html docs; verify subset renders with the three
   text colors on white, headings larger, mono code; click links incl. relative; back button; scroll a
   long doc.
4. **Web parity:** open the browser sub-window; same doc renders identically; iframe sandbox active
   (no script execution).
5. **Network:** load `http://` and `https://` pages; TLS validates; relative URLs resolve per scheme;
   urlbar edit/submit; `s.browser.urlbar=false` hides urlbar but back remains; trigger a bad-host error
   for the error state; confirm `--without spangap-net` build shows the file:// only message.
