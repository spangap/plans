# SUPE — Spectrum Utilization and Performance Enhancements

> Status: **specification.** Everything normative is here: the frames in
> §0.3, the schedule in §7, the meeting in §8, the failure ladder in §12, the
> regimes with their channels, ladder, sync words, timings and limits in §14,
> and adaptive power in §15. The ladder's conformance vectors in
> `supe-ladder-vectors.txt` are the §14.3.4 authority; the schedule and timing
> constants of §7 and §14.7 are stated values, and
> [`simulation.md`](simulation.md) is where they are meant to be settled.
>
> `sender_ident` (§4) is what makes the return leg and the hail-back possible
> at all, and it is the one thing here that gives up sender anonymity, so it
> is a key and not a constant.
>
> SUPE turns unicast traffic on a shared LoRa channel into meetings, entirely
> inside the modem, with the Reticulum daemon unmodified and unaware. Where
> the shared channel is all there is, a meeting is a dialogue on it: one
> short frame asks, the peer answers, and the traffic follows at the best
> rate the link supports. Where a channel plan gives it somewhere to go, a
> meeting is private — at derived times, channels and sync words — and one
> frame on the shared channel seeds everything; every meeting's own goodbye
> seeds the next, so a pair with steady traffic touches the shared channel
> once.
>
> A hail is a question, and the party it names is the one that answers it —
> at once if it can, and by hailing back when it can if it cannot. Nothing
> is transmitted toward a node whose presence has not just been demonstrated,
> and a node that is busy chooses its own moment to be reached.
>
> SUPE also adapts transmit power to what is actually needed, so two nodes in
> the same room might be carrying their unicast traffic at 125 µW.
>
> Companion documents carry derivation rather than specification —
> [`afa.md`](afa.md) for how the channel plan and the ladder's margins follow
> from the regulation, and [`psa.md`](psa.md) for access to the hailing
> channel, which SUPE does not own.
>
> A node speaking it is a SUPE RNode.

## 0. The flow

Two run-throughs, because a regime (§3, §14) decides what a hail can lead to.
**Regime 0** is the floor every node supports: one frequency, one bandwidth,
and no way to leave either. A hail there opens a *dialogue* — the peer answers
it directly, and everything that follows rides inside the one transaction the
hail cleared the channel for. It is the protocol with the least in it, and it
is where to read the protocol first. **Regime 1** is what a channel plan adds —
here the European 863–870 MHz plan — and with a frequency to go to, a hail
opens a *schedule* instead: derived appointments on channels nobody else is
camped on.

### 0.1 Regime 0 — one channel, one dialogue

Everything below happens on the one frequency the network hails on, under the
interface's own sync word, and the hail is the only frame that is
carrier-sensed: what answers it, and what follows that, is the same
transaction, on the clearance the hail obtained. There is no appointment to
derive because the appointment is *now*, and there is nowhere to go because
there is nowhere else.

A regime-0 dialogue comes in two shapes, and GIMME's budget byte is what
selects between them. **At a lowered spreading factor** — any budget above 0 —
it is a full meeting: train, closing checksums, one repair round, and a return
leg if the peer has traffic, all inside the transaction ceiling the hailing
channel is under. **At the hailing rate** — budget 0 — it is three things and
nothing else: the hail, the GIMME, and the frames. A resend at the hailing
rate costs exactly what the daemon's own retry costs, so the closing frames
would buy nothing, and the receiver hands frames up as they land, as it would
plain traffic.

```flow
the hailing channel (the frequency and modulation this network hails on;
                     everyone camps here, carrier-sensed)

  telling the neighbourhood who we are — once per announce interval
    A→*   ANNOUNCE  {type, regime/version, capabilities,
                     power this went out at,
                     4-byte identity hash × count}                5+4·count B
          └─ hashes last, so the count needs no byte of its own
          └─ regime 0 / version 0, and a neighbour on any other regime
             does not meet us

  A has traffic for B
    A→*   HAIL      {type, regime/version, tag, power this went out at,
                     salt, budget ceiling, count, length,
                     A's identity}                                  10 or 13 B
          └─ the hail. It says four things at once: A holds traffic for
             whoever holds this tag; this is how much, and the fastest
             A proposes to send it; answer me now; and if you cannot,
             hail me back when you can
          └─ carrier-sensed — the one frame of the dialogue that is.
             Everything that answers it rides on this clearance, inside
             the transaction ceiling the hailing channel is under
             (4 s in Europe, §14.2)

    B→A   GIMME     {type, 3 bytes of the hail's hash, power, budget,
                     count ceiling, how the hail was heard}               9 B
          └─ a turnaround later, at the hailing rate — the only rate A
             can be listening at, since nothing has been agreed
          └─ "I am here; send at this budget, this many frames at most."
             The hash bytes prove it answers THIS hail
          └─ the budget is B's choice, never above A's ceiling: 0 is the
             hailing spreading factor, 1 one step faster, and how far the
             ladder reaches is what both radios can do (§14.1). The byte
             selects which of the two shapes below follows
          └─ the hail reading is B's headroom at the hailing rate — what
             the budget choice runs on — and the only measurement of the
             direction A transmits in
      -or- HAVE      {…, budget ceiling, count ceiling, count, length,
                      how the hail was heard}                            11 B
          └─ B holds traffic for A as well: GIMME's terms with B's own
             train behind them. A answers GIMME, confirming one budget
             for both trains, and B's goes first

  at the hailing rate — GIMME said budget 0
    A→B   the frames × count, at the hail's stated power, flip-gap apart
          └─ LoRa frames in the interface's ordinary framing. B hands
             each up as it lands. Nothing follows: no checksums, no
             repair, no goodbye — a lost frame here is the daemon's to
             retry, as it always was
          └─ where B opened with HAVE, B's frames go first and A's
             follow a turnaround after B's last — each side holds the
             other's count and length, so nothing needs to sit between
          └─ what the dialogue bought over sending blind: 77 ms of probe
             before 760 ms of frame, proof B is there, one reading, and
             one carrier-sense contest for the whole batch

  at a lowered spreading factor — GIMME said budget ≥ 1
    both retune: A on GIMME's RX-done, B on its TX-done, one retune gap
    A→B   the frames × count — at the confirmed budget: the first frame
          at the new rate is the first frame of the train
    A→B   THATSIT   {type, power of the train just sent,
                     salt, 1-byte checksum × count}                   3+n B
          └─ frames carry no sequence numbers; the checksum list IS the
             sequence, and B aligns what it holds against it
    B→A   BYE       {type}                                                1 B
      -or- RESEND    {type, bitmask of the missing}                 1+⌈n/8⌉ B
          └─ ONE repair round, at a quarter of the hailing rate's price:
             A resends exactly those frames, B answers BYE or gives up
      -or- HAVE      {…, how the train was heard, resend bitmask}  11+⌈n/8⌉ B
          └─ B's turn: the answering HAVE carries the train's reading,
             any repair request, and B's own count and length; A's
             resent frames ride its closing turn
            B→A  the return frames × count
            B→A  THATSIT
            A→B  BYE -or- RESEND
    the whole dialogue, both trains and both repairs, inside the
    transaction ceiling; each train inside the length byte's 1.275 s

when the hail goes unanswered (§12)
    A, no GIMME by the deadline   waits an interval, camped, holding the
                                  traffic: B may be about to hail back
    B, if it heard the hail       and could not answer — its own frame
                                  was going out — owes A a hail, sent
                                  the moment it is free with a count of
                                  zero; A, hailed now and holding
                                  traffic for B, answers it with HAVE,
                                  B confirms with GIMME, and A's frames
                                  follow (§8)
    A, after the interval         hails again, louder; the third time at
                                  maximum; the third unanswered files B
                                  unreachable for a while, and what is
                                  queued leaves at its own patience
```

**Nothing is negotiated and nothing is waited for.** A hail is answered or it
is not; a GIMME names a budget and the frames follow at it. Every deadline is
arithmetic on frames both ends have seen, and a deadline missed means go home
holding what you hold.

**The probe is the point at the hailing rate.** Sent blind, a 500-byte packet
spends 760 ms of everyone's channel to find out whether the peer was there.
Hail and GIMME spend 77 ms to find out first, and return a path-loss reading
for free. Whether to hail at all is therefore an equation, and §18 leaves it
to one function: the plain airtime times the chance the peer is absent, plus
what batching saves, plus what a lowered rate saves, against the probe's cost.
In practice a lone short packet to a peer that answered recently goes plain;
anything longer, any budget above 0, any queue deeper than one, or any peer
whose record is doubtful, hails. A peer whose radio listens at several
spreading factors at once (§14.6) moves the line: to it a plain packet may go
at a lowered rate with no dialogue, and the probe pays only for the batch and
the report (§15).

**What regime 0 cannot do is leave the frequency.** A dialogue at any rate
still occupies the hailing channel, and every node camped there senses it as
busy for its length — so a meeting here is cheaper than the same frames sent
one by one, never free. A train at a lowered rate is energy without frames to
a listener at the hailing rate: the carrier sense sees it, the contention
estimator of [`psa.md`](psa.md) cannot decode it, which is the position a
hidden node already puts every estimator in. Both are what a channel plan is
for.

### 0.2 Regime 1 — nine channels, and what a channel plan adds

A regime with a channel plan gives a hail somewhere to send the meeting: a
frequency nobody is camped on. That turns the dialogue into a schedule —
appointments derived from the hail's own bytes, on channels and under sync
words the hail never names — and it makes the closing frames worth having at
every budget, because a resend on a private channel costs no contest with
anybody. The frames, the roles and the run are those of §0.1; what follows is
where a channel plan changes the answer. Regime 1 is the plan for ETSI EN
300 220 in 863–870 MHz (§14.2): nine 500 kHz channels between the alarm and
audio allocations, each with its own airtime budget, and a hailing frequency
that is never one of them.

```flow
the hailing channel (channel 0 — unchanged: every hail and every ANNOUNCE
                     goes out here, at the hailing configuration)

    A→*   ANNOUNCE  {…, regime 1 / version 0, …}
    A→*   HAIL      {…, salt, budget ceiling, count, length, …}      10 or 13 B
          └─ identical, and still the only frame this protocol ever
             puts on the hailing channel per conversation. Here it is a
             SEED as well as a question: the schedule below is a pure
             function of its bytes, which is what the salt is for

derivation (both ends, independently — §7)
    epoch      = the end of the HAIL, as each radio timed that RF event
    slots      two: a moment, a CHANNEL 1..9 and a sync word each, from
               the frame's hash; the second is never on the first's
               channel, so a busy channel is answered by a different one
    who        B speaks at both, A listens at both — the hailed party
               speaks first, because the hail was a question and B holds
               the answer; A, having sent the seed, is on the channel
               with its receiver open before there is anything to hear
    modulation = the hailing spreading factor and bandwidth, on that
                 channel, under that word
    horizon    = a quarter of a second, then the hailing channel again

the slot (channel_k, sync_k — a private frequency, not just a private
          word; nobody camped on the hailing channel hears what follows.
          The speaker senses first — the regime's absolute threshold,
          its own ledger for THIS channel, and the 100 ms before reusing
          a frequency, §14.2 — and any of the three failing skips the
          slot as a busy carrier would)

    B→A   GIMME     {…, budget, count ceiling, how the hail was heard}    9 B
          └─ the budget now indexes a ladder with bandwidth in it: from
             SF7/BW125, nine entries up to SF5/BW500 on a 500 kHz
             channel, six on a 250 kHz one (§14.3); both ends resolve it
             against the channel they both derived
      -or- HAVE      {…, budget ceiling, count ceiling, count, length,
                      how the hail was heard}                            11 B
          └─ B holds traffic for A as well: B's train goes first, A
             answers GIMME, and A's own train — described in the hail —
             rides A's answering turn. Whichever B sends, B sends first,
             and nothing A transmits on this channel is sent into silence
    A→B   the frames × count, at the confirmed budget, then THATSIT,
          then BYE / RESEND / answering HAVE — the full meeting of §0.1's
          lowered-rate shape, at EVERY budget: the train is bounded by
          1 s and the meeting by 4 s (§14.2), and every frame of it is
          credited to this channel's ledger, not the hailing channel's

the goodbye seeds again (§7)
    the same arithmetic, wide: slots from epoch′+150 ms, widening and
    jittered, never further apart than ~350 ms, horizon 3 s; each a
    moment, a channel and a word, at the budget this meeting confirmed,
    resolved against each slot's own channel
    slots alternate B,A,B,A… — B first, because whoever just RECEIVED
    is the likely replier. Here the holder of traffic speaks first,
    blind: nobody asked a question, so nobody owes an answer
    …and every meeting's goodbye reseeds the next. A pair with steady
    traffic touches the hailing channel once, ever

when the two slots pass unmet (§12)
    exactly §0.1's run — an interval, a louder hail, a hail-back from a
    B that heard and could not attend — with one more reason for B to
    have missed: it was mid-frame on the hailing channel when the slot
    came, and a frame in the air outranks every slot (§7)

what a hop costs
    a retune is microseconds on the silicon and one millisecond in the
    protocol's arithmetic (§14.7); a node at a meeting is deaf to the
    hailing channel for the meeting's length, in either regime — the
    difference here is that the hailing channel is deaf to IT
```

**The private cost is bounded by construction, and nothing private flies
blind.** A hail is answered by the party it named, at a slot that party
chooses to keep, so the first frame on a traffic channel is always sent by a
node that has just heard the other. A listener spends at most a guard and a
preamble per slot; everything expires at the horizon. Any feature that can
break this — an unbounded dwell, an unbounded retry, a frame transmitted toward
a peer that has not just been heard — is rejected on sight.

**The shared cost is unknowable, so the shared channel is the last resort, not
the baseline.** Nobody knows what a hailing transmission costs until they have
paid it — two carrier-senses against whatever the segment is doing, at whatever
band the contention ladder has climbed to — and part of the price is paid by
everyone else. What this protocol puts there is short and bounded: a hail, and
at worst a hail back. Its value grows with segment load, because that is when
the shared channel is dearest, and it is greatest under a channel plan, where
every frame after the hail costs the shared channel nothing.

**A channel plan changes where a meeting is, not what it is.** No frame gains
a field for it: the hail carries the regime nibble, both ends derive the
channel from bytes they already hold, and the budget byte means what the
slot's channel says it means. Regime is the only radio choice SUPE exposes. A
network chooses one, every node on it runs the same one, and a node whose
regime has expired stops speaking SUPE rather than speaking a stale dialect
(§3). Regime 0 needs no regulatory basis and runs anywhere; a regime with a
channel plan is a claim about a jurisdiction, and regime 1 is the one this
protocol currently makes.

### 0.3 Every frame and its fields

Field order, widths and type values below are normative.

Every frame's full name carries the prefix `SUPE_` — `SUPE_HAIL`,
`SUPE_ANNOUNCE`, `SUPE_GIMME` — and that is the name anywhere the frame could
be confused with anything else. This document is about nothing else, so it
omits the prefix throughout.

Every SUPE frame opens with its type byte, and that byte plus the regime and
version nibbles behind it (where carried) determine the frame's permitted
length exactly:

| Type | Frame | Sent on | Length |
|---|---|---|---|
| `0xC0`, `0xC1` | **never assigned** | | see below |
| `0xC2` | HAIL | main channel | 10, or 13 with `sender_ident` |
| `0xC3` | ANNOUNCE | main channel | 5 + 4·count |
| `0xC4` | HAVE | traffic channel, or the dialogue | 11 opening a slot; 11 + ⌈n/8⌉ answering a THATSIT |
| `0xC5` | GIMME | traffic channel, or the dialogue | 9 |
| `0xC6` | THATSIT | traffic channel, or the dialogue | 3 + count |
| `0xC7` | BYE | traffic channel, or the dialogue | 1 |
| `0xC8` | RESEND | traffic channel, or the dialogue | 1 + ⌈count/8⌉ |
| `0xC9`, `0xCA`–`0xCF`, `0xD2`–`0xDF` | reserved | | discard |
| `0xD0`, `0xD1` | **never assigned** | | see below |

**A type value never ends in 0 or 1.** An interface is free to put framing of
its own ahead of the Reticulum packet, and one that does will have its own
reachable byte values in this space. The framing this protocol was first built
on carries a random 4-bit sequence in the high nibble and a single flag in bit
0, which reaches every value ending in 0 or 1 and nothing else. Conceding those
four values costs nothing, and it means a SUPE frame can never be confused with
a framing byte from a node that has never heard of SUPE. Any interface that
prepends framing must check its own reachable set against this rule before
carrying SUPE.

**Levels and powers are `dBm + 64`, read as a signed byte.** Every field
carrying a transmit power or a received signal level uses that one encoding, so
the representable range is −192 dBm to +63 dBm at 1 dB. The offset is what buys
the bottom: a plain signed byte stops at −128 dBm and receivers already report
below −130, while nothing needs the top — +63 dBm is two kilowatts, and the
regulation caps radiated power at 14 dBm (§14.2). More sensitive receivers are
a great deal likelier than five-gigawatt transmitters, so the range is spent
accordingly.

**A stated transmit power is the power the frame actually left at, after every
cap.** Not the configured power, not the requested one. Every path-loss figure
in this protocol is a difference between a stated power and a measured level,
so a node that states what it intended rather than what it did corrupts the
other side's arithmetic by exactly the amount it was capped — silently, in the
direction that makes the link look better than it is.

