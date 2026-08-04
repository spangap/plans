# A proper air protocol for iface-lora (SX1262)

> Status: **design notes / plan**, not implemented. A robust common channel plus
> per-pair negotiated high-rate private links. §§1–6 capture what the SX1262 +
> Reticulum substrate *allows*; §§7a–7d are the worked design — backpressure
> layering, the airtime and duty-cycle arithmetic, the burst round, and the link
> budget at 25 mW. §0 is the flow. Counterpart to `iface-espnow`'s
> `docs/plans/proper-air-protocol.md`; cross-refer for the shared rnsd-oblivious
> layering and link-adaptation reasoning. Nothing here is wired up yet.

## 0. The flow

```
hail (SF7/BW125, shared, carrier-sensed)
  A→*   BLIP        = {op, A₄, B₄, cfg_idx, dur_ms, flags}          ~12 B
        └─ everyone in earshot: hold traffic for A and B for dur_ms

  [both retune → burst chan, BW500, max power, private sync word]

burst (no carrier sense from here on)
  A→B   MANIFEST    = {op, n, hash₂ × n}                            2+2n B
  B→A   MANIFEST_ACK= {op, verdict, rssi, snr, alt_cfg_idx}         6 B
        └─ ACK  → proceed
        └─ NACK{out of airtime on this band} + alt_cfg_idx + backoff
                → A caches it against B and re-blips with Y (or gives up to hail)
        └─ SILENCE within T → abort: step DOWN the cfg ladder and retry, else hail

  A→B   PKT₁ … PKTₙ  back-to-back, explicit header, ≤255 B frames
  A→B   MANIFEST    (repeat)                                        = "your turn"

  B→A   BITMAP_ACK  = {op, bitmap⌈n/8⌉, qdepth_B→A}                 4–7 B
  A→B   PKT_missing…  → BITMAP_ACK … until clean

        └─ qdepth_B→A > 0 → roles swap, B runs from MANIFEST
        └─ nothing left → both retune to hail
        └─ still trickling (rnsh) → stay; return to hail only to re-announce
```

Everything above `MANIFEST` is once per detour; everything below is once per
direction. In prose:

1. Both nodes camp on the **hailing channel** (SF7/BW125). Ordinary RNS traffic.
2. A has packets queued for B. A picks a burst config from B's stored rfprobe cliff
   (§7d's ladder — the fastest rung B's measured margin supports).
3. A takes its normal carrier-sense turn and sends one **blip**: burst config index +
   duration. Everyone in earshot now knows *A and B are gone for ~1.2 s* and holds
   traffic for them (§3).
4. Both **retune** to the burst channel at max power.
5. A sends the **manifest**: a 2-byte hash of every packet in the train.
6. B **acks the manifest** (6 B, with its RSSI/SNR of it). This is the only gate: it is
   the both-ends-can-hear-each-other check, B's consent, and — as a NACK in the same
   slot — B's refusal plus a counter-proposal, all taken before anything expensive is
   committed. Silence means abort.
7. A sends **the whole train back-to-back** — explicit headers, no carrier sense, no
   slots, no µs timing. It repeats the manifest at the end: redundancy, and *your turn*.
8. B replies with a **bitmap ack**: which hashes arrived, and how much B has queued
   for A.
9. Missing packets repeat. If B has traffic, the direction flips and B runs 5–8.
10. Both retune back to hail. Returning early is visible to holders as ordinary
    traffic from A or B.

Departure has **no accumulation timer**: A leaves with whatever is queued, and the
queue refills while the burst is in flight — the burst's own duration is the window
(§7c). The same rule gives two natural shapes: bulk drains in ~1 s and returns;
interactive traffic (rnsh) keeps trickling, so the pair *stays* on the burst channel,
returning to hail periodically to re-announce and to listen.

## 1. Why the SX1262 world is different from ESP-NOW

ESP-NOW rides 802.11: the PHY rate is signalled in the preamble and **auto-detected
per frame**, so a receiver hears all rates at once. **LoRa is the opposite.**
Demodulation requires the receiver to be **pre-tuned to one exact modem config** —
SF, BW, CR, preamble length, sync word, *and* center frequency. The SX1262 has a
**single demodulator**: it listens for exactly one `(SF, BW, freq)` at a time.

Consequences that shape everything below:

- **SF is effectively channelization, not in-band signalling.** Two nodes on
  different SF **cannot hear each other at all** — not slower, *silent*. No "energy
  present", no fallback.
- **No multi-SF simultaneous RX on this part.** That is a *gateway/concentrator*
  feature (SX1301/SX1302 have 8+ parallel demodulators). A T-Deck's SX1262
  transceiver can't, so don't design around it.
- **You own the MAC.** No association, no CSMA forced on you — you can schedule
  transmissions deterministically (see §5), which is a capability ESP-NOW's CSMA
  denies.
- **The SX1262 reports RSSI *and* SNR per received packet** (`GetPacketStatus`:
  `RssiPkt`/`SnrPkt`/`SignalRssiPkt`). SNR is the ideal LoRa link metric — LoRa
  decodes below the noise floor, so SNR tells you how much SF headroom a link has.
  This is the raw material for per-link rate choice (§4).

