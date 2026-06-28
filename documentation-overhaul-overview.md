# Documentation overhaul — per-straddle overview (for review)

Compiled from a per-straddle audit against the standard in
[`documentation-overhaul.md`](documentation-overhaul.md). `rns` is the reference
end-state. This is a **decision map**, not the edits themselves — it focuses on
the cross-cutting calls to make first, then the main per-straddle items.

Nothing here is verified beyond the agents' grep-checks; treat the specific keys/
defaults as "to confirm in the edit pass," but the structural and naming decisions
are ready to settle.

---

## Part A — Cross-cutting decisions (settle these first; they cascade)

### A1. "transport" → "interface" (platform-wide rename)
The `iface-*` family, `rns`, and `reticulous` all still call the packet-pipe
straddles **transports**, which collides with Reticulum's core **Transport**
(routing/path engine). The interfaces *register with* Transport.

- Rename the role word **transport → interface** in prose across `iface-auto`,
  `iface-espnow`, `iface-lora`, `iface-tcp`, `rns`, `reticulous`.
- The port symbol is **`RNSD_PORT_IFACE`** carrying **`rnsd_iface_t`**. The tokens
  `RNSD_PORT_TRANSPORT` and `RNSD_PORT_REGISTER` appear in docs/comments/yaml of
  rns, all four ifaces, and hw-tdeck — **none exist in code**. Sweep them.
- Interface registration naming is `tcp/0`, `lora/0`, `auto`, `espnow` — not the
  invented `tcp.<host>:<port>` / `autoif.<hash>` / `lora.<freq>.<sf>.<bw>` forms.
- Delete the dead empty dirs `/straddles/tr-{tcp,lora,auto,espnow}` (old "tr-"
  = transport naming). No `tr-*` tokens survive in code — clean to remove.

**Decision needed:** confirm "interface" as the platform-wide term and approve the
`RNSD_PORT_IFACE` token sweep (touches rns + 4 ifaces + reticulous + hw-tdeck).

### A2. The two big "docs living in the wrong straddle" piles
The hard rule "a straddle's docs live in that straddle" is violated wholesale in
two places. **Each owning straddle already has its own README+INTERNALS**, so most
of these are stale duplicates to fold-and-delete (coverage-audit first).

**`hw-tdeck/docs/` — move out, keep only board docs:**

| file | → target | note |
|---|---|---|
| `auto.md` | iface-auto | richer than the straddle's own README; fold + delete |
| `lora.md` | iface-lora | ~90% of iface-lora's real INTERNALS lives here |
| `tcp.md` | iface-tcp | stale (Phase-1) but has durable framing/topology |
| `lxmf.md`, `internals/lxmf.md` | lxmf | full schema + wire codec |
| `nomad.md` | nomad | 19 KB, saturated with dated prose |
| `maps.md` | maps | documents the *old* bake pipeline; salvage little |
| `component-plan.md` (1548 ln) | reticulous (as new INTERNALS) | de-phase; it's the project architecture |
| `testing.md` | reticulous | Python RNS/LXMF host harness |
| `tdeck.md`, `gps.md` | **stay** (board) | trim; add `rtc.md`/battery/audio coverage |

**`spangap-core/docs/` — move out, keep only core-platform docs:**

| file | → target | note |
|---|---|---|
| `lcd.md` (660 ln) | spangap-lcd | two refactors stale (`lcdSetBoard`, `lcdRegister`) |
| `web.md`, `web-interface.md`, `webrtc.md` | spangap-web | |
| `tls.md` | spangap-net (tls.cpp lives there) | |
| `ntp.md` | spangap-net | stale API (`ntpBegin`/`waitForTime` gone) |
| `remote-access.md` | split: keys → owners (acme/duckdns/upnp/net) | keep a thin cross-cutting trio overview |
| `wireguard.md` | wg | references a core path that no longer exists |
| `ota.md` | updater / retire (see A5) | self-marked "Superseded" |
| `development.md`, `getting-started.md` | `/straddles/spangap` build-system | reconcile with its CLAUDE.md/INTERNALS |
| `its.md`, `storage.md`, `fs.md`(rename from `unified-fs-api.md`), `logging.md`, `cron.md`, `power-management.md`, `memory.md`, `auth.md`, `idf-tweaks.md`, `flash-partitions.md`, `key-fixes.md` | **stay** (core) | |

**Decision needed:** approve the move-targets above, and confirm `flash-partitions.md`
and the slimmed `remote-access.md` stay as **cross-cutting** docs in core (the
standard permits multi-straddle architecture docs to live outside any one straddle).

