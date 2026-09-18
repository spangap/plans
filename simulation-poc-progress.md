# Simulation proof of concept — progress

Checkpoints:

1. Plumbing and a bare core — **PASS**
2. Network and the browser — **PASS**
3. Two stations over TCP — **PASS**
4. Three stations over LoRa — **PASS**
5. A message by hand — **PASS**
6. The chip build — **PASS**

## Three things a run needs

- `-x spangap/acme -x spangap/duckdns -x spangap/sshd -x spangap/wg` on top of
  §17.2's list, and `spangap/upnp` (see the deviation note under checkpoint 2).
  spangap-net additional-installs all five, and none of them is built for the
  host: each manifest pins `targets: [esp32s3]`, which is a hard failure on
  linux, and behind that the code rests on `esp_http_client` and lwIP —
  a WireGuard netif inside lwIP for `wg`, HTTP clients for `acme`, `duckdns`
  and `upnp`. Porting them means what spangap-net and spangap-web got: the
  manifest target, a linux source list, the socket-header switch and host
  stubs for the boot hooks the generated dispatch calls. None of it is needed
  by the staged set this step runs on.
- A fresh station holds Reticulum until an admin password is set. Over its TCP
  CLI: `auth passwd admin <pw>`.
- A radio with no region configured will not start. Over the same CLI:
  `lora up`, then `lora 0 freq 869.525`, `lora 0 sf 8`, `lora 0 bw 125`. The
  same three answers a board on a cable is given.

## How to build and run while the container image is stale

`spangap` in the container is the image's baked copy of `spangap-inside` plus a
baked copy of the manifest schema, so the build-system changes below are not
visible to it until the image is rebuilt host-side. Until then, drive the
workspace copy:

```sh
export SPANGAP_SCHEMA_PATH=/home/spangap/reticulous/spangap/build-system/schemas/straddle.schema.json
/usr/bin/python3 /home/spangap/reticulous/spangap/build-system/spangap-inside build …
```

One build directory serves both targets, and a target switch drops `sdkconfig`
and rebuilds from scratch — so the tree not in use is parked beside it. It is
the host tree that is parked now, at `reticulous/esp-idf/build.linux/`;
`reticulous/esp-idf/build/` holds the chip build checkpoint 6 finished with.
To go back to the host build, swap the two directories before building.

## 1. Plumbing and a bare core — PASS

Build (all one line, from the workspace root):

```
spangap build reticulous/reticulous --with spangap/hw-linux --no-net --no-web \
  -x reticulous/rnsh -x reticulous/iface-auto -x reticulous/iface-ble \
  -x reticulous/rnode-ble -x reticulous/nomad -x reticulous/maps -x spangap/viewer \
  -x reticulous/rns -x reticulous/iface-lora -x reticulous/iface-tcp -x reticulous/lxmf
```

Built for board **spangap/hw-linux** (IDF target `linux`).

Run:

```
cd reticulous/sim/nodes/1
SPANGAP_NODE_ID=1 SPANGAP_NODE_DIR=$PWD SPANGAP_BIND_ADDR=127.0.0.1 \
SPANGAP_FIXED_DIR=/home/spangap/reticulous/reticulous/esp-idf/build/data_merged \
  ../../../esp-idf/build/reticulous.elf
```

Observed:

- Configure: chip-only Kconfig symbols (`SPIRAM*`, `PM_*`, `ESPTOOLPY_*`,
  `ESP_WIFI_*`, `BOOTLOADER_WDT_*`, `TINYUSB_CDC_COUNT`, `MDNS_*`) come out as
  `warning: unknown kconfig symbol …`, never errors — §17.1's claim holds.
- `build/reticulous.elf` exists (6.3 MB).
- Boot log reaches `board confirmed: hw-linux`, `dev 6d0001`, `spangap ready`.
- `state/` fills with `boot`, `net_up`, `storage/root.json.gz`.
- The console on stdin enters CLI mode on a keystroke and answers `help` with
  the full command list.

Changed:

