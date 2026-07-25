# RLPG — Reticulous LXMF Proper Gation

A personal mailbox / propagation node bound to a single LXMF address, with
offline-verifiable proof of that binding. Deposits are anonymous; the mailbox
is a blind blob store that signals nothing to anyone.

## Design revision — delivery confirmation is recipient-sourced

The mailbox does **not** send delivery receipts. Only the recipient can
decrypt an envelope, and `message_id = SHA-256(dest||src||packed)` is a hash
over the *plaintext* the mailbox never sees — so a delivery confirmation that
carries that id and arrives *from the address the sender sent to* is
unforgeable proof of pickup, sourced end-to-end. This deletes a whole layer:

- **No depositor identify, no `receipt_to`.** Deposits are fully anonymous
  again (owner AUTH stays — it's how the owner opens a pickup session). The
  identify-means-receipt mechanism is removed.
- **No mailbox service messages.** Pickup deletes the blob (rx-proof →
  delete); the notification queue, the `state` field's "awaiting notify"
  value, and `flushNotifications` all go. The mailbox never contacts the
  sender.
- **Delivery confirmation:** on a successful RLPG pickup, the recipient's
  client sends the sender a tiny LXMF message carrying a delivery-confirm
  field = the picked-up `message_id`. The sender advances the matching parked
  `REMOTE_RLPG`/`OUR_RLPG` message → `DELIVERED`. Trust is inherent: a
  confirmation from peer X can only settle a message *we sent to* peer X, and
  only the real X could produce the id. Confirmations are field-only messages
  that never themselves generate a confirmation (no loop) and are never shown
  in a thread.
- **Expiry becomes client-side.** No `RLPG_EXPIRED` notice from the mailbox.
  The sender knows the deposit time and the advertised `retain_days`, so after
  `retain_days` + grace with no `DELIVERED` it flips its own parked message to
  `RLPG_EXPIRED` locally. Retention on the node just deletes silently.

The rest of this document predates the revision; where it describes mailbox
receipts, the service identity for receipts, `receipt_to`, or depositor
identify, the revision above supersedes it.

## Why not plain lxmf.propagation

- No binding: nothing proves a propagation node is authorized to hold mail
  for an address, so senders can't prefer "the recipient's own node".
- No receipts: a sender never learns the recipient actually picked a
  message up.
- Pull model: recipients must poll and sync; RLPG keeps a link open and
  pushes.

RLPG keeps the same end-to-end encryption model (node stores opaque
destination-encrypted blobs) and adds: an owner-signed certificate, deposit
bookkeeping by transient hash, pickup proofs, and notification messages.

## Roles, identities, destinations

The node has **one RNS identity** with a single aspect, `rlpg.mailbox` —
one destination for depositors and owner alike; the role is decided per
link session:

- no identify, or identify with any other identity → depositor session;
- identify with the identity whose pubkey hashes to the served LXMF
  address → owner session.

A single aspect means no separate owner destination ever has to be
announced just to get paths routed — the public announce serves both. The
first packet on any link is the cert either way (depositors verify it, the
owner runs the renewal check on it).

Plus **one service LXMF identity**: a normal `lxmf.delivery` destination the
node uses to send notification messages, display name
`RLPG service for <owner display name>`. Its dest hash is published in the
certificate so counterparties know which source to trust for receipts.

The served LXMF dest hash is node config. An uncertified node (before the
owner's first session, or after cert expiry) still announces — the owner
needs a routable path to install the cert — but with `served = nil` in
app_data: it advertises reachability, not service, and refuses deposits
(`RLPG_ERR`, reason `uncertified`). Clients never treat a reachability-only
announce as a mailbox.

## Certificate

Signed by the owner's LXMF identity; proves "node identity N is my mailbox
until T". msgpack array:

```
[0] version        uint
[1] owner_pubkey   bin 64   (X25519 || Ed25519, RNS identity blob)
[2] node_id_hash   bin 16   (RNS identity hash of the RLPG node)
[3] service_dest   bin 16   (lxmf.delivery hash of the node's service identity)
[4] issued_at      uint     (unix seconds)
[5] expires_at     uint
[6] signature      bin 64   (Ed25519 by owner over the packed bytes of [0..5])
```

~180 bytes packed — fits one link packet.

Self-certifying: the verifier recomputes the owner's `lxmf.delivery` dest
hash from `owner_pubkey` (no directory lookup needed) and checks it equals
the address the node claims to serve. Verification steps for a depositor:

1. Opening the link to `rlpg.mailbox` already proves the responder holds the
   destination's identity keys (RNS link handshake) — no separate identify
   from the node is needed. Check the link's destination identity hash
   equals `node_id_hash`.
2. Verify `signature` with the Ed25519 half of `owner_pubkey`.
3. Recompute owner's lxmf.delivery hash from `owner_pubkey`; this is the
   address the cert authorizes. Match it against the contact you're trying
   to reach.
4. Check `issued_at <= now <= expires_at` with a grace window (see Clocks).

Lifecycle: `expires_at - issued_at = cert_valid_days` (setting). On every
owner session the node sends its current cert (or "none"); if it is older
than `cert_renew_days` (setting) or absent, the owner's client signs and
uploads a fresh one. Expired cert ⇒ the announce reverts to
reachability-only (`served = nil`) and new deposits are refused (the stale
cert is still sent first, so depositors fail cleanly and fall back); held
mail and owner pickup are unaffected.

Revocation: none beyond expiry. Keep validity short (default: renew after
7 days, valid 14 — held mail expires after 7 days anyway (see Retention),
so owners must check in weekly regardless; the cert just needs to outlive
the retention window with slack). A cert with `expires_at = issued_at` acts as a tombstone
if the owner wants to shut a node down early — node deletes state and
reverts to reachability-only announces.

### Clocks

Expiry needs wall-clock time on depositor and node. Reticulous devices may
free-run; apply generous grace (default ±48 h) and treat "expired" as a
freshness signal, not a hard security boundary — the security boundary is
the signature + link identity proof.

## Discovery

Two independent paths, both required before a client persists anything:

1. **The RLPG node's own announce** (`rlpg.mailbox`), app_data msgpack:
   `[version, served_dest bin16|nil, cert_issued_at uint, deposit_stamp_cost uint, retain_days uint]`
   (`served = nil`, `issued = 0` while uncertified). A self-claim —
   worthless until the cert checks out.
2. **A field in the owner's `lxmf.delivery` announce** — authoritative,
   since announces are signed by the owner's identity (see next section).

Either way, on first sight the client connects to `rlpg.mailbox`, receives
the cert, verifies it, and only then stores in the contact's DB entry:
RLPG node identity hash + pubkey, deposit dest hash, service dest hash,
cert expiry. Re-verify (reconnect) when the stored cert expires.

### The lxmf.delivery announce field — interop

Current shapes in the wild (lxmf.cpp:1106, LXMF reference 0.9.8):

```
[a] 32B ratchet || msgpack([display_name|nil, stamp_cost])
[b] msgpack([display_name|nil, stamp_cost])
[c] msgpack([display_name|nil])
[d]/[e] raw utf-8 name (very old)
```

**We append array element [2]: the 16-byte `rlpg.mailbox` dest hash (or the
node identity hash — pick one, see Open questions).** This does not break
interop:

- Reference LXMF (`display_name_from_app_data`, `stamp_cost_from_app_data`)
  unpacks the array and reads indices [0]/[1] only; extra elements are
  ignored. Sideband/MeshChat/NomadNet go through these helpers.
- Our parser (lxmf.cpp:1115 `parseLxmfAnnounce`) likewise reads [0]/[1] and
  ignores `cnt > 2`.
- The array-length-as-version pattern is exactly how LXMF itself added
  stamp_cost ([c] → [b]).

The legacy raw-name forms can't carry it, but only RLPG-enabled clients emit
the field anyway. One real constraint: the element must go *inside* the
msgpack array — trailing bytes after the array would break strict
`unpackb()` consumers.

Because the announce is signed by the owner's identity, this field is
itself an owner-signed binding, refreshed on every announce. The standalone
cert is still needed because depositors may never hear the owner's announce
(that's the whole point of a mailbox) — but when both are seen, the announce
field wins on conflict (it's fresher and can't be replayed by a stale node).

## Deposit protocol (stranger → node)

1. Depositor opens link to `rlpg.mailbox`.
2. Node immediately sends the cert as the first packet. Depositor verifies
   (above); on failure: teardown, mark the node bad for this contact, fall
   back to other delivery paths.
3. Depositor sends deposit envelopes. Small → single link packet; large →
   RNS Resource. Envelope, msgpack:

   ```
   [0] version     uint
   [1] receipt_to  bin 16 | nil   (LXMF address for the pickup notification)
   [2] blob        bin            (LXMF propagation-format: destination-
                                   encrypted wire message, signed inside by
                                   the true source)
   [3] stamp       bin | nil      (outer proof-of-work, see Spam)
   ```

   The served destination is implicit (one node = one address), so no dest
   field. `transient_id = SHA-256(blob)` — computed by both sides, never
   transmitted. The node cannot see the inner LXMF message hash (it can't
   decrypt), so **all bookkeeping and notifications use the transient id;
   the depositing client records the `lxmf_hash ↔ transient_id` mapping at
   deposit time.** No plaintext message ids leak to the node or the wire.

   Resources need no extra header/hash packet: RNS Resource transfer already
   integrity-checks content, the envelope travels as the resource body, and
   end-to-end authenticity is the LXMF signature inside the encrypted blob,
   verified by the owner at pickup.

4. Node dedups on `transient_id`, stores, acks each envelope: transient id
   + code — `STORED`, `DUPLICATE` (counts as stored), `RLPG_FULL` (quota),
   or `RLPG_ERR` + u8 reason (bad stamp, oversize, store failure,
   uncertified). On FULL/ERR the depositor maps to the message statuses
   below and, if it has an own RLPG node configured, falls back to relaying
   through it.
5. Depositor tears down (or keeps the link for more messages), marks the
   message "held at recipient's mailbox" locally.
6. When the owner later picks the message up (rx-proof, below), the node
   sends one LXMF message from its service identity to `receipt_to` (if
   set) listing picked-up transient ids, batched per depositor. The
   depositor's client verifies the source identity equals the service
   identity from the verified cert, maps transient ids back to message
   hashes, and advances delivery status (second checkmark).

### Spam / DoS

The node stores blobs it cannot inspect, on small flash. Defenses:

- **Outer stamp**: proof-of-work over `blob || node_id_hash` meeting
  `deposit_stamp_cost` (advertised in announce app_data and enforceable
  before storing — unlike LXMF's inner stamp, which is encrypted). Reuse
  the lxmf straddle's stamp generator/validator.
- Per-envelope size cap, total storage quota, per-link rate limit. On full:
  refuse new deposits (`RLPG_FULL`) — mailbox semantics, never evict
  held mail.
- No identify required from depositors (preserves LXMF's anonymous-sender
  property); quotas are per-link/per-stamp, not per-identity.

## Pickup protocol (owner ↔ node)

1. Owner opens link to `rlpg.mailbox` and identifies. The node checks the
   received pubkey hashes to the configured served address; any other (or
   no) identify simply leaves the link a depositor session.
2. The current cert (or `none`) already arrived as the link's first packet.
   Owner renews if absent/stale (`cert_renew_days`). The first valid cert
   switches the announce from reachability-only to full service.
3. Node streams held envelopes oldest-first (packet or resource, one
   resource at a time), each prefixed with its transient id.
4. Owner decrypts, validates the inner LXMF message, then sends an
   **rx-proof** for the transient id (a plain ack suffices — the link is
   already authenticated by identify). Node deletes the blob and queues the
   depositor notification.
5. Link may stay open; new arrivals are pushed immediately.
6. An undecryptable/garbage blob still gets an rx-proof (variant:
   `discard`) so it's deleted rather than re-served forever; no depositor
   notification is sent for discards.

### Retention

Held mail expires after `s.rlpg.id.<n>.retain_days` (default 7): blob
deleted, depositor notified at `receipt_to` with status `RLPG_EXPIRED`.
Retention is advertised as announce app_data element [4] so senders know
the pickup window. This is the number that actually forces the owner's
check-in cadence — the cert only needs to outlive it with slack, hence the
7/14-day cert defaults.

## Outbound relay (owner sends via own node)

Over the same authenticated owner link. Envelope, msgpack:

```
[0] version      uint
[1] dest         bin 16        (final recipient's lxmf.delivery hash)
[2] lxmf_hash    bin 32        (real message hash — owner's own node may
                                know it; enables direct checkmark handling)
[3] blob         bin           (LXMF wire message, encrypted to dest,
                                signed by owner)
[4] timeout      uint | nil    (seconds; default from node setting)
```

Node delivery loop per message, until delivered or timeout:

1. Direct LXMF delivery (opportunistic packet or link), exactly what the
   lxmf straddle already does.
2. Recipient's RLPG node, if known (from the recipient's announce field [2]
   or a verified `rlpg.mailbox` announce) — deposit with
   `receipt_to = owner's LXMF address`.
3. (Optional, later) legacy `lxmf.propagation` fallback.

Retry scheduling is **announce-driven, not timer-driven**: any inbound
announce (`lxmf.delivery` or `rlpg.mailbox`) for a destination with queued
messages triggers an immediate attempt — the dest just proved it's alive
and gave us a fresh path. Between announces, the node issues occasional
path requests for queued destinations on a growing backoff
(`s.rlpg.pathreq_min_s`, doubling to `s.rlpg.pathreq_max_s`), each answered
path request being itself an attempt trigger. A slow background sweep
catches anything the event edges miss and expires messages past their
timeout.

Status reporting: LXMF message from the service identity to the owner
listing `(lxmf_hash, status)` pairs, batched:
`delivered` (reached the recipient's client — two checkmarks),
`held_remote` (parked at their RLPG node — one checkmark; the second
arrives later as that node's pickup notification to `receipt_to`),
`failed` (timeout exhausted).

## Service messages

Sent as normal LXMF from the node's service identity. Machine-readable part
in a custom LXMF field (pick an id from the unallocated integer range in
the fields registry, lxmf.cpp:85) carrying msgpack
`[version, [(id, status_code), ...]]` where id is a transient id (inbound
receipts) or lxmf_hash (outbound status), and `status_code` is the
`LxmfStatus` value itself — the enum is the shared vocabulary between node
and client, which is why the RLPG codes live in the append-only list.
Human-readable title/content as courtesy for non-RLPG clients.

Trust rule, both directions: **a service message is honored only if its
source identity matches a service_dest learned from a verified cert** (own
node's, or the one stored in the contact's DB entry). Display names prove
nothing.

## Client-side changes (lxmf straddle)

- Setting: own RLPG node, per identity — `s.lxmf.id.<n>.rlpg_node` = the
  node's identity hash, 32-hex (both aspect dest hashes derive from it; a
  mailbox serves exactly one address, so the binding is per LXMF identity,
  not global). Plus `s.lxmf.rlpg.cert_renew_days` / `.cert_valid_days`.
- Emit announce app_data element [2] when an own node is configured and
  certified; parse element [2] from others' announces
  (`parseLxmfAnnounce` gains one field).
- Subscribe to `rlpg.mailbox` announces; on either discovery path run
  verify-then-store into the contact DB entry: node identity hash + pubkey,
  deposit dest, service_dest, cert expiry.
- Outbound resolution order: direct → recipient's RLPG → own RLPG relay →
  fail (NO_ROUTE/NO_RESPONSE as today).
- Owner session logic: cert issue/renewal, pickup stream, rx-proofs,
  outbound envelopes.

### Status codes and checkmarks

Two new members appended to the `LxmfStatus` enum (append-only list,
lxmf.h; mirror in browser `lxmf.ts` LxmfStatus + STATUS_NAME):

```
LXMF_ST_REMOTE_RLPG      = 29  /* deposited at the recipient's RLPG node */
LXMF_ST_OUR_RLPG         = 30  /* handed to our own RLPG node for relay */
LXMF_ST_REMOTE_RLPG_FULL = 31  /* recipient's node refused: mailbox full */
LXMF_ST_REMOTE_RLPG_ERR  = 32  /* recipient's node refused: error */
LXMF_ST_RLPG_EXPIRED     = 33  /* held past retention, dropped unpicked */
```

All four are parked states: our client stops retrying (`tries = 255`, the
existing "no longer in play" marker — same shape as DELIVERED), but unlike
the gave-up statuses they can **move** when a service message or ack
arrives. Transitions:

- deposit ack `STORED` from a remote RLPG node → `REMOTE_RLPG`
- deposit ack `RLPG_FULL` / `RLPG_ERR` from remote →
  `REMOTE_RLPG_FULL` / `REMOTE_RLPG_ERR`; if an own node is configured,
  immediately hand off → `OUR_RLPG`
- accepted by our own node's outbound relay → `OUR_RLPG`
- service msgs from our own node carry `(hash, status_code)` pairs and move
  the message among `REMOTE_RLPG` / `REMOTE_RLPG_FULL` / `REMOTE_RLPG_ERR`
  as its relay attempts land (the node keeps retrying FULL/ERR remotes
  until its timeout) — the owner can watch the real trajectory, e.g.
  `OUR_RLPG → REMOTE_RLPG → DELIVERED`
- service msg status `DELIVERED` → `DELIVERED`
- service msg status `RLPG_EXPIRED` (retention ran out unpicked, sent by
  whichever mailbox held it) → `RLPG_EXPIRED`, terminal
- own node's timeout exhausted → `NO_RESPONSE`

UI: single checkmark for `REMOTE_RLPG`/`OUR_RLPG` (reached a mailbox; the
status name itself stays hidden behind the check), double checkmark for
`DELIVERED`; `REMOTE_RLPG_FULL`/`_ERR` surface as visible error states
(they may still resolve if our node's retries get through);
`RLPG_EXPIRED` is a hard failure — the recipient never picked it up.

### Timestamps

Three per-message times: **sent** (`ts`, exists), **received** (`recv_ts`,
exists), **delivered** — new `u32 delivered_ts`, set when the status
reaches `DELIVERED` (from a direct proof or a service message). Sharing a
slot between received and delivered (they're direction-exclusive) would
work, but `recv_ts` is the stable sort key and is set for outbound records
too — overloading it buys ~4 bytes and costs an invariant. With
migration.md's auto-migrator landed, adding the field is a builder-chain
edit with zero migration code, so a dedicated field it is.

## Node (new `rlpg` straddle)

- Depends on the lxmf straddle (full delivery stack for the outbound relay
  and the service identity).
- **Identity slots, same model as lxmf**: `secrets.rlpg.id.<n>.privkey`
  (node RNS identity via rnsd), settings under `s.rlpg.id.<n>.*`, runtime
  mirror `rlpg.id.<n>.*` (stats; `cert_state` = `none | valid | expired`
  plus `cert_expires` — so settings/browser and CLI both show "owner has
  not certified yet" or the remaining validity at a glance), storage
  watcher on both trees. One slot = one mailbox = one served address; a device can
  host several mailboxes for different owners.
- Per-slot settings (`s.rlpg.id.<n>.`): `serves` (32-hex owner LXMF dest),
  `enabled`, `quota_kb`, `max_envelope_kb`, `stamp_cost`,
  `outbound_timeout_s`, `retain_days` (held-mail expiry, default 7), `service_lxmf_id` (slot number of the lxmf-straddle
  identity used for service messages, created with the mailbox), `cert`
  (current cert bytes, hex — public data, lives in settings not secrets).
  Cert validity/renewal days are owner-client-side settings — the owner
  writes the cert.
- **`rlpg` CLI**, verb dispatch like `cliLxmf`:

  ```
  rlpg create <owner>       new mailbox slot serving <owner> (32-hex or
                            contact/announce name); mints node identity +
                            service LXMF identity
  rlpg destroy <n>          wipe slot <n> (secrets, settings, held mail)
  rlpg id [<n>]             list slots (* = selected) / switch selection
  rlpg status               cert state ("no cert yet — owner must connect"
                            / valid-until), announce state, held count,
                            quota use
  rlpg cert                 decode and print the current certificate
  rlpg held                 list held envelopes (tid, size, age, state)
  rlpg drop <tid|all>       delete held envelope(s)
  rlpg announce             force a mailbox announce
  ```

  Owner/client-side operations (choosing your own node, checkmarks) stay in
  the lxmf straddle and its CLI/settings — `rlpg` operates the node.
- Storage, two-tier:
  - **Blobs**: one raw flash file per envelope via the `fs` subsystem
    (`rlpg/<transient_id_hex>`). Not structured-DB — SGDB stores are
    whole-arena PSRAM-resident, built for small fixed-width records, not
    multi-KB opaque write-once blobs.
  - **Metadata**: one SGDB store, a record per held envelope —
    `data tid 32`, `data receipt_to 16`, `u32 arrived_ts`, `u32 size`,
    `u8 state` (`held → picked_up_awaiting_notify → done`). The `state`
    field *is* the reboot-surviving notification queue; batch and flush
    notifications opportunistically.
  - Write blob before metadata record, delete record before blob; boot
    sweep reconciles (blob without record → delete orphan; record without
    blob → drop record).
- Layout: wire formats (cert, envelopes, service-field pack/parse) live in
  the lxmf straddle — the client needs them and lxmf must not depend on
  rlpg; the rlpg straddle contains the deposit/owner link servers, store,
  and relay loop.

## Sequencing

**plans/migration.md lands first.** RLPG adds fields to two durable schemas
— `delivered_ts` on messages, and the RLPG block on contacts (node identity
hash + pubkey, deposit dest, service_dest, cert expiry). With the generic
auto-migrator in place each of these is a builder-chain edit with no
bespoke migration code or version bump; without it, every one costs a
retained old-schema copy and a hand-written field-copy migration.

## Implementation notes (v1, as built)

- **Announce field [2] carries the `rlpg.mailbox` dest hash** (resolved:
  directly linkable/path-requestable; the cert's node-identity binding is
  checked separately by hashing the recalled pubkey of that dest). Same
  value everywhere: `rlpg create` prints it, `s.lxmf.id.<n>.rlpg_node`
  holds it.
- **HELLO carries the service dest** alongside the (possibly nil) cert —
  the owner needs it to build the *first* cert.
- **rns straddle grew four generic primitives**: `rnsdEncryptFor` /
  `rnsdDecryptSelf` (mR Identity token encryption — same construction as
  RNS packet payloads, static identity key, no ratchets in this mR),
  `rnsdIdentityPubkey`, `rnsdIdentityHashFromPubkey`,
  `rnsdDestinationHashFromPubkey`.
- **Envelope blob** = mR Identity ciphertext of the full LXMF wire
  (`dest||src||sig||msgpack`); transient id = SHA-256 of the ciphertext.
  The depositing client stores the tid in the message record
  (`rlpg_tid`); the mailbox never sees inner ids.
- **Relay delivers to remote RLPG mailboxes only.** A direct final-hop
  attempt needs a pre-encrypted *raw* opportunistic packet send that rnsd
  does not expose; until that primitive exists, dests without a certified
  mailbox exhaust the relay timeout as NO_RESPONSE.
- **Service messages are parseable text** (`rlpg/1\n<status> <id_hex>`
  lines, `rlpgStatusPack/Parse`) rather than a custom LXMF field — doubles
  as the human-readable courtesy for non-RLPG clients; trusted only from
  a service dest learned via a verified cert (`s.lxmf.id.<n>.
  rlpg_service_dest` for the own node, contact `rlpg_svc` for remotes).
- **Contact records** gained `rlpg` (verified mailbox dest) + `rlpg_svc`;
  both written only after a cert verified in-session.
- Node storage as planned: blobs under `/state/rlpg/<n>/{held,out}/`,
  SGDB stores `s.rlpg.id.<n>.held` / `.outq` (held `state` field is the
  notification queue), boot reconcile both directions.

## Double-encryption capability (next)

Closes the "relay can't final-hop a long message" gap without a raw
packet send:

- `lxmf.delivery` announce app_data grows element [3]: uint `caps`
  bitfield, bit0 = *accepts double-encrypted payloads* — a link/resource
  payload that is a destination-encrypted envelope blob rather than
  plaintext LXMF wire; the receiver detects it (leading 16 bytes match no
  local delivery dest), decrypts with its identity, and re-enters the
  normal inbound pipeline. Element [2] (mailbox) becomes nil-able so [3]
  stays positional. Reticulous clients always emit bit0.
- An RLPG node can then deliver held relay mail *directly* to a capable
  recipient: open a link to its `lxmf.delivery` and send the stored
  ciphertext blob as packet/resource — end-to-end encryption intact, any
  size. Capability comes from the recipient's announce (the node already
  subscribes for retry triggers); order stays direct-capable → remote
  mailbox.
- Clients refuse to hand a long message (wire beyond the single-packet
  ceiling) to their own RLPG server unless the final recipient is known
  capable — fail TOO_LARGE instead of stranding it. (Later option:
  split long messages instead of refusing.)
- Legacy recipients: short messages still await the raw pre-encrypted
  send in rnsd; long ones need the split option. Reticulous→reticulous
  is fully covered by the capability bit.

## Open questions
- Default deposit_stamp_cost, and whether owner-relayed outbound deposits
  to remote RLPG nodes should generate stamps on-device (ESP32 PoW cost).
- Multiple mailboxes per address / mailbox migration (new cert for a new
  node while the old one still holds mail) — v1: one node per address.
- Whether rx-proofs should be owner-signed (would let the node *prove*
  pickup to depositors rather than assert it) — v1: link-auth ack.
