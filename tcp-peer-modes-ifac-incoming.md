# Plan: TCP peer modes, per-interface IFAC, and an incoming TCP facility

## Context

The RNS-over-TCP transport (`iface-tcp`) is outbound-only today and exposes
no way to pick a per-peer interface mode in the UI/CLI. Reticulum's IFAC
(Interface Access Codes — a per-interface shared-secret that scrambles and
authenticates every packet so only nodes sharing the same `network_name` +
`passphrase` interoperate) is **completely unimplemented**: the config doesn't
exist, and the crypto transforms in the vendored microreticulum are
commented-out Python pseudocode. There is also no inbound TCP listener.

This change delivers three things the user asked for:

1. **Set interface mode on TCP peers** — surface the already-stored per-peer
   `mode` in the CLI and browser.
2. **Per-interface IFAC** — add `network_name` + `passphrase` config to *every*
   interface (tcp peers, the new tcp server, lora, espnow, auto), plumb it to
   rnsd, and **implement real, wire-compatible IFAC enforcement** in
   microreticulum so packets without the matching key are dropped (interops
   byte-for-byte with upstream Reticulum).
3. **Incoming TCP facility** — a TCP listener in `iface-tcp` that registers each
   accepted connection as its own rnsd interface, with its own mode + IFAC.

Decisions confirmed with the user: IFAC is configured **per-interface** (each
interface can join a different IFAC network), and we implement **full
enforcement now** (not config-only).

Key facts established during exploration:
- All four iface straddles register interfaces through one path:
  `rnsd_transport_t` → `RNSD_PORT_TRANSPORT` (rnsd.cpp `onTransportConnect`,
  ~line 401). That struct + rnsd's `TaskInterface` (rnsd.cpp ~line 112) is the
  single choke point where IFAC gets applied.
- IFAC crypto primitives all exist: `Identity::full_hash` (SHA-256),
  `Cryptography::hkdf`, `Identity::sign` (Ed25519 — **verified deterministic**,
  `donna/ed25519.c`, which is what makes inbound verify-by-recompute valid),
  and `Identity::load_private_key` (splits a 64-byte blob). Missing pieces are
  orchestration only.
- Inbound TCP servers use a known pattern: register a `net_port_msg_t` on
  `NET_PORT_REG_PORT`, run an ITS server port, accept connections (see
  `sshd.cpp` lines 96-110 / 162-170, `web.cpp` connect handler). Config seeds
  for `s.tcp.server_enable`/`s.tcp.server_port` already exist (tcp.cpp ~810,
  "Phase 5").
- `secrets.*` keys persist but never leave the device and never sync to the
  browser; `s.*` keys persist and sync. So **passphrase → `secrets.*`**,
  **network_name → `s.*`**.

---

## Part A — IFAC core in microreticulum (the security-sensitive piece)

Goal: a packet on an IFAC-enabled interface is masked + signed on egress and
verified + unmasked + dropped-if-wrong on ingress, byte-identical to upstream
Reticulum. Ed25519's determinism guarantees `sign(reconstructed_raw) ==
sign(original_raw)`, so inbound recomputes and compares the access code.

**Files:**
- `rns/esp-idf/components/microreticulum/src/Interface.h`
- `rns/esp-idf/components/microreticulum/src/Transport.{h,cpp}`
- `rns/esp-idf/components/microreticulum/src/Type.h`

**Steps:**

1. **Interface.h** — `InterfaceImpl` currently has only `Bytes _ifac_identity`
   (line 91). Add `Bytes _ifac_key`, `uint16_t _ifac_size = 0`,
   `Bytes _ifac_signature`, and a cached live signer
   `Identity _ifac_identity_obj{Type::NONE}` (avoids per-packet key reload).
   Add public getters/setters on `class Interface` next to the existing
   `ifac_identity()` getter (line 208), mirroring its pattern. Keep
   `ifac_identity()` (returns non-empty `Bytes`) as the "IFAC enabled?"
   predicate so existing `if (interface.ifac_identity())` checks keep working.

2. **Type.h** — define the IFAC salt (currently only the `//z` comment at line
   ~144): `adf54d882c9a9b80771eb4995d702d4a3e733391b2a0f53f416d9f907e55cff8`.
   Provide it via a small `ifac_salt()` helper (a function-local `static Bytes`
   built with `appendHex(...)`) in Transport.cpp to avoid header ODR issues.
   Keep `IFAC_MIN_SIZE = 1` as the default `ifac_size`.