## 2. The model: robust common channel + per-pair private links

**Not** a network-wide "everybody switch to SF7 now" hop (one collision domain,
everyone must follow, single point of failure). Instead, the better model — closer
to **Bluetooth** (page on known params → private negotiated link between two
devices) or cellular (common control channel → dedicated negotiated resources):

- **Common channel.** One **intermediate, most-robust-feasible** config that *every*
  node camps on a priori (provisioned constant — no chicken-and-egg, because it's
  fixed/known). Carries announces, path discovery, small/control traffic, and the
  link-setup negotiation. This *is* the RNS broadcast interface.
- **Per-pair private links.** When two nodes have enough to exchange, they
  bilaterally negotiate their own fast config and move off the common channel for a
  bounded burst, then return. Only the pair coordinates — the rest of the network is
  undisturbed.

This turns a single shared collision domain into **many parallel point-to-point
links** (§4) — the aggregate-capacity win.

## 3. The "X and Y are gone for ~x ms" reservation

Before a pair leaves, they announce their absence on the common channel. Get the
semantics right — it is **weaker than a channel lock, on purpose**:

- It's a **virtual-carrier-sense / NAV-style hint**: "X and Y won't be on the common
  channel for ~x ms, so **queue anything destined for X or Y**." It is **not**
  "everyone go silent" — other pairs among the still-present nodes keep using the
  common channel freely. Don't let it drift into a full channel reservation; that
  would waste the shared medium.
- It's a **soft optimization, not a guarantee**: (a) **hidden nodes** out of range of
  the announcement may still send to X/Y and get nothing; (b) a node that misses the
  announcement won't hold. That's fine — same principle as the ESP-NOW doc: *the
  reservation is a hint; RNS higher-layer reliability + retry are ground truth.* It
  reduces wasted transmissions, it doesn't eliminate them.
- **Bound it.** The announced window is a **deadline**, not a promise to use it all —
  finish early, return early; hit the bound, return and re-reserve if still needed.
  Others' "hold" timers key off `announced_window + margin`.

## 4. The private bilateral link

The part that changes network capacity, not just one link's speed:

- **Frequency independence ⇒ spectral/code reuse.** X and Y need not share frequency
  with the network *or with other pairs*. Multiple pairs burst **simultaneously** on
  different frequencies (fully orthogonal) — or same freq / different SF (LoRa SFs
  are quasi-orthogonal). Parallel links → aggregate throughput scales with concurrent
  pairs, not capped by one channel.
- **Per-link, measurement-driven parameters.** During the brief common-channel
  negotiation each side reads its RSSI/SNR (§1) and they pick `SF/BW/CR/freq` matched
  to *that* link's budget — including **asymmetric** configs (X→Y at SF7, Y→X at SF9
  if the reverse path is worse), since each direction's SNR is measured
  independently. This is the realization of the ESP-NOW reception-report/asymmetry
  idea, but cleaner because it's a 2-party agreement.
- **Private sync word** on the burst keeps it from being confused with common-channel
  or other pairs' traffic.

## 5. Deterministic timing — a LoRa superpower (recall ESP-NOW couldn't)

> **The burst round in §7c does not need this.** Back-to-back frames plus a manifest
> stating the count give the receiver the sender's turn length without a schedule, so
> the pair case needs tens-of-ms accuracy, not µs — no slot arithmetic, no drift
> budget, no `PM_NO_LIGHT_SLEEP` lock, no DIO-edge capture. This section stays as the
> record of what the substrate *allows*, and is the reference if a network-wide
> synchronized scheme is ever wanted. Do not build it for the pair burst.

On ESP-NOW you can't know *a priori* when a packet goes out (CSMA backoff/retry).
On LoRa **you can**, because airtime is exactly computable from SF/BW/CR/preamble/
payload and you own the schedule. So:

- A scheduled transmission goes out **at its slot, by construction** — the "when did
  it actually go out" question (open on ESP-NOW) **dissolves**: scheduled = known,
  and TxDone/RxDone just confirm it.
- **One transmission can anchor a whole timeline** ("everyone times along"): the
  beacon's **RxDone** is a shared reference instant; a pre-agreed schedule unfolds
  from it (beacon-synchronized TDMA — the LoRaWAN Class B / 802.15.4e-TSCH model).
  Within a **pair** this is trivial (2 nodes, one RxDone anchor — no Class-B
  machinery needed); the full network-wide version is available if ever wanted.

Error budget for any "shared t=0" (increasing importance):
1. **Timestamp jitter** — read RxDone in the ISR after SPI status readback → µs-to-
   tens-of-µs; **hardware-capture the DIO edge** (ESP32-S3 GPTimer + ETM / input
   capture) → sub-µs. Capture the edge for tight slots.
2. **Propagation** — ~3.3 µs/km; single-digit µs at range, usually negligible vs
   guards.
