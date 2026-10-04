# MeshCore on sim-mesh

## Goal

Three MeshCore firmwares on sim-mesh, in the `meshcore` category, each driving
the virtual SX1262 through MeshCore's own RadioLib driver:

| Base | Upstream example | The station's console |
|---|---|---|
| `meshcore-companion-sx1262` | `examples/companion_radio` | **meshcore-cli**, on the companion's protocol line as on a desk, carried over TCP |
| `meshcore-repeater-sx1262` | `examples/simple_repeater` | the repeater's own serial command line |
| `meshcore-room-sx1262` | `examples/simple_room_server` | the room server's own serial command line |

## Where things are

Our one fork is a repo under the `sim-mesh` GitHub organisation: MeshCore (the
Portduino platform, the sim-mesh variant, the drivers, the companion's host and
the packaging). meshcore-cli and meshcore_py are used as released. The upstream
clones are under `competition/`, each its own git repo:

| Clone | What it is | Licence |
|---|---|---|
| `competition/meshcore-dev_MeshCore` | the firmware: core library, examples, 87 board variants, PlatformIO | MIT |
| `competition/meshcore-dev_meshcore-cli` | `meshcore-cli`, the Python command line (`meshcore_cli.py`, one file) | MIT |
| `competition/meshcore-dev_meshcore_py` | `meshcore`, the asyncio companion-protocol library the command line is built on | MIT |
| `competition/portduino` | Meshtastic's framework-portduino, the Arduino API on Linux. The build uses `0fdf803`, which platform-native `86c62ed` pins. The facts below hold there and on `main` | LGPL-2.1 |

## Principle: keep changes to MeshCore small and upstreamable

- **sim-mesh-specific code goes in new files:** the `sim_mesh_sx1262` variant, plus the companion's host and the drivers.
- **Portduino is a platform.** It is added the way upstream adds one:
  - new files under `src/helpers/portduino/`;
  - a `PORTDUINO_PLATFORM` branch at each place that already switches on the platform (step 1).
- **Every other edit to an upstream file is a generic hook** that means something on a real board too, one commit each (step 3).
- **No `#ifdef SIM_MESH` in upstream files.**
- **Nothing of sim-mesh in the platform's files either:** no `SIM_MESH_*` name, in code or in a file's name, under `src/helpers/portduino/`. What reads sim-mesh's environment lives in the variant.

The result is a "MeshCore on Linux (Portduino)" platform, plus one variant
whose radio is sim-mesh's.

## What upstream gives us (checked in the source)

**MeshCore**
- **No Linux platform.** Every platform is ESP32, nRF52, RP2040 or STM32.
- **One radio driver for every board.** The radio is RadioLib (pinned commit `6d89348`, 7.x), wrapped by `CustomSX1262` and `CustomSX1262Wrapper`.
  - Variants construct it as `new Module(NSS, DIO1, RESET, BUSY, spi)` in `target.cpp`.
  - RadioLib 7 also takes `Module(hal, …)` with a HAL (hardware abstraction layer) of our own, and that is the seam.
- **RadioLib's Arduino HAL sends one byte per call.** Its `spiTransfer` makes one `SPI.transfer(uint8_t)` per byte. sim-mesh needs one whole frame per chip-select cycle (`radio/portduino/README.md`). A HAL subclass whose `spiTransfer` makes a single in-place `SPI.transfer(buf, len)` meets that without touching RadioLib.
- **Default radio settings:** 869.618 MHz, BW 62.5 kHz, SF8, private sync word (`0x12`), 16-symbol preamble. The ether models all of these except preamble length.
- **All three builds can forward; only the defaults differ.**
  - Repeater and room server: `allowPacketForward` returns false only when `repeat.disable` is set, and also enforces the flood hop limits. Both default to forwarding (`CommonCLI.h`, `disable_fwd = 0`), and the CLI has `set repeat`.
  - Companion: the preference defaults to off (`NodePrefs.h`, `disable_fwd = 1`) and is set over the companion protocol, as the last byte of `CMD_SET_RADIO_PARAMS`.
  - The companion refuses to turn it on off its allowed frequencies (`isValidClientRepeatFreq`): 433.000, 869.495 and 918.000 MHz, or the build's `ALLOWED_REPEAT_FREQ_RANGE`. The default 869.618 MHz is not among them.
  - So a forwarding companion is a setting plus a build flag, not a fourth build.
- **The random seed comes from the chip.** Each `main.cpp` seeds `fast_rng`, which draws the retransmit jitter, with `radio_driver.getRngSeed()`: RadioLib's `random()`, eight reads of register 0x0819 per byte with the chip in receive. `radio_new_identity()` in a variant's `target.cpp` draws the identity from the same source (`RadioNoiseListener`).
- **Radio settings on the command line take a restart.** `set radio f,bw,sf,cr` on the repeater and room server saves and answers `OK - reboot to apply`; `set tx` applies at once. The companion applies `CMD_SET_RADIO_PARAMS` live. None of them sets the sync word or the preamble length.
- **The companion speaks its protocol on USB serial** when built with `ENABLE_USB_INTERFACE`: `usb_serial_interface.begin(Serial)`, an `ArduinoSerialInterface` on whatever `Serial` is.
- **Repeater and room server read their command line from `Serial`.** `main.cpp` has a line editor over `Serial.available()`/`Serial.read()`, passes each line to `the_mesh.handleCommand(0, command, reply)`, and prints replies as `  -> …`.
- **A variant may say what `Serial` is.** `variants/wio_wm1110/WioWM1110Board.h` has `#define Serial Serial1`.
- **`MESH_DEBUG_PRINT` writes to `Serial`** (`MeshCore.h`). A companion on USB serial is built without `MESH_DEBUG`, as on boards.
- **Storage is an `fs::FS`, chosen per platform.**
  - `FILESYSTEM` is picked by platform in `IdentityStore.h`.
  - Each `main.cpp` mounts its platform's FS in a platform `#if` chain.
  - The ESP32-style branches open files with a third argument (`open(path, "w", true)`). There are eight such calls, in `IdentityStore.cpp`, `ClientACL.cpp`, `RegionMap.cpp`, `CommonCLI.cpp`, `DataStore.cpp` and the repeater's and room server's `MyMesh.cpp`.
  - `DataStore::formatFileSystem()` ends in `#error "need to implement format()"` for a platform it doesn't know.
  - `getStorageUsedKb`/`getStorageTotalKb` return 0 for an unknown platform, which is fine.
