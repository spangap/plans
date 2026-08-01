# RNode endpoint in iface-lora (serial + TCP) and a core serial-handler mechanism

## Context

iface-lora currently has two endpoints: the radio and rnsd (an ITS connection to
`rnsd:RNSD_PORT_IFACE` whose handle is the packet path). This adds a third: an
RNode endpoint, so a stock RNS `RNodeInterface` client can attach to the device
as if it were RNode hardware — over USB serial (the common way RNodes are used)
and/or RNode-over-TCP. Radio commands from the client are executed by writing
the normal `s.lora.<n>.*` keys, so they flow through the existing config path
and re-register with rnsd, coalesced so a burst (freq+bw+sf+…) causes one
re-register. All three endpoints behave as one radio segment: a packet entering
from any one is presented to the other two. RNode-originated packets get a third
LoRaMon `type` value drawn orange on LCD/web graphs, and `lora neighbors` shows
a local `rnode` row next to `us`.

Serial transport requires a new **core mechanism**: a task can register a
serial-port handler for port 0 (the console port — USB-Serial-JTAG, or CDC 0
when the console is on `usb cdc`) or port 1 (the spare CDC port, which today is
created by `cdcOpen` and then owned by nobody). When a client attaches to a
claimed port, the serial machinery disconnects log/cli from it and connects the
port's byte stream to the handler task over ITS; on detach the console returns.
Registering port 1 while only one serial port exists (console on USJ) is an
error. Core stays generic — it knows "this port has a handler", not RNode.

Attach detection: TinyUSB CDC gives per-port DTR edges via
`callback_line_state_changed` (pyserial-class clients assert DTR on open, drop
on close; verified — cdcLineStateCb already uses it on ACM 0). The USJ
peripheral exposes **no line state to software** (no DTR/RTS in the S3 driver,
LL, or register struct; only SOF-based `usb_serial_jtag_is_connected()`), so on
USJ the takeover triggers in-band on the first received `0xC0` byte (KISS FEND —
never a console keystroke, and the first byte of every RNode detect burst), and
releases on handler disconnect (CMD_LEAVE) or `usb down`.

`plans/adaptive-power.md` §9 sketches this feature. Protocol reference:
`/home/spangap/rns-ref-venv/lib/python3.12/site-packages/RNS/Interfaces/RNodeInterface.py`.

Protocol facts that shape the design (verified in RNodeInterface.py):
- Serial is the client's default transport; RNode-over-TCP dials port 7633,
  hardcoded (`TCPConnection.TARGET_PORT`). A `tcp://host:port` config URI does
  **not** override it — the whole suffix is handed to `getaddrinfo` as a
  hostname and resolution fails. Both transports carry identical raw KISS.
