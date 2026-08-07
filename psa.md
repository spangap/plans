# PSA — waiting for a free channel

> Scope: **what we must do before keying up, and what we currently do instead.**
> Frequency and modulation are settled in [`afa.md`](afa.md); this file assumes
> its 9 × 500 kHz plan and picks up at "the frame is queued, may we transmit?".
> Regulatory figures are read from the standards named in §8; where a number
> here disagrees with those documents, the documents are right and this file is
> stale. Values marked **[verify]** are ones I have not read out of the current
> edition or datasheet and should not be certified against.
>
> PSA = polite spectrum access; CCA = Clear Channel Assessment; LBT =
> listen-before-talk; AFA = Adaptive Frequency Agility; CAD = Channel Activity
> Detection (a LoRa-specific hardware sense); APPC = adaptive p-persistent CSMA
> (our name for RNode's backoff, §5); e.r.p. = effective radiated power;
> OBW = occupied bandwidth; DFS = dynamic frequency scaling.

**There will always be two regimes, and they are not variants of one design.**

- **The reticulum channel** — one fixed frequency, camped on permanently, shared
  with third-party Reticulum nodes, carrying broadcast traffic. Frequency
  agility is impossible here by definition: it is the rendezvous point, and a
  node that hops off it stops being reachable. §1.
- **The nine AFA channels** — used in scheduled detours for unicast bursts, no
  node camps on them, every visit is negotiated on the reticulum channel first.
  §2.

The regulatory regime differs accordingly: duty cycle on the first, PSA + AFA on
the second. §3 is what the hardware allows either of them to do.

**Contention is arbitrated on the reticulum channel and nowhere else.** Every
detour is announced there first, in a small blip, on the one channel where
carrier sense and backoff run — so the hailing channel is both the bottleneck
and the scheduler. What it hands out is access to nine channels running at many
times its own rate, a space enormously larger in slot-time than the announcements
gating it can ever fill. **There is therefore no contention algorithm on the
nine, no occupancy scanning, no load balancing across them, and no plan to add
any.** Their transmit path is the regulatory CCA and the airtime ledger, full
stop. Anywhere this file discusses backoff, politeness or fairness it is
discussing the reticulum channel.

## 1. What we currently do on the main reticulum channel

### 1.1 The flow as built

```
frame queued (rnsd → ITS stream → drainOneOutbound)
  │
  ├─ splitPending?           a split RX in progress blocks TX (half duplex)
  │
  ├─ csmaClear(r)            gated on s.lora.<n>.lbt
  │   │
  │   ├─ sense               channelRssi() = getRSSI(false), one SPI txn,
  │   │                      busy = rssi > tracked_floor + 6 dB
  │   │
  │   ├─ appc=0  DIFS (2 slots idle) → backoff [0, 2^cw) slots, cw doubles
  │   │          on each busy encounter, resets on grant
  │   │
  │   └─ appc=1  DIFS (SIFS + 2 APPC slots) → accumulate free-medium time
  │              toward appcCw × appcSlot; band chosen by own airtime;
  │              busy freezes the accumulator rather than resetting it
  │
  ├─ grant → itsRecv one packet → startTransmit
  │
  └─ TxDone → appcAddAirtime() credits the 7500 ms bin
```

Sensing runs at `slotTicks` cadence (10–20 ms after the 100 Hz tick floor) and
the frame stays queued between passes; `nextDeadline()` wakes the task at the
next slot boundary. `lbt_timeout` sheds a frame that cannot win the channel.
`lbt=0` reverts to blind transmit. The full machine, the APPC parameter set and
the divergences from RNode are in
[`iface-lora/INTERNALS.md`](../iface-lora/INTERNALS.md) §6 and §6a and are not
repeated here.

### 1.2 What that is, in regulatory terms

**Nothing.** It is a good CSMA implementation and it has no standing under
EN 300 220 whatsoever:

- The busy test is **relative** — tracked noise floor plus 6 dB. With the floor
  around −105 dBm that lands near −99 dBm at BW125, roughly 18 dB *stricter*
  than the regulatory threshold, but strictness is not the point: there is no
  absolute threshold in the code at all, and on a channel with a raised floor
  the relative test drifts up with it.
- The assessment is a **single point sample**, not a ≥160 µs measurement.
- The interval between the last sample and the carrier coming up is
  **unbounded** — up to a full slot of staleness plus `startTransmit` latency,
  which at SF10+ is 90–100 ms against a 5 ms allowance.
- **No transmit budget of any kind is enforced.** APPC keeps two 7500 ms bins,
  ~15 s of history, purely to pick a contention band; they reset on any
  `s.lora.*` write via `applyConfig`. There is no hour, no cap, no refusal.
- `lbt` and `appc` are **live settings**. A regime that can be switched off from
  the CLI cannot be the basis of a declaration.

So today the device transmits as much as it likes, whenever the channel sounds
quiet to a threshold of its own choosing. That is fine on a bench and fine under
a licence-exempt regime we are not claiming; it is not compliant with either
alternative on offer.

### 1.3 What has to be added to be compliant here

The channel is fixed, so **AFA is impossible — and that most likely removes PSA
as an option altogether.**

The alternative to the duty cycle in EN 300 220-2 table B.1 is written as
**LBT+AFA**, not LBT alone. Frequency agility is not a bonus stacked on top of
listen-before-talk; it appears to be part of what earns the relief, and the
reasoning holds up on its face — a node that senses a busy channel and cannot
move has nowhere to go, so LBT on its own converts a duty-cycle limit into a
queue rather than into shared spectrum. Table 48's note that more AFA channels
buy more accumulated transmission time is the same idea read from the other
side: the cumulative allowance is described in terms of the channel structure
agility provides.

**[verify — whether EN 300 220-1 clause 5.21 defines an LBT-without-AFA category
at all, and if it does, what Tcum_on it carries.]** `afa.md` §1's table
summarises the same table B.1 cell as a plain "PSA: yes" for all four entries;
that is a summary of the identical wording and needs the same check. Nothing in
either file should be read as establishing that a fixed-frequency node can claim
100 s/h.

Until that is checked, take the conservative reading: **on the reticulum channel
the duty cycle is not the better option, it is the only one.** That makes the
decision easy and leaves only the question of placement — which is currently a
user setting with no default (`s.lora.<n>.frequency`, "region/antenna
dependent"):

| Placement | Duty cycle | Power |
|---|---|---|
| 863.0–865.0 (K) | 0.1 % = 3.6 s/h | 25 mW |
| 865.0–868.0 (L) | 1 % = 36 s/h | 25 mW |
| 868.0–868.6 (M) | 1 % = 36 s/h | 25 mW |
| 868.7–869.2 (N) | 0.1 % = 3.6 s/h | 25 mW |
| 869.4–869.65 | **10 % = 360 s/h** | **500 mW** |

The last row is the odd one out and it is where the Reticulum LoRa community in
Europe generally sits. It is deliberately outside `afa.md`'s scope (that plan
stops at 869.2), and it is the only entry in reach with 500 mW e.r.p. rather
than 25 mW — 100× the power and 10× the airtime of the K and N entries.
**[verify]** its exact edges, power limit and duty cycle against the current
EN 300 220-2 table B.1 and the national table; I am quoting it from memory and
it is the single most consequential number in this file.

If the hailing channel lands there, the trade is exactly right for a broadcast
announce channel: 360 s/h at 13 dB more power, no sensing timing discipline, no
dead-time budget, no absolute threshold. Even the 1 % entries at 36 s/h are
workable for announce traffic. LBT stays in place on top as a pure throughput
mechanism (§5) — unconstrained by any declaration, free to keep its own stricter
relative threshold, and costing nothing regulatory since being quieter than
required is never a violation.

What that requires, concretely:

1. **An hour-long transmit ledger for the channel**, credited from
   `loraAirtimeSeconds()` at TxDone — the same figure APPC already computes.
   Sliding window, not a calendar hour: the limit is "in any observation period
   of one hour" **[verify — whether the standard permits a fixed hourly reset,
   which is materially cheaper to implement]**.