**Frames that carry no power field fly at their sender's most recently stated
power.** The hail states the hailer's; a GIMME or HAVE states its sender's;
a THATSIT states the power of the train it closes — which may differ, the train
being that side's chance to act on the freshest reading it holds, at the
budget's own modulation (§15) — and RESEND, BYE and a repair round's frames
fly at whatever their sender last stated. A train that no THATSIT will follow
— the hailing-rate dialogue of §0.1 — flies at the hail's stated power, since
nothing could state otherwise afterwards. So any level measured pairs with a
known power, and the small frames stay small.

**Signal-to-noise is a separate signed byte in quarter-decibels**, covering
−32 dB to +31.75 dB. It is a ratio rather than a level, needs no offset, and
that is both the range and the resolution the silicon reports natively.

**The main channel carries only what it takes to open a meeting.** Nothing
else belongs there. Measurements, the receiver's terms and every verdict move
to the answer, which in a regime with a channel plan is on a channel that is
faster and bothers only the two parties involved.

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **HAIL** | type | 1 | `0xC2` — `110` protocol bits then 5 bits of type (§3) |
| | regime / version | 1 | a nibble each — fixes how everything after it is read, and whether a schedule is derived from it at all |
| | tag | 3 | first three bytes of an address the peer holds (§6): the first address field of the packet about to be sent, or, for a hail-back, the identity the hail being answered named |
| | power | 1 | `dBm + 64`, signed — what this frame went out at, so the peer's own reading becomes a path loss immediately |
| | salt | 1 | random — the seed's freshness where the hail seeds a schedule (§7). The schedule is a pure function of this frame's bytes, and everything else here can repeat exactly: the same tag, ceiling and count at the same power would derive the same channels in the same order, every attempt. One byte gives 256 distinct schedules per otherwise-identical request. Regime 0 derives nothing from it and carries it so that the frame has one layout |
| | budget ceiling | 1 | the highest budget (§14.3) the hailer proposes for its train. The ladder resolves against the channel the meeting lands on: channel 0 in regime 0, the slot's under a plan |
| | count | 1 | LoRa frames in the train the hailer holds for this tag |
| | length | 1 | how long that train takes at the ceiling — airtime plus the flip gaps of §14.7 — in 5 ms steps |
| | sender identity | 0 or 3 | first three bytes of one of the identity hashes the sender's own ANNOUNCE lists — the same one every time, so the far end files one node — under `sender_ident` (§4); presence is implicit in the frame length. Without it the peer can neither recognise its queued traffic as ours nor hail us back |
| | | **10 or 13** | |
| **ANNOUNCE** | type | 1 | `0xC3` |
| | regime / version | 1 | a nibble each; regime `0xF` is **not a regime** — see below |
| | capabilities | 2 | as below |
| | power | 1 | `dBm + 64`, signed — what this frame went out at |
| | identity hashes | 4 × count | first four bytes of each identity this node holds — last, so the count is implicit in the frame length |
| | | **5 + 4·count** | |

**Regime `0xF` — "I do not speak SUPE."** An announcement carrying it is a node
stating that it will not meet: a neighbour holding the opposite belief drops it
on the spot and goes back to plain framing for that node. The frame is otherwise
unchanged and its identities are read as usual, because a neighbour dropping the
belief still wants to know which node it is dropping it for.

Silence cannot say this. A node that has spoken SUPE and then stops is
indistinguishable from one that has gone out of range, and the neighbour would
keep trying to meet it for as long as its ladder allowed. So the statement is a
frame, and it is repeated on the ordinary announce interval — a neighbour that
boots later, or misses the one that announced the change, would otherwise hold
the wrong belief indefinitely.

It is not a dialect, so it is not checked against one: any version nibble
decodes. An unknown *real* regime is still discarded, because that one is a
claim about a dialect this node does not have.

A node in this state still **reads** everything. It ingests announcements and
keeps its picture of the neighbourhood current — who speaks the protocol, what
their radios can do, what the path loss to them is — so the switch can be thrown
back without waiting to rediscover anyone. What it stops doing is answering: no
hail is answered, no slot is attended, no hail is owed.

**Capabilities** — two bytes, carried by ANNOUNCE:

| Field | Size | Meaning |
|---|---|---|
| family / ceiling | 1 | a nibble each: this node's radio family (§14.6) — which for family 4 also fixes the spreading factors it listens at on the hailing channel — and the highest budget it will accept |
| maximum power | 1 | `dBm + 64`. The top bit is free — a transmit power never stores a negative value — and currently unassigned. It must never be spent on a level field |

**The answer, and the meeting** — every frame below is sent by a node that has
just heard the other: in regime 0 a turnaround after the hail, on the hailing
channel; under a channel plan at a slot of the schedule (§7), or inside the
meeting that slot opened. The hail hash is the first three bytes of SHA-256
over the hail exactly as it was transmitted. **HAVE is GIMME with a train
behind it**: the same fields, then a count and a length of its sender's own.

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **GIMME** | type | 1 | `0xC5` |
| | hail hash | 3 | which hail this answers. Under a plan, which schedule this meeting belongs to; a mismatch is somebody else's meeting, or a stale schedule — either way, discard and stay home |
| | power | 1 | `dBm + 64`, signed — what this frame, and every frame this side sends in this meeting, goes out at |
| | budget | 1 | the budget the hailer's train will fly at — the receiver's choice, never above the hail's ceiling, never above either node's capability ceiling, resolved against the channel the meeting is on. In regime 0, 0 selects the hailing-rate dialogue and anything above it the full meeting (§8) |
| | count ceiling | 1 | the most frames this side will hold: a RAM promise (§8). The hailer's train is trimmed to it |
| | heard | 2 | level (`dBm + 64`) and signal-to-noise (quarter-dB) of the frame this GIMME answers: the hail — the one measurement of the hailer's direction at the hailing configuration — or an opening HAVE |
| | | **9** | |
| **HAVE** (opening) | … | 9 | GIMME's fields — its `budget` byte read as the highest budget this side proposes, since here it is the sender of the coming train; its `heard` the hail's where it answers one, and on a wide slot the last frame heard from the peer — then: |
| | count | 1 | LoRa frames in this side's own train — never above a count ceiling already received |
| | length | 1 | how long that train takes at the proposed budget, in 5 ms steps |
| | | **11** | |
| *the frames* | — | — | not SUPE frames. LoRa frames in the interface's ordinary framing — its checksum, its split handling — transmitted back to back, separated by the flip gap of §14.7. SUPE decides where and at what rate they fly, and touches nothing else about them |
| **THATSIT** | type | 1 | `0xC6` |
| | power | 1 | `dBm + 64`, signed — what the train it closes went out at, and what this frame and its sender's frames fly at until stated otherwise. Stated after the fact, because the train's power is chosen on the freshest reading, at the confirmed budget's modulation (§15) — a power stated in advance would be a power chosen blind |
| | salt | 1 | random — this goodbye's freshness, for the same reason the hail carries one (§7). A one-frame train's THATSIT is otherwise a type, a power that rarely moves and one checksum: a few hundred distinct frames, so two ordinary meetings would close identically and seed the same wide schedule at two epochs with two roles |
| | checksums | 1 × n | one byte per frame of the train just sent, in transmission order (§8). The count is the frame's own length minus three, and is the count that actually flew |
| | | **3 + n** | |
| **BYE** | type | 1 | `0xC7` — every frame accounted for; the meeting is over |
| **RESEND** | type | 1 | `0xC8` |
| | bitmask | ⌈n/8⌉ | bit *i* set: frame *i* of the checksum list is missing. n is the count both sides hold |
| | | **1 + ⌈n/8⌉** | |
| **HAVE** (answering a THATSIT) | … | 11 | the opening form's fields, its `budget` the meeting's confirmed one and its `heard` now the train just received — the worst frame's, since that is the one the power controller must clear — then: |
| | resend bitmask | ⌈nₚ/8⌉ | over the *peer's* train just described by its THATSIT; all-zero when nothing is missing. nₚ is known to both sides, so the length is enumerable |
| | | **11 + ⌈nₚ/8⌉** | |

Three rules produce the shapes above, and they should hold for anything added
later:

- **Every frame has a length the receiver can enumerate** from its type and
  from state both sides already hold — one value for most, two for HAIL and
  HAVE, count-derived for ANNOUNCE, THATSIT, RESEND and the answering HAVE.
  Nothing is signalled by a flags byte and nothing is negotiated, so anything
  outside the enumerated set is discarded (§3).
- **On the main channel, count frames, not bytes.** With no checksum, every
  payload up to the quantum in §3 costs the same. HAIL's ten bytes fill a
  symbol group at SF7 exactly, and its thirteen the next; shaving below either
  buys nothing, which is why the hail carries the train's description rather
  than a frame of its own doing so.
- **Everywhere else, also count frames, not bytes.** A short frame is
  preamble-dominated, so folding a field into a frame that is already going
  out is nearly free while adding a frame costs its preamble plus a turnaround.
  That is why measurements, budgets, ceilings and repair requests all ride
  inside frames that exist anyway.

No SUPE frame carries a cyclic redundancy check (§3); the LoRa frames of a
train keep the interface's, being none of SUPE's business.
## 0.4 The words

Every term a node writes into a log is defined here, so that a line in the field
and a paragraph in this document are talking about the same thing.

| Term | Means |
|---|---|
| **hailing channel** | the shared channel the network already runs on, where every node listens by default |
| **hail** | a HAIL on the hailing channel: one short frame declaring that traffic is waiting and how much, asking to be answered, and asking to be hailed back if it cannot be |
| **dialogue** | regime 0's meeting: the hail and everything that answers it, inside the one transaction the hail cleared the channel for |
| **hailer** | the node that sent a hail; **hailed party**, the node its tag names |
| **hail-back** | a hail sent because one was received and not met. On the air it is an ordinary hail; the name describes why it was sent |
| **owed hail** | what a hailed party holds after hearing a hail it did not meet: a hail-back to send when it is free, forgotten when the hailer's patience runs out |
| **patience** | how long a packet may wait in the modem before it is dropped, measured from the moment it was queued. Per packet, never per peer |
| **run** | a hailer's attempt to reach one peer: up to three hails at rising power, ended by an answer or by the third going unanswered |
| **meeting** | one exchange between two parties — answer, budget, trains, goodbye — on the hailing channel in regime 0, on an agile channel under a plan |
| **train** | the run of LoRa frames one side sends inside a meeting; the unit is a frame on the air, not a packet from above |
| **narrow schedule** | under a channel plan: the two slots a hail seeds, close together, over a quarter of a second |
| **wide schedule** | under a channel plan: the slots a goodbye seeds, spread thin over a long horizon, and widening as they go |
| **rendezvous** | a meeting reached through a wide schedule — an appointment kept, with no frame spent arranging it |
| **slot** | one derived appointment: a time, a channel and a sync word, belonging to one side to speak in |
| **horizon** | how long a schedule's slots run for before it is retired |

Schedules, slots and horizons exist only where a regime gives them a
frequency to be on. Regime 0 has none of them: a hail there is answered a
turnaround later, on the channel it was sent on.

**Narrow and wide are named for how closely the slots are packed**, and the rest
follows from why each exists. A narrow schedule answers a hail, and the hailed
party either takes one of its two slots or owes a hail instead — there is
nothing to be gained by offering it a tenth chance to do what it will do on its
own terms anyway. A wide schedule is a standing appointment for traffic that may
never come, so its slots start close and stretch apart, front-loaded because the
likeliest moment for more traffic is straight after the last exchange and less
likely with every second that passes.

Three differences follow from what each schedule can prove rather than from its
shape. A narrow schedule's slots fly at the **hailing configuration**, because
nothing has been agreed yet — the hail was one frame on a shared channel. A wide
schedule's fly at the **budget its seeding meeting confirmed**, because that
meeting is standing evidence that modulation works between these two. And in a
narrow schedule the **hailed party speaks first**, whether or not it holds
traffic, because the hail asked a question and the slot is where it is
answered; in a wide one nobody asked anything, so the **holder of traffic
speaks first**, starting with the side that received the last train, being the
likely replier.

### How a node reports what happened

Two notations, used wherever a level or a channel is stated:

| Form | Means |
|---|---|
| `tx{<power> <rssi> <snr>}` | **this** node transmitted at `power` dBm, and the far end reported reading it at `rssi` dBm / `snr` dB |
| `rx{<power> <rssi> <snr>}` | the **far** node transmitted at `power` dBm, and this one read it at `rssi` / `snr` |
| `<channel>/<bandwidth kHz>/<spreading factor>` | what it flew at |

A triple is always *sent at, read as* — never one end's view given twice — which
is what makes a path loss readable straight off the line. A reading that never
came back is `?`, and that is not the same as a zero: one says nobody reported,
the other is a measurement.

A meeting is one line, opened by whoever spoke first so that the order on the
line is the order on the air:

```
Our hail tx{10 -58 12} 3/500/5: rx{14 -45 10} tx{10 -50 12} - rcvd 2/2, sent 5/5
Their hail rx{14 -45 10} 3/500/5: tx{10 -50 12} rx{14 -45 10} - sent 5/5, rcvd 2/2
```

`hail` says a hail arranged it and the triple after it is that frame;
`rndv` says it was an appointment already held, which cost no frame and has no
levels of its own to report. A side that carried nothing is left off rather than
zeroed — no triple and no count, by the same rule as the `?` above, since `0/0`
reads as a measurement of something. Whatever went wrong is appended to the same
line: a failure is a property of the meeting, not a separate event. A hail that
was answered by a hail-back rather than at its slots is logged as two lines,
because it was two hails.
## 1. The idea

One robust shared channel that every node camps on, carrying Reticulum
announces, path discovery and anything broadcast — and, per pair, a derived
schedule of private meeting places where unicast traffic actually flows. The
schedule is a pure function of a frame both ends already hold, so arranging a
meeting transmits nothing, and every meeting's goodbye seeds the next.

**A hail is a question, and the party it names answers it.** The hailer
declares traffic and then listens; the hailed party speaks at a slot if it can,
and hails back when it is free if it cannot. Nothing on a traffic channel is
ever transmitted toward a node that has not just been heard, and a busy node
is reached at a moment of its own choosing rather than at a moment somebody
else guessed.

**No node identity appears on the air except where a node chooses to publish
it.** Meetings are seeded on a 3-byte prefix of a Reticulum address and nothing
else. The one place a node names itself is its own periodic announcement, which
exists to serve the capability table — and, under `sender_ident`, in its hail,
which is what makes the return leg and the hail-back possible (§4).

**SUPE is a frame ferry, not a packet protocol.** Its whole Reticulum-awareness
is one act: reading the first address field of a queued packet to make a tag.
Everything it carries is opaque LoRa frames — counted as frames, checksummed as
frames, repaired as frames. A Reticulum packet split across two frames is two
frames here, and a repair round will happily resend one half; packets come into
existence a layer above, where the interface's ordinary framing reassembles
them. That is what keeps the protocol generic enough to carry anything the
interface frames.

## 2. What this rests on

Four properties of Reticulum, each verifiable against its reference
implementation. If any of them ceases to hold, SUPE breaks.

**The first address field is always what the next receiver holds.** For a
packet in transport it is the next hop's transport identity; at one hop it is
the destination hash; on a link it is the link identifier; for an implicit
delivery proof it is the truncated hash of the packet being proved, which the
sender holds in its receipt and every relay holds in its reverse table. The
only traffic whose first address nobody holds in advance is announces and path
requests — both broadcast, both of which stay on the main channel.

**The packet hash is invariant under transport rewriting.** `get_hashable_part`
in `Packet.cpp` masks off header type, context flag and propagation type, skips
the transport identity field, and excludes the hop byte. Both endpoints and
every relay therefore compute the same value from differently framed copies.
This is what lets both sides of a new link derive the same link identifier with
no signalling, and what makes proof tags match without coordination.

**You cannot address a neighbour you have not heard.** A specific next hop only
exists because a path exists, and a path is only ever built from an announce
that neighbour physically transmitted on this interface — the destination
itself at one hop, the relaying node otherwise. So whenever SUPE applies at
all, the address is known and a signal measurement for that neighbour already
exists. Where no next hop is known, Reticulum broadcasts blind and there is
nothing for SUPE to arrange.

**An identity hash is derivable and already shared, and a node has several.**
Every Reticulum announce carries the announcing identity's public key, so any
receiver can compute that identity's hash without asking, and every
destination announced under that identity resolves to it. But a node is not
one identity. A transport node has a transport identity of its own, which is
the address all relayed traffic is sent to and which Reticulum never announces
as such; each application on the node hangs its destinations off an identity
of its own — a messaging identity, a file-transfer identity — and nothing in
any Reticulum frame says that two identities share a radio. SUPE needs exactly
that fact, because the radio is what it measures and meets: one capability
entry, one path loss, one power offset per *node*, whichever of its identities
a given packet happens to name. ANNOUNCE (§9) is where the node states it —
every identity hash it holds, in one frame — and it is why ANNOUNCE publishes
identity hashes rather than destination hashes: a receiver already turns
destinations into identities from the announces it has, and what it cannot
learn anywhere else is which identities are one node.

## 3. Frames, regimes and versions

**Frames a stranger may have to judge carry a regime nibble and a version
nibble** in one byte — the two main-channel frames, per §0.3. The pair fixes
how every byte after it is read, so block lengths never need announcing. Each
version of each regime carries an expiry date, fixed when the software is
built. Past that date a node neither sends nor accepts frames naming it and
falls back to plain main-channel operation, so an obsolete dialect leaves the
air by itself instead of having to be spoken forever.

| Regime | Version | Meaning |
|---|---|---|
| 0 | 0 | Single Channel — one frequency, one bandwidth, the spreading factor the only thing that moves |
| 1 | 0 | ETSI EN 300 220, 863–870 MHz |

§14 holds both in full: channels, ladder, sync words, timings, ceilings and
limits.