- Connect burst: `CMD_DETECT (0x08, payload DETECT_REQ 0x73)`,
  `CMD_FW_VERSION`, `CMD_PLATFORM`, `CMD_MCU`. Host must answer detect with a
  CMD_DETECT frame carrying `DETECT_RESP 0x46` (any other payload actively
  clears the client's `detected` flag; ≤5 s on TCP), fw version ≥ 1.52 — else
  the client `RNS.panic()`s, which is `os._exit(255)` — then platform + mcu
  bytes. Platform ESP32 arms the client's `CMD_RESET 0xF8 → IOError` teardown
  and (with NRF52) unlocks framebuffer methods, which makes `detach()` emit a
  framebuffer-disable frame → report `PLATFORM_AVR (0x90)`. FW: 1.78.
- **TCP keepalive**: over TCP the client re-sends the entire 4-command detect
  burst every 3.5 s of TX idle, for the life of the connection. Handshake
  replies must be stateless and repeatable.
- Config burst: `CMD_FREQUENCY`/`CMD_BANDWIDTH` (4-byte big-endian unsigned,
  Hz), `CMD_TXPOWER`/`CMD_SF`/`CMD_CR` (1 byte: dBm / SF / CR denominator),
  `CMD_ST_ALOCK`/`CMD_LT_ALOCK` (2-byte big-endian, percent×100 — only sent
  when the client config sets airtime limits), then always `CMD_RADIO_STATE
  0x01` last. The client then sleeps **0.25 s (serial) / 1.5 s (TCP)** and
  validates the *echoed* values: bw/txpower/sf/state are compared
  unconditionally (absent echo = mismatch); **frequency echo is optional** —
  absent passes, present must be within ±100 Hz; CR never validated. On
  mismatch the client closes the port and re-runs the full handshake every
  5 s, forever — a churn loop, not a one-shot abort.
- Data: `CMD_DATA` frames both ways; host optionally precedes each with
  `CMD_STAT_RSSI` (rssi+157) and `CMD_STAT_SNR` (snr×4, signed). Stats are
  sticky attributes applied to the *next* data frame and cleared after it —
  they must precede the CMD_DATA they describe, and the client's Transport
  drops SNR unless RSSI was also set. Client HW_MTU is 508 with silent
  truncation (RNS_MTU 500 fits). The client never flushes a stalled partial
  command frame — the host must write whole KISS frames atomically. A client
  configured with a beacon may inject unsolicited ≤32-byte callsign CMD_DATA
  frames (non-Reticulum payload; it just airs).
- `CMD_READY` after each completed client-originated TX releases the client's
  queue — harmless when flow control is off, **mandatory** when the client
  enables it (always send it).
- Never send `CMD_STAT_RX 0x21` / `CMD_STAT_TX 0x22` (the client handler
  calls `ord()` on an int → TypeError → interface offline) nor `CMD_ERROR
  0x03/0x04` (unhandled → "Unknown hardware failure" IOError). `CMD_ERROR
  0x01` → IOError → the 5 s reconnect loop.
- Client `detach()` sends `RADIO_STATE OFF` then `CMD_LEAVE` then closes
  (0.5 s grace before the TCP socket close) — a literal OFF→`enable=0`
  mapping would let every clean client shutdown take the radio down for rnsd;
  see the deferred-off design (E). `TCPConnection.write` buffers frames while
  disconnected and flushes them on the next successful write, so a freshly
  accepted socket may carry stale pre-drop frames — onConnect resets decoder
  state and the KISS decoder resyncs on FEND.

## Files

- `spangap-core/esp-idf/src/cli.cpp` + `usb_ports.cpp` + `log.cpp`
  (+ `include/cli.h`) — serial-handler registry, takeover/release, idle-loop
  restructure, CDC 1 pump, port-count publish, mirror-gate flag
- `iface-lora/esp-idf/src/lora.cpp` — everything RNode (new banner section)
- `iface-lora/straddle.yaml` — settings rows
- `iface-lora/esp-idf/conditional/spangap-lcd/src/loramon_lcd.cpp` — LCD color
- `iface-lora/browser/src/panels/LoraMonWindow.vue` — web color
- `spangap-core/docs/cli-internals.md`/`logging-internals.md`,
  `iface-lora/INTERNALS.md`, `iface-lora/README.md` — docs

## A. Core: serial-handler registration (spangap-core)

API in `cli.h`, generic wording (no consumer references):

- `bool serialPortClaim(int port, const char* task, uint16_t itsPort)` /
  `void serialPortRelease(int port)`. Port 0 = the console port (USJ or CDC 0);
  port 1 = second CDC port. Claiming 1 while the console is on USJ (one serial
  port) fails with an error return + warn. One handler per port. Registry lives
  with the serial machinery in cli.cpp; claims are posted as volatile flags the
  serial task polls (the `consoleSwitchPending` pattern — the existing
  cross-task mechanism here is bare volatile bools, and this stays with it).
- Core publishes `sys.usb.serial_ports` (1 or 2) — **new**, written inside
  usb_ports.cpp from `usb cdc`/`usb jtag` switching (`switchConsole` is
  static; note `consoleCdcPortCount()` returns 0 on USJ, so the publish does
  its own USJ→1 / CDC→2 mapping) — so a claimant can (re)apply its claim when
  the transport changes.
- **Idle-loop restructure** (prerequisite): in log mode the serial task parks
  in `usb_serial_jtag_read_bytes(portMAX_DELAY)` (USJ — fine, port-0 attach
  there is in-band) or a plain `delay(50)` on CDC that ignores notifications,
  and with a CLI session open the loop never services port 1. The CDC idle
  and session branches switch to a notification-waited delay
  (`ulTaskNotifyTake` with timeout); TinyUSB `callback_rx` on a claimed port
  does `xTaskNotifyGive` to the serial task; the port-1 pump runs in both
  branches. Shuttle reads are block reads (`tinyusb_cdcacm_read`), not the
  1-byte `consoleCdcRead` path — the client's 250 ms serial echo window
  leaves no room for 50 ms-per-byte polling.
- **Attach**: on a claimed port, when a client is detected —
  - CDC (either port): DTR rise via per-port `callback_line_state_changed`
    (extend `cdcOpen` to install the callback on ACM 1 too). The callback's
    edge state (`prevRts`/`prevDtr`/`resetPending`) becomes per-port (its own
    comment already demands this before ACM 1 grows a callback). **While a
    port is claimed its esptool reset arming is disabled**: the existing
    `armed = prevRts && !prevDtr` + falling-RTS → `esp_restart()` logic fires
    on a normal pyserial close (DTR drops before RTS) — without this a clean
    client exit reboots the device. Trade-off: esptool auto-reset is
    unavailable on a claimed port.
  - USJ (port 0 only): first received `0xC0` byte in log mode (`handleChar` —
    a lambda inside `serialTaskFn`, so the handler state lives in the same
    closure — gains this check before the CLI-entry branch; the byte is
    forwarded on).
  - The serial task then: for port 0, returns to log mode if a CLI session is
    open and sets a new `serialInHandler` flag that suppresses both the log
    stdout mirror and CLI entry. `serialInHandler` is a third *variable*
    alongside `serialInCli` and `cliHandle` (which deliberately diverge —
    trailing-`;` commands clear `serialInCli` mid-session); both log.cpp
    mirror gates (logVprintf and the inbound-line echo) gain the new flag.
    For port 1, no console interaction.
    Then `itsConnect(task, itsPort, payload, …)` with a small connect struct
    `{uint8_t serialPort;}`. If the connect is rejected (handler busy), no
    takeover happens — port 0 stays a console.
  - Fix en route: `cliSerialResumeLog` only latches
    `cliUsbSerialAutoResumeLog`, and on the USJ path the idle branch never
    clears it — a call while already in log mode kills the *next* CLI
    session. Clear the flag in the idle branch; attach then need not
    special-case it (`switchConsole` already trips this today).
- **Shuttle**: serial task pumps bytes port↔ITS handle (port 0 reuses its
  existing read paths — USJ ring or block ACM 0 reads; port 1 adds ACM 1
  block reads driven by `callback_rx` notifying the task). Stream-mode
  `itsSend` can partial-write on timeout: the pump carries the unsent
  remainder forward — bytes are never dropped mid-stream.
- The serial task's `itsClientInit(1)` becomes `itsClientInit(2)` — a handler
  connection must coexist with the `cli:1` connection (port 1 case).
- **Release**: DTR drop (CDC — new logic in the per-port callback; today's
  `cdcLineStateCb` does reset detection only), handler-side `itsDisconnect`
  (lora drops the session on CMD_LEAVE), `usb down` (`cliUsbSerialLinkDown`
  is set only by that command — despite its comment it does not cover
  unplug), or `usb jtag` teardown (port 1 session dropped, claim goes
  dormant; re-armed when `sys.usb.serial_ports` returns to 2 and the claimant
  re-claims). On release of port 0 the console returns to log mode.

## B. lora: config-apply coalescing

Replace immediate `s_configDirty` apply with a pending deadline: arm-once —
later changes never push it out (storage.cpp:832-843 documents why an
immovable deadline is required: a busy device would otherwise starve the
apply) — plus an urgent pull-in, which is a new policy on its own merits:
shortening a deadline cannot starve, and the client's 0.25 s validation
window requires it.

- lora.cpp:813: `s_configDirty` → `s_cfgPend` + `s_cfgDueTick`;
  `#define LORA_CFG_COALESCE_MS 300`.
- `cfgArm(delayMs)`: keep the earlier of the requested and any pending
  deadline; `xTaskNotifyGive(s_task)`.
- `onCfgChange` (:3990) → `cfgArm(LORA_CFG_COALESCE_MS)`.
- :3080 — rfprobe restore-failure (radio left in sweep config, hardware
  recovery, nothing to coalesce) → `cfgArm(0)`.
- Loop entry :4926 → pend now, due now. Apply block :4928 fires when due:
  applyConfig + loraPublishDisplay per radio, then `rnodeEchoFlush()` and
  `rnodeApplyTransports()`.
- `nextDeadline()` (:4767): add an `s_cfgPend` clause next to `s_statsPend`.
- `CMD_RADIO_STATE` from the client calls `cfgArm(0)`: the config burst always
  ends with it, so the apply+echo happen immediately after the burst — inside
  the serial client's 0.25 s validation sleep. Coalescing still collapses the
  burst (the earlier writes armed 300 ms and nothing applied yet). CLI/web
  bursts without a terminator get the plain 300 ms window; either way a burst =
  one radioStop/radioStart = one `registerWithRnsd` (when the first attempt
  succeeds — the task loop retries a failed registration at 1 Hz).

## C. lora: TX origin threading

- `LoraRadio` (struct at :670): `bool txOurProto[2]` (:770) → `uint8_t
  txType[2]` holding `LORA_PKT_*`; new `#define LORA_PKT_RNODE 2` next to
  RNS/OURS (:76), plus `LORA_ORIG_RNSD/RNODE`.
- `beginTx` (:2850) gains `uint8_t origin`; sets `txType` per frame:
  `LORA_PKT_OURS` for the 0x04 prefix (:2871), else per origin
  (:2879/:2887/:2891). `probeStartTx` :2996 → OURS. `drainOneOutbound` :3909
  passes RNSD. Origin also passed to `neiObserve`.
- `serviceRadio` TX_DONE (:3925): `doneOurs` bool → `doneType` byte; loraMonPush
  `type = doneType`. The :3930 length adjustment strips the 1-byte RNode
  seq/split header that non-OURS frames carry on air; rnode-origin frames go
  out through the same framing as rnsd frames, so the adjustment keys on
  `== LORA_PKT_OURS` (RNS and RNODE both subtract 1).
  New `r->txFromRnode` (set in beginTx, cleared in txRearmRx/radioStop): on
  whole-packet completion send `CMD_READY` to the client.

## D. lora: settings, transports, ITS server port

One RNode, so a global settings group (not per-radio):

- `s.lora.rnode.enable` (0/1, default 0) — master switch
- `s.lora.rnode.radio` (default 0) — which radio the endpoint exposes
- `s.lora.rnode.serial` (default -1) — serial port to claim; -1 = no serial
- `s.lora.rnode.tcp` (default 7633) — TCP listen port; -1 or 0 = no TCP
  (7633 is the only port a stock client can dial); later also `.bt`
- straddle.yaml: switch row for `.enable`; the rest seeded in
  `LoraService::onInit` (`LORA_VERSION` 3→4). The existing
  `storageSubscribeChanges("s.lora", …)` prefix subscription already covers the
  group — changes land in the coalesced apply pass.

`rnodeApplyTransports()` (called from the apply pass — which also satisfies
net's constraint that the registration arrive from the endpoint's own task):
- TCP: two steps, per sshd.cpp:97-123 (`registerEndpointOnce` +
  `applyListenerState`): a one-time `itsSendAux("net", NET_PORT_REG_PORT,
  &reg, sizeof reg, …)` with `net_port_msg_t{itsPort=RNODE_ITS_PORT (0x524E),
  tcpNoDelay=1, keepAlive=1, backlog=1, nvsKey="rnode_port", defaultPort=0}`
  (zero-init the rest — `tcpPort`/`tls`/`ownPort` 0 means config-driven via
  `s.net.rnode_port`); then `storageSet("s.net.rnode_port",
  enable && tcp > 0 ? tcp : 0)` as the open/close driver.
- Serial: `serialPortClaim(serial, "lora", RNODE_ITS_PORT)` /
  `serialPortRelease` per the setting; claim failure (port 1 on a one-port
  console) → warn + publish. `NOW_AND_ON_CHANGE("sys.usb.serial_ports", …)` to
  re-apply when the console transport switches.
- Radio binding: `s.lora.rnode.radio`; if it changes (or rnode disabled) while
  a client is connected → disconnect the client.

Server port in `loraTaskMain`: `itsServerInit()` **before** the existing
`itsClientInit` at :4866 (lora has no server today; its.h:210-211 — the first
init call sizes the shared inbox) + `itsServerPortOpen(RNODE_ITS_PORT,
/*packetBased=*/false, /*maxHandles=*/1, 4096, 4096)` +
onConnect/onRecv/onDisconnect. Both net (TCP client) and core-serial (serial
client) connect here; onConnect discriminates by connect-payload length —
net sends `net_connect_t`, core-serial the 1-byte `{uint8_t serialPort}`
(the only discriminator available). `maxHandles=1` plus an explicit reject in
onConnect enforces the single session — a serial takeover attempt while a TCP
client is attached is refused, so the console isn't disturbed. Callbacks run
on the lora task via itsPoll.

Session state: one static `RnodeState` — handle, bound radio, KISS decoder
(inFrame/escape/cmd/buf[RNS_MTU+8]/len/overflow), one parked decoded packet
`txPkt[RNS_MTU]/txLen`, `echoPend`, `offPend`, `txAlternate`. onConnect: reject
if session exists, disabled, or radio stopped (`s_stop`); else reset state,
bind radio. onDisconnect: clear handle, drop txPkt, cancel offPend/echoPend.
`rns stop` also disconnects the client.

## E. lora: KISS decoder, command execution, echoes

- `rnodePump()` — from onRnodeRecv and once per task pass (next to the
  drainOneOutbound call :4963): while no packet parked and bytes available,
  itsRecv small chunks → `rnodeByte()` state machine (FEND framing, FESC
  unescape, overflow-swallow-to-FEND). The ITS ring is the inbound queue: the
  pump stalls while `txLen != 0`.
- `rnodeFrame()` at frame close; storage writes bracketed storageBegin/End:
  - DETECT→`DETECT_RESP 0x46` reply — sent for **every** CMD_DETECT (the TCP
    client re-detects every 3.5 s idle; handshake replies are stateless);
    FW_VERSION→`01 4E`; PLATFORM/MCU→`0x90` (AVR).
  - FREQUENCY/BANDWIDTH (range-checked), SF 5..12, CR 5..8 → storageSet on the
    bound radio's keys; TXPOWER clamped to 22 (chip ceiling, warn). Each calls
    `rnodeCfgTouched()`: `echoPend = true; cfgArm(LORA_CFG_COALESCE_MS)` —
    self-arms even when every write was a no-op, so the echo always fires.
  - RADIO_STATE `0x01` → `enable=1`, touched, then `cfgArm(0)` (burst
    terminator — immediate apply+echo, see B); `0x00` → set `offPend` only,
    touched, `cfgArm(0)`; `0xFF` (ASK) → echoPend + `cfgArm(0)`.
  - ST/LT_ALOCK: echo back zero, don't enforce (airtime governance stays
    LBT/APPC + rnsd's announce cap; the client parses but never validates
    these echoes).
  - LEAVE: cancel offPend, drop txPkt, drop the session (itsDisconnect — which
    also releases a serial port back to the console).
  - DATA: `0 < len ≤ RNS_MTU` → park in txPkt.
  - Anything else: ignore. Never emit 0x21/0x22 stat frames, CMD_ERROR
    0x03/0x04, or spontaneous CMD_RESET (client-side traps — see Context).
