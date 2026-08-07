# AFA — 868 MHz channel plan and modulation ladder

> Scope: **design intent, nothing built yet.** This file fixes the channel
> raster, the mode ladder, and the framing decisions so the implementation has a
> target. Regulatory figures are read from the standards named in §7; where a
> number here disagrees with those documents, the documents are right and this
> file is stale.
>
> AFA = Adaptive Frequency Agility; PSA = polite spectrum access (the
> listen-before-talk alternative to a duty cycle); CCA = Clear Channel
> Assessment; e.r.p. = effective radiated power.

## 1. Regulatory basis

Two documents govern everything below. **ETSI EN 300 220-2 V3.2.1 annex B,
table B.1** lists the EU-wide harmonised non-specific short-range-device bands
(mapped to EC Decision 2017/1483 band numbers) with, per band, the maximum
e.r.p. and whether PSA may substitute for the duty cycle. **ETSI EN 300 220-1
V3.1.1 clause 5.21** specifies what PSA costs.

Every 863–870 MHz entry that carries a duty cycle also permits PSA instead.

| Entry | Range | Width | Max e.r.p. | Duty cycle | PSA |
|---|---|---|---|---|---|
| K | 863.0–865.0 | 2 MHz | 25 mW | 0.1% | yes |
| L | 865.0–868.0 | 3 MHz | 25 mW | 1% | yes |
| M | 868.0–868.6 | 600 kHz | 25 mW | 1% | yes |
| N | 868.7–869.2 | 500 kHz | 25 mW | 0.1% | yes |

**868.6–868.7 and 869.2–869.4 are unavailable.** They are alarm and social-alarm
allocations, excluded from non-specific short-range-device use. They bound band
N on both sides, which is what makes channel 9 tight (§2).

Spectrum above 869.2 MHz is out of scope for this plan.

### 1.1 PSA parameters

Timing, EN 300 220-1 table 48:

| Parameter | Limit |
|---|---|
| Minimum CCA interval | 160 µs |
| Minimum deferral period | = CCA interval |
| Dead time (CCA end → transmit start) | declared, ≤ 5 ms |
| Ton_max, single transmission | 1 s |
| Ton_max, dialogue or polling sequence | 4 s |
| **Max Tcum_on** | **100 s per hour per 200 kHz of spectrum** |
| Toff_min, same operating frequency | 100 ms |

The cumulative limit is the one that shapes the design, and it is **measured
over** any 200 kHz portion of spectrum rather than allocated per channel width.
A channel wider than 200 kHz puts its full on-time into every 200 kHz portion it
covers, so widening a channel buys no airtime: a 500 kHz channel and a 200 kHz
channel are both capped at 100 s/h. Table 48 note 3 is the confirmation — more
AFA *channels* buy more accumulated transmission time, meaning more frequencies,
each occupying portions of its own.

This is why the ≥200 kHz guard in §2 matters beyond the non-overlap rule: it
guarantees no 200 kHz measurement window can span two channels, so each
channel's 100 s/h is independent. Packed tighter, a window straddling a boundary
would see both channels' on-time summed against one cap.

100 s/h is 2.78%, and it applies per channel rather than per band entry. That is
where PSA wins: the duty cycle would give the whole of band K 0.1% and the whole
of band L 1%, shared across every channel in them.

CCA threshold, table 45 referenced to the table 32 sensitivity limit
`-117 + 10·log₁₀(RB_kHz)` dBm:

| Transmit power | Threshold |
|---|---|
| < 100 mW e.r.p. | `-102 + 10·log₁₀(RB_kHz)` dBm |

Every channel in this plan is 25 mW, so only that row applies. Referenced to
0 dBd (+2.15 dBi); other antenna gains shift the threshold by the difference. At
125 kHz that is −81 dBm, at 500 kHz −75 dBm.

AFA itself imposes no minimum channel count and no timing constraints — it is
manufacturer-declared. The single hard rule is EN 300 220-2 clause 4.5.4.2:
**overlapping operating channels are not permitted.**

## 2. Channel plan

Placement rules: no raster, but ≥200 kHz of clear spectrum between the edges of
any two channels, no channel crossing a band-entry boundary, and the number of
500 kHz channels maximised.

