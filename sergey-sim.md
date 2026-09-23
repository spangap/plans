# SIMesh: the simulator as its own project, with Sergey's Rust stack as a second station kind

> Status: **implementation plan (2026-09-23), researched to the line against
> both codebases, nothing built.** Written for an implementing agent. Every
> work package ends in a check that can be run. Read §0 and §1 before
> touching anything.

## 0. Rules for the implementer

1. **Two new directories at the workspace root**, nothing else moves:
   - `SIMesh/`: the simulator, its own git repository, commits on `main`.
   - `sergey/`: clean clones of Sergey's repository, one branch per pull
     request, never a commit on its `main`.
2. **Copy, never move.** `reticulous/sim/`, `reticulous/ether/` and
   `iface-lora/esp-idf/src/host/` stay exactly as they are until WP8, which
   is a separate, coordinated step. Another agent is running that testbed.
   Until WP8 the workspace holds two copies of the ether and the testbed, and
   `SIMesh/` is the one that receives changes.
3. **Do not edit our firmware before WP8** except for the one file WP8 names.
   The `reticulous` station kind (WP5) supplies whatever the current firmware
   needs through its own environment, which is what a kind is for.
4. **Host tests only.** Nothing here is flashed. Build the Linux station with
   the command in `reticulous/sim/README.md` when a check needs it. Do not
   start a device build.
5. **Commits in `SIMesh/`**: straight to `main`, a `Signed-off-by:` trailer,
   no other trailer. **Commits in `sergey/reticulum`**: on a branch, his
   style (`feat(scope): …`, `fix(scope): …`, a message that says why),
   opened as a pull request against his `main` on git.emcomm.cc (Forgejo).
   His `scripts/gates.sh` must pass before a pull request is opened. State
   in the pull request that the code was written with AI assistance: his
   `docs/ai-assistance-and-provenance.md` treats the question seriously and
   deserves a plain answer.
6. **Clean-room rule in his tree** (`CONTRIBUTING.md`): never copy code from
   Python Reticulum or any other Reticulum implementation into his crates.
   Nothing in this plan needs to; the Rust written here is glue over his
   traits and our C library.
7. **No `SPANGAP_` names in `SIMesh/`.** The contract variables are
   `SIMESH_*` (§5.2). Our firmware reads `SPANGAP_*` until WP8; the
   `reticulous` kind sets both.
8. **Docs describe the current state**, never what changed. A lesson worth
   keeping becomes a rule in the relevant INTERNALS file.

## 1. What exists today, with file references

### 1.1 Our side

| Piece | Where | Facts that matter |
|---|---|---|
| the ether | `reticulous/ether/ether.py` (600 lines), `test_ether.py` (20 tests, spawn the ether as a child process) | one UDP `asyncio.DatagramProtocol`; stations keyed by `sid` from every message, address updated per datagram; matches receivers on `freq`, `bw`, `sf`, `sync` (`MATCH_KEYS`, line 45) and on `mode == "RX"` (line 169); rebases sender offsets onto its own clock; per-receiver capture verdict at `rx_end`; `MAX_FRAME_US` clamps a stated span at a minute |
| the testbed | `reticulous/sim/simd.py` (42 kB), `stations.py`, `setup.py`, `scenario.py`, `proxy.py`, `webrtc.py`, `seq.py`, `ui/` (Quasar, built to `ui/dist/spa`) | one `--elf` and one `--fixed` for every station (`simd.py:930`); "up" = TCP CLI on `127.0.0.1<id>:8081` answers (`setup.py`); setup lines are our CLI text over that port, then `save`; transport ring polls `show s.rnsd.transport_enabled` every few seconds (`simd.py:344`); the card's Web UI button assumes port 80; `after_start` (`simd.py:210`) is the whole per-station lifecycle |
| a station process | `stations.py:107` (env), `:123` (pty), `:128` (exec with cwd = node dir, stdin/stdout/stderr = pty slave) | env `SPANGAP_NODE_ID`, `SPANGAP_NODE_DIR`, `SPANGAP_BIND_ADDR`, `SPANGAP_ETHER`, `SPANGAP_FIXED_DIR`; supervisor restarts on exit after 0.5 s; `state/boot` marks a configured station |
| the chip model | `iface-lora/esp-idf/src/host/virtual_sx126x.{h,cpp}` (79 + 709 lines) | one `transfer(out, len, in)` per SPI frame; register map, 256-byte buffer, IRQ mask, PA table; TX schedules `TX_DONE` at `t_end`; RX follows one frame with the 6 dB capture rule; `SetCad` accepted and **ignored** (line ~503); CAD, GFSK, duty-cycled RX absent |
| its ESP-IDF borrowings | same file | `esp_timer_get_time`, `esp_timer_create/start_once/stop` (four one-shots per slot), `portENTER_CRITICAL` on a `portMUX_TYPE` (nests), `gpio_shim_set_level` for DIO1 via `virtualHalDio1Pin(slot)`, `etherPublishTx/State`, `loraToaSeconds` from `../lora_toa.h` (33 lines, pure math) |
| the ether link | `iface-lora/esp-idf/src/host/ether_task.{h,cpp}` (49 + 248 lines) | one non-blocking UDP socket bound to the station's address, one FreeRTOS task in `select()`; cJSON in, `snprintf` JSON out, `mbedtls_base64`; three weak `hwLinux*()` symbols with defaults |
| RadioLib's HAL | `iface-lora/esp-idf/src/host/virtual_hal.{h,cpp}` (64 + 117 lines) | stays with our firmware; BUSY always reads 0; RESET rising edge calls `chip->reset()` |
| the board | `hw-linux/esp-idf/src/hwlinux.cpp` | reads the five env vars once; `chdir` to node dir; `state/`; `fixed` symlink; MAC `02:73:69:6d:00:<id>` |
| the host command | `spangap/build-system/spangap-outside:3043` (`sim)`) | `docker exec … spangap sim`, which runs `simd.py`; `SIM_PORT=9011` at line 426 |
| the container | `spangap/build-system/Dockerfile` | `pip3 install pyyaml jsonschema aiohttp` at line 96; **no `cargo`, no `rustc`**; gcc, g++, cmake 3.30 present; aarch64 |

### 1.2 His side (`https://git.emcomm.cc/berlinmesh/reticulum`, main at 2026-09-22)

