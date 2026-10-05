# sim-mesh: the `meshtastic` category (meshtasticd on the virtual SX1262)

Goal: Meshtastic's Linux daemon (meshtasticd, firmware v2.7.26) runs as a
sim-mesh station on the virtual SX1262, driven by a `meshtastic` category
driver, with scripts reaching it as `<selection>.meshtastic.<verb>` and
message outcomes reported as `msg.status` / `msg.received` events, in both
real-time and virtual-time runs.

```
driver (testbed, Python) ── framed RPC on the console pty ──► host.py   (sid = id + 1_000_000, radio-less station)
host.py ── TCP <SIM_MESH_BIND_ADDR>:4403, 0x94 0xC3 len16 + ToRadio ──► meshtasticd   (sid = id, libsimradio-sx1262)
meshtasticd ── 0x94 0xC3 len16 + FromRadio ──► host.py ── "mthost: {json}" lines ──► driver.console_line
meshtasticd ── SPI frames / pins (simradio-portduino) ──► chip model ── ether
meshtasticd stdout ──► host.py private pty ──► "fw: …" lines on the console
```

## Decisions (made; do not re-open)

1. **The driver does not open the TCP API itself.** A connection from the
   testbed process is outside the run's time (README §5 / INTERNALS "What is
   still outside"): commands would land at whatever T the run had reached,
   making runs irreproducible, and acks would be timestamped late. Instead,
   like the MeshCore companion, the zip's `host.py` is a second, radio-less
   station process (`sids = (id, id + 1_000_000)`, `console_sid = id +
   1_000_000`) that owns the TCP API connection. Its TCP to meshtasticd is a
   shimmed station-to-station channel, and its console lines are drained
   before T moves, so everything lands at an exact T. The driver talks to
   host.py with the existing framed RPC; no contract change.
2. **No `meshtastic` Python package in the zip.** host.py speaks the stream
   protocol itself on asyncio (a few dozen lines of framing) with protobuf
   bindings generated from the firmware's own `protobufs` submodule (commit
   `6b1ded4`, v2.7.25), and the pure-Python `protobuf` runtime
   (`PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`), so the zip's pylib is
   architecture-independent. The upstream library is thread-based and
   blocking.
3. **Firmware: meshtasticd v2.7.26** (`v2.7.26.54e0d8d`, 2026-06-24, the
   newest non-prerelease), clone at
   `/home/spangap/reticulous/competition/meshtastic_firmware`. Our changes are
   one patch file kept in the sister directory and applied on a local branch
   `sim-mesh` of that clone.
4. **Reboot is process exit.** In the sim build, `Power::reboot` exits(0)
   instead of Portduino's in-process `execv`. host.py sees its child exit and
   `os._exit(0)`s, so the station restarts whole (README §4 "Exit to
   reboot"), exactly as the MeshCore companion does.
5. **Sockets bind to the station's address via the linker.** The TCP server
   in the framework's WiFi library binds `INADDR_ANY` with no option. The sim
   env links with `-Wl,--wrap=bind` and simradio-portduino gains a generic
   `__wrap_bind` that rewrites an `INADDR_ANY` IPv4 bind to
   `SIM_MESH_BIND_ADDR`. Every station keeps port 4403.
6. **The firmware's idle ends when a socket is readable.** meshtasticd polls
   its API on timers (accept every 100 ms, reads every 250 ms when quiet) and
   never blocks on a socket; in virtual time the ether holds T while a
   channel has unread bytes, so a quiet station would never read what host.py
   wrote. simradio-portduino's `rnode_idle` becomes a `poll()` over an eventfd
   (DIO1 rises, `rnode_wake`) plus descriptors the firmware registers; the
   link wraps `listen`/`accept`/`close` register and unregister them.
   The meshtastic patch routes `InterruptableDelay` through
   `rnode_idle`/`rnode_wake` and, when an idle ended on a descriptor, makes the
   API server's threads due at once.
7. **Settings are applied in one transaction.** Meshtastic reboots 7 s after
   `set_owner`, after any `commit_edit_settings`, and after most LoRa/role
   changes. While a station is being set up (`configured` False), the
   settings verbs (`name`, `radio`, `tx_power`, `role`, `hop_limit`) only
   record what they were told; `flush` sends it all as
   `begin_edit_settings … commit_edit_settings` (one reboot) and writes
   `state/sim-configured` the moment the commit is answered: the firmware
   has saved by then. simd runs the setup `flush` inside the station's
   `after_start` task, which the supervisor cancels when the process exits
   (`stations.py` `supervise`), so nothing in `flush` can run after the
   reboot; a marker written after it would never be written, and the station
   would be set up again at every start. After writing the marker, `flush`
   sleeps on the station's clock until that cancellation (at most 30 s, then
   `CommandError`), so the station stays in `setup` until it has restarted
   with its settings, and comes `up` on the next start. A `flush` with
   nothing pending returns at once (simd also calls it before a stop and a
   snapshot). After setup, each settings verb applies at once in its own
   transaction.
8. **Radio mapping is exact, not preset-based.** `radio()` sets
   `use_preset=false`, `bandwidth`, `spread_factor`, `coding_rate`,
   `override_frequency = freq_mhz`, `frequency_offset = 0`, `tx_enabled =
   true` and a `region` chosen from the frequency (table below). Sync word
   (0x2B) and preamble (16) are fixed in Meshtastic: `sync`/`preamble` other
   than those (or None) raise `CommandError` naming the fixed value, as the
   MeshCore driver does for its fixed ones. The EU duty cycle stays enforced
   (`override_duty_cycle` false): that is the firmware's real behaviour.
9. **The startup script stops sending the Reticulum sync word to everyone.**
   `testbed/scripts/startup.py` splits its radio rule: `sync=SYNC` only for
   `category == "reticulum"` nodes; the others get the same rule without
   `sync`. This also ends the error every MeshCore station records today.