- **The main loops sleep only when nothing is pending, and not on every build.** The hook is `MainBoard::sleep(secs)`, guarded by `hasPendingWork()` (the outbound queue is not empty).
  - Repeater: `board.sleep(30)` when the `powersaving_enabled` preference is set (default off), nothing is pending, and two minutes have passed since boot; `board.sleep(0)` on nRF52.
  - Companion: `board.sleep(0)` when nothing is pending, on nRF52 only.
  - Room server: never.
  - With a packet queued for a later instant, every loop spins until it is due.
- **Each `setup()` waits before the radio exists.** `Serial.begin`, a `delay(1000)` (repeater and room server) and `board.begin()` all come before `radio_init()`.
- **The companion protocol's frames are the same on TCP as on serial** (`0x3c`, a two-byte length, the payload: meshcore_py's `tcp_cx.py` and `serial_cx.py`). Upstream's own TCP door, `SerialWifiInterface`, is ESP32-only.

**Portduino**
- **`Serial` writes but never reads.**
  - `SimSerial::write` is `putchar` to stdout.
  - `available()` returns 0 and `read()` returns -1 (`cores/portduino/linux/LinuxSerial.cpp`).
  - So MeshCore's `Serial` on Portduino has to be one of ours.
- **The FS is `PortduinoFS`**, an `fs::FS` over a directory: `--fsdir DIR` on the command line, or `~/.portduino/default` without it.
  - It has `open(path, mode)`, `exists`, `remove`, `rename`, `mkdir`, `rmdir`.
  - It has no `open(path, mode, create)` and no `format()`.
  - `PortduinoFS` is a global `fs::FS` over `portduinoVFS`, a global of another translation unit.
- **`portduinoSetup()` runs before `setup()`.** It is a weak function an application may define; the default prints `No portduinoSetup() found, using default settings...`.
- **It prints `Portduino is starting, VFS root at …` to stdout** before `setup()`.
- **With pins bound, the main loop doesn't sleep between `loop()` calls**, so a virtual-time run would never see the station idle without step 3.

**meshcore_py and meshcore-cli**
- **The companion protocol is built for one client.**
  - Replies carry no request id: meshcore_py's `send()` (`commands/base.py`) takes the next event of the expected type, and holds no lock while it waits.
  - A message fetched from the queue is gone for any other client.
  - meshcore_py fetches messages by itself: `start_auto_message_fetching()` calls `commands.get_msg()` on every `MESSAGES_WAITING`, outside meshcore-cli.
  - So everything that talks to a companion goes through one connection, and one command at a time has to be enforced at `send()`.
- **meshcore-cli works as a library.** Its command functions take the connection object and an output `sink` (`interactive_loop(mc)`, `process_line(mc, line, json_output=…, sink=…)`).
  - `interactive_loop` makes its own `PromptSession` with no input or output given, so it takes prompt_toolkit's current application session's.
- **Both packages declare `bleak` as a dependency** and import it inside a `try`, so a `pip install` brings it (and dbus-fast) unless told `--no-deps`.

**sim-mesh**
- **The chip model has no random-number register.** `radio/src/model.cpp` names no 0x0819, so RadioLib's `random()` reads whatever an unset register reads, the same on every station.
- **The Portduino idle does not say idle to the ether.** `rnode_idle` is a wait on a condition variable. A station on it is seen idle through the shim's thread census, `SIM_MESH_IDLE=threads`.
- **TCP between two processes of one station is counted already.** They share an address, and the one that listens owns the connections made to it (`ether/INTERNALS.md`, "TCP between two stations"). A pty between them is not counted.

## The work, in order

### 1. The Portduino platform (MeshCore fork)

**New files, `src/helpers/portduino/`:**
- **`PortduinoBoard`:** a `MainBoard`. Battery reads a fixed millivolt value, and `reboot()` is `exit(0)`, which sim-mesh turns into a restart.
- **`PosixSerial`:** a `HardwareSerial` on a pair of descriptors, reading without blocking and writing unbuffered.
  - Its descriptors are 0 and 1 by default. A terminal there is put in raw mode, as a UART passes bytes: a pty's own line discipline turns the CR that ends a line into LF, which MeshCore's line editor never takes as a line's end.
  - A reader thread waits on the descriptor (or the listener), keeps what arrives, and calls `rnode_wake()`, so input ends the board's idle at once.
  - With `MESHCORE_SERIAL_LISTEN=addr:port` it is instead the one TCP connection accepted on a listener bound there, taken without blocking: nothing is available and writes are dropped while no client is connected, and a client that goes makes room for the next.
  - The variant makes it MeshCore's `Serial` (step 2).
