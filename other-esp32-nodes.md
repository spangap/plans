# Other ESP32 Reticulum nodes as sim-mesh stations

## Goal

Run two other firmwares' transport nodes on the sim-mesh ether beside Reticulous stations, standing in for Heltec V4s. The aim is to measure how their path-request forwarding behaves on a shared LoRa channel.

1. **attermann/microReticulum_Firmware** (Chad Attermann, the microReticulum author), built from its own native Linux target.
2. **A jrl290 stand-in**: the same attermann build with RTNode-HeltecV4's path-request rules carried onto the stack. It is not jrl290's firmware.

Background on the firmwares and their forwarding behaviour is in `plans/competition.md`.

## Principle: keep changes to attermann's code small and upstreamable

Every change to attermann's tree is a generic hook or setting that is useful on real hardware and could be sent to him as a pull request. The sim-mesh-specific code lives outside his tree. So:

- No sim-mesh names and no `#ifdef SIM_MESH` in his files.
- Hooks are weak functions whose default is today's behaviour, so an unmodified build behaves exactly as before.
- Timing values are build flags, not code edits.
- One commit per hook in the clone, so each can go upstream on its own.

If he takes all of them, the only thing left in his repo is the `[env:sim-mesh]` block in `platformio.ini`.

## Where things are

The clones are under `competition/` in the workspace, each its own git repo; nothing else builds from them.

| Clone | What it is |
|---|---|
| `competition/attermann_microReticulum_Firmware` | the firmware; RNode_Firmware fork with the stack embedded |
| `competition/attermann_microReticulum` | the stack, microReticulum |
| `competition/jrl290_RTNode-HeltecV4` | RTNode-HeltecV4 with its own old vendored stack under `lib/microReticulum/src/` |
| `competition/portduino` | Meshtastic's framework-portduino, checked out at the commit the firmware's platform pins (`32b7b2c`) |
| `competition/platform-native` | Meshtastic's PlatformIO platform, at `cab4b21`, which `[env:native]` pins |

Portduino is Meshtastic's Arduino-API layer for Linux. Arduino firmware built on it becomes an ordinary process, with `SPI`, GPIO (general-purpose input/output) and `millis()` backed by spidev and libgpiod, or by a simulated mode.

## What the attermann firmware already gives us

`[env:native]` in `platformio.ini` builds `RNode_Firmware.ino` as a Linux process on Portduino. The build uses `board = linux_hardware`, `-DMCU_VARIANT=MCU_NATIVE`, `-DMODEM=MODEM_RUNTIME`, and `lib_deps` taken from attermann's microStore and microReticulum on GitHub. `native/main.cpp` reads `rnoded.conf` from `--config`, from `$MR_CONFIG`, or from the working directory. It then `chdir`s into `data_dir` (overridable with `$MR_DATA_DIR`) and forces `op_mode` to TNC (terminal node controller) mode (`native/main.cpp:231`), which keeps transport on. The `.ino` then sets the LoRa interface to `MODE_GATEWAY` (`RNode_Firmware.ino:1021`) and sets `transport_enabled(true)`.

The facts that decide the port, all checked in the source:

- **SPI (Serial Peripheral Interface) framing is already whole-frame on native.** Under `#if MCU_VARIANT == MCU_NATIVE`, every `sx126x.cpp` bus operation assembles its whole frame and makes one in-place `SPI.transfer(buf, len)` between `digitalWrite(_ss, LOW)` and `digitalWrite(_ss, HIGH)`. That covers `singleTransfer`, `executeOpcode`, `executeOpcodeRead`, `writeBuffer` and `readBuffer`. The reason is that spidev drops chip-select between ioctls. That is exactly sim-mesh's one-frame-per-NSS-cycle (chip select) rule, so **`sx126x.cpp`'s bus code needs no change**.
- **BUSY never blocks.** `waitOnBusy()` spins on `digitalRead(_busy)` against `millis()`, but the sim-mesh chip model never drives BUSY high (`sim-mesh/radio/src/model.cpp:509`). The spin exits on its first read, even in virtual time.
- **Portduino's SPI can be replaced without patching Portduino.** `HardwareSPI` holds a `std::shared_ptr<SPIChip> spiChip`, which is protected (`portduino/ArduinoCore-API/api/HardwareSPI.h:132`). `HardwareSPI::begin()` creates a chip only when `spiChip` is null (`portduino/cores/portduino/linux/LinuxHardwareSPI.cpp`). `SPIChip` is a small virtual interface: `transfer(out, in, len, deassertCS)`, `beginTransaction` and `endTransaction` (`portduino/cores/portduino/SPIChip.h`). So we can set the global `SPI`'s chip to our own `SPIChip` before the driver calls `SPI.begin()`. A derived-class accessor can reach the protected member.
- **Portduino's GPIO can be replaced too.** `gpioBind(GPIOPinIf*)` swaps the implementation of one pin number (`portduino/cores/portduino/PortduinoGPIO.cpp`). Subclassing `GPIOPin` and overriding `readPinHardware()` and `writePin()` gives us NSS, reset, BUSY and DIO1. Interrupt service routines (ISRs) run through polling: Portduino's main loop calls `gpioIdle()` before each `loop()`, which calls `refreshIfNeeded()` on every pin with an ISR. That reads `readPinHardware()` and fires the ISR on the edge, on the main thread.
- **The main loop's sleep switches off.** Portduino's `main()` runs `gpioIdle(); loop();` and then `delay(loopDelay)` (100 ms) **only when `realHardware` is false** (`portduino/cores/portduino/main.cpp:170-178`). `gpioBind` sets `realHardware = true`. With our pins bound, the loop spins with no sleep at all, never blocks, and a virtual-time run never sees the station idle.
- **With `board = cross_platform`, the libgpiod code drops out.** `native_pinmap::bind_linux_gpios()` and `release_linux_gpios()` compile to nothing without `PORTDUINO_LINUX_HARDWARE` (`native/PinMap.cpp:70`, `:154`).
- **RNode's radio code is the node's MAC (medium access control) layer.** `LoRaInterface::send_outgoing` (`LoRaInterface.h:63`) only appends to RNode's `packet_queue`. From there `tx_queue_handler` (`RNode_Firmware.ino:2613`) runs CSMA (carrier-sense multiple access), and `flush_queue`, `transmit` and `sx126x::endPacket` follow. Received frames reach the stack through `modem_packet_queue`. CSMA, channel sensing and airtime accounting stay as they are. Only the KISS host side (TCP accept and serial buffering) can go.
- **Transmit busy-waits.** The DIO1 mask carries RX-done only (`sx126x.cpp:755`). `endPacket()` (`sx126x.cpp:575`) spins on `GetIrqStatus` with `yield()` until TX-done appears; on Portduino, `yield()` is `sched_yield()`. The chip model sets TX-done at the end of the frame's airtime on T. In virtual time the spin never blocks, so T never gets there and the station hangs on its first transmit.
- **Carrier detection is latched.** `sx126x::dcd()` (`sx126x.cpp:610`) reads the preamble-detected and header-detected IRQ status bits. Those bits stay set until cleared, so a check made late still sees a carrier that started in between.
- **The deadlines in the loop:**
  - Transport jobs every 250 ms (`Transport::_job_interval`, `Transport.cpp:148`), with links, receipts and announces checked every 1 s and tables culled every 60 s.
  - Reticulum housekeeping every 60 s (`JOB_INTERVAL`).
  - The CSMA wait, only while something is queued: DIFS (distributed inter-frame space, 48 ms: `CSMA_SIFS_MS` 0 plus 2 × `CSMA_SLOT_MIN_MS` 24), then the contention window, `random(cw_min, cw_max)` slots of 24 ms.
  - The modem status poll every `STATUS_INTERVAL_MS` (3 ms), in `check_modem_status` (`RNode_Firmware.ino:2421`). It samples `dcd()` and RSSI (received signal strength) into the channel-utilisation window (`DCD_SAMPLES` 2500, which is 7.5 s), the noise floor, interference detection and the false-preamble reset.
  - The KISS TCP accept and idle sweep, polled.

  Receive is not a deadline: DIO1 raises RX-done.