10. **Node numbers are deterministic.** meshtasticd is started with
    `-h <hwid>` in decimal, `hwid = node_id + 16` (Meshtasticator's offset;
    a decimal hwid N gives MAC `80:00:N` and node number N, which must be
    ≥ 4), and `-d <station dir>/state`, `-c <station dir>/config.yaml`. Keys
    come from `getrandom`, which the shim seeds from `SIM_MESH_SEED` and the
    node id, so runs are reproducible.
11. **Message events are shared with `meshcore`.** `msg_status`,
    `msg_received`, `tagged`, `untagged`, `STATUSES`, `STATUS_EVENT`,
    `RECEIVED_EVENT` move from `sim_mesh/meshcore/driver.py` into
    `sim_mesh/driver.py` (the base), and the meshcore module imports them from
    there under the same names (the installed MeshCore zips' driver imports
    `sim_mesh.meshcore.driver`). The `mid` travels in the text as `text
    #mid`, so a receiver reports it without the sender.
12. **Addressing by node name.** A script names the other end with `to`, a
    node's name; simd passes it on as is (as for meshcore). Each station's
    long name is its sim-mesh node name (`set_owner` long name = name, short
    name = first four characters), so host.py resolves a name through its
    node database. `!xxxxxxxx` is accepted too. A name not in the node
    database fails the send with `why: "unknown node <name>"`.
13. **Base `meshtastic-sx1262`, version a build stamp** (`YYYYMMDDhhmmss`):
    the same upstream version is rebuilt with patch changes, so semver would
    collide. `title: "Meshtastic 2.7.26 (meshtasticd)"`, `hardware:
    "Raspberry Pi"` (shown as "virtual Raspberry Pi"), `category:
    meshtastic`, `radio: sx1262`, `exec: host.py`, `driver: driver.py`,
    `env: {PYTHONPATH: ./pylib, PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION:
    python}`.
14. **Where things live.** Per-firmware material goes in the sister directory
    `/home/spangap/reticulous/sim-mesh-firmware/meshtastic/`: `driver.py`,
    `host.py`, `make_zip.py`, `firmware.patch`, `test_host.py`,
    `test_driver.py`. Generic changes go in sim-mesh.
15. **The implementer finishes the job alone.** The user has asked for this
    job to run unattended to a working result, which sets aside, for this
    job, the standing rule that the user does the building. The implementer
    installs whatever tools it needs (passwordless `sudo` and `apt-get`
    work, PyPI is reachable), builds the firmware, makes and installs the
    zip, runs real stations, and repeats until the acceptance run below
    passes. It asks nothing. It commits nothing in any repository: every
    change stays in the working trees. Prose (READMEs, INTERNALS, comments
    beyond what the code needs) is written only after the acceptance run has
    passed.

## The firmware side

All paths relative to the meshtastic clone unless stated. Verify each line
number against the tag before editing.

### Build environment

New `variants/native/sim-mesh/platformio.ini` (in the patch):

```ini
[env:sim-mesh]
extends = native_base
build_flags = ${portduino_base.build_flags_common}
  -I variants/native/portduino -li2c -lstdc++fs
  -DHAS_SCREEN=0 -DMESHTASTIC_EXCLUDE_SCREEN=1 -DSIM_MESH=1
  -Wl,--wrap=bind -Wl,--wrap=listen -Wl,--wrap=accept -Wl,--wrap=close
build_src_filter = ${native_base.build_src_filter} -<graphics/Panel_sdl.cpp> -<graphics/TFTDisplay.cpp> -<mesh/raspihttp/>
lib_deps = ${native_base.lib_deps}
  simradio-portduino=symlink:///home/spangap/reticulous/sim-mesh/radio/portduino
lib_ignore = ${portduino_base.lib_ignore}
  LovyanGFX
extra_scripts = ${env.extra_scripts}
  pre:/home/spangap/reticulous/sim-mesh/radio/portduino/casefold.py
  post:/home/spangap/reticulous/sim-mesh/radio/portduino/cross.py
```

- No `-DPORTDUINO_LINUX_HARDWARE`: that removes BlueZ, libgpiod and spidev,
  makes `initGPIOPin` a no-op (`PortduinoGlue.cpp:711–730`) and stops
  `SPI.begin` from opening `/dev/spidev*`, so config pins 1–4 need no
  `/dev/gpiochip`.
- No `-DHAS_UDP_MULTICAST`: the UDP mesh (224.0.0.69:4403 with
  `SO_REUSEPORT`) would join every station on the host into one mesh past
  the radio. Compiled out entirely.
- Web server: compiled out. The firmware builds it whenever the build host
  has `<ulfius.h>` (`__has_include` in `main.cpp:1023` and
  `mesh/raspihttp/PiWebServer.h:7`), and this env does not link ulfius, so a
  host with the header would fail to link. The env's `build_src_filter` drops
  `mesh/raspihttp/`, and the patch makes both `main.cpp` guards (the include
  at `:78` and the start at `:1023`) `#if __has_include(<ulfius.h>) &&
  !defined(SIM_MESH)`. config.yaml has no `Webserver:` either.
- Build needs (host): libyaml-cpp-dev libuv1-dev libi2c-dev libusb-1.0-0-dev
  libbsd-dev libssl-dev, plus Portduino's libgpiod-dev headers
  (`sim-mesh-firmware/README.md`).
- The container starts without PlatformIO and without most of these headers
  (only libbsd-dev and libusb-1.0-0-dev are there); "Tools to install",
  below, puts them in place.
- The platform pin (`61067ac`) is two commits ahead of
  `competition/platform-native` (`cab4b21`); the framework difference is six
  lines of I2C. Use meshtastic's pin.
- The framework's WiFi library is at
  `~/.platformio/packages/framework-portduino/libraries/WiFi` (Meshtastic/WiFi
  `92b0641`).

