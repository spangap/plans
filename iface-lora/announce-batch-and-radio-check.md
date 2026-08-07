# Announce batch + radio check

> Status: **design, nothing built.** Replaces the rfprobe protocol (`iface-lora`
> INTERNALS §14, §14.2) rather than sitting beside it. Timings assume the
> hailing channel at SF7/BW125, CR 4/5, preamble 12 — `plans/psa.md` for the
> regulatory frame, `plans/afa.md` for the channel plan.
>
> Open scoping questions are in §8 and at least the first one gates writing any
> of this.

## 1. The flow

```
      [carrier sense]
A→*   ANNOUNCE₁ ANNOUNCE₂ ANNOUNCE₃      back-to-back, no sensing between
        one bunch, filled to just under 1 s of air — 3 × 276 ms = 828 ms
        at SF7/BW125, at the node's full power
      [carrier sense]                     … repeat for as many bunches as
A→*   ANNOUNCE₄ …                             the buffer needs

      [carrier sense — the tail is won separately and as one piece]

A→*   BATCH₀  = {op, i, txp, n, hash₁ × n}       4 + n B, ~40 ms at n=4
A→*   BATCH₁  = same, i=1                        back-to-back, fixed 5 ms gap
        └─ "that was at <txp>, these are their 1-byte hashes,
            stand by for radio check — and i says how much of this
            preamble is left, so you can time the sweep from either copy"

      [20 ms — everyone retunes: implicit header, preamble 6, sync 0x23]

A→*   SWEEP₇  = {txp, hash₂, step}  × 7          4 B implicit, ~22 ms each
        power walks min → max across the 7, first at the floor, last at full
        hash₂ = the FIRST announce's hash: the only thing tying a headerless
        frame back to a node

      [step 7 seen, or its deadline passed → everyone retunes to SF5]

A→*   SWEEP₁₄ = {txp, hash₂, step}  × 14         4 B implicit, ~6 ms each
        the same walk at SF5, same 0x23 regime, 14 steps

      [→ everyone back to SF7, explicit header, preamble 12, sync 0x42]
```

**One-way throughout.** Nobody answers, nobody is addressed, nothing is
retried. A receiver's entire job is to notice which sweep steps it could
decode; the lowest step it heard is that node's power floor toward it, and by
reciprocity an estimate of what we need to reach them.

The whole tail is **one transmission and is held to 1 s**, carrier-sensed once
before it and never again inside it (§6). At a 4-announce manifest it comes to
~465 ms; at the buffer's 16-entry cap, ~507 ms.

## 2. The announce buffer

Per radio, in RAM.

- **Holds outgoing announces at wire hops 0 only** — our own originations, not
  anything relayed. The tap is the same one `neiObserve` uses on the transmit
  side, so the classification already exists.
- **Keyed by destination hash.** A new outgoing announce for a key it already
  holds *replaces* the stored one; the replacement is not a new entry.
- **Expires at one hour**, whether or not it was ever replayed.
- Otherwise inert: buffering has no effect on the packet going out normally.

Sizing: an announce is 167 B on the wire (`Destination::announce` = pubkey 64 +
name hash 10 + random hash 10 + signature 64, under a 19 B RNS header), plus
whatever `app_data` the announcing side attached. **Capped at 16 entries**,
×  256 B = 4 kB of PSRAM per radio.

The cap is not only about memory: every buffered announce adds a byte to the
manifest, which is sent twice, so the buffer size is what bounds the radio
check's airtime (§6). Sixteen keeps the whole tail near 500 ms without a runtime
check. Eviction at the cap is oldest-first.

**Replay sends the stored bytes verbatim.** The announce is signed over its own
contents including its random hash and timestamp, so it cannot be regenerated
here and must not be altered.

## 3. The batch

```
BATCH   [op][i][txp][n][h] × n
        op    u8    our air protocol, a new type
        i     u8    which copy this is, 0 or 1
        txp   i8    the power the announces above went out at
        n     u8    how many follow
        h     1 B   first byte of each announce's destination hash
```

4 + n bytes. At n = 4 that is 8 B of payload, ~40 ms of air; at the 16-entry cap,
20 B and ~61 ms.

