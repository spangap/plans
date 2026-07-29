# Adaptive TX power for iface-lora

> Status: **design notes / thinking**, not implemented. Captures where adaptive
> TX power belongs, what feedback is actually available, and the identification
> problem that makes per-neighbor power tractable. Counterpart to
> `iface-lora/proper-air-protocol.md` (SF/BW/CR rate adaptation) — power and rate
> are coupled (§7), but power is the smaller, earlier win and can ship on the
> plain broadcast interface without the negotiated-link machinery. The LoRaMon
> per-frame recorder (already built — see `iface-lora/INTERNALS.md`) is the data
> substrate for building and validating this.

## 1. Goal

Use full TX power only when needed. A frame to a close neighbour shouldn't go
out at +22 dBm; a link that only needs +5 dBm to close should use +5. Wins:
battery, and less self-interference / airtime pollution on a shared channel.

## 2. Where it lives — the interface, not rnsd

TX power is a **per-RF-hop physical property**. Only the LoRa interface has the
inputs (per-frame RSSI/SNR, the `txp` register, the channel noise floor it
already tracks for CSMA) and it is the only interface for which "power" means
anything — TCP/ESP-NOW have none. So the control loop and its measurement table
live in **iface-lora**; `rnsd` stays medium-agnostic (a power API in core would
be meaningless for every other interface).

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
  uplink measurement needs margin.
- **Active, cooperative (proofs).** A single-destination packet elicits a
  **proof** from the receiver. Proof-or-not, and the RTT, is a real end-to-end
  "it arrived" bit. Not universal: application destinations don't prove by
  default (PROVE_ALL is opt-in); the always-on elicitor is the **link handshake
  (LR → LRPROOF)**, which any node hosting a linkable destination must answer.
- **Active, µR-only (extended proof / rx-report).** A reticulous/µR peer appends
  *its own measured* `rssi|snr` to the proof (the "extended proof"; see
  `rns/INTERNALS.md` §5.7 — rnsd already captures this as the gateway signal).
  That's a direct readout of how much headroom the far end had receiving us — the
  ideal calibration signal. A vanilla receiver length-rejects the longer proof,
  so it's sent only to known-capable peers.

**Tiering:** reciprocity is the floor that always works; active proof-based
measurement is an upgrade for cooperative peers; the extended proof is the best
signal, µR-to-µR. Build all three as layers, not either/or.

Minimal proof elicitor: `rnstransport.probe` — a single-dest encrypted packet
(~115 B: header + 16 B dest + the ~96 B single-dest crypto envelope; payload
size is irrelevant) that draws an implicit proof (~one small frame back). Cheap
against LoRa airtime (a frame is tens–hundreds of ms; a SHA/probe is µs–one
frame). Prefer piggybacking on real single-dest traffic; synthetic probes only
to bootstrap or when idle — a probe costs our airtime *plus* the receiver's proof
airtime on a shared half-duplex channel.

## 4. The identification problem (the crux)

Per-neighbour power needs per-neighbour identity at the RF layer, and a broadcast
LoRa interface carries none in a data frame — the sender isn't named. But the
**neighbour we transmit *to*** is always resolvable from the outbound frame:

| outbound frame | next-hop identity |
|---|---|
| HEADER_1, dest type SINGLE | the dest hash itself |
| HEADER_1, dest type LINK | the `link_id` → neighbour, mapped at link setup |
| HEADER_2 (single or link) | the `transport_id` (next-hop) in the header |

Direct neighbours and their public keys come from **announces received at
`hops==1`** (a directly-received frame reports `hops()==1`). A link's
`link_id ↔ neighbour` is captured at setup: inbound link → the LR we receive
(`hops==1` ⇒ direct); outbound link → the returning LRPROOF (`hops==1` ⇒ direct).
Either way we get "this link_id's other end is a direct neighbour" and a
setup-time RSSI/SNR, without ever learning the peer's identity.

## 5. Proof is end-to-end, not per-hop

Power only affects the **first RF hop**, but a proof is end-to-end. So a returning
proof is a clean power signal **only when the destination is a direct neighbour**
(`hops==1`): dest == RF neighbour == prover. For a multi-hop dest, "no proof"
can't distinguish a too-weak first hop from a failure two hops downstream.

Resolution: key the power table on the **first-hop neighbour** (the `transport_id`
for HEADER_2), and *calibrate* that neighbour from traffic where it is itself the
direct single-dest prover (or from its announces / a direct probe). A neighbour's
required power is a physical-link property independent of what RNS traffic rides
it — calibrate per neighbour, apply to any frame whose first hop is that
neighbour.

rnsd already records the relaying neighbour's signal (`rnsd.gw.*`, §5.7) for
`hops>1` packets. It's a single global sample today; **keyed per `transport_id`**
it would be the downlink-calibration table for transit neighbours.

## 6. The controller

- **Don't sit at the cliff.** LoRa's SNR floor is sharp; find the edge, then back
  off a margin. The minimum drifts with fading / mobility / interference, so it's
  a tracking loop with hysteresis (widen margin after a miss, like CSMA's
  contention window), not a one-shot binary search.
- **Broadcast is a separate regime.** Announces have no single prover and must
  reach everyone — power them for the *farthest neighbour we still need*, derived
  from the per-neighbour table's worst member; never cut power on a broadcast.
- **RNS retry is ground truth.** Reciprocity/estimates can be wrong (noise
  asymmetry); keep margin and let end-to-end retransmission be the backstop.

## 7. Coupling with rate (SF/BW/CR)

Link margin is SNR headroom to the SF sensitivity floor. Cutting power trades
directly against SF/coding gain *and* worsens hidden-node loss. So power and rate
are one decision, not two — the proper-air-protocol's per-link measured-parameter
negotiation (`iface-lora/proper-air-protocol.md` §4) is the natural home for the
*joint* optimum. Power-only adaptation on the fixed common config is the earlier,
simpler slice; treat it as a floor that the later rate layer subsumes.

## 8. Groundwork already in place

`iface-lora`'s LoRaMon records every on-air frame — start ms, duration, bytes,
and `txp` (tx) / `rssi`+`snr` (rx) — as `lora.<n>.packets.<ms>` storage nodes.
That is the raw per-frame material to: build the per-neighbour SNR/RSSI history,
observe the airtime a given power level costs, and validate a controller offline
before it drives the `txp` register. It does not yet key anything by neighbour —
the next step is a neighbour table (keyed as §4) fed by announces (hops==1),
proofs (direct), and the extended rx-report.

## 9. Open items

- Neighbour table: keyed by dest-hash / link_id / transport_id (§4), holding
  last RSSI/SNR (near-end), last extended-proof signal (far-end, µR peers), and a
  derived power estimate + margin.
- Per-`transport_id` gateway-signal keying in rnsd (§5) for transit-neighbour
  downlink data.
- Whether the interface re-derives proof↔frame correlation itself or rnsd feeds
  down a per-frame outcome bit (rnsd already correlates proofs into receipts with
  rssi/snr/hops attached — don't duplicate if a small hint suffices).
- Margin policy and the miss→widen loop constants; interaction with CSMA and the
  SF sensitivity floor.
- Regulatory: lower power doesn't change dwell/duty, but the joint power+rate
  choice does — fold into the proper-air-protocol dwell math.