### The patch (`firmware.patch`, all under `#ifdef SIM_MESH` or weak hooks)

1. **Radio backend.** `src/platform/portduino/PortduinoGlue.cpp`: declare
   `__attribute__((weak)) void native_radio_backend_init() {}` and call it
   after `gpioInit(max_GPIO+1)` (≈ line 590) and the pin set-up, immediately
   before `SPI.begin` (≈ lines 670–675). `HardwareSPI::begin` keeps an
   existing chip (`LinuxHardwareSPI.cpp:148–169`), so the installed `SimChip`
   survives.
2. **Idle and wake.** `src/concurrency/InterruptableDelay.cpp:14–33`:
   under `SIM_MESH`, `delay(ms)` calls `rnode_idle(ms)` and returns `false`
   (its only caller, `main.cpp:1241`, ignores the result); `interrupt()` and
   `interruptFromISR()` call `rnode_wake()`.
3. **API threads due on input.** `OSThread` is a private base of both
   `ServerAPI` and `APIServerPort` (`src/mesh/api/ServerAPI.h`), and the
   port's instance is the file-static `apiPort` in
   `src/mesh/api/WiFiServerAPI.cpp:7`, so nothing outside can reach
   `setIntervalFromNow`. Under `SIM_MESH`: a public `void simMeshDue() {
   setIntervalFromNow(0); }` in `ServerAPI`; a public `void simMeshDue()` in
   `APIServerPort` that does the same for itself and calls
   `openAPI->simMeshDue()` when `openAPI` is set; and in `WiFiServerAPI.cpp`
   a free `void simMeshApiDue() { if (apiPort) apiPort->simMeshDue(); }`. In
   `src/main.cpp`, right after `mainDelay.delay(delayMsec)` (`:1241`), under
   `SIM_MESH`: `if (rnode_idle_fd_ready()) simMeshApiDue();`.
4. **Spins.** `LockingArduinoHal` (`src/mesh/RadioLibInterface.h/.cpp`):
   under `SIM_MESH` override RadioLib's `yield()` to `rnode_idle_radio(1)`
   (below: the idle without the watched descriptors), so `scanChannel`'s wait
   for channel-activity-detection done (RadioLib 7.6.0 `SX126x.cpp:427–439`,
   called from `SX126xInterface.cpp:357`) idles until DIO1 rises instead of
   spinning on `sched_yield`. It must not be `rnode_idle`: an API byte that
   lands during the scan would end every one of those idles at once, and the
   loop would spin with T standing still until the ether's one-second grace
   for unread input lets T go.
5. **Clock.** `src/main.cpp:380`: under `SIM_MESH` skip the
   `popen("timedatectl …")` (it forks a shell under the shim) and set the RTC
   at NTP quality from `gettimeofday` (`perhapsSetRTC(RTCQualityNTP, &tv)`),
   so `rx_time` and `last_heard` are set. The run's epoch must be at or after
   the build day's midnight (`BUILD_EPOCH`, `RTC.cpp:234–252`); the default
   epoch (wall clock at ether start) always is.
6. **Reboot.** `src/Power.cpp:762–785` (`Power::reboot`): under
   `SIM_MESH`, after `deInitApiServer()` and `SPI.end()`, `fflush` and
   `exit(0)` instead of Portduino's `reboot()`.

### simradio-portduino (generic, in sim-mesh `radio/portduino/`)

All of the following goes in a new source file beside the existing one,
`radio/portduino/idle.cpp`, which includes nothing of Arduino or Portduino
(POSIX only), so a test can compile it with the host's g++ alone;
`simradio_portduino.cpp` keeps the chip, the SPI and the pins, and its
`onPin` calls `rnode_wake()` on a DIO1 rise in place of signalling the
condition variable. `library.json` has `libArchive: false`, so both files'
objects are linked into every Portduino firmware.

- `rnode_idle(max_ms)`: a `poll()` on an eventfd (`EFD_NONBLOCK |
  EFD_CLOEXEC`, made on first use) plus the watched descriptors
  (`POLLIN | POLLRDHUP`), timeout `max_ms`. `rnode_wake()` writes the
  eventfd; the idle drains it before it returns. Semantics for MeshCore and
  microReticulum are unchanged (ends at `max_ms`, on a DIO1 rise or a wake;
  `max_ms == 0` returns at once; a wake before the idle ends the next one).
  `poll` is one of the shim's idle waits.
- `rnode_idle_radio(max_ms)`: the same wait on the eventfd alone, for a wait
  inside a radio operation that input must not end.
- New `void rnode_watch_fd(int)`, `void rnode_unwatch_fd(int)`, `int
  rnode_idle_fd_ready()` (whether the last `rnode_idle` ended on a watched
  descriptor; clears it). Guard the set with a mutex; at most 16 descriptors.
- **A descriptor whose peer has gone is unwatched by the idle itself**
  (`revents` has `POLLRDHUP`, `POLLHUP`, `POLLERR` or `POLLNVAL`), after
  reporting it ready once. Portduino's `WiFiClient` never notices end of
  stream (`WiFiClient.cpp` `available()` clears the error on a read of 0, so
  `connected()` stays true), and a descriptor at end of stream is readable
  for ever: left watched, every idle would return at once and T would never
  move. The idle must not `read`, `recv` or peek a watched descriptor to find
  this out: the shim counts what those take from a station's connection.
- New link-wrap helpers, used only by a firmware linked with the matching
  `-Wl,--wrap=`: `__wrap_bind` (an `AF_INET` bind to `INADDR_ANY` becomes
  `SIM_MESH_BIND_ADDR` when that is set, else passes through),
  `__wrap_listen` (watch the descriptor on success), `__wrap_accept` (watch
  the new one when ≥ 0), `__wrap_close` (unwatch, then close). Each calls
  its `__real_*`, **declared weak** (`extern "C" int __real_bind(int, const
  struct sockaddr*, socklen_t) __attribute__((weak));` and likewise the
  others): the MeshCore and microReticulum firmwares link this object
  without `--wrap`, where a plain reference to `__real_bind` would be an
  undefined symbol and break their builds. With `--wrap=bind` the linker
  resolves it to libc's `bind` (through the PLT, so the shim still sees it).