**One byte per announce is a coverage check, not an identifier**, which is what
makes it enough. The receiver already holds the full 16-byte destination hash of
every announce it decoded — it is ticking those off against the list, and a
false tick needs a 1-in-256 coincidence *within this one node's manifest*. A
receiver that heard none of them still learns a node exists and is about to
sweep, which is the other half of what BATCH is for. Nothing here authenticates
anything; the announces carry their own signatures.

**Sent twice, back-to-back, with a fixed 5 ms gap.** BATCH is the only thing
that moves a listener onto the `0x23` regime, so losing it costs that node the
entire check however loudly the sweep is then transmitted — and it arrives at
the one moment the channel is least likely to be clear, immediately after we
finished occupying it ourselves. Two copies roughly double the odds at the
fringe for 45 ms out of a ~465 ms tail.

**The index is what keeps the timing exact.** Both copies are byte-identical
apart from `i`, so their airtime is identical and a receiver can place the sweep
from *whichever* copy it decoded:

```
sweep_start = end_of_this_frame
            + (REPEATS − 1 − i) × (T_batch + BATCH_GAP_MS)
            + LEAD_MS
```

with `REPEATS` = 2, `BATCH_GAP_MS` = 5 and `LEAD_MS` = 20, all protocol
constants. `T_batch` the receiver already knows — it just demodulated a frame of
that length in that modem config. Hearing only the second copy is not a
degraded case; it is exactly as precise as hearing the first.

A receiver that hears both simply ignores the second: the manifest is
idempotent, and the sweep time it computes from each is the same instant.

## 4. The sweep frames

```
SWEEP   [txp][h0][h1][step]      4 B, implicit header, CR 4/5,
        txp   i8   the power THIS frame is being sent at    preamble 6,
        h₂    2 B  the FIRST announce's hash from the batch sync word 0x23
        step  u8   phase in the high nibble, index in the low
```

A distinct modem regime, and all three parts of it earn their place:

- **Implicit header**, so both ends must agree the length statically — 4 bytes,
  always. Saves the 20-bit PHY header on a frame whose payload is 32 bits.
- **Preamble 6**, the minimum. Nothing is being discovered: the receiver was
  told to expect these and is listening on a schedule.
- **Sync word `0x23`**, against `0x42` for ordinary traffic. The correlator on a
  node that is not in a radio check will not match, so sweep frames are silent
  to it rather than a stream of length-mismatched garbage and CRC errors. It
  also means our own receiver cannot mistake a sweep for RNS traffic outside the
  window. Same value the retired probe used for the same reason; it frees up
  with rfprobe and is worth keeping.

Both sweep phases share the regime — SF5 changes the spreading factor and
nothing else.

**BATCH is therefore load-bearing** — a node that misses it never switches to
`0x23` and sits out the whole check. That is why it goes at full power and why
it is sent twice (§3).

`h₂` is what couples a headerless frame to a node. Without it a sweep frame is
anonymous and a receiver could not attribute a power floor to anybody.

**It stays two bytes even though the manifest dropped to one, because shrinking
it would save nothing.** Payload lengths of 3 and 4 bytes cost the *same* number
of symbols — the `ceil()` in the time-on-air formula quantises them together, 13
symbols at SF7 and 18 at SF5 — so a 3-byte frame is exactly as long on air as a
4-byte one. Five bytes is where it jumps, so four is the largest free payload.

And the two hashes are doing different jobs. The manifest's byte checks off an
announce the receiver already holds in full; the sweep's tag has to pick one
sender out of everyone in earshot, from a frame with no header and no address.
Temporal context does most of that work — sweeps are only listened for in slots
computed from a specific node's BATCH — but with a byte going spare there is no
reason to lean on it harder than necessary.

**`step` is not in the original sketch and I have added it.** The cue to move to
SF5 was "receiving the last one", which fails closed in exactly the case the
sweep exists to measure: a receiver too far away to hear step 7 — the *full
power* frame — would be the one node that never follows to SF5. With an index,
a receiver that hears any step knows how many remain and can time the
transition; the last frame stops being load-bearing. Phase in the high nibble
distinguishes the SF7 walk from the SF5 one.

Power ladder: `N` steps spanning `[min, configured max]` inclusive, so
`step_dB = (max − min) / (N − 1)`. Over the SX1262's −9…+22 dBm that is 5.2 dB
at N = 7 and 2.4 dB at N = 14.

## 5. Scheduling

- **Every 15 minutes**, per node, decoupled from rnsd's own announce timing.
- **Jittered.** A fixed period across a fleet synchronises every node onto the
  same minute boundary and they collide forever. ±10 % uniform is enough to
  scatter them and costs nothing.
