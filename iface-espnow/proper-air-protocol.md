# A proper air protocol for iface-espnow

> Status: **design notes / plan**, not implemented. The shipping transport
> (`esp-idf/src/espnow.cpp`) is deliberately minimal: a single broadcast peer,
> LR-only, RNS packet 1:1 to one ESP-NOW frame, rnsd does all routing. This
> document captures what the ESP-NOW + Reticulum substrate *allows* so a more
> involved air protocol (directed unicast, link metrics, own-layer rate control)
> can be built on it later. Nothing here is wired up yet.

## 1. What ESP-NOW actually is (the facts everything else rests on)

ESP-NOW is **not** a separate PHY or MAC. Every ESP-NOW packet is an ordinary
**802.11 vendor-specific action frame** (category `0x7F`, Espressif OUI
`18:FE:34`) carried over the chip's normal Wi-Fi MAC/PHY. Consequences:

- It goes through the **same CSMA/CA** (listen-before-talk, random backoff,
  defer-on-busy, retry) as any Wi-Fi frame.
- Unicast frames get a **MAC-layer ACK**; broadcast frames do not. The ESP-NOW
  send-callback success/fail is derived from that 802.11 ACK.
- Connectionless: no association, no handshake, **no rate negotiation** (see §8).
- On-air payload unit is **≤250 B** (`ESP_NOW_MAX_DATA_LEN`). The v2 API raises
  the *API* cap to ~1490 B with fragmentation, but the wire unit is still a
  ≤250 B action-frame body per frame.
- Encryption, when enabled, is **802.11 CCMP** (the WPA2 primitive) on the
  action-frame body — not a separate ESP-NOW crypto layer.

### Data rates
Because it's just 802.11, the rate is whatever PHY rate the driver/peer config
selects, not a fixed "ESP-NOW rate":

- Full ladder via `wifi_phy_rate_t`: 1/2/5.5/11 (11b), 6–54 (11g/OFDM),
  MCS0–7 (11n, ~6.5–72 Mbps), plus the **LR rates**.
- Pin it per-peer with `esp_now_set_peer_rate_config()` (what the current code
  does for LR) or globally.
- Real goodput ≪ PHY rate: per-frame 802.11 overhead (preamble, headers, SIFS,
  ACK, DIFS, backoff) dominates for ≤250 B payloads.

### LR (Long Range) — the one mode that's genuinely different
LR is an **Espressif-proprietary PHY** (not in the 802.11 standard), enabled by
putting `WIFI_PROTOCOL_LR` in the protocol bitmap. Two rates only:
`WIFI_PHY_RATE_LORA_250K` (~0.25 Mbps) and `WIFI_PHY_RATE_LORA_500K` (~0.5 Mbps)
— *not* actual LoRa, just Espressif's marketing borrow. Heavy coding for
sensitivity; ~1–2 km LoS.

- **Only other Espressif chips in LR mode can decode it.** A standard Wi-Fi NIC
  cannot demodulate LR — it looks like noise. (To sniff LR you need an ESP32 in
  promiscuous + LR mode.)
- The MAC framing (action frame, vendor element) is the same; the PHY wrapper
  differs. Legacy 802.11 still senses LR as "energy present" and defers (CCA),
  so LR coexists for backoff purposes.
- LR-only nodes and 11b/g/n-only nodes **cannot hear each other** — the protocol
  bitmaps must overlap.

### Listening on multiple modes simultaneously — yes
- **11b + 11g + 11n are received together by default** — the PHY rate is signaled
  per-frame in the PLCP preamble and auto-detected. A station never "tunes to one
  rate."
- **LR can be received concurrently** by OR-ing its bit in:
  `WIFI_PROTOCOL_11B|11G|11N|LR`. The receiver then detects both the standard
  preambles and the Espressif-LR preamble and decodes each frame in whatever PHY
  it arrived. (Setting `LR` *alone* makes you LR-only and deaf to standard
  frames.) The current code already sets the full bitmap.
