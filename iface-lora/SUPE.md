# SUPE — Spectrum Utilization and Performance Enhancements

> Status: **design. Not implemented.**
>
> SUPE moves unicast traffic off the shared LoRa channel onto short private
> high-rate detours, entirely inside the modem, with the Reticulum daemon
> unmodified and unaware. It specifies how two nodes that know nothing about each
> other agree parameters and leave, using only what is visible in Reticulum packet
> headers.
>
> Everything normative is here: the frames in §0.1, the regimes with their
> channels, ladder, sync words and limits in §14, and adaptive power in §15.
> Companion documents carry derivation rather than specification —
> [`afa.md`](../afa.md) for how the channel plan and the ladder's margins follow
> from the regulation, [`psa.md`](../psa.md) for access to the hailing channel,
> which SUPE does not own, and
> [`proper-air-protocol.md`](proper-air-protocol.md) for the method of pricing a
> frame against a turnaround.
>
> A node speaking it is a SUPE RNode.

## 0. The flow

```
main channel (whatever this network hails on — the baseline for everything
              below; everyone camps here, carrier-sensed)

  telling the neighbourhood who we are — once per announce interval
    A→*   SUPE_ANNOUNCE1 {type, regime/version, channel + duration}       3 B
          └─ same SF and BW, only the frequency changes
          └─ every SUPE node follows; nothing in it says whose it is
          └─ regime 0: skipped, ANNOUNCE2 goes out here instead

  moving traffic — per detour
    A→*   SUPE_START     {type, regime/version, tag,
                          channel/step, duration, [sender identity]}    7 or 10 B
          └─ everyone else: hold traffic addressed to tag for duration,
             and to the sender's identity as well when it is named
          └─ nothing else is said here, and nothing answers here

announce channel (regime 0: the main channel, at hailing modulation)

    A→*   SUPE_ANNOUNCE2 {type, regime/version, capabilities,
                          power this went out at,
                          4-byte identity hash × count}           5+4·count B
          └─ hashes last, so the count needs no byte of its own

unicast channel (regime 0: the main channel, one or more steps up)

    B→A   HERE      {type, power of this frame, signal I heard the START at,
                     [capabilities]}
          └─ no HERE by the deadline → A returns to the main channel and
             offers again, at a lower step or not at all
    A→B   MANIFEST  {type, power the START went out at, power of this train,
                     [capabilities], count, length of the train}
          └─ no MANIFEST by the deadline → B returns to the main channel

    A→B   the packets themselves × count — ordinary Reticulum frames in the
          interface's ordinary framing, not SUPE frames at all

          └─ B returns to the main channel on the count, or when the stated
             length expires, whichever comes first
          └─ A returns when the last packet is out

  peer never appeared — escalate down, then conclude
    A→*   SUPE_START again, one step lower and on a different channel —
          sensed clear, and never one already tried in this transaction —
          down to step 0: the hailing configuration itself, moved off the
          shared channel, and the most robust offer that can be made
          └─ still no HERE → the peer is not there; drop the packet

  is anyone there? — a probe, not a detour: step 0, no channel change
    A→*   SUPE_START     {…, channel unchanged, step 0}                   7 B
    B→A   HERE           {…}                                             4 B
          └─ HERE → send the traffic plainly, here, no MANIFEST
          └─ silence → the peer is absent; drop, and hold it absent
          └─ this is regime 0's whole answer to "is it worth transmitting",
             since it has no second channel to offer
```

One detour carries traffic in one direction, ends by arithmetic, and is not
acknowledged.

### 0.1 Every frame and its fields

Field order, widths and type values below are normative.

Every SUPE frame opens with its type byte, and that byte plus the regime and
version nibbles behind it determine the frame's permitted length exactly:

| Type | Frame | Sent on | Length |
|---|---|---|---|
| `0xC0` | SUPE_START | hailing channel | 7, or 10 with `sender_ident` |
| `0xC1` | SUPE_ANNOUNCE1 | hailing channel | 3 |
| `0xC2` | SUPE_ANNOUNCE2 | announce channel, or hailing in regime 0 | 5 + 4·count |
| `0xC3`–`0xC7` | reserved | | discard |
| `0xC8` | HERE | unicast channel, or hailing in a probe | 4 |
| `0xC9` | MANIFEST | unicast channel | 7 |
| `0xCA`–`0xDF` | reserved | | discard |

The grouping is mnemonic only — `0xC0`–`0xC7` for frames that begin something,
`0xC8` upward for frames inside a transaction — and it is not a check a receiver
can apply, because HERE answers a probe on the hailing channel and ANNOUNCE2 is
sent there outright in regime 0. A reserved value is discarded exactly as a wrong
length is (§3). The deferred frames of §17 take numbers from `0xCA` upward.

**Levels and powers are `dBm + 64`, read as a signed byte.** Every field carrying
a transmit power or a received signal level uses that one encoding, so the
representable range is −192 dBm to +63 dBm at 1 dB. The offset is what buys the
bottom: a plain signed byte stops at −128 dBm and receivers already report below
−130, while nothing needs the top — +63 dBm is two kilowatts, and the regulation
caps radiated power at 14 dBm (§14.2). More sensitive receivers are a great deal
likelier than five-gigawatt transmitters, so the range is spent accordingly.

**Signal-to-noise is a separate signed byte in quarter-decibels**, covering
−32 dB to +31.75 dB. It is a ratio rather than a level, needs no offset, and that
is both the range and the resolution the silicon reports natively.

**The main channel carries only what it takes to get two nodes onto another
channel.** Nothing else belongs there. Capabilities, transmit powers, identities,
measurements and verdicts all move to a channel that is faster and bothers only
the two parties involved.

**Main channel** — both frames fit the 7-byte quantum (§3), about 31 ms each.

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **SUPE_START** | type | 1 | `0xC0` — `110` protocol bits then 5 bits of type (§3) |
| | regime / version | 1 | a nibble each — fixes how everything after it is read |
| | tag | 3 | first three bytes of the packet's first address field |
| | channel / step | 1 | a nibble each: channel index, and how many steps above the hailing configuration (§3) |
| | duration | 1 | in 20 ms steps; the range must reach the transaction ceiling (§14) |
| | sender identity | 0 or 3 | first three bytes of the sender's own identity hash, under `sender_ident` (§4); presence is implicit in the frame length |
| | | **7 or 10** | |
| **SUPE_ANNOUNCE1** | type | 1 | `0xC1` |
| | regime / version | 1 | a nibble each |
| | channel / duration | 1 | a nibble each: where ANNOUNCE2 follows, and how long it takes; spreading factor and bandwidth stay at the hailing values |
| | | **3** | skipped entirely in regime 0 |