- **Time goes through the C library.** Portduino's `millis()` and `micros()` are `gettimeofday` (`portduino/cores/portduino/linux/millis.cpp`), and `delay` is a C library sleep (`linux/LinuxCommon.cpp`). The sim-mesh time shim answers both.
- **Reboot re-execs the process.** `native/reboot.cpp` re-execs itself by absolute path. sim-mesh wants a station to exit and be restarted by its supervisor. A re-exec would reopen the ether link under the same `sid` inside one process.
- **KISS over TCP always listens.** `native/main.cpp:203` always calls `native_kiss_tcp::init(kiss_tcp_port, kiss_tcp_public)`, which binds `INADDR_LOOPBACK` or `INADDR_ANY` (`native/TCPHostInterface.cpp:133-157`). A failed bind is not fatal. The WebSocket console (`-DENABLE_WEBSOCKETS`) listens on port 8080. Every socket a sim-mesh station opens must bind `SIM_MESH_BIND_ADDR`.
- **The LoRa interface mode is already a provisioning field.** `PROV_GENERAL_LORA_MODE` (`Provisioning.cpp:190-215`) offers gateway, full, point-to-point, access point, roaming and boundary, and applies live.
- **The sync word is RNode's, 0x12**, not Reticulous's 0x42.
- **The sim-mesh model applies the board's front end.** The model applies the Heltec V4's GC1109 front-end curve and receive gain from `SIM_MESH_BOARD` (`sim-mesh/radio/src/model.cpp:168-220`). The firmware sets chip power, and the ether sees power at the antenna.

## Work: hooks in attermann's firmware (upstreamable)

In `competition/attermann_microReticulum_Firmware`, one commit each. None of these changes behaviour in a build that does not use it.

1. **Overridable timing constants.** Wrap `STATUS_INTERVAL_MS` and `DCD_SAMPLES` in `Config.h` (lines 178-180) in `#ifndef`; `UTIL_UPDATE_INTERVAL` and the airtime bin length already derive from them.
2. **An idle hook.** Add a weak `void rnode_idle(uint32_t max_ms)` whose default is `yield()`.
   - **At the end of `loop()`**, on `MCU_NATIVE` only, call `rnode_idle(next_deadline_ms())`. `next_deadline_ms()` returns the time until the earliest of:
     - the next modem status poll (`last_status_update + status_interval_ms`);
     - while `queue_height > 0`: the end of DIFS (`difs_wait_start + difs_ms`) during DIFS, the end of the contention window after it, and the next status poll while DIFS has not started;
     - an upper bound, `RNODE_IDLE_MAX_MS` (a build flag, default 0, which means "don't wait").
   - **In `sx126x::endPacket()`**, before the TX-done loop, call `rnode_idle(expected airtime)`. Compute the airtime from `lora_symbol_time_ms`, the preamble and the payload length, the way the firmware already computes airtime for `add_airtime`. Replace the loop's `yield()` with `rnode_idle(1)`.

   With the default hook, both call sites are one `yield()` more or less than today. On ESP32 the same hook could become light sleep, which is the reason to offer it upstream: today the firmware polls the chip 333 times a second and spins through every transmission.
3. **A radio backend hook.** Add a weak `void native_radio_backend_init()` (default: nothing) and call it in `portduinoSetup()` (`native/main.cpp`) right after `native_config::load`, before `native_pinmap::apply()` and before anything reads time. A backend library supplies the strong symbol.
4. **KISS TCP off and bind address.** In `native/config.cpp` and `native/main.cpp`:
   - `kiss_tcp_port = 0` skips `native_kiss_tcp::init`;
   - an optional `kiss_tcp_bind = <address>` key sets the bind address, taking precedence over `kiss_tcp_public`.

   `[env:sim-mesh]` leaves out `-DENABLE_WEBSOCKETS`, so the WebSocket console needs no key.
5. **Reboot by exit.** A `reboot_mode = exit` key in `rnoded.conf`. When it is set, `native_reboot` exits with status 0 after its cleanup instead of re-execing. This is for supervisor-managed deployments; systemd `Restart=always` needs the same.
6. **The LoRa interface mode as a config key.** A `lora_interface_mode` key in `rnoded.conf` (gateway, full, point_to_point, access_point, roaming, boundary; default gateway), parsed in `native/config.cpp` and used at `RNode_Firmware.ino:1021` in place of the constant `MODE_GATEWAY`. It is only the default that provisioning starts from: a persisted `PROV_GENERAL_LORA_MODE` value still wins, as now.

## Work: the backend library (outside attermann's tree)