**At this stage of development the version of every regime stays at zero and
everything here is expected to change.** The expiry is what makes that safe, so
it does the work a version number would otherwise do: it is a CALENDAR DATE,
stated in the build and moved by hand as the dialect is revised. A date rather
than an offset from each build because what matters is that every node on a
channel stops speaking the same dialect at the same moment — with an offset,
two nodes flashed a week apart expire a week apart, and the older one spends
that week talking to nobody while looking like a radio fault. Past the date a
node stops speaking SUPE altogether, which is the intended outcome: far better
than speaking a stale dialect at a network that has moved on. There are no
migrations and no compatibility shims anywhere in this protocol — the wire
changes outright, and the expiry retires what came before.

**Every node supports regime 0, and there is no reason not to.** It needs no
band plan, no channel raster and no regulatory basis of its own, and even its
slowest useful meeting beats sending the same frames on the shared channel. In
practice every node on a channel either runs the same regime or does not speak
SUPE at all.

Regime 0 does not move frequency at all, and does not change bandwidth: its
schedule's channel is always channel 0, and what separates a meeting from the
shared channel's traffic is the sync word alone (§14.5). Its whole ladder is
the spreading factors above the hailing one, at the hailing bandwidth.

**Regime 0's number is fixed; do not renumber it.** A frequency-agility setting
that already exists on an interface will often document 0 as "no agile
channels" rather than as a regime. The two readings agree in the only way that
matters: regime 0 has no channel plan, so resolving its number to no agile
channels is correct whichever way it is read, and the setting can name the
regime unchanged.

**The ladder is measured from the hailing configuration, not written in
absolutes.** Budget 1 is one place faster than whatever this network hails at,
budget 2 is two, and so on; budget 0 is the hailing configuration itself. A
network on SF9/BW125 gets SF8, SF7, SF6 where one on SF7/BW125 gets SF6, SF5 —
the same protocol, the same indices, no configuration anywhere.

Three things make that the right frame of reference:

- **The baseline is shared without being sent.** Two nodes with a schedule
  between them are, by definition, hearing each other, so they already agree on
  what budget 0 means. Nothing has to carry it.
- **It is the floor that matters.** A node we cannot reach at the hailing
  configuration cannot be reached anywhere else either — every place up the
  ladder trades margin for rate. So the interesting question is never "what
  modulation" but "how much margin do we have above the one that already
  works", which is exactly what the measurements in §11 produce.
- **The ladder is monotonic** in both directions (§14.3): every entry is faster
  and needs more margin than the one below, and no entry is dominated. That is
  what makes an index meaningful rather than a lookup.

**A budget index means nothing without the channel beside it.** How many
entries the ladder has and what each resolves to depends on the bandwidth the
channel permits — nine entries where 500 kHz is allowed, six where 250 kHz is
the ceiling. There is no ambiguity here: the channel is the slot's, derived
identically by both ends (§7), and the budget bytes of GIMME and HAVE are
resolved against it by both.

What a budget resolves to in absolute terms is still what governs the radio, so
the header mode and the family limits follow the *resulting* configuration
rather than the index. An entry landing on SF5 is unavailable on SX127x; the
same index on a slower network lands somewhere else entirely.

**The type byte is 3 bits of protocol and 5 bits of type.** The protocol bits
are `110`, so every SUPE frame begins `0xC0`–`0xDF` (§0.3). That range cannot
collide with Reticulum itself: SUPE requires an interface with no access code
(§13.1), and without one the daemon's flag byte never has its top bit set — the
access-code path is the only thing that sets it, and it sets it
unconditionally. So anything from `0x80` up is not Reticulum on a SUPE
interface.

**What the range must also dodge is the interface's own framing**, which sits
ahead of the Reticulum packet and is therefore what a receiver actually reads
first. That is a property of the interface rather than of Reticulum, and it is
why §0.3 concedes the four values ending in 0 or 1: restricting what *we*
transmit would do nothing about frames from a node that predates SUPE, whereas
choosing type values its framing cannot produce works against every vintage
without anyone agreeing to anything.

**A frame whose length is not one the regime, version and type allow is
discarded.** The permitted set is tiny — one length for most frames, two for
HAIL and for HAVE, count-derived for the rest — so the test rejects
outright rather than merely suspecting, and it is the last cheap filter before
we act on anything.

**Everything an index selects is a constant of the regime.** An index on the
wire means nothing except against a table both ends hold identically, so every
such table is compiled in and keyed by regime and version — and nothing it
contains may be a setting, because two neighbours who configured differently
would meet at different frequencies, budgets or sync words and never hear each
other. The regime owns:

- the channel raster the schedules draw from, and the maximum bandwidth each
  channel permits
- the modulation ladder and the rules that order it, along with each entry's
  coding rate, preamble length, header mode and low-data-rate optimisation
- the sync-word list a schedule draws from (§14.5)
- the schedule constants of §7 — spacings, jitters, horizons, windows
- frame layouts and their lengths, which is what makes the length test possible
- power ceilings, duty and dwell limits per band, and the train and transaction
  ceilings
- the turnaround, retune, patience and interval constants of §14.7, from which
  every deadline follows
- the expiry date of the regime itself

The regime setting picks which of these tables is in force. It is the only
radio choice SUPE exposes, and deliberately so: everything else about a meeting
follows from a number both sides already agree on.

**The hailing channel is the exception, and stays interface configuration.**
Its frequency, bandwidth, spreading factor, coding rate, preamble, transmit
power and sync word belong to the interface being joined, because that channel
is not ours — it is the Reticulum network itself, shared with neighbours that
have never heard of SUPE. A regime that dictated it would be dictating other
people's network. SUPE reads that configuration and never writes it.

**No SUPE frame carries a cyclic redundancy check.** All it buys is the radio
rejecting a corrupt frame instead of our own parse rejecting it a moment later,
and nothing downstream ever sees these frames, so the check has no consumer.
What it costs is a symbol group: its 16 bits push a frame into the next group
four times in seven, 5 ms each time on a network hailing at SF7/BW125.

This applies to SUPE's own frames and to nothing else. The LoRa frames a train
carries are transmitted in the interface's ordinary framing, checksum included
— they are destined for the daemon, so the radio rejecting a corrupt one saves
work that would otherwise be wasted upstream. That is the same reasoning
reaching the opposite conclusion, because the consumer is different.

**Payload size on the main channel is quantised, and every frame is sized to a
group boundary.** LoRa bills payload in groups of symbols: every length inside
a group costs the same, and the byte that crosses into the next one costs the
whole group. With the check off, the payload term at a hailing spreading factor
of `SF` is `ceil((8·PL − 4·SF + 28) / 4·(SF − 2·DE)) · 5 + 8` symbols, where
`DE` is 1 when the low-data-rate optimisation is in force (§14.3) and 0
otherwise. So the groups are `(SF − 2·DE)/2` bytes wide, and the largest
payload inside the *k*-th is

    ( k·(SF − 2·DE) + SF − 7 ) / 2

rounded down. At SF7 with the optimisation off that gives:

| bytes | symbols | at SF7/BW125, preamble 8 |
|---|---|---|
| 1–3 | 13 | 26 ms |
| 4–7 | 18 | 31 ms |
| 8–10 | 23 | 36 ms |
| 11–14 | 28 | 41 ms |

- HAIL at ten bytes fills the third group exactly, and its thirteen-byte form
  under `sender_ident` fills the fourth — which is why the hail describes the
  train it announces rather than leaving that to a frame of its own: the
  three bytes ride into space the group boundary was giving away, and a
  separate frame would cost a preamble and a turnaround.
- ANNOUNCE is the one main-channel frame whose length is not fixed, and
  the one not sized to a boundary: its `5 + 4·count` lands where the identity
  count puts it. One frame per interval per node is what that costs, which is
  why the interval is long and the count is every identity a node holds rather
  than one frame each.

Those figures assume preamble 8; the interface default of 12 adds about 4 ms to
each. Against roughly 760 ms for a full 500-byte Reticulum packet on the same
channel, the shared-channel cost of a hail is about one twentieth of a single
packet — and under a channel plan a reseeded schedule costs nothing there at
all.

**Configurations and channels are indices**, never literal frequency and
modulation values — a byte that named them outright would not fit the quantum
on the network where the quantum is tightest.

## 4. Options

The protocol admits exactly five deployment choices, named below in the
protocol's own terms; how an implementation exposes them is its own business.
Everything else two nodes must agree on is a regime constant (§3), because both
ends must hold it identically — no channel list, no ladder, no sync words, no
schedule constants, timings or limits are choosable anywhere. The hailing
channel's parameters — frequency, bandwidth, spreading factor, coding rate,
preamble, transmit power and sync word — belong to the interface being joined
(§3): SUPE reads them and never sets them.

Two options carry the on/off decision between them, and the division is
deliberate. `enable` is the gate: with it off the node's on-air behaviour is
exactly that of a node that has never heard of SUPE, so there is one thing to
turn off when comparing. The regime number names a table and never means "off"
— regime 0 is a working regime, not an absence of one. Where an interface
already carries a frequency-agility selection, that selection *is* the regime —
the two are the same quantity under different names: the channels, and what is
permissible on them — and a value the software does not recognise resolves to
no agile channels, the safe reading of a number it cannot understand.

| Option | Default | Meaning |
|---|---|---|
| `enable` | off | Speak SUPE on this interface at all. Off means a plain Reticulum LoRa interface. |
| `regime` | 0 | Which compiled-in regime is in force (§14) — channels, ladder, sync words, schedule constants, timings, ceilings, limits, expiry. The only radio choice SUPE exposes. Frames naming an expired regime are ignored. |
| `adaptive_txpower` | on | Transmit to each neighbour at a power measured for it, per §15. Off means every frame goes out at the configured power. |
| `announce_interval` | 30 min | The gap between a node's own SUPE announcements. It governs nothing else; Reticulum's announces are not SUPE's to schedule (§10). |
| `sender_ident` | on | Name ourselves in every hail. Costs three bytes and one symbol group, and gives up the protocol's default anonymity: a listener learns who is talking to whom, which no Reticulum header discloses. It buys three things, and none of them is optional in practice. **The hail-back (§12) cannot exist without it** — a hailed party that cannot meet us has nobody to hail. **The return leg (§8) cannot exist without it** — the tag a hail carries is the *other side's* address and says nothing about who is asking, so an unnamed hailer's queued traffic at the far end is indistinguishable from a stranger's and the hailed party can neither open with HAVE nor answer with one. And it lets the far end file what our cargo establishes — a link identifier above all — against us rather than against nobody (§11). Off restores the anonymity and gives all three up: an anonymous hail is answered at its two slots or not at all. A node that turns it off still parses and honours the ten-byte frame from those that do not. |

A sixth choice — dedicating a radio to attending meetings on another radio's
behalf — is an implementation arrangement rather than a protocol option:
nothing on the wire changes with it, and §18 carries what it asks of an
implementation.

## 5. Addresses that mean us

The modem keeps one flat set, learned by watching the traffic it carries for
the Reticulum daemon. No secrets, no cooperation from the daemon.

| Entry | Learned from | Retired by |
|---|---|---|
| our destination hashes | an announce we transmit at hop count zero | effectively never; refreshed by re-announce |
| our transport identity | an announce we relay — its first address field is ours | effectively never |
| our identity hashes | the identities we announce in ANNOUNCE (§9): a hail-back is tagged with the identity our own hail named, and nothing else in the traffic ever carries it | never |
| link identifiers we terminate | anything we transmit at hop count zero addressed to a link | an observed link close; otherwise keep-alive staleness |
| link identifiers we relay for | forwarding a link request, and forwarding any frame addressed to a link — the return direction of a relayed link arrives addressed to the link identifier, not to our transport identity, so a relay that skips this sleeps through it and the link dies. The request is the earliest evidence there is, and it is derivable at every hop: the identifier is a hash of the request with its hop count and transport field excluded | the same |
| pending proofs | the truncated hash of each packet we send or relay that may attract a delivery proof | the proof arriving; otherwise the receipt or reverse-table timeout |

Store the 3-byte prefix, an expiry, and a small reference count — 24 bits with
a thousand live entries collides internally often enough that a link close must
not unlist an unrelated pending proof. Roughly eight bytes an entry.

The full comparison is not the modem's job. It hops, receives, and hands the
frame up; the daemon does the authoritative match. A false positive therefore
costs one receive window and nothing else, which is what licenses the whole
scheme's imprecision.

Which traffic actually produces pending proof hashes is narrow: single data
packets to a destination that proves deliveries — in practice opportunistic
Lightweight Extensible Message Format delivery — and relayed copies of those.
Link traffic never does, because proofs over a link are addressed to the link
identifier and the link entry already covers them.

### 5.1 Addresses that mean someone else

The mirror of the table above, and what budget selection actually runs on:
given a tag about to be sent to, which node is behind it, and what is known
about reaching that node. Three joins build it, all from traffic the modem is
already carrying:

| Tag | Resolves to its node by |
|---|---|
| a transport identity | the announcement (§9) that listed it. Nothing else can: it is never announced by Reticulum, and a relayed announce's claim to it is a claim about who transmitted, which no signature covers |
| a destination hash | the Reticulum announce that carried that destination's public key, whose truncated hash is the identity (§2) |
| a link identifier | the node the link request was handed to, recorded as the request goes past: the destination where it was dialled directly, and the relay named in the request's first address field where it was dialled through one. Never the far end of a path — every frame of a session goes to the first hop, and a link to a destination behind a gateway is a link to the gateway as far as the air is concerned |
| an identity hash | the announcement that listed it — the one tag a hail-back carries, and the one a hailer must be able to resolve to the node that owes it nothing more than an answer |

Capabilities and measurements hang off the node — the set of identities one
ANNOUNCE listed together — never off the tag or off a single identity, so a
node with three identities and forty destinations costs one capability entry
and forty-three pointers.
This is the table that answers "what budget" — §11 is what fills it. It is also
what a hailed party consults before it speaks: whether the identity a hail
named has traffic queued behind it decides whether the first frame at the slot
is a GIMME or a HAVE (§8).

## 6. The main channel: the tag and HAIL

**Tag** — 3 bytes, always the first three bytes of an address the peer holds,
and nothing more. For a hail with a packet behind it that is the packet's first
address field; for a hail-back it is the identity the hail being answered
named. It has one meaning: an address the peer holds. There is no type, no flag
and no second interpretation, because the receiver has one flat table (§5), a
hit is a hit, and what follows — derive, attend, speak, and let the daemon
judge — never depends on why the entry is there.

That includes delivery proofs, which are addressed to the truncated hash of the
packet being proved. Exactly two nodes hold that hash: the origin, in its
receipt, and the one relay that forwarded the packet, in its reverse table. A
packet in transport names its next hop, so no second relay ever handled it and
no third node ever wakes. The tag is as precise here as anywhere else.

---

**HAIL** — hailer to hailed party, overheard by all
*"I have this many frames for whoever holds this address, and this is the
fastest I propose to send them. Answer me; if you cannot, hail me when you
can."*

Nine bytes, or twelve where it names itself, and the only frame this protocol
sends on the shared channel per pair of nodes — the announcement aside. It is
a **question, not a commitment**: nothing has been decided and nothing is
reserved. It says four things, and every hail says all four:

- **Traffic is waiting** for whoever holds the tag.
- **This is the train**: its count, its length, and the fastest budget the
  hailer proposes for it — so that the answer can confirm the terms without a
  frame of its own, and so that a listener knows what it is agreeing to hold.
- **Answer me**: a turnaround from now on this channel in regime 0, or at
  the two slots this frame's own bytes derive under a channel plan (§7).
- **Hail me back** if you cannot — when you are free, and while my patience
  lasts (§12).

The fourth is what makes the rest safe to keep small. A hailed party that
heard the hail but cannot answer — its own frame was going out, it is mid-frame
on the hailing channel when a slot comes, it is at another meeting's tail, both
slot channels are busy where it stands — is not lost to the hailer; it comes to
the hailer at a moment it chose, with one short frame, and the meeting that
follows is opened by the hailer's own traffic. The answer serves the common
case, where the hailed party is idle and responds at once; the hail-back serves
every other, and the two together are why a schedule has no third slot.

**It carries the train's description and nothing else about the meeting** — no
family, no measurement, no verdict. Those belonged on the shared channel only
while the answer had to come back there. The answer is the receiver's, and the
budget conversation happens in it, where the measurement that should drive it
— how this very frame was heard — has just been taken. The capability table
(§5.1) supplies family and ceiling for the ladder; a peer that never announced
is not a SUPE peer at all (§13.1).

**It states its power**, unlike everything else about it, because that one byte
turns every listener's reading into a path loss immediately — the seed is also
a measurement, at the hailing configuration, of exactly the link the meeting is
about to use.

**Our own identity, only if `sender_ident` says so.** Without it a hail names
the peer and no one else, which is what keeps the protocol anonymous — and what
makes both the return leg and the hail-back impossible, since the far end can
neither recognise its queued traffic as ours nor find us to hail (§4). It is a
deployment choice, and a gateway should make it.

**A hail-back is a hail.** Nothing on the wire marks it, and nothing needs to:
the node it names holds traffic for the node that sent it, and the meeting
opens the ordinary way, with the hailed party — the original hailer —
answering first, carrying its traffic (§8). A hail-back and a hail sent because
the sender has traffic of its own are indistinguishable, and are handled
identically. The word names a reason, not a frame.

There is no discovery handshake and no cold start, because there is nothing to
discover. A hail is only ever sent toward a peer whose ANNOUNCE has been
heard, which is what makes it a peer at all in SUPE's terms. Its address
and a signal measurement come from the Reticulum announce that built the path
(§2).