- `rnodeEchoFlush()` (end of the coalesced apply pass): emit
  FREQUENCY/BANDWIDTH/TXPOWER/SF/CR from the **applied** state (`r->cfg*`
  fields + frequency key) and RADIO_STATE from `r->running` — bw/txp/sf/state
  are the mandatory echoes; frequency is optional-but-must-be-exact (we send
  it from applied state, which is exact); CR is harmless — via
  `rnodeSendCmd(cmd, payload, n)` KISS encoder. If ON was requested but the
  radio failed to start, also `CMD_ERROR 0x01` so the client tears down (and
  re-dials every 5 s). Each frame goes out in one `itsSend` (the client never
  flushes a stalled partial command frame), with whole-frame space checked
  first — a stream-mode partial write would corrupt the KISS stream.
- Deferred radio-off: at the apply deadline `offPend` still set →
  `storageSet(enable, 0)` for real. `detach()`'s OFF+LEAVE+close cancels it —
  the radio stays up for rnsd. A client that turns the radio off and *stays
  connected* is honored.

## F. lora: three-way bridging

Fan-out rule: "presented to the radio" = transmitted (CSMA-gated); the fan-out
point for locally-originated packets is `beginTx`, so a packet the LBT valve
drops never aired and is bridged nowhere — correct segment semantics.

