# Documentation overhaul — execution spec (coordination source of truth)

Every agent working a straddle reads **this file** + its own section in
[`documentation-overhaul-overview.md`](documentation-overhaul-overview.md) +
the standard [`documentation-overhaul.md`](documentation-overhaul.md). `rns` is
the reference end-state.

This file is authoritative for: global rules, the per-straddle doc **structure**,
the cross-straddle **move-map**, and the **phased process**. Decisions A1–A7 are
settled (below); do not relitigate them.

---

## Settled decisions

- **A1 — rename "transport" → "interface".** Across prose, code comments, CLI/log
  strings, and yaml of `rns`, the four `iface-*`, and `reticulous`. Tokens
  `RNSD_PORT_TRANSPORT` and `RNSD_PORT_REGISTER` → **`RNSD_PORT_IFACE`** (payload
  `rnsd_iface_t`). Interface reg names: `tcp/0`, `lora/0`, `auto`, `espnow` (not
  `tcp.<host>:<port>` / `autoif.<hash>` / `lora.<freq>.<sf>.<bw>`).
- **A2 — full rewrite + consolidation.** Every doc that stays is rewritten to the
  standard. Information is pulled in from **all** current locations (plans, foreign
  straddles, code comments). Files in the wrong place are absorbed then retired.
- **A3 — multi-function straddles:** `spangap-core`, `spangap-net`, `spangap-web`,
  `spangap-lcd`, and `seccam`. **Everything else is mono-function** (incl. hw-tdeck,
  sshd, reticulous, updater, the ifaces).
- **A4 — no hand-written init.** Never tell a consumer to call `xInit()` / show an
  `app_main` sequence. State "starts automatically when the straddle is in the build."
- **A5 — `updater` is the update path; `ota` is superseded.** The upper layer (signed
  download/flasher) does **not exist yet** — only the groundwork (stage-check +
  boot-flip + runtime `state`/`updater` partitions). Docs reflect exactly that: no
  signature verification yet, manual staging only. `ota`'s docs get a plain
  "superseded by `updater`" framing (no roadmap prose).
- **A6 — retire stale meta files** (see move-map): `CLAUDE.md`, `README-old.md`,
  superseded per-straddle files. (Retire = rename to `*.old.md` in the cleanup phase,
  never hard-delete here.)
- **A7 — declarative settings ownership.** A doc never re-defines a config default
  owned by another straddle's `settings:` block — point at the owner. `s.<x>.version`
  config-version gates are out (no-migrations policy); note them as a *code* cleanup,
  don't document them as features.

Plus the standard's always-rules: present tense only (no "used to / now / Phase N /
deferred"); no pointers into `plans/*`; exhaustive verified storage/port/CLI surface;
no memory refs/slugs/`[[links]]`; verify every value against live code.

---

## Doc structure per straddle

