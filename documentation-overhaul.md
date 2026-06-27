# Documentation overhaul — rnsd / rns straddle

Guidelines for future instances, and a precise record of what was done. Read this
before touching rnsd documentation, comments, or the terminology below. Where this
document and older notes disagree, this document wins.

## The rule it establishes

**All rnsd documentation lives in exactly two files:**

- [`rns/README.md`](../rns/README.md) — the **operator / user guide**.
- [`rns/INTERNALS.md`](../rns/INTERNALS.md) — the **maintainer reference**.

There is **no rnsd documentation in `hw-tdeck/`** and **no rnsd design plan**.
Do not recreate `hw-tdeck/docs/rnsd.md` or a `link.md`-style plan. If you learn
something durable about rnsd, it goes in one of those two files (README if an
operator needs it, INTERNALS otherwise) — not a new doc.

### README.md — required shape

Operator-facing, example-driven, not a header dump:

1. One-paragraph what-it-is + the Reticulum one-liner.
2. **Origins** — brief: a *modified fork* of `attermann/microReticulum`, plus
   the one-line summary of what we added/changed (detail goes to INTERNALS).
3. What it owns (tree), what it does (the interface↔rnsd↔lxmf/nomad picture).
4. **How interfaces / lxmf / nomad talk to it** — short narrative + one minimal
   consumer code example. (Do **not** tell users to call `rnsdInit()`; the
   build's generated init starts rnsd — "if `rns` is in your build, it's running.")
5. ITS port map (table).
6. **Full storage-variable list** — settings (with defaults), runtime/telemetry,
   command sentinels, secrets. This must be exhaustive and verified against code.
7. CLI / user manual, including `rnstatus` and `rnpath` (and `rnpath -d` is
   destructive).

### INTERNALS.md — required shape

Maintainer-facing. **§1 first: an exhaustive inventory of everything we changed
or added to microReticulum and everything the rnsd layer adds.** Then the task
model/threading rule, ITS framing, hosted destinations, the full link lifecycle,
proof tracking, resource/request-response, boot barrier, persistence, maintainer
pitfalls (§8), browser, bundled components, and the announce-app_data diagnostics.
It is self-authoritative — it does **not** defer to an external plan.

## Terminology (enforce in code, comments, and docs)

| Use | Not | Why |
|---|---|---|
| **interface** / `iface` | "transport" (when it means a world-facing link) | "Transport" is mR's internal packet router; the things that talk to the world (tcp/lora/espnow/auto) are *interfaces*. |
| **our-dest** (`our_dest_t`, `s_our_dests`, `rnsd.dest.*`) | "mailbox" | "mailbox" is not Reticulum vocabulary (Reticulum says *Destination*) and collides with ITS's own "mailbox" message-queue term. |
| **modified fork** of µR | "vendored fork" | It's our fork; "vendored" only fits literally-bundled third-party (bzip2, donna crypto). |

**Keep `Transport` where it genuinely means mR's router:** `RNS::Transport`,
`Transport.cpp`, `Transport::request_path`, "transport node",
`s.rnsd.transport_enabled`, and the RNS app-name `rnstransport.*` all stay.

Renames performed (exact, for reference):

- `RNSD_PORT_TRANSPORT` → `RNSD_PORT_IFACE`; `rnsd_transport_t` → `rnsd_iface_t`
  (in rns + all four iface straddles: tcp, lora, espnow, auto).
- `mailbox_conn_t`→`our_dest_t`, `mailbox_pending_t`→`our_dest_pending_t`,
  `mailbox_receipt_t`→`our_dest_receipt_t`, `s_mailbox_conns`→`s_our_dests`,
  `s_mailbox_receipts`→`s_our_dest_receipts`, `mailbox*()`→`ourDest*()`,
  `onMailbox*`→`onOurDest*`, `RNSD_MAX_MAILBOX_CONNS`→`RNSD_MAX_OUR_DESTS` (and the
  other `RNSD_*MAILBOX*` macros), storage `rnsd.mailbox.<idx>.*`→`rnsd.dest.<idx>.*`,
  and lxmf's `connectMailbox`/`onMailbox*` → `connectOurDest`/`onOurDest*`.
- ITS's own "mailbox" concept in `spangap-core` is a *different* thing (message
  inbox) — leave it alone.

## Code-comment rules (applied; keep applying)

- **No plan-phase scaffolding** in comments or user-facing strings: strip
  `Phase A`–`Phase G`, `Phase 0/1`, `§N.N` section markers, and pointers to
  `docs/plans/*`, `link.md`, `nomad.md §`, `lxmf.md`, `component-plan.md`.
  De-phase CLI help text too ("(Phase B)" etc.).
- **No "we used to / previously / no longer" historical asides** — *unless* the
  note states a concrete pitfall a maintainer must avoid. Then keep the pitfall,
  drop the narration.
- When a comment leaned on a plan reference for meaning, replace it with a plain
  inline description; never leave dangling text. Durable knowledge that the plan
  carried goes into INTERNALS, not back into the comment.
- Don't reference memories, slugs, or `[[links]]` in code/commits/docs.

