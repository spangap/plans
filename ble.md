# Bluetooth Low Energy: a stack-owner straddle, an RNode door, and an RNS interface

## Status — planned 2026-08-17; implemented and field-tested 2026-08-18

Three new straddles: `spangap/spangap-ble` (owns the radio and the host stack),
`reticulous/rnode-ble` (a Bluetooth door onto the RNode endpoint that already
exists in iface-lora), `reticulous/iface-ble` (RNS over the ble-reticulum v2.2
protocol, so Columba phones and Linux `BLEInterface` nodes join the mesh
directly). Plus one small change to `iface-lora`.

All three straddles exist and each carries its own README and INTERNALS —
**those are the authoritative descriptions now**; the body of this plan is the
design rationale that produced them, and the section below is what remains to
be built. `iface-ble` is field-tested against Columba on a LilyGO T-Deck
(LXMF both ways). `rnode-ble` is written but has never been exercised against
a real client.

## To do

1. **RNode over BLE cannot work until spangap-ble has
   resolvable-private-address privacy.** — **Built 2026-08-19**, with the
   sketch below implemented in esp-nimble's HOST-based privacy mode rather
   than the controller's: the S3's controller-side engine generates RPAs the
   host can never read (and only once the resolving list is non-empty at all,
   which a bond-less mesh node never reaches), while host-based privacy sets
   each RPA through the ordinary random-address route — so the existing
   rotation machinery (per-disconnect, 15-minute idle, `bleOwnAddr`,
   `BLE_EV_UP` refire) carried over intact, just deriving from the IRK.
   Espressif's Kconfig gates that mode to the original ESP32 (whose
   controller lacks LL privacy, so there it is forced on); spangap-ble
   predefines the MYNEWT values at project scope
   (`esp-idf/project_include.cmake`) to use it on the S3. The IRK persists in
   `bonds.bin` (header grew a field; pre-privacy bond files are dropped on
   upgrade, correctly — they were keyed to dead addresses), and the identity
   a bond records is the chip's public address, stable for free. The
   election caveat below was ACCEPTED as-is: the coin flip is bounded per
   rotation window, and the Columba stale-state fix (item 2) retires its
   cost. The acceptance test below is still owed — a bonded phone across a
   rotation and across a reboot.

   The original analysis, for the rationale:

   The adapter address is a fresh
   non-resolvable private address at every host start, after every disconnect
   and at latest every 15 minutes while idle — deliberately, so
   mesh peers' per-address state (Columba keys dedupe sets, role flags and MTU
   maps by our address, and merges every new connection into whatever stale
   row a missed disconnect left) can never wedge us twice; phones mint a fresh
   address per connection attempt, and per-disconnect rotation is the same
   protection. But the RNS
   `RNodeInterface` client connects strictly to the address the phone bonded,
   so every bond points nowhere within minutes. The fix is BLE privacy proper:
   a persisted identity resolving key distributed at bonding, the adapter
   address becoming a resolvable private address over it, and every
   advertise/scan/connect call carrying the privacy own-address type — bonded
   peers then resolve every rotation while strangers still see a fresh
   address. Build it when rnode-ble is next up for real testing; the IRK must
   persist in the bond store, since a fresh IRK per boot strands bonds exactly
   the way rotation does.

   How, concretely — on the order of a hundred lines, all in spangap-ble plus
   one touch in iface-ble:
   - **IRK**: generate 16 random bytes once, persist them alongside the bonds
     in `/state/ble/bonds.bin` (same load/flush path, same
     write-`.new`-then-rename), and hand them to the host at every start with
     `ble_hs_pref_irk_set()` — before advertising begins, so the first RPA is
     already derived from it. NimBLE distributes the IRK during SMP key
     exchange on its own once it is set.
   - **Address type**: `own_addr_type` becomes `BLE_OWN_ADDR_RPA_RANDOM_DEFAULT`
     (identity = a random static address, kept as the stable identity behind
     the RPAs) in all three places spangap-ble uses it — the advertising
     params, `ble_gap_disc()`, `ble_gap_connect()`. The host then regenerates
     the RPA on its own timer; the explicit 15-minute `addrGenerate()`
     rotation is deleted rather than kept alongside.
   - **Caveat — the election loses its thumb on the scale.** Today's
     non-resolvable address (top bits 00) sorts below every phone's RPA (01),
     so iface-ble always wins the who-dials election against phones — the
     direction that survives Columba's stale-state bug. An RPA of our own is
     01-prefixed like theirs, making the election a coin flip per rotation,
     and the losing intervals hand the phone the poisoned direction for up to
     15 minutes. Either Columba's bug is fixed upstream first, or the mesh
     must keep a non-resolvable address while only the door goes private —
     which one legacy advertising instance cannot do (one address for both
     payloads). This tension is unresolved; resolve it before building.
   - **Election input**: iface-ble compares adapter addresses to decide who
     dials, and under RPA the address on the air is no longer what
     `bleOwnAddr()` returned at host start. The current RPA must be read from
     the host when it changes (NimBLE exposes no clean getter — read it back
     after advertising starts, or hook the host's RPA timer) and `BLE_EV_UP`
     re-fired exactly as the rotation does today, so the consumer contract —
     re-read on every `BLE_EV_UP` — is unchanged.
   - **Acceptance**: the test that matters is a phone bonded to the RNode door
     staying attached, or reattaching unaided, across an RPA change and across
     a reboot — both legs, because the failure modes differ: a rotation
     exercises resolution by IRK, a reboot exercises the IRK's persistence,
     and an IRK that silently failed to persist strands every bond in a way
     that only shows up two reboots later. Columba's mesh side must
     simultaneously keep treating us as new-address-same-identity, which is
     the property the whole scheme exists to preserve.
2. Upstream report to Columba (the full mechanism chronicle is iface-ble
   INTERNALS' appendix): the Python `_check_duplicate_identity` gate killing
   every reconnect for ~30 s after a teardown whose disconnect callbacks were
   lost (`stopImmediate()` clears without notifying Python; Android `close()`
   fires no callbacks), judging the dead session alive from a stale entry in
   its own `peers` dict; the Kotlin bridge's address-keyed stale-row merge
   that only static-address peers can ever hit; and the connection back-off
   whose failure counter is never persisted (a `data class copy` held in a
   local, then `scanner.removeDevice`), so success-then-death loops redial at
   full 5 s scan cadence forever.
3. `microreticulum` logs "No cache directory, creating…" against deliberately
   no-op file stubs on every boot — cosmetic, quiet it.

Every claim below marked "verified" was checked against the named source at
plan-revision time. File:line references are into this workspace.

## Context

`CONFIG_BT_ENABLED` is unset in every build today (verified: no authored
`CONFIG_BT_*` symbol anywhere in the workspace; the only hits are IDF-generated
"is not set" lines in `reticulous/esp-idf/sdkconfig:683`). Turning it on buys
two unrelated features that nevertheless share one radio, one host stack, one
advertising budget, one connection budget and one security policy — which is
why the stack gets an owner straddle of its own, the way spangap-net owns the
Wi-Fi radio and iface-espnow merely gates on it.

Every board in the workspace is an ESP32-S3, so there is **no Bluetooth Classic
radio**: both features are Bluetooth Low Energy. RNode's classic-serial path
(`BluetoothSerial`, RFCOMM, SSP pairing) is ESP32-original only; every S3 target
in RNode's own firmware sets `HAS_BLE` and runs the Nordic UART Service (NUS)
instead.

### Wire — RNode door (`rnode-ble`)

```
Sideband / RNS RNodeInterface (central)         device (peripheral)
  |                                              advertising, connectable,
  |                                              name "RNode xxxx"
  |  ---- connect (to a BONDED address) -------->
  |  ---- discover 6e400001-…-e50e24dcca9e ----->
  |  ---- write CCCD on …0003 (subscribe) ------>
  |  ---- exchange MTU, requests 512 ----------->
  |         (client goes online ONLY after MTU success)
  |  ---- write KISS bytes to …0002 ------------>   → ITS stream → lora:0x524E
  |  <--- notify KISS bytes on …0003 -----------    ← ITS stream ← lora:0x524E
```

(CCCD = Client Characteristic Configuration Descriptor — the standard
descriptor a central writes to enable notifications.)

### Wire — RNS interface (`iface-ble`)

```
lower BLE address (central)                     higher BLE address (peripheral)
  |  scan, filter service 37145b00-…-c5da28e3          advertising that service
  |  ---- connect ----------------------------->
  |  <--- READ Identity char …28e6 (16 B) ------
  |  ---- write CCCD on TX char …28e4 ---------->
  |  ---- WRITE 16 B to RX char …28e5 ---------->   identity handshake
  |  ---- WRITE fragments to RX ---------------->   [type|seq16|total16|payload]
  |  <--- NOTIFY fragments on TX ---------------
  |  ---- WRITE 1 B 0x00 keepalive every 15 s -->   (on TX idle)
```

### Protocol facts that shape the design

RNode door — verified in `/home/spangap/rns-ref-venv/lib/python3.12/site-packages/RNS/Interfaces/Android/RNodeInterface.py`
(`class BLEConnection`, :1626-1880) and in RNode_Firmware `BLESerial.{h,cpp}`:

- Nordic UART Service `6e400001-b5a3-f393-e0a9-e50e24dcca9e`; RX
  `…6e400002` (write, host → device), TX `…6e400003` (notify, device → host).
  Plain byte pipe — no framing of its own, the KISS stream is the payload.
- The client **only ever considers bonded devices** (`get_paired_devices()`
  :146, filtered to `DEVICE_TYPE_LE`/`DUAL` at :1802-1818). With no
  `ble_name`/`ble_addr` in the interface config it matches any bonded device
  whose name starts with `"rnode "` case-insensitively (:1815-1820). It never
  scans — so the name matters at pairing time only, but the device must still
  be advertising and connectable to be dialable.
- MTU: `TARGET_MTU = 512` requested (:1632); the client's own write chunk to RX
  is `min(mtu-5, 512)` (:1854), so inbound writes are always ≤ negotiated
  MTU − 5. `BASE_MTU = 20` until the exchange succeeds.
- Order verified at :1825-1857: connect → discover services → enable
  notifications (CCCD) → request MTU → **`connected = True` only inside
  `on_mtu_changed` on success**. No KISS bytes flow before that.
- **MTU failure is fatal and permanent.** On a connect timeout with an MTU
  request pending, the client appends `ERROR_INVALID_BLE_MTU (0x20)`, sets
  `should_run = False` and `awaiting_ble_reset = True` (:1776-1784) — the
  connection job stops entirely until Reticulum restarts. NimBLE answers every
  ATT MTU exchange automatically with its preferred MTU, so this only fires if
  we misconfigure the preferred MTU down near 23.
- `CONNECT_TIMEOUT = 7.0`, `RECONNECT_WAIT = 2.5` (BLE), `MTU_TIMEOUT = 4.0`.
- Transport-independent watchdog in `readLoop` (:1503-1504, `PORT_IO_TIMEOUT
  = 3` at :267): 3 s with no inbound bytes → the client re-sends `CMD_DETECT`;
  9 s → `IOError`, interface offline. The endpoint already answers every detect
  statelessly (`lora_rnode.cpp:155-160`, no per-session state), so this is
  satisfied by keeping notification latency well under a second.
- RNode's own firmware marks both characteristics encrypted + MITM and pairs
  with a displayed passkey. RNS itself requires only that the device be bonded.

RNS interface — verified in `torlando-tech/ble-reticulum`
(`BLE_PROTOCOL_v2.2.md`, `src/ble_reticulum/BLEFragmentation.py`, both
re-fetched at revision time) and `torlando-tech/columba`
(`docs/ble-architecture.md` re-fetched; `BleConstants.kt` per the original
survey). Columba runs the reference Python stack under Chaquopy with a Kotlin
radio driver, so Android and Linux are the same protocol:

- Service `37145b00-442d-4a94-917f-8f42c5da28e3`; RX `…28e5` (write /
  write-without-response, central → peripheral), TX `…28e4` (read + notify,
  peripheral → central), Identity `…28e6` (read, 16 bytes), standard CCCD.
- Both sides run both roles. **Who connects is decided by comparing the two
  adapter addresses as integers — lower address becomes central.** No
  negotiation, no retry from the other side.
- The peripheral cannot read from a central, so the central's first write to RX
  is a bare **16-byte identity hash**, written after subscribing to TX. A
  16-byte write is a handshake only while no identity is yet known for that
  address.
- Peers are keyed by the 16-byte identity, never by address — Android rotates
  its address about every 15 minutes. On rotation Columba updates the
  address → identity mapping and keeps the existing per-peer interface.
- Fragment header is 5 bytes, `!BHH`: type (`0x01` START, `0x02` CONTINUE,
  `0x03` END; Columba also defines `0x00` LONE, which the Python side never
  emits — `BLEFragmentation.py` has only the three), 16-bit sequence, 16-bit
  total. Every packet is fragmented, including single-fragment ones.
  Reassembly timeout `DEFAULT_TIMEOUT = 30.0` s.
- **Keepalive**: Columba writes a single byte `0x00` every 15 s of idle
  (against Android's supervision timeout) and disconnects after 3 failures.
  Consequence for us, both roles: any inbound write that is neither a 16-byte
  handshake nor a parseable ≥5-byte fragment is **silently ignored** (the
  1-byte keepalive must not feed the reassembler), and as central to an
  Android peripheral we **emit** the 0x00 write after 15 s of transmit idle.
- **Payload sizing differs between the two implementations.** Columba passes
  `usableValueLength(attMtu) = attMtu - 3` into the fragmenter (which then
  subtracts its 5-byte header), so its full fragments fit the writable
  attribute value. The Linux side passes bleak's raw ATT MTU, so `payload =
  mtu - 5` makes a full fragment `mtu` bytes — three bytes larger than the
  peer can accept. Follow Columba: emit `attMtu - 3 - 5` payload maximum,
  accept anything up to 512 inbound.
- Columba: max 7 peers, RSSI floor −85 dBm, 5 s discovery interval (30 s idle
  mode after 3 empty scans), per-peer RNS interface spawned like TCP client
  interfaces.

### Why three straddles

- `nimble_port_init()` / `ble_hs_cfg` / `ble_gatts_start()` are process-global.
  Two straddles both bringing the host up is not a thing.
- One legacy advertising instance carries one payload. Both features need to be
  discoverable by Android scanners, which use legacy scan parameters, so
  extended advertising with two instances is not a way out (see Decisions).
- The build generator warns when two straddles set the same kconfig symbol
  (`spangap-inside:2632-2635` — note: the *generator* at build time, not the
  schema, and `spangap validate` does not run this check), and both features
  need the same ~15 lines.
- The connection budget, the scan/advertise/connected radio time, and the bond
  store are all single resources someone has to hand out.

spangap-ble therefore stays **generic** — no Reticulum, RNode or rnsd anywhere
in its code, comments or docs. It knows "a consumer wants a service advertised
and connections accounted for", nothing about what rides them.

## Lay of the land — what the coding instance builds on

Platform contracts (all verified at the cited lines):

- **Boot**: a straddle declares `services: - { class: BleService, header:
  ble.h }` in `straddle.yaml`; the class is global, default-constructible,
  **ecosystem-free ctor** (`service.h:42-56`). `onInit()` runs in
  dependency-topo order — everything a straddle `requires:` has already run
  (`spangap-inside:1120-1145`, generated `app_main` at :2338-2351). The
  straddle band does not run in safe mode. The straddle's `CMakeLists.txt`
  must reference `${SPANGAP_CONDITIONAL_SRCS}` / `${SPANGAP_REQUIRES}` or
  staging refuses (`spangap-inside:909-922`).
- **ITS** (Inter-Task Streaming, `its.h`): `itsConnect(serverName, port, data,
  dataLen, timeout, ref, onRecv, onDisconnect)` (its.h:221); stream-mode
  `itsSend` has FreeRTOS stream-buffer semantics — **partial send on timeout,
  accepted bytes stay in the ring, no rollback** (its.cpp:2160-2171); packet
  mode is all-or-nothing. Callbacks dispatch **on the task that registered
  them**, via `itsPoll` (docs/its.md). Canonical loop: `for(;;){ while
  (itsPoll(0)){} /*work*/ itsPoll(block); }`.
- **Tasks**: `spawnTask(fn, name, stackBytes, arg, prio, core, STACK_PSRAM)`
  (`compat.h:86`), paired with `killSelf()`; core via `CORE_PRIMARY` /
  `CORE_SECONDARY_NO_LCD`, never bare ints. Every rns-ecosystem task runs at
  **priority 1** (`rnsd.h:63-70` — a producer above its consumer never lets
  back-pressure clear); spangap-net's own task is the precedent for a radio
  owner at priority 2 (`net.cpp:2847`).
- **Settings**: contributions in `straddle.yaml` `settings:` blocks; nodes are
  unowned, blocks at the same `at:` path concatenate, sections merge on
  exact-string match (`build-system/README.md:499-875`). A `switch:` row with
  `default:` on an `s.*` key is auto-seeded via `storageDefault()`. `s.*`
  persisted, `secrets.*` persisted but never sent to the browser, everything
  else ephemeral (`storage.h:17-19`). **Command sentinels are ephemeral keys
  (never `s.*`), answered on `<cmd>.error` / `<cmd>.done`** — model:
  `ntp.tz.set` (`ntp.cpp:116-140`). `storageSubscribeChanges` callbacks fire
  on the subscribing task — subscribe from the task that should handle them;
  `NOW_AND_ON_CHANGE` for enable-gates (`sshd.cpp:184-185`).
- **CLI**: `cliRegisterCmd` is longest-prefix dispatch — subcommands are their
  own whole-string registrations (`cliRegisterCmd("ble scan", …)` beside
  `cliRegisterCmd("ble", …)`). Convention (cli.h:62-73): bare command prints
  status; `help` → one line; `-h/--help` → full help. There is no separate
  `status` verb anywhere in the platform.
- **rnsd interface registration** (`ports.h:243-318`): there is no function —
  you `itsConnect("rnsd", RNSD_PORT_IFACE /*=1*/, &reg, sizeof(reg), …)` with
  a filled `rnsd_iface_t`; `itsDisconnect` deregisters. `name[24]`, `mtu`,
  `bitrate`, `mode` (wire values, translated by `mapIfaceMode` — never µR's
  bits), `in/out/fwd/rpt`, `announce_cap` (0 ⇒ 2%), `point_to_point`,
  `retain_announces`, `ifac_netname[32]`/`ifac_netkey[64]`, optional
  `rx_signal` (prefixes inbound frames with big-endian `int16 rssi | int16
  snr*10`). `RNSD_MAX_IFACES = 16` is the **global** cap across lora + auto +
  espnow + every tcp peer/inbound + every ble peer (rnsd.cpp:84) — budget
  accordingly. Bitrate is not cosmetic: it feeds the first-hop link timeout
  and the announce throttle's tx-time math.
- **rns lifecycle**: the `rns.ready` flag still exists but is **legacy**. An
  interface straddle registers `rnsServiceRegister(TAG, start, stop,
  RNS_PHASE_IFACE)` from `onInit()` (rnsd.h:76-88); `start` spawns the task
  once and thereafter un-parks it (`xTaskNotifyGive`); `stop` sets a flag and
  waits for the park. The task **parks on `itsPoll(portMAX_DELAY)` rather
  than deleting** — deleting leaked ITS state (`rns/INTERNALS.md:944-960`).
  Model: `espnow.cpp:583-651`.
- **The reconcile shape to copy** (`espnow.cpp:401-460`): a `s_configDirty`
  flag set by storage subscriptions + `xTaskNotifyGive`; `applyConfig()` reads
  every setting into locals, computes `changed`, tears down when disabled,
  else `stop-if-changed / start-if-stopped`. Not a timer — the 1 s `itsPoll`
  cap while enabled exists for stats publishing; while disabled the task parks
  forever.
- **The RNode endpoint today** (`iface-lora/esp-idf/src/lora_rnode.cpp`):
  ITS server port `RNODE_ITS_PORT 0x524E` opened with `maxHandles=1`
  (lora.cpp:735) — the session policy is structural. `onRnodeConnect`
  (:416-436) discriminates transport purely on payload length: 1 byte
  (`serial_handler_connect_t`, cli.h:262-265) ⇒ serial, anything else ⇒ TCP
  (`net_connect_t`, net.h:143-147 — 28 bytes with IPv6 on, **8 bytes with
  IPv6 off**). `onRnodeDisconnect` (:440-449) clears `offPend` so a clean
  client exit leaves the radio up. Every detect/fw/platform/mcu query is
  answered statelessly (:155-175).
- **The partial-write carry to copy** (`cli.cpp:1871-2025`): carry buffer per
  port; each pump pass sends the carry first and **returns without reading
  the port** if it didn't fully drain — that unread port is the back-pressure.
  The carry buffer must be ≥ the read chunk or bytes truncate mid-stream
  (the clamp at cli.cpp:2014).
- **Hazards from iface-tcp worth importing** (`tcp.cpp`): drain `itsRecv`
  before any early return in a recv callback or ITS redispatches forever
  (INTERNALS.md:287-290); coalesce drop logging into windows — per-frame log
  lines starved the consumer and self-sustained the overflow (tcp.cpp:171-215);
  gate telemetry publishing to ~1 Hz (tcp.cpp:1647-1656).
- **Docs standard**: `build-system/README.md:962-1055`. Mono-function straddle
  = `README.md` + `INTERNALS.md`, each opening with the check → do ladder;
  README carries the exhaustive storage-variable list; describe the present,
  never the path to it; plan files are never linked from docs.

## Files

New:

- `spangap-ble/` — `straddle.yaml`, `README.md`, `INTERNALS.md`,
  `CONTRIBUTING.md`, `LICENSE.md`, `esp-idf/{CMakeLists.txt,idf_component.yml}`,
  `esp-idf/include/ble.h`, `esp-idf/src/ble.cpp` (+ `ble_gap.cpp`,
  `ble_sec.cpp`, `ble_cli.cpp` if it earns the split)
- `rnode-ble/` — same skeleton, `esp-idf/include/rnode_ble.h`,
  `esp-idf/src/rnode_ble.cpp`
- `iface-ble/` — same skeleton, `esp-idf/include/ble_iface.h`,
  `esp-idf/src/ble_iface.cpp` + `ble_frag.cpp` + `ble_peers.cpp`

Touched:

- `iface-lora/esp-idf/include/rnode_door.h` — **new public header**: the door
  contract (`RNODE_ITS_PORT`, the connect payload) lifted out of the private
  `src/lora_rnode.h`
- `iface-lora/esp-idf/src/lora_rnode.cpp` — three-way transport discrimination
  in `onRnodeConnect`
- `iface-lora/INTERNALS.md` §17 — see Docs
- `reticulous/esp-idf/sdkconfig.defaults` — nothing; the symbols live in
  spangap-ble's `kconfig:` block (which is also what makes `when_kconfig` row
  gating work — it resolves only against `kconfig:` fragments, never against
  a buildable's `sdkconfig.defaults`)

## A. spangap-ble — the stack owner

`name: spangap/spangap-ble`, `prefix: ble`, `requires: []` (spangap-core is
implicit; consumers gain the ordering by requiring spangap-ble). Boot object
`BleService` via `services:`; ecosystem-free ctor; `onInit()` seeds defaults,
registers CLI, spawns the ble task.

**Kconfig fragments** (`kconfig:` block; all symbol names and defaults
verified against IDF v5.5.4 in the build container — `Kconfig.in` of
`components/bt`):

```
CONFIG_BT_ENABLED=y
CONFIG_BT_NIMBLE_ENABLED=y
CONFIG_BT_NIMBLE_ROLE_CENTRAL=y
CONFIG_BT_NIMBLE_ROLE_OBSERVER=y
CONFIG_BT_NIMBLE_ROLE_PERIPHERAL=y
CONFIG_BT_NIMBLE_ROLE_BROADCASTER=y
CONFIG_BT_NIMBLE_MAX_CONNECTIONS=8        # range 1..9 on S3, default 3
CONFIG_BT_CTRL_BLE_MAX_ACT=10             # range 1..10; conns + adv + scan
CONFIG_BT_NIMBLE_MAX_CCCDS=16             # default 8; two per peer with room
CONFIG_BT_NIMBLE_ATT_PREFERRED_MTU=517    # default 256; the RNode client needs 512
CONFIG_BT_NIMBLE_NVS_PERSIST=y
CONFIG_BT_NIMBLE_MAX_BONDS=8
CONFIG_BT_NIMBLE_SM_SC=y
CONFIG_BT_NIMBLE_MEM_ALLOC_MODE_EXTERNAL=y  # PSRAM; every board has it (CONFIG_SPIRAM=y is platform-band)
```

The S3 controller sources the C3-family Kconfig
(`components/bt/controller/esp32c3/Kconfig.in`), which is where
`BT_CTRL_BLE_MAX_ACT` lives — each activity costs 828 bytes of controller
RAM, and 8 connections + 1 advertising + 1 scanning is exactly the cap of 10.
If internal DRAM turns out tight (see Verification), the first lever is
lowering `MAX_CONNECTIONS`/`MAX_ACT`, not moving more host memory — the
controller's allocations are internal-DRAM-only regardless of
`MEM_ALLOC_MODE_EXTERNAL`.

**Public surface** (`ble.h`), all generic:

- Lifecycle: `bleUp()` / `bleDown()` / `bool bleIsUp()`, mirroring net's shape
  (`net.h:54-66`): the up/down calls post to the ble task's inbox and return —
  no radio work on the caller's task — and `bleUp()` no-ops with a log line
  when `s.ble.enable=0`, exactly as `netUp()` does (net.cpp:2856-2859). The
  host is brought up lazily — on the first reconcile pass in which a consumer
  has asked for it — not at boot, so a build with the straddle staged but
  nothing using it costs flash only.
- `void bleRegister(int event, ble_event_cb_t cb)` with `BLE_EV_UP`,
  `BLE_EV_DOWN`, `BLE_EV_CONNECT`, `BLE_EV_DISCONNECT`, `BLE_EV_MTU`,
  `BLE_EV_SUBSCRIBE`, `BLE_EV_CFG_CHANGED`. Copy `netRegister`'s documented
  contract wholesale (net.cpp:125-154): fixed array, append-only, register
  once per process, **UP is level-replayed synchronously on the registering
  task** (so a consumer that comes up after the host still sees it; UP
  handlers must be idempotent), everything else edge-only and dispatched on
  the ble task.
- GATT: `bool bleGattAdd(const struct ble_gatt_svc_def* svcs)`. Because the
  host starts lazily, this **cannot call `ble_gatts_add_svcs` directly** —
  NimBLE's GATT registry only exists between `nimble_port_init()` and
  `ble_gatts_start()`. `bleGattAdd` records the pointer in spangap-ble's own
  list (the definition must be `static const`, alive forever — document
  that); at host bring-up the ble task runs `nimble_port_init()` →
  `ble_svc_gap_init()`/`ble_svc_gatt_init()` → `ble_gatts_count_cfg` +
  `ble_gatts_add_svcs` for every recorded consumer → `ble_gatts_start()`.
  Consumers call it from `onInit()`; they `require:` spangap-ble, so its
  `onInit()` has run and the list exists. An add after the host has started
  warns and forces `ble_gatts_reset()` + re-add + restart, which drops
  connections — legal, but only the reconcile path should ever do it.
- Advertising: `int bleAdvRequest(const ble_adv_req_t*)` / `void
  bleAdvRelease(int slot)`. Request carries `{ name[16], uuid128[16],
  includeName, connectable, priority }`. spangap-ble owns the single legacy
  advertising instance and **round-robins** the requests, ~2 s per slot, one
  payload at a time (see Decisions). Legacy advertising data changes are
  stop → set params/data → restart. Note the interplay that makes round-robin
  safe for the RNode door: a central dials the *address*, not the payload, so
  as long as the currently-active slot is also connectable, a bonded client's
  connect lands during either slot — document this in INTERNALS. With no
  slots requested, advertising stops.
- Scanning: `bool bleScanStart(const ble_scan_req_t*)` / `bleScanStop()` —
  duty-cycled, RSSI floor, the 128-bit-UUID advertisement-data parse done once
  here (ESP-IDF hands over raw advertisement bytes; there is no BlueZ-style
  service filter). Results arrive as a callback with address, address type,
  RSSI, and the matched service UUID.
- Connection budget: `bool bleSlotReserve(const char* owner, int n)` /
  `bleSlotRelease`. Total is `CONFIG_BT_NIMBLE_MAX_CONNECTIONS`; a reservation
  is what keeps the interface from eating the door's slot.
- Identity of the local adapter: `bool bleOwnAddr(uint8_t out[6], uint8_t*
  type)` — iface-ble's role election needs it, and it must be the **public**
  address (`own_addr_type = BLE_OWN_ADDR_PUBLIC` everywhere; privacy/random
  addressing would make the election non-deterministic; spangap-ble does not
  enable it).
- Security: one global policy — `sm_bonding = 1`, `sm_sc = 1`, io capability
  `NO_INPUT_OUTPUT` (Just Works) in v1 — see Open questions for the passkey
  path. Bonds persisted by NimBLE in NVS. **Enforcement is per
  characteristic**, via the access flags a consumer sets in its own service
  definition, so the door can demand encryption while the interface's
  characteristics stay open and never trigger pairing.
- `void blePairingWindow(uint32_t seconds)` + `bool bleForget(const uint8_t
  addr[6])` / `bleForgetAll()` — new bonds are only accepted inside the window
  (reject the SM pairing request outside it in the GAP event handler).

**Task model.** One `ble` task, priority 2 on `CORE_PRIMARY` (the spangap-net
precedent for a radio owner; consumers stay at 1), canonical ITS loop. NimBLE's
host task is its own FreeRTOS task with host callbacks on it; spangap-ble
marshals everything consumers see onto the ble task (queue + notify, the
espnow recv-callback pattern) — no consumer code ever runs in host context,
and no NimBLE teardown ever runs inside a consumer-facing callback.
Reconcile in the espnow `applyConfig` shape: dirty flag from
`storageSubscribeChanges("s.ble", …)` (subscribed on the ble task) +
notification; stats publish at most 1 Hz and only when `uiTelemetryWanted()`.

**Settings.** spangap-ble defines a top-level node — `at: [ { id: bluetooth,
label: "Bluetooth", short: "BT", order: 12 } ]` (nodes are unowned; order
slots it between net/10 and reticulum/15). Rows: `s.ble.enable` (switch,
default 0 — the master switch; with it off the host never starts even if a
consumer asks), `s.ble.txpower` (dBm, default 9), a pairing button as `set:
{ key: "ble.pair", value: "60", edge: true }` — **the sentinel key is
ephemeral `ble.pair`, not `s.ble.pairing`** (sentinels are never `s.*`,
README:579-597), handled with the `<cmd>.error`/`<cmd>.done` counter pair.
Runtime readouts (ephemeral, finished strings): `ble.state_text`, `ble.peers`,
`ble.bonds`.

**CLI** — registered as whole-string commands: `ble` (bare = status, per
convention), `ble peers`, `ble bonds`, `ble pair [seconds]`, `ble forget
<addr|all>`, `ble scan` (one-shot debug scan printing address/RSSI/UUIDs).

**Coexistence.** The 2.4 GHz radio is shared with Wi-Fi and ESP-NOW. Software
coexistence is on by default in IDF; document the interaction in INTERNALS
(scanning is the expensive part, and it is the part we duty-cycle) and make the
scan duty cycle back off while any connection is attached. Note there is no
espnow-style `netIsUp()` gate to copy — BLE has its own controller; only the
antenna time is shared.

## B. rnode-ble — the RNode Bluetooth door

`name: reticulous/rnode-ble`, `prefix: rnode_ble`,
`requires: [spangap/spangap-ble, reticulous/iface-lora]`. Boot object
`RnodeBleService`.

- **GATT**: the Nordic UART Service as a `static const ble_gatt_svc_def[]`,
  registered through `bleGattAdd()` in `onInit()`. RX characteristic
  `WRITE | WRITE_NO_RSP` with `ENC | AUTHEN` access flags; TX `NOTIFY` with
  `ENC`. The flags are what force bonding for this service alone.
- **Advertising**: one `bleAdvRequest` with `connectable = 1`, name
  `"RNode %02X%02X"` from the last two bytes of the public adapter address
  (10 chars + NUL, fits `name[16]`; the client prefix-matches `"rnode "`
  case-insensitively; the suffix is cosmetic), the service UUID in the
  advertisement and the name in the scan response — 31 bytes will not hold
  both a 128-bit UUID and a name. Released while a session is attached (the
  client dials a bonded address; a second client would be refused anyway).
- **Session**: on `BLE_EV_SUBSCRIBE` for the TX characteristic —
  `itsConnect("lora", RNODE_ITS_PORT, &c, sizeof c, …)` with the new
  `rnode_door_connect_t`. Subscribe rather than first-byte, because the RNS
  client's verified order is connect → discover → subscribe → MTU → write, and
  no data arrives before MTU success. A refused connect (endpoint busy — a
  serial or TCP client attached first, or endpoint disabled) → drop the BLE
  connection; the client will retry every 2.5 s and land when the endpoint
  frees. On BLE disconnect, `itsDisconnect` — the endpoint's existing
  `onRnodeDisconnect` clears `offPend` so a clean client exit leaves the
  radio up.
- **Inbound** (RX write → ITS): stream-mode `itsSend` can partial-write on
  timeout; carry the remainder forward exactly as the core serial pump does
  (`cli.cpp:1871-2025`: carry-first, return-without-reading when the carry
  won't drain — here "without reading" means stop consuming host RX events;
  size the carry ≥ the largest ATT write, 512 B). The KISS stream is a byte
  stream — chunk boundaries are free, and unlike the device→client direction
  there is no whole-frame constraint here.
- **Outbound** (ITS → notify): `itsRecv` into chunks of `attMtu - 3`, one
  `ble_gatts_notify_custom` per chunk. NimBLE returns `BLE_HS_ENOMEM` when its
  buffers are full — **do not drop**: stop recving and let the bytes sit in
  the ITS ring (that is the back-pressure), resume on `BLE_GAP_EVENT_NOTIFY_TX`
  (marshalled onto the ble task by spangap-ble as `BLE_EV_*` or an internal
  resume signal). Dropping bytes here corrupts the KISS stream in a way the
  client cannot resynchronise from except by the 9 s watchdog. If sustained
  throughput needs more notify buffers, the knob is
  `CONFIG_BT_NIMBLE_MSYS_1_BLOCK_COUNT` — measure first.
- **Latency**: request a connection interval of 15–30 ms
  (`ble_gap_update_params`) once connected. The client's 3 s detect / 9 s
  offline watchdog is transport-independent, so a sleepy connection interval
  would kill sessions that are otherwise idle.
- **MTU**: preferred 517 set by spangap-ble; chunk to whatever was negotiated
  (`BLE_EV_MTU`). If the exchange never completes the client fails hard and
  permanently on its side — nothing for us to do but log it.
- **Settings**: rows contributed at `at: [ { id: reticulum }, { id: lora } ]`
  with `section: "RNode endpoint"` — the section label must exactly match
  iface-lora's existing string so the rows merge under it (sections merge on
  exact match, with a build warning on near-miss). Key: `s.ble.rnode.enable`
  (switch, default 0). Note the key lives in the owner's `s.ble.*` namespace
  while the *pane row* sits in the lora pane — key namespace and pane path
  are independent, and this is a straddle.yaml contribution, unlike
  `s.net.rnode_port`, which is a *runtime* `storageSet` from lora code
  (lora_rnode.cpp:393-397) — don't conflate the two mechanisms. Runtime:
  `ble.rnode.state_text`.
- **CLI** `rnode-ble` (bare = status).

## C. iface-lora — the third door

Small and mechanical:

- Lift `RNODE_ITS_PORT` and a new connect payload out of the private
  `src/lora_rnode.h` into a new public `include/rnode_door.h`. Nothing else
  moves — the KISS opcodes stay private.

  ```c
  typedef struct {
      uint8_t magic;        /* 0xB7: belt to the length's braces */
      uint8_t peerAddr[6];
      uint8_t addrType;
      uint8_t reserved[4];  /* size 12: sizeof(net_connect_t) is 28 with
                               IPv6 on but 8 with IPv6 off — 8 would collide */
  } rnode_door_connect_t;
  ```

  The size is load-bearing: `net_connect_t` (net.h:143-147) is 28 bytes under
  the current `CONFIG_LWIP_IPV6=y` but **8 bytes if IPv6 is ever switched
  off**, and the naive `{magic, addr[6], type}` struct is exactly 8. Pad to
  12 and keep a `static_assert` that the three sizes are pairwise distinct.
- `onRnodeConnect` (lora_rnode.cpp:423) currently reads the transport off the
  payload length (1 byte ⇒ serial, else TCP). Make it a three-way switch on
  length with the `static_assert`, verify the magic on the BLE branch, and
  label the log line `serial` / `tcp` / `ble`.
- No change to the session policy: `maxHandles=1` on the port plus the
  `S.handle >= 0` check is one session at a time across every transport,
  which is exactly what we want for Bluetooth too.

## D. iface-ble — RNS over the ble-reticulum v2.2 protocol

`name: reticulous/iface-ble`, `prefix: rns_ble`,
`requires: [reticulous/rns, spangap/spangap-ble]`. Boot object
`BleIfaceService`. **Lifecycle: `rnsServiceRegister(TAG, bleIfaceStart,
bleIfaceStop, RNS_PHASE_IFACE)` from `onInit()`** — the `rns.ready` barrier
is legacy and no iface straddle waits on it any more (espnow.cpp:561-563,
tcp.cpp:1549-1551); start spawns-or-unparks, stop parks, the task never
deletes itself. One task, priority 1, `CORE_PRIMARY`, canonical ITS loop,
`itsClientInit(max_peers + headroom)` (only the rnsd side needs client slots;
there is no net-side server here).

- **One rnsd interface per peer**, not one interface for the medium.
  Registration is `itsConnect("rnsd", RNSD_PORT_IFACE, &reg, sizeof reg,
  pdMS_TO_TICKS(500), slot, onRnsdRecv, onRnsdDisconnect)` per peer, exactly
  the iface-tcp inbound shape (tcp.cpp:1129-1171: slot index = ITS ref =
  array index, teardown closes rnsd first). Fields: `name = "ble/<first 4
  bytes of the peer identity, hex>"` (fits `name[24]`), `mtu = RNS_MTU` (500
  — `_FIXED_MTU` is unconditional in rnsd, the ATT MTU never leaks upward),
  `mode = RNS_IFACE_MODE_GATEWAY`, `point_to_point = 1` (split horizon),
  `retain_announces = 1` (an edge link whose peers we are custodians for —
  deliberate, and the opposite of tcp's default), `in = out = fwd = 1`,
  `announce_cap = 0` (rnsd applies the 2% default), bitrate a measured
  estimate (~50–100 kbit/s at MTU 247 and a 30 ms interval; put the real
  number in INTERNALS once measured — it feeds the first-hop link timeout and
  the announce throttle). **Budget note**: `RNSD_MAX_IFACES = 16` is global
  across all interface straddles; `max_peers` defaults to 4 partly for this
  reason. Rate-limit registration churn — every peer come/go is a
  register/deregister pair plus a storage batch that lxmf subscribes to.
- **Peer table** keyed by the 16-byte identity, with an address → identity map
  beside it, both roles in one table. Per peer: role, connection handle, ATT
  MTU, fragmenter sequence, one reassembly buffer (512 B), last-seen, RSSI,
  failure count and backoff.
- **Own identity** for the Identity characteristic:
  `rnsdIdentityHash("secrets.rnsd.identity", out16)` (rnsd.h:129-130,
  `RNSD_IDENT_HASH_LEN` = 16; runs inline on the caller's task).
- **Role election**: compare our public adapter address with the peer's as
  integers; lower connects. Peers with random addresses (every Android device)
  re-elect on each rotation, which is fine — the identity keeps the peer table
  and its rnsd interface stable across it.
- **Discovery**: `bleScanStart` with the service UUID and an RSSI floor from
  settings; score by RSSI and history; rate-limit connection attempts per peer
  (5 s) and back off after repeated failures. Advertise via `bleAdvRequest`
  with the service UUID and **no name** — the 31-byte budget is why v2.2 made
  the name optional.
- **Fragmentation** (`ble_frag.cpp`): emit `[type|seq16|total16]` + payload,
  payload capped at `attMtu - 3 - 5`; accept inbound fragments up to 512 and
  reassemble by total/seq with a 30 s timeout. Inbound writes that are neither
  a 16-byte handshake (from an identity-less address) nor a parseable ≥5-byte
  fragment are ignored silently — that is the keepalive path, not an error.
  A packet that arrives before its peer's identity is known is dropped with a
  warn, as upstream does.
- **Keepalive**: as central, write a single `0x00` to RX after 15 s of
  transmit idle (Columba's peripheral disconnects after 3 missed intervals).
  As peripheral, nothing to send — just tolerate the inbound byte.
- **Handshake**: as central, after subscribing, read Identity then write our
  16 bytes to RX. As peripheral, treat a 16-byte write from an address with no
  known identity as the handshake and only then create the peer's interface.
- **Settings** at `at: [ { id: reticulum }, { id: ble, label: "BLE" } ]`
  (espnow's shape — own child node under the reticulum pane), keys under the
  owner's namespace: `s.ble.rns.enable` (switch, default 0), `s.ble.rns.max_peers`
  (default 4), `s.ble.rns.min_rssi` (default −85), `s.ble.rns.scan_interval`
  (default 5 s), `s.ble.rns.ifac_netname` + `secrets.ble.rns.ifac_netkey`
  (masked `text:` row with `secret: true`) + `s.ble.rns.ifac_size` — one
  per-medium IFAC like tcp's server side, not per-peer (peers are discovered,
  not configured). Runtime: `ble.rns.state_text` / `ble.rns.peers` /
  `ble.rns.traffic`, published at most 1 Hz.
- **CLI** `bleif` (bare = status) and `bleif peers` (identity, role, MTU,
  RSSI, fragments in/out) — the shape `lora neighbors` has.

## E. Docs

Per the straddle docs standard (`build-system/README.md:962-1055`): each new
straddle is mono-function, so `README.md` (operator) + `INTERNALS.md`
(maintainer), each opening with the check → do ladder; the README carries the
exhaustive storage-variable list (settings, runtime keys, sentinels, secrets).

- `spangap-ble/README.md` — what it owns, settings, CLI, pairing workflow.
  `INTERNALS.md` — why the host starts lazily and the queued-GATT-registration
  consequence, the advertising round-robin (and why a bonded central can
  connect during any slot; why not extended advertising), the
  global-security-with-per-characteristic-enforcement split, the connection
  budget, Wi-Fi coexistence, NVS bond store, the host-task/ble-task marshal.
- `rnode-ble/README.md` — how to pair a phone and point Sideband at it, with
  the interface config snippet. `INTERNALS.md` — the wire sketch above, the
  bonded-only client behaviour, the MTU hard-failure (permanent until app
  restart), the 3 s/9 s watchdog, the back-pressure rule.
- `iface-ble/README.md` — what interoperates (Columba, Linux ble-reticulum)
  and the per-peer interface naming an operator will see in `rns ifaces`.
  `INTERNALS.md` — protocol v2.2 in full, the address-sort election, the
  identity handshake, the fragment header, the keepalive byte, and the
  `attMtu - 3` vs raw-MTU discrepancy with the Linux implementation.
- `iface-lora/INTERNALS.md` §17 — edit in place, not append: §17.1 transports
  and §17.2 ("One session, two doors" → three; its closing claim "that length
  is the only discriminator available, and nothing else needs one" at
  :1432-1440 is what the third door invalidates), pointer to `rnode_door.h`.
  While in there, fix two stale lines: :1386 says the endpoint lives in
  `lora.cpp` (it is `lora_rnode.cpp`), and :1418-1420 says net's port key is
  subscriber-driven (net polls — `epOpenAll` from `netPollOnce`).

## Implementation order

A (spangap-ble, standalone: `ble scan` + bare `ble` against a phone is the
acceptance test) → C (iface-lora door header, mechanical) → B (rnode-ble, the
smaller consumer and the one with a stock client to test against) → D
(iface-ble) → E (docs alongside each).

B before D deliberately: the door exercises the GATT server, bonding,
notification back-pressure and MTU without needing the central role at all, so
whatever is wrong in A surfaces against a known-good client first.

After each straddle skeleton: `spangap validate` (schema + deps only — it does
**not** run the kconfig-collision check), then a real `spangap build` (which
does), stating the board.

## Verification

Build-only from here (I build, the user flashes). The active target lives in
`.spangap-build` at the workspace root — currently
`reticulous/reticulous --with spangap/hw-lilygo-tdeck --kconfig
CONFIG_LORA_NO_SUPE=y`; state the board after every build and don't rebuild
just to restore a prior target.

- Size (verified against the 2026-08-17 stable build artifacts and the
  generated partition tables — there are no per-board partitions.csv files;
  `gen-partitions.py` derives the app partition from flash size and
  `CONFIG_SPANGAP_MAX_FIRMWARE_KB`):
  - heltecv4: app.bin 2,918,560 B in a 5.625 MiB app partition (49.5%).
  - tdeck: 4,274,832 B in 7.438 MiB (54.8%).
  - t3s3: 2,981,152 B in 3.375 MiB (84.2%, 545 KiB free).
  - nibble-zero: 2,891,312 B in 3.375 MiB (81.7%, 632 KiB free).
  NimBLE host + controller is expected to add 250–350 KB, which **fits every
  board** — t3s3 lands near 94% and is the one to watch. Note the build
  suppresses IDF's "smallest app partition is nearly full" warning as
  meaningless under shrink-wrap; an overflow surfaces only as the hard
  `--app-bytes` assertion, so report the measured delta explicitly after the
  first build with A staged.
- RAM: flash is not the scarce resource — internal DRAM is. The controller
  allocates internal-only memory (`MAX_ACT` × 828 B plus stack/buffers), and
  spangap-core's own notes put free internal DRAM at ~73 KB with Wi-Fi up.
  Bare `ble` prints free internal/PSRAM before and after host start; capture
  it with Wi-Fi up and down.

On-device, user-driven:

1. **A alone**: `s.ble.enable=1` → host up, `ble scan` sees phones and any
   RNode nearby; bare `ble` shows zero connections; `s.ble.enable=0` tears
   down and frees the heap it took.
2. **B**: pair a phone inside `ble pair 60`; Sideband RNode interface with no
   `ble_name` → connects, "firmware 1.78", radio lines, "configured and
   powered up". Leave it idle >10 s: the 3 s detect / 9 s offline watchdog
   must never fire. Kill Bluetooth on the phone mid-session → clean detach,
   radio stays up for rnsd, reconnect works. Confirm a second concurrent
   client is refused and that a serial or TCP client attached first blocks
   the Bluetooth door.
3. **B under load**: announce traffic in both directions while watching for a
   corrupted KISS stream (the client would go offline and re-dial every
   2.5 s) — this is the back-pressure rule under test.
4. **D against Columba**: phone and device discover each other, elect roles
   deterministically, exchange identities, and the device shows one
   `ble/<hex>` interface per peer in `rns ifaces`. Announce from the phone
   appears on the device; LXMF message both ways. Let the phone rotate its
   address (~15 min) and confirm the peer and its rnsd interface survive it.
   Leave the link idle past 45 s to prove the keepalive path.
5. **D against Linux**: a Raspberry Pi running ble-reticulum, to exercise the
   other implementation — specifically whether its raw-ATT-MTU fragments
   arrive truncated at high MTU (see Open questions).
6. **Both at once**: a phone bonded as an RNode client and a second phone
   running Columba, with the connection budget and the advertising round-robin
   under real contention. Then with ESP-NOW or Wi-Fi also up, for coexistence.

## Decisions — all agreed 2026-08-17

- **Three straddles, not two.** The stack owner exists because NimBLE
  initialises once, advertises once, and holds one bond store and one
  connection budget. It stays generic — nothing in spangap-ble knows what
  Reticulum is.
- **NimBLE, not Bluedroid.** Smaller, BLE-only, and nothing either feature
  needs is Bluedroid-exclusive. RNode's own firmware uses the Arduino/Bluedroid
  library; we are not reusing its code, only its wire protocol.
- **One legacy advertising instance, round-robined**, rather than extended
  advertising with an instance per consumer. Android scanners use legacy scan
  parameters by default, and both peer implementations we care about
  (Sideband via `able`, Columba) scan that way; an extended-only advertisement
  would be invisible to them. Cost: discovery takes up to one extra slot
  period; connects by bonded address are unaffected (any connectable slot
  accepts them).
- **GATT services are queued, not registered, by `bleGattAdd`.** Forced by the
  lazy host start; the consumer's service definition must be static const.
- **Bonding is global, enforcement is per characteristic.** One `ble_hs_cfg`,
  with encryption demanded only on the Nordic UART Service characteristics, so
  iface-ble never triggers a pairing dialog and the RNode door always does.
- **No privacy/random addressing on our side.** The v2.2 role election is an
  address comparison; a rotating local address makes it non-deterministic and
  invites connection storms.
- **The Bluetooth host starts lazily**, on the first consumer request, not at
  boot. A staged-but-unused straddle then costs flash only.
- **iface-ble uses the orchestrator, not the legacy barrier**:
  `rnsServiceRegister(…, RNS_PHASE_IFACE)`, park-don't-delete, priority 1.
- **Per-peer rnsd interfaces for iface-ble**, matching iface-tcp inbound and
  upstream, rather than one shared-medium interface. It gives correct
  split-horizon (`point_to_point = 1`) and per-peer announce accounting; the
  cost is one of the 16 global `RNSD_MAX_IFACES` per peer, which is why
  `max_peers` defaults to 4.
- **Follow Columba's fragment sizing** (`attMtu - 3` into the fragmenter, so
  payload `attMtu - 8`), not the Linux side's raw MTU, and accept up to 512
  inbound.
- **`rnode_door_connect_t` is 12 bytes with a magic**, because the naive 8
  bytes collides with `net_connect_t` under IPv6-off builds and length is the
  door's only discriminator.
- **Consumer keys live under the owner's namespace** — `s.ble.rnode.*`,
  `s.ble.rns.*`. The operator sees one Bluetooth namespace; the straddle
  boundary stays where the code is. (Settings *rows* still sit in whatever
  pane fits the operator — pane path and key namespace are independent.)
- **The RNode door reuses the existing endpoint wholesale.** No KISS code, no
  radio knowledge, no second session policy in rnode-ble — it moves bytes onto
  `RNODE_ITS_PORT` and nothing else. There is one RNode, and the session is
  first come, first served across transports: a CDC (or TCP) client attached
  first blocks the Bluetooth door until it disconnects, and vice versa.
- **v1 pairs Just Works** (`NO_INPUT_OUTPUT`) inside the timed window. RNS
  requires only that the device be bonded; the passkey-display path is an
  upgrade, not a prerequisite.

## Open questions

- Adaptive controller sizing: derive `ble_max_act`/connection count at
  hostStart from what the earmark actually secured, so a squeezed boot
  degrades to fewer connections instead of no Bluetooth. Today the numbers
  are static and the earmark warns when it cannot get its block.

- Does BlueZ truncate or reject the 3-bytes-too-large fragments the Linux
  ble-reticulum emits at high MTU, or does its own MTU accounting hide the
  bug? Determines whether we must clamp our negotiated MTU when talking to a
  Linux peer. Test 5 answers it.
- Advertising round-robin period: 2 s is a guess. If Columba's 5 s discovery
  interval and Sideband's connect timeout of 7 s leave slack, a longer period
  costs less radio. (Connects are unaffected either way — only discovery of
  the not-currently-advertised payload pays.)
- Whether the passkey display path is worth building on the boards with a
  screen (heltecv4 OLED, tdeck LCD), given Just Works bonding inside a timed
  window already satisfies the RNS client. RNode firmware displays a passkey;
  spangap-ble knowing about a display means gating on `CONFIG_SPANGAP_LCD`,
  which is a spangap-internal reference and allowed.
- Measured bitrate for the rnsd registration, and whether the default 2%
  announce cap is sane on a link this fast.
- Whether iface-ble should set `rx_signal = 1` and prefix inbound frames with
  per-packet RSSI (the mechanism exists, rnsd.cpp:599-619) — nice telemetry,
  but per-packet RSSI is not readily available from NimBLE without polling
  `ble_gap_conn_rssi`.