| Piece | Where | Facts that matter |
|---|---|---|
| the engine | `crates/reticulum-node` (`no_std`) | `Node<P: Platform, LINKS, PATHS, MAIL, GREET>`; `Node::new(platform, &[IfaceKind])` and `Node::init_in(&mut MaybeUninit, …)`; `node.attach_tx_slots(&mut PendingTxSlots)`; `node.poll(&mut [&mut dyn RadioPhy], host_rx: &[u8])` |
| `Platform` | `crates/reticulum-node/src/traits.rs:592` | required: `now_ms`, `rng_fill`, `kv_load/kv_save/kv_delete` (`KvKey`: `Identity`, `AuthStore`, `Page`, `TcpConfig`, `NodeConfig`, `MgmtAcl`, `NodeName`, `BootCount`, `RadioParams(i)`), `host_tx(&[u8])` (fully framed KISS), `request_reboot`. Defaulted: `board_id`, `fw_build`, `reset_reason`, `request_bootloader`, the message store, the page store, wifi, `log_drain/log_stats/log_clear` |
| `RadioPhy` | `traits.rs:315` | implemented by `LoRaIface` |
| the LoRa interface | `crates/reticulum-lora-radio/src/iface.rs` | `LoRaIface<R: LoRaRadio, D: DelayNs + Clone>::new(radio, jitter_seed: u32, bridge: HostBridge, delay)`; `service()` every 25 ms (`SERVICE_SLICE_MS`); `take_window_ms()`; framing `[seq<<4 \| SPLIT][≤254]`, RNS packets ≤ 500 B in ≤ 2 frames; CSMA: `rx_lock` first, then CAD, 5 attempts, backoff `10 + 30·attempt + jitter` ms; `cad_says_busy` polls `poll_cad` up to **1000 × 1 ms** and treats a CAD that never settles as busy |
| `LoRaRadio` | `crates/reticulum-lora-radio/src/lib.rs:105` | ten methods, sync, `no_std`; `sx126x_sync_word(0x12) = 0x1424`; `preamble_symbols(cfg)`: 24 ms of preamble floored at **18 symbols** |
| the SX1262 driver | `crates/reticulum-sx1262/src/lib.rs` (1943 lines), `seam.rs` (the `LoRaRadio` impl) | `Sx1262Radio<SPI: SpiBus<u8>, OUT: OutputPin, IN: InputPin, D: DelayNs>::new(spi, nss, reset, busy, dio1, rf_sw: Option<OUT>, delay)`; constructor does `hard_reset` (RESET low 1 ms, high, 6 ms) then `init_sx1262`; **DIO1 unused**, every IRQ wait polls `GetIrqStatus`; `wait_busy` polls `is_low` 10 000 × 10 µs; `await_acknowledge` polls `is_high` 60 × 10 µs after `SetStandby/SetRx/SetTx/SetCad/Calibrate/CalibrateImage/SetRfFrequency/SetDio3AsTcxoCtrl/SetRegulatorMode/SetPacketType`; `transmit` polls IRQ every 10 µs for `TX_DONE\|TIMEOUT`, leaves the chip in STDBY; `start_cad` = `GetDeviceErrors`, `SetStandby(RC)`, `SetCadParams(0x01, sf+13, 10, 0, 0,0,0)`, `ClearIrq`, `SetCad`; `poll_cad` waits for `CAD_DONE (0x0080)`, reads `CAD_DETECTED (0x0100)`; `IRQ_TIMEOUT = 0x0200`; sync word written as register `0x0740 ← 0x14 0x24` |
| the bus idiom | `lib.rs:367` onwards | `write_cmd`: `write([op])`, `write(params)`, `flush`, NSS high. `read_cmd`: `write([op, 0x00])`, `read(n)`, `flush`, NSS high. `read_buffer`: `write([0x1E, off, 0x00])`, `read`. `read_register`: `write([0x1D, hi, lo, 0x00])`, `read`. Every transaction is NSS low, writes, optional one read, flush, NSS high |
| the board glue | `fw/nrf52840-common/src/{engine,platform,host,kv,lora_iface,logring}.rs`, `fw/rak4630/src/main.rs` | embassy; `engine_task` loop: drain `HOST_RX`, `node.poll`, deferred reboot, then await the window in 25 ms slices calling `iface.service()` (and `supe_service`/`supe_drain_txq` in a `supe` build); host serial is **RNode KISS only, no text console**; logs go to a 2 kB ring read by `rncfg log` |
| `rncfg` | `tools/rncfg/src/main.rs` | opens `serialport::new(path, 115200)` with a 200 ms timeout; subcommands `detect`, `get`, `set`, `status`, `log`, `transport`, `name`, `announce`, `reboot`, `wipe`, … |
| `rnscale` | `tools/rnscale/` (`--profile sim`) | his own simulator: N real engines in **virtual time** over a made-up medium above `RadioPhy` (`sim.rs:205 impl RadioPhy for MediumRadio`). Engine-level, deterministic, no PHY. SIMesh is the complement: real time, real drivers, a PHY model, mixed stacks |
| SUPE | `crates/reticulum-supe`, wired through `reticulum-lora-radio` under feature `supe`; `supe-on`, `supe-sched`, `supe-band` are bench switches in `fw/nrf52840-common` | our conformance vectors copied verbatim; on-air only against himself; `supe-band` excludes channel 9 by reading duty cycle instead of adaptive spectrum access |
| toolchain | `CONTRIBUTING.md`, `scripts/preflight.sh`, `scripts/gates.sh` | Rust ≥ 1.88, a C linker, `libudev-dev` + `pkg-config` (for `rncfg`, a workspace member); `fw/` is **excluded** from the workspace; gates: `cargo check --workspace --all-features --locked`, `cargo test --workspace --all-features`, clippy at zero warnings, `cargo fmt`, a `thumbv7em-none-eabihf` check of the node |

### 1.3 Two facts that decide the design

- **He has no text console.** A station of his is configured with `rncfg`
  over a serial device and logs into a ring. Every testbed assumption about
  "type a CLI line at port 8081" is ours alone.
- **His driver never uses DIO1 and never blocks on the model's timing.** It
  polls the IRQ register over SPI. So the Rust side needs no interrupt
  plumbing at all, only a thread-safe `transfer`.

### 1.4 Four interop facts to carry into the mixed scenario

| | ours | his | consequence |
|---|---|---|---|
| sync word | `0x42` default (`lora.cpp:274`), register form `0x4424` | `0x12`, register `0x1424` (`init_sx1262`) | **the ether will not deliver between them** until the scenario sets ours: `lora 0 sync 0x12` |
| preamble | 12 symbols (`lora.cpp:269`) | `preamble_symbols`: 18 at SF8/125k | the ether does not match on preamble, so the sim delivers either way; set ours to `lora 0 preamble 18` so the sim does not hide a hardware question |
| on-air framing | `[seq<<4 \| SPLIT][≤254]` (`iface-lora/INTERNALS.md` §5) | identical (`iface.rs:129`) | packets cross with nothing translated |
| SUPE channel set | all nine channels, adaptive spectrum access, 100 s/h each | `supe-band` off: all nine; on: eight | keep his `supe-band` **off** in any SUPE run; his duty accounting is per-mille, ours is seconds-per-window |

## 2. The shape

```
                    ┌────────────────── one Linux process each ──────────────────┐
                    │                                                            │
ours:  rnsd ─ RadioLib driver ─ VirtualHal ──┐                                    │
                                             ├─ SIMesh/radio (C library) ─ UDP ───┼──► ether
his:   Node ─ LoRaIface ─ Sx1262Radio ─ simesh-hal ─ simesh-radio-sys ┘            │
                    │                                                            │
                    └────────────────────────────────────────────────────────────┘
                                          simd.py: kinds, scenario, map, proxy
```

