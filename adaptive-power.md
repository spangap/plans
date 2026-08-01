# Adaptive TX power for iface-lora — what's left

> Scope: **only the parts not yet built.** Everything that exists is documented
> in **`iface-lora/INTERNALS.md`**, which is authoritative for it: §12 the
> per-frame LoRaMon recorder, §13/§13.1 the passive neighbour table (`lora n`),
> §14 the two-way power probe (`lora rf`) with §14.1 on why it is shaped that
> way, and §14.2 the cooperative hash-linkage exchange. Don't re-litigate those
> here; if anything below contradicts INTERNALS, INTERNALS is right and this file
> is stale.
>
> What remains is the part that turns measurements into behaviour: a control
> loop, a story for neighbours that won't cooperate, and the coupling to rate.

## 1. Goal

Use full TX power only when needed. A frame to a close neighbour shouldn't go
out at +22 dBm; a link that only needs +5 dBm to close should use +5. Wins:
battery, and less self-interference / airtime pollution on a shared channel.

Nothing yet drives the `txp` register per neighbour. `lora rf` finds a link's
floor and parks the answer in the neighbour row (the `TX <dBm>` tag), but every
real frame still goes out at the configured power.

## 2. Where it lives — the interface, not rnsd

TX power is a **per-RF-hop physical property**. Only the LoRa interface has the
inputs (per-frame RSSI/SNR, the `txp` register, the channel noise floor it
already tracks for CSMA) and it is the only interface for which "power" means
anything — TCP/ESP-NOW have none. So the control loop and its measurement table
stay in **iface-lora**; `rnsd` stays medium-agnostic.

The one thing rnsd "has" that we'd want is *semantics* — is this an announce
(must go wide) vs. link data to a known-close peer (can go quiet). We don't need
rnsd for that: the interface already parses the RNS header, so it classifies by
packet type / dest type / hops locally. No rnsd coupling.

## 3. The feedback problem

Cutting power needs a signal for "did it still arrive / how much margin was
left." What's available, weakest-to-strongest:

- **Passive, universal (reciprocity).** Every RNS node emits announces; any
  frame we receive carries an RSSI/SNR *at our radio*. That measures *their→us*
  path. LoRa path loss is ~reciprocal, so it estimates *us→them* — the universal
  floor, works against every implementation, zero cooperation. Caveat: path loss
  is reciprocal but **noise at each end is not**, so a downlink estimate from an
  uplink measurement needs margin. Deeper caveat: it silently assumes the far
  end's TX power is *constant*. True for a dumb peer; false the moment the peer
  is itself adapting, and then two loops chase a reference each is moving and can
  diverge with both ends too quiet. Reciprocity is sound only against a
  fixed-power peer.
- **Active, cooperative (proofs).** A single-destination packet elicits a
  **proof** from the receiver. Proof-or-not, and the RTT, is a real end-to-end
  "it arrived" bit at a power *we* chose — the one signal that doesn't depend on
  knowing theirs. Not universal: application destinations don't prove by default
  (PROVE_ALL is opt-in); the always-on elicitor is the **link handshake
  (LR → LRPROOF)**, which any node hosting a linkable destination must answer.
  `lora n -v` already scores exactly this as the per-neighbour quality EWMA.
- **Active, µR-only (extended proof / rx-report).** A reticulous/µR peer appends
  *its own measured* `rssi|snr` to the proof (`rns/INTERNALS.md` §5.7) — a direct
  readout of the far end's headroom. A vanilla receiver length-rejects the longer
  proof, so it goes only to known-capable peers.

Minimal proof elicitor: `rnstransport.probe` — a single-dest encrypted packet
(~115 B) that draws an implicit proof. Cheap against LoRa airtime, but it costs
our airtime *plus* the receiver's proof airtime on a shared half-duplex channel,
so prefer piggybacking on real single-dest traffic; synthetic probes only to
bootstrap or when idle.

