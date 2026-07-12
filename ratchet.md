# RNS ratchet support

Reticulum ratchets give **forward secrecy**: a destination rotates an ephemeral
X25519 keypair, advertises the current public half in its announces, and senders
encrypt to that ratchet instead of the destination's long-term identity key. If
the identity key is later compromised, traffic encrypted to since-rotated
ratchets stays unreadable (the ephemeral private keys are discarded after
`RATCHET_EXPIRY`).

This port does **not** implement ratchets. `Packet.cpp` has a stub
(`// CBA RATCHET`), `Type.h` carries the constants, and nothing on the
announce / encrypt / decrypt paths touches them. That leaves two problems:

1. **We reject ratcheted announces (active bug).**
   `Identity::validate_announce` (`rns/.../src/Identity.cpp:430`) assumes the
   announce layout `public_key · name_hash · random_hash · signature · app_data`
   and ignores `packet.context_flag()`. Upstream inserts a 32-byte ratchet
   **before** the signature when ratchets are on, and the ratchet is covered by
   the signature (`signed_data = dest_hash + public_key + name_hash +
   random_hash + ratchet + app_data`). Modern peers — meshchat, nomadnet,
   Sideband — all call `enable_ratchets` on their delivery destination, so their
   announces carry `context_flag == FLAG_SET` and the ratchet field. We mis-slice
   them: read the ratchet bytes as part of the signature, omit the ratchet from
   `signed_data`, signature check fails, **announce rejected**. Also mis-reads
   their app_data (name/cost land at the wrong offset). This is a strong
   candidate for the "peer only appears in the announce list after a while / not
   at all" friction, and it is *upstream* of the link half-open delivery bug.

2. **No forward secrecy.** Everything we send is encrypted to peers' static
   identity keys; everything sent to us likewise.

## Decision / sequencing

Do the **receive** side and **outbound encrypt** first — these are pure wins,
interoperable, and require no change to what we advertise. Do **advertising our
own ratchet LAST, and keep it gated off** until inbound ratchet-decrypt exists
and is verified.

Rationale for the ordering: advertising a ratchet is only safe once we can
*decrypt* messages sent to that ratchet. The moment we advertise, compliant
senders start encrypting to it, and if we can't try our ratchet private keys on
inbound we lose every such message. So advertising must not ship before Phase 4,
and even then behind a default-off switch (`s.rns.ratchets_enabled = 0`).

Phases 1–3 give: correct validation of modern peers' announces (fixes bug 1) and
forward secrecy for messages **we send** (partial fix to bug 2). Phase 4 adds
forward secrecy for messages **sent to us**.

## Upstream mechanics (reference)

Verified against markqvist/Reticulum `RNS/Identity.py`, `RNS/Destination.py`.

Constants (already present in `Type.h`, confirm values):
- `RATCHETSIZE = 256` bits (32-byte X25519 public key)
- `RATCHET_EXPIRY = 60*60*24*30` (30 days) — drop remembered peer ratchets older
- `RATCHET_COUNT = 512` — how many of our own ratchets to retain (advertising)
- `RATCHET_INTERVAL = 30*60` — min seconds between our rotations (advertising)
- `ratchet_id = full_hash(ratchet_pub_bytes)[: NAME_HASH_LENGTH/8]` (first 10 B)

Announce wire layout when ratchets on:
`public_key(32) · name_hash(10) · random_hash(10) · ratchet(32) · signature(64) · app_data`
- presence signalled by `packet.context_flag == FLAG_SET`
- `signed_data = dest_hash + public_key + name_hash + random_hash + ratchet + app_data`

Encrypt (`Identity.encrypt(plaintext, ratchet=None)`): if a ratchet is given,
`target_public_key = X25519PublicKey.from_public_bytes(ratchet)` instead of
`self.pub`; the rest (ephemeral keypair, ECDH, HKDF, token) is unchanged. Sender
obtains the ratchet via `get_ratchet(destination.hash)`.