3. **Transport.cpp — derivation helper** `Transport::derive_ifac(Interface&,
   const std::string& netname, const std::string& netkey, uint16_t ifac_size =
   IFAC_MIN_SIZE)`. No-op when both strings empty. Otherwise (exact upstream
   `_add_interface` order): `ifac_origin = full_hash(netname) ++
   full_hash(netkey)` (each only if set) → `origin_hash = full_hash(origin)` →
   `ifac_key = hkdf(64, origin_hash, salt=ifac_salt(), context=None)` →
   `Identity id(false); id.load_private_key(ifac_key)` → `ifac_signature =
   id.sign(full_hash(ifac_key))` → store all members via the new setters.
   Validate `1 ≤ ifac_size ≤ 64` (clamp/default).

4. **Transport.cpp — outbound** (~lines 808-846), fill the
   `if (interface.ifac_identity()) { … }` branch (replace the `/*p … */`):
   - `ifac = ifac_identity_obj().sign(raw).right(ifac_size)`
   - `mask = hkdf(raw.size()+ifac_size, ifac, ifac_key(), None)`
   - `new_raw = [raw[0]|0x80, raw[1]] ++ ifac ++ raw.mid(2)`
   - mask loop over `new_raw`: `i==0` → `(b^mask[i])|0x80` (keep flag set);
     `i==1 || i>ifac_size+1` → `b^mask[i]`; indices `2..ifac_size+1` (the ifac
     field) → unchanged. Then `send_outgoing(masked)`.

5. **Transport.cpp — inbound** (~lines 1378-1438, inside `Transport::inbound`),
   replace the `/*p … */`. Work on a local `Bytes work = raw`:
   - IFAC-enabled iface: require `work.size() > 2+ifac_size` and `work[0]&0x80`;
     extract `ifac = work.mid(2, ifac_size)`; `mask = hkdf(work.size(), ifac,
     ifac_key(), None)`; unmask loop (`i<=1 || i>ifac_size+1` → `b^mask[i]`,
     ifac field skipped); reconstruct `new_raw = [unmasked[0]&0x7f, unmasked[1]]
     ++ unmasked.mid(2+ifac_size)`; `expected = sign(new_raw).right(ifac_size)`;
     accept (`work = new_raw`) iff `ifac == expected`, else `return` (drop).
   - flag set but iface required and mismatch/short → drop; flag not set on an
     IFAC iface → drop.
   - **Open (non-IFAC) iface:** drop any packet with the IFAC flag set; else
     pass through unchanged.
   - **Required follow-through:** every use of `raw` *after* this block in
     `inbound` (~line 1441+) must read the de-IFAC'd buffer — thread `work`
     through (e.g. `const Bytes& pkt = work;`). This is mechanical but mandatory.

6. Add a one-line comment at both `sign()` sites: IFAC verify-by-recompute
   requires deterministic Ed25519 (RFC 8032); do not substitute a randomized
   signer (would silently drop 100% of inbound IFAC traffic).

---

## Part B — Plumb IFAC + mode through rnsd

**Files:** `rns/esp-idf/include/ports.h`, `rns/esp-idf/src/rnsd.cpp`

1. **ports.h** — extend `rnsd_transport_t` (currently 38B, budget = 96B) to
   carry IFAC params: add `char ifac_netname[16]; char ifac_netkey[40];
   uint16_t ifac_size;` (repurpose `reserved`). Keep the
   `static_assert(sizeof(rnsd_transport_t) <= ITS_MAX_MSG_DATA)`. Document the
   length caps (15/39 chars). (If unbounded passphrases are later needed, switch
   these to `const char*` pointers derived synchronously in `onTransportConnect`
   — noted as the fallback, not the default.)

2. **rnsd.cpp `onTransportConnect`** (~line 426) — after building
   `slot->mr_iface` and **before** `Transport::register_interface`, call
   `RNS::Transport::derive_ifac(slot->mr_iface, info.ifac_netname,
   info.ifac_netkey, info.ifac_size ? info.ifac_size : 1)` when either string is
   non-empty. (`TaskInterface` already maps `mode`; no change there.)
   Optionally surface `rnsd.ifaces.<name>.ifac` = on/off in `publishIfaceUp`.

---

## Part C — Feature 1: set interface mode on TCP peers