**Capabilities** — two bytes, and never on the main channel:

| Field | Size | Meaning |
|---|---|---|
| family / top step | 1 | a nibble each — family, which decides what this radio can reach at all; and the highest step this node will accept, which family and the hailing configuration already bound but which a node may set lower as policy |
| maximum power | 1 | `dBm + 64`, signed; the adaptive-power flag rides in the top step nibble's spare bit rather than here, since this byte is fully spent |

**Announce channel** (the main channel, at hailing modulation, in regime 0)

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **SUPE_ANNOUNCE2** | type | 1 | `0xC2` |
| | regime / version | 1 | a nibble each |
| | capabilities | 2 | as above |
| | power | 1 | `dBm + 64`, signed — what this frame went out at, so a listener can turn its own reading into path loss |
| | identity hashes | 4 × count | first four bytes of each identity this node holds — last, so the count is implicit in the frame length |
| | | **5 + 4·count** | |

**Unicast channel** (the main channel, some steps above hailing, in regime 0)

| Frame | Field | Size | Meaning |
|---|---|---|---|
| **HERE** | type | 1 | `0xC8` |
| | power | 1 | `dBm + 64`, signed — what this frame went out at |
| | signal strength | 1 | `dBm + 64`, signed — what the START was heard at |
| | signal-to-noise | 1 | signed quarter-dB — what the START was heard at |
| | | **4** | the first thing on the channel, sent the moment the peer has retuned |
| **MANIFEST** | type | 1 | `0xC9` |
| | power of the START | 1 | `dBm + 64`, signed — what the opening frame went out at, which is what turns the peer's reading of it into path loss |
| | power of this train | 1 | `dBm + 64`, signed — what this and every packet frame goes out at |
| | capabilities | 2 | the sender's — the peer may never have heard its announcement |
| | count | 1 | packets to expect |
| | length | 1 | how long the train takes, in 5 ms steps; 1 s is the ceiling |
| | | **7** | |
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
  slower hailing configurations. Shaving below it buys nothing.
- **On the unicast channel, also count frames, not bytes.** A short frame there is
  preamble-dominated, so folding a field into a frame that is already going out is
  nearly free while adding a frame costs its preamble plus a turnaround. That is
  why powers and capabilities ride inside HERE and MANIFEST rather than in an
  opening exchange of their own.

No SUPE frame carries a cyclic redundancy check (§3); the Reticulum frames of a
train keep theirs, being none of SUPE's business. The regime and version nibbles
ride only on frames a stranger may have to judge — the two main-channel frames and
ANNOUNCE2. HERE and MANIFEST omit them: the START that opened the detour fixes the
dialect for both.

## 1. The idea

One robust shared channel that every node camps on, carrying Reticulum announces,
path discovery and anything broadcast, plus per-pair private detours for unicast
traffic. The detour is negotiated in one short frame on the shared channel and
runs at whatever rate the pair's measured link supports.

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
nibble** in one byte — the two main-channel frames and ANNOUNCE2, per §0.1. The
pair fixes how every byte after it is read, so block lengths never need
announcing. Each version of each regime carries an expiry date, fixed when the
software is built. Past that date a node neither sends nor accepts frames naming
it and falls back to plain main-channel operation, so an obsolete dialect leaves
the air by itself instead of having to be spoken forever.

| Regime | Version | Meaning |
|---|---|---|
| 0 | 0 | Single Channel — one frequency, one bandwidth, the spreading factor the only thing that moves |
| 1 | 0 | ETSI EN 300 220, 863–870 MHz |

§14 holds both in full: channels, ladder, sync words, ceilings and limits.

**August 2026.** At this stage of development the version of every regime stays at
zero and everything here is expected to change. No build should set an expiry more
than fourteen days ahead of its own build date. A node left unattended for a
fortnight then stops speaking SUPE altogether, which is the intended outcome: far
better than speaking a stale dialect at a network that has moved on.

Regime 0 does not move frequency at all, and does not change bandwidth: its
channel nibble is always zero and is ignored. Its whole ladder is the spreading
factors above the hailing one, at the hailing bandwidth —
two or three steps on most parts, each worth 2.5 dB of rate for 2.5 dB of margin.
Having no second channel, it reaches presence through the step 0 probe of §11
rather than through a floor offer.
SUPE_ANNOUNCE1 has nothing to announce and is skipped: ANNOUNCE2 and the power
ladder go out on the main channel at the hailing configuration, since an
announcement has to reach nodes that know nothing about us. Regime 0's number is
fixed; do not renumber it.

**The ladder is measured from the hailing configuration, not written in
absolutes.** Step 1 is one place faster than whatever this network hails at, step
2 is two, and so on; step 0 is the hailing configuration itself. A network on
SF9/BW125 gets SF8, SF7, SF6 where one on SF7/BW125 gets SF6, SF5 — the same
protocol, the same indices, no configuration anywhere.

Three things make that the right frame of reference:

- **The baseline is shared without being sent.** Two nodes negotiating a detour
  are, by definition, hearing each other on the hailing channel, so they already
  agree on what step 0 means. Nothing has to carry it.
- **It is the floor that matters.** A node we cannot reach at the hailing
  configuration cannot be reached anywhere else either — every step up trades
  margin for rate. So the interesting question is never "what modulation" but
  "how many steps of margin do we have above the one that already works", which is
  exactly what the measurements in §10 produce.
- **The ladder is monotonic** in both directions (§14.3): every step is faster and
  needs more margin than the one below, and no entry is dominated. That is what
  makes a step count meaningful rather than a lookup.

What a step resolves to in absolute terms is still what governs the radio, so the
sync word, the header mode and the family limits all follow the *resulting*
configuration rather than the step number. A step landing on SF5 takes `0x21` and
is unavailable on SX127x; the same step index on a slower network lands somewhere
else entirely and neither applies.

**The type byte is 3 bits of protocol and 5 bits of type.** The protocol bits are
`110`, so every SUPE frame begins `0xC0`–`0xDF` and there are 32 frame types
available. That range cannot collide with Reticulum: SUPE requires an interface
with no access code (§13), and without one the daemon's flag byte never has its
top bit set — the access-code path is the only thing that sets it, and it sets it
unconditionally. So anything from `0x80` up is not Reticulum on a SUPE interface,
and we sit in the middle of that space.

