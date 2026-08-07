# AFA demonstrator — build plan

> Scope: **the work, in order.** The regulatory reasoning lives in
> [`plans/psa.md`](../psa.md) and the channel/modulation plan in
> [`plans/afa.md`](../afa.md); neither is restated here. This file is what gets
> built, in what order, behind which gate.
>
> **Everything from here is gated behind `s.lora.<n>.afa` (default `0`)** — one
> exception, stated in §2, which is deliberately ungated so the first result is
> visible without touching the radio's behaviour.
>
> AFA = Adaptive Frequency Agility; CCA = Clear Channel Assessment; ToA = time
> on air.

## 0. Order of work

| Phase | What | Gated | Visible result |
|---|---|---|---|
| 1 | Radio/interface task split (§1) | — | nothing; refactor only |
| 2 | Hailing-channel RSSI at 1 Hz, grey floor under the LoRaMon graph (§2) | **no** | the graph, immediately |
| 3 | Regime 1 table, channel field on records, per-channel airtime (§3, §4) | yes | `lora <n>` output |
| 4 | Ten-channel sweep + a stacked graph per channel (§5, §6) | yes | the channel stack |

Phase 2 is independent of the split and of AFA — it can land alongside phase 1
or immediately after it.

## 1. The split

One radio task, **as empty as it can be made**. Not one task per radio: sharing
a task serialises the SPI bus by construction, which is what protects the
CCA→transmit window (`psa.md` §3.3, §6.4).

**The radio task knows nothing about policy.** It receives fully-decided work
and executes it against the chip:

```
interface task                         radio task
──────────────────────────────         ──────────────────────────────
decides: channel, modulation,          applyConfig / retune
  power, backoff target, DIFS,         sense: getRSSI(false)
  CCA threshold, deadline              count free-medium time to target
        │                              CCA, dead time, key up
        ├── TxRequest ──────────────▶  TxDone → ToA, Toff stamp
        ◀── TxResult ───────────────┤
        ◀── RxFrame  ───────────────┤  RX IRQ → bytes + rssi/snr/ms/ch
        ├── MeasureRequest ─────────▶  the §5 excursion
        ◀── RssiSample ─────────────┤
```

`TxRequest` carries everything the radio task would otherwise have to reason
about: `{channel, mod_idx, txp_dbm, backoff_target_ms, difs_ms,
cca_threshold_dbm, deadline_ms}`. The radio task counts down and transmits or
reports failure; it never consults a ledger, a band table or a neighbour row.

**Stays in the radio task**

- chip bring-up, `applyConfig`, retune
- packets in — read, timestamp, rssi/snr, tag with channel
- packets out — free-medium counting, CCA, dead time, key up
- quick measurements — `getRSSI(false)`, the §5 excursion
- `splitPending` half-duplex interlock, per-channel `last_tx_end_ms` for Toff
- time-on-air at TxDone

**Moves out**

- the airtime ledger and every cap decision — **not time-critical**; §4
- APPC band selection, contention-window constants, thresholds
- LoRaMon record writing (storage), the `AirBucket` rollup
- the neighbour table and its inline Ed25519 announce verification
- rfprobe ladder derivation (its slot *timing* stays; the arithmetic leaves)
- stats publication, telemetry, the CLI, the RNode endpoint's config writes

The line: **anything that must complete in bounded time next to the chip stays;
anything that allocates, verifies a signature, or touches flash leaves.** Flash
is the sharp one — `psa.md` §3.3 puts cache-blocking erases at the top of the
threats to the dead-time budget, and LoRaMon writes to storage today from the
same loop that would hold it.

**The hand-off must not backpressure.** Per-frame records and RSSI samples leave
through a bounded queue with an explicit drop policy. A slow flash write must
never stall a transmit; dropped records are correct under pressure and get
counted.

## 2. Hailing-channel RSSI — ungated, build first

Independent of AFA, of the sweep, and of any regime. The radio task samples
`getRSSI(false)` on the hailing channel **once a second** and publishes it. No
retune, no excursion, no risk: the radio is already in RX on that channel, so
this is one SPI transaction per second.

In the LoRaMon graph, the sample series draws as a **bar graph in very light
grey from the bottom of the plot**, on the same received-strength dBm axis the
frames already use, with the packet bars drawn **on top of** it. Channel noise
becomes the floor the traffic sits on.