- **Client → radio + rnsd**: `drainOneOutbound` (:3838) becomes a two-source
  drain: rnsd bytes (existing) and `s_rnode.txPkt` (when bound to this radio).
  The restructure is real, not cosmetic: today `ready` requires
  `rnsdHandle >= 0` and `avail == 0 → csmaResetAccess(r); return` fires
  early — with a parked rnode packet and no rnsd bytes that would wipe CSMA
  progress every pass (the failure the `hashTxPending` comment :3839-3843
  documents). Compute availability across **both** sources up front; the
  idle reset fires only when neither has data; the rnode source gates on
  `r->running` alone (no rnsd handle needed). Wait-clock keys on either
  source. Both pending → alternate via `txAlternate`. LBT-timeout drop of the
  rnode packet still sends `CMD_READY` (don't wedge a flow-controlled
  client). In `beginTx`: origin RNODE → `rnsdInject(r, data, len,
  RNODE_INJ_RSSI, RNODE_INJ_SNR10)`.
- `rnsdInject()`: factor the tail of `deliverInbound` (:2324-2337 — 4-byte
  big-endian rssi|snr10 prefix + itsSend to rnsdHandle; the `neiObserve` tap
  at :2323 stays caller-side); `deliverInbound` calls it with real readings.
  Synthetic signal for injected packets: rssi −10 dBm, snr 10.0 dB —
  top-of-scale "perfect local", impossible over the air, so unmistakable in
  signal views.