**Mono-function** (most straddles) — two files at the straddle root:
- `README.md` — operator guide (the standard's operator shape).
- `INTERNALS.md` — maintainer reference (leads with the §1 change/add inventory).

**Multi-function** (`spangap-*` + `seccam`) — a thin index README + a `docs/` pair
**per function**. NO trailing `## Internals` chapter, and NO root `INTERNALS.md`:
- `README.md` — short "what's here", indexes each function's `docs/<func>.md`.
- `docs/<func>.md` — operator guide for that function.
- `docs/<func>-internals.md` — maintainer reference for that function (the §1
  inventory for that function + threading/wire/lifecycle/pitfalls). Each
  `docs/<func>.md` links to its `docs/<func>-internals.md`.

Reason for the split (not a chapter): keep each file small so a context reading one
doc doesn't load the whole maintainer reference.

### Function decomposition for the multi-function straddles

- **spangap-core** `docs/`: `init`, `storage`, `its`, `fs` (rename from
  `unified-fs-api`), `logging`, `cron`, `power-management`, `memory`, `auth`, `cli`,
  `idf-tweaks` (absorb `spi_helper`/`compat`/heap-stub), `flash-partitions`
  (cross-cutting, stays in core). Each gets `<func>.md` + `<func>-internals.md`.
  Also keep a **slimmed cross-cutting `docs/remote-access.md`** that narrates the
  acme/duckdns/upnp trio and points at each owner (no key definitions — those move
  to the owners).
- **spangap-net** `docs/`: `net` (incl. `wget` as a section), `tls`, `ntp`, `mdns`.
- **spangap-web** `docs/`: `web` (HTTP/HTTPS/WebDAV/file-serving/loopback-exemption;
  auth *enforcement* is a section here, the credential primitive lives in core/auth),
  `webrtc`, `browser-shell` (Dock / `registerApp` / `GeneratedPanel`).
- **spangap-lcd** `docs/`: `shell` (launcher/statusbar/nav/recents/manager),
  `apps` (`LcdApp` lifecycle, input groups, per-app keypad focus, `lcdInstall`),
  `settings` (`lcdRegisterSettings` + generated panes), `terminal` (vterm).
- **seccam** `docs/`: `camera`, `audio`, `detection`, `recording`, `streaming`,
  `playback`, plus a standalone reference `supported-cameras.md` (pure table, no
  `-internals` pair). Merge today's over-fragmented files into these.

---

## Cross-straddle move-map (receiver absorbs; source is retired in cleanup)

A "move" = the **receiving** straddle's agent reads the source file *in place*, folds
the still-true content into its own docs (correcting stale facts), and fixes its own
outgoing links. The **source** straddle's agent does **not** touch foreign files —
they are listed for the cleanup phase, which renames them to `*.old.md`.

**From `hw-tdeck/docs/` →**
| source | receiver |
|---|---|
| `auto.md` | iface-auto |
| `lora.md` | iface-lora |
| `tcp.md` | iface-tcp |
| `lxmf.md` + `internals/lxmf.md` | lxmf |
| `nomad.md` | nomad |
| `maps.md` | maps |
| `component-plan.md`, `testing.md` | reticulous |
| (`tdeck.md`, `gps.md` stay — board) | hw-tdeck |

**From `spangap-core/docs/` →**
| source | receiver |
|---|---|
| `lcd.md` | spangap-lcd |
| `web.md`, `web-interface.md`, `webrtc.md` | spangap-web |
| `tls.md`, `ntp.md`, `wireguard.md` | spangap-net (wireguard → wg, see below) |
| `wireguard.md` | wg |
| `remote-access.md` | split: keys → acme/duckdns/upnp; slim trio overview stays in core |
| `ota.md` | salvage signing/manifest bits → ota (superseded framing) |
| `development.md`, `getting-started.md` | reconcile into `/straddles/spangap` build-system repo |

**Stale meta to retire (cleanup phase):** `hw-tdeck/{CLAUDE.md,README-old.md}`,
`spangap-core/{CLAUDE.md,esp-idf/CLAUDE.md,README-old.md,INTERNALS.md}` (INTERNALS
after its content is distributed into the per-func `-internals.md`),
`seccam/CLAUDE.md`, `spangap-web/browser/{README-old.md,CLAUDE.md}`, the absorbed
`hw-tdeck/docs/*` and `spangap-core/docs/*` foreign files above, and the empty
`/straddles/tr-{tcp,lora,auto,espnow}` dirs.

If two straddles both touch one fact (e.g. a net-owned port referenced by web), the
**owner** documents it fully; the other points at the owner.

---

## Phased process

**Phase 1 — content (no deletions).** Each straddle agent:
1. Reads code + all current doc sources (own files, plans, the foreign files routed
   to it in the move-map) and rewrites/creates its docs to the structure above.
2. Pulls in every durable fact from those sources, corrected against live code;
   de-phases; applies A1/A4/A5/A7.
3. Fixes its own outgoing links to point at new canonical locations.
4. Does **NOT** rename or delete anything. Emits a **CLEANUP MANIFEST**: the list of
   files that should become `*.old.md` (foreign files it absorbed, stale meta in its
   tree) and any links elsewhere it knows now dangle.

**Phase 2 — code-completeness pass.** Each agent re-reads its straddle's source
(headers, CLI registrations, storage keys, ITS ports, settings blocks, Kconfig) and
confirms every significant capability is in the docs; adds what's missing. This is a
deliberate second look, not a rehash.

**Phase 3 — cleanup (deletions).** Execute every CLEANUP MANIFEST: rename deletion
candidates to `*.old.md`, fix all remaining dangling references, delete the empty
`tr-*` dirs. Nothing is renamed before all receivers have absorbed it.

Commits (when the user directs): themed per-repo, DCO `Signed-off-by`, no AI
co-author trailer, no memory refs, stage only files this work touched.