3. **Clock drift between syncs — dominant.** ±10–20 ppm XTAL → ~20 µs/s, ~1.2 ms/min
   of skew. **This sets re-sync interval and guard size.** A TCXO cuts this sharply —
   the part's clock spec drives the whole budget.

Guard intervals absorb `drift-since-sync + jitter + propagation + TX↔RX turnaround +
retune`. SX1262 mode transitions are tens of µs to ~100 µs; PLL settle on a freq
change is a few hundred µs — all deterministic, so budget them exactly. Disciplining
the local clock to the sync cadence (PI-correct the timer rate) stretches how long a
schedule stays valid between syncs.

## 6. Realities to budget

- **Half-duplex deafness.** While on the private link, both nodes are deaf to the
  common channel — exactly what §3's reservation manages. But a node must not vanish
  so long it **misses announces or starves its other neighbors/links**: cap the
  per-session window and the off-channel duty fraction (fairness).
- **Amortization threshold — there isn't one.** The intuition that small transfers
  should ride the common channel (ESP-NOW's broadcast-for-small / unicast-for-bulk
  logic) does not carry over: §7b computes break-even at the *minimum* 19 B RNS
  packet, because the SF7→SF5/BW500 airtime ratio outruns the blip that buys it. The
  threshold to watch is not burst size but **turnaround** — the small-packet margin
  is 15 ms and a task-latency-bound retune consumes all of it.
- **Return / timeout.** If a side crashes or the burst overruns, both fall back to the
  common channel after a timeout and re-sync there.
- **Regulatory is *friendlier* for the burst.** Fast config (high BW / low SF) →
  short airtime → easy on **dwell-time** limits (e.g. US ~400 ms); spreading pairs
  across sub-bands also spreads the **EU 1% duty-cycle** budget. It's the *slow common
  channel* you must watch — a too-robust (high-SF) common config can blow dwell time
  (e.g. SF12/BW125 4-byte packet ≈ 800 ms > US 400 ms). Pick the common config to
  balance robustness against airtime/dwell; SF10/BW125 or SF9/BW250 is far more
  practical than SF12.
- **CAD for idle listening.** The SX1262's Channel Activity Detection cheaply (low
  power) detects a preamble at the current config without a full RX — good for
  camping on the common channel between bursts.

## 7. Staying rnsd-oblivious (same abstraction as iface-espnow)

The interface presents a "broadcast medium" to rnsd; underneath it:

1. buffers outbound packets whose next hop is Y,
2. when enough accumulate (the §6 threshold), negotiates a private link with Y on the
   common channel and announces the §3 absence window,
3. retunes, drains the buffer over the fast link, returns to the common channel,
4. rnsd just sees the packets delivered.

rnsd never knows a private high-rate link existed — exactly as it never knows about
MAC-unicast on ESP-NOW. The common channel *is* the RNS broadcast interface
(announces, path discovery, control); the private links are ephemeral fast pipes for
bulk next-hop delivery. Hop-count / announce / path-building all happen on the common
channel and read straight from the RNS header — see iface-espnow's doc §5–6, which
applies unchanged here.

## 7a. Backpressure: what moves down, and what cannot

§7's "buffers outbound packets whose next hop is Y" assumes rnsd hands the interface
a queue to drain. It does not today, and that is the first thing to fix — the
interface is the only party that knows who is in RF range (§13 of `INTERNALS.md`),
what margin each link has (§14 rfprobe) and what chip the peer is, so it must be the
party that decides how much to accept. But "backpressure" currently names two
different things upstream, and only one of them can move:

- **Admission / queueing — moves to the interface.** How many bytes may sit waiting
  for the medium is a property of the medium. The hand-off point already exists: the
  `RNSD_PORT_IFACE` ITS connection, where `drainOneOutbound` takes one packet per
  turn and outbound waits in the ITS buffer during a probe. That buffer needs a real
  depth and a readable ready/occupancy signal that rnsd's outbound path consults
  instead of its own counters (`RNSD_OUR_DEST_MAX_PENDING` is not even this — it caps
  concurrent *path searches*, not queued frames).
- **End-to-end windows — stay where they are.** `Channel::WINDOW` (2) and Resource's
  adaptive request window are RTT-bound against the *far end*, not the neighbour. A
  32-deep interface queue does not fill if Channel emits 2. Handing these to the
  interface would be handing it a decision it cannot make: it cannot see the far end.

**Consequence for what to build first.** Resource is the only subsystem that already
generates genuine next-hop depth — its receiver-driven window grows toward
`window_max` / `WINDOW_MAX_FAST` (`rns/INTERNALS.md` §Resource). LXMF attachments and
rnsh bulk are therefore the first customers of a burst link, and they need no changes
to Channel or to rnsd's pending table to produce a queue worth bursting.

**The layering leak to design around, not hand-wave.** §7's "rnsd never knows" is not
fully achievable. `Channel`'s retransmit deadline is `2.5 × RTT × (ring + 1.5)` and
`MAX_TRIES = 5` tears the link down. An envelope held through negotiation, retune and
burst can trip that deadline, and Channel then resends a packet the interface is
still holding — duplicate airtime, the exact opposite of the win. So rnsd can be *not
the decider* while still needing queue delay surfaced far enough upward to inflate
RTT and timeouts, or the burst must be bounded to complete inside the existing
budget. Pick one explicitly.