**Third parties see nothing, are owed nothing, and lose nothing.** No hold, no
reservation, no duration on the air. A node away at a meeting is briefly deaf,
as any busy node may be; whoever hails it meanwhile spends a short frame and
learns nothing false — nothing expensive is ever transmitted toward a party
that has not spoken first, and a missed slot from a peer still being heard
teaches the power controller nothing (§15). Who hears whom is already chaotic
on any real deployment; the layers above digest illegible silence constantly,
and every exchange a meeting carries is one that never contended on the shared
channel — which is more relief than any reservation hint ever bought.

**What it costs and what it buys.** At SF7/BW125 a hail costs 36 ms of shared
channel, 41 where it names itself. In regime 0 that and the answer are the
whole overhead of a batch, paid once per batch against one carrier-sense
contest and one blind-send risk per packet. Under a channel plan it is paid
once, and every meeting it or its successors carry costs the shared channel
nothing: a pair with steady traffic converges on announces as its entire
shared-channel footprint, and any arithmetic that weighs per-packet overhead
against per-packet savings collapses — the overhead is not per packet, not per
meeting, but per *conversation*. Where the hailed party was busy, a hail-back
costs one more.
## 7. The schedule

A schedule is a pure function of one frame both ends hold — the **seed** — and
of the roles the exchange around that frame fixed. Nothing about it is
transmitted. This section is normative; its constants are regime constants
(§3), and the values below are stated values awaiting
[`simulation.md`](simulation.md), like §14.7's.

**Only a regime with a channel plan derives a schedule.** An appointment is
worth keeping on a frequency nobody else is camped on; on the hailing channel
the moment it names is subject to everyone, and a hail there is answered in
place instead (§8). Regime 0 reads none of what follows, and carries the salt
only so that the frame has one layout.

**The seed and its stream.** The seed is the seeding frame's bytes exactly as
transmitted, and every seed is unique: HAIL carries a random salt for
exactly this (§0.3), and a THATSIT carries one for the same reason — its
checksums alone vary too little across the one-frame meetings that are most
of them.
Uniqueness is load-bearing — the stream below is a pure function of the seed,
so a repeated seed is a repeated schedule, the same channels in the same order,
and both the frequency diversity and the stale-versus-fresh hash distinction
rest on it never happening. Both ends compute the same digests and read the
same byte stream from them; everything position-, frequency- and word-shaped
about the schedule is that stream read in order. In full, with every quantity
an integer and every time in milliseconds:

```
D_0    = SHA-256( seed )
D_i    = SHA-256( seed ‖ i )          i = 1, 2, …  — one byte, as needed

hash   = D_0[0..2]                    the 3 bytes GIMME and HAVE quote

stream = D_0[3..31] ‖ D_1[0..31] ‖ D_2[0..31] ‖ …
         slot k consumes stream[3k], stream[3k+1], stream[3k+2]
         as j_k, c_k, s_k

t_0    = seed_gap                                       (narrow, §14.7)
       = 150 + (j_0 mod 40)                             (wide)
t_k    = t_(k-1) + 100 + (j_k mod 24)                   (narrow)
       = t_(k-1) + min(60 + 30·k, 350) + (j_k mod 40)   (wide)
         …measured from the epoch; slots exist while t_k ≤ horizon
         (230 narrow, 3000 wide)

chan_k = 1 + (c_k mod nChans)         regime 1; always channel 0 in regime 0
         narrow, k = 1: if chan_1 = chan_0, chan_1 = 1 + ((c_1 + 1) mod nChans)
sync_k = W_sf[ s_k mod N_sf ]         §14.5's word list for the slot's
                                      spreading factor, ordered ascending

speaker = fixed by ROLE (below), never by the stream
```

The narrow schedule's constants yield exactly two slots, at 100 ms and at
200–223 ms, and the second is never on the first's channel where the regime
has a second channel to give. The wide schedule's spacing yields thirteen to
fifteen slots inside its horizon. A conformance vector file over seeds belongs
beside the ladder's (§14.3.4) once these constants settle (§16).

**Two seeds exist and they differ in parameters and in who speaks:**

| | narrow schedule | wide schedule |
|---|---|---|
| seeded by | a hail on the main channel | a meeting's final THATSIT |
| epoch | the end of the seeding frame, as each radio timed that RF event | the same |
| first slot | one seed gap after the epoch | 150 ms + jitter after the epoch |
| slots | two, the second on another channel | as many as the horizon holds |
| horizon | 230 ms | 3 s |
| who speaks | the hailed party, at both slots, whether or not it holds traffic — the hail asked it a question | the holder of traffic, in its own slots; slots alternate, and the side that received the last train has the first |
| who listens | the hailer, at both | the other side, at each slot it chooses to attend |
| slot modulation | the hailing configuration | the budget the seeding meeting confirmed |
| expiry, unmet | the hailer waits to be hailed back (§12) | no evidence of anything |

**The hailed party speaks first because it is the one whose presence is in
question.** The hailer has just transmitted the seed, so it knows the schedule
before the seed has finished flying and is on the slot's channel with its
receiver open before there is anything to hear: it is never late. The hailed
party is the one that must demodulate the seed, derive the schedule, retune,
sense and speak — and a slow *speaker* is the safe direction for that to be,
because the listener is already there. The opposite arrangement puts the slow
party in the listening role, where opening late means missing the preamble and
hearing nothing at all however strong the signal, and it puts the hailer's
first traffic-channel frame into the air toward a node that has given no sign
of being there. Under this rule nothing on a traffic channel is transmitted
blind: the hailed party speaks because it was asked, and the hailer speaks
because it was answered.

**The wide schedule cannot do this, and does not try.** Nobody asked anything
at a goodbye, so neither side owes the other a frame, and the only way for a
non-holder to speak first would be for both sides to beacon at every slot,
which spends more than the blind HAVE it would save. So on a wide schedule
the holder of traffic speaks first, in its own slots, and a slot at which it
holds nothing is a slot at which nothing happens. The cost is bounded: one
short frame per owned slot, sensed first, on a private channel.

**Why the seed is the final THATSIT and not the BYE.** The goodbye must be a
frame both ends can *prove* the other holds, or the two derive different
schedules and silently never meet. The BYE that answers a THATSIT proves the
answering side held it; and a BYE that is lost still leaves both ends holding
the same THATSIT — the sender merely does not know the transfer concluded,
which the next meeting resolves. Seeding from the BYE itself would put the
schedule on the one frame whose loss is invisible.

**And it must be the meeting's LAST THATSIT, not merely one you hold.** An
answering HAVE promises a return train, and therefore a later THATSIT that
will close the meeting — so the earlier one stops being the goodbye the moment
that promise arrives. A side that never receives the promised one is holding a
superseded frame, and seeding from it derives a schedule the peer never
derives: the same orphan as seeding from an unproven one, reached from the
other end. Both losses are one dropped frame, and both cost a whole horizon.

**Holding the goodbye is not proof; only these two things are.** A side may
seed from a THATSIT when it *received* it — the peer sent it, so the peer holds
it — or when something of the peer's *answered* it: a BYE, a RESEND, or an
answering HAVE. A THATSIT sent into silence proves nothing, and seeding on
one is worse than seeding nothing: the sender derives a schedule its peer has
never heard of, then spends the entire horizon transmitting at slots nobody is
attending before it falls back to the shared channel. One lost frame becomes a
dead conversation that way — the sender concludes the peer is absent while the
peer sits idle and reachable. **Unproven, both ends agree on nothing**, which
is the recoverable state: the next traffic seeds a hail at once, and the
asymmetry never exists.

**The derivation's two anchors are outside the stream, deliberately:**

- **The epoch is a shared RF event, not a clock reading** — the end of the
  seeding frame, timed independently by each radio to sub-symbol accuracy. So
  clock error accumulates only across the silence since it: under 1 ms even on
  an RC oscillator across the full 3 s horizon, which the slot window's guard
  absorbs.
- **The speaker is fixed by role and never by the stream.** If it could derive
  from a disagreed seed, both parties would transmit at each other and nothing
  would detect it. A disagreed seed under a fixed role just means empty slots,
  which the horizon already handles.

**A frame arriving outranks every slot.** Attending one retunes the radio, and
a retune part-way through a preamble destroys the reception in progress — so a
node holding schedules would abandon real frames on the channel it is camped on
for slots that may well be empty. The frame in the air is certain and the slot
is speculative; the slot's own lateness tolerance absorbs the few milliseconds
of deferral, a missed slot costs at most a hail-back (§12), and a missed hail
means the peer cannot reach this node at all. That asymmetry is the whole
argument, and it matters most exactly when it is least convenient: a node
whose schedules have gone out of step with its peer's is both attending the
most slots and the one the peer is trying hardest to reach on the shared
channel.

What defers is the act of attending, and nothing else. A slot declined this way
is spent, not postponed: by the time the radio comes free its moment has gone,
and it is walked past like any other. Retiring a schedule at its horizon and
walking past slots already gone are bookkeeping that touches no radio, and they
must keep running for as long as the radio is held — a node that suspends them
instead has stopped the clock on every schedule it holds, so none of them can
ever expire, and the appointment it is already late for stays permanently the
next thing it means to do.

What may defer a slot is bounded by how long it lasts, not by whether the radio
is nominally spoken for. A locked preamble and a half-read header end on their
own within a frame time, and a slot that yields to one loses nothing it will not
get back within the same schedule. A wait measured in seconds is a different
thing wearing the same name: every slot inside it is walked past, so a node that
defers on that clock is not deferring at all — it has stopped attending its
schedules for the whole window, and gone deaf to the peer hailing it. The
asymmetry above settles which way that trade runs. A missed slot costs a
hail-back; a missed hail means the peer cannot reach this node at all, and no
partly-held frame is worth buying at that price.

The only state that stands the engine down rather than deferring within it is a
transmission of this node's own still going out, because that is the one case
where servicing a due slot would retune the chip out from beneath a frame
already on the air.

**A hail outranks a rendezvous.** When two schedules want the same moment, the
one seeded by a hail is served first. It was bought a moment ago with a frame on
the shared channel by a party that has traffic in hand and said so, and it lasts
a quarter of a second; a rendezvous is a standing appointment left by a meeting
already finished, lasting seconds, and quite possibly empty at both ends.
Served in an arbitrary order the rendezvous takes half of the contested
moments, and each of its retunes lands the node late for the appointment that
had something behind it — so a peer hails, is not answered at its slots, and is
made to wait for a hail-back while the node keeps an empty engagement.

**An orphaned wide schedule is abandoned, not spent.** The last frame of a
goodbye cannot itself be acknowledged, so the two ends can never be made to
agree about it in every case: whichever way the rule is written, some single
lost frame leaves one side holding a schedule the other never derived. That is
not a defect to be designed away but a property of the exchange, and the answer
is to make it cheap rather than impossible. A schedule that has spoken in its
own slots and been answered in none of them is being attended by nobody — a
data announcement is a question the far end owes an answer to, not a broadcast
— and after a couple of those the schedule is dropped and the traffic hails
instead. A whole horizon spent speaking into silence buys nothing that a hail
would not have bought at the start.

**A partial frame outlives its carrier by nothing.** A meeting hands over
everything it collected in one delivery, once. A fragment still waiting for its
partner after that delivery is waiting for something the meeting already failed
to bring — the repair round is over and the frame that would complete it is not
coming. It is discarded there, not left to a reassembly timeout, because a
fragment held past the point where it can complete is state every later decision
has to reason around for no possible gain.

**One narrow schedule per peer, and a crossed hail is a hail the peer did
not hear.** Two hails between the same pair can cross, and under the
hail-back they routinely do: the hailed party hails back the moment it is
free, the hailer hails again after an interval. A node that heard our hail
is waiting at our slots, not hailing — a live schedule with a peer holds its
traffic back from the shared channel — so a hail from the peer arriving while
ours is live says the peer did not hear ours. Ours is dropped for theirs on
the spot, unmet and unscored, and we speak at theirs with whatever we hold,
which is where the peer, holding only its own, is listening; the meeting that
follows carries both directions, since the hailed party opens with what it
has (§8). Two half-duplex radios cannot both have heard each other's hail
without one being deaf to the first, so there is no symmetric case to settle
and no tiebreak to derive. Two live schedules with one peer would interleave
— each retune arrives late for the other — which is the other reason not to
keep both. A hail retried by the same hailer likewise retires its
predecessor, at both ends: the hailed party that could not meet the first
will not meet the second any sooner, and holds one owed hail regardless of
how many it has heard; and a node that owes a hail to a peer whose fresh
schedule it holds speaks there instead of hailing. A goodbye's wide schedule
is untouched by any of this — a rendezvous from a closed meeting is
legitimately live beside a fresh hail.

**Attending a slot.** The listening side retunes, opens its receiver over
`[offset − slot_guard, offset + slot_guard + preamble]` with the timeout
stopped by a detected preamble, and goes home on silence — a few tens of
milliseconds per slot. **The window covers the software's slop, not the
preamble's width** (§14.7): both sides reach a slot from an idle task and open
it late, so the listener aims early enough that its own lateness still has it
listening before the speaker transmits, and stays until the speaker's own
tolerance has run out. Sizing the window to the preamble instead is the one
mistake that hides: it looks generous at a hailing configuration and shuts
before the speaker arrives at a fast one, which is precisely the modulation a
wide schedule flies at. On a narrow schedule the listener is the hailer, and
it is not reached from an idle task at all: it has just transmitted the seed,
holds the traffic, and has nothing better to do with its radio for a quarter of
a second — it may open earlier than the window asks and should, since being on
the channel early costs it nothing and covers a speaker that is early rather
than late.

The speaking side gives its slot up once it is `slot_lateness` past the moment,
senses the carrier first — the appointment grants the peer's attention, never
the spectrum; a busy channel means this slot is skipped, which costs and means
nothing — and opens. On a narrow schedule it opens whether or not it holds
traffic, with a GIMME or a HAVE (§8); on a wide schedule it opens only when
it holds traffic, with a HAVE, and a slot at which it holds nothing is
simply not attended.

**That sense is against the regime's absolute threshold (§14.2), never against
a tracked noise floor**, and the distinction is not a refinement. A floor is
learned by sensing: the hailing channel is sensed continuously and converges,
while a slot's channel is visited for a single sample seconds apart and never
leaves whatever it was seeded with. Every such tracker is asymmetric — a seed
above the real floor is corrected by the next sample, a seed below it is walked
off a few percent at a time — so a seed below the real floor makes every slot on
every fresh channel read busy, permanently, and no schedule can ever speak. The
failure is silent and total, and it looks exactly like a crowded band. The
demodulator still outranks the threshold: a preamble the receiver has locked
means the channel is occupied whatever the power reads, since LoRa decodes below
the noise.

Attendance is optional on both sides at every slot, and on a narrow schedule
declining has a consequence the wide schedule's declining does not: a hailed
party that takes neither slot owes a hail (§12). A busy node thins its
attendance and loses nothing but latency; a node with earlier commitments — its
own hailing receive above all — ranks them above any slot.

**Contact consumes the schedule.** The first met slot voids every later one:
the meeting that follows runs to its own conclusion however long that takes,
and its goodbye seeds afresh. The schedule exists to make contact, never to
carry it.

**The wide schedule is the reply's ride.** The measured shape of interactive
traffic is a reply born 100–400 ms after a train concludes — a proof the
daemon must decrypt, verify and sign before the modem sees it. The wide
schedule's early slots sit exactly there, owned by exactly the party that will
hold that reply, at the modulation that just worked. This is why no meeting
ever waits for a reply that might be coming: the schedule ahead of it is a
cheaper wait than any in-meeting grace, and it costs the waiting party nothing
but a slot dwell if the reply never appears.

**The spacing cap is the fallback arithmetic.** A slot only pays if waiting for
it is competitive with going to the shared channel now — but the shared cost is
unknowable in advance and partly paid by others (§0), so the cap errs long: no
two slots are ever further apart than ~350 ms, and data born mid-schedule waits
at most that for a private attempt before anything else is considered.

**Failure is silence, and silence here is cheap.** Every failure mode — a seed
disagreed, a schedule expired, a slot skipped, a preamble missed — costs a few
frames and dwells, self-clears at the horizon, and leaves both nodes exactly
where a node without SUPE always is: on the shared channel. A narrow schedule
passing unmet is not yet evidence of anything — the hailed party may be about
to hail back — and becomes evidence only when the interval for that passes too
(§12). A wide schedule expiring unmet is an ordinary end of conversation and
teaches nothing.

## 8. The meeting

Everything after the hail happens between the two parties the hail named, and
the first frame of it is the hailed party's. In regime 0 that frame follows the
hail by a turnaround, on the hailing channel, inside the transaction the hail
cleared; under a channel plan it opens a slot of the schedule (§7). Either way
the hailer has already said what it holds — the hail carries its count, its
length and the fastest rate it proposes — so the hailed party's first frame is
an *answer*, and nothing expensive flies until each side has heard the other.

**Who answers, and with what.** The hailed party sends one of two frames, and
the choice is made by one question: *does it hold traffic for the hailer?*

- **It does not — GIMME.** *"I am here; send at this budget, this many frames
  at most."* This is the ordinary answer, which is most hails.
- **It does — HAVE.** GIMME with a train behind it: the same terms, then its
  own count and length. Its own train goes first and the hailer's rides the
  answering turn. Under `sender_ident` the hailed party can tell, since the
  hail named the hailer; and it is every hail-back's meeting, since the node
  hailed back is the node that had traffic in the first place. In a
  hailing-rate dialogue the two trains simply follow one another — the hailed
  party's first, the hailer's a turnaround after its last frame, with nothing
  between them, since each side already holds the other's count and length.