- **`lora a[nnounce]`** runs the same sequence immediately, and resets the
  timer.

The replayed announces are *additional* to whatever rnsd emits on its own
schedule — "completely decoupled" read literally. That does mean a destination's
announce goes out more often than rnsd intends, which is a deliberate cost of
having the radio check carry a manifest that means something.

## 6. Politeness and airtime

Two sequences, each won once. Sensing per announce is not worth it — a backoff
between every frame turns 828 ms of air into several seconds of wall time for no
gain, since nobody else can usefully interleave anything in the gaps anyway.

**Announces go out in bunches of just under a second.** One channel access, then
frames back-to-back with no sensing between them, filling the bunch until the
next announce would push cumulative air past `Ton_max`. At SF7/BW125 that is
**three** announces — 828 ms, where a fourth would make 1104 ms. The buffer is
drained in as many bunches as it takes, each re-sensing.

The 1 s figure is `plans/psa.md` §2.4's single-transmission ceiling, used here as
the bunch size even though a run of separately-framed packets would more
naturally be a dialogue. Taking the tighter of the two is free — nothing wants
bunches longer than a second — and it means the bunching rule does not depend on
which reading of `Ton` a regulator applies.

**The radio check is one uninterruptible sequence, sensed separately, and held
to one second.** From the first BATCH to the last SF5 frame nothing may be
sensed: every gap in it is a schedule the receivers are counting on — they
compute the sweep start from a BATCH copy and the fixed constants — and a
backoff inserted anywhere inside desynchronises all of them.

**We do not claim the dialogue exemption for it.** `plans/psa.md` §2.6's 4 s
ceiling is for a dialogue or polling sequence, which implies an exchange; this
is one node transmitting one-way with nobody answering, so the honest reading is
a single transmission and the honest ceiling is **`Ton_max` = 1 s**. It fits with
roughly half the budget spare, so there is nothing to gain by arguing for the
looser clause — and a great deal to lose if the argument is later found wanting.

| | at n = 4 | at n = 16 (the cap) |
|---|---|---|
| BATCH × 2 + gap | 85 ms | 127 ms |
| lead-in | 20 ms | 20 ms |
| SF7 sweep, 7 slots | 210 ms | 210 ms |
| retune to SF5 | 10 ms | 10 ms |
| SF5 sweep, 14 slots | 140 ms | 140 ms |
| **total** | **~465 ms** | **~507 ms** |

A pleasant consequence: this design now needs nothing from §2.6 at all, so the
open question about the dialogue exemption's exact conditions stops gating it
and gates only the burst channel in `proper-air-protocol.md`.

- Channel access is taken once, before BATCH, **with the tail's whole duration
  as the requirement** — not just BATCH's.
- The duration is computed before transmitting, from the manifest that will
  actually be sent. If the airtime ledger cannot cover it, the radio check is
  skipped rather than started and abandoned. A half-sent sweep is worse than
  none: receivers would attribute a power floor to a node that never finished
  walking up.
- **The manifest is what makes the total variable, so the buffer is capped at
  16 entries** (§2). That bounds the tail below 510 ms by construction rather
  than by a runtime check that could fail late.
- The announce bunches come **first and are budgeted separately**, so a long
  buffer can never eat the tail's allowance. If the ledger runs dry part way
  through, the remaining bunches are dropped and the check still runs, with a
  manifest listing only what actually went out.

**Cost per cycle.** Six announces plus the tail is ~2.0 s of air every 15
minutes, so **~8 s/h**. Against a 10 % duty cycle that is nothing; against 1 %
(36 s/h) it is a fifth of the budget; against 0.1 % (3.6 s/h) it does not fit at
all and the period would have to stretch. Worth knowing before the hailing
channel's placement is fixed — `plans/psa.md` §1.3.

## 7. What this replaces

Retired outright, code removed:

- `lora rf[probe]` and its two-node fixed-time schedule (INTERNALS §14)
- the 0x02 / 0x03 cooperative hash-linkage frames (§14.2)
- the probe's slot timer, `ProbeState`, and the sweep regime it installs

**The passive neighbour table (§13) stays, and remains what couples nodes.** It
is not "our protocol" in any sense — it is built from observing rx/tx RNS
traffic, with the cryptographic identity join that clusters destination hashes
under one key, and it works against every RNS implementation including nodes
that will never run this firmware.

