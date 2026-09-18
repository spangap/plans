# Simulation — a virtual radio and a virtual ether

> Status: **design notes plus a completed research pass (2026-09-17); the
> virtual radio (§2), the ether (§4, §5, §9), the machine and container
> layout (§10.1), the host I/O rule (§2.3, §11) and the live testbed (§16)
> are designed to the level of messages, state machines and rules
> (2026-09-18). Step 1, the proof of concept (§17), is specified to the
> file and line and is the next build.** Written to be handed over.

## 0. The shape

A LoRa radio that behaves like an SX1262 to the code above it, but sends each
frame as a UDP datagram to a server — "the ether" — that holds every station's
position in a virtual 2D space. The ether decides, from the transmitter's power
and the geometry, which other stations hear the frame cleanly, which hear it as
a CRC error, and which merely have their noise floor raised by it. Frames that
are heard are delivered to the receiving station, which hands them to its normal
receive callback.

To any code above it, it is just a radio. Any number of stations can join.

```
station A ──┐
station B ──┼──► ether ──► per-station verdict: clean / CRC error / noise only
station C ──┤              (position, tx power, path loss, capture, sync word,
station D ──┘               occupancy, noise accumulation)
```

The stations are the Reticulous firmware itself, built for ESP-IDF's Linux
host target and run as ordinary Linux processes (§10). A 30–50 station run is
the working size for network experiments; the runtime imposes no ceiling near
that.

## 1. Why this is worth building

Two purposes, and both need many stations:

**Radio parameters.** Test SUPE and other radio improvements against synthetic
traffic on a topology we choose. Three specific things we currently cannot do,
in descending order of pain:

- **Retune turnaround is unmeasurable on hardware without a scope.** The two
  constants that govern every deadline in SUPE are guesses. In a simulator the
  virtual radio charges the datasheet times and is genuinely deaf while
  retuning, so a wrong constant shows up as deterministic frame loss in a test
  rather than as a mystery on air.
- **Contention behaviour needs more than two nodes.** Everything interesting
  about carrier sense, deferral, channel switch collisions and the deafness
  window happens with four to ten stations. We have two boards.
- **The departure policy is unanswerable by argument.** When a channel switch
  is worth taking, how long to hold traffic, how to treat a peer's application
  latency — these want to be measured against synthetic traffic patterns, not
  reasoned about. See §7.

**A test bed for all of our code.** Every straddle that can build on the host
gets tests that run against other nodes: announces, links, LXMF delivery,
Nomad page fetches, rnsh sessions, the interface straddles, the on-device UI
via its framebuffer (§14). What cannot build on the host is listed in §12; it
is a slice we accept losing.

## 2. The seam: an SX1262 on a virtual SPI bus

Our firmware talks to the radio through RadioLib, and RadioLib talks to the
chip through a `RadioLibHal` — a dozen virtuals for SPI transfer, GPIO level,
interrupt attach and time. The virtual radio is a HAL whose SPI transfer lands
in an emulated SX1262 instead of a wire. Above it, **RadioLib's real SX1262
driver and the same iface-lora sources compile and run on the host** — not a
model of our code, our code, and not a model of the driver either.

```
lora.cpp / lora_radio.cpp / lora_bridge.cpp …      unchanged
        │  PhysicalLayer* and SX126x* calls, mod->SPIwriteStream, getIrqFlags
RadioLib SX1262 / SX126x / Module                   unchanged, pinned version
        │  RadioLibHal: spiTransfer, digitalRead, attachInterrupt, millis …
VirtualHal  ──►  VirtualSx126x (chip model)  ◄──►  ether task  ◄─UDP─►  the ether
        │                 │
   GPIO shim ◄── DIO1 ────┘   (level-triggered, fires loraRadioIsr)
```

### 2.1 Why the bus and not a chip class

The X-macro table in `lora_radio.h` makes a sixteenth chip look like the
natural seam. It is not, because the driver does not stay on the
`PhysicalLayer` surface. It casts to the concrete class and reaches under it:

- `standbyXOSC` and `SetRxTxFallbackMode` through `mod->SPIwriteStream`
  (`radioHoldOsc`), the `0x8B5` sensitivity register through `SPIsetRegValue`,
  `calibrate`, `calibrateImage`, `sleep(retain)`, `standby(mode, wakeup)` in
  the front-end recalibration (`radioAgcReset`);
- `setDio2AsRfSwitch`, `setCurrentLimit`, `setRxBoostedGainMode`,
  `implicitHeader`, `explicitHeader`, the per-family `startReceive` timeout
  constants and the chip's own IRQ register bits through `getIrqMapped`;
- the `SX126x::getRSSI(false)` channel-level read that carrier sense lives on.

A chip class would need a sixteenth family added to nine dispatch switches, and
every SX126x-only path above would be skipped on the host rather than run. The
receiver logic those paths carry — the oscillator hold across a chain, the
latched preamble bit `radioRxInProgress` reads, the fact that a `startReceive`
begins with a standby that throws away a reception in progress — is exactly
what the simulation exists to exercise. Putting the seam under RadioLib means
the driver's own semantics come for free and stay in lockstep with the pinned
version (7.7.1): the chip model implements the datasheet, and RadioLib's
reading of the datasheet is the same code that ships.

The cost is emulating a command set rather than a method set: 37 opcodes are
issued by RadioLib's SX126x driver, of which about 30 are on the LoRa path, and
some 14 registers. That is a state machine of a few hundred lines, and the
datasheet is its specification.

### 2.2 The chip model

`VirtualSx126x` is one object per radio slot, driven by two things: SPI
transactions from the HAL, and timestamped events from the ether task. Its
state is the SX1262's:

- **Mode**: `SLEEP` (cold or warm), `STDBY_RC`, `STDBY_XOSC`, `FS`, `TX`,
  `RX`; plus `ready_at`, the virtual time at which the pending transition
  completes. Until then the chip is in the new mode for the driver and deaf
  for the ether.