2. **A refusal path**, distinct in telemetry from `lbt_timeout`'s contention
   shed. Out-of-budget and can't-win-the-channel are different conditions and
   the operator needs to tell them apart.
3. **Fixed, non-live parameters.** The duty-cycle percentage and the ledger
   behaviour compiled in, not settable; any override behind an explicitly
   non-compliant mode.
4. **Ledger persistence across radio restarts** at minimum, since today any
   `s.lora.*` write clears the bins. Across reboots is a judgement call — see
   §6.2.
5. **A frequency default, or a refusal to start without a declared region.**
   As long as the frequency is a free-form user setting, the device cannot know
   which duty cycle applies to it. A region table mapping frequency → band entry
   → duty cycle is a prerequisite for the ledger meaning anything.

Point 5 is the one that is easy to skip and shouldn't be. Everything else is
mechanism; this is the input the mechanism needs.

## 2. The AFA + PSA regime on the nine channels

### 2.1 The sequence the regulation mandates

```
frame queued for a burst detour
  │
  ├─ ledger      this channel's trailing-hour Tcum_on < 100 s?
  │              and ≥100 ms since our last TX on this channel (Toff)?
  │                 no → pick another of the nine (this is what AFA buys)
  │
  ├─ retune      SetStandby → SetRfFrequency → SetRx   (~0.25 ms, §3.2)
  │              no image recalibration: 863–870 is one calibration band
  │
  ├─ CCA         ≥160 µs energy measurement, on this channel, at ≥ the OBW
  │              we are about to occupy, against an ABSOLUTE threshold
  │                 busy → defer ≥ CCA interval, re-assess
  │
  ├─ dead time   ≤5 ms, declared and measured — carrier up
  │
  ├─ Ton         ≤1 s single transmission, ≤4 s dialogue or polling sequence
  │
  └─ TxDone      credit the ledger, stamp this channel's Toff
```

Every step above is mandated, and **that is the entire transmit path on these
channels** — there is nothing of ours layered on top. No APPC, no contention
window, no tracked-floor sense. The blip that bought this detour was already
carrier-sensed on the hailing channel, and that is where the arbitration
happened.

The CCA is therefore doing one job only: it is a **legality condition**, with a
threshold set by the regulator and generous enough that it will pass on a
channel carrying a link we would collide with (§4.2). It is not a collision
avoidance mechanism and should not be tuned as though it were. If it says busy
we defer, not because we are being polite but because transmitting would be
unlawful.

### 2.2 Why PSA here rather than duty cycle

**There is a choice here only because there is agility here.** §1.3's channel
has no such option; these nine do, and it is worth a lot. Across them the duty
cycle yields 79.2 s/h total — 3.6 s/h for band K, 36 s/h for L, 36 s/h for M,
3.6 s/h for N — and those are **per-band pools shared across every channel
inside them and every device we own**. PSA yields 100 s/h **per channel**,
900 s/h aggregate. That is the ~11× in `afa.md` §2 and it is the entire reason
the nine-channel plan exists.

The choice is per equipment declaration, not per packet. **[verify]** whether a
single device may declare duty cycle on one channel and LBT+AFA on nine others,
which is precisely the two-regime arrangement this file assumes throughout. If
it may not, the whole design needs revisiting. Together with §1.3's question
about LBT-without-AFA these two bound the design from both sides: one asks
whether the reticulum channel can ever escape its duty cycle, the other whether
the nine can have PSA while it does not.