| # | Centre (MHz) | BW | Span (MHz) | Entry | Max e.r.p. | Airtime |
|---|---|---|---|---|---|---|
| 1 | 863.350 | 500 kHz | 863.100–863.600 | K | 25 mW / 14 dBm | 100 s/h |
| 2 | 864.050 | 500 kHz | 863.800–864.300 | K | 25 mW / 14 dBm | 100 s/h |
| 3 | 864.750 | 500 kHz | 864.500–865.000 | K | 25 mW / 14 dBm | 100 s/h |
| 4 | 865.450 | 500 kHz | 865.200–865.700 | L | 25 mW / 14 dBm | 100 s/h |
| 5 | 866.150 | 500 kHz | 865.900–866.400 | L | 25 mW / 14 dBm | 100 s/h |
| 6 | 866.850 | 500 kHz | 866.600–867.100 | L | 25 mW / 14 dBm | 100 s/h |
| 7 | 867.550 | 500 kHz | 867.300–867.800 | L | 25 mW / 14 dBm | 100 s/h |
| 8 | 868.250 | 500 kHz | 868.000–868.500 | M | 25 mW / 14 dBm | 100 s/h |
| 9 | 868.950 | 500 kHz | 868.700–869.200 | N | 25 mW / 14 dBm | 100 s/h |

**9 × 500 kHz, uniform 25 mW, all on PSA. 900 s/h aggregate.**

Uniformity is worth something in itself: one power class, one channel width, one
spectrum-access regime, so the scheduler tracks a single 100 s/h counter per
channel with no special cases.

For comparison, duty-cycle operation over the same spectrum would yield 3.6 s/h
for band K, 36 s/h for L, 36 s/h for M and 3.6 s/h for N — 79.2 s/h in total,
and those are per-band budgets shared across the channels inside them, not
per-channel. PSA is worth roughly 11× here.

Channel 9 fills band N edge-to-edge with zero margin, bounded on both sides by
alarm allocations. This is the plan's weakest point and depends on out-of-band
emission performance. The fallback is 250 kHz at 868.950, giving 125 kHz margin
either side and leaving 8 × 500 kHz. Note the fallback costs **no airtime** —
a 250 kHz channel is capped at the same 100 s/h — so the only price is peak rate
on that channel.

### 2.1 Wide channels or many channels

Because the cap is per 200 kHz portion rather than per channel width, aggregate
airtime is set by how many *separated* channels exist, not by how much spectrum
they occupy. The same 5 600 kHz laid out as 200 kHz channels on a 400 kHz pitch
holds 14 channels in K+L+M plus 1 in band N — 1 500 s/h aggregate against this
plan's 900 s/h, a 1.7× gain.

The cost is peak rate: a 200 kHz channel admits BW125 modes only, capping a link
at SF5/BW125's 15.6 kbps against F4's 250 kbps here. This plan takes peak rate;
revisit if the traffic pattern turns out to be many slow flows rather than few
fast ones.

Nine is the ceiling. K+L+M is 5 600 kHz contiguous, and
`N × 500 + (N−1) × 200 ≤ 5600` caps that stretch at 8; band N contributes the
ninth. The 200 kHz left over in K+L+M cannot be filled either — any additional
channel needs its own 200 kHz guard first, so even a 125 kHz channel would need
325 kHz.

The 200 kHz separation at 868.5→868.7 lands on the alarm exclusion, so that
guard costs nothing that was usable anyway.

## 3. Modulation ladder

Reference is SF7/BW125. LoRa net bitrate is `SF × (4/5) × BW / 2^SF` at CR 4/5.

