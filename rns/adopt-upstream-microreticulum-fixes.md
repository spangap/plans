# Adopt five upstream microReticulum fixes into our fork

## Context

Our `rns` straddle vendors a fork of `attermann/microReticulum` at
`rns/esp-idf/components/microreticulum/`, pinned at upstream commit
`5642ae7` (2026-05-09, one past tag 0.3.1) — see the component's
`NOTICE.md`. Upstream has since shipped 0.4.x/0.5.0 with substantial
Link/Resource work. A July 2026 comparison of upstream HEAD `032c751`
(2026-07-13) against our tree identified five improvements worth
adopting. Upstream is Apache-2.0; we may read and adapt freely with
attribution.

This is adaptation-by-reading, not a merge: our files have diverged
heavily (mbedTLS crypto, spangap logging, no Arduino). Port the logic,
match our idioms.

**Upstream reference:** clone into a temp dir, do not vendor:

```
git clone https://github.com/attermann/microReticulum.git /tmp/mr-upstream
cd /tmp/mr-upstream && git checkout 032c751
```

Sources live under `src/microReticulum/`. Line numbers below were
verified against `032c751` (upstream) and our current main; re-verify
with grep before editing, they will drift.

## Facts already verified (don't re-derive)

- Our **initiator** sets `_establishment_timeout` from
  `get_first_hop_timeout()` only (our `Link.cpp:70`). Our **recipient**
  side already applies per-hop grace
  (`ESTABLISHMENT_TIMEOUT_PER_HOP * max(1, packet.hops()) + KEEPALIVE`,
  our `Link.cpp:224`). Only the initiator side is missing it.
- Our `LinkData.h:67-69` declares `_rssi`, `_snr`, `_q` floats; nothing
  ever populates them and `Link.h` exposes no getters.
- Our inbound link-request MTU clamp **already exists**
  (our `Link.cpp:203-210`, commented block gating on
  `link_mtu_discovery()`); an earlier review claimed it was missing —
  it is not. The LRPROOF `confirmed_mtu` path (our `Link.cpp:339-351`)
  matches upstream's unclamped handling.
- Our `Resource.cpp`/`ResourceData.h` have **no** `_req_hashlist` —
  a retransmitted RESOURCE_REQ makes us resend parts (benign, wasteful).
- Our `Link::link_closed()` (our `Link.cpp:748-756`) iterates
  `_incoming_resources`/`_outgoing_resources` directly while calling
  `cancel()` on each. `Resource::cancel()` itself does not erase from
  those containers, but it fires the `_concluded` callback, which may
  (now or in the future) mutate the lists → latent iterator dangle.
  Upstream snapshots into a `std::vector` before iterating, both in
  `link_closed()` and in the RESOURCE/HMU/ICL branches of `receive()`.

## Work items (in this order)

### 1. Per-hop establishment timeout grace (initiator side)

Upstream `Link.cpp:171` (commit `35bb40e`, 2026-07-09, contributed by
@ronandi):

```
_establishment_timeout += ESTABLISHMENT_TIMEOUT_PER_HOP * std::max((uint8_t)1, Transport::hops_to(destination.hash()));
```

Add the equivalent after our initiator-branch timeout assignment
(our `Link.cpp:70`). Without it, multi-hop links can time out during
establishment. Note `_expected_hops` is already computed one line
above ours — reuse it instead of a second `hops_to()` call.

### 2. RSSI/SNR/Q capture onto the link

Upstream `Link.cpp:1078-1090` (in `receive()`): snapshots the packet's
physical-layer stats onto the link when present (guarded by
`!Type::isNan()`), before packet processing. Port that into our
`Link::receive()` and add `rssi()`/`snr()`/`q()` getters to `Link.h`
(the `LinkData` fields already exist). Check how our packets carry
per-packet radio stats from iface-lora through rnsd — if the Packet
doesn't expose them yet, trace what upstream reads
(`packet.rssi()`-style accessors) and wire the equivalent; if our
Packet genuinely never carries them, note that in the commit and add
the capture behind the availability check anyway.
Follow-up (not this task): surface them in rnsd diagnostics.

### 3. MTU clamp audit

Mostly already done on our side (see facts above). Diff every MTU
application site against upstream: signalling construction (ours
`Link.cpp:101-107`, upstream `207-213`), inbound LR (ours `201-215`,
upstream `305-313`), LRPROOF `confirmed_mtu` (ours `339-351`), and
`update_mdu()`. Adopt any divergence that matters; if nothing is
missing, record "verified equivalent" in the commit message and move
on — do not manufacture a change.

### 4. RESOURCE_REQ deduplication

Upstream keeps a per-resource `_req_hashlist` (`ResourceData`, upstream
`ResourceData.h` around line 152) recording hashes of received request
packets; `Resource::request()` drops a request whose packet hash was
already seen, so a retransmitted RESOURCE_REQ doesn't resend the whole
part window. Port: container in our `ResourceData.h`, check-and-insert
at the top of our `Resource::request()` (our `Resource.cpp:657`). Keep
it bounded like upstream does (verify their cap; if they have none,
cap it ourselves — sender lifetime is bounded but LoRa retransmits are
common).

### 5. Iteration-snapshot pattern in `link_closed()`

Upstream `Link.cpp:829-835`: copy the resource sets into a local
`std::vector<Resource>` first, then iterate the copy calling
`cancel()`. Apply to our `link_closed()` (our `Link.cpp:748-756`,
also drop the `const_cast` while there — iterate mutable copies).
Then audit the RESOURCE_ADV/REQ/HMU/ICL branches of our `receive()`
(ours partially snapshots already) and align any direct
iterate-while-mutating loops with the same pattern.

## Attribution (required, do before the code lands)

The component's existing convention (see `NOTICE.md` §2): every
modified upstream file carries an in-file `Spangap fork:` notice, and
`NOTICE.md` §2 holds the summary table.

- Update the `NOTICE.md` §2 rows for `src/Link.{h,cpp}`,
  `src/LinkData.h`, `src/Resource.cpp`, `src/ResourceData.h` to record:
  "selected post-pin upstream improvements backported from
  `attermann/microReticulum` `032c751` (0.5.0 era): per-hop
  establishment timeout grace (upstream commit `35bb40e`, contributed
  by @ronandi), link signal-quality capture, RESOURCE_REQ
  deduplication, snapshot-before-cancel iteration." Adjust wording to
  what actually landed.
- Extend the in-file `Spangap fork:` blocks in each touched file with
  a one-liner naming the backport and the upstream commit.
- `LICENSE.upstream` needs no change (verbatim Apache-2.0, same
  copyright holder covers the newer code). Do not add license text to
  individual files.

## Verification

- `spangap build reticulous/reticulous --with spangap/hw-tdeck` must
  stay green.
- Item 1: establish a link to a Python RNS destination ≥2 hops away
  (Python transport node in between, as in the phase-f interop setup);
  confirm establishment succeeds and log the computed timeout.
- Item 2: over iface-lora, confirm `rssi()`/`snr()` return non-zero on
  an active link (add a temporary debug line or rnsd diagnostic).
- Item 4: hard to force a natural REQ retransmit; unit-style check is
  fine — feed the same request packet twice into `request()` and
  assert single processing (log counter).
- Item 5: exercise link teardown mid-Resource-transfer (kill the peer
  during a >16 KB transfer) and confirm clean cancel, no crash.
- Items 1-5 each land as their own commit with the upstream commit
  hash referenced in the message.