The pleasing part is that the announce batch needs no new coupling path at all:
**a replayed announce is an announce**, so it arrives at `neiObserve` and runs
the existing hops-0 ingest and identity join unchanged. The batch is simply a
node volunteering, every 15 minutes, exactly the input the table was already
built to consume. What the radio check adds on top is the power floor, which
observation alone cannot give.

Kept, but now fed from the radio check instead of from rfprobe:

- **adaptive TX power** (§15) — the radio check produces a per-neighbour power
  floor, and **every node's transmit power is from here on a reciprocity
  guess.** One-way costs us the peer's *measurement of us*, which rfprobe
  returned in its echo; what remains is our own measurement of them, which is
  the half reciprocity actually needs.

### 7.1 What a receiver derives, and why reciprocity is safe here

Each decoded sweep frame is a complete link-budget sample on its own, because
**the frame states the power it was sent at**. Two readings come out of a sweep:

- **The floor, directly.** The lowest step that decoded is the least power that
  node needs to reach us — measured, not computed, and needing no RSSI
  calibration at all.
- **Path loss, from every step.** `txp − rssi` for any decoded frame. Finer
  than the floor because it uses all of them rather than just the boundary, and
  it yields a margin rather than a threshold.

The self-stated power is what makes this sound. `plans/adaptive-power.md` §3
warns that passive reciprocity "silently assumes the far end's TX power is
*constant* … false the moment the peer is itself adapting, and then two loops
chase a reference each is moving and can diverge with both ends too quiet."
**That failure mode does not apply here.** Reciprocity is being applied to
*path loss*, not to received strength at an unknown transmit power — a peer that
is adapting still tells us what it transmitted, so the derived path loss is
unaffected by whatever its loop is doing. The divergence the passive scheme
risks is closed by three bytes on the wire.

What reciprocity still cannot give us is the **noise floor at the far end**.
Path loss is symmetric; ambient noise is not, and a node sitting next to an
interferer needs more power from us than our own quiet receiver would suggest.
That is a margin, not a measurement, and it stays a margin.

`plans/adaptive-power.md` §3's feedback ladder needs revising once this lands —
its cooperative and µR-only rungs both describe rfprobe machinery that is being
removed.

## 8. Order of work

**The removal and the addition are not separable**, which is worth knowing
before starting. Adaptive TX power sits physically and logically between them:
`apSettle` is interleaved among the probe functions, and `apPoll` — its whole
driver — works by raising `probe.req`. Deleting rfprobe first leaves adaptive
power with no source; adding the radio check first leaves two power measurement
paths briefly coexisting. The second is much the safer order:

1. Announce buffer (§2), fed from the existing hops-0 transmit tap.
2. BATCH + sweep transmitter (§3, §4), the bunching and channel access of §6,
   the 15-minute schedule of §5, and `lora a[nnounce]`.
3. Sweep receiver: BATCH → schedule → `0x23` regime → power floors into the
   neighbour rows.
4. Repoint adaptive power at those floors — `apPoll` stops raising `probe.req`.
5. Only then delete rfprobe, the 0x02/0x03 linkage, and `lora rfprobe`.

Two name collisions to avoid in step 5: **`probeRadio()` is chip detection**, not
rfprobe, and stays; and **`RF_PROTO_NAME` / `Neighbor.ourProto`** is the `lora n`
tag for "runs our code", which survives — the radio check becomes what sets it,
in place of an rfprobe run or a linkage frame.

## 9. Settled

**"Full power" is the node's configured `tx_power`, not the chip's ceiling.**
The ladder spans `[chip minimum, s.lora.<n>.tx_power]`, so a radio held to
14 dBm for regulatory reasons stays there and the sweep simply has a shorter
run. Nothing in this protocol may raise a node above what it was configured to
transmit at.

**Replayed announces do not count against `s.lora.<n>.announce_cap`.** The cap
governs the announce queue rnsd rebroadcasts on behalf of the network — it is
there to stop a slow segment drowning in other people's announces. A replay is a
node re-stating its *own* originations, bypasses rnsd entirely, and is already
bounded twice over: sixteen entries and one cycle per fifteen minutes.

The airtime is still real, and still lands in the polite-access ledger — §6's
~8 s/h. It is exempt from the cap, not from the budget.