- Tests, `radio/tests/test_portduino_idle.py`, in the manner of
  `test_shim.py`'s stand-in: a small C++ program (`radio/tests/idle_standin.cpp`)
  compiled with `idle.cpp` by the host's g++ with `-Wl,--wrap=bind
  -Wl,--wrap=listen -Wl,--wrap=accept -Wl,--wrap=close`, run in real time
  without the shim, covering: the bind rewrite (`getsockname` after a bind to
  `INADDR_ANY` with `SIM_MESH_BIND_ADDR=127.0.0.2`); an idle that ends on a
  readable accepted descriptor and reports it; an idle that ends on
  `rnode_wake` and does not; `rnode_idle_radio` not ending on a readable
  descriptor; a peer's close reported once and then no longer ending idles;
  `close` unwatching. A second build of the same program without any
  `--wrap` flag must link (the weak `__real_*`).

### config.yaml (written by host.py at every start, in the station directory)

```yaml
Lora:
  Module: sx1262
  CS: 1          # SIMRADIO_PIN_NSS default
  Reset: 2
  Busy: 3
  IRQ: 4         # DIO1
  DIO2_AS_RF_SWITCH: true
  DIO3_TCXO_VOLTAGE: true
  SX126X_MAX_POWER: <board chip max, 22>
Logging:
  LogLevel: info
  AsciiLogs: true   # its stdout is host.py's pty, a terminal: no colour codes in the "fw:" lines
General:
  MaxNodes: 200
  MaxMessageQueue: 100