A PlatformIO library at `sim-mesh/radio/portduino/` (`library.json`, one `.cpp`), next to `radio/backend/esp-idf/`. It serves any Portduino firmware; Meshtastic's `meshtasticd` is Portduino too. It links `sim-mesh/radio/build/libsimradio.a` with its include directory `sim-mesh/radio/include`; `sim-mesh build radio` builds the library. It supplies:

- **`native_radio_backend_init()`:**
  - Read `SIM_MESH_NODE_ID`, `SIM_MESH_BIND_ADDR` and `SIM_MESH_ETHER`, then call `simradio_station_open`.
  - Open slot 0 with `simradio_open`, with an `on_pin` callback that stores the DIO1 level and signals a condition variable on DIO1's rising edge.
  - Install a `SPIChip` into `SPI`, through a derived-class accessor, before the driver's `SPI.begin()`. Its `transfer(out, in, len, …)` calls `simradio_transfer`; it copies through a scratch buffer when `in == out`, since the driver transfers in place.
  - `gpioBind` pins at `pin_cs`, `pin_reset`, `pin_busy` and `pin_dio`, taken from the `native_config` it has just loaded:
    - reset calls `simradio_reset` on the rising edge;
    - BUSY and DIO1 return the stored level from `readPinHardware()`;
    - NSS does nothing, since each frame is already one `transfer`.
- **`rnode_idle(max_ms)`:** wait on the condition variable with `pthread_cond_timedwait` until `max_ms` has passed or DIO1 has risen. The shim answers that wait in node time. A DIO1 edge wakes the wait at once, and the next `gpioIdle()` fires the ISR. `max_ms == 0` returns at once.

The library carries no firmware-specific code beyond the two hook names. A small `sim-mesh/radio/portduino/README.md` says what it is and which hooks a firmware must call.

## Work: `[env:sim-mesh]` (the one block that stays local)

In `competition/attermann_microReticulum_Firmware/platformio.ini`. Base it on `[env:native]` with these changes:

- `board = cross_platform`, so the libgpiod path and `-lgpiod` drop out.
- Keep `-DLORA_TRANSPORT`, `-DMCU_VARIANT=MCU_NATIVE`, `-DMODEM=MODEM_RUNTIME`, `-DDISABLE_FIRMWARE_CHECKSUM`, `-DUSTORE_USE_POSIXFS=1` and the path-table sizes.
- Leave out `-DENABLE_WEBSOCKETS`.
- Timing flags: `-DSTATUS_INTERVAL_MS=50`, `-DDCD_SAMPLES=150` (the utilisation window stays 7.5 s) and `-DRNODE_IDLE_MAX_MS=50`.
- `lib_extra_dirs` or a `symlink://` `lib_deps` entry pointing at `sim-mesh/radio/portduino`.

**What 50 ms gives.** An idle station wakes 20 times per second of T. A transmission adds one wake each for the end of DIFS, the end of the contention window and the end of the airtime. Receive costs no latency, because DIO1 wakes the wait.

**What it costs in fidelity:**
- Because the carrier bits latch, each utilisation sample is a point sample of "a reception is in progress now". Utilisation stays unbiased, with 150 samples in place of 2500.
- That estimate feeds only the CSMA contention-window band (`update_csma_parameters`), and only above `CSMA_BAND_1_MAX_AIRTIME`. Their own transmit airtime (`airtime`, from `add_airtime`) is counted exactly regardless.
- `medium_free()` calls `update_modem_status()` itself, so every CSMA decision reads the chip at that moment.
- Noise-floor and interference tracking update at 20 Hz in place of 333 Hz.
- Transport jobs, due every 250 ms, run up to 50 ms late.

**Going to 250 ms** would need the stack's next job time (`Transport::_jobs_last_run + _job_interval`) in `next_deadline_ms()`. That means a small accessor in microReticulum, which would be a second upstream change, to the stack.

`rnoded.conf` for a station after setup. The kind's `env()` writes the fixed part, and setup's lines add the `lora_*` figures:

```
data_dir       = ./state
modem          = SX1262
pin_cs         = 1
pin_reset      = 2
pin_busy       = 3
pin_dio        = 4
lora_freq_hz   = 869525000
lora_bw_hz     = 125000
lora_sf        = 8
lora_cr        = 5
lora_txp       = 14
kiss_tcp_port  = 0
reboot_mode    = exit
```