**The answer flies at the configuration the hail arrived at.** That is the
hailing configuration for every radio but one: a hail to a node that listens
at several rates (§14.6, §15) may itself arrive below the hailing spreading
factor, and its sender is listening for the answer where it sent. Budgets
stay indexed from the hailing configuration whatever the hail flew at, so a
GIMME may confirm budget 0 to a hail that arrived at budget 2, and the train
then flies slower than the hail did.

**A side whose count is zero sends no train and no THATSIT.** A hail-back's
hail says so, and so does a GIMME answering traffic that has since expired.
The other side's train follows the frame that confirmed its budget as if the
zero side had opened with GIMME, and where both counts are zero the two go
home after the answer, two short frames having settled that nobody needed
anything.

Either way the answer's hash bytes do double duty. Under a plan the sync word
already filtered other networks and other pairs in silicon (§14.5); the hash
bytes kill the residual 2⁻⁸ and — more importantly — prove both ends are
talking about the same hail. A stale or disagreed seed fails here, in one
frame, instead of producing a meeting where each party believes a different
thing.

**GIMME** — the receiver's terms
*"Heard you; fly at this budget, no more than this many frames. Here is how
your hail reached me."*

The budget is the receiver's choice, never above the ceiling the hail
proposed, never above either node's capability ceiling, resolved against the
channel the meeting is on (§14.3). Where an opening HAVE proposed a ceiling of
its own, the GIMME that confirms it stays below both proposals, since one
budget serves both trains and each side proposed for the one it sends. The receiver chooses because the receiver
is the one about to hold the train: it holds its own noise floor, its own
airtime ledger for this channel, its own memory, and its reading of the hail —
the headroom-above-hailing that the budget choice classically runs on, taken
at the configuration the hail itself flew at.

The count ceiling is the receiver's other promise, and it is a RAM figure: a
train it accepts is a train it must hold to the end (below). The hailer flies
the first `ceiling` frames of the train its hail described and no more; if it
described fewer, it flies those. Its THATSIT's checksum count says what flew.

**In regime 0, GIMME's budget byte selects the dialogue's shape.** Budget 0:
the frames follow — the hailed party's train first where it opened with HAVE,
then the hailer's — at the hailing rate and at their senders' stated powers,
and each receiver hands each frame up as it lands. Nothing else is sent — no THATSIT,
no repair, no goodbye — because at the hailing rate a resend costs exactly what
the daemon's own retry costs, and the closing frames would buy nothing but
airtime on a channel everyone shares. Any budget above 0: both sides retune,
and the full meeting below follows. Under a channel plan the full meeting
follows at every budget, because a resend on a channel nobody else is on costs
no contest with anybody, and is cheaper than the daemon's retry at any rate.

**HAVE, opening** — the hailed party's terms and its own train
*"Heard you; here is what I hold, and the fastest I propose. Your turn comes
after mine."*

Its budget byte is a proposal, since its sender is about to transmit, and the
hailer answers with a GIMME confirming one at or below it — the hailer being
the receiver of this train, with its own memory and its own reading of the
HAVE, at the slot's own modulation. One confirmed budget then serves the whole
meeting, both trains. If the confirmed budget is below the proposed, the train
takes longer than the HAVE's length byte said: the receiver's deadline is that
length scaled by the two entries' ladder keys (§14.3.2), rounded up, and never
more than the regime's train ceiling plus guard — the sender trims its count
to fit that ceiling at the confirmed budget, and its THATSIT says what flew.

**The train** — the sender's frames, back to back at the confirmed budget,
separated by the flip gap of §14.7. These are not SUPE frames: they are LoRa
frames in the interface's ordinary framing, checksum and all. SUPE chose where
and how fast; it does not touch what travels.

Its power is the sender's to resolve fresh, on its reading of the last frame
the receiver sent — the GIMME or HAVE that answered. Against it stands the
confirmed budget's margin cost (§14.3): the train flies at a different
modulation than the frame that was measured, and §15's rule that power resolves
per configuration applies inside a meeting as much as anywhere. A reported
surplus is spent, a reported deficit is covered, and the receiver learns what
was chosen one frame later, from the THATSIT. A train no THATSIT follows flies
at the power its sender last stated.

**THATSIT** — after the train
*"That was the train: it went out at this power, n frames, and these are
their checksums, in order."*

One byte per frame — CRC-8, polynomial 0x07, over the frame's on-air bytes
(§16). The frames themselves carry no sequence numbers and need none: the
checksum list is the sequence, stated once, after the fact, by the side that
knows it. The receiver aligns the frames it holds against the list — an
ordered-subsequence match, greedy from the left — and any position it cannot
account for is missing. A checksum collision that mis-aligns the match
(adjacent equal bytes over a lost frame) resolves conservatively: when in
doubt, ask for more. What survives a wrong belief is the layers above, to whom
a wrongly-believed-received frame is indistinguishable from the ordinary lost
one.

**The answer** — one of three frames, a turnaround after THATSIT:

- **BYE** — every frame accounted for, nothing to say back. The meeting is
  over; both retune home; under a plan the THATSIT just answered is the next
  schedule's seed.
- **RESEND** — a bitmask over the checksum list. **One repair round**: the
  sender resends exactly the frames named, in order, and the receiver answers
  BYE either way — a BYE after an incomplete repair closes the meeting for
  whatever arrived, which costs one byte and spares the sender a silence
  deadline. What a single round cannot save belongs to the layers above, which
  have retry machinery of their own and better context for using it. A repair
  round's frames are the cheapest retransmission this stack can make — no
  contention, no renegotiation, at a rate the link just demonstrated.
- **HAVE, answering** — the receiver has traffic of its own. No GIMME answers
  it — the peer's attention is not in question, and the meeting's budget is
  already confirmed — and the answering form carries what a GIMME would have
  measured (how the train was heard, worst frame), the repair bitmask, all-zero
  when nothing is missing, and its own count and length. The return train
  follows at the meeting's confirmed budget, then its own THATSIT; the first
  sender's resent frames, if any were requested, ride ahead of its closing BYE
  or RESEND.

**Where the hailed party opened with HAVE**, the order is the same with the
sides swapped: its train, its THATSIT, then the hailer's answering HAVE — whose
count and length may restate the hail's or update them, the queue having had
time to grow — the hailer's train, the hailer's THATSIT, and the hailed party's
BYE or RESEND.

**A hail-back's meeting is this meeting with nothing added.** The hailed party
— the node whose traffic prompted the hail-back — answers with HAVE, in either
regime; the node that hailed back carried a count of zero in its hail, having
nothing of its own, and confirms the HAVE with GIMME. If
the traffic is gone by the time the hail-back arrives — delivered another way,
or expired at patience — the answer is a GIMME, the hailer's train is zero
frames long, and the two go home in two short frames.

**Delivery is whole, in sequence, at the close — where there is a close.**
Frames must reach the daemon in the order they were sent — the interface's
split reassembly depends on adjacency — so in a full meeting the receiving side
hands nothing up while the meeting runs: the train is held, the repair round
fills what it can, and the stack goes up in order when the meeting closes,
holes surrendered to the layers above. The consequence is a hard bound: **a
train is a RAM commitment**, and the count ceiling GIMME carries is the
receiver stating that bound before anything flies, sized to its own memory. In
a hailing-rate dialogue there is no repair and so nothing to wait for, and each
frame goes up as it lands, exactly as plain traffic does.

**Ends are arithmetic, not agreement.** Every deadline — the answer after the
hail or at the slot, the train after the answer, THATSIT after the train, the
next answer after THATSIT — follows from the constants of §14.7 and lengths
already exchanged, computable identically by both sides. A deadline missed
means go home; nothing is renegotiated, and the run or the schedule ahead is
the retry.

**What fills a train is a resource transfer**, and it fills it as a batch
rather than a trickle. Reticulum's resources are receiver-driven: the receiver
asks for a window of parts by hash, and the sender's handler transmits every
part it names in one pass, back to back. The window opens small and grows
toward a ceiling the link's measured performance sets, so there is a loop worth
noticing: SUPE raises the measured rate, a wider window makes fuller trains,
and fuller trains raise it again. Resource parts are also the one traffic that
never needs a delivery proof — they are link traffic, so they never enter the
pending-proof set of §5, and only the finished resource is proved, once, at the
end.
## 9. Announcing

**ANNOUNCE** — us to everyone, once per announce interval, on the main
channel at the hailing configuration, in every regime
*"These identities are me, this is what my radio can do, and this frame went
out at that power."*

**It goes out where everyone is already listening.** An announcement has to
reach nodes that know nothing about us yet, and the hailing configuration is
the only one every node is known to be camped on. Moving it to a channel of its
own would save this frame's airtime on the shared channel once per interval per
node, and charge every listener in earshot two retunes and a deafness window to
collect it — which is the worse trade in a neighbourhood of any size, and it is
why there is no announce channel and no frame announcing one.

The power byte is what makes the frame worth hearing: a listener already has
its own reading of it, and the two together give path loss rather than a bare
signal level.

Identity hashes, not destination hashes: a node typically has more destinations
than identities, and any receiver of a Reticulum announce can already derive
the identity hash from the public key it carried (§2). Four bytes rather than
the three used for tags, because a tag names one schedule and tolerates
collisions while this names a node in a table that persists — the extra byte is
worth its airtime here and not there.

**The transport identity is the one that must be in there.** It is a key of its
own, separate from the one a node's destinations hang off, and Reticulum
announces it nowhere: it reaches the air only as the first address field of a
packet in transport, which is the tag of everything relayed towards that node.
This frame is the only thing that says whose it is, and without it a gateway's
neighbours cannot recognise the address they send all their transit traffic to.
A node learns its own from the announces it relays.

**Every identity a node holds rides in one frame.** On the main channel an
unbundled announcement would spend a preamble and a frame per identity where
one frame carries all of them, so unbundling costs strictly more for every
party in earshot and buys nothing. There is no setting here and nothing to
spread in time. The only bound is the frame itself: a count that will not fit
one frame leaves its surplus for the next beat, which is a sender-local choice
needing no agreement.

**No power sweep follows it.** Ordinary operation measures the same thing and
measures it continuously: every level measured in this protocol pairs with a
stated power, so each exchange yields path loss rather than a bare reading, at
two configurations, for free. A deliberate sweep would buy a cliff measurement
the controller can derive from path loss and a target margin anyway, and it
would buy it once every thirty minutes instead of on every exchange.

**Every SUPE node files every ANNOUNCE it hears**, and it costs nothing to:
the frame arrives on the channel the node is already camped on, so there is no
listening decision to make and no window in which anything else is missed.
Capability entries go stale and nodes reboot, and a listener that tried to
judge its interest in advance would have no way to know when it had judged
wrong.

## 10. Reticulum announces are not ours to touch

Announces a node originates go out as the daemon hands them over, and relayed
announces likewise. No buffer, no replay, no batching, no pacing. An announce
is the daemon's decision and its timing is part of what it decided.

The one setting that mentions announcing therefore concerns SUPE's own frames
alone: `announce_interval` is the gap between a node's ANNOUNCE frames
and governs nothing a Reticulum announce does.

## 11. What is learned, and where it is filed

Everything below is learned from traffic that was going to happen anyway.
Nothing is measured on purpose, and nothing is transmitted in order to measure.

An announcement from a node nothing else has named yet is still worth keeping.
It carries four bytes of each identity and the capabilities together, which is
enough to file against, and the node's own Reticulum announce supplies the rest
whenever it comes. Discarding it instead costs a whole announce interval before
that peer can be met at all — the frame is the introduction.

Against the node, once its ANNOUNCE has been heard: capabilities and maximum
transmit power, keyed by every identity hash the announcement listed. That one
entry serves every destination under any of the node's identities and, if it
is a transport node, all traffic relayed through it — its transport identity
is the address that traffic is sent to, and it is one of the hashes in the
list.

Against the tag: path loss, never a bare signal level. Every reading in this
protocol pairs a level measured on one side with a power stated by the other,
so every exchange yields measurements rather than impressions. With A the
hailer and B the hailed party:

| Reading | Taken by | From | At |
|---|---|---|---|
| A → B pair | B | its reading of the hail, against the power it stated | the hailing configuration |
| A → B pair | B | its reading of A's GIMME, where A answered B's opening HAVE, against the power it stated | the answer's modulation |
| A → B pair | B | its reading of A's train, against the power A's THATSIT states one frame later — or the hail's, where no THATSIT follows | the budget's modulation |
| B → A pair | A | its reading of B's answer — GIMME or HAVE — against the power it stated; and of B's train against B's THATSIT, where a return leg flew | the answer's modulation, and the budget's |
| A → B report | A | the hail reading B's answer carries, and the reading in any later GIMME of B's — the only measurements of the direction A actually transmits in | the hailing configuration, and the meeting's |
| B → A report | B | the answering HAVE's account of how the train was heard, and the reading in any GIMME A sends | the meeting's modulation |

Nothing in that table is a reciprocity assumption. Every reading is taken by
the node that will use it, and the reported ones measure the direction the
reporter's peer transmits in — which no transmitter can measure for itself.

Two bindings that save a slow first meeting:

- **Links inherit.** When the traffic being carried is a link request, both
  sides compute the same link identifier independently and file everything
  under it, so all later traffic on that link opens at the peer's best budget.

  Every hop of a relayed request computes it too, and files it against the node
  it hands the request to. This is the whole of what makes a session through a
  gateway meetable: a link's frames name the link and nothing else — no
  destination, no transport identity — so a link identifier filed against
  nobody is a session that can only ever contend on the shared channel, which
  on a gateway is most of the traffic there is.

  The dialled side has to file it against the *node* as well, and the hail's
  sender identity is the only thing that lets it. A link request names nobody —
  that is Reticulum's design — so a link dialled to us is otherwise anonymous.
  Arriving as a meeting's cargo it is not anonymous at all: the node whose
  schedule carried it is the node that dialled, so the identifier is filed
  against it as the cargo lands and the very first frame back can ride a
  schedule.
- **Relays file per packet.** A relay handling a packet that may attract a
  proof records the sender's capabilities and signal against the reverse-table
  entry, not against the tag — the tag that schedule was seeded on was the
  relay's own transport identity, which every neighbour relaying through it
  shares.
## 12. Failing well

**A hail is answered in one of two ways, and unanswered in only one.** It is
answered when its answer comes — a turnaround later in regime 0, at one of its
two slots under a channel plan — or when the hailed party hails back before the
hailer's interval runs out. It is unanswered when neither happens: the deadline
or the slots pass, the interval passes, and nothing has come from the peer. Everything below follows from keeping those two states distinct, because
the first costs a frame and the second is evidence.

- **The hailed party's duty: the owed hail.** A node that hears a hail naming
  it and does not answer it — its own frame was going out as the hail ended;
  under a plan, it was receiving on the hailing channel when the slot came, it
  was at another meeting's tail, both slot channels were busy where it stood,
  it spoke at a slot and was not answered — owes the
  hailer a hail, and sends one at the first moment it is free: not receiving,
  not transmitting, not at a meeting, and the hailing channel clear. It is
  carrier-sensed like any frame. One owed hail per peer, however many hails
  from that peer were heard; a later hail renews its expiry and nothing else.
  It is discharged by sending it, by a meeting opening with that peer at any
  schedule meanwhile, or by expiry one patience after the hail's epoch — by
  which time the traffic that prompted the hail has expired too, and the
  hail-back would answer a question nobody is asking. A
  node that owes several sends them in the order the hails arrived, and a
  meeting that opens between two of them leaves the rest owed.

  A hailed party that holds traffic for the hailer and is free answers; the
  owed hail is what a party that could not does instead. Which frame it sends
  when the two finally meet is §8's business and is the same either way.

- **The hailer's run.** Silence — no answer by its deadline in regime 0, both
  slots unmet under a plan — is answered by waiting, on the hailing channel,
  holding the traffic: the hailed party may be about to hail back. After the
  interval (§14.7) with nothing from the peer, the hailer hails again, at more
  power, renewing the request and, under a plan, seeding two fresh slots; the
  third hail goes out at the configured maximum (§15). A run is per peer, and
  it runs while the peer has any unexpired traffic behind it: packets arriving
  mid-run join the queue and do not restart the ladder, because a peer that
  did not answer at maximum a moment ago has not become more reachable for
  another packet having arrived. The third hail unanswered ends the run and
  files the peer unreachable, below.

  **The run drops nothing; patience does.** Every packet carries the moment
  it was queued, and leaves the queue when its own age reaches patience
  (§14.7), or in a train when a meeting finally happens — whichever comes
  first. Three hails, their intervals and the channel-access waits in front of
  them fit inside one patience, so a packet that was queued when its run began
  is still there when the run ends, and a hail-back that arrives late finds
  whatever is still young enough. Dropping at patience is safe because link
  data, channel
  traffic, resource parts and proofs all have retry or receipt machinery above
  (§13), and holding a packet longer would put the modem's copy in the air
  beside the daemon's retransmission of it.

  **Power up.** Each successive hail in a run goes out at more power than the
  last, the third at the configured maximum. It covers the one failure the
  hailer can fix from its own side: a peer that simply did not hear the hail.
  There is no ceiling to walk down — the budget conversation moved to the
  meeting, where it is informed by measurement instead of by guessing at
  silence. Three hails is under 110 ms of shared channel, a seventh of what one
  full packet would have cost.

  **A hail-back ends the run wherever it arrives.** A hail from the peer,
  received at any point in the run, is an ordinary hail: the hailer is now the
  hailed party, holds traffic for the node that hailed it, and speaks first
  with HAVE at that schedule (§8). If it crosses with a live schedule of
  the hailer's own, ours is dropped unscored for theirs (§7); in regime 0 a
  hail from the peer arriving while ours awaits its answer IS the answer, and
  is answered. If it arrives
  after every queued packet has reached its patience, the traffic is gone and
  the meeting closes in two short frames.

