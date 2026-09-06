# LXMF delivery queue, telemetry removal, SUPE measurement publication

> Status: **executed.** What this describes is in `lxmf/`, `rns/` and
> `iface-lora/`; their README/INTERNALS are authoritative where they differ.

## What changes and why

Three things, one root: LXMF measured the radio itself — a telemetry header
on every packet rnsd handed up, an extended delivery proof carrying the
prover's rx signal, and a per-message store of it all — and its outbound retry
logic was spread over rnsd's path-search table, a 1 Hz requeue loop and a
one-shot path-dropping retry. SUPE (Spectrum Utilization and Performance
Enhancements) in iface-lora now measures path loss in both directions for every
radio peer as a by-product of its meetings, so LXMF's own measurement goes, and
delivery becomes one queue with one sweep and one timeout.

## 1. Delivery queue (lxmf)

```
cmd.send ──► processReady (attempt) ──ok──► outbox slot ──► DELIVERED
                 │                              │
                 │ can't yet                    │ attempt failed (no path in 60 s,
                 ▼                              │ no proof, link fell over, …)
             s_queue ◄──────────────────────────┘
                 │  every s.lxmf.delivery_interval min (default 10), while non-empty
                 ▼
            queueSweep: age ≥ s.lxmf.delivery_timeout min (default 60) → DELIVERY_TIMEOUT
                        else processReady again
```

- **One RAM queue** on the lxmf task (`s_queue`): messages awaiting a
  (re)attempt, holding no outbox slot and nothing rnsd-side. Entries carry the
  time they were first queued; a reboot re-queues every in-progress outbound it
  finds in storage with a fresh clock (the boot scan).
- **The sweep** runs from the 1 Hz tick when `s.lxmf.delivery_interval`
  minutes have passed since the last one, only while the queue is non-empty.
  It runs on the lxmf task — every piece of outbound state (outbox, wire cache,
  conversation links) is task-local and lock-free, so a separate task would
  need locking everywhere for no gain. Per entry: gone/cancelled/terminal →
  drop; in flight → skip; older than `s.lxmf.delivery_timeout` →
  `DELIVERY_TIMEOUT` (`tries = 255`; an RLPG mailbox may take custody instead);
  else one attempt.
- **An attempt is bounded.** A send rnsd parks for a path gets
  `LXMF_PATH_GRACE_S` (60 s); then lxmf cancels it rnsd-side and requeues,
  so rnsd's 4-slot per-connection path table can never fill with hour-long
  searches and a QUEUE_FULL answer just leaves the message queued for the next
  sweep. A proof timeout, a link that fails to establish or dies, a resource
  that fails, an outbox with no free slot and a busy conversation link all
  requeue instead of settling. Local errors (pack, malloc, bad peer, disabled)
  stay terminal at once. Nothing drops a cached path any more.
- `tries` counts lxmf attempts. `RETRYING_DELIVERY` / `RETRYING_LINK` /
  `REQUESTING_PATH` / `QUEUED` are the statuses a queued message shows.
- New status `DELIVERY_TIMEOUT = 38`; removed knob `s.lxmf.retry_throttle_min`.

## 2. Telemetry removal (rns, lxmf, rlpg, frontends)

- `IN_PACKET` is `opcode | full LXM wire`; `OUT_RESULT` is its fixed 9 bytes;
  a forwarded link packet is the plaintext alone; `rnsd_link_resource_done_t`
  loses `rssi/snr/iface`. Every link consumer (lxmf inbound/conversation/RLPG/
  propagation links, the rlpg node) reads the payload from byte 0.
- The extended proof is gone: `Packet::prove_report`, the `report_signal`
  argument, `PacketReceipt::remote_*`/`local_txp`, `Interface::tx_power_dbm`,
  `rnsd_iface_t.tx_power_known/dbm`, the +5 proof-length admits, rnsd's
  per-peer capability table and `rnsdSet/GetRxReportCap`, and LXMF announce
  caps bit 1. Proofs are vanilla in both directions.
- lxmf drops `rx_meta_t`, `lxmf.msgmeta.*` (store, schema, cap knob),
  `lxmf.contactsig.*`, `formatIface`. Message bubbles and detail pages show no
  routing data. The gateway indicator (`rnsd.gw.*`) is untouched.

## 3. SUPE measurement publication (iface-lora)

Every 15 s, while the peer table holds anyone and a frame has moved since the
last publication, per radio and per table slot:

```
lora.<n>.meas.<slot>.tags       6-hex prefixes the node answers to (dests, identities, node key)
lora.<n>.meas.<slot>.name       announced first-word names
lora.<n>.meas.<slot>.loss_to    dB us→them  (the peer's report of our frame)
lora.<n>.meas.<slot>.loss_from  dB them→us  (a frame heard here against the power the peer stated)
lora.<n>.meas.<slot>.to_ts      unix s of the loss_to measurement
lora.<n>.meas.<slot>.from_ts    unix s of the loss_from measurement
lora.<n>.meas.<slot>.rssi       dBm, strongest level heard from it
lora.<n>.meas.<slot>.snr        dB×10, best heard
lora.<n>.meas.<slot>.peer_txp   dBm the peer last stated
lora.<n>.meas.<slot>.txp        dBm we last sent to it at
lora.<n>.meas.<slot>.heard_ts   unix s last heard
```

A field is absent when not known. A slot that empties is deleted. Readers
find a peer by the first six hex characters of its destination hash in `tags`.

- **Ping** keeps `rtt_ms` and `hops` and adds `loss_to`/`loss_from` from this
  publication; every surface renders "us→them N dB / them→us N dB".
- **Contact bars** (list, thread header, web contact list) come from the
  peer's `rssi`/`snr` here instead of `lxmf.contactsig`.
- **`lora n`** prints a `path loss` line per neighbour: both directions with
  the age of each.