`lora_txp` is chip power. For a Heltec V4, the model maps it through the GC1109 curve to antenna power. The firmware logs to stdout and stderr, which is the station console, and sim-mesh appends it to the station's `log`. The console carries no framed RPC (remote procedure call), and stdin is unused.

## Work: the jrl290 stand-in

RTNode-HeltecV4 has no native target. Its `.ino` is ESP32-only, and its vendored stack predates attermann's `src/microReticulum/` layout and microStore. A real port would redo attermann's `native/` directory against a diverged tree. Instead, carry its rules onto attermann's stack as a second device. This branch is ours alone and is never offered upstream.

1. Branch `competition/attermann_microReticulum` as `jrl290-rules` and change three things (jrl290's code is in `competition/jrl290_RTNode-HeltecV4/lib/microReticulum/src/`):
   - `Interface.cpp`: add `MODE_FULL` and `MODE_BOUNDARY` to `DISCOVER_PATHS_FOR`, alongside access point, gateway and roaming (jrl290 `Interface.cpp:12`).
   - `Transport::path_request`: set `should_search_for_unknown` whenever `attached_interface` is set, whatever the interface's mode (jrl290 `Transport.cpp:3543-3546`).
   - The discovery forwarding loop: skip an interface only when it is the attached interface **and** `is_backbone()` (jrl290 `Transport.cpp:3693-3697`). If attermann's `Interface` has no `is_backbone()`, a LoRa-only station needs none: forward on every interface.
2. `[env:sim-mesh-jrl290]` extends `[env:sim-mesh]` and points `lib_deps` at the local branch (`microReticulum=symlink://../attermann_microReticulum`, checked out on `jrl290-rules`).
3. **LoRa in full mode**, as jrl290's `.ino:1027` sets it, through hook 6: the kind writes `lora_interface_mode = full` for these stations. The device file for the stand-in says so, so any node running it gets full mode without a nodeset having to declare it.

The stand-in does not include jrl290's other changes: ratcheted announce validation, HDLC and KISS dual framing, Ed25519 hardening, the TCP echo fix and the firewall. For LoRa-only path-request behaviour, those do not matter.

## Work: sim-mesh

1. **A new file, `sim-mesh/testbed/kinds/microreticulum.py`**: a `Kind` with `type_name = "microreticulum"`, modelled on `kinds/sergeyculum.py`. The station has no console to type at, so the kind's lines are edits to `rnoded.conf`, and a restart applies them. sim-mesh runs setup after `wait_up`, as lines through `run()`, and each batch ends with `flush()` (`kinds/__init__.py:156-175`). That fits a config-file firmware as it is.
   - **`env`**: set `SIM_MESH_IDLE=threads` in virtual time, `MR_CONFIG=<station dir>/rnoded.conf` and `MR_DATA_DIR=<station dir>/state`. When `rnoded.conf` is missing, write its fixed part: `data_dir`, `modem`, the four pins, `kiss_tcp_port = 0`, `reboot_mode = exit`. When the device's `env:` carries `MR_LORA_INTERFACE_MODE` (the jrl290 stand-in's does), add `lora_interface_mode` with that value.
   - **Its lines**:
     - `set <key> <value>` sets one `rnoded.conf` key;
     - `unset <key>` removes one.

     `run()` rewrites the file in place, keeps the other keys and comments, and marks the station dirty.
   - **`lines`**:
     - `radio`: `set lora_freq_hz`, `lora_bw_hz`, `lora_sf`, `lora_cr` and `lora_txp` for each figure given. Sync word and preamble are the firmware's own.
     - `tx_power`: `set lora_txp <dBm>`.
     - `role`: `transport` is accepted and says nothing, since the firmware is always a transport; `client` is refused.
     - `name`: accepted and says nothing.
     - `announce`, `message`, `path` and `peer_tcp`: refused, because the station is a pure transport node with no LXMF destination.
   - **`flush`**: when the station is dirty, `station.restart()` (`stations.py:521`), then `wait_up` again, then clear the flag. Setup's batches (declared, a script's rules, radio start) cost one restart each at most, and none when a batch changed nothing.
   - **`wait_up`**: the station's log shows `RNS Transport is READY!`, which the firmware prints just after `reticulum.start()` once transport is enabled. It is logged at trace level; the stack's runtime level is trace (`Log.cpp:26`), and the `.ino` sets trace again before start. It also proves the role. `RNS is inoperable` means the hardware did not come up, so `wait_up` fails at once on it instead of timing out. Match only lines printed since the station's latest start.
   - **`role`**: `"transport"` once `wait_up` has seen the marker.
   - **`address`**: none.
   - **`web_port`**: none.
   - **`configured`**: `rnoded.conf` exists and `state/` is not empty.

   The first boot of a new station runs on the firmware's default radio figures, before setup's lines arrive. It is thrown away by the restart after the first batch. It may put an announce on air on the default channel, which no other node shares.