Note that §4 deliberately does **not** use synthetic elicitation for the
near-term plan: against a peer that can't report what it heard, an elicited proof
buys a positive confirmation we can get from real traffic for free, and its
absence still isn't a usable negative. Keep the elicitor in mind for the
cooperative tier (§8) and for bootstrapping a neighbour we have no traffic for,
not as the basis of the loop.

Synthetic elicitation is in fact **dead on LoRa**, on airtime alone. The minimum
encrypted single-dest packet is 115 B — the envelope has a hard floor of 32 B
ephemeral key + 16 B IV + 16 B (one padded block, even at zero payload) + 32 B
HMAC, and a zero-length payload buys nothing. With its 83 B implicit proof that
is 352 ms at SF7 for **one estimate in one direction**, against ~330 ms for a
complete `lora rf` run that measures *both*. Against a capable peer the probe is
strictly better for the same airtime; against a vanilla one, 352 ms buys roughly
what two overheard announces give for free. (`rnprobe` defaults to `-s 32`, which
pads 32→48 and costs 32 B more than necessary; `-s 0` is the cheap form.)

## 3a. The power request — what supersedes most of the above

**This is the design that came out of thinking §3 through, and it replaces the
reciprocity bullet's caveats, the margin policy in §4.1, and the near-term case
for §8.** It is small enough to state in a paragraph.

A node about to transmit **prefixes a 4-byte frame, back to back in the same
channel access**, carrying a suggested TX power for the peer, and/or the RSSI/SNR
it measured of the peer's last frame. Absence of the prefix means "reply at your
maximum". That is the whole protocol: no negotiation, no acknowledgement, no
capability exchange, no error path.

**Why the suggestion and not the source material.** The party that chose the
destination hash knows the peer, holds its history, and knows its own noise
floor, antenna and sensitivity. The receiver holds one noisy RSSI sample and
would have to run a shared model over it. Sending the *answer* rather than the
inputs deletes three problems at once: the far-end noise floor (folded into the
suggestion by the only party who knows it), the shared SF-floor model (there
isn't one), and any agreed constant for "assumed peer power" (the fallback is
*your own* max, which needs no agreement between the ends).

The two fields map exactly onto the two knowledge states, which is why the frame
is "and/or" rather than either/or:

| we know who the peer is | we send | they do |
|---|---|---|
| yes | a suggested power | obey it |
| no | our RSSI/SNR of their frame | compute it themselves — they know what power they sent at, so there is no unknown term |

**Precedence: an explicit request outranks your own estimate.** The receiver is
the authority on its own reception. A transmitter's reciprocity guess is strictly
worse information, so it demotes to the un-negotiated case — out-of-the-blue
sends and first contact.

**This retires §3's divergence caveat structurally rather than by margin.** The
failure there was two loops chasing a reference each was moving. Here you are
always measuring a frame whose power *you specified*, so there is no unknown
power term and no dependence on the peer's loop output. It is also monotone
against a peer that cannot comply: if B clamps at its own max, A's measured path
loss comes out too large, so A asks for *more*, never less, and it parks at
"A asks for more than B can give, B gives max" without oscillating and without
anyone having to signal the clamp.

**Recovery is omission.** On a retransmit, simply don't prefix — the peer returns
to max. One step, immediate, no escalation ladder and no accumulator, matching
§4.3's "failure → jump up now, and significantly" for free. Drop your own power
back to max on the same retransmit and both directions recover together. This
matters because the step time is not ours: `DEFAULT_PER_HOP_TIMEOUT` is 6 s, so
every failed guess is six seconds of user-visible stall on a message someone is
waiting for — a much stronger argument for caution than §4.3's battery-versus-
connectivity framing makes.

### 3a.1 The frame

Four bytes, normal modem regime (sync 0x42, explicit header, preamble 12) so it
rides back to back with an RNS frame without a modem reconfigure:

| byte | field | encoding |
|---|---|---|
| 0 | magic `0x04` | — |
| 1 | suggested txpwr | `int8` dBm; sentinel = no suggestion |
| 2 | rssi of the peer's last frame | `probeEncRssi` — unsigned negated dBm; `0` = none |
| 3 | snr of the peer's last frame | `probeEncSnr` — `int8` quarter-dB |

Sentinels carry the "and/or", so no flags byte is needed. Dispatch is unambiguous
without reserving anything: `HEADER_MINSIZE` is 19 B and iface-lora adds a
framing byte, so nothing under 20 B on air can be an RNS packet — which is how
the existing 0x00/0x02/0x03 frames already discriminate.

**Size is not worth optimising below this.** At SF7 a 1–5 byte payload all costs
35.1 ms; 6–8 B costs 40.2 ms. The preamble alone is 16.6 ms. So there is exactly
one spare byte before the next symbol group, and shaving the frame from 4 bytes
to 2 would save nothing at all.

### 3a.2 When to prefix

- **Trigger on the reply power you want, not on your own.** The byte describes
  the *peer's* transmission. Usually correlated with your own power since the
  path is reciprocal, but not identical — the two ends' noise floors and ceilings
  differ.
- **Never when you'd suggest max.** Absence already means max, so the frame would
  be 35 ms saying nothing.
- **Never on a broadcast.** An announce has no single next hop and must reach
  everyone; §5's rule stands unchanged.
- **Only to a peer that has spoken our air protocol** (`ourProto`, the
  `RF_PROTO_NAME` tag in `lora n`). To anyone else the frame is 35 ms of
  unparseable noise on a shared channel, and we cannot detect that it was
  wasted. The tag is set by an `lora rf` run or by either 0x02/0x03 linkage
  frame, so that exchange is what bootstraps eligibility — and it is the
  remaining reason those mechanisms earn their keep.
- **Earn the right to dial down.** A single frame's RSSI moves several dB.
  `neiRecentSignal` already returns a sample count beside the mean, so the gate
  is "N samples in the ring before you dial anyone down".
- **Honouring a request is gated on `adaptive_txpwr`, unlike answering a
  probe.** Answering an `lora rf` is unconditional because a probe run does not
  change steady-state behaviour. Honouring a request *does* — it puts your
  transmit power under someone else's control, observably. If that were
  unconditional, a node with the key off would still be dialled down by its peers
  and still be correlatable across identities, and the opt-out would not be one.

### 3a.3 Consequences worth knowing

**Reciprocity must be fed by announces only.** The moment any node adapts, an
overheard frame no longer implies a known transmit power — and the leak is not
through the cases we scoped but through *relayed* HEADER_2 traffic, which is the
largest unicast category on any segment with a transport node and is not "out of
the blue". Announces are the one class guaranteed full power (broadcast, never
power-cut, emitted periodically by every node). Everything else is uncalibrated.
`neiSample` currently feeds the bucket ring from all rx traffic; for power
purposes it should not.

**Links are strictly better than opportunistic sends under this scheme**, for
three reasons rather than one. The prefix amortises — ad-hoc pays it every
exchange, 70 ms at SF7 and 281 ms at SF9 for the pair, while a link pays once at
setup. The request persists and can be refined mid-session by prefixing any later
packet, so both directions converge, where ad-hoc gets one guess with no
correction. And a link is *identifiable*, so the responder may legally keep state
about the peer — which is what forced statelessness on the ad-hoc path in the
first place. This lines up with what LXMF's `link-if-big` already does for size,
with a second reason.

**SF11/SF12 are not real configurations** and should not appear in cost
arguments. A full 500 B MTU is a 255 B split pair; at SF12 one half alone is
9.15 s and the pair 18.3 s, against a 6 s per-hop timeout — RNS retransmits while
the first transmission is still on the air. SF11 fails the same way (10.1 s).
SF10 is 4.66 s, inside the timeout only if nothing else contends, and `appc`
alone can back off 5.2 s there. The real range is SF7–SF9, so **SF9 is the honest
worst case**.