- `spangap/build-system/schemas/straddle.schema.json` — new `target` key.
- `spangap/build-system/spangap-inside` — `Manifest.target`; `cmd_build` reads
  the staged `hw-` straddle's target, exports `IDF_TARGET` for every build,
  passes `--preview` on linux, skips the flash-size check and the
  shrink-wrap/flasher-zip block on linux, and drops a `sdkconfig` generated for
  a different target so idf.py's pre-CMake mismatch check cannot block a switch.
- `spangap-core/esp-idf/sdkconfig.defaults.spangap` — `CONFIG_IDF_TARGET`
  deleted.
- `targets: - linux` added to the `idf_component.yml` of spangap-core, spanfs,
  spangap-net, spangap-web, rns, microreticulum, iface-lora, iface-tcp, lxmf.
  `espressif/mdns`, `joltwallet/littlefs`, `espressif/esp_tinyusb` gated with
  `rules: - if: "target != linux"`.
- `reticulous/esp-idf/main/CMakeLists.txt` — `driver esp_netif esp_wifi lwip`
  only off linux.
- `spangap-core/esp-idf/project_include.cmake` — `add_dependencies(flash …)`
  guarded on the target existing; the factory image stops after
  `spangap_data_merge` on linux.
- New straddle `hw-linux/` — manifest, `src/detect.cpp`, `src/hwlinux.{h,cpp}`,
  `components/driver/` (GPIO shim + the two SPI type names),
  `components/esp_timer/` (host implementation over CLOCK_MONOTONIC and one
  task).
- `spangap-core/esp-idf/CMakeLists.txt` — host source list (drops `pm.cpp`,
  `spi_helper.cpp`, `usb_ports.cpp`, `heap_track_stub.c`, adds `src/host/*`),
  host `_REQ`, the `--wrap` block off on linux, `-l:libz.so.1` on linux.
- `spangap-core/esp-idf/components/spanfs/CMakeLists.txt` — registers empty on
  linux.
- `spangap-core`: `include/fs.h` (relative roots on linux), `include/compat.h`
  (20 KB stack floor, no core affinity), `include/cli.h` (`consoleEmitRaw`),
  `src/fs.cpp`, `src/spangap_init.cpp`, `src/random.cpp`, `src/cron.cpp`,
  `src/cli.cpp`, `src/log.cpp`, `src/cli_cmd_sys.cpp` (`esp_system.h` for
  `esp_restart`), new `src/host/{pm_host.cpp,usb_ports_host.cpp,miniz_host.c}`.

Deviations from §17:

- §17.5 names only `src/host/pm_host.cpp`. Two more host files were needed:
  `usb_ports_host.cpp` (the console-transport flags and the CDC calls cli.cpp
  and log.cpp make from the branch that is never taken — `usb_ports.cpp` itself
  is out of the linux list and its stub branch still includes `soc/` headers),
  and `miniz_host.c`.
- **miniz.** §17.1 did not list it: `tdefl_init`, `tdefl_compress` and
  `tinfl_decompress` come from the chip's ROM and have no linux implementation,
  so the gzip in `storage.cpp`, `storage_db.cpp` and `targz.cpp` does not link.
  `src/host/miniz_host.c` implements those three over the system zlib
  (`libz.so.1`, linked by soname because the container has the runtime library
  and not its development symlink; zlib is declared in the file because the
  headers are absent). Each zlib stream lives inside the miniz state structure
  the caller already allocates, so lifetimes are unchanged.
- The GPIO shim reads and writes its pin table under `portENTER_CRITICAL` but
  calls the handler with nothing held: a handler ends in `portYIELD_FROM_ISR`,
  and the linux port's critical section is a per-thread signal mask with a
  global nesting count, which a yield from inside would hand to the wrong
  thread.
- `spawnTask` also forces `tskNO_AFFINITY` on the host; the port asserts on a
  request for core 1.
- `cli_cmd_sys.cpp` gained `#include "esp_system.h"` (for `esp_restart`) — it
  arrived transitively on the chip and does not on the host. Affects both
  targets, and is a correctness fix either way.

## 2. Network and the browser — PASS

Build: checkpoint 1's command without `--no-net --no-web`, plus
`-x spangap/acme -x spangap/duckdns -x spangap/sshd -x spangap/upnp -x spangap/wg`.
Built for board **spangap/hw-linux**.