- **rnsd → client**: in `beginTx`, origin RNSD → `rnodeForwardData(r, data,
  len, withStats=false)` (no stat frames for our own TX; stats are optional).
- **Radio → client**: `deliverInbound` (:2316) also calls
  `rnodeForwardData(..., withStats=true)` — STAT_RSSI + STAT_SNR (both, in
  that order, *before* the data frame — stats are sticky client-side and SNR
  is dropped without RSSI) from `r->rssiLast/snrLast`, then the KISS-escaped
  CMD_DATA frame (buffer `2*RNS_MTU+4`, worst case ~1 KB). Forwarding is
  all-or-nothing: check ITS free space for stats+data up front and skip the
  whole packet (warn) if it can't fit — a partial stream write corrupts the
  KISS framing. Only reassembled non-`ours` packets reach deliverInbound, so
  the client sees exactly the Reticulum traffic.
- `nextDeadline()` outbound clause (:4812-4827): today one conjunction
  (`rnsdHandle >= 0 && itsBytesAvailable(...)`) — split gating from
  availability and include the rnode source (parked packet or client bytes,
  without requiring rnsdHandle ≥ 0), else a parked packet never wakes the
  loop.

## G. lora: neighbors — local `rnode` row

- `Neighbor` (:140): add `bool isRnode`; helper `neiIsLocal(e) = isUs||isRnode`.
- Thread `txOrigin` through `neiObserve` (:1574) → `neiAnnounce` (:1484):
  :1533 becomes origin-split (`isRnode` vs `isUs`); the identity-merge loop
  :1538-1541 merges per-flag (the client's identities fold into one `rnode` row
  as ours fold into `us`); claim-fold guard :1549 → `!neiIsLocal`; HEADER_2
  rebroadcast tx branch (:1653-1671) gets the same split. `neiMergeInto`
  (:1370): merge `isRnode` too at the :1407 fold.
