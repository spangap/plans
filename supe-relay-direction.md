# SUPE relay direction

Reviewer notes for the fix in `iface-lora`: a SUPE (the LoRa interface's scheduled-exchange protocol, `reticulous/docs/supe.html`) transport that relays a Reticulum link sent the initiator's traffic back to the initiator.

## Wire sketch of the fault

A two-hop link, initiator A, transport T, responder B, all SUPE. L is the link identifier.

```
A ──LINKREQUEST (hops 0)──────────► T ──LINKREQUEST (hops 1)──► B
A ◄──PROOF L (hops 1)─────────────── T ◄──PROOF L (hops 0)──────── B
A ──DATA L identify (hops 0, plain)► T
A ──HAIL tag L──────────────────────► T          (A has more for L)
A ◄──GOT, then DATA L identify (hops 1) T          wrong: T's queued frame for B rides A's exchange
T ──HAIL tag L──────────► heard by A and B; A answers first
A ◄──DATA L message (hops 1)──────── T          wrong: the message for B goes to A
```

## Diagnosis, with the code path

Three things resolved "where does a frame of L go" to one node, and at a transport none of them can:

1. **The hail's tag.** `queueFill` (`lora_bridge.cpp`) tags a link frame with its first address field, the link identifier. At T both A and B hold L in their SUPE tag sets (A dialled it, B answers it; `supeTagAdd` in `lora_observe.cpp`), so a hail tagged L is answered by whichever of them hears it first. In run G's trace, n55's hail at T 1971.25 was answered by n23 (the initiator), and the 243 B message went to n23.
2. **The train.** When A hailed T with tag L, `hTrainBuild` (`lora_supe.cpp`) swept every queued packet whose tag matched L into the answering GOT, including the identify T had just received from A and was forwarding to B.
3. **The peer id.** `apNextHop4` (`lora_power.cpp`) answered for a link frame with the link's destination when that destination is a direct neighbour (B at T, for both directions), else the identifier's filing in the hash store, and `peersObserve` refiled L against the sender of every link frame that arrived as exchange cargo (`peersAddLink4(from, …)` in the `NEI_PT_DATA` branch) — at T that is A for A's frames and B for B's, so the filing flipped with each frame.

The lookup `tagNode` (`lora_supe.cpp`) resolves a tag to one node; at a transport L has two.

## What the spec says

- §5 lists "link identifiers we relay for" among the addresses that mean us: a relay answers hails tagged with L, which it must, since both endpoints send towards it tagged L.
- §5.1: a link identifier resolves to "the node the link request was handed to".
- §6: the tag is "the first three bytes of an address the peer holds", and "for a hail with a packet behind it that is the packet's first address field".
- §11, links inherit: "Every hop of a relayed request computes it too, and files it against the node it hands the request to."

**The spec is silent on the relay's two directions.** §5.1 and §11 give a relay one node for L, the responder's side, which is right for frames going towards the responder and wrong for frames going back. §6 then tags a relayed link frame with L, an address both of the relay's neighbours on that link hold, which contradicts §6's own premise that a tag names the peer: at a relay L is not an address only the peer holds. An amendment should say, in §5.1 and §6, that a relay resolves a frame of a link it relays to the party the frame did not come from, and tags the hail with an address of that neighbour alone (a destination it announced, or an identity from its ANNOUNCE), since the identifier names both parties. Not edited here.

## The fix

All in `iface-lora/esp-idf/src`:

- `lora_peers.h` `NeiLink`: the two sides of a link this node relays. Initiator's side: present on this interface when a request not for us is received here (first copy), its hop count is that request's, its row is named by exchange cargo. Responder's side: present when we transmit the request at hops ≥ 1, its row is the request's next hop, its hop count is the first link proof's. `NeiSeen` (the recent-rx ring) records the row that provably sent each frame (`fromPeer`, from a SUPE exchange).
- `lora_observe.cpp` `peersLinkRelayHop`: for an outbound link-addressed DATA or PROOF at wire hops ≥ 1 (an endpoint sends at hops 0, so this is exactly "relayed"), the neighbour it goes to. rnsd's link forwarding (`Transport.cpp`, link transport branch) rewrites only the hop count, to the arrival's plus one, so the truncated packet hash finds the arrival in the ring and the arrival hop count is known. Order: one side on this interface → that side; link proof → initiator; arrival named its sender → the other side; the two sides' hop counts differ → the side the arrival's hop count does not match (the test rnsd applies to a link's two directions); else nobody.
- `lora_bridge.cpp` `queueFill`: a relayed link frame is queued under the neighbour `peersLinkRelayHop` names, tagged with `peersNodeTag` (an announced destination, else node key, else identity: an address that neighbour alone holds). Unnamed: no tag, so the SUPE engine sends it plain, as the broadcast it then is.
- `lora_power.cpp` `apNextHop4`: same answer for the plain path's power; unnamed → configured power.
- `lora_observe.cpp`: the cargo refiling of L (`peersAddLink4(from, …)`) is skipped on a link this node relays.
- `lora_peers.cpp`: a merge and a retire carry or clear the two side rows; `peersLinkEnsure` starts them unnamed.
- `lora_bridge.cpp`: the no-SUPE build passes `LORAQ_PEER_NONE` rather than `0` (row 0) as "sender unknown".

Nothing here is host-specific. No host test covers the observer or the peer table (`esp-idf/test` holds the pure engine and core only), so the end-to-end runs are the test.

Consequence to know: on a link whose two sides are the same hop count from the relay (every two-hop link) a frame that reached the relay in the clear cannot be told apart by direction, and the relay sends it plain on the calling channel. The responder hears it as it would without SUPE. Only frames that reached the relay inside an exchange are relayed inside one.

## What was tried

The design above was the first one built. Alternatives considered and not taken:

- Hail with L and let the hailed party decide: both parties see an identical hail (same hailer, same tag), so neither can decide.
- Direction from hop counts alone: symmetric on every two-hop link, which is the commonest relayed case in city99.
- Tagging endpoint link frames by the first hop's address too: at an endpoint only an off-route node can also hold L, so it was left as the spec has it.

## Verification

**Two-hop check** (run G's warm snapshot `city99-supe-warm`, n23 → n42 through n55, all SUPE transports; one `lora 0 a` at each of the three, then one `lxmf send`). Sent at T 229.97, delivered (sender's `delivered mid=`) by T 251.2. The record around it:

```
230.561  n23 868.950 LINKREQUEST f258597d via d9835530 hops=0 -> n55    (in n23's exchange)
231.531  n55 868.250 LINKREQUEST hops=1                     -> n42    (n55 hailed; n42 answered)
232.331  n42 864.750 PROOF link/406c0dea link-proof hops=0 -> n55
233.127  n55 864.050 PROOF link/406c0dea link-proof hops=1 -> n23    (to the initiator)
234.126  n55 869.525 DATA  link/406c0dea link-rtt hops=1    -> broadcast (arrived in the clear, two-hop link: direction unknown)
235.750  n55 869.525 DATA  link/406c0dea hops=1 254B split  -> n23 n39 n42 n88 n89 (broadcast; n42 receives the message)
236.891  n42 865.450 PROOF link/406c0dea hops=0             -> n55
238.007  n55 864.050 PROOF link/406c0dea hops=1             -> n23    (delivery proof to the sender)
```

No frame of the link went from n55 back to n23 except the two proofs, which belong there. In run G the identify and the message went from n55 to n23 inside exchanges.

**Run H** (`simreport-runH.md`): supe delivered 112/720 (run G 16/720; lora 122/720), 28/29 at two route hops (G 0/30) and 24/35 at three (G 0/43). Single-frame relayed link frames in the traffic hour, sent inside an exchange: 2379 on to another station, 1 to the station the frame came from (G: 80 and 1394). The one is a 21 B frame n55 relayed from n42 to n39, which n42 also overheard on that channel.