### 2.3 The measurement

| | |
|---|---|
| Threshold, < 100 mW e.r.p. | `-102 + 10·log₁₀(RB_kHz)` dBm |
| At BW125 / BW250 / BW500 | −81 / −78 / −75 dBm |
| Reference | 0 dBd (+2.15 dBi); other antenna gain shifts it by the difference |
| Minimum CCA interval | 160 µs |
| Measurement bandwidth | ≥ the OBW of the transmission that follows **[verify]** |

The threshold derives from the table 32 receiver-sensitivity limit
`-117 + 10·log₁₀(RB_kHz)`, offset by 15 dB. Every channel in the plan is 25 mW,
so only the < 100 mW row ever applies and those three numbers are the complete
set.

**Adaptive power interacts with this.** EN 300 220-1 permits the threshold to be
relaxed when the transmitter runs below its declared maximum — a device
transmitting 10 dB down interferes 10 dB less and may tolerate more ambient
energy. The exact wording and whether the relaxation is capped is **[verify]**,
and it matters directly to
[`adaptive-power.md`](adaptive-power.md): if the relaxation exists as I remember
it, a per-neighbour power decision also moves the CCA threshold for that frame,
and the two loops share a variable rather than composing cleanly.

### 2.4 The timing

| Parameter | Limit |
|---|---|
| Minimum CCA interval | 160 µs |
| Minimum deferral period after a busy CCA | = CCA interval |
| Dead time, CCA end → transmit start | declared, ≤ 5 ms |
| Ton_max, single transmission | 1 s |
| Ton_max, dialogue or polling sequence | 4 s |
| Toff_min, same operating frequency | 100 ms |
| Max Tcum_on | 100 s per hour per 200 kHz of spectrum |

**Dead time is the constraint that bites hardest** and the one most easily
missed. The channel must be assessed clear and the carrier must come up within
5 ms of that assessment ending — no scheduler hop, no queue re-check, no "grant
now, transmit on the next task turn". Whatever we declare is what gets measured
on the bench. §3.3 is what stands in the way of it on this hardware.

**Toff_min is per operating frequency**, which is a second reason AFA earns its
place: 100 ms of enforced silence on the channel just used costs nothing when
there are eight others. On a single fixed channel it would be a hard ceiling on
frame rate.

### 2.5 The cumulative cap

100 s/h **per 200 kHz portion of spectrum**, not per channel. A 500 kHz channel
puts its full on-time into every 200 kHz portion it overlaps, so a wide channel
gets no more airtime than a narrow one; more airtime comes only from more
*separated* channels. The ≥200 kHz guard in `afa.md` §2 exists so no 200 kHz
measurement window can straddle two channels and sum their on-time against one
cap.

100 s/h is 2.78 %. That is not a limit a busy node reaches by accident — a
saturated burst detour walks into it — so the ledger is a real gate, not a
formality. It is also the number that makes §5's calibration mismatch matter.

### 2.6 The dialogue exemption

The 4 s Ton_max for a "dialogue or polling sequence" is the regulator
acknowledging that a request/response exchange cannot re-run CCA between every
frame without the gaps swallowing it. Within a dialogue, subsequent
transmissions may follow without a fresh CCA up to that 4 s **[verify — the
exact conditions, in particular the maximum permitted gap between frames and
whether both ends may rely on it]**.

This is load-bearing for
[`iface-lora/proper-air-protocol.md`](iface-lora/proper-air-protocol.md): that
design's burst round is a manifest, a back-to-back train and a bitmap ack,
explicitly with **no carrier sense from the manifest onwards**, sized at ~1.2 s.
That structure is legal under PSA only as a dialogue, and both directions
together must fit inside 4 s. Reading this clause precisely is a prerequisite
for building the burst channel, not a detail to settle afterwards — if the
exemption is narrower than assumed, the train needs a CCA and a ≤5 ms dead time
between every frame, and the entire timing argument collapses.

### 2.7 What gets declared

PSA is manufacturer-declared and then tested against the declaration: the CCA
threshold and measurement bandwidth, the dead time, Ton, Toff, the channel list,
and the AFA behaviour. AFA itself imposes no minimum channel count and no timing
rules (EN 300 220-2 §4.5.4); its single hard rule is that operating channels
shall not overlap.

Same consequence as §1.3 point 3: these must be compiled-in constants, not
`s.lora.*` keys.

## 3. What the hardware allows

ESP32-S3 plus SX1262, which is the T3S3 and the T-Deck. Everything in this
chapter is about whether §2's sequence is physically achievable and at what
cost.

### 3.1 One demodulator

The SX1262 has **a single demodulator**: it listens for exactly one
`(SF, BW, frequency)` at a time. Two nodes on different spreading factors cannot
hear each other at all — not slower, silent. This is the fact that shapes
everything else, and it is why `proper-air-protocol.md` negotiates every detour
on the hailing channel before either end retunes.

**A second radio does not cost an SPI host.** SPI is a bus: `spi_master` drives
several devices off one controller with a chip-select each, and every radio
brings its own `DIO1` interrupt and `BUSY` line, so two SX1262s share SCK/MOSI/
MISO on SPI2 and remain individually addressable and individually
interrupt-driven. The T3S3's split — SX1262 on SPI2, microSD on SPI3 — is a
choice to avoid arbitration between two very differently-behaved devices, not a
constraint on radio count.

What actually bounds it:

- **GPIOs**, 4 per radio beyond the shared three: NSS, DIO1, BUSY, RESET.
- **Bus arbitration latency.** A transaction in flight for radio B delays radio
  A's RSSI read. Harmless for ordinary traffic, but §3.3's CCA→transmit window
  is the one place where a few hundred µs of queueing is a compliance failure,
  so that window needs the bus to itself.
- **RF isolation, which is the real ceiling.** Two radios 700 kHz apart on one
  board cannot transmit and receive simultaneously. Antenna-to-antenna isolation
  at this size is perhaps 15–25 dB, so +22 dBm out of one arrives at the other's
  input around 0 dBm against a wanted signal near −120 dBm — not desense,
  total blocking, and close enough to the part's absolute maximum RF input to be
  a damage question rather than only a performance one **[verify the SX1262
  absolute-max input]**. Two receivers on two channels is fine; one transmitting
  while the other listens in-band is not, absent filtering that does not fit on
  these boards.

So two radios genuinely buy two *listening* channels, and that is worth having
(§3.5). They do not buy transmit-while-listening in 863–870.

### 3.2 Retune cost

Within 863–870 MHz there is nothing expensive to do. The band is a single
SX1262 image-calibration band, so a frequency change inside the nine-channel
plan needs **no recalibration** — that ~3.5 ms **[verify]** cost is paid once at
startup, not per hop.

A retune is `SetStandby` → `SetRfFrequency` → `SetRx`, three SPI transactions.
Measured in this build a RadioLib SPI transaction is ~55 µs of APB hold at
80 MHz, dominated by framing and BUSY handling rather than by the 5 bytes of
payload; the PLL settle adds tens of µs **[verify against the datasheet's
transceiver-timing table]**.

**Call it ~0.25 ms per retune.** Against a 5 ms dead-time allowance that is
comfortable — the retune belongs *before* the CCA, not between the CCA and the
transmit, so it is not even competing for that budget (§2.1). **AFA is cheap on
the transmit side.** The hard part is reception, and that is §3.4.

### 3.3 The 160 µs and the 5 ms, on FreeRTOS

Both regulatory intervals are far below the 100 Hz FreeRTOS tick this build
runs. Neither can be built out of `vTaskDelay`, and the existing CSMA machine —
which is entirely tick-paced — cannot be stretched to produce them.

The 160 µs is easy and almost free: **three back-to-back `getRSSI(false)` reads
span ~165 µs** at 55 µs per transaction, which is a defensible spanning
measurement rather than a point sample plus a delay. The cost is already
budgeted; today's single sample is one third of it.

The 5 ms is where the platform fights back. Threats to the CCA→carrier-up
window, in rough order of how likely each is to bite:

- **Flash operations block the instruction cache.** A storage commit erasing a
  sector stalls any code executing from flash for milliseconds. This alone can
  blow the budget, and it is triggered by ordinary unrelated activity. The final
  sequence has to be `IRAM_ATTR` and must not touch flash-resident data.
- **WiFi and BT.** On a build with `spangap-net` staged, the radio stack takes
  CPU in bursts that are not bounded at 5 ms, and it runs at high priority.
- **Task preemption generally.** Anything above the LoRa task's priority landing
  between the CCA and `startTransmit` is a violation.
- **DFS and light sleep.** Sensing is deliberately read at the DFS floor today;
  the CCA→TX window needs an `esp_pm_lock` held across it or the CPU may be at a
  fraction of full clock and the wake latency counts against the budget.

The shape of the fix: the backoff machine grants **candidacy**, and a short
final sequence — three RSSI reads, threshold compare, `startTransmit` — runs in
one uninterrupted stretch with the scheduler held off and the PM lock taken.
That stretch is ~300 µs, short enough that suspending preemption across it does
not disturb WiFi. SPI inside it must be polling-mode, not DMA-with-interrupt.

Then **measure it**: toggle a GPIO at last-sample-complete and at carrier-up,
catch it on a scope, and declare the measured worst case over a long run with
storage and WiFi active — not the intended one.

### 3.4 How many channels can we monitor at once?

Three separate answers, and the first is the one that surprises people.

**Regulatory: unlimited.** Tcum_on, Toff and the CCA all constrain
*transmission*. Nothing in EN 300 220 limits listening. Monitoring is a link
availability problem, not a compliance one, and no amount of receive activity
touches the 100 s/h budget.

**Simultaneously, in hardware: one per radio.** A radio costs a chip select on
the shared bus, four GPIOs, and the in-band transmit restriction — §3.1.

**By scanning: still about one, and here is why.** To receive a packet the
modem must be tuned to its channel for enough of the preamble to detect it.
Round-robin over N channels with dwell `d` symbols and retune time `T_r`, a
preamble of `P` symbols and symbol time `T_s = 2^SF / BW`:

```
N · (d·T_s + T_r) + d·T_s  ≤  P·T_s
```