- On the target **ESP32-S3** the available set is `{11b, 11g, 11n/HT20, LR}`.
  No 5 GHz (11a/ac), no 11ax (that's C5/C6).
- **One radio, one channel.** "Multiple modes" never means multiple channels —
  all of the above are received only on the single channel the interface is
  parked on. All peers (and any sniffer) must share a channel.

**Why this matters for §8:** a node can only *report* on a rate it can decode,
so every participant in the beacon/rate-report scheme must enable the full
bitmap or higher-rate beacons are invisible to it and read as pure loss.

## 2. Knowing *when* a packet actually went out (post-hoc, TX-side only)

Goal: post-hoc, on the transmitting node, when a frame left the air — **not**
depending on anyone receiving it.

**There is no public API that returns the hardware air-departure timestamp of an
arbitrary ESP-NOW frame.** The hardware knows the exact TSF instant (it stamps
beacons, does FTM) but doesn't expose it for application frames.

What you *can* do, TX-side:

- **TX-done callback + your own timestamp.** `esp_now_register_send_cb` fires
  after the MAC finishes the attempt; stamp it with `esp_timer_get_time()` (µs
  since boot) or `esp_wifi_get_tsf_time()` (radio clock, useful for cross-node
  alignment). The callback args carry no air-time stamp — you're stamping the
  callback, not the frame.
- **Broadcast is the clean case.** No ACK, no retries: the callback fires right
  after the single transmission, and CSMA backoff already happened *before* TX.
  So `timestamp ≈ end-of-air ± callback-dispatch jitter`, minus the (deterministic,
  subtractable) frame air-time. Jitter is tens of µs on a quiet system, worse
  under load.
- **Unicast is muddier.** The callback reflects completion *after the ACK*, and
  if the MAC retried it reflects the **last** attempt — it silently absorbs an
  unknown number of retransmissions. Avoid for clean air-time.

**Why it can't be exact TX-side:** the callback is a queued dispatch in the Wi-Fi
task; that scheduling step is non-deterministic jitter between the air event and
your `esp_timer` read, and it's unremovable in software.

**If you ever need sub-µs exactness**, you must observe the RF: a sniffer (even
your own second ESP32) reading `wifi_pkt_rx_ctrl_t.timestamp` (hardware µs), or a
physical RF/logic-analyzer tap. Both count as "something receiving it"; there is
no software-only TX-side path to true exactness.

**Recommendation:** broadcast + send-cb + `esp_timer`/`tsf`, minus computed frame
air-time, for ~tens-of-µs TX-side timing with no receiver.

## 3. Directly-seen peers — a neighbor table below RNS

Broadcast is a *choice*, not a constraint. ESP-NOW gives everything for a real
neighbor table, entirely below RNS:

- **Every RX hands you the neighbor.** `esp_now_recv_info_t` carries `src_addr`
  (6-byte MAC of the node whose radio you heard *directly*), `des_addr`
  (broadcast vs. your MAC), and `rx_ctrl` (`rssi`, `rate`, `channel`,
  `timestamp`). Key a table by MAC with RSSI / last-seen / rate / decoded-PHY.
  That table *is* "nodes I can hear directly."
- **RSSI per neighbor for free** → raw material for link selection / ETX-style
  metrics.
- A directly-heard neighbor is by definition **one radio hop** away (ESP-NOW does
  no L2 forwarding, so there is no L2 hop count — receiving the frame *is* the
  proof of direct reachability).

## 4. Unicast vs broadcast, peer registration, the cap

- **Unicast** = `esp_now_add_peer(mac)` then `esp_now_send(mac, …)`. Payoff over
  broadcast: a **MAC-layer ACK**, i.e. a real per-link forward-delivery
  confirmation in the send-cb.
- **Register/unregister are cheap RAM ops** (~µs, synchronous, internally locked)
  — *not* network operations, no handshake. An LRU that adds/removes MACs as
  next-hops churn is fine.
  - **Don't thrash per-packet** — add/del on cache *eviction*, keep hot
    next-hops resident.
  - **Cap ≈ 20** registered peers (unencrypted; fewer with per-peer crypto).
    *Tracking* neighbors is unlimited (just record from RX); only *active unicast
    peers* are capped.
- **Escape hatch if 20 is too few:** `esp_wifi_80211_tx()` injects raw 802.11
  frames to any MAC with **no peer table, no cap, no add/del** — you build the
  frame yourself. Keep in pocket for the involved design.
- **Discovery still needs broadcast** (can't unicast to an unheard node; RNS
  announces inherently flood). Natural shape: **hybrid** — broadcast for
  discovery/announces/flood, unicast for directed next-hop delivery.

## 5. RNS hop count and path building — what the interface can see itself

Two distinct notions of "hop"; don't conflate them.

- **Radio hops (L2):** intrinsic — a received frame is one radio hop away,
  `src_addr` is the transmitter. Needs nothing from rnsd.
- **RNS transport hops:** the Reticulum header carries a **hops byte** (the
  second header byte), incremented by each Transport node that retransmits. The
  interface holds the raw packet, so it can **read this directly without rnsd**.

| Question | Source | Needs rnsd? |
|---|---|---|
| Is this neighbor directly reachable? | ESP-NOW `src_addr` | No |
| How many transport hops has *this packet* travelled? | RNS header byte 2 | No |
| How many hops to *destination X* (best path) | aggregated from announces across ifaces | **Yes** — Transport's job |

### Announces and forwarder identity (the path-building mechanism)
When a Transport node **rebroadcasts an announce**, it does not forward the bytes
unchanged. It rewrites it into a **HEADER_2 ("transport") packet** whose two
address fields are:

```
[ transport_id of the relaying node ][ destination hash being announced ]
```

and **increments hops**. So the rebroadcast *carries the forwarder's RNS identity
(transport_id) inside it* — which is exactly what RNS records as the **next hop**
toward that destination:

```
path[destination_hash] = { next_hop = transport_id, hops = N, via = espnow, … }
```

To send later, RNS emits a HEADER_2 packet addressed `[next_hop_transport_id]
[destination_hash]`; the neighbor whose transport_id matches forwards it.

Key properties:
- **Distance-vector, not source-routed.** Each node learns only its *immediate
  next hop* + the hop *count*, never the full path.
- **First announce has no separate forwarder.** A fresh announce straight from the
  destination is HEADER_1, hops=0, single address = the destination itself; heard
  directly, the "forwarder" *is* the destination (degenerate-correct).
- On every rebroadcast you simultaneously learn the **transport_id** (RNS layer)
  *and* the **MAC** (`src_addr`, L2) *and* **RSSI/rate/hops** for that next hop.

## 6. The MAC ⇄ identity table + rnsd-oblivious unicast optimization

**Yes — you can keep a table mapping L2 MAC ⇄ RNS identity, distilled passively
from received announces, and unicast at L2 to reach a node, with rnsd entirely
oblivious.** The layering is clean: the interface presents a "broadcast medium" to
rnsd; rnsd hands it packet bytes (with the next hop already encoded); the
interface chooses physical broadcast vs. unicast. Semantics preserved → rnsd
never needs to know.

### Index on next-hop transport_id, not destination hash
The thing you unicast toward is the **next hop**, which for routed traffic is
*not* the destination. So store the MAC keyed by **both**:

- **`transport_id → MAC`** — learned from **rebroadcast announces** (HEADER_2,
  hops≥1, transport_id field = the relay). This is the routing table. On TX of a
  HEADER_2 packet, read its next-hop transport_id → look up MAC → unicast.
- **`destination_hash → MAC`** — learned from **direct announces** (HEADER_1,
  hops=0). Covers rnsd sending a HEADER_1 packet straight to a directly-reachable
  destination — here the original dest-hash → MAC instinct is correct.

On TX, branch on header type: HEADER_2 → match next-hop transport_id; HEADER_1 →
match destination hash. Miss → broadcast.

### Why rnsd-oblivious is self-consistent
Any next-hop transport_id that rnsd routes *out the espnow interface* is
necessarily a node this interface heard directly — that's how the path got
attached to this interface. So whenever rnsd asks to send to next-hop T via
espnow, the interface has already seen T's rebroadcast announce and has T's MAC.
The optimizer is never asked to resolve a MAC it couldn't have learned.

### Safety rails (skip these and you blackhole traffic)
1. **Keep announces on broadcast — always.** They must flood every neighbor;
   never unicast them. (Also preserves neighbors' ability to learn paths by
   overhearing — the one property otherwise lost by unicasting.)
2. **Fall back to broadcast on miss *or failure*.** Unicast has a MAC ACK; on
   send-cb failure (neighbor moved/slept/gone) **evict the entry and rebroadcast**
   (or drop and let RNS reliability retry). rnsd assumes the medium delivered to
   whoever's listening; a stale MAC that silently fails is a blackhole rnsd can't
   see. Treat **unicast as a hint, broadcast as ground truth.**
3. **Peer-cap LRU** (§4): register/unregister around eviction, raw-inject if you
   outgrow 20.

### Security note
L2 MACs are spoofable, but RNS validates cryptographically at its own layer
(transport_id is identity-derived). A bad MAC just causes a misdelivery you
recover from via fallback — not a security break. MAC is only ever a delivery
hint.

## 7. Own-layer link adaptation: beacon + reception report

**ESP-NOW has no rate negotiation / link adaptation.** Connectionless: no
association, no capability exchange, no "slow down" feedback. You set a **fixed
rate per peer** (`esp_now_set_peer_rate_config`) or globally; MAC retries happen
*at the same rate* (retry ≠ adaptation). With LR you *must* pin the rate. So the
rate control has to be built at our layer.

Design — two own-layer message types:

- **Frequent tiny beacon**, swept across the rate set: a `set_peer_rate_config →
  send` loop (those config calls are the same cheap RAM ops; one rate in flight to
  a peer at a time, but a serial sweep is fine). Tag each with a per-rate sequence
  number so receivers can detect *losses*, not just successes.
- **Infrequent reception report**: "what rates I receive *you* at, and how well."

What each mechanism actually buys:
- The **receiver gets RSSI + the decoded PHY rate for free** in `rx_ctrl` on every
  frame — no need to tag the rate in the payload (sequence number is for loss).
- The **unicast MAC ACK already covers the forward direction** — "did this land."
  So the reception report is *not* redundant with the ACK; its value is
  (a) **broadcast** delivery (no ACK), (b) **RSSI/quality at the far end**, and
  (c) **link asymmetry**: A may hear B fine while B barely hears A. The ACK only
  proves the forward path; the report is the only window on reverse-path quality →
  lets each direction pick its own rate.

Cost discipline:
- **LR airtime is expensive** — a small beacon at 250 kbps is already
  milliseconds-plus with overhead; × rates × neighbors on one shared channel adds
  up. Keep the *frequent* beacon a single tiny frame; make the *full multi-rate
  sweep + report* periodic and delay-tolerant.
- Every participant must enable the full `11B|11G|11N|LR` bitmap (§1) or
  higher-rate beacons are invisible and look like loss.

## 8. Open items to nail down before building

- **Exact RNS header layout** from the `rns` packet struct: byte-0 flag bits
  (IFAC / header type 1-vs-2 / context / propagation / destination type / packet
  type), the hops byte position, HEADER_1 (16 B) vs HEADER_2 (32 B) address-field
  widths, and where the announce's public key + signature + app_data sit. Needed
  to parse announces and to read next-hop transport_id on egress. *(This is
  wire-format reference in the `rns` straddle, independent of this transport.)*
- Concrete **egress decision** path: parse header type → pick address field →
  table lookup → unicast/broadcast, with the fallback rules from §6.
- **Table lifetime / eviction** policy for MAC⇄identity entries (tie to announce
  freshness and send-cb failures).
- **Beacon/report wire format** and cadence; per-(neighbor, rate) quality metric
  and how it feeds rate selection.
- Interaction with the **channel-conflict / WiFi-coexistence** policy already in
  `espnow.cpp` (the radio is shared with `net`).

---

*Captured from a design discussion; verify API names and the RNS header offsets
against ESP-IDF 5.5.4 and the `rns` straddle before implementing.*
