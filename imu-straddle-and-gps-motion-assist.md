# spangap/imu, GPS power policy, and PPS-grade time

Two changes that share a motive — a GNSS receiver is the most expensive thing on
a battery node, and most of what it burns is spent re-fixing a position that has
not changed:

1. **`gps` learns that a board may own a power switch** for the receiver, stated
   as Kconfig (GPIO + polarity) or supplied by the board where the switch is a
   PMU rail rather than a pin. Unset, nothing changes — the software standby
   path is what it is today.
2. **A new `spangap/imu` straddle** owns the accelerometer as a *motion oracle*,
   publishing "is this thing moving" on the storage bus, and `gps` compiles an
   assist against it, keyed on `CONFIG_STRADDLE_IMU`.

## The contract, first

```
imu (QMI8658)                storage bus                    gps
──────────────                ───────────                    ───
motion detected      ──►  imu.moving  = 1        ──►  wake receiver, resume tracking
                          imu.motion  = <n++>          (counter: "moved since you last looked")
still for s.imu.still_s ─► imu.moving  = 0        ──►  start the still timer
probe at boot        ──►  imu.present = 1              (capability gate; absent = 0 = assist off)
                          imu.model   = "QMI8658"

gps ──► CONFIG_GPS_POWER_PIN, or gpsBoardPower(false) ──► receiver rail
```

No straddle links against the other. This is the coupling `gps` already has with
`spangap-rtc` (`rtc.present`, `sys.time.disciplined`), for the same reason: a
build may carry either, both, or neither, and an absent key reads as zero, which
is the correct behaviour rather than a special case.

## Why it is worth the code

Measured figures, not estimates (MAX-M10S data sheet Table 15 at 3.0 V, VCC plus
V_IO; QMI8658C Table 16 at 1.8 V):

