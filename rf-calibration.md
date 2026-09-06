# RF calibration: antenna-referenced power and RSSI

## The invariant

Every dBm that crosses the `iface-lora` boundary is referenced to the **antenna
connector**. RadioLib's register-referenced numbers exist only inside the
conversion layer; no caller outside it ever sees one. The boundary stops at the
connector — antenna gain and feedline belong to the deployment, not the board,
and never enter a straddle.

`INTERNALS.md` §4b already states this invariant. Three paths do not honour it:

- The power a node **announces** is the request, not what leaves the antenna
  (`supe_engine.cpp` `h.pwrDbm = txp`, fed from `r->txPwrNow`), so every peer's
  `us->them` path loss is wrong by the conversion error.
- The **floor** is the chip's, not the antenna's (`apClamp` uses `AP_FLOOR_DBM`
  and `checkOutputPower`, both RadioLib's range), so the power controller
  converges on a number the board cannot produce.
- **RSSI** is the chip's reading, with nothing subtracting the front end's
  receive gain.

A front-end module (FEM — external power amplifier, low-noise amplifier and
antenna switch) makes all three visible at once. On a Heltec V4 in a mesh that
has settled at the floor, the node announces −9 dBm, radiates somewhere near
+6 dBm, and reads RSSI 20 dB hot. Its neighbours compute path losses ~25 dB
below the truth, in both directions, and the mesh believes it is the
best-connected node on the air.

## A scalar gain is not enough

Subtracting one gain figure from the request is the model Meshtastic uses
(`TX_GAIN_LORA`) and the model our `FEM_DECLARED` path uses. It fails on exactly
the hardware we have. Meshtastic, on the Heltec V4:

> Our TX_GAIN_LORA setting assumes a linear increase in power on top of
> SX126X_MAX_POWER, while the power amp behaviour in this case is non-linear,
> and affected by an attenuator in between.

Their stop-gap was pinning `SX126X_MAX_POWER = 11`, and the compensation has
broken twice since (firmware #8276, #10022).

The error being corrected is not the FEM's alone. The SX1262's own
set-versus-actual output is several dB off at the extremes with no front end at
all: measured, `setOutputPower(20)` yields ~+14 dBm on healthy LilyGO T3 and
T-Beam modules (RadioLib #903). Modelling chip and FEM separately multiplies two
uncertain models. **The calibration is one end-to-end curve: register setting →
measured power at the connector.**

## Calibration is keyed on the detected part

One board target can carry more than one front end. The Heltec V4 ships a
GC1109 on revisions ≤ 4.2 and a KCT8103L on 4.3, on the same enable net, told
apart at boot by `femInit`'s pull-up sense. The two parts differ by 2 dB of
transmit gain and 3 dB of receive gain, and only one of them has a published
measured curve.

So the calibration selects on `r->femType`, at the moment `femInit` sets it and
`femBandSelect` refines `maxTxDbm` — the same runtime decision, one step
further. `CONFIG_LORA_TX_POWER_MAX` stops being the source of truth for the
ceiling and becomes what it reads as: a board and regulatory cap applied on top.

## The data, and where it comes from

Provenance travels with every number. Three grades: **measured** (someone put it
on an analyser), **datasheet** (the part's published figure), **uncalibrated**
(identity, and said out loud).

### Heltec V4 — transmit, GC1109 revision — *measured*

tinySA Ultra Plus + 30 dB pad, 869.6 MHz, ±1 dB (MeshCore #1708). Chain is
`SX1262 → 17 dB attenuator → GC1109`.

| chip dBm | 1 | 5 | 10 | 12 | 14 | 16 | 18 | 20 | 22 |
|---|---|---|---|---|---|---|---|---|---|
| antenna dBm | +7.0 | +12.2 | +20.3 | +22.5 | +24.3 | +25.4 | +27.2 | +27.7 | +27.2 |

Two properties the curve must be allowed to express:

- **Non-monotonic at the top, and the top is expensive.** Output peaks around
  chip 18–20 and everything above it buys current, not power: chip 22 delivers
  *less* than chip 20, and 18 / 22 / 24 all land on the same +27.2. A follow-up
  with an R&S NRX power meter across three boards put the whole 18 → 22 climb at
  ~1.1 dB, for **605 → 741 mA**. So `maxTxDbm` is the curve's *maximum*, not its
  last entry, a request at or above the ceiling resolves to chip 20 and never to
  22, and the inverse in general must return the **lowest** chip setting that
  reaches the requested power.
- **Net gain collapses at low drive** — 6 dB at chip 1 against the 13 dB the
  part's small-signal gain minus the pad predicts. Nothing below chip 1 has been
  measured anywhere, and the trend is falling, so extrapolating to the chip's
  −9 dBm floor is unsound.

Heltec's own lab reports 25.6 dBm ±2 at the 27 dBm setting, consistent with the
top of this curve.

### Heltec V4 — transmit, KCT8103L revision — *uncalibrated*

No measurement published. Datasheet arithmetic only: chip − 17 dB pad + 32 dB
gain, saturating at a figure the datasheet does not state. At the chip floor
that is `−9 − 17 + 32 = +6 dBm`.

That number is a bound, not a curve, and it is enough to act on: a board whose
quietest transmission is +6 dBm cannot honestly announce −9.

### Receive gain — *datasheet*

A part property, independent of the board's transmit pad:

| part | RX gain | NF | RX input P1dB |
|---|---|---|---|
| GC1109 | 17 dB | 2.0 dB | −10 dBm |
| KCT8103L (3.3 V) | 20.0 dB | 1.65 dB | not published |

The GC1109's `< 1.0 dB` low-loss bypass is its **transmit** bypass path. Its
receive path runs through the LNA (mode table: `CSD=1, CTX=0` is *Receive LNA
mode*). Both detected parts amplify on receive.

The −10 dBm input compression point matters for bench work: a node at arm's
reach presents −12…−25 dBm to the front end, so it is at or past the FEM's own
compression before the SX1262 sees anything. Readings taken at that range are
not evidence of anything.

### Every other board — *uncalibrated*

T-Beam Supreme, T-Deck, T3S3, RAK/WisMesh, XIAO, Waveshare: only datasheet
"22 ± 1 dBm" exists; the published comparison tables are datasheet-sourced, not
measured. They stay identity, marked uncalibrated. No numbers are invented.

### Prior art for the receive side

There is no per-board RSSI offset published for any of these boards. The
mechanism is standard, though — Semtech's own packet forwarder configures
`rssi_offset`, where `rssi_offset = −Rx_gain`. Same model, our own numbers.

## Control wiring, from the Heltec schematics

Traced from the net-label columns of `HTIT-WB32LAF_V4.3.pdf` and
`WiFi_LoRa_32_V4.2.pdf`. The two revisions differ in which signal the MCU owns,
not only in which pin carries it:

| net | V4.2 (GC1109) | V4.3 (KCT8103L) |
|---|---|---|
| rail `VFEM_Ctrl` | GPIO 7 | GPIO 7 |
| `PA_CSD` (shutdown) | GPIO 2, no pull | GPIO 2, 10 K pull-up to `Vfem` |
| `PA_CPS` (PA / TX bypass) | GPIO 46 | no MCU connection, pull not fitted |
| `PA_CTX` (TX/RX direction) | radio `DIO2` | GPIO 5, 10 K to GND |

The pull-up on `PA_CSD` is the detect sense, and it is the only difference the
boot-time probe can see. On the GC1109 board the direction line comes off the
radio's own `DIO2` — which is why that design needs only one MCU control line
beyond shutdown, and why the RF-switch row must not assume the second pin means
the same thing on both boards.

## The conversions

One layer, three entry points, replacing `femChipDbm`:

- `rfChipDbm(r, antennaDbm)` → register value. Inverse of the curve, clamped to
  the reachable range, resolving ties to the lowest setting.
- `rfAntennaDbm(r, chipDbm)` → what actually radiates. Forward curve.
- `rfRssiDbm(r, chipRssi)` → antenna-referenced level.

Whole dB throughout: the SUPE wire carries power as `int8_t` dBm, the register
takes whole dB, and ±1 dB measurement uncertainty dominates the rounding.

### Configuration

One string per radio, parts named inline so a board that ships two front ends
declares both without doubling the Kconfig blocks:

```
CONFIG_LORA0_TX_CAL="gc1109 1:7,5:12,10:20,12:23,14:24,16:25,18:27,20:28,22:27"
```

Entries separated by `;`. A part with no entry falls back to the declared-gain
model. Receive gain is compiled in, keyed by `femType`, since it is a part
property; a board override symbol covers odd wiring if one ever turns up.

## Denomination audit

Every quantity in the tree that carries a dBm, and what it is referenced to once
this lands. "Connector" is the target for all of them; the column that matters
is what has to happen to get there.

| quantity | today | to do |
|---|---|---|
| `s.lora.<n>.tx_power`, `r->cfgTxp` | connector | nothing |
| `lora.<n>.tx_power_max`, `r->maxTxDbm` | connector | comes from the curve's top, not `CONFIG_LORA_TX_POWER_MAX` |
| `lora.<n>.tx_power_min`, `r->minTxDbm` | — | new; the curve's bottom |
| `r->txPwrNow` | the request | the achieved power, `rfAntennaDbm(chip)` |
| SUPE `pwrDbm` (hail, gap, train, announce) | the request | follows `txPwrNow` |
| `AP_FLOOR_DBM` | chip | `r->minTxDbm` |
| `apClamp` → `checkOutputPower` | chip range applied to a connector value | clamp against the curve |
| `USE` / `e->apPwr`, `EST` / `peersEstimateCliff10` | connector, from the above | correct once the above are |
| `s.lora.assumed_peer_txp` (default 22) | connector, bare-chip guess | keep, but the default is a bare SX1262's ceiling and reads low on a mesh with amplified boards |
| `r->rssiLast`, `r->snrLast` (`lora_bridge.cpp:261`) | chip | `rfRssiDbm` at the read |
| `channelRssi()` | chip | `rfRssiDbm` inside the accessor |
| `r->noiseFloor`, `chFloor[]` | chip | follows `channelRssi` |
| `CSMA_NOISE_FLOOR_DBM` seed | chip | re-reference |
| bucket ring `rssiSum`, `rssiMin/Max`, `peersRecentSignal` | chip | follows `rssiLast` |
| `pairRssi`, `stepRssi`, `apRptRssi` and the losses off them | chip level vs connector power | both ends connector, so the loss is finally connector-to-connector |
| `lora.<n>.meas.<slot>.rssi` / `.loss_to` / `.loss_from` | chip / mixed | follows; this is what `lxmf ping` and the contact bars read |
| `stats.rssi_last`, LoRaMon records, `lora n` verbose | chip | follows |
| RNode `CMD_STAT_RSSI` (`RN_RSSI_OFFSET`) | chip | follows; the offset is protocol scaling and stays |
| `supeSensitivityDeci()` | a far end's receiver | unchanged — see below |
| `peersHeadroom10()` `floor10` | an SNR floor | unchanged — SNR, so invariant |
| SNR everywhere | ratio | **nothing** — a gain in front of signal and noise alike cancels; do not offset it |

Two results from working through this that are worth stating up front.

**The wire needs no change.** `supeEncLevel` spans −192 … +63 dBm
(`supe.h:87`), so a node announcing +27 or +30 fits with room to spare. Nothing
about the frame format moves.

**Most control decisions are invariant; the reported numbers are not.** Margin
is `rssi − sensitivity`, so a uniform offset applied to both leaves every
ratchet, cliff and derivation deciding exactly as it does now. Likewise CSMA:
`noiseFloor` is tracked from the channel reading itself and the busy test is
`rssi > noiseFloor + CSMA_RSSI_MARGIN_DB`, a relative comparison, so the offset
cancels and only the seed constant is absolute. What is *not* invariant is every
number a human or a peer reads — the announced power, both path losses, the
published measurements. That asymmetry is the argument for doing this in one
move: shift one side only and working decisions start breaking.

**The sensitivity model needs nothing, for a reason worth writing down.** The
first draft of this plan called for making `SUPE_NOISE_FIGURE_DB` per-radio, on
the Friis argument that a low-noise amplifier in front makes the system noise
figure the amplifier's rather than the chip's. Reading the callers says
otherwise: all four — `apOpenPower`, `apOpenPowerAt`, `apMissWasPower` and the
train-margin test — ask this about the **far** end, what power must reach a
peer for it to decode. A peer's front end is not ours to assume, and crediting
one with an amplifier it may not have would under-power the link, so a bare
radio's figure is both correct and the conservative way to be wrong.

Nothing needs our *own* sensitivity, because no decision in the tree compares a
level of ours against an absolute floor: margin is an SNR question
(`peersHeadroom10`) and carrier sense tracks its own floor. The constant stays,
with that reasoning recorded next to it.

## Saying how well we know it

"Connector-referenced, to the best of current knowledge" is only honest if the
knowledge shows. Each radio carries the grade of its own calibration —
**measured**, **datasheet**, or **none** (identity) — and surfaces it where the
numbers are read: the `femInit` banner, the `lora` CLI caption beside
`tx_power_max`, and a published `lora.<n>.cal` key so a UI can mark a figure as
uncalibrated rather than presenting a guess as a measurement.

This stays local. It is a property of our build and our board, not of the link,
and a peer receiving it could do nothing with it — a stated power is a stated
power. So no field is added to the wire for it.

## Changes, by file

1. **`lora_fem.cpp`** — parse and hold the curves; `rfChipDbm` / `rfAntennaDbm`
   / `rfRssiDbm`. The ported Meshtastic per-dBm gain arrays go: they are within
   ~1 dB of the measured curve from chip 10 up and **4–5 dB wrong below chip 5**,
   which is precisely where a settled mesh operates.
2. **`lora_power.cpp` `apApplyPower`** — `r->txPwrNow = rfAntennaDbm(chip)`.
   `txPwrNow` is already the single truth that the announced power and the
   LoRaMon record both read, so this one line carries the fix outward.
3. **`lora_power.cpp` `apClamp`** — floor on `r->minTxDbm`, not `AP_FLOOR_DBM`,
   and stop handing an antenna-referenced power to RadioLib's
   `checkOutputPower`, which judges it against the chip's own −9…+22. That
   mixing caps every derived request at +22 at the antenna on a board whose
   `maxTxDbm` is 27, so the Heltec's top 5 dB is unreachable through the
   adaptive path — the same domain confusion as the floor, pointing the other
   way.
4. **`lora.cpp`** — `r->minTxDbm = rfAntennaDbm(chip floor)`, published beside
   `tx_power_max` as `tx_power_min`; clamp `cfgTxp` at both ends with the same
   warning the ceiling gets.
5. **`lora_bridge.cpp:261` and `channelRssi()`** — convert once. These two cover
   every consumer: SUPE pairs, the bucket ring, CSMA, LoRaMon, the RNode
   endpoint.
6. **`supe.cpp`** — `SUPE_NOISE_FIGURE_DB` keeps the bare radio's figure, with
   the far-end reasoning above recorded beside it so it is not "corrected"
   later.
7. **`lora_csma.h`** — re-reference the `CSMA_NOISE_FLOOR_DBM` seed. Nothing
   else in CSMA changes: the busy test is relative to a tracked floor, so the
   offset cancels.
8. **Calibration grade** — `femInit` banner, `lora` CLI caption, published
   `lora.<n>.cal`.
10. **`lora_rfcal.{h,cpp}`** — the arithmetic, kept free of ESP-IDF, RadioLib
    and FreeRTOS the way `supe.cpp` is, so `test/rfcal_test.cpp` can exercise
    the inverse search, the interpolation and the parser on the host.
9. **`hw-heltecv4/straddle.yaml`** — the GC1109 curve, with its source in the
   comment beside the FEM block that already documents itself that way.

## Open items

- **Nothing is measured below chip +1 dBm** on any board. That is the whole
  bottom of the ladder. Until it is, `minTxDbm` should be the lowest calibrated
  point rather than an extrapolation.
- **No KCT8103L transmit curve exists.** Ours is the revision that needs it.
- **The fleet can calibrate itself.** Path loss is reciprocal, so every A→B /
  B→A disagreement across the neighbour matrix is a calibration error in that
  pair; with three or more nodes it is a least-squares solve for per-node
  transmit and receive offsets. Constraints: discard any pair whose RSSI is
  above about −20 dBm (receiver compression), and nominate one uncalibrated bare
  board as the reference, since the solve is determined only up to a common
  offset.

## Sources

- MeshCore #1708 — Heltec V4 measured TX power table
- Meshtastic firmware #8070 — Heltec V4 attenuator and PA non-linearity
- Meshtastic firmware #10022 — `limitPower()` / `TX_GAIN_LORA` compensation
- GC1109 preliminary datasheet Rev 0.9.2, Geo-chip
- KCT8103L specifications, Kxcomtech
- Heltec V4 test results, wiki.heltec.org
- RadioLib #903 — measured LilyGO module output power
- Semtech LoRa FAQ — `rssi_offset = −Rx_gain`