### A3. Structure: which straddles are "multi-function" (README + docs/<fn>.md, no INTERNALS.md)
The standard splits large multi-function `spangap-*` straddles into a thin README +
`docs/<function>.md` (each ending in `## Internals`), with **no separate INTERNALS.md**.
Several straddles are the wrong shape today:

- **spangap-core** — has *both* INTERNALS.md and docs/; should be README+docs/ only
  (delete INTERNALS.md, distribute into per-function `## Internals`).
- **spangap-web** — multi-function (web/auth/webrtc/browser-shell) but mono-file;
  needs a `docs/` tree.
- **spangap-net** — multi-function (net/tls/ntp/mdns/wget) but mono-file; this is
  *why* tls/ntp leaked into core's docs. Needs `docs/`.
- **hw-tdeck** — multi-function board; has both INTERNALS.md and docs/ — collapse
  to README+docs/ after A2 moves.
- **seccam** — already multi-function docs/ but over-fragmented (10 files); consolidate.
- **sshd** — became two-function (server + outbound client); decide two-file vs
  `docs/{server,client}.md`.
- Mono-function (keep two-file README+INTERNALS): acme, audio, duckdns, the four
  ifaces, lxmf, maps, nomad, ota, upnp, viewer, wg, spangap-lcd, the hw boards,
  reticulous, updater.

**Decision needed:** confirm the multi-function set (esp. spangap-web and spangap-net
gaining `docs/` trees) before any content moves.

### A4. Auto-init: drop hand-written `xInit()` / `app_main` sequences everywhere
Nearly every straddle's README still says "call `xInit()` after netInit/rnsdInit"
or shows a hand-listed `app_main` sequence. Auto-init via the generated
`spangapInitStraddles()` supersedes all of it. Blanket fix: state "starts
automatically when in the build." Worst offenders: spangap-core (README init block
is factually wrong now), hw-tdeck (`tdeckPreInit/PostInit` vocabulary gone), seccam,
all ifaces, lxmf, acme, duckdns, ota, spangap-net. (`reticulous` README is already
clean here.)

### A5. `ota` vs `updater` — resolve the two-update-stories conflict
`updater` (single floor-image + boot-flip) supersedes A/B `ota`, and the shipped
partition table has no A/B/`otadata`/`fixed_b` by default — so **ota's upgrade path
is structurally dead on current hardware**, yet ota's README presents it as the live
mechanism with no mention of updater. `spangap-core/docs/ota.md` is already banner-
marked "Superseded."