- **Two records per peer, because presence and reachability are different
  facts.** A peer is **present** when anything of it has been heard recently:
  its Reticulum announce, its ANNOUNCE, a hail to anyone, a frame of its
  meeting with somebody else, a packet from it we carry or relay. A peer is
  **reachable** when it has answered *us* recently: a meeting opened with it at
  any schedule, or a hail from it naming us. Under an asymmetric link the two
  come apart — we hear it constantly and it never hears us — and a rule that
  let presence stand in for reachability would keep hailing such a peer for
  ever, once per frame heard, and never back off. So presence never clears a
  hold; it only shortens one.

- **Unreachable, and the hold.** Three hails unanswered in one run make the
  peer **unreachable**, and no hail is sent to it for the length of a hold.
  Traffic addressed to it meanwhile is queued, not refused: each packet waits
  out its own patience like any other, and the per-peer queue cap bounds what
  that can cost. Declaring that is a bet that trying again is not
  worth the airtime, and a meeting now costs one short frame, so the bet is a
  modest one made in stages: about a second at first, doubling with each
  further unanswered run, to a ceiling of a minute — or of ten seconds while
  the peer has been heard within the last minute, because a fault at a peer we
  can hear is likelier transient than terminal. A full minute imposed on the
  first finding is a blackout that outlives the conversation that provoked it.

  **A peer already unreachable gets one hail per hold, not three.** The run
  exists to establish unreachability, not to re-establish it; once
  established, whatever is queued when a hold ends gets a single hail at
  maximum power, and silence lengthens the hold towards its ceiling. The cost
  of a peer that is genuinely gone falls to one short frame a minute, and of
  a peer that is present and deaf to us, one short frame every ten seconds.

- **Reachability is restored by an answer and nothing else.** A meeting opened
  with the peer at any schedule, or a hail from it naming us — that one being
  the commonest, since a peer that missed a run of ours and has since come
  free will hail us the moment it has traffic of its own or an owed hail to
  send. Either clears the record on the spot and restores the full run. What
  does *not* clear it is hearing the peer talk to somebody else; that refreshes
  presence, shortens the hold, and is all a third party's traffic can
  demonstrate.

- **Two nodes that cannot hear each other oscillate, and the oscillation is
  cheap.** Each hails, hears nothing, backs off; each tries once per hold at
  maximum. Neither ever transmits anything on a traffic channel toward the
  other, because nothing there is transmitted until the other has spoken. The
  whole cost of a broken link is one short frame a minute from each end, and
  the first frame either hears from the other ends it.

- **Two hailers can want the same tag at once**, and on a segment with one
  transport node they usually do — every neighbour's traffic is tagged with
  that node's transport identity. Their schedules are seeded by different
  frames and
  land on different slots, channels and sync words, so they do not collide
  with each other; they compete only for the peer's attendance, and a peer
  attends what it can and owes a hail to the rest. Each hailer whose slots
  pass unmet then waits for that hail, which is exactly the busy-peer case the
  hail-back exists for — the unlucky hailer is not looking at absence, and its
  run only proceeds if the interval passes with nothing heard.

- **A wide schedule expiring unmet is not a failure and scores nothing.** It is
  the ordinary end of a conversation. The next traffic for that peer seeds a
  hail on the shared channel, as any traffic always could.

- **A missed slot inside a wide schedule scores nothing either**, in any
  direction. The peer may be mid-frame on the shared channel — a single
  500-byte packet there spans several early slots — busy at another meeting, or
  simply not listening, and every one of those is an ordinary state. The
  speaking side keeps its slots for as long as it holds traffic, thinning
  attendance as it likes; the horizon is the only give-up there is. Only the
  shared channel may declare a peer unreachable.

- **A meeting that dies mid-way** — a deadline missed after both sides have
  spoken — ends in both parties going home, holding what they hold. The
  receiver delivers the in-sequence prefix it can prove (§8) and surrenders the
  rest; the sender learns the meeting failed and the wide schedule that its
  last *completed* exchange seeded still stands. A meeting that dies is not an
  unanswered hail: the peer spoke, so it is reachable, and the next traffic
  hails it afresh. The channel it died on is the listening side's to remember:
  it chose it — its ledger, its noise — and it should not draw it again for
  that peer soon (§17).

- **Traffic to anything that is not a SUPE peer** never enters this at all. No
  announcement heard means no schedule seeded, and the packet goes on the main
  channel exactly as it would on an interface with SUPE switched off.

- **Never scheduled** — Reticulum announces and path requests. They are
  broadcast, so there is no tag to seed on and no single peer to meet; they go
  on the main channel with no SUPE involvement.

- **Expired regime** — a node whose regime is past its date stops speaking SUPE
  and operates as a plain interface. Its neighbours discover this the ordinary
  way, by it not appearing.

- **Deafness** — a node attending a slot or a meeting misses main-channel
  traffic for the duration. A hailer listening at its two slots is deaf for a
  quarter of a second; a hailed party speaking at one, for one short frame and
  its answer; meetings are bounded by the regime's ceilings; announces repeat
  and path requests are retried, so this is noise against announce cadence.
  §18's worker radios remove it entirely where a second radio exists.

- **A frame lost at a meeting** beyond what one repair round recovers is lost
  exactly as one lost on the main channel is, and the layers above handle it
  identically. **Duplicates** — a repair resend the receiver already held — are
  absorbed by Reticulum's duplicate hash list, exactly as a main-channel
  duplicate is. **Reordering** cannot happen: delivery is in sequence by
  construction (§8).

## 13. Staying invisible to the daemon

Nothing here alters what is on the wire at the Reticulum layer or what the
daemon sees. The modem delays and reroutes frames; the packets themselves pass
through untouched.

The one real interaction is arithmetic. The daemon derives receipt timeouts,
link keep-alive intervals, channel windows and the announce bandwidth cap from
the interface's declared bitrate. Because the modem varies modulation per
meeting and adds schedule latency, that declared number must describe the
*effective* rate including meetings, not the traffic channel's rate — otherwise
the far end of exactly the paths this speeds up reports delivery failures.

**The concrete mechanism to watch is the daemon's per-packet retransmit
arithmetic**, which multiplies a measured round trip by small constants and
tears links down after a handful of tries, and whose shortest per-hop timers
are on the order of seconds — receipts and link establishment at about six
seconds a hop in the reference implementation. A frame held through a run of
hails, a schedule wait, a meeting and a repair round must still fit inside that
budget, and two things keep it fit. Patience (§14.7) bounds every packet's
life in the modem from the moment the daemon handed it over — which is the
moment the daemon's own timer started — so the modem drops what it could not
deliver well before the daemon retransmits it, and the daemon's copy never
goes into the air beside one the modem is still holding.
And a meeting is bounded by the regime's transaction ceiling (§14), with the
schedule bounding the wait ahead of one — no two slots further apart than the
spacing cap, no schedule longer than its horizon. The bound holds only if
patience plus a meeting at its ceiling plus a horizon's wait fits the daemon's
budget at the measured constants of §14.7, which is not yet settled; if it
does not, the declared-bitrate route above is the remaining answer.

### 13.1 Where it does not apply

- **Interfaces with an access code configured.** The frame is masked end to end
  — flags, hops, addresses and context byte alike — so the modem cannot read an
  address and has nothing to seed on. SUPE degrades to plain main-channel
  operation.
- **Peers that have not announced themselves.** A node becomes a SUPE peer by
  its ANNOUNCE being heard, and nothing else. Traffic to anything else
  takes the main channel untouched, so a mixed segment needs no detection and
  no fallback — the absence of an announcement is the whole of it.

## 14. Regimes

A regime is the complete set of constants two nodes must hold identically in
order to meet at all: the channels, the ladder, the sync-word list, the
schedule constants, the timings, the ceilings and the limits. §3 gives the
reason none of it can be configuration. This chapter is that content, and it is
normative — an index on the wire or in a derivation means whatever the table
here says it means, for the version named in the seeding frame.

**Channel 0 is the hailing frequency in every regime.** It is where the
interface's own configuration lands (§3). A regime-1 schedule never draws it —
its raster is §14.2's nine channels; a regime-0 schedule always resolves to it,
because regime 0 has nothing else and changes only the modulation and the sync
word.

### 14.1 Regime 0 — Single Channel

One frequency, one bandwidth, and the spreading factor as the only thing that
moves — the whole protocol on the channel the network already hails on. It
needs no channel plan and therefore no regulatory band plan, which is what
makes it the regime a network can run anywhere, and what makes it the floor
every node supports (§3).

| Constant | Value |
|---|---|
| name | Single Channel |
| version | 0 |
| expires | a calendar date compiled into the build, moved by hand (§3) |
| channels | one: channel 0, the hailing frequency |
| schedule | none. A hail is answered a turnaround later, in place, and the meeting is a dialogue inside the transaction the hail cleared the channel for (§8) |
| the dialogue's shapes | two, selected by GIMME's budget byte: at budget 0 the hail, the answer and the frames, nothing else; at any budget above it the full meeting with its checksums, one repair round and return leg |
| ladder | the spreading factors above the hailing one, at the hailing bandwidth |
| budgets available | to the lowest spreading factor both radio families reach — from an SF7 network, two where both are SX126x and **none where either is SX127x**, whose first entry would be the barred SF6; slower-hailing networks reach further before that bites (§14.3) |
| sync words | the interface's own, throughout; nothing is derived (§14.5) |
| train ceiling | none of its own — see below |
| transaction ceiling | none of its own — the hailing channel's, which in Europe is the 4 s dialogue figure of §14.2; see below |
| transmit power | the interface's `tx_power` |
| airtime accounting | none |

**Regime 0 states no ceilings of its own, and must not invent any.** It has no
band plan and therefore no regulatory basis to draw them from — it runs on the
hailing channel, whose limits belong to whatever rules that network is
operating under, and which SUPE does not own (§3). A dialogue is a transaction
in the regulation's sense and must fit whatever that channel allows one; what
bounds it from inside is arithmetic: the length byte reaches 1.275 s at 5 ms a
step, and a full dialogue is two trains, their repairs and short frames.

**A regime-0 dialogue never leaves the frequency**, so it is never free of the
channel's other users: every node camped there senses it as busy for its
length, and the speaker's carrier sense before the hail was against all of
them. What it buys is bounded by that. At a lowered spreading factor it buys
rate — a quarter of the airtime at SF5 — and a repair round cheaper than the
daemon's retry. At the hailing rate it buys only the probe and the batch: proof
the peer is there before a long frame flies, one reading, and one contest for
the whole batch instead of one per packet. That is why the hailing-rate shape
carries no closing frames, and why a lone short packet to a peer that answered
recently is better sent plain (§0.1).

**A train at a lowered rate is energy without frames to a listener at the
hailing rate.** Its carrier sense sees it and defers; the contention estimator
of [`psa.md`](psa.md), which reads decoded traffic, does not. That is the
position a hidden node already puts every estimator in, and it is the price
of the ladder on a shared frequency.

### 14.2 Regime 1 — ETSI EN 300 220 (863–870 MHz)

**Regulatory basis.** ETSI EN 300 220-2 V3.2.1 annex B table B.1 for the
harmonised non-specific short-range-device bands, their maximum effective
radiated power and their duty cycles; EN 300 220-1 V3.1.1 clause 5.21 and table
48 for adaptive spectrum access, which every 863–870 MHz entry carrying a duty
cycle permits in place of that duty cycle. [`afa.md`](afa.md) §1 derives the
plan below from them and is the reference for why the numbers are what they
are.

**Channels.** Nine, uniform: 500 kHz, 25 mW, all on adaptive spectrum access,
none crossing a band boundary, and at least 200 kHz of clear spectrum between
any two edges — which is what keeps each channel's airtime budget independent
of its neighbours'.

| # | Centre | Span | Band entry |
|---|---|---|---|
| 1 | 863.350 MHz | 863.100–863.600 | K |
| 2 | 864.050 MHz | 863.800–864.300 | K |
| 3 | 864.750 MHz | 864.500–865.000 | K |
| 4 | 865.450 MHz | 865.200–865.700 | L |
| 5 | 866.150 MHz | 865.900–866.400 | L |
| 6 | 866.850 MHz | 866.600–867.100 | L |
| 7 | 867.550 MHz | 867.300–867.800 | L |
| 8 | 868.250 MHz | 868.000–868.500 | M |
| 9 | 868.950 MHz | 868.700–869.200 | N |

Channel 9 fills band N edge to edge, bounded on both sides by alarm allocations
that are not available for this use. If out-of-band emission performance does
not support it, the fallback is 250 kHz at the same centre, which costs peak
rate on that channel and no airtime at all.

**The regulation fixes several of this protocol's constants directly**, and
they are not free parameters:

| Constant | Value | Source |
|---|---|---|
| train ceiling | 1 s | Ton_max, single transmission |
| transaction ceiling | 4 s | Ton_max, dialogue or polling sequence |
| airtime per channel | 100 s in any 3600 s | max Tcum_on, for any given 200 kHz of spectrum |
| minimum gap before reusing a frequency | 100 ms | Toff_min, same operating frequency |
| clear-channel threshold | −75 dBm at 500 kHz, −81 dBm at 125 kHz | table 45, referenced to 0 dBd |
| maximum radiated power | 25 mW e.r.p. (14 dBm) | annex B table B.1 |
| minimum listen before transmitting | 160 µs | minimum CCA interval |
| minimum deferral before listening again | 160 µs, equal to the interval above | minimum deferral period |
| maximum gap from a clear reading to transmitting | 5 ms, declared | dead time |

**The transaction ceiling is what covers a whole meeting.** A meeting is a
dialogue by the regulation's own definition — two nodes alternating on one
frequency — which is precisely what the 4 s figure is for, and it comfortably
holds two 1 s trains, their repair rounds and their turnarounds. The 1 s
single-transmission figure still binds each train separately.

**Airtime is a budget and a window, not a percentage.** A regime records the
pair — here 100 s in any 3600 s — rather than a duty figure, because the pair
is what travels. European polite spectrum access is `100 / 3600`, a European
duty cycle is `360 / 3600`, and North American frequency-hopping dwell is
`0.4 / 20`: three regulatory shapes, one field pair, and no special case in the
code that reads it. A percentage would express the first two and lose the third
entirely.

**A power limit without its reference is not a limit.** The figure above is
25 mW *effective radiated power* — not effective isotropic radiated power, and
not conducted power at the connector. The three differ by the antenna's gain
and by 2.15 dB between the two radiated forms, so a table that records a number
without saying which is being measured is a compliance failure no functional
test will catch. Any regime added later carries the reference alongside the
number.

**The channel cap binds the frame, and the frame states what it did.** A node
configured to transmit at 22 dBm transmits at 14 on these channels, and every
power field in every frame it sends from there states 14 (§0.3). This is not a
detail: a node that states its configured power while transmitting at the
capped one hands its peer a path-loss figure wrong by exactly the difference,
in the direction that makes the link look better than it is, and the peer then
chooses a budget and a power on that basis.

Two further consequences worth naming. The 100 ms minimum gap before returning
to a frequency is part of the speaking side's carrier-sense duty at a slot:
a channel this node used within the gap is skipped exactly as a busy one is.
And the 100 s/h budget is per channel rather than per band precisely because of
the 200 kHz separation above, which is what the accounting in §14.4 must track.

### 14.3 The ladder, and how a budget resolves

This section is normative and admits no floating-point arithmetic anywhere.
Two implementations that resolve a budget differently do not fail loudly —
they set different spreading factors and the link simply dies, with no error
surface anywhere. Every rule below is stated over integers for that reason, and
§14.3.4 gives the conformance test.

#### 14.3.1 What the ladder contains

Given the two nodes' families, the hailing spreading factor and bandwidth, and
the maximum bandwidth the slot's channel permits, an entry `(sf, bw)` is in the
ladder if and only if all of:

- `bw` is one of the regime's permitted bandwidths
- `bw ≥ hail_bw` and `bw ≤ channel_max_bw`
- `sf ≤ hail_sf`
- `sf ≥ 7`, **or** both families reach `sf` per §14.6
- `sf ≠ 6` unless **both** families are able to frame SF6 with an explicit
  header, which excludes SX127x (§14.6)
- in regime 0 only, `bw = hail_bw`

The entry `(hail_sf, hail_bw)` is always in the ladder and is always index 0.
It satisfies the rules above by construction.

#### 14.3.2 How it is ordered

By this key, computed in unsigned integer arithmetic with bandwidth in hertz:

```
key(sf, bw) = (bw × sf) >> sf
```

ascending. That is the net bitrate up to a constant factor, and the shift is an
exact division because the divisor is a power of two. Nothing is rounded, no
coding-rate term appears — it is identical for every entry and cannot change an
ordering — and the products fit in 32 bits for every bandwidth this protocol
permits.

Ties break toward the **narrower bandwidth** first, then toward the **higher
spreading factor**. Both preferences point the same way: at equal rate, take
the entry with more margin. Ties are rare but the rule is not optional, because
sort stability is not a property an implementation may assume of another
implementation.

The key serves one more purpose: where a train described at one budget flies
at a lower one (§8), the receiver's deadline is the stated length scaled by
the ratio of the two keys, rounded up — the same integers, the same result at
both ends.

#### 14.3.3 What an index means

The budget bytes of GIMME and HAVE index the ordered ladder above. Index 0
is the hailing configuration; index *k* is *k* places up. An index beyond the
end of the ladder, or above either node's capability ceiling, is invalid and
the frame carrying it is discarded.