## 7b. Amortization: the airtime numbers

Computed against the seeded defaults — hail SF7/BW125/CR4-5/preamble 12, explicit
header, CRC on — with `loraAirtimeSeconds`'s AN1200.13 arithmetic, split framing
(1 B/frame, 255 B max, so a 500 B packet is two frames on either channel), and
`HEADER_MINSIZE` = 19 B as the smallest RNS packet.

**The contention wait cancels.** One carrier-sense covers the hail blip and the burst
that follows it, and the same wait would have been paid to send on the hailing
channel. So the detour's cost is blip + turnaround, and the comparison is pure
airtime. (For reference, average APPC wait at SF7/BW125 is 160 ms in band 1, rising
to 1060 ms in band 4.)

| RNS packet | all-on-hail | 8 B blip + SF5/BW500 burst | saving |
|---|---|---|---|
| 19 B | 60.7 ms | 45.7 ms | 15.0 ms (25 %) |
| 100 B | 178.4 ms | 55.9 ms | 122.5 ms (69 %) |
| 254 B | 403.7 ms | 75.8 ms | 327.9 ms (81 %) |
| 500 B | 797.2 ms | 109.1 ms | 688.1 ms (86 %) |

**Across a batch the wait does not cancel — it compounds against the hail channel.**
Each packet sent conventionally contends separately, and APPC's band is chosen from
the node's *own* transmit duty cycle, so a batch walks its own contention window up
as it proceeds. Fourteen 500 B packets on an **idle** medium, node starting at 0 %
airtime: packet 1 waits 160 ms (band 1), packets 2–7 wait 460 ms (band 2, 9–32 %
airtime), packets 8–14 wait 760 ms (band 3, 54–66 %). Total **19.40 s**, of which
8.24 s is contention. The same payload as one burst — a single band-1 wait, an 8 B
blip, retune, and 956 ms off-channel — is **1.16 s**: a 16.8× wall-clock win that
also hands 11.12 s of shared medium back to every other node in range. The burster
credits 40 ms of hail airtime and therefore *stays* in band 1, so its next
transaction starts cheap while a slow-path node is still sitting in band 3.

**Break-even is the minimum packet**, so there is no amortization threshold to
enforce — the detour pays for a single packet of any size. Two things decide whether
the small-packet end is worth having:

- **Turnaround is the whole small-packet margin.** At rfprobe's 15 ms slot guard the
  19 B case saves 1 ms — a wash. That guard covers responder *task* latency (RxDone
  IRQ → priority-2 task on a 10 ms tick → retune → arm). Driving the retune from the
  esp_timer path rfprobe already uses for slot transmits makes it PLL-settle bound
  (hundreds of µs) and restores the 15 ms.
- **The blip is nearly all preamble.** 8 B at preamble 12 is 40.2 ms, of which
  16.6 ms is preamble; preamble 6 (the rfprobe sweep regime) costs 34.1 ms. Note 8 B
  does not fit a literal descriptor — rfprobe's P1 needs 12 B for
  `[op][us₄][them₄][txp][flags][rsv]`, and 12 B costs 45.3 ms — so the burst config
  must be an index into a pre-agreed table, not `SF/BW/freq` on the wire.

**Account burst airtime separately.** APPC picks its contention band from this
radio's own transmit duty cycle, and `appcAddAirtime()` credits every frame at
TxDone. Moving 797 ms of airtime to 68 ms collapses that figure and keeps the node in
band 1, so the detour shortens every *future* pre-send wait as well — but only if
burst frames are not credited to the hailing channel's accounting.

## 7c. The burst round

The shape §0 sketches, and why each part is the cheap option rather than the clever
one. Costs are at SF5/BW500/CR4-5/preamble 8, explicit header.

**Every frame carries an explicit header — one modem config for the whole burst.**
Headerless framing is legitimate for a fixed-length immediate reply, where the length
is pinned by a protocol constant and the frame lands in a known window (rfprobe's sweep
regime runs exactly this way). It becomes a hazard only when a length is *negotiated* —
the coupling §14.1 of `INTERNALS.md` removed after it made every later change a
compatibility problem, and which §16 records as failing *silently* on a build mismatch.

But once the design has no hail-channel reply, no frame is left where it pays:

| frame | channel | explicit | headerless, preamble 6 | saved |
|---|---|---|---|---|
| MANIFEST_ACK | burst | 2.26 ms | 1.81 ms | 0.45 ms |
| BITMAP_ACK (n=28) | burst | 2.58 ms | 2.13 ms | 0.45 ms |

Every remaining candidate is on the burst channel, where 0.45 ms buys two modem
reconfigurations over SPI — landing in the same turnaround budget that is already the
tight number. The saving does not cover the switch, so nothing switches. (A
hail-channel reply would have saved 11.26 ms of 40.19, which is why the rule is worth
recording even though nothing currently uses it.)