| state | draw |
|---|---|
| GNSS cyclic tracking (`s.gps.interval=5`, today's default) | ~7 mA |
| GNSS continuous tracking | ~11.8 mA |
| GNSS software standby (UBX-RXM-PMREQ) | ~46 µA |
| GNSS hardware backup (VCC/V_IO cut, V_BCKP live) | ~28 µA |
| IMU accelerometer, low power, watching for motion | 30 µA (3 Hz) – 55 µA (128 Hz) |
| IMU asleep (what the board does today) | oscillator off; no published figure |

A parked node therefore costs ~7 mA to keep asking where it is, or ~76 µA to
keep an accelerometer watching for a reason to ask again. Two orders of
magnitude, and the fix it gives up is one it already has.

## 1. `gps`: a power switch the board may own

### Kconfig (added to `gps/esp-idf/Kconfig`)

| symbol | default | meaning |
|---|---|---|
| `GPS_POWER_PIN` | `-1` | GPIO that switches the receiver's rail. `-1` = no pin. |
| `GPS_POWER_ACTIVE_HIGH` | `y` | polarity of that pin. |
| `GPS_POWER_KEEPS_BACKUP` | `n` | the switch cuts VCC/V_IO but leaves V_BCKP alive, so the receiver keeps its ephemeris and can hot start. A board states this; `gps` will not guess it. |

### The board hook, for switches that are not pins

Some boards gate the receiver from the PMU, over I²C — the T-Beam Supreme's
GNSS is ALDO4. A pin number cannot express that, so `gps` declares

```c
/* Weak. A board whose receiver rail is not a GPIO defines this; returns false
   if it could not switch. gps calls it only where it would drive the pin. */
__attribute__((weak)) bool gpsBoardPower(bool on);
```

Resolution order inside `gpsPowerSet(on)`: **pin if configured, else the hook if
one is linked, else nothing** (and the caller falls back to software standby). A
board supplies one or the other, never both.

### Policy — the rail is for "off", not for duty cycling

Cutting power is **only** on the "the user turned GPS off" path, never inside the
tracking cycle. A receiver that loses its ephemeris pays 30 s of cold start to
save milliamp-seconds, which is a bad trade at any duty cycle. Concretely:

- `s.gps.enable = 0` → standby command as today, **then** the rail, but only if
  `GPS_POWER_KEEPS_BACKUP=y` or the operator asked for it (below).
- new setting **`s.gps.off_mode`**: `standby` (default) or `power`. On a board
  with no switch the row does not exist (`when_kconfig`).
- the L76K FORCE_ON pulse and the u-blox wake byte stay exactly as they are —
  with a rail cut, neither is needed, because the chip comes back fresh.

This also retires `s_needsPowerCycle` on boards with a switch: "power-cycle to
wake" becomes something the firmware can simply do.

## 2. `spangap/imu` — the motion oracle

A new straddle, `spangap/imu`, prefix `imu`, shaped exactly like `gps`: it owns
one chip family and a task; a board stages it from `additional_installs` and
publishes pins in a `when: spangap/imu` kconfig block.

**Scope is deliberately narrow.** It answers "is this device moving", and
nothing else. No orientation, no tilt, no step counting, no fusion, no sample
stream. Those are a different straddle's problem if they ever become one; a
motion oracle stays small enough to have a single, testable behaviour.

### Kconfig

| symbol | default | meaning |
|---|---|---|
| `IMU_PART_QMI8658` | choice | the only member today; a choice so a second part is additive |
| `IMU_SPI_HOST` | `-1` | SPI host the part is on; `-1` = not wired, service dormant |
| `IMU_SPI_SCK` / `_MOSI` / `_MISO` | `-1` | bus pins, used **only if the host is not already initialised** |
| `IMU_CS_PIN` | `-1` | chip select |
| `IMU_INT_PIN` | `-1` | interrupt line; `-1` = poll only |
| `IMU_INT_LINE` | `1` | which of the part's two interrupt outputs `IMU_INT_PIN` is wired to |

**Bus rule: initialise if free, adopt if taken.** The IMU usually shares its bus
with the SD card, which `spangapInit()` claims before any `onInit` runs. So the
service calls `spi_bus_initialize` and treats `ESP_ERR_INVALID_STATE` as success
— the host is up, add the device to it. Same spirit as `spangap-rtc` adopting
the board's I²C bus by port, and the reason the pin symbols above are qualified.

### Published keys — this is the API

| key | type | meaning |
|---|---|---|
| `imu.present` | 0/1 | the part answered at boot. The capability gate every consumer tests. |
| `imu.model` | string | `"QMI8658"`, or empty |
| `imu.moving` | 0/1 | debounced motion state: 1 on a motion event, 0 after `s.imu.still_s` without one |
| `imu.motion` | int | per-boot monotonic counter, bumped per motion event |
| `imu.still_s` | int | seconds since the last motion event |
| `imu.state` | string | finished text for a settings pane: `"moving"`, `"still 4m"`, `"asleep"`, `"not present"` |

`imu.moving` is for a policy that wants a state; `imu.motion` is for one that
wants an edge and may have been asleep through it — a counter cannot be missed
the way a level can. Both are published by the same task from the same event, so
they never disagree.

### Settings

`s.imu.enable` (default 1), `s.imu.threshold_mg` (default ~64 mg), `s.imu.odr`
(dropdown: 3 / 11 / 21 / 128 Hz — the datasheet's low-power rates, with their
draw in the labels), `s.imu.still_s` (default 30). Disabled, the part goes to
the power-down state the board currently applies and `imu.moving` stops being
published.

### How it watches

Wake-on-Motion (CTRL9 command `0x08`, threshold in `CAL1_L`, interrupt select
and blanking in `CAL1_H`), event latched in `STATUS1` and cleared by reading it.
Because the latch survives, **polling is a first-class mode**: with no `INT` pin
wired, or on a board where that pin cannot wake the SoC, the task reads `STATUS1`
on its own cadence and still never misses an event. That is what makes this
usable on the Supreme, whose IMU interrupt is on GPIO 33 — outside the S3's RTC
GPIO range, so no `ext0`/`ext1` deep-sleep source can see it.

### C API (`imu.h`)

For an in-image consumer that does link against it (none today):
`bool imuPresent(void)`, `bool imuMoving(void)`, `uint32_t imuMotionCount(void)`,
`uint32_t imuStillSeconds(void)`. Cross-straddle coupling stays on the storage
bus regardless — the header is a convenience, not the contract.

## 3. `gps`: the assist, under `CONFIG_STRADDLE_IMU`

```c
#if CONFIG_STRADDLE_IMU
    /* ... subscribe, still-timer, park/unpark ... */
#endif
```

`storageSubscribeChanges("imu.", onMotion)` and one added state in the task:

- **TRACKING** — today's behaviour, cyclic tracking at `s.gps.interval`.
- **PARKED** — entered when the oracle is live (`imu.present` is 1 **and** it has
  published within the watchdog window below) and `imu.moving` has been 0 for
  `s.gps.motion_hold` seconds. Sends the same standby the disable path sends,
  keeps the last fix and publishes `gps.state = "parked (still)"`, so a pane
  shows *why* the fix is not advancing rather than looking broken.
- **motion → TRACKING** — the wake path that already exists (wake byte / FORCE
  pulse), and a hot start, because power was never cut.

### The control is a switch, on by default

One row in the GPS section, gated on the straddle being staged and nothing else:

```yaml
      - switch:  { label: "IMU assisted", key: s.gps.imu_assist, default: 1 }
        when_kconfig: CONFIG_STRADDLE_IMU
      - caption: "Only track while the unit is moving."
        when_kconfig: CONFIG_STRADDLE_IMU
```

`when_kconfig` keeps the key out of storage entirely on a build with no IMU, so
there is no dead setting to explain. There is deliberately **no `when_key:
imu.present`** on it: the accelerometer is soldered to the boards that have one,
so `imu.present` describes a fault rather than a variant, and hiding the control
when the part is broken would take the evidence off the screen along with it.
The row stays; the fault shows up as `imu.state` reading "not present", where a
fault belongs. (`rtc.present` is not the same case — the T-Deck's PCF8563 really
is fitted-or-not.)

The label says "IMU assisted" rather than "IMU assisted GPS" because the section
is already headed GPS, and the caption carries the sentence that explains it.

**The hold time stays out of the pane.** `s.gps.motion_hold` remains a key with
a default (120 s) so it can be tuned from the CLI, but it gets no row: a switch
plus a number is two decisions where the operator has one question, and the
number only matters to someone already reading the log. Not surfacing it is also
what lets the default change later without a migration conversation.

The caption is worded "only track" rather than "only turn on" on purpose — the
receiver is **not** powered off here. It keeps its rail, its ephemeris and its
almanac, which is what makes coming back a one-second hot fix instead of a
half-minute cold start. Turning it off is the separate, deliberate thing
`s.gps.off_mode` does.

Four behaviours worth stating because they are easy to get wrong:

- **Fail open.** A silent oracle must never strand the receiver. If
  `imu.present` is 0, or nothing has been published to `imu.` within the
  watchdog window (a few multiples of `s.imu.still_s`), the assist treats the
  device as moving and tracks normally. A dead accelerometer then costs battery
  rather than position, which is the right way round — and it is the only guard
  that matters, since the part is soldered and its absence is always a fault.
- **Never park before the first fix.** A node that boots stationary still needs
  its position once; parking during acquisition would leave it with none.
- **Park is not disable.** `s.gps.enable` stays 1, the user's intent is
  untouched, and the rail is never cut here — only the receiver's own standby.
- **Parking stops disciplining the clock**, because the receiver is where GPS
  time comes from. A default-on assist therefore changes what keeps time on a
  stationary node, and the existing hand-back rules are what catch it: with an
  RTC the window is 3 days, without one an hour, after which ntp takes over.
  That is correct on a node with either an RTC or a network, and quietly wrong
  on a node with neither — an isolated, stationary node parked for good. The
  cheap answer if that case matters: unpark for a few seconds on a timer (an
  hour, say) when nothing else is disciplining the clock, which is single-digit
  milliamp-seconds and can key off the `sys.time.disciplined` staleness the
  straddle already tracks. Worth building only if that node exists.

## 4. `hw-lilygo-tbeam-supreme`

- stages `spangap/imu`; publishes host 3 + SCK 36 / MOSI 35 / MISO 37, CS 34,
  INT 33 in a `when: spangap/imu` block;
- keeps its own QMI8658 power-down under `#if !CONFIG_STRADDLE_IMU`, so a build
  without the straddle still silences the part;
- implements `gpsBoardPower()` over the PMU (ALDO4) and sets
  `CONFIG_GPS_POWER_KEEPS_BACKUP=y` — *pending confirmation that V_BCKP is fed
  from the 18650 rather than from ALDO4*; LilyGo's "GPS backup power comes from
  the 18650, hot start needs the battery" note says it does, the schematic would
  say it plainly.

## 5. PPS, and who sets the RTC

Separate from the motion work, same board, so it belongs in the same plan.

```
GNSS ──PPS edge──► gps: esp_timer stamp ──► system clock (sub-ms)
                                              │
                              rtc sync / mirror│ STOP → write → release on the second
                                              ▼
                                          PCF8563
```

**The layering already answers "gps or rtc".** `gps` disciplines the *system
clock*; `spangap-rtc` copies the system clock to the chip. The system clock is
the interchange format, which is why neither links against the other, and PPS
improves the first half without being visible to the second.

### `gps`: PPS instead of a guessed lag

- `CONFIG_GPS_PPS_PIN` (`-1` default; this board publishes 6). RTC-capable on
  the S3, so it can also be a deep-sleep wake source later.
- ISR stamps the rising edge with `esp_timer_get_time()`. The NMEA sentence that
  follows names which second that edge *was*, so the pair gives an absolute
  time with microsecond phase.
- That **replaces the static pipeline-lag constants** (70 ms u-blox @ 38400,
  260 ms L76K @ 9600) wherever the pin is wired, and turns them into a
  measurement worth publishing — `gps.lag_ms`, observed rather than assumed.
- Only trust pulses while the fix is valid: receivers emit them before lock with
  useless accuracy.
- With PPS, discipline slews; `settimeofday` steps stay for real discrepancies.

### `spangap-rtc`: `rtcSyncFromSystem()` and a `rtc sync` command

A function and a CLI verb, so it can be cronned rather than only happening as a
side effect of the mirroring. It prints what it did — the correction it applied
and how fresh the source was ("RTC was 1.4 s fast; set from system clock,
GPS-disciplined 3 s ago") — because a sync that silently wrote a bad time is the
failure worth seeing.

The write should be **phase-aligned**, which is where PPS pays off twice: the
PCF8563 counts whole seconds, so a naive copy inherits up to ±0.5 s of
quantisation. Setting `STOP` (control_status_1 bit 5), writing the time, and
releasing `STOP` on the system clock's second boundary starts the chip in phase
instead. **Confirm first** that `STOP` resets the prescaler divider chain — that
is what makes the phase ours rather than whatever the chip was already running.

The automatic mirroring stays exactly as it is; this is the deliberate,
observable version of the same act.

## Open questions

1. **INT1 or INT2 on GPIO 33?** The docs do not say, and WoM has to name one.
   One shake per setting on real hardware answers it; until then the poll path
   is the one that works.
2. **Is V_BCKP battery-fed on the Supreme?** Decides whether cutting ALDO4 is a
   28 µA hot-startable backup or a cold start next time.
3. **L76K units get standby, not PSM** — `gpsApplyRate` only speaks UBX. The
   motion assist still helps there (park = deep backup + FORCE wake), and is
   arguably worth *more*, since those units track continuously.
4. **Does `STOP` reset the PCF8563's prescaler?** Decides whether the aligned
   RTC write above buys sub-millisecond phase or only removes the rounding.
5. **Does anything light-sleep yet?** The IMU's interrupt can wake light sleep
   but not deep sleep on this board. If deep sleep is on the roadmap, the RTC
   alarm on GPIO 14 is the wake source that reaches it, and the pairing —
   "PCF8563 wakes us, IMU tells us whether to bother with GNSS" — is the shape
   worth designing toward.