The chip model and its ether link become **one C++ library with a C ABI and
a services seam**: six things the host supplies (clock, one-shot timers, a
lock, a pin write, a UDP socket, a reader thread). Two backends: ESP-IDF
(today's code, moved) and POSIX. Our firmware links the first, his Rust the
second. Everything above the SPI bus in either stack ships to hardware
unchanged.

Why not a `LoRaRadio` over the ether wire (skipping the chip): it would skip
his driver, his BUSY handling, his CAD, the sync-word register write, and the
way CAD kills a reception in progress, which are the things that break on
boards. Why not a Rust rewrite of the model: 700 lines of chip semantics that
took a design pass to get right, and a second copy would drift from the first
on the details the testbed exists to hold constant. Why not emulate the nRF:
nothing above the bus is learnt that this does not learn, at ten times the
cost.

## 3. Layout

```
SIMesh/                           its own git repo
  README.md                       what it is, how to run it, the kinds it knows
  INTERNALS.md                    design rules (from sim/INTERNALS.md, generalised)
  STATION.md                      the station contract (§5.2)
  ether/                          copy of reticulous/ether (ether.py, test_ether.py, docs)
  testbed/                        copy of reticulous/sim minus run/ and snapshots/
    simd.py stations.py setup.py scenario.py proxy.py webrtc.py seq.py
    kinds/                        __init__.py, reticulous.py, berlinmesh.py
    ui/                           the Quasar app (dist/ ignored)
    scenarios/                    committed examples: smoke4.yaml, mixed.yaml
  radio/                          the C library
    include/simradio.h
    src/model.cpp src/ether_link.cpp src/toa.h src/json.{h,cpp}
    backend/posix/services.cpp
    backend/esp-idf/services.cpp  backend/esp-idf/CMakeLists.txt (an IDF component)
    CMakeLists.txt
    tests/test_model.py           ctypes, no firmware
  .gitignore                      testbed/run/ testbed/snapshots/ testbed/ui/dist/ testbed/ui/node_modules/ radio/build/ __pycache__/

sergey/
  README.md                       what is here and the pull-request workflow
  reticulum/                      git clone of https://git.emcomm.cc/berlinmesh/reticulum
                                  remote `origin` = his; remote `fork` = ours (WP6.1)
                                  branch `simesh-target` = the pull request
```

In his tree, on the branch:

```
crates/simesh-radio-sys/          FFI to SIMesh/radio, builds it with the `cc` crate
crates/simesh-hal/                embedded-hal 1.0 SpiBus/OutputPin/InputPin/DelayNs over it
fw/simesh/                        the Linux station: Platform, engine thread, the two doors
```

## 4. Work packages

Order matters where a later package needs an earlier one's artefact. Each
ends with **Check:** what proves it.

### WP0. Toolchain: Rust in the build container

`spangap/build-system/Dockerfile`, after the pip line at 96:

```dockerfile
# Rust, for the SIMesh station kind that is written in it (sergey/reticulum).
# rustup ships no linker; gcc is already in the image.
RUN set -eux; \
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable; \
    /root/.cargo/bin/rustup component add clippy rustfmt; \
    apt-get update && apt-get install -y --no-install-recommends libudev-dev pkg-config && rm -rf /var/lib/apt/lists/*
ENV PATH="/root/.cargo/bin:${PATH}"
```

Mind the image's user: the container runs as `spangap`, so install as that
user or into a shared prefix (`RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/usr/local/cargo`
with `chmod -R a+rwX`), the way the IDF venv is shared. `libudev-dev` is
needed only because `rncfg` is a workspace member and `--workspace` builds
it. The image is rebuilt by the host's `spangap` command; the implementer
cannot rebuild it from inside the container and must ask.

**Check:** `cargo --version` ≥ 1.88 inside the container as `spangap`;
`cd sergey/reticulum && ./scripts/preflight.sh host` reports only the MCU
items missing.

### WP1. `SIMesh/`: the copy

1. `mkdir SIMesh && cd SIMesh && git init`.
2. `cp -r ../reticulous/ether SIMesh/ether`, `cp -r ../reticulous/sim SIMesh/testbed`
   then delete `testbed/run`, `testbed/snapshots`, `testbed/__pycache__`,
   `testbed/ui/dist`, `testbed/ui/node_modules`. Keep `testbed/scenarios/smoke4.yaml`
   and `smoke7.yaml`; drop `city99.yaml` and `mesh99.yaml` unless they are
   small enough to be examples (they are 35 kB and 13 kB, keep only smoke).
3. Fix the two relative imports: `simd.py:44` imports `webrtc`, and it
   imports the ether by path (`ether_module`); find the `sys.path` line that
   reaches `../ether` and make it `os.path.join(SIM_DIR, "..", "ether")`.
   `UI_DIST` at `simd.py:48` stays relative to `SIM_DIR`.
4. `DEFAULT_ELF` / `DEFAULT_FIXED` (`simd.py:46`) point into
   `reticulous/esp-idf/build.linux/`. They become the `reticulous` kind's
   defaults in WP5; for now make them relative to the workspace root
   (`SIM_DIR/../../reticulous/esp-idf/build.linux/…`).
5. README.md: from `sim/README.md`, first section rewritten to say what
   SIMesh is (a real-time LoRa testbed: an ether, a chip model, stations of
   several kinds, a map) and that spangap/reticulous is one user of it.
   INTERNALS.md from `sim/INTERNALS.md` with the "Where the code lives"
   table pointing at the new paths. Ether docs unchanged.
6. `.gitignore` as in §3. First commit.

**Check:** from the container, `cd SIMesh/testbed && python3 simd.py --elf
../../reticulous/esp-idf/build.linux/reticulous.elf --fixed …/data_merged --bind 0.0.0.0:9012 --ether 127.0.0.1:7001`
starts, loads `smoke4.yaml` through the page on port 9012, four stations come
up and announce. Port 9012 is not published by the container; use the
page over `spangap dev`'s range or check `run/record.tsv` fills and
`nc 127.0.0.11 8081` answers. `python3 -m pytest SIMesh/ether/test_ether.py` passes.

### WP2. `SIMesh/radio`: the library, ESP-IDF backend first

The point of doing the ESP-IDF backend first: the existing station binary
proves the extraction did not change behaviour before any new code exists.

#### 4.2.1 The C ABI (`include/simradio.h`)

```c
#ifdef __cplusplus
extern "C" {
#endif
typedef struct simradio simradio_t;

enum { SIMRADIO_PIN_DIO1 = 1, SIMRADIO_PIN_BUSY = 2 };

/* The station's one link to the ether. Idempotent. An empty `ether_addr`
 * means "no ether": models exist, transmissions go nowhere. */
int simradio_station_open(int sid, const char* bind_addr, const char* ether_addr);

/* One chip per radio slot. `on_pin(ctx, pin, level)` runs on whatever
 * thread moved the line: the bus caller, the timer thread, or the reader. */
simradio_t* simradio_open(int slot, void (*on_pin)(void*, int, int), void* ctx);

/* One complete SPI frame, NSS low to NSS high. out[0] is the opcode; `in`
 * is the status byte until the data starts, then the data. Thread-safe. */
void simradio_transfer(simradio_t*, const uint8_t* out, size_t len, uint8_t* in);

/* The RST line's rising edge. */
void simradio_reset(simradio_t*);

/* What the model drives on a line right now. */
int simradio_pin(simradio_t*, int pin);

/* Microseconds on the model's clock, for a host that wants to log against it. */
int64_t simradio_now_us(void);

void simradio_close(simradio_t*);
#ifdef __cplusplus
}
#endif
```

#### 4.2.2 The services seam (`src/services.h`, internal)

```c
struct simradio_services {
    int64_t (*now_us)(void);
    void*   (*timer_create)(void (*cb)(void*), void* arg, const char* name);
    void    (*timer_start_once)(void* timer, int64_t delay_us);   /* restarts if running */
    void    (*timer_stop)(void* timer);
    void    (*lock)(void);                                        /* recursive */
    void    (*unlock)(void);
    int     (*udp_open)(const char* bind_addr, const char* dest_host_port);  /* connected, non-blocking fd; -1 on failure */
    int     (*spawn_reader)(int fd, void (*on_datagram)(const char*, size_t)); /* 0 on success */
    void    (*log)(int level, const char* fmt, ...);
};
const struct simradio_services* simradio_services(void);   /* the backend provides it */
```

**ESP-IDF backend** (`backend/esp-idf/services.cpp`): `esp_timer_get_time`;
`esp_timer_create` + `start_once` + `stop`; one global `portMUX_TYPE` with
`portENTER_CRITICAL`/`portEXIT_CRITICAL` (the model today has one mux per
chip; one global is simpler and the contention is nil); the socket code from
`ether_task.cpp:201-235`; `spawnTask(etherTaskFn, "ether", 32768, …, 6, 0)`
from `ether_task.cpp:244`; `log` → our `info/warn/err`. This backend is an
IDF component: `backend/esp-idf/CMakeLists.txt` registers
`../../src/*.cpp` plus itself with `REQUIRES esp_timer driver` and
`PRIV_REQUIRES json mbedtls` if cJSON/mbedtls stay (see 4.2.4).

**POSIX backend** (`backend/posix/services.cpp`): `clock_gettime(CLOCK_MONOTONIC)`
zeroed at first call; timers are one `std::thread` with a `std::condition_variable`
and a min-heap keyed by deadline, callbacks run on that thread **with the lock
not held** (the model takes it itself); the lock is a `std::recursive_mutex`
(the ESP-IDF critical section nests, see `raise()` called from `txDoneCb`
after `portEXIT_CRITICAL`, and `rxEndCb` called directly from `onRxEnd`);
`udp_open` = `socket/bind/connect/O_NONBLOCK` as today; `spawn_reader` = a
`std::thread` blocking in `recv` (blocking is fine here, there is no FreeRTOS
underneath); `log` → `fprintf(stderr, "simradio: …")`.

#### 4.2.3 What moves where

| From | To | Change |
|---|---|---|
| `virtual_sx126x.cpp` | `src/model.cpp` | `esp_timer_*` → `services()->timer_*`; `esp_timer_get_time` → `now_us`; `portENTER/EXIT_CRITICAL(&d->mux)` → `lock()/unlock()`; `applyDio1(slot, high)` → `chip->on_pin(ctx, SIMRADIO_PIN_DIO1, high)`; `virtualHalDio1Pin` disappears (the HAL maps pin → slot on its own side); `etherPublishTx/State` → internal `ether_publish_*` in `ether_link.cpp`; `#include "../lora_toa.h"` → `"toa.h"` (copied verbatim) |
| `virtual_sx126x.h` | `src/model.h` (internal) + the C ABI | `VirtualRxBegin`/`VirtualRxEnd` stay internal; `virtualChip(slot)`/`virtualChipCount()` become the registry behind `simradio_open` |
| `ether_task.cpp` | `src/ether_link.cpp` | the three weak `hwLinux*()` symbols go: `sid`, bind and ether addresses are arguments of `simradio_station_open`; `handleMessage` unchanged; `sendLine` unchanged; task/socket creation via services |
| `lora_toa.h` | `src/toa.h` | verbatim; `iface-lora` keeps its own copy until WP8 (two copies of 33 lines of math, for a while) |
| cJSON, `mbedtls_base64` | `src/json.{h,cpp}` | the POSIX build cannot assume either. Write a 200-line reader for the six message shapes (flat objects of numbers, strings, one array of ints) and a base64 pair, **or** vendor cJSON (MIT) into `src/` and keep a 20-line base64. Vendoring is less risk; the ESP-IDF backend then also uses the vendored copy so both backends parse identically |
| `virtual_hal.cpp` | stays in `iface-lora` | `_chip->transfer` → `simradio_transfer(_chip, …)`; `_chip->reset()` → `simradio_reset`; `virtualChip(slot)` → `simradio_open(slot, onPin, this)` where `onPin` calls `gpio_shim_set_level(_pins->dio1, level)` (this is the level-triggered interrupt rule, unchanged). **Deferred to WP8.** |

Until WP8 the ESP-IDF backend is compiled and linked by nothing. It exists so
the extraction is done once. To prove it, WP2's check builds the library
against the IDF host port **out of tree**: a throwaway CMake project under
`radio/tests/esp-idf-link/` that adds `hw-linux`'s components and links
`libsimradio` with a `main` that opens a chip and sends `SetTx`. If that is
more than an hour of fighting IDF's CMake, drop it and rely on WP8's check;
say so in the commit.

#### 4.2.4 Behaviour that must not change (write these as tests in WP3)

From `model.cpp`, keep exactly:
- reply bytes are the status byte until the data starts (`memset(in, status, len)`);
- `SetTx` timeline: `t_pre = t0 + (preamble + 4.25)·Tsym`, `t_hdr = t_pre + 8·Tsym`,
  `t_end = t0 + toa`; radiated power from the PA table;
- leaving RX (`SetStandby/SetSleep/SetFs/SetTx`) abandons air, lock, pending, stops the three RX timers;
- `onRxBegin`: ignored unless mode is RX; energy updates `airLevel/airEndUs`; the demodulator follows one frame, replaced only by one louder by `kCaptureDb = 6`;
- `onRxEnd`: ignored unless RX and `id == lockId`; payload into `buf[rxBase..]`, `rssiPkt = -2·rssi`, `snrPkt = 4·snr`, `RX_DONE` plus `CRC_ERR`/`HEADER_ERR` per verdict;
- `GetRssiInst` reads `airLevel` while air is in flight else `-110`, and `0xFF` outside RX;
- register defaults at construction (sensitivity `0x94`, sync `0x14 0x24`, version string "SX1261 V2D 2D02");
- a `state` is published on every mode or carrier change and on every register write; a `tx` on `SetTx`.

#### 4.2.5 Build

`radio/CMakeLists.txt`: `add_library(simradio STATIC src/model.cpp src/ether_link.cpp src/json.cpp backend/posix/services.cpp)`
with `option(SIMRADIO_BACKEND "posix|esp-idf")`; C++17; `-fPIC` so the
ctypes tests can load it as a shared object too (`add_library(simradio_shared SHARED …)`).
The Rust `-sys` crate compiles the same sources with `cc` (WP6), so keep the
source list flat and free of generated files.

**Check:** `cmake -B build && cmake --build build` produces `libsimradio.a`
and `libsimradio.so` on the container's aarch64 with `-Wall -Wextra` clean.

### WP3. The POSIX backend, CAD, and the model's own tests

#### 4.3.1 CAD (mandatory, not optional)

His `cad_says_busy` treats a CAD that never raises `CAD_DONE` as busy after
one second, and after five attempts the transmit fails. A model that ignores
`SetCad` makes every one of his transmissions fail five seconds late. Add:

- `SetCadParams (0x88)`: store `cadSymbolNum` code (`0x00..0x04` = 1, 2, 4, 8, 16 symbols), `detPeak`, `detMin`, `exitMode` (`0x00` CAD_ONLY, `0x01` CAD_RX), `cadTimeout` (3 bytes, ignored).
- `SetCad (0xC5)`: mode → `"CAD"` (status bits: the datasheet reports RX `0x50` during CAD; use `ST_RX` for the status byte, `"CAD"` on the wire). Leaving RX for CAD abandons the demodulator lock but **keeps the energy** (`airLevel/airEndUs`): a frame the receiver was following is still on the air and is what CAD is meant to find. Arm one timer at `now + symbols·Tsym`. On expiry: `CAD_DONE`, plus `CAD_DETECTED` if `now < airEndUs` (energy in flight at this antenna); then mode → `STDBY_RC` (exit mode CAD_ONLY) or `RX` (CAD_RX, only when detected; else STDBY_RC), and publish state. Add `SetCad` to the "leaving RX" list for the lock and timers but not for the energy fields.
- **The ether must deliver `rx_begin` to a station in CAD**, or the energy the CAD needs to detect is never known for frames that start during the CAD window (2 symbols at SF8/125k is 4 ms; at SF12 it is 66 ms). One change in `ether.py:169`: `listening()` returns true for mode `"RX"` **or** `"CAD"`. The model in CAD mode records energy from `onRxBegin` (the "Energy first" block) and skips the demodulator block. `rx_end` for such a frame is ignored (no lock). Add an ether test: a station in CAD hears `rx_begin` and no `rx_end` verdict is recorded for it as a reception (or is recorded; pick one and assert it; the map should not draw a green flash for a CAD, so prefer not delivering `rx_end` to CAD stations: `deliver_end` checks the receiver's current mode).

#### 4.3.2 BUSY

Keep "never busy" (`simradio_pin(BUSY)` returns 0). His `await_acknowledge`
then spins 600 µs after every starting command; measured cost is nothing at
25 ms polls. If a later profile shows it, raise BUSY for 100 µs after the ten
starting opcodes using one more timer. Do not do it now.

#### 4.3.3 Tests (`radio/tests/test_model.py`, ctypes against `libsimradio.so`)

A fake ether: a UDP socket the test binds, the library pointed at it via
`simradio_station_open(sid, "127.0.0.1", "127.0.0.1:<port>")`. Helpers:
`cmd(op, params, read_n) -> bytes` building the frame exactly as his driver
does. Tests, each named for the rule it holds:

1. reply is the status byte until the data starts;
2. `SetTx` publishes a `tx` whose `t_pre/t_hdr/t_end` offsets match the ToA formula for SF8/BW125/CR5/pre 18/len 42;
3. `TX_DONE` lands at `t_end` ± 10 ms and the mode falls back per `SetRxTxFallbackMode`;
4. `rx_begin` in RX raises `PREAMBLE_DETECTED|SYNC_WORD_VALID` at `t_pre`, `HEADER_VALID` at `t_hdr`; `rx_end` clean → `RX_DONE`, buffer, `GetRxBufferStatus`, `GetPacketStatus` encode `-2·rssi` and `4·snr`;
5. `rx_end` with verdict `crc` → `RX_DONE|CRC_ERR`;
6. a second `rx_begin` 3 dB louder is ignored; 7 dB louder replaces the lock and the first frame's `rx_end` is ignored;
7. `SetStandby` during a reception drops it: no `RX_DONE` ever;
8. `GetRssiInst` reads the air level during a frame and −110 after, `0xFF` outside RX;
9. `WriteRegister 0x0740 ← 14 24` publishes `state` with `sync == 0x12`; `44 24` gives `0x42`;
10. CAD with no air: `CAD_DONE` only, after `2·Tsym`; CAD with a frame in flight: `CAD_DONE|CAD_DETECTED`; CAD started while following a frame abandons the lock; `rx_begin` arriving during CAD is counted as energy;
11. `ClearIrqStatus` clears only the given bits; `GetIrqStatus` never tears (hammer it from a thread while the timer thread raises bits);
12. `simradio_reset` restores mode and clears IRQs.

**Check:** `python3 -m pytest SIMesh/radio/tests SIMesh/ether` green.

### WP4. `hw-linux` env names: nothing yet

Our firmware keeps reading `SPANGAP_*` until WP8. The `reticulous` kind sets
them. No change in this package; it exists to say so.

### WP5. Station kinds in the testbed

#### 4.5.1 The contract every station gets (`SIMesh/STATION.md`)

| Variable | Meaning |
|---|---|
| `SIMESH_NODE_ID` | small integer, unique on the host; the last byte of any MAC the station makes |
| `SIMESH_NODE_DIR` | the station's directory; its cwd; state under `state/` |
| `SIMESH_BIND_ADDR` | `127.0.0.1<id>`; every socket binds here |
| `SIMESH_ETHER` | `host:port` of the ether |
| kind `env:` | anything the binary needs beyond that |

stdin/stdout are the console, text, shown to a person and appended to
`log`. Exit to reboot; the supervisor restarts on the same directory. Ports
are the station's own; its kind says which.

#### 4.5.2 Scenario schema (`scenario.py`)

```yaml
kinds:                                  # new, optional; absent = one `reticulous` kind from --elf/--fixed
  reticulous:
    elf: ../../reticulous/esp-idf/build.linux/reticulous.elf
    env: { SPANGAP_FIXED_DIR: ../../reticulous/esp-idf/build.linux/data_merged }
  berlinmesh:
    elf: ../../sergey/reticulum/target/release/simesh
nodes:
  alpha:  { id: 1, kind: reticulous, pos: [...] }      # kind: defaults to the first kind
  sergey: { id: 2, kind: berlinmesh, pos: [...], setup: ["set --freq-hz 869525000 --sf 8 --bw-hz 125000", "name sergey"] }
```

`read()` fills `kinds` from `--elf/--fixed` when absent, so every existing
scenario loads unchanged. `dump()` writes `kinds:` only when the scenario
has more than the default. Paths are relative to the scenario file. `expand()`
macros unchanged. The `setup:` at scenario level applies to nodes of the
**first** kind only, because a shared line cannot mean the same thing in two
dialects; document that, and prefer per-kind `setup:` under `kinds.<k>.setup`
(add it: lines every node of that kind gets, before its own).

#### 4.5.3 The kind class (`testbed/kinds/__init__.py`)

```python
class Kind:
    name: str
    def __init__(self, spec, scenario_dir): ...    # elf, env, setup from the yaml
    def env(self, station) -> dict                 # SIMESH_* plus spec env, paths absolutised
    async def wait_up(self, station, timeout) -> bool
    async def run(self, station, line, timeout) -> str        # one setup line / Run command
    async def flush(self, station)                            # before stop/snapshot; may be a no-op
    async def transport(self, station) -> bool | None         # None = unknown, no ring
    def web_port(self) -> int | None                          # None hides the Web UI button
    def configured(self, station) -> bool                     # has this directory been set up
```

`reticulous.py`: today's behaviour lifted out of `simd.py`/`setup.py`:
`wait_up` = TCP connect to `:8081`; `run` = `setup_module.ask`; `flush` =
`save`; `transport` = `show s.rnsd.transport_enabled`; `web_port` = 80;
`configured` = `state/boot` exists; `env` adds the five `SPANGAP_*` names
(same values as the `SIMESH_*` ones) plus `SPANGAP_FIXED_DIR` from the spec.

`berlinmesh.py`: `wait_up` = `$SIMESH_NODE_DIR/kiss` exists **and**
`rncfg detect <path>` prints `DETECT   : ok`; `run(line)` =
`rncfg <verb> <path> <args>` where `line` is `"<verb> <args…>"` and the
path is inserted after the verb (his argv is `rncfg <cmd> <PORT> …`);
`flush` = no-op (his KV writes are synchronous); `transport` = parse
`rncfg transport <path> get` (check the exact output form when implementing;
`None` if it cannot be parsed); `web_port` = `None`; `configured` =
`state/` non-empty. `rncfg` binary path: `<repo>/target/release/rncfg`,
from the kind's spec (`tools: { rncfg: … }`) or `PATH`.

`simd.py` changes: `make_station` passes the kind; `Station.env()` calls
`kind.env`; `after_start` calls `kind.wait_up`, `kind.run` per line,
`kind.transport`; `flush_station` → `kind.flush`; `read_transport` →
`kind.transport`; `node_message` gains `"kind"` and `"web": kind.web_port() is not None`;
`resolve_host` (the proxy) refuses a node whose kind has no web port with a
plain-text answer. The **Run command** dialog gains a kind selector; the
line goes only to stations of that kind. UI: the card hides **Web UI** when
`web` is false and shows the kind name under the hostname. Keep UI edits to
those two; `npx quasar build` in the container to check.

`stations.py`: `Station` takes `kind` instead of `elf, fixed`; `elf` comes
from the kind; the pty and supervisor are unchanged.

**Check:** `smoke4.yaml` unchanged loads and runs as before (regression);
a scenario with an explicit `kinds:` block naming only `reticulous` runs
identically; `python3 -m pytest testbed/` if any unit tests exist for
`scenario.py` (add one for `read()` round-tripping `kinds:`).

### WP6. `sergey/`: his tree, the crates, the station

#### 4.6.1 Setup

```sh
mkdir sergey && cd sergey
git clone https://git.emcomm.cc/berlinmesh/reticulum
cd reticulum
git remote add fork <our fork on git.emcomm.cc>     # ask Rop for the account/fork URL
git checkout -b simesh-target
```

`sergey/README.md`: the two remotes, the branch rule, how to run
`scripts/gates.sh`, how to build the station, and that `third_party/` stays
empty (his interop tests are not our concern here).

#### 4.6.2 `crates/simesh-radio-sys`

`Cargo.toml`: `links = "simradio"`, `build-dependencies = { cc = "1" }`,
no runtime deps. `build.rs`:

```rust
let dir = std::env::var("SIMESH_RADIO_DIR")
    .expect("SIMESH_RADIO_DIR must point at SIMesh/radio (the C chip model this station links)");
cc::Build::new().cpp(true).std("c++17")
    .include(format!("{dir}/include")).include(format!("{dir}/src"))
    .files(["src/model.cpp","src/ether_link.cpp","src/json.cpp","backend/posix/services.cpp"].map(|f| format!("{dir}/{f}")))
    .compile("simradio");
println!("cargo:rerun-if-env-changed=SIMESH_RADIO_DIR");
println!("cargo:rustc-link-lib=stdc++");   // the model is C++ behind a C ABI
```

`src/lib.rs`: the eight functions from §4.2.1 in an `extern "C"` block,
written by hand (no bindgen: the ABI is eight functions and must not drift
silently). `#![no_std]` is unnecessary; this is host-only.

Add both crates to the root workspace `members` (they are host crates and
should pass his gates); `fw/simesh` stays under the excluded `fw/` with its
own `Cargo.toml` and `[workspace]` table, like his other targets, using
`path` dependencies on `../../crates/*`. A `.cargo/config.toml` in
`fw/simesh` sets `[env] SIMESH_RADIO_DIR = { value = "../../../SIMesh/radio", relative = true }`
so `cargo build` works from the workspace layout without exporting anything;
the env var still wins when set.

#### 4.6.3 `crates/simesh-hal`

`Cargo.toml`: `embedded-hal = "1"`, `simesh-radio-sys`.

```rust
pub struct Chip(Arc<Inner>);          // Inner: *mut simradio_t, Mutex<Pending>, pins: AtomicU8 for DIO1/BUSY
pub struct SimSpi(Chip);              // SpiBus<u8>
pub enum Line { Nss, Reset, Busy, Dio1, RfSw }
pub struct SimPin(Chip, Line);        // OutputPin for Nss/Reset/RfSw, InputPin for Busy/Dio1
#[derive(Clone)] pub struct StdDelay; // DelayNs
impl Chip { pub fn open(slot: i32) -> Chip; pub fn split(&self) -> (SimSpi, SimPin /*nss*/, SimPin /*reset*/, SimPin /*busy*/, SimPin /*dio1*/) }
```

**Frame reassembly rule** (`Pending { open: bool, out: Vec<u8>, done: bool }`):

- `Nss.set_low()`: `open = true, out.clear(), done = false`.
- `SpiBus::write(words)`: if `done`, start a fresh frame (`out.clear(); done = false`); append.
- `SpiBus::read(buf)` / `transfer(rd, wr)` / `transfer_in_place(buf)`: append `wr` (or zeros of `rd.len()`); call `simradio_transfer(out, out.len(), in)`; copy the **last** `rd.len()` bytes of `in` into `rd`; `done = true`.
- `flush()`: no-op.
- `Nss.set_high()`: if `open && !done && !out.is_empty()`: `simradio_transfer` with a scratch `in`; then `open = false`.
- `Reset.set_low()`: remember; `Reset.set_high()` after a low: `simradio_reset`.
- `RfSw`: ignored.
- `Busy.is_low()`: `simradio_pin(BUSY) == 0`. `Dio1.is_high()`: `simradio_pin(DIO1) != 0`.
- `StdDelay::delay_ns/us/ms`: `std::thread::sleep`. Linux floors a sleep at about 50 µs; his `transmit` loop calls `delay_us(10)` per IRQ poll, so a frame's airtime is spent in a poll every ~60 µs, each a `simradio_transfer` under the lock. That is fine at one station and tolerable at twenty; if it shows, replace `delay_us(≤50)` with `std::thread::yield_now()`.

Traced against his idioms (`lib.rs:367-428`):
`write_cmd` → NSS low, write(op), write(params), flush, NSS high → one transfer at NSS high. ✓
`read_cmd` → write([op,0]), read(n) → transfer with `[op,0,0×n]`, return `in[2..]`. ✓ (`GetIrqStatus` needs `len ≥ 4`: 2 + n=2. ✓)
`read_buffer` → write([0x1E,off,0]), read(n) → `in[3..]`. ✓ matches `model.cpp` `CMD_READ_BUFFER` (`i ≥ 3`).
`read_register` → write([0x1D,hi,lo,0]), read(n) → `in[4..]`. ✓ (`i ≥ 4`).
`GetRssiInst` → `[0x15,0,0]`, `in[2]`. ✓  `GetPacketStatus` → `[0x14,0,0,0,0]`, `in[2..5]`. ✓
`GetDeviceErrors`: his reads 2 bytes → `[0x17,0,0,0]`, `in[2..4] = 0,0`. ✓

Tests (`crates/simesh-hal/tests/`): the reassembly rule against a fake
`simradio_transfer` is not possible through the `-sys` crate; instead test
against the real library with the fake ether from `radio/tests` reimplemented
in Rust (a `UdpSocket` on `127.0.0.1:0`): `Sx1262Radio::new` completes (no
`BusyTimeout`), `configure` publishes `state` with `sync == 0x12`,
`transmit(&[1,2,3])` returns `Ok` after the frame's airtime and the fake ether
saw one `tx` with a 3-byte payload, `start_receive` + an injected
`rx_begin/rx_end` makes `try_receive` return the payload, `start_cad` +
`poll_cad` settles within 100 ms. These run under `cargo test --workspace`
and need the C++ toolchain, which his CI host has (it builds `rncfg`).

#### 4.6.4 `fw/simesh`: the station

`fw/simesh/Cargo.toml`: a binary crate `simesh`, `[workspace]` (own root, as
`fw/rak4630`), deps `reticulum-node` (features as `nrf52840-common` uses:
check its `Cargo.toml`, take `rustcrypto`, `resource`, `alloc` as needed),
`reticulum-lora-radio`, `reticulum-sx1262` (feature `iface`), `simesh-hal`,
`reticulum-logring`, `reticulum-rnode-proto`, `log`, `getrandom`, `nix`
(for `openpty`, `ptsname_r`) or `libc`.

`src/platform.rs`:

| `Platform` method | Implementation |
|---|---|
| `now_ms` | `Instant::now() - START` |
| `rng_fill` | `getrandom::getrandom` |
| `kv_load/save/delete` | one file per key: `state/kv/<code>` with `key_code` copied in spirit from `fw/nrf52840-common/src/kv.rs:116` (`Identity=1, AuthStore=2, Page=3, TcpConfig=4, NodeConfig=5, MgmtAcl=6, NodeName=7, BootCount=8, RadioParams(i)=0x10+i`); write to a temp and rename, so a mid-write kill leaves the old blob |
| `host_tx` | write to the KISS pty master (below) |
| `request_reboot` | set an `AtomicBool`; the loop exits the process with status 0 after 50 ms |
| `board_id` | `reticulum_rnode_proto::BOARD_UNKNOWN` unless he assigns one |
| `fw_build` | `env!("CARGO_PKG_VERSION")` plus the git hash if easy |
| `log_drain/stats/clear` | a `reticulum_logring::Ring` behind a `Mutex`, fed by a `log::Log` implementation that **also** prints each line to stdout |

`src/main.rs`:

1. Read `SIMESH_NODE_ID`, `SIMESH_NODE_DIR`, `SIMESH_BIND_ADDR`, `SIMESH_ETHER`; `chdir(NODE_DIR)`; `mkdir state`.
2. `simesh_radio_sys::simradio_station_open(id, bind, ether)`.
3. **The KISS door**: `openpty`; `symlink(ptsname(slave), "kiss")` (unlink first); keep **both** fds open for the process lifetime (a master whose slave has been opened and closed by `rncfg` reads `EIO`; holding our own slave fd open prevents that). Set the slave raw. A reader thread on the master pushes bytes into an `mpsc` channel; `host_tx` writes to the master.
4. `Chip::open(0)` → `split()` → `Sx1262Radio::with_clock(spi, nss, reset, busy, dio1, None, StdDelay, ClockSource::Crystal)` (no TCXO: the model accepts `SetDio3AsTcxoCtrl` anyway, but Crystal skips a 10 ms sleep).
5. `LoRaIface::new(radio, jitter_seed, bridge, StdDelay)` with `bridge` = a function that KISS-frames the received packet with RSSI/SNR to the host, copied in shape from `fw/nrf52840-common/src/host.rs:93 bridge_rx` (it calls into his `reticulum-rnode-proto` encoders; check the exact calls there and reuse them).
6. `let mut node = Box::new(Node::<SimPlatform, 4, 192, 16, 2>::new(platform, &[IfaceKind::LoRaSubGhz])?)`; `node.attach_tx_slots(Box::leak(Box::new(PendingTxSlots::new())))`.
7. The loop, a direct transcription of `engine.rs:261-389` minus BLE, FSK and the watchdog: drain the channel into `drain[256]`, `node.poll(&mut [&mut iface], &drain[..n])`, check reboot, then `window = iface.take_window_ms().max(2)`, and while it lasts: break if host bytes are waiting, `sleep(min(remaining, 25 ms))`, `iface.service()`, and in a `supe` build `iface.supe_service(now)` + `iface.supe_drain_txq(now)`.
8. Features: `supe`, `supe-on`, `supe-sched`, `supe-band` mirrored from `nrf52840-common/Cargo.toml` so a SUPE bench build of this target is the same switches he flips on boards. Default: none.

Build: `cd sergey/reticulum/fw/simesh && cargo build --release`; the binary
is `fw/simesh/target/release/simesh`. `rncfg`: `cargo build --release -p rncfg`
at the workspace root → `target/release/rncfg`.

**Check:** run one station by hand from `SIMesh/testbed` with a two-node
scenario where both are `berlinmesh`; `rncfg detect run/nodes/a/kiss` says
ok; `rncfg set run/nodes/a/kiss --freq-hz 869525000 --sf 8 --bw-hz 125000`
(verify the exact `set` grammar in `rncfg`'s usage); `rncfg announce`
(or wait for the engine's first announce) and `seq.py --tail` shows an
ANNOUNCE heard by the other; `rncfg log` shows his engine's lines; the
console window shows the same lines.

### WP7. The mixed scenario (`SIMesh/testbed/scenarios/mixed.yaml`)

Three of ours (`alpha`, `bravo`, `charlie`) in a line 1 km apart, transport
enabled, and `sergey1` next to `alpha`, `sergey2` next to `charlie`, with an
obstruction of 80 dB between `sergey1` and `sergey2`. Exponent 2.7,
869.525 MHz, SF8, BW125, CR5. Scenario-level (reticulous) setup adds:

```
lora 0 sync 0x12
lora 0 preamble 18
```

and the `berlinmesh` kind's setup sets the same carrier, sf, bw, cr and a
name. Then, in order, each one a `seq.py` reading or a station log line:

1. **Announces cross both ways.** `seq.py --only ANNOUNCE`: every station's announce is heard by its neighbours of the other kind. If not, the first thing to check is `record.tsv` `state` lines: `sync` and `freq/bw/sf` must be equal for the pair.
2. **A path forms through our transports.** `rncfg` on `sergey1` shows a path to `sergey2`'s destination via `alpha` (his `rncfg` has `heard`/`probe`; use what exists).
3. **A two-frame split both ways.** A 400-byte payload (an LXMF message from `rnchat` on his side; from ours, the LXMF CLI) shows as two frames with the same seq nibble and reassembles at the far end.
4. **CSMA under contention.** Simulation ▸ Run command `lora 0 a` at the `reticulous` kind with 30 s spread while his announce every 30 s (`supe-on`-style bench interval is only in a supe build; otherwise trigger with `rncfg announce`): `seq.py` shows his frames starting after ours end, never inside them, and ours likewise.
5. **The hidden terminal.** `sergey1` and `sergey2` transmit within the same second (two `rncfg announce`s): `charlie` and `alpha` each keep their near frame, `bravo` in the middle keeps neither, red flashes on the map.
6. **A SUPE exchange** (only when both sides are built with SUPE and his `supe-band` is off): a hail from ours answered by his, or the reverse, one complete exchange in `seq.py`. Expect divergence and record it; that is the point of this step.

**Check:** items 1 to 5 observed and written into `SIMesh/README.md` as the
mixed-kind walkthrough, with the exact commands.

### WP8. Our firmware links the library (coordinated, last)

One commit in `iface-lora` and one in `hw-linux`, applied when the other
agent's use of `reticulous/sim` is over, or on a branch until then:

1. `iface-lora/esp-idf/CMakeLists.txt:20-26`: replace the three host sources with the SIMesh component: `list(APPEND EXTRA_COMPONENT_DIRS ${WORKSPACE}/SIMesh/radio/backend/esp-idf)` (find how `hw-linux` gets its components onto `EXTRA_COMPONENT_DIRS`; the buildable stages every straddle's `components/`, so the simplest is a `components/simradio` directory in `hw-linux` whose `CMakeLists.txt` points at `../../../../SIMesh/radio` sources; a relative path out of the straddle is unusual and must be documented in `hw-linux/README.md`), `REQUIRES simradio` on `iface-lora`'s host build.
2. `virtual_hal.cpp` as in the 4.2.3 table; delete `virtual_sx126x.*`, `ether_task.*`.
3. `hwlinux.cpp`: read `SIMESH_*`; call `simradio_station_open(nodeId, bindAddr, ether)` from `HwLinuxBoard::onStart` (today `etherStart()` is called from the radio bring-up; find the call and move it). Drop the three `hwLinux*()` C functions if nothing else uses them (`grep -rn hwLinux`).
4. `reticulous` kind: drop the `SPANGAP_*` env.
5. `reticulous/sim`, `reticulous/ether`: delete, with `reticulous/README.md`
   and `hw-linux/README.md` pointing at `SIMesh/`. `spangap-outside:3043`
   `sim)` runs `SIMesh/testbed/simd.py`; simplest is the in-container
   `spangap sim` verb (`spangap-inside`) changing the path it execs.

**Check:** the Linux station builds, `smoke4.yaml` runs on the SIMesh
testbed with the rebuilt binary, `seq.py` shows announces as before, and
`record.tsv` `state` lines are byte-identical in shape to the old ones.

## 5. Pitfalls, each with the reason

1. **The model's critical section nests.** `rxEndCb` is called directly from `onRxEnd` and takes the lock again; `raise()` takes it after `txDoneCb` released it. A plain `std::mutex` deadlocks on the first received frame. Use `std::recursive_mutex`.
2. **Callbacks that drive DIO1 must run with the lock released**, as today's `applyDio1` does. Our HAL's `on_pin` runs the driver's interrupt handler synchronously, and that handler issues SPI commands that take the lock. On the Rust side `on_pin` only stores a level, but the rule holds for both.
3. **The timer thread must not hold the lock while sleeping**, and a `timer_start_once` on a running timer restarts it (`esp_timer_stop` then `start_once` today). The three RX timers are re-armed for every frame.
4. **`rx_begin` offsets are the sender's clock**; the model schedules `tPre - t0` and `tHdr - t0` from `now`. Do not "correct" this with the ether's `t0`.
5. **His `read_cmd` sends the NOP itself** (`[op, 0x00]`) and then reads. A reassembly that inserted its own NOP would shift every reply by one byte, and `GetIrqStatus` would read the status byte as the high IRQ byte: every IRQ bit set, `TX_DONE|TIMEOUT` seen instantly, transmits "succeeding" in zero time. Test 6.3's `transmit` timing check catches it.
6. **`SpiBus::write` may be called several times per frame** (opcode, then params, then data for `write_buffer`). Append; do not transfer per write.
7. **A frame with a read is complete at the read**, not at NSS high. Transfer once; at NSS high do nothing if `done`.
8. **CAD is mandatory** (4.3.1). Without `CAD_DONE`, his transmit fails after 5 s and logs "CAD never settled"; the symptom looks like a dead radio.
9. **The ether must deliver to a station in CAD**, or his carrier sense is blind to frames that start during the CAD window and the hidden-terminal test says the wrong thing.
10. **Sync word 0x42 vs 0x12.** The mixed scenario must set ours. The ether's `record.tsv` `state` lines say what each station last stated; check there first when nothing crosses.
11. **`SetTx` is issued after `SetStandby(XOSC)` and a `SetPacketParams` with the real length**; the model reads `payloadLen` from the last packet params, so the ordering his driver uses (`apply_packet_params(len)` then `write_buffer` then `SetTx`) is the one the model was written against. RadioLib does the same.
12. **His `transmit` leaves the chip in STDBY and `resume_rx` re-arms RX**; the model's fallback mode after `TX_DONE` is whatever `SetRxTxFallbackMode` said (he never sends it, so STDBY_RC). Both agree.
13. **The pty EIO trap**: reading a pty master returns `EIO` once every slave fd has been closed. `rncfg` opens and closes the slave per command. Keep one slave fd open in the station forever.
14. **`serialport` sets termios on the slave** (baud, raw). A pty accepts it. But `rncfg`'s 200 ms read timeout means the engine must answer a KISS command within one poll iteration; the loop breaks its window early when host bytes are waiting (`engine.rs:365`), so keep that `break` in the transcription.
15. **Stdout is the console; do not print KISS there.** The testbed appends stdout to `log` and shows it in the Console window; binary KISS would render as garbage and, worse, a stray `0xC0` in a log line would be read as a frame delimiter by nothing, but it confuses people.
16. **`Node` is ~43 kB plus a 192-entry path table; build it on the heap** (`Box::new(Node::new(..))`), never as a stack temporary in a thread with the default 2 MB stack. It fits, but `init_in` exists for a reason and a `Box<MaybeUninit<Node>>` with `init_in` is the exact analogue of his static.
17. **`fw/` is excluded from his workspace**; `fw/simesh` needs its own `[workspace]` and its own `Cargo.lock`. His gates do not build `fw/`; add `fw/simesh` to `scripts/gates.sh`'s host section on the branch (a `cargo check` in that directory) so the pull request proves it builds.
18. **His release profile is `opt-level = "z"` with LTO**; signature checks are slow. For a twenty-station run build with `--profile sim` (exists at the workspace root; `fw/simesh`'s own workspace needs the same `[profile.sim]` copied in).
19. **`libudev-dev` is needed to build the workspace at all** because `rncfg` is a member; without it `cargo test --workspace` fails in a way that looks unrelated.
20. **`cc` needs `cargo:rustc-link-lib=stdc++`** or the link fails on `std::map`/`std::thread` symbols with a page of undefined references.
21. **Do not rename `SPANGAP_*` in our firmware before WP8**; the other agent's testbed sets those names.
22. **Every path in a scenario is relative to the scenario file**; `os.path.abspath(args.elf)` in `parse_args` (`simd.py:952`) is the precedent, apply it in `Kind.__init__`.
23. **A station is "configured" is kind-specific.** Ours writes `state/boot`; his writes `state/kv/1` (Identity) on first boot. The setup lines run only on an unconfigured station; get this wrong and `rncfg name` runs on every restart (harmless) or never (a station with no name).
24. **His engine's transport setting** (`rncfg transport`) defaults to off; the mixed scenario relies on **our** nodes for transport, which is why the topology in WP7 puts his at the ends.
25. **`preamble_symbols` is 18 for both at SF8/125k only if ours is set to 18**; the ether ignores preamble, so a mismatch is invisible in the sim and real on hardware. Set it, and note in `STATION.md` that the ether does not model preamble matching.
26. **The ether keys stations by `sid`, and a `sid` collision between two kinds is two processes answering as one station.** Node ids are unique per scenario already; keep it that way in `scenario.py`'s `next_id`.
27. **Twenty of his stations polling SPI every 60 µs during transmit is CPU**; measure before scaling a mixed scenario past ten.

## 6. Done criteria

- `SIMesh/` is a repository with the ether, the testbed, the C library, the
  station contract, and tests that run without firmware.
- `reticulous/sim` and `reticulous/ether` are untouched until WP8.
- Two kinds exist and a scenario can mix them; one scenario file in
  `SIMesh/testbed/scenarios/` does.
- `sergey/reticulum` on branch `simesh-target` builds `fw/simesh`, passes
  `scripts/gates.sh`, and a pull request is open with the AI-assistance
  statement.
- The five mixed checks of WP7 are observed and written up.
- WP8 is done or is a ready branch with its own check passed.

## 7. Open questions, for Rop and Sergey

1. **Where the Rust crates live.** This plan puts all three in his tree, on a branch, because they instantiate his `Node` and implement his `Platform`. If he would rather not carry a C++ dependency, `simesh-radio-sys` and `simesh-hal` can live in `SIMesh/rust/` and `fw/simesh` alone goes to him, with a path dependency. Ask before opening the pull request.
2. **A board id for the SIMesh target** in `reticulum-rnode-proto` (`BOARD_*`), so `rncfg detect` names it. His to assign.
3. **Sync word.** Our default `0x42` is not RNode's `0x12`. Whether ours should change is a reticulous question, not a testbed one; the scenario sets it meanwhile.
4. **SUPE channel 9 and duty accounting.** His `supe-band` masks channel 9 on a duty-cycle reading; our plan states adaptive spectrum access for every channel (`plans/SUPE.md` §14.2, `plans/afa.md` §1). Both ends must derive the same schedule, so this is settled between the two of you before WP7 step 6 means anything.
5. **The SIMesh public home.** Until it has a remote, his `build.rs` finds the library by `SIMESH_RADIO_DIR`; once it does, a git submodule under `third_party/` (which his tree already gitignores; use `vendor/simesh-radio` instead) is the clean form.
