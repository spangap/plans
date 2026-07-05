# RNS Channel + rnsh/rnshd + CLI login-gate — implementation plan

Five coupled deliverables:

1. **Channel** — port RNS `Channel` (reliable, sequenced, windowed messaging that
   rides *inside* a `Link`) into the vendored microReticulum, and expose it through
   `rnsd` the same way `Link` is exposed today. The underlying `Link` handle stays
   hidden; consumers only ever touch the channel, and it behaves like a link does
   now (open by dest-hash, packet-mode send/recv, storage-observable state).
2. **`rnsh`** — outgoing remote-shell **CLI command in the `rns` straddle**. Opens a
   channel to a remote `rnsh` aspect and relays an interactive CLI session, with the
   remote's stdout+stderr collapsed into one output stream.
3. **`rnshd`** — a daemon that advertises destination aspect **`rnsh`** and accepts
   an incoming **channel**.
4. **`login`** — a new member on the CLI connect AUX (`cli_connect_t`). When set, the
   CLI prints `Enter admin password: ` and refuses **all** command processing until
   the admin password is entered correctly. Server-side, no circumvention.
5. **Wire it up** — `rnshd` opens the CLI backend with `login=1` on every accepted
   channel, so remote users are forced through the admin gate.

---

## 0. Ground truth (what exists today)

- **Channel is a stub.** `rns/esp-idf/components/microreticulum/src/Channel.{h,cpp}`
  declare an empty pimpl (`class Object {}`), no logic. `Link::get_channel()` is
  commented out (`//z`, `Link.h:228`). `Type.h:585` `namespace Channel {}` is empty.
  The one usable hook already present: `Type.h:378` defines Link packet context
  `CHANNEL = 0x0E`. So the transport hook exists; the state machine does not.
- **Python reference is vendored in-tree** (port from these, do not reinvent):
  - `/straddles/archive/diptych/research/reticulum/Reticulum/RNS/Channel.py` (705 ln)
  - `.../RNS/Buffer.py` (371 ln)
  - `.../RNS/Utilities/rnsh/{protocol,session,initiator,process}.py`
- **Link consumer API is the template to mirror** (`rns/esp-idf/src/rnsd.cpp`):
  - `rnsd_link_connect_t` (`rnsd.cpp:194-206`), C wrapper `rnsdLinkOpen` (`2766-2789`).
  - Slot table `link_conn_t` (`3119-3186`), `linkAlloc`/`linkFindBy*` (`3188-3266`),
    storage-key helpers `linkKey`/`linkPublishState`/`linkSetError` (`3268-3319`).
  - Establish core `linkKickoff` (`3832-3906`); connect `onLinkConnect` (`4046-4137`);
    lifecycle `onLinkEstablishedCb` (`3392-3460`), `onLinkClosedCb` (`3462-3479`),
    `onLinkPacketCb` (`3481-3500`); data `onLinkRecv` (`4139-4176`); teardown
    `onLinkDisconnect` (`4178-4195`); 1 Hz `linkTick` (`3960-4044`).
  - **Inbound** links: `rnsdDestListenLinks` (`2863-2879`) → `RNSD_DEST_LINK_LISTEN`
    frame handled at `rnsd.cpp:1143-1163` (caches owning-task + inbox port, flips
    `accepts_links(true)`, sets `onIncomingLinkEstablished`) → on handshake complete
    `onIncomingLinkEstablished` (`4305-4401`) opens a fresh ITS conn back to the
    consumer's inbox port carrying `rnsd_link_incoming_t` (`ports.h:204-211`).
    Working reference consumer: the `clink` test task, `rnsd.cpp:4403-4622`,
    `CLINK_INBOX_PORT 120`.
  - Port registration `4801-4815`; slot alloc in init `4703-4712`; CLI-command
    registration `5070-5073` (`cliRegisterCmd("rnsd", …)` etc.).
