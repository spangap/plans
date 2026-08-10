# SUPE — Spectrum Utilization and Performance Enhancements

> Status: **specified and implemented.** Bidirectional detours complete end to
> end on real hardware; the pure core, the state machine and the ladder are
> host-tested, and `supe-ladder-vectors.txt` is the §14.3.4 conformance
> authority.
>
> The timing constants of §14.7 — the turnaround, the manifest lead, the train
> gap — and the SF5/SF6 sync-word behaviour of §16 are still estimates rather
> than measurements, and every deadline here depends on the first.
> [`simulation.md`](simulation.md) is where they are meant to be settled.
>
> `sender_ident` ships (§4). It is what makes the reverse leg possible at all,
> and it is the one thing here that gives up sender anonymity, so it is a key
> and not a constant.
>
> SUPE moves unicast traffic off the shared LoRa channel onto short private
> high-rate detours, entirely inside the modem, with the Reticulum daemon
> unmodified and unaware. It specifies how two nodes that know nothing about each
> other agree parameters and leave, using only what is visible in Reticulum packet
> headers.
>
> Everything normative is here: the frames in §0.1, the regimes with their
> channels, ladder, sync words, timings and limits in §14, and adaptive power in
> §15. Companion documents carry derivation rather than specification —
> [`afa.md`](afa.md) for how the channel plan and the ladder's margins follow
> from the regulation, and [`psa.md`](psa.md) for access to the hailing channel,
> which SUPE does not own.
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

  arranging a detour — two frames, one carrier-sense between them
    A→*   SUPE_START  {type, regime/version, tag,
                       A's family and ceiling, A's load}                  7 B
          └─ "I have traffic for whoever holds this address, this much of it,
              and this is what my radio can do"
          └─ load is in bytes, not milliseconds: A does not know the
             modulation yet, and B is about to choose it
          └─ carrier-sensed like any other transmission

    B→*   SUPE_GRANT  {type, regime/version, channel/budget, duration,
                       power of this frame + the reverse flag,
                       how the START was heard,
                       3 bytes of the START's hash}                      10 B
          └─ "meet me on that channel at that budget, this is how long the
              whole thing takes — and whether anything is coming back"
          └─ the reverse flag: B holds traffic for the requester, so a
             reverse MANIFEST will exist. It answers the one question no
             count field can: whether to expect a frame at all
          └─ a turnaround response inside the transaction: no carrier sense,
             no contention wait
          └─ everyone else: hold traffic addressed to tag for that duration
          └─ nothing on the air when the GRANT was due to BEGIN → nobody
             answered. The START goes out again inside this same request; if
             that draws silence too, two more tries with more power and a lower
             ceiling, then the peer is absent and its traffic is dropped for a
             minute (§11)

        both retune, observing the retune gap of §14.7; the requester adds
        the manifest lead, so the answerer's receiver is armed first

unicast channel (the channel SUPE_GRANT named; in regime 0, the hailing
                 frequency at a faster modulation)

    A→B   MANIFEST  {type, power of this train, how the GRANT was heard,
                     capabilities, count, length, the START hash}        11 B
    A→B   the packets themselves × count — ordinary Reticulum frames in the
          interface's ordinary framing, not SUPE frames at all, separated by
          the fixed receiver-flip gap of §14.7
          └─ no MANIFEST by the deadline → B returns to the main channel

  reverse flag set:
    B→A   MANIFEST  {…, how A's train was heard, its own count and length}
    B→A   the packets themselves × count
          └─ count 0 → the declared traffic vanished underneath (a radio
             cycle); nothing after all, both go home
          └─ no MANIFEST by the deadline → A returns to the main channel

  reverse flag clear:
          nothing — B goes home on A's stated count or length, A on its own
          last frame. No close is sent and nothing is waited for.

        both return to the main channel
```

One detour carries traffic in **both** directions, ends by arithmetic, and is not
acknowledged. **Nothing is waited for anywhere when things go right.** Ping-pong
traffic has its reverse leg — the proof of the previous packet, the next
request — buffered before the transaction starts, so the answer is a train or
nothing, declared in the GRANT; a reply that appears later opens its own
transaction. The only intervals between frames are the receiver-turnaround
covers of §14.7, counted into every stated length. Deadlines exist for misses
alone: a packet lost is lost, and the layers above retry.

**Why the answer is on the main channel.** Three things follow from putting it
there rather than letting the requester decide and leave.

The **peer is the better chooser**: it has been camped on the main channel and
knows which channels it has recently heard quiet, its own remaining airtime and
its own reuse gaps — none of which the requester can know, being about to transmit
rather than listen. A **duration is only meaningful once the modulation is
fixed**, and the peer fixes it — which is why the requester states how many
*bytes* it has and the peer states how many milliseconds that will take. And the answer arriving *before*
either side retunes means **the reverse direction has somewhere to go**: one
detour carries a round trip, which is what traffic that stalls for a delivery
proof — most interactive traffic — actually needs.

It costs one extra frame on the shared channel, and it saves one on the detour
channel plus a turnaround, because an answer heard on the main channel is already
proof the peer is there. §6 carries the arithmetic.

### 0.1 Every frame and its fields

Field order, widths and type values below are normative.

Every SUPE frame opens with its type byte, and that byte plus the regime and
version nibbles behind it determine the frame's permitted length exactly:

| Type | Frame | Sent on | Length |
|---|---|---|---|
| `0xC0`, `0xC1` | **never assigned** | | see below |
| `0xC2` | SUPE_START | main channel | 7, or 10 with `sender_ident` |
| `0xC3` | **reserved, never reassigned** | | discard |
| `0xC4` | SUPE_ANNOUNCE2 | main channel | 5 + 4·count |
| `0xC5` | SUPE_GRANT | main channel | 10 |
| `0xC6`, `0xC7` | reserved | | discard |
| `0xC8` | **reserved, never reassigned** | | discard |
| `0xC9` | MANIFEST | unicast channel | 11 |
| `0xCA`–`0xCF` | reserved | | discard |
| `0xD0`, `0xD1` | **never assigned** | | see below |
| `0xD2`–`0xDF` | reserved | | discard |

`0xC3` and `0xC8` are held out of the assignable set deliberately. Twenty-six
values is more than this protocol will ever need, and keeping two in reserve costs
nothing against the possibility of a number meaning two things to two captures.

**A type value never ends in 0 or 1.** An interface is free to put framing of its
own ahead of the Reticulum packet, and one that does will have its own reachable
byte values in this space. The framing this protocol was first built on carries a
random 4-bit sequence in the high nibble and a single flag in bit 0, which reaches
every value ending in 0 or 1 and nothing else. Conceding those four values costs
nothing — twenty-eight remain — and it means a SUPE frame can never be confused
with a framing byte from a node that has never heard of SUPE. Any interface that
prepends framing must check its own reachable set against this rule before
carrying SUPE.

The grouping is otherwise mnemonic only — `0xC2`–`0xC7` for frames on the main
channel, `0xC8` upward for frames inside a detour — and it is not a check a
receiver can apply. A reserved value is discarded exactly as a wrong length is
(§3). The deferred frames of §17 take numbers from `0xCA` upward.

**Levels and powers are `dBm + 64`, read as a signed byte.** Every field carrying
a transmit power or a received signal level uses that one encoding, so the
representable range is −192 dBm to +63 dBm at 1 dB. The offset is what buys the
bottom: a plain signed byte stops at −128 dBm and receivers already report below
−130, while nothing needs the top — +63 dBm is two kilowatts, and the regulation
caps radiated power at 14 dBm (§14.2). More sensitive receivers are a great deal
likelier than five-gigawatt transmitters, so the range is spent accordingly.

**A stated transmit power is the power the frame actually left at, after every
cap.** Not the configured power, not the requested one. Every path-loss figure in
this protocol is a difference between a stated power and a measured level, so a
node that states what it intended rather than what it did corrupts the other
side's arithmetic by exactly the amount it was capped — silently, in the direction
that makes the link look better than it is.

**Signal-to-noise is a separate signed byte in quarter-decibels**, covering
−32 dB to +31.75 dB. It is a ratio rather than a level, needs no offset, and that
is both the range and the resolution the silicon reports natively.

**The main channel carries only what it takes to get two nodes onto another
channel.** Nothing else belongs there. Full capabilities, train descriptions and
verdicts all move to a channel that is faster and bothers only the two parties
involved. The one exception is the measurement pair in SUPE_GRANT, which is there
because it is free — the frame exists anyway, and two bytes inside it cost nothing
against a frame of its own later.

**Main channel.** At the SF7/BW125 most networks hail on (§3), SUPE_START's seven
bytes cost 31 ms and SUPE_GRANT's ten cost 36 ms. Each sits on a symbol-group
boundary.

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **SUPE_START** | type | 1 | `0xC2` — `110` protocol bits then 5 bits of type (§3) |
| | regime / version | 1 | a nibble each — fixes how everything after it is read |
| | tag | 3 | first three bytes of the packet's first address field |
| | family / ceiling | 1 | a nibble each: this node's radio family (§14.6), and the highest budget it will accept. The peer needs both to resolve the ladder, and cannot have them any other way if it never heard this node's announcement |
| | load | 1 | in 32-byte units — how much traffic is queued for this peer, as `ceil( Σ(bytes + 16) / 32 )` over the queued packets, saturating at 255. **Bytes rather than time**, because the modulation is the peer's to choose and a duration stated before it is chosen means nothing. The 16 bytes charged per packet stand in for its preamble and header (§6) |
| | sender identity | 0 or 3 | first three bytes of the sender's own identity hash, under `sender_ident` (§4); presence is implicit in the frame length |
| | | **7 or 10** | |
| **SUPE_GRANT** | type | 1 | `0xC5` |
| | regime / version | 1 | a nibble each — **the regime of the detour**, which may be lower than the START's (§6) |
| | channel / budget | 1 | a nibble each: the channel index this detour uses, and the link budget on it (§14.3). **Budget 15 means refused**, and then the channel nibble carries the reason |
| | duration | 1 | in 20 ms steps — how long the whole transaction takes, computed from the requester's load at the modulation this frame just chose, plus this node's own train and the turnarounds. The only duration on the air, and what every third party holds for |
| | power | 1 | `dBm + 64`, signed, in the low seven bits — what this frame went out at. **The top bit is the reverse flag**: this node holds traffic for the requester, so a reverse MANIFEST will follow the requester's train. Clear, both sides go home on the train's end with no further frame |
| | signal strength | 1 | `dBm + 64`, signed — what the START was heard at |
| | signal-to-noise | 1 | signed quarter-dB — what the START was heard at |
| | START hash | 3 | first three bytes of SHA-256 over the START frame exactly as it was transmitted, all seven or ten bytes of it — which offer this answers, and the transaction's identifier from here on |
| | | **10** | |

**Capabilities** — two bytes, carried by MANIFEST and by ANNOUNCE2, and never by
SUPE_START or SUPE_GRANT:

| Field | Size | Meaning |
|---|---|---|
| family / ceiling | 1 | a nibble each — the same pair SUPE_START carries in the clear, repeated here for nodes that hear the announcement and never the START |
| maximum power | 1 | `dBm + 64`, with the adaptive-power flag in the top bit. That bit is free wherever a *transmit power* rides — a transmit power never stores a negative value, where a received level routinely does. It is spent exactly twice in this protocol: here, and as SUPE_GRANT's reverse flag. Never on a level field |

**Main channel, continued** — the announcement, at hailing modulation, in every
regime

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **SUPE_ANNOUNCE2** | type | 1 | `0xC4` |
| | regime / version | 1 | a nibble each |
| | capabilities | 2 | as above |
| | power | 1 | `dBm + 64`, signed — what this frame went out at, so a listener can turn its own reading into path loss |
| | identity hashes | 4 × count | first four bytes of each identity this node holds — last, so the count is implicit in the frame length |
| | | **5 + 4·count** | |

**Unicast channel** — one frame shape, sent by each side in turn

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **MANIFEST** | type | 1 | `0xC9` |
| | power | 1 | `dBm + 64`, signed — what this frame and every packet behind it goes out at |
| | signal strength | 1 | `dBm + 64`, signed — how the peer's last frame was heard. For the sender's MANIFEST that is the GRANT, on the main channel; for the peer's, it is the train that just arrived, at the detour's own modulation |
| | signal-to-noise | 1 | signed quarter-dB, the same frame |
| | capabilities | 2 | the sender's — the peer may never have heard its announcement |
| | count | 1 | packets in this train. **Zero means nothing after all** (§8) |
| | length | 1 | how long this train takes — its airtime plus the receiver-flip gaps of §14.7 — in 5 ms steps; 1.275 s is the ceiling the field imposes |
| | transaction hash | 3 | the three bytes the GRANT carried, returned unchanged by both sides for the life of the transaction |
| | | **11** | |
| *the packets* | — | — | not SUPE frames. Ordinary Reticulum frames in the interface's ordinary framing — its checksum, its existing handling of a packet too large for one frame — transmitted on the unicast channel exactly as they would be on the main one. SUPE decides where and at what rate they go, and touches nothing else about them. |

Three rules produce the shapes above, and they should hold for anything added
later:

- **Every frame has a length the receiver can enumerate** from its regime, version
  and type alone: one value for most, two for SUPE_START depending on whether the
  sender names itself, and a count-derived length for SUPE_ANNOUNCE2. Nothing is
  signalled by a flags byte and nothing is negotiated, so anything outside the
  enumerated set is discarded (§3).
- **On the main channel, count frames, not bytes.** With no checksum, every
  payload up to the quantum in §3 costs the same — seven bytes at SF7, more at
  slower hailing configurations. Shaving below it buys nothing, and this is why
  SUPE_GRANT carries a full measurement pair: the frame was going to cost a
  symbol group regardless.
- **On the unicast channel, also count frames, not bytes.** A short frame there is
  preamble-dominated, so folding a field into a frame that is already going out is
  nearly free while adding a frame costs its preamble plus a turnaround. That is
  why powers, measurements and capabilities all ride inside MANIFEST rather than
  in an opening exchange of their own.

No SUPE frame carries a cyclic redundancy check (§3); the Reticulum frames of a
train keep theirs, being none of SUPE's business. The regime and version nibbles
ride only on frames a stranger may have to judge — the three main-channel frames.
MANIFEST omits them: the GRANT that opened the detour fixes the dialect for both,
including which regime the detour itself runs under.

## 1. The idea

One robust shared channel that every node camps on, carrying Reticulum announces,
path discovery and anything broadcast, plus per-pair private detours for unicast
traffic. The detour is arranged in two short frames on the shared channel and runs
at whatever rate the pair's measured link supports, in both directions.

**No node identity appears on the air except where a node chooses to publish it.**
Transactions are opened on a 3-byte prefix of a Reticulum address and nothing
else. The one place a node names itself is its own periodic announcement, which
exists to serve the capability table.

## 2. What this rests on

Four properties of Reticulum, each verifiable against its reference
implementation. If any of them ceases to hold, SUPE breaks.

**The first address field is always what the next receiver holds.** For a packet
in transport it is the next hop's transport identity; at one hop it is the
destination hash; on a link it is the link identifier; for an implicit delivery
proof it is the truncated hash of the packet being proved, which the sender holds
in its receipt and every relay holds in its reverse table. The only traffic whose
first address nobody holds in advance is announces and path requests — both
broadcast, both of which stay on the main channel.

**The packet hash is invariant under transport rewriting.** `get_hashable_part`
in `Packet.cpp` masks off header type, context flag and propagation type, skips
the transport identity field, and excludes the hop byte. Both endpoints and every
relay therefore compute the same value from differently framed copies. This is
what lets both sides of a new link derive the same link identifier with no
signalling, and what makes proof tags match without coordination.

**You cannot address a neighbour you have not heard.** A specific next hop only
exists because a path exists, and a path is only ever built from an announce that
neighbour physically transmitted on this interface — the destination itself at one
hop, the relaying node otherwise. So whenever SUPE applies at all, the address is
known and a signal measurement for that neighbour already exists. Where no next
hop is known, Reticulum broadcasts blind and there is nothing for SUPE to
negotiate with.

**A node's identity hash is derivable and already shared.** Every Reticulum
announce carries the announcing identity's public key, so any receiver can compute
that node's identity hash without asking. All of a node's destinations share it,
and a transport node's identity hash is literally the address relayed traffic is
sent to. That makes it the natural key for a capability table — one entry per
node rather than one per destination — and it is why SUPE_ANNOUNCE2 publishes
identity hashes rather than destination hashes.

## 3. Frames, regimes and versions

**Frames a stranger may have to judge carry a regime nibble and a version
nibble** in one byte — the three main-channel frames, per §0.1. The pair fixes how
every byte after it is read, so block lengths never need announcing. Each version
of each regime carries an expiry date, fixed when the software is built. Past that
date a node neither sends nor accepts frames naming it and falls back to plain
main-channel operation, so an obsolete dialect leaves the air by itself instead of
having to be spoken forever.

| Regime | Version | Meaning |
|---|---|---|
| 0 | 0 | Single Channel — one frequency, one bandwidth, the spreading factor the only thing that moves |
| 1 | 0 | ETSI EN 300 220, 863–870 MHz |

§14 holds both in full: channels, ladder, sync words, timings, ceilings and
limits.

**August 2026.** At this stage of development the version of every regime stays at
zero and everything here is expected to change. The expiry is what makes that
safe, so it does the work a version number would otherwise do: no build should set
one more than fourteen days ahead of its own build date. A node left unattended for a fortnight then stops speaking SUPE
altogether, which is the intended outcome: far better than speaking a stale
dialect at a network that has moved on.

**Every node supports regime 0, and there is no reason not to.** It needs no band
plan, no channel raster and no regulatory basis of its own, and even its slowest
useful detour beats sending the same packet on the shared channel. In practice
every node on a channel either runs the same regime or does not speak SUPE at all;
the mixed case that remains is a regime-1 node meeting one that only has regime 0,
and §6 handles it by letting the answering node name the lower regime.

Regime 0 does not move frequency at all, and does not change bandwidth: its
channel nibble is always zero. Its whole ladder is the spreading factors above the
hailing one, at the hailing bandwidth — two or three budgets on most parts, each
worth about 2.5 dB of rate for 2.5 dB of margin.

**Regime 0's number is fixed; do not renumber it.** A frequency-agility setting
that already exists on an interface will often document 0 as "no agile channels"
rather than as a regime. The two readings agree in the only way that matters:
regime 0 has no channel plan, so resolving its number to no agile channels is
correct whichever way it is read, and the setting can name the regime unchanged.

**The ladder is measured from the hailing configuration, not written in
absolutes.** Budget 1 is one place faster than whatever this network hails at,
budget 2 is two, and so on; budget 0 is the hailing configuration itself. A
network on SF9/BW125 gets SF8, SF7, SF6 where one on SF7/BW125 gets SF6, SF5 —
the same protocol, the same indices, no configuration anywhere.

Three things make that the right frame of reference:

- **The baseline is shared without being sent.** Two nodes arranging a detour
  are, by definition, hearing each other on the hailing channel, so they already
  agree on what budget 0 means. Nothing has to carry it.
- **It is the floor that matters.** A node we cannot reach at the hailing
  configuration cannot be reached anywhere else either — every place up the ladder
  trades margin for rate. So the interesting question is never "what modulation"
  but "how much margin do we have above the one that already works", which is
  exactly what the measurements in §10 produce.
- **The ladder is monotonic** in both directions (§14.3): every entry is faster
  and needs more margin than the one below, and no entry is dominated. That is
  what makes an index meaningful rather than a lookup.

**A budget index means nothing without the channel beside it, and they always
travel together.** How many entries the ladder has and what each resolves to
depends on the bandwidth the named channel permits — nine entries where 500 kHz is
allowed, six where 250 kHz is the ceiling. That was a genuine ambiguity while the
sender named a budget without knowing which channel would carry it. It is not one
here: SUPE_GRANT carries both nibbles in the same byte, chosen together by the
node that knows both, and every recipient resolves against the channel it just
read. No mapping table between bandwidths is needed and none should be built.

What a budget resolves to in absolute terms is still what governs the radio, so
the sync word, the header mode and the family limits all follow the *resulting*
configuration rather than the index. A budget landing on SF5 takes `0x21` and is
unavailable on SX127x; the same index on a slower network lands somewhere else
entirely and neither applies.

**The type byte is 3 bits of protocol and 5 bits of type.** The protocol bits are
`110`, so every SUPE frame begins `0xC0`–`0xDF`, of which 28 are assignable (§0.1).
That range cannot collide with Reticulum itself: SUPE requires an interface with
no access code (§13), and without one the daemon's flag byte never has its top bit
set — the access-code path is the only thing that sets it, and it sets it
unconditionally. So anything from `0x80` up is not Reticulum on a SUPE interface.

**What the range must also dodge is the interface's own framing**, which sits
ahead of the Reticulum packet and is therefore what a receiver actually reads
first. That is a property of the interface rather than of Reticulum, and it is why
§0.1 concedes the four values ending in 0 or 1: restricting what *we* transmit
would do nothing about frames from a node that predates SUPE, whereas choosing
type values its framing cannot produce works against every vintage without
anyone agreeing to anything.

**A frame whose length is not one the regime, version and type allow is
discarded.** The permitted set is tiny — one length for most frames, two for
SUPE_START, and a count-derived length for SUPE_ANNOUNCE2 — so the test rejects
outright rather than merely suspecting, and it is the last cheap filter before we
act on anything.

**Everything an index selects is a constant of the regime.** An index on the wire
means nothing except against a table both ends hold identically, so every such
table is compiled in and keyed by regime and version — and nothing it contains may
be a setting, because two neighbours who configured differently would meet at
different frequencies, budgets or sync words and never hear each other. The regime
owns:

- the channel raster the detours draw from, which indices name which, and the
  maximum bandwidth each permits
- the modulation ladder and the rules that order it, along with each entry's
  coding rate, preamble length, header mode and low-data-rate optimisation
- the sync word each resulting configuration takes
- frame layouts and their lengths, which is what makes the length test possible
- the duration encoding — 20 ms steps, whose range must reach the regime's
  transaction ceiling (§14) — and the ceilings themselves
- power ceilings, duty and dwell limits per band
- the turnaround and retune constants of §14.7, from which every deadline follows
- the expiry date of the regime itself

The regime setting picks which of these tables is in force. It is the only radio
choice SUPE exposes, and deliberately so: everything else about a detour follows
from a number both sides already agree on.

**The hailing channel is the exception, and stays interface configuration.** Its
frequency, bandwidth, spreading factor, coding rate, preamble, transmit power and
sync word are `s.lora.<n>.*` as they are today, because that channel is not ours —
it is the Reticulum network being joined, shared with neighbours that have never
heard of SUPE. A regime that dictated it would be dictating other people's
network. SUPE reads those keys and never writes them.

**Sync words: the interface's own on the main channel — `0x42` by convention and
by default — then `0x67` off it, and `0x21` at SF5.** The latter two are regime
constants selected by the resulting configuration, not fields on the wire:
SUPE_GRANT names the channel and budget, so both sides derive the same word with
nothing further transmitted and no branch at the call site. Remember that the
two-byte form on SX126x is a nibble expansion, not an addition
([`afa.md`](afa.md) §5.4).

**Not all sync words are equally good, and the reason is arithmetic.** The sync
word is two symbols after the preamble, and each nibble is scaled by 8 to become
a symbol value — a bin index in a space of 2^SF bins. All that separates two sync
words on air is the distance between those bins, `|Δnibble| × 8`, and the
demodulator simply takes the strongest bin. So:

- **Nibbles that differ by 1 are 8 bins apart** — the minimum, and the most
  easily confused once noise, frequency offset or clock drift spread energy into
  neighbouring bins.
- **A nibble that matches contributes nothing.** That symbol discriminates not at
  all, and the pair is down to one symbol of separation.
- **Nibbles stay inside 1–7**, because 0 and 8-and-up fall outside the useful
  scaling.

Measured that way `0x67` sits 40 bins from `0x12` in both symbols and 24 from
`0x34`, which is as far from other people's networks as the space allows. The
values to avoid are the ones a nibble step away in both symbols — `0x23` is the
worst of them, one step from `0x12` and one from `0x34` — since they look
plausible and separate almost nothing.

**SF5 cannot have a good sync word, and does not need one.** Its 32 bins put
every nibble above 3 out of range, leaving nine possible words — and `0x12` is
built from two of those three nibble values, so all nine sit within one step of it
in at least one symbol. `0x21` is the best of them, at the minimum separation of
8 bins, and that is the ceiling. What makes it a non-problem is *where* it is
spent: LoRaWAN defines no data rate below SF7, and generic `0x12` networks live
at SF7 and above too, so SF5 is very nearly empty. The strong word goes where the
neighbours are, and the weak one where nobody else is listening.

`0x42` is out of range at SF5 for the same arithmetic — its leading 4 wants bin
32 — but it only ever transmits on the hailing channel, and nothing hails at SF5.
Hailing is chosen for reach, so it sits at the slow end of the ladder by
definition; SF5 is the far end of the other side.

Four filters run before anything is believed. The preamble is not among them —
plain upchirps are identical on every LoRa network, so a mismatched neighbour still
pays the detection. Of the four, the explicit header's own check does most of the
work and costs nothing, killing essentially all noise that survives the sync word.
The sync word itself is a courtesy separator rather than a barrier (§3). The type
byte's protocol bits and the length test then catch what a foreign network could
plausibly produce — which, given the header check, is very little.

**No SUPE frame carries a cyclic redundancy check.** All it buys is the radio
rejecting a corrupt frame instead of our own parse rejecting it a moment later,
and nothing downstream ever sees these frames, so the check has no consumer. What
it costs is a symbol group: its 16 bits push a frame into the next group four
times in seven, 5 ms each time on a network hailing at SF7/BW125.

This applies to SUPE's own frames and to nothing else. The Reticulum packets a
train carries are transmitted in the interface's ordinary framing, checksum
included — they are destined for the daemon, so the radio rejecting a corrupt one
saves work that would otherwise be wasted upstream. That is the same reasoning
reaching the opposite conclusion, because the consumer is different.

**Payload size on the main channel is quantised, and every frame is sized to a
group boundary.** LoRa bills payload in groups of symbols: every length inside a
group costs the same, and the byte that crosses into the next one costs the whole
group. With the check off, the payload term at a hailing spreading factor of `SF`
is `ceil((8·PL − 4·SF + 28) / 4·(SF − 2·DE)) · 5 + 8` symbols, where `DE` is 1
when the low-data-rate optimisation is in force (§14.3) and 0 otherwise. So the
groups are `(SF − 2·DE)/2` bytes wide, and the largest payload inside the *k*-th
is

    ( k·(SF − 2·DE) + SF − 7 ) / 2

rounded down. At SF7 with the optimisation off that gives:

| bytes | symbols | at SF7/BW125, preamble 8 |
|---|---|---|
| 1–3 | 13 | 26 ms |
| 4–7 | 18 | 31 ms |
| 8–10 | 23 | 36 ms |
| 11–14 | 28 | 41 ms |

- SUPE_START at seven bytes fills the second group exactly, and its ten-byte form
  under `sender_ident` fills the third.
- SUPE_GRANT at ten bytes fills the third exactly. Its measurement pair and its
  three-byte hash are what fill it: at eight bytes it would cost the same group
  and say less.
- SUPE_ANNOUNCE2 is the one frame whose length is not fixed, and it is the one
  frame on this channel that is not sized to a boundary: its `5 + 4·count` lands
  where the identity count puts it. One frame per interval per node is what that
  costs, which is why the interval is long and the count is every identity a node
  holds rather than one frame each.

The boundaries move with the hailing configuration, and not always against us: a
network hailing at SF12, where the optimisation is compulsory, puts everything up
to seven bytes in the *first* group, so SUPE_START costs one group there rather
than two.

Those figures assume preamble 8; the interface default of 12 adds about 4 ms to
each. Against roughly 760 ms for a full 500-byte Reticulum packet on the same
channel, the shared-channel cost of a whole detour — both frames — is about one
eleventh of a single packet.

**Configurations and channels are indices**, one nibble each, never literal
frequency and modulation values — a byte that named them outright would not fit
the quantum on the network where the quantum is tightest.

## 4. Configuration

Two keys carry the on/off decision between them, and the division is deliberate.
`enable` is the gate: with it off the device's on-air behaviour is exactly what it
was, so there is one thing to turn off when comparing. The regime number names a
table and never means "off" — regime 0 is a working regime, not an absence of one.

**The regime is not a key of SUPE's own.** Where an interface already carries a
frequency-agility regime number, that key names the regime, because the two are
the same quantity under different names: the channels, and what is permissible on
them. A second key would be a second answer to one question. A regime number the
firmware does not recognise resolves to no agile channels, which is the safe
reading of a value it cannot understand.

All the rest under one prefix. Note what is *not* here: no channel list, no
ladder, no sync words, no durations, timings or limits. Those are regime constants
(§3), because both ends must hold them identically. `s.lora.<n>.frequency`,
`bandwidth`, `spreading_factor`, `coding_rate`, `preamble`, `tx_power` and
`sync_word` describe the hailing channel, and SUPE only reads them.

| Key | Default | Meaning |
|---|---|---|
| `s.lora.<n>.SUPE.enable` | off | Speak SUPE on this interface at all. Off means a plain Reticulum LoRa interface. |
| the interface's frequency-agility key | `0` | Which compiled-in regime is in force (§14) — channels, ladder, sync words, timings, ceilings, limits, expiry. The only radio choice SUPE exposes, and the interface's own key rather than one of ours (above). Frames naming an expired regime are ignored. |
| `s.lora.<n>.SUPE.adaptive_txpower` | on | Transmit to each neighbour at a power measured for it, per §15. Off means every frame goes out at `tx_power`. |
| `s.lora.<n>.SUPE.announce_interval` | `30` | Minutes between a node's own SUPE announcements. It governs nothing else; Reticulum's announces are not SUPE's to schedule (§9). |
| `s.lora.<n>.SUPE.worker` | off | This radio does not present an interface to the daemon and is instead available to carry detours for any radio in the same band, so that radio can stay on its hailing channel. See §18. |
| `s.lora.<n>.SUPE.sender_ident` | on | Name ourselves in every SUPE_START. Costs three bytes and one symbol group, and gives up the protocol's default anonymity: a listener learns who is talking to whom, which no Reticulum header discloses. It buys three things, and the first is not optional in practice — **the reverse leg (§8) cannot exist without it**, since the tag a START carries is the *answerer's* address and says nothing about who is asking, so an unnamed requester's queued traffic at the far end is indistinguishable from a stranger's. It also lets neighbours hold traffic for us as well as for the peer we are servicing (§6), and lets the answerer file what our cargo establishes — a link identifier above all — against us rather than against nobody (§10). Off restores the anonymity and gives all three up; a node that turns it off still parses and honours the ten-byte frame from those that do not. |

## 5. Addresses that mean us

The modem keeps one flat set, learned by watching the traffic it carries for the
Reticulum daemon. No secrets, no cooperation from the daemon.

| Entry | Learned from | Retired by |
|---|---|---|
| our destination hashes | an announce we transmit at hop count zero | effectively never; refreshed by re-announce |
| our transport identity | an announce we relay — its first address field is ours | effectively never |
| link identifiers we terminate | anything we transmit at hop count zero addressed to a link | an observed link close; otherwise keep-alive staleness |
| link identifiers we relay for | forwarding a frame addressed to a link — the return direction of a relayed link arrives addressed to the link identifier, not to our transport identity, so a relay that skips this sleeps through it and the link dies | the same |
| pending proofs | the truncated hash of each packet we send or relay that may attract a delivery proof | the proof arriving; otherwise the receipt or reverse-table timeout |

Store the 3-byte prefix, an expiry, and a small reference count — 24 bits with a
thousand live entries collides internally often enough that a link close must not
unlist an unrelated pending proof. Roughly eight bytes an entry.

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

The mirror of the table above, and what budget selection actually runs on: given a
tag about to be sent to, which node is behind it, and what is known about reaching
that node. Three joins build it, all from traffic the modem is already carrying:

| Tag | Resolves to its node by |
|---|---|
| a transport identity | being one already — a relayed announce's first address field is the relaying node's identity hash |
| a destination hash | the Reticulum announce that carried that destination's public key, whose truncated hash is the identity (§2) |
| a link identifier | the destination the link request was addressed to, recorded as the request goes past |

Capabilities and measurements hang off the identity, never off the tag, so a node
with forty destinations costs one capability entry and forty pointers. This is the
table that answers "what budget" — §10 is what fills it.

## 6. Messages on the main channel

**Tag** — 3 bytes, always the first three bytes of the first address field of the
packet about to be sent, and nothing more. It has one meaning: an address the
peer holds. There is no type, no flag and no second interpretation, because the
receiver has one flat table (§5), a hit is a hit, and what follows — answer, hop,
receive, hand up, let the daemon judge — never depends on why the entry is there.

That includes delivery proofs, which are addressed to the truncated hash of the
packet being proved. Exactly two nodes hold that hash: the origin, in its receipt,
and the one relay that forwarded the packet, in its reverse table. A packet in
transport names its next hop, so no second relay ever handled it and no third node
ever wakes. The tag is as precise here as anywhere else.

---

**SUPE_START** — sender to peer, overheard by all
*"I have traffic for whoever holds this address, about this much of it, and this
is what my radio can do. Where shall we go?"*

Seven bytes, or ten where it names itself. It is a **request, not a commitment**:
nothing has been decided, no channel has been named, and a sender that hears
nothing back simply transmits on the main channel as it always would.

It carries the sender's family and ceiling in the clear because the peer needs
both to resolve the ladder, and may never have heard the sender's announcement.
Two nibbles is the whole cost, and it is what removes any requirement that a
budget index mean the same thing to two nodes that do not know each other's
silicon.

**The load is stated in bytes, and that is the only honest unit available here.**
The requester does not know the modulation — the peer has not chosen it yet — so
any figure in milliseconds would be a guess at the hailing rate that the peer is
about to invalidate. Bytes convert cleanly the moment the modulation is fixed, and
they convert the same way for every listener.

What it counts is `ceil( Σ(bytes + 16) / 32 )` over the packets queued for this
peer, saturating at 255 and therefore reaching 8160 bytes. **The unit is scaled to
a train.** At the fastest entry any regime here reaches — SF5/BW500 — a second of
air carries about 7 KB: twenty-eight full 255-byte frames in 979 ms, and 7.2 KB/s
for traffic split the way Reticulum actually splits it. So a saturated load byte
means "at least a train's worth", which is the only thing the peer needs to know
at that end of the range, and the quantum is small enough that anything shorter is
described to within one 20 ms duration step.

The sixteen bytes charged per packet stand in for its preamble and header, which
are real airtime and which a byte count alone would miss entirely. LoRa airtime is
symbols: an opening costs 24.25 of them, or 26.25 at SF5 and SF6 where the header
term is longer, and payload runs at `10 / SF` symbols a byte. That puts the true
figure at 13 bytes at SF5, 17 at SF7 and 24 at SF12 — so sixteen is close across
the whole ladder, and in any case smaller than the 32-byte quantum it feeds.

Folding the overhead into the byte count this way is also what lets the peer treat
the load as one blob rather than needing the packet count: the per-packet cost is
already in it.

There is no duration field here, and there does not need to be: every listener has
the load and the hailing configuration, so anyone who wants a pessimistic bound
before the GRANT arrives can compute the same one the requester would have sent.

**Our own identity, only if `sender_ident` says so.** Without it a START names the
peer and no one else, which is what keeps the protocol anonymous — and what leaves
the sender unprotected, since neighbours can hold traffic only for a tag they can
recognise, and nothing on the wire says who left. That asymmetry falls hardest on
exactly the node it should spare: a gateway is the tag most traffic is addressed
to, so when it initiates a detour, everyone's traffic to it goes into an empty
channel. Naming ourselves costs three bytes, one symbol group, and the anonymity;
it buys neighbours the ability to hold for us too. It is a deployment choice, and
a gateway should make it.

There is no discovery handshake and no cold start, because there is nothing to
discover. A detour is only ever requested of a peer whose SUPE_ANNOUNCE2 has been
heard, which is what makes it a peer at all in SUPE's terms. Its address and a
signal measurement come from the Reticulum announce that built the path (§2).

---

**SUPE_GRANT** — peer to sender, overheard by all
*"Meet me on that channel at that budget. I will be gone this long, this frame
went out at that power, and I heard you at this one."*

Ten bytes, and the single most consequential frame in the protocol.

**The answering node chooses, and that is the point.** It has been camped on the
main channel and knows which channels it has recently heard quiet, which the
requesting node structurally cannot know — it is about to transmit, not listen,
and an excursion to sense a candidate would put a retune on the transmit path to
read the wrong end of the link anyway. It knows its own airtime budget per
channel, its own recent transmissions and the 100 ms reuse gaps they imply. And it
knows the requester's family from the START, so it can resolve the ladder for the
channel it is choosing, in the same breath, with no ambiguity about which ladder
either side means.

**It costs no contention.** SUPE_GRANT is a turnaround response inside a
transaction, transmitted within the interval of §14.7 without carrier sense — the
medium was just won by the frame it answers. So its cost is its airtime and
nothing else.

**It is also the arrival proof, and that is why no such frame exists on the
detour channel.** Hearing a GRANT establishes that the peer is present and
willing, before either side has spent a retune on it — earlier than any
detour-channel frame could, and without a turnaround.

**Refusal is a first-class answer.** Budget 15 means refused and the channel
nibble carries the reason: out of airtime on every channel, no channel quiet, the
regime is not one this node runs, the ceiling asked for is beyond it, or simply
busy. A refusal still carries its power and measurement bytes, so it is not a
wasted frame — the requester gets a free path-loss reading out of being told no.
What it buys is knowing *how long* not to ask again: a node out of airtime for the
hour and a node busy this second want very different backoffs, and silence cannot
tell them apart.

**The regime nibble is the detour's, and may be lower than the request's.** A node
that has only regime 0 answers a regime-1 START with a regime-0 GRANT, naming
channel 0 and a budget on the hailing frequency. Since every node supports regime
0 (§3), that is a working answer rather than a refusal, and it makes regime 0 the
universal floor beneath every richer plan. Raising the regime above the request's
is not permitted: the requester named what it can speak.

**Everyone else** who hears the GRANT holds traffic addressed to the tag — and to
the requester's identity, when it named itself — for the stated duration. It is a
virtual carrier-sense hint, not a reservation: other pairs keep using the main
channel meanwhile, nodes out of earshot will not hold, and anything sent regardless
is covered by ordinary Reticulum retries. Returning early is visible as ordinary
traffic.

**One duration, stated by the node that computed it.** Both sides derive their own
deadlines from the modulation and the load, so neither needs the number for its own
conduct; it is on the air for third parties, and it is stated by the only node in a
position to compute it correctly. A node that hears the START and not the GRANT
holds nothing, which is right: nothing was arranged.

**A hold is released early by the access procedure's fixed interval, not at the
duration itself.** Every contention machine opens with a fixed idle period before
its random draw begins — a DIFS, in the shape [`psa.md`](psa.md) §5 describes. A
node that holds for the full stated duration and *then* starts that fixed interval
enters the draw one whole interval behind a node that was never holding at all.

That is a real and systematic penalty, and it falls on the node that was being
polite. Detours are short and frequent, so a node in a SUPE neighbourhood holds
often; a node that never heard the hint never holds and is in the draw first,
every time.

**This is a fairness problem in access ordering, and not the whole ledger.** A
node that transmits into the pair regardless does pay for it, and pays more: it
spends a full packet's airtime on a receiver that is not there, gains a
retransmission at the layer above, and the medium is occupied for all of it. The
hold is self-interested before it is polite — a held packet is a packet not
wasted. What the subtraction fixes is narrower and still worth fixing: having
correctly declined to waste a transmission, a node should not then be last in
line.

So a holding node **starts its fixed interval at `duration − fixed_interval`**,
timed so the interval ends exactly as the pair is due back and the random draw
begins from there. Nothing transmits before the pair returns — the draw is still
ahead of it — and a node that held now enters contention on the same footing as
one that did not. Two properties make this safe: the interval being subtracted is
idle time, so nothing is emitted during it; and it is the *fixed* part that moves,
never the random part, so the draw that separates two nodes wanting the channel at
the same instant is untouched.

The returning pair does not get the same treatment and should not: it cannot sense
a channel it is not on, so it senses on arrival like anyone else. It has just had
the medium to itself, and going to the back of the queue is the correct outcome.

---

**What the second frame costs, and what it buys.** At SF7/BW125 the pair costs
67 ms of shared channel where a bare offer alone would cost 31. Against a
500-byte packet's 797 ms on the same channel that is 8 % rather than 4 %, and it
buys:

- the reverse direction on the same detour, which halves the number of detours
  for any traffic that stalls for a delivery proof — which is most interactive
  traffic
- one fewer frame and one fewer turnaround on the detour channel
- a channel chosen by the node that can actually judge it
- a refusal that can be acted on
- a measurement pair delivered before either side leaves the main channel, so
  even a detour that fails outright has taught both sides something

For a single small packet in one direction it is the worse trade, and honestly
so. For everything else it is not close.

## 7. Announcing

**SUPE_ANNOUNCE2** — us to everyone, once per announce interval, on the main
channel at the hailing configuration, in every regime
*"These identities are me, this is what my radio can do, and this frame went out
at that power."*

**It goes out where everyone is already listening.** An announcement has to reach
nodes that know nothing about us yet, and the hailing configuration is the only
one every node is known to be camped on. Moving it to a channel of its own would
save this frame's airtime on the shared channel once per interval per node, and
charge every listener in earshot two retunes and a deafness window to collect it
— which is the worse trade in a neighbourhood of any size, and it is why there is
no announce channel and no frame announcing one.

The power byte is what makes the frame worth hearing: a listener already has its
own reading of it, and the two together give path loss rather than a bare signal
level.

Identity hashes, not destination hashes: a node typically has more destinations
than identities, and any receiver of a Reticulum announce can already derive the
identity hash from the public key it carried (§2). Four bytes rather than the
three used for tags, because a tag names one transaction and tolerates collisions
while this names a node in a table that persists — the extra byte is worth its
airtime here and not there.

**Every identity a node holds rides in one frame.** On the main channel an
unbundled announcement would spend a preamble and a frame per identity where one
frame carries all of them, so unbundling costs strictly more for every party in
earshot and buys nothing. There is no setting here and nothing to spread in time.
The only bound is the frame itself: a count that will not fit one frame leaves
its surplus for the next beat, which is a sender-local choice needing no
agreement.

**No power sweep follows it.** Ordinary operation measures the same thing and
measures it continuously: every frame in this protocol that carries a measurement
also carries the power that produced it, so each exchange yields path loss rather
than a bare reading, at two configurations, for free. A deliberate sweep would buy
a cliff measurement the controller can derive from path loss and a target margin
anyway, and it would buy it once every thirty minutes instead of on every
exchange.

**Every SUPE node files every ANNOUNCE2 it hears**, and it costs nothing to: the
frame arrives on the channel the node is already camped on, so there is no
listening decision to make and no window in which anything else is missed.
Capability entries go stale and nodes reboot, and a listener that tried to judge
its interest in advance would have no way to know when it had judged wrong.

## 8. Messages on the unicast channel

One frame shape, sent by each side that has a train to describe, and the
requesting node speaks first. No acknowledgement, and no negotiation:
everything was settled on the main channel — including, via the GRANT's
reverse flag, whether the answering side will speak here at all.

**Both sides retune on the GRANT.** The requester retunes when it has transmitted
nothing further; the answering node retunes the moment its GRANT is out. Both
observe the retune gap of §14.7, the requester adds the manifest lead — spent
from inside the turnaround allowance the deadline already budgets, so the
answering node's receiver is armed before the first preamble flies — and the
requester's MANIFEST is the first thing on the channel.

**MANIFEST** — before each train
*"This train goes out at that power, here is how I heard you, here is what my
radio can do, expect this many packets, and it will take this long."*

It carries capabilities unconditionally, in both directions. Each side has seen
the other's family in the START or inferred it from the GRANT, but neither has
necessarily heard the other's announcement, and two bytes on this channel are not
worth a condition. For a link-identifier tag the capabilities are the one handle
either side will ever hold on a peer that dialled it, and filing them against
the link is what lets reverse traffic on that link detour at all (§10).

**Its measurement is the one a one-directional detour cannot take.** Each
MANIFEST reports how the peer's last frame was heard, and because every frame in
this protocol states its own transmit power, that reading becomes a path loss
immediately. The
requester reports the GRANT as it heard it on the main channel — closing the
reverse path at the hailing configuration. The answering node reports the train it
has just received, at the detour's own modulation — which is the only way a
sender ever learns how its own train landed, and the reason the reverse MANIFEST
carries the reading even when the transaction taught it nothing else.

**MANIFEST is fixed length, and that is what lets it be its own confirmation.** A
peer waiting on a frame whose length it cannot predict has to wait generously; a
peer waiting on eleven bytes at a known modulation has an exact deadline. Hearing
it also tells the receiver that this modulation closes in that direction, so
nothing further needs to be exchanged before the packets start.

**The GRANT's reverse flag decides how a transaction ends, and nothing is
waited for either way.**

- **flag clear** — the answering node had nothing queued for the requester when
  it granted. It goes home the moment the requester's stated count or length is
  satisfied; the requester goes home on its own last frame. No close is sent:
  the ending is arithmetic both sides already hold, and a frame that says
  "nothing" spends airtime saying it. Silence here is the normal outcome, so
  nobody reads it as loss.
- **flag set** — the reverse MANIFEST and its train follow the requester's, and
  from the requester's side silence past the deadline now genuinely means loss
  (§15 reads it as exactly that). **Count 0 keeps one meaning: nothing after
  all** — the corner where the declared traffic vanished underneath (a radio
  going down between the GRANT and the turn). Both sides go home on it.

There is no wait state anywhere in a transaction. Ping-pong traffic has its
reverse leg — the proof of the previous packet, the next request — buffered
before the transaction starts, which is why the flag can be declared in the
GRANT at all; a reply that appears later opens its own transaction. The
answering node can only recognise that buffered traffic as the requester's if
the START named the requester, so `sender_ident` (§4) is what the reverse flag
rests on: unnamed, every packet queued for that peer looks like a stranger's and
the flag is never set.

**Nothing is held back to wait for a ride.** A packet that could ride a
transaction the peer might open — a proof above all — takes the channel on its
own terms as soon as it can. The wait costs the far end its send window for as
long as it lasts, and the ride it was waiting for carries it as reverse cargo
whether or not it was holding out for one. Holding a
pair on a private channel to wait for a daemon is waiting this protocol does
not do.

**The transaction hash** is the three bytes the GRANT carried, returned unchanged
by both sides for the life of the detour, and it is not redundancy. Two pairs out
of earshot of each other can be granted the same channel at the same moment and
neither will know. Echoing the tag would not separate them: the dangerous case is
precisely two requesters wanting the *same* tag, which on a segment with one
transport node is the ordinary case rather than a corner one (§11). The START's
own hash does separate them, because each requester knows exactly what it
transmitted and each answering node knows exactly what it received.

So a **MANIFEST quoting a hash this node did not grant or was not granted** means
the channel is carrying somebody else's exchange. Leave at once rather than
waiting out the deadline, and do not count the train that follows as your own —
a collision converted into an immediate return rather than a mixed-up exchange.

**The packets** × count — and **these are not SUPE frames.** They are ordinary
Reticulum frames in the interface's ordinary framing, checksum and all, including
whatever it already does with a packet too large for a single frame. SUPE chose
the channel and the rate; it does not touch what travels on them. What differs
from the main channel is only the absence of carrier sensing, there being nobody
else here, and that they arrive back to back — separated only by the fixed
receiver-flip gap of §14.7, which every stated length includes.

The two kinds stay distinguishable by the rule that already separates them on the
main channel: a SUPE frame begins `0xC0`–`0xDF`, and a Reticulum frame on an
interface without an access code never has its top bit set at all (§3). Each side
counts *packets*, not frames, so a split packet counts once, when both halves are
in.

---

**A detour is worth taking whenever there is a peer to take it with**, and the
reason is that the sender's own arithmetic is the wrong arithmetic. Weighed as a
private trade — overhead against the fraction of airtime the faster modulation
saves — a small packet looks marginal. Weighed from where the scarcity actually
is, it is not close.

**Count it from the other side of the room.** For every node except the two
involved, a detour replaces a packet's entire main-channel airtime with two short
frames. Nothing else is spent there.

**One contention wait covers the whole thing.** The requester's carrier-sense
covers the START, the GRANT that answers it and the detour behind both, and the
same wait would have been paid to transmit on the hailing channel anyway. So a
detour's cost against a plain transmission is the two frames plus turnarounds and
retunes, and the comparison reduces to airtime — which computes, hailing at
SF7/BW125 against a detour at SF5/BW500:

| Reticulum packet | all on the hailing channel | detour | saving |
|---|---|---|---|
| 19 B | 60.7 ms | 81.7 ms | −21.0 ms (worse) |
| 100 B | 178.4 ms | 91.9 ms | 86.5 ms (48 %) |
| 254 B | 403.7 ms | 111.8 ms | 291.9 ms (72 %) |
| 500 B | 797.2 ms | 145.1 ms | 652.1 ms (82 %) |

**Break-even is not the smallest packet, and that should be stated plainly.** A
single main-channel frame would break even at the smallest thing Reticulum can
emit; the second frame moves it, and a bare 19-byte header alone in one direction
costs more than sending it plainly. Three things make that acceptable rather than
a fault. The row assumes the reverse direction is empty, and the reverse direction
is exactly what the second frame bought — a 19-byte packet that draws a proof back
needs one detour where it would otherwise need two. The 100-byte row, which is
where real traffic lives, still saves half. And the decision is not fixed here: §18 puts it in one function
with the queue in front of it, so a lone small packet in one direction can simply
not detour.

**Across a batch the wait does not cancel — it compounds, and against the shared
channel.** Each packet sent conventionally contends separately, and the contention
band is chosen from the node's *own* accumulated transmit duty, so a batch walks
its own waiting time upward as it goes. Fourteen 500-byte packets on an idle
medium, starting from nothing: the first waits 160 ms, the next six 460 ms as the
node crosses into the second band, the last seven 760 ms in the third. **19.40 s,
of which 8.24 s is contention alone.** The same payload as one detour is
**1.20 s** — a wall-clock win of over sixteen times that also hands 11.12 s of
shared medium back to every other node in range.

**And the node that detoured stays cheap.** It credited about 75 ms of airtime to
the shared channel rather than eight hundred, so it remains in the lowest
contention band while a node that sent conventionally now sits in the third. The
detour shortens every one of its future waits as well — but only if detour airtime
is accounted separately and never credited against the hailing channel's own duty
figure. That is an implementation requirement, not an optimisation: get it wrong
and this effect disappears silently.

**It improves with load, which is when it matters.** The two frames are a fixed
cost amortised over the whole transaction: one packet relieves the shared channel
of one packet's airtime for 67 ms, fourteen relieve it of fourteen for the same
67 ms. Trains grow when a node is busy, and a node is busy when the channel is
contended — so the mechanism is most efficient exactly when the network most needs
it to be.

**What fills a train is a resource transfer**, and it fills it as a batch rather
than a trickle. Reticulum's resources are receiver-driven: the receiver asks for a
window of parts by hash, and the sender's handler for that request transmits every
part it names in one pass, back to back, with nothing between them. The window
opens at 4 and grows toward a ceiling the link's measured performance sets — 4 on
a very slow link, 10 on a slow one, 75 where the rate justifies it. At a link data
unit of 431 bytes those ceilings are 1.7 KB and 4.3 KB a batch, against about
7.8 KB for a one-second train at the fastest budget, so a slow-link window fits
inside a single train with room to spare. Only the fast ceiling overflows one, and
then into four.

There is a loop in that worth noticing: the window ceiling is chosen from measured
rate, SUPE raises the measured rate, a wider window makes fuller trains, and fuller
trains raise it again. Resource parts are also the one traffic that never needs a
delivery proof — they are link traffic, so they never enter the pending-proof set
of §5 at all, and only the finished resource is proved, once, at the end.

**Each train ends by arithmetic, not by agreement.** The receiver returns when it
has the stated number of packets or when the stated length expires, whichever
comes first; the sender returns when the last packet is out. Both outcomes are
known from that MANIFEST alone, and neither side's arithmetic depends on the
other's.

**Nothing is acknowledged and nothing is repeated.** A packet lost here is lost
the way a packet on the main channel is lost, and the layers above deal with it as
they always do — delivery proofs, channel windows, resource part requests. Adding
repair would mean holding traffic for the sender and negotiating over it. What a sender does get is the peer's
MANIFEST, which tells it how the train landed — enough to choose a lower budget
next time, which is what the information was wanted for.

**One failure path, the same for both sides: go back to the main channel.**
Nothing can be renegotiated on this channel, because any adjustment would have to
be inferred by both sides in lockstep while neither can hear the other. Both
deadlines follow from regime constants and the resolved modulation, so both sides
know them without exchanging anything, and the duration announced in the GRANT
covers the worst case for everyone else.

## 9. Reticulum announces are not ours to touch

Announces a node originates go out as the daemon hands them over, and relayed
announces likewise. No buffer, no replay, no batching, no pacing. An announce is
the daemon's decision and its timing is part of what it decided.

The one setting that mentions announcing therefore concerns SUPE's own frames
alone: `announce_interval` is the gap between a node's SUPE_ANNOUNCE2 frames and
governs nothing a Reticulum announce does.

## 10. What is learned, and where it is filed

Everything below is learned from traffic that was going to happen anyway. Nothing
is measured on purpose, and nothing is transmitted in order to measure.

An announcement from a node nothing else has named yet is still worth keeping.
It carries four bytes of each identity and the capabilities together, which is
enough to file against, and the node's own Reticulum announce supplies the rest
whenever it comes. Discarding it instead costs a whole announce interval before
that peer can be detoured with — the frame is the introduction.

Against the identity hash, once its SUPE_ANNOUNCE2 has been heard: capabilities,
maximum transmit power, whether it honours adaptive power requests. That entry
serves every destination the node owns and, if it is a transport node, all
traffic relayed through it — its identity hash is the address that traffic is
sent to.

Against the tag: path loss, never a bare signal level. Every pair is a level
measured here and the transmit power that produced it, stated by the other
side. **A detour yields three such pairs, plus the one reading that cannot be a
pair:**

| Reading | Taken by | From | At |
|---|---|---|---|
| A → B headroom | B | its own reading of the START — which states no power and is adapted (§15), so it pairs with nothing. What it is instead is margin against the hailing demodulation floor at A's *current* power, and that is exactly what the budget choice runs on | the hailing configuration |
| B → A pair | A | its own reading of the GRANT, against the power B stated | the hailing configuration |
| A → B pair | B | its own reading of A's train, against the power A's MANIFEST stated | the detour's modulation |
| B → A pair | A | its own reading of B's train, against the power B's MANIFEST stated | the detour's modulation |

Nothing in that table is a reciprocity assumption. Every reading is taken by
the node that will use it; the three pairs come from frames whose transmit
power the other side stated in the frame itself, and the one bare reading is
consumed by the only decision it is sufficient for.

Two bindings that save a slow first detour:

- **Links inherit.** When the packet being sent is a link request, both sides
  compute the same link identifier independently and file everything under it, so
  all later traffic on that link opens at the peer's best budget.

  The dialled side has to file it against the *node* as well, and the START's
  sender identity is the only thing that lets it. A link request names nobody —
  that is Reticulum's design — so a link dialled to us is otherwise anonymous,
  and our whole side of the session, the link proof first, flies on the shared
  channel until something else eventually names the pair. Arriving as a detour's
  cargo it is not anonymous at all: the node that asked for the detour is the
  node that dialled, so the identifier is filed against it as the cargo lands
  and the very first frame back can detour.
- **Relays file per packet.** A relay handling a packet that may attract a proof
  records the sender's capabilities and signal against the reverse-table entry,
  not against the tag — the tag that transaction opened on was the relay's own
  transport identity, which every neighbour relaying through it shares.

## 11. Failing well

- **No GRANT by the deadline** — the peer is away on someone else's detour, did
  not hear the START, or heard it and could not answer in time. The three are
  told apart at the *first* deadline, which expires when the GRANT must have
  **begun** rather than when it must have arrived (§14.7): a frame on the air at
  that instant is the answer being delivered, and only an idle channel is
  silence. Silence there is answered by sending the START again, byte for byte
  so its hash still names the same request, inside the same request and without
  advancing anything below — no strike, no power step, no ceiling step. Once. **A START that
  draws no GRANT is the cheapest presence test this protocol has**, and it is what
  absence is concluded from: 31 ms of shared channel and a deadline, against
  roughly 760 ms to transmit a full packet into an empty room. Because the peer
  chooses the channel and the modulation, there is nothing to walk down and no
  escalation across the raster — the ladder that a sender-chooses protocol needs
  does not exist here. What escalates instead is the *request itself*, along the
  two axes the requester still owns:

  **Power up, ceiling down.** Each retry goes out at more power than the last, the
  third at the configured maximum (§15), and each asks for a lower ceiling nibble
  than the last, the third asking for budget 0. The first covers a peer that
  simply did not hear us; the second covers a peer that heard us and could offer
  nothing it thought we would accept. Neither is knowable from silence, so both
  are tried.

- **The absence ladder, in full.** On a peer with no absence record:

  1. **Request** (and, on an idle channel, the same request once more). Silence,
     so wait a short randomised interval — long enough to outlast somebody
     else's detour, which is the likeliest cause. The interval paces *requests*,
     so it runs from the request rather than from the moment it was given up on:
     the deadline just spent waiting is already time spent not asking. Then
  2. **request again**, more power, lower ceiling. Silence, wait again, and
  3. **request a third time**, at maximum power and budget 0. Silence.

  Then the peer is **absent for one minute**, and traffic addressed to it is
  **dropped** rather than transmitted into the void — safe, because link data,
  channel traffic, resource parts and proofs all have retry or receipt machinery
  above (§12). Three requests is about 93 ms of shared channel, an eighth of what
  one full packet would have cost.

  **A peer that already has an absence record gets one request, not three.** The
  ladder exists to establish absence, not to re-establish it; once established,
  each subsequent attempt is a single request at maximum power and budget 0, and
  silence renews the minute. The cost of a peer that is genuinely gone therefore
  falls to one short frame a minute.

- **Absence is cancelled by any evidence of life, not by the clock alone.**
  Hearing the node at all clears the record immediately and restores the full
  ladder: its Reticulum announce, its SUPE_ANNOUNCE2, a GRANT it sends to somebody
  else, or a packet from it we carry or relay. Talking to a third party is as good
  as talking to us — better, in fact, since it proves both presence and that the
  radio is working. This is what keeps a busy transport node, which is away on
  other people's detours constantly, from being written off by the one mechanism
  that would hurt most.
- **A refusal is not a failure.** It says how long not to ask again, which silence
  cannot. Honour it: a node that has told us it is out of airtime for the hour and
  is asked again in a second has been given a reason to stop answering at all.
- **The answering node owns channel quality, and learns from it.** If the detour
  fails after a GRANT — no MANIFEST arrives — the channel it chose is the prime
  suspect, and it is the node holding the evidence. It should not choose that
  channel again for that peer for a while. The requester learns nothing here and
  should not try to.
- **Absence is a provisional verdict, not a finding**, and every part of the
  ladder above is shaped by that. The same silence is produced by a peer away on
  someone else's detour, and a busy transport node is away constantly — it is the
  tag most traffic is addressed to, so it is both the node most likely to be
  mid-detour and the node whose wrongly-declared absence costs most. Three things
  keep the verdict cheap: the interval between requests is longer than a detour,
  the verdict expires by itself, and any evidence of life cancels it outright.
- **Dropping is otherwise a queue policy** (§18), driven by age and depth. The one
  place this protocol drops traffic of its own accord is the absence hold above,
  and it drops there precisely because the alternative is transmitting a full
  packet into an empty room, at twenty-five times the cost of the request that
  already established nobody is listening.
- **Two requesters can want the same tag at once**, and on a segment with one
  transport node they usually do — every neighbour's traffic is tagged with that
  node's identity. The answering node grants one of them; the other hears a GRANT
  quoting a hash it did not send and knows immediately that it was not answered,
  rather than waiting out a deadline. A small randomised delay before requesting
  keeps the collision from repeating in lockstep. The loser must not read this as
  absence — it is the opposite, direct evidence the peer is present and busy.
- **Traffic to anything that is not a SUPE peer** never enters this at all. No
  announcement heard means no request made, and the packet goes on the main
  channel exactly as it would on an interface with SUPE switched off.
- **Never detoured** — Reticulum announces and path requests. They are broadcast,
  so there is no tag to name and no single peer to meet; they go on the main
  channel with no SUPE involvement. This is also why no reserved wildcard tag is
  needed: the radio dwells on the main channel by default.
- **Expired regime** — a node whose regime is past its date stops speaking SUPE
  and operates as a plain interface. Its neighbours discover this the ordinary
  way, by it not appearing.
- **Deafness window** — a node away on a detour misses main-channel traffic.
  Announces repeat on their interval and path requests are retried, so with short
  detours this is noise against announce cadence. §18's worker radios remove it
  entirely where a second radio exists.
- **A packet lost on the unicast channel** is lost exactly as one lost on the main
  channel is, and the layers above handle it identically.
- **Duplicates** from a partially completed detour followed by a main-channel
  resend are absorbed by Reticulum's duplicate hash list.
- **Reordering** is safe: sequence numbers in channels, hash maps in resources,
  message-level handling above.

## 12. Staying invisible to the daemon

Nothing here alters what is on the wire at the Reticulum layer or what the daemon
sees. The modem delays and reroutes frames; the packets themselves pass through
untouched.

The one real interaction is arithmetic. The daemon derives receipt timeouts, link
keep-alive intervals, channel windows and the announce bandwidth cap from the
interface's declared bitrate. Because the modem varies modulation per transaction
and adds rendezvous latency, that declared number must describe the *effective*
rate including detours, not the unicast channel's rate — otherwise the far end of
exactly the paths this speeds up reports delivery failures.

**The concrete mechanism to watch is Channel's retransmit deadline**, which is
`2.5 × RTT × (ring + 1.5)` with five tries tearing the link down. A packet held
through a request, an answer, a retune and a train can trip it, and Channel then
resends a packet the modem is still holding — duplicate airtime, which is the
exact opposite of the win. There are two ways out and the choice has to be made
explicitly rather than discovered: surface the queue delay far enough upward that
the round-trip estimate and the timeouts derived from it inflate to match, or
bound a detour to complete inside the budget that already exists.

**A detour is bounded to complete inside the budget** — the second. The first
reaches up into the daemon, which is the one thing this protocol is built not to
do, and it would trade an arithmetic constraint SUPE can satisfy for a change in
software SUPE does not own. Bounding costs a ceiling on how long a transaction
may run, and both regimes already state one or derive one from the field widths
(§14), so the constraint is a comparison rather than a new mechanism.

**The reverse direction cuts both ways here.** The reverse train extends a
transaction, so the ceiling binds sooner — but it is also what carries the
proof Channel is waiting for, so the round trip it must fit inside is one
detour rather than two. The bound holds only if a transaction at its ceiling
fits inside Channel's budget at the *measured* turnaround of §14.7, which is
not yet settled. If it does not, the declared-bitrate route above is the
remaining answer.

## 13. Where it does not apply

- **Interfaces with an access code configured.** The frame is masked end to end —
  flags, hops, addresses and context byte alike — so the modem cannot read an
  address and has nothing to match. SUPE degrades to plain main-channel operation.
- **Peers that have not announced themselves.** A node becomes a SUPE peer by its
  SUPE_ANNOUNCE2 being heard, and nothing else. Traffic to anything else takes the
  main channel untouched, so a mixed segment needs no detection and no fallback —
  the absence of an announcement is the whole of it. The decaying counter is for
  something narrower: a peer that has announced but stops answering requests.

## 14. Regimes

A regime is the complete set of constants two nodes must hold identically in order
to meet at all: the channels, the ladder, the sync words, the timings, the
ceilings and the limits. §3 gives the reason none of it can be configuration. This
chapter is that content, and it is normative — an index on the wire means whatever
the table here says it means, for the version named in the frame that carried it.

**Channel 0 is the hailing frequency in every regime.** It is where the
interface's own configuration lands (§3). A regime-1 GRANT must not name it; a
regime-0 GRANT always does, because regime 0 has nothing else and changes only the
modulation. That is the whole difference between the two regimes at the wire
level.

### 14.1 Regime 0 — Single Channel

One frequency, one bandwidth, and the spreading factor as the only thing that
moves — the whole protocol on the channel the network already hails on. It needs
no channel plan and therefore no regulatory band plan, which is what makes it the
regime a network can run anywhere, and what makes it the floor every node
supports (§3).

| Constant | Value |
|---|---|
| name | Single Channel |
| version | 0 |
| expires | set at build time, no more than 14 days ahead (§3) |
| channels | one: channel 0, the hailing frequency |
| ladder | the spreading factors above the hailing one, at the hailing bandwidth |
| budgets available | to the lowest spreading factor both radio families reach — from an SF7 network, two where both are SX126x and **none where either is SX127x**, whose first entry would be the barred SF6; slower-hailing networks reach further before that bites (§14.3) |
| sync word, budget 0 | the interface's own |
| sync word, above 0 | `0x67`; `0x21` where an entry lands on SF5 |
| train ceiling | none of its own — see below |
| transaction ceiling | none of its own — see below |
| duration encoding | 20 ms steps, which bounds a transaction at 5.1 s regardless |
| transmit power | the interface's `tx_power` |
| airtime accounting | none |

**Regime 0 states no ceilings of its own, and must not invent any.** It has no band
plan and therefore no regulatory basis to draw them from — it runs on the hailing
channel, whose limits belong to whatever regime that network is operating under,
and which SUPE does not own (§3). Where that basis is a duty cycle there is no
maximum transmission time at all, and a fabricated figure would be a limit nobody
imposed. What still bounds a detour is arithmetic rather than regulation: the
duration byte reaches 5.1 s at 20 ms a step, and MANIFEST's length byte reaches
1.275 s at 5 ms a step. Those are the real ceilings here, and they come from the
field widths.

**A regime-0 GRANT at budget 0 is a working answer.** It names the hailing
frequency at the hailing modulation, which sounds like no detour at all — and is
exactly that. What it is *for* is the case where a node has nothing faster to
offer but is present, willing, and has traffic of its own: the pair exchanges
trains where they already stand, with the reverse direction and the measurements
that come with it, and the only thing given up is the rate. That is also why this
protocol needs no probe frame: a request answered is a peer proven present, at
whatever rate the pair can manage.

### 14.2 Regime 1 — ETSI EN 300 220 (863–870 MHz)

**Regulatory basis.** ETSI EN 300 220-2 V3.2.1 annex B table B.1 for the harmonised
non-specific short-range-device bands, their maximum effective radiated power and
their duty cycles; EN 300 220-1 V3.1.1 clause 5.21 and table 48 for adaptive
spectrum access, which every 863–870 MHz entry carrying a duty cycle permits in
place of that duty cycle. [`afa.md`](afa.md) §1 derives the plan below from
them and is the reference for why the numbers are what they are.

**Channels.** Nine, uniform: 500 kHz, 25 mW, all on adaptive spectrum access, none
crossing a band boundary, and at least 200 kHz of clear spectrum between any two
edges — which is what keeps each channel's airtime budget independent of its
neighbours'.

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
that are not available for this use. If out-of-band emission performance does not
support it, the fallback is 250 kHz at the same centre, which costs peak rate on
that channel and no airtime at all.

**The regulation fixes several of this protocol's constants directly**, and they
are not free parameters:

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

**The transaction ceiling is what covers both directions.** A transaction here is
a dialogue by the regulation's own definition — two nodes alternating on one
frequency — which is precisely what the 4 s figure is for, and it comfortably
holds two 1 s trains plus their turnarounds. The 1 s single-transmission figure
still binds each train separately.

**Airtime is a budget and a window, not a percentage.** A regime records the pair
— here 100 s in any 3600 s — rather than a duty figure, because the pair is what
travels. European polite spectrum access is `100 / 3600`, a European duty cycle is
`360 / 3600`, and North American frequency-hopping dwell is `0.4 / 20`: three
regulatory shapes, one field pair, and no special case in the code that reads it. A
percentage would express the first two and lose the third entirely.

**A power limit without its reference is not a limit.** The figure above is
25 mW *effective radiated power* — not effective isotropic radiated power, and not
conducted power at the connector. The three differ by the antenna's gain and by
2.15 dB between the two radiated forms, so a table that records a number without
saying which is being measured is a compliance failure no functional test will
catch. Any regime added later carries the reference alongside the number.

**The channel cap binds the frame, and the frame states what it did.** A node
configured to transmit at 22 dBm transmits at 14 on these channels, and every
power field in every frame it sends from there states 14 (§0.1). This is not a
detail: a node that states its configured power while transmitting at the capped
one hands its peer a path-loss figure wrong by exactly the difference, in the
direction that makes the link look better than it is, and the peer then chooses a
budget and a power on that basis.

Three further consequences worth naming. The 100 ms minimum gap before returning
to a frequency is why the answering node must not grant a channel it has just
used, and why it tracks its own recent grants. The 100 s/h budget is per channel
rather than per band precisely because of the 200 kHz separation above, which is
what the accounting in §14.4 must track. And the duration byte is quantised at
20 ms rather than finer because its range has to reach 4 s.

### 14.3 The ladder, and how a budget resolves

This section is normative and admits no floating-point arithmetic anywhere. Two
implementations that resolve a budget differently do not fail loudly — they set
different spreading factors and the link simply dies, with no error surface
anywhere. Every rule below is stated over integers for that reason, and §14.3.4
gives the conformance test.

#### 14.3.1 What the ladder contains

Given the answering node's family, the requesting node's family, the hailing
spreading factor and bandwidth, and the maximum bandwidth the named channel
permits, an entry `(sf, bw)` is in the ladder if and only if all of:

- `bw` is one of the regime's permitted bandwidths
- `bw ≥ hail_bw` and `bw ≤ channel_max_bw`
- `sf ≤ hail_sf`
- `sf ≥ 7`, **or** both families reach `sf` per §14.6
- `sf ≠ 6` unless **both** families are able to frame SF6 with an explicit header,
  which excludes SX127x (§14.6)
- in regime 0 only, `bw = hail_bw`

The entry `(hail_sf, hail_bw)` is always in the ladder and is always index 0. It
satisfies the rules above by construction.

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
spreading factor**. Both preferences point the same way: at equal rate, take the
entry with more margin. Ties are rare but the rule is not optional, because sort
stability is not a property an implementation may assume of another
implementation.

#### 14.3.3 What an index means

The budget nibble is an index into the ordered ladder above. Index 0 is the
hailing configuration; index *k* is *k* places up. An index beyond the end of the
ladder, or above either node's ceiling nibble, is invalid and the frame carrying
it is discarded.

**The answering node resolves before it grants, and the requester resolves after
it hears.** Both hold every input: family from the START and from the GRANT's own
sender, hailing configuration from the channel they are both camped on, maximum
bandwidth from the channel index in the same byte as the budget. There is nothing
left to infer and nothing to signal.

Ordered from the SF7/BW125 that most networks hail on, on a 500 kHz channel with
both nodes on SX126x, with each entry's cost in margin against that reference:

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

Both columns increase monotonically and no entry is dominated, which is what makes
an index meaningful rather than a lookup (§3). The margin figures come from
Semtech's required-SNR values and are what turn a measured path loss into a budget
choice; [`afa.md`](afa.md) §3 carries their derivation.

The same inputs with a 250 kHz channel maximum give a six-entry ladder — the
first five entries of the table above plus SF5/BW250 on top — and index 6 is
then invalid rather than meaning something else. This is the ambiguity §3
describes, and it is resolved by the channel and the budget always travelling
in the same byte.

**A pair including an SX127x has a ladder of bandwidth changes only.** That family
reaches no SF5 at all, and SF6 there demands an implicit header, which no framing
in this protocol supplies (§14.6). Strike both and what remains from SF7/BW125 on
a 500 kHz channel is SF7/BW250 and SF7/BW500: two entries, 2× and 4× the rate, at
3.0 dB and 6.0 dB. The consequence for regime 0 is blunter — it cannot change
bandwidth, so **an SX127x pair hailing at SF7 has no entries above 0 under regime
0**. A slower-hailing network is unaffected until the ladder reaches SF6: from SF8
such a pair has one, from SF9 two. Where there are none, budget 0 is still a valid
grant (§14.1) and still carries the reverse direction and the measurements; it
simply carries them at hailing rate.

**A budget costs reach as well as margin.** The margin column says what the link
has to give up; what it buys is rate, and what it spends is coverage. From
SF7/BW125 to SF5/BW500 is 11.02 dB — 6.02 dB of it from four times the bandwidth
and 5 dB from the demodulator's signal-to-noise limit — which puts the fastest
entry's reach between 28 % of the hailing channel's in free space and 53 % at a
path-loss exponent of 4, so roughly half in practice. A peer at the edge of
hearing therefore has no budget at all, and the ladder is a facility for the near
population. That is the population a node exchanges most of its traffic with,
which is why this is a tolerable shape rather than a limitation.

**Low-data-rate optimisation is part of what an index resolves to**, not a local
choice: both ends must set it identically or neither decodes. It is compulsory
wherever a symbol lasts longer than 16 ms, which is `2^SF / BW > 16 ms` —

| Configuration | Symbol | Optimisation |
|---|---|---|
| SF11 / BW125 | 16.4 ms | on |
| SF12 / BW125 | 32.8 ms | on |
| SF12 / BW250 | 16.4 ms | on |
| everything faster | under 16 ms | off |

It exists to keep a receiver locked when the crystals at each end drift measurably
within one symbol, and it pays for that by carrying two bits fewer per symbol —
which is why it appears in the airtime arithmetic of §3 as `DE`. A network hailing
at SF11 or SF12 therefore starts with it on and may switch it off partway up the
ladder; the rule above decides that, from the resulting configuration, on both
sides, with nothing transmitted.

#### 14.3.4 Conformance

Prose cannot be executed and two careful readings of it can still differ. An
implementation is conformant with this section if and only if it reproduces
`supe-ladder-vectors.txt` exactly, over the full cross-product of:

- requesting family and answering family, over every value in §14.6
- hailing spreading factor 5 through 12
- hailing bandwidth over every permitted value
- channel maximum bandwidth over every permitted value
- regime 0 and regime 1

Each line gives the inputs, the ladder length, and every `(index, sf, bw,
low_data_rate, sync_word)` it resolves to. The file is generated from the rules
above and is the authority when it and a reading of the prose disagree.

This is the same discipline a cryptographic test vector serves, and for the same
reason: the failure being guarded against is silent divergence between two
independent implementations, which no amount of care in either one can detect.

### 14.4 Airtime accounting

Where a regime states an airtime budget, a node has to be able to answer whether
it has spent it. **This section states the property that answer must have, not
the structure that produces it** — the structure is an implementation's own
business, and every implementation already has an airtime figure of some shape
that this must not be confused with.

Four requirements, and all four are load-bearing:

- **The transmit path reads a verdict, not an arithmetic result.** "May I
  transmit on this channel" is precomputed, per channel, and looked up. A budget
  summed at the moment of transmitting puts a loop on the path that most needs
  to be short.
- **It is fed unconditionally**, not gated on anything being displayed,
  recorded or watched. A figure that exists only while somebody is looking at it
  cannot defend a cap.
- **Every transmission is counted, including ones that achieved nothing** — an
  aborted train, a GRANT nobody heard, a frame cut short. The regulation counts
  emissions, not successes.
- **It errs by no more than the margin the effective cap absorbs.** Whatever the
  recomputation interval, the verdict may be that interval stale, so the
  effective cap is set below the legal one by at least what can be spent in it.
  Set that way, coarse bins are free: the cap is approached slowly and the error
  cannot cross it.

A ring of 10-second buckets, one per channel, satisfies all four — 360 buckets
to the hour at 16 bits of milliseconds each is 7.2 kB for ten channels, credited
at transmit-done with the time on air the radio already computes, recomputed once
per bucket. It is a good answer and not the only one. What it is not is a
reinterpretation of a coarser total kept for some other purpose: a structure that
ages whole buckets out of a short history lets the budget be spent late in one
bucket, aged out, and spent again, which approaches twice the cap inside a true
window. Defending a cap wants a structure chosen to defend it.

**The answering node reads this table before it grants**, which is what makes it
the right chooser: it is the only node that knows its own remaining budget per
channel and its own 100 ms reuse gaps. A grant that would breach either is not
made, and if no channel is available the answer is a refusal with the reason
(§6), not silence.

### 14.5 Sync words

| Where | Word | Why |
|---|---|---|
| hailing channel | the interface's `sync_word`, `0x42` by convention | that channel belongs to the Reticulum network, not to SUPE (§3) |
| any detour, except | `0x67` | furthest from `0x12` and from LoRaWAN's `0x34` that the space allows |
| a budget landing on SF5 | `0x21` | SF5's 32 bins admit no nibble above 3; this is the best of the nine words that fit, and SF5 is empty of other networks (§3) |

A regime-0 detour at budget 0 stays on the hailing frequency at the hailing
modulation and therefore keeps the interface's own sync word — it is not off the
channel in any sense the sync word cares about. Every budget above 0 takes `0x67`
or `0x21` even in regime 0, which is what separates a detour's train from the
shared channel's traffic while both occupy the same frequency.

### 14.6 Radio families

The family nibble names what a peer's silicon can do, in the few respects that
change what goes on the air. The ceiling nibble beside it already says how far up
the ladder a node will go; family exists for the things a ceiling cannot express —
chiefly that one family reaches no lower than SF6, and frames it there differently
from everyone else.

| Value | Family | Parts | Spreading factors | On the air |
|---|---|---|---|---|
| 0 | SX126x | SX1261, SX1262, SX1268, LLCC68 | 5–12 | the reference case; nothing special |
| 1 | SX127x | SX1272, SX1276, SX1277, SX1278 | 6–12 | **no SF5 at all**, and SF6 carries an implicit header, so an entry landing there needs a fixed frame length agreed in advance (§16) |
| 2 | SX128x | SX1280, SX1281, SX1282 | 5–12 | 2.4 GHz only, and 203/406/812/1625 kHz bandwidths — no overlap with a 863–870 MHz regime, so it can only appear under a regime of its own |
| 3 | LR11x0 | LR1110, LR1120, LR1121 | 5–12 | as SX126x for these purposes |
| 4 | LR2021 | LR2021 | 5–12 | as SX126x for these purposes |

Both rules that follow are the **answering node's** to apply, because it is the
node that chooses:

- **Every entry in the ladder must be reachable by both**, which §14.3.1 states as
  a membership rule rather than as a check afterwards. A pair of SX126x nodes on
  an SF7 network has eight entries above 0 on a 500 kHz channel; a pair including
  an SX127x has two, and under regime 0 none.
- **SF6 is not offered where either side is family 1.** It demands an implicit
  header there, and no framing in this protocol supplies the fixed length that
  would need. §16 carries the exception that would recover it.

The nibble holds sixteen values against five families, which is room enough that a
new part gets a number rather than a compatibility rule.

### 14.7 Turnaround, retune and deadlines

Every deadline in this protocol is derived from the constants below and a time
on air. They are regime constants (§3) and all of them are currently estimates
rather than measurements — the open item that most wants
[`simulation.md`](simulation.md), because a bench measurement of them needs
instrumentation we do not have and a simulator charges them by construction.

| Constant | Value | What it is |
|---|---|---|
| turnaround | 25 ms | The longest a node may take between the end of a frame it is answering and the start of its answer, on the same channel and configuration. It is what a GRANT must beat after a START, and what a MANIFEST must beat after a train. |
| retune gap | 1 ms | The interval both sides observe between the last frame on one channel and the first frame on another. It exists to cover the synthesizer, not the software. |
| manifest lead | 3 ms | What the requester adds after its retune before its MANIFEST, spent from inside the turnaround allowance the deadline already budgets. The answering node is in its own transmit-done, tune and arm path when the GRANT ends, and a fast budget's preamble is shorter than that path's scheduling jitter — the lead is what puts its receiver on the air first. |
| train gap | 2 ms | The receiver-flip interval before and between the packets of a train: RX-done must be serviced, the frame read out and dispatched, and receive re-armed before the next preamble flies. Counted into every stated train length, so the far end's deadline covers it. Nothing else rides in it — the sender's next packet is already built. |
| guard | 10 ms | Slack added to every derived deadline, absorbing scheduling jitter at both ends. |
| per-packet overhead | 16 bytes | What a packet's preamble and header cost, expressed in payload bytes, for the load field of SUPE_START (§6). Not a timing constant, but the same kind of thing: a figure both ends must charge identically or they will size the same queue differently. |

**The retune gap is deliberately small, and the reason is that the silicon is
fast.** A synthesizer hop on an SX1261/2 is 30 µs, a full wake from standby about
150 µs, and the transmit-receive switch under 600 ns (datasheet table 3-7); the
power-amplifier ramp is 10 µs to 3.4 ms depending on how it is configured. All of
that is microseconds, and the BUSY line signals readiness directly, so a driver
that waits on it is already correct without any interval at all. The millisecond
is there for the scheduling gap between a completion interrupt and the task that
acts on it, not for the radio — and it is paid twice per detour, which is why it
is not padded.

**The turnaround is generous, and the reason is that it is free.** It appears only
inside deadlines: a node that answers in 3 ms is not penalised for the constant
being 25, and no airtime is spent on it by anybody. What it buys is tolerance for
a responder whose radio task is behind a storage commit or a display update.
Shrinking it would tighten the deadlines and gain nothing, because nothing waits
out a turnaround that has already been satisfied.

The deadlines that follow, all derived and none transmitted:

| Waiting for | Armed at | Deadline |
|---|---|---|
| GRANT, on the main channel | end of the START | Two stages. `turnaround + guard` — no time on air in it — expires when the GRANT must have **begun**, and asks the receiver whether a frame is arriving. One is: wait it out, `toa(GRANT, hailing) + guard`. None is: nobody answered, established half a frame earlier and against evidence rather than against an estimate of a frame that was never sent |
| first MANIFEST, on the detour | end of the GRANT | `retune_gap + turnaround + toa(MANIFEST, budget) + guard` — the manifest lead spends part of the turnaround, never extends it |
| a train, on the detour | its MANIFEST processed | the `length` that MANIFEST stated, plus `guard` |
| reverse MANIFEST, on the detour | end of the requester's train | `turnaround + toa(MANIFEST, budget) + guard` — armed only when the GRANT's reverse flag declared one; without it the requester's own last frame ends the transaction and nothing is armed at all |

Every one of them is computable by both sides from the frames already exchanged,
which is what lets a failure be a silent return rather than a negotiation.

## 15. Adaptive transmit power

Part of SUPE, gated on `SUPE.adaptive_txpower`, and applying to unicast only:
SUPE_START, every frame inside a detour, and any packet sent plainly to a single
peer. Never to anything broadcast — announces and path requests.

**SUPE_GRANT is never adapted, and SUPE_START always is.** The two look alike and
are not. A GRANT is the frame every third party holds traffic on and measures path
loss from, so its reach is the reach of the hint, and it goes out at maximum. A
START is addressed to one node and nothing depends on strangers hearing it — the
GRANT it draws carries the hold — so it is the natural power probe, and §11's
absence ladder is that probe run to conclusion: each retry at more power, the last
at maximum. A peer that answers only the third request has told the controller
exactly where the cliff is, on frames it was going to spend anyway.

**Maximum means the node's configured `tx_power`, capped by the channel's
regulatory limit, never the radio's ceiling.** The range this controller works in
is bounded above by the lower of those two and below by what the part can do, so a
radio held to 14 dBm on a channel that permits 14 stays there and simply has a
shorter run to walk. Nothing here may raise a node above either bound, under any
failure, for any reason.

**Start at the top and walk down on evidence.** The controller's initial power for
a peer it has never adapted to is the configured maximum, not a computed value.

**Do not compute an absolute power from a path loss and a modelled sensitivity.**
It produces plausible arithmetic and implausible answers: a 49 dB path loss
against a −121 dBm modelled sensitivity and 10 dB of margin gives −62 dBm, which
is nonsense, and the only thing between that and a dead link is a floor constant.
A floor doing that much work is not a safety net, it is the design. The measured
term is real; the modelled one is not, and a controller that treats them alike
fails silently and completely. §14.3's insistence on integer rules over formulas
guards the same hazard in a different place.

**Power is derived from a learned offset, never stored as an absolute:**

```
power = clamp( maximum − offset , floor , maximum )
```

where `offset` starts at zero and only ever moves on evidence about *this* peer.
The measured path loss and the entry's margin cost (§14.3) inform how large a step
to take and where to stop, but they do not set the power directly — they bound the
search rather than replacing it.

- **Failure raises the power fast.** A single miss cuts the offset by about 6 dB;
  a peer that has gone silent goes straight back to maximum. Being wrong downward
  costs connectivity, so recovery is immediate and large.
- **Success lowers it slowly**, 1–2 dB, so any overshoot past the cliff is small
  and the next failure recovers it.
- **The decrement is gated on evidence, not time.** Require a number of successful
  exchanges with *that* peer since the last change. "Nothing went wrong lately"
  means nothing if nothing was sent, and a controller that dials down on a timer
  will walk a quiet link into the ground.
- **Where it broke is remembered.** After a failure at a given power, do not return
  below it plus a margin for a while, on a decaying floor. Without that the loop
  oscillates across the cliff instead of settling above it.

**The reverse MANIFEST is what supplies the evidence this loop needs, and the
GRANT's reverse flag is what makes its absence readable.** A one-directional
unacknowledged detour tells a sender nothing about whether its train landed,
and a loop built on one has to lean on Reticulum's own delivery signals, which
arrive late and only for traffic that is proved at all. Here a transaction
whose GRANT declared reverse traffic ends with the peer's MANIFEST, which both
proves the train arrived and states the level it arrived at (§10) — a complete
round trip with a measurement in it, at no cost. A transaction whose GRANT
declared nothing ends in silence by design, and that silence teaches the
controller nothing: only a *declared* MANIFEST that fails to arrive is
evidence.

- **The peer's MANIFEST comes back, reporting good margin** — the power suffices
  and there is room. Sustained, this walks the offset up.
- **The peer's MANIFEST comes back reporting thin margin** — hold, do not reduce.
- **A declared MANIFEST never comes** — the power may have been too low, or the
  peer may be gone. Raise immediately, record the failed power in the floor,
  and do not distinguish the two cases: both want more power next time.
- **No GRANT, then a GRANT one rung up the ladder** — §11's escalation, read as a
  power measurement. The rung that succeeded is the floor plus a margin; the rungs
  that did not are below it. This is the cleanest reading the controller ever gets,
  because nothing else varies between the attempts.
- **No GRANT even at maximum** — the peer is absent (§11) and nothing about power
  is learned. Do not move the offset on this.

## 16. Open items

- **The §14.7 constants, measured rather than assumed** — the turnaround, the
  retune gap, the manifest lead and the train gap. They set every deadline in
  the protocol and the dead air inside every train, and with them how cheap a
  failed request is. The measurement wants the responder's
  received-to-retuned-to-armed path and the receiver's flip time between
  back-to-back frames, and it is the first thing
  [`simulation.md`](simulation.md) should be pointed at. The lead and the gap
  in particular are ceilings to shrink: the sender's next packet is prebuilt,
  so both cover the *other* end's software alone.
- **A fixed-length framing exception for SF6 on family 1.** SF6 demands an implicit
  header on SX127x ([`afa.md`](afa.md) §4.1), so the protocol skips it and leaves
  such pairs with bandwidth entries alone — and, on a network hailing at SF7, with
  nothing at all under regime 0 (§14.3). An exception carrying a length agreed in
  advance would recover a spreading-factor entry for every pair including an
  SX127x.
- **Out-of-band emission performance, and whether channel 9 can be 500 kHz.** The
  channel fills band N edge to edge between two alarm allocations (§14.2), so what
  decides it is the skirt of a 500 kHz LoRa signal against the out-of-band limits at
  868.6 and 869.2 MHz — not the far-field spurious limits that dominate most
  compliance work. Two things to establish and neither is settled here: what the
  part's datasheet states for transmitter spectral performance and what filtering
  the reference designs assume to meet EN 300 220-1; and which clause the band-edge
  limit actually falls under. The fallback is already known — 250 kHz at the same
  centre — so this decides only whether the fallback is needed.
- **Preamble handling when a budget lands on SF5 or SF6.** Both have modified
  preamble and sync behaviour on the SX126x; confirm `0x21` at SF5 and `0x67` at
  SF6 land as §3's arithmetic says. The bin arithmetic is settled; the silicon's
  treatment of it wants checking.
- **Regime 1.** Whether hailing and detour traffic land in one duty budget or two.
- **The reference count on tag entries.** Three bytes with a thousand live
  entries collides internally often enough to matter; confirm a count is enough
  and that nothing needs the full address kept alongside.
- **Refusal reasons.** The channel nibble carries one when the budget nibble is 15
  (§6), and the set is not yet fixed. It wants to be small and to map cleanly onto
  backoff durations, since that is the only thing a requester does with it.

## 17. Deferred

- **Multi-round transactions.** More than one train each way inside a single
  detour. The frames already support it — a MANIFEST with a nonzero count may
  follow any other — but the ceilings, the fairness against third parties
  holding traffic, and the interaction with §12's bound all want thinking about
  before it is allowed.
- **Repair.** If a train is repeated at all, the hashes belong in the *receiver's*
  MANIFEST after the fact, not in the sender's beforehand — and only when the
  count, or a checksum over the whole train, disagrees. A manifest that listed a
  hash per packet would spend airtime on every successful train to serve the rare
  failed one, which is backwards. The reverse MANIFEST is the frame this would
  live in, so the structural obstacle is gone and only the decision remains.
- **Destination-scoped proof returns.** Naming the destination a proof belongs to
  rather than the packet hash, so a burst of messages to one destination collapses
  to a single table entry. It costs precision — the destination's owner and every
  other recent sender wake too — so it is only worth doing if pending-proof entries
  become a real memory pressure. They are eight bytes and short-lived, so probably
  not.
- **Remembering which channels work, per peer.** This belongs entirely to the
  answering node, which is the one that chooses. Two halves: what
  it knows about itself — an optional bitmask in the capabilities marking channels
  that never work where it stands, persistent local interference on part of the
  band, a warehouse interrogating tags, a neighbour's equipment parked on one
  frequency — and what it observes, which channels have carried a detour to this
  peer and which have only ever produced silence. Both want a rule for how a
  channel is judged bad, and how that judgement is forgotten.
- **Holding traffic briefly** for addresses suspected to be away on someone else's
  detour.
- **Re-arming the hint during a long detour.** One short frame from a third party,
  restating that the tag is away until T, for nodes that missed the GRANT. Wants a
  rule for which third party sends it and for stopping before the pair returns.
- **Occupying the medium while a peer is away**, so nodes that cannot hear the
  hint defer instead of transmitting into a deaf receiver. Pays clearly in a star
  around one gateway, where everything in earshot is addressed to the absent node.
  Cheap forms are indistinguishable from jamming, and a second radio (§18) solves
  the same problem outright, so this is the single-radio gateway's workaround and
  nothing more. Not to be built without simulating it first.
- **Asymmetric configurations** per direction. Both directions being measured on
  every detour (§10) makes this plausible for the first time — but
  both trains share one channel and one modulation, and splitting them means two
  retunes inside a transaction.

## 18. What this protocol assumes of an implementation

SUPE is a wire protocol and this document is not an architecture. But three
implementation properties are load-bearing enough that getting them wrong makes
the protocol behave badly on air rather than merely making the code awkward, and
they are stated here for that reason alone.

**Detour airtime is accounted separately from the hailing channel's.** §8's
compounding argument, and the whole claim that a detouring node stays cheap,
depends on it. Credit a detour's transmissions against the hailing channel's duty
figure and the effect vanishes silently, with nothing failing and nothing to see.

**Whether to detour is one function, in one place.** Its inputs are the peer, the
queue and the channel; its output is `no`, `now`, or `wait until t`. The protocol
deliberately does not specify it, because the right answer varies with traffic
shape and nobody has measured it yet — §8's own break-even table shows a lone
19-byte packet losing while a 100-byte one wins by half. What the protocol does
require is that the decision be *findable*: a policy scattered across a transmit
path cannot be measured, cannot be replaced, and cannot be simulated.
[`simulation.md`](simulation.md) §7 is written against exactly that signature.

**A radio carrying a detour need not be the radio that hails.** The deafness
window of §11 exists only because one radio cannot be in two places. A node with a
second radio in the same band can stay on its hailing channel throughout, and the
protocol is entirely indifferent to which radio transmits what — nothing on the
wire changes. What that costs an implementation is that a detour must ask for *a
radio able to reach this channel* rather than assuming its own, which is a seam
worth building before it is needed rather than retrofitting into a state machine
that assumes otherwise. `SUPE.worker` (§4) marks a radio as available for this and
presents no interface to the daemon; workers serve any hailing channel in their own
band, and crossing bands is a routing problem rather than this protocol's.