| Mode | Net bitrate | Raw on-air | Δ vs SF7/BW125 | Min channel |
|---|---|---|---|---|
| SF7 / BW125 | 5 469 bps | 6 836 bps | 0 dB (ref) | 125 kHz |
| SF6 / BW125 | 9 375 bps | 11 719 bps | +2.5 dB | 125 kHz |
| SF7 / BW250 | 10 938 bps | 13 672 bps | +3.0 dB | 250 kHz |
| SF5 / BW125 | 15 625 bps | 19 531 bps | +5.0 dB | 125 kHz |
| SF6 / BW250 | 18 750 bps | 23 438 bps | +5.5 dB | 250 kHz |
| SF7 / BW500 | 21 875 bps | 27 344 bps | +6.0 dB | 500 kHz |
| SF5 / BW250 | 31 250 bps | 39 063 bps | +8.0 dB | 250 kHz |
| SF6 / BW500 | 37 500 bps | 46 875 bps | +8.5 dB | 500 kHz |
| SF5 / BW500 | 62 500 bps | 78 125 bps | +11.0 dB | 500 kHz |
| **F2** (GFSK) | **125 000 bps** | 250 000 bps | **~+17.5 dB** | 500 kHz |
| **F4** (GFSK) | **250 000 bps** | 300 000 bps | **~+24.5 dB** | 500 kHz |

Both columns increase monotonically, so no mode is dominated and the table is a
usable rate-adaptation ladder end to end.

Every channel in §2 is 500 kHz, so the whole ladder is available on every
channel — the narrower modes simply occupy part of one. The "min channel" column
matters only if a future revision adds narrower channels.

The Δ column for LoRa rows is `10·log₁₀(BW/125 kHz)` plus 2.5 dB per spreading
factor below 7, from Semtech's required-SNR figures (SF7 −7.5 dB, SF6 −5 dB,
SF5 −2.5 dB). Those are solid. **F2/F4 are modelled** from Eb/N0 with an assumed
6 dB noise figure, 1.5 dB implementation margin and estimated coding gains —
±2 dB until measured, good enough to decide whether to build them, not good
enough to set a link margin from.

Cost per doubling changes character across the boundary: ~3.3 dB inside the LoRa
region, where spreading factor and bandwidth both buy processing gain, against
6–7 dB in the FSK region where there is none left. The 11 dB step from SF5/BW500
to F2 is the largest in the table and is where the modulation actually changes.

## 4. SX127x and SF6

SX127x supports SF6–SF12. **SF5 does not exist on the part** — no configuration
reaches it. SX126x supports SF5–SF12.

SF6 requires three specific things on SX127x:

- **Implicit header mandatory** — `ImplicitHeaderModeOn = 1` in RegModemConfig1
- **RegDetectOptimize (0x31)** → `0xC5`, against `0xC3` for SF7–SF12
- **RegDetectionThreshold (0x37)** → `0x0C`, against `0x0A` for SF7–SF12

Confirm those register values against the datasheet's spreading-factor-6 section
before committing. There is nothing exotic in the modulation; it is a
configuration recipe.

### 4.1 Implicit header consequences

Implicit header removes the on-air PHY header, which normally carries payload
length, coding rate and the CRC-present flag. Both ends must therefore agree
statically on coding rate and CRC presence, and **the receiver must know the
payload length before demodulation begins** — it writes that into
RegPayloadLength and the modem stops there. Three workable patterns:

1. **Fixed length** for every frame.
2. **Fixed maximum, padded** — receiver always reads N bytes, a length field
   inside the payload trims it. Costs airtime on short frames.
3. **Protocol-determined** — receiver sets RegPayloadLength from protocol state.
   Viable with a defined request/response protocol against a known peer.

Implicit header returns 20 bits in the time-on-air calculation, so short fixed
frames waste less than the padding suggests.

### 4.2 Cross-family incompatibility

Semtech **changed the SF6 modulation** in SX126x. This is a chipset-level
difference in the transmitted signal, not a register or header-mode mismatch —
no combination of settings bridges it. SX126x and SX127x will not interoperate
at SF6, and SF5 does not exist on SX127x at all. **SF7–SF12 interoperate
normally**, subject only to sync word translation (§5.4).

SF6 therefore requires matching silicon at both ends, which is a per-link
constraint rather than a fleet-wide one.

Driver support for SF6 on SX127x is uneven because it needs that special-case
path. Several simple Arduino LoRa libraries set the spreading factor without the
DetectOptimize and DetectionThreshold writes, which fails silently — the radio
transmits and never receives. Check those two registers before suspecting
anything else.

## 5. GFSK framing