```

No `Webserver`, no `Config: EnableUDP`, no `spidev: ch341`.

## The zip: `host.py`

Model on `/home/spangap/reticulous/sim-mesh/firmware/meshcore-companion-sx1262_*/host.py`
(join_ether, private pty for the child, framed-RPC console split,
`PR_SET_PDEATHSIG`, `os._exit` on child exit). Differences:

- **Start.** `join_ether()` first (ctypes `simradio_station_open(SIM_MESH_NODE_ID,
  SIM_MESH_BIND_ADDR, SIM_MESH_ETHER)` from `SIM_MESH_RADIO_LIB`, in virtual
  time only). Write `config.yaml` (board figures from `SIM_MESH_BOARD`).
  Start `<HERE>/meshtasticd -c <dir>/config.yaml -d <dir>/state -h
  <hwid>` by absolute path with `SIM_MESH_NODE_ID =
  $MESHTASTIC_FIRMWARE_NODE_ID`.
- **Connect.** asyncio `open_connection(SIM_MESH_BIND_ADDR, 4403)`,
  retrying every 0.5 s on the run's clock (asyncio sleeps are shimmed).
  The server takes one client and a new one closes the old, so host.py is
  the only client. Its writes must not fail: the client socket is
  non-blocking and any `-1` drops it (`WiFiClient.cpp:123–141`), so host.py
  reads continuously. host.py never closes this connection and never opens a
  second one: when it is lost while the firmware still runs, host.py says so
  on the console and `os._exit(0)`s, and the station restarts whole.
- **Framing.** Each message is `0x94 0xC3`, big-endian 16-bit length, then
  the protobuf. On the read side, resynchronise on the magic; skip anything
  else.
- **Handshake.** `ToRadio{want_config_id = <random nonzero, not 69420/69421>}`;
  collect `my_info` (own node number), `node_info` (own first, then
  others), `config`, `moduleConfig`, `channel` until `config_complete_id ==
  nonce`. Only then print the marker `serial: framed rpc v1` and answer
  framed RPC. Packets queue in the firmware until the handshake completes.
- **Node database.** Kept live from `node_info` and every received
  `packet`: number → `{name (user.long_name), id "!%08x", hops_away, snr,
  last_heard}`.
- **Heartbeat.** `ToRadio{heartbeat{nonce=0}}` every 300 s of the run's
  clock (idle timeout is 15 min).
- **Packet ids.** host.py sets every `MeshPacket.id` (random nonzero 32-bit
  from `os.urandom`, seeded by the shim) so acks correlate.
- **Admin.** A `MeshPacket{to = own number, decoded{portnum = 6 ADMIN_APP,
  payload = AdminMessage}}`; local admin needs no session passkey.
- **Config copies.** host.py keeps the handshake's LoRa and device config
  and writes each `lora`/`device` command's changes into its copy, so a
  second command in the same transaction builds on the first.
- **Bounds.** Every command answers within 4 s of the run's clock (framed
  RPC gives a command 5 s): a `send` whose `queueStatus` has not come by then
  answers `{"error": "no answer from the firmware"}`.
- **Console language** (one line per command, same for framed RPC and a
  person typing; reply is one JSON object, `{"error": …}` on failure):

  | Command | Does | Reply |
  |---|---|---|
  | `info` | | `{num, id, name, role, lora{…}, hop_limit}` from the handshake |
  | `edit begin` / `edit commit` | `begin_edit_settings` / `commit_edit_settings` (commit reboots in 7 s) | `{}` |
  | `owner <long> <short>` | `set_owner` | `{}` |
  | `lora <key>=<value> …` | `set_config.lora`, the handshake's LoRa config with these fields changed | `{}` |
  | `device role=<ROLE>` | `set_config.device`, the handshake's with `role` changed | `{}` |
  | `send <dest> <ch> <ack 0/1> <text…>` | text packet; `dest` a name, `!id` or `^all` | `{id, res}` from the `queueStatus` with `mesh_packet_id == id`, or `{error}` |
  | `traceroute <dest>` | `TRACEROUTE_APP`, empty `RouteDiscovery`, `want_response` | `{id}`; the result comes as an event |
  | `nodes` | | `[{num, id, name, hops_away, snr, last_heard}]` |
  | `nodeinfo` | `heartbeat{nonce=1}` (forces a NodeInfo broadcast) | `{}` |

- **Events**, each a line `mthost: {json}` (the driver's `console_line`):
  - `{"event":"routing","id":request_id,"from":"!…","error":"NONE|MAX_RETRANSMIT|…"}`
    for every `ROUTING_APP` packet with a `request_id`. A real ack has
    `from` = the destination. `from` = own number with `NONE` is an implicit
    ack (a want_ack broadcast heard rebroadcast).
  - `{"event":"recv","from":"!…","to":"!…|^all","ch":n,"text":…,"snr":…,"rssi":…,"hops":hop_start-hop_limit}`
    for every `TEXT_MESSAGE_APP` packet.
  - `{"event":"traceroute","id":request_id,"route":[…],"snr_towards":[…],"route_back":[…],"snr_back":[…]}`
    (SNR ÷ 4 in dB; `0xFFFFFFFF` hops shown as `null`).
- **Shutdown.** On EOF from the API (the firmware is rebooting) keep waiting
  for the child; on child exit `os._exit(0)`.

Rate limits the firmware applies to API clients (`PhoneAPI.cpp:828–859`):
text 1 per 2 s, traceroute 1 per 30 s. The firmware answers a refused text
with a `queueStatus` whose `res` is 0 and then a `RATE_LIMIT_EXCEEDED`
routing error for its id, so it reaches the driver as a `routing` event
(`sent`, then `failed`). It answers a refused traceroute with a
`queueStatus` (`res` 0) and nothing more, which is why the driver's
`traceroute` has a time limit. A `queueStatus` with a nonzero `res` is the
`send` reply's `error`. host.py does not queue or retry.

## The zip: `driver.py`

```python
from sim_mesh.driver import CommandError, chip_dbm, ...
from sim_mesh.meshtastic.driver import MeshtasticDriver
class Meshtasticd(MeshtasticDriver): ...
DRIVER = Meshtasticd
```

- `argv`: `["env", "SIM_MESH_NODE_ID=%d" % (id + 1_000_000),
  "MESHTASTIC_FIRMWARE_NODE_ID=%d" % id, "python3", <exec>]`. `env`:
  `SIM_MESH_IDLE=threads`. `sids`: `(id, id + 1_000_000)`. `console_sid`:
  `id + 1_000_000`.
- `configured`: `state/sim-configured` exists.
- `wait_up`: `rpc_ready(station, timeout, timeout)` then `info` answers.
- `run`: `rpc_query`, JSON-decoded, `{"error"}` raised as `CommandError`.
- Pending settings per station (decision 7), everything LoRa (`radio`,
  `tx_power`, `hop_limit`) merged into one `lora` line. `flush` with nothing
  pending returns at once. `flush` with something pending: `edit begin`,
  then `owner`, `lora`, `device` (those that are pending), then `edit
  commit`; on its reply write `state/sim-configured` and clear the pending
  set; then `await self.pause(station, 30)` and raise `CommandError("… did
  not restart")` if that returns. The pause is normally ended by the task's
  cancellation when the process exits; let `CancelledError` through
  untouched.
- After setup (`configured` True), each settings verb does `edit begin`, its
  one command, `edit commit` itself, then waits for the restart from where
  it is (a script's command, not the `after_start` task, so nothing cancels
  it): note `station.starts`, then `pause` in 0.5 s steps until
  `station.starts` has grown and `station.status == "up"`, at most 60 s,
  else `CommandError`. It does not call `wait_up` itself; simd's
  `after_start` for the new start does.
- `name(name)`: `owner <name> <name[:4]>`.
- `radio(freq_mhz, sf, bw_khz, cr, tx_dbm, sync, preamble)`: decision 8.
  `bandwidth` as in the findings' bandwidth codes; refuse a width with no
  code. Region: the first region in the firmware's table order (findings)
  whose span contains `freq_mhz` and is at least the bandwidth wide. If none
  qualifies, refuse with `CommandError` naming the frequency and bandwidth.
  The firmware would otherwise silently fall back to LONG_FAST. The
  driver keeps the table as a constant copied from the firmware. The
  station's region is reported in `diagnostics` because it sets the duty
  cycle (EU_868 10 %) and the power limit.
- `tx_power(dbm)`: `lora tx_power=<chip_dbm(board, dbm)>`.
  `tx_power = 0` means "region maximum" in Meshtastic, so a computed 0 is
  sent as 1.
- `diagnostics()`: `{"info": info, "nodes": nodes}`.
- `console_line`: parse `mthost: ` lines:
  - `routing` for a sent id: from the destination with `NONE` gives
    `delivered`; any error gives `failed` with `why` = the error name. The
    implicit ack of a broadcast is not a status.
  - `recv`: `msg_received(mid from untagged(text), sender=<from id>)`, or
    `chan=<ch>` when `to == ^all`. Text without a tag is ignored.
  - `traceroute`: resolves the pending verb's future. The verb waits for it
    at most 60 s of the run's clock (`asyncio.wait_for` around a future
    would be on the wall clock: wait with `pause` in steps instead), then
    raises `CommandError("no traceroute answer from <dest>")`.
- `current_role(station)` is `async`, as simd awaits it (`simd.py`
  `read_role`): it asks `info` and maps its `role`.

## The category: `sim_mesh/meshtastic/` (sim-mesh)

`sim_mesh/meshtastic/__init__.py` (`CATEGORY = "meshtastic"`) and
`driver.py`: `MeshtasticDriver(Driver)`, `category = "meshtastic"`, `VERBS
+= ("role", "hop_limit", "sendtext", "traceroute", "nodes", "nodeinfo")`,
each defaulting to `cannot`. Their names are Meshtastic's own (its CLI's
`--sendtext`, `--traceroute`, `--nodes`), as `meshcore`'s are meshcore-cli's.

