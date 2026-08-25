# SUPE — Spectrum Utilization and Performance Enhancements

> Status: **Aug 2026 — implemented in development versions of
> [Reticulous](https://reticulous.net).** The schedule constants of §7 and the
> timing constants of §14.7 are now measurements taken on hardware rather than
> estimates; the sync-word behaviour at SF5/SF6 is still an estimate, and
> [`simulation.md`](simulation.md) is where it is meant to be settled.
> `supe-ladder-vectors.txt` remains the §14.3.4 conformance authority for the
> ladder.
>
> `sender_ident` (§4) is what makes the return leg possible at all, and it is
> the one thing here that gives up sender anonymity, so it is a key and not a
> constant.
>
> SUPE moves unicast traffic off the shared LoRa channel onto private meetings
> at derived times, channels and sync words, entirely inside the modem, with the
> Reticulum daemon unmodified and unaware. One frame on the shared channel seeds
> everything; every meeting's own goodbye seeds the next, so a pair with steady
> traffic touches the shared channel once.
>
> SUPE also adapts transmit power to what is actually needed, so two nodes in
> the same room might be carrying their unicast traffic at 125 µW.
>
> Everything normative is here: the frames in §0.1, the schedule in §7, the
> regimes with their channels, ladder, sync words, timings and limits in §14,
> and adaptive power in §15. Companion documents carry derivation rather than
> specification — [`afa.md`](afa.md) for how the channel plan and the ladder's
> margins follow from the regulation, and [`psa.md`](psa.md) for access to the
> hailing channel, which SUPE does not own.
>
> A node speaking it is a SUPE RNode.

## 0. The flow

```
main channel (whatever this network hails on — the baseline for everything
              below; everyone camps here, carrier-sensed)

  telling the neighbourhood who we are — once per announce interval
    A→*   SUPE_ANNOUNCE2 {type, regime/version, capabilities,
                          power this went out at,
                          4-byte identity hash × count}           5+4·count B
          └─ hashes last, so the count needs no byte of its own
          └─ every regime announces here and identically

  A has traffic for B and no live schedule with it
    A→*   PRIVSYNC  {type, regime/version, tag,
                     power this went out at, salt, A's identity}     7 or 10 B
          └─ the salt is one random byte, and it is the seed's
             freshness: without it two identical requests derive the
             SAME schedule — the same channels in the same order
          └─ carrier-sensed like any frame; the ONLY thing this protocol
             ever puts on the shared channel twice-per-pair is nothing —
             this frame is it, once
          └─ nothing about the meeting is transmitted: both ends derive
             it from this frame's own bytes

derivation (both ends, independently — §7)
    epoch      = the end of the PRIVSYNC, as each radio timed that RF event
    slots      k = 0,1,2… : offset, channel, sync word from the frame's
               hash; slots alternate A,B,A,B… — A transmits in its slots,
               B in its own; A first, because A declared the traffic.
               Parity is fixed by role, never by the hash
    modulation = the hailing configuration, on the slot's channel
    horizon    = a few hundred ms, then the shared channel again

traffic channel (channel_k, sync_k — the transmitting side senses the
                 carrier first: the appointment grants the peer's
                 attention, never the spectrum)

    A→B   HAVEDATA  {type, 3 bytes of the seed hash, power,
                     budget ceiling, count, length}                       8 B
          └─ the sync word already filtered strangers in silicon; the
             hash bytes prove both ends derived the SAME schedule, and
             kill the residue
    B→A   GIMME     {type, 3 bytes of the seed hash, power,
                     budget (≤ the ceiling), how PRIVSYNC was heard,
                     how HAVEDATA was heard}                              10 B
          └─ attention confirmed before anything expensive flies
          └─ the PRIVSYNC reading is B's headroom at the hailing
             configuration — what the budget choice runs on; the
             HAVEDATA reading is a fresh pair at this channel's. Both
             power controllers eat for free
    A→B   the frames themselves × count — LoRa frames in the interface's
          ordinary framing, at the budget GIMME named, flip-gap apart.
          SUPE neither reads nor counts the Reticulum packets inside
          them: a split packet is simply two frames
    A→B   THATSIT   {type, power of the train just sent,
                     1-byte checksum × count}                         2+n B
          └─ no count byte: the frame's own length says n
          └─ the train's power was A's to adjust on GIMME's fresh
             reading, resolved at the budget's own modulation (§15);
             this byte is what keeps its levels pairable
          └─ frames carry no sequence numbers; the checksum list IS the
             sequence, and B aligns what it holds against it
    B→A   BYE       {type}                                                1 B
          └─ all n accounted for; the meeting is over
      -or- RESEND    {type, bitmask of the missing}                 1+⌈n/8⌉ B
          └─ ONE repair round: A resends exactly those frames, B answers
             BYE or gives up — what is still missing belongs to the
             layers above
      -or- HAVEDATA  {…, how the train was heard, resend bitmask}      ~11 B
          └─ B's turn: GIMME is skipped — A's attention is not in
             question — and the answering HAVEDATA carries both the
             train's reading and any repair request; A's resent frames
             ride its closing turn
            B→A  the return frames × count
            B→A  THATSIT
            A→B  BYE -or- RESEND

  the goodbye: the meeting's final THATSIT is the newest frame both ends
  demonstrably hold — the BYE that answers it proves the holding, and a
  lost BYE leaves both ends deriving from the same frame anyway. Its
  bytes seed the next schedule:

derivation, again (§7)
    the same arithmetic, wide: slots from epoch′+150 ms, widening and
    jittered, never further apart than ~350 ms, horizon 3 s
    slots alternate B,A,B,A… — B first, because whoever just RECEIVED
    is the likely replier
    modulation = what this meeting just flew; it worked seconds ago

  …and every meeting's goodbye reseeds the next schedule. A pair with
  steady traffic touches the shared channel once, ever.

the failure ladder (all of it private, all of it bounded)
    the listening side, nothing at a slot:  rx window = preamble + guard,
          stop-timer-on-preamble; silence → home, and NOTHING is scored
    the transmitting side, nothing queued:  nobody comes — means nothing
    the transmitting side, unanswered:      keep your slots while you
          hold traffic; thin attendance if you like. The horizon is the
          only give-up there is
    a narrow schedule expires unmet:         shared-channel evidence —
          the absence ladder's business (§12), because the seed flew
          on the shared channel
    a wide schedule expires unmet:          no evidence at all; the next
          data buys a PRIVSYNC, as it always could
    contact at any slot:                    consumes the schedule — every
          remaining slot is void, and the meeting's goodbye seeds afresh
```

**Nothing is negotiated and nothing is waited for.** The schedule is a pure
function of a frame both ends hold; a meeting either happens at a slot or it
does not, and either way both ends know everything the other knows. Deadlines
exist for misses alone. The reply that is born too late for this meeting — a
proof the daemon takes a few hundred milliseconds to produce — meets its peer
at the first slot of the next schedule, which is what the schedule is for.

**The private cost is bounded by construction.** A listener spends at most a
preamble-width per slot; a transmitter at most a short frame per owned slot;
everything expires at the horizon. Any feature that can break this — an
unbounded dwell, an unbounded retry, a missed slot scoring evidence — is
rejected on sight.

**The shared cost is unknowable, so the shared channel is the last resort, not
the baseline.** Nobody knows what a hailing transmission costs until they have
paid it — two carrier-senses against whatever the segment is doing, at whatever
band the contention ladder has climbed to — and part of the price is paid by
everyone else. This protocol's value grows with segment load, because that is
when the shared channel is dearest.

### 0.1 Every frame and its fields

Field order, widths and type values below are normative.

Every SUPE frame opens with its type byte, and that byte plus the regime and
version nibbles behind it (where carried) determine the frame's permitted
length exactly:

| Type | Frame | Sent on | Length |
|---|---|---|---|
| `0xC0`, `0xC1` | **never assigned** | | see below |
| `0xC2` | PRIVSYNC | main channel | 7, or 10 with `sender_ident` |
| `0xC3` | SUPE_ANNOUNCE2 | main channel | 5 + 4·count |
| `0xC4` | HAVEDATA | traffic channel | 8 opening; 10 + ⌈n/8⌉ answering |
| `0xC5` | GIMME | traffic channel | 10, or 8 on a wide schedule |
| `0xC6` | THATSIT | traffic channel | 2 + count |
| `0xC7` | BYE | traffic channel | 1 |
| `0xC8` | RESEND | traffic channel | 1 + ⌈count/8⌉ |
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
power.** A side's opening HAVEDATA or GIMME states its power; its THATSIT
states the power of the train it closes — which may differ, the train being
that side's chance to act on the reading its peer just reported, at the
budget's own modulation (§15) — and RESEND, BYE and a repair round's frames fly
at whatever their sender last stated. So any level measured on this channel
pairs with a known power, and the small frames stay small.

**Signal-to-noise is a separate signed byte in quarter-decibels**, covering
−32 dB to +31.75 dB. It is a ratio rather than a level, needs no offset, and
that is both the range and the resolution the silicon reports natively.

**The main channel carries only what it takes to seed a schedule.** Nothing
else belongs there. Budgets, counts, measurements and verdicts all move to a
channel that is faster and bothers only the two parties involved.

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **PRIVSYNC** | type | 1 | `0xC2` — `110` protocol bits then 5 bits of type (§3) |
| | regime / version | 1 | a nibble each — fixes how everything after it is read, and which channel raster the schedule draws from |
| | tag | 3 | first three bytes of the packet's first address field — an address the peer holds (§6) |
| | power | 1 | `dBm + 64`, signed — what this frame went out at, so the peer's own reading becomes a path loss immediately |
| | salt | 1 | random — the seed's freshness. The schedule is a pure function of this frame's bytes, and everything else here can repeat exactly: the same tag at the same power would derive the same channels in the same order, every attempt, and the diversity §7's stream promises would be a fiction. One byte gives 256 distinct schedules per otherwise-identical request, and makes a stale schedule and a fresh one differ in hash rather than only in epoch |
| | sender identity | 0 or 3 | first three bytes of the sender's own identity hash, under `sender_ident` (§4); presence is implicit in the frame length |
| | | **7 or 10** | |
| **SUPE_ANNOUNCE2** | type | 1 | `0xC3` |
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
keep trying to meet it for as long as its absence ladder allowed. So the
statement is a frame, and it is repeated on the ordinary announce interval —
a neighbour that boots later, or misses the one that announced the change, would
otherwise hold the wrong belief indefinitely.

It is not a dialect, so it is not checked against one: any version nibble
decodes. An unknown *real* regime is still discarded, because that one is a
claim about a dialect this node does not have.

A node in this state still **reads** everything. It ingests announcements and
keeps its picture of the neighbourhood current — who speaks the protocol, what
their radios can do, what the path loss to them is — so the switch can be thrown
back without waiting to rediscover anyone. What it stops doing is answering: no
hail is seeded, no slot is attended, no invitation is taken up.

**Capabilities** — two bytes, carried by SUPE_ANNOUNCE2:

| Field | Size | Meaning |
|---|---|---|
| family / ceiling | 1 | a nibble each: this node's radio family (§14.6), and the highest budget it will accept |
| maximum power | 1 | `dBm + 64`. The top bit is free — a transmit power never stores a negative value — and currently unassigned. It must never be spent on a level field |

**Traffic channel** — every frame below flies at a slot of the schedule (§7)
or inside a meeting one slot opened. The seed hash is the first three bytes of
SHA-256 over the seeding frame exactly as it was transmitted.

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **HAVEDATA** (opening) | type | 1 | `0xC4` |
| | seed hash | 3 | which schedule this meeting belongs to. A mismatch is somebody else's meeting, or a stale schedule — either way, discard and stay home |
| | power | 1 | `dBm + 64`, signed — what this frame, and every frame this side sends in this meeting, goes out at |
| | budget ceiling | 1 | the highest budget (§14.3) this side proposes for the trains. The ladder resolves against the slot's channel |
| | count | 1 | LoRa frames in the coming train |
| | length | 1 | how long the train takes — airtime plus the flip gaps of §14.7 — in 5 ms steps |
| | | **8** | |
| **GIMME** | type | 1 | `0xC5` |
| | seed hash | 3 | as above |
| | power | 1 | what this frame, and every frame this side sends in this meeting, goes out at |
| | budget | 1 | the budget the trains will fly at — the receiver's choice, never above the proposed ceiling and never above either node's capability ceiling |
| | PRIVSYNC heard | 2 | level (`dBm + 64`) and signal-to-noise (quarter-dB) of the PRIVSYNC — the headroom at the hailing configuration that the budget choice runs on. **Present only on a narrow schedule**; a wide schedule was seeded by a goodbye, and the pair is fresh from a meeting instead |
| | HAVEDATA heard | 2 | the same pair for the HAVEDATA just received, at the slot's own modulation |
| | | **10, or 8 wide** | |
| *the frames* | — | — | not SUPE frames. LoRa frames in the interface's ordinary framing — its checksum, its split handling — transmitted back to back, separated by the flip gap of §14.7. SUPE decides where and at what rate they fly, and touches nothing else about them |
| **THATSIT** | type | 1 | `0xC6` |
| | power | 1 | `dBm + 64`, signed — what the train it closes went out at, and what this frame and its sender's frames fly at until stated otherwise. Stated after the fact, because the train's power is chosen *after* the peer's report, at the confirmed budget's modulation (§15) — a power stated in advance would be a power chosen blind |
| | checksums | 1 × n | one byte per frame of the train just sent, in transmission order (§8). The count is the frame's own length minus two, and must equal the HAVEDATA's count |
| | | **2 + n** | |
| **BYE** | type | 1 | `0xC7` — every frame accounted for; the meeting is over |
| **RESEND** | type | 1 | `0xC8` |
| | bitmask | ⌈n/8⌉ | bit *i* set: frame *i* of the checksum list is missing. n is the count both sides hold |
| | | **1 + ⌈n/8⌉** | |
| **HAVEDATA** (answering) | … | 8 | the opening form's fields, then: |
| | train heard | 2 | level and signal-to-noise of the train just received — the worst frame's, since that is the one the power controller must clear |
| | resend bitmask | ⌈nₚ/8⌉ | over the *peer's* train just described by its THATSIT; all-zero when nothing is missing. nₚ is known to both sides, so the length is enumerable |
| | | **10 + ⌈nₚ/8⌉** | |

Three rules produce the shapes above, and they should hold for anything added
later:

- **Every frame has a length the receiver can enumerate** from its type and
  from state both sides already hold — one value for most, two for PRIVSYNC and
  GIMME, count-derived for SUPE_ANNOUNCE2, THATSIT, RESEND and the answering
  HAVEDATA. Nothing is signalled by a flags byte and nothing is negotiated, so
  anything outside the enumerated set is discarded (§3).
- **On the main channel, count frames, not bytes.** With no checksum, every
  payload up to the quantum in §3 costs the same. PRIVSYNC's seven bytes fill a
  symbol group at SF7 exactly; shaving below that buys nothing.
- **On the traffic channel, also count frames, not bytes.** A short frame there
  is preamble-dominated, so folding a field into a frame that is already going
  out is nearly free while adding a frame costs its preamble plus a turnaround.
  That is why measurements, budgets and repair requests all ride inside frames
  that exist anyway.

No SUPE frame carries a cyclic redundancy check (§3); the LoRa frames of a
train keep the interface's, being none of SUPE's business.

## 0.2 The words

Every term a node writes into a log is defined here, so that a line in the field
and a paragraph in this document are talking about the same thing.

| Term | Means |
|---|---|
| **hailing channel** | the shared channel the network already runs on, where every node listens by default |
| **hail** | a PRIVSYNC on the hailing channel: one short frame declaring that traffic is waiting |
| **meeting** | one exchange on an agile channel — attention, budget, trains, goodbye |
| **train** | the run of LoRa frames one side sends inside a meeting; the unit is a frame on the air, not a packet from above |
| **narrow schedule** | the slots a hail seeds: packed close together over a short horizon |
| **wide schedule** | the slots a goodbye seeds: spread thin over a long one, and widening as they go |
| **rendezvous** | a meeting reached through a wide schedule — an appointment kept, with no frame spent arranging it |
| **slot** | one derived appointment: a time, a channel and a sync word, belonging to one side to speak in |
| **horizon** | how long a schedule's slots run for before it is retired |

**Narrow and wide are named for how closely the slots are packed**, and the rest
follows from why each exists. A narrow schedule answers a hail, so the far end
is known to be listening and the only question is which slot lands: try hard,
try soon, and if the horizon runs out, hail again — that costs one short frame.
A wide schedule is a standing appointment for traffic that may never come, so
its slots start close and stretch apart, front-loaded because the likeliest
moment for more traffic is straight after the last exchange and less likely
with every second that passes.

Two differences follow from what each schedule can prove rather than from its
shape. A narrow schedule's slots fly at the **hailing configuration**, because
nothing has been agreed yet — the hail was one frame on a shared channel. A wide
schedule's fly at the **budget its seeding meeting confirmed**, because that
meeting is standing evidence that modulation works between these two. And in a
narrow schedule the seeker speaks first, having declared the traffic; in a wide
one the side that *received* the last train speaks first, being the likely
replier.

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
Our hail tx{10 -58 12} 3/500/5: tx{10 -50 12} rx{14 -45 10} - sent 5/5, rcvd 2/2
Their hail rx{14 -45 10} 3/500/5: rx{14 -45 10} tx{10 -50 12} - rcvd 2/2, sent 5/5
```

`hail` says a hail arranged it and the triple after it is that frame;
`rndv` says it was an appointment already held, which cost no frame and has no
levels of its own to report. A side that carried nothing is left off rather than
zeroed — no triple and no count, by the same rule as the `?` above, since `0/0`
reads as a measurement of something. Whatever went wrong is appended to the same
line: a failure is a property of the meeting, not a separate event.

## 1. The idea

One robust shared channel that every node camps on, carrying Reticulum
announces, path discovery and anything broadcast — and, per pair, a derived
schedule of private meeting places where unicast traffic actually flows. The
schedule is a pure function of a frame both ends already hold, so arranging a
meeting transmits nothing, and every meeting's goodbye seeds the next.

**No node identity appears on the air except where a node chooses to publish
it.** Meetings are seeded on a 3-byte prefix of a Reticulum address and nothing
else. The one place a node names itself is its own periodic announcement, which
exists to serve the capability table — and, optionally, in its PRIVSYNC, which
is what makes the return leg possible (§4).

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

**A node's identity hash is derivable and already shared.** Every Reticulum
announce carries the announcing identity's public key, so any receiver can
compute that node's identity hash without asking. All of a node's destinations
share it, and a transport node's identity hash is literally the address relayed
traffic is sent to. That makes it the natural key for a capability table — one
entry per node rather than one per destination — and it is why SUPE_ANNOUNCE2
publishes identity hashes rather than destination hashes.

## 3. Frames, regimes and versions

**Frames a stranger may have to judge carry a regime nibble and a version
nibble** in one byte — the two main-channel frames, per §0.1. The pair fixes
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
than speaking a stale dialect at a network that has moved on. There
are no migrations and no compatibility shims anywhere in this protocol — the
wire changes outright, and the expiry retires what came before.

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
identically by both ends (§7), and the budget bytes of HAVEDATA and GIMME are
resolved against it by both.

What a budget resolves to in absolute terms is still what governs the radio, so
the header mode and the family limits follow the *resulting* configuration
rather than the index. An entry landing on SF5 is unavailable on SX127x; the
same index on a slower network lands somewhere else entirely.

**The type byte is 3 bits of protocol and 5 bits of type.** The protocol bits
are `110`, so every SUPE frame begins `0xC0`–`0xDF` (§0.1). That range cannot
collide with Reticulum itself: SUPE requires an interface with no access code
(§13.1), and without one the daemon's flag byte never has its top bit set — the
access-code path is the only thing that sets it, and it sets it
unconditionally. So anything from `0x80` up is not Reticulum on a SUPE
interface.

**What the range must also dodge is the interface's own framing**, which sits
ahead of the Reticulum packet and is therefore what a receiver actually reads
first. That is a property of the interface rather than of Reticulum, and it is
why §0.1 concedes the four values ending in 0 or 1: restricting what *we*
transmit would do nothing about frames from a node that predates SUPE, whereas
choosing type values its framing cannot produce works against every vintage
without anyone agreeing to anything.

**A frame whose length is not one the regime, version and type allow is
discarded.** The permitted set is tiny — one length for most frames, two for
PRIVSYNC and GIMME, count-derived for the rest — so the test rejects outright
rather than merely suspecting, and it is the last cheap filter before we act on
anything.

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
- the turnaround and retune constants of §14.7, from which every deadline
  follows
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

- PRIVSYNC at seven bytes fills the second group exactly, and its ten-byte form
  under `sender_ident` fills the third — the salt byte rode into space the
  group boundary was giving away.
- SUPE_ANNOUNCE2 is the one main-channel frame whose length is not fixed, and
  the one not sized to a boundary: its `5 + 4·count` lands where the identity
  count puts it. One frame per interval per node is what that costs, which is
  why the interval is long and the count is every identity a node holds rather
  than one frame each.

Those figures assume preamble 8; the interface default of 12 adds about 4 ms to
each. Against roughly 760 ms for a full 500-byte Reticulum packet on the same
channel, the shared-channel cost of seeding a schedule is about one
twenty-fifth of a single packet — and a reseeded schedule costs nothing there
at all.

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
| `sender_ident` | on | Name ourselves in every PRIVSYNC. Costs three bytes and one symbol group, and gives up the protocol's default anonymity: a listener learns who is talking to whom, which no Reticulum header discloses. It buys two things, and the first is not optional in practice — **the return leg (§8) cannot exist without it**, since the tag a PRIVSYNC carries is the *other side's* address and says nothing about who is asking, so an unnamed seeker's queued traffic at the far end is indistinguishable from a stranger's and the answering HAVEDATA is never sent. It also lets the far end file what our cargo establishes — a link identifier above all — against us rather than against nobody (§11). Off restores the anonymity and gives both up; a node that turns it off still parses and honours the ten-byte frame from those that do not. |

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
| link identifiers we terminate | anything we transmit at hop count zero addressed to a link | an observed link close; otherwise keep-alive staleness |
| link identifiers we relay for | forwarding a frame addressed to a link — the return direction of a relayed link arrives addressed to the link identifier, not to our transport identity, so a relay that skips this sleeps through it and the link dies | the same |
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
| a transport identity | being one already — a relayed announce's first address field is the relaying node's identity hash |
| a destination hash | the Reticulum announce that carried that destination's public key, whose truncated hash is the identity (§2) |
| a link identifier | the destination the link request was addressed to, recorded as the request goes past |

Capabilities and measurements hang off the identity, never off the tag, so a
node with forty destinations costs one capability entry and forty pointers.
This is the table that answers "what budget" — §11 is what fills it.

## 6. The main channel: the tag and PRIVSYNC

**Tag** — 3 bytes, always the first three bytes of the first address field of
the packet about to be sent, and nothing more. It has one meaning: an address
the peer holds. There is no type, no flag and no second interpretation, because
the receiver has one flat table (§5), a hit is a hit, and what follows —
derive, attend, receive, hand up, let the daemon judge — never depends on why
the entry is there.

That includes delivery proofs, which are addressed to the truncated hash of the
packet being proved. Exactly two nodes hold that hash: the origin, in its
receipt, and the one relay that forwarded the packet, in its reverse table. A
packet in transport names its next hop, so no second relay ever handled it and
no third node ever wakes. The tag is as precise here as anywhere else.

---

**PRIVSYNC** — seeker to peer, overheard by all
*"I have traffic for whoever holds this address. You know where to find me."*

Six bytes, or nine where it names itself, and the only frame this protocol
sends on the shared channel per pair of nodes — the announcement aside. It is
a **seed, not a commitment**: nothing has been decided and nothing is reserved.
A seeker that meets nobody at any slot of the schedule it seeded simply
transmits on the main channel as it always would.

**It carries no load, no ceiling and no family.** All of that belonged on the
shared channel only while the answer had to come back there. Here the answer is
a derived schedule, attendance costs the peer one slot dwell (§7), and the
budget conversation happens at the meeting, where bytes are cheap and the
measurement that should drive it — how this very frame was heard — has just
been taken. The capability table (§5.1) supplies family and ceiling for the
ladder; a peer that never announced is not a SUPE peer at all (§13.1).

**It states its power**, unlike everything else about it, because that one byte
turns every listener's reading into a path loss immediately — the seed is also
a measurement, at the hailing configuration, of exactly the link the meeting is
about to use.

**Our own identity, only if `sender_ident` says so.** Without it a PRIVSYNC
names the peer and no one else, which is what keeps the protocol anonymous —
and what makes the return leg impossible, since the far end cannot recognise
its queued traffic as ours (§4). It is a deployment choice, and a gateway
should make it.

There is no discovery handshake and no cold start, because there is nothing to
discover. A schedule is only ever seeded toward a peer whose SUPE_ANNOUNCE2 has
been heard, which is what makes it a peer at all in SUPE's terms. Its address
and a signal measurement come from the Reticulum announce that built the path
(§2).

**Third parties see nothing, are owed nothing, and lose nothing.** No hold, no
reservation, no duration on the air. A node away at a meeting is briefly deaf,
as any busy node may be; whoever tries it meanwhile spends a short frame and
learns nothing false — trains require an answered HAVEDATA, so nothing
expensive can ever be aimed at an absentee, and a missed delivery signal from a
peer still being heard teaches the power controller nothing (§15). Who hears
whom is already chaotic on any real deployment; the layers above digest
illegible silence constantly, and every exchange a meeting carries is one that
never contended on the shared channel — which is more relief than any
reservation hint ever bought.

**What it costs and what it buys.** At SF7/BW125 a PRIVSYNC costs 31 ms of
shared channel, once, and every meeting it or its successors carry costs the
shared channel nothing. A pair with steady traffic converges on announces as
its entire shared-channel footprint. The arithmetic that used to weigh
per-packet overhead against per-packet savings collapses: the overhead is not
per packet, not per meeting, but per *conversation*.

## 7. The schedule

A schedule is a pure function of one frame both ends hold — the **seed** — and
of the roles the exchange around that frame fixed. Nothing about it is
transmitted. This section is normative; its constants are regime constants
(§3), and the values below are estimates awaiting
[`simulation.md`](simulation.md), like §14.7's.

**The seed and its stream.** The seed is the seeding frame's bytes exactly as
transmitted, and every seed is unique: PRIVSYNC carries a random salt for
exactly this (§0.1), and a THATSIT's checksums vary with the train it closed.
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

hash   = D_0[0..2]                    the 3 bytes HAVEDATA and GIMME quote

stream = D_0[3..31] ‖ D_1[0..31] ‖ D_2[0..31] ‖ …
         slot k consumes stream[3k], stream[3k+1], stream[3k+2]
         as j_k, c_k, s_k

t_0    = seed_gap                                       (narrow, §14.7)
       = 150 + (j_0 mod 40)                             (wide)
t_k    = t_(k-1) + 40 + (j_k mod 24)                    (narrow)
       = t_(k-1) + min(60 + 30·k, 350) + (j_k mod 40)   (wide)
         …measured from the epoch; slots exist while t_k ≤ horizon
         (400 narrow, 3000 wide)

chan_k = 1 + (c_k mod nChans)         regime 1; always channel 0 in regime 0
sync_k = W_sf[ s_k mod N_sf ]         §14.5's word list for the slot's
                                      spreading factor, ordered ascending

tx_k   = alternating from the first transmitter, fixed by ROLE (below)
```

The narrow schedule's spacing yields nine or ten slots inside its horizon, the
wide schedule's thirteen to fifteen inside its own. A conformance vector file
over seeds belongs beside the ladder's (§14.3.4) once these constants settle
(§16).

**Two seeds exist and they differ only in parameters:**

| | narrow schedule | wide schedule |
|---|---|---|
| seeded by | a PRIVSYNC on the main channel | a meeting's final THATSIT |
| epoch | the end of the seeding frame, as each radio timed that RF event | the same |
| first slot | one seed gap after the epoch | 150 ms + jitter after the epoch |
| horizon | 400 ms | 3 s |
| first transmitter | A — the seeker declared the traffic | B — whoever just received is the likely replier |
| slot modulation | the hailing configuration | the budget the seeding meeting confirmed |
| expiry, unmet | shared-channel evidence: one strike on the absence ladder (§12), and only while nothing is being heard from the peer | no evidence of anything |

**Why the seed is the final THATSIT and not the BYE.** The goodbye must be a
frame both ends can *prove* the other holds, or the two derive different
schedules and silently never meet. The BYE that answers a THATSIT proves the
answering side held it; and a BYE that is lost still leaves both ends holding
the same THATSIT — the seeker merely does not know the transfer concluded,
which the next meeting resolves. Seeding from the BYE itself would put the
schedule on the one frame whose loss is invisible.

**And it must be the meeting's LAST THATSIT, not merely one you hold.** An
answering HAVEDATA promises a return train, and therefore a later THATSIT that
will close the meeting — so the opener's own stops being the goodbye the moment
that promise arrives. A side that never receives the promised one is holding a
superseded frame, and seeding from it derives a schedule the peer never
derives: the same orphan as seeding from an unproven one, reached from the
other end. Both losses are one dropped frame, and both cost a whole horizon.

**Holding the goodbye is not proof; only these two things are.** A side may
seed from a THATSIT when it *received* it — the peer sent it, so the peer holds
it — or when something of the peer's *answered* it: a BYE, a RESEND, or an
answering HAVEDATA. A THATSIT sent into silence proves nothing, and seeding on
one is worse than seeding nothing: the sender derives a schedule its peer has
never heard of, then spends the entire horizon transmitting at slots nobody is
attending before it falls back to the shared channel. One lost frame becomes a
dead conversation that way — the sender concludes the peer is absent while the
peer sits idle and reachable. **Unproven, both ends agree on nothing**, which
is the recoverable state: the next traffic seeds a PRIVSYNC at once, and the
asymmetry never exists.

**The derivation's two anchors are outside the stream, deliberately:**

- **The epoch is a shared RF event, not a clock reading** — the end of the
  seeding frame, timed independently by each radio to sub-symbol accuracy. So
  clock error accumulates only across the silence since it: under 1 ms even on
  an RC oscillator across the full 3 s horizon, which the slot window's guard
  absorbs.
- **The transmitter parity is fixed by role and never by the stream.** If
  parity could derive from a disagreed seed, both parties would transmit at
  each other and nothing would detect it. A disagreed seed under fixed parity
  just means empty slots, which the horizon already handles.

**A frame arriving outranks every slot.** Attending one retunes the radio, and
a retune part-way through a preamble destroys the reception in progress — so a
node holding schedules would abandon real frames on the channel it is camped on
for slots that may well be empty. The frame in the air is certain and the slot
is speculative; the slot's own lateness tolerance absorbs the few milliseconds
of deferral, a missed slot means nothing (§12), and a missed PRIVSYNC means the
peer cannot reach this node at all. That asymmetry is the whole argument, and it
matters most exactly when it is least convenient: a node whose schedules have
gone out of step with its peer's is both attending the most slots and the one
the peer is trying hardest to reach on the shared channel.

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
asymmetry above settles which way that trade runs. A missed slot means nothing;
a missed hail means the peer cannot reach this node at all, and no partly-held
frame is worth buying at that price.

The only state that stands the engine down rather than deferring within it is a
transmission of this node's own still going out, because that is the one case
where servicing a due slot would retune the chip out from beneath a frame
already on the air.

**A hail outranks a rendezvous.** When two schedules want the same moment, the
one seeded by a hail is served first. It was bought a moment ago with a frame on
the shared channel by a party that has traffic in hand and said so, and it lasts
a few hundred milliseconds; a rendezvous is a standing appointment left by a
meeting already finished, lasting seconds, and quite possibly empty at both
ends. Served in an arbitrary order the rendezvous takes half of the contested
moments, and each of its retunes lands the node late for the appointment that
had something behind it — so a peer hails, is not answered, and concludes the
node is gone while the node is busy keeping an empty engagement.

**An orphaned schedule is abandoned, not spent.** The last frame of a goodbye
cannot itself be acknowledged, so the two ends can never be made to agree about
it in every case: whichever way the rule is written, some single lost frame
leaves one side holding a schedule the other never derived. That is not a defect
to be designed away but a property of the exchange, and the answer is to make it
cheap rather than impossible. A schedule that has spoken in its own slots and
been answered in none of them is being attended by nobody — a data announcement
is a question the far end owes an answer to, not a broadcast — and after a
couple of those the schedule is dropped and the traffic takes the shared channel
instead. A whole horizon spent speaking into silence buys nothing that a hail
would not have bought at the start.

**A partial frame outlives its carrier by nothing.** A meeting hands over
everything it collected in one delivery, once. A fragment still waiting for its
partner after that delivery is waiting for something the meeting already failed
to bring — the repair round is over and the frame that would complete it is not
coming. It is discarded there, not left to a reassembly timeout, because a
fragment held past the point where it can complete is state every later decision
has to reason around for no possible gain.

**One narrow schedule per peer.** A hail retried is a hail whose schedule went
unanswered; that schedule is dead to both ends, and a node that keeps it holds
appointments nobody will attend. Two live sets for one peer is worse than one
wasted set, because the two interleave: every retune for one arrives late for
the other, and a node can spend a whole horizon late for both. Both ends derive
this from the same frame, so both retire the same schedule. A goodbye's wide
schedule is untouched — a rendezvous from a closed meeting is legitimately live
beside a fresh hail.

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
wide schedule flies at. The transmitting side attends only when it holds
traffic, gives its slot up once it is `slot_lateness` past the moment, senses
the carrier first — the appointment grants the peer's
attention, never the spectrum; a busy channel means this slot is skipped, which
costs and means nothing — and opens with HAVEDATA.

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
the noise. Attendance is optional on
both sides at every slot. A busy node thins its attendance and loses nothing
but latency; a node with earlier commitments — its own hailing receive above
all — ranks them above any slot.

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
where a node without SUPE always is: on the shared channel. The one silence
that is evidence is a *narrow* schedule expiring unmet, because its seed flew on
the shared channel and was itself carrier-sensed: that is the absence ladder's
input (§12). A wide schedule expiring unmet is an ordinary end of conversation
and teaches nothing.

## 8. The meeting

Everything on the traffic channel happens inside a meeting that one slot
opened, between the two parties the schedule belongs to. The transmitting side
of the slot speaks first, and the first exchange is short in both directions —
attention is confirmed before anything expensive flies.

**HAVEDATA** — the slot's transmitter, opening
*"It's me, this schedule; my train is this many frames and this long, and I
propose we fly at this budget."*

Its seed-hash bytes do double duty. The sync word already filtered other
networks and other pairs in silicon (§14.5); the hash bytes kill the residual
2⁻⁸ and — more importantly — prove both ends derived the same schedule from the
same seed. A stale or disagreed seed fails here, in one frame, instead of
producing a meeting where each party believes a different thing.

**GIMME** — the listener's answer, a turnaround later
*"Heard you; fly at this budget. Here is how your PRIVSYNC reached me, and how
that HAVEDATA just did."*

The budget is the listener's choice, never above the proposed ceiling and never
above either node's capability ceiling, resolved against the slot's channel
(§14.3). The listener chooses because the listener is the one about to receive:
it holds its own noise floor, its own airtime ledger for this channel, and the
freshest possible reading of the link — the HAVEDATA it just demodulated. On a
narrow schedule its PRIVSYNC reading is the headroom-above-hailing that the
budget choice classically runs on; on a wide schedule the previous meeting's
measurements are seconds old and the HAVEDATA pair refreshes them.

GIMME also commits the listener's memory: a train it accepts is a train it must
hold to the end (below), so answering is a resource decision, and the count and
length just proposed are what it decided on.

**The train** — the opener's frames, back to back at the confirmed budget,
separated by the flip gap of §14.7. These are not SUPE frames: they are LoRa
frames in the interface's ordinary framing, checksum and all. SUPE chose where
and how fast; it does not touch what travels.

Its power is the opener's to resolve fresh, and the GIMME it just heard is
what it resolves on: the youngest measurement in the whole protocol, taken on
this very channel, one turnaround ago, of the opener's own transmission.
Against it stands the confirmed budget's margin cost (§14.3) — the train
flies at a different modulation than the frame that was measured, and §15's
rule that power resolves per configuration applies inside a meeting as much as
anywhere. A reported surplus is spent, a reported deficit is covered, and the
receiver learns what was chosen one frame later.

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
  over; both retune home; the THATSIT just answered is the next schedule's
  seed.
- **RESEND** — a bitmask over the checksum list. **One repair round**: the
  opener resends exactly the frames named, in order, and the receiver answers
  BYE either way — a BYE after an incomplete repair closes the meeting for
  whatever arrived, which costs one byte and spares the opener a silence
  deadline. What a single round cannot save belongs to the layers above, which
  have retry machinery of their own and better context for using it. A repair
  round's frames are the cheapest retransmission this stack can make — no
  contention, no renegotiation, at a rate the link just demonstrated.
- **HAVEDATA, answering** — the receiver has traffic of its own. GIMME is
  skipped — the opener's attention is not in question — and the answering form
  carries what GIMME would have measured (how the train was heard, worst frame)
  plus the repair bitmask, all-zero when nothing is missing. The return train
  follows, then its own THATSIT; the opener's resent frames, if any were
  requested, ride ahead of its closing BYE or RESEND.

**Delivery is whole, in sequence, at the close.** Frames must reach the daemon
in the order they were sent — the interface's split reassembly depends on
adjacency — so the receiving side hands nothing up while the meeting runs: the
train is held, the repair round fills what it can, and the stack goes up in
order when the meeting closes, holes surrendered to the layers above. The
consequence is a hard bound: **a train is a RAM commitment**, and the count and
length ceilings are memory figures as much as airtime ones, sized to the
smallest board that speaks the protocol.

**Ends are arithmetic, not agreement.** Every deadline inside a meeting —
GIMME after HAVEDATA, the train after GIMME, THATSIT after the train, the
answer after THATSIT — follows from the constants of §14.7 and lengths already
exchanged, computable identically by both sides. A deadline missed means go
home; nothing is renegotiated on a channel where neither side can hear anyone
else, and the schedule ahead is the retry.

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

**SUPE_ANNOUNCE2** — us to everyone, once per announce interval, on the main
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

**Every SUPE node files every ANNOUNCE2 it hears**, and it costs nothing to:
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
alone: `announce_interval` is the gap between a node's SUPE_ANNOUNCE2 frames
and governs nothing a Reticulum announce does.

## 11. What is learned, and where it is filed

Everything below is learned from traffic that was going to happen anyway.
Nothing is measured on purpose, and nothing is transmitted in order to measure.

An announcement from a node nothing else has named yet is still worth keeping.
It carries four bytes of each identity and the capabilities together, which is
enough to file against, and the node's own Reticulum announce supplies the rest
whenever it comes. Discarding it instead costs a whole announce interval before
that peer can be met at all — the frame is the introduction.

Against the identity hash, once its SUPE_ANNOUNCE2 has been heard:
capabilities and maximum transmit power. That entry serves every destination
the node owns and, if it is a transport node, all traffic relayed through it —
its identity hash is the address that traffic is sent to.

Against the tag: path loss, never a bare signal level. Every reading in this
protocol pairs a level measured on one side with a power stated by the other,
so every exchange yields measurements rather than impressions:

| Reading | Taken by | From | At |
|---|---|---|---|
| A → B pair | B | its reading of the PRIVSYNC, against the power it stated | the hailing configuration |
| A → B pair | B | its reading of the HAVEDATA, against the power it stated | the slot's modulation |
| A → B pair | B | its reading of A's train, against the power A's THATSIT states one frame later | the budget's modulation |
| B → A pair | A | its reading of the GIMME, against the power it stated — and of B's train against B's THATSIT, where a return leg flew | both |
| A → B report | A | GIMME's account of how the PRIVSYNC and the HAVEDATA were heard — the only measurements of the direction A actually transmits in | both configurations |
| B → A report | B | the answering HAVEDATA's account of how the train was heard | the meeting's modulation |

Nothing in that table is a reciprocity assumption. Every reading is taken by
the node that will use it, and the reported ones measure the direction the
reporter's peer transmits in — which no transmitter can measure for itself.

Two bindings that save a slow first meeting:

- **Links inherit.** When the traffic being carried is a link request, both
  sides compute the same link identifier independently and file everything
  under it, so all later traffic on that link opens at the peer's best budget.

  The dialled side has to file it against the *node* as well, and PRIVSYNC's
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

- **A narrow schedule expires unmet** — the peer is busy, deaf, or gone, and the
  three are indistinguishable. This is the one silence that scores, because its
  seed was carrier-sensed onto the shared channel and its slots gave the peer
  several hundred milliseconds of chances: one strike. Silence is answered by
  seeding again — a fresh PRIVSYNC, so a fresh schedule — after a short
  randomised interval, long enough to outlast somebody else's meeting, which is
  the likeliest cause.

  **Power up.** Each successive PRIVSYNC goes out at more power than the last,
  the third at the configured maximum (§15). It covers the one failure the
  seeker can fix from its own side: a peer that simply did not hear the seed.
  There is no ceiling to walk down — the budget conversation moved to the
  meeting, where it is informed by measurement instead of by guessing at
  silence.

- **A node still being heard is never absent.** Whatever went wrong with an
  appointment, frames arriving from that node refute the only claim the ladder
  makes. A missed schedule earns its own backoff and nothing more. Skipping this
  test scores contention as absence — two schedules colliding, the loser
  reported as a peer that has gone — and blacklists a party in mid-conversation.

- **The absence ladder, in full.** On a peer with no absence record: seed, wait,
  seed at more power, wait, seed at maximum. Three narrow schedules expiring
  unmet while nothing at all is heard make the peer **absent**, and traffic
  addressed to it is **dropped** rather than transmitted into the void — safe,
  because link data, channel traffic, resource parts and proofs all have retry
  or receipt machinery above (§13). Three seeds is under 110 ms of shared
  channel, a seventh of what one full packet would have cost.

  **The hold starts small and grows.** Declaring absence is a bet that trying
  again is not worth the airtime, and a meeting now costs one short frame, so
  the bet is a modest one made in stages: about a second at first, doubling with
  each further unmet appointment, to a ceiling of a minute. A full minute
  imposed on the first finding is a blackout that outlives the conversation that
  provoked it — the next attempt then falls inside it too, and a fault that
  would have cleared on its own instead looks permanent to anyone retrying.

  **A peer that already has an absence record gets one seed, not three.** The
  ladder exists to establish absence, not to re-establish it; once established,
  each subsequent attempt is a single PRIVSYNC at maximum power, and silence
  lengthens the hold towards its ceiling. The cost of a peer that is genuinely
  gone falls to one short frame a minute.

- **Absence is cancelled by any evidence of life, not by the clock alone.**
  Hearing the node at all clears the record immediately and restores the full
  ladder: its Reticulum announce, its SUPE_ANNOUNCE2, a frame of its meeting
  with somebody else, or a packet from it we carry or relay. Talking to a third
  party is as good as talking to us — better, since it proves both presence and
  a working radio. This is what keeps a busy transport node, which is away at
  other people's meetings constantly, from being written off by the one
  mechanism that would hurt most.

- **A wide schedule expiring unmet is not a failure and scores nothing.** It is
  the ordinary end of a conversation. The next traffic for that peer seeds a
  narrow schedule on the shared channel, as any traffic always could.

- **A missed slot inside a live schedule scores nothing either**, in any
  direction. The peer may be mid-frame on the shared channel — a single
  500-byte packet there spans several early slots — busy at another meeting, or
  simply not listening, and every one of those is an ordinary state. The
  transmitting side keeps its slots for as long as it holds traffic, thinning
  attendance as it likes; the horizon is the only give-up there is. Only the
  shared channel may declare a peer absent.

- **A meeting that dies mid-way** — a deadline missed after GIMME — ends in
  both parties going home, holding what they hold. The receiver delivers the
  in-sequence prefix it can prove (§8) and surrenders the rest; the sender
  learns the meeting failed and the wide schedule that its last *completed*
  exchange seeded still stands. The channel it died on is the listening side's
  to remember: it chose it — its ledger, its noise — and it should not draw it
  again for that peer soon (§17).

- **Two seekers can want the same tag at once**, and on a segment with one
  transport node they usually do — every neighbour's traffic is tagged with
  that node's identity. Their schedules are seeded by different frames and land
  on different slots, channels and sync words, so they do not collide with each
  other; they compete only for the peer's attendance, and a peer attends what
  it can. The seeker whose slots go unattended is looking at an ordinary busy
  peer, not at absence — its narrow schedule's expiry still scores its strike,
  which is why the strike threshold is three and the interval between seeds is
  randomised.

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
  traffic for the duration. Slot dwells are milliseconds; meetings are bounded
  by the regime's ceilings; announces repeat and path requests are retried, so
  this is noise against announce cadence. §18's worker radios remove it
  entirely where a second radio exists.

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
tears links down after a handful of tries. A frame held through a schedule
wait, a meeting and a repair round must still fit inside that budget. Two
things keep it fit: a meeting is bounded by the regime's transaction ceiling
(§14), and the schedule bounds the wait ahead of one — no two slots further
apart than the spacing cap, no schedule longer than its horizon. The bound
holds only if a meeting at its ceiling plus a horizon's wait fits the daemon's
budget at the measured constants of §14.7, which is not yet settled; if it does
not, the declared-bitrate route above is the remaining answer.

### 13.1 Where it does not apply

- **Interfaces with an access code configured.** The frame is masked end to end
  — flags, hops, addresses and context byte alike — so the modem cannot read an
  address and has nothing to seed on. SUPE degrades to plain main-channel
  operation.
- **Peers that have not announced themselves.** A node becomes a SUPE peer by
  its SUPE_ANNOUNCE2 being heard, and nothing else. Traffic to anything else
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
| ladder | the spreading factors above the hailing one, at the hailing bandwidth |
| budgets available | to the lowest spreading factor both radio families reach — from an SF7 network, two where both are SX126x and **none where either is SX127x**, whose first entry would be the barred SF6; slower-hailing networks reach further before that bites (§14.3) |
| sync words | the schedule's derived word (§14.5); the interface's own word never appears at a meeting |
| train ceiling | none of its own — see below |
| transaction ceiling | none of its own — see below |
| transmit power | the interface's `tx_power` |
| airtime accounting | none |

**Regime 0 states no ceilings of its own, and must not invent any.** It has no
band plan and therefore no regulatory basis to draw them from — it runs on the
hailing channel, whose limits belong to whatever regime that network is
operating under, and which SUPE does not own (§3). What still bounds a meeting
is arithmetic rather than regulation: HAVEDATA's length byte reaches 1.275 s at
5 ms a step, and that is the real ceiling here, from the field width.

**A regime-0 meeting at budget 0 is a working meeting.** It flies at the
hailing modulation on the hailing frequency, under a different sync word —
which sounds like no gain at all, and is exactly the case of a link with no
headroom: the pair exchanges trains, the return leg, the measurements and the
repair round at the only rate the link supports, without contending for the
shared channel or occupying its listeners. The rate was never the only point.

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
power field in every frame it sends from there states 14 (§0.1). This is not a
detail: a node that states its configured power while transmitting at the
capped one hands its peer a path-loss figure wrong by exactly the difference,
in the direction that makes the link look better than it is, and the peer then
chooses a budget and a power on that basis.

Two further consequences worth naming. The 100 ms minimum gap before returning
to a frequency is part of the transmitting side's carrier-sense duty at a slot:
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

#### 14.3.3 What an index means

The budget bytes of HAVEDATA and GIMME index the ordered ladder above. Index 0
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
a budget choice; [`afa.md`](afa.md) §3 carries their derivation.

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
  aborted train, a HAVEDATA nobody heard, a frame cut short. The regulation
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

**Both sides read this table at every slot.** The transmitting side's
carrier-sense duty includes its own ledger — a slot whose channel has no budget
left is skipped like a busy one — and the listening side's GIMME is a budget
decision too: the trains it invites will spend its ledger, at the budget it
names.

### 14.5 Sync words

The hailing channel keeps the interface's own `sync_word`, `0x42` by convention
— that channel belongs to the Reticulum network, not to SUPE (§3). Every
meeting flies under a **derived word**: the slot's stream byte indexes a word
list both ends construct identically, per spreading factor, from the rules
below. The word is the first filter a meeting has, and it runs in silicon — a
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
and everything after GIMME indexes the list built for the confirmed budget's —
the same byte, re-indexed, since a word admissible at one spreading factor can
be out of range at another. No distance is kept between the list's own words: a
cross-detection between two of our meetings just wakes a receiver whose
seed-hash check kills the frame — the 2⁻⁸ residue is cheaper than the entropy
that separating them would spend.

**Between our meetings and the hailing channel**, the word is the whole
separation in regime 0, where both occupy one frequency; the seed-hash bytes
back it, and the length test backs those. Remember that the two-byte form on
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
| 4 | LR2021 | LR2021 | 5–12 | as SX126x for these purposes |

Both rules that follow are the **budget chooser's** to apply — the GIMME
sender's:

- **Every entry in the ladder must be reachable by both**, which §14.3.1 states
  as a membership rule rather than as a check afterwards. A pair of SX126x
  nodes on an SF7 network has eight entries above 0 on a 500 kHz channel; a
  pair including an SX127x has two, and under regime 0 none.
- **SF6 is not offered where either side is family 1.** It demands an implicit
  header there, and no framing in this protocol supplies the fixed length that
  would need. §16 carries the exception that would recover it.

The nibble holds sixteen values against five families, which is room enough
that a new part gets a number rather than a compatibility rule.

### 14.7 Turnaround, retune and deadlines

Every deadline in this protocol is derived from the constants below and a time
on air. They are regime constants (§3) and all of them are currently estimates
rather than measurements — the open item that most wants
[`simulation.md`](simulation.md), because a bench measurement of them needs
instrumentation we do not have and a simulator charges them by construction.

| Constant | Value | What it is |
|---|---|---|
| turnaround | 25 ms | The longest a node may take between the end of a frame it is answering and the start of its answer, on the same channel and configuration. It is what a GIMME must beat after a HAVEDATA, and what the answer must beat after a THATSIT. |
| seed gap | 100 ms | The epoch to the first slot of a narrow schedule. Not the turnaround: that measures a node already on the channel with the exchange in hand, while this one measures a node that has just demodulated a frame and must derive a schedule from it, retune to a channel that frame chose, and have its receiver open — all before the far end starts speaking. It is sized to the **slower** of the two ends, because being inside a slot's window is not the same as hearing it: a receiver opened part-way through a frame has missed the preamble and hears nothing at all, however strong the signal. Set to a turnaround, it produced a pair that met only when the speaker happened to be later than the listener — frequent enough to look correct, and failing whenever either end was briefly busy. |
| retune gap | 1 ms | The interval both sides observe between the last frame at one configuration and the first at another — arriving at a slot, and stepping to the confirmed budget after GIMME. It exists to cover the synthesizer, not the software. |
| train gap | 2 ms | The receiver-flip interval before and between the frames of a train: RX-done must be serviced, the frame read out and dispatched, and receive re-armed before the next preamble flies. Counted into every stated train length, so the far end's deadline covers it. Nothing else rides in it — the sender's next frame is already built. |
| guard | 10 ms | Slack added to every derived deadline *inside a meeting*, absorbing scheduling jitter at both ends. There the task is hot: the previous frame's completion is what drives the next step. |
| slot guard | 40 ms | Each edge of a slot's listening window. A slot is reached from an idle task — a timer fires, the task wakes, retunes, senses, builds — and on hardware that path puts a slot's opening frame 16–20 ms past its nominal moment. The window is sized to that slop and **not to the preamble**: a preamble is 16.6 ms at a hailing SF7/125k and 2.1 ms at SF5/500k, so a window sized to it is generous exactly where the schedule is slow and shut before the speaker transmits exactly where it is fast. It also dominates clock drift across a schedule's whole horizon (§7). |
| slot lateness | 20 ms | How late the transmitting side may open a slot before giving it up. Well inside the listener's tail, so a speaker that still tries is a speaker the listener is still hearing. |

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

The deadlines that follow, all derived and none transmitted:

| Waiting for | Armed at | Deadline |
|---|---|---|
| a slot, listening side | the slot's offset | the window `[offset − guard, offset + guard + toa(preamble)]`, its timeout stopped by a detected preamble. Silence at the window's end is a slot unmet, which means nothing (§12) |
| GIMME | end of the opening HAVEDATA | `turnaround + toa(GIMME, slot) + guard` |
| the train's first frame | end of GIMME | `retune_gap + turnaround + guard` |
| the train | its first frame | the HAVEDATA's stated `length`, plus `guard` |
| THATSIT | end of the train's last frame | `turnaround + toa(THATSIT, n, budget) + guard` |
| the answer — BYE, RESEND or answering HAVEDATA | end of THATSIT | `turnaround + toa(answering HAVEDATA, budget) + guard` — sized to the largest of the three, since which arrives is the answer itself |
| a repair round's frames | the RESEND that asked | the resent frames' airtime plus gaps, computable from the bitmask, plus `guard` |

Every one of them is computable by both sides from the frames already
exchanged and the schedule already derived, which is what lets a failure be a
silent return rather than a negotiation.

## 15. Adaptive transmit power

Part of SUPE, gated on the `adaptive_txpower` option (§4), and applying to
unicast only:
PRIVSYNC, every frame of a meeting, and any packet sent plainly to a single
peer. Never to anything broadcast — announces and path requests.

**Every frame this protocol aims at one node is adapted, because every frame it
sends is aimed at one node.** Nothing here needs third-party reach: no frame
carries a hold, a reservation or a hint for anyone but its addressee (§6), so
there is no frame that must go out at maximum for somebody else's sake. The
schedule's slots are attended by exactly one listener, and the shared channel's
one SUPE frame beyond the announcement — PRIVSYNC — is addressed to the one
node whose measurements the controller holds.

**PRIVSYNC is the natural power probe.** §12's absence ladder is that probe run
to conclusion: each seed at more power, the last at maximum. A peer whose
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
  6 dB; a peer that has gone absent goes straight back to maximum. Being wrong
  downward costs connectivity, so recovery is immediate and large.
- **Success lowers it slowly**, 1–2 dB, so any overshoot past the cliff is
  small and the next failure recovers it.
- **The decrement is gated on evidence, not time.** Require a number of
  successful exchanges with *that* peer since the last change. "Nothing went
  wrong lately" means nothing if nothing was sent, and a controller that dials
  down on a timer will walk a quiet link into the ground.
- **Where it broke is remembered.** After a failure at a given power, do not
  return below it plus a margin for a while, on a decaying floor. Without that
  the loop oscillates across the cliff instead of settling above it.

**The train's power is resolved where the train flies, on the report just
received.** GIMME's HAVEDATA reading is milliseconds old and measures the
opener's own transmission on the very channel the train is about to use;
the confirmed budget's margin cost (§14.3) is the known step between the
modulation that was measured and the one about to fly. The adjustment is a
relative correction from a fresh measurement — the one kind of arithmetic this
section permits — clamped by the maximum and remembered by the floor like any
other move, and THATSIT states the result so the peer's pairing stays true.
The same applies to the answering side's train against the readings it was
handed.

**A meeting is a measurement machine, and its closing frames are the loop's
acknowledgements.** GIMME reports how the PRIVSYNC and the HAVEDATA were heard
— the direction the opener transmits in, which no transmitter can measure for
itself. The answering HAVEDATA reports the train the same way. And the closing
BYE or RESEND, though it carries no reading, is the proof the train's sender
needs: its arrival says the train and its THATSIT landed, and a RESEND's
bitmask says precisely how well. A repair bitmask growing dense at a given
power is the cliff announcing itself before anything is lost outright.

**What is evidence and what is not:**

- **The closing frame comes back, the bitmask sparse or empty** — the power
  suffices and the GIMME/HAVEDATA readings say how much room there is.
  Sustained, this walks the offset up.
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
- **A narrow schedule expires unmet** — one strike of §12's ladder, read as a
  power measurement exactly when a later, louder seed is attended: the seed
  power that drew attendance bounds the cliff from above, the ones that did not
  bound it from below. Nothing else varies between the attempts, which makes it
  the cleanest reading the controller ever gets.
- **A missed slot, a wide schedule expiring, a delivery signal that never came
  from a peer still being heard** — nothing. A peer whose frames still arrive
  is reachable, whatever else went wrong; congestion, a busy far end and a
  stalled transfer are all made worse by transmitting harder, and none of them
  is about power.

## 16. Open items

- **The §14.7 and §7 constants, measured rather than assumed** — the
  turnaround, the retune gap, the train gap, the guard, and the schedule's
  spacings, jitters and horizons. They set every deadline and every slot, and
  with them how cheap a failed attempt is. First thing
  [`simulation.md`](simulation.md) should be pointed at, and the schedule
  derivation wants a conformance vector file the way the ladder has one
  (§14.3.4).
- **A downgraded budget against the train ceiling.** HAVEDATA sizes its count
  and length at the *proposed* budget; a GIMME that confirms a lower one
  stretches the same count by up to a ladder span, which can carry the train
  past the regime's train ceiling. The receiver holds every input to see this
  coming, so the rule probably belongs to it — confirm no budget whose implied
  train exceeds the ceiling, or cap the count in the same breath (the count
  byte the RAM item below would add carries that too) — but which, and how the
  opener's deadline reads it, is not yet fixed.
- **The checksum function.** CRC-8 polynomial 0x07 is specified provisionally
  (§8); what it needs is to be identical at both ends, cheap per frame, and no
  worse than 2⁻⁸ on the alignment match. Confirm the choice before first
  implementation, since it cannot change inside a version.
- **The count ceiling as the receiver's byte.** A train is a RAM commitment the
  GIMME sender makes (§8); a small board may want to cap the count below the
  proposal, and a count byte beside GIMME's budget would carry that. Named here
  so it is a considered change rather than a rediscovered gap.
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
  slot it owns.
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
  meeting. The answering HAVEDATA's budget bytes are where it would go.

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

**Whether to seed is one function, in one place.** Its inputs are the peer, the
queue and the channel; its output is `no`, `now`, or `wait until t`. The
protocol deliberately does not specify it, because the right answer varies with
traffic shape and nobody has measured it yet. What the protocol does require is
that the decision be *findable*: a policy scattered across a transmit path
cannot be measured, cannot be replaced, and cannot be simulated.
[`simulation.md`](simulation.md) §7 is written against exactly that signature.

**The train buffer is provisioned, not assumed.** Delivery at the close of a
meeting (§8) means a receiver holds a whole train in memory — count times the
interface's frame size, per concurrent meeting, of which there is one. The
count ceiling a node advertises and the GIMMEs it answers are promises against
that buffer, made before the train flies, never discovered against the
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