2. **The backend library, `sim-mesh/radio/portduino/`**, above. It is additive; nothing in sim-mesh builds it.
3. **`sim-mesh/testbed/kinds/__init__.py:236-237`**: add the class to the registry tuple. Do this **last**, after the class passes its tests, because the registry imports every kind whenever any kind is resolved, so a broken module takes every simulation down.
4. **`sim-mesh/testbed/sim_mesh/reticulum/__init__.py:13`**: `KIND_TYPES = ("reticulous", "microreticulum")`, so that `seq.py`, `delivery.py`, `airtime.py` and the other analysis tools decode its frames as Reticulum.
5. **`sim-mesh/devices/local/microreticulum_local.yaml` and `microreticulum-jrl290_local.yaml`**: compiled builds run in place:
   - `kind: microreticulum`
   - `elf:` the respective `.pio/build/<env>/program`
   - `stands_for: ESP32-S3`
   - `name:` "microReticulum (attermann)" and "microReticulum (jrl290 rules)"
   - the stand-in's also has `env: {MR_LORA_INTERFACE_MODE: full}`
6. **Nodesets**: stand these nodes on `heltec_v4` (`testbed/boards/catalogue.yaml:27`). Reticulous nodes in the same nodeset must declare `sync: 0x12`. Declare equal preambles too, since the medium does not model preamble length.

Nothing in the existing parts of `radio/`, the ether, simd, the front or the existing kinds changes. Reticulous is not touched at all.

## Order, so nothing shared breaks

1. The hooks in the firmware clone, one commit each. The backend library in `sim-mesh/radio/portduino/`. Then a host build of `[env:sim-mesh]` (`pio run -e sim-mesh`).
2. Run the binary by hand against a lone ether (`python3 sim-mesh/ether/ether.py …` per `sim-mesh/README.md`, "The pieces on their own") with the `SIM_MESH_*` variables set. Confirm that it opens the link, initialises the SX1262 and transmits an announce, which shows as a frame in the ether's record.
3. Write the kind and its `devices/local` file. Run it with `simd --build` on a nodeset of two of these nodes and one Reticulous node. Run `cd sim-mesh/testbed && python3 -m pytest -q`.
4. Add the kind to the registry tuple and `KIND_TYPES`, then run the test suite again.
5. Build the jrl290 stand-in and run the same nodeset with it.
6. Once both work, add the new kind's rows to the station-kinds and intents tables in `sim-mesh/README.md`, its environment to `sim-mesh/STATION.md`, and the backend library to the README's "Where the code lives".

## Verifying the behaviour under study

Use a chain or a star of 5 to 10 of these transport nodes, with a Reticulous client at each end. The client runs `rnpath -j <dest>` for a destination whose path is unknown. Then:

- `seq.py` and `airtime.py` count the path-request copies and `PATH_RESPONSE` copies on air per request, for attermann and the stand-in against Reticulous-only transport.
- Expect up to one request rebroadcast per transport node in earshot, a matching burst of path responses, and collisions from the unjittered `packet.send()`.
- The Python behaviour, which never re-sends on the arriving interface, is the baseline.

## Open points

- Station logs: trace level logs every packet, so a long run of many stations writes large logs. Watch the size before raising the level. Raising it would need a runtime log-level key, a seventh hook.
- Whether anything else in their `loop()` keeps a `millis()` deadline shorter than 50 ms that `next_deadline_ms()` does not know about, and so would fire late: the provisioning subsystem, microStore's `filesystem.loop()`, `RNG.loop()`, and `LoRa->handleDio0IfPending()`. Read each before relying on the wait. A late timer shows up as behaviour that differs from a run built with `-DSTATUS_INTERVAL_MS=3 -DDCD_SAMPLES=2500 -DRNODE_IDLE_MAX_MS=3`, which is the check to make.