**Decision needed:** retire `ota` (docs + note it's legacy) **or** keep it with a
plain "superseded by updater" framing? Also note: updater carries **no signature
verification** (deferred), so ota's signing/manifest material isn't yet replaced —
that gap should be stated, not hidden.

### A6. Files to delete (stale CLAUDE.md / README-old.md / superseded)
`hw-tdeck/{CLAUDE.md, README-old.md}`, `spangap-core/{CLAUDE.md, esp-idf/CLAUDE.md,
README-old.md}`, `seccam/CLAUDE.md`, `spangap-web/browser/{README-old.md, CLAUDE.md}`.
All are self-described as superseded and are still cross-linked as if authoritative —
delete after coverage-audit, fix the "See also/Read next" links.

### A7. Declarative settings — stop docs re-defining keys the `settings:` block owns
duckdns, upnp, wg, nomad (and others) have docs that re-list/redefine config defaults
now owned by the straddle.yaml `settings:` block. Per the standard, point at the
owner, don't restate. Separately, the `s.<x>.version` config-version gates found in
nomad, upnp, wg, audio, tcp, etc. conflict with the project's **no-config-migrations**
policy — flag for *code* removal, out of scope for docs but worth a tracking note.

---

## Part B — Per-straddle summaries

Format: **[terminology] / [biggest doc⇄code gap] / [structure]**. "Fold in" =
external doc to absorb (see A2).

### Core platform

**spangap-core** — *Canonical vocabulary owner; biggest job.*
- Terms: teach the 3-call bring-up (`spangapInit` → `spangapInitStraddles` →
  `spangapPostAppInit`); enumerate the full prefix set (`s.*`, `secrets.*`, bare,
  plus the undocumented `fw.*` read-only identity + `sys.*` telemetry namespaces).
- Gaps: README `app_main` block is wrong (real order differs; `storageInit`/`cronInit`
  are now `init:` hooks, `itsInit` isn't in `spangapInit`); module map omits live
  `auth.cpp`, `mem`/`mem_new.cpp`, `cli_cmd_mount.cpp`; aspirational `scripts/`
  operator-tools block (no such dir); "Phase-1 split" prose throughout.
- Structure: delete INTERNALS.md → per-function `docs/` (A3); move 9 foreign docs
  out (A2); add docs for `cli`, `spi_helper`, `compat`, the dispatcher.

**spangap-net** — mono-file but multi-function.
- Terms: drop the "call centre/call center" metaphor → "net task / socket
  multiplexer"; keep "proxy" out (collides with shelved reverse-proxy) → "socket relay."
- Gaps: README claims "firmware-only, no browser, not a UI activator" — **false**
  (has browser panels + `net_lcd.cpp` LCD pane + `browser_register`); `s.net.hostname`
  default is `FW_HOSTNAME` not `PROJECT_NAME`; no storage list, no CLI manual
  (`wget` entirely undocumented); doesn't mention it bundles acme/upnp/wg/duckdns/sshd
  via `additional_installs`.
- Structure: needs `docs/{net,tls,ntp,mdns}.md` (fold core's tls.md/ntp.md, dropping
  ntp's stale API).

**spangap-web** — mono-file but multi-function; pre-app-dock docs.
- Terms: `menuRegistry` doesn't exist (→ `useMenuStore`/`menu.register`); `MenuBar` →
  `Dock`/`registerApp`; `SettingsPanel` → `GeneratedPanel`. Broken import example in
  `browser/README.md`.
- Gaps: NetworkPanel/WifiScanDialog now live in **spangap-net**, not here; `network`
  module gone; WebDAV flagged "WIP" but implemented; loopback-HTTP auth/redirect
  exemption (the thing that lets the LCD viewer fetch its own pages) undocumented;
  no storage surface; WebRTC port 4433 is **net-owned** (point at owner).
- Structure: needs `docs/{web,auth,webrtc,browser-shell}.md`; fold the app/settings
  model from plans; delete `browser/{README-old,CLAUDE}.md`.

**spangap-lcd** — docs describe the retired pre-Brookesia API.
- Terms: `lcdRegister(name,icon,fn)` is **gone** → `lcdInstall(LcdApp*)`; "program"
  → "app"; invented types `lcdProgramFn`/`lcdPaneFn` → `lcd_fn_t`; `lcdSettingToggle`
  → `lcdSettingSwitch`.
- Gaps: legacy launcher deleted, phone-shell is unconditional (`CONFIG_LCD_PHONE`
  defined nowhere, yet headers gate on it); stale file map; recents/nav/`LcdApp`
  lifecycle + per-app keypad-focus save/restore all undocumented; `setStatusIcon`
  is a stub; missing storage keys (`s.lcd.date_format`, etc.).
- Structure: two-file OK; **fold in `spangap-core/docs/lcd.md`** (rewrite against
  current source) + the `LcdApp`/shell model from `plans/brookesia.md` et al.; fix
  hw-tdeck's "spangap-core's lcd component" mis-framing.

### Reticulum stack

**rns** — *reference; largely compliant.* Only stragglers: stale
`RNSD_PORT_TRANSPORT`/`tr-*` tokens in straddle.yaml comments (A1); README "Settings"
table missing 8 live `s.rnsd.*` tunables; identity-cache described as hardcoded but
it's now a key; drop the `## Status` section (dated prose — the reference shouldn't
carry it).

**iface-auto** — `s.auto.enable` defaults **OFF** (docs say on); registers as `auto`
not `autoif.<hash>`; `group` is a *name* not a hash (upstream `group_id`, default
`"reticulum"`); README storage list grossly incomplete (modes, IFAC keys, telemetry);
fold in hw-tdeck/docs/auto.md (fixing its `RNSD_PORT_TRANSPORT`→`RNSD_PORT_IFACE`).

**iface-espnow** — "transport"→"interface"; "ESPnow"→"ESP-NOW" in prose; wrong key
`s.espnow.lr_rate` (real `s.espnow.rate`); whole IFAC + channel-conflict subsystem
undocumented; wrong CLI (`espnow status/flap` don't exist); self-contradicting
"spangap-net not a dep" (it's a hard dep). Fold in hw-tdeck/CLAUDE.md prose; mine
(don't lift live) `plans/iface-espnow/proper-air-protocol.md` (unbuilt).

**iface-lora** — "RNode framing"/"HDLC/KISS" is a false claim → "LoRa split framing";
INTERNALS is SX1262-only but code drives 15 chips/5 families; phantom listen-before-
talk state machine (never built); wrong IFAC keys (`ifac_psk` doesn't exist); fold in
hw-tdeck/docs/lora.md (16 KB ≈ the real INTERNALS) corrected to multi-radio.

**iface-tcp** — code shipped peer-modes + per-iface IFAC + inbound TCP server; docs
still say "Phase-1 outbound-only, inbound planned." Wrong keys (`s.tcp.listen_port`
→ `s.tcp.server_port`); huge undocumented CLI; fold in hw-tdeck/docs/tcp.md; document
the LCD panel slice.

**lxmf** — LCD program is "LXMF" not "LXMessenger" (+ bogus `lcdRegister` signature);
clickable `lxmf@<hash>` links shipped but undocumented; `s.lxmf.link.idle_s` default
is 600 not 0; tickets contradiction (`auto_ticket` defaults on but mostly a no-op);
stale `#if CONFIG_SPANGAP_LCD` claims; **fold in & delete** hw-tdeck/docs/lxmf.md +
internals/lxmf.md (the docs currently *defer outward* to them).

**nomad** — "drift/announce-drift feed" is an in-house coinage → "announced-nodes
feed" (upstream = announces); LCD slice path wrong; `NOMAD_RESP_PORT=130` + several
defaults undocumented; stale bookmark-format comment contradicts its own parser;
**fold in & delete** hw-tdeck/docs/nomad.md; dangling link to a nonexistent
`docs/plans/nomad.md`.

### Apps & services

**maps** — *Mono-function (no own docs/; the "10 files" was hw-tdeck's).* README
file-tree contradicts INTERNALS (worker/compositor live in `conditional/spangap-lcd/`,
not `src/maps.cpp` which is a no-op); phantom "browser half" in yaml (no browser
source exists); ".bin default" stale (code is `.jpg` first). Storage verified correct.
**Fold in & delete** the stale hw-tdeck/docs/maps.md.

**viewer** — *already in good shape.* Main fix: "Window/Window-menu" → "Dock" (the
dock replaced the menu bar) — pervasive across code+docs; stale yaml `s.viewer.urlbar`
comment (key gone); "seeds defaults" claim false (nothing defaulted); INTERNALS
`lcdRegister(...)` → `LcdApp`/`lcdInstall`; cut journey residue ("used to", "removed");
the loopback-exemption/stack-raise detail belongs in spangap-web (it owns it), viewer
just references it.

**seccam** — *Biggest single rename:* **`diptych` → `spangap`** (14+ refs; all
`../diptych/...` cross-links are dead). The "diptych split" migration never happened
the way docs say (already done via spangap deps). `live`→`stream` and `detect`↔
`triggers` naming reconciliation. No `main.cpp`/`app_main` (it's `seccam.cpp`/
`seccamInit`); wrong source paths everywhere (`esp-idf/main/`). Consolidate the
over-fragmented 10-file docs/ (4 files for "camera", 2 for "streaming"); delete
CLAUDE.md, key-fixes.md, tcp_rtsp.md (journey log).

**sshd** — *Became two-function:* full **outbound SSH client** (`ssh_client.cpp`,
CLI `ssh`/`ssh-keygen`, `s.ssh.*`/`secrets.ssh.privkey`) is entirely undocumented.
Password auth already migrated to core `auth` (`secrets.sshd.password` is dead); CLI
table wrong; stale "protocol not yet implemented" strings in *live* code; header
algorithm matrix missing ML-KEM. Fold in `plans/sshd/ssh.md` + the device-side of
`plans/ssh-spangap-cli-bridge.md`. Structure: likely `docs/{server,client}.md`.

**acme** — fabricated storage model (docs invent `secrets.acme.*`; real state is PEM
files on `/state/`); "Directory" label misuses RFC-8555 term (it's the *account* URL);
`s.acme.method` auto-selection + `acme renew` CLI undocumented; `acmeCheck()` dead on
boot (cron-driven); **fold in** the authoritative ACME section from
`spangap-core/docs/remote-access.md` (which also has its own stale bits to not carry).

**duckdns** — invented API (`duckdnsSetTxt()` etc. — it's a storage-key callback on
`dns.txtrecord`); `s.duckdns.enable` doesn't exist; token is `s.duckdns.token`
(secret), **not** `secrets.duckdns.token`; cron is 15 min not "5–15"; AAAA claim false
(A-record only); CLI verbs undocumented; declarative-settings ownership.

**upnp** — "forward/`fwd_t`" → "port mapping" (the IGD term); wrong key
`s.upnp.external_port` → `s.upnp.ext_port`; invented `internal_port`/live-state keys;
**UDP is mapped** (docs say TCP-only); `UPSTREAM_DOWN` *does* delete mappings (docs say
it doesn't); `AddAnyPortMapping` not used; vestigial `browser/` dir (package.json
claims a panel that's now generated).

**wg** — `s.wg.allowed_ips` invented → `s.wg.address`+`netmask`; secret is
`secrets.wg.key` not `.privkey`; `wgConnect/wgDisconnect` don't exist; "key generated
on first enable" false (only via `wg keygen`); `s.wg.dns` is a declared-but-unread
stub; PM-lock claim fiction; "8 KB wg task" stale (no longer a task). **Fold in &
delete** `spangap-core/docs/wireguard.md` (the real manual, wrong location).

**ota** — see A5. Plus: Ed25519→**ECDSA P-256** (wrong primitive named); CLI is
`version`/`version upgrade`/`version rollback` not `ota`; keys are `s.sys.ota.*` not
`s.ota.*`; `otaSetPubkey()` doesn't exist (compile-time `ota_pubkey.h`); rollback
described backwards (no bootloader auto-rollback); no cron (event-driven).

**updater** — "updater" overloaded (straddle vs `ota_1` slot vs flasher) — qualify in
prose; "stages-and-triggers" overstates (it *checks* a pre-staged file → trigger);
`/state/flashme.bin` → `fsStatePath()` (it's `/sdcard/state` on SD); cut the
"until the partition plan lands" prose (shipped: runtime `state` partition +
`updater`/`otadata` slots). Add an INTERNALS.md (partition-table parser, slot
fallbacks, boot-flip); point at core's `flash-partitions.md` rather than duplicate.

### Boards

**hw-tdeck** — see A2 (the docs/ pile) + A6 (delete CLAUDE.md/README-old.md). README/
INTERNALS still describe the **pre-split monolith** ("application straddle that
produces the image", owns app_main/partitions/OTA/SPA) — it's now a non-buildable
board-HAL straddle (`tdeck.cpp`/`gps.cpp`/`rtc.cpp` + audio/lcd shims). Dangling
`../../s/` and `research/` link paths; aspirational file-layout box; three live
subsystems (**RTC, battery monitor, ES7210 audio shim**) entirely undocumented;
`tdeckPreInit/PostInit` → real `tdeckStart`/`tdeckLcdStart` hooks. Collapse to
README + board-only docs/.

**hw-heltecv4** — README describes a **buildable** straddle; it's a non-buildable
board-HAL component (CMake + yaml say so). Lists files that don't exist
(`esp-idf/main/`, `sdkconfig.defaults`, `web-interface/`); wrong build command
(`--no-lcd`); pin map + radio kconfig are correct (keep). **No INTERNALS.md** —
create one (Vext rail, LoRa CS-park ordering, quad-PSRAM DRAM-headroom pitfall).
Near-total README rewrite to the board-HAL shape.

**hw-seeed-sense** — *No .md files at all.* Config-only board straddle (ships zero
HAL sources; the XIAO Sense pin map currently lives in `seccam/.../Seeed_XIAO_...h`).
Create README + INTERNALS from the manifest (8 MB octal PSRAM, SD 1-bit pins,
`SPIRAM_TRY_ALLOCATE_WIFI_LWIP` rationale = the 0x101 pitfall). Note the pending
camera/audio HAL factor-out and the SD-pin duplication trap.

### Project

**reticulous** — *buildable assembler; only a README.* "transport"→"interface" (A1);
`browser: web-interface` → `browser: browser` (every sibling uses `browser/`);
`## Status`/`## On Claude Code` journey prose to cut; defers to the misplaced
`hw-tdeck/docs/component-plan.md` (fold that → a new reticulous **INTERNALS.md**:
the `additional_installs`/`--without` cascade, build-generated init dispatch,
partition/flash-size generation, OTA-omitted rationale, factory-state image). Stale
org-profile READMEs (`reticulous-.github*`) drifting — make them thin pointers.
app_main is correctly auto-generated (no stale sequence — good).

---

## Suggested sequencing for the edit pass

1. **Settle A1–A7** (terminology, doc-move targets, structure shapes, ota/updater).
2. **rns first** as the locked reference (tiny stragglers).
3. **spangap-core / -net / -web / -lcd** — they own vocabulary and absorb the most
   misplaced docs; everything downstream points at them.
4. **ifaces + lxmf + nomad + maps** — fold the hw-tdeck/docs pile into owners.
5. **hw-tdeck / heltecv4 / seeed-sense / reticulous** — boards + project, after their
   components are settled.
6. **Leaf services** (acme/duckdns/upnp/wg/ota/updater/sshd/audio/seccam/viewer) — mostly
   self-contained.