**A frame whose length is not one the regime, version and type allow is
discarded.** The permitted set is tiny — one length for most frames, two for
SUPE_START, and a count-derived length for SUPE_ANNOUNCE2 — so the test rejects
outright rather than merely suspecting, and it is the last cheap filter before we
act on anything.

**Everything an index selects is a constant of the regime.** An index on the wire
means nothing except against a table both ends hold identically, so every such
table is compiled in and keyed by regime and version — and nothing it contains may
be a setting, because two neighbours who configured differently would meet at
different frequencies, steps or sync words and never hear each other. The regime
owns:

- the channel raster the detours draw from, and which indices name which
- the modulation ladder: what one step above the hailing configuration means, and
  each step's coding rate, preamble length, header mode and low-data-rate
  optimisation
- the sync word each resulting configuration takes
- frame layouts and their lengths, which is what makes the length test possible
- the duration encoding — 20 ms steps, whose range must reach the regime's
  transaction ceiling (§14) — and the ceilings themselves
- power ceilings, duty and dwell limits per band
- the retune guard, and the expiry date of the regime itself

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
constants selected by the resulting configuration, not fields on the wire: SUPE_START names the step, so
both sides derive the same word with nothing transmitted and no branch at the call
site. Remember that the two-byte form on SX126x is a nibble expansion, not an
addition ([`afa.md`](../afa.md) §5.4).

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

- SUPE_START at seven bytes fills the second group exactly, and its ten-byte form
  under `sender_ident` fills the third. Either way it sits on a boundary and wastes
  nothing.
- SUPE_ANNOUNCE1 at three bytes fills the first. A fourth byte would have cost a
  whole group for one field, which is why channel and duration share one.

The boundaries move with the hailing configuration, and not always against us: a
network hailing at SF12, where the optimisation is compulsory, puts everything up
to seven bytes in the *first* group, so SUPE_START costs one group there rather
than two.

Those figures assume preamble 8; the interface default of 12 adds about 4 ms to
each. Against roughly 760 ms for a full 500-byte Reticulum packet on the same
channel, the shared-channel cost of a detour is about one twenty-fifth of a single
packet. A network hailing slower moves every figure here and none of the layouts,
which are regime constants and identical everywhere.

**Configurations and channels are indices**, one nibble each, never literal
frequency and modulation values — a byte that named them outright would not fit
the quantum on the network where the quantum is tightest.

## 4. Configuration

All under one prefix. Note what is *not* here: no channel list, no ladder, no
sync words, no durations or limits. Those are regime constants (§3), because both
ends must hold them identically. `s.lora.<n>.frequency`, `bandwidth`,
`spreading_factor`, `coding_rate`, `preamble`, `tx_power` and `sync_word` describe
the hailing channel, and SUPE only reads them.

| Key | Default | Meaning |
|---|---|---|
| `s.lora.<n>.SUPE.enable` | off | Speak SUPE on this interface at all. Off means a plain Reticulum LoRa interface. |
| `s.lora.<n>.SUPE.regime` | `0` | Which compiled-in regime is in force (§14) — channels, ladder, sync words, ceilings, limits, expiry. The only radio choice SUPE exposes. Frames naming an expired regime are ignored. |
| `s.lora.<n>.SUPE.adaptive_txpower` | off | Transmit to each neighbour at a power measured for it, per §15. Off means every frame goes out at `tx_power`. |
| `s.lora.<n>.SUPE.announce_interval` | `30` | Minutes between a node's own SUPE announcements. It governs nothing else; Reticulum's announces are not SUPE's to schedule (§9). |
| `s.lora.<n>.SUPE.sender_ident` | off | Name ourselves in every SUPE_START, so neighbours hold traffic for us as well as for the peer we are servicing (§6). Costs three bytes and one symbol group, and gives up the protocol's default anonymity. A gateway — the node most traffic is addressed to, and the one whose silent absence costs most — should turn it on. |
| `s.lora.<n>.SUPE.announce_bundling` | on | Whether one SUPE_ANNOUNCE2 lists several of our identity hashes or each gets its own frame. Off means the frames must be spread slightly in time (§16). |

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

The mirror of the table above, and what step selection actually runs on: given a
tag about to be sent to, which node is behind it, and what is known about reaching
that node. Three joins build it, all from traffic the modem is already carrying:

| Tag | Resolves to its node by |
|---|---|
| a transport identity | being one already — a relayed announce's first address field is the relaying node's identity hash |
| a destination hash | the Reticulum announce that carried that destination's public key, whose truncated hash is the identity (§2) |
| a link identifier | the destination the link request was addressed to, recorded as the request goes past |

Capabilities and measurements hang off the identity, never off the tag, so a node
with forty destinations costs one capability entry and forty pointers. This is the
table that answers "what step" — §10 is what fills it.

## 6. Messages on the main channel

**Tag** — 3 bytes, always the first three bytes of the first address field of the
packet about to be sent, and nothing more. It has one meaning: an address the
peer holds. There is no type, no flag and no second interpretation, because the
receiver has one flat table (§5), a hit is a hit, and what follows — hop, receive,
hand up, let the daemon judge — never depends on why the entry is there.

That includes delivery proofs, which are addressed to the truncated hash of the
packet being proved. Exactly two nodes hold that hash: the origin, in its receipt,
and the one relay that forwarded the packet, in its reverse table. A packet in
transport names its next hop, so no second relay ever handled it and no third node
ever wakes. The tag is as precise here as anywhere else.

**Capabilities** — how far up the ladder of §14.3 the radio reaches, its maximum
transmit power, and whether it adapts that power (§15). Near-static per device,
two bytes, and never sent here.

---

**SUPE_START** — sender to peer, overheard by all
*"I am taking traffic for whoever holds this address to that channel, at that
step, for that long."*
Seven bytes, and that is the entire main-channel cost of a detour.

Nothing answers it here, and almost nothing else is said here. Not our
capabilities: the peer needs them only in order to transmit to us, and by then we
are both on a channel where two bytes cost nothing. Not the power this frame went
out at: the peer measures the frame when it arrives and can hold that reading until
MANIFEST tells it what power produced it, which is the same information one frame
later and free.

**Our own identity, only if `sender_ident` says so.** Without it a START names the
peer and no one else, which is what keeps the protocol anonymous — and what leaves
the sender unprotected, since neighbours can hold traffic only for a tag they can
recognise, and nothing on the wire says who left. That asymmetry falls hardest on
exactly the node it should spare: a gateway is the tag most traffic is addressed
to, so when it initiates a detour, everyone's traffic to it goes into an empty
channel. Naming ourselves costs three bytes, one symbol group, and the anonymity;
it buys neighbours the ability to hold for us too. It is a deployment choice, and
a gateway should make it.