**The proposer resolves before it proposes, and the chooser before it
chooses.** Both hold every input: families and ceilings from the capability
table (§5.1), the hailing configuration from the channel they both camp on, the
maximum bandwidth from the slot's channel, derived identically by both (§7).
There is nothing left to infer and nothing to signal.

Ordered from the SF7/BW125 that most networks hail on, on a 500 kHz channel
with both nodes on SX126x, with each entry's cost in margin against that
reference:

| Index | Configuration | `key` | Net bitrate | Margin cost |
|---|---|---|---|---|
| 0 | SF7 / BW125 | 6 835 | 5 469 bps | reference |
| 1 | SF6 / BW125 | 11 718 | 9 375 bps | 2.5 dB |
| 2 | SF7 / BW250 | 13 671 | 10 938 bps | 3.0 dB |
| 3 | SF5 / BW125 | 19 531 | 15 625 bps | 5.0 dB |
| 4 | SF6 / BW250 | 23 437 | 18 750 bps | 5.5 dB |
| 5 | SF7 / BW500 | 27 343 | 21 875 bps | 6.0 dB |
| 6 | SF5 / BW250 | 39 062 | 31 250 bps | 8.0 dB |
| 7 | SF6 / BW500 | 46 875 | 37 500 bps | 8.5 dB |
| 8 | SF5 / BW500 | 78 125 | 62 500 bps | 11.0 dB |

Both columns increase monotonically and no entry is dominated, which is what
makes an index meaningful rather than a lookup (§3). The margin figures come
from Semtech's required-SNR values and are what turn a measured path loss into
a budget choice; [`afa.md`](afa.md) §3 carries their derivation. Measured
sensitivities on the LR2021 at 125 kHz — −127.5 dBm at SF7, −125 at SF6,
−122 at SF5 — put SF7 to SF5 at 5.5 dB rather than 5.0, and the derivation
should be reconciled with the parts it is applied to (§16).

The same inputs with a 250 kHz channel maximum give a six-entry ladder — the
first five entries of the table above plus SF5/BW250 on top — and index 6 is
then invalid rather than meaning something else.

**A pair including an SX127x has a ladder of bandwidth changes only.** That
family reaches no SF5 at all, and SF6 there demands an implicit header, which
no framing in this protocol supplies (§14.6). Strike both and what remains from
SF7/BW125 on a 500 kHz channel is SF7/BW250 and SF7/BW500: two entries, 2× and
4× the rate, at 3.0 dB and 6.0 dB. The consequence for regime 0 is blunter — it
cannot change bandwidth, so **an SX127x pair hailing at SF7 has no entries
above 0 under regime 0**. A slower-hailing network is unaffected until the
ladder reaches SF6: from SF8 such a pair has one, from SF9 two. Where there are
none, budget 0 is still a working meeting (§14.1); it simply flies at hailing
rate.

**A budget costs reach as well as margin.** The margin column says what the
link has to give up; what it buys is rate, and what it spends is coverage. From
SF7/BW125 to SF5/BW500 is 11.02 dB — 6.02 dB of it from four times the
bandwidth and 5 dB from the demodulator's signal-to-noise limit — which puts
the fastest entry's reach between 28 % of the hailing channel's in free space
and 53 % at a path-loss exponent of 4, so roughly half in practice. A peer at
the edge of hearing therefore has no budget at all, and the ladder is a
facility for the near population. That is the population a node exchanges most
of its traffic with, which is why this is a tolerable shape rather than a
limitation.

**Low-data-rate optimisation is part of what an index resolves to**, not a
local choice: both ends must set it identically or neither decodes. It is
compulsory wherever a symbol lasts longer than 16 ms, which is
`2^SF / BW > 16 ms` —

| Configuration | Symbol | Optimisation |
|---|---|---|
| SF11 / BW125 | 16.4 ms | on |
| SF12 / BW125 | 32.8 ms | on |
| SF12 / BW250 | 16.4 ms | on |
| everything faster | under 16 ms | off |

It exists to keep a receiver locked when the crystals at each end drift
measurably within one symbol, and it pays for that by carrying two bits fewer
per symbol — which is why it appears in the airtime arithmetic of §3 as `DE`. A
network hailing at SF11 or SF12 therefore starts with it on and may switch it
off partway up the ladder; the rule above decides that, from the resulting
configuration, on both sides, with nothing transmitted.

#### 14.3.4 Conformance

Prose cannot be executed and two careful readings of it can still differ. An
implementation is conformant with this section if and only if it reproduces
`supe-ladder-vectors.txt` exactly, over the full cross-product of:

- the two families, over every value in §14.6
- hailing spreading factor 5 through 12
- hailing bandwidth over every permitted value
- channel maximum bandwidth over every permitted value
- regime 0 and regime 1

Each line gives the inputs, the ladder length, and every `(index, sf, bw,
low_data_rate)` it resolves to. The file is generated from the rules above and
is the authority when it and a reading of the prose disagree. The schedule
derivation of §7 wants the same treatment — a vector file over seeds — once its
constants settle (§16).

This is the same discipline a cryptographic test vector serves, and for the
same reason: the failure being guarded against is silent divergence between two
independent implementations, which no amount of care in either one can detect.

### 14.4 Airtime accounting

Where a regime states an airtime budget, a node has to be able to answer
whether it has spent it. **This section states the property that answer must
have, not the structure that produces it** — the structure is an
implementation's own business, and every implementation already has an airtime
figure of some shape that this must not be confused with.

Four requirements, and all four are load-bearing:

- **The transmit path reads a verdict, not an arithmetic result.** "May I
  transmit on this channel" is precomputed, per channel, and looked up. A
  budget summed at the moment of transmitting puts a loop on the path that most
  needs to be short.
- **It is fed unconditionally**, not gated on anything being displayed,
  recorded or watched. A figure that exists only while somebody is looking at
  it cannot defend a cap.
- **Every transmission is counted, including ones that achieved nothing** — an
  aborted train, a GIMME nobody answered, a frame cut short. The regulation
  counts emissions, not successes.
- **It errs by no more than the margin the effective cap absorbs.** Whatever
  the recomputation interval, the verdict may be that interval stale, so the
  effective cap is set below the legal one by at least what can be spent in it.
  Set that way, coarse bins are free: the cap is approached slowly and the
  error cannot cross it.

A ring of 10-second buckets, one per channel, satisfies all four — 360 buckets
to the hour at 16 bits of milliseconds each is 7.2 kB for ten channels,
credited at transmit-done with the time on air the radio already computes,
recomputed once per bucket. It is a good answer and not the only one. What it
is not is a reinterpretation of a coarser total kept for some other purpose: a
structure that ages whole buckets out of a short history lets the budget be
spent late in one bucket, aged out, and spent again, which approaches twice the
cap inside a true window. Defending a cap wants a structure chosen to defend
it.

**Both sides read this table at every slot.** The speaking side's carrier-sense
duty includes its own ledger — a slot whose channel has no budget left is
skipped like a busy one — and the receiving side's GIMME is a budget decision
too: the train it invites will spend its ledger, at the budget it names.

### 14.5 Sync words

The hailing channel keeps the interface's own `sync_word`, `0x42` by convention
— that channel belongs to the Reticulum network, not to SUPE (§3). Under a
channel plan every meeting flies under a **derived word**: the slot's stream
byte indexes a word list both ends construct identically, per spreading
factor, from the rules below. Regime 0 has no meeting off the hailing channel
and keeps the interface's word throughout: the one thing a derived word would
buy there — sparing third parties' demodulators a frame not meant for them —
is not what a shared frequency offers anyone, and a dialogue that everyone
can decode is one the channel's contention estimator can count. The word is the first filter a meeting has, and it runs in silicon — a
frame under somebody else's word never raises an interrupt.

**The arithmetic the rules rest on.** The sync word is two symbols after the
preamble, and each nibble is scaled by 8 to become a symbol value — a bin index
in a space of 2^SF bins. All that separates two sync words on air is the
distance between those bins, and the demodulator simply takes the strongest
bin. Frequency error spreads energy into neighbouring bins: at SF7/BW125 a bin
is ~977 Hz wide and 20 ppm of crystal error at 868 MHz is ~17 kHz — about two
nibble-steps — so words within two nibble-steps of a foreign network's word can
cross-detect between poorly calibrated radios.

**The word list, per spreading factor:**

- **no zero nibbles** — bin 0 is another preamble upchirp, so a zero nibble
  detects weakly and false-triggers on long preambles;
- **nibbles inside the bin space** — `nibble × 8 < 2^SF`, which caps nibbles at
  3 for SF5 and 7 for SF6 and admits all fifteen from SF7 up;
- **a two-nibble berth, in both symbols, around every foreign word** — `0x12`
  and `0x24` (vanilla LoRa defaults), `0x34` (LoRaWAN public), and the
  interface's configured hailing word. A word is excluded when *both* its
  nibbles are within 2 of the corresponding nibble of a foreign word; one
  distant symbol is separation enough;
- **at SF5, exact exclusions only** — its nine possible words all sit within
  the berth of `0x12`, so the berth rule would empty the list. SF5 is nearly
  empty of other networks — LoRaWAN defines nothing below SF7 — so the weak
  separation is spent where nobody else is listening, exactly as the strong
  separation is spent where they are.

The list is ordered ascending and indexed by the slot's stream byte (§7). The
list is per spreading factor, and the stream byte follows the frames: the
slot's opening exchange indexes the list built for the slot's spreading factor,
and everything from the train on indexes the list built for the confirmed
budget's — the same byte, re-indexed, since a word admissible at one spreading
factor can be out of range at another. No distance is kept between the list's
own words: a cross-detection between two of our meetings just wakes a receiver
whose seed-hash check kills the frame — the 2⁻⁸ residue is cheaper than the
entropy that separating them would spend.

**Between our meetings and the hailing channel** there is a frequency as well
as a word; between two of our own meetings the seed-hash bytes back the word,
and the length test backs those. Remember that the two-byte form on
SX126x is a nibble expansion, not an addition ([`afa.md`](afa.md) §5.4), so the
word space really is the eight-bit one across families.

### 14.6 Radio families

The family nibble names what a peer's silicon can do, in the few respects that
change what goes on the air. The ceiling nibble beside it already says how far
up the ladder a node will go; family exists for the things a ceiling cannot
express — chiefly that one family reaches no lower than SF6, and frames it
there differently from everyone else.

| Value | Family | Parts | Spreading factors | On the air |
|---|---|---|---|---|
| 0 | SX126x | SX1261, SX1262, SX1268, LLCC68 | 5–12 | the reference case; nothing special |
| 1 | SX127x | SX1272, SX1276, SX1277, SX1278 | 6–12 | **no SF5 at all**, and SF6 carries an implicit header, so an entry landing there needs a fixed frame length agreed in advance (§16). Also lacks the receiver's stop-timer-on-preamble, so its slot dwell is a CAD loop rather than a timed receive — a listener cost, invisible on the air |
| 2 | SX128x | SX1280, SX1281, SX1282 | 5–12 | 2.4 GHz only, and 203/406/812/1625 kHz bandwidths — no overlap with a 863–870 MHz regime, so it can only appear under a regime of its own |
| 3 | LR11x0 | LR1110, LR1120, LR1121 | 5–12 | as SX126x for these purposes |
| 4 | LR2021 | LR2021 | 5–12 | as SX126x at a meeting. On the hailing channel it **listens at several spreading factors at once**, and which ones is fixed by this rule rather than announced: the hailing SF and the three below it, never below SF5, at the hailing bandwidth — SF5, SF6 and SF7 on an SF7 network, SF6 through SF9 on an SF9 one. Anything bound for the hailing channel — a plain packet, a hail — may go to such a node at any of those (§15) |

Both rules that follow are the **budget chooser's** to apply — the GIMME
sender's:

- **Every entry in the ladder must be reachable by both**, which §14.3.1 states
  as a membership rule rather than as a check afterwards. A pair of SX126x
  nodes on an SF7 network has eight entries above 0 on a 500 kHz channel; a
  pair including an SX127x has two, and under regime 0 none.
- **SF6 is not offered where either side is family 1.** It demands an implicit
  header there, and no framing in this protocol supplies the fixed length that
  would need. §16 carries the exception that would recover it.

**Family 4's listening set is a rule, not a capability field**, because the
configuration is this protocol's to make and the rule is computable by both
ends from the hailing SF alone. The silicon's own constraints shape it: one
main detector and up to three side detectors, all on one channel and one
bandwidth, all distinct, the main carrying the smallest SF, and no more than
four apart — so four consecutive SFs ending at the hailing one is the largest
set that always fits, and it fits with a detector spare on any network hailing
at SF7 or below. Detection is parallel and demodulation is not: the first
detector to see a preamble takes the demodulator for that frame, and the
packet status says which one it was. The datasheet states no sensitivity and
no current penalty for a side detector, which is what lets the set be treated
as listening rather than as listening less well; the figure wants confirming on
the bench (§16). Every side detector must be reprogrammed after any change of
modulation, which for an implementation means after every meeting.

The nibble holds sixteen values against five families, which is room enough
that a new part gets a number rather than a compatibility rule.

### 14.7 Turnaround, retune, patience and deadlines

Every deadline in this protocol is derived from the constants below and a time
on air. They are regime constants (§3) and all of them are stated values rather
than measurements — the open item that most wants
[`simulation.md`](simulation.md), because a bench measurement of them needs
instrumentation we do not have and a simulator charges them by construction.

| Constant | Value | What it is |
|---|---|---|
| turnaround | 50 ms | The longest a node may take between the end of a frame it is answering and the start of its answer, on the same channel and configuration. It is what the second frame of a meeting must beat after the first, and what the answer must beat after a THATSIT. Sized with room above the flip below: an answer waits the flip and then rides the answering node's own path to the air, and a deadline that expires on an answer that is merely slow costs a whole meeting. |
| flip | 10 ms | The least a node leaves between the end of a frame it is answering and the start of its answer. The node that just transmitted is turning from transmit to receive — and, after a GIMME, retuning to the budget on the way — and a preamble that starts before that turn is done is never heard. At the hailing configuration a preamble is long enough to hide the turn; at the fastest budgets it is two milliseconds and hides nothing. Every answer observes it: the GIMME after a HAVE, the train after a GIMME, the RESEND or BYE after a THATSIT, the answer to a hail in regime 0. A frame that follows the sender's own frame — the THATSIT after its train — does not, since the far end's receiver has been open throughout. |
| seed gap | 100 ms | Under a channel plan only: the epoch to the first slot of a narrow schedule. It is the **speaker's** allowance: the hailed party has just demodulated a frame and must derive a schedule from it, retune to a channel that frame chose, sense, and be speaking — while the listener, having transmitted the seed, knew the schedule before the seed had finished flying and is on the channel with its receiver open at no cost to anyone. A speaker that is slow is heard by a listener that is already there; the reverse arrangement is the one that fails, because a receiver opened part-way through a frame has missed the preamble and hears nothing at all, however strong the signal. |
| retune gap | 1 ms | The interval both sides observe between the last frame at one configuration and the first at another — arriving at a slot, and stepping to the confirmed budget before the train. It exists to cover the synthesizer, not the software. |
| train gap | 2 ms | The interval before and between the frames of a train: the receiving side must service RX-done and read the frame out of its modem before the next one lands in the same buffer. The receiver itself stays open throughout — nothing restarts it between frames, because restarting a receiver aborts the preamble it is already demodulating. Counted into every stated train length, so the far end's deadline covers it. Nothing else rides in it — the sender's next frame is already built. |
| guard | 10 ms | Slack added to every derived deadline *inside a meeting*, absorbing scheduling jitter at both ends. There the task is hot: the previous frame's completion is what drives the next step. |
| slot guard | 40 ms | Each edge of a slot's listening window. A slot is reached from an idle task — a timer fires, the task wakes, retunes, senses, builds — and on hardware that path puts a slot's opening frame 16–20 ms past its nominal moment. The window is sized to that slop and **not to the preamble**: a preamble is 16.6 ms at a hailing SF7/125k and 2.1 ms at SF5/500k, so a window sized to it is generous exactly where the schedule is slow and shut before the speaker transmits exactly where it is fast. It also dominates clock drift across a schedule's whole horizon (§7). |
| slot lateness | 20 ms | How late the speaking side may open a slot before giving it up. Well inside the listener's tail, so a speaker that still tries is a speaker the listener is still hearing. |
| hail interval | 300 ms, plus up to 200 ms of jitter | How long a hailer waits, after a narrow schedule's horizon, for a hail-back before hailing again. Long enough for a hailed party caught mid-frame on the hailing channel to finish and hail; jittered so that two hailers of one busy peer do not retry in step. |
| patience | 3 s | How long a packet may wait in the modem, from the moment it was queued, before it is dropped (§12). Per packet: a run's three hails, schedules and intervals fit inside one patience, with the channel-access waits in front of each hail, so the packet that started a run outlives it; it must sit well inside the daemon's shortest per-hop timer (§13). It also bounds the owed hail at the other end, which expires one patience after the hail it answers — a generous stand-in for the hailer's oldest packet. |
| hold | 1 s, doubling to 60 s; ceiling 10 s while the peer has been heard within the last minute | How long an unreachable peer is left alone between single hails at maximum power (§12). |

**The retune gap is deliberately small, and the reason is that the silicon is
fast.** A synthesizer hop on an SX1261/2 is 30 µs, a full wake from standby
about 150 µs, and the transmit-receive switch under 600 ns (datasheet table
3-7); the power-amplifier ramp is 10 µs to 3.4 ms depending on how it is
configured. All of that is microseconds, and the BUSY line signals readiness
directly, so a driver that waits on it is already correct without any interval
at all. The millisecond is there for the scheduling gap between a completion
interrupt and the task that acts on it, not for the radio.