Two frames would not have been eligible in any case. **MANIFEST** is not a reply and
its length (2 + 2n) is unknown to the receiver in advance. **The trailing MANIFEST
copy** is known-length by then, but exists precisely as redundancy for a lost leading
manifest — and a headerless frame can only be received by a node already armed with its
length, so making it implicit deletes the case it exists for.

**No slots, no shared clock.** Frames go back-to-back and the manifest states how
many there are, so the receiver knows when the sender's turn ends without a schedule.
This deletes most of §5 — no µs slot arithmetic, no clock-drift budget, no
`PM_NO_LIGHT_SLEEP` lock, no hardware DIO-edge capture. Timing accuracy needed is
tens of ms, not µs.

**The manifest is the probe.** Sent as its own frame — op byte + a 2-byte hash per
packet — it is gated on a 4-byte ack before the burst commits. That one exchange
proves four things at once: the peer retuned, uplink closes at the burst config,
downlink closes at it, and the peer knows the expected count.

| | cost | share of a 950 ms burst |
|---|---|---|
| manifest, 14 packets (29 B) | 5.46 ms | |
| manifest-ack (4 B) | 2.26 ms | |
| handshake @ 1 ms turnaround | 9.71 ms | **1.0 %** |
| handshake @ 15 ms turnaround | 37.71 ms | 4.0 % |

**One departure mode, and the manifest-ack carries every verdict.** There is no
hail-channel handshake before the detour: A blips and goes. The manifest-ack slot then
serves three answers at once — **ACK** proceed, **NACK** with a reason, an alternative
config index and a back-off hint, and **silence** meaning abort. A refusal costs
nothing extra because it occupies a slot that had to exist anyway, and it can only
arise when B is reachable, which is precisely when a counter-proposal is worth
carrying.

Rejecting the alternative — a hail-channel ack after the blip — matters for a reason
beyond simplicity: it would spend the shared channel, the resource this whole design
exists to protect (§7d). Discovering that a peer is absent or unwilling costs 68–136 ms
of the *burst* channel instead, which §7c already prices as cheap enough to attempt
without certainty. Repeated refusal is handled by memory, not by a handshake: the
NACK's back-off hint is cached against that peer in the neighbour table, so the cost is
paid once per peer rather than once per attempt.

**Failing the check is cheap enough to attempt optimistically.** The carrier-sense
wait is not wasted — the fallback send on hail needs it anyway — so the marginal loss
of a refused burst is blip + retune + manifest + timeout + retune back: **68 ms** at a
fast retune, 136 ms at a slow one. That is 8.5 % (resp. 17 %) of a *single* 500 B
packet's airtime on the hailing channel. Combined with §7d's stored-cliff gate, the
node has cheap prediction, cheap verification and cheap failure — all three, which is
what makes attempting without certainty sound.

**The manifest-ack should state its RSSI/SNR of the manifest**, as every rfprobe
frame states its own power. Two bytes, and the node learns its margin *at the burst
config* instead of extrapolating from a SF7 measurement — which feeds the neighbour
table, so later attempts can skip the check or step down to a slower burst config
rather than failing outright.

**Repeat the manifest at the end.** A lost leading manifest leaves the receiver with
no count, no identities and no idea when the sender's turn ends — the one timing
signal the whole scheme rests on. A trailing copy costs 0.6 %, provides the
redundancy, and doubles as the explicit *your turn* marker so handover no longer
depends on having heard the start. A silence timeout is the backstop.