Files scrubbed this pass: `rnsd.cpp`, `rnsd.h`, `ports.h`, `lxmf.cpp`/`.h`,
`nomad.cpp`/`.h`, the microreticulum `CMakeLists.txt`, and µR src comments in
`Resource.cpp`, `Type.h`, `Link.cpp` (which referenced `link.md`).

## Behavioral changes made in this pass (so you know the current state)

These were code changes, not just docs — don't undo them:

- **Link lifetime == the consumer's ITS handle, 1:1. No parking.** Removed
  `orphan_ttl` parking, the explicit teardown aux + `rnsdLinkTeardown()`, and the
  warm-reuse path. Closing the handle tears the Link down. Warm-hold is the
  *consumer's* job (lxmf's conv-link pool, `s.lxmf.link.idle_s` default 600).
- **`linkKickoff` asserts** the constructed OUT-destination hash equals the
  caller's `dest_hash` (`last_error = aspect_mismatch` on mismatch) — catches a
  wrong aspect for a hash, e.g. hash-only rnsh-style dials.
- **µR `Destination::expand_name`**: an empty aspect no longer appends a trailing
  dot, so an app-name-only destination (e.g. rnsh's `"rnsh"`) hashes the same as
  upstream RNS. Only app-name-only destinations are affected.
- **Documented Link as ours**: `Link` was a stub (`Link_stub.cpp`) before us; we
  brought up the real `Link`, unblocked by the hand-rolled MsgPack shim. This is
  recorded as the single largest µR addition in INTERNALS §1.1.
- The microreticulum component `README.md` was reduced to a pointer to the rns
  README/INTERNALS, keeping only the pinned-commit rationale and licensing.

## How the consolidation was done (method to reuse)

1. **Gather** every rnsd source: `hw-tdeck/docs/rnsd.md`, `plans/hw-tdeck/link.md`,
   code comments, commit history, the microreticulum README, and the live headers
   (`rnsd.h`, `ports.h`).
2. **Write** README + INTERNALS from that material.
3. **Coverage audit before deleting anything**: walk every fact, value, key,
   opcode, CLI command, and pitfall in the deletion candidates and confirm it is
   either (a) reflected in README/INTERNALS or (b) genuinely stale. Anything that
   is neither gets **added** to the right file.
4. **Verify every fact against live source, not the plan.** The plan docs carried
   stale values. Confirmed-stale-and-excluded examples: `RNSD_MAX_LINK_CONNS=8`
   (it's `32`), settings `s.rnsd.enable` / `s.rnsd.name` /
   `s.rnsd.link.max_inbound_resources_per_link` (no reads exist in code). Do not
   migrate a number from a plan without grepping the source.
5. **Build after every code change.** The rename, the scrub, and the comment edits
   were each followed by a clean full build.

### Durable facts that were folded in from the deletion candidates

(So a future instance can confirm they survived.) Into README: `rnstatus` /
`rnpath` commands, the `rnsd.paths` telemetry key. Into INTERNALS: the
`demote_dbg` log-demotion patch; the opportunistic-vs-DIRECT 16-byte
strip/prepend asymmetry; `rnsd persist` is a **no-op stub**; the staggered 1 Hz
tick (avoids tcp ITS-send drops); the path-table-publish watchdog hazard + the
64-entry cap; `sameLink` callback resolution; the signed-`int32` announce-due
compare; the `thread_local`/`printf` bans and the µR-vs-spangap log-macro
`push_macro` dance; the fact that `rnstransport.remote.management` is hosted but
does **not** service requests yet; and the announce-`app_data` dialect/decoder
diagnostics (§11).

### Exceptions — encountered but deliberately not carried over

- **Stale / removed behavior**: Link parking / `orphan_ttl` / explicit teardown,
  §10a.2 reboot link persistence, all Phase A–G rollout/checklist prose, the old
  "mailbox" term, `RNSD_PORT_REGISTER`→`TRANSPORT` history, the
  `ITS_MAX_MSG_DATA` padding migration, the µR Link-status table.
- **Plan-only, not in code**: `s.rnsd.enable`, `s.rnsd.name`,
  `max_inbound_resources_per_link` — excluded as non-existent.
- **Domain-foreign**: lxmf method-choice keys, the Phase-F `/state` retention
  defect, the lxmf-internal ports 100/101 — these belong to lxmf's docs, not rns.

## What was deleted, and the dangling refs fixed

Deleted:
- `hw-tdeck/docs/rnsd.md`
- `plans/hw-tdeck/link.md`

References repointed to `rns/README.md` / `rns/INTERNALS.md` (or had the
plan-pointer removed): `hw-tdeck/INTERNALS.md`, `hw-tdeck/CLAUDE.md`,
`hw-tdeck/docs/nomad.md`, and the `link.md` mentions in the µR src comments.

## Explicitly out of scope

`hw-tdeck/docs/component-plan.md` is a **broad multi-component** architecture
document (rnsd, lora, auto, tcp, lxmf, IFAC, storage conventions, browser menus),
not an rnsd doc. It was **not** deleted and carries durable design content for the
other straddles. If it is ever retired it needs its own audit — do not fold it
into the rns docs.