Observed, one station at 127.0.0.11:

```
I [net] opening port 8081 (cli_port)
I [net] opening port 80 (http_port)
I [net] opening port 443 (https_port)
I [web] ready (3 maps, 11 mime types)
```

- `nc 127.0.0.11 8081` gets the CLI; `net` answers with the station's address,
  its public ports and its traffic counters.
- `curl http://127.0.0.11/` returns the SPA: 200, 390 bytes, the deployed
  `index.html` inflated out of `index.html.gz`.

Changed:

- `spangap-net`: new `src/net_relay.cpp` (the event bus, the endpoint table and
  listen sockets, the accept-and-proxy loop, the dial path, the traffic
  counters, `netForceClose`, `netInitCommon`, `netRelayTaskInit`) and
  `src/net_priv.h` (what the relay and a link backend share). `src/net.cpp`
  keeps WiFi and drives the relay. New `src/host/net_host.cpp` (the host link
  backend) and `src/host/services_host.cpp` (NTP and mDNS on a host: the clock
  is already right, so it publishes `sys.time.valid` and `sys.boot_time` and
  applies the timezone). `include/net.h` switches its socket headers and
  supplies `ip_addr_t` over `struct in_addr` on linux; `src/tls.cpp` the same.
  `CMakeLists.txt`: the host source list, the host REQUIRES, `PRIV_INCLUDE_DIRS
  "src"`.
- `spangap-web`: `webrtc_task.cpp`/`webrtc_sctp.cpp` out of the host list,
  `src/host/webrtc_stub.cpp` in; `web.cpp`'s lwIP and `esp_memory_utils`
  includes guarded; the three base URL mappings spell `FS_FIXED`/`FS_STATE`/
  `FS_SDCARD` instead of the literals; a new `openRegular()` refuses to serve a
  directory as a file.
- `spangap-core`: `cli_cmd_fs.cpp` and `lxmf.cpp` likewise spell the constants.

Deviations from §17:

- §17.6 has `epOpenPort` take a bind-address argument. It reads a module-level
  `netBindAddrV4` instead, which the host backend sets from `hwLinuxBindAddr()`
  and the chip leaves at `INADDR_ANY` — the same fact, without threading a
  parameter through `epOpenAll`'s three call sites.
- `net_host.cpp` reaches the board's accessors through a weak `extern "C"`
  declaration rather than including `hwlinux.h`: a straddle may not name a
  board straddle in REQUIRES, and the symbol resolves at executable link time.
- **spangap-web's hard dependency on upnp.** `-x spangap/upnp` used to cascade
  spangap-web out of the build, because `webrtc_task.cpp` included `upnp.h`
  unconditionally. That call site (one reflexive ICE candidate) is now gated on
  `CONFIG_SPANGAP_UPNP` and `spangap/upnp` moved from spangap-web's `requires:`
  to its `additional_installs:` — still default-on, so the chip build stages
  exactly what it staged before.
- `s.net.cli_port` defaults to 8081 on the host (seeded by `net_host.cpp`
  before the shared config tree, whose own default is "closed"): a station has
  no cable, so its TCP CLI is the only door, and it is on its own loopback
  address.
- A station process serving a directory: POSIX opens one for reading and
  reports its inode size, where LittleFS and spanfs refuse — so a request for
  a mapping's root answered 200 with a length nothing could fill. Fixed in
  `openWithGzFallback` for both targets.

## 3. Two stations over TCP — PASS

Build: checkpoint 2's command with `-x reticulous/rns` and
`-x reticulous/iface-tcp` dropped. Built for board **spangap/hw-linux**.

Set-up, over each station's TCP CLI:

```
nc 127.0.0.11 8081   auth passwd admin <pw>
                     set tcp.server.add={"port":4965}
nc 127.0.0.12 8081   auth passwd admin <pw>
                     tcp peer add 127.0.0.11:4965
```

Observed:

- station 1: `port 4965 access_point enabled active=1/8`, `in 127.0.0.1
  access_point rx=343 tx=334`; log `register: iface=tcp_in/127.0.0.1#0 …` and
  `replayed 1 hosted announce onto tcp_in/127.0.0.1#0`.