- Switch RF-layer "terminates at our transmitter" guards to `neiIsLocal()`:
  eviction protection, adaptive-power skips (:2764/:2815/:3139/:3236/:3819),
  the own-hash cluster (`neiOwnHashCount` :3256, :3282, :3323, :3368,
  `probeOwnFirst4` :3431-3437), announce-count/hash-advert walks
  (:4434/:4452/:4476), **and the five `neiDestIsUs` call sites**
  (:1692/:1728/:1745/:1773/:2732 — relay detection and next-hop selection;
  packets destined to the client's identities also terminate at our radio).
  Keep plain `isUs` where it means our own identities.
- CLI (`cliPrintNeighbors` :4324, `neiWalk` :4196, `neiPrintNode` :4212): count
  nUs/nRnode/nNodes; header suffix "and us" / "and rnode" / "and us + rnode";
  walk pass-0 emits local rows (numbered 0, never `lora rf` targets); label
  `"rnode"`; capability-line skip guard → `neiIsLocal`.

## H. LoRaMon orange (LCD + web)

Color `#E89040` — between the existing yellow `#E8D040` and red `#E84040`.

- Firmware: covered by `LORA_PKT_RNODE` + C (record format unchanged; both
  parsers already tolerate a third `type` value).
- LCD `loramon_lcd.cpp`: `C_RNODE` in initColors (:76-86); three-way branch at
  :249; legend recolor string ~:327 gains ` #E89040 rnode#` (grow the
  `char b[96]` — the added 15 chars overflow it, and truncation would show as
  a silently missing legend entry, not an error).
- Web `LoraMonWindow.vue`: `C_RNODE` const (:72), three-way at :222, legend
  span + `.c-rnode` CSS (:449).

## I. Docs

- `spangap-core/docs/cli-internals.md` (+ logging-internals.md §4-5 cross-ref):
  the serial-handler registry, the `serialInHandler` flag beside
  `serialInCli`, the DTR-vs-FEND attach triggers and why USJ has no line
  state, per-port line-state edge tracking + reset-arming suppression on
  claimed ports, the notification-waited idle loop, `sys.usb.serial_ports`.
  Note: both CDC interfaces share USB string index 4 — a host cannot tell
  ACM 0 from ACM 1 by name.
- `iface-lora/INTERNALS.md`: new section "RNode endpoint" (protocol answers
  incl. AVR-platform rationale — the ESP32-only reset path, not display
  polling; serial 0.25 s vs TCP 1.5 s echo validation and the
  RADIO_STATE-terminated coalescing; the 3.5 s TCP re-detect keepalive and
  stateless handshake; mismatch = 5 s reconnect churn; TCP port 7633;
  bridging model + "bridged iff it aired"; deferred-off/LEAVE cancel;
  single-session policy + payload-length discrimination; synthetic injection
  signal; shared-channel caveat). Update §7 (third endpoint), §9 (coalescing
  rewrite), §11 (version 4, rnode group), §12 (type value 2 / txType), §13
  (rnode row / neiIsLocal), §16 pitfalls (detach trap; txpower-clamp
  reconnect churn; 0x21/0x22 client crash).
- `iface-lora/README.md`: "Using the device as an RNode" — settings, serial and
  `tcp://` client config snippets, warning that client radio settings
  reconfigure and persist the device's channel.

## Implementation order

A core serial-handler → B coalescing → C origin threading → D settings/ports →
E decoder/handshake → F bridging → G neighbors → H colors → I docs.
(B–H depend only on A for the serial transport; TCP works without A. A's
largest pieces are the idle-loop restructure and the per-port line-state /
reset-arming rework — they land first within A.)

## Verification

Build-only (I build; user flashes):
- `spangap build` for the active target (reticulous/reticulous --with
  spangap/hw-lilygo-tdeck) — covers core, firmware, and the LCD-conditional
  `loramon_lcd.cpp`. State the board after each build.
- Browser SPA build for the Vue change.
- Grep: no remaining `txOurProto` / bare `s_configDirty`.

On-device (user-driven, with `/home/spangap/rns-ref-venv`):
1. `s.lora.rnode.enable=1`, `.tcp=7633` → net listens; `.serial=0` → console
   port claimed (log/cli still work until a KISS FEND arrives); `.serial=1` on
   USJ console → claim error warned; after `usb cdc` → claim succeeds.
2. TCP: RNS client `port = tcp://<device>` — detect, "firmware 1.78", radio
   reporting lines, "configured and powered up"; device log shows one radio
   begin and no re-registration caused by the burst itself (the 1 Hz retry
   may add attempts if rnsd was momentarily away). Leave the client idle
   >4 s → keepalive detect bursts answered, connection stays up.
3. Serial: RNS client `port = /dev/ttyACM0` (or ACM1 for port 1) — same, and
   the echo arrives inside the 0.25 s serial validation window (RADIO_STATE
   pulls the apply). Console resumes on client exit (DTR drop on CDC; LEAVE on
   USJ). **Closing the pyserial client must not reboot the device**
   (reset-arming suppressed on the claimed port).
4. Traffic: client announce → orange TX bars in LoRaMon (LCD + web), packet on
   air and in device rnsd; `lora neighbors` header "… and us + rnode", local
   `rnode` row. Third RF node announce → client logs it with RSSI/SNR;
   device-rnsd announce → client receives it, stays yellow locally.
5. CLI burst `lora 0 freq …; lora 0 bw …; lora 0 sf …` → one restart.
6. Detach: stop the client cleanly → radio stays up (offPend cancelled),
   reconnect works; second concurrent client rejected; client `txpower=23`
   fails validation and re-dials every ~5 s — expect the churn loop
   (documented), not a single abort.

## Decisions taken (flag if you disagree)

- USJ port-0 attach trigger is the in-band KISS FEND byte (no DTR exists on
  USJ — verified in IDF driver/LL/registers); CDC ports use DTR. Console on a
  claimed port 0 therefore stays usable until an RNode client actually speaks.
- A claimed CDC port disables esptool auto-reset arming while claimed — the
  alternative is a device reboot on every clean pyserial close.
- Handshake replies are stateless and repeat on demand (TCP re-detects every
  3.5 s idle).
- `RADIO_STATE OFF` deferred by the coalesce window and cancelled by
  LEAVE/disconnect — a clean client shutdown does not take the radio down.
- TX power > 22 clamped and honestly echoed → such a client fails validation
  and reconnect-churns every ~5 s (no lying; docs warn).
- ST/LT airtime locks echoed as zero, not enforced (parsed, never validated).
- Client-set channel params persist in NVS (the "set s.lora vars" requirement)
  — survives reboot, overwrites operator settings; docs warn. This is a
  deliberate reversal of adaptive-power.md §9's "reject the change or migrate
  rnsd to a fresh interface" options; §9's µR peer-ident idea stays out of
  scope for v1.
- RNode-origin traffic bypasses rnsd's announce cap (the client's own stack
  governs it); LBT/APPC still gates airtime.
- Platform AVR / fw 1.78 (sidesteps the client's ESP32-only reset teardown and
  framebuffer unlock).
- One session at a time across transports; serial takeover is refused while a
  TCP client is attached (console undisturbed).