**The neighbour table is load-bearing again.** An intermediate design had the
sender state its own power, which made path loss derivable per frame with no
identity and made clustering optional. Moving the computation to the initiator
reverses that: it must resolve dest-hash → node → history before it can name a
power, which is exactly what the table and the 0x03 linkage do.

**What is left for `lora rf`.** Bootstrapping on a channel where nobody has
announced yet, and checking the model against ground truth. Diagnostic, not
mechanism. First contact needs no probe: it goes at max because there is no basis
for anything else, which makes the peer's fallback assumption correct by
construction.

**Anonymity.** Telling a peer to go quiet and watching for the change elsewhere
correlates identities across links. It is real but weak next to what is already
exposed: iface-lora *deliberately* broadcasts 0x03 frames asserting "these hashes
are one device", and below the protocol, per-device carrier frequency offset and
PA nonlinearity fingerprint a radio passively with no cooperation. Reticulum's
anonymity model is network-level and never claimed unlinkability against a local
RF observer. The mitigation is the opt-out above, and it is the default.

## 4. Non-cooperating neighbours — reciprocity only, plus regression detection

**Decision: don't try to find the cliff on peers that can't tell us anything.**
Estimate from reciprocity, add margin, and spend the effort on noticing when a
power that *was* working stops working. That capability is needed regardless —
conditions change, nodes move, interference arrives — so it is the right thing to
build even if power adaptation were free.

The reason not to search downward on a vanilla peer is structural, not a matter
of tidiness. `lora rf` works because a **fixed-time slotted exchange makes
absence unambiguous** — nothing but "too quiet" can explain a silent slot. On
real traffic every negative is contaminated:

- We hear the relay of a packet we routed through a transit neighbour → proof
  they heard us. We *don't* hear it → they may have had no path onward, been
  mid-transmission, hit their own LBT timeout, or be bridging to TCP where the
  relay is inaudible. And when the first hop genuinely was too quiet, nothing
  tells us: we are the transport, so we don't retransmit; the originator times
  out end-to-end seconds later and that outcome never reaches us.
- A missing proof is better but still needs the `provesData` carve-out, because
  PROVE_ALL is opt-in and silence from a non-prover isn't failure.

Positive signals from real traffic are trustworthy; negative ones are not — so
we don't *search* with them. But that is a much weaker constraint than it first
looks, because a contaminated negative under §4.3's loop only ever causes an
unnecessary step **up**, which is safe. The loop doesn't need to know why a
frame failed; it needs to recover fast when one does.

### 4.1 The estimate — continuous, from recent packets

> Mostly superseded by §3a: the margin policy below exists because the far end's
> noise floor and real TX power are unknown, and a power *request* removes both.
> What survives is the estimate itself — it is how a sender decides what to
> request, and the un-negotiated fallback.

Path loss from a received frame is `assumed_peer_txp − rssi`, and the power we
need is that loss plus the SF sensitivity floor plus margin. Which is exactly
`probeHeadroom10()` with the peer's power supplied — the estimator already
exists, it just needs a power to assume.

Compute it **continuously, over a recent window**, not once. Every frame we hear
from a neighbour refreshes it, at no cost, and it is the only input that moves
*before* anything breaks. This is what makes the windowed-envelope fix in §4.2
load-bearing rather than cosmetic: a lifetime min/max cannot track a path.

- **Assume +22 dBm for a peer we know nothing about.** This errs the safe way:
  if they are actually quieter, we *over*-estimate path loss and transmit higher
  than needed. Wasteful, never broken.
- **Use the stated power wherever we have it.** A peer that has completed
  `lora rf` told us its txp, and its frames carry it. Scope the +22 assumption
  strictly to unprobed peers — otherwise our own adapting nodes, which
  deliberately transmit low, would be read as distant and answered at full power.
  This is §3's divergence caveat in its practical form.