**The turnaround is generous, and the reason is that it is free.** It appears
only inside deadlines: a node that answers in 3 ms is not penalised for the
constant being 25, and no airtime is spent on it by anybody. What it buys is
tolerance for a responder whose radio task is behind a storage commit or a
display update. Shrinking it would tighten the deadlines and gain nothing,
because nothing waits out a turnaround that has already been satisfied.

**Patience is the one constant here that faces outward.** Every other value
bounds an exchange between two modems; this one bounds how long a modem may
hold something the daemon believes it has sent. Too short, and a peer that was
merely busy hails back to find nothing waiting; too long, and the daemon's own
retransmission goes into the air beside the copy the modem still holds. It is
measured per packet from the queue rather than per peer from a hail because
the daemon's timer is per packet too, and a packet that joined a run late is
no younger for it. Three seconds leaves a run its three hails with the
channel-access wait in front of each — a hail behind a busy hailing channel
waits a second and more for its turn, and a run that cannot afford three of
those never reaches the hail at maximum power — and still leaves the daemon's
six-second-a-hop arithmetic three seconds of margin. It is the value most
worth measuring against real daemon behaviour (§16).

The deadlines that follow, all derived and none transmitted:

| Waiting for | Armed at | Deadline |
|---|---|---|
| a slot, listening side | the slot's offset | the window `[offset − guard, offset + guard + toa(preamble)]`, its timeout stopped by a detected preamble. Silence at the window's end is a slot unmet. On a narrow schedule the hailer may open earlier and should (§7) |
| the answer to a hail, regime 0 | end of the hail | `turnaround + toa(HAVE, hailing) + guard` — sized to the larger of the two answers, since which arrives is the answer itself |
| the GIMME that confirms an opening HAVE | end of the HAVE | `turnaround + toa(GIMME, slot) + guard` |
| the train's first frame | end of the GIMME that confirmed its budget — or, in a hailing-rate dialogue, of the frame before it | `retune_gap + turnaround + guard` |
| the train | its first frame | the `length` its hail or HAVE stated, scaled by the ladder keys where the confirmed budget is lower than the one it was stated at (§14.3.2), never more than the train ceiling; plus `guard` |
| THATSIT | end of the train's last frame | `turnaround + toa(THATSIT, n, budget) + guard` |
| the answer — BYE, RESEND or answering HAVE | end of THATSIT | `turnaround + toa(answering HAVE, budget) + guard` — sized to the largest of the three, since which arrives is the answer itself |
| a repair round's frames | the RESEND that asked | the resent frames' airtime plus gaps, computable from the bitmask, plus `guard` |
| a hail-back | a narrow schedule's horizon | the hail interval; then the next hail of the run, until the third goes unanswered |

Every one of them is computable by both sides from the frames already
exchanged and the schedule already derived, which is what lets a failure be a
silent return rather than a negotiation.

## 15. Adaptive transmit power

Part of SUPE, gated on the `adaptive_txpower` option (§4), and applying to
unicast only: every hail, every frame of a meeting, and any packet sent plainly
to a single peer. Never to anything broadcast — announces and path requests.

**Every frame this protocol aims at one node is adapted, because every frame it
sends is aimed at one node.** Nothing here needs third-party reach: no frame
carries a hold, a reservation or a hint for anyone but its addressee (§6), so
there is no frame that must go out at maximum for somebody else's sake. The
schedule's slots are attended by exactly one listener, and the shared channel's
one SUPE frame beyond the announcement — the hail — is addressed to the one
node whose measurements the controller holds.

**The hail is the natural power probe.** §12's run is that probe run to
conclusion: each hail at more power, the last at maximum. A peer whose
attendance begins only at the third schedule has told the controller exactly
where the cliff is, on frames it was going to spend anyway.

**Maximum means the node's configured `tx_power`, capped by the channel's
regulatory limit, never the radio's ceiling.** The range this controller works
in is bounded above by the lower of those two and below by what the part can
do, so a radio held to 14 dBm on a channel that permits 14 stays there and
simply has a shorter run to walk. Nothing here may raise a node above either
bound, under any failure, for any reason.

**Start at the top and walk down on evidence.** The controller's initial power
for a peer it has never adapted to is the configured maximum, not a computed
value.

**Do not compute an absolute power from a path loss and a modelled
sensitivity.** It produces plausible arithmetic and implausible answers: a
49 dB path loss against a −121 dBm modelled sensitivity and 10 dB of margin
gives −62 dBm, which is nonsense, and the only thing between that and a dead
link is a floor constant. A floor doing that much work is not a safety net, it
is the design. The measured term is real; the modelled one is not, and a
controller that treats them alike fails silently and completely. §14.3's
insistence on integer rules over formulas guards the same hazard in a different
place.

**Power is derived from a learned offset, never stored as an absolute:**

```
power = clamp( maximum − offset , floor , maximum )
```

where `offset` starts at zero and only ever moves on evidence about *this*
peer. The measured path loss and the entry's margin cost (§14.3) inform how
large a step to take and where to stop, but they do not set the power directly
— they bound the search rather than replacing it.

- **Failure raises the power fast.** A single miss cuts the offset by about
  6 dB; a peer that has gone unreachable goes straight back to maximum. Being
  wrong downward costs connectivity, so recovery is immediate and large.
- **Success lowers it slowly**, 1–2 dB, so any overshoot past the cliff is
  small and the next failure recovers it.
- **The decrement is gated on evidence, not time.** Require a number of
  successful exchanges with *that* peer since the last change. "Nothing went
  wrong lately" means nothing if nothing was sent, and a controller that dials
  down on a timer will walk a quiet link into the ground.
- **Where it broke is remembered.** After a failure at a given power, do not
  return below it plus a margin for a while, on a decaying floor. Without that
  the loop oscillates across the cliff instead of settling above it.

**A peer that listens at several rates gets a hailing-channel budget beside
the power offset, and the two are one margin.** To a family-4 node (§14.6) a
frame bound for the hailing channel — a plain packet, a hail — may fly at any
spreading factor in its listening set, and the controller holds, per such peer,
the one it currently sends at. The rules above apply to it unchanged: start at
the hailing SF, which is the top; step down one entry on the same evidence
that would lower the power, spending the entry's margin cost (§14.3) out of
the same measured surplus; step back up on a failure, and remember where it
broke. The first choice toward a peer nothing has yet reported on is the only
blind one, and it is made from the peer's announce — its stated power against
our reading, read as the level our own frame will arrive at, which is a
reciprocity assumption this protocol otherwise never makes — and it errs
toward the hailing SF. Every report that follows replaces the assumption with
a measurement. What this buys is the largest saving in the protocol: a lone
packet to such a peer at SF5 costs a quarter of the airtime and a quarter of
the blind-send risk, with no dialogue at all, and a hail to it costs about
10 ms.

**The train's power is resolved where the train flies, on the report just
received.** The GIMME or HAVE that answered carries a reading milliseconds
old, of the sender's own transmission, on the very channel the train is about
to use; the confirmed budget's margin cost (§14.3) is the known step between
the modulation that was measured and the one about to fly. The adjustment is a
relative correction from a fresh measurement — the one kind of arithmetic this
section permits — clamped by the maximum and remembered by the floor like any
other move, and THATSIT states the result so the peer's pairing stays true.
The same applies to the answering side's train against the readings it was
handed.

**A meeting is a measurement machine, and its closing frames are the loop's
acknowledgements.** Every GIMME and HAVE reports how the last frame from its
peer was heard — the hail, an opening HAVE, or the train's worst frame — which
is the direction the reporter's peer transmits in, and which no transmitter
can measure for itself. A hailing-rate dialogue ends with the train and
reports nothing after it, so there the controller learns from the answer alone
and the train flies at the hail's power; it adapts between dialogues, not
inside one. And the
closing BYE or RESEND, though it carries no reading, is the proof the train's
sender needs: its arrival says the train and its THATSIT landed, and a
RESEND's bitmask says precisely how well. A repair bitmask growing dense at a
given power is the cliff announcing itself before anything is lost outright.

**What is evidence and what is not:**

- **The closing frame comes back, the bitmask sparse or empty** — the power
  suffices and the readings say how much room there is. Sustained, this walks
  the offset up.
- **The readings report thin margin** — hold, do not reduce.
- **A meeting's deadline lapses after contact was made** — the power may have
  been too low, or the channel died under them, and raising covers both. But
  the two are distinguishable more often than they look, and the peer's own
  report of our frames is what distinguishes them: put the power that just
  failed through the path loss that report measured, at the configuration that
  failed, and compare with what that configuration needs. **With real margin in
  hand, do not raise** — the frame was lost to something power does not fix,
  and each miss that raises anyway files a floor six dB up for a decay's
  length, so a handful of them walk a link with thirty dB of margin to maximum
  and hold it there. That is the measured term overruling the fact of a miss,
  which is the same discipline this section applies everywhere else: a
  measurement outranks an inference. With no fresh report there is no
  measurement and the miss stands on its own — raise.
- **A hail unanswered at its slots, then a louder one answered at its own** —
  a power measurement: the hail power that drew attendance bounds the cliff
  from above, the ones that did not bound it from below. Nothing else varies
  between the attempts, which makes it the cleanest reading the controller
  ever gets. A hail-back says less — the peer heard *some* hail of the run and
  was busy — so only attendance at a slot binds the reading to a particular
  hail.
- **A missed slot, a wide schedule expiring, a hail answered by a hail-back, a
  delivery signal that never came from a peer still being heard** — nothing. A
  peer whose frames still arrive is reachable, whatever else went wrong;
  congestion, a busy far end and a stalled transfer are all made worse by
  transmitting harder, and none of them is about power.

## 16. Open items

- **Side-detector sensitivity on the LR2021, measured.** §14.6 treats a side
  detector as listening, not listening less well, on the datasheet's silence
  about any penalty. Confirm on the bench at the cliff before a hail is
  trusted at a side SF, and read AN1200.102 (LR2021 LoRa performance) for
  whatever it says. In the same session, reconcile §14.3.3's margin costs with
  the part's measured sensitivities (5.5 dB SF7 to SF5 at 125 kHz against the
  table's 5.0).
- **The §14.7 and §7 constants, measured rather than assumed** — the
  turnaround, the retune gap, the train gap, the guard, the seed gap now that
  it is the speaker's allowance, and the schedule's spacings, jitters and
  horizons. They set every deadline and every slot, and with them how cheap a
  failed attempt is. First thing [`simulation.md`](simulation.md) should be
  pointed at, and the schedule derivation wants a conformance vector file the
  way the ladder has one (§14.3.4).
- **Patience, the hail interval and the hold against the daemon's timers.**
  The three bound how long a packet lives in the modem, and they are stated
  against a reading of the reference daemon's per-hop arithmetic rather than
  against a measurement of it under load. What wants checking is that a
  packet's drop at patience always precedes the daemon's retransmission of
  it, and that an owed hail arriving after the traffic has expired is rare
  enough not to matter.
- **The checksum function.** CRC-8 polynomial 0x07 is specified provisionally
  (§8); what it needs is to be identical at both ends, cheap per frame, and no
  worse than 2⁻⁸ on the alignment match. Confirm the choice before first
  implementation, since it cannot change inside a version.
- **A fixed-length framing exception for SF6 on family 1.** SF6 demands an
  implicit header on SX127x ([`afa.md`](afa.md) §4.1), so the protocol skips it
  and leaves such pairs with bandwidth entries alone — and, on a network
  hailing at SF7, with nothing at all under regime 0 (§14.3). An exception
  carrying a length agreed in advance would recover a spreading-factor entry
  for every pair including an SX127x.
- **Out-of-band emission performance, and whether channel 9 can be 500 kHz.**
  The channel fills band N edge to edge between two alarm allocations (§14.2),
  so what decides it is the skirt of a 500 kHz LoRa signal against the
  out-of-band limits at 868.6 and 869.2 MHz — not the far-field spurious limits
  that dominate most compliance work. The fallback is already known — 250 kHz
  at the same centre — so this decides only whether the fallback is needed.
- **Preamble handling at SF5 and SF6.** Both have modified preamble and sync
  behaviour on the SX126x; confirm the derived word list of §14.5 lands as the
  bin arithmetic says. The arithmetic is settled; the silicon's treatment of it
  wants checking.
- **Regime 1.** Whether hailing and meeting traffic land in one duty budget or
  two.
- **The reference count on tag entries.** Three bytes with a thousand live
  entries collides internally often enough to matter; confirm a count is enough
  and that nothing needs the full address kept alongside.

## 17. Deferred

- **A hail-back-only announcement.** A node that is away at meetings most of
  the time — a gateway — takes few slots and owes many hails, and its
  neighbours spend two slots' listening on it before learning that. One bit of
  the capabilities would let it say so in advance: *hail me, and I will hail
  you back; do not attend my slots*. The wire needs nothing else, since the
  hail-back already exists; what it costs is that every conversation with such
  a node opens with two frames on the shared channel instead of one, which is
  why PUBSYNC below is the real answer for that node and this is only the
  cheap one.
- **PUBSYNC — the public schedule.** The same primitive with one parameter
  changed: a frame that seeds a schedule whose slots *anyone* may transmit in,
  contention inside a slot resolved by ordinary carrier sense at a better
  modulation than hailing's. A gateway advertising 10–20 % of its time as open
  windows publishes a schedule instead of deriving pairwise ones. With it, a
  byte or two of the sender's own clock phase piggybacked on frames it already
  transmits, so the neighbourhood stays disciplined to its rhythm without
  PUBSYNC repeating often — and so a crystal-disciplined node can tell an
  RC-clocked one how far it has drifted, one correction per frame heard.
- **A keyed derivation.** The schedule is a pure function of a public frame, so
  a third party can compute where a pair meets. Replacing the seed's hash with
  a keyed digest makes the hop sequence private without changing a frame or a
  state machine — deliberately: nothing functional rests on the derivation
  being public, so keying it later is purely a privacy decision. Named here so
  the openness is read as a decision, not an oversight.
- **A clock-quality capability.** The flat guard covers RC drift across a 3 s
  horizon; a longer horizon, or tighter windows, wants schedules drawn to fit
  the worse clock of the pair. One nibble of the capabilities would carry it.
- **Acting on the first frame of a split packet.** Frames arrive one at a time
  and the observer reads the first frame's addresses at its RX-done, so "this
  packet is not for us" is knowable a frame early. Cheap to know, costly to act
  on — leaving mid-reassembly forfeits the packet — so the sane first use is a
  node one frame into a not-for-us reception knowing it is free to leave for a
  slot it must speak at.
- **Remembering which channels work, per peer.** Two halves: what a node knows
  about where it stands — persistent local interference on part of the band, a
  warehouse interrogating tags, a neighbour's equipment parked on one frequency
  — and what it observes, which channels have carried a meeting and which have
  only ever produced silence. Both want a rule for how a channel is judged bad,
  and how that judgement is forgotten; the schedule's derivation would then
  filter rather than draw blind.
- **Destination-scoped proof returns.** Naming the destination a proof belongs
  to rather than the packet hash, so a burst of messages to one destination
  collapses to a single table entry. It costs precision — the destination's
  owner and every other recent sender wake too — so it is only worth doing if
  pending-proof entries become a real memory pressure. They are eight bytes and
  short-lived, so probably not.
- **Asymmetric budgets per direction.** Both directions are measured at every
  meeting (§11), which makes this plausible — but both trains share one channel
  and one confirmed budget, and splitting them means a retune inside the
  meeting. The answering HAVE's budget byte is where it would go.

## 18. What this protocol assumes of an implementation

SUPE is a wire protocol and this document is not an architecture. But four
implementation properties are load-bearing enough that getting them wrong makes
the protocol behave badly on air rather than merely making the code awkward,
and they are stated here for that reason alone.

**Meeting airtime is accounted separately from the hailing channel's.** The
claim that a node using SUPE stays cheap on the shared channel — and therefore
stays in the low contention bands for the traffic that must still go there —
depends on it. Credit a meeting's transmissions against the hailing channel's
duty figure and the effect vanishes silently, with nothing failing and nothing
to see.

**Whether to hail is one function, in one place.** Its inputs are the peer, the
queue and the channel; its output is `no`, `now`, or `wait until t`. The
protocol deliberately does not specify it, because the right answer varies with
traffic shape and nobody has measured it yet. What the protocol does require is
that the decision be *findable*: a policy scattered across a transmit path
cannot be measured, cannot be replaced, and cannot be simulated.
[`simulation.md`](simulation.md) §7 is written against exactly that signature.
The owed hail is an input to the same function, not a second one: a node that
is free and owes a hail has a peer, a reason and a channel, and the answer is
`now`.

**The train buffer is provisioned, not assumed.** Delivery at the close of a
meeting (§8) means a receiver holds a whole train in memory — count times the
interface's frame size, per concurrent meeting, of which there is one. The
count ceiling a node advertises and the ones its GIMMEs state are promises
against that buffer, made before the train flies, never discovered against the
allocator afterwards.

**A radio attending a meeting need not be the radio that hails.** The deafness
of §12 exists only because one radio cannot be in two places. A node with a
second radio in the same band can stay on its hailing channel throughout, and
the protocol is entirely indifferent to which radio transmits what — nothing on
the wire changes. What that costs an implementation is that a slot must ask for
*a radio able to reach this channel* rather than assuming its own, which is a
seam worth building before it is needed rather than retrofitting into a state
machine that assumes otherwise. A radio so dedicated presents no interface to
the daemon and serves any hailing channel in its own band; crossing bands is a
routing problem rather than this protocol's.