This is the phase that makes the rest legible, and it is worth having on its own
even if nothing else ships.

## 3. Regime 1

A **regime** is a numbered, versioned table of what is permissible on air — the
thing two nodes name when they agree how to behave.

**`s.lora.<n>.afa` IS the regime number**, not a flag. 0 is not a regime — it
means no agility, the hailing channel alone. 1 is ours.

```
REGIME 1
  channels[]                          modulations[]
    ch          u8   0 = hailing        idx        u8
    freq_hz     u32                     kind       u8   0 LoRa, 1 GFSK
    bw_hz       u32                     sf         u8   LoRa only
    airtime_max f32  seconds            bw_hz      u32
    airtime_win u32  seconds            delta_db   f32  vs SF7/BW125
    max_tx_s    f32  single tx
    max_tx_txn  f32  dialogue
    max_txpwr   i8   dBm
```

`airtime_max / airtime_win` as a **pair** is what makes the table portable: EU
polite spectrum access is `100.0 / 3600`, an EU duty cycle is `360.0 / 3600` at
10 %, and US frequency-hopping dwell is `0.4 / 20`. One field pair, three
regulatory shapes, no special cases in the code that reads it.

Channel 0 is the hailing channel and is flagged never-leave. Channels 1–9 are
`afa.md` §2's plan: 500 kHz, `100.0 / 3600`, `max_tx_s` 1.0, `max_tx_txn` 4.0,
`max_txpwr` 14.

The modulation list is `afa.md` §3's ladder, numbered, each carrying its dB loss
against SF7/BW125:

| idx | Mode | delta_db |
|---|---|---|
| 0 | SF7 / BW125 | 0.0 |
| 1 | SF6 / BW125 | 2.5 |
| 2 | SF7 / BW250 | 3.0 |
| 3 | SF5 / BW125 | 5.0 |
| 4 | SF6 / BW250 | 5.5 |
| 5 | SF7 / BW500 | 6.0 |
| 6 | SF5 / BW250 | 8.0 |
| 7 | SF6 / BW500 | 8.5 |
| 8 | SF5 / BW500 | 11.0 |
| 9 | F2 GFSK | 17.5 |
| 10 | F4 GFSK | 24.5 |

Indices are the negotiation currency — a peer proposes a rung by number, and
both ends resolve it through their own copy of the table. The list is global
rather than per-channel because every channel in the plan is 500 kHz, so the
whole ladder is available on all of them.

For the demonstrator the table is compiled in and nothing negotiates yet. It
exists now so that the record format, the airtime accounting and the UI are all
written against channel and modulation *indices* from the start, rather than
being retrofitted when negotiation arrives.

## 4. Per-channel airtime

**Every packet record gains a channel field**, `0` for the hailing channel.
Today's LoRaMon record is a packed string per frame under
`lora.<n>.packets.<ms>`; the field appends:

```
rx:  r|<rssi>|<snr>|<dur_ms>|<bytes>|<type>|<ch>
tx:  t|<txp>|<dur_ms>|<bytes>|<type>|<wait_ms>|<ch>
```

Until AFA is enabled every record carries `0`, which is the point — the plumbing
is exercised long before the radio ever leaves the hailing channel.

**The ledger cannot be derived from those records.** LoRaMon recording is gated
on a viewer being open (`sys.stats.web_loramon` / `sys.stats.lcd_loramon`) and
its subtree is dropped when the last one closes. An airtime figure that only
exists while somebody is watching is useless for a cap. So:

**A per-channel airtime ring, always fed, independent of any viewer.** 10-second
buckets × 360 = one hour, per channel: `u16` milliseconds per bucket (10 000 fits
easily), 10 channels → **7.2 kB**. Credited at TxDone with the time-on-air the
radio task already computes.

Recomputed **once per 10 seconds**, on the interface task. Precision is not
required: the cap is approached slowly and defended with margin, so a 10 s
worst-case quantization error against a 100 s budget is acceptable so long as
the effective cap is set a bucket below the legal one. This is explicitly *not*
on the transmit path — the radio task reads a precomputed per-channel verdict,
never an arithmetic result.

This supersedes `psa.md` §6.2's 480-bin proposal: same purpose, coarser, simpler,
and sized to the demonstrator rather than to certification.