- **Noise is not reciprocal even where path loss is**, so margin is mandatory,
  not decorative.

### 4.2 Inferring link quality from real traffic

Three signals, all already collected or nearly so, none needing cooperation:

| signal | state today |
|---|---|
| elicited-proof hit ratio per neighbour (EWMA + counters) | **built** — the `q N/255` column |
| relay of our routed packet re-heard one hop up | **built, but aggregate** — the seen-ring counts in-band relays per radio, not per neighbour |
| trend in *their* signal to us (reciprocity as a leading indicator) | **partly** — the 12 × 5-min buckets give a 1 h trend at 5-min resolution |

The third is the cheapest and most general: if their signal to us degrades, ours
to them probably has too, and it costs nothing but listening.

**One thing to fix:** `rssiMin`/`rssiMax` are *lifetime* extremes, so a single
outlier pins them forever and they say nothing about current conditions. For
adaptation the envelope needs to be windowed or decaying; the bucket ring is
already the right shape and the envelope is not.

### 4.3 The loop: fast up, slow down (AIMD)

Classic additive-increase/multiplicative-decrease, inverted for power — and
deliberately so, because the costs are asymmetric: being wrong upward costs
battery, being wrong downward costs connectivity. That asymmetry is the argument
*for* exploring downward, not against it: make the recovery immediate and large,
and the exploration becomes cheap.

- **Failure → jump up now, and significantly.** Not one step. A hard failure
  (link lost, repeated misses) goes straight toward max; a single miss takes a
  large step (~6 dB, 4× power). Recovering a working link instantly is worth far
  more than the battery the jump costs.
- **Sustained success → walk down slowly.** 1–2 dB at a time, so any overshoot
  past the cliff is small and the next failure recovers it immediately. This is
  what actually harvests the power the conservative estimate left on the table,
  and without it the feature is inert on non-cooperating peers.
- **Gate the decrement on evidence volume, not elapsed time.** "Nothing bad
  happened lately" means nothing if we sent one packet in ten minutes. Require N
  *successful transmissions to that neighbour* since the last change before
  stepping down. This is the difference between a sound version of the rule and a
  random walk toward the cliff.
- **Remember where it broke.** After a failure at power X, don't return below
  X + margin for a while — a decaying "known bad" floor, the analogue of TCP's
  `ssthresh`. Without it the loop oscillates across the cliff instead of settling
  above it.
- **Never on broadcasts.** Announces must reach everyone; §5's rule stands.

#### Couple it to the live estimate: learn the offset, not the power

The loop above is purely reactive, and a purely reactive loop only ever learns by
breaking something. §4.1's estimate is refreshed continuously from packets we
were going to hear anyway, so it is free early warning — use it.

The clean way to combine them is to have **the loop learn an offset from the
estimate, not an absolute power**:

```
P = clamp( recent_estimate + learned_offset , floor, max )
```

- The **estimate** tracks the *path*: fading, movement, a new obstruction. It
  moves without anyone failing, so a 10 dB fade raises P before a packet is lost
  rather than after.
- The **offset** is what the loop learns empirically — and it is exactly the part
  the estimate cannot know: noise asymmetry at their end, antenna and front-end
  differences, their real TX power versus our assumed +22. That correction is hard
  won, so it must survive a change in conditions instead of being discarded and
  relearned every time the path moves. Tracking an offset is what preserves it.
- Failure adjusts the **offset** upward (fast, large); sustained success walks it
  down (slow, evidence-gated). The known-bad floor is likewise an offset floor.

This also keeps the +22 assumption honest: for an unknown peer the estimate moves
only when the *path* moves, because their power is held constant by assumption.
For a peer whose power we know (probed, or stated in its frames) use the stated
value — otherwise a peer adapting its own power looks exactly like a path change
and the two loops chase each other, which is §3's divergence caveat.