- **CLI-over-ITS** (`spangap-core/esp-idf`):
  - Ports `CLI_PORT_TCP=8081` (stream), `CLI_PORT_DC=1` (packet) — `cli.h:15-16`.
  - Connect AUX struct `cli_connect_t { cli_mode_t mode; uint8_t from_usb_serial;
    cli_color_t color; uint8_t no_prompt; }` — `cli.h:35-48`. Decoded in
    `cliTcpConnect` (`cli.cpp:1139-1164`), which already tolerates short/legacy and
    oversized payloads.
  - Per-slot state `cli_slot_t` (`cli.cpp:145-161`); dispatch loop `1242-1318`;
    output is a **single stream** through `cliOut`→`itsCliWrite` (`cli.cpp:312-341`)
    — there is no separate stderr, so stdout/stderr are collapsed by construction.
  - Password prompt primitive `cliReadLine(out,len,CLI_ECHO_STARS)` (`cli.cpp:347-405`).
  - **There is no CLI auth today** — any client that reaches the port gets an
    unauthenticated session.
  - Admin gate: `authLogin(pw, "admin", outRealm, outCookie) == AUTH_OK`
    (`auth.h:42`, `auth.cpp:388-419`), `authEnabled()` (`auth.h:28`). Password stored
    hashed under `secrets.auth.realms.<n>.{name,hash}`, admin = realm 0.
- **sshd is the daemon template** (`/straddles/sshd`): `sshdInit` (`sshd.cpp:413-445`)
  spawns a task, registers CLI cmds + settings; on channel-run it dials the CLI as an
  ITS client, `cli_connect_t cc = { cliMode, 0, color, noPrompt }`,
  `itsConnect("cli", CLI_PORT_TCP, &cc, …)` (`sshd_session.cpp:914-943`); auth is
  `authLogin(pw,"admin",…)` (`sshd_session.cpp:799-812`); it forwards the single CLI
  stream verbatim (never emits EXTENDED_DATA). sshd is TCP-only (no RNS).

---

## 1. Design decisions & non-goals

- **The channel is a real RNS Channel on the wire** (byte-identical Envelope +
  packet context `0x0E`), so it interoperates at the Channel layer with stock
  Reticulum. **But the application protocol inside is our own minimal rnsh framing**,
  *not* upstream rnsh's msgpack/PTY protocol. Both `rnsh` and `rnshd` are ours, so we
  need no wire-compat with the Python `rnsh`.
- **Non-goals (v1):** upstream-rnsh interop; PTY / `WindowSizeMessage` / terminal
  resize; a distinct stderr stream (collapsed into one output stream — this *is* the
  "collapsing stdout and stderr" requirement); bz2 stream compression; the RNS
  `Buffer`/`RawChannelReader/Writer` byte-stream layer; adaptive RTT window growth
  (start with a small fixed window); channel-borne Resources/requests (big transfers
  stay on `Link`+Resource). These are all noted as future extensions.
- **Channel exposed "like a link":** the `rnsd` channel API deals in **opaque
  reliable byte-messages** (each ≤ channel MDU). Reliability/sequencing lives in the
  Channel layer; the consumer just sees ordered, de-duplicated, delivered messages.
- **New straddle `reticulous/rnsh`** holds **both** halves of the shell: the
  outgoing `rnsh` **client CLI command** and the **server daemon** (advertises aspect
  `rnsh`, accepts an incoming channel, bridges to the login-gated CLI). Naming: the
  component is `rnsh` (not `rnshd`) per the user. `rns` stays the pure protocol core
  (Channel primitive + rnsd channel API only) — its README explicitly does not own
  apps. `rnsh` `requires: reticulous/rns` (+ core for auth). The client command is a
  thin consumer of the channel API, akin to the existing `rnprobe`/`clink` probes.

---

## 2. Deliverable 1 — Channel primitive in microReticulum

Port `Channel.py` → `Channel.{h,cpp}`. Keep the Envelope + reliability faithful;
drop the Python message-factory-registry sugar in favour of a small callback the
outer layer (rnsd) drives — mR's C-function-pointer callbacks carry no userdata, so
match by object identity exactly as the Link code does (`sameLink`, `rnsd.cpp:3204`).

### 2.1 Wire format (byte-identical to RNS)

- **Envelope** = 6-byte big-endian header + payload:
  `pack(">HHH", msgtype, sequence, length) + data` (Channel.py `Envelope.pack/unpack`).
  `sequence` in `[0, 0xFFFF]`, wraps mod `SEQ_MODULUS = 0x10000`.