There is no discovery handshake and no cold start either, because there is nothing
to discover. A detour is only ever offered to a peer whose SUPE_ANNOUNCE2 has been
heard, which is what makes it a peer at all in SUPE's terms — and that frame
carries its capabilities. Its address and a signal measurement come from the
Reticulum announce that built the path (§2). So at the moment of offering, the
sender knows the peer speaks SUPE, knows what its radio can do, and knows how well
it hears. Everything the offer needs is settled before it is made.

What remains uncertain is only whether the peer is listening right now, and that is
discovered on the unicast channel where the failure is cheap, rather than being paid
for in shared-channel airtime.

**SUPE_ANNOUNCE1** — us to everyone, once per announce interval
*"I am about to say who I am, over there, for that long."*
Four bytes, which cost exactly what seven would.

Spreading factor and bandwidth do not change — the announcement has to reach nodes
that know nothing about us yet, and the hailing configuration is the only one every
node is known to be listening on. Only the frequency moves, and only so that the
identity list does not spend shared-channel airtime. Skipped entirely in regime 0,
which has nowhere to move to.

**Everyone else** who hears a START holds traffic addressed to that tag — and to
the sender's identity, when the sender named it — for the stated duration. It is a virtual carrier-sense hint, not a reservation: other
pairs keep using the main channel meanwhile, nodes out of earshot will not hold,
and anything sent regardless is covered by ordinary Reticulum retries. Returning
early is visible as ordinary traffic.

Note the asymmetry against [`proper-air-protocol.md`](proper-air-protocol.md) §3:
because no node is named in a START, neighbours hold traffic for the *address*
being serviced, not for the pair. Traffic for the sender is not held.

## 7. Messages on the announce channel

**SUPE_ANNOUNCE2** — us to everyone, once, immediately after ANNOUNCE1
*"These identities are me, this is what my radio can do, and this frame went out
at that power."*

The power byte is what makes the frame worth hearing: a listener already has its
own reading of it, and the two together give path loss rather than a bare signal
level.

Identity hashes, not destination hashes: a node typically has more destinations
than identities, and any receiver of a Reticulum announce can already derive the
identity hash from the public key it carried (§2). Four bytes rather than the
three used for tags, because a tag names one transaction and tolerates collisions
while this names a node in a table that persists — the extra byte is worth its
airtime here and not there.

Whether several identity hashes ride in one frame or each gets its own is the
`announce_bundling` setting. With it off the frames need to be spread slightly in
time rather than sent back to back; how is not settled (§16).

**No power sweep follows it.** Ordinary operation measures the same thing and
measures it continuously: every detour states the power of every frame it sends, so
each one yields path loss rather than a bare reading, at two configurations, for
free. A deliberate sweep would buy a cliff measurement the controller can derive
from path loss and a target margin anyway, and it would buy it once every thirty
minutes instead of on every exchange. There is nothing to transmit here that
working traffic does not already supply.

**Every SUPE node follows every ANNOUNCE1.** Nothing in the frame says whose it is,
so no node can judge its interest in advance, and the design does not ask it to —
capability entries go stale, nodes reboot, and a listener that skipped would have
no way to know it needed to listen. The cost is a deafness window on the main
channel per announcement heard, which is what keeps ANNOUNCE2 short and the
interval long.

## 8. Messages on the unicast channel

Two SUPE frames, then the traffic itself. One direction, no acknowledgement, and
the peer speaks first.

**HERE** — peer to sender, the moment it has retuned
*"I made it. This frame is at that power, and I heard your START at this
strength."*

It is also the entire answer to a probe (§11), where there is nothing to retune to
and no train behind it — the sender wanted only to know that somebody is listening
before spending airtime on a full packet.

**The peer opening removes a worst-case wait from every detour.** A sender that
spoke first would have to assume the slowest retune any peer might take before
transmitting, on every transaction, whether or not this peer is slow. Letting the
peer announce its own readiness turns that assumption into an observation: the
sender transmits when it hears HERE, and the worst case is only a deadline for
giving up.

It also puts the first real measurement on the channel immediately. The peer
reports the raw strength it heard rather than a path loss, because it cannot know
what power produced it yet; MANIFEST supplies that a moment later.

**MANIFEST** — sender to peer, on hearing HERE
*"The START went out at that power, this train goes out at this one, here is what
my radio can do, expect this many packets, and it will take this long."*

It carries the sender's capabilities unconditionally. The peer, having answered an
offer, has already published its own — that is why it was offered to — but nothing
says the sender's announcement ever reached it, and two bytes on this channel are
not worth a condition.

Its two power bytes are what the main channel does not carry. The peer has been
holding its reading of the START since it heard it; the first byte turns that
reading into path loss for the hailing configuration, the second does the same for
this step as the train arrives.

**MANIFEST is fixed length, and that is what lets it be its own confirmation.** A
peer waiting on a frame whose length it cannot predict has to wait generously; a
peer waiting on seven bytes at a known step has an exact deadline. Hearing
it also tells the peer the sender is present and that this step closes in both
directions — the peer's own HERE proved one, MANIFEST proves the other — so nothing
further needs to be exchanged before the packets start.

**The packets** × count — sender to peer, and **these are not SUPE frames.** They
are ordinary Reticulum frames in the interface's ordinary framing, checksum and
all, including whatever it already does with a packet too large for a single frame.
SUPE chose the channel and the rate; it does not touch what travels on them. What
differs from the main channel is only the absence of carrier sensing, there being
nobody else here, and that they arrive back to back.

The two kinds stay distinguishable by the rule that already separates them on the
main channel: a SUPE frame begins `0xC0`–`0xDF`, and a Reticulum frame on an
interface without an access code never has its top bit set at all (§3). The peer
counts *packets*, not frames, so a split packet counts once, when both halves are
in.

**A detour is worth taking whenever there is a peer to take it with**, and the
reason is that the sender's own arithmetic is the wrong arithmetic. Weighed as a
private trade — overhead against the fraction of airtime the faster step saves —
a small packet looks marginal. Weighed from where the scarcity actually is, it
is not close.

**Count it from the other side of the room.** For every node except the two
involved, a detour replaces a packet's entire main-channel airtime with a
seven-byte START. Nothing else is spent there. The smallest Reticulum packet that
exists — a header and no payload — costs about 51 ms at SF7/BW125 against the
START's 31 ms, and every real packet widens the gap from there. Even allowing for
offers that go unanswered, the shared channel pays `START + p(fail) × T` against
`T`, which favours the detour for any packet whose main-channel airtime exceeds
about 34 ms at a one-in-ten failure rate. Reticulum has no such packet.