With the default 8-symbol LoRa preamble and detection needing roughly 4 symbols
**[verify — the SX126x LoRa preamble detector's actual symbol requirement]**,
the `T_r` term aside, this is `N ≤ P/d − 1 = 1`. **The result is independent of
SF and BW**, because dwell and preamble scale together — scanning viability is
set by the *ratio* `P/d`, not by the modem config. Retune time then eats into it
from below, and worst at the fast end: at SF5/BW500 the symbol is 64 µs, so a
4-symbol dwell is 256 µs against a 250 µs retune, halving N. Fast modes are
doubly bad at scanning.

Worked numbers, 8-symbol preamble:

| Mode | T_s | preamble | 4-sym dwell | retune | N |
|---|---|---|---|---|---|
| SF7 / BW500 | 256 µs | 2.05 ms | 1.02 ms | 0.25 ms | 0 |
| SF7 / BW125 | 1.02 ms | 8.2 ms | 4.1 ms | 0.25 ms | 0 |
| SF12 / BW125 | 32.8 ms | 262 ms | 131 ms | 0.25 ms | 0 |

Zero, meaning the scan cannot even revisit a single other channel without
risking a miss. To scan all nine you would need `P ≥ 9·(d + T_r/T_s) + d ≈ 45`
symbols at high SF, so a **64-symbol preamble** — eight times the default. At
SF7/BW500 that is 16.4 ms of preamble on every transmission, against ~8 ms for
a 255-byte payload at F4. **The preamble would cost more airtime than the
data**, on channels whose entire purpose is peak rate, and it would be charged
against the 100 s/h ledger.

So: **do not scan the nine channels.** The architecture the hardware wants is
exactly the one `proper-air-protocol.md` already describes — camp on the
reticulum channel, negotiate the detour there, retune both ends by agreement,
return. Nobody listens on an AFA channel except by appointment. The two-regime
split in this file is not only a regulatory convenience; it is what a
single-demodulator radio forces.

If a second radio is fitted, the natural division is one camped on the reticulum
channel permanently and one roaming the nine. That removes the blackout while
the roaming radio *receives*, but not while it transmits: §3.1's isolation
problem means a +22 dBm burst on an AFA channel blocks the hail-camped receiver
a few MHz away just as thoroughly as retuning it would. Two radios convert the
blackout from "whenever we visit an AFA channel" to "whenever we transmit on
one", which is a real gain — the listening half of a burst detour stops costing
hail coverage — but it is not the clean separation it first looks like.

### 3.5 Dipping out of the hailing channel

The scanning arithmetic above rules out *monitoring* the nine, but it does not
rule out a brief excursion. Taking the reticulum channel at SF7/BW125 — the
question being whether we can leave, take one RSSI measurement on some other
channel, and come back without ever missing a preamble:

```
retune out    SetStandby → SetRfFrequency → SetRx      ~0.23 ms
CCA           3 × getRSSI(false), ≥160 µs               ~0.17 ms
retune back   SetStandby → SetRfFrequency → SetRx      ~0.23 ms
                                                       ─────────
                                                        ~0.63 ms, call it 0.7
```

Against SF7/BW125, where `T_s` = 1.024 ms and an 8-symbol preamble is
**8.19 ms** of up-chirps. Detection needs a contiguous `d` symbols inside that
window, so the largest absence that can never cause a miss is
`(P − d)·T_s` = **4.10 ms** at `d` = 4, or 6.14 ms at `d` = 2.

**So yes, comfortably — one dip costs 0.7 ms of a 4.10 ms budget, 17 % of it.**

**The asymmetry is why.** A CCA needs 160 µs; preamble detection needs 4
symbols, which at SF7/BW125 is 4.10 ms — **25× longer**. That ratio is what
makes §3.4's monitoring impossible and this excursion nearly free. We are not
trying to *hear* anything on the far channel, only to measure energy, and energy
has no acquisition time.

**Where this matters** is the aborted detour. The mandated sequence in §2.1
retunes, assesses, and transmits; if the CCA comes back busy there is nothing to
transmit and we return to hail having been away ~0.7 ms. **A refused detour
costs no hail coverage**, so the CCA can be honoured strictly — deferring and
retrying — without the compliance path quietly eating into the channel we
actually camp on. That is the only reason the arithmetic is worth having: it is
not a licence to go looking around the nine channels, and §2.1's path is not to
be extended into one.

**The dip must be gated on the receiver being idle.** Leaving RX mid-packet
destroys it, and unlike a preamble there is no partial-detection argument to
fall back on. `PreambleDetected` / `HeaderValid` must both be clear before the
excursion, and once a header is valid we are committed for the whole payload —
~400 ms for 255 bytes at SF7/BW125. The existing `splitPending` half-duplex
guard is the same shape and the same place to hang it.

The budget scales with `T_s`, so a slower hailing channel is strictly easier and
the arithmetic above is the tight case. It would stop being comfortable only if
the hailing channel moved to BW500, where the 4-symbol budget falls to 1.02 ms
and a single 0.7 ms dip consumes 69 % of it.

## 4. Sensing techniques

### 4.1 Energy detect

The regulatory CCA is an **energy measurement** — total power in the measurement
bandwidth against an absolute threshold, carrying no notion of what the energy
is. On our hardware that is `getRSSI(false)` (the "current channel" overload,
distinct from the base `getRSSI()` which reports the last packet's RSSI), one
SPI transaction while the modem sits in continuous RX.

- **It is the only sense the regulation recognises.** Everything below is
  additional, not alternative.
- **It sees everything**, including non-LoRa ambient — RFID interrogators in
  865–868, other SRD traffic, our own spurs. A feature for legality, a nuisance
  for throughput.
- **It sees nothing below the noise floor**, which is where LoRa lives (§4.2).
- **It must be on the target channel**, hence the retune ordering in §2.1.

### 4.2 The below-noise-floor problem

This is the central technical tension of the whole file.

**The regulatory threshold is far above thermal noise.** At BW500 it is
−75 dBm. Thermal noise in 500 kHz with a 6 dB noise figure is
`-174 + 10·log₁₀(500 000) + 6 ≈ -111 dBm`. The threshold sits **36 dB** above it.

**LoRa demodulates below the noise floor** — SF7 at −7.5 dB SNR, SF12 at
−20 dB. A neighbour's SF7/BW500 transmission arriving at −118 dBm is perfectly
decodable by anyone tuned to it and reads as *silence* to a regulatory CCA. A
comfortable, high-margin link at −100 dBm also reads as silence: 25 dB clear of
the threshold.

So a compliant CCA will hand us the channel **in the middle of somebody else's
packet**, routinely. The regulation is doing its job — protecting the band from
energy, not arbitrating between two LoRa nodes — but it means "may we transmit"
and "should we transmit" are computed from entirely different measurements.

| | Regulatory CCA | Our tracked-floor test |
|---|---|---|
| Reference | absolute, −81/−78/−75 dBm | tracked noise floor + 6 dB |
| Typical floor | — | ~−105 dBm initial estimate |
| Effective threshold | −81 dBm @ BW125 | ~−99 dBm @ BW125 |
| Purpose | legality | not colliding |
| Blind to | sub-threshold LoRa (most of it) | strong non-LoRa it can't distinguish |

Ours is ~18 dB stricter, which is the right direction — PSA sets a ceiling on
what we may ignore, never a floor — but it still cannot see a link running 20 dB
under the noise.

**This is observable on the LoRaMon graph, and it had teeth.** A frame can be
received end to end without the carrier sense registering anything: the signal
never crossed the busy threshold, and the sense is a point sample once per slot
rather than a continuous watch, so even a strong frame can fall between two of
them. Under APPC that was not merely a missed observation. The contention window
is a wall-clock target accumulated *while the medium reads free*, so an unseen
reception had its entire time on air banked as credit toward our own next
transmission — **hearing a neighbour out made us more eager to transmit, not
less.** Exactly backwards.

### 4.2.1 The receiver is a channel sensor

The fix is to stop treating carrier sense as the only source of channel state.
The receiver holds the one piece of evidence the sense structurally lacks: it
decoded a frame, therefore the medium was occupied, and it knows for exactly how
long because the time on air is already computed for the record.

So every completed reception now reports the medium it held — CRC failures and
our own air protocol included, since a corrupt frame occupied the channel just
as long and a frame we consumed ourselves was still somebody transmitting. Two
corrections follow: the next sense reads busy, putting the inter-frame space
after the frame rather than over the top of it; and the free-medium credit
banked during the frame is given back.

This is free, exact, and available on every frame we receive. It does not close
§4.2 — a frame addressed to someone else that we cannot decode is still
invisible, and the hidden node is untouched — but it converts the traffic we
*can* hear from something the sense might miss into something it cannot.

### 4.3 LoRa CAD

The SX126x has hardware **Channel Activity Detection**
(`setCad(symbolNum, detPeak, detMin, exitMode, timeout)`, via RadioLib's
`startChannelScan()` / `scanChannel()`, IRQ on `CadDone` with `CadDetected`). It
correlates against the LoRa preamble chirp at the **currently configured SF and
BW** and inherits LoRa's processing gain, so it detects exactly the
below-the-floor transmissions §4.2 says RSSI misses.

**It is not usable here, and the reason is structural rather than a matter of
cost.** CAD answers "is there a LoRa preamble at *this exact* SF and BW",
and that question is either unanswerable or already answered everywhere we would
want to ask it:

- **On the nine AFA channels we do not know what anyone else is running.**
  These are ordinary licence-exempt SRD spectrum with arbitrary occupants —
  LoRaWAN gateways on their own SF ladder, metering and telemetry FSK, the RFID
  interrogators that dominate 865–868, proprietary everything. We have no way to
  know their modulation, let alone their spreading factor. CAD tuned to our
  config is blind to all of it — not less sensitive, blind — so it returns
  confident negatives on a channel that may be saturated. **A sense that is
  wrong in the reassuring direction is worse than no sense.**
- **On the reticulum channel we do know the config, and the receiver is already
  using it.** We camp there in continuous RX at the shared SF and BW, so the
  modem's own `PreambleDetected` and `HeaderValid` report exactly what CAD would
  report, for free, without leaving RX. CAD adds value only when you are *not*
  already listening on the config you want to detect, which on the one channel
  where its precondition holds is never.
- **It can never serve as the regulatory CCA** in any case: a sense that ignores
  above-threshold non-LoRa energy is not an energy measurement.

The cost argument is secondary but points the same way — standby → DIO config →
clear IRQ → `setCad` → read result is ≈6 transactions against 1, and each sense
drops the receiver out of RX, so a CAD-driven loop deafens itself at exactly the
moment it is characterising the channel. Measured, not assumed.

**So RSSI is the only realistic sense we have**, on both regimes, and §4.2's
blindness to sub-noise-floor LoRa is not a gap we can close — it is a permanent
property of the design to be absorbed by retransmission (§4.5) rather than
engineered away.

### 4.4 Sync-word-aware occupancy

For the GFSK modes the packet engine offers `PreambleDetected` and
`SyncWordValid` IRQs. Unlike CAD these give **identity**: a valid sync word says
the energy is a frame in *our* network (`afa.md` §5.4), so we can distinguish
traffic we should defer to and could decode from unknown energy.

- Own-network traffic → deferring is productive; we'd receive it anyway.
- Foreign energy above threshold → must defer (regulation), nothing to receive.
- Foreign energy below threshold → invisible either way.

It is not a pollable sense: it fires only once a preamble has been detected and
the correlator has locked, which is a statement about a frame already arriving.
Free and passive, but not something a backoff machine can ask a question of —
and since the only backoff machine we run is the hailing channel's, that is
where the distinction has anywhere to go. On the nine it changes nothing: the
CCA is an energy threshold and does not care whose frame it is.

### 4.5 Summary

| Technique | Sees weak LoRa | Sees non-LoRa | Identifies sender | Regulatory | Cost/sense | Use |
|---|---|---|---|---|---|---|
| RSSI energy detect | no | yes | no | **required** | 1 SPI txn | **the CCA** |
| Tracked-floor RSSI | marginally | yes | no | no standing | 1 SPI txn | **the backoff** |
| Completed reception | **yes, if for us** | no | yes | no | free | **the backoff** (§4.2.1) |
| LoRa CAD | own cfg only | no | no | no | ~6 txn + RX drop | none (§4.3) |
| Sync-word IRQ | n/a | n/a | yes | no | free, passive | passive, RX-side |

**Two senses ship and both are RSSI** — an absolute-threshold measurement for
legality and a tracked-floor one for throughput, differing in reference rather
than in mechanism — **plus the receiver itself**, which is not a sense at all
but a report of channel state after the fact (§4.2.1). That is the whole sensing
story.

Two limits come with it and neither is closeable. **Sub-noise-floor LoRa is
invisible** (§4.2), and nothing available to us sees it on a channel whose
occupants we cannot characterise. **The hidden node survives everything**: every
technique here measures the channel at the transmitter while the collision
happens at the receiver. LBT reduces collisions, it does not eliminate them.
Both are absorbed downstream — by the bitmap-ack retransmission in the burst
protocol, and on the reticulum channel by Reticulum's own retries — rather than
by any improvement to sensing.

## 5. APPC — the backoff we have, and what it isn't

**The mechanism is APPC — "adaptive p-persistent CSMA" — and the coin flip in
that name does not exist.** The acronym was coined in this straddle; RNode
firmware, where every constant in it comes from, has no name for it and treats
it as simply how CSMA works there. Textbook p-persistent CSMA (Kleinrock &
Tobagi, 1975) gates each transmit opportunity behind a probability *p*. What
APPC does is draw the random backoff from **one of four contention-window
bands**, selecting the band by how much of the recent past *this radio* spent
transmitting. Same goal — load-responsive politeness — reached by sizing the
window rather than by rolling dice. The name is a label for the feature.

| Own airtime | Band | Window drawn |
|---|---|---|
| ≤ 7 % | 1 | 0–13 slots |
| 8–38 % | 2 | 15–28 slots |
| 39–77 % | 3 | 30–43 slots |
| ≥ 78 % | 4 | 45–58 slots |

The load signal is **our own transmit duty cycle**, not observed channel
occupancy — also RNode's choice. It holds up because every radio on a congested
channel transmits more, retries included, so own-airtime tracks aggregate load
closely enough to act on, and it costs no extra sensing since each frame's
time-on-air is already computed.

**APPC runs on the reticulum channel and only there.** The duty cycle says when
we *may* transmit; APPC says when we *should*, and its entire input is our own
history — it never looks at the channel at all. The nine AFA channels have no
equivalent and are not getting one: their transmit path is §2.1's, and the
arbitration that would otherwise belong on them already happened in the blip.

That confinement suits the mechanism. The bands were calibrated by RNode for a
regime with no airtime cap, and against a hailing channel at 10 % duty cycle
they sit about right — band 2 opens at 8 % own airtime, roughly where the budget
itself starts to bind, so the backoff lengthens as the ledger tightens rather
than fighting it.

## 6. Design sketch

Nothing here is built.

### 6.1 The transmit path

The two regimes share the ledger and nothing else.

```
reticulum channel                  AFA channel
──────────────────────────────     ──────────────────────────────
ledger: under the duty cycle?      ledger: Tcum_on < 100 s, Toff elapsed?
                                     no → another channel, or wait
APPC / exponential backoff,
tracked-floor sense                retune to target        ~0.25 ms
  → grant
                                   ── no yield from here ──
startTransmit                        PM lock, scheduler held, IRAM only
                                     3 × getRSSI(false), ≥160 µs
                                     any sample > absolute threshold
                                       → abort, back to hail (§3.5)
                                     all clear → startTransmit
                                   ── dead time ends at carrier up ──

TxDone: credit the ledger          TxDone: credit the ledger, stamp Toff
```

The left column has a contention mechanism and a lax, self-referential
threshold; the right has no contention mechanism and a hard, absolute one. They
are not two configurations of one path and should not be written as one.

The invariant to hold and to state in the code: **between the final CCA and
`startTransmit` there is no yield, no queue re-check, no flash access, and no
SPI other than the transmit setup itself.**

### 6.2 The airtime ledger

Per channel, sliding over an hour, credited with the same
`loraAirtimeSeconds()` figure APPC already computes at TxDone. Both regimes need
one; only the cap differs (10 % of the hour vs 100 s).

RNode's full **480-bin ring at 7500 ms** is exactly the right shape and exactly
what this straddle currently discards — we keep only the two live bins APPC
reads, because the long-term duty-cycle lock upstream feeds with it was never
implemented. This is that consumer arriving. 480 bins × 10 channels × 4 bytes is
~19 kB, affordable; but 7.5 s granularity against a 100 s budget is 7.5 %
worst-case quantization error, so the bins want to be finer *or* the effective
cap set a bin's worth below the legal one.

It drives exactly one behaviour: **refuse when the channel is at cap.** A
refusal on one of the nine sends the detour to another of them, which is AFA
working as intended and not a balancing policy — first channel with budget is a
fine rule, and any cleverer one would be the fairness algorithm these channels
are not getting.

Bins must survive a radio restart or the cap is evadable by accident, which is
worse than evadable on purpose. Across a *reboot* is a judgement call: a device
that reboots hourly and starts each hour with a clean budget is not obviously
compliant. **[verify]** whether the standard says anything about power-cycle
behaviour; absent that, persisting to storage on a coarse schedule is the
defensible reading — and note that the storage write itself is a threat to §3.3's
dead-time window, so it must not happen anywhere near a transmit.

### 6.3 Channel state

Per channel: `last_tx_end_ms` (Toff), `spent_this_hour`, the bin ring, the
absolute CCA threshold for its bandwidth, and which regime it is under. Ten
entries — the reticulum channel plus the nine — with the first flagged as
duty-cycle, no-AFA, never-leave. There is no per-channel occupancy estimate and
no scan state, because nothing reads them.

### 6.4 Task ownership

> The build order, the `s.lora.<n>.afa` gate, regime 0 and the demonstrator UI
> are in [`iface-lora/afa-demonstrator.md`](iface-lora/afa-demonstrator.md).
> Where that file gives a concrete size or cadence it supersedes the sketch
> here.

Today one FreeRTOS task services every radio and also carries everything built
on top of them — the neighbour table with its inline Ed25519 announce
verification, the LoRaMon recorder, stats publication, rfprobe, the RNode
endpoint's config writes. §3.3 says the transmit path needs a stretch that
touches no flash and yields to nothing; that stretch currently shares a loop
with a recorder whose ring *is* a storage subtree.

**Split by criticality: one low-level radio task, everything else a caller.**

| Radio task | Everyone else |
|---|---|
| chip bring-up, `applyConfig`, retune | who to talk to, at what power, when |
| packets in — read, timestamp, rssi/snr | parse, verify, classify, route |
| packets out — CCA, dead time, key up | queueing, framing policy, rate choice |
| quick measurements — `getRSSI(false)`, the CCA sequence, §3.5 dips | what to do with the numbers |
| the CSMA machine and its sensing cadence | APPC band constants, thresholds |
| ledger check and in-RAM bin ring | ledger **persistence** to storage |
| `splitPending`, Toff stamps, time-on-air at TxDone | LoRaMon ring, neighbour table, stats, telemetry |

The line is: **anything that must complete in bounded time next to the chip
stays; anything that allocates, verifies a signature, or touches flash leaves.**
Ed25519 verification and LoRaMon's persistence are the two that most obviously
have to move, and moving the latter is what makes §6.2's "the storage write must
not happen anywhere near a transmit" true by construction rather than by
scheduling luck.

**Still one task for all radios, not one per radio.** Sharing a task serialises
the SPI bus by construction, so a sibling radio physically cannot issue a
transaction between another's final CCA and its `startTransmit`. Peer tasks on
one bus would turn that into mutual exclusion we have to build and prove, in the
one window where the failure mode is a compliance breach. §3.1's isolation limit
caps what concurrency would buy in any case. Per-radio *state* — §6.3's table,
the ledger, the Toff stamps — is already the shape; per-radio *tasks* are not.

Note the dead time itself is safe either way once the window is a
scheduler-suspended critical section: sibling work cannot intrude on it. Task
structure governs how promptly the window opens and who else is on the bus, not
whether the window holds.

**The hand-off is where this can go wrong.** Per-frame records leaving the radio
task must go through a bounded queue with an explicit drop policy — if a slow
flash write can backpressure the producer, the coupling we removed is back, with
the recorder now able to stall a transmit. Dropping LoRaMon records under
pressure is correct and must be counted; blocking the radio task is not.

## 7. Open questions

1. **Whether LBT without AFA buys anything at all** (§1.3). Table B.1's
   alternative to the duty cycle reads as LBT+AFA, so the working assumption is
   that a fixed-frequency node has no route out of its duty cycle. Confirm
   against clause 5.21 rather than against `afa.md`'s summary of the same cell.
2. **Where the reticulum channel is placed, and that entry's real limits**
   (§1.3). Sets the duty cycle it lives under; currently a user setting with no
   default.
3. **Whether one device may declare duty cycle on one channel and LBT+AFA on
   nine** (§2.2). The two-regime design assumes yes throughout.
4. **The dialogue exemption's exact conditions** (§2.6) — gates the burst
   protocol.
5. **The adaptive-power threshold relaxation** (§2.3) — couples PSA to
   `adaptive-power.md`.
6. **CCA measurement bandwidth** — must the receiver bandwidth match the
   transmission's OBW or merely cover it? Decides whether one wide sense can
   clear a narrow transmission.
7. **Sliding vs fixed hour** (§1.3) — materially different implementations, and
   the answer applies to both regimes' ledgers.
8. **Ledger persistence across power cycles** (§6.2).
9. **Ton_max at high SF.** A 255-byte SF12/BW125 frame is well past 1 s of air.
   Either the PSA path caps frame size per modem config, or high-SF rungs are
   PSA-ineligible — which contradicts the single-declaration reading. Needs
   resolving before the modem ladder is frozen.
10. **The SX126x preamble detector's real symbol requirement** (§3.4). The
    scanning arithmetic is only as good as that number.

## 8. Sources

- ETSI EN 300 220-1 V3.1.1 (2017-02) — clause 5.21 (tables 45, 46, 48),
  clause 5.14 (table 32)
- ETSI EN 300 220-2 V3.2.1 (2018-06) — annex B table B.1, clause 4.5.4 (AFA)
- CEPT/ERC Recommendation 70-03 — the CEPT-wide position and the national
  entries, including the 869.4–869.65 allocation §1.3 depends on
- SX1261/2 datasheet — `SetCad` parameters, `GetRssiInst`, image-calibration
  bands, transceiver timings, PA ramp
- RNode firmware — `Config.h` *CSMA Parameters*, `update_csma_parameters()`,
  `add_airtime()`; the 480-bin ring §6.2 wants back
- `iface-lora/INTERNALS.md` §6 and §6a — the CSMA machine and APPC as built
- `hw-lilygo-t3s3-sx1262/INTERNALS.md` §2 — the two SPI hosts §3.1 counts
- Semtech AN1200.22 / SX127x datasheet — CAD behaviour and its limits

Both ETSI documents are the editions cited in `afa.md`; check for newer
revisions before certifying anything.