```
|<-- not whitened -->|<------------- whitened ------------->|
[ preamble 0x55… ] [ sync word 1–8 B ] [ len ] [ addr ] [ payload ] [ CRC ]
   AGC, AFC,          bit-exact          ^ optional, depends on framing mode
   bit-clock          correlator
   recovery           → byte alignment
                      → whitening LFSR reset point
```

### 5.1 How the receiver anchors

The preamble and sync word are **not whitened**, which is what makes the
bootstrap possible:

1. **Preamble** is alternating 1010… — maximum transition density is what AGC
   settling, AFC pull-in and bit-clock recovery need. After it the receiver has
   bit timing but no byte alignment.
2. **Sync word** is a bit-exact correlation against 1–8 configured bytes, sent
   in the clear. Matching it establishes byte alignment and a known position.
3. **Whitening LFSR resets to its seed** at the first bit after the sync word
   and runs free from there, in lockstep at both ends.

This is an **additive (synchronous) scrambler**, not a self-synchronising one —
the LFSR output is XORed with the data and carries no feedback from received
bits. It recovers nothing on its own and depends entirely on both ends starting
at the same bit position. A missed sync word yields garbage with no partial
recovery.

Consequences for sync word choice: low autocorrelation sidelobes so the
correlator does not false-trigger, and no resemblance to the preamble, which
makes `0x55` unusable.

### 5.2 Length handling

Both modes are available, unlike implicit-header SF6:

- **Fixed** — `RegPayloadLength` (SX127x) or payload length in `SetPacketParams`
  (SX126x). Nothing on air; the engine counts N bytes after sync.
- **Variable** — the first de-whitened byte after sync is the length. Costs one
  byte, needs no pre-agreement.

### 5.3 Whitening

Hardware, both families. SX127x: `RegPacketConfig1`, `DcFree` field — 00 none,
01 Manchester, 10 whitening, 9-bit LFSR. SX126x: enabled in `SetPacketParams`,
with the seed in a writable 2-byte register at **0x06B8**.

Whitening earns its place beyond clock recovery and DC balance: unwhitened
repetitive data produces discrete spectral lines rather than a noise-like
spectrum, which inflates measured occupied bandwidth and pushes energy into
adjacent-channel and spurious limits. Given how tightly channel 9 sits against
the alarm allocations, that is a compliance concern and not only a link one.
Manchester is the alternative but halves throughput.

**The exact scope of whitening differs between the families** — specifically
whether it covers the length byte and the CRC, and the order in which CRC and
whitening are applied. Read this out of the datasheets rather than assuming. It
is one concrete reason SX126x and SX127x GFSK do not interoperate out of the
box, alongside the LFSR seed. Getting it wrong presents as correct sync
detection followed by consistently failing CRCs.

### 5.4 Sync word translation

SX127x uses a 1-byte sync word, SX126x a 2-byte register holding the same
on-air content. Decompose into nibbles and pad each with a low nibble of 4:

```
SX127x  0xYZ   →   SX126x  0xY4 0xZ4
```

| Network | SX127x | SX126x | Nibbles |
|---|---|---|---|
| Private / default | `0x12` | `0x1424` | Y=1, Z=2 |
| Public / LoRaWAN | `0x34` | `0x3444` | Y=3, Z=4 |

`0x34 → 0x3444` looks like "original byte plus `0x44`" by coincidence, which
leads people to write `0x1244` for `0x12`. Decompose into nibbles every time.
Nibbles are conventionally kept to 1–7 because each is scaled by 8 to produce
its symbol value.

The chip performs no expansion. A 1-byte driver API does it in software (RadioLib's
`SX126x::setSyncWord(uint8_t, uint8_t controlBits = 0x44)` is where the two 4s
come from); a 16-bit or raw-register API does not. Calling a 16-bit API with
`0x12` writes a valid-but-wrong sync word and produces a radio that transmits
happily, receives nothing, and reports no error.

`0x34` is reserved for LoRaWAN and must not be used for a private network.

## 6. Implementation sketch — F2 and F4

Neither mode exists yet. Target net throughput is 2× and 4× SF5/BW500.