## 5. The RSSI sweep

Behind the gate. The radio task visits the hailing channel plus the nine, taking
**one** RSSI measurement each, as brief excursions, at **1 Hz**.

Costs, from `psa.md` §3.2 and §3.5: seven SPI transactions and two PLL settles
per excursion, ~0.5 ms away from hail, well inside the 4.10 ms preamble budget
at SF7/BW125. Nine excursions per second is ~0.1–0.35 mA average depending on
whether they share a wake — under 3 % of an idle node either way.

Gating, non-negotiable: **no excursion while the receiver is mid-packet.**
`PreambleDetected` and `HeaderValid` must both be clear, and once a header is
valid the radio is committed for the whole payload. A busy hailing channel
simply yields fewer samples, which is the correct failure.

**Carrier sense outranks measurement.** While the CSMA machine is sensing, the
radio belongs to it: no excursion is taken and no sample is produced or
published for that beat. Channel access is the radio's job and the sweep is
decoration — the sample series simply goes absent while a frame is contending
for the medium, and the viewers draw the gap rather than a stale value. One
arbiter in the radio task, and it always resolves in favour of the sense.

Two consumers, no ITS backlog:

- **Browser** — current values published on the 1 Hz beat as one packed key,
  `lora.<n>.rssi` = `<ch0>|<ch1>|…|<ch9>`, riding the existing ephemeral
  republish-merge rather than adding ten keys of mirror churn.
- **LCD** — the last **60 seconds** held in RAM, 60 samples × 10 channels ×
  `int8` dBm = 600 bytes. No history beyond the window and none persisted.

## 6. LoRaMon — the channel graph

Two changes to the viewers, browser only for the demonstrator.

**The existing graph** (channel 0) gains the §2 grey floor: measured RSSI as a
very light grey bar graph rising from the bottom on the received-strength axis,
frames drawn over it. This is phase 2 and lands before anything else here.

**One graph per agile channel** stacks underneath it, each a quarter of the
hailing channel's height. They span the same width, which is the point: the time
axis is shared, so a moment is the same column in all ten and the eye can run
down it. Same bands, same dBm scale, same window pills. The gutters are reserved
on every graph so the plots line up, but labelled only on the hailing channel's
— repeating one scale ten times is noise, and a quarter-height band has no room
for the numbers.

Each stacked graph is the same idea as the main one: the channel's RSSI as a
light grey floor, that channel's packets lying on top. Ten strips read as a
spectrum over time — where the traffic is, and what the noise under it was.

Underneath each, its centre frequency and bandwidth. The hailing channel's
caption starts with the same pair, then keeps its transmit airtime and channel
busy figures; the agile channels carry transmit airtime only. **Channel busy is
deliberately absent there** — what someone else is doing on a channel we visit
once a second for 200 µs is one instant sampled per second, and a percentage
would invite reading it as occupancy.

The colour key moves up beside the window pills, one pill's width clear of them.

The stack is gated: with `afa=0` the published channel list has one entry, so
there is nothing to draw and nothing is shown.

## 7. Settings

| Key | Default | Meaning |
|---|---|---|
| `s.lora.<n>.afa` | `0` | **The regime number**, and the gate for §3–§6. 0 = no agility. Live. |

Deliberately one key, and an integer rather than a switch. Sub-behaviours are not
individually settable — the point of the gate is that the device's on-air
behaviour is unchanged until it is set, so there is exactly one thing to turn off
when comparing. An unknown regime number resolves to no agile channels, which is
the safe reading of a value this firmware does not understand.

Note `s.lora.<n>.frequency` still has no default (`psa.md` §1.3 point 5). Regime
0's channel 0 has to come from somewhere; for the demonstrator it takes the
configured frequency, and the region table that would validate it is out of
scope here.

## 8. Open points

1. **Record format compatibility.** Appending `<ch>` is safe for parsers that
   index by position, but the browser and LCD readers both need checking for
   field-count assumptions before the field lands.
2. **Whether the nine-band graph shares the main graph's dBm axis** or gets a
   compressed one. Nine narrow strips at the full −130…−30 range may be
   unreadable; a clipped range per band is the alternative.
4. **`max_tx_s` on the hailing channel.** Under a duty cycle there is no Ton
   limit, so regime 0 needs a sentinel rather than a fabricated number.