- **Parameters**, each written by its command and read back where the
  datasheet allows: packet type, RF frequency (the raw 32-bit word,
  converted with the datasheet's 32 MHz / 2^25 step), modulation params
  (SF, BW code, CR, LDRO), packet params (preamble, header type, payload
  length, CRC, IQ), PA config and TX params (power, ramp code), DIO IRQ
  params (IRQ mask, DIO1/2/3 masks), buffer base addresses, fallback mode,
  TCXO control and its programmed delay, DIO2 RF-switch flag, regulator mode.
- **Registers**: a sparse 16-bit-addressed byte map. Reads and writes pass
  through for every address, with datasheet reset values for the ones
  RadioLib read-modify-writes (sensitivity config, TX clamp, IQ config, RTC
  control, event mask, RX gain, OCP). Four addresses carry meaning: the
  version string (answers with the string RadioLib's SX1262 class expects,
  which is what `findChip` and therefore `begin()` succeed on), and the two
  sync-word bytes, decoded from the nibble-expanded form back to the 8-bit
  word the ether matches on.
- **Data buffer**: 256 bytes, with the TX and RX base addresses and the RX
  start pointer that `GetRxBufferStatus` reports.
- **IRQ status**: the 16-bit register. DIO1 is high while any bit in the DIO1
  mask is set and drops when `ClearIrqStatus` clears the last one — the
  level-triggered line the driver's re-arm paths and its raised-line backstop
  (`radioIrqLinePending`) assume.
- **Packet status** of the last reception: RSSI, SNR and signal RSSI in the
  chip's encodings; **device errors**; **RSSI instant** as computed below.

The **status byte** every transaction returns carries the mode and the command
status the datasheet specifies, so `SPIparseStatus` runs for real: a command
the datasheet forbids in the current mode answers `CMD_INVALID`, and the
driver's `SPI_CMD_*` branches are reachable. Anything the driver can hit on
hardware has a path to being hit here.

**Transitions charge datasheet time.** The interesting numbers from the SX1262
datasheet's timing tables, charged as `ready_at`:

| Transition | Charged | Why it matters |
|---|---|---|
| `STDBY_RC` → `STDBY_XOSC`/`FS`/`RX`/`TX`, crystal | TS_OSC 150 µs | every start of receive after a standby |
| the same, TCXO programmed | the delay in `SetDIO3AsTCXOCtrl` (RadioLib programs 5 ms) | the whole reason `radioHoldOsc` exists; unheld, every transmit in a chain waits it out |
| `STDBY_XOSC` → `FS` | TS_FS 40 µs | PLL lock on every retune |
| `FS` → `TX` | the PA ramp from `SetTxParams`, 10 µs to 3.4 ms | the transmit's real start on the air |
| `FS` → `RX` | TS_FS | the receiver's real opening |
| `TX` done → fallback | 0 | the fallback mode decides the next cost |
| `SLEEP` → `STDBY_RC` on the NSS wake | TS_OSC + 500 µs | the wake pulse RadioLib waits a rounded-up tick for |
| `Calibrate` (all blocks) | 3.5 ms | the front-end recalibration is a deaf window |

A frame whose first symbol lands before `ready_at` is not received, and the
ether records why (§4.4). This is the mechanism by which a wrong turnaround
constant becomes deterministic frame loss.

**BUSY never blocks the driver.** RadioLib waits for the BUSY line before and
after every command and spins on it after `SetTx`. On hardware that wait is
microseconds; in virtual time a spinning task would stop the clock. So the
virtual HAL's BUSY reads low always, and the datasheet's cost is carried on the
chip's own timeline (`ready_at`) rather than on the driver's. The driver runs a
few hundred microseconds ahead of where it would on silicon, which is inside
the scheduling jitter the protocol already allows for; the deafness is charged
where it matters, at the antenna. What this does not exercise is listed in §2.4.

**Receive.** The chip decodes nothing. The ether tells it that a frame is
arriving and, later, how it ended (§4). On `rx_begin` the model schedules the
chip's IRQ bits on its own clock, at the instants the sender's timeline
states: `PREAMBLE_DETECTED` a few symbols in, `SYNC_WORD_VALID` and
`HEADER_VALID` when the header has flown, `RX_DONE` (with `CRC_ERR` where the
verdict says so) or `HEADER_ERR` at the end. It stays in `RX` throughout,
as the real part does in continuous receive, and copies the payload into the
buffer at the RX base so `GetRxBufferStatus` and `ReadBuffer` find it. Leaving
`RX` before the end — a `SetStandby`, a `SetTx` — abandons the reception:
nothing further is raised, and the bits already set stay latched until cleared,
which is the stale-preamble situation `radioRxInProgress` has deadlines for.
A `signal` from the ether (a frame this receiver cannot lock onto) raises
nothing and only enters the RSSI computation.

**Transmit.** `SetTx` computes the frame's timeline from the chip's own
parameters — the one copy of the time-on-air formula, `lora_toa.h`, is compiled
into the firmware already — and sends the ether a `tx` carrying the start
(after the transition), preamble end, header end and frame end, the modulation
and packet parameters, the sync word, the power as programmed (clamped by the
PA configuration's ceiling) and the payload bytes. `TX_DONE` is raised on the
chip's own clock at the frame end; the ether is not consulted. The mode then
falls back as `SetRxTxFallbackMode` says.

**Reported levels.** `GetRssiInst` answers with the current level at the
antenna as the ether has described it — the station's noise floor plus every
overlapping signal, weighted by bandwidth overlap — in the chip's `-x/2`
encoding, and answers `0xFF` when the chip is not in `RX`, because the real
part does, and the carrier-sense code has a guard for exactly that reading.
Packet RSSI and SNR are what the ether reported in `rx_end`. The mapping from
true level to what the chip *reports* on a quiet channel is a calibration knob
(§8), not physics.

### 2.3 The host HAL, the GPIO shim and the ether task

**`VirtualHal : RadioLibHal`** replaces `EspIdfHal` on the host. `spiTransfer`
hands the outgoing bytes to the chip model and takes the reply bytes back;
`digitalRead` of BUSY is 0, of DIO1 is the chip's line; `digitalWrite` of RST
resets the model; `attachInterrupt` registers the callback with the GPIO
shim; `millis`/`micros` read the virtual clock through `esp_timer_get_time`;
`delay` is `vTaskDelay`, rounding up as the hardware HAL does. `ready()` is
true. The `lora.cpp` construction site is the one place that changes: the
per-radio `hal` becomes a `RadioLibHal*` and the host build news a
`VirtualHal` there.

**The GPIO shim** is what makes the interrupt path the same as hardware. The
`driver` component is absent on the Linux target, and iface-lora uses
`gpio_get_level`, `gpio_intr_enable` and the HAL trampoline's
`gpio_ll_intr_disable`, while `pmGpioWakeEnable` and the front-end code use a
few more. The shim is a pin table with a level, an enabled flag and a handler,
and one rule: **a level-triggered pin whose interrupt is enabled while its
level is high fires immediately.** That is the property INTERNALS §3 of
iface-lora rests on — the trampoline disables the interrupt, the task drains
and re-enables it, and a line still high re-fires — and it is what turns a
completed frame behind a disabled interrupt into a serviced one rather than a
hung task. The shim lives with the host board straddle (§11) and is the only
`driver` surface the host build provides.

**The ether task** is one FreeRTOS task per station process. It blocks on a
task notification, and when woken drains the UDP socket with non-blocking
reads, parses each datagram, and applies it to the addressed slot's chip
model under a critical section. Chip events that are due at a later virtual
time — an IRQ bit at a header instant, `TX_DONE` at a frame end, `ready_at`
passing — are `esp_timer` one-shots, which on the host is the virtual clock by
construction (§3). When a masked IRQ bit sets, the model raises DIO1 through
the shim, which invokes the registered callback: `loraRadioIsr`, unchanged,
doing its `vTaskNotifyGiveFromISR`.

The task does not own the socket's blocking wait, because of the **host I/O
rule** every host-only component obeys:

> No FreeRTOS task blocks in a host system call. One native pthread per
> process, created outside FreeRTOS, does all blocking I/O: a single `poll()`
> over the ether's UDP socket and every TCP socket the net backend owns. When
> a descriptor is ready it raises a real-time signal. The signal handler is
> the interrupt service routine: it makes one `FromISR` call — a notification
> to the ether task, or a queue send for the socket's owner — and yields from
> ISR, exactly as the port's own `SIGALRM` handler calls `xTaskIncrementTick`.
> The native thread calls no FreeRTOS primitive, and it is the only thread in
> the process that handles `EINTR`.

The rule exists because the IDF Linux port only knows a task is blocked when
it blocked on a FreeRTOS primitive. A task sitting in `recvfrom` or `select`
is, to the simulated scheduler, the running task: a high-priority one holds
the CPU across ticks and starves everything below it, a low-priority one is
reached only when nothing else is ready and then adds a tick of latency per
switch, and with any such task present the idle task never runs, which is the
moment virtual time needs in order to emit `wait` (§3). Espressif documents
the limitation; the rule is its consequence. It also confines every host
syscall that the tick signal can interrupt to one thread written for it.

The same port property has a second consequence: the tick handler can land
inside a libc call that is not async-signal-safe and switch to a task that
makes the same call, which deadlocks on stdio's lock. The host log sink
therefore assembles the line and emits it with `write(2)`, never `fwrite`
(§11).

The port ships one legal shortcut, and the proof of concept (§17) is built on
it: when lwIP is off, IDF interposes `select()` for every FreeRTOS task
(`components/freertos/esp_additions/FreeRTOSSimulator_wrappers.c`). The
interposed call polls the descriptors with a zero timeout and then blocks in
`vTaskDelay` for at most ten ticks, so the scheduler sees a blocked task and
nothing starves. What it costs is a tick of latency per socket event and the
idle moment: a task cycling through `vTaskDelay` is never idle, so virtual
time's `wait` cannot be derived from it. The native thread and the signal are
therefore the upgrade that virtual time requires, not a precondition for
running in real time.

### 2.4 Not modelled in the radio

- BUSY timing, so a driver path that mishandles the wake pulse after `SetSleep`
  (iface-lora INTERNALS §3) cannot be caught here.
- The GFSK, BPSK and LR-FHSS modems, CAD, duty-cycled receive and the RX
  timeout: accepted and ignored, or refused as `CMD_INVALID`. The driver uses
  continuous receive only.
- Frequency error, the random-number register, spectral scan.
- Any other family. One chip is emulated because one chip is the driver's
  richest path; SX127x's absent preamble IRQ or the LR2021's FIFO would be a
  second model, and are not worth one until a test wants them.

### 2.5 Selection and files

The host board straddle selects `LORA0_RADIO_SX1262` with `LORA_COUNT=1` and
placeholder pin numbers; iface-lora's Kconfig, X-macro and dispatch are
untouched. The host-only sources — `VirtualHal`, `VirtualSx126x`, the ether
task — sit in iface-lora under a directory gated by
`if(IDF_TARGET STREQUAL linux)` beside the gating-out of `esp_idf_hal.cpp`,
because they are the radio's host backend the way `esp_idf_hal.cpp` is its
chip backend. The GPIO shim is the board straddle's, because it is not about
the radio.

## 3. Time

Two modes, and the ether owns the clock in both.

**Virtual time (default).** Stations block on "advance me to T"; the ether
releases them in order. Fully deterministic and reproducible from a seed, and it
can run far faster than real time — which is what makes a thousand-seed overnight
sweep possible.

**Real time.** The ether tracks wall clock. Slower, non-deterministic, but lets
unmodified external processes join — notably the Reticulum reference
implementation in Python.

Determinism is the point of the exercise: a six-node race that cannot be
reproduced cannot be debugged. So virtual time is where this ends up, and real
time is the switch you flip when you want Python in the room.

**Real time is built first.** Two firmware processes, one ether, wall clock,
no changes to the FreeRTOS tick. That exercises the radio (§2), the ether
(§4, §9), the protocol and the whole host port (§11) and yields a working
test bed before the hard part below is touched. Virtual time is then an
upgrade to a thing that already runs, and the record from real-time runs
(§4.4) says whether reproducibility is the problem actually being had: if
contention experiments repeat well enough at wall clock across a few reruns,
virtual time stays on the shelf. It is the part of this plan most likely to
consume weeks and to be subtly wrong, and it is not attempted until there is
something to compare it against.

**How the host build gets a virtual clock.** Nobody has done this on the IDF
Linux port; the seams are small and all under our control:

- The FreeRTOS Linux port ticks from `setitimer(ITIMER_REAL)` raising `SIGALRM`
  into a handler that calls `xTaskIncrementTick` once. A linker `--wrap` of
  `setitimer` that never arms the timer, plus the ether coordinator raising
  `SIGALRM` per tick, turns the tick into "advance to T". spangap-core already
  uses `--wrap` for the heap-tracking fix, so the mechanism exists in the build.
- On IDF 5.5.4 the `esp_timer` component is header-only on the Linux target.
  We must supply the implementation anyway, so `esp_timer_get_time()` — and
  with it `millis()` in `compat.h` — is the virtual clock by construction.
- Wall-clock calls are the leak to plug: `time()` in 29 files, `gettimeofday`
  in 5, `localtime` in 8, `settimeofday` in 1. Same `--wrap` treatment, backed
  by the ether's clock.
- Reference design: Zephyr's native_sim with BabbleSim. Device code runs in
  zero simulated time; a central Phy process withholds replies until simulated
  time reaches the event; idle devices send "wait until T" so the Phy can
  advance. The ether is that Phy.
- "Nothing runnable before T" is the idle task running with the kernel's next
  wake time in hand, which is what FreeRTOS's tickless-idle hook hands out.
  It is only reachable if no task is a phantom of the scheduler's — the host
  I/O rule in §2.3 is what makes the idle task reachable at all.

A virtual-time run is nearly serial whatever its station count: the ether
releases stations only inside the lookahead window, so one run occupies one
or two cores. More cores buy more runs side by side with different seeds,
not a faster run.

IDF 6.x replaces the signal tick with a cooperative scheduler thread. The
mechanism above targets the pinned 5.5.4; a version move revisits it.

## 4. The ether

The ether is one process that holds every station's position, every radio's
receiver state as a timeline, and every transmission in flight. A station
tells it what its chip is doing; it tells the station what reaches the
antenna. The chip decodes nothing and the ether emulates no chip: physics on
one side of the socket, silicon on the other.

```
station S (chip model)                     ether                      station R (chip model)
   │ hello {sid, radios}                     │                                 │
   │────────────────────────────────────────▶│  places S at its scenario position
   │ ◀────────────────────────────────────── │ welcome {t0, seed, mode}        │
   │ state {t, idx, mode, ready_at, freq,    │                                 │
   │        bw, sf, cr, sync, hdr, crc, pre} │                                 │
   │────────────────────────────────────────▶│  every command that moves the receiver
   │ tx {t, idx, id, t0, t_pre, t_hdr, t_end,│                                 │
   │     freq, bw, sf, cr, sync, hdr, crc,   │                                 │
   │     pre, power_dbm, payload}            │                                 │
   │────────────────────────────────────────▶│  level at every R in range; R's state at t0
   │                                         │ rx_begin {t0, id, t_pre, t_hdr, t_end, level}
   │                                         │────────────────────────────────▶│  R locks: IRQ bits scheduled
   │                                         │ signal {t0, id, t_end, freq, bw, level}
   │                                         │────────────────────────────────▶│  R cannot lock: RSSI only
   │                                         │ rx_end {t, id, verdict, payload, rssi, snr}
   │                                         │────────────────────────────────▶│  at t_end (or t_hdr for a header error)
   │                                         │ rx_abort {t, id, cause}         │
   │                                         │────────────────────────────────▶│  a stronger frame took the receiver
   │ wait {t, until}          (virtual time) │                                 │
   │────────────────────────────────────────▶│                                 │
   │ ◀────────────────────────────────────── │ advance {t}                     │
```

Every message carries the sender's virtual time `t`; the ether is a
conservative discrete-event simulator over them (§3, §9). A station's `tx` is
known to the ether before any other station is advanced past its `t0`, so
`rx_begin` always arrives before the receiver's clock reaches the frame.

### 4.1 What the ether must model

In priority order. Note that propagation is *not* first: it is the easy part and
the least decision-relevant.

**1. Capture effect.** Two overlapping frames: the stronger wins if it is roughly
6 dB above the other *and* arrived within the weaker's preamble; otherwise both
are lost. This is what actually determines LoRa behaviour under contention. Get
it wrong and carrier sense looks either much better or much worse than reality,
and every conclusion drawn downstream is worthless.

**2. Wrong sync word raises the noise floor but is not received.** This is the
precise mechanism SUPE's private channels depend on. Unmodelled, the central
premise of the protocol goes untested.

**3. Retuning costs time and you are deaf during it.** Charge the datasheet
numbers — TS_HOP 30 µs, TS_FS 40 µs, TS_OSC 150 µs, PA ramp 10 µs to 3.4 ms —
and drop anything that arrives mid-retune. Best value per line in the whole
thing.

**4. Occupancy and airtime.** Time on air from the real formula; a channel is
occupied for the frame's duration; a receiver must be tuned for the *whole*
frame to get it. That is what catches deafness-window bugs mechanically instead
of by inspection.

**5. Noise accumulation** from concurrent transmissions, which is what makes
clear-channel assessment meaningful.

**6. CRC errors.** Do not model bit errors. Inside the capture margin band, mark
the frame as a CRC error with some probability. Our code paths treat CRC errors
distinctly and need to see them.

**Explicitly not modelled at first:** multipath, fading, Doppler, antenna
patterns, temperature drift. Realistic, but not decision-relevant for anything
being decided.

**Path loss** itself: free space plus a configurable exponent, plus optional
per-pair fixed obstruction in dB. Positions in a config file. Static before
mobile.

### 4.2 Levels and noise

Everything below is in true dBm at the antenna; what a chip *reports* is the
chip model's business (§2.2).

- **Level at a receiver.** `L = P_tx + G_tx + G_rx − PL(d)`, with
  `PL(d) = FSPL(1 m, f) + 10·n·log10(d) + obstruction(S, R)`. `n` is the
  scenario's exponent (2 is free space; 2.7 is the suburban default), the
  obstruction a per-pair constant in dB, and `FSPL(1 m, f)` the free-space
  term at the carrier (31.2 dB at 868 MHz). Stations more than 30 dB below
  the receiver's noise are not told about the frame at all.
- **Noise.** Per station, `N = −174 + 10·log10(BW) + NF`, with `NF` a
  scenario constant (6 dB) — about −117 dBm at 125 kHz. Every overlapping
  signal the receiver is not locked onto adds to it, scaled by the fraction
  of the receiver's bandwidth it covers, and by a further `isf_rejection_db`
  (16 dB) when it is a LoRa frame at a different spreading factor on the same
  channel, since the spreading factors are nearly orthogonal.
- **Sensitivity** is a threshold on `SNR = L − N`, per spreading factor, from
  the datasheet: −2.5 dB at SF5, −5 at SF6, −7.5 at SF7, and 2.5 dB lower per
  step to −20 at SF12. Below it the receiver never detects a preamble. In the
  band from the threshold to `crc_margin_db` (3 dB) above it, the frame locks
  but ends as a CRC error with a probability that runs from 1 at the threshold
  to 0 at the top of the band, drawn from the seeded generator. Above the band
  it is clean unless something else happens to it.
- **Reported SNR** is `L − N` with `N` taken as the worst it was during the
  frame; reported RSSI is `L`.

### 4.3 The verdict

Evaluated per receiver `R` for every transmission `F` that reaches it, in this
order, and recorded with its cause (§4.4). "Matches" means the same carrier,
bandwidth and spreading factor as `R`'s receiver; the sync word is checked
separately because it governs locking and nothing else.

1. **Not in `RX` at `t0`** (standby, sleep, transmitting): `signal`.
   Cause `no_rx`, or `tx` when `R` was the one transmitting.
2. **In `RX` but `ready_at > t0`**: `signal`. Cause `settling`. This is the
   deafness window, and the one measurement the whole project was started for.
3. **Does not match** — different carrier, bandwidth or spreading factor:
   `signal` with the overlap-scaled level. Cause `off_channel` or `wrong_rate`.
4. **Matches, wrong sync word**: `signal` at full level. Cause `wrong_sync`.
   The receiver does not lock, but the frame is a same-modulation interferer
   for anything the receiver *is* locked onto, and enters rule 6 for that.
5. **Matches, below sensitivity**: `signal`. Cause `below_sensitivity`.
6. **`R` is already locked onto `F′`.** With `SIR = L(F′) − L(F)`:
   - `SIR ≥ capture_db` (6): `F′` is unaffected; `F` is `signal`, cause
     `captured` (it lost).
   - `SIR ≤ −capture_db` and `t0 < F′.t_pre`: the stronger frame arrived
     inside the weaker's preamble and takes the receiver. `F′` gets
     `rx_abort {captured}`; `F` proceeds to rule 7.
   - otherwise both are lost: `F′`'s verdict becomes `hdr` if `t0 < F′.t_hdr`
     (a `HEADER_ERR` at `F′.t_hdr`), else `crc` (a `CRC_ERR` at `F′.t_end`),
     cause `collided`; `F` is `signal`, cause `collided`.
7. **Lock.** `rx_begin` to `R` at `t0`. The verdict starts as `clean` or, in
   the margin band, as the seeded draw says. Later frames re-enter rule 6
   against it. At `t_end`, `rx_end` carries the final verdict, the payload
   (for `clean` and `crc`; the bytes of a CRC failure are the sender's bytes
   with the CRC flag, not corrupted bytes — the driver never reads them), and
   the levels.
8. **`R` leaves `RX` before `t_end`** — a `state` says so: the chip model has
   already abandoned the reception locally; the ether records cause
   `left_rx` and sends nothing further for that frame.

Nothing here knows what a Reticulum packet is. SUPE's private exchanges are
private by rule 4, its channels by rule 3, its deafness by rule 2, and its
contention by rule 6 — which is the list in §4.1 in the order it was written.

### 4.4 The record

Every event the ether processes is one line of the run's record, tab-separated
and ordered by virtual time: `state`, `tx`, `rx_begin`, `signal`, `rx_end`,
`rx_abort`, each with the station, slot, frame id and the fields above, and
for every frame at every receiver the **cause** it was or was not received:

```
delivered   clean · crc_margin
lost        no_rx · tx · settling · off_channel · wrong_rate · wrong_sync ·
            below_sensitivity · captured · collided · left_rx
```

The per-run summary aggregates these per station and per pair: frames sent,
airtime per channel, and a loss table by cause. A departure policy (§7) is
judged on that table; a retune constant is wrong when `settling` is non-zero
in a scenario where the protocol says it cannot be. The record is also the
input to the referee.

## 5. The ether as referee

The ether sees everything, so it can assert rather than merely report. The
assertions run over the record at the end of a run, per station and per
channel, from the scenario's channel plan:

- no station exceeded 100 s of transmission in any 3600 s window on any 500 kHz
  channel
- no station returned to a frequency inside the 100 ms minimum off-time
- no station exceeded the channel's radiated power cap
- every transmission was preceded by a clear-channel assessment within the
  declared dead time — visible to the ether as a `state` in `RX` on that
  channel for at least the sense window before the `tx`
- no transmission started while the transmitter was locked onto a frame
  (the receiver's own `rx_begin` without an `rx_end`), which is the
  transmit-over-a-reception fault `radioRxInProgress` exists to prevent

That turns regulatory compliance and the driver's medium-access invariants
from a code review into a test that fails, with the line of the record that
failed it.

## 6. The Python reference implementation as oracle

The Reticulum reference implementation joins a run as an ordinary station and
answers two questions we currently answer by reading Python:

- what the daemon does when an interface stops accepting outbound packets
  (the backpressure question — we can only say take-it-or-wait, never "take
  these and hold those")
- whether real Reticulum behaves the way our neighbour and identity inference
  assumes

No virtual RNode serial interface is needed for this. iface-lora's RNode
endpoint already accepts a stock `RNodeInterface` client over TCP port 7633, so
a Python node attaches to a firmware node's virtual LoRa and becomes the third
endpoint of the radio segment. Over IP, a Python node peers through iface-tcp,
which on the host build rides plain host sockets. Both are real-time mode (§3).

## 7. What this is for

The immediate consumer is the departure policy. SUPE deliberately puts that
decision in one pure function:

```
should_channel switch(peer_state, queue_state, channel_state) -> { no | now | wait_until(t) }
```

no side effects, no radio access, one call site. The traffic patterns to run it
against:

- **interactive ping-pong** — an rnsh session, which stalls for a delivery proof
  before sending more; the case where a naive hold timer is strictly harmful
- **bulk transfer** — a resource, receiver-driven, batches of parts
- **contended gateway** — many stations, one transport node, all traffic tagged
  with the same identity
- **absent peer** — how much is spent discovering nothing is there
- **mixed segment** — SUPE and non-SUPE nodes sharing the channel, where the
  question is whether we are a good neighbour

Committing to a policy now would be the mistake. Committing to where the policy
lives costs nothing, and it is already done.

## 8. Risks

**A simulator that is subtly wrong gives confidence rather than information.**
The mitigation is cheap: calibrate against the few things measurable on real
hardware — time on air (exactly), received signal strength at two known
distances, channel switch success rate — and keep those as regression checks on the ether
itself.

**Scope creep towards physical realism.** The list in §4 is ordered; the tail of
it is where the temptation lives and where the value stops.

**The host build is not the chip.** §12 lists what it cannot find. The
mitigation is §13: keep booting the real binary somewhere.

## 9. Transport and mechanics

**Transport.** UDP to a central ether, one message per datagram, JSON with the
payload in base64. It keeps the distributed case free, is readable in a packet
capture, and costs nothing at this scale: a fifty-station run is a few hundred
datagrams a second. Do not optimise to a Unix socket, a binary encoding or
shared memory until it is demonstrably slow.

**Messages.** Every message names its sender's station id, radio slot and
virtual time `t` in microseconds. From the station:

| Message | When | Carries |
|---|---|---|
| `hello` | process start | station id, the slots it has, the scenario name it expects |
| `state` | every command that changes mode, `ready_at` or a matching key | mode, `ready_at`, carrier, bandwidth, spreading factor, coding rate, sync word, header type, CRC, preamble length |
| `tx` | `SetTx` | frame id, `t0`, `t_pre`, `t_hdr`, `t_end`, the same keys, power in dBm, payload |
| `wait` | virtual time: the station has nothing runnable before `until` | `until` |

From the ether: `welcome` (start time, seed, mode), `rx_begin`, `signal`,
`rx_end`, `rx_abort` as in §4, and `advance` in virtual time. A station that
receives nothing it understands ignores it; a message for a slot that is not in
`RX` is applied to the RSSI picture and nothing else.

**Ordering.** In virtual time the ether processes messages in `t` order and
advances a station only to a time before which no message for it can still
arise — the lookahead is the smallest chip transition (§2.2), so a `tx` at
`t0` is always known before any receiver is released past `t0`. A station's
`wait` is its promise that it will send nothing stamped earlier than `until`.
In real time the ether stamps arrivals with its own clock and the same rules
run with whatever order the network gave; the record says which mode a run
used, and a virtual-time record is reproducible from its seed by construction.

**The scenario file** is YAML, one file per run, and is the whole input:

```yaml
seed: 1
time: virtual                # or real
path_loss: { exponent: 2.7, noise_figure_db: 6, capture_db: 6,
             crc_margin_db: 3, isf_rejection_db: 16 }
channel_plan: etsi-863-870   # what the referee holds stations to
stations:
  a: { pos: [0, 0],    gain_db: 2, firmware: hw-linux, radios: [{ chip: sx1262 }] }
  b: { pos: [800, 0],  gain_db: 2, firmware: hw-linux, radios: [{ chip: sx1262 }] }
  c: { pos: [400, 300], gain_db: 0, firmware: hw-linux, radios: [{ chip: sx1262 }],
       settings: { s.lora.0.SUPE.enabled: 0 } }
obstructions:
  - { between: [a, c], db: 20 }
traffic: []                  # the harness's business (§15); the ether only records
```

The harness reads the same file to spawn the stations (§15) with their
settings; the ether reads it for positions, gains, obstructions, the physics
constants and the channel plan the referee enforces. The chip model's timing
table (§2.2) is not in the file: it is the datasheet, and a run that wants
a slower chip is asking a different question.

**Implementation shape.** Python 3, one process, one asyncio UDP endpoint, an
event heap keyed by virtual time, a `Station` with a position and a per-slot
receiver timeline, the set of frames in flight per receiver, and one
`random.Random(seed)`. The record is written as it happens; the summary and
the referee run over it at the end. On the order of two thousand lines
including the referee, and no dependency beyond the standard library and
YAML. It is a separate program from the firmware and from the harness, in the
plans' sense of §11: the platform work there is a precondition, the ether is
not part of it.

Python because the harness is pytest, the scenario is data, the referee is a
table walk, and there is nothing in the pipeline (§4.3) that is more than a
few comparisons per frame per receiver. The one thing that must agree between
the ether and the firmware — a frame's timeline — is computed by the
transmitting chip model from `lora_toa.h` and *stated* in the `tx`, so the
ether holds no copy of the time-on-air formula and cannot drift from it.

## 10. The runtime: ESP-IDF's Linux host target

The firmware is built with `idf.py --preview set-target linux` and runs as a
native process: the real FreeRTOS kernel on pthreads (single simulated core,
priorities honoured, preemption via the signal tick), host libc, host sockets.
A node costs nothing when idle, so 30–50 per machine is comfortable and
hundreds are plausible. The only prior art with a socket-bridged radio,
Bramble, runs its firmware nodes this way on wall clock with a shim of a few
hundred lines and no IDF patches; its firmware is much smaller than ours.

What IDF 5.5.4 provides on that target, and what it does not:

| Works | Mock only or absent |
|---|---|
| freertos (real kernel), esp_event, esp_partition (flash as an mmap'd file), nvs_flash, mbedtls, esp-tls, esp_http_client/server, log, console, heap (caps ignored), esp_random via `getentropy` | driver (gpio/spi/i2c/uart), esp_timer (headers only), lwip (off; sockets are host sockets), esp_wifi, bt, esp_psram, esp_pm, sleep, usb/tinyusb, esp_lcd, app_update, vfs (eventfd only), esp_mac |

### 10.1 The machine and the containers

The runs happen in Docker Desktop on a Mac — a laptop for development, a
Mac Studio (M3, 128 GB) for anything sized. Docker Desktop runs a native
aarch64 Linux VM, so station processes run at full speed and nothing in the
design is specific to the Mac; the same image runs on any Linux box or cloud
VM. What the machine does and does not buy:

- **Cores buy runs side by side**, not a faster run (§3). A single
  virtual-time run uses one or two cores at any station count; twenty runs
  with different seeds use twenty.
- **Memory is not a constraint.** A host-target station is a pthread-per-task
  process with an unbounded malloc heap and an mmap'd flash file; budget tens
  of MB per station. Fifty stations fit on the laptop.
- **Real time needs a quiet VM.** In real-time mode every station keeps its
  tick timer armed and expects it delivered on time; LoRa's timescales are
  milliseconds, so hypervisor jitter is tolerable, but a build running in the
  same VM shows up as tick drift. Real-time runs do not share the VM with a
  build.
- **Nothing a run writes goes on a bind mount.** Virtiofs is slow and has
  its own file semantics (the build-system README documents the surprises).
  Per-station state, the record and every output live on the container's own
  filesystem or a tmpfs.

**One image, sibling containers, one dependency direction.** The build-env
image already holds IDF, host gcc, QEMU, Python and reticulum; the ether adds
two thousand lines of Python and PyYAML. What must not happen is one container
building, serving flashmon, relaying device ports and hosting a fifty-process
run at once: a real-time run beside a build is non-reproducible for reasons
that have nothing to do with the protocol. So:

- The **build container** is unchanged. It produces the `hw-linux` ELF into
  the builds tree by the same make-builds path that produces every board's
  flashable image.
- A **sim container** is a sibling from the same image, one per run, with the
  build output mounted read-only, no workspace mount, and its state on its
  own filesystem. A batch run publishes no ports; a testbed run (§16)
  publishes one.
- **A run depends on three files** — the ELF, the ether, the scenario — and
  an ether address. It never depends on `IDF_PATH` or on a workspace path.
  This is the invariant to hold from the first two-process run onward, because
  it is what lets a run container later shrink to Python plus the ELF and run
  anywhere.

Running natively on macOS instead of in the VM is rejected: the clock, the
wall-clock leaks and the heap fix all rest on GNU ld's `--wrap`, which Apple's
linker lacks, and the VM costs nothing in speed to trade for it.

## 11. The porting bill

Everything below is host-only code behind `if(IDF_TARGET STREQUAL linux)`
in the affected straddle's CMakeLists, plus one board straddle.

**`hw-linux` board straddle.** Kconfig for one virtual radio, no pins, a
`detect_hw()` that always matches, the per-node identity in place of the MAC.

**spangap-net host backend — the largest item.** `net.cpp` is 3,100 lines but
only about 40 call sites touch `esp_wifi`, `esp_netif`, mDNS and SNTP, and
consumers only see its ITS ports (`NET_PORT_TCP_DIAL`, `NET_PORT_REG_PORT`, the
UDP surface). The backend answers those over host sockets and reports the host
interface as "upstream up". It is not a thin shim over libc, because of the
host I/O rule (§2.3): it is a socket table with one FreeRTOS queue per
socket, fed by the process's single native I/O thread through the signal
handler, and its sockets are non-blocking. Consumers block on the queues, so
iface-tcp, iface-auto, sshd, the TCP CLI, the web server, rnsh and WebRTC
work unchanged at the ITS surface. mDNS, SNTP, WireGuard (`wg` uses lwIP
netif internals), UPnP, DuckDNS and ACME are built out. The straddles that
include `lwip/sockets.h` and call `recv` themselves (acme, iface-auto,
webrtc, upnp) go through the socket table or stay built out; none of them
gets a blocking host socket.

Every station binds its listening ports on a loopback address of its own
(`127.0.0.k`, assigned by the launcher), so each keeps the canonical port
numbers the firmware and its web UI assume, and fifty stations do not
collide.

**Storage.** No LittleFS and no IDF VFS on the host, so `/state` and `/fixed`
are per-node host directories. The roots are already the constants `FS_STATE`,
`FS_FIXED`, `FS_SDCARD` in `fs.h`, with literal paths in about 11 files; a
per-node prefix is the whole shim. Not exercised on the host: runtime state
partition growth, safe-mode backup and restore, factory reset by partition,
the updater.

**Radio.** §2: the virtual HAL, the SX1262 model and the ether task in
iface-lora's host directory; the GPIO shim with level-trigger semantics in the
board straddle, which is the only piece of `driver` the host build has. The
one edit to shipping code is the HAL's construction site in `lora.cpp`.

**Clock.** Our `esp_timer` implementation on the virtual clock, the `setitimer`
wrap, the wall-clock wraps (§3).

**Console and log sink.** Log output already goes to stdout, but the host
sink emits each assembled line with `write(2)`: the current `fwrite` from any
task deadlocks when the tick signal lands inside stdio (§2.3). The CLI's
serial layer is USB Serial/JTAG or UART and needs a stdio backend; tests can
equally use ssh or the TCP CLI, which arrive with the net backend.

**Memory.** `heap_caps_*` ignores capability flags and reports free memory as
`UINT32_MAX`, so `gp_alloc`/`dram_alloc` work and memory telemetry is
meaningless. Ignore `sys.mem.*` on the host.

**Build system.** Every straddle pins `targets: [esp32s3]` in
`idf_component.yml` and needs `linux` added. tinyusb, joltwallet/littlefs, the
esp_lcd panel and touch drivers and espressif/mdns must be gated out of the
host build. `spangap-inside` needs a notion of target so `hw-linux` sets
`CONFIG_IDF_TARGET="linux"`. The Linux target is a `--preview` target that
breaks between IDF releases; the pinned 5.5.4 matters.

**Width.** The build container is 64-bit ARM, so pointer and `long` widths
differ from the chip. Wire-format code with width assumptions surfaces as
host-only failures — a nuisance and a free audit.

Effort: the net backend, the clock and its coordinator, the radio, the FS
prefix, the console, the board straddle and CMake gating across every
straddle add up to weeks of platform work before the ether itself, which is a
separate project.

## 12. Doors closed by not emulating the chip

Closed on the host route, open under QEMU (§13):

- Memory behaviour: the internal DRAM versus PSRAM split, fragmentation, heap
  exhaustion, stack overflows, alignment and DMA placement, IRAM and
  cache-disabled ISR rules.
- True dual-core parallelism. The Linux port is single-core; races between
  cores never happen.
- Preemption fidelity. Priorities and preemption exist but through signals,
  so starvation and priority-inversion bugs look different.
- The binary itself: xtensa gcc, 32-bit pointers, linker placement, ROM
  functions, hardware crypto.
- Boot chain, partitions, OTA, safe mode, factory reset, watchdogs, panics,
  backtraces, core dumps, reset reasons. `esp_restart` is process exit.

Closed on both routes, so not a reason to prefer QEMU:

- WiFi, BLE and ESP-NOW. QEMU's ESP32-S3 has no radio and no ADC, and the WiFi
  PHY blob spins on the ADC.
- Sleep and power management, including cron's deep-sleep awareness and GPIO
  wake.
- Every I2C and SPI peripheral: GPS, IMU, RTC, SD card, USB console and CDC,
  battery ADC.
- Timing accuracy. QEMU is not cycle-accurate; CPU cost against airtime budgets
  is a hardware-only measurement.
- RF calibration, TCXO and front-end paths.

A second fake radio for BLE is possible later on the same pattern; not a
priority.

## 13. QEMU as the standing smoke target

Espressif's QEMU (`esp_develop_9.2.2`, installed in the build container as
`qemu-system-xtensa`) boots the real Reticulous image. Verified on
2026-09-17 with the meshnology-w12 build rebuilt for a UART0 console:

```
qemu-system-xtensa -M esp32s3 -m 8M \
  -global driver=ssi_psram,property=is_octal,value=true \
  -drive file=flash.bin,if=mtd,format=raw -nic user,model=open_eth \
  -nographic -serial mon:stdio
```

The app finds 8 MB octal PSRAM and the whole platform comes up — storage,
logging, framed RPC, web, rnsd, sshd, LXMF stores, `spangap ready`. What stops
it, and what a `hw-qemu` board straddle avoids:

- No SAR ADC: IDF's ADC calibration constructor and any board battery sampler
  spin forever, and the WiFi PHY blob spins in `ram_read_sar2_code`. The board
  straddle links no ADC user; net must not start WiFi (the OpenCores Ethernet
  NIC is what QEMU offers).
- No BT controller: `nimble_port_init` asserts; BLE is built out.
- No USB Serial/JTAG output: console on UART0, `CONFIG_ESP_CONSOLE_UART_DEFAULT`.
- No I2C: OLED probes time out harmlessly.
- No general-purpose SPI: RadioLib cannot reach an SX1262 model without a QEMU
  fork (MeshBench and HeyVern/Bramble each carry one, bridged over a socket to
  their simulator). The virtual radio over UDP through the Ethernet NIC needs
  no fork.
- LittleFS fails to format the state partition while NVS writes fine; root
  cause not found.

Cost: about one host core and ~100 MB per instance; four ran side by side with
guest time keeping pace; published experience says 8–10 per 12-core box. No
virtual time, non-reproducible. Its value is the smoke question — does the
actual binary still boot and run the platform — asked regularly while the
simulation runs natively.

## 14. Displays

Capture the framebuffer; do not emulate a panel. The seam is one file,
`lcd_lvgl.cpp`, where LVGL's flush callback hands strips to `esp_lcd`.
`lcdmirror` already taps that callback into a full RGB565 framebuffer with a
damage box; that sink is reused on the host without its WebRTC half. `tinylcd`
runs u8g2 in full-buffer mode, so its 1-bit framebuffer is read directly.

LVGL 9's test mode (`LV_USE_TEST`) adds an in-memory display, deterministic
time advance (`lv_test_fast_forward`), injected keypad and pointer events, and
PNG screenshot comparison that writes an error image on mismatch. It needs no
SDL and is what LVGL's own CI uses. The lvgl component has no target
restriction; only the `esp_lcd_*` panel and touch deps are gated out. Board
input drivers (keyboard, trackball, touch) become fake input devices.

## 15. The test harness

The pytest subprocess-per-peer model in reticulous INTERNALS §9 already fits:
a firmware node is a subprocess with a per-node directory, an ether address and
a node id, printing a readiness line; the Python reference peers are the same
kind of subprocess. Tests drive nodes through ssh or the TCP CLI, assert on
storage keys, logs, framebuffers and the ether's referee report.

## 16. The live testbed

A run is not only a closed machine driven by pytest. The same processes on
the same wire are also a testbed a person works in from browser tabs:
open a station's web UI, send a message by hand, read NetGraph's map of what
that station believes the network is, watch what the ether says actually
happened. Nothing inside a station changes for this — each is a whole
firmware process with its own web server, ssh and TCP CLI on host sockets —
so the whole feature is reachability and a launcher.

```
browser on the Mac ── https://a.sim.localhost:8443 ──► published port ──► proxy ──► 127.0.0.a:443  (station a's web UI)
                   ── https://ether.sim.localhost:8443 ────────────────────────────► the ether view
Reticulum client   ── TCP 7633 on 127.0.0.a (RNode endpoint) or iface-tcp peer ──► station a
```

- **Reachability.** Processes inside the VM are invisible to a browser on
  macOS except through published ports. The sim container publishes one. A
  proxy behind it routes on hostname to the station's own loopback address
  (§11), so every station keeps its canonical URL space and its websockets;
  browsers resolve any name under `.localhost` to loopback with no
  configuration. Offset ports per station, the crude alternative, break any
  UI that assumes its canonical port.
- **The ether view.** The ether serves a page of its own: stations on the
  plane, links shaded by path loss, frames in flight, the loss table by
  cause as it grows, and drag-to-move for a station, which updates its
  position at runtime. It is the ether's existing state and a websocket; a
  few hundred lines. It is also how a topology is made by hand instead of by
  editing YAML, and it makes mobility a free feature.
- **Sending by hand.** A station's own web UI; or a stock Reticulum client
  attached to a station's RNode endpoint or peered over iface-tcp, which is
  the same attachment §6 uses for the oracle.
- **The launcher.** `spangap sim <scenario>` starts a sim container from the
  build output, spawns the ether and the stations exactly as the harness
  does (§15), assigns loopback addresses, starts the proxy, and prints the
  URLs. pytest and the launcher share the spawning code; a testbed run is a
  harness run with a human where the assertions would be.
- **Real time, always.** A person cannot act in virtual time, so the testbed
  is the plan's real-time mode, and the mode that gets used most; virtual
  time stays the batch mode for reproducible experiments. A later upgrade,
  not in scope, is the virtual clock paced to wall clock, which would give a
  testbed run deterministic ordering with a person in the loop.
- **Every station has a console, two ways.** Its stdin and stdout are its
  serial console: the CLI's serial task reads a non-blocking stdin through
  the interposed `select()` (§2.3) and writes with `write(2)`, so a station
  started in a terminal, a tmux pane or under the launcher's `--console`
  flag takes commands exactly as a board on a cable does, first-run setup
  included. Its TCP CLI on port 8081 of its own loopback address is the
  second door, reached with `nc`, and it is what the launcher and the
  harness use. Bootstrapping two stations that see each other is therefore
  the same three answers the serial setup asks for, typed into each
  station's console, followed by the mesh commands.
- **flashmon is not the front door.** It owns a serial console per browser
  tab. If a per-station console tab is wanted later, it is a
  websocket-to-stdio bridge in the launcher.

## 17. Step 1: the proof of concept

Three stations and one ether, real time, on one machine, with a browser on
the Mac reaching a station's web UI. It proves the host port, the virtual
radio seam and the wire, and it is the testbed's first working form. It is
written so that an agent can execute it without reading the rest of this
plan; every hook point is named with its file and line as they stand.

**Done means:** the chip build still builds; `build/reticulous.elf` exists for
`hw-linux`; one station prints `spangap ready` and answers on its TCP CLI; a
browser on the macOS host loads a station's web UI; three stations exchange
announces over the virtual LoRa and a message sent by hand from station a's
web UI arrives at station b; the ether's record shows the frames.

### 17.1 Facts the work rests on

IDF v5.5.4 at `/opt/esp/idf`, host gcc 13, aarch64. These are verified in the
source and are not to be re-derived:

- **Target selection.** `IDF_TARGET` from the environment wins over every
  guess (`tools/cmake/targets.cmake`, `__target_init`). A defaults file
  naming `CONFIG_IDF_TARGET="esp32s3"` would be written into `sdkconfig` and
  then fail the mismatch check, so no defaults file may name the target.
  `--preview` is enforced only by `set-target`; a plain `idf.py build` with
  `IDF_TARGET=linux` exported does not need it.
- **Manifests.** Every `idf_component.yml` with `targets:` not listing
  `linux` is a hard failure (`idf_component_manager/dependencies.py:50-60`).
  Managed dependencies are gated per target with `rules: - if: "target !=
  linux"` (`idf_component_tools/manifest/models.py:70-110`).
- **REQUIRES.** A component whose CMakeLists returns before registering on
  linux is "not registered", and any REQUIRES on it is fatal
  (`tools/cmake/build.cmake:332-337`). Not registered on linux: `driver`,
  `esp_driver_spi`, `esp_pm`, `esp_wifi`, `esp_psram`, `sdmmc`,
  `app_update`, `bootloader_support`, `esp_adc`, `esp_driver_usb_serial_jtag`
  registers, so do `esp_driver_gpio` (empty), `fatfs`, `esp_driver_sdmmc`,
  `esp_driver_sdspi`, `esp_app_format`, `esp_http_client`, `json`,
  `esp_event`, `nvs_flash`, `esp_partition`, `spi_flash`, `mbedtls`,
  `esp_rom`, `esp_hw_support`, `vfs` (eventfd stubs only), `lwip` (empty,
  no headers). `esp_netif` registers but only its lwIP-free objects; nothing
  host-side may include its headers.
- **Component override.** A component directory found later in the search
  order replaces an IDF component of the same name
  (`tools/cmake/component.cmake:170-177`). The reticulous project already
  adds `staging/components/*/components` to `EXTRA_COMPONENT_DIRS`
  (`reticulous/esp-idf/CMakeLists.txt:8-22`), so a board straddle's
  `esp-idf/components/<name>/` overrides IDF's `<name>` when that board is
  staged. The mock components under `tools/mocks/` use exactly this, reading
  the original's include dir through `COMPONENT_OVERRIDEN_DIR`.
- **esp_timer** is headers only on linux (`components/esp_timer/CMakeLists.txt:3-4`).
  Every `esp_timer_*` call is an undefined symbol until we supply them.
- **FreeRTOS port.** One pthread per task, one running at a time, tick from
  `setitimer` raising `SIGALRM` at `CONFIG_FREERTOS_HZ` = 100 (the
  workspace's `sdkconfig` has it at 100). Every task stack must be at least
  16 KB plus 40 bytes (`config/linux/include/freertos/FreeRTOSConfig_arch.h:27`);
  a smaller stack fails `pthread_attr_setstack` and aborts. `main()` lives in
  `portable/linux/port_idf.c:93-140`, sets stdout unbuffered, and starts
  `app_main` on a task sized by `CONFIG_ESP_MAIN_TASK_STACK_SIZE`.
  `xTaskCreatePinnedToCoreWithCaps` exists (`esp_additions/idf_additions.c`).
- **select() is interposed** when lwIP is off (`esp_additions/FreeRTOSSimulator_wrappers.c`):
  zero-timeout poll, then `vTaskDelay` of the remaining time capped at ten
  ticks. `read`, `recv`, `accept` are not interposed: they must be
  non-blocking. `CONFIG_LWIP_ENABLE` defaults to n on linux and stays n.
- **Heap.** `heap_caps_*` ignore caps and wrap libc; free size reports
  `UINT32_MAX` (`components/heap/heap_caps_linux.c`).
- **Flash.** `esp_partition` mmaps `/tmp/idf-partition-XXXXXX`, sized from
  the built partition table, whose absolute build-dir path is compiled in
  (`components/esp_partition/partition_linux.c:202-221`). NVS works through
  it; `CONFIG_NVS_ENCRYPTION` must be off.
- **Process.** `esp_restart()` prints and `exit(0)`
  (`port/soc/linux/system_internal.c:12-16`). `esp_efuse_mac_get_default`,
  `bootloader_random_*`, `esp_littlefs_*`, `esp_ota_*`, `esp_flash_*`,
  `esp_vfs_*`, `usb_serial_jtag_*`, `gpio_*`, `spi_*`, `esp_pm_*`,
  `esp_sleep_*` have no implementation. `esp_random`/`esp_fill_random`
  exist (`esp_hw_support/port/linux/esp_random.c`). `esp_log_set_vprintf`
  works. `esp_rom_crc32_le` exists.
- **Kconfig symbols that only exist on the chip** (`CONFIG_SPIRAM*`,
  `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG`, `CONFIG_ESPTOOLPY_*`, WiFi buffers)
  are assigned in `sdkconfig.defaults.spangap` and become undefined on
  linux: `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG` is `depends on
  SOC_USB_SERIAL_JTAG_SUPPORTED`, so `cli.cpp`'s `#else` POSIX branches
  compile. The first configure confirms that kconfgen only warns on them.
- **RadioLib** 7.7.1 registers as an IDF component on any IDF target because
  `ESP_PLATFORM` is set for linux too (`tools/cmake/build.cmake:715`), and
  builds generic (`BuildOpt.h:149`). `SX126x::findChip` reads 16 bytes at
  register `0x0320` and compares the first six to the class's chip-type
  string, which for the SX1262 class is `"SX1261"`: the two parts are one
  silicon and report the same string (`SX126x.cpp:1479-1492`,
  `RADIOLIB_SX1262_CHIP_TYPE`). `Module::SPItransferStream` performs one
  `spiTransfer` per command between a CS low and a CS high, after spinning
  on `digitalRead(BUSY)` (`Module.cpp:362-391`).

### 17.2 The staged set and the build command

```
spangap build reticulous/reticulous --with spangap/hw-linux \
    --without reticulous/rnsh reticulous/iface-auto reticulous/iface-ble \
              reticulous/rnode-ble reticulous/nomad reticulous/maps spangap/viewer \
              spangap/acme spangap/duckdns spangap/sshd spangap/wg spangap/upnp
```

Staged: `spangap-core`, `spangap-net`, `spangap-web`, `rns` (with its
vendored `microreticulum`), `iface-lora`, `iface-tcp`, `lxmf`, `hw-linux`.
`additional_installs` entries are pruned silently when excluded
(`spangap-inside:491-506`); the last five exclusions are spangap-net's own
installs, none of which has a linux target. Work proceeds in the checkpoints
of §17.10, each of which stages a smaller set with `--no-net`, `--no-web` and
further `--without` entries.

A fresh station holds Reticulum until an admin password is set; over its TCP
CLI that is `auth passwd admin <pw>`, followed by the radio's region. This is
the first-run setup of §16, and every checkpoint from 3 on performs it per
station.

The state of the last chip build is `reticulous/reticulous --with
spangap/hw-meshnology-w12 --kconfig CONFIG_SPANGAP_USB_CDC=y`
(`.spangap-build`); that build must still succeed after every change below,
and it is rebuilt once at the end to prove it.

### 17.3 Build system

- **`spangap/build-system/schemas/straddle.schema.json`**: add an optional
  `target` string, enum `esp32s3` and `linux`, default `esp32s3`, next to
  `platforms` (line 68). Description: the IDF target this board straddle
  builds for.
- **`spangap/build-system/spangap-inside`**, `cmd_build` (3742): after the
  dependency closure (3832-3835) find the staged straddle whose repo name
  starts with `hw-` and read its `target`, default `esp32s3`. Put
  `IDF_TARGET` into `build_env` (3863) for every build, chip builds included.
  When the target is `linux`: prepend `--preview` to the idf.py argument
  list in `run_build_idf` (3119), skip the shrink-wrap and flasher-zip block
  after the build (3890-3903; `measure_build_sizes` reads chip artefacts), and
  skip `flash_size_configured` (3186-3218). Persist the invocation in
  `.spangap-build` as today.
- **`spangap-core/esp-idf/sdkconfig.defaults.spangap:314`**: delete
  `CONFIG_IDF_TARGET="esp32s3"`. The target now comes from the environment
  for every build.
- **`idf_component.yml`, add `- linux` under `targets:`** in:
  `spangap-core/esp-idf`, `spangap-core/esp-idf/components/spanfs`,
  `spangap-net/esp-idf`, `spangap-web/esp-idf`, `rns/esp-idf`,
  `rns/esp-idf/components/microreticulum` (line 27), `iface-lora/esp-idf`,
  `iface-tcp/esp-idf`, `lxmf/esp-idf`.
- **Managed dependencies gated off linux** with `rules: - if: "target !=
  linux"`: `joltwallet/littlefs`, `espressif/mdns`, `espressif/esp_tinyusb`
  in `spangap-core/esp-idf/idf_component.yml:10-17`; `espressif/mdns` in
  `spangap-net/esp-idf/idf_component.yml:17`; `espressif/esp_tinyusb` in
  `reticulous/esp-idf/main/idf_component.yml:24-25`. `jgromes/radiolib`
  stays.
- **`reticulous/esp-idf/main/CMakeLists.txt:34-38`**: `driver esp_event
  esp_netif esp_wifi lwip` become `esp_event` plus, when
  `IDF_TARGET STREQUAL linux` is false, the other four. The same edit in
  `hw-heltecv4/esp-idf/CMakeLists.txt` and `hw-meshnology-w12` is not
  needed; those boards are never staged on linux.
- **`spangap-core/esp-idf/project_include.cmake`**: in
  `spangap_browser_build` (149) guard `add_dependencies(flash …)` with
  `if(TARGET flash)`. In `spangap_create_factory_image` (40) keep the
  `spangap_data_merge` target on linux and skip `spanfs_create_partition_image`
  and both report scripts; `data_merged` in the build directory is the
  station's `/fixed` on the host (§17.5).
- **`spangap-core/esp-idf/cmake/bootstrap.cmake`** is unchanged: it reads
  `CONFIG_ESPTOOLPY_FLASHSIZE_*` textually and generates `partitions.csv`,
  which `partition_table` turns into the `.bin` that `esp_partition` on
  linux maps.

### 17.4 The `hw-linux` board straddle

Repository `hw-linux` at the workspace root, `name: spangap/hw-linux`,
`prefix: hwlinux`, `firmware: esp-idf`, `target: linux`, one service
`{ class: HwLinuxBoard, header: hwlinux.h }`, no `additional_installs`.

`kconfig:`

```
- CONFIG_LORA_COUNT=1
- CONFIG_LORA_SPI_HOST=2
- CONFIG_LORA_SCK_PIN=0
- CONFIG_LORA_MOSI_PIN=1
- CONFIG_LORA_MISO_PIN=2
- CONFIG_LORA0_CS_PIN=3
- CONFIG_LORA0_DIO1_PIN=4
- CONFIG_LORA0_BUSY_PIN=5
- CONFIG_LORA0_RST_PIN=6
- CONFIG_LORA0_RADIO_SX1262=y
- CONFIG_LORA0_TCXO_MV=0
- CONFIG_ESPTOOLPY_FLASHSIZE_16MB=y
- CONFIG_SPANGAP_MAX_FIRMWARE_KB=6144
- CONFIG_ESP_MAIN_TASK_STACK_SIZE=65536
- CONFIG_FREERTOS_TIMER_TASK_STACK_DEPTH=4096
- CONFIG_LWIP_ENABLE=n
- # CONFIG_SPIRAM is not set
- # CONFIG_SPANGAP_USB_CDC is not set
- # CONFIG_NVS_ENCRYPTION is not set
```

Pin numbers are indices into the GPIO shim's table, nothing else; `-1`
would cast to RadioLib's "not connected". `esp-idf/idf_component.yml` pins
`targets: [linux]`. `esp-idf/CMakeLists.txt` registers `src/detect.cpp`,
`src/hwlinux.cpp`, `${SPANGAP_CONDITIONAL_SRCS}`, `INCLUDE_DIRS include`,
`REQUIRES ${SPANGAP_REQUIRES} esp_event`. The component name starts with
`hw-`, which is what keeps `detect_hw` linked
(`spangap-core/esp-idf/CMakeLists.txt:216-222`).

- **`src/detect.cpp`**: `extern "C" const char* detect_hw(void) { return
  "hw-linux"; }`. It must not include `detect_probe.h`.
- **`src/hwlinux.cpp`** and **`include/hwlinux.h`**: the station's
  environment, read once: `SPANGAP_NODE_ID` (a small integer, the station's
  identity), `SPANGAP_NODE_DIR` (its state directory), `SPANGAP_BIND_ADDR`
  (its loopback address, `127.0.0.1<id>`), `SPANGAP_ETHER` (`host:port`),
  `SPANGAP_FIXED_DIR` (the build's `data_merged`). Accessors
  `hwLinuxNodeId()`, `hwLinuxBindAddr()`, `hwLinuxEtherAddr()`.
  `HwLinuxBoard::onStart()` runs before `spangapInit()`
  (`spangap-core/esp-idf/src/service.cpp:34-42`): it creates
  `SPANGAP_NODE_DIR` and `state/` under it, replaces `fixed` in it with a
  symlink to `SPANGAP_FIXED_DIR`, and `chdir()`s into it, so the relative
  roots of §17.5 resolve. It also defines the identity the platform reads:
  `extern "C" esp_err_t esp_efuse_mac_get_default(uint8_t* mac)` filling a
  locally administered MAC whose last byte is the node id. Call sites:
  `spangap_init.cpp:106,581`, `net.cpp:1243,1410,3018`.
- **`esp-idf/components/driver/`**: a component named `driver` that
  overrides IDF's. `include/driver/gpio.h` declares the subset iface-lora
  uses: `gpio_num_t`, `GPIO_NUM_MAX` (64), `gpio_config_t`, `gpio_mode_t`,
  `gpio_int_type_t` with `GPIO_INTR_DISABLE/POSEDGE/NEGEDGE/HIGH_LEVEL/LOW_LEVEL`,
  `GPIO_PULLUP_DISABLE`, `GPIO_PULLDOWN_DISABLE`, `gpio_config`,
  `gpio_set_level`, `gpio_get_level`, `gpio_set_intr_type`,
  `gpio_isr_handler_add`, `gpio_isr_handler_remove`, `gpio_intr_enable`,
  `gpio_intr_disable`, `gpio_sleep_sel_dis`, `gpio_sleep_sel_en`,
  `gpio_install_isr_service`, `gpio_wakeup_enable`, `gpio_wakeup_disable`,
  plus `gpio_shim_set_level(pin, level)` for the chip model to drive its
  DIO1. `include/driver/spi_master.h` declares only `spi_host_device_t`
  (an enum with `SPI2_HOST`) and `spi_device_handle_t` (a `void*`), so that
  `iface-lora/esp-idf/include/esp_idf_hal.h:22-23` still parses;
  `include/hal/gpio_ll.h` is not needed because `esp_idf_hal.cpp` is not
  compiled. `src/gpio_shim.c` is the pin table: level, enabled, interrupt
  type, handler and argument, with the one rule from §2.3: setting a level
  on a pin whose interrupt is enabled and whose type matches, or enabling
  the interrupt of a level-typed pin while its level is high, invokes the
  handler at once, on the calling task, under `portENTER_CRITICAL`. Handlers
  are `loraRadioIsr` (`iface-lora/esp-idf/src/lora.cpp:201-210`), which
  calls `vTaskNotifyGiveFromISR` and `portYIELD_FROM_ISR`: on this port
  those are the task-level notify and yield, legal from a task
  (`portable/linux/include/freertos/portmacro.h:82-85`).
  `REQUIRES freertos`.
- **`esp-idf/components/esp_timer/`**: a component named `esp_timer` that
  overrides IDF's headers-only one, with `CMakeLists.txt` taking the
  original include dir from `COMPONENT_OVERRIDEN_DIR` as
  `tools/mocks/esp_timer/CMakeLists.txt` does, `INCLUDE_DIRS` that dir,
  `SRCS src/esp_timer_host.c`, `REQUIRES freertos esp_common`. The
  implementation: `esp_timer_get_time` is `CLOCK_MONOTONIC` in microseconds
  from the first call; `esp_timer_create`, `_start_once`, `_start_periodic`,
  `_restart`, `_stop`, `_delete`, `_is_active`, `_get_period`,
  `_get_expiry_time`, `_init`, `_early_init`, `_deinit` over a mutex-guarded
  sorted list; one task `esp_timer` at priority 22 with a 32 KB stack that
  sleeps in `ulTaskNotifyTake` until the earliest expiry (in ticks, rounded
  up), runs due callbacks on its own stack, and is notified on every
  start/stop. `ESP_TIMER_ISR` is treated as `ESP_TIMER_TASK`. Users in the
  staged set: `storage.cpp:959-977`, `lora_supe.cpp:101-102,636-660`, the
  chip model (§17.8), and `esp_timer_get_time` everywhere.

### 17.5 spangap-core on the host

All host-only code sits in `spangap-core/esp-idf/src/host/` and is compiled
only when `IDF_TARGET STREQUAL linux`; on that target `CMakeLists.txt:1`'s
glob drops `pm.cpp`, `spi_helper.cpp`, `usb_ports.cpp`, `heap_track_stub.c`
and the `--wrap` block (137-191), and `_REQ` (30-56) becomes `driver
esp_event nvs_flash json esp_timer esp_rom esp_app_format spi_flash
esp_partition mbedtls esp_hw_support fatfs`. The `spangap-net` private
include lookup (78-88) stays.

- **Roots.** `include/fs.h:20-22` defines `FS_FIXED "/fixed"`, `FS_STATE
  "/state"`, `FS_SDCARD "/sdcard"`; on linux they are `"fixed"`, `"state"`,
  `"sdcard"` (relative, resolved against the station directory the board
  `chdir()`ed into). The literals stay literals because they are
  concatenated at compile time (`fs.cpp:1108-1109,1151-1152`,
  `storage.cpp:2269`). Fix the sites that spell the path instead of the
  constant, on the chip build too: `spangap-web/esp-idf/src/web.cpp:2121-2123`,
  `spangap-core/esp-idf/src/cli_cmd_fs.cpp:363,365`,
  `lxmf/esp-idf/src/lxmf.cpp:2927,8532`.
- **`fs.cpp`.** Include lines 26,31,33,35,36,38,39,41,42 and the functions
  `mountStateLittlefs` (180-191), `statePartitionEnsure` (1246-1310), the
  SD block (867-1054), `esp_ota_get_running_partition` (84) and the
  `esp_flash_*` sites (1274-1288,1399-1402) go behind
  `#if !CONFIG_IDF_TARGET_LINUX`. On linux `fs_init()` (1423) calls
  `nvs_flash_init()`, then `fs_mkdirp("state")`, skips spanfs (1439-1448:
  `fixed` is the board's symlink) and LittleFS (1451), and continues to the
  worker spawns (1478-1489) unchanged. `fsFormatFlash` (1022) and
  `sdAvailable()` report unsupported/false. The state-wipe breadcrumb
  (157-172) keeps using NVS. `spanfs` leaves `_REQ` on linux.
- **`spangap_init.cpp`.** Include `esp_littlefs.h` (26) and `hal/wdt_hal.h`
  (29) and the `esp_littlefs_format("state")` at 620 go behind
  `#if !CONFIG_IDF_TARGET_LINUX` (on linux the project-changed branch removes
  `state/` recursively instead). `esp_restart()` stays: it exits, and the
  launcher restarts the process.
- **`random.cpp`.** `bootloader_random_enable/disable` (33,36) and the
  include (6) go behind the same guard; `esp_fill_random` is the entropy
  source either way.
- **`include/compat.h`.** `spawnTask` (86-100): on linux raise `stackBytes`
  to at least 20480 before creating the task. `esp_sleep.h` (4) stays; it
  is declarations only.
- **`src/host/pm_host.cpp`.** Every symbol of `include/pm.h` as a no-op:
  `pmInit`, `pmPollUsb`, `pmRegisterCmds`, `pmUsbSerialJtagReattach`,
  `pmLockCreate/Acquire/Release`, `deepSleepAllowed` (false),
  `pmBoostAuto`, `pmBoost`, `pmBoostEnd`, `pmGpioWakeEnable` (0),
  `pmGpioWakeDisable`, `pmOnLightSleepWake`, `pmRecordDeepSleep`,
  `heapDump`, `pmStatsAddSampler`, `pmStatsHistory` (0), `pmStatsAvg`
  (0). `pmUsbAttached` returns true, which is what makes `logWireLive()`
  (`log.cpp:543-545`) true. `pmBoostHeld` is inline in the header and reads
  a task-local pointer; unchanged.
- **`log.cpp`.** The two `fwrite(…, stdout)` sites (642, 908) become
  `write(STDOUT_FILENO, …)` on linux. Nothing else changes; the log task,
  the ring and `esp_log_set_vprintf` (1225) work as on the chip.
- **`cli.cpp`.** The serial task (`serialTaskFn`, 1940; spawned at 2649)
  stays and becomes the station's console. On linux its setup puts
  `STDIN_FILENO` in `O_NONBLOCK`, `portRead` (1984-1995) calls `select()`
  on fd 0 with a one-tick timeout before `read()`, and `portWrite`
  (1996-2020) and `serialEmit` (1597-1631) use `write(STDOUT_FILENO, …)`
  instead of `fwrite`; `cliFlush` (102-112) is a no-op. The
  `usb_serial_jtag_driver_install` block (1948-1965) is already inside
  `#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG`. The TCP CLI on 8081 arrives with
  the net backend (`net.cpp:270-277` registers it).
- **`cron.cpp`** 355-361 is already guarded; the `esp_sleep.h` include stays.
- **`mem.h`**, `its.cpp`, `storage.cpp`, `cli_cmd_fs.cpp`, `storage_db.cpp`
  compile unchanged: caps are ignored and `esp_rom_crc32_le` exists.
- **`sys.mem.*`** telemetry is meaningless on the host; leave it.

### 17.6 spangap-net on the host

`net.cpp` is split so the host backend shares the relay instead of copying
it. The chip build is the proof that the split changed nothing.

- **`src/net_relay.cpp`** (new, both targets): the event bus (145-160), the
  endpoint table and listener sockets (212-367: `epFindByKey`,
  `epRegister`, `netOnAux`, `netRegisterCorePorts`, `epOpenPort`,
  `epOpenAll`, `netPublicPorts`, `netClientClose`, `epCloseAll`), the
  accept and relay loop (371-588: `netAcceptOne`, `netPollOnce`,
  `netFindClient`, `netItsDisconnect`), the dial path (596-693), the
  traffic counters (2900-2977, 3108-3109), `netForceClose` (3120-3126), and
  the state the relay reads: `s_linkUp`, `upstreamUp`, `setUpstream`
  (1186-1195), `netIsUp`, `netIsStaConnected`, `netGetLocalIp`. `epOpenPort`
  (293-326) gains a bind address argument; the chip passes `INADDR_ANY`, the
  host passes `hwLinuxBindAddr()`. `net_priv.h` declares what the two
  halves share.
- **`src/net.cpp`** keeps everything WiFi: `wifiNetifInit` onward, the
  state machine (1179-2233), scanning, AP, ping, the netif traffic hooks
  (2827-2844), and `netInit` (2981-3070) minus the portable defaults block,
  which moves to `net_relay.cpp` as `netInitCommon()`. Not compiled on
  linux, nor are `spangap_mdns.cpp`, `ntp.cpp`, `wget.cpp`.
- **`src/host/net_host.cpp`** (linux only): `netInit()` calls
  `netInitCommon()`, publishes `wifi.sta.up=1`, `wifi.sta.ip` and
  `wifi.mac` from the board's address and identity, and spawns the net task.
  The task registers the ITS ports as `netTaskFn` does (1657-1665), calls
  `netRegisterCorePorts`, `epOpenAll()`, `setUpstream(true)` (which sets
  `net.up` and signals the flag rnsd's boot barrier waits on,
  `rnsd.cpp:8067`), fires `NET_EV_UP`, then loops `netPollOnce()`. Inside
  `netPollOnce` the 10 ms `select` (444-446) is the interposed one: it
  polls and sleeps a tick. `netUp`/`netDown`/`NET_CMD_*` are no-ops that
  keep the link up. `netApplyPs`, `pmBoostAuto` (443) are already stubs.
- **`include/net.h:11-12`**: on linux include `<sys/socket.h>`,
  `<netinet/in.h>`, `<arpa/inet.h>`, `<netdb.h>` and define
  `typedef struct in_addr ip_addr_t;` with inline `ip_addr_isloopback`,
  `ipaddr_ntoa` and `ip_addr_set_ip4_u32_val` over it. These serve
  `web.cpp:1423,1531,1547`, `tcp.cpp:1410` and `net_relay.cpp`'s
  `netAcceptOne`. `tls.cpp:18` takes the same include switch.
- **`CMakeLists.txt`**: on linux the glob becomes the explicit list
  `net_relay.cpp tls.cpp host/net_host.cpp` and REQUIRES drops `esp_wifi
  esp_netif lwip espressif__mdns esp_http_client`.
- **Ports.** Web on 80 and 443 of the station's own loopback address, CLI on
  8081, iface-tcp's inbound on its configured port, RNode on 7633. Docker
  sets `net.ipv4.ip_unprivileged_port_start=0` in containers; if binding 80
  fails, set `s.net.http_port` per station instead.

### 17.7 spangap-web on the host

- `web.cpp:34` (`lwip/ip_addr.h`) goes behind `#if !CONFIG_IDF_TARGET_LINUX`;
  the host types come from `net.h`.
- `webrtc_task.cpp` and `webrtc_sctp.cpp` are excluded from the linux
  source list; `webrtcInit` is a legacy init hook the generated dispatch
  calls, so `src/host/webrtc_stub.cpp` defines it empty, and whatever
  `web.cpp` calls into the WebRTC half (grep `webrtc` in `web.cpp`) is
  stubbed there too.
- `CMakeLists.txt` REQUIRES drops `esp_netif lwip` on linux.
- `/fixed/webroot` is served from `data_merged`, which
  `spangap_browser_build` fills through `web-interface/deploy.sh`; the
  build runs Node in the container as today.

### 17.8 iface-lora on the host

- **`src/lora_priv.h:213`**: `EspIdfHal* hal` becomes `RadioLibHal* hal`.
  `esp_idf_hal.h` stays included (line 17) for `lora_fem.cpp:87-98`'s level
  constants; it parses on the host through the `driver` shim headers.
- **`src/lora.cpp:1015-1032`**: under `#if CONFIG_IDF_TARGET_LINUX` the slot
  gets `new VirtualHal(i)` and skips the `ready()` check; the `#else` keeps
  the `EspIdfHal` construction, its `init()` and `ready()` as they are.
  `r->hal->init()` at 1020 stays common.
- **`CMakeLists.txt`**: on linux remove `src/esp_idf_hal.cpp` from
  `LORA_SRCS` and add `src/host/virtual_hal.cpp`,
  `src/host/virtual_sx126x.cpp`, `src/host/ether_task.cpp`. `REQUIRES
  driver esp_timer esp_event` stay: both are ours on the host.
- **`src/host/virtual_hal.h/.cpp`**, `class VirtualHal : public RadioLibHal`,
  constructed as `RadioLibHal(MODE_INPUT, MODE_OUTPUT, LEVEL_LOW, LEVEL_HIGH,
  EDGE_RISING, EDGE_FALLING)` with the same constants `EspIdfHal` uses
  (`esp_idf_hal.h:30-37`). `pinMode`: no-op. `digitalWrite`: RST low then
  high resets the model; CS low opens a transaction, CS high closes it;
  other pins set the shim level. `digitalRead`: BUSY is 0, DIO1 and the
  rest read the shim. `attachInterrupt`/`detachInterrupt`: the shim's
  `gpio_isr_handler_add/remove` plus `gpio_intr_enable`. `delay`:
  `vTaskDelay` rounded up. `delayMicroseconds`: `vTaskDelay(1)` above one
  tick, else spin on `esp_timer_get_time`. `millis`/`micros`:
  `esp_timer_get_time`. `spiTransfer(out, len, in)`: one command frame to
  the model, `out[0]` the opcode; the model fills `in`, whose every byte is
  the status byte until data starts. `spiBegin/End/Transaction`: no-ops.
  `yield`: `taskYIELD`.
- **`src/host/virtual_sx126x.h/.cpp`**: the chip model of §2.2 at proof-of-
  concept depth. Commands, by opcode: `SetSleep 0x84`, `SetStandby 0x80`,
  `SetFs 0xC1`, `SetTx 0x83`, `SetRx 0x82`, `StopTimerOnPreamble 0x9F`,
  `SetRegulatorMode 0x96`, `Calibrate 0x89`, `CalibrateImage 0x98`,
  `SetPaConfig 0x95`, `SetRxTxFallbackMode 0x93`, `WriteRegister 0x0D`,
  `ReadRegister 0x1D`, `WriteBuffer 0x0E`, `ReadBuffer 0x1E`,
  `SetDioIrqParams 0x08`, `GetIrqStatus 0x12`, `ClearIrqStatus 0x02`,
  `SetDIO2AsRfSwitchCtrl 0x9D`, `SetDIO3AsTCXOCtrl 0x97`,
  `SetRfFrequency 0x86`, `SetPacketType 0x8A`, `GetPacketType 0x11`,
  `SetTxParams 0x8E`, `SetModulationParams 0x8B`, `SetPacketParams 0x8C`,
  `SetCadParams 0x88`, `SetBufferBaseAddress 0x8F`,
  `SetLoRaSymbNumTimeout 0xA0`, `GetStatus 0xC0`, `GetRssiInst 0x15`,
  `GetRxBufferStatus 0x13`, `GetPacketStatus 0x14`, `GetDeviceErrors 0x17`,
  `ClearDeviceErrors 0x07`, `GetStats 0x10`, `ResetStats 0x00`; anything
  else answers `CMD_INVALID` in the status byte. Registers are a sparse map
  preloaded with the datasheet reset values RadioLib read-modify-writes
  (`0x0889` sensitivity, `0x08D8` TX clamp, `0x0736` IQ, `0x0902` RTC
  control, `0x0944` event mask, `0x08AC` RX gain, `0x08E7` OCP), with
  `0x0320..0x032F` answering `"SX1261 V2D 2D02"` and `0x0740/0x0741` the
  sync word in the nibble-expanded form, decoded to the 8-bit word the
  ether matches on. Mode transitions are instantaneous in this step
  (`ready_at` is now); the datasheet timing table of §2.2 is a later
  addition and the field exists from the start. `SetTx` computes `t0`,
  `t_pre`, `t_hdr`, `t_end` with `loraToaSeconds` (`src/lora_toa.h:21-33`)
  from the chip's parameters, sends the ether a `tx`, and arms an
  `esp_timer` one-shot that raises `TX_DONE` at `t_end` and falls back as
  programmed. `rx_begin` from the ether arms one-shots that set
  `PREAMBLE_DETECTED`, `SYNC_WORD_VALID`, `HEADER_VALID` at the stated
  instants; `rx_end` copies the payload to the RX base and sets `RX_DONE`
  (with `CRC_ERR` when the verdict says so) and the packet status. Leaving
  `RX` cancels the pending one-shots. DIO1 is driven high through
  `gpio_shim_set_level` while any bit under the DIO1 mask is set, low when
  `ClearIrqStatus` clears the last one. `GetRssiInst` answers the noise
  floor, or the level the ether last stated for a frame in flight, in the
  chip's `-x/2` encoding, and `0xFF` outside `RX`. All state changes happen
  under `portENTER_CRITICAL`, because the ether task and the radio task both
  reach the model.
- **`src/host/ether_task.cpp`**: one task, 32 KB stack, priority above the
  radio task's. A UDP socket bound to an ephemeral port on the station's
  loopback address, connected to `hwLinuxEtherAddr()`. Sends `hello` at
  start and a `state` on every command that changes mode, carrier,
  bandwidth, spreading factor, coding rate, sync word, header type, CRC or
  preamble length. Loop: `select()` on the socket with a one-tick timeout
  (interposed), drain with `recvfrom(MSG_DONTWAIT)`, parse, apply to the
  model. Messages are JSON, one per datagram, payload base64, fields as in
  §9; a station ignores what it does not understand.

### 17.9 The ether, first form

`ether/ether.py`, Python 3 standard library plus nothing: `asyncio`
`DatagramProtocol` on `--bind host:port`. Stations by `sid` from `hello`;
`state` stored per slot; on `tx` from station S, for every other station in
`RX` whose carrier, bandwidth, spreading factor and sync word match, send
`rx_begin` at once and schedule `rx_end` at `t_end` with verdict `clean`,
`rssi` a constant `-80` and `snr` `10`; if another frame is in flight on the
same carrier at any moment of this one, both end as `crc`. No positions, no
path loss, no referee. Every message in and out is one line of
`record.tsv`. About three hundred lines. `welcome` carries `mode: real`.

### 17.10 Order of work

Each checkpoint is a build and a run; the next starts only when the
previous one passes.

1. **Plumbing and a bare core.** §17.3, §17.4 without the radio pieces,
   §17.5. Build with `--no-net --no-web --without reticulous/rns
   reticulous/iface-lora reticulous/iface-tcp reticulous/lxmf` plus the
   §17.2 list. Run one station: it reaches `spangap ready`, its console on
   stdin answers `help`, `state/` fills with `storage/root.json.gz`. First
   thing to confirm in the configure log: chip-only Kconfig symbols in the
   defaults are warnings, not errors.
2. **Network and the browser.** §17.6, §17.7. Build with `--without
   reticulous/rns reticulous/iface-lora reticulous/iface-tcp
   reticulous/lxmf`. Run one station; `nc 127.0.0.11 8081` gets the CLI;
   `curl http://127.0.0.11/` gets the SPA; the browser on the Mac gets it
   through the proxy (§17.11).
3. **Two stations over TCP.** Add `rns` and `iface-tcp`. Point b's iface-tcp
   at a's inbound port from b's console; a's announces show up in b's CLI.
4. **Three stations over LoRa.** §17.8, §17.9. Add `iface-lora`; start the
   ether; a, b, c see each other's announces with iface-tcp unconfigured;
   `record.tsv` has the frames.
5. **A message by hand.** Add `lxmf`. From a's web UI send to b; b shows it.
6. **The chip build.** Rebuild `reticulous/reticulous --with
   spangap/hw-meshnology-w12 --kconfig CONFIG_SPANGAP_USB_CDC=y` and report
   that it builds. Do not flash.

### 17.11 Running it

`sim/run.py` in the plans-adjacent `sim/` directory of the reticulous
buildable (`reticulous/sim/`): starts the ether, then N stations from
`reticulous/esp-idf/build/reticulous.elf` with the §17.4 environment, node
ids 1..N, directories `sim/nodes/<id>/`, stdout to `sim/nodes/<id>/log`,
stdin from a pty kept open; `--console <id>` attaches the terminal to that
station's pty; `--ether-only`, `--nodes N`, `--fixed <dir>`. It also starts
`sim/proxy.py`: an HTTP reverse proxy listening on port 9011 that routes
`Host: <id>.sim.localhost` to `127.0.0.1<id>:80`, so the Mac's browser opens
`http://1.sim.localhost:9011/`. Port 9011 is the testbed's own fixed port,
published at the same number on both sides beside flashmon's 9010
(`spangap-outside`, `SIM_PORT`, overridable with `SPANGAP_SIM_PORT`); the
dev-port range 9000–9009 is published to ephemeral host ports and is
`spangap dev`'s. A container created before the port existed is recreated
by the next host-side `spangap` command. Chrome and Firefox resolve
`.localhost` names to loopback without configuration; Safari needs
`/etc/hosts` entries. The proxy is plain HTTP; HTTPS on 443 is not part of
this step.

### 17.12 Rules for the executing agent

- Real time only. Nothing from §3 is touched: no linker wraps of the clock,
  no `wait`, no `advance`.
- No FreeRTOS task blocks in a host syscall. Sockets are non-blocking; the
  only waits are `select()` (interposed), FreeRTOS primitives and
  `vTaskDelay`. `stdin` is non-blocking.
- No `fwrite`/`printf` from more than one task to the same stream; the log
  sink and the console use `write(2)`.
- Every task stack at least 20 KB on the host.
- Chip-only code goes behind `#if !CONFIG_IDF_TARGET_LINUX` or out of the
  linux source list; host-only code lives in `src/host/` directories and is
  listed only under `if(IDF_TARGET STREQUAL linux)`. No `#ifdef` inside a
  function where a separate file will do.
- The chip build is rebuilt and must succeed at the end; nothing is flashed.
- Comments describe the current invariant, never what changed. No
  references to this plan in code.
- Progress is written to `plans/simulation-poc-progress.md` after every
  checkpoint: what passed, what was changed, what is open.

## 18. Considered and rejected

**Running natively on macOS.** The IDF Linux simulator does run on macOS,
but the clock, the wall-clock wraps and the heap fix all rest on GNU ld's
`--wrap`, which Apple's linker lacks, and the VM runs the same code at native
speed. See §10.1.

**Wokwi.** The headless CLI runs in Wokwi's cloud on a per-minute budget;
custom chips are sandboxed WASM with no socket API; multi-MCU diagrams do not
exist.

**QEMU as the primary runtime.** One core per node, ten nodes per box, no
virtual time. Kept as the smoke target (§13).

**A QEMU fork with a register-level SX1262.** Runs the LoRa driver unmodified
but inherits QEMU's ceiling and adds a fork to maintain.

**Mininet-WiFi and wmediumd.** Emulated 802.11 radios (`mac80211_hwsim`) in
network namespaces with a user-space medium daemon. Everything the daemon
models is 802.11 — MAC, rates, frames, backoff — with capture only implicit
in an SINR calculation and no medium-busy enforcement; nothing of LoRa's
physics. The Mininet-WiFi fork is wall-clock only; the dormant upstream has a
virtual-time mode only inside a User-Mode Linux time-travel kernel. No LoRa
virtual radio exists for Linux at all. For IP-link realism in tests, plain
network namespaces with `tc netem` do the job unprivileged.