**And this is why detection speed is what matters now.** Estimate accuracy barely
does — the offset absorbs it — but failure-detection *latency* bounds how far
below the cliff we can drift before recovering, and therefore how aggressive the
walk-down can safely be. That is where §4.2's effort should go. RNS
retransmission remains the backstop beneath all of it.

New per-neighbour state: the learned offset, a decaying known-bad offset floor, a
success count since the last change, and the last-change timestamp. The absolute
power is derived, not stored. Small enough to sit in `Neighbor` alongside what's
there.

## 5. The controller

- **Don't sit at the cliff.** LoRa's SNR floor is sharp; find the edge, then back
  off a margin. The minimum drifts with fading / mobility / interference, so it's
  a tracking loop with hysteresis, not a one-shot search. `lora rf` finds the
  edge for a cooperating peer; §4.3 is the same loop driven by weaker inputs for
  everyone else. There is one ratchet, with two grades of evidence feeding it.
- **Broadcast is a separate regime.** Announces have no single prover and must
  reach everyone — power them for the *farthest neighbour we still need*, derived
  from the per-neighbour table's worst member; never cut power on a broadcast.
- **RNS retry is ground truth.** Estimates can be wrong (noise asymmetry); keep
  margin and let end-to-end retransmission be the backstop.
- **Validate offline first.** The LoRaMon store already records every frame's
  power, signal and protocol (INTERNALS §12) — the substrate to test a controller
  against recorded traffic before it drives the register.

## 6. Keying power to the outbound frame

The controller must know, for a frame about to go out, *which* neighbour's power
applies. That is resolvable from the outbound frame itself:

| outbound frame | next-hop identity |
|---|---|
| HEADER_1, dest type SINGLE | the dest hash itself |
| HEADER_1, dest type LINK | the `link_id` → neighbour, mapped at link setup |
| HEADER_2 (single or link) | the `transport_id` (next-hop) in the header |

Power only affects the **first RF hop**, but a proof is end-to-end, so a
returning proof is a clean power signal **only when the destination is a direct
neighbour** (`hops == 0` on air). For a multi-hop dest, "no proof" can't
distinguish a too-weak first hop from a failure two hops downstream. Resolution:
key the table on the **first-hop neighbour** and *calibrate* that neighbour from
traffic where it is itself the direct prover; a neighbour's required power is a
physical-link property independent of what RNS traffic rides it.

The table this keys into exists (INTERNALS §13), including the identity
clustering that makes "this neighbour" well-defined. What's missing is the lookup
on the tx path, and the per-neighbour power column being *driven* rather than
merely displayed.

Also unbuilt: per-`transport_id` keying of rnsd's gateway signal (`rnsd.gw.*`,
`rns/INTERNALS.md` §5.7), a single global sample today, which would be the
downlink-calibration table for transit neighbours.

## 7. Coupling with rate (SF/BW/CR)

Link margin is SNR headroom to the SF sensitivity floor. Cutting power trades
directly against SF/coding gain *and* worsens hidden-node loss. So power and rate
are one decision, not two — `iface-lora/proper-air-protocol.md` §4's per-link
measured-parameter negotiation is the natural home for the *joint* optimum.
Power-only adaptation on the fixed common config is the earlier, simpler slice;
treat it as a floor the later rate layer subsumes.

Regulatory: lower power doesn't change dwell/duty, but the joint power+rate
choice does — fold into the proper-air-protocol dwell math. Note that carrier
sense is a **separate** obligation from dwell and is mandatory in some regimes
(ARIB STD-T108 on 920 MHz), which constrains what may run without LBT
regardless of how short the frames are.

## 8. Cooperative measurement between smart nodes — the radio-management digest

> Superseded for the near term by §3a, which gets the same "no unknown far-power
> term" property in 4 bytes on traffic that was going out anyway. Keep this
> section for what §3a does *not* cover: passive topology enrichment from hearing
> what a neighbour heard.

§3's reciprocity floor breaks when two adaptive nodes face each other, so between
capable peers cooperation is the only way the loops converge. The 0x02/0x03
linkage exchange (INTERNALS §14.2) is a narrow slice of this idea —
identification and hash coupling — but the *measurement* half is unbuilt: a
periodic digest carrying