Per-peer `mode` is already stored (`s.tcp.peers.<id>.mode`) and loaded
(tcp.cpp `loadPeerConfig` ~191) — only the *surfacing* is missing.

**Files:** `iface-tcp/esp-idf/src/tcp.cpp`, `iface-tcp/browser/src/panels/TcpPanel.vue`

1. **CLI** — add `tcp peer mode <slot> <full|gateway|access_point|roaming|
   boundary>` in `cliTcpPeer` (~622), writing `s.tcp.peers.<slot>.mode`; show
   the current mode in `cliTcpStatus` (~548) and in the `-h` help.
2. **Browser** — TcpPanel.vue uses a raw per-peer form (host/port/enable via the
   custom `PeerField`). Add a Mode `q-select`/`SettingSelect` bound to
   `s.tcp.peers.<idx>.mode` with the five-mode option list (copy LoRa's
   `modeOptions`, LoraPanel.vue ~112). Write via the existing
   `device.set(...)`/`sendJson` path.

---

## Part D — Feature 2: IFAC config on all interfaces

Each interface reads its `network_name` (`s.*`) + `passphrase` (`secrets.*`) +
optional `ifac_size`, and fills the new `rnsd_transport_t` IFAC fields at
register time. On change, the iface re-registers (teardown → re-register) so the
new key takes effect — the existing config-change/reload paths already do this.

**Config keys (per-interface, per the chosen model):**
- TCP per-peer: `s.tcp.peers.<id>.ifac_netname`, `secrets.tcp.peers.<id>.ifac_netkey`, `s.tcp.peers.<id>.ifac_size`
- TCP server: `s.tcp.server_ifac_netname`, `secrets.tcp.server_ifac_netkey`, `s.tcp.server_ifac_size`
- LoRa: `s.lora.0.ifac_netname`, `secrets.lora.0.ifac_netkey`, `s.lora.0.ifac_size`
- ESP-NOW: `s.espnow.0.ifac_netname`, `secrets.espnow.0.ifac_netkey`, `s.espnow.0.ifac_size`
- Auto: `s.auto.ifac_netname`, `secrets.auto.ifac_netkey`, `s.auto.ifac_size`

**Firmware (same pattern in each, next to where each fills `reg.mode`):**
- `iface-tcp/esp-idf/src/tcp.cpp` — in `loadPeerConfig`/`attemptConnect`, read
  the per-peer keys into `peer_t` and copy into the `rnsd_transport_t reg`
  (~322). Subscribe `secrets.tcp.peers`/`s.tcp.peers.*.ifac_*` to trigger
  reload+re-register.
- `iface-lora/esp-idf/src/lora.cpp`, `iface-espnow/esp-idf/src/espnow.cpp`,
  `iface-auto/esp-idf/src/auto.cpp` — read their `ifac_netname`/`ifac_netkey`/
  `ifac_size` keys where they build their `rnsd_transport_t`; re-register on
  change.

**Browser (per panel):** add a network-name `SettingText` (`s.*.ifac_netname`)
and a passphrase field for `secrets.*.ifac_netkey`. Because `secrets.*` never
sync to the browser, the passphrase field is **write-only** (a "set passphrase"
input that writes via `device.set`, never reads back a value — show a
placeholder like "•••• set" only if a companion `s.*.ifac_set` flag is exposed;
simplest is a plain write-only text box with helper text). Panels:
`iface-tcp` TcpPanel.vue (per-peer + server section), `iface-lora` LoraPanel.vue,
`iface-espnow` EspnowPanel.vue, `iface-auto` AutoPanel.vue.

---

## Part E — Feature 3: incoming TCP facility

Add an inbound listener to the existing `tcp` task (one task, both directions),
following the `sshd.cpp` server pattern. Each accepted connection becomes an
`inbound_peer_t` (mirrors `peer_t`: net_handle, rnsd_handle, HDLC assembly,
byte counters) registered with rnsd as `tcp_in/<addr:port>`, framed identically.

**File:** `iface-tcp/esp-idf/src/tcp.cpp` (+ TcpPanel.vue, INTERNALS.md)

1. **Config** (defaults already partly seeded ~810): keep `s.tcp.server_enable`
   (0), `s.tcp.server_port` (4965); add `s.tcp.server_mode` (default `gateway`),
   `s.tcp.max_inbound` (default 8), and the server IFAC keys (Part D).