| Verb | Means | Returns |
|---|---|---|
| `role(role)` | `client`, `client_mute`, `client_hidden`, `client_base`, `router`, `router_late`, `tracker`, `sensor`, `tak`, `tak_tracker`, `lost_and_found` | |
| `hop_limit(n)` | hops a packet it originates may take, 0–7 | |
| `sendtext(text, mid, dest=None, ch_index=0, want_ack=True)` | a text message to the node named `dest`, or to the channel when `dest` is None | nothing; simd answers the script with `mid` |
| `traceroute(dest)` | | `{route, snr_towards, route_back, snr_back}`, hops as node names where known |
| `nodes()` | | `[(name, id, hops_away, snr, last_heard)]` |
| `nodeinfo()` | a NodeInfo broadcast now | |

`current_role(station)`: `router` for ROUTER / ROUTER_LATE, `client`
otherwise (the role poll's vocabulary, `sim_mesh/__init__.py:47–48`).

Events: `sendtext` reports `sent` when the firmware queued it (the `send`
reply's `res == 0`) and `failed` with `why` when it refused. Then for a
direct message `delivered` or `failed` from the routing event. A channel
message ends at `sent`. The firmware always ends a want_ack direct message
with an ack or `MAX_RETRANSMIT`, so there is no driver-side expiry except
across a restart: messages still open when the station restarts are
reported `failed`, `why: "station restarted"`.

## sim-mesh touch points (from the `meshcore` precedent)

- `testbed/sim_mesh/driver.py`: receive the shared message helpers
  (decision 11).
- `testbed/sim_mesh/meshcore/driver.py`: import them from the base.
- `testbed/firmware.py:74`: `meshtastic` already in `CATEGORIES`.
- `testbed/drivers.py:34–35, 41`: import `MeshtasticDriver`, add to
  `CATEGORY_CLASSES`.
- `testbed/simd.py`: `MESHTASTIC_TO_VERBS = ("sendtext", "traceroute")`,
  `MESHTASTIC_MID_VERBS = ("sendtext",)` beside the meshcore ones (≈ 208);
  the `to → dest` branch (≈ 1697–1704) and mid minting (≈ 1732) for
  `category == "meshtastic"`; its docstring (≈ 140–148). `traceroute`
  without `to` is refused as the meshcore verbs are; `sendtext` without `to`
  is a channel message and passes no `dest`. The library's
  `sendtext(text, to=None, ch_index=0, want_ack=True)` leaves `to` out of
  what it gives when it is None.
- `testbed/sim_mesh/library.py`: `class _Meshtastic(_Layer)` with an
  `@verb` per category verb (as `_Meshcore`, ≈ 643–688) and
  `Nodes.meshtastic = _Accessor(_Meshtastic)` (≈ 695–697); its docs
  (≈ 101–114).
- `testbed/scripts/startup.py`: decision 9, and
  `nodes(tag="router").on_first_boot(Node.meshtastic.role("router"))`
  beside the `transport` rule.
- `testbed/stub_firmware.py`: a `MESHTASTIC_DRIVER` stub like
  `MESHCORE_DRIVER` (≈ 80–117).
- Tests: `test_script.py:252–254` and `test_simd.py:433–435` assert that a
  `meshtastic` firmware is refused; they become positive tests. Add the
  meshtastic counterparts of `test_script.py:237–249` (layer, verbs) and
  `test_simd.py:463–497` (meta, mid minting, events).
- UI: nothing (`ScriptsPage` filters firmware by category generically).
- `sim_mesh/__init__.py` `PROTOCOLS`: not touched (no frame parser for the
  airtime and sequence tools in this plan).

## The zip script: `sim-mesh-firmware/meshtastic/make_zip.py`

`make_zip.py PROGRAM [--arch ARCH]`:

1. Generate bindings (this exact recipe was run against the clone and
   imports under the pure-Python runtime). `pip install --target <tmp>/tools
   grpcio-tools==1.84.0`, then with `PYTHONPATH=<tmp>/tools`: `python3 -m
   grpc_tools.protoc -I <clone>/protobufs --python_out=<tmp>/pylib
   <clone>/protobufs/meshtastic/*.proto <clone>/protobufs/nanopb.proto`.
   That gives `pylib/meshtastic/*_pb2.py` and `pylib/nanopb_pb2.py`
   (`deviceonly.proto` imports it).
2. `pip install --target <tmp>/pylib --no-deps --only-binary :all:
   --platform any --implementation py --abi none protobuf==7.36.2`, which
   takes the `py3-none-any` wheel. Check, with `PYTHONPATH=<tmp>/pylib
   PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`, that `from meshtastic
   import mesh_pb2, admin_pb2, portnums_pb2, config_pb2` works.

Steps 1 and 2 are a function, `build_pylib(dest)`, that the tests use too:
the container's Python has neither `protobuf` nor `grpcio-tools`, so
`test_host.py` builds `sim-mesh-firmware/meshtastic/.pylib` with it once
(skipped when it is there; the directory is git-ignored), puts it on
`sys.path` and sets `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` before
importing. PyPI is reachable from the container.

3. Copy `PROGRAM` as `meshtasticd`, `host.py`, `driver.py`; write
   `node.yaml` (decision 13, `arch` from `--arch` or `uname -m`, version the
   UTC stamp).
4. Zip as `meshtastic-sx1262_<arch>_<stamp>.zip`.

The implementer leaves the clone on its local branch `sim-mesh` (made from
the tag, the clone's present detached HEAD) with the changes in its working
tree, uncommitted, new files marked with `git add -N`, and writes
`firmware.patch` as `git diff v2.7.26.54e0d8d`, again after every change to
the clone; it then proves the patch on a pristine tree: `git worktree add
<scratch> v2.7.26.54e0d8d`, `git -C <scratch> apply --check firmware.patch`,
and removes the worktree.

The build, which the implementer runs itself, as often as it takes:

```sh
cd /home/spangap/reticulous/competition/meshtastic_firmware   # on branch sim-mesh, changes in the tree
pio run -e sim-mesh
python3 /home/spangap/reticulous/sim-mesh-firmware/meshtastic/make_zip.py .pio/build/sim-mesh/meshtasticd
/home/spangap/reticulous/sim-mesh/sim firmware add meshtastic-sx1262_<arch>_<stamp>.zip
```

## Tools to install

The container (Ubuntu 24.04, aarch64) is a firmware build environment, not
sim-mesh's image: its `python3` is ESP-IDF's environment, without `aiohttp`,
`yaml` or `pytest`, there is no `cargo` on the path, and no PlatformIO. So,
before step A:

- **sim-mesh, natively.** `cd /home/spangap/reticulous/sim-mesh && ./sim
  install` (README "Getting started"): the system packages through apt, the
  Python environment `sim-mesh/.venv`, Rust. From then on every `sim`
  command and every test runs with `SIM_MESH_NATIVE=1` in the environment
  and with `sim-mesh/.venv/bin` first on `PATH` (so `python3` is the one
  with `aiohttp`, `yaml` and `pytest`); there is no Docker in the container.
  Run the three test suites (README "Tests") once before changing anything,
  and note what fails already, so a later failure is known to be new or not.
- **The firmware's headers.** `sudo apt-get install -y libyaml-cpp-dev
  libuv1-dev libi2c-dev libgpiod-dev libssl-dev pkg-config` (libbsd-dev and
  libusb-1.0-0-dev are installed).
- **PlatformIO.** `python3 -m venv ~/.platformio/penv &&
  ~/.platformio/penv/bin/pip install platformio`, and use
  `~/.platformio/penv/bin/pio`. Its first build of the env fetches the
  platform at Meshtastic's pin and the libraries. `~/.platformio/packages`
  already holds a `framework-portduino`; if PlatformIO replaces it with the
  pinned one, re-check the WiFi library facts in "Research findings" against
  the new copy (`WiFiServer.cpp`, `WiFiClient.cpp`), since the wraps and the
  end-of-stream rule rest on them.
- Anything else a step turns out to need is installed the same way.

## Acceptance

The job is done when all of this holds, on the built firmware, not on
stand-ins:

1. `pio run -e sim-mesh` in the meshtastic clone compiles and links, and
   `make_zip.py` makes a zip that `sim firmware add` installs.
2. `pio run -e sim-mesh` in
   `/home/spangap/reticulous/competition/attermann_microReticulum_Firmware`
   still compiles and links (it links simradio-portduino without any
   `--wrap`, which proves the weak `__real_*` and the idle's unchanged
   behaviour for the firmwares that share the library). Leave that clone
   otherwise untouched, and install nothing from it.
3. A script of the implementer's own, `testbed/scripts/meshtastic-check.py`,
   run with `sim run meshtastic-check --geodata <G> --nodeset <N>` on a
   small nodeset of its own making (three stations, each in range of the
   next; `testbed/geodata/berlin-centre` is installed) with every node on
   the new firmware, in virtual time (`--time max`), and its record
   (`events.jsonl`) shows, without a person:
   - each station set up once: one restart after its first-boot settings,
     then `up`, and not set up again;
   - after `nodeinfo()` and a wait, each station's `nodes()` names the
     others;
   - a direct `sendtext` from the first to the second: `msg.status` `sent`,
     `msg.received` at the second with the same `mid`, `msg.status`
     `delivered` at the first;
   - a channel `sendtext`: `msg.received` at the other stations, the
     sender's status ending at `sent`;
   - a `traceroute` from the first to the third that returns a route;
   - a settings verb after setup (`hop_limit`) that restarts the station and
     returns with it `up`.
4. The same script in real time (`--time real`) gives the same outcomes.
5. The virtual-time run, twice with the same `--seed` and `--epoch`, puts
   the same `msg.*` events at the same T. If it does not, find out which
   input reached a station outside T (simd's log names a channel that "lets
   go of T") and fix it; a run that only sometimes stalls a second on unread
   input is recorded in the hand-off, not chased for ever.
6. The virtual-time run's pace is sane: simd's log shows no station stuck
   busy, and T advances well faster than the wall clock while the network
   is quiet. A run that crawls means an idle that is not idling (a
   descriptor readable for ever, a spin): fix it.
7. All three sim-mesh test suites pass, bar what failed before the work
   began, and so do `test_host.py`, `test_driver.py` and
   `test_portduino_idle.py`.
8. A run with one MeshCore companion among the Meshtastic stations still
   brings the companion up (the startup script's radio rule, decision 9).

Stop every simulation the work started (`sim stop`, and the front it
started) before finishing, and leave none running.

## Work, in order

Everything is done in one go, without questions: where this plan and the
code disagree, the code as read decides, and the final report says what
differed. Line numbers and behaviours of the firmware written here were read
from the source but never compiled or run; the compiler and the acceptance
run decide. When the firmware does not do what this plan expects, read it,
change the patch, the host or the driver to fit what it does, rebuild and
run again, keeping to the decisions above; only a decision that turns out
impossible is departed from, and the final report says so and why.

A. Shared message helpers into the base driver; meshcore imports them;
   tests green.
B. simradio-portduino: poll-based idle, watched descriptors, the wrap
   helpers; `radio/tests`.
C. The `meshtastic` category in sim-mesh (module, drivers table, simd,
   library, stub, tests, startup split).
D. `host.py` with `test_host.py`: a fake meshtasticd (asyncio TCP server on
   loopback speaking FromRadio) covering the handshake, framing
   resynchronisation, every console command, every event, EOF/child exit.
E. `driver.py` with `test_driver.py`: radio mapping and region table, the
   pending/flush transaction, console_line → events, restart → open messages
   failed.
F. `firmware.patch` and the env; `make_zip.py`; build, install the zip, and
   run until every point of "Acceptance" holds.
G. After the acceptance run has passed: docs. Write the README's
   `meshtastic` section (§7b) and correct the category lists at `:172–175`
   and `:2106`. Update the README's walkthrough table, INTERNALS (`:1794`,
   and the idle and descriptor rule for Portduino firmwares), the
   `radio/portduino/README.md` (new functions, wraps) and
   `sim-mesh-firmware/README.md` (table row, build line). In
   `sim-mesh.net/contract.md`, add §7a and §7b. `contract.md:74`,
   `using-firmware.md:32–33` and `index.md:54–55` are stale; correct them.

## Research findings behind the decisions (settled)

- **Virtual time and the descriptor-woken idle.** The shim's timed `poll`
  (`radio/shim/simclock.c:768–790`, `node_wait` at `:572–609`) records the
  descriptors a blocked thread waits on (`wait_on`), and `census_quiet`
  (`:451–470`) only declares the station idle when none of them is ready.
  So a firmware idling in `poll()` on its API sockets is never declared idle
  with bytes waiting for it, and wakes when they land. The current
  `rnode_idle` (a `pthread_cond_timedwait`) gives the census no descriptors,
  which is why it must become a `poll()`. After a wake, `StreamAPI::readStream`
  (`src/mesh/StreamAPI.cpp:110–165`) drains everything available in one run
  and then polls every 5 ms for 2 s; that is extra idles, not a correctness
  problem.
- **WiFiServer** uses `socket`, `bind` (to `INADDR_ANY`), `listen(psock,
  5)`, a non-blocking `accept` (not `accept4`), and `::close`
  (`libraries/WiFi/src/WiFiServer.cpp:50–111`, `WiFiClient.cpp:88, 207`). The
  env wraps `bind`, `listen`, `accept` and `close`; no `accept4` wrap. The
  shim still sees `listen` (a `--wrap`ped call reaches libc through the PLT,
  where `LD_PRELOAD` interposes).
- **`override_frequency` is used verbatim** (`src/mesh/RadioInterface.cpp:865–867`),
  never range-checked against the region. But `applyModemConfig`
  (`:794–830`) requires the region's span (`freqEnd - freqStart`) to be at
  least the bandwidth. If it is less, it records
  `INVALID_RADIO_SETTING` and silently falls back to LONG_FAST with presets
  on. So the region must be chosen for its span as well (rule below). The
  region also sets the duty cycle and the power limit.
- **Bandwidth codes** (`src/mesh/MeshRadio.h:98–125`): `bandwidth` is
  integer kHz except 31 → 31.25, 62 → 62.5, 200 → 203.125, 400 → 406.25,
  800 → 812.5, 1600 → 1625 (the last four are 2.4 GHz). There are no codes
  for 7.8, 10.4, 15.6, 20.8 or 41.7 kHz: refuse those. The driver maps
  `bw_khz` 31.25/62.5 to 31/62, accepts 125, 250 and 500 as they are, and
  accepts the 2.4 GHz widths for `LORA_24`.
- **The chip model accepts everything meshtasticd sends**
  (`sim-mesh/radio/src/model.cpp:877–921`): `SetDIO2AsRfSwitchCtrl`,
  `SetDIO3AsTCXOCtrl`, `SetRegulatorMode`, `Calibrate`, `CalibrateImage`,
  `SetCadParams`, `SetCad`, `SetRxTxFallbackMode`, `StopTimerOnPreamble`,
  `SetLoRaSymbNumTimeout`, register reads and writes. Registers are kept as
  written, so the read-back-verified 0x8B5 write and the CRC polynomial
  read-modify-write in `SX126xInterface::init` pass. Keep both config.yaml keys.
- **Receive is plain `SetRx`.** RadioLib 7.6.0
  `SX126x::startReceiveDutyCycleAuto(16, 8)` computes `sleepPeriod = 0`,
  below `tcxoDelay + 1016`, and calls `startReceive(RX_TIMEOUT_INF)`
  (`SX126x.cpp:505–550`). The model has no `SetRxDutyCycle` and needs none.
- **Spins.** The model's BUSY is never busy (`model.cpp:653`), so
  `resetAGC`'s BUSY loop (`SX126xInterface.cpp:426–432`) exits at once and
  its `hal->delay(5)` is a shimmed `usleep`. `resetAGC` does not spin. The
  real spin is `SX126x::scanChannel` (`SX126x.cpp:427–439`): it loops on
  `digitalRead(DIO1)` plus `hal->yield()` for the whole CAD (2 symbols,
  ≈ 16 ms at SF11/250 kHz) before every transmission. The `yield()` →
  `rnode_idle(1)` override is required.
- **The firmware's region table** (`src/mesh/RadioInterface.cpp:43–219`), as
  `name start–end MHz, duty %, power limit dBm`, in table order:
  US 902–928 100 30; EU_433 433–434 10 10; EU_868 869.4–869.65 10 27;
  CN 470–510 100 19; JP 920.5–923.5 100 13; ANZ 915–928 100 30;
  ANZ_433 433.05–434.79 100 14; RU 868.7–869.2 100 20; KR 920–923 100 23;
  TW 920–925 100 27; IN 865–867 100 30; NZ_865 864–868 100 36;
  TH 920–925 10 27; UA_433 433–434.7 10 10; UA_868 868–868.6 1 14;
  MY_433 433–435 100 20; MY_919 919–924 100 27; SG_923 917–925 100 20;
  PH_433 433–434.7 100 10; PH_868 868–869.4 100 14; PH_915 915–918 100 24;
  KZ_433 433.075–434.775 100 10; KZ_863 863–868 100 30;
  NP_865 865–868 100 30; BR_902 902–907.5 100 30; LORA_24 2400–2483.5 100 10.