- station 2: `0 up 127.0.0.11:4965 access_point enabled rx=346 tx=334`; log
  `register: iface=tcp/0 …` and `replayed 1 hosted announce onto tcp/0`.
- `tcp n -v` on each names the other as its one neighbour; `rns` reports
  `rnsd.up: 1  rns.ready: 1` on both.

The announces are visible as the bytes each side received and as the replay
lines; there is no CLI that lists *received* announces until lxmf is staged,
so the catalogue check belongs to checkpoint 5.

Changed:

- `rns/esp-idf/src/rnsd.cpp`: dropped an unused `driver/usb_serial_jtag.h`
  include (nothing in the file uses it; it has no host counterpart).
- `rns/esp-idf/components/microreticulum/CMakeLists.txt`: `-Wno-error=format=`
  and `-Wno-error=class-memaccess` for the vendored sources on linux only (both
  fire only on a 64-bit host), and mR's own `src/Utilities/tlsf.c` compiled on
  linux — the host's `heap_caps_*` wrap libc and export no `tlsf_*`, which the
  pool-stats walkers in `Memory.cpp` call.
- `hw-linux/straddle.yaml`: `# CONFIG_HEAP_TASK_TRACKING is not set` — the host
  heap attributes nothing to a task and has no
  `heap_caps_get_per_task_info` to link.
- `spangap-core/esp-idf/src/host/miniz_host.c`: `tdefl_init` decides whether
  there is a zlib stream to close from a 64-bit sentinel, not from a flag in
  memory the caller never cleared. Reading that uninitialised flag segfaulted
  every station inside `deflateEnd` at the first settings save.

## 4. Three stations over LoRa — PASS

Build: checkpoint 3's command with `-x reticulous/iface-lora` dropped. Built
for board **spangap/hw-linux**.

Run (the other agent's launcher, which starts the ether, the proxy and the
stations): `python3 reticulous/sim/run.py --nodes 3`. Then, per station, the
password and the radio's region over its TCP CLI (see above).

Observed:

- `lora/0: SX1262 found (cs=3 irq=4 busy=5 rst=6)` on every station, then
  `lora/0 up: 869.525 MHz BW=125kHz SF8 CR4/5 TXP=0dBm preamble=12 sync=0x42`
  and `registered as iface lora/0 (mtu=500 bitrate=2000 mode=access_point)`.
- `sim/record.tsv`: 12 `tx` frames, 24 `rx_begin` and 24 `rx_end` — every
  transmission reaching both of the other two stations.
- `lora n` on each station lists the other two by their RNS identity, with
  `rssi -80..-80 dBm  snr 10.0..10.0 dB` — the ether's constants, arriving
  through the model's packet-status registers.
- `lora` reports `rx 2/334B tx 1/167B`, an airtime ledger and a noise floor,
  so the driver's CSMA and observation paths run on the model unchanged.

Changed:

- `iface-lora/esp-idf/src/lora_priv.h`: the per-radio `hal` is a `RadioLibHal*`.
- `iface-lora/esp-idf/src/lora.cpp`: the host constructs a `VirtualHal` and
  brings the ether up before the radios.
- New `iface-lora/esp-idf/src/host/`: `virtual_hal.{h,cpp}` (RadioLib's HAL over
  the GPIO shim and the model), `virtual_sx126x.{h,cpp}` (the command
  interpreter, register file, payload buffer and frame timing) and
  `ether_task.{h,cpp}` (the UDP link, JSON in and out, base64 payloads).
- `iface-lora/esp-idf/CMakeLists.txt`: `esp_idf_hal.cpp` out, the three host
  files in.
- `hw-linux/esp-idf/components/driver/include/driver/gpio.h`: the pin numbering
  and the mode/pull/trigger enumerations come from IDF's own
  `hal/gpio_types.h`, which the host target ships; only the driver surface is
  declared here. Redefining them collided.

Deviations from §17:

- **§17.1's RadioLib fact is wrong.** `SX126x::findChip` does not compare
  against `"SX1262"`: RadioLib 7.7.1 defines `RADIOLIB_SX1262_CHIP_TYPE` as
  `"SX1261"` (the two parts are one silicon and report the same string). The
  model answers `"SX1261 V2D 2D02"` at `0x0320`; with `"SX1262"` there, every
  probe failed with `chip not found (-2)`.
- `VirtualHal`'s constructor takes the slot's `LoraSlot*` as well as its index:
  the HAL has to know which pin is RST (the reset edge) and which is BUSY.
- `attachInterrupt` goes through a per-pin trampoline rather than casting
  RadioLib's `void(*)(void)` to the shim's `void(*)(void*)`.
- Mode transitions are instantaneous, as §17.8 says for this step; `ready_at`
  is on the wire and carries the current instant.

## 5. A message by hand — PASS

Build: checkpoint 4's command with `-x reticulous/lxmf` dropped — the full
staged set of §17.2. Built for board **spangap/hw-linux**.

Observed, with three stations on the virtual LoRa and **no** iface-tcp peer or
inbound port configured anywhere (`tcp` reports "no outbound peers configured,
incoming ports: 0 configured"), so LoRa is the only path:

```
station 1   lxmf create station1      → b8b9d80530ff4b800015c0cc9568d8cc
station 2   lxmf create station2      → 6da8c4aedea247d6377f23f70ddd6161
both        lxmf announce
station 1   lxmf announces            → 6da8c4ae…  hops 1  cost 8  station2
station 2   lxmf announces            → b8b9d805…  hops 1  cost 8  station1
station 1   lxmf send 6da8c4ae… hello from the simulated ether
station 2   lxmf msgs                 → 1 conversation with station1, 1 unread
            lxmf read 1               → "hello from the simulated ether"
```

Station 2's log: `recv mid=851190ab… from=b8b9d805… len=175B`.

The proxy routes by hostname: `curl -H 'Host: 1.sim.localhost'
http://127.0.0.1:9000/` and the same for `2.` each return the station's SPA.

Changed:

- `lxmf/esp-idf/src/lxmf.cpp`: `briefDur`'s buffer is 24 bytes, not 16 — the
  widest `long` the compiler admits plus a suffix does not fit in 16 on a
  64-bit host, and `-Werror=format-truncation` says so.

Deviation from §17: the message was sent from station 1's **CLI**, not from its
web UI. The web UI is a single-page app that drives the station over its
WebSocket, which curl cannot stand in for; what was verified instead is that
the UI loads over the proxy from both stations, and that the send-and-receive
path underneath it carries a message end to end over the virtual radio.

## 6. The chip build — PASS

```
spangap build reticulous/reticulous --with spangap/hw-meshnology-w12 \
    --kconfig CONFIG_SPANGAP_USB_CDC=y
```

Built for board **spangap/hw-meshnology-w12** (target `esp32s3`); nothing was
flashed. Output:

```
build: done.
spangap: wrote flasher.zip (4 images)
  compiled code            3.24M  (+2.39M free in app slot)
  compressed files          312K
  user state              10.00M
```

`.spangap-build` again names this invocation, so a bare `spangap build` from
the workspace root repeats the chip build.

## Open issues

- **The container's `spangap` is stale.** Every build above was run through the
  workspace copy of `spangap-inside` with `SPANGAP_SCHEMA_PATH` pointing at the
  workspace schema (see the top of this file). Once the host rebuilds the
  image, the plain `spangap build …` invocations work as written.
- **One build directory, two targets.** `reticulous/esp-idf/build/` holds
  whichever target was built last, and switching costs a full rebuild. The host
  tree is parked at `reticulous/esp-idf/build.linux/`; to go back to it, swap
  the two directories by hand before building. Worth giving the host target its
  own build directory.
- **A station needs five CLI commands before it does anything** (the password
  and the radio's region). Faithful to a board on a cable, but the launcher
  should offer to type them.
- IDF's `nvs_flash` forces `--coverage` on the linux target, so every station
  writes `.gcda` files at exit. Harmless so far; if concurrent stations start
  complaining, set `GCOV_PREFIX` per station in the launcher.
- Not verified from here: a browser on the macOS host reaching a station
  through the container's published port 9000. The proxy itself was exercised
  by hostname from inside the container.