- **`PortduinoMeshFS`:** `PortduinoFS` plus `open(path, mode, create)` (which makes the missing directories of a path opened to write) and `format()`.
  - `format()` empties the FS root.
  - It is mounted at `--fsdir state`, so a station's store lives in its own `state/`.
  - `MeshFS` is a global with a VFS of its own, so it does not depend on `portduinoVFS` being made first; `MeshFS.begin()` puts it on the directory Portduino mounted, and each `main.cpp` calls it before using the store.
- **An RTC clock on `time()`:** the time shim already gives it the run's epoch.
- **A boot banner:** `PortduinoBoard::begin()` prints, to stdout, the MeshCore version, commit and build date, and the FS root.

**`PORTDUINO_PLATFORM` branches in upstream files, one commit** (the build found exactly these):
- `IdentityStore.h`: `FILESYSTEM` is `PortduinoMeshFS`.
- The three `main.cpp`: mount it (`MeshFS.begin(); fs = &MeshFS; IdentityStore store(MeshFS, "/identity")`), and the companion's `DataStore store(MeshFS, rtc_clock)` and its mount branch in `setup()`.
- The three `MyMesh.h`: the FS include (repeater `:8`, room server `:6`, companion `:18`).
- `DataStore.cpp`: `formatFileSystem()`, `return _fs->format();`. Its other chains end in an `#else` that `PortduinoMeshFS` answers as ESP32's FS does (open for read and write, the identity directory, the blob directory), and the storage sizes' `#else` is 0. `DataStore.h:23` is NRF52/STM32 only.
- Repeater `MyMesh.cpp` `formatFileSystem()` and `saveIdentity()`, room server's the same: chains with a branch per platform and an `#error` for the rest.
- `TxtDataHelpers.cpp`: `ltoa()`, which the C library here lacks; the Arduino API declares it in `itoa.h`.
- No change is needed at the eight three-argument `open` calls, nor at `CommonCLI.cpp:402` (`powersaving on` answers `Board not supported` in its `#else`).
- `SimpleMeshTables.h` and `RadioLibWrappers.cpp` test `ESP32` alone, for things Portduino does without.

**The build:**
- **`[env:portduino_*]` blocks in `platformio.ini`:** `platform-native` with `board = cross_platform`, at the commits Meshtastic pins. That makes three environments: companion, repeater and room server.
- **`WholeFrameHal`:** a RadioLib `ArduinoHal` subclass whose `spiTransfer` makes one `SPI.transfer(buf, len)`, as above. It lives in the Portduino platform, since spidev on a real Linux board wants whole frames too.

### 2. The sim-mesh variant (fork, new files)

**`variants/sim_mesh_sx1262/`:**
- `target.h` has `#define Serial SimSerialPort`, after the wio_wm1110 precedent, with `SimSerialPort` a `PosixSerial`.
- `target.cpp` constructs `Module(&hal, SIMRADIO_PIN_NSS, …DIO1, …RESET, …BUSY)`, `hal` a `WholeFrameHal`.
- **It defines `portduinoSetup()`, which calls `native_radio_backend_init()`.** That opens the station's link before `setup()` and its `delay(1000)`, as the contract asks ("opens its link early", §5), and before the first `SPI.begin()`.
- **`SimMeshBoard`,** a `PortduinoBoard`: its `begin()` adds sim-mesh's lines to the banner (base and stamp, node id, bind address and ether, and the radio and board from `SIM_MESH_BOARD`: chip, max dBm, front end).
- It generates identities from `getrandom` instead of radio noise. The time shim keys `getrandom` by seed and node id, so identities are distinct and reproducible.

**`lib_deps`:** `symlink://<sim-mesh>/radio/portduino`, plus `casefold.py` as a `pre:` script, per that library's README.

**The chip's random numbers (sim-mesh change, `radio/src/model.cpp`).** A read of register 0x0819 while the chip receives answers bytes from `getrandom`, which the time shim keys by seed and node id in a virtual-time run. That gives every station its own `fast_rng` seed, and so its own retransmit jitter, through upstream's unchanged `getRngSeed()`, and serves any other RadioLib firmware. First confirm what the model answers there today (two stations printing `getRngSeed()`), and add a test beside `radio/tests/test_model.py`.

### 3. Idle (fork: one generic hook)