- truncated hashes of frames we recently **sent**,
- truncated hashes of frames we recently **heard**, each tagged with the RSSI/SNR
  we measured for it,
- our current `txp`,
- our identity fragment.

**Why the echoed hashes are the crux.** When neighbour B reports "heard hash H at
SNR S" and H is a frame **we** transmitted, we know exactly what power we used
for H. That is an exact link-budget sample with **no unknown-far-power term** —
the very term that makes reciprocity diverge when both ends adapt. Every ordinary
frame already on the channel becomes a *retroactive* calibration probe once its
hash is echoed: no proof airtime, no synthetic probe. Truncated hashes keep the
digest tiny.

Four jobs, one frame: *identify* (only capable nodes emit and parse it, and it
must be **mutual** — if A thinks B is smart but B thinks A is dumb you get the
worst of both), *calibrate* (the samples above, both directions), *coordinate*
(with explicit `txp` on the table neither side runs a blind search against a
moving target), and *enrich topology* (hearing what a neighbour heard extends our
view one hop, passively).

This demotes §3's synthetic probe to a bootstrap / dumb-peer tool rather than the
steady state. Framing constraint: droppable-as-noise by a vanilla rnsd receiver
while a µR interface parses it — the same class of trick the extended proof uses.
Interface-local; rnsd never sees it.

## 9. Later: Reticulous as an RNode

A larger prize, genuinely later. Imagine this built into an RNode: Reticulous
*being* an RNode next to its own stack. The LoRa interface exposes an
RNode-compatible endpoint (RNode KISS/serial framing, including RNode-over-TCP)
alongside rnsd's own use of the radio. Any standard Reticulum client — phone,
desktop — attaches as if to an ordinary RNode and immediately stops transmitting
at max power, **with no change to the client**. A tiny serial-protocol addition
lets the interface recognise a µR-capable peer ident, so µR clients get the full
cooperative layer while vanilla clients still get unilateral reciprocity-based
adaptation (§3/§4). The reach is the point: the entire existing RNode-client
population gets adaptive power for free.

**Known caveat (host-driven radio params).** A client that reconfigures SF / BW /
frequency / CR can't be silently honoured under an adaptive layer sharing the
radio. Per setting, either reject the change as an error, or migrate our own rnsd
onto the new channel as a fresh interface. Fold this into the
proper-air-protocol channel model rather than solving it here.

## 10. Open items, in one list

- Margin policy and the miss→widen loop constants; interaction with CSMA and the
  SF sensitivity floor (§5).
- The loop constants: up-step vs. down-step size, the success count that gates a
  decrement, and the decay rate of the known-bad floor (§4.3).
- Failure-detection latency — the thing that bounds how aggressive the walk-down
  can be (§4.2/§4.3).
- Per-neighbour keying of the in-band relay detector, currently one aggregate
  counter per radio (§4.2).
- Windowing or decaying the RSSI/SNR envelope — lifetime extremes are useless as
  a change signal (§4.2).
- Consuming the `ROAMING` flag we already carry, and deriving a mobility proxy
  from recent RSSI variance for peers that can't send one — a mobile peer should
  walk down more reluctantly and jump up harder (§4.3).
- Per-neighbour power actually driving the tx path (§6).
- Per-`transport_id` gateway-signal keying in rnsd (§6).
- Link-quality window constant: EWMA vs. fixed window — long enough to be
  stable, short enough to track a fading/mobility change.
- Whether the interface re-derives proof↔frame correlation itself or rnsd feeds
  down a per-frame outcome bit (rnsd already correlates proofs into receipts with
  rssi/snr/hops attached — don't duplicate if a small hint suffices).
- Tagging LoRaMon frames with their neighbour key, so per-neighbour signal
  history becomes a query over the existing store rather than new state (§5).