| | SF5/BW500 (baseline) | **F2** | **F4** |
|---|---|---|---|
| Net throughput | 62.5 kbps | 125 kbps (2.0×) | 250 kbps (4.0×) |
| Raw on-air rate | — | 250 kbps | 300 kbps (chip max) |
| FEC | LoRa CR 4/5 | rate 1/2, K=7 | rate 5/6 punctured |
| Modulation | LoRa | GFSK, BT 0.5 | GFSK, BT 0.5 |
| Fdev | — | 62.5 kHz | 52.5 kHz |
| Modulation index h | — | 0.5 | 0.35 |
| Occupied BW (Carson) | 500 kHz | 375 kHz (75%) | 405 kHz (81%) |
| RxBw, SX1262 step | — | 467 kHz | 467 kHz |

Bandwidth follows Carson: `occupied ≈ 2·Fdev + Br ≈ Br·(h + 1)`. Receiver
bandwidth needs ~35 kHz on top for frequency error (±10 ppm crystals at both
ends at 868 MHz). Carson is conservative here — BT 0.5 Gaussian shaping
suppresses the outer sidebands, so measured 99% occupied bandwidth comes in
below these figures.

**F4 cannot be both 4× and generously coded.** 250 kbps net against the 300 kbps
hardware ceiling leaves only rate 5/6. A rate-1/2 4× mode would need 500 kbps
raw, which does not fit a 500 kHz channel at any modulation index, since 2-FSK
tops out near 1 bit/s/Hz. F2 gets the generous coding; F4 is coded as heavily as
the ceiling permits.

### 6.1 Coding chain

**One mother code for both modes** — rate 1/2, K=7, polynomials 133/171, with F4
punctured to 5/6 off the same encoder. One Viterbi decoder serves both; only the
puncture mask differs. Take soft decisions off the demodulator where available:
the ~5 dB coding gain assumed for F2 is the soft-decision figure and
hard-decision costs about 1.5 dB of it.

Order, transmit side:

```
payload → own CRC → convolutional encode → block interleave → radio (hardware whitening) → air
```

**Disable the chip's CRC.** It would compute over the encoded bitstream and says
nothing about whether decoding succeeded. The CRC must live inside the
FEC-protected payload so it validates the decoded result.

Interleaver depth should exceed the expected burst length. At 250–300 kbps a
1 ms hit is 250–300 bits, so a 32×32 block interleaver (1024 bits) covers
roughly 3–4 ms. That is the parameter to raise near RFID interrogators in
865–868, where burst interference is the realistic failure mode rather than
Gaussian noise — interleaving matters more than code rate in that environment.

Neither family's FSK engine provides FEC or interleaving. CRC is detection only.
Everything corrective is ours. The packet engine does provide preamble
detection, sync word up to 8 bytes, fixed or variable length framing, address
filtering and whitening.

### 6.2 Parameter choices to revisit

- **h = 0.35 on F4** is what makes it fit. Non-coherent FSK demodulation prefers
  h near 0.7–1.0; 0.35 costs roughly 1.5–2 dB and leans harder on AFC to track
  crystal error. F4 at h = 0.5 (Fdev 75 kHz, occupied 450 kHz) recovers that but
  eats most of the channel margin.
- **SX126x hardware address filtering is known-flaky.** Implementing address
  comparison in our own payload is the common workaround.

## 7. Sources

- ETSI EN 300 220-2 V3.2.1 (2018-06) — annex B table B.1, annex C table C.1,
  clauses 4.3.10 and 4.5
- ETSI EN 300 220-1 V3.1.1 (2017-02) — clause 5.21 (tables 45, 46, 48), clause
  5.14 (table 32)
- CEPT/ERC Recommendation 70-03 — the CEPT-wide position, including national
  non-EU-harmonised entries in the same spectrum
- EC Decision 2017/1483 — the band numbers table B.1 maps to
- SX1261/2 and SX1276 datasheets — modulation, register and packet-engine detail
- mLRS `SX126x_SX127x_INCOMPATIBILITY.md` — the SF5/SF6 cross-family situation

EN 300 220-2 is the 2018 edition and EN 300 220-1 the 2017 edition. Check for
newer revisions and the relevant national table before certifying anything.