2. **Listener** — in `tcpTaskMain` (~747) also `itsServerInit(...)` +
   `itsServerPortOpen(TCP_PORT_INBOUND, /*packetBased=*/false, max_inbound,
   4096, 4096)` + `itsServerOnConnect/OnRecv/OnDisconnect`. When
   `s.tcp.server_enable`, send a `net_port_msg_t` to `NET_PORT_REG_PORT`
   (`nvsKey="tcp_server_port"`, `defaultPort=4965`, itsPort=TCP_PORT_INBOUND).
   Toggle registration on `s.tcp.server_enable` change.
3. **Accept** — `onInboundConnect(handle,data,len)`: alloc an `inbound_peer_t`
   (cap at `max_inbound`, reject with `-1` when full); read client IP from
   `net_connect_t` (`ipaddr_ntoa`); register with rnsd
   (`tcp_in/<addr:port>`, `reg.mode = s.tcp.server_mode`, server IFAC fields).
   `onInboundRecv` → `itsRecv` + `hdlcConsume`; `onInboundDisconnect` →
   teardown + deregister (close rnsd_handle). Reuse the existing `hdlcSend`/
   `hdlcConsume` helpers.
4. **CLI/status** — extend `cliTcp`: `tcp server start|stop`, show server state
   + inbound peer list/count in `cliTcpStatus`. Publish
   `tcp.server.inbound_count` (ephemeral).
5. **Browser** — add a "Incoming" section to TcpPanel.vue: enable
   `SettingToggle` (`s.tcp.server_enable`), port `SettingText`
   (`s.tcp.server_port`), mode `SettingSelect` (`s.tcp.server_mode`), plus the
   server IFAC fields (Part D), and a live inbound-count display.

---

## Order of work

1. Part A (IFAC core) — foundational; everything else depends on it.
2. Part B (rnsd plumbing) — exposes IFAC + carries the new struct fields.
3. Part C (peer mode surfacing) — small, independent, good first validation.
4. Part E (incoming facility) — new server path.
5. Part D (IFAC config across all five interface surfaces + browser).

---

## Verification

This container has no hardware/serial; flashing + monitoring is host-side via a
`spangap monitor` the user runs (README "Working with a real device"). Plan:

1. **Build (in-container):** `spangap validate` then `spangap build` from the
   workspace. The build runs the staging lint and the browser build; a clean
   build confirms the microreticulum edits compile and CMake wiring is intact.
2. **Flash + boot:** `spangap flash` (signals the host monitor); watch
   `.spangap-log` for the rnsd/tcp task boot lines and an iface register line
   showing the mode (e.g. `register: iface=tcp/0 … mode=full`).
3. **Mode (Part C):** `spangap cli tcp peer mode 0 full` then `spangap cli tcp`
   — status shows `mode=full`; `.spangap-log` shows re-register at the new mode.
4. **IFAC enforcement (Parts A/B/D) — the critical test, needs two endpoints:**
   - Configure a TCP peer to dial a standard **upstream Reticulum `rnsd`** TCP
     server that has IFAC set with a known `network_name`+`passphrase`. Set the
     matching `s.tcp.peers.0.ifac_netname` + `secrets.tcp.peers.0.ifac_netkey`
     on-device. Confirm announces flow both ways (network map / `spangap cli`
     dest list populates) — proves byte-exact interop.
   - **Negative:** change the passphrase on one side only; confirm packets are
     dropped (no announces cross, iface rx counters rise but nothing decodes).
   - **Open-vs-IFAC:** point an IFAC-enabled peer at an open server (and vice
     versa); confirm traffic is rejected per the rules in Part A.5.
5. **Incoming facility (Part E):** `s.tcp.server_enable=1`; from another RNS
   node dial the device's `server_port`; confirm `.spangap-log` logs an accept,
   a `tcp_in/<addr>` register, and `spangap cli tcp` lists the inbound peer.
   Re-run the IFAC matrix against the server using `s.tcp.server_ifac_*`.
6. **Browser:** load the web UI, Settings → Reticulum → Transports → TCP;
   confirm the per-peer Mode selector, the Incoming section, and the IFAC
   network-name/passphrase fields render and persist (passphrase is write-only).

**Correctness risks to watch (from the IFAC design):** Ed25519 must stay
deterministic; the inbound `raw`→`work` follow-through must be complete (else
decode reads masked bytes); mask/skip index ranges must be identical on both
paths (`2..ifac_size+1` skipped); `ifac_size` clamped to 1–64.