So there is no threshold to compute and no departure rule to tune. The measured
quantities decide *which step* to offer; they do not decide whether to leave.

**It improves with load, which is when it matters.** The START is a fixed cost
amortised over the whole train: one packet relieves the shared channel of one
packet's airtime for 31 ms, fourteen relieve it of fourteen for the same 31 ms.
Trains grow when a node is busy, and a node is busy when the channel is contended
— so the mechanism is most efficient exactly when the network most needs it to be.

That is also why there is no accumulation timer. A sender leaves with whatever is
queued and lets the queue refill while it is away. Waiting to build a bigger train
would buy a better ratio on something already paid for, at the cost of latency on
everything held back.

**What fills a train is a resource transfer**, and it fills it as a batch rather
than a trickle. Reticulum's resources are receiver-driven: the receiver asks for a
window of parts by hash, and the sender's handler for that request transmits every
part it names in one pass, back to back, with nothing between them. The window
opens at 4 and grows toward a ceiling the link's measured performance sets — 4 on a
very slow link, 10 on a slow one, 75 where the rate justifies it. At a link data
unit of 431 bytes those ceilings are 1.7 KB and 4.3 KB a batch, against about
7.8 KB for a one-second train at the fastest step, so a slow-link window fits
inside a single train with room to spare. Only the fast ceiling overflows one, and
then into four.

There is a loop in that worth noticing: the window ceiling is chosen from measured
rate, SUPE raises the measured rate, a wider window makes fuller trains, and fuller
trains raise it again. Resource parts are also the one traffic that never needs a
delivery proof — they are link traffic, so they never enter the pending-proof set
of §5 at all, and only the finished resource is proved, once, at the end.

**The train ends by arithmetic, not by agreement.** The peer returns to the main
channel when it has the stated number of packets or when the stated length expires,
whichever comes first; the sender returns when the last packet is out. Both
outcomes are known from MANIFEST alone.

**Nothing is acknowledged and nothing is repeated.** A packet lost here is lost the
way a packet on the main channel is lost, and the layers above deal with it as they
always do — delivery proofs, channel windows, resource part requests. Adding repair
would mean holding traffic for the sender and negotiating over it, and the modem
has nowhere to hold it. What the sender loses by this is direct knowledge of
whether the step worked; what it observes instead is whether Reticulum's own
delivery signals come back, which is slower but costs no airtime at all.

**One failure path, the same for both sides: go back to the main channel.** The
step is named in the START, so changing it means a new START. Nothing can be
renegotiated on this channel, because any adjustment would have to be inferred by
both sides in lockstep while neither can hear the other. A sender that hears no
HERE by its deadline returns and offers again, at a lower step or not at all if the
peer has gone quiet too often. A peer that hears no MANIFEST returns and waits.
Both deadlines follow from regime constants and the step, so both sides know them
without exchanging anything, and the duration announced in the START bounds the
worst case for everyone else.

## 9. Reticulum announces are not ours to touch

Announces a node originates go out as the daemon hands them over, and relayed
announces likewise. No buffer, no replay, no batching, no pacing. An announce is
the daemon's decision and its timing is part of what it decided.

Both settings that mention announcing therefore concern SUPE's own frames alone:
the interval is between a node's SUPE_ANNOUNCE1 frames, and bundling is whether
several identity hashes share one SUPE_ANNOUNCE2.

## 10. What is learned, and where it is filed

Everything below is learned from traffic that was going to happen anyway. Nothing
is measured on purpose, and nothing is transmitted in order to measure.

Against the identity hash, once its SUPE_ANNOUNCE2 has been heard: capabilities,
maximum transmit power, whether it honours adaptive power requests. That entry
serves every destination the node owns and, if it is a transport node, all
traffic relayed through it — its identity hash is the address that traffic is
sent to.

Against the tag: path loss, never a bare signal level. Every reading is a pair —
a level measured here, and the transmit power that produced it, stated by the other
side one frame later. A detour yields three of them. The sender learns the path
towards the peer at the hailing configuration, from the peer's reading of the START
in HERE, and the path back from itself at the unicast step, from its own reading of
HERE. The peer learns the path towards itself at both, from its readings of the
START and of the train. None of it is a reciprocity assumption, and the one gap —
the sender's view of its own train — is in §17.

Two bindings that save a slow first detour:

- **Links inherit.** When the packet being sent is a link request, both sides
  compute the same link identifier independently and file everything under it, so
  all later traffic on that link opens at the peer's best step.
- **Relays file per packet.** A relay handling a packet that may attract a proof
  records the sender's capabilities and signal against the reverse-table entry,
  not against the tag — the tag that transaction opened on was the relay's own
  transport identity, which every neighbour relaying through it shares.

## 11. Failing well

- **No HERE by the deadline** — the peer is away on someone else's detour, did not
  hear the START, cannot manage the step offered, or cannot use that channel where
  it stands. Return to the main channel and offer again, holding the outcome against
  the tag on a counter that decays so a silent peer is retried occasionally rather
  than never. A failed offer costs one seven-byte frame plus two retunes and a
  deadline, and only the frame is spent on the shared channel.
- **Every offer moves the channel as well as the step**, to a channel sensed clear
  and never to one already tried in this transaction. Silence has two causes — a
  step the link cannot carry, and a channel that is unusable where the peer stands —
  and an offer that changes only the step tests only one of them. Changing both each
  time covers both, and refusing to reuse a channel stops one bad frequency from
  sinking every attempt and being read as an absent peer. In regime 1 the
  regulation requires it independently: a frequency may not be reused for 100 ms
  after leaving it (§14.2). The sender can only sense
  its own end of the channel; the peer's end is exactly what it cannot see, which is
  why the escalation walks away from a channel rather than retrying it.
- **The floor is step 0 on another channel** — the hailing configuration itself,
  moved off the shared channel. It is the most robust offer that can be made:
  anything a peer could hear at all, it can hear there. A peer that does not answer
  *that* is treated as absent for the hold period, and its packet dropped rather
  than transmitted into the void. Safe to drop: link data, channel traffic, resource
  parts and proofs all have retry or receipt machinery above.
- **Absence is a provisional verdict, not a finding.** The same silence is produced
  by a peer that is away on someone else's detour, and a busy transport node is away
  often — it is the tag most traffic is addressed to, so it is the node most likely
  to be mid-detour and the node whose wrongly-declared absence costs most. Two things
  keep the verdict cheap: it expires after the hold, and it is suppressed outright
  when we have recently heard a START naming that tag, which is direct evidence the
  peer is busy rather than gone.