**Hash and bitmap at RNS-packet granularity, not frame.** A 500 B packet is still two
LoRa frames (§5's 255 B cap applies on both channels); a packet missing either frame
repeats whole. Slightly wasteful on repair, and it buys a large simplification:
because frames go back-to-back, a packet's two frames are adjacent and only one split
is ever in flight, so §6's single-slot `splitBuf` and its 4-bit sequence tag work
unchanged. A windowed, interleaved design would have required widening both.

**2-byte hashes are the right width.** They need only disambiguate within one
transaction: collision odds are 0.18 % at 16 packets, 0.58 % at 28, and a collision
costs one spurious "received" that RNS's own retry already covers. They earn their
keep on the repair round, where positional indices from the previous round no longer
apply but hashes still name the same packets.

**Departure is self-clocking — there is no window constant.** Since §7b puts
break-even at a single minimum-size packet, holding a lone packet to accumulate is
pure added latency for no gain. Instead: depart immediately with whatever is queued;
while a burst is in flight, accumulate; on return, take everything that piled up. A
saturated source refills during the ~1.16 s cycle (it needs 5.9 KiB/s to fill a
14-packet train) so its trains are always full, while sparse traffic departs at N=1
promptly. The only ceiling is the transmitter-on limit, which caps the train — it is
a fill limit, not a latency timer.

**Leave time on the hailing channel; it is cheap to do so.** Cycle is 202 ms
departure + 961 ms burst = 1.163 s.

| off-channel duty | sustained | hail idle between bursts | vs conventional |
|---|---|---|---|
| 100 % | 48.1 kbps | — | 16.6× |
| 50 % | 24.1 kbps | 1.16 s | 8.3× |
| 25 % | 12.0 kbps | 3.49 s | 4.2× |

(conventional = 2.89 kbps, §7b's 14-packet / 19.40 s figure.) Three quarters of the
node's time can go back to the hailing channel while it stays 4× ahead, which is what
makes a conservative duty fraction a defensible default rather than a tuning problem.

**Packed framing is a declared mode, not the default.** The alternative is to treat
the burst as one contiguous byte stream — fill every frame to 255 B regardless of RNS
packet boundaries, and let the manifest slice it — which amortizes preamble and
header across fewer frames. Measured inside a 1 s window, with the leading + trailing
manifest in both schemes:

| RNS packet | unpacked | packed | gain |
|---|---|---|---|
| 19 B | 3762 B | 6061 B | 1.61× |
| 50 B | 5600 B | 6750 B | 1.21× |
| 100 B | 6500 B | 7000 B | 1.08× |
| 254 B | 7112 B | 7112 B | 1.00× |
| 500 B | 7000 B | 7000 B | 1.00× |

The gain runs inversely with packet size and vanishes at max size, because a 500 B
packet is already 255 + 247 — the frames were full and there were no headers left to
save. Three notes if it is ever built:

- **Repair at frame granularity, not packet.** The stream is contiguous, so resending
  the lost 255 B frame repairs everything that depended on it — strictly less airtime
  than resending whole packets. Position then identifies everything within a burst, so
  the manifest needs neither hashes nor offsets, only **lengths** (2 B each, offsets
  derived cumulatively), and the ack bitmap covers frame indices.
- **Delivery becomes deferred.** Unpacked, each packet is complete on arrival and
  streams straight to rnsd. Packed, a packet spanning a lost frame cannot be delivered
  until the repair lands, so the receiver holds the whole ~7 KB burst — and needs a
  real reassembly buffer with a frame bitmap, where unpacked leaves §6's single-slot
  `splitBuf` untouched.
- **Robustness is not the objection.** A lost 255 B frame destroys 255 B of payload
  either way; the only amplification is packets straddling a boundary, at most one per
  boundary.

Small-packet trains are **structural, not hypothetical** — see the return leg below —
but at proof size (83 B) the packing gain is only 1.11×, which does not pay for
burst-buffer reassembly and deferred delivery. The 1.6× figure needs ~19 B packets,
and the traffic with real queue depth is Resource transfer (§7a), all max-size and
gaining 1.00×. Reserve **one flag bit in the manifest** to declare the framing —
forward-compatible at no cost — and build it only if measurement turns up a real
source of ~20 B trains.

**The return leg carries the proofs, and that is most of the win.** A proven exchange
is 1:1 by packet count: an implicit proof is 83 B on the wire (`HEADER_MINSIZE` 19 +
`IMPL_LENGTH` 64 — signature only, the default; 87 with the reticulous rx-report
trailer). So half the packets in such an exchange are small — though only 14 % of the
bytes, and 33 % of the frames on the burst channel.

| 14 × 500 B out, 14 proofs back | cost |
|---|---|
| proofs conventionally, node already loaded by the data send | **16.38 s** |
| the 14 × 500 B data packets that caused them | 11.16 s |
| proofs as the return leg of the same burst | **180 ms** (13 % of a 1345 ms transaction) |

Conventionally the acknowledgements cost *more* than the payload: each 83 B proof is
152.8 ms of airtime, but by then the node sits in APPC band 3–4 from its own data
send and pays 760–1060 ms of contention per proof. In the burst they ride a direction
flip that already exists and contend for nothing — 91× on that leg alone.

**Interactive Channel traffic inverts the value proposition — it wants a held-open
rendezvous, not a burst.** A Channel *is* proof-per-packet (`Link.cpp:1665` proves
every CHANNEL packet), and a small one — an rnsh keystroke, 6 B envelope header + 1 B
payload → one 16 B AES block + 48 B `TOKEN_OVERHEAD` + 19 B RNS header — is 83 B on
the wire, the same size as its own proof. One character echo is therefore **four
contended transmissions**: keystroke, its proof, the echo, its proof.

| | per character |
|---|---|
| hail, APPC band 1 (idle) | 1.25 s |
| hail, band 2 / band 3 | 2.45 s / 3.65 s |
| per-keystroke burst | 0.260 s (4.8×) |
| held-open burst channel | 0.058 s (21.6×) |

Batching contributes nothing here: `Channel::WINDOW` is 2, so a full window is 166 B —
2 % of a 1 s train — and the queue never exists. The cost is four contention waits on
alternating nodes, not airtime, so the fix is to stop paying contention at all by
staying on the burst channel for the session.

This needs no new mechanism: §7c's self-clocking rule already says do not return to
hail while traffic is flowing. Bulk gets a ~1 s train because that is how long its
queue takes to drain; interactive gets a session because traffic keeps trickling.
It does force one thing — §3's reservation announces a *bounded* window, so a
held-open session must return to hail periodically to re-announce it, which is also
exactly when it catches the announces it would otherwise miss. The deafness
mitigation and the reservation refresh are the same trip.

Two things not to conclude from this. The bitmap ack **cannot replace proofs**: it is
hop-local link-layer state, while a proof is a signature that must reach the origin,
possibly several hops away. And the 1:1 ratio is Channel and opportunistic/LXMF
traffic only — this port link-proves CHANNEL packets (`Link.cpp:1665`) but not
`RESOURCE` parts, which get one `RESOURCE_PRF` per resource and return part-requests
instead, so the bulk case's return leg is lighter.

**Three additions to §3's reservation.** The blip already carries burst config and
duration for the counterparty, so **the NAV hint costs nothing extra** — every node in
earshot gets it from the same frame. Holding off is **individually rational**, not
altruistic: a node that holds does not transmit into a void, does not spend airtime on
a doomed frame, and therefore keeps its own APPC duty — and its contention band — low,
so the rule needs no enforcement. And **early return must be observable**: §3 has
holders keying off `announced_window + margin`, but §3 also tells the pair to finish
early and return early, so any hail-channel traffic from A or B has to release the
hold. The naive implementation is a timer that ignores it and waits out the full
window for nothing.

## 7d. Link budget and duty economics at 25 mW

Worked for a 25 mW (14 dBm ERP), 1 % duty-cycle band, both ends at the cap — no
throttling, since the regulatory ceiling is already low. Sensitivities are the
link-budget form `-174 + 10log10(BW) + NF + SNR_limit` at NF 6 dB; substitute SX1262
datasheet figures before sizing the final pad.

|  | sensitivity | max path loss |
|---|---|---|
| hail SF7/BW125 | −124.5 dBm | 138.5 dB |
| burst SF5/BW500 | −113.5 dBm | 127.5 dB |

The burst config needs **11.02 dB** more budget — 6.02 dB of it from 4× the
bandwidth, 5 dB from the SF5-vs-SF7 demodulator SNR limit. That puts burst reach at
28 % (free space) to 53 % (n=4) of hailing reach; roughly half in practice.

**The capability test needs no new measurement.** Burst-capable at a given config
means `rfprobe cliff ≤ 14 − extra_dB − pad`. Since §15's adaptive power already holds
close neighbours at −9/−3 for ordinary traffic, the peers the node has characterised
as cheap are the same population that can burst. Gate the attempt on the stored cliff;
§7c's manifest-ack confirms it before the second is committed.

**Burst configs are a ladder, and a failed attempt steps down it rather than
returning to hail.** SF5/BW500 is not the only useful rung — bandwidth costs 6.02 dB
regardless of SF, and each step of SF buys ~2.5 dB back, so the low rungs are cheap
in link budget while still beating the hailing channel by multiples:

| config | rate | extra dB vs hail | 500 B packet | vs hail | qualifying rungs (3 dB pad) |
|---|---|---|---|---|---|
| SF5/BW500 | 62.5 kbps | 11.02 | 67.9 ms | 11.7× | −9, −3 |
| SF6/BW500 | 37.5 kbps | 8.52 | 114.0 ms | 7.0× | −9, −3 |
| SF7/BW500 | 21.9 kbps | 6.02 | 197.2 ms | 4.0× | −9, −3, +3 |
| SF8/BW500 | 12.5 kbps | 3.52 | 348.4 ms | 2.3× | −9, −3, +3 |

**SF8/BW500 costs 3.52 dB more than the hailing config and is still 2.3× faster** —
essentially anything that can talk on hail at all can do it, so the burst-capable set
is most of the neighbour table rather than only its closest members. Break-even holds
at every rung: at the slowest, a 40 ms blip plus 348 ms still beats 797 ms for a
single 500 B packet. The small-packet margin does narrow at the low rungs, so a node
with only tiny packets queued may prefer hail at the bottom of the ladder.

**Getting off the hailing channel has value independent of the node's own speed.**
Every rung frees the shared medium for everyone else and keeps the node's own APPC
duty — and therefore its contention band — low (§7b). So the decision is not "is this
rung fast enough to be worth it" but "does this rung beat hail at all", which is true
everywhere on the ladder. That is also why a refused attempt should retry one rung
down rather than fall back: §7c prices a failed attempt at 68–136 ms marginal, cheap
enough to spend twice.

**Duty cycle is the binding ceiling, and it is where the burst wins hardest.** Under
a duty regime, transmit *time* is the currency:

| | payload per second of transmit | per hour at 1 % (36 s) |
|---|---|---|
| burst | 7283 B | 256 KiB (37 bursts) |
| hail | 627 B | 22 KiB (45 packets) |

**11.6× more payload per unit of regulatory allowance.** This is a different claim
from §7b's fairness/deafness duty table: that one is about being a good neighbour,
this one is a legal ceiling, and the burst multiplies the node's allowance by an
order of magnitude.

Two placement consequences:

- **Put the burst channel in a different sub-band from the hailing channel** where
  the allocation permits. Duty budgets are per band, so separate sub-bands give two
  independent allowances rather than splitting one.
- **25 mW is ERP, so antenna gain buys no TX budget** — it is already inside the
  limit. It does buy RX sensitivity, adding margin asymmetrically, which is what can
  move a marginal +3 dBm neighbour into the burst-capable set.

## 7e. Regulatory domain profiles

The limits belong in a per-domain config the interface reads, not in constants. §7d
works one case (25 mW, 1 % duty); the profile is what lets other domains be expressed
without touching the protocol.

**Limits attach to sub-bands, not to the domain.** Duty cycle is a per-band budget, so
the profile is a set of bands each carrying its own limits, plus a set of complete
radio configs (the hail channel and the §7d burst ladder) that each reference a band.
Two burst channels in different sub-bands therefore draw on independent budgets — the
concrete reason §7d recommends the split.

Per band, at least:

- **`max_txpwr` *and its reference*** — ERP, EIRP or conducted. 25 mW ERP is not 25 mW
  EIRP, and a profile that omits the reference is a silent compliance failure no test
  catches.
- **`regime`: duty-cycle or listen-before-talk.** Load-bearing for this design, because
  the burst deliberately stops carrier-sensing after the blip (§7c) — legal where duty
  cycle is the chosen basis, not where LBT is mandated. It must be per **channel**, not
  per domain: some domains impose a *minimum* bandwidth for wideband modulation, which
  can put the hail channel and the burst channel under different rules inside one
  domain.
- **`max_airtime_per_hr`** (or duty fraction), **`max_single_transmission`**,
  **`max_transaction`**, **`min_dead_time`** between transmissions.

**Enforcement is a sized gap, not a new invention.** APPC keeps two 7500 ms bins —
enough to choose a contention band, not to police an hourly budget. An hourly budget
needs 3600 / 7.5 = **480 bins**, which is exactly the upstream RNode ring that
`INTERNALS.md` §6a records as deliberately unimplemented here. Implementing it is the
work; the one requirement beyond upstream is that it be kept **per band**, since that
is the unit the rule applies to.

**Budget is rarely the binding constraint, so this is a compliance requirement rather
than a scheduling one.** Across 7–8 burst channels at ~100 s/hr each, a node can
afford ~728 bursts/hr — roughly **4.9 MiB/hr**, far beyond what a LoRa mesh carries,
and one burst spends 1 % of a single channel's hourly allowance. It binds only a
genuinely saturated node: §7c's 50 % off-channel fairness cap would want ~1548
bursts/hr against 728 affordable. So the accounting must exist to be correct, but the
protocol does not depend on it to behave well, and the NACK's out-of-airtime verdict is
a rarely-exercised safety valve rather than a main path.

**The profile is what makes that NACK actionable** (§0): B can only answer "out of
airtime on this band, try Y" if it is accounting per band against a profile, and Y is
only a real alternative if it draws on a different budget.

## 8. Open items to nail down before building

- **Domain profile schema** (§7e) — the band/channel/limit structure, and a first
  profile filled in for the target allocation: whether a 500 kHz burst channel fits the
  intended sub-band, and whether hail and burst land in the same duty budget or two
  (§7d assumes two).
- **Per-band airtime accounting** (§7e) — the 480-bin hourly ring that `INTERNALS.md`
  §6a records as unimplemented, kept per band rather than per radio. Needed for
  compliance; not a prerequisite for the protocol to behave well, since budget binds
  only a saturated node.
- **Retune turnaround** — the deciding number for small packets (§7b) and for the
  manifest handshake's share of the burst (§7c). Measure the responder's RxDone →
  retuned → armed path on the esp_timer route, not the task route.
- **Blip and manifest-ack wire format** — burst config *index* into the §7e profile
  (not literal `SF/BW/freq`: 8 B costs 40.2 ms and 12 B costs 45.3 ms at SF7, so the
  descriptor has to be a table index), duration for the §3 reservation, a sync word for
  the burst channel; and in the manifest-ack, the verdict, rssi/snr, alternative config
  index and back-off hint. Plus the manifest's framing flag bit (§7c) while the format
  is open.
- **SX1262 specifics** — SF5 reachability through the X-macro `begin()` calls, whether
  RadioLib applies Semtech's BW500 sensitivity-optimisation register write, CAD
  parameters, `GetPacketStatus` SNR/RSSI plumbing, mode-transition + PLL-settle
  timings. Datasheet sensitivities to replace §7d's link-budget estimates.
- **Where the queue comes from.** §7a: Resource is the only subsystem generating real
  next-hop depth today. Confirm a Resource transfer actually presents 14 packets to
  the interface at once rather than pacing them.
- **Fairness policy** — pick the off-channel duty fraction from §7c's table, and check
  the chosen window against the announce cadence so a node does not miss announces.
- **Interaction with current `lora.cpp` / `esp_idf_hal.cpp`** and the shared-radio /
  region config already there.
- **Not needed for the pair burst** (§5): TCXO-vs-XTAL drift budget, slot arithmetic,
  DIO-edge capture. Revisit only for a network-wide synchronized scheme.

---

*Captured from a design discussion; verify SX1262 register/IRQ behaviour against the
datasheet + the `lora` HAL, and the RNS header offsets against the `rns` straddle,
before implementing.*