Decrypt (`Identity.decrypt(token, ratchets=…)`): iterate the destination's known
ratchet **private** keys, do ECDH of each against the token's prepended ephemeral
pub, try to open the token; fall back to the static identity key. (No selector in
the token — it's trial decryption, bounded by how many ratchets we hold.)

## Phase 1 — Validate ratcheted announces (fixes bug 1)

File: `rns/esp-idf/components/microreticulum/src/Identity.cpp`,
`validate_announce` (~430).

- Read `packet.context_flag()` (already plumbed — used in `Transport.cpp`).
- When `FLAG_SET`: extract `ratchet = data.mid(KEYSIZE/8 + NAME_HASH_LENGTH/8 +
  RANDOM_HASH_LENGTH/8, RATCHETSIZE/8)`, then read `signature` and `app_data`
  from offsets shifted by `RATCHETSIZE/8`.
- Build `signed_data = dest_hash + public_key + name_hash + random_hash +
  ratchet + app_data` (concatenate ratchet only when present — matches upstream,
  where absent ratchet contributes zero bytes).
- On success, remember the ratchet (Phase 2) alongside the existing
  `remember(...)` call.
- Guard all lengths; a malformed/short ratcheted announce must fail closed, not
  read OOB.

This alone makes us accept and correctly parse modern peers' announces.

## Phase 2 — Remember / recall peer ratchets

File: `Identity.cpp` / `Identity.h` (mirror the existing `remember` /
`recall` / `recall_app_data` pattern, keyed by destination hash).

- `remember_ratchet(dest_hash, ratchet_bytes)` → store `{ratchet, received_ts}`.
- `recall_ratchet(dest_hash)` / `get_ratchet(dest_hash)` → return latest
  non-expired ratchet, else empty.
- Expiry: honour `RATCHET_EXPIRY`; drop on recall if stale.
- **Storage**: use the platform storage abstraction (`storageSet/Get`), not
  files — key e.g. `rns.ratchet.<dest_hash_hex>` holding msgpack
  `{r: bytes, t: int}`. Keep it in the evictable/bounded store; a mesh can
  announce many destinations (cross-ref `plans/spangap-core/evictable_storage.md`).
- In-memory LRU cache in front of storage to avoid a read per outbound packet.

## Phase 3 — Encrypt outbound to peer ratchets (partial fix to bug 2)

Files: `Identity.cpp` `encrypt` (~547); `Destination.cpp` encrypt path; the
Packet pack path that calls into them.

- Add optional ratchet param to `Identity::encrypt(plaintext, ratchet={})`.
  When non-empty, do the ECDH against `ratchet` instead of `_object->_pub_bytes`
  (line 559); everything else unchanged. Prepend ephemeral pub as today.
- `Destination::encrypt` (SINGLE, OUT) looks up `Identity::get_ratchet(hash())`
  and passes it through when present.
- Record the `ratchet_id` used on the outbound `Packet` (`Packet.h:339
  _ratchet_id`, currently unset) if/when the link-proof path needs it; for plain
  opportunistic single packets the receiver trial-decrypts, so the id is
  optional. Scope this only if a concrete path needs it.
- Confirm this also covers the **initial link-request** encryption to the
  destination (link setup encrypts to the destination identity/ratchet). Audit
  `Link` establishment for a second encrypt-to-destination site.

After Phase 3 we still advertise no ratchet, so nothing encrypts *to* us with one
— inbound decrypt is unchanged and safe.

## Phase 4 — Advertise our own ratchet (GATED OFF by default)

Only after Phases 1–3 ship and inbound ratchet-decrypt below is implemented and
tested. Ship behind `s.rns.ratchets_enabled`, default `0`.

Destination side (`Destination.cpp` / `Destination.h`):
- `enable_ratchets()` — initialise/load our ratchet set from storage.
- `rotate_ratchets()` — generate a new X25519 keypair, insert at front, clean to
  `RATCHET_COUNT`, persist; rotate no more often than `RATCHET_INTERVAL`.
- `announce()` (~203): when enabled, insert `ratchet_public_bytes(ratchets[0])`
  between `random_hash` and `signature`, include it in `signed_data`, and set the
  announce packet's `context_flag = FLAG_SET`.
- Persist our ratchet **private** keys in storage; survive reboot so in-flight
  messages to a recent ratchet remain decryptable.

Inbound decrypt (`Identity.cpp` `decrypt`, ~587) — **required** before advertising:
- Accept the set of our destination's ratchet private keys.
- Trial-decrypt: for each ratchet priv, ECDH against the token's ephemeral pub,
  attempt token open; on failure fall back to the static identity key (line 607).
- Bound the work (most-recent-first, stop at first success; cap iterations).

## Testing / interop

- **Bug-1 regression**: capture a real ratcheted announce from nomadnet/meshchat
  (context_flag set) and assert `validate_announce` now accepts it and extracts
  name/cost correctly. Also assert a non-ratchet announce still validates.
- **Phase 3**: send from reticulous → a ratchet-advertising peer; confirm the
  peer decrypts (it should, since we encrypt to its ratchet) and that a peer with
  no ratchet still receives (fallback to identity key).
- **Phase 4**: with the gate on, peer → reticulous over a rotated ratchet;
  confirm decrypt across a rotation and across a reboot (persistence).
- Round-trip reticulous ↔ reticulous with the gate on both ends.

## Risks / notes

- Announce signature is consensus-critical: a wrong `signed_data` order or offset
  makes our announces unvalidatable by everyone. Get Phase 1 byte-exact against a
  real capture before touching Phase 4.
- Advertising before inbound decrypt works = silent black-hole of all
  ratchet-encrypted inbound. Hence the strict ordering and the default-off gate.
- Storage pressure: remembering a ratchet per destination on a busy mesh needs
  bounds/eviction, not unbounded growth.
- `context_flag` is already carried through `Transport`; no new packet plumbing
  expected, only reading it in validate and setting it in announce.
- Keep the LXMF app_data untouched — the ratchet is an RNS-layer field, never in
  app_data. (The old "prefix ratchet to app_data" note in `lxmf.cpp` was wrong
  and has been corrected.)
</content>
</invoke>