- **Two senders can want the same tag at once**, and on a segment with one transport
  node they usually do — every neighbour's traffic is tagged with that node's
  identity. Both offer, on different channels, and only one can be answered. The
  loser must not read that as absence, which is the rule above; a small randomised
  delay before offering keeps the collision from repeating in lockstep.
- **An offer that changes neither channel nor step is a probe, not a detour.** The
  peer answers HERE from where it stands, without retuning; the sender then sends
  the traffic plainly on the channel both are already on. No MANIFEST follows,
  because nothing about a train needs describing when there is no train. This is
  how regime 0 concludes absence, having no second channel to offer — and it is
  worth having in any regime for traffic that is going out on the main channel
  anyway.
- **Probing is gated on staleness, and its answer is remembered.** A sender probes
  a peer it has not heard from in **five minutes**, and on silence holds it absent
  for **one minute**, dropping traffic for it outright rather than probing again.
  Both timers are local policy: unlike everything in §3 they need no agreement,
  because neither is visible to the other side.
- **The arithmetic behind all of it.** An offer costs about 31 ms and a probe with
  its answer about 80 ms, against roughly 760 ms for one full packet on the same
  channel. So a handful of offers costs a fraction of a single transmission into an
  empty room, and a probe pays for itself whenever the peer is missing more than
  about one time in ten — which is exactly what the five-minute gate arranges, a
  peer silent that long being far likelier gone than busy. This is what HERE buys
  beyond timing: evidence of presence, before anything long is sent.
- **Traffic to anything that is not a SUPE peer** never enters this at all. No
  announcement heard means no offer made, and the packet goes on the main channel
  exactly as it would on an interface with SUPE switched off.
- **Never dropped** — Reticulum announces and path requests. They have no retry at
  the Reticulum layer and are broadcast, so they simply go on the main channel with
  no SUPE involvement. This is also why no reserved wildcard tag is needed: the
  radio dwells on the main channel by default.
- **Expired regime** — a node whose regime is past its date stops speaking SUPE and
  operates as a plain interface. Its neighbours discover this the ordinary way,
  by it not appearing.
- **Deafness window** — a node away on a detour or following someone's announcement
  misses main-channel traffic. Announces repeat on their interval and path requests
  are retried, so with short detours this is noise against announce cadence.
- **A packet lost on the unicast channel** is lost exactly as one lost on the main
  channel is, and the layers above handle it identically. Nothing in SUPE notices,
  and nothing in SUPE tries.
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

## 13. Where it does not apply

- **Interfaces with an access code configured.** The frame is masked end to end —
  flags, hops, addresses and context byte alike — so the modem cannot read an
  address and has nothing to match. SUPE degrades to plain main-channel operation.
- **Peers that have not announced themselves.** A node becomes a SUPE peer by its
  SUPE_ANNOUNCE2 being heard, and nothing else. Traffic to anything else takes the
  main channel untouched, so a mixed segment needs no detection and no fallback —
  the absence of an announcement is the whole of it. The decaying counter is for
  something narrower: a peer that has announced but stops answering offers.

## 14. Regimes

A regime is the complete set of constants two nodes must hold identically in order
to meet at all: the channels, the ladder, the sync words, the ceilings and the
limits. §3 gives the reason none of it can be configuration. This chapter is that
content, and it is normative — an index on the wire means whatever the table here
says it means, for the version named in the frame that carried it.

### 14.1 Regime 0 — Single Channel

One frequency, one bandwidth, and the spreading factor as the only thing that
moves — the whole protocol on the channel the network already hails on. It needs
no channel plan and therefore no regulatory band plan, which is what makes it the
regime a network can run anywhere. The cost is that it cannot conclude a peer's
absence from a failed offer, since every step it can make is faster than hailing;
it reaches for the probe instead (§11).

| Constant | Value |
|---|---|
| name | Single Channel |
| version | 0 |
| expires | set at build time, no more than 14 days ahead (§3) |
| channels | none; the channel nibble is zero and ignored |
| ladder | the spreading factors above the hailing one, at the hailing bandwidth |
| steps available | to the lowest spreading factor the radio family reaches — from an SF7 network, two on SX126x and **none on SX127x**, whose first step would be the barred SF6; slower-hailing networks reach further before that bites (§14.3) |
| sync word, main channel | the interface's own |
| sync word, unicast | `0x67`; `0x21` where a step lands on SF5 |
| train ceiling | 1 s |
| transaction ceiling | 4 s |
| duration encoding | 20 ms steps |
| transmit power | the interface's `tx_power` |
| airtime accounting | none |

### 14.2 Regime 1 — ETSI EN 300 220 (863–870 MHz)

**Regulatory basis.** ETSI EN 300 220-2 V3.2.1 annex B table B.1 for the harmonised
non-specific short-range-device bands, their maximum effective radiated power and
their duty cycles; EN 300 220-1 V3.1.1 clause 5.21 and table 48 for adaptive
spectrum access, which every 863–870 MHz entry carrying a duty cycle permits in
place of that duty cycle. [`afa.md`](../afa.md) §1 derives the plan below from
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
| airtime per channel | 100 s/h | max Tcum_on, for any given 200 kHz of spectrum |
| minimum gap before reusing a frequency | 100 ms | Toff_min, same operating frequency |
| clear-channel threshold | −75 dBm at 500 kHz, −81 dBm at 125 kHz | table 45, referenced to 0 dBd |
| maximum radiated power | 25 mW, 14 dBm | annex B table B.1 |
| minimum listen before transmitting | 160 µs | minimum CCA interval |

Three consequences worth naming. The train's 1 s ceiling and the transaction's 4 s
ceiling are the regulation's, not a design preference — which is also why the
duration byte is quantised at 20 ms rather than finer: its range has to reach 4 s.
The 100 ms minimum gap before returning to a frequency is a second, independent
reason an escalation never reuses a channel within a transaction (§11). And the
100 s/h budget is per channel rather than per band precisely because of the
200 kHz separation above, which is what the accounting in §16 must track.

### 14.3 The ladder, in both regimes

**The ladder is every modulation the regime permits, ordered by net bitrate
`SF × (4/5) × BW / 2^SF`, ties broken toward the narrower bandwidth.** Step *k* is
*k* places above the entry the network hails on. Regime 0 admits only entries at
the hailing bandwidth; regime 1 admits all of them.

Ordered from the SF7/BW125 that most networks hail on, with each entry's cost in
margin against that reference:

| Step from SF7/BW125 | Configuration | Net bitrate | Margin cost |
|---|---|---|---|
| 0 | SF7 / BW125 | 5 469 bps | reference |
| 1 | SF6 / BW125 | 9 375 bps | 2.5 dB |
| 2 | SF7 / BW250 | 10 938 bps | 3.0 dB |
| 3 | SF5 / BW125 | 15 625 bps | 5.0 dB |
| 4 | SF6 / BW250 | 18 750 bps | 5.5 dB |
| 5 | SF7 / BW500 | 21 875 bps | 6.0 dB |
| 6 | SF5 / BW250 | 31 250 bps | 8.0 dB |
| 7 | SF6 / BW500 | 37 500 bps | 8.5 dB |
| 8 | SF5 / BW500 | 62 500 bps | 11.0 dB |

Both columns increase monotonically and no entry is dominated, which is what makes
a step *count* meaningful (§3). In regime 0 the same ordering restricted to
BW125 gives step 1 = SF6 and step 2 = SF5. The margin figures come from Semtech's
required-SNR values and are what turn a measured path loss into a step choice;
[`afa.md`](../afa.md) §3 carries their derivation and the frequency-shift entries
above this table.

**A pair including an SX127x has a ladder of bandwidth changes only.** That family
reaches no SF5 at all, and there is no framing for SF6 between two of them — SF6
demands an implicit header there, and nothing in this protocol supplies a fixed
length to go with it (§14.5). Strike both from the table and what remains from
SF7/BW125 is SF7/BW250 and SF7/BW500: two steps, 2× and 4× the rate, at 3.0 dB and
6.0 dB. The consequence for regime 0 is blunter — it cannot change bandwidth, so
those two steps do not exist there either, and **an SX127x pair hailing at SF7 has
no steps at all under regime 0**, its first step being the one that is barred. A
slower-hailing network is unaffected until the ladder reaches SF6: from SF8 such a
pair has one step, from SF9 it has two. Where there are none, the pair still uses
the probe (§11), which needs no step, but it gains nothing else until regime 1.

**Low-data-rate optimisation is part of what a step resolves to**, not a local
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

### 14.4 Sync words

| Where | Word | Why |
|---|---|---|
| hailing channel | the interface's `sync_word`, `0x42` by convention | that channel belongs to the Reticulum network, not to SUPE (§3) |
| any step, except | `0x67` | furthest from `0x12` and from LoRaWAN's `0x34` that the space allows |
| a step landing on SF5 | `0x21` | SF5's 32 bins admit no nibble above 3; this is the best of the nine words that fit, and SF5 is empty of other networks (§3) |

### 14.5 Radio families

The family nibble names what a peer's silicon can do, in the few respects that
change what goes on the air. The step nibble beside it already says how far up the
ladder a node will go; family exists for the things a step count cannot express —
chiefly that one family reaches no lower than SF6, and frames it there differently
from everyone else.

| Value | Family | Parts | Spreading factors | On the air |
|---|---|---|---|---|
| 0 | SX126x | SX1261, SX1262, SX1268, LLCC68 | 5–12 | the reference case; nothing special |
| 1 | SX127x | SX1272, SX1276, SX1277, SX1278 | 6–12 | **no SF5 at all**, and SF6 carries an implicit header, so a step landing there needs a fixed frame length agreed in advance (§16) |
| 2 | SX128x | SX1280, SX1281, SX1282 | 5–12 | 2.4 GHz only, and 203/406/812/1625 kHz bandwidths — no overlap with a 863–870 MHz regime, so it can only appear under a regime of its own |
| 3 | LR11x0 | LR1110, LR1120, LR1121 | 5–12 | as SX126x for these purposes |
| 4 | LR2021 | LR2021 | 5–12 | as SX126x for these purposes |

Two rules follow, and both are the sender's to apply before it offers:

- **A step must be reachable by both.** The offering node resolves the step to a
  configuration (§14.3) and checks it against the peer's family as well as its own.
  A pair of SX126x nodes on an SF7 network has two steps before SF5's floor stops
  them; a pair including an SX127x has only the bandwidth entries, and from SF7 in
  regime 0 none at all.
- **SF6 is not offered to family 1 at all.** It demands an implicit header there,
  and no framing in this protocol supplies the fixed length that would need. A step
  resolving to SF6 with either side on SX127x is skipped, which is what leaves such
  a pair with bandwidth changes alone (§14.3). §16 carries the exception that would
  recover it.

The nibble holds sixteen values against five families, which is room enough that a
new part gets a number rather than a compatibility rule.

## 15. Adaptive transmit power

Part of SUPE, gated on `SUPE.adaptive_txpower`, and applying to unicast only:
every frame in a detour after the START, and any packet sent plainly to a single
peer. Never to anything broadcast — announces, path requests and the START itself,
whose reach is what the hold in §6 depends on.

**Power is derived, not stored.** Per peer the node keeps a learned offset, a
decaying floor recording where transmission last failed, and a count of successful
transmissions since the offset last moved:

```
power = clamp( path loss + step margin + offset , floor , maximum )
```

The first two terms are measurement, and SUPE produces them without spending
anything: every HERE and MANIFEST states the power it went out at, so each pair of
frames yields path loss rather than a bare reading (§10), and §14.3 gives the
margin the chosen step needs. They track the *path* — fading, movement, a new
obstruction — and they move before anything breaks.

**The offset is what the loop learns**, and it is exactly what measurement cannot
reach: the noise floor at the other end, antenna and front-end differences, and
the difference between a peer's assumed and actual power. Learning an offset rather
than an absolute power is what preserves that correction when the path moves.

- **Failure raises it fast.** A single miss steps up about 6 dB; a peer that has
  gone silent goes straight toward maximum. Being wrong downward costs
  connectivity, so recovery is immediate and large.
- **Success lowers it slowly**, 1–2 dB, so any overshoot past the cliff is small
  and the next failure recovers it.
- **The decrement is gated on evidence, not time.** Require a number of successful
  transmissions to *that* peer since the last change. "Nothing went wrong lately"
  means nothing if nothing was sent.
- **Where it broke is remembered.** After a failure at a given power, do not return
  below it plus a margin for a while, on a decaying floor. Without that the loop
  oscillates across the cliff instead of settling above it.

**The probe is the power test.** A detour gives no acknowledgement, so within one
transaction there is no direct evidence that a train landed — but a probe (§11) is
a START and a HERE, and that is a complete round trip. Probes may therefore go out
at the adapted power rather than full power, and the outcome separates cleanly:

- **HERE comes back** — the power suffices, and the reading it carries says by how
  much. Sustained, this is what walks the offset down.
- **Silence, then HERE at full power** — the peer is present and the power was too
  low. Raise the offset immediately and record the failed power in the floor.
- **Silence at full power too** — the peer is absent, which is §11's verdict, and
  nothing about power is learned.

That is the whole feedback loop, and it costs only frames the probe was already
going to spend. Everything else — a train that vanished, a step that was too
ambitious — surfaces only through Reticulum's own delivery signals, late and only
for traffic that is proved at all (§16).

## 16. Open items

- **A fixed-length framing exception for SF6 on family 1.** SF6 demands an implicit
  header on SX127x ([`afa.md`](../afa.md) §4.1), so the protocol skips it and leaves
  such pairs with bandwidth steps alone — and, on a network hailing at SF7, with
  nothing at all under regime 0 (§14.3). An exception carrying a length agreed in
  advance would recover a spreading-factor step for every pair including an SX127x,
  which on those networks is the difference between regime 0 doing something for
  that hardware and doing nothing. Worth
  deciding on those grounds rather than on tidiness. Note it is a property of where
  a step *lands*: a slower-hailing network reaches its second step without touching
  SF6.
- **Out-of-band emission performance, and whether channel 9 can be 500 kHz.** The
  channel fills band N edge to edge between two alarm allocations (§14.2), so what
  decides it is the skirt of a 500 kHz LoRa signal against the out-of-band limits at
  868.6 and 869.2 MHz — not the far-field spurious limits that dominate most
  compliance work. Two things to establish and neither is settled here: what the
  part's datasheet states for transmitter spectral performance and what filtering
  the reference designs assume to meet EN 300 220-1, since a bare radio typically
  does not; and which clause the band-edge limit actually falls under. The fallback
  is already known — 250 kHz at the same centre, costing peak rate and no airtime —
  so this decides only whether the fallback is needed. [`afa.md`](../afa.md) is
  where the derivation belongs once the figures exist.
- **Retune turnaround.** Sets both deadlines — how long a sender waits for HERE,
  and a peer for MANIFEST — and with them how cheap a failed offer is and how many
  offers a silent peer is worth. Measure the responder's received-to-retuned-to-armed
  path on the timer route, not the task route.
- **How far the escalation walks**, which §8 gives a shape for: keep offering while
  the shared-channel airtime already spent on offers stays below what the packet
  would cost there outright, since past that point the detour is costing the
  channel more than plain transmission would. At 31 ms an offer that is roughly one
  attempt for a bare Reticulum header and a couple of dozen for a full packet — so
  the cap scales with what is queued rather than being a constant. Confirm the
  arithmetic and decide whether anything else should bound it.
- **How a sender judges a step without acknowledgements.** Delivery signals coming
  back through Reticulum are the only evidence a step worked, and they arrive late
  and only for traffic that is proved at all. Decide what a sender does with a
  step that produces silence: hold it, drop one, or wait for the peer's next
  announcement.
- **Regime 1.** Whether hailing and unicast land in one duty budget or two.
- **Preamble handling when a step lands on SF5 or SF6.** Both have modified
  preamble and sync behaviour on the SX126x; confirm `0x21` at SF5 and `0x67` at
  SF6 land as §3's arithmetic says, in the same datasheet pass as the question
  above. The bin arithmetic is settled; the silicon's treatment of it wants
  checking. Only networks hailing fast enough to reach those spreading factors are
  affected.
- **Whether MANIFEST should echo the regime and version** as a guard against a
  peer that retuned on a stale START, at the cost of one byte per detour.
- **Spreading unbundled announcements** in time — what the offsets are and
  whether they are fixed or jittered.
- **The reference count on tag entries.** Three bytes with a thousand live
  entries collides internally often enough to matter; confirm a count is enough
  and that nothing needs the full address kept alongside.
- **Per-band airtime accounting** — an hourly ring kept per band rather than per
  radio. Needed for compliance; the protocol behaves without it, since budget binds
  only a saturated node.
- **Unprefixed setting names.** Whether the interface honours `adaptive_txpwr` and
  `announce_interval` without the `SUPE.` prefix as aliases, or requires the
  prefixed forms.
- **Whether ANNOUNCE1 and the announce channel still earn their place** now that no
  power sweep follows ANNOUNCE2. Moving a frame of `5 + 4·count` bytes off the
  shared channel saves tens of milliseconds once per interval per node, against
  every neighbour in earshot retuning twice and going deaf meanwhile. Sending
  ANNOUNCE2 on the main channel instead would delete a frame type, a channel role
  and a deafness window, and make regimes 0 and 1 announce identically.

## 17. Deferred

- **Traffic in both directions on one detour.** The peer answering with its own
  train, rather than opening a separate detour of its own later. It needs the modem
  to hold traffic destined for a specific peer, which is a queue it does not have.
  Everything else is in place: the channel is already up and both directions are
  already proven.
- **Repair.** If a train is repeated at all, the hashes belong in the *receiver's*
  reply after the fact, not in the manifest beforehand — and only when the count,
  or a checksum over the whole train, disagrees. A manifest that listed a hash per
  packet would spend airtime on every successful train to serve the rare failed
  one, which is backwards. Note this also wants the reverse queue above.
- **Reporting the train's own signal.** The peer measures the packets at the step
  that carried them and has no way to say so, so the sender never learns how well
  its own train landed. A single reading in a reverse frame would close it, once
  there is a reverse frame.
- **Destination-scoped proof returns.** Naming the destination a proof belongs to
  rather than the packet hash, so a burst of messages to one destination collapses
  to a single table entry. It costs precision — the destination's owner and every
  other recent sender wake too, and since the peer speaks first on the unicast
  channel their HERE frames would collide — so it is only worth doing if
  pending-proof entries become a real memory pressure. They are eight bytes and
  short-lived, so probably not.
- **Remembering which channels work, per peer.** Two halves of the same thing.
  What a node knows about itself: an optional bitmask in the capabilities marking
  channels that never work where it stands — persistent local interference on part
  of the band, a warehouse interrogating tags, a neighbour's equipment parked on one
  frequency — costing two bytes in a frame that never touches the shared channel.
  And what a sender observes: which channels have carried a detour to this peer and
  which have only ever produced silence, so an escalation starts from evidence
  instead of walking the raster afresh every time. Both want a rule for how a
  channel is judged bad, and how that judgement is forgotten.
- **Holding traffic briefly** for addresses suspected to be away on someone else's
  detour.
- **Asymmetric configurations** per direction, once there is traffic in both
  directions to configure.