- **Why not `MainBoard::sleep()`:** upstream calls it only when nothing is pending, on some builds, behind a preference. A station in virtual time has to wait while a packet is queued for a later instant too, or T never reaches that instant. The commit message says so, since the hook will otherwise read as a second `sleep()`.
- **The hook:** a virtual `MainBoard::idle(uint32_t max_ms)`, a no-op by default, called unconditionally at the end of each example's `loop()`: a wait of at most `max_ms` that any interrupt ends. That's one commit touching `MeshCore.h`, the three `main.cpp` files, and what says how long (`Dispatcher`, `PacketManager`, `RadioLibWrapper`, below), and it is a power-saving hook on a real board as well.
- **Portduino:** `idle` calls `rnode_idle(max_ms)`, which waits until `max_ms` passes, DIO1 rises or `rnode_wake()` is called, in node time. The platform has weak versions of both; sim-mesh's `radio/portduino` library has the strong ones, `rnode_wake()` among them (a sim-mesh change).
- **Saying idle to the ether:** all three drivers' `env` sets `SIM_MESH_IDLE=threads`, so the shim reports the station idle while its loop is in that wait. Without it the station crawls on the radio library's watchdog.
- **How long:** `Dispatcher::millisUntilDue(BOARD_IDLE_MAX_MILLIS)`, the next instant anything is due, with `BOARD_IDLE_MAX_MILLIS` (5 s, `MeshCore.h`) a cap for timers nothing announces. Every MeshCore timer is set with `futureMillis()` or checked with `millisHasNowPassed()`, so those two note each future instant they see (a check comes true at its timestamp + 1), and `millisUntilDue()` adds:
  - the outbound queue's next packet scheduled after now, and the inbound queue's next while no send is in progress. A packet already due waits on what `checkSend()` noted (the next transmit slot, the airtime budget, a busy channel's retry) or on the send's end, an interrupt, so it asks for no pass of its own;
  - the radio's `pollMillis()` (`mesh::Radio`, -1 by default): `RadioLibWrapper` takes its 64 noise-floor samples one a loop, so while sampling it asks for the next loop at once, as a board's loop runs them back to back, and 20 ms later when the channel was busy.
- **Two things a long idle laid bare:**
  - RadioLib waits `delayMicroseconds(1)` after each SPI transaction before reading BUSY. On Portduino that is a sleep, which in virtual time is an idle and a barrier per transaction. `WholeFrameHal` lets a wait of 1 µs or less be the pin read that follows, which takes longer on Linux.
  - Portduino finds an interrupt's edge by reading the pin once a loop (`gpioIdle()`). Between two receptions DIO1 goes low and high again while the station idles, the read sees high twice, and the interrupt is lost: the station heard the frame and never took it. sim-mesh's `radio/portduino` reports a rise since the last read as an edge (a sim-mesh change).
- **Measured:** three quiet repeaters run 600 s of T in 0.2 s of wall (1 070 barriers, a wake every 2 s each, the noise-floor calibration); with two companions and their hosts in place of two repeaters, 0.4 s (3 212). A fixed 20 ms idle took 10.9 s for the latter.

### 4. Repeater and room server: framed RPC on the console (fork, variant code)

```
station → sim-mesh   "framed rpc v1"                          printed once at boot
sim-mesh → station   F5 53 47 01 <id> <len:2> set repeat off
station → sim-mesh   F5 53 47 01 <id> <len:2> OK
```

- **Taking frames out:** `SimSerialPort` takes framed RPC frames (`spangap-core/docs/framed-rpc.md`) out of what it reads on descriptor 0, before `main.cpp`'s line editor sees them.
- **Answering:** each frame's command line goes to `the_mesh.handleCommand(0, line, reply)`, the same call the line editor makes, and `reply` comes back in a frame.
- **Everything else** passes through as typed, so a person at the console and the driver never interleave.
- **No upstream file changes.**

### 5. The companion station: two processes

```
station console pty ◄──► host (python3, sid B) ── meshcore_py TCP to <bind addr>:5000 ──► companion firmware (sid A, SX1262)
                          ├─ console demultiplexer: reads descriptor 0
                          │    ├─ framed RPC → process_cmds(mc, …)       for the driver
                          │    └─ every other byte → pipe input → meshcore-cli interactive_loop(mc)   for the person
                          └─ firmware stdout/stderr → console            its banner and log
```

- **The firmware is the stock companion with `ENABLE_USB_INTERFACE`.**
  - Its `Serial` is `PosixSerial` listening on TCP (`MESHCORE_SERIAL_LISTEN=$SIM_MESH_BIND_ADDR:5000`, set by the host). The protocol and its framing are the serial line's; only the carrier is a socket, which sim-mesh counts in virtual time already.
  - Its stdout and stderr stay text: Portduino's start-up line, the boot banner, anything else it prints.
- **`exec` is the host:**
  - it starts the firmware, and connects with meshcore_py (`MeshCore.create_tcp(host, 5000)`), trying again on the run's clock until the firmware listens;
  - it passes the firmware's stdout and stderr through to the console, marked as firmware output.
- **The console demultiplexer:** the host alone reads descriptor 0, through `os.read`, so the shim counts it.
  - It takes framed RPC frames out and answers them.
  - It writes every other byte to a prompt_toolkit pipe input (`create_pipe_input()`).
- **The person:** meshcore-cli's own `interactive_loop(mc)`, run inside `create_app_session(input=<the pipe input>, output=<the console>)`, so its `PromptSession` reads what the demultiplexer passes on. No change to meshcore-cli.
- **The driver:** it asks over framed RPC on the same console pty.
  - The host runs each command line as meshcore-cli's own command line does, `process_cmds(mc, shlex.split(line), json_output=True, sink=…)`, and frames the JSON back, with what meshcore-cli logged as an error. `process_line` answers a command it does not know with nothing, and sim-mesh's readiness probe needs an answer: `process_cmds` says it did not know it.
  - The host reports what the driver hears of as `mchost: {json}` lines on the console: every acknowledgement (`{"event": "ack", "code"}`) and every message fetched (`{"event": "recv", "from" | "chan", "text"}`).
  - The driver's commands may be echoed in the console.
  - The driver never asks for messages (`recv`, `sync_msgs`): meshcore_py's own fetching takes each from the queue once, and the host hears of it as an event.
- **Send confirmations and incoming messages** are events on the one connection, so both consumers see them, and a message is fetched once.
- **One command at a time:** at start the host wraps `mc.commands.send` in an `asyncio.Lock`. That is the one place every command passes (the interactive loop's, the driver's, and meshcore_py's own message fetching), so none takes another's reply. The lock is held for a command and its reply, not for a wait on a later event (an ACK, a login).
- **Restarts:**
  - If the firmware exits (a reboot), the host exits, and the station restarts as a whole.
  - If someone quits the interactive loop, only the loop restarts.
- **The zip carries** meshcore-cli, meshcore_py and their dependencies under `pylib/`, for CPython 3.12 aarch64: prompt_toolkit, wcwidth, pyserial, pyserial-asyncio-fast, pycryptodome, pycayennelpp, requests and its own (urllib3, certifi, idna, charset-normalizer). pyserial stays because both packages import it at import. `bleak` is left out: both import it inside a `try`.
- **Virtual time: the host joins the ether as a second process of the station** (contract §4, "It may be several processes"):
  - `sids` returns `(node_id, node_id + 1_000_000)`, and `console_sid` returns the second.
  - The driver's `env` sets `SIM_MESH_IDLE=threads` for both processes. The time shim reads `SIM_MESH_NODE_ID` when it loads, before any Python runs, so the host's own id is set on its command line: the driver's `argv` is `env SIM_MESH_NODE_ID=<B> MESHCORE_FIRMWARE_NODE_ID=<A> python3 host.py`, and the host gives the firmware `A`.
  - At start the host calls `simradio_station_open` through `ctypes` on `$SIM_MESH_RADIO_LIB`.
  - The time shim already handles what CPython waits in: semaphores, and condition deadlines on the monotonic clock.
  - The TCP connection between the two is counted as it stands: the firmware listens, so the connection is its, and the host's end is the process that reports from it. No sim-mesh change.

### 6. The `meshcore` category (sim-mesh)

`testbed/sim_mesh/meshcore/driver.py`, beside `reticulum`, registered in `drivers.py`; `.meshcore.<verb>` in the script library; README §7a.

- **simd** passes `to` of `msg`, `path` and `reset_path` as `dest` unchanged, a contact's name (a node's own once it has advertised it), and answers `msg` and `chan` with the message's id, which it gives them, as it does `lxmf.send`.

- **Naming:** the category's verbs carry meshcore-cli's own command names and mean what those commands mean. They are not shaped like `reticulum`'s verbs.
- **Cross-protocol tests:** a meta-library above the categories, written later, turns a protocol-neutral intent ("send a message from A to B") into each protocol's own commands. That is how delivery is compared apples for apples.

Every firmware's verbs (`name`, `radio`, `radio_up`, `tx_power`, `diagnostics`, from `sim_mesh.driver.Driver`), then the category's:

| Verb | meshcore-cli | Means | Returns |
|---|---|---|---|
| `repeat(on)` | `set repeat` | forwarding on or off: all three builds. The repeater's and room server's CLI has `set repeat on|off`; meshcore-cli has it for the companion as the fifth field of `set radio f,bw,sf,cr,on|off`. The companion build sets `ALLOWED_REPEAT_FREQ_RANGE` to the whole band the firmware takes (150–2500 MHz), so it is not refused off upstream's three frequencies | |
| `advert()` | `advert` | a zero-hop advert (the repeater's and room server's CLI: `advert.zerohop`) | |
| `floodadv()` | `floodadv` | a flooded advert (their CLI: `advert`) | |
| `contacts()` | `contacts` | | `[(name, public-key prefix, path length or None)]` |
| `msg(dest, text, mid)` | `msg` | a direct message to a contact; `mid` is sim-mesh's id | |
| `chan(nb, text, mid)` | `chan` | a message on channel `nb` | |
| `path(dest)` | `path` | | the contact's path: hops, or None for flood. Read from the contact's record (`contacts`): meshcore-cli 1.6.5's `path` fails on meshcore_py 2.3.15's contacts, which carry no `out_path_hash_len` |
| `reset_path(dest)` | `reset_path` | back to flood for that contact | |

**Every firmware's verbs, on MeshCore:**
- **`radio(...)`** takes `freq_mhz`, `bw_khz`, `sf` and `cr`, and refuses `sync` and `preamble`, which MeshCore has no setting for. The companion applies it live. The repeater and room server save it and apply it at a restart, so their drivers note that it was set and restart the station in `flush`.
- **`tx_power(dbm)`** is at the connector: the driver sets `chip_dbm(station.board, dbm)`, as the chip's power is what MeshCore's setting is.
- **`wait_up`** is `rpc_ready`: the repeater and room server print the framed RPC marker from the variant, the companion's host once its connection to the firmware answers.

**Events:**
- **`msg.status`** under `mid`: `sent`, then for `msg` `delivered` or `failed`.
  - The companion pushes "send confirmed" (an ACK). The host sees it on its connection and reports it to the driver. A timeout gives `failed`.
  - A channel message has no ACK, so `chan` ends at `sent`.
- **`msg.received`** from the receiving station: its host reports every direct and channel message meshcore_py fetches, with the sender's public-key prefix or the channel, and the text. sim-mesh's id travels in the text, as ` #<mid>` at its end (`tagged`/`untagged` in `sim_mesh.meshcore.driver`), so the report carries `mid`. A room server's posts reach its members the same way. This is what says a channel message arrived, and what the cross-protocol comparison counts.
- Repeater and room server have no user messaging. Their drivers implement every firmware's verbs plus `repeat`, `advert` and `floodadv`, through their CLI (`set name`, `set radio`, `set tx`, `set repeat`, `advert`), and refuse the rest.

### 7. Packaging (fork)

- **One script** builds the three environments, makes the three `<base>_aarch64_<stamp>.zip`, and checks with `ldd` that nothing outside the allowed set is loaded.
- **Each zip carries:**
  - `node.yaml` (`category: meshcore`, `radio: sx1262`, `hardware: ESP32-S3`);
  - its `driver.py`;
  - the executable, which the driver starts with `--fsdir state`.
  - The companion zip also carries the host and `pylib/`.
- **Publishing:** `sim firmware publish ZIP…` puts the zips on the pre-built list, run on the host, where `sim` takes the token of the `gh` logged in there.

## Building it: instructions for the implementer

You are picking this up cold. Do steps 1–7 above in this order. What follows is
the how.

### Rules of the house

- **Read first:**
  - this whole plan;
  - sim-mesh's `README.md`, "The firmware contract" §1–11 (zip, `node.yaml`, environment, process, time, driver, ether, virtual radio, building);
  - `sim-mesh/radio/portduino/README.md`;
  - `spangap-core/docs/framed-rpc.md`;
  - `plans/other-esp32-nodes.md`, "What the attermann firmware already gives us", for Portduino under sim-mesh.
- **Commits:**
  - Commit only when the user says so, where they say. Never offer or ask to commit.
  - Commits carry a DCO `Signed-off-by:` line only, no AI co-author trailer.
  - Never commit `.pio/`, zips or `pylib/`.
- **Work on our fork only.**
  - Clone upstream fresh beside sim-mesh (step A).
  - Leave the `competition/` clones untouched: they are the upstream reference.
  - At the very end, before the first commit:
    - rename the upstream remote to `upstream`;
    - set `origin` to `https://github.com/sim-mesh/MeshCore.git`.
    - Nothing else is needed on GitHub: the user's `spangap push-all`, run on the host, creates the repo and pushes.
- **Keep upstream files' edits to the commits this plan names:** one for the Portduino platform branches, one for `MainBoard::idle()`. Everything else goes in new files.
- **Code style:**
  - Nothing blocks: no busy-waits, no sleeps in a loop. Waits are `rnode_idle()` or a descriptor becoming readable.
  - Comments state what is, not what changed.
  - Framework diagnostics go through the framework's own log, never stray `printf`, except the boot banner, which is meant for stdout.
- **When you're blocked or a fact here turns out wrong,** say so in prose, fix this plan, and carry on with what you can. Don't open popups (AskUserQuestion or ExitPlanMode); put questions to the user in prose.

### The build machine

This spangap container is the build machine: aarch64, Ubuntu 24.04, glibc 2.39. That is exactly sim-mesh's image for this architecture, so a binary built here runs in sim-mesh unchanged. The workspace is `/home/spangap/reticulous`, and the user's sim-mesh runs from the same tree on the host.

Not available here: `gh`, `docker`/`podman`, `cargo`, and aiohttp in the default `python3` (which is ESP-IDF's virtual environment).

Portduino's core compiles `AsyncUDP.cpp` and `LinuxHardwareI2C.cpp` whatever the board, so the build needs the libuv and libi2c headers (`sudo apt-get install libuv1-dev libi2c-dev`). The program links neither: they sit in the core's archive, unreferenced.

The user's sim-mesh front runs on the host, and this container reaches it at **`http://host.docker.internal:8800`** (`/api/sims` answers). So you can drive multi-station runs yourself (step H).

### A. Clones

```sh
cd /home/spangap/reticulous
git clone https://github.com/meshcore-dev/MeshCore.git meshcore-sim       # becomes sim-mesh/MeshCore at the end
git -C meshcore-sim checkout -b sim-mesh a366955c                          # the commit this plan was checked against
```

- meshcore_py (`1e6fdd4`, v2.3.15) and meshcore-cli (`a43041f`, v1.6.5) are used as released, from PyPI. Neither needs a fork. If PyPI has no meshcore-cli 1.6.5, install it from `competition/meshcore-dev_meshcore-cli`.
- Name the clone as you like, but keep it beside `sim-mesh/` in the workspace: `sim` mounts the workspace, so its image can see it.

### B. PlatformIO

```sh
/usr/bin/python3 -m venv ~/pio-venv
~/pio-venv/bin/pip install -U platformio
export PATH=~/pio-venv/bin:$PATH
pio --version
```

Use `/usr/bin/python3`, not ESP-IDF's `python3`, which is first on `PATH`.

### C. The virtual radio library

`libsimradio-sx1262.so` and `libsimclock.so` (the time shim) are in `sim-mesh/radio/build/` once the user's `sim` has started. If they are missing or older than `sim-mesh/radio/`, build them here:

```sh
cmake -S sim-mesh/radio -B sim-mesh/radio/build && cmake --build sim-mesh/radio/build -j
```

The firmware links the radio by name and never carries it (contract §9).

### D. The PlatformIO environments

Put these in `variants/sim_mesh_sx1262/platformio.ini`. The top-level `platformio.ini` already pulls in every `variants/*/platformio.ini` (`extra_configs`), so it needs no edit. `[arduino_base]` there carries RadioLib (`6d89348`), Crypto, RTClib and CayenneLPP, plus the default radio settings.

```ini
[portduino_base]
platform = https://github.com/meshtastic/platform-native/archive/86c62edfb7084d11669aa255411a70580e83823b.zip
framework = arduino
board = cross_platform                      ; -DPORTDUINO_CROSSPLATFORM: no libgpiod, no spidev
build_flags =
  ${arduino_base.build_flags}
  -D PORTDUINO_PLATFORM
  -D RADIOLIB_EEPROM_UNSUPPORTED
  -D RADIO_CLASS=CustomSX1262
  -D WRAPPER_CLASS=CustomSX1262Wrapper
  -D LORA_TX_POWER=22
  -I variants/sim_mesh_sx1262
  -std=gnu++17
build_src_filter = ${arduino_base.build_src_filter}
  +<helpers/portduino/*.cpp>
  +<../variants/sim_mesh_sx1262>
lib_deps =
  ${arduino_base.lib_deps}
  symlink:///home/spangap/reticulous/sim-mesh/radio/portduino
extra_scripts = pre:/home/spangap/reticulous/sim-mesh/radio/portduino/casefold.py

[env:sim_mesh_sx1262_companion]
extends = portduino_base
build_flags = ${portduino_base.build_flags}
  -D ENABLE_USB_INTERFACE
  -D MAX_CONTACTS=350
  -D MAX_GROUP_CHANNELS=40
  -D ALLOWED_REPEAT_FREQ_RANGE='{150000,2500000}'
build_src_filter = ${portduino_base.build_src_filter} +<../examples/companion_radio/*.cpp>
lib_deps = ${portduino_base.lib_deps}
  densaugeo/base64 @ ~1.4.0

[env:sim_mesh_sx1262_repeater]
extends = portduino_base
build_flags = ${portduino_base.build_flags}
  -D ADVERT_NAME='"Repeater"'
  -D ADMIN_PASSWORD='"password"'
build_src_filter = ${portduino_base.build_src_filter} +<../examples/simple_repeater/*.cpp>

[env:sim_mesh_sx1262_room]
extends = portduino_base
build_flags = ${portduino_base.build_flags}
  -D ADVERT_NAME='"Room"'
  -D ADMIN_PASSWORD='"password"'
  -D ROOM_PASSWORD='"hello"'
build_src_filter = ${portduino_base.build_src_filter} +<../examples/simple_room_server/*.cpp>
```

This is a sketch. Copy the real per-role flags from an existing ESP32 variant's `[env:…_companion_radio_usb]`, `[env:…_repeater]` and `[env:…_room_server]` blocks (e.g. `variants/heltec_v3/platformio.ini`), leaving out everything about displays, BLE, WiFi and GPS. In particular:
- **Symlink path:** the `symlink://` path must be absolute here. On the host, `sim` mounts the workspace at the user's path, so a build run there needs that path instead. Builds run here.
- **RadioLib:** if it is in MeshCore's base `lib_deps` at the pinned commit, it comes along. Don't add Meshtastic's RadioLib.

### E. Build

```sh
cd /home/spangap/reticulous/meshcore-sim
pio run -e sim_mesh_sx1262_repeater          # start here: the smallest, no host process
pio run -e sim_mesh_sx1262_room
pio run -e sim_mesh_sx1262_companion
```

The executables are `.pio/build/<env>/program`. Expect the first build to stop at platform `#if` chains that have no Portduino branch. Each one gets a `PORTDUINO_PLATFORM` branch in the one platform commit (step 1); list them in this plan as you find them. `ldd .pio/build/<env>/program` must show nothing but libc, libm, libstdc++, libgcc_s, the loader and `libsimradio-sx1262.so` (contract §10).

### F. Smoke test here, against a bare ether (real time)

```sh
cd /home/spangap/reticulous
python3 sim-mesh/ether/ether.py --bind 127.0.0.1:7100 --record /tmp/rec.tsv &    # background: the ether
mkdir -p /tmp/n1/state
SIM_MESH_NODE_ID=1 SIM_MESH_NODE_DIR=/tmp/n1 SIM_MESH_BIND_ADDR=127.16.0.1 \
SIM_MESH_ETHER=127.0.0.1:7100 SIM_MESH_RADIO_LIB=$PWD/sim-mesh/radio/build/libsimradio-sx1262.so \
SIM_MESH_BOARD='{"chip":"sx1262","max_dbm":22}' \
LD_LIBRARY_PATH=$PWD/sim-mesh/radio/build \
  sh -c 'cd /tmp/n1 && exec ~/reticulous/meshcore-sim/.pio/build/sim_mesh_sx1262_repeater/program --fsdir state'
```

- **What a bare ether gives you:** with no loss tables it carries nothing between stations ("a band with no table is silence"). It still proves the station joins, the radio initialises through RadioLib, and the console works.
- **Pass:**
  - the banner prints;
  - the radio comes up with no RadioLib error;
  - `ver`, `get name` and `set name n1` typed at the repeater answer;
  - `state/` holds its identity and prefs after a restart;
  - CPU stays near idle while nothing happens (step 3's `idle` hook).
  - two stations started this way print different `getRngSeed()` values (step 2's chip random numbers).
- **Then:** the same with a framed RPC frame piped in (build one per `framed-rpc.md`). Then the companion through its host (step 5): `infos`, `set name`, `contacts`, and `set repeat on` accepted at the default frequency.

### G. The zips

The packaging script (step 7) lives in the fork, e.g. `variants/sim_mesh_sx1262/sim/make-zips`. Per build it lays out a directory and zips it as `<base>_aarch64_<UTC stamp>.zip`:

```
node.yaml       base, arch: aarch64, version: '<stamp>', category: meshcore, radio: sx1262,
                exec: program (companion: host.py), driver: driver.py, title, hardware: ESP32-S3
program         the executable
driver.py       the MeshCore driver for that build (step 6)
host.py, pylib/ companion only
```

Fill `pylib/` for CPython 3.12 aarch64 from wheels:

```sh
pip install --target pylib --only-binary=:all: --python-version 3.12 --platform manylinux2014_aarch64 \
  --no-deps meshcore==2.3.15 meshcore-cli==1.6.5 prompt_toolkit wcwidth pyserial pyserial-asyncio-fast \
  pycryptodome pycayennelpp requests urllib3 certifi idna charset-normalizer
```

`--no-deps` keeps `bleak` and dbus-fast out, so every package is named. The script then imports `meshcore_cli.meshcore_cli` with `PYTHONPATH=pylib` alone (`/usr/bin/python3 -S`), which fails on one that is missing.

Set `env: {PYTHONPATH: ./pylib}` in the companion's `node.yaml`.

### H. Two stations and more: against the user's running front

**`sim` verbs from this container: set `SIM_MESH_FRONT=host.docker.internal:8800`.** sim-mesh's README, "A front elsewhere", describes it. With it set:
- every `sim` verb works from here: the simulation verbs talk to the user's front, and the file verbs work on this same tree;
- no container engine is looked for;
- no front is ever started here.

**Python for those verbs:** they need aiohttp and pyyaml:

```sh
/usr/bin/python3 -m venv ~/sim-venv && ~/sim-venv/bin/pip install aiohttp pyyaml
export PATH=~/sim-venv/bin:$PATH SIM_MESH_FRONT=host.docker.internal:8800
```

**Installing firmware:** through the front, so the page hears of it:

```sh
curl -sS --data-binary @<zip> "http://host.docker.internal:8800/api/firmware/add?name=$(basename <zip>)"
```

**Reading results:** stations run in the host's container. Their directories, logs and run records are under `sim-mesh/testbed/runs/<sim>/` in this same tree, so read them directly.

**Ask the user first:** a station runs on the user's machine, and they may be using the front. So before starting or stopping simulations, check that it's fine to.

**Or a simd of your own, here:** the ladder below needs no front. A simd made in-process the way `testbed/test_simd.py` makes one (`simd.Simd(simd.parse_args([...]))` with `store`'s directories and `firmware.FIRMWARE_DIR` pointed at a scratch tree, its own `--bind`, `--ether`, `--net 127.60.0.0/22` and `--run`, the zips added there with `firmware.add`, synthetic ground `synthetic: {exponent: 3.0}`, and a `globals.py` holding MeshCore's channel) runs real and virtual time in this container and touches nothing of the user's. On that ground at 22 dBm, SF8 and 62.5 kHz, 8 km is in range and 16 km is not. Steps 1–5 below pass that way.

Then, on a geodata with two nodes in range and one pair out of range, run a script, or drive it with `meta`/`command` over the control websocket, through this ladder:
1. **Two companions in range:** `advert` on both, `contacts` shows the other, `msg` gives `msg.status` `delivered` and the other's `msg.received`, and `chan` gives `sent` and the other's `msg.received`.
2. **The same pair out of range with a repeater between them:** delivery through the repeater, and `path` shows one hop.
3. **A room server:** a companion logs in and posts. This goes through meshcore-cli's own commands at the console.
4. **The same three in virtual time** (`simd --time max`): every station reaches `up`, T moves, and no station stalls. This is the test of step 3's idle hook and of step 5's two processes on one TCP connection.
5. **A flood through several repeaters in range of each other:** their retransmissions start at different instants (the run's record), which is the test of the chip's random numbers.

The sim-mesh changes (the `meshcore` category in `testbed/sim_mesh/meshcore/`, and the chip model's random-number register) are made in `sim-mesh/` in this workspace, and need sim-mesh's own tests (README "Tests") to pass.

## Considered and rejected: ESP-IDF's Linux target

Running MeshCore the way reticulous runs, on ESP-IDF's Linux host target, was considered and rejected.

- **What it would add:** ESP-IDF's start-up and log format, and the Arduino loop as a FreeRTOS task, as on a real ESP32. There is no ROM or bootloader output on that target either.
- **Why not:**
  - Arduino-ESP32 builds only for real chips, so it would need an Arduino layer on that target that we alone maintain.
  - `ESP32Board`'s dependencies (`rom/rtc.h`, `soc/rtc.h`, `driver/rtc_io.h`, `SPIFFS`, `WiFi`) don't exist there, so we'd write our own board class anyway.
  - PlatformIO's ESP-IDF framework has no Linux target, so the build would leave MeshCore's build system and could not go upstream.
  - MeshCore is a single loop that barely uses tasks, so the task model adds little fidelity.

## Later

- **LR2021.** MeshCore already has `CustomLR2021`. Once sim-mesh has its LR2021 radio, the same variant gets a second base, `-lr2021`.

## Afterwards: standard_reticulum, as firmware zips

Bring back the `standard_reticulum` station: stock Python Reticulum (RNS's
`RNodeInterface`) plus an LXMF router, as `rnsd` runs on a computer with an
RNode on its USB port. It was in sim-mesh as `stations/standard_reticulum/` and
`testbed/kinds/standard_reticulum.py` (`c0b9d90`), and went when firmware moved
to zips (`5e75f08`).

- **Two RNodes behind it, as two bases:**
  - reticulous, through its RNode endpoint (`iface-lora`, serial and TCP);
  - attermann's microReticulum_Firmware Linux build, its own stack never started.
- **The same two-process station as the MeshCore companion:**
  - the host as the second process (`node_id + 1_000_000`);
  - the `ctypes` join.
- **The RNode on its serial line, an inner pty (sim-mesh change).** RNS's `RNodeInterface` opens a serial port, so the two processes are joined by a pty the host makes, not by TCP.
  - The time shim counts console reads (descriptor 0) and TCP bytes, but not a pty between two processes of one station. Without that, the RNode could be granted time before it has read a command.
  - The shim counts the inner pty the way it counts a TCP connection, as channel `pty/B>A` and its reverse.
- **`reticulum` category**, so its stations take part in the same delivery analysis as reticulous's.