- **Transport:** each envelope is exactly one `RNS::Packet(link, raw,
  context = Type::Packet::CHANNEL /*0x0E*/)`. Never a Resource. Message payload must
  fit `channel.mdu = link.mdu - 6` (compute at runtime; do not hardcode — the
  Channel research flagged the exact Link MDU constant needs verifying in `Type.h`).
- **App msgtype:** reserve one internal type for "opaque consumer bytes" (rnsd
  wraps consumer sends in it). `rnsh`'s own framing lives *inside* that payload
  (§6), so Channel stays application-agnostic and the rnsd API stays byte-array —
  the same isolation `rnsd.h` already promises consumers.

### 2.2 Classes (Channel.h/.cpp)

- `class Envelope` — `{ uint16_t msgtype, sequence, length; Bytes data; bool packed;
  double_t/… }`; `Bytes pack()`, `bool unpack(const Bytes&)`.
- `class Channel` (pimpl over `shared_ptr<ChannelData>`, matching mR's handle idiom):
  - **TX:** `send(const Bytes& payload)` → alloc `sequence = _next_sequence++`,
    build Envelope, `Packet(link, env, CHANNEL)`, `pkt.send()` → `PacketReceipt`,
    push `{envelope, receipt, tries, sent_at}` onto `_tx_ring`. Gate on
    `is_ready_to_send()`: refuse when `outstanding >= _window` (return false / an
    `ME_LINK_NOT_READY` equivalent so rnsd can buffer — see §3 outbox).
  - **TX proof/timeout:** hook the receipt's delivery callback → remove from
    `_tx_ring`, grow `_window` up to `_window_max`. Poll receipt status + a
    wall-clock deadline each tick (mR never fires receipt *timeout* callbacks — the
    Link code already works around this at `rnsd.cpp:3973-3989`): on timeout,
    `retry(envelope)` = re-`send()` the packet, `tries++`, shrink `_window` toward
    `_window_min`; after `_max_tries = 5` give up → `outlet.timed_out()` →
    `link.teardown()`.
  - **RX:** `_receive(const Bytes& plaintext)` (called from Link, §2.3) → unpack →
    window/dup check (reject out-of-window seq, handle wrap via `SEQ_MODULUS`) →
    insert into `_rx_ring` sorted by sequence, dropping dups → deliver the
    contiguous run from `_next_rx_sequence`, advancing it mod `SEQ_MODULUS`, invoking
    the registered receive callback per envelope.
  - `set_message_callback(cb)` — single sink rnsd registers (delivered payload →
    consumer). `add/receive` semantics mirror Python's `add_message_handler` but we
    need only one handler.
  - **Constants (start conservative):** `WINDOW = 2`, `WINDOW_MIN = 2`,
    `WINDOW_MAX = 2` initially (fixed window — correct stop-and-wait-ish; adaptive
    RTT growth to `WINDOW_MAX_SLOW/MED/FAST` is a later optimization),
    `SEQ_MODULUS = 0x10000`, `_max_tries = 5`. Timeout backoff per Python:
    `1.5^(tries-1) · max(rtt·2.5, 0.025) · (len(tx_ring)+1.5)`.
- `class ChannelOutlet` (a.k.a. `LinkChannelOutlet`) — the Link⇄Channel shim:
  `send(raw)`→`Packet(link, raw, CHANNEL).send()`; `resend(pkt)`; `mdu()`; `rtt()`;
  `set_packet_delivered_callback`/`set_packet_timeout_callback`; `timed_out()`→
  `link.teardown()`. Bind delivery/timeout to the Packet's `PacketReceipt`.

### 2.3 microReticulum Link patch (small, documented)

- Re-enable `const Channel get_channel();` in `Link.h`; implement in `Link.cpp`:
  lazily construct `Channel(ChannelOutlet(*this))`, cache on `LinkData`, return it.
- In `Link::receive(const Packet&)`: when `packet.context() == Type::Packet::CHANNEL`,
  route the decrypted plaintext to `_channel._receive(plaintext)` instead of the
  normal packet callback (Python: `Link.py:1165`). Guard for "no channel yet".
- Record the µR change in `rns/INTERNALS.md` (project convention for every µR patch).

### 2.4 Testing (host + device)

- Host unit test for Envelope pack/unpack round-trip and RX resequencing/dup-drop
  (feed out-of-order sequences, assert in-order delivery, assert wrap at 0xFFFF).
- Device smoke: a `rnsd cchan` debug command (mirror `rnsd clink`, `rnsd.cpp:4403+`)
  that opens a channel, echoes messages, verifies ordered reliable delivery over a
  real link between two devices / a device and a Python peer.

---

## 3. Deliverable 2 — rnsd channel consumer API

Mirror the Link consumer API 1:1, both outbound and inbound. The channel connection
*owns a hidden Link*; consumers never see the link. Reuse the establishment
machinery to avoid drift.

### 3.1 Shared refactor (prereq)

Factor the outbound-link establishment core out of `linkKickoff` (`rnsd.cpp:3832`)
into a helper that both link and channel slots call:
`bool rnsEstablishOutbound(dest_hash, aspect, identity_key, link_timeout_ms, RNS::Link& out)`
— does recall → load identity → split aspect → build `Destination(OUT,SINGLE)` →
hash-verify → `RNS::Link(dest)` → establishment-timeout computation. Callbacks are
attached by the caller (link vs channel differ only in what happens once ACTIVE).
This keeps the two paths from copy-paste divergence.

### 3.2 ports.h / rnsd.h additions

- `constexpr uint16_t RNSD_PORT_CHANNEL = 11;` (next free after `RNSD_PORT_LINK=10`).
- `rnsd_channel_connect_t` — identical shape to `rnsd_link_connect_t`
  (`ports.h`/`rnsd.cpp:194-206`): `dest_hash[16]`, `aspect[32]`, `identity_key[40]`,
  `tag[24]`, `path_timeout_ms`, `link_timeout_ms`. `static_assert ≤ ITS_MAX_MSG_DATA`.
- Inbound: add frame op `RNSD_DEST_CHANNEL_LISTEN = 0x08` (parallel to
  `RNSD_DEST_LINK_LISTEN = 0x07`, `ports.h:197`) + reuse `rnsd_link_incoming_t` for
  the per-channel inbox connect payload (rename comment; same fields suffice).
- `rnsd.h` wrappers (docstrings mirror `rnsdLinkOpen`, `rnsd.h:149-207`):
  - `int rnsdChannelOpen(dest_hash, aspect, identity_key, tag, path_timeout_ms,
    link_timeout_ms, ref, on_recv, on_disconnect)` — packet-mode handle; each
    `itsSend` = one reliable channel message, each `itsRecv` = one delivered message.
    Accept-immediately; watch `rnsd.chan.<tag>.state`.
  - `bool rnsdDestListenChannels(int dest_handle, uint16_t target_port)` — like
    `rnsdDestListenLinks` (`rnsd.h:191-207`) but forwards a **channel** per accepted
    inbound link.

### 3.3 rnsd.cpp implementation

- New slot table `chan_conn_t` (parallel to `link_conn_t`), each slot holding:
  `used, handle, ref, tag[24], dest_hash, aspect, identity_key, state`, a **hidden**
  `RNS::Link link{NONE}`, `RNS::Channel channel{NONE}`, timers (`opened_at,
  last_activity, path_deadline, estab_deadline`), and a **pre-active outbox** of
  queued messages (a small `std::vector<std::vector<uint8_t>>`, bounded — the channel
  can hold more than the link's one-packet outbox because Channel itself is the
  reliability layer; cap it, e.g. 8, and backpressure like `RNSD_DEST_AUX_QUEUE_FULL`).
  Allocate from PSRAM + placement-new (as `s_link_conns`, `rnsd.cpp:4703-4712`).
- Handlers, each modeled on its link twin:
  - `onChannelConnect` (← `onLinkConnect` 4046): parse `rnsd_channel_connect_t`,
    dup-tag guard, alloc, init `rnsd.chan.<tag>.{direction=out,aspect,remote_hash,
    opened_s,last_error}`, then recall→ESTABLISHING+kickoff or AWAITING_PATH+request.
  - On link ACTIVE (established cb, ← `onLinkEstablishedCb` 3392): call
    `link.get_channel()`, `channel.set_message_callback(onChannelMsgCb)`, publish
    `rnsd.chan.<tag>.{link_id,mtu,rtt_ms,activated_s,state=active}`, then **flush the
    pre-active outbox** through `channel.send()`.
  - `onChannelMsgCb` (delivered payload → consumer): resolve slot by channel/link
    identity, `itsSend(handle, payload, len, 0)` (packet-mode, drop-on-backpressure
    → `last_error=rx_overflow`), bump `rx_msgs`.
  - `onChannelRecv` (← `onLinkRecv` 4139): consumer bytes → if ACTIVE and
    `channel.is_ready_to_send()` → `channel.send(Bytes)`; else enqueue in the outbox
    (bounded). Reject `> channel.mdu` with `last_error=oversize`.
  - `onChannelClosedCb` / `onChannelDisconnect` (← 3462 / 4178): teardown hidden
    Link (which tears the channel), `storageDeleteTree("rnsd.chan.<tag>")`, free slot
    after the same 3 s terminal-state grace as `linkTick`.
  - `channelTick` (← `linkTick` 3960): drive AWAITING_PATH/ESTABLISHING timeouts and
    the Channel's own tx-ring retry/timeout poll (§2.2). Called 1 Hz beside
    `linkTick` at `rnsd.cpp:5008`.
- **Inbound** (`rnshd` path): handle `RNSD_DEST_CHANNEL_LISTEN` in `onOurDestRecv`
  (beside the `0x07` case at `rnsd.cpp:1143-1163`) — cache owning task + inbox port,
  `accepts_links(true)`, set `onIncomingChannelEstablished`. That established callback
  (← `onIncomingLinkEstablished` 4305): alloc a `chan_conn_t` (direction "in", tag
  `"cin.<8hex>"`), `link.get_channel()`, register `onChannelMsgCb`, populate
  `rnsd_link_incoming_t`, and `itsConnectByTaskHandle(listener_task, inbox_port, …,
  onChannelRecv, onChannelDisconnect)` back to `rnshd`.
- Register the port + wire the aux at init (← `4801-4815`): `itsServerPortOpen(
  RNSD_PORT_CHANNEL, ITS_PACKET, …)`, `itsServerOnConnect/OnRecv/OnDisconnect`.
- **Storage keys:** `rnsd.chan.<tag>.{state,direction,aspect,remote_hash,opened_s,
  activated_s,link_id,mtu,rtt_ms,rx_msgs,tx_msgs,last_error}` and reverse index
  `rnsd.chan.byid.<link_id>` — mirror the documented `rnsd.links.<tag>.*` tree
  (`README.md:211`). Add a row to the rns README storage table.

---

## 4. Deliverable 3 — `login` AUX member on `cli_connect_t`

Goal: a connecting client can demand that the CLI force admin-password entry before
**any** command runs, with enforcement entirely server-side (no client can skip it).

### 4.1 Struct change (back-compatible)

Append to `cli_connect_t` (`cli.h:35-48`):
```c
uint8_t login;  /* 1 = require admin-password login before any command runs */
```
Appending keeps back-compat: `cliTcpConnect` already treats a short payload as
legacy and reads only the fields present (`cli.cpp:1148-1153`) — old clients →
`login=0`. Store it on `cli_slot_t` (`cli.cpp:145-161`) as `bool loginRequired`
plus `bool authed`.

### 4.2 Server-side enforcement (the "no circumvention" core)

All of this lives in `spangap-core/.../cli.cpp` — the client cannot influence it
beyond setting the bit:

1. On connect with `login=1`: set `slot.loginRequired=true, slot.authed=false`, put
   the slot into a new `AWAIT_PW` sub-state, and immediately emit `Enter admin
   password: ` (no command prompt yet).
2. In the dispatch loop (`cli.cpp:1242-1318`), **before** any command handling: if
   `slot.loginRequired && !slot.authed`, route every received byte into a
   password-line accumulator with echo masked (reuse the `CLI_ECHO_STARS` masking
   that `cliReadLine` uses, `cli.cpp:347-405`) — do **not** feed the normal line
   editor or `cliProcess`. On newline: `authLogin(line, "admin", realm, cookie)`.
   - `AUTH_OK` → `slot.authed=true`, clear buffer, print the normal banner/prompt,
     begin accepting commands.
   - otherwise → increment `slot.pwTries`; after 3 failures (and honour
     `authLogin`'s built-in rate-limit/backoff, `auth.cpp:388-419`) print a failure
     line and **disconnect the slot** (`pendingClose`). No command is ever dispatched
     while unauthenticated, so there is no path to run anything without the password.
3. `authEnabled()` interplay: if `authEnabled()` is false (no admin password set), a
   `login=1` connection must **fail closed** — refuse the session (or print "admin
   password not set" and disconnect), never fall through to an open shell. (Decision:
   fail closed; see §9.)
4. Because the gate is purely a function of `slot.authed`, it applies identically to
   ANSI and LINE modes and regardless of what bytes the client sends first (a command
   pipelined on the first line is consumed as a password guess, not executed).

Transports that never send a `cli_connect_t` (browser DC `cliDcConnect`
`cli.cpp:1169-1194`; net-forwarded raw-TCP fallback) default `login=0` — unchanged.
The gate is opt-in by the connecting task; `rnshd` (and optionally future callers)
opt in. sshd keeps its own SSH-level `authLogin` and does not need this.

### 4.3 Reuse note

Prefer a per-slot state-machine (above) over calling `cliReadLine` from the connect
callback: `cliReadLine` is a nested modal read intended for use *inside* a command
handler, and blocking the single CLI task in the connect path would stall other
slots. The state-machine keeps the task responsive and multi-client.

---

## 5. Deliverable 4 — `rnsh` straddle: server daemon half

New straddle `/straddles/rnsh`, structured like `/straddles/sshd`. It contains **both**
the server daemon (this section) and the client command (§6). Server naming uses the
`rnsh` prefix (no `d`): `rnshServerInit`, `rnshServerTask`, `s.rnsh.server.*`.

### 5.1 straddle.yaml
```yaml
schema_version: 1
name: reticulous/rnsh
prefix: rnsh
firmware: esp-idf
platforms: [idf]
requires:
  - reticulous/rns           # channel API + rnsd
init:
  - call: rnshInit           # brings up client CLI cmd + server daemon
settings:
  - menu:
      - { id: mesh, label: "Mesh Network", placement: 3 }
      - { id: rnsh, label: "Remote shell" }
    web: false
    rows:
      - section: "Reticulum shell (rnsh)"
      - switch: { label: "Server enabled", key: "s.rnsh.server.enabled", default: 0 }
```
`rnshInit` registers the `rnsh` client CLI command (§6) unconditionally and spawns the
server task (gated on `s.rnsh.server.enabled`, default off).
Default **off** — it exposes the admin CLI over the mesh; opt-in only. (Core/auth is
available transitively via rns; add an explicit `spangap-core` require only if the
build graph needs it.)

### 5.2 Task & bring-up (← sshd.cpp:413-445 / 160-187)

- `rnshdInit()`: register storage defaults gated on `s.rnshd.version`, register CLI
  admin cmd(s) (`rnshd` status), spawn `rnshdTask` (PSRAM stack, prio ~5).
- `rnshdTask`: `itsServerInit` + `itsClientInit`; open inbox port
  `RNSHD_INBOX_PORT` (pick a distinct number, e.g. **130** — clink uses 120, lxmf
  100/101); host the destination and register the channel listener:
  - `dest = rnsdDestOpen("rnsh", "secrets.rnshd.identity", /*SINGLE*/0, ref,
    onDestRecv, onDestDisc)` — creates/loads rnshd's own identity.
  - `rnsdDestListenChannels(dest, RNSHD_INBOX_PORT)` (Deliverable 2 inbound API).
  - Announce the destination so clients can find it (reuse rnsd's announce path;
    the `clink listen` reference does this at `rnsd.cpp:4612-4619`). Announce cadence
    gated by a setting (`s.rnshd.announce.interval`), debounced on iface-up like the
    rest of rnsd's announces.
- Listener driven by storage: `NOW_AND_ON_CHANGE("s.rnshd.enabled", …)` opens/closes
  the destination+listener (mirrors sshd's `applyListenerState`, `sshd.cpp:114-181`).

### 5.3 Per-session bridge (← sshd_session.cpp)

- Fixed session array `RNSHD_MAX_SESSIONS` (start 2, like sshd), each slot holding
  the inbox ITS handle + the CLI backend handle + state.
- `onInboxConnect(handle, rnsd_link_incoming_t)`: a remote channel arrived. Open the
  CLI backend **with login forced**:
  ```c
  cli_connect_t cc = { CLI_ANSI, 0, color, /*no_prompt*/0, /*login*/1 };   // Deliverable 5
  backend = itsConnect("cli", CLI_PORT_TCP, &cc, sizeof(cc), …,
                       sessionSlot, onBackendRecv, onBackendDisc);
  ```
  (Mode: `CLI_ANSI` for an interactive shell; `color` from `s.rnshd.color`.)
- Byte pump, both directions (verbatim, single stream — this satisfies "collapsing
  stdout and stderr": the CLI backend is one ITS stream, so remote output is unified
  by construction, exactly as sshd forwards it without EXTENDED_DATA):
  - remote → CLI: `onInboxRecv` gets a delivered channel message → strip the 1-byte
    rnsh opcode (§6) → `itsSend(backend, data, n, …)`.
  - CLI → remote: `onBackendRecv` drains the backend → wrap in an rnsh `DATA` message
    → `itsSend(inboxHandle, …)` (rnsd sends it reliably over the channel).
- Teardown: remote closes channel → `onInboxDisconnect` → `itsDisconnect(backend)`;
  backend closes (user typed `exit`) → drain + optionally send an rnsh `EXIT` message
  → tear the channel down. (← `session_close`/`session_on_backend_close`,
  `sshd_session.cpp:1192-1282`.)
- **Auth** is entirely delegated to the CLI login gate (Deliverable 5): rnshd does
  not itself call `authLogin`. The Link is already end-to-end encrypted+authenticated
  by Reticulum when the channel is established; rnshd adds the admin-password gate via
  `login=1`, and the `Enter admin password:` prompt flows over the channel to the
  remote user.

### 5.4 Browser/docs
- Optional `browser_register` + a small settings panel (enable switch, identity
  hash, announce interval) — can be deferred; the generated `settings:` pane covers
  the essential toggle. Add `README.md` + `INTERNALS.md` for the straddle.

---

## 6. Deliverable 5 — `rnsh` outgoing CLI command (in the `rnsh` straddle)

A CLI command registered from `rnshInit` via `cliRegisterCmd("rnsh", cliRnsh)`
(same API the rns probes use at `rnsd.cpp:5070-5073`). It runs on the CLI task (which
is already an ITS client and supports nested modal reads), just like `rnprobe`/`clink`,
and consumes the rnsd channel API exported by `rns`.

### 6.1 rnsh app-layer framing (inside each channel message)

Minimal 1-byte opcode prefix (Channel already gives reliability/ordering, so this is
all that's needed):
- `0x00 RNSH_DATA` — terminal bytes, both directions (keystrokes ↔ combined output,
  including the `Enter admin password:` prompt and every command's stdout+stderr).
- `0x01 RNSH_EXIT` — server→client, optional 1-byte status; signals session end
  (otherwise channel/link teardown ends the session).

Define these in a shared `rnsh.h` under `rns/esp-idf/include` (both the `rnsh` client
and the `rnshd` server include it).

### 6.2 `cliRnsh(const char* args)` behavior

1. Parse `rnsh <dest_hash_hex> [aspect]` (default aspect `rnsh`). Help via
   `cliWantsHelp` (pattern at `rnsd.cpp:1936`). Tokenize like `cliRnprobe`
   (`rnsd.cpp:2371-2420`).
2. `rnsdRecallPubkey(dest_hash, pub)`; if unknown → `rnsdRequestPath(dest_hash)`,
   print "requesting path…", poll a few seconds, retry (same recovery the README
   documents, `README.md:133`).
3. `h = rnsdChannelOpen(dest_hash, aspect, "", "rnsh.<n>", path_to, link_to, ref,
   onRnshRecv, onRnshDisc)`. Poll `rnsd.chan.rnsh.<n>.state` until `active` or a
   timeout/`failed` (print `last_error`).
4. Enter an **interactive relay loop** (this is the "outgoing traffic" path):
   - local input: `cliReadRaw` (`cli.h:104-121`) grabs the operator's terminal bytes
     → send as `RNSH_DATA` over the channel (`itsSend(h,…)`).
   - remote output: `onRnshRecv` delivers channel messages → `RNSH_DATA` payloads
     `cliWrite`n to the operator's terminal (single stream — stdout+stderr already
     collapsed by the remote CLI). `RNSH_EXIT` or channel-close → leave the loop.
   - A local escape (e.g. `~.`) tears down: `itsDisconnect(h)` and return.
5. Print a short "session closed (status N)" line; ensure the channel handle is
   always disconnected on exit (including error/timeout paths).

(If a non-interactive `rnsh <dest> -c "<cmd>"` one-shot is wanted later, send one
`RNSH_DATA` = `"<cmd>\n"`, stream output until `RNSH_EXIT`, print, close — but the
interactive relay is the primary target since the remote side is an interactive,
login-gated CLI.)

### 6.3 Docs
Add `rnsh <dest_hash> [aspect]` to the CLI table in `rns/README.md` (§CLI,
`README.md:227-252`).

---

## 7. Build / settings / cross-cutting

- **µR build:** Channel adds no new Kconfig; C++ exceptions + msgpack already
  enabled in `rns/straddle.yaml`. No bz2 dependency for v1 (compression dropped).
- **Tunables via Kconfig/README** (project convention — no magic constants):
  `CONFIG_SPANGAP_RNS_CHANNEL_WINDOW` (default 2), `_CHANNEL_MAX_TRIES` (5),
  channel outbox depth; document in `rns/README.md`. rnshd tunables as `s.rnshd.*`
  settings + any board-agnostic default in `sdkconfig.defaults.spangap`.
- **Storage docs:** extend the rns README storage tables with the `rnsd.chan.<tag>.*`
  tree and the new `s.rnshd.*` keys.
- **No config-version migrations** (zero users) — set `storageDefault` directly.
- Follow repo comment/commit conventions: no "see memory"/slug references in code or
  READMEs; DCO `Signed-off-by` only, no Co-Authored-By; commit straight to main when
  the user directs (do not auto-commit).

---

## 8. Suggested build order (each step independently testable)

1. **Channel primitive + Link patch** (Deliverable 1) with host unit tests for
   Envelope + resequencing. No rnsd/UI yet.
2. **rnsd outbound channel API** (`rnsdChannelOpen`, `RNSD_PORT_CHANNEL`, storage) +
   a `rnsd cchan` debug command; validate ordered reliable delivery device↔device.
3. **`login` AUX gate** in core CLI (Deliverable 3) — test with a throwaway client
   that sets `login=1`; verify no command runs pre-auth, bounded retries, fail-closed
   when no admin password set.
4. **rnsd inbound channel API** (`rnsdDestListenChannels`, `RNSD_DEST_CHANNEL_LISTEN`,
   `onIncomingChannelEstablished`).
5. **rnshd straddle** (Deliverable 4) bridging inbound channel ↔ CLI backend with
   `login=1`.
6. **`rnsh` client command** (Deliverable 5); full loop: `rnsh <dest>` → admin
   prompt over the mesh → interactive session → exit.

Build/flash with `spangap build reticulous/reticulous --with spangap/hw-tdeck
--with <rnshd>` (and never from a sub-straddle). Verify on-device via `spangap cli`,
`spangap log`, `spangap flash`.

---

## 9. Open questions / decisions to confirm

1. **rnshd placement** — its own straddle (recommended, mirrors sshd, keeps `rns`
   app-free) vs folded into `rns`. Plan assumes a separate straddle.
2. **rnshd naming/family** — `spangap/rnshd` vs `reticulous/rnshd` (rns is
   `reticulous/*`). Plan leans `reticulous/rnshd` for family consistency; confirm.
3. **`login=1` when no admin password is set** — fail closed (recommended: refuse the
   session) vs allow through. Plan assumes fail-closed.
4. **rnsh session model** — interactive relay (recommended; the remote is an
   interactive login-gated CLI) vs one-shot `-c`. Plan builds interactive; one-shot
   is a trivial later add.
5. **Channel window** — fixed `WINDOW=2` for v1 (recommended) vs porting the adaptive
   RTT window sizing now. Plan defers adaptive sizing.
6. **Upstream-rnsh interop** — explicitly a non-goal for v1 (custom minimal app
   protocol, real RNS Channel on the wire). Confirm we don't need to talk to the
   Python `rnsh`/`rnshd`.
